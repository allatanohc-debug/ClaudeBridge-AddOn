# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely>=2.0"]
# ///
"""
Cotation automatique - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\ (a cote de TableauSurfaces.py, qu'il utilise)
Puis dans Archicad : palette Tapir > "Cotation.py" > Executer.

Plan :
- Exterieur, facade par facade (trouvees toutes seules, meme en biais ou en L) :
  chaine 1 = ouvertures en tableau + angles, chaine 2 = decrochements + murs de refend + poteaux,
  chaine 3 = cote totale. Seulement les chaines necessaires (pas de doublon).
- Interieur : longueur x largeur de chaque piece (nu a nu), epaisseurs des murs, largeur des portes.
- Ouvertures : etiquette « L x H » (1,80 x 1,20) a cote de chaque porte / fenetre.
- Niveaux : repere de niveau dans chaque piece (±0,00, +0,15...), lu sur les dalles.
- Chaine traversante : 2 clics, tout ce que la ligne traverse est cote.

Les cotes sont associatives (elles suivent les murs) : l'extension « sonde » chaque mur et chaque
ouverture pour trouver leurs points d'accroche, puis relit chaque cote creee. Une cote qui ne tombe pas
juste est refaite sur des points fixes (repere invisible a l'impression) : la valeur est toujours juste.
Style : celui de ton outil Cote d'Archicad (stylo, police, marqueurs, arrondi).
"""

import argparse
import math
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load(name: str):
    """Importe un script voisin, meme renomme par Windows ("TableauSurfaces (4).py")."""
    try:
        return __import__(name)
    except ModuleNotFoundError:
        import importlib.util
        here = Path(__file__).resolve().parent
        copies = sorted(here.glob(f"{name}*.py"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not copies:
            raise SystemExit(f"{name}.py introuvable : mets-le dans le meme dossier ({here}).")
        spec = importlib.util.spec_from_file_location(name, copies[0])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module


T = _load("TableauSurfaces")
api, tapir, load_json, save_json = T.api, T.tapir, T.load_json, T.save_json

VERSION = "16/10 - v1"
MEMOIRE = "cotation.json"
REGLAGES = "cotation_reglages.json"
CALQUE = "COTATION"
TOL = 0.002                       # 2 mm : un point d'accroche doit tomber a moins de 2 mm
PAQUET = 150

DEFAUT = {
    "portee": "Étage actuel",
    "echelle": 100,
    "chaine_ouvertures": True, "chaine_decrochements": True, "chaine_totale": True,
    "si_necessaire": True, "refends": True, "poteaux": True,
    "distance_mm": 10.0, "ecart_mm": 7.0, "ignorer_cm": 5.0,
    "pieces": True, "epaisseurs": "Murs intérieurs", "portes_int": False,
    "etiquettes": "Toutes les ouvertures", "allege": False, "parallele": False, "texte_mm": 2.0,
    "niveaux": True, "associatives": True, "calque": "",
}
PORTEES = ("Étage actuel", "Tous les étages", "Murs sélectionnés")
EPAISSEURS = ("Aucune", "Murs intérieurs", "Murs extérieurs", "Tous les murs")
ETIQUETTES = ("Toutes les ouvertures", "Ouvertures extérieures", "Aucune")
ECHELLES = (20, 50, 100, 200, 500)

# points d'accroche a essayer sur chaque type d'element (le reste est appris : bouton « Apprendre »)
CANDIDATS = {
    "Wall": [{"inIndex": k} for k in range(1, 9)] + [{"inIndex": k, "special": 1} for k in range(1, 5)]
            + [{"line": True, "inIndex": k} for k in (11, 12, 13, 14, 21, 22, 23, 24)]
            + [{"nodeId": k} for k in range(1, 5)],
    "Door": [{"inIndex": k} for k in range(1, 25)],
    "Window": [{"inIndex": k} for k in range(1, 25)],
    "Column": [{"inIndex": k} for k in range(1, 13)],
    "Hotspot": [{"inIndex": k} for k in range(0, 3)],
}
CLES_ACCROCHE = ("line", "inIndex", "special", "nodeType", "nodeStatus", "nodeId")
PRIORITE = {"Door": 0, "Window": 0, "Wall": 1, "Column": 2, "Hotspot": 9}


# ----------------------------------------------------------------- outils ---
def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def xy(p) -> tuple:
    return (float(p["x"]), float(p["y"]))


def P(p) -> dict:
    return {"x": round(p[0], 6), "y": round(p[1], 6)}


def dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1]


def add(a, b, k=1.0) -> tuple:
    return (a[0] + b[0] * k, a[1] + b[1] * k)


def unit(v) -> tuple:
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-12 else (1.0, 0.0)


def fmt(v: float, nd: int = 2) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def fmt_level(z: float) -> str:
    return "±0,00" if abs(z) < 0.0005 else f"{z:+.2f}".replace(".", ",")


def mm(opts: dict, value_mm: float) -> float:
    """mm papier -> metres sur le plan."""
    return value_mm * float(opts["echelle"]) / 1000.0


def ids(guids) -> list[dict]:
    return [{"elementId": {"guid": g}} for g in guids]


def delete(guids: list[str]) -> None:
    for part in _chunks([g for g in guids if g], 400):
        try:
            tapir("DeleteElements", {"elements": ids(part)})
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- lecture ---
def stories() -> tuple[list[dict], int]:
    info = tapir("GetStories")
    return sorted(info.get("stories", []), key=lambda s: s.get("index", 0)), int(info.get("actStory", 0))


def layer_index(name: str) -> int:
    """Calque des cotes : celui choisi, sinon un calque « cot... / dimension », sinon COTATION (cree)."""
    rows = tapir("GetAttributesByType", {"attributeType": "Layer"}).get("attributes", [])
    by_name = {r.get("name", ""): r for r in rows}
    if name and name in by_name:
        return int(by_name[name]["index"])
    for r in rows:
        low = r.get("name", "").lower()
        if low.startswith("cot") or "dimension" in low:
            return int(r["index"])
    tapir("CreateLayers", {"layerDataArray": [{"name": CALQUE}]})
    rows = tapir("GetAttributesByType", {"attributeType": "Layer"}).get("attributes", [])
    found = next((r for r in rows if r.get("name") == CALQUE), None)
    return int(found["index"]) if found else 0


def layer_names() -> list[str]:
    try:
        return [r.get("name", "") for r in tapir("GetAttributesByType", {"attributeType": "Layer"}).get("attributes", [])]
    except Exception:  # noqa: BLE001
        return []


def read_type(kind: str, floor: int | None, fields: list[str], only: set | None = None) -> list[dict]:
    try:
        elements = tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
    except Exception:  # noqa: BLE001
        return []
    if only is not None:
        elements = [e for e in elements if e["elementId"]["guid"] in only]
    keep = []
    if floor is not None:
        for part in _chunks(elements, 500):
            heads = tapir("GetDetailsOfElements", {"elements": part, "fields": ["floorIndex"]}).get("detailsOfElements", [])
            keep += [e for e, h in zip(part, heads) if "error" not in h and
                     int(round(float(h.get("floorIndex", 0)))) == floor]
    else:
        keep = elements
    out = []
    for part in _chunks(keep, 200):
        rows = tapir("GetDetailsOfElements", {"elements": part, "fields": fields}).get("detailsOfElements", [])
        for e, r in zip(part, rows):
            if "error" not in r:
                r["guid"] = e["elementId"]["guid"]
                r["type"] = r.get("type", kind)
                out.append(r)
    return out


def selected_guids() -> set[str]:
    try:
        sel = api("API.GetSelectedElements").get("elements", [])
    except Exception:  # noqa: BLE001
        sel = tapir("GetSelectedElements").get("elements", [])
    return {e["elementId"]["guid"] for e in sel if isinstance(e, dict) and "elementId" in e}


def read_story(story: int, only_walls: set | None = None) -> dict:
    walls = read_type("Wall", story, ["type", "floorIndex", "details", "floorPlanPolygons"], only_walls)
    wall_ids = {w["guid"] for w in walls}
    openings = [o for kind in ("Window", "Door") for o in read_type(kind, story, ["type", "floorIndex", "details"])
                if (o.get("details", {}).get("ownerElementId") or {}).get("guid") in wall_ids]
    columns = read_type("Column", story, ["type", "floorIndex", "details", "floorPlanPolygons"]) if only_walls is None else []
    slabs = read_type("Slab", story, ["type", "floorIndex", "details"]) if only_walls is None else []
    zones = read_type("Zone", story, ["type", "floorIndex", "details"]) if only_walls is None else []
    return {"walls": walls, "openings": openings, "columns": columns, "slabs": slabs, "zones": zones}


# --------------------------------------------------------------- geometrie ---
class Wall:
    def __init__(self, row: dict):
        from shapely.geometry import Polygon
        d = row.get("details", {})
        self.guid, self.row = row["guid"], row
        self.b, self.e = xy(d.get("begCoordinate", {"x": 0, "y": 0})), xy(d.get("endCoordinate", {"x": 1, "y": 0}))
        self.straight = abs(float(d.get("arcAngle", 0.0) or 0.0)) < 1e-6 and d.get("geometryType", "Straight") == "Straight"
        self.u = unit((self.e[0] - self.b[0], self.e[1] - self.b[1]))
        self.n = (-self.u[1], self.u[0])
        self.length = math.dist(self.b, self.e)
        polys = []
        for p in row.get("floorPlanPolygons", []) or []:
            pts = [xy(c) for c in p.get("coordinates", [])]
            if len(pts) >= 3:
                poly = Polygon(pts)
                polys.append(poly if poly.is_valid else poly.buffer(0))
        if polys:
            s = [dot((x - self.b[0], y - self.b[1]), self.n) for poly in polys for x, y in poly.exterior.coords]
            self.s_min, self.s_max = min(s), max(s)
        else:
            t = float(d.get("begThickness", 0.2) or 0.2)
            self.s_min, self.s_max = -t / 2, t / 2
            polys = [self.rect(0.0, self.length, self.s_min, self.s_max)] if self.length > 1e-6 else []
        self.polys = [p for p in polys if not p.is_empty]
        self.facade = False

    def at(self, along: float, across: float) -> tuple:
        return (self.b[0] + self.u[0] * along + self.n[0] * across, self.b[1] + self.u[1] * along + self.n[1] * across)

    def rect(self, a0, a1, s0, s1):
        from shapely.geometry import Polygon
        return Polygon([self.at(a0, s0), self.at(a1, s0), self.at(a1, s1), self.at(a0, s1)])

    @property
    def thickness(self) -> float:
        return self.s_max - self.s_min


class Opening:
    def __init__(self, row: dict, wall: Wall):
        d = row.get("details", {})
        self.guid, self.kind, self.wall = row["guid"], row["type"], wall
        self.width = float(d.get("width", 0.0) or 0.0)
        self.height = float(d.get("height", 0.0) or 0.0)
        self.sill = float(d.get("sillHeight", 0.0) or 0.0)
        along = float(d.get("centerOffset", 0.0) or 0.0)
        # centre de l'ouverture : depuis le debut du mur ; si ca tombe dans le plein, depuis la fin
        mid = (wall.s_min + wall.s_max) / 2
        from shapely.geometry import Point
        self.along = along
        if wall.polys and any(p.buffer(-0.001).contains(Point(wall.at(along, mid))) for p in wall.polys):
            alt = wall.length - along
            if not any(p.buffer(-0.001).contains(Point(wall.at(alt, mid))) for p in wall.polys):
                self.along = alt
        self.c = wall.at(self.along, 0.0)
        self.exterior_side = 0            # +1 / -1 : cote du mur qui donne dehors (0 = ouverture interieure)

    def rect(self):
        return self.wall.rect(self.along - self.width / 2, self.along + self.width / 2, self.wall.s_min, self.wall.s_max)

    def edge_points(self, side: int) -> tuple:
        s = self.wall.s_max if side > 0 else self.wall.s_min
        return self.wall.at(self.along - self.width / 2, s), self.wall.at(self.along + self.width / 2, s)


def dominant_axes(walls: list[Wall]) -> tuple:
    weights = {}
    for w in walls:
        if w.straight and w.length > 0.3:
            a = math.degrees(math.atan2(w.u[1], w.u[0])) % 90.0
            key = round(a * 2) / 2 % 90.0
            weights[key] = weights.get(key, 0.0) + w.length
    a = math.radians(max(weights, key=weights.get) if weights else 0.0)
    return (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))


