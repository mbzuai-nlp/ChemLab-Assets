# BEGIN CHEM-180 ALICAT MC APPEND-ONLY TESTS
"""Synthetic CHEM-180 fixtures; no hardware or real gas sample data."""
import copy as _amc_test_copy
import json as _amc_test_json
import math as _amc_test_math
from pathlib import Path as _amc_test_Path
import sys as _amc_test_sys
import unittest as _amc_test_unittest

_AMC_TEST_ROOT = _amc_test_Path(__file__).resolve().parents[1]
_amc_test_sys.path.insert(0, str(_AMC_TEST_ROOT / "scripts"))
_amc_test_sys.path.insert(0, str(_AMC_TEST_ROOT / "skills" / "chemistry-lab-assets" / "scripts"))
from basic40_runtime_automation import (AlicatMCRuntime, ALICAT_MC_TYPE,
    ALICAT_MC_FS_M3_S, ALICAT_MC_MIN_M3_S, ALICAT_MC_R, export_alicat_mc_contract_metadata)
from asset_tool import lint_schema as _amc_lint_schema, validate_value as _amc_validate_value


class TestAlicatMCRuntime(_amc_test_unittest.TestCase):
    def setUp(self):
        self.host = AlicatMCRuntime()
        self.host.create_instance("mfc_a")
        self.host.create_instance("mfc_b")
        self.host.host_register_gas("synthetic_gas", compatibility_evidence="Synthetic fixture compatibility only")
        self.host.host_register_gas("synthetic_known_property", compatibility_evidence="Synthetic fixture only", molar_mass_kg_mol=0.028, property_evidence="Explicit synthetic ideal gas property, not a vendor library entry")
        self.host.register_principal("learner", visibility="agent")
        self.host.register_principal("evaluator", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("evaluator")
        self.counter = 0

    def invoke(self, cid, instance="mfc_a", **arguments):
        self.counter += 1
        return self.agent.invoke(instance_id=instance, capability_id=cid, arguments=arguments, request_id="request_%d" % self.counter)

    def ok(self, cid, instance="mfc_a", **arguments):
        response = self.invoke(cid, instance, **arguments)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, sid, instance="mfc_a", debug=False):
        return (self.judge if debug else self.agent).read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def boundary(self, *, instance="mfc_a", equilibrium=True, no_flow=True, upstream=130000.0, downstream=101325.0, temperature=298.15):
        self.host.host_set_boundary(instance, upstream_pressure_Pa=upstream, downstream_pressure_Pa=downstream,
            temperature_K=temperature, equilibrium=equilibrium, no_flow=no_flow)

    def configure(self, *, instance="mfc_a", gas="synthetic_gas", tau=0.2):
        self.ok("power.set", instance, enabled=True)
        self.ok("gas.select", instance, gas_id=gas)
        self.host.host_connect_ports(instance, upstream_node_id=instance+"_supply", downstream_node_id=instance+"_receiver")
        self.boundary(instance=instance)
        if tau is not None:
            self.host.host_configure_dynamics(instance, time_constant_s=tau, evidence="Synthetic test assumption; independent of the 30 ms best-case vendor T63")
        self.ok("flow.zero", instance)
        self.ok("flow.reset_total", instance)

    def start(self, target=ALICAT_MC_FS_M3_S / 2, **configuration):
        self.configure(**configuration)
        instance = configuration.get("instance", "mfc_a")
        self.boundary(instance=instance, equilibrium=False, no_flow=False)
        self.ok("flow.set_setpoint", instance, flow_rate_m3_s=target)

    def test_catalog_default_scale_and_unknown_parameters(self):
        metadata = export_alicat_mc_contract_metadata()["asset_types"][ALICAT_MC_TYPE]
        self.assertEqual(ALICAT_MC_FS_M3_S, 1e-3/60)
        self.assertEqual(self.value("gas.reference_temperature_K"), 298.15)
        self.assertEqual(self.value("gas.reference_pressure_Pa"), 101325.0)
        self.assertIsNone(self.value("gas.selected_id"))
        for sid in ("measurement.standard_flow_m3_s", "measurement.mass_flow_kg_s", "measurement.actual_flow_m3_s", "measurement.total_standard_volume_m3", "measurement.pressure_Pa", "measurement.temperature_K"):
            response = self.agent.read(instance_id="mfc_a", state_ids=[sid])["observations"][sid]
            self.assertIsNone(response["value"])
            self.assertEqual(response["quality"], "unavailable")
            self.assertIn("reason", response)
        for param in metadata["parameters"].values():
            if param["evidence"] == "unknown":
                self.assertIsNone(param["value"])
        self.assertEqual(metadata["parameters"]["fastest_control_t63_s"]["value"], 0.03)
        self.assertIsNone(metadata["parameters"]["default_dynamic_time_constant_s"]["value"])

    def test_reads_describe_and_query_do_not_advance_or_mutate(self):
        self.start()
        before = self.host.debug_snapshot()
        for _ in range(3):
            self.agent.describe(instance_id="mfc_a")
            self.agent.describe(asset_type_id=ALICAT_MC_TYPE)
            self.agent.read(instance_id="mfc_a")
            self.ok("flow.read")
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_instances_share_only_clock_not_state(self):
        self.start()
        self.host.host_step(0.2)
        self.assertGreater(self.value("measurement.standard_flow_m3_s"), 0)
        self.assertIsNone(self.value("measurement.standard_flow_m3_s", "mfc_b"))
        self.assertFalse(self.value("power.enabled", "mfc_b"))
        self.assertEqual(self.value("flow.setpoint_m3_s", "mfc_b"), 0)
        for instance in ("mfc_a", "mfc_b"):
            self.assertEqual(self.agent.read(instance_id=instance)["clock"]["time_s"], 0.2)
        independent = AlicatMCRuntime()
        independent.create_instance("mfc_a")
        self.assertEqual(independent.time_s, 0)

    def test_requests_replay_and_conflict_without_reexecution(self):
        self.configure()
        args = dict(instance_id="mfc_a", capability_id="flow.set_setpoint", arguments={"flow_rate_m3_s": ALICAT_MC_FS_M3_S}, request_id="fixed")
        original = self.agent.invoke(**args)
        self.boundary(equilibrium=False, no_flow=False)
        self.host.host_step(1)
        before = self.host.debug_snapshot()
        replay = self.agent.invoke(**args)
        self.assertEqual(replay, original)
        replay["result"].clear()
        self.assertEqual(self.agent.invoke(**args), original)
        conflict = self.agent.invoke(**{**args, "arguments": {"flow_rate_m3_s": 0}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_setpoint_range_rejects_nonfinite_bool_under_minimum_and_over_fullscale(self):
        self.configure()
        for value in (-1, True, False, float("nan"), float("inf"), -float("inf"), 10**400, ALICAT_MC_MIN_M3_S/2, ALICAT_MC_FS_M3_S*1.001):
            with self.subTest(value=value):
                before = self.host.debug_snapshot()
                response = self.invoke("flow.set_setpoint", flow_rate_m3_s=value)
                self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(response["error"]["side_effects"], "none")
                self.assertEqual(self.host.debug_snapshot(), before)
        for value in (0, ALICAT_MC_MIN_M3_S, ALICAT_MC_FS_M3_S):
            self.ok("flow.set_setpoint", flow_rate_m3_s=value)
            self.assertEqual(self.value("flow.setpoint_m3_s"), value)

    def test_unknown_keys_bad_ids_and_invalid_host_inputs_do_not_mutate(self):
        before = self.host.debug_snapshot()
        for malformed in ([], {}, True, 0):
            self.assertEqual(self.agent.read(instance_id=malformed)["status"], "rejected")
            self.assertEqual(self.agent.describe(instance_id=malformed)["status"], "rejected")
            self.assertEqual(self.agent.invoke(instance_id=malformed, capability_id="power.set", arguments={"enabled": True}, request_id={})["status"], "rejected")
        self.assertEqual(self.invoke("power.set", enabled=True, role="evaluator")["error"]["code"], "INVALID_ARGUMENT")
        for value in (True, float("nan"), float("inf"), -1, 3601, 10**400):
            with self.assertRaises(ValueError):
                self.host.host_step(value)
        self.assertEqual(self.host.debug_snapshot(), before)
        with self.assertRaises(ValueError):
            self.host.host_register_gas("bad", compatibility_evidence="", molar_mass_kg_mol=0.02)
        with self.assertRaises(ValueError):
            self.host.host_configure_dynamics("mfc_a", time_constant_s=0, evidence="test")
        with self.assertRaises(ValueError):
            self.host.host_connect_ports("mfc_a", upstream_node_id="same", downstream_node_id="same")
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_gas_requires_host_compatibility_registration(self):
        self.ok("power.set", enabled=True)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("gas.select", gas_id="arbitrary_unregistered_gas")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.host.debug_snapshot(), before)
        self.ok("gas.select", gas_id="synthetic_gas")
        self.assertEqual(self.value("gas.selected_id"), "synthetic_gas")
        self.assertIsNone(self.value("measurement.mass_flow_kg_s"))

    def test_zero_requires_host_no_flow_equilibrium_and_valid_topology(self):
        self.ok("power.set", enabled=True)
        self.ok("gas.select", gas_id="synthetic_gas")
        for stage in ("unconnected", "unbalanced", "flowing", "invalid_temperature"):
            if stage != "unconnected":
                self.host.host_connect_ports("mfc_a", upstream_node_id="supply", downstream_node_id="receiver")
            if stage == "unbalanced":
                self.boundary(equilibrium=False)
            elif stage == "flowing":
                self.boundary(no_flow=False)
            elif stage == "invalid_temperature":
                self.boundary(temperature=350)
            before = self.host.debug_snapshot()
            response = self.invoke("flow.zero")
            self.assertEqual(response["error"]["code"], "PRECONDITION_FAILED", stage)
            self.assertEqual(self.host.debug_snapshot(), before)
        self.boundary()
        self.ok("flow.zero")
        self.assertTrue(self.value("flow.zeroed"))
        self.assertEqual(self.value("measurement.standard_flow_m3_s"), 0)
        self.assertEqual(self.host.time_s, 0)

    def test_no_default_dynamics_and_no_fixed_30ms_settling(self):
        self.start(tau=None)
        self.host.host_step(0.03)
        self.assertIsNone(self.value("measurement.standard_flow_m3_s"))
        self.assertIsNone(self.value("truth.response_time_constant_s", debug=True))
        self.assertFalse(self.value("measurement.accuracy_conditions_met"))
        self.assertIsNone(self.value("measurement.standard_accuracy_bound_m3_s"))
        self.assertFalse(self.value("simulation.dynamic_model_configured"))

    def test_explicit_tau_response_exact_integral_and_step_partition(self):
        self.start(tau=0.2)
        self.start(instance="mfc_b", tau=0.2)
        target = ALICAT_MC_FS_M3_S / 2
        self.host.host_step(0.2)
        self.assertAlmostEqual(self.value("measurement.standard_flow_m3_s"), target*(1-_amc_test_math.exp(-1)), delta=1e-17)
        expected_volume = target*(0.2-0.2*(1-_amc_test_math.exp(-1)))
        self.assertAlmostEqual(self.value("measurement.total_standard_volume_m3"), expected_volume, delta=1e-17)
        # Comparing two separately hosted schedules checks integral consistency.
        single = self.value("measurement.total_standard_volume_m3")
        other = TestAlicatMCRuntime()
        other.setUp()
        other.start(tau=0.2)
        for _ in range(20):
            other.host.host_step(0.01)
        self.assertAlmostEqual(other.value("measurement.total_standard_volume_m3"), single, delta=1e-17)
        self.assertEqual(self.value("measurement.standard_flow_m3_s"), self.value("measurement.standard_flow_m3_s", "mfc_b"))
        self.assertFalse(self.value("measurement.accuracy_conditions_met"))

    def test_accuracy_max_branch_and_host_equilibrium_not_elapsed_time(self):
        self.start(target=ALICAT_MC_FS_M3_S, tau=0.2)
        self.host.host_step(5)
        self.assertFalse(self.value("measurement.accuracy_conditions_met"))
        self.assertIsNone(self.value("measurement.standard_accuracy_bound_m3_s"))
        self.boundary(equilibrium=True, no_flow=False)
        q = self.value("measurement.standard_flow_m3_s")
        self.assertAlmostEqual(self.value("measurement.standard_accuracy_bound_m3_s"), 0.006*q)
        self.ok("flow.set_setpoint", flow_rate_m3_s=ALICAT_MC_FS_M3_S*0.01)
        self.assertFalse(self.value("measurement.accuracy_conditions_met"))
        self.host.host_step(5)
        self.boundary(equilibrium=True, no_flow=False)
        self.assertAlmostEqual(self.value("measurement.standard_accuracy_bound_m3_s"), 0.001*ALICAT_MC_FS_M3_S)
        self.ok("power.set", enabled=False)
        self.assertIsNone(self.value("measurement.standard_accuracy_bound_m3_s"))

    def test_standard_actual_and_mass_flow_have_explicit_reference_conversion(self):
        self.start(gas="synthetic_known_property", tau=0.2)
        self.boundary(equilibrium=False, no_flow=False, upstream=150000, temperature=310)
        self.host.host_step(0.4)
        q = self.value("measurement.standard_flow_m3_s")
        self.assertAlmostEqual(self.value("measurement.actual_flow_m3_s"), q*310/298.15*101325/150000, delta=1e-17)
        self.assertAlmostEqual(self.value("measurement.mass_flow_kg_s"), q*101325*.028/(ALICAT_MC_R*298.15), delta=1e-17)
        observation = self.agent.read(instance_id="mfc_a", state_ids=["measurement.pressure_Pa"])["observations"]["measurement.pressure_Pa"]
        self.assertEqual(observation["value"], 150000)
        self.assertEqual(observation["quality"], "estimated")

    def test_reference_change_invalidates_zero_and_total_interval(self):
        self.configure()
        self.assertEqual(self.value("measurement.total_standard_volume_m3"), 0)
        self.ok("gas.set_reference_conditions", temperature_K=273.15, pressure_Pa=100000)
        self.assertEqual(self.value("gas.reference_temperature_K"), 273.15)
        self.assertFalse(self.value("flow.zeroed"))
        self.assertIsNone(self.value("measurement.standard_flow_m3_s"))
        self.assertIsNone(self.value("measurement.total_standard_volume_m3"))
        self.ok("flow.set_setpoint", flow_rate_m3_s=ALICAT_MC_FS_M3_S/2)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("gas.set_reference_conditions", temperature_K=298.15, pressure_Pa=101325)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_ports_express_inlet_outlet_and_invalid_backpressure_is_unavailable(self):
        self.start()
        self.assertEqual(self.value("ports.upstream_node_id"), "mfc_a_supply")
        self.assertEqual(self.value("ports.downstream_node_id"), "mfc_a_receiver")
        self.host.host_step(0.1)
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError):
            self.host.host_connect_ports("mfc_a", upstream_node_id="new", downstream_node_id="newer")
        self.assertEqual(self.host.debug_snapshot(), before)
        self.boundary(equilibrium=False, no_flow=False, upstream=101325, downstream=150000)
        self.host.host_step(0.1)
        self.assertIsNone(self.value("measurement.standard_flow_m3_s"))
        self.assertIsNone(self.value("measurement.total_standard_volume_m3"))

    def test_target_zero_retains_transient_until_host_step_and_reset_total_is_explicit(self):
        self.start()
        self.host.host_step(0.2)
        before_q = self.value("measurement.standard_flow_m3_s")
        before_v = self.value("measurement.total_standard_volume_m3")
        self.ok("flow.set_setpoint", flow_rate_m3_s=0)
        self.assertEqual(self.value("measurement.standard_flow_m3_s"), before_q)
        self.assertEqual(self.value("measurement.total_standard_volume_m3"), before_v)
        self.host.host_step(0.2)
        self.assertAlmostEqual(self.value("measurement.standard_flow_m3_s"), before_q*_amc_test_math.exp(-1), delta=1e-17)
        self.assertGreater(self.value("measurement.total_standard_volume_m3"), before_v)
        self.ok("flow.reset_total")
        self.assertEqual(self.value("measurement.total_standard_volume_m3"), 0)
        self.assertGreater(self.value("measurement.standard_flow_m3_s"), 0)

    def test_agent_truth_filter_describe_read_query_logs_errors_and_identity(self):
        self.start()
        self.host.host_step(0.2)
        payloads = [self.agent.describe(instance_id="mfc_a"), self.agent.read(instance_id="mfc_a"), self.ok("flow.read"), self.agent.logs()]
        denied = self.agent.read(instance_id="mfc_a", state_ids=["truth.standard_flow_m3_s"])
        unknown = self.agent.read(instance_id="mfc_a", state_ids=["private_nonexistent"])
        self.assertEqual(denied["error"], unknown["error"])
        payloads += [denied, unknown]
        text = _amc_test_json.dumps(payloads)
        self.assertNotIn("truth.", text)
        self.assertNotIn("dynamics_evidence", text)
        self.assertNotIn("property_evidence", text)
        self.assertGreater(self.value("truth.standard_flow_m3_s", debug=True), 0)
        with self.assertRaises(TypeError):
            self.agent.read(instance_id="mfc_a", state_ids=["truth.standard_flow_m3_s"], role="evaluator")
        with self.assertRaises(ValueError):
            self.host.register_principal("learner", visibility="evaluator")
        with self.assertRaises(ValueError):
            self.host.session("not_authenticated")

    def test_scope_write_routing_unsupported_pid_and_host_step_hidden(self):
        self.host.register_principal("scoped", visibility="agent", instance_ids=["mfc_a"])
        scoped = self.host.session("scoped")
        self.assertEqual(scoped.read(instance_id="mfc_b")["error"]["code"], "UNKNOWN_INSTANCE")
        response = self.agent.write(instance_id="mfc_a", state_id="power.enabled", value=True, request_id="write_power")
        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(response, self.agent.invoke(instance_id="mfc_a", capability_id="power.set", arguments={"enabled": True}, request_id="write_power"))
        before = self.host.debug_snapshot()
        for sid in ("truth.standard_flow_m3_s", "measurement.standard_flow_m3_s", "gas.reference_temperature_K", "nonexistent"):
            self.assertEqual(self.agent.write(instance_id="mfc_a", state_id=sid, value=1, request_id=sid)["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.invoke("control.set_pid")["status"], "unsupported")
        self.assertEqual(self.invoke("sim.step", dt_s=1)["error"]["code"], "UNKNOWN_CAPABILITY")
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_extreme_positive_reference_and_molar_mass_never_emit_nonfinite_json(self):
        self.configure(gas="synthetic_known_property")
        self.ok("gas.set_reference_conditions", temperature_K=5e-324, pressure_Pa=1e308)
        self.ok("flow.zero")
        self.ok("flow.reset_total")
        self.boundary(equilibrium=False, no_flow=False)
        self.ok("flow.set_setpoint", flow_rate_m3_s=ALICAT_MC_FS_M3_S)
        self.host.host_step(0.2)
        result = self.agent.read(instance_id="mfc_a")
        _amc_test_json.dumps(result, allow_nan=False)
        self.assertIsNone(self.value("measurement.actual_flow_m3_s"))
        self.assertIsNone(self.value("measurement.mass_flow_kg_s"))

    def test_metadata_input_outputs_and_declared_immediate_effects_match_runtime(self):
        metadata = export_alicat_mc_contract_metadata()["asset_types"][ALICAT_MC_TYPE]
        for cid, cap in metadata["capabilities"].items():
            self.assertEqual(_amc_lint_schema(cap["input_schema"]), [], cid)
            self.assertEqual(_amc_lint_schema(cap["output_schema"]), [], cid)
            for example in cap["example_arguments"]:
                self.assertEqual(_amc_validate_value(example, cap["input_schema"]), [], cid)
        checked = set()
        def verify(cid, **arguments):
            before = self.host.debug_snapshot()["instances"]["mfc_a"]["values"]
            response = self.ok(cid, **arguments)
            after = self.host.debug_snapshot()["instances"]["mfc_a"]["values"]
            changed = {sid for sid in after if before[sid] != after[sid]}
            declared = {e["state_id"] for e in metadata["capabilities"][cid]["state_effects"]}
            self.assertLessEqual(changed, declared, (cid, changed-declared))
            self.assertEqual(_amc_validate_value(response["result"], metadata["capabilities"][cid]["output_schema"]), [], cid)
            checked.add(cid)
        verify("power.set", enabled=True)
        verify("gas.select", gas_id="synthetic_gas")
        verify("gas.set_reference_conditions", temperature_K=298.15, pressure_Pa=101325.0)
        self.host.host_connect_ports("mfc_a", upstream_node_id="supply", downstream_node_id="receiver")
        self.boundary()
        verify("flow.zero")
        verify("flow.reset_total")
        verify("flow.set_setpoint", flow_rate_m3_s=ALICAT_MC_FS_M3_S/2)
        verify("flow.read")
        self.assertEqual(checked, {cid for cid, cap in metadata["capabilities"].items() if cap["availability"] == "implemented"})
        values = self.host.debug_snapshot()["instances"]["mfc_a"]["values"]
        self.assertEqual(set(values), set(metadata["state_slots"]))
        for sid, slot in metadata["state_slots"].items():
            self.assertEqual(_amc_lint_schema(slot["value_schema"]), [], sid)
            self.assertEqual(_amc_validate_value(values[sid], slot["value_schema"]), [], sid)
    def test_reconnecting_ports_clears_old_boundaries_and_zero_assertions(self):
        self.configure()
        self.host.host_connect_ports("mfc_a", upstream_node_id="new_supply", downstream_node_id="new_receiver")
        for sid in ("truth.upstream_pressure_Pa", "truth.downstream_pressure_Pa", "truth.temperature_K"):
            self.assertIsNone(self.value(sid, debug=True))
        self.assertFalse(self.value("truth.no_flow", debug=True))
        self.assertFalse(self.value("truth.equilibrium", debug=True))
        self.assertFalse(self.value("flow.zeroed"))
        self.assertIsNone(self.value("measurement.pressure_Pa"))
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("flow.zero")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.host.debug_snapshot(), before)
        self.boundary()
        self.ok("flow.zero")
        self.assertTrue(self.value("flow.zeroed"))

    def test_unknown_flow_never_substitutes_for_host_confirmed_stopped_flow(self):
        self.start()
        self.host.host_step(0.1)
        self.boundary(equilibrium=False, no_flow=False, upstream=101325, downstream=150000)
        self.ok("flow.set_setpoint", flow_rate_m3_s=0)
        self.assertIsNone(self.value("truth.standard_flow_m3_s", debug=True))
        self.assertFalse(self.value("truth.no_flow", debug=True))
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("gas.select", gas_id="synthetic_known_property")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.invoke("gas.set_reference_conditions", temperature_K=273.15, pressure_Pa=100000)["error"]["code"], "PRECONDITION_FAILED")
        with self.assertRaises(ValueError):
            self.host.host_connect_ports("mfc_a", upstream_node_id="new_supply", downstream_node_id="new_receiver")
        with self.assertRaises(ValueError):
            self.host.host_configure_dynamics("mfc_a", time_constant_s=1, evidence="Test change must be rejected during unknown flow")
        self.assertEqual(self.host.debug_snapshot(), before)
        self.boundary()
        self.ok("gas.select", gas_id="synthetic_known_property")
# END CHEM-180 ALICAT MC APPEND-ONLY TESTS

# CHEM-181 P-702CV runtime validation; all numeric fixtures are synthetic scenarios.
import copy as _p702cv_test_copy
import importlib.util as _p702cv_importlib
import json as _p702cv_test_json
import math as _p702cv_test_math
from pathlib import Path as _P702CVPath
import sys as _p702cv_test_sys
import unittest as _p702cv_unittest

