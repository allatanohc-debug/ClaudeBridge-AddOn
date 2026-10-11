// =============================================================================
//  Claude Bridge - mise a jour automatique silencieuse
//
//  Principe (Windows) :
//    1. au demarrage, un PowerShell cache telecharge la derniere version publiee ;
//    2. si elle differe du fichier charge, le fichier actuel est renomme en .old
//       (Windows autorise le renommage d'une extension en cours d'utilisation)
//       et la nouvelle version prend sa place ;
//    3. au demarrage suivant, Archicad charge la nouvelle version et le .old est supprime.
//  En cas d'echec (pas d'internet, droits insuffisants...), rien ne change.
// =============================================================================

#include "APIEnvir.h"
#include "ACAPinc.h"

#include "AutoUpdate.hpp"

#include "Location.hpp"
#include "Process.hpp"

#include <string>

namespace {

const char* LatestReleaseUrl = "https://github.com/allatanohc-debug/ClaudeBridge-AddOn/releases/latest/download/ClaudeBridge.apx";

GS::Process updaterProcess;

// PowerShell -EncodedCommand attend le script en UTF-16LE encode en base64 :
// cela evite tout probleme de guillemets dans la ligne de commande.
GS::UniString EncodeForPowerShell (const GS::UniString& script)
{
	static const char* alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

	std::string bytes;
	const UIndex length = script.GetLength ();
	bytes.reserve (static_cast<size_t> (length) * 2);
	for (UIndex i = 0; i < length; ++i) {
		const unsigned short unit = static_cast<unsigned short> (script.GetChar (i));
		bytes.push_back (static_cast<char> (unit & 0xFF));
		bytes.push_back (static_cast<char> ((unit >> 8) & 0xFF));
	}

	std::string encoded;
	size_t i = 0;
	while (i + 2 < bytes.size ()) {
		const unsigned int n = (static_cast<unsigned char> (bytes[i]) << 16) |
							   (static_cast<unsigned char> (bytes[i + 1]) << 8) |
							   static_cast<unsigned char> (bytes[i + 2]);
		encoded.push_back (alphabet[(n >> 18) & 63]);
		encoded.push_back (alphabet[(n >> 12) & 63]);
		encoded.push_back (alphabet[(n >> 6) & 63]);
		encoded.push_back (alphabet[n & 63]);
		i += 3;
	}
	const size_t rest = bytes.size () - i;
	if (rest == 1) {
		const unsigned int n = static_cast<unsigned char> (bytes[i]) << 16;
		encoded.push_back (alphabet[(n >> 18) & 63]);
		encoded.push_back (alphabet[(n >> 12) & 63]);
		encoded += "==";
	} else if (rest == 2) {
		const unsigned int n = (static_cast<unsigned char> (bytes[i]) << 16) | (static_cast<unsigned char> (bytes[i + 1]) << 8);
		encoded.push_back (alphabet[(n >> 18) & 63]);
		encoded.push_back (alphabet[(n >> 12) & 63]);
		encoded.push_back (alphabet[(n >> 6) & 63]);
		encoded += "=";
	}
	return GS::UniString (encoded.c_str ());
}

}	// namespace


void StartBackgroundUpdateCheck ()
{
#if defined (WINDOWS)
	IO::Location ownLocation;
	if (ACAPI_GetOwnLocation (&ownLocation) != NoError) {
		return;
	}
	GS::UniString ownPath;
	if (ownLocation.ToPath (&ownPath) != NoError || ownPath.IsEmpty ()) {
		return;
	}
	ownPath.ReplaceAll (GS::UniString ("'"), GS::UniString ("''"));

	const GS::UniString script =
		GS::UniString ("$ErrorActionPreference = 'Stop'\n") +
		"$own = '" + ownPath + "'\n" +
		"$old = $own + '.old'\n" +
		"if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue }\n" +
		"$tmp = Join-Path $env:TEMP 'ClaudeBridge_latest.apx'\n" +
		"[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12\n" +
		"Invoke-WebRequest -UseBasicParsing -Uri '" + LatestReleaseUrl + "' -OutFile $tmp\n" +
		"$bytes = [IO.File]::ReadAllBytes($tmp)\n" +
		"if ($bytes.Length -lt 50000 -or $bytes[0] -ne 77 -or $bytes[1] -ne 90) { Remove-Item -LiteralPath $tmp -Force; exit 1 }\n" +
		"if ((Get-FileHash -LiteralPath $tmp).Hash -eq (Get-FileHash -LiteralPath $own).Hash) { Remove-Item -LiteralPath $tmp -Force; exit 0 }\n" +
		"Move-Item -LiteralPath $own -Destination $old -Force\n" +
		"try { Move-Item -LiteralPath $tmp -Destination $own -Force }\n" +
		"catch { Move-Item -LiteralPath $old -Destination $own -Force; exit 1 }\n";

	GS::Array<GS::UniString> argv;
	argv.Push ("-NoProfile");
	argv.Push ("-NonInteractive");
	argv.Push ("-WindowStyle");
	argv.Push ("Hidden");
	argv.Push ("-EncodedCommand");
	argv.Push (EncodeForPowerShell (script));

	try {
		updaterProcess = GS::Process::Create ("powershell.exe", argv, GS::Process::CreateNoWindow);
	} catch (...) {
		// Pas de mise a jour possible : l'extension continue de fonctionner normalement.
	}
#endif
}
