# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Ombres decoratives en vue en plan - script Tapir (Archicad 28, mode normal)

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis dans Archicad : palette Tapir > choisir "Ombres.py" > Executer.

Une petite fenetre s'ouvre et reste ouverte : couleur (gris par defaut),
longueur et inclinaison (0 a 359 degres, avec apercu). "Appliquer" dessine sur
l'etage courant une ombre unie sous les murs, les poteaux et les objets, en
remplacant les ombres precedentes : on ajuste et on reapplique jusqu'au resultat
voulu. "Supprimer les ombres" les enleve. Les reglages choisis sont retenus.

Les ombres utilisent un stylo reserve (215) du jeu de stylos actif.
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
DECALAGE_MM = 2.0          # longueur de l'ombre par defaut (mm papier)
DIRECTION_DEG = 315.0      # inclinaison par defaut : 0 = droite, 90 = haut, 315 = bas a droite
COULEUR_DEFAUT = (0.70, 0.70, 0.70)   # gris (rouge, vert, bleu entre 0 et 1)
STYLO_OMBRE = 215          # stylo reserve aux ombres (le tableau des surfaces utilise 216-255)
OMBRE_MURS = True
OMBRE_POTEAUX = True
OMBRE_OBJETS = True
MOTS_RONDS = "round, rond, ronde, circ, cylind, oval, ellip, tabouret, stool, pouf, vasque, lavabo rond"
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


DIRECTIONS = {  # libelle -> angle en degres
    "Bas a droite": -45.0, "Bas": -90.0, "Bas a gauche": -135.0, "Gauche": 180.0,
    "Haut a gauche": 135.0, "Haut": 90.0, "Haut a droite": 45.0, "Droite": 0.0,
}


def load_settings() -> dict:
    saved = load_json("ombres_reglages.json")
    color = saved.get("couleur")
    if not (isinstance(color, list) and len(color) == 3):
        color = list(COULEUR_DEFAUT)
    angle = saved.get("angle_deg")
    if not isinstance(angle, (int, float)):
        angle = DIRECTIONS.get(saved.get("direction"), DIRECTION_DEG)   # anciens reglages
    length = saved.get("decalage_mm", DECALAGE_MM)
    return {"couleur": [float(c) for c in color], "angle_deg": float(angle) % 360.0,
            "decalage_mm": float(length) if isinstance(length, (int, float)) else DECALAGE_MM,
            "mots_ronds": str(saved.get("mots_ronds", MOTS_RONDS))}


