"""CosmosHote - TEST : V-Ray Link se connecte a Chaos Cosmos comme logiciel hote (comme V-Ray pour SketchUp),
sans SketchUp. Ouvre Cosmos ; chaque materiau/objet telecharge est recu et ses fichiers sont listes.

A lancer depuis Tapir (Archicad ouvert), SketchUp ferme, Cosmos installe.
Journal : "cosmos_hote.txt" sur le Bureau (a m'envoyer).
"""
import http.client
import json
import os
import re
import subprocess
import sys
import ssl
import struct
import threading
import time
import uuid
import webbrowser
from pathlib import Path

VERSION = "19/10 - test hote 4 (plateforme SketchUp)"
HOME = Path.home()
DESK = next((p for p in (HOME / "OneDrive" / "Desktop", HOME / "OneDrive" / "Bureau", HOME / "Desktop") if p.exists()), HOME)
LOG = DESK / "cosmos_hote.txt"
CBSERVICE = Path(r"C:\Program Files\Chaos\Cosmos\cbservice.exe")
# Cosmos ne connait que les logiciels qu'il supporte : on se presente avec la meme "plateforme"
# que V-Ray pour SketchUp (memes formats de fichiers V-Ray), sous le nom V-Ray Link.
HOTE = ("SketchUp", "26.2.243")
IMPORTER_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "claudebridge/vray-link/archicad"))
SVC = "/content.v1beta2."
BASES = [("http", 30305), ("https", 30306)]

_log_lock = threading.Lock()
_ui_log = None


def log(*a):
    s = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    with _log_lock:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(s + "\n")
    if _ui_log:
        _ui_log(s)


# ---------------------------------------------------------------- protobuf minimal (sans dependance)
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


# ---------------------------------------------------------------- gRPC-Web
def _conn(base, timeout):
    scheme, port = base
    if scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE        # certificat local auto-signe de Cosmos (127.0.0.1)
        return http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout, context=ctx)
    return http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)


def _frames(resp):
    while True:
        head = resp.read(5)
        if len(head) < 5:
            return
        flag, ln = head[0], struct.unpack(">I", head[1:])[0]
        body = resp.read(ln) if ln else b""
        yield flag, body


def call(base, method, msg: bytes, timeout=15, stream=False):
    c = _conn(base, None if stream else timeout)
    c.request("POST", SVC + method, body=b"\x00" + struct.pack(">I", len(msg)) + msg,
              headers={"Content-Type": "application/grpc-web+proto", "X-Grpc-Web": "1",
                       "Accept": "application/grpc-web+proto", "X-User-Agent": "grpc-web-python/vraylink"})
    r = c.getresponse()
    ct = r.getheader("Content-Type", "")
    if r.status != 200 or "grpc" not in ct:
        raise RuntimeError(f"HTTP {r.status} {ct} {r.read(200)!r}")
    gst = r.getheader("grpc-status")
    if gst and gst != "0":
        raise RuntimeError(f"grpc-status {gst} {r.getheader('grpc-message')}")
    if stream:
        return r
    msgs, trailers = [], b""
    for flag, body in _frames(r):
        if flag & 0x80:
            trailers = body
        else:
            msgs.append(body)
    if b"grpc-status:" in trailers and b"grpc-status:0" not in trailers.replace(b" ", b""):
        raise RuntimeError("trailers " + trailers.decode("utf-8", "replace"))
    return msgs[0] if msgs else b""



