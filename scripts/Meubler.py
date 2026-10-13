# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely>=2.0"]
# ///
"""
Meubler - amenagement meuble automatique d'une piece - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\ (a cote de TableauSurfaces.py, qu'il utilise)
Puis dans Archicad : palette Tapir > "Meubler.py" > Executer.

1. Choisis le type de piece (Chambre, Sejour, Cuisine, Salle de bain...) : la liste des meubles
   proposes s'adapte. Coche ceux que tu veux.
2. "Cliquer dans la piece..." : clic dans une piece fermee (comme la baguette magique), ou
   "Meubler les zones selectionnees" : le type est deduit du nom de chaque zone.
3. Le script lit le contour, les portes (on ne bloque pas leur ouverture) et les fenetres
   (pas d'armoire devant), puis place les meubles avec les passages necessaires.

Rendu : objets 3D de ta bibliotheque Archicad quand il en trouve un qui correspond (bouton
"Bibliotheque..." pour choisir), sinon un dessin 2D propre (remplissage blanc + traits fins).
Chaque meuble est un groupe que tu peux deplacer ou tourner. "Annuler le dernier" efface tout.
"""

import argparse
import json
import math
import sys
import unicodedata
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

MEMOIRE = "meubles.json"                 # guids dessines (exclus de la baguette magique)
REGLAGES = "meubler_reglages.json"
STYLO_TRAIT = 204                        # traits des meubles dessines
STYLO_FOND = 205                         # remplissage blanc des meubles dessines
COULEUR_TRAIT = (0.25, 0.25, 0.25)
RENDUS = ("Objets 3D de la bibliothèque (sinon dessin 2D)", "Dessin 2D seulement")

# ------------------------------------------------------------------ meubles ---
# w = largeur (le long du mur), d = profondeur, h = hauteur ; haut = meuble haut (pas devant une fenetre)
MEUBLES = {
    "lit_double":  dict(nom="Lit double", w=1.60, d=2.00, h=0.50, mots=["lit double", "double bed", "bed double"]),
    "lit_simple":  dict(nom="Lit simple", w=0.90, d=1.90, h=0.50, mots=["lit simple", "lit 1", "single bed", "bed single"]),
    "chevet":      dict(nom="Table de chevet", w=0.45, d=0.40, h=0.50, mots=["chevet", "night table", "bedside", "nightstand"]),
    "armoire":     dict(nom="Armoire", w=1.20, d=0.60, h=2.20, haut=True, mots=["armoire", "wardrobe", "penderie"]),
    "bureau":      dict(nom="Bureau", w=1.20, d=0.60, h=0.75, mots=["bureau", "desk", "table de travail"]),
    "chaise":      dict(nom="Chaise", w=0.45, d=0.45, h=0.90, mots=["chaise", "chair"], sauf=["longue", "arm", "lounge", "fauteuil"]),
    "canape":      dict(nom="Canapé", w=2.20, d=0.90, h=0.80, mots=["canape", "sofa", "divan"]),
    "table_basse": dict(nom="Table basse", w=1.10, d=0.60, h=0.40, mots=["table basse", "coffee table", "low table"]),
    "meuble_tv":   dict(nom="Meuble TV", w=1.60, d=0.45, h=0.50, mots=["meuble tv", "tv", "television", "media"]),
    "fauteuil":    dict(nom="Fauteuil", w=0.80, d=0.80, h=0.80, mots=["fauteuil", "armchair", "lounge chair"]),
    "table":       dict(nom="Table à manger + chaises", w=1.60, d=0.90, h=0.75,
                        mots=["table a manger", "table de salle a manger", "dining table", "table rectangulaire",
                              "table rect", "table "], sauf=["chevet", "basse", "night", "coffee", "bureau", "desk",
                                                             "lampe", "lamp", "tennis", "billard"]),
    "etagere":     dict(nom="Étagère / bibliothèque", w=1.00, d=0.35, h=2.00, haut=True,
                        mots=["etagere", "bibliotheque", "bookshelf", "shelf", "rayonnage"]),
    "cuisine":     dict(nom="Plan de travail (évier + plaques)", w=2.40, d=0.60, h=0.90, mots=[]),
    "frigo":       dict(nom="Réfrigérateur", w=0.60, d=0.65, h=1.85, haut=True,
                        mots=["refrigerateur", "frigo", "refrigerator", "fridge"]),
    "baignoire":   dict(nom="Baignoire", w=1.70, d=0.70, h=0.55, mots=["baignoire", "bathtub", "bath tub", "bath"]),
    "douche":      dict(nom="Douche", w=0.90, d=0.90, h=0.10, mots=["douche", "shower"]),
    "lavabo":      dict(nom="Lavabo", w=0.60, d=0.45, h=0.85, mots=["lavabo", "vasque", "washbasin", "wash basin"]),
    "lave_mains":  dict(nom="Lave-mains", w=0.45, d=0.30, h=0.85, mots=["lave-mains", "lave mains", "hand basin"]),
    "wc":          dict(nom="WC", w=0.40, d=0.65, h=0.80, mots=["cuvette", "wc ", "toilet", "w.c."]),
    "lave_linge":  dict(nom="Lave-linge", w=0.60, d=0.60, h=0.85, mots=["lave-linge", "lave linge", "washing machine", "washer"]),
    "seche_linge": dict(nom="Sèche-linge", w=0.60, d=0.60, h=0.85, mots=["seche-linge", "seche linge", "dryer"]),
    "console":     dict(nom="Console / meuble d'entrée", w=1.20, d=0.40, h=0.85, mots=["console", "sideboard", "commode", "buffet"]),
    "placard":     dict(nom="Placard", w=1.20, d=0.60, h=2.20, haut=True, mots=["placard", "cabinet", "armoire"]),
}

