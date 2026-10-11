# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Tableau des surfaces - script Tapir (Archicad 28, mode normal)

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis dans Archicad : palette Tapir > choisir "TableauSurfaces.py" > Executer.

1. Creer les zones (pieces), plusieurs a la fois :
   - "Cliquer dans les pieces..." : un clic dans chaque piece fermee par des murs,
     Archicad trouve ses limites tout seul (Echap pour terminer) ;
   - "Dessiner une piece (contour)..." : cliquer les coins de la piece comme on
     veut (clic sur le 1er point ou Echap pour fermer) ; le tableau suit a chaque piece ;
   - "Zones depuis la selection" : selectionner des polylignes fermees ou des
     remplissages dessines comme on veut -> une zone pour chacun, a cette forme ;
   - "Mettre a jour zones + tableau" : apres avoir bouge des murs ou retouche
     des zones, recalcule les zones puis redessine le tableau.

2. Tableau. La fenetre reste ouverte : couleur du bandeau, titres, texte
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
import time
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
PALETTE = [(0.98, 0.84, 0.55), (0.62, 0.78, 0.55), (0.55, 0.72, 0.90), (0.95, 0.62, 0.55),
           (0.78, 0.66, 0.88), (0.98, 0.72, 0.40), (0.55, 0.82, 0.80), (0.90, 0.60, 0.75),
           (0.80, 0.80, 0.45), (0.70, 0.60, 0.50)]   # couleurs de secours (jamais de blanc)
STYLO_CONTOUR = 211             # stylo des traits provisoires du contour (couleur vive)
COULEUR_CONTOUR = (1.0, 0.35, 0.0)    # orange vif
SURBRILLANCE = (255, 90, 0, 255)      # surbrillance Archicad du contour en cours
TRAIT_CONTOUR_MM = 0.7          # epaisseur des traits provisoires
MARQUE_MM = 2.5                 # taille du repere sur le 1er point
FERMETURE_MM = 1.5              # clic a moins de 1,5 mm (papier) du 1er point = contour ferme
STYLO_TRAIT = 212
STYLO_BLANC = 213
STYLO_BANDEAU = 214

PORT = 19723
MEMOIRE = "tableaux_surfaces.json"
REGLAGES = "tableau_surfaces_reglages.json"


# ------------------------------------------------------------ communication ---
def _post(payload: dict, timeout: float = 120) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def api(command: str, parameters: dict | None = None, timeout: float = 120) -> dict:
    payload = {"command": command}
    if parameters is not None:
        payload["parameters"] = parameters
    answer = _post(payload, timeout)
    if not answer.get("succeeded"):
        raise RuntimeError(f"{command} : {answer.get('error')}")
    return answer.get("result", {})


def tapir(command: str, parameters: dict | None = None, timeout: float = 120) -> dict:
    result = api("API.ExecuteAddOnCommand", {
        "addOnCommandId": {"commandNamespace": "TapirCommand", "commandName": command},
        "addOnCommandParameters": parameters or {},
    }, timeout)
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
    for index, zone in enumerate(zones):
        if is_whiteish(zone["color"]):                       # pas de blanc : couleur de la palette
            zone["color"] = PALETTE[index % len(PALETTE)]
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
            pen(STYLO_CONTOUR, COULEUR_CONTOUR, "Tableau des surfaces - contour en cours"),
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


# ------------------------------------------------------------------ zones ---
def zone_categories() -> list[tuple[str, dict, tuple]]:
    """(nom, attributeId, couleur) des categories de zones du projet."""
    attrs = [a for a in tapir("GetAttributesByType", {"attributeType": "ZoneCategory"}).get("attributes", [])
             if "attributeId" in a]
    colors = {}
    if attrs:
        try:
            cats = tapir("GetZoneCategories", {"attributeIds": [{"attributeId": a["attributeId"]} for a in attrs],
                                               "fields": ["color"]}).get("zoneCategories", [])
            for a, cat in zip(attrs, cats):
                c = cat.get("color", {}) if isinstance(cat, dict) else {}
                colors[a["attributeId"]["guid"]] = (c.get("red", 1.0), c.get("green", 1.0), c.get("blue", 1.0))
        except Exception:  # noqa: BLE001
            pass
    return [(a.get("name", "?"), a["attributeId"], colors.get(a["attributeId"]["guid"], (1.0, 1.0, 1.0))) for a in attrs]


