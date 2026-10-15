"""Template-backed (real public data) tests for t4_measure: Spinsolve 80 NMR, MiniFlex600 XRD, XPR204E not_applicable.

Libraries: assets/reactions/magritek_spinsolve80_nmr_library (HMDB via PubChem) and
assets/reactions/rigaku_miniflex600_xrd_library (RRUFF XPOW + COD). Legacy synthetic fixture paths must stay unchanged.
"""
import copy
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

def _need(p):
    """Raise FileNotFoundError for a missing build record or source cache (the public release skips such tests)."""
    if not Path(p).exists():
        raise FileNotFoundError(2, "not present in this tree", str(p))

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/chemistry-lab-assets/scripts"))
from asset_tool import lint_schema, validate_value  # noqa: E402
from basic40_runtime_measure import (MagritekSpinsolve80Runtime, RigakuMiniFlex600Runtime, SPINSOLVE80_TYPE,  # noqa: E402
    RIGAKU_MINIFLEX600_TYPE, export_magritek_spinsolve80_contract_metadata, export_rigaku_miniflex600_contract_metadata)
from basic40_templates import magritek_spinsolve80 as nmr_tpl  # noqa: E402
from basic40_templates import rigaku_miniflex600 as xrd_tpl  # noqa: E402

NMR_LIB = ROOT / "assets/reactions/magritek_spinsolve80_nmr_library/templates.json"
XRD_LIB = ROOT / "assets/reactions/rigaku_miniflex600_xrd_library/templates.json"
NMR_VALIDATION = ROOT / "outputs/basic40_batch02/t4_measure/magritek_spinsolve80/templates/output_template_validation.json"
XRD_VALIDATION = ROOT / "outputs/basic40_batch02/t4_measure/rigaku_miniflex600/templates/output_template_validation.json"
CONTRACTS = {k: ROOT / f"assets/basic40_batch02/t4_measure/{k}/asset.contract.json" for k in ("magritek_spinsolve80", "rigaku_miniflex600", "mt_xpr204e")}


def band(lo_ppm, hi_ppm, ref=6.0, mhz=80.0):
    return {"frequency_min_hz": (lo_ppm - ref) * mhz, "frequency_max_hz": (hi_ppm - ref) * mhz}


