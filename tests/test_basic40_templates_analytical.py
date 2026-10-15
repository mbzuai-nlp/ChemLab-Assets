"""Template-backed (public real-data) paths of the six t3_analytical basic40_batch02 instruments.

GC 8890B (spectral_library gc_retention.json), HPLC 1260 (hplc_uv.json + uv_vis.json), HS 8697 (Sander 2023 Henry
library), DR6000 (Hach methods + NIST UV + PhotochemCAD), FT/IR-4700 (NIST IR, decoder-corrected shared file) and 5910 Ri
(sedimentation reference). Legacy fixture paths are covered by tests/test_basic40_analytical.py and must not change.
"""
import copy
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _need(p):
    """Raise FileNotFoundError for a missing build record or source cache (the public release skips such tests)."""
    if not Path(p).exists():
        raise FileNotFoundError(2, "not present in this tree", str(p))

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.basic40_runtime_analytical import (  # noqa: E402
    AgilentGC8890B, AgilentHPLC1260Runtime, AgilentHS8697, Eppendorf5910Ri, HachDR6000, JascoFTIR4700,
    export_agilent_gc8890b_contract_metadata, export_agilent_hplc1260_contract_metadata,
    export_agilent_hs8697_contract_metadata, export_eppendorf_5910ri_contract_metadata,
    export_hach_dr6000_contract_metadata, export_jasco_ftir4700_contract_metadata)
from scripts.basic40_templates import _analytical_common as common  # noqa: E402
from scripts.basic40_templates import agilent_gc8890b as gc_t  # noqa: E402
from scripts.basic40_templates import agilent_hs8697 as hs_t  # noqa: E402
from scripts.basic40_templates import hach_dr6000 as dr_t  # noqa: E402

_spec = importlib.util.spec_from_file_location("_templates_asset_tool", ROOT / "skills/chemistry-lab-assets/scripts/asset_tool.py")
asset_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asset_tool)

R = 8.31446261815324
HALF_PI = math.pi / 2
OUT = ROOT / "outputs" / "basic40_batch02" / "t3_analytical"
LIBRARIES = {
    "agilent_gc8890b": ["assets/reactions/spectral_library/gc_retention.json"],
    "agilent_hplc1260": ["assets/reactions/spectral_library/hplc_uv.json", "assets/reactions/spectral_library/uv_vis.json"],
    "agilent_hs8697": ["assets/reactions/agilent_hs8697_headspace_library/templates.json"],
    "hach_dr6000": ["assets/reactions/hach_dr6000_colorimetric_library/templates.json", "assets/reactions/spectral_library/uv_vis.json",
                    "assets/reactions/jasco_fp8350_fluorescence_library/templates.json"],
    "jasco_ftir4700": ["assets/reactions/spectral_library/ir_spectra.json"],  # corrected in place 2026-10-14; overlay library retired
    "eppendorf_5910ri": ["assets/reactions/eppendorf_5910ri_sedimentation_library/templates.json"],
}


def scene(_instance, _updates):
    return None


class Caller:
    def __init__(self, test, device, metadata):
        self.test, self.device, self.metadata, self.n = test, device, metadata, 0

    def __call__(self, cid, expect="succeeded", **arguments):
        self.n += 1
        response = self.device.invoke(capability_id=cid, arguments=arguments, request_id=f"tpl_{self.n}")
        self.test.assertEqual(response["status"], expect, response)
        if expect in ("succeeded", "accepted"):
            schema = self.metadata["capabilities"][cid]["output_schema"]
            self.test.assertEqual(asset_tool.validate_value(response["result"], schema), [], cid)
        return response

    def unchanged(self, cid, code, **arguments):
        before = self.device.debug_snapshot()
        response = self(cid, expect="rejected", **arguments)
        self.test.assertEqual(response["error"]["code"], code)
        self.test.assertEqual(response["error"]["side_effects"], "none")
        self.test.assertEqual(before, self.device.debug_snapshot())
        return response


def assert_provenance(test, result, *libraries):
    test.assertIs(result["synthetic"], False)
    test.assertTrue(result["limitations"])
    for rel in libraries:
        test.assertIn(rel, result["template_source"])
        test.assertEqual(len(result["library_sha256"][rel]), 64)


STATIC_TEXT = ("template_source", "library_sha256", "evidence", "synthetic_parts", "limitations")