_p702cv_test_root = _P702CVPath(__file__).resolve().parents[1]
_p702cv_test_sys.path.insert(0, str(_p702cv_test_root / 'scripts'))
from basic40_runtime_automation import P702CVRuntime as _P702CVRuntime
from basic40_runtime_automation import export_p702cv_contract_metadata as _p702cv_export
from basic40_runtime_automation import P702CV_GAS_CONSTANT as _P702CV_R
_p702cv_tool_spec = _p702cv_importlib.spec_from_file_location('p702cv_asset_tool', _p702cv_test_root / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
_p702cv_asset_tool = _p702cv_importlib.module_from_spec(_p702cv_tool_spec)
_p702cv_tool_spec.loader.exec_module(_p702cv_asset_tool)


class TestP702CVRuntime(_p702cv_unittest.TestCase):
    def fixture(self, instance_id='p702cv_test', **changes):
        args = dict(upstream_volume_m3=0.001, temperature_K=293.15,
            initial_upstream_absolute_pressure_Pa=301325.0,
            downstream_absolute_pressure_Pa=101325.0, inflow_mol_s=0.0001,
            valve_conductance_mol_s_Pa=1e-8, pressure_reference_Pa=101325.0,
            control_gain_per_Pa_s=1e-6)
        args.update(changes)
        return _P702CVRuntime(instance_id, **args)

    def setUp(self):
        self.calls = []
        self.runtime = self.fixture(scene_adapter=lambda instance, values: self.calls.append((instance, _p702cv_test_copy.deepcopy(values))))
        self.runtime.register_principal('judge', visibility='evaluator')
        self.judge = self.runtime.session('judge')
        self.counter = 0

    def invoke(self, cap, **args):
        self.counter += 1
        return self.runtime.invoke(capability_id=cap, arguments=args, request_id='test_' + str(self.counter))

    def ok(self, cap, **args):
        response = self.invoke(cap, **args)
        self.assertEqual(response['status'], 'succeeded', response)
        errors = _p702cv_asset_tool.validate_value(response['result'], _p702cv_export()['capabilities'][cap]['output_schema'])
        self.assertEqual(errors, [])
        return response

    def value(self, state, debug=False):
        session = self.judge if debug else self.runtime
        return session.read(state_ids=[state])['observations'][state]['value']

    def advance(self, dt):
        return self.runtime.step(dt, world_time_s=self.runtime.debug_snapshot()['world_time_s'] + dt)

    def power(self):
        self.ok('power.set', enabled=True)

    def test_constructor_requires_scenario_values_and_unknown_specs_are_null(self):
        with self.assertRaises(TypeError):
            _P702CVRuntime('unconfigured')
        for key, value in [('upstream_volume_m3', 0), ('temperature_K', 260), ('inflow_mol_s', -1), ('valve_conductance_mol_s_Pa', None), ('control_gain_per_Pa_s', True), ('initial_upstream_absolute_pressure_Pa', float('inf'))]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.fixture(**{key: value})
        config = _p702cv_export()['configuration']
        self.assertEqual(config['sensor_full_scale_Pa'], 500000)
        self.assertEqual(config['gas'], 'N2')
        for key in ['vendor_valve_Kv', 'actual_valve_stroke_m', 'closed_loop_response_s']:
            self.assertIsNone(config[key])
        self.assertIn('sensor-only', config['sensor_response_scope'])

    def test_all_schemas_and_examples_pass_contract_tool(self):
        metadata = _p702cv_export()
        for key, spec in metadata['capabilities'].items():
            with self.subTest(capability=key):
                for field in ['input_schema', 'output_schema']:
                    self.assertEqual(_p702cv_asset_tool.lint_schema(spec[field]), [])
                self.assertEqual(_p702cv_asset_tool.validate_value(spec['example_arguments'], spec['input_schema']), [])
        for key, spec in metadata['state_slots'].items():
            self.assertEqual(_p702cv_asset_tool.lint_schema(spec['value_schema']), [], key)

    def test_public_read_does_not_advance_or_mutate(self):
        self.power()
        self.advance(0.1)
        before = self.runtime.debug_snapshot()
        for _ in range(5):
            self.runtime.read()
            self.runtime.describe()
            self.judge.read()
        self.assertEqual(before, self.runtime.debug_snapshot())
        self.assertEqual(self.runtime.read()['clock']['time_s'], 0.1)

    def test_ideal_pressure_observation_is_estimated_with_validity_limits(self):
        self.assertEqual(self.runtime.read(state_ids=['pressure.reading_pa'])['observations']['pressure.reading_pa']['unavailable_reason'], 'power_off')
        self.power()
        reading = self.runtime.read(state_ids=['pressure.reading_pa'])['observations']['pressure.reading_pa']
        self.assertEqual(reading['value'], 200000.0)
        self.assertEqual(reading['quality'], 'estimated')
        for absolute, valid in [(101325.0, False), (111325.0, True), (601325.0, True), (601326.0, False)]:
            r = self.fixture('range', initial_upstream_absolute_pressure_Pa=absolute)
            r.invoke(capability_id='power.set', arguments={'enabled': True}, request_id='on')
            obs = r.read(state_ids=['pressure.reading_pa'])['observations']['pressure.reading_pa']
            self.assertEqual(obs['value'] is not None, valid)
            self.assertEqual(obs['quality'], 'estimated' if valid else 'unavailable')

    def test_gas_balance_closed_outlet_accumulates_only_supplied_inflow(self):
        initial = self.value('process.upstream_moles_mol', True)
        p0 = self.value('process.upstream_absolute_pressure_pa', True)
        self.advance(20)
        n = self.value('process.upstream_moles_mol', True)
        self.assertAlmostEqual(n - initial, 0.0001 * 20, places=14)
        self.assertAlmostEqual(self.value('process.upstream_absolute_pressure_pa', True), p0 + _P702CV_R * 293.15 / 0.001 * 0.0001 * 20, places=8)
        self.assertEqual(self.value('process.integrated_outflow_mol', True), 0.0)

    def test_backpressure_controller_cannot_create_pressure_without_inflow(self):
        r = self.fixture(inflow_mol_s=0.0)
        before = r.debug_snapshot()['state']['process.upstream_absolute_pressure_pa']
        r.step(300, world_time_s=300)
        self.assertEqual(r.debug_snapshot()['state']['process.upstream_absolute_pressure_pa'], before)

    def test_open_outlet_matches_analytic_solution_and_gas_conservation(self):
        self.power()
        self.ok('valve.set_opening', opening=0.7)
        n0 = self.value('process.upstream_moles_mol', True)
        dt = 100.0
        a = _P702CV_R * 293.15 / 0.001
        k = 0.7e-8
        equilibrium = 101325.0 + 0.0001 / k
        expected = equilibrium + (301325.0 - equilibrium) * _p702cv_test_math.exp(-a * k * dt)
        self.advance(dt)
        s = self.runtime.debug_snapshot()['state']
        self.assertAlmostEqual(s['process.upstream_absolute_pressure_pa'], expected, places=8)
        self.assertGreater(s['process.integrated_outflow_mol'], 0)
        self.assertAlmostEqual(s['process.upstream_moles_mol'] + s['process.integrated_outflow_mol'], n0 + s['process.integrated_inflow_mol'], places=14)
        self.assertAlmostEqual(s['process.upstream_absolute_pressure_pa'] * 0.001, s['process.upstream_moles_mol'] * _P702CV_R * 293.15, places=10)

    def test_downstream_check_prevents_reverse_flow_and_handles_crossing(self):
        r = self.fixture(initial_upstream_absolute_pressure_Pa=100000.0, downstream_absolute_pressure_Pa=110000.0, inflow_mol_s=0.0)
        for cap, args in [('power.set', {'enabled': True}), ('valve.set_opening', {'opening': 1.0})]:
            r.invoke(capability_id=cap, arguments=args, request_id=cap)
        r.step(50.0, world_time_s=50.0)
        s = r.debug_snapshot()['state']
        self.assertEqual(s['process.upstream_absolute_pressure_pa'], 100000.0)
        self.assertEqual(s['process.integrated_outflow_mol'], 0.0)
        r.set_boundary(inflow_mol_s=0.001, downstream_absolute_pressure_Pa=110000.0)
        r.step(20.0, world_time_s=70.0)
        s = r.debug_snapshot()['state']
        self.assertGreater(s['process.upstream_absolute_pressure_pa'], 110000.0)
        self.assertGreater(s['process.integrated_outflow_mol'], 0.0)

    def test_automatic_control_has_correct_backpressure_sign(self):
        self.power()
        self.ok('pressure.set_setpoint', pressure_Pa=100000.0)
        self.ok('control.set_mode', mode='automatic')
        self.advance(0.1)
        self.assertAlmostEqual(self.value('valve.opening'), 0.01)
        self.assertGreater(self.value('pressure.deviation_pa'), 0)
        self.ok('pressure.set_setpoint', pressure_Pa=400000.0)
        self.advance(0.1)
        self.assertEqual(self.value('valve.opening'), 0.0)

    def test_ramp_changes_active_target_only_during_host_step(self):
        self.power()
        self.ok('pressure.set_setpoint', pressure_Pa=200000.0)
        self.ok('control.set_ramp_rate', rate_Pa_s=10000.0)
        self.ok('pressure.set_setpoint', pressure_Pa=400000.0)
        self.assertEqual(self.value('pressure.active_setpoint_pa'), 200000.0)
        self.advance(0.5)
        self.assertEqual(self.value('pressure.active_setpoint_pa'), 205000.0)
        self.ok('control.set_ramp_rate', rate_Pa_s=None)
        self.advance(0.1)
        self.assertEqual(self.value('pressure.active_setpoint_pa'), 400000.0)

    def test_manual_and_automatic_preconditions_are_atomic(self):
        cases = [('pressure.set_setpoint', {'pressure_Pa': 200000.0})]
        for cap, args in cases:
            before = self.runtime.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)['error']['code'], 'PRECONDITION_FAILED')
            self.assertEqual(before, self.runtime.debug_snapshot())
        self.power()
        before = self.runtime.debug_snapshot()
        self.assertEqual(self.invoke('control.set_mode', mode='automatic')['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.runtime.debug_snapshot())
        self.ok('pressure.set_setpoint', pressure_Pa=200000.0)
        self.ok('control.set_mode', mode='automatic')
        before = self.runtime.debug_snapshot()
        self.assertEqual(self.invoke('valve.set_opening', opening=0.5)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.runtime.debug_snapshot())

    def test_sensor_invalid_automatic_holds_output_and_reports_condition(self):
        self.power()
        self.ok('pressure.set_setpoint', pressure_Pa=200000.0)
        self.ok('control.set_mode', mode='automatic')
        self.runtime.set_boundary(inflow_mol_s=1.0, downstream_absolute_pressure_Pa=101325.0)
        self.advance(1.0)
        self.assertIsNone(self.value('pressure.reading_pa'))
        self.assertEqual(self.value('control.status'), 'sensor_unavailable')
        opening = self.value('valve.opening')
        self.advance(0.1)
        self.assertEqual(self.value('valve.opening'), opening)

    def test_power_off_closes_simulated_outlet_and_leaves_scene_independent(self):
        self.power()
        self.ok('valve.set_opening', opening=0.8)
        self.ok('scene.set_valve_position', position=0.3)
        self.ok('power.set', enabled=False)
        self.assertEqual(self.value('valve.opening'), 0.0)
        self.assertEqual(self.value('scene.valve_position'), 0.3)
        self.assertIsNone(self.value('pressure.reading_pa'))
        self.assertEqual(self.value('control.status'), 'power_off')

    def test_invalid_arguments_and_huge_numbers_do_not_mutate(self):
        self.power()
        for cap, args in [('power.set', {'enabled': 1}), ('pressure.set_setpoint', {'pressure_Pa': 40000000}), ('pressure.set_setpoint', {'pressure_Pa': 99999}), ('pressure.set_setpoint', {'pressure_Pa': True}), ('valve.set_opening', {'opening': float('nan')}), ('control.set_gain', {'gain_per_Pa_s': 10 ** 400}), ('control.set_mode', {'mode': 'automatic', 'visibility': 'debug'}), ('control.set_ramp_rate', {'rate_Pa_s': -1}), ('scene.set_valve_position', {'position': 1.01})]:
            with self.subTest(cap=cap):
                before = self.runtime.debug_snapshot()
                response = self.invoke(cap, **args)
                self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT')
                self.assertEqual(response['error']['side_effects'], 'none')
                self.assertEqual(before, self.runtime.debug_snapshot())
        for cap, args in [([], {}), ('unknown.capability', {})]:
            self.assertEqual(self.invoke(cap, **args)['error']['code'], 'UNKNOWN_CAPABILITY')

    def test_bad_world_clock_and_boundary_are_atomic(self):
        before = self.runtime.debug_snapshot()
        for dt, time in [(0, 0), (1e-9, 0), (1, 2), (float('nan'), 1), (True, 1), (10 ** 400, 10 ** 400)]:
            with self.assertRaises(ValueError):
                self.runtime.step(dt, world_time_s=time)
            self.assertEqual(before, self.runtime.debug_snapshot())
        with self.assertRaises(ValueError):
            self.runtime.set_boundary(inflow_mol_s=-0.1, downstream_absolute_pressure_Pa=100000.0)
        self.assertEqual(before, self.runtime.debug_snapshot())

    def test_request_replay_and_conflict_are_idempotent_after_time_step(self):
        args = dict(capability_id='power.set', arguments={'enabled': True}, request_id='same')
        first = self.runtime.invoke(**args)
        self.advance(0.1)
        before = self.runtime.debug_snapshot()
        self.assertEqual(self.runtime.invoke(**args), first)
        self.assertEqual(self.runtime.debug_snapshot(), before)
        conflict = self.runtime.invoke(**{**args, 'arguments': {'enabled': False}})
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(self.runtime.debug_snapshot(), before)

    def test_write_routes_through_capability_and_truth_is_read_only(self):
        self.power()
        response = self.runtime.write(state_id='pressure.setpoint_pa', value=250000.0, request_id='write')
        self.assertEqual(response['status'], 'succeeded')
        self.assertEqual(self.runtime.invoke(capability_id='pressure.set_setpoint', arguments={'pressure_Pa': 250000.0}, request_id='write'), response)
        before = self.runtime.debug_snapshot()
        response = self.judge.write(state_id='process.upstream_moles_mol', value=1.0, request_id='truth')
        self.assertEqual(response['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(before, self.runtime.debug_snapshot())

    def test_agent_truth_and_host_capabilities_are_filtered(self):
        self.power()
        metadata = self.runtime.describe()
        self.assertNotIn('simulation.step', metadata['capabilities'])
        self.assertNotIn('simulation.set_boundary', metadata['capabilities'])
        serialized = _p702cv_test_json.dumps([metadata, self.runtime.read()])
        self.assertNotIn('process.upstream_moles_mol', serialized)
        self.assertNotIn('process.upstream_absolute_pressure_pa', serialized)
        private = self.runtime.read(state_ids=['process.upstream_moles_mol'])
        unknown = self.runtime.read(state_ids=['unknown.private'])
        self.assertEqual(private['error'], unknown['error'])
        for cap in ['simulation.step', 'simulation.set_boundary']:
            self.assertEqual(self.invoke(cap)['error']['code'], 'UNKNOWN_CAPABILITY')
        with self.assertRaises(TypeError):
            self.runtime.read(state_ids=[], visibility='evaluator')
        with self.assertRaises(ValueError):
            self.runtime.session('unregistered')
        with self.assertRaises(ValueError):
            self.runtime.register_principal('judge', visibility='agent')

    def test_two_instances_have_independent_state_dedup_scene_and_same_clock(self):
        other_calls = []
        other = self.fixture('other', scene_adapter=lambda instance, values: other_calls.append(values))
        self.runtime.invoke(capability_id='power.set', arguments={'enabled': True}, request_id='same')
        other.invoke(capability_id='power.set', arguments={'enabled': False}, request_id='same')
        self.ok('scene.set_valve_position', position=0.5)
        other.step(0.5, world_time_s=0.5)
        self.runtime.step(0.5, world_time_s=0.5)
        self.assertTrue(self.value('power.enabled'))
        self.assertFalse(other.read(state_ids=['power.enabled'])['observations']['power.enabled']['value'])
        self.assertEqual(other.read(state_ids=['scene.valve_position'])['observations']['scene.valve_position']['value'], 0)
        self.assertEqual(other_calls, [])
        self.assertEqual(other.read()['clock'], self.runtime.read()['clock'])

    def test_scene_adapter_rejection_is_atomic_and_records_external_uncertainty(self):
        missing = self.fixture('missing')
        before = missing.debug_snapshot()
        response = missing.invoke(capability_id='scene.set_valve_position', arguments={'position': 1.0}, request_id='scene')
        self.assertEqual(response['status'], 'unsupported')
        self.assertEqual(before, missing.debug_snapshot())
        callbacks = []
        def broken(instance, values):
            callbacks.append(values)
            raise RuntimeError('private backend detail must never leak')
        failing = self.fixture('failing', scene_adapter=broken)
        before = failing.debug_snapshot()
        args = dict(capability_id='scene.set_valve_position', arguments={'position': 1.0}, request_id='scene')
        response = failing.invoke(**args)
        self.assertEqual(response['error']['code'], 'DEVICE_ERROR')
        self.assertEqual(response['error']['side_effects'], 'unknown')
        self.assertNotIn('private backend', _p702cv_test_json.dumps(response))
        self.assertEqual(before, failing.debug_snapshot())
        self.assertEqual(response, failing.invoke(**args))
        self.assertEqual(len(callbacks), 1)

    def test_all_capabilities_execute_with_schema_checked_outputs(self):
        self.power()
        self.ok('pressure.set_setpoint', pressure_Pa=200000.0)
        self.ok('control.set_gain', gain_per_Pa_s=2e-6)
        self.ok('control.set_ramp_rate', rate_Pa_s=10000.0)
        self.ok('valve.set_opening', opening=0.1)
        self.ok('scene.set_valve_position', position=0.8)
        self.ok('control.set_mode', mode='automatic')
        for cap, args in [('simulation.set_boundary', {'inflow_mol_s': 0.001, 'downstream_absolute_pressure_Pa': 100000.0}), ('simulation.step', {'dt_s': 0.1, 'world_time_s': 0.1})]:
            response = self.judge.invoke(capability_id=cap, arguments=args, request_id=cap)
            self.assertEqual(response['status'], 'succeeded', response)
            self.assertEqual(_p702cv_asset_tool.validate_value(response['result'], _p702cv_export()['capabilities'][cap]['output_schema']), [])
        for key, obs in self.judge.read()['observations'].items():
            self.assertEqual(_p702cv_asset_tool.validate_value(obs['value'], _p702cv_export()['state_slots'][key]['value_schema']), [], key)
        self.assertEqual(self.calls[-1], ('p702cv_test', {'scene.valve_position': 0.8}))

# CHEM-181 additional effect-coverage assertion (synthetic fixtures).
def _p702cv_test_declared_immediate_effects(self):
    self.power()
    sequence = [('pressure.set_setpoint', {'pressure_Pa': 200000.0}),
                ('control.set_gain', {'gain_per_Pa_s': 0.000002}),
                ('control.set_ramp_rate', {'rate_Pa_s': 5000.0}),
                ('valve.set_opening', {'opening': 0.8}),
                ('scene.set_valve_position', {'position': 0.4}),
                ('control.set_mode', {'mode': 'automatic'}),
                ('power.set', {'enabled': False})]
    for cap, args in sequence:
        before = self.runtime.debug_snapshot()['state']
        self.ok(cap, **args)
        after = self.runtime.debug_snapshot()['state']
        changed = {key for key in before if before[key] != after[key]}
        declared = set(_p702cv_export()['capabilities'][cap]['state_effects'])
        self.assertLessEqual(changed, declared, (cap, changed - declared))
TestP702CVRuntime.test_declared_immediate_effects_cover_actual_changes = _p702cv_test_declared_immediate_effects


# BEGIN CHEM-182 VICI EUTA APPEND-ONLY TESTS
# All clock values and host confirmations below are synthetic fixtures.
import copy as _veuta_test_copy
import importlib.util as _veuta_test_importlib
import json as _veuta_test_json
import math as _veuta_test_math
from pathlib import Path as _VeutaTestPath
import sys as _veuta_test_sys
import unittest as _veuta_test_unittest

_VEUTA_TEST_ROOT = _VeutaTestPath(__file__).resolve().parents[1]
_veuta_test_sys.path.insert(0, str(_VEUTA_TEST_ROOT / 'scripts'))
from basic40_runtime_automation import ViciEutaValveRuntime as _VeutaRuntime
from basic40_runtime_automation import export_vici_euta_contract_metadata as _veuta_metadata
from basic40_runtime_automation import VICI_EUTA_ASSET_TYPE_ID as _VEUTA_TYPE
_veuta_tool_spec = _veuta_test_importlib.spec_from_file_location('veuta_asset_tool', _VEUTA_TEST_ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
_veuta_asset_tool = _veuta_test_importlib.module_from_spec(_veuta_tool_spec)
_veuta_tool_spec.loader.exec_module(_veuta_asset_tool)


class TestViciEutaValveRuntime(_veuta_test_unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.valve = _VeutaRuntime('vici_test', scene_adapter=lambda instance, values: self.calls.append((instance, _veuta_test_copy.deepcopy(values))))
        self.request = 0

    def select(self, position, **changes):
        self.request += 1
        args = dict(capability_id='valve.select_position', arguments={'position': position}, request_id='selection_%d' % self.request)
        args.update(changes)
        return self.valve.invoke(**args)

    def arrive(self, position, **changes):
        self.request += 1
        args = dict(position=position, world_time_s=float(self.request), request_id='arrival_%d' % self.request)
        args.update(changes)
        return self.valve.host_arrive(**args)

    def value(self, state):
        return self.valve.read(state_ids=[state])['observations'][state]['value']

    def test_constructor_validates_id_scene_adapter_and_host_time(self):
        for name in ['', ' ', None, 1]:
            with self.assertRaises(ValueError):
                _VeutaRuntime(name)
        for time in [True, -1, float('nan'), float('inf'), 10 ** 400]:
            with self.assertRaises(ValueError):
                _VeutaRuntime('bad', initial_world_time_s=time)
        with self.assertRaises(ValueError):
            _VeutaRuntime('bad', scene_adapter=False)

    def test_initial_unknowns_and_required_configuration_are_honest(self):
        self.assertIsNone(self.value('valve.position'))
        self.assertIsNone(self.value('valve.target_position'))
        self.assertFalse(self.value('valve.in_position'))
        self.assertEqual(self.value('route.connections'), [])
        self.assertEqual(self.value('scene.rotor_angle_rad'), 0.0)
        self.assertEqual(self.value('scene.sync_status'), 'unbound')
        config = _veuta_metadata()['configuration']
        self.assertEqual(config['position_count'], 6)
        self.assertEqual(config['thermal_standoff_length_m'], 0.0508)
        self.assertEqual(config['bore_diameter_m'], 0.001)
        self.assertEqual(config['fitting_nominal_outer_diameter_m'], 0.003175)
        self.assertEqual(config['serial_interfaces'], ['RS232', 'RS485'])
        for key in ['switching_time_s', 'dead_volume_m3', 'residual_fraction', 'cross_contamination_fraction', 'whole_instrument_envelope_m', 'whole_instrument_mass_kg']:
            self.assertIsNone(config[key], key)
        obs = self.valve.read(state_ids=['valve.switching_time_s'])['observations']['valve.switching_time_s']
        self.assertIsNone(obs['value'])
        self.assertEqual(obs['quality'], 'unavailable')
        self.assertEqual(obs['unavailable_reason'], 'unknown_configuration_specification')

    def test_metadata_schemas_examples_and_state_values_validate(self):
        meta = _veuta_metadata()
        for cid, cap in {**meta['capabilities'], **meta['host_capabilities']}.items():
            for name in ['input_schema', 'output_schema']:
                self.assertEqual(_veuta_asset_tool.lint_schema(cap[name]), [], (cid, name))
            self.assertEqual(_veuta_asset_tool.validate_value(cap['example_arguments'], cap['input_schema']), [], cid)
        for state, definition in meta['state_slots'].items():
            self.assertEqual(_veuta_asset_tool.lint_schema(definition['value_schema']), [], state)
            self.assertEqual(_veuta_asset_tool.validate_value(self.value(state), definition['value_schema']), [], state)

    def test_selection_clears_confirmation_and_route_but_does_not_move(self):
        self.select(2)
        self.arrive(2)
        before = self.valve.debug_snapshot()
        callbacks = len(self.calls)
        result = self.select(5)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result'], {'valve.target_position': 5, 'valve.in_position': False, 'route.connected_port_id': None})
        self.assertFalse(self.value('valve.in_position'))
        self.assertIsNone(self.value('valve.position'))
        self.assertIsNone(self.value('route.connected_port_id'))
        self.assertEqual(self.value('route.connections'), [])
        self.assertEqual(self.value('scene.rotor_angle_rad'), before['state']['scene.rotor_angle_rad'])
        self.assertEqual(self.valve.debug_snapshot()['world_time_s'], before['world_time_s'])
        self.assertEqual(len(self.calls), callbacks)

    def test_all_six_arrivals_match_exact_one_to_common_topology_and_angles(self):
        meta = _veuta_metadata()
        for position in range(1, 7):
            selection = self.select(position)
            self.assertEqual(selection['status'], 'succeeded')
            self.assertEqual(_veuta_asset_tool.validate_value(selection['result'], meta['capabilities']['valve.select_position']['output_schema']), [])
            result = self.arrive(position)
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(_veuta_asset_tool.validate_value(result['result'], meta['host_capabilities']['host.arrive']['output_schema']), [])
            self.assertEqual(self.value('route.connections'), [['port_common', 'port_%d' % position]])
            self.assertEqual(self.value('route.connected_port_id'), 'port_%d' % position)
            self.assertEqual(self.value('valve.position'), position)
            self.assertTrue(self.value('valve.in_position'))
            expected = (position - 1) * _veuta_test_math.pi / 3
            self.assertAlmostEqual(self.value('scene.rotor_angle_rad'), expected)
            self.assertEqual(self.value('scene.sync_status'), 'synced')
            self.assertEqual(self.calls[-1], ('vici_test', {'scene.rotor_angle_rad': expected}))
            self.assertIsNone(self.value('valve.switching_time_s'))

    def test_read_and_discovery_are_pure_and_do_not_complete_switch(self):
        self.select(6)
        self.valve.host_set_time(world_time_s=1e9, request_id='advance')
        before = self.valve.debug_snapshot()
        for _ in range(5):
            self.valve.describe()
            self.valve.read()
        self.assertEqual(before, self.valve.debug_snapshot())
        self.assertFalse(self.value('valve.in_position'))
        self.assertEqual(self.value('route.connections'), [])
        self.assertEqual(self.calls, [])

    def test_strict_invalid_arguments_are_atomic(self):
        for position in [True, False, 1.0, '1', 0, 7, -1, None, [], float('nan'), float('inf'), 10 ** 400]:
            before = self.valve.debug_snapshot()
            response = self.select(position)
            self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT', position)
            self.assertEqual(response['error']['side_effects'], 'none')
            self.assertEqual(before, self.valve.debug_snapshot())
        for arguments in [{}, {'position': 1, 'extra': 2}, None, [1]]:
            before = self.valve.debug_snapshot()
            response = self.select(1, arguments=arguments)
            self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT')
            self.assertEqual(before, self.valve.debug_snapshot())
        for request in ['', ' ', None, 12, []]:
            response = self.select(1, request_id=request)
            self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(self.select(1, capability_id=[])['error']['code'], 'UNKNOWN_CAPABILITY')

    def test_busy_same_position_and_mismatched_arrival_are_atomic(self):
        self.assertEqual(self.arrive(2)['error']['code'], 'PRECONDITION_FAILED')
        self.select(2)
        before = self.valve.debug_snapshot()
        for position in [2, 3]:
            self.assertEqual(self.select(position)['error']['code'], 'BUSY')
            self.assertEqual(before, self.valve.debug_snapshot())
        self.assertEqual(self.arrive(4)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.valve.debug_snapshot())
        self.arrive(2)
        before = self.valve.debug_snapshot()
        self.assertEqual(self.select(2)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(self.arrive(2)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.valve.debug_snapshot())

    def test_replay_conflict_and_return_copy_are_isolated(self):
        first = self.select(4, request_id='one')
        expected = _veuta_test_copy.deepcopy(first)
        first['result']['valve.target_position'] = 6
        self.arrive(4)
        before = self.valve.debug_snapshot()
        replay = self.select(4, request_id='one')
        self.assertEqual(replay, expected)
        self.assertEqual(before, self.valve.debug_snapshot())
        self.assertEqual(self.select(5, request_id='one')['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(before, self.valve.debug_snapshot())

    def test_host_arrival_replay_does_not_reapply_old_pose(self):
        self.select(1)
        initial = self.arrive(1, request_id='arrive_once', world_time_s=1)
        self.select(3)
        self.arrive(3, world_time_s=2)
        before = self.valve.debug_snapshot()
        n = len(self.calls)
        self.assertEqual(self.arrive(1, request_id='arrive_once', world_time_s=1), initial)
        self.assertEqual(before, self.valve.debug_snapshot())
        self.assertEqual(len(self.calls), n)
        conflict = self.arrive(2, request_id='arrive_once', world_time_s=1)
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')

    def test_write_routes_through_selection_and_other_slots_are_read_only(self):
        response = self.valve.write(state_id='valve.target_position', value=5, request_id='write')
        self.assertEqual(response['status'], 'succeeded')
        self.assertEqual(self.select(5, request_id='write'), response)
        for state in ['valve.position', 'route.connections', 'scene.rotor_angle_rad', 'missing', []]:
            before = self.valve.debug_snapshot()
            result = self.valve.write(state_id=state, value=0, request_id='reject_write')
            self.assertEqual(result['error']['code'], 'ACCESS_DENIED')
            self.assertEqual(before, self.valve.debug_snapshot())

    def test_unknown_instance_and_host_capabilities_are_not_agent_accessible(self):
        for capability in ['host.arrive', 'host.set_time']:
            self.assertNotIn(capability, self.valve.describe()['capabilities'])
            response = self.valve.invoke(capability_id=capability, arguments={}, request_id='hidden')
            self.assertEqual(response['error']['code'], 'UNKNOWN_CAPABILITY')
        self.assertNotIn('host_capabilities', self.valve.describe())
        self.assertEqual(self.valve.describe(asset_type_id=_VEUTA_TYPE)['asset_type_id'], _VEUTA_TYPE)
        self.assertEqual(self.valve.describe(asset_type_id=_VEUTA_TYPE, instance_id='vici_test')['error']['code'], 'INVALID_ARGUMENT')
        before = self.valve.debug_snapshot()
        for response in [self.valve.read(instance_id='other'), self.valve.describe(instance_id='other'),
                         self.select(1, instance_id='other'), self.valve.write(instance_id='other', state_id='valve.target_position', value=1, request_id='write')]:
            self.assertEqual(response['error']['code'], 'UNKNOWN_INSTANCE')
        self.assertEqual(before, self.valve.debug_snapshot())
        with self.assertRaises(TypeError):
            self.valve.invoke(capability_id='host.arrive', arguments={}, request_id='hack', visibility='evaluator')

    def test_world_clock_is_explicit_monotonic_and_does_not_automatically_arrive(self):
        self.select(4)
        result = self.valve.host_set_time(world_time_s=100, request_id='clock')
        self.assertEqual(result['status'], 'succeeded')
        self.assertFalse(self.value('valve.in_position'))
        self.assertEqual(_veuta_asset_tool.validate_value(result['result'], _veuta_metadata()['host_capabilities']['host.set_time']['output_schema']), [])
        before = self.valve.debug_snapshot()
        for time in [-1, True, '100', None, float('nan'), float('inf'), 10 ** 400]:
            response = self.valve.host_set_time(world_time_s=time, request_id='bad_time')
            self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT')
            self.assertEqual(before, self.valve.debug_snapshot())
        for time in [99, 100]:
            response = self.valve.host_set_time(world_time_s=time, request_id='not_later')
            self.assertEqual(response['error']['code'], 'PRECONDITION_FAILED')
            self.assertEqual(before, self.valve.debug_snapshot())
        self.assertEqual(self.arrive(4, world_time_s=99)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.valve.debug_snapshot())
        self.assertEqual(self.arrive(4, world_time_s=100)['status'], 'succeeded')
        self.assertIsNone(self.value('valve.switching_time_s'))

    def test_unbound_scene_topology_is_honestly_reported(self):
        valve = _VeutaRuntime('headless')
        valve.invoke(capability_id='valve.select_position', arguments={'position': 3}, request_id='select')
        response = valve.host_arrive(position=3, world_time_s=0, request_id='arrive')
        self.assertEqual(response['status'], 'succeeded')
        self.assertEqual(response['result']['scene.sync_status'], 'unbound')
        self.assertTrue(response['result']['valve.in_position'])

    def test_callback_failure_keeps_state_atomic_and_deduplicates_uncertain_effect(self):
        calls = []
        def broken(instance, values):
            calls.append((instance, values))
            raise RuntimeError('private implementation detail')
        valve = _VeutaRuntime('broken', scene_adapter=broken)
        valve.invoke(capability_id='valve.select_position', arguments={'position': 3}, request_id='select')
        before = valve.debug_snapshot()
        response = valve.host_arrive(position=3, world_time_s=1, request_id='arrival')
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['code'], 'DEVICE_ERROR')
        self.assertEqual(response['error']['side_effects'], 'unknown')
        self.assertNotIn('private implementation', _veuta_test_json.dumps(response))
        self.assertEqual(before, valve.debug_snapshot())
        self.assertEqual(response, valve.host_arrive(position=3, world_time_s=1, request_id='arrival'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(before, valve.debug_snapshot())

    def test_two_instances_metadata_observations_and_requests_are_isolated(self):
        other = _VeutaRuntime('other')
        pristine = other.debug_snapshot()
        self.select(6, request_id='shared')
        self.arrive(6)
        self.assertEqual(other.debug_snapshot(), pristine)
        response = other.invoke(capability_id='valve.select_position', arguments={'position': 1}, request_id='shared')
        self.assertEqual(response['status'], 'succeeded')
        other.host_arrive(position=1, world_time_s=2, request_id='arrival')
        self.assertEqual(other.read()['clock'], self.valve.read()['clock'])
        self.assertEqual(other.read(state_ids=['route.connections'])['observations']['route.connections']['value'], [['port_common', 'port_1']])
        observation = self.valve.read()
        observation['observations']['route.connections']['value'][0][1] = 'poison'
        self.assertEqual(self.value('route.connections'), [['port_common', 'port_6']])
        metadata = self.valve.describe()
        metadata['capabilities']['valve.select_position']['input_schema']['properties']['position']['maximum'] = 7
        self.assertEqual(self.valve.describe()['capabilities']['valve.select_position']['input_schema']['properties']['position']['maximum'], 6)

    def test_declared_effects_cover_actual_changed_state(self):
        caps = {**_veuta_metadata()['capabilities'], **_veuta_metadata()['host_capabilities']}
        calls = [('valve.select_position', lambda: self.select(3)),
                 ('host.arrive', lambda: self.arrive(3)),
                 ('host.set_time', lambda: self.valve.host_set_time(world_time_s=100, request_id='time')),
                 ('valve.select_position', lambda: self.select(5))]
        for capability, invoke in calls:
            before = self.valve.debug_snapshot()['state']
            response = invoke()
            self.assertEqual(response['status'], 'succeeded')
            after = self.valve.debug_snapshot()['state']
            changed = {k for k in before if before[k] != after[k]}
            self.assertLessEqual(changed, set(caps[capability]['state_effects']))
# END CHEM-182 VICI EUTA APPEND-ONLY TESTS


# BEGIN CHEM-179 ZIATH DP5 APPEND-ONLY TESTS
import csv as _dp5_test_csv
import io as _dp5_test_io
import json as _dp5_test_json
import unittest as _dp5_test_unittest
import sys as _dp5_test_sys
from pathlib import Path as _dp5_test_Path
_DP5_TEST_ROOT = _dp5_test_Path(__file__).resolve().parents[1]
_dp5_test_sys.path.insert(0, str(_DP5_TEST_ROOT / 'scripts'))
_dp5_test_sys.path.insert(0, str(_DP5_TEST_ROOT / 'skills/chemistry-lab-assets/scripts'))
from basic40_runtime_automation import ZiathDP5Runtime as _ZiathDP5Runtime, ZIATH_DP5_TYPE as _DP5_TEST_TYPE, export_ziath_dp5_contract_metadata as _dp5_metadata
from asset_tool import lint_schema as _dp5_lint_schema, validate_value as _dp5_validate_value


class TestZiathDP5Runtime(_dp5_test_unittest.TestCase):
    def setUp(self):
        self.calls = []
        def scene(instance, targets):
            self.calls.append((instance, targets))
            return True
        self.host = _ZiathDP5Runtime()
        self.host.create_instance('dp5_a', scene_adapter=scene)
        self.host.create_instance('dp5_b', scene_adapter=scene)
        self.host.register_principal('learner', visibility='agent')
        self.host.register_principal('second', visibility='agent', instance_ids=['dp5_a'])
        self.host.register_principal('judge', visibility='evaluator')
        self.agent, self.judge, self.second = self.host.session('learner'), self.host.session('judge'), self.host.session('second')
        self.counter = 0

    def invoke(self, cid, instance='dp5_a', **args):
        self.counter += 1
        return self.agent.invoke(instance_id=instance, capability_id=cid, arguments=args, request_id='t_%d' % self.counter)

    def ok(self, cid, instance='dp5_a', **args):
        result = self.invoke(cid, instance, **args)
        self.assertIn(result['status'], ['succeeded', 'accepted'], result)
        self.assertEqual(_dp5_validate_value(result['result'], _dp5_metadata()['capabilities'][cid]['output_schema']), [], cid)
        return result

    def value(self, sid, instance='dp5_a', debug=False):
        return (self.judge if debug else self.agent).read(instance_id=instance, state_ids=[sid])['observations'][sid]['value']

    def load(self, capacity=24, instance='dp5_a'):
        self.ok('power.set', instance, enabled=True)
        self.ok('configuration.set', instance, rack_format=capacity, identity_matching=True, export_format='csv')
        wells = ['W%03d' % (i + 1) for i in range(capacity)]
        self.host.host_load_rack(instance, rack_id='rack_' + instance, rack_format=capacity, well_ids=wells,
            visible_barcodes={'W001': 'PRIVATE_MATCH', 'W002': 'PRIVATE_WRONG', 'W004': 'PRIVATE_EXTRA', 'W005': 'PRIVATE_MATCH'},
            task_manifest={'W001': 'PRIVATE_MATCH', 'W002': 'PRIVATE_EXPECTED', 'W003': 'PRIVATE_MISSING'})
        return wells

    def step(self, dt):
        self.host.host_step(dt, world_time_s=self.host.time_s + dt)

    def test_metadata_schema_examples_capacities_and_unknown_specs(self):
        m = _dp5_metadata()
        self.assertEqual(m['asset_type_id'], _DP5_TEST_TYPE)
        self.assertEqual(m['parameters']['supported_rack_capacities']['value'], [24, 48, 96, 240])
        self.assertEqual(m['parameters']['scan_time_s']['value'], 2.0)
        for spec in m['parameters'].values():
            if spec['evidence'] == 'unknown':
                self.assertIsNone(spec['value'])
        for cid, cap in m['capabilities'].items():
            for key in ('input_schema', 'output_schema'):
                self.assertEqual(_dp5_lint_schema(cap[key]), [], (cid, key))
            for example in cap['example_arguments']:
                self.assertEqual(_dp5_validate_value(example, cap['input_schema']), [])
        for sid, slot in m['state_slots'].items():
            self.assertEqual(_dp5_lint_schema(slot['value_schema']), [], sid)

    def test_all_capacities_require_explicit_unique_wells(self):
        for cap in (24, 48, 96, 240):
            self.load(cap)
            self.assertEqual(len(self.value('rack.well_ids')), cap)
            self.assertEqual(self.calls[-1][1], {'scene.rack_format': cap})
            self.ok('acquisition.start')
            self.step(2.0)
            self.assertEqual(len(self.value('results.data')['mapping']), cap)
            self.host.host_unload_rack('dp5_a')
        self.assertFalse(self.value('rack.loaded'))
        self.assertEqual(self.value('truth.visible_barcodes', debug=True), [])

    def test_scan_timing_and_mapping_missing_mismatch_duplicates(self):
        self.load()
        start = self.ok('acquisition.start')
        self.assertEqual(start['status'], 'accepted')
        operation = start['operation_id']
        self.step(1.999)
        self.assertIsNone(self.value('results.data'))
        self.assertEqual(self.agent.poll(instance_id='dp5_a', operation_id=operation)['status'], 'running')
        self.step(.001)
        self.assertEqual(self.agent.poll(instance_id='dp5_a', operation_id=operation)['status'], 'succeeded')
        data = self.value('results.data')
        self.assertEqual(data['completed_time_s'], 2.0)
        self.assertEqual(data['mapping'][0]['status'], 'matched')
        self.assertEqual(data['mismatch_wells'], ['W002'])
        self.assertIn('W003', data['missing_wells'])
        self.assertEqual(data['unexpected_wells'], ['W004', 'W005'])
        self.assertEqual(data['duplicate_tube_ids'], ['PRIVATE_MATCH'])
        self.assertEqual(data['quality'], 'estimated')
        self.assertEqual(_dp5_validate_value(data, _dp5_metadata()['state_slots']['results.data']['value_schema']), [])

    def test_no_truth_before_scan_in_read_describe_invoke_poll_errors(self):
        self.load()
        start = self.ok('acquisition.start')
        self.step(1.0)
        values = [self.agent.read(instance_id='dp5_a'), self.agent.describe(instance_id='dp5_a'),
            self.agent.describe(asset_type_id=_DP5_TEST_TYPE), start, self.agent.poll(instance_id='dp5_a', operation_id=start['operation_id']),
            self.invoke('results.export', format='json')]
        serialized = _dp5_test_json.dumps(values)
        self.assertNotIn('PRIVATE_', serialized)
        self.assertNotIn('truth.visible_barcodes', serialized)
        private = self.agent.read(instance_id='dp5_a', state_ids=['truth.visible_barcodes'])
        unknown = self.agent.read(instance_id='dp5_a', state_ids=['no_such_private_state'])
        self.assertEqual(private, unknown)
        self.assertEqual(self.value('truth.visible_barcodes', debug=True)[0]['tube_id'], 'PRIVATE_MATCH')
        with self.assertRaises(TypeError):
            self.agent.read(instance_id='dp5_a', visibility='evaluator')
        with self.assertRaises(ValueError):
            self.host.session('unregistered')
        self.assertNotIn('host_api', self.agent.describe(instance_id='dp5_a'))
        for cap in ['host_load_rack', 'simulation.step', 'host.attach']:
            self.assertEqual(self.invoke(cap)['error']['code'], 'UNKNOWN_CAPABILITY')

    def test_read_describe_and_poll_never_advance_clock_or_mutate(self):
        self.load()
        op = self.ok('acquisition.start')['operation_id']
        self.step(.3)
        before = self.host.debug_snapshot()
        for _ in range(5):
            self.agent.read(instance_id='dp5_a')
            self.agent.describe(instance_id='dp5_a')
            self.agent.poll(instance_id='dp5_a', operation_id=op)
            self.ok('acquisition.poll', operation_id=op)
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_cancel_retains_time_progress_and_never_exposes_partial_ids(self):
        self.load()
        op = self.ok('acquisition.start')['operation_id']
        self.step(.6)
        cancelled = self.agent.cancel(instance_id='dp5_a', operation_id=op, request_id='cancel')
        self.assertEqual(cancelled['result']['status'], 'cancelled')
        self.assertEqual(cancelled['result']['elapsed_s'], .6)
        self.assertEqual(cancelled['result']['progress'], .3)
        self.step(10.0)
        self.assertEqual(self.value('acquisition.elapsed_s'), .6)
        self.assertIsNone(self.value('results.data'))
        self.assertNotIn('PRIVATE_', _dp5_test_json.dumps(cancelled))
        self.assertEqual(self.invoke('results.export', format='csv')['status'], 'rejected')
        self.ok('acquisition.start')
        self.assertEqual(self.value('acquisition.elapsed_s'), 0)
        self.step(2)
        self.assertIsNotNone(self.value('results.data'))

    def test_power_off_cancels_and_keeps_partial_time(self):
        self.load()
        op = self.ok('acquisition.start')['operation_id']
        self.step(.25)
        self.ok('power.set', enabled=False)
        self.assertFalse(self.value('power.enabled'))
        self.assertEqual(self.value('acquisition.status'), 'cancelled')
        self.assertEqual(self.agent.poll(instance_id='dp5_a', operation_id=op)['result']['elapsed_s'], .25)
        self.step(3)
        self.assertIsNone(self.value('results.data'))

    def test_busy_and_preconditions_reject_atomically(self):
        def reject(cap, code, **args):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)['error']['code'], code)
            self.assertEqual(self.host.debug_snapshot(), before)
        reject('acquisition.start', 'PRECONDITION_FAILED')
        self.load()
        self.ok('scene.set_rack_lift', lift_m=.05)
        reject('acquisition.start', 'PRECONDITION_FAILED')
        self.ok('scene.set_rack_lift', lift_m=0)
        self.ok('acquisition.start')
        reject('scene.set_rack_lift', 'BUSY', lift_m=.1)
        reject('configuration.set', 'BUSY', rack_format=24, identity_matching=True, export_format='json')
        reject('acquisition.start', 'BUSY')
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError):
            self.host.host_unload_rack('dp5_a')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_invalid_arguments_nonfinite_boolean_and_extra_fields_are_atomic(self):
        self.load()
        bad = [('power.set', {'enabled': 1}), ('scene.set_rack_lift', {'lift_m': True}), ('scene.set_rack_lift', {'lift_m': float('nan')}),
            ('scene.set_rack_lift', {'lift_m': float('inf')}), ('scene.set_rack_lift', {'lift_m': 10**400}), ('scene.set_rack_lift', {'lift_m': -.01}),
            ('scene.set_rack_lift', {'lift_m': .100001}), ('configuration.set', {'rack_format': True, 'identity_matching': True, 'export_format': 'json'}),
            ('configuration.set', {'rack_format': 384, 'identity_matching': True, 'export_format': 'json'}), ('acquisition.start', {'duration_s': 0}),
            ('results.export', {'format': 'xml'}), ('acquisition.poll', {'operation_id': None}), ('acquisition.start', {'visibility': 'evaluator'})]
        for cid, args in bad:
            with self.subTest(cid=cid, args=args):
                before = self.host.debug_snapshot()
                reply = self.invoke(cid, **args)
                self.assertEqual(reply['error']['code'], 'INVALID_ARGUMENT')
                self.assertEqual(reply['error']['side_effects'], 'none')
                self.assertEqual(self.host.debug_snapshot(), before)

    def test_clock_and_host_input_validation_are_atomic(self):
        self.load()
        for dt, t in [(0, 0), (-1, -1), (1, 2), (True, 1), (float('nan'), 1), (float('inf'), float('inf')), (10**400,10**400)]:
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.host_step(dt, world_time_s=t)
            self.assertEqual(self.host.debug_snapshot(), before)
        for change in [{'well_ids': ['X'] * 24}, {'visible_barcodes': {'alien': 'SECRET'}}, {'rack_format': 96}, {'visible_barcodes': {'W001': None}}]:
            args = {'rack_id': 'replacement', 'rack_format': 24, 'well_ids': ['W%03d' % (i+1) for i in range(24)], 'visible_barcodes': {}, 'task_manifest': {}}
            args.update(change)
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.host_load_rack('dp5_a', **args)
            self.assertEqual(self.host.debug_snapshot(), before)

    def test_export_json_csv_actual_outputs_and_matching_disabled(self):
        self.load()
        self.ok('configuration.set', rack_format=24, identity_matching=False, export_format='csv')
        self.ok('acquisition.start')
        self.step(2.0)
        data = self.value('results.data')
        self.assertEqual(data['mapping'][1]['status'], 'read')
        self.assertIsNone(data['mapping'][1]['expected_tube_id'])
        self.assertEqual(data['mismatch_wells'], [])
        json_result = self.ok('results.export', format='json')['result']
        self.assertEqual(_dp5_test_json.loads(json_result['content']), data)
        csv_result = self.ok('results.export', format='csv')['result']
        rows = list(_dp5_test_csv.DictReader(_dp5_test_io.StringIO(csv_result['content'])))
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[0]['tube_id'], 'PRIVATE_MATCH')
        self.assertEqual(rows[2]['status'], 'missing')
        self.assertEqual(csv_result['media_type'], 'text/csv')

    def test_request_replay_conflict_and_input_output_copy_isolation(self):
        self.load()
        req = dict(instance_id='dp5_a', capability_id='acquisition.start', arguments={}, request_id='same')
        first = self.agent.invoke(**req)
        self.step(.5)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**req), first)
        self.assertEqual(self.host.debug_snapshot(), before)
        changed = self.agent.invoke(**{**req, 'capability_id': 'power.set', 'arguments': {'enabled': False}})
        self.assertEqual(changed['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(self.host.debug_snapshot(), before)
        first['result']['progress'] = -1
        self.assertEqual(self.agent.invoke(**req)['result']['progress'], 0)
        state = self.agent.read(instance_id='dp5_a')
        state['observations']['rack.well_ids']['value'].append('MUTATION')
        self.assertEqual(len(self.value('rack.well_ids')), 24)

    def test_operation_binding_and_instance_scope_are_enforced(self):
        self.load()
        op = self.ok('acquisition.start')['operation_id']
        before = self.host.debug_snapshot()
        self.assertEqual(self.second.poll(instance_id='dp5_a', operation_id=op)['error']['code'], 'UNKNOWN_OPERATION')
        self.assertEqual(self.second.cancel(instance_id='dp5_a', operation_id=op, request_id='x')['error']['code'], 'UNKNOWN_OPERATION')
        self.assertEqual(self.agent.poll(instance_id='dp5_b', operation_id=op)['error']['code'], 'UNKNOWN_OPERATION')
        self.assertEqual(self.second.read(instance_id='dp5_b')['error']['code'], 'UNKNOWN_INSTANCE')
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_missing_and_failed_scene_callbacks_never_report_success(self):
        self.host.create_instance('absent')
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('power.set', 'absent', enabled=True)['status'], 'unsupported')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertEqual(self.agent.describe(instance_id='absent')['capabilities']['power.set']['availability'], 'unsupported')
        callbacks = []
        def broken(i, target):
            callbacks.append(target)
            raise RuntimeError('SECRET private callback detail')
        self.host.create_instance('broken', scene_adapter=broken)
        req = dict(instance_id='broken', capability_id='power.set', arguments={'enabled': True}, request_id='fail')
        before = self.host.debug_snapshot()
        response = self.agent.invoke(**req)
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['side_effects'], 'unknown')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertEqual(self.agent.invoke(**req), response)
        self.assertEqual(len(callbacks), 1)
        self.assertNotIn('SECRET', _dp5_test_json.dumps(response))
        self.host.create_instance('unconfirmed', scene_adapter=lambda i, target: None)
        self.assertEqual(self.invoke('power.set', 'unconfirmed', enabled=True)['error']['code'], 'DEVICE_ERROR')

    def test_two_instances_share_host_clock_but_not_data_or_requests(self):
        self.load(24, 'dp5_a')
        self.load(48, 'dp5_b')
        req = dict(capability_id='acquisition.start', arguments={}, request_id='same')
        a = self.agent.invoke(instance_id='dp5_a', **req)
        self.step(1.0)
        b = self.agent.invoke(instance_id='dp5_b', **req)
        self.assertNotEqual(a['operation_id'], b['operation_id'])
        self.step(1.0)
        self.assertIsNotNone(self.value('results.data', 'dp5_a'))
        self.assertIsNone(self.value('results.data', 'dp5_b'))
        self.assertEqual(self.value('acquisition.progress', 'dp5_b'), .5)
        self.assertEqual(self.agent.read(instance_id='dp5_a')['clock'], self.agent.read(instance_id='dp5_b')['clock'])
        self.ok('scene.set_rack_lift', 'dp5_a', lift_m=.1)
        self.assertEqual(self.value('scene.rack_lift_m', 'dp5_b'), 0)
        isolated = _ZiathDP5Runtime()
        isolated.create_instance('dp5_a')
        self.assertEqual(isolated.time_s, 0)

    def test_write_routes_only_authorized_writable_slots(self):
        self.load()
        reply = self.agent.write(instance_id='dp5_a', state_id='scene.rack_lift_m', value=.08, request_id='write')
        self.assertEqual(reply['status'], 'succeeded')
        self.assertEqual(self.value('scene.rack_lift_m'), .08)
        self.assertEqual(self.calls[-1][1], {'scene.rack_lift_m': .08})
        before = self.host.debug_snapshot()
        self.assertEqual(self.judge.write(instance_id='dp5_a', state_id='truth.visible_barcodes', value=[], request_id='no')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_host_attachment_confirmation_release_and_removal_boundaries(self):
        self.load()
        events = []
        def accept(i, event):
            events.append((i, event))
            return True
        event = self.host.host_attach('dp5_a', constraint_id='world_constraint_1', attachment_adapter=accept)
        self.assertEqual(event['event'], 'attach')
        self.assertEqual(self.value('rack.attachment_constraint_id'), 'world_constraint_1')
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError): self.host.host_unload_rack('dp5_a')
        with self.assertRaises(RuntimeError): self.host.host_release('dp5_a', attachment_adapter=lambda i,e: False)
        self.assertEqual(self.host.debug_snapshot(), before)
        self.ok('scene.set_rack_lift', lift_m=.1)
        release = self.host.host_release('dp5_a', attachment_adapter=accept)
        self.assertEqual(release['event'], 'release')
        self.assertEqual(self.value('scene.rack_lift_m'), .1)
        self.assertTrue(self.value('rack.loaded'))
        self.assertIsNone(self.value('rack.attachment_constraint_id'))
        self.host.host_unload_rack('dp5_a')
        self.assertFalse(self.value('rack.loaded'))
        self.assertEqual([e[1]['event'] for e in events], ['attach', 'release'])

    def test_declared_effects_cover_actual_changes_and_read_output_schemas(self):
        self.load()
        sequence = [('scene.set_rack_lift', {'lift_m': .03}), ('scene.set_rack_lift', {'lift_m': 0}),
            ('configuration.set', {'rack_format': 24, 'identity_matching': False, 'export_format': 'json'}), ('acquisition.start', {})]
        for cid, args in sequence:
            before = self.host.debug_snapshot()['instances']['dp5_a']['state']
            self.ok(cid, **args)
            after = self.host.debug_snapshot()['instances']['dp5_a']['state']
            changed = {sid for sid in before if before[sid] != after[sid]}
            declared = {entry['state_id'] for entry in _dp5_metadata()['capabilities'][cid]['state_effects']}
            self.assertLessEqual(changed, declared, (cid, changed - declared))
        self.step(2)
        for sid, obs in self.judge.read(instance_id='dp5_a')['observations'].items():
            self.assertEqual(_dp5_validate_value(obs['value'], _dp5_metadata()['state_slots'][sid]['value_schema']), [], sid)
# END CHEM-179 ZIATH DP5 APPEND-ONLY TESTS


# CHEM-179 historical-operation cache consistency after changing racks.
def _dp5_test_historical_operation_results(self):
    self.load()
    op = self.ok('acquisition.start')['operation_id']
    self.step(2.0)
    req = dict(instance_id='dp5_a', capability_id='acquisition.poll', arguments={'operation_id': op}, request_id='historic')
    initial = self.agent.invoke(**req)
    self.assertTrue(initial['result']['results_available'])
    self.host.host_unload_rack('dp5_a')
    fresh = self.agent.poll(instance_id='dp5_a', operation_id=op)
    self.assertEqual(fresh['status'], 'succeeded')
    self.assertFalse(fresh['result']['results_available'])
    self.assertFalse(self.ok('acquisition.poll', operation_id=op)['result']['results_available'])
    self.assertEqual(self.agent.invoke(**req), initial)
    self.assertIsNone(self.value('results.data'))
TestZiathDP5Runtime.test_historical_operation_result_availability = _dp5_test_historical_operation_results

# BEGIN CHEM-177 INTELLIXCAP S1 APPEND-ONLY TESTS
"""Synthetic geometry fixtures are assumptions, never manufacturer parameters."""
import copy as _ixs_test_copy
import json as _ixs_test_json
from pathlib import Path as _ixs_test_Path
import sys as _ixs_test_sys
import unittest as _ixs_test_unittest

_IXS_TEST_ROOT = next(p for p in _ixs_test_Path(__file__).resolve().parents if (p / 'skills' / 'chemistry-lab-assets' / 'scripts' / 'asset_tool.py').exists())
_ixs_test_sys.path.insert(0, str(_IXS_TEST_ROOT / 'scripts'))
_ixs_test_sys.path.insert(0, str(_IXS_TEST_ROOT / 'skills' / 'chemistry-lab-assets' / 'scripts'))
from basic40_runtime_automation import (IntelliXcapS1Runtime, INTELLIXCAP_S1_TYPE, export_intellixcap_s1_contract_metadata)
from asset_tool import lint_schema as _ixs_lint_schema, validate_value as _ixs_validate_value


class TestIntelliXcapS1Runtime(_ixs_test_unittest.TestCase):
    def setUp(self):
        self.host = IntelliXcapS1Runtime()
        for iid in ('capper_a', 'capper_b'):
            self.host.create_instance(iid)
        self.host.host_register_consumable('synthetic_15ml_type', cap_diameter_m=0.022, nominal_volume_m3=15e-6, compatibility_evidence='Synthetic fixture compatibility only')
        self.host.register_principal('learner', visibility='agent', instance_ids=['capper_a', 'capper_b'])
        self.host.register_principal('other', visibility='agent', instance_ids=['capper_a'])
        self.host.register_principal('judge', visibility='evaluator')
        self.agent = self.host.session('learner')
        self.other = self.host.session('other')
        self.judge = self.host.session('judge')
        self.counter = 0

    def invoke(self, cid, iid='capper_a', **args):
        self.counter += 1
        return self.agent.invoke(instance_id=iid, capability_id=cid, arguments=args, request_id='ixs_req_%s' % self.counter)

    def value(self, sid, iid='capper_a', session=None):
        return (session or self.agent).read(instance_id=iid, state_ids=[sid])['observations'][sid]['value']

    def configure(self, iid='capper_a'):
        self.assertEqual(self.invoke('power.set', iid, enabled=True)['status'], 'succeeded')
        self.assertEqual(self.invoke('consumable.select', iid, consumable_id='synthetic_15ml_type')['status'], 'succeeded')
        self.host.host_present_sample(iid, tube_id=iid+'_tube', cap_id=iid+'_cap', consumable_id='synthetic_15ml_type', evidence='Synthetic placed sample, no robot execution')
        self.host.host_configure_motion(iid, open_gap_m=0.030, closed_gap_m=0.022, rotation_delta_rad=1.2, initial_rotation_rad=0.0, clamp_duration_s=0.25, rotation_duration_s=0.5, release_duration_s=0.25, evidence='Synthetic demonstration motion, not manufacturer stroke or cycle calibration')

    def start(self, iid='capper_a'):
        response = self.invoke('cycle.start', iid)
        self.assertEqual(response['status'], 'accepted', response)
        return response['operation_id']

    def pending(self, kind, iid='capper_a'):
        return next(e for e in reversed(self.host.host_events(iid)) if e['kind'] == kind and not e['acknowledged'])

    def attach(self, op, iid='capper_a'):
        event = self.pending('attach', iid)
        self.host.host_confirm_attachment(iid, operation_id=op, event_id=event['event_id'], constraint_id=iid+'_external_constraint', evidence='Synthetic host test confirms its external constraint; not a physics engine test')

    def release(self, op, iid='capper_a'):
        event = self.pending('release', iid)
        self.host.host_confirm_release(iid, operation_id=op, event_id=event['event_id'], constraint_id=iid+'_external_constraint', evidence='Synthetic host test confirms external removal')

    def finish(self, op, iid='capper_a', succeeded=True):
        self.host.host_step(0.25)
        self.attach(op, iid)
        self.host.host_step(0.5)
        self.host.host_confirm_thread_result(iid, operation_id=op, succeeded=succeeded, evidence='Synthetic host result; no physical thread solve')
        self.release(op, iid)
        self.host.host_step(0.25)

    def test_unknown_defaults_and_catalog_scope(self):
        spec = export_intellixcap_s1_contract_metadata()
        self.assertEqual(spec['parameters']['whole_instrument_mass_kg'], 3.8)
        self.assertEqual(spec['parameters']['whole_instrument_dimensions_xyz_m'], [0.170, 0.240, 0.300])
        self.assertEqual(spec['parameters']['typical_cycle_upper_exclusive_s'], 3)
        self.assertEqual(spec['parameters']['nominal_tube_volume_classes_m3'], [15e-6, 50e-6])
        for key in ('default_rotation_delta_rad', 'default_jaw_travel_m', 'default_cycle_duration_s', 'torque_limit_N_m', 'clamp_force_N', 'spindle_speed_rad_s', 'component_masses_kg'):
            self.assertIsNone(spec['parameters'][key])
        for sid in ('rotation_rad', 'jaw_gap_m', 'cycle.host_confirmed', 'sample.tube_id', 'attachment.constraint_id'):
            observation = self.agent.read(instance_id='capper_a', state_ids=[sid])['observations'][sid]
            self.assertIsNone(observation['value'])
            self.assertEqual(observation['quality'], 'unavailable')
            self.assertTrue(observation['reason'])
        self.assertFalse(self.value('simulation.motion_configured'))
        self.assertEqual(self.invoke('cycle.start')['error']['code'], 'PRECONDITION_FAILED')

    def test_actual_schemas_inputs_results_and_observations(self):
        spec = export_intellixcap_s1_contract_metadata()
        for cid, cap in spec['capabilities'].items():
            self.assertFalse(_ixs_lint_schema(cap['input_schema']), cid)
            self.assertFalse(_ixs_lint_schema(cap['output_schema']), cid)
            self.assertFalse(_ixs_validate_value(cap['example_arguments'], cap['input_schema']), cid)
        for sid, slot in spec['state_slots'].items():
            self.assertFalse(_ixs_lint_schema(slot['value_schema']), sid)
        self.configure()
        for cid, args in [('power.set', {'enabled': True}), ('cycle.set_mode', {'mode': 'decap'}), ('consumable.select', {'consumable_id': 'synthetic_15ml_type'}), ('cycle.read', {}), ('cycle.start', {})]:
            response = self.invoke(cid, **args)
            self.assertFalse(_ixs_validate_value(response['result'], spec['capabilities'][cid]['output_schema']), response)
        self.host.host_step(0.125)
        observations = self.agent.read(instance_id='capper_a')['observations']
        for sid, obs in observations.items():
            self.assertFalse(_ixs_validate_value(obs['value'], spec['state_slots'][sid]['value_schema']), sid)
            self.assertEqual(obs['unit'], spec['state_slots'][sid]['unit'])

    def test_analytic_motion_waits_for_external_attach_and_result(self):
        self.configure()
        op = self.start()
        self.host.host_step(0.125)
        self.assertAlmostEqual(self.value('jaw_gap_m'), 0.026)
        self.assertEqual(self.value('rotation_rad'), 0)
        self.host.host_step(20)
        self.assertEqual(self.value('cycle.status'), 'awaiting_attach')
        self.assertAlmostEqual(self.value('jaw_gap_m'), 0.022)
        self.assertEqual(self.value('rotation_rad'), 0)
        self.assertIsNone(self.value('attachment.constraint_id'))
        self.attach(op)
        self.host.host_step(0.25)
        self.assertAlmostEqual(self.value('rotation_rad'), 0.6)
        self.assertAlmostEqual(self.value('jaw_gap_m'), 0.022)
        self.host.host_step(100)
        self.assertEqual(self.value('cycle.status'), 'awaiting_thread_result')
        self.assertEqual(self.agent.poll(operation_id=op)['status'], 'running')
        self.assertTrue(self.value('cycle.motion_completed'))
        self.assertIsNone(self.value('cycle.host_confirmed'))
        self.assertAlmostEqual(self.value('rotation_rad'), 1.2)
        self.host.host_confirm_thread_result('capper_a', operation_id=op, succeeded=True, evidence='Synthetic host confirms')
        self.host.host_step(100)
        self.assertEqual(self.value('cycle.status'), 'awaiting_release')
        self.release(op)
        self.host.host_step(0.125)
        self.assertAlmostEqual(self.value('jaw_gap_m'), 0.026)
        self.host.host_step(0.125)
        result = self.agent.poll(operation_id=op)
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['result']['cycle.host_confirmed'])
        self.assertIsNone(result['result']['attachment.constraint_id'])
        self.assertAlmostEqual(result['result']['jaw_gap_m'], 0.030)

    def test_recap_sign_and_previous_truth_invalidated(self):
        self.configure()
        op = self.start()
        self.finish(op)
        self.assertEqual(self.value('truth.thread_state', session=self.judge), 'open')
        self.assertEqual(self.invoke('cycle.set_mode', mode='recap')['status'], 'succeeded')
        recap = self.start()
        self.assertIsNone(self.value('truth.thread_state', session=self.judge))
        self.finish(recap)
        self.assertAlmostEqual(self.value('rotation_rad'), 0)
        self.assertEqual(self.value('truth.thread_state', session=self.judge), 'closed')

    def test_host_reported_failure_releases_without_fake_success(self):
        self.configure()
        op = self.start()
        self.finish(op, succeeded=False)
        response = self.agent.poll(operation_id=op)
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['code'], 'HOST_REPORTED_FAILURE')
        self.assertEqual(response['error']['side_effects'], 'partial')
        self.assertTrue(response['result']['cycle.motion_completed'])
        self.assertFalse(response['result']['cycle.host_confirmed'])
        self.assertIsNone(self.value('truth.thread_state', session=self.judge))

    def test_read_poll_describe_never_advance_or_mutate(self):
        self.configure()
        op = self.start()
        self.host.host_step(0.125)
        before = self.host.debug_snapshot()
        for _ in range(3):
            self.agent.read(instance_id='capper_a')
            self.agent.describe(asset_type_id=INTELLIXCAP_S1_TYPE)
            self.agent.describe(instance_id='capper_a')
            self.agent.poll(operation_id=op)
            self.invoke('cycle.read')
            self.host.host_motion_snapshot('capper_a')
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_idempotent_start_replay_and_conflict(self):
        self.configure()
        arguments = dict(instance_id='capper_a', capability_id='cycle.start', arguments={}, request_id='unique-start')
        first = self.agent.invoke(**arguments)
        self.host.host_step(0.125)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**arguments), first)
        self.assertEqual(self.agent.invoke(**{**arguments, 'capability_id': 'cycle.read'})['error']['code'], 'REQUEST_CONFLICT')
        replay = self.agent.invoke(**arguments)
        replay['result'].clear()
        self.assertEqual(self.agent.invoke(**arguments), first)
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_write_uses_declared_capability_and_rejects_scene_writes(self):
        response = self.agent.write(instance_id='capper_a', state_id='cycle.mode', value='recap', request_id='write-mode')
        self.assertEqual(response['result']['cycle.mode'], 'recap')
        via_invoke = self.agent.invoke(instance_id='capper_a', capability_id='cycle.set_mode', arguments={'mode': 'recap'}, request_id='write-mode')
        self.assertEqual(response, via_invoke)
        before = self.host.debug_snapshot()
        for sid in ('rotation_rad', 'jaw_gap_m', 'truth.thread_state', 'nonexistent'):
            self.assertEqual(self.agent.write(instance_id='capper_a', state_id=sid, value=1, request_id='bad-'+sid)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_instances_share_clock_but_not_state_sample_or_operations(self):
        self.configure('capper_a')
        self.configure('capper_b')
        op_a = self.start('capper_a')
        self.host.host_step(0.125)
        self.assertAlmostEqual(self.value('jaw_gap_m'), 0.026)
        self.assertAlmostEqual(self.value('jaw_gap_m', 'capper_b'), 0.030)
        self.assertEqual(self.value('cycle.status', 'capper_b'), 'idle')
        self.assertEqual(self.agent.read(instance_id='capper_b')['clock']['time_s'], 0.125)
        op_b = self.start('capper_b')
        self.host.host_step(0.125)
        self.assertEqual(self.value('cycle.status'), 'awaiting_attach')
        self.assertAlmostEqual(self.value('jaw_gap_m', 'capper_b'), 0.026)
        self.assertNotEqual(op_a, op_b)
        isolated = IntelliXcapS1Runtime()
        isolated.create_instance('capper_a')
        self.assertEqual(isolated.time_s, 0)
        self.assertIsNone(isolated.host_motion_snapshot('capper_a')['jaw_gap_m'])

    def test_rejections_leave_state_motion_and_operations_unchanged(self):
        self.configure()
        invalid = [('power.set', {'enabled': 1}), ('power.set', {'enabled': True, 'role': 'evaluator'}), ('cycle.set_mode', {'mode': 'wrong'}), ('consumable.select', {'consumable_id': 'unregistered'}), ('cycle.start', {'duration_s': 0.2}), ('no.such_capability', {})]
        for cid, args in invalid:
            before = self.host.debug_snapshot()
            response = self.invoke(cid, **args)
            self.assertEqual(response['status'], 'rejected')
            self.assertEqual(response['error']['side_effects'], 'none')
            self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.invoke('torque.set_limit')['status'], 'unsupported')
        op = self.start()
        for cid, args in [('power.set', {'enabled': False}), ('cycle.set_mode', {'mode': 'recap'}), ('consumable.select', {'consumable_id': 'synthetic_15ml_type'}), ('cycle.start', {})]:
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cid, **args)['error']['code'], 'BUSY')
            self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.poll(operation_id=op)['status'], 'running')

    def test_invalid_host_values_and_phase_confirmations_are_atomic(self):
        self.configure()
        before = self.host.debug_snapshot()
        for value in (True, float('nan'), float('inf'), -1, 10**400):
            with self.assertRaises(ValueError):
                self.host.host_step(value)
        for diameter in (True, 0.0109, 0.0401, float('nan'), float('inf'), 10**400):
            with self.assertRaises(ValueError):
                self.host.host_register_consumable('bad', cap_diameter_m=diameter, compatibility_evidence='test')
        with self.assertRaises(ValueError):
            self.host.host_configure_motion('capper_a', open_gap_m=0.03, closed_gap_m=0.022, rotation_delta_rad=1.2, initial_rotation_rad=0, clamp_duration_s=0, rotation_duration_s=0.5, release_duration_s=0.25, evidence='test')
        with self.assertRaises(ValueError):
            self.host.host_present_sample('capper_b', tube_id='capper_a_tube', cap_id='othercap', consumable_id='synthetic_15ml_type', evidence='test')
        self.assertEqual(before, self.host.debug_snapshot())
        op = self.start()
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError):
            self.host.host_confirm_thread_result('capper_a', operation_id=op, succeeded=True, evidence='premature')
        with self.assertRaises(ValueError):
            self.host.host_confirm_attachment('capper_a', operation_id=op, event_id='invented', constraint_id='constraint', evidence='premature')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_compatibility_boundary_and_matching_sample_required(self):
        for diameter in (0.011, 0.040):
            self.host.host_register_consumable('boundary_%s' % diameter, cap_diameter_m=diameter, compatibility_evidence='Synthetic boundary test')
        self.configure()
        self.invoke('consumable.select', consumable_id='boundary_0.011')
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('cycle.start')['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.invoke('consumable.select', consumable_id='synthetic_15ml_type')
        self.host.host_configure_motion('capper_a', open_gap_m=0.020, closed_gap_m=0.010, rotation_delta_rad=1.2, initial_rotation_rad=0, clamp_duration_s=0.25, rotation_duration_s=0.5, release_duration_s=0.25, evidence='Deliberately too narrow fixture')
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('cycle.start')['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_cancel_during_clamping_freezes_pose_and_requires_recovery(self):
        self.configure()
        op = self.start()
        self.host.host_step(0.125)
        original = self.agent.cancel(operation_id=op, request_id='cancel-clamp')
        self.assertEqual(original['status'], 'cancelled')
        self.assertAlmostEqual(original['result']['jaw_gap_m'], 0.026)
        self.assertIsNone(original['result']['attachment.constraint_id'])
        self.host.host_step(100)
        self.assertAlmostEqual(self.value('jaw_gap_m'), 0.026)
        self.assertEqual(self.agent.cancel(operation_id=op, request_id='cancel-clamp'), original)
        self.assertEqual(self.invoke('cycle.start')['error']['code'], 'PRECONDITION_FAILED')

    def test_cancel_after_attach_preserves_constraint_until_host_release(self):
        self.configure()
        op = self.start()
        self.host.host_step(0.25)
        self.attach(op)
        self.host.host_step(0.25)
        result = self.agent.cancel(operation_id=op, request_id='cancel-rotation')
        self.assertAlmostEqual(result['result']['rotation_rad'], 0.6)
        self.assertEqual(result['result']['attachment.constraint_id'], 'capper_a_external_constraint')
        self.host.host_step(5)
        self.assertAlmostEqual(self.value('rotation_rad'), 0.6)
        self.assertEqual(self.invoke('cycle.start')['error']['code'], 'BUSY')
        self.release(op)
        self.assertIsNone(self.value('attachment.constraint_id'))
        self.assertIsNone(self.agent.poll(operation_id=op)['result']['attachment.constraint_id'])
        self.assertEqual(self.agent.poll(operation_id=op)['status'], 'cancelled')
        self.assertAlmostEqual(self.value('jaw_gap_m'), 0.022)

    def test_cancel_pending_attach_rejects_late_external_acknowledgement(self):
        self.configure()
        op = self.start()
        self.host.host_step(0.25)
        event = self.pending('attach')
        self.agent.cancel(operation_id=op, request_id='cancel-before-attach')
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError):
            self.host.host_confirm_attachment('capper_a', operation_id=op, event_id=event['event_id'], constraint_id='late', evidence='late')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_access_filters_truth_scopes_operations_errors_and_describe(self):
        self.configure()
        op = self.start()
        self.finish(op)
        describe = self.agent.describe(instance_id='capper_a')
        self.assertNotIn('truth.thread_state', _ixs_test_json.dumps(describe))
        self.assertNotIn('host_api', describe)
        self.assertNotIn('truth.thread_state', self.agent.read(instance_id='capper_a')['observations'])
        self.assertEqual(self.agent.read(instance_id='capper_a', state_ids=['truth.thread_state'])['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(self.other.read(instance_id='capper_b')['error']['code'], 'UNKNOWN_INSTANCE')
        before = self.host.debug_snapshot()
        self.assertEqual(self.other.poll(operation_id=op)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(self.other.cancel(operation_id=op, request_id='forbidden')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(self.judge.poll(operation_id=op)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.value('truth.thread_state', session=self.judge), 'open')
        for bad_id in ({}, [], True, 0):
            self.assertEqual(self.agent.read(instance_id=bad_id)['status'], 'rejected')
            self.assertEqual(self.agent.poll(operation_id=bad_id)['status'], 'rejected')

    def test_whole_step_and_partitioned_step_are_consistent(self):
        self.configure('capper_a')
        self.configure('capper_b')
        self.start('capper_a')
        self.start('capper_b')
        for _ in range(5):
            self.host.host_step(0.025)
        self.assertAlmostEqual(self.value('jaw_gap_m'), 0.026)
        self.assertAlmostEqual(self.value('jaw_gap_m'), self.value('jaw_gap_m', 'capper_b'))
        self.assertEqual(self.value('rotation_rad'), self.value('rotation_rad', 'capper_b'))
# END CHEM-177 INTELLIXCAP S1 APPEND-ONLY TESTS

# BEGIN CHEM-176 AZENTA HEAT SEALER APPEND-ONLY TESTS
import copy as _ahs_test_copy
import json as _ahs_test_json
import sys as _ahs_test_sys
import unittest as _ahs_test_unittest
from pathlib import Path as _ahs_test_Path
_AHS_TEST_ROOT = _ahs_test_Path(__file__).resolve().parents[1]
_ahs_test_sys.path.insert(0, str(_AHS_TEST_ROOT / 'scripts'))
_ahs_test_sys.path.insert(0, str(_AHS_TEST_ROOT / 'skills' / 'chemistry-lab-assets' / 'scripts'))
from basic40_runtime_automation import AzentaHeatSealerRuntime as _AHSRuntime, export_azenta_heat_sealer_contract_metadata as _ahs_test_metadata
from asset_tool import validate_value as _ahs_test_validate, lint_schema as _ahs_test_lint


class TestAzentaHeatSealerRuntime(_ahs_test_unittest.TestCase):
    def setUp(self):
        self.host, self.calls, self.serial = _AHSRuntime(), [], 0
        def scene(i, targets):
            self.calls.append((i, targets))
            return True
        self.host.create_instance('ahs_a', scene_adapter=scene)
        self.host.create_instance('ahs_b', scene_adapter=scene)
        self.host.register_principal('agent', visibility='agent')
        self.host.register_principal('judge', visibility='evaluator')
        self.host.register_principal('limited', visibility='agent', instance_ids=['ahs_a'])
        self.agent, self.judge, self.other = [self.host.session(i) for i in ('agent', 'judge', 'limited')]

    def invoke(self, cid, iid='ahs_a', **args):
        self.serial += 1
        return self.agent.invoke(instance_id=iid, capability_id=cid, arguments=args, request_id='ahs_req_%d' % self.serial)

    def ok(self, cid, iid='ahs_a', **args):
        r = self.invoke(cid, iid, **args)
        self.assertIn(r['status'], ('succeeded', 'accepted'), r)
        self.assertEqual(_ahs_test_validate(r['result'], _ahs_test_metadata()['capabilities'][cid]['output_schema']), [], cid)
        return r

    def value(self, sid, iid='ahs_a', debug=False):
        return (self.judge if debug else self.agent).read(instance_id=iid, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        return self.host.host_step(dt, world_time_s=self.host.time_s + dt)

    def load(self, iid='ahs_a', height=.02, count=5, duration=1.0, temperature=423.15):
        self.ok('power.set', iid, enabled=True)
        self.host.host_load_plate(iid, plate_id=iid + '_plate', height_m=height, plate_adapter_id='adapter', compatibility_evidence='SYNTHETIC_PRIVATE_PLATE')
        self.host.host_load_film(iid, roll_id=iid + '_roll', width_m=.08, remaining_seals=count, film_roll_position_id='roll_position', compatibility_evidence='SYNTHETIC_PRIVATE_FILM')
        self.ok('configuration.set', iid, press_time_s=duration, plate_adapter_id='adapter', film_roll_position_id='roll_position')
        self.ok('thermal.set_temperature', iid, temperature_K=temperature)
        self.host.host_set_temperature(iid, temperature_K=temperature, evidence='SYNTHETIC_PRIVATE_TEMP')
        self.ok('scene.set_press_displacement', iid, displacement_m=.077-height)

    def test_metadata_examples_and_initial_unknown_values(self):
        m = _ahs_test_metadata()
        self.assertEqual(m['catalog_id'], 'CHEM-176')
        self.assertEqual(m['parameters']['body_mass_without_roll_kg']['value'], 27)
        self.assertIsNone(m['parameters']['thermal_rate_K_s']['value'])
        self.assertIsNone(m['parameters']['press_pressure_Pa']['value'])
        for cid, cap in m['capabilities'].items():
            self.assertEqual(_ahs_test_lint(cap['input_schema']), [], cid)
            self.assertEqual(_ahs_test_lint(cap['output_schema']), [], cid)
            for example in cap['example_arguments']:
                self.assertEqual(_ahs_test_validate(example, cap['input_schema']), [], cid)
            self.assertEqual(cap['implementation']['robot'], 'absent')
            self.assertEqual(cap['implementation']['hardware'], 'absent')
        for sid, obs in self.judge.read(instance_id='ahs_a')['observations'].items():
            self.assertEqual(_ahs_test_validate(obs['value'], m['state_slots'][sid]['value_schema']), [], sid)
        for sid in ('truth.temperature_K', 'truth.thermal_rate_K_s', 'film.remaining_seals', 'configuration.press_time_s'):
            self.assertIsNone(self.value(sid, debug=True))
        self.assertEqual(self.invoke('sealing.set_pressure')['status'], 'unsupported')

    def test_temperature_setpoint_only_host_rate_and_read_purity(self):
        self.ok('power.set', enabled=True)
        self.ok('thermal.set_temperature', temperature_K=373.15)
        self.step(2)
        self.assertIsNone(self.value('measurement.temperature_K'))
        self.host.host_set_temperature('ahs_a', temperature_K=293.15, evidence='synthetic explicit initial temperature')
        self.step(2)
        self.assertEqual(self.value('measurement.temperature_K'), 293.15)
        self.host.host_configure_thermal_rate('ahs_a', thermal_rate_K_s=10, evidence='synthetic linear 10 K/s assumption, not vendor value')
        self.step(3)
        self.assertEqual(self.value('measurement.temperature_K'), 323.15)
        before = self.host.debug_snapshot()
        self.agent.read(instance_id='ahs_a')
        self.ok('sealing.read')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.step(5)
        self.assertEqual(self.value('measurement.temperature_K'), 373.15)
        self.assertTrue(self.value('thermal.target_reached'))
        self.ok('thermal.set_temperature', temperature_K=468.15)
        self.assertEqual(self.value('measurement.temperature_K'), 373.15)
        self.assertFalse(self.value('thermal.target_reached'))
        self.step(9.5)
        self.assertEqual(self.value('measurement.temperature_K'), 468.15)
        obs = self.agent.read(instance_id='ahs_a', state_ids=['measurement.temperature_K'])['observations']['measurement.temperature_K']
        self.assertEqual(obs['quality'], 'estimated')

    def test_temperature_uniformity_not_used_as_readiness_tolerance(self):
        self.load()
        self.host.host_set_temperature('ahs_a', temperature_K=422.65, evidence='synthetic not at target, though within 1 K')
        self.assertFalse(self.value('thermal.target_reached'))
        self.assertEqual(self.invoke('sealing.start')['error']['code'], 'PRECONDITION_FAILED')

    def test_accepted_completion_consumption_and_historical_operation(self):
        self.load(duration=.1, count=5000, height=.060)
        self.assertAlmostEqual(self.value('scene.press_displacement_m'), .017)
        r = self.ok('sealing.start')
        self.assertEqual(r['status'], 'accepted')
        self.assertEqual(self.value('film.remaining_seals'), 4999)
        self.assertFalse(self.value('plate.simulated_sealed'))
        op = r['operation_id']
        self.step(.04)
        self.assertAlmostEqual(self.value('sealing.progress'), .4)
        self.step(.06)
        self.assertEqual(self.agent.poll(instance_id='ahs_a', operation_id=op)['status'], 'succeeded')
        self.assertTrue(self.value('plate.simulated_sealed'))
        self.assertEqual(self.value('sealing.elapsed_s'), .1)
        self.assertEqual(self.invoke('sealing.start')['error']['code'], 'PRECONDITION_FAILED')
        self.ok('scene.set_press_displacement', displacement_m=0)
        self.host.host_unload_plate('ahs_a')
        self.assertTrue(self.agent.poll(instance_id='ahs_a', operation_id=op)['result']['simulated_sealed'])
        self.assertFalse(self.value('plate.simulated_sealed'))

    def test_cancellation_and_power_loss_preserve_partial_effects(self):
        self.load(duration=10, count=2)
        op = self.ok('sealing.start')['operation_id']
        self.step(2)
        q = self.value('scene.press_displacement_m')
        cancelled = self.agent.cancel(instance_id='ahs_a', operation_id=op, request_id='cancel')
        self.assertEqual(cancelled['result']['status'], 'cancelled')
        self.step(20)
        self.assertEqual(self.value('sealing.elapsed_s'), 2)
        self.assertEqual(self.value('sealing.progress'), .2)
        self.assertEqual(self.value('film.remaining_seals'), 1)
        self.assertEqual(self.value('scene.press_displacement_m'), q)
        self.assertFalse(self.value('plate.simulated_sealed'))
        self.ok('sealing.start')
        self.step(1)
        self.ok('power.set', enabled=False)
        self.assertEqual(self.value('sealing.status'), 'cancelled')
        self.assertEqual(self.value('sealing.elapsed_s'), 1)
        self.assertEqual(self.value('film.remaining_seals'), 0)
        self.assertIsNone(self.value('measurement.temperature_K'))
        self.assertFalse(self.value('thermal.target_reached'))

    def test_temperature_loss_fails_preserving_partial_effects(self):
        self.load()
        op = self.ok('sealing.start')['operation_id']
        self.step(.2)
        self.host.host_set_temperature('ahs_a', temperature_K=400, evidence='synthetic temperature deviation')
        self.assertEqual(self.agent.poll(instance_id='ahs_a', operation_id=op)['status'], 'failed')
        self.step(2)
        self.assertEqual(self.value('sealing.elapsed_s'), .2)
        self.assertEqual(self.value('film.remaining_seals'), 4)
        self.assertFalse(self.value('plate.simulated_sealed'))

    def test_bounds_extra_fields_boolean_nonfinite_atomic_rejection(self):
        self.load()
        bad = [('power.set', {'enabled': 1}), ('thermal.set_temperature', {'temperature_K': 373.149}),
            ('thermal.set_temperature', {'temperature_K': 468.151}), ('thermal.set_temperature', {'temperature_K': None}),
            ('scene.set_press_displacement', {'displacement_m': -.001}), ('scene.set_press_displacement', {'displacement_m': .074001}),
            ('scene.set_press_displacement', {'displacement_m': True}), ('scene.set_press_displacement', {'displacement_m': float('nan')}),
            ('scene.set_press_displacement', {'displacement_m': float('inf')}), ('scene.set_press_displacement', {'displacement_m': 10**400}),
            ('configuration.set', {'press_time_s': .099, 'plate_adapter_id': 'adapter', 'film_roll_position_id': 'roll_position'}),
            ('configuration.set', {'press_time_s': 10.001, 'plate_adapter_id': 'adapter', 'film_roll_position_id': 'roll_position'}),
            ('configuration.set', {'press_time_s': True, 'plate_adapter_id': 'adapter', 'film_roll_position_id': 'roll_position'}),
            ('sealing.start', {'visibility': 'evaluator'}), ('sealing.set_pressure', {'pressure_Pa': 1000})]
        for cid, args in bad:
            with self.subTest(cid=cid, args=args):
                before = self.host.debug_snapshot()
                r = self.invoke(cid, **args)
                self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
                self.assertEqual(r['error']['side_effects'], 'none')
                self.assertEqual(self.host.debug_snapshot(), before)

    def test_host_input_bounds_and_clock_atomic_rejection(self):
        for height in (.00299, .06001, True, float('nan')):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.host_load_plate('ahs_a', plate_id='p', height_m=height, plate_adapter_id='a', compatibility_evidence='fixture')
            self.assertEqual(self.host.debug_snapshot(), before)
        for width, count in [(.07499, 10), (.08501, 10), (.08, 5001), (.08, -1), (.08, True), (True, 1)]:
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.host_load_film('ahs_a', roll_id='r', width_m=width, remaining_seals=count, film_roll_position_id='position', compatibility_evidence='fixture')
            self.assertEqual(self.host.debug_snapshot(), before)
        for dt, world in [(0,0), (-1,-1), (1,2), (True,1), (float('nan'),1), (float('inf'),float('inf')), (10**400,10**400)]:
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.host_step(dt, world_time_s=world)
            self.assertEqual(self.host.debug_snapshot(), before)
        for rate, evidence in [(None,'fixture'), (0,'fixture'), (-1,'fixture'), (True,'fixture'), (float('inf'),'fixture'), (1,'')]:
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.host_configure_thermal_rate('ahs_a', thermal_rate_K_s=rate, evidence=evidence)
            self.assertEqual(self.host.debug_snapshot(), before)
        for temp in (0, -1, True, float('inf')):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.host_set_temperature('ahs_a', temperature_K=temp, evidence='fixture')
            self.assertEqual(self.host.debug_snapshot(), before)

    def test_geometry_contact_bounds_and_prerequisites(self):
        self.ok('scene.set_press_displacement', displacement_m=.074)
        self.ok('scene.set_press_displacement', displacement_m=0)
        self.load(height=.003)
        self.assertEqual(self.value('scene.press_displacement_m'), .074)
        self.ok('scene.set_press_displacement', displacement_m=.05)
        self.assertEqual(self.invoke('sealing.start')['error']['code'], 'PRECONDITION_FAILED')
        self.load('ahs_b', height=.060)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('scene.set_press_displacement', 'ahs_b', displacement_m=.018)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertEqual(self.invoke('configuration.set', 'ahs_b', press_time_s=1, plate_adapter_id='wrong', film_roll_position_id='roll_position')['error']['code'], 'PRECONDITION_FAILED')
        self.ok('sealing.start', 'ahs_b')
        for cid, args in [('thermal.set_temperature', {'temperature_K':373.15}), ('scene.set_press_displacement', {'displacement_m':0}), ('configuration.set', {'press_time_s':1,'plate_adapter_id':'adapter','film_roll_position_id':'roll_position'}), ('sealing.start', {})]:
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cid, 'ahs_b', **args)['error']['code'], 'BUSY')
            self.assertEqual(self.host.debug_snapshot(), before)
        with self.assertRaises(ValueError): self.host.host_unload_plate('ahs_b')

    def test_scene_missing_failure_and_retry_confirmation(self):
        self.host.create_instance('missing')
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('scene.set_press_displacement', 'missing', displacement_m=0)['status'], 'unsupported')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertEqual(self.agent.describe(instance_id='missing')['capabilities']['scene.set_press_displacement']['availability'], 'unsupported')
        attempted = []
        def broken(i, target):
            attempted.append(target)
            raise RuntimeError('SYNTHETIC_PRIVATE_ERROR')
        self.host.create_instance('broken', scene_adapter=broken)
        req = dict(instance_id='broken', capability_id='scene.set_press_displacement', arguments={'displacement_m':.01}, request_id='retry')
        before = self.host.debug_snapshot()
        r = self.agent.invoke(**req)
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['error']['side_effects'], 'unknown')
        self.assertEqual(self.agent.invoke(**req), r)
        self.assertEqual(len(attempted), 1)
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertNotIn('SYNTHETIC_PRIVATE', _ahs_test_json.dumps(r))
        self.host.create_instance('unconfirmed', scene_adapter=lambda i,t: None)
        self.assertEqual(self.invoke('scene.set_press_displacement', 'unconfirmed', displacement_m=0)['status'], 'failed')

    def test_identity_filter_operation_owner_and_write_routes(self):
        self.load()
        op = self.ok('sealing.start')['operation_id']
        for answer in (self.agent.describe(instance_id='ahs_a'), self.agent.read(instance_id='ahs_a'), self.ok('sealing.read')):
            text = _ahs_test_json.dumps(answer)
            self.assertNotIn('SYNTHETIC_PRIVATE', text)
            self.assertNotIn('truth.', text)
        self.assertEqual(self.agent.read(instance_id='ahs_a', state_ids=['truth.temperature_K'])['error']['code'], 'ACCESS_DENIED')
        self.assertIn('truth.temperature_K', self.judge.read(instance_id='ahs_a')['observations'])
        self.assertEqual(self.other.read(instance_id='ahs_b')['error']['code'], 'UNKNOWN_INSTANCE')
        self.assertEqual(self.other.poll(instance_id='ahs_a', operation_id=op)['error']['code'], 'UNKNOWN_OPERATION')
        self.assertEqual(self.other.cancel(instance_id='ahs_a', operation_id=op, request_id='other')['error']['code'], 'UNKNOWN_OPERATION')
        self.assertEqual(self.agent.poll(instance_id='ahs_b', operation_id=op)['error']['code'], 'UNKNOWN_OPERATION')
        before = self.host.debug_snapshot()
        self.assertEqual(self.judge.write(instance_id='ahs_a', state_id='truth.temperature_K', value=1, request_id='write_truth')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(self.host.debug_snapshot(), before)
        r = self.agent.write(instance_id='ahs_b', state_id='power.enabled', value=True, request_id='write_power')
        self.assertEqual(r['status'], 'succeeded')
        self.assertTrue(self.value('power.enabled', 'ahs_b'))

    def test_idempotency_conflict_and_copy_isolation(self):
        self.load()
        req = dict(instance_id='ahs_a', capability_id='sealing.start', arguments={}, request_id='same')
        first = self.agent.invoke(**req)
        self.step(.2)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**req), first)
        self.assertEqual(self.value('film.remaining_seals'), 4)
        conflict = self.agent.invoke(**{**req, 'capability_id':'power.set', 'arguments':{'enabled':False}})
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(self.host.debug_snapshot(), before)
        first['result']['progress'] = 123
        self.assertEqual(self.agent.invoke(**req)['result']['progress'], 0)
        snapshot = self.agent.read(instance_id='ahs_a')
        snapshot['observations']['film.remaining_seals']['value'] = 999
        self.assertEqual(self.value('film.remaining_seals'), 4)

    def test_two_instances_share_clock_not_temperature_requests_or_film(self):
        self.load('ahs_a', count=1, duration=1, temperature=373.15)
        self.load('ahs_b', count=10, duration=2, temperature=468.15)
        req = dict(capability_id='sealing.start', arguments={}, request_id='same')
        a = self.agent.invoke(instance_id='ahs_a', **req)
        self.step(.5)
        b = self.agent.invoke(instance_id='ahs_b', **req)
        self.assertNotEqual(a['operation_id'], b['operation_id'])
        self.step(.5)
        self.assertTrue(self.value('plate.simulated_sealed', 'ahs_a'))
        self.assertFalse(self.value('plate.simulated_sealed', 'ahs_b'))
        self.assertEqual(self.value('sealing.progress', 'ahs_b'), .25)
        self.assertEqual(self.value('film.remaining_seals', 'ahs_a'), 0)
        self.assertEqual(self.value('film.remaining_seals', 'ahs_b'), 9)
        self.assertEqual(self.value('measurement.temperature_K', 'ahs_a'), 373.15)
        self.assertEqual(self.value('measurement.temperature_K', 'ahs_b'), 468.15)
        self.assertEqual(self.agent.read(instance_id='ahs_a')['clock'], self.agent.read(instance_id='ahs_b')['clock'])
        isolated = _AHSRuntime()
        isolated.create_instance('ahs_a')
        self.assertEqual(isolated.time_s, 0)

    def test_host_attachment_confirmation_release_and_no_fake_grasp(self):
        self.load()
        self.ok('scene.set_press_displacement', displacement_m=0)
        events = []
        def confirm(i, e):
            events.append((i,e))
            return True
        before = self.host.debug_snapshot()
        with self.assertRaises(RuntimeError): self.host.host_attach('ahs_a', constraint_id='c', attachment_adapter=lambda i,e:False)
        self.assertEqual(self.host.debug_snapshot(), before)
        event = self.host.host_attach('ahs_a', constraint_id='c', attachment_adapter=confirm)
        self.assertEqual(event['event'], 'attach')
        self.assertEqual(self.value('plate.attachment_constraint_id'), 'c')
        with self.assertRaises(ValueError): self.host.host_unload_plate('ahs_a')
        before = self.host.debug_snapshot()
        with self.assertRaises(RuntimeError): self.host.host_release('ahs_a', attachment_adapter=lambda i,e:None)
        self.assertEqual(self.host.debug_snapshot(), before)
        self.host.host_release('ahs_a', attachment_adapter=confirm)
        self.assertIsNone(self.value('plate.attachment_constraint_id'))
        self.assertTrue(self.value('plate.loaded'))
        self.host.host_unload_plate('ahs_a')
        self.assertFalse(self.value('plate.loaded'))
        self.assertEqual([e[1]['event'] for e in events], ['attach','release'])

    def test_declared_effects_cover_changes_and_state_output_schemas(self):
        self.load()
        seq = [('thermal.set_temperature', {'temperature_K':373.15}), ('thermal.set_temperature', {'temperature_K':423.15}),
            ('configuration.set', {'press_time_s':2,'plate_adapter_id':'adapter','film_roll_position_id':'roll_position'}),
            ('scene.set_press_displacement', {'displacement_m':.057}), ('sealing.start', {}), ('power.set', {'enabled':False})]
        for cid, args in seq:
            before = self.host.debug_snapshot()['instances']['ahs_a']['state']
            self.ok(cid, **args)
            after = self.host.debug_snapshot()['instances']['ahs_a']['state']
            changed = {k for k in before if before[k] != after[k]}
            declared = {e['state_id'] for e in _ahs_test_metadata()['capabilities'][cid]['state_effects']}
            self.assertLessEqual(changed, declared, (cid, changed - declared))
        for sid, obs in self.judge.read(instance_id='ahs_a')['observations'].items():
            self.assertEqual(_ahs_test_validate(obs['value'], _ahs_test_metadata()['state_slots'][sid]['value_schema']), [], sid)
# END CHEM-176 AZENTA HEAT SEALER APPEND-ONLY TESTS

# CHEM-176 rejected request replay must remain idempotent after preconditions change.
def _ahs_test_rejected_request_replay(self):
    request = dict(instance_id='ahs_a', capability_id='thermal.set_temperature', arguments={'temperature_K':423.15}, request_id='rejected_replay')
    initial = self.agent.invoke(**request)
    self.assertEqual(initial['error']['code'], 'PRECONDITION_FAILED')
    self.ok('power.set', enabled=True)
    before = self.host.debug_snapshot()
    self.assertEqual(self.agent.invoke(**request), initial)
    self.assertEqual(self.host.debug_snapshot(), before)
    changed = self.agent.invoke(**{**request, 'arguments': {'temperature_K':373.15}})
    self.assertEqual(changed['error']['code'], 'REQUEST_CONFLICT')
TestAzentaHeatSealerRuntime.test_rejected_request_replay_after_precondition_change = _ahs_test_rejected_request_replay

# BEGIN CHEM-174 QUANTOS Q2 XPE206DR APPEND-ONLY TESTS
import json as _qt_test_json
import unittest as _qt_test_unittest
import sys as _qt_test_sys
from pathlib import Path as _qt_test_Path
_QT_TEST_ROOT = _qt_test_Path(__file__).resolve().parents[1] if _qt_test_Path(__file__).name == 'test_basic40_automation.py' else _qt_test_Path(__file__).resolve().parents[4]
_qt_test_sys.path.insert(0, str(_QT_TEST_ROOT / 'scripts'))
_qt_test_sys.path.insert(0, str(_QT_TEST_ROOT / 'skills' / 'chemistry-lab-assets' / 'scripts'))
from basic40_runtime_automation import QuantosQ2Runtime as _QtRuntime, QUANTOS_Q2_TYPE as _QtType, export_quantos_q2_contract_metadata as _qt_test_metadata, apply_quantos_q2_scene_patch as _qt_test_scene
from asset_tool import validate_value as _qt_test_validate, lint_schema as _qt_test_lint


class TestQuantosQ2Runtime(_qt_test_unittest.TestCase):
    def setUp(self):
        self.host = _QtRuntime()
        self.scene = []
        def confirm(iid,patch):
            self.scene.append((iid,patch))
            return True
        self.host.create_instance('qt_a',scene_adapter=confirm)
        self.host.create_instance('qt_b',scene_adapter=confirm)
        self.host.register_principal('agent',visibility='agent')
        self.host.register_principal('judge',visibility='evaluator')
        self.host.register_principal('other',visibility='agent',instance_ids=['qt_a'])
        self.agent,self.judge,self.other = self.host.session('agent'),self.host.session('judge'),self.host.session('other')
        self.count = 0

    def invoke(self,cid,iid='qt_a',**args):
        self.count += 1
        return self.agent.invoke(instance_id=iid,capability_id=cid,arguments=args,request_id='qt_req_%d'%self.count)

    def ok(self,cid,iid='qt_a',**args):
        r = self.invoke(cid,iid,**args)
        self.assertIn(r['status'],('succeeded','accepted'),r)
        self.assertEqual(_qt_test_validate(r['result'],_qt_test_metadata()['capabilities'][cid]['output_schema']),[],cid)
        return r

    def val(self,sid,iid='qt_a',debug=False):
        return (self.judge if debug else self.agent).read(instance_id=iid,state_ids=[sid])['observations'][sid]['value']

    def step(self,dt):
        self.host.host_step(dt,world_time_s=self.host.time_s+dt)

    def prepare(self,iid='qt_a',source=.004,contents=0,target=.001,rate=.0001):
        self.host.host_configure_geometry(iid,door_travel_m=.11,evidence='Synthetic geometric travel; not vendor travel')
        self.ok('door.set_opening',iid,opening_m=.11)
        self.host.host_load_head(iid,head_id=iid+'_head',source_mass_kg=source,free_flowing=True,evidence='SYNTHETIC_PRIVATE_SOURCE fixture')
        self.host.host_load_container(iid,container_id=iid+'_vessel',position_id='balance_pan',content_mass_kg=contents,evidence='SYNTHETIC_PRIVATE_CONTENT fixture')
        self.host.host_configure_dosing(iid,rate_kg_s=rate,rate_evidence='Explicit synthetic constant rate, not from calcium carbonate table',observation_evidence='Explicit ideal mass observation, not vendor balance performance')
        self.ok('power.set',iid,enabled=True)
        self.ok('head.select',iid,head_id=iid+'_head')
        self.ok('container.select_position',iid,position_id='balance_pan')
        self.ok('dosing.set_target',iid,target_mass_kg=target)
        self.ok('dosing.set_tolerance',iid,tolerance_kg=.000001)
        self.ok('door.set_opening',iid,opening_m=0)
        self.ok('balance.tare',iid)

    def test_catalog_and_unknown_defaults(self):
        metadata = _qt_test_metadata()
        self.assertEqual(metadata['asset_type_id'],_QtType)
        self.assertEqual(metadata['parameters']['target_range_kg']['value'],[1e-6,.005])
        self.assertEqual(metadata['parameters']['caco3_10mg_typical_time_s']['value'],22)
        self.assertEqual(metadata['parameters']['caco3_10mg_rsd']['value'],.0115)
        self.assertEqual(metadata['parameters']['caco3_50mg_rsd']['value'],.0026)
        self.assertEqual(metadata['parameters']['caco3_1000mg_typical_time_s']['value'],71)
        for param in metadata['parameters'].values():
            if param['evidence']=='unknown':
                self.assertIsNone(param['value'])
        for sid in ('dosing.target_mass_kg','dosing.tolerance_kg','measurement.balance_net_kg','measurement.actual_dosed_kg','scene.door_opening_m'):
            obs = self.agent.read(instance_id='qt_a',state_ids=[sid])['observations'][sid]
            self.assertIsNone(obs['value'])
            self.assertEqual(obs['quality'],'unavailable')
            self.assertIn('reason',obs)
        self.assertEqual(self.invoke('autosampler.advance')['status'],'unsupported')

    def test_metadata_schemas_and_examples(self):
        metadata = _qt_test_metadata()
        for sid,slot in metadata['state_slots'].items():
            self.assertEqual(_qt_test_lint(slot['value_schema']),[],sid)
        for cid,cap in metadata['capabilities'].items():
            self.assertEqual(_qt_test_lint(cap['input_schema']),[],cid)
            self.assertEqual(_qt_test_lint(cap['output_schema']),[],cid)
            for example in cap['example_arguments']:
                self.assertEqual(_qt_test_validate(example,cap['input_schema']),[],cid)
            self.assertEqual(cap['implementation']['robot'],'absent')
            self.assertEqual(cap['implementation']['hardware'],'absent')

    def test_no_reads_queries_or_describe_advance_time_or_state(self):
        self.prepare()
        op = self.ok('dosing.start')['operation_id']
        before = self.host.debug_snapshot()
        for n in range(3):
            self.agent.describe(instance_id='qt_a')
            self.agent.describe(asset_type_id=_QtType)
            self.agent.read(instance_id='qt_a')
            self.ok('dosing.read')
            self.agent.poll(instance_id='qt_a',operation_id=op)
        self.assertEqual(self.host.debug_snapshot(),before)

    def test_conserved_target_stop_and_exact_elapsed(self):
        self.prepare(contents=.0002)
        first = self.ok('dosing.start')
        self.assertEqual(first['status'],'accepted')
        self.assertEqual(self.val('measurement.actual_dosed_kg'),0)
        self.step(2)
        self.assertAlmostEqual(self.val('measurement.actual_dosed_kg'),.0002)
        self.assertEqual(self.val('dosing.elapsed_s'),2)
        self.step(50)
        self.assertEqual(self.val('dosing.status'),'succeeded')
        self.assertAlmostEqual(self.val('measurement.actual_dosed_kg'),.001)
        self.assertAlmostEqual(self.val('measurement.error_kg'),0)
        self.assertFalse(self.val('measurement.out_of_tolerance'))
        self.assertAlmostEqual(self.val('dosing.elapsed_s'),10)
        self.assertAlmostEqual(self.val('measurement.balance_net_kg'),.001)
        total = self.val('truth.source_mass_kg',debug=True)+self.val('truth.container_mass_kg',debug=True)
        self.assertAlmostEqual(total,.0042)
        ledger = self.host.host_transfer_ledger()
        self.assertAlmostEqual(sum(t['mass_kg'] for t in ledger),.001)
        self.assertTrue(all(t['donor_id']=='qt_a_head' and t['receiver_id']=='qt_a_vessel' and t['unit']=='kg' for t in ledger))
        self.assertEqual(len({t['transaction_id'] for t in ledger}),len(ledger))
        self.assertAlmostEqual(ledger[-1]['time_s'],10)
        before = self.host.debug_snapshot()['instances']
        self.step(10)
        self.assertEqual(self.host.debug_snapshot()['instances'],before)

    def test_cancel_and_power_loss_keep_partial_mass(self):
        self.prepare()
        op = self.ok('dosing.start')['operation_id']
        self.step(2)
        r = self.agent.cancel(instance_id='qt_a',operation_id=op,request_id='cancel')
        self.assertEqual(r['status'],'cancelled')
        self.assertAlmostEqual(r['result']['measurement.actual_dosed_kg'],.0002)
        self.assertEqual(r['result']['dosing.elapsed_s'],2)
        self.step(30)
        self.assertAlmostEqual(self.val('measurement.actual_dosed_kg'),.0002)
        self.assertEqual(self.val('dosing.elapsed_s'),2)
        self.assertEqual(self.invoke('dosing.start')['error']['code'],'PRECONDITION_FAILED')
        self.ok('balance.tare')
        op2 = self.ok('dosing.start')['operation_id']
        self.step(1)
        self.ok('power.set',enabled=False)
        self.assertEqual(self.val('dosing.status'),'cancelled')
        self.assertIsNone(self.val('measurement.actual_dosed_kg'))
        self.assertAlmostEqual(self.val('truth.container_mass_kg',debug=True),.0003)
        self.assertEqual(self.agent.poll(instance_id='qt_a',operation_id=op2)['status'],'cancelled')

    def test_source_depletion_fails_with_measurable_partial_effect(self):
        self.prepare(source=.00025)
        op = self.ok('dosing.start')['operation_id']
        self.step(20)
        self.assertEqual(self.val('dosing.status'),'failed')
        self.assertAlmostEqual(self.val('measurement.actual_dosed_kg'),.00025)
        self.assertAlmostEqual(self.val('measurement.error_kg'),-.00075)
        self.assertTrue(self.val('measurement.out_of_tolerance'))
        self.assertAlmostEqual(self.val('dosing.elapsed_s'),2.5)
        self.assertEqual(self.val('truth.source_mass_kg',debug=True),0)
        self.assertEqual(self.agent.poll(instance_id='qt_a',operation_id=op)['status'],'failed')
        self.assertEqual(self.agent.poll(instance_id='qt_a',operation_id=op)['error']['code'],'SOURCE_DEPLETED')
        self.assertEqual(self.agent.poll(instance_id='qt_a',operation_id=op)['error']['side_effects'],'partial')
        self.assertAlmostEqual(sum(t['mass_kg'] for t in self.host.host_transfer_ledger()),.00025)

    def test_target_range_and_invalid_request_atomic_rejection(self):
        self.prepare()
        for target in (1e-6,.005):
            self.ok('dosing.set_target',target_mass_kg=target)
        invalid = [('dosing.set_target',{'target_mass_kg':.000000999}),('dosing.set_target',{'target_mass_kg':.005001}),('dosing.set_target',{'target_mass_kg':True}),('dosing.set_target',{'target_mass_kg':float('nan')}),('dosing.set_target',{'target_mass_kg':float('inf')}),('dosing.set_target',{'target_mass_kg':10**400}),('dosing.set_target',{'target_mass_kg':None}),('dosing.set_tolerance',{'tolerance_kg':-1}),('power.set',{'enabled':1}),('head.select',{'head_id':''}),('head.select',{'head_id':False}),('door.set_opening',{'opening_m':-.01}),('door.set_opening',{'opening_m':.11001}),('dosing.start',{'visibility':'evaluator'}),('balance.tare',{'extra':0}),('dosing.poll',{'operation_id':[]})]
        for cid,args in invalid:
            with self.subTest(cid=cid,args=args):
                before = self.host.debug_snapshot()
                response = self.invoke(cid,**args)
                self.assertEqual(response['error']['code'],'INVALID_ARGUMENT',response)
                self.assertEqual(response['error']['side_effects'],'none')
                self.assertEqual(self.host.debug_snapshot(),before)

    def test_host_validation_and_atomic_common_clock(self):
        for rate in (0,-1,True,float('nan'),float('inf'),None,10**400):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.host_configure_dosing('qt_a',rate_kg_s=rate,rate_evidence='fixture',observation_evidence='fixture')
            self.assertEqual(before,self.host.debug_snapshot())
        for dt,world in [(0,0),(-1,-1),(True,1),(1,2),(float('nan'),1),(float('inf'),float('inf')),(10**400,10**400)]:
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.host_step(dt,world_time_s=world)
            self.assertEqual(before,self.host.debug_snapshot())
        with self.assertRaises(ValueError): self.host.host_load_head('qt_a',head_id='h',source_mass_kg=.1,free_flowing=True,evidence='fixture')
        self.host.host_configure_geometry('qt_a',door_travel_m=.11,evidence='fixture')
        self.ok('door.set_opening',opening_m=.11)
        for mass,flow in [(-1,True),(.1,False),(.1,1),(True,True),(float('nan'),True)]:
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.host_load_head('qt_a',head_id='h',source_mass_kg=mass,free_flowing=flow,evidence='fixture')
            self.assertEqual(before,self.host.debug_snapshot())

    def test_missing_model_callback_failure_idempotency_and_private_error(self):
        self.host.create_instance('missing')
        self.assertEqual(self.invoke('door.set_opening','missing',opening_m=0)['status'],'unsupported')
        self.assertEqual(self.agent.describe(instance_id='missing')['capabilities']['door.set_opening']['availability'],'unsupported')
        self.prepare()
        calls = []
        def fail(iid,patch):
            calls.append(patch)
            raise RuntimeError('SYNTHETIC_PRIVATE_HOST_ERROR')
        self.host._instances['qt_a']['adapter'] = fail
        before = self.host.debug_snapshot()
        request = dict(instance_id='qt_a',capability_id='door.set_opening',arguments={'opening_m':.05},request_id='fail_once')
        response = self.agent.invoke(**request)
        self.assertEqual(response['status'],'failed')
        self.assertEqual(response['error']['side_effects'],'unknown')
        self.assertNotIn('SYNTHETIC_PRIVATE',_qt_test_json.dumps(response))
        self.assertEqual(self.agent.invoke(**request),response)
        self.assertEqual(len(calls),1)
        self.assertEqual(before,self.host.debug_snapshot())
        self.host._instances['qt_a']['adapter'] = lambda iid,p: None
        self.assertEqual(self.invoke('door.set_opening',opening_m=.05)['status'],'failed')

    def test_prerequisites_resource_locks_and_door_tare_invalidation(self):
        self.prepare()
        self.ok('door.set_opening',opening_m=.01)
        self.assertFalse(self.val('balance.tared'))
        self.assertEqual(self.invoke('balance.tare')['error']['code'],'PRECONDITION_FAILED')
        self.assertEqual(self.invoke('dosing.start')['error']['code'],'PRECONDITION_FAILED')
        self.ok('door.set_opening',opening_m=0)
        self.assertEqual(self.invoke('dosing.start')['error']['code'],'PRECONDITION_FAILED')
        self.ok('balance.tare')
        self.ok('dosing.start')
        for cid,args in [('door.set_opening',{'opening_m':.01}),('head.select',{'head_id':'qt_a_head'}),('container.select_position',{'position_id':'balance_pan'}),('balance.tare',{}),('dosing.start',{}),('power.set',{'enabled':True}),('dosing.set_target',{'target_mass_kg':.001})]:
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cid,**args)['error']['code'],'BUSY')
            self.assertEqual(before,self.host.debug_snapshot())
        with self.assertRaises(ValueError): self.host.host_unload_head('qt_a')
        with self.assertRaises(ValueError): self.host.host_unload_container('qt_a')
        with self.assertRaises(ValueError): self.host.host_configure_dosing('qt_a',rate_kg_s=1,rate_evidence='fixture',observation_evidence='fixture')

    def test_identity_state_filter_and_operation_ownership(self):
        self.prepare()
        op = self.ok('dosing.start')['operation_id']
        for response in (self.agent.describe(instance_id='qt_a'),self.agent.read(instance_id='qt_a'),self.ok('dosing.read'),self.agent.poll(instance_id='qt_a',operation_id=op)):
            payload = _qt_test_json.dumps(response)
            self.assertNotIn('SYNTHETIC_PRIVATE',payload)
            self.assertNotIn('truth.',payload)
        self.assertEqual(self.agent.read(instance_id='qt_a',state_ids=['truth.source_mass_kg'])['error']['code'],'ACCESS_DENIED')
        self.assertEqual(self.other.read(instance_id='qt_b')['error']['code'],'UNKNOWN_INSTANCE')
        self.assertEqual(self.other.poll(instance_id='qt_a',operation_id=op)['error']['code'],'UNKNOWN_OPERATION')
        self.assertEqual(self.other.cancel(instance_id='qt_a',operation_id=op,request_id='steal')['error']['code'],'UNKNOWN_OPERATION')
        self.assertEqual(self.agent.poll(instance_id='qt_b',operation_id=op)['error']['code'],'UNKNOWN_OPERATION')
        self.assertIn('truth.source_mass_kg',self.judge.read(instance_id='qt_a')['observations'])
        before = self.host.debug_snapshot()
        self.assertEqual(self.judge.write(instance_id='qt_a',state_id='truth.source_mass_kg',value=.2,request_id='truth_write')['error']['code'],'ACCESS_DENIED')
        self.assertEqual(before,self.host.debug_snapshot())

    def test_write_routes_and_rejected_request_replay(self):
        req = dict(instance_id='qt_a',capability_id='dosing.set_target',arguments={'target_mass_kg':.001},request_id='before_power')
        response = self.agent.invoke(**req)
        self.assertEqual(response['error']['code'],'PRECONDITION_FAILED')
        self.ok('power.set',enabled=True)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**req),response)
        self.assertEqual(before,self.host.debug_snapshot())
        self.assertEqual(self.agent.invoke(**{**req,'arguments':{'target_mass_kg':.002}})['error']['code'],'REQUEST_CONFLICT')
        response = self.agent.write(instance_id='qt_a',state_id='dosing.target_mass_kg',value=.002,request_id='write_target')
        self.assertEqual(response['status'],'succeeded')
        self.assertEqual(self.val('dosing.target_mass_kg'),.002)
        response = self.agent.write(instance_id='qt_a',state_id='dosing.target_mass_kg',value=None,request_id='write_null')
        self.assertEqual(response['error']['code'],'INVALID_ARGUMENT')

    def test_idempotency_copy_isolation_and_historical_result(self):
        self.prepare()
        req = dict(instance_id='qt_a',capability_id='dosing.start',arguments={},request_id='once')
        first = self.agent.invoke(**req)
        self.step(2)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**req),first)
        self.assertEqual(before,self.host.debug_snapshot())
        self.assertEqual(self.agent.invoke(**{**req,'capability_id':'power.set','arguments':{'enabled':False}})['error']['code'],'REQUEST_CONFLICT')
        first['result']['dosing.elapsed_s']=999
        self.assertEqual(self.agent.invoke(**req)['result']['dosing.elapsed_s'],0)
        self.step(20)
        old = self.agent.poll(instance_id='qt_a',operation_id=first['operation_id'])
        self.ok('balance.tare')
        self.ok('dosing.set_target',target_mass_kg=.0001)
        self.ok('dosing.start')
        self.assertEqual(self.agent.poll(instance_id='qt_a',operation_id=first['operation_id'])['result'],old['result'])
        result = self.agent.read(instance_id='qt_a')
        result['observations']['dosing.elapsed_s']['value']=999
        self.assertEqual(self.val('dosing.elapsed_s'),0)

    def test_two_instances_share_clock_isolate_mass_and_requests(self):
        self.prepare('qt_a',rate=.0001)
        self.prepare('qt_b',rate=.00005)
        req = dict(capability_id='dosing.start',arguments={},request_id='same')
        a = self.agent.invoke(instance_id='qt_a',**req)
        b = self.agent.invoke(instance_id='qt_b',**req)
        self.assertNotEqual(a['operation_id'],b['operation_id'])
        self.step(10)
        self.assertEqual(self.val('dosing.status','qt_a'),'succeeded')
        self.assertEqual(self.val('dosing.status','qt_b'),'running')
        self.assertAlmostEqual(self.val('measurement.actual_dosed_kg','qt_b'),.0005)
        self.assertAlmostEqual(self.val('truth.source_mass_kg','qt_a',True),.003)
        self.assertAlmostEqual(self.val('truth.source_mass_kg','qt_b',True),.0035)
        self.assertEqual(self.agent.read(instance_id='qt_a')['clock'],self.agent.read(instance_id='qt_b')['clock'])
        other = _QtRuntime()
        other.create_instance('qt_a')
        self.assertEqual(other.time_s,0)

    def test_host_attachment_confirmation_no_fake_grasp_and_unload_preserves_mass(self):
        self.prepare()
        self.ok('dosing.start')
        self.step(20)
        self.ok('door.set_opening',opening_m=.11)
        events=[]
        def confirm(iid,event):
            events.append(event)
            return True
        before = self.host.debug_snapshot()
        with self.assertRaises(RuntimeError): self.host.host_attach('qt_a',constraint_id='c1',attachment_adapter=lambda i,e:False)
        self.assertEqual(before,self.host.debug_snapshot())
        event=self.host.host_attach('qt_a',constraint_id='c1',attachment_adapter=confirm)
        self.assertEqual(event['event'],'attach')
        self.assertEqual(event['robot'],'absent')
        with self.assertRaises(ValueError): self.host.host_unload_container('qt_a')
        with self.assertRaises(RuntimeError): self.host.host_release('qt_a',attachment_adapter=lambda i,e:None)
        self.assertEqual(self.val('container.attachment_constraint_id'),'c1')
        self.host.host_release('qt_a',attachment_adapter=confirm)
        self.assertIsNone(self.val('container.attachment_constraint_id'))
        self.assertEqual([e['event'] for e in events],['attach','release'])
        vessel=self.host.host_unload_container('qt_a')
        source=self.host.host_unload_head('qt_a')
        self.assertAlmostEqual(vessel['content_mass_kg'],.001)
        self.assertAlmostEqual(source['source_mass_kg'],.003)
        self.assertIsNone(self.val('container.loaded_id'))
        self.assertIsNone(self.val('head.loaded_id'))

    def test_declared_effects_cover_actual_changes_and_output_values(self):
        self.prepare()
        seq=[('dosing.set_target',{'target_mass_kg':.002}),('dosing.set_tolerance',{'tolerance_kg':.0002}),('container.select_position',{'position_id':'balance_pan'}),('door.set_opening',{'opening_m':.01}),('door.set_opening',{'opening_m':0}),('balance.tare',{}),('dosing.start',{}),('power.set',{'enabled':False})]
        for cid,args in seq:
            before=self.host.debug_snapshot()['instances']['qt_a']['state']
            self.ok(cid,**args)
            after=self.host.debug_snapshot()['instances']['qt_a']['state']
            changed={sid for sid in before if before[sid]!=after[sid]}
            declared={e['state_id'] for e in _qt_test_metadata()['capabilities'][cid]['state_effects']}
            self.assertLessEqual(changed,declared,(cid,changed-declared))
        for sid,obs in self.judge.read(instance_id='qt_a')['observations'].items():
            self.assertEqual(_qt_test_validate(obs['value'],_qt_test_metadata()['state_slots'][sid]['value_schema']),[],sid)

    def test_scene_bridge_target_and_authority_guards(self):
        class Location: y=0
        class Door: location=Location(); animation_data=None; constraints=[]
        class Scene: objects={'quantos__door_slide':Door()}
        scene=Scene()
        self.assertTrue(_qt_test_scene(scene,{'scene.door_opening_m':.11}))
        self.assertEqual(scene.objects['quantos__door_slide'].location.y,.11)
        with self.assertRaises(ValueError): _qt_test_scene(scene,{'wrong':.1})
        with self.assertRaises(ValueError): _qt_test_scene(scene,{'scene.door_opening_m':True})
        scene.objects['quantos__door_slide'].constraints=['override']
        with self.assertRaises(ValueError): _qt_test_scene(scene,{'scene.door_opening_m':.05})
        scene.objects={}
        with self.assertRaises(ValueError): _qt_test_scene(scene,{'scene.door_opening_m':0})
