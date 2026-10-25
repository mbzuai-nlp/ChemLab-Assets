"""Instrument consumables the robot uses to load samples (the default-lab inventory's not_built types, 2026-10-22).

Each item is the specific model the instrument's own documents use (manual / brochure page cited, local copies under
references/manuals - page references only) with the consumable's catalogue data, and it fits the instrument's slot in the
Chemistry-Sim / Matterix slot tables.  Where the instrument asset already models the accessory, its geometry is reused
(a4S film roll mesh; TL2300 cell and DSC pan dimensions; Spinsolve tube and port size).
"""
from __future__ import annotations

import json
import math

import numpy as np
import trimesh

from containers import (CAP_SILVER, CAP_WHITE, GLASS_CLEAR, PP_BLACK, PP_CLEAR, ScrewCapSpec, build_screwcap, DENSITY)
from labware_kit import (FRANKA_MAX_GRASP_M, HAND_ABOVE_TCP_M, ROOT, LabwareAsset, Link, Prim, fill_height, frame,
                         inner_volume_below, profile_volume, revolve, ring_boxes, top_down_frames)
import sbs

ACCESS = "accessed 2026-10-22"
MAN = "references/manuals"           # local copies (never published); cited by file + page only

SRC = {
    "spinsolve_brochure": {"kind": "manufacturer", "reference": f"Magritek Spinsolve 80 brochure 2026 ({MAN}/magritek_spinsolve80/datasheet__spinsolve80_brochure_2026.pdf)",
                           "locator": "p.8 specifications: standard 5 mm OD NMR sample tubes, 7 in long; minimum sample volume 250 uL", "note": "Tube the Spinsolve takes.", "licence": "facts"},
    "norell_s5_500_7": {"kind": "manufacturer", "reference": "Norell S-5-500-7 NMR tube (Sigma-Aldrich NORS55007, https://b2b.sigmaaldrich.com/US/en/product/aldrich/nors55007)",
                        "locator": "OD 5 mm, ID 4.2 mm, length 7 in (178 mm), with cap", "note": "5 mm x 7 in NMR tube. " + ACCESS + ".", "licence": "facts"},
    "spinsolve_asset": {"kind": "code", "reference": "Chemistry-Sim assets/basic40_batch02/t4_measure/magritek_spinsolve80 (sample_tube part O5 x 180 mm; Matterix slot sample_port r 5 mm)",
                        "locator": "geometry_manifest.json components.sample_tube; slots_index sample_port", "note": "The instrument asset's own tube proxy (reuse check).", "licence": "project"},
    "tl23_datasheet": {"kind": "manufacturer", "reference": f"Hach TL23 series benchtop turbidimeter data sheet DOC052.53.25013 ({MAN}/hach_tl2300epa/)",
                       "locator": "p.3: sample cell = round cells 95 x 25 mm borosilicate glass with rubber-lined screw caps; 25 mm cell: 20 mL minimum sample",
                       "note": "Cell the TL2300 takes (also the sealed StablCal standard format).", "licence": "facts"},
    "tl2300_asset": {"kind": "code", "reference": "Chemistry-Sim assets/expansion_batch03/t4_measure/hach_tl2300epa (sample_cell + cell_cap parts; Matterix slot `cell` hole r 12.5 mm)",
                     "locator": "geometry_manifest.json components.sample_cell / cell_cap", "note": "Reused dimensions of the instrument asset's cell.", "licence": "project"},
    "pa800_brochure": {"kind": "manufacturer", "reference": f"SCIEX PA 800 Plus brochure ({MAN}/sciex_pa800/datasheet__pa800plus_brochure.pdf) and system overview guide",
                       "locator": "brochure p.10 accessories: Universal vials (100 pack) A62251, universal vial caps A62250, SDS-MW analysis kit 390953; overview guide p.21: buffer trays 36 universal vials, sample tray 48 universal vials",
                       "note": "Vials the PA 800 Plus trays take.", "licence": "facts"},
    "sciex_a62251": {"kind": "manufacturer", "reference": "SCIEX Universal Vials 100 pack A62251 / Universal Vial Caps A62250 (https://us-store.sciex.com/USD/capillary-electrophoresis/Universal-Vials-100-Pack-zidA62251)",
                     "locator": "polymethylpentene vial, single use; caps sold separately", "note": "No dimensions published on the store page. " + ACCESS + ".", "licence": "facts"},
    "pa800_asset": {"kind": "code", "reference": "Chemistry-Sim assets/expansion_batch04/t3_analytical/sciex_pa800 + Matterix slots_index (BI_/BO_/SI_/SO_ holes r 6 mm)",
                    "locator": "slot radius 0.006 m", "note": "Tray hole size the vial must fit.", "licence": "project"},
    "facsaria_guide": {"kind": "manufacturer", "reference": f"BD FACSAria III User's Guide ({MAN}/bd_facsariaiii/operating_manual__facsaria_iii_users_guide_*.pdf); tech specs 2018",
                       "locator": "guide p.286: 5-mL polystyrene test tubes 12 x 75 mm (BD Falcon), uncapped, 125 per bag, 352052; tech specs p.3: 12 x 75 mm sample tubes",
                       "note": "Sample tube the FACSAria III takes.", "licence": "facts"},
    "cfx_flier": {"kind": "manufacturer", "reference": f"Bio-Rad CFX Opus 96 flier bulletin 7299 ({MAN}/biorad_cfxopus/datasheet__cfx_opus_96_flier_bulletin_7299.pdf)",
                  "locator": "p.2 ordering: HSP9655 Hard-Shell 96-well PCR plates, pkg of 50, low profile, thin wall, skirted, white shell / white wells",
                  "note": "Plate the CFX Opus 96 uses.", "licence": "facts"},
    "ot_biorad_pcr": {"kind": "code", "reference": "Opentrons labware definition biorad_96_wellplate_200ul_pcr v1 (Apache-2.0; brandId hsp9601); local copy references/labware/opentrons/",
                      "locator": "127.76 x 85.48 x 16.06 mm; wells d 5.46 mm, depth 14.81 mm, bottom z 1.25 mm, 200 uL", "note": "Same Hard-Shell low-profile skirted geometry as HSP9655 (white wells).", "licence": "Apache-2.0 (Opentrons)"},
    "dsc_guide": {"kind": "manufacturer", "reference": f"TA Instruments DSC Q Series Getting Started Guide rev N ({MAN}/ta_dsc2500/); UCSB DSC2500 quick start",
                  "locator": "p.64 consumables: Tzero aluminum pans 901683.901, Tzero aluminum lids 901671.901 (pkg of 100); quick start p.1: Tzero pans for solids",
                  "note": "Pans the DSC 2500 Fusion cell takes.", "licence": "facts"},
    "dsc_asset": {"kind": "code", "reference": "Chemistry-Sim assets/expansion_batch03/t1_thermal/ta_dsc2500 (sample_pan cylinder r 3.1 mm x 2.5 mm incl. rim; Matterix slot `sample` r 3 mm)",
                  "locator": "geometry_manifest.json components.sample_pan / sample_pan_rim", "note": "Reused pan dimensions (photo estimate in that asset; TA does not publish them).", "licence": "project"},
    "elementar_capsule": {"kind": "manufacturer", "reference": "Elementar tin capsules 11 x 8 mm, V 0.5 mL, 100 pcs, SKU 200006458 (former S05 003 400) (https://portal.elementar.com/us/tin-capsules-11-x-8-mm-v-0-5-ml-100-pcs-package.html)",
                          "locator": "11 mm high x 8 mm diameter, 0.5 mL, for solids", "note": "Tin capsule for CHNS combustion (vario EL cube carousel). " + ACCESS + ".", "licence": "facts"},
    "varioel_asset": {"kind": "code", "reference": "Matterix slots_index instrument.elementar_varioel sample_mount (port r 4 mm)", "locator": "slot radius 0.004 m",
                      "note": "Carousel position size the capsule must fit.", "licence": "project"},
    "hellma_100_10_40": {"kind": "manufacturer", "reference": "Hellma macro cell 100-QS, 10 mm, 3500 uL, PTFE lid, cat. 100-10-40 (https://analytics-shop.com/us/hl100-10-40)",
                         "locator": "external 12.5 x 12.5 x 45 mm, internal width 9.5 mm, light path 10 mm, 200-2500 nm, PTFE lid", "note": "Quartz cuvette for UV-vis / fluorescence cell holders (12.5 mm square standard). " + ACCESS + ".", "licence": "facts"},
    "brand_759005": {"kind": "manufacturer", "reference": "BRAND macro PS cuvette 759005 (Fisher 10549301, https://fishersci.co.uk/shop/products/polystyrene-macro-cuvette/10549301; VWR listing)",
                     "locator": "12.5 x 12.5 x 45 mm, light path 10 mm, 2.5-4.5 mL, 340-900 nm, window 10 x 35 mm, 100 per pack", "note": "Disposable visible-range cuvette. " + ACCESS + ".", "licence": "facts"},
    "protean_gels_manual": {"kind": "manufacturer", "reference": f"Bio-Rad Mini-PROTEAN Precast Gels instruction manual 1658100 ({MAN}/biorad_miniprotean/other__mini-protean_precast_gels_manual_1658100.pdf)",
                            "locator": "p.8 1.4 Specifications: gel 7.2 x 8.6 cm, 1.0 mm thick; cassette 8.5 x 10 cm, styrene copolymer; 10-well comb 50 uL",
                            "note": "Mini-PROTEAN TGX 4-20 % 10-well precast gel (cat. 4561094).", "licence": "facts"},
    "biacore_handbook": {"kind": "manufacturer", "reference": f"Biacore T200 Instrument Handbook ({MAN}/cytiva_biacoret200/operating_manual__biacore_t200_instrument_handbook.pdf)",
                         "locator": "p.21 sensor chip port; p.29 sensor chip = gold-coated glass slide on a supporting frame, normally in a protective sheath; p.30 Sensor Chip CM5 general-purpose chip",
                         "note": "Chip the Biacore T200 docks.", "licence": "facts"},
    "cytiva_cm5": {"kind": "manufacturer", "reference": "Cytiva Series S Sensor Chip CM5, BR100530 (3 pack) / 29104988 (1) / 29149603 (10) (https://www.cytivalifesciences.com/en/us/products/items/sensor-chip-cm5-p-05858)",
                   "locator": "Series S chip for Biacore T200; carboxymethylated dextran on gold", "note": "Chip and sheath dimensions are not published (assumption below). " + ACCESS + ".", "licence": "facts"},
    "biacore_asset": {"kind": "code", "reference": "Chemistry-Sim assets/expansion_batch04/t4_measure/cytiva_biacoret200 (sensor_chip_port 90 x 40 x 60 mm) + Matterix slot sensor_chip_slot (port r 12 mm, insert +Y)",
                      "locator": "geometry_manifest.json components.sensor_chip_port; slots_index", "note": "Port the chip cassette must fit.", "licence": "project"},
    "ftir_manual": {"kind": "manufacturer", "reference": f"JASCO FT/IR-4000/6000 operation manual and FT/IR-4000/6000 brochure LFT1309 ({MAN}/jasco_ftir4700/)",
                    "locator": "operation manual p.25 and p.30: the supplied polystyrene film is inserted into the sample holder; brochure p.4: standard sample = polystyrene film",
                    "note": "Wavenumber check standard of the FT/IR-4700.", "licence": "facts"},
    "sigma_z804290": {"kind": "manufacturer", "reference": "Sigma-Aldrich Z804290 polystyrene IR calibration films (https://perfqws.sigmaaldrich.com/MY/en/product/aldrich/z804290)",
                      "locator": "38 um certified film (+ 76 um film) mounted on 2 x 3 in cardstock holders with a 19 mm aperture, for the universal slide holder",
                      "note": "Card format standard for the FTIR slide holder. " + ACCESS + ".", "licence": "facts"},
    "miniflex_docs": {"kind": "manufacturer", "reference": f"Rigaku MiniFlex brochure rev0723 and MiniFlex 600 SOPs ({MAN}/rigaku_miniflex600_external_cooling/)",
                      "locator": "brochure p.6: various sample holders (glass 0.2 mm / 0.5 mm, zero-background, air-sensitive); Boise State SOP p.4: standard plate-type sample holder; UNT quick guide p.1: glass holder on the standard stage",
                      "note": "Holder type the MiniFlex standard stage takes.", "licence": "facts"},
    "glass_holder_dims": {"kind": "manufacturer", "reference": "Machined Quartz glass XRD sample holder 50 x 35 x 2 mm, O15 x 0.2 mm well (https://machinedquartz.com/?p=7266)",
                          "locator": "rectangular 50 x 35 mm, 2 mm thick, round reception O15 mm, depth 0.2 mm", "note": "Closest published footprint for a 0.2 mm glass plate holder; Rigaku does not publish its outer size. " + ACCESS + ".", "licence": "facts"},
    "gelpak_ad1135": {"kind": "manufacturer", "reference": "Gel-Pak AD series hinged box AD-1135 (Ted Pella listing https://www.tedpella.com/storage-boxes-bags_html/gel-pak-boxes.aspx; Fisher 3.5 x 4.5 x 0.5 in hinged box)",
                      "locator": "3.5 x 4.5 x 0.5 in (89 x 114 x 12.7 mm), clear hinged box, gel-coated floor", "note": "Case for flat reference items (sapphire disc, XPS foils, quartz plate, dummy cell). " + ACCESS + ".", "licence": "facts"},
    "a4s_manual": {"kind": "manufacturer", "reference": f"Azenta a4S Automated Roll Heat Sealer user manual 347598 ({MAN}/azenta_automated_roll_heat_sealer_a4s/)",
                   "locator": "p.16 seal roll supports: <265 mm and <140 mm roll diameter; p.30: seal rolls 75-85 mm wide; p.41 seal profiles incl. 4ti-0520 Peel Seal (175 C, 3 s)",
                   "note": "Roll format the a4S takes.", "licence": "facts"},
    "a4s_asset": {"kind": "code", "reference": "Chemistry-Sim assets/basic40_batch02/t5_automation/azenta_heat_sealer (film_roll part: O126 x 80 mm mesh)",
                  "locator": "meshes/film_roll.obj, model.urdf link film_roll", "note": "Reused roll mesh.", "licence": "project"},
    "consumable_assumption": {"kind": "assumption", "reference": "consumable sizes not published by the maker", "locator": "engineering value",
                              "note": "Wall thicknesses, caps, lids and the few unpublished outer sizes are chosen to fit the instrument slot named in each asset; each is listed in that asset's limitations.",
                              "licence": "n/a"},
    "pp_hdpe_density": {"kind": "assumption", "reference": "typical resin densities (PP 900, PS 1050, PE 950, PMP 830, PTFE 2200 kg/m3; tin 7290, aluminium 2700, fused silica 2200)",
                        "locator": "engineering value", "note": "Used to compute masses from modelled volumes.", "licence": "n/a"},
    "robot_access": {"kind": "code", "reference": "robot-access constraints of the Franka arm set-up for the storage tasks, 2026-10-22", "locator": "Franka <= 74 mm grasp, hand 37 mm above TCP",
                     "note": "Grasp frames.", "licence": "project"},
}

