"""CosmosDiagnostic - observe comment le service Chaos Cosmos (127.0.0.1:30305) accepte un logiciel hote.

But : trouver comment V-Ray Link peut s'enregistrer comme "hote" (comme V-Ray pour SketchUp)
pour que Cosmos affiche aussi les MATERIAUX, sans SketchUp.

A lancer avec Cosmos ouvert (cbservice demarre), SketchUp ferme.
Resultat : un fichier "cosmos_rapport.txt" sur le Bureau, a m'envoyer.
"""
import os
import re
import socket
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:30305"
HOME = Path.home()
DESK = next((p for p in (HOME / "OneDrive" / "Desktop", HOME / "OneDrive" / "Bureau", HOME / "Desktop") if p.exists()), HOME)
OUT = DESK / "cosmos_rapport.txt"
CACHE = Path(os.environ.get("APPDATA", HOME)) / "ClaudeBridge" / "cosmos_diag"
MOTS = ["importer", "/api/", "ws://", "wss://", "websocket", "30306", "assetType", "asset_type",
        "material", "vrmat", "register", "capabilit", "host", "plugin"]

lines = []


def log(*a):
    lines.append(" ".join(str(x) for x in a))


def get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "VRayLink-diag"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def port_ouvert(p):
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=1):
            return True
    except OSError:
        return False


def extraits(texte, mot, n=12, large=160):
    out = []
    for m in re.finditer(re.escape(mot), texte, re.I):
        a, b = max(0, m.start() - large), min(len(texte), m.end() + large)
        out.append(texte[a:b].replace("\n", " "))
        if len(out) >= n:
            break
    return out


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    log("=== PORTS ===")
    for p in (30305, 30306):
        log(p, "ouvert" if port_ouvert(p) else "FERME")

    log("\n=== PAGE D'ACCUEIL ===")
    try:
        st, hd, body = get(BASE + "/")
        html = body.decode("utf-8", "replace")
        (CACHE / "index.html").write_text(html, encoding="utf-8")
        log("status", st, "|", hd.get("Content-Type"))
        log(html[:1500])
    except Exception as e:  # noqa: BLE001
        log("ERREUR :", e, "-> Cosmos est-il ouvert ?")
        html = ""

    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html) + re.findall(r'href="([^"]+\.js)"', html)
    log("\n=== SCRIPTS ===", scripts)
    tout = ""
    for s in dict.fromkeys(scripts):
        url = s if s.startswith("http") else BASE + "/" + s.lstrip("/")
        try:
            _, _, js = get(url, 20)
            js = js.decode("utf-8", "replace")
            (CACHE / (re.sub(r"[^\w.]", "_", s)[-80:])).write_text(js, encoding="utf-8")
            tout += "\n" + js
            log(url, len(js), "caracteres")
        except Exception as e:  # noqa: BLE001
            log(url, "ERREUR", e)

    log("\n=== URLS D'API TROUVEES DANS LE CODE ===")
    apis = sorted(set(re.findall(r'["\'`](/(?:api|v\d|importer|ws|socket)[\w/\-{}.$:?=&]*)["\'`]', tout)))
    for a in apis[:200]:
        log(" ", a)

    for mot in MOTS:
        ex = extraits(tout, mot)
        log(f"\n=== '{mot}' : {len(re.findall(re.escape(mot), tout, re.I))} occurrences ===")
        for e in ex:
            log("  ...", e, "...")

    log("\n=== ESSAIS D'URL ===")
    for u in ["/api", "/api/v1", "/api/importers", "/api/v1/importers", "/importers", "/api/hosts",
              "/api/config", "/api/status", "/api/version", "/config.json", "/version"]:
        try:
            st, hd, body = get(BASE + u, 4)
            log(u, st, hd.get("Content-Type"), body[:300])
        except Exception as e:  # noqa: BLE001
            log(u, "->", str(e)[:80])

    log("\n=== JOURNAUX (logs) COSMOS ===")
    racines = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", ""), os.environ.get("PROGRAMDATA", ""),
               str(HOME / "OneDrive" / "Documents" / "Chaos Cosmos"), r"C:\Program Files\Chaos\Cosmos"]
    for r in racines:
        if not r or not Path(r).exists():
            continue
        base = Path(r)
        cibles = [base] if "Cosmos" in r else [d for d in base.iterdir() if d.is_dir() and ("chaos" in d.name.lower() or "cosmos" in d.name.lower())]
        for d in cibles:
            for f in d.rglob("*"):
                if f.is_file() and f.suffix.lower() in (".log", ".txt", ".json", ".ini", ".cfg", ".yaml", ".yml", ".toml") \
                        and "Packages" not in f.parts and f.stat().st_size < 50_000_000:
                    log("\n#", f, f.stat().st_size, "octets")
                    try:
                        t = f.read_text("utf-8", "replace")
                    except Exception:  # noqa: BLE001
                        continue
                    hits = [l for l in t.splitlines() if re.search(r"importer|register|host|material|ws:|websocket|30306", l, re.I)]
                    for l in (hits[-40:] if hits else t.splitlines()[:10]):
                        log("   ", l[:300])

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("Rapport ecrit :", OUT)
    try:
        os.startfile(str(OUT))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
