# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely>=2.0"]
# ///
"""
Amenagement - personnages vus de dessus - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\ (a cote de TableauSurfaces.py, qu'il utilise)
Puis dans Archicad : palette Tapir > "Amenagement.py" > Executer.

Personnages en vue en plan dessines par le script (pas de DWG) : debout, qui marche,
assis, au bureau, couche. Chaque personnage est un groupe de vrais elements 2D Archicad
(remplissage blanc + contour), modifiable et deplacable comme le reste.

Placement : 1er clic = position, 2e clic = direction du regard. Echap pour terminer.
"""

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import TableauSurfaces as T  # noqa: E402

MEMOIRE = "personnages.json"       # guids dessines (exclus de la baguette magique)
REGLAGES = "amenagement_reglages.json"
POSES = ("Debout", "Qui marche", "Assis", "Au bureau (assis, bras en avant)", "Couche")
STYLO_CONTOUR_PERSO = 207          # gris fonce du contour
COULEUR_CONTOUR = (0.30, 0.30, 0.30)
TRAIT_MM = 0.18


# --------------------------------------------------------------- silhouettes ---
# Coordonnees en metres, personnage centre sur (0, 0), regard vers +y.
def ellipse(cx, cy, rx, ry, angle=0.0, n=36):
    c, s = math.cos(angle), math.sin(angle)
    return [(cx + rx * math.cos(t) * c - ry * math.sin(t) * s,
             cy + rx * math.cos(t) * s + ry * math.sin(t) * c)
            for t in (2 * math.pi * k / n for k in range(n))]


def capsule(x1, y1, x2, y2, r, n=10):
    """Membre arrondi (bras, jambe, pied) entre deux points."""
    ang = math.atan2(y2 - y1, x2 - x1)
    pts = []
    for k in range(n + 1):                       # demi-cercle au bout 2
        t = ang - math.pi / 2 + math.pi * k / n
        pts.append((x2 + r * math.cos(t), y2 + r * math.sin(t)))
    for k in range(n + 1):                       # demi-cercle au bout 1
        t = ang + math.pi / 2 + math.pi * k / n
        pts.append((x1 + r * math.cos(t), y1 + r * math.sin(t)))
    return pts


def mirror(shape):
    return [(-x, y) for x, y in reversed(shape)]


def silhouette(pose: str):
    """Renvoie (formes fusionnees = contour exterieur, formes de detail dessinees par-dessus).

    Style "trait blanc" des plans de presentation : silhouette blanche, contour fin, avec les bras,
    les mains et la tete dessines par-dessus le corps pour lire le geste."""
    head = ellipse(0, 0.035, 0.092, 0.108)
    hair = ellipse(0, 0.005, 0.094, 0.08)                    # masse des cheveux vers l'arriere
    shoulders = ellipse(0, 0, 0.22, 0.115)
    hand = lambda x, y: ellipse(x, y, 0.033, 0.033, n=16)   # noqa: E731
    if pose == "Qui marche":
        arm_f = capsule(-0.19, 0.01, -0.21, 0.17, 0.04)
        arm_b = capsule(0.19, -0.01, 0.20, -0.14, 0.04)
        body = [ellipse(0, 0, 0.22, 0.115, math.radians(-8)),
                capsule(-0.08, 0.06, -0.09, 0.27, 0.042),          # pied avant
                capsule(0.08, -0.04, 0.09, -0.19, 0.042)]          # pied arriere
        details = [arm_f, arm_b, hand(-0.212, 0.20), hand(0.202, -0.17), head, hair]
    elif pose == "Assis":
        arm_l = capsule(-0.19, 0.0, -0.15, 0.22, 0.04)
        body = [shoulders, capsule(-0.09, 0.04, -0.10, 0.40, 0.075), capsule(0.09, 0.04, 0.10, 0.40, 0.075),
                capsule(-0.10, 0.40, -0.11, 0.53, 0.045), capsule(0.10, 0.40, 0.11, 0.53, 0.045)]
        details = [arm_l, mirror(arm_l), hand(-0.145, 0.25), hand(0.145, 0.25), head, hair]
    elif pose.startswith("Au bureau"):
        arm_l = capsule(-0.19, 0.0, -0.13, 0.27, 0.04)
        body = [shoulders, capsule(-0.09, 0.04, -0.10, 0.38, 0.075), capsule(0.09, 0.04, 0.10, 0.38, 0.075)]
        details = [arm_l, mirror(arm_l), hand(-0.12, 0.30), hand(0.12, 0.30), head, hair]
    elif pose == "Couche":
        head = ellipse(0, 0.72, 0.085, 0.10)
        hair = ellipse(0, 0.76, 0.09, 0.07)
        arm_l = capsule(-0.21, 0.53, -0.23, 0.15, 0.04)
        body = [ellipse(0, 0.53, 0.22, 0.11), capsule(0, 0.50, 0, 0.05, 0.17),
                capsule(-0.08, 0.05, -0.10, -0.60, 0.07), capsule(0.08, 0.05, 0.10, -0.60, 0.07),
                capsule(-0.10, -0.60, -0.13, -0.70, 0.042), capsule(0.10, -0.60, 0.13, -0.70, 0.042)]
        details = [arm_l, mirror(arm_l), hand(-0.232, 0.11), hand(0.232, 0.11), head, hair]
    else:   # debout
        arm_l = capsule(-0.20, 0.01, -0.215, -0.06, 0.04)
        body = [shoulders, capsule(-0.08, 0.0, -0.09, 0.15, 0.042), capsule(0.08, 0.0, 0.09, 0.15, 0.042)]
        details = [arm_l, mirror(arm_l), head, hair]
    return body, details