from sources import SOURCES as _GLOBAL_SOURCES  # noqa: E402

for _k, _v in SRC.items():                       # make these ids usable by containers.build_screwcap (sources.pick)
    _GLOBAL_SOURCES.setdefault(_k, _v)

QUARTZ = (0.90, 0.95, 0.98, 0.30)
PS_CLEAR = (0.92, 0.94, 0.96, 0.45)
TIN = (0.80, 0.80, 0.78, 1.0)
ALU = (0.82, 0.83, 0.85, 1.0)
CARD = (0.95, 0.93, 0.85, 1.0)
GOLD = (0.85, 0.70, 0.30, 1.0)
PMP = (0.93, 0.93, 0.93, 0.6)
GEL_CASSETTE = (0.80, 0.88, 0.92, 0.55)


def _src(*ids):
    return {i: _GLOBAL_SOURCES[i] for i in ids}


def _finish(a: LabwareAsset, family: str, cls: str, fits, maker, model, cat, frames, extra: dict, limitations):
    a.labware.update({"type_id": a.asset_type_id, "family": family, "labware_class": cls, "fits": list(fits), "maker": maker, "model": model,
                      "catalogue_no": cat, "frames": frames, **extra})
    a.labware.setdefault("empty_mass_kg", round(sum(l.mass_kg for l in a.links), 6))
    a.limitations_zh = limitations


