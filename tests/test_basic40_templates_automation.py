"""Template (real public data) tests for the t5_automation tier (agent key: automation).

CHEM-180 Alicat MC: NIST SRD 69 gas-property library + Alicat Gas Select mixtures (derived) wired into
AlicatMCRuntime (gas.select library IDs, real-gas standard/actual/mass conversion, ideal-LFE viscosity
indication with a host-declared line gas, template.list / template.read / flow.read_template).
The other eight automation instruments are registered as output_templates=not_applicable.
"""
import copy
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "chemistry-lab-assets" / "scripts"))

from basic40_runtime_automation import AlicatMCRuntime, ALICAT_MC_TYPE, ALICAT_MC_FS_M3_S, ALICAT_MC_R  # noqa: E402
from basic40_templates import alicat_mc as amc  # noqa: E402
import template_library  # noqa: E402
from asset_tool import lint_schema, validate_value  # noqa: E402

LIB = ROOT / amc.TEMPLATE_REL
TIER = ROOT / "assets" / "basic40_batch02" / "t5_automation"
VALIDATION = ROOT / "outputs/basic40_batch02/t5_automation/alicat_mc/templates/output_template_validation.json"
NOT_APPLICABLE = ("azenta_heat_sealer", "azenta_intellixcap_s1", "bronkhorst_p702cv", "mt_quantos_q2",
                  "opentrons_flex", "preciseflex_400", "vici_euta", "ziath_dp5")


def load_library():
    return json.loads(LIB.read_text(encoding="utf-8"))


class TestAlicatGasLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = load_library()
        cls.records = {r["substance_id"]: r for r in cls.lib["templates"]}

    def test_record_fields_units_evidence_and_unique_ids(self):
        ids = [r["substance_id"] for r in self.lib["templates"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.lib["asset_type_id"], ALICAT_MC_TYPE)
        for r in self.lib["templates"]:
            with self.subTest(r["substance_id"]):
                for key in ("substance_id", "name", "cas", "unit", "conditions", "evidence", "source", "accessed", "molar_mass_kg_mol", "alicat"):
                    self.assertIn(key, r)
                self.assertIn(r["evidence"], ("measured", "measured_literature", "derived", "assumption"))
                self.assertGreater(r["molar_mass_kg_mol"], 0.001)
                self.assertLess(r["molar_mass_kg_mol"], 0.3)
                self.assertIn(r["alicat"]["gas_select"], ("standard_mc", "s_series_only", "not_listed"))
                if r["kind"] == "pure":
                    self.assertEqual(r["evidence"], "measured_literature")
                    self.assertRegex(r["cas"], r"^\d+-\d\d-\d$")
                    expected = {**template_library.shared_ids_for(r["cas"], r["substance_id"]),
                                **template_library.shared_ids_for(r["cas"], r["substance_id"].removeprefix("gas_"))}
                    self.assertEqual(r["shared_ids"], expected)
                    self.assertEqual(r["unit"]["viscosity_Pa_s"], "Pa*s")
                else:
                    self.assertEqual(r["evidence"], "derived")
                    self.assertIsNone(r["cas"])
                    self.assertAlmostEqual(sum(r["composition_mole_fraction"].values()), 1.0, places=12)
                    self.assertTrue(set(r["composition_mole_fraction"]) <= {k for k, v in self.records.items() if v["kind"] == "pure"})
                    self.assertIn("Wilke", r["derivation"]["viscosity"])

    def test_at_least_50_runtime_usable_nist_pure_gases(self):
        usable = []
        for r in self.lib["templates"]:
            if r["kind"] != "pure":
                continue
            stp = amc.gas_state(r["substance_id"], 298.15, 101325.0)
            if stp and stp["viscosity_Pa_s"] and amc.gas_state(r["substance_id"], 323.15, 101325.0):
                usable.append(r["substance_id"])
        self.assertGreaterEqual(len(usable), 50, usable)

    def test_alicat_standard_gas_select_list_coverage(self):
        entries = self.lib["alicat_gas_select_list"]["entries"]
        standard = [e for e in entries if e["gas_select"] == "standard_mc"]
        self.assertEqual(len(standard), 98)  # 21 pure + 77 mixtures, as on the Rev6 spec sheet
        missing = [e["listed_name"] for e in standard if e["library_id"] is None]
        self.assertEqual(missing, ["Acetylene (C2H2)"])
        for e in standard:
            if e["library_id"]:
                self.assertEqual(self.records[e["library_id"]]["alicat"]["gas_select"], "standard_mc")

    def test_independent_validation_report_passed_for_current_library(self):
        report = json.loads(VALIDATION.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["count"], len(self.lib["templates"]))
        self.assertEqual(report["library_sha256"], template_library.library_sha256(amc.TEMPLATE_REL))
        self.assertTrue(all(c["status"] == "passed" for c in report["checks"]))

    def test_nist_nodes_are_reproduced_exactly_and_physical_sanity(self):
        for gas, (rho_lo, rho_hi), (mu_lo, mu_hi) in (("gas_nitrogen", (1.144, 1.147), (17.5e-6, 18.2e-6)),
                                                      ("gas_helium", (0.163, 0.164), (19.5e-6, 20.2e-6)),
                                                      ("gas_carbon_dioxide", (1.80, 1.81), (14.8e-6, 15.2e-6)),
                                                      ("mix_air", (1.18, 1.19), (18.2e-6, 18.8e-6))):
            with self.subTest(gas):
                state = amc.gas_state(gas, 298.15, 101325.0)
                self.assertTrue(rho_lo < state["density_kg_m3"] < rho_hi, state)
                self.assertTrue(mu_lo < state["viscosity_Pa_s"] < mu_hi, state)
        co2 = self.records["gas_carbon_dioxide"]
        stored = co2["values_at_reference_conditions"]["stp_298.15K_101325Pa"]
        state = amc.gas_state("gas_carbon_dioxide", 298.15, 101325.0)
        self.assertAlmostEqual(state["density_kg_m3"] / stored["density_kg_m3"], 1.0, places=12)
        self.assertAlmostEqual(state["viscosity_Pa_s"] / stored["viscosity_Pa_s"], 1.0, places=12)
        # viscosity of a dilute gas rises with temperature
        self.assertGreater(amc.gas_state("gas_nitrogen", 333.15, 101325.0)["viscosity_Pa_s"], amc.gas_state("gas_nitrogen", 263.15, 101325.0)["viscosity_Pa_s"])

    def test_condensation_and_out_of_range_are_unavailable_not_extrapolated(self):
        self.assertIsNotNone(amc.gas_state("gas_n_butane", 298.15, 200000.0))
        self.assertIsNone(amc.gas_state("gas_n_butane", 298.15, 300000.0))  # NIST p_sat(298.15 K) ~ 0.24 MPa
        self.assertIsNone(amc.gas_state("gas_r11", 298.15, 150000.0))
        self.assertIsNone(amc.gas_state("gas_nitrogen", 400.0, 101325.0))
        self.assertIsNone(amc.gas_state("gas_nitrogen", 298.15, 5e6))
        self.assertIsNone(amc.gas_state("unknown_gas", 298.15, 101325.0))
        self.assertIsNone(amc.gas_state("mix_hd_5", 298.15, 1.1e6))  # LPG at 160 psia condenses
        self.assertIsNotNone(amc.gas_state("mix_hd_5", 298.15, 101325.0))


class TestAlicatTemplateRuntime(unittest.TestCase):
    def setUp(self):
        self.host = AlicatMCRuntime()
        for name in ("mfc_a", "mfc_b"):
            self.host.create_instance(name)
        self.host.host_register_gas("synthetic_known_property", compatibility_evidence="Synthetic fixture only", molar_mass_kg_mol=0.0280134,
                                    property_evidence="Synthetic ideal gas property")
        self.host.register_principal("learner", visibility="agent")
        self.host.register_principal("evaluator", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("evaluator")
        self.counter = 0

    def invoke(self, cid, instance="mfc_a", **arguments):
        self.counter += 1
        return self.agent.invoke(instance_id=instance, capability_id=cid, arguments=arguments, request_id="r%d" % self.counter)

    def ok(self, cid, instance="mfc_a", **arguments):
        response = self.invoke(cid, instance, **arguments)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, sid, instance="mfc_a", debug=False):
        return (self.judge if debug else self.agent).read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def start(self, gas, *, instance="mfc_a", upstream=130000.0, temperature=298.15, target=ALICAT_MC_FS_M3_S / 2, tau=0.2):
        self.ok("power.set", instance, enabled=True)
        self.ok("gas.select", instance, gas_id=gas)
        self.host.host_connect_ports(instance, upstream_node_id=instance + "_in", downstream_node_id=instance + "_out")
        self.host.host_set_boundary(instance, upstream_pressure_Pa=upstream, downstream_pressure_Pa=101325.0, temperature_K=temperature, equilibrium=True, no_flow=True)
        self.host.host_configure_dynamics(instance, time_constant_s=tau, evidence="test assumption")
        self.ok("flow.zero", instance)
        self.ok("flow.reset_total", instance)
        self.host.host_set_boundary(instance, upstream_pressure_Pa=upstream, downstream_pressure_Pa=101325.0, temperature_K=temperature, equilibrium=False, no_flow=False)
        self.ok("flow.set_setpoint", instance, flow_rate_m3_s=target)

    def test_library_gas_selection_rules_keep_host_registration_path(self):
        self.ok("power.set", enabled=True)
        self.assertEqual(self.invoke("gas.select", gas_id="not_a_gas")["error"]["code"], "PRECONDITION_FAILED")
        for gas in ("gas_ammonia", "gas_r134a", "gas_r12", "mix_r_410a"):  # S-Series-only or not on the MC list
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("gas.select", gas_id=gas)["error"]["code"], "PRECONDITION_FAILED", gas)
            self.assertEqual(self.host.debug_snapshot(), before)
        self.ok("gas.select", gas_id="gas_nitrogen")
        self.ok("gas.select", gas_id="mix_c_25")
        self.ok("gas.select", gas_id="synthetic_known_property")
        with self.assertRaises(ValueError):
            self.host.host_enable_library_gas("gas_ammonia", compatibility_evidence=" ")
        with self.assertRaises(ValueError):
            self.host.host_enable_library_gas("not_a_gas", compatibility_evidence="test")
        self.host.host_enable_library_gas("gas_ammonia", compatibility_evidence="Host asserts an S-Series (MCS) wetted-material variant")
        self.ok("gas.select", gas_id="gas_ammonia")
        self.assertEqual(self.value("gas.selected_id"), "gas_ammonia")

    def test_library_gas_flow_uses_nist_real_gas_density(self):
        self.start("gas_carbon_dioxide", upstream=500000.0, temperature=310.0)
        self.start("synthetic_known_property", instance="mfc_b", upstream=500000.0, temperature=310.0)
        self.host.host_step(0.4)
        q = self.value("measurement.standard_flow_m3_s")
        self.assertEqual(q, self.value("measurement.standard_flow_m3_s", "mfc_b"))  # k = 1: same dynamics as the legacy path
        actual = amc.gas_state("gas_carbon_dioxide", 310.0, 500000.0)
        standard = amc.gas_state("gas_carbon_dioxide", 298.15, 101325.0)
        self.assertAlmostEqual(self.value("measurement.actual_flow_m3_s"), q * standard["density_kg_m3"] / actual["density_kg_m3"], delta=1e-18)
        self.assertAlmostEqual(self.value("measurement.mass_flow_kg_s"), q * standard["density_kg_m3"], delta=1e-18)
        ideal_actual = q * 310.0 / 298.15 * 101325.0 / 500000.0
        self.assertLess(self.value("measurement.actual_flow_m3_s"), ideal_actual)  # CO2 Z < 1 at 0.5 MPa
        self.assertGreater(self.value("measurement.actual_flow_m3_s"), ideal_actual * 0.97)
        self.assertEqual(self.value("measurement.actual_flow_m3_s", "mfc_b"), ideal_actual)

    def test_wrong_gas_in_line_follows_viscosity_ratio_and_stays_hidden(self):
        self.start("gas_nitrogen", target=ALICAT_MC_FS_M3_S / 2)
        self.host.host_set_line_gas("mfc_a", gas_id="gas_helium")
        for _ in range(40):
            self.host.host_step(0.2)
        mu_he = amc.gas_state("gas_helium", 298.15, 130000.0)["viscosity_Pa_s"]
        mu_n2 = amc.gas_state("gas_nitrogen", 298.15, 130000.0)["viscosity_Pa_s"]
        k = amc.indication_factor(self.host, self.host.debug_snapshot()["instances"]["mfc_a"]["values"], self.host._instances["mfc_a"])
        self.assertAlmostEqual(k, mu_he / mu_n2, delta=0.002 * k)  # density ratios nearly cancel for near-ideal gases
        indicated = self.value("measurement.standard_flow_m3_s")
        true = self.value("truth.standard_flow_m3_s", debug=True)
        self.assertAlmostEqual(indicated, ALICAT_MC_FS_M3_S / 2, delta=1e-12)  # controller servos on the indication
        self.assertAlmostEqual(true, ALICAT_MC_FS_M3_S / 2 / k, delta=1e-12)
        self.assertAlmostEqual(self.value("measurement.total_standard_volume_m3"), k * self.value("truth.total_standard_volume_m3", debug=True), delta=1e-15)
        payload = json.dumps([self.agent.describe(instance_id="mfc_a"), self.agent.read(instance_id="mfc_a"), self.ok("flow.read"),
                              self.ok("flow.read_template"), self.agent.logs()])
        self.assertNotIn("gas_helium", payload)
        self.assertNotIn("truth.", payload)
        self.host.host_set_line_gas("mfc_a", gas_id=None)
        self.assertEqual(self.value("measurement.standard_flow_m3_s"), true)
        self.host.host_set_line_gas("mfc_a", gas_id="oxygen")  # reaction-ledger id (substances.json) resolves to gas_oxygen
        self.assertEqual(self.host._instances["mfc_a"]["template_line_gas_id"], "gas_oxygen")
        with self.assertRaises(ValueError):
            self.host.host_set_line_gas("mfc_a", gas_id="not_a_gas")

    def test_reference_conditions_use_library_density_or_become_unavailable(self):
        rho_ntp = next(r for r in load_library()["templates"] if r["substance_id"] == "gas_argon")[
            "values_at_reference_conditions"]["ntp_273.15K_101325Pa"]["density_kg_m3"]
        other = AlicatMCRuntime()
        other.create_instance("x")
        other.register_principal("p", visibility="agent")
        session = other.session("p")
        calls = [("power.set", {"enabled": True}), ("gas.select", {"gas_id": "gas_argon"}),
                 ("gas.set_reference_conditions", {"temperature_K": 273.15, "pressure_Pa": 101325.0})]
        for i, (cid, args) in enumerate(calls):
            self.assertEqual(session.invoke(instance_id="x", capability_id=cid, arguments=args, request_id=str(i))["status"], "succeeded")
        other.host_connect_ports("x", upstream_node_id="i", downstream_node_id="o")
        other.host_set_boundary("x", upstream_pressure_Pa=130000.0, downstream_pressure_Pa=101325.0, temperature_K=298.15, equilibrium=True, no_flow=True)
        other.host_configure_dynamics("x", time_constant_s=0.2, evidence="test")
        for i, cid in enumerate(("flow.zero", "flow.reset_total")):
            session.invoke(instance_id="x", capability_id=cid, arguments={}, request_id="z%d" % i)
        other.host_set_boundary("x", upstream_pressure_Pa=130000.0, downstream_pressure_Pa=101325.0, temperature_K=298.15, equilibrium=False, no_flow=False)
        session.invoke(instance_id="x", capability_id="flow.set_setpoint", arguments={"flow_rate_m3_s": ALICAT_MC_FS_M3_S / 2}, request_id="s")
        other.host_step(0.5)
        obs = session.read(instance_id="x")["observations"]
        q_ntp = obs["measurement.standard_flow_m3_s"]["value"]
        self.assertGreater(q_ntp, 0)
        self.assertAlmostEqual(obs["measurement.mass_flow_kg_s"]["value"], q_ntp * rho_ntp, delta=1e-18)  # NTP (0 °C, 1 atm) NIST density
        # reference state outside the NIST grid: standard flow still indicated, density-based readings unavailable
        session.invoke(instance_id="x", capability_id="flow.set_setpoint", arguments={"flow_rate_m3_s": 0}, request_id="s0")
        other.host_set_boundary("x", upstream_pressure_Pa=130000.0, downstream_pressure_Pa=101325.0, temperature_K=298.15, equilibrium=True, no_flow=True)
        self.assertEqual(session.invoke(instance_id="x", capability_id="gas.set_reference_conditions",
                                        arguments={"temperature_K": 5e-324, "pressure_Pa": 1e308}, request_id="far")["status"], "succeeded")
        session.invoke(instance_id="x", capability_id="flow.zero", arguments={}, request_id="z9")
        obs = session.read(instance_id="x")["observations"]
        self.assertEqual(obs["measurement.standard_flow_m3_s"]["value"], 0.0)
        self.assertIsNone(obs["measurement.mass_flow_kg_s"]["value"])
        self.assertIsNone(obs["measurement.actual_flow_m3_s"]["value"])
        json.dumps(obs, allow_nan=False)

    def test_line_gas_with_host_registered_selection_is_unavailable(self):
        self.start("synthetic_known_property")
        self.host.host_step(0.2)
        self.assertIsNotNone(self.value("measurement.standard_flow_m3_s"))
        self.host.host_set_line_gas("mfc_a", gas_id="gas_argon")
        self.assertIsNone(self.value("measurement.standard_flow_m3_s"))
        self.host.host_step(0.2)
        self.assertIsNone(self.value("truth.standard_flow_m3_s", debug=True))

    def test_condensing_gas_makes_flow_unavailable(self):
        self.ok("power.set", enabled=True)
        self.ok("gas.select", gas_id="gas_n_butane")
        self.host.host_connect_ports("mfc_a", upstream_node_id="in", downstream_node_id="out")
        self.host.host_set_boundary("mfc_a", upstream_pressure_Pa=200000.0, downstream_pressure_Pa=101325.0, temperature_K=298.15, equilibrium=True, no_flow=True)
        self.host.host_configure_dynamics("mfc_a", time_constant_s=0.2, evidence="test")
        self.ok("flow.zero")
        self.ok("flow.reset_total")
        self.host.host_set_boundary("mfc_a", upstream_pressure_Pa=200000.0, downstream_pressure_Pa=101325.0, temperature_K=298.15, equilibrium=False, no_flow=False)
        self.ok("flow.set_setpoint", flow_rate_m3_s=ALICAT_MC_FS_M3_S / 4)
        self.host.host_step(0.5)
        self.assertGreater(self.value("measurement.standard_flow_m3_s"), 0)
        self.host.host_set_boundary("mfc_a", upstream_pressure_Pa=400000.0, downstream_pressure_Pa=101325.0, temperature_K=298.15, equilibrium=False, no_flow=False)
        self.host.host_step(0.5)
        self.assertIsNone(self.value("measurement.standard_flow_m3_s"))
        self.assertIsNone(self.value("truth.standard_flow_m3_s", debug=True))

    def test_mixture_flow_and_template_read_flow(self):
        self.start("mix_air")
        self.host.host_step(1.0)
        response = self.ok("flow.read_template")["result"]
        q = self.value("measurement.standard_flow_m3_s")
        self.assertEqual(response["measurement.standard_flow_m3_s"], q)
        self.assertEqual(response["gas_id"], "mix_air")
        self.assertEqual(response["evidence"], "derived")
        self.assertFalse(response["synthetic"])
        self.assertTrue(response["synthetic_components"])
        self.assertEqual(response["template_source"], amc.TEMPLATE_REL)
        self.assertEqual(response["library_sha256"], template_library.library_sha256(amc.TEMPLATE_REL))
        self.assertAlmostEqual(response["measurement.mass_flow_kg_s"], q * response["density_standard_kg_m3"], delta=1e-18)
        self.assertTrue(1.18 < response["density_standard_kg_m3"] < 1.19)
        self.assertTrue(18e-6 < response["viscosity_actual_Pa_s"] < 19e-6)
        self.assertEqual(validate_value(response, amc.FLOW_OUTPUT), [])

    def test_queries_validate_and_do_not_mutate(self):
        self.start("gas_nitrogen")
        self.host.host_step(0.3)
        before = self.host.debug_snapshot()
        listing = self.ok("template.list")["result"]
        self.assertEqual(listing["n_records"], len(load_library()["templates"]))
        self.assertEqual(validate_value(listing, amc.LIST_OUTPUT), [])
        read = self.ok("template.read", substance_id="gas_argon")["result"]
        self.assertEqual(validate_value(read, amc.READ_OUTPUT), [])
        self.assertEqual(read["cas"], "7440-37-1")
        self.assertEqual(len(read["isobar_1atm_viscosity_Pa_s"]), len(read["isobar_1atm_temperature_K"]))
        self.assertEqual(self.invoke("template.read", substance_id="nope")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.invoke("template.read", substance_id="gas_argon", extra=1)["error"]["code"], "INVALID_ARGUMENT")
        self.ok("flow.read_template")
        self.assertEqual(self.host.debug_snapshot()["instances"], before["instances"])
        self.assertEqual(self.host.debug_snapshot()["time_s"], before["time_s"])
        other = AlicatMCRuntime()
        other.create_instance("x")
        other.register_principal("p", visibility="agent")
        session = other.session("p")
        session.invoke(instance_id="x", capability_id="power.set", arguments={"enabled": True}, request_id="1")
        other.host_register_gas("h", compatibility_evidence="test")
        session.invoke(instance_id="x", capability_id="gas.select", arguments={"gas_id": "h"}, request_id="2")
        self.assertEqual(session.invoke(instance_id="x", capability_id="flow.read_template", arguments={}, request_id="3")["error"]["code"], "PRECONDITION_FAILED")

    def test_describe_lists_template_capabilities_with_lintable_schemas(self):
        spec = self.agent.describe(asset_type_id=ALICAT_MC_TYPE)
        for cid in amc.CAPABILITY_IDS:
            cap = spec["capabilities"][cid]
            self.assertEqual(cap["availability"], "implemented")
            self.assertEqual(lint_schema(cap["input_schema"]), [])
            self.assertEqual(lint_schema(cap["output_schema"]), [])
            for example in cap["example_arguments"]:
                self.assertEqual(validate_value(example, cap["input_schema"]), [])
        self.assertEqual(spec["asset_version"], amc.CONTRACT_ASSET_VERSION)

    def test_missing_library_falls_back_to_legacy_behaviour(self):
        original = amc.TEMPLATE_REL
        try:
            amc.TEMPLATE_REL = "assets/reactions/alicat_mc_gas_property_library/missing.json"
            self.ok("power.set", enabled=True)
            self.assertEqual(self.invoke("gas.select", gas_id="gas_nitrogen")["error"]["code"], "PRECONDITION_FAILED")
            self.assertNotIn("template.read", self.agent.describe(asset_type_id=ALICAT_MC_TYPE)["capabilities"])
            self.assertEqual(self.invoke("template.list")["error"]["code"], "UNKNOWN_CAPABILITY")
        finally:
            amc.TEMPLATE_REL = original


