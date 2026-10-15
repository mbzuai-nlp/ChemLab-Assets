"""Template-backed (public real data) extensions of the t4_measure electrochemistry/titration assets.

Agent key ``measure_b``: Gamry Reference 620, Metrohm 899 (KF coulometer), Metrohm 914 (pH/DO/conductivity),
Metrohm OMNIS (potentiometric titrator). Libraries live under ``assets/reactions/``; runtime extensions under
``scripts/basic40_templates/``. Run: PYTHONPATH=scripts:tests python -m unittest tests/test_basic40_templates_measure_b.py
"""
from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "scripts", ROOT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from basic40_runtime_measure import (  # noqa: E402
    GAMRY_REFERENCE620_PORTS, GAMRY_REFERENCE620_TYPE, METROHM899_TYPE, METROHM914_TYPE, METROHM_OMNIS_TYPE,
    GamryReference620Runtime, Metrohm899Host, Metrohm914Runtime, MetrohmOmnisRuntime,
    export_gamry_reference620_contract_metadata, export_metrohm899_contract_metadata, export_metrohm914_contract_metadata,
    export_metrohm_omnis_contract_metadata)
from basic40_templates import _acidbase as ab  # noqa: E402
from basic40_templates import gamry_ref620 as gt  # noqa: E402
from basic40_templates import metrohm_899 as kt  # noqa: E402
from basic40_templates import metrohm_914 as mt  # noqa: E402
from basic40_templates import metrohm_omnis as ot  # noqa: E402
from basic40_templates._echem_common import F, LN10, R, matches  # noqa: E402
from template_library import index_by_id, load_library, shared_ids_for  # noqa: E402

EVIDENCE = {"measured", "measured_literature", "derived", "assumption"}
LIBRARIES = {
    "gamry_ref620": "assets/reactions/gamry_ref620_redox_library/templates.json",
    "metrohm_899": "assets/reactions/metrohm_899_water_library/templates.json",
    "metrohm_914": "assets/reactions/metrohm_914_standards_library/templates.json",
    "metrohm_omnis": "assets/reactions/metrohm_omnis_acidbase_library/templates.json",
}
VALIDATIONS = {slug: f"outputs/basic40_batch02/t4_measure/{slug}/templates/output_template_validation.json" for slug in LIBRARIES}
LEGACY_CAPS = {
    "gamry": {"power.set", "slot.set_connection", "electrochem.configure_waveform", "electrochem.start", "electrochem.stop",
              "electrochem.read_series", "electrochem.eis"},
    "kf899": {"power.set", "slot.set_installed", "coulometry.configure", "coulometry.start", "coulometry.stop", "coulometry.reading", "coulometry.curve"},
    "m914": {"power.set", "probe.set_installed", "electrochemistry.select_probe", "electrochemistry.configure_temperature",
             "electrochemistry.configure_endpoint", "acquisition.set_interval", "electrochemistry.calibrate", "acquisition.start",
             "acquisition.stop", "electrochemistry.reading", "acquisition.records"},
    "omnis": {"power.set", "slot.set_connection", "dosing.configure_cylinder", "titration.configure_titrant", "titration.configure_method",
              "dosing.set_rate", "titration.configure_stability", "stirring.set_speed", "titration.start", "titration.stop",
              "titration.read_curve", "titration.calculate_content"},
}
NERNST = R * 298.15 * LN10 / F
RT_F = R * 298.15 / F


class MeasureBLibraryTests(unittest.TestCase):
    """Every library: provenance fields, evidence vocabulary, shared ids, independent validation passed."""

    def test_common_record_fields_and_validation(self):
        for slug, rel in LIBRARIES.items():
            lib = load_library(rel)
            self.assertEqual(lib["schema_version"], "1.0.0", slug)
            index = index_by_id(rel)  # raises on duplicate substance_id
            self.assertEqual(len(index), len(lib["templates"]))
            for rec in lib["templates"]:
                for key in ("substance_id", "record_type", "name", "cas", "unit", "conditions", "evidence", "source", "accessed", "provenance"):
                    self.assertIn(key, rec, (slug, rec.get("substance_id"), key))
                self.assertIn(rec["evidence"], EVIDENCE, (slug, rec["substance_id"]))
                self.assertTrue(rec["source"], rec["substance_id"])
                self.assertIsInstance(rec["provenance"], list)
                if rec.get("cas") and "shared_ids" in rec and rec["record_type"] not in ("solvent_saturation",):
                    self.assertEqual(rec["shared_ids"], {**shared_ids_for(rec["cas"], rec["substance_id"]), **rec["shared_ids"]})
            report = json.loads((ROOT / VALIDATIONS[slug]).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "passed", slug)
            self.assertEqual(report["n_failed"], 0, slug)
            self.assertEqual(report["n_records"], len(lib["templates"]), slug)

    def test_record_counts(self):
        gamry = load_library(LIBRARIES["gamry_ref620"])["templates"]
        self.assertGreaterEqual(sum(r["record_type"] == "redox_couple" for r in gamry), 50)
        self.assertGreaterEqual(sum(bool(r.get("cv_model_eligible")) for r in gamry), 50)
        acid = load_library(LIBRARIES["metrohm_omnis"])["templates"]
        self.assertGreaterEqual(sum(r["record_type"] == "acid_base" for r in acid), 50)
        self.assertGreaterEqual(len(load_library(LIBRARIES["metrohm_899"])["templates"]), 50)

    def test_cc_by_sa_flag_on_wikipedia_records(self):
        for rec in load_library(LIBRARIES["gamry_ref620"])["templates"]:
            if "wikipedia.org" in rec["source"]:
                self.assertIn("CC BY-SA", rec["license"], rec["substance_id"])