# ------------------------------------------------------------------------------------------------ screw / push-cap items
def nmr_tube() -> LabwareAsset:
    s = ScrewCapSpec(slug="norell_nmr_tube_5mm_7in", type_id="labware.norell_nmr_tube_5mm_7in", name_zh="Norell 5 mm × 7 in NMR 管（S-5-500-7，带盖）",
                     name_en="Norell S-5-500-7 NMR tube 5 mm x 7 in with cap", family="tube", nominal_ml=0.6, od_m=0.005, h_body_m=0.178, h_total_m=0.1835,
                     neck_r_m=0.0025, neck_in_r_m=0.0021, thread_zone_m=0.0075, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=0.0, wall_m=0.0004,
                     bottom_m=0.0005, body_material="borosilicate_3_3", body_rgba=GLASS_CLEAR, cap_od_m=0.0068, cap_h_m=0.013, cap_top_m=0.002,
                     cap_material="pe", cap_rgba=CAP_WHITE, turns=0.25, pitch_m=None, thread_name="push-on PE cap", bottom_shape="round", cone_h_m=0.0021,
                     tip_r_m=0.0006, n_ring=8, stands_upright=False, labware_class="nmr_tube", fits=("nmr_tube_5mm", "spinsolve_sample_port"),
                     sources=("spinsolve_brochure", "norell_s5_500_7", "spinsolve_asset", "consumable_assumption", "wall_assumption", "closure_turns", "pp_hdpe_density", "robot_access"),
                     dims_source="norell_s5_500_7", nominal_source="spinsolve_brochure", maker="Norell", model="Standard Series 500 MHz NMR tube, 5 mm x 7 in",
                     catalogue_no="S-5-500-7 (Sigma NORS55007)", storage_note_zh="放在 NMR 管架；样品 0.3–0.5 mL（Spinsolve 手册：最少 250 µL）",
                     extra_limitations=("推入式 PE 盖：按 touch + intent 规则“转 1/4 圈 + 提起”即取下；盖尺寸为假设。",
                                        "Spinsolve 实例中的 sample_tube 部件（Ø5×180 mm）与本管一致；装样还需转子（spinner，仪器侧，未单独建模）。"),
                     notes_zh="Spinsolve 80 使用标准 5 mm 外径、7 in 长的 NMR 管（Magritek 资料 p.8）；Norell S-5-500-7：外径 5 mm、内径 4.2 mm、长 178 mm。")
    a = build_screwcap(s)
    a.sources = _src(*s.sources)
    a.labware["nominal_capacity_m3"] = 6.0e-7
    a.labware["nominal_capacity_evidence"] = "typical NMR fill 0.6 mL (Spinsolve minimum 0.25 mL, brochure p.8)"
    a.labware["instrument_fit"] = {"instrument.magritek_spinsolve80": {"slot": "sample_port", "slot_radius_m": 0.005, "item_radius_m": 0.0025}}
    return a


def tl2300_cell() -> LabwareAsset:
    s = ScrewCapSpec(slug="hach_tl2300_sample_cell_25mm", type_id="labware.hach_tl2300_sample_cell_25mm", name_zh="Hach TL2300 样品池（Ø25 × 95 mm 硼硅玻璃，内衬螺旋盖）",
                     name_en="Hach TL23 round sample cell 95 x 25 mm with rubber-lined screw cap", family="vial", nominal_ml=20.0, od_m=0.025, h_body_m=0.095,
                     h_total_m=0.104, neck_r_m=0.0118, neck_in_r_m=0.0102, thread_zone_m=0.012, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=1.0,
                     wall_m=0.0012, bottom_m=0.0015, body_material="borosilicate_3_3", body_rgba=GLASS_CLEAR, cap_od_m=0.027, cap_h_m=0.013, cap_top_m=0.002,
                     cap_material="pp", cap_rgba=PP_BLACK, turns=1.0, pitch_m=0.0021, thread_name="cell screw cap (rubber-lined)", n_ring=16,
                     labware_class="turbidity_cell", fits=("turbidity_cell_25mm", "tl2300_cell"),
                     sources=("tl23_datasheet", "tl2300_asset", "consumable_assumption", "wall_assumption", "closure_turns", "pp_hdpe_density", "robot_access"),
                     dims_source="tl23_datasheet", maker="Hach", model="Round sample cell 95 x 25 mm, borosilicate, rubber-lined screw cap (TL23 series)",
                     catalogue_no="TL23 sample cell (data sheet DOC052.53.25013 p.3)", storage_note_zh="样品或 StablCal 密封标准（同规格）；放在架上或标准物质柜",
                     extra_limitations=("95×25 mm 为目录值；盖尺寸（Ø27×13 mm）与壁厚为假设（TL2300 实例中的 cell_cap 只建了露出部分）。",),
                     notes_zh="TL2300 浊度仪样品池：Ø25×95 mm 硼硅玻璃圆池 + 内衬螺旋盖（TL23 数据表 p.3）；StablCal 密封标准用同规格样品池。")
    a = build_screwcap(s)
    a.sources = _src(*s.sources)
    a.mechanisms["closure.thread"]["source_ids"] = ["consumable_assumption"]
    a.mechanisms["closure.thread"]["parameters"]["detach_after_rad"]["source_ids"] = ["consumable_assumption"]
    a.mechanisms["closure.thread"]["parameters"]["thread_pitch_m"]["source_ids"] = ["consumable_assumption"]
    a.state_slots["closure.state"]["source_ids"] = ["consumable_assumption"]
    a.prop("cap", "release_turns", s.turns, "1", "拧下所需圈数", "assumption", ["consumable_assumption"])
    for l in a.links:
        if l.joint:
            l.joint["source_ids"] = ["consumable_assumption"]
    a.labware["instrument_fit"] = {"instrument.hach_tl2300epa": {"slot": "cell", "slot_radius_m": 0.0125, "item_radius_m": 0.0125}}
    return a


