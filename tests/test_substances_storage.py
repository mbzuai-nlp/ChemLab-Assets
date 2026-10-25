"""substances.json schema 1.1.0: storage class, GHS hazard data and liquid boiling point / viscosity (with PubChem sources),
plus the substances added for the default-lab inventory.  The file must equal what scripts/substances/apply_storage_hazard.py
derives from the cached PubChem records (references/substance_safety_raw/), and the 1.0.0 fields must be untouched in form.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "substances"))

import apply_storage_hazard as A  # noqa: E402
from new_substances import NEW_MIXTURES, NEW_PURE  # noqa: E402

DATA = json.loads((ROOT / "assets" / "reactions" / "substances.json").read_text(encoding="utf-8"))
SUBS = DATA["substances"]
CLASSES = {"flammable", "acid", "base", "oxidizer", "toxic", "cold", "compressed_gas", "general"}


class TestSchema(unittest.TestCase):
    def test_version_and_classes(self):
        self.assertEqual(DATA["schema_version"], "1.1.0")
        self.assertEqual(set(DATA["storage_classes"]), CLASSES)

    def test_legacy_fields_kept(self):
        for sid, r in SUBS.items():
            with self.subTest(sid=sid):
                for k in ("name_zh", "formula", "molar_mass_kg_mol", "phase", "density_kg_m3", "fid_visible", "evidence", "source"):
                    self.assertIn(k, r)
                if r.get("kind") != "mixture":
                    self.assertIsInstance(r["molar_mass_kg_mol"], float)

    def test_every_substance_has_storage_and_ghs(self):
        for sid, r in SUBS.items():
            with self.subTest(sid=sid):
                self.assertIn(r["storage_class"], CLASSES)
                self.assertEqual(r["storage"]["class"], r["storage_class"])
                self.assertTrue(r["storage"]["rule"])
                g = r["ghs"]
                if g["hazard_codes"]:
                    self.assertIn(g["basis"], ("eu_harmonised_clp_annex_vi", "echa_cl_notifications_ge10pct", "other_authority"))
                    self.assertTrue(g["source"]["cid"] and g["source"]["url"].startswith("https://pubchem.ncbi.nlm.nih.gov/compound/"))
                else:
                    self.assertTrue(g.get("note") or g.get("not_classified") or g.get("basis"))

    def test_liquids_have_bp_and_viscosity_or_a_reason(self):
        liquids = [s for s, r in SUBS.items() if r["phase"] == "liquid"]
        self.assertGreaterEqual(len(liquids), 50)
        for sid in liquids:
            r = SUBS[sid]
            with self.subTest(sid=sid):
                if r.get("boiling_point_K") is not None:
                    self.assertTrue(200 < r["boiling_point_K"] < 800)
                    self.assertTrue(r["boiling_point_source"]["cid"])
                else:
                    self.assertTrue(r.get("boiling_point_note"))
                if r.get("viscosity_Pa_s") is not None:
                    self.assertTrue(1e-4 < r["viscosity_Pa_s"] < 10)
                    self.assertTrue(r["viscosity_source"]["cid"])
                else:
                    self.assertTrue(r.get("viscosity_note"))
        n_bp = sum(1 for s in liquids if SUBS[s].get("boiling_point_K"))
        self.assertGreaterEqual(n_bp, 45)

    def test_known_values(self):
        e = SUBS["ethanol"]
        self.assertEqual(e["storage_class"], "flammable")
        self.assertIn("H225", e["ghs"]["hazard_codes"])
        self.assertAlmostEqual(e["boiling_point_K"], 351.4, delta=0.3)          # CRC 78.24 C
        self.assertAlmostEqual(e["viscosity_Pa_s"], 1.074e-3, delta=2e-5)      # CRC 1.074 mPa s at 25 C
        self.assertAlmostEqual(SUBS["water"]["boiling_point_K"], 373.1, delta=0.2)
        self.assertEqual(SUBS["sulfuric_acid"]["storage_class"], "acid")
        self.assertEqual(SUBS["hydrochloric_acid"]["storage_class"], "acid")
        self.assertNotIn("H280", SUBS["hydrochloric_acid"]["ghs"]["hazard_codes"])   # gas-only code dropped for the aqueous stock
        self.assertEqual(SUBS["sodium_hydroxide"]["storage_class"], "base")
        self.assertEqual(SUBS["hydrogen_peroxide"]["storage_class"], "oxidizer")
        self.assertEqual(SUBS["potassium_chlorate"]["storage_class"], "oxidizer")
        self.assertEqual(SUBS["nitrogen"]["storage_class"], "compressed_gas")
        self.assertEqual(SUBS["triethylamine"]["storage_class"], "flammable")
        self.assertIn("corrosive_base", SUBS["triethylamine"]["storage"]["flags"])
        self.assertEqual(SUBS["nitric_acid"]["storage_class"], "acid")
        self.assertIn("oxidizer", SUBS["nitric_acid"]["storage"]["flags"])
        self.assertEqual(SUBS["formaldehyde_aqueous"]["storage_class"], "toxic")
        self.assertEqual(SUBS["sodium_chloride"]["storage_class"], "general")
        self.assertEqual(SUBS["ethanol_vapor"]["storage_class"], "general")      # ledger-only vapour species

    def test_new_inventory_substances(self):
        for sid, (cas, *_rest) in NEW_PURE.items():
            with self.subTest(sid=sid):
                self.assertIn(sid, SUBS)
                self.assertEqual(SUBS[sid]["cas"], cas)
        for sid in NEW_MIXTURES:
            with self.subTest(sid=sid):
                self.assertEqual(SUBS[sid]["kind"], "mixture")
                self.assertIsNone(SUBS[sid]["molar_mass_kg_mol"])
        self.assertEqual(SUBS["argon"]["storage_class"], "compressed_gas")
        self.assertAlmostEqual(SUBS["potassium_hydrogen_phthalate"]["molar_mass_kg_mol"], 0.20422, places=4)
        self.assertEqual(SUBS["protein_ladder_prestained"]["storage"]["temperature_c"], [-20, -20])


class TestReproducible(unittest.TestCase):
    def test_file_equals_derivation_from_cached_pubchem(self):
        """Re-deriving every added field from the cached records gives exactly the file's values."""
        if not A.RAW.is_dir():
            self.skipTest("needs the cached PubChem records (references/substance_safety_raw/), which are not distributed")
        for sid, r in SUBS.items():
            if r.get("kind") == "mixture" or r.get("cas") is None:
                continue
            with self.subTest(sid=sid):
                ghs = A.parse_ghs(sid, r["phase"], r.get("name_zh", ""))
                st = A.storage_for(sid, r, ghs, A.cold_hint(sid))
                self.assertEqual(ghs, r["ghs"])
                self.assertEqual(st, r["storage"])



