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

VERSION = "20/10 - v4.8b (intérieurs + herbe)"
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
TYPES_CAMERA = {"Standard": 0, "Panorama 360° sphérique (VR)": 9, "Cubemap 6×1 (VR)": 10}
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
FOCALES = (12, 14, 16, 18, 20, 24, 28, 35, 50)

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


def write_materials(path: Path, mats: dict, library: dict, assigned: dict, report=print, custom: dict | None = None) -> list[str]:
    """Un materiau par surface : materiau Cosmos s'il est associe, sinon couleur Archicad + reglages devines.
    Ecrit aussi materiaux_sans_scan.vrscene (materiaux scannes .vrscan remplaces) : secours si V-Ray plante."""
    used = []
    plain = path.with_name("materiaux_sans_scan.vrscene")
    with path.open("w", encoding="utf-8") as out, plain.open("w", encoding="utf-8") as out2:
        out.write("// Materiaux (V-Ray Link)\n")
        out2.write("// Materiaux sans scans (secours V-Ray Link)\n")
        for name, info in mats.items():
            mid = mat_id(name)
            if (custom or {}).get(name):                      # materiau regle a la main : prioritaire
                block = f"// {name} (matériau personnalisé)\n" + custom_material_block(mid, custom[name])
                out.write(block)
                out2.write(block)
                used.append(f"{name} → réglé à la main")
                continue
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
LIVE_MAT: dict = {"rev": 0, "surfaces": [], "c": {}, "tous": {}}     # reglages en cours (editeur -> visionneuse)


def viewer_mesh(key: str) -> bytes:
    """Modele en binaire pour la visionneuse : nv, ni, positions f32, couleurs u8 (rgba), indices u32 (cache)."""
    d = data_dir() / key
    cache, out = d / "geometrie.pkl", d / "visionneuse.bin"
    if out.exists() and out.stat().st_mtime >= cache.stat().st_mtime and (d / "visionneuse_groupes.json").exists():
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
    groups, start = [], 0
    for name, (_v, faces) in meshes.items():
        if faces:
            groups.append([name, start, len(faces)])
            start += len(faces)
    (d / "visionneuse_groupes.json").write_text(json.dumps(groups), encoding="utf-8")
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
                        raw = viewer_mesh(key)
                        try:
                            groups = (data_dir() / key / "visionneuse_groupes.json").read_text(encoding="utf-8")
                        except OSError:
                            groups = "[]"
                        body = b'{"groupes":' + groups.encode("utf-8") + b',"d":"' + base64.b64encode(raw) + b'"}'
                        self._send(body, "application/json")
                    elif self.path.startswith("/api/mat"):
                        self._send(json.dumps(LIVE_MAT).encode("utf-8"), "application/json")
                    elif self.path.startswith("/api/tex"):
                        import base64
                        from urllib.parse import parse_qs, urlparse
                        f_ = parse_qs(urlparse(self.path).query).get("f", [""])[0]
                        ok_ = f_ and Path(f_).suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp") and Path(f_).exists()
                        body = b'{"d":"' + (base64.b64encode(Path(f_).read_bytes()) if ok_ else b"") + b'"}'
                        self._send(body, "application/json")
                    elif self.path.startswith("/editeur"):
                        self._send(EDITOR_HTML.encode("utf-8"), "text/html; charset=utf-8")
                    elif self.path.startswith("/api/surfaces"):
                        self._send(json.dumps(owner.surfaces()).encode("utf-8"), "application/json")
                    elif self.path.startswith("/api/choix"):
                        from urllib.parse import parse_qs, urlparse
                        id_ = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                        res = {"fini": id_ in CHOIX, "path": CHOIX.get(id_, "")}
                        self._send(json.dumps(res).encode("utf-8"), "application/json")
                    elif self.path.startswith("/api/cosmos_tex"):
                        from urllib.parse import parse_qs, urlparse
                        nom = parse_qs(urlparse(self.path).query).get("s", [""])[0]
                        self._send(json.dumps(owner.cosmos_tex(nom)).encode("utf-8"), "application/json")
                    elif self.path.startswith("/api/apercu_img"):
                        import base64
                        f_ = APERCU.get("fichier", "")
                        body = b'{"d":"' + (base64.b64encode(Path(f_).read_bytes()) if f_ and Path(f_).exists() else b"") + b'"}'
                        self._send(body, "application/json")
                    elif self.path.startswith("/api/objets"):
                        self._send(json.dumps(OBJS).encode("utf-8"), "application/json")
                    elif self.path.startswith("/api/lumieres"):
                        self._send(json.dumps(LIGHTS).encode("utf-8"), "application/json")
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
                    if msg.get("action") == "live":           # apercu editeur -> visionneuse, sans passer par Tk
                        LIVE_MAT.update(surfaces=list(msg.get("surfaces") or []), c=msg.get("c") or {}, rev=LIVE_MAT["rev"] + 1)
                    else:
                        owner.on_camera(msg)
                    self._send(b'{"ok":true}', "application/json")
                except Exception as error:  # noqa: BLE001
                    self.send_error(400, str(error))

        self.on_camera = on_camera
        self.etat = lambda: {}
        self.surfaces = lambda: []
        self.cosmos_tex = lambda nom: {}

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

    def open(self, page: str = "") -> None:
        url = f"http://127.0.0.1:{self.port}/{page}"
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
#app.ferme{grid-template-columns:1fr 0}#app.ferme aside{display:none}
#toggle{position:absolute;top:10px;right:10px;z-index:5;background:rgba(32,35,42,.85);color:var(--txt);border:1px solid var(--line);
 border-radius:7px;padding:6px 10px;cursor:pointer;font:13px "Segoe UI",sans-serif}
