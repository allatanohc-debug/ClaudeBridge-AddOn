"""CosmosDiagnostic3 - lit le "contrat" interne du service Chaos Cosmos (cbservice.exe) :
les messages qu'un logiciel hote (importer) echange avec Cosmos, + teste l'interface MCP de Cosmos.

Ne modifie rien. A lancer avec Cosmos ouvert (cbservice demarre), SketchUp ferme.
Resultat : "cosmos_rapport3.zip" sur le Bureau, a m'envoyer.
"""
import json
import os
import re
import ssl
import urllib.request
import zipfile
from pathlib import Path

HOME = Path.home()
DESK = next((p for p in (HOME / "OneDrive" / "Desktop", HOME / "OneDrive" / "Bureau", HOME / "Desktop") if p.exists()), HOME)
EXE = Path(r"C:\Program Files\Chaos\Cosmos\cbservice.exe")
RUNTIME = Path(os.environ.get("LOCALAPPDATA", HOME)) / "Chaos" / "Cosmos" / "runtime.json"
OUT = DESK / "cosmos_rapport3.zip"

lines = []


def log(*a):
    lines.append(" ".join(str(x) for x in a))


def chaines(data: bytes):
    """Chaines utiles du binaire Go : services gRPC, routes HTTP, MCP, ports."""
    motif = re.compile(r"v1beta|v1alpha|\.v1\.|Service/|/mcp|mcp|grpc|/api/|/import|import_|tool|30305|30306|127\.0\.0\.1|localhost|"
                       r"websocket|/ws|sse|jsonrpc|Importer|importer|asset", re.I)
    vus = set()
    for m in re.finditer(rb"[\x20-\x7e]{5,400}", data):
        s = m.group().decode("ascii", "replace")
        if motif.search(s) and s not in vus:
            vus.add(s)
            yield s


def descripteurs(data: bytes):
    """Descripteurs protobuf bruts (fichiers .proto compiles) embarques dans le binaire."""
    out = []
    for m in re.finditer(rb"\x0a([\x05-\x7f])([\w/\-.]{3,120}\.proto)\x12", data):
        if m.group(1)[0] != len(m.group(2)):
            continue
        out.append((m.start(), m.group(2).decode()))
    return out


def sonde(url, corps=None, entetes=None):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE           # certificat local auto-signe de Cosmos (127.0.0.1 uniquement)
    h = {"User-Agent": "VRayLink-diag", "Accept": "application/json, text/event-stream"}
    h.update(entetes or {})
    req = urllib.request.Request(url, data=corps, headers=h, method="POST" if corps else "GET")
    try:
        with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
            return f"{r.status} {r.headers.get('Content-Type')} {dict(r.headers).get('Mcp-Session-Id', '')} | " + r.read(3000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} | " + e.read(800).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "ERREUR " + str(e)[:200]


def main():
    zf = zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED)
    if RUNTIME.exists():
        log("=== runtime.json ===")
        log(RUNTIME.read_text("utf-8", "replace"))

    if EXE.exists():
        data = EXE.read_bytes()
        log("\n=== cbservice.exe", len(data), "octets ===")
        ch = list(chaines(data))
        zf.writestr("chaines.txt", "\n".join(ch))
        log(len(ch), "chaines utiles -> chaines.txt")
        desc = descripteurs(data)
        log("\n=== descripteurs protobuf :", len(desc), "===")
        for i, (pos, nom) in enumerate(desc):
            log("  ", pos, nom)
            if re.search(r"google/protobuf|grpc/|validate|openapiv2|gnostic", nom):
                continue
            zf.writestr(f"proto/{i:03d}_{re.sub(r'[^\w.]', '_', nom)}.bin", data[pos:pos + 120_000])
    else:
        log("cbservice.exe introuvable :", EXE)

    log("\n=== SONDES MCP / API ===")
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "VRayLink-diag", "version": "0.1"}}}).encode()
    liste = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode()
    for base in ("http://127.0.0.1:30305", "https://127.0.0.1:30306"):
        for chemin in ("/mcp", "/mcp/", "/api/mcp", "/v1/mcp", "/sse", "/mcp/sse", "/api/v1/mcp"):
            r = sonde(base + chemin, init, {"Content-Type": "application/json"})
            log(base + chemin, "initialize ->", r[:600])
            if r.startswith("200"):
                sid = re.search(r"200 \S+ (\S+) \|", r)
                ent = {"Content-Type": "application/json"}
                if sid and sid.group(1) not in ("", "|"):
                    ent["Mcp-Session-Id"] = sid.group(1)
                log("   tools/list ->", sonde(base + chemin, liste, ent)[:3000])
        for chemin in ("/", "/healthz", "/health", "/version", "/api/v1/importers", "/content.v1beta2.ImporterService/ListImporters"):
            log(base + chemin, "GET ->", sonde(base + chemin)[:200])

    zf.writestr("rapport.txt", "\n".join(lines))
    zf.close()
    print("Rapport ecrit :", OUT)
    try:
        os.startfile(str(OUT.parent))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
