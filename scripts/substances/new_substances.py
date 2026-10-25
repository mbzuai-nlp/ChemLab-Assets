"""Substances the default-lab inventory needs that were not in substances.json (chemlab default-lab inventory,
2026-10-22).  Pure substances get PubChem-sourced molar mass / formula / density / phase (fetched by fetch_pubchem_safety.py --new
into references/substance_safety_raw/<id>/); mixtures (latex, bead standards, ladders, buffer concentrates) have no molar mass and
are marked kind = "mixture" with their composition from the product description.
"""

NEW_PURE = {
    # id: (CAS, name_zh, phase at 20-25 C, fid_visible, note)
    "argon": ("7440-37-1", "氩气", "gas", False, "carrier / purge gas (cylinder)"),
    "helium": ("7440-59-7", "氦气", "gas", False, "GC carrier gas (cylinder)"),
    "silicon": ("7440-21-3", "硅", "solid", False, "XRD line-position standard (NIST SRM 640-series powder)"),
    "aluminium_oxide": ("1344-28-1", "氧化铝（刚玉）", "solid", False, "XRD intensity standard (NIST SRM 676-series corundum)"),
    "indium": ("7440-74-6", "铟", "solid", False, "DSC temperature / enthalpy calibration metal"),
    "potassium_hydrogen_phthalate": ("877-24-7", "邻苯二甲酸氢钾", "solid", False, "primary acidimetric standard; pH 4.01 buffer"),
    "sodium_dihydrogen_phosphate": ("7558-80-7", "磷酸二氢钠", "solid", False, "phosphate buffer component (pH 7.00 buffers)"),
    # 2026-10-23: the pH 6.86 primary standard of DIN 19266 / NIST is KH2PO4 0.025 mol/kg + Na2HPO4 0.025 mol/kg
    "potassium_dihydrogen_phosphate": ("7778-77-0", "磷酸二氢钾", "solid", False,
                                       "pH 6.86 phosphate standard buffer component (DIN 19266 / NIST: 0.025 mol/kg with Na2HPO4)"),
    "disodium_hydrogen_phosphate": ("7558-79-4", "磷酸氢二钠", "solid", False,
                                    "pH 6.86 phosphate standard buffer component (DIN 19266 / NIST: 0.025 mol/kg with KH2PO4)"),
    "sodium_tetraborate_decahydrate": ("1303-96-4", "十水合四硼酸钠（硼砂）", "solid", False, "pH 9.18 buffer"),
    "lithium_chloride": ("7447-41-8", "氯化锂", "solid", False, "water-activity standard salt (saturated LiCl, aw 0.113)"),
    "formazin": ("9004-77-7", "福尔马肼（浊度标准聚合物）", "aqueous", False, "turbidity primary standard (4000 NTU suspension)"),
    "sulfanilic_acid": ("121-57-3", "对氨基苯磺酸", "solid", False, "nitrite (Griess) reagent component"),
    "ferrocene": ("102-54-5", "二茂铁", "solid", True, "electrochemistry internal reference (Fc+/Fc)"),
    # 2026-10-23 (inventory consistency: ids the default-lab inventory used from the spectral compound table, so
    # that substances.json stays the single vocabulary)
    "acetonitrile": ("75-05-8", "乙腈", "liquid", True, "HPLC mobile-phase solvent (stock bottle)"),
    "benzene": ("71-43-2", "苯", "liquid", True, "component of an instrument's default check sample"),
    "caffeine": ("58-08-2", "咖啡因", "solid", True, "HPLC check standard (100 mg/L in water)"),
    "decane": ("124-18-5", "正癸烷", "liquid", True, "GC n-alkane check mix component"),
    "hexane": ("110-54-3", "正己烷", "liquid", True, "solvent (stock bottle, GC dilutions)"),
    "oxalic_acid": ("144-62-7", "草酸", "solid", False, "silicate-channel reagent of the segmented-flow analyser"),
    "propan_2_ol": ("67-63-0", "异丙醇", "liquid", True, "solvent (stock bottle, cleaning)"),
    "tetrahydrofuran": ("109-99-9", "四氢呋喃", "liquid", True, "solvent of the solvent purification system"),
    # 2026-10-23: the C8-C20 n-alkanes of the GC retention-index standard (Supelco 04070, ~40 mg/L each in hexane);
    # ids as in the crosswalk / spectral library (IUPAC names; C20 = icosane, also called eicosane); phase at 20-25 C from the
    # melting points (C17 22 C, C18 28 C, C19 32 C, C20 37 C)
    "octane": ("111-65-9", "正辛烷", "liquid", True, "n-alkane retention-index standard component (C8)"),
    "nonane": ("111-84-2", "正壬烷", "liquid", True, "n-alkane retention-index standard component (C9)"),
    "undecane": ("1120-21-4", "正十一烷", "liquid", True, "n-alkane retention-index standard component (C11)"),
    "dodecane": ("112-40-3", "正十二烷", "liquid", True, "n-alkane retention-index standard component (C12)"),
    "tridecane": ("629-50-5", "正十三烷", "liquid", True, "n-alkane retention-index standard component (C13)"),
    "tetradecane": ("629-59-4", "正十四烷", "liquid", True, "n-alkane retention-index standard component (C14)"),
    "pentadecane": ("629-62-9", "正十五烷", "liquid", True, "n-alkane retention-index standard component (C15)"),
    "hexadecane": ("544-76-3", "正十六烷", "liquid", True, "n-alkane retention-index standard component (C16)"),
    "heptadecane": ("629-78-7", "正十七烷", "liquid", True, "n-alkane retention-index standard component (C17; mp about 22 C)"),
    "octadecane": ("593-45-3", "正十八烷", "solid", True, "n-alkane retention-index standard component (C18)"),
    "nonadecane": ("629-92-5", "正十九烷", "solid", True, "n-alkane retention-index standard component (C19)"),
    "icosane": ("112-95-8", "正二十烷", "solid", True, "n-alkane retention-index standard component (C20, eicosane)"),
}

