"""Labware, consumable and storage-furniture assets (assets/labware/, built by scripts/labware/build_labware.py).

Per asset (offline, no Isaac): files and URDF meshes exist, every link has collision (visual meshes watertight where they are
closed shells), URDF forward kinematics stays above the floor at every joint limit, frames lie inside the asset, the Franka
grasp-width flag is consistent and the hand clears the top, interior volume >= nominal capacity, screw caps detach and reseat
(collision check + closure rule), lids lift off, every door has hinge / limits / handle / mass, storage slots lie inside the
furniture with the declared pitch and headroom, and the contract validates with --check-files.
Catalog: every type / furniture entry points at existing files, the inventory's provisional types map to catalog types, the
storage places map to furniture slots with the inventory's position names, reused LabUtopia types point at existing USDs.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "labware"))

import verify_labware as V  # noqa: E402
from closure import Closure, HingedClosure  # noqa: E402

LABWARE = ROOT / "assets" / "labware"
CATALOG = json.loads((LABWARE / "catalog.json").read_text(encoding="utf-8"))
MATTERIX_DATA = ROOT.parent / "Matterix" / "source" / "matterix_assets" / "data"


class TestEachAsset(unittest.TestCase):
    def test_all_assets_pass_offline_checks(self):
        dirs = list(V.asset_dirs())
        self.assertGreaterEqual(len(dirs), 35)
        for d in dirs:
            with self.subTest(asset=d.name):
                r = V.verify_asset(d.resolve())
                failed = {c["id"]: c["data"] for c in r["checks"] if c["status"] != "passed"}
                self.assertEqual(failed, {}, f"{d.name}: {failed}")


class TestClosureRule(unittest.TestCase):
    def test_screw_cap_sequence(self):
        c = Closure(detach_after_rad=7.854)
        self.assertEqual(c.twist(3.0, cap_grasped=False), "closed")      # touching without a grasp does nothing
        self.assertEqual(c.twist(3.0, cap_grasped=True), "loosened")
        self.assertEqual(c.twist(-3.0, cap_grasped=True), "closed")       # turning back re-tightens
        self.assertEqual(c.twist(8.0, cap_grasped=True), "detached")
        self.assertTrue(c.is_open)
        self.assertEqual(c.twist(-8.0, cap_grasped=True), "detached")     # not seated: a free cap stays free
        c.seat(True)
        self.assertEqual(c.twist(-4.0, cap_grasped=True), "loosened")
        self.assertEqual(c.twist(-4.0, cap_grasped=True), "closed")

    def test_hinged(self):
        h = HingedClosure(open_rad=1.5708)
        self.assertEqual(h.state, "closed")
        self.assertEqual(h.set_angle(1.0), "open")
        self.assertEqual(h.set_angle(0.2), "closed")


class TestCatalog(unittest.TestCase):
    def test_built_entries_point_at_files(self):
        for section in ("types", "furniture"):
            for tid, e in CATALOG[section].items():
                if e.get("source") != "chemistry_sim":
                    continue
                with self.subTest(type=tid):
                    for key in ("contract", "manifest", "model_urdf", "model_glb"):
                        self.assertTrue((ROOT / e[key]).exists(), f"{tid}: {key} {e[key]}")
                    self.assertIn("frames", e)

    def test_containers_have_liquid_interior_and_cap_frames(self):
        for tid, e in CATALOG["types"].items():
            if e.get("source") != "chemistry_sim" or (e.get("closure") or {}).get("type") != "screw_cap":
                continue
            with self.subTest(type=tid):
                fr = e["frames"]
                for k in ("grasp", "pre_grasp", "place", "cap_grasp", "cap_seat"):
                    self.assertIn(k, fr)
                it = e["interior"]
                self.assertGreater(it["radius_m"], 0)
                self.assertLess(it["floor_m"], it["height_m"])
                self.assertGreaterEqual(e["brim_capacity_m3"], e["nominal_capacity_m3"])
                self.assertTrue(e["maker"] and e["catalogue_no"], "real model required")

    def test_every_new_type_cites_a_real_model_or_says_why_not(self):
        for section in ("types", "furniture"):
            for tid, e in CATALOG[section].items():
                if e.get("source") != "chemistry_sim":
                    continue
                with self.subTest(type=tid):
                    self.assertTrue(e.get("maker"), f"{tid}: maker missing")
                    self.assertTrue(e["sources"], f"{tid}: no sources")

    def test_doors_for_mat_b(self):
        doors = {tid: e["doors"] for tid, e in CATALOG["furniture"].items() if e.get("doors")}
        self.assertIn("furniture.belart_secador_2_vertical", doors)
        for tid, dd in doors.items():
            for name, d in dd.items():
                with self.subTest(door=f"{tid}.{name}"):
                    self.assertEqual(len(d["hinge_axis"]), 3)
                    self.assertEqual(len(d["hinge_origin_m"]), 3)
                    self.assertLess(d["limits"][0], d["limits"][1])
                    self.assertGreater(d["mass_kg"], 0)
                    self.assertEqual(d["handle"]["approach_axis"], [0.0, 1.0, 0.0])

    def test_bench_shelf_meets_robot_access(self):
        e = CATALOG["furniture"]["furniture.trippnt_step_shelf_2tier"]
        slots = {s["name"]: s for s in e["slots"]}
        self.assertEqual(sorted(slots), [f"T{t}-{k:02d}" for t in (1, 2) for k in range(1, 6)])
        for t in (1, 2):
            xs = sorted(slots[f"T{t}-{k:02d}"]["position_m"][0] for k in range(1, 6))
            self.assertTrue(all(b - a >= 0.11 - 1e-9 for a, b in zip(xs, xs[1:])))
        # upper tier behind (larger y) and higher than the lower tier; nothing above the slots
        self.assertGreater(slots["T2-01"]["position_m"][1], slots["T1-01"]["position_m"][1] + 0.1)
        self.assertGreater(slots["T2-01"]["position_m"][2], slots["T1-01"]["position_m"][2])
        self.assertTrue(all(s["headroom_m"] >= 0.48 for s in slots.values()))

    def test_places_map_to_furniture_slots(self):
        for place, m in CATALOG["places"].items():
            with self.subTest(place=place):
                f = CATALOG["furniture"][m["furniture"]]
                names = [s["name"] for s in f["slots"] if s.get("place_id") == place]
                self.assertTrue(names, f"{place}: no slots")
                self.assertTrue(all(n.startswith(m["slot_prefix"]) for n in names))

    def test_inventory_types_resolve(self):
        km = CATALOG["inventory_type_map"]
        for k, v in km.items():
            with self.subTest(inventory_type=k):
                self.assertIn(v["status"], ("exact", "packaging_of", "substitute", "not_built", "not_applicable"))
                if v["type"]:
                    self.assertIn(v["type"], CATALOG["types"])
                else:
                    self.assertIn(v["status"], ("not_built", "not_applicable"))
                    self.assertTrue(v.get("note"))
                for rec, u in (v.get("per_record_units") or {}).items():
                    self.assertIn(u["type"], CATALOG["types"], f"{k}/{rec}")
        self.assertEqual([k for k, v in km.items() if v["status"] == "not_built"], [])

    def test_reused_types_point_at_existing_usd(self):
        reused = {k: v for k, v in CATALOG["types"].items() if v.get("reused")}
        self.assertGreaterEqual(len(reused), 18)
        if not all(e.get("usd") for e in reused.values()):           # the public catalog names the external source instead
            self.assertTrue(all(e.get("external") and e.get("files_included") is False for e in reused.values()))
            self.skipTest("reused types are external (their 3D files are not in this tree)")
        if not MATTERIX_DATA.exists():
            self.skipTest("Matterix checkout not next to Chemistry-Sim")
        for tid, e in reused.items():
            with self.subTest(type=tid):
                rel = e["usd"].split("Matterix:source/matterix_assets/data/")[1]
                self.assertTrue((MATTERIX_DATA / rel).exists(), e["usd"])

    def test_quantos_head_and_vial_fit(self):
        head = CATALOG["types"]["labware.mt_quantos_qh016_bnmw"]
        fit = head["instrument_fit"]["instrument.mt_quantos_q2_xpe206dr"]
        self.assertTrue(fit["ok"] and fit["head_slot_port_inside_head"] and fit["outlet_above_container_seat_xy"])
        self.assertTrue(fit["collision_free_against_instrument_proxies"] and fit["insertion_path_clear"])   # open support (instrument 1.2.2)
        self.assertEqual(head["catalogue_no"], "11141503")
        self.assertAlmostEqual(head["nominal_capacity_m3"], 1.6e-5)
        self.assertTrue(head["frames"]["grasp"]["franka_ok"])
        vial = CATALOG["types"]["labware.agilent_hs_vial_20ml"]["instrument_fit"]["instrument.mt_quantos_q2_xpe206dr"]
        self.assertTrue(vial["ok"])
        self.assertGreaterEqual(vial["vial_opening_m"], vial["limits"]["min_opening_m"])
        holder = CATALOG["types"]["labware.mt_quantos_head_container"]
        self.assertEqual([p["name"] for p in holder["slots"]], ["head"])
        self.assertEqual(CATALOG["inventory_type_map"]["quantos_dosing_head"]["type"], "labware.mt_quantos_qh016_bnmw")

    def test_vial_rack_20ml(self):
        # MN 702503 bench rack for the 20 mL headspace vials (2026-10-23): real model, robot slots, finger clearance
        rack = CATALOG["types"]["labware.mn_702503_vial_rack_23mm"]
        self.assertEqual(CATALOG["inventory_type_map"]["vial_rack_20ml"]["type"], "labware.mn_702503_vial_rack_23mm")
        self.assertEqual((rack["catalogue_no"], rack["positions_total"]), ("702503", 36))
        self.assertEqual([round(v, 4) for v in rack["size_m"]], [0.323, 0.091, 0.03])
        names = [s["name"] for s in rack["slots"]]
        self.assertEqual(names, [f"{r}{i:02d}" for r in "AC" for i in range(1, 13)])
        vial = CATALOG["types"]["labware.agilent_hs_vial_20ml"]["outer"]
        ra = rack["robot_access"]
        self.assertLess(vial["diameter_m"], rack["grid"]["hole_diameter_m"])
        self.assertLessEqual(ra["approach_open_width_m"], ra["max_grasp_width_m"])
        self.assertGreater(ra["approach_open_width_m"], vial["diameter_m"])
        self.assertGreaterEqual(ra["min_finger_gap_rows_A_C_m"], 0.002)          # fingers clear every occupied neighbour
        self.assertLess(ra["min_finger_gap_if_row_B_filled_m"], 0.0)             # why row B stays empty
        self.assertGreater(ra["hand_bottom_above_vial_tops_m"], 0.0)
        self.assertGreater(ra["fingertip_end_above_deck_m"], 0.005)
        for s in rack["slots"]:
            with self.subTest(slot=s["name"]):
                self.assertEqual(s["kind"], "storage")
                self.assertGreaterEqual(s["required_free_above_vial_top_m"], 0.25)
                self.assertAlmostEqual(s["vial_grasp_tcp_m"][2] - s["position_m"][2], 0.0445, places=4)
                self.assertIn(f"slot_{s['name']}", rack["frames"])

    def test_sbs_wells_match_opentrons_definitions(self):
        for tid, name in (("labware.corning_96_wellplate_360ul_flat", "corning_96_wellplate_360ul_flat"),
                          ("labware.opentrons_flex_tiprack_1000ul", "opentrons_flex_96_tiprack_1000ul"),
                          ("labware.nest_12_reservoir_15ml", "nest_12_reservoir_15ml")):
            d = json.loads((ROOT / "references" / "labware" / "opentrons" / f"{name}.json").read_text(encoding="utf-8"))
            e = CATALOG["types"][tid]
            X, Y = d["dimensions"]["xDimension"] / 1000, d["dimensions"]["yDimension"] / 1000
            for w, wd in d["wells"].items():
                with self.subTest(type=tid, well=w):
                    p = e["wells"][w]["pos_m"]
                    self.assertAlmostEqual(p[0], wd["x"] / 1000 - X / 2, places=5)
                    self.assertAlmostEqual(p[1], wd["y"] / 1000 - Y / 2, places=5)
                    self.assertAlmostEqual(e["wells"][w]["volume_m3"], wd["totalLiquidVolume"] * 1e-9, places=12)
            self.assertFalse(e["frames"]["sbs_grasp"]["franka_ok"])


if __name__ == "__main__":
    unittest.main()
