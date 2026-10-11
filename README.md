# Claude Bridge — Add-On Archicad 28

Extension Archicad qui dessine, à côté du plan, le **tableau des surfaces des zones** de l'étage courant :

| Couleur | Zone | Surface |
| --- | --- | --- |
| case remplie de la couleur de la zone | numéro + nom | surface en m² |

- La couleur vient de la **catégorie de zone** Archicad. Si deux zones ont la même couleur, la suivante reçoit une nuance plus claire ou plus foncée : chaque zone a une couleur unique.
- Le total des surfaces est en dernière ligne.
- Relancer la commande **remplace** le tableau existant (mise à jour après modification des zones).
- Tout le tableau s'annule en un seul **Ctrl+Z**.

## Utilisation

Dans Archicad, sur le plan de l'étage voulu : **Options > Tableau des surfaces (Claude Bridge)**.

Claude peut aussi lancer l'action via la commande JSON `ClaudeBridge / CreateZoneAreaTable`.

## Compilation (dans le cloud)

Chaque envoi sur GitHub lance le workflow **Compiler Claude Bridge** (onglet *Actions*). Le fichier `ClaudeBridge.apx` se télécharge dans la section *Artifacts* du build terminé.

## Installation

1. Ranger `ClaudeBridge.apx` dans un dossier fixe.
2. Archicad > Options > Gestionnaire d'Add-Ons > Ajouter.
3. Pendant les tests, lancer Archicad en mode démo : `Archicad.exe -DEMO`.
4. Pour l'utiliser dans Archicad normal : créer des identifiants MDID gratuits sur https://archicadapi.graphisoft.com/profile/add-ons et les mettre dans `RFIX/AddOnFix.grc`.

Basé sur le template officiel [GRAPHISOFT/archicad-addon-cmake](https://github.com/GRAPHISOFT/archicad-addon-cmake) (licence MIT).
