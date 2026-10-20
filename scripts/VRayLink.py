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

VERSION = "20/10 - v3.2 (objets suivis dans le plan)"
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
           "1080 × 1920 (story)": (1080, 1920), "2000 × 2000 (carré)": (2000, 2000),
           "3508 × 2480 (A3/A4 paysage, 300 dpi A4)": (3508, 2480), "4961 × 3508 (A3 paysage 300 dpi)": (4961, 3508),
           "3840 × 1646 (cinéma 21:9)": (3840, 1646), "2480 × 3508 (A4 portrait)": (2480, 3508)}
MOTEURS = {"CPU (processeur)": "", "GPU CUDA (carte NVIDIA)": "-rtEngine=5", "GPU RTX (NVIDIA RTX)": "-rtEngine=7"}
FORMATS = {"PNG": ".png", "JPEG": ".jpg", "EXR 32 bits (multicouche, retouche)": ".exr"}
CANAUX = {
    "Occlusion ambiante (AO)": 'RenderChannelExtraTex ch_ao { name="AO"; consider_for_aa=1; texmap=ch_ao_tex; }\n'
                               "TexDirt ch_ao_tex { radius=0.6; subdivs=8; white_color=AColor(1,1,1,1); black_color=AColor(0,0,0,1); }",
    "Profondeur (Z-depth)": 'RenderChannelZDepth ch_z { name="ZDepth"; depth_from_camera=1; depth_black=0; depth_white=80; }',
    "Masque par matériau (Material ID)": 'RenderChannelMtlID ch_mid { name="MaterialID"; }',
    "Cryptomatte (masques Photoshop/Nuke)": 'RenderChannelCryptomatte ch_crypto { name="Cryptomatte"; id_type=1; }',
    "Normales": 'RenderChannelNormals ch_n { name="Normals"; }',
}
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
    # Archicad n'exporte que la selection si quelque chose est selectionne : on deselectionne le temps de l'export
    sel = []
    try:
        sel = [{"elementId": e["elementId"]} for e in tapir("GetSelectedElements").get("elements", [])
               if isinstance(e, dict) and "elementId" in e]
        if sel:
            tapir("ChangeSelectionOfElements", {"addElementsToSelection": [], "removeElementsFromSelection": sel})
    except Exception:  # noqa: BLE001
        sel = []
    try:
        res = tapir("IFCFileOperation", {"method": "save", "ifcFilePath": str(path), "fileType": "ifc"}, timeout=1800)
    finally:
        if sel:
            try:
                tapir("ChangeSelectionOfElements", {"addElementsToSelection": sel, "removeElementsFromSelection": []})
            except Exception:  # noqa: BLE001
                pass
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
    """Un materiau par surface : materiau Cosmos s'il est associe, sinon couleur Archicad + reglages devines.
    Ecrit aussi materiaux_sans_scan.vrscene (materiaux scannes .vrscan remplaces) : secours si V-Ray plante."""
    used = []
    plain = path.with_name("materiaux_sans_scan.vrscene")
    with path.open("w", encoding="utf-8") as out, plain.open("w", encoding="utf-8") as out2:
        out.write("// Materiaux (V-Ray Link)\n")
        out2.write("// Materiaux sans scans (secours V-Ray Link)\n")
        for name, info in mats.items():
            mid = mat_id(name)
            m = guess_material(name, info, library)
            simple = f"// surface : {name}\n" + _brdf(mid, m) + f"MtlSingleBRDF {mid} {{\n  brdf={mid}_brdf;\n}}\n"
            a = assigned.get(name)
            if a and a.get("dossier"):
                try:
                    folder = Path(a["dossier"])
                    vrmat = sorted(folder.glob("*.vrmat"), key=_lod)[0]
                    fixed, top = vrmat_for_render(vrmat)
                    block = (f"// {name} <- Cosmos : {folder.name}\nMtlVRmat {mid} {{\n  filename=\"{str(fixed).replace(chr(92), '/')}\";\n"
                             f"  mtlname=\"{top}\";\n}}\n")
                    out.write(block)
                    scan = ".vrscan" in fixed.read_text("utf-8", "replace").lower()
                    out2.write(simple if scan else block)
                    used.append(f"{name} → {virtual_surface(str(folder))[9:]}" + (" (scanné)" if scan else ""))
                    continue
                except Exception as error:  # noqa: BLE001
                    report(f"Matériau Cosmos indisponible pour {name} : {error}")
            out.write(simple)
            out2.write(simple)
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



# --------------------------------------------------------------- cameras ---
CALQUE_CAMERAS = "V-Ray Link - Caméras"


def camera_cone(cam: dict, length: float | None = None) -> tuple:
    """(oeil, bord gauche, bord droit, cible) en plan, pour dessiner l'angle de vue."""
    ex, ey = cam["eye"][0], cam["eye"][1]
    tx, ty = cam["target"][0], cam["target"][1]
    d = math.hypot(tx - ex, ty - ey) or 1.0
    ux, uy = (tx - ex) / d, (ty - ey) / d
    half = math.atan(18.0 / float(cam.get("focale", 24)))          # demi-angle horizontal (capteur 36 mm)
    L = length or max(d, 4.0)
    edge = L / math.cos(half)
    left = (ex + edge * (ux * math.cos(half) - uy * math.sin(half)), ey + edge * (ux * math.sin(half) + uy * math.cos(half)))
    right = (ex + edge * (ux * math.cos(-half) - uy * math.sin(-half)), ey + edge * (ux * math.sin(-half) + uy * math.cos(-half)))
    return (ex, ey), left, right, (tx, ty)


def plan_footprint(key: str, cells: int = 140):
    """Plan du modele vu de dessus, d'apres le dernier export : {"x0","y0","pas","sols":[[i,j]], "murs":[[i,j]]}."""
    d = data_dir() / key
    cache, out = d / "geometrie.pkl", d / "plan_apercu.json"
    try:
        stamp = cache.stat().st_mtime
    except OSError:
        return None
    try:
        saved = json.loads(out.read_text(encoding="utf-8"))
        if saved.get("stamp") == stamp:
            return saved
    except Exception:  # noqa: BLE001
        pass
    try:
        meshes, _mats, offset, _ground = pickle.loads(cache.read_bytes())
    except Exception:  # noqa: BLE001
        return None
    ox, oy = offset[0], offset[1]
    xs = [v[0] for verts, _f in meshes.values() for v in verts]
    ys = [v[1] for verts, _f in meshes.values() for v in verts]
    if not xs:
        return None
    x0, y0 = min(xs), min(ys)
    step = max(max(xs) - x0, max(ys) - y0) / cells or 1.0
    sols, murs = set(), set()

    def line(a, b, into):
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / step) + 1
        for t in range(n + 1):
            f = t / n
            into.add((int((a[0] + (b[0] - a[0]) * f - x0) / step), int((a[1] + (b[1] - a[1]) * f - y0) / step)))

    for verts, faces in meshes.values():
        for t in range(0, len(faces), 3):
            p = [verts[faces[t]], verts[faces[t + 1]], verts[faces[t + 2]]]
            area = (p[1][0] - p[0][0]) * (p[2][1] - p[0][1]) - (p[2][0] - p[0][0]) * (p[1][1] - p[0][1])
            if abs(area) < step * step * 0.05:                      # face verticale (mur) : un trait
                line(p[0], p[1], murs)
                line(p[1], p[2], murs)
                continue
            i0, i1 = int((min(q[0] for q in p) - x0) / step), int((max(q[0] for q in p) - x0) / step)
            j0, j1 = int((min(q[1] for q in p) - y0) / step), int((max(q[1] for q in p) - y0) / step)
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    cx, cy = x0 + (i + .5) * step, y0 + (j + .5) * step
                    s1 = (p[1][0] - p[0][0]) * (cy - p[0][1]) - (p[1][1] - p[0][1]) * (cx - p[0][0])
                    s2 = (p[2][0] - p[1][0]) * (cy - p[1][1]) - (p[2][1] - p[1][1]) * (cx - p[1][0])
                    s3 = (p[0][0] - p[2][0]) * (cy - p[2][1]) - (p[0][1] - p[2][1]) * (cx - p[2][0])
                    if (s1 >= 0 and s2 >= 0 and s3 >= 0) or (s1 <= 0 and s2 <= 0 and s3 <= 0):
                        sols.add((i, j))
    res = {"stamp": stamp, "x0": x0 + ox, "y0": y0 + oy, "pas": step,
           "sols": sorted(sols - murs), "murs": sorted(murs)}
    try:
        out.write_text(json.dumps(res), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return res


def _layer_index(name: str) -> int:
    rows = tapir("GetAttributesByType", {"attributeType": "Layer"}).get("attributes", [])
    found = next((r for r in rows if r.get("name") == name), None)
    if not found:
        tapir("CreateLayers", {"layerDataArray": [{"name": name}]})
        rows = tapir("GetAttributesByType", {"attributeType": "Layer"}).get("attributes", [])
        found = next((r for r in rows if r.get("name") == name), None)
    return int(found["index"]) if found else 0


def erase_cameras_in_plan(state: dict, key: str) -> None:
    guids = (state.get("traces") or {}).pop(key, [])
    for i in range(0, len(guids), 300):
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in guids[i:i + 300]]})
        except Exception:  # noqa: BLE001
            pass


def draw_cameras_in_plan(cams: list, state: dict, key: str, current: str = "") -> int:
    """Dessine chaque camera dans le plan (calque « V-Ray Link - Caméras ») : appareil, cone de vision, nom."""
    erase_cameras_in_plan(state, key)
    story = tapir("GetStories").get("actStory", 0)
    layer = _layer_index(CALQUE_CAMERAS)
    created = []
    for cam in cams:
        (ex, ey), left, right, (tx, ty) = camera_cone(cam)
        pen = 20 if cam["nom"] == current else 3
        dx, dy = tx - ex, ty - ey
        d = math.hypot(dx, dy) or 1.0
        ux, uy, s = dx / d, dy / d, 0.35
        body = [(ex - ux * s - uy * s, ey - uy * s + ux * s), (ex - ux * s + uy * s, ey - uy * s - ux * s),
                (ex + uy * s, ey - ux * s), (ex - uy * s, ey + ux * s), (ex - ux * s - uy * s, ey - uy * s + ux * s)]
        polys = [[(ex, ey), left, right, (ex, ey)], body, [(ex, ey), (tx, ty)]]
        data = [{"floorInd": story, "layerIndex": layer, "linePenIndex": pen,
                 "coordinates": [{"x": x, "y": y} for x, y in pl]} for pl in polys]
        try:
            res = tapir("CreatePolylines", {"polylinesData": data}).get("elements", [])
            mine = [r["elementId"]["guid"] for r in res if "elementId" in r]
            txt = tapir("CreateTexts", {"textsData": [{
                "coordinate": {"x": ex - ux * 0.9, "y": ey - uy * 0.9, "z": 0.0}, "text": cam["nom"],
                "floorIndex": story, "style": {"height": 2.5, "bold": True, "penIndex": pen,
                                               "anchor": "MiddleMiddle", "justification": "Center"}}]}).get("elements", [])
            mine += [r["elementId"]["guid"] for r in txt if "elementId" in r]
        except Exception:  # noqa: BLE001
            continue
        if mine:
            try:
                tapir("SetDetailsOfElements", {"elementsWithDetails": [
                    {"elementId": {"guid": g}, "details": {"layerIndex": layer}} for g in mine]})
                tapir("CreateGroups", {"elementGroups": [{"elements": [{"elementId": {"guid": g}} for g in mine]}]})
            except Exception:  # noqa: BLE001
                pass
        created += mine
    state.setdefault("traces", {})[key] = created
    return len(cams)