def is_whiteish(color) -> bool:
    return min(color) > 0.92


def auto_category(categories: list, used_guids: list[str]) -> dict | None:
    """Une categorie coloree (pas blanche), de preference pas encore utilisee : une couleur par piece."""
    colored = [c for c in categories if not is_whiteish(c[2])]
    if not colored:
        return None
    unused = [c for c in colored if c[1]["guid"] not in used_guids]
    pick = unused[0] if unused else colored[len(used_guids) % len(colored)]
    return pick[1]


def next_zone_number(zones: list[dict]) -> int:
    numbers = [int(z["number"]) for z in zones if str(z.get("number", "")).strip().isdigit()]
    return (max(numbers) + 1) if numbers else 1


def new_zone(story: int, name: str, number: int, category: dict | None, geometry: dict, stamp=None) -> str | None:
    """Cree une zone ; renvoie un message d'erreur ou None."""
    data = {"floorIndex": story, "name": name, "numberStr": f"{number:02d}", "geometry": geometry}
    if category:
        data["categoryAttributeId"] = category
    if stamp:
        data["stampPosition"] = stamp
    result = tapir("CreateZones", {"zonesData": [data]}).get("elements", [{}])
    item = result[0] if result else {}
    if "error" in item:
        return str(item["error"].get("message", item["error"]) if isinstance(item["error"], dict) else item["error"])
    return None


def focus_archicad() -> None:
    """Remet la fenetre d'Archicad au premier plan (Windows), pour que le clic aille au plan."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if "archicad" in buffer.value.lower():
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(callback, 0)
        if found:
            user32.ShowWindow(found[0], 9)          # SW_RESTORE si reduite
            user32.SetForegroundWindow(found[0])
    except Exception:  # noqa: BLE001
        pass


TRAITS = {"Droits (0 / 90 degres)": 90.0, "Droits + 45 degres": 45.0, "Libres": 0.0}


def constrain(prev: dict, point: dict, step_deg: float) -> dict:
    """Redresse le trait prev -> point sur l'angle autorise le plus proche (0/90 ou 45 degres)."""
    if step_deg <= 0:
        return point
    dx, dy = point["x"] - prev["x"], point["y"] - prev["y"]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return point
    step = math.radians(step_deg)
    angle = round(math.atan2(dy, dx) / step) * step
    length = dx * math.cos(angle) + dy * math.sin(angle)
    return {"x": prev["x"] + length * math.cos(angle), "y": prev["y"] + length * math.sin(angle)}


def is_aligned(a: dict, b: dict, step_deg: float) -> bool:
    if step_deg <= 0:
        return True
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    if math.hypot(dx, dy) < 1e-6:
        return True
    step = math.radians(step_deg)
    angle = math.atan2(dy, dx)
    return abs(angle - round(angle / step) * step) < math.radians(0.5)


def closing_corner(points: list[dict], step_deg: float) -> dict | None:
    """Coin a ajouter pour fermer le contour avec des traits droits (None si deja aligne)."""
    first, last = points[0], points[-1]
    if is_aligned(last, first, step_deg):
        return None
    best, best_area = None, -1.0
    for corner in ({"x": first["x"], "y": last["y"]}, {"x": last["x"], "y": first["y"]}):
        poly = points + [corner]
        area = abs(sum(p["x"] * q["y"] - q["x"] * p["y"] for p, q in zip(poly, poly[1:] + poly[:1])))
        if area > best_area:
            best, best_area = corner, area
    return best


def highlight(guids: list[str]) -> None:
    """Surbrillance Archicad des elements (liste vide = enlever la surbrillance)."""
    try:
        tapir("HighlightElements", {"elements": [{"elementId": {"guid": g}} for g in guids],
                                    "highlightedColors": [list(SURBRILLANCE)] * len(guids)})
    except Exception:  # noqa: BLE001
        pass


