# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely>=2.0"]
# ///
"""
Tableau des surfaces - script Tapir (Archicad 28, mode normal)

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis dans Archicad : palette Tapir > choisir "TableauSurfaces.py" > Executer.

1. Creer les zones (pieces), plusieurs a la fois :
   - "Cliquer dans les pieces..." : un clic dans chaque piece fermee par des murs,
     Archicad trouve ses limites tout seul (Echap pour terminer) ;
   - "Dessiner une piece (contour)..." : cliquer les coins de la piece comme on
     veut (clic sur le 1er point ou Echap pour fermer) ; le tableau suit a chaque piece ;
   - "Zones depuis la selection" : selectionner des polylignes fermees ou des
     remplissages dessines comme on veut -> une zone pour chacun, a cette forme ;
   - "Mettre a jour zones + tableau" : apres avoir bouge des murs ou retouche
     des zones, recalcule les zones puis redessine le tableau.

3. Nomenclature : pastille numerotee (01, 02...) dans chaque piece, et legende
   soit separee en bas du plan (deplacable), soit integree au tableau (colonne N°).
   "Mettre a jour" garde la place des pastilles et de la legende deplacees a la main.

2. Tableau. La fenetre reste ouverte : couleur du bandeau, titres, texte
du total et mots qui reperent les surfaces annexes. "Appliquer" dessine, a droite
du plan de l'etage courant, deux tableaux :

    SURFACES HABITABLES            (bandeau de couleur, titre en blanc)
    RDC                            (un intertitre par niveau)
    piece a vivre        25,04
    ...
    TOTAL                48,87

    SURFACES ANNEXES               (terrasse, garage, balcon...)
    Terrasse             22,84
    TOTAL                22,84

Toutes les zones du projet sont prises, niveau par niveau.
Relancer le script remplace les tableaux precedents.

Le bandeau est un vrai remplissage Archicad : apres coup, on peut changer son
motif, ses stylos, etc. comme n'importe quel remplissage.
Chaque zone a son code couleur (case a gauche du nom) : la couleur de sa
categorie de zone, une couleur differente par zone.
Stylos reserves dans le jeu de stylos actif : 212 (texte et traits, noir),
213 (texte du bandeau, blanc), 214 (couleur du bandeau), 255, 254... (zones).
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- reglages ---
ECHELLE = 100                   # echelle du plan (100 = 1:100)
COULEUR_BANDEAU = (0.93, 0.11, 0.14)   # rouge par defaut (rouge, vert, bleu entre 0 et 1)
TITRE_HABITABLES = "SURFACES HABITABLES"
TITRE_ANNEXES = "SURFACES ANNEXES"
TEXTE_TOTAL = "TOTAL"
MOTS_ANNEXES = "terrasse, balcon, loggia, garage, parking, carport, veranda, véranda, cour, jardin, patio, porche, auvent, annexe, local technique"

UNITE = " m\u00b2"               # unite ecrite apres chaque surface ("" pour ne rien mettre)
COL_NOM_MM = 50.0               # largeur de la colonne des noms (mm papier)
COL_SURFACE_MM = 22.0           # largeur de la colonne des surfaces (mm papier)
BANDEAU_MM = 6.0                # hauteur du bandeau de titre
LIGNE_MM = 4.2                  # hauteur d'une ligne
TEXTE_MM = 2.5                  # hauteur du texte courant
TITRE_MM = 3.0                  # hauteur du texte du bandeau
ECART_PLAN_MM = 15.0            # espace entre le plan et les tableaux
ECART_TABLEAUX_MM = 8.0         # espace entre les deux tableaux
TRAITS_ENTRE_LIGNES = True      # trait horizontal sous chaque ligne (lecture plus facile)
TRAIT_FIN_MM = 0.18             # epaisseur des traits interieurs
TRAIT_CADRE_MM = 0.35           # epaisseur du cadre et du trait du total

CASE_MM = 6.0                   # largeur de la case de couleur de chaque zone
STYLOS_ZONES = 40               # stylos 255, 254, ... = couleurs des zones (max de zones colorees)
PALETTE = [(0.98, 0.84, 0.55), (0.62, 0.78, 0.55), (0.55, 0.72, 0.90), (0.95, 0.62, 0.55),
           (0.78, 0.66, 0.88), (0.98, 0.72, 0.40), (0.55, 0.82, 0.80), (0.90, 0.60, 0.75),
           (0.80, 0.80, 0.45), (0.70, 0.60, 0.50)]   # couleurs de secours (jamais de blanc)
STYLO_CONTOUR = 211             # stylo des traits provisoires du contour (couleur vive)
COULEUR_CONTOUR = (1.0, 0.35, 0.0)    # orange vif
SURBRILLANCE = (255, 90, 0, 255)      # surbrillance Archicad du contour en cours
TRAIT_CONTOUR_MM = 0.7
STYLO_REPERE = 210              # stylo des reperes (lignes d'alignement)
COULEUR_REPERE = (0.0, 0.65, 1.0)     # bleu clair
TRAIT_REPERE_MM = 0.13
REPERE_M = 12.0                 # longueur des reperes de chaque cote du point (m)          # epaisseur des traits provisoires
MARQUE_MM = 2.5                 # taille du repere sur le 1er point
FERMETURE_MM = 1.5              # clic a moins de 1,5 mm (papier) du 1er point = contour ferme
STYLO_TRAIT = 212
STYLO_BLANC = 213
STYLO_BANDEAU = 214

PORT = 19723
MEMOIRE = "tableaux_surfaces.json"
REGLAGES = "tableau_surfaces_reglages.json"

# --- nomenclature (pastilles numerotees + legende en bas du plan)
NOM_COULEUR = (0.62, 0.10, 0.10)  # rouge sombre : cercles et numeros (style plan de presentation)
NOM_TITRE = ""                    # vide = automatique : "Plan du rez-de-chaussee", "Plan du R+1"...
NOM_PASTILLE_MM = 7.0             # diametre de la pastille (mm papier)
NOM_TEXTE_PASTILLE_MM = 3.0       # hauteur du numero dans la pastille
NOM_LIGNE_MM = 6.0                # hauteur d'une ligne de la legende
NOM_COLONNE_MM = 55.0             # largeur d'une colonne de la legende
NOM_LIGNES_MAX = 6                # lignes par colonne avant de passer a la colonne suivante
NOM_BANDEAU_MM = 6.0
NOM_TEXTE_MM = 2.5
NOM_ECART_MM = 15.0          # espace entre le bas du plan et la legende
STYLO_LEGENDE = 209
STYLO_GRIS = 208
NOM_MEMOIRE = "nomenclature.json"          # guids dessines (exclus de la baguette magique)
NOM_POSITIONS = "nomenclature_positions.json"
NOM_REGLAGES = "nomenclature_reglages.json"
NOM_ORDRES = ("Automatique (sens de lecture)", "En cliquant les pieces dans l'ordre")
SORTIES = ("Tableau des surfaces", "Nomenclature seule (sans tableau)", "Tableau + nomenclature")
NOM_LEGENDES = ("Separee, en bas du plan (deplacable)", "Integree au tableau des surfaces (colonne N°)")
NOM_FORMATS = ("1, 2, 3...", "01, 02, 03...", "Par niveau : 001 (RDC), 101 (R+1)...")


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


def ok_items(items: list[dict]) -> list[dict]:
    return [item for item in items if "error" not in item]


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
    color = saved.get("couleur")
    if not (isinstance(color, list) and len(color) == 3):
        color = list(COULEUR_BANDEAU)
    return {
        "couleur": [float(c) for c in color],
        "titre_habitables": str(saved.get("titre_habitables", TITRE_HABITABLES)),
        "titre_annexes": str(saved.get("titre_annexes", TITRE_ANNEXES)),
        "texte_total": str(saved.get("texte_total", TEXTE_TOTAL)).upper(),
        "mots_annexes": str(saved.get("mots_annexes", MOTS_ANNEXES)),
        "codes_couleur": bool(saved.get("codes_couleur", True)),
        "sortie": saved.get("sortie") if saved.get("sortie") in SORTIES else SORTIES[0],
    }


# ----------------------------------------------------------------- outils ---
def polygon_area(points: list[dict]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:] + points[:1]):
        total += a["x"] * b["y"] - b["x"] * a["y"]
    return abs(total) / 2.0


def format_area(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def story_label(index: int) -> str:
    if index == 0:
        return "RDC"
    if index > 0:
        return f"R+{index}"
    return "Sous-sol" if index == -1 else f"Sous-sol {-index}"


# ---------------------------------------------------------------- lecture ---
def read_zones() -> list[dict]:
    elements = tapir("GetElementsByType", {"elementType": "Zone"}).get("elements", [])
    if not elements:
        return []
    details = tapir("GetDetailsOfElements", {"elements": elements}).get("detailsOfElements", [])
    zones = []
    for element, detail in zip(elements, details):
        if "error" in detail:
            continue
        d = detail.get("details", {})
        zones.append({
            "element": element,
            "story": detail.get("floorIndex", 0),
            "number": d.get("numberStr", ""),
            "name": d.get("name", ""),
            "category": d.get("categoryAttributeId", {}).get("guid"),
            "outline": d.get("polygonOutline", []),
            "holes": d.get("holes", []),
        })
    return zones


def fill_areas(zones: list[dict]) -> None:
    try:  # 1) surface calculee par Archicad
        names = api("API.GetAllPropertyNames").get("properties", [])
        builtin = [p["nonLocalizedName"] for p in names if p.get("type") == "BuiltIn" and "nonLocalizedName" in p]
        preferred = ["Zone_CalculatedArea", "Zone_MeasuredArea", "Zone_NetArea", "Zone_ZoneArea", "Zone_GrossArea"]
        choice = next((n for n in preferred if n in builtin), None) or \
            next((n for n in builtin if n.startswith("Zone_") and "Area" in n), None)
        if choice:
            pid = api("API.GetPropertyIds", {"properties": [{"type": "BuiltIn", "nonLocalizedName": choice}]})["properties"][0]["propertyId"]
            values = api("API.GetPropertyValuesOfElements", {
                "elements": [z["element"] for z in zones], "properties": [{"propertyId": pid}],
            }).get("propertyValuesForElements", [])
            for zone, value in zip(zones, values):
                pv = value.get("propertyValues", [{}])[0].get("propertyValue", {})
                if pv.get("status", "normal") == "normal" and isinstance(pv.get("value"), (int, float)):
                    zone["area"] = float(pv["value"])
    except Exception as error:  # noqa: BLE001
        print(f"(Surface Archicad indisponible, calcul geometrique : {error})")
    for zone in zones:  # 2) a defaut : contour moins les trous
        if "area" not in zone:
            area = polygon_area(zone["outline"]) if len(zone["outline"]) >= 3 else 0.0
            for hole in zone["holes"]:
                pts = hole.get("polygonOutline", [])
                if len(pts) >= 3:
                    area -= polygon_area(pts)
            zone["area"] = max(area, 0.0)


def fill_categories(zones: list[dict]) -> None:
    guids = sorted({z["category"] for z in zones if z["category"]})
    names = {}
    if guids:
        try:
            cats = tapir("GetZoneCategories", {"attributeIds": [{"attributeId": {"guid": g}} for g in guids],
                                               "fields": ["categoryCode", "color"]}).get("zoneCategories", [])
            for guid, cat in zip(guids, cats):
                if "error" not in cat:
                    c = cat.get("color", {})
                    names[guid] = (cat.get("name", ""), (c.get("red", 0.8), c.get("green", 0.8), c.get("blue", 0.8)))
        except Exception:  # noqa: BLE001
            pass
    for zone in zones:
        zone["category_name"], zone["color"] = names.get(zone["category"], ("", (0.8, 0.8, 0.8)))


def make_colors_unique(zones: list[dict]) -> None:
    """Deux zones de meme couleur : la suivante est eclaircie ou assombrie (une couleur par zone)."""
    seen = []
    for index, zone in enumerate(zones):
        if is_whiteish(zone["color"]):                       # pas de blanc : couleur de la palette
            zone["color"] = PALETTE[index % len(PALETTE)]
        base, color, step = zone["color"], zone["color"], 0
        light = sum(base) / 3 > 0.6          # couleur claire : on assombrit d'abord
        while any(math.dist(color, other) < 0.12 for other in seen) and step < 12:
            step += 1
            amount = 0.18 * ((step + 1) // 2)
            if (step % 2 == 1) != light:
                color = tuple(min(1.0, c + (1.0 - c) * amount) for c in base)
            else:
                color = tuple(max(0.0, c * (1.0 - amount)) for c in base)
        zone["color"] = color
        seen.append(color)


def is_annex(zone: dict, words: list[str]) -> bool:
    text = f"{zone['name']} {zone['category_name']}".lower()
    return any(word and word in text for word in words)


# ------------------------------------------------------------------ stylos ---
def set_pens(color: list[float], zones: list[dict]) -> bool:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables]
    details = [t for t in tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                ).get("penTables", []) if "error" not in t]
    table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
    if table is None:
        return False
    widths = {p.get("index"): p.get("width", 0.1) for p in table.get("pens", [])}

    def pen(index, rgb, description):
        return {"index": index, "width": widths.get(index, 0.1), "description": description,
                "color": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}

    answer = tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
        "attributeId": table["attributeId"], "name": table["name"], "pens": [
            pen(STYLO_TRAIT, (0.0, 0.0, 0.0), "Tableau des surfaces - texte et traits"),
            pen(STYLO_BLANC, (1.0, 1.0, 1.0), "Tableau des surfaces - titre"),
            pen(STYLO_BANDEAU, color, "Tableau des surfaces - bandeau"),
            pen(STYLO_CONTOUR, COULEUR_CONTOUR, "Tableau des surfaces - contour en cours"),
            pen(STYLO_REPERE, COULEUR_REPERE, "Tableau des surfaces - reperes"),
        ] + [pen(z["pen"], z["color"], f"Tableau des surfaces - {z['name'] or z['number']}"[:120])
             for z in zones if z.get("pen")]}]})
    return "error" not in answer.get("attributeIds", [{}])[0]


def solid_fill_id() -> dict | None:
    fills = tapir("GetAttributesByType", {"attributeType": "Fill"}).get("attributes", [])
    ids = [{"attributeId": f["attributeId"]} for f in fills]
    for fill in tapir("GetFills", {"attributeIds": ids, "fields": ["subType"]}).get("fills", []):
        if fill.get("subType") == "Solid":
            return fill["attributeId"]
    return None


# ------------------------------------------------------------------ zones ---
def zone_categories() -> list[tuple[str, dict, tuple]]:
    """(nom, attributeId, couleur) des categories de zones du projet."""
    attrs = [a for a in tapir("GetAttributesByType", {"attributeType": "ZoneCategory"}).get("attributes", [])
             if "attributeId" in a]
    colors = {}
    if attrs:
        try:
            cats = tapir("GetZoneCategories", {"attributeIds": [{"attributeId": a["attributeId"]} for a in attrs],
                                               "fields": ["color"]}).get("zoneCategories", [])
            for a, cat in zip(attrs, cats):
                c = cat.get("color", {}) if isinstance(cat, dict) else {}
                colors[a["attributeId"]["guid"]] = (c.get("red", 1.0), c.get("green", 1.0), c.get("blue", 1.0))
        except Exception:  # noqa: BLE001
            pass
    return [(a.get("name", "?"), a["attributeId"], colors.get(a["attributeId"]["guid"], (1.0, 1.0, 1.0))) for a in attrs]


def is_whiteish(color) -> bool:
    return min(color) > 0.92


def auto_category(categories: list, used_guids: list[str]) -> dict | None:
    """Une categorie coloree (pas blanche), de preference pas encore utilisee : une couleur par piece."""
    colored = [c for c in categories if not is_whiteish(c[2])]
    if not colored:
        return None
    unused = [c for c in colored if c[1]["guid"] not in used_guids]
    pick = unused[0] if unused else colored[len(used_guids) % len(colored)]
    return pick[1]


def next_zone_number(zones: list[dict]) -> int:
    numbers = [int(z["number"]) for z in zones if str(z.get("number", "")).strip().isdigit()]
    return (max(numbers) + 1) if numbers else 1


def new_zone(story: int, name: str, number: int, category: dict | None, geometry: dict, stamp=None) -> str | None:
    """Cree une zone ; renvoie un message d'erreur ou None."""
    data = {"floorIndex": story, "name": name, "numberStr": f"{number:02d}", "geometry": geometry}
    if category:
        data["categoryAttributeId"] = category
    if stamp:
        data["stampPosition"] = stamp
    result = tapir("CreateZones", {"zonesData": [data]}).get("elements", [{}])
    item = result[0] if result else {}
    if "error" in item:
        return str(item["error"].get("message", item["error"]) if isinstance(item["error"], dict) else item["error"])
    return None