NEW_MIXTURES = {
    "polystyrene_latex": {"name_zh": "聚苯乙烯乳胶标准粒子悬液", "formula": "(C8H8)n dispersion in water", "phase": "aqueous",
                          "composition": "monodisperse polystyrene spheres (e.g. 100 nm, 1 % w/v) in water with surfactant",
                          "density_kg_m3": 1050.0, "note": "particle-size check standard (DLS / laser diffraction); polymer density 1050 kg/m3 (polystyrene)"},
    "calibration_beads_fluorescent": {"name_zh": "荧光校准微球（流式细胞仪）", "formula": "polystyrene microspheres with dyes, aqueous", "phase": "aqueous",
                                      "composition": "CS&T-type fluorescent polystyrene beads in buffer with azide",
                                      "density_kg_m3": 1050.0, "note": "cytometer set-up standard; supplied cold (2-8 C)"},
    "protein_ladder_prestained": {"name_zh": "预染蛋白分子量标准", "formula": "protein mixture in loading buffer", "phase": "aqueous",
                                  "composition": "recombinant prestained proteins 10-250 kDa in Tris / glycerol loading buffer",
                                  "density_kg_m3": 1100.0, "note": "SDS-PAGE marker; supplied frozen (-20 C)"},
    "hbs_ep_plus_10x": {"name_zh": "HBS-EP+ 缓冲液（10×）", "formula": "HEPES / NaCl / EDTA / polysorbate 20 in water", "phase": "aqueous",
                        "composition": "0.1 M HEPES, 1.5 M NaCl, 30 mM EDTA, 0.5 % v/v surfactant P20, pH 7.4 (10x concentrate)",
                        "density_kg_m3": 1060.0, "note": "SPR running buffer concentrate (Biacore); store 2-8 C"},
}