class TestAutomationContracts(unittest.TestCase):
    def test_alicat_contract_registers_templates_and_capabilities(self):
        contract = json.loads((TIER / "alicat_mc" / "asset.contract.json").read_text(encoding="utf-8"))
        block = contract["extensions"]["chemistry_lab.output_templates"]
        self.assertEqual(block["status"], "template")
        self.assertEqual(block["library"], amc.TEMPLATE_REL)
        self.assertEqual(block["n_records"], len(load_library()["templates"]))
        self.assertEqual(contract["asset_version"], amc.CONTRACT_ASSET_VERSION)
        self.assertEqual(contract["artifacts"]["template_library"]["sha256"], template_library.library_sha256(amc.TEMPLATE_REL))
        host = AlicatMCRuntime()
        host.create_instance("a")
        host.register_principal("p", visibility="agent")
        session = host.session("p")
        for cid in amc.CAPABILITY_IDS:
            cap = contract["capabilities"][cid]
            self.assertEqual(cap["implementation"]["process"], "verified")
        result = session.invoke(instance_id="a", capability_id="template.read", arguments={"substance_id": "gas_xenon"}, request_id="1")["result"]
        self.assertEqual(validate_value(result, contract["capabilities"]["template.read"]["output_schema"]), [])

    def test_other_automation_assets_are_not_applicable(self):
        for slug in NOT_APPLICABLE:
            with self.subTest(slug):
                contract = json.loads((TIER / slug / "asset.contract.json").read_text(encoding="utf-8"))
                block = contract["extensions"]["chemistry_lab.output_templates"]
                self.assertEqual(block["status"], "not_applicable")
                self.assertTrue(block["reason_zh"].strip())
                self.assertEqual(set(block), {"status", "reason_zh"})


if __name__ == "__main__":
    unittest.main()