def sciex_vial() -> LabwareAsset:
    s = ScrewCapSpec(slug="sciex_universal_vial_2ml", type_id="labware.sciex_universal_vial_2ml", name_zh="SCIEX PA 800 Plus 通用样品瓶（A62251）+ 盖（A62250）",
                     name_en="SCIEX universal vial A62251 with universal vial cap A62250", family="vial", nominal_ml=2.0, od_m=0.0115, h_body_m=0.031,
                     h_total_m=0.0335, neck_r_m=0.0055, neck_in_r_m=0.0048, thread_zone_m=0.006, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=0.5,
                     wall_m=0.0007, bottom_m=0.001, body_material="pp", body_rgba=PMP, cap_od_m=0.0125, cap_h_m=0.0085, cap_top_m=0.0015,
                     cap_material="pe", cap_rgba=(0.20, 0.35, 0.80, 1.0), turns=0.25, pitch_m=None, thread_name="push-on cap", n_ring=12,
                     labware_class="vial", fits=("ce_universal_vial", "pa800_tray_hole"),
                     sources=("pa800_brochure", "sciex_a62251", "pa800_asset", "consumable_assumption", "wall_assumption", "closure_turns", "pp_hdpe_density", "robot_access"),
                     dims_source="pa800_asset", nominal_source="consumable_assumption", maker="SCIEX", model="Universal vial (polymethylpentene) + universal vial cap",
                     catalogue_no="A62251 (vials, 100) / A62250 (caps, 100)", storage_note_zh="CE 缓冲液 / 样品；SDS-MW 试剂盒的液体分装进这些瓶",
                     extra_limitations=("SCIEX 不公布样品瓶外形：外径 11.5 mm、高 31 mm、容量 2 mL 为按 PA 800 托盘孔（r 6 mm）选的假设；材料按聚甲基戊烯（密度近似用 PP）。",
                                        "推入式盖：按“转 1/4 圈 + 提起”取下（touch + intent）。"),
                     notes_zh="PA 800 Plus 缓冲液托盘（36 位）与样品托盘（48 位）用的通用样品瓶与瓶盖（SCIEX A62251 / A62250，系统指南 p.21、资料 p.10）。")
    a = build_screwcap(s)
    a.sources = _src(*s.sources)
    a.labware["nominal_capacity_evidence"] = "assumption (2 mL class vial; not published)"
    a.labware["instrument_fit"] = {"instrument.sciex_pa800": {"slot": "BI_F1..SO_* tray holes", "slot_radius_m": 0.006, "item_radius_m": 0.00575}}
    return a


# ------------------------------------------------------------------------------------------------ open revolved items
def _open_revolved(slug, name_zh, name_en, family, cls, outer, inner, material, rgba, nominal_m3, nominal_ev, fits, maker, model, cat, sources, dims_src,
                   limitations, notes, n_ring=12, min_t=0.0008, upright=True, lid=None, fit=None):
    a = LabwareAsset(slug, f"labware.{slug}", name_zh, name_en, "consumables", f"labware.consumable.{family}", "passive", description_zh=notes, sources=_src(*sources))
    body_mesh = revolve(outer + inner, sections=48)
    H = outer[-1][1]
    R = max(r for r, _ in outer)
    col = [Prim("cylinder", [max(outer[1][0], 0.0006), max(inner[-1][1], 0.0004)], [0, 0, max(inner[-1][1], 0.0004) / 2])]
    pts = outer[1:]
    for (r0, z0), (r1, z1) in zip(pts[:-1], pts[1:]):
        if z1 - z0 > 1e-5 and max(r0, r1) > 0.0004:
            col += ring_boxes(r0, z0, r1, z1, min_t, n_ring)
    mass = body_mesh.volume * DENSITY.get(material, 1000.0)
    a.add(Link("body", name_zh.split("（")[0], "盛放样品", f"{material} 外壳", visual=[(body_mesh, rgba)], collision=col,
               solid_collision=[Prim("cylinder", [R, H], [0, 0, H / 2])], mass_kg=mass, mass_evidence="assumption",
               mass_note_zh=f"建模体积 × {material} 密度", material=material, geometry_evidence="documented"))
    interior = [(r, z) for r, z in reversed(inner)]
    brim = profile_volume(interior + [(0.0, H)])
    top = H
    if lid:
        lid(a, H)
        top = a.bounds()[1][2]
    z_g = max(min(top - HAND_ABOVE_TCP_M + 0.004, top * 0.6), min(0.004, top / 2))
    fr = top_down_frames(z_g, top, 2 * R, yaw_free=True, contact_z=(max(0.0, z_g - 0.009), z_g + 0.009),
                         note_zh="俯抓外壁；小件贴近台面时指尖会触台（捏取）")
    extra = {"nominal_capacity_m3": nominal_m3, "nominal_capacity_evidence": nominal_ev, "brim_capacity_m3": round(brim, 10),
             "interior": {"shape": "revolved", "radius_m": round(max(r for r, _ in inner), 5), "floor_m": round(inner[-1][1], 5), "height_m": H,
                          "profile_rz_m": [[round(r, 5), round(z, 5)] for r, z in interior],
                          "nominal_fill_height_m": round(fill_height(interior, nominal_m3), 6) if nominal_m3 and nominal_m3 < brim else None},
             "outer": {"diameter_m": 2 * R, "height_body_m": H, "source_id": dims_src}, "stands_upright": upright,
             "franka_grasp_ok": bool(2 * R <= FRANKA_MAX_GRASP_M)}
    if fit:
        extra["instrument_fit"] = fit
    _finish(a, family, cls, fits, maker, model, cat, fr, extra, limitations)
    a.prop("body", "outer_diameter_m", 2 * R, "m", "外径", "documented", [dims_src])
    a.prop("body", "height_m", H, "m", "高度", "documented", [dims_src])
    return a


def falcon_352052() -> LabwareAsset:
    R, L, t = 0.006, 0.075, 0.0008
    outer = [(0.0, 0.0)] + [(R * math.sin(a_), R * (1 - math.cos(a_))) for a_ in np.linspace(0.25, math.pi / 2, 6)] + [(R, L - 0.001), (R + 0.0004, L)]
    inner = [(R - t, L), (R - t, R)] + [((R - t) * math.sin(a_), t + (R - t) * (1 - math.cos(a_))) for a_ in np.linspace(math.pi / 2, 0.25, 6)] + [(0.0, t)]
    return _open_revolved("falcon_352052_tube_12x75", "Falcon 352052 圆底聚苯乙烯管 5 mL（12 × 75 mm，无盖）", "Falcon 352052 5 mL polystyrene round-bottom tube 12 x 75 mm",
                          "tube", "tube_12x75", outer, inner, "ps", PS_CLEAR, 5e-6, "documented (5-mL test tube, FACSAria guide p.286)",
                          ("tube_12x75", "facs_sample_tube"), "Corning (BD Falcon)", "5 mL polystyrene round-bottom test tube, 12 x 75 mm, uncapped, 125/bag", "352052",
                          ("facsaria_guide", "consumable_assumption", "pp_hdpe_density", "robot_access"), "facsaria_guide",
                          ["12×75 mm 与 5 mL 按 BD 指南 p.286；壁厚 0.8 mm 为假设。", "圆底管不能自立：放管架或仪器管座；无盖（上机前不加盖）。"],
                          "FACSAria III 上样管：BD Falcon 5 mL 聚苯乙烯圆底管 12×75 mm（无盖，125 支/袋，352052；BD 用户指南 p.286）。", upright=False,
                          fit={"instrument.bd_facsariaiii": {"slot": "sample_tube_seat", "slot_radius_m": 0.02, "item_radius_m": R}})


def elementar_capsule() -> LabwareAsset:
    R, H, t = 0.004, 0.011, 0.00012
    outer = [(0.0, 0.0), (R, 0.0), (R, H)]
    inner = [(R - t, H), (R - t, t), (0.0, t)]
    return _open_revolved("elementar_tin_capsule_11x8", "Elementar 锡囊 11 × 8 mm（0.5 mL，200006458）", "Elementar tin capsule 11 x 8 mm, 0.5 mL",
                          "capsule", "tin_capsule", outer, inner, "tin", TIN, 5e-7, "documented (Elementar: V 0.5 mL)",
                          ("tin_capsule", "varioel_carousel"), "Elementar", "Tin capsules 11 x 8 mm, V 0.5 mL, 100 pcs", "200006458 (former S05 003 400)",
                          ("elementar_capsule", "varioel_asset", "consumable_assumption", "pp_hdpe_density", "robot_access"), "elementar_capsule",
                          ["Ø8×11 mm 与 0.5 mL 按 Elementar 目录；锡箔厚 0.12 mm 为假设（碰撞壁 0.8 mm）。",
                           "实际使用时称样后把锡囊捏扁折叠成小块再放入自动进样盘；折叠由宿主记账（不模拟变形）。"],
                          "vario EL cube CHNS 分析用锡囊：Elementar 200006458（11×8 mm，0.5 mL，100 个/包）；进样盘位 r 4 mm。",
                          fit={"instrument.elementar_varioel": {"slot": "sample_mount", "slot_radius_m": 0.004, "item_radius_m": R}})


