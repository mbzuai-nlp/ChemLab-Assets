"""Real-data template tests for the basic40_batch02 t2_vacuum tier (2026-10).

ika_rv10            -> assets/reactions/ika_rv10_evaporation_library (NIST Antoine/Tboil/dHvap, NIST EOS/PubChem density)
buchi_lyovapor_l250 -> assets/reactions/buchi_lyovapor_l250_formulation_library (literature Tg'/Tc/Te, Murphy & Koop ice)
biotage_extrahera   -> assets/reactions/biotage_extrahera_spe_library (PubChem logP/pKa, published SPE recoveries)
elga_purelab_flex3, inert_purelab_he, vacuubrand_pc3001 -> not_applicable (contract extension only)

Legacy fixture paths stay the default; these tests only exercise the opt-in template paths and the contracts.
"""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

def _need(p):
    """Raise FileNotFoundError for a missing build record or source cache (the public release skips such tests)."""
    if not Path(p).exists():
        raise FileNotFoundError(2, "not present in this tree", str(p))

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "chemistry-lab-assets" / "scripts"))
from asset_tool import lint_schema, validate_value  # noqa: E402
from basic40_runtime_vacuum import ExtraheraHost, LyovaporHost, RV10Host  # noqa: E402
from basic40_templates import biotage_extrahera as spe_tpl  # noqa: E402
from basic40_templates import buchi_lyovapor_l250 as lyo_tpl  # noqa: E402
from basic40_templates import ika_rv10 as rv10_tpl  # noqa: E402
import template_library  # noqa: E402

TIER = ROOT / "assets" / "basic40_batch02" / "t2_vacuum"
OUT = ROOT / "outputs" / "basic40_batch02" / "t2_vacuum"
ENVELOPE_KEYS = {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"}


def _lib(rel):
    return json.loads((ROOT / rel).read_text())


def _check_schemas(test, caps):
    for cap, info in caps.items():
        test.assertEqual(lint_schema(info["input_schema"]), [], cap)
        test.assertEqual(lint_schema(info["output_schema"]), [], cap)
        test.assertEqual(validate_value(info["example_arguments"][0], info["input_schema"]), [], cap)


class _Base(unittest.TestCase):
    def library_common(self, rel, validation, minimum_usable):
        lib = _lib(rel)
        records = lib["templates"]
        self.assertEqual(len({r["substance_id"] for r in records}), len(records))
        for r in records:
            for key in ("substance_id", "name", "cas", "evidence", "source", "accessed", "unit", "conditions"):
                self.assertIn(key, r, (r["substance_id"], key))
            self.assertIn(r["evidence"], ("measured", "measured_literature", "derived", "assumption"))
            self.assertTrue(r["source"])
            if r.get("cas"):  # links into the shared compound tables (spectral_library by CAS, substances.json by id)
                self.assertEqual(r["shared_ids"].get("spectral_library"), template_library.shared_compounds_by_cas().get(r["cas"]), r["substance_id"])
                if "substances" in r["shared_ids"]:
                    self.assertIn(r["shared_ids"]["substances"], template_library.shared_substance_ids())
        self.assertGreaterEqual(sum(r["runtime_usable"] for r in records), minimum_usable)
        report = json.loads((OUT / validation).read_text())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["count"], len(records))
        self.assertEqual(report["library_sha256"], template_library.library_sha256(rel))
        self.assertTrue(all(c["status"] == "passed" for c in report["checks"]))
        return lib