def focus_archicad() -> None:
    """Remet la fenetre d'Archicad au premier plan (Windows), pour que le clic aille au plan."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if "archicad" in buffer.value.lower():
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(callback, 0)
        if found:
            user32.ShowWindow(found[0], 9)          # SW_RESTORE si reduite
            user32.SetForegroundWindow(found[0])
    except Exception:  # noqa: BLE001
        pass


TRAITS = {"Droits (0 / 90 degres)": 90.0, "Droits + 45 degres": 45.0, "Libres": 0.0}


def constrain(prev: dict, point: dict, step_deg: float) -> dict:
    """Redresse le trait prev -> point sur l'angle autorise le plus proche (0/90 ou 45 degres)."""
    if step_deg <= 0:
        return point
    dx, dy = point["x"] - prev["x"], point["y"] - prev["y"]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return point
    step = math.radians(step_deg)
    angle = round(math.atan2(dy, dx) / step) * step
    length = dx * math.cos(angle) + dy * math.sin(angle)
    return {"x": prev["x"] + length * math.cos(angle), "y": prev["y"] + length * math.sin(angle)}


def is_aligned(a: dict, b: dict, step_deg: float) -> bool:
    if step_deg <= 0:
        return True
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    if math.hypot(dx, dy) < 1e-6:
        return True
    step = math.radians(step_deg)
    angle = math.atan2(dy, dx)
    return abs(angle - round(angle / step) * step) < math.radians(0.5)


def closing_corner(points: list[dict], step_deg: float) -> dict | None:
    """Coin a ajouter pour fermer le contour avec des traits droits (None si deja aligne)."""
    first, last = points[0], points[-1]
    if is_aligned(last, first, step_deg):
        return None
    best, best_area = None, -1.0
    for corner in ({"x": first["x"], "y": last["y"]}, {"x": last["x"], "y": first["y"]}):
        poly = points + [corner]
        area = abs(sum(p["x"] * q["y"] - q["x"] * p["y"] for p, q in zip(poly, poly[1:] + poly[:1])))
        if area > best_area:
            best, best_area = corner, area
    return best


NOMS_PIECES = [
    "Chambre", "Chambre 1", "Chambre 2", "Chambre 3", "Chambre parentale", "Chambre d'amis", "Chambre d'enfant",
    "Suite parentale", "Séjour", "Salon", "Salle à manger", "Pièce à vivre", "Cuisine", "Cuisine ouverte",
    "Kitchenette", "Office", "Cellier", "Buanderie", "Arrière-cuisine", "Salle de bain", "Salle d'eau", "SdB", "SDE",
    "WC", "WC visiteurs", "Toilettes", "Dressing", "Placard", "Rangement", "Débarras", "Entrée", "Hall", "Dégagement",
    "Couloir", "Circulation", "Palier", "Escalier", "Bureau", "Bibliothèque", "Salle de jeux", "Salle de sport",
    "Salle de cinéma", "Mezzanine", "Véranda", "Terrasse", "Terrasse couverte", "Balcon", "Loggia", "Patio", "Cour",
    "Jardin", "Garage", "Carport", "Parking", "Atelier", "Local technique", "Chaufferie", "Cave", "Grenier",
    "Combles", "Abri", "Piscine", "Pool house", "Gardiennage", "Boyerie", "Chambre de bonne", "Magasin", "Boutique",
    "Accueil", "Salle de réunion", "Open space", "Espace de travail", "Salle de pause", "Vestiaire", "Infirmerie",
]


def simplify(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def suggest_names(typed: str, extra: list[str], limit: int = 8) -> list[str]:
    """Noms de pieces qui correspondent a ce qui est tape (debut de mot d'abord, sans tenir compte des accents)."""
    key = simplify(typed.strip())
    pool, seen = [], set()
    for name in list(extra) + NOMS_PIECES:
        if name and simplify(name) not in seen:
            seen.add(simplify(name))
            pool.append(name)
    if not key:
        return pool[:limit]
    starts = [n for n in pool if simplify(n).startswith(key)]
    words = [n for n in pool if n not in starts and any(w.startswith(key) for w in simplify(n).replace("'", " ").split())]
    inside = [n for n in pool if n not in starts and n not in words and key in simplify(n)]
    return (starts + words + inside)[:limit]


def highlight(guids: list[str]) -> None:
    """Surbrillance Archicad des elements (liste vide = enlever la surbrillance)."""
    try:
        tapir("HighlightElements", {"elements": [{"elementId": {"guid": g}} for g in guids],
                                    "highlightedColors": [list(SURBRILLANCE)] * len(guids)})
    except Exception:  # noqa: BLE001
        pass


def drop_collinear(points: list[dict]) -> list[dict]:
    """Retire les sommets inutiles (alignes avec leurs voisins)."""
    pts = list(points)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b["x"] - a["x"]) * (c["y"] - b["y"]) - (b["y"] - a["y"]) * (c["x"] - b["x"])
            if abs(cross) < 1e-6:
                pts.pop(i)
                changed = True
                break
    return pts