def _measured_part(payload):
    """Drop the static provenance/limitation text (identical for every sample) before an identity-leak check."""
    if isinstance(payload, dict):
        return {k: _measured_part(v) for k, v in payload.items() if k not in STATIC_TEXT}
    if isinstance(payload, list):
        return [_measured_part(v) for v in payload]
    return payload


def assert_no_identity(test, payload, names):
    text = json.dumps(_measured_part(payload)).lower()
    for name in names:
        test.assertNotIn(name.lower(), text)


class TestGC8890BTemplates(unittest.TestCase):
    MIX = ["acetone", "dichloromethane", "hexane", "ethyl_acetate", "benzene", "toluene"]

    def setUp(self):
        self.gc = AgilentGC8890B("gc_tpl", scene_adapter=scene)
        self.meta = export_agilent_gc8890b_contract_metadata()
        self.call = Caller(self, self.gc, self.meta)

    def ready(self, sample_id, oven_K=333.15):
        self.call("power.set", enabled=True)
        self.call("thermal.set_temperature", zone="oven", temperature_K=oven_K)
        self.call("lid.set_angle", angle_rad=1.4)
        self.call("sample.load", sample_id=sample_id)
        self.call("lid.set_angle", angle_rad=0.0)

    def test_elution_order_follows_nist_retention_index_and_identity_is_hidden(self):
        self.gc.register_template_sample("mix", composition_kg={sid: 1e-7 for sid in self.MIX}, truth={"secret": "mix"})
        self.ready("mix")
        chromatogram = self.call("chromatography.measure")["result"]["chromatogram"]
        assert_provenance(self, chromatogram, gc_t.LIB_REL)
        self.assertEqual(chromatogram["sampling"], "template_transport_simulation")
        self.assertEqual(len(chromatogram["peaks"]), len(self.MIX))
        times = chromatogram["retention_time_s"]
        self.assertEqual(times, sorted(times))
        assert_no_identity(self, chromatogram, self.MIX + ["secret"])
        state = self.gc.debug_snapshot()["state"]
        run = gc_t.simulate(self.gc.debug_snapshot()["registered_samples"]["mix"]["template"], oven_K=state["thermal.oven.setpoint_k"],
                            inlet_K=state["thermal.inlet.setpoint_k"], fid_K=state["thermal.fid.setpoint_k"])
        by_time = sorted(run["species"], key=lambda sid: run["species"][sid]["apex_time_s"])
        by_ri = sorted(run["species"], key=lambda sid: run["species"][sid]["retention_index_used"])
        self.assertEqual(by_time, by_ri)
        for detail, peak_time in zip((run["species"][sid] for sid in by_time), times):
            self.assertLess(abs(detail["apex_time_s"] - peak_time), 2.0)
            self.assertLess(abs(detail["residual_kg"]), 1e-18)
        observed = self.gc.read(state_ids=["measurement.chromatogram"])["observations"]["measurement.chromatogram"]["value"]
        self.assertEqual(asset_tool.validate_value(observed, self.meta["state_slots"]["measurement.chromatogram"]["value_schema"]), [])

    def test_hotter_oven_elutes_earlier(self):
        self.gc.register_template_sample("tol", composition_kg={"toluene": 1e-7})
        self.ready("tol", oven_K=333.15)
        cold = self.call("chromatography.measure")["result"]["chromatogram"]["retention_time_s"][0]
        self.call("thermal.set_temperature", zone="oven", temperature_K=393.15)
        hot = self.call("chromatography.measure")["result"]["chromatogram"]["retention_time_s"][0]
        self.assertLess(hot, cold)

    def test_unlit_fid_setpoint_rejects_template_measure_without_side_effects(self):
        self.gc.register_template_sample("tol", composition_kg={"toluene": 1e-7})
        self.ready("tol")
        self.call("thermal.set_temperature", zone="fid", temperature_K=400.0)
        self.call.unchanged("chromatography.measure", "PRECONDITION_FAILED")

    def test_legacy_fixture_path_is_unchanged_and_template_read_is_public(self):
        self.gc.register_sample("fixture")
        self.ready("fixture")
        legacy = self.call("chromatography.measure")["result"]["chromatogram"]
        self.assertTrue(legacy["synthetic"])
        self.assertEqual(legacy["sampling"], "instant_fixture")
        record = self.call("template.read", substance_id="ethyl_acetate")["result"]
        self.assertEqual(record["cas"], "141-78-6")
        self.assertEqual(record["model_fingerprint"]["nonpolar"]["retention_index_used"], 612.0)
        self.call.unchanged("template.read", "INVALID_ARGUMENT", substance_id="not_a_compound")

    def test_registration_rejects_unknown_or_unsupported_species(self):
        for composition, column in (({"unobtainium": 1e-7}, "nonpolar"), ({"glycerol": 1e-7}, "nonpolar"),
                                    ({"toluene": -1.0}, "nonpolar"), ({"caffeine": 1e-7}, "polar"), ({}, "nonpolar")):
            with self.assertRaises(ValueError):
                self.gc.register_template_sample("bad", composition_kg=composition, column=column)
        self.assertNotIn("bad", self.gc.debug_snapshot()["registered_samples"])