# type de piece -> [(meuble, coche par defaut)] dans l'ordre de placement
PIECES = {
    "Chambre":            [("lit_double", True), ("chevet", True), ("armoire", True), ("bureau", False)],
    "Chambre d'enfant":   [("lit_simple", True), ("chevet", True), ("armoire", True), ("bureau", True)],
    "Séjour":             [("canape", True), ("table_basse", True), ("meuble_tv", True), ("fauteuil", True),
                           ("table", True), ("console", False)],
    "Salon":              [("canape", True), ("table_basse", True), ("meuble_tv", True), ("fauteuil", True)],
    "Salle à manger":     [("table", True), ("console", True)],
    "Cuisine":            [("cuisine", True), ("frigo", True), ("table", False)],
    "Salle de bain":      [("baignoire", True), ("douche", False), ("lavabo", True), ("wc", False),
                           ("lave_linge", False)],
    "Salle d'eau":        [("douche", True), ("lavabo", True), ("wc", False)],
    "WC":                 [("wc", True), ("lave_mains", True)],
    "Bureau":             [("bureau", True), ("etagere", True), ("fauteuil", False)],
    "Entrée":             [("console", True), ("placard", False)],
    "Buanderie":          [("lave_linge", True), ("seche_linge", True), ("placard", True)],
    "Dressing":           [("placard", True), ("armoire", True)],
}
TAILLES_LIT = {"Lit 140 × 190": (1.40, 1.90), "Lit 160 × 200": (1.60, 2.00), "Lit 180 × 200": (1.80, 2.00)}

MOTS_PIECES = [  # (mots dans le nom de la zone, type) - le premier qui correspond gagne
    (("enfant", "bebe", "bébé", "kid"), "Chambre d'enfant"),
    (("chambre", "ch.", "suite", "bedroom", "parent"), "Chambre"),
    (("salle a manger", "salle à manger", "sam", "dining"), "Salle à manger"),
    (("sejour", "séjour", "living", "pièce de vie", "piece de vie"), "Séjour"),
    (("salon", "lounge"), "Salon"),
    (("cuisine", "kitchen", "kitchenette"), "Cuisine"),
    (("salle d'eau", "sde", "s.d.e", "douche", "shower"), "Salle d'eau"),
    (("bain", "sdb", "s.d.b", "bath"), "Salle de bain"),
    (("wc", "toilette", "w.c"), "WC"),
    (("bureau", "office", "travail"), "Bureau"),
    (("entree", "entrée", "hall", "sas"), "Entrée"),
    (("buanderie", "cellier", "laverie", "laundry"), "Buanderie"),
    (("dressing",), "Dressing"),
]


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")


def guess_room(name: str) -> str | None:
    low = " " + plain(name) + " "
    for words, kind in MOTS_PIECES:
        if any(plain(w) in low for w in words):
            return kind
    return None


# ------------------------------------------------------------------ reglages ---
def load_settings() -> dict:
    saved = load_json(REGLAGES)
    choices = {k: bool(v) for k, v in (saved.get("choix") or {}).items() if isinstance(v, bool)}
    biblio = {k: v for k, v in (saved.get("bibliotheque") or {}).items() if isinstance(v, str)}
    flips = {k: bool(v) for k, v in (saved.get("retourner") or {}).items()}
    return {
        "piece": saved.get("piece") if saved.get("piece") in PIECES else "Chambre",
        "choix": choices,                       # "Chambre/armoire": True
        "lit": saved.get("lit") if saved.get("lit") in TAILLES_LIT else "Lit 160 × 200",
        "rendu": saved.get("rendu") if saved.get("rendu") in RENDUS else RENDUS[0],
        "bibliotheque": biblio,                 # meuble -> nom de l'objet ("" = dessin 2D)
        "retourner": flips,                     # meuble -> objet tourne de 180 degres
    }


def chosen_items(settings: dict, room: str) -> list[str]:
    return [k for k, default in PIECES[room] if settings["choix"].get(f"{room}/{k}", default)]


# ---------------------------------------------------------------- geometrie ---
def rot(v, a):
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def add(a, b, k=1.0):
    return (a[0] + b[0] * k, a[1] + b[1] * k)


def rect(center, u, n, w, d):
    """Rectangle de largeur w (le long de u) et profondeur d (le long de n), centre donne."""
    from shapely.geometry import Polygon
    hu, hn = (u[0] * w / 2, u[1] * w / 2), (n[0] * d / 2, n[1] * d / 2)
    pts = [(center[0] - hu[0] - hn[0], center[1] - hu[1] - hn[1]), (center[0] + hu[0] - hn[0], center[1] + hu[1] - hn[1]),
           (center[0] + hu[0] + hn[0], center[1] + hu[1] + hn[1]), (center[0] - hu[0] + hn[0], center[1] - hu[1] + hn[1])]
    return Polygon(pts)


class Pose:
    """Meuble pose : centre, u = direction de sa largeur, n = direction de sa face avant (vers la piece)."""

    def __init__(self, key, center, u, n, w, d):
        self.key, self.c, self.u, self.n, self.w, self.d = key, center, u, n, w, d

    @property
    def body(self):
        return rect(self.c, self.u, self.n, self.w, self.d)

    def front(self, depth, width=None, shift=0.0):
        """Zone de passage devant le meuble."""
        width = width or self.w
        c = add(add(self.c, self.n, self.d / 2 + depth / 2), self.u, shift)
        return rect(c, self.u, self.n, width, depth)


