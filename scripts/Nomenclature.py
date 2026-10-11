# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely>=2.0"]
# ///
"""
Nomenclature des pieces - script Tapir (Archicad 28, mode normal)

A placer dans : Documents\\Tapir\\custom-scripts\\ (a cote de TableauSurfaces.py, qu'il utilise)
Puis dans Archicad : palette Tapir > "Nomenclature.py" > Executer.

Representation par numerotation : une pastille numerotee (01, 02, 03...) dans chaque
piece de l'etage courant, et une legende "numero + nom" en bas du plan.

- Ordre : automatique (sens de lecture) ou en cliquant les pieces dans l'ordre voulu.
- Format : 01, 02... ou par niveau (001, 002 au RDC ; 101, 102 au R+1...).
- La legende et chaque pastille sont des groupes : on peut les deplacer librement.
  "Mettre a jour" redessine en gardant les positions choisies a la main.
- Independant du tableau des surfaces (on peut utiliser l'un, l'autre ou les deux).

Stylos reserves : 212 (texte et traits, noir), 213 (blanc), 209 (bandeau de la legende).
"""

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import TableauSurfaces as T  # noqa: E402

# ---------------------------------------------------------------- reglages ---
COULEUR_BANDEAU = (0.93, 0.11, 0.14)  # rouge par defaut, comme le tableau des surfaces
TITRE_LEGENDE = "LÉGENDE"
PASTILLE_MM = 7.0             # diametre de la pastille (mm papier)
TEXTE_PASTILLE_MM = 2.6       # hauteur du numero dans la pastille
LIGNE_MM = 6.0                # hauteur d'une ligne de la legende
COLONNE_MM = 55.0             # largeur d'une colonne de la legende
LIGNES_MAX = 8                # lignes par colonne avant de passer a la colonne suivante
BANDEAU_MM = 6.0
TEXTE_MM = 2.5
ECART_PLAN_MM = 15.0          # espace entre le bas du plan et la legende
STYLO_LEGENDE = 209
MEMOIRE = "nomenclature.json"          # guids dessines (exclus de la baguette magique)
POSITIONS = "nomenclature_positions.json"
REGLAGES = "nomenclature_reglages.json"
ORDRES = ("Automatique (sens de lecture)", "En cliquant les pieces dans l'ordre")
FORMATS = ("01, 02, 03...", "Par niveau : 001 (RDC), 101 (R+1)...")


# ------------------------------------------------------------------ outils ---
def load_settings() -> dict:
    saved = T.load_json(REGLAGES)
    color = saved.get("couleur")
    return {
        "couleur": [float(c) for c in color] if isinstance(color, list) and len(color) == 3 else list(COULEUR_BANDEAU),
        "titre": str(saved.get("titre", TITRE_LEGENDE)),
        "ordre": saved.get("ordre") if saved.get("ordre") in ORDRES else ORDRES[0],
        "format": saved.get("format") if saved.get("format") in FORMATS else FORMATS[0],
        "surfaces": bool(saved.get("surfaces", False)),
    }


def set_pens(color) -> None:
    tables = T.tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables]
    details = [t for t in T.tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                  ).get("penTables", []) if "error" not in t]
    table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
    if table is None:
        return
    widths = {p.get("index"): p.get("width", 0.1) for p in table.get("pens", [])}

    def pen(index, rgb, description):
        return {"index": index, "width": widths.get(index, 0.1), "description": description,
                "color": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}

    T.tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
        "attributeId": table["attributeId"], "name": table["name"], "pens": [
            pen(T.STYLO_TRAIT, (0.0, 0.0, 0.0), "Texte et traits"),
            pen(T.STYLO_BLANC, (1.0, 1.0, 1.0), "Blanc"),
            pen(STYLO_LEGENDE, color, "Nomenclature - bandeau de la legende")]}]})


def zone_center(zone: dict) -> dict:
    """Point bien a l'interieur de la piece (pas forcement le centre geometrique)."""
    pts = zone["outline"]
    try:
        from shapely.geometry import Polygon
        poly = Polygon([(p["x"], p["y"]) for p in pts]).buffer(0)
        if not poly.is_empty:
            q = poly.representative_point() if not poly.contains(poly.centroid) else poly.centroid
            return {"x": q.x, "y": q.y}
    except Exception:  # noqa: BLE001
        pass
    return T.centroid(pts)