def drop_collinear(points: list[dict]) -> list[dict]:
    """Retire les sommets inutiles (alignes avec leurs voisins)."""
    pts = list(points)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b["x"] - a["x"]) * (c["y"] - b["y"]) - (b["y"] - a["y"]) * (c["x"] - b["x"])
            if abs(cross) < 1e-6:
                pts.pop(i)
                changed = True
                break
    return pts


def pick_point(prompt: str) -> tuple[dict | None, str]:
    """Attend un clic dans Archicad. Renvoie (point, "") ou (None, raison)."""
    try:
        answer = tapir("GetPointFromUser", {"prompt": prompt}, timeout=3600)
    except Exception as error:  # noqa: BLE001
        return None, str(error)
    pos = answer.get("position") if isinstance(answer, dict) else None
    if not pos or "error" in answer:
        return None, str(answer.get("error", "")) if isinstance(answer, dict) else ""
    return {"x": pos["x"], "y": pos["y"]}, ""


def selected_outlines() -> list[tuple[list[dict], list[dict], list[dict]]]:
    """Contours (points, arcs, trous) des polylignes et remplissages selectionnes."""
    try:
        selection = api("API.GetSelectedElements").get("elements", [])
    except Exception:  # noqa: BLE001
        selection = tapir("GetSelectedElements").get("elements", [])
    if not selection:
        return []
    details = tapir("GetDetailsOfElements", {"elements": selection, "fields": ["type", "details"]}
                    ).get("detailsOfElements", [])
    outlines = []
    for detail in details:
        if "error" in detail:
            continue
        kind = detail.get("type", "")
        d = detail.get("details", {})
        if kind in ("PolyLine", "Hatch"):
            pts = [{"x": c["x"], "y": c["y"]} for c in d.get("coordinates", [])]   # tel quel : les arcs s'y referent
            if len(pts) >= 3:
                holes = [{"polygonOutline": h.get("polygonOutline", []), "polygonArcs": h.get("polygonArcs", [])}
                         for h in d.get("holes", [])] if kind == "Hatch" else []
                outlines.append((pts, d.get("arcs", []), holes))
    return outlines


