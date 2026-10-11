# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Ombres decoratives en vue en plan - script Tapir (Archicad 28, mode normal)

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis dans Archicad : palette Tapir > choisir "Ombres.py" > Executer.

Ajoute sur l'etage courant une ombre grise unie, decalee vers le bas a droite,
sous les murs, les poteaux et les objets (mobilier, arbres...).
Relancer le script remplace les ombres precedentes de l'etage
(utile apres avoir modifie le plan).

Couleur : gris par defaut. Pour la changer, lancer "OmbresCouleur.py".
Les ombres utilisent un stylo reserve (215) du jeu de stylos actif :
changer sa couleur recolore aussitot toutes les ombres.
"""

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- reglages ---
ECHELLE = 100              # echelle du plan (100 = 1:100)
DECALAGE_MM = 2.0          # longueur de l'ombre (mm papier)
DIRECTION_DEG = -45.0      # direction de l'ombre : -45 = vers le bas a droite
COULEUR_DEFAUT = (0.70, 0.70, 0.70)   # gris (rouge, vert, bleu entre 0 et 1)
STYLO_OMBRE = 215          # stylo reserve aux ombres (le tableau des surfaces utilise 216-255)
OMBRE_MURS = True
OMBRE_POTEAUX = True
OMBRE_OBJETS = True
MOTS_ARBRES = ("tree", "arbre", "palm", "palmier", "bush", "buisson", "plant", "shrub", "vegetation", "baum")

PORT = 19723


# ------------------------------------------------------------ communication ---
def _post(payload: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
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


# ---------------------------------------------------------------- memoire ---
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
    (data_folder() / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def shadow_color() -> tuple[float, float, float]:
    color = load_json("ombres_reglages.json").get("couleur")
    if isinstance(color, list) and len(color) == 3:
        return tuple(float(c) for c in color)
    return COULEUR_DEFAUT


# ------------------------------------------------------------- geometrie ---
Point = tuple[float, float]


def clean(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for p in points:
        if not out or math.dist(p, out[-1]) > 1e-6:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-6:
        out.pop()
    return out


def signed_area(points: list[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2.0


def is_convex(points: list[Point]) -> bool:
    n = len(points)
    for i in range(n):
        a, b, c = points[i], points[(i + 1) % n], points[(i + 2) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < -1e-9:
            return False
    return True


def shadow_polygons(points: list[Point], d: Point) -> list[list[Point]]:
    """Partie de l'ombre qui depasse de l'element : la forme glissee le long de d, moins la forme."""
    pts = clean(points)
    if len(pts) < 3 or abs(signed_area(pts)) < 1e-9:
        return []
    if signed_area(pts) < 0:
        pts.reverse()                                   # sens anti-horaire
    n = len(pts)
    # arete "eclairee" vers d : sa normale exterieure (dy, -dx) pointe dans le sens de d
    front = [((pts[(i + 1) % n][1] - pts[i][1]) * d[0] - (pts[(i + 1) % n][0] - pts[i][0]) * d[1]) > 1e-12
             for i in range(n)]
    if not any(front):
        return []
    if is_convex(pts):
        start = next(i for i in range(n) if front[i] and not front[i - 1])
        chain = [pts[start]]
        i = start
        while front[i]:
            i = (i + 1) % n
            chain.append(pts[i])
        moved = [(x + d[0], y + d[1]) for x, y in reversed(chain)]
        return [chain + moved]
    # forme concave : un quadrilatere par arete exposee (l'element se dessine par-dessus)
    quads = []
    for i in range(n):
        if front[i]:
            a, b = pts[i], pts[(i + 1) % n]
            quads.append([a, b, (b[0] + d[0], b[1] + d[1]), (a[0] + d[0], a[1] + d[1])])
    return quads


def rectangle(origin: Point, width: float, depth: float, angle: float, mirrored: bool) -> list[Point]:
    sx = -1.0 if mirrored else 1.0
    c, s = math.cos(angle), math.sin(angle)
    corners = [(0.0, 0.0), (sx * width, 0.0), (sx * width, depth), (0.0, depth)]
    return [(origin[0] + x * c - y * s, origin[1] + x * s + y * c) for x, y in corners]


def circle(center: Point, radius: float, sides: int = 24) -> list[Point]:
    return [(center[0] + radius * math.cos(2 * math.pi * k / sides),
             center[1] + radius * math.sin(2 * math.pi * k / sides)) for k in range(sides)]


# ---------------------------------------------------------------- lecture ---
def current_story() -> int:
    return tapir("GetStories").get("actStory", 0)


def elements(kind: str) -> list[dict]:
    return tapir("GetElementsByType", {"elementType": kind}).get("elements", [])


def cut_polygons(kind: str, story: int) -> list[list[Point]]:
    elems = elements(kind)
    if not elems:
        return []
    details = tapir("GetDetailsOfElements", {"elements": elems, "fields": ["floorIndex", "floorPlanPolygons"]}
                    ).get("detailsOfElements", [])
    shapes = []
    for detail in details:
        if "error" in detail or detail.get("floorIndex") != story:
            continue
        for poly in detail.get("floorPlanPolygons", []):
            shapes.append([(c["x"], c["y"]) for c in poly.get("coordinates", [])])
    return shapes


def object_shapes(story: int) -> list[list[Point]]:
    elems = elements("Object")
    if not elems:
        return []
    details = tapir("GetDetailsOfElements", {"elements": elems, "fields": ["floorIndex", "details"]}
                    ).get("detailsOfElements", [])
    shapes = []
    for detail in details:
        if "error" in detail or detail.get("floorIndex") != story:
            continue
        d = detail.get("details", {})
        origin = d.get("origin", {})
        size = d.get("dimensions", {})
        width, depth = size.get("x", 0.0), size.get("y", 0.0)
        if width <= 1e-6 or depth <= 1e-6:
            continue
        name = d.get("libPart", {}).get("name", "").lower()
        o = (origin.get("x", 0.0), origin.get("y", 0.0))
        if any(word in name for word in MOTS_ARBRES):
            shapes.append(circle(o, max(width, depth) / 2.0))
        else:
            shapes.append(rectangle(o, width, depth, d.get("angle", 0.0), bool(d.get("reflected"))))
    return shapes


# ----------------------------------------------------------------- stylo ---
def set_shadow_pen(color: tuple[float, float, float]) -> bool:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables]
    details = [t for t in tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                ).get("penTables", []) if "error" not in t]
    table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
    if table is None:
        return False
    width = next((p.get("width", 0.1) for p in table.get("pens", []) if p.get("index") == STYLO_OMBRE), 0.1)
    answer = tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
        "attributeId": table["attributeId"], "name": table["name"],
        "pens": [{"index": STYLO_OMBRE, "width": width, "description": "Ombres (Claude Bridge)",
                  "color": {"red": color[0], "green": color[1], "blue": color[2]}}]}]})
    return "error" not in answer.get("attributeIds", [{}])[0]


def solid_fill_id() -> dict | None:
    fills = tapir("GetAttributesByType", {"attributeType": "Fill"}).get("attributes", [])
    ids = [{"attributeId": f["attributeId"]} for f in fills]
    for fill in tapir("GetFills", {"attributeIds": ids, "fields": ["subType"]}).get("fills", []):
        if fill.get("subType") == "Solid":
            return fill["attributeId"]
    return None


# ------------------------------------------------------------------ main ---
def main() -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args, _ = parser.parse_known_args()
    PORT = args.port

    story = current_story()
    shapes: list[list[Point]] = []
    if OMBRE_MURS:
        shapes += cut_polygons("Wall", story)
    if OMBRE_POTEAUX:
        shapes += cut_polygons("Column", story)
    if OMBRE_OBJETS:
        shapes += object_shapes(story)

    # Supprimer les ombres precedentes de cet etage
    project = tapir("GetProjectInfo")
    key = f"{project.get('projectPath') or project.get('projectName') or 'Sans titre'}|{story}"
    memory = load_json("ombres.json")
    if memory.get(key):
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in memory[key]]})
        except Exception:  # noqa: BLE001
            pass
        memory[key] = []
        save_json("ombres.json", memory)

    if not shapes:
        print("Aucun mur, poteau ou objet sur l'etage courant : pas d'ombre a dessiner.")
        return 0

    length = DECALAGE_MM * ECHELLE / 1000.0
    angle = math.radians(DIRECTION_DEG)
    d = (length * math.cos(angle), length * math.sin(angle))

    if not set_shadow_pen(shadow_color()):
        print(f"Attention : couleur non appliquee, le stylo {STYLO_OMBRE} garde sa couleur actuelle.")
    solid = solid_fill_id()

    hatches = []
    for shape in shapes:
        for poly in shadow_polygons(shape, d):
            hatch = {"floorInd": story, "coordinates": [{"x": x, "y": y} for x, y in poly],
                     "fillPenIndex": STYLO_OMBRE, "fillBackgroundPenIndex": STYLO_OMBRE,
                     "contourPenIndex": STYLO_OMBRE, "showArea": False}
            if solid:
                hatch["fillId"] = solid
            hatches.append(hatch)

    created = []
    for start in range(0, len(hatches), 500):
        created += ok_items(tapir("CreateHatches", {"hatchesData": hatches[start:start + 500]}).get("elements", []))

    if created:
        try:
            tapir("CreateGroups", {"elementGroups": [{"elements": created}]})
        except Exception:  # noqa: BLE001
            pass

    memory[key] = [e["elementId"]["guid"] for e in created if "elementId" in e]
    save_json("ombres.json", memory)
    print(f"Ombres creees : {len(created)} (murs, poteaux, objets de l'etage courant).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
