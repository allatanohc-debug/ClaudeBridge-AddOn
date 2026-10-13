"""
Calques magiques - Photoshop 2024 (Windows)
===========================================
Combine deux idees de Canva dans Photoshop :
  - « Grab Text » : les textes ecrits dans l'image sont lus, effaces proprement (Remplissage
    d'apres le contenu) et recrees en calques de texte modifiables (meme place, taille, couleur).
  - « Magic Layers » : l'image plate est separee en calques : sujet detoure, ciel, formes et
    aplats de couleur (flyers), et un fond reconstruit dessous.

Lancement : double-clic sur « Lancer Calques magiques.bat » (ou menu Fichier > Scripts de Photoshop
si « Calques magiques.jsx » est installe). Photoshop doit etre ouvert avec une image.
Tout se fait sur ton ordinateur (lecture du texte par Windows, hors ligne).

Le document d'origine n'est jamais detruit : ses calques sont seulement masques, et tout le
travail est ajoute au-dessus (groupe « Calques magiques »). Ctrl+Z / Historique fonctionnent.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
import time

VERSION = "13/10 - 1re version"
HERE = os.path.dirname(os.path.abspath(__file__))
REGLAGES = os.path.join(HERE, "calques_magiques_reglages.json")

TYPES = ("Flyer / affiche", "Photo", "Rendu d'architecture")
# options par defaut selon le type d'image
DEFAUTS = {
    "Flyer / affiche": {"textes": True, "effacer": True, "sujet": True, "ciel": False, "formes": True},
    "Photo": {"textes": True, "effacer": True, "sujet": True, "ciel": True, "formes": False},
    "Rendu d'architecture": {"textes": True, "effacer": True, "sujet": False, "ciel": True, "formes": False},
}
POLICE_NORMALE = "ArialMT"
POLICE_GRASSE = "Arial-BoldMT"
OCR_MAX = 2600                     # taille max (px) de l'image envoyee a la lecture de texte de Windows


# ---------------------------------------------------------------- dependances ---
PAQUETS = {"numpy": "numpy", "PIL": "pillow", "scipy": "scipy", "win32com": "pywin32"}


def missing_packages() -> list[str]:
    out = []
    for module, package in PAQUETS.items():
        try:
            __import__(module)
        except ImportError:
            out.append(package)
    if os.name == "nt":
        try:
            import winsdk.windows.media.ocr  # noqa: F401
        except ImportError:
            try:
                import winrt.windows.media.ocr  # noqa: F401
            except ImportError:
                out.append("winsdk")
    return out


def install(packages: list[str]) -> str:
    cmd = [sys.executable, "-m", "pip", "install", "--user", *packages]
    run = subprocess.run(cmd, capture_output=True, text=True)
    if run.returncode != 0 and "winsdk" in packages:
        # Python trop recent pour winsdk : paquets winrt separes
        alt = [p for p in packages if p != "winsdk"] + [
            "winrt-runtime", "winrt-Windows.Media.Ocr", "winrt-Windows.Graphics.Imaging",
            "winrt-Windows.Storage", "winrt-Windows.Storage.Streams", "winrt-Windows.Globalization",
            "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections"]
        run = subprocess.run([sys.executable, "-m", "pip", "install", "--user", *alt], capture_output=True, text=True)
    return run.stdout[-1500:] + run.stderr[-1500:]


def load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


# ------------------------------------------------------------------ OCR Windows ---
def _ocr_modules():
    try:
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except ImportError:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import FileAccessMode, StorageFile
    return Language, BitmapDecoder, OcrEngine, FileAccessMode, StorageFile


def ocr_languages() -> list[tuple[str, str]]:
    """(code, nom) des langues de lecture installees dans Windows."""
    try:
        _L, _B, OcrEngine, _F, _S = _ocr_modules()
        return [(l.language_tag, l.display_name) for l in OcrEngine.available_recognizer_languages]
    except Exception:  # noqa: BLE001
        return []


def ocr_image(path: str, langs: list[str]) -> list[dict]:
    """Lit le texte de l'image. Renvoie des lignes : {texte, mots:[{texte, x, y, w, h}]} en pixels de l'image
    envoyee. langs vide = langues de Windows ; plusieurs langues = on garde la lecture la plus complete."""
    Language, BitmapDecoder, OcrEngine, FileAccessMode, StorageFile = _ocr_modules()

    async def run(lang):
        f = await StorageFile.get_file_from_path_async(path)
        stream = await f.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_language(Language(lang)) if lang else \
            OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return []
        result = await engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            words = [{"texte": w.text, "x": w.bounding_rect.x, "y": w.bounding_rect.y,
                      "w": w.bounding_rect.width, "h": w.bounding_rect.height} for w in line.words]
            if words:
                lines.append({"texte": line.text, "mots": words})
        return lines

    best: list = []
    for lang in (langs or [None]):
        try:
            lines = asyncio.run(run(lang))
        except Exception:  # noqa: BLE001
            continue
        score = sum(len(l["texte"]) for l in lines)
        if score > sum(len(l["texte"]) for l in best):
            best = lines
    return best


# -------------------------------------------------------------- analyse pixels ---
def load_rgb(path: str):
    import numpy as np
    from PIL import Image
    img = Image.open(path).convert("RGBA")
    arr = np.asarray(img).astype(np.float32)
    rgb, alpha = arr[..., :3], arr[..., 3:4] / 255.0
    return rgb * alpha + 255.0 * (1 - alpha)                      # transparent -> blanc


def ocr_input(src: str, dst: str) -> float:
    """Copie de l'image pour la lecture (reduite si trop grande). Renvoie le facteur image -> copie."""
    from PIL import Image
    img = Image.open(src).convert("RGB")
    k = min(1.0, OCR_MAX / max(img.size))
    if k < 1.0:
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    img.save(dst)
    return k