class TestHPLC1260Templates(unittest.TestCase):
    def setUp(self):
        self.lc = AgilentHPLC1260Runtime("lc_tpl", scene_adapter=scene)
        self.meta = export_agilent_hplc1260_contract_metadata()
        self.call = Caller(self, self.lc, self.meta)

    def run_method(self, sample_id, probe=False, **changes):
        self.call("power.set", enabled=True)
        self.call("door.set_angle", angle_rad=HALF_PI)
        self.call("sample.load", sample_id=sample_id)
        self.call("door.set_angle", angle_rad=0.0)
        self.call("mobile_phase.set_available", available=True)
        method = copy.deepcopy(self.meta["capabilities"]["chromatography.set_method"]["example_arguments"][0])
        method.update(run_duration_s=1200.0, sample_interval_s=0.5, wavelength_m=230e-9, **changes)
        self.call("chromatography.set_method", **method)
        self.call("chromatography.measure", expect="accepted")
        if probe:
            self.assertEqual(self.call("template.read", substance_id="caffeine")["result"]["substance_id"], "caffeine")
        self.lc.host_advance(self.lc._now + method["run_duration_s"] + 1)
        return self.call("chromatography.get_chromatogram")["result"]["chromatogram"]

    def test_xlogp_order_beer_lambert_area_and_unknown_uv_response(self):
        self.lc.register_template_sample("vial", composition_mol_m3={"caffeine": 1.0, "paracetamol": 1.0, "benzoic_acid": 1.0, "hexane": 1.0})
        chromatogram = self.run_method("vial")
        assert_provenance(self, chromatogram, "assets/reactions/spectral_library/hplc_uv.json", common.UV_REL)
        self.assertTrue(chromatogram["complete"])
        apexes = [p["retention_time_s"] for p in chromatogram["peaks"]]
        self.assertEqual(len(apexes), 3)  # hexane: no NIST UV curve -> no modelled absorbance (and elutes after the run)
        table = common.load_json("assets/reactions/spectral_library/hplc_uv.json")["compounds"]
        expected = sorted(["caffeine", "paracetamol", "benzoic_acid"], key=lambda sid: table[sid]["xlogp"])
        flow = self.meta["capabilities"]["chromatography.set_method"]["example_arguments"][0]["flow_m3_s"]
        for apex, sid in zip(apexes, expected):
            self.assertAlmostEqual(apex, 1.5e-6 / flow * (1 + table[sid]["reversed_phase_C18"]["k_estimate"]), delta=0.5)
        # integral of a Gaussian peak: epsilon * l * n_injected / F, summed over the three UV-active solutes
        injected = 1.0 * 1e-8
        area = sum(common.uv_epsilon(sid, 230e-9) * 0.010 * injected / flow for sid in expected)
        self.assertAlmostEqual(chromatogram["integrated_signal_s"], area, delta=0.01 * area)
        assert_no_identity(self, chromatogram, ["caffeine", "paracetamol", "benzoic", "hexane"])

    def test_double_flow_halves_retention(self):
        self.lc.register_template_sample("vial", composition_mol_m3={"caffeine": 1.0})
        flow = self.meta["capabilities"]["chromatography.set_method"]["example_arguments"][0]["flow_m3_s"]
        slow = self.run_method("vial")["peaks"][0]["retention_time_s"]
        other = AgilentHPLC1260Runtime("lc_tpl2", scene_adapter=scene)
        other.register_template_sample("vial", composition_mol_m3={"caffeine": 1.0})
        self.lc, self.call = other, Caller(self, other, self.meta)
        fast = self.run_method("vial", flow_m3_s=2 * flow)["peaks"][0]["retention_time_s"]
        self.assertAlmostEqual(fast, slow / 2, delta=0.5)

    def test_template_read_is_available_while_running_and_legacy_default_unchanged(self):
        self.lc.register_sample("fixture")
        self.call("power.set", enabled=True)
        record = self.call("template.read", substance_id="caffeine")["result"]
        self.assertEqual(record["retention_evidence"], "derived_assumption")
        self.assertIsNotNone(record["uv_curve"])
        legacy = self.run_method("fixture", probe=True)
        self.assertTrue(legacy["synthetic"])