class RV10TemplateTests(_Base):
    def setUp(self):
        self.host = RV10Host()
        self.host.create_instance("rv")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.n = 0

    def invoke(self, cap, session=None, **args):
        self.n += 1
        return (session or self.agent).invoke(instance_id="rv", capability_id=cap, arguments=args, request_id=f"rvt_{self.n}")

    def start(self, *, solvent="ethanol", volume=2e-4, pressure=17500.0, ua=30.0, bath=333.15, initial=333.15):
        self.host.configure_bath("rv", effective_heat_capacity_j_k=5000.0, initial_temperature_k=initial, heat_loss_w_k=0.0, evidence="engineering_assumption: test bath")
        mass = self.host.configure_template_charge("rv", solvent_id=solvent, volume_m3=volume, condensation_efficiency=0.9, heat_transfer_w_k=ua, evidence="engineering_assumption: test UA")
        self.host.connect_vacuum_source("rv", "pump", pressure_pa=pressure, evidence="external snapshot")
        for cap, args in (("power.set", {"enabled": True}), ("rotation.start", {}), ("thermal.set_temperature", {"temperature_k": bath}), ("thermal.start", {})):
            self.assertEqual(self.invoke(cap, **args)["status"], "succeeded", cap)
        return mass

    def test_library_real_records_validation_and_shared_ids(self):
        lib = self.library_common(rv10_tpl.TEMPLATE_REL, "ika_rv10/templates/output_template_validation.json", 50)
        usable = [r for r in lib["templates"] if r["runtime_usable"]]
        for r in usable:
            self.assertTrue(r["antoine_fits"] and r["vaporization_enthalpy_points"] and r["density_kg_m3"] > 0)
            for fit in r["antoine_fits"]:
                lo, hi = fit["temperature_range_k"]
                self.assertLess(lo, hi)
        self.assertEqual(lib["rule_of_thumb"]["evidence"], "assumption")
        self.assertEqual(rv10_tpl.record("ethanol")["shared_ids"]["spectral_library"], "ethanol")

    def test_template_read_is_public_agent_query_and_inverts_antoine(self):
        caps = self.agent.describe(instance_id="rv")["capabilities"]
        self.assertIn("template.read", caps)
        self.assertNotIn("evaporation.template_report", caps)
        self.assertIn("evaporation.template_report", self.judge.describe(instance_id="rv")["capabilities"])
        _check_schemas(self, rv10_tpl.capabilities(include_evaluator=True))
        before = self.host.debug_snapshot()
        out = self.invoke("template.read", substance_id="ethanol", pressure_pa=17500.0, boiling_temperature_k=313.15)
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(set(out), ENVELOPE_KEYS)
        self.assertEqual(validate_value(out["result"], rv10_tpl.TEMPLATE_READ_OUTPUT), [])
        res = out["result"]
        fit = next(f for f in res["template"]["antoine_fits"] if f["fit_id"] == res["boiling_fit_id"])
        t = res["boiling_temperature_at_pressure_k"]
        self.assertAlmostEqual(1e5 * 10 ** (fit["A"] - fit["B_k"] / (t + fit["C_k"])), 17500.0, delta=1e-6)
        self.assertTrue(fit["temperature_range_k"][0] <= t <= fit["temperature_range_k"][1])
        self.assertAlmostEqual(t, 312.7, delta=0.3)  # ethanol at 175 mbar boils near 40 degC
        self.assertAlmostEqual(res["rule_of_thumb"]["recommended_bath_temperature_k"], t + 20.0)
        self.assertEqual(res["rule_of_thumb"]["evidence"], "assumption")
        self.assertGreater(res["pressure_for_boiling_temperature_pa"], 17500.0)
        self.assertEqual(res["template_source"], rv10_tpl.TEMPLATE_REL)
        self.assertFalse(res["synthetic"])
        self.assertEqual(before["instances"], self.host.debug_snapshot()["instances"])

    def test_known_boiling_points_and_normal_boiling_consistency(self):
        for sid, pressure, celsius in (("water", 7200.0, 39.5), ("acetone", 55600.0, 39.7), ("toluene", 7700.0, 39.5), ("methanol", 33700.0, 38.9)):
            t, _ = rv10_tpl.boiling_temperature_k(rv10_tpl.record(sid), pressure)
            self.assertAlmostEqual(t - 273.15, celsius, delta=0.5, msg=sid)
        for sid in ("ethanol", "toluene", "hexane", "acetone", "benzene"):
            rec = rv10_tpl.record(sid)
            t, _ = rv10_tpl.boiling_temperature_k(rec, 101325.0)
            self.assertAlmostEqual(t, rec["normal_boiling_point_k"], delta=1.5, msg=sid)

    def test_outside_antoine_range_unknown_and_invalid_are_rejected_or_null(self):
        out = self.invoke("template.read", substance_id="dmso", pressure_pa=10.0, boiling_temperature_k=None)  # below every DMSO fit range
        self.assertEqual(out["status"], "succeeded")
        self.assertIsNone(out["result"]["boiling_temperature_at_pressure_k"])
        self.assertEqual(self.invoke("template.read", substance_id="unobtainium", pressure_pa=None, boiling_temperature_k=None)["error"]["code"], "UNKNOWN_REFERENCE")
        for bad in ({"substance_id": "ethanol"}, {"substance_id": "ethanol", "pressure_pa": -1.0, "boiling_temperature_k": None},
                    {"substance_id": "ethanol", "pressure_pa": True, "boiling_temperature_k": None}):
            self.assertEqual(self.agent.invoke(instance_id="rv", capability_id="template.read", arguments=bad, request_id=f"bad_{self.n}_{len(bad)}_{bad.get('pressure_pa')}")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.invoke("evaporation.template_report")["error"]["code"], "ACCESS_DENIED")

    def test_template_charge_mass_boiling_rate_and_conservation(self):
        mass = self.start()
        rec = rv10_tpl.record("ethanol")
        self.assertAlmostEqual(mass, rec["density_kg_m3"] * 2e-4)
        report = self.host.template_charge_report("rv")
        self.assertTrue(report["boiling"])
        t_boil = report["boiling_temperature_at_pressure_k"]
        dh = rv10_tpl.vaporization_enthalpy(rec, t_boil)
        expected = min(30.0 * (333.15 - t_boil), 1350.0) / (dh["value_j_mol"] / rec["molar_mass_kg_mol"])
        self.assertAlmostEqual(report["evaporation_rate_kg_s"], expected, places=12)
        self.host.step(60.0)
        truth = {sid: self.judge.read(instance_id="rv", state_ids=[sid])["observations"][sid]["value"] for sid in
                 ("evaporation.remaining_mass_kg", "evaporation.evaporated_mass_kg", "recovery.condensate_mass_kg", "evaporation.uncondensed_mass_kg")}
        self.assertAlmostEqual(truth["evaporation.evaporated_mass_kg"], expected * 60.0, places=12)
        self.assertAlmostEqual(truth["evaporation.remaining_mass_kg"] + truth["evaporation.evaporated_mass_kg"], mass, places=12)
        self.assertAlmostEqual(truth["recovery.condensate_mass_kg"] + truth["evaporation.uncondensed_mass_kg"], truth["evaporation.evaporated_mass_kg"], places=12)
        out = self.invoke("evaporation.template_report", session=self.judge)
        self.assertEqual(validate_value(out["result"], rv10_tpl.REPORT_OUTPUT), [])
        self.assertEqual(out["result"]["solvent_id"], "ethanol")
        self.assertTrue(out["result"]["synthetic_components"])
        for _ in range(10):
            self.host.step(600.0)
        self.assertEqual(self.host.template_charge_report("rv")["regime"], "charge_exhausted")
        self.assertFalse(self.agent.read(instance_id="rv", state_ids=["evaporation.running"])["observations"]["evaporation.running"]["value"])

    def test_below_boiling_point_nothing_evaporates(self):
        self.start(pressure=101325.0, bath=333.15)
        self.assertEqual(self.host.template_charge_report("rv")["regime"], "sub_boiling_neglected")
        self.host.step(120.0)
        self.assertEqual(self.judge.read(instance_id="rv", state_ids=["evaporation.evaporated_mass_kg"])["observations"]["evaporation.evaporated_mass_kg"]["value"], 0.0)
        self.assertFalse(self.agent.read(instance_id="rv", state_ids=["evaporation.running"])["observations"]["evaporation.running"]["value"])

    def test_agent_surfaces_do_not_reveal_charge_identity(self):
        self.start()
        self.host.step(10.0)
        dumped = json.dumps([self.agent.read(instance_id="rv"), self.agent.logs()])  # describe only carries static examples
        self.assertNotIn("ethanol", dumped)

    def test_legacy_empirical_path_unchanged_and_not_template_backed(self):
        self.host.configure_bath("rv", effective_heat_capacity_j_k=1000.0, initial_temperature_k=293.15, heat_loss_w_k=0.0, evidence="engineering_assumption: test")
        self.host.configure_evaporation("rv", charge_mass_kg=1.0, evaporation_rate_kg_s=1e-3, condensation_efficiency=1.0, evidence="engineering_assumption: test")
        self.host.connect_vacuum_source("rv", "pump", pressure_pa=1000.0, evidence="snapshot")
        for cap, args in (("power.set", {"enabled": True}), ("rotation.start", {}), ("thermal.start", {})):
            self.invoke(cap, **args)
        self.host.step(10.0)
        self.assertAlmostEqual(self.judge.read(instance_id="rv", state_ids=["evaporation.evaporated_mass_kg"])["observations"]["evaporation.evaporated_mass_kg"]["value"], 1e-2)
        self.assertFalse(self.host.template_charge_report("rv")["template_backed"])

    def test_invalid_template_charge_is_rejected_without_mutation(self):
        before = self.host.debug_snapshot()
        for kwargs in ({"solvent_id": "mtbe"}, {"solvent_id": "nope"}, {"volume_m3": 0.0}, {"heat_transfer_w_k": -1.0}, {"evidence": " "}):
            args = dict(solvent_id="ethanol", volume_m3=1e-4, condensation_efficiency=1.0, heat_transfer_w_k=10.0, evidence="x")
            args.update(kwargs)
            with self.assertRaises(ValueError):
                self.host.configure_template_charge("rv", **args)
        self.assertEqual(before, self.host.debug_snapshot())


