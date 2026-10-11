// =============================================================================
//  Claude Bridge - Tableau des surfaces par zone
//
//  Dessine a droite du plan de l'etage courant un tableau avec, pour chaque zone :
//  une case remplie de la couleur de la zone, son numero et son nom, sa surface.
//  Le total figure en derniere ligne. Relancer la commande met le tableau a jour.
// =============================================================================

#include "APIEnvir.h"
#include "ACAPinc.h"

#include "ZoneAreaTable.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <string>
#include <string_view>
#include <vector>

namespace {

// Marque posee sur chaque element du tableau, pour pouvoir le remplacer ensuite.
const char*	TableTag = "ClaudeBridge.ZoneAreaTable.v1";

// Mise en page, en millimetres "papier" (convertis selon l'echelle du plan).
const double	GapFromPlanMm		= 15.0;		// espace entre le plan et le tableau
const double	TableWidthMm		= 95.0;		// largeur totale du tableau
const double	RowHeightMm			= 7.0;		// hauteur d'une ligne
const double	SwatchLeftMm		= 2.0;		// case de couleur : debut
const double	SwatchWidthMm		= 12.0;		// case de couleur : largeur
const double	SwatchInsetMm		= 1.2;		// marge haute et basse de la case
const double	NameLeftMm			= 17.0;		// colonne du nom
const double	AreaRightMm			= 92.0;		// bord droit de la colonne surface
const double	ColumnSeparatorMm	= 63.0;		// trait vertical entre zones et surfaces
const double	TextSizeMm			= 2.5;		// hauteur du texte courant
const double	TitleSizeMm			= 3.5;		// hauteur du titre
const double	TitleSpaceMm		= 9.0;		// place reservee au titre au-dessus du tableau

struct ZoneRow {
	GS::UniString	number;
	GS::UniString	name;
	double			area = 0.0;
	API_RGBColor	color = {};
};

struct PlanBounds {
	bool	valid = false;
	double	xMin = 0.0;
	double	yMin = 0.0;
	double	xMax = 0.0;
	double	yMax = 0.0;

