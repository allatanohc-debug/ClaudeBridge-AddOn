# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Claustra - murs de claustra en un geste - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\ (a cote de TableauSurfaces.py, qu'il utilise)
Puis dans Archicad : palette Tapir > "Claustra.py" > Executer.

1. Choisis un modele (ou un type) et regle-le : l'apercu montre le claustra en elevation.
2. « Dessiner le claustra... » : clique le point A, puis B (puis C, D... pour un mur qui tourne),
   Echap pour finir. Memes reperes que le tableau des surfaces (traits droits, longueurs).
   Ou : selectionne des murs / traits / polylignes et « Claustra sur la selection ».
3. Le claustra est construit en vrais elements 3D (morphes) groupes : plan, coupe, facade, 3D.
   « Modifier le claustra selectionne » le reconstruit avec les reglages actuels, meme trace.

Types : lames verticales (orientables), lames horizontales (brise-soleil, inclinables, montants),
grille de blocs ajoures, briques en quinconce, treillis en losange, motif personnalise (cases).
« Mes modeles » : enregistre tes reglages sous un nom pour les retrouver.
"""

import argparse
import json
import math
import sys
import time
import uuid
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

VERSION = "15/10 - 1re version"
MEMOIRE = "claustra.json"                 # claustras dessines : trace + reglages + guids
REGLAGES = "claustra_reglages.json"
MAX_SANS_CONFIRMER = 1500                 # au-dela : demander confirmation (beaucoup d'elements)

# ------------------------------------------------------------------- types ---
# (cle, libelle, valeur par defaut en cm ou degres, min, max) - les longueurs sont en cm dans la fenetre
COMMUN = [
    ("hauteur", "Hauteur", 250.0, 10, 2000),
    ("base", "Départ au-dessus du sol", 0.0, -500, 2000),
    ("epaisseur", "Épaisseur (profondeur)", 10.0, 1, 100),
]
TYPES = {
    "Lames verticales": [
        ("largeur", "Largeur d'une lame", 4.0, 0.5, 100),
        ("ecart", "Vide entre lames", 4.0, 0, 200),
        ("angle", "Orientation des lames (°)", 0.0, -90, 90),
    ],
    "Lames horizontales": [
        ("hauteur_lame", "Hauteur d'une lame", 4.0, 0.5, 100),
        ("ecart", "Vide entre lames", 4.0, 0, 200),
        ("inclinaison", "Inclinaison des lames (°)", 0.0, -80, 80),
        ("entraxe_montants", "Montants tous les (0 = sans)", 150.0, 0, 1000),
        ("section_montant", "Section des montants", 6.0, 1, 50),
    ],
    "Grille (blocs ajourés)": [
        ("module", "Taille d'un bloc", 20.0, 3, 200),
        ("paroi", "Épaisseur des parois", 3.0, 0.5, 50),
    ],
    "Briques en quinconce": [
        ("longueur", "Longueur d'une brique", 22.0, 3, 100),
        ("hauteur_brique", "Hauteur d'une brique", 11.0, 2, 100),
        ("vide", "Vide entre deux briques", 11.0, 0, 100),
        ("joint", "Joint horizontal", 1.0, 0, 50),
    ],
    "Treillis en losange": [
        ("barre", "Largeur des barres", 3.0, 0.5, 50),
        ("ecart", "Écart entre barres", 20.0, 2, 300),
        ("angle", "Angle (°)", 45.0, 15, 75),
    ],
    "Blocs décoratifs": [
        ("bloc", "Taille d'un bloc (carré)", 19.0, 5, 100),
        ("joint", "Joint de mortier", 1.0, 0, 5),
    ],
    "Motif personnalisé": [
        ("case_l", "Largeur d'une case", 10.0, 1, 200),
        ("case_h", "Hauteur d'une case", 10.0, 1, 200),
    ],
}
CADRE = [("cadre", "Largeur du cadre (0 = sans)", 0.0, 0, 50)]

# blocs claustra : forme des jours dans un bloc carre 0..1 (x vers la droite, z vers le haut)
BLOCS = ("Rond", "Petit rond", "Carré", "Carré à cadre", "Diagonale", "Traverse", "Feuille",
         "Étoile 4 branches", "Rotary (étoile + arcs)", "Fleur 4 pétales", "Cercles", "Losange")
POSES = ("Tous identiques", "Tournés en alternance (90°)", "Moulinet (4 rotations)", "Miroir en damier")
MOTIF_DEFAUT = ["XX.X", "X.XX", ".XX.", "XX.X"]       # X = plein, . = vide (motif personnalise)

MODELES_DEFAUT = {
    "Lames bois verticales 4 × 10": {"type": "Lames verticales", "largeur": 4, "ecart": 4, "epaisseur": 10, "hauteur": 250},
    "Lames orientées 45°": {"type": "Lames verticales", "largeur": 2, "ecart": 8, "epaisseur": 15, "angle": 45},
    "Brise-soleil horizontal": {"type": "Lames horizontales", "hauteur_lame": 3, "ecart": 7, "epaisseur": 15,
                                "inclinaison": 20, "entraxe_montants": 150, "section_montant": 6},
    "Claustra béton 20 × 20": {"type": "Grille (blocs ajourés)", "module": 20, "paroi": 3, "epaisseur": 10,
                               "cadre": 0},
    "Briques en quinconce": {"type": "Briques en quinconce", "longueur": 22, "hauteur_brique": 11, "vide": 11,
                             "joint": 1, "epaisseur": 11},
    "Bloc rond (Orb)": {"type": "Blocs décoratifs", "bloc": 19, "joint": 1, "epaisseur": 10, "bloc_forme": "Rond"},
    "Bloc feuille en moulinet (Palma)": {"type": "Blocs décoratifs", "bloc": 29, "joint": 1, "epaisseur": 10,
                                         "bloc_forme": "Feuille", "pose": "Moulinet (4 rotations)"},
    "Bloc diagonale alternée (Incline)": {"type": "Blocs décoratifs", "bloc": 29, "joint": 1, "epaisseur": 10,
                                          "bloc_forme": "Diagonale", "pose": "Tournés en alternance (90°)"},
    "Bloc étoile (Rotary)": {"type": "Blocs décoratifs", "bloc": 29, "joint": 1, "epaisseur": 10,
                             "bloc_forme": "Rotary (étoile + arcs)"},
    "Treillis losange": {"type": "Treillis en losange", "barre": 3, "ecart": 20, "angle": 45, "epaisseur": 4,
                         "cadre": 5},
}


def defaults(kind: str) -> dict:
    out = {"type": kind, "ajuster": True, "motif": list(MOTIF_DEFAUT), "bloc_forme": BLOCS[0], "pose": POSES[0]}
    for key, _l, val, _a, _b in COMMUN + TYPES[kind] + CADRE:
        out[key] = val
    return out


# --------------------------------------------------------------- geometrie ---
def box(origin, ax, ay, az, sx, sy, sz) -> dict:
    """Pave : coin d'origine + 3 axes (repere local du mur : x le long, y a travers, z vers le haut)."""
    return {"o": origin, "ax": ax, "ay": ay, "az": az, "s": (sx, sy, sz)}


def straight(x0, y0, z0, sx, sy, sz) -> dict:
    return box((x0, y0, z0), (1, 0, 0), (0, 1, 0), (0, 0, 1), sx, sy, sz)