def inside(point: dict, pts: list[dict]) -> bool:
    x, y, result = point["x"], point["y"], False
    for a, b in zip(pts, pts[1:] + pts[:1]):
        if (a["y"] > y) != (b["y"] > y) and x < a["x"] + (y - a["y"]) * (b["x"] - a["x"]) / (b["y"] - a["y"]):
            result = not result
    return result


def reading_order(zones: list[dict]) -> list[dict]:
    """Sens de lecture : de haut en bas par bandes, puis de gauche a droite."""
    band = 2.0
    return sorted(zones, key=lambda z: (-round(z["center"]["y"] / band), z["center"]["x"]))


def number_label(index: int, story: int, fmt: str) -> str:
    if fmt == FORMATS[1]:
        return f"{max(story, 0)}{index:02d}"
    return f"{index:02d}"


def write_zone_numbers(zones: list[dict]) -> bool:
    """Ecrit le numero dans la zone Archicad (propriete Numero de zone), si Archicad l'accepte."""
    try:
        names = T.api("API.GetAllPropertyNames").get("properties", [])
        builtin = [p["nonLocalizedName"] for p in names if p.get("type") == "BuiltIn"]
        name = next((n for n in ("Zone_ZoneNumber", "Zone_Number") if n in builtin), None)
        if not name:
            return False
        pid = T.api("API.GetPropertyIds", {"properties": [{"type": "BuiltIn", "nonLocalizedName": name}]}
                    )["properties"][0]["propertyId"]
        result = T.api("API.SetPropertyValuesOfElements", {"elementPropertyValues": [
            {"elementId": z["element"]["elementId"], "propertyId": pid,
             "propertyValue": {"value": z["label"]}} for z in zones]})
        return all(r.get("success", True) for r in result.get("executionResults", []))
    except Exception:  # noqa: BLE001
        return False


def current_offset(ref_guid: str | None, origin: list | None) -> tuple[float, float]:
    """De combien l'utilisateur a deplace un groupe (d'apres un de ses remplissages)."""
    if not ref_guid or not origin:
        return 0.0, 0.0
    try:
        detail = T.tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": ref_guid}}],
                                                  "fields": ["details"]}).get("detailsOfElements", [{}])[0]
        pts = detail.get("details", {}).get("coordinates", [])
        if not pts:
            return 0.0, 0.0
        return min(p["x"] for p in pts) - origin[0], min(p["y"] for p in pts) - origin[1]
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


# ------------------------------------------------------------------ dessin ---
class Draw:
    def __init__(self, story: int, solid):
        self.story, self.solid = story, solid
        self.mm = T.ECHELLE / 1000.0
        self.texts, self.hatches, self.lines = [], [], []

    def text(self, content, x, y, anchor="LeftMiddle", bold=False, size=TEXTE_MM, pen=None):
        just = {"L": "Left", "M": "Center", "R": "Right"}[anchor[0]]
        self.texts.append({"coordinate": {"x": x, "y": y, "z": 0.0}, "text": content, "floorIndex": self.story,
                           "style": {"height": size, "bold": bold, "penIndex": pen or T.STYLO_TRAIT,
                                     "anchor": anchor, "justification": just}})

    def hatch(self, pts, pen):
        h = {"floorInd": self.story, "showArea": False, "coordinates": pts,
             "fillPenIndex": pen, "fillBackgroundPenIndex": pen, "contourPenIndex": pen}
        if self.solid:
            h["fillId"] = self.solid
        self.hatches.append(h)

    def line(self, pts, weight=0.18):
        self.lines.append({"floorInd": self.story, "linePenIndex": T.STYLO_TRAIT, "penWeightMm": weight,
                           "coordinates": pts})

    def pastille(self, center, label, diameter_mm=PASTILLE_MM, text_mm=TEXTE_PASTILLE_MM):
        r = diameter_mm * self.mm / 2
        ring = [{"x": center["x"] + r * math.cos(2 * math.pi * k / 40),
                 "y": center["y"] + r * math.sin(2 * math.pi * k / 40)} for k in range(40)]
        self.hatch(ring, T.STYLO_BLANC)
        self.line(ring + [ring[0]], 0.25)
        self.text(label, center["x"], center["y"], "MiddleMiddle", bold=True, size=text_mm)

    def flush(self) -> tuple[list[dict], list[str]]:
        """Cree tout ; renvoie (elements, guids des remplissages dans l'ordre)."""
        hatches = T.ok_items(T.tapir("CreateHatches", {"hatchesData": self.hatches}).get("elements", [])) if self.hatches else []
        lines = T.ok_items(T.tapir("CreatePolylines", {"polylinesData": self.lines}).get("elements", [])) if self.lines else []
        texts = T.ok_items(T.tapir("CreateTexts", {"textsData": self.texts}).get("elements", [])) if self.texts else []
        self.texts, self.hatches, self.lines = [], [], []
        return hatches + lines + texts, [h["elementId"]["guid"] for h in hatches]


