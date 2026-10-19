# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
V-Ray Link - rendre un projet Archicad avec V-Ray, sans SketchUp - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\ (a cote de TableauSurfaces.py, qu'il utilise)
Puis dans Archicad : palette Tapir > "VRayLink.py" > Executer.

Etape 1 :
- la geometrie du projet (export IFC automatique, converti pour V-Ray, surface par surface) ;
- un materiau V-Ray par surface Archicad (couleur d'Archicad + reflets/verre/metal devines d'apres le nom) ;
- les cameras : vues automatiques (aerienne, facades) + cameras placees en 2 clics dans le plan ;
- le soleil reel (lieu du projet, date, heure) et le ciel physique ;
- « Rendre » : l'image se calcule dans la fenetre V-Ray (V-Ray Frame Buffer) et s'enregistre.

Utilise vray.exe installe avec V-Ray pour SketchUp (C:\\Program Files\\Chaos\\...).
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pickle
import re
import ssl
import struct
import subprocess
import sys
import threading
import time
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

VERSION = "19/10 - v2.0c (matériaux Cosmos)"
REGLAGES = "vray_link.json"
ABIDJAN = (5.36, -4.01)                    # lieu par defaut si le projet n'en a pas
VILLES = {                                 # (latitude, longitude, fuseau horaire)
    "Abidjan": (5.36, -4.01, 0.0), "Grand-Bassam": (5.21, -3.74, 0.0), "Assinie": (5.13, -3.28, 0.0),
    "Yamoussoukro": (6.82, -5.28, 0.0), "Bouaké": (7.69, -5.03, 0.0), "San-Pédro": (4.75, -6.64, 0.0),
    "Korhogo": (9.46, -5.63, 0.0), "Daloa": (6.88, -6.45, 0.0), "Dakar": (14.69, -17.44, 0.0),
    "Lomé": (6.13, 1.22, 0.0), "Accra": (5.60, -0.19, 0.0), "Ouagadougou": (12.37, -1.52, 0.0),
    "Paris": (48.86, 2.35, 1.0),
}
LIEU_ARCHICAD = "Lieu du projet Archicad"

QUALITES = {                               # minutes max, seuil de bruit
    "Brouillon": (1.0, 0.05), "Moyenne": (3.0, 0.02), "Haute": (8.0, 0.01), "Finale": (20.0, 0.005),
}
TAILLES = {"1280 × 720 (16:9)": (1280, 720), "1920 × 1080 (16:9)": (1920, 1080), "2560 × 1440 (16:9)": (2560, 1440),
           "3840 × 2160 (4K)": (3840, 2160), "1600 × 1200 (4:3)": (1600, 1200), "1080 × 1350 (Instagram)": (1080, 1350),
           "1080 × 1920 (story)": (1080, 1920)}
AMBIANCES = {                              # f/, 1/vitesse, ISO
    "Extérieur jour": (8.0, 250.0, 100.0),
    "Intérieur lumineux": (8.0, 30.0, 400.0),
    "Intérieur sombre": (4.0, 30.0, 800.0),
    "Nuit": (4.0, 8.0, 1600.0),
}
SOLS = {"Gazon": (0.26, 0.36, 0.16), "Latérite (terre rouge)": (0.55, 0.32, 0.20), "Béton gris": (0.55, 0.55, 0.53),
        "Sable": (0.78, 0.70, 0.55), "Aucun": None}
FOCALES = (14, 18, 24, 28, 35, 50)

# reglages de materiau devines d'apres le nom de la surface Archicad
FAMILLES = [
    (("verre", "vitr", "glass", "glazing", "miroir"), {"type": "verre"}),
    (("eau", "water", "piscine", "pool"), {"type": "eau"}),
    (("chrome", "inox", "miroir"), {"reflect": 0.9, "gloss": 0.95, "metal": 1.0}),
    (("métal", "metal", "alu", "acier", "steel", "zinc", "cuivre", "laiton", "fer "), {"reflect": 0.8, "gloss": 0.7, "metal": 1.0}),
    (("carrel", "céram", "ceram", "faïence", "faience", "marbre", "granit", "tile", "grès", "gres"), {"reflect": 0.6, "gloss": 0.85}),
    (("laqu", "vernis", "brillant", "gloss"), {"reflect": 0.6, "gloss": 0.8}),
    (("bois", "parquet", "wood", "iroko", "teck", "chêne", "chene", "oak", "walnut", "timber"),
     {"reflect": 0.35, "gloss": 0.62, "teinte": (0.50, 0.33, 0.20)}),
    (("cuir", "leather", "plastique", "plastic", "pvc"), {"reflect": 0.35, "gloss": 0.6}),
    (("béton", "beton", "concrete", "ciment", "enduit", "plâtre", "platre", "peint", "paint", "crépi", "crepi"),
     {"reflect": 0.12, "gloss": 0.45}),
    (("brique", "terre", "tuile", "brick", "pierre", "stone", "latérite", "laterite"), {"reflect": 0.08, "gloss": 0.4}),
    (("herbe", "gazon", "grass", "gras", "lawn", "végét", "veget", "feuill", "leaf", "arbre", "tree"),
     {"reflect": 0.05, "gloss": 0.35, "teinte": (0.32, 0.45, 0.18)}),
    (("asphalt", "bitum", "goudron", "route", "road", "enrobé", "enrobe"), {"reflect": 0.08, "gloss": 0.45,
                                                                          "teinte": (0.16, 0.16, 0.16)}),
    (("gravier", "gravel", "pavé", "pave", "dallage"), {"reflect": 0.08, "gloss": 0.4}),
    (("tissu", "fabric", "textile", "moquette", "carpet", "lin "), {"reflect": 0.03, "gloss": 0.3}),
]


# ----------------------------------------------------------------- outils ---
def data_dir() -> Path:
    d = T.data_folder() / "vray"
    d.mkdir(parents=True, exist_ok=True)
    return d


def renders_dir(project: str) -> Path:
    base = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / "Rendus V-Ray"
    d = base / safe(project) / dt.date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")[:60] or "vue"


def srgb_to_linear(c: float) -> float:
    c = max(0.0, min(1.0, float(c)))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def vec(v) -> str:
    return f"Vector({v[0]:.6f},{v[1]:.6f},{v[2]:.6f})"


def find_vray(saved: str = "") -> str:
    if saved and Path(saved).is_file():
        return saved
    roots = [Path(os.environ.get(k, "")) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
    roots += [Path("C:/Program Files")]
    found = []
    for root in roots:
        for sub in ("Chaos", "Chaos Group"):
            base = root / sub
            if base.is_dir():
                found += list(base.rglob("vray.exe"))
    found = sorted(set(found), key=lambda p: (0 if "SketchUp" in str(p) else 1 if "V-Ray" in str(p) else 2, str(p)))
    return str(found[0]) if found else ""


# --------------------------------------------------------------- Archicad ---
def project_info() -> dict:
    info = {"name": "Projet", "lat": ABIDJAN[0], "lon": ABIDJAN[1], "north": math.pi / 2, "lieu": "Abidjan (par défaut)"}
    try:
        p = tapir("GetProjectInfo")
        info["name"] = p.get("projectName") or p.get("projectPath", "Projet").split("\\")[-1].split("/")[-1] or "Projet"
    except Exception:  # noqa: BLE001
        pass
    try:
        g = tapir("GetGeoLocation").get("projectLocation", {})
        lat, lon = float(g.get("latitude", 0.0)), float(g.get("longitude", 0.0))
        budapest = abs(lat - 47.5) < 0.3 and abs(lon - 19.05) < 0.3     # lieu par defaut d'Archicad, jamais regle
        info["archicad"] = None if budapest or (abs(lat) < 1e-6 and abs(lon) < 1e-6) else (lat, lon)
        if info["archicad"]:
            info.update(lat=lat, lon=lon, lieu=f"{abs(lat):.2f}° {'N' if lat >= 0 else 'S'}, "
                                                f"{abs(lon):.2f}° {'E' if lon >= 0 else 'O'}")
        info["north"] = float(g.get("north", math.pi / 2))
    except Exception:  # noqa: BLE001
        pass
    return info


def apply_place(proj: dict, place: str) -> None:
    """Le lieu choisi dans la fenetre : une ville, ou le lieu regle dans Archicad."""
    if place == LIEU_ARCHICAD and proj.get("archicad"):
        proj["lat"], proj["lon"] = proj["archicad"]
        proj["fuseau"] = round(proj["lon"] / 15.0)
    else:
        lat, lon, tz = VILLES.get(place, VILLES["Abidjan"])
        proj.update(lat=lat, lon=lon, fuseau=tz)


def story_level() -> float:
    info = tapir("GetStories")
    act = info.get("actStory", 0)
    return next((float(s.get("level", 0.0)) for s in info.get("stories", []) if s.get("index") == act), 0.0)


def archicad_bbox(kinds=("Wall", "Slab", "Roof", "Column")):
    """Boite du modele dans Archicad : sert a recaler l'IFC (tout) et a cadrer les vues auto (le bati seul)."""
    ids_ = []
    for kind in kinds:
        try:
            ids_ += tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
        except Exception:  # noqa: BLE001
            pass
    if not ids_:
        return None
    lo, hi = [1e30] * 3, [-1e30] * 3
    for i in range(0, len(ids_), 400):
        rows = tapir("Get3DBoundingBoxes", {"elements": ids_[i:i + 400]}).get("boundingBoxes3D", [])
        for r in rows:
            b = r.get("boundingBox3D", r)
            if "xMin" not in b:
                continue
            for k, (a, c) in enumerate((("xMin", "xMax"), ("yMin", "yMax"), ("zMin", "zMax"))):
                lo[k], hi[k] = min(lo[k], b[a]), max(hi[k], b[c])
    return (lo, hi) if lo[0] < 1e29 else None


def _ids(rows) -> list[dict]:
    return [{"attributeId": r["attributeId"]} for r in rows if "attributeId" in r]


def archicad_surfaces() -> dict:
    """Surfaces Archicad : par guid, par index et par nom -> {name, rgb, transparence, texture}."""
    out = {"guid": {}, "index": {}, "name": {}}
    rows = tapir("GetAttributesByType", {"attributeType": "Surface"}).get("attributes", [])
    details = []
    for i in range(0, len(rows), 300):
        details += tapir("GetSurfaces", {"attributeIds": _ids(rows[i:i + 300]),
                                         "fields": ["surfaceColor", "transparency", "texture"]}).get("surfaces", [])
    for r, d in zip(rows, details):
        if "error" in d:
            continue
        c = d.get("surfaceColor") or {}
        info = {"name": r.get("name", ""), "rgb": (c.get("red", 0.7), c.get("green", 0.7), c.get("blue", 0.7)),
                "transparence": float(d.get("transparency", 0.0) or 0.0) / 100.0,
                "texture": (d.get("texture") or {}).get("name", "")}
        out["guid"][r["attributeId"]["guid"]] = info
        out["index"][str(r.get("index"))] = info
        out["name"][info["name"]] = info
    return out


def element_surfaces(surf: dict, report=print) -> dict:
    """GlobalId IFC de chaque element -> surface Archicad visible (cote de reference, dessus, materiau)."""
    bms = tapir("GetAttributesByType", {"attributeType": "BuildingMaterial"}).get("attributes", [])
    bm_surface = {}
    for i in range(0, len(bms), 300):
        part = bms[i:i + 300]
        rows = tapir("GetBuildingMaterials", {"attributeIds": _ids(part), "fields": ["cutSurfaceIndex"]}
                     ).get("buildingMaterials", [])
        for r, d in zip(part, rows):
            info = surf["index"].get(str(d.get("cutSurfaceIndex")))
            if info:
                bm_surface[r["attributeId"]["guid"]] = info
    comps = tapir("GetAttributesByType", {"attributeType": "Composite"}).get("attributes", [])
    comp_surface = {}
    for i in range(0, len(comps), 300):
        part = comps[i:i + 300]
        rows = tapir("GetComposites", {"attributeIds": _ids(part), "fields": ["skins"]}).get("composites", [])
        for r, d in zip(part, rows):
            skins = d.get("skins") or []
            if skins:
                g = (skins[0].get("buildingMaterialId") or {}).get("guid")
                if g in bm_surface:
                    comp_surface[r["attributeId"]["guid"]] = bm_surface[g]
    out = {}
    for kind in ("Wall", "Slab", "Roof", "Shell", "Column", "Beam", "Mesh", "Morph", "Stair", "Railing"):
        try:
            els = tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
        except Exception:  # noqa: BLE001
            continue
        for i in range(0, len(els), 300):
            part = els[i:i + 300]
            try:
                det = tapir("GetDetailsOfElements", {"elements": part, "fields": ["details"]}).get("detailsOfElements", [])
                ifc = tapir("GetIFCIdsOfElements", {"elements": part}).get("elementIFCIds", [])
            except Exception:  # noqa: BLE001
                continue
            for d, f in zip(det, ifc):
                gid = f.get("ifcId") if isinstance(f, dict) else None
                if not gid or "error" in d:
                    continue
                dd = d.get("details", {}) or {}
                info, forced = None, False
                for key in ("topMaterial", "referenceMaterial", "surfaceId"):
                    ov = dd.get(key)
                    if isinstance(ov, dict) and ov.get("overridden"):
                        info = surf["guid"].get((ov.get("attributeId") or {}).get("guid"))
                        forced = bool(info)
                    elif isinstance(ov, dict) and "guid" in ov:
                        info = surf["guid"].get(ov["guid"])
                    if info:
                        break
                if not info:
                    info = comp_surface.get((dd.get("compositeId") or {}).get("guid")) or \
                        bm_surface.get((dd.get("buildingMaterialId") or {}).get("guid"))
                if info:
                    out[gid] = (info["name"], forced)
    report(f"Surfaces Archicad : {len(surf['name'])}, éléments reliés : {len(out)}")
    return out


def export_ifc(path: Path) -> None:
    if path.exists():
        path.unlink()
    res = tapir("IFCFileOperation", {"method": "save", "ifcFilePath": str(path), "fileType": "ifc"}, timeout=1800)
    if isinstance(res, dict) and res.get("success") is False:
        raise RuntimeError(f"Export IFC refusé : {res.get('error')}")
    for _ in range(60):
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.5)
    raise RuntimeError("Archicad n'a pas écrit le fichier IFC.")


# ------------------------------------------------------------- geometrie ---
def material_name(f, m) -> str:
    """Nom de la surface Archicad derriere un materiau d'ifcopenshell."""
    name = str(getattr(m, "name", "") or "")
    match = re.match(r"^Ifc\w+-(\d+)$", name)
    if not match:
        return name or "Sans nom"
    try:
        ent = f.by_id(int(match.group(1)))
        if ent.is_a("IfcSurfaceStyle"):
            return ent.Name or name
        for style in f.by_type("IfcSurfaceStyle"):
            if ent in (style.Styles or ()):
                return style.Name or name
    except Exception:  # noqa: BLE001
        pass
    return name


def _rgb(m) -> tuple:
    d = getattr(m, "diffuse", None)
    if d is None:
        return (0.7, 0.7, 0.7)
    try:
        return (float(d.r()), float(d.g()), float(d.b()))
    except Exception:  # noqa: BLE001
        try:
            return tuple(float(x) for x in d)[:3]
        except Exception:  # noqa: BLE001
            return (0.7, 0.7, 0.7)


def ensure_ifcopenshell(report=print) -> None:
    """Installe le module IFC au premier rendu (une seule fois, environ 1 minute)."""
    try:
        import ifcopenshell  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    report("Premier rendu : installation du module IFC (ifcopenshell), une minute environ...")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for cmd in ([sys.executable, "-m", "pip", "install", "--user", "ifcopenshell"],
                [sys.executable, "-m", "pip", "install", "ifcopenshell"],
                ["uv", "pip", "install", "--python", sys.executable, "ifcopenshell"]):
        try:
            if subprocess.run(cmd, capture_output=True, creationflags=flags, timeout=900).returncode == 0:
                break
        except Exception:  # noqa: BLE001
            continue
    import importlib
    import site
    importlib.invalidate_caches()
    try:
        user_site = site.getusersitepackages()
        if user_site not in sys.path:
            sys.path.append(user_site)
    except Exception:  # noqa: BLE001
        pass
    import ifcopenshell  # noqa: F401  (ModuleNotFoundError si l'installation a echoue)


def ifc_meshes(path: Path, report=print, surf: dict | None = None, by_element: dict | None = None
               ) -> tuple[dict, dict, tuple]:
    """IFC -> {surface: (sommets, triangles)}, {surface: {rgb, transparence}}, boite."""
    import multiprocessing

    import ifcopenshell
    import ifcopenshell.geom

    f = ifcopenshell.open(str(path))
    s = ifcopenshell.geom.settings()
    try:
        s.set("use-world-coords", True)
    except Exception:  # noqa: BLE001
        s.set(s.USE_WORLD_COORDS, True)
    it = ifcopenshell.geom.iterator(s, f, max(1, multiprocessing.cpu_count() - 1))
    meshes, mats = {}, {}
    diag: dict = {}
    lo, hi = [1e30] * 3, [-1e30] * 3
    skip = {"IfcSpace", "IfcOpeningElement", "IfcAnnotation", "IfcGrid", "IfcVirtualElement"}
    count = 0
    if not it.initialize():
        raise RuntimeError("L'IFC ne contient aucune géométrie lisible.")
    while True:
        sh = it.get()
        try:
            ent = f.by_id(sh.id)
            cls = ent.is_a()
        except Exception:  # noqa: BLE001
            cls = ""
        if cls not in skip:
            g = sh.geometry
            v, fc, mids = g.verts, g.faces, list(g.material_ids)
            names = [material_name(f, m) for m in g.materials]
            known = (surf or {}).get("name", {})
            fb = (by_element or {}).get(getattr(ent, "GlobalId", None) if cls else None)
            for k, nm in enumerate(names):
                # surface forcee dans Archicad (dessus de dalle, face de mur) : elle prime ;
                # sinon la surface Archicad de l'element remplace un style IFC inconnu ou absent
                if fb and (fb[1] or (nm not in known and (nm.startswith("Ifc") or nm in ("Sans nom", "")))):
                    names[k] = fb[0]
            styles = diag.setdefault(cls or "?", {})
            for nm in names:
                styles[nm] = styles.get(nm, 0) + 1
            for m, nm in zip(g.materials, names):
                if nm not in mats:
                    if nm in known:
                        a = known[nm]
                        mats[nm] = {"rgb": a["rgb"], "transparence": a["transparence"], "texture": a["texture"]}
                    else:
                        t = getattr(m, "transparency", 0.0)
                        mats[nm] = {"rgb": _rgb(m), "transparence": 0.0 if t is None or t != t else float(t)}
            for k in range(0, len(v), 3):
                for a in range(3):
                    lo[a], hi[a] = min(lo[a], v[k + a]), max(hi[a], v[k + a])
            local: dict = {}
            for t in range(len(fc) // 3):
                mi = mids[t] if t < len(mids) else -1
                nm = names[mi] if 0 <= mi < len(names) else "Sans nom"
                local.setdefault(nm, []).append(t)
            for nm, tris in local.items():
                verts, faces = meshes.setdefault(nm, ([], []))
                remap = {}
                for t in tris:
                    for c in fc[3 * t:3 * t + 3]:
                        if c not in remap:
                            remap[c] = len(verts)
                            verts.append((v[3 * c], v[3 * c + 1], v[3 * c + 2]))
                        faces.append(remap[c])
            count += 1
            if count % 200 == 0:
                report(f"Conversion de la géométrie : {count} éléments...")
        if not it.next():
            break
    try:                                     # diagnostic : quelle surface pour quel type d'element
        lines = [f"{c} : " + ", ".join(f"{n} x{k}" for n, k in sorted(v.items(), key=lambda x: -x[1])[:8])
                 for c, v in sorted(diag.items())]
        (path.parent / "surfaces_diagnostic.txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if "Sans nom" in meshes and "Sans nom" not in mats:
        mats["Sans nom"] = {"rgb": (0.75, 0.75, 0.75), "transparence": 0.0}
    return meshes, mats, (lo, hi)


def guess_material(name: str, info: dict, library: dict) -> dict:
    """Reglages V-Ray d'une surface : bibliotheque d'abord, sinon devines d'apres le nom."""
    if name in library:
        return dict(library[name])
    low = name.lower()
    out = {"rgb": list(info.get("rgb", (0.7, 0.7, 0.7))), "reflect": 0.15, "gloss": 0.5, "metal": 0.0, "type": "opaque"}
    if info.get("transparence", 0.0) > 0.3:
        out["type"] = "verre"
    for words, params in FAMILLES:
        if any(w in low for w in words):
            out.update({k: v for k, v in params.items() if k != "teinte"})
            if params.get("teinte") and info.get("texture"):      # couleur d'un materiau texture : souvent grise
                out["rgb"] = list(params["teinte"])
            break
    return out


def mat_id(name: str) -> str:
    return "M_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]


def _box_uv(verts, faces, offset, k: float):
    """Coordonnees de texture en projection boite (1 unite = 1 m, multipliee par k) : sommets doubles par axe."""
    uv, idx, out = [], {}, []
    ox, oy, oz = offset
    for t in range(0, len(faces), 3):
        a, b, c = faces[t], faces[t + 1], faces[t + 2]
        pa, pb_, pc = verts[a], verts[b], verts[c]
        u = (pb_[0] - pa[0], pb_[1] - pa[1], pb_[2] - pa[2])
        v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        n = (abs(u[1] * v[2] - u[2] * v[1]), abs(u[2] * v[0] - u[0] * v[2]), abs(u[0] * v[1] - u[1] * v[0]))
        axis = 0 if n[0] >= n[1] and n[0] >= n[2] else 1 if n[1] >= n[2] else 2
        for vi in (a, b, c):
            key = (vi, axis)
            j = idx.get(key)
            if j is None:
                x, y, z = verts[vi][0] + ox, verts[vi][1] + oy, verts[vi][2] + oz
                p = (y, z) if axis == 0 else (x, z) if axis == 1 else (x, y)
                j = idx[key] = len(uv)
                uv.append((p[0] * k, p[1] * k))
            out.append(j)
    return uv, out


def write_geometry(path: Path, meshes: dict, offset: tuple, scales: dict | None = None) -> int:
    """Geometrie seule (les materiaux sont dans materiaux.vrscene) + coordonnees de texture pour les materiaux Cosmos."""
    tris = 0
    scales = scales or {}
    ox, oy, oz = offset
    with path.open("w", encoding="utf-8") as out:
        out.write("// Geometrie Archicad -> V-Ray (V-Ray Link)\n")
        for k, (name, (verts, faces)) in enumerate(meshes.items()):
            if not faces:
                continue
            gid = f"g{k}"
            out.write(f"// surface : {name}\nGeomStaticMesh {gid} {{\n  vertices=ListVector(")
            for i in range(0, len(verts), 2000):
                chunk = ",".join(f"Vector({x + ox:.4f},{y + oy:.4f},{z + oz:.4f})" for x, y, z in verts[i:i + 2000])
                out.write(("," if i else "") + chunk)
            out.write(");\n  faces=ListInt(")
            for i in range(0, len(faces), 6000):
                out.write(("," if i else "") + ",".join(str(c) for c in faces[i:i + 6000]))
            uv, uvf = _box_uv(verts, faces, offset, float(scales.get(name, 1.0)))
            out.write(");\n  map_channels=List(List(1,ListVector(")
            for i in range(0, len(uv), 2000):
                out.write(("," if i else "") + ",".join(f"Vector({a:.4f},{b:.4f},0)" for a, b in uv[i:i + 2000]))
            out.write("),ListInt(")
            for i in range(0, len(uvf), 6000):
                out.write(("," if i else "") + ",".join(str(c) for c in uvf[i:i + 6000]))
            out.write(")));\n}\n")
            out.write(f"Node {gid}_node {{\n  transform=Transform(Matrix(Vector(1,0,0),Vector(0,1,0),Vector(0,0,1)),"
                      f"Vector(0,0,0));\n  geometry={gid};\n  material={mat_id(name)};\n}}\n")
            tris += len(faces) // 3
    return tris


def _brdf(mid: str, m: dict) -> str:
    r, g, b = (srgb_to_linear(c) for c in m["rgb"])
    if m.get("type") == "verre":
        return (f"BRDFVRayMtl {mid}_brdf {{\n  diffuse=AColor(0.02,0.02,0.02,1);\n  reflect=AColor(1,1,1,1);\n"
                f"  reflect_glossiness=1;\n  fresnel=1;\n  refract=AColor({0.9 * max(r, .6):.4f},{0.9 * max(g, .6):.4f},"
                f"{0.9 * max(b, .6):.4f},1);\n  refract_ior=1.5;\n  refract_affect_shadows=1;\n}}\n")
    if m.get("type") == "eau":
        return (f"BRDFVRayMtl {mid}_brdf {{\n  diffuse=AColor(0.01,0.03,0.04,1);\n  reflect=AColor(1,1,1,1);\n"
                f"  reflect_glossiness=0.98;\n  fresnel=1;\n  refract=AColor(0.85,0.95,0.95,1);\n  refract_ior=1.33;\n"
                f"  fog_color=Color(0.6,0.85,0.85);\n  fog_mult=0.3;\n  refract_affect_shadows=1;\n}}\n")
    rf = float(m.get("reflect", 0.15))
    return (f"BRDFVRayMtl {mid}_brdf {{\n  diffuse=AColor({r:.4f},{g:.4f},{b:.4f},1);\n"
            f"  reflect=AColor({rf:.3f},{rf:.3f},{rf:.3f},1);\n  reflect_glossiness={float(m.get('gloss', .5)):.3f};\n"
            f"  fresnel=1;\n  metalness={float(m.get('metal', 0.0)):.2f};\n}}\n")


def cosmos_assignments(state: dict, key: str) -> dict:
    """Surface -> materiau Cosmos (associations par nom de surface + elements choisis dans Archicad)."""
    out = dict(state.get("cosmos") or {})
    for gid, folder in ((state.get("selection") or {}).get(key) or {}).items():
        out[virtual_surface(folder)] = {"dossier": folder, "echelle": 1.0}
    return out


def virtual_surface(folder: str) -> str:
    return "Cosmos · " + re.sub(r"_[0-9a-f]{8}$", "", Path(folder).name).replace("_", " ")


def write_materials(path: Path, mats: dict, library: dict, assigned: dict, report=print) -> list[str]:
    """Un materiau par surface : materiau Cosmos s'il est associe, sinon couleur Archicad + reglages devines."""
    used = []
    with path.open("w", encoding="utf-8") as out:
        out.write("// Materiaux (V-Ray Link)\n")
        for name, info in mats.items():
            mid = mat_id(name)
            a = assigned.get(name)
            if a and a.get("dossier"):
                try:
                    folder = Path(a["dossier"])
                    vrmat = sorted(folder.glob("*.vrmat"), key=_lod)[0]
                    fixed, top = vrmat_for_render(vrmat)
                    out.write(f"// {name} <- Cosmos : {folder.name}\nMtlVRmat {mid} {{\n  filename=\"{str(fixed).replace(chr(92), '/')}\";\n"
                              f"  mtlname=\"{top}\";\n}}\n")
                    used.append(f"{name} → {virtual_surface(str(folder))[9:]}")
                    continue
                except Exception as error:  # noqa: BLE001
                    report(f"Matériau Cosmos indisponible pour {name} : {error}")
            m = guess_material(name, info, library)
            out.write(f"// surface : {name}\n" + _brdf(mid, m) + f"MtlSingleBRDF {mid} {{\n  brdf={mid}_brdf;\n}}\n")
    return used


# ---------------------------------------------------------------- soleil ---
def sun_position(lat: float, lon: float, day: dt.date, hour: float, tz: float = 0.0) -> tuple[float, float]:
    """(azimut depuis le nord, sens horaire ; hauteur) en radians - formules de la NOAA."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + (hour - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hour * 60 + eqt + 4 * lon - 60 * tz
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cosz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    zen = math.acos(max(-1.0, min(1.0, cosz)))
    az = math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(decl) * math.cos(la)) + math.pi
    return az % (2 * math.pi), math.pi / 2 - zen


def sun_vector(az: float, alt: float, north: float) -> tuple:
    """Direction vers le soleil dans les coordonnees du projet (north = angle du nord depuis l'axe X)."""
    a = north - az                          # azimut compte vers l'est = sens horaire
    return (math.cos(alt) * math.cos(a), math.cos(alt) * math.sin(a), math.sin(alt))


def frame(direction: tuple, up=(0.0, 0.0, 1.0)) -> tuple:
    """Axes (x, y, z) d'un repere dont l'axe -z regarde dans `direction`."""
    L = math.sqrt(sum(c * c for c in direction)) or 1.0
    z = [-c / L for c in direction]
    x = [up[1] * z[2] - up[2] * z[1], up[2] * z[0] - up[0] * z[2], up[0] * z[1] - up[1] * z[0]]
    Lx = math.sqrt(sum(c * c for c in x))
    if Lx < 1e-9:
        x = [1.0, 0.0, 0.0]
        Lx = 1.0
    x = [c / Lx for c in x]
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
    return x, y, z


# --------------------------------------------------------------- cameras ---
def compass(vx: float, vy: float, north: float) -> str:
    a = (math.degrees(north - math.atan2(vy, vx))) % 360        # 0 = nord, sens horaire
    names = ["nord", "nord-est", "est", "sud-est", "sud", "sud-ouest", "ouest", "nord-ouest"]
    return names[int((a + 22.5) // 45) % 8]


def auto_cameras(bbox, north: float) -> list[dict]:
    if not bbox:
        return []
    lo, hi = bbox
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    w, d, h = hi[0] - lo[0], hi[1] - lo[1], max(hi[2] - lo[2], 3.0)
    diag = math.hypot(w, d)
    zt = lo[2] + h * 0.4
    cams = [{"nom": "Vue aérienne", "auto": True, "focale": 35,
             "eye": [cx - 0.55 * diag, cy - 0.8 * diag, lo[2] + h + 0.55 * diag], "target": [cx, cy, zt]}]
    for vx, vy, span in ((0, -1, w), (0, 1, w), (-1, 0, d), (1, 0, d)):
        other = d if span == w else w
        dist = max(span * 1.15, 8.0) + other / 2
        eye = [cx + vx * dist, cy + vy * dist, lo[2] + 1.6]
        cams.append({"nom": f"Façade {compass(vx, vy, north)}", "auto": True, "focale": 24, "eye": eye,
                     "target": [cx, cy, lo[2] + min(h * 0.45, 4.0)]})
    return cams


# ----------------------------------------------------------------- scene ---
def write_scene(path: Path, geometry: Path, cam: dict, opts: dict, proj: dict, ground_z: float) -> None:
    W, H = TAILLES.get(opts["taille"], (1920, 1080))
    minutes, thr = QUALITES.get(opts["qualite"], QUALITES["Haute"])
    fnum, shutter, iso = AMBIANCES.get(opts["ambiance"], AMBIANCES["Extérieur jour"])
    eye, target = list(cam["eye"]), list(cam["target"])
    if opts.get("verticales") and not cam.get("nom", "").startswith("Vue aérienne"):
        target[2] = eye[2]                    # camera horizontale : les verticales restent droites
    x, y, z = frame([t - e for t, e in zip(target, eye)])
    hfov = 2 * math.atan(18.0 / float(cam.get("focale", 24)))
    day = dt.date(dt.date.today().year, int(opts["mois"]), int(opts["jour"]))
    az, alt = sun_position(proj["lat"], proj["lon"], day, float(opts["heure"]), float(proj.get("fuseau", 0.0)))
    sd = sun_vector(az, alt, proj["north"])
    sx, sy, sz = frame([-c for c in sd])
    s = [f"// V-Ray Link - {cam['nom']} - {proj['name']}",
         "SettingsUnitsInfo { meters_scale=1; photometric_scale=1; }",
         f"SettingsOutput {{ img_width={W}; img_height={H}; }}",
         "SettingsGI { on=1; primary_engine=2; secondary_engine=3; }",
         "SettingsLightCache { subdivs=1500; }",
         f"SettingsImageSampler {{ type=3; progressive_maxTime={minutes:.2f}; progressive_threshold={thr}; "
         "progressive_minSubdivs=1; progressive_maxSubdivs=100; }",
         "SettingsColorMapping { type=6; dark_mult=1; bright_mult=1; gamma=2.2; subpixel_mapping=0; clamp_output=0; }",
         "RenderChannelDenoiser denoiser { enabled=1; name=\"VRayDenoiser\"; }",
         f"RenderView view {{\n  transform=Transform(Matrix({vec(x)},{vec(y)},{vec(z)}),{vec(eye)});\n  fov={hfov:.6f};\n}}",
         f"CameraPhysical cam {{ type=0; specify_fov=1; fov={hfov:.6f}; f_number={fnum}; shutter_speed={shutter}; "
         f"ISO={iso}; exposure=1; white_balance=Color(1,0.95,0.88); vignetting=0.3; }}",
         f"SunLight sun {{\n  transform=Transform(Matrix({vec(sx)},{vec(sy)},{vec(sz)}),{vec([c * 1000 for c in sd])});\n"
         f"  intensity_multiplier=1;\n  size_multiplier={float(opts.get('taille_soleil', 3.0))};\n"
         f"  turbidity={float(opts.get('trouble', 3.0))};\n  ozone=0.35;\n  sky_model=3;\n}}",
         "TexSky sky { sun=sun; }",
         "SettingsEnvironment { bg_tex=sky; gi_tex=sky; reflect_tex=sky; refract_tex=sky; "
         "bg_tex_on=1; gi_tex_on=1; reflect_tex_on=1; refract_tex_on=1; }"]
    sol = SOLS.get(opts.get("sol", "Gazon"))
    if sol:
        r, g, b = (srgb_to_linear(c) for c in sol)
        s += [f"BRDFVRayMtl sol_brdf {{ diffuse=AColor({r:.4f},{g:.4f},{b:.4f},1); reflect=AColor(0.05,0.05,0.05,1); "
              "reflect_glossiness=0.35; fresnel=1; }",
              "MtlSingleBRDF sol_mtl { brdf=sol_brdf; }",
              "GeomPlane sol_geo { }",
              f"Node sol_node {{ transform=Transform(Matrix(Vector(1,0,0),Vector(0,1,0),Vector(0,0,1)),"
              f"Vector(0,0,{ground_z - 0.005:.4f})); geometry=sol_geo; material=sol_mtl; }}"]
    s.append('#include "materiaux.vrscene"')
    s.append(f'#include "{geometry.name}"')
    path.write_text("\n".join(s) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ Cosmos ---
# V-Ray Link se presente a Chaos Cosmos comme un logiciel hote (meme "plateforme" que V-Ray pour SketchUp,
# donc memes fichiers V-Ray : .vrmat / .vrmesh / .vrscan). Cosmos envoie alors chaque telechargement.
# Protocole : gRPC content.v1beta2 (ImportAPI/WaitForImport) sur HTTP/2, ecrit ici sans dependance.
COSMOS_EXE = Path(r"C:\Program Files\Chaos\Cosmos\cbservice.exe")
COSMOS_HOTE = ("SketchUp", "26.2.243")
COSMOS_ID = "351afdea-cf1f-5e8a-b423-acfcb0c01648"        # identifiant fixe de V-Ray Link aupres de Cosmos
SVC = "/content.v1beta2."
TEXTURE_EXT = ("tx", "jpg", "jpeg", "png", "exr", "tif", "tiff", "hdr", "bmp", "vrscan", "vrmesh", "ies", "vrmat", "vrscene")

def _varint(n: int) -> bytes:
    out = bytearray()
    n &= (1 << 64) - 1
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb(*fields) -> bytes:
    """fields : (numero, valeur) ; valeur int/bool -> varint, str/bytes -> length-delimited, list -> repete."""
    out = bytearray()
    for num, val in fields:
        vals = val if isinstance(val, list) else [val]
        for v in vals:
            if v is None:
                continue
            if isinstance(v, bool) or isinstance(v, int):
                out += _varint(num << 3) + _varint(int(v))
            else:
                b = v.encode() if isinstance(v, str) else v
                out += _varint(num << 3 | 2) + _varint(len(b)) + b
    return bytes(out)


def _rvarint(b, i):
    n = s = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return n, i


def decode(b: bytes, depth=0):
    """Decodage generique -> {numero: [valeurs]} ; sous-messages decodes si possible."""
    out = {}
    i = 0
    while i < len(b):
        key, i = _rvarint(b, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _rvarint(b, i)
        elif wt == 2:
            ln, i = _rvarint(b, i)
            raw = b[i:i + ln]
            i += ln
            v = raw
            try:
                txt = raw.decode("utf-8")
                if all(c.isprintable() or c in "\r\n\t" for c in txt):
                    v = txt
            except UnicodeDecodeError:
                pass
            if isinstance(v, bytes) and depth < 8:
                try:
                    v = decode(raw, depth + 1)
                except Exception:  # noqa: BLE001
                    v = raw.hex()
        elif wt == 5:
            v = struct.unpack("<f", b[i:i + 4])[0]
            i += 4
        elif wt == 1:
            v = struct.unpack("<d", b[i:i + 8])[0]
            i += 8
        else:
            raise ValueError("wire type %d" % wt)
        out.setdefault(num, []).append(v)
    return out




class H2Call:
    """Client gRPC minimal sur HTTP/2 (bibliotheque standard uniquement, pas de grpcio)."""

    def __init__(self, port, tls, timeout):
        import socket
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        if tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE        # certificat local auto-signe (127.0.0.1)
            ctx.set_alpn_protocols(["h2"])
            sock = ctx.wrap_socket(sock, server_hostname="localhost")
        self.s, self.port, self.tls = sock, port, tls
        self.buf = b""
        self.s.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
                       + self._frame(4, 0, 0, struct.pack(">HI", 4, 16 * 1024 * 1024))
                       + self._frame(8, 0, 0, struct.pack(">I", 1 << 30)))

    @staticmethod
    def _frame(typ, flags, sid, payload):
        return struct.pack(">I", len(payload))[1:] + bytes([typ, flags]) + struct.pack(">I", sid) + payload

    @staticmethod
    def _hstr(b):
        n = len(b)
        if n < 127:
            return bytes([n]) + b
        out, n = bytearray([127]), n - 127
        while n >= 128:
            out.append((n & 0x7F) | 0x80)
            n >>= 7
        out.append(n)
        return bytes(out) + b

    def request(self, method, msg):
        hdrs = [(":method", "POST"), (":scheme", "https" if self.tls else "http"), (":path", SVC + method),
                (":authority", f"127.0.0.1:{self.port}"), ("content-type", "application/grpc"), ("te", "trailers"),
                ("user-agent", "vraylink-grpc/1.0")]
        block = b"".join(b"\x00" + self._hstr(k.encode()) + self._hstr(v.encode()) for k, v in hdrs)
        body = b"\x00" + struct.pack(">I", len(msg)) + msg
        self.s.sendall(self._frame(1, 0x4, 1, block) + self._frame(0, 0x1, 1, body))

    def _read(self, n):
        while len(self.buf) < n:
            d = self.s.recv(65536)
            if not d:
                raise ConnectionError("connexion fermee par Cosmos")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def messages(self):
        data = b""
        while True:
            head = self._read(9)
            ln = int.from_bytes(head[:3], "big")
            typ, flags, sid = head[3], head[4], int.from_bytes(head[5:9], "big") & 0x7FFFFFFF
            payload = self._read(ln) if ln else b""
            if typ == 0 and sid == 1:                       # DATA
                if flags & 0x8:                             # padding
                    payload = payload[1:len(payload) - payload[0]]
                data += payload
                if ln:
                    self.s.sendall(self._frame(8, 0, 0, struct.pack(">I", ln)) + self._frame(8, 0, 1, struct.pack(">I", ln)))
                while len(data) >= 5 and len(data) >= 5 + int.from_bytes(data[1:5], "big"):
                    m = int.from_bytes(data[1:5], "big")
                    yield data[5:5 + m]
                    data = data[5 + m:]
                if flags & 0x1:
                    return
            elif typ == 1 and sid == 1 and flags & 0x1:     # trailers -> fin
                return
            elif typ == 4 and not flags & 0x1:              # SETTINGS -> ACK
                self.s.sendall(self._frame(4, 0x1, 0, b""))
            elif typ == 6 and not flags & 0x1:              # PING -> reponse
                self.s.sendall(self._frame(6, 0x1, 0, payload))
            elif typ == 3 and sid == 1:                     # RST_STREAM
                raise ConnectionError("flux refuse (RST code %d)" % int.from_bytes(payload[:4], "big"))
            elif typ == 7:                                  # GOAWAY
                raise ConnectionError("GOAWAY code %d %s" % (int.from_bytes(payload[4:8], "big"), payload[8:200]))

    def close(self):
        try:
            self.s.close()
        except Exception:  # noqa: BLE001
            pass


def _quiet_run(cmd: list) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="mbcs" if os.name == "nt" else None,
                          errors="replace", creationflags=flags, timeout=20).stdout or ""


def _port_open(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def cosmos_ports() -> list:
    """Ports des services Cosmos en cours (il peut y en avoir plusieurs)."""
    ports = []
    rt = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Chaos" / "Cosmos" / "runtime.json"
    try:
        ports += [int(p) for p in re.findall(r"(?:port\D{1,4}|:)(\d{4,5})", rt.read_text("utf-8", "replace"))]
    except Exception:  # noqa: BLE001
        pass
    try:
        pids = set(re.findall(r'"cbservice\.exe","(\d+)"',
                              _quiet_run(["tasklist", "/FI", "IMAGENAME eq cbservice.exe", "/FO", "CSV", "/NH"]), re.I))
        for line in _quiet_run(["netstat", "-ano", "-p", "TCP"]).splitlines():
            m = re.search(r"TCP\s+(127\.0\.0\.1|0\.0\.0\.0|\[::1?\]):(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
            if m and m.group(3) in pids:
                ports.append(int(m.group(2)))
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(ports + [30305]))


class _Grpc:
    def __init__(self, port: int, tls: bool):
        self.port, self.tls = port, tls

    def unary(self, method: str, msg: bytes, timeout: float = 10):
        c = H2Call(self.port, self.tls, timeout)
        try:
            c.request(method, msg)
            for m in c.messages():
                return m
            raise RuntimeError("pas de réponse")
        finally:
            c.close()

    def stream(self, method: str, msg: bytes):
        c = H2Call(self.port, self.tls, 15)
        c.request(method, msg)
        c.s.settimeout(None)
        try:
            yield from c.messages()
        finally:
            c.close()


def _first(d: dict, k: int, default=None):
    v = d.get(k)
    return v[0] if v else default


class CosmosHost:
    """Connexion permanente a Cosmos ; `on_import(nom_du_paquet)` est appele a chaque telechargement."""

    def __init__(self, on_import, on_status):
        self.on_import, self.on_status = on_import, on_status
        self.ports: set = set()
        self.running = False
        self.lock = threading.Lock()

    def request(self) -> bytes:
        info = pb((1, "V-Ray Link (Archicad)"), (2, COSMOS_ID), (3, os.getpid()))
        feats = pb((1, True), (2, True), (4, True), (6, True), (7, True), (8, True), (9, True), (11, True),
                   (12, pb((1, True), (2, True), (3, True))), (14, True), (15, True), (16, True), (18, True))
        return pb((1, info), (2, pb((1, "V-Ray"), (2, "7.20.00"))), (3, pb((1, COSMOS_HOTE[0]), (2, COSMOS_HOTE[1]))),
                  (4, pb((3, [1, 2]))), (5, feats))

    def start(self) -> None:
        if self.running:
            self.scan()
            return
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while self.running:
            try:
                self.scan()
            except Exception as error:  # noqa: BLE001
                self.on_status(f"Cosmos : {error}")
            time.sleep(20)                   # Cosmos redemarre parfois : on se reconnecte

    def scan(self) -> None:
        if not _port_open(30305) and COSMOS_EXE.exists():
            self.on_status("Démarrage de Chaos Cosmos...")
            os.startfile(str(COSMOS_EXE))  # type: ignore[attr-defined]
            for _ in range(30):
                time.sleep(1)
                if _port_open(30305):
                    break
        found = 0
        for port in cosmos_ports():
            with self.lock:
                if port in self.ports or port - 1 in self.ports:
                    found += 1
                    continue
            for tls in (False, True):
                api = _Grpc(port, tls)
                try:
                    api.unary("ImportAPI/ListImporters", b"", timeout=4)
                except Exception:  # noqa: BLE001
                    continue
                with self.lock:
                    self.ports.add(port)
                threading.Thread(target=self._listen, args=(api,), daemon=True).start()
                found += 1
                break
        self.on_status("Cosmos connecté : télécharge des matériaux, ils arrivent ici." if found
                       else "Cosmos introuvable (est-il installé ?).")

    def _listen(self, api: _Grpc) -> None:
        try:
            for body in api.stream("ImportAPI/WaitForImport", self.request()):
                if body:
                    ev = decode(body)
                    threading.Thread(target=self._received, args=(api, ev), daemon=True).start()
        except Exception:  # noqa: BLE001
            pass
        with self.lock:
            self.ports.discard(api.port)

    def _received(self, api: _Grpc, ev: dict) -> None:
        pid, rev = _first(ev, 2, ""), _first(ev, 3, 0)
        name = ""
        try:
            info = decode(api.unary("PackagesAPI/FindPackageById", pb((1, pid), (2, rev))))
            name = _first(_first(_first(info, 1, {}), 4, {}), 1, "")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)                       # laisse Cosmos finir d'ecrire les fichiers
        self.on_import(name or pid)


def cosmos_url() -> str:
    return (f"http://127.0.0.1:30305/?utm_campaign=cosmos&utm_source=V-Ray+7.20.00"
            f"&utm_medium={COSMOS_HOTE[0]}+Pro+{COSMOS_HOTE[1]}&importer_id={COSMOS_ID}")


def cosmos_packages() -> Path:
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    for p in (home / "OneDrive" / "Documents" / "Chaos Cosmos" / "Packages", home / "Documents" / "Chaos Cosmos" / "Packages"):
        if p.is_dir():
            return p
    return home / "Documents" / "Chaos Cosmos" / "Packages"


_LOD = {"3dh": 0, "3d": 1, "3dm": 2, "3dl": 3}


def _lod(path: Path) -> int:
    m = re.search(r"_(3dh|3dm|3dl|3d)_", path.name)
    return _LOD.get(m.group(1), 4) if m else 4


def cosmos_library(kind: str = "Materials") -> list[dict]:
    """Paquets Cosmos telecharges : [{nom, categorie, dossier, vrmat, vrmesh, apercu, date}] du plus recent au plus ancien."""
    base = cosmos_packages() / kind
    out = []
    if not base.is_dir():
        return out
    for folder in base.glob("*/*"):
        if not folder.is_dir():
            continue
        vrmats = sorted(folder.glob("*.vrmat"), key=_lod)
        if not vrmats:
            continue
        meshes = sorted(folder.glob("*.vrmesh"), key=_lod)
        prev = sorted((folder / "Previews").glob("*.png")) or sorted((folder / "Previews").glob("*.jpg"))
        label = re.sub(r"_[0-9a-f]{8}$", "", folder.name).replace("_", " ")
        out.append({"nom": label, "categorie": folder.parent.name.replace("_", " "), "dossier": str(folder),
                    "vrmat": str(vrmats[0]), "vrmesh": str(meshes[0]) if meshes else "",
                    "apercu": str(prev[0]) if prev else "", "date": folder.stat().st_mtime})
    out.sort(key=lambda e: -e["date"])
    return out


def vrmat_for_render(vrmat: Path) -> tuple[Path, str]:
    """Copie du .vrmat avec des chemins absolus (textures, scans) + nom du materiau principal."""
    text = vrmat.read_text("utf-8", "replace")
    folder = vrmat.parent
    files = {p.name.lower(): p for p in folder.rglob("*") if p.is_file()}

    def fix(m):
        raw = m.group(1).strip()
        p = Path(raw.replace("\\", "/"))
        if p.is_absolute() and p.exists():
            return m.group(0)
        cand = folder / raw.lstrip("/\\")
        if not cand.exists():
            base = p.name.lower()
            cand = files.get(base) or next((f for n, f in files.items() if n.endswith("_" + base) or n.endswith(base)), None)
        if not cand:
            return m.group(0)
        return ">" + str(cand).replace("\\", "/").replace("&", "&amp;") + "<"

    pattern = r">([^<>]+?\.(?:" + "|".join(TEXTURE_EXT) + r"))<"
    fixed = re.sub(pattern, fix, text, flags=re.I)
    urls = re.findall(r'<Asset\s+[^>]*?url="([^"]+)"[^>]*?type="material"', fixed) + \
        re.findall(r'<Asset\s+[^>]*?type="material"[^>]*?url="([^"]+)"', fixed)
    urls = list(dict.fromkeys(urls))
    top = next((u for u in urls if fixed.count(u) == 1), urls[0] if urls else "")
    out = data_dir() / "cosmos" / f"{safe(vrmat.parent.name)}.vrmat"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(fixed, encoding="utf-8")
    return out, top.lstrip("/")


def thumbnail(entry: dict, size: int = 96):
    """Petite image d'apercu (mise en cache) pour la liste des materiaux."""
    import tkinter as tk
    cache = data_dir() / "cosmos" / "apercus" / f"{safe(Path(entry['dossier']).name)}_{size}.png"
    try:
        if cache.exists():
            return tk.PhotoImage(file=str(cache))
        if not entry.get("apercu"):
            return None
        img = tk.PhotoImage(file=entry["apercu"])
        k = max(1, max(img.width(), img.height()) // size)
        small = img.subsample(k, k)
        cache.parent.mkdir(parents=True, exist_ok=True)
        small.write(str(cache), format="png")
        return small
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- etat ---
def load_settings() -> dict:
    saved = load_json(REGLAGES)
    today = dt.date.today()
    opts = {"qualite": "Haute", "taille": "1920 × 1080 (16:9)", "ambiance": "Extérieur jour", "sol": "Gazon",
            "jour": today.day, "mois": today.month, "heure": 16.5, "fuseau": 0.0, "verticales": True,
            "trouble": 3.0, "taille_soleil": 3.0, "lieu": "Abidjan", "reutiliser": False, "oeil": 1.6, "cible": 1.4, "focale": 24}
    opts.update({k: v for k, v in (saved.get("options") or {}).items() if k in opts})
    return {"options": opts, "cameras": saved.get("cameras") or {}, "bibliotheque": saved.get("bibliotheque") or {},
            "vray": saved.get("vray", ""), "coches": saved.get("coches") or {},
            "cosmos": saved.get("cosmos") or {}, "selection": saved.get("selection") or {}}


def save_settings(state: dict) -> None:
    out = {k: state.get(k) or {} for k in ("options", "cameras", "bibliotheque", "coches", "cosmos", "selection")}
    out["vray"] = state.get("vray") or ""
    save_json(REGLAGES, out)


def prepare_geometry(state: dict, proj: dict, report=print) -> tuple[Path, float, str]:
    """Exporte et convertit le modele (ou reprend la conversion precedente), puis ecrit les materiaux.
    Renvoie (fichier geometrie, sol z, resume)."""
    key = safe(proj["name"])
    d = data_dir() / key
    d.mkdir(parents=True, exist_ok=True)
    geo, meta, cache = d / "geometrie.vrscene", d / "geometrie.json", d / "geometrie.pkl"
    assigned = cosmos_assignments(state, key)
    selection = (state.get("selection") or {}).get(key) or {}
    sel_sig = hashlib.md5(json.dumps(sorted(selection.items())).encode()).hexdigest()
    info = {}
    if meta.exists():
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            info = {}
    reuse = (state["options"].get("reutiliser") and geo.exists() and cache.exists() and info.get("version") == 2
             and info.get("selection") == sel_sig)
    if reuse:
        meshes, mats, offset, ground = pickle.loads(cache.read_bytes())
        note = "géométrie reprise de l'export précédent"
    else:
        if state["options"].get("reutiliser") and info.get("selection") not in (None, sel_sig):
            report("Matériaux appliqués à des éléments : nouvel export du modèle...")
        ensure_ifcopenshell(report)
        report("Export IFC depuis Archicad...")
        ifc = d / "modele.ifc"
        export_ifc(ifc)
        surf, by_element = {"name": {}}, {}
        try:
            report("Lecture des surfaces Archicad...")
            surf = archicad_surfaces()
            by_element = element_surfaces(surf, report)
        except Exception as error:  # noqa: BLE001
            report(f"(surfaces Archicad non lues : {error})")
        for gid, folder in selection.items():          # elements choisis dans Archicad : materiau Cosmos force
            by_element[gid] = (virtual_surface(folder), True)
        report("Conversion de la géométrie pour V-Ray...")
        meshes, mats, (lo, hi) = ifc_meshes(ifc, report, surf, by_element)
        if not meshes:
            raise RuntimeError("Aucune géométrie dans l'export IFC (calques masqués ?).")
        for name in meshes:
            mats.setdefault(name, {"rgb": (0.75, 0.75, 0.75), "transparence": 0.0})
        offset = (0.0, 0.0, 0.0)
        box = archicad_bbox()
        if box:                                  # IFC decale par rapport a Archicad (point de base, site) : on recale
            dx = [box[0][k] - lo[k] for k in range(3)]
            if any(abs(c) > 0.05 for c in dx):
                offset = tuple(dx)
        ground = (box[0][2] if box else lo[2] + offset[2])
        cache.write_bytes(pickle.dumps((meshes, mats, offset, ground)))
        note = ""
    scales = {n: float(a.get("echelle", 1.0)) for n, a in assigned.items() if n in meshes}
    uv_sig = hashlib.md5(json.dumps(sorted(scales.items())).encode()).hexdigest()
    if not (reuse and info.get("uv") == uv_sig):
        report("Écriture de la géométrie...")
        tris = write_geometry(geo, meshes, offset, scales)
    else:
        tris = int(info.get("triangles", 0))
    used = write_materials(d / "materiaux.vrscene", {n: mats.get(n, {}) for n in meshes}, state["bibliotheque"],
                           assigned, report)
    meta.write_text(json.dumps({"version": 2, "sol": ground, "surfaces": sorted(meshes), "triangles": tris,
                                "selection": sel_sig, "uv": uv_sig,
                                "couleurs": {n: list(mats.get(n, {}).get("rgb", (0.7, 0.7, 0.7))) for n in meshes}}),
                    encoding="utf-8")
    state["surfaces"] = {n: list(mats.get(n, {}).get("rgb", (0.7, 0.7, 0.7))) for n in sorted(meshes)}
    summary = f"{len(meshes)} surfaces, {tris:,} triangles".replace(",", " ")
    if note:
        summary += f" ({note})"
    if used:
        summary += "\nMatériaux Cosmos : " + " ; ".join(used)
    return geo, ground, summary


def diagnostic(folder: Path) -> list[str]:
    try:
        return (folder / "surfaces_diagnostic.txt").read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return ["(pas de diagnostic : géométrie reprise de l'export précédent)"]


def launch(vray: str, scene: Path, image: Path, show: bool = True) -> None:
    """Lance V-Ray ; en cas d'erreur la fenetre noire reste ouverte (message lisible) et un journal est ecrit."""
    bat = scene.parent / "rendre_cette_vue.bat"
    log = scene.parent / "vray_journal.txt"
    bat.write_text("\r\n".join([
        "@echo off", "chcp 65001 >nul", "title V-Ray Link - rendu",
        f'"{vray}" -sceneFile="{scene}" -imgFile="{image}" -display={1 if show else 0} -autoClose=0 -verboseLevel=3',
        "if errorlevel 1 (", "  echo.", "  echo *** V-Ray s'est arrete sur une erreur : fais une capture de cette fenetre. ***",
        "  pause", ")"]) + "\r\n", encoding="utf-8")
    try:
        log.unlink()
    except Exception:  # noqa: BLE001
        pass
    subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(scene.parent), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def launch_batch(vray: str, jobs: list[tuple[Path, Path]], folder: Path) -> Path:
    bat = jobs[0][0].parent / "rendre_les_vues.bat"
    lines = ["@echo off", "chcp 65001 >nul", "title V-Ray Link - rendu des vues"]
    for k, (scene, image) in enumerate(jobs, 1):
        lines.append(f'echo Vue {k}/{len(jobs)} : {image.stem}')
        lines.append(f'"{vray}" -sceneFile="{scene}" -imgFile="{image}" -display=1 -autoClose=1')
        lines.append("if errorlevel 1 (echo *** Erreur V-Ray sur cette vue : fais une capture. *** & pause)")
    lines += [f'explorer "{folder}"', "echo Termine.", "timeout /t 5"]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return bat


# ---------------------------------------------------------------- fenetre ---
def window(state: dict) -> None:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    proj = project_info()
    opts = state["options"]
    state["vray"] = find_vray(state.get("vray", ""))
    root = tk.Tk()
    root.title(f"V-Ray Link - version {VERSION}")
    root.attributes("-topmost", True)
    nb = ttk.Notebook(root)
    nb.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    outer = ttk.Frame(nb, padding=12)
    nb.add(outer, text="  Rendu  ")
    status = tk.StringVar(value="Choisis la vue, le soleil et la qualité, puis « Rendre cette vue ».")
    head = ttk.Frame(outer)
    head.grid(row=0, column=0, sticky="ew")
    ttk.Label(head, text="V-Ray Link", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
    ok = bool(state["vray"])
    ttk.Label(head, text=("● V-Ray trouvé" if ok else "● V-Ray introuvable"),
              foreground="#2f7a3e" if ok else "#a33").grid(row=0, column=1, sticky="w", padx=10)
    ttk.Label(head, text=f"Projet : {proj['name']}", foreground="#555").grid(row=1, column=0, columnspan=2, sticky="w")

    # --- vues
    box = ttk.LabelFrame(outer, text="Vues (coche celles à rendre en série)", padding=8)
    box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    listf = ttk.Frame(box)
    listf.grid(row=0, column=0, columnspan=4, sticky="ew")
    cam_vars: dict = {}
    current = tk.StringVar(value="")
    key = safe(proj["name"])
    autos: list = []

    def all_cams() -> list[dict]:
        return autos + state["cameras"].get(key, [])

    def refresh():
        for w in listf.winfo_children():
            w.destroy()
        cam_vars.clear()
        coches = state["coches"].get(key, [])
        for k, c in enumerate(all_cams()):
            v = tk.BooleanVar(value=c["nom"] in coches)
            cam_vars[c["nom"]] = v
            ttk.Checkbutton(listf, variable=v).grid(row=k, column=0)
            ttk.Radiobutton(listf, text=c["nom"] + ("" if c.get("auto") else "  (placée)"), value=c["nom"],
                            variable=current).grid(row=k, column=1, sticky="w")
            ttk.Label(listf, text=f"{c.get('focale', 24)} mm", foreground="#777").grid(row=k, column=2, sticky="w", padx=8)
        names = [c["nom"] for c in all_cams()]
        if current.get() not in names and names:
            current.set(names[0])

    def compute_autos():
        nonlocal autos
        try:
            autos = auto_cameras(archicad_bbox(("Wall", "Roof", "Column")) or archicad_bbox(), proj["north"])
        except Exception as error:  # noqa: BLE001
            autos = []
            status.set(f"Vues automatiques indisponibles : {error}")
        refresh()

    row = ttk.Frame(box)
    row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
    ttk.Label(row, text="Œil (m)").grid(row=0, column=0)
    eye_var = tk.DoubleVar(value=opts["oeil"])
    ttk.Spinbox(row, from_=0.3, to=200, increment=0.1, textvariable=eye_var, width=5).grid(row=0, column=1, padx=(2, 8))
    ttk.Label(row, text="Cible (m)").grid(row=0, column=2)
    tgt_var = tk.DoubleVar(value=opts["cible"])
    ttk.Spinbox(row, from_=0, to=200, increment=0.1, textvariable=tgt_var, width=5).grid(row=0, column=3, padx=(2, 8))
    ttk.Label(row, text="Focale").grid(row=0, column=4)
    foc_var = tk.IntVar(value=opts["focale"])
    ttk.Combobox(row, values=FOCALES, textvariable=foc_var, width=4, state="readonly").grid(row=0, column=5, padx=(2, 8))
    ttk.Button(row, text="Placer une caméra (2 clics)...", command=lambda: place()).grid(row=0, column=6)
    ttk.Button(row, text="Supprimer", command=lambda: remove()).grid(row=0, column=7, padx=4)
    vert_var = tk.BooleanVar(value=opts["verticales"])
    ttk.Checkbutton(box, text="Verticales droites (caméra horizontale)", variable=vert_var).grid(row=2, column=0, sticky="w",
                                                                                              pady=(4, 0))

    # --- qualite
    q = ttk.LabelFrame(outer, text="Qualité", padding=8)
    q.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    qual_var = tk.StringVar(value=opts["qualite"])
    for k, name in enumerate(QUALITES):
        ttk.Radiobutton(q, text=name, value=name, variable=qual_var).grid(row=0, column=k, sticky="w", padx=(0, 10))
    ttk.Label(q, text="Taille").grid(row=1, column=0, sticky="w", pady=(6, 0))
    size_var = tk.StringVar(value=opts["taille"])
    ttk.Combobox(q, values=list(TAILLES), textvariable=size_var, state="readonly", width=24
                 ).grid(row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))

    # --- soleil
    so = ttk.LabelFrame(outer, text="Soleil et ciel", padding=8)
    so.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    places = list(VILLES) + ([LIEU_ARCHICAD] if proj.get("archicad") else [])
    place_var = tk.StringVar(value=opts.get("lieu") if opts.get("lieu") in places else "Abidjan")
    ttk.Label(so, text="Lieu").grid(row=0, column=0, sticky="w")
    place_box = ttk.Combobox(so, values=places, textvariable=place_var, state="readonly", width=24)
    place_box.grid(row=0, column=1, columnspan=3, sticky="w")
    if proj.get("archicad"):
        ttk.Label(so, text=f"(Archicad : {proj['lieu']})", foreground="#777").grid(row=0, column=4, sticky="w", padx=6)
    apply_place(proj, place_var.get())
    ttk.Label(so, text="Date (jour / mois)").grid(row=1, column=0, sticky="w", pady=(4, 0))
    day_var, month_var = tk.IntVar(value=opts["jour"]), tk.IntVar(value=opts["mois"])
    ttk.Spinbox(so, from_=1, to=31, textvariable=day_var, width=4).grid(row=1, column=1, sticky="w", pady=(4, 0))
    ttk.Spinbox(so, from_=1, to=12, textvariable=month_var, width=4).grid(row=1, column=2, sticky="w", pady=(4, 0))
    hour_var = tk.DoubleVar(value=opts["heure"])
    hour_lbl = tk.StringVar()

    def show_hour(*_):
        apply_place(proj, place_var.get())
        h = float(hour_var.get())
        az, alt = sun_position(proj["lat"], proj["lon"], dt.date(2026, max(1, min(12, int(month_var.get() or 1))),
                                                                   max(1, min(28, int(day_var.get() or 1)))), h,
                               proj.get("fuseau", 0.0))
        hour_lbl.set(f"{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}  ·  soleil à {math.degrees(alt):.0f}° "
                     f"{'(nuit)' if alt < 0 else ''}")

    ttk.Label(so, text="Heure").grid(row=2, column=0, sticky="w", pady=(4, 0))
    ttk.Scale(so, from_=5.5, to=20.0, variable=hour_var, command=show_hour, length=260
              ).grid(row=2, column=1, columnspan=3, sticky="w", pady=(4, 0))
    ttk.Label(so, textvariable=hour_lbl).grid(row=3, column=1, columnspan=3, sticky="w")
    show_hour()
    place_box.bind("<<ComboboxSelected>>", show_hour)

    # --- ambiance
    am = ttk.LabelFrame(outer, text="Ambiance", padding=8)
    am.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    amb_var = tk.StringVar(value=opts["ambiance"])
    ttk.Label(am, text="Exposition").grid(row=0, column=0, sticky="w")
    ttk.Combobox(am, values=list(AMBIANCES), textvariable=amb_var, state="readonly", width=20).grid(row=0, column=1, padx=6)
    ttk.Label(am, text="Sol autour").grid(row=0, column=2, sticky="w")
    sol_var = tk.StringVar(value=opts["sol"])
    ttk.Combobox(am, values=list(SOLS), textvariable=sol_var, state="readonly", width=20).grid(row=0, column=3, padx=6)
    reuse_var = tk.BooleanVar(value=opts["reutiliser"])
    ttk.Checkbutton(am, text="Réutiliser la géométrie du dernier rendu (plus rapide si seuls la vue ou le soleil changent)",
                    variable=reuse_var).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

    report = tk.Text(outer, width=78, height=6, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=6, column=0, pady=(6, 0))
    ttk.Label(outer, textvariable=status, foreground="#555", wraplength=560).grid(row=7, column=0, sticky="w")

    def show(lines):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", "\n".join(lines))
        report.configure(state="disabled")

    def say(text):
        status.set(text)
        root.update_idletasks()

    def collect():
        try:
            opts.update(qualite=qual_var.get(), taille=size_var.get(), ambiance=amb_var.get(), sol=sol_var.get(),
                        jour=int(day_var.get()), mois=int(month_var.get()), heure=round(float(hour_var.get()), 2),
                        verticales=bool(vert_var.get()), lieu=place_var.get(), reutiliser=bool(reuse_var.get()), oeil=float(eye_var.get()),
                        cible=float(tgt_var.get()), focale=int(foc_var.get()))
        except (ValueError, tk.TclError):
            pass
        apply_place(proj, opts.get("lieu", "Abidjan"))
        state["coches"][key] = [n for n, v in cam_vars.items() if v.get()]
        save_settings(state)

    def place():
        collect()
        root.withdraw()
        try:
            T.focus_archicad()
            a, why = T.pick_point("V-Ray : clique la position de l'œil (caméra)")
            if a is None:
                show([f"Annulé. {why}"])
                return
            b, why = T.pick_point("V-Ray : clique le point regardé")
            if b is None:
                show([f"Annulé. {why}"])
                return
            z0 = story_level()
        finally:
            root.deiconify()
        n = len(state["cameras"].get(key, [])) + 1
        name = simpledialog.askstring("Nom de la caméra", "Nom de cette vue :", initialvalue=f"Vue {n}", parent=root)
        if not name:
            return
        cam = {"nom": name.strip(), "focale": int(opts["focale"]),
               "eye": [a["x"], a["y"], z0 + float(opts["oeil"])], "target": [b["x"], b["y"], z0 + float(opts["cible"])]}
        cams = [c for c in state["cameras"].get(key, []) if c["nom"] != cam["nom"]]
        state["cameras"][key] = cams + [cam]
        save_settings(state)
        refresh()
        current.set(cam["nom"])
        show([f"Caméra « {cam['nom']} » placée."])

    def remove():
        name = current.get()
        cams = state["cameras"].get(key, [])
        if not any(c["nom"] == name for c in cams):
            show(["Seules les caméras placées peuvent être supprimées (les vues automatiques se recalculent)."])
            return
        state["cameras"][key] = [c for c in cams if c["nom"] != name]
        save_settings(state)
        refresh()

    def check_vray() -> bool:
        if state["vray"] and Path(state["vray"]).is_file():
            return True
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="Où est vray.exe ?", filetypes=[("vray.exe", "vray.exe")], parent=root)
        if path:
            state["vray"] = path
            save_settings(state)
            return True
        messagebox.showerror("V-Ray introuvable", "Installe V-Ray (pour SketchUp) ou indique vray.exe.", parent=root)
        return False

    def render(names: list[str]):
        collect()
        if not names:
            show(["Aucune vue choisie."])
            return
        if not check_vray():
            return
        try:
            geo, ground, info = prepare_geometry(state, proj, say)
            folder = renders_dir(proj["name"])
            stamp = time.strftime("%Hh%M")
            jobs = []
            for name in names:
                cam = next(c for c in all_cams() if c["nom"] == name)
                scene = geo.parent / f"scene_{safe(name)}.vrscene"
                write_scene(scene, geo, cam, opts, proj, ground)
                jobs.append((scene, folder / f"{safe(name)}_{stamp}.png"))
            if len(jobs) == 1:
                launch(state["vray"], *jobs[0])
                say("Rendu lancé : la fenêtre V-Ray s'ouvre.")
            else:
                launch_batch(state["vray"], jobs, folder)
                say(f"{len(jobs)} rendus lancés l'un après l'autre.")
            show([f"Géométrie : {info}.", f"Images enregistrées dans : {folder}",
                  f"Qualité {opts['qualite']}, {opts['taille']}, {opts['ambiance']}, soleil {opts['jour']}/{opts['mois']} "
                  f"à {int(opts['heure']):02d}h{int(round(opts['heure'] % 1 * 60)) % 60:02d}.", "",
                  "Surfaces par type d'élément :"] + diagnostic(geo.parent))
        except ModuleNotFoundError:
            show(["Le module IFC (ifcopenshell) n'a pas pu s'installer tout seul.",
                  f"Dans une invite de commandes, tape :  \"{sys.executable}\" -m pip install ifcopenshell",
                  "puis relance le rendu."])
            say("Module manquant.")
        except Exception as error:  # noqa: BLE001
            show([f"Erreur : {error}"])
            say("Erreur.")

    def open_folder():
        folder = renders_dir(proj["name"])
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            show([str(folder)])

    buttons = ttk.Frame(outer)
    buttons.grid(row=5, column=0, sticky="e", pady=(8, 0))
    ttk.Button(buttons, text="Ouvrir le dossier des rendus", command=open_folder).grid(row=0, column=0, padx=3)
    ttk.Button(buttons, text="Rendre les vues cochées", command=lambda: render(
        [n for n, v in cam_vars.items() if v.get()])).grid(row=0, column=1, padx=3)
    ttk.Button(buttons, text="Rendre cette vue", command=lambda: render([current.get()] if current.get() else [])
               ).grid(row=0, column=2, padx=3)
    materials_tab(root, nb, state, proj, key)
    root.protocol("WM_DELETE_WINDOW", lambda: (collect(), root.destroy()))
    root.after(100, compute_autos)
    root.mainloop()


# ---------------------------------------------------------- onglet Materiaux ---
def project_surfaces(state: dict, key: str) -> tuple[dict, bool]:
    """Surfaces du projet -> couleur. (vrai, si elles viennent du dernier rendu = seulement celles utilisees)"""
    meta = data_dir() / key / "geometrie.json"
    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
        if info.get("couleurs"):
            return {n: tuple(c) for n, c in info["couleurs"].items()}, True
    except Exception:  # noqa: BLE001
        pass
    try:
        return {n: tuple(i["rgb"]) for n, i in archicad_surfaces()["name"].items()}, False
    except Exception:  # noqa: BLE001
        return {}, False


def materials_tab(root, nb, state: dict, proj: dict, key: str) -> None:
    import tkinter as tk
    from tkinter import ttk

    tab = ttk.Frame(nb, padding=10)
    nb.add(tab, text="  Matériaux  ")
    tab.columnconfigure(0, weight=2)
    tab.columnconfigure(1, weight=3)
    tab.rowconfigure(1, weight=1)
    msg = tk.StringVar(value="1. Choisis une ou plusieurs surfaces à gauche   2. Clique un matériau à droite   "
                             "3. « Associer » (ou double-clic sur le matériau).")
    cosmos_state = tk.StringVar(value="○ Cosmos non connecté")
    lib: list = []
    chosen = {"entry": None}
    cells: dict = {}
    images: dict = {}
    swatches: dict = {}

    # ---------------- gauche : surfaces
    left = ttk.LabelFrame(tab, text="Surfaces du projet", padding=6)
    left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
    left.rowconfigure(1, weight=1)
    left.columnconfigure(0, weight=1)
    sfilter = tk.StringVar()
    top = ttk.Frame(left)
    top.grid(row=0, column=0, sticky="ew")
    ttk.Label(top, text="Filtre").pack(side="left")
    ttk.Entry(top, textvariable=sfilter, width=18).pack(side="left", padx=4)
    ttk.Button(top, text="↻", width=3, command=lambda: load_surfaces(True)).pack(side="left")
    origin = ttk.Label(left, text="", foreground="#777", wraplength=330)
    tree = ttk.Treeview(left, columns=("mat", "ech"), show="tree headings", height=18, selectmode="extended")
    tree.heading("#0", text="Surface Archicad")
    tree.heading("mat", text="Matériau V-Ray")
    tree.heading("ech", text="Échelle")
    tree.column("#0", width=190)
    tree.column("mat", width=170)
    tree.column("ech", width=55, anchor="center")
    tree.grid(row=1, column=0, sticky="nsew", pady=4)
    sb = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
    sb.grid(row=1, column=1, sticky="ns", pady=4)
    tree.configure(yscrollcommand=sb.set)
    origin.grid(row=2, column=0, columnspan=2, sticky="w")
    surfaces: dict = {}

    def swatch(rgb):
        k = tuple(int(c * 255) for c in rgb)
        if k not in swatches:
            img = tk.PhotoImage(width=14, height=14)
            img.put("#%02x%02x%02x" % k, to=(0, 0, 14, 14))
            swatches[k] = img
        return swatches[k]

    def label_of(folder: str) -> str:
        return virtual_surface(folder)[9:]

    def fill_tree():
        sel = set(tree.selection())
        tree.delete(*tree.get_children())
        f = sfilter.get().strip().lower()
        assigned = state.get("cosmos") or {}
        for name in sorted(surfaces, key=lambda n: (n not in assigned, n.lower())):
            if f and f not in name.lower():
                continue
            a = assigned.get(name)
            mat = f"◆ {label_of(a['dossier'])}" if a else "auto (couleur Archicad)"
            ech = f"×{float(a.get('echelle', 1.0)):g}" if a else ""
            tree.insert("", "end", iid=name, text=" " + name, image=swatch(surfaces[name]), values=(mat, ech))
        keep = [i for i in sel if tree.exists(i)]
        if keep:
            tree.selection_set(keep)

    def load_surfaces(force=False):
        nonlocal surfaces
        found, from_render = project_surfaces(state, key)
        if found:
            surfaces = found
        origin.configure(text=("Surfaces vues au dernier rendu." if from_render else
                               "Toutes les surfaces Archicad (après un premier rendu, seules celles utilisées s'affichent)."))
        fill_tree()

    sfilter.trace_add("write", lambda *_: fill_tree())

    # ---------------- droite : bibliotheque Cosmos
    right = ttk.LabelFrame(tab, text="Matériaux V-Ray (Chaos Cosmos)", padding=6)
    right.grid(row=0, column=1, rowspan=2, sticky="nsew")
    right.columnconfigure(0, weight=1)
    right.rowconfigure(2, weight=1)
    bar = ttk.Frame(right)
    bar.grid(row=0, column=0, sticky="ew")
    ttk.Button(bar, text="🌐  Ouvrir Chaos Cosmos", command=lambda: open_cosmos()).pack(side="left")
    state_lbl = ttk.Label(bar, textvariable=cosmos_state, foreground="#777")
    state_lbl.pack(side="left", padx=8)
    srch = ttk.Frame(right)
    srch.grid(row=1, column=0, sticky="ew", pady=(6, 4))
    query = tk.StringVar()
    cat = tk.StringVar(value="Toutes")
    ttk.Label(srch, text="Rechercher").pack(side="left")
    ttk.Entry(srch, textvariable=query, width=20).pack(side="left", padx=4)
    cat_box = ttk.Combobox(srch, textvariable=cat, state="readonly", width=16, values=["Toutes"])
    cat_box.pack(side="left", padx=4)
    count = ttk.Label(srch, text="", foreground="#777")
    count.pack(side="left", padx=6)

    canvas = tk.Canvas(right, width=470, height=380, background="#fafafa", highlightthickness=0)
    canvas.grid(row=2, column=0, sticky="nsew")
    vs = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
    vs.grid(row=2, column=1, sticky="ns")
    canvas.configure(yscrollcommand=vs.set)
    grid = tk.Frame(canvas, background="#fafafa")
    canvas.create_window((0, 0), window=grid, anchor="nw")
    grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")
                    if str(nb.select()) == str(tab) else None)
    empty = ttk.Label(right, foreground="#777", wraplength=440, justify="left", text=(
        "Aucun matériau Cosmos sur ce PC pour l'instant.\n\nClique « Ouvrir Chaos Cosmos », choisis un matériau "
        "(Materials, Chaos Scans, V-Ray Material Library…) et télécharge-le : il apparaît ici tout seul."))

    def blank():
        if "blank" not in swatches:
            img = tk.PhotoImage(width=96, height=96)
            img.put("#e4e4e4", to=(0, 0, 96, 96))
            swatches["blank"] = img
        return swatches["blank"]

    def highlight(entry):
        chosen["entry"] = entry
        for folder, cell in cells.items():
            on = entry is not None and folder == entry["dossier"]
            cell.configure(background="#cfe6ff" if on else "#fafafa")
            for w in cell.winfo_children():
                w.configure(background="#cfe6ff" if on else "#fafafa")
        if entry:
            msg.set(f"Matériau choisi : {entry['nom']}  ({entry['categorie']}).  Clique « Associer » pour l'appliquer aux "
                    f"surfaces sélectionnées.")

    def fill_grid():
        for w in grid.winfo_children():
            w.destroy()
        cells.clear()
        q, c = query.get().strip().lower(), cat.get()
        shown = [e for e in lib if (c == "Toutes" or e["categorie"] == c) and
                 (not q or q in e["nom"].lower() or q in e["categorie"].lower())]
        count.configure(text=f"{len(shown)} / {len(lib)}")
        if not lib:
            empty.grid(row=2, column=0, sticky="nw", padx=10, pady=10)
        else:
            empty.grid_forget()
        cols = 4
        for k, e in enumerate(shown):
            cell = tk.Frame(grid, background="#fafafa", padx=4, pady=4, cursor="hand2")
            cell.grid(row=k // cols, column=k % cols, sticky="n")
            img = images.get(e["dossier"]) or blank()
            pic = tk.Label(cell, background="#fafafa", image=img)
            pic.pack()
            tk.Label(cell, text=e["nom"], background="#fafafa", wraplength=104, font=("Segoe UI", 8),
                     justify="center").pack()
            cells[e["dossier"]] = cell
            for w in (cell, *cell.winfo_children()):
                w.bind("<Button-1>", lambda _e, en=e: highlight(en))
                w.bind("<Double-Button-1>", lambda _e, en=e: (highlight(en), associate()))
        if chosen["entry"] and chosen["entry"]["dossier"] in cells:
            highlight(chosen["entry"])
        load_thumbs([e for e in shown if e["dossier"] not in images])

    def load_thumbs(todo):
        if not todo:
            return
        e = todo.pop(0)
        img = thumbnail(e)
        images[e["dossier"]] = img
        cell = cells.get(e["dossier"])
        if cell and img:
            cell.winfo_children()[0].configure(image=img)
        root.after(15, load_thumbs, todo)

    def load_library(select_name: str = ""):
        nonlocal lib
        lib = cosmos_library("Materials")
        cats = sorted({e["categorie"] for e in lib})
        cat_box.configure(values=["Toutes"] + cats)
        if select_name:
            hit = next((e for e in lib if select_name.lower().replace(" ", "") in e["nom"].lower().replace(" ", "")), lib[0]
                       if lib else None)
            if hit:
                query.set("")
                cat.set("Toutes")
                chosen["entry"] = hit
        fill_grid()

    query.trace_add("write", lambda *_: fill_grid())
    cat_box.bind("<<ComboboxSelected>>", lambda *_: fill_grid())

    # ---------------- actions
    acts = ttk.Frame(tab)
    acts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Button(acts, text="◀  Associer aux surfaces sélectionnées", command=lambda: associate()).grid(row=0, column=0, padx=3)
    ttk.Button(acts, text="Appliquer aux éléments sélectionnés dans Archicad",
               command=lambda: apply_selection()).grid(row=0, column=1, padx=3)
    ttk.Button(acts, text="Retirer (revenir à la couleur Archicad)", command=lambda: remove()).grid(row=0, column=2, padx=3)
    ttk.Label(acts, text="Échelle").grid(row=0, column=3, padx=(12, 2))
    scale_var = tk.DoubleVar(value=1.0)
    ttk.Spinbox(acts, from_=0.05, to=20, increment=0.25, textvariable=scale_var, width=5).grid(row=0, column=4)
    ttk.Button(acts, text="Appliquer l'échelle", command=lambda: set_scale()).grid(row=0, column=5, padx=3)
    sel_info = tk.StringVar()
    ttk.Label(tab, textvariable=sel_info, foreground="#555").grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
    ttk.Button(tab, text="Effacer les matériaux posés sur des éléments", command=lambda: clear_selection()
               ).grid(row=3, column=1, sticky="e", pady=(6, 0))
    ttk.Label(tab, textvariable=msg, foreground="#2a5d9f", wraplength=900).grid(row=4, column=0, columnspan=2, sticky="w",
                                                                            pady=(4, 0))

    def refresh_sel_info():
        n = len((state.get("selection") or {}).get(key) or {})
        sel_info.set(f"{n} élément(s) Archicad avec un matériau Cosmos posé directement." if n else "")

    def associate():
        e = chosen["entry"]
        names = list(tree.selection())
        if not e:
            msg.set("Choisis d'abord un matériau à droite.")
            return
        if not names:
            msg.set("Choisis d'abord une ou plusieurs surfaces à gauche (Ctrl+clic pour en prendre plusieurs).")
            return
        cosmos = state.setdefault("cosmos", {})
        for n in names:
            old = cosmos.get(n) or {}
            cosmos[n] = {"dossier": e["dossier"], "echelle": float(old.get("echelle", 1.0))}
        save_settings(state)
        fill_tree()
        msg.set(f"« {e['nom']} » associé à {len(names)} surface(s). Il sera utilisé au prochain rendu "
                f"(et dans tous tes projets qui ont ces surfaces).")

    def remove():
        names = list(tree.selection())
        cosmos = state.setdefault("cosmos", {})
        for n in names:
            cosmos.pop(n, None)
        save_settings(state)
        fill_tree()
        msg.set(f"{len(names)} surface(s) reviennent à la couleur Archicad.")

    def set_scale():
        names = [n for n in tree.selection() if n in (state.get("cosmos") or {})]
        try:
            k = max(0.01, float(scale_var.get()))
        except (ValueError, tk.TclError):
            return
        for n in names:
            state["cosmos"][n]["echelle"] = k
        save_settings(state)
        fill_tree()
        msg.set(f"Échelle ×{k:g} pour {len(names)} surface(s) : >1 = motif plus petit et plus serré, <1 = motif plus grand."
                if names else "Choisis d'abord des surfaces qui ont un matériau Cosmos.")

    def apply_selection():
        e = chosen["entry"]
        if not e:
            msg.set("Choisis d'abord un matériau à droite, puis sélectionne les éléments dans Archicad.")
            return
        try:
            els = tapir("GetSelectedElements").get("elements", [])
            if not els:
                msg.set("Rien n'est sélectionné dans Archicad : sélectionne des murs, dalles, objets… puis recommence.")
                return
            ids = tapir("GetIFCIdsOfElements", {"elements": els}).get("elementIFCIds", [])
        except Exception as error:  # noqa: BLE001
            msg.set(f"Lecture de la sélection impossible : {error}")
            return
        sel = state.setdefault("selection", {}).setdefault(key, {})
        n = 0
        for r in ids:
            gid = r.get("ifcId") if isinstance(r, dict) else None
            if gid:
                sel[gid] = e["dossier"]
                n += 1
        save_settings(state)
        refresh_sel_info()
        msg.set(f"« {e['nom']} » posé sur {n} élément(s) sélectionné(s). Au prochain rendu, le modèle est réexporté "
                f"pour en tenir compte.")

    def clear_selection():
        (state.get("selection") or {}).pop(key, None)
        save_settings(state)
        refresh_sel_info()
        msg.set("Matériaux posés sur des éléments effacés : les éléments reprennent le matériau de leur surface.")

    # ---------------- Cosmos
    host = {"h": None}

    def on_status(text):
        root.after(0, lambda: cosmos_state.set(("● " if "connecté" in text else "○ ") + text))

    def on_import(name):
        def ui():
            load_library(name)
            msg.set(f"Nouveau matériau reçu de Cosmos : « {name} ». Choisis des surfaces à gauche puis « Associer ».")
            try:
                root.deiconify()
                root.lift()
            except Exception:  # noqa: BLE001
                pass
        root.after(0, ui)

    def connect():
        if host["h"] is None:
            host["h"] = CosmosHost(on_import, on_status)
        cosmos_state.set("○ Connexion à Cosmos...")
        threading.Thread(target=host["h"].start, daemon=True).start()

    def open_cosmos():
        connect()

        def go():
            for _ in range(40):
                if host["h"].ports:
                    break
                time.sleep(0.5)
            import webbrowser
            webbrowser.open(cosmos_url())
        threading.Thread(target=go, daemon=True).start()
        msg.set("Cosmos s'ouvre dans ton navigateur. Télécharge un matériau (bouton de téléchargement / import) : "
                "il arrive ici automatiquement.")

    def on_tab(_e=None):
        if str(nb.select()) == str(tab) and not surfaces:
            load_surfaces()
            load_library()
            refresh_sel_info()
            connect()

    nb.bind("<<NotebookTabChanged>>", on_tab)


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
