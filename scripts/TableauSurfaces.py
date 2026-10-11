# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Tableau des surfaces - script Tapir (Archicad 28, mode normal)

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis dans Archicad : barre Tapir > choisir "TableauSurfaces.py" > Executer.

Dessine a droite du plan de l'etage courant un tableau quadrille :
  case de couleur | numero + nom de la zone | surface
avec le total en bas. Relancer le script remplace le tableau precedent.

Couleur : Tapir ne permet pas encore de colorer un remplissage en RGB exact.
Le script reserve donc les derniers stylos du jeu de stylos actif (255, 254, ...)
et leur donne la couleur exacte de chaque zone. A eviter pour d'autres elements.
Si ce n'est pas possible, il prend le stylo le plus proche (couleur approchee).
"""

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- reglages ---
ECHELLE = 100            # echelle du plan (100 = 1:100) : sert a convertir les mm papier
TEXTE_MM = 2.5           # hauteur du texte courant (mm papier)
TITRE_MM = 3.5           # hauteur du titre (mm papier)
LIGNE_MM = 7.0           # hauteur d'une ligne du tableau (mm papier)
LARGEUR_MM = 95.0        # largeur du tableau (mm papier)
ECART_MM = 15.0          # espace entre le plan et le tableau (mm papier)
COL_CASE_MM = (2.0, 14.0)    # case de couleur : debut, fin
COL_NOM_MM = 17.0            # debut de la colonne Zone
COL_SEPARATION_MM = 63.0     # trait vertical entre Zone et Surface
COL_SURFACE_MM = 92.0        # bord droit de la colonne Surface

STYLOS_RESERVES = 40     # stylos 255, 254, ... recolores aux couleurs des zones (max de zones)
PORT = 19723
DIAGNOSTIC = False       # True : details en plus, journal ouvert dans le Bloc-notes a la fin


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


# ------------------------------------------------------------------ outils ---
def polygon_area(points: list[dict]) -> float:
    total = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        total += p["x"] * q["y"] - q["x"] * p["y"]
    return abs(total) / 2.0


def format_area(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + " m²"


def rgb(color: dict) -> tuple[float, float, float]:
    return (color.get("red", 0.8), color.get("green", 0.8), color.get("blue", 0.8))


def hexa(color) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02X}" for c in color)


def distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def ok_items(items: list[dict]) -> list[dict]:
    return [item for item in items if "error" not in item]


def memory_file() -> Path:
    folder = Path(os.environ.get("APPDATA", Path.home())) / "ClaudeBridge"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "tableaux_surfaces.json"


def load_memory() -> dict:
    try:
        return json.loads(memory_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_memory(data: dict) -> None:
    memory_file().write_text(json.dumps(data, indent=2), encoding="utf-8")


# -------------------------------------------------------------- lecture ---
def current_story() -> tuple[int, str]:
    info = tapir("GetStories")
    act = info.get("actStory", 0)
    name = next((s.get("name", "") for s in info.get("stories", []) if s.get("index") == act), "")
    return act, name


def zones_on_story(story: int) -> list[dict]:
    elements = tapir("GetElementsByType", {"elementType": "Zone"}).get("elements", [])
    if not elements:
        return []
    details = tapir("GetDetailsOfElements", {"elements": elements}).get("detailsOfElements", [])
    zones = []
    for element, detail in zip(elements, details):
        if "error" in detail or detail.get("floorIndex") != story:
            continue
        d = detail.get("details", {})
        zones.append({
            "element": element,
            "number": d.get("numberStr", ""),
            "name": d.get("name", ""),
            "category": d.get("categoryAttributeId", {}).get("guid"),
            "outline": d.get("polygonOutline", []),
            "holes": d.get("holes", []),
        })
    return zones


def fill_areas(zones: list[dict]) -> None:
    # 1) surface calculee par Archicad (propriete integree de la zone)
    try:
        names = api("API.GetAllPropertyNames").get("properties", [])
        builtin = [p["nonLocalizedName"] for p in names if p.get("type") == "BuiltIn" and "nonLocalizedName" in p]
        preferred = ["Zone_CalculatedArea", "Zone_MeasuredArea", "Zone_NetArea", "Zone_ZoneArea", "Zone_GrossArea"]
        choice = next((n for n in preferred if n in builtin), None)
        if choice is None:
            choice = next((n for n in builtin if n.startswith("Zone_") and "Area" in n), None)
        if choice:
            pid = api("API.GetPropertyIds", {"properties": [{"type": "BuiltIn", "nonLocalizedName": choice}]})["properties"][0]["propertyId"]
            values = api("API.GetPropertyValuesOfElements", {
                "elements": [z["element"] for z in zones],
                "properties": [{"propertyId": pid}],
            }).get("propertyValuesForElements", [])
            for zone, value in zip(zones, values):
                pv = value.get("propertyValues", [{}])[0].get("propertyValue", {})
                if pv.get("status", "normal") == "normal" and isinstance(pv.get("value"), (int, float)):
                    zone["area"] = float(pv["value"])
    except Exception as error:  # noqa: BLE001
        print(f"(Surface Archicad indisponible, calcul geometrique : {error})")
    # 2) a defaut : surface du contour moins les trous
    for zone in zones:
        if "area" not in zone:
            area = polygon_area(zone["outline"]) if len(zone["outline"]) >= 3 else 0.0
            for hole in zone["holes"]:
                pts = hole.get("polygonOutline", [])
                if len(pts) >= 3:
                    area -= polygon_area(pts)
            zone["area"] = max(area, 0.0)


def fill_colors(zones: list[dict]) -> None:
    guids = sorted({z["category"] for z in zones if z["category"]})
    colors = {}
    if guids:
        cats = tapir("GetZoneCategories", {"attributeIds": [{"attributeId": {"guid": g}} for g in guids], "fields": ["color"]})
        for guid, cat in zip(guids, cats.get("zoneCategories", [])):
            if "error" in cat:
                print(f"  ! categorie {guid} illisible : {cat['error']}")
            elif "color" in cat:
                colors[guid] = (rgb(cat["color"]), cat.get("name", ""))
            else:
                print(f"  ! categorie {cat.get('name', guid)} sans couleur")
    for zone in zones:
        zone["color"], zone["category_name"] = colors.get(zone["category"], ((0.8, 0.8, 0.8), "?"))


def make_colors_unique(zones: list[dict]) -> None:
    """Deux zones de meme couleur : la suivante est eclaircie ou assombrie (une couleur par zone)."""
    seen = []
    for zone in zones:
        base, color, step = zone["color"], zone["color"], 0
        while any(distance(color, other) < 0.05 for other in seen):
            step += 1
            amount = 0.15 * ((step + 1) // 2)
            if step % 2:   # eclaircir
                color = tuple(min(1.0, c + (1.0 - c) * amount) for c in base)
            else:          # assombrir
                color = tuple(max(0.0, c * (1.0 - amount)) for c in base)
            if step > 12:
                break
        zone["color"] = color
        seen.append(color)


def active_pen_table() -> dict:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables]
    details = tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}).get("penTables", [])
    details = [t for t in details if "error" not in t]
    active = next((t for t in details if t.get("isActiveForModel")), None)
    if active is None:
        print("  ! aucun jeu de stylos marque actif : premier jeu utilise")
        active = details[0] if details else {}
    print(f"Jeu de stylos utilise : {active.get('name', '?')}")
    return active


def reserve_pens(zones: list[dict], table: dict) -> bool:
    """Donne a chaque zone un stylo reserve (255, 254, ...) recolore a la couleur exacte de la zone."""
    if not table or len(zones) > STYLOS_RESERVES:
        return False
    widths = {p["index"]: p.get("width", 0.1) for p in table.get("pens", [])}
    pens = []
    for i, zone in enumerate(zones):
        index = 255 - i
        zone["pen"] = index
        red, green, blue = zone["color"]
        pens.append({"index": index, "color": {"red": red, "green": green, "blue": blue},
                     "width": widths.get(index, 0.1), "description": f"Tableau des surfaces {zone['number']}"[:120]})
    try:
        answer = tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
            "attributeId": table["attributeId"], "name": table["name"], "pens": pens}]})
        result = answer.get("attributeIds", [{}])[0]
        if "error" in result:
            raise RuntimeError(result["error"])
    except Exception as error:  # noqa: BLE001
        print(f"  ! stylos reserves impossibles ({error}) : couleurs approchees")
        return False
    for zone in zones:
        zone["pen_color"] = zone["color"]
    return True


def nearest_pens(zones: list[dict], table: dict) -> None:
    pens = [(p["index"], rgb(p["color"])) for p in table.get("pens", []) if p.get("index", 0) > 0]
    used = set()
    for zone in zones:
        candidates = [(distance(zone["color"], c), i, c) for i, c in pens if i not in used] or [(0, 1, (0, 0, 0))]
        _, best, color = min(candidates)
        zone["pen"], zone["pen_color"] = best, color
        used.add(best)


def solid_fill_id() -> dict | None:
    fills = tapir("GetAttributesByType", {"attributeType": "Fill"}).get("attributes", [])
    ids = [{"attributeId": f["attributeId"]} for f in fills]
    details = tapir("GetFills", {"attributeIds": ids, "fields": ["subType"]}).get("fills", [])
    for fill in details:
        if fill.get("subType") == "Solid":
            return fill["attributeId"]
    return None


def plan_bounds(zones: list[dict]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for zone in zones:
        for p in zone["outline"]:
            xs.append(p["x"])
            ys.append(p["y"])
    return min(xs), min(ys), max(xs), max(ys)


# ----------------------------------------------------------- diagnostic ---
class Journal:
    def __init__(self, stream):
        self.stream, self.lines = stream, []

    def write(self, text):
        self.lines.append(text)
        try:
            self.stream.write(text)
        except Exception:  # noqa: BLE001
            pass

    def flush(self):
        try:
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass


def dump_zone_color_properties(zones: list[dict]) -> None:
    try:
        names = api("API.GetAllPropertyNames").get("properties", [])
        keys = ("color", "colour", "pen", "fill", "categ")
        wanted = [p["nonLocalizedName"] for p in names if p.get("type") == "BuiltIn"
                  and any(k in p.get("nonLocalizedName", "").lower() for k in keys)
                  and (p["nonLocalizedName"].startswith("Zone_") or "Fill" in p["nonLocalizedName"] or "Pen" in p["nonLocalizedName"])]
        print(f"Proprietes candidates : {', '.join(wanted) or 'aucune'}")
        if not wanted:
            return
        ids = api("API.GetPropertyIds", {"properties": [{"type": "BuiltIn", "nonLocalizedName": n} for n in wanted]})["properties"]
        valid = [(n, i["propertyId"]) for n, i in zip(wanted, ids) if "propertyId" in i]
        values = api("API.GetPropertyValuesOfElements", {
            "elements": [z["element"] for z in zones],
            "properties": [{"propertyId": pid} for _, pid in valid],
        }).get("propertyValuesForElements", [])
        for zone, value in zip(zones, values):
            shown = []
            for (name, _), pv in zip(valid, value.get("propertyValues", [])):
                v = pv.get("propertyValue", {})
                if v.get("status", "normal") == "normal":
                    shown.append(f"{name}={v.get('value')}")
            print(f"  {zone['number']} : {'; '.join(shown) or '-'}")
    except Exception as error:  # noqa: BLE001
        print(f"  (proprietes illisibles : {error})")


def open_journal(journal: Journal) -> None:
    try:
        path = Path(os.environ.get("APPDATA", Path.home())) / "ClaudeBridge" / "TableauSurfaces_journal.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(journal.lines), encoding="utf-8")
        if DIAGNOSTIC and os.name == "nt":
            os.startfile(str(path))  # noqa: S606
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------- dessin ---
def main() -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args, _ = parser.parse_known_args()
    PORT = args.port

    story, story_name = current_story()
    zones = zones_on_story(story)
    if not zones:
        print("Aucune zone sur l'etage courant : rien a mettre dans le tableau.")
        return 0

    fill_areas(zones)
    fill_colors(zones)
    zones.sort(key=lambda z: (z["number"], z["name"]))
    if DIAGNOSTIC:
        dump_zone_color_properties(zones)
    make_colors_unique(zones)
    table = active_pen_table()
    if not reserve_pens(zones, table):
        nearest_pens(zones, table)
    for z in zones:
        print(f"  {z['number']} {z['name']} | categorie {z['category_name']} | couleur {hexa(z['color'])}"
              f" -> stylo {z['pen']} {hexa(z['pen_color'])}")
    solid = solid_fill_id()

    # Supprimer le tableau precedent de cet etage
    project = tapir("GetProjectInfo")
    key = f"{project.get('projectPath') or project.get('projectName') or 'Sans titre'}|{story}"
    memory = load_memory()
    old = memory.get(key, [])
    if old:
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in old]})
        except Exception:  # noqa: BLE001
            pass

    mm = ECHELLE / 1000.0
    _, _, x_max, y_max = plan_bounds(zones)
    x0 = x_max + ECART_MM * mm
    top = y_max - 9.0 * mm
    row = LIGNE_MM * mm
    width = LARGEUR_MM * mm
    n = len(zones)
    bottom = top - row * (n + 2)
    text_offset = (LIGNE_MM - TEXTE_MM) / 2.0 * mm
    total = sum(z["area"] for z in zones)

    texts, hatches, lines = [], [], []

    def text(content, x, y, right=False, bold=False, size=TEXTE_MM):
        texts.append({
            "coordinate": {"x": x, "y": y, "z": 0.0},
            "text": content,
            "floorIndex": story,
            "style": {"height": size, "bold": bold,
                      "anchor": "RightBottom" if right else "LeftBottom",
                      "justification": "Right" if right else "Left"},
        })

    def line(xa, ya, xb, yb):
        lines.append({"floorInd": story, "coordinates": [{"x": xa, "y": ya}, {"x": xb, "y": yb}]})

    title = "Tableau des surfaces" + (f" - {story_name}" if story_name else "")
    text(title, x0, top + 2.5 * mm, bold=True, size=TITRE_MM)

    header_bottom = top - row
    text("Zone", x0 + COL_NOM_MM * mm, header_bottom + text_offset, bold=True)
    text("Surface", x0 + COL_SURFACE_MM * mm, header_bottom + text_offset, right=True, bold=True)

    for i, zone in enumerate(zones):
        row_bottom = header_bottom - row * (i + 1)
        x1, x2 = x0 + COL_CASE_MM[0] * mm, x0 + COL_CASE_MM[1] * mm
        y1, y2 = row_bottom + 1.2 * mm, row_bottom + row - 1.2 * mm
        hatch = {"floorInd": story, "coordinates": [{"x": x1, "y": y1}, {"x": x2, "y": y1}, {"x": x2, "y": y2}, {"x": x1, "y": y2}],
                 "fillPenIndex": zone["pen"], "fillBackgroundPenIndex": zone["pen"], "contourPenIndex": zone["pen"], "showArea": False}
        if solid:
            hatch["fillId"] = solid
        hatches.append(hatch)
        label = f"{zone['number']} {zone['name']}".strip()
        text(label, x0 + COL_NOM_MM * mm, row_bottom + text_offset)
        text(format_area(zone["area"]), x0 + COL_SURFACE_MM * mm, row_bottom + text_offset, right=True)
        line(x0, row_bottom, x0 + width, row_bottom)          # trait sous chaque ligne

    text("Total", x0 + COL_NOM_MM * mm, bottom + text_offset, bold=True)
    text(format_area(total), x0 + COL_SURFACE_MM * mm, bottom + text_offset, right=True, bold=True)

    line(x0, header_bottom, x0 + width, header_bottom)       # sous l'en-tete
    line(x0 + COL_SEPARATION_MM * mm, top, x0 + COL_SEPARATION_MM * mm, bottom)  # trait vertical
    line(x0, top, x0 + width, top)                           # cadre
    line(x0 + width, top, x0 + width, bottom)
    line(x0 + width, bottom, x0, bottom)
    line(x0, bottom, x0, top)

    created = []
    created += ok_items(tapir("CreateHatches", {"hatchesData": hatches}).get("elements", []))
    created += ok_items(tapir("CreatePolylines", {"polylinesData": lines}).get("elements", []))
    created += ok_items(tapir("CreateTexts", {"textsData": texts}).get("elements", []))

    try:
        tapir("CreateGroups", {"elementGroups": [{"elements": created}]})
    except Exception:  # noqa: BLE001
        pass

    memory[key] = [e["elementId"]["guid"] for e in created if "elementId" in e]
    save_memory(memory)

    print(f"Tableau des surfaces cree : {n} zone(s), total {format_area(total)}.")
    return 0


if __name__ == "__main__":
    journal = Journal(sys.stdout)
    sys.stdout = journal
    code = 1
    try:
        code = main()
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
    finally:
        sys.stdout = journal.stream
        open_journal(journal)  # journal toujours ecrit dans %APPDATA%\\ClaudeBridge
    sys.exit(code)