def settings_window(settings: dict, apply) -> None:
    """Fenetre de reglages qui reste ouverte : chaque clic sur Appliquer redessine les ombres."""
    import tkinter as tk
    from tkinter import colorchooser, ttk

    def to_hex(c):
        return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in c)

    state = dict(settings)
    root = tk.Tk()
    root.title("Ombres - Claude Bridge")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.grid()

    # couleur
    ttk.Label(frame, text="Couleur").grid(row=0, column=0, sticky="w", pady=4)
    swatch = tk.Label(frame, width=6, bg=to_hex(state["couleur"]), relief="groove")
    swatch.grid(row=0, column=1, sticky="w", padx=8)

    def pick():
        rgb, _ = colorchooser.askcolor(color=to_hex(state["couleur"]), title="Couleur des ombres", parent=root)
        if rgb:
            state["couleur"] = [v / 255.0 for v in rgb]
            swatch.configure(bg=to_hex(state["couleur"]))

    def default_color():
        state["couleur"] = list(COULEUR_DEFAUT)
        swatch.configure(bg=to_hex(state["couleur"]))

    ttk.Button(frame, text="Choisir...", command=pick).grid(row=0, column=2, padx=4, sticky="w")
    ttk.Button(frame, text="Gris par defaut", command=default_color).grid(row=0, column=3, padx=4, sticky="w")

    # longueur
    ttk.Label(frame, text="Longueur (mm papier)").grid(row=1, column=0, sticky="w", pady=4)
    length = tk.DoubleVar(value=state["decalage_mm"])
    ttk.Scale(frame, from_=0.5, to=15, variable=length, length=200,
              command=lambda _v: length_text.set(f"{length.get():.1f}")).grid(row=1, column=1, columnspan=2, sticky="we", padx=8)
    length_text = tk.StringVar(value=f"{state['decalage_mm']:.1f}")
    length_entry = ttk.Entry(frame, textvariable=length_text, width=6)
    length_entry.grid(row=1, column=3, sticky="w", padx=4)

    # inclinaison
    ttk.Label(frame, text="Inclinaison (degres)").grid(row=2, column=0, sticky="w", pady=4)
    angle = tk.DoubleVar(value=state["angle_deg"])
    angle_text = tk.StringVar(value=f"{state['angle_deg']:.0f}")
    compass = tk.Canvas(frame, width=90, height=90, highlightthickness=0)
    compass.grid(row=2, column=3, rowspan=2, padx=4)

    def draw_compass():
        compass.delete("all")
        compass.create_oval(8, 8, 82, 82, outline="#999")
        compass.create_rectangle(37, 37, 53, 53, fill="#333", outline="")      # l'element
        a = math.radians(angle.get())
        ln = 14 + 2 * min(15.0, max(0.5, length.get()))
        dx, dy = ln * math.cos(a), -ln * math.sin(a)
        compass.create_polygon(37 + dx, 37 + dy, 53 + dx, 37 + dy, 53 + dx, 53 + dy, 37 + dx, 53 + dy,
                               fill=to_hex(state["couleur"]), outline="")
        compass.create_rectangle(37, 37, 53, 53, fill="#333", outline="")
        compass.create_text(45, 4, text="haut", font=("Segoe UI", 7), anchor="n")

    def on_angle(_v=None):
        angle_text.set(f"{angle.get():.0f}")
        draw_compass()

    ttk.Scale(frame, from_=0, to=359, variable=angle, length=200, command=on_angle
              ).grid(row=2, column=1, columnspan=2, sticky="we", padx=8)
    angle_entry = ttk.Entry(frame, textvariable=angle_text, width=6)
    angle_entry.grid(row=3, column=1, sticky="w", padx=8)
    ttk.Label(frame, text="0 = droite, 90 = haut, 180 = gauche, 270 = bas",
              foreground="#666").grid(row=3, column=2, sticky="w")

    def read_entries(_e=None):
        try:
            angle.set(float(angle_text.get().replace(",", ".")) % 360.0)
        except ValueError:
            pass
        try:
            length.set(max(0.1, float(length_text.get().replace(",", "."))))
        except ValueError:
            pass
        on_angle()
        length_text.set(f"{length.get():.1f}")

    angle_entry.bind("<Return>", read_entries)
    angle_entry.bind("<FocusOut>", read_entries)
    length_entry.bind("<Return>", read_entries)
    length_entry.bind("<FocusOut>", read_entries)
    length.trace_add("write", lambda *_: draw_compass())

    ttk.Label(frame, text="Objets ronds si\nle nom contient").grid(row=4, column=0, sticky="w", pady=4)
    round_words = tk.StringVar(value=state.get("mots_ronds", MOTS_RONDS))
    ttk.Entry(frame, textvariable=round_words, width=40).grid(row=4, column=1, columnspan=3, sticky="we", padx=8)

    status = tk.StringVar(value="Regle puis clique sur Appliquer : tu vois le resultat dans Archicad.")
    ttk.Label(frame, textvariable=status, foreground="#555", wraplength=420).grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def do(action):
        read_entries()
        state["angle_deg"] = angle.get() % 360.0
        state["decalage_mm"] = length.get()
        state["mots_ronds"] = round_words.get()
        save_json("ombres_reglages.json", state)
        status.set("En cours...")
        root.update_idletasks()
        try:
            status.set(apply(action, dict(state)))
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")

    buttons = ttk.Frame(frame)
    buttons.grid(row=6, column=0, columnspan=4, pady=(12, 0), sticky="e")
    ttk.Button(buttons, text="Supprimer les ombres", command=lambda: do("supprimer")).grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    ttk.Button(buttons, text="Appliquer", command=lambda: do("dessiner")).grid(row=0, column=2, padx=4)
    root.bind("<Escape>", lambda _e: root.destroy())
    draw_compass()
    root.mainloop()


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