class TestHS8697Templates(unittest.TestCase):
    def setUp(self):
        self.hs = AgilentHS8697("hs_tpl", scene_adapter=scene, ambient_temperature_K=293.15)
        self.meta = export_agilent_hs8697_contract_metadata()
        self.call = Caller(self, self.hs, self.meta)
        self.hs.register_principal("judge", visibility="evaluator")
        self.judge = self.hs.session("judge")

    def ready(self, sample_id, oven_K=353.15):
        self.call("power.set", enabled=True)
        self.call("lid.set_angle", angle_rad=HALF_PI)
        self.call("sample.load", sample_id=sample_id, slot_id="tray_01")
        self.call("sample.transfer", source_slot="tray_01", target_slot="oven_01")
        self.call("lid.set_angle", angle_rad=0.0)
        for zone in ("oven", "loop_valve", "transfer_line"):
            self.call("thermal.set_temperature", zone=zone, temperature_K=oven_K)
        self.call("method.set_equilibration_time", duration_s=600.0)
        self.hs.register_gc("gc_tpl")

    def test_partition_mass_balance_and_van_t_hoff(self):
        self.hs.register_template_sample("v", volume_m3=20e-6, liquid_volume_m3=5e-6, composition_mol_m3={"toluene": 0.01, "ethanol": 1.0})
        for temperature in (313.15, 353.15):
            eq = self.hs.headspace_transfer("v", temperature_K=temperature)
            beta = 15e-6 / 5e-6
            self.assertAlmostEqual(eq["phase_ratio"], beta)
            for sid, c0 in (("toluene", 0.01), ("ethanol", 1.0)):
                item = eq["species"][sid]
                record = hs_t.get_template(hs_t.LIB_REL, sid)
                hscp = record["henry_hscp_mol_m3_pa"] * math.exp(record["henry_temperature_coefficient_K"] * (1 / temperature - 1 / 298.15))
                self.assertAlmostEqual(item["partition_coefficient_K"], hscp * R * temperature, places=9)
                self.assertAlmostEqual(item["gas_concentration_mol_m3"], c0 / (item["partition_coefficient_K"] + beta), places=12)
                total = item["liquid_concentration_mol_m3"] * 5e-6 + item["gas_concentration_mol_m3"] * 15e-6
                self.assertAlmostEqual(total, c0 * 5e-6, delta=1e-15)
        cold, hot = (self.hs.headspace_transfer("v", temperature_K=t)["species"]["toluene"]["partition_coefficient_K"] for t in (313.15, 353.15))
        self.assertLess(hot, cold)  # B > 0: the analyte moves to the headspace when heated

    def test_template_measure_needs_transfer_config_then_returns_real_data_chromatogram(self):
        self.hs.register_template_sample("v", volume_m3=20e-6, liquid_volume_m3=10e-6,
                                         composition_mol_m3={"toluene": 0.01, "dichloromethane": 0.05, "hexane": 0.001})
        self.ready("v")
        self.call.unchanged("measure", "PRECONDITION_FAILED", oven_slot="oven_01")
        self.hs.configure_template_transfer(loop_volume_m3=1e-6, evidence="assumption")
        result = self.call("measure", oven_slot="oven_01")["result"]
        assert_provenance(self, result, hs_t.LIB_REL, gc_t.LIB_REL)
        self.assertEqual(len(result["chromatogram"]["peaks"]), 3)
        self.assertEqual(result["headspace"]["phase_ratio"], 1.0)
        assert_no_identity(self, result, ["toluene", "dichloromethane", "hexane"])
        for sid, observation in self.judge.read()["observations"].items():
            self.assertEqual(asset_tool.validate_value(observation["value"], self.meta["state_slots"][sid]["value_schema"]), [], sid)
        status = self.hs.read(state_ids=["vial.status"])["observations"]["vial.status"]["value"][0]["status"]
        self.assertEqual(status, "measured_template")
        self.assertFalse(self.hs.read(state_ids=["injection.log"])["observations"]["injection.log"]["value"][-1]["synthetic"])

    def test_record_without_temperature_coefficient_is_not_extrapolated(self):
        self.hs.register_template_sample("v", volume_m3=20e-6, liquid_volume_m3=10e-6, composition_mol_m3={"benzyl_alcohol": 0.01})
        self.hs.configure_template_transfer(loop_volume_m3=1e-6, evidence="assumption")
        self.ready("v")
        self.assertIsNone(self.hs.headspace_transfer("v"))
        self.call.unchanged("measure", "PRECONDITION_FAILED", oven_slot="oven_01")

    def test_headspace_hands_off_to_the_gc_template_path(self):
        self.hs.register_template_sample("v", volume_m3=20e-6, liquid_volume_m3=10e-6, composition_mol_m3={"toluene": 0.01})
        self.hs.configure_template_transfer(loop_volume_m3=1e-6, evidence="assumption")
        self.ready("v")
        loop = {sid: item["loop_mass_kg"] for sid, item in self.hs.headspace_transfer("v")["species"].items()}
        gc = AgilentGC8890B("gc_tpl", scene_adapter=scene)
        gc.register_template_sample("from_hs", composition_kg=loop, injection_fraction=1.0)
        gc_call = Caller(self, gc, export_agilent_gc8890b_contract_metadata())
        gc_call("power.set", enabled=True)
        gc_call("lid.set_angle", angle_rad=1.4)
        gc_call("sample.load", sample_id="from_hs")
        gc_call("lid.set_angle", angle_rad=0.0)
        self.assertEqual(len(gc_call("chromatography.measure")["result"]["chromatogram"]["peaks"]), 1)
        record = self.call("template.read", substance_id="toluene")["result"]
        self.assertEqual(record["gc_retention_id"], "toluene")