# ---------------------------------------------------------------- gRPC natif (ImportAPI n'est pas sur le gRPC-Web)
def cbservice_ports():
    """Ports TCP sur lesquels ecoute cbservice.exe (+ ceux de runtime.json)."""
    ports = []
    rt = Path(os.environ.get("LOCALAPPDATA", HOME)) / "Chaos" / "Cosmos" / "runtime.json"
    if rt.exists():
        t = rt.read_text("utf-8", "replace")
        log("runtime.json :", t.replace("\n", " ")[:600])
        ports += [int(p) for p in re.findall(r"(?:port\D{1,4}|:)(\d{4,5})", t)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        tl = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cbservice.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, encoding="mbcs" if os.name == "nt" else None, errors="replace", creationflags=flags, timeout=20).stdout
        pids = set(re.findall(r'"cbservice\.exe","(\d+)"', tl, re.I))
        ns = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, encoding="mbcs" if os.name == "nt" else None, errors="replace",
                            creationflags=flags, timeout=20).stdout
        for line in ns.splitlines():
            m = re.search(r"TCP\s+(127\.0\.0\.1|0\.0\.0\.0|\[::1?\]):(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
            if m and m.group(3) in pids:
                ports.append(int(m.group(2)))
        log("cbservice pid", sorted(pids), "ecoute sur", sorted(set(ports)))
    except Exception as e:  # noqa: BLE001
        log("netstat :", e)
    ports += [30305, 30306]
    return list(dict.fromkeys(ports))


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


class Native:
    def __init__(self, port, tls):
        self.port, self.tls = port, tls
        self.nom = f"127.0.0.1:{port} {'TLS' if tls else 'h2c'}"

    def unary(self, method, msg, timeout=10):
        c = H2Call(self.port, self.tls, timeout)
        try:
            c.request(method, msg)
            for m in c.messages():
                return m
            raise RuntimeError("aucune reponse (methode inconnue ou refusee)")
        finally:
            c.close()

    def stream(self, method, msg):
        c = H2Call(self.port, self.tls, 15)
        c.request(method, msg)
        c.s.settimeout(None)
        try:
            yield from c.messages()
        finally:
            c.close()


def find_native():
    for port in cbservice_ports():
        for tls in (False, True):
            api = Native(port, tls)
            try:
                res = decode(api.unary("ImportAPI/ListImporters", b"", timeout=4))
                log("ImportAPI OK sur", api.nom, "- hotes :", show(res))
                return api
            except Exception as e:  # noqa: BLE001
                log("   ", api.nom, ":", str(e)[:160])
    return None


# ---------------------------------------------------------------- messages
def importer_info():
    return pb((1, "V-Ray Link (Archicad)"), (2, IMPORTER_ID), (3, os.getpid()))


def wait_for_import_request():
    features = pb((1, True), (2, True), (4, True), (6, True), (7, True), (8, True), (9, True), (11, True),
                  (12, pb((1, True), (2, True), (3, True))), (14, True), (15, True), (16, True), (18, True))
    options = pb((3, [1, 2]))
    return pb((1, importer_info()),
              (2, pb((1, "V-Ray"), (2, "7.20.00"))),
              (3, pb((1, HOTE[0]), (2, HOTE[1]))),
              (4, options), (5, features))


def show(obj, ind="   "):
    return json.dumps(obj, ensure_ascii=False, default=str, indent=1)[:6000].replace("\n", "\n" + ind)


def any_unary(base, native, meth, msg):
    try:
        return call(base, meth, msg)
    except Exception:  # noqa: BLE001
        if native is None:
            raise
        return native.unary(meth, msg)


def handle_import(base, ev, native=None):
    pid = (ev.get(2) or [""])[0]
    rev = (ev.get(3) or [0])[0]
    var = (ev.get(6) or [""])[0]
    log(f"*** IMPORT RECU : package {pid} revision {rev} variante {var!r}")
    log("   evenement brut :", show(ev))
    for nom, meth, msg in [
        ("FindAllRepresentations", "PackagesAPI/FindAllRepresentations", pb((1, pid), (3, rev))),
        ("FindPackageFileInfo", "PackagesAPI/FindPackageFileInfo", pb((1, pid), (2, str(rev)))),
        ("FindPackageById", "PackagesAPI/FindPackageById", pb((1, pid), (2, rev))),
        ("ResolveFilePaths", "PathAPI/ResolveFilePaths", pb((1, pid), (2, rev), (4, var))),
    ]:
        try:
            log(f"   {nom} ->", show(decode(any_unary(base, native, meth, msg))))
        except Exception as e:  # noqa: BLE001
            log(f"   {nom} ERREUR", e)
    pk = HOME / "OneDrive" / "Documents" / "Chaos Cosmos" / "Packages"
    if not pk.exists():
        pk = HOME / "Documents" / "Chaos Cosmos" / "Packages"
    if pk.exists():
        recent = sorted((f for f in pk.rglob("*") if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)[:25]
        log("   fichiers recents dans Packages :")
        for f in recent:
            log("     ", f, f.stat().st_size)


def listen(base, native, stop):
    while not stop.is_set():
        try:
            it = native.stream("ImportAPI/WaitForImport", wait_for_import_request())
            log("CONNECTE comme hote Cosmos sur", native.nom, "- id", IMPORTER_ID)
            for body in it:
                if body:
                    threading.Thread(target=handle_import, args=(base, decode(body), native), daemon=True).start()
            log("fin du flux")
        except Exception as e:  # noqa: BLE001
            log("flux interrompu :", str(e).replace("\n", " ")[:300])
        if not stop.is_set():
            time.sleep(3)


def listen_cancel(native, stop):
    try:
        for body in native.stream("ImportAPI/WaitForCancel", pb((1, importer_info()))):
            if body:
                log("annulation demandee :", show(decode(body)))
    except Exception as e:  # noqa: BLE001
        log("flux annulation :", str(e).replace("\n", " ")[:200])


def port_ok(port):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main():
    global _ui_log
    import tkinter as tk
    LOG.write_text(f"CosmosHote {VERSION}\n", encoding="utf-8")
    root = tk.Tk()
    root.title("V-Ray Link - test hote Cosmos")
    root.geometry("760x420")
    txt = tk.Text(root, wrap="word", font=("Consolas", 9))
    txt.pack(fill="both", expand=True)

    def ui(s):
        root.after(0, lambda: (txt.insert("end", s[:400] + "\n"), txt.see("end")))
    _ui_log = ui

    def start():
        if not port_ok(30305) and CBSERVICE.exists():
            log("Cosmos arrete -> demarrage de cbservice.exe")
            os.startfile(str(CBSERVICE))  # type: ignore[attr-defined]
            for _ in range(20):
                time.sleep(1)
                if port_ok(30305):
                    break
        base = None
        for b in BASES:
            try:
                call(b, "SystemAPI/GetSystemInfo", b"")
                log("API web Cosmos OK sur", b)
                base = b
                break
            except Exception as e:  # noqa: BLE001
                log("API web sur", b, ":", e)
        base = base or BASES[0]
        native = find_native()
        if not native:
            log("ECHEC : ImportAPI introuvable. Envoie ce journal.")
            return
        stop = threading.Event()
        threading.Thread(target=listen, args=(base, native, stop), daemon=True).start()
        threading.Thread(target=listen_cancel, args=(native, stop), daemon=True).start()
        time.sleep(2)
        try:
            log("hotes connectes maintenant :", show(decode(native.unary("ImportAPI/ListImporters", b""))))
        except Exception as e:  # noqa: BLE001
            log("ListImporters :", e)
        scheme, port = base
        host = "localhost" if scheme == "https" else "127.0.0.1"
        url = (f"https://localhost:30306/?utm_campaign=cosmos&utm_source=V-Ray+7.20.00"
               f"&utm_medium={HOTE[0]}+Pro+{HOTE[1]}&importer_id={IMPORTER_ID}")
        log("ouverture de Cosmos :", url)
        webbrowser.open(url)
        log(">>> Dans Cosmos : telecharge UN materiau (ex. beton) puis UN objet. Laisse cette fenetre ouverte.")

    threading.Thread(target=start, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