	void Add (const API_Box3D& box)
	{
		if (!valid) {
			xMin = box.xMin; yMin = box.yMin; xMax = box.xMax; yMax = box.yMax;
			valid = true;
			return;
		}
		xMin = std::min (xMin, box.xMin);
		yMin = std::min (yMin, box.yMin);
		xMax = std::max (xMax, box.xMax);
		yMax = std::max (yMax, box.yMax);
	}
};

GS::UniString Utf16 (std::u16string_view text)
{
	return GS::UniString (text);
}

std::string ToUtf8 (const GS::UniString& text)
{
	return std::string (text.ToCStr (CC_UTF8).Get ());
}

GS::UniString FormatArea (double area)
{
	GS::UniString text = GS::UniString::Printf ("%.2f", area);
	text.ReplaceAll (GS::UniString ("."), GS::UniString (","));
	text += Utf16 (u" m²");
	return text;
}

// ---------------------------------------------------------------- couleurs ---

double ColorDistance (const API_RGBColor& a, const API_RGBColor& b)
{
	const double dr = a.f_red - b.f_red;
	const double dg = a.f_green - b.f_green;
	const double db = a.f_blue - b.f_blue;
	return std::sqrt (dr * dr + dg * dg + db * db);
}

// amount > 0 : vers le blanc ; amount < 0 : vers le noir.
API_RGBColor Shade (const API_RGBColor& color, double amount)
{
	auto mix = [amount] (double value) {
		const double result = (amount >= 0.0) ? value + (1.0 - value) * amount : value * (1.0 + amount);
		return std::min (1.0, std::max (0.0, result));
	};
	API_RGBColor shaded = color;
	shaded.f_red = mix (color.f_red);
	shaded.f_green = mix (color.f_green);
	shaded.f_blue = mix (color.f_blue);
	return shaded;
}

bool IsColorUsed (const API_RGBColor& color, const std::vector<API_RGBColor>& used)
{
	for (const API_RGBColor& other : used) {
		if (ColorDistance (color, other) < 0.04) {
			return true;
		}
	}
	return false;
}

// Garde la couleur Archicad de chaque zone ; si elle est deja prise par une zone
// precedente, essaie des nuances de plus en plus eloignees (plus claire, plus foncee...).
void MakeColorsUnique (std::vector<ZoneRow>& rows)
{
	std::vector<API_RGBColor> used;
	for (ZoneRow& row : rows) {
		API_RGBColor candidate = row.color;
		for (int step = 1; step <= 12 && IsColorUsed (candidate, used); ++step) {
			double amount = 0.15 * ((step + 1) / 2);
			if (step % 2 == 0) {
				amount = -amount;
			}
			amount = std::min (0.9, std::max (-0.9, amount));
			candidate = Shade (row.color, amount);
		}
		row.color = candidate;
		used.push_back (candidate);
	}
}

// ------------------------------------------------------------------ etages ---

short GetCurrentStory (GS::UniString& storyName)
{
	API_StoryInfo storyInfo = {};
	if (ACAPI_ProjectSetting_GetStorySettings (&storyInfo) != NoError) {
		return 0;
	}

	const short actStory = storyInfo.actStory;
	if (storyInfo.data != nullptr) {
		const Int32 index = static_cast<Int32> (actStory) - static_cast<Int32> (storyInfo.firstStory);
		const Int32 count = static_cast<Int32> (storyInfo.lastStory) - static_cast<Int32> (storyInfo.firstStory) + 1;
		if (index >= 0 && index < count) {
			storyName = GS::UniString ((*storyInfo.data)[index].uName);
		}
		BMKillHandle (reinterpret_cast<GSHandle*> (&storyInfo.data));
	}
	return actStory;
}

// --------------------------------------------------- marquage des elements ---

void TagElement (const API_Guid& guid)
{
	API_Elem_Head head = {};
	head.guid = guid;

	API_ElementUserData userData = {};
	userData.dataVersion = 1;
	userData.platformSign = GS::Act_Platform_Sign;
	userData.flags = 0;
	const Int32 length = Strlen32 (TableTag) + 1;
	userData.dataHdl = BMAllocateHandle (length, ALLOCATE_CLEAR, 0);
	if (userData.dataHdl == nullptr) {
		return;
	}
	CHCopyC (TableTag, *userData.dataHdl);
	ACAPI_Element_SetUserData (&head, &userData);
	BMKillHandle (&userData.dataHdl);
}

bool IsTaggedElement (const API_Guid& guid)
{
	API_Elem_Head head = {};
	head.guid = guid;

	API_ElementUserData userData = {};
	if (ACAPI_Element_GetUserData (&head, &userData) != NoError) {
		return false;
	}

	bool tagged = false;
	if (userData.dataHdl != nullptr) {
		const Int32 length = Strlen32 (TableTag) + 1;
		if (BMGetHandleSize (userData.dataHdl) >= length) {
			tagged = (std::strncmp (*userData.dataHdl, TableTag, static_cast<size_t> (length)) == 0);
		}
		BMKillHandle (&userData.dataHdl);
	}
	return tagged;
}

// Supprime le tableau deja present sur l'etage courant (elements portant la marque).
void DeletePreviousTable ()
{
	GS::Array<API_Guid> toDelete;
	const API_ElemTypeID types[] = { API_HatchID, API_TextID, API_LineID };
	for (const API_ElemTypeID typeID : types) {
		GS::Array<API_Guid> guids;
		if (ACAPI_Element_GetElemList (API_ElemType (typeID), &guids, APIFilt_OnActFloor) != NoError) {
			continue;
		}
		for (const API_Guid& guid : guids) {
			if (IsTaggedElement (guid)) {
				toDelete.Push (guid);
			}
		}
	}
	if (!toDelete.IsEmpty ()) {
		ACAPI_Element_Delete (toDelete);
	}
}

// --------------------------------------------------- creation d'elements ---

GSErrCode CreateLine (const API_Coord& begC, const API_Coord& endC, short story)
{
	API_Element element = {};
	element.header.type = API_LineID;
	GSErrCode err = ACAPI_Element_GetDefaults (&element, nullptr);
	if (err != NoError) {
		return err;
	}
	element.header.floorInd = story;
	element.line.begC = begC;
	element.line.endC = endC;

	API_ElementMemo memo = {};
	err = ACAPI_Element_Create (&element, &memo);
	ACAPI_DisposeElemMemoHdls (&memo);
	if (err == NoError) {
		TagElement (element.header.guid);
	}
	return err;
}

GSErrCode CreateColorRectangle (double x, double y, double width, double height, const API_RGBColor& color,
								const API_AttributeIndex& solidFill, short story)
{
	API_Element element = {};
	element.header.type = API_HatchID;
	GSErrCode err = ACAPI_Element_GetDefaults (&element, nullptr);
	if (err != NoError) {
		return err;
	}
	element.header.floorInd = story;
	element.hatch.hatchType = API_FillHatch;
	element.hatch.fillInd = solidFill;
	element.hatch.hatchFlags = static_cast<UInt32> (APIHatch_HasFgRGBColor) | static_cast<UInt32> (APIHatch_HasBkgRGBColor);
	element.hatch.foregroundRGB = color;
	element.hatch.backgroundRGB = color;
	element.hatch.showArea = false;
	element.hatch.poly.nCoords = 5;
	element.hatch.poly.nSubPolys = 1;
	element.hatch.poly.nArcs = 0;

	API_ElementMemo memo = {};
	memo.coords = reinterpret_cast<API_Coord**> (BMhAllClear ((element.hatch.poly.nCoords + 1) * sizeof (API_Coord)));
	memo.pends = reinterpret_cast<Int32**> (BMhAllClear ((element.hatch.poly.nSubPolys + 1) * sizeof (Int32)));
	if (memo.coords == nullptr || memo.pends == nullptr) {
		ACAPI_DisposeElemMemoHdls (&memo);
		return APIERR_MEMFULL;
	}
	(*memo.coords)[1].x = x;			(*memo.coords)[1].y = y;
	(*memo.coords)[2].x = x + width;	(*memo.coords)[2].y = y;
	(*memo.coords)[3].x = x + width;	(*memo.coords)[3].y = y + height;
	(*memo.coords)[4].x = x;			(*memo.coords)[4].y = y + height;
	(*memo.coords)[5] = (*memo.coords)[1];
	(*memo.pends)[0] = 0;
	(*memo.pends)[1] = element.hatch.poly.nCoords;

	err = ACAPI_Element_Create (&element, &memo);
	ACAPI_DisposeElemMemoHdls (&memo);
	if (err == NoError) {
		TagElement (element.header.guid);
	}
	return err;
}

GSErrCode CreateText (const GS::UniString& content, const API_Coord& location, double sizeMm,
					  bool alignRight, bool bold, short story)
{
	API_Element element = {};
	element.header.type = API_TextID;
	GSErrCode err = ACAPI_Element_GetDefaults (&element, nullptr);
	if (err != NoError) {
		return err;
	}
	element.header.floorInd = story;
	element.text.loc = location;
	element.text.size = sizeMm;
	element.text.angle = 0.0;
	element.text.anchor = alignRight ? APIAnc_RB : APIAnc_LB;
	element.text.just = alignRight ? APIJust_Right : APIJust_Left;
	element.text.faceBits = bold ? static_cast<unsigned short> (APIFace_Bold) : static_cast<unsigned short> (0);
	element.text.nonBreaking = true;
	element.text.useEolPos = true;
	element.text.width = 0.0;
	element.text.height = 0.0;
	element.text.nLine = 1;

	// Contenu + un paragraphe et un "run" explicites (comme Tapir) : sans eux,
	// Archicad 28 lit mal les caracteres non ASCII (m² affiche "mÂ²").
	const Int32 length = static_cast<Int32> (content.GetLength ());
	API_ElementMemo memo = {};
	memo.textContent = new GS::UniString (content);
	memo.paragraphs = reinterpret_cast<API_ParagraphType**> (BMhAllClear (sizeof (API_ParagraphType)));
	if (memo.paragraphs == nullptr) {
		ACAPI_DisposeElemMemoHdls (&memo);
		return APIERR_MEMFULL;
	}
	API_ParagraphType& paragraph = (*memo.paragraphs)[0];
	paragraph.from = 0;
	paragraph.range = length;
	paragraph.just = element.text.just;
	paragraph.spacing = -1.0;
	paragraph.tab = reinterpret_cast<API_TabType*> (BMpAllClear (sizeof (API_TabType)));
	paragraph.run = reinterpret_cast<API_RunType*> (BMpAllClear (sizeof (API_RunType)));
	paragraph.eolPos = reinterpret_cast<Int32*> (BMpAllClear (sizeof (Int32)));
	if (paragraph.tab == nullptr || paragraph.run == nullptr || paragraph.eolPos == nullptr) {
		ACAPI_DisposeElemMemoHdls (&memo);
		return APIERR_MEMFULL;
	}
	paragraph.run[0].from = 0;
	paragraph.run[0].range = length;
	paragraph.run[0].pen = element.text.pen;
	paragraph.run[0].faceBits = element.text.faceBits;
	paragraph.run[0].font = element.text.font;
	paragraph.run[0].effectBits = static_cast<unsigned short> (element.text.effectsBits);
	paragraph.run[0].size = element.text.size;
	paragraph.eolPos[0] = length - 1;

	err = ACAPI_Element_Create (&element, &memo);
	ACAPI_DisposeElemMemoHdls (&memo);
	if (err == NoError) {
		TagElement (element.header.guid);
	}
	return err;
}

// ------------------------------------------------------------ lecture plan ---

bool FindSolidFill (API_AttributeIndex& solidFill)
{
	bool found = false;
	ACAPI_Attribute_EnumerateAttributesByType (API_FilltypeID, [&] (API_Attribute& attribute) {
		if (!found && attribute.filltype.subType == APIFill_Solid) {
			solidFill = attribute.header.index;
			found = true;
		}
	});
	return found;
}

API_RGBColor GetZoneColor (const API_Element& zone)
{
	API_RGBColor color = {};
	color.f_red = 0.8;
	color.f_green = 0.8;
	color.f_blue = 0.8;

	API_Attribute attribute = {};
	attribute.header.typeID = API_ZoneCatID;
	attribute.header.index = zone.zone.catInd;
	if (ACAPI_Attribute_Get (&attribute) == NoError) {
		color = attribute.zoneCat.rgb;
	}
	return color;
}

double GetZoneArea (const API_Guid& guid)
{
	API_ElementQuantity quantity = {};
	API_Quantities quantities;
	quantities.elements = &quantity;

	API_QuantitiesMask mask;
	ACAPI_ELEMENT_QUANTITY_MASK_CLEAR (mask);
	ACAPI_ELEMENT_QUANTITY_MASK_SET (mask, zone, area);

	API_QuantityPar params = {};
	params.minOpeningSize = 1.0e-5;

	if (ACAPI_Element_GetQuantities (guid, &params, &quantities, &mask) != NoError) {
		return 0.0;
	}
	return quantity.zone.area;
}

void AddBoundsOfType (API_ElemTypeID typeID, PlanBounds& bounds)
{
	GS::Array<API_Guid> guids;
	if (ACAPI_Element_GetElemList (API_ElemType (typeID), &guids, APIFilt_OnActFloor) != NoError) {
		return;
	}
	for (const API_Guid& guid : guids) {
		API_Elem_Head head = {};
		head.guid = guid;
		if (ACAPI_Element_GetHeader (&head) != NoError) {
			continue;
		}
		API_Box3D box = {};
		if (ACAPI_Element_CalcBounds (&head, &box) == NoError) {
			bounds.Add (box);
		}
	}
}

}	// namespace


