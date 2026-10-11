# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Tableau des surfaces - script Tapir (Archicad 28, mode normal)

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis dans Archicad : palette Tapir > choisir "TableauSurfaces.py" > Executer.

Une petite fenetre s'ouvre et reste ouverte : couleur du bandeau, titres, texte
du total et mots qui reperent les surfaces annexes. "Appliquer" dessine, a droite
du plan de l'etage courant, deux tableaux :

    SURFACES HABITABLES            (bandeau de couleur, titre en blanc)
    RDC                            (un intertitre par niveau)
    piece a vivre        25,04
    ...
    ToTaL                48,87

    SURFACES ANNEXES               (terrasse, garage, balcon...)
    Terrasse             22,84
    ToTaL                22,84

Toutes les zones du projet sont prises, niveau par niveau.
Relancer le script remplace les tableaux precedents.

Le bandeau est un vrai remplissage Archicad : apres coup, on peut changer son
motif, ses stylos, etc. comme n'importe quel remplissage.
Chaque zone a son code couleur (case a gauche du nom) : la couleur de sa
categorie de zone, une couleur differente par zone.
Stylos reserves dans le jeu de stylos actif : 212 (texte et traits, noir),
213 (texte du bandeau, blanc), 214 (couleur du bandeau), 255, 254... (zones).
"""

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- reglages ---
ECHELLE = 100                   # echelle du plan (100 = 1:100)
COULEUR_BANDEAU = (0.93, 0.11, 0.14)   # rouge par defaut (rouge, vert, bleu entre 0 et 1)
TITRE_HABITABLES = "SURFACES HABITABLES"
TITRE_ANNEXES = "SURFACES ANNEXES"
TEXTE_TOTAL = "ToTaL"
MOTS_ANNEXES = "terrasse, balcon, loggia, garage, parking, carport, veranda, véranda, cour, jardin, patio, porche, auvent, annexe, local technique"

COL_NOM_MM = 50.0               # largeur de la colonne des noms (mm papier)
COL_SURFACE_MM = 22.0           # largeur de la colonne des surfaces (mm papier)
BANDEAU_MM = 6.0                # hauteur du bandeau de titre
LIGNE_MM = 4.2                  # hauteur d'une ligne
TEXTE_MM = 2.5                  # hauteur du texte courant
TITRE_MM = 3.0                  # hauteur du texte du bandeau
ECART_PLAN_MM = 15.0            # espace entre le plan et les tableaux
ECART_TABLEAUX_MM = 8.0         # espace entre les deux tableaux
TRAITS_ENTRE_LIGNES = True      # trait horizontal sous chaque ligne (lecture plus facile)
TRAIT_FIN_MM = 0.18             # epaisseur des traits interieurs
TRAIT_CADRE_MM = 0.35           # epaisseur du cadre et du trait du total

CASE_MM = 6.0                   # largeur de la case de couleur de chaque zone
STYLOS_ZONES = 40               # stylos 255, 254, ... = couleurs des zones (max de zones colorees)
STYLO_TRAIT = 212
STYLO_BLANC = 213
STYLO_BANDEAU = 214

PORT = 19723
MEMOIRE = "tableaux_surfaces.json"
REGLAGES = "tableau_surfaces_reglages.json"


# ------------------------------------------------------------ communication ---
def _post(payload: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def api(command: str, parameters: dict | None = None) -> dict:
    payload = {"command": command}
    if parameters is not None:
        payload["parameters"] = parameters
    answer = _post(payload)
    if not answer.get("succeeded"):
        raise RuntimeError(f"{command} : {answer.get('error')}")
    return answer.get("result", {})


def tapir(command: str, parameters: dict | None = None) -> dict:
    result = api("API.ExecuteAddOnCommand", {
        "addOnCommandId": {"commandNamespace": "TapirCommand", "commandName": command},
        "addOnCommandParameters": parameters or {},
    })
    response = result.get("addOnCommandResponse", {})
    if isinstance(response, dict) and response.get("success") is False:
        raise RuntimeError(f"{command} : {response.get('error')}")
    return response


def ok_items(items: list[dict]) -> list[dict]:
    return [item for item in items if "error" not in item]


# ---------------------------------------------------------------- fichiers ---
def data_folder() -> Path:
    folder = Path(os.environ.get("APPDATA", Path.home())) / "ClaudeBridge"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_json(name: str) -> dict:
    try:
        return json.loads((data_folder() / name).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_json(name: str, data: dict) -> None:
    (data_folder() / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_settings() -> dict:
    saved = load_json(REGLAGES)
    color = saved.get("couleur")
    if not (isinstance(color, list) and len(color) == 3):
        color = list(COULEUR_BANDEAU)
    return {
        "couleur": [float(c) for c in color],
        "titre_habitables": str(saved.get("titre_habitables", TITRE_HABITABLES)),
        "titre_annexes": str(saved.get("titre_annexes", TITRE_ANNEXES)),
        "texte_total": str(saved.get("texte_total", TEXTE_TOTAL)),
        "mots_annexes": str(saved.get("mots_annexes", MOTS_ANNEXES)),
        "codes_couleur": bool(saved.get("codes_couleur", True)),
    }


# ----------------------------------------------------------------- outils ---
def polygon_area(points: list[dict]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:] + points[:1]):
        total += a["x"] * b["y"] - b["x"] * a["y"]
    return abs(total) / 2.0


def format_area(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def story_label(index: int) -> str:
    if index == 0:
        return "RDC"
    if index > 0:
        return f"R+{index}"
    return "Sous-sol" if index == -1 else f"Sous-sol {-index}"


# ---------------------------------------------------------------- lecture ---
def read_zones() -> list[dict]:
    elements = tapir("GetElementsByType", {"elementType": "Zone"}).get("elements", [])
    if not elements:
        return []
    details = tapir("GetDetailsOfElements", {"elements": elements}).get("detailsOfElements", [])
    zones = []
    for element, detail in zip(elements, details):
        if "error" in detail:
            continue
        d = detail.get("details", {})
        zones.append({
            "element": element,
            "story": detail.get("floorIndex", 0),
            "number": d.get("numberStr", ""),
            "name": d.get("name", ""),
            "category": d.get("categoryAttributeId", {}).get("guid"),
            "outline": d.get("polygonOutline", []),
            "holes": d.get("holes", []),
        })
    return zones


def fill_areas(zones: list[dict]) -> None:
    try:  # 1) surface calculee par Archicad
        names = api("API.GetAllPropertyNames").get("properties", [])
        builtin = [p["nonLocalizedName"] for p in names if p.get("type") == "BuiltIn" and "nonLocalizedName" in p]
        preferred = ["Zone_CalculatedArea", "Zone_MeasuredArea", "Zone_NetArea", "Zone_ZoneArea", "Zone_GrossArea"]
        choice = next((n for n in preferred if n in builtin), None) or \
            next((n for n in builtin if n.startswith("Zone_") and "Area" in n), None)
        if choice:
            pid = api("API.GetPropertyIds", {"properties": [{"type": "BuiltIn", "nonLocalizedName": choice}]})["properties"][0]["propertyId"]
            values = api("API.GetPropertyValuesOfElements", {
                "elements": [z["element"] for z in zones], "properties": [{"propertyId": pid}],
            }).get("propertyValuesForElements", [])
            for zone, value in zip(zones, values):
                pv = value.get("propertyValues", [{}])[0].get("propertyValue", {})
                if pv.get("status", "normal") == "normal" and isinstance(pv.get("value"), (int, float)):
                    zone["area"] = float(pv["value"])
    except Exception as error:  # noqa: BLE001
        print(f"(Surface Archicad indisponible, calcul geometrique : {error})")
    for zone in zones:  # 2) a defaut : contour moins les trous
        if "area" not in zone:
            area = polygon_area(zone["outline"]) if len(zone["outline"]) >= 3 else 0.0
            for hole in zone["holes"]:
                pts = hole.get("polygonOutline", [])
                if len(pts) >= 3:
                    area -= polygon_area(pts)
            zone["area"] = max(area, 0.0)


def fill_categories(zones: list[dict]) -> None:
    guids = sorted({z["category"] for z in zones if z["category"]})
    names = {}
    if guids:
        try:
            cats = tapir("GetZoneCategories", {"attributeIds": [{"attributeId": {"guid": g}} for g in guids],
                                               "fields": ["categoryCode", "color"]}).get("zoneCategories", [])
            for guid, cat in zip(guids, cats):
                if "error" not in cat:
                    c = cat.get("color", {})
                    names[guid] = (cat.get("name", ""), (c.get("red", 0.8), c.get("green", 0.8), c.get("blue", 0.8)))
        except Exception:  # noqa: BLE001
            pass
    for zone in zones:
        zone["category_name"], zone["color"] = names.get(zone["category"], ("", (0.8, 0.8, 0.8)))


def make_colors_unique(zones: list[dict]) -> None:
    """Deux zones de meme couleur : la suivante est eclaircie ou assombrie (une couleur par zone)."""
    seen = []
    for zone in zones:
        base, color, step = zone["color"], zone["color"], 0
        light = sum(base) / 3 > 0.6          # couleur claire : on assombrit d'abord
        while any(math.dist(color, other) < 0.12 for other in seen) and step < 12:
            step += 1
            amount = 0.18 * ((step + 1) // 2)
            if (step % 2 == 1) != light:
                color = tuple(min(1.0, c + (1.0 - c) * amount) for c in base)
            else:
                color = tuple(max(0.0, c * (1.0 - amount)) for c in base)
        zone["color"] = color
        seen.append(color)


def is_annex(zone: dict, words: list[str]) -> bool:
    text = f"{zone['name']} {zone['category_name']}".lower()
    return any(word and word in text for word in words)


# ------------------------------------------------------------------ stylos ---
def set_pens(color: list[float], zones: list[dict]) -> bool:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables]
    details = [t for t in tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                ).get("penTables", []) if "error" not in t]
    table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
    if table is None:
        return False
    widths = {p.get("index"): p.get("width", 0.1) for p in table.get("pens", [])}

    def pen(index, rgb, description):
        return {"index": index, "width": widths.get(index, 0.1), "description": description,
                "color": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}

    answer = tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
        "attributeId": table["attributeId"], "name": table["name"], "pens": [
            pen(STYLO_TRAIT, (0.0, 0.0, 0.0), "Tableau des surfaces - texte et traits"),
            pen(STYLO_BLANC, (1.0, 1.0, 1.0), "Tableau des surfaces - titre"),
            pen(STYLO_BANDEAU, color, "Tableau des surfaces - bandeau"),
        ] + [pen(z["pen"], z["color"], f"Tableau des surfaces - {z['name'] or z['number']}"[:120])
             for z in zones if z.get("pen")]}]})
    return "error" not in answer.get("attributeIds", [{}])[0]


def solid_fill_id() -> dict | None:
    fills = tapir("GetAttributesByType", {"attributeType": "Fill"}).get("attributes", [])
    ids = [{"attributeId": f["attributeId"]} for f in fills]
    for fill in tapir("GetFills", {"attributeIds": ids, "fields": ["subType"]}).get("fills", []):
        if fill.get("subType") == "Solid":
            return fill["attributeId"]
    return None


# ------------------------------------------------------------------ dessin ---
class Drawing:
    def __init__(self, story: int, solid: dict | None):
        self.story, self.solid = story, solid
        self.mm = ECHELLE / 1000.0
        self.texts, self.hatches, self.lines = [], [], []

    def text(self, content, x, y, anchor, bold=False, size=TEXTE_MM, pen=STYLO_TRAIT):
        justification = {"L": "Left", "M": "Center", "R": "Right"}[anchor[0]]
        self.texts.append({
            "coordinate": {"x": x, "y": y, "z": 0.0}, "text": content, "floorIndex": self.story,
            "style": {"height": size, "bold": bold, "penIndex": pen, "justification": justification,
                      "anchor": {"L": "LeftMiddle", "M": "MiddleMiddle", "R": "RightMiddle"}[anchor[0]]},
        })

    def line(self, xa, ya, xb, yb, weight=TRAIT_FIN_MM):
        self.lines.append({"floorInd": self.story, "linePenIndex": STYLO_TRAIT, "penWeightMm": weight,
                           "coordinates": [{"x": xa, "y": ya}, {"x": xb, "y": yb}]})

    def band(self, x1, y1, x2, y2):
        self.swatch(x1, y1, x2, y2, STYLO_BANDEAU)

    def swatch(self, x1, y1, x2, y2, pen):
        hatch = {"floorInd": self.story, "showArea": False,
                 "coordinates": [{"x": x1, "y": y1}, {"x": x2, "y": y1}, {"x": x2, "y": y2}, {"x": x1, "y": y2}],
                 "fillPenIndex": pen, "fillBackgroundPenIndex": pen, "contourPenIndex": pen}
        if self.solid:
            hatch["fillId"] = self.solid
        self.hatches.append(hatch)

    def table(self, x0, top, title, groups, total_label, show_headings) -> float:
        """Dessine un tableau ; renvoie l'ordonnee de son bas."""
        mm = self.mm
        width = (COL_NOM_MM + COL_SURFACE_MM) * mm
        sep = x0 + COL_NOM_MM * mm
        row = LIGNE_MM * mm
        pad = 1.2 * mm
        value_x = sep + COL_SURFACE_MM * mm / 2       # surfaces centrees dans leur colonne

        band_bottom = top - BANDEAU_MM * mm
        self.band(x0, band_bottom, x0 + width, top)
        self.text(title, x0 + width / 2, (top + band_bottom) / 2, "M", bold=True, size=TITRE_MM, pen=STYLO_BLANC)

        y = band_bottom
        total = 0.0
        row_lines = []
        for heading, zones in groups:
            if show_headings:
                y -= row
                self.text(heading, x0 + pad, y + row / 2, "L", bold=True)
                row_lines.append(y)
            for zone in zones:
                y -= row
                label = zone["name"] or zone["number"] or "Zone"
                text_x = x0 + pad
                if zone.get("pen"):                                         # code couleur de la zone
                    self.swatch(x0 + pad, y + row * 0.2, x0 + pad + CASE_MM * mm, y + row * 0.8, zone["pen"])
                    text_x = x0 + pad * 2 + CASE_MM * mm
                self.text(label, text_x, y + row / 2, "L")
                self.text(format_area(zone["area"]), value_x, y + row / 2, "M")
                total += zone["area"]
                row_lines.append(y)
        body_bottom = y
        if TRAITS_ENTRE_LIGNES:
            for line_y in row_lines[:-1]:                                     # trait sous chaque ligne
                self.line(x0, line_y, x0 + width, line_y)
        y -= row * 1.1
        self.text(total_label, x0 + COL_NOM_MM * mm / 2, y + row * 0.55, "M", bold=True, size=TEXTE_MM * 1.1)
        self.text(format_area(total), value_x, y + row * 0.55, "M", bold=True, size=TEXTE_MM * 1.1)

        self.line(x0, band_bottom, x0 + width, band_bottom)                       # sous le bandeau
        self.line(x0, body_bottom, x0 + width, body_bottom, TRAIT_CADRE_MM)       # au-dessus du total
        self.line(sep, band_bottom, sep, y)                                       # trait vertical
        for xa, ya, xb, yb in ((x0, top, x0 + width, top), (x0 + width, top, x0 + width, y),
                               (x0 + width, y, x0, y), (x0, y, x0, top)):
            self.line(xa, ya, xb, yb, TRAIT_CADRE_MM)                             # cadre
        return y