def spread(length: float, item: float, gap: float, fit: bool) -> list[float]:
    """Positions (debut) d'elements de largeur `item` separes de `gap`, du debut a la fin du mur.
    fit : l'ecart est ajuste pour que le premier et le dernier element tombent pile aux extremites."""
    if length < item - 1e-9:
        return []
    pitch = item + gap
    if pitch <= 1e-9:
        return [0.0]
    n = int(math.floor((length - item) / pitch + 1e-9)) + 1
    if fit and n > 1:
        pitch = (length - item) / (n - 1)
    return [i * pitch for i in range(n)]


def clip_segment(p, d, xmin, xmax, zmin, zmax):
    """Partie de la droite p + t*d dans le rectangle (Liang-Barsky). Renvoie (t0, t1) ou None."""
    t0, t1 = -1e9, 1e9
    for pc, dc, lo, hi in ((p[0], d[0], xmin, xmax), (p[1], d[1], zmin, zmax)):
        if abs(dc) < 1e-12:
            if pc < lo or pc > hi:
                return None
            continue
        a, b = (lo - pc) / dc, (hi - pc) / dc
        t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
    return (t0, t1) if t1 - t0 > 1e-6 else None


# ----------------------------------------------------------- blocs percés ---
def _circle(cx, cz, r, n=24):
    return [(cx + r * math.cos(2 * math.pi * k / n), cz + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _arc(c, r, a0, a1, n):
    return [(c[0] + r * math.cos(a0 + (a1 - a0) * k / n), c[1] + r * math.sin(a0 + (a1 - a0) * k / n))
            for k in range(n + 1)]


def _lens(p, q, width, n=10):
    """Feuille (lentille) de p a q, de largeur max `width` (deux arcs de cercle)."""
    mx, mz = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
    L = math.dist(p, q)
    h = width / 2
    R = (L * L / 4 + h * h) / (2 * h)
    ux, uz = (q[0] - p[0]) / L, (q[1] - p[1]) / L
    nx, nz = -uz, ux
    pts = []
    for side, a, b in ((1, p, q), (-1, q, p)):
        bulge = (mx + side * nx * h, mz + side * nz * h)
        c = (mx - side * nx * (R - h), mz - side * nz * (R - h))
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        d = (math.atan2(b[1] - c[1], b[0] - c[0]) - a0 + math.pi) % (2 * math.pi) - math.pi
        am = (math.atan2(bulge[1] - c[1], bulge[0] - c[0]) - a0) % (2 * math.pi)
        inside = (0 <= am <= d) if d >= 0 else (am >= 2 * math.pi + d)
        if not inside:
            d = d - math.copysign(2 * math.pi, d)
        pts += [(c[0] + R * math.cos(a0 + d * k / n), c[1] + R * math.sin(a0 + d * k / n)) for k in range(n)]
    return pts


def block_holes(shape: str) -> list[list[tuple]]:
    """Jours d'un bloc carre unitaire (0..1)."""
    b = 0.11                                            # bord plein du bloc
    if shape == "Rond":
        return [_circle(0.5, 0.5, 0.36)]
    if shape == "Petit rond":
        return [_circle(0.5, 0.5, 0.2)]
    if shape == "Carré":
        return [[(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]]
    if shape == "Carré à cadre":
        return [[(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)]]
    if shape == "Diagonale":
        return [[(b + 0.05, b), (1 - b, b), (1 - b, 1 - b - 0.05)]]
    if shape == "Traverse":
        d = 0.14
        return [[(b, b + d), (1 - b - d, 1 - b), (b, 1 - b)], [(b + d, b), (1 - b, b), (1 - b, 1 - b - d)]]
    if shape == "Feuille":
        c = 0.26
        return [_lens((0.17, 0.17), (0.83, 0.83), 0.36),
                [(b, 1 - b - c), (b + c, 1 - b), (b, 1 - b)], [(1 - b - c, b), (1 - b, b), (1 - b, b + c)]]
    if shape in ("Étoile 4 branches", "_etoile_petite"):
        R = 0.53 if shape == "Étoile 4 branches" else 0.6   # arcs centres sur les coins
        corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
        tip = math.sqrt(R * R - 0.25)
        tips = [(0.5, tip), (1 - tip, 0.5), (0.5, 1 - tip), (tip, 0.5)]
        pts = []
        for i in range(4):
            c = corners[(i + 1) % 4]
            p, q = tips[i], tips[(i + 1) % 4]
            a0 = math.atan2(p[1] - c[1], p[0] - c[0])
            d = (math.atan2(q[1] - c[1], q[0] - c[0]) - a0 + math.pi) % (2 * math.pi) - math.pi
            pts += _arc(c, R, a0, a0 + d, 8)[:-1]
        return [pts]
    if shape == "Rotary (étoile + arcs)":
        out = block_holes("_etoile_petite")
        c, x0 = (0.5, -0.244), 0.2                     # arc : 11 cm de fleche, sous les pointes de l'etoile
        r = math.dist(c, (x0, b))
        a0 = math.atan2(b - c[1], x0 - c[0])
        sliver = _arc(c, r, a0, math.pi - a0, 10)              # arc au-dessus du bord bas
        sliver = [(x, max(z, b)) for x, z in sliver]
        cap = [(x, z) for x, z in sliver]
        for rot in range(4):
            out.append(_transform(cap[::-1] if rot % 2 else cap, rot, False))
        return out
    if shape == "Fleur 4 pétales":
        out = []
        for dx, dz in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
            p = (0.5 + dx * 0.06, 0.5 + dz * 0.06)
            q = (0.5 + dx * 0.36, 0.5 + dz * 0.36)
            out.append(_lens(p, q, 0.17, 8))
        return out
    if shape == "Cercles":
        return [_circle(0.3, 0.7, 0.15, 18), _circle(0.71, 0.7, 0.17, 18), _circle(0.29, 0.29, 0.16, 18),
                _circle(0.72, 0.3, 0.11, 16)]
    if shape == "Losange":
        return [[(0.5, 0.16), (0.84, 0.5), (0.5, 0.84), (0.16, 0.5)]]
    return [_circle(0.5, 0.5, 0.36)]


def _area(poly):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1])) / 2


def _transform(poly, rot: int, mirror: bool):
    out = []
    for x, z in poly:
        if mirror:
            x = 1 - x
        for _ in range(rot % 4):                        # quart de tour autour du centre
            x, z = 1 - z, x
        out.append((x, z))
    return out


def plate_body(w: float, h: float, t: float, holes: list[list[tuple]]) -> dict:
    """Corps de morphe : plaque w x h (x, z) d'epaisseur t (y) percee des jours donnes (coord. 0..1)."""
    outer = [(0, 0), (w, 0), (w, h), (0, h)]                        # sens trigo
    loops = [outer]
    for hole in holes:
        pts = [(x * w, z * h) for x, z in hole]
        if _area(pts) > 0:
            pts = pts[::-1]                                         # jours : sens horaire
        loops.append(pts)
    verts, front_ids, back_ids = [], [], []
    for loop in loops:
        f_ids, b_ids = [], []
        for x, z in loop:
            f_ids.append(len(verts))
            verts.append({"x": x, "y": 0.0, "z": z})
            b_ids.append(len(verts))
            verts.append({"x": x, "y": t, "z": z})
        front_ids.append(f_ids)
        back_ids.append(b_ids)
    polys = [{"vertexIds": front_ids[0], "holes": [{"vertexIds": l} for l in front_ids[1:]]},
             {"vertexIds": back_ids[0][::-1], "holes": [{"vertexIds": l[::-1]} for l in back_ids[1:]]}]
    for f_ids, b_ids in zip(front_ids, back_ids):                   # faces de cote (tour et jours)
        n = len(f_ids)
        for i in range(n):
            j = (i + 1) % n
            polys.append({"vertexIds": [f_ids[i], b_ids[i], b_ids[j], f_ids[j]]})
    return {"bodyType": "Solid", "vertices": verts, "polygons": polys}