class TestDR6000Templates(unittest.TestCase):
    def setUp(self):
        self.dr = HachDR6000("dr_tpl", scene_adapter=scene)
        self.meta = export_hach_dr6000_contract_metadata()
        self.call = Caller(self, self.dr, self.meta)

    def load(self, sample_id):
        self.call("power.set", enabled=True)
        self.call("lid.set_angle", angle_rad=HALF_PI)
        self.call("sample.load", sample_id=sample_id)
        self.call("lid.set_angle", angle_rad=0.0)

    def test_hach_program_absorbance_from_published_sensitivity(self):
        record = dr_t._hach()["hach_prog_490"]
        self.dr.register_template_sample("po4", template_id="hach_prog_490", concentration_kg_m3=1.0e-3)
        self.load("po4")
        self.call.unchanged("spectroscopy.measure", "PRECONDITION_FAILED")  # default full scan: undefined off-wavelength
        self.call("spectroscopy.set_wavelength", wavelength_m=record["wavelength_m"])
        spectrum = self.call("spectroscopy.measure")["result"]["spectrum"]
        assert_provenance(self, spectrum, dr_t.HACH_REL)
        expected = 0.010 * 1.0e-3 / record["sensitivity"]["delta_concentration_kg_m3"]
        self.assertAlmostEqual(spectrum["absorbance"][0], expected, places=12)
        self.assertEqual(spectrum["method_range_status"], "within_range")
        self.call("spectroscopy.set_mode", mode="transmittance")
        spectrum = self.call("spectroscopy.measure")["result"]["spectrum"]
        self.assertAlmostEqual(spectrum["signal"][0], 10 ** -expected, places=12)
        assert_no_identity(self, spectrum, ["phosph", "orthophosphate"])

    def test_nist_uv_and_photochemcad_beer_lambert(self):
        self.dr.register_template_sample("caf", template_id="caffeine", concentration_mol_m3=0.05, path_length_m=0.01)
        self.load("caf")
        self.call("spectroscopy.set_scan", start_m=230e-9, stop_m=290e-9, step_m=5e-9)
        spectrum = self.call("spectroscopy.measure")["result"]["spectrum"]
        for wavelength, absorbance in zip(spectrum["wavelength_m"], spectrum["absorbance"]):
            self.assertAlmostEqual(absorbance, common.uv_epsilon("caffeine", wavelength) * 0.05 * 0.01, places=12)
        self.call("lid.set_angle", angle_rad=HALF_PI)
        self.call("sample.unload")
        self.dr.register_template_sample("r6g", template_id="pcad_083", concentration_mol_m3=0.005)
        self.call("sample.load", sample_id="r6g")
        self.call("lid.set_angle", angle_rad=0.0)
        self.call("spectroscopy.set_scan", start_m=500e-9, stop_m=560e-9, step_m=1e-9)
        spectrum = self.call("spectroscopy.measure")["result"]["spectrum"]
        peak = max(range(len(spectrum["absorbance"])), key=spectrum["absorbance"].__getitem__)
        self.assertAlmostEqual(spectrum["wavelength_m"][peak], 530e-9, delta=2e-9)
        # PhotochemCAD scales Rhodamine 6G to 116000 M^-1 cm^-1 at 529.8 nm -> A = 11600 m^2/mol * 0.005 mol/m^3 * 0.01 m
        self.assertAlmostEqual(spectrum["absorbance"][peak], 0.58, delta=0.01)

    def test_template_read_families_and_rejections(self):
        for template_id, kind in (("hach_prog_490", "hach_method"), ("benzene", "nist_uv_spectrum"), ("pcad_083", "photochemcad_spectrum")):
            self.assertEqual(self.call("template.read", substance_id=template_id)["result"]["template_kind"], kind)
        self.call.unchanged("template.read", "INVALID_ARGUMENT", substance_id="hach_prog_99999")
        with self.assertRaises(ValueError):
            self.dr.register_template_sample("x", template_id="hach_prog_630", concentration_kg_m3=1e-3)  # no sensitivity
        with self.assertRaises(ValueError):
            self.dr.register_template_sample("x", template_id="caffeine", concentration_kg_m3=1e-3)

    def test_legacy_fixture_default_unchanged(self):
        self.dr.register_sample("fixture")
        self.load("fixture")
        self.assertTrue(self.call("spectroscopy.measure")["result"]["spectrum"]["synthetic"])


