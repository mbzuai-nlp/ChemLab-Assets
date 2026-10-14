"""Compound list for the spectral template library.

Each row: (substance_id, English name, Chinese name, CAS, class).  CAS numbers are the lookup key for both the
NIST WebBook (C-number = CAS without dashes) and PubChem; ``fetch_sources.py`` cross-checks the molecular formula
returned by both services so a wrong CAS is caught before it enters the library.
"""

COMPOUNDS = [
    # alcohols / polyols
    ("methanol", "methanol", "甲醇", "67-56-1", "alcohol"),
    ("ethanol", "ethanol", "乙醇", "64-17-5", "alcohol"),
    ("propan_1_ol", "1-propanol", "正丙醇", "71-23-8", "alcohol"),
    ("propan_2_ol", "2-propanol", "异丙醇", "67-63-0", "alcohol"),
    ("butan_1_ol", "1-butanol", "正丁醇", "71-36-3", "alcohol"),
    ("butan_2_ol", "2-butanol", "2-丁醇", "78-92-2", "alcohol"),
    ("tert_butanol", "2-methyl-2-propanol", "叔丁醇", "75-65-0", "alcohol"),
    ("isobutanol", "2-methyl-1-propanol", "异丁醇", "78-83-1", "alcohol"),
    ("isoamyl_alcohol", "3-methyl-1-butanol", "异戊醇", "123-51-3", "alcohol"),
    ("pentan_1_ol", "1-pentanol", "正戊醇", "71-41-0", "alcohol"),
    ("hexan_1_ol", "1-hexanol", "正己醇", "111-27-3", "alcohol"),
    ("octan_1_ol", "1-octanol", "正辛醇", "111-87-5", "alcohol"),
    ("cyclohexanol", "cyclohexanol", "环己醇", "108-93-0", "alcohol"),
    ("benzyl_alcohol", "benzyl alcohol", "苯甲醇", "100-51-6", "alcohol"),
    ("ethylene_glycol", "ethylene glycol", "乙二醇", "107-21-1", "polyol"),
    ("glycerol", "glycerol", "甘油", "56-81-5", "polyol"),
    ("menthol", "menthol", "薄荷醇", "89-78-1", "terpenoid"),
    # acids / anhydrides
    ("formic_acid", "formic acid", "甲酸", "64-18-6", "carboxylic_acid"),
    ("acetic_acid", "acetic acid", "乙酸", "64-19-7", "carboxylic_acid"),
    ("propanoic_acid", "propanoic acid", "丙酸", "79-09-4", "carboxylic_acid"),
    ("butanoic_acid", "butanoic acid", "丁酸", "107-92-6", "carboxylic_acid"),
    ("hexanoic_acid", "hexanoic acid", "己酸", "142-62-1", "carboxylic_acid"),
    ("lactic_acid", "lactic acid", "乳酸", "50-21-5", "carboxylic_acid"),
    ("benzoic_acid", "benzoic acid", "苯甲酸", "65-85-0", "carboxylic_acid"),
    ("salicylic_acid", "salicylic acid", "水杨酸", "69-72-7", "carboxylic_acid"),
    ("acetic_anhydride", "acetic anhydride", "乙酸酐", "108-24-7", "anhydride"),
    # esters
    ("methyl_formate", "methyl formate", "甲酸甲酯", "107-31-3", "ester"),
    ("ethyl_formate", "ethyl formate", "甲酸乙酯", "109-94-4", "ester"),
    ("methyl_acetate", "methyl acetate", "乙酸甲酯", "79-20-9", "ester"),
    ("ethyl_acetate", "ethyl acetate", "乙酸乙酯", "141-78-6", "ester"),
    ("propyl_acetate", "propyl acetate", "乙酸丙酯", "109-60-4", "ester"),
    ("butyl_acetate", "butyl acetate", "乙酸丁酯", "123-86-4", "ester"),
    ("isoamyl_acetate", "isoamyl acetate", "乙酸异戊酯", "123-92-2", "ester"),
    ("benzyl_acetate", "benzyl acetate", "乙酸苄酯", "140-11-4", "ester"),
    ("ethyl_propanoate", "ethyl propanoate", "丙酸乙酯", "105-37-3", "ester"),
    ("ethyl_butanoate", "ethyl butanoate", "丁酸乙酯", "105-54-4", "ester"),
    ("ethyl_lactate", "ethyl lactate", "乳酸乙酯", "97-64-3", "ester"),
    ("methyl_benzoate", "methyl benzoate", "苯甲酸甲酯", "93-58-3", "ester"),
    ("ethyl_benzoate", "ethyl benzoate", "苯甲酸乙酯", "93-89-0", "ester"),
    ("methyl_salicylate", "methyl salicylate", "水杨酸甲酯", "119-36-8", "ester"),
    ("methyl_methacrylate", "methyl methacrylate", "甲基丙烯酸甲酯", "80-62-6", "ester"),
    ("aspirin", "acetylsalicylic acid", "阿司匹林", "50-78-2", "pharmaceutical"),
    # aldehydes / ketones
    ("acetaldehyde", "acetaldehyde", "乙醛", "75-07-0", "aldehyde"),
    ("propanal", "propanal", "丙醛", "123-38-6", "aldehyde"),
    ("hexanal", "hexanal", "己醛", "66-25-1", "aldehyde"),
    ("benzaldehyde", "benzaldehyde", "苯甲醛", "100-52-7", "aldehyde"),
    ("cinnamaldehyde", "cinnamaldehyde", "肉桂醛", "104-55-2", "aldehyde"),
    ("vanillin", "vanillin", "香草醛", "121-33-5", "aldehyde"),
    ("acetone", "acetone", "丙酮", "67-64-1", "ketone"),
    ("butanone", "2-butanone", "丁酮", "78-93-3", "ketone"),
    ("cyclohexanone", "cyclohexanone", "环己酮", "108-94-1", "ketone"),
    ("acetophenone", "acetophenone", "苯乙酮", "98-86-2", "ketone"),
    ("camphor", "camphor", "樟脑", "76-22-2", "terpenoid"),
    # aromatics
    ("benzene", "benzene", "苯", "71-43-2", "aromatic"),
    ("toluene", "toluene", "甲苯", "108-88-3", "aromatic"),
    ("ethylbenzene", "ethylbenzene", "乙苯", "100-41-4", "aromatic"),
    ("o_xylene", "o-xylene", "邻二甲苯", "95-47-6", "aromatic"),
    ("m_xylene", "m-xylene", "间二甲苯", "108-38-3", "aromatic"),
    ("p_xylene", "p-xylene", "对二甲苯", "106-42-3", "aromatic"),
    ("styrene", "styrene", "苯乙烯", "100-42-5", "aromatic"),
    ("naphthalene", "naphthalene", "萘", "91-20-3", "aromatic"),
    ("phenol", "phenol", "苯酚", "108-95-2", "phenol"),
    ("eugenol", "eugenol", "丁香酚", "97-53-0", "phenol"),
    ("anisole", "anisole", "苯甲醚", "100-66-3", "ether"),
    ("aniline", "aniline", "苯胺", "62-53-3", "amine"),
    ("nitrobenzene", "nitrobenzene", "硝基苯", "98-95-3", "nitro"),
    ("chlorobenzene", "chlorobenzene", "氯苯", "108-90-7", "halogenated"),
    ("pyridine", "pyridine", "吡啶", "110-86-1", "heterocycle"),
    # ethers / halogenated / polar aprotic
    ("diethyl_ether", "diethyl ether", "乙醚", "60-29-7", "ether"),
    ("tetrahydrofuran", "tetrahydrofuran", "四氢呋喃", "109-99-9", "ether"),
    ("dioxane_1_4", "1,4-dioxane", "1,4-二氧六环", "123-91-1", "ether"),
    ("dichloromethane", "dichloromethane", "二氯甲烷", "75-09-2", "halogenated"),
    ("chloroform", "chloroform", "氯仿", "67-66-3", "halogenated"),
    ("carbon_tetrachloride", "carbon tetrachloride", "四氯化碳", "56-23-5", "halogenated"),
    ("dichloroethane_1_2", "1,2-dichloroethane", "1,2-二氯乙烷", "107-06-2", "halogenated"),
    ("acetonitrile", "acetonitrile", "乙腈", "75-05-8", "nitrile"),
    ("dmso", "dimethyl sulfoxide", "二甲亚砜", "67-68-5", "sulfoxide"),
    ("dmf", "N,N-dimethylformamide", "N,N-二甲基甲酰胺", "68-12-2", "amide"),
    # alkanes / alkenes (GC retention-index anchors)
    ("pentane", "pentane", "正戊烷", "109-66-0", "alkane"),
    ("hexane", "hexane", "正己烷", "110-54-3", "alkane"),
    ("heptane", "heptane", "正庚烷", "142-82-5", "alkane"),
    ("octane", "octane", "正辛烷", "111-65-9", "alkane"),
    ("nonane", "nonane", "正壬烷", "111-84-2", "alkane"),
    ("decane", "decane", "正癸烷", "124-18-5", "alkane"),
    ("dodecane", "dodecane", "正十二烷", "112-40-3", "alkane"),
    ("cyclohexane", "cyclohexane", "环己烷", "110-82-7", "alkane"),
    ("cyclohexene", "cyclohexene", "环己烯", "110-83-8", "alkene"),
    ("limonene", "limonene", "柠檬烯", "138-86-3", "terpene"),
    # small inorganics / gases present in the reaction templates
    ("water", "water", "水", "7732-18-5", "inorganic"),
    ("hydrogen_peroxide", "hydrogen peroxide", "过氧化氢", "7722-84-1", "inorganic"),
    ("carbon_dioxide", "carbon dioxide", "二氧化碳", "124-38-9", "gas"),
    ("ammonia", "ammonia", "氨", "7664-41-7", "gas"),
    # pharmaceuticals / HPLC-UV teaching classics
    ("caffeine", "caffeine", "咖啡因", "58-08-2", "pharmaceutical"),
    ("paracetamol", "acetaminophen", "对乙酰氨基酚", "103-90-2", "pharmaceutical"),
    ("ibuprofen", "ibuprofen", "布洛芬", "15687-27-1", "pharmaceutical"),
]

IDS = [row[0] for row in COMPOUNDS]
BY_ID = {row[0]: {"id": row[0], "name_en": row[1], "name_zh": row[2], "cas": row[3], "class": row[4]} for row in COMPOUNDS}
assert len(IDS) == len(set(IDS)), "duplicate substance ids"
assert len({r[3] for r in COMPOUNDS}) == len(COMPOUNDS), "duplicate CAS"
