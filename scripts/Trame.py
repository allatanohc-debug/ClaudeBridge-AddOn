# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Trame - axes de trame (grille) au milieu des murs - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis dans Archicad : palette Tapir > "Trame.py" > Executer.

1. Selectionne les murs porteurs (et poteaux) dans le plan. Rien de selectionne = tout l'etage.
2. "Creer / mettre a jour la trame" :
   - une ligne d'axe passe au milieu de chaque mur (les murs alignes donnent un seul axe),
   - les axes depassent du plan, avec une bulle numerotee au bout (A, B, C... / 1, 2, 3...),
   - cotes entre les axes et cote totale (en option).
3. Tout est fait de vrais elements 2D Archicad (lignes, cercles, textes, cotes), groupes :
   tu peux les modifier a la main. Relancer remplace la trame de l'etage.
"""

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

PORT = 19723
MEMOIRE = "trame.json"                    # guids dessines, par etage (exclus de la baguette magique)
REGLAGES = "trame_reglages.json"

STYLO_AXE = 200                           # lignes d'axe et bulles
STYLO_TEXTE = 201                         # lettres / chiffres des bulles
STYLO_COTE = 202                          # cotes dessinees (si les cotes associatives echouent)
NOM_TYPE_LIGNE = "Trame - axe (Claude)"

NUMEROTATIONS = ("Verticaux : A, B, C...   Horizontaux : 1, 2, 3...",
                 "Verticaux : 1, 2, 3...   Horizontaux : A, B, C...")
BULLES = ("Aux deux bouts", "En bas et à gauche", "En haut et à droite")
LIGNES = ("Tirets-points (axe)", "Tirets", "Continu")

DEFAUTS = {
    "echelle": 100,
    "numerotation": NUMEROTATIONS[0],
    "haut_en_bas": False,                 # chiffres/lettres des axes horizontaux de haut en bas
    "bulles": BULLES[0],
    "ligne": LIGNES[0],
    "diametre_mm": 8.0,                   # diametre des bulles sur le papier
    "texte_mm": 3.5,
    "debord_mm": 22.0,                    # longueur des axes au-dela du plan (papier)
    "fusion_cm": 15.0,                    # axes plus proches que ca = un seul axe
    "epaisseur_min_cm": 0.0,              # ignorer les murs plus fins (0 = tous)
    "poteaux": True,
    "cotes": True,
    "cote_totale": True,
    "couleur_axe": [0.45, 0.45, 0.50],
    "epaisseur_axe": 0.13,
    "couleur_texte": [0.15, 0.15, 0.15],
}


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


def ok_items(items: list) -> list:
    return [i for i in items if isinstance(i, dict) and "error" not in i and "elementId" in i]


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
    out = {}
    for key, default in DEFAUTS.items():
        value = saved.get(key, default)
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, (int, float)):
            try:
                value = type(default)(value)
            except (TypeError, ValueError):
                value = default
        elif isinstance(default, list):
            value = [float(v) for v in value] if isinstance(value, list) and len(value) == 3 else list(default)
        elif isinstance(default, str):
            choices = {"numerotation": NUMEROTATIONS, "bulles": BULLES, "ligne": LIGNES}.get(key)
            value = value if choices is None or value in choices else default
        out[key] = value
    return out


def memory_key(story: int) -> str:
    try:
        info = tapir("GetProjectInfo")
        project = info.get("projectPath") or info.get("projectName") or "Sans titre"
    except Exception:  # noqa: BLE001
        project = "Sans titre"
    return f"{project}|{story}"


# ------------------------------------------------------------------ lecture ---
def current_story() -> int:
    return tapir("GetStories").get("actStory", 0)


def selection() -> list[dict]:
    try:
        elements = api("API.GetSelectedElements").get("elements", [])
    except Exception:  # noqa: BLE001
        elements = tapir("GetSelectedElements").get("elements", [])
    return [e for e in elements if isinstance(e, dict) and "elementId" in e]


def read(elements: list[dict]) -> list[dict]:
    rows = []
    for i in range(0, len(elements), 150):
        part = elements[i:i + 150]
        answer = tapir("GetDetailsOfElements", {"elements": part,
                                                "fields": ["type", "floorIndex", "details", "floorPlanPolygons"]}
                       ).get("detailsOfElements", [])
        for element, detail in zip(part, answer):
            if "error" not in detail:
                detail["guid"] = element["elementId"]["guid"]
                rows.append(detail)
    return rows


def source_elements(settings: dict, story: int) -> tuple[list[dict], bool]:
    """Murs (et poteaux) selectionnes ; rien de selectionne = tout l'etage courant."""
    kinds = ("Wall", "Column") if settings["poteaux"] else ("Wall",)
    own = set(load_json(MEMOIRE).get(memory_key(story), []))
    rows = [r for r in read(selection()) if r.get("type") in kinds and r["guid"] not in own]
    if rows:
        return rows, True
    found = []
    for kind in kinds:
        try:
            found += tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
        except Exception:  # noqa: BLE001
            pass
    return [r for r in read(found) if r.get("floorIndex") == story], False