# END CHEM-174 QUANTOS Q2 XPE206DR APPEND-ONLY TESTS

# BEGIN CHEM-175 OPENTRONS FLEX APPEND-ONLY TESTS
import copy as _flex_test_copy
import json as _flex_test_json
from pathlib import Path as _flex_test_Path
import sys as _flex_test_sys
import unittest as _flex_test_unittest
_FLEX_TEST_ROOT = _flex_test_Path(__file__).resolve().parents[1]
_flex_test_sys.path.insert(0, str(_FLEX_TEST_ROOT / 'scripts'))
_flex_test_sys.path.insert(0, str(_FLEX_TEST_ROOT / 'skills' / 'chemistry-lab-assets' / 'scripts'))
from basic40_runtime_automation import (OpentronsFlexRuntime, OPENTRONS_FLEX_TYPE,
    OPENTRONS_FLEX_MIN_M3, OPENTRONS_FLEX_MAX_M3, OPENTRONS_FLEX_WORK_SLOTS,
    OPENTRONS_FLEX_STAGING_SLOTS, export_opentrons_flex_contract_metadata)
from asset_tool import lint_schema as _flex_test_lint, validate_value as _flex_test_validate


class TestOpentronsFlexRuntime(_flex_test_unittest.TestCase):
    def setUp(self):
        self.host = OpentronsFlexRuntime()
        self.host.create_instance('flex_a')
        self.host.create_instance('flex_b')
        self.host.register_principal('learner', visibility='agent')
        self.host.register_principal('judge', visibility='evaluator')
        self.agent, self.judge = self.host.session('learner'), self.host.session('judge')
        self.counter = 0
        self.origin = {'x_m': 0.0, 'y_m': 0.0, 'z_m': 0.0}
        self.dest = {'x_m': -0.23, 'y_m': 0.21, 'z_m': 0.05}
        self.events = []

    def invoke(self, cid, instance='flex_a', **arguments):
        self.counter += 1
        return self.agent.invoke(instance_id=instance, capability_id=cid, arguments=arguments, request_id='flex_test_%d' % self.counter)

    def ok(self, cid, instance='flex_a', **arguments):
        result = self.invoke(cid, instance, **arguments)
        self.assertEqual(result['status'], 'succeeded', result)
        schema = export_opentrons_flex_contract_metadata()['capabilities'][cid]['output_schema']
        self.assertEqual(_flex_test_validate(result['result'], schema), [])
        return result

    def value(self, sid, instance='flex_a', debug=False):
        return (self.judge if debug else self.agent).read(instance_id=instance, state_ids=[sid])['observations'][sid]['value']

    def register(self, *, instance='flex_a', capacity=1e-6, donor=2e-6, receiver=0.0, receiver_capacity=5e-6, receiver_slot='A2'):
        self.host.host_register_tip(instance, 'fixture_tip', slot_id='A1', capacity_m3=capacity, position_m=self.origin, evidence='Synthetic empty tip; host capacity input')
        self.host.host_register_container(instance, 'fixture_donor', slot_id='B1', capacity_m3=5e-6, volume_m3=donor, position_m=self.origin, evidence='Synthetic container ledger fixture')
        self.host.host_register_container(instance, 'fixture_receiver', slot_id=receiver_slot, capacity_m3=receiver_capacity, volume_m3=receiver, position_m=self.dest, evidence='Synthetic container ledger fixture')

    def attach(self, *, instance='flex_a', **register):
        self.register(instance=instance, **register)
        self.ok('power.set', instance, enabled=True)
        self.confirm('attach', 'A1', instance=instance)
        self.ok('tip.attach', instance, tip_id='fixture_tip')

    def confirm(self, event, slot, *, position=None, instance='flex_a'):
        self.host.host_confirm_tip_event(instance, event=event, tip_id='fixture_tip', slot_id=slot,
            position_m=position or self.origin, evidence='Synthetic host constraint assertion; no contact simulation')

    def total(self, instance='flex_a'):
        item = self.host.debug_snapshot()['instances'][instance]
        return sum(v['volume_m3'] for v in item['containers'].values()) + sum(v['volume_m3'] for v in item['tips'].values())

    def assert_reject_unchanged(self, cid, code='PRECONDITION_FAILED', **arguments):
        before = self.host.debug_snapshot()
        response = self.invoke(cid, **arguments)
        self.assertEqual(response['error']['code'], code, response)
        self.assertEqual(response['error']['side_effects'], 'none')
        self.assertEqual(self.host.debug_snapshot(), before)
        return response

    def test_metadata_catalog_si_unknowns_and_exact_slot_ids(self):
        m = export_opentrons_flex_contract_metadata()
        self.assertEqual(m['catalog_id'], 'CHEM-175')
        self.assertEqual((OPENTRONS_FLEX_MIN_M3, OPENTRONS_FLEX_MAX_M3), (5e-9, 1e-6))
        self.assertEqual(m['parameters']['whole_instrument_mass_kg']['value'], 88.5)
        self.assertEqual(m['parameters']['whole_instrument_width_m']['value'], 0.87)
        self.assertEqual(set(OPENTRONS_FLEX_WORK_SLOTS), {r+str(c) for r in 'ABCD' for c in range(1,4)})
        self.assertEqual(set(OPENTRONS_FLEX_STAGING_SLOTS), {'A4', 'B4', 'C4', 'D4'})
        for spec in m['parameters'].values():
            if spec['evidence'] == 'unknown':
                self.assertIsNone(spec['value'])
        self.assertEqual(m['parameters']['systematic_error_fraction_at_1000ul']['value'], .005)
        self.assertEqual(m['parameters']['cv_fraction_at_1000ul']['value'], .0015)
        self.assertEqual(m['parameters']['systematic_error_fraction_at_5ul']['value'], .05)
        self.assertTrue(all(v['implementation']['robot'] == 'absent' for v in m['capabilities'].values()))
        self.assertTrue(all(v['implementation']['hardware'] == 'absent' for v in m['capabilities'].values()))

    def test_all_schemas_examples_and_initial_observations_validate(self):
        metadata = export_opentrons_flex_contract_metadata()
        for cid, cap in metadata['capabilities'].items():
            for key in ('input_schema', 'output_schema'):
                self.assertEqual(_flex_test_lint(cap[key]), [], (cid, key))
            for example in cap['example_arguments']:
                self.assertEqual(_flex_test_validate(example, cap['input_schema']), [], cid)
        for sid, slot in metadata['state_slots'].items():
            self.assertEqual(_flex_test_lint(slot['value_schema']), [], sid)
            self.assertEqual(_flex_test_validate(self.value(sid, debug=True), slot['value_schema']), [], sid)
        unavailable = self.judge.read(instance_id='flex_a', state_ids=['truth.tip_volume_m3'])['observations']['truth.tip_volume_m3']
        self.assertIsNone(unavailable['value'])
        self.assertEqual(unavailable['quality'], 'unavailable')

    def test_reads_and_query_do_not_advance_or_mutate(self):
        self.attach()
        self.host.host_set_clock(7)
        before = self.host.debug_snapshot()
        for _ in range(3):
            self.agent.read(instance_id='flex_a')
            self.judge.read(instance_id='flex_a')
            self.agent.describe(instance_id='flex_a')
            self.agent.describe(asset_type_id=OPENTRONS_FLEX_TYPE)
            self.ok('state.read')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertEqual(self.host.time_s, 7)

    def test_power_required_and_write_routes_supported_setpoint(self):
        self.assert_reject_unchanged('motion.move', **self.dest)
        self.assert_reject_unchanged('sample.aspirate', container_id='fixture_donor', volume_m3=5e-9)
        result = self.agent.write(instance_id='flex_a', state_id='power.enabled', value=True, request_id='write_power')
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(self.value('power.enabled'))
        for sid in ('motion.x_m', 'truth.tip_volume_m3', 'tip.attached_id'):
            before = self.host.debug_snapshot()
            result = self.agent.write(instance_id='flex_a', state_id=sid, value=0, request_id='write_'+sid)
            self.assertEqual(result['error']['code'], 'ACCESS_DENIED')
            self.assertEqual(self.host.debug_snapshot(), before)

    def test_joint_atomic_motion_and_scene_callback_values(self):
        self.ok('power.set', enabled=True)
        self.host.host_register_scene_adapter('flex_a', lambda iid, xyz: self.events.append((iid, _flex_test_copy.deepcopy(xyz))))
        self.host.host_sync_scene('flex_a')
        self.assertEqual(self.events, [('flex_a', self.origin)])
        self.ok('motion.move', **self.dest)
        self.assertEqual(self.events[-1], ('flex_a', self.dest))
        self.assertEqual({k: self.value('motion.'+k) for k in self.dest}, self.dest)
        self.ok('motion.move', x_m=.24, y_m=-.21, z_m=.22)
        self.ok('motion.move', x_m=-.24, y_m=.21, z_m=0)
        self.assertEqual(self.host.time_s, 0)
        unbound = self.agent.describe(instance_id='flex_b')['capabilities']['motion.move']
        self.assertEqual(unbound['model_effects']['mode'], 'none')
        self.assertEqual(unbound['implementation']['scene'], 'absent')

    def test_scene_exception_does_not_commit_and_is_unknown_external_effect(self):
        self.ok('power.set', enabled=True)
        def fail(iid, xyz):
            raise RuntimeError('SECRET_HOST_DETAIL')
        self.host.host_register_scene_adapter('flex_a', fail)
        before = self.host.debug_snapshot()
        result = self.invoke('motion.move', **self.dest)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['side_effects'], 'unknown')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertNotIn('SECRET_HOST_DETAIL', _flex_test_json.dumps(result))

    def test_numeric_bool_nonfinite_and_out_of_range_rejected(self):
        self.attach()
        for value in (True, False, float('nan'), float('inf'), -float('inf'), 10**400, -1, 0, 4.99e-9, 1.001e-6):
            self.assert_reject_unchanged('sample.aspirate', code='INVALID_ARGUMENT', container_id='fixture_donor', volume_m3=value)
        for key, values in {'x_m': (.24001, -.24001), 'y_m': (.21001, -.21001), 'z_m': (-.00001, .22001)}.items():
            for value in values + (True, float('nan'), float('inf'), 10**400):
                self.assert_reject_unchanged('motion.move', code='INVALID_ARGUMENT', **{**self.origin, key: value})
        self.assert_reject_unchanged('power.set', code='INVALID_ARGUMENT', enabled=1)

    def test_unknown_arguments_bad_types_and_injected_role_fail(self):
        self.assert_reject_unchanged('power.set', code='INVALID_ARGUMENT', enabled=True, role='evaluator')
        self.assert_reject_unchanged('motion.move', code='INVALID_ARGUMENT', x_m=0, y_m=0)
        self.assert_reject_unchanged('state.read', code='INVALID_ARGUMENT', state_ids=['truth.containers'])
        for iid in ({}, [], True, 0, ''):
            self.assertEqual(self.agent.read(instance_id=iid)['status'], 'rejected')
            self.assertEqual(self.agent.describe(instance_id=iid)['status'], 'rejected')
            self.assertEqual(self.agent.invoke(instance_id=iid, capability_id='power.set', arguments={'enabled':True}, request_id='bad')['status'], 'rejected')
        for args in (None, [], True, {'enabled': True, 'extra': object()}):
            response = self.agent.invoke(instance_id='flex_a', capability_id='power.set', arguments=args, request_id='bad_args'+str(self.counter))
            self.counter += 1
            self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT')

    def test_attach_requires_host_confirmation_and_matching_position(self):
        self.register()
        self.ok('power.set', enabled=True)
        self.assert_reject_unchanged('tip.attach', tip_id='fixture_tip')
        self.confirm('attach', 'A2')
        self.assert_reject_unchanged('tip.attach', tip_id='fixture_tip')
        self.confirm('attach', 'A1')
        self.ok('motion.move', **self.dest)
        self.ok('motion.move', **self.origin)
        self.assert_reject_unchanged('tip.attach', tip_id='fixture_tip')
        self.confirm('attach', 'A1')
        self.ok('tip.attach', tip_id='fixture_tip')
        self.assertEqual(self.value('tip.attached_id'), 'fixture_tip')
        self.assertEqual(self.value('tip.events')[0]['constraint_authority'], 'host')
        self.assert_reject_unchanged('tip.attach', tip_id='fixture_tip')

    def test_sample_requires_known_donor_tip_and_matching_target(self):
        self.register()
        self.ok('power.set', enabled=True)
        self.assert_reject_unchanged('sample.aspirate', container_id='fixture_donor', volume_m3=5e-9)
        self.confirm('attach', 'A1')
        self.ok('tip.attach', tip_id='fixture_tip')
        self.assert_reject_unchanged('sample.aspirate', container_id='not_registered', volume_m3=5e-9)
        self.ok('motion.move', **self.dest)
        self.assert_reject_unchanged('sample.aspirate', container_id='fixture_donor', volume_m3=5e-9)
        self.ok('motion.move', **self.origin)
        self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=5e-9)

    def test_min_and_max_volume_transfers_conserve_inventory(self):
        self.attach()
        original = self.total()
        self.host.host_set_clock(12)
        self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=1e-6)
        self.assertAlmostEqual(self.value('truth.tip_volume_m3', debug=True), 1e-6, delta=1e-20)
        self.assertAlmostEqual(self.total(), original, delta=1e-20)
        self.ok('motion.move', **self.dest)
        self.ok('sample.dispense', container_id='fixture_receiver', volume_m3=5e-9)
        self.ok('sample.dispense', container_id='fixture_receiver', volume_m3=1e-6-5e-9)
        self.assertAlmostEqual(self.value('truth.tip_volume_m3', debug=True), 0, delta=1e-20)
        self.assertAlmostEqual(self.total(), original, delta=1e-20)
        self.assertEqual(self.value('sample.transfer_count'), 3)
        records = self.value('truth.transfers', debug=True)
        self.assertEqual(records[0]['donor_id'], 'fixture_donor')
        self.assertEqual(records[0]['receiver_id'], 'fixture_tip')
        self.assertEqual(records[-1]['receiver_id'], 'fixture_receiver')
        self.assertTrue(all(r['time_s'] == 12 and r['unit'] == 'm^3' for r in records))

    def test_two_hundred_minimum_transfers_empty_full_tip_without_roundoff_rejection(self):
        self.attach()
        self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=1e-6)
        self.ok('motion.move', **self.dest)
        for _ in range(200):
            self.ok('sample.dispense', container_id='fixture_receiver', volume_m3=5e-9)
        self.assertAlmostEqual(self.value('truth.tip_volume_m3', debug=True), 0.0, delta=1e-20)
        self.assertEqual(self.value('sample.transfer_count'), 201)

    def test_capacity_and_donor_inventory_failures_preserve_everything(self):
        self.attach(capacity=1e-7, donor=5e-8)
        self.assert_reject_unchanged('sample.aspirate', container_id='fixture_donor', volume_m3=1e-7)
        self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=5e-8)
        self.assert_reject_unchanged('sample.aspirate', container_id='fixture_donor', volume_m3=5e-9)
        self.ok('motion.move', **self.dest)
        self.assert_reject_unchanged('sample.dispense', container_id='fixture_receiver', volume_m3=1e-7)
        self.ok('sample.dispense', container_id='fixture_receiver', volume_m3=5e-8)
        self.assert_reject_unchanged('sample.dispense', container_id='fixture_receiver', volume_m3=5e-9)

    def test_tip_capacity_and_receiver_capacity_independently_enforced(self):
        self.attach(capacity=1e-7, receiver_capacity=5e-8)
        self.assert_reject_unchanged('sample.aspirate', container_id='fixture_donor', volume_m3=1e-6)
        self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=1e-7)
        self.assert_reject_unchanged('sample.aspirate', container_id='fixture_donor', volume_m3=5e-9)
        self.ok('motion.move', **self.dest)
        self.assert_reject_unchanged('sample.dispense', container_id='fixture_receiver', volume_m3=1e-7)
        self.ok('sample.dispense', container_id='fixture_receiver', volume_m3=5e-8)
        self.assert_reject_unchanged('sample.dispense', container_id='fixture_receiver', volume_m3=5e-9)

    def test_staging_slots_have_no_direct_pipette_transfer(self):
        self.attach(receiver_slot='A4')
        self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=1e-7)
        self.ok('motion.move', **self.dest)
        self.assert_reject_unchanged('sample.dispense', container_id='fixture_receiver', volume_m3=1e-7)

    def test_release_requires_confirmation_and_preserves_tip_contents(self):
        self.attach()
        self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=1e-7)
        total = self.total()
        self.assert_reject_unchanged('tip.release', target_slot_id='B2')
        self.confirm('release', 'B2')
        self.ok('tip.release', target_slot_id='B2')
        self.assertIsNone(self.value('tip.attached_id'))
        self.assertIsNone(self.value('truth.tip_volume_m3', debug=True))
        self.assertEqual([e['event'] for e in self.value('tip.events')], ['attach', 'release'])
        self.assertAlmostEqual(self.total(), total, delta=1e-20)
        self.confirm('attach', 'B2')
        self.ok('tip.attach', tip_id='fixture_tip')
        self.assertAlmostEqual(self.value('truth.tip_volume_m3', debug=True), 1e-7, delta=1e-20)

    def test_release_to_staging_records_event_but_cannot_pick_without_host_reposition(self):
        self.attach()
        self.confirm('release', 'A4')
        self.ok('tip.release', target_slot_id='A4')
        self.assertEqual(self.value('tip.last_slot_id'), 'A4')
        self.confirm('attach', 'A4')
        self.assert_reject_unchanged('tip.attach', tip_id='fixture_tip')

    def test_request_retry_and_conflict_without_double_transfer(self):
        self.attach()
        args = dict(instance_id='flex_a', capability_id='sample.aspirate', arguments={'container_id':'fixture_donor', 'volume_m3':1e-7}, request_id='same_transfer')
        first = self.agent.invoke(**args)
        self.assertEqual(first['status'], 'succeeded')
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**args), first)
        retry = self.agent.invoke(**args)
        retry['result'].clear()
        self.assertEqual(self.agent.invoke(**args), first)
        conflict = self.agent.invoke(**{**args, 'arguments':{'container_id':'fixture_donor', 'volume_m3':2e-7}})
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_rejected_request_stays_rejected_after_host_preconditions_change(self):
        args = dict(instance_id='flex_a', capability_id='motion.move', arguments=self.dest, request_id='retry_rejection')
        first = self.agent.invoke(**args)
        self.assertEqual(first['error']['code'], 'PRECONDITION_FAILED')
        self.ok('power.set', enabled=True)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**args), first)
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_instances_inventory_requests_and_clock_scope(self):
        self.attach()
        self.attach(instance='flex_b')
        a_before = self.host.debug_snapshot()['instances']['flex_a']
        self.ok('sample.aspirate', 'flex_b', container_id='fixture_donor', volume_m3=1e-7)
        self.assertEqual(self.host.debug_snapshot()['instances']['flex_a'], a_before)
        self.assertEqual(self.value('sample.transfer_count'), 0)
        self.assertEqual(self.value('sample.transfer_count', 'flex_b'), 1)
        self.host.host_set_clock(15)
        for iid in ('flex_a', 'flex_b'):
            self.assertEqual(self.agent.read(instance_id=iid)['clock']['time_s'], 15)
        independent = OpentronsFlexRuntime()
        independent.create_instance('flex_a')
        self.assertEqual(independent.time_s, 0)

    def test_public_discovery_reads_outputs_and_errors_hide_private_inventory(self):
        self.attach(donor=1.23456789e-6)
        outputs = [self.agent.describe(instance_id='flex_a'), self.agent.read(instance_id='flex_a'), self.ok('state.read')]
        outputs.append(self.invoke('sample.aspirate', container_id='fixture_donor', volume_m3=1e-6))
        outputs.append(self.invoke('sample.aspirate', container_id='fixture_donor', volume_m3=1e-6))
        for value in outputs:
            encoded = _flex_test_json.dumps(value)
            self.assertNotIn('truth.', encoded)
            self.assertNotIn('1.23456789', encoded)
            self.assertNotIn('Synthetic container ledger fixture', encoded)
        for sid in ('truth.tip_volume_m3', 'truth.containers', 'truth.tips', 'truth.transfers'):
            result = self.agent.read(instance_id='flex_a', state_ids=[sid])
            self.assertEqual(result['error']['code'], 'ACCESS_DENIED')
        self.assertIsNotNone(self.value('truth.containers', debug=True))
        public = self.agent.read(instance_id='flex_a')
        public['observations']['tip.events']['value'].clear()
        self.assertEqual(len(self.value('tip.events')), 1)

    def test_host_identity_scope_cannot_read_or_invoke_another_instance(self):
        self.host.register_principal('scoped', visibility='agent', instance_ids=['flex_b'])
        scoped = self.host.session('scoped')
        self.ok('power.set', enabled=True)
        for result in (scoped.read(instance_id='flex_a'), scoped.invoke(instance_id='flex_a', capability_id='power.set', arguments={'enabled':False}, request_id='out_of_scope')):
            self.assertEqual(result['error']['code'], 'UNKNOWN_INSTANCE')
            self.assertIsNone(result['state_revision'])
        self.assertTrue(self.value('power.enabled'))
        with self.assertRaises(ValueError):
            self.host.session('caller_supplied_evaluator')

    def test_unsupported_and_unknown_capabilities_do_not_mutate(self):
        for cid in ('velocity.set', 'sample.mix', 'liquid_level.detect'):
            before = self.host.debug_snapshot()
            result = self.invoke(cid)
            self.assertEqual(result['status'], 'unsupported')
            self.assertEqual(result['error']['code'], 'UNSUPPORTED')
            self.assertEqual(self.host.debug_snapshot(), before)
        self.assert_reject_unchanged('robot.grasp', code='UNKNOWN_CAPABILITY')

    def test_host_invalid_inventory_and_time_rejected_without_change(self):
        before = self.host.debug_snapshot()
        for value in (True, float('nan'), float('inf'), -1, 10**400):
            with self.assertRaises(ValueError):
                self.host.host_register_container('flex_a', 'invalid', slot_id='A1', capacity_m3=1e-6, volume_m3=value, position_m=self.origin, evidence='Synthetic')
            with self.assertRaises(ValueError):
                self.host.host_set_clock(value)
            self.assertEqual(self.host.debug_snapshot(), before)
        for position in ({'x_m':0, 'y_m':0}, {**self.origin,'z_m':True}):
            with self.assertRaises(ValueError):
                self.host.host_register_tip('flex_a', 'invalid_tip', slot_id='A1', capacity_m3=1e-6, position_m=position, evidence='Synthetic')
        with self.assertRaises(ValueError):
            self.host.host_register_scene_adapter('flex_a', None)
        self.assertEqual(self.host.debug_snapshot(), before)
        self.host.host_set_clock(2)
        with self.assertRaises(ValueError):
            self.host.host_set_clock(1)

    def test_declared_state_effects_cover_all_runtime_state_changes(self):
        self.register()
        metadata = export_opentrons_flex_contract_metadata()
        sequence = [('power.set', {'enabled':True}), ('tip.attach', {'tip_id':'fixture_tip'}),
            ('sample.aspirate', {'container_id':'fixture_donor','volume_m3':1e-7}), ('motion.move', self.dest),
            ('sample.dispense', {'container_id':'fixture_receiver','volume_m3':1e-7}), ('tip.release', {'target_slot_id':'A2'}), ('power.set', {'enabled':False})]
        for cid, args in sequence:
            if cid == 'tip.attach':
                self.confirm('attach', 'A1')
            if cid == 'tip.release':
                self.confirm('release', 'A2', position=self.dest)
            before = self.host.debug_snapshot()['instances']['flex_a']['state']
            self.ok(cid, **args)
            after = self.host.debug_snapshot()['instances']['flex_a']['state']
            changed = {sid for sid in before if before[sid] != after[sid]}
            declared = {e['state_id'] for e in metadata['capabilities'][cid]['state_effects']}
            self.assertLessEqual(changed, declared, (cid, changed - declared))
        for sid, obs in self.judge.read(instance_id='flex_a')['observations'].items():
            self.assertEqual(_flex_test_validate(obs['value'], metadata['state_slots'][sid]['value_schema']), [], sid)
