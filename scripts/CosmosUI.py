"""CosmosUI - telecharge le code de la page Cosmos (tous ses morceaux) pour trouver pourquoi elle dit
"... failed to register to Cosmos Browser". Ne modifie rien.
Resultat : "cosmos_ui.zip" sur le Bureau, a m'envoyer.
"""
import os
import re
import ssl
import urllib.request
import zipfile
from pathlib import Path

HOME = Path.home()
DESK = next((p for p in (HOME / "OneDrive" / "Desktop", HOME / "OneDrive" / "Bureau", HOME / "Desktop") if p.exists()), HOME)
OUT = DESK / "cosmos_ui.zip"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE   # uniquement pour la page locale https://localhost:30306


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VRayLink-diag"})
    with urllib.request.urlopen(req, timeout=30, context=CTX if "localhost" in url or "127.0.0.1" in url else None) as r:
        return r.read().decode("utf-8", "replace")


def main():
    zf = zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED)
    log = []
    seen, todo = set(), []
    for start in ("http://127.0.0.1:30305/", "https://localhost:30306/"):
        try:
            html = get(start)
            zf.writestr("index_%s.html" % start.split(":")[-1].strip("/"), html)
            todo += re.findall(r'(?:src|href)="([^"]+\.js)"', html)
            cdn = re.search(r'(https://[^"/]+)/_next/', html)
            base = cdn.group(1) if cdn else start.rstrip("/")
            break
        except Exception as e:  # noqa: BLE001
            log.append(f"{start} : {e}")
    else:
        base = "https://content-cdn.chaosgroup.com"
    log.append("base " + base)
    n = 0
    while todo and n < 400:
        u = todo.pop(0)
        url = u if u.startswith("http") else (base + "/" + u.lstrip("/") if u.startswith(("/_next", "_next")) else
                                               base + "/_next/" + u.lstrip("/"))
        if url in seen:
            continue
        seen.add(url)
        try:
            js = get(url)
        except Exception as e:  # noqa: BLE001
            log.append(f"ERR {url} {e}")
            continue
        n += 1
        zf.writestr("js/" + re.sub(r"[^\w.\-]", "_", url.split("/_next/")[-1])[-120:], js)
        for m in re.findall(r'["\'](static/(?:chunks|css)/[^"\']+?\.js)["\']', js):
            todo.append("/_next/" + m)
        for m in re.findall(r'["\'](/_next/static/[^"\']+?\.js)["\']', js):
            todo.append(m)
        # carte webpack (runtime Next.js) : {id:"nom"} et {id:"hash"}
        i = js.find('"static/chunks/"')
        if i >= 0:
            zone = js[i:i + 60000]
            dicts = [dict(re.findall(r'(\d+):"([^"]+)"', d)) for d in re.findall(r'\{(?:\d+:"[^"]*",?)+\}', zone)]
            names = next((d for d in dicts if d and not all(re.fullmatch(r"[0-9a-f]{8,20}", v) for v in d.values())), {})
            for d in dicts:
                if d and all(re.fullmatch(r"[0-9a-f]{8,20}", v) for v in d.values()):
                    for cid, h in d.items():
                        todo.append(f"/_next/static/chunks/{names.get(cid, cid)}.{h}.js")
    log.append(f"{n} fichiers JS telecharges")
    zf.writestr("journal.txt", "\n".join(log))
    zf.close()
    print("ecrit :", OUT)
    try:
        os.startfile(str(OUT.parent))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
