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
Le script prend, pour chaque zone, le stylo du jeu de stylos actif dont la
couleur est la plus proche de la couleur de la zone, et ne reutilise jamais
deux fois le meme stylo (chaque zone a sa propre couleur).
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

PORT = 19723


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
            if "error" not in cat:
                colors[guid] = rgb(cat.get("color", {}))
    for zone in zones:
        zone["color"] = colors.get(zone["category"], (0.8, 0.8, 0.8))


def active_pens() -> list[tuple[int, tuple]]:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables]
    details = tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}).get("penTables", [])
    active = next((t for t in details if t.get("isActiveForModel")), details[0] if details else {})
    return [(p["index"], rgb(p["color"])) for p in active.get("pens", []) if p.get("index", 0) > 0]


def assign_pens(zones: list[dict], pens: list[tuple[int, tuple]]) -> None:
    used = set()
    for zone in zones:
        candidates = [(distance(zone["color"], c), i) for i, c in pens if i not in used] or [(0, 1)]
        _, best = min(candidates)
        zone["pen"] = best
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
    assign_pens(zones, active_pens())
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
                 "fillPenIndex": zone["pen"], "fillBackgroundPenIndex": zone["pen"], "contourPenIndex": zone["pen"]}
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
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