def reading_dir(t: tuple) -> tuple:
    """Sens de lecture d'une cote : vers la droite, ou vers le haut."""
    if t[0] < -1e-6 or (abs(t[0]) <= 1e-6 and t[1] < 0):
        return (-t[0], -t[1])
    return t


class Plan:
    """Tout ce qu'on va coter sur un etage."""

    def __init__(self, story: int, data: dict, opts: dict, level: float = 0.0):
        from shapely.geometry import LineString, Point, Polygon
        from shapely.ops import unary_union
        from shapely.geometry.polygon import orient

        self.story, self.opts, self.level = story, opts, level
        self.walls = [Wall(r) for r in data["walls"]]
        self.walls = [w for w in self.walls if w.polys]
        by_guid = {w.guid: w for w in self.walls}
        self.openings = []
        for r in data["openings"]:
            w = by_guid.get((r.get("details", {}).get("ownerElementId") or {}).get("guid", ""))
            if w and w.straight and float(r.get("details", {}).get("width", 0) or 0) > 0.05:
                self.openings.append(Opening(r, w))
        self.columns = []
        for r in data["columns"]:
            polys = [Polygon([xy(c) for c in p.get("coordinates", [])]) for p in r.get("floorPlanPolygons", []) or []
                     if len(p.get("coordinates", [])) >= 3]
            if polys:
                self.columns.append({"guid": r["guid"], "poly": unary_union([p.buffer(0) for p in polys])})
        self.u, self.v = dominant_axes(self.walls)
        self.warnings: list[str] = []
        self.dims: list[dict] = []          # {"kind", "points", "ref", "dir", "label"}
        self.thickness: list[dict] = []
        self.texts: list[dict] = []
        self.lines: list[list[tuple]] = []
        self.sides: list[dict] = []

        solids = [p for w in self.walls for p in w.polys] + [c["poly"] for c in self.columns] + \
                 [o.rect() for o in self.openings]
        if not solids:
            self.union = self.footprint = Polygon()
            self.rooms = []
            return
        u = unary_union([s.buffer(0) for s in solids])
        self.union = u.buffer(0.003, join_style=2).buffer(-0.003, join_style=2)
        comps = [g for g in getattr(self.union, "geoms", [self.union]) if g.geom_type == "Polygon"]
        filled = [Polygon(g.exterior) for g in comps if Polygon(g.exterior).area >= 1.0]
        self.footprint = unary_union(filled) if filled else Polygon()
        rest = self.footprint.difference(self.union) if filled else Polygon()
        self.rooms = [g for g in getattr(rest, "geoms", [rest]) if g.geom_type == "Polygon" and g.area >= 1.0]
        self.zones = data.get("zones", [])
        self.slabs = data.get("slabs", [])

        # --- facades : aretes exterieures visibles, regroupees par orientation
        edges = []
        for comp in getattr(self.footprint, "geoms", [self.footprint]):
            if comp.is_empty:
                continue
            ring = list(orient(comp, 1.0).exterior.coords)
            for a, b in zip(ring, ring[1:]):
                L = math.dist(a, b)
                if L < 0.005:
                    continue
                n = ((b[1] - a[1]) / L, -(b[0] - a[0]) / L)
                vis = False
                for k in (0.5, 0.15, 0.85):
                    m = (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
                    ray = LineString([add(m, n, 0.01), add(m, n, 500.0)])
                    if not ray.intersects(self.footprint):
                        vis = True
                        break
                if vis:
                    edges.append({"a": a, "b": b, "n": n, "len": L})
        groups: list[dict] = []
        for e in edges:
            g = next((g for g in groups if dot(g["n"], e["n"]) > math.cos(math.radians(1.5))), None)
            if g is None:
                groups.append({"n": e["n"], "edges": [e]})
            else:
                g["edges"].append(e)
        all_pts = [pt for comp in getattr(self.footprint, "geoms", [self.footprint]) if not comp.is_empty
                   for pt in comp.exterior.coords]
        for g in groups:
            total = sum(e["len"] for e in g["edges"])
            if total < 0.5:
                continue
            n = g["n"]
            t = reading_dir((-n[1], n[0]))
            self.sides.append({"n": n, "t": t, "edges": g["edges"], "extreme": max(dot(p, n) for p in all_pts),
                               "line": unary_union([LineString([e["a"], e["b"]]) for e in g["edges"]])})

        # murs de facade et cote exterieur des ouvertures
        for side in self.sides:
            for w in self.walls:
                if not w.straight or abs(dot(w.u, side["t"])) < 0.996:
                    continue
                sgn = 1 if dot(w.n, side["n"]) > 0 else -1
                face = w.s_max if sgn > 0 else w.s_min
                probe = LineString([w.at(0, face), w.at(w.length, face)])
                if probe.distance(side["line"]) < 0.02 and probe.intersection(side["line"].buffer(0.02)).length > 0.05:
                    w.facade = True
                    for o in self.openings:
                        if o.wall is w:
                            q = w.at(o.along, face)
                            if Point(q).distance(side["line"]) < 0.03:
                                o.exterior_side = sgn
        for col in self.columns:
            col["facade"] = any(col["poly"].distance(s["line"]) < 0.03 for s in self.sides)

        self._exterior()
        self._interior()
        self._labels()
        self._levels()
        self._checks()

    # ---------------------------------------------------------- exterieur
    def _exterior(self):
        from shapely.geometry import Point
        o = self.opts
        seuil = float(o["ignorer_cm"]) / 100.0
        all_pts = [pt for comp in getattr(self.footprint, "geoms", [self.footprint]) if not comp.is_empty
                   for pt in comp.exterior.coords]
        for side in self.sides:
            n, t = side["n"], side["t"]

            def pt(p, kind):
                return {"p": (p[0], p[1]), "s": dot(p, t), "kind": kind}

            corners = [pt(p, "angle") for e in side["edges"] if e["len"] >= seuil for p in (e["a"], e["b"])]
            if not corners:
                continue
            s_vals = [dot(p, t) for p in all_pts]
            lo = min(s_vals)
            hi = max(s_vals)

            def extreme(target):            # le sommet le plus proche de la facade a cette abscisse
                cand = [p for p in all_pts if abs(dot(p, t) - target) < 1e-6]
                return max(cand, key=lambda p: dot(p, n))

            total = [pt(extreme(lo), "total"), pt(extreme(hi), "total")]
            ouvertures = []
            for op in self.openings:
                if op.exterior_side and abs(dot(op.wall.u, t)) > 0.996 and \
                        dot(op.wall.n, n) * op.exterior_side > 0:
                    a, b = op.edge_points(op.exterior_side)
                    if Point(op.wall.at(op.along, op.wall.s_max if op.exterior_side > 0 else op.wall.s_min)
                             ).distance(side["line"]) < 0.03:
                        ouvertures += [pt(a, "ouverture"), pt(b, "ouverture")]
            refends = []
            if o["refends"]:
                facade_walls = [w for w in self.walls if w.facade and abs(dot(w.u, t)) > 0.996]
                for w in self.walls:
                    if w.facade or not w.straight or abs(dot(w.u, n)) < 0.996:
                        continue
                    if not any(pw.distance(fp) < 0.01 for fwall in facade_walls for pw in w.polys for fp in fwall.polys):
                        continue
                    verts = [p for poly in w.polys for p in poly.exterior.coords]
                    top = max(dot(p, n) for p in verts)
                    near = [p for p in verts if dot(p, n) > top - 0.01]
                    # le refend doit arriver sur une facade visible de ce cote
                    if min(Point(p).distance(side["line"]) for p in near) > max(0.6, max(fw.thickness for fw in facade_walls) + 0.05
                                                                              if facade_walls else 0.6):
                        continue
                    refends += [pt(min(near, key=lambda p: dot(p, t)), "refend"),
                                pt(max(near, key=lambda p: dot(p, t)), "refend")]
            poteaux = []
            if o["poteaux"]:
                for col in self.columns:
                    if col["poly"].distance(side["line"]) < 0.6:
                        verts = list(col["poly"].exterior.coords) if col["poly"].geom_type == "Polygon" else []
                        if verts:
                            poteaux += [pt(min(verts, key=lambda p: dot(p, t)), "poteau"),
                                        pt(max(verts, key=lambda p: dot(p, t)), "poteau")]

            c1 = dedupe(corners + ouvertures) if ouvertures else []
            c2 = dedupe(corners + refends + poteaux + total)
            c3 = dedupe(total)
            chains = []
            if o["chaine_ouvertures"] and len(c1) >= 2:
                chains.append(("ouvertures", c1))
            if o["chaine_decrochements"] and len(c2) >= 2:
                same3 = svals(c2) == svals(c3)
                same1 = bool(c1) and svals(c2) == svals(c1)
                if not o["si_necessaire"] or not (same3 or same1):
                    chains.append(("décrochements", c2))
            if o["chaine_totale"] and len(c3) == 2:
                if not o["si_necessaire"] or not chains or svals(chains[-1][1]) != svals(c3):
                    chains.append(("totale", c3))
            for k, (label, points) in enumerate(chains):
                off = side["extreme"] + mm(o, o["distance_mm"]) + k * mm(o, o["ecart_mm"])
                self.dims.append({"kind": "ext", "label": f"{facade_name(n)} : {label}", "points": [q["p"] for q in points],
                                  "ref": (n[0] * off, n[1] * off), "dir": t, "side": facade_name(n)})

    # ---------------------------------------------------------- interieur
    def _interior(self):
        from shapely.geometry import LineString, Point
        o = self.opts
        if o["pieces"]:
            stamps = [xy(z["details"]["stampPosition"]) for z in self.zones if "stampPosition" in z.get("details", {})]
            for room in self.rooms:
                if room.area < 1.5:
                    continue
                c = room.representative_point()
                p = (c.x, c.y)
                off = mm(o, 5.0)
                for d, other in ((self.u, self.v), (self.v, self.u)):
                    q = add(p, other, off)
                    if not room.contains(Point(q)) or any(math.dist(q, s) < off * 0.6 for s in stamps):
                        q = add(p, other, -off)
                        if not room.contains(Point(q)):
                            q = p
                    seg = LineString([add(q, d, -1000), add(q, d, 1000)]).intersection(room)
                    piece = next((g for g in getattr(seg, "geoms", [seg]) if g.geom_type == "LineString"
                                  and g.distance(Point(q)) < 1e-6), None)
                    if piece is None or piece.length < 0.5:
                        continue
                    a, b = piece.coords[0], piece.coords[-1]
                    self.dims.append({"kind": "piece", "label": "pièce", "points": [a, b], "ref": q,
                                      "dir": reading_dir(d)})
        if o["portes_int"]:
            for op in self.openings:
                if op.exterior_side == 0 and op.kind == "Door":
                    a, b = op.edge_points(1)
                    ref = op.wall.at(op.along, op.wall.s_max + mm(o, 3.0))
                    self.dims.append({"kind": "porte", "label": "porte", "points": [a, b], "ref": ref,
                                      "dir": reading_dir(op.wall.u)})
        which = o["epaisseurs"]
        if which != "Aucune":
            for w in self.walls:
                if not w.straight or w.length < 0.3:
                    continue
                if (which == "Murs intérieurs" and w.facade) or (which == "Murs extérieurs" and not w.facade):
                    continue
                big = max(w.polys, key=lambda p: p.area)
                rp = big.representative_point()
                self.thickness.append({"wall": w.guid, "ref": (rp.x, rp.y), "dir": w.n, "value": w.thickness})

    # ---------------------------------------------------------- etiquettes
    def _labels(self):
        from shapely.geometry import Point
        o = self.opts
        if o["etiquettes"] == "Aucune":
            return
        h = float(o["texte_mm"])
        for op in self.openings:
            if o["etiquettes"] == "Ouvertures extérieures" and not op.exterior_side:
                continue
            w = op.wall
            if op.exterior_side:
                inside = -op.exterior_side
            else:                                   # cloison : le cote de la plus grande piece
                def room_area(sgn):
                    q = Point(w.at(op.along, (w.s_max if sgn > 0 else w.s_min) + sgn * 0.3))
                    return max((r.area for r in self.rooms if r.contains(q)), default=0.0)
                inside = 1 if room_area(1) >= room_area(-1) else -1
            n_in = (w.n[0] * inside, w.n[1] * inside)
            face = w.s_max if inside > 0 else w.s_min
            base = add(w.at(op.along, face), n_in, mm(o, 1.2))
            text = f"{fmt(op.width)} x {fmt(op.height)}"
            if o["allege"] and op.kind == "Window" and op.sill > 0.001:
                text += f" (all. {fmt(op.sill)})"
            horizontal = abs(w.u[0]) >= abs(w.u[1])
            if horizontal or o["parallele"]:
                a = math.atan2(w.u[1], w.u[0])
                if a > math.pi / 2 + 1e-6:
                    a -= math.pi
                elif a <= -math.pi / 2 + 1e-6:
                    a += math.pi
                up = (-math.sin(a), math.cos(a))
                anchor = "MiddleBottom" if dot(n_in, up) > 0 else "MiddleTop"
            else:
                a = 0.0
                anchor = "LeftMiddle" if n_in[0] > 0 else "RightMiddle"
            self.texts.append({"coordinate": {"x": base[0], "y": base[1], "z": 0.0}, "text": text,
                               "floorIndex": self.story,
                               "style": {"height": h, "angle": a, "anchor": anchor,
                                         "justification": "Center" if anchor.startswith("Middle") else
                                         ("Left" if anchor.startswith("Left") else "Right")}})

    # ---------------------------------------------------------- niveaux
    def _levels(self):
        from shapely.geometry import Point, Polygon
        o = self.opts
        if not o["niveaux"]:
            return
        slabs = []
        for s in self.slabs:
            d = s.get("details", {})
            pts = [xy(c) for c in d.get("polygonOutline", [])]
            if len(pts) >= 3:
                top = float(d.get("zCoordinate", 0.0)) + float(d.get("offsetFromTop", 0.0) or 0.0)
                slabs.append((Polygon(pts).buffer(0), top))
        size = mm(o, 2.2)
        for room in self.rooms:
            if room.area < 1.5:
                continue
            c = room.representative_point()
            m = add(add((c.x, c.y), self.v, -mm(o, 7.0)), self.u, -mm(o, 9.0))
            if not room.contains(Point(m)):
                m = add((c.x, c.y), self.v, -mm(o, 7.0))
            if not room.contains(Point(m)):
                m = (c.x, c.y)
            tops = [top for poly, top in slabs if poly.contains(Point(m)) and top <= self.level + 1.0]
            z = max(tops) if tops else self.level
            tri = [add(m, self.u, -size / 2), add(m, self.u, size / 2), add(m, self.v, -size * 0.8)]
            tri = [(p[0] + self.v[0] * size * 0.8, p[1] + self.v[1] * size * 0.8) for p in tri]
            self.lines.append(tri + [tri[0]])
            a = math.atan2(self.u[1], self.u[0])
            t_pos = add(add(m, self.u, size * 0.8), self.v, size * 0.4)
            self.texts.append({"coordinate": {"x": t_pos[0], "y": t_pos[1], "z": 0.0}, "text": fmt_level(z),
                               "floorIndex": self.story,
                               "style": {"height": float(o["texte_mm"]), "angle": a, "anchor": "LeftMiddle",
                                         "justification": "Left"}})

    # ---------------------------------------------------------- controles
    def _checks(self):
        axes = [self.u, self.v]
        for w in self.walls:
            if not w.straight or w.length < 0.5:
                continue
            dev = min(math.degrees(math.acos(min(1.0, abs(dot(w.u, a))))) for a in axes)
            if 0.05 < dev < 3.0:
                c = w.at(w.length / 2, 0)
                self.warnings.append(f"Mur presque d'équerre ({dev:.2f}°) vers x={fmt(c[0])} y={fmt(c[1])}")
        odd = []
        for d in self.dims:
            s = sorted(dot(p, d["dir"]) for p in d["points"])
            for a, b in zip(s, s[1:]):
                v = (b - a) * 1000
                if abs(v - round(v / 5) * 5) > 0.6 and len(odd) < 12:
                    odd.append(fmt(v / 1000, 3))
        if odd:
            self.warnings.append("Cotes non rondes (au mm) : " + ", ".join(odd))

    def summary(self) -> list[str]:
        out = []
        kinds = {}
        for d in self.dims:
            kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
        names = {"ext": "chaînes extérieures", "piece": "cotes de pièces", "porte": "portes intérieures"}
        out.append(f"{T.story_label(self.story)} : " + ", ".join(f"{v} {names.get(k, k)}" for k, v in kinds.items())
                   + (f", {len(self.thickness)} épaisseurs" if self.thickness else "")
                   + f", {sum(1 for t in self.texts if ' x ' in t['text'])} étiquettes"
                   + (f", {len(self.lines)} niveaux" if self.lines else "") + ".")
        for side in sorted({d.get("side") for d in self.dims if d.get("side")}):
            ch = [d for d in self.dims if d.get("side") == side]
            total = max(dot(p, ch[0]["dir"]) for p in ch[-1]["points"]) - min(dot(p, ch[0]["dir"]) for p in ch[-1]["points"])
            out.append(f"   {side} : {len(ch)} chaîne(s), total {fmt(total)} m")
        return out + [f"⚠ {w}" for w in self.warnings]


def dedupe(points: list[dict]) -> list[dict]:
    out = []
    for q in sorted(points, key=lambda q: q["s"]):
        if out and abs(q["s"] - out[-1]["s"]) < 0.005:
            continue
        out.append(q)
    return out


def svals(points: list[dict]) -> list[float]:
    return [round(q["s"], 3) for q in points]


def facade_name(n: tuple) -> str:
    a = math.degrees(math.atan2(n[1], n[0])) % 360
    names = ["Façade est", "Façade nord-est", "Façade nord", "Façade nord-ouest", "Façade ouest", "Façade sud-ouest",
             "Façade sud", "Façade sud-est"]
    return names[int((a + 22.5) // 45) % 8]


# ------------------------------------------------------------ accroches ---
class Resolver:
    """Trouve, pour un point du plan, un point d'accroche associatif (mur, ouverture, poteau) qui y tombe."""

    def __init__(self, learned: dict):
        self.points: list[tuple] = []      # (x, y, kind, witness)
        self.lines: list[tuple] = []       # (x, y, ux, uy, kind, witness)
        self.learned = learned
        self.hotspot_template: dict | None = None
        self.ok = False

    def probe(self, elements: list[tuple]) -> None:
        """elements : (guid, type, point de reference, direction du mur ou None)."""
        items, meta = [], []
        for guid, kind, ref, udir in elements:
            cands = list(CANDIDATS.get(kind, [])) + [c for c in self.learned.get(kind, []) if c not in CANDIDATS.get(kind, [])]
            if not cands:
                continue
            items.append({"referencePoint": P(ref), "direction": {"x": 1.0, "y": 0.0},
                          "witnessPoints": [dict({"elementId": {"guid": guid}}, **c) for c in cands]})
            meta.append((guid, kind, ref, udir, cands))
        created, retry = [], []
        for part_i in range(0, len(items), PAQUET):
            part = items[part_i:part_i + PAQUET]
            try:
                res = tapir("CreateAssociativeDimensions", {"dimensionsData": part}).get("elements", [])
            except Exception:  # noqa: BLE001
                res = [{"error": "refus"}] * len(part)
            for k, r in enumerate(res):
                m = meta[part_i + k]
                if "elementId" in r:
                    created.append((r["elementId"]["guid"], m))
                else:
                    retry.append(m)
        # un candidat refuse fait echouer toute la cote : on reessaie point par point
        singles = []
        for guid, kind, ref, udir, cands in retry:
            for c in cands:
                w = dict({"elementId": {"guid": guid}}, **c)
                singles.append(({"referencePoint": P(ref), "direction": {"x": 1.0, "y": 0.0}, "witnessPoints": [w, w]},
                                (guid, kind, ref, udir, [c])))
        for part in _chunks(singles, PAQUET):
            try:
                res = tapir("CreateAssociativeDimensions", {"dimensionsData": [s[0] for s in part]}).get("elements", [])
            except Exception:  # noqa: BLE001
                res = []
            for r, s in zip(res, part):
                if "elementId" in r:
                    created.append((r["elementId"]["guid"], s[1]))
        if not created:
            return
        info = {m[0]: m for _g, m in created}
        try:
            data = []
            for part in _chunks([g for g, _m in created], PAQUET):
                data += tapir("GetDimensionData", {"elements": ids(part)}).get("dimensionsData", [])
        finally:
            delete([g for g, _m in created])
        for dim in data:
            for wp in dim.get("witnessPoints", []) or []:
                base = (wp.get("baseElementId") or {}).get("guid")
                if base not in info or "coordinate" not in wp:
                    continue
                guid, kind, ref, udir, _c = info[base]
                loc = xy(wp["coordinate"])
                if math.dist(loc, ref) > 60.0:             # point non calcule (0,0) : on l'ignore
                    continue
                witness = {"elementId": {"guid": guid}}
                for key in CLES_ACCROCHE:
                    if key in wp:
                        witness[key] = wp[key]
                if wp.get("line") and udir:
                    self.lines.append((loc[0], loc[1], udir[0], udir[1], kind, witness))
                else:
                    self.points.append((loc[0], loc[1], kind, witness))
                if kind == "Hotspot" and self.hotspot_template is None and math.dist(loc, ref) < TOL:
                    self.hotspot_template = {k: v for k, v in witness.items() if k != "elementId"}
        self.ok = bool(self.points or self.lines)

    def resolve(self, p: tuple, d: tuple) -> dict | None:
        best = None
        for x, y, kind, witness in self.points:
            if abs(x - p[0]) <= TOL and abs(y - p[1]) <= TOL:
                rank = PRIORITE.get(kind, 5)
                if best is None or rank < best[0]:
                    best = (rank, witness)
        if best:
            return best[1]
        for x, y, ux, uy, kind, witness in self.lines:
            if abs((p[0] - x) * uy - (p[1] - y) * ux) <= TOL and abs(ux * d[0] + uy * d[1]) < 0.9:
                return witness
        return None


# ------------------------------------------------------------- creation ---
class Builder:
    def __init__(self, story: int, layer: int, opts: dict, learned: dict, report=print):
        self.story, self.layer, self.opts, self.report = story, layer, opts, report
        self.resolver = Resolver(learned)
        self.created: list[str] = []
        self.assoc = self.fixed = self.failed = 0
        self.hot_guids: set[str] = set()

    def _hotspots(self, points: list[tuple]) -> list[dict]:
        data = [{"floorInd": self.story, "layerIndex": self.layer, "position": P(p)} for p in points]
        out = []
        for part in _chunks(data, PAQUET):
            res = tapir("CreateHotspots", {"hotspotsData": part}).get("elements", [])
            out += [r["elementId"]["guid"] if "elementId" in r else None for r in res]
        self.created += [g for g in out if g]
        self.hot_guids.update(g for g in out if g)
        if self.resolver.hotspot_template is None and out and out[0]:
            self.resolver.probe([(out[0], "Hotspot", points[0], None)])
            if self.resolver.hotspot_template is None:
                self.resolver.hotspot_template = {"inIndex": 1}
        return [dict({"elementId": {"guid": g}}, **self.resolver.hotspot_template) if g else None for g in out]

    def calibrate(self, plan: Plan) -> None:
        if not self.opts["associatives"]:
            return
        self.report("Recherche des points d'accroche (murs, ouvertures, poteaux)...")
        els = [(w.guid, "Wall", w.at(w.length / 2, 0), w.u) for w in plan.walls]
        els += [(o.guid, o.kind, o.c, o.wall.u) for o in plan.openings]
        els += [(c["guid"], "Column", (c["poly"].centroid.x, c["poly"].centroid.y), None) for c in plan.columns]
        self.resolver.probe(els)

    def dimensions(self, dims: list[dict]) -> None:
        """Cree les cotes, les relit, refait sur points fixes celles qui ne tombent pas juste."""
        if not dims:
            return
        pending = []
        fixed_needed = []
        for d in dims:
            ws = [self.resolver.resolve(p, d["dir"]) if self.opts["associatives"] else None for p in d["points"]]
            pending.append((d, ws))
            fixed_needed += [p for p, w in zip(d["points"], ws) if w is None]
        if fixed_needed:
            hs = iter(self._hotspots(fixed_needed))
            pending = [(d, [w if w is not None else next(hs) for w in ws]) for d, ws in pending]
        made = self._create(pending)
        redo = []
        for (d, ws), guid in zip(pending, made):
            if guid is None or not self._check(guid, d):
                if guid:
                    delete([guid])
                redo.append(d)
            else:
                self.created.append(guid)
                if any(w["elementId"]["guid"] in self.hot_guids for w in ws):
                    self.fixed += 1
                else:
                    self.assoc += 1
        if redo:
            hs = self._hotspots([p for d in redo for p in d["points"]])
            it = iter(hs)
            pending2 = [(d, [next(it) for _p in d["points"]]) for d in redo]
            made2 = self._create(pending2)
            for (d, _ws), guid in zip(pending2, made2):
                if guid:
                    self.created.append(guid)
                if guid and self._check(guid, d):
                    self.fixed += 1
                else:
                    self.failed += 1

    def _create(self, pending: list) -> list:
        items = []
        for d, ws in pending:
            items.append({"referencePoint": P(d["ref"]), "direction": P(d["dir"]), "floorIndex": self.story,
                          "witnessPoints": [w for w in ws if w is not None]})
        out = []
        for part in _chunks(items, PAQUET):
            try:
                res = tapir("CreateAssociativeDimensions", {"dimensionsData": part}).get("elements", [])
            except Exception as error:  # noqa: BLE001
                self.report(f"Cotes refusées : {error}")
                res = [{}] * len(part)
            out += [r["elementId"]["guid"] if "elementId" in r else None for r in res]
        self._data = {}
        for part in _chunks([g for g in out if g], PAQUET):
            try:
                rows = tapir("GetDimensionData", {"elements": ids(part)}).get("dimensionsData", [])
            except Exception:  # noqa: BLE001
                rows = []
            for g, r in zip(part, rows):
                self._data[g] = r
        return out

    def _check(self, guid: str, d: dict) -> bool:
        row = self._data.get(guid)
        if not row or "witnessPoints" not in row:
            return True                     # pas de relecture possible : on fait confiance
        want = sorted(dot(p, d["dir"]) for p in d["points"])
        got = sorted(dot(xy(w["coordinate"]), d["dir"]) for w in row["witnessPoints"] if "coordinate" in w)
        return len(want) == len(got) and all(abs(a - b) <= 0.003 for a, b in zip(want, got))

    def thickness(self, items: list[dict]) -> None:
        data = [{"wallId": {"guid": t["wall"]}, "referencePoint": P(t["ref"]), "direction": P(t["dir"])} for t in items]
        for part in _chunks(data, PAQUET):
            try:
                res = tapir("CreateWallThicknessDimensions", {"dimensionsData": part}).get("elements", [])
                self.created += [r["elementId"]["guid"] for r in res if "elementId" in r]
            except Exception as error:  # noqa: BLE001
                self.report(f"Épaisseurs refusées : {error}")

    def annotations(self, texts: list[dict], lines: list[list[tuple]]) -> None:
        for part in _chunks(texts, PAQUET):
            try:
                res = tapir("CreateTexts", {"textsData": part}).get("elements", [])
                self.created += [r["elementId"]["guid"] for r in res if "elementId" in r]
            except Exception as error:  # noqa: BLE001
                self.report(f"Textes refusés : {error}")
        data = [{"floorInd": self.story, "layerIndex": self.layer, "coordinates": [P(p) for p in l]} for l in lines]
        for part in _chunks(data, PAQUET):
            try:
                res = tapir("CreatePolylines", {"polylinesData": part}).get("elements", [])
                self.created += [r["elementId"]["guid"] for r in res if "elementId" in r]
            except Exception:  # noqa: BLE001
                pass

    def to_layer(self) -> None:
        if not self.layer:
            return
        for part in _chunks(self.created, 300):
            try:
                tapir("SetDetailsOfElements", {"elementsWithDetails": [
                    {"elementId": {"guid": g}, "details": {"layerIndex": self.layer}} for g in part]})
            except Exception:  # noqa: BLE001
                pass


# -------------------------------------------------------------- memoire ---
def load_settings() -> dict:
    saved = load_json(REGLAGES)
    opts = dict(DEFAUT)
    opts.update({k: v for k, v in (saved.get("options") or {}).items() if k in DEFAUT})
    return {"options": opts, "appris": saved.get("appris") or {}}


def save_settings(state: dict) -> None:
    save_json(REGLAGES, {"options": state["options"], "appris": state["appris"]})


def remember(story: int, kind: str, guids: list[str]) -> str:
    memory = load_json(MEMOIRE)
    cid = uuid.uuid4().hex[:8]
    memory.setdefault("cotations", []).append({"id": cid, "story": story, "kind": kind, "guids": guids,
                                               "date": time.strftime("%d/%m %H:%M")})
    memory["dernier"] = cid
    save_json(MEMOIRE, memory)
    return cid


def forget(predicate) -> int:
    memory = load_json(MEMOIRE)
    gone = [c for c in memory.get("cotations", []) if predicate(c)]
    guids = [g for c in gone for g in c["guids"]]
    delete(guids)
    memory["cotations"] = [c for c in memory.get("cotations", []) if not predicate(c)]
    if memory.get("dernier") in {c["id"] for c in gone}:
        memory["dernier"] = memory["cotations"][-1]["id"] if memory["cotations"] else None
    save_json(MEMOIRE, memory)
    return len(guids)


# --------------------------------------------------------------- actions ---
def build_plans(opts: dict, report=print) -> list[Plan]:
    all_stories, act = stories()
    levels = {int(s["index"]): float(s.get("level", 0.0)) for s in all_stories}
    portee = opts["portee"]
    only = None
    if portee == "Murs sélectionnés":
        only = selected_guids()
        if not only:
            raise RuntimeError("Sélectionne d'abord des murs dans Archicad.")
    targets = [int(s["index"]) for s in all_stories] if portee == "Tous les étages" else [act]
    plans = []
    for story in targets:
        report(f"Lecture de {T.story_label(story)}...")
        data = read_story(story, only)
        if not data["walls"]:
            continue
        plans.append(Plan(story, data, opts, levels.get(story, 0.0)))
    return plans


def apply_plans(plans: list[Plan], state: dict, report=print) -> list[str]:
    opts = state["options"]
    layer = layer_index(opts.get("calque", ""))
    out = []
    for plan in plans:
        if opts["portee"] != "Murs sélectionnés":    # « mettre a jour » : on remplace la cotation automatique
            forget(lambda c, s=plan.story: c["story"] == s and c["kind"] == "auto")
        b = Builder(plan.story, layer, opts, state["appris"], report)
        b.calibrate(plan)
        report(f"{T.story_label(plan.story)} : création des cotes...")
        b.dimensions(plan.dims)
        b.thickness(plan.thickness)
        b.annotations(plan.texts, plan.lines)
        b.to_layer()
        remember(plan.story, "auto" if opts["portee"] != "Murs sélectionnés" else "selection", b.created)
        out += plan.summary()
        out.append(f"   Accroche : {b.assoc} cote(s) associatives, {b.fixed} sur points fixes"
                   + (f", {b.failed} à vérifier" if b.failed else "") + ".")
    return out


def crossing_chain(a: tuple, b: tuple, state: dict, report=print) -> str:
    """Chaine traversante : tout ce que la ligne A-B traverse."""
    from shapely.geometry import LineString
    opts = state["options"]
    all_stories, act = stories()
    levels = {int(s["index"]): float(s.get("level", 0.0)) for s in all_stories}
    plan = Plan(act, read_story(act), dict(opts, pieces=False, portes_int=False, epaisseurs="Aucune",
                                           etiquettes="Aucune", niveaux=False), levels.get(act, 0.0))
    d = reading_dir(unit((b[0] - a[0], b[1] - a[1])))
    line = LineString([a, b])
    hits = line.intersection(plan.union.boundary)
    pts = [(g.x, g.y) for g in getattr(hits, "geoms", [hits]) if g.geom_type == "Point"]
    pts += [c for g in getattr(hits, "geoms", [hits]) if g.geom_type == "LineString" for c in (g.coords[0], g.coords[-1])]
    pts = sorted({(round(x, 5), round(y, 5)) for x, y in pts}, key=lambda p: dot(p, d))
    if len(pts) < 2:
        return "La ligne ne traverse aucun mur."
    layer = layer_index(opts.get("calque", ""))
    bld = Builder(act, layer, opts, state["appris"], report)
    bld.calibrate(plan)
    bld.dimensions([{"kind": "trav", "points": pts, "ref": a, "dir": d}])
    bld.to_layer()
    remember(act, "traversante", bld.created)
    s = [dot(p, d) for p in pts]
    return f"Chaîne traversante : {len(pts) - 1} cotes, total {fmt(s[-1] - s[0])} m."


def room_at(point: tuple, state: dict, report=print) -> str:
    from shapely.geometry import Point
    opts = state["options"]
    all_stories, act = stories()
    levels = {int(s["index"]): float(s.get("level", 0.0)) for s in all_stories}
    plan = Plan(act, read_story(act), dict(opts, chaine_ouvertures=False, chaine_decrochements=False,
                                           chaine_totale=False), levels.get(act, 0.0))
    room = next((r for r in plan.rooms if r.contains(Point(point))), None)
    if room is None:
        return "Pas de pièce fermée à cet endroit."
    plan.dims = [d for d in plan.dims if d["kind"] == "piece" and room.buffer(0.01).contains(Point(d["ref"]))]
    plan.texts = [t for t in plan.texts if room.buffer(0.6).contains(Point(t["coordinate"]["x"], t["coordinate"]["y"]))]
    plan.lines = [l for l in plan.lines if room.contains(Point(l[0]))]
    plan.thickness = []
    layer = layer_index(opts.get("calque", ""))
    bld = Builder(act, layer, opts, state["appris"], report)
    bld.calibrate(plan)
    bld.dimensions(plan.dims)
    bld.annotations(plan.texts, plan.lines)
    bld.to_layer()
    remember(act, "piece", bld.created)
    return f"Pièce cotée ({len(plan.dims)} cotes)."


def learn_from_selection(state: dict) -> str:
    """Lit une cote posee a la main : ses points d'accroche serviront aux prochaines cotations."""
    sel = list(selected_guids())
    if not sel:
        return "Sélectionne une cote que tu as posée à la main (sur une fenêtre, un angle...)."
    rows = tapir("GetDimensionData", {"elements": ids(sel)}).get("dimensionsData", [])
    learned, n = state["appris"], 0
    bases = {(w.get("baseElementId") or {}).get("guid") for r in rows if "error" not in r
             for w in r.get("witnessPoints", []) or []} - {None}
    types = {}
    if bases:
        heads = tapir("GetDetailsOfElements", {"elements": ids(sorted(bases)), "fields": ["type"]}).get("detailsOfElements", [])
        types = {g: h.get("type") for g, h in zip(sorted(bases), heads) if "error" not in h}
    for r in rows:
        for w in r.get("witnessPoints", []) or []:
            kind = types.get((w.get("baseElementId") or {}).get("guid"))
            if not kind:
                continue
            c = {k: w[k] for k in CLES_ACCROCHE if k in w and w[k] not in (None, False, 0)}
            if c and c not in learned.setdefault(kind, []) and c not in CANDIDATS.get(kind, []):
                learned[kind].append(c)
                n += 1
    save_settings(state)
    return f"{n} nouveau(x) point(s) d'accroche appris." if n else "Rien de nouveau : ces accroches sont déjà connues."


# ---------------------------------------------------------------- fenetre ---
def window(state: dict) -> None:
    import tkinter as tk
    from tkinter import ttk

    opts = state["options"]
    root = tk.Tk()
    root.title(f"Cotation automatique - version {VERSION}")
    root.attributes("-topmost", True)
    outer = ttk.Frame(root, padding=10)
    outer.grid()
    status = tk.StringVar(value="Règle la cotation, « Aperçu » pour voir, « Coter » pour poser les cotes.")
    v: dict = {}

    def var(key, kind=tk.StringVar):
        v[key] = kind(value=opts[key])
        return v[key]

    left = ttk.Frame(outer)
    left.grid(row=0, column=0, sticky="nw")

    box = ttk.LabelFrame(left, text="Portée", padding=6)
    box.grid(row=0, column=0, sticky="ew")
    for k, p in enumerate(PORTEES):
        ttk.Radiobutton(box, text=p, value=p, variable=v.get("portee") or var("portee")).grid(row=0, column=k, sticky="w")
    ttk.Label(box, text="Échelle 1:").grid(row=1, column=0, sticky="e", pady=(4, 0))
    ttk.Combobox(box, values=ECHELLES, textvariable=var("echelle", tk.IntVar), width=6, state="readonly"
                 ).grid(row=1, column=1, sticky="w", pady=(4, 0))

    ext = ttk.LabelFrame(left, text="Extérieur (façade par façade)", padding=6)
    ext.grid(row=1, column=0, sticky="ew", pady=(6, 0))
    ttk.Checkbutton(ext, text="Chaîne 1 : ouvertures en tableau + angles", variable=var("chaine_ouvertures", tk.BooleanVar)
                    ).grid(row=0, column=0, columnspan=4, sticky="w")
    ttk.Checkbutton(ext, text="Chaîne 2 : décrochements", variable=var("chaine_decrochements", tk.BooleanVar)
                    ).grid(row=1, column=0, sticky="w")
    ttk.Checkbutton(ext, text="+ murs de refend", variable=var("refends", tk.BooleanVar)).grid(row=1, column=1, sticky="w")
    ttk.Checkbutton(ext, text="+ poteaux", variable=var("poteaux", tk.BooleanVar)).grid(row=1, column=2, sticky="w")
    ttk.Checkbutton(ext, text="Chaîne 3 : cote totale", variable=var("chaine_totale", tk.BooleanVar)
                    ).grid(row=2, column=0, sticky="w")
    ttk.Checkbutton(ext, text="Seulement les chaînes nécessaires (pas de doublon)",
                    variable=var("si_necessaire", tk.BooleanVar)).grid(row=3, column=0, columnspan=4, sticky="w")
    row = ttk.Frame(ext)
    row.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
    for k, (key, label, w) in enumerate((("distance_mm", "1re chaîne à (mm)", 5), ("ecart_mm", "écart (mm)", 5),
                                         ("ignorer_cm", "ignorer retours < (cm)", 5))):
        ttk.Label(row, text=label).grid(row=0, column=2 * k, sticky="w", padx=(0 if k == 0 else 8, 2))
        ttk.Spinbox(row, from_=0, to=100, increment=1, textvariable=var(key, tk.DoubleVar), width=w
                    ).grid(row=0, column=2 * k + 1)

    inn = ttk.LabelFrame(left, text="Intérieur", padding=6)
    inn.grid(row=2, column=0, sticky="ew", pady=(6, 0))
    ttk.Checkbutton(inn, text="Pièces : longueur × largeur (nu à nu)", variable=var("pieces", tk.BooleanVar)
                    ).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(inn, text="Épaisseurs des murs").grid(row=1, column=0, sticky="w")
    ttk.Combobox(inn, values=EPAISSEURS, textvariable=var("epaisseurs"), state="readonly", width=18
                 ).grid(row=1, column=1, sticky="w", padx=6)
    ttk.Checkbutton(inn, text="Largeur des portes intérieures (cote)", variable=var("portes_int", tk.BooleanVar)
                    ).grid(row=2, column=0, columnspan=2, sticky="w")
    ttk.Checkbutton(inn, text="Repères de niveau dans les pièces (±0,00, +0,15...)",
                    variable=var("niveaux", tk.BooleanVar)).grid(row=3, column=0, columnspan=2, sticky="w")

    lab = ttk.LabelFrame(left, text="Ouvertures : étiquette « L x H »", padding=6)
    lab.grid(row=3, column=0, sticky="ew", pady=(6, 0))
    ttk.Combobox(lab, values=ETIQUETTES, textvariable=var("etiquettes"), state="readonly", width=22
                 ).grid(row=0, column=0, sticky="w")
    ttk.Label(lab, text="Texte (mm)").grid(row=0, column=1, sticky="e", padx=(8, 2))
    ttk.Spinbox(lab, from_=1.0, to=6.0, increment=0.25, textvariable=var("texte_mm", tk.DoubleVar), width=5
                ).grid(row=0, column=2, sticky="w")
    ttk.Checkbutton(lab, text="+ allège des fenêtres", variable=var("allege", tk.BooleanVar)
                    ).grid(row=1, column=0, sticky="w")
    ttk.Checkbutton(lab, text="Texte parallèle au mur (aussi murs verticaux)", variable=var("parallele", tk.BooleanVar)
                    ).grid(row=1, column=1, columnspan=2, sticky="w")

    adv = ttk.LabelFrame(left, text="Accroche et calque", padding=6)
    adv.grid(row=4, column=0, sticky="ew", pady=(6, 0))
    ttk.Checkbutton(adv, text="Cotes associatives (suivent les murs)", variable=var("associatives", tk.BooleanVar)
                    ).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(adv, text="Calque").grid(row=1, column=0, sticky="w")
    ttk.Combobox(adv, values=[""] + layer_names(), textvariable=var("calque"), width=26
                 ).grid(row=1, column=1, sticky="w", padx=6)
    ttk.Label(adv, text="(vide = un calque « Cotation » existant, sinon COTATION)", foreground="#777"
              ).grid(row=2, column=0, columnspan=2, sticky="w")

    right = ttk.Frame(outer)
    right.grid(row=0, column=1, sticky="n", padx=(10, 0))
    ttk.Label(right, text="Aperçu", foreground="#555").grid(row=0, column=0, sticky="w")
    canvas = tk.Canvas(right, width=520, height=470, background="white", highlightthickness=1,
                       highlightbackground="#ccc")
    canvas.grid(row=1, column=0)
    report = tk.Text(right, width=72, height=8, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=2, column=0, pady=(6, 0))
    ttk.Label(outer, textvariable=status, foreground="#555").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def show(lines):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", "\n".join(lines))
        report.configure(state="disabled")

    def collect():
        for key, var_ in v.items():
            try:
                opts[key] = var_.get()
            except Exception:  # noqa: BLE001
                pass
        save_settings(state)

    def say(text):
        status.set(text)
        root.update_idletasks()

    plans_cache: dict = {}

    def draw_preview(plan: Plan):
        canvas.delete("all")
        if not plan.footprint.is_empty:
            x0, y0, x1, y1 = plan.footprint.bounds
        else:
            return
        for d in plan.dims:
            for p in d["points"]:
                x0, y0, x1, y1 = min(x0, p[0]), min(y0, p[1]), max(x1, p[0]), max(y1, p[1])
            s = [dot(p, d["dir"]) for p in d["points"]]
            off = dot(d["ref"], (-d["dir"][1], d["dir"][0]))
            for q in (min(s), max(s)):
                pp = (d["dir"][0] * q - d["dir"][1] * off, d["dir"][1] * q + d["dir"][0] * off)
                x0, y0, x1, y1 = min(x0, pp[0]), min(y0, pp[1]), max(x1, pp[0]), max(y1, pp[1])
        W, H, m = 520, 470, 20
        k = min((W - 2 * m) / max(x1 - x0, 1e-6), (H - 2 * m) / max(y1 - y0, 1e-6))

        def sc(p):
            return (m + (p[0] - x0) * k, H - m - (p[1] - y0) * k)

        for g in getattr(plan.union, "geoms", [plan.union]):
            if g.geom_type == "Polygon":
                canvas.create_polygon([c for p in g.exterior.coords for c in sc(p)], fill="#bbb", outline="#555")
                for hole in g.interiors:
                    canvas.create_polygon([c for p in hole.coords for c in sc(p)], fill="white", outline="#555")
        colors = {"ext": "#c0392b", "piece": "#2471a3", "porte": "#7d3c98", "trav": "#117a65"}
        for d in plan.dims:
            t = d["dir"]
            nrm = (-t[1], t[0])
            off = dot(d["ref"], nrm)
            col = colors.get(d["kind"], "#333")
            s = sorted(dot(p, t) for p in d["points"])

            def on(q):
                return (t[0] * q + nrm[0] * off, t[1] * q + nrm[1] * off)

            canvas.create_line(*sc(on(s[0])), *sc(on(s[-1])), fill=col)
            for p in d["points"]:
                q = dot(p, t)
                canvas.create_line(*sc(p), *sc(on(q)), fill=col, dash=(2, 2))
                a, b = sc(on(q))
                canvas.create_line(a - 3, b + 3, a + 3, b - 3, fill=col, width=2)
            if k * (s[-1] - s[0]) > 40:
                for a_, b_ in zip(s, s[1:]):
                    if (b_ - a_) * k > 22:
                        mid = sc(on((a_ + b_) / 2))
                        canvas.create_text(mid[0], mid[1] - 6, text=fmt(b_ - a_), fill=col, font=("Segoe UI", 7))
        for t in plan.texts:
            p = sc((t["coordinate"]["x"], t["coordinate"]["y"]))
            canvas.create_text(*p, text=t["text"], fill="#555", font=("Segoe UI", 7),
                               angle=math.degrees(t["style"].get("angle", 0.0)))
        for th in plan.thickness:
            canvas.create_oval(*[c - 2 for c in sc(th["ref"])], *[c + 2 for c in sc(th["ref"])], outline="#d68910")

    def preview():
        collect()
        say("Lecture du plan...")
        try:
            plans = build_plans(dict(opts, portee="Étage actuel" if opts["portee"] == "Tous les étages" else opts["portee"]),
                                say)
            if not plans:
                show(["Aucun mur sur cet étage."])
                return
            plans_cache["p"] = plans[0]
            draw_preview(plans[0])
            show(plans[0].summary())
            say("Aperçu de l'étage actuel. « Coter » pour poser les cotes.")
        except Exception as error:  # noqa: BLE001
            say(f"Erreur : {error}")

    def run():
        collect()
        try:
            say("Lecture du plan...")
            plans = build_plans(opts, say)
            if not plans:
                show(["Aucun mur à coter."])
                return
            draw_preview(plans[0])
            lines = apply_plans(plans, state, say)
            show(lines)
            say("Terminé. « Annuler la dernière » pour revenir en arrière, « Coter » à nouveau pour mettre à jour.")
        except Exception as error:  # noqa: BLE001
            say(f"Erreur : {error}")

    def crossing():
        collect()
        root.withdraw()
        try:
            T.focus_archicad()
            a, why = T.pick_point("Chaîne traversante : clique le point de départ")
            if a is None:
                show([f"Annulé. {why}"])
                return
            b, why = T.pick_point("Chaîne traversante : clique le point d'arrivée")
            if b is None:
                show([f"Annulé. {why}"])
                return
            show([crossing_chain(xy(a), xy(b), state, say)])
        except Exception as error:  # noqa: BLE001
            show([f"Erreur : {error}"])
        finally:
            root.deiconify()

    def one_room():
        collect()
        root.withdraw()
        try:
            T.focus_archicad()
            p, why = T.pick_point("Clique dans la pièce à coter")
            show([room_at(xy(p), state, say)] if p else [f"Annulé. {why}"])
        except Exception as error:  # noqa: BLE001
            show([f"Erreur : {error}"])
        finally:
            root.deiconify()

    def undo():
        memory = load_json(MEMOIRE)
        last = memory.get("dernier")
        n = forget(lambda c: c["id"] == last) if last else 0
        show([f"Dernière cotation effacée ({n} éléments)." if n else "Rien à annuler."])

    def clear_story():
        act = tapir("GetStories").get("actStory", 0)
        n = forget(lambda c: c["story"] == act)
        show([f"Cotes de {T.story_label(act)} posées par l'extension effacées ({n} éléments)."])

    def learn():
        try:
            show([learn_from_selection(state)])
        except Exception as error:  # noqa: BLE001
            show([f"Erreur : {error}"])

    buttons = ttk.Frame(outer)
    buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
    for k, (text, cmd) in enumerate((("Apprendre (cote sélectionnée)", learn), ("Effacer l'étage", clear_story),
                                     ("Annuler la dernière", undo), ("Coter une pièce (clic)...", one_room),
                                     ("Chaîne traversante (2 clics)...", crossing), ("Aperçu", preview),
                                     ("Coter", run))):
        ttk.Button(buttons, text=text, command=cmd).grid(row=0, column=k, padx=3)
    root.protocol("WM_DELETE_WINDOW", lambda: (collect(), root.destroy()))
    root.bind("<Escape>", lambda _e: (collect(), root.destroy()))
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=T.PORT)
    args, _ = parser.parse_known_args()
    T.PORT = args.port
    window(load_settings())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
