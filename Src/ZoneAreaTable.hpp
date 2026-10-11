#ifndef CLAUDEBRIDGE_ZONEAREATABLE_HPP
#define CLAUDEBRIDGE_ZONEAREATABLE_HPP

#include "UniString.hpp"

// Resultat de la creation du tableau des surfaces.
struct ZoneAreaTableResult {
	bool			success = false;
	GS::UniString	message;
	Int32			zoneCount = 0;
	double			totalArea = 0.0;
};

// Lit toutes les zones de l'etage courant (nom, numero, surface, couleur de categorie)
// et dessine a droite du plan un tableau : une case de couleur, le nom et la surface
// de chaque zone, puis le total. Chaque zone recoit une couleur unique : si deux zones
// ont la meme couleur Archicad, la suivante recoit une nuance plus claire ou plus foncee.
// Un tableau existant sur l'etage est remplace (mise a jour).
ZoneAreaTableResult CreateZoneAreaTable ();

#endif