# ---------------------------------------------------------------- geometrie ---
def polygons(row: dict) -> list[list[tuple]]:
    out = []
    for p in row.get("floorPlanPolygons", []) or []:
        pts = [(c["x"], c["y"]) for c in p.get("coordinates", [])]
        if len(pts) >= 3:
            out.append(pts)
    return out


def wall_axis(row: dict, min_thickness: float):
    """(angle, point milieu, longueur) de l'axe d'un mur droit, mesure sur son dessin en plan."""
    d = row.get("details", {})
    beg, end = d.get("begCoordinate"), d.get("endCoordinate")
    if not beg or not end or abs(d.get("arcAngle", 0.0) or 0.0) > 1e-6:
        return None
    dx, dy = end["x"] - beg["x"], end["y"] - beg["y"]
    length = math.hypot(dx, dy)
    if length < 0.05:
        return None
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    pts = [pt for poly in polygons(row) for pt in poly]
    if pts:
        ts = [(x - beg["x"]) * nx + (y - beg["y"]) * ny for x, y in pts]
        t1, t2 = min(ts), max(ts)
    else:
        th = float(d.get("thickness", 0.0) or 0.0)
        t1, t2 = 0.0, th
    if (t2 - t1) + 1e-6 < min_thickness:
        return None
    mid = (t1 + t2) / 2
    point = (beg["x"] + nx * mid, beg["y"] + ny * mid)
    return math.atan2(uy, ux), point, length


def dominant_angle(axes: list) -> float:
    """Orientation principale (modulo 90 degres), ponderee par la longueur des murs."""
    if not axes:
        return 0.0
    best, best_score = 0.0, -1.0
    for angle, _p, _l in axes:
        a = angle % (math.pi / 2)
        score = sum(l for b, _q, l in axes if angle_gap(a, b % (math.pi / 2), math.pi / 2) < math.radians(1.5))
        if score > best_score:
            best, best_score = a, score
    if best > math.pi / 4:                 # garder des axes proches de l'horizontale / verticale
        best -= math.pi / 2
    return best


def angle_gap(a: float, b: float, period: float) -> float:
    d = (a - b) % period
    return min(d, period - d)


def cluster(values: list[tuple[float, float]], tol: float) -> list[float]:
    """values = [(position, poids)] -> positions fusionnees (moyenne ponderee)."""
    out = []
    for pos, w in sorted(values):
        if out and pos - out[-1][0] / out[-1][1] <= tol:
            out[-1][0] += pos * w
            out[-1][1] += w
        else:
            out.append([pos * w, w])
    return [s / w for s, w in out]