class TestFTIR4700Templates(unittest.TestCase):
    def setUp(self):
        self.ir = JascoFTIR4700("ir_tpl", scene_adapter=scene)
        self.meta = export_jasco_ftir4700_contract_metadata()
        self.call = Caller(self, self.ir, self.meta)

    def measure(self, sample_id, accessory="atr", resolution=400.0, start=40000.0, stop=400000.0):
        self.call("power.set", enabled=True)
        self.call("lid.set_angle", angle_rad=HALF_PI)
        self.call("accessory.install", mode=accessory)
        self.call("method.configure", start_wavenumber_m_inv=start, stop_wavenumber_m_inv=stop, resolution_m_inv=resolution,
                  scan_count=16, output_mode="absorbance")
        self.call("lid.set_angle", angle_rad=0.0)
        self.call("background.acquire")
        self.call("lid.set_angle", angle_rad=HALF_PI)
        self.call("sample.load", sample_id=sample_id)
        self.call("lid.set_angle", angle_rad=0.0)
        return self.call("measure")

    def test_condensed_phase_reference_resolution_and_identity_hidden(self):
        self.ir.register_template_sample("ea", substance_id="ethyl_acetate", phase="liquid")
        spectrum = self.measure("ea")["result"]["spectrum"]
        assert_provenance(self, spectrum, "assets/reactions/spectral_library/ir_spectra.json")
        self.assertTrue(spectrum["phase_matched"])
        self.assertFalse(spectrum["atr_correction_applied"])
        strongest = spectrum["wavenumber_m_inv"][max(range(len(spectrum["absorbance"])), key=spectrum["absorbance"].__getitem__)] / 100
        self.assertTrue(1200 <= strongest <= 1260 or 1720 <= strongest <= 1760, strongest)  # C-O / C=O stretch
        assert_no_identity(self, spectrum, ["ethyl", "acetate"])
        def maxima(values):
            return sum(1 for i in range(1, len(values) - 1) if values[i] > 0.05 and values[i - 1] < values[i] >= values[i + 1])
        self.call("lid.set_angle", angle_rad=HALF_PI)
        self.call("sample.unload")
        broad = self.measure("ea", resolution=3200.0)["result"]["spectrum"]
        self.assertEqual(broad["effective_resolution_m_inv"], 3200.0)
        self.assertEqual(broad["point_spacing_m_inv"], 1600.0)
        self.assertLess(maxima(broad["absorbance"]), maxima(spectrum["absorbance"]))  # 32 cm^-1 merges neighbouring bands

    def test_gas_phase_and_corrected_overlay(self):
        self.ir.register_template_sample("co2", substance_id="carbon_dioxide", phase="gas")
        spectrum = self.measure("co2", accessory="transmission")["result"]["spectrum"]
        self.assertTrue(spectrum["phase_matched"])
        self.assertIn("GAS", spectrum["reference_state"].upper())
        record = self.call("template.read", substance_id="octane")["result"]
        corrected = [s for s in record["spectra"] if s["corrected_by"]]
        self.assertTrue(corrected)
        self.assertAlmostEqual(corrected[0]["peaks_m_inv"][0][0], 292400.0, delta=1000.0)  # C-H stretch from the k block

    def test_out_of_coverage_range_rejected_and_legacy_unchanged(self):
        self.ir.register_template_sample("ea", substance_id="ethyl_acetate", phase="liquid")
        self.call("power.set", enabled=True)
        self.call("lid.set_angle", angle_rad=HALF_PI)
        self.call("accessory.install", mode="atr")
        self.call("method.configure", start_wavenumber_m_inv=450000.0, stop_wavenumber_m_inv=780000.0, resolution_m_inv=400.0,
                  scan_count=16, output_mode="absorbance")
        self.call("lid.set_angle", angle_rad=0.0)
        self.call("background.acquire")
        self.call("lid.set_angle", angle_rad=HALF_PI)
        self.call("sample.load", sample_id="ea")
        self.call("lid.set_angle", angle_rad=0.0)
        self.call.unchanged("measure", "PRECONDITION_FAILED")
        other = JascoFTIR4700("ir_legacy", scene_adapter=scene)
        other.register_sample("fixture")
        self.ir, self.call = other, Caller(self, other, self.meta)
        self.assertTrue(self.measure("fixture")["result"]["spectrum"]["synthetic"])