def ellipse(center: Point, rx: float, ry: float, angle: float = 0.0, sides: int = 48) -> list[Point]:
    c, s = math.cos(angle), math.sin(angle)
    pts = []
    for k in range(sides):
        t = 2 * math.pi * k / sides
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((center[0] + x * c - y * s, center[1] + x * s + y * c))
    return pts


def arc_points(a: Point, b: Point, center: Point, step_deg: float = 7.5) -> list[Point]:
    """Points intermediaires de l'arc (le plus court) de a vers b autour de center."""
    r = math.dist(a, center)
    t1 = math.atan2(a[1] - center[1], a[0] - center[0])
    t2 = math.atan2(b[1] - center[1], b[0] - center[0])
    span = math.remainder(t2 - t1, 2 * math.pi)
    n = max(1, int(abs(math.degrees(span)) / step_deg))
    return [(center[0] + r * math.cos(t1 + span * k / n), center[1] + r * math.sin(t1 + span * k / n))
            for k in range(1, n)]


def round_edges(points: list[Point], centers: list[Point]) -> list[Point]:
    """Les contours lus dans Archicad perdent leurs arcs : on recourbe les aretes dont les deux
    bouts sont a la meme distance d'un centre d'arc connu (mur courbe)."""
    out: list[Point] = []
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        out.append(a)
        for c in centers:
            ra, rb = math.dist(a, c), math.dist(b, c)
            if ra > 1e-3 and abs(ra - rb) < max(1e-3, ra * 1e-3) and math.dist(a, b) > 1e-3:
                out.extend(arc_points(a, b, c))
                break
    return out


def circle(center: Point, radius: float, sides: int = 48) -> list[Point]:
    return [(center[0] + radius * math.cos(2 * math.pi * k / sides),
             center[1] + radius * math.sin(2 * math.pi * k / sides)) for k in range(sides)]


# ---------------------------------------------------------------- lecture ---
def current_story() -> int:
    return tapir("GetStories").get("actStory", 0)


def elements(kind: str) -> list[dict]:
    return tapir("GetElementsByType", {"elementType": kind}).get("elements", [])


def wall_shapes(story: int) -> list[list[Point]]:
    elems = elements("Wall")
    if not elems:
        return []
    details = tapir("GetDetailsOfElements", {"elements": elems, "fields": ["floorIndex", "floorPlanPolygons", "details"]}
                    ).get("detailsOfElements", [])
    shapes = []
    for detail in details:
        if "error" in detail or detail.get("floorIndex") != story:
            continue
        d = detail.get("details", {})
        centers = []
        angle = d.get("arcAngle", 0.0) or 0.0
        if abs(angle) > 1e-6 and "begCoordinate" in d and "endCoordinate" in d:   # mur courbe
            b, e = (d["begCoordinate"]["x"], d["begCoordinate"]["y"]), (d["endCoordinate"]["x"], d["endCoordinate"]["y"])
            half = math.dist(b, e) / 2
            if half > 1e-6:
                m = ((b[0] + e[0]) / 2, (b[1] + e[1]) / 2)
                ux, uy = (e[0] - b[0]) / (2 * half), (e[1] - b[1]) / (2 * half)
                h = half / math.tan(abs(angle) / 2)
                centers = [(m[0] - uy * h, m[1] + ux * h), (m[0] + uy * h, m[1] - ux * h)]
        for poly in detail.get("floorPlanPolygons", []):
            pts = clean([(c["x"], c["y"]) for c in poly.get("coordinates", [])])
            shapes.append(round_edges(pts, centers) if centers else pts)
    return shapes


