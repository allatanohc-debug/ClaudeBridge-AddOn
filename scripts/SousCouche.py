# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely>=2.0"]
# ///
"""
Sous-couche - comme la « sous-couche » (Underlay) de Revit - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\ (a cote de TableauSurfaces.py, qu'il utilise)
Puis dans Archicad : palette Tapir > "SousCouche.py" > Executer.

Sur chaque etage, on voit en gris ce qu'il y a a l'etage du dessous (ou celui qu'on choisit) :
murs, poteaux, dalles, escaliers, portes, mobilier, traits 2D (contour du terrain, garage...).
Contrairement a la Trace et Reference, ce sont de vrais traits 2D : ils s'impriment, partent
dans les PDF / DWG et sur les mises en page.

- Comme Revit, chaque etage a sa plage « De ... A ... » (par defaut : l'etage juste en dessous,
  aucune pour le plus bas). On peut aussi choisir un etage au-dessus (« vers le haut », en tirets).
- Tout est sur le calque « SOUS-COUCHE » (verrouille : on ne peut pas le selectionner par
  erreur, comme Revit). Masquer ce calque = masquer toutes les sous-couches.
- « Mise a jour automatique » (fenetre ouverte) : quand un etage change, les sous-couches qui le
  montrent sont redessinees toutes seules, comme Revit.
- Stylo 205 (dessous) et 204 (dessus) : gris demi-teinte, couleur et epaisseur reglables.
"""

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_tableau():
    """Importe TableauSurfaces.py, meme renomme par Windows ("TableauSurfaces (4).py")."""
    try:
        import TableauSurfaces
        return TableauSurfaces
    except ModuleNotFoundError:
        import importlib.util
        here = Path(__file__).resolve().parent
        copies = sorted(here.glob("TableauSurfaces*.py"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not copies:
            raise SystemExit(f"TableauSurfaces.py introuvable : mets-le dans le meme dossier ({here}).")
        spec = importlib.util.spec_from_file_location("TableauSurfaces", copies[0])
        module = importlib.util.module_from_spec(spec)
        sys.modules["TableauSurfaces"] = module
        spec.loader.exec_module(module)
        return module


T = _load_tableau()
api, tapir, load_json, save_json = T.api, T.tapir, T.load_json, T.save_json

VERSION = "16/10 - v1"
MEMOIRE = "sous_couche.json"
CALQUE = "SOUS-COUCHE"
STYLO_DESSOUS = 205
STYLO_DESSUS = 204
GRIS = (0.62, 0.62, 0.62)                 # demi-teinte (Revit : gris moyen)
EPAISSEUR_MM = 0.10
PLAN_DE_COUPE = 1.10                      # hauteur du plan de coupe au-dessus de l'etage (m)
AUCUNE = "Aucune"
INTERVALLE_AUTO_S = 8                     # mise a jour automatique : verification toutes les 8 s
PAQUET = 300                              # elements crees par appel

# (cle, libelle, types Archicad, coche par defaut) - comme les categories visibles de Revit
CATEGORIES = [
    ("murs", "Murs, poteaux, murs-rideaux", ["Wall", "Column", "CurtainWall"], True),
    ("dalles", "Dalles (contours)", ["Slab"], True),
    ("toits", "Toitures", ["Roof"], True),
    ("escaliers", "Escaliers", ["Stair"], True),
    ("ouvertures", "Portes et fenêtres", ["Door", "Window"], True),
    ("objets", "Mobilier, objets, équipements", ["Object", "Lamp"], True),
    ("morphes", "Morphes", ["Morph"], True),
    ("traits", "Traits 2D (contour, terrain, places de garage...)", ["Line", "PolyLine", "Arc", "Circle"], True),
    ("remplissages", "Contours des remplissages 2D", ["Hatch"], False),
    ("zones", "Zones (contours des pièces)", ["Zone"], False),
    ("terrain", "Terrain (maillages)", ["Mesh"], False),
]
COUPES = {"Wall", "Column", "CurtainWall", "Stair", "Morph"}   # dessines par leur coupe en plan


# ---------------------------------------------------------------- geometrie ---
def xy(p) -> tuple:
    return (float(p["x"]), float(p["y"]))


def arc_points(p, q, ang, steps=None) -> list[tuple]:
    """Points intermediaires d'un arc de p a q (angle signe, positif = sens trigo)."""
    chord = math.dist(p, q)
    if chord < 1e-9 or abs(ang) < 1e-6:
        return []
    ux, uy = (q[0] - p[0]) / chord, (q[1] - p[1]) / chord
    h = (chord / 2) / math.tan(ang / 2)
    c = ((p[0] + q[0]) / 2 - uy * h, (p[1] + q[1]) / 2 + ux * h)
    r = math.dist(c, p)
    a0 = math.atan2(p[1] - c[1], p[0] - c[0])
    steps = steps or max(4, int(abs(ang) / (math.pi / 16)))
    return [(c[0] + r * math.cos(a0 + ang * k / steps), c[1] + r * math.sin(a0 + ang * k / steps))
            for k in range(1, steps)]


def sample(points: list, arcs: list | None, closed: bool) -> list[tuple]:
    """Contour Archicad (points + arcs, index a partir de 0) -> liste de points."""
    pts = [xy(p) for p in points or []]
    if closed and len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts = pts[:-1]
    by_start = {}
    for a in arcs or []:
        by_start[int(a.get("begIndex", 0))] = float(a.get("arcAngle", 0.0))
    n = len(pts)
    out = []
    last = n if closed else n - 1
    for i in range(n):
        out.append(pts[i])
        if i < last and i in by_start:
            out.extend(arc_points(pts[i], pts[(i + 1) % n], by_start[i]))
    return out


def ellipse_points(origin, r, ratio, rot, a0, a1) -> list[tuple]:
    if a1 <= a0:
        a1 += 2 * math.pi
    steps = max(8, int((a1 - a0) / (math.pi / 24)))
    cr, sr = math.cos(rot), math.sin(rot)
    out = []
    for k in range(steps + 1):
        a = a0 + (a1 - a0) * k / steps
        x, y = r * math.cos(a), r * ratio * math.sin(a)
        out.append((origin[0] + x * cr - y * sr, origin[1] + x * sr + y * cr))
    return out


def rectangle(origin, A, B, angle, reflected=False) -> list[tuple]:
    xs = (-A, 0.0) if reflected else (0.0, A)
    ca, sa = math.cos(angle), math.sin(angle)
    corners = [(xs[0], 0.0), (xs[1], 0.0), (xs[1], B), (xs[0], B)]
    return [(origin[0] + x * ca - y * sa, origin[1] + x * sa + y * ca) for x, y in corners]


def opening_symbol(op: dict, wall: dict) -> list[list[tuple]]:
    """Porte : vantail + battement ; fenetre : deux traits du vitrage (murs droits seulement)."""
    d, w = op["details"], wall["details"]
    if abs(float(w.get("arcAngle", 0.0) or 0.0)) > 1e-6 or w.get("geometryType") not in (None, "Straight"):
        return []
    b, e = xy(w["begCoordinate"]), xy(w["endCoordinate"])
    length = math.dist(b, e)
    width = float(d.get("width", 0.0))
    if length < 1e-6 or width < 0.05:
        return []
    u = ((e[0] - b[0]) / length, (e[1] - b[1]) / length)
    n = (-u[1], u[0])
    verts = [xy(c) for poly in wall.get("floorPlanPolygons", []) for c in poly.get("coordinates", [])]
    if verts:
        s = [(v[0] - b[0]) * n[0] + (v[1] - b[1]) * n[1] for v in verts]
        s_min, s_max = min(s), max(s)
    else:
        t = float(w.get("begThickness", 0.2))
        s_min, s_max = -t / 2, t / 2
    t = float(d.get("centerOffset", 0.0))
    c = (b[0] + u[0] * t, b[1] + u[1] * t)

    def at(along, across):
        return (c[0] + u[0] * along + n[0] * across, c[1] + u[1] * along + n[1] * across)

    if op["type"] == "Window":
        mid, gap = (s_min + s_max) / 2, (s_max - s_min) / 6
        return [[at(-width / 2, mid - gap), at(width / 2, mid - gap)],
                [at(-width / 2, mid + gap), at(width / 2, mid + gap)]]
    side = 1.0 if d.get("oSide") else -1.0
    if w.get("flipped"):
        side = -side
    hinge = 1.0 if d.get("reflected") else -1.0
    face = s_max if side > 0 else s_min
    h = at(hinge * width / 2, face)
    tip = (h[0] + n[0] * side * width, h[1] + n[1] * side * width)
    jamb = at(-hinge * width / 2, face)
    a_tip = math.atan2(tip[1] - h[1], tip[0] - h[0])
    a_jamb = math.atan2(jamb[1] - h[1], jamb[0] - h[0])
    sweep = (a_jamb - a_tip + math.pi) % (2 * math.pi) - math.pi
    arc = [(h[0] + width * math.cos(a_tip + sweep * k / 12), h[1] + width * math.sin(a_tip + sweep * k / 12))
           for k in range(13)]
    return [[h, tip], arc]


# ---------------------------------------------------------------- Archicad ---
def stories() -> tuple[list[dict], int]:
    info = tapir("GetStories")
    rows = sorted(info.get("stories", []), key=lambda s: s.get("index", 0))
    return rows, int(info.get("actStory", 0))


def story_name(s: dict) -> str:
    name = str(s.get("name", "") or "").strip()
    label = T.story_label(int(s["index"]))
    return f"{label} - {name}" if name and name.lower() != label.lower() else label


def own_layer(lock: bool | None = None) -> int:
    """Index du calque SOUS-COUCHE (cree verrouille s'il manque). lock=True/False : (de)verrouille."""
    def find():
        rows = tapir("GetAttributesByType", {"attributeType": "Layer"}).get("attributes", [])
        return next((r for r in rows if r.get("name") == CALQUE), None)

    found = find()
    if found and lock is None:
        return int(found["index"])
    data = {"name": CALQUE, "isLocked": True if lock is None else bool(lock)}
    if found:
        try:
            info = tapir("GetLayers", {"attributeIds": [{"attributeId": found["attributeId"]}]}).get("layers", [{}])[0]
            info = info.get("layer", info)
            if bool(info.get("isLocked")) == bool(lock):
                return int(found["index"])
            data["isHidden"] = bool(info.get("isHidden", False))
            if "intersectionGroupNr" in info:
                data["intersectionGroupNr"] = int(info["intersectionGroupNr"])
        except Exception:  # noqa: BLE001
            pass
    tapir("CreateLayers", {"overwriteExisting": True, "layerDataArray": [data]})
    found = find()
    return int(found["index"]) if found else 0


def line_types() -> list[tuple[str, int]]:
    try:
        rows = tapir("GetAttributesByType", {"attributeType": "Line"}).get("attributes", [])
    except Exception:  # noqa: BLE001
        return []
    return [(r.get("name", ""), int(r["index"])) for r in rows if "index" in r]


def dashed_default(types: list[tuple[str, int]]) -> str:
    for word in ("tiret", "dash", "pointill", "cach", "hidden"):
        for name, _i in types:
            if word in name.lower():
                return name
    return types[1][0] if len(types) > 1 else (types[0][0] if types else "")


def set_pens(color: list[float], width: float) -> None:
    """Stylos 205 / 204 gris dans TOUS les jeux de stylos (impression avec n'importe quel jeu)."""
    try:
        tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
        rgb = dict(zip(("red", "green", "blue"), color))
        data = [{"attributeId": t["attributeId"], "name": t["name"], "pens": [
            {"index": STYLO_DESSOUS, "width": width, "description": "Sous-couche (étage inférieur)", "color": rgb},
            {"index": STYLO_DESSUS, "width": width, "description": "Sous-couche (étage supérieur)", "color": rgb}]}
            for t in tables if "attributeId" in t]
        if data:
            tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": data})
    except Exception as error:  # noqa: BLE001
        print(f"(stylos non regles : {error})")


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def read_elements(types: list[str], floors: set[int], layer: int, fields: list[str] | None) -> list[dict]:
    """Elements des etages voulus (hors calque SOUS-COUCHE), avec leurs details."""
    out = []
    for kind in types:
        try:
            ids = tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
        except Exception:  # noqa: BLE001
            continue
        keep = []
        for part in _chunks(ids, 500):
            heads = tapir("GetDetailsOfElements", {"elements": part, "fields": ["floorIndex", "layerIndex"]}
                          ).get("detailsOfElements", [])
            for el, h in zip(part, heads):
                if "error" in h or int(h.get("layerIndex", -1)) == layer:
                    continue
                if int(round(float(h.get("floorIndex", 0)))) in floors:
                    keep.append(el)
        for part in _chunks(keep, 200):
            params = {"elements": part}
            want = fields or (["type", "floorIndex", "details", "floorPlanPolygons"] if kind in COUPES
                              else ["type", "floorIndex", "details"])
            params["fields"] = want
            rows = tapir("GetDetailsOfElements", params).get("detailsOfElements", [])
            for el, row in zip(part, rows):
                if "error" not in row:
                    row["guid"] = el["elementId"]["guid"]
                    row["type"] = row.get("type", kind)
                    out.append(row)
    return out


def bounding_boxes(guids: list[str]) -> dict:
    boxes = {}
    for part in _chunks(guids, 300):
        try:
            rows = tapir("Get3DBoundingBoxes", {"elements": [{"elementId": {"guid": g}} for g in part]}
                         ).get("boundingBoxes3D", [])
        except Exception:  # noqa: BLE001
            return boxes
        for g, row in zip(part, rows):
            bb = row.get("boundingBox3D", row)
            if "xMin" in bb:
                boxes[g] = bb
    return boxes


def types_for(categories: dict) -> list[str]:
    out = []
    for key, _label, kinds, _default in CATEGORIES:
        if categories.get(key):
            out += kinds
    if categories.get("ouvertures") and "Wall" not in out:
        out.append("Wall")                      # les portes ont besoin de leur mur
    return out


def linework(elements: list[dict], categories: dict, target_level: float, levels: dict) -> list[list[tuple]]:
    """Elements lus -> traits 2D de la sous-couche (murs fusionnes, le reste coupe par les murs)."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import linemerge, unary_union

    walls = {e["guid"]: e for e in elements if e["type"] == "Wall"}
    cut_polys, lines = [], []
    wanted = set(types_for(categories))
    cut_z = target_level + PLAN_DE_COUPE
    boxed = [e["guid"] for e in elements if (e["type"] in ("Object", "Lamp") and "Object" in wanted) or
             (e["type"] in ("Stair", "Morph") and not e.get("floorPlanPolygons") and e["type"] in wanted)]
    boxes = bounding_boxes(boxed) if boxed else {}

    def add_ring(points, closed=True):
        if len(points) >= 2:
            lines.append(list(points) + ([points[0]] if closed and len(points) > 2 else []))

    for e in elements:
        kind, d = e["type"], e.get("details", {})
        if kind not in wanted:
            continue
        if kind == "Wall" and not categories.get("murs"):
            continue
        if kind in ("Wall", "Column"):                  # deja coupe sur l'etage actuel : Archicad le montre
            z, hgt = float(d.get("zCoordinate", -1e9)), float(d.get("height", 0.0))
            if z <= cut_z <= z + hgt:
                continue
        if kind in COUPES:
            polys = [[xy(c) for c in p.get("coordinates", [])] for p in e.get("floorPlanPolygons", [])]
            polys = [p for p in polys if len(p) >= 3]
            if polys:
                for p in polys:
                    poly = Polygon(p)
                    cut_polys.append(poly if poly.is_valid else poly.buffer(0))
                continue
        if kind in ("Slab", "Roof", "Zone", "Hatch", "Mesh"):
            outline = d.get("polygonOutline") or d.get("coordinates") or d.get("polygonCoordinates") or []
            arcs = d.get("polygonArcs") or d.get("arcs") or []
            add_ring(sample(outline, arcs, True))
            for hole in d.get("holes", []) or []:
                add_ring(sample(hole.get("polygonOutline") or hole.get("polygonCoordinates") or [],
                                hole.get("polygonArcs") or [], True))
        elif kind in ("Object", "Lamp"):
            bb = boxes.get(e["guid"])
            angle = float(d.get("angle", 0.0))
            aligned = abs(math.sin(2 * angle)) < 1e-3
            if bb and aligned:
                add_ring([(bb["xMin"], bb["yMin"]), (bb["xMax"], bb["yMin"]), (bb["xMax"], bb["yMax"]),
                          (bb["xMin"], bb["yMax"])])
            elif "origin" in d and "dimensions" in d:
                add_ring(rectangle(xy(d["origin"]), float(d["dimensions"]["x"]), float(d["dimensions"]["y"]),
                                   angle, bool(d.get("reflected"))))
        elif kind in ("Door", "Window"):
            wall = walls.get((d.get("ownerElementId") or {}).get("guid", ""))
            if wall:
                for seg in opening_symbol(e, wall):
                    add_ring(seg, closed=False)
        elif kind == "Line":
            add_ring([xy(d["begCoordinate"]), xy(d["endCoordinate"])], closed=False)
        elif kind == "PolyLine":
            add_ring(sample(d.get("coordinates", []), d.get("arcs", []), False), closed=False)
        elif kind in ("Arc", "Circle"):
            o, r = xy(d["origin"]), float(d.get("radius", 0.0))
            ratio = float(d.get("ratio", 1.0) or 1.0)
            a0, a1 = (float(d.get("begAngle", 0.0)), float(d.get("endAngle", 0.0))) if kind == "Arc" \
                else (0.0, 2 * math.pi)
            if r > 1e-6:
                add_ring(ellipse_points(o, r, ratio, float(d.get("angle", 0.0)), a0, a1), closed=False)
        elif kind in ("Stair", "Morph") and e["guid"] in boxes:   # non coupe : son encombrement
            bb = boxes[e["guid"]]
            add_ring([(bb["xMin"], bb["yMin"]), (bb["xMax"], bb["yMin"]), (bb["xMax"], bb["yMax"]),
                      (bb["xMin"], bb["yMax"])])

    out = []
    walls_union = unary_union([p.buffer(0.002, join_style=2) for p in cut_polys]).buffer(-0.002, join_style=2) \
        if cut_polys else None
    if walls_union is not None and not walls_union.is_empty:
        geoms = getattr(walls_union, "geoms", [walls_union])
        for g in geoms:
            if g.geom_type != "Polygon" or g.area < 1e-5:
                continue
            for ring in [g.exterior] + list(g.interiors):
                out.append(list(ring.simplify(0.0005).coords))
    if lines:
        shapes = [LineString(l) for l in lines if len(l) >= 2]
        geom = unary_union([g for g in shapes if g.length > 1e-4])
        if walls_union is not None and not walls_union.is_empty:
            geom = geom.difference(walls_union.buffer(-0.004, join_style=2))
        segs = []
        for g in getattr(geom, "geoms", [geom]):
            if g.geom_type == "LineString":
                segs.append(g)
            elif g.geom_type == "MultiLineString":
                segs += list(g.geoms)
        if segs:
            merged = linemerge(segs)
            for g in getattr(merged, "geoms", [merged]):
                if g.geom_type == "LineString" and g.length > 1e-3:
                    out.append(list(g.simplify(0.0005).coords))
    return out


# ------------------------------------------------------------------ memoire ---
def load_state() -> dict:
    saved = load_json(MEMOIRE)
    cats = {k: d for k, _l, _t, d in CATEGORIES}
    cats.update({k: bool(v) for k, v in (saved.get("categories") or {}).items() if k in cats})
    color = saved.get("couleur")
    return {
        "plages": saved.get("plages") or {},           # "etage" -> {"de": i | None, "a": j | None}
        "guids": saved.get("guids") or {},             # "etage" -> guids dessines
        "signatures": saved.get("signatures") or {},   # "etage source" -> empreinte
        "categories": cats,
        "couleur": [float(c) for c in color] if isinstance(color, list) and len(color) == 3 else list(GRIS),
        "epaisseur": float(saved.get("epaisseur", EPAISSEUR_MM)),
        "trait_dessous": saved.get("trait_dessous", ""),
        "trait_dessus": saved.get("trait_dessus", ""),
        "auto": bool(saved.get("auto", True)),
    }


def save_state(state: dict) -> None:
    save_json(MEMOIRE, state)


def default_range(index: int, all_indexes: list[int]) -> dict:
    lower = [i for i in all_indexes if i < index]
    return {"de": max(lower), "a": max(lower)} if lower else {"de": None, "a": None}


def range_of(state: dict, index: int, all_indexes: list[int]) -> dict:
    r = state["plages"].get(str(index))
    return r if isinstance(r, dict) and "de" in r else default_range(index, all_indexes)


def source_floors(r: dict) -> set[int]:
    if r.get("de") is None:
        return set()
    a = r.get("a") if r.get("a") is not None else r["de"]
    lo, hi = sorted((int(r["de"]), int(a)))
    return set(range(lo, hi + 1))


# ------------------------------------------------------------------ dessin ---
def erase(state: dict, index: int) -> int:
    guids = state["guids"].pop(str(index), [])
    for part in _chunks(guids, 500):
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in part]})
        except Exception:  # noqa: BLE001
            pass
    return len(guids)


def draw_story(state: dict, index: int, all_stories: list[dict], layer: int, types_by_name: dict) -> str:
    """Efface puis redessine la sous-couche d'un etage (le calque doit etre deverrouille)."""
    indexes = [int(s["index"]) for s in all_stories]
    levels = {int(s["index"]): float(s.get("level", 0.0)) for s in all_stories}
    erase(state, index)
    r = range_of(state, index, indexes)
    floors = source_floors(r)
    floors.discard(index)
    label = T.story_label(index)
    if not floors:
        return f"{label} : aucune sous-couche."
    elements = read_elements(types_for(state["categories"]), floors, layer, None)
    paths = linework(elements, state["categories"], levels.get(index, 0.0), levels)
    above = min(floors) > index
    pen = STYLO_DESSUS if above else STYLO_DESSOUS
    ltype = types_by_name.get(state["trait_dessus"] if above else state["trait_dessous"])
    data = []
    for p in paths:
        item = {"floorInd": index, "layerIndex": layer, "linePenIndex": pen, "penWeightMm": state["epaisseur"],
                "coordinates": [{"x": round(x, 5), "y": round(y, 5)} for x, y in p]}
        if ltype:
            item["lineTypeIndex"] = ltype
        data.append(item)
    guids = []
    for part in _chunks(data, PAQUET):
        answer = tapir("CreatePolylines", {"polylinesData": part}, timeout=300)
        guids += [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
    state["guids"][str(index)] = guids
    state["signatures"][str(index)] = {str(f): signature_of([e for e in elements if int(round(float(
        e.get("floorIndex", 0)))) == f]) for f in floors}
    names = ", ".join(T.story_label(f) for f in sorted(floors))
    return f"{label} : sous-couche de {names} ({len(elements)} éléments lus, {len(guids)} traits)."


def signature_of(rows: list[dict]) -> str:
    """Empreinte d'un etage source : change des qu'un element bouge, apparait ou disparait."""
    text = json.dumps(sorted((r["guid"], json.dumps(r.get("details", {}), sort_keys=True)) for r in rows))
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def update(state: dict, targets: list[int], report=print) -> list[str]:
    all_stories, _act = stories()
    set_pens(state["couleur"], state["epaisseur"])
    types_by_name = {n: i for n, i in line_types()}
    layer = own_layer(lock=False)
    out = []
    try:
        for index in targets:
            report(f"{T.story_label(index)}...")
            out.append(draw_story(state, index, all_stories, layer, types_by_name))
    finally:
        own_layer(lock=True)
        save_state(state)
    return out


def changed_targets(state: dict) -> list[int]:
    """Mise a jour automatique : etages dont la sous-couche montre un etage qui a change."""
    all_stories, _act = stories()
    indexes = [int(s["index"]) for s in all_stories]
    layer = own_layer()
    drawn = [i for i in indexes if str(i) in state["guids"]]
    floors = set()
    for i in drawn:
        floors |= source_floors(range_of(state, i, indexes)) - {i}
    if not floors:
        return []
    rows = read_elements(types_for(state["categories"]), floors, layer, ["type", "floorIndex", "details"])
    now = {str(f): signature_of([r for r in rows if int(round(float(r.get("floorIndex", 0)))) == f]) for f in floors}
    out = []
    for i in drawn:
        old = state["signatures"].get(str(i))
        wanted = {str(f) for f in source_floors(range_of(state, i, indexes)) - {i}}
        if not isinstance(old, dict) or set(old) != wanted or any(old[f] != now[f] for f in wanted):
            out.append(i)
    return out


# ------------------------------------------------------------------ fenetre ---
def window(state: dict) -> None:
    import tkinter as tk
    from tkinter import colorchooser, ttk

    all_stories, act = stories()
    indexes = [int(s["index"]) for s in all_stories]
    choices = [AUCUNE] + [story_name(s) for s in all_stories]
    by_label = {story_name(s): int(s["index"]) for s in all_stories}
    label_of = {v: k for k, v in by_label.items()}
    ltypes = line_types()
    names = [n for n, _i in ltypes]
    if state["trait_dessous"] not in names:
        state["trait_dessous"] = names[0] if names else ""
    if state["trait_dessus"] not in names:
        state["trait_dessus"] = dashed_default(ltypes)

    root = tk.Tk()
    root.title(f"Sous-couche (comme Revit) - version {VERSION}")
    root.attributes("-topmost", True)
    outer = ttk.Frame(root, padding=12)
    outer.grid()
    status = tk.StringVar(value="Règle la sous-couche de chaque étage, puis « Mettre à jour ».")

    # --- plages par etage (comme les proprietes de vue de Revit)
    box = ttk.LabelFrame(outer, text="Sous-couche de chaque étage (Revit : Plage - Niveau de base / supérieur)",
                         padding=8)
    box.grid(row=0, column=0, columnspan=2, sticky="ew")
    ttk.Label(box, text="Étage", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(box, text="Voir l'étage", font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w", padx=6)
    ttk.Label(box, text="jusqu'à", font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky="w", padx=6)
    ttk.Label(box, text="", width=10).grid(row=0, column=3)
    rows_frame = box
    holder = None
    if len(all_stories) > 10:                              # beaucoup d'etages : liste qui defile
        holder = tk.Canvas(box, height=300, highlightthickness=0)
        bar = ttk.Scrollbar(box, orient="vertical", command=holder.yview)
        rows_frame = ttk.Frame(holder)
        rows_frame.bind("<Configure>", lambda _e: holder.configure(scrollregion=holder.bbox("all")))
        holder.create_window((0, 0), window=rows_frame, anchor="nw")
        holder.configure(yscrollcommand=bar.set)
        holder.grid(row=1, column=0, columnspan=4, sticky="ew")
        bar.grid(row=1, column=4, sticky="ns")
    range_vars = {}
    for k, s in enumerate(reversed(all_stories)):
        i = int(s["index"])
        r = range_of(state, i, indexes)
        row = k + (0 if holder else 1)
        bold = ("Segoe UI", 9, "bold") if i == act else ("Segoe UI", 9)
        ttk.Label(rows_frame, text=story_name(s) + ("   ← actuel" if i == act else ""), font=bold
                  ).grid(row=row, column=0, sticky="w", pady=1)
        de = tk.StringVar(value=label_of.get(r.get("de"), AUCUNE))
        a = tk.StringVar(value=label_of.get(r.get("a"), "") if r.get("a") not in (None, r.get("de")) else "")
        ttk.Combobox(rows_frame, values=choices, textvariable=de, state="readonly", width=24
                     ).grid(row=row, column=1, padx=6, pady=1)
        ttk.Combobox(rows_frame, values=[""] + choices[1:], textvariable=a, state="readonly", width=24
                     ).grid(row=row, column=2, padx=6, pady=1)
        info = tk.StringVar(value=f"{len(state['guids'].get(str(i), []))} traits" if str(i) in state["guids"] else "")
        ttk.Label(rows_frame, textvariable=info, foreground="#777").grid(row=row, column=3, sticky="w")
        range_vars[i] = (de, a, info)
    quick = ttk.Frame(box)
    quick.grid(row=99, column=0, columnspan=4, sticky="w", pady=(6, 0))
    ttk.Button(quick, text="Tous : étage du dessous", command=lambda: preset("dessous")).grid(row=0, column=0)
    ttk.Button(quick, text="Tous : RDC", command=lambda: preset("rdc")).grid(row=0, column=1, padx=4)
    ttk.Button(quick, text="Tous : aucune", command=lambda: preset("aucune")).grid(row=0, column=2)
    ttk.Label(box, text="« jusqu'à » vide = un seul étage. Un étage au-dessus = vue « vers le haut » (débords de "
                        "toiture, balcons...), dessinée avec son propre type de trait.",
              foreground="#555", wraplength=640).grid(row=100, column=0, columnspan=4, sticky="w", pady=(4, 0))

    # --- categories
    cat_box = ttk.LabelFrame(outer, text="Ce qu'on voit (Revit : catégories visibles)", padding=8)
    cat_box.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
    cat_vars = {}
    for k, (key, label, _t, _d) in enumerate(CATEGORIES):
        v = tk.BooleanVar(value=state["categories"].get(key, False))
        ttk.Checkbutton(cat_box, text=label, variable=v).grid(row=k, column=0, sticky="w")
        cat_vars[key] = v

    # --- style (Revit : demi-teinte / sous-couche)
    st = ttk.LabelFrame(outer, text="Style (Revit : Demi-teinte / Sous-couche)", padding=8)
    st.grid(row=1, column=1, sticky="nsew", pady=(8, 0), padx=(8, 0))
    swatch = tk.Label(st, text="      ", relief="solid", borderwidth=1)
    swatch.grid(row=0, column=1, sticky="w", padx=6)

    def paint():
        swatch.configure(background="#%02x%02x%02x" % tuple(int(c * 255) for c in state["couleur"]))

    def pick():
        initial = "#%02x%02x%02x" % tuple(int(c * 255) for c in state["couleur"])
        rgb, _hex = colorchooser.askcolor(color=initial, parent=root, title="Couleur de la sous-couche")
        if rgb:
            state["couleur"] = [c / 255 for c in rgb]
            paint()

    paint()
    ttk.Label(st, text="Couleur").grid(row=0, column=0, sticky="w")
    ttk.Button(st, text="Changer...", command=pick).grid(row=0, column=2, sticky="w")
    ttk.Label(st, text="Épaisseur (mm)").grid(row=1, column=0, sticky="w", pady=(4, 0))
    width_var = tk.StringVar(value=f"{state['epaisseur']:.2f}")
    ttk.Combobox(st, values=["0.05", "0.10", "0.13", "0.18", "0.25"], textvariable=width_var, width=6
                 ).grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(4, 0))
    ttk.Label(st, text="Trait (étages dessous)").grid(row=2, column=0, sticky="w", pady=(4, 0))
    below_var = tk.StringVar(value=state["trait_dessous"])
    ttk.Combobox(st, values=names, textvariable=below_var, state="readonly", width=22
                 ).grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=(4, 0))
    ttk.Label(st, text="Trait (étages dessus)").grid(row=3, column=0, sticky="w", pady=(4, 0))
    above_var = tk.StringVar(value=state["trait_dessus"])
    ttk.Combobox(st, values=names, textvariable=above_var, state="readonly", width=22
                 ).grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=(4, 0))
    auto_var = tk.BooleanVar(value=state["auto"])
    ttk.Checkbutton(st, text="Mise à jour automatique (comme Revit)", variable=auto_var
                    ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
    ttk.Label(st, text="Tant que cette fenêtre est ouverte (réduite si tu veux),\nles sous-couches suivent tes "
                       "modifications.", foreground="#555").grid(row=5, column=0, columnspan=3, sticky="w")
    ttk.Label(st, text=f"Calque « {CALQUE} » (verrouillé)\nStylos {STYLO_DESSOUS} (dessous) et {STYLO_DESSUS} "
                       "(dessus)", foreground="#555").grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

    report = tk.Text(outer, width=92, height=6, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=2, column=0, columnspan=2, pady=(8, 0))
    ttk.Label(outer, textvariable=status, foreground="#555").grid(row=3, column=0, columnspan=2, sticky="w")

    def show(lines):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", "\n".join(lines))
        report.configure(state="disabled")

    def preset(kind):
        for i, (de, a, _info) in range_vars.items():
            lower = [j for j in indexes if j < i]
            if kind == "dessous":
                de.set(label_of[max(lower)] if lower else AUCUNE)
            elif kind == "rdc":
                de.set(label_of[0] if 0 in label_of and i > 0 else AUCUNE)
            else:
                de.set(AUCUNE)
            a.set("")

    def collect():
        for i, (de, a, _info) in range_vars.items():
            d = by_label.get(de.get())
            state["plages"][str(i)] = {"de": d, "a": by_label.get(a.get(), d) if d is not None else None}
        state["categories"] = {k: bool(v.get()) for k, v in cat_vars.items()}
        try:
            state["epaisseur"] = max(0.01, float(width_var.get().replace(",", ".")))
        except ValueError:
            pass
        state["trait_dessous"], state["trait_dessus"] = below_var.get(), above_var.get()
        state["auto"] = bool(auto_var.get())
        save_state(state)

    def refresh_counts():
        for i, (_de, _a, info) in range_vars.items():
            info.set(f"{len(state['guids'].get(str(i), []))} traits" if str(i) in state["guids"] else "")

    busy = {"on": False}

    def run(targets):
        collect()
        busy["on"] = True
        status.set("Mise à jour en cours...")
        root.update_idletasks()
        try:
            lines = update(state, targets, lambda t: (status.set(t), root.update_idletasks()))
            show(lines)
            status.set("Terminé. Pense à cocher le calque SOUS-COUCHE dans tes combinaisons de calques d'export.")
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")
        finally:
            busy["on"] = False
            refresh_counts()

    def clear_all():
        collect()
        own_layer(lock=False)
        n = sum(erase(state, i) for i in list(map(int, state["guids"])))
        own_layer(lock=True)
        state["signatures"] = {}
        save_state(state)
        refresh_counts()
        show([f"Sous-couches effacées ({n} traits)."])

    def tick():
        if auto_var.get() and not busy["on"] and state["guids"]:
            try:
                targets = changed_targets(state)
                if targets:
                    run(targets)
                    status.set(time.strftime("%H:%M:%S") + " : mise à jour automatique de " +
                               ", ".join(T.story_label(t) for t in targets) + ".")
            except Exception as error:  # noqa: BLE001
                status.set(f"(vérification automatique impossible : {error})")
        root.after(INTERVALLE_AUTO_S * 1000, tick)

    buttons = ttk.Frame(outer)
    buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
    ttk.Button(buttons, text="Tout effacer", command=clear_all).grid(row=0, column=0, padx=3)
    ttk.Button(buttons, text="Mettre à jour l'étage actuel",
               command=lambda: run([tapir("GetStories").get("actStory", act)])).grid(row=0, column=1, padx=3)
    ttk.Button(buttons, text="Mettre à jour tous les étages", command=lambda: run(indexes)
               ).grid(row=0, column=2, padx=3)
    root.protocol("WM_DELETE_WINDOW", lambda: (collect(), root.destroy()))
    root.after(INTERVALLE_AUTO_S * 1000, tick)
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=T.PORT)
    args, _ = parser.parse_known_args()
    T.PORT = args.port
    window(load_state())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