def label(index: int, letters: bool) -> str:
    if not letters:
        return str(index + 1)
    text = ""
    n = index
    while True:
        text = chr(ord("A") + n % 26) + text
        n = n // 26 - 1
        if n < 0:
            return text


# ------------------------------------------------------------------ stylos ---
def set_pens(settings: dict) -> None:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables if "attributeId" in t]
    if not ids:
        return
    details = [t for t in tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                ).get("penTables", []) if "error" not in t]
    table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
    if table is None:
        return

    def pen(index, rgb, width, description):
        return {"index": index, "width": width, "description": description,
                "color": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}

    tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
        "attributeId": table["attributeId"], "name": table["name"], "pens": [
            pen(STYLO_AXE, settings["couleur_axe"], settings["epaisseur_axe"], "Trame - axes et bulles"),
            pen(STYLO_TEXTE, settings["couleur_texte"], 0.18, "Trame - reperes"),
            pen(STYLO_COTE, settings["couleur_texte"], 0.13, "Trame - cotes"),
        ]}]})


def line_type(settings: dict) -> dict | None:
    """Type de ligne des axes : tirets-points cree une fois dans le projet ; None = ligne continue."""
    style = settings["ligne"]
    if style == LIGNES[2]:
        return None
    dash = [{"dash": 10.0, "gap": 1.5}, {"dash": 1.5, "gap": 1.5}] if style == LIGNES[0] else \
        [{"dash": 4.0, "gap": 2.0}]
    name = NOM_TYPE_LIGNE if style == LIGNES[0] else "Trame - tirets (Claude)"
    try:
        answer = tapir("CreateLines", {"overwriteExisting": True, "lineDataArray": [{
            "name": name, "lineType": "Dashed", "scaleWithPlan": False,
            "period": sum(d["dash"] + d["gap"] for d in dash), "dashItems": dash}]})
        first = (answer.get("attributeIds") or [{}])[0]
        if "attributeId" in first:
            return first["attributeId"]
    except Exception:  # noqa: BLE001
        pass
    return None


# ------------------------------------------------------------------ dessin ---
class Drawing:
    def __init__(self, story: int, scale: float, ltype):
        self.story, self.mm, self.ltype = story, scale / 1000.0, ltype
        self.lines, self.circles, self.texts, self.axis_lines = [], [], [], []

    def axis(self, a, b):
        data = {"floorInd": self.story, "begCoordinate": {"x": a[0], "y": a[1]},
                "endCoordinate": {"x": b[0], "y": b[1]}, "linePenIndex": STYLO_AXE}
        if self.ltype:
            data["lineTypeId"] = self.ltype
        self.axis_lines.append(data)

    def line(self, a, b, pen=STYLO_COTE):
        self.lines.append({"floorInd": self.story, "begCoordinate": {"x": a[0], "y": a[1]},
                           "endCoordinate": {"x": b[0], "y": b[1]}, "linePenIndex": pen})

    def circle(self, c, r):
        self.circles.append({"floorInd": self.story, "origin": {"x": c[0], "y": c[1]}, "radius": r,
                             "linePenIndex": STYLO_AXE})

    def text(self, content, p, size_mm, angle=0.0, pen=STYLO_TEXTE, anchor="MiddleMiddle"):
        self.texts.append({"coordinate": {"x": p[0], "y": p[1], "z": 0.0}, "text": content,
                           "floorIndex": self.story,
                           "style": {"height": size_mm, "penIndex": pen, "anchor": anchor,
                                     "justification": "Center", "angle": angle}})

    def create_axes(self) -> list[dict]:
        if not self.axis_lines:
            return []
        try:
            made = tapir("CreateLineElements", {"linesData": self.axis_lines}).get("elements", [])
        except Exception:  # noqa: BLE001                        type de ligne refuse : sans
            for d in self.axis_lines:
                d.pop("lineTypeId", None)
            made = tapir("CreateLineElements", {"linesData": self.axis_lines}).get("elements", [])
        self.axis_lines = []
        return made                                              # meme ordre que demande

    def flush(self) -> list[dict]:
        out = []
        if self.lines:
            out += ok_items(tapir("CreateLineElements", {"linesData": self.lines}).get("elements", []))
        if self.circles:
            out += ok_items(tapir("CreateCircles", {"circlesData": self.circles}).get("elements", []))
        if self.texts:
            out += ok_items(tapir("CreateTexts", {"textsData": self.texts}).get("elements", []))
        self.lines, self.circles, self.texts = [], [], []
        return out


