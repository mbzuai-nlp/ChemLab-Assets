"""Synthetic CHEM-188 process/API tests; no hardware or actual scene evaluation."""
import copy
import json
import math
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "chemistry-lab-assets" / "scripts"))
from basic40_runtime_vacuum import (ASSET_TYPE_ID, MAXIMUM_PUMPING_SPEED_M3_S,
    STATE_SLOTS, VacuumHost, VacuumSceneSynchronizationError, export_contract_metadata)
from asset_tool import lint_schema, validate_value


class Basic40VacuumTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = VacuumHost(scene_adapter=lambda instance, updates: self.calls.append((instance, copy.deepcopy(updates))))
        self.host.create_instance("source")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.sequence = 0

    def invoke(self, cap, *, instance="source", **args):
        self.sequence += 1
        return self.agent.invoke(instance_id=instance, capability_id=cap, arguments=args, request_id=f"request_{self.sequence}")

    def ok(self, cap, *, instance="source", **args):
        result = self.invoke(cap, instance=instance, **args)
        self.assertEqual(result["status"], "succeeded", result)
        return result

    def value(self, sid, *, instance="source", truth=False):
        session = self.judge if truth else self.agent
        return session.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def connect(self, *, initial=None, volume=.001, leak=0, instance="source", chamber="fixture"):
        self.host.create_chamber(chamber, initial_pressure_pa=initial, volume_m3=volume, leak_throughput_pa_m3_s=leak,
            evidence="engineering_assumption: synthetic test fixture; not a manufacturer chamber")
        self.host.connect_source(instance, chamber)

    def start(self, **kwargs):
        self.connect(**kwargs)
        instance = kwargs.get("instance", "source")
        self.ok("power.set", enabled=True, instance=instance)
        self.ok("vacuum.start", instance=instance)

    def test_source_requires_power_and_external_chamber(self):
        for stage in ("unpowered", "no_chamber"):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("vacuum.start")["error"]["code"], "PRECONDITION_FAILED", stage)
            self.assertEqual(before, self.host.debug_snapshot())
            self.ok("power.set", enabled=True)
        self.connect()
        self.ok("vacuum.start")
        self.assertEqual(self.host.time_s, 0)
        self.assertEqual(self.value("vacuum.pressure_pa", truth=True), 101325)

    def test_read_describe_are_pure_and_missing_values_are_unavailable(self):
        before = self.host.debug_snapshot()
        for _ in range(3):
            reading = self.agent.read(instance_id="source")
            self.agent.describe(instance_id="source")
        self.assertEqual(before, self.host.debug_snapshot())
        for sid in ("vacuum.indicated_pressure_pa", "recovery.condensate_volume_m3"):
            self.assertIsNone(reading["observations"][sid]["value"])
            self.assertEqual(reading["observations"][sid]["quality"], "unavailable")
            self.assertTrue(reading["observations"][sid]["reason"])

    def test_pressure_down_matches_closed_form_without_leak(self):
        self.start(volume=.010)
        self.host.step(30)
        expected = 200 + (101325 - 200) * math.exp(-MAXIMUM_PUMPING_SPEED_M3_S * 30 / .010)
        self.assertAlmostEqual(self.value("vacuum.pressure_pa", truth=True), expected, delta=1e-8)
        self.assertAlmostEqual(self.value("vacuum.removed_gas_pa_m3", truth=True), .010 * (101325 - expected), delta=1e-9)
        self.assertEqual(self.value("vacuum.indicated_pressure_pa"), round(expected))
        readout = self.agent.read(instance_id="source", state_ids=["vacuum.indicated_pressure_pa"])["observations"]["vacuum.indicated_pressure_pa"]
        self.assertEqual(readout["quality"], "estimated")

    def test_pumping_speed_depends_on_pressure_and_never_exceeds_catalog_maximum(self):
        self.start(volume=.010)
        fast = self.value("vacuum.pumping_speed_m3_s", truth=True)
        self.host.step(100)
        slow = self.value("vacuum.pumping_speed_m3_s", truth=True)
        self.assertGreater(fast, slow)
        self.assertGreater(slow, 0)
        self.assertLessEqual(fast, 2 / 3600)
        p = self.value("vacuum.pressure_pa", truth=True)
        self.assertAlmostEqual(slow, MAXIMUM_PUMPING_SPEED_M3_S * (1 - 200 / p))

    def test_closed_and_open_gas_ballast_have_distinct_ultimate_pressures(self):
        for enabled, floor in ((False, 200.0), (True, 400.0)):
            with self.subTest(gas_ballast=enabled):
                host = VacuumHost()
                host.create_instance("pump")
                host.create_chamber("fixture")
                host.connect_source("pump", "fixture")
                host.register_principal("e", visibility="evaluator")
                session = host.session("e")
                for n, (cap, args) in enumerate((('power.set', {'enabled': True}), ('vacuum.set_gas_ballast', {'enabled': enabled}), ('vacuum.start', {}))):
                    self.assertEqual(session.invoke(instance_id="pump", capability_id=cap, arguments=args, request_id=str(n))["status"], "succeeded")
                host.step(300)
                p = session.read(instance_id="pump", state_ids=["vacuum.pressure_pa"])["observations"]["vacuum.pressure_pa"]["value"]
                self.assertGreaterEqual(p, floor)
                self.assertAlmostEqual(p, floor, delta=1e-7)

    def test_pressure_setpoint_does_not_teleport_and_control_stops_at_target(self):
        self.start()
        self.ok("vacuum.set_pressure", pressure_pa=5000.0)
        self.assertEqual(self.value("vacuum.pressure_pa", truth=True), 101325)
        self.host.step(60)
        self.assertAlmostEqual(self.value("vacuum.pressure_pa", truth=True), 5000)
        self.assertEqual(self.value("vacuum.pumping_speed_m3_s", truth=True), 0)
        removed = self.value("vacuum.removed_gas_pa_m3", truth=True)
        self.host.step(60)
        self.assertEqual(self.value("vacuum.removed_gas_pa_m3", truth=True), removed)

    def test_higher_target_and_opening_ballast_do_not_invent_a_vent_valve(self):
        self.start(initial=250)
        self.ok("vacuum.set_pressure", pressure_pa=10000)
        self.ok("vacuum.set_gas_ballast", enabled=True)
        self.host.step(100)
        self.assertEqual(self.value("vacuum.pressure_pa", truth=True), 250)
        self.assertEqual(self.value("vacuum.removed_gas_pa_m3", truth=True), 0)

    def test_pump_down_mode_ignores_pressure_control_target(self):
        self.start()
        self.ok("vacuum.set_pressure", pressure_pa=20000)
        self.host.step(30)
        self.assertEqual(self.value("vacuum.pressure_pa", truth=True), 20000)
        self.ok("vacuum.set_mode", mode="pump_down")
        self.host.step(30)
        self.assertLess(self.value("vacuum.pressure_pa", truth=True), 210)
        self.assertEqual(self.value("vacuum.target_pressure_pa"), 20000)

    def test_leak_equilibrium_and_gas_inventory_are_conserved(self):
        volume, leak, ambient = .001, .05, 101325
        self.start(volume=volume, leak=leak)
        self.host.step(120)
        chamber = self.host.debug_snapshot()["chambers"]["fixture"]
        q = MAXIMUM_PUMPING_SPEED_M3_S
        equilibrium = (q * 200 + leak) / (q + leak / ambient)
        self.assertAlmostEqual(chamber["pressure_pa"], equilibrium, delta=1e-7)
        removed = self.value("vacuum.removed_gas_pa_m3", truth=True)
        self.assertAlmostEqual(volume * ambient + chamber["admitted_gas_pa_m3"] - removed, volume * chamber["pressure_pa"], delta=1e-10)

    def test_leak_raises_low_pressure_then_controller_holds_target(self):
        volume, leak, initial, target = .001, .1, 1000, 2000
        self.start(volume=volume, leak=leak, initial=initial)
        self.ok("vacuum.set_pressure", pressure_pa=target)
        self.host.step(100)
        chamber = self.host.debug_snapshot()["chambers"]["fixture"]
        self.assertAlmostEqual(chamber["pressure_pa"], target, delta=1e-8)
        removed = self.value("vacuum.removed_gas_pa_m3", truth=True)
        self.assertGreater(removed, 0)
        self.assertAlmostEqual(volume * initial + chamber["admitted_gas_pa_m3"] - removed, volume * target, delta=1e-10)
        expected_q = leak * (1 - target / self.host.ambient_pressure_pa) / target
        self.assertAlmostEqual(self.value("vacuum.pumping_speed_m3_s", truth=True), expected_q)

    def test_stop_and_power_off_preserve_removed_gas_and_allow_only_external_leak(self):
        self.start(leak=.01)
        self.host.step(30)
        for cap, args in (("vacuum.stop", {}), ("power.set", {"enabled": False})):
            self.ok(cap, **args)
            before_p = self.value("vacuum.pressure_pa", truth=True)
            removed = self.value("vacuum.removed_gas_pa_m3", truth=True)
            self.host.step(10)
            self.assertGreater(self.value("vacuum.pressure_pa", truth=True), before_p)
            self.assertEqual(self.value("vacuum.removed_gas_pa_m3", truth=True), removed)
            self.assertFalse(self.value("vacuum.running"))
            self.assertEqual(self.value("vacuum.pumping_speed_m3_s", truth=True), 0)
            if cap == "vacuum.stop":
                self.ok("vacuum.start")

    def test_steps_are_partition_invariant_through_control_crossings(self):
        for initial, leak, target in ((101325, .03, 2000), (1000, .1, 2000), (10000, 0, 200), (101325, 100, 5000)):
            outcomes = []
            for steps in ((120,), (3, 7, 20, 30, 60)):
                host = VacuumHost()
                host.create_instance("p")
                host.create_chamber("c", initial_pressure_pa=initial, leak_throughput_pa_m3_s=leak)
                host.connect_source("p", "c")
                host.register_principal("e", visibility="evaluator")
                session = host.session("e")
                for n, (cap, args) in enumerate((("power.set", {"enabled": True}), ("vacuum.set_pressure", {"pressure_pa": target}), ("vacuum.start", {}))):
                    session.invoke(instance_id="p", capability_id=cap, arguments=args, request_id=str(n))
                for dt in steps:
                    host.step(dt)
                snap = host.debug_snapshot()
                outcomes.append((snap["chambers"]["c"]["pressure_pa"], snap["chambers"]["c"]["admitted_gas_pa_m3"], snap["instances"]["p"]["state"]["vacuum.removed_gas_pa_m3"]))
            for a, b in zip(*outcomes):
                self.assertAlmostEqual(a, b, delta=1e-8)

    def test_randomized_boundary_inventory_and_nonnegative_pressure(self):
        rng = random.Random(188)
        for trial in range(30):
            host = VacuumHost()
            host.create_instance("p")
            volume, initial, leak = 10 ** rng.uniform(-5, -1), rng.uniform(0, 101325), rng.uniform(0, .2)
            host.create_chamber("c", volume_m3=volume, initial_pressure_pa=initial, leak_throughput_pa_m3_s=leak)
            host.connect_source("p", "c")
            host.register_principal("a")
            session = host.session("a")
            for n, (cap, args) in enumerate((("power.set", {"enabled": True}), ("vacuum.set_pressure", {"pressure_pa": rng.uniform(200, 30000)}), ("vacuum.set_gas_ballast", {"enabled": bool(trial % 2)}), ("vacuum.start", {}))):
                session.invoke(instance_id="p", capability_id=cap, arguments=args, request_id=str(n))
            host.step(rng.uniform(.01, 1000))
            snap = host.debug_snapshot()
            chamber, source = snap["chambers"]["c"], snap["instances"]["p"]["state"]
            self.assertGreaterEqual(chamber["pressure_pa"], 0)
            self.assertLessEqual(chamber["pressure_pa"], host.ambient_pressure_pa)
            self.assertGreaterEqual(source["vacuum.removed_gas_pa_m3"], 0)
            self.assertAlmostEqual(initial * volume + chamber["admitted_gas_pa_m3"] - source["vacuum.removed_gas_pa_m3"], volume * chamber["pressure_pa"], delta=1e-7)

    def test_two_sources_are_isolated_and_share_only_host_clock(self):
        self.start()
        self.host.create_instance("other")
        self.connect(instance="other", chamber="other_fixture", initial=80000)
        self.ok("vacuum.set_gas_ballast", enabled=True)
        self.host.step(20)
        self.assertEqual(self.value("vacuum.pressure_pa", instance="other", truth=True), 80000)
        self.assertFalse(self.value("vacuum.gas_ballast_enabled", instance="other"))
        self.assertEqual(self.value("vacuum.removed_gas_pa_m3", instance="other", truth=True), 0)
        self.assertLess(self.value("vacuum.pressure_pa", truth=True), 80000)
        self.assertEqual(self.agent.read(instance_id="other")["clock"], self.agent.read(instance_id="source")["clock"])

    def test_shared_chamber_and_running_reconnection_are_rejected_atomically(self):
        self.start()
        self.host.create_instance("other")
        self.host.create_chamber("other_fixture")
        before = self.host.debug_snapshot()
        for instance, chamber in (("other", "fixture"), ("source", "other_fixture")):
            with self.assertRaises(ValueError):
                self.host.connect_source(instance, chamber)
            self.assertEqual(before, self.host.debug_snapshot())

    def test_invalid_parameters_and_unknown_modes_do_not_mutate_state(self):
        invalid = [("vacuum.set_pressure", {"pressure_pa": value}) for value in (True, -1, 0, 199.99, 101326, math.nan, math.inf, "200")]
        invalid += [("vacuum.set_gas_ballast", {"enabled": 1}), ("vacuum.set_mode", {"mode": "vent"}), ("power.set", {"enabled": True, "role": "evaluator"}), ("vacuum.start", {"dt_s": 3})]
        for cap, args in invalid:
            before = self.host.debug_snapshot()
            response = self.invoke(cap, **args)
            self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT", (cap, args))
            self.assertEqual(response["error"]["side_effects"], "none")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_host_validation_is_atomic_and_step_zero_is_pure(self):
        self.connect()
        for dt in (-1, math.nan, math.inf, True, 3601, "1"):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.step(dt)
            self.assertEqual(before, self.host.debug_snapshot())
        before = self.host.debug_snapshot()
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        for kwargs in ({"volume_m3": 0}, {"initial_pressure_pa": True}, {"initial_pressure_pa": 101326}, {"leak_throughput_pa_m3_s": -1}, {"evidence": ""}):
            with self.assertRaises(ValueError):
                self.host.create_chamber("bad", **kwargs)
            self.assertEqual(before, self.host.debug_snapshot())
        for pressure in (110001, -1, math.nan, True):
            with self.assertRaises(ValueError):
                self.host.set_exhaust_pressure("source", pressure)
            self.assertEqual(before, self.host.debug_snapshot())
        self.host.set_exhaust_pressure("source", 110000)

    def test_agent_truth_filter_applies_to_describe_read_write_results_and_logs(self):
        self.start()
        self.host.step(3)
        private = {s for s, info in STATE_SLOTS.items() if info["visibility"] != "agent"}
        description = self.agent.describe(instance_id="source")
        reading = self.agent.read(instance_id="source")
        for sid in private:
            self.assertNotIn(sid, json.dumps(description))
            self.assertNotIn(sid, reading["observations"])
            for response in (self.agent.read(instance_id="source", state_ids=[sid]), self.agent.write(instance_id="source", state_id=sid, value=7, request_id="private")):
                self.assertEqual(response["error"]["code"], "ACCESS_DENIED")
                self.assertEqual(response["observations"], {})
                self.assertNotIn(sid, json.dumps(response))
            self.assertNotIn(sid, json.dumps(self.agent.logs()))
        self.assertEqual(set(self.judge.read(instance_id="source")["observations"]), set(STATE_SLOTS))
        self.assertNotIn("sim.step", description["capabilities"])
        self.assertEqual(VacuumHost.describe_host()["sim.step"]["visibility"], "trusted_host_only")

    def test_identity_and_instance_access_are_host_bound(self):
        self.host.create_instance("restricted")
        self.host.register_principal("scoped", instance_ids=["source"])
        scoped = self.host.session("scoped")
        for response in (scoped.read(instance_id="restricted"), scoped.describe(instance_id="restricted"), scoped.invoke(instance_id="restricted", capability_id="power.set", arguments={"enabled": True}, request_id="r")):
            self.assertEqual(response["error"]["code"], "UNKNOWN_INSTANCE")
        with self.assertRaises(ValueError):
            self.host.session("self_claimed_evaluator")
        with self.assertRaises(ValueError):
            self.host.register_principal("learner", visibility="evaluator")

    def test_request_replay_conflict_and_returned_objects_are_isolated(self):
        args = dict(instance_id="source", capability_id="power.set", arguments={"enabled": True}, request_id="same")
        response = self.agent.invoke(**args)
        self.host.step(1)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**args), response)
        response["result"]["enabled"] = False
        self.assertTrue(self.agent.invoke(**args)["result"]["enabled"])
        conflict = self.agent.invoke(**{**args, "arguments": {"enabled": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())
        logs = self.agent.logs()
        logs.clear()
        self.assertTrue(self.agent.logs())

    def test_write_routes_to_capability_and_rejects_read_only_slots(self):
        response = self.agent.write(instance_id="source", state_id="vacuum.target_pressure_pa", value=4000, request_id="write")
        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(self.value("vacuum.target_pressure_pa"), 4000)
        before = self.host.debug_snapshot()
        response = self.agent.write(instance_id="source", state_id="gas_ballast.angle_rad", value=.3, request_id="bad_write")
        self.assertEqual(response["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_scene_binding_updates_gas_ballast_exactly_once_without_time_advance(self):
        self.assertEqual(set(self.calls[0][1]), set(STATE_SLOTS))
        self.ok("vacuum.set_gas_ballast", enabled=True)
        self.assertEqual(self.calls[-1][1]["gas_ballast.angle_rad"], math.pi / 2)
        self.assertEqual(self.value("gas_ballast.angle_rad"), math.pi / 2)
        self.assertEqual(self.host.time_s, 0)
        self.ok("vacuum.set_gas_ballast", enabled=False)
        self.assertEqual(self.calls[-1][1]["gas_ballast.angle_rad"], 0)
        self.host.scene_adapter = None
        self.ok("vacuum.set_gas_ballast", enabled=True)
        self.assertEqual(self.value("gas_ballast.angle_rad"), math.pi / 2)

    def test_failed_scene_command_reports_unknown_external_effect_and_keeps_process(self):
        def broken(instance, updates):
            raise RuntimeError("private_backend_marker")
        self.host.scene_adapter = broken
        before = self.host.debug_snapshot()
        response = self.invoke("vacuum.set_gas_ballast", enabled=True)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("private_backend_marker", json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())

    def test_scene_step_failure_retains_completed_process_and_common_clock(self):
        self.start()
        self.host.scene_adapter = lambda instance, updates: (_ for _ in ()).throw(RuntimeError("private_backend_marker"))
        with self.assertRaises(VacuumSceneSynchronizationError):
            self.host.step(10)
        self.assertEqual(self.host.time_s, 10)
        self.assertLess(self.value("vacuum.pressure_pa", truth=True), 101325)
        self.assertTrue(self.host.debug_snapshot()["scene_faults"])
        self.assertNotIn("private_backend_marker", json.dumps(self.agent.logs()))

    def test_declared_water_control_is_unsupported_and_condensate_stays_null(self):
        self.assertEqual(self.agent.describe(instance_id="source")["capabilities"]["cooling.set_flow_rate"]["availability"], "declared")
        before = self.host.debug_snapshot()
        response = self.invoke("cooling.set_flow_rate", flow_m3_s=1e-6)
        self.assertEqual(response["status"], "unsupported")
        self.assertEqual(response["error"]["code"], "UNSUPPORTED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.start()
        self.host.step(50)
        self.assertIsNone(self.value("recovery.condensate_volume_m3"))

    def test_real_results_and_observations_conform_to_exported_schemas(self):
        self.connect()
        metadata = export_contract_metadata()["asset_types"][ASSET_TYPE_ID]
        for cap, info in metadata["capabilities"].items():
            self.assertEqual(lint_schema(info["input_schema"]), [], cap)
            self.assertEqual(lint_schema(info["output_schema"]), [], cap)
            args = info["example_arguments"][0]
            self.assertEqual(validate_value(args, info["input_schema"]), [], cap)
            response = self.invoke(cap, **args)
            if info["availability"] == "declared":
                self.assertEqual(response["status"], "unsupported")
            else:
                self.assertEqual(response["status"], "succeeded", cap)
                self.assertEqual(validate_value(response["result"], info["output_schema"]), [], cap)
            self.assertEqual(set(response), {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"})
            self.assertEqual(response["observations"], {})
        self.host.step(1)
        for sid, observation in self.judge.read(instance_id="source")["observations"].items():
            self.assertEqual(validate_value(observation["value"], metadata["state_slots"][sid]["value_schema"]), [], sid)
            self.assertEqual(observation["unit"], metadata["state_slots"][sid]["unit"])
            self.assertEqual(observation["sample_time_s"], 1)
            if sid == "vacuum.removed_gas_pa_m3":
                self.assertEqual(observation["sample_window_s"], [0, 1])

    def test_integral_window_starts_at_later_instance_creation_and_reconnect_preserves_total(self):
        self.host.step(20)
        self.host.create_instance("later")
        self.start(instance="later", chamber="later_fixture")
        self.host.step(10)
        result = self.judge.read(instance_id="later", state_ids=["vacuum.removed_gas_pa_m3"])
        observation = result["observations"]["vacuum.removed_gas_pa_m3"]
        self.assertEqual(observation["sample_window_s"], [20, 30])
        self.assertGreater(observation["value"], 0)
        self.ok("vacuum.stop", instance="later")
        self.host.create_chamber("replacement")
        self.host.connect_source("later", "replacement")
        after = self.judge.read(instance_id="later", state_ids=["vacuum.removed_gas_pa_m3"])["observations"]["vacuum.removed_gas_pa_m3"]
        self.assertEqual(after, observation)


if __name__ == "__main__":
    unittest.main()

# CHEM-024 tests appended independently of the original CHEM-188 suite.
# Use unittest discovery or validate_rv10_runtime(); the unchanged earlier main
# guard intentionally retains the original direct-script invocation behavior.
from basic40_runtime_vacuum import (RV10_ASSET_TYPE_ID, RV10_MINIMUM_SPEED_RAD_S,
    RV10_MAXIMUM_SPEED_RAD_S, RV10_STATE_SLOTS, RV10_CAPABILITY_ARGUMENTS,
    RV10Host, RV10SceneSynchronizationError, export_rv10_contract_metadata)


class Basic40RV10Tests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = RV10Host(scene_adapter=lambda instance, updates: self.calls.append((instance, copy.deepcopy(updates))))
        self.host.create_instance("rv10")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.sequence = 0

    def invoke(self, cap, *, instance="rv10", **args):
        self.sequence += 1
        return self.agent.invoke(instance_id=instance, capability_id=cap, arguments=args, request_id=f"rv10_request_{self.sequence}")

    def ok(self, cap, *, instance="rv10", **args):
        result = self.invoke(cap, instance=instance, **args)
        self.assertEqual(result["status"], "succeeded", result)
        return result

    def value(self, sid, *, instance="rv10", truth=False):
        session = self.judge if truth else self.agent
        return session.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def configure_bath(self, *, instance="rv10", capacity=1000.0, initial=293.15, loss=0.0):
        self.host.configure_bath(instance, effective_heat_capacity_j_k=capacity, initial_temperature_k=initial,
            heat_loss_w_k=loss, evidence="engineering_assumption: explicit synthetic fixture, not measured bath or fluid")

    def start_all(self, *, instance="rv10"):
        self.configure_bath(instance=instance)
        self.ok("power.set", enabled=True, instance=instance)
        self.ok("lift.set_position", position_m=.14, instance=instance)
        self.ok("rotation.set_speed", speed_rad_s=10.0, instance=instance)
        self.ok("rotation.start", instance=instance)
        self.ok("thermal.set_temperature", temperature_k=453.15, instance=instance)
        self.ok("thermal.start", instance=instance)

    def test_discovery_read_purity_and_missing_parameters_are_null(self):
        before = self.host.debug_snapshot()
        for _ in range(3):
            description = self.agent.describe(instance_id="rv10")
            self.assertEqual(description["asset_type_id"], RV10_ASSET_TYPE_ID)
            readings = self.agent.read(instance_id="rv10")
            self.judge.read(instance_id="rv10")
        self.assertEqual(before, self.host.debug_snapshot())
        for sid in ("thermal.indicated_temperature_k", "vacuum.indicated_pressure_pa", "recovery.indicated_condensate_mass_kg", "recovery.condensate_volume_m3"):
            self.assertIsNone(readings["observations"][sid]["value"])
            self.assertEqual(readings["observations"][sid]["quality"], "unavailable")
            self.assertTrue(readings["observations"][sid]["reason"])
        self.assertNotIn("sim.step", description["capabilities"])
        self.assertEqual(self.invoke("sim.step", dt_s=1)["error"]["code"], "UNKNOWN_CAPABILITY")
        self.assertEqual(self.host.time_s, 0)

    def test_preconditions_do_not_mutate_process_and_power_is_not_time(self):
        for cap, args in (("rotation.start", {}), ("thermal.start", {}), ("lift.set_position", {"position_m": .1})):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], "PRECONDITION_FAILED")
            self.assertEqual(before, self.host.debug_snapshot())
        self.ok("power.set", enabled=True)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("thermal.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.host.time_s, 0)

    def test_range_rejection_is_atomic_for_si_units_and_types(self):
        self.start_all()
        invalid = [("lift.set_position", {"position_m": -.0001}), ("lift.set_position", {"position_m": .14001}),
            ("rotation.set_speed", {"speed_rad_s": 0}), ("rotation.set_speed", {"speed_rad_s": RV10_MINIMUM_SPEED_RAD_S - 1e-8}),
            ("rotation.set_speed", {"speed_rad_s": RV10_MAXIMUM_SPEED_RAD_S + 1e-8}), ("thermal.set_temperature", {"temperature_k": 453.1501}),
            ("thermal.set_temperature", {"temperature_k": 293.14}), ("lift.set_position", {"position_m": True}), ("power.set", {"enabled": 1}),
            ("lift.set_position", {"position_m": float("nan")}), ("rotation.set_speed", {"speed_rad_s": float("inf")}),
            ("thermal.set_temperature", {"temperature_k": "333.15"}), ("rotation.stop", {"extra": 0}), ("power.set", {})]
        for cap, args in invalid:
            with self.subTest(cap=cap, args=args):
                before, count = self.host.debug_snapshot(), len(self.calls)
                response = self.invoke(cap, **args)
                self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(response["error"]["side_effects"], "none")
                self.assertEqual(before, self.host.debug_snapshot())
                self.assertEqual(count, len(self.calls))

    def test_lift_reaches_both_bounds_and_only_host_step_moves_it(self):
        self.ok("power.set", enabled=True)
        self.ok("lift.set_position", position_m=.14)
        self.assertEqual(self.value("lift.position_m", truth=True), 0)
        self.assertTrue(self.value("lift.moving"))
        self.host.step(2)
        self.assertAlmostEqual(self.value("lift.position_m", truth=True), .04)
        self.host.step(100)
        self.assertEqual(self.value("lift.position_m", truth=True), .14)
        self.assertFalse(self.value("lift.moving"))
        self.ok("lift.set_position", position_m=0)
        self.host.step(1)
        self.assertAlmostEqual(self.value("lift.position_m", truth=True), .12)
        self.host.step(10)
        self.assertEqual(self.value("lift.position_m", truth=True), 0)
        self.assertEqual(self.value("rotation.angle_rad", truth=True), 0)
        self.assertIsNone(self.value("thermal.temperature_k", truth=True))

    def test_rotation_rpm_conversion_continuous_angle_and_stop(self):
        self.assertAlmostEqual(RV10_MINIMUM_SPEED_RAD_S, 5 * 2 * math.pi / 60)
        self.assertAlmostEqual(RV10_MAXIMUM_SPEED_RAD_S, 280 * 2 * math.pi / 60)
        self.ok("power.set", enabled=True)
        self.ok("rotation.set_speed", speed_rad_s=RV10_MAXIMUM_SPEED_RAD_S)
        self.ok("rotation.start")
        self.assertEqual(self.value("rotation.angle_rad", truth=True), 0)
        self.host.step(60)
        self.assertAlmostEqual(self.value("rotation.angle_rad", truth=True), 280 * 2 * math.pi)
        self.ok("rotation.set_speed", speed_rad_s=RV10_MINIMUM_SPEED_RAD_S)
        self.host.step(60)
        self.assertAlmostEqual(self.value("rotation.angle_rad", truth=True), 285 * 2 * math.pi)
        self.ok("rotation.stop")
        angle = self.value("rotation.angle_rad", truth=True)
        self.host.step(10)
        self.assertEqual(self.value("rotation.angle_rad", truth=True), angle)
        self.assertEqual(self.value("rotation.speed_rad_s", truth=True), 0)
        self.assertEqual(self.value("lift.position_m", truth=True), 0)

    def test_three_independent_processes_advance_together_and_stop_separately(self):
        self.start_all()
        before = self.host.debug_snapshot()["instances"]["rv10"]["state"]
        self.assertEqual(before["lift.position_m"], 0)
        self.assertEqual(before["rotation.angle_rad"], 0)
        self.assertEqual(before["thermal.temperature_k"], 293.15)
        self.host.step(2)
        self.assertAlmostEqual(self.value("lift.position_m", truth=True), .04)
        self.assertAlmostEqual(self.value("rotation.angle_rad", truth=True), 20)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), 293.15 + 1350 * 2 / 1000)
        self.ok("rotation.stop")
        self.host.step(2)
        self.assertAlmostEqual(self.value("lift.position_m", truth=True), .08)
        self.assertAlmostEqual(self.value("rotation.angle_rad", truth=True), 20)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), 293.15 + 1350 * 4 / 1000)
        self.ok("thermal.stop")
        self.host.step(2)
        self.assertAlmostEqual(self.value("lift.position_m", truth=True), .12)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), 293.15 + 1350 * 4 / 1000)

    def test_heater_bounded_energy_and_setpoint_does_not_heat_immediately(self):
        self.configure_bath(capacity=2000)
        self.ok("power.set", enabled=True)
        initial = self.value("thermal.temperature_k", truth=True)
        self.ok("thermal.set_temperature", temperature_k=453.15)
        self.ok("thermal.start")
        self.assertEqual(self.value("thermal.temperature_k", truth=True), initial)
        self.assertEqual(self.value("thermal.heating_power_w", truth=True), 1350)
        self.host.step(10)
        final = self.value("thermal.temperature_k", truth=True)
        self.assertAlmostEqual(2000 * (final - initial), 1350 * 10, delta=1e-8)
        self.host.step(3600)
        self.assertEqual(self.value("thermal.temperature_k", truth=True), 453.15)
        self.assertEqual(self.value("thermal.heating_power_w", truth=True), 0)
        self.assertEqual(self.value("lift.position_m", truth=True), 0)
        self.assertEqual(self.value("rotation.angle_rad", truth=True), 0)

    def test_heat_loss_matches_closed_form_and_cooling_has_no_active_power(self):
        self.configure_bath(capacity=1000, loss=10)
        self.ok("power.set", enabled=True)
        self.ok("thermal.set_temperature", temperature_k=453.15)
        self.ok("thermal.start")
        self.host.step(10)
        expected = 293.15 + 135 * (1 - math.exp(-.1))
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), expected)
        self.ok("thermal.stop")
        self.host.step(20)
        expected = 293.15 + (expected - 293.15) * math.exp(-.2)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), expected)
        self.assertEqual(self.value("thermal.heating_power_w", truth=True), 0)

    def test_ideal_thermostat_holds_target_and_cools_from_above(self):
        self.configure_bath(capacity=1000, initial=350, loss=10)
        self.ok("power.set", enabled=True)
        self.ok("thermal.set_temperature", temperature_k=333.15)
        self.ok("thermal.start")
        self.assertEqual(self.value("thermal.heating_power_w", truth=True), 0)
        self.host.step(1000)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), 333.15)
        self.assertAlmostEqual(self.value("thermal.heating_power_w", truth=True), 400)
        self.host.step(1000)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), 333.15)

    def test_host_configured_ambient_is_enforced_without_catalog_minimum_guess(self):
        host = RV10Host(ambient_temperature_k=310)
        host.create_instance("warm")
        host.register_principal("p")
        session = host.session("p")
        before = host.debug_snapshot()
        response = session.invoke(instance_id="warm", capability_id="thermal.set_temperature", arguments={"temperature_k": 300}, request_id="cold")
        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(before, host.debug_snapshot())
        self.assertEqual(session.read(instance_id="warm", state_ids=["thermal.target_temperature_k"])["observations"]["thermal.target_temperature_k"]["value"], 310)

    def test_power_loss_retains_position_angle_mass_and_stops_active_processes(self):
        self.start_all()
        self.host.step(2)
        self.ok("power.set", enabled=False)
        before = self.host.debug_snapshot()["instances"]["rv10"]["state"]
        self.host.step(20)
        after = self.host.debug_snapshot()["instances"]["rv10"]["state"]
        for sid in ("lift.position_m", "rotation.angle_rad", "thermal.temperature_k"):
            self.assertEqual(before[sid], after[sid])
        for sid in ("rotation.running", "thermal.enabled", "lift.moving", "evaporation.running"):
            self.assertFalse(after[sid])
        self.assertEqual(after["rotation.speed_rad_s"], 0)
        self.assertEqual(after["thermal.heating_power_w"], 0)

    def test_external_source_reference_snapshot_staleness_and_no_internal_pump(self):
        self.host.connect_vacuum_source("rv10", "shared_source", pressure_pa=2500.25, evidence="engineering_assumption: externally supplied fixture boundary")
        self.assertEqual(set(self.host.debug_snapshot()["instances"]), {"rv10"})
        self.assertEqual(self.value("vacuum.source_asset_type_id"), ASSET_TYPE_ID)
        reading = self.agent.read(instance_id="rv10", state_ids=["vacuum.indicated_pressure_pa"])["observations"]["vacuum.indicated_pressure_pa"]
        self.assertEqual(reading["value"], 2500)
        self.assertEqual(reading["quality"], "estimated")
        self.host.step(10)
        stale = self.agent.read(instance_id="rv10", state_ids=["vacuum.indicated_pressure_pa"])["observations"]["vacuum.indicated_pressure_pa"]
        self.assertEqual(stale["quality"], "stale")
        self.assertEqual(stale["sample_time_s"], 0)
        self.host.set_external_pressure("rv10", 1000, evidence="engineering_assumption: refreshed external source")
        fresh = self.agent.read(instance_id="rv10", state_ids=["vacuum.indicated_pressure_pa"])["observations"]["vacuum.indicated_pressure_pa"]
        self.assertEqual(fresh["quality"], "estimated")
        self.assertEqual(fresh["sample_time_s"], 10)
        self.host.set_external_pressure("rv10", None, evidence="engineering_assumption: disconnected pressure observation")
        self.assertIsNone(self.value("vacuum.indicated_pressure_pa"))

    def test_evaporation_unavailable_without_parameters_and_external_vacuum(self):
        self.start_all()
        self.host.step(20)
        for sid in ("evaporation.remaining_mass_kg", "evaporation.evaporated_mass_kg", "recovery.condensate_mass_kg"):
            self.assertIsNone(self.value(sid, truth=True))
        self.assertFalse(self.value("evaporation.running"))
        self.ok("rotation.stop")
        self.ok("thermal.stop")
        self.host.configure_evaporation("rv10", charge_mass_kg=.1, evaporation_rate_kg_s=.002, condensation_efficiency=.8, evidence="engineering_assumption: explicit empirical fixture")
        self.ok("rotation.start")
        self.ok("thermal.start")
        self.host.step(10)
        self.assertEqual(self.value("evaporation.remaining_mass_kg", truth=True), .1)
        self.assertFalse(self.value("evaporation.running"))

    def test_empirical_evaporation_mass_conservation_depletion_and_stop(self):
        self.configure_bath()
        self.host.configure_evaporation("rv10", charge_mass_kg=.1, evaporation_rate_kg_s=.002, condensation_efficiency=.8, evidence="engineering_assumption: charge and rate are synthetic inputs")
        self.host.connect_vacuum_source("rv10", "shared_source", pressure_pa=5000, evidence="engineering_assumption: held external pressure")
        self.ok("power.set", enabled=True)
        self.ok("rotation.start")
        self.ok("thermal.set_temperature", temperature_k=333.15)
        self.ok("thermal.start")
        self.host.step(10)
        self.assertAlmostEqual(self.value("evaporation.remaining_mass_kg", truth=True), .08)
        self.assertAlmostEqual(self.value("evaporation.evaporated_mass_kg", truth=True), .02)
        self.assertAlmostEqual(self.value("recovery.condensate_mass_kg", truth=True), .016)
        self.assertAlmostEqual(self.value("evaporation.uncondensed_mass_kg", truth=True), .004)
        self.ok("rotation.stop")
        self.host.step(10)
        self.assertAlmostEqual(self.value("evaporation.remaining_mass_kg", truth=True), .08)
        self.ok("rotation.start")
        self.host.step(100)
        self.assertEqual(self.value("evaporation.remaining_mass_kg", truth=True), 0)
        self.assertFalse(self.value("evaporation.running"))
        collected = self.value("recovery.condensate_mass_kg", truth=True)
        uncondensed = self.value("evaporation.uncondensed_mass_kg", truth=True)
        self.assertAlmostEqual(collected + uncondensed, .1)
        self.assertAlmostEqual(self.value("evaporation.evaporated_mass_kg", truth=True), .1)
        self.assertIsNone(self.value("recovery.condensate_volume_m3"))
        observation = self.judge.read(instance_id="rv10", state_ids=["recovery.condensate_mass_kg"])["observations"]["recovery.condensate_mass_kg"]
        self.assertEqual(observation["sample_window_s"], [0, 120])

    def test_host_configuration_invalid_arguments_are_atomic(self):
        before = self.host.debug_snapshot()
        invalid = [lambda: self.host.configure_bath("rv10", effective_heat_capacity_j_k=0, initial_temperature_k=300, heat_loss_w_k=0, evidence="explicit"),
            lambda: self.host.configure_bath("rv10", effective_heat_capacity_j_k=1000, initial_temperature_k=500, heat_loss_w_k=0, evidence="explicit"),
            lambda: self.host.configure_bath("rv10", effective_heat_capacity_j_k=1000, initial_temperature_k=300, heat_loss_w_k=-1, evidence="explicit"),
            lambda: self.host.configure_bath("rv10", effective_heat_capacity_j_k=1000, initial_temperature_k=300, heat_loss_w_k=0, evidence=""),
            lambda: self.host.configure_evaporation("rv10", charge_mass_kg=.1, evaporation_rate_kg_s=.01, condensation_efficiency=1.1, evidence="explicit"),
            lambda: self.host.configure_evaporation("rv10", charge_mass_kg=True, evaporation_rate_kg_s=.01, condensation_efficiency=.5, evidence="explicit"),
            lambda: self.host.connect_vacuum_source("rv10", "other", source_asset_type_id=RV10_ASSET_TYPE_ID, pressure_pa=1, evidence="explicit"),
            lambda: self.host.connect_vacuum_source("rv10", "other", pressure_pa=-1, evidence="explicit"),
            lambda: self.host.set_external_pressure("rv10", 1000, evidence="explicit")]
        for call in invalid:
            with self.assertRaises(ValueError):
                call()
            self.assertEqual(before, self.host.debug_snapshot())

    def test_common_clock_dual_instance_and_principal_isolation(self):
        self.host.create_instance("other")
        self.host.register_principal("restricted", instance_ids=["rv10"])
        restricted = self.host.session("restricted")
        self.start_all()
        self.host.step(3)
        self.assertEqual(self.value("lift.position_m", instance="other", truth=True), 0)
        self.assertEqual(self.value("rotation.angle_rad", instance="other", truth=True), 0)
        self.assertIsNone(self.value("thermal.temperature_k", instance="other", truth=True))
        for result in (restricted.read(instance_id="other"), restricted.describe(instance_id="other"),
                restricted.invoke(instance_id="other", capability_id="power.set", arguments={"enabled": True}, request_id="cross"),
                restricted.write(instance_id="other", state_id="power.enabled", value=True, request_id="cross_write")):
            self.assertEqual(result["error"]["code"], "UNKNOWN_INSTANCE")
            self.assertIsNone(result["state_revision"])
        self.assertEqual(self.judge.read(instance_id="other")["clock"]["time_s"], 3)
        self.assertEqual(self.agent.read(instance_id="rv10")["clock"]["time_s"], 3)

    def test_process_truth_filtering_covers_describe_read_write_results_and_logs(self):
        self.start_all()
        self.host.step(3)
        truth_ids = {sid for sid, slot in RV10_STATE_SLOTS.items() if slot["visibility"] != "agent"}
        description = self.agent.describe(instance_id="rv10")
        self.assertTrue(truth_ids.isdisjoint(description["state_slots"]))
        for cap in description["capabilities"].values():
            self.assertTrue(truth_ids.isdisjoint(cap["observes"]))
            self.assertTrue(truth_ids.isdisjoint(e["state_id"] for e in cap["state_effects"]))
        self.assertTrue(truth_ids.isdisjoint(self.agent.read(instance_id="rv10")["observations"]))
        for sid in truth_ids:
            self.assertEqual(self.agent.read(instance_id="rv10", state_ids=[sid])["error"]["code"], "ACCESS_DENIED")
            self.assertEqual(self.agent.write(instance_id="rv10", state_id=sid, value=1, request_id="truth_" + sid)["error"]["code"], "ACCESS_DENIED")
        logs = self.agent.logs()
        for item in logs:
            self.assertEqual(item["response"]["observations"], {})
            self.assertEqual(set(item["response"]["result"]), set(RV10_CAPABILITY_ARGUMENTS[item["capability_id"]]) if item["capability_id"] not in ("rotation.start", "thermal.start") else {"running" if item["capability_id"] == "rotation.start" else "enabled"})
        self.assertFalse(hasattr(self.agent, "debug_snapshot"))
        self.assertFalse(hasattr(self.agent, "step"))
        with self.assertRaises(TypeError):
            self.agent.read(instance_id="rv10", visibility="evaluator")

    def test_write_routes_to_capability_and_retry_deduplication_is_per_principal(self):
        self.ok("power.set", enabled=True)
        result = self.agent.write(instance_id="rv10", state_id="lift.target_position_m", value=.1, request_id="write_1")
        before = self.host.debug_snapshot()
        again = self.agent.invoke(instance_id="rv10", capability_id="lift.set_position", arguments={"position_m": .1}, request_id="write_1")
        self.assertEqual(result, again)
        self.assertEqual(before, self.host.debug_snapshot())
        conflict = self.agent.invoke(instance_id="rv10", capability_id="lift.set_position", arguments={"position_m": .11}, request_id="write_1")
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())
        other = self.judge.invoke(instance_id="rv10", capability_id="lift.set_position", arguments={"position_m": .12}, request_id="write_1")
        self.assertEqual(other["status"], "succeeded")
        self.assertEqual(self.value("lift.target_position_m"), .12)
        self.assertEqual(self.value("lift.position_m", truth=True), 0)
        self.assertEqual(self.agent.write(instance_id="rv10", state_id="rotation.running", value=True, request_id="forbidden")["error"]["code"], "ACCESS_DENIED")

    def test_invalid_read_describe_and_unknown_targets_return_filtered_errors(self):
        for args in ({}, {"instance_id": "rv10", "asset_type_id": RV10_ASSET_TYPE_ID}, {"asset_type_id": "wrong"}):
            self.assertEqual(self.agent.describe(**args)["error"]["code"], "INVALID_ARGUMENT")
        for ids in (["power.enabled", "power.enabled"], "power.enabled", [3]):
            self.assertEqual(self.agent.read(instance_id="rv10", state_ids=ids)["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.agent.read(instance_id="rv10", state_ids=["unknown"])["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.invoke("unknown")["error"]["code"], "UNKNOWN_CAPABILITY")
        self.assertEqual(self.agent.read(instance_id="missing")["error"]["code"], "UNKNOWN_INSTANCE")

    def test_invalid_host_steps_are_atomic_and_zero_step_is_noop(self):
        self.start_all()
        for dt in (-1, 3600.1, float("nan"), float("inf"), True, "1"):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.step(dt)
            self.assertEqual(before, self.host.debug_snapshot())
        before, count = self.host.debug_snapshot(), len(self.calls)
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(count, len(self.calls))

    def test_scene_callbacks_receive_authoritative_motion_and_command_failures_are_atomic(self):
        self.start_all()
        self.host.step(2)
        updates = self.calls[-1][1]
        self.assertAlmostEqual(updates["lift.position_m"], .04)
        self.assertAlmostEqual(updates["rotation.angle_rad"], 20)
        self.assertAlmostEqual(updates["thermal.indicated_temperature_k"], 295.8)
        def broken(instance, updates):
            raise RuntimeError("private_rv10_scene_marker")
        self.host.scene_adapter = broken
        before = self.host.debug_snapshot()
        response = self.invoke("rotation.stop")
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertNotIn("private_rv10_scene_marker", json.dumps(response))
        self.assertNotIn("private_rv10_scene_marker", json.dumps(self.agent.logs()))

    def test_failed_scene_step_retains_process_time_and_other_instance_attempt(self):
        self.start_all()
        self.host.create_instance("other")
        seen = []
        def broken(instance, updates):
            seen.append(instance)
            if instance == "rv10":
                raise RuntimeError("private_rv10_scene_marker")
        self.host.scene_adapter = broken
        with self.assertRaises(RV10SceneSynchronizationError):
            self.host.step(2)
        self.assertEqual(self.host.time_s, 2)
        self.assertAlmostEqual(self.value("lift.position_m", truth=True), .04)
        self.assertEqual(seen, ["rv10", "other"])
        self.assertTrue(self.host.debug_snapshot()["scene_faults"])
        self.assertNotIn("private_rv10_scene_marker", json.dumps(self.agent.logs()))

    def test_all_real_results_observations_and_declared_capabilities_match_schemas(self):
        self.configure_bath()
        metadata = export_rv10_contract_metadata()["asset_types"][RV10_ASSET_TYPE_ID]
        for cap, info in metadata["capabilities"].items():
            self.assertEqual(lint_schema(info["input_schema"]), [], cap)
            self.assertEqual(lint_schema(info["output_schema"]), [], cap)
            args = info["example_arguments"][0]
            self.assertEqual(validate_value(args, info["input_schema"]), [], cap)
            before = self.host.debug_snapshot()
            result = self.invoke(cap, **args)
            if info["availability"] == "declared":
                self.assertEqual(result["status"], "unsupported", cap)
                self.assertEqual(before, self.host.debug_snapshot())
            else:
                self.assertEqual(result["status"], "succeeded", cap)
                self.assertEqual(validate_value(result["result"], info["output_schema"]), [], cap)
            self.assertEqual(set(result), {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"})
            self.assertEqual(result["observations"], {})
        self.host.step(1)
        for sid, observation in self.judge.read(instance_id="rv10")["observations"].items():
            self.assertEqual(validate_value(observation["value"], metadata["state_slots"][sid]["value_schema"]), [], sid)
            self.assertEqual(observation["unit"], metadata["state_slots"][sid]["unit"])

    def test_thermal_solver_remains_stable_at_negligible_loss_and_small_capacity(self):
        self.configure_bath(capacity=1000, loss=1e-320)
        self.ok("power.set", enabled=True)
        self.ok("thermal.set_temperature", temperature_k=453.15)
        self.ok("thermal.start")
        self.host.step(1)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), 294.5)
        self.ok("thermal.stop")
        self.configure_bath(capacity=1e-306, loss=10)
        self.ok("thermal.start")
        self.host.step(1)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), 428.15)


