# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Substitution graphique - changer les couleurs de la SELECTION - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis dans Archicad : palette Tapir > "SubstitutionGraphique.py" > Executer.

La fenetre reste ouverte a cote d'Archicad : tu selectionnes des elements dans le plan,
tu coches ce que tu veux changer, tu choisis la couleur, puis "Appliquer a la selection".

Chaque partie se regle separement (coche = change, decoche = ne touche pas) :
  Murs, poteaux, poutres : interieur (remplissage de coupe) / contour des murs (+ epaisseur)
  Objets (mobilier, sanitaires...) : lignes et contours / remplissages
                                     (+ portes et fenetres si tu coches l'option)
  Textes et etiquettes : couleur du texte
  Dessin 2D : lignes, polylignes, arcs / contour des hachures / remplissage des hachures

Couleurs : "Couleur..." = couleur libre (le script l'ecrit dans un stylo libre, entre 160 et 199,
avec l'epaisseur choisie) ; "Stylo..." = un stylo deja present dans ton jeu de stylos.
Deux selections peuvent avoir des couleurs differentes : chaque couleur a son propre stylo.

"Remettre d'origine" : remet les couleurs d'avant le script sur les elements selectionnes.
Ctrl+Z dans Archicad marche aussi (une fois par type d'element modifie).

Contour des murs : demande Tapir 1.7.1 ou plus recent (Tapir 1.7.0 ne sait pas le changer).
"""

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

PORT = 19723
REGLAGES = "substitution_graphique_reglages.json"
MEMOIRE = "substitution_graphique_memoire.json"

STYLOS_LIBRES = range(160, 200)      # stylos que le script peut recolorer pour les couleurs libres
LARGEUR_REMPLISSAGE = 0.10           # epaisseur des stylos de remplissage (sans effet visible)

# role : (groupe, libelle, nature, couleur par defaut, epaisseur par defaut en mm ou None)
ROLES = {
    "murs_interieur": ("murs", "Intérieur (remplissage de coupe)", "fond", (0.45, 0.45, 0.45), None),
    "murs_contour":   ("murs", "Contour des murs (trait coupé)", "trait", (0.10, 0.10, 0.10), 0.35),
    "murs_vues":      ("murs", "Lignes vues, non coupées (coupe / façade)", "trait", (0.35, 0.35, 0.35), 0.13),
    "objets_lignes":  ("objets", "Lignes et contours", "trait", (0.30, 0.30, 0.30), 0.13),
    "objets_fond":    ("objets", "Remplissages", "fond", (0.93, 0.93, 0.93), None),
    "textes":         ("textes", "Couleur du texte", "texte", (0.10, 0.10, 0.10), None),
    "lignes":         ("dessin", "Lignes, polylignes, arcs", "trait", (0.30, 0.30, 0.30), 0.18),
    "hach_contour":   ("dessin", "Contour des hachures", "trait", (0.30, 0.30, 0.30), 0.13),
    "hach_fond":      ("dessin", "Remplissage des hachures", "fond", (0.88, 0.88, 0.88), None),
}
GROUPES = {
    "murs": "Murs, dalles, poteaux, poutres, morphes",
    "objets": "Objets (mobilier, sanitaires, cuisine...)",
    "textes": "Textes et étiquettes",
    "dessin": "Dessin 2D (lignes et hachures)",
}
ACTIFS_DEFAUT = {"murs_interieur", "murs_contour", "objets_lignes", "textes"}

PREREGLAGES = {
    "Présentation grise": {
        "murs_interieur": ((0.45, 0.45, 0.45), None), "murs_contour": ((0.10, 0.10, 0.10), 0.35),
        "objets_lignes": ((0.40, 0.40, 0.40), 0.13), "objets_fond": ((0.95, 0.95, 0.95), None),
        "textes": ((0.15, 0.15, 0.15), None), "lignes": ((0.40, 0.40, 0.40), 0.13),
        "hach_contour": ((0.55, 0.55, 0.55), 0.10), "hach_fond": ((0.90, 0.90, 0.90), None)},
    "Noir et blanc": {
        "murs_interieur": ((0.0, 0.0, 0.0), None), "murs_contour": ((0.0, 0.0, 0.0), 0.50),
        "objets_lignes": ((0.0, 0.0, 0.0), 0.13), "objets_fond": ((1.0, 1.0, 1.0), None),
        "textes": ((0.0, 0.0, 0.0), None), "lignes": ((0.0, 0.0, 0.0), 0.13),
        "hach_contour": ((0.0, 0.0, 0.0), 0.10), "hach_fond": ((0.85, 0.85, 0.85), None)},
    "Murs blancs, contour noir épais": {
        "murs_interieur": ((1.0, 1.0, 1.0), None), "murs_contour": ((0.0, 0.0, 0.0), 0.70),
        "objets_lignes": ((0.35, 0.35, 0.35), 0.13), "objets_fond": ((1.0, 1.0, 1.0), None),
        "textes": ((0.10, 0.10, 0.10), None)},
    "Annotations rouges": {
        "textes": ((0.80, 0.22, 0.22), None), "lignes": ((0.80, 0.22, 0.22), 0.10)},
}

# types d'elements -> (commande Tapir, cle de la liste) pour l'interieur / le contour des murs
MURS = {"Wall": ("ModifyWalls", "wallsWithDetails"),
        "Column": ("ModifyColumns", "columnsWithDetails"),
        "Beam": ("ModifyBeams", "beamsWithDetails"),
        "Slab": ("ModifySlabs", "slabsWithDetails")}
MORPHES = {"Morph": ("ModifyMorphs", "morphsWithDetails")}   # stylos simples (pas "overridden")
VUES = ("Plan", "Coupe / façade")
OBJETS = ("Object", "Lamp")
OBJETS_MODIF = {"Object": ("ModifyObjects", "objectsWithDetails"), "Lamp": ("ModifyLamps", "lampsWithDetails")}
OUVERTURES = ("Door", "Window", "Skylight")
TEXTES = {"Text": ("ModifyTexts", "textsWithDetails"), "Label": ("ModifyLabels", "labelsWithDetails")}
LIGNES = ("Line", "PolyLine", "Arc", "Circle", "Spline")
NOMS_TYPES = {"Wall": "mur", "Column": "poteau", "Slab": "dalle", "Morph": "morphe", "Beam": "poutre", "Object": "objet", "Lamp": "luminaire",
              "Door": "porte", "Window": "fenêtre", "Skylight": "fenêtre de toit", "Text": "texte",
              "Label": "étiquette", "Line": "ligne", "PolyLine": "polyligne", "Arc": "arc",
              "Circle": "cercle", "Spline": "courbe", "Hatch": "hachure"}

MOTS_FOND_MOTIF = ("back", "bkg", "bgpen", "_bg", "fond", "arriere", "arrière")
MOTS_REMPLISSAGE = ("fill", "remplis", "hachur", "hatch")


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


def chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


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
    roles = {}
    for key, (_g, _l, _n, color, width) in ROLES.items():
        s = (saved.get("roles") or {}).get(key) or {}
        c = s.get("couleur")
        pen = s.get("stylo")
        w = s.get("epaisseur")
        roles[key] = {
            "actif": bool(s.get("actif", key in ACTIFS_DEFAUT)),
            "couleur": [float(v) for v in c] if isinstance(c, list) and len(c) == 3 else list(color),
            "stylo": pen if isinstance(pen, int) and not isinstance(pen, bool) and 1 <= pen <= 255 else None,
            "epaisseur": float(w) if isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0 else width,
        }
    return {"roles": roles,
            "vue": saved.get("vue") if saved.get("vue") in VUES else VUES[0],
            "murs_plein": bool(saved.get("murs_plein", True)),
            "ouvertures": bool(saved.get("ouvertures", False))}


def project_key() -> str:
    try:
        info = tapir("GetProjectInfo")
        return info.get("projectPath") or info.get("projectName") or "Sans titre"
    except Exception:  # noqa: BLE001
        return "Sans titre"


def tapir_version() -> tuple:
    try:
        text = str(tapir("GetAddOnVersion").get("version", "0"))
        return tuple(int(p) for p in text.split(".")[:3] if p.isdigit())
    except Exception:  # noqa: BLE001
        return (0,)


# ------------------------------------------------------------------ stylos ---
def active_pen_table() -> dict | None:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables if "attributeId" in t]
    if not ids:
        return None
    details = [t for t in tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                ).get("penTables", []) if "error" not in t]
    return next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)


def pen_palette() -> dict:
    """{numero: (r, v, b, description, epaisseur)} du jeu de stylos actif (lecture seule)."""
    try:
        table = active_pen_table()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for pen in (table or {}).get("pens", []):
        color = pen.get("color")
        if isinstance(pen.get("index"), int) and isinstance(color, dict):
            out[pen["index"]] = (color.get("red", 0.5), color.get("green", 0.5), color.get("blue", 0.5),
                                 pen.get("description", ""), pen.get("width", 0.1))
    return out


def same_color(a, b) -> bool:
    return all(abs(float(x) - float(y)) < 0.004 for x, y in zip(a, b))


def resolve_pens(settings: dict, keys: list[str], memory: dict) -> dict:
    """Numero de stylo de chaque role : le stylo Archicad choisi, sinon un stylo libre (160-199) qui
    porte deja cette couleur + epaisseur, sinon un nouveau. Ecrit les nouveaux stylos en une fois."""
    pool = memory.setdefault("stylos", {})          # {"160": {"couleur": [...], "epaisseur": 0.35}}
    pens, new = {}, {}
    for key in keys:
        role = settings["roles"][key]
        if role["stylo"] is not None:
            pens[key] = role["stylo"]
            continue
        width = role["epaisseur"] if ROLES[key][2] == "trait" else None
        found = None
        for index, data in list(pool.items()) + list(new.items()):
            if same_color(data["couleur"], role["couleur"]) and (
                    width is None or abs(data["epaisseur"] - width) < 0.001):
                found = int(index)
                break
        if found is None:
            used = {int(i) for i in pool} | {int(i) for i in new}
            free = [i for i in STYLOS_LIBRES if i not in used]
            if not free:                            # tout est pris : on recycle le plus ancien
                free = [int(next(iter(pool)))]
                pool.pop(str(free[0]))
            found = free[0]
            new[str(found)] = {"couleur": list(role["couleur"]),
                               "epaisseur": width if width is not None else LARGEUR_REMPLISSAGE}
        pens[key] = found
    if new:
        table = active_pen_table()
        if table is None:
            raise RuntimeError("jeu de stylos actif introuvable")
        answer = tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
            "attributeId": table["attributeId"], "name": table["name"], "pens": [
                {"index": int(i), "width": d["epaisseur"], "description": f"Substitution graphique {i}",
                 "color": {"red": d["couleur"][0], "green": d["couleur"][1], "blue": d["couleur"][2]}}
                for i, d in new.items()]}]})
        if "error" in (answer.get("attributeIds") or [{}])[0]:
            raise RuntimeError("Tapir a refusé l'écriture des stylos")
        pool.update(new)
    return pens


# ----------------------------------------------------------------- lecture ---
def selection() -> list[dict]:
    try:
        elements = api("API.GetSelectedElements").get("elements", [])
    except Exception:  # noqa: BLE001
        elements = tapir("GetSelectedElements").get("elements", [])
    return [e for e in elements if isinstance(e, dict) and "elementId" in e]


def read_details(elements: list[dict]) -> list[dict]:
    rows = []
    for part in chunks(elements, 200):
        answer = tapir("GetDetailsOfElements", {"elements": part, "fields": ["type", "details"]}
                       ).get("detailsOfElements", [])
        for element, detail in zip(part, answer):
            if "error" not in detail:
                rows.append({"guid": element["elementId"]["guid"], "type": detail.get("type", ""),
                             "details": detail.get("details") or {}})
    return rows


def describe(rows: list[dict]) -> str:
    counts: dict = {}
    for r in rows:
        name = NOMS_TYPES.get(r["type"], r["type"].lower() or "élément")
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "Rien de sélectionné dans Archicad."
    parts = [f"{n} {name}{'s' if n > 1 and not name.endswith('s') else ''}" for name, n in
             sorted(counts.items(), key=lambda kv: -kv[1])]
    return "Sélection : " + ", ".join(parts)


def pen_params(guids: list[str]) -> dict:
    """{guid: [(nom, valeur, nature)]} des parametres GDL de type stylo (nature : trait / fond / motif)."""
    out = {}
    for part in chunks(guids, 50):
        answer = tapir("GetGDLParametersOfElements", {"elements": [{"elementId": {"guid": g}} for g in part]}
                       ).get("gdlParametersOfElements", [])
        for guid, entry in zip(part, answer):
            found = []
            for p in (entry or {}).get("parameters", []) if isinstance(entry, dict) else []:
                if p.get("type") != "PenColor" or p.get("isLocked") or p.get("dimension1") \
                        or not isinstance(p.get("value"), (int, float)):
                    continue
                text = f"{p.get('name', '')} {p.get('displayName', '')}".lower()
                if any(w in text for w in MOTS_FOND_MOTIF):
                    nature = "motif"
                elif any(w in text for w in MOTS_REMPLISSAGE):
                    nature = "fond"
                else:
                    nature = "trait"
                found.append((p["name"], int(p["value"]), nature))
            out[guid] = found
    return out


# ----------------------------------------------------------------- ecriture ---
def count_ok(answer: dict, total: int) -> int:
    results = answer.get("executionResults")
    if not isinstance(results, list):
        return total
    return sum(1 for r in results if isinstance(r, dict) and r.get("success") is not False and "error" not in r)


def send(command: str, key: str, items: list[dict]) -> tuple[int, str]:
    """Envoie par paquets ; renvoie (nombre reussi, derniere erreur)."""
    ok, error = 0, ""
    for part in chunks(items, 100):
        try:
            ok += count_ok(tapir(command, {key: part}), len(part))
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
    return ok, error


def write_fields(kind: str, by_guid: dict) -> tuple[int, str]:
    """by_guid : {guid: {champ: valeur}} pour un type d'element. Champs : ceux de Modify* ou de SetDetails."""
    if not by_guid:
        return 0, ""
    if kind in MURS or kind in OBJETS_MODIF or kind in MORPHES:
        command, key = MURS.get(kind) or OBJETS_MODIF.get(kind) or MORPHES[kind]
        return send(command, key, [{"elementId": {"guid": g}, **fields} for g, fields in by_guid.items()])
    if kind in TEXTES:
        command, key = TEXTES[kind]
        return send(command, key, [{"elementId": {"guid": g}, "style": {"penIndex": f["penIndex"]}}
                                   for g, f in by_guid.items()])
    return send("SetDetailsOfElements", "elementsWithDetails",
                [{"elementId": {"guid": g}, "details": {"typeSpecificDetails": f}} for g, f in by_guid.items()])


def write_gdl(by_guid: dict) -> tuple[int, str]:
    items = [{"elementId": {"guid": g}, "gdlParameters": [{"name": n, "value": v} for n, v in params.items()]}
             for g, params in by_guid.items() if params]
    return send("SetGDLParametersOfElements", "elementsWithGDLParameters", items)


# ----------------------------------------------------------------- actions ---
def run_apply(settings: dict) -> str:
    rows = read_details(selection())
    if not rows:
        return "Rien de sélectionné. Sélectionne des éléments dans Archicad, puis clique « Appliquer »."
    roles = settings["roles"]
    on = {k for k, r in roles.items() if r["actif"]}
    if not on:
        return "Coche au moins une chose à changer."
    version = tapir_version()
    contour_ok = version >= (1, 7, 1)
    notes = []

    object_types = OBJETS + (OUVERTURES if settings["ouvertures"] else ())
    present = {r["type"] for r in rows}
    coupe = settings.get("vue") == VUES[1]
    if not coupe:
        on.discard("murs_vues")
    needed = []
    if "murs_interieur" in on and present & (set(MURS) | set(MORPHES)):
        needed.append("murs_interieur")
    if "murs_contour" in on and (("Wall" in present and contour_ok) or "Morph" in present):
        needed.append("murs_contour")
    if "murs_vues" in on and (("Wall" in present and contour_ok) or "Morph" in present):
        needed.append("murs_vues")
    if present & set(object_types):
        needed += [k for k in ("objets_lignes", "objets_fond") if k in on]
    if "textes" in on and present & set(TEXTES):
        needed.append("textes")
    if "lignes" in on and present & set(LIGNES):
        needed.append("lignes")
    if "Hatch" in present:
        needed += [k for k in ("hach_contour", "hach_fond") if k in on]
    if not needed:
        lines = ["Rien à changer : la sélection ne contient pas d'élément des parties cochées.", describe(rows)]
        if "murs_contour" in on and "Wall" in present and not contour_ok:
            lines.append(contour_note(version))
        return "\n".join(lines)

    memory = load_json(MEMOIRE)
    project = memory.setdefault(project_key(), {})
    pens = resolve_pens(settings, needed, project)
    origine = project.setdefault("origine", {})        # {guid: {"type": t, "champs": {...}, "gdl": {...}}}

    def remember(guid, kind, field, value, gdl=False):
        entry = origine.setdefault(guid, {"type": kind, "champs": {}, "gdl": {}})
        entry["gdl" if gdl else "champs"].setdefault(field, value)

    plans: dict = {}                                    # {type: {guid: {champ: valeur}}}
    gdl_plan: dict = {}
    for r in rows:
        kind, guid, d = r["type"], r["guid"], r["details"]
        fields = {}
        if kind in MURS and "murs_interieur" in pens:
            pen = pens["murs_interieur"]
            fields["cutFillPen"] = {"overridden": True, "penIndex": pen}
            remember(guid, kind, "cutFillPen", d.get("cutFillPen") or {"overridden": False})
            if settings["murs_plein"]:
                fields["cutFillBackgroundPen"] = {"overridden": True, "penIndex": pen}
                remember(guid, kind, "cutFillBackgroundPen", d.get("cutFillBackgroundPen") or {"overridden": False})
        if kind == "Wall" and "murs_contour" in pens and contour_ok:
            fields["contourPen"] = pens["murs_contour"]          # trait coupe : plan ET coupe
            if isinstance(d.get("contourPen"), int):
                remember(guid, kind, "contourPen", d["contourPen"])
        if kind == "Wall" and "murs_vues" in pens and contour_ok:
            fields["contourPen3D"] = pens["murs_vues"]           # lignes vues en coupe / facade / 3D
            if isinstance(d.get("contourPen3D"), int):
                remember(guid, kind, "contourPen3D", d["contourPen3D"])
        if kind in MORPHES:                                       # pas de lecture : Ctrl+Z seulement
            if "murs_interieur" in pens:
                fields["cutFillPen"] = pens["murs_interieur"]
                if settings["murs_plein"]:
                    fields["cutFillBackgroundPen"] = pens["murs_interieur"]
            if "murs_contour" in pens:
                fields["cutLinePen"] = pens["murs_contour"]
            if "murs_vues" in pens:
                fields["uncutLinePen"] = pens["murs_vues"]
        if kind in TEXTES and "textes" in pens:
            style = d.get("style") if isinstance(d.get("style"), dict) else {}
            old = style.get("penIndex", d.get("pen"))
            if kind == "Label" and not style:
                continue                                # etiquette symbole : pas de style texte
            fields["penIndex"] = pens["textes"]
            if isinstance(old, int):
                remember(guid, kind, "penIndex", old)
        if kind in OBJETS_MODIF and "objets_lignes" in pens:
            # "Remplacer les stylos de ligne de l'objet" : toutes ses lignes prennent ce stylo
            fields["pen"] = pens["objets_lignes"]
            fields["useObjectPens"] = False
            if isinstance(d.get("pen"), int):
                remember(guid, kind, "pen", d["pen"])
            if isinstance(d.get("useObjectPens"), bool):
                remember(guid, kind, "useObjectPens", d["useObjectPens"])
        if kind in OBJETS_MODIF and coupe and ({"objets_lignes", "objets_fond"} & set(pens)):
            # partie coupee de l'objet en coupe : "Remplacer les attributs de coupe"
            if "objets_lignes" in pens:
                fields["sectionContourPen"] = pens["objets_lignes"]
            if "objets_fond" in pens:
                fields["sectionFillPen"] = pens["objets_fond"]
                fields["sectionFillBackgroundPen"] = pens["objets_fond"]
            fields["useObjectSectionAttributes"] = False
            for name in ("sectionContourPen", "sectionFillPen", "sectionFillBackgroundPen", "useObjectSectionAttributes"):
                if name in fields and isinstance(d.get(name), (int, bool)):
                    remember(guid, kind, name, d[name])
        if kind in LIGNES and "lignes" in pens:
            fields["linePenIndex"] = pens["lignes"]
            if isinstance(d.get("linePenIndex"), int):
                remember(guid, kind, "linePenIndex", d["linePenIndex"])
        if kind == "Hatch":
            for role, field in (("hach_contour", "contourPenIndex"), ("hach_fond", "fillPenIndex")):
                if role in pens:
                    fields[field] = pens[role]
                    if isinstance(d.get(field), int):
                        remember(guid, kind, field, d[field])
        if fields:
            plans.setdefault(kind, {})[guid] = fields
        if kind in object_types and ({"objets_lignes", "objets_fond"} & set(pens)):
            gdl_plan[guid] = kind

    report = []
    for kind, by_guid in plans.items():
        ok, error = write_fields(kind, by_guid)
        report.append(line(kind, ok, len(by_guid), error))

    if gdl_plan:
        params = pen_params(list(gdl_plan))
        to_set: dict = {}
        none = 0
        for guid, kind in gdl_plan.items():
            values = {}
            for name, value, nature in params.get(guid, []):
                role = "objets_lignes" if nature == "trait" else "objets_fond"
                if role in pens and value != pens[role]:
                    values[name] = pens[role]
                    remember(guid, kind, name, value, gdl=True)
            if values:
                to_set[guid] = values
            elif not params.get(guid):
                none += 1
        if to_set:
            ok, error = write_gdl(to_set)
            report.append(line("réglages de stylos des objets", ok, len(to_set), error))
        if none:
            report.append(f"  {none} objet(s) sans réglage de stylo modifiable (couleurs fixées dans l'objet).")
        if not to_set and not none:
            report.append("  Objets : déjà dans ces couleurs.")

    save_json(MEMOIRE, memory)
    head = [f"Vue : {settings.get('vue', VUES[0])}", describe(rows), "Stylos utilisés : " + ", ".join(
        f"{ROLES[k][1].lower()} = {p}" for k, p in pens.items())]
    if "murs_contour" in on and "Wall" in present and not contour_ok:
        head.append(contour_note(version))
    if present & {"Column", "Beam", "Slab"} and ({"murs_contour", "murs_vues"} & on):
        head.append("Le contour des poteaux, poutres et dalles ne peut pas être changé par Tapir "
                    "(seulement leur intérieur).")
    if "Morph" in present:
        head.append("Morphes : « Remettre d'origine » ne peut pas les remettre, utilise Ctrl+Z dans Archicad.")
    return "\n".join(head + report + notes)


def contour_note(version: tuple) -> str:
    shown = ".".join(str(v) for v in version) if version != (0,) else "inconnue"
    return (f"Contour des murs non changé : ta version de Tapir ({shown}) ne le permet pas. "
            "Mets Tapir à jour (1.7.1 ou plus) pour cette option.")


def line(kind: str, ok: int, total: int, error: str) -> str:
    name = NOMS_TYPES.get(kind, kind)
    mark = "OK" if ok >= total else ("partiel" if ok else "échec")
    text = f"  {name} : {min(ok, total)}/{total} modifié(s) [{mark}]"
    if error and ok < total:
        text += f" - {error[:160]}"
    return text


def run_restore() -> str:
    rows = read_details(selection())
    if not rows:
        return "Sélectionne dans Archicad les éléments à remettre comme avant, puis clique « Remettre d'origine »."
    memory = load_json(MEMOIRE)
    project = memory.get(project_key(), {})
    origine = project.get("origine", {})
    chosen = [r for r in rows if r["guid"] in origine]
    if not chosen:
        return "Aucun élément de la sélection n'a été modifié par ce script."
    plans: dict = {}
    gdl: dict = {}
    for r in chosen:
        entry = origine[r["guid"]]
        if entry.get("champs"):
            plans.setdefault(entry["type"], {})[r["guid"]] = dict(entry["champs"])
        if entry.get("gdl"):
            gdl[r["guid"]] = dict(entry["gdl"])
    report = []
    for kind, by_guid in plans.items():
        if kind in TEXTES:
            by_guid = {g: f for g, f in by_guid.items() if "penIndex" in f}
        ok, error = write_fields(kind, by_guid)
        report.append(line(kind, ok, len(by_guid), error))
    if gdl:
        ok, error = write_gdl(gdl)
        report.append(line("réglages de stylos des objets", ok, len(gdl), error))
    for r in chosen:
        origine.pop(r["guid"], None)
    save_json(MEMOIRE, memory)
    return "\n".join([f"{len(chosen)} élément(s) remis comme avant :"] + report)


# ------------------------------------------------------------------ fenetre ---
def settings_window(settings: dict) -> None:
    import tkinter as tk
    from tkinter import colorchooser, ttk

    def to_hex(c):
        return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in c)

    state = settings
    pen_info: dict = {}
    root = tk.Tk()
    root.title("Substitution graphique - couleurs de la sélection")
    root.attributes("-topmost", True)
    outer = ttk.Frame(root, padding=12)
    outer.grid(sticky="nsew")

    sel_text = tk.StringVar(value="Lecture de la sélection...")
    status = tk.StringVar(value="Sélectionne dans Archicad, coche ce que tu veux changer, puis « Appliquer ».")
    top = ttk.Frame(outer)
    top.grid(row=0, column=0, sticky="ew")
    ttk.Label(top, textvariable=sel_text, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(top, text="Préréglage").grid(row=0, column=1, sticky="e", padx=(20, 4))
    preset_var = tk.StringVar(value="(choisir)")
    preset_box = ttk.Combobox(top, values=list(PREREGLAGES), textvariable=preset_var, state="readonly", width=28)
    preset_box.grid(row=0, column=2, sticky="e")
    top.columnconfigure(0, weight=1)
    vue_frame = ttk.Frame(top)
    vue_frame.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
    ttk.Label(vue_frame, text="Je travaille en :").grid(row=0, column=0, padx=(0, 6))
    vue_var = tk.StringVar(value=state.get("vue", VUES[0]))
    for i, v in enumerate(VUES):
        ttk.Radiobutton(vue_frame, text=v, value=v, variable=vue_var,
                        command=lambda: show_mode()).grid(row=0, column=i + 1, padx=4)

    widgets: dict = {}
    active = {k: tk.BooleanVar(value=r["actif"]) for k, r in state["roles"].items()}
    plein = tk.BooleanVar(value=state["murs_plein"])
    ouvertures = tk.BooleanVar(value=state["ouvertures"])

    def refresh(key):
        role = state["roles"][key]
        w = widgets[key]
        if role["stylo"] is not None and role["stylo"] in pen_info:
            role["couleur"] = list(pen_info[role["stylo"]][:3])
        w["swatch"].configure(bg=to_hex(role["couleur"]))
        w["tag"].configure(text=f"stylo {role['stylo']}" if role["stylo"] is not None else "couleur libre")
        if w.get("width") is not None:
            w["width_var"].set(f"{role['epaisseur']:.2f}")
            w["width"].configure(state="disabled" if role["stylo"] is not None else "normal")
        on = active[key].get()
        for name in ("swatch_btn", "pen_btn"):
            w[name].configure(state="normal" if on else "disabled")

    def pick_color(key):
        role = state["roles"][key]
        rgb, _ = colorchooser.askcolor(color=to_hex(role["couleur"]), title=ROLES[key][1], parent=root)
        if rgb:
            role["couleur"] = [v / 255.0 for v in rgb]
            role["stylo"] = None
            refresh(key)

    def pick_pen(key):
        nonlocal pen_info
        if not pen_info:
            pen_info = pen_palette()
        if not pen_info:
            status.set("Jeu de stylos illisible : utilise « Couleur... ».")
            return
        win = tk.Toplevel(root)
        win.title("Choisir un stylo d'Archicad")
        win.attributes("-topmost", True)
        win.transient(root)
        info = tk.StringVar(value="Survole un stylo pour voir sa description")
        grid = ttk.Frame(win, padding=8)
        grid.grid()

        def choose(number):
            state["roles"][key]["stylo"] = number
            if number in pen_info:
                state["roles"][key]["epaisseur"] = float(pen_info[number][4])
            refresh(key)
            win.destroy()

        for position, number in enumerate(sorted(pen_info)):
            r, g, b, descr, width = pen_info[number]
            fg = "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 0.55 else "#ffffff"
            btn = tk.Button(grid, text=str(number), width=3, bg=to_hex((r, g, b)), fg=fg, font=("Segoe UI", 8),
                            relief="solid" if number == state["roles"][key]["stylo"] else "raised",
                            command=lambda n=number: choose(n))
            btn.grid(row=position // 16, column=position % 16)
            btn.bind("<Enter>", lambda _e, n=number, d=descr, w=width: info.set(f"Stylo {n} - {d} - {w} mm"))
        ttk.Label(win, textvariable=info, padding=(8, 0, 8, 8)).grid(row=1, column=0, sticky="w")
        win.bind("<Escape>", lambda _e: win.destroy())

    row_frame = 1
    for group, title in GROUPES.items():
        box = ttk.LabelFrame(outer, text=title, padding=(10, 6))
        box.grid(row=row_frame, column=0, sticky="ew", pady=(8, 0))
        row_frame += 1
        r = 0
        for key, (g, label, nature, _c, width) in ROLES.items():
            if g != group:
                continue
            w = widgets[key] = {"cells": []}
            check = ttk.Checkbutton(box, text=label, variable=active[key], width=38,
                                    command=lambda k=key: refresh(k))
            check.grid(row=r, column=0, sticky="w", pady=2)
            w["cells"].append(check)
            w["swatch"] = tk.Label(box, width=4, relief="groove")
            w["swatch"].grid(row=r, column=1, padx=(4, 4))
            w["swatch_btn"] = ttk.Button(box, text="Couleur...", width=9, command=lambda k=key: pick_color(k))
            w["swatch_btn"].grid(row=r, column=2)
            w["pen_btn"] = ttk.Button(box, text="Stylo...", width=7, command=lambda k=key: pick_pen(k))
            w["pen_btn"].grid(row=r, column=3, padx=(2, 0))
            w["tag"] = ttk.Label(box, width=12, foreground="#666")
            w["tag"].grid(row=r, column=4, sticky="w", padx=(6, 0))
            w["cells"] += [w["swatch"], w["swatch_btn"], w["pen_btn"], w["tag"]]
            if nature == "trait":
                w["width_var"] = tk.StringVar()
                lab1 = ttk.Label(box, text="épaisseur")
                lab1.grid(row=r, column=5, sticky="e", padx=(6, 2))
                w["width"] = ttk.Spinbox(box, from_=0.05, to=2.0, increment=0.05, width=5,
                                         textvariable=w["width_var"], format="%.2f")
                w["width"].grid(row=r, column=6)
                lab2 = ttk.Label(box, text="mm")
                lab2.grid(row=r, column=7, sticky="w", padx=(2, 0))
                w["cells"] += [lab1, w["width"], lab2]
            else:
                w["width"] = None
            r += 1
        if group == "murs":
            ttk.Checkbutton(box, text="Intérieur en couleur pleine (motif et fond de la même couleur)",
                            variable=plein).grid(row=r, column=0, columnspan=8, sticky="w", pady=(2, 0))
        if group == "objets":
            ttk.Checkbutton(box, text="Inclure les portes et fenêtres", variable=ouvertures
                            ).grid(row=r, column=0, columnspan=8, sticky="w", pady=(2, 0))

    for key in ROLES:
        refresh(key)

    def show_mode():
        coupe = vue_var.get() == VUES[1]
        for cell in widgets["murs_vues"]["cells"]:
            cell.grid() if coupe else cell.grid_remove()
        status.set("Coupe / façade : sélectionne les éléments dans la fenêtre de coupe ou de façade."
                   if coupe else "Sélectionne dans Archicad, coche ce que tu veux changer, puis « Appliquer ».")

    show_mode()

    def on_preset(*_):
        for key, (color, width) in PREREGLAGES.get(preset_var.get(), {}).items():
            role = state["roles"][key]
            role["couleur"] = list(color)
            role["stylo"] = None
            if width is not None:
                role["epaisseur"] = width
            refresh(key)
        status.set(f"Préréglage « {preset_var.get()} » chargé : coche les parties à changer, puis Appliquer.")

    preset_box.bind("<<ComboboxSelected>>", on_preset)

    report = tk.Text(outer, width=92, height=9, wrap="word", state="disabled", background="#f6f6f6",
                     font=("Segoe UI", 9))
    report.grid(row=row_frame, column=0, pady=(10, 0), sticky="ew")
    ttk.Label(outer, textvariable=status, foreground="#555").grid(row=row_frame + 1, column=0, sticky="w", pady=(6, 0))

    def show(text: str):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", text)
        report.configure(state="disabled")

    def collect() -> dict:
        for key, role in state["roles"].items():
            role["actif"] = bool(active[key].get())
            w = widgets[key]
            if w.get("width") is not None and role["stylo"] is None:
                try:
                    value = float(w["width_var"].get().replace(",", "."))
                    role["epaisseur"] = min(max(value, 0.01), 5.0)
                except ValueError:
                    pass
        state["murs_plein"] = bool(plein.get())
        state["ouvertures"] = bool(ouvertures.get())
        state["vue"] = vue_var.get()
        save_json(REGLAGES, state)
        return state

    def do(action):
        status.set("En cours...")
        root.update_idletasks()
        try:
            current = collect()
            show(run_apply(current) if action == "appliquer" else run_restore())
            status.set("Terminé. Ctrl+Z dans Archicad annule aussi.")
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")
        poll(force=True)

    last = {"guids": None}

    def poll(force=False):
        try:
            elements = selection()
            guids = tuple(sorted(e["elementId"]["guid"] for e in elements))
            if force or guids != last["guids"]:
                last["guids"] = guids
                sel_text.set(describe(read_details(elements)) if elements else "Rien de sélectionné dans Archicad.")
        except Exception:  # noqa: BLE001
            sel_text.set("Archicad ne répond pas (Tapir lancé ?)")
        if not force:
            root.after(1500, poll)

    buttons = ttk.Frame(outer)
    buttons.grid(row=row_frame + 2, column=0, pady=(10, 0), sticky="e")
    ttk.Button(buttons, text="Remettre d'origine (sélection)", command=lambda: do("restaurer")).grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    ttk.Button(buttons, text="Appliquer à la sélection", command=lambda: do("appliquer")).grid(row=0, column=2, padx=4)
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
        print(f"(Fenetre indisponible : {error}. Application avec les reglages enregistres.)")
        print(run_apply(settings))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