class TestEppendorf5910RiTemplates(unittest.TestCase):
    def setUp(self):
        self.host = Eppendorf5910Ri("cf_tpl", scene_adapter=scene)
        self.meta = export_eppendorf_5910ri_contract_metadata()
        self.call = Caller(self, self.host.session(), self.meta)
        self.call.device.debug_snapshot = self.host.debug_snapshot

    def method(self, temperature_k=277.15):
        self.call("power.set", enabled=True)
        self.call("centrifuge.set_method", speed_rad_s=4000 * 2 * math.pi / 60, temperature_k=temperature_k, duration_s=600.0,
                  acceleration_level=9, braking_level=9)

    def test_stokes_estimate_uses_nist_medium_at_method_temperature(self):
        args = dict(particle_id="particle_escherichia_coli_cell", medium_id="medium_water", particle_diameter_m=1e-6, radius_m=0.15, path_length_m=0.03)
        self.call.unchanged("sedimentation.estimate", "PRECONDITION_FAILED", **args)
        self.method()
        result = self.call("sedimentation.estimate", **args)["result"]
        assert_provenance(self, result, "assets/reactions/eppendorf_5910ri_sedimentation_library/templates.json")
        medium = common.load_json("assets/reactions/eppendorf_5910ri_sedimentation_library/templates.json")
        water = next(r for r in medium["templates"] if r["substance_id"] == "medium_water")
        rho = common.interp(277.15, water["temperature_K"], water["density_kg_m3"])
        mu = common.interp(277.15, water["temperature_K"], water["viscosity_Pa_s"])
        omega = 4000 * 2 * math.pi / 60
        velocity = (1100.0 - rho) * 1e-12 * omega ** 2 * 0.15 / (18 * mu)
        self.assertAlmostEqual(result["cases"]["central"]["velocity_m_s"], velocity, places=15)
        self.assertAlmostEqual(result["relative_centrifugal_force"], omega ** 2 * 0.15 / 9.80665)
        self.assertAlmostEqual(result["cases"]["central"]["time_to_traverse_path_s"], 0.03 / velocity)

    def test_floating_ranges_and_temperature_outside_liquid_range(self):
        self.method()
        result = self.call("sedimentation.estimate", particle_id="particle_chylomicron", medium_id="medium_water",
                           particle_diameter_m=2e-7, radius_m=0.15, path_length_m=0.03)["result"]
        self.assertEqual(result["cases"]["high"]["direction"], "floats")
        ldl = self.call("sedimentation.estimate", particle_id="particle_ldl", medium_id="medium_water",
                        particle_diameter_m=2.2e-8, radius_m=0.15, path_length_m=0.03)["result"]
        self.assertEqual(set(ldl["cases"]), {"low", "high"})
        self.call("centrifuge.set_method", speed_rad_s=1000.0, temperature_k=265.15, duration_s=60.0, acceleration_level=1, braking_level=1)
        self.call.unchanged("sedimentation.estimate", "PRECONDITION_FAILED", particle_id="particle_gold", medium_id="medium_water",
                            particle_diameter_m=1e-7, radius_m=0.1, path_length_m=0.01)
        self.call.unchanged("sedimentation.estimate", "INVALID_ARGUMENT", particle_id="medium_water", medium_id="medium_water",
                            particle_diameter_m=1e-7, radius_m=0.1, path_length_m=0.01)

    def test_template_read_and_unsupported_predictor_kept(self):
        self.assertEqual(self.call("template.read", substance_id="particle_gold")["result"]["particle"]["density_kg_m3"], 19300.0)
        self.assertEqual(self.call("template.read", substance_id="medium_water")["result"]["template_kind"], "medium")
        self.assertEqual(self.call("centrifuge.predict_sedimentation", expect="unsupported")["error"]["code"], "UNSUPPORTED")