# -------------------------------------------------------------------- run ---
def group_by_story(zones: list[dict]) -> list[tuple[str, list[dict]]]:
    stories = sorted({z["story"] for z in zones})
    return [(story_label(s), sorted((z for z in zones if z["story"] == s), key=lambda z: (z["number"], z["name"])))
            for s in stories]


def run(action: str, settings: dict) -> str:
    stories = tapir("GetStories")
    current = stories.get("actStory", 0)
    project = tapir("GetProjectInfo")
    project_key = project.get("projectPath") or project.get("projectName") or "Sans titre"

    # supprimer les tableaux precedents de ce projet (toutes versions)
    memory = load_json(MEMOIRE)
    old = [g for key, guids in memory.items() if key.split("|")[0] == project_key for g in guids]
    if old:
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in old]})
        except Exception:  # noqa: BLE001
            pass
    memory = {k: v for k, v in memory.items() if k.split("|")[0] != project_key}
    save_json(MEMOIRE, memory)
    if action == "supprimer":
        return "Tableaux des surfaces supprimes."

    zones = read_zones()
    if not zones:
        return "Aucune zone dans le projet : rien a mettre dans le tableau."
    fill_areas(zones)
    fill_categories(zones)
    words = [w.strip().lower() for w in settings["mots_annexes"].split(",") if w.strip()]
    habitable = [z for z in zones if not is_annex(z, words)]
    annexes = [z for z in zones if is_annex(z, words)]

    ordered = [z for _, g in group_by_story(habitable) for z in g] + [z for _, g in group_by_story(annexes) for z in g]
    if settings.get("codes_couleur", True):
        make_colors_unique(ordered)
        for i, zone in enumerate(ordered[:STYLOS_ZONES]):
            zone["pen"] = 255 - i
    warning = "" if set_pens(settings["couleur"], ordered) else " Attention : couleurs non appliquees (stylos 212-214)."
    drawing = Drawing(current, solid_fill_id())

    plan = [z for z in zones if z["story"] == current] or zones
    xs = [p["x"] for z in plan for p in z["outline"]]
    ys = [p["y"] for z in plan for p in z["outline"]]
    mm = drawing.mm
    x0 = max(xs) + ECART_PLAN_MM * mm
    top = max(ys)

    if habitable:
        top = drawing.table(x0, top, settings["titre_habitables"], group_by_story(habitable),
                            settings["texte_total"], show_headings=True) - ECART_TABLEAUX_MM * mm
    if annexes:
        groups = group_by_story(annexes)
        drawing.table(x0, top, settings["titre_annexes"], groups, settings["texte_total"],
                      show_headings=len(groups) > 1)

    created = []
    created += ok_items(tapir("CreateHatches", {"hatchesData": drawing.hatches}).get("elements", []))
    created += ok_items(tapir("CreatePolylines", {"polylinesData": drawing.lines}).get("elements", []))
    created += ok_items(tapir("CreateTexts", {"textsData": drawing.texts}).get("elements", []))
    try:
        tapir("CreateGroups", {"elementGroups": [{"elements": created}]})
    except Exception:  # noqa: BLE001
        pass

    memory[f"{project_key}|tableau"] = [e["elementId"]["guid"] for e in created if "elementId" in e]
    save_json(MEMOIRE, memory)
    total_h = sum(z["area"] for z in habitable)
    total_a = sum(z["area"] for z in annexes)
    return (f"Tableaux dessines : {len(habitable)} zone(s) habitable(s) = {format_area(total_h)} m2, "
            f"{len(annexes)} annexe(s) = {format_area(total_a)} m2.{warning}")


