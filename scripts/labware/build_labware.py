"""Build the labware / storage-furniture assets and assets/labware/catalog.json.

    python scripts/labware/build_labware.py                 # build everything, verify, write catalog.json
    python scripts/labware/build_labware.py --only duran_gl45_500ml_clear --no-verify
    python scripts/labware/build_labware.py --catalog-only  # rewrite catalog.json from the existing manifests

catalog.json lists
  * "types": every container / consumable / tool type: our new assets (source chemistry_sim) and the reused LabUtopia /
    Matterix assets (source labutopia / matterix, MIT, pointing at the existing USD) with frames, interior, capacity, mass;
  * "furniture": storage furniture with its storage slots and doors;
  * "places": the default-lab inventory's storage places (chemlab/chemlab/inventory.py) -> furniture type + position names;
  * "inventory_type_map": the inventory's provisional container type ids -> catalog types.
The Isaac Sim storage scene reads this file; chemlab reads it for capacities and storage hints.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from labware_kit import FRANKA_MAX_GRASP_M, HAND_ABOVE_TCP_M, LABWARE_DIR, ROOT  # noqa: E402
from sources import SOURCES  # noqa: E402

MATTERIX_DATA = ROOT.parent / "Matterix" / "source" / "matterix_assets" / "data"

# the inventory's provisional container type ids (chemlab/chemlab/inventory.py PROVISIONAL_TYPES, 2026-10-22) -> catalog types.
# status: exact = the asset is that item; packaging_of = the unit item is built, the box/bag is not modelled (count = units per pack);
# substitute = a built asset stands in with a stated difference; not_built = no asset yet (reason).
INVENTORY_TYPE_MAP = {
    "bottle_gl45_1000ml_clear": {"type": "labware.duran_gl45_1000ml_clear", "status": "exact"},
    "bottle_gl45_500ml_clear": {"type": "labware.duran_gl45_500ml_clear", "status": "exact"},
    "bottle_gl45_2500ml_amber": {"type": "labware.winchester_2500ml_amber", "status": "substitute",
                                 "note": "2.5 L amber Winchester (38 mm screw neck); DURAN makes no 2.5 L GL45 bottle"},
    "bottle_hdpe_500ml": {"type": "labware.nalgene_nm_hdpe_500ml", "status": "exact"},
    "bottle_hdpe_1000ml": {"type": "labware.nalgene_nm_hdpe_1000ml", "status": "exact"},
    "jar_hdpe_500g": {"type": "labware.nalgene_wm_hdpe_500ml", "status": "exact"},
    "bottle_glass_crm_unit": {"type": "labware.duran_gl45_100ml_amber", "status": "substitute",
                              "note": "CRM unit bottles are small amber glass bottles of the issuing body; the 100 mL amber GL45 bottle stands in"},
    "plate_96_flat_ps": {"type": "labware.corning_96_wellplate_360ul_flat", "status": "exact", "carrier": "labware.sbs_plate_carrier"},
    "box_microplates_96_flat_ps": {"type": "labware.corning_96_wellplate_360ul_flat", "status": "packaging_of", "count": 25,
                                   "note": "sleeve of 25 plates; sleeve not modelled"},
    "tiprack_flex_1000ul": {"type": "labware.opentrons_flex_tiprack_1000ul", "status": "exact", "carrier": "labware.sbs_plate_carrier"},
    "tiprack_flex_200ul": {"type": "labware.opentrons_flex_tiprack_200ul", "status": "exact", "carrier": "labware.sbs_plate_carrier"},
    "bag_tubes_1p5ml": {"type": "labware.eppendorf_safelock_1p5ml", "status": "packaging_of", "count": 500, "note": "bag of 500 tubes; bag not modelled"},
    "bag_tubes_conical_15ml": {"type": "labware.falcon_tube_15ml", "status": "packaging_of", "count": 50, "note": "bag of 50; bag not modelled"},
    "bag_tubes_conical_50ml": {"type": "labware.falcon_tube_50ml", "status": "packaging_of", "count": 25, "note": "bag of 25; bag not modelled"},
    "box_vials_2ml_screw": {"type": "labware.agilent_vial_2ml_screw", "status": "packaging_of", "count": 100, "note": "Agilent 5182-0715 is sold 100/pk"},
    "box_vials_2ml_crimp": {"type": "labware.agilent_vial_2ml_screw", "status": "substitute", "count": 100,
                            "note": "crimp-top vial has the same 12 x 32 mm body; crimp cap not modelled (screw vial stands in)"},
    "box_vials_20ml_headspace": {"type": "labware.agilent_hs_vial_20ml", "status": "packaging_of", "count": 100, "note": "Agilent 5188-2753 is sold 100/pk"},
    "cylinder_50l_200bar": {"type": "labware.gas_cylinder_50l_200bar", "status": "exact"},
    "cylinder_10l_200bar": {"type": "labware.gas_cylinder_10l_200bar", "status": "exact"},
    "cylinder_10l_co2_liquefied": {"type": "labware.gas_cylinder_10l_200bar", "status": "substitute",
                                   "note": "same 10 L steel shell; liquefied CO2 at ~57 bar (20 C) is a ledger property, no dip tube modelled"},
    "cylinder_10l_acetylene": {"type": "labware.gas_cylinder_10l_200bar", "status": "substitute",
                               "note": "acetylene cylinders hold a porous mass + acetone at ~19 bar and are wider; shape of the 10 L cylinder stands in until a sourced model is built"},
    "vial_bead_standard": {"type": "labware.agilent_vial_2ml_screw", "status": "substitute", "note": "small reagent vial stands in"},
    "vial_protein_ladder": {"type": "labware.eppendorf_safelock_1p5ml", "status": "substitute", "note": "ladders ship in 0.5-1.5 mL tubes"},
    "vial_crm_metal_shot": {"type": "labware.agilent_hs_vial_20ml", "status": "substitute", "note": "metal-shot CRM unit (small glass vial) stands in"},
    "ampoule_box_crm": {"type": "labware.hach_tl2300_sample_cell_25mm", "status": "packaging_of",
                        "note": "box of sealed standards; the unit depends on the record (per_record_units); the box itself is not modelled",
                        "per_record_units": {
                            "turbidity_stablcal_kit": {"type": "labware.hach_tl2300_sample_cell_25mm", "status": "exact", "count": 6,
                                                       "note": "StablCal standards are sealed 95 x 25 mm cells (TL23 data sheet p.3)"},
                            "aw_standard_0_760": {"type": "labware.agilent_hs_vial_20ml", "status": "substitute", "count": 5,
                                                  "note": "METER verification standards are small sealed vials emptied into a disposable sample cup"},
                            "aw_standard_0_250": {"type": "labware.agilent_hs_vial_20ml", "status": "substitute", "count": 5,
                                                  "note": "as aw_standard_0_760"},
                            "osmolality_cal_400": {"type": "labware.agilent_vial_2ml_screw", "status": "substitute", "count": 12,
                                                   "note": "Knauer calibration solution: 12 small vials per box (K7400S brochure p.11)"}}},
    "xrd_holder_zero_background": {"type": "labware.rigaku_glass_holder_0p2mm", "status": "substitute",
                                   "note": "glass 0.2 mm well plate for the MiniFlex standard stage; a zero-background Si plate has the same role, its outer size is not published"},
    "ir_film_card": {"type": "labware.sigma_ir_polystyrene_card_z804290", "status": "exact"},
    "case_reference_plate": {"type": "labware.gelpak_ad1135_case", "status": "exact",
                             "note": "Gel-Pak AD-1135 hinged case; the reference item inside (sapphire disc, foils, quartz plate, dummy cell) is ledger content"},
    "box_pcr_plates_96_skirted": {"type": "labware.biorad_hardshell_pcr_plate_96", "status": "packaging_of", "count": 50, "note": "HSP9655 pkg of 50 (CFX Opus flier p.2)"},
    "bag_tubes_12x75mm_ps": {"type": "labware.falcon_352052_tube_12x75", "status": "packaging_of", "count": 125, "note": "125 per bag (FACSAria guide p.286)"},
    "box_dsc_pans_tzero": {"type": "labware.ta_tzero_pan_lid", "status": "packaging_of", "count": 100, "note": "pkg of 100 pans + 100 lids (DSC guide p.64)"},
    "box_tin_capsules": {"type": "labware.elementar_tin_capsule_11x8", "status": "packaging_of", "count": 100, "note": "100 pcs package (Elementar 200006458)"},
    "box_cuvettes_ps_10mm": {"type": "labware.brand_ps_macro_cuvette_759005", "status": "packaging_of", "count": 100, "note": "pack of 100"},
    "cuvette_quartz_10mm_case": {"type": "labware.hellma_100_10_40_quartz_cuvette", "status": "packaging_of", "count": 4, "note": "cuvette case not modelled"},
    "box_nmr_tubes_5mm": {"type": "labware.norell_nmr_tube_5mm_7in", "status": "packaging_of", "count": 25},
    "box_gels_precast_10well": {"type": "labware.biorad_tgx_gel_4_20_10well", "status": "packaging_of", "count": 10, "note": "box of 10 gels (4561094)"},
    "roll_heat_seal_film": {"type": "labware.azenta_seal_roll_4ti0520", "status": "exact", "note": "mesh reused from the a4S asset's film_roll part"},
    "box_sensor_chips_spr": {"type": "labware.cytiva_series_s_sensor_chip_cm5", "status": "packaging_of", "count": 3, "note": "BR100530 = pack of 3"},
    "kit_ce_reagents": {"type": "labware.sciex_universal_vial_2ml", "status": "packaging_of",
                        "note": "SDS-MW kit 390953 (box not modelled); its liquids are run from PA 800 universal vials (A62251 + caps A62250)"},
    "quantos_dosing_head": {"type": "labware.mt_quantos_qh016_bnmw", "status": "exact",
                            "note": "QH016-BNMW 16 mL powder head (Quantos manual p.102/p.107); geometry from the mt_quantos_q2 asset's dosing_head part"},
    "quantos_head_container": {"type": "labware.mt_quantos_head_container", "status": "exact",
                               "note": "plastic container supplied with each head (manual p.27/p.94); holds one head upright on the bench shelf"},
    "quantos_target_vial": {"type": "labware.agilent_hs_vial_20ml", "status": "exact",
                            "note": "fits the Quantos sample-container limits (manual p.103); the instrument asset's sample_container part is an authored placeholder"},
    "vial_rack_20ml": {"type": "labware.mn_702503_vial_rack_23mm", "status": "exact",
                       "note": "MACHEREY-NAGEL 702503 36-position rack for 23.1 mm headspace vials (20 mL Agilent vials); robot slots A01-A12 and C01-C12 "
                               "(fingers close along Y), row B stays empty; needs >= 0.25 m free above the vial tops (bench top)"},
    "in_instrument_item": {"type": None, "status": "not_applicable", "note": "items already inside instruments are not spawned on shelves"},
}


def registries() -> dict:
    regs = {}
    import containers
    regs.update(containers.ALL_CONTAINER_BUILDERS)
    for mod in ("sbs", "tools", "furniture", "consumables", "quantos", "racks"):
        try:
            m = __import__(mod)
            regs.update(getattr(m, "BUILDERS"))
        except ModuleNotFoundError:
            pass
    return regs


def labutopia_types() -> dict:
    """Reused assets: LabUtopia glassware / props (MIT) and the Matterix / Chemistry-Sim accessories already in Matterix."""
    out = {}
    idx_p = MATTERIX_DATA / "labutopia" / "labutopia_index.json"
    idx = json.loads(idx_p.read_text(encoding="utf-8")) if idx_p.exists() else {}
    nominal = {"beaker_50ml": 50, "beaker_100ml": 100, "beaker_150ml": 150, "beaker_250ml": 250, "beaker_600ml": 600, "beaker_wide_500ml": 500,
               "beaker_wide_1000ml": 1000, "beaker_opaque_100ml": 100, "erlenmeyer_100ml": 100, "erlenmeyer_250ml": 250, "erlenmeyer_500ml": 500,
               "erlenmeyer_1000ml": 1000, "erlenmeyer_wide_1000ml": 1000, "graduated_cylinder_500ml": 500}
    for name, e in idx.items():
        if e.get("category") not in ("labware", "prop", "appliance"):
            continue
        sx, sy, h = e["size_m"]
        entry = {"source": "labutopia", "licence": e.get("license", "MIT (LabUtopia)"), "reused": True,
                 "usd": f"Matterix:source/matterix_assets/data/{e['usd']}", "name_en": name.replace("_", " "), "note_zh": e.get("note_zh", ""),
                 "size_m": e["size_m"], "empty_mass_kg": e.get("mass_kg"), "mass_evidence": "LabUtopia index value (not weighed)",
                 "frames_index": e.get("frames"), "grasp_height_m": e.get("grasp_height_m"), "category": e.get("category"),
                 "source_detail": e.get("source")}
        if name in nominal:
            r = min(sx, sy) / 2
            wall = 0.0025 if h > 0.06 else 0.0015
            entry.update({"family": "open_vessel", "labware_class": name.split("_")[0] if not name.startswith("graduated") else "cylinder",
                          "nominal_capacity_m3": nominal[name] * 1e-6, "nominal_capacity_evidence": "documented (name of the LabUtopia asset)",
                          "interior": {"shape": "cylinder_estimate", "radius_m": round(max(r - wall, 0.004), 5), "floor_m": 0.004, "height_m": round(h, 5),
                                       "note_zh": "与 Matterix chemsim_slots.labware_spec 相同的估计（外径 − 壁厚 2.5/1.5 mm，底 4 mm）；锥形瓶/量筒的实际内腔不是圆柱"},
                          "franka_grasp_ok": bool(min(sx, sy) <= FRANKA_MAX_GRASP_M) or name.startswith("erlenmeyer"),
                          "closure": None, "storage_class_hint": "labware (empty glassware: consumables cabinet / bench)"})
        out[f"labutopia.{name}"] = entry
    geo_p = MATTERIX_DATA / "chemsim" / "geometry_index.json"
    geo = json.loads(geo_p.read_text(encoding="utf-8")) if geo_p.exists() else {}
    for tid in ("labware.gc_sample_vial", "labware.gc_syringe"):
        if tid in geo:
            g = geo[tid]
            out[f"matterix.{tid.split('.', 1)[1]}"] = {
                "source": "matterix (Chemistry-Sim accessory)", "licence": "project", "reused": True,
                "usd": f"Matterix:source/matterix_assets/data/{g['usd']}", "size_m": g["size_m"], "empty_mass_kg": g.get("mass_kg"),
                "frames_index": g.get("frames"), "note_zh": "Chemistry-Sim GC 附件（已在 Matterix 中转换）；origin 在包围盒中心（base_offset = −bbox_min z）",
                "family": "vial" if "vial" in tid else "syringe"}
    for name, usd in (("beaker500ml", "labware/beaker500ml/beaker-500ml-inst.usda"),
                      ("table_thorlabs_75x90", "infrastructure/tables/table-thorlabs-75x90/table-inst.usda")):
        if (MATTERIX_DATA / usd).exists():
            out[f"matterix.{name}"] = {"source": "matterix", "licence": "Matterix (BSD-3-Clause)", "reused": True,
                                       "usd": f"Matterix:source/matterix_assets/data/{usd}",
                                       "family": "open_vessel" if "beaker" in name else "furniture",
                                       "nominal_capacity_m3": 5e-4 if "beaker" in name else None,
                                       "note_zh": "Matterix 自带资产（matterix_assets/labware/beakers.py / infrastructure）"}
    return out


def manifest_entry(d: Path) -> dict:
    man = json.loads((d / "geometry_manifest.json").read_text(encoding="utf-8"))
    lw = man.get("labware") or {}
    rel = d.relative_to(ROOT).as_posix()
    srcs = [{"source_id": s["source_id"], "kind": s["kind"], "reference": s["reference"], "locator": s["locator"],
             "licence": s.get("licence") or SOURCES.get(s["source_id"], {}).get("licence")} for s in man.get("sources", [])]
    arts = {k: (f"{rel}/{v}" if v else None) for k, v in man["artifacts"].items() if k != "meshes_dir"}
    e = {"source": "chemistry_sim", "licence": "project asset (dimensions: cited facts; Opentrons-derived data Apache-2.0)", "reused": False,
         "dir": rel, "contract": f"{rel}/asset.contract.json", "manifest": f"{rel}/geometry_manifest.json", **arts,
         "name_zh": man["name_zh"], "name_en": man.get("name_en"), "size_m": man["overall_dimensions_m"], "total_mass_kg": man["total_mass_kg"],
         "links": [c["id"] for c in man["components"]],
         "joints": {k: {kk: v[kk] for kk in v if kk in ("type", "axis", "limits_rad", "limits_m", "note_zh")} for k, v in man["joint_definitions"].items()},
         "sources": srcs}
    e.update({k: v for k, v in lw.items() if k not in ("volume_table_m_ml",)})
    if man.get("ports"):
        e["slots"] = man["ports"]
    return e


def write_catalog() -> dict:
    types, furniture = {}, {}
    for m in sorted(LABWARE_DIR.glob("*/*/geometry_manifest.json")):
        d = m.parent
        e = manifest_entry(d)
        tid = json.loads(m.read_text(encoding="utf-8"))["asset_type_id"]
        (furniture if d.parent.name == "furniture" else types)[tid] = e
    types.update(labutopia_types())
    places = {}
    try:
        import furniture as F
        places = F.PLACE_MAP
    except ModuleNotFoundError:
        pass
    cat = {
        "schema_version": "1.0.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "scripts/labware/build_labware.py",
        "description_zh": "实验室容器、耗材、器具与存放家具目录：新建资产（assets/labware/，几何+合同+抓取坐标+内腔）与复用资产（LabUtopia MIT / Matterix）。"
                          "chemlab 的 configs/inventory_default.json 用 type id 引用这里的类型，用 place id 引用 places。",
        "conventions": {"units": "SI (m, kg, m3, rad)", "up_axis": "+Z", "front": "-Y", "origin": "footprint centre, base at z = 0",
                        "quaternion": "(x, y, z, w)", "frames": "TCP target positions in the asset frame (closed state); rot (1,0,0,0) = top-down",
                        "frame_names": ["grasp", "pre_grasp", "post_grasp", "place", "pre_place", "cap_grasp", "cap_seat", "pour_lip", "sbs_grasp", "handle_<door>"]},
        "robot_constraints": {"gripper": "Franka Hand", "max_open_m": 0.08, "max_grasp_width_m": FRANKA_MAX_GRASP_M, "hand_above_tcp_m": HAND_ABOVE_TCP_M,
                              "approach": "top-down", "source": "robot set-up constraints, 2026-10-22"},
        "types": types, "furniture": furniture, "places": places, "inventory_type_map": INVENTORY_TYPE_MAP,
        "sources_licences": {k: v.get("licence") for k, v in SOURCES.items()},
    }
    (LABWARE_DIR / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    return cat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--catalog-only", action="store_true")
    a = ap.parse_args()
    if not a.catalog_only:
        for slug, fn in registries().items():
            if a.only and slug not in a.only:
                continue
            asset = fn()
            asset.export()
            asset.write_contract()
            print(f"built {asset.asset_type_id:55s} -> {asset.rel_dir}", flush=True)
    cat = write_catalog()
    print(f"catalog: {len(cat['types'])} types, {len(cat['furniture'])} furniture, {len(cat['places'])} places")
    if not a.no_verify:
        import verify_labware as V
        reports = [V.verify_asset(d) for d in V.asset_dirs(a.only)]
        bad = [r for r in reports if r["status"] != "passed"]
        for r in reports:
            failed = [c["id"] for c in r["checks"] if c["status"] != "passed"]
            print(f"{r['status']:6s} {r['asset']:55s} {len(r['checks']):2d} checks" + (f" FAILED {failed}" if failed else ""))
        if not a.only:
            (LABWARE_DIR / "verification_report.json").write_text(json.dumps(
                {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "method": "scripts/labware/verify_labware.py",
                 "assets": reports}, ensure_ascii=False, indent=1), encoding="utf-8")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