class LyovaporTemplateTests(_Base):
    def setUp(self):
        self.host = LyovaporHost()
        self.host.create_instance("fz")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.n = 0

    def invoke(self, cap, session=None, **args):
        self.n += 1
        return (session or self.agent).invoke(instance_id="fz", capability_id=cap, arguments=args, request_id=f"lyt_{self.n}")

    def value(self, sid):
        return self.judge.read(instance_id="fz", state_ids=[sid])["observations"][sid]["value"]

    def boundary(self, sample_k, pressure=10.0, condenser=188.15):
        self.host.set_boundary("fz", pressure_pa=pressure, shelf_temperature_k=253.15, sample_temperature_k=sample_k, condenser_temperature_k=condenser, evidence="engineering_assumption: test boundary")

    def prepare(self, formulation="sucrose", sample_k=238.15, pressure=10.0, legacy=False):
        self.invoke("chamber.set_open", open=True)
        if legacy:
            self.host.initialize_sample("fz", dry_mass_kg=1e-3, ice_mass_kg=1e-4, bound_water_mass_kg=5e-5, pre_frozen=True, evidence="engineering_assumption: test")
        else:
            self.host.initialize_template_sample("fz", formulation_id=formulation, dry_mass_kg=1e-3, ice_mass_kg=1e-4, bound_water_mass_kg=5e-5, pre_frozen=True, evidence="engineering_assumption: test")
        self.invoke("chamber.set_open", open=False)
        self.host.connect_vacuum("fz", source_instance_id="src", asset_type_id="external.synthetic_compatible_vacuum", minimum_pressure_pa=3, evidence="engineering_assumption: external")
        self.boundary(sample_k, pressure)
        self.host.configure_kinetics("fz", sublimation_rate_kg_s=1e-5, desorption_rate_kg_s=5e-6, evidence="engineering_assumption: test rates")
        for cap, args in (("power.set", {"enabled": True}), ("thermal.set_protection_temperature", {"temperature_k": 313.15}), ("lyophilization.set_recipe", {"recipe": {"pre_freeze_s": 2.0, "primary_drying_s": 20.0, "secondary_drying_s": 20.0}}), ("lyophilization.start", {})):
            self.assertEqual(self.invoke(cap, **args)["status"], "succeeded", cap)

    def test_library_records_ice_equation_and_validation(self):
        lib = self.library_common(lyo_tpl.TEMPLATE_REL, "buchi_lyovapor_l250/templates/output_template_validation.json", 50)
        ice = lyo_tpl.record("ice_ih")
        self.assertEqual((ice["equation"]["A"], ice["equation"]["B"], ice["equation"]["C"], ice["equation"]["D"]), (9.550426, -5723.265, 3.53068, -0.00728332))
        for rec in lib["templates"]:
            if rec["kind"] != "equation" and rec["runtime_usable"]:
                self.assertTrue(150 < rec["critical_temperature_k"] < 273.16, rec["substance_id"])
                self.assertTrue(all(v["quote"] and v["source"] for v in rec["values"]))
            if rec["quality"] == "low_confidence_only":
                self.assertFalse(rec["runtime_usable"])
        sucrose = lyo_tpl.record("sucrose")
        self.assertEqual(sucrose["critical_basis"], "tc")
        self.assertTrue(240.0 < sucrose["critical_temperature_k"] < 245.0)

    def test_murphy_koop_ice_vapour_pressure(self):
        self.assertAlmostEqual(lyo_tpl.ice_vapor_pressure_pa(273.16), 611.65, delta=0.05)
        self.assertAlmostEqual(lyo_tpl.ice_vapor_pressure_pa(243.15), 38.0, delta=0.1)
        self.assertIsNone(lyo_tpl.ice_vapor_pressure_pa(100.0))
        self.assertIsNone(lyo_tpl.ice_vapor_pressure_pa(280.0))
        out = self.invoke("template.read", substance_id="ice_ih", temperature_k=243.15)
        self.assertEqual(validate_value(out["result"], lyo_tpl.TEMPLATE_READ_OUTPUT), [])
        self.assertAlmostEqual(out["result"]["ice_vapor_pressure_pa"], lyo_tpl.ice_vapor_pressure_pa(243.15))

    def test_template_read_margin_flags_and_access(self):
        caps = self.agent.describe(instance_id="fz")["capabilities"]
        self.assertIn("template.read", caps)
        self.assertNotIn("drying.template_report", caps)
        _check_schemas(self, lyo_tpl.capabilities(include_evaluator=True))
        crit = lyo_tpl.record("mannitol")["critical_temperature_k"]
        for t, flag in ((crit - 5, "below_critical"), (crit - 1, "within_2k_below_critical"), (crit + 1, "above_critical_collapse_or_meltback_risk")):
            res = self.invoke("template.read", substance_id="mannitol", temperature_k=t)["result"]
            self.assertEqual(res["product_temperature_flag"], flag)
            self.assertAlmostEqual(res["margin_to_critical_k"], t - crit)
        self.assertEqual(self.invoke("template.read", substance_id="nope", temperature_k=None)["error"]["code"], "UNKNOWN_REFERENCE")
        self.assertEqual(self.invoke("template.read", substance_id="sucrose")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.invoke("drying.template_report")["error"]["code"], "ACCESS_DENIED")

    def test_primary_drying_held_above_critical_and_without_ice_driving_force(self):
        self.prepare(sample_k=238.15, pressure=10.0)
        self.host.step(5.0)
        self.assertEqual(self.value("cycle.phase"), "primary_drying")
        self.assertEqual(self.value("cycle.wait_reason"), "none")
        ice_before = self.value("sample.ice_mass_kg")
        crit = lyo_tpl.record("sucrose")["critical_temperature_k"]
        self.boundary(crit + 3.0)
        self.host.step(5.0)
        self.assertEqual(self.value("cycle.wait_reason"), "temperature")
        self.assertEqual(self.value("sample.ice_mass_kg"), ice_before)
        report = self.invoke("drying.template_report", session=self.judge)["result"]
        self.assertEqual(validate_value(report, lyo_tpl.REPORT_OUTPUT), [])
        self.assertEqual(report["product_temperature_flag"], "above_critical_collapse_or_meltback_risk")
        self.boundary(200.0, pressure=10.0, condenser=188.15)  # p_ice(200 K) ~0.16 Pa < 10 Pa
        self.host.step(5.0)
        self.assertEqual(self.value("cycle.wait_reason"), "pressure")
        self.assertFalse(self.host.template_drying_report("fz")["sublimation_thermodynamically_possible"])
        self.boundary(238.15)
        self.host.step(100.0)
        water = sum(self.value(s) for s in ("sample.ice_mass_kg", "sample.bound_water_mass_kg", "condenser.ice_mass_kg", "condenser.drained_mass_kg"))
        self.assertAlmostEqual(water, 1.5e-4, places=15)
        self.assertIn(self.value("cycle.phase"), ("secondary_drying", "complete"))

    def test_secondary_drying_not_gated_by_collapse_and_legacy_sample_untouched(self):
        self.prepare(sample_k=238.15)
        self.host.step(30.0)
        self.boundary(293.15)  # warm product after ice removal: secondary drying is not held by Tc
        self.host.step(30.0)
        self.assertEqual(self.value("cycle.phase"), "complete")
        self.assertIsNone(self.value("sample.collapse_temperature_k"))
        host = LyovaporHost()
        host.create_instance("x")
        self.assertIsNone(host._instances["x"].template)
        self.assertTrue(self.host.template_drying_report("fz")["template_backed"])

    def test_legacy_initialize_clears_template_and_agent_sees_no_formulation(self):
        self.prepare()
        self.host.step(3.0)
        dumped = json.dumps([self.agent.read(instance_id="fz"), self.agent.logs()])
        self.assertNotIn("sucrose", dumped)
        self.invoke("lyophilization.stop")
        self.host.set_boundary("fz", pressure_pa=100000, shelf_temperature_k=293.15, sample_temperature_k=293.15, condenser_temperature_k=293.15, evidence="vented")
        self.host.disconnect_vacuum("fz")
        self.invoke("chamber.set_open", open=True)
        self.host.unload_sample("fz")
        self.assertIsNone(self.host._instances["fz"].template)
        self.host.initialize_sample("fz", dry_mass_kg=1e-3, ice_mass_kg=1e-4, bound_water_mass_kg=0.0, pre_frozen=True, evidence="legacy")
        self.assertFalse(self.host.template_drying_report("fz")["template_backed"])
        with self.assertRaises(ValueError):
            lyo_tpl.initialize_sample(self.host, "fz", formulation_id="arabinose", dry_mass_kg=1e-3, ice_mass_kg=1e-4, bound_water_mass_kg=0.0, pre_frozen=True, evidence="x")