def block_items(s: dict, x_lo: float, z_lo: float, W: float, Hz: float, t: float) -> list[dict]:
    cm = 0.01
    size, joint = s.get("bloc", 19.0) * cm, s.get("joint", 0.0) * cm
    fit = bool(s.get("ajuster", True))
    pitch = size + joint
    nx = max(1, round((W + joint) / pitch)) if fit else max(1, int((W + joint + 1e-9) // pitch))
    nz = max(1, round((Hz + joint) / pitch)) if fit else max(1, int((Hz + joint + 1e-9) // pitch))
    bw = (W - joint * (nx - 1)) / nx if fit else size
    bh = (Hz - joint * (nz - 1)) / nz if fit else size
    pose = s.get("pose", POSES[0])
    base_holes = block_holes(s.get("bloc_forme", BLOCS[0]))
    cache: dict = {}
    out = []
    for j in range(nz):
        for i in range(nx):
            if pose == POSES[1]:
                rot, mir = (i + j) % 2, False
            elif pose == POSES[2]:
                rot, mir = [0, 1, 3, 2][(i % 2) + 2 * (j % 2)], False
            elif pose == POSES[3]:
                rot, mir = 0, (i + j) % 2 == 1
            else:
                rot, mir = 0, False
            key = (rot, mir)
            if key not in cache:
                cache[key] = plate_body(bw, bh, t, [_transform(hh, rot, mir) for hh in base_holes])
            x, z = x_lo + i * (bw + joint), z_lo + j * (bh + joint)
            out.append({"o": (x, -t / 2, z), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
                        "s": (bw, t, bh), "body": cache[key], "jours": [_transform(hh, rot, mir) for hh in base_holes]})
    if joint > 0:                                                    # joints de mortier (pleins)
        used_w, used_h = nx * bw + (nx - 1) * joint, nz * bh + (nz - 1) * joint
        for i in range(1, nx):
            out.append(straight(x_lo + i * (bw + joint) - joint, -t / 2 + 0.005, z_lo, joint, t - 0.01, used_h))
        for j in range(1, nz):
            out.append(straight(x_lo, -t / 2 + 0.005, z_lo + j * (bh + joint) - joint, used_w, t - 0.01, joint))
    return out


def pattern_boxes(s: dict, L: float) -> list[dict]:
    """Paves d'un troncon de claustra de longueur L (repere local, metres)."""
    cm = 0.01
    H, t = s["hauteur"] * cm, s["epaisseur"] * cm
    f = max(0.0, s.get("cadre", 0.0)) * cm
    fit = bool(s.get("ajuster", True))
    y0 = -t / 2
    out: list[dict] = []
    # zone a remplir (a l'interieur du cadre)
    x_lo, x_hi, z_lo, z_hi = f, L - f, f, H - f
    W, Hz = x_hi - x_lo, z_hi - z_lo
    if W <= 0.005 or Hz <= 0.005:
        return [straight(0, y0, 0, L, t, H)] if L > 0 and H > 0 else []
    kind = s["type"]

    if kind == "Lames verticales":
        w, gap, ang = s["largeur"] * cm, s["ecart"] * cm, math.radians(s.get("angle", 0.0))
        c, sn = math.cos(ang), math.sin(ang)
        ax, ay = (c, sn, 0.0), (-sn, c, 0.0)
        # emprise en plan d'une lame tournee, pour rester dans le troncon
        foot = abs(w * c) + abs(t * sn)
        for x in spread(W, foot, max(0.0, gap - (foot - w)), fit):
            cx = x_lo + x + foot / 2
            o = (cx - ax[0] * w / 2 - ay[0] * t / 2, -ax[1] * w / 2 - ay[1] * t / 2, z_lo)
            out.append(box(o, ax, ay, (0, 0, 1), w, t, Hz))

    elif kind == "Lames horizontales":
        h, gap, inc = s["hauteur_lame"] * cm, s["ecart"] * cm, math.radians(s.get("inclinaison", 0.0))
        step = s.get("entraxe_montants", 0.0) * cm
        sec = s.get("section_montant", 6.0) * cm
        posts = []
        if step > 0:
            n = max(1, int(math.ceil((W - sec) / step)))
            pitch = (W - sec) / n
            posts = [x_lo + i * pitch for i in range(n + 1)]
            for x in posts:
                out.append(straight(x, -sec / 2, z_lo, sec, sec, Hz))
        c, sn = math.cos(inc), math.sin(inc)
        ay, az = (0.0, c, sn), (0.0, -sn, c)
        ext = abs(h * c) + abs(t * sn)                   # hauteur occupee par une lame inclinee
        for z in spread(Hz, ext, max(0.0, gap - (ext - h)), fit):
            cz = z_lo + z + ext / 2
            o = (x_lo, -ay[1] * t / 2 - az[1] * h / 2, cz - ay[2] * t / 2 - az[2] * h / 2)
            out.append(box(o, (1, 0, 0), ay, az, W, t, h))

    elif kind == "Grille (blocs ajourés)":
        m, e = s["module"] * cm, s["paroi"] * cm
        nx = max(1, round(W / m)) if fit else max(1, int(W // m))
        nz = max(1, round(Hz / m)) if fit else max(1, int(Hz // m))
        mx, mz = (W / nx, Hz / nz) if fit else (m, m)
        for i in range(nx + 1):                              # parois verticales (pleine hauteur)
            x = x_lo + min(i * mx, W - e) if i < nx else x_lo + min(nx * mx, W) - e
            out.append(straight(x, y0, z_lo, e, t, min(Hz, nz * mz)))
        for j in range(nz + 1):                              # parois horizontales (pleine longueur)
            z = z_lo + min(j * mz, Hz - e) if j < nz else z_lo + min(nz * mz, Hz) - e
            out.append(straight(x_lo, y0, z, min(W, nx * mx), t, e))

    elif kind == "Briques en quinconce":
        bl, bh = s["longueur"] * cm, s["hauteur_brique"] * cm
        gap, joint = s["vide"] * cm, s.get("joint", 0.0) * cm
        rows = max(1, int((Hz + joint + 1e-9) // (bh + joint)))
        pitch_z = (Hz - bh) / (rows - 1) if fit and rows > 1 else bh + joint
        pitch_x = bl + gap
        for j in range(rows):
            z = z_lo + j * pitch_z
            shift = (pitch_x / 2) if j % 2 else 0.0
            x = -shift
            while x < W - 1e-6:
                a, b = max(0.0, x), min(W, x + bl)
                if b - a > 0.01:                           # morceaux de brique aux bouts
                    out.append(straight(x_lo + a, y0, z, b - a, t, bh))
                x += pitch_x

    elif kind == "Treillis en losange":
        e, gap, ang = s["barre"] * cm, s["ecart"] * cm, math.radians(s.get("angle", 45.0))
        for sign in (1, -1):
            d = (math.cos(ang), sign * math.sin(ang))
            nrm = (-d[1], d[0])
            # droites paralleles espacees de `gap` (perpendiculairement), couvrant tout le rectangle
            corners = [(0, 0), (W, 0), (0, Hz), (W, Hz)]
            proj = [cx * nrm[0] + cz * nrm[1] for cx, cz in corners]
            k = math.floor(min(proj) / gap)
            while k * gap <= max(proj) + 1e-9:
                p = (nrm[0] * k * gap, nrm[1] * k * gap)
                seg = clip_segment(p, d, 0, W, 0, Hz)
                k += 1
                if not seg:
                    continue
                t0, t1 = seg
                start = (x_lo + p[0] + d[0] * t0, z_lo + p[1] + d[1] * t0)
                ax3, az3 = (d[0], 0.0, d[1]), (-d[1], 0.0, d[0])
                o = (start[0] - az3[0] * e / 2, y0, start[1] - az3[2] * e / 2)
                out.append(box(o, ax3, (0, 1, 0), az3, t1 - t0, t, e))

    elif kind == "Blocs décoratifs":
        out += block_items(s, x_lo, z_lo, W, Hz, t)

    elif kind == "Motif personnalisé":
        cw, ch = s["case_l"] * cm, s["case_h"] * cm
        motif = [r for r in (s.get("motif") or MOTIF_DEFAUT) if r] or MOTIF_DEFAUT
        cols = max(len(r) for r in motif)
        motif = [r.ljust(cols, ".") for r in motif]
        n_x, n_z = max(1, int(round(W / cw))), max(1, int(round(Hz / ch)))
        cw2, ch2 = (W / n_x, Hz / n_z) if fit else (cw, ch)
        if not fit:
            n_x, n_z = int(W // cw), int(Hz // ch)
        for j in range(n_z):
            row = motif[len(motif) - 1 - (j % len(motif))]      # la 1re ligne du motif est en haut
            i = 0
            while i < n_x:
                if row[i % cols] in "Xx#1":
                    k = i
                    while k + 1 < n_x and row[(k + 1) % cols] in "Xx#1":
                        k += 1
                    out.append(straight(x_lo + i * cw2, y0, z_lo + j * ch2, (k - i + 1) * cw2, t, ch2))
                    i = k + 1
                else:
                    i += 1

    if f > 0:                                                # cadre : traverses haute / basse, montants
        out.append(straight(0, y0, 0, L, t, f))
        out.append(straight(0, y0, H - f, L, t, f))
        out.append(straight(0, y0, f, f, t, H - 2 * f))
        out.append(straight(L - f, y0, f, f, t, H - 2 * f))
    return out


def world_boxes(points: list[tuple], s: dict, z0: float) -> list[dict]:
    """Paves de tout le trace (troncons A-B, B-C...) en coordonnees du plan. z0 = cote du depart."""
    out = []
    for a, b in zip(points, points[1:]):
        L = math.dist(a, b)
        if L < 0.02:
            continue
        u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        n = (-u[1], u[0])

        def w(v):
            return (v[0] * u[0] + v[1] * n[0], v[0] * u[1] + v[1] * n[1], v[2])

        for bx in pattern_boxes(s, L):
            o = bx["o"]
            wo = w(o)
            item = {"basePoint": {"x": a[0] + wo[0], "y": a[1] + wo[1], "z": z0 + wo[2]},
                    "xAxis": dict(zip("xyz", w(bx["ax"]))), "yAxis": dict(zip("xyz", w(bx["ay"]))),
                    "zAxis": dict(zip("xyz", w(bx["az"])))}
            if "body" in bx:
                item["body"] = bx["body"]
            else:
                item["size"] = {"x": bx["s"][0], "y": bx["s"][1], "z": bx["s"][2]}
            out.append(item)
    return out


def elevation_holes(s: dict, L: float) -> list[list[tuple]]:
    """Apercu : jours des blocs perces, en elevation."""
    out = []
    for bx in pattern_boxes(s, L):
        for hole in bx.get("jours", []):
            out.append([(bx["o"][0] + x * bx["s"][0], bx["o"][2] + z * bx["s"][2]) for x, z in hole])
    return out


def elevation_polys(s: dict, L: float) -> list[list[tuple]]:
    """Apercu : contour de chaque pave vu en elevation (x, z)."""
    polys = []
    for bx in pattern_boxes(s, L):
        o, ax, ay, az = bx["o"], bx["ax"], bx["ay"], bx["az"]
        sx, sy, sz = bx["s"]
        pts = []
        for i in (0, 1):
            for j in (0, 1):
                for k in (0, 1):
                    p = [o[c] + ax[c] * sx * i + ay[c] * sy * j + az[c] * sz * k for c in range(3)]
                    pts.append((p[0], p[2]))
        polys.append(_hull(pts))
    return polys


def plan_polys(s: dict, L: float) -> list[list[tuple]]:
    polys = []
    for bx in pattern_boxes(s, L):
        o, ax, ay, az = bx["o"], bx["ax"], bx["ay"], bx["az"]
        sx, sy, sz = bx["s"]
        pts = []
        for i in (0, 1):
            for j in (0, 1):
                for k in (0, 1):
                    p = [o[c] + ax[c] * sx * i + ay[c] * sy * j + az[c] * sz * k for c in range(3)]
                    pts.append((p[0], p[1]))
        polys.append(_hull(pts))
    return polys


def _hull(pts):
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in pts))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------- Archicad ---
def story_level(story: int) -> float:
    for s in tapir("GetStories").get("stories", []):
        if s.get("index") == story:
            return float(s.get("level", 0.0))
    return 0.0


def attributes(kind: str) -> list[tuple[str, dict]]:
    try:
        rows = tapir("GetAttributesByType", {"attributeType": kind}).get("attributes", [])
    except Exception:  # noqa: BLE001
        return []
    return [(r.get("name", ""), r["attributeId"]) for r in rows if "attributeId" in r]


def set_trace_pens() -> None:
    """Stylos du trace provisoire (memes que le tableau des surfaces)."""
    try:
        tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
        ids = [{"attributeId": t["attributeId"]} for t in tables if "attributeId" in t]
        details = [t for t in tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel"]}
                                    ).get("penTables", []) if "error" not in t] if ids else []
        table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
        if table:
            tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
                "attributeId": table["attributeId"], "name": table["name"], "pens": [
                    {"index": T.STYLO_CONTOUR, "width": 0.1, "description": "Tableau des surfaces - contour en cours",
                     "color": dict(zip(("red", "green", "blue"), T.COULEUR_CONTOUR))},
                    {"index": T.STYLO_REPERE, "width": 0.1, "description": "Tableau des surfaces - reperes",
                     "color": dict(zip(("red", "green", "blue"), T.COULEUR_REPERE))}]}]})
    except Exception:  # noqa: BLE001
        pass


def trace_path(story: int, step_deg: float, guides_on: bool, title: str):
    """Trace A -> B (-> C...) point par point, comme « Dessiner une piece » du tableau des surfaces :
    trait orange, reperes bleus, longueurs, traits droits ; clic sur le point precedent = annuler ;
    Echap ou 2e clic sur le dernier point = fin. Renvoie (points, nettoyer)."""
    temp, guides, labels = [], [], []
    close_dist = T.FERMETURE_MM * T.ECHELLE / 1000.0

    def delete(guids: list) -> None:
        if guids:
            try:
                tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in guids if g]})
            except Exception:  # noqa: BLE001
                pass
        guids.clear()

    def draw_temp(coords: list) -> None:
        try:
            answer = tapir("CreatePolylines", {"polylinesData": [{
                "floorInd": story, "coordinates": coords,
                "linePenIndex": T.STYLO_CONTOUR, "penWeightMm": T.TRAIT_CONTOUR_MM}]})
            temp.extend(e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e)
        except Exception:  # noqa: BLE001
            pass
        T.highlight(temp)

    def draw_guides(points: list) -> None:
        delete(guides)
        if not points or not guides_on:
            return
        reach = T.REPERE_M
        data = []
        for q in [points[-1]] + ([points[0]] if len(points) > 1 else []):
            data.append({"floorInd": story, "linePenIndex": T.STYLO_REPERE, "penWeightMm": T.TRAIT_REPERE_MM,
                         "coordinates": [{"x": q["x"] - reach, "y": q["y"]}, {"x": q["x"] + reach, "y": q["y"]}]})
            data.append({"floorInd": story, "linePenIndex": T.STYLO_REPERE, "penWeightMm": T.TRAIT_REPERE_MM,
                         "coordinates": [{"x": q["x"], "y": q["y"] - reach}, {"x": q["x"], "y": q["y"] + reach}]})
        try:
            answer = tapir("CreatePolylines", {"polylinesData": data})
            guides.extend(e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e)
        except Exception:  # noqa: BLE001
            pass

    def draw_length(a: dict, b: dict) -> None:
        if not guides_on:
            labels.append(None)
            return
        length = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
        mid = {"x": (a["x"] + b["x"]) / 2, "y": (a["y"] + b["y"]) / 2}
        off = 2.5 * T.ECHELLE / 1000.0
        if length > 1e-9:
            mid = {"x": mid["x"] - (b["y"] - a["y"]) / length * off, "y": mid["y"] + (b["x"] - a["x"]) / length * off}
        try:
            answer = tapir("CreateTexts", {"textsData": [{
                "coordinate": {"x": mid["x"], "y": mid["y"], "z": 0.0}, "text": T.format_area(length),
                "floorIndex": story, "style": {"height": 2.5, "bold": True, "penIndex": T.STYLO_CONTOUR,
                                               "anchor": "MiddleMiddle", "justification": "Center"}}]})
            items = [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
            labels.append(items[0] if items else None)
        except Exception:  # noqa: BLE001
            labels.append(None)

    def cleanup() -> None:
        T.highlight([])
        delete(temp)
        delete([g for g in labels if g])
        labels.clear()
        delete(guides)

    points: list[dict] = []
    while True:
        T.focus_archicad()
        prompt = (f"{title} : point {len(points) + 1} (Echap = terminer, point precedent = annuler)" if points
                  else f"{title} : point de depart A (Echap = annuler)")
        point, _ = T.pick_point(prompt)
        if point is None:
            break
        here = (point["x"], point["y"])
        if points and math.dist(here, (points[-1]["x"], points[-1]["y"])) <= close_dist and len(points) >= 2:
            break                                          # 2e clic sur le dernier point : fin
        if len(points) >= 2 and math.dist(here, (points[-2]["x"], points[-2]["y"])) <= close_dist:
            points.pop()                                   # clic sur l'avant-dernier point : annule le dernier
            if len(temp) > 1:
                delete([temp.pop()])
                T.highlight(temp)
            if labels:
                label = labels.pop()
                if label:
                    delete([label])
            draw_guides(points)
            continue
        if points:
            point = T.constrain(points[-1], point, step_deg)
            if math.dist((point["x"], point["y"]), (points[-1]["x"], points[-1]["y"])) < 1e-4:
                continue
        else:
            r = T.MARQUE_MM * T.ECHELLE / 1000.0 / 2
            draw_temp([{"x": point["x"] - r, "y": point["y"] - r}, {"x": point["x"] + r, "y": point["y"] - r},
                       {"x": point["x"] + r, "y": point["y"] + r}, {"x": point["x"] - r, "y": point["y"] + r},
                       {"x": point["x"] - r, "y": point["y"] - r}])
        if points:
            draw_temp([points[-1], point])
            draw_length(points[-1], point)
        points.append(point)
        draw_guides(points)
    delete(guides)
    if len(points) < 2:
        cleanup()
        return None, cleanup
    return [(p["x"], p["y"]) for p in points], cleanup


def selected_paths() -> tuple[list[dict], list[str]]:
    """Traces depuis la selection : murs (axe), traits, polylignes. Renvoie ([{points, story}], murs)."""
    try:
        sel = api("API.GetSelectedElements").get("elements", [])
    except Exception:  # noqa: BLE001
        sel = tapir("GetSelectedElements").get("elements", [])
    sel = [e for e in sel if isinstance(e, dict) and "elementId" in e]
    if not sel:
        return [], []
    own = {g for c in load_json(MEMOIRE).get("claustras", []) for g in c.get("guids", [])}
    sel = [e for e in sel if e["elementId"]["guid"] not in own]
    details = []
    for i in range(0, len(sel), 150):
        part = sel[i:i + 150]
        rows = tapir("GetDetailsOfElements", {"elements": part, "fields": ["type", "floorIndex", "details",
                                                                           "floorPlanPolygons"]}
                     ).get("detailsOfElements", [])
        for e, r in zip(part, rows):
            if isinstance(r, dict) and "error" not in r:
                r["guid"] = e["elementId"]["guid"]
                details.append(r)
    paths, walls = [], []
    for r in details:
        d, kind, story = r.get("details", {}), r.get("type"), r.get("floorIndex", 0)
        if kind == "Wall":
            axis = wall_centerline(r)
            if axis:
                paths.append({"points": axis[0], "story": story, "hauteur": axis[1], "epaisseur": axis[2],
                              "base": axis[3]})
                walls.append(r["guid"])
        elif kind == "Line" and d.get("begCoordinate") and d.get("endCoordinate"):
            paths.append({"points": [(d["begCoordinate"]["x"], d["begCoordinate"]["y"]),
                                     (d["endCoordinate"]["x"], d["endCoordinate"]["y"])], "story": story})
        elif kind == "PolyLine" and len(d.get("coordinates", [])) >= 2:
            paths.append({"points": [(c["x"], c["y"]) for c in d["coordinates"]], "story": story})
    return paths, walls


def wall_centerline(row: dict):
    """(points de l'axe, hauteur, epaisseur, decalage bas) d'un mur droit, mesures sur son dessin en plan."""
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
    pts = [(c["x"], c["y"]) for p in (row.get("floorPlanPolygons") or []) for c in p.get("coordinates", [])]
    if pts:
        ts = [(x - beg["x"]) * nx + (y - beg["y"]) * ny for x, y in pts]
        t1, t2 = min(ts), max(ts)
    else:
        th = float(d.get("begThickness", 0.2) or 0.2)
        t1, t2 = 0.0, th
    mid = (t1 + t2) / 2
    a = (beg["x"] + nx * mid, beg["y"] + ny * mid)
    b = (end["x"] + nx * mid, end["y"] + ny * mid)
    return [a, b], float(d.get("height", 2.5) or 2.5), max(0.01, t2 - t1), float(d.get("bottomOffset", 0.0) or 0.0)


def resolve(name: str, kind: str):
    if not name:
        return None
    return next((aid for n, aid in attributes(kind) if n == name), None)


def build(points: list[tuple], s: dict, story: int) -> tuple[list[str], int]:
    """Cree les morphes du claustra, les groupe. Renvoie (guids, nombre demande)."""
    z0 = story_level(story) + s.get("base", 0.0) * 0.01
    boxes = world_boxes(points, s, z0)
    mat = resolve(s.get("materiau", ""), "BuildingMaterial")
    surf = resolve(s.get("surface", ""), "Surface")
    for b in boxes:
        b["floorIndex"] = story
        if mat:
            b["buildingMaterialId"] = mat
        if surf:
            b["surfaceId"] = surf
    guids = []
    for i in range(0, len(boxes), 200):
        made = tapir("CreateMorphs", {"morphsData": boxes[i:i + 200]}, timeout=600).get("elements", [])
        guids += [e["elementId"]["guid"] for e in made if isinstance(e, dict) and "elementId" in e]
    if len(guids) > 1:
        try:
            tapir("CreateGroups", {"elementGroups": [{"elements": [{"elementId": {"guid": g}} for g in guids]}]})
        except Exception:  # noqa: BLE001
            pass
    return guids, len(boxes)


def remember(points, s, story, guids, sur_mur=False) -> str:
    memory = load_json(MEMOIRE)
    cid = uuid.uuid4().hex[:8]
    memory.setdefault("claustras", []).append({"id": cid, "story": story, "points": points,
                                               "reglages": s, "guids": guids, "sur_mur": sur_mur,
                                               "date": time.strftime("%d/%m %H:%M")})
    memory["dernier"] = cid
    save_json(MEMOIRE, memory)
    return cid


def delete_claustra(cid: str) -> int:
    memory = load_json(MEMOIRE)
    items = memory.get("claustras", [])
    found = next((c for c in items if c["id"] == cid), None)
    if not found:
        return 0
    try:
        tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in found["guids"]]})
    except Exception:  # noqa: BLE001
        pass
    memory["claustras"] = [c for c in items if c["id"] != cid]
    if memory.get("dernier") == cid:
        memory["dernier"] = memory["claustras"][-1]["id"] if memory["claustras"] else None
    save_json(MEMOIRE, memory)
    return len(found["guids"])