# ------------------------------------------------------------ visionneuse 3D ---
# Une page web locale (three.js) affiche le modele du dernier export : on s'y deplace comme dans la 3D d'Archicad,
# puis « Rendre cette vue » / « Enregistrer la vue » renvoient la camera a V-Ray Link.
def viewer_mesh(key: str) -> bytes:
    """Modele en binaire pour la visionneuse : nv, ni, positions f32, couleurs u8 (rgba), indices u32 (cache)."""
    d = data_dir() / key
    cache, out = d / "geometrie.pkl", d / "visionneuse.bin"
    if out.exists() and out.stat().st_mtime >= cache.stat().st_mtime:
        data = out.read_bytes()
        if _mesh_ok(data):
            return data
    meshes, mats, offset, _ground = pickle.loads(cache.read_bytes())
    import array
    pos, col, idx = array.array("f"), array.array("B"), array.array("I")
    for name, (verts, faces) in meshes.items():
        if not faces:
            continue
        base = len(pos) // 3
        rgb = (mats.get(name) or {}).get("rgb", (0.75, 0.75, 0.75))
        c = [max(0, min(255, int(float(v) * 255))) for v in rgb[:3]] + [255]
        for x, y, z in verts:
            pos.extend((x + offset[0], y + offset[1], z + offset[2]))
            col.extend(c)
        idx.extend(base + f for f in faces)
    if sys.byteorder != "little":
        pos.byteswap()
        idx.byteswap()
    data = struct.pack("<II", len(pos) // 3, len(idx)) + pos.tobytes() + col.tobytes() + idx.tobytes()
    if not _mesh_ok(data):
        raise RuntimeError("modèle 3D vide ou incohérent (refais un rendu pour réexporter)")
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    return data


def _mesh_ok(data: bytes) -> bool:
    if len(data) < 8:
        return False
    nv, ni = struct.unpack("<II", data[:8])
    return nv > 0 and ni > 0 and len(data) == 8 + nv * 16 + ni * 4


class ViewerServer:
    """Petit serveur web local : /, /modele.bin, /infos (JSON), POST /camera."""

    def __init__(self, key: str, infos, on_camera):
        import http.server
        owner = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, body: bytes, ctype: str):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                view = memoryview(body)
                for i in range(0, len(view), 1 << 20):
                    self.wfile.write(view[i:i + (1 << 20)])
                self.wfile.flush()

            def do_GET(self):
                try:
                    if self.path.startswith("/api/geometrie"):
                        # en JSON (base64) : les gestionnaires de telechargement (IDM...) capturent les .bin
                        import base64
                        body = b'{"d":"' + base64.b64encode(viewer_mesh(key)) + b'"}'
                        self._send(body, "application/json")
                    elif self.path.startswith("/api/etat"):
                        self._send(json.dumps(owner.etat()).encode("utf-8"), "application/json")
                    elif self.path.startswith("/api/hdri"):
                        import base64
                        src = (infos() if callable(infos) else infos).get("_hdri_path", "")
                        body = b'{"d":"' + (base64.b64encode(Path(src).read_bytes()) if src else b"") + b'"}'
                        self._send(body, "application/json")
                    elif self.path.startswith("/infos"):
                        self._send(json.dumps(infos() if callable(infos) else infos).encode("utf-8"), "application/json")
                    else:
                        self._send(VIEWER_HTML.encode("utf-8"), "text/html; charset=utf-8")
                except (ConnectionError, OSError):
                    pass
                except Exception as error:  # noqa: BLE001
                    try:
                        self.send_error(500, str(error)[:200])
                    except Exception:  # noqa: BLE001
                        pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0) or 0)
                try:
                    msg = json.loads(self.rfile.read(n).decode("utf-8"))
                    owner.on_camera(msg)
                    self._send(b'{"ok":true}', "application/json")
                except Exception as error:  # noqa: BLE001
                    self.send_error(400, str(error))

        self.on_camera = on_camera
        self.etat = lambda: {}

        class Quiet(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):     # pas de fenetre d'erreur Tapir pour une page fermee
                try:
                    import traceback
                    (data_dir() / "visionneuse_erreurs.txt").open("a", encoding="utf-8").write(traceback.format_exc() + "\n")
                except Exception:  # noqa: BLE001
                    pass

        self.httpd = Quiet(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def open(self) -> None:
        url = f"http://127.0.0.1:{self.port}/"
        for edge in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                     r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
            if Path(edge).exists():
                subprocess.Popen([edge, f"--app={url}", "--window-size=1280,820"])
                return
        import webbrowser
        webbrowser.open(url)


VIEWER_HTML = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>V-Ray Link · Visionneuse 3D</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#16181c;--panel:#20232a;--line:#30343d;--txt:#e8e6e1;--mute:#9aa0aa;--acc:#e0702a}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);font:13px "Segoe UI",system-ui,sans-serif}
#app{display:grid;grid-template-columns:1fr 270px;height:100%}
#stage{position:relative;display:flex;align-items:center;justify-content:center;overflow:hidden}
#frame{position:relative;box-shadow:0 0 0 1px var(--line),0 0 0 9999px rgba(0,0,0,.55)}
canvas{display:block}
#hud{position:absolute;left:12px;bottom:10px;color:var(--mute);font-size:12px;line-height:1.5;pointer-events:none}
#load{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--mute);font-size:15px}
aside{background:var(--panel);border-left:1px solid var(--line);padding:16px;display:flex;flex-direction:column;gap:14px;overflow:auto}
h1{font-size:15px;margin:0}h1 b{color:var(--acc)}
label{display:block;color:var(--mute);font-size:12px;margin-bottom:4px}
select,input{width:100%;background:#15171b;color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:7px 8px;font:inherit}
.row{display:flex;gap:6px}.row>*{flex:1}
button{background:#2b2f37;color:var(--txt);border:1px solid var(--line);border-radius:7px;padding:9px 10px;font:inherit;cursor:pointer}
button:hover{border-color:#4a505c}button.main{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
.seg{display:flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}.seg button{flex:1;border:0;border-radius:0}
.seg button.on{background:#3a3f4a;color:#fff}
#msg{color:var(--mute);font-size:12px;min-height:32px}
kbd{background:#2b2f37;border:1px solid var(--line);border-radius:4px;padding:0 4px;font-size:11px}
</style></head><body>
<div id="app"><div id="stage"><div id="frame"><div id="load">Chargement du modèle…</div></div>
<div id="hud"></div></div>
<aside>
<h1>V-Ray Link · <b>Visionneuse 3D</b></h1>
<div><label>Aller à une vue</label><select id="cams"></select></div>
<div><label>Navigation</label><div class="seg"><button id="mOrbit" class="on">Tourner autour</button><button id="mWalk">Marcher</button></div></div>
<div class="row"><div><label>Focale</label><select id="foc"></select></div>
<div><label>Hauteur yeux (m)</label><input id="eyeh" type="number" step="0.1" value="1.6"></div></div>
<button id="eyeBtn">Se mettre à hauteur d'yeux</button>
<div><label>Soleil : <span id="sunTxt"></span></label>
<input id="hour" type="range" min="5.5" max="19.5" step="0.05" style="padding:0">
<div class="row" style="margin-top:6px"><div><label>Jour</label><input id="day" type="number" min="1" max="31"></div>
<div><label>Mois</label><input id="month" type="number" min="1" max="12"></div></div>
<div style="margin-top:6px"><label>Intensité du soleil : <span id="intTxt"></span></label>
<input id="sunInt" type="range" min="0" max="3" step="0.05" style="padding:0"></div>
<div class="row" style="margin-top:6px"><button id="play">▶ Course du soleil</button></div>
<button id="useSun" style="width:100%;margin-top:6px">Utiliser cette heure pour les rendus</button></div>
<div id="hdriBox"><label>Ciel HDRI : <span id="rotTxt"></span></label>
<input id="hdriRot" type="range" min="0" max="360" step="1" style="padding:0">
<label style="margin-top:6px;display:flex;gap:6px;align-items:center"><input id="hdriShow" type="checkbox" checked style="width:auto"> Afficher la HDRI</label>
<div id="hdriMsg" style="color:var(--mute);font-size:12px"></div></div>
<div><label>Végétation : <span id="vegTxt"></span></label>
<label style="display:flex;gap:6px;align-items:center"><input id="vegShow" type="checkbox" checked style="width:auto"> Afficher herbe / plantes / objets</label></div>
<div><label>Nom de la vue</label><input id="name" value="Vue 3D"></div>
<button id="save">Enregistrer la vue</button>
<button id="render" class="main">Rendre cette vue</button>
<div id="msg"></div>
<div style="color:var(--mute);font-size:12px;line-height:1.7">
<b style="color:var(--txt)">Tourner autour</b> : clic gauche = tourner, clic droit = déplacer, molette = avancer.<br>
<b style="color:var(--txt)">Marcher</b> : clic gauche glissé = regarder, <kbd>Z</kbd><kbd>Q</kbd><kbd>S</kbd><kbd>D</kbd> ou flèches = avancer / côtés, <kbd>A</kbd>/<kbd>E</kbd> = monter / descendre, <kbd>Maj</kbd> = plus vite.<br>
Le cadre clair = l'image exacte du rendu.</div>
</aside></div>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
<script>
window.addEventListener("error",e=>{const l=document.getElementById("load");if(l)l.textContent="Erreur : "+(e.message||e)});
window.addEventListener("unhandledrejection",e=>{const l=document.getElementById("load");if(l)l.textContent="Erreur : "+(e.reason&&e.reason.message||e.reason)});
setTimeout(()=>{const l=document.getElementById("load");if(l&&!window.__three)l.textContent="Le moteur 3D (three.js) ne se charge pas : vérifie la connexion Internet."},15000);
</script>
<script type="module">
import * as THREE from "three";
window.__three=true;
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {RGBELoader} from "three/addons/loaders/RGBELoader.js";
import {EXRLoader} from "three/addons/loaders/EXRLoader.js";
const $=id=>document.getElementById(id);
const r0=await fetch("/infos"); if(!r0.ok) throw new Error("infos : "+r0.status+" "+(await r0.text()).slice(0,200));
const info=await r0.json(); $("load").textContent="Chargement du modèle…";
const aspect=info.largeur/info.hauteur;
const frame=$("frame"), stage=$("stage");
const renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.shadowMap.enabled=true; renderer.shadowMap.type=THREE.PCFSoftShadowMap;
frame.appendChild(renderer.domElement);
const scene=new THREE.Scene(); scene.background=new THREE.Color(0xbcd3e6);
scene.fog=new THREE.Fog(0xbcd3e6,400,2000);
const cam=new THREE.PerspectiveCamera(50,aspect,0.05,5000); cam.up.set(0,0,1);
const hemi=new THREE.HemisphereLight(0xffffff,0x8a8f7a,0.75); scene.add(hemi);
const sun=new THREE.DirectionalLight(0xfff4e0,2.2); sun.castShadow=true; sun.shadow.mapSize.set(4096,4096);
sun.shadow.bias=-0.0004; sun.shadow.normalBias=0.03; scene.add(sun); scene.add(sun.target);
const ground=new THREE.Mesh(new THREE.PlaneGeometry(4000,4000),new THREE.MeshLambertMaterial({color:0x7d9a5c}));
ground.position.z=info.sol-0.01; ground.receiveShadow=true; scene.add(ground);
const controls=new OrbitControls(cam,renderer.domElement); controls.enableDamping=true; controls.dampingFactor=0.12;
function fit(){const W=stage.clientWidth-24,H=stage.clientHeight-24;let w=W,h=W/aspect;if(h>H){h=H;w=H*aspect}
 renderer.setSize(Math.floor(w),Math.floor(h)); cam.aspect=aspect; cam.updateProjectionMatrix();}
addEventListener("resize",fit); fit();
function setFocal(f){const hf=2*Math.atan(18/f); cam.fov=THREE.MathUtils.radToDeg(2*Math.atan(Math.tan(hf/2)/aspect)); cam.updateProjectionMatrix();}
for(const f of info.focales){const o=document.createElement("option");o.value=f;o.textContent=f+" mm";$("foc").appendChild(o)}
$("foc").value=24; setFocal(24); $("foc").onchange=()=>setFocal(+$("foc").value);
function goTo(c){cam.position.set(...c.eye); controls.target.set(...c.target); $("foc").value=c.focale||24; setFocal(+$("foc").value);
 if(!c.auto) $("name").value=c.nom; controls.update();}
info.cameras.forEach((c,i)=>{const o=document.createElement("option");o.value=i;o.textContent=c.nom;$("cams").appendChild(o)});
$("cams").onchange=()=>goTo(info.cameras[+$("cams").value]);
// modele
const r1=await fetch("/api/geometrie"); if(!r1.ok) throw new Error("modèle : "+r1.status+" "+(await r1.text()).slice(0,200));
const b64=(await r1.json()).d; const raw=atob(b64); const u8=new Uint8Array(raw.length);
for(let i=0;i<raw.length;i++)u8[i]=raw.charCodeAt(i);
const buf=u8.buffer;
if(buf.byteLength<8) throw new Error("modèle reçu vide ("+buf.byteLength+" octets)");
const dv=new DataView(buf); const nv=dv.getUint32(0,true), ni=dv.getUint32(4,true);
if(buf.byteLength!==8+nv*16+ni*4) throw new Error(`modèle incomplet (${buf.byteLength} octets reçus, ${8+nv*16+ni*4} attendus)`);
let o=8; const pos=new Float32Array(buf,o,nv*3); o+=nv*12; const col=new Uint8Array(buf,o,nv*4); o+=nv*4;
const idx=new Uint32Array(buf.slice(o,o+ni*4));
let g=new THREE.BufferGeometry(); g.setAttribute("position",new THREE.BufferAttribute(pos,3));
g.setAttribute("color",new THREE.BufferAttribute(col,4,true)); g.setIndex(new THREE.BufferAttribute(idx,1));
g=g.toNonIndexed(); g.computeVertexNormals();
const model=new THREE.Mesh(g,new THREE.MeshLambertMaterial({vertexColors:true,side:THREE.DoubleSide}));
for(const t of (info.arbres||[])){const k=t[3]||1;const trunk=new THREE.Mesh(new THREE.CylinderGeometry(.15*k,.2*k,2.5*k,8),new THREE.MeshLambertMaterial({color:0x6b4f35}));
 trunk.rotation.x=Math.PI/2;trunk.position.set(t[0],t[1],t[2]+1.25*k);trunk.castShadow=true;scene.add(trunk);
 const crown=new THREE.Mesh(new THREE.IcosahedronGeometry(2.2*k,1),new THREE.MeshLambertMaterial({color:0x4f7a3a,flatShading:true}));
 crown.position.set(t[0],t[1],t[2]+4.2*k);crown.castShadow=true;crown.receiveShadow=true;scene.add(crown)}
model.castShadow=true; model.receiveShadow=true; scene.add(model);
g.computeBoundingBox(); const bb=g.boundingBox, mid=bb.getCenter(new THREE.Vector3()), rad=bb.getSize(new THREE.Vector3()).length()/2+5;
// soleil (formules NOAA, comme pour le rendu V-Ray)
function sunDir(h,day,month){const n=Math.floor((Date.UTC(2026,month-1,day)-Date.UTC(2026,0,0))/864e5);
 const g=2*Math.PI/365*(n-1+(h-12)/24);
 const eqt=229.18*(0.000075+0.001868*Math.cos(g)-0.032077*Math.sin(g)-0.014615*Math.cos(2*g)-0.040849*Math.sin(2*g));
 const decl=0.006918-0.399912*Math.cos(g)+0.070257*Math.sin(g)-0.006758*Math.cos(2*g)+0.000907*Math.sin(2*g)-0.002697*Math.cos(3*g)+0.00148*Math.sin(3*g);
 const tst=h*60+eqt+4*info.lon-60*info.fuseau, ha=(tst/4-180)*Math.PI/180, la=info.lat*Math.PI/180;
 const cz=Math.sin(la)*Math.sin(decl)+Math.cos(la)*Math.cos(decl)*Math.cos(ha), zen=Math.acos(Math.max(-1,Math.min(1,cz)));
 const az=(Math.atan2(Math.sin(ha),Math.cos(ha)*Math.sin(la)-Math.tan(decl)*Math.cos(la))+Math.PI)%(2*Math.PI), alt=Math.PI/2-zen;
 const a=info.north-az; return {alt,az,v:new THREE.Vector3(Math.cos(alt)*Math.cos(a),Math.cos(alt)*Math.sin(a),Math.sin(alt))}}
const sc=sun.shadow.camera; sc.left=-rad; sc.right=rad; sc.top=rad; sc.bottom=-rad; sc.near=1; sc.far=rad*4; sc.updateProjectionMatrix();
const COMPASS=["N","NE","E","SE","S","SO","O","NO"];
function updateSun(){const h=+$("hour").value, d=Math.max(1,Math.min(31,+$("day").value||1)), m=Math.max(1,Math.min(12,+$("month").value||1));
 const s=sunDir(h,d,m); const up=Math.max(s.alt,0.0);
 sun.position.copy(mid).addScaledVector(s.v,rad*2); sun.target.position.copy(mid);
 const k=+$("sunInt").value; $("intTxt").textContent=Math.round(k*100)+" %";
 sun.intensity=s.alt>0?2.2*k*Math.min(1,s.alt*4+0.15):0; hemi.intensity=s.alt>0?0.75:0.25;
 const hh=Math.floor(h), mm=Math.round((h%1)*60)%60;
 $("sunTxt").textContent=`${String(hh).padStart(2,"0")}:${String(mm).padStart(2,"0")} · hauteur ${(s.alt*180/Math.PI).toFixed(0)}° · ${COMPASS[Math.round(s.az/(Math.PI/4))%8]}${s.alt<=0?" (nuit)":""}`;}
$("hour").value=info.heure; $("day").value=info.jour; $("month").value=info.mois; $("sunInt").value=info.intensite??1;
for(const id of ["hour","day","month","sunInt"]) $(id).addEventListener("input",updateSun);
updateSun();
let playing=null; $("play").onclick=()=>{if(playing){clearInterval(playing);playing=null;$("play").textContent="▶ Course du soleil";return}
 $("play").textContent="■ Arrêter"; playing=setInterval(()=>{let h=+$("hour").value+0.05; if(h>19.5)h=5.5; $("hour").value=h; updateSun()},40)};
$("useSun").onclick=async()=>{await fetch("/camera",{method:"POST",headers:{"Content-Type":"application/json"},
 body:JSON.stringify({action:"soleil",heure:+$("hour").value,jour:+$("day").value,mois:+$("month").value,intensite:+$("sunInt").value,hdri_rot:+$("hdriRot").value})});
 $("msg").textContent="Heure et date envoyées à V-Ray Link : les rendus utiliseront ce soleil."};
const edges=new THREE.LineSegments(new THREE.EdgesGeometry(g,30),new THREE.LineBasicMaterial({color:0x333333,transparent:true,opacity:.35}));
scene.add(edges);
$("load").remove();
const vegGroup=new THREE.Group(); scene.add(vegGroup);
for(const ob of (info.objets||[])){const k=ob.echelle||1, t=(ob.taille&&ob.taille.length)?ob.taille:[0.8,0.8,1.0,0];
 const m=new THREE.Mesh(new THREE.BoxGeometry(t[0]*k,t[1]*k,t[2]*k),new THREE.MeshLambertMaterial({color:0xe0702a,transparent:true,opacity:.6}));
 m.position.set(ob.x,ob.y,ob.z+(t[3]+t[2]/2)*k); m.rotation.z=(ob.rot||0)*Math.PI/180; m.castShadow=true; vegGroup.add(m)}
// herbe / plantes : instances legeres (un seul objet par plante, des milliers de copies)
let nVeg=0;
for(const v of (info.vegetation||[])){const t=(v.taille&&v.taille.length)?v.taille:[0.4,0.4,0.1,0], k=v.echelle||1;
 const grass=v.type.startsWith("Herbe"); const geo=grass?new THREE.ConeGeometry(Math.max(t[0],t[1])*k/2,Math.max(t[2],0.05)*k,5)
   :new THREE.IcosahedronGeometry(Math.max(t[0],t[1],t[2])*k/2,0);
 if(grass)geo.rotateX(Math.PI/2);
 const mat=new THREE.MeshLambertMaterial({color:grass?0x5f8f3a:0x4a7d3c,flatShading:true});
 const n=v.pts.length/3, im=new THREE.InstancedMesh(geo,mat,n); const M=new THREE.Matrix4(), q=new THREE.Quaternion(), S=new THREE.Vector3(1,1,1);
 for(let i=0;i<n;i++){q.setFromAxisAngle(new THREE.Vector3(0,0,1),i*2.399);
  M.compose(new THREE.Vector3(v.pts[3*i],v.pts[3*i+1],v.pts[3*i+2]+(grass?Math.max(t[2],0.05)*k/2:Math.max(t[0],t[1],t[2])*k/2)),q,S);im.setMatrixAt(i,M)}
 im.castShadow=!grass; im.receiveShadow=true; vegGroup.add(im); nVeg+=n}
$("vegTxt").textContent=nVeg?nVeg.toLocaleString("fr-FR")+" touffes (aperçu)":"aucune";
$("vegShow").onchange=()=>{vegGroup.visible=$("vegShow").checked};
// ciel HDRI : sphere autour de la scene, tournee comme dans V-Ray
const sky=new THREE.Group(); scene.add(sky);
$("hdriRot").value=info.hdri?info.hdri.rot:0;
function setRot(){const a=+$("hdriRot").value; sky.rotation.z=a*Math.PI/180; $("rotTxt").textContent=a+"°"}
$("hdriRot").addEventListener("input",setRot); setRot();
let lastSent=0, busy=false;
function pushLive(){lastSent=Date.now(); fetch("/camera",{method:"POST",headers:{"Content-Type":"application/json"},
 body:JSON.stringify({action:"reglage",hdri_rot:+$("hdriRot").value,intensite:+$("sunInt").value})}).catch(()=>{})}
$("hdriRot").addEventListener("change",pushLive); $("sunInt").addEventListener("change",pushLive);
setInterval(async()=>{if(busy||Date.now()-lastSent<1500)return; busy=true;
 try{const e=await (await fetch("/api/etat")).json();
  if(Math.abs(e.hdri_rot-(+$("hdriRot").value))>0.4){$("hdriRot").value=e.hdri_rot; setRot()}
  if(Math.abs(e.intensite-(+$("sunInt").value))>0.01){$("sunInt").value=e.intensite; updateSun()}}catch(_){}
 busy=false},1000);
if(info.hdri&&info.hdri.on){$("hdriMsg").textContent="Chargement de la HDRI…";
 try{const r=await fetch("/api/hdri"); const b64=(await r.json()).d; const raw=atob(b64); const u=new Uint8Array(raw.length);
  for(let i=0;i<raw.length;i++)u[i]=raw.charCodeAt(i);
  let tex;
  if(info.hdri.ext===".hdr"){tex=new RGBELoader().parse(u.buffer); const t2=new THREE.DataTexture(tex.data,tex.width,tex.height,THREE.RGBAFormat,tex.type);
   t2.colorSpace=THREE.LinearSRGBColorSpace; t2.flipY=true; t2.needsUpdate=true; tex=t2}
  else if(info.hdri.ext===".exr"){const e=new EXRLoader().parse(u.buffer); tex=new THREE.DataTexture(e.data,e.width,e.height,THREE.RGBAFormat,e.type); tex.needsUpdate=true}
  else{tex=await new THREE.TextureLoader().loadAsync(URL.createObjectURL(new Blob([u]))); tex.colorSpace=THREE.SRGBColorSpace}
  tex.minFilter=THREE.LinearFilter; tex.magFilter=THREE.LinearFilter; tex.generateMipmaps=false;
  const sph=new THREE.Mesh(new THREE.SphereGeometry(1800,64,32),new THREE.MeshBasicMaterial({map:tex,side:THREE.BackSide,fog:false,depthWrite:false}));
  sph.rotation.x=Math.PI/2; sky.add(sph); scene.fog=null;
  $("hdriShow").onchange=()=>{sky.visible=$("hdriShow").checked; scene.background=new THREE.Color(sky.visible?0x000000:0xbcd3e6)};
  $("hdriMsg").textContent="Tourne le curseur : le ciel tourne comme dans le rendu. « Rendre cette vue » garde cette rotation.";
 }catch(e){$("hdriMsg").textContent="HDRI non affichable ici ("+e.message+"), mais elle sera utilisée au rendu."}}
else{$("hdriMsg").textContent="Aucune HDRI .hdr/.exr choisie (⚙ Réglages V-Ray avancés › Environnement)."}
goTo(info.cameras.find(c=>c.nom===info.courante)||info.cameras[0]);
// marcher
let mode="orbit"; const keys={};
$("mOrbit").onclick=()=>{mode="orbit";$("mOrbit").classList.add("on");$("mWalk").classList.remove("on");controls.enabled=true};
$("mWalk").onclick=()=>{mode="walk";$("mWalk").classList.add("on");$("mOrbit").classList.remove("on");controls.enabled=false};
addEventListener("keydown",e=>{if(e.target.tagName==="INPUT")return;keys[e.code]=true});addEventListener("keyup",e=>keys[e.code]=false);
let drag=null; renderer.domElement.addEventListener("pointerdown",e=>{if(mode==="walk"){drag=[e.clientX,e.clientY];renderer.domElement.setPointerCapture(e.pointerId)}});
renderer.domElement.addEventListener("pointerup",()=>drag=null);
renderer.domElement.addEventListener("pointermove",e=>{if(!drag||mode!=="walk")return;const dx=e.clientX-drag[0],dy=e.clientY-drag[1];drag=[e.clientX,e.clientY];
 const d=new THREE.Vector3().subVectors(controls.target,cam.position);const r=d.length();
 let yaw=Math.atan2(d.y,d.x)-dx*0.004, pit=Math.asin(THREE.MathUtils.clamp(d.z/r,-1,1))-dy*0.004; pit=THREE.MathUtils.clamp(pit,-1.45,1.45);
 controls.target.set(cam.position.x+r*Math.cos(pit)*Math.cos(yaw),cam.position.y+r*Math.cos(pit)*Math.sin(yaw),cam.position.z+r*Math.sin(pit)); cam.lookAt(controls.target)});
renderer.domElement.addEventListener("wheel",e=>{if(mode!=="walk")return;e.preventDefault();step(-Math.sign(e.deltaY)*1.0,0,0)},{passive:false});
function step(f,s,u){const d=new THREE.Vector3().subVectors(controls.target,cam.position);d.z=0;d.normalize();
 const side=new THREE.Vector3(d.y,-d.x,0);const m=d.multiplyScalar(f).add(side.multiplyScalar(s)).add(new THREE.Vector3(0,0,u));
 cam.position.add(m);controls.target.add(m)}
const clock=new THREE.Clock();
function loop(){const dt=Math.min(clock.getDelta(),.05);const v=(keys.ShiftLeft||keys.ShiftRight?12:4)*dt;
 if(mode==="walk"||Object.values(keys).some(Boolean)){
  const f=(keys.KeyW||keys.KeyZ||keys.ArrowUp?1:0)-(keys.KeyS||keys.ArrowDown?1:0);
  const s=(keys.KeyD||keys.ArrowRight?1:0)-(keys.KeyA&&mode!=="walk"||keys.KeyQ||keys.ArrowLeft?1:0);
  const u=(mode==="walk"&&keys.KeyE?1:0)-(mode==="walk"&&keys.KeyA?1:0);
  if(f||s||u)step(f*v,s*v,u*v);}
 if(mode==="orbit")controls.update(); else cam.lookAt(controls.target);
 const p=cam.position;$("hud").textContent=`Caméra  x ${p.x.toFixed(1)}  y ${p.y.toFixed(1)}  ·  hauteur ${(p.z-info.sol).toFixed(2)} m  ·  ${$("foc").value} mm`;
 renderer.render(scene,cam);requestAnimationFrame(loop)}
loop();
$("eyeBtn").onclick=()=>{const h=info.sol+(+$("eyeh").value||1.6);const d=new THREE.Vector3().subVectors(controls.target,cam.position);d.z=0;
 if(d.length()<0.5)d.set(5,0,0);cam.position.z=h;controls.target.set(cam.position.x+d.x,cam.position.y+d.y,h);controls.update()};
async function send(action){const body={action,nom:$("name").value.trim()||"Vue 3D",focale:+$("foc").value,
 eye:cam.position.toArray(),target:controls.target.toArray(),
 heure:+$("hour").value,jour:+$("day").value,mois:+$("month").value,intensite:+$("sunInt").value,hdri_rot:+$("hdriRot").value};
 $("msg").textContent=action==="rendre"?"Rendu demandé : la fenêtre V-Ray va s'ouvrir…":"Vue enregistrée dans V-Ray Link.";
 try{await fetch("/camera",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}
 catch(e){$("msg").textContent="V-Ray Link est fermé : relance-le."}}
$("save").onclick=()=>send("enregistrer"); $("render").onclick=()=>send("rendre");
</script></body></html>"""


# ----------------------------------------------------------------- scene ---
def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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
    dist = math.dist(eye, target) or 5.0
    if opts.get("dof"):                       # flou : on ouvre le diaphragme et on compense par la vitesse
        f_new = float(opts.get("dof_f", 2.8))
        shutter = shutter * (fnum / f_new) ** 2
        fnum = f_new
    k_wb = float(opts.get("balance", 0.0))    # -1 froid ... +1 chaud
    wb = (1.0 - 0.12 * k_wb, 0.97, 0.88 + 0.12 * k_wb) if k_wb else (1.0, 0.95, 0.88)
    s = [f"// V-Ray Link - {cam['nom']} - {proj['name']}",
         "SettingsUnitsInfo { meters_scale=1; photometric_scale=1; }",
         f"SettingsOutput {{ img_width={W}; img_height={H}; }}",
         "SettingsGI { on=1; primary_engine=2; secondary_engine=3; }",
         "SettingsLightCache { subdivs=1500; }",
         f"SettingsImageSampler {{ type=3; progressive_maxTime={minutes:.2f}; progressive_threshold={thr}; "
         "progressive_minSubdivs=1; progressive_maxSubdivs=100; }",
         "SettingsColorMapping { type=6; dark_mult=1; bright_mult=1; gamma=2.2; subpixel_mapping=0; clamp_output=0; "
         f"burn_value={max(0.2, min(1.0, 2.0 - float(opts.get('contraste', 1.0)))):.2f}; }}",
         "RenderChannelDenoiser denoiser { enabled=1; name=\"VRayDenoiser\"; }",
         f"RenderView view {{\n  transform=Transform(Matrix({vec(x)},{vec(y)},{vec(z)}),{vec(eye)});\n  fov={hfov:.6f};\n}}",
         f"CameraPhysical cam {{ type=0; specify_fov=1; fov={hfov:.6f}; f_number={fnum}; shutter_speed={shutter}; "
         f"ISO={iso}; exposure={0 if (opts.get('hdri') and not opts.get('hdri_soleil', True)) else 1}; white_balance=Color({wb[0]:.3f},{wb[1]:.3f},{wb[2]:.3f}); "
         f"vignetting={float(opts.get('vignettage', 0.3)):.2f}; use_dof={1 if opts.get('dof') else 0}; "
         f"specify_focus=1; focus_distance={dist:.3f}; }}",
         f"SunLight sun {{\n  transform=Transform(Matrix({vec(sx)},{vec(sy)},{vec(sz)}),{vec([c * 1000 for c in sd])});\n"
         f"  intensity_multiplier={float(opts.get('soleil_intensite', 1.0)):.3f};\n  size_multiplier={float(opts.get('taille_soleil', 3.0))};\n"
         f"  turbidity={float(opts.get('trouble', 3.0))};\n  ozone=0.35;\n  sky_model=3;\n}}",
         "TexSky sky { sun=sun; }"]
    hdri = str(opts.get("hdri") or "")
    env = "sky"
    if hdri and Path(hdri).exists():
        a = math.radians(float(opts.get("hdri_rot", 0.0)))
        m = float(opts.get("hdri_mult", 1.0))
        s += [f'BitmapBuffer hdri_bmp {{ file="{hdri.replace(chr(92), "/")}"; gamma=1; filter_type=0; }}',
              f'UVWGenEnvironment hdri_uvw {{ mapping_type="spherical"; uvw_matrix=Matrix(Vector({math.cos(a):.5f},{math.sin(a):.5f},0),'
              f'Vector({-math.sin(a):.5f},{math.cos(a):.5f},0),Vector(0,0,1)); }}',
              f"TexBitmap hdri_tex {{ bitmap=hdri_bmp; uvwgen=hdri_uvw; color_mult=AColor({m:.3f},{m:.3f},{m:.3f},1); }}"]
        env = "hdri_tex"
        if not opts.get("hdri_soleil", True):
            s[s.index(next(l for l in s if l.startswith("SunLight sun")))] = \
                re.sub(r"intensity_multiplier=[0-9.]+;", "intensity_multiplier=0;", next(l for l in s if l.startswith("SunLight sun")))
    s.append(f"SettingsEnvironment {{ bg_tex={env}; gi_tex={env}; reflect_tex={env}; refract_tex={env}; "
             "bg_tex_on=1; gi_tex_on=1; reflect_tex_on=1; refract_tex_on=1; }")
    for name in opts.get("canaux") or []:
        if name in CANAUX:
            s.append(CANAUX[name])
    if opts.get("moteur", "").startswith("GPU"):
        minutes, thr = QUALITES.get(opts["qualite"], QUALITES["Haute"])
        s.append(f"SettingsRTEngine {{ max_render_time={minutes:.2f}; noise_threshold={thr}; max_sample_level=0; }}")
    sol = SOLS.get(opts.get("sol", "Gazon"))
    if sol:
        r, g, b = (srgb_to_linear(c) for c in sol)
        s += [f"BRDFVRayMtl sol_brdf {{ diffuse=AColor({r:.4f},{g:.4f},{b:.4f},1); reflect=AColor(0.05,0.05,0.05,1); "
              "reflect_glossiness=0.35; fresnel=1; }",
              "MtlSingleBRDF sol_mtl { brdf=sol_brdf; }",
              "GeomPlane sol_geo { }",
              f"Node sol_node {{ transform=Transform(Matrix(Vector(1,0,0),Vector(0,1,0),Vector(0,0,1)),"
              f"Vector(0,0,{ground_z - 0.005:.4f})); geometry=sol_geo; "
              f"material={'sol_cosmos' if 'sol_cosmos' in _read(path.parent / 'materiaux.vrscene') else 'sol_mtl'}; }}"]
    s.append('#include "materiaux.vrscene"')
    s.append(f'#include "{geometry.name}"')
    if (path.parent / "objets.vrscene").exists():
        s.append('#include "objets.vrscene"')
    text = "\n".join(s) + "\n"
    path.write_text(text, encoding="utf-8")
    secours = path.with_name(path.stem + "_sans_scan.vrscene")      # secours si un materiau scanne fait planter V-Ray
    secours.write_text(text.replace('#include "materiaux.vrscene"', '#include "materiaux_sans_scan.vrscene"'),
                       encoding="utf-8")


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


def vrmesh_size(path) -> list:
    """Dimensions (m) d'un proxy .vrmesh Cosmos : [largeur x, largeur y, hauteur, z mini] (lecture du 1er bloc de sommets)."""
    import zlib
    try:
        d = Path(path).read_bytes()[:4_000_000]
        i = 0
        while True:
            i = d.find(b"x", i)
            if i < 0 or i > 4096:
                return []
            if d[i + 1] in (0x01, 0x5E, 0x9C, 0xDA):
                try:
                    r = zlib.decompressobj().decompress(d[i:])
                    if len(r) >= 36 and len(r) % 12 == 0:
                        v = struct.unpack(f"<{len(r) // 4}f", r)
                        xs, ys, zs = v[0::3], v[1::3], v[2::3]
                        k = ECHELLE_COSMOS
                        size = [(max(xs) - min(xs)) * k, (max(ys) - min(ys)) * k, (max(zs) - min(zs)) * k, min(zs) * k]
                        if all(0 < c < 200 for c in size[:3]):
                            return size
                except Exception:  # noqa: BLE001
                    pass
            i += 1
    except Exception:  # noqa: BLE001
        return []


def vrmat_missing(vrmat: Path) -> list:
    """Textures referencees par un .vrmat (deja corrige) qui n'existent pas sur le disque."""
    try:
        text = Path(vrmat).read_text("utf-8", "replace")
    except OSError:
        return ["(vrmat illisible)"]
    refs = re.findall(r">([^<>]+?\.(?:tx|jpg|jpeg|png|exr|tif|tiff|hdr|bmp))<", text, flags=re.I)
    return [Path(r.replace("&amp;", "&")).name for r in dict.fromkeys(refs) if not Path(r.replace("&amp;", "&")).exists()]


def cosmos_hdris() -> list[dict]:
    """HDRI telechargees depuis Cosmos (dossier Packages/HDRI...) : [{nom, fichier, apercu, dossier, categorie}]."""
    out = []
    base = cosmos_packages()
    if not base.is_dir():
        return out
    for folder in base.glob("*/*/*"):
        if not folder.is_dir() or "hdr" not in folder.parts[-3].lower():
            continue
        files = [p for ext in ("*.exr", "*.hdr", "*.tx") for p in folder.rglob(ext) if "preview" not in str(p).lower()]
        if not files:
            continue
        files.sort(key=lambda p: (p.suffix.lower() != ".exr", p.suffix.lower() != ".hdr", -p.stat().st_size))
        prev = sorted((folder / "Previews").glob("*.png")) + sorted((folder / "Previews").glob("*.jpg"))
        out.append({"nom": re.sub(r"_[0-9a-f]{8}$", "", folder.name).replace("_", " "), "fichier": str(files[0]),
                    "apercu": str(prev[0]) if prev else "", "dossier": str(folder), "categorie": folder.parent.name,
                    "date": folder.stat().st_mtime})
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
    lod = re.search(r"_(3dh|3dm|3dl|3d)_", vrmat.name)
    out = data_dir() / "cosmos" / f"{safe(vrmat.parent.name)}{'_' + lod.group(1) if lod else ''}.vrmat"
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
            "jour": today.day, "mois": today.month, "heure": 16.5, "fuseau": 0.0, "verticales": True, "moteur": "CPU (processeur)", "format": "PNG", "canaux": [],
            "dof": False, "dof_f": 2.8, "hdri": "", "hdri_mult": 1.0, "hdri_rot": 0.0, "hdri_soleil": True, "soleil_intensite": 1.0,
            "balance": 0.0, "vignettage": 0.3, "contraste": 1.0,
            "trouble": 3.0, "taille_soleil": 3.0, "lieu": "Abidjan", "reutiliser": False, "oeil": 1.6, "cible": 1.4, "focale": 24}
    opts.update({k: v for k, v in (saved.get("options") or {}).items() if k in opts})
    return {"options": opts, "cameras": saved.get("cameras") or {}, "bibliotheque": saved.get("bibliotheque") or {},
            "vray": saved.get("vray", ""), "coches": saved.get("coches") or {},
            "cosmos": saved.get("cosmos") or {}, "selection": saved.get("selection") or {},
            "traces": saved.get("traces") or {}, "objets": saved.get("objets") or {},
            "scatter": saved.get("scatter") or {}}


def save_settings(state: dict) -> None:
    out = {k: state.get(k) or {} for k in ("options", "cameras", "bibliotheque", "coches", "cosmos", "selection", "traces", "objets", "scatter")}
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
    reuse = (state["options"].get("reutiliser") and geo.exists() and cache.exists() and info.get("version") == 3
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
    sol = assigned.get(SOL_AUTOUR)                      # materiau Cosmos pour le sol autour (texture d'herbe...)
    if sol and sol.get("dossier"):
        try:
            fixed, top = vrmat_for_render(sorted(Path(sol["dossier"]).glob("*.vrmat"), key=_lod)[0])
            block = (f"MtlVRmat sol_cosmos {{\n  filename=\"{str(fixed).replace(chr(92), '/')}\";\n  mtlname=\"{top}\";\n}}\n")
            for f_ in ("materiaux.vrscene", "materiaux_sans_scan.vrscene"):
                with (d / f_).open("a", encoding="utf-8") as out:
                    out.write(block)
            used.append(f"{SOL_AUTOUR} → {virtual_surface(sol['dossier'])[9:]}")
        except Exception as error:  # noqa: BLE001
            report(f"Matériau du sol indisponible : {error}")
    meta.write_text(json.dumps({"version": 3, "sol": ground, "surfaces": sorted(meshes), "triangles": tris,
                                "selection": sel_sig, "uv": uv_sig,
                                "couleurs": {n: list(mats.get(n, {}).get("rgb", (0.7, 0.7, 0.7))) for n in meshes}}),
                    encoding="utf-8")
    state["surfaces"] = {n: list(mats.get(n, {}).get("rgb", (0.7, 0.7, 0.7))) for n in sorted(meshes)}
    n_obj = write_objects(d / "objets.vrscene", (state.get("objets") or {}).get(key, []), report)
    sc_notes: list = []
    n_sc = write_scatter(d / "objets.vrscene", (state.get("scatter") or {}).get(key, []), meshes, offset, ground, key,
                         sc_notes.append)
    summary = f"{len(meshes)} surfaces, {tris:,} triangles".replace(",", " ")
    if note:
        summary += f" ({note})"
    if used:
        summary += "\nMatériaux Cosmos : " + " ; ".join(used)
    if n_obj:
        summary += f"\nObjets Cosmos : {n_obj}"
    if n_sc or sc_notes:
        summary += f"\nScatter : {n_sc:,} exemplaires".replace(",", " ") + "".join("\n  · " + t for t in sc_notes)
        summary += f"\n  (sol V-Ray à {ground:.2f} m)"
    return geo, ground, summary


def diagnostic(folder: Path) -> list[str]:
    try:
        return (folder / "surfaces_diagnostic.txt").read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return ["(pas de diagnostic : géométrie reprise de l'export précédent)"]


def _run_bat(bat: Path, lines: list, env_vars: dict, cwd: Path | None = None) -> None:
    """Le .bat reste en ASCII : les chemins (accents : Facade, aerienne...) passent par des variables
    d'environnement, que cmd transmet sans les abimer."""
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="ascii", errors="replace", newline="")
    env = dict(os.environ)
    env.update({k: str(v) for k, v in env_vars.items()})
    subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(cwd) if cwd else None, env=env,
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def launch(vray: str, scene: Path, image: Path, show: bool = True, engine: str = "") -> None:
    """Lance V-Ray. S'il se ferme sans image : relance ; s'il plante encore, rendu de secours sans
    materiaux scannes (.vrscan) ; en dernier recours la fenetre reste ouverte avec le code d'erreur."""
    secours = scene.with_name(scene.stem + "_sans_scan.vrscene")
    opts = f"-display={1 if show else 0} -autoClose=0 -verboseLevel=3"
    _run_bat(scene.parent / "rendre_cette_vue.bat", [
        "@echo off", "title V-Ray Link - rendu",
        'if exist "%VRL_IMAGE%" del "%VRL_IMAGE%"',
        f'"%VRL_VRAY%" -sceneFile="%VRL_SCENE%" -imgFile="%VRL_IMAGE%" {opts} {engine}',
        'if exist "%VRL_IMAGE%" goto fin',
        "echo.", "echo V-Ray s'est ferme sans image : nouvel essai" + (" sur le processeur (CPU)..." if engine else "..."),
        "timeout /t 3 >nul",
        f'"%VRL_VRAY%" -sceneFile="%VRL_SCENE%" -imgFile="%VRL_IMAGE%" {opts}',
        'if exist "%VRL_IMAGE%" goto fin',
        'if not exist "%VRL_SECOURS%" goto echec',
        "echo.", "echo ============================================================",
        "echo  V-Ray plante sur un materiau SCANNE ^(Chaos Scans^).",
        "echo  Rendu de secours : ces materiaux sont remplaces par la couleur Archicad.",
        "echo ============================================================", "timeout /t 3 >nul",
        f'"%VRL_VRAY%" -sceneFile="%VRL_SECOURS%" -imgFile="%VRL_IMAGE%" {opts}',
        'if exist "%VRL_IMAGE%" goto fin',
        ":echec", "set CODE=%errorlevel%", "echo.",
        "echo *** V-Ray n'a pas pu rendre l'image ^(code %CODE%^) : fais une capture de cette fenetre. ***",
        "pause", ":fin"], {"VRL_VRAY": vray, "VRL_SCENE": scene, "VRL_IMAGE": image,
                           "VRL_SECOURS": secours if secours.exists() else ""}, scene.parent)


def launch_batch(vray: str, jobs: list[tuple[Path, Path]], folder: Path, engine: str = "") -> Path:
    bat = jobs[0][0].parent / "rendre_les_vues.bat"
    lines = ["@echo off", "title V-Ray Link - rendu des vues"]
    env = {"VRL_VRAY": vray, "VRL_DOSSIER": folder}
    for k, (scene, image) in enumerate(jobs, 1):
        env[f"VRL_S{k}"], env[f"VRL_I{k}"] = scene, image
        lines.append(f"echo Vue {k}/{len(jobs)}")
        lines.append(f'"%VRL_VRAY%" -sceneFile="%VRL_S{k}%" -imgFile="%VRL_I{k}%" -display=1 -autoClose=1 {engine}')
        lines.append(f'if not exist "%VRL_I{k}%" "%VRL_VRAY%" -sceneFile="%VRL_S{k}%" -imgFile="%VRL_I{k}%" -display=1 -autoClose=1')
        env[f"VRL_B{k}"] = scene.with_name(scene.stem + "_sans_scan.vrscene")
        lines.append(f'if not exist "%VRL_I{k}%" "%VRL_VRAY%" -sceneFile="%VRL_B{k}%" -imgFile="%VRL_I{k}%" -display=1 -autoClose=1')
        lines.append(f'if not exist "%VRL_I{k}%" (echo *** Erreur V-Ray sur cette vue : fais une capture. *** & pause)')
    lines += ['explorer "%VRL_DOSSIER%"', "echo Termine.", "timeout /t 5"]
    _run_bat(bat, lines, env, bat.parent)
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

    def tk_error(exc, val, tb):                 # erreur dans un bouton : message lisible ici, detail dans un fichier
        import traceback
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            (data_dir() / "erreurs.txt").open("a", encoding="utf-8").write(time.strftime("%H:%M:%S ") + text + "\n")
        except Exception:  # noqa: BLE001
            pass
        messagebox.showerror("V-Ray Link", f"{val}\n\n(Si Archicad a une fenêtre de réglages ouverte, ferme-la et recommence.)\n"
                             f"Détail : {data_dir() / 'erreurs.txt'}", parent=root)

    root.report_callback_exception = tk_error
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
        placed = state["cameras"].get(key, [])
        names = {c["nom"] for c in placed}
        return [a for a in autos if a["nom"] not in names] + placed

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
        draw_plan()

    def compute_autos():
        nonlocal autos
        try:
            autos = auto_cameras(archicad_bbox(("Wall", "Roof", "Column")) or archicad_bbox(), proj["north"])
        except Exception as error:  # noqa: BLE001
            autos = []
            status.set(f"Vues automatiques indisponibles : {error}")
        refresh()


    # --- plan de reperage : toutes les cameras vues de dessus, deplacables a la souris
    plan = tk.Canvas(box, width=340, height=230, background="#fbfaf7", highlightthickness=1, highlightbackground="#ccc")
    plan.grid(row=0, column=4, rowspan=3, padx=(12, 0), sticky="n")
    foot = {"cells": None, "step": 0.0}
    view = {"k": 1.0, "ox": 0.0, "oy": 0.0}
    drag = {"cam": None, "part": None}

    def to_px(x, y):
        return view["ox"] + x * view["k"], view["oy"] - y * view["k"]

    def to_world(px, py):
        return (px - view["ox"]) / view["k"], (view["oy"] - py) / view["k"]

    def draw_plan(*_):
        if foot["cells"] is None:
            foot["cells"] = plan_footprint(key) or {}
        f = foot["cells"]
        plan.delete("all")
        cams = all_cams()
        cur = next((c for c in cams if c["nom"] == current.get()), None)
        xs, ys = [], []
        if f:
            n = max([c[0] for c in f["murs"] + f["sols"]] + [0]) + 1
            m_ = max([c[1] for c in f["murs"] + f["sols"]] + [0]) + 1
            xs += [f["x0"], f["x0"] + n * f["pas"]]
            ys += [f["y0"], f["y0"] + m_ * f["pas"]]
        for c in ([cur] if (cur and f) else cams):          # cadre sur le bati + la vue choisie
            xs += [c["eye"][0], c["target"][0]]
            ys += [c["eye"][1], c["target"][1]]
        if not xs:
            plan.create_text(170, 115, text="Le plan apparaîtra après le premier rendu", fill="#888")
            return
        W, H, m = 340, 230, 22
        k = min((W - 2 * m) / max(max(xs) - min(xs), 1.0), (H - 2 * m) / max(max(ys) - min(ys), 1.0))
        view.update(k=k, ox=m - min(xs) * k + ((W - 2 * m) - (max(xs) - min(xs)) * k) / 2,
                    oy=H - m + min(ys) * k - ((H - 2 * m) - (max(ys) - min(ys)) * k) / 2)
        if f:
            for cells, col in ((f["sols"], "#dedad0"), (f["murs"], "#4a4a4a")):
                for i, j in cells:
                    a, b = to_px(f["x0"] + i * f["pas"], f["y0"] + j * f["pas"])
                    c, d = to_px(f["x0"] + (i + 1) * f["pas"], f["y0"] + (j + 1) * f["pas"])
                    plan.create_rectangle(a, d, c, b, fill=col, outline="")
        for cam in sorted(cams, key=lambda c: c["nom"] == current.get()):
            on = cam["nom"] == current.get()
            col = "#e0702a" if on else "#7d8590"
            (ex, ey), left, right, (tx, ty) = camera_cone(cam, None if on else 18 / k)
            pts = [to_px(ex, ey), to_px(*left), to_px(*right)]
            plan.create_polygon(*[v for p in pts for v in p], fill="#e0702a" if on else "#e7e9ec", outline=col,
                                stipple="gray25" if on else "", width=2 if on else 1)
            a, b = to_px(ex, ey)
            plan.create_oval(a - 6, b - 6, a + 6, b + 6, fill=col, outline="white", width=2)
            if on:
                c, d = to_px(tx, ty)
                plan.create_line(a, b, c, d, fill=col, dash=(3, 2))
                plan.create_oval(c - 5, d - 5, c + 5, d + 5, outline=col, width=2)
            plan.create_text(a, b + 14, text=cam["nom"], fill=col, font=("Segoe UI", 8, "bold" if on else "normal"))
        plan.create_text(6, 6, anchor="nw", fill="#888", font=("Segoe UI", 8),
                         text="Glisse le point (caméra) ou le rond (point visé)")

    def editable(cam: dict) -> dict:
        """Une vue automatique deplacee devient une camera placee (meme nom)."""
        cams = state["cameras"].setdefault(key, [])
        mine = next((c for c in cams if c["nom"] == cam["nom"]), None)
        if mine is None:
            mine = {k2: (list(v) if isinstance(v, list) else v) for k2, v in cam.items() if k2 != "auto"}
            cams.append(mine)
        return mine

    def on_press(ev):
        best = None
        for cam in all_cams():
            for part, pt in (("eye", cam["eye"]), ("target", cam["target"])):
                if part == "target" and cam["nom"] != current.get():
                    continue
                a, b = to_px(pt[0], pt[1])
                d = math.hypot(ev.x - a, ev.y - b)
                if d < 12 and (best is None or d < best[0]):
                    best = (d, cam, part)
        if best:
            current.set(best[1]["nom"])
            drag.update(cam=editable(best[1]), part=best[2])
        draw_plan()

    def on_move(ev):
        if not drag["cam"]:
            return
        x, y = to_world(ev.x, ev.y)
        drag["cam"][drag["part"]][0], drag["cam"][drag["part"]][1] = x, y
        draw_plan()

    def on_release(_ev):
        if drag["cam"]:
            save_settings(state)
            name = drag["cam"]["nom"]
            drag.update(cam=None, part=None)
            refresh()
            current.set(name)
            if state.get("traces", {}).get(key):
                to_plan(quiet=True)

    plan.bind("<ButtonPress-1>", on_press)
    plan.bind("<B1-Motion>", on_move)
    plan.bind("<ButtonRelease-1>", on_release)
    current.trace_add("write", lambda *_: draw_plan())

    def to_plan(quiet=False):
        try:
            n = draw_cameras_in_plan(all_cams(), state, key, current.get())
            save_settings(state)
            if not quiet:
                say(f"{n} caméras dessinées dans le plan (calque « {CALQUE_CAMERAS} », la vue choisie en orange).")
        except Exception as error:  # noqa: BLE001
            say(f"Dessin dans le plan impossible : {error}")

    def off_plan():
        erase_cameras_in_plan(state, key)
        save_settings(state)
        say("Caméras retirées du plan.")

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
    foc_box = ttk.Combobox(row, values=FOCALES, textvariable=foc_var, width=4, state="readonly")
    foc_box.grid(row=0, column=5, padx=(2, 8))
    ttk.Button(row, text="Placer une caméra (2 clics)...", command=lambda: place()).grid(row=0, column=6)
    ttk.Button(row, text="Supprimer", command=lambda: remove()).grid(row=0, column=7, padx=4)
    vert_var = tk.BooleanVar(value=opts["verticales"])
    ttk.Checkbutton(box, text="Verticales droites (caméra horizontale)", variable=vert_var).grid(row=2, column=0, sticky="w",
                                                                                              pady=(4, 0))
    pr = ttk.Frame(box)
    pr.grid(row=3, column=0, columnspan=5, sticky="w", pady=(4, 0))
    ttk.Button(pr, text="📐 Montrer les caméras dans le plan Archicad", command=lambda: to_plan()).grid(row=0, column=0)
    ttk.Button(pr, text="Les retirer du plan", command=lambda: off_plan()).grid(row=0, column=1, padx=4)
    ttk.Button(pr, text="🧭 Visionneuse 3D : se déplacer et rendre", command=lambda: open_viewer()).grid(row=0, column=2, padx=4)

    def focal_changed(_ev=None):
        cam = next((c for c in all_cams() if c["nom"] == current.get()), None)
        if cam:
            try:
                editable(cam)["focale"] = int(foc_var.get())
            except (ValueError, tk.TclError):
                return
            save_settings(state)
            refresh()

    foc_box.bind("<<ComboboxSelected>>", focal_changed)

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
        to_plan(quiet=True)
        show([f"Caméra « {cam['nom']} » placée et dessinée dans le plan (calque « {CALQUE_CAMERAS} »).",
              "Ajuste-la en glissant ses points dans le petit plan à droite des vues."])

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
            try:                                  # copies / deplacements faits dans Archicad sur les reperes d'objets
                objs_, note_ = sync_objects_from_plan((state.get("objets") or {}).get(key, []))
                if note_:
                    state.setdefault("objets", {}).setdefault(key, [])[:] = objs_
                    save_settings(state)
                    say(note_)
            except Exception:  # noqa: BLE001
                pass
            geo, ground, info = prepare_geometry(state, proj, say)
            folder = renders_dir(proj["name"])
            stamp = time.strftime("%Hh%M")
            jobs = []
            for name in names:
                cam = next(c for c in all_cams() if c["nom"] == name)
                scene = geo.parent / f"scene_{safe(name)}.vrscene"
                write_scene(scene, geo, cam, opts, proj, ground)
                jobs.append((scene, folder / f"{safe(name)}_{stamp}{FORMATS.get(opts.get('format'), '.png')}"))
            if len(jobs) == 1:
                launch(state["vray"], *jobs[0], engine=MOTEURS.get(opts.get("moteur"), ""))
                say("Rendu lancé : la fenêtre V-Ray s'ouvre.")
            else:
                launch_batch(state["vray"], jobs, folder, MOTEURS.get(opts.get("moteur"), ""))
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

    def advanced():
        from tkinter import filedialog
        win = tk.Toplevel(root)
        win.title("Réglages V-Ray avancés")
        win.attributes("-topmost", True)
        win.transient(root)
        f = ttk.Frame(win, padding=12)
        f.grid(sticky="nsew")
        v = {}
        live["adv"] = v
        win.bind("<Destroy>", lambda e: live.pop("adv", None) if e.widget is win else None)

        def push_live(*_):                      # reglage du dialogue -> visionneuse (lu par /api/etat)
            try:
                opts["hdri_rot"] = float(v["hdri_rot"].get())
                opts["hdri_mult"] = float(v["hdri_mult"].get())
                opts["soleil_intensite"] = float(v["soleil_intensite"].get())
            except (ValueError, tk.TclError, KeyError):
                pass

        def section(title, row):
            box_ = ttk.LabelFrame(f, text=title, padding=8)
            box_.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            return box_

        m = section("Moteur de rendu", 0)
        v["moteur"] = tk.StringVar(value=opts.get("moteur", "CPU (processeur)"))
        ttk.Combobox(m, values=list(MOTEURS), textvariable=v["moteur"], state="readonly", width=28).grid(row=0, column=0, sticky="w")
        ttk.Label(m, foreground="#777", wraplength=420, justify="left",
                  text="GPU = beaucoup plus rapide avec une carte NVIDIA. Si le GPU échoue, V-Ray Link relance tout seul "
                       "sur le processeur.").grid(row=1, column=0, sticky="w", pady=(4, 0))

        im = section("Image", 1)
        ttk.Label(im, text="Format").grid(row=0, column=0, sticky="w")
        v["format"] = tk.StringVar(value=opts.get("format", "PNG"))
        ttk.Combobox(im, values=list(FORMATS), textvariable=v["format"], state="readonly", width=34).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(im, text="Contraste").grid(row=1, column=0, sticky="w", pady=(6, 0))
        v["contraste"] = tk.DoubleVar(value=opts.get("contraste", 1.0))
        ttk.Scale(im, from_=1.0, to=1.8, variable=v["contraste"], length=220).grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))

        ca = section("Caméra", 2)
        v["dof"] = tk.BooleanVar(value=opts.get("dof", False))
        ttk.Checkbutton(ca, text="Profondeur de champ (arrière-plan flou, net sur le point visé)", variable=v["dof"]).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(ca, text="Ouverture f/").grid(row=1, column=0, sticky="w")
        v["dof_f"] = tk.DoubleVar(value=opts.get("dof_f", 2.8))
        ttk.Combobox(ca, values=(1.4, 2.0, 2.8, 4.0, 5.6), textvariable=v["dof_f"], width=5, state="readonly").grid(
            row=1, column=1, sticky="w", padx=6)
        ttk.Label(ca, text="(petit = plus de flou)", foreground="#777").grid(row=1, column=2, sticky="w")
        ttk.Label(ca, text="Balance des blancs").grid(row=2, column=0, sticky="w", pady=(6, 0))
        v["balance"] = tk.DoubleVar(value=opts.get("balance", 0.0))
        ttk.Scale(ca, from_=-1.0, to=1.0, variable=v["balance"], length=200).grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(ca, text="froid ◀ ▶ chaud", foreground="#777").grid(row=3, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Label(ca, text="Vignettage").grid(row=4, column=0, sticky="w")
        v["vignettage"] = tk.DoubleVar(value=opts.get("vignettage", 0.3))
        ttk.Scale(ca, from_=0.0, to=1.0, variable=v["vignettage"], length=200).grid(row=4, column=1, columnspan=2, sticky="w", padx=6)

        en = section("Environnement (ciel HDRI)", 3)
        v["hdri"] = tk.StringVar(value=opts.get("hdri", ""))
        ttk.Entry(en, textvariable=v["hdri"], width=44).grid(row=0, column=0, columnspan=2, sticky="w")

        def browse():
            path = filedialog.askopenfilename(parent=win, title="Image HDRI",
                                              filetypes=[("HDRI", "*.hdr *.exr *.jpg *.png *.tif"), ("Tous", "*.*")])
            if path:
                v["hdri"].set(path)
        ttk.Button(en, text="Parcourir…", command=browse).grid(row=0, column=2, padx=4)
        ttk.Button(en, text="Aucun", command=lambda: v["hdri"].set("")).grid(row=0, column=3)
        hd = cosmos_hdris()
        if hd:
            ttk.Label(en, text="HDRI Cosmos").grid(row=5, column=0, sticky="w", pady=(6, 0))
            hd_var = tk.StringVar()
            hd_box = ttk.Combobox(en, values=[e["nom"] for e in hd], textvariable=hd_var, state="readonly", width=30)
            hd_box.grid(row=5, column=1, columnspan=3, sticky="w", pady=(6, 0))
            hd_box.bind("<<ComboboxSelected>>", lambda _e: v["hdri"].set(next(e["fichier"] for e in hd if e["nom"] == hd_var.get())))
        else:
            ttk.Label(en, foreground="#777", wraplength=420, justify="left",
                      text="Astuce : télécharge une HDRI dans Cosmos (catégorie HDRI) : elle apparaîtra ici dans une liste.").grid(
                row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(en, text="Intensité").grid(row=1, column=0, sticky="w", pady=(6, 0))
        v["hdri_mult"] = tk.DoubleVar(value=opts.get("hdri_mult", 1.0))
        ttk.Spinbox(en, from_=0.1, to=50, increment=0.5, textvariable=v["hdri_mult"], width=6).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(en, text="Rotation (°)").grid(row=2, column=0, sticky="w")
        v["hdri_rot"] = tk.DoubleVar(value=opts.get("hdri_rot", 0.0))
        v["hdri_rot"].trace_add("write", push_live)
        ttk.Scale(en, from_=0, to=360, variable=v["hdri_rot"], length=200).grid(row=2, column=1, columnspan=3, sticky="w")
        v["hdri_soleil"] = tk.BooleanVar(value=opts.get("hdri_soleil", True))
        ttk.Checkbutton(en, text="Garder le soleil V-Ray (ombres nettes réglées par l'heure)", variable=v["hdri_soleil"]).grid(
            row=3, column=0, columnspan=4, sticky="w")
        ttk.Label(en, foreground="#777", text="Sans HDRI : ciel physique V-Ray (lieu, date, heure).").grid(
            row=4, column=0, columnspan=4, sticky="w")

        ttk.Label(en, text="Intensité du soleil").grid(row=6, column=0, sticky="w", pady=(6, 0))
        v["soleil_intensite"] = tk.DoubleVar(value=opts.get("soleil_intensite", 1.0))
        v["soleil_intensite"].trace_add("write", push_live)
        ttk.Spinbox(en, from_=0, to=5, increment=0.1, textvariable=v["soleil_intensite"], width=6).grid(
            row=6, column=1, sticky="w", pady=(6, 0))
        ch = section("Canaux de rendu (pour Photoshop)", 4)
        chosen_ch = set(opts.get("canaux") or [])
        for k, name in enumerate(CANAUX):
            v["ch_" + name] = tk.BooleanVar(value=name in chosen_ch)
            ttk.Checkbutton(ch, text=name, variable=v["ch_" + name]).grid(row=k // 2, column=k % 2, sticky="w", padx=(0, 12))
        ttk.Label(ch, foreground="#777", wraplength=420, justify="left",
                  text="Enregistrés à côté de l'image (en EXR : tous dans le même fichier).").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))

        def ok():
            try:
                for name in ("moteur", "format", "dof", "dof_f", "balance", "vignettage", "hdri", "hdri_mult", "hdri_rot",
                             "hdri_soleil", "contraste", "soleil_intensite"):
                    opts[name] = v[name].get()
                opts["canaux"] = [n for n in CANAUX if v["ch_" + n].get()]
            except (ValueError, tk.TclError):
                pass
            save_settings(state)
            say(f"Réglages avancés : {opts['moteur']}, {opts['format']}"
                f"{', flou f/' + str(opts['dof_f']) if opts['dof'] else ''}{', HDRI' if opts['hdri'] else ''}"
                f"{', ' + str(len(opts['canaux'])) + ' canaux' if opts['canaux'] else ''}.")
            win.destroy()
        bar = ttk.Frame(f)
        bar.grid(row=5, column=0, sticky="e")
        ttk.Button(bar, text="Annuler", command=win.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(bar, text="OK", command=ok).grid(row=0, column=1)

    def open_folder():
        folder = renders_dir(proj["name"])
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            show([str(folder)])

    buttons = ttk.Frame(outer)
    buttons.grid(row=5, column=0, sticky="e", pady=(8, 0))
    ttk.Button(buttons, text="⚙ Réglages V-Ray avancés…", command=lambda: advanced()).grid(row=0, column=0, padx=(0, 12))
    ttk.Button(buttons, text="Ouvrir le dossier des rendus", command=open_folder).grid(row=0, column=4, padx=3)
    ttk.Button(buttons, text="Rendre les vues cochées", command=lambda: render(
        [n for n, v in cam_vars.items() if v.get()])).grid(row=0, column=1, padx=3)
    ttk.Button(buttons, text="Rendre cette vue", command=lambda: render([current.get()] if current.get() else [])
               ).grid(row=0, column=2, padx=3)

    # --- visionneuse 3D (page web locale)
    import queue
    live: dict = {}
    viewer = {"srv": None}
    snap: dict = {}
    inbox: "queue.Queue[dict]" = queue.Queue()

    def viewer_infos() -> dict:
        W, H = TAILLES.get(opts["taille"], (1920, 1080))
        try:
            ground = float(json.loads((data_dir() / key / "geometrie.json").read_text(encoding="utf-8")).get("sol", 0.0))
        except Exception:  # noqa: BLE001
            ground = 0.0
        try:
            az, alt = sun_position(proj["lat"], proj["lon"], dt.date(2026, int(opts["mois"]), min(28, int(opts["jour"]))),
                                   float(opts["heure"]), float(proj.get("fuseau", 0.0)))
            sv = [c * 100 for c in sun_vector(az, max(alt, 0.2), proj["north"])]
        except Exception:  # noqa: BLE001
            sv = [50, -50, 80]
        apply_place(proj, opts.get("lieu", "Abidjan"))
        return {"largeur": W, "hauteur": H, "sol": ground, "focales": list(FOCALES), "soleil": sv,
                "lat": proj["lat"], "lon": proj["lon"], "fuseau": float(proj.get("fuseau", 0.0)), "north": proj["north"],
                "heure": float(opts["heure"]), "jour": int(opts["jour"]), "mois": int(opts["mois"]),
                "intensite": float(opts.get("soleil_intensite", 1.0)),
                "objets": [{"nom": o["nom"], "x": o["x"], "y": o["y"], "z": o["z"], "echelle": o.get("echelle", 1.0),
                            "rot": o.get("rotation", 0.0), "taille": obj_size(o["dossier"])}
                           for o in (state.get("objets") or {}).get(key, [])],
                "vegetation": vegetation(),
                "hdri": hdri_info(),
                "arbres": tree_points(),
                "courante": current.get(),
                "cameras": [{"nom": c["nom"], "eye": c["eye"], "target": c["target"], "focale": c.get("focale", 24),
                             "auto": bool(c.get("auto"))} for c in all_cams()]}

    def obj_size(folder: str) -> list:
        try:
            return vrmesh_size(sorted(Path(folder).glob("*.vrmesh"), key=_lod)[-1])
        except Exception:  # noqa: BLE001
            return []

    def hdri_info() -> dict:
        path = str(opts.get("hdri") or "")
        ok = bool(path) and Path(path).suffix.lower() in (".hdr", ".exr", ".jpg", ".jpeg", ".png") and Path(path).exists()
        snap["_hdri_path"] = path if ok else ""
        return {"on": ok, "rot": float(opts.get("hdri_rot", 0.0)), "mult": float(opts.get("hdri_mult", 1.0)),
                "ext": Path(path).suffix.lower() if ok else "", "soleil": bool(opts.get("hdri_soleil", True))}

    def vegetation() -> list:
        """Herbe / fleurs du scatter, en version legere pour la visionneuse (echantillon de points + taille reelle)."""
        out, budget = [], 20000
        scs = [sc for sc in (state.get("scatter") or {}).get(key, []) if sc.get("type") != "Arbres"]
        if not scs:
            return out
        try:
            meshes, _m, offset, ground = pickle.loads((data_dir() / key / "geometrie.pkl").read_bytes())
        except Exception:  # noqa: BLE001
            meshes, offset, ground = {}, (0.0, 0.0, 0.0), 0.0
        import random
        for sc in scs:
            pts = scatter_points(sc, meshes, offset, ground, key)
            n = min(len(pts), budget // len(scs))
            if not n:
                continue
            pts = random.Random(2).sample(pts, n) if len(pts) > n else pts
            out.append({"nom": sc["nom"], "type": sc.get("type", ""), "taille": obj_size(sc["dossier"]),
                        "echelle": float(sc.get("echelle", 1.0)), "pts": [round(c, 2) for p_ in pts for c in p_]})
        return out

    def tree_points() -> list:
        """Arbres du scatter, pour les montrer (avec leur ombre) dans la visionneuse."""
        trees = [sc for sc in (state.get("scatter") or {}).get(key, []) if sc.get("type") == "Arbres"]
        if not trees:
            return []
        try:
            meshes, _m, offset, ground = pickle.loads((data_dir() / key / "geometrie.pkl").read_bytes())
        except Exception:  # noqa: BLE001
            return []
        return [list(p) + [float(sc.get("echelle", 1.0))] for sc in trees
                for p in scatter_points(sc, meshes, offset, ground, key)][:3000]

    def poll_viewer():
        try:
            while True:
                msg = inbox.get_nowait()
                if "heure" in msg:                                   # soleil choisi dans la visionneuse
                    hour_var.set(round(float(msg["heure"]), 2))
                    day_var.set(int(msg["jour"]))
                    month_var.set(int(msg["mois"]))
                    show_hour()
                    collect()
                if "hdri_rot" in msg:
                    opts["hdri_rot"] = float(msg["hdri_rot"]) % 360
                    save_settings(state)
                    if live.get("adv"):
                        live["adv"]["hdri_rot"].set(round(opts["hdri_rot"], 1))
                if "intensite" in msg:
                    opts["soleil_intensite"] = max(0.0, min(5.0, float(msg["intensite"])))
                    save_settings(state)
                    if live.get("adv"):
                        live["adv"]["soleil_intensite"].set(round(opts["soleil_intensite"], 2))
                if msg.get("action") == "reglage":
                    continue
                if msg.get("action") == "soleil":
                    say(f"Soleil réglé depuis la visionneuse : {opts['jour']}/{opts['mois']} à "
                        f"{int(opts['heure']):02d}h{int(round(opts['heure'] % 1 * 60)) % 60:02d}.")
                    continue
                name = str(msg.get("nom") or "Vue 3D")[:60]
                cam = {"nom": name, "focale": int(msg.get("focale", 24)),
                       "eye": [float(v) for v in msg["eye"]][:3], "target": [float(v) for v in msg["target"]][:3]}
                cams = [c for c in state["cameras"].get(key, []) if c["nom"] != name]
                state["cameras"][key] = cams + [cam]
                save_settings(state)
                refresh()
                current.set(name)
                if state.get("traces", {}).get(key):
                    to_plan(quiet=True)
                if msg.get("action") == "rendre":
                    render([name])
                else:
                    say(f"Vue « {name} » enregistrée depuis la visionneuse 3D.")
        except queue.Empty:
            pass
        root.after(300, poll_viewer)

    def open_viewer():
        collect()
        if not (data_dir() / key / "geometrie.pkl").exists():
            try:
                say("Premier export du modèle pour la visionneuse...")
                prepare_geometry(state, proj, say)
            except Exception as error:  # noqa: BLE001
                show([f"Export impossible : {error}"])
                return
        try:
            snap.clear()
            snap.update(viewer_infos())          # calcule ici (fil principal de Tk), lu ensuite par le serveur web
        except Exception as error:  # noqa: BLE001
            show([f"Visionneuse : {error}"])
            return
        try:
            viewer_mesh(key)                     # prepare le modele avant d'ouvrir la page
        except Exception as error:  # noqa: BLE001
            show([f"Modèle 3D illisible : {error}"])
            return
        if viewer["srv"] is None:
            viewer["srv"] = ViewerServer(key, lambda: dict(snap), inbox.put)
            viewer["srv"].etat = lambda: {"hdri_rot": float(opts.get("hdri_rot", 0.0)),
                                          "intensite": float(opts.get("soleil_intensite", 1.0))}
        viewer["srv"].open()
        say("Visionneuse 3D ouverte : place-toi, puis « Rendre cette vue ». (Elle montre le dernier export du modèle.)")

    root.after(300, poll_viewer)
    materials_tab(root, nb, state, proj, key)
    objects_tab(root, nb, state, proj, key)
    root.protocol("WM_DELETE_WINDOW", lambda: (collect(), root.destroy()))
    root.after(100, compute_autos)
    root.mainloop()



# ------------------------------------------------------------------ objets ---
CALQUE_OBJETS = "V-Ray Link - Objets Cosmos"
ECHELLE_COSMOS = 0.01          # les modeles Cosmos (.vrmesh) sont en centimetres ; reglable par objet


def write_objects(path: Path, objs: list, report=print) -> int:
    """Objets Cosmos places : proxy .vrmesh + materiau .vrmat, position/rotation/echelle."""
    n = 0
    with path.open("w", encoding="utf-8") as out:
        out.write("// Objets Cosmos (V-Ray Link)\n")
        for k, o in enumerate(objs):
            try:
                folder = Path(o["dossier"])
                mesh = sorted(folder.glob("*.vrmesh"), key=_lod)[0]
                vrmat = sorted(folder.glob("*.vrmat"), key=_lod)[0]
                fixed, top = vrmat_for_render(vrmat)
            except Exception as error:  # noqa: BLE001
                report(f"Objet « {o.get('nom')} » indisponible : {error}")
                continue
            a = math.radians(float(o.get("rotation", 0.0)))
            s = float(o.get("echelle", 1.0)) * ECHELLE_COSMOS
            c, si = math.cos(a) * s, math.sin(a) * s
            oid = f"obj{k}"
            out.write(f"// {o.get('nom')}\nGeomMeshFile {oid}_geo {{\n  file=\"{str(mesh).replace(chr(92), '/')}\";\n}}\n"
                      f"MtlVRmat {oid}_mtl {{\n  filename=\"{str(fixed).replace(chr(92), '/')}\";\n  mtlname=\"{top}\";\n}}\n"
                      f"Node {oid} {{\n  transform=Transform(Matrix(Vector({c:.6f},{si:.6f},0),Vector({-si:.6f},{c:.6f},0),"
                      f"Vector(0,0,{s:.6f})),Vector({o['x']:.4f},{o['y']:.4f},{o['z']:.4f}));\n"
                      f"  geometry={oid}_geo;\n  material={oid}_mtl;\n}}\n")
            n += 1
    return n



SOL_AUTOUR = "Sol autour (gazon)"
ZONE_PLAN = "Zone dessinée dans le plan…"
DALLES_SEL = "Dalles sélectionnées dans Archicad"


def _inside(poly, x, y) -> bool:
    ok, j = False, len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            ok = not ok
        j = i
    return ok


def selected_slab_zones() -> list:
    """Contours (et trous) des dalles / maillages / zones selectionnes dans Archicad, avec leur hauteur de dessus."""
    els = tapir("GetSelectedElements").get("elements", [])
    els = [{"elementId": e["elementId"]} for e in els if isinstance(e, dict) and "elementId" in e]
    if not els:
        return []
    det = tapir("GetDetailsOfElements", {"elements": els, "fields": ["type", "details"]}).get("detailsOfElements", [])
    try:
        boxes = tapir("Get3DBoundingBoxes", {"elements": els}).get("boundingBoxes3D", [])
    except Exception:  # noqa: BLE001
        boxes = []
    zones = []
    for k, d in enumerate(det):
        if not isinstance(d, dict) or "error" in d:
            continue
        dd = d.get("details", {}) or {}
        outline = dd.get("polygonOutline") or dd.get("polygonCoordinates") or dd.get("coordinates") or []
        poly = [[float(c["x"]), float(c["y"])] for c in outline if "x" in c]
        if len(poly) < 3:
            continue
        holes = [[[float(c["x"]), float(c["y"])] for c in (h.get("polygonOutline") or h.get("polygonCoordinates") or [])]
                 for h in dd.get("holes", []) or []]
        b = boxes[k].get("boundingBox3D", boxes[k]) if k < len(boxes) and isinstance(boxes[k], dict) else {}
        z = float(b.get("zMax", dd.get("zCoordinate", 0.0)))
        zones.append({"poly": poly, "trous": [h for h in holes if len(h) >= 3], "z": z})
    return zones
SCATTER_TYPES = {  # densite affichee, unite (m2 par unite), variation %, espacement min (m), version legere
    "Herbe / gazon": (6.0, 1.0, 30, 0.0, True),
    "Fleurs / buissons": (1.0, 1.0, 25, 0.6, True),
    "Arbres": (2.0, 100.0, 20, 5.0, False),
}
SCATTER_MAX = 30000


def scatter_points(sc: dict, meshes: dict, offset: tuple, ground: float, key: str) -> list:
    """Points (x, y, z) repartis au hasard (graine fixe) sur une surface Archicad (faces vers le haut) ou sur le sol autour."""
    import random
    rnd = random.Random(f"{sc.get('dossier')}|{sc.get('surface')}")
    dens = max(0.01, float(sc.get("densite", 4.0)))
    ox, oy, oz = offset
    pts = []
    if sc.get("zones"):                                   # dalles selectionnees dans Archicad
        for zn in sc["zones"]:
            poly = zn["poly"]
            xs, ys = [q[0] for q in poly], [q[1] for q in poly]
            n = min(SCATTER_MAX * 4, int((max(xs) - min(xs)) * (max(ys) - min(ys)) * dens))
            for _ in range(n):
                x, y = rnd.uniform(min(xs), max(xs)), rnd.uniform(min(ys), max(ys))
                if _inside(poly, x, y) and not any(_inside(h, x, y) for h in zn.get("trous", [])):
                    pts.append((x, y, float(zn["z"])))
        return _spaced(pts, float(sc.get("espacement", 0.0)))
    if sc.get("surface") == ZONE_PLAN and sc.get("zone"):
        poly = sc["zone"]
        xs, ys = [q[0] for q in poly], [q[1] for q in poly]
        n = min(SCATTER_MAX * 4, int((max(xs) - min(xs)) * (max(ys) - min(ys)) * dens))

        def inside(x, y):
            ok, j = False, len(poly) - 1
            for i in range(len(poly)):
                (xi, yi), (xj, yj) = poly[i], poly[j]
                if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
                    ok = not ok
                j = i
            return ok
        for _ in range(n):
            x, y = rnd.uniform(min(xs), max(xs)), rnd.uniform(min(ys), max(ys))
            if inside(x, y):
                pts.append((x, y, float(sc.get("z", ground))))
        return _spaced(pts, float(sc.get("espacement", 0.0)))
    if sc.get("surface") == SOL_AUTOUR:
        f = plan_footprint(key) or {}
        bati = {tuple(c) for c in f.get("sols", []) + f.get("murs", [])}
        if not f:
            return []
        n_i = max([c[0] for c in bati] + [0]) + 1
        n_j = max([c[1] for c in bati] + [0]) + 1
        margin = float(sc.get("rayon", 15.0))
        x0, y0 = f["x0"] - margin, f["y0"] - margin
        x1, y1 = f["x0"] + n_i * f["pas"] + margin, f["y0"] + n_j * f["pas"] + margin
        n = min(SCATTER_MAX, int((x1 - x0) * (y1 - y0) * dens))
        for _ in range(n):
            x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
            cell = (int((x - f["x0"]) // f["pas"]), int((y - f["y0"]) // f["pas"]))
            if cell not in bati:
                pts.append((x, y, ground))
        return _spaced(pts, float(sc.get("espacement", 0.0)))
    verts, faces = meshes.get(sc.get("surface"), ([], []))
    up, flat = [], []
    for t in range(0, len(faces), 3):
        a, b, c = verts[faces[t]], verts[faces[t + 1]], verts[faces[t + 2]]
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        nx, ny, nz = u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]
        area = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
        if area < 1e-6 or abs(nz) / (2 * area) < 0.7:          # seulement les faces horizontales
            continue
        (up if nz > 0 else flat).append((a, b, c, area))      # normale vers le haut = dessus de la dalle
    def mean_z(group):
        a_ = sum(t[3] for t in group) or 1.0
        return sum(t[3] * (t[0][2] + t[1][2] + t[2][2]) / 3 for t in group) / a_
    # le dessus de la dalle = le groupe de faces horizontales le plus haut (l'orientation IFC n'est pas fiable)
    chosen = up if (up and (not flat or mean_z(up) >= mean_z(flat))) else (flat or up)
    tris, total = [], 0.0
    for a, b, c, area in chosen:
        tris.append((total, a, b, c, area))
        total += area
    if not tris:
        return []
    n = min(SCATTER_MAX, int(total * dens))
    import bisect
    keys = [t[0] for t in tris]
    tops = {}
    for _ in range(n):
        k = bisect.bisect_right(keys, rnd.uniform(0, total)) - 1
        _s, a, b, c, _ar = tris[k]
        r1, r2 = rnd.random(), rnd.random()
        if r1 + r2 > 1:
            r1, r2 = 1 - r1, 1 - r2
        x = a[0] + r1 * (b[0] - a[0]) + r2 * (c[0] - a[0])
        y = a[1] + r1 * (b[1] - a[1]) + r2 * (c[1] - a[1])
        z = a[2] + r1 * (b[2] - a[2]) + r2 * (c[2] - a[2])
        cell = (round(x, 1), round(y, 1))
        if cell in tops and tops[cell] >= z:
            continue
        tops[cell] = z
        pts.append((x + ox, y + oy, z + oz))
    return _spaced(pts, float(sc.get("espacement", 0.0)))


def _spaced(pts: list, gap: float) -> list:
    """Retire les points trop proches (arbres : espacement minimal entre troncs)."""
    if gap <= 0:
        return pts[:SCATTER_MAX]
    grid, out = {}, []
    for p in pts:
        cx, cy = int(p[0] // gap), int(p[1] // gap)
        if any(math.hypot(p[0] - q[0], p[1] - q[1]) < gap
               for i in (-1, 0, 1) for j in (-1, 0, 1) for q in grid.get((cx + i, cy + j), ())):
            continue
        grid.setdefault((cx, cy), []).append(p)
        out.append(p)
        if len(out) >= SCATTER_MAX:
            break
    return out


def write_scatter(path: Path, scatters: list, meshes: dict, offset: tuple, ground: float, key: str, report=print) -> int:
    """Ajoute a objets.vrscene les objets repartis : une geometrie (.vrmesh) partagee, un Node par exemplaire."""
    import random
    total = 0
    with path.open("a", encoding="utf-8") as out:
        for k, sc in enumerate(scatters):
            try:
                folder = Path(sc["dossier"])
                meshes_ = sorted(folder.glob("*.vrmesh"), key=_lod)
                lods = [m_ for m_ in meshes_ if _lod(m_) < 4] or meshes_     # jamais un fichier sans niveau de detail connu
                mesh = lods[-1 if sc.get("leger", True) else 0]
                mats_ = sorted(folder.glob("*.vrmat"), key=_lod)
                vrmat = next((m_ for m_ in mats_ if _lod(m_) == _lod(mesh)), mats_[0])   # meme niveau de detail que le maillage
                fixed, top = vrmat_for_render(vrmat)
                missing = vrmat_missing(fixed)
            except Exception as error:  # noqa: BLE001
                report(f"Scatter « {sc.get('nom')} » indisponible : {error}")
                continue
            pts = scatter_points(sc, meshes, offset, ground, key)
            if len(pts) >= SCATTER_MAX:
                report(f"Scatter « {sc.get('nom')} » limité à {SCATTER_MAX} exemplaires (baisse la densité).")
            sid = f"sc{k}"
            out.write(f"// scatter : {sc.get('nom')} sur {sc.get('surface')} ({len(pts)} exemplaires)\n"
                      f"GeomMeshFile {sid}_geo {{\n  file=\"{str(mesh).replace(chr(92), '/')}\";\n}}\n")
            if missing or sc.get("couleur_simple"):
                report(f"Scatter « {sc.get('nom')} » : textures introuvables ({', '.join(missing[:3])}) → couleur verte simple")
                out.write(f"BRDFVRayMtl {sid}_brdf {{ diffuse=AColor(0.06,0.16,0.03,1); reflect=AColor(0.08,0.08,0.08,1); "
                          f"reflect_glossiness=0.45; fresnel=1; translucency_color=AColor(0.1,0.25,0.05,1); }}\n"
                          f"MtlSingleBRDF {sid}_mtl {{ brdf={sid}_brdf; double_sided=1; }}\n")
            else:
                out.write(f"MtlVRmat {sid}_mtl {{\n  filename=\"{str(fixed).replace(chr(92), '/')}\";\n  mtlname=\"{top}\";\n}}\n")
            rnd = random.Random(k)
            base, var = float(sc.get("echelle", 1.0)) * ECHELLE_COSMOS, float(sc.get("variation", 30)) / 100.0
            for i, (x, y, z) in enumerate(pts):
                a = rnd.uniform(0, 2 * math.pi)
                sz = base * (1 + rnd.uniform(-var, var))
                c, si = math.cos(a) * sz, math.sin(a) * sz
                out.write(f"Node {sid}_{i} {{ transform=Transform(Matrix(Vector({c:.5f},{si:.5f},0),Vector({-si:.5f},{c:.5f},0),"
                          f"Vector(0,0,{sz:.5f})),Vector({x:.3f},{y:.3f},{z:.3f})); geometry={sid}_geo; material={sid}_mtl; }}\n")
            total += len(pts)
            if pts:
                zs = [p_[2] for p_ in pts]
                report(f"Scatter « {sc.get('nom')} » : {len(pts)} exemplaires, posés entre {min(zs):.2f} et {max(zs):.2f} m "
                       f"(maillage {mesh.name}, matériau {vrmat.name} › {top})")
            else:
                report(f"Scatter « {sc.get('nom')} » : 0 exemplaire (surface sans dessus horizontal ?)")
    return total


def draw_object_marker(o: dict) -> list:
    """Repere de l'objet dans le plan : cercle + fleche d'orientation + nom (calque des objets Cosmos)."""
    story = tapir("GetStories").get("actStory", 0)
    layer = _layer_index(CALQUE_OBJETS)
    x, y, r = o["x"], o["y"], 0.4 * float(o.get("echelle", 1.0))
    a = math.radians(float(o.get("rotation", 0.0)))
    circle = [{"x": x + r * math.cos(t * math.pi / 8), "y": y + r * math.sin(t * math.pi / 8)} for t in range(17)]
    arrow = [{"x": x, "y": y}, {"x": x + r * 1.4 * math.cos(a - math.pi / 2), "y": y + r * 1.4 * math.sin(a - math.pi / 2)}]
    guids = []
    try:
        res = tapir("CreatePolylines", {"polylinesData": [
            {"floorInd": story, "layerIndex": layer, "linePenIndex": 20, "coordinates": circle},
            {"floorInd": story, "layerIndex": layer, "linePenIndex": 20, "coordinates": arrow}]}).get("elements", [])
        guids += [e["elementId"]["guid"] for e in res if "elementId" in e]
        res = tapir("CreateTexts", {"textsData": [{"coordinate": {"x": x, "y": y - r - 0.35, "z": 0.0}, "text": o["nom"],
                                                   "floorIndex": story, "style": {"height": 2.0, "penIndex": 20,
                                                   "anchor": "MiddleMiddle", "justification": "Center"}}]}).get("elements", [])
        guids += [e["elementId"]["guid"] for e in res if "elementId" in e]
        if guids:
            tapir("SetDetailsOfElements", {"elementsWithDetails": [
                {"elementId": {"guid": g}, "details": {"layerIndex": layer}} for g in guids]})
            tapir("CreateGroups", {"elementGroups": [{"elements": [{"elementId": {"guid": g}} for g in guids]}]})
    except Exception:  # noqa: BLE001
        pass
    return guids


CALQUE_VEGETATION = "V-Ray Link - Végétation"


def draw_scatter_in_plan(sc: dict, key: str) -> list:
    """Apercu du scatter dans le plan : un petit rond par touffe (echantillon), un grand par arbre, + nom."""
    try:
        meshes, _m, offset, ground = pickle.loads((data_dir() / key / "geometrie.pkl").read_bytes())
    except Exception:  # noqa: BLE001
        meshes, offset, ground = {}, (0.0, 0.0, 0.0), 0.0
    pts = scatter_points(sc, meshes, offset, ground, key)
    if not pts:
        return []
    tree = sc.get("type") == "Arbres"
    import random
    sample = pts if len(pts) <= 600 else random.Random(1).sample(pts, 600)
    r = 2.0 * float(sc.get("echelle", 1.0)) if tree else 0.12
    n = 12 if tree else 5
    story = tapir("GetStories").get("actStory", 0)
    layer = _layer_index(CALQUE_VEGETATION)
    data = [{"floorInd": story, "layerIndex": layer, "linePenIndex": 3 if not tree else 16,
             "coordinates": [{"x": x + r * math.cos(2 * math.pi * k / n), "y": y + r * math.sin(2 * math.pi * k / n)}
                             for k in range(n + 1)]} for x, y, _z in sample]
    guids = []
    for i in range(0, len(data), 300):
        try:
            res = tapir("CreatePolylines", {"polylinesData": data[i:i + 300]}).get("elements", [])
            guids += [e["elementId"]["guid"] for e in res if "elementId" in e]
        except Exception:  # noqa: BLE001
            break
    try:
        cx, cy = sum(p[0] for p in sample) / len(sample), sum(p[1] for p in sample) / len(sample)
        res = tapir("CreateTexts", {"textsData": [{"coordinate": {"x": cx, "y": cy, "z": 0.0},
                                                   "text": f"{sc['nom']} ({len(pts)})", "floorIndex": story,
                                                   "style": {"height": 2.5, "bold": True, "penIndex": 3,
                                                             "anchor": "MiddleMiddle", "justification": "Center"}}]})
        guids += [e["elementId"]["guid"] for e in res.get("elements", []) if "elementId" in e]
        for i in range(0, len(guids), 300):
            tapir("SetDetailsOfElements", {"elementsWithDetails": [
                {"elementId": {"guid": g}, "details": {"layerIndex": layer}} for g in guids[i:i + 300]]})
        tapir("CreateGroups", {"elementGroups": [{"elements": [{"elementId": {"guid": g}} for g in guids]}]})
    except Exception:  # noqa: BLE001
        pass
    return guids


def sync_objects_from_plan(objs: list) -> tuple[list, str]:
    """Relit les reperes d'objets Cosmos dans le plan (calque « V-Ray Link - Objets Cosmos ») : ce qui a ete copie,
    deplace, tourne ou efface dans Archicad est repris tel quel. Renvoie (objets, resume)."""
    rows = tapir("GetAttributesByType", {"attributeType": "Layer"}).get("attributes", [])
    lay = next((r for r in rows if r.get("name") == CALQUE_OBJETS), None)
    if not lay:
        return objs, ""
    layer = int(lay["index"])
    stories = {s_.get("index"): float(s_.get("level", 0.0)) for s_ in tapir("GetStories").get("stories", [])}

    def read(kind):
        ids = tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
        out = []
        for i in range(0, len(ids), 400):
            part = ids[i:i + 400]
            det = tapir("GetDetailsOfElements", {"elements": part, "fields": ["floorIndex", "layerIndex", "details"]}
                        ).get("detailsOfElements", [])
            for el, d in zip(part, det):
                if isinstance(d, dict) and "error" not in d and int(d.get("layerIndex", -1)) == layer:
                    out.append((el["elementId"]["guid"], d))
        return out

    polys, texts = read("PolyLine"), read("Text")
    circles, arrows = [], []
    for g, d in polys:
        pts = [(float(c["x"]), float(c["y"])) for c in (d.get("details") or {}).get("coordinates", [])]
        if len(pts) >= 12:
            cx, cy = sum(p[0] for p in pts[:-1]) / (len(pts) - 1), sum(p[1] for p in pts[:-1]) / (len(pts) - 1)
            circles.append({"g": g, "c": (cx, cy), "r": math.dist((cx, cy), pts[0]), "f": d.get("floorIndex", 0)})
        elif len(pts) == 2:
            arrows.append({"g": g, "a": pts[0], "b": pts[1]})
    labels = []
    for g, d in texts:
        dd = d.get("details") or {}
        pos = dd.get("coordinate") or dd.get("position") or dd.get("origin") or {}
        txt = str(dd.get("text") or dd.get("content") or "").strip()
        if txt and "x" in pos:
            labels.append({"g": g, "p": (float(pos["x"]), float(pos["y"])), "t": txt})
    by_name = {o["nom"]: o["dossier"] for o in objs}
    lib = {e["nom"]: e["dossier"] for e in cosmos_library("3D_Models")}
    out, lost = [], 0
    for c in circles:
        arrow = min(arrows, key=lambda a: math.dist(a["a"], c["c"]), default=None)
        rot = 0.0
        traces = [c["g"]]
        if arrow and math.dist(arrow["a"], c["c"]) < 0.05 + c["r"] * 0.1:
            rot = (math.degrees(math.atan2(arrow["b"][1] - arrow["a"][1], arrow["b"][0] - arrow["a"][0])) + 90) % 360
            traces.append(arrow["g"])
        lab = min(labels, key=lambda t_: math.dist(t_["p"], (c["c"][0], c["c"][1] - c["r"] - 0.35)), default=None)
        if not lab or math.dist(lab["p"], c["c"]) > c["r"] + 2.0:
            lost += 1
            continue
        traces.append(lab["g"])
        folder = by_name.get(lab["t"]) or lib.get(lab["t"])
        if not folder:
            lost += 1
            continue
        out.append({"nom": lab["t"], "dossier": folder, "x": c["c"][0], "y": c["c"][1],
                    "z": stories.get(c["f"], 0.0), "rotation": round(rot, 1), "echelle": round(c["r"] / 0.4, 3),
                    "traces": traces})
    note = f"{len(out)} objet(s) Cosmos lus dans le plan" + (f" ({lost} repère(s) incomplet(s) ignoré(s))" if lost else "")
    return out, note


def delete_elements(guids: list) -> None:
    for i in range(0, len(guids or []), 300):
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in guids[i:i + 300]]})
        except Exception:  # noqa: BLE001
            pass


def objects_tab(root, nb, state: dict, proj: dict, key: str) -> None:
    import tkinter as tk
    from tkinter import ttk

    tab = ttk.Frame(nb, padding=10)
    nb.add(tab, text="  Objets  ")
    tab.columnconfigure(0, weight=2)
    tab.columnconfigure(1, weight=3)
    tab.rowconfigure(0, weight=1)
    msg = tk.StringVar(value="1. Choisis un objet à droite   2. « Placer dans Archicad » puis clique dans le plan   "
                             "3. Ajuste rotation / taille à gauche.")
    lib: list = []
    images: dict = {}
    cells: dict = {}
    chosen = {"entry": None}
    objs = state.setdefault("objets", {}).setdefault(key, [])
    scats = state.setdefault("scatter", {}).setdefault(key, [])

    # ---------------- gauche : objets places
    left = ttk.LabelFrame(tab, text="Objets placés dans le projet", padding=6)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    left.rowconfigure(0, weight=1)
    left.columnconfigure(0, weight=1)
    tree = ttk.Treeview(left, columns=("rot", "ech"), show="tree headings", height=16, selectmode="extended")
    tree.heading("#0", text="Objet")
    tree.heading("rot", text="Rotation")
    tree.heading("ech", text="Taille")
    tree.column("#0", width=200)
    tree.column("rot", width=70, anchor="center")
    tree.column("ech", width=60, anchor="center")
    tree.grid(row=0, column=0, columnspan=6, sticky="nsew")
    ttk.Button(left, text="↺ 15°", width=7, command=lambda: rotate(-15)).grid(row=1, column=0, pady=4)
    ttk.Button(left, text="↻ 15°", width=7, command=lambda: rotate(15)).grid(row=1, column=1)
    ttk.Button(left, text="↻ 90°", width=7, command=lambda: rotate(90)).grid(row=1, column=2)
    size_var = tk.DoubleVar(value=1.0)
    ttk.Spinbox(left, from_=0.1, to=50, increment=0.1, textvariable=size_var, width=5).grid(row=1, column=3, padx=(8, 2))
    ttk.Button(left, text="Taille", command=lambda: resize()).grid(row=1, column=4)
    ttk.Button(left, text="Supprimer", command=lambda: remove()).grid(row=1, column=5, padx=4)
    ttk.Label(left, foreground="#777", wraplength=360, justify="left",
              text="Taille 1 = taille réelle de l'objet Cosmos. Si un objet sort 100 fois trop grand ou trop petit, "
                   "mets 0.01 ou 100 et dis-le-moi : je corrigerai l'unité par défaut.").grid(
        row=2, column=0, columnspan=6, sticky="w")

    def refresh_tree():
        tree.delete(*tree.get_children())
        for k, o in enumerate(objs):
            tree.insert("", "end", iid=str(k), text=o["nom"], values=(f"{o.get('rotation', 0):.0f}°", f"{o.get('echelle', 1):g}"))
        for k, sc in enumerate(scats):
            unit = SCATTER_TYPES.get(sc.get("type", ""), (0, 1.0))[1]
            dtxt = f"{sc['densite'] * unit:g}/m²" if unit == 1 else f"{sc['densite'] * unit:g} pour {unit:g} m²"
            lieu = {SOL_AUTOUR: "autour de la maison", ZONE_PLAN: "zone dessinée"}.get(sc["surface"], sc["surface"])
            tree.insert("", "end", iid=f"s{k}", text=f"{'🌳' if unit != 1 else '🌿'} {sc['nom']} — {lieu} ({dtxt})",
                        values=("au hasard", f"{sc.get('echelle', 1):g}"))

    def selected() -> list:
        out = []
        for i in tree.selection():
            if i.startswith("s"):
                if int(i[1:]) < len(scats):
                    out.append(scats[int(i[1:])])
            elif int(i) < len(objs):
                out.append(objs[int(i)])
        return out

    def redraw(o):
        if o in scats:
            return
        delete_elements(o.get("traces", []))
        o["traces"] = draw_object_marker(o)

    def rotate(deg):
        for o in selected():
            if o in scats:
                continue
            o["rotation"] = (float(o.get("rotation", 0)) + deg) % 360
            redraw(o)
        save_settings(state)
        keep = tree.selection()
        refresh_tree()
        tree.selection_set(keep)

    def resize():
        try:
            v = float(size_var.get())
        except (ValueError, tk.TclError):
            return
        for o in selected():
            o["echelle"] = v
            redraw(o)
        save_settings(state)
        keep = tree.selection()
        refresh_tree()
        tree.selection_set(keep)

    def remove():
        for o in selected():
            if o in scats:
                delete_elements(o.get("traces", []))
                scats.remove(o)
                continue
            delete_elements(o.get("traces", []))
            objs.remove(o)
        save_settings(state)
        refresh_tree()
        msg.set("Objet(s) supprimé(s) du rendu et du plan.")

    # ---------------- droite : bibliotheque Cosmos (modeles 3D)
    right = ttk.LabelFrame(tab, text="Objets 3D (Chaos Cosmos)", padding=6)
    right.grid(row=0, column=1, sticky="nsew")
    right.rowconfigure(2, weight=1)
    right.columnconfigure(0, weight=1)
    head = ttk.Frame(right)
    head.grid(row=0, column=0, sticky="ew")
    ttk.Button(head, text="🌐 Ouvrir Chaos Cosmos", command=lambda: open_cosmos()).pack(side="left")
    ttk.Button(head, text="↻", width=3, command=lambda: load_library()).pack(side="left", padx=4)
    srch = ttk.Frame(right)
    srch.grid(row=1, column=0, sticky="ew", pady=(6, 4))
    query, cat = tk.StringVar(), tk.StringVar(value="Toutes")
    ttk.Label(srch, text="Rechercher").pack(side="left")
    ttk.Entry(srch, textvariable=query, width=20).pack(side="left", padx=4)
    cat_box = ttk.Combobox(srch, textvariable=cat, state="readonly", width=16, values=["Toutes"])
    cat_box.pack(side="left", padx=4)
    canvas = tk.Canvas(right, width=470, height=360, background="#fafafa", highlightthickness=0)
    canvas.grid(row=2, column=0, sticky="nsew")
    vs = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
    vs.grid(row=2, column=1, sticky="ns")
    canvas.configure(yscrollcommand=vs.set)
    grid = tk.Frame(canvas, background="#fafafa")
    canvas.create_window((0, 0), window=grid, anchor="nw")
    grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    empty = ttk.Label(right, foreground="#777", wraplength=440, justify="left", text=(
        "Aucun objet Cosmos sur ce PC pour l'instant.\n\nClique « Ouvrir Chaos Cosmos », choisis un objet "
        "(meubles, plantes, voitures, personnages…) et télécharge-le : il apparaît ici tout seul."))
    blank = tk.PhotoImage(width=96, height=96)
    blank.put("#e4e4e4", to=(0, 0, 96, 96))

    def highlight(entry):
        chosen["entry"] = entry
        for folder, cell in cells.items():
            on = entry is not None and folder == entry["dossier"]
            for w in (cell, *cell.winfo_children()):
                w.configure(background="#cfe6ff" if on else "#fafafa")
        if entry:
            msg.set(f"Objet choisi : {entry['nom']}. Clique « Placer dans Archicad » puis clique son emplacement dans le plan.")

    def fill_grid():
        for w in grid.winfo_children():
            w.destroy()
        cells.clear()
        q, c = query.get().strip().lower(), cat.get()
        shown = [e for e in lib if (c == "Toutes" or e["categorie"] == c) and (not q or q in e["nom"].lower())]
        if lib:
            empty.grid_forget()
        else:
            empty.grid(row=2, column=0, sticky="nw", padx=10, pady=10)
        for k, e in enumerate(shown):
            cell = tk.Frame(grid, background="#fafafa", padx=4, pady=4, cursor="hand2")
            cell.grid(row=k // 4, column=k % 4, sticky="n")
            tk.Label(cell, background="#fafafa", image=images.get(e["dossier"]) or blank).pack()
            tk.Label(cell, text=e["nom"], background="#fafafa", wraplength=104, font=("Segoe UI", 8)).pack()
            cells[e["dossier"]] = cell
            for w in (cell, *cell.winfo_children()):
                w.bind("<Button-1>", lambda _e, en=e: highlight(en))
                w.bind("<Double-Button-1>", lambda _e, en=e: (highlight(en), place()))
        if chosen["entry"] and chosen["entry"]["dossier"] in cells:
            highlight(chosen["entry"])
        todo = [e for e in shown if e["dossier"] not in images]

        def thumbs():
            if not todo:
                return
            e = todo.pop(0)
            images[e["dossier"]] = thumbnail(e)
            cell = cells.get(e["dossier"])
            if cell and images[e["dossier"]]:
                cell.winfo_children()[0].configure(image=images[e["dossier"]])
            root.after(15, thumbs)
        thumbs()

    def load_library(select_name: str = ""):
        nonlocal lib
        lib = [e for e in cosmos_library("3D_Models") if e.get("vrmesh")]
        cat_box.configure(values=["Toutes"] + sorted({e["categorie"] for e in lib}))
        if select_name:
            hit = next((e for e in lib if select_name.lower().replace(" ", "") in e["nom"].lower().replace(" ", "")), None)
            if hit:
                chosen["entry"] = hit
        fill_grid()

    query.trace_add("write", lambda *_: fill_grid())
    cat_box.bind("<<ComboboxSelected>>", lambda *_: fill_grid())

    def place():
        e = chosen["entry"]
        if not e:
            msg.set("Choisis d'abord un objet à droite.")
            return
        root.withdraw()
        try:
            T.focus_archicad()
            p, why = T.pick_point(f"V-Ray : clique l'emplacement de « {e['nom']} »")
            z0 = story_level()
        finally:
            root.deiconify()
        if p is None:
            msg.set(f"Placement annulé. {why}")
            return
        o = {"nom": e["nom"], "dossier": e["dossier"], "x": p["x"], "y": p["y"], "z": z0, "rotation": 0.0, "echelle": 1.0}
        o["traces"] = draw_object_marker(o)
        objs.append(o)
        save_settings(state)
        refresh_tree()
        tree.selection_set(str(len(objs) - 1))
        msg.set(f"« {e['nom']} » placé (repère orange dans le plan, calque « {CALQUE_OBJETS} »). "
                "Il apparaîtra au prochain rendu. Double-clic sur un objet à droite = le placer encore.")

    simple = ttk.Frame(tab)
    simple.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Button(simple, text="📍 Placer un objet (clic dans le plan)", command=place).grid(row=0, column=0, padx=3, ipady=4)
    ttk.Button(simple, text="🌱 Ajouter de la végétation (herbe, fleurs, arbres)…",
               command=lambda: assistant()).grid(row=0, column=1, padx=8, ipady=4)
    def show_vegetation():
        n = 0
        for sc in scats:
            delete_elements(sc.get("traces", []))
            sc["traces"] = draw_scatter_in_plan(sc, key)
            n += 1 if sc["traces"] else 0
        save_settings(state)
        msg.set(f"Végétation dessinée dans le plan (calque « {CALQUE_VEGETATION} ») : {n} sur {len(scats)}."
                + ("" if n == len(scats) else "  Les autres n'ont aucun emplacement : fais d'abord un rendu (export du modèle)."))

    ttk.Button(simple, text="👁 Voir la végétation dans le plan", command=show_vegetation).grid(row=0, column=3, padx=3, ipady=4)

    def resync(quiet=False):
        try:
            new, note = sync_objects_from_plan(objs)
        except Exception as error:  # noqa: BLE001
            if not quiet:
                msg.set(f"Lecture du plan impossible : {error}")
            return
        if note:
            objs[:] = new
            save_settings(state)
            refresh_tree()
            if not quiet:
                msg.set(note + " : copies, déplacements, rotations et suppressions faits dans Archicad sont pris en compte.")

    ttk.Button(simple, text="↻ Relire le plan", command=resync).grid(row=0, column=6, padx=3, ipady=4)

    def quick_lawn():
        grass = [e for e in lib if kind_of(e) == "Herbe / gazon"]
        e = chosen["entry"] if chosen["entry"] in grass else (grass[0] if grass else None)
        if not e:
            msg.set("Aucune herbe téléchargée : « Ouvrir Chaos Cosmos » › 3D Models › Plants, cherche « grass ».")
            return
        try:
            zones = selected_slab_zones()
        except Exception as error:  # noqa: BLE001
            msg.set(f"Lecture de la sélection impossible : {error}")
            return
        if not zones:
            msg.set("⚠ Sélectionne d'abord dans Archicad la ou les dalles de gazon, puis reclique « Gazon sur la sélection ».")
            return
        _d, _u, var, gap, light = SCATTER_TYPES["Herbe / gazon"]
        item = {"nom": e["nom"], "dossier": e["dossier"], "surface": f"{len(zones)} dalle(s) sélectionnée(s)",
                "zones": zones, "densite": 5.0, "variation": var, "echelle": 1.0, "espacement": gap, "leger": light,
                "type": "Herbe / gazon"}
        add_scatter(item)
        n = len(scatter_points(item, {}, (0, 0, 0), 0.0, key))
        msg.set(f"✔ « {e['nom']} » posé sur {len(zones)} dalle(s) : {n:,} touffes (ronds verts dans le plan). ".replace(",", " ")
                + "Lance un rendu pour la voir.")

    ttk.Button(simple, text="🌿 Gazon sur la sélection", command=quick_lawn).grid(row=0, column=2, padx=3, ipady=4)
    adv_on = tk.BooleanVar(value=False)
    ttk.Checkbutton(simple, text="Réglages experts", variable=adv_on,
                    command=lambda: acts.grid() if adv_on.get() else acts.grid_remove()).grid(row=0, column=4, padx=8)
    acts = ttk.Frame(tab)
    acts.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    acts.grid_remove()
    ttk.Separator(acts, orient="vertical").grid(row=0, column=1, sticky="ns", padx=8)
    type_var = tk.StringVar(value="Herbe / gazon")
    type_box = ttk.Combobox(acts, textvariable=type_var, values=list(SCATTER_TYPES), state="readonly", width=15)
    type_box.grid(row=1, column=2, columnspan=2, sticky="w", padx=3, pady=(4, 0))
    unit_lbl = tk.StringVar(value="par m²")
    ttk.Label(acts, text="🌿 Scatter sur").grid(row=0, column=2)
    surf_var = tk.StringVar(value=SOL_AUTOUR)
    surf_box = ttk.Combobox(acts, textvariable=surf_var, width=22, state="readonly")
    surf_box.grid(row=0, column=3, padx=3)
    ttk.Label(acts, text="Densité").grid(row=0, column=4)
    ttk.Label(acts, textvariable=unit_lbl, foreground="#777").grid(row=1, column=4, columnspan=2, sticky="w")
    gap_var = tk.DoubleVar(value=0.0)
    ttk.Label(acts, text="Espacement min (m)").grid(row=1, column=6, sticky="e", pady=(4, 0))
    ttk.Spinbox(acts, from_=0, to=30, increment=0.5, textvariable=gap_var, width=4).grid(row=1, column=7, padx=3, pady=(4, 0))

    def type_changed(_e=None):
        d, unit, var, gap, _l = SCATTER_TYPES[type_var.get()]
        dens_var.set(d)
        var_var.set(var)
        gap_var.set(gap)
        unit_lbl.set("par m²" if unit == 1 else f"pour {unit:g} m²")

    type_box.bind("<<ComboboxSelected>>", type_changed)
    dens_var = tk.DoubleVar(value=6.0)
    ttk.Spinbox(acts, from_=0.1, to=60, increment=1, textvariable=dens_var, width=5).grid(row=0, column=5, padx=3)
    ttk.Label(acts, text="Variation %").grid(row=0, column=6)
    var_var = tk.IntVar(value=30)
    ttk.Spinbox(acts, from_=0, to=90, increment=5, textvariable=var_var, width=4).grid(row=0, column=7, padx=3)
    ttk.Button(acts, text="Répartir l'objet choisi", command=lambda: scatter()).grid(row=0, column=8, padx=3)

    def load_surface_names():
        names, _ = project_surfaces(state, key)
        grass = sorted(n for n in names if n != SOL_AUTOUR and any(w in n.lower() for w in ("grass", "gazon", "herbe", "pelouse", "lawn")))
        surf_box.configure(values=[SOL_AUTOUR, ZONE_PLAN] + grass +
                           sorted(n for n in names if n not in grass and n != SOL_AUTOUR))
        if grass and surf_var.get() == SOL_AUTOUR:
            surf_var.set(grass[0])

    def scatter():
        e = chosen["entry"]
        if not e:
            msg.set("Choisis d'abord à droite l'objet à répartir (touffe d'herbe, fleurs, gravier…).")
            return
        try:
            shown, var, gap = float(dens_var.get()), int(var_var.get()), float(gap_var.get())
        except (ValueError, tk.TclError):
            return
        _d, unit, _v, _g, light = SCATTER_TYPES[type_var.get()]
        item = {"nom": e["nom"], "dossier": e["dossier"], "surface": surf_var.get(), "densite": shown / unit,
                "variation": var, "echelle": 1.0, "rayon": 15.0, "espacement": gap, "leger": light, "type": type_var.get()}
        if surf_var.get() == ZONE_PLAN:
            pts, item["z"] = draw_zone()
            if len(pts) < 3:
                msg.set("Zone annulée : il faut au moins 3 points.")
                return
            item["zone"] = pts
        add_scatter(item)
        unit_txt = "par m²" if unit == 1 else f"pour {unit:g} m²"
        msg.set(f"« {e['nom']} » ({type_var.get()}) sera réparti sur « {surf_var.get()} » : {shown:g} {unit_txt}, "
                f"taille ±{var} %{f', au moins {gap:g} m entre deux' if gap else ''}, rotation au hasard. "
                "Visible au prochain rendu.")
    ttk.Label(tab, textvariable=msg, foreground="#2b5d8a", wraplength=820, justify="left").grid(
        row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # ---------------- assistant vegetation : 3 questions simples
    def add_scatter(item: dict) -> None:
        for old in [sc for sc in scats if sc["dossier"] == item["dossier"] and sc["surface"] == item["surface"]
                    and item["surface"] != ZONE_PLAN and not item.get("zones")]:
            delete_elements(old.get("traces", []))             # meme plante au meme endroit : on remplace
            scats.remove(old)
        try:
            item["traces"] = draw_scatter_in_plan(item, key)
        except Exception:  # noqa: BLE001
            item["traces"] = []
        scats.append(item)
        save_settings(state)
        refresh_tree()

    def kind_of(e) -> str:
        t = (e["nom"] + " " + e["categorie"]).lower()
        if any(w in t for w in ("tree", "arbre", "palm", "palmier", "baobab", "oak", "pine")):
            return "Arbres"
        if any(w in t for w in ("grass", "herbe", "gazon", "lawn", "turf", "clover", "moss")):
            return "Herbe / gazon"
        return "Fleurs / buissons"

    QUANTITES = {"Herbe / gazon": (2.0, 5.0, 10.0), "Fleurs / buissons": (0.3, 0.8, 1.5), "Arbres": (0.005, 0.01, 0.02)}

    def draw_zone():
        pts = []
        root.withdraw()
        try:
            T.focus_archicad()
            while True:
                p, _why = T.pick_point(f"V-Ray : clique le coin {len(pts) + 1} de la zone (Échap pour terminer, 3 coins minimum)")
                if p is None:
                    break
                pts.append([p["x"], p["y"]])
            try:
                z = story_level()
            except Exception:  # noqa: BLE001
                z = 0.0
        finally:
            root.deiconify()
        return pts, z

    def assistant():
        win = tk.Toplevel(root)
        win.title("Ajouter de la végétation")
        win.attributes("-topmost", True)
        win.transient(root)
        f = ttk.Frame(win, padding=14)
        f.grid(sticky="nsew")
        big = ("Segoe UI", 11, "bold")
        plants = [e for e in lib if kind_of(e) != "Fleurs / buissons" or
                  any(w in (e["nom"] + e["categorie"]).lower() for w in ("plant", "flower", "fleur", "bush", "shrub", "buisson"))]
        pick = {"e": chosen["entry"] if chosen["entry"] in plants else (plants[0] if plants else None)}

        ttk.Label(f, text="1.  Quelle plante ?", font=big).grid(row=0, column=0, sticky="w")
        grid_ = tk.Frame(f, background="#fafafa")
        grid_.grid(row=1, column=0, sticky="w", pady=(4, 10))
        cells_ = {}

        def choose(e):
            pick["e"] = e
            for d, c in cells_.items():
                on = d == e["dossier"]
                for w in (c, *c.winfo_children()):
                    w.configure(background="#cfe6ff" if on else "#fafafa")
            what.set(f"→ {e['nom']}  ({kind_of(e).lower()})")

        if not plants:
            ttk.Label(grid_, text="Aucune plante téléchargée pour l'instant.", foreground="#a33").grid(row=0, column=0, sticky="w")
            ttk.Button(grid_, text="🌐 Télécharger de l'herbe / des arbres dans Cosmos (3D Models › Plants)",
                       command=lambda: (open_cosmos(), win.destroy())).grid(row=1, column=0, sticky="w", pady=4)
        for k, e in enumerate(plants[:12]):
            c = tk.Frame(grid_, background="#fafafa", padx=4, pady=4, cursor="hand2")
            c.grid(row=k // 6, column=k % 6)
            tk.Label(c, image=images.get(e["dossier"]) or blank, background="#fafafa").pack()
            tk.Label(c, text=e["nom"], wraplength=96, font=("Segoe UI", 8), background="#fafafa").pack()
            cells_[e["dossier"]] = c
            for w in (c, *c.winfo_children()):
                w.bind("<Button-1>", lambda _e, en=e: choose(en))
        what = tk.StringVar()

        ttk.Label(f, text="2.  Où la mettre ?", font=big).grid(row=2, column=0, sticky="w")
        where = tk.StringVar()
        wf = ttk.Frame(f)
        wf.grid(row=3, column=0, sticky="w", pady=(4, 10))
        names, _ = project_surfaces(state, key)
        grass = sorted(n for n in names if n != SOL_AUTOUR and any(w in n.lower() for w in ("grass", "gazon", "herbe", "pelouse", "lawn")))
        ttk.Radiobutton(wf, text="Sur les dalles que j'ai sélectionnées dans Archicad  (le plus rapide)", value=DALLES_SEL,
                        variable=where).grid(row=0, column=0, sticky="w")
        r = 1
        for n in grass:
            ttk.Radiobutton(wf, text=f"Sur la pelouse du projet  « {n} »", value=n, variable=where).grid(row=r, column=0, sticky="w")
            r += 1
        ttk.Radiobutton(wf, text="Partout autour de la maison (15 m), sauf sous le bâtiment", value=SOL_AUTOUR,
                        variable=where).grid(row=r, column=0, sticky="w")
        ttk.Radiobutton(wf, text="Dans une zone que je dessine dans le plan (je clique les coins)", value=ZONE_PLAN,
                        variable=where).grid(row=r + 1, column=0, sticky="w")
        other = tk.StringVar()
        of = ttk.Frame(wf)
        of.grid(row=r + 2, column=0, sticky="w")
        ttk.Radiobutton(of, text="Sur une autre surface :", value="__autre__", variable=where).grid(row=0, column=0)
        ttk.Combobox(of, textvariable=other, values=sorted(n for n in names if n not in grass and n != SOL_AUTOUR),
                     state="readonly", width=26).grid(row=0, column=1, padx=4)
        where.set(DALLES_SEL)

        ttk.Label(f, text="3.  Combien ?", font=big).grid(row=4, column=0, sticky="w")
        qty = tk.IntVar(value=1)
        qf = ttk.Frame(f)
        qf.grid(row=5, column=0, sticky="w", pady=(4, 10))
        for k, lab in enumerate(("Clairsemé", "Normal", "Dense")):
            ttk.Radiobutton(qf, text=lab, value=k, variable=qty).grid(row=0, column=k, padx=(0, 16))

        ttk.Label(f, textvariable=what, foreground="#2b5d8a").grid(row=6, column=0, sticky="w")

        def add():
            e = pick["e"]
            if not e:
                return
            target = other.get() if where.get() == "__autre__" else where.get()
            if not target:
                return
            kind = kind_of(e)
            _d, _u, var, gap, light = SCATTER_TYPES[kind]
            item = {"nom": e["nom"], "dossier": e["dossier"], "surface": target, "densite": QUANTITES[kind][qty.get()],
                    "variation": var, "echelle": 1.0, "rayon": 15.0, "espacement": gap, "leger": light, "type": kind}
            if target == DALLES_SEL:
                try:
                    zones = selected_slab_zones()
                except Exception as error:  # noqa: BLE001
                    what.set(f"Lecture de la sélection impossible : {error}")
                    return
                if not zones:
                    what.set("⚠ Rien de sélectionné : dans Archicad, sélectionne la ou les dalles de gazon, puis « Ajouter ».")
                    return
                item.update(zones=zones, surface=f"{len(zones)} dalle(s) sélectionnée(s)")
            if target == ZONE_PLAN:
                win.withdraw()
                pts, z = draw_zone()
                if len(pts) < 3:
                    win.deiconify()
                    what.set("Zone annulée : clique au moins 3 coins dans le plan, puis Échap.")
                    return
                item.update(zone=pts, z=z)
            win.destroy()
            add_scatter(item)
            lieu = {SOL_AUTOUR: "autour de la maison", ZONE_PLAN: "dans ta zone",
                    DALLES_SEL: f"sur {item['surface']}"}.get(target, f"sur « {target} »")
            msg.set(f"✔ C'est prêt : « {e['nom']} » ({('clairsemé', 'normal', 'dense')[qty.get()]}) {lieu}. "
                    f"{'Les touffes sont dessinées dans le plan (petits ronds). ' if item.get('traces') else ''}"
                    "Lance un rendu (onglet Rendu, Brouillon) pour la voir.")

        bar = ttk.Frame(f)
        bar.grid(row=7, column=0, sticky="e", pady=(8, 0))
        ttk.Button(bar, text="Annuler", command=win.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(bar, text="✔  Ajouter", command=add).grid(row=0, column=1, ipadx=10, ipady=3)
        if pick["e"]:
            choose(pick["e"])

    def on_import(name):
        root.after(0, lambda: (load_library(name), msg.set(f"Nouvel objet reçu de Cosmos : « {name} ».")))

    def open_cosmos():
        hooks = state.get("_cosmos") or {}
        if hooks.get("open"):
            hooks["open"]()
        else:
            import webbrowser
            webbrowser.open(cosmos_url())
        msg.set("Cosmos s'ouvre : télécharge un objet (3D Models), il arrive ici automatiquement.")

    state.setdefault("_cosmos", {}).setdefault("listeners", []).append(on_import)
    loaded = {"v": False}

    def on_tab(_e=None):
        if str(nb.select()) == str(tab) and not loaded["v"]:
            loaded["v"] = True
            resync(quiet=True)
            refresh_tree()
            load_library()
            load_surface_names()
            hooks = state.get("_cosmos") or {}
            if hooks.get("connect"):
                hooks["connect"]()

    nb.bind("<<NotebookTabChanged>>", on_tab, add="+")


# ---------------------------------------------------------- onglet Materiaux ---
def project_surfaces(state: dict, key: str) -> tuple[dict, bool]:
    """Surfaces du projet -> couleur. (vrai, si elles viennent du dernier rendu = seulement celles utilisees)"""
    meta = data_dir() / key / "geometrie.json"
    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
        if info.get("couleurs"):
            out = {n: tuple(c) for n, c in info["couleurs"].items()}
            out[SOL_AUTOUR] = (0.33, 0.47, 0.22)
            return out, True
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
        for listener in (state.get("_cosmos") or {}).get("listeners", []):
            try:
                listener(name)
            except Exception:  # noqa: BLE001
                pass

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

    nb.bind("<<NotebookTabChanged>>", on_tab, add="+")
    state.setdefault("_cosmos", {}).update(connect=connect, open=open_cosmos)


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
