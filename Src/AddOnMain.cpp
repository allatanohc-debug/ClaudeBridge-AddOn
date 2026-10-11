// =============================================================================
//  Claude Bridge - Add-On Archicad 28
//
//  Menu Options > "Tableau des surfaces (Claude Bridge)" :
//      dessine a cote du plan le tableau des surfaces des zones de l'etage,
//      avec la couleur de chaque zone.
//
//  Commandes JSON (namespace "ClaudeBridge"), utilisables par Claude via MCP :
//      Ping                 - verifie que l'Add-On est charge
//      CreateZoneAreaTable  - meme action que le menu
// =============================================================================

#include "APIEnvir.h"
#include "ACAPinc.h"

#include "ResourceIds.hpp"
#include "RS.hpp"
#include "UniString.hpp"
#include "ObjectState.hpp"

#include "ZoneAreaTable.hpp"
#include "AutoUpdate.hpp"

static const GSResID AddOnInfoID			= ID_ADDON_INFO;
	static const Int32 AddOnNameID			= 1;
	static const Int32 AddOnDescriptionID	= 2;

static const short AddOnMenuID				= ID_ADDON_MENU;
	static const Int32 ZoneTableCommandID	= 1;

// -----------------------------------------------------------------------------
//  Commandes JSON
// -----------------------------------------------------------------------------

class ClaudeBridgeCommand : public API_AddOnCommand {
public:
	virtual GS::String GetNamespace () const override { return "ClaudeBridge"; }
	virtual GS::Optional<GS::UniString> GetSchemaDefinitions () const override { return {}; }
	virtual GS::Optional<GS::UniString> GetInputParametersSchema () const override { return {}; }
	virtual GS::Optional<GS::UniString> GetResponseSchema () const override { return {}; }
	virtual API_AddOnCommandExecutionPolicy GetExecutionPolicy () const override
	{
		return API_AddOnCommandExecutionPolicy::ScheduleForExecutionOnMainThread;
	}
	virtual bool IsProcessWindowVisible () const override { return false; }
	virtual void OnResponseValidationFailed (const GS::ObjectState& /*response*/) const override {}
};

class PingCommand : public ClaudeBridgeCommand {
public:
	virtual GS::String GetName () const override { return "Ping"; }

	virtual GS::ObjectState Execute (const GS::ObjectState& /*parameters*/, GS::ProcessControl& /*processControl*/) const override
	{
		GS::ObjectState response;
		response.Add ("success", true);
		response.Add ("addOnName", GS::UniString (ADDON_NAME));
		response.Add ("version", GS::UniString (ADDON_VERSION));
		return response;
	}
};

class CreateZoneAreaTableCommand : public ClaudeBridgeCommand {
public:
	virtual GS::String GetName () const override { return "CreateZoneAreaTable"; }

	virtual GS::ObjectState Execute (const GS::ObjectState& /*parameters*/, GS::ProcessControl& /*processControl*/) const override
	{
		const ZoneAreaTableResult result = CreateZoneAreaTable ();
		GS::ObjectState response;
		response.Add ("success", result.success);
		response.Add ("message", result.message);
		response.Add ("zoneCount", result.zoneCount);
		response.Add ("totalArea", result.totalArea);
		return response;
	}
};

// -----------------------------------------------------------------------------
//  Menu
// -----------------------------------------------------------------------------

static GSErrCode MenuCommandHandler (const API_MenuParams* menuParams)
{
	if (menuParams->menuItemRef.menuResID == AddOnMenuID &&
		menuParams->menuItemRef.itemIndex == ZoneTableCommandID)
	{
		const ZoneAreaTableResult result = CreateZoneAreaTable ();
		ACAPI_WriteReport ("%T", true, result.message.ToPrintf ());
	}
	return NoError;
}

// -----------------------------------------------------------------------------
//  Bibliotheque integree du template (conservee telle quelle)
// -----------------------------------------------------------------------------

#if defined(macintosh)

bool CCALL ResourceTypesCallback (GSResType resType, GSResModule /*resModule*/, void *userData)
{
	constexpr GSResType libPartType = 'FLDR';
	if (resType == libPartType) {
		bool* hasLibPart = static_cast<bool*> (userData);
		*hasLibPart = true;
		return false;
	}
	return true;
}

#else

bool CCALL ResourceTypesCallback (const WCHAR* resType, GSResModule /*resModule*/, void *userData)
{
	constexpr auto libPartType = "FLDR";
	if (GS::UniString (resType) == libPartType) {
		bool* hasLibPart = static_cast<bool*> (userData);
		*hasLibPart = true;
		return false;
	}
	return true;
}

#endif

static bool HasBuiltInLibPart ()
{
	bool hasLibPart = false;
	RSEnumResourceTypes (ResourceTypesCallback, &hasLibPart, ACAPI_GetOwnResModule ());
	return hasLibPart;
}

// -----------------------------------------------------------------------------
//  Points d'entree de l'Add-On
// -----------------------------------------------------------------------------

API_AddonType CheckEnvironment (API_EnvirParams* envir)
{
	RSGetIndString (&envir->addOnInfo.name, AddOnInfoID, AddOnNameID, ACAPI_GetOwnResModule ());
	RSGetIndString (&envir->addOnInfo.description, AddOnInfoID, AddOnDescriptionID, ACAPI_GetOwnResModule ());

	// Reste charge pour repondre aux commandes JSON a tout moment.
	return APIAddon_Preload;
}

GSErrCode RegisterInterface (void)
{
	GSErrCode err = ACAPI_MenuItem_RegisterMenu (AddOnMenuID, 0, MenuCode_Tools, MenuFlag_Default);
	if (err == NoError && HasBuiltInLibPart ()) {
		err = ACAPI_AddOnIntegration_RegisterBuiltInLibrary ();
	}
	return err;
}

GSErrCode Initialize (void)
{
	GSErrCode err = ACAPI_MenuItem_InstallMenuHandler (AddOnMenuID, MenuCommandHandler);
	if (err == NoError) {
		err = ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (GS::NewOwned<PingCommand> ());
	}
	if (err == NoError) {
		err = ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (GS::NewOwned<CreateZoneAreaTableCommand> ());
	}
	if (err == NoError) {
		StartBackgroundUpdateCheck ();
	}
	return err;
}

GSErrCode FreeData (void)
{
	return NoError;
}