# END CHEM-175 OPENTRONS FLEX APPEND-ONLY TESTS

# CHEM-176 invoke-based polling validates the published API result independently of Session.poll.
def _ahs_test_invoke_polling_surface(self):
    self.load()
    op = self.ok('sealing.start')['operation_id']
    self.step(.25)
    before = self.host.debug_snapshot()
    result = self.ok('sealing.poll', operation_id=op)
    self.assertEqual(result['result']['status'], 'running')
    self.assertEqual(result['result']['elapsed_s'], .25)
    self.assertEqual(self.host.debug_snapshot(), before)
    rejected = self.invoke('sealing.poll', operation_id='unknown')
    self.assertEqual(rejected['error']['code'], 'UNKNOWN_OPERATION')
    self.assertEqual(self.host.debug_snapshot(), before)
TestAzentaHeatSealerRuntime.test_invoke_polling_public_result = _ahs_test_invoke_polling_surface

# CHEM-176 lowercase contract slot IDs are accepted by the actual public facade.
def _ahs_test_canonical_state_aliases(self):
    self.ok('power.set', enabled=True)
    response = self.agent.write(instance_id='ahs_a', state_id='thermal.setpoint_k', value=423.15, request_id='canonical_write')
    self.assertEqual(response['status'],'succeeded')
    slots = self.agent.describe(instance_id='ahs_a')['state_slots']
    self.assertIn('thermal.setpoint_k',slots)
    self.assertNotIn('thermal.setpoint_K',slots)
    r = self.agent.read(instance_id='ahs_a',state_ids=['thermal.setpoint_k','measurement.temperature_k'])
    self.assertEqual(r['observations']['thermal.setpoint_k']['value'],423.15)
    self.assertIsNone(r['observations']['measurement.temperature_k']['value'])
    self.assertEqual(self.agent.read(instance_id='ahs_a',state_ids=['truth.temperature_k'])['error']['code'],'ACCESS_DENIED')
    self.assertIn('truth.temperature_k',self.judge.read(instance_id='ahs_a',state_ids=['truth.temperature_k'])['observations'])