def fmt(value: float) -> str:
    return f"{value:.2f}"


# ----------------------------------------------------------------- actions ---
def delete_grid(story: int) -> int:
    memory = load_json(MEMOIRE)
    key = memory_key(story)
    guids = memory.pop(key, [])
    if guids:
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in guids]})
        except Exception:  # noqa: BLE001
            pass
    save_json(MEMOIRE, memory)
    return len(guids)


def run_create(settings: dict) -> str:
    story = current_story()
    rows, from_selection = source_elements(settings, story)
    if not rows:
        return "Aucun mur trouvé. Sélectionne des murs (ou laisse vide pour tout l'étage), puis relance."
    min_th = settings["epaisseur_min_cm"] / 100.0
    walls = [a for a in (wall_axis(r, min_th) for r in rows if r.get("type") == "Wall") if a]
    if not walls:
        return "Aucun mur droit utilisable (murs courbes ou trop fins ignorés)."
    angle = dominant_angle(walls)
    ux, uy = math.cos(angle), math.sin(angle)              # direction "horizontale" de la trame
    vx, vy = -uy, ux                                        # direction "verticale"

    def to_uv(p):
        return p[0] * ux + p[1] * uy, p[0] * vx + p[1] * vy

    def to_xy(u, v):
        return u * ux + v * vx, u * uy + v * vy

    verticals, horizontals, skipped = [], [], 0             # (position, poids)
    for a, point, length in walls:
        u, v = to_uv(point)
        if angle_gap(a, angle, math.pi) < math.radians(2):
            horizontals.append((v, length))
        elif angle_gap(a, angle + math.pi / 2, math.pi) < math.radians(2):
            verticals.append((u, length))
        else:
            skipped += 1
    columns = 0
    for r in rows:
        if r.get("type") == "Column":
            pts = [to_uv(p) for poly in polygons(r) for p in poly]
            if pts:
                us, vs = [p[0] for p in pts], [p[1] for p in pts]
                verticals.append(((min(us) + max(us)) / 2, 0.3))
                horizontals.append(((min(vs) + max(vs)) / 2, 0.3))
                columns += 1

    tol = max(settings["fusion_cm"], 0.5) / 100.0
    xs, ys = cluster(verticals, tol), cluster(horizontals, tol)
    if not xs and not ys:
        return "Aucun axe trouvé."

    all_uv = [to_uv(p) for r in rows for poly in polygons(r) for p in poly] or \
        [(x, y) for x in xs for y in ys]
    umin, umax = min(p[0] for p in all_uv), max(p[0] for p in all_uv)
    vmin, vmax = min(p[1] for p in all_uv), max(p[1] for p in all_uv)

    scale = max(float(settings["echelle"]), 1.0)
    mm = scale / 1000.0
    over = settings["debord_mm"] * mm
    radius = settings["diametre_mm"] * mm / 2
    letters_vertical = settings["numerotation"] == NUMEROTATIONS[0]
    bubbles = settings["bulles"]
    start = bubbles in (BULLES[0], BULLES[1])               # bas / gauche
    finish = bubbles in (BULLES[0], BULLES[2])              # haut / droite

    removed = delete_grid(story)
    set_pens(settings)
    draw = Drawing(story, scale, line_type(settings))

    ordered_y = sorted(ys, reverse=settings["haut_en_bas"])
    names_x = {x: label(i, letters_vertical) for i, x in enumerate(sorted(xs))}
    names_y = {y: label(i, not letters_vertical) for i, y in enumerate(ordered_y)}

    for x in sorted(xs):                                    # axes verticaux, du bas vers le haut
        draw.axis(to_xy(x, vmin - over), to_xy(x, vmax + over))
    for y in sorted(ys):                                    # axes horizontaux, de gauche a droite
        draw.axis(to_xy(umin - over, y), to_xy(umax + over, y))
    axis_ids = draw.create_axes()
    created = ok_items(axis_ids)

    for x in sorted(xs):
        for ok, v in ((start, vmin - over - radius), (finish, vmax + over + radius)):
            if ok:
                draw.circle(to_xy(x, v), radius)
                draw.text(names_x[x], to_xy(x, v), settings["texte_mm"], angle)
    for y in sorted(ys):
        for ok, u in ((start, umin - over - radius), (finish, umax + over + radius)):
            if ok:
                draw.circle(to_xy(u, y), radius)
                draw.text(names_y[y], to_xy(u, y), settings["texte_mm"], angle)

    notes = []
    if settings["cotes"] or settings["cote_totale"]:
        v_ids = axis_ids[:len(xs)]
        h_ids = axis_ids[len(xs):]
        chains = [
            (sorted(xs), v_ids, lambda t, off: to_xy(t, vmin - off), (ux, uy), angle),
            (sorted(ys), h_ids, lambda t, off: to_xy(umin - off, t), (vx, vy), angle + math.pi / 2),
        ]
        for positions, ids, place, direction, text_angle in chains:
            if len(positions) < 2:
                continue
            rows_dims = []
            if settings["cotes"]:
                rows_dims.append((positions, ids, over * 0.40))
            if settings["cote_totale"] and len(positions) > 2:
                rows_dims.append(([positions[0], positions[-1]], [ids[0], ids[-1]], over * 0.70))
            for pos, guids, off in rows_dims:
                if not associative_dimension(guids, place(pos[0], off), direction, story):
                    manual_dimension(draw, pos, place, off, text_angle, settings, mm)
                    if not any(n.startswith("Cotes dessinées") for n in notes):
                        notes.append("Cotes dessinées en lignes et textes (les cotes Archicad liées "
                                     "aux axes n'ont pas pu être créées).")

    created += draw.flush()
    guids = [e["elementId"]["guid"] for e in created]
    guids += DIMS.pop("guids", [])
    if guids:
        try:
            tapir("CreateGroups", {"elementGroups": [{"elements": [{"elementId": {"guid": g}} for g in guids]}]})
        except Exception:  # noqa: BLE001
            pass
    memory = load_json(MEMOIRE)
    memory[memory_key(story)] = guids
    save_json(MEMOIRE, memory)

    lines = [f"Trame créée : {len(xs)} axe(s) vertical(aux), {len(ys)} axe(s) horizontal(aux)"
             + (" (ancienne trame remplacée)" if removed else "") + ".",
             ("Depuis la sélection : " if from_selection else "Tout l'étage : ")
             + f"{len(walls)} mur(s)" + (f", {columns} poteau(x)" if columns else "") + "."]
    if skipped:
        lines.append(f"{skipped} mur(s) en biais ignoré(s) (pas parallèles à la trame).")
    lines += notes
    return "\n".join(lines)