class Room:
    def __init__(self, polygon, doors, windows):
        """polygon (shapely, coordonnees du plan). On travaille dans un repere tourne pour que les murs
        principaux soient horizontaux / verticaux."""
        from shapely import affinity
        self.world = polygon
        self.angle = self.main_angle(polygon)
        self.origin = (polygon.centroid.x, polygon.centroid.y)
        self.P = affinity.rotate(polygon, -self.angle, origin=self.origin, use_radians=True)
        self.inner = self.P.buffer(-0.002)
        loc = lambda g: affinity.rotate(g, -self.angle, origin=self.origin, use_radians=True)  # noqa: E731
        self.doors = [loc(g) for g in doors]
        self.windows = [loc(g) for g in windows]
        self.bodies, self.clears = [], []        # geometrie occupee / passages a garder libres
        self.poses: list[Pose] = []
        self.segments = self.walls()

    @staticmethod
    def main_angle(poly) -> float:
        best, score = 0.0, -1.0
        coords = list(poly.exterior.coords)
        edges = [(math.atan2(b[1] - a[1], b[0] - a[0]) % (math.pi / 2), math.dist(a, b)) for a, b in zip(coords, coords[1:])]
        for a, _l in edges:
            s = sum(l for b, l in edges if min(abs(a - b), math.pi / 2 - abs(a - b)) < math.radians(2))
            if s > score:
                best, score = a, s
        return best - math.pi / 2 if best > math.pi / 4 else best

    def walls(self):
        """Segments de mur droits (repere local) : (a, u, n vers l'interieur, longueur)."""
        from shapely.geometry import Point
        out = []
        rings = [self.P.exterior] + list(self.P.interiors)
        for ring in rings:
            coords = list(ring.coords)
            for a, b in zip(coords, coords[1:]):
                length = math.dist(a, b)
                if length < 0.35:
                    continue
                u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
                if min(abs(u[0]), abs(u[1])) > 0.035:          # mur en biais : ignore
                    continue
                n = (-u[1], u[0])
                mid = add(add(a, u, length / 2), n, 0.05)
                if not self.P.contains(Point(mid)):
                    n = (-n[0], -n[1])
                out.append((a, u, n, length))
        return out

    # -- tests
    def free(self, body, clears=()) -> bool:
        if not self.inner.contains(body):
            return False
        if any(body.intersects(g) for g in self.doors):
            return False
        if any(body.intersects(g) for g in self.bodies) or any(body.intersects(g) for g in self.clears):
            return False
        for c in clears:
            if not self.P.buffer(0.01).contains(c):
                return False
            if any(c.intersects(g) for g in self.bodies):
                return False
        return True

    def near_window(self, geom, gap=0.08) -> bool:
        return any(geom.buffer(gap).intersects(w) for w in self.windows)

    def door_distance(self, point) -> float:
        from shapely.geometry import Point
        if not self.doors:
            return 5.0
        return min(Point(point).distance(d) for d in self.doors)

    def commit(self, pose: Pose, clears=()):
        self.poses.append(pose)
        self.bodies.append(pose.body)
        self.clears.extend(clears)

    # -- recherche le long des murs
    def along_walls(self, w, d, gap=0.01, step=0.05):
        """Tous les emplacements possibles d'un meuble w x d, dos au mur."""
        for a, u, n, length in self.segments:
            if length + 1e-6 < w:
                continue
            ts = [i * step for i in range(int((length - w) / step) + 1)] + [length - w, (length - w) / 2]
            for t in ts:
                center = add(add(a, u, t + w / 2), n, gap + d / 2)
                yield center, u, n, t, length


# ----------------------------------------------------------------- placement ---
def place_wall_item(room: Room, key, w, d, clear, score_fn, tall=False):
    best, best_score = None, -1e9
    for center, u, n, t, length in room.along_walls(w, d):
        pose = Pose(key, center, u, n, w, d)
        body = pose.body
        front = pose.front(clear) if clear > 0 else None
        if not room.free(body, [front] if front is not None else []):
            continue
        if tall and room.near_window(body):
            continue
        s = score_fn(pose, t, length)
        if s > best_score:
            best, best_score = pose, s
    return best


def corner_bonus(t, w, length):
    return 1.0 if t < 0.02 or t > length - w - 0.02 else 0.0


def place_bed(room: Room, key, w, d, nightstands: int):
    ns = MEUBLES["chevet"]
    for count in ([nightstands, 1, 0] if nightstands else [0]):
        total = w + count * (ns["w"] + 0.03)
        best, best_score, best_parts = None, -1e9, None
        for center, u, n, t, length in room.along_walls(total, d):
            back_left = add(add(center, u, -total / 2), n, -d / 2)
            sides = ["L", "R"][:count] if count < 2 else ["L", "R"]
            offset = (ns["w"] + 0.03) if "L" in sides else 0.0
            bed_c = add(add(back_left, u, offset + w / 2), n, d / 2)
            bed = Pose(key, bed_c, u, n, w, d)
            parts = [bed]
            if "L" in sides:
                parts.append(Pose("chevet", add(add(back_left, u, ns["w"] / 2), n, ns["d"] / 2), u, n, ns["w"], ns["d"]))
            if "R" in sides:
                parts.append(Pose("chevet", add(add(back_left, u, offset + w + 0.03 + ns["w"] / 2), n, ns["d"] / 2),
                                  u, n, ns["w"], ns["d"]))
            clears = [bed.front(0.70, w + 0.6 if key == "lit_double" else w)]
            side = 0.55 if key == "lit_double" else 0.0
            if side:
                for sgn in (-1, 1):
                    c = add(add(bed_c, u, sgn * (w / 2 + side / 2)), n, (ns["d"] if count else 0) / 2)
                    clears.append(rect(c, u, n, side, d - (ns["d"] if count else 0)))
            if not all(room.free(p.body) for p in parts) or not room.free(parts[0].body, clears):
                continue
            from shapely.ops import unary_union
            headboard = unary_union([p.body for p in parts]).buffer(0.01)
            s = (-2.0 if room.near_window(headboard, 0.1) else 0.0) \
                - abs(t - (length - total) / 2) * 0.6 + min(room.door_distance(bed_c), 4.0) * 0.4 \
                + (0.3 if count == nightstands else 0.0)
            if s > best_score:
                best, best_score, best_parts = clears, s, parts
        if best_parts:
            for p in best_parts:
                room.commit(p)
            room.clears.extend(best)
            return best_parts
    return []


def place_sofa(room: Room, items):
    sofa, table = MEUBLES["canape"], MEUBLES["table_basse"]
    best, best_score = None, -1e9
    for center, u, n, t, length in room.along_walls(sofa["w"], sofa["d"]):
        pose = Pose("canape", center, u, n, sofa["w"], sofa["d"])
        parts = [pose]
        clears = [pose.front(1.20 if "table_basse" in items else 0.8)]
        if "table_basse" in items:
            parts.append(Pose("table_basse", add(center, n, sofa["d"] / 2 + 0.40 + table["d"] / 2), u, n,
                              table["w"], table["d"]))
        if not room.free(pose.body, clears) or not all(room.free(p.body) for p in parts[1:]):
            continue
        # face au sofa : un mur a 2,4 - 4,5 m pour la tele
        facing = [s for s in room.segments if s[2][0] * n[0] + s[2][1] * n[1] < -0.99]
        dist = min((abs((center[0] - a[0]) * nn[0] + (center[1] - a[1]) * nn[1]) for a, _u, nn, _l in facing),
                   default=0.0)
        s = sofa["w"] * 0 + (1.5 if 2.4 <= dist <= 5.0 else 0.0) - abs(t - (length - sofa["w"]) / 2) * 0.4 \
            + min(room.door_distance(center), 3.0) * 0.3 + (0.5 if room.near_window(pose.body, 0.15) is False else 0.0)
        if s > best_score:
            best, best_score = (parts, clears), s
    if not best:
        return []
    parts, clears = best
    for p in parts:
        room.commit(p)
    room.clears.extend(clears[1:] if len(parts) > 1 else clears)
    if len(parts) > 1:                                   # passage autour de la table basse
        room.clears.append(parts[1].front(0.45))
    return parts