TestAzentaHeatSealerRuntime.test_canonical_lowercase_state_aliases = _ahs_test_canonical_state_aliases

# BEGIN CHEM-174 QUANTOS COMMON-CLOCK RESOLUTION REGRESSION
def _qt_test_clock_resolution_rejection_is_atomic(self):
    self.prepare()
    self.step(1e9)
    self.ok('dosing.start')
    before = self.host.debug_snapshot()
    with self.assertRaises(ValueError):
        self.host.host_step(1e-12, world_time_s=self.host.time_s)
    self.assertEqual(self.host.debug_snapshot(), before)
    self.step(.25)
    self.assertAlmostEqual(self.val('measurement.actual_dosed_kg'), .000025)
    self.assertEqual(self.val('dosing.elapsed_s'), .25)
    fresh = _QtRuntime()
    fresh.host_step(1.0, world_time_s=1.0)
    fresh_before = fresh.debug_snapshot()
    with self.assertRaises(ValueError):
        fresh.host_step(1e-12, world_time_s=1.0-1e-12)
    self.assertEqual(fresh.debug_snapshot(), fresh_before)


TestQuantosQ2Runtime.test_clock_resolution_rejection_is_atomic = _qt_test_clock_resolution_rejection_is_atomic
# END CHEM-174 QUANTOS COMMON-CLOCK RESOLUTION REGRESSION