#toggle:hover{border-color:#4a505c}
.mini{width:34px;height:30px;padding:0;background:rgba(32,35,42,.85);font-size:14px}.mini.on{background:var(--acc);border-color:var(--acc)}
.aides{display:flex;flex-wrap:wrap;gap:4px 12px}.aides label{display:flex;gap:5px;align-items:center;color:var(--txt);font-size:12px;margin:0}
.aides input{width:auto}
#guides{position:absolute;inset:0;pointer-events:none}
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
<div id="hud"></div><div id="updown" style="position:absolute;left:12px;top:12px;display:flex;flex-direction:column;gap:4px;z-index:5">
<button id="bUp" class="mini" title="Monter (E / Espace)">▲</button><button id="bDown" class="mini" title="Descendre (C)">▼</button>
<button id="bMes" class="mini" title="Mesurer une distance (M)">📏</button>
<button id="bLum" class="mini" title="Ajouter une lumière (L)">💡</button>
<button id="bMove" class="mini" title="Déplacer / tourner un objet ou une lumière (G)">✋</button></div>
<div id="editBar" style="position:absolute;left:50%;transform:translateX(-50%);bottom:14px;background:rgba(32,35,42,.95);border:1px solid var(--line);border-radius:8px;padding:6px;display:none;gap:6px;z-index:6;align-items:center">
<span id="selName" style="padding:0 6px;color:var(--mute)">Glisse un objet à la souris (Ctrl + glisser = tourner)</span>
<button id="eMove" class="on">↔ Déplacer</button><button id="eRot">⟳ Tourner</button><button id="eDrop">⤓ Poser</button>
<button id="eDup">⧉ Dupliquer</button><button id="eDel">🗑</button><button id="eEnd">Terminé</button></div>
<div id="lumMenu" style="position:absolute;left:56px;top:110px;background:rgba(32,35,42,.95);border:1px solid var(--line);border-radius:7px;padding:6px;display:none;z-index:6;flex-direction:column;gap:4px">
<b style="font-size:12px;color:var(--mute);padding:2px 4px">Ajouter une lumière :</b></div>
<div id="mesure" style="position:absolute;left:56px;top:12px;background:rgba(32,35,42,.9);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font-size:12px;display:none;z-index:5"></div><div id="tilt" style="position:absolute;right:12px;bottom:10px;color:var(--mute);font-size:12px"></div>
<div id="cadre" style="position:absolute;left:50%;transform:translateX(-50%);top:10px;color:var(--mute);font-size:12px;background:rgba(0,0,0,.35);padding:3px 10px;border-radius:10px"></div>
<button id="bApercu" style="position:absolute;right:120px;top:10px;z-index:5;background:var(--acc);border-color:var(--acc);color:#fff;padding:6px 12px">⚡ Aperçu V-Ray</button>
<div id="apercu" style="position:absolute;inset:0;display:none;align-items:center;justify-content:center;z-index:7;background:rgba(10,11,13,.6)">
<div style="position:relative"><img id="apImg" style="display:block;max-width:100%;max-height:100%;box-shadow:0 0 0 1px #555"><div id="apMsg" style="color:#fff;font-size:15px;padding:20px"></div>
<button id="apClose" style="position:absolute;right:8px;top:8px">✕ Revenir à la 3D</button>
<div id="apMix" style="position:absolute;left:8px;top:8px;background:rgba(20,22,26,.85);padding:5px 9px;border-radius:6px;font-size:12px;display:none">
Comparer avec la 3D : <input id="apOp" type="range" min="0" max="1" step="0.01" value="1" style="width:130px;vertical-align:middle"></div></div></div><button id="toggle" title="Masquer / afficher les réglages (touche P)">Réglages ▶</button></div>
<aside>
<h1>V-Ray Link · <b>Visionneuse 3D</b></h1>
<div><label>Aller à une vue</label><select id="cams"></select></div>
<div><label>Type de vue</label><div class="seg"><button id="vPersp">Perspective libre</button><button id="v2pf">2 points de fuite</button><button id="vOrtho">Orthogonale</button></div>
<div id="orthoBox" style="margin-top:6px"><label>Largeur visible : <span id="owTxt"></span></label>
<input id="ow" type="range" min="2" max="120" step="0.5" value="20" style="padding:0">
<div style="color:var(--mute);font-size:12px">Sans perspective (axonométrie, élévation, plan) : place-toi face à la façade ou au-dessus, puis règle la largeur.</div></div>
<div id="shiftBox" style="margin-top:6px"><label>Décentrement vertical : <span id="shiftTxt"></span></label>
<input id="shift" type="range" min="-0.6" max="0.6" step="0.01" value="0" style="padding:0">
<div style="color:var(--mute);font-size:12px">Monte / descend le cadre sans pencher la caméra (verticales toujours droites).</div></div></div>
<div><label>Vue intérieure</label>
<div class="row"><button id="hAssis" title="Hauteur d'œil assis">Assis 1,2 m</button><button id="hDebout" title="Hauteur d'œil debout">Debout 1,6 m</button></div>
<label style="margin-top:6px">Traverser les murs (plan de coupe) : <span id="coupeTxt"></span></label>
<input id="coupe" type="range" min="0" max="6" step="0.05" value="0" style="padding:0">
<label style="margin-top:6px">Masquer au-dessus de : <span id="cutTxt"></span></label>
<input id="cut" type="range" min="0" max="30" step="0.1" value="30" style="padding:0">
<label style="margin-top:6px">Exposition de la vue : <span id="evTxt"></span></label>
<input id="ev" type="range" min="-3" max="5" step="0.25" value="0" style="padding:0">
<div style="color:var(--mute);font-size:12px">Plan de coupe : ce qui est à moins de X m de la caméra est ignoré (rendu compris) ⇒ on peut se placer « dans » un mur pour cadrer une petite pièce. « Masquer au-dessus » sert seulement à naviguer.</div></div>
<div><label>Aides au cadrage</label><div class="aides">
<label><input type="checkbox" id="gTiers" checked> Tiers</label><label><input type="checkbox" id="gHorizon" checked> Horizon</label>
<label><input type="checkbox" id="gCroix"> Centre</label><label><input type="checkbox" id="gDiag"> Diagonales</label>
<label><input type="checkbox" id="gMarges"> Marges</label></div></div>
<div><label>Navigation</label><div class="seg"><button id="mOrbit" class="on">Tourner autour</button><button id="mWalk">Marcher</button></div></div>
<div class="row"><div><label>Champ de vision (°)</label><input id="fovDeg" type="number" min="5" max="140" step="1" value="35"></div><div><label>Focale (mm)</label><input id="foc" type="number" min="4" max="300" step="1" value="24" list="focList"><datalist id="focList"></datalist></div></div>
<input id="fovR" type="range" min="5" max="140" step="1" value="35" title="Champ de vision vertical, comme l'outil Zoom de SketchUp">
<div class="row">
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
<b style="color:var(--txt)">Marcher</b> : clic gauche glissé = regarder, <kbd>Z</kbd><kbd>Q</kbd><kbd>S</kbd><kbd>D</kbd> ou flèches = avancer / côtés, <kbd>E</kbd>/<kbd>Espace</kbd> = monter, <kbd>C</kbd> = descendre (aussi Page ↑ / Page ↓ ou les boutons ▲ ▼ sur l'image), <kbd>Maj</kbd> = plus vite.<br>
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
let aspect=info.largeur/info.hauteur;
const frame=$("frame"), stage=$("stage");
const renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.shadowMap.enabled=true; renderer.shadowMap.type=THREE.PCFSoftShadowMap; renderer.localClippingEnabled=true;
frame.appendChild(renderer.domElement);
const scene=new THREE.Scene(); scene.background=new THREE.Color(0xbcd3e6);
scene.fog=new THREE.Fog(0xbcd3e6,400,2000);
const cam=new THREE.PerspectiveCamera(50,aspect,0.05,5000); cam.up.set(0,0,1);
const hemi=new THREE.HemisphereLight(0xffffff,0x8a8f7a,0.75); scene.add(hemi);
const sun=new THREE.DirectionalLight(0xfff4e0,2.2); sun.castShadow=true; sun.shadow.mapSize.set(4096,4096);
sun.shadow.bias=-0.0004; sun.shadow.normalBias=0.03; scene.add(sun); scene.add(sun.target);
const ground=new THREE.Mesh(new THREE.PlaneGeometry(4000,4000),new THREE.MeshLambertMaterial({color:0x7d9a5c}));
ground.position.z=info.sol-0.01; ground.receiveShadow=true; scene.add(ground);
const controls=new OrbitControls(cam,renderer.domElement); controls.enableDamping=true; controls.dampingFactor=0.12; window.__ctl=true;
const SVGNS="http://www.w3.org/2000/svg"; const guides=document.createElementNS(SVGNS,"svg"); guides.id="guides"; frame.appendChild(guides);
let vmode="persp";
function applyShift(){const w=renderer.domElement.width, h=renderer.domElement.height, s=vmode==="2pf"?+$("shift").value:0;
 if(s) cam.setViewOffset(w,h,0,-s*h,w,h); else cam.clearViewOffset(); cam.updateProjectionMatrix();
 $("shiftTxt").textContent=(s>0?"+":"")+Math.round(s*100)+" %"}
function drawGuides(){const w=renderer.domElement.clientWidth, h=renderer.domElement.clientHeight; guides.setAttribute("viewBox",`0 0 ${w} ${h}`);
 let g=""; const L=(x1,y1,x2,y2,c,d)=>g+=`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c}" stroke-width="1" ${d?'stroke-dasharray="6 5"':""}/>`;
 if($("gTiers").checked){for(const k of [1/3,2/3]){L(w*k,0,w*k,h,"rgba(255,255,255,.55)");L(0,h*k,w,h*k,"rgba(255,255,255,.55)")}}
 if($("gDiag").checked){L(0,0,w,h,"rgba(255,255,255,.35)");L(w,0,0,h,"rgba(255,255,255,.35)")}
 if($("gCroix").checked){L(w/2-14,h/2,w/2+14,h/2,"#fff");L(w/2,h/2-14,w/2,h/2+14,"#fff")}
 if($("gMarges").checked){g+=`<rect x="${w*.05}" y="${h*.05}" width="${w*.9}" height="${h*.9}" fill="none" stroke="rgba(255,210,120,.7)" stroke-dasharray="4 4"/>`}
 if($("gHorizon").checked){const v=new THREE.Vector3(cam.position.x+Math.cos(yaw0())*1000,cam.position.y+Math.sin(yaw0())*1000,cam.position.z).project(cam);
  const y=(1-v.y)/2*h; if(y>-2&&y<h+2) L(0,y,w,y,"#e0702a",true)}
 guides.innerHTML=g}
function yaw0(){const d=new THREE.Vector3().subVectors(controls.target,cam.position);return Math.atan2(d.y,d.x)}
function fit(){const W=stage.clientWidth-24,H=stage.clientHeight-24;let w=W,h=W/aspect;if(h>H){h=H;w=H*aspect}
 renderer.setSize(Math.floor(w),Math.floor(h)); cam.aspect=aspect; cam.updateProjectionMatrix(); if(window.__ctl)applyShift();}
addEventListener("resize",fit); fit();
function setPanel(open){$("app").classList.toggle("ferme",!open); $("toggle").textContent=open?"Réglages ▶":"◀ Réglages";
 try{localStorage.setItem("vrl_panneau",open?"1":"0")}catch(_){} requestAnimationFrame(fit)}
$("toggle").onclick=()=>setPanel($("app").classList.contains("ferme"));
addEventListener("keydown",e=>{if(e.target.tagName!=="INPUT"&&e.code==="KeyP")setPanel($("app").classList.contains("ferme"))});
try{if(localStorage.getItem("vrl_panneau")==="0")setPanel(false)}catch(_){}
function setFocal(f){f=Math.max(4,Math.min(300,+f||24)); const hf=2*Math.atan(18/f); cam.fov=THREE.MathUtils.radToDeg(2*Math.atan(Math.tan(hf/2)/aspect)); cam.updateProjectionMatrix();
 if(document.activeElement!==$("foc"))$("foc").value=+f.toFixed(1); const v=Math.round(cam.fov); if(document.activeElement!==$("fovDeg"))$("fovDeg").value=v; $("fovR").value=v}
function setFov(v){v=Math.max(5,Math.min(140,+v||35)); const hf=2*Math.atan(Math.tan(THREE.MathUtils.degToRad(v)/2)*aspect); setFocal(18/Math.tan(hf/2))}
for(const f of info.focales){const o=document.createElement("option");o.value=f;o.textContent=f+" mm";$("focList").appendChild(o)}
$("foc").value=24; setFocal(24); $("foc").oninput=()=>setFocal(+$("foc").value); $("fovDeg").oninput=()=>setFov(+$("fovDeg").value); $("fovR").oninput=()=>setFov(+$("fovR").value);
renderer.domElement.addEventListener("wheel",e=>{if(!e.shiftKey)return; e.preventDefault(); e.stopImmediatePropagation(); setFov(cam.fov+(e.deltaY>0?2:-2))},{capture:true,passive:false});
const ortho=new THREE.OrthographicCamera(-10,10,5,-5,-2000,5000); ortho.up.set(0,0,1);
function syncOrtho(){const w=+$("ow").value; ortho.left=-w/2; ortho.right=w/2; ortho.top=w/aspect/2; ortho.bottom=-w/aspect/2;
 ortho.position.copy(cam.position); ortho.quaternion.copy(cam.quaternion); ortho.updateProjectionMatrix(); $("owTxt").textContent=w+" m"}
$("ow").addEventListener("input",syncOrtho);
renderer.domElement.addEventListener("wheel",e=>{if(vmode!=="ortho")return; e.preventDefault(); e.stopImmediatePropagation();
 const w=Math.min(120,Math.max(2,+$("ow").value*(e.deltaY>0?1.1:0.9))); $("ow").value=w; syncOrtho()},{capture:true,passive:false});
function setMode(m){vmode=m; $("vPersp").classList.toggle("on",m==="persp"); $("v2pf").classList.toggle("on",m==="2pf");
 $("vOrtho").classList.toggle("on",m==="ortho"); $("orthoBox").style.display=m==="ortho"?"":"none";
 $("shiftBox").style.display=m==="2pf"?"":"none";
 if(m==="2pf"){controls.minPolarAngle=controls.maxPolarAngle=Math.PI/2; controls.target.z=cam.position.z} else {controls.minPolarAngle=0; controls.maxPolarAngle=Math.PI}
 controls.update(); applyShift()}
$("vPersp").onclick=()=>setMode("persp"); $("v2pf").onclick=()=>setMode("2pf"); $("vOrtho").onclick=()=>setMode("ortho");
function eyeAt(hh){const h=info.sol+hh; const d=new THREE.Vector3().subVectors(controls.target,cam.position); d.z=0; if(d.length()<0.3)d.set(3,0,0);
 cam.position.z=h; controls.target.set(cam.position.x+d.x,cam.position.y+d.y,h); controls.update()}
$("hAssis").onclick=()=>eyeAt(1.2); $("hDebout").onclick=()=>eyeAt(1.6);
function setCoupe(){const c=+$("coupe").value; cam.near=c>0?c:0.05; cam.updateProjectionMatrix(); $("coupeTxt").textContent=c>0?c.toFixed(2)+" m":"non"}
$("coupe").addEventListener("input",setCoupe);
const cutPlane=new THREE.Plane(new THREE.Vector3(0,0,-1),0);
function setCut(){const v=+$("cut").value; cutPlane.constant=info.sol+v;
 for(const m of (window.__mats||[])){m.clippingPlanes=v>=30?null:[cutPlane]; m.needsUpdate=true}
 $("cutTxt").textContent=v>=30?"rien":v.toFixed(1)+" m"}
$("cut").addEventListener("input",setCut);
function setEv(){const e=+$("ev").value; renderer.toneMappingExposure=Math.pow(2,e); $("evTxt").textContent=(e>0?"+":"")+e+" EV"}
$("ev").addEventListener("input",setEv);
renderer.toneMapping=THREE.ACESFilmicToneMapping; setCoupe(); setCut(); setEv();
$("shift").addEventListener("input",applyShift);
function goTo(c){cam.position.set(...c.eye); controls.target.set(...c.target); $("foc").value=c.focale||24; setFocal(+$("foc").value);
 $("shift").value=c.decentrement||0; $("ow").value=c.ortho_largeur||20; setMode(c.mode||"persp");
 $("coupe").value=c.coupe||0; setCoupe(); $("ev").value=c.ev||0; setEv();
 if(!c.auto) $("name").value=c.nom; controls.update();}
info.cameras.forEach((c,i)=>{const o=document.createElement("option");o.value=i;o.textContent=c.nom;$("cams").appendChild(o)});
$("cams").onchange=()=>goTo(info.cameras[+$("cams").value]);
// modele
const r1=await fetch("/api/geometrie"); if(!r1.ok) throw new Error("modèle : "+r1.status+" "+(await r1.text()).slice(0,200));
const geoJ=await r1.json(); const b64=geoJ.d; const GROUPES=geoJ.groupes||[]; const raw=atob(b64); const u8=new Uint8Array(raw.length);
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
// coordonnees de texture en projection boite (1 unite = 1 m), comme pour V-Ray
{const P=g.attributes.position.array, uv=new Float32Array(P.length/3*2);
 for(let t=0;t<P.length/9;t++){const o=t*9;
  const ux=P[o+3]-P[o],uy=P[o+4]-P[o+1],uz=P[o+5]-P[o+2],vx=P[o+6]-P[o],vy=P[o+7]-P[o+1],vz=P[o+8]-P[o+2];
  const nx=Math.abs(uy*vz-uz*vy),ny=Math.abs(uz*vx-ux*vz),nz=Math.abs(ux*vy-uy*vx);
  const ax=(nx>=ny&&nx>=nz)?0:(ny>=nz?1:2);
  for(let k=0;k<3;k++){const x=P[o+3*k],y=P[o+3*k+1],z=P[o+3*k+2];const i=(t*3+k)*2;
   if(ax===0){uv[i]=y;uv[i+1]=z}else if(ax===1){uv[i]=x;uv[i+1]=z}else{uv[i]=x;uv[i+1]=y}}}
 g.setAttribute("uv",new THREE.BufferAttribute(uv,2))}
const C=g.attributes.color.array, MATS=[], PAR={};
for(const [nom,start,count] of GROUPES){const m=new THREE.MeshPhysicalMaterial({color:new THREE.Color(C[start*4]/255,C[start*4+1]/255,C[start*4+2]/255).convertSRGBToLinear(),
  roughness:0.85,metalness:0,side:THREE.DoubleSide}); m.userData.base=m.color.clone();
 PAR[nom]=MATS.length; g.addGroup(start,count,MATS.length); MATS.push(m)}
if(!GROUPES.length) MATS.push(new THREE.MeshLambertMaterial({vertexColors:true,side:THREE.DoubleSide}));
const model=new THREE.Mesh(g,GROUPES.length?MATS:MATS[0]); window.__mats=MATS; setCut();
{const {RoomEnvironment}=await import("three/addons/environments/RoomEnvironment.js");
 const pm=new THREE.PMREMGenerator(renderer); scene.environment=pm.fromScene(new RoomEnvironment(),0.04).texture;
 scene.environmentIntensity=0.9; hemi.intensity=0.45}
// reglages de materiaux (editeur V-Ray Link) appliques en direct
const texCache={};
async function tex(path,size,rot,srgb,du=0,dv=0){if(!path)return null; const k=path+"|"+srgb;
 if(!texCache[k]){texCache[k]=(async()=>{const r=await fetch("/api/tex?f="+encodeURIComponent(path)); const d=(await r.json()).d; if(!d)return null;
   const t=await new THREE.TextureLoader().loadAsync("data:image/*;base64,"+d); t.wrapS=t.wrapT=THREE.RepeatWrapping;
   t.colorSpace=srgb?THREE.SRGBColorSpace:THREE.NoColorSpace; t.anisotropy=8; return t})()}
 const t0=await texCache[k]; if(!t0)return null; const t=t0.clone(); t.needsUpdate=true;
 const s=1/Math.max(0.01,size||1); t.repeat.set(s,s); t.rotation=(rot||0)*Math.PI/180; t.offset.set(-du*s,-dv*s); return t}
async function applyMat(nom,c){const i=PAR[nom]; if(i===undefined)return; const m=MATS[i];
 if(!c){m.color.copy(m.userData.base);m.map=m.roughnessMap=m.alphaMap=m.bumpMap=m.normalMap=null;m.roughness=.85;m.metalness=0;
  m.opacity=1;m.transparent=false;m.transmission=0;if(!m.userData.lumMesh)m.emissive.set(0);m.needsUpdate=true;return}
 const sz=c.taille_tex||1, rot=c.rotation_tex||0;
 if(c.couleur)m.color.setRGB(c.couleur[0],c.couleur[1],c.couleur[2],THREE.SRGBColorSpace);
 const du=c.decalage_u||0, dv=c.decalage_v||0;
 m.map=await tex(c.tex_couleur,sz,rot,true,du,dv); if(m.map){const k=1+(c.luminosite||0); m.color.setRGB(k,k,k)}
 m.roughness=1-(c.brillance??0.5)*(c.reflet>0.02?1:0.3); m.metalness=c.metal||0; m.ior=c.ior||1.5;
 m.specularIntensity=Math.min(1,(c.reflet??0.1)*2);
 m.roughnessMap=await tex(c.tex_brillance,sz,rot,false);
 const op=c.opacite??1; m.alphaMap=await tex(c.tex_opacite,sz,rot,false); m.transparent=op<1||!!m.alphaMap; m.opacity=op;
 m.transmission=c.refraction||0; m.thickness=0.02; if(m.transmission>0)m.roughness=Math.max(m.roughness,(c.flou_refraction||0)*0.6);
 const rel=await tex(c.tex_relief,sz,rot,false); const normal=c.type_relief==="Normal map";
 m.normalMap=normal?rel:null; m.bumpMap=normal?null:rel; m.bumpScale=(c.relief||0.5)*0.05; if(normal)m.normalScale.set(c.relief||0.5,c.relief||0.5);
 const em=c.emission||0; if(!m.userData.lumMesh){m.emissive.setRGB(1,0.9,0.7); m.emissiveIntensity=em*0.4; if(!em)m.emissive.set(0)}
 m.needsUpdate=true}
let matRev=-1;
async function pollMat(){try{const L=await (await fetch("/api/mat")).json();
  if(L.rev!==matRev){matRev=L.rev; for(const [n,c] of Object.entries(L.tous||{}))await applyMat(n,c);
   for(const n of (L.surfaces||[]))await applyMat(n,L.c&&Object.keys(L.c).length?L.c:null)}}catch(_){}
 setTimeout(pollMat,400)}
for(const t of (info.arbres||[])){const k=t[3]||1;const trunk=new THREE.Mesh(new THREE.CylinderGeometry(.15*k,.2*k,2.5*k,8),new THREE.MeshLambertMaterial({color:0x6b4f35}));
 trunk.rotation.x=Math.PI/2;trunk.position.set(t[0],t[1],t[2]+1.25*k);trunk.castShadow=true;scene.add(trunk);
 const crown=new THREE.Mesh(new THREE.IcosahedronGeometry(2.2*k,1),new THREE.MeshLambertMaterial({color:0x4f7a3a,flatShading:true}));
 crown.position.set(t[0],t[1],t[2]+4.2*k);crown.castShadow=true;crown.receiveShadow=true;scene.add(crown)}
model.castShadow=true; model.receiveShadow=true; scene.add(model); pollMat();
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
 sun.intensity=s.alt>0?2.2*k*Math.min(1,s.alt*4+0.15):0; hemi.intensity=s.alt>0?0.45:0.2;
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
const objGroup=new THREE.Group(); scene.add(objGroup); let OBJ=[], objRev=-1;
function drawObjs(){objGroup.clear(); for(const ob of OBJ){const k=ob.echelle||1, t=(ob.taille&&ob.taille.length)?ob.taille:[0.8,0.8,1.0,0];
 const g=new THREE.BoxGeometry(t[0]*k,t[1]*k,t[2]*k); g.translate(0,0,(t[3]+t[2]/2)*k);           // origine = pied de l'objet
 const m=new THREE.Mesh(g,new THREE.MeshLambertMaterial({color:0xe0702a,transparent:true,opacity:.6}));
 m.position.set(ob.x,ob.y,ob.z); m.rotation.z=(ob.rot||0)*Math.PI/180; m.castShadow=true; m.userData={kind:"obj",i:ob.i,nom:ob.nom}; objGroup.add(m);
 if(tc&&sel&&sel.userData.kind==="obj"&&sel.userData.i===ob.i){sel=m; tc.attach(m)}}}
async function pollObjs(){try{const j=await (await fetch("/api/objets")).json(); if(j.rev!==objRev&&!tcDrag){objRev=j.rev; OBJ=j.liste||[]; drawObjs()}}catch(_){}
 setTimeout(pollObjs,900)}
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
  if(Math.abs(e.intensite-(+$("sunInt").value))>0.01){$("sunInt").value=e.intensite; updateSun()}
  if(e.taille){const a=e.taille[0]/e.taille[1]; if(Math.abs(a-aspect)>0.001){aspect=a; fit(); setFocal(+$("foc").value)}
   $("cadre").textContent=`Cadre du rendu : ${e.taille[0]} × ${e.taille[1]} px · ${e.qualite}${e.type_camera&&e.type_camera!=="Standard"?" · "+e.type_camera:""}`}
  if(e.apercu&&e.apercu.rev!==apRev){apRev=e.apercu.rev; showApercu(e.apercu)}}catch(_){}
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
addEventListener("keydown",e=>{if(e.target.tagName==="INPUT"||e.target.tagName==="SELECT")return;keys[e.code]=true;
 if(["Space","PageUp","PageDown"].includes(e.code))e.preventDefault()});addEventListener("keyup",e=>keys[e.code]=false);
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
let btnUp=false, btnDown=false;
for(const [id,set] of [["bUp",v=>btnUp=v],["bDown",v=>btnDown=v]]){const b=$(id);
 b.addEventListener("pointerdown",()=>set(true)); for(const ev of ["pointerup","pointerleave"])b.addEventListener(ev,()=>set(false))}
// ---- outil Mesurer : deux clics sur le modele -> distance, horizontale, hauteur
let mesureOn=false, mPts=[]; const mGroup=new THREE.Group(); scene.add(mGroup); const ray=new THREE.Raycaster();
function setMesure(on){mesureOn=on; $("bMes").classList.toggle("on",on); $("mesure").style.display=on?"":"none";
 mPts=[]; mGroup.clear(); $("mesure").textContent=on?"Mesure : clique le 1er point sur le modèle (Échap pour quitter)":""; renderer.domElement.style.cursor=on?"crosshair":""}
$("bMes").onclick=()=>setMesure(!mesureOn);
addEventListener("keydown",e=>{if(e.target.tagName==="INPUT")return; if(e.code==="KeyM")setMesure(!mesureOn); if(e.code==="Escape"&&mesureOn)setMesure(false)});
let downAt=null; renderer.domElement.addEventListener("pointerdown",e=>downAt=[e.clientX,e.clientY]);
renderer.domElement.addEventListener("pointerup",e=>{if(!mesureOn||!downAt||Math.hypot(e.clientX-downAt[0],e.clientY-downAt[1])>4)return;
 const r=renderer.domElement.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),vmode==="ortho"?ortho:cam);
 const hit=ray.intersectObjects([model,ground],false)[0]; if(!hit)return; const p=hit.point.clone();
 if(mPts.length===2){mPts=[];mGroup.clear()}
 mPts.push(p); const dot=new THREE.Mesh(new THREE.SphereGeometry(0.06,12,8),new THREE.MeshBasicMaterial({color:0xe0702a,depthTest:false})); dot.position.copy(p); dot.renderOrder=9; mGroup.add(dot);
 if(mPts.length===2){const [a,b]=mPts; const ln=new THREE.Line(new THREE.BufferGeometry().setFromPoints([a,b]),new THREE.LineBasicMaterial({color:0xe0702a,depthTest:false})); ln.renderOrder=9; mGroup.add(ln);
  const d=a.distanceTo(b), h=Math.hypot(b.x-a.x,b.y-a.y), z=b.z-a.z;
  $("mesure").innerHTML=`<b style="color:var(--acc)">${d.toFixed(2)} m</b> &nbsp;· horizontal ${h.toFixed(2)} m · hauteur ${z>=0?"+":""}${z.toFixed(2)} m<br><span style="color:var(--mute)">Clique un nouveau point pour une autre mesure</span>`}
 else $("mesure").textContent="Mesure : clique le 2e point"});
// ---- lumieres : affichage + pose en cliquant sur une surface
let sel=null, tc=null, tcDrag=false;
const lumGroup=new THREE.Group(); scene.add(lumGroup); let LUM=[], lumRev=-1, poseType=null;
const LTYPES={"Spot":{intensite:1000,cone:45,penombre:25,rayon_ombre:.02},"IES":{intensite:1,ies:""},"Rectangle":{intensite:30,largeur:1,hauteur:.5,invisible:true},
 "Sphère":{intensite:30,rayon:.08,invisible:false},"Omni":{intensite:1000,rayon_ombre:.05},"Dôme":{intensite:1},
 "Maillage":{intensite:10,surface:"",invisible:false}};