class TestPh686Standard(unittest.TestCase):
    """The pH 6.86 primary standard (DIN 19266 / NIST) is KH2PO4 + Na2HPO4, 0.025 mol/kg each."""

    def test_phosphate_standard_salts(self):
        for sid, cas, formula, mass in (("potassium_dihydrogen_phosphate", "7778-77-0", "H2KO4P", 0.136086),
                                        ("disodium_hydrogen_phosphate", "7558-79-4", "HNa2O4P", 0.141959)):
            with self.subTest(sid=sid):
                r = SUBS[sid]
                self.assertEqual((r["cas"], r["formula"], r["phase"], r["storage_class"]), (cas, formula, "solid", "general"))
                self.assertAlmostEqual(r["molar_mass_kg_mol"], mass, places=6)
                self.assertTrue(r["source"].startswith("PubChem CID"))
                self.assertGreater(r["density_kg_m3"], 1000.0)               # HSDB: 2340 (KH2PO4), approx 1700 (Na2HPO4)

    def test_inventory_buffer_is_the_equimolal_standard(self):
        sys.path.insert(0, str(ROOT / "chemlab"))
        from chemlab import inventory
        rec = next(r for r in inventory.STOCK if r["id"] == "buffer_ph6_86")
        got = {c["substance"]: c["amount"] for c in rec["contents"]}
        self.assertEqual(set(got), {"potassium_dihydrogen_phosphate", "disodium_hydrogen_phosphate", "water"})
        kg_water = got["water"]["value"] * 1.0                              # 0.5 L of a dilute solution ~ 0.5 kg water
        for sid in ("potassium_dihydrogen_phosphate", "disodium_hydrogen_phosphate"):
            molality = got[sid]["value"] / 1000.0 / SUBS[sid]["molar_mass_kg_mol"] / kg_water
            self.assertAlmostEqual(molality, 0.025, places=4, msg=sid)
        self.assertNotIn("sodium_dihydrogen_phosphate", got)


if __name__ == "__main__":
    unittest.main()