# BEGIN CHEM-175 OPENTRONS FLEX REVIEW REGRESSION TESTS

def _flex_test_accumulate_minimum_then_dispense_full_range(self):
    self.attach(donor=1e-6)
    for _ in range(200):
        self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=5e-9)
    self.assertEqual(self.value('truth.tip_volume_m3', debug=True), 1e-6)
    donor = self.host.debug_snapshot()['instances']['flex_a']['containers']['fixture_donor']
    self.assertEqual(donor['volume_m3'], 0.0)
    self.ok('motion.move', **self.dest)
    self.ok('sample.dispense', container_id='fixture_receiver', volume_m3=1e-6)
    self.assertEqual(self.value('truth.tip_volume_m3', debug=True), 0.0)
    self.assertEqual(self.value('sample.transfer_count'), 201)
    self.assertEqual(self.total(), 1e-6)


def _flex_test_decimal_roundtrip_capacity_boundaries(self):
    self.attach(donor=1e-6, capacity=1e-6)
    self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=1e-6)
    for _ in range(200):
        self.ok('sample.dispense', container_id='fixture_donor', volume_m3=5e-9)
    self.assertEqual(self.value('truth.tip_volume_m3', debug=True), 0.0)
    self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=1e-6)
    self.assertEqual(self.value('truth.tip_volume_m3', debug=True), 1e-6)
    self.assertEqual(self.total(), 1e-6)