# ---------------------------------------------------------------------------------------------------- Gamry
class GamryTemplateTests(unittest.TestCase):
    def setUp(self):
        self.host = GamryReference620Runtime()
        self.host.create_instance("g_a")
        self.host.create_instance("g_b")
        self.host.register_principal("learner", visibility="agent", instance_ids=["g_a"])
        self.host.register_principal("judge", visibility="evaluator")
        self.agent = self.host.session("learner")
        self.judge = self.host.session("judge")
        self.n = 0

    def call(self, cap, args=None, session=None):
        self.n += 1
        return (session or self.agent).invoke(instance_id="g_a", capability_id=cap, arguments={} if args is None else args,
                                              request_id=f"tpl_{self.n}")

    def ok(self, cap, args=None, session=None):
        response = self.call(cap, args, session)
        self.assertEqual(response["status"], "succeeded", response)
        return response["result"]

    def ready(self):
        self.ok("power.set", {"enabled": True})
        for port in GAMRY_REFERENCE620_PORTS:
            self.ok("slot.set_connection", {"port_id": port, "connection_id": "cell_" + port})

    def cell(self, **overrides):
        spec = {"couple_id": "redox_fep3_to_fep2", "oxidant_concentration_mol_m3": 1.0, "reductant_concentration_mol_m3": 0.0,
                "electrode_area_m2": 7.07e-6, "reference_electrode_id": "refel_she"}
        spec.update(overrides)
        return spec

    def test_legacy_metadata_unchanged_and_describe_additive(self):
        meta = export_gamry_reference620_contract_metadata()["asset_types"][GAMRY_REFERENCE620_TYPE]
        self.assertEqual(set(meta["capabilities"]), LEGACY_CAPS["gamry"])
        described = self.agent.describe(instance_id="g_a")["capabilities"]
        self.assertEqual(set(described), LEGACY_CAPS["gamry"] | set(gt.TEMPLATE_CAPABILITY_IDS))
        for cid in gt.TEMPLATE_CAPABILITY_IDS:
            for example in described[cid]["example_arguments"]:
                self.assertTrue(matches(described[cid]["input_schema"], example), cid)
        self.assertNotIn("scheduler.started_at_s", json.dumps(self.agent.describe(instance_id="g_a")))

    def test_template_read_and_list_are_public_and_stateless(self):
        before = self.host.debug_snapshot()
        listing = self.ok("template.list")
        self.assertTrue(matches(gt.LIST_OUTPUT, listing))
        self.assertGreaterEqual(sum(r["cv_model_eligible"] for r in listing["records"]), 50)
        rec = self.ok("template.read", {"substance_id": "redox_fecn6m3_to_fecn6m4"})
        self.assertTrue(matches(gt.READ_OUTPUT, rec))
        self.assertAlmostEqual(rec["record"]["e_standard_v"], 0.3704)
        self.assertEqual(rec["evidence"], "measured_literature")
        self.assertEqual(rec["template_source"], gt.TEMPLATE_REL)
        self.assertEqual(self.call("template.read", {"substance_id": "no_such_couple"})["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.call("template.read", {})["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_preconditions(self):
        scan = {"e_start_v": 1.1, "e_vertex_v": 0.4, "scan_rate_v_s": 0.1}
        self.assertEqual(self.call("electrochem.template_cv", scan)["error"]["code"], "PRECONDITION_FAILED")
        self.host.host_load_template_cell("g_a", self.cell())
        self.assertEqual(self.call("electrochem.template_cv", scan)["error"]["code"], "PRECONDITION_FAILED")  # power/slots
        self.ready()
        self.ok("electrochem.configure_waveform", {"program": {"mode": "potential", "sample_interval_s": 0.1, "values": [0.0, 0.1]}})
        self.ok("electrochem.start")
        self.assertEqual(self.call("electrochem.template_cv", scan)["error"]["code"], "BUSY")
        with self.assertRaises(ValueError):
            self.host.host_load_template_cell("g_a", self.cell())  # playback running
        self.ok("electrochem.stop")
        self.ok("electrochem.template_cv", scan)
        for bad in ({"e_start_v": 0.5, "e_vertex_v": 0.5, "scan_rate_v_s": 0.1}, {"e_start_v": 12, "e_vertex_v": 0, "scan_rate_v_s": 0.1},
                    {"e_start_v": 0.5, "e_vertex_v": 0.0, "scan_rate_v_s": 0}):
            self.assertEqual(self.call("electrochem.template_cv", bad)["error"]["code"], "INVALID_ARGUMENT")

    def test_cell_validation(self):
        for bad in (self.cell(couple_id="redox_cup2_to_cu_s"),  # deposition couple: not soluble/soluble
                    self.cell(couple_id="nope"), self.cell(oxidant_concentration_mol_m3=0.0),
                    self.cell(reference_electrode_id="unknown_ref"),
                    self.cell(couple_id="redox_c6h4o2_to_c6h6o2"),  # proton coupled without pH
                    self.cell(couple_id="redox_fcp_to_fc_acetonitrile"),  # nonaqueous needs its SCE scale
                    {**self.cell(), "extra": 1}):
            with self.assertRaises(ValueError):
                self.host.host_load_template_cell("g_a", bad)
        with self.assertRaises(ValueError):
            self.host.host_load_template_cell("missing", self.cell())

    def test_randles_sevcik_and_reversible_peak_positions(self):
        self.ready()
        d_fe2 = 7.19e-10  # USGS phreeqc.dat -dw for Fe+2 via the pine library
        self.host.host_load_template_cell("g_a", self.cell())
        res = self.ok("electrochem.template_cv", {"e_start_v": 1.1, "e_vertex_v": 0.45, "scan_rate_v_s": 0.1})
        self.assertTrue(matches(gt.CV_OUTPUT, res))
        expected = gt.randles_sevcik(1, 7.07e-6, 1.0, d_fe2, 0.1, 298.15)
        self.assertAlmostEqual(res["randles_sevcik_peak_current_a"], -expected, delta=1e-12)
        self.assertAlmostEqual(res["cathodic_peak"]["current_a"] / -expected, 1.0, delta=0.01)
        self.assertAlmostEqual(res["cathodic_peak"]["potential_v"] - 0.771, -1.109 * RT_F, delta=0.0015)
        self.assertAlmostEqual(res["peak_separation_v"], 2.218 * RT_F, delta=0.003)
        self.assertAlmostEqual(res["half_wave_potential_v"], 0.771, delta=0.0015)
        self.assertFalse(res["synthetic"])
        self.assertIn("assumption", res["evidence"]["diffusion_ratio"])
        self.assertEqual(res["evidence"]["standard_potential"], "measured_literature")
        self.assertEqual(len(res["curve"]), 2 * res["scan"]["points_per_segment"] + 1)
        self.assertFalse(res["current_overload"])

    def test_reference_electrode_conversion_and_ph_dependence(self):
        self.ready()
        self.host.host_load_template_cell("g_a", self.cell(reference_electrode_id="refel_sce_sat_kcl"))
        sce = self.ok("electrochem.template_cv", {"e_start_v": 0.9, "e_vertex_v": 0.2, "scan_rate_v_s": 0.1})
        self.assertAlmostEqual(sce["half_wave_potential_v"], 0.771 - 0.241, delta=0.0015)
        self.assertEqual(sce["reference_electrode_id"], "refel_sce_sat_kcl")
        # quinone/hydroquinone: 2 H+ / 2 e-, -59 mV per pH
        q = {"couple_id": "redox_c6h4o2_to_c6h6o2", "diffusion_override_m2_s": {"oxidant": 9e-10, "reductant": 9e-10}}
        halves = []
        for ph in (2.0, 4.0):
            self.host.host_load_template_cell("g_a", self.cell(**q, ph=ph))
            halves.append(self.ok("electrochem.template_cv", {"e_start_v": 1.0, "e_vertex_v": 0.0, "scan_rate_v_s": 0.05})["half_wave_potential_v"])
        self.assertAlmostEqual(halves[0], 0.6992 - 2 * NERNST, delta=0.0015)
        self.assertAlmostEqual(halves[1] - halves[0], -2 * NERNST, delta=0.0015)

    def test_missing_diffusion_reports_potentials_but_no_current(self):
        self.ready()
        self.host.host_load_template_cell("g_a", self.cell(couple_id="redox_fecn6m3_to_fecn6m4"))  # no public D in the USGS table
        res = self.ok("electrochem.template_cv", {"e_start_v": 0.8, "e_vertex_v": 0.0, "scan_rate_v_s": 0.1})
        self.assertIsNone(res["randles_sevcik_peak_current_a"])
        self.assertIsNone(res["cathodic_peak"]["current_a"])
        self.assertTrue(all(p["current_a"] is None for p in res["curve"]))
        self.assertAlmostEqual(res["half_wave_potential_v"], 0.3704, delta=0.0015)
        self.assertIn("missing", res["evidence"]["diffusion_coefficient"])
        self.host.host_load_template_cell("g_a", self.cell(couple_id="redox_fecn6m3_to_fecn6m4",
                                                           diffusion_override_m2_s={"oxidant": 7.6e-10, "reductant": 6.3e-10}))
        res = self.ok("electrochem.template_cv", {"e_start_v": 0.8, "e_vertex_v": 0.0, "scan_rate_v_s": 0.1})
        self.assertAlmostEqual(res["cathodic_peak"]["current_a"] / res["randles_sevcik_peak_current_a"], 1.0, delta=0.01)
        self.assertEqual(res["evidence"]["diffusion_coefficient"], "host_assumption")
        # E1/2 shifts by (RT/nF) ln sqrt(D_R/D_O)
        self.assertAlmostEqual(res["half_wave_potential_v"], 0.3704 + 0.5 * R * 298.15 / F * math.log(6.3 / 7.6), delta=0.0015)

    def test_ohmic_drop_capacitance_and_nonaqueous_ferrocene(self):
        self.ready()
        scan = {"e_start_v": 1.3, "e_vertex_v": 0.25, "scan_rate_v_s": 0.5}
        self.host.host_load_template_cell("g_a", self.cell(oxidant_concentration_mol_m3=10.0))
        base = self.ok("electrochem.template_cv", scan)
        self.host.host_load_template_cell("g_a", self.cell(oxidant_concentration_mol_m3=10.0, uncompensated_resistance_ohm=200.0))
        ohmic = self.ok("electrochem.template_cv", scan)
        self.assertGreater(ohmic["peak_separation_v"], base["peak_separation_v"] + 0.01)
        self.host.host_load_template_cell("g_a", self.cell(double_layer_capacitance_f_m2=0.2))
        cap = self.ok("electrochem.template_cv", scan)
        self.assertAlmostEqual(cap["curve"][1]["current_a"], -0.2 * 7.07e-6 * 0.5, delta=1e-9)
        self.host.host_load_template_cell("g_a", self.cell(couple_id="redox_fcp_to_fc_acetonitrile", oxidant_concentration_mol_m3=0.0,
                                                           reductant_concentration_mol_m3=1.0, reference_electrode_id="refel_sce_sat_kcl",
                                                           diffusion_override_m2_s={"oxidant": 2.4e-9, "reductant": 2.4e-9}))
        fc = self.ok("electrochem.template_cv", {"e_start_v": 0.0, "e_vertex_v": 0.8, "scan_rate_v_s": 0.1})
        self.assertAlmostEqual(fc["half_wave_potential_v"], 0.40, delta=0.0015)
        self.assertGreater(fc["anodic_peak"]["current_a"], 0)

    def test_truth_filtering_dedup_and_isolation(self):
        self.ready()
        self.host.host_load_template_cell("g_a", self.cell())
        scan = {"e_start_v": 1.1, "e_vertex_v": 0.45, "scan_rate_v_s": 0.1}
        agent = self.call("electrochem.template_cv", scan)
        text = json.dumps(agent)
        self.assertIsNone(agent["result"]["truth"])
        for secret in ("redox_fep3_to_fep2", "oxidant_concentration_mol_m3", "Fe^3+"):
            self.assertNotIn(secret, text)
        self.assertNotIn("redox_fep3_to_fep2", json.dumps(self.agent.logs()))
        again = self.agent.invoke(instance_id="g_a", capability_id="electrochem.template_cv", arguments=scan, request_id=agent["request_id"])
        self.assertEqual(again, agent)
        judged = self.ok("electrochem.template_cv", scan, session=self.judge)
        self.assertEqual(judged["truth"]["couple_id"], "redox_fep3_to_fep2")
        self.assertEqual(self.agent.invoke(instance_id="g_b", capability_id="template.list", arguments={}, request_id="x")["error"]["code"],
                         "UNKNOWN_INSTANCE")
        before = self.host.debug_snapshot()
        self.ok("electrochem.template_cv", scan)
        self.assertEqual(before, self.host.debug_snapshot())  # analysis does not advance time or state



# ---------------------------------------------------------------------------------------------------- Metrohm 899
class Metrohm899TemplateTests(unittest.TestCase):
    def setUp(self):
        self.host = Metrohm899Host()
        self.host.create_instance("kf_a")
        self.host.create_instance("kf_b")
        self.host.register_principal("operator", visibility="agent", instance_ids=["kf_a"])
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("operator"), self.host.session("judge")
        self.n = 0

    def call(self, cap, session=None, **args):
        self.n += 1
        return (session or self.agent).invoke(instance_id="kf_a", capability_id=cap, arguments=args, request_id=f"kf_tpl_{self.n}")

    def ok(self, cap, session=None, **args):
        response = self.call(cap, session, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response["result"]

    def value(self, sid):
        return self.judge.read(instance_id="kf_a", state_ids=[sid])["observations"][sid]["value"]

    def run_to_end(self, substance_id, mass, temperature_k=None, drift=0.0):
        for name in ("cell", "generator", "indicator"):
            self.ok("slot.set_installed", slot=name, installed=True)
        self.host.load_template_sample(instance_id="kf_a", substance_id=substance_id, sample_mass_kg=mass, background_drift_kg_s=drift,
                                       reagent_ready=True, request_id="tpl_sample", temperature_k=temperature_k)
        self.ok("coulometry.configure", current_a=0.4, drift_compensation_kg_s=drift, endpoint_hold_s=1.0, sample_mass_kg=mass)
        self.ok("power.set", enabled=True)
        self.ok("coulometry.start")
        for _ in range(400):
            self.host.step(1.0)
            if self.value("coulometry.status") == "COMPLETED":
                break
        self.assertEqual(self.value("coulometry.status"), "COMPLETED")

    def test_legacy_metadata_unchanged_and_describe_additive(self):
        self.assertEqual(set(export_metrohm899_contract_metadata()["capabilities"]), LEGACY_CAPS["kf899"])
        described = self.agent.describe(instance_id="kf_a")
        self.assertEqual(set(described["capabilities"]), LEGACY_CAPS["kf899"] | set(kt.TEMPLATE_CAPABILITY_IDS))
        self.assertNotIn('"truth.', json.dumps(described))

    def test_hydrate_stoichiometry_titrated_through_faraday(self):
        rec = self.ok("template.read", substance_id="copper_ii_sulfate_pentahydrate", temperature_k=None)
        w = rec["record"]["water_mass_fraction"]
        self.assertAlmostEqual(w, 5 * 18.015 / 249.68, delta=2e-4)  # CuSO4·5H2O
        self.assertEqual(rec["record"]["evidence"], "derived")
        mass = 1e-5  # 10 mg hydrate -> 3.6 mg water
        self.run_to_end("copper_ii_sulfate_pentahydrate", mass)
        self.assertAlmostEqual(self.value("truth.sample_water_kg"), w * mass, places=15)
        report = self.ok("coulometry.template_report")
        self.assertTrue(matches(kt.REPORT_OUTPUT, report))
        self.assertTrue(report["template_backed"])
        self.assertFalse(report["synthetic"])
        self.assertAlmostEqual(report["water_mass_kg"], w * mass, delta=1e-12)
        self.assertAlmostEqual(report["charge_c"], w * mass * 2 * F / 0.01801528, delta=1e-6)
        self.assertAlmostEqual(report["water_fraction"], w, delta=1e-9)
        self.assertIsNone(report["truth"])
        text = json.dumps([report, self.agent.logs(), self.agent.read(instance_id="kf_a")])
        self.assertNotIn("copper", text)
        judged = self.ok("coulometry.template_report", session=self.judge)
        self.assertEqual(judged["truth"]["substance_id"], "copper_ii_sulfate_pentahydrate")
        self.assertAlmostEqual(judged["truth"]["expected_charge_c"], report["charge_c"], delta=1e-6)

    def test_kf_standards_and_saturated_solvent(self):
        tartrate = self.ok("template.read", substance_id="disodium_tartrate_dihydrate", temperature_k=None)
        self.assertAlmostEqual(tartrate["water_mass_fraction_at_temperature"], 0.1566, delta=5e-5)
        self.assertEqual(self.ok("template.read", substance_id="water", temperature_k=None)["water_mass_fraction_at_temperature"], 1.0)
        benzene = kt.material("benzene_water_saturated")
        t0, w0 = benzene["temperature_k"][0], benzene["water_mass_fraction"][0]
        self.assertEqual(kt.water_fraction(benzene, t0), (w0, "tabulated"))
        mid = (benzene["temperature_k"][0] + benzene["temperature_k"][1]) / 2
        self.assertAlmostEqual(kt.water_fraction(benzene, mid)[0], (benzene["water_mass_fraction"][0] + benzene["water_mass_fraction"][1]) / 2)
        with self.assertRaises(ValueError):
            kt.water_fraction(benzene, None)
        with self.assertRaises(ValueError):
            kt.water_fraction(benzene, 500.0)
        self.assertIsNone(self.ok("template.read", substance_id="benzene_water_saturated", temperature_k=None)["water_mass_fraction_at_temperature"])
        self.run_to_end("benzene_water_saturated", 0.005, temperature_k=t0)
        self.assertAlmostEqual(self.ok("coulometry.template_report")["water_mass_kg"], w0 * 0.005, delta=1e-12)

    def test_host_validation_replay_and_legacy_replacement(self):
        for kwargs in ({"substance_id": "nope", "sample_mass_kg": 0.001}, {"substance_id": "water", "sample_mass_kg": 0.0},
                       {"substance_id": "benzene_water_saturated", "sample_mass_kg": 0.001}):
            with self.assertRaises(ValueError):
                self.host.load_template_sample(instance_id="kf_a", background_drift_kg_s=0.0, reagent_ready=True, request_id="bad", **kwargs)
        args = dict(instance_id="kf_a", substance_id="water", sample_mass_kg=1e-5, background_drift_kg_s=0.0, reagent_ready=True, request_id="r1")
        self.host.load_template_sample(**args)
        snapshot = self.host.debug_snapshot()
        self.host.load_template_sample(**args)  # idempotent replay
        self.assertEqual(snapshot, self.host.debug_snapshot())
        with self.assertRaises(ValueError):
            self.host.load_template_sample(**{**args, "sample_mass_kg": 2e-5})
        self.assertTrue(self.ok("coulometry.template_report")["template_backed"])
        self.host.load_sample(instance_id="kf_a", water_mass_kg=1e-6, background_drift_kg_s=0.0, reagent_ready=True, request_id="legacy")
        report = self.ok("coulometry.template_report")
        self.assertFalse(report["template_backed"])
        self.assertTrue(report["synthetic"])
        self.assertIsNone(report["template_source"])

    def test_list_read_errors_and_stateless(self):
        before = self.host.debug_snapshot()
        listing = self.ok("template.list")
        self.assertTrue(matches(kt.LIST_OUTPUT, listing))
        self.assertGreaterEqual(len(listing["records"]), 50)
        self.assertEqual(self.call("template.read", substance_id="unknown")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.call("template.read")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(before, self.host.debug_snapshot())


# ---------------------------------------------------------------------------------------------------- Metrohm 914
class Metrohm914TemplateTests(unittest.TestCase):
    def setUp(self):
        self.host = Metrohm914Runtime()
        self.host.create_instance("m_a")
        self.host.create_instance("m_b")
        self.host.register_principal("learner", visibility="agent", instance_ids=["m_a"])
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.n = 0

    def call(self, cap, session=None, **args):
        self.n += 1
        return (session or self.agent).invoke(instance_id="m_a", capability_id=cap, arguments=args, request_id=f"m_tpl_{self.n}")

    def ok(self, cap, session=None, **args):
        response = self.call(cap, session, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response["result"]

    def prepare(self, slot="ph"):
        self.ok("probe.set_installed", slot=slot, installed=True)
        self.ok("electrochemistry.select_probe", slot=slot)
        self.ok("electrochemistry.configure_endpoint", hold_s=2, tolerance_fraction=0)
        self.ok("power.set", enabled=True)

    def settle(self, solution):
        for _ in range(3):
            self.host.host_step_template(self.host.time_s + 1, solutions={"m_a": solution})
        return self.ok("electrochemistry.reading")

    def test_legacy_metadata_unchanged_and_describe_additive(self):
        meta = export_metrohm914_contract_metadata()["asset_types"][METROHM914_TYPE]
        self.assertEqual(set(meta["capabilities"]), LEGACY_CAPS["m914"])
        described = self.agent.describe(instance_id="m_a")
        self.assertEqual(set(described["capabilities"]), LEGACY_CAPS["m914"] | set(mt.TEMPLATE_CAPABILITY_IDS))
        self.assertFalse(any(n.startswith("truth.") for n in described["state_slots"]))

    def test_nist_buffer_reading_and_calibration_with_certificate_value(self):
        self.prepare()
        buffer = {"kind": "ph_buffer", "substance_id": "nist_srm_185i_khp_buffer", "temperature_k": 298.15, "standard_channel": "ph"}
        reading = self.settle(buffer)
        self.assertEqual(reading["ph"], 4.005)  # NIST SRM 185i certified pH(S) at 25 °C
        self.assertTrue(reading["stable"])
        public = self.ok("template.read", substance_id="nist_srm_185i_khp_buffer", temperature_k=310.15, pressure_pa=None, salinity=None)
        self.assertEqual(public["value_at_temperature"], 4.025)
        self.assertEqual(public["record"]["evidence"], "measured")
        self.assertEqual(self.call("electrochemistry.calibrate", slot="ph", standard_value=4.0)["error"]["code"], "PRECONDITION_FAILED")
        self.ok("electrochemistry.calibrate", slot="ph", standard_value=4.005)
        # 37 °C buffer: certificate 4.025; 41.5 °C interpolates between 40 and 45 °C
        self.assertEqual(self.settle({**buffer, "temperature_k": 310.15})["ph"], 4.025)
        self.assertAlmostEqual(mt.buffer_ph(mt.standard("nist_srm_185i_khp_buffer"), 314.65), 4.031 + 0.3 * (4.044 - 4.031), places=12)
        with self.assertRaises(ValueError):
            mt.build_sample({**buffer, "temperature_k": 330.0})  # outside certificate range: no extrapolation
        report = self.ok("electrochemistry.template_reading")
        self.assertTrue(matches(mt.REPORT_OUTPUT, report))
        self.assertTrue(report["template_backed"])
        self.assertFalse(report["synthetic"])
        self.assertEqual(report["evidence"]["ph"], "measured")
        self.assertIsNone(report["truth"])
        self.assertNotIn("185i", json.dumps([report] + [row for row in self.agent.logs() if row["capability_id"] != "template.read"]))
        self.assertEqual(self.ok("electrochemistry.template_reading", session=self.judge)["truth"]["solution"]["substance_id"], "nist_srm_185i_khp_buffer")

    def test_weak_acid_solution_ph_from_shared_pka_table(self):
        self.prepare()
        acetic = {"kind": "solution", "components": [{"substance_id": "acetic_acid", "concentration_mol_m3": 100.0}], "temperature_k": 298.15}
        ka = 10 ** -4.756
        h = (-ka + math.sqrt(ka * ka + 4 * ka * 0.1)) / 2  # Kw negligible
        self.assertAlmostEqual(self.settle(acetic)["ph"], round(-math.log10(h), 3), delta=0.0011)
        buffer = {"kind": "solution", "temperature_k": 298.15,
                  "components": [{"substance_id": "phosphoric_acid", "concentration_mol_m3": 25.0, "protons_as_added": 2},
                                 {"substance_id": "acetic_acid", "concentration_mol_m3": 0.0}]}
        sample = mt.build_sample(buffer)["sample"]
        h, c = 10 ** -sample["ph"], 0.025  # independent charge balance of NaH2PO4 (Na+ counter-ion) at the returned pH
        k1, k2, k3 = (10 ** -x for x in (2.148, 7.198, 12.35))
        d = h ** 3 + k1 * h * h + k1 * k2 * h + k1 * k2 * k3
        anions = c * (k1 * h * h + 2 * k1 * k2 * h + 3 * k1 * k2 * k3) / d
        self.assertAlmostEqual((h + c - 10 ** -13.997 / h - anions) / c, 0.0, delta=1e-6)
        ammonia = mt.build_sample({"kind": "solution", "temperature_k": 298.15, "components": [{"substance_id": "ammonia", "concentration_mol_m3": 100.0}]})
        self.assertAlmostEqual(ammonia["sample"]["ph"], 13.997 - 0.5 * ((13.997 - 9.245) - math.log10(0.1)), delta=0.01)  # weak base
        strong = mt.build_sample({"kind": "solution", "temperature_k": 298.15,
                                  "strong_electrolytes": [{"substance_id": "titrant_hydrochloric_acid", "concentration_mol_m3": 10.0}]})
        self.assertAlmostEqual(strong["sample"]["ph"], 2.0, delta=1e-6)
        warm = mt.build_sample({**acetic, "temperature_k": 323.15})
        self.assertIn("acetic_acid", warm["sources"]["ph"]["pka_models"])
        self.assertNotEqual(warm["sample"]["ph"], mt.build_sample(acetic)["sample"]["ph"])

    def test_conductivity_standard_and_dissolved_oxygen(self):
        self.prepare("conductance")
        kcl = {"kind": "conductivity_standard", "substance_id": "kcl_0p01_mol_kg_conductivity_wu1991", "temperature_k": 298.15, "cell_constant_m_inv": 100.0}
        reading = self.settle(kcl)
        self.assertAlmostEqual(reading["conductance_s"], 0.140823 / 100.0, places=12)
        with self.assertRaises(ValueError):
            mt.build_sample({**kcl, "cell_constant_m_inv": None})
        lot = mt.standard("nist_srm_3191_kcl_conductivity")
        self.assertAlmostEqual(mt.conductivity_s_m(lot, 298.15), lot["conductivity_s_m"][0])
        with self.assertRaises(ValueError):
            mt.conductivity_s_m(lot, 303.15)  # certified alpha only valid at 25 ± 0.5 °C
        model = mt.do_model_record()
        worst = max(abs(mt.do_saturation_kg_m3(cp["temperature_k"], cp["pressure_pa"], cp["salinity_permil"]) - cp["do_kg_m3"]) for cp in model["check_points"])
        self.assertLess(worst, 5.1e-6)  # USGS DOTABLES output is rounded to 0.01 mg/L
        self.assertAlmostEqual(mt.do_saturation_kg_m3(298.15), 8.26e-3, delta=1e-5)
        sample = mt.build_sample({"kind": "water", "temperature_k": 298.15, "do_saturation_fraction": 0.5})["sample"]
        self.assertAlmostEqual(sample["do_kg_m3"], 0.5 * mt.do_saturation_kg_m3(298.15))
        with self.assertRaises(ValueError):
            mt.build_sample({"kind": "water", "temperature_k": 298.15})  # nothing template-backed

    def test_legacy_sample_clears_template_and_errors(self):
        self.prepare()
        self.settle({"kind": "ph_buffer", "substance_id": "nist_srm_187f_borax_buffer", "temperature_k": 298.15})
        self.assertTrue(self.ok("electrochemistry.template_reading")["template_backed"])
        legacy = {"ph": 7.0, "conductance_s": None, "do_kg_m3": None, "temperature_k": 298.15, "reference_ph": 7.0, "reference_potential_v": 0.0}
        self.host.host_step(self.host.time_s + 1, inputs={"m_a": legacy})
        report = self.ok("electrochemistry.template_reading")
        self.assertFalse(report["template_backed"])
        self.assertTrue(report["synthetic"])
        with self.assertRaises(ValueError):
            self.host.host_step_template(self.host.time_s + 1, solutions={"m_a": {"kind": "ph_buffer", "substance_id": "kcl_0p01_mol_kg_conductivity_wu1991", "temperature_k": 298.15}})
        with self.assertRaises(ValueError):
            self.host.host_step_template(self.host.time_s + 1, solutions={"m_a": {"kind": "ph_buffer", "temperature_k": 298.15}}, inputs={"m_a": legacy})
        self.assertEqual(self.call("template.read", substance_id="nope", temperature_k=None, pressure_pa=None, salinity=None)["error"]["code"], "INVALID_ARGUMENT")
        acid = self.ok("template.read", substance_id="acetic_acid", temperature_k=None, pressure_pa=None, salinity=None)
        self.assertEqual(acid["pka_at_temperature"], [4.756])
        self.assertGreaterEqual(len(self.ok("template.list")["records"]), 50)


# ---------------------------------------------------------------------------------------------------- Metrohm OMNIS
class MetrohmOmnisTemplateTests(unittest.TestCase):
    def setUp(self):
        self.host = MetrohmOmnisRuntime()
        self.host.create_instance("o_a")
        self.host.create_instance("o_b")
        self.host.register_principal("student", visibility="agent", instance_ids=["o_a"])
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("student"), self.host.session("judge")
        self.n = 0

    def call(self, cap, session=None, **args):
        self.n += 1
        return (session or self.agent).invoke(instance_id="o_a", capability_id=cap, arguments=args, request_id=f"o_tpl_{self.n}")

    def ok(self, cap, session=None, **args):
        response = self.call(cap, session, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response["result"]

    def titrate(self, analyte, *, concentration=100.0, maximum=1.5e-5, until=3000):
        self.ok("slot.set_connection", port_id="electrode", connection_id="electrode_01")
        self.ok("slot.set_connection", port_id="titration_cell", connection_id="beaker_01")
        self.ok("dosing.configure_cylinder", volume_m3=2e-5)
        self.ok("titration.configure_titrant", concentration_mol_m3=concentration)
        self.ok("titration.configure_method", mode="dynamic_equivalent", dose_increment_m3=1e-7, maximum_volume_m3=maximum, endpoint_potential_v=None)
        self.ok("dosing.set_rate", rate_m3_s=5e-7)
        self.ok("titration.configure_stability", hold_s=0.5, tolerance_v=0.0)
        self.ok("stirring.set_speed", angular_speed_rad_s=10.0)
        self.ok("power.set", enabled=True)
        self.host.host_load_template_sample("o_a", analyte)
        self.ok("titration.start")
        self.host.host_step(until)
        return self.ok("titration.read_curve")

    def test_legacy_metadata_unchanged_and_describe_additive(self):
        meta = export_metrohm_omnis_contract_metadata()["asset_types"][METROHM_OMNIS_TYPE]
        self.assertEqual(set(meta["capabilities"]), LEGACY_CAPS["omnis"])
        described = self.agent.describe(instance_id="o_a")
        self.assertEqual(set(described["capabilities"]), LEGACY_CAPS["omnis"] | set(ot.TEMPLATE_CAPABILITY_IDS))
        text = json.dumps(described)
        for hidden in ('"truth.', '"acid_moles"', '"reference_potential_v"'):
            self.assertNotIn(hidden, text)

    def test_weak_acid_curve_equivalence_and_content(self):
        curve = self.titrate({"components": [{"substance_id": "acetic_acid", "amount_mol": 0.001}], "volume_m3": 2.5e-5,
                              "titrant_id": "titrant_sodium_hydroxide"})
        self.assertEqual(curve["status"], "completed")
        self.assertAlmostEqual(curve["endpoint_volume_m3"], 1.0e-5, delta=0.02e-5)
        half = min(curve["samples"], key=lambda p: abs(p["volume_m3"] - 0.5e-5))
        self.assertAlmostEqual(half["ph"], 4.756, delta=0.01)  # half-equivalence pH = pKa (ideal)
        content = self.ok("titration.calculate_content", sample_volume_m3=2.5e-5, stoichiometric_ratio=1)
        self.assertAlmostEqual(content["content_mol_m3"], 40.0, delta=0.2)
        report = self.ok("titration.template_report")
        self.assertTrue(matches(ot.REPORT_OUTPUT, report))
        self.assertTrue(report["template_backed"])
        self.assertFalse(report["synthetic"])
        self.assertEqual(report["titrant_id"], "titrant_sodium_hydroxide")
        self.assertIsNone(report["truth"])
        self.assertNotIn("acetic", json.dumps([report, self.agent.logs(), curve]))
        judged = self.ok("titration.template_report", session=self.judge)
        self.assertAlmostEqual(judged["truth"]["expected_equivalence_volumes_m3"][0], 1.0e-5)

    def test_primary_standard_base_titrated_with_acid_and_polyprotic(self):
        curve = self.titrate({"components": [{"substance_id": "srm_84l_potassium_hydrogen_phthalate", "mass_kg": 2.0422e-4}],
                              "volume_m3": 5e-5, "titrant_id": "titrant_sodium_hydroxide"})
        expected = 2.0422e-4 * 0.999934 / 0.20422 / 100.0
        self.assertAlmostEqual(curve["endpoint_volume_m3"], expected, delta=0.02e-5)
        self.setUp()
        curve = self.titrate({"components": [{"substance_id": "ammonia", "amount_mol": 0.001}], "volume_m3": 2.5e-5,
                              "titrant_id": "titrant_hydrochloric_acid"})
        self.assertAlmostEqual(curve["endpoint_volume_m3"], 1.0e-5, delta=0.02e-5)
        self.assertLess(curve["samples"][-1]["ph"], curve["samples"][0]["ph"])
        self.setUp()
        curve = self.titrate({"components": [{"substance_id": "phosphoric_acid", "amount_mol": 0.0005}], "volume_m3": 2.5e-5,
                              "titrant_id": "titrant_sodium_hydroxide"})
        self.assertAlmostEqual(curve["endpoint_volume_m3"], 0.5e-5, delta=0.02e-5)  # first equivalence point H3PO4 -> H2PO4-

    def test_template_read_temperature_and_validation(self):
        acetic = self.ok("template.read", substance_id="acetic_acid", temperature_k=None)
        self.assertEqual(acetic["pka_at_temperature"], [4.756])
        warm = self.ok("template.read", substance_id="tris", temperature_k=310.15)
        self.assertLess(warm["pka_at_temperature"][0], 8.072)  # Tris pK falls ~0.03/K (ΔH > 0)
        self.assertEqual(warm["temperature_models"], ["goldberg_eq12_dh_dcp"])
        water = self.ok("template.read", substance_id="water", temperature_k=323.15)
        self.assertAlmostEqual(water["pkw_at_temperature"], 13.26, delta=0.02)
        std = self.ok("template.read", substance_id="srm_84l_potassium_hydrogen_phthalate", temperature_k=None)
        self.assertEqual(std["record"]["certified_mass_fraction"], 0.999934)
        self.assertEqual(self.call("template.read", substance_id="nope", temperature_k=None)["error"]["code"], "INVALID_ARGUMENT")
        for bad in ({"components": [{"substance_id": "acetic_acid", "amount_mol": 1e-3}], "volume_m3": 2.5e-5, "titrant_id": "acetic_acid"},
                    {"components": [{"substance_id": "acetic_acid"}], "volume_m3": 2.5e-5, "titrant_id": "titrant_sodium_hydroxide"},
                    {"components": [{"substance_id": "acetic_acid", "amount_mol": 1e-3, "mass_kg": 1e-3}], "volume_m3": 2.5e-5, "titrant_id": "titrant_sodium_hydroxide"},
                    {"components": [{"substance_id": "acetic_acid", "amount_mol": 1e-3, "protons_as_added": 3}], "volume_m3": 2.5e-5, "titrant_id": "titrant_sodium_hydroxide"}):
            with self.assertRaises(ValueError):
                self.host.host_load_template_sample("o_a", bad)

    def test_legacy_sample_clears_template(self):
        self.host.host_load_template_sample("o_a", {"components": [{"substance_id": "acetic_acid", "amount_mol": 0.001}],
                                                   "volume_m3": 2.5e-5, "titrant_id": "titrant_sodium_hydroxide"})
        self.assertTrue(self.ok("titration.template_report")["template_backed"])
        self.host.host_load_sample("o_a", {"volume_m3": 2.5e-5, "acid_moles": 0.001, "temperature_k": 298.15, "reference_ph": 7.0, "reference_potential_v": 0.0})
        report = self.ok("titration.template_report")
        self.assertFalse(report["template_backed"])
        self.assertTrue(report["synthetic"])



class MeasureBContractTests(unittest.TestCase):
    """Patched contracts declare exactly the runtime's template capability schemas (skipped before patching)."""

    def test_contract_capabilities_match_runtime_metadata(self):
        modules = {"gamry_ref620": gt, "metrohm_899": kt, "metrohm_914": mt, "metrohm_omnis": ot}
        for slug, module in modules.items():
            contract = json.loads((ROOT / f"assets/basic40_batch02/t4_measure/{slug}/asset.contract.json").read_text(encoding="utf-8"))
            ext = contract["extensions"].get("chemistry_lab.output_templates")
            if ext is None:
                self.skipTest(f"{slug} contract not patched yet")
            self.assertEqual(ext["status"], "template")
            meta = module.capability_metadata()
            for cid in module.TEMPLATE_CAPABILITY_IDS:
                cap = contract["capabilities"][cid]
                self.assertEqual(cap["input_schema"], meta[cid]["input_schema"], (slug, cid))
                self.assertEqual(cap["output_schema"], meta[cid]["output_schema"], (slug, cid))
                self.assertTrue(matches(cap["input_schema"], cap["example_arguments"]), (slug, cid))
                self.assertEqual(cap["availability"], "implemented")
                for component in cap["component_ids"]:
                    self.assertIn(cid, contract["components"][component]["capability_ids"])
            for aid in ("template_library", "template_module", "template_validation"):
                self.assertIn(aid, contract["artifacts"], slug)


if __name__ == "__main__":
    unittest.main()