def place(points, x, y, angle):
    c, s = math.cos(angle), math.sin(angle)
    return [{"x": x + px * c - py * s, "y": y + px * s + py * c} for px, py in points]


def person_elements(pose: str, x: float, y: float, angle: float, story: int, solid, scale: float = 1.0):
    """Hatches + polylignes Tapir d'un personnage (angle = direction du regard, 0 = vers +y)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    body, details = silhouette(pose)
    body = [[(px * scale, py * scale) for px, py in shape] for shape in body]
    details = [[(px * scale, py * scale) for px, py in shape] for shape in details]
    outline = unary_union([Polygon(b).buffer(0) for b in body + details])
    rings = [list(g.exterior.coords)[:-1] for g in getattr(outline, "geoms", [outline])]
    hatches, lines = [], []

    def white(points):
        hatch = {"floorInd": story, "showArea": False, "coordinates": place(points, x, y, angle),
                 "fillPenIndex": T.STYLO_BLANC, "fillBackgroundPenIndex": T.STYLO_BLANC,
                 "contourPenIndex": STYLO_CONTOUR_PERSO}
        if solid:
            hatch["fillId"] = solid
        hatches.append(hatch)

    def stroke(points, weight):
        ring = place(points, x, y, angle)
        lines.append({"floorInd": story, "linePenIndex": STYLO_CONTOUR_PERSO, "penWeightMm": weight,
                      "coordinates": ring + [ring[0]]})

    for ring in rings:                     # silhouette blanche + contour exterieur un peu plus epais
        white(ring)
        stroke(ring, TRAIT_MM * 1.4)
    for d in details:                      # bras, mains, tete, cheveux par-dessus (lecture du geste)
        white(d)
        stroke(d, TRAIT_MM)
    return hatches, lines


# ------------------------------------------------------------------ actions ---
def set_pen() -> None:
    try:
        tables = T.tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
        ids = [{"attributeId": t["attributeId"]} for t in tables]
        details = [t for t in T.tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                      ).get("penTables", []) if "error" not in t]
        table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
        if table is None:
            return
        widths = {p.get("index"): p.get("width", 0.1) for p in table.get("pens", [])}
        T.tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
            "attributeId": table["attributeId"], "name": table["name"], "pens": [
                {"index": T.STYLO_BLANC, "width": widths.get(T.STYLO_BLANC, 0.1), "description": "Blanc",
                 "color": {"red": 1.0, "green": 1.0, "blue": 1.0}},
                {"index": STYLO_CONTOUR_PERSO, "width": widths.get(STYLO_CONTOUR_PERSO, 0.1),
                 "description": "Personnages - contour",
                 "color": {"red": COULEUR_CONTOUR[0], "green": COULEUR_CONTOUR[1], "blue": COULEUR_CONTOUR[2]}}]}]})
    except Exception:  # noqa: BLE001
        pass


def project_key(story: int) -> str:
    project = T.tapir("GetProjectInfo")
    return f"{project.get('projectPath') or project.get('projectName') or 'Sans titre'}|{story}"


def draw_person(pose: str, x: float, y: float, angle: float, story: int, solid, scale: float) -> list[str]:
    hatches, lines = person_elements(pose, x, y, angle, story, solid, scale)
    created = T.ok_items(T.tapir("CreateHatches", {"hatchesData": hatches}).get("elements", []))
    created += T.ok_items(T.tapir("CreatePolylines", {"polylinesData": lines}).get("elements", []))
    if created:
        try:
            T.tapir("CreateGroups", {"elementGroups": [{"elements": created}]})
        except Exception:  # noqa: BLE001
            pass
    return [e["elementId"]["guid"] for e in created if "elementId" in e]


def place_people(pose: str, scale: float) -> str:
    story = T.tapir("GetStories").get("actStory", 0)
    key = project_key(story)
    set_pen()
    solid = T.solid_fill_id()
    memory = T.load_json(MEMOIRE)
    count = 0
    while True:
        T.focus_archicad()
        where, _ = T.pick_point(f"{pose} : clique la position (Echap = fin)")
        if where is None:
            break
        T.focus_archicad()
        towards, _ = T.pick_point("Clique la direction du regard (Echap = vers le haut)")
        angle = 0.0
        if towards is not None and math.dist((where["x"], where["y"]), (towards["x"], towards["y"])) > 1e-3:
            angle = math.atan2(towards["y"] - where["y"], towards["x"] - where["x"]) - math.pi / 2
        guids = draw_person(pose, where["x"], where["y"], angle, story, solid, scale)
        memory.setdefault(key, []).extend(guids)
        T.save_json(MEMOIRE, memory)
        count += 1
    return f"{count} personnage(s) place(s)." if count else "Aucun personnage place."


def delete_people() -> str:
    story = T.tapir("GetStories").get("actStory", 0)
    key = project_key(story)
    memory = T.load_json(MEMOIRE)
    guids = memory.pop(key, [])
    if guids:
        try:
            T.tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in guids]})
        except Exception:  # noqa: BLE001
            pass
    T.save_json(MEMOIRE, memory)
    return "Personnages de l'étage supprimés." if guids else "Aucun personnage à supprimer."


# ------------------------------------------------------------------ fenetre ---
def window() -> None:
    import tkinter as tk
    from tkinter import ttk

    saved = T.load_json(REGLAGES)
    root = tk.Tk()
    root.title("Amenagement - Claude Bridge")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    f = ttk.Frame(root, padding=14)
    f.grid()
    box = ttk.LabelFrame(f, text=" Personnages vus de dessus ", padding=10)
    box.grid(row=0, column=0, sticky="we")
    ttk.Label(box, text="Pose").grid(row=0, column=0, sticky="w", pady=3)
    pose = tk.StringVar(value=saved.get("pose") if saved.get("pose") in POSES else POSES[0])
    ttk.Combobox(box, values=POSES, textvariable=pose, state="readonly", width=34).grid(row=0, column=1, sticky="w", padx=8)
    ttk.Label(box, text="Taille").grid(row=1, column=0, sticky="w", pady=3)
    size = tk.StringVar(value=saved.get("taille", "Adulte"))
    ttk.Combobox(box, values=("Adulte", "Grand", "Enfant"), textvariable=size, state="readonly", width=34
                 ).grid(row=1, column=1, sticky="w", padx=8)
    ttk.Label(box, text="1er clic = position, 2e clic = direction du regard. Echap pour terminer.",
              foreground="#666").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
    status = tk.StringVar(value="")
    ttk.Label(f, textvariable=status, foreground="#555", wraplength=420).grid(row=1, column=0, sticky="w", pady=(10, 0))

    def do(action):
        T.save_json(REGLAGES, {"pose": pose.get(), "taille": size.get()})
        scale = {"Adulte": 1.0, "Grand": 1.12, "Enfant": 0.7}[size.get()]
        try:
            if action == "placer":
                root.withdraw()
                try:
                    status.set(place_people(pose.get(), scale))
                finally:
                    root.deiconify()
            else:
                status.set(delete_people())
        except Exception as error:  # noqa: BLE001
            root.deiconify()
            status.set(f"Erreur : {error}")

    b = ttk.Frame(f)
    b.grid(row=2, column=0, pady=(12, 0), sticky="e")
    ttk.Button(b, text="Supprimer les personnages", command=lambda: do("supprimer")).grid(row=0, column=0, padx=4)
    ttk.Button(b, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    ttk.Button(b, text="Placer des personnages...", command=lambda: do("placer")).grid(row=0, column=2, padx=4)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=T.PORT)
    args, _ = parser.parse_known_args()
    T.PORT = args.port
    window()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