def looks_round(points: list[Point], center: Point, axis: float) -> float | None:
    """Rayon si le contour lu est celui d'un poteau rond (arcs perdus), sinon None."""
    pts = clean(points)
    if not pts:
        return None
    radii = [math.dist(p, center) for p in pts]
    r = sum(radii) / len(radii)
    if r <= 1e-4 or max(abs(x - r) for x in radii) > r * 0.02:
        return None                                  # sommets pas sur un meme cercle
    if len(pts) <= 3:
        return r
    if len(pts) == 4:                                # rond (sommets sur les axes) ou carre (sommets en coin) ?
        on_axes = all(abs(math.remainder(math.atan2(p[1] - center[1], p[0] - center[0]) - axis, math.pi / 2))
                      < math.radians(3) for p in pts)
        return r if on_axes else None
    return r                                         # 5 sommets ou plus sur un cercle : rond


def column_shapes(story: int) -> list[list[Point]]:
    elems = elements("Column")
    if not elems:
        return []
    details = tapir("GetDetailsOfElements", {"elements": elems, "fields": ["floorIndex", "floorPlanPolygons", "details"]}
                    ).get("detailsOfElements", [])
    on_story = [(e, d) for e, d in zip(elems, details) if "error" not in d and d.get("floorIndex") == story]
    boxes: list = [None] * len(on_story)
    try:
        answer = tapir("Get3DBoundingBoxes", {"elements": [e for e, _ in on_story]}).get("boundingBoxes3D", [])
        boxes = [item.get("boundingBox3D") if isinstance(item, dict) else None for item in answer] + boxes[len(answer):]
    except Exception:  # noqa: BLE001
        pass
    shapes = []
    for (_, detail), box in zip(on_story, boxes):
        d = detail.get("details", {})
        origin = d.get("origin", {})
        center = (origin.get("x", 0.0), origin.get("y", 0.0))
        axis = d.get("axisRotationAngle", 0.0) or 0.0
        polys = [clean([(c["x"], c["y"]) for c in p.get("coordinates", [])]) for p in detail.get("floorPlanPolygons", [])]
        polys = [p for p in polys if p]
        if box and (box["xMax"] - box["xMin"]) > 1e-6:
            bc = ((box["xMin"] + box["xMax"]) / 2, (box["yMin"] + box["yMax"]) / 2)
        else:
            bc = center
        if not polys:                                # contour illisible (cercle a 2 arcs) : rond d'apres son emprise
            if box:
                shapes.append(ellipse(bc, (box["xMax"] - box["xMin"]) / 2, (box["yMax"] - box["yMin"]) / 2))
            continue
        for pts in polys:
            r = looks_round(pts, center, axis) or looks_round(pts, bc, axis)
            if r is None and len(pts) < 3 and box:
                r = (box["xMax"] - box["xMin"]) / 2
            shapes.append(circle(center if looks_round(pts, center, axis) else bc, r) if r else pts)
    return shapes


def plan_boxes(elems: list[dict]) -> list[dict | None]:
    """Cadre reel du symbole en plan (xMin, yMin, xMax, yMax) de chaque element, ou None."""
    boxes: list[dict | None] = [None] * len(elems)
    try:  # cadre 2D du symbole dessine (API Archicad)
        answer = api("API.Get2DBoundingBoxes", {"elements": elems}).get("boundingBoxes2D", [])
        for i, item in enumerate(answer[:len(elems)]):
            if isinstance(item, dict) and "boundingBox2D" in item:
                boxes[i] = item["boundingBox2D"]
    except Exception:  # noqa: BLE001
        pass
    missing = [i for i, b in enumerate(boxes) if b is None]
    if missing:
        try:  # a defaut, emprise 3D (Tapir)
            answer = tapir("Get3DBoundingBoxes", {"elements": [elems[i] for i in missing]}).get("boundingBoxes3D", [])
            for i, item in zip(missing, answer):
                if isinstance(item, dict) and "boundingBox3D" in item:
                    boxes[i] = item["boundingBox3D"]
        except Exception:  # noqa: BLE001
            pass
    return boxes