def pick_point(prompt: str) -> tuple[dict | None, str]:
    """Attend un clic dans Archicad. Renvoie (point, "") ou (None, raison)."""
    try:
        answer = tapir("GetPointFromUser", {"prompt": prompt}, timeout=3600)
    except Exception as error:  # noqa: BLE001
        return None, str(error)
    pos = answer.get("position") if isinstance(answer, dict) else None
    if not pos or "error" in answer:
        return None, str(answer.get("error", "")) if isinstance(answer, dict) else ""
    return {"x": pos["x"], "y": pos["y"]}, ""


def selected_outlines() -> list[tuple[list[dict], list[dict], list[dict]]]:
    """Contours (points, arcs, trous) des polylignes et remplissages selectionnes."""
    try:
        selection = api("API.GetSelectedElements").get("elements", [])
    except Exception:  # noqa: BLE001
        selection = tapir("GetSelectedElements").get("elements", [])
    if not selection:
        return []
    details = tapir("GetDetailsOfElements", {"elements": selection, "fields": ["type", "details"]}
                    ).get("detailsOfElements", [])
    outlines = []
    for detail in details:
        if "error" in detail:
            continue
        kind = detail.get("type", "")
        d = detail.get("details", {})
        if kind in ("PolyLine", "Hatch"):
            pts = [{"x": c["x"], "y": c["y"]} for c in d.get("coordinates", [])]   # tel quel : les arcs s'y referent
            if len(pts) >= 3:
                holes = [{"polygonOutline": h.get("polygonOutline", []), "polygonArcs": h.get("polygonArcs", [])}
                         for h in d.get("holes", [])] if kind == "Hatch" else []
                outlines.append((pts, d.get("arcs", []), holes))
    return outlines


def centroid(points: list[dict]) -> dict:
    if len(points) > 3 and abs(points[0]["x"] - points[-1]["x"]) < 1e-9 and abs(points[0]["y"] - points[-1]["y"]) < 1e-9:
        points = points[:-1]
    a = cx = cy = 0.0
    for p, q in zip(points, points[1:] + points[:1]):
        cross = p["x"] * q["y"] - q["x"] * p["y"]
        a += cross
        cx += (p["x"] + q["x"]) * cross
        cy += (p["y"] + q["y"]) * cross
    if abs(a) < 1e-12:
        return {"x": sum(p["x"] for p in points) / len(points), "y": sum(p["y"] for p in points) / len(points)}
    return {"x": cx / (3 * a), "y": cy / (3 * a)}


# --------------------------------------------------------- baguette magique ---
BAGUETTE_TOL = 0.01             # m : petits jours entre murs/traits refermes (comme la baguette d'Archicad)


def _own_guids() -> set[str]:
    """Elements dessines par nos scripts (tableau, ombres) : ils ne delimitent pas les pieces."""
    own = set()
    for name in (MEMOIRE, "ombres.json", "nomenclature.json", "personnages.json", "trame.json", "meubles.json"):
        for guids in load_json(name).values():
            if isinstance(guids, list):
                own.update(guids)
    return own


def wall_body(detail: dict) -> list[list[tuple]]:
    """Corps du mur en plan. Mur droit : rectangle plein (les baies de portes sont refermees,
    comme pour la baguette magique qui s'arrete au droit des murs)."""
    d = detail.get("details", {})
    polys = [[(c["x"], c["y"]) for c in p.get("coordinates", [])] for p in detail.get("floorPlanPolygons", [])]
    polys = [p for p in polys if len(p) >= 3]
    beg, end = d.get("begCoordinate"), d.get("endCoordinate")
    if not polys or not beg or not end or abs(d.get("arcAngle", 0.0) or 0.0) > 1e-6 or d.get("geometryType") == "Polygonal":
        return polys
    bx, by = beg["x"], beg["y"]
    length = math.hypot(end["x"] - bx, end["y"] - by)
    if length < 1e-6:
        return polys
    ux, uy = (end["x"] - bx) / length, (end["y"] - by) / length
    nx, ny = -uy, ux
    ss = [(x - bx) * ux + (y - by) * uy for p in polys for x, y in p]
    ts = [(x - bx) * nx + (y - by) * ny for p in polys for x, y in p]
    s1, s2, t1, t2 = min(ss), max(ss), min(ts), max(ts)
    return [[(bx + s * ux + t * nx, by + s * uy + t * ny) for s, t in ((s1, t1), (s2, t1), (s2, t2), (s1, t2))]]


class MagicWand:
    """Trouve l'espace ferme autour d'un clic, delimite par les murs, poteaux, lignes et polylignes."""

    def __init__(self, story: int):
        from shapely.geometry import LineString, Polygon, box
        from shapely.ops import unary_union

        own = _own_guids()
        shapes = []
        for kind in ("Wall", "Column"):
            elems = tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
            if not elems:
                continue
            fields = ["floorIndex", "floorPlanPolygons"] + (["details"] if kind == "Wall" else [])
            for detail in tapir("GetDetailsOfElements", {"elements": elems, "fields": fields}).get("detailsOfElements", []):
                if "error" in detail or detail.get("floorIndex") != story:
                    continue
                for poly in (wall_body(detail) if kind == "Wall" else
                             [[(c["x"], c["y"]) for c in p.get("coordinates", [])] for p in detail.get("floorPlanPolygons", [])]):
                    if len(poly) >= 3:
                        g = Polygon(poly).buffer(0)
                        if not g.is_empty:
                            shapes.append(g.buffer(BAGUETTE_TOL, join_style=2))
        for kind in ("Line", "PolyLine"):
            elems = [e for e in tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
                     if e["elementId"]["guid"] not in own]
            if not elems:
                continue
            for detail in tapir("GetDetailsOfElements", {"elements": elems, "fields": ["floorIndex", "details"]}
                                ).get("detailsOfElements", []):
                if "error" in detail or detail.get("floorIndex") != story:
                    continue
                d = detail.get("details", {})
                if kind == "Line" and "begCoordinate" in d:
                    pts = [(d["begCoordinate"]["x"], d["begCoordinate"]["y"]), (d["endCoordinate"]["x"], d["endCoordinate"]["y"])]
                else:
                    pts = [(c["x"], c["y"]) for c in d.get("coordinates", [])]
                if len(pts) >= 2:
                    shapes.append(LineString(pts).buffer(BAGUETTE_TOL, cap_style=3, join_style=2))
        self.empty = not shapes
        if self.empty:
            self.spaces = []
            return
        obstacles = unary_union(shapes)
        x1, y1, x2, y2 = obstacles.bounds
        self.frame = box(x1 - 1, y1 - 1, x2 + 1, y2 + 1)
        free = self.frame.difference(obstacles)
        self.spaces = list(getattr(free, "geoms", [free]))

    def face(self, point: dict):
        """Polygone (shapely) de la piece cliquee, ou None si l'espace n'est pas ferme."""
        from shapely.geometry import Point
        p = Point(point["x"], point["y"])
        for space in self.spaces:
            if space.contains(p):
                if space.exterior.intersects(self.frame.exterior):
                    return None                       # l'espace "fuit" vers l'exterieur : pas ferme
                room = space.buffer(BAGUETTE_TOL, join_style=2).simplify(0.001)
                return room if room.geom_type == "Polygon" else None
        return None


def face_geometry(face) -> tuple[dict, dict]:
    """Geometrie de zone Tapir (contour + trous) et position du tampon."""
    outline = [{"x": x, "y": y} for x, y in face.exterior.coords]
    holes = [{"polygonOutline": [{"x": x, "y": y} for x, y in ring.coords]} for ring in face.interiors]
    geometry = {"polygonCoordinates": outline}
    if holes:
        geometry["holes"] = holes
    inside = face.representative_point()
    return geometry, {"x": inside.x, "y": inside.y}


# ------------------------------------------------------------------ dessin ---
class Drawing:
    def __init__(self, story: int, solid: dict | None):
        self.story, self.solid = story, solid
        self.mm = ECHELLE / 1000.0
        self.texts, self.hatches, self.lines = [], [], []

    def text(self, content, x, y, anchor, bold=False, size=TEXTE_MM, pen=STYLO_TRAIT):
        justification = {"L": "Left", "M": "Center", "R": "Right"}[anchor[0]]
        self.texts.append({
            "coordinate": {"x": x, "y": y, "z": 0.0}, "text": content, "floorIndex": self.story,
            "style": {"height": size, "bold": bold, "penIndex": pen, "justification": justification,
                      "anchor": {"L": "LeftMiddle", "M": "MiddleMiddle", "R": "RightMiddle"}[anchor[0]]},
        })

    def line(self, xa, ya, xb, yb, weight=TRAIT_FIN_MM):
        self.lines.append({"floorInd": self.story, "linePenIndex": STYLO_TRAIT, "penWeightMm": weight,
                           "coordinates": [{"x": xa, "y": ya}, {"x": xb, "y": yb}]})

    def band(self, x1, y1, x2, y2):
        self.swatch(x1, y1, x2, y2, STYLO_BANDEAU)

    def swatch(self, x1, y1, x2, y2, pen):
        hatch = {"floorInd": self.story, "showArea": False,
                 "coordinates": [{"x": x1, "y": y1}, {"x": x2, "y": y1}, {"x": x2, "y": y2}, {"x": x1, "y": y2}],
                 "fillPenIndex": pen, "fillBackgroundPenIndex": pen, "contourPenIndex": pen}
        if self.solid:
            hatch["fillId"] = self.solid
        self.hatches.append(hatch)

    def table(self, x0, top, title, groups, total_label, show_headings) -> float:
        """Dessine un tableau ; renvoie l'ordonnee de son bas."""
        mm = self.mm
        width = (COL_NOM_MM + COL_SURFACE_MM) * mm
        sep = x0 + COL_NOM_MM * mm
        row = LIGNE_MM * mm
        pad = 1.2 * mm
        value_x = sep + COL_SURFACE_MM * mm / 2       # surfaces centrees dans leur colonne

        band_bottom = top - BANDEAU_MM * mm
        self.band(x0, band_bottom, x0 + width, top)
        self.text(title, x0 + width / 2, (top + band_bottom) / 2, "M", bold=True, size=TITRE_MM, pen=STYLO_BLANC)

        y = band_bottom
        total = 0.0
        row_lines = []
        for heading, zones in groups:
            if show_headings:
                y -= row
                self.text(heading, x0 + pad, y + row / 2, "L", bold=True)
                row_lines.append(y)
            for zone in zones:
                y -= row
                label = zone["name"] or zone["number"] or "Zone"
                text_x = x0 + pad
                if zone.get("nom_label"):                                   # pastille N° (nomenclature integree)
                    r = min(row * 0.42, 2.4 * mm)
                    c = (text_x + r, y + row / 2)
                    ring = [{"x": c[0] + r * math.cos(2 * math.pi * k / 32), "y": c[1] + r * math.sin(2 * math.pi * k / 32)}
                            for k in range(32)]
                    hatch = {"floorInd": self.story, "showArea": False, "coordinates": ring,
                             "fillPenIndex": STYLO_BLANC, "fillBackgroundPenIndex": STYLO_BLANC, "contourPenIndex": STYLO_BLANC}
                    if self.solid:
                        hatch["fillId"] = self.solid
                    self.hatches.append(hatch)
                    self.lines.append({"floorInd": self.story, "linePenIndex": STYLO_TRAIT, "penWeightMm": 0.2,
                                       "coordinates": ring + [ring[0]]})
                    self.texts.append({"coordinate": {"x": c[0], "y": c[1], "z": 0.0}, "text": zone["nom_label"],
                                       "floorIndex": self.story, "style": {"height": 1.8, "bold": True, "penIndex": STYLO_TRAIT,
                                                                            "anchor": "MiddleMiddle", "justification": "Center"}})
                    text_x += 2 * r + pad
                if zone.get("pen"):                                         # code couleur de la zone
                    self.swatch(text_x, y + row * 0.2, text_x + CASE_MM * mm, y + row * 0.8, zone["pen"])
                    text_x = text_x + pad + CASE_MM * mm
                self.text(label, text_x, y + row / 2, "L")
                self.text(format_area(zone["area"]) + UNITE, value_x, y + row / 2, "M")
                total += zone["area"]
                row_lines.append(y)
        body_bottom = y
        if TRAITS_ENTRE_LIGNES:
            for line_y in row_lines[:-1]:                                     # trait sous chaque ligne
                self.line(x0, line_y, x0 + width, line_y)
        y -= row * 1.1
        self.text(total_label.upper(), x0 + COL_NOM_MM * mm / 2, y + row * 0.55, "M", bold=True, size=TEXTE_MM * 1.1)
        self.text(format_area(total) + UNITE, value_x, y + row * 0.55, "M", bold=True, size=TEXTE_MM * 1.1)

        self.line(x0, band_bottom, x0 + width, band_bottom)                       # sous le bandeau
        self.line(x0, body_bottom, x0 + width, body_bottom, TRAIT_CADRE_MM)       # au-dessus du total
        self.line(sep, band_bottom, sep, y)                                       # trait vertical
        for xa, ya, xb, yb in ((x0, top, x0 + width, top), (x0 + width, top, x0 + width, y),
                               (x0 + width, y, x0, y), (x0, y, x0, top)):
            self.line(xa, ya, xb, yb, TRAIT_CADRE_MM)                             # cadre
        return y