def tzero_pan() -> LabwareAsset:
    R, H, t = 0.0031, 0.0018, 0.0001

    def lid(a: LabwareAsset, Hb):
        disc = Prim("cylinder", [R - 0.0001, 0.0002], [0, 0, 0.0001])
        a.add(Link("lid", "Tzero 盖（901671.901）", "盖住样品，压机压合", "铝盖；压合后与盘不可分（宿主记录压合状态）", parent="body",
                   origin_m=[0.0, 0.0, Hb], joint={"type": "prismatic", "axis": [0, 0, 1], "limits": [0.0, 0.01], "evidence": "assumption",
                                                   "source_ids": ["consumable_assumption"], "note_zh": "未压合时可提起；压合（crimp）由 Tzero 压机完成，宿主记录"},
                   visual=[(disc.mesh(), ALU)], collision=[disc], mass_kg=disc.mesh().volume * 2700.0, mass_evidence="assumption",
                   mass_note_zh="铝盖体积 × 2700 kg/m3", material="aluminium", geometry_evidence="assumption", detachable=True))

    outer = [(0.0, 0.0), (R - 0.0002, 0.0), (R, 0.0002), (R, H)]
    inner = [(R - t, H), (R - t, t), (0.0, t)]
    a = _open_revolved("ta_tzero_pan_lid", "TA Tzero 铝坩埚 + 盖（901683.901 / 901671.901）", "TA Instruments Tzero aluminum pan + Tzero lid",
                       "dsc_pan", "dsc_pan", outer, inner, "aluminium", ALU, 2e-8, "assumption (sample ~5 mg; Tzero pan volume not published)",
                       ("dsc_pan", "dsc2500_sample_platform"), "TA Instruments", "Tzero aluminum pan + Tzero aluminum lid", "901683.901 + 901671.901",
                       ("dsc_guide", "dsc_asset", "consumable_assumption", "pp_hdpe_density", "robot_access"), "dsc_asset",
                       ["零件号按 TA 手册 p.64；盘径 6.2 mm 取自 DSC2500 实例的 sample_pan 部件（照片估计，TA 不公布尺寸），高 1.8 mm、壁厚 0.1 mm 为假设。",
                        "盖为可提起的独立刚体；压合（crimp）由 Tzero 压机完成，属宿主动作。"],
                       "DSC 2500 Fusion 池用 Tzero 铝坩埚与盖（TA 零件 901683.901 / 901671.901，入门指南 p.64）。", n_ring=12, min_t=0.0003, lid=lid,
                       fit={"instrument.ta_dsc2500": {"slot": "sample / reference", "slot_radius_m": 0.003, "item_radius_m": R}})
    a.labware["closure"] = {"type": "lift_off_lid", "link": "lid", "joint": "lid_joint", "axis": [0, 0, 1], "clear_lift_m": 0.002,
                            "rule_zh": "未压合：盖可提起/放回；压合后由宿主标记为 sealed（不可再分）", "parts": {"body": "parts/body.urdf", "lid": "parts/lid.urdf"}}
    return a


# ------------------------------------------------------------------------------------------------ box-shaped items
def _box_link(a, lid_name, prims, rgba, material, rho, name_zh, func, phys, parent=None, origin=(0, 0, 0), joint=None, detachable=False, mass_note=None):
    m = trimesh.util.concatenate([p.mesh() for p in prims])
    vol = sum(p.mesh().volume for p in prims)
    return a.add(Link(lid_name, name_zh, func, phys, parent=parent, origin_m=list(origin), joint=joint, visual=[(m, rgba)], collision=prims,
                      mass_kg=vol * rho, mass_evidence="assumption", mass_note_zh=mass_note or f"体积 × {material} 密度 {rho:.0f} kg/m3", material=material,
                      geometry_evidence="documented", detachable=detachable))


def square_cuvette(slug, name_zh, name_en, material, rgba, rho, nominal_m3, fits, maker, model, cat, sources, dims_src, with_lid, limitations, notes,
                   inner=(0.0100, 0.0095), tb=0.0015):
    a = LabwareAsset(slug, f"labware.{slug}", name_zh, name_en, "consumables", "labware.consumable.cuvette", "passive", description_zh=notes, sources=_src(*sources))
    X, H = 0.0125, 0.045
    ix, iy = inner                               # light path 10 mm (along X) x internal width
    wx, wy = (X - ix) / 2, (X - iy) / 2
    prims = [Prim("box", [X, X, tb], [0, 0, tb / 2]),
             Prim("box", [wx, X, H - tb], [-(X - wx) / 2, 0, tb + (H - tb) / 2]), Prim("box", [wx, X, H - tb], [(X - wx) / 2, 0, tb + (H - tb) / 2]),
             Prim("box", [ix, wy, H - tb], [0, -(X - wy) / 2, tb + (H - tb) / 2]), Prim("box", [ix, wy, H - tb], [0, (X - wy) / 2, tb + (H - tb) / 2])]
    _box_link(a, "body", prims, rgba, material, rho, "比色皿", "盛放样品；光程 10 mm（沿 X）", f"{material} 方形比色皿 12.5×12.5×45 mm")
    top = H
    if with_lid:
        lid = [Prim("box", [ix - 0.0003, iy - 0.0003, 0.006], [0, 0, -0.003]), Prim("box", [X, X, 0.002], [0, 0, 0.001])]
        _box_link(a, "lid", lid, (0.96, 0.96, 0.96, 1.0), "ptfe", 2200.0, "PTFE 盖", "防挥发/防尘", "PTFE 塞式盖", parent="body", origin=(0, 0, H),
                  joint={"type": "prismatic", "axis": [0, 0, 1], "limits": [0.0, 0.02], "evidence": "assumption", "source_ids": ["consumable_assumption"],
                         "note_zh": "盖为塞入式：提起即取下（touch + intent）"}, detachable=True)
        top = H + 0.002
        a.labware["closure"] = {"type": "lift_off_lid", "link": "lid", "joint": "lid_joint", "axis": [0, 0, 1], "clear_lift_m": 0.008,
                                "rule_zh": "抓盖提起 ≥ clear_lift_m 即取下；放回对准后压下", "parts": {"body": "parts/body.urdf", "lid": "parts/lid.urdf"}}
    z_g = top - HAND_ABOVE_TCP_M + 0.004
    fr = top_down_frames(z_g, top, X, yaw_free=False, contact_z=(z_g - 0.009, z_g + 0.009),
                         note_zh="俯抓磨砂侧面：手指沿 Y 合拢（光学窗在 ±X 面，不要夹光路面）")
    fr["optical_axis"] = frame([0, 0, 0.015], "base_link", rot=[0, 0.70710678, 0, 0.70710678], note_zh="光路沿 +X，中心高 15 mm（标准 15 mm 光束中心高）")
    brim = ix * iy * (H - tb)
    _finish(a, "cuvette", "cuvette_10mm", fits, maker, model, cat, fr,
            {"nominal_capacity_m3": nominal_m3, "nominal_capacity_evidence": "documented", "brim_capacity_m3": round(brim, 10),
             "interior": {"shape": "box", "size_m": [ix, iy], "radius_m": round(min(ix, iy) / 2, 5), "floor_m": tb, "height_m": H,
                          "note_zh": "方形内腔 10 × 9.5 mm（radius_m 为内切圆，供圆柱近似）"},
             "light_path_m": 0.010, "outer": {"size_m": [X, X, H], "source_id": dims_src}, "stands_upright": True, "franka_grasp_ok": True},
            limitations)
    a.prop("body", "outer_size_m", [X, X, H], "m", "外形 12.5×12.5×45 mm", "documented", [dims_src])
    a.prop("body", "light_path_m", 0.010, "m", "光程 10 mm", "documented", [dims_src])
    return a