def _flex_test_unrepresentable_inventory_change_is_atomic_rejection(self):
    self.attach()
    self.host.host_register_container('flex_a', 'extreme_fixture', slot_id='C1', capacity_m3=1e300, volume_m3=1e300,
        position_m=self.origin, evidence='Synthetic extreme arithmetic stress case; not real labware')
    self.assert_reject_unchanged('sample.aspirate', container_id='extreme_fixture', volume_m3=5e-9)
    self.host.host_register_container('flex_a', 'extreme_receiver_fixture', slot_id='C2', capacity_m3=1e300, volume_m3=1e299,
        position_m=self.origin, evidence='Synthetic extreme arithmetic stress case; not real labware')
    self.ok('sample.aspirate', container_id='fixture_donor', volume_m3=5e-9)
    self.assert_reject_unchanged('sample.dispense', container_id='extreme_receiver_fixture', volume_m3=5e-9)


def _flex_test_metadata_observes_exactly_matches_public_result_schemas(self):
    metadata = export_opentrons_flex_contract_metadata()
    for cid, cap in metadata['capabilities'].items():
        self.assertEqual(set(cap['observes']), set(cap['output_schema']['properties']), cid)
        self.assertEqual(set(cap['observes']), set(cap['output_schema']['required']), cid)
        for sid in cap['observes']:
            self.assertIn(sid, metadata['state_slots'], cid)
            self.assertEqual(metadata['state_slots'][sid]['visibility'], 'agent', cid)
        self.assertLessEqual({e['state_id'] for e in cap['state_effects']}, set(metadata['state_slots']), cid)
    self.assertIn('not a live tool pose', metadata['state_slots']['truth.tips']['value_schema']['items']['properties']['position_m']['description'])


TestOpentronsFlexRuntime.test_accumulate_minimum_then_dispense_full_range = _flex_test_accumulate_minimum_then_dispense_full_range
TestOpentronsFlexRuntime.test_decimal_roundtrip_capacity_boundaries = _flex_test_decimal_roundtrip_capacity_boundaries
TestOpentronsFlexRuntime.test_unrepresentable_inventory_change_is_atomic_rejection = _flex_test_unrepresentable_inventory_change_is_atomic_rejection
TestOpentronsFlexRuntime.test_metadata_observes_exactly_matches_public_result_schemas = _flex_test_metadata_observes_exactly_matches_public_result_schemas
# END CHEM-175 OPENTRONS FLEX REVIEW REGRESSION TESTS

# BEGIN CHEM-178 PRECISEFLEX 400 APPEND-ONLY TESTS
import copy as _pf400_test_copy
import json as _pf400_test_json
import math as _pf400_test_math
from pathlib import Path as _pf400_test_Path
import sys as _pf400_test_sys
import unittest as _pf400_test_unittest

_PF400_TEST_ROOT = _pf400_test_Path(__file__).resolve().parents[1]
_pf400_test_sys.path.insert(0, str(_PF400_TEST_ROOT / 'scripts'))
_pf400_test_sys.path.insert(0, str(_PF400_TEST_ROOT / 'skills' / 'chemistry-lab-assets' / 'scripts'))
from basic40_runtime_automation import (PreciseFlex400Runtime, PRECISEFLEX400_TYPE,
    preciseflex400_forward_kinematics, export_preciseflex400_contract_metadata)
from asset_tool import lint_schema as _pf400_test_lint, validate_value as _pf400_test_validate


