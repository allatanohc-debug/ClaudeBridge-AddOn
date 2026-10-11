# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Mise a jour de l'Add-On Claude Bridge, en un clic depuis la palette Tapir.

A placer dans : Documents\\Tapir\\custom-scripts\\
Puis, dans Archicad : palette Tapir > choisir ce script > Executer.

Ce que fait le script :
  1. telecharge la derniere version publiee sur GitHub ;
  2. si elle est deja installee, s'arrete ;
  3. sinon ferme Archicad, remplace le fichier, puis relance Archicad
     (avec -DEMO si Archicad avait ete lance en mode demo).

L'Add-On installe est toujours : Documents\\ClaudeBridge\\ClaudeBridge.apx
"""

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

DOWNLOAD_URL = "https://github.com/allatanohc-debug/ClaudeBridge-AddOn/releases/latest/download/ClaudeBridge.apx"


def documents_folder() -> Path:
    # Dossier "Documents" reel (y compris s'il est redirige vers OneDrive).
    buffer = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
    return Path(buffer.value)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def run_tapir(port: int, command: str, parameters: dict | None = None) -> dict:
    payload = {
        "command": "API.ExecuteAddOnCommand",
        "parameters": {
            "addOnCommandId": {"commandNamespace": "TapirCommand", "commandName": command},
            "addOnCommandParameters": parameters or {},
        },
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


HELPER_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$newFile = '{new_file}'
$target  = '{target}'
$archicad = Get-CimInstance Win32_Process -Filter "Name='Archicad.exe'" | Select-Object -First 1
if ($archicad) {{
    $exe  = $archicad.ExecutablePath
    $demo = $archicad.CommandLine -match '-DEMO'
    Wait-Process -Id $archicad.ProcessId -Timeout 300
}}
Start-Sleep -Seconds 2
$done = $false
for ($i = 0; $i -lt 60 -and -not $done; $i++) {{
    try {{ Copy-Item -LiteralPath $newFile -Destination $target -Force -ErrorAction Stop; $done = $true }}
    catch {{ Start-Sleep -Seconds 2 }}
}}
if ($done) {{ Remove-Item -LiteralPath $newFile -Force }}
if ($exe) {{
    if ($demo) {{ Start-Process -FilePath $exe -ArgumentList '-DEMO' }} else {{ Start-Process -FilePath $exe }}
}}
"""


def start_detached_helper(new_file: Path, target: Path) -> None:
    helper = Path(tempfile.gettempdir()) / "ClaudeBridge_update.ps1"
    helper.write_text(HELPER_SCRIPT.format(new_file=new_file, target=target), encoding="utf-8-sig")
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(helper)]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Sortir du "job" d'Archicad pour survivre a sa fermeture.
        subprocess.Popen(command, creationflags=flags | 0x01000000, close_fds=True)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(command, creationflags=flags, close_fds=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19723)
    args, _ = parser.parse_known_args()

    install_folder = documents_folder() / "ClaudeBridge"
    install_folder.mkdir(parents=True, exist_ok=True)
    target = install_folder / "ClaudeBridge.apx"
    new_file = install_folder / "ClaudeBridge.apx.new"

    print("Telechargement de la derniere version de Claude Bridge...")
    try:
        with urllib.request.urlopen(DOWNLOAD_URL, timeout=120) as response:
            data = response.read()
    except Exception as error:  # noqa: BLE001
        print(f"Echec du telechargement : {error}")
        return 1

    if len(data) < 50_000 or not data.startswith(b"MZ"):
        print("Le fichier telecharge ne ressemble pas a un Add-On valide. Mise a jour annulee.")
        return 1

    if hashlib.sha256(data).hexdigest() == sha256(target):
        print("Claude Bridge est deja a jour. Rien a faire.")
        return 0

    if not target.exists():
        target.write_bytes(data)
        print(f"Premiere installation : fichier copie dans {target}")
        print("Ajoute-le une fois via Options > Gestionnaire d'Add-Ons, puis redemarre Archicad.")
        return 0

    new_file.write_bytes(data)
    start_detached_helper(new_file, target)
    print("Nouvelle version prete. Archicad va se fermer puis se relancer automatiquement.")
    print("Si Archicad demande d'enregistrer le projet, reponds selon ton besoin.")
    time.sleep(1)
    try:
        run_tapir(args.port, "QuitArchicad")
    except Exception as error:  # noqa: BLE001
        print(f"Impossible de fermer Archicad automatiquement ({error}). Ferme-le toi-meme : la mise a jour se fera ensuite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
