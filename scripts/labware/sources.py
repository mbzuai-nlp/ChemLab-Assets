"""Sources for the labware and storage-furniture assets (assets/labware/).

Every entry: kind (contract source kind), reference (URL / document), locator (page, table row, catalogue number), note
(which values were taken), licence.  Vendor pages behind bot protection (dwk.com, sigmaaldrich, fishersci, analytics-shop ...)
returned HTTP 403 to direct download on 2026-10-22; for those the values were read from the vendor / distributor listing as
indexed by a web search on that date, and the note says so.  Dimensions are facts; no manual or catalogue text is copied.
"""

ACCESS = "accessed 2026-10-22"

SOURCES = {
    # ---------------------------------------------------------------- bottles, jars, vials, tubes
    "duran_gl45_clear": {
        "kind": "manufacturer",
        "reference": "DWK Life Sciences, DURAN Original GL 45 laboratory bottle, clear (e.g. https://www.dwk.com/na/duran-original-gl-45-laboratory-bottle-clear-with-screw-cap-and-pouring-ring-pp-blue-1000-ml-218015455); dimension table also listed by Parkes Scientific (https://parkesscientific.com/p/duran-21801-original-laboratory-bottles-with-high-temperature-closures-red)",
        "locator": "cat. 21801 24 5x (100 mL), 21801 36 5x (250 mL), 21801 44 5x (500 mL), 21801 54 5x (1000 mL), with screw cap + pouring ring PP blue; table columns capacity / thread / OD / height",
        "note": "OD x height with cap and pouring ring: 100 mL 56 x 105 mm, 250 mL 70 x 143 mm, 500 mL 86 x 181 mm, 1000 mL 101 x 230 mm; the bottle-only versions are 5 mm lower (100/138/176/225 mm). Read from the vendor/distributor tables via web search, " + ACCESS + " (dwk.com 403 to direct download).",
        "licence": "facts (catalogue dimensions)"},
    "duran_gl45_amber": {
        "kind": "manufacturer",
        "reference": "DWK Life Sciences, DURAN GL 45 laboratory bottle, amber (e.g. Sigma-Aldrich DWK218062405, https://www.sigmaaldrich.com/US/en/product/sigma/dwk218062405)",
        "locator": "cat. 21806 xx (amber series, same capacities as 21801)",
        "note": "Amber series uses the same bottle geometry as the clear Original series (same capacity / thread / OD / height columns); colour from the amber glass. " + ACCESS + ".",
        "licence": "facts"},
    "gl45_thread": {
        "kind": "publication",
        "reference": "DIN 168-1 (knuckle threads for glass containers); GL 45 values as quoted from 'Guide to World Screw Threads' in https://www.practicalmachinist.com/forum/threads/gl-45-threads.264357/",
        "locator": "GL 45: neck major diameter 45.00 mm, pitch 4 mm, thread depth 1.36 mm",
        "note": "DIN 168-1 is paywalled; the secondary quotation is used for the neck major diameter (45 mm) and pitch (4 mm). " + ACCESS + ".",
        "licence": "facts"},
    "duran_gl45_cap": {
        "kind": "manufacturer",
        "reference": "DWK DURAN screw cap PP blue GL 45 (type K, DWK 293032803), distributor listing https://www.labfriend.com.au/dwk-duran-screw-caps-pp-blue-gl45-k",
        "locator": "dimensions (O x H) 58 x 27 mm",
        "note": "Blue PP GL 45 screw cap: 58 mm outside diameter, 27 mm high. " + ACCESS + ".",
        "licence": "facts"},
    "iso3585": {
        "kind": "publication",
        "reference": "ISO 3585:1998 Borosilicate glass 3.3 - Properties (DURAN is borosilicate glass 3.3)",
        "locator": "Table 1: density 2.23 g/cm3",
        "note": "Density of borosilicate glass 3.3 = 2230 kg/m3, used to compute empty glass masses from the modelled wall volume.",
        "licence": "facts"},
    "winchester_25l": {
        "kind": "manufacturer",
        "reference": "Philip Harris / Findel 'Winchester Bottle - Amber Glass - 2.5L', product FE00001092 (https://www.philipharris.co.uk/product/fe00001092)",
        "locator": "product dimensions: 297 mm height x 132.9 mm width",
        "note": "2.5 L amber glass Winchester solvent bottle; listing gives height 297 mm and width (= body diameter) 132.9 mm; narrow screw mouth with black plastic lined cap. " + ACCESS + ".",
        "licence": "facts"},
    "nalgene_wm_250": {
        "kind": "manufacturer",
        "reference": "Thermo Scientific Nalgene wide-mouth HDPE packaging bottle 250 mL, cat. 312189-0008 (listing https://www.calpaclab.com/nalgene-312189-0008-8-ounce-250ml-wide-mouth-hdpe-bottles-nalgene/ng-312189-0008)",
        "locator": "OD 61 mm, height 131 mm, neck ID 33 mm, closure 43-415",
        "note": "Wide-mouth HDPE bottle with PP screw closure used as a powder jar. " + ACCESS + ".",
        "licence": "facts"},
    "nalgene_wm_1000": {
        "kind": "manufacturer",
        "reference": "Thermo Scientific Nalgene wide-mouth HDPE packaging bottle 1000 mL, cat. 312189-0032 (https://www.thermofisher.com/order/catalog/product/312189-0032)",
        "locator": "diameter 91 mm, height 199 mm, closure 63 mm",
        "note": "Wide-mouth HDPE 1 L bottle with 63 mm PP closure used as a large powder jar. " + ACCESS + ".",
        "licence": "facts"},
    "nalgene_nm_500": {
        "kind": "manufacturer",
        "reference": "Thermo Scientific Nalgene narrow-mouth HDPE lab quality bottle with closure 500 mL, cat. 2002-0016 (https://www.thermofisher.com/order/catalog/product/2002-0016)",
        "locator": "diameter 73 mm, height 170 mm, inner neck 21 mm, 28 mm closure (PP)",
        "note": "Narrow-mouth HDPE reagent bottle. " + ACCESS + ".",
        "licence": "facts"},
    "nalgene_nm_1000": {
        "kind": "manufacturer",
        "reference": "Thermo Scientific Nalgene narrow-mouth natural HDPE packaging bottle with closure 1000 mL, cat. 312089-0032 (https://www.thermofisher.com/order/catalog/product/mx/en/312089-0032)",
        "locator": "diameter 92 mm, height 217 mm, inner neck 27 mm, 38-430 closure",
        "note": "Narrow-mouth HDPE 1 L reagent bottle. " + ACCESS + ".",
        "licence": "facts"},
    "nalgene_wm_500": {
        "kind": "manufacturer",
        "reference": "Thermo Scientific Nalgene wide-mouth HDPE packaging bottle 500 mL, cat. 312189-0016 (https://www.thermofisher.com/order/catalog/product/312189-0016); economy version 2189-0016",
        "locator": "diameter 73 mm, height 168 mm, inner neck 44 mm, 53-415 closure",
        "note": "Wide-mouth HDPE jar for ~500 g of a solid reagent. " + ACCESS + ".",
        "licence": "facts"},
    "rama_10l_cylinder": {
        "kind": "manufacturer",
        "reference": "PWENT / Rama Cylinders steel gas cylinder 10 L, 200 bar, ISO 9809-1 (https://pwent.eu/en/product_tag/10-liter/)",
        "locator": "10 L / 200 bar / 140 mm diameter / 850 mm length / 14.9 kg",
        "note": "Small lab gas cylinder. " + ACCESS + ".",
        "licence": "facts"},
    "agilent_5182_0715": {
        "kind": "manufacturer",
        "reference": "Agilent 5182-0715 screw top vial, 2 mL, clear (https://www.agilent.com/store/en/Prod-5182-0715/5182-0715)",
        "locator": "vial size 12 x 32 mm, 12 mm cap (9 mm thread)",
        "note": "Standard 2 mL autosampler screw vial. " + ACCESS + ".",
        "licence": "facts"},
    "agilent_5188_2753": {
        "kind": "manufacturer",
        "reference": "Agilent 5188-2753 headspace vial, screw top, 20 mL (https://www.agilent.com/store/es_ES/Prod-5188-2753/5188-2753); also Crawford Scientific listing",
        "locator": "vial size 22.75 x 75 mm, 18 mm screw cap, round bottom",
        "note": "20 mL screw-top headspace vial (fits the HS 8697 headspace sampler). " + ACCESS + ".",
        "licence": "facts"},
    "falcon_352096": {
        "kind": "manufacturer",
        "reference": "Corning Falcon 352096 15 mL PP conical centrifuge tube (Corning page for the racked version 352097: https://ecatalog.corning.com/life-sciences/b2b/SE/en/p/352097)",
        "locator": "approx. 17 mm O.D. x 120 mm length",
        "note": "15 mL conical tube with PE flat-top screw cap. " + ACCESS + ".",
        "licence": "facts"},
    "falcon_352070": {
        "kind": "manufacturer",
        "reference": "Corning Falcon 352070 50 mL PP conical centrifuge tube (listing https://www.capitolscientific.com/Falcon-a-Corning-Brand-352070-Falcon-a-Corning-Brand-352070-High-Clarity-50mL-Polypropylene-Co)",
        "locator": "approx. 30 mm O.D. x 115 mm length",
        "note": "50 mL conical tube with screw cap. " + ACCESS + ".",
        "licence": "facts"},
    "eppendorf_safelock_15": {
        "kind": "manufacturer",
        "reference": "Eppendorf Tubes Snap Cap - Technical Data Sheet (document 0012 595 671-00/2026-03), https://www.eppendorf.com/product-media/doc/en/17238493/Consumables_Technical-data_Eppendorf-Tubes-snap-caps.pdf",
        "locator": "p. 6 'Safe-Lock Tubes 1.5 mL' drawing: length 38.9 mm (37.8 inner), OD 10.8 mm (top 20 mm), cone OD 10.7 -> tip, rim 2 mm, lid span 37.2 mm, closed approx. 41 mm high / 20 mm wide, lid opens to 90 deg (approx. 59 mm high)",
        "note": "Safe-Lock 1.5 mL tube (cat. 0030120086, PP); values read from the data-sheet drawing.",
        "licence": "facts (drawing dimensions only; no figure copied)"},
    "pp_hdpe_density": {
        "kind": "assumption",
        "reference": "typical resin densities: PP 900 kg/m3, HDPE 950 kg/m3, PS 1050 kg/m3, PE 950 kg/m3",
        "locator": "engineering value",
        "note": "Used only to compute the mass of plastic parts from their modelled volume; the grade-to-grade spread is about +/-2 %.",
        "licence": "n/a"},
    "wall_assumption": {
        "kind": "assumption",
        "reference": "wall and bottom thicknesses of containers / caps",
        "locator": "engineering value",
        "note": "Manufacturers do not publish wall thicknesses; glass bottles 2.5 mm wall / 4 mm bottom, plastic tubes 0.8-1.0 mm, caps 2-3 mm, chosen from typical pressed-glass and moulded-PP practice. They set the empty mass and the interior volume (the computed brim volume is checked against the nominal capacity).",
        "licence": "n/a"},
    "neck_profile_estimate": {
        "kind": "photo",
        "reference": "product photographs of the same catalogue items (vendor pages)",
        "locator": "shoulder / neck / collar proportions",
        "note": "Shoulder height, collar and thread-zone heights are estimated from product photos and scaled to the documented OD / height; the documented values (OD, height, thread) are kept exactly.",
        "licence": "n/a"},
    "closure_turns": {
        "kind": "assumption",
        "reference": "screw-cap release angle",
        "locator": "engineering value",
        "note": "Turns until a screw cap is free: GL 45 1.25 turns (pitch 4 mm, ~5 mm engagement), SPI 400-type finishes 1 turn, 415-type 2 turns, vial and tube caps 1-1.5 turns. No manufacturer publishes these; the detach rule only needs a threshold.",
        "licence": "n/a"},
    # ---------------------------------------------------------------- SBS / Opentrons
    "ansi_slas_1_2004": {
        "kind": "publication",
        "reference": "ANSI/SLAS 1-2004 Microplates - Footprint Dimensions (and ANSI/SLAS 4-2004 Well Positions)",
        "locator": "outside footprint 127.76 x 85.48 mm; well A1 at 14.38 / 11.24 mm, 9 mm pitch",
        "note": "Microplate footprint used for the plates, tip racks, reservoir and the plate carrier pocket.",
        "licence": "facts"},
    "ot_corning_96_flat": {
        "kind": "code",
        "reference": "Opentrons labware definition corning_96_wellplate_360ul_flat v1 (shared-data/labware/definitions/2/corning_96_wellplate_360ul_flat/1.json, Apache-2.0); local copy references/labware/opentrons/",
        "locator": "dimensions 127.76 x 85.47 x 14.22 mm; wells circular d 6.86 mm, depth 10.67 mm, bottom z 3.55 mm, 360 uL; A1 x 14.38 y 74.24; brandId Corning 3650/3590/...",
        "note": "Corning 96-well flat-bottom plate (e.g. Corning 3590), as used by the default Flex deck in chemlab.",
        "licence": "Apache-2.0 (Opentrons)"},
    "ot_nest_96_deep": {
        "kind": "code",
        "reference": "Opentrons labware definition nest_96_wellplate_2ml_deep v1 (Apache-2.0); local copy references/labware/opentrons/",
        "locator": "127.6 x 85.3 x 41 mm; square wells 8.2 x 8.2 mm, depth 38 mm, bottom z 3 mm, 2000 uL; NEST 503001/503501",
        "note": "NEST 96 deep-well plate 2 mL.",
        "licence": "Apache-2.0 (Opentrons)"},
    "ot_flex_tiprack_50": {
        "kind": "code",
        "reference": "Opentrons labware definition opentrons_flex_96_tiprack_50ul v1 (Apache-2.0); local copy references/labware/opentrons/",
        "locator": "127.75 x 85.75 x 99 mm; tip length 57.9 mm, overlap 10.5 mm; wells d 5.58 mm from z 1.5 mm; gripHeightFromLabwareBottom 23.9 mm, gripForce 16 N",
        "note": "Opentrons Flex 96 tip rack 50 uL (tips included).",
        "licence": "Apache-2.0 (Opentrons)"},
    "ot_flex_tiprack_200": {
        "kind": "code",
        "reference": "Opentrons labware definition opentrons_flex_96_tiprack_200ul v1 (Apache-2.0); local copy references/labware/opentrons/",
        "locator": "127.75 x 85.75 x 99 mm; tip length 58.35 mm; wells d 5.59 mm; grip height 23.9 mm",
        "note": "Opentrons Flex 96 tip rack 200 uL (tips included).",
        "licence": "Apache-2.0 (Opentrons)"},
    "ot_flex_tiprack_1000": {
        "kind": "code",
        "reference": "Opentrons labware definition opentrons_flex_96_tiprack_1000ul v1 (Apache-2.0); local copy references/labware/opentrons/",
        "locator": "127.75 x 85.75 x 99 mm; tip length 95.6 mm; wells d 5.47 mm; grip height 23.9 mm",
        "note": "Opentrons Flex 96 tip rack 1000 uL (tips included).",
        "licence": "Apache-2.0 (Opentrons)"},
    "ot_nest_12_reservoir": {
        "kind": "code",
        "reference": "Opentrons labware definition nest_12_reservoir_15ml v1 (Apache-2.0); local copy references/labware/opentrons/",
        "locator": "127.76 x 85.48 x 31.4 mm; 12 troughs 8.2 x 71.2 mm, depth 26.85 mm, bottom z 4.55 mm, 15 mL; NEST 360102",
        "note": "NEST 12-well reservoir 15 mL, as used by the default Flex deck in chemlab.",
        "licence": "Apache-2.0 (Opentrons)"},
    "thermo_75006449": {
        "kind": "manufacturer",
        "reference": "Thermo Scientific microtitre (microplate) carrier 75006449 with metal handle (listing https://www.djblabcare.co.uk/djb/product/203/Accessories-75006449-Microtitre_carrier_for_4_microtitre_plates)",
        "locator": "carrier for microplates with a carrying handle (centrifuge accessory)",
        "note": "Closest real product found to a handled SBS carrier; no catalogue carrier with a robot handle <= 74 mm was found, so the plate carrier is a design on the ANSI/SLAS footprint (see limitations).",
        "licence": "facts"},
    "plate_ps_mass": {
        "kind": "assumption",
        "reference": "mass of SBS polystyrene / polypropylene labware",
        "locator": "engineering value",
        "note": "Empty masses of plates, tip racks and the reservoir are computed from the modelled solid volume x resin density; no vendor publishes them.",
        "licence": "n/a"},
    # ---------------------------------------------------------------- tools
    "globe_3620": {
        "kind": "manufacturer",
        "reference": "Globe Scientific 3620 antistatic square polystyrene weighing dish (listing https://www.calpaclab.com/weighing-boat-plastic-square-antistatic-41x41x8mm-polystyrene-white-20ml/gsci-3620); same size as Heathrow HS1421A / Sigma Z708542",
        "locator": "41 x 41 x 8 mm, 20 mL, PS, antistatic",
        "note": "Small square weighing boat. " + ACCESS + ".",
        "licence": "facts"},
    "bochem_3340": {
        "kind": "manufacturer",
        "reference": "Bochem 3340 micro spoon spatula, 18/10 stainless steel, 150 mm (Fisher UK listing https://fishersci.co.uk/shop/products/18-10-stainless-steel-micro-spoon-spatulas-6/p-8000566)",
        "locator": "length 150 mm, spoon 9 mm, blade 40 mm (blade width 5 mm)",
        "note": "Double-ended micro spoon spatula for powders. " + ACCESS + ".",
        "licence": "facts"},
    # ---------------------------------------------------------------- gas
    "rama_50l_cylinder": {
        "kind": "manufacturer",
        "reference": "PWENT / Rama Cylinders steel seamless gas cylinder 50 L, 200 bar, ISO 9809-1 (https://pwent.eu/en/products/products/gas-cylinder-products-high-pressure-low-pressure/steel-50-liter-gas-cylinder-seaproof-200-bar-european-producer/); cross-check Unique Group 50 L 229 x 1460 mm",
        "locator": "50 L / 200 bar working pressure / 229 mm diameter / 1500 mm length / 54 kg",
        "note": "Typical European 50 L industrial / lab gas cylinder; the listing does not say whether 54 kg is tare - used as the empty mass. " + ACCESS + ".",
        "licence": "facts"},
    "linde_c200_2": {
        "kind": "manufacturer",
        "reference": "Linde REDLINE C 200/2 two-stage cylinder pressure regulator, data sheet (https://static.prd.echannel.linde.com/wcsstore/DK_REN_Industrial_Gas_Store/pdf/Linde%20REN%20data%20sheet%20REDLINE%20C%20200-2%20v1_tcm575-547262.pdf)",
        "locator": "p.1 article table (e.g. Art. 4461 chrome-plated brass 1-10 bar); p.3 technical data (max primary 230 bar, DIN 477 inlet, 6 mm compression outlet, weight 3.33 kg max.); p.4 drawing approx. 178 x 150 mm",
        "note": "Regulator mounted on the 50 L cylinder.",
        "licence": "facts"},
    # ---------------------------------------------------------------- furniture
    "asecos_q90_195_120": {
        "kind": "manufacturer",
        "reference": "asecos safety storage cabinet Q-CLASSIC-90, model Q90.195.120, product data sheet ID 34192-041 (https://asecos-configurator.com/EN/DE/asecos/download/DAT_EXT_34192041-product-data-sheet_IN.pdf); 3-shelf version listed by LabFriend (https://www.labfriend.com.my/asecos-q-classic-90-safety-storage-cabinet-1200x615x1955mm-3-shelves)",
        "locator": "data sheet p.1: external 1193 x 615 x 1953 mm, internal 1050 x 522 x 1647 mm, 460 kg, 2 wing doors, opening angle 89.5 deg, depth with open doors 1165 mm, interior grid 32 mm, transport base 90 mm, RAL 7016 body / RAL 7035 doors; p.2 drawing (door leaves); listing: 3 height-adjustable shelves (75 kg each) + bottom collecting sump",
        "note": "EN 14470-1 type 90 cabinet for flammable liquids.",
        "licence": "facts"},
    "asecos_sl_196_120": {
        "kind": "manufacturer",
        "reference": "asecos cabinet for acids and alkalis SL-CLASSIC, model SL.196.120.MV, product data sheet ID 30606-005 (https://www.asecos-configurator.com/EN/IT/download/DAT_EXT_30606005-product-data-sheet_IN.pdf); pull-out trays per Fisher Scientific listing 15231035",
        "locator": "p.1: external 1197 x 603 x 1965 mm, internal (per compartment) 485 x 570 x 1869 mm, 215 kg, 2 doors, depth with open doors 1153 mm, two hermetically separated compartments (acids / bases), laboratory white",
        "note": "Non-flammable acids and bases in separate compartments.",
        "licence": "facts"},
    "asecos_cs_195_105": {
        "kind": "manufacturer",
        "reference": "asecos chemical cabinet CS-CLASSIC, model CS.195.105 (Fisher Scientific listing 15995516, https://www.fishersci.pt/shop/products/cabinet-chemicals-cs-classic-right-hinged-14/15995516; dicsa listing CS.195.105)",
        "locator": "external 1055 x 520 x 1950 mm; internal width 996 mm, internal height 1835 mm; lockable; for non-flammable chemicals and toxic substances",
        "note": "Used for the ventilated, lockable toxics cabinet (variant of the corrosive cabinet family). Internal depth not listed: assumed 470 mm (external 520 minus walls). " + ACCESS + ".",
        "licence": "facts"},
    "liebherr_lkv3910": {
        "kind": "manufacturer",
        "reference": "Liebherr laboratory refrigerator LKv 3910 MediLine (Reagecon listing https://www.reagecon.com/zh-cn/liebherr-laboratory-refrigerator-lkv-3910-3-16-c-9698683; Remma https://remma.fr/en/model/laboratory-refrigerator-mediline-lkv-3910)",
        "locator": "external 597 x 615 x 1840 mm, internal 440 x 435 x 1635 mm, 360 L gross, +3...+16 C, 5 height-adjustable shelves (45 kg each), spark-free interior",
        "note": "Lab refrigerator for the 2-8 C store. " + ACCESS + ".",
        "licence": "facts"},
    "duran_desiccator_250": {
        "kind": "manufacturer",
        "reference": "DWK DURAN desiccator with knobbed lid, DN 250, cat. 247816604 = bottom 247706605 + lid 244106608 (https://www.dwk.com/duran-desiccator-with-knobbed-lid; faust.ch article 247816604); porcelain plate DN 250 DU297256609",
        "locator": "DN 250: flange inner 274 mm, flange outer 320 +/- 2 mm, height (complete) 357 mm; knob lid height 122 mm",
        "note": "Glass desiccator for hygroscopic solids and check standards. Plate diameter not listed: 235 mm assumed (fits the DN 250 bottom). " + ACCESS + ".",
        "licence": "facts"},
    "denios_wh230_s2": {
        "kind": "manufacturer",
        "reference": "DENIOS gas cylinder wall bracket in steel, model WH 230-S2, item 115878W (https://denios.co.uk/steel-gas-cylinder-mounting-support-model-wh-230-s2-2-cylinders-115878/Gas Cylinder Handling)",
        "locator": "for 2 cylinders max. O 230 mm, external 560 x 175 x 60 mm, 3 kg, galvanised, securing chain",
        "note": "Four brackets side by side hold the 8 cylinder positions of the default lab. " + ACCESS + ".",
        "licence": "facts"},
    "trippnt_step_shelf": {
        "kind": "manufacturer",
        "reference": "TrippNT bottomless PVC step shelf 24 in, Weber Scientific TNT24W10-8 (https://weberscientific.com/step-shelves-workstation), Avantor 11376-388 / supplier no. 50123 (https://avantorsciences.com/ca/en/product/10053831/step-shelf-pvc-trippnt)",
        "locator": "24 in (610 mm) wide, 10 in (254 mm) high, 9 in (229 mm) deep; 1 in (25 mm) safety lip; chemically resistant PVC; for reagent bottles",
        "note": "Closest real bench step shelf. Our asset adds a matching low front tray so both tiers belong to one asset (the product is a single bottomless step standing on the bench); see limitations. " + ACCESS + ".",
        "licence": "facts"},
    "asecos_g90_205_140": {
        "kind": "manufacturer",
        "reference": "asecos gas cylinder cabinet G-ULTIMATE-90, model G90.205.140 (ID 30641-001-30642), listings Fisher Scientific 15185564 (https://fishersci.dk/shop/products/g-ultimate-90-gas-cylinders-storage-cabinet/15185564) and LabFriend (https://www.labfriend.com.my/asecos-gas-cylinder-cabinet-g-ultimate-90-wd)",
        "locator": "external 1398 x 615 x 2050 mm, internal 1245 x 400 x 1858 mm, 4 x 50 L or 8 x 10 L cylinders, 707.5 kg (another listing 690 kg), EN 14470-2 type G90, two wing doors",
        "note": "Ventilated fire-resistant cabinet for flammable gas cylinders. " + ACCESS + ".",
        "licence": "facts"},
    "liebherr_lguex1500": {
        "kind": "manufacturer",
        "reference": "Liebherr MediLine LGUex 1500 laboratory freezer (listings https://ssllc.com/product/Liebherr-mediline-A135965, https://sydneycommercialkitchens.com.au/catering-equipment/freezers-medical/Liebherr)",
        "locator": "external 600 x 615 x 820 mm, 129 L, 3 drawers + basket, down to -26 C, explosion-protected interior",
        "note": "Under-bench lab freezer for the -20 C store. " + ACCESS + ".",
        "licence": "facts"},
    "metro_super_erecta": {
        "kind": "manufacturer",
        "reference": "Metro Super Erecta chrome wire shelving: shelf 1848NC (48 x 18 in) and posts 74P (74 in); listing https://www.katom.com/index.php/001-1848NC.html",
        "locator": "shelf 48 in L x 18 in W (1219 x 457 mm), post 74 in (1880 mm), 4-shelf unit",
        "note": "Open room reagent rack (out of the fixed arm's reach). " + ACCESS + ".",
        "licence": "facts"},
    "inventory_places": {
        "kind": "code",
        "reference": "Chemistry-Sim chemlab/chemlab/inventory.py PLACES (the default-lab inventory, 2026-10-22)",
        "locator": "place ids and position names (S<shelf>-<nn>, T<tier>-<nn>, P1-<nn>, C<n>)",
        "note": "Slot counts and position names follow the default-lab inventory's storage-place list.",
        "licence": "project"},
    "robot_access": {
        "kind": "code",
        "reference": "robot-access constraints of the Franka arm set-up for the storage tasks, 2026-10-22",
        "locator": "Franka gripper <= 80 mm open (grasp zones <= 74 mm), hand collision 37 mm above TCP, top-down access, bench-shelf pitch >= 11 cm and >= 25 cm free above bottle tops",
        "note": "Used to place grasp frames and the bench-shelf slot layout.",
        "licence": "project"},
}


def pick(*ids: str) -> dict:
    return {i: SOURCES[i] for i in ids}
