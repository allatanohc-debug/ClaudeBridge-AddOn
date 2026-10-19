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
import math
import os
import re
import subprocess
import sys
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

VERSION = "19/10 - v1.3 (étape 1)"
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
    (("bois", "parquet", "wood", "iroko", "teck", "chêne", "chene"), {"reflect": 0.35, "gloss": 0.62}),
    (("cuir", "leather", "plastique", "plastic", "pvc"), {"reflect": 0.35, "gloss": 0.6}),
    (("béton", "beton", "concrete", "ciment", "enduit", "plâtre", "platre", "peint", "paint", "crépi", "crepi"),
     {"reflect": 0.12, "gloss": 0.45}),
    (("brique", "terre", "tuile", "brick", "pierre", "stone", "latérite", "laterite"), {"reflect": 0.08, "gloss": 0.4}),
    (("herbe", "gazon", "grass", "végét", "veget", "feuill", "leaf", "arbre", "tree"), {"reflect": 0.05, "gloss": 0.35}),
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


def ifc_meshes(path: Path, report=print) -> tuple[dict, dict, tuple]:
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
            for m, nm in zip(g.materials, names):
                if nm not in mats:
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
            out.update(params)
            break
    return out


def write_geometry(path: Path, meshes: dict, mats: dict, offset: tuple, library: dict) -> int:
    tris = 0
    with path.open("w", encoding="utf-8") as out:
        out.write("// Geometrie Archicad -> V-Ray (V-Ray Link)\n")
        for k, (name, (verts, faces)) in enumerate(meshes.items()):
            if not faces:
                continue
            m = guess_material(name, mats.get(name, {}), library)
            mid = f"m{k}"
            r, g, b = (srgb_to_linear(c) for c in m["rgb"])
            out.write(f"// surface : {name}\n")
            if m.get("type") == "verre":
                out.write(f"BRDFVRayMtl {mid}_brdf {{\n  diffuse=AColor(0.02,0.02,0.02,1);\n  reflect=AColor(1,1,1,1);\n"
                          f"  reflect_glossiness=1;\n  fresnel=1;\n  refract=AColor({0.9 * max(r, .6):.4f},{0.9 * max(g, .6):.4f},"
                          f"{0.9 * max(b, .6):.4f},1);\n  refract_ior=1.5;\n  refract_affect_shadows=1;\n}}\n")
            elif m.get("type") == "eau":
                out.write(f"BRDFVRayMtl {mid}_brdf {{\n  diffuse=AColor(0.01,0.03,0.04,1);\n  reflect=AColor(1,1,1,1);\n"
                          f"  reflect_glossiness=0.98;\n  fresnel=1;\n  refract=AColor(0.85,0.95,0.95,1);\n  refract_ior=1.33;\n"
                          f"  fog_color=Color(0.6,0.85,0.85);\n  fog_mult=0.3;\n  refract_affect_shadows=1;\n}}\n")
            else:
                rf = float(m.get("reflect", 0.15))
                out.write(f"BRDFVRayMtl {mid}_brdf {{\n  diffuse=AColor({r:.4f},{g:.4f},{b:.4f},1);\n"
                          f"  reflect=AColor({rf:.3f},{rf:.3f},{rf:.3f},1);\n  reflect_glossiness={float(m.get('gloss', .5)):.3f};\n"
                          f"  fresnel=1;\n  metalness={float(m.get('metal', 0.0)):.2f};\n}}\n")
            out.write(f"MtlSingleBRDF {mid} {{\n  brdf={mid}_brdf;\n}}\n")
            out.write(f"GeomStaticMesh {mid}_geo {{\n  vertices=ListVector(")
            ox, oy, oz = offset
            for i in range(0, len(verts), 2000):
                chunk = ",".join(f"Vector({x + ox:.4f},{y + oy:.4f},{z + oz:.4f})" for x, y, z in verts[i:i + 2000])
                out.write(("," if i else "") + chunk)
            out.write(");\n  faces=ListInt(")
            for i in range(0, len(faces), 6000):
                out.write(("," if i else "") + ",".join(str(c) for c in faces[i:i + 6000]))
            out.write(");\n}\n")
            out.write(f"Node {mid}_node {{\n  transform=Transform(Matrix(Vector(1,0,0),Vector(0,1,0),Vector(0,0,1)),"
                      f"Vector(0,0,0));\n  geometry={mid}_geo;\n  material={mid};\n}}\n")
            tris += len(faces) // 3
    return tris


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
    s.append(f'#include "{geometry.name}"')
    path.write_text("\n".join(s) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- etat ---
def load_settings() -> dict:
    saved = load_json(REGLAGES)
    today = dt.date.today()
    opts = {"qualite": "Haute", "taille": "1920 × 1080 (16:9)", "ambiance": "Extérieur jour", "sol": "Gazon",
            "jour": today.day, "mois": today.month, "heure": 16.5, "fuseau": 0.0, "verticales": True,
            "trouble": 3.0, "taille_soleil": 3.0, "lieu": "Abidjan", "reutiliser": False, "oeil": 1.6, "cible": 1.4, "focale": 24}
    opts.update({k: v for k, v in (saved.get("options") or {}).items() if k in opts})
    return {"options": opts, "cameras": saved.get("cameras") or {}, "bibliotheque": saved.get("bibliotheque") or {},
            "vray": saved.get("vray", ""), "coches": saved.get("coches") or {}}


def save_settings(state: dict) -> None:
    save_json(REGLAGES, {k: state[k] for k in ("options", "cameras", "bibliotheque", "vray", "coches")})


def prepare_geometry(state: dict, proj: dict, report=print) -> tuple[Path, float, str]:
    """Exporte et convertit le modele (ou reprend la conversion precedente). Renvoie (fichier, sol z, resume)."""
    d = data_dir() / safe(proj["name"])
    d.mkdir(parents=True, exist_ok=True)
    geo, meta = d / "geometrie.vrscene", d / "geometrie.json"
    if state["options"].get("reutiliser") and geo.exists() and meta.exists():
        import json
        info = json.loads(meta.read_text(encoding="utf-8"))
        return geo, float(info.get("sol", 0.0)), "géométrie reprise de l'export précédent"
    ensure_ifcopenshell(report)
    report("Export IFC depuis Archicad...")
    ifc = d / "modele.ifc"
    export_ifc(ifc)
    report("Conversion de la géométrie pour V-Ray...")
    meshes, mats, (lo, hi) = ifc_meshes(ifc, report)
    if not meshes:
        raise RuntimeError("Aucune géométrie dans l'export IFC (calques masqués ?).")
    offset = (0.0, 0.0, 0.0)
    box = archicad_bbox()
    if box:                                  # IFC decale par rapport a Archicad (point de base, site) : on recale
        dx = [box[0][k] - lo[k] for k in range(3)]
        if any(abs(c) > 0.05 for c in dx):
            offset = tuple(dx)
    tris = write_geometry(geo, meshes, mats, offset, state["bibliotheque"])
    ground = (box[0][2] if box else lo[2] + offset[2])
    import json
    meta.write_text(json.dumps({"sol": ground, "surfaces": sorted(meshes), "triangles": tris}), encoding="utf-8")
    state.setdefault("surfaces", sorted(meshes))
    listing = ", ".join(f"{n} ({'/'.join(str(int(c * 255)) for c in mats.get(n, {}).get('rgb', (0, 0, 0)))})"
                        for n in sorted(meshes)[:25])
    return geo, ground, (f"{len(meshes)} surfaces, {tris:,} triangles".replace(",", " ") + "\nSurfaces : " + listing)


def launch(vray: str, scene: Path, image: Path, show: bool = True) -> None:
    args = [vray, f"-sceneFile={scene}", f"-imgFile={image}", f"-display={1 if show else 0}", "-autoClose=0"]
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(args, cwd=str(scene.parent), creationflags=flags)


def launch_batch(vray: str, jobs: list[tuple[Path, Path]], folder: Path) -> Path:
    bat = jobs[0][0].parent / "rendre_les_vues.bat"
    lines = ["@echo off", "chcp 65001 >nul", "title V-Ray Link - rendu des vues"]
    for k, (scene, image) in enumerate(jobs, 1):
        lines.append(f'echo Vue {k}/{len(jobs)} : {image.stem}')
        lines.append(f'"{vray}" -sceneFile="{scene}" -imgFile="{image}" -display=1 -autoClose=1')
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
    outer = ttk.Frame(root, padding=12)
    outer.grid()
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

    report = tk.Text(outer, width=78, height=5, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=5, column=0, pady=(8, 0))
    ttk.Label(outer, textvariable=status, foreground="#555", wraplength=560).grid(row=6, column=0, sticky="w")

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
                  f"à {int(opts['heure']):02d}h{int(round(opts['heure'] % 1 * 60)) % 60:02d}."])
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
    buttons.grid(row=7, column=0, sticky="e", pady=(8, 0))
    ttk.Button(buttons, text="Ouvrir le dossier des rendus", command=open_folder).grid(row=0, column=0, padx=3)
    ttk.Button(buttons, text="Rendre les vues cochées", command=lambda: render(
        [n for n, v in cam_vars.items() if v.get()])).grid(row=0, column=1, padx=3)
    ttk.Button(buttons, text="Rendre cette vue", command=lambda: render([current.get()] if current.get() else [])
               ).grid(row=0, column=2, padx=3)
    root.protocol("WM_DELETE_WINDOW", lambda: (collect(), root.destroy()))
    root.after(100, compute_autos)
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