def scale_lines(lines: list[dict], k: float) -> list[dict]:
    if abs(k - 1.0) < 1e-6:
        return lines
    out = []
    for line in lines:
        out.append({"texte": line["texte"], "mots": [
            {"texte": w["texte"], "x": w["x"] / k, "y": w["y"] / k, "w": w["w"] / k, "h": w["h"] / k}
            for w in line["mots"]]})
    return out


DESCENDANTES = set("gjpqyQ,;()[]{}|")


def analyse_line(rgb, line: dict) -> dict | None:
    """Couleur du texte, couleur du fond, gras, taille et ligne de base d'une ligne lue."""
    import numpy as np
    H, W = rgb.shape[:2]
    x1 = max(0, int(min(w["x"] for w in line["mots"])))
    y1 = max(0, int(min(w["y"] for w in line["mots"])))
    x2 = min(W, int(math.ceil(max(w["x"] + w["w"] for w in line["mots"]))))
    y2 = min(H, int(math.ceil(max(w["y"] + w["h"] for w in line["mots"]))))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    h = y2 - y1
    pad = max(2, int(h * 0.25))
    ox1, oy1, ox2, oy2 = max(0, x1 - pad), max(0, y1 - pad), min(W, x2 + pad), min(H, y2 + pad)
    outer = rgb[oy1:oy2, ox1:ox2].reshape(-1, 3)
    inner_mask = np.zeros((oy2 - oy1, ox2 - ox1), bool)
    inner_mask[y1 - oy1:y2 - oy1, x1 - ox1:x2 - ox1] = True
    ring = outer[~inner_mask.reshape(-1)]
    background = np.median(ring, axis=0) if len(ring) else np.array([255.0, 255.0, 255.0])
    inside = rgb[y1:y2, x1:x2].reshape(-1, 3)
    dist = np.linalg.norm(inside - background, axis=1)
    threshold = max(40.0, float(np.percentile(dist, 50)))
    ink = inside[dist > threshold]
    if len(ink) < 5:
        ink = inside[dist >= np.percentile(dist, 80)]
    # couleur du texte : les pixels les plus eloignes du fond (le coeur des lettres, sans l'anticrenelage)
    far = np.linalg.norm(ink - background, axis=1)
    core = ink[far >= np.percentile(far, 60)] if len(ink) > 10 else ink
    color = np.median(core, axis=0)
    # ligne de base et haut des lettres d'apres l'encre : la plupart des lettres posent sur la ligne de base,
    # les jambages (g, p, J...) sont minoritaires
    m = (dist > threshold).reshape(h, x2 - x1)
    cols = np.where(m.any(axis=0))[0]
    text = line["texte"]
    if len(cols) >= 3:
        bottoms = np.array([np.max(np.where(m[:, c])[0]) for c in cols])
        tops = np.array([np.min(np.where(m[:, c])[0]) for c in cols])
        base = y1 + float(np.median(bottoms)) + 1
        top = y1 + float(np.percentile(tops, 10))
    else:
        has_desc = any(c in DESCENDANTES for c in text)
        base, top = y1 + h * (0.78 if has_desc else 1.0), y1
    body = max(2.0, base - top)                                    # hauteur des capitales / hampes
    size = body / 0.72                                             # taille de police (em) en px
    stroke = _stroke(inside, dist, threshold, x2 - x1, h) * h      # epaisseur des traits en px
    return {
        "texte": text, "x": x1, "y": y1, "w": x2 - x1, "h": h,
        "ligne_base": base, "taille": size, "haut": top,
        "couleur": [int(round(c)) for c in color], "fond": [int(round(c)) for c in background],
        "gras": stroke / size > 0.125,
        "mots": [{"x": w["x"], "y": w["y"], "w": w["w"], "h": w["h"]} for w in line["mots"]],
    }