def group(elements: list[dict]) -> None:
    if elements:
        try:
            T.tapir("CreateGroups", {"elementGroups": [{"elements": elements}]})
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------- run ---
def run(action: str, settings: dict, ordered: list[dict] | None = None) -> str:
    story = T.tapir("GetStories").get("actStory", 0)
    project = T.tapir("GetProjectInfo")
    key = f"{project.get('projectPath') or project.get('projectName') or 'Sans titre'}|{story}"
    memory, positions = T.load_json(MEMOIRE), T.load_json(POSITIONS)
    previous = positions.get(key, {})

    # positions choisies a la main avant de tout effacer
    # decalage total = decalage deja retenu + deplacement fait a la main depuis le dernier dessin
    ddx, ddy = current_offset(previous.get("legende_ref"), previous.get("legende_origine"))
    base = previous.get("legende_decalage", [0.0, 0.0])
    legend_offset = (base[0] + ddx, base[1] + ddy)
    moved = {}
    for zone_guid, info in previous.get("pastilles", {}).items():
        dx, dy = current_offset(info.get("ref"), info.get("origine"))
        bx, by = info.get("decalage", [0.0, 0.0])
        if abs(bx + dx) > 1e-6 or abs(by + dy) > 1e-6:
            moved[zone_guid] = (bx + dx, by + dy)

    if memory.get(key):
        try:
            T.tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in memory[key]]})
        except Exception:  # noqa: BLE001
            pass
    memory[key] = []
    if action == "supprimer":
        positions.pop(key, None)
        T.save_json(MEMOIRE, memory)
        T.save_json(POSITIONS, positions)
        return "Pastilles et legende supprimees."

    zones = [z for z in T.read_zones() if z["story"] == story and len(z["outline"]) >= 3]
    if not zones:
        T.save_json(MEMOIRE, memory)
        return "Aucune zone sur cet etage : cree d'abord les pieces (outil Tableau des surfaces ou Archicad)."
    T.fill_areas(zones)
    for z in zones:
        z["center"] = zone_center(z)
        z["guid"] = z["element"]["elementId"]["guid"]

    if ordered:                                    # ordre clique, puis le reste en sens de lecture
        rest = [z for z in reading_order(zones) if z["guid"] not in {o["guid"] for o in ordered}]
        zones = [z for o in ordered for z in zones if z["guid"] == o["guid"]] + rest
    elif action == "mettre_a_jour" and previous.get("ordre"):
        rank = {g: i for i, g in enumerate(previous["ordre"])}
        zones = sorted(zones, key=lambda z: (rank.get(z["guid"], 10 ** 6), -z["center"]["y"], z["center"]["x"]))
    else:
        zones = reading_order(zones)
    for i, z in enumerate(zones, start=1):
        z["label"] = number_label(i, story, settings["format"])

    try:
        set_pens(settings["couleur"])
    except Exception:  # noqa: BLE001
        pass
    solid = T.solid_fill_id()
    draw = Draw(story, solid)
    all_guids, pastilles = [], {}

    # pastilles (un groupe par pastille, deplacable)
    for z in zones:
        dx, dy = moved.get(z["guid"], (0.0, 0.0))
        center = {"x": z["center"]["x"] + dx, "y": z["center"]["y"] + dy}
        draw.pastille(center, z["label"])
        elements, hatch_guids = draw.flush()
        group(elements)
        all_guids += [e["elementId"]["guid"] for e in elements]
        if hatch_guids:
            r = PASTILLE_MM * draw.mm / 2
            pastilles[z["guid"]] = {"ref": hatch_guids[0], "origine": [center["x"] - r, center["y"] - r],
                                    "decalage": [dx, dy]}

    # legende en bas du plan (un groupe, deplacable)
    mm = draw.mm
    xs = [p["x"] for z in zones for p in z["outline"]]
    ys = [p["y"] for z in zones for p in z["outline"]]
    columns = max(1, math.ceil(len(zones) / LIGNES_MAX))
    rows = math.ceil(len(zones) / columns)
    col_w = (COLONNE_MM + (22 if settings["surfaces"] else 0)) * mm
    width = columns * col_w
    x0 = min(xs) + legend_offset[0]
    top = min(ys) - ECART_PLAN_MM * mm + legend_offset[1]
    band_bottom = top - BANDEAU_MM * mm
    bottom = band_bottom - rows * LIGNE_MM * mm - 2 * mm
    band = [{"x": x0, "y": band_bottom}, {"x": x0 + width, "y": band_bottom},
            {"x": x0 + width, "y": top}, {"x": x0, "y": top}]
    draw.hatch(band, STYLO_LEGENDE)
    draw.text(settings["titre"], x0 + width / 2, (top + band_bottom) / 2, "MiddleMiddle", bold=True, size=3.0,
              pen=T.STYLO_BLANC)
    for i, z in enumerate(zones):
        col, row = divmod(i, rows)
        cx = x0 + col * col_w + 5 * mm
        cy = band_bottom - 1 * mm - (row + 0.5) * LIGNE_MM * mm
        draw.pastille({"x": cx, "y": cy}, z["label"], diameter_mm=5.0, text_mm=2.0)
        draw.text(z["name"] or "Piece", cx + 4.5 * mm, cy, "LeftMiddle")
        if settings["surfaces"]:
            draw.text(T.format_area(z["area"]) + T.UNITE, x0 + (col + 1) * col_w - 2 * mm, cy, "RightMiddle")
    for c in range(1, columns):
        draw.line([{"x": x0 + c * col_w, "y": band_bottom}, {"x": x0 + c * col_w, "y": bottom}])
    draw.line([{"x": x0, "y": top}, {"x": x0 + width, "y": top}, {"x": x0 + width, "y": bottom},
               {"x": x0, "y": bottom}, {"x": x0, "y": top}], 0.35)
    elements, hatch_guids = draw.flush()
    group(elements)
    all_guids += [e["elementId"]["guid"] for e in elements]

    numbers_written = write_zone_numbers(zones)

    memory[key] = all_guids
    positions[key] = {"legende_ref": hatch_guids[0] if hatch_guids else None,
                      "legende_origine": [x0, band_bottom], "legende_decalage": list(legend_offset),
                      "pastilles": pastilles, "ordre": [z["guid"] for z in zones]}
    T.save_json(MEMOIRE, memory)
    T.save_json(POSITIONS, positions)
    note = "" if numbers_written else " (numeros non ecrits dans les zones Archicad : seulement sur le plan)"
    kept = f" {len(moved)} pastille(s) laissee(s) a ta place." if moved else ""
    return f"{len(zones)} piece(s) numerotee(s), legende dessinee.{kept}{note}"