DIMS: dict = {}


def associative_dimension(line_ids: list[dict], ref, direction, story: int) -> bool:
    """Cote Archicad liee aux debuts des lignes d'axe (elle suit si on deplace un axe)."""
    if any("elementId" not in e for e in line_ids):
        return False
    try:
        answer = tapir("CreateAssociativeDimensions", {"dimensionsData": [{
            "referencePoint": {"x": ref[0], "y": ref[1]},
            "direction": {"x": direction[0], "y": direction[1]},
            "floorIndex": story,
            "witnessPoints": [{"elementId": e["elementId"], "nodeId": 1} for e in line_ids]}]})
        made = ok_items(answer.get("elements", []))
        if made:
            DIMS.setdefault("guids", []).extend(m["elementId"]["guid"] for m in made)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def manual_dimension(draw: Drawing, positions: list[float], place, off: float, text_angle: float,
                     settings: dict, mm: float) -> None:
    a, b = place(positions[0], off), place(positions[-1], off)
    draw.line(a, b)
    tick = 1.5 * mm
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    for t in positions:
        p = place(t, off)
        draw.line((p[0] - (ux + nx) * tick, p[1] - (uy + ny) * tick),
                  (p[0] + (ux + nx) * tick, p[1] + (uy + ny) * tick))
        draw.line((p[0] - nx * tick * 1.5, p[1] - ny * tick * 1.5), (p[0] + nx * tick * 1.5, p[1] + ny * tick * 1.5))
    for t1, t2 in zip(positions, positions[1:]):
        p = place((t1 + t2) / 2, off)
        q = (p[0] + nx * 1.6 * mm, p[1] + ny * 1.6 * mm)
        draw.text(fmt(t2 - t1), q, 2.0, text_angle, pen=STYLO_COTE)