def selected_claustras() -> list[dict]:
    try:
        sel = api("API.GetSelectedElements").get("elements", [])
    except Exception:  # noqa: BLE001
        sel = tapir("GetSelectedElements").get("elements", [])
    guids = {e["elementId"]["guid"] for e in sel if isinstance(e, dict) and "elementId" in e}
    return [c for c in load_json(MEMOIRE).get("claustras", []) if guids & set(c.get("guids", []))]


def describe(s: dict, points, count: int) -> str:
    length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    return (f"{s['type']} : {length:.2f} m × {s['hauteur'] / 100:.2f} m, {count} élément(s)"
            .replace(".", ",", 2))


# ------------------------------------------------------------------ fenetre ---
def load_settings() -> dict:
    saved = load_json(REGLAGES)
    kind = saved.get("type") if saved.get("type") in TYPES else "Lames verticales"
    current = defaults(kind)
    current.update({k: v for k, v in (saved.get("courant") or {}).items() if k in current or k in
                    ("materiau", "surface")})
    current["type"] = kind
    models = dict(MODELES_DEFAUT)
    models.update(saved.get("modeles") or {})
    return {"courant": current, "modeles": models, "traits": saved.get("traits", "Droits (0 / 90 degres)"),
            "reperes": saved.get("reperes", True), "remplacer_murs": saved.get("remplacer_murs", False)}