def pick_order(story: int) -> list[dict]:
    """Clique les pieces dans l'ordre voulu (Echap pour finir)."""
    zones = [z for z in T.read_zones() if z["story"] == story and len(z["outline"]) >= 3]
    for z in zones:
        z["guid"] = z["element"]["elementId"]["guid"]
    order, marks = [], []
    try:
        while True:
            T.focus_archicad()
            point, _ = T.pick_point(f"Piece n{len(order) + 1} : clique dedans (Echap = fin)")
            if point is None:
                break
            hit = next((z for z in zones if inside(point, z["outline"]) and z not in order), None)
            if hit is None:
                continue
            order.append(hit)
            # numero provisoire en surbrillance a l'endroit du clic
            try:
                answer = T.tapir("CreateTexts", {"textsData": [{
                    "coordinate": {"x": point["x"], "y": point["y"], "z": 0.0}, "text": f"{len(order):02d}",
                    "floorIndex": story, "style": {"height": 4.0, "bold": True, "penIndex": T.STYLO_CONTOUR,
                                                   "anchor": "MiddleMiddle", "justification": "Center"}}]})
                marks += [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
                T.highlight(marks)
            except Exception:  # noqa: BLE001
                pass
    finally:
        T.highlight([])
        if marks:
            try:
                T.tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in marks]})
            except Exception:  # noqa: BLE001
                pass
    return order