def run_delete() -> str:
    story = current_story()
    n = delete_grid(story)
    return f"Trame de l'étage supprimée ({n} élément(s))." if n else "Pas de trame créée par le script sur cet étage."


# ------------------------------------------------------------------ fenetre ---
def settings_window(settings: dict) -> None:
    import tkinter as tk
    from tkinter import colorchooser, ttk

    def to_hex(c):
        return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in c)

    state = settings
    root = tk.Tk()
    root.title("Trame - axes au milieu des murs")
    root.attributes("-topmost", True)
    outer = ttk.Frame(root, padding=12)
    outer.grid()
    sel_text = tk.StringVar(value="Lecture de la sélection...")
    status = tk.StringVar(value="Sélectionne les murs porteurs (rien = tout l'étage), puis « Créer la trame ».")
    ttk.Label(outer, textvariable=sel_text, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")

    variables: dict = {}

    def field(parent, row, label, key, kind="num", values=None, width=8, unit=""):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        if kind == "combo":
            var = tk.StringVar(value=state[key])
            ttk.Combobox(parent, values=values, textvariable=var, state="readonly", width=46
                         ).grid(row=row, column=1, columnspan=2, sticky="w", padx=6)
        elif kind == "check":
            var = tk.BooleanVar(value=state[key])
            ttk.Checkbutton(parent, variable=var).grid(row=row, column=1, sticky="w", padx=6)
        else:
            var = tk.StringVar(value=str(state[key]))
            ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky="w", padx=6)
            if unit:
                ttk.Label(parent, text=unit, foreground="#666").grid(row=row, column=2, sticky="w")
        variables[key] = var

    box1 = ttk.LabelFrame(outer, text="Axes", padding=8)
    box1.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    field(box1, 0, "Numérotation", "numerotation", "combo", NUMEROTATIONS)
    field(box1, 1, "Axes horizontaux numérotés de haut en bas", "haut_en_bas", "check")
    field(box1, 2, "Type de ligne", "ligne", "combo", LIGNES)
    field(box1, 3, "Inclure les poteaux", "poteaux", "check")
    field(box1, 4, "Fusionner les axes à moins de", "fusion_cm", unit="cm")
    field(box1, 5, "Ignorer les murs plus fins que", "epaisseur_min_cm", unit="cm  (0 = tous)")

    box2 = ttk.LabelFrame(outer, text="Bulles et cotes", padding=8)
    box2.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    field(box2, 0, "Bulles", "bulles", "combo", BULLES)
    field(box2, 1, "Diamètre des bulles", "diametre_mm", unit="mm (papier)")
    field(box2, 2, "Hauteur des lettres", "texte_mm", unit="mm")
    field(box2, 3, "Dépassement des axes", "debord_mm", unit="mm (papier)")
    field(box2, 4, "Échelle du plan  1 :", "echelle")
    field(box2, 5, "Cotes entre les axes", "cotes", "check")
    field(box2, 6, "Cote totale", "cote_totale", "check")

    box3 = ttk.LabelFrame(outer, text="Couleurs", padding=8)
    box3.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    swatches = {}

    def color_row(row, label, key):
        ttk.Label(box3, text=label).grid(row=row, column=0, sticky="w", pady=2)
        sw = tk.Label(box3, width=4, relief="groove", bg=to_hex(state[key]))
        sw.grid(row=row, column=1, padx=6)
        swatches[key] = sw

        def pick():
            rgb, _ = colorchooser.askcolor(color=to_hex(state[key]), title=label, parent=root)
            if rgb:
                state[key] = [v / 255.0 for v in rgb]
                sw.configure(bg=to_hex(state[key]))
        ttk.Button(box3, text="Couleur...", command=pick).grid(row=row, column=2)

    color_row(0, "Axes et bulles", "couleur_axe")
    color_row(1, "Lettres, chiffres et cotes", "couleur_texte")
    ttk.Label(box3, text="Épaisseur des axes").grid(row=2, column=0, sticky="w", pady=2)
    variables["epaisseur_axe"] = tk.StringVar(value=str(state["epaisseur_axe"]))
    ttk.Entry(box3, textvariable=variables["epaisseur_axe"], width=8).grid(row=2, column=1, padx=6)
    ttk.Label(box3, text="mm", foreground="#666").grid(row=2, column=2, sticky="w")

    report = tk.Text(outer, width=70, height=6, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=4, column=0, pady=(10, 0))
    ttk.Label(outer, textvariable=status, foreground="#555").grid(row=5, column=0, sticky="w", pady=(6, 0))

    def show(text):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", text)
        report.configure(state="disabled")

    def collect():
        for key, var in variables.items():
            default = DEFAUTS[key]
            value = var.get()
            if isinstance(default, bool):
                state[key] = bool(value)
            elif isinstance(default, (int, float)):
                try:
                    state[key] = type(default)(float(str(value).replace(",", ".")))
                except ValueError:
                    pass
            else:
                state[key] = value
        save_json(REGLAGES, state)

    def do(action):
        status.set("En cours...")
        root.update_idletasks()
        try:
            collect()
            show(run_create(state) if action == "creer" else run_delete())
            status.set("Terminé. Ctrl+Z dans Archicad annule aussi.")
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")

    last = {"sel": None}

    def poll():
        try:
            sel = selection()
            key = tuple(sorted(e["elementId"]["guid"] for e in sel))
            if key != last["sel"]:
                last["sel"] = key
                rows = read(sel) if sel else []
                w = sum(1 for r in rows if r.get("type") == "Wall")
                c = sum(1 for r in rows if r.get("type") == "Column")
                sel_text.set(f"Sélection : {w} mur(s), {c} poteau(x)" if (w or c)
                             else "Rien de sélectionné : la trame sera faite sur tout l'étage.")
        except Exception:  # noqa: BLE001
            sel_text.set("Archicad ne répond pas (Tapir lancé ?)")
        root.after(1500, poll)

    buttons = ttk.Frame(outer)
    buttons.grid(row=6, column=0, pady=(10, 0), sticky="e")
    ttk.Button(buttons, text="Supprimer la trame (étage)", command=lambda: do("supprimer")).grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    ttk.Button(buttons, text="Créer / mettre à jour la trame", command=lambda: do("creer")).grid(row=0, column=2, padx=4)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.after(200, poll)
    root.mainloop()


def main() -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args, _ = parser.parse_known_args()
    PORT = args.port
    settings = load_settings()
    try:
        settings_window(settings)
    except ImportError as error:
        print(f"(Fenetre indisponible : {error}. Reglages enregistres utilises.)")
        print(run_create(settings))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