def place_tv(room: Room, sofa: Pose | None):
    tv = MEUBLES["meuble_tv"]
    def score(pose, t, length):
        if sofa is None:
            return -abs(t - (length - tv["w"]) / 2)
        facing = pose.n[0] * sofa.n[0] + pose.n[1] * sofa.n[1] < -0.99
        lateral = abs((pose.c[0] - sofa.c[0]) * pose.u[0] + (pose.c[1] - sofa.c[1]) * pose.u[1])
        return (3.0 if facing else -5.0) - lateral
    pose = place_wall_item(room, "meuble_tv", tv["w"], tv["d"], 0.6, score)
    if pose and sofa is not None and pose.n[0] * sofa.n[0] + pose.n[1] * sofa.n[1] > -0.99:
        return None                                       # pas de mur en face du canape
    if pose:
        room.commit(pose, [pose.front(0.6)])
    return pose


def place_table(room: Room, prefer_away_from=None):
    """Table + chaises tout autour, au milieu de l'espace libre."""
    from shapely.geometry import Point
    t = MEUBLES["table"]
    x1, y1, x2, y2 = room.P.bounds
    horizontal = (x2 - x1) >= (y2 - y1)
    for w, d in ((t["w"], t["d"]), (1.40, 0.80), (1.20, 0.80), (0.90, 0.90)):
        u, n = ((1.0, 0.0), (0.0, 1.0)) if horizontal else ((0.0, 1.0), (-1.0, 0.0))
        target = room.P.centroid
        if prefer_away_from is not None:
            rest = room.P.difference(prefer_away_from.buffer(1.8))
            if not rest.is_empty:
                target = rest.centroid
        best, best_d = None, 1e9
        steps = 0.10
        x = x1
        while x <= x2:
            y = y1
            while y <= y2:
                body = rect((x, y), u, n, w, d)
                ring = rect((x, y), u, n, w + 1.40, d + 1.40)      # chaises + passage
                if room.inner.contains(body) and room.free(body, [ring]):
                    dist = Point(x, y).distance(target)
                    if dist < best_d:
                        best, best_d = (x, y), dist
                y += steps
            x += steps
        if best:
            pose = Pose("table", best, u, n, w, d)
            room.commit(pose, [rect(best, u, n, w + 1.40, d + 1.40)])
            chairs = []
            per_side = max(1, int(w / 0.60))
            for i in range(per_side):
                s = -w / 2 + w / per_side * (i + 0.5)
                for sgn in (-1, 1):                               # le long des grands cotes
                    c = add(add(best, u, s), n, sgn * (d / 2 + 0.25))
                    chairs.append(Pose("chaise", c, u, (n[0] * -sgn, n[1] * -sgn), 0.45, 0.45))
            if w >= 1.40:
                for sgn in (-1, 1):                               # en bout de table
                    c = add(best, u, sgn * (w / 2 + 0.25))
                    chairs.append(Pose("chaise", c, (n[0] * sgn, n[1] * sgn), (-u[0] * sgn, -u[1] * sgn), 0.45, 0.45))
            for ch in chairs:
                room.poses.append(ch)
            return [pose] + chairs
    return []


def place_kitchen(room: Room, items):
    """Plan de travail lineaire le long du plus long mur libre, frigo a un bout."""
    with_fridge = "frigo" in items
    for length_wanted in (3.60, 3.00, 2.40, 1.80, 1.20):
        best, best_score = None, -1e9
        fridge_w = MEUBLES["frigo"]["w"] + 0.02 if with_fridge else 0.0
        total = length_wanted + fridge_w
        for center, u, n, t, length in room.along_walls(total, 0.65):
            back_left = add(add(center, u, -total / 2), n, -0.65 / 2)
            for fridge_left in ((True, False) if with_fridge else (False,)):
                start = fridge_w if fridge_left else 0.0
                counter = Pose("cuisine", add(add(back_left, u, start + length_wanted / 2), n, 0.30), u, n,
                               length_wanted, 0.60)
                parts = [counter]
                if with_fridge:
                    fx = MEUBLES["frigo"]["w"] / 2 if fridge_left else length_wanted + 0.02 + MEUBLES["frigo"]["w"] / 2
                    fridge = Pose("frigo", add(add(back_left, u, fx), n, 0.325), u, n, MEUBLES["frigo"]["w"], 0.65)
                    if room.near_window(fridge.body):
                        continue
                    parts.append(fridge)
                clears = [counter.front(1.0)]
                if not all(room.free(p.body) for p in parts) or not room.free(counter.body, clears):
                    continue
                s = corner_bonus(t, total, length) - abs(length - total) * 0.05
                if s > best_score:
                    best, best_score = (parts, clears), s
        if best:
            for p in best[0]:
                room.commit(p)
            room.clears.extend(best[1])
            return best[0]
    return []