class TestPreciseFlex400Runtime(_pf400_test_unittest.TestCase):
    def setUp(self):
        self.host = PreciseFlex400Runtime()
        self.host.create_instance('pf_a')
        self.host.create_instance('pf_b')
        self.host.register_principal('agent', visibility='agent')
        self.host.register_principal('judge', visibility='evaluator')
        self.agent, self.judge = self.host.session('agent'), self.host.session('judge')
        self.meta = export_preciseflex400_contract_metadata()
        self.home = {k: 0.0 for k in self.meta['kinematics']['joint_limits']}
        self.target = {**self.home, 'z_m': .2, 'shoulder_rad': .25, 'elbow_rad': -.5, 'wrist_rad': .125}
        self.counter = 0
        self.events = []

    def invoke(self, cid, instance='pf_a', **args):
        self.counter += 1
        return self.agent.invoke(instance_id=instance, capability_id=cid, arguments=args, request_id='pf_request_%d' % self.counter)

    def ok(self, cid, instance='pf_a', **args):
        response = self.invoke(cid, instance, **args)
        self.assertEqual(response['status'], 'succeeded', response)
        self.assertEqual(_pf400_test_validate(response['result'], self.meta['capabilities'][cid]['output_schema']), [], cid)
        return response

    def value(self, sid, instance='pf_a', debug=False):
        return (self.judge if debug else self.agent).read(instance_id=instance, state_ids=[sid])['observations'][sid]['value']

    def initialize(self, instance='pf_a', adapter=None):
        # Synthetic adapter verifies orchestration only; native geometry is tested separately by Blender.
        self.host.host_register_scene_adapter(instance, adapter or (lambda iid, pose: _pf400_test_copy.deepcopy(pose)))
        self.host.host_commit_pose(instance, joints=self.home, world_time_s=self.host.time_s)
        self.ok('power.set', instance, enabled=True)

    def move(self, joints=None, instance='pf_a', time_s=None):
        joints = joints or self.target
        self.ok('motion.set_joints', instance, joints=joints)
        self.host.host_commit_pose(instance, joints=joints, world_time_s=self.host.time_s if time_s is None else time_s)

    def register(self, instance='pf_a', payload=.2, constraint=True, adapter=None):
        source = preciseflex400_forward_kinematics(self.home)['tcp_position_m']
        dest = preciseflex400_forward_kinematics(self.target)['tcp_position_m']
        self.host.host_register_container(instance, 'fixture_container', source_port_id='fixture_source', position_m=source, tolerance_m=1e-6, gross_payload_kg=payload, evidence='Synthetic explicit total carried payload fixture; no material truth provided to the agent')
        self.host.host_register_receiver_port(instance, 'fixture_receiver', position_m=dest, tolerance_m=1e-6, evidence='Synthetic position tolerance, unrelated to vendor repeatability')
        if constraint:
            def callback(iid, event):
                self.events.append(_pf400_test_copy.deepcopy(event))
                return True
            self.host.host_register_constraint(instance, 'fixture_constraint', container_id='fixture_container', adapter=adapter or callback, evidence='Synthetic host attachment constraint; no contact solver')

    def attach(self):
        self.initialize()
        self.register()
        return self.ok('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')

    def reject_unchanged(self, cid, code='PRECONDITION_FAILED', **args):
        before = self.host.debug_snapshot()
        response = self.invoke(cid, **args)
        self.assertEqual(response['status'], 'rejected', response)
        self.assertEqual(response['error']['code'], code)
        self.assertEqual(self.host.debug_snapshot(), before)
        return response

    def test_metadata_schema_and_examples_are_valid(self):
        self.assertEqual(self.meta['catalog_id'], 'CHEM-178')
        self.assertEqual(self.meta['parameters']['whole_machine_mass_kg']['value'], 20)
        for key in ('manufacturer_upper_arm_length_m', 'manufacturer_forearm_length_m', 'manufacturer_wrist_to_tcp_offset_m', 'link_mass_kg', 'link_inertia_kg_m2', 'gripper_force_N', 'contact_friction', 'joint_speed_limits_rad_s'):
            self.assertIsNone(self.meta['parameters'][key]['value'])
        for cid, cap in self.meta['capabilities'].items():
            self.assertEqual(_pf400_test_lint(cap['input_schema']), [], cid)
            self.assertEqual(_pf400_test_lint(cap['output_schema']), [], cid)
            for example in cap['example_arguments']:
                self.assertEqual(_pf400_test_validate(example, cap['input_schema']), [], cid)
            self.assertEqual(cap['implementation']['robot'], 'absent')
            self.assertEqual(cap['implementation']['hardware'], 'absent')
        for sid, slot in self.meta['state_slots'].items():
            self.assertEqual(_pf400_test_lint(slot['value_schema']), [], sid)

    def test_default_positions_are_unknown_and_reads_do_not_initialize(self):
        before = self.host.debug_snapshot()
        for _ in range(3):
            read = self.agent.read(instance_id='pf_a')
            self.ok('state.read')
            self.agent.describe(instance_id='pf_a')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertEqual(read['clock']['time_s'], 0)
        self.assertIsNone(self.value('scene.joints'))
        self.assertEqual(read['observations']['scene.joints']['quality'], 'unavailable')
        self.assertEqual(self.value('motion.status'), 'uninitialized')

    def test_missing_host_cannot_move_or_attach(self):
        self.ok('power.set', enabled=True)
        self.reject_unchanged('motion.set_joints', joints=self.target)
        self.reject_unchanged('sample.attach', container_id='unknown', constraint_id='unknown')
        with self.assertRaises(ValueError):
            self.host.host_commit_pose('pf_a', joints=self.home, world_time_s=0)

    def test_setpoint_write_waits_for_host_pose_and_does_not_advance_time(self):
        self.initialize()
        actual_before = self.value('scene.joints')
        response = self.agent.write(instance_id='pf_a', state_id='motion.target_joints', value=self.target, request_id='set_target_via_write')
        self.assertEqual(response['status'], 'succeeded')
        self.assertEqual(self.value('motion.target_joints'), self.target)
        self.assertEqual(self.value('scene.joints'), actual_before)
        self.assertEqual(self.value('motion.status'), 'target_pending')
        self.assertEqual(self.host.time_s, 0)
        self.host.host_commit_pose('pf_a', joints=self.target, world_time_s=5)
        self.assertEqual(self.value('scene.joints'), self.target)
        self.assertEqual(self.value('motion.status'), 'ready')
        self.assertEqual(self.host.time_s, 5)
        self.assertEqual(self.value('scene.tcp_position_m'), preciseflex400_forward_kinematics(self.target)['tcp_position_m'])

    def test_host_pose_must_match_current_target(self):
        self.initialize()
        self.ok('motion.set_joints', joints=self.target)
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError):
            self.host.host_commit_pose('pf_a', joints=self.home, world_time_s=4)
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_each_joint_accepts_both_limits_and_rejects_outside(self):
        self.initialize()
        for key, limits in self.meta['kinematics']['joint_limits'].items():
            for limit in limits:
                joints = {**self.home, key: limit}
                self.ok('motion.set_joints', joints=joints)
                self.host.host_commit_pose('pf_a', joints=joints, world_time_s=0)
            for value in (limits[0] - 1e-9, limits[1] + 1e-9, True, float('nan'), float('inf'), 10**400, '0', None):
                self.reject_unchanged('motion.set_joints', code='INVALID_ARGUMENT', joints={**self.home, key: value})
        self.reject_unchanged('motion.set_joints', code='INVALID_ARGUMENT', joints={**self.home, 'fifth_axis': 0})
        self.reject_unchanged('motion.set_joints', code='INVALID_ARGUMENT', joints={'z_m': 0})
        self.reject_unchanged('motion.set_joints', code='INVALID_ARGUMENT', joints=self.home, extra=True)

    def test_fk_direction_reach_and_rotated_end_effector_bounds(self):
        before = self.host.debug_snapshot()
        g = self.meta['kinematics']['geometry']
        fk = self.ok('motion.forward_kinematics', joints=self.home)['result']
        self.assertAlmostEqual(fk['tcp_position_m']['x_m'], g['shoulder_x_m'] + g['upper_arm_m'] + g['forearm_m'] + g['tool_offset_local_m']['x_m'])
        self.assertAlmostEqual(fk['tcp_position_m']['y_m'], g['shoulder_y_m'] + g['tool_offset_local_m']['y_m'])
        lifted = preciseflex400_forward_kinematics({**self.home, 'z_m': .4})
        self.assertAlmostEqual(lifted['tcp_position_m']['z_m'] - fk['tcp_position_m']['z_m'], .4)
        rotated = preciseflex400_forward_kinematics({**self.home, 'shoulder_rad': .5})
        self.assertGreater(rotated['tcp_position_m']['y_m'], fk['tcp_position_m']['y_m'])
        q = self.target
        pose = preciseflex400_forward_kinematics(q)
        self.assertAlmostEqual(pose['tool_yaw_rad'], q['shoulder_rad'] + q['elbow_rad'] + q['wrist_rad'])
        # Independently transform all eight local box corners into world coordinates.
        angle = pose['tool_yaw_rad']
        corners = []
        for x in g['tool_aabb_local_m']['x_m']:
            for y in g['tool_aabb_local_m']['y_m']:
                for z in g['tool_aabb_local_m']['z_m']:
                    corners.append((pose['tcp_position_m']['x_m'] + x*_pf400_test_math.cos(angle)-y*_pf400_test_math.sin(angle), pose['tcp_position_m']['y_m']+x*_pf400_test_math.sin(angle)+y*_pf400_test_math.cos(angle), pose['tcp_position_m']['z_m']+z))
        for n, axis in enumerate(('x_m','y_m','z_m')):
            self.assertAlmostEqual(pose['end_effector_aabb_m']['min_m'][axis], min(c[n] for c in corners))
            self.assertAlmostEqual(pose['end_effector_aabb_m']['max_m'][axis], max(c[n] for c in corners))
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_bad_scene_evaluation_faults_and_does_not_claim_new_pose(self):
        def bad_scene(iid, expected):
            result = _pf400_test_copy.deepcopy(expected)
            result['tcp_position_m']['x_m'] += .01
            return result
        self.host.host_register_scene_adapter('pf_a', bad_scene)
        with self.assertRaises(RuntimeError):
            self.host.host_commit_pose('pf_a', joints=self.home, world_time_s=10)
        self.assertEqual(self.value('motion.status'), 'fault')
        self.assertIsNone(self.value('scene.joints'))
        self.assertEqual(self.host.time_s, 0)
        self.ok('power.set', enabled=True)
        self.reject_unchanged('motion.set_joints', joints=self.target)

    def test_scene_must_confirm_bounds_and_joint_values_not_only_tcp(self):
        for changed in ('joints', 'end_effector_aabb_m'):
            h = PreciseFlex400Runtime()
            h.create_instance('fixture')
            def bad(iid, expected, field=changed):
                actual = _pf400_test_copy.deepcopy(expected)
                if field == 'joints':
                    actual[field]['wrist_rad'] += .1
                else:
                    actual[field]['max_m']['z_m'] += .01
                return actual
            h.host_register_scene_adapter('fixture', bad)
            with self.assertRaises(RuntimeError):
                h.host_commit_pose('fixture', joints=self.home, world_time_s=0)
            self.assertEqual(h.debug_snapshot()['instances']['fixture']['state']['motion.status'], 'fault')

    def test_world_clock_marks_old_pose_stale_and_requires_reconfirmation(self):
        self.initialize()
        self.register()
        self.host.host_set_clock(8)
        before = self.host.debug_snapshot()
        read = self.agent.read(instance_id='pf_a', state_ids=['scene.tcp_position_m'])
        self.assertEqual(read['observations']['scene.tcp_position_m']['quality'], 'stale')
        self.assertEqual(read['observations']['scene.tcp_position_m']['sample_time_s'], 0)
        self.assertEqual(read['clock']['time_s'], 8)
        self.assertEqual(self.host.debug_snapshot(), before)
        self.reject_unchanged('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.host.host_commit_pose('pf_a', joints=self.home, world_time_s=8)
        self.ok('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')

    def test_attach_requires_registered_constraint_matching_container_and_position(self):
        self.initialize()
        self.register(constraint=False)
        self.reject_unchanged('sample.attach', container_id='fixture_container', constraint_id='missing')
        self.host.host_register_constraint('pf_a', 'fixture_constraint', container_id='fixture_container', adapter=lambda iid,event: True, evidence='Synthetic explicit constraint')
        self.reject_unchanged('sample.attach', container_id='missing', constraint_id='fixture_constraint')
        self.move()
        self.reject_unchanged('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.move(self.home)
        self.ok('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.reject_unchanged('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')

    def test_unknown_and_overweight_payload_cannot_attach(self):
        for iid, payload in (('pf_a', None), ('pf_b', .500001)):
            self.initialize(iid)
            self.register(iid, payload=payload)
            before = self.host.debug_snapshot()
            result = self.invoke('sample.attach', iid, container_id='fixture_container', constraint_id='fixture_constraint')
            self.assertEqual(result['error']['code'], 'PRECONDITION_FAILED')
            self.assertEqual(self.host.debug_snapshot(), before)
        self.assertIsNone(self.value('truth.registered_payloads', debug=True)[0]['gross_payload_kg'])

    def test_rated_payload_boundary_accepts_registered_host_constraint(self):
        self.initialize()
        self.register(payload=.5)
        self.ok('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.assertEqual(self.value('sample.attached_container_id'), 'fixture_container')
        self.assertEqual(self.events[0]['event'], 'attach')
        self.assertEqual(self.events[0]['constraint_authority'], 'host')
        self.assertEqual(self.events[0]['robot'], 'absent')

    def test_release_requires_receiver_position_and_records_host_topology(self):
        self.attach()
        self.reject_unchanged('sample.release', receiver_port_id='missing')
        self.reject_unchanged('sample.release', receiver_port_id='fixture_receiver')
        self.move(time_s=7)
        self.ok('sample.release', receiver_port_id='fixture_receiver')
        self.assertIsNone(self.value('sample.attached_container_id'))
        self.assertIsNone(self.value('sample.constraint_id'))
        self.assertEqual([e['event'] for e in self.value('sample.events')], ['attach', 'release'])
        self.assertEqual(self.value('sample.events')[-1]['time_s'], 7)
        container = self.host.debug_snapshot()['instances']['pf_a']['containers']['fixture_container']
        self.assertEqual(container['port_id'], 'fixture_receiver')
        self.ok('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.assertEqual(self.value('sample.events')[-1]['port_id'], 'fixture_receiver')

    def test_attachment_callback_failure_is_unknown_and_requires_host_reconciliation(self):
        calls = []
        def fail(iid, event):
            calls.append(event)
            return {'success': True}  # An ambiguous truthy value is not confirmation.
        self.initialize()
        self.register(adapter=fail)
        response = self.invoke('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['side_effects'], 'unknown')
        self.assertEqual(self.value('motion.status'), 'fault')
        self.assertIsNone(self.value('sample.attached_container_id'))
        self.assertEqual(self.value('sample.events'), [])
        self.reject_unchanged('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.assertEqual(len(calls), 1)
        self.host.host_reconcile_fault('pf_a', attached_container_id=None, constraint_id=None, evidence='Synthetic host inspected external world and confirmed no constraint exists')
        self.assertEqual(self.value('motion.status'), 'uninitialized')
        self.assertIsNone(self.value('scene.joints'))
        self.host.host_commit_pose('pf_a', joints=self.home, world_time_s=0)
        self.assertEqual(self.value('motion.status'), 'ready')

    def test_release_callback_exception_keeps_registered_attachment_and_faults(self):
        def adapter(iid, event):
            if event['event'] == 'release':
                raise RuntimeError('Synthetic failure')
            return True
        self.initialize()
        self.register(adapter=adapter)
        self.ok('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.move()
        result = self.invoke('sample.release', receiver_port_id='fixture_receiver')
        self.assertEqual(result['error']['side_effects'], 'unknown')
        self.assertEqual(self.value('motion.status'), 'fault')
        self.assertEqual(self.value('sample.attached_container_id'), 'fixture_container')
        self.assertEqual(len(self.value('sample.events')), 1)

    def test_request_dedup_conflict_and_deep_copy(self):
        self.initialize()
        self.register()
        args = {'instance_id':'pf_a', 'capability_id':'sample.attach', 'arguments':{'container_id':'fixture_container', 'constraint_id':'fixture_constraint'}, 'request_id':'one_attach'}
        first = self.agent.invoke(**args)
        self.assertEqual(first['status'], 'succeeded')
        before = self.host.debug_snapshot()
        for _ in range(2):
            self.assertEqual(self.agent.invoke(**args), first)
        returned = self.agent.invoke(**args)
        returned['result']['sample.events'].clear()
        self.assertEqual(self.agent.invoke(**args), first)
        conflict = self.agent.invoke(**{**args, 'arguments': {'container_id':'other', 'constraint_id':'fixture_constraint'}})
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_rejection_remains_cached_after_host_registration(self):
        args = {'instance_id':'pf_a', 'capability_id':'motion.set_joints', 'arguments':{'joints':self.target}, 'request_id':'rejected_target'}
        first = self.agent.invoke(**args)
        self.assertEqual(first['status'], 'rejected')
        self.initialize()
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**args), first)
        self.assertEqual(self.host.debug_snapshot(), before)

    def test_instance_isolation_and_host_authenticated_scope(self):
        self.initialize('pf_a')
        self.initialize('pf_b')
        self.register('pf_a')
        self.register('pf_b')
        before = self.host.debug_snapshot()['instances']['pf_a']
        self.ok('sample.attach', 'pf_b', container_id='fixture_container', constraint_id='fixture_constraint')
        self.assertEqual(self.host.debug_snapshot()['instances']['pf_a'], before)
        self.host.register_principal('scoped', visibility='agent', instance_ids=['pf_b'])
        scoped = self.host.session('scoped')
        for result in (scoped.read(instance_id='pf_a'), scoped.describe(instance_id='pf_a'), scoped.invoke(instance_id='pf_a', capability_id='power.set', arguments={'enabled':False}, request_id='outside')):
            self.assertEqual(result['error']['code'], 'UNKNOWN_INSTANCE')
            self.assertIsNone(result['state_revision'])
        with self.assertRaises(ValueError):
            self.host.session('caller_claims_evaluator')
        independent = PreciseFlex400Runtime()
        independent.create_instance('pf_a')
        self.assertEqual(independent.time_s, 0)
        self.assertEqual(independent.debug_snapshot()['instances']['pf_a']['state']['sample.container_ids'], [])

    def test_public_api_hides_payload_truth_and_host_evidence(self):
        self.initialize()
        self.register(payload=.123456789)
        for response in (self.agent.describe(instance_id='pf_a'), self.agent.describe(asset_type_id=PRECISEFLEX400_TYPE), self.agent.read(instance_id='pf_a'), self.ok('state.read')):
            encoded = _pf400_test_json.dumps(response)
            self.assertNotIn('truth.', encoded)
            self.assertNotIn('0.123456789', encoded)
            self.assertNotIn('Synthetic explicit total carried payload fixture', encoded)
        denied = self.agent.read(instance_id='pf_a', state_ids=['truth.registered_payloads'])
        self.assertEqual(denied['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(self.value('truth.registered_payloads', debug=True)[0]['gross_payload_kg'], .123456789)
        for sid in ('scene.joints', 'truth.registered_payloads', 'sample.constraint_id'):
            before = self.host.debug_snapshot()
            reply = self.agent.write(instance_id='pf_a', state_id=sid, value=None, request_id='forbidden_'+sid)
            self.assertEqual(reply['error']['code'], 'ACCESS_DENIED')
            self.assertEqual(self.host.debug_snapshot(), before)

    def test_invalid_host_clock_geometry_and_registry_rejected_without_change(self):
        before = self.host.debug_snapshot()
        for value in (True, float('nan'), float('inf'), -1, 10**400):
            with self.assertRaises(ValueError):
                self.host.host_set_clock(value)
            self.assertEqual(self.host.debug_snapshot(), before)
        with self.assertRaises(ValueError):
            self.host.host_register_scene_adapter('pf_a', None)
        for value in (True, -1, float('nan'), float('inf'), 10**400):
            with self.assertRaises(ValueError):
                self.host.host_register_container('pf_a', 'invalid', source_port_id='source', position_m={'x_m':0,'y_m':0,'z_m':0}, tolerance_m=1e-5, gross_payload_kg=value, evidence='Synthetic')
            self.assertEqual(self.host.debug_snapshot(), before)
        with self.assertRaises(ValueError):
            self.host.host_register_receiver_port('pf_a', 'bad', position_m={'x_m':0,'y_m':0}, tolerance_m=1e-5, evidence='Synthetic')
        with self.assertRaises(ValueError):
            self.host.host_register_constraint('pf_a', 'bad', container_id='missing', adapter=lambda iid,e:True, evidence='Synthetic')
        self.assertEqual(self.host.debug_snapshot(), before)
        self.host.host_set_clock(3)
        with self.assertRaises(ValueError):
            self.host.host_set_clock(2)

    def test_unsupported_claims_do_not_mutate_or_report_fake_success(self):
        before = self.host.debug_snapshot()
        for cid,args in (('robot.contact_grasp',{}), ('motion.set_tcp_velocity',{'velocity_m_s':.1})):
            reply = self.invoke(cid, **args)
            self.assertEqual(reply['status'], 'unsupported')
            self.assertEqual(reply['error']['code'], 'UNSUPPORTED')
            self.assertEqual(self.host.debug_snapshot(), before)
        self.reject_unchanged('robot.pick', code='UNKNOWN_CAPABILITY')

    def test_declared_effects_cover_every_public_operation_change(self):
        self.initialize()
        self.register()
        sequence = [('power.set', {'enabled':True}), ('sample.attach', {'container_id':'fixture_container','constraint_id':'fixture_constraint'}), ('motion.set_joints', {'joints':self.target}), ('sample.release', {'receiver_port_id':'fixture_receiver'}), ('power.set', {'enabled':False})]
        for cid,args in sequence:
            if cid == 'sample.release':
                self.host.host_commit_pose('pf_a', joints=self.target, world_time_s=0)
            before = self.host.debug_snapshot()['instances']['pf_a']['state']
            self.ok(cid, **args)
            after = self.host.debug_snapshot()['instances']['pf_a']['state']
            changed = {sid for sid in before if before[sid] != after[sid]}
            declared = {entry['state_id'] for entry in self.meta['capabilities'][cid]['state_effects']}
            self.assertLessEqual(changed, declared, (cid, changed-declared))
        for sid, obs in self.judge.read(instance_id='pf_a')['observations'].items():
            self.assertEqual(_pf400_test_validate(obs['value'], self.meta['state_slots'][sid]['value_schema']), [], sid)

    def test_release_partial_failure_can_reconcile_real_receiver_location(self):
        def adapter(iid, event):
            if event['event'] == 'release':
                raise RuntimeError('Synthetic constraint released but reply lost')
            return True
        self.initialize()
        self.register(adapter=adapter)
        self.ok('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.move()
        result = self.invoke('sample.release', receiver_port_id='fixture_receiver')
        self.assertEqual(result['status'], 'failed')
        position = preciseflex400_forward_kinematics(self.target)['tcp_position_m']
        self.host.host_reconcile_fault('pf_a', attached_container_id=None, constraint_id=None, evidence='Synthetic host verified container at receiver', container_locations=[{'container_id':'fixture_container','port_id':'fixture_receiver','position_m':position,'tolerance_m':1e-6}])
        self.host.host_commit_pose('pf_a', joints=self.target, world_time_s=0)
        self.ok('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')
        self.assertEqual(self.value('sample.events')[-1]['port_id'], 'fixture_receiver')

    def test_reconciliation_without_container_position_keeps_attach_disabled(self):
        self.initialize()
        self.register(adapter=lambda iid,event: False)
        self.assertEqual(self.invoke('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')['status'], 'failed')
        self.host.host_reconcile_fault('pf_a', attached_container_id=None, constraint_id=None, evidence='Synthetic host confirmed no constraint; container position not confirmed')
        self.host.host_commit_pose('pf_a', joints=self.home, world_time_s=0)
        self.reject_unchanged('sample.attach', container_id='fixture_container', constraint_id='fixture_constraint')

    def test_attachment_callback_cannot_reenter_same_request(self):
        args = {'instance_id':'pf_a','capability_id':'sample.attach','arguments':{'container_id':'fixture_container','constraint_id':'fixture_constraint'},'request_id':'reentrant_request'}
        reentries = []
        def callback(iid,event):
            reentries.append(self.agent.invoke(**args))
            self.assertEqual(self.agent.read(instance_id=iid)['status'], 'succeeded')
            return True
        self.initialize()
        self.register(adapter=callback)
        self.assertEqual(self.agent.invoke(**args)['status'], 'succeeded')
        self.assertEqual(len(reentries), 1)
        self.assertEqual(reentries[0]['error']['code'], 'BUSY')
        self.assertEqual(len(self.value('sample.events')), 1)
        self.assertEqual(self.agent.invoke(**args)['status'], 'succeeded')
        self.assertEqual(len(reentries), 1)

    def test_scene_callback_cannot_advance_clock_or_mutate_another_instance(self):
        def callback(iid,pose):
            with self.assertRaises(ValueError):
                self.host.host_set_clock(6)
            with self.assertRaises(ValueError):
                self.host.create_instance('illegal_during_callback')
            result = self.agent.invoke(instance_id='pf_b', capability_id='power.set', arguments={'enabled':True}, request_id='illegal_power')
            self.assertEqual(result['error']['code'], 'BUSY')
            return pose
        self.host.host_register_scene_adapter('pf_a', callback)
        self.host.host_commit_pose('pf_a', joints=self.home, world_time_s=5)
        self.assertEqual(self.host.time_s, 5)
        self.assertFalse(self.value('power.enabled', 'pf_b'))
        self.assertNotIn('illegal_during_callback', self.host.debug_snapshot()['instances'])

    def test_official_joint_limits_and_proxy_dimensions_remain_separate_evidence(self):
        limits = self.meta['kinematics']['joint_limits']
        for key, deg in (('shoulder_rad',93),('elbow_rad',168),('wrist_rad',960)):
            self.assertAlmostEqual(limits[key][0], -_pf400_test_math.radians(deg))
            self.assertAlmostEqual(limits[key][1], _pf400_test_math.radians(deg))
        self.assertEqual(limits['z_m'], (0.0,0.4))
        self.assertEqual(self.meta['kinematics']['geometry']['evidence'], 'assumption')
        self.assertAlmostEqual(preciseflex400_forward_kinematics(self.home)['tcp_position_m']['x_m'], .579)
        self.assertIsNone(self.meta['parameters']['manufacturer_upper_arm_length_m']['value'])
        self.assertIsNone(self.meta['parameters']['manufacturer_forearm_length_m']['value'])

    def test_wrist_rotates_tool_offset_and_retains_unwrapped_joint_angle(self):
        turned = preciseflex400_forward_kinematics({**self.home,'wrist_rad':_pf400_test_math.pi/2})
        self.assertAlmostEqual(turned['tcp_position_m']['x_m'], .5)
        self.assertAlmostEqual(turned['tcp_position_m']['y_m'], .079)
        full = preciseflex400_forward_kinematics({**self.home,'wrist_rad':2*_pf400_test_math.pi})
        self.assertAlmostEqual(full['tcp_position_m']['x_m'], .579)
        self.assertAlmostEqual(full['tcp_position_m']['y_m'], 0)
        self.assertAlmostEqual(full['tool_yaw_rad'], 2*_pf400_test_math.pi)
        self.assertAlmostEqual(full['joints']['wrist_rad'], 2*_pf400_test_math.pi)

    def test_fk_tcp_stays_inside_declared_horizontal_reach_at_joint_extremes(self):
        limits = self.meta['kinematics']['joint_limits']
        for sh in (*limits['shoulder_rad'],0):
            for el in (*limits['elbow_rad'],0):
                for wr in (*limits['wrist_rad'],0):
                    for z in (0,.4):
                        p = preciseflex400_forward_kinematics({'z_m':z,'shoulder_rad':sh,'elbow_rad':el,'wrist_rad':wr})['tcp_position_m']
                        self.assertLessEqual(_pf400_test_math.hypot(p['x_m'],p['y_m']), .579+1e-12)
                        self.assertAlmostEqual(p['z_m'], .2+z)
# END CHEM-178 PRECISEFLEX 400 APPEND-ONLY TESTS