def window(state: dict) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(f"Claustra - version {VERSION}")
    root.attributes("-topmost", True)
    outer = ttk.Frame(root, padding=12)
    outer.grid()
    cur = state["courant"]
    status = tk.StringVar(value="Choisis un modèle ou règle ton claustra, puis « Dessiner le claustra ».")

    # --- modeles
    top = ttk.Frame(outer)
    top.grid(row=0, column=0, columnspan=2, sticky="ew")
    ttk.Label(top, text="Mes modèles", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    model_var = tk.StringVar(value="")
    model_box = ttk.Combobox(top, values=list(state["modeles"]), textvariable=model_var, state="readonly", width=30)
    model_box.grid(row=0, column=1, padx=6)
    ttk.Button(top, text="Enregistrer comme modèle...", command=lambda: save_model()).grid(row=0, column=2, padx=4)
    ttk.Button(top, text="Supprimer", command=lambda: delete_model()).grid(row=0, column=3, padx=4)

    # --- reglages (gauche)
    left = ttk.Frame(outer)
    left.grid(row=1, column=0, sticky="nw", pady=(8, 0))
    ttk.Label(left, text="Type de claustra", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    type_var = tk.StringVar(value=cur["type"])
    type_box = ttk.Combobox(left, values=list(TYPES), textvariable=type_var, state="readonly", width=26)
    type_box.grid(row=0, column=1, sticky="w", padx=6)
    params = ttk.LabelFrame(left, text="Dimensions (cm)", padding=8)
    params.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    vars_: dict = {}
    fit_var = tk.BooleanVar(value=cur.get("ajuster", True))
    shape_var = tk.StringVar(value=cur.get("bloc_forme", BLOCS[0]) if cur.get("bloc_forme") in BLOCS else BLOCS[0])
    pose_var = tk.StringVar(value=cur.get("pose", POSES[0]) if cur.get("pose") in POSES else POSES[0])
    motif_frame = ttk.LabelFrame(left, text="Motif (clique les cases : plein / vide)", padding=8)

    mat_box = ttk.LabelFrame(left, text="Matière", padding=8)
    mat_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    materials = [n for n, _a in attributes("BuildingMaterial")]
    surfaces = [n for n, _a in attributes("Surface")]
    mat_var = tk.StringVar(value=cur.get("materiau", ""))
    surf_var = tk.StringVar(value=cur.get("surface", ""))
    ttk.Label(mat_box, text="Matériau de construction").grid(row=0, column=0, sticky="w")
    ttk.Combobox(mat_box, values=[""] + materials, textvariable=mat_var, width=30).grid(row=0, column=1, padx=6)
    ttk.Label(mat_box, text="Surface (rendu)").grid(row=1, column=0, sticky="w", pady=(4, 0))
    ttk.Combobox(mat_box, values=[""] + surfaces, textvariable=surf_var, width=30).grid(row=1, column=1, padx=6,
                                                                                      pady=(4, 0))
    ttk.Label(mat_box, text="(vide = réglages par défaut de l'outil Morphe)", foreground="#777"
              ).grid(row=2, column=0, columnspan=2, sticky="w")

    # --- apercu (droite)
    right = ttk.Frame(outer)
    right.grid(row=1, column=1, sticky="n", padx=(12, 0), pady=(8, 0))
    ttk.Label(right, text="Aperçu (élévation et plan d'un morceau de 2 m)", foreground="#555").grid(row=0, column=0)
    canvas = tk.Canvas(right, width=380, height=300, background="white", highlightthickness=1,
                       highlightbackground="#ccc")
    canvas.grid(row=1, column=0)
    plan_canvas = tk.Canvas(right, width=380, height=70, background="white", highlightthickness=1,
                            highlightbackground="#ccc")
    plan_canvas.grid(row=2, column=0, pady=(4, 0))
    info_var = tk.StringVar(value="")
    ttk.Label(right, textvariable=info_var, foreground="#555").grid(row=3, column=0, sticky="w")

    # --- trace
    draw_box = ttk.LabelFrame(outer, text="Dessiner", padding=8)
    draw_box.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Label(draw_box, text="Traits").grid(row=0, column=0, sticky="w")
    lines_var = tk.StringVar(value=state["traits"] if state["traits"] in T.TRAITS else "Droits (0 / 90 degres)")
    ttk.Combobox(draw_box, values=list(T.TRAITS), textvariable=lines_var, state="readonly", width=24
                 ).grid(row=0, column=1, padx=6, sticky="w")
    guides_var = tk.BooleanVar(value=state["reperes"])
    ttk.Checkbutton(draw_box, text="Repères (alignements + longueurs)", variable=guides_var
                    ).grid(row=0, column=2, sticky="w")
    replace_var = tk.BooleanVar(value=state["remplacer_murs"])
    ttk.Checkbutton(draw_box, text="Sur des murs sélectionnés : supprimer le mur d'origine", variable=replace_var
                    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
    ttk.Label(draw_box, text="Clique A puis B (puis C, D... si le mur tourne). Echap ou 2e clic sur le dernier point "
                             "= terminer. Clic sur le point précédent = annuler le dernier trait.",
              foreground="#555", wraplength=720).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

    report = tk.Text(outer, width=96, height=5, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=3, column=0, columnspan=2, pady=(8, 0))
    ttk.Label(outer, textvariable=status, foreground="#555").grid(row=4, column=0, columnspan=2, sticky="w")

    def show(text):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", text)
        report.configure(state="disabled")

    # -------------------------------------------------- champs du type choisi
    motif_cells: list = []
    motif_size = {"cols": 4, "rows": 4}

    def build_params(*_):
        for child in params.winfo_children():
            child.destroy()
        vars_.clear()
        kind = type_var.get()
        base = defaults(kind)
        rows = COMMUN + TYPES[kind] + CADRE
        for i, (key, label, val, lo, hi) in enumerate(rows):
            ttk.Label(params, text=label).grid(row=i, column=0, sticky="w", pady=1)
            v = tk.DoubleVar(value=float(cur.get(key, base.get(key, val))))
            vars_[key] = v
            sp = ttk.Spinbox(params, from_=lo, to=hi, increment=1 if hi > 20 else 0.5, textvariable=v, width=8,
                             command=redraw)
            sp.grid(row=i, column=1, padx=6, sticky="w")
            sp.bind("<KeyRelease>", lambda _e: root.after(300, redraw))
        ttk.Checkbutton(params, text="Ajuster l'espacement pour finir pile aux extrémités", variable=fit_var,
                        command=redraw).grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(4, 0))
        if kind == "Blocs décoratifs":
            r0 = len(rows) + 1
            ttk.Label(params, text="Dessin du bloc").grid(row=r0, column=0, sticky="w", pady=(6, 0))
            fb = ttk.Combobox(params, values=BLOCS, textvariable=shape_var, state="readonly", width=24)
            fb.grid(row=r0, column=1, sticky="w", padx=6, pady=(6, 0))
            fb.bind("<<ComboboxSelected>>", redraw)
            ttk.Label(params, text="Pose").grid(row=r0 + 1, column=0, sticky="w", pady=(4, 0))
            fp = ttk.Combobox(params, values=POSES, textvariable=pose_var, state="readonly", width=24)
            fp.grid(row=r0 + 1, column=1, sticky="w", padx=6, pady=(4, 0))
            fp.bind("<<ComboboxSelected>>", redraw)
        if kind == "Motif personnalisé":
            motif_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
            build_motif(cur.get("motif") or MOTIF_DEFAUT)
        else:
            motif_frame.grid_forget()
        redraw()

    def build_motif(rows_):
        for child in motif_frame.winfo_children():
            child.destroy()
        motif_cells.clear()
        motif_size["rows"], motif_size["cols"] = len(rows_), max(len(r) for r in rows_)
        grid = ttk.Frame(motif_frame)
        grid.grid(row=0, column=0, rowspan=3)
        for j, r in enumerate(rows_):
            line = []
            for i in range(motif_size["cols"]):
                full = i < len(r) and r[i] in "Xx#1"
                cell = tk.Label(grid, width=2, height=1, relief="solid", borderwidth=1,
                                background="#555" if full else "white")
                cell.grid(row=j, column=i)
                cell.bind("<Button-1>", lambda _e, c=cell: (c.configure(
                    background="white" if c.cget("background") == "#555" else "#555"), redraw()))
                line.append(cell)
            motif_cells.append(line)
        ctrl = ttk.Frame(motif_frame)
        ctrl.grid(row=0, column=1, padx=10, sticky="n")
        ttk.Label(ctrl, text="Colonnes").grid(row=0, column=0, sticky="w")
        cols_v = tk.IntVar(value=motif_size["cols"])
        rows_v = tk.IntVar(value=motif_size["rows"])
        ttk.Spinbox(ctrl, from_=1, to=12, textvariable=cols_v, width=4,
                    command=lambda: resize_motif(rows_v.get(), cols_v.get())).grid(row=0, column=1)
        ttk.Label(ctrl, text="Lignes").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(ctrl, from_=1, to=12, textvariable=rows_v, width=4,
                    command=lambda: resize_motif(rows_v.get(), cols_v.get())).grid(row=1, column=1)

    def motif_rows() -> list[str]:
        return ["".join("X" if c.cget("background") == "#555" else "." for c in line) for line in motif_cells] \
            or list(MOTIF_DEFAUT)

    def resize_motif(rows_n, cols_n):
        old = motif_rows()
        new = []
        for j in range(max(1, rows_n)):
            r = old[j] if j < len(old) else ""
            new.append((r + "." * cols_n)[:max(1, cols_n)])
        build_motif(new)
        redraw()

    def current() -> dict:
        s = dict(defaults(type_var.get()))
        for k, v in vars_.items():
            try:
                s[k] = float(v.get())
            except (tk.TclError, ValueError):
                pass
        s["ajuster"] = bool(fit_var.get())
        s["bloc_forme"], s["pose"] = shape_var.get(), pose_var.get()
        s["motif"] = motif_rows() if motif_cells else cur.get("motif", MOTIF_DEFAUT)
        s["materiau"], s["surface"] = mat_var.get(), surf_var.get()
        return s

    def redraw(*_):
        try:
            s = current()
        except Exception:  # noqa: BLE001
            return
        L, H = 2.0, max(0.1, s["hauteur"] / 100)
        canvas.delete("all")
        plan_canvas.delete("all")
        k = min(360 / L, 280 / H)
        ox, oy = (380 - L * k) / 2, 290

        def ev(p):
            return ox + p[0] * k, oy - p[1] * k
        polys = elevation_polys(s, L)
        canvas.create_rectangle(ox, oy - H * k, ox + L * k, oy, outline="#ddd", dash=(3, 3))
        for poly in polys:
            if len(poly) >= 3:
                canvas.create_polygon([v for p in poly for v in ev(p)], fill="#b9a88f", outline="#5b4a33")
        for hole in elevation_holes(s, L):
            canvas.create_polygon([v for p in hole for v in ev(p)], fill="white", outline="#5b4a33")
        t = s["epaisseur"] / 100
        kp = min(360 / L, 50 / max(t, 0.05))
        for poly in plan_polys(s, L):
            if len(poly) >= 3:
                plan_canvas.create_polygon([v for p in poly for c in [((380 - L * kp) / 2 + p[0] * kp,
                                                                         35 - p[1] * kp)] for v in c],
                                           fill="#8a7a63", outline="#3d3020")
        per_m = len(polys) / L
        info_var.set(f"{len(polys)} élément(s) sur 2 m (≈ {per_m:.0f} par mètre de claustra)")

    type_box.bind("<<ComboboxSelected>>", lambda _e: (cur.update(defaults(type_var.get())), build_params()))
    build_params()

    # ----------------------------------------------------------------- modeles
    def apply_model(*_):
        model = state["modeles"].get(model_var.get())
        if not model:
            return
        kind = model.get("type", "Lames verticales")
        cur.clear()
        cur.update(defaults(kind))
        cur.update(model)
        type_var.set(kind)
        fit_var.set(cur.get("ajuster", True))
        mat_var.set(cur.get("materiau", ""))
        surf_var.set(cur.get("surface", ""))
        shape_var.set(cur.get("bloc_forme", BLOCS[0]) if cur.get("bloc_forme") in BLOCS else BLOCS[0])
        pose_var.set(cur.get("pose", POSES[0]) if cur.get("pose") in POSES else POSES[0])
        build_params()
    model_box.bind("<<ComboboxSelected>>", apply_model)

    def save_model():
        from tkinter import simpledialog
        name = simpledialog.askstring("Enregistrer le modèle", "Nom du modèle :", parent=root,
                                      initialvalue=model_var.get() or type_var.get())
        if not name:
            return
        state["modeles"][name.strip()] = current()
        model_box.configure(values=list(state["modeles"]))
        model_var.set(name.strip())
        persist()
        status.set(f"Modèle « {name.strip()} » enregistré.")

    def delete_model():
        name = model_var.get()
        if name in state["modeles"]:
            del state["modeles"][name]
            model_box.configure(values=list(state["modeles"]))
            model_var.set("")
            persist()

    def persist():
        own = {k: v for k, v in state["modeles"].items() if MODELES_DEFAUT.get(k) != v}
        save_json(REGLAGES, {"type": type_var.get(), "courant": current(), "modeles": own,
                             "traits": lines_var.get(), "reperes": bool(guides_var.get()),
                             "remplacer_murs": bool(replace_var.get())})

    # ----------------------------------------------------------------- actions
    def confirm_count(s, points) -> bool:
        count = len(world_boxes(points, s, 0.0))
        if count <= MAX_SANS_CONFIRMER:
            return True
        return messagebox.askyesno("Beaucoup d'éléments",
                                   f"Ce claustra fait {count} éléments 3D. Archicad peut mettre un moment.\n"
                                   "Continuer ?", parent=root)

    def make(points, s, story, sur_mur=False) -> str:
        if not confirm_count(s, points):
            return "Annulé."
        guids, wanted = build(points, s, story)
        if not guids:
            return "Aucun élément créé (Archicad a refusé les morphes)."
        remember(points, s, story, guids, sur_mur)
        text = describe(s, points, len(guids))
        if len(guids) < wanted:
            text += f" ({wanted - len(guids)} refusé(s) par Archicad)"
        return text

    def draw():
        s = current()
        persist()
        story = tapir("GetStories").get("actStory", 0)
        set_trace_pens()
        results = []
        root.withdraw()
        try:
            while True:
                points, cleanup = trace_path(story, T.TRAITS.get(lines_var.get(), 90.0), bool(guides_var.get()),
                                             f"Claustra {len(results) + 1}")
                if points is None:
                    break
                cleanup()
                results.append(make(points, s, story))
        finally:
            root.deiconify()
        show("\n".join(results) or "Aucun tracé.")

    def on_selection():
        s = current()
        persist()
        paths, walls = selected_paths()
        if not paths:
            show("Sélectionne d'abord des murs, des traits ou des polylignes dans Archicad.")
            return
        results = []
        for p in paths:
            s2 = dict(s)
            if "hauteur" in p:                       # sur un mur : sa hauteur, son epaisseur, son depart
                s2["hauteur"], s2["base"] = p["hauteur"] * 100, p["base"] * 100
                s2["epaisseur"] = p["epaisseur"] * 100
            results.append(make(p["points"], s2, p["story"], "hauteur" in p))
        if walls and replace_var.get():
            try:
                tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in walls]})
                results.append(f"{len(walls)} mur(s) d'origine supprimé(s).")
            except Exception as error:  # noqa: BLE001
                results.append(f"Murs non supprimés : {error}")
        show("\n".join(results))

    def modify():
        found = selected_claustras()
        if not found:
            show("Sélectionne un élément d'un claustra dessiné par cette extension, puis recommence.")
            return
        s = current()
        persist()
        results = []
        for c in found:
            delete_claustra(c["id"])
            s2 = dict(s)
            old = c.get("reglages", {})
            if c.get("sur_mur"):
                s2.update({k: old[k] for k in ("hauteur", "base", "epaisseur") if k in old})
            results.append("Modifié : " + make([tuple(p) for p in c["points"]], s2, c["story"], c.get("sur_mur", False)))
        show("\n".join(results))

    def load_selected():
        found = selected_claustras()
        if not found:
            show("Sélectionne un élément d'un claustra pour reprendre ses réglages.")
            return
        s = found[0].get("reglages", {})
        kind = s.get("type", "Lames verticales")
        cur.clear()
        cur.update(defaults(kind))
        cur.update(s)
        type_var.set(kind)
        fit_var.set(cur.get("ajuster", True))
        mat_var.set(cur.get("materiau", ""))
        surf_var.set(cur.get("surface", ""))
        shape_var.set(cur.get("bloc_forme", BLOCS[0]) if cur.get("bloc_forme") in BLOCS else BLOCS[0])
        pose_var.set(cur.get("pose", POSES[0]) if cur.get("pose") in POSES else POSES[0])
        build_params()
        show(f"Réglages repris : {kind}. Change-les puis « Modifier le claustra sélectionné ».")

    def undo():
        memory = load_json(MEMOIRE)
        cid = memory.get("dernier")
        n = delete_claustra(cid) if cid else 0
        show(f"Dernier claustra effacé ({n} élément(s))." if n else "Rien à annuler.")

    def do(fn):
        status.set("En cours...")
        root.update_idletasks()
        try:
            fn()
            status.set("Terminé. Chaque claustra est un groupe (déverrouille le groupe pour toucher une lame).")
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")

    buttons = ttk.Frame(outer)
    buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(8, 0))
    ttk.Button(buttons, text="Annuler le dernier", command=lambda: do(undo)).grid(row=0, column=0, padx=3)
    ttk.Button(buttons, text="Reprendre les réglages du sélectionné", command=lambda: do(load_selected)
               ).grid(row=0, column=1, padx=3)
    ttk.Button(buttons, text="Modifier le claustra sélectionné", command=lambda: do(modify)).grid(row=0, column=2, padx=3)
    ttk.Button(buttons, text="Claustra sur la sélection", command=lambda: do(on_selection)).grid(row=0, column=3, padx=3)
    ttk.Button(buttons, text="Dessiner le claustra...", command=lambda: do(draw)).grid(row=0, column=4, padx=3)
    root.protocol("WM_DELETE_WINDOW", lambda: (persist(), root.destroy()))
    root.bind("<Escape>", lambda _e: (persist(), root.destroy()))
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
