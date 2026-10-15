"""Shared-id crosswalk (assets/reactions/shared_id_crosswalk.json) and its helpers in scripts/template_library.py.

The crosswalk links every template-library record that carries a CAS / InChIKey / PubChem CID (or a shared id as its
record id) to the shared compound table (spectral_library/compounds.json) and to substances.json.  These tests check
that the file was built from the current shared tables, that every link is internally consistent (a CAS link really
points at the compound with that CAS), that the reverse index agrees with the per-record links, and that the helper
functions answer "which instruments have real templates for this compound".
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import template_library as tl  # noqa: E402

REACTIONS = ROOT / "assets" / "reactions"


def load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


class TestCrosswalkFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cw = tl.crosswalk()
        cls.compounds = load("assets/reactions/spectral_library/compounds.json")["compounds"]
        cls.substances = load("assets/reactions/substances.json")["substances"]

    def test_built_from_current_shared_tables(self):
        for key, rel in (("spectral_library", "assets/reactions/spectral_library/compounds.json"), ("substances", "assets/reactions/substances.json")):
            entry = self.cw["shared_tables"][key]
            self.assertEqual(entry["path"], rel)
            self.assertEqual(entry["sha256"], hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(), f"crosswalk is stale for {rel}; rerun build_crosswalk.py")

    def test_every_link_points_at_an_existing_shared_id(self):
        for rel, lib in self.cw["libraries"].items():
            for record in lib["records"]:
                ids = record["shared_ids"]
                for cid in ids.get("spectral_library_all", [ids["spectral_library"]] if "spectral_library" in ids else []):
                    self.assertIn(cid, self.compounds, (rel, record["record_id"]))
                for sid in ids.get("substances_all", [ids["substances"]] if "substances" in ids else []):
                    self.assertIn(sid, self.substances, (rel, record["record_id"]))
                for cid in ids.get("component_spectral_library", []):
                    self.assertIn(cid, self.compounds)

    def test_cas_links_match_the_compound_cas(self):
        n = 0
        for rel, lib in self.cw["libraries"].items():
            for record in lib["records"]:
                ids = record["shared_ids"]
                if ids.get("match_basis") == "cas":
                    self.assertEqual(self.compounds[ids["spectral_library"]]["cas"], ids_key(record), (rel, record["record_id"]))
                    self.assertIn(ids_key(record), record["identifiers"]["cas"])
                    n += 1
        self.assertGreater(n, 2000)

    def test_reverse_index_agrees_with_record_links(self):
        for cid, rows in self.cw["by_compound"].items():
            self.assertIn(cid, self.compounds)
            for row in rows:
                lib = self.cw["libraries"][row["library"]]
                self.assertTrue(row["asset_type_id"] in lib["asset_type_ids"] or (row["asset_type_id"] == "(unattributed)" and not lib["asset_type_ids"]))
                linked = {r["record_id"] for r in lib["records"]
                          if cid in r["shared_ids"].get("spectral_library_all", [r["shared_ids"].get("spectral_library")])}
                self.assertTrue(set(row["record_ids"]) <= linked, (cid, row["library"]))

    def test_stats_are_consistent(self):
        stats = self.cw["stats"]
        self.assertEqual(stats["n_compounds"], len(self.compounds))
        multi = sum(1 for v in self.cw["instruments_by_compound"].values() if len(v) >= 2)
        self.assertEqual(stats["n_compounds_in_2plus_instrument_libraries"], multi)
        self.assertGreaterEqual(stats["n_libraries_linked_to_spectral_library"], 60)
        for rel, lib in self.cw["libraries"].items():
            cov = lib["coverage"]
            self.assertLessEqual(cov["linked_spectral_library"], len(lib["records"]))
            self.assertLessEqual(len(lib["records"]), cov["records"])

    def test_substance_aliases_resolve_to_the_same_molecule(self):
        mapping = self.cw["substances_to_spectral_library"]
        for sid, cid in mapping.items():
            s_cas = self.substances[sid].get("cas")
            if s_cas:
                self.assertEqual(self.compounds[cid]["cas"], s_cas, sid)
        self.assertEqual(mapping.get("ethanol_vapor"), "ethanol")
        self.assertEqual(mapping.get("water_vapor"), "water")
        for sid in ("sulfuric_acid", "hydrochloric_acid", "oxygen", "hydrogen", "carbon_monoxide", "maleic_acid", "succinic_acid"):
            self.assertEqual(mapping.get(sid), sid, f"molecular reaction species {sid} must be in the shared table")


def ids_key(record):
    return record["shared_ids"]["match_key"]


class TestHelpers(unittest.TestCase):
    def test_shared_id_for_record_known_records(self):
        hs = "assets/reactions/agilent_hs8697_headspace_library/templates.json"
        ids = tl.shared_id_for_record(hs, "acetaldehyde")
        self.assertEqual(ids["spectral_library"], "acetaldehyde")
        self.assertEqual(ids["match_basis"], "cas")
        nmr = "assets/reactions/magritek_spinsolve80_nmr_library/templates.json"
        record = tl.load_library(nmr)["templates"][0]
        self.assertEqual(tl.shared_id_for_record(nmr, record["substance_id"])["spectral_library"], tl.shared_compounds_by_cas()[record["cas"]])
        self.assertEqual(tl.shared_id_for_record(str(ROOT / hs), "acetaldehyde"), ids)  # absolute path accepted
        self.assertIsNone(tl.shared_id_for_record(hs, "no_such_record"))
        self.assertIsNone(tl.shared_id_for_record("assets/reactions/no_such_library/templates.json", "acetaldehyde"))

    def test_libraries_for_compound(self):
        rows = tl.libraries_for_compound("ethanol")
        instruments = {r["asset_type_id"] for r in rows}
        self.assertIn("instrument.agilent_hs8697", instruments)
        self.assertIn("instrument.agilent_gc8890b", instruments)  # via the shared gc_retention.json
        own = tl.libraries_for_compound("ethanol", include_shared_tables=False)
        self.assertFalse(any(r["library"].startswith("assets/reactions/spectral_library/") for r in own))
        self.assertLess(len(own), len(rows))
        for r in rows:
            self.assertTrue(r["record_ids"])
        # a substances.json phase variant resolves to the same compound
        vapour = {(r["asset_type_id"], r["library"]) for r in tl.libraries_for_compound("ethanol_vapor")}
        self.assertTrue({(r["asset_type_id"], r["library"]) for r in rows} <= vapour)
        self.assertEqual(tl.libraries_for_compound("no_such_compound"), [])

    def test_expanded_compounds_reach_several_instruments(self):
        cw = tl.crosswalk()
        added = json.loads((ROOT / "outputs/spectral_library_expansion/selection.json").read_text(encoding="utf-8"))["selected"]
        reached = [s["id"] for s in added if len(cw["instruments_by_compound"].get(s["id"], [])) >= 2]
        self.assertGreaterEqual(len(reached), 0.9 * len(added))
        self.assertGreaterEqual(len({r["asset_type_id"] for r in tl.libraries_for_compound("triethylamine", include_shared_tables=False)}), 2)

    def test_existing_helper_unchanged(self):
        self.assertEqual(tl.shared_ids_for("64-17-5", "ethanol"), {"spectral_library": "ethanol", "substances": "ethanol"})
        self.assertEqual(tl.shared_ids_for(None, None), {})


if __name__ == "__main__":
    unittest.main()