# ---------------------------------------------------------------- fenetre ---
def settings_window(settings: dict, apply) -> None:
    import tkinter as tk
    from tkinter import colorchooser, ttk

    def to_hex(c):
        return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in c)

    state = dict(settings)
    root = tk.Tk()
    root.title("Tableau des surfaces - Claude Bridge")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.grid()

    ttk.Label(frame, text="Couleur du bandeau").grid(row=0, column=0, sticky="w", pady=4)
    swatch = tk.Label(frame, width=6, bg=to_hex(state["couleur"]), relief="groove")
    swatch.grid(row=0, column=1, sticky="w", padx=8)

    def pick():
        rgb, _ = colorchooser.askcolor(color=to_hex(state["couleur"]), title="Couleur du bandeau", parent=root)
        if rgb:
            state["couleur"] = [v / 255.0 for v in rgb]
            swatch.configure(bg=to_hex(state["couleur"]))

    def default_color():
        state["couleur"] = list(COULEUR_BANDEAU)
        swatch.configure(bg=to_hex(state["couleur"]))

    colors = ttk.Frame(frame)
    colors.grid(row=0, column=2, sticky="w")
    ttk.Button(colors, text="Choisir...", command=pick).grid(row=0, column=0, padx=(0, 4))
    ttk.Button(colors, text="Rouge par defaut", command=default_color).grid(row=0, column=1)

    fields = {}
    for row, (key, label) in enumerate((("titre_habitables", "Titre du 1er tableau"),
                                        ("titre_annexes", "Titre du 2e tableau"),
                                        ("texte_total", "Texte du total"),
                                        ("mots_annexes", "Zones annexes si le nom\nou la categorie contient")), start=1):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        var = tk.StringVar(value=state[key])
        ttk.Entry(frame, textvariable=var, width=48).grid(row=row, column=1, columnspan=2, sticky="we", padx=8)
        fields[key] = var

    swatches = tk.BooleanVar(value=state.get("codes_couleur", True))
    ttk.Checkbutton(frame, text="Afficher le code couleur de chaque zone", variable=swatches
                    ).grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=4)

    status = tk.StringVar(value="Regle puis clique sur Appliquer : le tableau est redessine dans Archicad.")
    ttk.Label(frame, textvariable=status, foreground="#555", wraplength=460).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def do(action):
        for key, var in fields.items():
            state[key] = var.get()
        state["codes_couleur"] = bool(swatches.get())
        save_json(REGLAGES, state)
        status.set("En cours...")
        root.update_idletasks()
        try:
            status.set(apply(action, dict(state)))
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")

    buttons = ttk.Frame(frame)
    buttons.grid(row=7, column=0, columnspan=3, pady=(12, 0), sticky="e")
    ttk.Button(buttons, text="Supprimer le tableau", command=lambda: do("supprimer")).grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    ttk.Button(buttons, text="Appliquer", command=lambda: do("dessiner")).grid(row=0, column=2, padx=4)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.mainloop()


def main() -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args, _ = parser.parse_known_args()
    PORT = args.port

    settings = load_settings()
    try:
        settings_window(settings, run)
        return 0
    except ImportError as error:
        print(f"(Fenetre de reglages indisponible : {error}. Reglages enregistres utilises.)")
    print(run("dessiner", settings))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