class TestLibrariesAndMetadata(unittest.TestCase):
    def test_new_libraries_have_required_fields_and_enough_real_records(self):
        minimum = {"assets/reactions/agilent_hs8697_headspace_library/templates.json": 50,
                   "assets/reactions/hach_dr6000_colorimetric_library/templates.json": 50,
                   "assets/reactions/eppendorf_5910ri_sedimentation_library/templates.json": 50}
        for rel, count in minimum.items():
            records = common.load_json(rel)["templates"]
            self.assertGreaterEqual(len(records), count, rel)
            self.assertEqual(len({r["substance_id"] for r in records}), len(records))
            for record in records:
                for key in ("substance_id", "name", "cas", "unit", "evidence", "source", "accessed", "shared_ids"):
                    self.assertIn(key, record, (rel, record["substance_id"], key))
                self.assertIn(record["evidence"], ("measured", "measured_literature", "derived", "assumption"))
        hs = common.load_json("assets/reactions/agilent_hs8697_headspace_library/templates.json")["templates"]
        self.assertGreaterEqual(sum(r["henry_temperature_coefficient_K"] is not None for r in hs), 50)
        hach = common.load_json("assets/reactions/hach_dr6000_colorimetric_library/templates.json")["templates"]
        self.assertGreaterEqual(sum(bool(r["sensitivity"]) and r["wavelength_m"] is not None for r in hach), 50)

    def test_independent_validations_passed(self):
        for slug in LIBRARIES:
            data = json.loads((OUT / slug / "templates" / "output_template_validation.json").read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "passed", slug)
            self.assertTrue(all(check["status"] == "passed" for check in data["checks"]), slug)

    def test_metadata_exposes_template_read_and_schemas_lint(self):
        for export in (export_agilent_gc8890b_contract_metadata, export_agilent_hplc1260_contract_metadata, export_agilent_hs8697_contract_metadata,
                       export_hach_dr6000_contract_metadata, export_jasco_ftir4700_contract_metadata, export_eppendorf_5910ri_contract_metadata):
            meta = export()
            self.assertEqual(meta["capabilities"]["template.read"]["availability"], "implemented")
            for cid, cap in meta["capabilities"].items():
                for field in ("input_schema", "output_schema"):
                    self.assertEqual(asset_tool.lint_schema(cap[field]), [], (cid, field))
                self.assertEqual(asset_tool.validate_value(cap["example_arguments"][0], cap["input_schema"]), [], cid)

    def test_contracts_register_output_templates(self):
        for slug in LIBRARIES:
            contract = json.loads((ROOT / "assets/basic40_batch02/t3_analytical" / slug / "asset.contract.json").read_text(encoding="utf-8"))
            ext = contract["extensions"]["chemistry_lab.output_templates"]
            self.assertEqual(ext["status"], "template", slug)
            self.assertGreaterEqual(ext["n_records"], 50, slug)
            _need(ROOT / ext["validation"])
            self.assertIn("template.read", contract["capabilities"], slug)
            for rel in LIBRARIES[slug]:
                self.assertIn(rel, ext["library"], slug)


if __name__ == "__main__":
    unittest.main()