// =============================================================================

ZoneAreaTableResult CreateZoneAreaTable ()
{
	ZoneAreaTableResult result;

	GS::UniString storyName;
	const short story = GetCurrentStory (storyName);

	// 1. Lire les zones de l'etage courant.
	GS::Array<API_Guid> zoneGuids;
	ACAPI_Element_GetElemList (API_ElemType (API_ZoneID), &zoneGuids, APIFilt_OnActFloor);

	std::vector<ZoneRow> rows;
	for (const API_Guid& guid : zoneGuids) {
		API_Element zone = {};
		zone.header.guid = guid;
		if (ACAPI_Element_Get (&zone) != NoError) {
			continue;
		}
		ZoneRow row;
		row.number = GS::UniString (zone.zone.roomNoStr);
		row.name = GS::UniString (zone.zone.roomName);
		row.area = GetZoneArea (guid);
		row.color = GetZoneColor (zone);
		rows.push_back (row);
	}

	if (rows.empty ()) {
		result.message = Utf16 (u"Aucune zone sur l'étage courant : rien à mettre dans le tableau.");
		return result;
	}

	std::sort (rows.begin (), rows.end (), [] (const ZoneRow& a, const ZoneRow& b) {
		const std::string numberA = ToUtf8 (a.number);
		const std::string numberB = ToUtf8 (b.number);
		if (numberA != numberB) {
			return numberA < numberB;
		}
		return ToUtf8 (a.name) < ToUtf8 (b.name);
	});

	MakeColorsUnique (rows);

	API_AttributeIndex solidFill = APIInvalidAttributeIndex;
	if (!FindSolidFill (solidFill)) {
		result.message = Utf16 (u"Aucun motif de remplissage plein trouvé dans le projet.");
		return result;
	}

	// 2. Position : a droite du plan (zones et murs de l'etage).
	PlanBounds bounds;
	AddBoundsOfType (API_ZoneID, bounds);
	AddBoundsOfType (API_WallID, bounds);
	if (!bounds.valid) {
		result.message = Utf16 (u"Impossible de calculer l'emprise du plan.");
		return result;
	}

	double scale = 100.0;
	if (ACAPI_Drawing_GetDrawingScale (&scale) != NoError || scale <= 0.0) {
		scale = 100.0;
	}
	const double mm = scale / 1000.0;		// 1 mm papier en metres du modele

	const double x0 = bounds.xMax + GapFromPlanMm * mm;
	const double tableTop = bounds.yMax - TitleSpaceMm * mm;
	const double rowH = RowHeightMm * mm;
	const double textOffset = (RowHeightMm - TextSizeMm) / 2.0 * mm;
	const double width = TableWidthMm * mm;
	const Int32 rowCount = static_cast<Int32> (rows.size ());
	const double tableBottom = tableTop - rowH * (rowCount + 2);	// en-tete + zones + total

	double totalArea = 0.0;
	for (const ZoneRow& row : rows) {
		totalArea += row.area;
	}

	// 3. Dessiner, en une seule operation annulable (Ctrl+Z).
	const GSErrCode err = ACAPI_CallUndoableCommand (Utf16 (u"Tableau des surfaces"), [&] () -> GSErrCode {
		DeletePreviousTable ();

		GSErrCode e = NoError;
		auto check = [&e] (GSErrCode value) {
			if (e == NoError && value != NoError) {
				e = value;
			}
		};

		// Titre
		GS::UniString title = Utf16 (u"Tableau des surfaces");
		if (!storyName.IsEmpty ()) {
			title += GS::UniString (" - ") + storyName;
		}
		check (CreateText (title, API_Coord { x0, tableTop + 2.5 * mm }, TitleSizeMm, false, true, story));

		// En-tete
		const double headerBottom = tableTop - rowH;
		check (CreateText (GS::UniString ("Zone"), API_Coord { x0 + NameLeftMm * mm, headerBottom + textOffset }, TextSizeMm, false, true, story));
		check (CreateText (GS::UniString ("Surface"), API_Coord { x0 + AreaRightMm * mm, headerBottom + textOffset }, TextSizeMm, true, true, story));
		check (CreateLine (API_Coord { x0, headerBottom }, API_Coord { x0 + width, headerBottom }, story));

		// Une ligne par zone
		for (Int32 i = 0; i < rowCount; ++i) {
			const ZoneRow& row = rows[static_cast<size_t> (i)];
			const double rowBottom = headerBottom - rowH * (i + 1);

			check (CreateColorRectangle (x0 + SwatchLeftMm * mm, rowBottom + SwatchInsetMm * mm,
										 SwatchWidthMm * mm, (RowHeightMm - 2.0 * SwatchInsetMm) * mm,
										 row.color, solidFill, story));

			GS::UniString label = row.name;
			if (!row.number.IsEmpty ()) {
				label = row.number + GS::UniString (" ") + row.name;
			}
			check (CreateText (label, API_Coord { x0 + NameLeftMm * mm, rowBottom + textOffset }, TextSizeMm, false, false, story));
			check (CreateText (FormatArea (row.area), API_Coord { x0 + AreaRightMm * mm, rowBottom + textOffset }, TextSizeMm, true, false, story));

			// Trait horizontal sous chaque zone (la derniere est fermee par le trait du total)
			if (i < rowCount - 1) {
				check (CreateLine (API_Coord { x0, rowBottom }, API_Coord { x0 + width, rowBottom }, story));
			}
		}

		// Total
		const double totalTop = tableBottom + rowH;
		check (CreateLine (API_Coord { x0, totalTop }, API_Coord { x0 + width, totalTop }, story));
		check (CreateText (GS::UniString ("Total"), API_Coord { x0 + NameLeftMm * mm, tableBottom + textOffset }, TextSizeMm, false, true, story));
		check (CreateText (FormatArea (totalArea), API_Coord { x0 + AreaRightMm * mm, tableBottom + textOffset }, TextSizeMm, true, true, story));

		// Trait vertical entre la colonne des zones et celle des surfaces
		const double separatorX = x0 + ColumnSeparatorMm * mm;
		check (CreateLine (API_Coord { separatorX, tableTop }, API_Coord { separatorX, tableBottom }, story));

		// Cadre
		check (CreateLine (API_Coord { x0, tableTop }, API_Coord { x0 + width, tableTop }, story));
		check (CreateLine (API_Coord { x0 + width, tableTop }, API_Coord { x0 + width, tableBottom }, story));
		check (CreateLine (API_Coord { x0 + width, tableBottom }, API_Coord { x0, tableBottom }, story));
		check (CreateLine (API_Coord { x0, tableBottom }, API_Coord { x0, tableTop }, story));

		return e;
	});

	result.zoneCount = rowCount;
	result.totalArea = totalArea;
	if (err != NoError) {
		result.message = Utf16 (u"Le tableau n'a pas pu être dessiné entièrement (code Archicad ") +
						 GS::UniString::Printf ("%d", static_cast<int> (err)) + GS::UniString (").");
		return result;
	}

	result.success = true;
	result.message = Utf16 (u"Tableau des surfaces créé : ") + GS::UniString::Printf ("%d", static_cast<int> (rowCount)) +
					 Utf16 (u" zone(s), total ") + FormatArea (totalArea) + GS::UniString (".");
	return result;
}