class Spinsolve80TemplateTests(unittest.TestCase):
    METHOD = {"nucleus": "1H", "pulse_sequence": "single_pulse", "scans": 1, "relaxation_delay_s": 1.0,
              "acquisition_time_s": 0.4, "points": 512, "decoupling": False, "gradient_enabled": False}

    def setUp(self):
        self.calls = []
        self.host = MagritekSpinsolve80Runtime(scene_adapter=lambda iid, snap: self.calls.append((iid, copy.deepcopy(snap))))
        self.host.create_instance("nmr_a")
        self.host.register_principal("agent", visibility="agent", instance_ids=["nmr_a"])
        self.host.register_principal("evaluator", visibility="evaluator")
        self.agent, self.evaluator = self.host.session("agent"), self.host.session("evaluator")
        self.n = 0

    def invoke(self, cap, **args):
        self.n += 1
        return self.agent.invoke(instance_id="nmr_a", capability_id=cap, arguments=args, request_id=f"tpl_{self.n}")

    def ok(self, cap, **args):
        response = self.invoke(cap, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response["result"]

    def acquire(self, sample_id, **method):
        if not self.host._instances["nmr_a"]["power"]:
            self.ok("power.set", enabled=True)
        if self.host._instances["nmr_a"]["sample_id"] is not None:
            self.ok("sample.remove")
        self.ok("sample.insert", sample_id=sample_id)
        self.ok("acquisition.configure", **{**self.METHOD, **method})
        self.ok("acquisition.start")
        cfg = {**self.METHOD, **method}
        self.host.host_step(self.host.time_s + cfg["scans"] * (cfg["relaxation_delay_s"] + cfg["acquisition_time_s"]))

    def test_library_counts_provenance_and_independent_validation(self):
        lib = json.loads(NMR_LIB.read_text())
        templates = lib["templates"]
        self.assertGreaterEqual(sum(1 for t in templates if t["nuclei"]["1H"]), 50)
        self.assertGreaterEqual(sum(1 for t in templates if t["nuclei"]["13C"]), 50)
        self.assertEqual(len({t["substance_id"] for t in templates}), len(templates))
        for t in templates:
            self.assertIn(t["evidence"], ("measured", "measured_literature", "derived", "assumption"))
            self.assertTrue(t["source"] and t["accessed"] and t["unit"])
        report = json.loads(NMR_VALIDATION.read_text())
        self.assertEqual(report["n_records"], len(templates))
        self.assertEqual(report["n_failed"], 0)
        ethanol = nmr_tpl.record("ethanol")
        self.assertEqual(ethanol["shared_ids"], {"spectral_library": "ethanol", "substances": "ethanol"})
        self.assertEqual(nmr_tpl.record("ethyl_acetate")["shared_ids"].get("substances"), "ethyl_acetate")

    def test_template_read_is_public_lookup_without_side_effects(self):
        before, clock, calls = copy.deepcopy(self.host._instances), self.host.time_s, len(self.calls)
        ref = self.ok("template.read", substance_id="ethyl_acetate", nucleus="1H")
        self.assertFalse(ref["synthetic"])
        self.assertEqual(ref["observe_frequency_hz"], 80e6)
        self.assertAlmostEqual(sum(g["n_nuclei"] for g in ref["groups"]), 8)
        self.assertEqual(sorted(round(g["center_ppm"], 1) for g in ref["groups"]), [1.3, 2.0, 4.1])
        carbon = self.ok("template.read", substance_id="ethyl_acetate", nucleus="13C")
        self.assertAlmostEqual(carbon["observe_frequency_hz"], 80e6 * 0.25145020)
        self.assertTrue(any(abs(g["center_ppm"] - 170.9) < 1.5 for g in carbon["groups"]))
        self.assertEqual(self.invoke("template.read", substance_id="no_such_compound", nucleus="1H")["error"]["code"], "UNKNOWN_REFERENCE")
        self.assertEqual(self.invoke("template.read", substance_id="ethanol")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.invoke("template.read", substance_id="ethanol", nucleus="15N")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.host._instances, before)
        self.assertEqual((self.host.time_s, len(self.calls)), (clock, calls))

    def test_j_pattern_offsets_are_kept_in_hz_at_80_mhz(self):
        ref = self.ok("template.read", substance_id="ethanol", nucleus="1H")
        methyl = min(ref["groups"], key=lambda g: g["center_ppm"])
        strong = sorted((l for l in methyl["lines"] if l["relative_nuclei"] > 0.3), key=lambda l: l["shift_ppm"])
        self.assertEqual(len(strong), 3)  # CH3 triplet of the 90 MHz CDCl3 reference
        for a, b in zip(strong, strong[1:]):
            spacing_hz = b["frequency_offset_hz"] - a["frequency_offset_hz"]
            self.assertTrue(5.5 < spacing_hz < 8.5, spacing_hz)
            self.assertAlmostEqual(b["shift_ppm"] - a["shift_ppm"], spacing_hz / 80.0, places=9)

    def test_signals_scale_with_proton_count_and_amount(self):
        self.host.register_template_sample("nmr_a", "tube", [{"substance_id": "ethanol", "amount_mol": 0.002}, {"substance_id": "ethyl_acetate", "amount_mol": 0.001}])
        signals = self.host._instances["nmr_a"]["samples"]["tube"]
        expected_h = 0.002 * nmr_tpl.record("ethanol")["nuclei"]["1H"][0]["proton_count_rule"]["n_h_basis"] + 0.001 * 8
        self.assertAlmostEqual(sum(p["amplitude"] for p in signals["1H"]), nmr_tpl.AMPLITUDE_PER_MOL * expected_h)
        self.assertAlmostEqual(sum(p["amplitude"] for p in signals["13C"]), nmr_tpl.AMPLITUDE_PER_MOL * (0.002 * 2 + 0.001 * 4))
        self.assertTrue(all(p["t2_s"] == nmr_tpl.T2_S for n in signals.values() for p in n))

    def test_acquired_template_spectrum_ppm_axis_and_integrals(self):
        self.host.register_template_sample("nmr_a", "tube_1", [{"substance_id": "ethanol", "amount_mol": 0.001}, {"substance_id": "ethyl_acetate", "amount_mol": 0.001}])
        self.host.register_template_sample("nmr_a", "tube_2", [{"substance_id": "ethanol", "amount_mol": 0.002}, {"substance_id": "ethyl_acetate", "amount_mol": 0.001}])
        integrals = {}
        for tube in ("tube_1", "tube_2"):
            self.acquire(tube)
            legacy = self.ok("nmr.read_spectrum")
            self.assertTrue(legacy["synthetic"])
            self.assertEqual(len(legacy["samples"]), 512)
            result = self.ok("nmr.read_template_spectrum")
            self.assertFalse(result["synthetic"])
            self.assertTrue(result["synthetic_parts"] and result["limitations"])
            self.assertEqual(result["template_source"], nmr_tpl.TEMPLATE_REL)
            for p, q in zip(result["samples"], legacy["samples"]):
                self.assertAlmostEqual(p["chemical_shift_ppm"], 6.0 + q["frequency_offset_hz"] / 80.0)
                self.assertEqual(p["amplitude"], q["amplitude"])
            top = max(result["samples"], key=lambda p: p["amplitude"])
            self.assertTrue(1.0 < top["chemical_shift_ppm"] < 2.2, top)
            integrals[tube] = {"ethanol_ch2": self.ok("nmr.integrate", **band(3.45, 3.92))["integral_hz"],
                               "acetate_och2": self.ok("nmr.integrate", **band(3.95, 4.32))["integral_hz"]}
        ratio_ethanol = integrals["tube_2"]["ethanol_ch2"] / integrals["tube_1"]["ethanol_ch2"]
        ratio_acetate = integrals["tube_2"]["acetate_och2"] / integrals["tube_1"]["acetate_och2"]
        self.assertAlmostEqual(ratio_ethanol, 2.0, delta=0.2)
        self.assertAlmostEqual(ratio_acetate, 1.0, delta=0.15)

    def test_carbon_template_uses_derived_13c_frequency(self):
        self.host.register_template_sample("nmr_a", "tube", [{"substance_id": "ethyl_acetate", "amount_mol": 0.001}])
        self.acquire("tube", nucleus="13C", acquisition_time_s=0.1)
        result = self.ok("nmr.read_template_spectrum")
        self.assertAlmostEqual(result["observe_frequency_hz"], 80e6 * 0.25145020)
        self.assertEqual(result["reference_shift_ppm"], 110.0)
        self.assertIsNone(self.ok("nmr.read_spectrum")["carrier_frequency_hz"])  # legacy slot semantics unchanged
        peaks = [p["chemical_shift_ppm"] for p in sorted(result["samples"], key=lambda p: -p["amplitude"])[:12]]
        for shift in (14.2, 21.0, 60.4, 171.0):
            self.assertTrue(any(abs(s - shift) < 1.5 for s in peaks), (shift, peaks))

    def test_solvent_selects_reference_spectrum_and_truth_stays_host_side(self):
        self.host.register_template_sample("nmr_a", "tube_d2o", [{"substance_id": "ethanol", "amount_mol": 0.001}], solvent="D2O")
        truth = self.host.template_sample_truth("nmr_a")["tube_d2o"]
        spec = truth["composition"][0]["spectra"]["1H"]
        self.assertEqual(spec["solvent_key"], "D2O")
        self.assertTrue(spec["solvent_match"])
        self.acquire("tube_d2o")
        self.ok("nmr.read_template_spectrum")
        self.ok("nmr.integrate", **band(1.0, 1.4))
        agent_text = json.dumps([self.agent.describe(instance_id="nmr_a"), self.agent.read(instance_id="nmr_a"),
                                 self.ok("nmr.read_spectrum"), self.ok("nmr.read_template_spectrum"), self.agent.logs(),
                                 self.host.scene_snapshot("nmr_a")])
        for secret in ("ethanol", "tube_d2o_truth", "hmdb", "template_samples", '"composition"', '"t2_s"', '"signals"'):
            self.assertNotIn(secret, agent_text)
        registered = self.evaluator.read(instance_id="nmr_a", state_ids=["truth.registered_samples"])["observations"]["truth.registered_samples"]["value"]
        self.assertEqual(registered, [])  # template tubes are not part of the legacy synthetic truth table
        self.assertEqual(self.agent.read(instance_id="nmr_a", state_ids=["truth.registered_samples"])["error"]["code"], "ACCESS_DENIED")

    def test_invalid_registrations_leave_state_unchanged(self):
        bad = [([], None), ([{"substance_id": "ethanol"}], None), ([{"substance_id": "unknown_x", "amount_mol": 0.001}], None),
               ([{"substance_id": "ethanol", "amount_mol": 0}], None), ([{"substance_id": "ethanol", "amount_mol": 2.0}], None),
               ([{"substance_id": "ethanol", "amount_mol": float("nan")}], None),
               ([{"substance_id": "ethanol", "amount_mol": 0.001}, {"substance_id": "ethanol", "amount_mol": 0.001}], None),
               ([{"substance_id": "ethanol", "amount_mol": 0.001}], "H2O")]
        for composition, solvent in bad:
            before = copy.deepcopy(self.host._instances)
            with self.assertRaises(ValueError):
                self.host.register_template_sample("nmr_a", "tube", composition, solvent=solvent)
            self.assertEqual(self.host._instances, before)
        self.host.register_template_sample("nmr_a", "tube", [{"substance_id": "water", "amount_mol": 0.001}])
        with self.assertRaises(ValueError):
            self.host.register_template_sample("nmr_a", "tube", [{"substance_id": "water", "amount_mol": 0.001}])
        with self.assertRaises(ValueError):
            self.host.register_sample("nmr_a", "tube", {"1H": [], "13C": []})

    def test_legacy_fixture_path_and_metadata_unchanged(self):
        self.host.register_sample("nmr_a", "fixture", {"1H": [{"frequency_offset_hz": 8, "amplitude": 2.0, "t2_s": 0.4}], "13C": [{"frequency_offset_hz": -12, "amplitude": 3.0, "t2_s": 0.3}]})
        self.acquire("fixture", scans=2, acquisition_time_s=0.5, points=64)
        fid = self.ok("nmr.read_fid")
        self.assertEqual((fid["samples"][0]["real"], fid["samples"][0]["imaginary"]), (2, 0))
        spectrum = self.ok("nmr.read_spectrum")
        self.assertEqual(max(spectrum["samples"], key=lambda p: p["amplitude"])["frequency_offset_hz"], 8)
        self.assertEqual(self.invoke("nmr.read_template_spectrum")["error"]["code"], "PRECONDITION_FAILED")
        exported = export_magritek_spinsolve80_contract_metadata()["asset_types"][SPINSOLVE80_TYPE]["capabilities"]
        self.assertFalse(set(nmr_tpl.TEMPLATE_CAPABILITIES) & set(exported))
        described = self.agent.describe(instance_id="nmr_a")["capabilities"]
        self.assertLessEqual(set(nmr_tpl.TEMPLATE_CAPABILITIES) | set(exported), set(described))

    def test_capability_schemas_and_outputs_validate(self):
        meta = nmr_tpl.capability_metadata()
        for name, spec in meta.items():
            self.assertEqual(lint_schema(spec["input_schema"]), [], name)
            self.assertEqual(lint_schema(spec["output_schema"]), [], name)
            self.assertEqual(validate_value(spec["example_arguments"], spec["input_schema"]), [], name)
        self.host.register_template_sample("nmr_a", "tube", [{"substance_id": "acetic_acid", "amount_mol": 0.001}])
        self.acquire("tube")
        outputs = {"template.read": self.ok("template.read", substance_id="acetic_acid", nucleus="1H"),
                   "nmr.read_template_spectrum": self.ok("nmr.read_template_spectrum")}
        for name, value in outputs.items():
            self.assertEqual(validate_value(value, meta[name]["output_schema"]), [], name)


class MiniFlex600TemplateTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = RigakuMiniFlex600Runtime(scene_adapter=lambda name, data: self.calls.append((name, copy.deepcopy(data))))
        self.host.create_instance("mini_a")
        self.host.register_principal("agent", visibility="agent", instance_ids=["mini_a"])
        self.host.register_principal("evaluator", visibility="evaluator")
        self.agent, self.evaluator = self.host.session("agent"), self.host.session("evaluator")
        self.n = 0

    def invoke(self, cap, **args):
        self.n += 1
        return self.agent.invoke(instance_id="mini_a", capability_id=cap, arguments=args, request_id=f"tpl_{self.n}")

    def ok(self, cap, **args):
        response = self.invoke(cap, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response["result"]

    def prepare(self, current=0.015, sample="crucible_01"):
        self.ok("power.set", enabled=True)
        self.ok("access.set_door_angle", angle_rad=0.8)
        self.ok("slot.set_sample", connection_id=sample)
        self.ok("access.set_door_angle", angle_rad=0)
        self.ok("xray.configure", voltage_v=40000, current_a=current)
        self.ok("xrd.set_slit", slit_id="std_slit")
        self.host.host_set_cooling("mini_a", connection_id="chiller", ready=True)

    def scan(self, start_deg=20, stop_deg=60, step_deg=0.02, integration=0.1):
        self.ok("xrd.configure_scan", start_rad=math.radians(start_deg), stop_rad=math.radians(stop_deg), step_rad=math.radians(step_deg), integration_time_s=integration)
        self.ok("xrd.start_scan")
        points = int(round((stop_deg - start_deg) / step_deg)) + 1
        self.host.host_step(self.host.time_s + points * integration)
        return self.ok("xrd.read_template_pattern")

    @staticmethod
    def height_near(points, deg, window=0.15):
        return max(p["intensity_cps"] for p in points if abs(math.degrees(p["two_theta_rad"]) - deg) <= window)

    def test_library_counts_shared_ids_and_independent_validation(self):
        lib = json.loads(XRD_LIB.read_text())
        templates = lib["templates"]
        self.assertGreaterEqual(len(templates), 50)
        self.assertEqual(len({t["substance_id"] for t in templates}), len(templates))
        for t in templates:
            self.assertEqual(t["evidence"], "derived")
            self.assertTrue(t["source"] and t["accessed"] and t["reflections"])
            self.assertEqual(t["unit"]["d_m"], "m")
        report = json.loads(XRD_VALIDATION.read_text())
        self.assertEqual((report["n_records"], report["n_failed"]), (len(templates), 0))
        self.assertTrue(all(c["status"] == "passed" for c in report["calculator_crosscheck"]))
        links = {t["substance_id"]: t["shared_ids"].get("substances") for t in templates}
        for phase, substance in (("calcite", "calcium_carbonate"), ("lime", "calcium_oxide"), ("halite", "sodium_chloride"),
                                 ("chlorargyrite", "silver_chloride"), ("nahcolite", "sodium_bicarbonate"), ("natrite", "sodium_carbonate"),
                                 ("nitratine", "sodium_nitrate"), ("pyrolusite", "manganese_dioxide"), ("silver_nitrate", "silver_nitrate")):
            self.assertEqual(links[phase], substance)
        self.assertEqual(xrd_tpl.record("calcium_carbonate")["substance_id"], "calcite")
        self.assertEqual(xrd_tpl.record("calcium_oxide")["substance_id"], "lime")
        for standard in ("quartz", "corundum", "silicon", "rutile", "anatase", "zincite", "periclase", "tenorite", "sylvite"):
            self.assertIsNotNone(xrd_tpl.record(standard), standard)

    def test_template_read_public_and_side_effect_free(self):
        before, clock = copy.deepcopy(self.host._instances), self.host.time_s
        ref = self.ok("template.read", phase_id="calcium_carbonate")
        self.assertEqual(ref["phase_id"], "calcite")
        self.assertFalse(ref["synthetic"])
        strongest = max(ref["reflections"], key=lambda r: r["intensity_rel"])
        self.assertEqual(strongest["hkl"], [1, 0, 4])
        self.assertAlmostEqual(strongest["d_m"], 3.03e-10, delta=0.01e-10)
        self.assertEqual(self.invoke("template.read", phase_id="unobtainium")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.invoke("template.read")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.host._instances, before)
        self.assertEqual(self.host.time_s, clock)

    def test_furnace_product_mixture_peaks_and_fractions(self):
        self.prepare()
        meta = self.host.host_load_template_sample("mini_a", [{"phase_id": "calcium_oxide", "weight_fraction": 0.6}, {"phase_id": "calcium_carbonate", "weight_fraction": 0.4}])
        self.assertEqual([p["phase_id"] for p in meta["phases"]], ["lime", "calcite"])
        result = self.scan()
        self.assertFalse(result["synthetic"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["samples"]), 2001)
        legacy = self.ok("xrd.read_pattern")
        self.assertTrue(legacy["synthetic"])
        self.assertEqual([p["intensity_cps"] for p in legacy["samples"]], [p["intensity_cps"] for p in result["samples"]])
        lime_200 = math.degrees(2 * math.asin(xrd_tpl.KA1_M / (2 * 2.4075e-10)))
        calcite_104 = math.degrees(2 * math.asin(xrd_tpl.KA1_M / (2 * xrd_tpl.record("calcite")["reflections"][1]["d_m"])))
        h_lime = self.height_near(result["samples"], lime_200)
        h_calcite = self.height_near(result["samples"], 29.40)
        self.assertGreater(h_lime, 0.5 * 0.6 * xrd_tpl.PEAK_RATE_REF_CPS)
        self.assertAlmostEqual(h_lime / h_calcite, 0.6 / 0.4, delta=0.25)
        self.assertLess(self.height_near(result["samples"], 33.0, 0.3), 0.05 * h_calcite)  # no reflection of either phase there
        self.assertTrue(28 < calcite_104 < 31)

    def test_tube_current_scales_intensity_and_sample_change_invalidates_template(self):
        self.prepare(current=0.015)
        self.host.host_load_template_sample("mini_a", [{"phase_id": "halite", "weight_fraction": 1.0}])
        full = self.scan(30, 33, 0.02, 0.1)
        self.ok("xray.configure", voltage_v=40000, current_a=0.007)
        self.assertEqual(self.invoke("xrd.start_scan")["error"]["code"], "PRECONDITION_FAILED")  # response bound to old tube setting
        self.host.host_load_template_sample("mini_a", [{"phase_id": "halite", "weight_fraction": 1.0}])
        reduced = self.scan(30, 33, 0.02, 0.1)
        self.assertAlmostEqual(self.height_near(reduced["samples"], 31.7) / self.height_near(full["samples"], 31.7), 7 / 15, places=6)
        self.host.host_load_synthetic_response("mini_a", [{"two_theta_rad": 0.0, "intensity_cps": 1.0}, {"two_theta_rad": 2.0, "intensity_cps": 1.0}])
        self.assertEqual(self.invoke("xrd.read_template_pattern")["error"]["code"], "PRECONDITION_FAILED")
        self.host.host_load_template_sample("mini_a", [{"phase_id": "halite", "weight_fraction": 1.0}])
        self.ok("access.set_door_angle", angle_rad=0.8)
        self.ok("slot.set_sample", connection_id="another_holder")
        self.assertEqual(self.invoke("xrd.read_template_pattern")["error"]["code"], "PRECONDITION_FAILED")
        self.assertIsNone(self.host.template_sample_truth("mini_a"))

    def test_scan_outside_template_coverage_is_rejected(self):
        self.prepare()
        self.host.host_load_template_sample("mini_a", [{"phase_id": "quartz", "weight_fraction": 1.0}])
        self.ok("xrd.configure_scan", start_rad=math.radians(80), stop_rad=math.radians(100), step_rad=math.radians(0.05), integration_time_s=0.1)
        before = copy.deepcopy(self.host._instances)
        self.assertEqual(self.invoke("xrd.start_scan")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.host._instances, before)

    def test_invalid_phase_lists_leave_state_unchanged(self):
        with self.assertRaises(ValueError):  # tube/slit/sample not configured yet
            self.host.host_load_template_sample("mini_a", [{"phase_id": "quartz", "weight_fraction": 1.0}])
        self.prepare()
        for phases in ([], [{"phase_id": "quartz"}], [{"phase_id": "unknown", "weight_fraction": 0.5}],
                       [{"phase_id": "quartz", "weight_fraction": 0.7}, {"phase_id": "calcite", "weight_fraction": 0.5}],
                       [{"phase_id": "quartz", "weight_fraction": 0.5}, {"phase_id": "quartz", "weight_fraction": 0.2}],
                       [{"phase_id": "quartz", "weight_fraction": float("inf")}], [{"phase_id": "quartz", "weight_fraction": True}]):
            before = copy.deepcopy(self.host._instances)
            with self.assertRaises(ValueError):
                self.host.host_load_template_sample("mini_a", phases)
            self.assertEqual(self.host._instances, before)

    def test_truth_isolation_and_legacy_metadata_unchanged(self):
        self.prepare()
        self.host.host_load_template_sample("mini_a", [{"phase_id": "lime", "weight_fraction": 0.5}, {"phase_id": "periclase", "weight_fraction": 0.3}])
        self.scan(30, 45, 0.05, 0.1)
        agent_text = json.dumps([self.agent.describe(instance_id="mini_a"), self.agent.read(instance_id="mini_a"), self.ok("xrd.read_pattern"),
                                 self.ok("xrd.read_template_pattern"), self.agent.logs(), self.host.scene_snapshot("mini_a")])
        for secret in ("lime", "periclase", "weight_fraction", "template_meta", "9006694", "\"truth."):
            self.assertNotIn(secret, agent_text)
        truth = self.host.template_sample_truth("mini_a")
        self.assertEqual([p["phase_id"] for p in truth["phases"]], ["lime", "periclase"])
        self.assertAlmostEqual(truth["non_diffracting_fraction"], 0.2)
        self.assertIsNotNone(self.evaluator.read(instance_id="mini_a", state_ids=["truth.response"])["observations"]["truth.response"]["value"])
        exported = export_rigaku_miniflex600_contract_metadata()["asset_types"][RIGAKU_MINIFLEX600_TYPE]["capabilities"]
        self.assertFalse(set(xrd_tpl.TEMPLATE_CAPABILITIES) & set(exported))
        described = self.agent.describe(instance_id="mini_a")["capabilities"]
        self.assertLessEqual(set(xrd_tpl.TEMPLATE_CAPABILITIES) | set(exported), set(described))
        self.assertEqual(described["xrd.phase_search"]["availability"], "unsupported")

    def test_legacy_synthetic_response_path_unchanged(self):
        self.prepare()
        self.host.host_load_synthetic_response("mini_a", [{"two_theta_rad": 0.0, "intensity_cps": 10}, {"two_theta_rad": math.radians(2), "intensity_cps": 30}])
        self.ok("xrd.configure_scan", start_rad=0, stop_rad=math.radians(2), step_rad=math.radians(0.5), integration_time_s=1)
        self.ok("xrd.start_scan")
        self.host.host_step(5)
        pattern = self.ok("xrd.read_pattern")["samples"]
        self.assertEqual([p["intensity_cps"] for p in pattern], [10, 15, 20, 25, 30])
        self.assertEqual(self.invoke("xrd.read_template_pattern")["error"]["code"], "PRECONDITION_FAILED")

    def test_capability_schemas_and_outputs_validate(self):
        meta = xrd_tpl.capability_metadata()
        for name, spec in meta.items():
            self.assertEqual(lint_schema(spec["input_schema"]), [], name)
            self.assertEqual(lint_schema(spec["output_schema"]), [], name)
            for example in spec["example_arguments"]:
                self.assertEqual(validate_value(example, spec["input_schema"]), [], name)
        self.prepare()
        self.host.host_load_template_sample("mini_a", [{"phase_id": "corundum", "weight_fraction": 1.0}])
        outputs = {"xrd.read_template_pattern": self.scan(25, 45, 0.05, 0.1), "template.read": self.ok("template.read", phase_id="corundum")}
        for name, value in outputs.items():
            self.assertEqual(validate_value(value, meta[name]["output_schema"]), [], name)


class ContractRegistrationTests(unittest.TestCase):
    def test_output_templates_extensions(self):
        xpr = json.loads(CONTRACTS["mt_xpr204e"].read_text())
        entry = xpr["extensions"]["chemistry_lab.output_templates"]
        self.assertEqual(entry["status"], "not_applicable")
        self.assertTrue(entry["reason_zh"])
        for slug, caps in (("magritek_spinsolve80", nmr_tpl.TEMPLATE_CAPABILITIES), ("rigaku_miniflex600", xrd_tpl.TEMPLATE_CAPABILITIES)):
            contract = json.loads(CONTRACTS[slug].read_text())
            entry = contract["extensions"]["chemistry_lab.output_templates"]
            self.assertEqual(entry["status"], "template")
            self.assertGreaterEqual(entry["n_records"], 50)
            self.assertTrue(entry["runtime_usable"])
            _need(ROOT / entry["validation"])
            for cap in caps:
                self.assertEqual(contract["capabilities"][cap]["availability"], "implemented", cap)
            self.assertIn("template_library", contract["artifacts"])


if __name__ == "__main__":
    unittest.main()