def furnish(room: Room, kind: str, items: list[str], settings: dict) -> tuple[list[Pose], list[str]]:
    placed, missing = [], []

    def got(poses, key):
        if poses:
            placed.extend(poses if isinstance(poses, list) else [poses])
        else:
            missing.append(MEUBLES[key]["nom"])

    sofa = None
    for key in items:
        spec = MEUBLES[key]
        if key in ("chevet", "table_basse", "chaise"):
            continue                                       # places avec le lit / le canape / le bureau
        if key in ("lit_double", "lit_simple"):
            w, d = TAILLES_LIT[settings["lit"]] if key == "lit_double" else (spec["w"], spec["d"])
            got(place_bed(room, key, w, d, (2 if key == "lit_double" else 1) if "chevet" in items else 0), key)
        elif key == "canape":
            parts = place_sofa(room, items)
            sofa = parts[0] if parts else None
            got(parts, key)
        elif key == "meuble_tv":
            got(place_tv(room, sofa), key)
        elif key == "table":
            got(place_table(room, sofa.body if sofa else None), key)
        elif key == "cuisine":
            got(place_kitchen(room, items), key)
        elif key == "frigo" and "cuisine" in items:
            continue
        elif key == "bureau":
            pose = place_wall_item(room, key, spec["w"], spec["d"], 0.9,
                                   lambda p, t, l: (2.0 if room.near_window(p.body, 0.3) else 0.0)
                                   + corner_bonus(t, p.w, l) * 0.5)
            if pose:
                room.commit(pose, [pose.front(0.9)])
                chair = Pose("chaise", add(pose.c, pose.n, pose.d / 2 + 0.20), pose.u, (-pose.n[0], -pose.n[1]), 0.45, 0.45)
                room.poses.append(chair)
                got([pose, chair], key)
            else:
                got(None, key)
        elif key in ("baignoire", "douche", "lave_linge", "seche_linge"):
            pose = place_wall_item(room, key, spec["w"], spec["d"], 0.6 if key != "baignoire" else 0.7,
                                   lambda p, t, l: corner_bonus(t, p.w, l) * 3 - abs(l - p.w) * 0.3)
            if pose:
                room.commit(pose, [pose.front(0.6)])
            got(pose, key)
        elif key == "wc":
            pose = place_wall_item(room, key, 0.80, spec["d"], 0.6,
                                   lambda p, t, l: min(room.door_distance(p.c), 3.0) - abs(t - (l - p.w) / 2) * 0.2)
            if pose:                                        # 0,80 = cuvette + 20 cm libres de chaque cote
                pose.w = spec["w"]
                room.commit(pose, [pose.front(0.6, 0.8)])
            got(pose, key)
        elif key == "fauteuil" and sofa is not None:
            # a cote du canape, tourne vers la table basse
            done = None
            for sgn in (1, -1):
                c = add(add(sofa.c, sofa.u, sgn * (sofa.w / 2 + 0.55)), sofa.n, 0.80)
                pose = Pose(key, c, (sofa.n[0] * sgn, sofa.n[1] * sgn), (-sofa.u[0] * sgn, -sofa.u[1] * sgn),
                            spec["w"], spec["d"])
                if room.free(pose.body):
                    room.commit(pose)
                    done = pose
                    break
            got(done, key)
        else:
            tall = bool(spec.get("haut"))
            clear = 0.7 if tall else 0.6
            score = (lambda p, t, l: corner_bonus(t, p.w, l) * 2 + min(room.door_distance(p.c), 3.0) * 0.2) if tall \
                else (lambda p, t, l: -abs(t - (l - p.w) / 2) * 0.3)
            if key == "console":
                score = lambda p, t, l: -room.door_distance(p.c)          # noqa: E731  pres de l'entree
            pose = place_wall_item(room, key, spec["w"], spec["d"], clear, score, tall)
            if pose:
                room.commit(pose, [pose.front(clear)])
            got(pose, key)
    return placed, missing


# -------------------------------------------------------------- dessin 2D ---
def circle(cx, cy, r, n=28):
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def box_pts(x1, y1, x2, y2):
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def symbol(key: str, w: float, d: float):
    """Dessin du meuble en coordonnees locales : centre (0,0), x = largeur, dos du meuble vers +y.
    Renvoie (contours remplis de blanc, traits)."""
    hw, hd = w / 2, d / 2
    outline = box_pts(-hw, -hd, hw, hd)
    fills, lines = [outline], [outline + [outline[0]]]

    def poly(p, closed=True):
        lines.append(p + [p[0]] if closed else p)

    if key in ("lit_double", "lit_simple"):
        n_p = 2 if w >= 1.2 else 1
        pw = (w - 0.16 - 0.06 * (n_p - 1)) / n_p
        for i in range(n_p):
            x = -hw + 0.08 + i * (pw + 0.06)
            pil = box_pts(x, hd - 0.08 - 0.30, x + pw, hd - 0.08)
            fills.append(pil)
            poly(pil)
        poly([(-hw, hd - 0.48), (hw, hd - 0.48)], False)                        # retour de couette
        poly([(hw - 0.45, -hd), (hw, -hd + 0.45)], False)                       # couette rabattue
    elif key == "chevet":
        poly(circle(0, 0, min(w, d) * 0.25))
    elif key in ("armoire", "placard"):
        poly([(-hw, hd), (hw, -hd)], False)
        poly([(-hw, -hd), (hw, hd)], False)
        poly([(-hw, -hd + 0.04), (hw, -hd + 0.04)], False)
    elif key in ("canape", "fauteuil"):
        arm, back = 0.18, 0.22
        poly(box_pts(-hw, -hd, -hw + arm, hd))
        poly(box_pts(hw - arm, -hd, hw, hd))
        poly([(-hw + arm, hd - back), (hw - arm, hd - back)], False)
        seats = max(1, round((w - 2 * arm) / 0.70))
        for i in range(1, seats):
            x = -hw + arm + (w - 2 * arm) * i / seats
            poly([(x, -hd + 0.05), (x, hd - back)], False)
    elif key == "table_basse":
        poly(box_pts(-hw + 0.04, -hd + 0.04, hw - 0.04, hd - 0.04))
    elif key == "meuble_tv":
        poly(box_pts(-w * 0.35, hd - 0.12, w * 0.35, hd - 0.05))
    elif key == "chaise":
        poly([(-hw + 0.03, hd - 0.07), (hw - 0.03, hd - 0.07)], False)
    elif key == "etagere":
        for i in range(1, 4):
            x = -hw + w * i / 4
            poly([(x, -hd), (x, hd)], False)
    elif key == "cuisine":
        poly([(-hw, -hd + 0.03), (hw, -hd + 0.03)], False)
        sink_x = -hw + w * 0.35
        poly(box_pts(sink_x - 0.40, -hd + 0.10, sink_x + 0.40, hd - 0.08))
        poly(box_pts(sink_x - 0.36, -hd + 0.13, sink_x, hd - 0.11))
        poly(circle(sink_x - 0.18, 0.0, 0.03, 12))
        hob_x = hw - min(0.55, w * 0.25)
        for dx in (-0.14, 0.14):
            for dy in (-0.12, 0.12):
                poly(circle(hob_x + dx, dy, 0.08 if dy > 0 else 0.06, 20))
    elif key == "frigo":
        poly([(-hw, hd), (hw, -hd)], False)
        poly([(-hw, -hd), (hw, hd)], False)
    elif key == "baignoire":
        inner = [(-hw + 0.10, -hd + 0.12), (hw - 0.25, -hd + 0.07), (hw - 0.08, -hd + 0.20), (hw - 0.08, hd - 0.20),
                 (hw - 0.25, hd - 0.07), (-hw + 0.10, hd - 0.12)]
        poly(inner)
        poly(circle(hw - 0.22, 0.0, 0.03, 12))
    elif key == "douche":
        poly([(-hw, hd), (hw, -hd)], False)
        poly([(-hw, -hd), (hw, hd)], False)
        poly(circle(0, 0, 0.04, 12))
    elif key in ("lavabo", "lave_mains"):
        e = [(0.0 + math.cos(a) * w * 0.32, -0.02 + math.sin(a) * d * 0.30) for a in
             [2 * math.pi * k / 28 for k in range(28)]]
        poly(e)
        poly(circle(0, hd - 0.06, 0.02, 10))
    elif key == "wc":
        tank = box_pts(-hw, hd - 0.18, hw, hd)
        bowl = [(math.cos(a) * 0.18, hd - 0.42 + math.sin(a) * 0.24) for a in [2 * math.pi * k / 32 for k in range(32)]]
        inner = [(math.cos(a) * 0.12, hd - 0.44 + math.sin(a) * 0.16) for a in [2 * math.pi * k / 24 for k in range(24)]]
        fills[:] = [bowl, tank]
        lines[:] = [bowl + [bowl[0]], tank + [tank[0]], inner + [inner[0]]]
    elif key in ("lave_linge", "seche_linge"):
        poly(circle(0, -0.03, min(w, d) * 0.33))
        poly([(-hw, hd - 0.10), (hw, hd - 0.10)], False)
    elif key == "console":
        poly([(-hw, -hd + 0.04), (hw, -hd + 0.04)], False)
    return fills, lines