for(const t of Object.keys(LTYPES)){const b=document.createElement("button"); b.textContent=t; b.onclick=()=>{poseType=t; $("lumMenu").style.display="none";
  $("mesure").style.display=""; $("mesure").textContent=t==="Dôme"?"Dôme ajouté (lumière d'ambiance tout autour)":"Clique sur une surface (plafond, mur…) pour poser : "+t+"  — Échap pour annuler";
  renderer.domElement.style.cursor="crosshair"; if(t==="Dôme"){addLight(new THREE.Vector3(0,0,info.sol+5),new THREE.Vector3(0,0,-1)); poseType=null}}; $("lumMenu").appendChild(b)}
$("bLum").onclick=()=>{const m=$("lumMenu"); m.style.display=m.style.display==="flex"?"none":"flex"};
addEventListener("keydown",e=>{if(e.target.tagName==="INPUT")return; if(e.code==="KeyL")$("bLum").click();
 if(e.code==="Escape"&&poseType){poseType=null;$("mesure").style.display="none";renderer.domElement.style.cursor=""}});
function addLight(p,n,surf){const t=poseType||"Dôme", off=t==="Spot"||t==="IES"?0.12:t==="Rectangle"?0.02:0.1;
 const pos=p.clone().addScaledVector(n,off), tgt=pos.clone().add(n);
 const l=Object.assign({type:t,nom:t+" "+(LUM.filter(x=>x.type===t).length+1),pos:pos.toArray().map(v=>+v.toFixed(3)),cible:tgt.toArray().map(v=>+v.toFixed(3)),
  couleur:[1,.92,.8],actif:true},JSON.parse(JSON.stringify(LTYPES[t])));
 if(t==="Maillage"){l.surface=surf||""; l.nom="Surface lumineuse "+(surf||"")}
 LUM.push(l); drawLights(); fetch("/camera",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"lum_save",liste:LUM})});
 $("mesure").textContent=`✔ ${l.nom} posée. Règle-la dans l'Éditeur V-Ray › Lumières (couleur, intensité, angle…).`; setTimeout(()=>{if(!mesureOn)$("mesure").style.display="none"},4000)}
renderer.domElement.addEventListener("pointerup",e=>{if(!poseType||!downAt||Math.hypot(e.clientX-downAt[0],e.clientY-downAt[1])>4)return;
 const r=renderer.domElement.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),vmode==="ortho"?ortho:cam);
 const hit=ray.intersectObjects([model,ground],false)[0]; if(!hit)return; const n=(hit.face?hit.face.normal.clone():new THREE.Vector3(0,0,1));
 if(n.dot(new THREE.Vector3().subVectors((vmode==="ortho"?ortho:cam).position,hit.point))<0)n.negate();
 let surf=""; if(hit.object===model&&hit.face){const gi=g.groups.find(G=>hit.faceIndex*3>=G.start&&hit.faceIndex*3<G.start+G.count); if(gi)surf=GROUPES[gi.materialIndex][0]}
 if(poseType==="Maillage"&&!surf){$("mesure").textContent="Clique sur une surface du bâtiment (pas le sol V-Ray)"; return}
 addLight(hit.point,n.normalize(),surf); poseType=null; renderer.domElement.style.cursor=""});
function drawLights(){lumGroup.clear();
 for(const m of (window.__mats||[])){if(m.userData.lumMesh){m.emissive.set(0);m.emissiveIntensity=0;m.userData.lumMesh=false;m.needsUpdate=true}}
 for(const l of LUM){if(l.type!=="Maillage"||l.actif===false)continue; const i=PAR[l.surface]; if(i===undefined)continue; const m=MATS[i];
  m.emissive.setRGB(...(l.couleur||[1,.92,.8]),THREE.SRGBColorSpace); m.emissiveIntensity=Math.min(4,l.intensite*0.15); m.userData.lumMesh=true; m.needsUpdate=true}
 for(const l of LUM){if(l.actif===false)continue; const c=new THREE.Color().setRGB(...(l.couleur||[1,.92,.8]),THREE.SRGBColorSpace);
  const p=new THREE.Vector3(...l.pos), tg=new THREE.Vector3(...(l.cible||[l.pos[0],l.pos[1],l.pos[2]-1])); let L, giz;
  const gm=new THREE.MeshBasicMaterial({color:c}); 
  if(l.type==="Spot"||l.type==="IES"){L=new THREE.SpotLight(c,(l.type==="IES"?800*l.intensite:l.intensite)*0.03,0,(l.cone||60)*Math.PI/360,Math.min(1,(l.penombre||25)/(l.cone||45)),2);
   L.target.position.copy(tg); lumGroup.add(L.target); giz=new THREE.Mesh(new THREE.ConeGeometry(.08,.15,16,1,true),gm); giz.position.copy(p);
   giz.lookAt(tg); giz.rotateX(-Math.PI/2)}
  else if(l.type==="Rectangle"){L=new THREE.PointLight(c,l.intensite*(l.largeur||1)*(l.hauteur||.5)*1.2,0,2);
   giz=new THREE.Mesh(new THREE.PlaneGeometry(l.largeur||1,l.hauteur||.5),new THREE.MeshBasicMaterial({color:c,side:THREE.DoubleSide})); giz.position.copy(p); giz.lookAt(tg)}
  else if(l.type==="Dôme"){L=new THREE.HemisphereLight(c,0x444444,l.intensite*0.8)}
  else if(l.type==="Maillage"){continue}
  else{L=new THREE.PointLight(c,l.type==="Omni"?l.intensite*0.03:l.intensite*0.8,0,2); giz=new THREE.Mesh(new THREE.SphereGeometry(Math.max(.04,l.rayon||.08),16,10),gm); giz.position.copy(p)}
  L.position.copy(p); L.castShadow=false; lumGroup.add(L);
  if(giz){giz.userData={kind:"lum",i:LUM.indexOf(l),nom:l.nom,light:L}; lumGroup.add(giz);
   if(tc&&sel&&sel.userData.kind==="lum"&&sel.userData.i===giz.userData.i){sel=giz; tc.attach(giz)}}}}
async function pollLights(){try{const j=await (await fetch("/api/lumieres")).json(); if(j.rev!==lumRev&&!tcDrag){lumRev=j.rev; LUM=j.liste||[]; drawLights()}}catch(_){}
 setTimeout(pollLights,800)}
pollLights();
// ---- deplacer / tourner les objets Cosmos et les lumieres
const {TransformControls}=await import("three/addons/controls/TransformControls.js");
tc=new TransformControls(cam,renderer.domElement); tc.setSize(0.9); scene.add(tc); let editOn=false;
tc.addEventListener("dragging-changed",e=>{controls.enabled=!e.value&&mode==="orbit"; tcDrag=e.value; if(!e.value)commit()});
function setEdit(on){editOn=on; $("bMove").classList.toggle("on",on); $("editBar").style.display=on?"flex":"none"; if(!on){tc.detach(); sel=null}
 renderer.domElement.style.cursor=on?"pointer":""}
$("bMove").onclick=()=>setEdit(!editOn); $("eEnd").onclick=()=>setEdit(false);
addEventListener("keydown",e=>{if(e.target.tagName==="INPUT")return; if(e.code==="KeyG")setEdit(!editOn); if(e.code==="Escape"&&editOn)setEdit(false);
 if(editOn&&sel&&(e.code==="Delete"||e.code==="Backspace"))$("eDel").click()});
function tool(m){tc.setMode(m); $("eMove").classList.toggle("on",m==="translate"); $("eRot").classList.toggle("on",m==="rotate");
 tc.showX=tc.showY=m==="translate"; tc.showZ=true}
$("eMove").onclick=()=>tool("translate"); $("eRot").onclick=()=>tool("rotate"); tool("translate");
renderer.domElement.addEventListener("pointerup",e=>{if(!editOn||tcDrag||!downAt||Math.hypot(e.clientX-downAt[0],e.clientY-downAt[1])>4)return;
 const r=renderer.domElement.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),cam);
 const hit=ray.intersectObjects([...objGroup.children,...lumGroup.children.filter(c=>c.userData.kind)],false)[0];
 if(hit){sel=hit.object; tc.attach(sel); $("selName").textContent=(sel.userData.kind==="lum"?"💡 ":"📦 ")+sel.userData.nom}
 else{tc.detach(); sel=null; $("selName").textContent="Clique un objet orange ou une lumière"}});
const postJ=b=>fetch("/camera",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});
function commit(op){if(!sel)return; const u=sel.userData, p=sel.position;
 if(u.kind==="obj"){postJ({action:"obj_edit",op:op||"move",i:u.i,x:+p.x.toFixed(3),y:+p.y.toFixed(3),z:+p.z.toFixed(3),rot:+(sel.rotation.z*180/Math.PI).toFixed(1)})}
 else{const l=LUM[u.i]; if(!l)return; const d=[p.x-l.pos[0],p.y-l.pos[1],p.z-l.pos[2]];
  l.pos=[p.x,p.y,p.z].map(v=>+v.toFixed(3)); if(l.cible)l.cible=l.cible.map((v,k)=>+(v+d[k]).toFixed(3));
  if(op==="dup"){const c=JSON.parse(JSON.stringify(l)); c.nom=l.nom+" copie"; c.pos[0]+=0.5; if(c.cible)c.cible[0]+=0.5; LUM.push(c)}
  if(op==="delete"){LUM.splice(u.i,1); tc.detach(); sel=null}
  drawLights(); postJ({action:"lum_save",liste:LUM})}}
$("eDrop").onclick=()=>{if(!sel)return; ray.set(sel.position.clone().add(new THREE.Vector3(0,0,0.01)),new THREE.Vector3(0,0,-1));
 const hit=ray.intersectObjects([model,ground],false)[0]; if(hit){sel.position.z=hit.point.z; commit()}};
$("eDup").onclick=()=>{if(!sel)return; if(sel.userData.kind==="obj"){sel.position.x+=0.6; commit("dup"); sel.position.x-=0.6} else commit("dup")};
$("eDel").onclick=()=>{if(!sel)return; if(sel.userData.kind==="obj"){commit("delete"); tc.detach(); sel=null} else commit("delete");
 $("selName").textContent="Supprimé"};
// glisser directement l'objet a la souris (il suit les surfaces : sol, terrasse, table…) ; Ctrl + glisser = tourner
let dragO=null;
renderer.domElement.addEventListener("pointerdown",e=>{if(!editOn||e.button!==0||(tc&&tc.axis))return;
 const r=renderer.domElement.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),cam);
 const hit=ray.intersectObjects([...objGroup.children,...lumGroup.children.filter(c=>c.userData.kind)],false)[0]; if(!hit)return;
 sel=hit.object; tc.attach(sel); $("selName").textContent=(sel.userData.kind==="lum"?"💡 ":"📦 ")+sel.userData.nom;
 dragO={x:e.clientX,rot0:sel.rotation.z,moved:false,ctrl:e.ctrlKey}; controls.enabled=false; tcDrag=true;
 renderer.domElement.setPointerCapture(e.pointerId); e.stopImmediatePropagation()},{capture:true});
renderer.domElement.addEventListener("pointermove",e=>{if(!dragO||!sel)return; dragO.moved=true;
 if(dragO.ctrl){sel.rotation.z=dragO.rot0+(e.clientX-dragO.x)*0.01; return}
 const r=renderer.domElement.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),cam);
 const hit=ray.intersectObjects([model,ground],false).find(h=>!h.face||Math.abs(h.face.normal.z)>0.6);
 if(hit){sel.position.x=hit.point.x; sel.position.y=hit.point.y; if(sel.userData.kind==="obj")sel.position.z=hit.object===ground?info.sol:hit.point.z}
 else{const pl=new THREE.Plane(new THREE.Vector3(0,0,1),-sel.position.z), pt=new THREE.Vector3(); if(ray.ray.intersectPlane(pl,pt)){sel.position.x=pt.x; sel.position.y=pt.y}}},{capture:true});
renderer.domElement.addEventListener("pointerup",e=>{if(!dragO)return; const moved=dragO.moved; dragO=null; tcDrag=false; controls.enabled=mode==="orbit";
 if(moved)commit()},{capture:true});
pollObjs();
// ---- apercu V-Ray rapide : ce que donnera le rendu, dans la visionneuse
let apRev=-1, apWait=false;
function showApercu(a){const box=$("apercu"); if(!apWait&&a.etat!=="calcul")return;
 box.style.display="flex"; const img=$("apImg"), m=$("apMsg");
 if(a.etat==="calcul"){img.style.display="none"; m.style.display=""; m.textContent="⚡ Calcul de l'aperçu V-Ray (petite image, ~20 à 40 s)…"}
 else if(a.etat==="pret"){fetch("/api/apercu_img").then(r=>r.json()).then(j=>{img.src="data:image/png;base64,"+j.d; img.style.display=""; m.style.display="none";
  const cv=renderer.domElement.getBoundingClientRect(); img.style.width=cv.width+"px"; img.style.height=cv.height+"px";   // meme cadre que la 3D
  $("apMix").style.display=""; $("apOp").value=1; $("apOp").oninput()}); apWait=false}
 else{img.style.display="none"; m.style.display=""; m.textContent="Aperçu impossible — "+a.etat; apWait=false}}
$("apClose").onclick=()=>{$("apercu").style.display="none"};
$("apOp").oninput=()=>{const o=+$("apOp").value; $("apImg").style.opacity=o; $("apercu").style.background=`rgba(10,11,13,${0.6*o})`};
$("bApercu").onclick=()=>{apWait=true; $("apercu").style.display="flex"; $("apImg").style.display="none"; $("apMsg").style.display=""; $("apMsg").textContent="⚡ Préparation…";
 fetch("/camera",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"apercu",eye:cam.position.toArray(),target:controls.target.toArray(),
  focale:+$("foc").value,mode:vmode,decentrement:vmode==="2pf"?+$("shift").value:0,coupe:+$("coupe").value,ev:+$("ev").value,ortho_largeur:+$("ow").value,
  heure:+$("hour").value,jour:+$("day").value,mois:+$("month").value,intensite:+$("sunInt").value,hdri_rot:+$("hdriRot").value})})};
const clock=new THREE.Clock();
function loop(){const dt=Math.min(clock.getDelta(),.05);const v=(keys.ShiftLeft||keys.ShiftRight?12:4)*dt;
 if(mode==="walk"||Object.values(keys).some(Boolean)){
  const f=(keys.KeyW||keys.KeyZ||keys.ArrowUp?1:0)-(keys.KeyS||keys.ArrowDown?1:0);
  const s=(keys.KeyD||keys.ArrowRight?1:0)-(keys.KeyQ||keys.KeyA||keys.ArrowLeft?1:0);
  const u=(keys.KeyE||keys.Space||keys.PageUp||btnUp?1:0)-(keys.KeyC||keys.PageDown||btnDown?1:0);
  if(f||s||u)step(f*v,s*v,u*v);}
 if(vmode==="2pf")controls.target.z=cam.position.z;
 if(mode==="orbit")controls.update(); else cam.lookAt(controls.target);
 drawGuides(); {const d=new THREE.Vector3().subVectors(controls.target,cam.position); const t=Math.asin(THREE.MathUtils.clamp(d.z/d.length(),-1,1))*180/Math.PI;
  $("tilt").textContent=vmode==="2pf"?"verticales droites":`inclinaison ${t>0?"+":""}${t.toFixed(1)}°`}
 const p=cam.position;$("hud").textContent=`Caméra  x ${p.x.toFixed(1)}  y ${p.y.toFixed(1)}  ·  hauteur ${(p.z-info.sol).toFixed(2)} m  ·  ${(+$("foc").value).toFixed(0)} mm · ${Math.round(cam.fov)}°`;
 if(vmode==="ortho"){syncOrtho(); renderer.render(scene,ortho)} else renderer.render(scene,cam);requestAnimationFrame(loop)}
loop();
$("eyeBtn").onclick=()=>{const h=info.sol+(+$("eyeh").value||1.6);const d=new THREE.Vector3().subVectors(controls.target,cam.position);d.z=0;
 if(d.length()<0.5)d.set(5,0,0);cam.position.z=h;controls.target.set(cam.position.x+d.x,cam.position.y+d.y,h);controls.update()};