class ExtraheraTemplateTests(_Base):
    def setUp(self):
        self.host = ExtraheraHost()
        self.host.create_instance("spe")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.n = 0

    def invoke(self, cap, session=None, **args):
        self.n += 1
        return (session or self.agent).invoke(instance_id="spe", capability_id=cap, arguments=args, request_id=f"spt_{self.n}")

    def run_method(self, analyte="cortisol", sorbent="isolute_sle_plus", complete=True):
        self.host.connect_vacuum_source("spe", "pump")
        self.host.set_utilities("spe", vacuum_ready=True, compressed_air_ready=True, nitrogen_ready=True, evidence="engineering_assumption: utilities")
        self.host.initialize_liquids("spe", {"load": 3e-6, "wash": 3e-6, "elute": 3e-6}, evidence="engineering_assumption: inventories")
        self.host.assign_template_analyte("spe", source_position="load", analyte_id=analyte, concentration_kg_m3=1e-4, sorbent_id=sorbent, evidence="engineering_assumption: spiked sample")
        for cap, args in (("power.set", {"enabled": True}), ("deck.configure", {"format": "plate96", "cartridge_volume_m3": None, "collection_plate": "c", "solvent_positions": ["load", "wash", "elute"]}),
                          ("manifold.set_position", {"z_m": 0.0}), ("pressure.set", {"pressure_pa": 100000}),
                          ("method.configure", {"steps": [{"phase": p, "solvent_position": p, "volume_m3": 1e-6, "flow_m3_s": 1e-7, "drain_flow_m3_s": 1e-7, "repetitions": 1} for p in ("load", "wash", "elute")]}),
                          ("method.start", {})):
            self.assertIn(self.invoke(cap, **args)["status"], ("succeeded", "accepted"), cap)
        for _ in range(10 if complete else 1):
            self.host.step(10.0)

    def test_library_real_logp_pka_recoveries_and_validation(self):
        lib = self.library_common(spe_tpl.TEMPLATE_REL, "biotage_extrahera/templates/output_template_validation.json", 50)
        with_logp = [r for r in lib["templates"] if r["logp"] is not None]
        self.assertGreaterEqual(len(with_logp), 50)
        recoveries = [x for r in lib["templates"] for x in r["recoveries"]]
        self.assertGreaterEqual(len(recoveries), 150)
        self.assertTrue(all(x["evidence"] == "measured_literature" and x["quote"] and x["locator"] and x["sorbent_id"] for x in recoveries))
        self.assertTrue(any(x["processing_system"] == "Extrahera" for x in recoveries))

    def test_template_read_ionisation_is_derived_and_recovery_filtered(self):
        caps = self.agent.describe(instance_id="spe")["capabilities"]
        self.assertIn("template.read", caps)
        self.assertNotIn("spe.template_report", caps)
        _check_schemas(self, spe_tpl.capabilities(include_evaluator=True))
        res = self.invoke("template.read", substance_id="amphetamine", ph=6.0, sorbent_id=None)["result"]
        self.assertEqual(validate_value(res, spe_tpl.TEMPLATE_READ_OUTPUT), [])
        ion = res["ionisation"]
        self.assertEqual(ion["charge_type"], "base")
        self.assertAlmostEqual(ion["neutral_fraction"], 1 / (1 + 10 ** (ion["pka_used"] - 6.0)))
        self.assertTrue(ion["evidence"].startswith("derived"))
        res = self.invoke("template.read", substance_id="cortisol", ph=None, sorbent_id="isolute_sle_plus")["result"]
        self.assertTrue(res["template"]["recoveries"])
        self.assertTrue(all(r["sorbent_id"] == "isolute_sle_plus" for r in res["template"]["recoveries"]))
        self.assertIsNotNone(res["recovery_summary"]["median_recovery_fraction"])
        res = self.invoke("template.read", substance_id="cortisol", ph=None, sorbent_id="evolute_express_cx")["result"]
        self.assertIsNone(res["recovery_summary"]["median_recovery_fraction"])
        self.assertEqual(self.invoke("template.read", substance_id="cortisol", ph=None, sorbent_id="c18_unknown")["error"]["code"], "UNKNOWN_REFERENCE")
        self.assertEqual(self.invoke("template.read", substance_id="cortisol", ph=15.0, sorbent_id=None)["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.invoke("spe.template_report")["error"]["code"], "ACCESS_DENIED")

    def test_completed_method_uses_published_recovery_for_matching_sorbent(self):
        self.run_method()
        report = self.invoke("spe.template_report", session=self.judge)["result"]
        self.assertEqual(validate_value(report, spe_tpl.REPORT_OUTPUT), [])
        self.assertEqual(report["method_status"], "completed")
        a = report["analytes"][0]
        published = sorted(r["recovery_fraction"] for r in spe_tpl.record("cortisol")["recoveries"] if r["sorbent_id"] == "isolute_sle_plus" and r["processing_system"] == "Extrahera")
        self.assertAlmostEqual(a["recovery_summary"]["median_recovery_fraction"], published[len(published) // 2])
        self.assertAlmostEqual(a["withdrawn_volume_m3"], 1e-6)
        self.assertAlmostEqual(a["expected_eluted_mass_kg"], 1e-6 * 1e-4 * a["recovery_summary"]["median_recovery_fraction"])
        self.assertAlmostEqual(a["expected_eluate_concentration_kg_m3"], a["expected_eluted_mass_kg"] / report["collection_volume_m3"])
        self.assertEqual(self.host.template_recovery_report("spe"), report)

    def test_unmatched_sorbent_and_incomplete_method_give_null_recovery(self):
        self.run_method(analyte="ibuprofen", sorbent="evolute_express_abn")
        a = self.host.template_recovery_report("spe")["analytes"][0]
        self.assertEqual(a["status"], "no_published_recovery_for_sorbent")
        self.assertIsNone(a["expected_eluted_mass_kg"])
        other = ExtraheraTemplateTests("test_unmatched_sorbent_and_incomplete_method_give_null_recovery")
        other.setUp()
        other.run_method(complete=False)
        self.assertEqual(other.host.template_recovery_report("spe")["analytes"][0]["status"], "not_loaded_by_completed_method")

    def test_agent_surfaces_hide_assigned_analyte_and_assignment_is_validated(self):
        self.run_method(analyte="zolmitriptan")
        dumped = json.dumps([self.agent.read(instance_id="spe"), self.agent.logs()])
        self.assertNotIn("zolmitriptan", dumped)
        self.assertIsNone(self.agent.read(instance_id="spe", state_ids=["chemistry.recovery_fraction"])["observations"]["chemistry.recovery_fraction"]["value"])
        host = ExtraheraHost()
        host.create_instance("x")
        with self.assertRaises(ValueError):
            host.assign_template_analyte("x", source_position="load", analyte_id="cortisol", concentration_kg_m3=1.0, sorbent_id="isolute_sle_plus", evidence="x")
        host.initialize_liquids("x", {"load": 1e-6}, evidence="x")
        before = host.debug_snapshot()
        for kwargs in ({"analyte_id": "nope"}, {"sorbent_id": "nope"}, {"source_position": "missing"}, {"concentration_kg_m3": -1.0}):
            args = dict(source_position="load", analyte_id="cortisol", concentration_kg_m3=1.0, sorbent_id="isolute_sle_plus", evidence="x")
            args.update(kwargs)
            with self.assertRaises(ValueError):
                host.assign_template_analyte("x", **args)
        self.assertEqual(before, host.debug_snapshot())


class VacuumTierContractTests(unittest.TestCase):
    SLUGS = ("ika_rv10", "buchi_lyovapor_l250", "biotage_extrahera", "elga_purelab_flex3", "inert_purelab_he", "vacuubrand_pc3001")

    def contract(self, slug):
        return json.loads((TIER / slug / "asset.contract.json").read_text())

    def test_output_template_extensions(self):
        for slug in self.SLUGS:
            ext = self.contract(slug)["extensions"]["chemistry_lab.output_templates"]
            if slug in ("elga_purelab_flex3", "inert_purelab_he", "vacuubrand_pc3001"):
                self.assertEqual(ext["status"], "not_applicable", slug)
                self.assertTrue(ext["reason_zh"], slug)
            else:
                self.assertEqual(ext["status"], "implemented", slug)
                lib = _lib(ext["library"])
                self.assertEqual(ext["n_records"], len(lib["templates"]))
                self.assertEqual(json.loads((ROOT / ext["validation"]).read_text())["status"], "passed")
                caps = self.contract(slug)["capabilities"]
                self.assertEqual(caps["template.read"]["visibility"], "agent")

    def test_all_vacuum_contracts_validate_with_files(self):
        _need(OUT)  # build records are not part of the public release
        for slug in self.SLUGS:
            out = subprocess.run([sys.executable, str(ROOT / "skills/chemistry-lab-assets/scripts/asset_tool.py"), "validate", str(TIER / slug / "asset.contract.json"),
                                  "--check-files", "--asset-root", str(ROOT)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, slug + out.stdout[-2000:] + out.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