# ------------------------------------------------------------------ creation ---
def to_world(room: Room, p):
    return add(room.origin, rot((p[0] - room.origin[0], p[1] - room.origin[1]), room.angle))


def pose_world(room: Room, pose: Pose):
    """Centre, angle (dos du meuble = +y local de l'objet) dans le repere du plan."""
    center = to_world(room, pose.c)
    n = rot(pose.n, room.angle)                     # face avant
    angle = math.atan2(n[0], -n[1])                 # +y local tourne = -n (dos vers le mur)
    return center, angle


def library_parts() -> list[str]:
    try:
        parts = tapir("GetAvailableLibraryParts", {"filterByTypeId": "Object"}).get("libraryParts", [])
    except Exception:  # noqa: BLE001
        try:
            parts = tapir("GetAvailableLibraryParts").get("libraryParts", [])
        except Exception:  # noqa: BLE001
            return []
    names = {p.get("documentName") for p in parts if p.get("documentName") and p.get("typeId", "Object") == "Object"}
    return sorted(names)


def matches(key: str, names: list[str]) -> list[str]:
    words = [plain(w) for w in MEUBLES[key]["mots"]]
    banned = [plain(w) for w in MEUBLES[key].get("sauf", [])]
    if not words:
        return []
    scored = []
    for name in names:
        low = " " + plain(name) + " "
        hits = [w for w in words if w in low]
        if hits and not any(b in low for b in banned):
            first = words.index(hits[0])
            scored.append((first, len(name), name))
    return [n for _f, _l, n in sorted(scored)]


def library_choice(key: str, settings: dict, names: list[str]) -> str:
    if settings["rendu"] == RENDUS[1]:
        return ""
    if key in settings["bibliotheque"]:
        name = settings["bibliotheque"][key]
        return name if (not name or name in names) else ""
    found = matches(key, names)
    return found[0] if found else ""


def story_level(story: int) -> float:
    for s in tapir("GetStories").get("stories", []):
        if s.get("index") == story:
            return float(s.get("level", 0.0))
    return 0.0


def set_pens() -> None:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables if "attributeId" in t]
    details = [t for t in tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                ).get("penTables", []) if "error" not in t] if ids else []
    table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
    if table:
        tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
            "attributeId": table["attributeId"], "name": table["name"], "pens": [
                {"index": STYLO_TRAIT, "width": 0.13, "description": "Meubles - traits",
                 "color": {"red": COULEUR_TRAIT[0], "green": COULEUR_TRAIT[1], "blue": COULEUR_TRAIT[2]}},
                {"index": STYLO_FOND, "width": 0.10, "description": "Meubles - fond blanc",
                 "color": {"red": 1.0, "green": 1.0, "blue": 1.0}}]}]})


def build(room: Room, poses: list[Pose], story: int, settings: dict, names: list[str]) -> tuple[list[str], list[str]]:
    """Cree les meubles. Renvoie (guids crees, noms des objets de bibliotheque utilises)."""
    level = story_level(story)
    objects, drawn = [], []                        # (pose, nom d'objet) / pose
    for pose in poses:
        name = library_choice(pose.key, settings, names) if pose.key != "cuisine" else ""
        (objects if name else drawn).append((pose, name))
    guids, used = [], []

    if objects:
        data = []
        for pose, name in objects:
            center, angle = pose_world(room, pose)
            if settings["retourner"].get(pose.key):
                angle += math.pi
            corner = add(center, rot((-pose.w / 2, -pose.d / 2), angle))
            data.append({"libraryPartName": name, "coordinates": {"x": corner[0], "y": corner[1], "z": level},
                         "dimensions": {"x": pose.w, "y": pose.d, "z": MEUBLES[pose.key]["h"]},
                         "angle": angle, "floorIndex": story})
        made = tapir("CreateObjects", {"objectsData": data}).get("elements", [])
        fixes, ok_pairs = [], []
        for (pose, name), elem in zip(objects, made):
            if isinstance(elem, dict) and "elementId" in elem:
                ok_pairs.append((pose, elem))
                used.append(name)
            else:
                drawn.append((pose, ""))                     # objet refuse : dessin 2D a la place
        if ok_pairs:                                         # recentrer (origine propre a chaque objet)
            try:
                boxes = api("API.Get2DBoundingBoxes", {"elements": [e for _p, e in ok_pairs]}).get("boundingBoxes2D", [])
            except Exception:  # noqa: BLE001
                boxes = []
            for (pose, elem), box in zip(ok_pairs, boxes):
                b = box.get("boundingBox2D") if isinstance(box, dict) else None
                if b:
                    want = to_world(room, pose.c)
                    dx, dy = want[0] - (b["xMin"] + b["xMax"]) / 2, want[1] - (b["yMin"] + b["yMax"]) / 2
                    if abs(dx) > 0.005 or abs(dy) > 0.005:
                        fixes.append({"elementId": elem["elementId"], "moveVector": {"x": dx, "y": dy, "z": 0.0}})
                guids.append(elem["elementId"]["guid"])
            if fixes:
                try:
                    tapir("MoveElements", {"elementsWithMoveVectors": fixes})
                except Exception:  # noqa: BLE001
                    pass

    if drawn:
        set_pens()
        solid = T.solid_fill_id()
        hatches, polylines, owner_h, owner_l = [], [], [], []
        for i, (pose, _n) in enumerate(drawn):
            center, angle = pose_world(room, pose)
            fills, lines = symbol(pose.key, pose.w, pose.d)
            tr = lambda p: add(center, rot(p, angle))  # noqa: E731
            for f in fills:
                h = {"floorInd": story, "showArea": False, "coordinates": [{"x": x, "y": y} for x, y in map(tr, f)],
                     "fillPenIndex": STYLO_FOND, "fillBackgroundPenIndex": STYLO_FOND, "contourPenIndex": STYLO_FOND}
                if solid:
                    h["fillId"] = solid
                hatches.append(h)
                owner_h.append(i)
            for ln in lines:
                polylines.append({"floorInd": story, "linePenIndex": STYLO_TRAIT, "penWeightMm": 0.13,
                                  "coordinates": [{"x": x, "y": y} for x, y in map(tr, ln)]})
                owner_l.append(i)
        groups = [[] for _ in drawn]
        made_h = tapir("CreateHatches", {"hatchesData": hatches}).get("elements", []) if hatches else []
        made_l = tapir("CreatePolylines", {"polylinesData": polylines}).get("elements", []) if polylines else []
        for owner, elem in list(zip(owner_h, made_h)) + list(zip(owner_l, made_l)):
            if isinstance(elem, dict) and "elementId" in elem:
                groups[owner].append(elem)
                guids.append(elem["elementId"]["guid"])
        groups = [g for g in groups if len(g) > 1]
        if groups:
            try:
                tapir("CreateGroups", {"elementGroups": [{"elements": g} for g in groups]})
            except Exception:  # noqa: BLE001
                pass
    return guids, used