# -------------------------------------------------------------------- run ---
def plan_extent(story: int, zones: list[dict]) -> tuple[float, float, float, float]:
    """Emprise de TOUT le dessin de l'etage (murs, poteaux, dalles, objets, zones), pas seulement des
    pieces deja creees : le tableau et la legende se posent hors du plan et ne bougent plus quand
    on ajoute des pieces."""
    xs = [p["x"] for z in zones for p in z["outline"]]
    ys = [p["y"] for z in zones for p in z["outline"]]
    for kind in ("Wall", "Column", "Slab", "Object", "Window", "Door"):
        try:
            elems = tapir("GetElementsByType", {"elementType": kind}).get("elements", [])
            if not elems:
                continue
            floors = tapir("GetDetailsOfElements", {"elements": elems, "fields": ["floorIndex"]}).get("detailsOfElements", [])
            mine = [e for e, d in zip(elems, floors) if isinstance(d, dict) and d.get("floorIndex") == story]
            if not mine:
                continue
            for item in tapir("Get3DBoundingBoxes", {"elements": mine}).get("boundingBoxes3D", []):
                box = item.get("boundingBox3D") if isinstance(item, dict) else None
                if box:
                    xs += [box["xMin"], box["xMax"]]
                    ys += [box["yMin"], box["yMax"]]
        except Exception:  # noqa: BLE001
            continue
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def group_by_story(zones: list[dict]) -> list[tuple[str, list[dict]]]:
    stories = sorted({z["story"] for z in zones})
    return [(story_label(s), sorted((z for z in zones if z["story"] == s),
                                    key=lambda z: (z.get("nom_label") or "~", z["number"], z["name"])))
            for s in stories]


def run(action: str, settings: dict) -> str:
    stories = tapir("GetStories")
    current = stories.get("actStory", 0)
    project = tapir("GetProjectInfo")
    project_key = project.get("projectPath") or project.get("projectName") or "Sans titre"

    # supprimer les tableaux precedents de ce projet (toutes versions)
    memory = load_json(MEMOIRE)
    old = [g for key, guids in memory.items() if key.split("|")[0] == project_key for g in guids]
    if old:
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in old]})
        except Exception:  # noqa: BLE001
            pass
    memory = {k: v for k, v in memory.items() if k.split("|")[0] != project_key}
    save_json(MEMOIRE, memory)
    if action == "supprimer":
        return "Tableaux des surfaces supprimes."

    zones = read_zones()
    if not zones:
        return "Aucune zone dans le projet : rien a mettre dans le tableau."
    nom_positions = load_json(NOM_POSITIONS)
    for zone in zones:   # nomenclature integree : numero de la piece dans le tableau
        info = nom_positions.get(f"{project_key}|{zone['story']}", {})
        if info.get("integree"):
            zone["nom_label"] = info.get("numeros", {}).get(zone["element"]["elementId"]["guid"])
    fill_areas(zones)
    fill_categories(zones)
    words = [w.strip().lower() for w in settings["mots_annexes"].split(",") if w.strip()]
    habitable = [z for z in zones if not is_annex(z, words)]
    annexes = [z for z in zones if is_annex(z, words)]

    ordered = [z for _, g in group_by_story(habitable) for z in g] + [z for _, g in group_by_story(annexes) for z in g]
    if settings.get("codes_couleur", True):
        make_colors_unique(ordered)
        for i, zone in enumerate(ordered[:STYLOS_ZONES]):
            zone["pen"] = 255 - i
    warning = "" if set_pens(settings["couleur"], ordered) else " Attention : couleurs non appliquees (stylos 212-214)."
    drawing = Drawing(current, solid_fill_id())

    plan = [z for z in zones if z["story"] == current] or zones
    _, _, x_max, y_max = plan_extent(current, plan)
    mm = drawing.mm
    x0 = x_max + ECART_PLAN_MM * mm
    top = y_max

    if habitable:
        top = drawing.table(x0, top, settings["titre_habitables"], group_by_story(habitable),
                            settings["texte_total"], show_headings=True) - ECART_TABLEAUX_MM * mm
    if annexes:
        groups = group_by_story(annexes)
        drawing.table(x0, top, settings["titre_annexes"], groups, settings["texte_total"],
                      show_headings=len(groups) > 1)

    created = []
    created += ok_items(tapir("CreateHatches", {"hatchesData": drawing.hatches}).get("elements", []))
    created += ok_items(tapir("CreatePolylines", {"polylinesData": drawing.lines}).get("elements", []))
    created += ok_items(tapir("CreateTexts", {"textsData": drawing.texts}).get("elements", []))
    try:
        tapir("CreateGroups", {"elementGroups": [{"elements": created}]})
    except Exception:  # noqa: BLE001
        pass

    memory[f"{project_key}|tableau"] = [e["elementId"]["guid"] for e in created if "elementId" in e]
    save_json(MEMOIRE, memory)
    total_h = sum(z["area"] for z in habitable)
    total_a = sum(z["area"] for z in annexes)
    return (f"Tableaux dessines : {len(habitable)} zone(s) habitable(s) = {format_area(total_h)} m2, "
            f"{len(annexes)} annexe(s) = {format_area(total_a)} m2.{warning}")


# ---------------------------------------------------------------- fenetre ---
# ------------------------------------------------------------ nomenclature ---
def nom_load_settings() -> dict:
    saved = load_json(NOM_REGLAGES)
    color = saved.get("couleur")
    return {
        "couleur": ([float(c) for c in color] if isinstance(color, list) and len(color) == 3
                    and [round(c, 2) for c in color] != [0.93, 0.11, 0.14] else list(NOM_COULEUR)),
        "titre": "" if str(saved.get("titre", NOM_TITRE)).strip().upper() in ("LÉGENDE", "LEGENDE") else str(saved.get("titre", NOM_TITRE)),
        "ordre": saved.get("ordre") if saved.get("ordre") in NOM_ORDRES else NOM_ORDRES[0],
        "format": saved.get("format") if saved.get("format") in NOM_FORMATS else NOM_FORMATS[0],
        "surfaces": bool(saved.get("surfaces", False)),
        "legende": saved.get("legende") if saved.get("legende") in NOM_LEGENDES else NOM_LEGENDES[0],
    }


def nom_set_pens(color) -> None:
    tables = tapir("GetAttributesByType", {"attributeType": "PenTable"}).get("attributes", [])
    ids = [{"attributeId": t["attributeId"]} for t in tables]
    details = [t for t in tapir("GetPenTables", {"attributeIds": ids, "fields": ["isActiveForModel", "pens"]}
                                  ).get("penTables", []) if "error" not in t]
    table = next((t for t in details if t.get("isActiveForModel")), details[0] if details else None)
    if table is None:
        return
    widths = {p.get("index"): p.get("width", 0.1) for p in table.get("pens", [])}

    def pen(index, rgb, description):
        return {"index": index, "width": widths.get(index, 0.1), "description": description,
                "color": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}

    tapir("CreatePenTables", {"overwriteExisting": True, "penTableDataArray": [{
        "attributeId": table["attributeId"], "name": table["name"], "pens": [
            pen(STYLO_TRAIT, (0.0, 0.0, 0.0), "Texte et traits"),
            pen(STYLO_BLANC, (1.0, 1.0, 1.0), "Blanc"),
            pen(STYLO_LEGENDE, color, "Nomenclature - cercles et numeros"),
            pen(STYLO_GRIS, (0.33, 0.33, 0.33), "Nomenclature - textes de la legende")]}]})


def empty_fill_id() -> dict | None:
    fills = tapir("GetAttributesByType", {"attributeType": "Fill"}).get("attributes", [])
    ids = [{"attributeId": f["attributeId"]} for f in fills]
    for fill in tapir("GetFills", {"attributeIds": ids, "fields": ["subType"]}).get("fills", []):
        if fill.get("subType") == "Empty":
            return fill["attributeId"]
    return None


def zone_center(zone: dict) -> dict:
    """Point bien a l'interieur de la piece (pas forcement le centre geometrique)."""
    pts = zone["outline"]
    try:
        from shapely.geometry import Polygon
        poly = Polygon([(p["x"], p["y"]) for p in pts]).buffer(0)
        if not poly.is_empty:
            q = poly.representative_point() if not poly.contains(poly.centroid) else poly.centroid
            return {"x": q.x, "y": q.y}
    except Exception:  # noqa: BLE001
        pass
    return centroid(pts)


def inside(point: dict, pts: list[dict]) -> bool:
    x, y, result = point["x"], point["y"], False
    for a, b in zip(pts, pts[1:] + pts[:1]):
        if (a["y"] > y) != (b["y"] > y) and x < a["x"] + (y - a["y"]) * (b["x"] - a["x"]) / (b["y"] - a["y"]):
            result = not result
    return result