# ---------------------------------------------------------------- fenetre ---
def window(settings: dict) -> None:
    import tkinter as tk
    from tkinter import colorchooser, ttk

    def to_hex(c):
        return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in c)

    state = dict(settings)
    root = tk.Tk()
    root.title("Nomenclature des pieces - Claude Bridge")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    f = ttk.Frame(root, padding=14)
    f.grid()

    ttk.Label(f, text="Ordre des numeros").grid(row=0, column=0, sticky="w", pady=4)
    order = tk.StringVar(value=state["ordre"])
    ttk.Combobox(f, values=ORDRES, textvariable=order, state="readonly", width=38).grid(row=0, column=1, columnspan=2, sticky="w", padx=8)
    ttk.Label(f, text="Format").grid(row=1, column=0, sticky="w", pady=4)
    fmt = tk.StringVar(value=state["format"])
    ttk.Combobox(f, values=FORMATS, textvariable=fmt, state="readonly", width=38).grid(row=1, column=1, columnspan=2, sticky="w", padx=8)
    ttk.Label(f, text="Titre de la legende").grid(row=2, column=0, sticky="w", pady=4)
    title = tk.StringVar(value=state["titre"])
    ttk.Entry(f, textvariable=title, width=40).grid(row=2, column=1, columnspan=2, sticky="w", padx=8)
    ttk.Label(f, text="Couleur du bandeau").grid(row=3, column=0, sticky="w", pady=4)
    swatch = tk.Label(f, width=6, bg=to_hex(state["couleur"]), relief="groove")
    swatch.grid(row=3, column=1, sticky="w", padx=8)

    def pick():
        rgb, _ = colorchooser.askcolor(color=to_hex(state["couleur"]), title="Couleur du bandeau", parent=root)
        if rgb:
            state["couleur"] = [v / 255.0 for v in rgb]
            swatch.configure(bg=to_hex(state["couleur"]))

    ttk.Button(f, text="Choisir...", command=pick).grid(row=3, column=2, sticky="w")
    surfaces = tk.BooleanVar(value=state["surfaces"])
    ttk.Checkbutton(f, text="Ajouter les surfaces dans la legende", variable=surfaces).grid(row=4, column=1, columnspan=2, sticky="w", padx=8)

    status = tk.StringVar(value="La legende et les pastilles se deplacent librement ; Mettre a jour garde leurs positions.")
    ttk.Label(f, textvariable=status, foreground="#555", wraplength=460).grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def collect():
        state.update(ordre=order.get(), format=fmt.get(), titre=title.get(), surfaces=bool(surfaces.get()))
        T.save_json(REGLAGES, state)
        return dict(state)

    def do(action):
        s = collect()
        try:
            ordered = None
            if action == "numeroter" and s["ordre"] == ORDRES[1]:
                root.withdraw()
                try:
                    ordered = pick_order(T.tapir("GetStories").get("actStory", 0))
                finally:
                    root.deiconify()
            status.set("En cours...")
            root.update_idletasks()
            status.set(run(action, s, ordered))
        except Exception as error:  # noqa: BLE001
            root.deiconify()
            status.set(f"Erreur : {error}")

    b = ttk.Frame(f)
    b.grid(row=6, column=0, columnspan=3, pady=(12, 0), sticky="e")
    ttk.Button(b, text="Supprimer", command=lambda: do("supprimer")).grid(row=0, column=0, padx=4)
    ttk.Button(b, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    ttk.Button(b, text="Mettre a jour (garde les positions)", command=lambda: do("mettre_a_jour")).grid(row=0, column=2, padx=4)
    ttk.Button(b, text="Numeroter", command=lambda: do("numeroter")).grid(row=0, column=3, padx=4)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=T.PORT)
    args, _ = parser.parse_known_args()
    T.PORT = args.port
    settings = load_settings()
    try:
        window(settings)
    except ImportError as error:
        print(f"(Fenetre indisponible : {error})")
        print(run("numeroter", settings))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