# ---------------------------------------------------------------- portes ---
def openings(story: int, near) -> tuple[list, list]:
    """Emprises des portes (avec leur debattement) et des fenetres proches de la piece."""
    from shapely.geometry import box
    doors, windows = [], []
    for kind, out in (("Door", doors), ("Window", windows)):
        try:
            elems = tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
        except Exception:  # noqa: BLE001
            continue
        if not elems:
            continue
        floors = tapir("GetDetailsOfElements", {"elements": elems, "fields": ["floorIndex"]}).get("detailsOfElements", [])
        elems = [e for e, f in zip(elems, floors) if f.get("floorIndex") == story]
        if not elems:
            continue
        try:
            boxes = api("API.Get2DBoundingBoxes", {"elements": elems}).get("boundingBoxes2D", [])
        except Exception:  # noqa: BLE001
            continue
        for item in boxes:
            b = item.get("boundingBox2D") if isinstance(item, dict) else None
            if b:
                g = box(b["xMin"], b["yMin"], b["xMax"], b["yMax"])
                if g.intersects(near):
                    out.append(g.buffer(0.05 if kind == "Door" else 0.0))
    return doors, windows


# ----------------------------------------------------------------- actions ---
def remember(guids: list[str], story: int) -> None:
    memory = load_json(MEMOIRE)
    memory.setdefault("dernier", [])
    memory["dernier"] = guids
    memory.setdefault(f"etage {story}", [])
    memory[f"etage {story}"] = list(dict.fromkeys(memory[f"etage {story}"] + guids))
    save_json(MEMOIRE, memory)


def furnish_polygon(polygon, kind: str, settings: dict, story: int, names: list[str]) -> str:
    doors, windows = openings(story, polygon.buffer(0.5))
    room = Room(polygon, doors, windows)
    items = chosen_items(settings, kind)
    if not items:
        return f"{kind} : aucun meuble coché."
    placed, missing = furnish(room, kind, items, settings)
    if not placed:
        return f"{kind} ({polygon.area:.1f} m²) : pas assez de place pour les meubles cochés."
    guids, used = build(room, room.poses, story, settings, names)
    remember(guids, story)
    text = f"{kind} ({polygon.area:.1f} m²) : {len(room.poses)} meuble(s) placé(s)"
    if used:
        text += f", dont {len(used)} objet(s) 3D de la bibliothèque"
    text += "."
    if missing:
        text += " Pas de place pour : " + ", ".join(dict.fromkeys(missing)) + "."
    return text


def zone_polygon(zone: dict):
    from shapely.geometry import Polygon
    pts = [(c["x"], c["y"]) for c in zone.get("outline", [])]
    holes = [[(c["x"], c["y"]) for c in h.get("polygonOutline", [])] for h in zone.get("holes", [])]
    if len(pts) < 3:
        return None
    poly = Polygon(pts, [h for h in holes if len(h) >= 3]).buffer(0)
    return poly if poly.geom_type == "Polygon" else None


def run_zones(settings: dict) -> str:
    try:
        selected = {e["elementId"]["guid"] for e in api("API.GetSelectedElements").get("elements", [])}
    except Exception:  # noqa: BLE001
        selected = {e["elementId"]["guid"] for e in tapir("GetSelectedElements").get("elements", [])}
    zones = [z for z in T.read_zones() if z["element"]["elementId"]["guid"] in selected]
    if not zones:
        return "Sélectionne une ou plusieurs zones dans Archicad, puis relance."
    names = library_parts()
    lines = []
    for z in zones:
        kind = guess_room(z["name"]) or settings["piece"]
        poly = zone_polygon(z)
        if poly is None:
            lines.append(f"{z['name'] or 'Zone'} : contour illisible.")
            continue
        lines.append(f"{z['name'] or kind} → " + furnish_polygon(poly, kind, settings, z["story"], names))
    return "\n".join(lines)


def zone_name_at(point: dict, story: int) -> str:
    from shapely.geometry import Point
    for z in T.read_zones():
        if z["story"] != story:
            continue
        poly = zone_polygon(z)
        if poly is not None and poly.contains(Point(point["x"], point["y"])):
            return z["name"]
    return ""


def run_undo() -> str:
    memory = load_json(MEMOIRE)
    guids = memory.get("dernier", [])
    if not guids:
        return "Rien à annuler."
    try:
        tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in guids]})
    except Exception as error:  # noqa: BLE001
        return f"Erreur : {error}"
    for key in list(memory):
        if isinstance(memory[key], list):
            memory[key] = [g for g in memory[key] if g not in guids]
    memory["dernier"] = []
    save_json(MEMOIRE, memory)
    return f"Dernier aménagement effacé ({len(guids)} élément(s))."