def centroid(points: list[dict]) -> dict:
    if len(points) > 3 and abs(points[0]["x"] - points[-1]["x"]) < 1e-9 and abs(points[0]["y"] - points[-1]["y"]) < 1e-9:
        points = points[:-1]
    a = cx = cy = 0.0
    for p, q in zip(points, points[1:] + points[:1]):
        cross = p["x"] * q["y"] - q["x"] * p["y"]
        a += cross
        cx += (p["x"] + q["x"]) * cross
        cy += (p["y"] + q["y"]) * cross
    if abs(a) < 1e-12:
        return {"x": sum(p["x"] for p in points) / len(points), "y": sum(p["y"] for p in points) / len(points)}
    return {"x": cx / (3 * a), "y": cy / (3 * a)}


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
    from tkinter import colorchooser, simpledialog, ttk

    def to_hex(c):
        return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in c)

    state = dict(settings)
    root = tk.Tk()
    root.title("Tableau des surfaces - Claude Bridge")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    outer = ttk.Frame(root, padding=12)
    outer.grid()
    status = tk.StringVar(value="1. Cree tes zones   2. Regle le tableau puis Appliquer.")

    def busy(text):
        status.set(text)
        root.update_idletasks()

    # ------------------------------------------------------------ 1. zones
    zbox = ttk.LabelFrame(outer, text=" 1. Creer les zones (pieces) ", padding=10)
    zbox.grid(row=0, column=0, sticky="we")

    try:
        categories = zone_categories()
    except Exception:  # noqa: BLE001
        categories = []
    ttk.Label(zbox, text="Categorie").grid(row=0, column=0, sticky="w", pady=3)
    AUTO = "(automatique : une couleur par piece)"
    category_names = [AUTO] + [n for n, _, _ in categories]
    category = tk.StringVar(value=state.get("categorie", AUTO) if state.get("categorie") in category_names else AUTO)
    ttk.Combobox(zbox, values=category_names, textvariable=category, state="readonly", width=30
                 ).grid(row=0, column=1, sticky="w", padx=8)

    ttk.Label(zbox, text="Nom par defaut").grid(row=1, column=0, sticky="w", pady=3)
    base_name = tk.StringVar(value=state.get("nom_piece", "Piece"))
    ttk.Entry(zbox, textvariable=base_name, width=32).grid(row=1, column=1, sticky="w", padx=8)
    ask_name = tk.BooleanVar(value=state.get("demander_nom", True))
    ttk.Checkbutton(zbox, text="Demander le nom de chaque piece", variable=ask_name
                    ).grid(row=2, column=1, sticky="w", padx=8)

    ttk.Label(zbox, text="Traits du contour").grid(row=3, column=0, sticky="w", pady=3)
    lines_mode = tk.StringVar(value=state.get("traits", "Droits (0 / 90 degres)")
                              if state.get("traits") in TRAITS else "Droits (0 / 90 degres)")
    ttk.Combobox(zbox, values=list(TRAITS), textvariable=lines_mode, state="readonly", width=30
                 ).grid(row=3, column=1, sticky="w", padx=8)

    def chosen_category():
        if category.get() == AUTO:
            used = [z["category"] for z in read_zones() if z.get("category")]
            return auto_category(categories, used)
        return next((aid for n, aid, _ in categories if n == category.get()), None)

    def remember():
        state["categorie"] = category.get()
        state["nom_piece"] = base_name.get()
        state["demander_nom"] = bool(ask_name.get())
        state["traits"] = lines_mode.get()

    def by_clicks():
        """Piece par piece : chaque clic cree la zone et met le tableau a jour aussitot."""
        remember()
        story = tapir("GetStories").get("actStory", 0)
        number = next_zone_number(read_zones())
        created, missed = 0, 0
        last = "Clique dans une piece fermee par des murs. Echap pour terminer."
        root.withdraw()
        try:
            while True:
                focus_archicad()
                started = time.time()
                point, reason = pick_point(f"Piece {number} : clique dedans (Echap pour terminer)")
                if point is None:
                    if created == 0 and missed == 0 and time.time() - started < 1.0:
                        last = ("Archicad n'a pas pu attendre le clic. Mets la fenetre du plan au premier plan "
                                f"puis recommence. ({reason})")
                    break
                name = f"{base_name.get()} {number}"
                if ask_name.get():
                    root.deiconify()
                    answer = simpledialog.askstring("Nom de la piece", "Nom de cette piece :", initialvalue=name, parent=root)
                    root.withdraw()
                    if answer is None:
                        continue
                    name = answer.strip() or name
                error = new_zone(story, name, number, chosen_category(), {"referencePosition": point}, point)
                if error:
                    missed += 1
                    last = f"Clic hors d'une piece fermee par des murs ({error}). Clique ailleurs ou Echap."
                    print(last)
                    continue
                created += 1
                number += 1
                last = f"{name} creee. " + apply("dessiner", dict(collect()))   # tableau mis a jour tout de suite
                print(last)
        finally:
            root.deiconify()
        status.set((f"{created} piece(s) ajoutee(s) au tableau. " if created else "") + last)

    def room_done_dialog(number: int, default_name: str) -> tuple[str, str]:
        """Contour ferme : nom de la piece + Ajouter au tableau / Recommencer / Terminer."""
        result = {"choice": "terminer", "name": default_name}
        win = tk.Toplevel(root)
        win.title(f"Piece {number} terminee")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        box = ttk.Frame(win, padding=14)
        box.grid()
        ttk.Label(box, text="Contour ferme. Nom de la piece :").grid(row=0, column=0, columnspan=3, sticky="w")
        name_var = tk.StringVar(value=default_name)
        entry = ttk.Entry(box, textvariable=name_var, width=34)
        entry.grid(row=1, column=0, columnspan=3, sticky="we", pady=(4, 10))
        entry.select_range(0, "end")
        entry.focus_set()

        def close(choice):
            result["choice"], result["name"] = choice, (name_var.get().strip() or default_name)
            win.destroy()

        ttk.Button(box, text="Terminer", command=lambda: close("terminer")).grid(row=2, column=0, padx=3)
        ttk.Button(box, text="Recommencer le contour", command=lambda: close("recommencer")).grid(row=2, column=1, padx=3)
        ttk.Button(box, text="Ajouter au tableau", command=lambda: close("ajouter")).grid(row=2, column=2, padx=3)
        win.bind("<Return>", lambda _e: close("ajouter"))
        win.protocol("WM_DELETE_WINDOW", lambda: close("terminer"))
        win.lift()
        win.focus_force()
        root.wait_window(win)
        return result["choice"], result["name"]

    def by_drawing():
        """Dessiner soi-meme le contour de chaque piece, point par point ; le tableau suit a chaque piece."""
        remember()
        story = tapir("GetStories").get("actStory", 0)
        number = next_zone_number(read_zones())
        try:
            set_pens(state["couleur"], [])                  # stylo du contour en surbrillance
        except Exception:  # noqa: BLE001
            pass

        def draw_temp(temp: list, coords: list) -> None:
            """Trait provisoire epais, de couleur vive, en surbrillance."""
            try:
                answer = tapir("CreatePolylines", {"polylinesData": [{
                    "floorInd": story, "coordinates": coords,
                    "linePenIndex": STYLO_CONTOUR, "penWeightMm": TRAIT_CONTOUR_MM}]})
                temp += [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
            except Exception:  # noqa: BLE001
                pass
            highlight(temp)

        close_dist = FERMETURE_MM * ECHELLE / 1000.0
        created = 0
        last = "Dessine les pieces une par une ; chaque contour ferme propose \"Ajouter au tableau\"."
        root.withdraw()
        try:
            while True:
                points, temp = [], []
                while True:
                    focus_archicad()
                    if points:
                        prompt = f"Piece {number} : point {len(points) + 1} (1er point/Echap = fermer, point precedent = annuler)"
                    else:
                        prompt = f"Piece {number} : 1er coin (Echap = terminer)"
                    point, _ = pick_point(prompt)
                    if point is None:
                        break
                    if len(points) >= 3 and math.dist((point["x"], point["y"]), (points[0]["x"], points[0]["y"])) <= close_dist:
                        break
                    if len(points) >= 2 and math.dist((point["x"], point["y"]), (points[-2]["x"], points[-2]["y"])) <= close_dist:
                        points.pop()                     # clic sur l'avant-dernier point : annule le dernier
                        if len(temp) > 1:            # (temp[0] = repere du 1er point)
                            try:
                                tapir("DeleteElements", {"elements": [{"elementId": {"guid": temp.pop()}}]})
                            except Exception:  # noqa: BLE001
                                pass
                            highlight(temp)
                        continue
                    if points:
                        point = constrain(points[-1], point, TRAITS.get(lines_mode.get(), 90.0))
                        if math.dist((point["x"], point["y"]), (points[-1]["x"], points[-1]["y"])) < 1e-4:
                            continue
                    if not points:               # repere en surbrillance sur le 1er point (pour fermer)
                        r = MARQUE_MM * ECHELLE / 1000.0 / 2
                        draw_temp(temp, [{"x": point["x"] - r, "y": point["y"] - r}, {"x": point["x"] + r, "y": point["y"] - r},
                                         {"x": point["x"] + r, "y": point["y"] + r}, {"x": point["x"] - r, "y": point["y"] + r},
                                         {"x": point["x"] - r, "y": point["y"] - r}])
                    if points:   # trait provisoire pour voir le contour se dessiner
                        draw_temp(temp, [points[-1], point])
                    points.append(point)
                if len(points) >= 3:             # traits droits jusqu'au bout : coin de fermeture si besoin
                    corner = closing_corner(points, TRAITS.get(lines_mode.get(), 90.0))
                    if corner is not None:
                        draw_temp(temp, [points[-1], corner])
                        points.append(corner)
                if len(points) >= 3 and temp:   # dernier trait : fermer le contour visible
                    draw_temp(temp, [points[-1], points[0]])

                def clear_temp():
                    highlight([])
                    if temp:
                        try:
                            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in temp]})
                        except Exception:  # noqa: BLE001
                            pass

                if len(points) < 3:
                    clear_temp()
                    break                                   # Echap sans contour : fin
                choice, name = room_done_dialog(number, f"{base_name.get()} {number}")
                clear_temp()
                if choice == "terminer":
                    break
                if choice == "recommencer":
                    continue
                points = drop_collinear(points)
                error = new_zone(story, name, number, chosen_category(), {"polygonCoordinates": points}, centroid(points))
                if error:
                    last = f"Zone impossible avec ce contour ({error})."
                    print(last)
                    continue
                created += 1
                number += 1
                last = f"{name} creee. " + apply("dessiner", dict(collect()))
                print(last)
        finally:
            root.deiconify()
        status.set((f"{created} piece(s) dessinee(s) et ajoutee(s) au tableau. " if created else "") + last)

    def by_selection():
        remember()
        busy("Lecture de la selection...")
        outlines = selected_outlines()
        if not outlines:
            status.set("Selectionne d'abord une ou plusieurs polylignes fermees ou remplissages qui delimitent tes pieces.")
            return
        story = tapir("GetStories").get("actStory", 0)
        number = next_zone_number(read_zones())
        created, errors = 0, []
        for pts, arcs, holes in outlines:
            name = f"{base_name.get()} {number}"
            if ask_name.get():
                answer = simpledialog.askstring("Nom de la piece", f"Nom de la piece {number} :", initialvalue=name, parent=root)
                if answer is None:
                    continue
                name = answer.strip() or name
            geometry = {"polygonCoordinates": pts}
            if arcs:
                geometry["polygonArcs"] = arcs
            if holes:
                geometry["holes"] = holes
            error = new_zone(story, name, number, chosen_category(), geometry, centroid(pts))
            if error:
                errors.append(error)
            else:
                created += 1
                number += 1
        message = f"{created} zone(s) creee(s) depuis la selection."
        if errors:
            message += f" Echec pour {len(errors)} : {errors[0]}"
        busy(message + " Mise a jour du tableau...")
        status.set(message + (" " + apply("dessiner", dict(collect())) if created else ""))

    def update_all():
        busy("Recalcul des zones...")
        try:
            tapir("UpdateZones", {"keepStampPosition": True})
        except Exception as error:  # noqa: BLE001
            status.set(f"Recalcul des zones impossible : {error}")
            return
        status.set("Zones recalculees. " + apply("dessiner", dict(collect())))

    zbtn = ttk.Frame(zbox)
    zbtn.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
    ttk.Button(zbtn, text="Cliquer dans les pieces (une par une)...", command=by_clicks).grid(row=0, column=0, padx=(0, 6))
    ttk.Button(zbtn, text="Dessiner une piece (contour)...", command=by_drawing).grid(row=0, column=1, padx=6)
    ttk.Button(zbtn, text="Zones depuis la selection", command=by_selection).grid(row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="we")
    ttk.Button(zbtn, text="Mettre a jour zones + tableau", command=update_all).grid(row=1, column=1, padx=6, pady=(6, 0), sticky="we")

    # ------------------------------------------------------------ 2. tableau
    frame = ttk.LabelFrame(outer, text=" 2. Tableau ", padding=10)
    frame.grid(row=1, column=0, sticky="we", pady=(10, 0))

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

    def collect() -> dict:
        for key, var in fields.items():
            state[key] = var.get()
        state["codes_couleur"] = bool(swatches.get())
        remember()
        save_json(REGLAGES, state)
        return state

    ttk.Label(outer, textvariable=status, foreground="#555", wraplength=520).grid(row=2, column=0, sticky="w", pady=(10, 0))

    def do(action):
        collect()
        busy("En cours...")
        try:
            status.set(apply(action, dict(state)))
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")

    def safe(fn):
        def wrapped():
            try:
                fn()
            except Exception as error:  # noqa: BLE001
                root.deiconify()
                status.set(f"Erreur : {error}")
        return wrapped

    buttons = ttk.Frame(outer)
    buttons.grid(row=3, column=0, pady=(12, 0), sticky="e")
    ttk.Button(buttons, text="Supprimer le tableau", command=lambda: do("supprimer")).grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    ttk.Button(buttons, text="Appliquer", command=lambda: do("dessiner")).grid(row=0, column=2, padx=4)
    for child, fn in zip(zbtn.winfo_children(), (by_clicks, by_drawing, by_selection, update_all)):
        child.configure(command=safe(fn))
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