# CHEM-025 appended coverage; run via validate_lyovapor_runtime() or unittest discovery.
from basic40_runtime_vacuum import (LYOVAPOR_ASSET_TYPE_ID, LYOVAPOR_STATE_SLOTS,
    LYOVAPOR_ARGUMENTS, LYOVAPOR_MAX_RATE_KG_S, LyovaporHost,
    LyovaporSceneSynchronizationError, export_lyovapor_contract_metadata)


class Basic40LyovaporTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = LyovaporHost(scene_adapter=lambda i, s: self.calls.append((i, copy.deepcopy(s))))
        self.host.create_instance("freeze")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.lab, self.judge = self.host.session("learner"), self.host.session("judge")
        self.n = 0

    def invoke(self, cap, **args):
        self.n += 1
        return self.lab.invoke(instance_id="freeze", capability_id=cap, arguments=args, request_id=f"lyo_{self.n}")

    def ok(self, cap, **args):
        out = self.invoke(cap, **args)
        self.assertEqual(out["status"], "succeeded", out)
        self.assertEqual(out["result"], {"applied": True})
        self.assertEqual(out["observations"], {})
        return out

    def value(self, sid, *, private=False):
        return (self.judge if private else self.lab).read(instance_id="freeze", state_ids=[sid])["observations"][sid]["value"]

    def boundary(self, **overrides):
        values = dict(pressure_pa=50, shelf_temperature_k=253.15, sample_temperature_k=243.15, condenser_temperature_k=188.15, evidence="engineering_assumption: synthetic actual boundary")
        values.update(overrides)
        self.host.set_boundary("freeze", **values)

    def prepare(self, *, kinetics=True, compatible=True, frozen=True, ice=.0001, bound=.00005, dry=.001, recipe=None, boundary=True):
        self.ok("chamber.set_open", open=True)
        self.host.initialize_sample("freeze", dry_mass_kg=dry, ice_mass_kg=ice, bound_water_mass_kg=bound, pre_frozen=frozen, evidence="engineering_assumption: synthetic prefrozen vial")
        self.ok("chamber.set_open", open=False)
        self.host.connect_vacuum("freeze", source_instance_id="external_source", asset_type_id="external.synthetic_compatible_vacuum" if compatible else ASSET_TYPE_ID, minimum_pressure_pa=3 if compatible else 200, evidence="engineering_assumption: external boundary only; no source instantiated")
        if boundary:
            self.boundary(pressure_pa=50 if compatible else 200)
        if kinetics:
            self.host.configure_kinetics("freeze", sublimation_rate_kg_s=1e-5, desorption_rate_kg_s=5e-6, evidence="engineering_assumption: fixed synthetic test rates, not calibration")
        self.ok("power.set", enabled=True)
        self.ok("lyophilization.set_recipe", recipe=recipe or {"pre_freeze_s": 2, "primary_drying_s": 20, "secondary_drying_s": 20})

    def water(self):
        return sum(self.value(sid, private=True) for sid in ("sample.ice_mass_kg", "sample.bound_water_mass_kg", "condenser.ice_mass_kg", "condenser.drained_mass_kg"))

    def test_null_defaults_read_describe_are_pure(self):
        before = self.host.debug_snapshot()
        description = self.lab.describe(asset_type_id=LYOVAPOR_ASSET_TYPE_ID)
        self.assertEqual(description["catalog_id"], "CHEM-025")
        for _ in range(3):
            for sid in ("vacuum.indicated_pressure_pa", "thermal.indicated_sample_temperature_k", "sample.pre_frozen", "cycle.recipe"):
                obs = self.lab.read(instance_id="freeze", state_ids=[sid])["observations"][sid]
                self.assertIsNone(obs["value"])
                self.assertEqual(obs["quality"], "unavailable")
        self.assertIsNone(self.value("sample.ice_mass_kg", private=True))
        self.assertEqual(before, self.host.debug_snapshot())

    def test_all_settings_route_without_teleporting_actual_boundaries(self):
        self.prepare()
        original = self.value("thermal.shelf_temperature_k", private=True)
        for cap, args in (("vacuum.set_pressure", {"pressure_pa": 30}), ("thermal.set_temperature", {"temperature_k": 333.15}), ("thermal.set_protection_temperature", {"temperature_k": 300}), ("lyophilization.set_endpoint", {"mode": "model_moisture", "moisture_threshold": .01})):
            self.ok(cap, **args)
        self.assertEqual(self.value("thermal.shelf_temperature_k", private=True), original)
        self.assertEqual(self.value("vacuum.pressure_pa", private=True), 50)
        result = self.lab.write(instance_id="freeze", state_id="vacuum.target_pressure_pa", value=50, request_id="write_target")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.value("vacuum.target_pressure_pa"), 50)
        self.assertEqual(self.host.time_s, 0)

    def test_shared_pc3001_cannot_start_nominal_freeze_drying_or_fake_low_pressure(self):
        self.prepare(compatible=False)
        self.assertFalse(self.value("vacuum.compatible"))
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("lyophilization.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(ValueError):
            self.boundary(pressure_pa=50)
        with self.assertRaises(ValueError):
            self.host.connect_vacuum("freeze", source_instance_id="fake", asset_type_id=ASSET_TYPE_ID, minimum_pressure_pa=3, evidence="engineering_assumption")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertNotIn("chambers", self.host.debug_snapshot())

    def test_external_pre_freeze_confirmation_required(self):
        self.prepare(frozen=False)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("lyophilization.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_model_endpoint_and_phase_transitions_conserve_water(self):
        self.prepare()
        initial_water = self.water()
        self.ok("lyophilization.set_endpoint", mode="model_moisture", moisture_threshold=.01)
        self.ok("lyophilization.start")
        self.host.step(2)
        self.assertEqual(self.value("cycle.phase"), "primary_drying")
        self.assertEqual(self.value("condenser.ice_mass_kg", private=True), 0)
        self.host.step(20)
        self.assertEqual(self.value("cycle.phase"), "secondary_drying")
        self.assertAlmostEqual(self.value("sample.ice_mass_kg", private=True), 0)
        self.host.step(20)
        self.assertEqual(self.value("cycle.phase"), "complete")
        self.assertEqual(self.value("cycle.endpoint_status"), "model_threshold_met")
        self.assertFalse(self.value("cycle.running"))
        self.assertAlmostEqual(self.water(), initial_water, delta=1e-15)
        self.assertAlmostEqual(self.value("condenser.ice_mass_kg", private=True), initial_water)
        self.assertEqual(self.value("cycle.elapsed_s"), 42)

    def test_missing_kinetics_remains_unverified_and_masses_unchanged(self):
        self.prepare(kinetics=False)
        self.ok("lyophilization.set_endpoint", mode="model_moisture", moisture_threshold=.01)
        self.assertEqual(self.invoke("lyophilization.start")["status"], "rejected")
        self.ok("lyophilization.set_endpoint", mode="schedule_only", moisture_threshold=.01)
        initial_water = self.water()
        self.ok("lyophilization.start")
        self.host.step(100)
        self.assertEqual(self.value("cycle.endpoint_status"), "schedule_complete_unverified")
        self.assertEqual(self.value("condenser.ice_mass_kg", private=True), 0)
        self.assertEqual(self.water(), initial_water)

    def test_short_schedule_does_not_claim_dryness_or_desorb_with_free_ice(self):
        self.prepare(ice=.001, bound=.001, recipe={"pre_freeze_s": 1, "primary_drying_s": 1, "secondary_drying_s": 1})
        self.ok("lyophilization.set_endpoint", mode="model_moisture", moisture_threshold=.01)
        self.ok("lyophilization.start")
        self.host.step(3)
        self.assertEqual(self.value("cycle.endpoint_status"), "model_threshold_not_met")
        self.assertEqual(self.value("sample.bound_water_mass_kg", private=True), .001)
        self.assertAlmostEqual(self.value("sample.ice_mass_kg", private=True), .00099)

    def test_pause_resume_stop_abort_and_poweroff_preserve_partial_effects(self):
        self.prepare()
        self.ok("lyophilization.start")
        self.host.step(7)
        ice = self.value("sample.ice_mass_kg", private=True)
        self.ok("lyophilization.pause")
        self.host.step(100)
        self.assertEqual(self.value("sample.ice_mass_kg", private=True), ice)
        self.assertEqual(self.value("cycle.elapsed_s"), 7)
        self.ok("lyophilization.resume")
        self.host.step(1)
        self.assertLess(self.value("sample.ice_mass_kg", private=True), ice)
        for action in ("lyophilization.stop", "lyophilization.abort", "power.set"):
            self.ok(action, **({"enabled": False} if action == "power.set" else {}))
            retained = self.water()
            paused_ice = self.value("sample.ice_mass_kg", private=True)
            self.host.step(10)
            self.assertEqual(self.water(), retained)
            self.assertEqual(self.value("sample.ice_mass_kg", private=True), paused_ice)
            self.assertFalse(self.value("cycle.running"))
            if action != "power.set":
                self.ok("lyophilization.start")
        self.assertEqual(self.value("cycle.endpoint_status"), "aborted")

    def test_missing_boundary_and_pressure_temperature_gates_preserve_effective_time(self):
        self.prepare(boundary=False)
        self.ok("lyophilization.start")
        self.host.step(10)
        self.assertEqual(self.value("cycle.wait_reason"), "missing_boundary")
        self.assertEqual(self.value("cycle.elapsed_s"), 0)
        self.boundary(pressure_pa=500)
        self.host.step(10)
        self.assertEqual(self.value("cycle.wait_reason"), "pressure")
        self.assertEqual(self.value("cycle.elapsed_s"), 2)
        self.boundary(sample_temperature_k=290)
        self.host.step(10)
        self.assertEqual(self.value("cycle.wait_reason"), "temperature")
        self.assertEqual(self.value("cycle.elapsed_s"), 2)
        self.boundary()
        self.host.step(1)
        self.assertEqual(self.value("cycle.elapsed_s"), 3)

    def test_condenser_capacity_and_rate_upper_bound(self):
        self.prepare(ice=6, bound=0, dry=1, recipe={"pre_freeze_s": 0, "primary_drying_s": 604800, "secondary_drying_s": 1})
        self.host.configure_kinetics("freeze", sublimation_rate_kg_s=LYOVAPOR_MAX_RATE_KG_S, desorption_rate_kg_s=0, evidence="engineering_assumption: catalog upper bound stress test")
        self.ok("lyophilization.start")
        self.host.step(86400)
        self.assertAlmostEqual(self.value("condenser.ice_mass_kg", private=True), 4)
        self.host.step(86400)
        self.assertEqual(self.value("condenser.ice_mass_kg", private=True), 5)
        self.assertEqual(self.value("cycle.wait_reason"), "condenser_full")
        self.assertAlmostEqual(self.value("cycle.elapsed_s"), 108000)
        self.assertAlmostEqual(self.value("sample.ice_mass_kg", private=True), 1)
        self.assertAlmostEqual(self.water(), 6)

    def test_partition_invariance_of_phases_and_mass(self):
        snapshots = []
        for steps in ([42], [1, 1, 3, 7, 11, 19]):
            self.setUp()
            self.prepare()
            self.ok("lyophilization.set_endpoint", mode="model_moisture", moisture_threshold=.01)
            self.ok("lyophilization.start")
            for dt in steps:
                self.host.step(dt)
            snapshots.append({sid: self.value(sid, private=True) for sid in ("cycle.phase", "cycle.elapsed_s", "cycle.endpoint_status", "sample.ice_mass_kg", "sample.bound_water_mass_kg", "condenser.ice_mass_kg")})
        for sid in snapshots[0]:
            if isinstance(snapshots[0][sid], float):
                self.assertAlmostEqual(snapshots[0][sid], snapshots[1][sid], delta=1e-12)
            else:
                self.assertEqual(snapshots[0][sid], snapshots[1][sid])

    def test_manual_lid_and_unloading_need_safe_pressure_and_idle(self):
        self.prepare()
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("chamber.set_open", open=True)["status"], "rejected")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("lyophilization.start")
        self.assertEqual(self.invoke("chamber.set_open", open=True)["error"]["code"], "BUSY")
        self.ok("lyophilization.abort")
        self.host.disconnect_vacuum("freeze")
        self.assertEqual(self.value("vacuum.pressure_pa", private=True), 50)
        self.assertEqual(self.invoke("chamber.set_open", open=True)["status"], "rejected")
        self.boundary(pressure_pa=101325)
        self.ok("chamber.set_open", open=True)
        self.assertEqual(self.value("lid.lift_m"), .16)
        returned = self.host.unload_sample("freeze")
        self.assertEqual(returned["sample.ice_mass_kg"], .0001)
        self.assertIsNone(self.value("sample.ice_mass_kg", private=True))

    def test_defrost_preserves_collected_water_and_requires_external_warmth(self):
        self.prepare()
        self.ok("lyophilization.start")
        self.host.step(42)
        collected = self.value("condenser.ice_mass_kg", private=True)
        self.assertEqual(self.invoke("condenser.defrost")["status"], "rejected")
        self.boundary(pressure_pa=101325)
        self.ok("chamber.set_open", open=True)
        self.host.unload_sample("freeze")
        self.host.disconnect_vacuum("freeze")
        self.assertEqual(self.invoke("condenser.defrost")["status"], "rejected")
        self.boundary(pressure_pa=101325, condenser_temperature_k=280)
        self.ok("condenser.defrost")
        self.assertEqual(self.value("condenser.ice_mass_kg", private=True), 0)
        self.assertEqual(self.value("condenser.drained_mass_kg", private=True), collected)

    def test_invalid_arguments_rejected_atomically_including_exclusive_kelvin(self):
        cases = [("vacuum.set_pressure", {"pressure_pa": value}) for value in (0, 9.99, 100.01, True, math.nan, math.inf, "50")]
        cases += [(cap, {"temperature_k": value}) for cap in ("thermal.set_temperature", "thermal.set_protection_temperature") for value in (0, -1, 333.16, True)]
        cases += [("power.set", {"enabled": 1}), ("lyophilization.set_recipe", {"recipe": {"pre_freeze_s": 1}}), ("lyophilization.set_endpoint", {"mode": "sensor", "moisture_threshold": .1}), ("lyophilization.start", {"evaluator": True})]
        for cap, args in cases:
            before = self.host.debug_snapshot()
            result = self.invoke(cap, **args)
            self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT", (cap, args))
            self.assertEqual(result["error"]["side_effects"], "none")
            self.assertEqual(before, self.host.debug_snapshot())
        self.ok("thermal.set_temperature", temperature_k=180)

    def test_invalid_host_mutations_and_zero_step_are_atomic(self):
        self.prepare()
        for dt in (-1, 86401, True, math.nan, math.inf, "1"):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.step(dt)
            self.assertEqual(before, self.host.debug_snapshot())
        before = self.host.debug_snapshot()
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        for values in ({"condenser_temperature_k": 188}, {"shelf_temperature_k": 333.16}, {"sample_temperature_k": True}, {"pressure_pa": 2}, {"evidence": ""}):
            with self.assertRaises(ValueError):
                self.boundary(**values)
            self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(ValueError):
            self.host.configure_kinetics("freeze", sublimation_rate_kg_s=LYOVAPOR_MAX_RATE_KG_S, desorption_rate_kg_s=1e-5, evidence="engineering_assumption")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_instances_are_isolated_and_share_host_time(self):
        self.prepare()
        self.host.create_instance("other")
        self.ok("lyophilization.start")
        self.host.step(10)
        reading = self.lab.read(instance_id="other")
        self.assertEqual(reading["clock"]["time_s"], 10)
        self.assertEqual(reading["observations"]["cycle.phase"]["value"], "idle")
        self.assertIsNone(reading["observations"]["sample.pre_frozen"]["value"])
        self.assertFalse(reading["observations"]["power.enabled"]["value"])

    def test_truth_filtered_from_describe_results_errors_logs_and_scene_callback(self):
        self.prepare()
        self.ok("lyophilization.start")
        self.host.step(5)
        private_ids = {sid for sid, slot in LYOVAPOR_STATE_SLOTS.items() if slot["visibility"] != "agent"}
        public = self.lab.describe(instance_id="freeze")
        for sid in private_ids:
            self.assertNotIn(sid, json.dumps(public))
            self.assertNotIn(sid, self.lab.read(instance_id="freeze")["observations"])
            for response in (self.lab.read(instance_id="freeze", state_ids=[sid]), self.lab.write(instance_id="freeze", state_id=sid, value=99, request_id="forbidden")):
                self.assertEqual(response["error"]["code"], "ACCESS_DENIED")
                self.assertNotIn(sid, json.dumps(response))
            self.assertNotIn(sid, json.dumps(self.lab.logs()))
            for _, call in self.calls:
                self.assertNotIn(sid, call)
        self.assertEqual(set(self.judge.read(instance_id="freeze")["observations"]), set(LYOVAPOR_STATE_SLOTS))
        self.assertNotIn("sim.step", public["capabilities"])

    def test_host_identity_grants_cannot_be_elevated_by_request(self):
        self.host.create_instance("restricted")
        self.host.register_principal("scoped", instance_ids=["freeze"])
        scoped = self.host.session("scoped")
        for response in (scoped.read(instance_id="restricted"), scoped.describe(instance_id="restricted"), scoped.invoke(instance_id="restricted", capability_id="power.set", arguments={"enabled": True}, request_id="try")):
            self.assertEqual(response["error"]["code"], "UNKNOWN_INSTANCE")
        with self.assertRaises(ValueError):
            self.host.register_principal("learner", visibility="evaluator")
        with self.assertRaises(ValueError):
            self.host.session("self_promoted")

    def test_request_replay_conflict_and_mutation_isolation(self):
        args = dict(instance_id="freeze", capability_id="power.set", arguments={"enabled": True}, request_id="replay")
        original = self.lab.invoke(**args)
        self.host.step(3)
        before = self.host.debug_snapshot()
        self.assertEqual(self.lab.invoke(**args), original)
        original["result"]["applied"] = False
        self.assertTrue(self.lab.invoke(**args)["result"]["applied"])
        conflict = self.lab.invoke(**{**args, "arguments": {"enabled": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())
        self.lab.logs().clear()
        self.assertTrue(self.lab.logs())

    def test_command_scene_failure_rolls_back_process_but_step_failure_commits_clock(self):
        def broken(i, states):
            raise RuntimeError("private_backend_error")
        self.host.scene_adapter = broken
        before = self.host.debug_snapshot()
        response = self.invoke("chamber.set_open", open=True)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("private_backend_error", json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.scene_adapter = None
        self.prepare()
        self.ok("lyophilization.start")
        self.host.scene_adapter = broken
        with self.assertRaises(LyovaporSceneSynchronizationError):
            self.host.step(7)
        self.assertEqual(self.host.time_s, 7)
        self.assertGreater(self.value("condenser.ice_mass_kg", private=True), 0)
        self.assertTrue(self.host.debug_snapshot()["scene_faults"])

    def test_exported_schemas_match_real_values_examples_and_reply_shapes(self):
        self.prepare()
        spec = export_lyovapor_contract_metadata()["asset_types"][LYOVAPOR_ASSET_TYPE_ID]
        self.assertEqual(set(spec["capabilities"]), set(LYOVAPOR_ARGUMENTS))
        for cap, info in spec["capabilities"].items():
            self.assertEqual(lint_schema(info["input_schema"]), [], cap)
            self.assertEqual(lint_schema(info["output_schema"]), [], cap)
            self.assertEqual(validate_value(info["example_arguments"][0], info["input_schema"]), [], cap)
        response = self.ok("lyophilization.start")
        self.assertEqual(set(response), {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"})
        self.assertEqual(validate_value(response["result"], spec["capabilities"]["lyophilization.start"]["output_schema"]), [])
        self.host.step(3)
        for sid, item in self.judge.read(instance_id="freeze")["observations"].items():
            self.assertEqual(validate_value(item["value"], spec["state_slots"][sid]["value_schema"]), [], sid)
            self.assertEqual(item["unit"], spec["state_slots"][sid]["unit"])
            self.assertEqual(item["sample_time_s"], 3)


# CHEM-190 tests use externally supplied synthetic boundaries, never real hardware.
from basic40_runtime_vacuum import (PURELAB_ASSET_TYPE_ID, PURELAB_STATE_SLOTS,
    PURELAB_ARGUMENTS, PURELAB_MAX_DISPENSE_M3_S, PURELAB_MAX_RO_M3_S,
    PurelabHost, PurelabSceneSynchronizationError, export_purelab_contract_metadata)


class Basic40PurelabTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = PurelabHost(scene_adapter=lambda i, s: self.calls.append((i, copy.deepcopy(s))))
        self.host.create_instance("pure")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.lab, self.judge = self.host.session("learner"), self.host.session("judge")
        self.seq = 0

    def invoke(self, cap, *, instance="pure", **args):
        self.seq += 1
        return self.lab.invoke(instance_id=instance, capability_id=cap, arguments=args, request_id=f"pure_{self.seq}")

    def ok(self, cap, *, instance="pure", **args):
        response = self.invoke(cap, instance=instance, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, sid, private=False, instance="pure"):
        return (self.judge if private else self.lab).read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def configure(self, *, feed=.02, buffer=.004, capacity=.006, qualified=True, drain=True, ro=PURELAB_MAX_RO_M3_S, recovery=.5, recirc=1e-5, flush=2e-5):
        self.host.configure_supply("pure", feed_volume_m3=feed, purified_volume_m3=buffer, buffer_capacity_m3=capacity, qualified_feed=qualified, drain_connected=drain, ro_product_rate_m3_s=ro, ro_recovery_fraction=recovery, recirculation_flow_m3_s=recirc, flush_flow_m3_s=flush, evidence="engineering_assumption: finite external boundary and supplied rates, not internal device parameters")

    def prepare(self, *, target=.001, receiver_capacity=.02, **kwargs):
        self.configure(**kwargs)
        self.host.connect_receiver("pure", receiver_id="beaker", volume_m3=0.0, capacity_m3=receiver_capacity, evidence="engineering_assumption: external receiver")
        self.host.install_consumables("pure", evidence="synthetic host installation confirmation")
        self.ok("power.set", enabled=True)
        self.ok("water.set_volume", volume_m3=target)
        self.ok("water.set_flow_rate", flow_m3_s=PURELAB_MAX_DISPENSE_M3_S)

    def balance(self):
        return sum(self.value(sid, True) or 0.0 for sid in ("water.feed_volume_m3", "water.buffer_volume_m3", "water.receiver_volume_m3", "water.waste_volume_m3"))

    def test_defaults_are_unknown_and_read_does_not_advance_or_mutate(self):
        before = self.host.debug_snapshot()
        for _ in range(4):
            reading = self.lab.read(instance_id="pure")
            self.lab.describe(instance_id="pure")
            self.lab.describe(asset_type_id=PURELAB_ASSET_TYPE_ID)
        self.assertEqual(before, self.host.debug_snapshot())
        for sid in ("water.resistivity_ohm_m", "water.temperature_k", "water.toc_mass_fraction", "consumables.remaining_life_fraction", "water.target_volume_m3", "water.flow_rate_m3_s", "water.qualified_feed"):
            self.assertIsNone(reading["observations"][sid]["value"])
            self.assertEqual(reading["observations"][sid]["quality"], "unavailable")
            self.assertTrue(reading["observations"][sid]["reason"])
        self.assertNotIn("sim.step", self.lab.describe(instance_id="pure")["capabilities"])

    def test_missing_preconditions_reject_atomically(self):
        for cap, args in (("water.start", {}), ("water.set_mode", {"mode": "recirculate"}), ("water.set_mode", {"mode": "flush"}), ("consumables.reset", {})):
            before = self.host.debug_snapshot()
            response = self.invoke(cap, **args)
            self.assertEqual(response["error"]["code"], "PRECONDITION_FAILED")
            self.assertEqual(before, self.host.debug_snapshot())
        self.prepare(qualified=False)
        self.assertEqual(self.invoke("water.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.invoke("water.set_mode", mode="recirculate")["error"]["code"], "PRECONDITION_FAILED")

    def test_quantitative_dispense_independent_balance_and_catalog_rate(self):
        self.prepare()
        initial = self.balance()
        self.ok("water.start")
        self.assertEqual(self.value("water.indicated_dispensed_volume_m3"), 0)
        self.host.step(10)
        amount = 2e-3 / 60 * 10
        self.assertAlmostEqual(self.value("water.receiver_volume_m3", True), amount, delta=1e-15)
        self.assertAlmostEqual(self.value("water.buffer_volume_m3", True), .004-amount, delta=1e-15)
        self.assertAlmostEqual(self.value("consumables.throughput_m3"), amount, delta=1e-15)
        self.assertAlmostEqual(self.balance(), initial, delta=1e-15)
        self.host.step(90)
        self.assertEqual(self.value("water.dispense_status"), "completed")
        self.assertFalse(self.value("water.dispensing"))
        self.assertAlmostEqual(self.value("water.indicated_dispensed_volume_m3"), .001, delta=1e-15)
        self.assertAlmostEqual(self.balance(), initial, delta=1e-15)
        self.assertEqual(self.value("water.ro_product_volume_m3", True), 0)

    def test_stop_and_poweroff_retain_partial_material_and_new_batch_is_separate(self):
        self.prepare()
        initial = self.balance()
        self.ok("water.start")
        self.host.step(3)
        partial = self.value("water.receiver_volume_m3", True)
        self.ok("water.stop")
        self.host.step(100)
        self.assertEqual(self.value("water.receiver_volume_m3", True), partial)
        self.assertEqual(self.value("water.indicated_dispensed_volume_m3"), partial)
        self.assertEqual(self.value("water.dispense_status"), "stopped")
        self.ok("water.start")
        self.assertEqual(self.value("water.indicated_dispensed_volume_m3"), 0)
        self.host.step(2)
        self.ok("power.set", enabled=False)
        total = self.value("water.receiver_volume_m3", True)
        self.host.step(10)
        self.assertEqual(self.value("water.receiver_volume_m3", True), total)
        self.assertAlmostEqual(total, PURELAB_MAX_DISPENSE_M3_S * 5, delta=1e-15)
        self.assertEqual(self.value("water.dispense_status"), "power_off")
        self.assertAlmostEqual(self.balance(), initial, delta=1e-15)

    def test_buffer_and_receiver_limits_keep_partial_results(self):
        for kwargs, expected, reason in (({"buffer": .0002}, .0002, "supply_empty"), ({"receiver_capacity": .0003}, .0003, "receiver_full")):
            self.setUp()
            self.prepare(**kwargs)
            initial = self.balance()
            self.ok("water.start")
            self.host.step(100)
            self.assertEqual(self.value("water.dispense_status"), reason)
            self.assertFalse(self.value("water.dispensing"))
            self.assertAlmostEqual(self.value("water.indicated_dispensed_volume_m3"), expected, delta=1e-15)
            self.assertAlmostEqual(self.balance(), initial, delta=1e-15)
            retained = self.value("water.receiver_volume_m3", True)
            self.host.step(100)
            self.assertEqual(self.value("water.receiver_volume_m3", True), retained)

    def test_ro_recirculation_and_flush_obey_independent_volume_balance(self):
        self.prepare(buffer=.001, capacity=.01)
        initial = self.balance()
        self.ok("water.set_mode", mode="recirculate")
        self.host.step(60)
        produced = 20e-3 / 3600 * 60
        self.assertAlmostEqual(self.value("water.ro_product_volume_m3", True), produced, delta=1e-15)
        self.assertAlmostEqual(self.value("water.feed_volume_m3", True), .02 - produced/.5, delta=1e-15)
        self.assertAlmostEqual(self.value("water.buffer_volume_m3", True), .001+produced, delta=1e-15)
        self.assertAlmostEqual(self.value("water.waste_volume_m3", True), produced, delta=1e-15)
        self.assertAlmostEqual(self.value("consumables.throughput_m3"), 1e-5*60, delta=1e-15)
        self.assertAlmostEqual(self.balance(), initial, delta=1e-15)
        self.ok("water.set_mode", mode="flush")
        self.host.step(10)
        self.assertAlmostEqual(self.value("water.buffer_volume_m3", True), .001+produced-2e-5*10, delta=1e-15)
        self.assertAlmostEqual(self.balance(), initial, delta=1e-15)
        self.ok("water.set_mode", mode="idle")
        before = {sid: self.value(sid, True) for sid in ("water.feed_volume_m3", "water.buffer_volume_m3", "water.waste_volume_m3")}
        self.host.step(100)
        self.assertEqual(before, {sid: self.value(sid, True) for sid in before})

    def test_ro_feed_depletion_buffer_saturation_flush_exhaustion(self):
        for feed, buffer, capacity, expected in ((.0002, .001, .01, .0001), (.02, .001, .0012, .0002)):
            self.setUp()
            self.prepare(feed=feed, buffer=buffer, capacity=capacity)
            initial = self.balance()
            self.ok("water.set_mode", mode="recirculate")
            self.host.step(1000)
            self.assertAlmostEqual(self.value("water.ro_product_volume_m3", True), expected, delta=1e-15)
            self.assertAlmostEqual(self.balance(), initial, delta=1e-15)
            self.ok("water.set_mode", mode="flush")
            self.host.step(1000)
            self.assertEqual(self.value("water.buffer_volume_m3", True), 0)
            self.assertAlmostEqual(self.balance(), initial, delta=1e-15)
            self.host.step(1000)
            self.assertAlmostEqual(self.balance(), initial, delta=1e-15)

    def test_unknown_process_rates_are_not_invented(self):
        self.prepare(ro=None, recovery=None, recirc=None, flush=None, drain=False)
        self.assertEqual(self.invoke("water.set_mode", mode="recirculate")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.invoke("water.set_mode", mode="flush")["error"]["code"], "PRECONDITION_FAILED")
        self.ok("water.start")
        self.host.step(100)
        self.assertEqual(self.value("water.dispense_status"), "completed")

    def test_partition_invariance_and_no_undocumented_daily_quota(self):
        snapshots = []
        for steps in ([601], [1, 13, 97, 240, 250]):
            self.setUp()
            self.prepare(target=.015, buffer=.025, capacity=.03, receiver_capacity=.03)
            self.ok("water.start")
            for dt in steps:
                self.host.step(dt)
            snapshots.append({sid: self.value(sid, True) for sid in ("water.dispense_status", "water.dispensed_volume_m3", "water.receiver_volume_m3", "water.buffer_volume_m3", "consumables.throughput_m3")})
        self.assertEqual(snapshots[0]["water.dispense_status"], "completed")
        self.assertAlmostEqual(snapshots[0]["water.dispensed_volume_m3"], .015, delta=1e-15)
        for sid in snapshots[0]:
            if isinstance(snapshots[0][sid], str):
                self.assertEqual(snapshots[0][sid], snapshots[1][sid])
            else:
                self.assertAlmostEqual(snapshots[0][sid], snapshots[1][sid], delta=1e-14)

    def test_reset_requires_one_use_host_replacement_and_preserves_audit(self):
        self.prepare()
        self.ok("water.start")
        self.host.step(3)
        self.assertEqual(self.invoke("consumables.reset")["error"]["code"], "BUSY")
        self.ok("water.stop")
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("consumables.reset")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.confirm_consumables_replacement("pure", evidence="synthetic world confirms physical replacement")
        total = self.value("consumables.cumulative_throughput_m3", True)
        self.ok("consumables.reset")
        self.assertEqual(self.value("consumables.throughput_m3"), 0)
        self.assertEqual(self.value("consumables.cumulative_throughput_m3", True), total)
        self.assertEqual(self.value("consumables.status"), "replacement_confirmed")
        self.assertIsNone(self.value("consumables.remaining_life_fraction"))
        self.assertEqual(self.invoke("consumables.reset")["error"]["code"], "PRECONDITION_FAILED")

    def test_readings_have_explicit_source_temperature_and_sample_time(self):
        self.host.set_readings("pure", resistivity_ohm_m=182000, temperature_k=298.15, toc_mass_fraction=4e-9, source_kind="synthetic", evidence="catalog illustration, not predicted water quality")
        self.host.step(8)
        readout = self.lab.read(instance_id="pure")["observations"]
        for sid, expected in (("water.resistivity_ohm_m", 182000), ("water.temperature_k", 298.15), ("water.toc_mass_fraction", 4e-9)):
            self.assertEqual(readout[sid]["value"], expected)
            self.assertEqual(readout[sid]["quality"], "estimated")
            self.assertEqual(readout[sid]["sample_time_s"], 0)
        self.host.set_readings("pure", resistivity_ohm_m=150000, temperature_k=295, source_kind="external_observation", evidence="external observation fixture")
        obs = self.lab.read(instance_id="pure", state_ids=["water.resistivity_ohm_m"])["observations"]["water.resistivity_ohm_m"]
        self.assertEqual(obs["quality"], "valid")
        self.assertEqual(obs["sample_time_s"], 8)
        before = self.host.debug_snapshot()
        for args in ({"resistivity_ohm_m": 182000}, {"temperature_k": 0}, {"toc_mass_fraction": True}, {"resistivity_ohm_m": math.inf, "temperature_k": 298.15}):
            with self.assertRaises(ValueError):
                self.host.set_readings("pure", **args, source_kind="synthetic", evidence="fixture")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_manual_arm_limits_and_write_routing_use_same_capabilities(self):
        response = self.lab.write(instance_id="pure", state_id="arm.height_m", value=.12, request_id="height")
        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(self.value("arm.height_m"), .12)
        self.assertEqual(self.calls[-1][1]["arm.height_m"], .12)
        self.prepare()
        for state, value in (("water.target_volume_m3", .0005), ("water.flow_rate_m3_s", 1e-5), ("water.mode", "idle"), ("power.enabled", True)):
            self.assertEqual(self.lab.write(instance_id="pure", state_id=state, value=value, request_id=state)["status"], "succeeded")
        self.ok("water.start")
        self.assertEqual(self.invoke("arm.set_height", height_m=0)["error"]["code"], "BUSY")

    def test_invalid_arguments_and_busy_commands_are_atomic(self):
        cases = [("water.set_flow_rate", {"flow_m3_s": v}) for v in (0, -1, PURELAB_MAX_DISPENSE_M3_S*1.01, True, math.nan, math.inf, "1")]
        cases += [("water.set_volume", {"volume_m3": v}) for v in (0, -1, True, None)]
        cases += [("arm.set_height", {"height_m": v}) for v in (-.001, .12001, True)]
        cases += [("power.set", {"enabled": 1}), ("water.start", {"evaluator": True}), ("water.set_mode", {"mode": "auto_purity"}), ("consumables.reset", {"confirmed": True})]
        for cap, args in cases:
            before = self.host.debug_snapshot()
            response = self.invoke(cap, **args)
            self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT", (cap, args))
            self.assertEqual(response["error"]["side_effects"], "none")
            self.assertEqual(before, self.host.debug_snapshot())
        self.prepare()
        self.ok("water.start")
        for cap, args in (("water.start", {}), ("water.set_mode", {"mode": "idle"}), ("water.set_volume", {"volume_m3": .005}), ("water.set_flow_rate", {"flow_m3_s": 1e-5})):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], "BUSY")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_host_invalid_input_and_overflow_step_are_atomic(self):
        before = self.host.debug_snapshot()
        for kwargs in ({"feed": -1}, {"buffer": .03}, {"ro": PURELAB_MAX_RO_M3_S*2}, {"recovery": 0}, {"recovery": None}, {"drain": False}, {"recirc": math.inf}, {"qualified": 1}):
            with self.assertRaises(ValueError):
                self.configure(**kwargs)
            self.assertEqual(before, self.host.debug_snapshot())
        for dt in (-1, 86401, True, math.nan, math.inf, "1"):
            with self.assertRaises(ValueError):
                self.host.step(dt)
            self.assertEqual(before, self.host.debug_snapshot())
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        self.prepare(recirc=1e308)
        self.ok("water.set_mode", mode="recirculate")
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError):
            self.host.step(2)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_integral_windows_follow_creation_batch_and_reset(self):
        self.host.step(5)
        self.prepare()
        self.ok("water.start")
        self.host.step(3)
        reading = self.judge.read(instance_id="pure")["observations"]
        self.assertEqual(reading["water.indicated_dispensed_volume_m3"]["sample_window_s"], [5, 8])
        self.assertEqual(reading["water.total_dispensed_volume_m3"]["sample_window_s"], [0, 8])
        self.ok("water.stop")
        self.host.confirm_consumables_replacement("pure", evidence="synthetic replacement")
        self.ok("consumables.reset")
        self.host.step(2)
        self.assertEqual(self.lab.read(instance_id="pure", state_ids=["consumables.throughput_m3"])["observations"]["consumables.throughput_m3"]["sample_window_s"], [8, 10])

    def test_two_instances_share_clock_and_cannot_reuse_receiver(self):
        self.prepare()
        self.host.create_instance("other")
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError):
            self.host.connect_receiver("other", receiver_id="beaker", volume_m3=0, capacity_m3=.01, evidence="fixture")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("water.start")
        self.host.step(3)
        reading = self.lab.read(instance_id="other")
        self.assertEqual(reading["clock"]["time_s"], 3)
        self.assertFalse(reading["observations"]["power.enabled"]["value"])
        self.assertEqual(reading["observations"]["water.indicated_dispensed_volume_m3"]["value"], 0)
        self.assertIsNone(reading["observations"]["water.resistivity_ohm_m"]["value"])
        self.ok("water.stop")
        removed = self.host.disconnect_receiver("pure")
        self.assertAlmostEqual(removed["volume_m3"], PURELAB_MAX_DISPENSE_M3_S*3, delta=1e-15)
        self.assertIsNone(self.value("water.receiver_volume_m3", True))

    def test_principal_scope_and_truth_isolation_all_public_channels(self):
        self.prepare()
        self.ok("water.start")
        self.host.step(3)
        private = {sid for sid, info in PURELAB_STATE_SLOTS.items() if info["visibility"] != "agent"}
        discovery = json.dumps(self.lab.describe(instance_id="pure"))
        for sid in private:
            self.assertNotIn(sid, discovery)
            self.assertNotIn(sid, self.lab.read(instance_id="pure")["observations"])
            for response in (self.lab.read(instance_id="pure", state_ids=[sid]), self.lab.write(instance_id="pure", state_id=sid, value=100, request_id="forbidden")):
                self.assertEqual(response["error"]["code"], "ACCESS_DENIED")
                self.assertNotIn(sid, json.dumps(response))
            self.assertNotIn(sid, json.dumps(self.lab.logs()))
            for _, states in self.calls:
                self.assertNotIn(sid, states)
        self.assertEqual(set(self.judge.read(instance_id="pure")["observations"]), set(PURELAB_STATE_SLOTS))
        self.host.create_instance("other")
        self.host.register_principal("scoped", instance_ids=["pure"])
        scoped = self.host.session("scoped")
        for response in (scoped.read(instance_id="other"), scoped.describe(instance_id="other"), scoped.invoke(instance_id="other", capability_id="power.set", arguments={"enabled": True}, request_id="x")):
            self.assertEqual(response["error"]["code"], "UNKNOWN_INSTANCE")
        with self.assertRaises(ValueError):
            self.host.register_principal("learner", visibility="evaluator")
        with self.assertRaises(ValueError):
            self.host.session("self_promoted")

    def test_request_replay_conflict_and_returned_objects_are_isolated(self):
        args = dict(instance_id="pure", capability_id="power.set", arguments={"enabled": True}, request_id="replay")
        original = self.lab.invoke(**args)
        self.host.step(3)
        before = self.host.debug_snapshot()
        self.assertEqual(self.lab.invoke(**args), original)
        original["result"]["applied"] = False
        self.assertTrue(self.lab.invoke(**args)["result"]["applied"])
        conflict = self.lab.invoke(**{**args, "arguments": {"enabled": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())
        self.lab.logs().clear()
        self.assertTrue(self.lab.logs())
        data = self.lab.read(instance_id="pure")
        data["observations"]["power.enabled"]["value"] = False
        self.assertTrue(self.value("power.enabled"))

    def test_scene_failure_command_rolls_back_but_step_retains_water_and_clock(self):
        def broken(instance, states):
            raise RuntimeError("private backend material state")
        self.host.scene_adapter = broken
        before = self.host.debug_snapshot()
        response = self.invoke("arm.set_height", height_m=.12)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("private backend", json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.scene_adapter = None
        self.prepare()
        initial = self.balance()
        self.ok("water.start")
        self.host.scene_adapter = broken
        with self.assertRaises(PurelabSceneSynchronizationError):
            self.host.step(3)
        self.assertEqual(self.host.time_s, 3)
        self.assertGreater(self.value("water.receiver_volume_m3", True), 0)
        self.assertAlmostEqual(self.balance(), initial, delta=1e-15)
        self.assertTrue(self.host.debug_snapshot()["scene_faults"])

    def test_exported_schemas_match_every_api_actual_result_and_state(self):
        self.prepare()
        spec = export_purelab_contract_metadata()["asset_types"][PURELAB_ASSET_TYPE_ID]
        self.assertEqual(set(spec["capabilities"]), set(PURELAB_ARGUMENTS))
        for cap, info in spec["capabilities"].items():
            self.assertEqual(lint_schema(info["input_schema"]), [], cap)
            self.assertEqual(lint_schema(info["output_schema"]), [], cap)
            self.assertEqual(validate_value(info["example_arguments"][0], info["input_schema"]), [], cap)
            if cap == "consumables.reset":
                self.host.confirm_consumables_replacement("pure", evidence="synthetic replacement")
            response = self.ok(cap, **info["example_arguments"][0])
            self.assertEqual(validate_value(response["result"], info["output_schema"]), [], cap)
            self.assertEqual(set(response), {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"})
        for sid, obs in self.judge.read(instance_id="pure")["observations"].items():
            self.assertEqual(validate_value(obs["value"], spec["state_slots"][sid]["value_schema"]), [], sid)
            self.assertEqual(obs["unit"], spec["state_slots"][sid]["unit"])

    def test_large_json_integers_reject_without_raising_or_mutation(self):
        huge = 10**400
        for cap, args in (("water.set_volume", {"volume_m3": huge}), ("water.set_flow_rate", {"flow_m3_s": huge}), ("arm.set_height", {"height_m": huge})):
            before = self.host.debug_snapshot()
            response = self.invoke(cap, **args)
            self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError):
            self.host.step(huge)
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(ValueError):
            self.configure(feed=huge)
        self.assertEqual(before, self.host.debug_snapshot())

# CHEM-189 append-only tests; distinct from CHEM-190 ELGA PURELAB naming.
from basic40_runtime_vacuum import (INERT_PURELAB_ASSET_TYPE_ID, INERT_PURELAB_STATE_SLOTS,
    INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID, InertPureLabHost, InertPureLabSceneSynchronizationError,
    export_inert_purelab_contract_metadata)


class Basic40InertPureLabTests(unittest.TestCase):
    """Synthetic control/boundary tests; no OEM kinetics or actual hardware."""
    def setUp(self):
        self.calls = []
        self.host = InertPureLabHost(scene_adapter=lambda i, u: self.calls.append((i, copy.deepcopy(u))))
        self.host.create_instance("box")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.sequence = 0

    def invoke(self, cap, *, instance="box", **args):
        self.sequence += 1
        return self.agent.invoke(instance_id=instance, capability_id=cap, arguments=args, request_id=f"inert_{self.sequence}")

    def ok(self, cap, *, instance="box", **args):
        response = self.invoke(cap, instance=instance, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, sid, *, instance="box", truth=False):
        session = self.judge if truth else self.agent
        return session.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def boundary(self, pressure=101325, *, source=True, gas=True, instance="box"):
        self.host.set_external_boundary(instance, pressure_pa=pressure, source_running=source, gas_supply_ready=gas,
            evidence="engineering_assumption: synthetic externally supplied pressure/flags, not an OEM cycle curve")

    def configure(self, *, instance="box"):
        self.host.connect_vacuum_source(instance, "shared_pc3001")
        self.host.configure_cycle_boundaries(instance, evacuation_pressure_pa=1000, backfill_pressure_pa=101325,
            evidence="engineering_assumption: 1000/101325 Pa synthetic test endpoints, not catalog values")
        self.boundary(instance=instance)
        self.ok("power.set", instance=instance, enabled=True)

    def start(self, *, cycles=1, instance="box"):
        self.configure(instance=instance)
        self.ok("antechamber.set_cycles", instance=instance, cycles=cycles)
        self.ok("antechamber.cycle.start", instance=instance)

    def phase(self, pressure, *, instance="box"):
        self.boundary(pressure, instance=instance)
        self.host.step(1)

    def test_metadata_distinct_type_and_catalog_units(self):
        spec = export_inert_purelab_contract_metadata()["asset_types"][INERT_PURELAB_ASSET_TYPE_ID]
        self.assertEqual(spec["catalog_id"], "CHEM-189")
        self.assertEqual(spec["model"], "Inert | PureLab HE 2GB")
        self.assertAlmostEqual(spec["parameters"]["maximum_circulation_flow_m3_s"]["value"], 60 * .028316846592 / 60)
        self.assertEqual(spec["parameters"]["per_column_water_capacity_kg"]["value"], 1.8)
        self.assertIsNone(spec["parameters"]["purification_rate"]["value"])
        self.assertEqual(self.value("vacuum.source_asset_type_id"), INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID)
        import basic40_runtime_vacuum as runtime
        self.assertIn("instrument.elga_purelab_flex3", runtime.export_purelab_contract_metadata()["asset_types"])

    def test_read_describe_pure_and_sensors_initially_unavailable(self):
        before = self.host.debug_snapshot()
        for _ in range(3):
            self.agent.describe(instance_id="box")
            response = self.agent.read(instance_id="box")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.host.time_s, 0)
        for sid, slot in INERT_PURELAB_STATE_SLOTS.items():
            if slot["origin"] == "sensor":
                item = response["observations"][sid]
                self.assertIsNone(item["value"])
                self.assertEqual(item["quality"], "unavailable")
                self.assertTrue(item["reason"])

    def test_pressure_set_range_no_teleport_no_sensor_synthesis(self):
        self.host.set_process_truth("box", {"pressure.box_gauge_pa": 73.25}, evidence="engineering_assumption: test truth")
        for p in (-1500, 1500, 0):
            self.ok("pressure.set", pressure_pa=p)
            self.assertEqual(self.value("pressure.target_gauge_pa"), p)
            self.assertEqual(self.value("pressure.box_gauge_pa", truth=True), 73.25)
            self.assertIsNone(self.value("pressure.indicated_gauge_pa"))
        for p in (-1500.001, 1500.001, True, float("nan"), float("inf"), "100", None):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("pressure.set", pressure_pa=p)["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_purification_control_does_not_claim_purity_or_flow(self):
        self.assertEqual(self.invoke("purification.set", enabled=True)["error"]["code"], "PRECONDITION_FAILED")
        self.ok("power.set", enabled=True)
        self.ok("purification.set", enabled=True)
        self.host.step(100)
        self.assertTrue(self.value("purification.enabled"))
        for sid in ("atmosphere.water_mole_fraction", "atmosphere.oxygen_mole_fraction", "purification.flow_m3_s", "purification.oxygen_removed_m3", "purification.water_removed_kg"):
            self.assertIsNone(self.value(sid))
        self.ok("purification.set", enabled=False)
        self.assertFalse(self.value("purification.enabled"))

    def test_purge_requires_power_and_fresh_gas_boundary_without_flow(self):
        self.assertEqual(self.invoke("purge.set", enabled=True)["error"]["code"], "PRECONDITION_FAILED")
        self.configure()
        self.boundary(gas=False)
        self.assertEqual(self.invoke("purge.set", enabled=True)["error"]["code"], "PRECONDITION_FAILED")
        self.boundary()
        self.host.step(1)
        self.assertEqual(self.invoke("purge.set", enabled=True)["error"]["code"], "PRECONDITION_FAILED")
        self.boundary()
        self.ok("purge.set", enabled=True)
        self.assertTrue(self.value("purge.enabled"))
        self.assertIsNone(self.value("purge.flow_m3_s"))
        self.ok("purge.set", enabled=False)

    def test_regeneration_explicitly_unsupported_and_atomic(self):
        before = self.host.debug_snapshot()
        response = self.invoke("regeneration.start")
        self.assertEqual(response["status"], "unsupported")
        self.assertEqual(response["error"]["code"], "UNSUPPORTED")
        self.assertEqual(response["error"]["side_effects"], "none")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.value("regeneration.status"), "unsupported")

    def test_doors_interlock_both_directions_and_angles(self):
        for door, other in (("inner", "outer"), ("outer", "inner")):
            self.ok(f"antechamber.{door}_door.set", open=True)
            self.assertEqual(self.value(f"antechamber.{door}_door_angle_rad"), math.pi / 2)
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(f"antechamber.{other}_door.set", open=True)["error"]["code"], "PRECONDITION_FAILED")
            self.assertEqual(before, self.host.debug_snapshot())
            self.ok(f"antechamber.{door}_door.set", open=False)
            self.assertEqual(self.value(f"antechamber.{door}_door_angle_rad"), 0)
        self.assertEqual(self.host.time_s, 0)

    def test_cycle_rejects_missing_power_source_thresholds_or_boundary(self):
        for stage in range(4):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("antechamber.cycle.start")["error"]["code"], "PRECONDITION_FAILED", stage)
            self.assertEqual(before, self.host.debug_snapshot())
            if stage == 0: self.ok("power.set", enabled=True)
            if stage == 1: self.host.connect_vacuum_source("box", "shared_pc3001")
            if stage == 2: self.host.configure_cycle_boundaries("box", evacuation_pressure_pa=1000, backfill_pressure_pa=101325, evidence="engineering_assumption: fixture")
        self.boundary()
        self.ok("antechamber.cycle.start")
        self.assertEqual(self.value("antechamber.phase"), "evacuating")

    def test_cycle_range_enforces_integer_one_to_nine(self):
        for cycles in (1, 9):
            self.ok("antechamber.set_cycles", cycles=cycles)
        for cycles in (0, 10, -1, 1.0, True, 2.5, float("nan"), "3"):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("antechamber.set_cycles", cycles=cycles)["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_cycle_door_and_busy_rejections_are_atomic(self):
        self.configure()
        self.ok("antechamber.outer_door.set", open=True)
        self.assertEqual(self.invoke("antechamber.cycle.start")["error"]["code"], "PRECONDITION_FAILED")
        self.ok("antechamber.outer_door.set", open=False)
        self.ok("antechamber.cycle.start")
        for cap, args, code in (("antechamber.cycle.start", {}, "BUSY"), ("antechamber.set_cycles", {"cycles": 2}, "BUSY"), ("antechamber.inner_door.set", {"open": True}, "PRECONDITION_FAILED"), ("antechamber.outer_door.set", {"open": True}, "PRECONDITION_FAILED")):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], code)
            self.assertEqual(before, self.host.debug_snapshot())

    def test_time_alone_never_completes_cycle_and_zero_step_is_pure(self):
        self.start()
        self.host.step(3600)
        self.assertEqual(self.value("antechamber.completed_cycles"), 0)
        self.assertEqual(self.value("antechamber.phase"), "evacuating")
        self.boundary(900)
        before = self.host.debug_snapshot()
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.step(1)
        self.assertEqual(self.value("antechamber.phase"), "backfilling")
        self.host.step(3600)
        self.assertEqual(self.value("antechamber.completed_cycles"), 0)

    def test_nine_cycles_count_only_completed_evacuation_then_backfill(self):
        self.start(cycles=9)
        for expected in range(1, 10):
            self.phase(101325)
            self.assertEqual(self.value("antechamber.completed_cycles"), expected - 1)
            self.phase(900)
            self.assertEqual(self.value("antechamber.phase"), "backfilling")
            self.assertEqual(self.value("antechamber.completed_cycles"), expected - 1)
            self.phase(900)
            self.assertEqual(self.value("antechamber.completed_cycles"), expected - 1)
            self.phase(101325)
            self.assertEqual(self.value("antechamber.completed_cycles"), expected)
        self.assertEqual(self.value("antechamber.phase"), "completed")
        self.assertFalse(self.value("antechamber.cycle_active"))
        self.phase(900)
        self.phase(101325)
        self.assertEqual(self.value("antechamber.completed_cycles"), 9)

    def test_boundary_sample_not_reused_and_unready_sources_stall(self):
        self.start()
        self.boundary(900, source=False)
        self.host.step(1)
        self.assertEqual(self.value("antechamber.phase"), "evacuating")
        self.phase(900)
        self.assertEqual(self.value("antechamber.phase"), "backfilling")
        self.host.step(1)
        self.assertEqual(self.value("antechamber.completed_cycles"), 0)
        self.boundary(101325, gas=False)
        self.host.step(1)
        self.assertEqual(self.value("antechamber.completed_cycles"), 0)
        self.phase(101325)
        self.assertEqual(self.value("antechamber.completed_cycles"), 1)

    def test_stale_start_and_low_initial_pressure_rejected(self):
        self.configure()
        self.host.step(1)
        self.assertEqual(self.invoke("antechamber.cycle.start")["error"]["code"], "PRECONDITION_FAILED")
        self.boundary(900)
        self.assertEqual(self.invoke("antechamber.cycle.start")["error"]["code"], "PRECONDITION_FAILED")
        self.boundary(source=False)
        self.assertEqual(self.invoke("antechamber.cycle.start")["error"]["code"], "PRECONDITION_FAILED")
        self.boundary()
        self.ok("antechamber.cycle.start")

    def test_stop_retains_partial_effects_and_restart_explicitly_resets_count(self):
        self.start(cycles=3)
        self.phase(900)
        self.phase(101325)
        self.phase(900)
        self.ok("antechamber.cycle.stop")
        self.assertEqual(self.value("antechamber.completed_cycles"), 1)
        self.assertEqual(self.value("antechamber.phase"), "stopped")
        self.assertEqual(self.value("antechamber.pressure_pa", truth=True), 900)
        self.phase(101325)
        self.assertEqual(self.value("antechamber.completed_cycles"), 1)
        self.boundary()
        self.ok("antechamber.cycle.start")
        self.assertEqual(self.value("antechamber.completed_cycles"), 0)

    def test_power_off_stops_controls_preserves_count_and_pressure(self):
        self.start(cycles=2)
        self.phase(900)
        self.phase(101325)
        self.boundary()
        self.ok("purification.set", enabled=True)
        self.ok("purge.set", enabled=True)
        self.ok("power.set", enabled=False)
        self.assertEqual(self.value("antechamber.completed_cycles"), 1)
        self.assertEqual(self.value("antechamber.phase"), "stopped")
        self.assertFalse(self.value("antechamber.cycle_active"))
        self.assertFalse(self.value("purification.enabled"))
        self.assertFalse(self.value("purge.enabled"))
        self.host.step(100)
        self.assertEqual(self.value("antechamber.pressure_pa", truth=True), 101325)

    def test_observations_supplied_separately_and_become_stale(self):
        self.host.set_process_truth("box", {"atmosphere.oxygen_truth_mole_fraction": .20}, evidence="engineering_assumption: synthetic truth")
        self.assertIsNone(self.value("atmosphere.oxygen_mole_fraction"))
        supplied = {"atmosphere.oxygen_mole_fraction": 4e-6, "atmosphere.water_mole_fraction": 8e-6, "pressure.indicated_gauge_pa": 1700}
        self.host.set_observations("box", supplied, evidence="engineering_assumption: independent synthetic sensor readings", quality="estimated")
        for sid, value in supplied.items():
            item = self.agent.read(instance_id="box", state_ids=[sid])["observations"][sid]
            self.assertEqual(item["value"], value)
            self.assertEqual(item["quality"], "estimated")
        self.ok("power.set", enabled=True)
        self.ok("purification.set", enabled=True)
        self.host.step(10)
        for sid, value in supplied.items():
            item = self.agent.read(instance_id="box", state_ids=[sid])["observations"][sid]
            self.assertEqual(item["value"], value)
            self.assertEqual(item["quality"], "stale")
            self.assertEqual(item["sample_time_s"], 0)
        self.assertEqual(self.value("atmosphere.oxygen_truth_mole_fraction", truth=True), .20)

    def test_truth_filtered_in_all_public_surfaces_including_scene(self):
        private = {sid for sid, slot in INERT_PURELAB_STATE_SLOTS.items() if slot["visibility"] != "agent"}
        self.host.set_process_truth("box", {"atmosphere.oxygen_truth_mole_fraction": .246813579}, evidence="private_fixture_marker")
        self.host.sync_scene()
        self.ok("power.set", enabled=True)
        for sid in private:
            for response in (self.agent.read(instance_id="box", state_ids=[sid]), self.agent.write(instance_id="box", state_id=sid, value=0, request_id="denied")):
                self.assertEqual(response["error"]["code"], "ACCESS_DENIED")
                self.assertNotIn(sid, json.dumps(response))
        public = json.dumps([self.agent.describe(instance_id="box"), self.agent.read(instance_id="box"), self.agent.logs(), self.calls])
        for sid in private:
            self.assertNotIn(sid, public)
        self.assertNotIn("0.246813579", public)
        self.assertNotIn("private_fixture_marker", public)
        self.assertEqual(set(self.judge.read(instance_id="box")["observations"]), set(INERT_PURELAB_STATE_SLOTS))

    def test_instance_isolation_and_host_identity_grants(self):
        self.host.create_instance("other")
        self.start(instance="box")
        other_before = copy.deepcopy(self.host.debug_snapshot()["instances"]["other"]["state"])
        self.phase(900)
        self.assertEqual(self.host.debug_snapshot()["instances"]["other"]["state"], other_before)
        self.host.register_principal("scoped", instance_ids=["box"])
        scoped = self.host.session("scoped")
        for response in (scoped.read(instance_id="other"), scoped.describe(instance_id="other"), scoped.invoke(instance_id="other", capability_id="power.set", arguments={"enabled": True}, request_id="x")):
            self.assertEqual(response["error"]["code"], "UNKNOWN_INSTANCE")
        with self.assertRaises(ValueError): self.host.session("self_claimed_evaluator")
        with self.assertRaises(ValueError): self.host.register_principal("learner", visibility="evaluator")
        self.assertNotIn("sim.step", self.agent.describe(instance_id="box")["capabilities"])
        self.assertFalse(hasattr(self.agent, "step"))

    def test_request_dedup_conflicts_and_returned_copies(self):
        args = dict(instance_id="box", capability_id="antechamber.inner_door.set", arguments={"open": True}, request_id="same")
        response = self.agent.invoke(**args)
        calls = len(self.calls)
        self.host.step(1)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**args), response)
        self.assertEqual(calls, len(self.calls))
        response["result"]["open"] = False
        self.assertTrue(self.agent.invoke(**args)["result"]["open"])
        conflict = self.agent.invoke(**{**args, "arguments": {"open": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())
        logs = self.agent.logs(); logs.clear()
        self.assertTrue(self.agent.logs())

    def test_write_routes_and_cannot_bypass_interlock_or_truth(self):
        response = self.agent.write(instance_id="box", state_id="pressure.target_gauge_pa", value=-500, request_id="w1")
        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(self.value("pressure.target_gauge_pa"), -500)
        self.agent.write(instance_id="box", state_id="antechamber.inner_door_open", value=True, request_id="w2")
        response = self.agent.write(instance_id="box", state_id="antechamber.outer_door_open", value=True, request_id="w3")
        self.assertEqual(response["error"]["code"], "PRECONDITION_FAILED")
        response = self.agent.write(instance_id="box", state_id="antechamber.inner_door_angle_rad", value=.5, request_id="w4")
        self.assertEqual(response["error"]["code"], "ACCESS_DENIED")

    def test_scene_callback_door_state_matches_request_without_hidden_values(self):
        self.ok("antechamber.inner_door.set", open=True)
        updates = self.calls[-1][1]
        self.assertEqual(updates, {"antechamber.inner_door_open": True, "antechamber.inner_door_angle_rad": math.pi / 2})
        self.ok("antechamber.inner_door.set", open=False)
        self.assertEqual(self.calls[-1][1]["antechamber.inner_door_angle_rad"], 0)
        self.assertEqual(self.host.time_s, 0)

    def test_command_scene_failure_preserves_process_but_reports_unknown_scene(self):
        self.host.scene_adapter = lambda i, u: (_ for _ in ()).throw(RuntimeError("private_backend_marker"))
        before = self.host.debug_snapshot()
        response = self.invoke("antechamber.inner_door.set", open=True)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("private_backend_marker", json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())

    def test_host_step_scene_failure_retains_phase_and_clock(self):
        self.start()
        self.boundary(900)
        self.host.scene_adapter = lambda i, u: (_ for _ in ()).throw(RuntimeError("private_backend_marker"))
        with self.assertRaises(InertPureLabSceneSynchronizationError): self.host.step(1)
        self.assertEqual(self.host.time_s, 1)
        self.assertEqual(self.value("antechamber.phase"), "backfilling")
        self.assertTrue(self.host.debug_snapshot()["scene_faults"])
        self.assertNotIn("private_backend_marker", json.dumps(self.agent.logs()))

    def test_host_configuration_validation_no_internal_pump_or_impossible_floor(self):
        for source, typ in (("", INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID), ("box", INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID), ("p", "instrument.some_other_pump")):
            with self.assertRaises(ValueError): self.host.connect_vacuum_source("box", source, asset_type_id=typ)
        self.configure()
        for low, high in ((199, 101325), (1000, 500), (True, 101325), (1000, float("inf"))):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.configure_cycle_boundaries("box", evacuation_pressure_pa=low, backfill_pressure_pa=high, evidence="engineering_assumption: fixture")
            self.assertEqual(before, self.host.debug_snapshot())
        for p in (199, -1, True, float("inf"), float("nan")):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.boundary(p)
            self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(set(self.host.debug_snapshot()["instances"]), {"box"})
        self.ok("antechamber.cycle.start")
        with self.assertRaises(ValueError): self.host.connect_vacuum_source("box", "p2")
        with self.assertRaises(ValueError): self.host.configure_cycle_boundaries("box", evacuation_pressure_pa=500, backfill_pressure_pa=101325, evidence="engineering_assumption: fixture")

    def test_host_invalid_time_and_observation_rejected_without_change(self):
        for dt in (-1, True, float("nan"), float("inf"), 3601):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.step(dt)
            self.assertEqual(before, self.host.debug_snapshot())
        for values in ({"atmosphere.oxygen_mole_fraction": -1}, {"atmosphere.water_mole_fraction": 2}, {"pressure.indicated_gauge_pa": True}, {"antechamber.indicated_pressure_pa": -1}, {"atmosphere.oxygen_truth_mole_fraction": .1}):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.set_observations("box", values, evidence="engineering_assumption: fixture")
            self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(ValueError): self.host.set_observations("box", {"atmosphere.oxygen_mole_fraction": .1}, evidence="")

    def test_protocol_invalid_requests_and_describe_target_selection(self):
        for kwargs in ({}, {"asset_type_id": INERT_PURELAB_ASSET_TYPE_ID, "instance_id": "box"}, {"asset_type_id": "wrong"}):
            self.assertEqual(self.agent.describe(**kwargs)["error"]["code"], "INVALID_ARGUMENT")
        for cap, args in (("pressure.set", {"pressure_pa": 1, "extra": 2}), ("power.set", {"enabled": 1}), ("regeneration.start", {"unknown": True})):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.invoke("sim.step", dt_s=1)["error"]["code"], "UNKNOWN_CAPABILITY")
        for ids in ("power.enabled", ["power.enabled", "power.enabled"], [7]):
            self.assertEqual(self.agent.read(instance_id="box", state_ids=ids)["error"]["code"], "INVALID_ARGUMENT")
        for req in ("", None, 1, "a" * 257):
            self.assertEqual(self.agent.invoke(instance_id="box", capability_id="power.set", arguments={"enabled": True}, request_id=req)["error"]["code"], "INVALID_ARGUMENT")

    def test_real_capability_results_and_observations_match_schemas(self):
        self.configure()
        spec = export_inert_purelab_contract_metadata()["asset_types"][INERT_PURELAB_ASSET_TYPE_ID]
        for cap, info in spec["capabilities"].items():
            self.assertEqual(lint_schema(info["input_schema"]), [], cap)
            self.assertEqual(lint_schema(info["output_schema"]), [], cap)
            args = info["example_arguments"][0]
            self.assertEqual(validate_value(args, info["input_schema"]), [], cap)
            if cap == "antechamber.outer_door.set": self.ok("antechamber.inner_door.set", open=False)
            response = self.invoke(cap, **args)
            self.assertEqual(response["status"], "unsupported" if cap == "regeneration.start" else "succeeded", cap)
            if cap != "regeneration.start": self.assertEqual(validate_value(response["result"], info["output_schema"]), [], cap)
            self.assertEqual(set(response), {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"})
        for sid, obs in self.judge.read(instance_id="box")["observations"].items():
            self.assertEqual(validate_value(obs["value"], spec["state_slots"][sid]["value_schema"]), [], sid)
            self.assertEqual(obs["unit"], spec["state_slots"][sid]["unit"])
            self.assertIsNone(obs["sample_window_s"])

# CHEM-031 synthetic tests. Appended after the existing main guard: use discovery.
from basic40_runtime_vacuum import (EXTRAHERA_ASSET_TYPE_ID, EXTRAHERA_STATE_SLOTS,
    EXTRAHERA_CAPABILITY_ARGUMENTS, EXTRAHERA_JOINTS, EXTRAHERA_SCENE_STATE_IDS,
    EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID, ExtraheraHost,
    ExtraheraSceneSynchronizationError, export_extrahera_contract_metadata)


class Basic40ExtraheraTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = ExtraheraHost(scene_adapter=lambda i, u: self.calls.append((i, copy.deepcopy(u))))
        self.host.create_instance("spe")
        self.host.register_principal("learner")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.sequence = 0

    def invoke(self, cap, *, instance="spe", **args):
        self.sequence += 1
        return self.agent.invoke(instance_id=instance, capability_id=cap, arguments=args, request_id=f"extrahera_test_{self.sequence}")

    def ok(self, cap, *, instance="spe", **args):
        response = self.invoke(cap, instance=instance, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def accepted(self, cap, *, instance="spe", **args):
        response = self.invoke(cap, instance=instance, **args)
        self.assertEqual(response["status"], "accepted", response)
        self.assertIsNotNone(response["operation_id"])
        return response["operation_id"]

    def value(self, sid, *, truth=False, instance="spe"):
        session = self.judge if truth else self.agent
        return session.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def configure(self, *, instance="spe", format="cartridges24", cartridge_volume_m3=3e-6):
        self.ok("power.set", instance=instance, enabled=True)
        self.ok("deck.configure", instance=instance, format=format, cartridge_volume_m3=cartridge_volume_m3, collection_plate="collection_a", solvent_positions=["load", "wash", "elute"])
        self.host.connect_vacuum_source(instance, "shared_pc3001")
        self.host.set_utilities(instance, vacuum_ready=True, compressed_air_ready=True, nitrogen_ready=True, evidence="engineering_assumption: synthetic utility availability")
        self.host.initialize_liquids(instance, {"load": 10e-6, "wash": 10e-6, "elute": 10e-6}, evidence="engineering_assumption: synthetic inventories, initially empty process vessels")

    def method(self, *, instance="spe", repetitions=1):
        self.configure(instance=instance)
        self.ok("manifold.set_position", instance=instance, z_m=0.0)
        self.ok("pressure.set", instance=instance, pressure_pa=100000)
        steps = [{"phase": p, "solvent_position": p, "volume_m3": 1e-6, "flow_m3_s": 1e-7, "drain_flow_m3_s": 1e-7, "repetitions": repetitions} for p in ("load", "wash", "elute")]
        self.ok("method.configure", instance=instance, steps=steps)
        return steps

    def total(self, instance="spe"):
        return sum(self.value("fluid." + n + "_m3", truth=True, instance=instance) for n in ("sources", "pipette", "extraction", "waste", "collection"))

    def assert_conserved(self, instance="spe"):
        self.assertAlmostEqual(self.total(instance), self.value("fluid.initial_total_m3", truth=True, instance=instance), delta=1e-17)
        for n in ("sources", "pipette", "extraction", "waste", "collection"):
            self.assertGreaterEqual(self.value("fluid." + n + "_m3", truth=True, instance=instance), 0)
        self.assertLessEqual(self.value("fluid.pipette_m3", truth=True, instance=instance), 1e-6 + 1e-18)

    def test_default_unknown_quantities_are_null_and_no_time_on_read(self):
        self.assertEqual(self.value("pressure.target_pa"), 0)
        for sid in ("pressure.indicated_pa", "chemistry.recovery_fraction", "chemistry.adsorbed_mass_kg", "fluid.pipette_m3", "fluid.initial_total_m3"):
            obs = self.judge.read(instance_id="spe", state_ids=[sid])["observations"][sid]
            self.assertIsNone(obs["value"])
            self.assertEqual(obs["quality"], "unavailable")
        before = self.host.debug_snapshot()
        self.agent.read(instance_id="spe")
        self.agent.describe(asset_type_id=EXTRAHERA_ASSET_TYPE_ID)
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.host.time_s, 0)

    def test_deck_all_five_formats_and_invalid_combinations(self):
        for fmt, volume in (("cartridges24", 1e-6), ("cartridges24", 3e-6), ("cartridges24", 6e-6), ("plate96", None), ("plate48", None)):
            self.ok("deck.configure", format=fmt, cartridge_volume_m3=volume, collection_plate="collection", solvent_positions=["sample", "solvent"])
            self.assertEqual(self.value("deck.format"), fmt)
            self.assertEqual(self.value("deck.cartridge_volume_m3"), volume)
        for fmt, volume, positions in (("cartridges24", None, ["s"]), ("plate96", 3e-6, ["s"]), ("cartridges24", 2e-6, ["s"]), ("plate48", None, ["s", "s"]), ("plate96", None, [""])):
            before = self.host.debug_snapshot()
            r = self.invoke("deck.configure", format=fmt, cartridge_volume_m3=volume, collection_plate="collection", solvent_positions=positions)
            self.assertEqual(r["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_joint_bounds_and_actual_callback_mapping(self):
        self.ok("power.set", enabled=True)
        for values in ((0, 0, 0), (.44, .28, .20)):
            self.ok("gantry.move", x_m=values[0], y_m=values[1], z_m=values[2])
            for axis, value in zip(("x_m", "y_m", "z_m"), values): self.assertEqual(self.value("gantry." + axis), value)
        for q in (0, .08): self.ok("manifold.set_position", z_m=q)
        for axis, upper in (("x_m", .44), ("y_m", .28), ("z_m", .20)):
            for invalid in (-.001, upper + .001, True, float("inf"), float("nan")):
                args = {"x_m": .2, "y_m": .1, "z_m": .1}; args[axis] = invalid
                before = self.host.debug_snapshot()
                self.assertEqual(self.invoke("gantry.move", **args)["error"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(before, self.host.debug_snapshot())
        self.host.sync_scene()
        self.assertEqual(set(self.calls[-1][1]), set(EXTRAHERA_SCENE_STATE_IDS))
        self.assertEqual(self.calls[-1][1]["manifold.z_m"], .08)

    def test_pressure_range_step_utilities_interlocks_and_unknown_observation(self):
        self.configure()
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("pressure.set", pressure_pa=100000)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("manifold.set_position", z_m=0)
        for pressure in (0, 10000, 500000): self.ok("pressure.set", pressure_pa=pressure)
        for pressure in (-10000, 510000, 5000, True, float("inf"), float("nan")):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("pressure.set", pressure_pa=pressure)["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())
        for cap, args in (("gantry.move", {"x_m": .1, "y_m": .1, "z_m": .1}), ("manifold.set_position", {"z_m": .05})):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], "PRECONDITION_FAILED")
            self.assertEqual(before, self.host.debug_snapshot())
        self.host.step(300)
        self.assertIsNone(self.value("pressure.indicated_pa"))
        self.assertEqual(self.value("pressure.target_pa"), 500000)

    def test_single_stroke_partial_full_and_exact_volume_conservation(self):
        self.configure()
        op = self.accepted("sample.aspirate", solvent_position="load", volume_m3=1e-6, flow_m3_s=1e-7)
        self.assertEqual(self.value("fluid.pipette_m3", truth=True), 0)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.poll(instance_id="spe", operation_id=op)["status"], "running")
        self.agent.read(instance_id="spe")
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.step(4)
        self.assertAlmostEqual(self.value("fluid.pipette_m3", truth=True), .4e-6)
        self.assert_conserved()
        self.host.step(6)
        self.assertEqual(self.agent.poll(instance_id="spe", operation_id=op)["status"], "succeeded")
        self.assertAlmostEqual(self.value("fluid.pipette_m3", truth=True), 1e-6)
        op2 = self.accepted("sample.dispense", destination="collection", volume_m3=1e-6, flow_m3_s=2e-7)
        self.host.step(5)
        self.assertEqual(self.agent.poll(instance_id="spe", operation_id=op2)["status"], "succeeded")
        self.assertAlmostEqual(self.value("fluid.collection_m3", truth=True), 1e-6)
        self.assert_conserved()

    def test_maximum_stroke_is_not_scaled_by_plate_count_or_cartridge_volume(self):
        self.configure(format="plate96", cartridge_volume_m3=None)
        for volume in (0, -1e-6, 1.001e-6, 96e-6, True):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("sample.aspirate", solvent_position="load", volume_m3=volume, flow_m3_s=1e-7)["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())
        self.accepted("sample.aspirate", solvent_position="load", volume_m3=.6e-6, flow_m3_s=1e-7)
        self.host.step(10)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("sample.aspirate", solvent_position="load", volume_m3=.5e-6, flow_m3_s=1e-7)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_missing_sources_and_insufficient_pipette_rejected_atomically(self):
        self.configure()
        for cap, args in (("sample.aspirate", {"solvent_position": "missing", "volume_m3": 1e-6, "flow_m3_s": 1e-7}), ("sample.dispense", {"destination": "collection", "volume_m3": 1e-6, "flow_m3_s": 1e-7})):
            before = self.host.debug_snapshot()
            response = self.invoke(cap, **args)
            self.assertEqual(response["error"]["code"], "PRECONDITION_FAILED")
            self.assertEqual(before, self.host.debug_snapshot())
            self.assertNotIn("fluid.", json.dumps(response))

    def test_manual_cancel_retains_partially_transferred_liquid_and_can_dispense_it(self):
        self.configure()
        op = self.accepted("sample.aspirate", solvent_position="load", volume_m3=1e-6, flow_m3_s=1e-7)
        self.host.step(4)
        result = self.agent.cancel(instance_id="spe", operation_id=op, request_id="cancel_manual")
        self.assertEqual(result["status"], "cancelled")
        self.host.step(100)
        self.assertAlmostEqual(self.value("fluid.pipette_m3", truth=True), .4e-6)
        self.accepted("sample.dispense", destination="waste", volume_m3=.4e-6, flow_m3_s=1e-7)
        self.host.step(4)
        self.assertAlmostEqual(self.value("fluid.waste_m3", truth=True), .4e-6)
        self.assert_conserved()

    def test_method_order_load_wash_elute_and_output_routing(self):
        self.method()
        op = self.accepted("method.start")
        self.assertEqual(self.value("method.phase"), "load")
        self.host.step(30)
        self.assertEqual(self.value("method.phase"), "wash")
        self.assertEqual(self.value("method.completed_strokes"), 1)
        self.assertAlmostEqual(self.value("fluid.waste_m3", truth=True), 1e-6)
        self.host.step(30)
        self.assertEqual(self.value("method.phase"), "elute")
        self.assertAlmostEqual(self.value("fluid.waste_m3", truth=True), 2e-6)
        self.assertEqual(self.value("fluid.collection_m3", truth=True), 0)
        self.host.step(30)
        self.assertEqual(self.agent.poll(instance_id="spe", operation_id=op)["status"], "succeeded")
        self.assertEqual(self.value("method.completed_strokes"), 3)
        self.assertAlmostEqual(self.value("fluid.collection_m3", truth=True), 1e-6)
        self.assertIsNone(self.value("chemistry.recovery_fraction"))
        self.assert_conserved()

    def test_method_repetitions_and_large_step_match_partitioned_progress(self):
        self.method(repetitions=2)
        self.host.create_instance("other")
        self.method(instance="other", repetitions=2)
        self.accepted("method.start")
        self.accepted("method.start", instance="other")
        self.host.step(200)
        for instance in ("spe", "other"):
            self.assertEqual(self.value("method.completed_strokes", instance=instance), 6)
            self.assertAlmostEqual(self.value("fluid.waste_m3", truth=True, instance=instance), 4e-6)
            self.assertAlmostEqual(self.value("fluid.collection_m3", truth=True, instance=instance), 2e-6)
            self.assert_conserved(instance)
        # A separate trusted world executes the same method in smaller steps.
        saved_host, saved_agent, saved_judge = self.host, self.agent, self.judge
        self.host = ExtraheraHost(); self.host.create_instance("spe")
        self.host.register_principal("learner"); self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.method(repetitions=2); self.accepted("method.start")
        for _ in range(200): self.host.step(1)
        self.assertEqual(self.value("method.completed_strokes"), 6)
        for name in ("sources", "pipette", "extraction", "waste", "collection"):
            expected = saved_judge.read(instance_id="spe", state_ids=["fluid." + name + "_m3"])["observations"]["fluid." + name + "_m3"]["value"]
            self.assertAlmostEqual(self.value("fluid." + name + "_m3", truth=True), expected, delta=1e-17)

    def test_method_requires_order_positive_explicit_rates_and_valid_repetitions(self):
        steps = self.method()
        for replacement in (list(reversed(steps)), [steps[0], steps[0], steps[2]], steps[:2]):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("method.configure", steps=replacement)["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())
        for key, value in (("flow_m3_s", 0), ("drain_flow_m3_s", -1), ("repetitions", True), ("repetitions", 0), ("repetitions", 1001), ("volume_m3", 2e-6)):
            bad = copy.deepcopy(steps); bad[0][key] = value
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("method.configure", steps=bad)["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_method_insufficient_cumulative_source_is_checked_before_transfer(self):
        steps = self.method(repetitions=4)
        for step in steps: step["solvent_position"] = "load"
        self.ok("method.configure", steps=steps)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("method.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_method_stop_and_poweroff_retain_partial_effects(self):
        self.method()
        op = self.accepted("method.start")
        self.host.step(35)
        self.ok("method.stop")
        self.assertEqual(self.agent.poll(instance_id="spe", operation_id=op)["status"], "cancelled")
        self.assertEqual(self.value("method.completed_strokes"), 1)
        self.assertAlmostEqual(self.value("fluid.pipette_m3", truth=True), .5e-6)
        self.assertAlmostEqual(self.value("fluid.waste_m3", truth=True), 1e-6)
        self.host.step(500)
        self.assertEqual(self.value("method.completed_strokes"), 1)
        self.assert_conserved()
        self.assertEqual(self.invoke("method.start")["error"]["code"], "PRECONDITION_FAILED")
        self.ok("power.set", enabled=False)
        self.assertEqual(self.value("pressure.target_pa"), 0)
        self.assert_conserved()

    def test_poweroff_cancels_active_transfer_without_deleting_liquid(self):
        self.configure()
        op = self.accepted("sample.aspirate", solvent_position="load", volume_m3=1e-6, flow_m3_s=1e-7)
        self.host.step(3)
        self.ok("power.set", enabled=False)
        self.host.step(500)
        self.assertEqual(self.agent.poll(instance_id="spe", operation_id=op)["status"], "cancelled")
        self.assertAlmostEqual(self.value("fluid.pipette_m3", truth=True), .3e-6)
        self.assert_conserved()

    def test_utility_loss_fails_on_next_host_step_without_fake_flow(self):
        self.method()
        op = self.accepted("method.start")
        self.host.step(12)
        retained = self.total()
        self.host.set_utilities("spe", vacuum_ready=False, compressed_air_ready=True, nitrogen_ready=False, evidence="engineering_assumption: synthetic source loss")
        self.assertEqual(self.value("method.status"), "running")
        self.host.step(1)
        polled = self.agent.poll(instance_id="spe", operation_id=op)
        self.assertEqual(polled["status"], "failed")
        self.assertEqual(polled["error"]["side_effects"], "partial")
        self.assertEqual(self.value("pressure.target_pa"), 0)
        self.assertAlmostEqual(self.total(), retained)
        self.assert_conserved()

    def test_busy_requests_reject_without_changes(self):
        self.method()
        self.accepted("method.start")
        for cap, args in (("gantry.move", {"x_m": 0, "y_m": 0, "z_m": 0}), ("method.start", {}), ("pressure.set", {"pressure_pa": 0}), ("sample.aspirate", {"solvent_position": "load", "volume_m3": 1e-6, "flow_m3_s": 1e-7})):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], "BUSY")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_requests_are_deduplicated_conflicting_parameters_rejected(self):
        self.configure()
        kwargs = {"instance_id": "spe", "capability_id": "sample.aspirate", "arguments": {"solvent_position": "load", "volume_m3": 1e-6, "flow_m3_s": 1e-7}, "request_id": "same"}
        response = self.agent.invoke(**kwargs)
        self.host.step(2)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**kwargs), response)
        response["result"]["control_status"] = "corrupted"
        self.assertEqual(self.agent.invoke(**kwargs)["result"]["control_status"], "started")
        conflict = self.agent.invoke(**{**kwargs, "arguments": {**kwargs["arguments"], "volume_m3": .5e-6}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())
        op = self.value("operation.active_id")
        cancelled = self.agent.cancel(instance_id="spe", operation_id=op, request_id="same_cancel")
        self.host.step(1)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.cancel(instance_id="spe", operation_id=op, request_id="same_cancel"), cancelled)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_operation_and_instance_scopes_and_host_identity(self):
        self.configure()
        self.host.create_instance("other")
        self.host.register_principal("limited", instance_ids=["other"])
        limited = self.host.session("limited")
        op = self.accepted("sample.aspirate", solvent_position="load", volume_m3=1e-6, flow_m3_s=1e-7)
        other_before = copy.deepcopy(self.host.debug_snapshot()["instances"]["other"])
        self.host.step(4)
        self.assertEqual(self.host.debug_snapshot()["instances"]["other"], other_before)
        for response in (limited.read(instance_id="spe"), limited.describe(instance_id="spe"), limited.invoke(instance_id="spe", capability_id="power.set", arguments={"enabled": False}, request_id="x")):
            self.assertEqual(response["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(self.judge.poll(instance_id="spe", operation_id=op)["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.agent.poll(instance_id="other", operation_id=op)["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.judge.cancel(instance_id="spe", operation_id=op, request_id="steal")["error"]["code"], "ACCESS_DENIED")
        with self.assertRaises(ValueError): self.host.session("self_proclaimed_evaluator")
        with self.assertRaises(ValueError): self.host.register_principal("learner", visibility="evaluator")
        self.assertFalse(hasattr(self.agent, "step"))
        self.assertNotIn("step", self.agent.describe(instance_id="spe")["capabilities"])

    def test_truth_filtered_from_describe_read_invoke_logs_and_scene(self):
        self.configure()
        private = {s for s, slot in EXTRAHERA_STATE_SLOTS.items() if slot["visibility"] != "agent"}
        for sid in private:
            for response in (self.agent.read(instance_id="spe", state_ids=[sid]), self.agent.write(instance_id="spe", state_id=sid, value=0, request_id="deny")):
                self.assertEqual(response["error"]["code"], "ACCESS_DENIED")
                self.assertNotIn(sid, json.dumps(response))
        self.accepted("sample.aspirate", solvent_position="load", volume_m3=1e-6, flow_m3_s=1e-7)
        self.host.step(3)
        self.host.sync_scene()
        public = json.dumps([self.agent.describe(instance_id="spe"), self.agent.read(instance_id="spe"), self.agent.logs(), self.calls])
        for sid in private: self.assertNotIn(sid, public)
        self.assertNotIn("synthetic inventories", public)
        self.assertEqual(set(self.judge.read(instance_id="spe")["observations"]), set(EXTRAHERA_STATE_SLOTS))
        for _, updates in self.calls: self.assertTrue(set(updates) <= set(EXTRAHERA_SCENE_STATE_IDS))

    def test_write_is_capability_routed_and_cannot_modify_truth_or_atomic_gantry(self):
        self.agent.write(instance_id="spe", state_id="power.enabled", value=True, request_id="w1")
        self.assertTrue(self.value("power.enabled"))
        response = self.agent.write(instance_id="spe", state_id="manifold.z_m", value=.07, request_id="w2")
        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(self.value("manifold.z_m"), .07)
        before = self.host.debug_snapshot()
        for sid in ("gantry.x_m", "fluid.pipette_m3", "pressure.indicated_pa"):
            self.assertEqual(self.agent.write(instance_id="spe", state_id=sid, value=0, request_id="w3")["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.write(instance_id="spe", state_id="pressure.target_pa", value=5000, request_id="w4")["error"]["code"], "INVALID_ARGUMENT")

    def test_command_scene_failure_does_not_commit_process_or_leak_backend_error(self):
        self.ok("power.set", enabled=True)
        self.host.scene_adapter = lambda i, u: (_ for _ in ()).throw(RuntimeError("private_backend_marker"))
        before = self.host.debug_snapshot()
        result = self.invoke("gantry.move", x_m=.1, y_m=.1, z_m=.1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["side_effects"], "unknown")
        self.assertNotIn("private_backend_marker", json.dumps(result))
        self.assertEqual(before, self.host.debug_snapshot())

    def test_host_step_scene_failure_retains_clock_and_committed_liquid(self):
        self.method()
        self.accepted("method.start")
        self.host.scene_adapter = lambda i, u: (_ for _ in ()).throw(RuntimeError("private_backend_marker"))
        with self.assertRaises(ExtraheraSceneSynchronizationError): self.host.step(30)
        self.assertEqual(self.host.time_s, 30)
        self.assertEqual(self.value("method.phase"), "wash")
        self.assertAlmostEqual(self.value("fluid.waste_m3", truth=True), 1e-6)
        self.assert_conserved()
        self.assertTrue(self.host.debug_snapshot()["scene_faults"])
        self.assertNotIn("private_backend_marker", json.dumps(self.agent.logs()))

    def test_host_configuration_validates_and_creates_no_internal_pump(self):
        for source, typ in (("", EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID), ("spe", EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID), ("external", "instrument.compressor")):
            with self.assertRaises(ValueError): self.host.connect_vacuum_source("spe", source, asset_type_id=typ)
        with self.assertRaises(ValueError): self.host.set_utilities("spe", vacuum_ready=True, compressed_air_ready=True, nitrogen_ready=True, evidence="fixture")
        self.configure()
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError): self.host.initialize_liquids("spe", {"load": 10}, evidence="replacement")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(set(self.host.debug_snapshot()["instances"]), {"spe"})
        self.assertEqual(self.value("vacuum.source_instance_id"), "shared_pc3001")
        self.assertEqual(self.value("vacuum.source_asset_type_id"), EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID)

    def test_invalid_host_time_zero_step_and_input_are_pure(self):
        before = self.host.debug_snapshot()
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        for dt in (-1, True, float("nan"), float("inf"), 3601, 10 ** 500):
            with self.assertRaises(ValueError): self.host.step(dt)
            self.assertEqual(before, self.host.debug_snapshot())
        for values in ({"s": -1}, {"s": True}, {"s": float("nan")}, {"": 1}, {}):
            with self.assertRaises(ValueError): self.host.initialize_liquids("spe", values, evidence="fixture")
            self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(ValueError): self.host.initialize_liquids("spe", {"s": 1}, evidence="")

    def test_protocol_validation_and_returned_copy_integrity(self):
        for kwargs in ({}, {"asset_type_id": EXTRAHERA_ASSET_TYPE_ID, "instance_id": "spe"}, {"asset_type_id": "wrong"}): self.assertEqual(self.agent.describe(**kwargs)["error"]["code"], "INVALID_ARGUMENT")
        for ids in ("power.enabled", ["power.enabled", "power.enabled"], [7]): self.assertEqual(self.agent.read(instance_id="spe", state_ids=ids)["error"]["code"], "INVALID_ARGUMENT")
        for req in ("", None, 1, "x" * 257): self.assertEqual(self.agent.invoke(instance_id="spe", capability_id="power.set", arguments={"enabled": True}, request_id=req)["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.invoke("sim.step", dt_s=1)["error"]["code"], "UNKNOWN_CAPABILITY")
        self.assertEqual(self.invoke("power.set", enabled=True, extra=True)["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.invoke("power.set", enabled=1)["error"]["code"], "INVALID_ARGUMENT")
        observed = self.agent.read(instance_id="spe"); observed["observations"]["power.enabled"]["value"] = True
        self.assertFalse(self.value("power.enabled"))
        logs = self.agent.logs(); logs.clear(); self.assertTrue(self.agent.logs())

    def test_metadata_schemas_and_actual_capability_result_coverage(self):
        spec = export_extrahera_contract_metadata()["asset_types"][EXTRAHERA_ASSET_TYPE_ID]
        for cap, info in spec["capabilities"].items():
            self.assertEqual(lint_schema(info["input_schema"]), [], cap)
            self.assertEqual(lint_schema(info["output_schema"]), [], cap)
            self.assertEqual(validate_value(info["example_arguments"][0], info["input_schema"]), [], cap)
        self.configure()
        calls = [self.ok("power.set", enabled=True), self.ok("gantry.move", x_m=.1, y_m=.1, z_m=.1), self.ok("manifold.set_position", z_m=0), self.ok("pressure.set", pressure_pa=100000)]
        caps = ["power.set", "gantry.move", "manifold.set_position", "pressure.set"]
        for cap in ("sample.aspirate", "sample.dispense"):
            result = self.invoke(cap, **spec["capabilities"][cap]["example_arguments"][0]); self.assertEqual(result["status"], "accepted")
            calls.append(result); caps.append(cap); self.host.step(20)
        for cap in ("method.configure", "method.start", "method.stop"):
            result = self.invoke(cap, **spec["capabilities"][cap]["example_arguments"][0]); self.assertIn(result["status"], ("accepted", "succeeded"))
            calls.append(result); caps.append(cap)
        for cap, response in zip(caps, calls):
            self.assertEqual(validate_value(response["result"], spec["capabilities"][cap]["output_schema"]), [], cap)
            self.assertEqual(set(response), {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"})
        for sid, observation in self.judge.read(instance_id="spe")["observations"].items():
            self.assertEqual(validate_value(observation["value"], spec["state_slots"][sid]["value_schema"]), [], sid)
            self.assertEqual(observation["unit"], spec["state_slots"][sid]["unit"])
        parameters = spec["parameters"]
        self.assertEqual(parameters["accuracy_fraction_at_1000ul"]["reference_conditions"], "only at 1000 uL; not a noise model")
        self.assertIsNone(parameters["recovery_fraction"]["value"])


# CHEM-031 timing metadata must describe both acknowledgement and completion.
def _extrahera_test_async_completion_timing(self):
    caps = export_extrahera_contract_metadata()["asset_types"][EXTRAHERA_ASSET_TYPE_ID]["capabilities"]
    for cap, states in {"sample.aspirate": ("sample.status", "operation.active_id"), "sample.dispense": ("sample.status", "operation.active_id"), "method.start": ("method.status", "method.phase", "operation.active_id")}.items():
        for sid in states:
            timings = {effect["timing"] for effect in caps[cap]["state_effects"] if effect["state_id"] == sid}
            self.assertEqual(timings, {"immediate", "on_step"})


Basic40ExtraheraTests.test_async_completion_timing_is_declared = _extrahera_test_async_completion_timing