def flat_item(slug, name_zh, name_en, family, cls, size, rgba, material, rho, fits, maker, model, cat, sources, dims_src, limitations, notes,
              features=(), grasp_axis="Y", fit=None, extra=None, franka_ok=True):
    """A flat rectangular item lying on its largest face (card, holder, chip cassette, gel cassette)."""
    a = LabwareAsset(slug, f"labware.{slug}", name_zh, name_en, "consumables", f"labware.consumable.{family}", "passive", description_zh=notes, sources=_src(*sources))
    X, Y, Z = size
    prims = [Prim("box", [X, Y, Z], [0, 0, Z / 2])] + list(features)
    _box_link(a, "body", prims, rgba, material, rho, name_zh.split("（")[0], "承载样品/标准", f"{material} 平板件 {X*1000:.1f}×{Y*1000:.1f}×{Z*1000:.1f} mm")
    top = max(p.mesh().bounds[1][2] for p in prims)
    w = Y if grasp_axis == "Y" else X
    z_g = top / 2
    fr = top_down_frames(z_g, top, w, yaw_free=False, contact_z=(0.0, top),
                         note_zh=f"沿 {grasp_axis} 方向夹两侧边（平放件：指尖贴台面捏取）")
    if grasp_axis == "X":
        fr["grasp"]["rot_xyzw"] = [0.70710678, 0.70710678, 0.0, 0.0]
    fr["grasp"]["franka_ok"] = bool(franka_ok and w <= FRANKA_MAX_GRASP_M)
    if not franka_ok and w <= FRANKA_MAX_GRASP_M:
        fr["grasp"]["franka_blocked_reason"] = "flat item lying on the bench: no finger clearance under it (stand it in a holder or use a wider / suction gripper)"
    ext = {"outer": {"size_m": [X, Y, Z], "source_id": dims_src}, "stands_upright": True, "franka_grasp_ok": fr["grasp"]["franka_ok"]}
    if fit:
        ext["instrument_fit"] = fit
    ext.update(extra or {})
    _finish(a, family, cls, fits, maker, model, cat, fr, ext, limitations)
    a.prop("body", "outer_size_m", [X, Y, Z], "m", "外形", "documented" if dims_src not in ("consumable_assumption",) else "assumption", [dims_src])
    return a


def gel_cassette() -> LabwareAsset:
    X, Y, Z = 0.100, 0.085, 0.0055               # 10 cm wide, 8.5 cm high (manual p.8), lying flat; thickness assumed
    comb = Prim("box", [0.080, 0.004, 0.003], [0, Y / 2 - 0.002, Z + 0.0015])
    wells = [Prim("box", [0.0055, 0.008, 0.0004], [-0.0315 + 0.007 * k, Y / 2 - 0.012, Z + 0.0002]) for k in range(10)]
    a = flat_item("biorad_tgx_gel_4_20_10well", "Bio-Rad Mini-PROTEAN TGX 预制胶 4–20 %（10 孔，4561094）", "Bio-Rad Mini-PROTEAN TGX precast gel 4-20 %, 10-well, 50 uL",
                  "gel", "precast_gel_mini", (X, Y, Z), GEL_CASSETTE, "ps", 1050.0, ("mini_protean_gel", "protean_gel_slot"), "Bio-Rad",
                  "Mini-PROTEAN TGX Precast Gel, 4-20 %, 10-well, 50 uL", "4561094", ("protean_gels_manual", "consumable_assumption", "pp_hdpe_density", "robot_access"),
                  "protean_gels_manual",
                  ["卡盒 8.5×10 cm、胶 7.2×8.6 cm×1.0 mm 按手册 p.8；卡盒厚 5.5 mm、梳子与孔位为假设；平放时 8.5 cm 方向沿 Y。",
                   "上电泳槽时竖直插入电极组件（Matterix 槽位 gel_1..4，沿 −Z 插入）；拔梳、加样由宿主/移液器记录。",
                   "10 cm 宽超出 Franka 可抓宽度：沿 Y 夹 8.5 cm 边也超出；需宽口夹爪或竖放在架上从短边抓取（franka_ok=false）。"],
                  "Mini-PROTEAN Tetra 电泳槽用 TGX 预制胶卡盒（卡盒 8.5×10 cm，手册 p.8）。", features=[comb] + wells, franka_ok=False,
                  fit={"instrument.biorad_miniprotean": {"slot": "gel_1..gel_4", "slot_radius_m": 0.05, "item_half_width_m": X / 2}},
                  extra={"gel": {"size_m": [0.086, 0.072, 0.001], "wells": 10, "well_volume_m3": 5e-8, "percent": "4-20"},
                         "nominal_capacity_m3": 5e-7, "nominal_capacity_evidence": "10 wells x 50 uL (catalogue)", "storage_note_zh": "2–8 °C 保存"})
    return a


def sensor_chip() -> LabwareAsset:
    X, Y, Z = 0.022, 0.070, 0.004
    window = Prim("box", [0.010, 0.012, 0.0004], [0, -0.020, Z + 0.0002])
    return flat_item("cytiva_series_s_sensor_chip_cm5", "Cytiva Series S 传感芯片 CM5（含保护套，BR100530）", "Cytiva Series S Sensor Chip CM5 in its sheath",
                     "sensor_chip", "spr_chip", (X, Y, Z), (0.20, 0.22, 0.26, 1.0), "ps", 1050.0, ("spr_sensor_chip", "biacore_chip_port"), "Cytiva",
                     "Series S Sensor Chip CM5", "BR100530 (3) / 29104988 (1) / 29149603 (10)",
                     ("biacore_handbook", "cytiva_cm5", "biacore_asset", "consumable_assumption", "pp_hdpe_density", "robot_access"), "consumable_assumption",
                     ["芯片与保护套外形 Cytiva 未公布：22×70×4 mm 为按 Biacore 实例芯片口（r 12 mm，沿 +Y 插入）选的假设；金膜窗口位置为示意。",
                      "插入/对接（dock）由仪器 chip.dock 能力与宿主完成；芯片表面状态只在运行层记录。"],
                     "Biacore T200 用 Series S Sensor Chip CM5：金膜玻片装在支撑框与保护套内（手册 p.29–30），沿箭头方向插入芯片口。",
                     features=[window], grasp_axis="X",
                     fit={"instrument.cytiva_biacoret200": {"slot": "sensor_chip_slot", "slot_radius_m": 0.012, "item_half_width_m": X / 2, "insert_axis": [0, 1, 0]}},
                     extra={"insert_direction_zh": "沿 +Y（窗口端朝前插入）", "storage_note_zh": "2–8 °C 干燥保存"})