def centered_rectangle(center: Point, width: float, depth: float, angle: float) -> list[Point]:
    c, s = math.cos(angle), math.sin(angle)
    corners = [(-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2)]
    return [(center[0] + x * c - y * s, center[1] + x * s + y * c) for x, y in corners]


def object_shapes(story: int, round_words: list[str]) -> list[list[Point]]:
    elems = elements("Object")
    if not elems:
        return []
    details = tapir("GetDetailsOfElements", {"elements": elems, "fields": ["floorIndex", "details"]}
                    ).get("detailsOfElements", [])
    on_story = [(e, d) for e, d in zip(elems, details) if "error" not in d and d.get("floorIndex") == story]
    if not on_story:
        return []
    boxes = plan_boxes([e for e, _ in on_story])
    shapes = []
    for (_, detail), box in zip(on_story, boxes):
        d = detail.get("details", {})
        size = d.get("dimensions", {})
        width, depth = size.get("x", 0.0), size.get("y", 0.0)
        angle = d.get("angle", 0.0)
        name = d.get("libPart", {}).get("name", "").lower()
        is_tree = any(word in name for word in MOTS_ARBRES)
        is_round = is_tree or any(word and word in name for word in round_words)
        if box:
            x1, y1, x2, y2 = box["xMin"], box["yMin"], box["xMax"], box["yMax"]
            if x2 - x1 <= 1e-6 or y2 - y1 <= 1e-6:
                continue
            center = ((x1 + x2) / 2, (y1 + y2) / 2)
            quarter = abs(math.remainder(angle, math.pi / 2)) < math.radians(1)
            if is_round and (quarter or width <= 1e-6 or depth <= 1e-6):
                shapes.append(ellipse(center, (x2 - x1) / 2, (y2 - y1) / 2))          # rond / ovale
            elif is_round:
                shapes.append(ellipse(center, width / 2, depth / 2, angle))
            elif quarter or width <= 1e-6 or depth <= 1e-6:
                shapes.append([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])   # cadre exact du symbole
            else:
                shapes.append(centered_rectangle(center, width, depth, angle))
        elif width > 1e-6 and depth > 1e-6:   # dernier recours : point d'insertion + dimensions
            origin = d.get("origin", {})
            o = (origin.get("x", 0.0), origin.get("y", 0.0))
            if is_round:
                c, sn = math.cos(angle), math.sin(angle)
                mid = (o[0] + (width / 2) * c - (depth / 2) * sn, o[1] + (width / 2) * sn + (depth / 2) * c)
                shapes.append(ellipse(mid, width / 2, depth / 2, angle))
            else:
                shapes.append(rectangle(o, width, depth, angle, bool(d.get("reflected"))))
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
def run(action: str, settings: dict) -> str:
    story = current_story()

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

    if action == "supprimer":
        return "Ombres de l'etage courant supprimees."

    shapes: list[list[Point]] = []
    if OMBRE_MURS:
        shapes += wall_shapes(story)
    if OMBRE_POTEAUX:
        shapes += column_shapes(story)
    if OMBRE_OBJETS:
        shapes += object_shapes(story, [w.strip().lower() for w in settings.get("mots_ronds", MOTS_RONDS).split(",")])
    if not shapes:
        return "Aucun mur, poteau ou objet sur l'etage courant : pas d'ombre a dessiner."

    length = settings["decalage_mm"] * ECHELLE / 1000.0
    angle = math.radians(settings["angle_deg"])
    d = (length * math.cos(angle), length * math.sin(angle))

    warning = ""
    if not set_shadow_pen(tuple(settings["couleur"])):
        warning = f" Attention : couleur non appliquee (stylo {STYLO_OMBRE})."
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
    return (f"Ombres dessinees : {len(created)} - inclinaison {settings['angle_deg']:.0f} degres, "
            f"longueur {settings['decalage_mm']:.1f} mm.{warning}")


def main() -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args, _ = parser.parse_known_args()
    PORT = args.port

    settings = load_settings()
    try:
        settings_window(settings, lambda action, state: run(action, state))
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
