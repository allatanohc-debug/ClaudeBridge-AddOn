# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Couleur des ombres - script Tapir (Archicad 28)

A placer dans : Documents\\Tapir\\custom-scripts\\ (a cote de Ombres.py)
Puis dans Archicad : palette Tapir > choisir "OmbresCouleur.py" > Executer.

Ouvre le selecteur de couleurs de Windows. La couleur choisie :
  - est appliquee tout de suite aux ombres deja dessinees ;
  - est retenue pour les prochaines fois que tu lances Ombres.py.
Bouton "Gris par defaut" : revient a la couleur de depart.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import Ombres  # noqa: E402


def choose_color(current: tuple[float, float, float]) -> tuple[float, float, float] | None | str:
    import tkinter as tk
    from tkinter import colorchooser

    result: dict = {"value": None}
    root = tk.Tk()
    root.title("Couleur des ombres")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    def to_hex(c):
        return "#" + "".join(f"{round(v * 255):02X}" for v in c)

    swatch = tk.Label(root, width=12, height=3, bg=to_hex(current), relief="groove")
    swatch.grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8))
    chosen = {"color": current}

    def pick():
        rgb, _ = colorchooser.askcolor(color=to_hex(chosen["color"]), title="Couleur des ombres", parent=root)
        if rgb:
            chosen["color"] = tuple(v / 255.0 for v in rgb)
            swatch.configure(bg=to_hex(chosen["color"]))

    def default():
        chosen["color"] = Ombres.COULEUR_DEFAUT
        swatch.configure(bg=to_hex(chosen["color"]))

    def ok():
        result["value"] = chosen["color"]
        root.destroy()

    tk.Button(root, text="Choisir une couleur...", command=pick).grid(row=1, column=0, padx=(16, 4), pady=8)
    tk.Button(root, text="Gris par defaut", command=default).grid(row=1, column=1, padx=4, pady=8)
    tk.Button(root, text="Appliquer", command=ok, default="active").grid(row=1, column=2, padx=(4, 16), pady=8)
    root.bind("<Return>", lambda _e: ok())
    root.mainloop()
    return result["value"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=Ombres.PORT)
    args, _ = parser.parse_known_args()
    Ombres.PORT = args.port

    try:
        color = choose_color(Ombres.shadow_color())
    except Exception as error:  # noqa: BLE001
        print(f"Impossible d'ouvrir le selecteur de couleurs : {error}")
        return 1
    if color is None:
        print("Aucun changement.")
        return 0

    settings = Ombres.load_json("ombres_reglages.json")
    settings["couleur"] = list(color)
    Ombres.save_json("ombres_reglages.json", settings)

    if Ombres.set_shadow_pen(color):
        print("Couleur des ombres mise a jour.")
    else:
        print("Couleur retenue : elle sera appliquee au prochain lancement de Ombres.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