def ir_card() -> LabwareAsset:
    X, Y, Z = 0.0762, 0.0508, 0.0008
    film = Prim("cylinder", [0.0095, 0.0001], [0, 0, Z + 0.00005])
    return flat_item("sigma_ir_polystyrene_card_z804290", "红外聚苯乙烯校准膜卡（38 µm，2 × 3 in 卡，Ø19 mm 窗，Z804290）", "Polystyrene IR calibration film 38 um on 2 x 3 in card (Sigma-Aldrich Z804290)",
                     "ir_standard", "ir_film_card", (X, Y, Z), CARD, "cardstock", 800.0, ("ir_film_card", "ftir_slide_holder"), "Sigma-Aldrich",
                     "Polystyrene IR calibration films (38 um certified + 76 um), 2 x 3 in cardstock holders, 19 mm aperture", "Z804290",
                     ("ftir_manual", "sigma_z804290", "consumable_assumption", "robot_access"), "sigma_z804290",
                     ["2×3 in 卡与 Ø19 mm 窗按目录；卡厚 0.8 mm 为假设；膜只建一层示意。", "平放薄卡：Franka 俯抓需指尖贴台面，或把卡立放在卡架上（franka_ok=false）。"],
                     "FT/IR-4700 波数检查标准：聚苯乙烯膜（仪器附带，操作手册 p.25/p.30），这里用通用 2×3 in 片夹规格的 Sigma Z804290 膜卡。",
                     features=[film], franka_ok=False,
                     fit={"instrument.jasco_ftir4700": {"slot": "accessory (slide holder in the sample compartment)", "slot_radius_m": 0.0755, "item_half_width_m": X / 2}},
                     extra={"film": {"thickness_m": 3.8e-5, "aperture_diameter_m": 0.019, "bands_cm-1": [3060.0, 2849.5, 1601.2, 1028.3, 906.7]}})


def xrd_holder() -> LabwareAsset:
    X, Y, Z = 0.050, 0.035, 0.002
    well = Prim("cylinder", [0.0075, 0.0002], [0, 0, Z - 0.0001])
    return flat_item("rigaku_glass_holder_0p2mm", "XRD 玻璃样品板（0.2 mm 槽，MiniFlex 标准平板样品架）", "XRD glass sample holder plate, 0.2 mm well (MiniFlex standard stage)",
                     "xrd_holder", "xrd_flat_holder", (X, Y, Z), (0.86, 0.93, 0.96, 0.5), "soda_lime_glass", 2500.0, ("xrd_flat_holder", "miniflex_standard_stage"),
                     "Rigaku (holder type) / Machined Quartz (published footprint)", "Glass sample holder, 0.2 mm deep well (MiniFlex standard plate-type holder)",
                     "Rigaku glass holder 0.2 mm (outer size per Machined Quartz 50 x 35 x 2 mm, O15 x 0.2 mm well)",
                     ("miniflex_docs", "glass_holder_dims", "consumable_assumption", "robot_access"), "glass_holder_dims",
                     ["Rigaku 不公布玻璃样品板外形：50×35×2 mm、Ø15×0.2 mm 槽取最接近的公开规格（Machined Quartz）；与 MiniFlex 标准样品台（槽位 r 30 mm）匹配为假设。",
                      "清单类型名为 zero_background：零背景硅板外形同样未公布，暂以 0.2 mm 玻璃板代表（材料差异只影响背景，不影响几何）。",
                      "装粉、压平由宿主记录；平放薄板：Franka 需指尖贴台面捏取。"],
                     "MiniFlex 600 标准平板型样品架（Boise State SOP p.4；手册列出玻璃 0.2/0.5 mm 与零背景硅板，brochure p.6）。",
                     features=[well], grasp_axis="Y",
                     fit={"instrument.rigaku_miniflex600_external_cooling": {"slot": "sample_holder (standard stage)", "slot_radius_m": 0.03, "item_half_diagonal_m": 0.0305}},
                     extra={"well": {"diameter_m": 0.015, "depth_m": 0.0002}, "nominal_capacity_m3": 3.5e-8,
                            "nominal_capacity_evidence": "computed: O15 x 0.2 mm well"})


def gelpak_case() -> LabwareAsset:
    X, Y, Zb, Zl = 0.1143, 0.0889, 0.0090, 0.0037
    t = 0.0012
    base = [Prim("box", [X, Y, t], [0, 0, t / 2])] + [Prim("box", [X, t, Zb - t], [0, s * (Y - t) / 2, t + (Zb - t) / 2]) for s in (-1, 1)] + \
           [Prim("box", [t, Y - 2 * t, Zb - t], [s * (X - t) / 2, 0, t + (Zb - t) / 2]) for s in (-1, 1)]
    a = LabwareAsset("gelpak_ad1135_case", "labware.gelpak_ad1135_case", "Gel-Pak AD-1135 透明翻盖样品盒（3.5 × 4.5 × 0.5 in）",
                     "Gel-Pak AD-1135 clear hinged box 3.5 x 4.5 x 0.5 in", "consumables", "labware.consumable.case", "passive",
                     description_zh="放平板标准件的翻盖盒（蓝宝石盘、XPS 金属箔、石英控制片、假电解池等）：Gel-Pak AD-1135，89×114×12.7 mm，底面带凝胶。",
                     sources=_src("gelpak_ad1135", "consumable_assumption", "robot_access"))
    _box_link(a, "body", base, (0.88, 0.92, 0.95, 0.5), "ps", 1050.0, "盒底", "承放标准件（凝胶面固定）", "透明 PS 盒底")
    lid = [Prim("box", [X, Y, t], [X / 2, 0, Zl - t / 2])] + [Prim("box", [X, t, Zl - t], [X / 2, s * (Y - t) / 2, (Zl - t) / 2]) for s in (-1, 1)] + \
          [Prim("box", [t, Y - 2 * t, Zl - t], [X / 2 + s * (X - t) / 2, 0, (Zl - t) / 2]) for s in (-1, 1)]
    _box_link(a, "lid", lid, (0.88, 0.92, 0.95, 0.5), "ps", 1050.0, "翻盖", "盖住标准件", "透明 PS 翻盖，铰链在 −X 边", parent="body",
              origin=(-X / 2, 0, Zb), joint={"type": "revolute", "axis": [0, -1, 0], "limits": [0.0, round(math.radians(170), 6)], "evidence": "assumption",
                                             "source_ids": ["consumable_assumption"], "note_zh": "正方向 = 绕 −X 边铰链向上翻开"})
    top = Zb + Zl
    fr = top_down_frames(top / 2, top, Y, yaw_free=False, contact_z=(0.0, top), note_zh="沿 Y 夹 89 mm 边：超出 Franka 张开宽度")
    fr["grasp"]["franka_ok"] = False
    fr["lid_tab"] = frame([X / 2, 0, Zb + Zl], "base_link", rot=[0, 0, 0, 1], note_zh="翻盖自由边（+X）：向上推开")
    _finish(a, "case", "reference_case", ("reference_case",), "Gel-Pak (Delphon)", "AD series hinged box AD-1135, 3.5 x 4.5 x 0.5 in", "AD-1135", fr,
            {"outer": {"size_m": [X, Y, top], "source_id": "gelpak_ad1135"}, "closure": {"type": "hinged_lid", "link": "lid", "joint": "lid_joint", "axis": [0, -1, 0],
                                                                                         "open_rad": round(math.radians(170), 6), "rule_zh": "推 lid_tab 翻开；翻盖不脱离盒底"},
             "franka_grasp_ok": False, "stands_upright": True,
             "contents_note_zh": "盒内标准件只记账（蓝宝石盘 / Ag、Cu 箔 / 石英控制片 / 假电解池）；取出放入仪器由宿主或宽口夹爪完成"},
            ["外形按 Gel-Pak AD-1135 目录；壁厚、盖高分配与铰链为假设。", "89 mm 短边超出 Franka 可抓宽度（franka_ok=false）。"])
    a.prop("body", "outer_size_m", [X, Y, top], "m", "外形 4.5×3.5×0.5 in", "documented", ["gelpak_ad1135"])
    return a