async function send(action){const body={action,nom:$("name").value.trim()||"Vue 3D",focale:+$("foc").value,
 eye:cam.position.toArray(),target:controls.target.toArray(),mode:vmode,decentrement:vmode==="2pf"?+$("shift").value:0,coupe:+$("coupe").value,ev:+$("ev").value,ortho_largeur:+$("ow").value,
 heure:+$("hour").value,jour:+$("day").value,mois:+$("month").value,intensite:+$("sunInt").value,hdri_rot:+$("hdriRot").value};
 $("msg").textContent=action==="rendre"?"Rendu demandé : la fenêtre V-Ray va s'ouvrir…":"Vue enregistrée dans V-Ray Link.";
 try{await fetch("/camera",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}
 catch(e){$("msg").textContent="V-Ray Link est fermé : relance-le."}}
$("save").onclick=()=>send("enregistrer"); $("render").onclick=()=>send("rendre");
</script></body></html>"""



# ---------------------------------------------------------------- lumieres ---
TYPES_LUMIERE = {   # type : reglages par defaut (comme V-Ray pour SketchUp)
    "Spot": {"intensite": 1000.0, "cone": 45.0, "penombre": 25.0, "rayon_ombre": 0.02},
    "IES": {"intensite": 1.0, "ies": ""},
    "Rectangle": {"intensite": 30.0, "largeur": 1.0, "hauteur": 0.5, "invisible": True},
    "Sphère": {"intensite": 30.0, "rayon": 0.08, "invisible": False},
    "Omni": {"intensite": 1000.0, "rayon_ombre": 0.05},
    "Dôme": {"intensite": 1.0},
    "Maillage": {"intensite": 10.0, "surface": "", "invisible": False},   # Mesh Light : une surface du modele eclaire
}
LIGHTS: dict = {"rev": 0, "liste": []}
APERCU: dict = {"rev": 0, "etat": "", "fichier": ""}  # apercu V-Ray rapide demande depuis la visionneuse
OBJS: dict = {"rev": 0, "liste": []}        # objets Cosmos places (visionneuse : deplacer / tourner)      # copie partagee avec les pages web (visionneuse / editeur)


def _light_frame(l: dict):
    """Repere de la lumiere : -Z vers la cible (V-Ray eclaire selon -Z pour spot / rectangle / IES)."""
    pos = [float(v) for v in l.get("pos", (0, 0, 2.5))]
    tgt = [float(v) for v in l.get("cible", (pos[0], pos[1], pos[2] - 1))]
    d = [t - p for t, p in zip(tgt, pos)]
    if math.hypot(*d) < 1e-6:
        d = [0, 0, -1]
    up = (0.0, 1.0, 0.0) if abs(d[2]) / (math.hypot(*d)) > 0.95 else (0.0, 0.0, 1.0)
    x, y, z = frame(d, up)
    return pos, x, y, z


def write_lights(path: Path, lights: list, report=print, gids: dict | None = None) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as out:
        out.write("// Lumieres (V-Ray Link)\n")
        for k, l in enumerate(lights):
            if not l.get("actif", True):
                continue
            t = l.get("type", "Sphère")
            pos, x, y, z = _light_frame(l)
            tm = f"Transform(Matrix({vec(x)},{vec(y)},{vec(z)}),{vec(pos)})"
            r, g, b = (srgb_to_linear(float(c)) for c in (l.get("couleur") or (1.0, 0.92, 0.8)))
            col = f"Color({r:.4f},{g:.4f},{b:.4f})"
            it = float(l.get("intensite", TYPES_LUMIERE.get(t, {}).get("intensite", 30.0)))
            lid = f"lum{k}"
            if t == "Spot":
                out.write(f"LightSpot {lid} {{ transform={tm}; color={col}; intensity={it:.3f}; "
                          f"coneAngle={math.radians(float(l.get('cone', 45))):.5f}; penumbraAngle={math.radians(float(l.get('penombre', 25))):.5f}; "
                          f"decay=2; shadowRadius={float(l.get('rayon_ombre', 0.02)):.3f}; }}\n")
            elif t == "IES":
                ies = str(l.get("ies") or "")
                if not (ies and Path(ies).is_file()):
                    report(f"Lumière « {l.get('nom')} » : fichier IES introuvable, remplacée par un spot")
                    out.write(f"LightSpot {lid} {{ transform={tm}; color={col}; intensity={1000 * it:.3f}; coneAngle=1.4; "
                              "penumbraAngle=0.5; decay=2; }\n")
                else:
                    out.write(f'LightIES {lid} {{ transform={tm}; ies_file="{ies.replace(chr(92), "/")}"; '
                              f"filter_color=Color({r * it:.4f},{g * it:.4f},{b * it:.4f}); soft_shadows=1; }}\n")
            elif t == "Rectangle":
                out.write(f"LightRectangle {lid} {{ transform={tm}; color={col}; intensity={it:.3f}; units=0; "
                          f"u_size={float(l.get('largeur', 1.0)) / 2:.4f}; v_size={float(l.get('hauteur', 0.5)) / 2:.4f}; "
                          f"invisible={1 if l.get('invisible', True) else 0}; noDecay=0; }}\n")
            elif t == "Omni":
                out.write(f"LightOmni {lid} {{ transform={tm}; color={col}; intensity={it:.3f}; decay=2; "
                          f"shadowRadius={float(l.get('rayon_ombre', 0.05)):.3f}; }}\n")
            elif t == "Maillage":
                gid = (gids or {}).get(l.get("surface", ""))
                if not gid:
                    report(f"Lumière « {l.get('nom')} » : surface « {l.get('surface')} » absente du modèle")
                    continue
                out.write(f"LightMesh {lid} {{ transform=Transform(Matrix(Vector(1,0,0),Vector(0,1,0),Vector(0,0,1)),Vector(0,0,0)); "
                          f"geometry={gid}; color={col}; intensity={it:.3f}; units=0; doubleSided=1; "
                          f"invisible={1 if l.get('invisible') else 0}; }}\n")
            elif t == "Dôme":
                out.write(f"LightDome {lid} {{ transform=Transform(Matrix(Vector(1,0,0),Vector(0,1,0),Vector(0,0,1)),Vector(0,0,0)); "
                          f"color={col}; intensity={it:.3f}; invisible=0; }}\n")
            else:
                out.write(f"LightSphere {lid} {{ transform={tm}; color={col}; intensity={it:.3f}; units=0; "
                          f"radius={float(l.get('rayon', 0.08)):.4f}; invisible={1 if l.get('invisible') else 0}; }}\n")
            n += 1
    return n


def archicad_lamps() -> list:
    """Luminaires Archicad (outil Lampe) -> lumieres spheriques au centre haut de chaque luminaire."""
    ids = tapir("GetElementsByType", {"elementType": "Lamp"}).get("elements", [])
    out = []
    for i in range(0, len(ids), 300):
        part = ids[i:i + 300]
        boxes = tapir("Get3DBoundingBoxes", {"elements": part}).get("boundingBoxes3D", [])
        for el, bx in zip(part, boxes):
            b = bx.get("boundingBox3D", bx) if isinstance(bx, dict) else {}
            if "xMin" not in b:
                continue
            cx, cy = (b["xMin"] + b["xMax"]) / 2, (b["yMin"] + b["yMax"]) / 2
            z = b["zMin"] + 0.02                                    # sous le luminaire : il eclaire vers le bas
            out.append({"type": "Sphère", "nom": f"Luminaire {len(out) + 1}", "pos": [cx, cy, z], "cible": [cx, cy, z - 1],
                        "couleur": [1.0, 0.86, 0.68], "intensite": 30.0, "rayon": 0.05, "invisible": False, "actif": True,
                        "archicad": el["elementId"]["guid"]})
    return out


# ----------------------------------------------------------------- scene ---
def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def render_size(opts: dict) -> tuple[int, int]:
    W, H = TAILLES.get(opts["taille"], (1920, 1080))
    if opts.get("taille_perso"):
        W, H = max(64, int(opts.get("largeur", 3000))), max(64, int(opts.get("hauteur", 2000)))
    vr = TYPES_CAMERA.get(opts.get("type_camera", "Standard"), 0)
    if vr == 9:
        W, H = W, W // 2                      # panorama 360 : image 2:1
    elif vr == 10:
        W, H = W, max(64, W // 6)             # cubemap 6x1
    if opts.get("_apercu"):                   # apercu rapide : petite image, meme cadrage
        k = 640 / max(W, H)
        W, H = max(32, int(W * k)), max(32, int(H * k))
    return W, H


def window_portals(meshes: dict, mats: dict) -> list:
    """Rectangles des vitrages (pour les portails de lumiere V-Ray) : centre, largeur, hauteur, direction horizontale."""
    out = []
    for name, (verts, faces) in meshes.items():
        info = mats.get(name) or {}
        if float(info.get("transparence", 0.0)) < 0.15 and "glass" not in name.lower() and "verre" not in name.lower():
            continue
        if "mirror" in name.lower() or "miroir" in name.lower():
            continue
        parent = list(range(len(verts)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]; i = parent[i]
            return i
        for k in range(0, len(faces) - 2, 3):
            a, b, c = find(faces[k]), find(faces[k + 1]), find(faces[k + 2])
            parent[b] = a; parent[c] = a
        groups = {}
        for i in range(len(verts)):
            groups.setdefault(find(i), []).append(verts[i])
        for pts in groups.values():
            xs, ys, zs = [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts]
            h = max(zs) - min(zs)
            if h < 0.3 or len(pts) < 4:
                continue                                   # verriere horizontale / petit detail : ignore
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
            ux, uy = math.cos(ang), math.sin(ang)
            proj = [(x - mx) * ux + (y - my) * uy for x, y in zip(xs, ys)]
            thick = [-(x - mx) * uy + (y - my) * ux for x, y in zip(xs, ys)]
            w = max(proj) - min(proj)
            if w < 0.3 or max(thick) - min(thick) > 0.3:
                continue
            c = (mx + ux * (max(proj) + min(proj)) / 2, my + uy * (max(proj) + min(proj)) / 2, (max(zs) + min(zs)) / 2)
            out.append({"centre": c, "largeur": w, "hauteur": h, "dir": (ux, uy)})
    # vitres doubles (2 faces d'un meme vitrage) : une seule
    res = []
    for p in sorted(out, key=lambda q: -q["largeur"] * q["hauteur"]):
        if not any(math.dist(p["centre"], q["centre"]) < 0.3 and abs(p["largeur"] - q["largeur"]) < 0.2 for q in res):
            res.append(p)
    return res


def interieur(opts: dict) -> bool:
    return str(opts.get("ambiance", "")).startswith("Intérieur")


def zones_claires(opts: dict) -> float:
    """0..1 : protection des zones trop claires (fenetres, ciel). Auto : 60 % en interieur, 0 % dehors."""
    v = opts.get("zones_claires")
    return (0.6 if interieur(opts) else 0.0) if v in (None, "", "auto") else max(0.0, min(1.0, float(v) / 100.0))


def write_scene(path: Path, geometry: Path, cam: dict, opts: dict, proj: dict, ground_z: float) -> None:
    W, H = render_size(opts)
    vr = TYPES_CAMERA.get(opts.get("type_camera", "Standard"), 0)
    minutes, thr = QUALITES.get(opts["qualite"], QUALITES["Haute"])
    if opts.get("_apercu"):
        minutes, thr = 0.35, 0.08
    fnum, shutter, iso = AMBIANCES.get(opts["ambiance"], AMBIANCES["Extérieur jour"])
    inside = interieur(opts)
    if inside and not opts.get("_apercu"):
        minutes *= 1.5                        # interieur : lumiere indirecte -> plus de temps pour un rendu propre
    eye, target = list(cam["eye"]), list(cam["target"])
    mode = cam.get("mode")                    # "2pf" (2 points de fuite) / "persp" (libre) ; sinon reglage general
    if TYPES_CAMERA.get(opts.get("type_camera", "Standard"), 0) or mode == "2pf" or (
            mode is None and opts.get("verticales") and not cam.get("nom", "").startswith("Vue aérienne")):
        target[2] = eye[2]                    # camera horizontale : les verticales restent droites
    shift = float(cam.get("decentrement", 0.0)) if mode == "2pf" else 0.0
    shutter = shutter / (2.0 ** float(cam.get("ev", 0.0)))          # correction d'exposition propre a la vue (+1 = 2x plus clair)
    clip = float(cam.get("coupe", 0.0))                               # plan de coupe : traverse les murs devant la camera
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
         f"SettingsLightCache {{ subdivs={3000 if inside else 1500}; retrace_enabled={1 if inside else 0}; }}",
         f"SettingsImageSampler {{ type=3; progressive_maxTime={minutes:.2f}; progressive_threshold={thr}; "
         "progressive_minSubdivs=1; progressive_maxSubdivs=100; }",
         # Reinhard (type 6) : bright_mult = « burn » = garde le detail des zones trop claires (1 = aucun effet)
         "SettingsColorMapping { type=6; dark_mult=1; gamma=2.2; subpixel_mapping=0; clamp_output=0; "
         f"bright_mult={max(0.2, min(1.0, 2.0 - float(opts.get('contraste', 1.0))) - 0.8 * zones_claires(opts)):.2f}; }}",
         "RenderChannelDenoiser denoiser { enabled=1; mode=1; name=\"VRayDenoiser\"; }",   # mode 1 : pas de fichier « VRayDenoiser » en double ; l image debruitee = .effectsResult (voir _fx)
         f"RenderView view {{\n  transform=Transform(Matrix({vec(x)},{vec(y)},{vec(z)}),{vec(eye)});\n  fov={hfov:.6f};\n"
         + (f"  clipping=1;\n  clipping_near={clip:.3f};\n  clipping_far=100000;\n" if clip > 0 else "") + "}",
         f"CameraPhysical cam {{ type=0; specify_fov=1; fov={hfov:.6f}; f_number={fnum}; shutter_speed={shutter}; "
         f"ISO={iso}; exposure={0 if (opts.get('hdri') and not opts.get('hdri_soleil', True)) else 1}; white_balance=Color({wb[0]:.3f},{wb[1]:.3f},{wb[2]:.3f}); "
         f"vignetting={float(opts.get('vignettage', 0.3)):.2f}; use_dof={1 if opts.get('dof') else 0}; "
         f"specify_focus=1; focus_distance={dist:.3f}; lens_shift=0; vertical_offset={-shift * H / W:.4f}; }}",   # unite = largeur image, + = cadre vers le bas (mesure sur V-Ray)
         f"SunLight sun {{\n  transform=Transform(Matrix({vec(sx)},{vec(sy)},{vec(sz)}),{vec([c * 1000 for c in sd])});\n"
         f"  intensity_multiplier={float(opts.get('soleil_intensite', 1.0)):.3f};\n  size_multiplier={float(opts.get('taille_soleil', 3.0))};\n"
         f"  turbidity={float(opts.get('trouble', 3.0))};\n  ozone=0.35;\n  sky_model=3;\n}}",
         "TexSky sky { sun=sun; }"]
    if vr:                                    # panorama / cubemap VR : camera horizontale, sans appareil physique
        s = [l_ for l_ in s if not l_.startswith("CameraPhysical cam")]
        s.append(f"SettingsCamera {{ type={vr}; fov=6.283185; height=180; }}")   # height = champ vertical (degres) du panorama
        s = [l_.replace(f"fov={hfov:.6f};", "fov=6.283185;") if l_.startswith("RenderView view") else l_ for l_ in s]
    auto_expo = bool(vr) or mode == "ortho" or opts.get("ambiance") == "Nuit"
    if mode == "ortho" and not vr:           # vue orthogonale : pas d'appareil photo physique -> exposition automatique
        width = max(1.0, float(cam.get("ortho_largeur", 20.0)))
        s = [l_.replace("  fov=", f"  orthographic=1;\n  orthographicWidth={width:.3f};\n  ortho_adjust_to_scene=0;\n  fov=")
             if l_.startswith("RenderView view") else l_ for l_ in s if not l_.startswith("CameraPhysical cam")]
    if auto_expo:                            # nuit / ortho : V-Ray regle lui-meme l'exposition (comme V-Ray pour SketchUp)
        ae = f"auto_exposure=2; auto_corrections_mode=1; auto_exposure_compensation={float(cam.get('ev', 0.0)):.2f};"
        if any(l_.startswith("SettingsCamera") for l_ in s):
            s = [l_.replace("SettingsCamera {", "SettingsCamera { " + ae, 1) if l_.startswith("SettingsCamera") else l_ for l_ in s]
        else:
            s.append(f"SettingsCamera {{ {ae} }}")
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
    if inside and opts.get("portails", True):
        # Interieur : le ciel eclaire par un Dome Light adaptatif (V-Ray trouve tout seul les fenetres, comme des
        # portails automatiques) au lieu de l'environnement brut -> beaucoup moins de grain dans la piece.
        s.append(f"LightDome ciel_dome {{ transform=Transform(Matrix(Vector(1,0,0),Vector(0,1,0),Vector(0,0,1)),Vector(0,0,0)); "
                 f"use_dome_tex=1; dome_tex={env}; dome_spherical=1; dome_adaptive=1; invisible=1; affectReflections=0; }}")
        s.append(f"SettingsEnvironment {{ bg_tex={env}; reflect_tex={env}; refract_tex={env}; use_gi=0; }}")
    else:
        s.append(f"SettingsEnvironment {{ bg_tex={env}; gi_tex={env}; reflect_tex={env}; refract_tex={env}; }}")
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
    if (path.parent / "lumieres.vrscene").exists():
        s.append('#include "lumieres.vrscene"')
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
    # Le lecteur .vrmat de vray.exe (V-Ray pour SketchUp) ne comprend pas « texture::out_intensity » :
    # la texture d'opacite des feuilles etait perdue -> herbe / feuillage invisibles (seuls quelques reflets blancs).
    fixed = fixed.replace("::out_intensity", "")
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
            "type_camera": "Standard", "taille_perso": False, "largeur": 3000, "hauteur": 2000,
            "balance": 0.0, "vignettage": 0.3, "contraste": 1.0,
            "trouble": 3.0, "taille_soleil": 3.0, "zones_claires": "auto", "portails": True, "lieu": "Abidjan", "reutiliser": False, "oeil": 1.6, "cible": 1.4, "focale": 24}
    opts.update({k: v for k, v in (saved.get("options") or {}).items() if k in opts})
    return {"options": opts, "cameras": saved.get("cameras") or {}, "bibliotheque": saved.get("bibliotheque") or {},
            "vray": saved.get("vray", ""), "coches": saved.get("coches") or {},
            "cosmos": saved.get("cosmos") or {}, "selection": saved.get("selection") or {},
            "traces": saved.get("traces") or {}, "objets": saved.get("objets") or {},
            "scatter": saved.get("scatter") or {}, "mat_perso": saved.get("mat_perso") or {},
            "lumieres": saved.get("lumieres") or {}}


def save_settings(state: dict) -> None:
    out = {k: state.get(k) or {} for k in ("options", "cameras", "bibliotheque", "coches", "cosmos", "selection", "traces", "objets", "scatter", "mat_perso", "lumieres")}
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
                           assigned, report, (state.get("mat_perso") or {}).get(key, {}))
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
    lights = (state.get("lumieres") or {}).get(key, [])
    n_lum = write_lights(d / "lumieres.vrscene", lights, report, {name: f"g{k}" for k, name in enumerate(meshes)})
    sc_notes: list = []
    n_sc = write_scatter(d / "objets.vrscene", (state.get("scatter") or {}).get(key, []), meshes, offset, ground, key,
                         sc_notes.append)
    summary = f"{len(meshes)} surfaces, {tris:,} triangles".replace(",", " ") + (f", {n_lum} lumière(s)" if n_lum else "")
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
        'set RC=%errorlevel%',
        'if exist "%VRL_IMAGE%" goto fin',
        'if "%RC%"=="0" goto fin',
        "echo.", "echo V-Ray s'est ferme sans image : nouvel essai" + (" sur le processeur (CPU)..." if engine else "..."),
        "timeout /t 3 >nul",
        f'"%VRL_VRAY%" -sceneFile="%VRL_SCENE%" -imgFile="%VRL_IMAGE%" {opts}',
        'set RC=%errorlevel%',
        'if exist "%VRL_IMAGE%" goto fin',
        'if "%RC%"=="0" goto fin',
        'if not exist "%VRL_SECOURS%" goto echec',
        "echo.", "echo ============================================================",
        "echo  V-Ray plante sur un materiau SCANNE ^(Chaos Scans^).",
        "echo  Rendu de secours : ces materiaux sont remplaces par la couleur Archicad.",
        "echo ============================================================", "timeout /t 3 >nul",
        f'"%VRL_VRAY%" -sceneFile="%VRL_SECOURS%" -imgFile="%VRL_IMAGE%" {opts}',
        'if exist "%VRL_IMAGE%" goto fin',
        ":echec", "set CODE=%RC%", "echo.",
        "echo *** V-Ray n'a pas pu rendre l'image ^(code %CODE%^) : fais une capture de cette fenetre. ***",
        "pause", ":fin", *_SWAP("VRL_IMAGE", "VRL_FX", "VRL_BRUT")],
        {"VRL_VRAY": vray, "VRL_SCENE": scene, "VRL_IMAGE": image, "VRL_FX": _fx(image), "VRL_BRUT": _brut(image),
         "VRL_SECOURS": secours if secours.exists() else ""}, scene.parent)


def _fx(image: Path) -> Path:
    """V-Ray enregistre l'image brute (avec grain) + « .effectsResult » = l'image debruitee, celle a garder."""
    return image.with_name(image.stem + ".effectsResult" + image.suffix)


def _brut(image: Path) -> Path:
    return image.with_name(image.stem + "_brut_avec_grain" + image.suffix)


def _SWAP(img: str, fx: str, brut: str) -> list:
    return [f'if exist "%{fx}%" if exist "%{img}%" (copy /y "%{img}%" "%{brut}%" >nul & copy /y "%{fx}%" "%{img}%" >nul & del "%{fx}%")']


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
        env[f"VRL_F{k}"], env[f"VRL_R{k}"] = _fx(image), _brut(image)
        lines += _SWAP(f"VRL_I{k}", f"VRL_F{k}", f"VRL_R{k}")
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
    ttk.Label(am, text="Fenêtres et ciel trop blancs : garder le détail").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
    zc_var = tk.StringVar(value=str(opts.get("zones_claires", "auto")))
    ttk.Combobox(am, values=["auto", "0", "25", "50", "75", "100"], textvariable=zc_var, width=6).grid(
        row=2, column=2, sticky="w", pady=(6, 0))
    ttk.Label(am, foreground="#777", wraplength=430, justify="left",
              text="« auto » = 60 % en intérieur (on voit encore le jardin par la fenêtre), 0 % dehors. "
                   "En intérieur, V-Ray Link éclaire aussi la pièce avec la lumière du ciel « intelligente » de V-Ray "
                   "(elle passe par les fenêtres) : image moins granuleuse.").grid(row=3, column=0, columnspan=4, sticky="w")
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
                        zones_claires=(zc_var.get().strip() if zc_var.get().strip() in ("auto", "") else float(zc_var.get())),
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
        ttk.Label(im, text="Type de caméra").grid(row=2, column=0, sticky="w", pady=(6, 0))
        v["type_camera"] = tk.StringVar(value=opts.get("type_camera", "Standard"))
        ttk.Combobox(im, values=list(TYPES_CAMERA), textvariable=v["type_camera"], state="readonly", width=34).grid(
            row=2, column=1, sticky="w", padx=6, pady=(6, 0))
        v["taille_perso"] = tk.BooleanVar(value=opts.get("taille_perso", False))
        ttk.Checkbutton(im, text="Taille personnalisée (px)", variable=v["taille_perso"]).grid(row=3, column=0, sticky="w", pady=(6, 0))
        wh = ttk.Frame(im)
        wh.grid(row=3, column=1, sticky="w", padx=6, pady=(6, 0))
        v["largeur"] = tk.IntVar(value=opts.get("largeur", 3000))
        v["hauteur"] = tk.IntVar(value=opts.get("hauteur", 2000))
        ttk.Spinbox(wh, from_=64, to=16000, increment=100, textvariable=v["largeur"], width=7).grid(row=0, column=0)
        ttk.Label(wh, text=" × ").grid(row=0, column=1)
        ttk.Spinbox(wh, from_=64, to=16000, increment=100, textvariable=v["hauteur"], width=7).grid(row=0, column=2)
        ttk.Label(im, foreground="#777", wraplength=430, justify="left",
                  text="Panorama 360° : image 2:1 à regarder en visite virtuelle (casque VR, Kuula, Facebook 360). "
                       "Cubemap : 6 faces côte à côte. Place la caméra au centre de la pièce, à 1,6 m.").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
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
                             "hdri_soleil", "contraste", "soleil_intensite", "type_camera", "taille_perso", "largeur", "hauteur"):
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
                             "auto": bool(c.get("auto")), "mode": c.get("mode") or ("2pf" if opts.get("verticales") else "persp"),
                             "decentrement": c.get("decentrement", 0.0), "coupe": c.get("coupe", 0.0),
                             "ortho_largeur": c.get("ortho_largeur", 20.0),
                             "ev": c.get("ev", 0.0)} for c in all_cams()]}

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
                if msg.get("action") == "choisir":
                    from tkinter import filedialog
                    if msg.get("filtre") == "ies":
                        path = filedialog.askopenfilename(parent=root, title="Fichier IES (photométrie du luminaire)",
                                                          filetypes=[("IES", "*.ies *.IES"), ("Tous", "*.*")])
                    else:
                        path = filedialog.askopenfilename(parent=root, title="Image de texture", filetypes=[
                            ("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.exr *.tx *.bmp"), ("Tous", "*.*")])
                    CHOIX[str(msg.get("id"))] = path or ""
                    continue
                if msg.get("action") == "apercu":
                    start_preview(msg)
                    continue
                if msg.get("action") == "obj_edit":
                    lst = state.setdefault("objets", {}).setdefault(key, [])
                    i = int(msg.get("i", -1))
                    if 0 <= i < len(lst):
                        o = lst[i]
                        op = msg.get("op")
                        if op == "delete":
                            delete_elements(o.get("traces", []))
                            lst.pop(i)
                        else:
                            if op == "dup":
                                o = {k_: v_ for k_, v_ in o.items() if k_ != "traces"}
                                lst.append(o)
                            for k_ in ("x", "y", "z"):
                                if k_ in msg:
                                    o[k_] = float(msg[k_])
                            if "rot" in msg:
                                o["rotation"] = round(float(msg["rot"]) % 360, 1)
                            delete_elements(o.get("traces", []))
                            try:
                                o["traces"] = draw_object_marker(o)
                            except Exception:  # noqa: BLE001
                                o["traces"] = []
                        save_settings(state)
                        OBJS.update(liste=objs_payload(), rev=OBJS["rev"] + 1)
                        say(f"Objet « {o['nom']} » {'supprimé' if op == 'delete' else 'mis à jour'} (plan et rendu).")
                    continue
                if msg.get("action") in ("lum_save", "lum_import"):
                    lst = state.setdefault("lumieres", {}).setdefault(key, [])
                    if msg["action"] == "lum_save":
                        lst[:] = [dict(l_) for l_ in (msg.get("liste") or [])]
                    else:
                        try:
                            known = {l_.get("archicad") for l_ in lst}
                            new = [l_ for l_ in archicad_lamps() if l_["archicad"] not in known]
                            lst.extend(new)
                            say(f"{len(new)} luminaire(s) Archicad ajouté(s) comme lumières V-Ray.")
                        except Exception as error:  # noqa: BLE001
                            say(f"Lecture des luminaires impossible : {error}")
                    save_settings(state)
                    LIGHTS.update(liste=[dict(l_) for l_ in lst], rev=LIGHTS["rev"] + 1)
                    continue
                if msg.get("action") == "mat_save":
                    store = state.setdefault("mat_perso", {}).setdefault(key, {})
                    if msg.get("c"):
                        store[msg["surface"]] = dict(msg["c"])
                    else:
                        store.pop(msg.get("surface"), None)
                    save_settings(state)
                    LIVE_MAT.update(tous=dict(store), surfaces=[msg.get("surface")] if not msg.get("c") else [], c={},
                                    rev=LIVE_MAT["rev"] + 1)
                    snap["_surfaces"] = surfaces_info()
                    say(f"Matériau « {msg.get('surface')} » {'réglé' if msg.get('c') else 'remis d’origine'} (prochain rendu).")
                    continue
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
                cam = {"nom": name, "focale": round(float(msg.get("focale", 24)), 2),
                       "eye": [float(v) for v in msg["eye"]][:3], "target": [float(v) for v in msg["target"]][:3],
                       "mode": msg.get("mode") if msg.get("mode") in ("2pf", "ortho") else "persp",
                       "decentrement": float(msg.get("decentrement", 0.0)), "ortho_largeur": float(msg.get("ortho_largeur", 20.0)),
                       "coupe": max(0.0, float(msg.get("coupe", 0.0))), "ev": float(msg.get("ev", 0.0))}
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

    def surfaces_info() -> list:
        cols, _ = project_surfaces(state, key)
        perso = (state.get("mat_perso") or {}).get(key, {})
        cos = state.get("cosmos") or {}
        return [{"nom": n, "rgb": list(c)[:3], "perso": perso.get(n), "cosmos": bool((cos.get(n) or {}).get("dossier"))}
                for n, c in sorted(cols.items())]

    sizes: dict = {}

    def start_preview(msg: dict):
        """Apercu V-Ray rapide (640 px, ~20 s, sans fenetre) de la vue de la visionneuse, affiche dans la page."""
        if APERCU.get("etat") == "calcul":
            return
        if not check_vray():
            return
        collect()
        cam = {"nom": "Aperçu", "focale": round(float(msg.get("focale", 24)), 2), "eye": [float(v) for v in msg["eye"]][:3],
               "target": [float(v) for v in msg["target"]][:3], "mode": msg.get("mode") if msg.get("mode") in ("2pf", "ortho") else "persp",
               "decentrement": float(msg.get("decentrement", 0.0)), "ortho_largeur": float(msg.get("ortho_largeur", 20.0)),
               "coupe": float(msg.get("coupe", 0.0)), "ev": float(msg.get("ev", 0.0))}
        o2 = dict(opts, _apercu=True, reutiliser=True)
        if "heure" in msg:
            o2.update(heure=float(msg["heure"]), jour=int(msg["jour"]), mois=int(msg["mois"]))
        if "intensite" in msg:
            o2["soleil_intensite"] = float(msg["intensite"])
        if "hdri_rot" in msg:
            o2["hdri_rot"] = float(msg["hdri_rot"])
        APERCU.update(etat="calcul", rev=APERCU["rev"] + 1)
        st2 = dict(state, options=o2)
        try:
            geo, ground, _info = prepare_geometry(st2, proj, lambda *_: None)
            scene = geo.parent / "scene_apercu.vrscene"
            write_scene(scene, geo, cam, o2, proj, ground)
        except Exception as error:  # noqa: BLE001
            APERCU.update(etat=f"erreur : {error}", rev=APERCU["rev"] + 1)
            return
        image = geo.parent / "apercu.png"

        def run():
            try:
                if image.exists():
                    image.unlink()
                subprocess.run([state["vray"], f"-sceneFile={scene}", f"-imgFile={image}", "-display=0", "-autoClose=1",
                                "-verboseLevel=1"] + ([MOTEURS[opts["moteur"]]] if MOTEURS.get(opts.get("moteur")) else []),
                               cwd=str(scene.parent), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=600)
                ok_ = image.exists()
                fx = image.with_name(image.stem + ".effectsResult" + image.suffix)      # image debruitee
                if ok_ and fx.exists() and fx.stat().st_mtime >= image.stat().st_mtime - 5:
                    image = fx
                APERCU.update(etat="pret" if ok_ else "erreur : V-Ray n'a pas produit d'image", fichier=str(image) if ok_ else "",
                              rev=APERCU["rev"] + 1)
            except Exception as error:  # noqa: BLE001
                APERCU.update(etat=f"erreur : {error}", rev=APERCU["rev"] + 1)
        threading.Thread(target=run, daemon=True).start()

    def objs_payload() -> list:
        out = []
        for i, o in enumerate((state.get("objets") or {}).get(key, [])):
            if o["dossier"] not in sizes:
                sizes[o["dossier"]] = obj_size(o["dossier"])
            out.append({"i": i, "nom": o["nom"], "x": o["x"], "y": o["y"], "z": o["z"], "rot": o.get("rotation", 0.0),
                        "echelle": o.get("echelle", 1.0), "taille": sizes[o["dossier"]]})
        return out

    def open_viewer(page: str = ""):
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
        LIVE_MAT.update(tous=dict((state.get("mat_perso") or {}).get(key, {})), surfaces=[], c={}, rev=LIVE_MAT["rev"] + 1)
        snap["_surfaces"] = surfaces_info()
        LIGHTS.update(liste=[dict(l_) for l_ in (state.get("lumieres") or {}).get(key, [])], rev=LIGHTS["rev"] + 1)
        OBJS.update(liste=objs_payload(), rev=OBJS["rev"] + 1)
        if viewer["srv"] is None:
            viewer["srv"] = ViewerServer(key, lambda: dict(snap), inbox.put)
            viewer["srv"].etat = lambda: {"hdri_rot": float(opts.get("hdri_rot", 0.0)),
                                          "intensite": float(opts.get("soleil_intensite", 1.0)),
                                          "taille": list(render_size(opts)), "qualite": opts.get("qualite"),
                                          "type_camera": opts.get("type_camera", "Standard"), "apercu": dict(APERCU)}
            viewer["srv"].surfaces = lambda: snap.get("_surfaces", [])
            viewer["srv"].cosmos_tex = lambda nom: cosmos_textures(((state.get("cosmos") or {}).get(nom) or {}).get("dossier", ""))
        viewer["srv"].open(page)
        if page:
            say("Éditeur de matériaux ouvert : les réglages s'affichent en direct (et dans la visionneuse si elle est ouverte).")
            return
        say("Visionneuse 3D ouverte : place-toi, puis « Rendre cette vue ». (Elle montre le dernier export du modèle.)")

    root.after(300, poll_viewer)
    state["_open_viewer"] = open_viewer
    state["_open_editor"] = lambda: open_viewer("editeur")
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
    "Herbe / gazon": (6.0, 1.0, 30, 0.0, False),        # version detaillee : les copies partagent la meme geometrie
    "Fleurs / buissons": (1.0, 1.0, 25, 0.6, False),
    "Arbres": (2.0, 100.0, 20, 5.0, False),
}
SCATTER_MAX = 30000


def grass_density(folder: str, default: float) -> float:
    """Touffes par m2 pour un gazon couvrant (~95 %) : depend de la taille reelle de la touffe Cosmos."""
    try:
        mesh = sorted(Path(folder).glob("*.vrmesh"), key=_lod)[0]
        w, d_ = vrmesh_size(mesh)[:2]
        return max(default, min(60.0, 4.5 / max(0.01, w * d_)))
    except Exception:  # noqa: BLE001
        return default


def scatter_points(sc: dict, meshes: dict, offset: tuple, ground: float, key: str) -> list:
    """Points (x, y, z) repartis au hasard (graine fixe) sur une surface Archicad (faces vers le haut) ou sur le sol autour."""
    import random
    rnd = random.Random(f"{sc.get('dossier')}|{sc.get('surface')}")
    dens = max(0.01, float(sc.get("densite", 4.0)))
    if sc.get("type") == "Herbe / gazon" and sc.get("zones") and not sc.get("densite_manuelle"):
        dens = grass_density(sc.get("dossier", ""), dens)
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
                mesh = lods[0]                                   # la plus detaillee (3dl = vue lointaine : touffes en « taches »)
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
        lab = min(labels, key=lambda t_: math.dist(t_["p"], c["c"]), default=None)      # le nom le plus proche du rond
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



# ---------------------------------------------------------- materiau perso ---
MAT_PRESETS = {   # reflet, brillance, metal, refraction, ior
    "Mat (enduit, béton)": dict(reflet=0.05, brillance=0.35, metal=0.0, refraction=0.0, ior=1.5),
    "Satiné (peinture, bois verni)": dict(reflet=0.35, brillance=0.7, metal=0.0, refraction=0.0, ior=1.5),
    "Brillant (laqué, carrelage)": dict(reflet=0.8, brillance=0.92, metal=0.0, refraction=0.0, ior=1.6),
    "Métal brossé": dict(reflet=1.0, brillance=0.75, metal=1.0, refraction=0.0, ior=2.5),
    "Métal poli / chrome": dict(reflet=1.0, brillance=0.98, metal=1.0, refraction=0.0, ior=3.0),
    "Verre": dict(reflet=1.0, brillance=1.0, metal=0.0, refraction=0.95, ior=1.52),
    "Verre dépoli": dict(reflet=1.0, brillance=0.75, metal=0.0, refraction=0.9, ior=1.52, flou_refraction=0.7),
    "Eau": dict(reflet=1.0, brillance=0.98, metal=0.0, refraction=0.95, ior=1.33),
    "Miroir": dict(reflet=1.0, brillance=1.0, metal=1.0, refraction=0.0, ior=20.0),
}


EDITOR_HTML = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>V-Ray Link · Éditeur de matériaux</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#1d1f23;--panel:#25282d;--panel2:#2c3036;--line:#383c44;--txt:#e6e6e6;--mute:#9aa0aa;--acc:#1fa3ff;--org:#e0702a}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);font:13px "Segoe UI",system-ui,sans-serif}
#app{display:grid;grid-template-columns:270px 1fr;height:100%}
#list{background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
#list header{padding:10px;border-bottom:1px solid var(--line)}
#list h1{font-size:14px;margin:0 0 8px}#list h1 b{color:var(--acc)}
#q{width:100%;background:#17191c;border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:7px 9px;font:inherit}
#items{overflow:auto;flex:1;padding:4px 0}
.it{display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;border-left:3px solid transparent}
.it:hover{background:#2e3238}.it.on{background:#203448;border-left-color:var(--acc)}
.dot{width:14px;height:14px;border-radius:50%;flex:none;box-shadow:inset 0 0 0 1px rgba(255,255,255,.2)}
.it .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{font-size:10px;padding:1px 5px;border-radius:4px;background:#3a3f48;color:var(--mute)}.tag.p{background:#2b4a2b;color:#9fdc9f}.tag.c{background:#4a3a24;color:#f0b77a}
#main{display:grid;grid-template-rows:300px 1fr;min-height:0}
#prev{position:relative;background:repeating-conic-gradient(#2a2d32 0 25%,#232529 0 50%) 0 0/24px 24px;border-bottom:1px solid var(--line);display:flex;justify-content:center;align-items:center}
#prev canvas{border-radius:4px}
#pinfo{position:absolute;left:12px;top:10px;color:var(--mute);font-size:12px}
#shape{position:absolute;right:12px;top:10px;display:flex;gap:4px}
#params{overflow:auto;padding:10px 14px 80px}
.sec{background:var(--panel);border:1px solid var(--line);border-radius:6px;margin-bottom:8px}
.sec>h3{margin:0;padding:9px 12px;font-size:13px;display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none}
.sec>h3::before{content:"▸";color:var(--mute);transition:transform .15s}.sec.open>h3::before{transform:rotate(90deg)}
.sec .body{display:none;padding:4px 12px 12px}.sec.open .body{display:block}
.row{display:grid;grid-template-columns:150px 1fr 56px;align-items:center;gap:10px;margin:7px 0}
.row label{color:var(--mute)}
input[type=range]{width:100%;accent-color:var(--acc)}
.num{background:#17191c;border:1px solid var(--line);color:var(--acc);border-radius:4px;padding:3px 5px;width:56px;font:inherit;text-align:right}
.file{display:flex;gap:6px;align-items:center}.file .fn{flex:1;background:#17191c;border:1px solid var(--line);border-radius:4px;padding:5px 8px;color:var(--txt);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:28px}
.file .th{width:28px;height:28px;border-radius:4px;background:#17191c center/cover;border:1px solid var(--line);flex:none}
button{background:#33373e;color:var(--txt);border:1px solid var(--line);border-radius:5px;padding:5px 10px;cursor:pointer;font:inherit}
button:hover{border-color:#566}button.main{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
select{background:#17191c;color:var(--txt);border:1px solid var(--line);border-radius:4px;padding:4px 6px;font:inherit}
input[type=color]{width:56px;height:26px;border:1px solid var(--line);background:#17191c;border-radius:4px;padding:0}
#bar{position:fixed;right:0;bottom:0;left:270px;background:var(--panel);border-top:1px solid var(--line);padding:10px 14px;display:flex;gap:8px;align-items:center}
#msg{flex:1;color:var(--mute)}
.empty{color:var(--mute);padding:40px;text-align:center}
.tabs{display:flex;gap:4px;margin-bottom:8px}.tabs button{flex:1}.tabs button.on{background:var(--acc);border-color:var(--acc);color:#fff}
input[type=text].txt{background:#17191c;border:1px solid var(--line);color:var(--txt);border-radius:4px;padding:4px 6px;font:inherit;width:100%}
.xyz{display:flex;gap:4px}.xyz input{width:70px}
</style></head><body>
<div id="app">
<aside id="list"><header><h1>V-Ray Link · <b>Éditeur</b></h1>
<div class="tabs"><button id="tMat" class="on">🎨 Matériaux</button><button id="tLum">💡 Lumières</button></div>
<input id="q" placeholder="Rechercher…"></header><div id="items"></div><div id="lumBtns" style="display:none;padding:8px;border-top:1px solid var(--line);flex-direction:column;gap:6px">
<button id="lImport">⇩ Importer les luminaires Archicad</button><div style="color:var(--mute);font-size:12px">Pour poser une lumière à un endroit précis : visionneuse 3D, bouton 💡, puis clic sur le plafond ou le mur.</div></div></aside>
<section id="main">
<div id="prev"><div id="pinfo">Aperçu</div><div id="shape"><button data-s="sphere">Sphère</button><button data-s="cube">Cube</button><button data-s="plan">Plan</button></div></div>
<div id="params"><div class="empty">Choisis une surface à gauche.</div></div>
</section></div>
<div id="bar"><span id="msg">Les réglages s'affichent en direct ici et dans la visionneuse 3D.</span>
<button id="reset">Matériau d'origine</button><button id="save" class="main">Appliquer au rendu</button></div>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from "three";
import {RoomEnvironment} from "three/addons/environments/RoomEnvironment.js";
const $=id=>document.getElementById(id);
const post=(b)=>fetch("/camera",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});
// ---------- apercu
const pv=$("prev"); const R=new THREE.WebGLRenderer({antialias:true,alpha:true}); R.setPixelRatio(Math.min(devicePixelRatio,2));
R.toneMapping=THREE.ACESFilmicToneMapping; R.shadowMap.enabled=true; pv.appendChild(R.domElement);
const S=new THREE.Scene(); const C=new THREE.PerspectiveCamera(30,1.6,0.05,50); C.position.set(0,0.55,2.5); C.lookAt(0,0.45,0);
const pm=new THREE.PMREMGenerator(R); S.environment=pm.fromScene(new RoomEnvironment(),0.04).texture;
const L=new THREE.DirectionalLight(0xffffff,2); L.position.set(2,3,2); L.castShadow=true; S.add(L);
const floor=new THREE.Mesh(new THREE.PlaneGeometry(6,6),new THREE.MeshStandardMaterial({color:0x8a8d92,roughness:.9}));
floor.rotation.x=-Math.PI/2; floor.receiveShadow=true; S.add(floor);
const M=new THREE.MeshPhysicalMaterial({color:0xcccccc,roughness:.85}); let obj;
function shape(k){if(obj)S.remove(obj); const g=k==="cube"?new THREE.BoxGeometry(.75,.75,.75):k==="plan"?new THREE.PlaneGeometry(1.6,1.0):new THREE.SphereGeometry(.45,96,64);
 obj=new THREE.Mesh(g,M); obj.castShadow=true; obj.position.y=k==="plan"?0.5:(k==="cube"?0.375:0.45); S.add(obj); kind=k; applyLive()}
let kind="sphere"; for(const b of document.querySelectorAll("#shape button"))b.onclick=()=>shape(b.dataset.s);
function size(){const w=Math.min(pv.clientWidth-40,pv.clientHeight*1.6-20),h=w/1.6; R.setSize(w,h); C.aspect=1.6; C.updateProjectionMatrix()}
addEventListener("resize",size); size();
(function loop(){if(obj&&kind!=="plan")obj.rotation.y+=0.003; R.render(S,C); requestAnimationFrame(loop)})();
const texCache={};
async function tex(path,sz,rot,srgb,du=0,dv=0){if(!path)return null; const k=path+"|"+srgb;
 if(!texCache[k]){texCache[k]=(async()=>{const r=await fetch("/api/tex?f="+encodeURIComponent(path)); const d=(await r.json()).d; if(!d)return null;
  const t=await new THREE.TextureLoader().loadAsync("data:image/*;base64,"+d); t.wrapS=t.wrapT=THREE.RepeatWrapping; t.anisotropy=8;
  t.colorSpace=srgb?THREE.SRGBColorSpace:THREE.NoColorSpace; return t})()}
 const t0=await texCache[k]; if(!t0)return null; const t=t0.clone(); t.needsUpdate=true;
 const ext=kind==="sphere"?[2.83,1.41]:kind==="cube"?[0.75,0.75]:[1.6,1.0]; const s=1/Math.max(0.01,sz||1);
 t.repeat.set(ext[0]*s,ext[1]*s); t.rotation=(rot||0)*Math.PI/180; t.offset.set(-du*s,-dv*s); return t}
async function paint(c){const m=M, sz=c.taille_tex||1, rot=c.rotation_tex||0, du=c.decalage_u||0, dv=c.decalage_v||0;
 m.color.setRGB(c.couleur[0],c.couleur[1],c.couleur[2],THREE.SRGBColorSpace);
 m.map=await tex(c.tex_couleur,sz,rot,true,du,dv); if(m.map){const k=1+(c.luminosite||0); m.color.setRGB(k,k,k)}
 m.roughness=1-(c.brillance??0.5)*(c.reflet>0.02?1:0.3); m.metalness=c.metal||0; m.ior=c.ior||1.5; m.specularIntensity=Math.min(1,(c.reflet??0.1)*2);
 m.roughnessMap=await tex(c.tex_brillance,sz,rot,false,du,dv);
 const op=c.opacite??1; m.alphaMap=await tex(c.tex_opacite,sz,rot,false,du,dv); m.transparent=op<1||!!m.alphaMap; m.opacity=op;
 m.transmission=c.refraction||0; m.thickness=0.3; if(m.transmission>0)m.roughness=Math.max(m.roughness,(c.flou_refraction||0)*0.6);
 const rel=await tex(c.tex_relief,sz,rot,false,du,dv), nm=c.type_relief==="Normal map";
 m.normalMap=nm?rel:null; m.bumpMap=nm?null:rel; m.bumpScale=(c.relief||0.5)*2; if(nm)m.normalScale.set(c.relief||0.5,c.relief||0.5);
 const em=c.emission||0; m.emissive.setRGB(1,.9,.7); m.emissiveIntensity=em*0.4; if(!em)m.emissive.set(0);
 m.clearcoat=c.vernis||0; m.clearcoatRoughness=1-(c.vernis_brillance??0.9); m.sheen=c.velours||0; m.sheenColor.setRGB(1,1,1);
 m.needsUpdate=true}
// ---------- donnees
let SURF=[], cur=null, C0=null;
const DEF={couleur:[.75,.75,.75],tex_couleur:"",taille_tex:1,rotation_tex:0,decalage_u:0,decalage_v:0,luminosite:0,contraste_tex:1,saturation:1,teinte:0,
 reflet:.1,brillance:.5,metal:0,ior:1.5,tex_brillance:"",refraction:0,flou_refraction:0,opacite:1,tex_opacite:"",tex_relief:"",type_relief:"Bump (gris)",relief:.5,
 emission:0,vernis:0,vernis_brillance:.9,velours:0,procedural:"Aucune",proc_variation:.3,proc_taille:.3};
const PRESETS=__PRESETS__;
async function load(){SURF=await (await fetch("/api/surfaces")).json(); list()}
function hex(c){return "#"+c.map(v=>Math.round(Math.max(0,Math.min(1,v))*255).toString(16).padStart(2,"0")).join("")}
function list(){const q=$("q").value.toLowerCase(); $("items").innerHTML="";
 for(const s of SURF){if(q&&!s.nom.toLowerCase().includes(q))continue; const d=document.createElement("div"); d.className="it"+(cur&&cur.nom===s.nom?" on":"");
  d.innerHTML=`<span class="dot" style="background:${hex((s.perso&&s.perso.couleur)||s.rgb)}"></span><span class="nm"></span>${s.perso?'<span class="tag p">réglé</span>':s.cosmos?'<span class="tag c">Cosmos</span>':""}`;
  d.querySelector(".nm").textContent=s.nom; d.onclick=()=>select(s); $("items").appendChild(d)}}
$("q").oninput=list;
function select(s){cur=s; C0=Object.assign({},DEF,{couleur:s.rgb},s.perso||{}); build(); list(); applyLive()}
// ---------- formulaire
const SECS=[
 ["Diffuse",[["color","couleur","Couleur"],["file","tex_couleur","Texture (image)"],["num","taille_tex","Taille de l'image (m)",.05,10,.05],
  ["num","rotation_tex","Rotation (°)",0,180,1],["num","decalage_u","Décalage horizontal (m)",-2,2,.01],["num","decalage_v","Décalage vertical (m)",-2,2,.01],
  ["num","luminosite","Luminosité",-1,1,.01],["num","contraste_tex","Contraste *",0,2,.01],["num","saturation","Saturation *",0,2,.01],["num","teinte","Teinte (°) *",-180,180,1]]],
 ["Reflection",[["num","reflet","Réflexion",0,1,.01],["num","brillance","Brillance",0,1,.01],["num","metal","Métal",0,1,.01],["num","ior","Indice (IOR)",1,3,.01],["file","tex_brillance","Texture de brillance"]]],
 ["Refraction",[["num","refraction","Réfraction (verre)",0,1,.01],["num","flou_refraction","Flou (verre dépoli)",0,1,.01]]],
 ["Coat (vernis)",[["num","vernis","Vernis",0,1,.01],["num","vernis_brillance","Brillance du vernis",0,1,.01]]],
 ["Sheen (velours)",[["num","velours","Velours (tissus)",0,1,.01]]],
 ["Opacity",[["num","opacite","Opacité",0,1,.01],["file","tex_opacite","Texture d'opacité"]]],
 ["Bump",[["file","tex_relief","Texture de relief"],["sel","type_relief","Type",["Bump (gris)","Normal map"]],["num","relief","Intensité",0,2,.01]]],
 ["Self-illumination",[["num","emission","Émission (lumineux)",0,20,.1]]],
 ["Texture procédurale *",[["sel","procedural","Type",["Aucune","Bruit (variation de couleur)","Usure / saleté dans les angles"]],["num","proc_variation","Variation",0,1,.01],["num","proc_taille","Taille du motif (m)",.02,3,.01]]]];
const opened=new Set(["Diffuse","Reflection"]);
function build(){const P=$("params"); P.innerHTML="";
 const pr=document.createElement("div"); pr.className="row"; pr.innerHTML=`<label>Préréglage</label><select id="preset"><option value="">—</option>${Object.keys(PRESETS).map(k=>`<option>${k}</option>`).join("")}</select><span></span>`;
 P.appendChild(pr); $("preset").onchange=e=>{Object.assign(C0,PRESETS[e.target.value]||{}); build(); applyLive()};
 for(const [title,fields] of SECS){const sec=document.createElement("div"); sec.className="sec"+(opened.has(title)?" open":"");
  sec.innerHTML=`<h3>${title}</h3><div class="body"></div>`; sec.querySelector("h3").onclick=()=>{sec.classList.toggle("open"); sec.classList.contains("open")?opened.add(title):opened.delete(title)};
  const body=sec.querySelector(".body");
  for(const f of fields){const r=document.createElement("div"); r.className="row"; const [t,k,lab]=f; r.innerHTML=`<label>${lab}</label>`;
   if(t==="num"){const [, , ,lo,hi,st]=f; r.innerHTML+=`<input type="range" min="${lo}" max="${hi}" step="${st}" value="${C0[k]}"><input class="num" type="number" step="${st}" value="${C0[k]}">`;
    const [rg,nb]=r.querySelectorAll("input"); rg.oninput=()=>{nb.value=rg.value;C0[k]=+rg.value;applyLive()}; nb.onchange=()=>{rg.value=nb.value;C0[k]=+nb.value;applyLive()}}
   else if(t==="color"){r.innerHTML+=`<input type="color" value="${hex(C0.couleur)}"><span></span>`; r.querySelector("input").oninput=e=>{const h=e.target.value;
     C0.couleur=[1,3,5].map(i=>parseInt(h.substr(i,2),16)/255); applyLive()}}
   else if(t==="sel"){r.innerHTML+=`<select>${f[3].map(o=>`<option ${o===C0[k]?"selected":""}>${o}</option>`).join("")}</select><span></span>`; r.querySelector("select").onchange=e=>{C0[k]=e.target.value;applyLive()}}
   else if(t==="file"){r.style.gridTemplateColumns="150px 1fr"; r.innerHTML+=`<div class="file"><span class="th"></span><span class="fn"></span><button>…</button><button>✕</button></div>`;
    const fn=r.querySelector(".fn"), th=r.querySelector(".th"), [b1,b2]=r.querySelectorAll("button");
    const show=()=>{fn.textContent=C0[k]?C0[k].split(/[\\/]/).pop():"(aucune)"; fn.title=C0[k]||""; th.style.backgroundImage="";
     if(C0[k]&&/\.(jpe?g|png|bmp|webp)$/i.test(C0[k]))fetch("/api/tex?f="+encodeURIComponent(C0[k])).then(r=>r.json()).then(j=>{if(j.d)th.style.backgroundImage=`url(data:image/*;base64,${j.d})`})};
    show(); b1.onclick=async()=>{$("msg").textContent="Choisis l'image dans la fenêtre qui s'ouvre…"; const id=Math.random().toString(36).slice(2);
     await post({action:"choisir",id}); for(let i=0;i<600;i++){await new Promise(r=>setTimeout(r,500)); const j=await (await fetch("/api/choix?id="+id)).json();
      if(j.fini){if(j.path){C0[k]=j.path; show(); applyLive()} $("msg").textContent=""; break}}};
    b2.onclick=()=>{C0[k]="";show();applyLive()}}
   body.appendChild(r)}
  if(title==="Diffuse"&&cur.cosmos){const b=document.createElement("button"); b.textContent="⇩ Reprendre les textures du matériau Cosmos"; b.onclick=async()=>{
    Object.assign(C0,await (await fetch("/api/cosmos_tex?s="+encodeURIComponent(cur.nom))).json()); build(); applyLive()}; body.appendChild(b)}
  P.appendChild(sec)}
 const n=document.createElement("div"); n.style.cssText="color:var(--mute);font-size:12px;margin-top:6px"; n.textContent="* visible seulement au rendu V-Ray (pas dans l'aperçu).";
 P.appendChild(n); $("pinfo").textContent=cur.nom}
let liveT=null;
function applyLive(){if(!cur)return; paint(C0); clearTimeout(liveT); liveT=setTimeout(()=>post({action:"live",surfaces:[cur.nom],c:C0}),120)}
$("save").onclick=async()=>{if(!cur)return; await post({action:"mat_save",surface:cur.nom,c:C0}); cur.perso=Object.assign({},C0); list(); $("msg").textContent="✔ Appliqué : visible au prochain rendu."};
$("reset").onclick=async()=>{if(!cur)return; await post({action:"mat_save",surface:cur.nom,c:null}); cur.perso=null; select(cur); $("msg").textContent="Matériau d'origine rétabli."};
// =================== LUMIERES ===================
let tab="mat", LUM=[], lumRev=-1, curL=-1, lastEdit=0;
const LTYPES=__LTYPES__;
const room=new THREE.Group(); {const m=new THREE.MeshStandardMaterial({color:0xb9b9b9,roughness:.9});
 const w=new THREE.Mesh(new THREE.PlaneGeometry(6,4),m); w.position.set(0,2,-1.2); w.receiveShadow=true; room.add(w)} 
const lprev=new THREE.Group(); room.add(lprev);
function setTab(t){tab=t; $("tMat").classList.toggle("on",t==="mat"); $("tLum").classList.toggle("on",t==="lum");
 $("lumBtns").style.display=t==="lum"?"flex":"none"; $("save").style.display=$("reset").style.display=t==="mat"?"":"none";
 if(t==="lum"){S.add(room); if(obj)obj.visible=false; S.environment=null; L.intensity=0.05; floor.material.color.set(0x9a9a9a); $("shape").style.display="none"; lumList(); lumForm()}
 else{S.remove(room); if(obj)obj.visible=true; S.environment=envTex; L.intensity=2; $("shape").style.display=""; list(); if(cur)build(); else $("params").innerHTML='<div class="empty">Choisis une surface à gauche.</div>'}}
const envTex=S.environment;
$("tMat").onclick=()=>setTab("mat"); $("tLum").onclick=()=>setTab("lum");
$("lImport").onclick=async()=>{$("msg").textContent="Lecture des luminaires dans Archicad…"; await post({action:"lum_import"}); lastEdit=0};
async function pollL(){try{const j=await (await fetch("/api/lumieres")).json(); if(j.rev!==lumRev&&Date.now()-lastEdit>1500){lumRev=j.rev; LUM=j.liste||[];
  if(tab==="lum"){lumList(); if(curL>=LUM.length)curL=LUM.length-1; lumForm()}}}catch(_){} setTimeout(pollL,1000)}
const ICON={"Spot":"🔦","IES":"🔦","Rectangle":"▭","Sphère":"●","Omni":"✳","Dôme":"◓","Maillage":"〰"};
let SUN={intensite:1}, sunPrev=1;
async function pollSun(){try{const e=await (await fetch("/api/etat")).json(); if(e.intensite!==undefined&&Date.now()-lastEdit>1500){SUN.intensite=e.intensite; if(tab==="lum")sunRow()}}catch(_){}
 setTimeout(pollSun,1500)}
function lrow(icon,name,val,on,click,setVal,setOn,selected){const d=document.createElement("div"); d.className="it"+(selected?" on":"");
 d.innerHTML=`<span style="width:18px;text-align:center">${icon}</span><span class="nm"></span><input class="num" type="number" step="any" value="${val}" style="width:62px">
 <input type="checkbox" ${on?"checked":""} style="width:auto;margin:0">`; d.querySelector(".nm").textContent=name; if(!on)d.style.opacity=.55;
 const [n,c]=d.querySelectorAll("input"); n.onclick=c.onclick=e=>e.stopPropagation(); n.onchange=()=>setVal(+n.value); c.onchange=()=>setOn(c.checked);
 d.onclick=click; return d}
function sunRow(){const old=$("sunRow"); const d=lrow("☀","SunLight (soleil)",SUN.intensite,SUN.intensite>0,()=>{},
  v=>{SUN.intensite=v; lastEdit=Date.now(); post({action:"reglage",intensite:v})},
  on=>{if(on){SUN.intensite=sunPrev||1}else{sunPrev=SUN.intensite||1; SUN.intensite=0} lastEdit=Date.now(); post({action:"reglage",intensite:SUN.intensite}); sunRow()},false);
 d.id="sunRow"; d.title="Soleil V-Ray : intensité (1 = normal, 0 = éteint, pour une vue de nuit)"; if(old)old.replaceWith(d); else $("items").appendChild(d)}
function lumList(){const q=$("q").value.toLowerCase(); $("items").innerHTML="";
 const h=document.createElement("div"); h.style.cssText="padding:6px 12px;color:var(--mute);font-size:12px;font-weight:600"; h.textContent="Lights"; $("items").appendChild(h);
 LUM.forEach((l,i)=>{if(q&&!l.nom.toLowerCase().includes(q))return;
  $("items").appendChild(lrow(ICON[l.type]||"●",l.nom,l.intensite,l.actif!==false,()=>{curL=i; lumList(); lumForm()},
   v=>{l.intensite=v; saveL(); if(i===curL)lumForm()}, on=>{l.actif=on; saveL(); lumList()}, i===curL))});
 sunRow();
 if(!LUM.length){const e=document.createElement("div"); e.className="empty"; e.style.padding="20px"; e.innerHTML="Aucune lumière.<br>Visionneuse 3D › 💡, ou « Importer les luminaires Archicad »."; $("items").appendChild(e)}}
function saveL(){lastEdit=Date.now(); clearTimeout(saveL.t); saveL.t=setTimeout(()=>post({action:"lum_save",liste:LUM}),350); drawPrevL()}
function lumForm(){const P=$("params"); P.innerHTML=""; const l=LUM[curL]; if(!l){P.innerHTML='<div class="empty">Choisis une lumière à gauche.</div>'; drawPrevL(); return}
 $("pinfo").textContent=l.nom+" · "+l.type;
 const sec=document.createElement("div"); sec.className="sec open"; sec.innerHTML=`<h3>${l.type} Light</h3><div class="body"></div>`; const B=sec.querySelector(".body"); P.appendChild(sec);
 const row=(lab,html)=>{const r=document.createElement("div"); r.className="row"; r.innerHTML=`<label>${lab}</label>${html}`; B.appendChild(r); return r};
 let r=row("Nom",`<input class="txt" type="text"><span></span>`); r.querySelector("input").value=l.nom; r.querySelector("input").oninput=e=>{l.nom=e.target.value; saveL(); lumList()};
 r=row("Allumée",`<input type="checkbox" ${l.actif!==false?"checked":""} style="width:auto"><span></span>`); r.querySelector("input").onchange=e=>{l.actif=e.target.checked; saveL(); lumList()};
 r=row("Type",`<select>${Object.keys(LTYPES).map(t=>`<option ${t===l.type?"selected":""}>${t}</option>`).join("")}</select><span></span>`);
 r.querySelector("select").onchange=e=>{l.type=e.target.value; Object.entries(LTYPES[l.type]).forEach(([k,v])=>{if(l[k]===undefined)l[k]=v}); l.intensite=LTYPES[l.type].intensite; saveL(); lumList(); lumForm()};
 r=row("Couleur",`<input type="color" value="${hex(l.couleur||[1,.92,.8])}"><span></span>`); r.querySelector("input").oninput=e=>{const h=e.target.value; l.couleur=[1,3,5].map(i=>parseInt(h.substr(i,2),16)/255); saveL(); lumList()};
 const nums=[["intensite","Intensité",0,l.type==="Spot"||l.type==="Omni"?20000:(l.type==="IES"||l.type==="Dôme"?20:500),l.type==="IES"||l.type==="Dôme"?.05:1]];
 if(l.type==="Spot")nums.push(["cone","Angle du cône (°)",1,170,1],["penombre","Pénombre (°)",0,90,1],["rayon_ombre","Douceur des ombres (m)",0,.5,.01]);
 if(l.type==="Rectangle")nums.push(["largeur","Largeur (m)",.05,10,.05],["hauteur","Hauteur (m)",.05,10,.05]);
 if(l.type==="Sphère")nums.push(["rayon","Rayon (m)",.01,1,.01]);
 if(l.type==="Omni")nums.push(["rayon_ombre","Douceur des ombres (m)",0,.5,.01]);
 for(const [k,lab,lo,hi,st] of nums){const v=l[k]??LTYPES[l.type][k]??0; r=row(lab,`<input type="range" min="${lo}" max="${hi}" step="${st}" value="${v}"><input class="num" type="number" step="${st}" value="${v}">`);
  const [rg,nb]=r.querySelectorAll("input"); rg.oninput=()=>{nb.value=rg.value;l[k]=+rg.value;saveL()}; nb.onchange=()=>{rg.value=nb.value;l[k]=+nb.value;saveL()}}
 if(l.type==="Rectangle"||l.type==="Sphère"){r=row("Invisible à la caméra",`<input type="checkbox" ${l.invisible?"checked":""} style="width:auto"><span></span>`); r.querySelector("input").onchange=e=>{l.invisible=e.target.checked;saveL()}}
 if(l.type==="Maillage"){r=row("Surface qui éclaire",`<select>${["",...SURF.map(x=>x.nom)].map(n=>`<option ${n===l.surface?"selected":""}>${n}</option>`).join("")}</select><span></span>`);
  r.querySelector("select").onchange=e=>{l.surface=e.target.value; saveL()};
  r=row("Invisible à la caméra",`<input type="checkbox" ${l.invisible?"checked":""} style="width:auto"><span></span>`); r.querySelector("input").onchange=e=>{l.invisible=e.target.checked;saveL()}}
 if(l.type==="IES"){r=row("Fichier IES",`<div class="file"><span class="fn"></span><button>…</button></div>`); r.style.gridTemplateColumns="150px 1fr";
  const fn=r.querySelector(".fn"); fn.textContent=l.ies?l.ies.split(/[\/]/).pop():"(aucun : ressemble à un spot)";
  r.querySelector("button").onclick=async()=>{const id=Math.random().toString(36).slice(2); await post({action:"choisir",id,filtre:"ies"});
   for(let i=0;i<600;i++){await new Promise(z=>setTimeout(z,500)); const j=await (await fetch("/api/choix?id="+id)).json(); if(j.fini){if(j.path){l.ies=j.path; fn.textContent=j.path.split(/[\/]/).pop(); saveL()} break}}}}
 if(l.type!=="Dôme"&&l.type!=="Maillage"){for(const [k,lab] of [["pos","Position x / y / z (m)"],["cible","Vise vers x / y / z"]]){const v=l[k]||[0,0,0];
  r=row(lab,`<div class="xyz">${v.map(c=>`<input class="num" type="number" step="0.05" value="${(+c).toFixed(2)}">`).join("")}</div><span></span>`);
  r.querySelectorAll("input").forEach((inp,i)=>inp.onchange=()=>{l[k]=l[k]||[0,0,0]; l[k][i]=+inp.value; saveL()})}}
 const bar=document.createElement("div"); bar.style.cssText="display:flex;gap:6px;margin-top:10px";
 bar.innerHTML=`<button id="lDup">Dupliquer</button><button id="lDel">Supprimer</button>`; B.appendChild(bar);
 $("lDup").onclick=()=>{const c=JSON.parse(JSON.stringify(l)); c.nom=l.nom+" copie"; c.pos=[c.pos[0]+.5,c.pos[1],c.pos[2]]; c.cible=[c.cible[0]+.5,c.cible[1],c.cible[2]]; delete c.archicad;
  LUM.push(c); curL=LUM.length-1; saveL(); lumList(); lumForm()};
 $("lDel").onclick=()=>{LUM.splice(curL,1); curL=Math.min(curL,LUM.length-1); saveL(); lumList(); lumForm()};
 const n=document.createElement("div"); n.style.cssText="color:var(--mute);font-size:12px;margin-top:8px";
 n.textContent="Maillage = Mesh Light : toute une surface éclaire (ruban LED, abat-jour, plafond lumineux). Intensités comme V-Ray pour SketchUp (Spot ≈ 1000, Rectangle / Sphère ≈ 30). Pour un intérieur, choisis l'exposition « Intérieur » ou monte l'exposition de la vue.";
 B.appendChild(n); drawPrevL()}
function drawPrevL(){lprev.clear(); const l=LUM[curL]; if(!l||l.actif===false)return; const c=new THREE.Color().setRGB(...(l.couleur||[1,.92,.8]),THREE.SRGBColorSpace);
 let X; const top=new THREE.Vector3(0,2.2,0);
 if(l.type==="Spot"||l.type==="IES"){X=new THREE.SpotLight(c,(l.type==="IES"?800*l.intensite:l.intensite)*0.012,0,Math.min(1.5,(l.cone||60)*Math.PI/360),Math.min(1,(l.penombre||25)/(l.cone||45)),2);
  X.position.copy(top); X.target.position.set(0,0,0); lprev.add(X.target)}
 else if(l.type==="Rectangle"){X=new THREE.PointLight(c,l.intensite*(l.largeur||1)*(l.hauteur||.5)*.5,0,2); X.position.copy(top);
  const g=new THREE.Mesh(new THREE.PlaneGeometry(l.largeur||1,l.hauteur||.5),new THREE.MeshBasicMaterial({color:c,side:THREE.DoubleSide})); g.rotation.x=Math.PI/2; g.position.copy(top); lprev.add(g)}
 else if(l.type==="Dôme"){X=new THREE.HemisphereLight(c,0x222222,l.intensite)}
 else if(l.type==="Maillage"){X=new THREE.PointLight(c,l.intensite*0.6,0,2); X.position.set(0,1.2,-1.1);
  const g=new THREE.Mesh(new THREE.BoxGeometry(1.6,0.06,0.06),new THREE.MeshBasicMaterial({color:c})); g.position.copy(X.position); lprev.add(g)}
 else{X=new THREE.PointLight(c,l.type==="Omni"?l.intensite*0.012:l.intensite*0.35,0,2); X.position.set(0,1.6,0);
  const g=new THREE.Mesh(new THREE.SphereGeometry(Math.max(.04,l.rayon||.08),16,10),new THREE.MeshBasicMaterial({color:c})); g.position.copy(X.position); lprev.add(g)}
 X.castShadow=true; lprev.add(X)}
$("q").oninput=()=>tab==="lum"?lumList():list();
shape("sphere"); load(); pollL(); pollSun();
</script></body></html>
""".replace("__PRESETS__", json.dumps(MAT_PRESETS, ensure_ascii=False)).replace(
    "__LTYPES__", json.dumps(TYPES_LUMIERE, ensure_ascii=False))
CHOIX: dict = {}           # fichiers choisis depuis l'editeur web (id -> chemin)


def _tex(name: str, path: str, size: float, rot: float, color: bool = True, off=(0.0, 0.0)) -> str:
    """TexBitmap avec la projection boite du modele (1 unite UV = 1 m) : taille reelle de l'image en metres."""
    k = 1.0 / max(0.01, float(size or 1.0))
    a = math.radians(float(rot or 0.0))
    c, s = math.cos(a) * k, math.sin(a) * k
    tu, tv = -float(off[0] or 0.0) * k, -float(off[1] or 0.0) * k
    return (f'BitmapBuffer {name}_bmp {{ file="{str(path).replace(chr(92), "/")}"; '
            f'{"color_space=2;" if color else "color_space=0; gamma=1;"} filter_type=1; }}\n'
            f"UVWGenChannel {name}_uvw {{ uvw_channel=1; uvw_transform=Transform(Matrix(Vector({c:.6f},{s:.6f},0),"
            f"Vector({-s:.6f},{c:.6f},0),Vector(0,0,1)),Vector({tu:.5f},{tv:.5f},0)); wrap_u=1; wrap_v=1; }}\n"
            f"TexBitmap {name} {{ bitmap={name}_bmp; uvwgen={name}_uvw; }}\n")


def custom_material_block(mid: str, c: dict) -> str:
    """Materiau V-Ray complet (BRDFVRayMtl) d'apres les reglages de l'editeur."""
    out, p = [], []
    r, g, b = (srgb_to_linear(v) for v in c.get("couleur", (0.75, 0.75, 0.75)))
    size, rot = float(c.get("taille_tex", 1.0)), float(c.get("rotation_tex", 0.0))
    off = (float(c.get("decalage_u", 0.0)), float(c.get("decalage_v", 0.0)))
    dif = f"AColor({r:.4f},{g:.4f},{b:.4f},1)"
    if c.get("tex_couleur") and Path(c["tex_couleur"]).exists():
        out.append(_tex(f"{mid}_dif", c["tex_couleur"], size, rot, off=off))
        dif = f"{mid}_dif"
        br, ct = float(c.get("luminosite", 0.0)), float(c.get("contraste_tex", 1.0))
        sa, hu = float(c.get("saturation", 1.0)), float(c.get("teinte", 0.0))
        if abs(br) > 1e-3 or abs(ct - 1) > 1e-3 or abs(sa - 1) > 1e-3 or abs(hu) > 0.5:
            out.append(f"ColorCorrection {mid}_cc {{ texture_map={mid}_dif; brightness={br * 100:.1f}; contrast={(ct - 1) * 100:.1f}; "
                       f"saturation={(sa - 1) * 100:.1f}; hue_shift={hu:.1f}; }}\n")
            dif = f"{mid}_cc"
    elif c.get("procedural") == "Bruit (variation de couleur)":
        var = float(c.get("proc_variation", 0.3))
        out.append(f"UVWGenObject {mid}_nuvw {{ }}\n"   # coordonnees 3D de l'objet : taille du motif en metres, sans UV
                   f"TexNoiseMax {mid}_noise {{ color1=AColor({r:.4f},{g:.4f},{b:.4f},1); "
                   f"color2=AColor({r * (1 - var):.4f},{g * (1 - var):.4f},{b * (1 - var):.4f},1); "
                   f"size={float(c.get('proc_taille', 0.3)):.3f}; type=1; uvwgen={mid}_nuvw; }}\n")
        dif = f"{mid}_noise"
    if c.get("procedural") == "Usure / saleté dans les angles":
        var = float(c.get("proc_variation", 0.3))
        out.append(f"TexDirt {mid}_dirt {{ white_color={dif}; black_color=AColor({r * (1 - var) * 0.6:.4f},{g * (1 - var) * 0.6:.4f},"
                   f"{b * (1 - var) * 0.6:.4f},1); radius={float(c.get('proc_taille', 0.3)):.3f}; subdivs=8; }}\n")
        dif = f"{mid}_dirt"
    p.append(f"diffuse={dif};")
    rf = float(c.get("reflet", 0.1))
    p += [f"reflect=AColor({rf:.3f},{rf:.3f},{rf:.3f},1);", f"reflect_glossiness={float(c.get('brillance', 0.5)):.3f};",
          "fresnel=1;", f"fresnel_ior={float(c.get('ior', 1.5)):.3f};", f"metalness={float(c.get('metal', 0.0)):.2f};"]
    if c.get("tex_brillance") and Path(c["tex_brillance"]).exists():
        out.append(_tex(f"{mid}_gl", c["tex_brillance"], size, rot, color=False, off=off))
        p.append(f"reflect_glossiness_tex={mid}_gl::out_intensity;")
    if float(c.get("vernis", 0.0)) > 0:
        p += [f"coat_amount={float(c['vernis']):.3f};", f"coat_glossiness={float(c.get('vernis_brillance', 0.9)):.3f};",
              "coat_color=Color(1,1,1);", "coat_ior=1.6;"]
    if float(c.get("velours", 0.0)) > 0:
        sv = float(c["velours"])
        p += [f"sheen_color=Color({sv:.3f},{sv:.3f},{sv:.3f});", "sheen_glossiness=0.8;"]
    rr = float(c.get("refraction", 0.0))
    if rr > 0:
        p += [f"refract=AColor({rr:.3f},{rr:.3f},{rr:.3f},1);", f"refract_ior={float(c.get('ior', 1.5)):.3f};",
              f"refract_glossiness={1.0 - float(c.get('flou_refraction', 0.0)):.3f};", "refract_affect_shadows=1;"]
    op = float(c.get("opacite", 1.0))
    if c.get("tex_opacite") and Path(c["tex_opacite"]).exists():
        out.append(_tex(f"{mid}_op", c["tex_opacite"], size, rot, color=False, off=off))
        p.append(f"opacity={mid}_op::out_intensity;")
    elif op < 1.0:
        p.append(f"opacity={op:.3f};")
    em = float(c.get("emission", 0.0))
    if em > 0:
        er, eg, eb = (srgb_to_linear(v) for v in c.get("couleur_emission", (1.0, 0.9, 0.7)))
        p += [f"self_illumination=Color({er * em:.4f},{eg * em:.4f},{eb * em:.4f});", "self_illumination_gi=1;"]
    out.append(f"BRDFVRayMtl {mid}_brdf {{\n  " + "\n  ".join(p) + "\n}\n")
    base = f"{mid}_brdf"
    if c.get("tex_relief") and Path(c["tex_relief"]).exists():
        normal = c.get("type_relief") == "Normal map"
        out.append(_tex(f"{mid}_bump", c["tex_relief"], size, rot, color=False, off=off))
        amt = float(c.get("relief", 0.5))
        out.append(f"BRDFBump {mid}_brdfb {{ base_brdf={mid}_brdf; "
                   + (f"map_type=1; bump_tex_color={mid}_bump; bump_tex_mult={amt:.3f}; " if normal
                      else f"map_type=0; bump_tex_float={mid}_bump::out_intensity; bump_tex_mult={amt * 0.05:.4f}; ")
                   + "}\n")
        base = f"{mid}_brdfb"
    out.append(f"MtlSingleBRDF {mid} {{ brdf={base}; double_sided=1; }}\n")
    return "".join(out)


def cosmos_textures(folder: str) -> dict:
    """Images d'un materiau Cosmos, rangees par role d'apres leur nom (couleur, brillance, relief, opacite)."""
    roles = {}
    try:
        vrmat = sorted(Path(folder).glob("*.vrmat"), key=_lod)[0]
        fixed, _top = vrmat_for_render(vrmat)
        text = fixed.read_text("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return roles
    for ref in dict.fromkeys(re.findall(r">([^<>]+?\.(?:tx|jpg|jpeg|png|tif|tiff|exr))<", text, flags=re.I)):
        ref = ref.replace("&amp;", "&")
        low = Path(ref).name.lower()
        role = ("tex_relief" if any(w in low for w in ("normal", "bump", "_nrm", "height")) else
                "tex_brillance" if any(w in low for w in ("gloss", "rough", "refl", "spec")) else
                "tex_opacite" if any(w in low for w in ("opac", "alpha", "mask")) else
                "tex_couleur" if any(w in low for w in ("diff", "color", "albedo", "base", "col")) else None)
        if role and role not in roles:
            # la visionneuse n'affiche pas les .tx : on prefere une .jpg/.png de meme nom si elle existe
            alt = next((Path(ref).with_suffix(e_) for e_ in (".jpg", ".png") if Path(ref).with_suffix(e_).exists()), None)
            roles[role] = str(alt or ref)
    if "tex_relief" in roles and "normal" in Path(roles["tex_relief"]).name.lower():
        roles["type_relief"] = "Normal map"
    return roles


def material_editor(root, state: dict, key: str, names: list, colors: dict, on_done) -> None:
    """Fenetre « Modifier le materiau » (couleur, textures, reflets, brillance, opacite, relief, verre, lumiere)."""
    import tkinter as tk
    from tkinter import colorchooser, filedialog, ttk
    store = state.setdefault("mat_perso", {}).setdefault(key, {})
    first = names[0]
    c0 = dict(store.get(first) or {})
    c0.setdefault("couleur", tuple(colors.get(first, (0.75, 0.75, 0.75))))
    win = tk.Toplevel(root)
    win.title("Modifier le matériau" + (f" — {first}" if len(names) == 1 else f" — {len(names)} surfaces"))
    win.attributes("-topmost", True)
    win.transient(root)
    f = ttk.Frame(win, padding=12)
    f.grid(sticky="nsew")
    v: dict = {}

    def num(name, default):
        v[name] = tk.DoubleVar(value=float(c0.get(name, default)))
        return v[name]

    def row_slider(parent, r, label, name, default, lo, hi, hint=""):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w")
        var = num(name, default)
        ttk.Scale(parent, from_=lo, to=hi, variable=var, length=210).grid(row=r, column=1, sticky="w", padx=6)
        lab = ttk.Label(parent, width=6)
        lab.grid(row=r, column=2, sticky="w")
        var.trace_add("write", lambda *_: lab.configure(text=f"{var.get():.2f}"))
        lab.configure(text=f"{var.get():.2f}")
        if hint:
            ttk.Label(parent, text=hint, foreground="#888").grid(row=r, column=3, sticky="w")

    def row_file(parent, r, label, name):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w")
        v[name] = tk.StringVar(value=c0.get(name, ""))
        ttk.Entry(parent, textvariable=v[name], width=34).grid(row=r, column=1, columnspan=2, sticky="w", padx=6)

        def pick():
            p_ = filedialog.askopenfilename(parent=win, title=label, filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.exr *.tx *.bmp"), ("Tous", "*.*")])
            if p_:
                v[name].set(p_)
        ttk.Button(parent, text="…", width=3, command=pick).grid(row=r, column=3, sticky="w")
        ttk.Button(parent, text="✕", width=3, command=lambda: v[name].set("")).grid(row=r, column=4, sticky="w")

    # preset
    pr = ttk.LabelFrame(f, text="Point de départ (préréglage)", padding=8)
    pr.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    preset = tk.StringVar(value="")
    pbox = ttk.Combobox(pr, values=list(MAT_PRESETS), textvariable=preset, state="readonly", width=32)
    pbox.grid(row=0, column=0, sticky="w")

    def apply_preset(_e=None):
        for k_, val in MAT_PRESETS[preset.get()].items():
            if k_ in v:
                v[k_].set(val)
    pbox.bind("<<ComboboxSelected>>", apply_preset)

    # couleur
    co = ttk.LabelFrame(f, text="Couleur / texture", padding=8)
    co.grid(row=1, column=0, rowspan=3, sticky="new", pady=(0, 8), padx=(0, 8))
    color = {"rgb": tuple(c0["couleur"])}
    sw = tk.Label(co, width=6, background="#%02x%02x%02x" % tuple(int(x * 255) for x in color["rgb"]), relief="groove")
    sw.grid(row=0, column=1, sticky="w", padx=6)

    def choose_color():
        res = colorchooser.askcolor(color=sw.cget("background"), parent=win, title="Couleur")
        if res and res[0]:
            color["rgb"] = tuple(x / 255 for x in res[0])
            sw.configure(background=res[1])
            live()
    ttk.Label(co, text="Couleur").grid(row=0, column=0, sticky="w")
    ttk.Button(co, text="Choisir…", command=choose_color).grid(row=0, column=2, sticky="w")
    row_file(co, 1, "Texture couleur", "tex_couleur")
    ttk.Label(co, text="Taille de l'image (m)").grid(row=2, column=0, sticky="w")
    ttk.Spinbox(co, from_=0.05, to=50, increment=0.1, textvariable=num("taille_tex", 1.0), width=6).grid(row=2, column=1, sticky="w", padx=6)
    row_slider(co, 3, "Rotation de la texture", "rotation_tex", 0.0, 0, 180)
    row_slider(co, 4, "Décalage horizontal (m)", "decalage_u", 0.0, -2, 2)
    row_slider(co, 5, "Décalage vertical (m)", "decalage_v", 0.0, -2, 2)
    row_slider(co, 6, "Luminosité", "luminosite", 0.0, -1, 1, "corrige l'image")
    row_slider(co, 7, "Contraste", "contraste_tex", 1.0, 0, 2)
    row_slider(co, 8, "Saturation", "saturation", 1.0, 0, 2)
    row_slider(co, 9, "Teinte (°)", "teinte", 0.0, -180, 180)

    # procedural : quand on n'a pas d'image
    pc = ttk.LabelFrame(f, text="Texture sans image (procédurale)", padding=8)
    pc.grid(row=4, column=0, sticky="new", pady=(0, 8), padx=(0, 8))
    v["procedural"] = tk.StringVar(value=c0.get("procedural", "Aucune"))
    ttk.Combobox(pc, values=("Aucune", "Bruit (variation de couleur)", "Usure / saleté dans les angles"),
                 textvariable=v["procedural"], state="readonly", width=32).grid(row=0, column=0, columnspan=2, sticky="w")
    row_slider(pc, 1, "Variation / intensité", "proc_variation", 0.3, 0, 1)
    row_slider(pc, 2, "Taille du motif (m)", "proc_taille", 0.3, 0.02, 3)
    ttk.Label(pc, foreground="#777", wraplength=420, justify="left",
              text="Bruit : enduit, béton, pierre sans texture. Usure : salit les coins et les jonctions (aspect vécu). "
                   "Visible au rendu V-Ray.").grid(row=3, column=0, columnspan=4, sticky="w")
    cosmos_src = ((state.get("cosmos") or {}).get(first) or {}).get("dossier")
    if cosmos_src:
        def from_cosmos():
            for role, path in cosmos_textures(cosmos_src).items():
                if role in v:
                    v[role].set(path)
            live()
        ttk.Button(co, text="⇩ Reprendre les textures du matériau Cosmos", command=from_cosmos).grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(6, 0))

    # reflets
    rf = ttk.LabelFrame(f, text="Reflets", padding=8)
    rf.grid(row=1, column=1, sticky="ew", pady=(0, 8))
    row_slider(rf, 0, "Réflexion", "reflet", 0.1, 0, 1, "0 = aucun reflet")
    row_slider(rf, 1, "Brillance", "brillance", 0.5, 0, 1, "1 = miroir net")
    row_slider(rf, 2, "Métal", "metal", 0.0, 0, 1)
    row_slider(rf, 3, "Indice (IOR)", "ior", 1.5, 1.0, 3.0)
    row_file(rf, 4, "Texture de brillance", "tex_brillance")

    # transparence
    tr = ttk.LabelFrame(f, text="Transparence", padding=8)
    tr.grid(row=2, column=1, sticky="ew", pady=(0, 8))
    row_slider(tr, 0, "Opacité", "opacite", 1.0, 0, 1, "0 = invisible")
    row_file(tr, 1, "Texture d'opacité", "tex_opacite")
    row_slider(tr, 2, "Réfraction (verre)", "refraction", 0.0, 0, 1)
    row_slider(tr, 3, "Flou de réfraction", "flou_refraction", 0.0, 0, 1, "verre dépoli")

    # relief + lumiere
    re_ = ttk.LabelFrame(f, text="Relief et lumière", padding=8)
    re_.grid(row=3, column=1, sticky="ew", pady=(0, 8))
    row_file(re_, 0, "Texture de relief", "tex_relief")
    v["type_relief"] = tk.StringVar(value=c0.get("type_relief", "Bump (gris)"))
    ttk.Combobox(re_, values=("Bump (gris)", "Normal map"), textvariable=v["type_relief"], state="readonly", width=14).grid(
        row=1, column=1, sticky="w", padx=6)
    row_slider(re_, 2, "Intensité du relief", "relief", 0.5, 0, 2)
    row_slider(re_, 3, "Émission (lumineux)", "emission", 0.0, 0, 20, "lampe, écran, néon")

    def current() -> dict:
        c = {"couleur": list(color["rgb"])}
        for k_, var in v.items():
            try:
                c[k_] = var.get()
            except (ValueError, tk.TclError):
                pass
        return c

    def live(*_):                               # apercu en direct dans la visionneuse 3D
        LIVE_MAT.update(surfaces=list(names), c=current(), tous=dict(store), rev=LIVE_MAT["rev"] + 1)

    def live_end():
        LIVE_MAT.update(surfaces=[n for n in names if n not in store], c={}, tous=dict(store), rev=LIVE_MAT["rev"] + 1)

    for var in v.values():
        var.trace_add("write", live)
    sw.bind("<Configure>", live)
    live()

    def ok():
        c = current()
        for n in names:
            store[n] = dict(c)
        live_end()
        on_done(f"Matériau personnalisé appliqué à {len(names)} surface(s) : visible au prochain rendu.")
        win.destroy()

    def reset():
        for n in names:
            store.pop(n, None)
        live_end()
        on_done("Matériau personnalisé retiré : la surface reprend sa couleur Archicad (ou son matériau Cosmos).")
        win.destroy()

    def cancel():
        live_end()
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", cancel)
    bar = ttk.Frame(f)
    bar.grid(row=8, column=0, columnspan=2, sticky="ew")
    ttk.Button(bar, text="Revenir au matériau d'origine", command=reset).grid(row=0, column=0)
    ttk.Button(bar, text="Annuler", command=cancel).grid(row=0, column=1, padx=(80, 4))
    ttk.Button(bar, text="OK", command=ok).grid(row=0, column=2)
    tip = ttk.Frame(f)
    tip.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Label(tip, foreground="#2b5d8a", wraplength=420, justify="left",
              text="Aperçu en direct : ouvre la visionneuse 3D, chaque réglage s'y voit tout de suite sur la surface "
                   "(images .jpg / .png ; les .tx et .exr ne s'affichent qu'au rendu).").grid(row=0, column=0, sticky="w")
    if state.get("_open_viewer"):
        ttk.Button(tip, text="🧭 Ouvrir la visionneuse", command=state["_open_viewer"]).grid(row=0, column=1, padx=6)


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

    def edit_material():
        names = [n for n in tree.selection()]
        if not names:
            msg.set("Choisis d'abord une ou plusieurs surfaces à gauche, puis « 🎨 Modifier le matériau ».")
            return
        cols, _ = project_surfaces(state, key)

        def done(text):
            save_settings(state)
            msg.set(text)
            try:
                load_surfaces(True)
            except Exception:  # noqa: BLE001
                pass
        material_editor(root, state, key, names, cols, done)

    ttk.Button(acts, text="🎨 Modifier le matériau…", command=edit_material).grid(row=0, column=6, padx=(12, 3))
    ttk.Button(acts, text="🎛 Éditeur V-Ray (aperçu)", command=lambda: state.get("_open_editor") and state["_open_editor"]()).grid(
        row=0, column=7, padx=3)
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