def _stroke(inside, dist, threshold, w, h) -> float:
    """Epaisseur moyenne des traits / hauteur (indice de gras)."""
    import numpy as np
    m = (dist > threshold).reshape(h, w) if h * w == len(dist) else None
    if m is None or not m.any():
        return 0.0
    runs = []
    for row in m[:: max(1, h // 12)]:
        r = np.diff(np.concatenate(([0], row.astype(int), [0])))
        starts, ends = np.where(r == 1)[0], np.where(r == -1)[0]
        runs += list(ends - starts)
    return float(np.median(runs)) / h if runs else 0.0


def text_mask(rgb, lines: list[dict], path: str) -> int:
    """Masque des lettres (blanc opaque sur transparent), un peu elargi, pour effacer le texte."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    H, W = rgb.shape[:2]
    mask = np.zeros((H, W), bool)
    for line in lines:
        bg = np.array(line["fond"], np.float32)
        tc = np.array(line["couleur"], np.float32)
        for w in line["mots"]:
            pad = max(2, int(w["h"] * 0.12))
            x1, y1 = max(0, int(w["x"]) - pad), max(0, int(w["y"]) - pad)
            x2, y2 = min(W, int(w["x"] + w["w"]) + pad), min(H, int(w["y"] + w["h"]) + pad)
            if x2 <= x1 or y2 <= y1:
                continue
            block = rgb[y1:y2, x1:x2]
            d_bg = np.linalg.norm(block - bg, axis=2)
            d_tc = np.linalg.norm(block - tc, axis=2)
            gap = float(np.linalg.norm(tc - bg))
            letters = d_tc < max(35.0, 0.45 * gap)                 # pixels de la couleur du texte
            letters &= d_tc < d_bg
            grow = max(2, int(w["h"] * 0.08))
            letters = ndimage.binary_dilation(letters, iterations=grow)
            mask[y1:y2, x1:x2] |= letters
    out = np.zeros((H, W, 4), np.uint8)
    out[mask] = (255, 255, 255, 255)
    Image.fromarray(out, "RGBA").save(path)
    return int(mask.sum())


def color_shapes(rgb, colors: int, folder: str, min_part: float = 0.004, max_layers: int = 16) -> list[dict]:
    """Aplats et formes d'un visuel graphique : une couleur dominante = le fond, chaque forme d'une
    autre couleur (assez grande) devient un calque. Renvoie [{fichier, nom}] (PNG plein format)."""
    import numpy as np
    from PIL import Image, ImageFilter
    from scipy import ndimage
    H, W = rgb.shape[:2]
    img = Image.fromarray(rgb.astype(np.uint8), "RGB").filter(ImageFilter.MedianFilter(5))
    small_k = min(1.0, 900 / max(W, H))
    small = img.resize((max(1, int(W * small_k)), max(1, int(H * small_k))), Image.NEAREST) if small_k < 1 else img
    pal_img = small.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    palette = np.array(pal_img.getpalette()[: colors * 3], np.float32).reshape(-1, 3)
    # appartenance de chaque pixel pleine taille a la couleur la plus proche
    full = np.asarray(img).astype(np.float32)
    labels = np.zeros((H, W), np.int16)
    best = np.full((H, W), np.inf, np.float32)
    for i, c in enumerate(palette):
        d = np.sum((full - c) ** 2, axis=2)
        closer = d < best
        labels[closer], best[closer] = i, d[closer]
    counts = np.bincount(labels.ravel(), minlength=len(palette))
    background = int(np.argmax(counts))
    shapes = []
    for i, c in enumerate(palette):
        if i == background or counts[i] < min_part * H * W:
            continue
        comp, n = ndimage.label(ndimage.binary_opening(labels == i, iterations=1))
        if n == 0:
            continue
        sizes = ndimage.sum(np.ones_like(comp), comp, index=range(1, n + 1))
        for j, size in enumerate(sizes, start=1):
            if size < min_part * H * W:
                continue
            part = ndimage.binary_closing(comp == j, iterations=2)
            shapes.append((float(size), i, part, c))
    shapes.sort(key=lambda s: -s[0])
    out = []
    for k, (size, i, part, c) in enumerate(shapes[:max_layers]):
        rgba = np.zeros((H, W, 4), np.uint8)
        rgba[..., :3] = rgb.astype(np.uint8)
        rgba[..., 3] = np.where(part, 255, 0)
        path = os.path.join(folder, f"forme_{k}.png")
        Image.fromarray(rgba, "RGBA").save(path)
        hexa = "#%02X%02X%02X" % tuple(int(v) for v in c)
        out.append({"fichier": path, "nom": f"Forme {k + 1} ({hexa})"})
    return out


# ----------------------------------------------------------------- Photoshop ---
class Photoshop:
    def __init__(self):
        import win32com.client
        self.app = win32com.client.Dispatch("Photoshop.Application")

    def run(self, code: str) -> str:
        return str(self.app.DoJavaScript(wrap(code)) or "")


def wrap(code: str) -> str:
    """Bibliotheque + code, sans boite de dialogue Photoshop pendant le traitement."""
    body = code.strip().rstrip(";")
    return (JSX_LIB + "\nvar __dd = app.displayDialogs; app.displayDialogs = DialogModes.NO; var __r;\n"
            "try { __r = " + body + "; } finally { app.displayDialogs = __dd; }\n__r;")


def js(value) -> str:
    """Valeur Python -> litteral JavaScript (ExtendScript n'a pas JSON)."""
    return json.dumps(value, ensure_ascii=True)


def jpath(path: str) -> str:
    return js(path.replace("\\", "/"))


JSX_LIB = r"""
function cTID(s){return charIDToTypeID(s);} function sTID(s){return stringIDToTypeID(s);}
function selectionFromLayer(layer){
  app.activeDocument.activeLayer = layer;
  var d = new ActionDescriptor(), r = new ActionReference(), r2 = new ActionReference();
  r.putProperty(cTID("Chnl"), cTID("fsel")); d.putReference(cTID("null"), r);
  r2.putEnumerated(cTID("Chnl"), cTID("Chnl"), cTID("Trsp")); d.putReference(cTID("T   "), r2);
  executeAction(cTID("setd"), d, DialogModes.NO);
}
function contentAwareFill(){
  var d = new ActionDescriptor();
  d.putEnumerated(cTID("Usng"), cTID("FlCn"), sTID("contentAware"));
  d.putUnitDouble(cTID("Opct"), cTID("#Prc"), 100);
  d.putEnumerated(cTID("Md  "), cTID("BlnM"), cTID("Nrml"));
  executeAction(cTID("Fl  "), d, DialogModes.NO);
}
function placePng(doc, path, name, parent){
  var src = app.open(new File(path));
  var lyr = src.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
  src.close(SaveOptions.DONOTSAVECHANGES);
  app.activeDocument = doc;
  lyr.name = name;
  if (parent) lyr.move(parent, ElementPlacement.INSIDE);
  return lyr;
}
function findSet(doc, name){
  for (var i = 0; i < doc.layerSets.length; i++) if (doc.layerSets[i].name == name) return doc.layerSets[i];
  return null;
}
function findLayer(parent, name){
  for (var i = 0; i < parent.artLayers.length; i++) if (parent.artLayers[i].name == name) return parent.artLayers[i];
  return null;
}
function pxUnits(){
  var old = [app.preferences.rulerUnits, app.preferences.typeUnits];
  app.preferences.rulerUnits = Units.PIXELS; app.preferences.typeUnits = TypeUnits.PIXELS;
  return old;
}
function restoreUnits(old){ app.preferences.rulerUnits = old[0]; app.preferences.typeUnits = old[1]; }
"""


def jsx_export(path: str) -> str:
    """Copie aplatie (RVB 8 bits) du document actif en PNG. Renvoie 'largeur,hauteur,resolution,nom'."""
    return f"""
(function(){{
  if (app.documents.length == 0) return "ERREUR:Aucune image ouverte dans Photoshop.";
  var doc = app.activeDocument, old = pxUnits();
  var dup = doc.duplicate("calques_magiques_tmp", true);
  try {{ dup.flatten(); }} catch(e) {{}}
  if (dup.mode != DocumentMode.RGB) dup.changeMode(ChangeMode.RGB);
  if (dup.bitsPerChannel != BitsPerChannelType.EIGHT) dup.bitsPerChannel = BitsPerChannelType.EIGHT;
  dup.saveAs(new File({jpath(path)}), new PNGSaveOptions(), true);
  dup.close(SaveOptions.DONOTSAVECHANGES);
  app.activeDocument = doc;
  var out = doc.width.as("px") + "," + doc.height.as("px") + "," + doc.resolution + "," + doc.name;
  restoreUnits(old);
  return out;
}})();
"""


def jsx_prepare(mask_path: str | None) -> str:
    """Groupe « Calques magiques » + calque « Fond » (copie de l'image), anciens calques masques,
    texte efface au Remplissage d'apres le contenu."""
    erase = ""
    if mask_path:
        erase = f"""
  var m = placePng(doc, {jpath(mask_path)}, "masque texte", null);
  selectionFromLayer(m);
  m.remove();
  doc.activeLayer = fond;
  try {{ contentAwareFill(); erased = "ok"; }} catch(e) {{ erased = "ERREUR:" + e; }}
  doc.selection.deselect();
"""
    return f"""
(function(){{
  var doc = app.activeDocument, old = pxUnits(), erased = "non";
  var group = doc.layerSets.add(); group.name = "Calques magiques";
  doc.selection.selectAll();
  doc.selection.copy(true);
  doc.selection.deselect();
  var fond = doc.paste(); fond.name = "Fond";
  fond.move(group, ElementPlacement.INSIDE);
  for (var i = 0; i < doc.layers.length; i++) {{
    var l = doc.layers[i];
    if (l != group) {{ try {{ l.visible = false; }} catch(e) {{}} }}
  }}
  {erase}
  restoreUnits(old);
  return erased;
}})();
"""


def jsx_export_layer(path: str) -> str:
    """Exporte le calque « Fond » (apres effacement du texte) pour la separation des formes."""
    return f"""
(function(){{
  var doc = app.activeDocument, old = pxUnits();
  var group = findSet(doc, "Calques magiques"), fond = findLayer(group, "Fond");
  var dup = doc.duplicate("calques_magiques_fond", false);
  for (var i = dup.layers.length - 1; i >= 0; i--) {{ try {{ dup.layers[i].visible = false; }} catch(e) {{}} }}
  var g2 = findSet(dup, "Calques magiques"); g2.visible = true;
  for (var j = 0; j < g2.layers.length; j++) g2.layers[j].visible = (g2.layers[j].name == "Fond");
  dup.flatten();
  if (dup.mode != DocumentMode.RGB) dup.changeMode(ChangeMode.RGB);
  if (dup.bitsPerChannel != BitsPerChannelType.EIGHT) dup.bitsPerChannel = BitsPerChannelType.EIGHT;
  dup.saveAs(new File({jpath(path)}), new PNGSaveOptions(), true);
  dup.close(SaveOptions.DONOTSAVECHANGES);
  app.activeDocument = doc;
  restoreUnits(old);
  return "ok";
}})();
"""


def jsx_magic(subject: bool, sky: bool, shapes: list[dict], fill_holes: bool) -> str:
    """Sujet detoure, ciel et formes en calques ; trous rebouches dans le fond."""
    shape_js = js([{"f": s["fichier"].replace("\\", "/"), "n": s["nom"]} for s in shapes])
    return f"""
(function(){{
  var doc = app.activeDocument, old = pxUnits(), report = [];
  var group = findSet(doc, "Calques magiques"), fond = findLayer(group, "Fond");
  function fillUnder(layer){{
    if (!{js(fill_holes)}) return;
    try {{
      selectionFromLayer(layer);
      doc.selection.expand(new UnitValue(4, "px"));
      doc.activeLayer = fond; contentAwareFill(); doc.selection.deselect();
    }} catch(e) {{ try {{ doc.selection.deselect(); }} catch(e2) {{}} }}
  }}
  if ({js(sky)}) {{
    try {{
      doc.activeLayer = fond;
      executeAction(sTID("selectSky"), undefined, DialogModes.NO);
      executeAction(sTID("copyToLayer"), undefined, DialogModes.NO);
      doc.activeLayer.name = "Ciel"; report.push("ciel");
      doc.selection.deselect();
    }} catch(e) {{ report.push("ciel non trouve"); try {{ doc.selection.deselect(); }} catch(e2) {{}} }}
  }}
  var shapesSet = null, list = {shape_js};
  if (list.length) {{
    shapesSet = group.layerSets.add(); shapesSet.name = "Formes";
    for (var i = list.length - 1; i >= 0; i--) {{
      var lyr = placePng(doc, list[i].f, list[i].n, shapesSet);
      fillUnder(lyr);
    }}
    report.push(list.length + " formes");
  }}
  if ({js(subject)}) {{
    try {{
      doc.activeLayer = fond;
      var d = new ActionDescriptor(); d.putBoolean(sTID("sampleAllLayers"), false);
      executeAction(sTID("autoCutout"), d, DialogModes.NO);
      executeAction(sTID("copyToLayer"), undefined, DialogModes.NO);
      var subj = doc.activeLayer; subj.name = "Sujet";
      doc.selection.deselect();
      subj.move(group, ElementPlacement.PLACEATBEGINNING);
      fillUnder(subj);
      report.push("sujet");
    }} catch(e) {{ report.push("sujet non trouve"); try {{ doc.selection.deselect(); }} catch(e2) {{}} }}
  }}
  restoreUnits(old);
  return report.join(", ");
}})();
"""


def jsx_texts(lines: list[dict], regular: str, bold: str, fit: bool) -> str:
    """Un calque de texte par ligne lue : meme texte, place, taille et couleur ; ajuste a la largeur."""
    data = [{"t": l["texte"], "x": round(l["x"], 1), "y": round(l["y"], 1), "b": round(l["ligne_base"], 1), "s": round(l["taille"], 2),
             "w": round(l["w"], 1), "h": round(l["h"], 1), "c": l["couleur"],
             "f": bold if l["gras"] else regular} for l in lines]
    return f"""
(function(){{
  var doc = app.activeDocument, old = pxUnits(), made = 0, errors = [];
  var group = findSet(doc, "Calques magiques");
  var texts = group.layerSets.add(); texts.name = "Textes";
  texts.move(group, ElementPlacement.PLACEATBEGINNING);
  var list = {js(data)};
  for (var i = list.length - 1; i >= 0; i--) {{
    var L = list[i];
    try {{
      var t = texts.artLayers.add(); t.kind = LayerKind.TEXT;
      var ti = t.textItem;
      ti.contents = L.t;
      try {{ ti.font = L.f; }} catch(e) {{}}
      ti.size = L.s;
      var c = new SolidColor(); c.rgb.red = L.c[0]; c.rgb.green = L.c[1]; c.rgb.blue = L.c[2]; ti.color = c;
      ti.position = [L.x, L.b];
      if ({js(fit)}) {{
        var b = t.bounds, bw = b[2].as("px") - b[0].as("px"), bh = b[3].as("px") - b[1].as("px");
        if (bh > 0) {{
          var k = Math.max(0.6, Math.min(1.6, L.h / bh));
          if (Math.abs(k - 1) > 0.04) {{ ti.size = L.s * k; ti.position = [L.x, L.b]; }}
        }}
        b = t.bounds; bw = b[2].as("px") - b[0].as("px");
        var n = L.t.length;
        if (n > 1 && bw > 0) {{
          var extra = (L.w - bw) / (n - 1);
          var em = ti.size.as ? ti.size.as("px") : Number(ti.size);
          ti.tracking = Math.max(-200, Math.min(800, Math.round(extra / em * 1000)));
        }}
        b = t.bounds;                                   // meme boite que le texte d'origine
        t.translate(L.x - b[0].as("px"), L.y - b[1].as("px"));
      }}
      t.name = L.t.substring(0, 40);
      made++;
    }} catch(e) {{ errors.push(L.t + " : " + e); }}
  }}
  restoreUnits(old);
  return made + (errors.length ? " | " + errors.slice(0, 3).join(" ; ") : "");
}})();
"""


def jsx_fonts() -> str:
    return """
(function(){
  var a = [];
  for (var i = 0; i < app.fonts.length; i++) a.push(app.fonts[i].postScriptName + "\\t" + app.fonts[i].name);
  return a.join("\\n");
})();
"""


# --------------------------------------------------------------- traitement ---
def decompose(ps, options: dict, log) -> str:
    """Toute la chaine sur l'image active de Photoshop."""
    work = tempfile.mkdtemp(prefix="calques_magiques_")
    flat = os.path.join(work, "image.png")
    log("Copie de l'image...")
    info = ps.run(jsx_export(flat))
    if info.startswith("ERREUR:"):
        return info[7:]
    rgb = load_rgb(flat)
    lines = []
    if options["textes"]:
        log("Lecture du texte (Windows)...")
        small = os.path.join(work, "ocr.png")
        k = ocr_input(flat, small)
        raw = scale_lines(ocr_image(small, options.get("langues", [])), k)
        lines = [a for a in (analyse_line(rgb, l) for l in raw) if a and a["texte"].strip()]
    mask = None
    if lines and options["effacer"]:
        mask = os.path.join(work, "masque.png")
        text_mask(rgb, lines, mask)
    log("Calque de fond" + (" et effacement du texte..." if mask else "..."))
    erased = ps.run(jsx_prepare(mask))
    shapes = []
    if options["formes"]:
        log("Séparation des formes et aplats...")
        clean = os.path.join(work, "fond.png")
        ps.run(jsx_export_layer(clean))
        shapes = color_shapes(load_rgb(clean), int(options.get("couleurs", 8)), work)
    if options["sujet"] or options["ciel"] or shapes:
        log("Sujet, ciel, formes...")
        magic = ps.run(jsx_magic(options["sujet"], options["ciel"], shapes, options.get("reboucher", True)))
    else:
        magic = ""
    made = ""
    if lines:
        log(f"Création de {len(lines)} calque(s) de texte...")
        made = ps.run(jsx_texts(lines, options["police"], options["police_grasse"], True))
    report = [f"Image : {info.split(',', 3)[-1]}"]
    report.append(f"Textes lus : {len(lines)} ligne(s)" + (f", calques créés : {made}" if lines else ""))
    if mask:
        report.append("Texte d'origine effacé." if erased == "ok" else f"Effacement : {erased}")
    if magic:
        report.append("Calques : " + magic)
    if options["textes"] and not lines:
        report.append("Aucun texte trouvé (texte trop stylisé, trop petit, ou langue non installée dans Windows).")
    return "\n".join(report)


# -------------------------------------------------------------------- fenetre ---
def window() -> None:
    import tkinter as tk
    from tkinter import ttk

    state = load_json(REGLAGES)
    root = tk.Tk()
    root.title(f"Calques magiques - version {VERSION}")
    root.attributes("-topmost", True)
    outer = ttk.Frame(root, padding=12)
    outer.grid()

    kind_var = tk.StringVar(value=state.get("type") if state.get("type") in TYPES else TYPES[0])
    opts = DEFAUTS[kind_var.get()]
    v = {k: tk.BooleanVar(value=state.get(k, opts[k])) for k in opts}

    top = ttk.Frame(outer)
    top.grid(row=0, column=0, sticky="w")
    ttk.Label(top, text="Type d'image", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    kind_box = ttk.Combobox(top, values=TYPES, textvariable=kind_var, state="readonly", width=22)
    kind_box.grid(row=0, column=1, padx=8)

    def kind_changed(*_):
        for k, val in DEFAUTS[kind_var.get()].items():
            v[k].set(val)
    kind_box.bind("<<ComboboxSelected>>", kind_changed)

    tbox = ttk.LabelFrame(outer, text="Textes (Grab Text)", padding=8)
    tbox.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    ttk.Checkbutton(tbox, text="Lire les textes et les recréer en calques de texte modifiables",
                    variable=v["textes"]).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Checkbutton(tbox, text="Effacer le texte d'origine (Remplissage d'après le contenu)",
                    variable=v["effacer"]).grid(row=1, column=0, columnspan=3, sticky="w")
    ttk.Label(tbox, text="Langue").grid(row=2, column=0, sticky="w", pady=(6, 0))
    langs = ocr_languages()
    lang_names = ["Automatique (langues de Windows)", "Toutes les langues installées"] + [f"{n} ({c})" for c, n in langs]
    lang_var = tk.StringVar(value=state.get("langue") if state.get("langue") in lang_names else lang_names[0])
    ttk.Combobox(tbox, values=lang_names, textvariable=lang_var, state="readonly", width=34
                 ).grid(row=2, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 0))
    ttk.Label(tbox, text="Police normale").grid(row=3, column=0, sticky="w", pady=(4, 0))
    font_var = tk.StringVar(value=state.get("police", POLICE_NORMALE))
    font_box = ttk.Combobox(tbox, textvariable=font_var, width=34)
    font_box.grid(row=3, column=1, sticky="w", padx=8, pady=(4, 0))
    ttk.Label(tbox, text="Police grasse").grid(row=4, column=0, sticky="w", pady=(4, 0))
    bold_var = tk.StringVar(value=state.get("police_grasse", POLICE_GRASSE))
    bold_box = ttk.Combobox(tbox, textvariable=bold_var, width=34)
    bold_box.grid(row=4, column=1, sticky="w", padx=8, pady=(4, 0))
    ttk.Button(tbox, text="Lister mes polices", command=lambda: load_fonts()).grid(row=3, column=2, rowspan=2, padx=4)

    mbox = ttk.LabelFrame(outer, text="Calques (Magic Layers)", padding=8)
    mbox.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    ttk.Checkbutton(mbox, text="Détourer le sujet principal (personne, objet, bâtiment)", variable=v["sujet"]
                    ).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Checkbutton(mbox, text="Séparer le ciel", variable=v["ciel"]).grid(row=1, column=0, columnspan=3, sticky="w")
    ttk.Checkbutton(mbox, text="Séparer les formes et aplats de couleur (flyers, logos)", variable=v["formes"]
                    ).grid(row=2, column=0, columnspan=2, sticky="w")
    ttk.Label(mbox, text="couleurs").grid(row=2, column=3, sticky="e")
    colors_var = tk.IntVar(value=int(state.get("couleurs", 8)))
    ttk.Spinbox(mbox, from_=3, to=16, textvariable=colors_var, width=4).grid(row=2, column=2, sticky="e")
    fill_var = tk.BooleanVar(value=state.get("reboucher", True))
    ttk.Checkbutton(mbox, text="Reconstruire le fond sous les éléments détachés", variable=fill_var
                    ).grid(row=3, column=0, columnspan=3, sticky="w")

    report = tk.Text(outer, width=70, height=8, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=3, column=0, pady=(10, 0))
    status = tk.StringVar(value="Ouvre une image dans Photoshop, puis clique sur « Décomposer l'image active ».")
    ttk.Label(outer, textvariable=status, foreground="#555", wraplength=520).grid(row=4, column=0, sticky="w", pady=(6, 0))

    def show(text):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", text)
        report.configure(state="disabled")

    ps_holder = {}

    def photoshop():
        if "ps" not in ps_holder:
            ps_holder["ps"] = Photoshop()
        return ps_holder["ps"]

    def load_fonts():
        status.set("Lecture des polices de Photoshop...")
        root.update_idletasks()
        try:
            raw = photoshop().run(jsx_fonts())
            names = [ln.split("\t")[0] for ln in raw.splitlines() if ln.strip()]
            font_box.configure(values=names)
            bold_box.configure(values=[n for n in names if "bold" in n.lower() or "black" in n.lower()] or names)
            status.set(f"{len(names)} polices. Choisis la police normale et la police grasse (nom PostScript).")
        except Exception as error:  # noqa: BLE001
            status.set(f"Photoshop injoignable : {error}")

    def collect() -> dict:
        choice = lang_var.get()
        if choice == lang_names[0]:
            chosen = []
        elif choice == lang_names[1]:
            chosen = [c for c, _n in langs]
        else:
            chosen = [choice.rsplit("(", 1)[-1].rstrip(")")]
        options = {k: bool(var.get()) for k, var in v.items()}
        options.update(type=kind_var.get(), langue=choice, langues=chosen, police=font_var.get().strip() or POLICE_NORMALE,
                       police_grasse=bold_var.get().strip() or POLICE_GRASSE, couleurs=int(colors_var.get()),
                       reboucher=bool(fill_var.get()))
        save_json(REGLAGES, {k: val for k, val in options.items() if k != "langues"})
        return options

    def go():
        options = collect()
        started = time.time()

        def log(text):
            status.set(text)
            root.update_idletasks()
        try:
            text = decompose(photoshop(), options, log)
            status.set(f"Terminé en {time.time() - started:.0f} s. Tout est dans le groupe « Calques magiques » "
                       "(les calques d'origine sont masqués, pas supprimés).")
            show(text)
        except Exception as error:  # noqa: BLE001
            status.set("Erreur.")
            show(f"Erreur : {error}\n\nVérifie que Photoshop est ouvert avec une image et qu'aucune boîte de "
                 "dialogue n'est ouverte dans Photoshop.")

    buttons = ttk.Frame(outer)
    buttons.grid(row=5, column=0, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Fermer", command=root.destroy).grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Décomposer l'image active", command=go).grid(row=0, column=1, padx=4)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.mainloop()


def main() -> int:
    missing = missing_packages()
    if missing:
        print("Installation des modules necessaires (une seule fois) : " + ", ".join(missing))
        print(install(missing))
        still = missing_packages()
        if still:
            print("Impossible d'installer : " + ", ".join(still))
            input("Entree pour fermer...")
            return 1
    window()
    return 0


if __name__ == "__main__":
    sys.exit(main())