def pcr_plate() -> LabwareAsset:
    a = sbs.build_plate("biorad_96_wellplate_200ul_pcr", "biorad_hardshell_pcr_plate_96", "Bio-Rad Hard-Shell 96 孔 PCR 板（低型，有裙边，HSP9655/HSP9601）",
                        "ot_biorad_pcr", "pcr_plate", (0.95, 0.95, 0.95, 1.0), sbs.PP)
    a.sources.update(_src("cfx_flier", "ot_biorad_pcr"))
    a.labware["catalogue_no"] = "HSP9655 (white wells, CFX Opus flier p.2) / HSP9601 (Opentrons definition)"
    a.labware["maker"] = "Bio-Rad"
    a.labware["instrument_fit"] = {"instrument.biorad_cfxopus": {"slot": "sample_block", "slot_radius_m": 0.06, "item": "96-well block, 9 mm pitch"}}
    a.limitations_zh.append("几何来自 Opentrons 的 HSP9601 定义；CFX Opus 资料列出的 HSP9655 是同一 Hard-Shell 低型裙边板的白孔版本。")
    return a


def a4s_roll() -> LabwareAsset:
    src = ROOT / "assets" / "basic40_batch02" / "t5_automation" / "azenta_heat_sealer" / "meshes" / "film_roll.obj"
    mesh = trimesh.load(src, force="mesh")
    b = mesh.bounds
    mesh.apply_translation([-(b[0][0] + b[1][0]) / 2, -(b[0][1] + b[1][1]) / 2, -b[0][2]])
    W, D = float(b[1][0] - b[0][0]), float(b[1][2] - b[0][2])
    a = LabwareAsset("azenta_seal_roll_4ti0520", "labware.azenta_seal_roll_4ti0520", "Azenta a4S 封板膜卷（可剥离透明膜 4ti-0520 型，宽 80 mm）",
                     "Azenta peelable clear seal roll for the a4S (4ti-0520 type), 80 mm wide", "consumables", "labware.consumable.seal_roll", "passive",
                     description_zh="a4S 自动封膜机用的封板膜卷：几何直接取自 a4S 实例的 film_roll 部件（Ø126×80 mm）；规格符合手册（卷宽 75–85 mm，p.30；小卷支架 <140 mm，p.16）。",
                     sources=_src("a4s_manual", "a4s_asset", "consumable_assumption", "robot_access"))
    R = D / 2
    col = [Prim("cylinder", [R, W], [0, 0, R], [0.0, math.pi / 2, 0.0])]
    vol = math.pi * (R ** 2 - 0.038 ** 2) * W
    a.add(Link("body", "膜卷", "a4S 封板用的膜材", "PET/PP 复合膜卷绕在纸芯上（实例网格复用）", visual=[(mesh, (0.92, 0.94, 0.96, 0.8))], collision=col,
               mass_kg=vol * 1100.0 * 0.9, mass_evidence="assumption", mass_note_zh="膜卷体积（扣 Ø76 mm 纸芯）× 约 1000 kg/m3",
               material="pet_film", geometry_evidence="documented", geometry_note_zh="网格来自 azenta_heat_sealer/meshes/film_roll.obj"))
    fr = top_down_frames(R, D, D, yaw_free=False, contact_z=(R - 0.009, R + 0.009), note_zh="卷径 126 mm、宽 80 mm 都超出 Franka 张开宽度")
    fr["grasp"]["franka_ok"] = False
    _finish(a, "seal_roll", "seal_roll", ("a4s_seal_roll",), "Azenta (4titude)", "Clear peelable heat seal, roll format, for the a4S (seal profile 4ti-0520)",
            "4ti-0520 (seal profile in the a4S, manual p.41; roll catalogue variant not verified)", fr,
            {"outer": {"diameter_m": D, "width_m": W, "source_id": "a4s_asset"}, "franka_grasp_ok": False, "stands_upright": False, "reused_from": "instrument.azenta_heat_sealer:film_roll",
             "instrument_fit": {"instrument.azenta_heat_sealer": {"slot": "roll_support (small roll < 140 mm)", "roll_width_range_m": [0.075, 0.085]}}},
            ["几何复用 a4S 实例的 film_roll 网格；卷宽 80 mm、卷径 126 mm 在手册范围内（宽 75–85 mm、小卷 <140 mm）。",
             "膜型号按 a4S 内置封膜程序 4ti-0520（Peel Seal）；对应卷装货号未核实。", "Franka 无法抓取（直径与宽度都 >74 mm）：换卷是人工/宿主步骤。"])
    a.prop("body", "roll_diameter_m", D, "m", "卷径（实例网格）", "documented", ["a4s_asset"])
    a.prop("body", "roll_width_m", W, "m", "卷宽（实例网格；手册范围 75–85 mm）", "documented", ["a4s_manual"])
    return a


def hellma() -> LabwareAsset:
    return square_cuvette("hellma_100_10_40_quartz_cuvette", "Hellma 100-QS 石英比色皿（10 mm，3.5 mL，PTFE 盖，100-10-40）",
                          "Hellma macro cell 100-QS, 10 mm, 3500 uL, PTFE lid", "fused_silica", QUARTZ, 2200.0, 3.5e-6, ("cuvette_12p5", "cell_holder_12p5"),
                          "Hellma", "Macro cell type 100-QS, HP quartz, 10 mm, 3500 uL, PTFE lid", "100-10-40",
                          ("hellma_100_10_40", "consumable_assumption", "pp_hdpe_density", "robot_access"), "hellma_100_10_40", True,
                          ["外形 12.5×12.5×45 mm、内宽 9.5 mm、光程 10 mm 按目录；底厚 1.5 mm、盖尺寸为假设。",
                           "适配 12.5 mm 方形样品池架（UV-vis / 荧光 / CD）；装到具体仪器样品池由宿主或 Matterix 槽位决定。"],
                          "石英比色皿 Hellma 100-10-40：12.5 mm 方形标准外形，适配 JASCO FP-8350 / J-1500 / Hach DR6000 等 12.5 mm 池架。")


def brand_ps() -> LabwareAsset:
    return square_cuvette("brand_ps_macro_cuvette_759005", "BRAND PS 一次性比色皿（10 mm，2.5–4.5 mL，759005）", "BRAND macro PS cuvette 10 mm 2.5-4.5 mL",
                          "ps", PS_CLEAR, 1050.0, 4.5e-6, ("cuvette_12p5", "cell_holder_12p5"), "BRAND", "Macro cuvette, PS, 10 mm, 2.5-4.5 mL, 340-900 nm",
                          "759005", ("brand_759005", "consumable_assumption", "pp_hdpe_density", "robot_access"), "brand_759005", False,
                          ["外形与光程按目录；内腔 10×10.5 mm（壁厚约 1 mm）与底厚 1 mm 为假设（使满口 ≥ 目录上限 4.5 mL）。", "PS 只用于 340–900 nm（可见区）。"],
                          "一次性可见光比色皿 BRAND 759005：12.5×12.5×45 mm、光程 10 mm、2.5–4.5 mL、100 个/包。", inner=(0.0100, 0.0105), tb=0.0010)


BUILDERS = {
    "norell_nmr_tube_5mm_7in": nmr_tube,
    "hach_tl2300_sample_cell_25mm": tl2300_cell,
    "sciex_universal_vial_2ml": sciex_vial,
    "falcon_352052_tube_12x75": falcon_352052,
    "elementar_tin_capsule_11x8": elementar_capsule,
    "ta_tzero_pan_lid": tzero_pan,
    "biorad_tgx_gel_4_20_10well": gel_cassette,
    "cytiva_series_s_sensor_chip_cm5": sensor_chip,
    "sigma_ir_polystyrene_card_z804290": ir_card,
    "rigaku_glass_holder_0p2mm": xrd_holder,
    "gelpak_ad1135_case": gelpak_case,
    "biorad_hardshell_pcr_plate_96": pcr_plate,
    "azenta_seal_roll_4ti0520": a4s_roll,
    "hellma_100_10_40_quartz_cuvette": hellma,
    "brand_ps_macro_cuvette_759005": brand_ps,
}