def reading_order(zones: list[dict]) -> list[dict]:
    """Sens de lecture : de haut en bas par bandes, puis de gauche a droite."""
    band = 2.0
    return sorted(zones, key=lambda z: (-round(z["center"]["y"] / band), z["center"]["x"]))


def number_label(index: int, story: int, fmt: str) -> str:
    if fmt == NOM_FORMATS[2]:
        return f"{max(story, 0)}{index:02d}"
    if fmt == NOM_FORMATS[1]:
        return f"{index:02d}"
    return str(index)


def write_zone_numbers(zones: list[dict]) -> bool:
    """Ecrit le numero dans la zone Archicad (propriete Numero de zone), si Archicad l'accepte."""
    try:
        names = api("API.GetAllPropertyNames").get("properties", [])
        builtin = [p["nonLocalizedName"] for p in names if p.get("type") == "BuiltIn"]
        name = next((n for n in ("Zone_ZoneNumber", "Zone_Number") if n in builtin), None)
        if not name:
            return False
        pid = api("API.GetPropertyIds", {"properties": [{"type": "BuiltIn", "nonLocalizedName": name}]}
                    )["properties"][0]["propertyId"]
        result = api("API.SetPropertyValuesOfElements", {"elementPropertyValues": [
            {"elementId": z["element"]["elementId"], "propertyId": pid,
             "propertyValue": {"value": z["label"]}} for z in zones]})
        return all(r.get("success", True) for r in result.get("executionResults", []))
    except Exception:  # noqa: BLE001
        return False


def current_offset(ref_guid: str | None, origin: list | None) -> tuple[float, float]:
    """De combien l'utilisateur a deplace un groupe (d'apres un de ses remplissages)."""
    if not ref_guid or not origin:
        return 0.0, 0.0
    try:
        detail = tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": ref_guid}}],
                                                  "fields": ["details"]}).get("detailsOfElements", [{}])[0]
        pts = detail.get("details", {}).get("coordinates", [])
        if not pts:
            return 0.0, 0.0
        return min(p["x"] for p in pts) - origin[0], min(p["y"] for p in pts) - origin[1]
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


# ------------------------------------------------------------------ dessin ---
class NomDraw:
    def __init__(self, story: int, solid, empty=None):
        self.story, self.solid, self.empty = story, solid, empty
        self.mm = ECHELLE / 1000.0
        self.texts, self.hatches, self.lines = [], [], []

    def text(self, content, x, y, anchor="LeftMiddle", bold=False, size=NOM_TEXTE_MM, pen=None):
        just = {"L": "Left", "M": "Center", "R": "Right"}[anchor[0]]
        self.texts.append({"coordinate": {"x": x, "y": y, "z": 0.0}, "text": content, "floorIndex": self.story,
                           "style": {"height": size, "bold": bold, "penIndex": pen or STYLO_TRAIT,
                                     "anchor": anchor, "justification": just}})

    def hatch(self, pts, pen):
        h = {"floorInd": self.story, "showArea": False, "coordinates": pts,
             "fillPenIndex": pen, "fillBackgroundPenIndex": pen, "contourPenIndex": pen}
        if self.solid:
            h["fillId"] = self.solid
        self.hatches.append(h)

    def line(self, pts, weight=0.18, pen=None):
        self.lines.append({"floorInd": self.story, "linePenIndex": pen or STYLO_TRAIT, "penWeightMm": weight,
                           "coordinates": pts})

    def ghost(self, pts):
        """Remplissage transparent (repere de position du groupe). A defaut de motif vide : fond blanc."""
        h = {"floorInd": self.story, "showArea": False, "coordinates": pts,
             "fillPenIndex": STYLO_BLANC, "fillBackgroundPenIndex": STYLO_BLANC, "contourPenIndex": STYLO_BLANC}
        fill = self.empty or self.solid
        if fill:
            h["fillId"] = fill
        self.hatches.append(h)

    def pastille(self, center, label, diameter_mm=NOM_PASTILLE_MM, text_mm=NOM_TEXTE_PASTILLE_MM):
        """Cercle fin de couleur, sans fond, avec le numero de la meme couleur."""
        r = diameter_mm * self.mm / 2
        ring = [{"x": center["x"] + r * math.cos(2 * math.pi * k / 48),
                 "y": center["y"] + r * math.sin(2 * math.pi * k / 48)} for k in range(48)]
        self.ghost(ring)
        self.line(ring + [ring[0]], 0.25, STYLO_LEGENDE)
        self.text(label, center["x"], center["y"], "MiddleMiddle", size=text_mm, pen=STYLO_LEGENDE)

    def flush(self) -> tuple[list[dict], list[str]]:
        """Cree tout ; renvoie (elements, guids des remplissages dans l'ordre)."""
        hatches = ok_items(tapir("CreateHatches", {"hatchesData": self.hatches}).get("elements", [])) if self.hatches else []
        lines = ok_items(tapir("CreatePolylines", {"polylinesData": self.lines}).get("elements", [])) if self.lines else []
        texts = ok_items(tapir("CreateTexts", {"textsData": self.texts}).get("elements", [])) if self.texts else []
        self.texts, self.hatches, self.lines = [], [], []
        return hatches + lines + texts, [h["elementId"]["guid"] for h in hatches]


def nom_group(elements: list[dict]) -> None:
    if elements:
        try:
            tapir("CreateGroups", {"elementGroups": [{"elements": elements}]})
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------- nom_run ---
def nom_run(action: str, settings: dict, ordered: list[dict] | None = None) -> str:
    story = tapir("GetStories").get("actStory", 0)
    project = tapir("GetProjectInfo")
    key = f"{project.get('projectPath') or project.get('projectName') or 'Sans titre'}|{story}"
    memory, positions = load_json(NOM_MEMOIRE), load_json(NOM_POSITIONS)
    previous = positions.get(key, {})

    # positions choisies a la main avant de tout effacer
    # decalage total = decalage deja retenu + deplacement fait a la main depuis le dernier dessin
    ddx, ddy = current_offset(previous.get("legende_ref"), previous.get("legende_origine"))
    base = previous.get("legende_decalage", [0.0, 0.0])
    legend_offset = (base[0] + ddx, base[1] + ddy)
    moved = {}
    for zone_guid, info in previous.get("pastilles", {}).items():
        dx, dy = current_offset(info.get("ref"), info.get("origine"))
        bx, by = info.get("decalage", [0.0, 0.0])
        if abs(bx + dx) > 1e-6 or abs(by + dy) > 1e-6:
            moved[zone_guid] = (bx + dx, by + dy)

    if memory.get(key):
        try:
            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in memory[key]]})
        except Exception:  # noqa: BLE001
            pass
    memory[key] = []
    if action == "supprimer":
        positions.pop(key, None)
        save_json(NOM_MEMOIRE, memory)
        save_json(NOM_POSITIONS, positions)
        return "Pastilles et legende supprimees."

    zones = [z for z in read_zones() if z["story"] == story and len(z["outline"]) >= 3]
    if not zones:
        save_json(NOM_MEMOIRE, memory)
        return "Aucune zone sur cet etage : cree d'abord les pieces (outil Tableau des surfaces ou Archicad)."
    fill_areas(zones)
    for z in zones:
        z["center"] = zone_center(z)
        z["guid"] = z["element"]["elementId"]["guid"]

    if ordered:                                    # ordre clique, puis le reste en sens de lecture
        rest = [z for z in reading_order(zones) if z["guid"] not in {o["guid"] for o in ordered}]
        zones = [z for o in ordered for z in zones if z["guid"] == o["guid"]] + rest
    elif action == "mettre_a_jour" and previous.get("ordre"):
        rank = {g: i for i, g in enumerate(previous["ordre"])}
        zones = sorted(zones, key=lambda z: (rank.get(z["guid"], 10 ** 6), -z["center"]["y"], z["center"]["x"]))
    else:
        zones = reading_order(zones)
    for i, z in enumerate(zones, start=1):
        z["label"] = number_label(i, story, settings["format"])

    try:
        nom_set_pens(settings["couleur"])
    except Exception:  # noqa: BLE001
        pass
    solid = solid_fill_id()
    try:
        empty = empty_fill_id()
    except Exception:  # noqa: BLE001
        empty = None
    draw = NomDraw(story, solid, empty)
    all_guids, pastilles = [], {}

    # pastilles (un groupe par pastille, deplacable)
    for z in zones:
        dx, dy = moved.get(z["guid"], (0.0, 0.0))
        center = {"x": z["center"]["x"] + dx, "y": z["center"]["y"] + dy}
        draw.pastille(center, z["label"])
        elements, hatch_guids = draw.flush()
        nom_group(elements)
        all_guids += [e["elementId"]["guid"] for e in elements]
        if hatch_guids:
            r = NOM_PASTILLE_MM * draw.mm / 2
            pastilles[z["guid"]] = {"ref": hatch_guids[0], "origine": [center["x"] - r, center["y"] - r],
                                    "decalage": [dx, dy]}

    integrated = settings.get("legende") == NOM_LEGENDES[1]
    numbers = {z["guid"]: z["label"] for z in zones}
    if integrated:                                 # legende = colonne N° du tableau des surfaces
        numbers_written = write_zone_numbers(zones)
        memory[key] = all_guids
        positions[key] = {"legende_ref": None, "legende_origine": None, "legende_decalage": list(legend_offset),
                          "pastilles": pastilles, "ordre": [z["guid"] for z in zones],
                          "numeros": numbers, "integree": True}
        save_json(NOM_MEMOIRE, memory)
        save_json(NOM_POSITIONS, positions)
        kept = f" {len(moved)} pastille(s) laissee(s) a ta place." if moved else ""
        return f"{len(zones)} piece(s) numerotee(s), numeros ajoutes au tableau.{kept}"

    # legende en bas du plan (un groupe, deplacable)
    mm = draw.mm
    plan_x_min, plan_y_min, _, _ = plan_extent(story, zones)   # sous TOUT le plan, pas sous les seules pieces
    columns = max(1, math.ceil(len(zones) / NOM_LIGNES_MAX))
    rows = math.ceil(len(zones) / columns)
    col_w = (NOM_COLONNE_MM + (22 if settings["surfaces"] else 0)) * mm
    width = columns * col_w
    x0 = plan_x_min + legend_offset[0]
    top = plan_y_min - NOM_ECART_MM * mm + legend_offset[1]
    # legende libre, style plan de presentation :
    #   Plan du rez-de-chaussee            (titre gris)
    #   1. Entree           7. Terrasse     (numero en couleur, nom en gris, 2 colonnes ou plus)
    title = settings["titre"].strip() or (
        "Plan du rez-de-chaussée" if story == 0 else (f"Plan du R+{story}" if story > 0 else f"Plan du sous-sol {-story}"))
    title_h, text_h, num_w = 4.2, 3.2, 7.0
    first_row = top - title_h * mm - 4.0 * mm
    bottom = first_row - (rows - 1) * NOM_LIGNE_MM * mm - 3 * mm
    width = columns * col_w
    draw.ghost([{"x": x0 - 2 * mm, "y": bottom}, {"x": x0 + width, "y": bottom},
                {"x": x0 + width, "y": top + 2 * mm}, {"x": x0 - 2 * mm, "y": top + 2 * mm}])   # repere de position
    draw.text(title, x0, top, "LeftTop", size=title_h, pen=STYLO_GRIS)
    for i, z in enumerate(zones):
        col, row = divmod(i, rows)
        cx = x0 + col * col_w
        cy = first_row - row * NOM_LIGNE_MM * mm
        draw.text(f"{z['label']}.", cx + num_w * mm, cy, "RightMiddle", size=text_h, pen=STYLO_LEGENDE)
        draw.text(z["name"] or "Piece", cx + (num_w + 1.2) * mm, cy, "LeftMiddle", size=text_h, pen=STYLO_GRIS)
        if settings["surfaces"]:
            draw.text(format_area(z["area"]) + UNITE, cx + col_w - 4 * mm, cy, "RightMiddle", size=text_h * 0.85,
                      pen=STYLO_GRIS)
    legend_origin = [x0 - 2 * mm, bottom]            # coin bas-gauche du repere (pour retrouver le deplacement)
    elements, hatch_guids = draw.flush()
    nom_group(elements)
    all_guids += [e["elementId"]["guid"] for e in elements]

    numbers_written = write_zone_numbers(zones)

    memory[key] = all_guids
    positions[key] = {"legende_ref": hatch_guids[0] if hatch_guids else None,
                      "legende_origine": legend_origin, "legende_decalage": list(legend_offset),
                      "pastilles": pastilles, "ordre": [z["guid"] for z in zones],
                      "numeros": numbers, "integree": False}
    save_json(NOM_MEMOIRE, memory)
    save_json(NOM_POSITIONS, positions)
    note = "" if numbers_written else " (numeros non ecrits dans les zones Archicad : seulement sur le plan)"
    kept = f" {len(moved)} pastille(s) laissee(s) a ta place." if moved else ""
    return f"{len(zones)} piece(s) numerotee(s), legende dessinee.{kept}{note}"


