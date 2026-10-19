"""CosmosDiagnostic2 - cherche, dans les fichiers de V-Ray pour SketchUp, comment V-Ray
s'enregistre comme "hote" aupres de Chaos Cosmos (le code qui fait marcher les telechargements).

Ne modifie rien. Lit seulement des fichiers. SketchUp peut etre ferme.
Resultat : "cosmos_rapport2.txt" sur le Bureau, a m'envoyer.
"""
import os
import re
from pathlib import Path

HOME = Path.home()
DESK = next((p for p in (HOME / "OneDrive" / "Desktop", HOME / "OneDrive" / "Bureau", HOME / "Desktop") if p.exists()), HOME)
OUT = DESK / "cosmos_rapport2.txt"
APPDATA = Path(os.environ.get("APPDATA", HOME))
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME))

RACINES = [
    Path(r"C:\Program Files\Chaos\V-Ray\V-Ray for SketchUp"),
    Path(r"C:\Program Files\Chaos\Cosmos"),
    Path(r"C:\Program Files\Chaos\Vantage"),
    APPDATA / "Chaos" / "V-Ray for SketchUp",
    LOCAL / "Chaos" / "Cosmos",
]
for v in ("2026", "2025", "2024"):
    RACINES.append(APPDATA / "SketchUp" / f"SketchUp {v}" / "SketchUp" / "Plugins")

TEXTE = {".rb", ".js", ".mjs", ".cjs", ".json", ".html", ".htm", ".ini", ".xml", ".cfg", ".txt", ".yml", ".yaml",
         ".toml", ".py", ".ts", ".conf", ".properties", ".bat", ".cmd", ".ps1"}
BIN = {".dll", ".exe", ".node", ".so", ".pyd", ".asar"}
MOTIF = re.compile(r"importer|cosmos|30306|30305|register_?host|hostapp|first_importer|asset_?type", re.I)
MOTIF_BIN = re.compile(rb"[\x20-\x7e]{6,300}")
FILTRE_BIN = re.compile(r"importer|/api/|cosmos.*(http|/|register|host|import)|30306|register.*host|host.*register|utm_", re.I)

lines = []


def log(*a):
    lines.append(" ".join(str(x) for x in a))


def scan_texte(f: Path, maxi=40):
    try:
        t = f.read_text("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return
    hits = []
    for m in MOTIF.finditer(t):
        a, b = max(0, m.start() - 200), min(len(t), m.end() + 200)
        hits.append(t[a:b].replace("\n", " ").replace("\r", " "))
        if len(hits) >= maxi:
            break
    if hits:
        log(f"\n## TEXTE {f}  ({f.stat().st_size} octets, {len(hits)} extraits)")
        vus = set()
        for h in hits:
            k = h[150:260]
            if k in vus:
                continue
            vus.add(k)
            log("   ...", h, "...")


def scan_bin(f: Path, maxi=120):
    try:
        if f.stat().st_size > 300_000_000:
            return
        data = f.read_bytes()
    except Exception:  # noqa: BLE001
        return
    found = []
    for m in MOTIF_BIN.finditer(data):
        s = m.group().decode("ascii", "replace")
        if FILTRE_BIN.search(s):
            found.append(s)
    # chaines UTF-16 (Windows)
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,300}", data):
        s = m.group().decode("utf-16-le", "replace")
        if FILTRE_BIN.search(s):
            found.append("[w] " + s)
    if found:
        found = list(dict.fromkeys(found))
        log(f"\n## BINAIRE {f}  ({f.stat().st_size} octets, {len(found)} chaines)")
        for s in found[:maxi]:
            log("   ", s[:300])


def main():
    log("=== DOSSIERS ===")
    fichiers = []
    for r in RACINES:
        if not r.exists():
            log("absent :", r)
            continue
        n = 0
        for f in r.rglob("*"):
            if f.is_file():
                fichiers.append(f)
                n += 1
        log("ok :", r, n, "fichiers")

    log("\n=== FICHIERS QUI PARLENT DE COSMOS (noms) ===")
    for f in fichiers:
        if re.search(r"cosmos|importer|neui|browser", str(f), re.I):
            log("  ", f, f.stat().st_size)

    log("\n=== EXTENSIONS RUBY (Plugins SketchUp) ===")
    for f in fichiers:
        if f.suffix.lower() in (".rb", ".rbe", ".rbs") and "Plugins" in f.parts:
            log("  ", f, f.stat().st_size)

    log("\n=== CONTENU TEXTE ===")
    for f in fichiers:
        if f.suffix.lower() in TEXTE and f.stat().st_size < 30_000_000:
            scan_texte(f)

    log("\n=== CONTENU BINAIRE (dll/exe) ===")
    for f in fichiers:
        if f.suffix.lower() in BIN and (re.search(r"cosmos|neui|vray|importer|browser|cb", f.name, re.I)):
            scan_bin(f)

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("Rapport ecrit :", OUT)
    try:
        os.startfile(str(OUT))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
