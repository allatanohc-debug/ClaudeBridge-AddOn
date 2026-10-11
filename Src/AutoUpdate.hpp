#ifndef CLAUDEBRIDGE_AUTOUPDATE_HPP
#define CLAUDEBRIDGE_AUTOUPDATE_HPP

// Au demarrage d'Archicad, verifie en arriere-plan (sans fenetre) si une nouvelle
// version de Claude Bridge est publiee sur GitHub. Si oui, elle est telechargee et
// mise en place a cote du fichier actuel : elle sera chargee au prochain demarrage
// d'Archicad. Aucune action n'est demandee a l'utilisateur.
void StartBackgroundUpdateCheck ();

#endif