def nom_pick_order(story: int) -> list[dict]:
    """Clique les pieces dans l'ordre voulu (Echap pour finir)."""
    zones = [z for z in read_zones() if z["story"] == story and len(z["outline"]) >= 3]
    for z in zones:
        z["guid"] = z["element"]["elementId"]["guid"]
    order, marks = [], []
    try:
        while True:
            focus_archicad()
            point, _ = pick_point(f"Piece n{len(order) + 1} : clique dedans (Echap = fin)")
            if point is None:
                break
            hit = next((z for z in zones if inside(point, z["outline"]) and z not in order), None)
            if hit is None:
                continue
            order.append(hit)
            # numero provisoire en surbrillance a l'endroit du clic
            try:
                answer = tapir("CreateTexts", {"textsData": [{
                    "coordinate": {"x": point["x"], "y": point["y"], "z": 0.0}, "text": f"{len(order):02d}",
                    "floorIndex": story, "style": {"height": 4.0, "bold": True, "penIndex": STYLO_CONTOUR,
                                                   "anchor": "MiddleMiddle", "justification": "Center"}}]})
                marks += [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
                highlight(marks)
            except Exception:  # noqa: BLE001
                pass
    finally:
        highlight([])
        if marks:
            try:
                tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in marks]})
            except Exception:  # noqa: BLE001
                pass
    return order