# ------------------------------------------------------------------ fenetre ---
def settings_window(settings: dict) -> None:
    import time
    import tkinter as tk
    from tkinter import ttk

    state = settings
    root = tk.Tk()
    root.title("Meubler une pièce")
    root.attributes("-topmost", True)
    outer = ttk.Frame(root, padding=12)
    outer.grid()
    status = tk.StringVar(value="Choisis le type de pièce, coche les meubles, puis clique dans la pièce.")

    top = ttk.Frame(outer)
    top.grid(row=0, column=0, sticky="ew")
    ttk.Label(top, text="Pièce", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    room_var = tk.StringVar(value=state["piece"])
    room_box = ttk.Combobox(top, values=list(PIECES), textvariable=room_var, state="readonly", width=24)
    room_box.grid(row=0, column=1, padx=8, sticky="w")
    ttk.Label(top, text="(les zones sélectionnées gardent le type de leur nom)", foreground="#777"
              ).grid(row=0, column=2, sticky="w")

    items_box = ttk.LabelFrame(outer, text="Meubles proposés", padding=8)
    items_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    item_vars: dict = {}
    lit_var = tk.StringVar(value=state["lit"])

    def refresh_items(*_):
        for child in items_box.winfo_children():
            child.destroy()
        item_vars.clear()
        room = room_var.get()
        for i, (key, default) in enumerate(PIECES[room]):
            var = tk.BooleanVar(value=state["choix"].get(f"{room}/{key}", default))
            item_vars[key] = var
            spec = MEUBLES[key]
            label = spec["nom"] if key in ("cuisine", "table") else f"{spec['nom']}  ({spec['w']:.2f} × {spec['d']:.2f} m)"
            ttk.Checkbutton(items_box, text=label, variable=var).grid(row=i, column=0, sticky="w", pady=1)
            if key == "lit_double":
                ttk.Combobox(items_box, values=list(TAILLES_LIT), textvariable=lit_var, state="readonly", width=14
                             ).grid(row=i, column=1, padx=8)

    room_box.bind("<<ComboboxSelected>>", refresh_items)
    refresh_items()

    out_box = ttk.LabelFrame(outer, text="Rendu", padding=8)
    out_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    render_var = tk.StringVar(value=state["rendu"])
    for i, r in enumerate(RENDUS):
        ttk.Radiobutton(out_box, text=r, value=r, variable=render_var).grid(row=i, column=0, sticky="w")
    ttk.Button(out_box, text="Bibliothèque...", command=lambda: library_window()).grid(row=0, column=1, rowspan=2, padx=10)

    report = tk.Text(outer, width=78, height=7, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=3, column=0, pady=(10, 0))
    ttk.Label(outer, textvariable=status, foreground="#555").grid(row=4, column=0, sticky="w", pady=(6, 0))

    def show(text):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", text)
        report.configure(state="disabled")

    def collect():
        room = room_var.get()
        state["piece"] = room
        for key, var in item_vars.items():
            state["choix"][f"{room}/{key}"] = bool(var.get())
        state["lit"] = lit_var.get()
        state["rendu"] = render_var.get()
        save_json(REGLAGES, state)

    def library_window():
        win = tk.Toplevel(root)
        win.title("Meubles de la bibliothèque Archicad")
        win.attributes("-topmost", True)
        win.transient(root)
        frame = ttk.Frame(win, padding=10)
        frame.grid()
        names = library_parts()
        ttk.Label(frame, text=f"{len(names)} objets trouvés dans la bibliothèque chargée. "
                              "Choisis l'objet de chaque meuble (ou « dessin 2D »).",
                  foreground="#555").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        rows = {}
        for i, key in enumerate(k for k in MEUBLES if k != "cuisine"):
            ttk.Label(frame, text=MEUBLES[key]["nom"]).grid(row=i + 1, column=0, sticky="w", pady=1)
            found = matches(key, names)
            values = ["(dessin 2D)"] + found + [n for n in names if n not in found]
            current = state["bibliotheque"].get(key, found[0] if found else "")
            var = tk.StringVar(value=current or "(dessin 2D)")
            ttk.Combobox(frame, values=values, textvariable=var, width=40).grid(row=i + 1, column=1, padx=6)
            flip = tk.BooleanVar(value=state["retourner"].get(key, False))
            ttk.Checkbutton(frame, text="tourner de 180°", variable=flip).grid(row=i + 1, column=2, sticky="w")
            rows[key] = (var, flip)

        def save():
            for key, (var, flip) in rows.items():
                value = var.get()
                state["bibliotheque"][key] = "" if value == "(dessin 2D)" else value
                state["retourner"][key] = bool(flip.get())
            save_json(REGLAGES, state)
            win.destroy()
        ttk.Button(frame, text="Enregistrer", command=save).grid(row=len(rows) + 2, column=2, sticky="e", pady=(8, 0))

    def click_room():
        collect()
        story = tapir("GetStories").get("actStory", 0)
        status.set("Lecture des murs...")
        root.update_idletasks()
        wand = T.MagicWand(story)
        names = library_parts()
        root.withdraw()
        results = []
        try:
            while True:
                T.focus_archicad()
                started = time.time()
                point, reason = T.pick_point("Clique dans la pièce à meubler (Echap pour terminer)")
                if point is None:
                    if not results and time.time() - started < 1.0:
                        results.append(f"Archicad n'a pas pu attendre le clic ({reason}).")
                    break
                face = wand.face(point) if not wand.empty else None
                if face is None:
                    results.append("Pièce non fermée à cet endroit (vérifie les murs / traits).")
                    continue
                zone = zone_name_at(point, story)
                kind = guess_room(zone) or state["piece"]
                results.append(furnish_polygon(face, kind, state, story, names))
        finally:
            root.deiconify()
        show("\n".join(results) or "Aucune pièce cliquée.")

    def do(action):
        status.set("En cours...")
        root.update_idletasks()
        try:
            if action == "clic":
                click_room()
            elif action == "zones":
                collect()
                show(run_zones(state))
            else:
                show(run_undo())
            status.set("Terminé. Chaque meuble est un groupe : déplace-le ou tourne-le librement.")
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")

    buttons = ttk.Frame(outer)
    buttons.grid(row=5, column=0, pady=(10, 0), sticky="e")
    ttk.Button(buttons, text="Annuler le dernier", command=lambda: do("annuler")).grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    ttk.Button(buttons, text="Meubler les zones sélectionnées", command=lambda: do("zones")).grid(row=0, column=2, padx=4)
    ttk.Button(buttons, text="Cliquer dans la pièce...", command=lambda: do("clic")).grid(row=0, column=3, padx=4)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=T.PORT)
    args, _ = parser.parse_known_args()
    T.PORT = args.port
    settings_window(load_settings())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