def settings_window(settings: dict, apply) -> None:
    import tkinter as tk
    from tkinter import colorchooser, simpledialog, ttk

    def to_hex(c):
        return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in c)

    state = dict(settings)
    root = tk.Tk()
    root.title("Tableau des surfaces + nomenclature - Claude Bridge")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    outer = ttk.Frame(root, padding=12)
    outer.grid()
    status = tk.StringVar(value="1. Cree tes zones   2. Regle le tableau puis Appliquer.")

    def busy(text):
        status.set(text)
        root.update_idletasks()

    # ------------------------------------------------------------ ce que je veux faire
    top_bar = ttk.Frame(outer)
    top_bar.grid(row=0, column=0, sticky="we", pady=(0, 8))
    ttk.Label(top_bar, text="Je veux faire :", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    output_mode = tk.StringVar(value=state.get("sortie", SORTIES[0]))
    ttk.Combobox(top_bar, values=SORTIES, textvariable=output_mode, state="readonly", width=36
                 ).grid(row=0, column=1, sticky="w", padx=8)

    def wants_table() -> bool:
        return output_mode.get() != SORTIES[1]

    def wants_nomenclature() -> bool:
        return output_mode.get() != SORTIES[0]

    def add_label() -> str:
        return "Ajouter au tableau" if wants_table() else "Ajouter la piece"

    # ------------------------------------------------------------ 1. zones
    zbox = ttk.LabelFrame(outer, text=" 1. Creer les zones (pieces) ", padding=10)
    zbox.grid(row=1, column=0, sticky="we")

    try:
        categories = zone_categories()
    except Exception:  # noqa: BLE001
        categories = []
    ttk.Label(zbox, text="Categorie").grid(row=0, column=0, sticky="w", pady=3)
    AUTO = "(automatique : une couleur par piece)"
    category_names = [AUTO] + [n for n, _, _ in categories]
    category = tk.StringVar(value=state.get("categorie", AUTO) if state.get("categorie") in category_names else AUTO)
    ttk.Combobox(zbox, values=category_names, textvariable=category, state="readonly", width=30
                 ).grid(row=0, column=1, sticky="w", padx=8)

    ttk.Label(zbox, text="Nom par defaut").grid(row=1, column=0, sticky="w", pady=3)
    base_name = tk.StringVar(value=state.get("nom_piece", "Piece"))
    ttk.Entry(zbox, textvariable=base_name, width=32).grid(row=1, column=1, sticky="w", padx=8)
    ask_name = tk.BooleanVar(value=state.get("demander_nom", True))
    ttk.Checkbutton(zbox, text="Demander le nom de chaque piece", variable=ask_name
                    ).grid(row=2, column=1, sticky="w", padx=8)

    show_guides = tk.BooleanVar(value=state.get("reperes", True))
    ttk.Checkbutton(zbox, text="Reperes (alignements + longueurs des traits)", variable=show_guides
                    ).grid(row=3, column=2, sticky="w", padx=4)
    ttk.Label(zbox, text="Traits du contour").grid(row=3, column=0, sticky="w", pady=3)
    lines_mode = tk.StringVar(value=state.get("traits", "Droits (0 / 90 degres)")
                              if state.get("traits") in TRAITS else "Droits (0 / 90 degres)")
    ttk.Combobox(zbox, values=list(TRAITS), textvariable=lines_mode, state="readonly", width=30
                 ).grid(row=3, column=1, sticky="w", padx=8)

    def chosen_category():
        if category.get() == AUTO:
            used = [z["category"] for z in read_zones() if z.get("category")]
            return auto_category(categories, used)
        return next((aid for n, aid, _ in categories if n == category.get()), None)

    def remember():
        state["categorie"] = category.get()
        state["nom_piece"] = base_name.get()
        state["demander_nom"] = bool(ask_name.get())
        state["traits"] = lines_mode.get()
        state["reperes"] = bool(show_guides.get())

    def by_clicks():
        """Baguette magique : clic dans un espace ferme (murs, poteaux, lignes) -> contour trouve,
        mis en surbrillance, puis Ajouter au tableau."""
        remember()
        story = tapir("GetStories").get("actStory", 0)
        number = next_zone_number(read_zones())
        created, missed = 0, 0
        last = "Clique dans un espace ferme. Echap pour terminer."
        busy("Lecture des murs et des traits du plan...")
        try:
            wand = MagicWand(story)
        except ImportError:
            wand = None
            print("(Baguette magique indisponible : module shapely absent. Detection d'Archicad utilisee.)")
        try:
            set_pens(state["couleur"], [])
        except Exception:  # noqa: BLE001
            pass
        root.withdraw()
        try:
            while True:
                focus_archicad()
                started = time.time()
                point, reason = pick_point(f"Piece {number} : clic dans l'espace (Echap pour terminer)")
                if point is None:
                    if created == 0 and missed == 0 and time.time() - started < 1.0:
                        last = ("Archicad n'a pas pu attendre le clic. Mets la fenetre du plan au premier plan "
                                f"puis recommence. ({reason})")
                    break
                face = wand.face(point) if wand and not wand.empty else None
                if face is None:
                    # a defaut : detection automatique d'Archicad (murs uniquement)
                    name = f"{base_name.get()} {number}"
                    if ask_name.get():
                        root.deiconify()
                        answer = ask_room_name(number, name)
                        root.withdraw()
                        if answer is None:
                            continue
                        name = answer.strip() or name
                    error = new_zone(story, name, number, chosen_category(), {"referencePosition": point}, point)
                    if error:
                        missed += 1
                        last = "Espace pas ferme a cet endroit (murs ou traits ouverts). Clique ailleurs ou Echap."
                        print(last)
                        continue
                else:
                    # apercu en surbrillance du contour trouve
                    rings = [list(face.exterior.coords)] + [list(r.coords) for r in face.interiors]
                    temp = []
                    try:
                        answer = tapir("CreatePolylines", {"polylinesData": [{
                            "floorInd": story, "linePenIndex": STYLO_CONTOUR, "penWeightMm": TRAIT_CONTOUR_MM,
                            "coordinates": [{"x": x, "y": y} for x, y in ring]} for ring in rings]})
                        temp = [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
                    except Exception:  # noqa: BLE001
                        pass
                    highlight(temp)
                    root.deiconify()
                    choice, name = name_dialog(f"Piece {number} trouvee - {format_area(face.area)} m2",
                                               "Espace trouve. Nom de la piece :", f"{base_name.get()} {number}",
                                               [("Terminer", "terminer"), ("Ignorer ce clic", "ignorer"),
                                                (add_label(), "ajouter")])
                    root.withdraw()
                    highlight([])
                    if temp:
                        try:
                            tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in temp]})
                        except Exception:  # noqa: BLE001
                            pass
                    if choice == "terminer":
                        break
                    if choice == "ignorer":
                        continue
                    geometry, stamp = face_geometry(face)
                    error = new_zone(story, name, number, chosen_category(), geometry, stamp)
                    if error:
                        missed += 1
                        last = f"Zone impossible ({error})."
                        print(last)
                        continue
                created += 1
                number += 1
                last = f"{name} creee. " + refresh_all()   # tableau mis a jour tout de suite
                print(last)
        finally:
            root.deiconify()
        status.set((f"{created} piece(s) ajoutee(s). " if created else "") + last)

    def name_dialog(title: str, message: str, default_name: str, buttons: list) -> tuple[str, str]:
        """Nom de la piece avec propositions pendant la frappe. buttons = [(texte, choix), ...], le dernier = Entree."""
        try:
            used = sorted({z["name"] for z in read_zones() if z.get("name")})
        except Exception:  # noqa: BLE001
            used = []
        result = {"choice": buttons[0][1], "name": default_name}
        win = tk.Toplevel(root)
        win.title(title)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        box = ttk.Frame(win, padding=14)
        box.grid()
        ttk.Label(box, text=message).grid(row=0, column=0, columnspan=len(buttons), sticky="w")
        name_var = tk.StringVar(value=default_name)
        entry = ttk.Entry(box, textvariable=name_var, width=40)
        entry.grid(row=1, column=0, columnspan=len(buttons), sticky="we", pady=(4, 2))
        listbox = tk.Listbox(box, height=6, activestyle="dotbox", exportselection=False)
        listbox.grid(row=2, column=0, columnspan=len(buttons), sticky="we", pady=(0, 10))
        ttk.Label(box, text="Clique une proposition (ou fleches + Tab) pour la prendre. Entree pour valider.",
                  foreground="#666").grid(row=3, column=0, columnspan=len(buttons), sticky="w", pady=(0, 8))

        def refresh(*_):
            listbox.delete(0, "end")
            for name in suggest_names(name_var.get(), used):
                listbox.insert("end", name)
            if listbox.size():
                listbox.selection_set(0)

        def take(_e=None):
            sel = listbox.curselection()
            if sel:
                name_var.set(listbox.get(sel[0]))
                entry.icursor("end")
            return "break"

        def move(delta):
            if not listbox.size():
                return "break"
            sel = listbox.curselection()
            index = max(0, min(listbox.size() - 1, (sel[0] if sel else -1) + delta))
            listbox.selection_clear(0, "end")
            listbox.selection_set(index)
            listbox.see(index)
            return "break"

        def close(choice):
            result["choice"], result["name"] = choice, (name_var.get().strip() or default_name)
            win.destroy()

        name_var.trace_add("write", refresh)
        entry.bind("<Down>", lambda _e: move(1))
        entry.bind("<Up>", lambda _e: move(-1))
        entry.bind("<Tab>", take)
        listbox.bind("<ButtonRelease-1>", lambda _e: (take(), entry.focus_set()))   # un clic suffit
        for column, (text, choice) in enumerate(buttons):
            ttk.Button(box, text=text, command=lambda c=choice: close(c)).grid(row=4, column=column, padx=3)
        win.bind("<Return>", lambda _e: close(buttons[-1][1]))
        win.protocol("WM_DELETE_WINDOW", lambda: close(buttons[0][1]))
        refresh()
        entry.select_range(0, "end")
        entry.focus_set()
        win.lift()
        win.focus_force()
        root.wait_window(win)
        return result["choice"], result["name"]

    def room_done_dialog(number: int, default_name: str) -> tuple[str, str]:
        """Contour ferme : nom de la piece + Ajouter au tableau / Recommencer / Terminer."""
        return name_dialog(f"Piece {number} terminee", "Contour ferme. Nom de la piece :", default_name,
                           [("Terminer", "terminer"), ("Recommencer le contour", "recommencer"),
                            (add_label(), "ajouter")])

    def ask_room_name(number: int, default_name: str) -> str | None:
        choice, name = name_dialog("Nom de la piece", f"Nom de la piece {number} :", default_name,
                                   [("Ignorer cette piece", "annuler"), ("OK", "ok")])
        return name if choice == "ok" else None

    def by_drawing():
        """Dessiner soi-meme le contour de chaque piece, point par point ; le tableau suit a chaque piece."""
        remember()
        story = tapir("GetStories").get("actStory", 0)
        number = next_zone_number(read_zones())
        try:
            set_pens(state["couleur"], [])                  # stylo du contour en surbrillance
        except Exception:  # noqa: BLE001
            pass

        def draw_temp(temp: list, coords: list) -> None:
            """Trait provisoire epais, de couleur vive, en surbrillance."""
            try:
                answer = tapir("CreatePolylines", {"polylinesData": [{
                    "floorInd": story, "coordinates": coords,
                    "linePenIndex": STYLO_CONTOUR, "penWeightMm": TRAIT_CONTOUR_MM}]})
                temp += [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
            except Exception:  # noqa: BLE001
                pass
            highlight(temp)

        def delete(guids: list) -> None:
            if guids:
                try:
                    tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in guids]})
                except Exception:  # noqa: BLE001
                    pass
            guids.clear()

        def draw_guides(guides: list, points: list) -> None:
            """Reperes : lignes horizontale et verticale par le dernier point et par le 1er point."""
            delete(guides)
            if not points or not show_guides.get():
                return
            reach = REPERE_M
            through = [points[-1]] + ([points[0]] if len(points) > 1 else [])
            data = []
            for q in through:
                data.append({"floorInd": story, "linePenIndex": STYLO_REPERE, "penWeightMm": TRAIT_REPERE_MM,
                             "coordinates": [{"x": q["x"] - reach, "y": q["y"]}, {"x": q["x"] + reach, "y": q["y"]}]})
                data.append({"floorInd": story, "linePenIndex": STYLO_REPERE, "penWeightMm": TRAIT_REPERE_MM,
                             "coordinates": [{"x": q["x"], "y": q["y"] - reach}, {"x": q["x"], "y": q["y"] + reach}]})
            try:
                answer = tapir("CreatePolylines", {"polylinesData": data})
                guides += [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
            except Exception:  # noqa: BLE001
                pass

        def draw_length(labels: list, a: dict, b: dict) -> None:
            """Longueur du trait ecrite a cote (en m)."""
            if not show_guides.get():
                labels.append(None)
                return
            length = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
            mid = {"x": (a["x"] + b["x"]) / 2, "y": (a["y"] + b["y"]) / 2}
            off = 2.5 * ECHELLE / 1000.0
            if length > 1e-9:   # decale le texte a cote du trait
                mid = {"x": mid["x"] - (b["y"] - a["y"]) / length * off, "y": mid["y"] + (b["x"] - a["x"]) / length * off}
            try:
                answer = tapir("CreateTexts", {"textsData": [{
                    "coordinate": {"x": mid["x"], "y": mid["y"], "z": 0.0}, "text": format_area(length),
                    "floorIndex": story, "style": {"height": 2.5, "bold": True, "penIndex": STYLO_CONTOUR,
                                                   "anchor": "MiddleMiddle", "justification": "Center"}}]})
                items = [e["elementId"]["guid"] for e in answer.get("elements", []) if "elementId" in e]
                labels.append(items[0] if items else None)
            except Exception:  # noqa: BLE001
                labels.append(None)

        close_dist = FERMETURE_MM * ECHELLE / 1000.0
        created = 0
        last = "Dessine les pieces une par une ; chaque contour ferme propose \"Ajouter au tableau\"."
        root.withdraw()
        try:
            while True:
                points, temp, guides, labels = [], [], [], []
                while True:
                    focus_archicad()
                    if points:
                        prompt = f"Piece {number} : point {len(points) + 1} (1er point/Echap = fermer, point precedent = annuler)"
                    else:
                        prompt = f"Piece {number} : 1er coin (Echap = terminer)"
                    point, _ = pick_point(prompt)
                    if point is None:
                        break
                    if len(points) >= 3 and math.dist((point["x"], point["y"]), (points[0]["x"], points[0]["y"])) <= close_dist:
                        break
                    if len(points) >= 2 and math.dist((point["x"], point["y"]), (points[-2]["x"], points[-2]["y"])) <= close_dist:
                        points.pop()                     # clic sur l'avant-dernier point : annule le dernier
                        if len(temp) > 1:            # (temp[0] = repere du 1er point)
                            delete([temp.pop()])
                            highlight(temp)
                        if labels:
                            label = labels.pop()
                            if label:
                                delete([label])
                        draw_guides(guides, points)
                        continue
                    if points:
                        point = constrain(points[-1], point, TRAITS.get(lines_mode.get(), 90.0))
                        if math.dist((point["x"], point["y"]), (points[-1]["x"], points[-1]["y"])) < 1e-4:
                            continue
                    if not points:               # repere en surbrillance sur le 1er point (pour fermer)
                        r = MARQUE_MM * ECHELLE / 1000.0 / 2
                        draw_temp(temp, [{"x": point["x"] - r, "y": point["y"] - r}, {"x": point["x"] + r, "y": point["y"] - r},
                                         {"x": point["x"] + r, "y": point["y"] + r}, {"x": point["x"] - r, "y": point["y"] + r},
                                         {"x": point["x"] - r, "y": point["y"] - r}])
                    if points:   # trait provisoire pour voir le contour se dessiner
                        draw_temp(temp, [points[-1], point])
                        draw_length(labels, points[-1], point)
                    points.append(point)
                    draw_guides(guides, points)
                if len(points) >= 3:             # traits droits jusqu'au bout : coin de fermeture si besoin
                    corner = closing_corner(points, TRAITS.get(lines_mode.get(), 90.0))
                    if corner is not None:
                        draw_temp(temp, [points[-1], corner])
                        draw_length(labels, points[-1], corner)
                        points.append(corner)
                if len(points) >= 3 and temp:   # dernier trait : fermer le contour visible
                    draw_temp(temp, [points[-1], points[0]])

                delete(guides)
                if len(points) >= 3:
                    draw_length(labels, points[-1], points[0])

                def clear_temp():
                    highlight([])
                    delete(temp)
                    delete([g for g in labels if g])
                    delete(guides)

                if len(points) < 3:
                    clear_temp()
                    break                                   # Echap sans contour : fin
                choice, name = room_done_dialog(number, f"{base_name.get()} {number}")
                clear_temp()
                if choice == "terminer":
                    break
                if choice == "recommencer":
                    continue
                points = drop_collinear(points)
                error = new_zone(story, name, number, chosen_category(), {"polygonCoordinates": points}, centroid(points))
                if error:
                    last = f"Zone impossible avec ce contour ({error})."
                    print(last)
                    continue
                created += 1
                number += 1
                last = f"{name} creee. " + refresh_all()
                print(last)
        finally:
            root.deiconify()
        status.set((f"{created} piece(s) dessinee(s) et ajoutee(s). " if created else "") + last)

    def by_selection():
        remember()
        busy("Lecture de la selection...")
        outlines = selected_outlines()
        if not outlines:
            status.set("Selectionne d'abord une ou plusieurs polylignes fermees ou remplissages qui delimitent tes pieces.")
            return
        story = tapir("GetStories").get("actStory", 0)
        number = next_zone_number(read_zones())
        created, errors = 0, []
        for pts, arcs, holes in outlines:
            name = f"{base_name.get()} {number}"
            if ask_name.get():
                answer = ask_room_name(number, name)
                if answer is None:
                    continue
                name = answer.strip() or name
            geometry = {"polygonCoordinates": pts}
            if arcs:
                geometry["polygonArcs"] = arcs
            if holes:
                geometry["holes"] = holes
            error = new_zone(story, name, number, chosen_category(), geometry, centroid(pts))
            if error:
                errors.append(error)
            else:
                created += 1
                number += 1
        message = f"{created} zone(s) creee(s) depuis la selection."
        if errors:
            message += f" Echec pour {len(errors)} : {errors[0]}"
        busy(message + " Mise a jour du tableau...")
        status.set(message + (" " + refresh_all() if created else ""))

    def update_all():
        busy("Recalcul des zones...")
        try:
            tapir("UpdateZones", {"keepStampPosition": True})
        except Exception as error:  # noqa: BLE001
            status.set(f"Recalcul des zones impossible : {error}")
            return
        status.set("Zones recalculees. " + refresh_all())

    zbtn = ttk.Frame(zbox)
    zbtn.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
    ttk.Button(zbtn, text="Baguette magique (clic dans la piece)...", command=by_clicks).grid(row=0, column=0, padx=(0, 6))
    ttk.Button(zbtn, text="Dessiner une piece (contour)...", command=by_drawing).grid(row=0, column=1, padx=6)
    ttk.Button(zbtn, text="Zones depuis la selection", command=by_selection).grid(row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="we")
    ttk.Button(zbtn, text="Mettre a jour (pieces modifiees)", command=update_all).grid(row=1, column=1, padx=6, pady=(6, 0), sticky="we")

    # ------------------------------------------------------------ 2. tableau
    frame = ttk.LabelFrame(outer, text=" 2. Tableau ", padding=10)
    frame.grid(row=2, column=0, sticky="we", pady=(10, 0))

    ttk.Label(frame, text="Couleur du bandeau").grid(row=0, column=0, sticky="w", pady=4)
    swatch = tk.Label(frame, width=6, bg=to_hex(state["couleur"]), relief="groove")
    swatch.grid(row=0, column=1, sticky="w", padx=8)

    def pick():
        rgb, _ = colorchooser.askcolor(color=to_hex(state["couleur"]), title="Couleur du bandeau", parent=root)
        if rgb:
            state["couleur"] = [v / 255.0 for v in rgb]
            swatch.configure(bg=to_hex(state["couleur"]))

    def default_color():
        state["couleur"] = list(COULEUR_BANDEAU)
        swatch.configure(bg=to_hex(state["couleur"]))

    colors = ttk.Frame(frame)
    colors.grid(row=0, column=2, sticky="w")
    ttk.Button(colors, text="Choisir...", command=pick).grid(row=0, column=0, padx=(0, 4))
    ttk.Button(colors, text="Rouge par defaut", command=default_color).grid(row=0, column=1)

    fields = {}
    for row, (key, label) in enumerate((("titre_habitables", "Titre du 1er tableau"),
                                        ("titre_annexes", "Titre du 2e tableau"),
                                        ("texte_total", "Texte du total"),
                                        ("mots_annexes", "Zones annexes si le nom\nou la categorie contient")), start=1):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        var = tk.StringVar(value=state[key])
        ttk.Entry(frame, textvariable=var, width=48).grid(row=row, column=1, columnspan=2, sticky="we", padx=8)
        fields[key] = var

    swatches = tk.BooleanVar(value=state.get("codes_couleur", True))
    ttk.Checkbutton(frame, text="Afficher le code couleur de chaque zone", variable=swatches
                    ).grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=4)

    def collect() -> dict:
        for key, var in fields.items():
            state[key] = var.get()
        state["codes_couleur"] = bool(swatches.get())
        state["sortie"] = output_mode.get()
        remember()
        save_json(REGLAGES, state)
        return state

    # ------------------------------------------------------------ 3. nomenclature
    nom_state = nom_load_settings()
    nbox = ttk.LabelFrame(outer, text=" 3. Nomenclature : numeros dans les pieces + legende en bas du plan ", padding=10)
    nbox.grid(row=3, column=0, sticky="we", pady=(10, 0))
    ttk.Label(nbox, text="Ordre des numeros").grid(row=0, column=0, sticky="w", pady=3)
    nom_order = tk.StringVar(value=nom_state["ordre"])
    ttk.Combobox(nbox, values=NOM_ORDRES, textvariable=nom_order, state="readonly", width=38).grid(row=0, column=1, columnspan=2, sticky="w", padx=8)
    ttk.Label(nbox, text="Format").grid(row=1, column=0, sticky="w", pady=3)
    nom_fmt = tk.StringVar(value=nom_state["format"])
    ttk.Combobox(nbox, values=NOM_FORMATS, textvariable=nom_fmt, state="readonly", width=38).grid(row=1, column=1, columnspan=2, sticky="w", padx=8)
    ttk.Label(nbox, text="Legende").grid(row=6, column=0, sticky="w", pady=3)
    nom_mode = tk.StringVar(value=nom_state["legende"])
    ttk.Combobox(nbox, values=NOM_LEGENDES, textvariable=nom_mode, state="readonly", width=44).grid(row=6, column=1, columnspan=2, sticky="w", padx=8)
    ttk.Label(nbox, text="Titre (vide = auto)").grid(row=2, column=0, sticky="w", pady=3)
    nom_title = tk.StringVar(value=nom_state["titre"])
    ttk.Entry(nbox, textvariable=nom_title, width=40).grid(row=2, column=1, columnspan=2, sticky="w", padx=8)
    ttk.Label(nbox, text="Couleur cercles + numeros").grid(row=3, column=0, sticky="w", pady=3)
    nom_swatch = tk.Label(nbox, width=6, bg=to_hex(nom_state["couleur"]), relief="groove")
    nom_swatch.grid(row=3, column=1, sticky="w", padx=8)

    def nom_pick():
        rgb, _ = colorchooser.askcolor(color=to_hex(nom_state["couleur"]), title="Couleur du bandeau de la legende", parent=root)
        if rgb:
            nom_state["couleur"] = [v / 255.0 for v in rgb]
            nom_swatch.configure(bg=to_hex(nom_state["couleur"]))

    ttk.Button(nbox, text="Choisir...", command=nom_pick).grid(row=3, column=2, sticky="w")
    nom_surfaces = tk.BooleanVar(value=nom_state["surfaces"])
    ttk.Checkbutton(nbox, text="Ajouter les surfaces dans la legende", variable=nom_surfaces
                    ).grid(row=4, column=1, columnspan=2, sticky="w", padx=8)

    def nom_collect() -> dict:
        nom_state.update(ordre=nom_order.get(), format=nom_fmt.get(), titre=nom_title.get(),
                         surfaces=bool(nom_surfaces.get()), legende=nom_mode.get())
        save_json(NOM_REGLAGES, nom_state)
        return dict(nom_state)

    def nom_do(action):
        settings_nom = nom_collect()
        try:
            ordered = None
            if action == "numeroter" and settings_nom["ordre"] == NOM_ORDRES[1]:
                root.withdraw()
                try:
                    ordered = nom_pick_order(tapir("GetStories").get("actStory", 0))
                finally:
                    root.deiconify()
            busy("Nomenclature en cours...")
            if not wants_table():                   # sans tableau : legende forcement separee
                settings_nom["legende"] = NOM_LEGENDES[0]
            message = nom_run(action, settings_nom, ordered)
            if wants_table() and table_drawn():     # le tableau suit (ajoute ou retire la colonne N°)
                message += " " + apply("dessiner", dict(collect()))
            status.set(message)
        except Exception as error:  # noqa: BLE001
            root.deiconify()
            status.set(f"Erreur : {error}")

    nbtn = ttk.Frame(nbox)
    nbtn.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))
    ttk.Button(nbtn, text="Numeroter + legende", command=lambda: nom_do("numeroter")).grid(row=0, column=0, padx=(0, 6))
    ttk.Button(nbtn, text="Mettre a jour (garde les positions)", command=lambda: nom_do("mettre_a_jour")).grid(row=0, column=1, padx=6)
    ttk.Button(nbtn, text="Supprimer la nomenclature", command=lambda: nom_do("supprimer")).grid(row=0, column=2, padx=6)

    def table_drawn() -> bool:
        try:
            project = tapir("GetProjectInfo")
            project_key = project.get("projectPath") or project.get("projectName") or "Sans titre"
            return any(k.split("|")[0] == project_key and v for k, v in load_json(MEMOIRE).items())
        except Exception:  # noqa: BLE001
            return False

    def nomenclature_drawn() -> bool:
        try:
            story = tapir("GetStories").get("actStory", 0)
            project = tapir("GetProjectInfo")
            key = f"{project.get('projectPath') or project.get('projectName') or 'Sans titre'}|{story}"
            return bool(load_json(NOM_MEMOIRE).get(key))
        except Exception:  # noqa: BLE001
            return False

    def refresh_all() -> str:
        """Apres creation de pieces : tableau redessine, et nomenclature mise a jour si elle existe."""
        message = ""
        if wants_nomenclature():                    # numeros d'abord : le tableau peut les reprendre
            try:
                settings_nom = nom_collect()
                if not wants_table():
                    settings_nom["legende"] = NOM_LEGENDES[0]
                message = nom_run("mettre_a_jour" if nomenclature_drawn() else "numeroter", settings_nom) + " "
            except Exception as error:  # noqa: BLE001
                message = f"(nomenclature : {error}) "
        if wants_table():
            message += apply("dessiner", dict(collect()))
        return message.strip()

    ttk.Label(outer, textvariable=status, foreground="#555", wraplength=520).grid(row=4, column=0, sticky="w", pady=(10, 0))

    def do(action):
        collect()
        busy("En cours...")
        try:
            status.set(apply(action, dict(state)))
        except Exception as error:  # noqa: BLE001
            status.set(f"Erreur : {error}")

    def safe(fn):
        def wrapped():
            try:
                fn()
            except Exception as error:  # noqa: BLE001
                root.deiconify()
                status.set(f"Erreur : {error}")
        return wrapped

    buttons = ttk.Frame(outer)
    buttons.grid(row=5, column=0, pady=(12, 0), sticky="e")
    table_buttons = [ttk.Button(buttons, text="Supprimer le tableau", command=lambda: do("supprimer")),
                     ttk.Button(buttons, text="Appliquer le tableau", command=lambda: do("dessiner"))]
    table_buttons[0].grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Fermer", command=root.destroy).grid(row=0, column=1, padx=4)
    table_buttons[1].grid(row=0, column=2, padx=4)

    def show_mode(*_):
        """N'affiche que ce qui sert : tableau, nomenclature, ou les deux."""
        state["sortie"] = output_mode.get()
        save_json(REGLAGES, state)
        if wants_table():
            frame.grid()
            for b in table_buttons:
                b.grid()
        else:
            frame.grid_remove()
            for b in table_buttons:
                b.grid_remove()
        if wants_nomenclature():
            nbox.grid()
        else:
            nbox.grid_remove()
        status.set("1. Cree tes pieces, puis " + ("numerote-les (partie 3)." if output_mode.get() == SORTIES[1]
                                                 else "le tableau se dessine tout seul."))

    output_mode.trace_add("write", show_mode)
    show_mode()
    for child, fn in zip(zbtn.winfo_children(), (by_clicks, by_drawing, by_selection, update_all)):
        child.configure(command=safe(fn))
    root.bind("<Escape>", lambda _e: root.destroy())
    root.mainloop()


def main() -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args, _ = parser.parse_known_args()
    PORT = args.port

    settings = load_settings()
    try:
        settings_window(settings, run)
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
