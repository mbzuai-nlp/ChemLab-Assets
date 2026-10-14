"""CHEM-185 synthetic runtime tests; recording callbacks are not geometry QA."""
import copy
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/chemistry-lab-assets/scripts"))
from asset_tool import lint_schema, validate_value
from basic40_runtime import SceneSynchronizationError
from basic40_runtime_thermal import (NABERTHERM_TYPE, NABERTHERM_CAPACITY_J_K,
    NABERTHERM_MAX_K, NaberthermL9Runtime, export_nabertherm_contract_metadata,
    nabertherm_calibration_report)


class NaberthermL9RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = NaberthermL9Runtime(scene_adapter=lambda instance, updates: self.calls.append((instance, copy.deepcopy(updates))))
        self.host.create_instance("furnace_a")
        self.host.create_instance("furnace_b")
        self.host.register_principal("learner", visibility="agent", instance_ids=["furnace_a"])
        self.host.register_principal("evaluator", visibility="evaluator")
        self.agent = self.host.session("learner")
        self.judge = self.host.session("evaluator")
        self.counter = 0

    def invoke(self, cap, **arguments):
        self.counter += 1
        return self.agent.invoke(instance_id="furnace_a", capability_id=cap, arguments=arguments, request_id=f"req_{self.counter}")

    def ok(self, cap, **arguments):
        result = self.invoke(cap, **arguments)
        self.assertEqual(result["status"], "succeeded", result)
        return result

    def value(self, sid, instance="furnace_a"):
        return self.judge.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def heat(self, target=NABERTHERM_MAX_K):
        self.ok("power.set", enabled=True)
        self.ok("thermal.set_temperature", temperature_K=target)

    def program(self, segments):
        self.ok("power.set", enabled=True)
        self.ok("thermal.configure_program", segments=segments)
        self.ok("thermal.start_program")

    def test_catalogue_heatup_1000C_4500s_and_independent_1100C_endpoint(self):
        self.heat()
        self.host.step(4500)
        self.assertAlmostEqual(self.value("thermal.temperature_k"), 1273.15, delta=1e-9)
        self.assertAlmostEqual(self.value("thermal.indicated_temperature_k"), 1273.15, delta=1e-9)
        self.host.step(900)
        self.assertAlmostEqual(self.value("thermal.temperature_k"), 1373.15, delta=1e-9)
        report = nabertherm_calibration_report()
        self.assertFalse(report["whole_mass_used_as_component_capacity"])
        self.assertGreater(NABERTHERM_CAPACITY_J_K, 10000)
        self.assertLess(NABERTHERM_CAPACITY_J_K, 11000)
        self.assertEqual(report["rated_power_W"], 3400)

    def test_setpoint_does_not_advance_temperature_and_read_is_pure(self):
        self.ok("power.set", enabled=True)
        before = self.host.debug_snapshot()
        self.ok("thermal.set_temperature", temperature_K=1273.15)
        after = self.host.debug_snapshot()
        old_state, state = before["instances"]["furnace_a"]["state"], after["instances"]["furnace_a"]["state"]
        self.assertEqual([sid for sid in state if state[sid] != old_state[sid]], ["thermal.target_k"])
        for _ in range(4):
            self.agent.read(instance_id="furnace_a")
            self.agent.describe(instance_id="furnace_a")
        self.assertEqual(after, self.host.debug_snapshot())
        self.assertEqual(self.host.time_s, 0)
        self.host.step(60)
        self.assertGreater(self.value("thermal.temperature_k"), 298.15)
        self.assertLess(self.value("thermal.temperature_k"), 1273.15)

    def test_common_clock_and_two_instances_are_independent(self):
        self.heat()
        self.host.step(100)
        self.assertEqual(self.value("thermal.temperature_k", "furnace_b"), 298.15)
        self.assertFalse(self.value("power.enabled", "furnace_b"))
        self.assertGreater(self.value("thermal.temperature_k"), 298.15)
        for instance in ("furnace_a", "furnace_b"):
            self.assertEqual(self.judge.read(instance_id=instance)["clock"]["time_s"], 100)
        denied = self.agent.read(instance_id="furnace_b")
        self.assertEqual(denied["error"]["code"], "UNKNOWN_INSTANCE")

    def test_door_is_host_authority_with_heat_inhibition_and_cooling(self):
        self.heat()
        self.host.step(1000)
        hot = self.value("thermal.temperature_k")
        self.ok("door.open")
        self.assertEqual(self.value("door.angle_rad"), math.pi / 2)
        self.assertFalse(self.value("door.closed"))
        self.assertFalse(self.value("heater.enabled"))
        self.assertEqual(self.calls[-1][1]["door.angle_rad"], math.pi / 2)
        self.host.step(100)
        cooled = self.value("thermal.temperature_k")
        self.assertLess(cooled, hot)
        self.ok("door.close")
        self.assertEqual(self.value("door.angle_rad"), 0)
        self.assertTrue(self.value("door.closed"))
        self.assertTrue(self.value("heater.enabled"))
        self.host.step(100)
        self.assertGreater(self.value("thermal.temperature_k"), cooled)
        self.assertEqual(self.value("door.angle_rad", "furnace_b"), 0)

    def test_power_off_cools_without_instant_reset(self):
        self.heat()
        self.host.step(1000)
        hot = self.value("thermal.temperature_k")
        self.ok("power.set", enabled=False)
        self.assertFalse(self.value("heater.enabled"))
        self.assertEqual(self.value("thermal.temperature_k"), hot)
        self.host.step(500)
        self.assertTrue(298.15 < self.value("thermal.temperature_k") < hot)

    def test_heating_cannot_exceed_input_energy_and_partition_is_consistent(self):
        self.heat()
        self.host.step(10)
        rise = self.value("thermal.temperature_k") - 298.15
        self.assertLessEqual(rise * NABERTHERM_CAPACITY_J_K, 3400 * 10 + 1e-8)
        self.host.step(4490)
        one_chunk = self.value("thermal.temperature_k")
        second = NaberthermL9Runtime()
        second.create_instance("second")
        second.invoke(instance_id="second", capability_id="power.set", arguments={"enabled": True}, request_id="power")
        second.invoke(instance_id="second", capability_id="thermal.set_temperature", arguments={"temperature_K": NABERTHERM_MAX_K}, request_id="temp")
        for _ in range(75):
            second.step(60)
        self.assertAlmostEqual(one_chunk, second.debug_snapshot()["instances"]["second"]["state"]["thermal.temperature_k"], delta=1e-8)

    def test_invalid_inputs_do_not_change_state_time_or_scene(self):
        self.heat()
        invalid = [("thermal.set_temperature", {"temperature_K": value}) for value in
            (True, "1273.15", -1, 298.14, 1373.16, math.inf, math.nan, 10**400)]
        invalid += [("power.set", {"enabled": 1}), ("door.open", {"angle_rad": -1}),
            ("thermal.set_temperature", {"temperature_K": 800, "role": "evaluator"}),
            ("thermal.configure_program", {"segments": [{"temperature_K": 500, "ramp_rate_K_s": 0, "hold_s": 1}]}),
            ("thermal.configure_program", {"segments": []}), ("thermal.configure_program", {"segments": [None]}),
            ("thermal.configure_program", {"segments": [{"temperature_K": 500, "ramp_rate_K_s": 0.1, "hold_s": -1}]})]
        for cap, args in invalid:
            with self.subTest(cap=cap, args=str(args)[:100]):
                before, calls = self.host.debug_snapshot(), len(self.calls)
                response = self.invoke(cap, **args)
                self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(response["error"]["side_effects"], "none")
                self.assertEqual(self.host.debug_snapshot(), before)
                self.assertEqual(len(self.calls), calls)
        for value in (-1, True, math.inf, math.nan, 86401, "1", 10**400):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.step(value)
            self.assertEqual(before, self.host.debug_snapshot())

    def test_temperature_boundaries_and_off_precondition(self):
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("thermal.set_temperature", temperature_K=500)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.heat(1373.15)
        self.host.step(86400)
        self.assertEqual(self.value("thermal.temperature_k"), 1373.15)
        self.ok("thermal.set_temperature", temperature_K=298.15)
        old = self.value("thermal.temperature_k")
        self.host.step(1)
        self.assertLess(self.value("thermal.temperature_k"), old)

    def test_agent_truth_filtered_from_all_public_surfaces(self):
        self.heat()
        self.host.step(33.7)
        for session in (self.agent, self.host):
            read = session.read(instance_id="furnace_a")
            self.assertNotIn("thermal.temperature_k", json.dumps(read))
            self.assertEqual(read["observations"]["thermal.indicated_temperature_k"]["quality"], "estimated")
            self.assertNotIn("thermal.temperature_k", json.dumps(session.describe(instance_id="furnace_a")))
            denied = session.read(instance_id="furnace_a", state_ids=["thermal.temperature_k"])
            unknown = session.read(instance_id="furnace_a", state_ids=["unknown"])
            self.assertEqual(denied["error"], unknown["error"])
        self.assertNotIn("thermal.temperature_k", json.dumps(self.agent.logs()))
        with self.assertRaises(TypeError):
            self.agent.read(instance_id="furnace_a", role="evaluator")
        with self.assertRaises(ValueError):
            self.host.register_principal("learner", visibility="evaluator")
        self.assertIn("thermal.temperature_k", self.judge.read(instance_id="furnace_a")["observations"])
        copied = self.agent.read(instance_id="furnace_a")
        copied["observations"]["power.enabled"]["value"] = False
        self.assertTrue(self.value("power.enabled"))

    def test_duplicate_request_and_conflict(self):
        args = {"instance_id": "furnace_a", "capability_id": "power.set", "arguments": {"enabled": True}, "request_id": "dedup"}
        response = self.agent.invoke(**args)
        self.host.step(1)
        before, calls = self.host.debug_snapshot(), len(self.calls)
        self.assertEqual(response, self.agent.invoke(**args))
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(calls, len(self.calls))
        conflict = self.agent.invoke(**{**args, "arguments": {"enabled": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_missing_or_failed_scene_is_not_false_success(self):
        self.host.scene_adapter = None
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("door.open")["status"], "unsupported")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.describe(instance_id="furnace_a")["capabilities"]["door.open"]["availability"], "unsupported")
        def broken(*_):
            raise RuntimeError("sensitive backend exception")
        self.host.scene_adapter = broken
        failed = self.invoke("door.open")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["side_effects"], "unknown")
        self.assertNotIn("sensitive", json.dumps(failed))
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(SceneSynchronizationError):
            self.host.step(10)
        self.assertEqual(self.host.time_s, 10)

    def test_program_ramp_hold_completion_and_stop_preserve_thermal_inertia(self):
        self.program([{ "temperature_K": 303.15, "ramp_rate_K_s": 0.1, "hold_s": 10},
                      { "temperature_K": 308.15, "ramp_rate_K_s": 0.1, "hold_s": 10}])
        self.host.step(20)
        self.assertAlmostEqual(self.value("thermal.target_k"), 300.15, delta=1e-8)
        self.assertEqual(self.value("program.phase"), "ramp")
        self.assertEqual(self.value("program.hold_elapsed_s"), 0)
        self.host.step(200)
        self.assertFalse(self.value("program.running"))
        self.assertEqual(self.value("program.phase"), "completed")
        self.assertEqual(self.value("program.segment_index"), 2)
        self.assertEqual(self.value("thermal.target_k"), 298.15)
        self.assertGreater(self.value("thermal.temperature_k"), 298.15)
        self.ok("thermal.start_program")
        self.host.step(10)
        hot = self.value("thermal.temperature_k")
        self.ok("thermal.stop_program")
        self.assertEqual(self.value("program.phase"), "stopped")
        self.assertEqual(self.value("thermal.temperature_k"), hot)

    def test_program_pauses_at_open_door_and_holds_only_when_reached(self):
        self.program([{"temperature_K": 303.15, "ramp_rate_K_s": 1, "hold_s": 20}])
        self.host.step(2)
        self.assertEqual(self.value("program.hold_elapsed_s"), 0)
        self.ok("door.open")
        target = self.value("thermal.target_k")
        self.host.step(100)
        self.assertEqual(self.value("program.phase"), "paused_door")
        self.assertEqual(self.value("thermal.target_k"), target)
        self.assertEqual(self.value("program.hold_elapsed_s"), 0)
        self.ok("door.close")
        self.host.step(25)
        self.assertEqual(self.value("program.phase"), "hold")
        self.assertLess(self.value("program.hold_elapsed_s"), 20)
        self.host.step(30)
        self.assertEqual(self.value("program.phase"), "completed")

    def test_active_program_rejects_override_and_power_off_stops(self):
        self.program([{"temperature_K": 500, "ramp_rate_K_s": 0.1, "hold_s": 20}])
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("thermal.set_temperature", temperature_K=600)["error"]["code"], "BUSY")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("power.set", enabled=False)
        self.assertFalse(self.value("program.running"))
        self.assertFalse(self.value("heater.enabled"))
        self.assertEqual(self.value("program.phase"), "stopped")

    def test_program_configuration_is_copied_and_writes_cannot_advance_clock(self):
        self.ok("power.set", enabled=True)
        segments = [{"temperature_K": 500, "ramp_rate_K_s": 0.1, "hold_s": 20}]
        self.ok("thermal.configure_program", segments=segments)
        segments[0]["temperature_K"] = 900
        self.assertEqual(self.value("program.segments")[0]["temperature_K"], 500)
        observed = self.value("program.segments")
        observed[0]["hold_s"] = 0
        self.assertEqual(self.value("program.segments")[0]["hold_s"], 20)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("sim.step", dt_s=100)["error"]["code"], "UNKNOWN_CAPABILITY")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("door.open")
        self.assertEqual(self.invoke("thermal.start_program")["error"]["code"], "PRECONDITION_FAILED")

    def test_metadata_schemas_match_real_outputs_and_write_routes(self):
        spec = export_nabertherm_contract_metadata()["asset_types"][NABERTHERM_TYPE]
        self.ok("power.set", enabled=True)
        for cap, meta in spec["capabilities"].items():
            with self.subTest(cap=cap):
                self.assertEqual(lint_schema(meta["input_schema"]), [])
                self.assertEqual(lint_schema(meta["output_schema"]), [])
                args = meta["example_arguments"][0]
                self.assertEqual(validate_value(args, meta["input_schema"]), [])
                result = self.ok(cap, **args)
                self.assertEqual(validate_value(result["result"], meta["output_schema"]), [])
        for sid, observation in self.judge.read(instance_id="furnace_a")["observations"].items():
            self.assertEqual(validate_value(observation["value"], spec["state_slots"][sid]["value_schema"]), [])
        result = self.agent.write(instance_id="furnace_a", state_id="thermal.target_k", value=600, request_id="write")
        self.assertEqual(result["result"]["setpoint_K"], 600)
        denied = self.agent.write(instance_id="furnace_a", state_id="thermal.temperature_k", value=600, request_id="denied")
        self.assertEqual(denied["error"]["code"], "ACCESS_DENIED")
        before = self.host.debug_snapshot()
        for cap in spec["declared_capabilities"]:
            self.assertEqual(self.invoke(cap)["status"], "unsupported")
        self.assertEqual(before, self.host.debug_snapshot())


if __name__ == "__main__":
    unittest.main()

# BEGIN CHEM-186 TF1 tests: append-only shared thermal test module.
import copy as _tf1_test_copy
import importlib.util as _tf1_test_importlib
import json as _tf1_test_json
import math as _tf1_test_math
import pathlib as _tf1_test_pathlib
import sys as _tf1_test_sys
import unittest as _tf1_test_unittest

_TF1_TEST_ROOT = _tf1_test_pathlib.Path(__file__).resolve().parents[1]
_tf1_test_sys.path.insert(0, str(_TF1_TEST_ROOT / 'scripts'))
_tf1_test_sys.path.insert(0, str(_TF1_TEST_ROOT / 'skills' / 'chemistry-lab-assets' / 'scripts'))
from basic40_runtime_thermal import (CarboliteTF1Runtime, CarboliteTF1SceneSynchronizationError,
                                     CARBOLITE_TF1_ASSET_TYPE, CARBOLITE_TF1_PARAMETERS,
                                     CARBOLITE_TF1_STATE_SLOTS, carbolite_tf1_metadata)
from asset_tool import validate_value as _tf1_test_validate_value


class CarboliteTF1RuntimeTests(_tf1_test_unittest.TestCase):
    def setUp(self):
        self.scene_calls = []
        self.host = CarboliteTF1Runtime(scene_adapter=lambda instance, updates: self.scene_calls.append((instance, _tf1_test_copy.deepcopy(updates))))
        self.host.create_instance('tf1_a')
        self.host.create_instance('tf1_b')
        self.host.register_principal('learner', visibility='agent')
        self.host.register_principal('judge', visibility='evaluator')
        self.agent = self.host.session('learner')
        self.judge = self.host.session('judge')
        self.counter = 0

    def call(self, capability, instance='tf1_a', **arguments):
        self.counter += 1
        return self.agent.invoke(instance_id=instance, capability_id=capability, arguments=arguments, request_id='tf1_test_' + str(self.counter))

    def ok(self, capability, instance='tf1_a', **arguments):
        response = self.call(capability, instance, **arguments)
        self.assertEqual(response['status'], 'succeeded', response)
        return response

    def value(self, slot, instance='tf1_a', debug=False):
        session = self.judge if debug else self.agent
        return session.read(instance_id=instance, state_ids=[slot])['observations'][slot]['value']

    def assemble_temperature(self, target=1373.15):
        self.ok('power.set', enabled=True)
        self.ok('thermal.set_temperature', temperature_K=target)

    def remove_seals(self):
        self.ok('scene.set_end_seal', side='left', withdrawal_m=0.12)
        self.ok('scene.set_end_seal', side='right', withdrawal_m=0.12)

    def test_target_write_and_read_do_not_advance_temperature_energy_or_clock(self):
        self.ok('power.set', enabled=True)
        before = self.host.debug_snapshot()
        response = self.ok('thermal.set_temperature', temperature_K=1373.15)
        after = self.host.debug_snapshot()
        self.assertEqual(response['result'], {'setpoint_K': 1373.15})
        self.assertEqual(before['time_s'], after['time_s'])
        for slot in ('thermal.temperature_k', 'thermal.indicated_temperature_k', 'thermal.heater_energy_j', 'thermal.heat_loss_energy_j', 'thermal.stored_energy_j'):
            self.assertEqual(before['instances']['tf1_a']['state'][slot], after['instances']['tf1_a']['state'][slot])
        for _ in range(8):
            self.agent.read(instance_id='tf1_a')
            self.judge.read(instance_id='tf1_a')
            self.agent.describe(instance_id='tf1_a')
        self.assertEqual(after, self.host.debug_snapshot())

    def test_manufacturer_heatup_reference_35_minutes_to_1100_c(self):
        self.assemble_temperature()
        self.host.step(2099)
        self.assertLess(self.value('thermal.temperature_k', debug=True), 1373.15)
        self.assertGreater(self.value('thermal.temperature_k', debug=True), 1372)
        self.host.step(1)
        self.assertAlmostEqual(self.value('thermal.temperature_k', debug=True), 1373.15, places=9)
        self.assertEqual(self.host.time_s, 2100)
        self.assertAlmostEqual(self.value('thermal.heater_energy_j', debug=True), 1500 * 2100, places=6)
        self.assertAlmostEqual(self.value('thermal.indicated_temperature_k') - 273.15, 1100.0, places=8)

    def test_independent_full_power_analytic_solution_and_energy_balance(self):
        self.assemble_temperature(1473.15)
        capacity = CARBOLITE_TF1_PARAMETERS['thermal_capacity_J_K']['value']
        self.host.step(800)
        expected_temperature = 293.15 + 1500 / 0.5 * (1 - _tf1_test_math.exp(-0.5 * 800 / capacity))
        self.assertAlmostEqual(self.value('thermal.temperature_k', debug=True), expected_temperature, places=10)
        self.assertAlmostEqual(self.value('thermal.heater_energy_j', debug=True), 1_200_000, places=6)
        expected_stored = capacity * (expected_temperature - 293.15)
        self.assertAlmostEqual(self.value('thermal.stored_energy_j', debug=True), expected_stored, places=6)
        self.assertAlmostEqual(self.value('thermal.heat_loss_energy_j', debug=True), 1_200_000 - expected_stored, places=6)
        self.assertGreater(self.value('thermal.heat_loss_energy_j', debug=True), 0)

    def test_hold_power_equals_losses_and_no_target_overshoot(self):
        self.assemble_temperature()
        self.host.step(2100)
        heater_before = self.value('thermal.heater_energy_j', debug=True)
        lost_before = self.value('thermal.heat_loss_energy_j', debug=True)
        stored_before = self.value('thermal.stored_energy_j', debug=True)
        self.host.step(1000)
        self.assertEqual(self.value('thermal.temperature_k', debug=True), 1373.15)
        self.assertAlmostEqual(self.value('thermal.heater_energy_j', debug=True) - heater_before, 540_000, places=6)
        self.assertAlmostEqual(self.value('thermal.heat_loss_energy_j', debug=True) - lost_before, 540_000, places=6)
        self.assertEqual(self.value('thermal.stored_energy_j', debug=True), stored_before)

    def test_power_off_cooling_releases_energy_without_heating(self):
        self.assemble_temperature()
        self.host.step(2100)
        self.ok('power.set', enabled=False)
        heater_before = self.value('thermal.heater_energy_j', debug=True)
        stored_before = self.value('thermal.stored_energy_j', debug=True)
        loss_before = self.value('thermal.heat_loss_energy_j', debug=True)
        self.host.step(1800)
        capacity = CARBOLITE_TF1_PARAMETERS['thermal_capacity_J_K']['value']
        expected = 293.15 + 1080 * _tf1_test_math.exp(-0.5 * 1800 / capacity)
        self.assertAlmostEqual(self.value('thermal.temperature_k', debug=True), expected, places=10)
        self.assertEqual(self.value('thermal.heater_energy_j', debug=True), heater_before)
        self.assertAlmostEqual(self.value('thermal.heat_loss_energy_j', debug=True) - loss_before, stored_before - self.value('thermal.stored_energy_j', debug=True), places=6)
        self.assertGreater(expected, 293.15)

    def test_downward_setpoint_cools_then_holds_and_conserves(self):
        self.assemble_temperature()
        self.host.step(2100)
        self.ok('thermal.set_temperature', temperature_K=1200)
        self.assertEqual(self.value('thermal.temperature_k', debug=True), 1373.15)
        self.host.step(3600)
        self.assertEqual(self.value('thermal.temperature_k', debug=True), 1200)
        self.assertAlmostEqual(self.value('thermal.heater_energy_j', debug=True) - self.value('thermal.heat_loss_energy_j', debug=True), self.value('thermal.stored_energy_j', debug=True), places=6)
        self.ok('thermal.set_temperature', temperature_K=293.15)
        self.host.step(3600)
        self.assertGreater(self.value('thermal.temperature_k', debug=True), 293.15)
        self.assertLess(self.value('thermal.temperature_k', debug=True), 1200)

    def test_coarse_and_fine_step_agree_across_target_crossing(self):
        fine = CarboliteTF1Runtime()
        fine.create_instance('fine')
        fine.register_principal('judge', visibility='evaluator')
        observer = fine.session('judge')
        observer.invoke(instance_id='fine', capability_id='power.set', arguments={'enabled': True}, request_id='on')
        observer.invoke(instance_id='fine', capability_id='thermal.set_temperature', arguments={'temperature_K': 1373.15}, request_id='temp')
        self.assemble_temperature()
        self.host.step(3600)
        for _ in range(120):
            fine.step(30)
        state_fine = fine.debug_snapshot()['instances']['fine']['state']
        for slot in ('thermal.temperature_k', 'thermal.heater_energy_j', 'thermal.heat_loss_energy_j', 'thermal.stored_energy_j'):
            self.assertAlmostEqual(self.value(slot, debug=True), state_fine[slot], delta=1e-6)

    def test_two_instances_are_independent_with_one_shared_clock(self):
        self.assemble_temperature()
        self.host.step(600)
        self.assertGreater(self.value('thermal.temperature_k', debug=True), 293.15)
        self.assertEqual(self.value('thermal.temperature_k', 'tf1_b', True), 293.15)
        self.assertEqual(self.value('thermal.heater_energy_j', 'tf1_b', True), 0)
        self.assertFalse(self.value('power.enabled', 'tf1_b'))
        self.assertEqual(self.agent.read(instance_id='tf1_a')['clock'], self.agent.read(instance_id='tf1_b')['clock'])
        self.assertEqual(self.host.time_s, 600)

    def test_invalid_nonfinite_and_out_of_range_arguments_leave_state_unchanged(self):
        self.assemble_temperature()
        cases = [('thermal.set_temperature', {'temperature_K': value}) for value in (float('nan'), float('inf'), -float('inf'), True, None, '1100', 10**1000, 293.14, 1473.16)]
        cases += [('power.set', {'enabled': value}) for value in (1, 'true', None)]
        cases += [('scene.set_end_seal', {'side': 'left', 'withdrawal_m': value}) for value in (-0.1, .12001, float('nan'), True)]
        cases += [('scene.set_end_seal', {'side': 'front', 'withdrawal_m': 0}), ('thermal.set_temperature', {'temperature_K': 1100, 'role': 'evaluator'}), ('scene.set_work_tube', {'withdrawal_m': .800001}), ('scene.set_gas_connection', {'port_id': 'gas_inlet', 'connected': 1})]
        for capability, arguments in cases:
            before = self.host.debug_snapshot()
            response = self.call(capability, **arguments)
            self.assertEqual(response['status'], 'rejected', response)
            self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT', response)
            self.assertEqual(before, self.host.debug_snapshot())

    def test_invalid_host_steps_leave_all_state_and_time_unchanged(self):
        self.assemble_temperature()
        for value in (-1, 3600.1, float('nan'), float('inf'), True, None, 10**1000):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.step(value)
            self.assertEqual(before, self.host.debug_snapshot())
        before = self.host.debug_snapshot()
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_scene_disassembly_prerequisites_and_callback_coordinates(self):
        before = self.host.debug_snapshot()
        response = self.call('scene.set_work_tube', withdrawal_m=.8)
        self.assertEqual(response['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok('scene.set_gas_connection', port_id='gas_inlet', connected=True)
        before = self.host.debug_snapshot()
        self.assertEqual(self.call('scene.set_end_seal', side='left', withdrawal_m=.12)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok('scene.set_gas_connection', port_id='gas_inlet', connected=False)
        self.remove_seals()
        self.ok('scene.set_work_tube', withdrawal_m=.8)
        self.assertEqual(self.scene_calls[-1], ('tf1_a', {'work_tube.withdrawal_m': .8}))
        before = self.host.debug_snapshot()
        for capability, arguments in (('power.set', {'enabled': True}), ('scene.set_end_seal', {'side': 'right', 'withdrawal_m': 0}), ('scene.set_gas_connection', {'port_id': 'gas_outlet', 'connected': True})):
            self.assertEqual(self.call(capability, **arguments)['error']['code'], 'PRECONDITION_FAILED')
            self.assertEqual(before, self.host.debug_snapshot())
        self.ok('scene.set_work_tube', withdrawal_m=0)
        self.ok('scene.set_end_seal', side='left', withdrawal_m=0)
        self.ok('scene.set_end_seal', side='right', withdrawal_m=0)
        self.ok('power.set', enabled=True)
        before = self.host.debug_snapshot()
        self.assertEqual(self.call('scene.set_end_seal', side='left', withdrawal_m=.12)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertIn(('tf1_a', {'end_seal.left.withdrawal_m': .12}), self.scene_calls)
        self.assertIn(('tf1_a', {'end_seal.right.withdrawal_m': .12}), self.scene_calls)

    def test_no_scene_backend_is_explicitly_unsupported(self):
        host = CarboliteTF1Runtime()
        host.create_instance('plain')
        host.register_principal('agent', visibility='agent')
        agent = host.session('agent')
        metadata = agent.describe(instance_id='plain')
        for capability, arguments in (('scene.set_end_seal', {'side': 'left', 'withdrawal_m': .1}), ('scene.set_work_tube', {'withdrawal_m': 0}), ('scene.set_gas_connection', {'port_id': 'gas_inlet', 'connected': True})):
            before = host.debug_snapshot()
            response = agent.invoke(instance_id='plain', capability_id=capability, arguments=arguments, request_id=capability)
            self.assertEqual(response['status'], 'unsupported')
            self.assertEqual(metadata['capabilities'][capability]['availability'], 'unsupported')
            self.assertEqual(before, host.debug_snapshot())

    def test_scene_command_failure_keeps_internal_state_and_hides_backend_text(self):
        def broken(instance, updates):
            raise RuntimeError('PRIVATE_FAKE_PROCESS_SECRET')
        self.host.scene_adapter = broken
        before = self.host.debug_snapshot()
        response = self.call('scene.set_end_seal', side='left', withdrawal_m=.12)
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['side_effects'], 'unknown')
        self.assertNotIn('PRIVATE_FAKE_PROCESS_SECRET', _tf1_test_json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())

    def test_scene_step_failure_commits_time_and_energy_then_can_resync(self):
        self.assemble_temperature()
        def broken(instance, updates):
            raise RuntimeError('PRIVATE_FAKE_PROCESS_SECRET')
        self.host.scene_adapter = broken
        with self.assertRaises(CarboliteTF1SceneSynchronizationError):
            self.host.step(10)
        self.assertEqual(self.host.time_s, 10)
        self.assertAlmostEqual(self.value('thermal.heater_energy_j', debug=True), 15000, places=8)
        self.assertGreater(self.value('thermal.temperature_k', debug=True), 293.15)
        self.assertEqual(len(self.host.debug_snapshot()['scene_faults']), 1)
        self.host.scene_adapter = lambda instance, updates: self.scene_calls.append((instance, _tf1_test_copy.deepcopy(updates)))
        self.host.sync_scene()
        self.assertEqual(self.host.time_s, 10)
        self.assertEqual(set(self.scene_calls[-1][1]), set(CARBOLITE_TF1_STATE_SLOTS))

    def test_gas_connection_is_state_only_with_unavailable_flow_and_pressure(self):
        self.ok('scene.set_gas_connection', port_id='gas_inlet', connected=True)
        self.ok('scene.set_gas_connection', port_id='gas_outlet', connected=True)
        self.assemble_temperature()
        self.host.step(100)
        for slot in ('gas.flow_rate_m3_s', 'gas.pressure_pa'):
            observation = self.judge.read(instance_id='tf1_a', state_ids=[slot])['observations'][slot]
            self.assertIsNone(observation['value'])
            self.assertEqual(observation['quality'], 'unavailable')
            self.assertIn('reason', observation)
        self.assertTrue(self.value('gas.inlet.connected'))
        self.assertTrue(self.value('gas.outlet.connected'))
        self.ok('scene.set_gas_connection', port_id='gas_outlet', connected=False)
        self.assertTrue(self.value('gas.inlet.connected'))
        self.assertFalse(self.value('gas.outlet.connected'))

    def test_agent_truth_filter_in_read_describe_errors_logs_and_write(self):
        self.assemble_temperature()
        self.host.step(125)
        restricted = [sid for sid, slot in CARBOLITE_TF1_STATE_SLOTS.items() if slot['visibility'] != 'agent']
        observation_ids = set(self.agent.read(instance_id='tf1_a')['observations'])
        metadata = self.agent.describe(instance_id='tf1_a')
        for sid in restricted:
            self.assertNotIn(sid, observation_ids)
            self.assertNotIn(sid, metadata['state_slots'])
            self.assertEqual(self.agent.read(instance_id='tf1_a', state_ids=[sid])['error']['code'], 'ACCESS_DENIED')
            self.assertEqual(self.agent.write(instance_id='tf1_a', state_id=sid, value=0, request_id='write_' + sid)['error']['code'], 'ACCESS_DENIED')
            self.assertNotIn(sid, _tf1_test_json.dumps(self.agent.logs()))
            for cap in metadata['capabilities'].values():
                self.assertNotIn(sid, [effect['state_id'] for effect in cap['state_effects']])
        display = self.agent.read(instance_id='tf1_a', state_ids=['thermal.indicated_temperature_k'])['observations']['thermal.indicated_temperature_k']
        self.assertEqual(display['quality'], 'estimated')
        self.assertNotIn('sim.step', metadata['capabilities'])

    def test_principal_scope_and_identity_cannot_be_chosen_in_arguments(self):
        self.host.register_principal('scoped', visibility='agent', instance_ids=['tf1_b'])
        limited = self.host.session('scoped')
        for response in (limited.read(instance_id='tf1_a'), limited.describe(instance_id='tf1_a'), limited.invoke(instance_id='tf1_a', capability_id='power.set', arguments={'enabled': True}, request_id='restricted')):
            self.assertEqual(response['error']['code'], 'UNKNOWN_INSTANCE')
            self.assertIsNone(response['state_revision'])
        response = self.call('power.set', enabled=True, role='evaluator')
        self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT')
        with self.assertRaises(ValueError):
            self.host.session('invented_evaluator')

    def test_deduplicated_requests_and_conflicts_do_not_reexecute(self):
        request = {'instance_id': 'tf1_a', 'capability_id': 'power.set', 'arguments': {'enabled': True}, 'request_id': 'fixed'}
        original = self.agent.invoke(**request)
        self.host.step(2)
        before = self.host.debug_snapshot()
        calls = len(self.scene_calls)
        self.assertEqual(self.agent.invoke(**request), original)
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(calls, len(self.scene_calls))
        conflict = self.agent.invoke(**{**request, 'arguments': {'enabled': False}})
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_write_routes_fixed_side_and_port_arguments(self):
        response = self.agent.write(instance_id='tf1_a', state_id='end_seal.right.withdrawal_m', value=.12, request_id='write_right')
        self.assertEqual(response['result'], {'side': 'right', 'withdrawal_m': .12})
        self.assertEqual(self.value('end_seal.left.withdrawal_m'), 0)
        self.assertEqual(self.value('end_seal.right.withdrawal_m'), .12)
        response = self.agent.write(instance_id='tf1_a', state_id='gas.inlet.connected', value=True, request_id='write_inlet')
        self.assertEqual(response['result'], {'port_id': 'gas_inlet', 'connected': True})
        self.assertTrue(self.value('gas.inlet.connected'))
        self.assertFalse(self.value('gas.outlet.connected'))

    def test_declared_capability_input_output_schemas_match_real_calls(self):
        executed = []
        executed.append(('scene.set_gas_connection', {'port_id': 'gas_inlet', 'connected': True}, self.ok('scene.set_gas_connection', port_id='gas_inlet', connected=True)))
        self.ok('scene.set_gas_connection', port_id='gas_inlet', connected=False)
        executed.append(('scene.set_end_seal', {'side': 'left', 'withdrawal_m': .12}, self.ok('scene.set_end_seal', side='left', withdrawal_m=.12)))
        self.ok('scene.set_end_seal', side='right', withdrawal_m=.12)
        executed.append(('scene.set_work_tube', {'withdrawal_m': .8}, self.ok('scene.set_work_tube', withdrawal_m=.8)))
        self.ok('scene.set_work_tube', withdrawal_m=0)
        executed.append(('power.set', {'enabled': True}, self.ok('power.set', enabled=True)))
        executed.append(('thermal.set_temperature', {'temperature_K': 1373.15}, self.ok('thermal.set_temperature', temperature_K=1373.15)))
        for name, arguments, response in executed:
            metadata = carbolite_tf1_metadata()['capabilities'][name]
            self.assertEqual(_tf1_test_validate_value(arguments, metadata['input_schema']), [])
            self.assertEqual(_tf1_test_validate_value(response['result'], metadata['output_schema']), [])
            self.assertEqual(response['protocol_version'], '1.0.0')
            self.assertIsNone(response['operation_id'])
            self.assertIsNone(response['error'])
        self.assertEqual({name for name, _, _ in executed}, set(carbolite_tf1_metadata()['capabilities']))

    def test_observation_metadata_and_integral_window_are_correct(self):
        self.host.step(15)
        self.host.create_instance('later')
        self.ok('power.set', instance='later', enabled=True)
        self.ok('thermal.set_temperature', instance='later', temperature_K=1000)
        self.host.step(20)
        response = self.judge.read(instance_id='later')
        self.assertEqual(response['clock']['time_s'], 35)
        for slot, observation in response['observations'].items():
            self.assertEqual(observation['unit'], CARBOLITE_TF1_STATE_SLOTS[slot]['unit'])
            self.assertEqual(observation['sample_time_s'], 35)
        self.assertEqual(response['observations']['thermal.heater_energy_j']['sample_window_s'], [15, 35])
        self.assertIsNone(response['observations']['thermal.temperature_k']['sample_window_s'])

    def test_model_parameters_distinguish_manufacturer_data_assumptions_and_unknowns(self):
        p = carbolite_tf1_metadata()['parameters']
        self.assertEqual(p['heater_power_W']['value'], 1500)
        self.assertEqual(p['reference_heatup_time_s']['value'], 2100)
        self.assertEqual(p['maximum_continuous_temperature_K']['value'], 1373.15)
        self.assertEqual(p['maximum_temperature_K']['value'], 1473.15)
        self.assertEqual(p['heat_loss_W_K']['evidence'], 'assumption')
        self.assertEqual(p['thermal_capacity_J_K']['evidence'], 'assumption')
        self.assertNotEqual(p['thermal_capacity_J_K']['value'], 15000)
        for key in ('work_tube_inner_diameter_m', 'work_tube_mass_kg', 'end_seal_mass_kg', 'gas_flow_rate_m3_s', 'gas_pressure_pa'):
            self.assertIsNone(p[key]['value'])
            self.assertEqual(p[key]['evidence'], 'unknown')
# END CHEM-186 TF1 tests.


# CHEM-012 tests: unique names/imports preserve concurrent thermal asset suites.
import copy as _vd56_test_copy
import json as _vd56_test_json
import math as _vd56_test_math
import sys as _vd56_test_sys
import unittest as _vd56_test_unittest
from pathlib import Path as _VD56TestPath
_VD56_TEST_ROOT = _VD56TestPath(__file__).resolve().parents[1]
_vd56_test_sys.path.insert(0, str(_VD56_TEST_ROOT / "scripts"))
_vd56_test_sys.path.insert(0, str(_VD56_TEST_ROOT / "skills" / "chemistry-lab-assets" / "scripts"))
from basic40_runtime_thermal import BinderVD56Runtime as _VD56TestRuntime
from basic40_runtime_thermal import BinderVD56SceneSynchronizationError as _VD56TestSceneError
from basic40_runtime_thermal import export_binder_vd56_metadata as _vd56_test_metadata
from basic40_runtime_thermal import BINDER_VD56_PARAMETERS as _VD56_TEST_PARAMETERS
from asset_tool import lint_schema as _vd56_test_lint, validate_value as _vd56_test_value_schema


class BinderVD56RuntimeTests(_vd56_test_unittest.TestCase):
    def setUp(self):
        self.scene_calls = []
        self.host = _VD56TestRuntime(scene_adapter=lambda key, values: self.scene_calls.append((key, _vd56_test_copy.deepcopy(values))))
        self.host.create_instance("vd56_a")
        self.host.create_instance("vd56_b")
        self.host.register_principal("learner", visibility="agent")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.counter = 0

    def invoke(self, cap, instance="vd56_a", **args):
        self.counter += 1
        return self.agent.invoke(instance_id=instance, capability_id=cap, arguments=args, request_id=f"vd56_test_{self.counter}")

    def ok(self, cap, instance="vd56_a", **args):
        response = self.invoke(cap, instance=instance, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, sid, instance="vd56_a", truth=False):
        session = self.judge if truth else self.agent
        return session.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def connect(self, instance="vd56_a", boundary=5000.0, tau=120.0):
        self.host.configure_external_vacuum(instance, connected=True, boundary_pressure_pa=boundary, time_constant_s=tau, evidence="Synthetic explicit external boundary; private_configuration_marker")

    def evacuate(self):
        self.connect()
        self.ok("vacuum.set_valve", opened=True)
        self.host.step(3600)

    def test_vd56_temperature_target_only_and_80_minute_calibration(self):
        self.ok("power.set", enabled=True)
        old = self.value("thermal.temperature_k", truth=True)
        response = self.ok("thermal.set_temperature", temperature_K=373.15)
        self.assertEqual(response["result"], {"setpoint_K": 373.15})
        self.assertEqual(self.value("thermal.temperature_k", truth=True), old)
        self.assertEqual(self.host.time_s, 0)
        self.host.step(3600)
        halfway = self.value("thermal.temperature_k", truth=True)
        self.assertGreater(halfway, 350)
        self.assertLess(halfway, 373.15)
        self.host.step(1199)
        self.assertLess(self.value("thermal.temperature_k", truth=True), 373.15)
        self.host.step(1)
        self.assertAlmostEqual(self.value("thermal.temperature_k", truth=True), 373.15, places=9)
        self.assertAlmostEqual(self.value("thermal.indicated_temperature_k"), 373.15)
        self.assertAlmostEqual(_VD56_TEST_PARAMETERS["heat_loss_W_K"]["value"] * 78, 180)
        self.assertEqual(self.host.time_s, 4800)

    def test_vd56_power_off_cools_and_lower_target_does_not_teleport(self):
        self.ok("power.set", enabled=True)
        self.ok("thermal.set_temperature", temperature_K=373.15)
        self.host.step(3600)
        hot = self.value("thermal.temperature_k", truth=True)
        self.ok("thermal.set_temperature", temperature_K=323.15)
        self.assertEqual(self.value("thermal.temperature_k", truth=True), hot)
        self.host.step(60)
        warm = self.value("thermal.temperature_k", truth=True)
        self.assertLess(warm, hot)
        self.assertGreater(warm, 323.15)
        self.ok("power.set", enabled=False)
        self.host.step(3600)
        self.assertLess(self.value("thermal.temperature_k", truth=True), warm)
        self.assertGreater(self.value("thermal.temperature_k", truth=True), 295.15)

    def test_vd56_read_describe_logs_do_not_advance_or_mutate(self):
        self.evacuate()
        before = self.host.debug_snapshot()
        for _ in range(4):
            self.agent.read(instance_id="vd56_a")
            self.agent.describe(instance_id="vd56_a")
            self.agent.logs()
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.read(instance_id="vd56_b")["clock"]["time_s"], 3600)

    def test_vd56_instances_and_configuration_are_isolated(self):
        b = _vd56_test_copy.deepcopy(self.host.debug_snapshot()["instances"]["vd56_b"])
        self.evacuate()
        self.ok("power.set", enabled=True)
        self.ok("thermal.set_temperature", temperature_K=373.15)
        self.host.step(30)
        self.assertEqual(b, self.host.debug_snapshot()["instances"]["vd56_b"])
        self.assertNotIn("vd56_b", self.host.debug_snapshot()["external_vacuum_boundaries"])
        self.assertLess(self.value("pressure.pressure_pa", truth=True), 6000)
        self.assertEqual(self.value("pressure.pressure_pa", instance="vd56_b", truth=True), 101325)
        self.assertGreater(self.value("thermal.temperature_k", truth=True), 295.15)

    def test_vd56_vacuum_requires_connection_and_atomic_prechecks(self):
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("vacuum.set_valve", opened=True)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.connect()
        self.ok("scene.set_door", angle_rad=.4)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("vacuum.set_valve", opened=True)["status"], "rejected")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("scene.set_door", angle_rad=0.0)
        self.ok("vent.set_valve", opened=True)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("vacuum.set_valve", opened=True)["status"], "rejected")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("vent.set_valve", opened=False)
        self.ok("vacuum.set_valve", opened=True)
        self.assertEqual(self.value("pressure.pressure_pa", truth=True), 101325)
        self.host.step(120)
        self.assertGreater(self.value("pressure.pressure_pa", truth=True), 5000)
        self.assertLess(self.value("pressure.pressure_pa", truth=True), 50000)

    def test_vd56_power_leaves_external_pump_and_valves_independent(self):
        self.connect()
        self.ok("power.set", enabled=True)
        self.ok("vacuum.set_valve", opened=True)
        self.ok("power.set", enabled=False)
        self.assertTrue(self.value("vacuum.valve_open"))
        self.assertTrue(self.value("vacuum.external_connected"))
        self.host.step(120)
        self.assertLess(self.value("pressure.pressure_pa", truth=True), 101325)
        self.assertEqual(self.value("thermal.temperature_k", truth=True), 295.15)

    def test_vd56_isolated_leak_and_backfill_are_time_driven(self):
        self.evacuate()
        self.ok("vacuum.set_valve", opened=False)
        initial = self.value("pressure.pressure_pa", truth=True)
        self.host.step(3600)
        self.assertAlmostEqual(self.value("pressure.pressure_pa", truth=True) - initial, 1000.0, places=9)
        low = self.value("pressure.pressure_pa", truth=True)
        self.ok("vent.set_valve", opened=True)
        self.assertEqual(self.value("pressure.pressure_pa", truth=True), low)
        self.host.step(30)
        partial = self.value("pressure.pressure_pa", truth=True)
        self.assertGreater(partial, low)
        self.assertLess(partial, 101325)
        self.host.step(3600)
        self.assertAlmostEqual(self.value("pressure.pressure_pa", truth=True), 101325)
        self.ok("vent.set_valve", opened=False)
        self.host.step(3600)
        self.assertEqual(self.value("pressure.pressure_pa", truth=True), 101325)

    def test_vd56_pressure_proxy_fluctuation_only_at_50_mbar(self):
        self.evacuate()
        values = []
        for _ in range(8):
            self.host.step(15)
            values.append(self.value("pressure.indicated_pressure_pa"))
        self.assertEqual(min(values), 4900)
        self.assertEqual(max(values), 5100)
        self.assertTrue(all(4900 <= p <= 5100 for p in values))
        self.assertAlmostEqual(self.value("pressure.pressure_pa", truth=True), 5000, places=5)
        self.host.configure_external_vacuum("vd56_a", connected=True, boundary_pressure_pa=40000, time_constant_s=120, evidence="Synthetic 400 mbar boundary; no 50 mbar proxy claim")
        self.host.step(3600)
        self.host.step(15)
        self.assertEqual(self.value("pressure.indicated_pressure_pa"), 40000)
        obs = self.agent.read(instance_id="vd56_a", state_ids=["pressure.indicated_pressure_pa"])["observations"]["pressure.indicated_pressure_pa"]
        self.assertEqual(obs["quality"], "estimated")
        self.assertEqual(obs["unit"], "Pa")

    def test_vd56_door_requires_backfill_and_scene_sync(self):
        self.evacuate()
        self.ok("vacuum.set_valve", opened=False)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("scene.set_door", angle_rad=.5)["status"], "rejected")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("vent.set_valve", opened=True)
        self.host.step(300)
        self.ok("scene.set_door", angle_rad=.5)
        self.assertEqual(self.value("door.angle_rad"), .5)
        self.assertEqual(self.scene_calls[-1], ("vd56_a", {"door.angle_rad": .5}))
        self.host.scene_adapter = None
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("scene.set_door", angle_rad=0)["status"], "unsupported")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.describe(instance_id="vd56_a")["capabilities"]["scene.set_door"]["availability"], "unsupported")

    def test_vd56_invalid_inputs_and_temperature_boundaries_are_atomic(self):
        self.ok("power.set", enabled=True)
        cases = [("thermal.set_temperature", {"temperature_K": x}) for x in (True, None, 304.14, 493.16, float("nan"), float("inf"))]
        cases += [("power.set", {"enabled": 1}), ("vent.set_valve", {"opened": "true"}), ("scene.set_door", {"angle_rad": -.1}), ("scene.set_door", {"angle_rad": 2}), ("thermal.set_temperature", {"temperature_K": 333.15, "role": "evaluator"})]
        for cap, args in cases:
            with self.subTest(cap=cap, args=args):
                before = self.host.debug_snapshot()
                self.assertEqual(self.invoke(cap, **args)["status"], "rejected")
                self.assertEqual(before, self.host.debug_snapshot())
        for temperature in (304.15, 493.15): self.ok("thermal.set_temperature", temperature_K=temperature)
        self.ok("power.set", enabled=False)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("thermal.set_temperature", temperature_K=333.15)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_vd56_host_configuration_prevalidates_every_field(self):
        self.connect()
        for params in ({"boundary_pressure_pa": 0}, {"boundary_pressure_pa": 101325}, {"time_constant_s": 0}, {"time_constant_s": True}, {"time_constant_s": float("nan")}, {"evidence": ""}, {"connected": 1}):
            args = dict(connected=True, boundary_pressure_pa=5000, time_constant_s=120, evidence="synthetic host config")
            args.update(params)
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.host.configure_external_vacuum("vd56_a", **args)
            self.assertEqual(before, self.host.debug_snapshot())
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError): self.host.configure_external_vacuum("vd56_a", connected=True, evidence="missing performance")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("vacuum.set_valve", opened=True)
        self.host.configure_external_vacuum("vd56_a", connected=False, evidence="Synthetic disconnect")
        self.assertFalse(self.value("vacuum.valve_open"))
        self.assertFalse(self.value("vacuum.external_connected"))
        self.assertNotIn("vd56_a", self.host.debug_snapshot()["external_vacuum_boundaries"])

    def test_vd56_agent_truth_identity_and_log_isolation(self):
        self.evacuate()
        agent_payload = _vd56_test_json.dumps([self.agent.describe(instance_id="vd56_a"), self.agent.read(instance_id="vd56_a"), self.agent.logs()])
        for hidden in ("thermal.temperature_k", "pressure.pressure_pa", "private_configuration_marker", "external_vacuum_boundaries"):
            self.assertNotIn(hidden, agent_payload)
        denied = self.agent.read(instance_id="vd56_a", state_ids=["pressure.pressure_pa"])
        unknown = self.agent.read(instance_id="vd56_a", state_ids=["not_a_slot"])
        self.assertEqual(denied, unknown)
        self.assertEqual(denied["error"]["code"], "ACCESS_DENIED")
        self.assertIn("pressure.pressure_pa", self.judge.read(instance_id="vd56_a")["observations"])
        self.host.register_principal("restricted", visibility="agent", instance_ids=["vd56_b"])
        restricted = self.host.session("restricted")
        response = restricted.read(instance_id="vd56_a")
        self.assertEqual(response["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertIsNone(response["state_revision"])
        self.assertEqual(restricted.logs(), [])
        self.assertFalse(hasattr(self.agent, "step"))
        self.assertNotIn("sim.step", self.agent.describe(instance_id="vd56_a")["capabilities"])
        with self.assertRaises(ValueError): self.host.register_principal("learner", visibility="evaluator")

    def test_vd56_write_routes_and_cannot_change_truth(self):
        response = self.agent.write(instance_id="vd56_a", state_id="power.enabled", value=True, request_id="vd56_write_power")
        self.assertEqual(response["status"], "succeeded")
        response = self.agent.write(instance_id="vd56_a", state_id="thermal.target_k", value=373.15, request_id="vd56_write_temp")
        self.assertEqual(response["result"], {"setpoint_K": 373.15})
        self.assertEqual(self.value("thermal.temperature_k", truth=True), 295.15)
        before = self.host.debug_snapshot()
        for state_id in ("thermal.temperature_k", "pressure.pressure_pa", "pressure.indicated_pressure_pa", "vacuum.external_connected"):
            result = self.agent.write(instance_id="vd56_a", state_id=state_id, value=123, request_id="vd56_write_" + state_id)
            self.assertEqual(result["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_vd56_request_replay_and_conflict_preserve_state(self):
        args = dict(instance_id="vd56_a", capability_id="power.set", arguments={"enabled": True}, request_id="vd56_same_request")
        result = self.agent.invoke(**args)
        self.host.step(30)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**args), result)
        self.assertEqual(self.agent.invoke(**{**args, "arguments": {"enabled": False}})["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_vd56_callback_failure_sanitized_and_partial_step_honest(self):
        def fail(*_): raise RuntimeError("private_configuration_marker")
        self.host.scene_adapter = fail
        before = self.host.debug_snapshot()
        response = self.invoke("power.set", enabled=True)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("private_configuration_marker", _vd56_test_json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(RuntimeError): self.connect()
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.scene_adapter = None
        self.ok("power.set", enabled=True)
        self.ok("thermal.set_temperature", temperature_K=373.15)
        self.host.scene_adapter = fail
        with self.assertRaises(_VD56TestSceneError): self.host.step(30)
        self.assertEqual(self.host.time_s, 30)
        self.assertGreater(self.value("thermal.temperature_k", truth=True), 295.15)
        self.assertTrue(self.host.debug_snapshot()["scene_faults"])

    def test_vd56_pressure_program_declared_unsupported(self):
        self.assertEqual(self.agent.describe(instance_id="vd56_a")["capabilities"]["pressure.program"]["availability"], "declared")
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("pressure.program", steps=[{"pressure_Pa": 5000, "duration_s": 600}])["status"], "unsupported")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_vd56_real_result_and_observation_schemas(self):
        spec = _vd56_test_metadata()["asset_types"]["instrument.binder_vd56"]
        self.connect()
        self.ok("power.set", enabled=True)
        for capability, info in spec["capabilities"].items():
            self.assertEqual(_vd56_test_lint(info["input_schema"]), [])
            self.assertEqual(_vd56_test_lint(info["output_schema"]), [])
            args = info["example_arguments"][0]
            if capability == "vacuum.set_valve": self.ok("scene.set_door", angle_rad=0)
            if capability == "vent.set_valve": self.ok("vacuum.set_valve", opened=False)
            self.assertEqual(_vd56_test_value_schema(args, info["input_schema"]), [])
            response = self.ok(capability, **args)
            self.assertEqual(_vd56_test_value_schema(response["result"], info["output_schema"]), [])
            self.assertEqual(response["observations"], {})
            self.assertEqual(set(response), {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"})
        for sid, obs in self.judge.read(instance_id="vd56_a")["observations"].items():
            self.assertEqual(_vd56_test_value_schema(obs["value"], spec["state_slots"][sid]["value_schema"]), [])
            self.assertEqual(obs["unit"], spec["state_slots"][sid]["unit"])

    def test_vd56_invalid_time_and_zero_step_leave_state_unchanged(self):
        before = self.host.debug_snapshot()
        for dt in (-1, True, 3601, float("inf"), float("nan")):
            with self.assertRaises(ValueError): self.host.step(dt)
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.host.describe_host()["sim.step"]["visibility"], "trusted_host_only")

    def test_vd56_time_partition_is_consistent(self):
        second = _VD56TestRuntime()
        second.create_instance("vd56_other")
        second.register_principal("agent", visibility="agent")
        session = second.session("agent")
        self.connect()
        second.configure_external_vacuum("vd56_other", connected=True, boundary_pressure_pa=5000, time_constant_s=120, evidence="synthetic partition fixture")
        for cap, args in (("power.set", {"enabled": True}), ("thermal.set_temperature", {"temperature_K": 373.15}), ("vacuum.set_valve", {"opened": True})):
            self.ok(cap, **args)
            self.assertEqual(session.invoke(instance_id="vd56_other", capability_id=cap, arguments=args, request_id=cap)["status"], "succeeded")
        self.host.step(120)
        for _ in range(12): second.step(10)
        a = self.host.debug_snapshot()["instances"]["vd56_a"]["state"]
        b = second.debug_snapshot()["instances"]["vd56_other"]["state"]
        self.assertAlmostEqual(a["thermal.temperature_k"], b["thermal.temperature_k"], places=9)
        self.assertAlmostEqual(a["pressure.pressure_pa"], b["pressure.pressure_pa"], places=7)
        self.assertEqual(self.host.time_s, second.time_s)

# BEGIN CHEM-187 JULABO independent runtime tests (append-only shared file).
import copy as _julabo_test_copy
import json as _julabo_test_json
import math as _julabo_test_math
from basic40_runtime_thermal import (JulaboCorioCD200FRuntime as _JulaboTestRuntime,
    export_julabo_corio_cd200f_metadata as _julabo_test_metadata,
    JULABO_CORIO_CD200F_TYPE as _JULABO_TEST_TYPE,
    JULABO_CORIO_CD200F_MAX_FLOW as _JULABO_TEST_MAX_FLOW)


class JulaboCorioCD200FRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = _JulaboTestRuntime(scene_adapter=lambda key, updates: self.calls.append((key, _julabo_test_copy.deepcopy(updates))))
        self.host.create_instance('julabo_a')
        self.host.create_instance('julabo_b')
        self.host.register_principal('julabo_learner', visibility='agent', instance_ids=['julabo_a'])
        self.host.register_principal('julabo_evaluator', visibility='evaluator')
        self.agent, self.judge = self.host.session('julabo_learner'), self.host.session('julabo_evaluator')
        self.request = 0

    def invoke(self, capability, **arguments):
        self.request += 1
        return self.agent.invoke(instance_id='julabo_a', capability_id=capability, arguments=arguments, request_id='julabo_req_' + str(self.request))

    def ok(self, capability, **arguments):
        result = self.invoke(capability, **arguments)
        self.assertEqual(result['status'], 'succeeded', result)
        return result

    def value(self, sid, instance='julabo_a', truth=False):
        return (self.judge if truth else self.agent).read(instance_id=instance, state_ids=[sid])['observations'][sid]['value']

    def config(self, **changes):
        args = dict(effective_heat_capacity_J_K=7200.0, heat_loss_W_K=0.0, lid_open_loss_W_K=0.0, initial_temperature_K=293.15,
            minimum_temperature_K=253.15, maximum_temperature_K=423.15, evidence='Synthetic explicit host configuration; private_julabo_configuration_marker')
        args.update(changes)
        self.host.configure_bath_model('julabo_a', **args)

    def start(self, target=373.15):
        self.ok('power.set', enabled=True)
        self.ok('circulation.set_running', running=True)
        self.ok('thermal.set_temperature', temperature_K=target)

    def assert_energy(self, places=6):
        s = self.host.debug_snapshot()['instances']['julabo_a']['state']
        self.assertAlmostEqual(s['thermal.stored_energy_change_j'], s['thermal.heater_energy_j'] - s['thermal.cooling_energy_j'] - s['thermal.environment_energy_j'], places=places)

    def test_julabo_empty_initial_state_is_unknown_and_no_phantom_heating(self):
        obs = self.agent.read(instance_id='julabo_a')['observations']['thermal.indicated_temperature_k']
        self.assertIsNone(obs['value'])
        self.assertEqual(obs['quality'], 'unavailable')
        self.ok('power.set', enabled=True)
        self.ok('thermal.set_temperature', temperature_K=423.15)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('circulation.set_running', running=True)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.step(3600)
        self.assertIsNone(self.value('thermal.temperature_k', truth=True))
        self.assertFalse(self.value('thermal.model_configured'))
        self.assertEqual(self.value('thermal.heater_energy_j', truth=True), 0)

    def test_julabo_setpoint_only_and_reads_are_pure(self):
        self.config()
        self.ok('power.set', enabled=True)
        before = self.host.debug_snapshot()
        self.ok('thermal.set_temperature', temperature_K=373.15)
        after = self.host.debug_snapshot()
        old, new = before['instances']['julabo_a']['state'], after['instances']['julabo_a']['state']
        self.assertEqual([key for key in new if new[key] != old[key]], ['thermal.target_k'])
        for _ in range(3):
            self.agent.read(instance_id='julabo_a')
            self.agent.describe(instance_id='julabo_a')
            self.agent.logs()
        self.assertEqual(after, self.host.debug_snapshot())
        self.assertEqual(self.host.time_s, 0)
        self.host.step(30)
        self.assertEqual(self.value('thermal.temperature_k', truth=True), 293.15)

    def test_julabo_heating_chart_100_to_150C_in_180s_under_explicit_assumption(self):
        self.config(initial_temperature_K=373.15)
        self.start(423.15)
        self.host.step(60)
        self.assertAlmostEqual(self.value('thermal.temperature_k', truth=True), 373.15 + 2000 * 60 / 7200, places=10)
        self.host.step(120)
        self.assertEqual(self.value('thermal.temperature_k', truth=True), 423.15)
        self.assertAlmostEqual(self.value('thermal.heater_energy_j', truth=True), 360000, places=6)
        self.assert_energy()
        self.host.step(600)
        self.assertAlmostEqual(self.value('thermal.heater_energy_j', truth=True), 360000, places=6)

    def test_julabo_display_uses_001C_grid_and_not_sensor_accuracy(self):
        self.config()
        self.start()
        self.host.step(60)
        obs = self.agent.read(instance_id='julabo_a', state_ids=['thermal.indicated_temperature_k'])['observations']['thermal.indicated_temperature_k']
        self.assertAlmostEqual(obs['value'], 309.82)
        self.assertEqual(obs['quality'], 'estimated')
        self.assertNotEqual(obs['value'], self.value('thermal.temperature_k', truth=True))

    def test_julabo_cooling_reference_points_and_graph_time_fit(self):
        capacity = 930.0 * 2.5 / _julabo_test_math.log(220.0 / 170.0)
        self.config(effective_heat_capacity_J_K=capacity)
        self.start(273.15)
        for temperature, expected in ((253.15, 60), (263.15, 130), (273.15, 170), (283.15, 195), (293.15, 220)):
            a, b = self.host._cooling_line(temperature)
            self.assertAlmostEqual(a + b * temperature, expected)
        self.host.step(930)
        self.assertAlmostEqual(self.value('thermal.temperature_k', truth=True), 273.15, places=9)
        self.assertLessEqual(self.value('thermal.cooling_energy_j', truth=True), 220 * 930)
        self.assert_energy()

    def test_julabo_cooling_never_exceeds_reference_power_and_no_domain_extrapolation(self):
        self.config(effective_heat_capacity_J_K=8000)
        self.start(253.15)
        last_t, last_e = 293.15, 0
        for _ in range(45):
            self.host.step(60)
            temperature = self.value('thermal.temperature_k', truth=True)
            energy = self.value('thermal.cooling_energy_j', truth=True)
            self.assertLessEqual(energy - last_e, 220 * 60 + 1e-7)
            self.assertGreaterEqual(temperature, 253.15)
            self.assertLessEqual(temperature, last_t)
            last_t, last_e = temperature, energy
        self.assertEqual(last_t, 253.15)
        self.assert_energy()
        self.ok('power.set', enabled=False)
        self.config(initial_temperature_K=313.15)
        self.start(273.15)
        self.host.step(60)
        self.assertFalse(self.value('thermal.cooling_curve_supported'))
        self.assertEqual(self.value('thermal.temperature_k', truth=True), 313.15)
        self.assertEqual(self.value('thermal.cooling_energy_j', truth=True), 0)

    def test_julabo_nonreference_ambient_disables_ethanol_cooling_curve(self):
        host = _JulaboTestRuntime(ambient_temperature_K=298.15)
        host.create_instance('different_ambient')
        host.configure_bath_model('different_ambient', effective_heat_capacity_J_K=8000, heat_loss_W_K=0, lid_open_loss_W_K=0, initial_temperature_K=293.15, minimum_temperature_K=253.15, maximum_temperature_K=423.15, evidence='Synthetic different-ambient fixture')
        for i, (capability, args) in enumerate((('power.set', {'enabled': True}), ('circulation.set_running', {'running': True}), ('thermal.set_temperature', {'temperature_K': 273.15}))):
            self.assertEqual(host.invoke(instance_id='different_ambient', capability_id=capability, arguments=args, request_id=str(i))['status'], 'succeeded')
        host.step(60)
        self.assertFalse(host.read(instance_id='different_ambient')['observations']['thermal.cooling_curve_supported']['value'])
        self.assertEqual(host.debug_snapshot()['instances']['different_ambient']['state']['thermal.temperature_k'], 293.15)

    def test_julabo_energy_conservation_with_losses_hold_and_poweroff(self):
        self.config(heat_loss_W_K=3, lid_open_loss_W_K=6)
        self.start(313.15)
        self.host.step(600)
        self.assertAlmostEqual(self.value('thermal.temperature_k', truth=True), 313.15)
        self.assert_energy()
        energy = self.value('thermal.heater_energy_j', truth=True)
        self.host.step(60)
        self.assertAlmostEqual(self.value('thermal.heater_energy_j', truth=True) - energy, 3 * 20 * 60)
        self.ok('scene.set_bath_lid', angle_rad=_julabo_test_math.pi / 2)
        self.host.step(60)
        self.assert_energy()
        hot = self.value('thermal.temperature_k', truth=True)
        self.ok('power.set', enabled=False)
        self.host.step(60)
        self.assertAlmostEqual(self.value('thermal.temperature_k', truth=True), 293.15 + (hot - 293.15) * _julabo_test_math.exp(-9 * 60 / 7200), places=9)
        self.assertFalse(self.value('circulation.running'))
        self.assert_energy()

    def test_julabo_subambient_environment_heat_gain_and_hold_balance(self):
        self.config(initial_temperature_K=273.15, effective_heat_capacity_J_K=8000, heat_loss_W_K=2)
        self.start(273.15)
        self.host.step(300)
        self.assertEqual(self.value('thermal.temperature_k', truth=True), 273.15)
        self.assertAlmostEqual(self.value('thermal.cooling_energy_j', truth=True), 12000)
        self.assertAlmostEqual(self.value('thermal.environment_energy_j', truth=True), -12000)
        self.assert_energy()

    def test_julabo_mass_cp_configuration_is_explicit_and_isolated(self):
        self.host.configure_bath_fluid('julabo_a', mass_kg=4, specific_heat_J_kg_K=2000, heat_loss_W_K=1, lid_open_loss_W_K=0, initial_temperature_K=293.15, minimum_temperature_K=253.15, maximum_temperature_K=373.15, evidence='Synthetic liquid; not manufacturer fluid properties')
        cfg = self.host.debug_snapshot()['instances']['julabo_a']['thermal_configuration']
        self.assertEqual(cfg['effective_heat_capacity_J_K'], 8000)
        self.start(333.15)
        self.host.step(60)
        self.assertGreater(self.value('thermal.temperature_k', truth=True), 293.15)
        self.assertIsNone(self.value('thermal.temperature_k', instance='julabo_b', truth=True))
        self.assertIsNone(self.host.debug_snapshot()['instances']['julabo_b']['thermal_configuration'])
        self.assertEqual(self.judge.read(instance_id='julabo_b')['clock']['time_s'], 60)

    def test_julabo_external_operating_point_independent_extrema_and_no_default_flow(self):
        self.config()
        self.start()
        self.assertIsNone(self.value('circulation.flow_rate_m3_s'))
        self.assertIsNone(self.value('circulation.pressure_rise_pa'))
        self.ok('circulation.set_running', running=False)
        before = self.host.debug_snapshot()
        for flow, pressure in ((_JULABO_TEST_MAX_FLOW, 35000), (.0003, 20000), (.0001, 35001), (-1, 0), (True, 100), (.0001, float('nan'))):
            with self.assertRaises(ValueError): self.host.configure_circulation_boundary('julabo_a', connected=True, flow_rate_m3_s=flow, pressure_rise_Pa=pressure, evidence='Synthetic boundary')
            self.assertEqual(before, self.host.debug_snapshot())
        self.host.configure_circulation_boundary('julabo_a', connected=True, flow_rate_m3_s=8 / 60000, pressure_rise_Pa=25800, evidence='Approximate graph readback water operating point; imposed boundary, not circuit solution')
        self.ok('circulation.set_running', running=True)
        self.assertEqual(self.value('circulation.flow_rate_m3_s'), 8 / 60000)
        self.assertEqual(self.value('circulation.pressure_rise_pa'), 25800)
        self.ok('power.set', enabled=False)
        self.assertEqual(self.value('circulation.flow_rate_m3_s'), 0)
        self.host.configure_circulation_boundary('julabo_a', connected=False, evidence='Synthetic disconnect')
        self.assertFalse(self.value('circulation.external_connected'))

    def test_julabo_lid_requires_scene_and_synchronizes_angle(self):
        self.ok('scene.set_bath_lid', angle_rad=.7)
        self.assertEqual(self.value('bath_lid.angle_rad'), .7)
        self.assertEqual(self.calls[-1], ('julabo_a', {'bath_lid.angle_rad': .7}))
        self.host.scene_adapter = None
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('scene.set_bath_lid', angle_rad=0)['status'], 'unsupported')
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.describe(instance_id='julabo_a')['capabilities']['scene.set_bath_lid']['availability'], 'unsupported')

    def test_julabo_rejected_inputs_are_atomic(self):
        self.config()
        self.ok('power.set', enabled=True)
        cases = [('thermal.set_temperature', {'temperature_K': v}) for v in (True, None, float('nan'), float('inf'), 253.14, 423.16)]
        cases += [('power.set', {'enabled': 1}), ('circulation.set_running', {'running': 'true'}), ('scene.set_bath_lid', {'angle_rad': -1}), ('scene.set_bath_lid', {'angle_rad': 2}), ('thermal.set_temperature', {'temperature_K': 300, 'visibility': 'evaluator'})]
        for cap, arguments in cases:
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **arguments)['status'], 'rejected')
            self.assertEqual(before, self.host.debug_snapshot())
        for endpoint in (253.15, 423.15): self.ok('thermal.set_temperature', temperature_K=endpoint)
        self.ok('power.set', enabled=False)
        self.config(minimum_temperature_K=273.15, maximum_temperature_K=373.15)
        self.ok('power.set', enabled=True)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('thermal.set_temperature', temperature_K=253.15)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_julabo_host_configuration_validates_all_fields_before_commit(self):
        self.config()
        for changes in ({'effective_heat_capacity_J_K': 0}, {'heat_loss_W_K': -1}, {'lid_open_loss_W_K': True}, {'initial_temperature_K': 500}, {'minimum_temperature_K': 300}, {'maximum_temperature_K': float('inf')}, {'evidence': ''}, {'heat_loss_W_K': float('nan')}):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.config(**changes)
            self.assertEqual(before, self.host.debug_snapshot())
        self.start()
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError): self.config()
        self.assertEqual(before, self.host.debug_snapshot())

    def test_julabo_agent_truth_identity_and_log_filtering(self):
        self.config()
        self.start()
        self.host.step(60)
        payload = _julabo_test_json.dumps([self.agent.describe(instance_id='julabo_a'), self.agent.read(instance_id='julabo_a'), self.agent.logs()])
        for hidden in ('thermal.temperature_k', 'thermal.heater_energy_j', 'thermal.cooling_energy_j', 'private_julabo_configuration_marker', 'thermal_configuration'):
            self.assertNotIn(hidden, payload)
        self.assertEqual(self.agent.read(instance_id='julabo_a', state_ids=['thermal.temperature_k']), self.agent.read(instance_id='julabo_a', state_ids=['unknown_slot']))
        denied = self.agent.read(instance_id='julabo_b')
        self.assertEqual(denied['error']['code'], 'UNKNOWN_INSTANCE')
        self.assertIsNone(denied['state_revision'])
        self.assertIn('thermal.temperature_k', self.judge.read(instance_id='julabo_a')['observations'])
        self.assertFalse(hasattr(self.agent, 'step'))
        self.assertNotIn('sim.step', self.agent.describe(instance_id='julabo_a')['capabilities'])
        with self.assertRaises(ValueError): self.host.register_principal('julabo_learner', visibility='evaluator')
        observations = self.agent.read(instance_id='julabo_a')
        observations['observations']['power.enabled']['value'] = False
        self.assertTrue(self.value('power.enabled'))

    def test_julabo_write_routes_and_request_replay_conflict(self):
        self.config()
        response = self.agent.write(instance_id='julabo_a', state_id='power.enabled', value=True, request_id='julabo_write_power')
        self.assertEqual(response['status'], 'succeeded')
        args = dict(instance_id='julabo_a', capability_id='thermal.set_temperature', arguments={'temperature_K': 333.15}, request_id='julabo_replay')
        first = self.agent.invoke(**args)
        self.host.step(30)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**args), first)
        self.assertEqual(self.agent.invoke(**{**args, 'arguments': {'temperature_K': 313.15}})['error']['code'], 'REQUEST_CONFLICT')
        for sid in ('thermal.temperature_k', 'thermal.indicated_temperature_k', 'thermal.model_configured', 'circulation.flow_rate_m3_s'):
            self.assertEqual(self.agent.write(instance_id='julabo_a', state_id=sid, value=100, request_id=sid)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_julabo_callback_failure_is_sanitized_and_step_partial_effects_honest(self):
        self.config()
        def fail(*_): raise RuntimeError('private_julabo_configuration_marker')
        self.host.scene_adapter = fail
        before = self.host.debug_snapshot()
        response = self.invoke('power.set', enabled=True)
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['side_effects'], 'unknown')
        self.assertNotIn('private_julabo_configuration_marker', _julabo_test_json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(RuntimeError): self.config()
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.scene_adapter = None
        self.start()
        self.host.scene_adapter = fail
        with self.assertRaises(SceneSynchronizationError): self.host.step(60)
        self.assertEqual(self.host.time_s, 60)
        self.assertGreater(self.value('thermal.temperature_k', truth=True), 293.15)
        self.assertEqual(len(self.host.debug_snapshot()['scene_faults']), 2)
        self.assertNotIn('private_julabo_configuration_marker', _julabo_test_json.dumps(self.host.debug_snapshot()['scene_faults']))

    def test_julabo_time_partition_and_cooling_knots(self):
        self.config(effective_heat_capacity_J_K=8000, heat_loss_W_K=1)
        self.start(253.15)
        host = _JulaboTestRuntime()
        host.create_instance('other')
        host.configure_bath_model('other', effective_heat_capacity_J_K=8000, heat_loss_W_K=1, lid_open_loss_W_K=0, initial_temperature_K=293.15, minimum_temperature_K=253.15, maximum_temperature_K=423.15, evidence='Synthetic partition fixture')
        for cap, args in (('power.set', {'enabled': True}), ('circulation.set_running', {'running': True}), ('thermal.set_temperature', {'temperature_K': 253.15})):
            host.invoke(instance_id='other', capability_id=cap, arguments=args, request_id=cap)
        self.host.step(1800)
        for _ in range(180): host.step(10)
        a, b = self.host.debug_snapshot()['instances']['julabo_a']['state'], host.debug_snapshot()['instances']['other']['state']
        for sid in ('thermal.temperature_k', 'thermal.cooling_energy_j', 'thermal.environment_energy_j', 'thermal.stored_energy_change_j'):
            self.assertAlmostEqual(a[sid], b[sid], places=6)
        self.assert_energy()

    def test_julabo_invalid_time_zero_step_and_host_identity_inputs(self):
        before = self.host.debug_snapshot()
        for dt in (-1, True, 3601, float('inf'), float('nan')):
            with self.assertRaises(ValueError): self.host.step(dt)
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.host.describe_host()['sim.step']['visibility'], 'trusted_host_only')
        for scope in ('julabo_a', [None], ['unknown']):
            with self.assertRaises(ValueError): self.host.register_principal('bad', visibility='agent', instance_ids=scope)

    def test_julabo_real_result_and_observation_schemas(self):
        self.config()
        spec = _julabo_test_metadata()['asset_types'][_JULABO_TEST_TYPE]
        for cap, info in spec['capabilities'].items():
            self.assertEqual(lint_schema(info['input_schema']), [])
            self.assertEqual(lint_schema(info['output_schema']), [])
            args = info['example_arguments'][0]
            self.assertEqual(validate_value(args, info['input_schema']), [])
            response = self.ok(cap, **args)
            self.assertEqual(validate_value(response['result'], info['output_schema']), [])
            self.assertEqual(response['observations'], {})
        for sid, observation in self.judge.read(instance_id='julabo_a')['observations'].items():
            self.assertEqual(validate_value(observation['value'], spec['state_slots'][sid]['value_schema']), [])
            self.assertEqual(observation['unit'], spec['state_slots'][sid]['unit'])
        self.assertIsNone(spec['parameters']['thermal_capacity_J_K']['value'])
        self.assertIsNone(spec['parameters']['heat_loss_W_K']['value'])
# END CHEM-187 JULABO runtime tests.

# BEGIN CHEM-191 Miele PLW 6111 runtime tests (append-only asset scope).
from basic40_runtime_thermal import (MielePLW6111Runtime as _MieleTestRuntime,
    MIELE_PLW6111_TYPE as _MIELE_TEST_TYPE, export_miele_plw6111_metadata as _miele_test_metadata)


class MielePLW6111RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.scene_calls = []
        self.host = _MieleTestRuntime(scene_adapter=lambda key, updates: self.scene_calls.append((key, copy.deepcopy(updates))))
        self.host.create_instance('miele_a')
        self.host.create_instance('miele_b')
        self.host.register_principal('miele_agent', visibility='agent', instance_ids=['miele_a'])
        self.host.register_principal('miele_judge', visibility='evaluator')
        self.agent, self.judge = self.host.session('miele_agent'), self.host.session('miele_judge')
        self.request = 0

    def invoke(self, capability, **args):
        self.request += 1
        return self.agent.invoke(instance_id='miele_a', capability_id=capability, arguments=args, request_id='miele_req_' + str(self.request))

    def ok(self, capability, **args):
        response = self.invoke(capability, **args)
        self.assertEqual(response['status'], 'succeeded', response)
        return response

    def state(self, instance='miele_a'):
        return self.host.debug_snapshot()['instances'][instance]['state']

    def configure(self, **updates):
        values = dict(effective_heat_capacity_J_K=41800, heat_loss_W_K=0, initial_temperature_K=293.15, maximum_temperature_K=368.15,
            evidence='SYNTHETIC_PRIVATE_MIELE_CONFIG: assumed 10kg water *4180J/kg/K, no measured charge, zero loss reference')
        values.update(updates)
        self.host.configure_water_model('miele_a', **values)

    def start(self, temperature=333.15, wash_s=60, rinse_s=30, dry_s=30):
        self.ok('power.set', enabled=True)
        self.ok('thermal.set_temperature', temperature_K=temperature)
        self.ok('washing.configure_program', wash_s=wash_s, rinse_s=rinse_s, dry_s=dry_s)
        self.ok('washing.start')

    def assert_energy(self):
        s = self.state()
        self.assertAlmostEqual(s['water.stored_energy_change_j'], s['water.heater_energy_j'] - s['water.environment_energy_j'], places=6)

    def test_miele_unknown_water_model_has_no_phantom_temperature_or_heating(self):
        observation = self.agent.read(instance_id='miele_a')['observations']['water.indicated_temperature_k']
        self.assertIsNone(observation['value'])
        self.assertEqual(observation['quality'], 'unavailable')
        self.ok('power.set', enabled=True)
        self.ok('thermal.set_temperature', temperature_K=333.15)
        self.ok('washing.configure_program', wash_s=60, rinse_s=30, dry_s=30)
        before = self.host.debug_snapshot()
        response = self.invoke('washing.start')
        self.assertEqual(response['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.step(100)
        self.assertIsNone(self.state()['water.temperature_k'])
        self.assertEqual(self.state()['water.heater_energy_j'], 0)

    def test_miele_set_temperature_only_changes_target_and_read_is_pure(self):
        self.configure()
        self.ok('power.set', enabled=True)
        before = self.state()
        self.ok('thermal.set_temperature', temperature_K=333.15)
        after = self.state()
        self.assertEqual([key for key in after if before[key] != after[key]], ['thermal.target_k'])
        snapshot = self.host.debug_snapshot()
        for _ in range(3):
            self.agent.read(instance_id='miele_a')
            self.agent.describe(instance_id='miele_a')
            self.agent.logs()
        self.assertEqual(snapshot, self.host.debug_snapshot())
        self.host.step(100)
        self.assertEqual(self.state()['water.temperature_k'], 293.15)
        self.assertEqual(self.state()['water.heater_energy_j'], 0)

    def test_miele_7500w_water_energy_bound_and_exact_reference_heatup(self):
        self.configure()
        self.start()
        self.host.step(100)
        s = self.state()
        self.assertAlmostEqual(s['water.temperature_k'], 293.15 + 7500 * 100 / 41800, places=10)
        self.assertEqual(s['water.heater_energy_j'], 750000)
        self.assertEqual(s['program.phase_elapsed_s'], 0)
        self.assertEqual(s['program.phase'], 'wash')
        heatup = 41800 * 40 / 7500
        self.host.step(heatup - 100)
        self.assertAlmostEqual(self.state()['water.temperature_k'], 333.15, places=10)
        self.assertAlmostEqual(self.state()['program.phase_elapsed_s'], 0, places=9)
        self.assert_energy()

    def test_miele_phase_order_holds_then_drying_only_timer(self):
        self.configure()
        self.start()
        self.host.step(41800 * 40 / 7500 + 60)
        self.assertEqual(self.state()['program.phase'], 'rinse')
        self.host.step(30)
        self.assertEqual(self.state()['program.phase'], 'dry')
        heat_before = self.state()['water.heater_energy_j']
        self.host.step(29)
        self.assertEqual(self.state()['program.phase'], 'dry')
        self.assertEqual(self.state()['water.heater_energy_j'], heat_before)
        self.host.step(1)
        self.assertEqual(self.state()['program.phase'], 'completed')
        self.assertFalse(self.state()['program.running'])
        self.assertEqual(self.state()['program.phase_elapsed_s'], 30)
        self.assertEqual(self.state()['water.heater_energy_j'], heat_before)
        self.assert_energy()

    def test_miele_loss_analytic_heatup_and_dry_passive_cooling(self):
        c, ua = 41800, 5
        self.configure(heat_loss_W_K=ua)
        self.start(wash_s=10, rinse_s=10, dry_s=100)
        heatup = -c / ua * math.log1p(-ua * 40 / 7500)
        self.host.step(heatup + 20)
        self.assertEqual(self.state()['program.phase'], 'dry')
        self.assertAlmostEqual(self.state()['water.temperature_k'], 333.15, places=9)
        heat_before = self.state()['water.heater_energy_j']
        self.host.step(100)
        self.assertAlmostEqual(self.state()['water.temperature_k'], 293.15 + 40 * math.exp(-ua * 100 / c), places=9)
        self.assertEqual(self.state()['water.heater_energy_j'], heat_before)
        self.assertEqual(self.state()['program.phase'], 'completed')
        self.assert_energy()

    def test_miele_unreachable_target_does_not_complete_a_wash(self):
        self.configure(effective_heat_capacity_J_K=10000, heat_loss_W_K=200)
        self.start(temperature=343.15, wash_s=1, rinse_s=1, dry_s=0)
        self.host.step(3600)
        self.assertLess(self.state()['water.temperature_k'], 343.15)
        self.assertEqual(self.state()['program.phase'], 'wash')
        self.assertEqual(self.state()['program.phase_elapsed_s'], 0)
        self.assertTrue(self.state()['program.running'])
        self.assertLessEqual(self.state()['water.heater_energy_j'], 7500 * 3600)
        self.assert_energy()

    def test_miele_three_basket_slots_door_constraints_and_write_routes(self):
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('basket.set_position', slot='middle', extension_m=.3)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok('door.open')
        self.assertEqual(self.state()['door.angle_rad'], math.pi / 2)
        for index, name in enumerate(('lower', 'middle', 'upper')):
            response = self.agent.write(instance_id='miele_a', state_id='basket.' + name + '.extension_m', value=.3, request_id='miele_basket_' + name)
            self.assertEqual(response['status'], 'succeeded')
            self.assertEqual(response['result'], {'slot': name, 'extension_m': .3})
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke('door.close')['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())
        for name in ('lower', 'middle', 'upper'): self.ok('basket.set_position', slot=name, extension_m=0)
        self.ok('door.close')
        self.assertTrue(self.state()['door.closed'])
        self.assertEqual(self.scene_calls[-1][1]['door.angle_rad'], 0)
        self.assertEqual(self.scene_calls[-1][1]['basket.middle.extension_m'], 0)

    def test_miele_busy_rejects_door_basket_program_and_setpoint_without_mutation(self):
        self.configure()
        self.start()
        actions = [('door.open', {}), ('door.close', {}), ('basket.set_position', {'slot': 'upper', 'extension_m': 0}),
            ('thermal.set_temperature', {'temperature_K': 343.15}), ('washing.configure_program', {'wash_s': 10, 'rinse_s': 10, 'dry_s': 0}), ('washing.start', {})]
        for capability, args in actions:
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(capability, **args)['error']['code'], 'BUSY')
            self.assertEqual(before, self.host.debug_snapshot())

    def test_miele_stop_retains_heat_elapsed_and_poweroff_stops(self):
        self.configure(heat_loss_W_K=1)
        self.start()
        self.host.step(300)
        before = self.state()
        self.ok('washing.stop')
        self.assertEqual(self.state()['water.temperature_k'], before['water.temperature_k'])
        self.assertEqual(self.state()['program.phase_elapsed_s'], before['program.phase_elapsed_s'])
        self.host.step(100)
        self.assertLess(self.state()['water.temperature_k'], before['water.temperature_k'])
        self.assertEqual(self.state()['water.heater_energy_j'], before['water.heater_energy_j'])
        self.ok('washing.start')
        self.host.step(1)
        before = self.state()
        self.ok('power.set', enabled=False)
        self.assertFalse(self.state()['program.running'])
        self.assertEqual(self.state()['program.phase'], 'stopped')
        self.assertEqual(self.state()['water.temperature_k'], before['water.temperature_k'])
        self.assert_energy()

    def test_miele_scene_backend_missing_is_unsupported_and_no_mutation(self):
        host = _MieleTestRuntime()
        host.create_instance('isolated')
        for capability, arguments in [('door.open', {}), ('door.close', {}), ('basket.set_position', {'slot': 'lower', 'extension_m': .2})]:
            before = host.debug_snapshot()
            response = host.invoke(instance_id='isolated', capability_id=capability, arguments=arguments, request_id=capability)
            self.assertEqual(response['status'], 'unsupported')
            self.assertEqual(response['error']['side_effects'], 'none')
            self.assertEqual(before, host.debug_snapshot())
            self.assertEqual(host.describe(instance_id='isolated')['capabilities'][capability]['availability'], 'unsupported')

    def test_miele_scene_failure_rollback_and_poststep_partial_failure(self):
        def fail(*args): raise RuntimeError('SYNTHETIC_PRIVATE_MIELE_CONFIG')
        self.configure()
        self.start()
        self.host.scene_adapter = fail
        before = self.host.debug_snapshot()
        response = self.invoke('washing.stop')
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['side_effects'], 'unknown')
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertNotIn('SYNTHETIC_PRIVATE_MIELE_CONFIG', json.dumps(response))
        with self.assertRaises(SceneSynchronizationError): self.host.step(10)
        self.assertEqual(self.host.time_s, 10)
        self.assertGreater(self.state()['water.temperature_k'], 293.15)
        self.assertEqual(len(self.host.debug_snapshot()['scene_faults']), 2)
        self.assertNotIn('SYNTHETIC_PRIVATE_MIELE_CONFIG', json.dumps(self.host.debug_snapshot()['scene_faults']))

    def test_miele_truth_filter_all_response_metadata_logs_scene_and_scopes(self):
        self.configure()
        self.start()
        self.host.step(17)
        denied = self.agent.read(instance_id='miele_a', state_ids=['water.temperature_k'])
        self.assertEqual(denied['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(denied['observations'], {})
        self.assertEqual(self.agent.read(instance_id='miele_b')['error']['code'], 'UNKNOWN_INSTANCE')
        self.assertEqual(self.agent.write(instance_id='miele_a', state_id='water.temperature_k', value=300, request_id='bad_truth_write')['error']['code'], 'ACCESS_DENIED')
        public = [self.agent.describe(instance_id='miele_a'), self.agent.read(instance_id='miele_a'), self.agent.logs(), denied, self.scene_calls]
        encoded = json.dumps(public)
        for hidden in ('water.temperature_k', 'water.heater_energy_j', 'water.environment_energy_j', 'water.stored_energy_change_j', 'SYNTHETIC_PRIVATE_MIELE_CONFIG', 'water_configuration'):
            self.assertNotIn(hidden, encoded)
        obs = self.judge.read(instance_id='miele_a', state_ids=['water.temperature_k'])['observations']['water.temperature_k']
        self.assertGreater(obs['value'], 293.15)
        indicated = self.agent.read(instance_id='miele_a', state_ids=['water.indicated_temperature_k'])['observations']['water.indicated_temperature_k']
        self.assertEqual(indicated['quality'], 'estimated')
        self.assertNotEqual(indicated['value'], obs['value'])
        self.host.register_principal('miele_no_scope', visibility='agent', instance_ids=[])
        self.assertEqual(self.host.session('miele_no_scope').describe(asset_type_id=_MIELE_TEST_TYPE)['error']['code'], 'ACCESS_DENIED')

    def test_miele_invalid_arguments_and_host_configuration_are_atomic(self):
        cases = [('power.set', {'enabled': 1}), ('thermal.set_temperature', {'temperature_K': True}), ('thermal.set_temperature', {'temperature_K': float('nan')}),
            ('door.open', {'role': 'evaluator'}), ('basket.set_position', {'slot': 'fourth', 'extension_m': .2}), ('basket.set_position', {'slot': 'lower', 'extension_m': -.1}),
            ('washing.configure_program', {'wash_s': 0, 'rinse_s': 1, 'dry_s': 0}), ('washing.configure_program', {'wash_s': 1, 'rinse_s': 1, 'dry_s': float('inf')})]
        for cap, args in cases:
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)['error']['code'], 'INVALID_ARGUMENT')
            self.assertEqual(before, self.host.debug_snapshot())
        for changes in ({'effective_heat_capacity_J_K': 0}, {'heat_loss_W_K': -1}, {'maximum_temperature_K': 280}, {'evidence': ''}, {'initial_temperature_K': float('nan')}):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError): self.configure(**changes)
            self.assertEqual(before, self.host.debug_snapshot())
        self.configure()
        self.ok('power.set', enabled=True)
        before = self.host.debug_snapshot()
        with self.assertRaises(ValueError): self.configure()
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.invoke('thermal.set_temperature', temperature_K=370)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_miele_request_replay_conflict_and_return_values_isolated(self):
        args = dict(instance_id='miele_a', capability_id='power.set', arguments={'enabled': True}, request_id='miele_duplicate')
        first = self.agent.invoke(**args)
        before = self.host.debug_snapshot()
        replay = self.agent.invoke(**args)
        self.assertEqual(first, replay)
        self.assertEqual(before, self.host.debug_snapshot())
        replay['result']['enabled'] = False
        self.assertTrue(self.agent.invoke(**args)['result']['enabled'])
        args['arguments'] = {'enabled': False}
        self.assertEqual(self.agent.invoke(**args)['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(len(self.agent.logs()), 1)

    def test_miele_double_instance_isolation_same_host_clock(self):
        before = copy.deepcopy(self.host.debug_snapshot()['instances']['miele_b'])
        self.configure()
        self.ok('door.open')
        self.ok('basket.set_position', slot='middle', extension_m=.25)
        self.ok('basket.set_position', slot='middle', extension_m=0)
        self.ok('door.close')
        self.start()
        self.host.step(60)
        self.assertEqual(before, self.host.debug_snapshot()['instances']['miele_b'])
        self.assertEqual(self.judge.read(instance_id='miele_b')['clock']['time_s'], 60)
        self.assertEqual(self.agent.read(instance_id='miele_a')['clock']['time_s'], 60)
        self.assertIsNone(self.state('miele_b')['water.temperature_k'])

    def test_miele_time_partition_matches_complete_program_and_energy(self):
        self.configure(heat_loss_W_K=5)
        self.start(wash_s=20, rinse_s=10, dry_s=100)
        other = _MieleTestRuntime()
        other.create_instance('other')
        other.configure_water_model('other', effective_heat_capacity_J_K=41800, heat_loss_W_K=5, initial_temperature_K=293.15, maximum_temperature_K=368.15, evidence='Synthetic partition fixture')
        for cap, args in [('power.set', {'enabled': True}), ('thermal.set_temperature', {'temperature_K': 333.15}), ('washing.configure_program', {'wash_s': 20, 'rinse_s': 10, 'dry_s': 100}), ('washing.start', {})]:
            other.invoke(instance_id='other', capability_id=cap, arguments=args, request_id=cap)
        self.host.step(600)
        for _ in range(120): other.step(5)
        a, b = self.state(), other.debug_snapshot()['instances']['other']['state']
        for sid in ('water.temperature_k', 'water.heater_energy_j', 'water.environment_energy_j', 'water.stored_energy_change_j', 'program.phase_elapsed_s'):
            self.assertAlmostEqual(a[sid], b[sid], places=6)
        self.assertEqual(a['program.phase'], b['program.phase'])
        self.assert_energy()

    def test_miele_invalid_step_read_inputs_and_zero_step_are_pure(self):
        before = self.host.debug_snapshot()
        for dt in (True, -1, float('inf'), float('nan'), 86401):
            with self.assertRaises(ValueError): self.host.step(dt)
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        for fields in ('door.closed', ['door.closed', 'door.closed'], [None]):
            self.assertEqual(self.agent.read(instance_id='miele_a', state_ids=fields)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.host.describe_host()['sim.step']['visibility'], 'trusted_host_only')
        for scope in ('miele_a', [None], ['missing']):
            with self.assertRaises(ValueError): self.host.register_principal('bad_scope', visibility='agent', instance_ids=scope)

    def test_miele_real_schemas_and_unknown_parameters(self):
        spec = _miele_test_metadata()['asset_types'][_MIELE_TEST_TYPE]
        for cap, info in spec['capabilities'].items():
            self.assertEqual(lint_schema(info['input_schema']), [], cap)
            self.assertEqual(lint_schema(info['output_schema']), [], cap)
            self.assertEqual(validate_value(info['example_arguments'][0], info['input_schema']), [], cap)
        self.configure()
        results = [('power.set', self.ok('power.set', enabled=True)), ('thermal.set_temperature', self.ok('thermal.set_temperature', temperature_K=333.15)),
            ('door.open', self.ok('door.open')), ('basket.set_position', self.ok('basket.set_position', slot='upper', extension_m=.2))]
        self.ok('basket.set_position', slot='upper', extension_m=0)
        results.extend([('door.close', self.ok('door.close')), ('washing.configure_program', self.ok('washing.configure_program', wash_s=1, rinse_s=1, dry_s=0)),
            ('washing.start', self.ok('washing.start')), ('washing.stop', self.ok('washing.stop'))])
        for cap, response in results:
            self.assertEqual(validate_value(response['result'], spec['capabilities'][cap]['output_schema']), [], cap)
            self.assertEqual(response['observations'], {})
        for sid, observation in self.judge.read(instance_id='miele_a')['observations'].items():
            self.assertEqual(validate_value(observation['value'], spec['state_slots'][sid]['value_schema']), [], sid)
            self.assertEqual(observation['unit'], spec['state_slots'][sid]['unit'])
        for param in ('thermal_capacity_J_K', 'heat_loss_W_K', 'water_charge_kg', 'manufacturer_heatup_s'):
            self.assertIsNone(spec['parameters'][param]['value'])
        self.assertEqual(spec['parameters']['water_heater_power_W']['value'], 7500)
        self.assertEqual(spec['parameters']['whole_machine_mass_kg']['value'], 251)
    def test_miele_zero_dry_phase_finishes_at_rinse_boundary(self):
        self.configure()
        self.start(temperature=293.15, wash_s=1, rinse_s=1, dry_s=0)
        self.host.step(2)
        self.assertEqual(self.state()['program.phase'], 'completed')
        self.assertFalse(self.state()['program.running'])
        self.assertEqual(self.state()['program.phase_elapsed_s'], 0)
        self.assertEqual(self.state()['water.heater_energy_j'], 0)

# END CHEM-191 Miele PLW 6111 runtime tests.

# BEGIN CHEM-047 Multiwave 7101 runtime tests (append-only batch scope).
from basic40_runtime_thermal import (Multiwave7101Runtime as _MultiwaveTestRuntime,
    MULTIWAVE_7101_TYPE as _MULTIWAVE_TEST_TYPE, MULTIWAVE_7101_CAPACITY_J_K as _MULTIWAVE_TEST_C,
    export_multiwave_7101_metadata as _multiwave_test_metadata,
    multiwave_7101_calibration_report as _multiwave_test_calibration)


class Multiwave7101RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = _MultiwaveTestRuntime(scene_adapter=lambda sid, updates: self.calls.append((sid, copy.deepcopy(updates))))
        self.host.create_instance('multiwave_a')
        self.host.create_instance('multiwave_b')
        self.host.register_principal('multiwave_learner', visibility='agent', instance_ids=['multiwave_a'])
        self.host.register_principal('multiwave_judge', visibility='evaluator')
        self.agent, self.judge = self.host.session('multiwave_learner'), self.host.session('multiwave_judge')
        self.counter = 0

    def invoke(self, cap, **args):
        self.counter += 1
        return self.agent.invoke(instance_id='multiwave_a', capability_id=cap, arguments=args, request_id=f'multiwave_req_{self.counter}')

    def ok(self, cap, **args):
        response = self.invoke(cap, **args)
        self.assertEqual(response['status'], 'succeeded', response)
        return response

    def value(self, sid, instance='multiwave_a'):
        return self.judge.read(instance_id=instance, state_ids=[sid])['observations'][sid]['value']

    def heat(self, target=573.15, power=1500):
        self.ok('power.set', enabled=True)
        self.ok('thermal.set_temperature', temperature_K=target)
        self.ok('microwave.set_power', power_W=power)
        self.ok('microwave.set_enabled', enabled=True)

    def assert_energy(self):
        self.assertAlmostEqual(self.value('thermal.absorbed_energy_j') - self.value('thermal.environment_energy_j'), self.value('thermal.stored_energy_change_j'), places=7)
        self.assertAlmostEqual(self.value('thermal.stored_energy_change_j'), _MULTIWAVE_TEST_C * (self.value('thermal.temperature_k') - 298.15), places=7)

    def test_multiwave_reference_time_uses_explicit_power_assumptions(self):
        self.heat(target=448.15, power=1000)
        self.host.step(329)
        self.assertLess(self.value('thermal.temperature_k'), 448.15)
        self.host.step(1)
        self.assertAlmostEqual(self.value('thermal.temperature_k'), 448.15, places=9)
        self.assertAlmostEqual(self.value('thermal.absorbed_energy_j'), 330000.0, places=7)
        report = _multiwave_test_calibration()
        self.assertFalse(report['is_measured_calibration'])
        self.assertIsNone(report['source_absorbed_power_W'])
        self.assertIsNone(report['source_ambient_temperature_K'])
        self.assertLess(report['assumed_reference_absorbed_power_W'], 2000 * .55)
        self.assertAlmostEqual(report['reference_175C_at_assumed1000W_s'], 330)
        self.assertFalse(report['whole_instrument_mass_used_as_thermal_capacity'])
        self.assertIsNone(report['factory_ramp_K_s'])
        self.assert_energy()

    def test_multiwave_transferable_power_initial_slope_and_hard_maximum(self):
        self.heat()
        self.host.step(1e-4)
        rate = (self.value('thermal.temperature_k') - 298.15) / 1e-4
        self.assertAlmostEqual(rate, 1500 / _MULTIWAVE_TEST_C, delta=1e-6)
        self.assertLessEqual(self.value('thermal.absorbed_energy_j'), .150000001)
        self.host.step(600)
        self.assertAlmostEqual(self.value('thermal.temperature_k'), 573.15, places=9)
        self.assertAlmostEqual(self.value('microwave.delivered_power_w'), 275.0)
        before = self.value('thermal.absorbed_energy_j')
        self.host.step(1000)
        self.assertEqual(self.value('thermal.temperature_k'), 573.15)
        self.assertAlmostEqual(self.value('thermal.absorbed_energy_j') - before, 275000)
        self.assert_energy()

    def test_multiwave_target_write_changes_only_target_and_reads_are_pure(self):
        self.ok('power.set', enabled=True)
        before = self.host.debug_snapshot()['instances']['multiwave_a']['state']
        self.ok('thermal.set_temperature', temperature_K=448.15)
        after = self.host.debug_snapshot()['instances']['multiwave_a']['state']
        self.assertEqual([sid for sid in before if before[sid] != after[sid]], ['thermal.target_k'])
        snapshot = self.host.debug_snapshot()
        for _ in range(3):
            self.agent.read(instance_id='multiwave_a')
            self.agent.describe(instance_id='multiwave_a')
            self.agent.logs()
        self.assertEqual(snapshot, self.host.debug_snapshot())
        self.host.step(100)
        self.assertEqual(self.value('thermal.temperature_k'), 298.15)
        self.ok('microwave.set_enabled', enabled=True)
        self.host.step(1)
        self.assertGreater(self.value('thermal.temperature_k'), 298.15)

    def test_multiwave_physical_cooling_retains_heat_after_target_and_power_changes(self):
        self.heat()
        self.host.step(600)
        self.ok('thermal.set_temperature', temperature_K=348.15)
        self.assertEqual(self.value('thermal.temperature_k'), 573.15)
        self.host.step(60)
        self.assertAlmostEqual(self.value('thermal.temperature_k'), 298.15 + 275 * math.exp(-60 / _MULTIWAVE_TEST_C), places=9)
        self.assertEqual(self.value('microwave.delivered_power_w'), 0)
        self.ok('power.set', enabled=False)
        hot = self.value('thermal.temperature_k')
        self.host.step(600)
        self.assertAlmostEqual(self.value('thermal.temperature_k'), 298.15 + (hot - 298.15) * math.exp(-600 / _MULTIWAVE_TEST_C), places=9)
        self.assertFalse(self.value('microwave.enabled'))
        self.assert_energy()

    def test_multiwave_door_callback_inhibits_heating_without_pressure_claim(self):
        self.heat()
        self.host.step(200)
        hot = self.value('thermal.temperature_k')
        self.ok('door.open')
        self.assertEqual(self.calls[-1][1]['door.angle_rad'], math.pi / 2)
        self.assertFalse(self.value('microwave.enabled'))
        self.assertEqual(self.value('microwave.delivered_power_w'), 0)
        self.assertEqual(self.invoke('microwave.set_enabled', enabled=True)['error']['code'], 'PRECONDITION_FAILED')
        self.host.step(60)
        self.assertAlmostEqual(self.value('thermal.temperature_k'), 298.15 + (hot - 298.15) * math.exp(-6 * 60 / _MULTIWAVE_TEST_C), places=9)
        self.ok('door.close')
        self.assertFalse(self.value('microwave.enabled'))
        self.assertIsNone(self.value('rack.pressure_limit_pa'))
        self.assert_energy()

    def test_multiwave_all24_slots_are_visual_only_and_si_capacity(self):
        self.assertEqual(self.value('rack.slot_count'), 24)
        self.assertEqual(self.value('rack.slot_capacity_m3'), 8e-6)
        self.assertAlmostEqual(24 * self.value('rack.slot_capacity_m3'), .000192)
        self.assertEqual(self.invoke('rack.set_slot', slot_index=0, occupied=True)['error']['code'], 'PRECONDITION_FAILED')
        self.ok('door.open')
        for index in range(24): self.ok('rack.set_slot', slot_index=index, occupied=True)
        self.assertEqual(self.value('rack.occupied_slots'), [True] * 24)
        self.assertEqual(self.calls[-1][1]['rack.occupied_slots'], [True] * 24)
        self.assertEqual(self.value('thermal.temperature_k'), 298.15)
        self.assertEqual(self.value('thermal.stored_energy_change_j'), 0)
        self.assertEqual(self.value('rack.occupied_slots', 'multiwave_b'), [False] * 24)

    def test_multiwave_no_scene_means_scene_commands_unsupported(self):
        self.host.scene_adapter = None
        before = self.host.debug_snapshot()
        for cap, args in [('door.open', {}), ('door.close', {}), ('rack.set_slot', {'slot_index': 0, 'occupied': True})]:
            self.assertEqual(self.invoke(cap, **args)['status'], 'unsupported')
            self.assertEqual(self.agent.describe(instance_id='multiwave_a')['capabilities'][cap]['availability'], 'unsupported')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_multiwave_rejections_leave_state_clock_scene_unchanged(self):
        self.ok('power.set', enabled=True)
        cases = [('thermal.set_temperature', {'temperature_K': x}) for x in (True, None, float('nan'), float('inf'), 298.14, 573.151)]
        cases += [('microwave.set_power', {'power_W': x}) for x in (-1, 1500.1, True, float('nan'))]
        cases += [('power.set', {'enabled': 1}), ('microwave.set_enabled', {'enabled': 'true'}), ('door.open', {'force': True}), ('rack.set_slot', {'slot_index': -1, 'occupied': True}), ('rack.set_slot', {'slot_index': 24, 'occupied': True}), ('rack.set_slot', {'slot_index': True, 'occupied': True}), ('rack.set_slot', {'slot_index': 0.0, 'occupied': True}), ('rack.set_slot', {'slot_index': 0, 'occupied': 1}), ('thermal.set_temperature', {'temperature_K': 400, 'visibility': 'evaluator'})]
        before, calls = self.host.debug_snapshot(), copy.deepcopy(self.calls)
        for cap, args in cases:
            self.assertEqual(self.invoke(cap, **args)['status'], 'rejected')
            self.assertEqual(before, self.host.debug_snapshot())
            self.assertEqual(calls, self.calls)
        for target in (298.15, 573.15): self.ok('thermal.set_temperature', temperature_K=target)
        for power in (0, 1500): self.ok('microwave.set_power', power_W=power)

    def test_multiwave_agent_truth_and_scoped_identity_filtering(self):
        self.heat()
        self.host.step(20)
        payload = json.dumps([self.agent.describe(instance_id='multiwave_a'), self.agent.read(instance_id='multiwave_a'), self.agent.logs(), self.calls])
        for hidden in ('thermal.temperature_k', 'thermal.absorbed_energy_j', 'thermal.environment_energy_j', 'thermal.stored_energy_change_j'):
            self.assertNotIn(hidden, payload)
            self.assertEqual(self.agent.read(instance_id='multiwave_a', state_ids=[hidden]), self.agent.read(instance_id='multiwave_a', state_ids=['unknown_slot']))
        self.assertIn('thermal.temperature_k', self.judge.read(instance_id='multiwave_a')['observations'])
        self.assertFalse(hasattr(self.agent, 'step'))
        self.assertNotIn('sim.step', self.agent.describe(instance_id='multiwave_a')['capabilities'])
        denied = self.agent.read(instance_id='multiwave_b')
        self.assertEqual(denied['error']['code'], 'UNKNOWN_INSTANCE')
        self.assertIsNone(denied['state_revision'])
        with self.assertRaises(ValueError): self.host.register_principal('multiwave_learner', visibility='evaluator')
        response = self.agent.read(instance_id='multiwave_a')
        response['observations']['rack.occupied_slots']['value'][0] = True
        self.assertFalse(self.value('rack.occupied_slots')[0])
        self.assertEqual(response['observations']['thermal.indicated_temperature_k']['quality'], 'estimated')
        self.assertEqual(response['observations']['rack.pressure_limit_pa']['quality'], 'unavailable')

    def test_multiwave_write_routes_idempotent_replay_and_conflict(self):
        self.assertEqual(self.agent.write(instance_id='multiwave_a', state_id='power.enabled', value=True, request_id='multiwave_write')['status'], 'succeeded')
        args = {'instance_id': 'multiwave_a', 'capability_id': 'thermal.set_temperature', 'arguments': {'temperature_K': 448.15}, 'request_id': 'multiwave_replay'}
        first = self.agent.invoke(**args)
        self.host.step(30)
        before, call_count = self.host.debug_snapshot(), len(self.calls)
        self.assertEqual(self.agent.invoke(**args), first)
        self.assertEqual(self.agent.invoke(**{**args, 'arguments': {'temperature_K': 450.15}})['error']['code'], 'REQUEST_CONFLICT')
        for sid in ('thermal.temperature_k', 'door.angle_rad', 'rack.occupied_slots', 'rack.pressure_limit_pa', 'microwave.delivered_power_w'):
            self.assertEqual(self.agent.write(instance_id='multiwave_a', state_id=sid, value=10, request_id=sid)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(call_count, len(self.calls))

    def test_multiwave_shared_clock_and_instance_isolation(self):
        self.heat()
        self.host.step(330)
        self.assertGreater(self.value('thermal.temperature_k'), 298.15)
        self.assertEqual(self.value('thermal.temperature_k', 'multiwave_b'), 298.15)
        self.assertFalse(self.value('power.enabled', 'multiwave_b'))
        self.assertEqual(self.value('thermal.absorbed_energy_j', 'multiwave_b'), 0)
        for sid in ('multiwave_a', 'multiwave_b'):
            self.assertEqual(self.judge.read(instance_id=sid)['clock']['time_s'], 330)

    def test_multiwave_partition_independence_across_heating_hold_and_cooling(self):
        self.heat(target=448.15, power=1000)
        other = _MultiwaveTestRuntime()
        other.create_instance('other')
        for cap, args in [('power.set', {'enabled': True}), ('thermal.set_temperature', {'temperature_K': 448.15}), ('microwave.set_power', {'power_W': 1000}), ('microwave.set_enabled', {'enabled': True})]:
            other.invoke(instance_id='other', capability_id=cap, arguments=args, request_id=cap)
        self.host.step(600)
        for _ in range(60): other.step(10)
        for target in (348.15, 298.15):
            self.ok('thermal.set_temperature', temperature_K=target)
            other.invoke(instance_id='other', capability_id='thermal.set_temperature', arguments={'temperature_K': target}, request_id=str(target))
            self.host.step(3600)
            for _ in range(360): other.step(10)
        a, b = self.host.debug_snapshot()['instances']['multiwave_a']['state'], other.debug_snapshot()['instances']['other']['state']
        for sid in ('thermal.temperature_k', 'thermal.absorbed_energy_j', 'thermal.environment_energy_j', 'thermal.stored_energy_change_j'):
            self.assertAlmostEqual(a[sid], b[sid], places=6)
        self.assert_energy()

    def test_multiwave_low_power_equilibrium_below_unreachable_target(self):
        self.heat(power=100)
        self.host.step(86400)
        self.assertAlmostEqual(self.value('thermal.temperature_k'), 398.15, places=8)
        self.assertLess(self.value('thermal.temperature_k'), self.value('thermal.target_k'))
        self.assertEqual(self.value('microwave.delivered_power_w'), 100)
        self.assert_energy()

    def test_multiwave_callback_failures_do_not_leak_or_claim_rollback(self):
        def fail(*_): raise RuntimeError('private_multiwave_truth_marker')
        self.host.scene_adapter = fail
        before = self.host.debug_snapshot()
        response = self.invoke('power.set', enabled=True)
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['side_effects'], 'unknown')
        self.assertNotIn('private_multiwave_truth_marker', json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.scene_adapter = None
        self.heat()
        self.host.scene_adapter = fail
        with self.assertRaises(SceneSynchronizationError): self.host.step(60)
        self.assertEqual(self.host.time_s, 60)
        self.assertGreater(self.value('thermal.temperature_k'), 298.15)
        faults = self.host.debug_snapshot()['scene_faults']
        self.assertEqual(len(faults), 2)
        self.assertNotIn('private_multiwave_truth_marker', json.dumps(faults))

    def test_multiwave_hardware_pressure_chemistry_robot_explicitly_unsupported(self):
        before = self.host.debug_snapshot()
        for cap in ('hardware.connect', 'pressure.set_limit', 'chemistry.digest', 'robot.transfer'):
            response = self.invoke(cap)
            self.assertEqual(response['status'], 'unsupported')
            self.assertEqual(response['error']['side_effects'], 'none')
        self.assertEqual(self.invoke('sim.step', dt_s=1)['error']['code'], 'UNKNOWN_CAPABILITY')
        self.assertEqual(before, self.host.debug_snapshot())

    def test_multiwave_host_clock_zero_invalid_inputs_and_identity_validation(self):
        before = self.host.debug_snapshot()
        for dt in (-1, True, float('nan'), float('inf'), 86401):
            with self.assertRaises(ValueError): self.host.step(dt)
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.host.describe_host()['sim.step']['visibility'], 'trusted_host_only')
        for scope in ('multiwave_a', [None], ['unknown']):
            with self.assertRaises(ValueError): self.host.register_principal('bad_scope', visibility='agent', instance_ids=scope)
        for ambient in (True, float('nan'), 270, 320):
            with self.assertRaises(ValueError): _MultiwaveTestRuntime(ambient_temperature_K=ambient)

    def test_multiwave_real_results_match_schemas_and_all_units(self):
        spec = _multiwave_test_metadata()['asset_types'][_MULTIWAVE_TEST_TYPE]
        for cap, info in {**spec['capabilities'], **spec['declared_capabilities']}.items():
            self.assertEqual(lint_schema(info['input_schema']), [])
            self.assertEqual(lint_schema(info['output_schema']), [])
            self.assertEqual(validate_value(info['example_arguments'][0], info['input_schema']), [])
            if cap in spec['declared_capabilities']: continue
            if cap == 'microwave.set_enabled': self.ok('door.close')
            if cap == 'rack.set_slot': self.ok('door.open')
            response = self.ok(cap, **info['example_arguments'][0])
            self.assertEqual(validate_value(response['result'], info['output_schema']), [])
            self.assertEqual(response['observations'], {})
        for sid, obs in self.judge.read(instance_id='multiwave_a')['observations'].items():
            self.assertEqual(validate_value(obs['value'], spec['state_slots'][sid]['value_schema']), [])
            self.assertEqual(obs['unit'], spec['state_slots'][sid]['unit'])
        self.assertIsNone(spec['parameters']['rack_pressure_limit_Pa']['value'])
        self.assertIsNone(spec['parameters']['factory_ramp_rate_K_s']['value'])
# END CHEM-047 Multiwave 7101 runtime tests.

# BEGIN CHEM-018 Parr4591 tests (append-only asset scope).
from basic40_runtime_thermal import (Parr4591Runtime as _ParrTestRuntime, PARR_4591_TYPE as _PARR_TEST_TYPE,
    PARR_4591_MAX_ABSOLUTE_PA as _PARR_TEST_MAX_PA, PARR_4591_MAX_SPEED_RAD_S as _PARR_TEST_SPEED,
    PARR_4591_CAPACITY_J_K as _PARR_TEST_C, export_parr_4591_metadata as _parr_test_metadata)


class Parr4591RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.host=_ParrTestRuntime(scene_adapter=lambda sid,values:self.calls.append((sid,copy.deepcopy(values))))
        self.host.create_instance('parr_a');self.host.create_instance('parr_b')
        self.host.register_principal('parr_learner',visibility='agent',instance_ids=['parr_a'])
        self.host.register_principal('parr_judge',visibility='evaluator')
        self.agent=self.host.session('parr_learner');self.judge=self.host.session('parr_judge');self.counter=0

    def invoke(self,cap,**args):
        self.counter+=1
        return self.agent.invoke(instance_id='parr_a',capability_id=cap,arguments=args,request_id=f'parr_req_{self.counter}')

    def ok(self,cap,**args):
        response=self.invoke(cap,**args);self.assertEqual(response['status'],'succeeded',response);return response

    def value(self,sid,instance='parr_a'):
        return self.judge.read(instance_id=instance,state_ids=[sid])['observations'][sid]['value']

    def heat(self,target=498.15):
        self.ok('power.set',enabled=True);self.ok('thermal.set_temperature',temperature_K=target);self.ok('heater.set_enabled',enabled=True)

    def assert_energy(self):
        self.assertAlmostEqual(self.value('thermal.heater_energy_j')-self.value('thermal.environment_energy_j'),self.value('thermal.stored_energy_change_j'),places=7)
        self.assertAlmostEqual(self.value('thermal.stored_energy_change_j'),_PARR_TEST_C*(self.value('thermal.temperature_k')-298.15),places=7)

    def unchanged_rejection(self,cap,code,**args):
        before=copy.deepcopy(self.host.debug_snapshot());calls=len(self.calls)
        response=self.invoke(cap,**args)
        self.assertEqual(response['status'],'rejected');self.assertEqual(response['error']['code'],code)
        self.assertEqual(response['error']['side_effects'],'none');self.assertEqual(before,self.host.debug_snapshot());self.assertEqual(calls,len(self.calls))

    def test_parr_target_write_only_target_and_reads_do_not_advance(self):
        self.ok('power.set',enabled=True);before=self.host.debug_snapshot()['instances']['parr_a']['state']
        self.ok('thermal.set_temperature',temperature_K=373.15)
        after=self.host.debug_snapshot()['instances']['parr_a']['state']
        self.assertEqual([sid for sid in before if before[sid]!=after[sid]],['thermal.target_k'])
        before=self.host.debug_snapshot()
        for _ in range(4):self.agent.describe(instance_id='parr_a');self.agent.read(instance_id='parr_a');self.agent.logs()
        self.assertEqual(before,self.host.debug_snapshot());self.host.step(10);self.assertEqual(self.value('thermal.temperature_k'),298.15)

    def test_parr_finite_700w_initial_slope_and_assumed_heatup(self):
        self.heat();self.host.step(.0001)
        self.assertAlmostEqual((self.value('thermal.temperature_k')-298.15)/.0001,.7,delta=1e-6)
        expected=-1000/.7*math.log(1-.7*200/700)
        self.host.step(expected-.0002);self.assertLess(self.value('thermal.temperature_k'),498.15)
        self.host.step(.0002);self.assertAlmostEqual(self.value('thermal.temperature_k'),498.15,places=9)
        self.assertAlmostEqual(self.value('heater.delivered_power_w'),140);self.assert_energy()
        meta=_parr_test_metadata()['asset_types'][_PARR_TEST_TYPE]['parameters']
        self.assertEqual(meta['thermal_capacity_J_K']['evidence'],'assumption');self.assertIsNone(meta['manufacturer_heatup_time_s']['value'])

    def test_parr_closed_temperature_pressure_coupling_and_gas_conservation(self):
        self.host.configure_gas_charge('parr_a',pressure_Pa=2000000,evidence='synthetic inert gas')
        n=self.value('gas.moles');p=self.value('pressure.pressure_pa');t=self.value('thermal.temperature_k')
        self.heat();self.host.step(200)
        self.assertAlmostEqual(self.value('pressure.pressure_pa')/p,self.value('thermal.temperature_k')/t,places=12)
        self.assertEqual(n,self.value('gas.moles'));self.assertEqual(self.value('gas.exchanged_moles'),0)
        self.assert_energy()

    def test_parr_overpressure_target_rejected_without_any_process_or_scene_effect(self):
        self.host.configure_gas_charge('parr_a',pressure_Pa=18000000,evidence='synthetic high-pressure fixture')
        self.ok('power.set',enabled=True)
        self.unchanged_rejection('thermal.set_temperature','OVERPRESSURE',temperature_K=498.15)
        self.ok('thermal.set_temperature',temperature_K=320)
        self.ok('heater.set_enabled',enabled=True);self.host.step(1000)
        self.assertLessEqual(self.value('pressure.pressure_pa'),_PARR_TEST_MAX_PA)

    def test_parr_absolute_gauge_pressure_boundary_and_temperature_limit(self):
        self.host.configure_gas_charge('parr_a',pressure_Pa=_PARR_TEST_MAX_PA,evidence='synthetic exact limit')
        self.assertEqual(self.value('pressure.maximum_gauge_pressure_pa'),20700000)
        self.assertAlmostEqual(self.value('pressure.pressure_pa')-101325,20700000)
        self.ok('power.set',enabled=True)
        self.unchanged_rejection('thermal.set_temperature','OVERPRESSURE',temperature_K=298.16)
        self.unchanged_rejection('thermal.set_temperature','INVALID_ARGUMENT',temperature_K=498.1501)

    def test_parr_vent_open_is_target_only_then_finite_pressure_relaxation(self):
        self.host.configure_gas_charge('parr_a',pressure_Pa=1000000,evidence='synthetic gas fixture')
        before=self.value('pressure.pressure_pa');self.ok('vent.set_open',open=True)
        self.assertEqual(before,self.value('pressure.pressure_pa'));self.host.step(15)
        self.assertAlmostEqual(self.value('pressure.pressure_pa'),101325+(before-101325)*math.exp(-1),places=7)
        self.assertLess(self.value('gas.moles'),self.value('gas.initial_moles'))
        self.assertAlmostEqual(self.value('gas.initial_moles')+self.value('gas.exchanged_moles'),self.value('gas.moles'),places=15)

    def test_parr_vent_close_forecast_rejects_without_effect(self):
        self.host.configure_gas_charge('parr_a',pressure_Pa=18000000,evidence='synthetic gas fixture')
        self.ok('vent.set_open',open=True);self.ok('power.set',enabled=True);self.ok('thermal.set_temperature',temperature_K=498.15)
        self.unchanged_rejection('vent.set_open','OVERPRESSURE',open=False)
        self.host.step(60);self.ok('vent.set_open',open=False)

    def test_parr_open_lid_pressure_interlock_and_no_hidden_venting(self):
        self.host.configure_gas_charge('parr_a',pressure_Pa=300000,evidence='synthetic gas fixture')
        self.unchanged_rejection('lid.open','PRECONDITION_FAILED')
        self.ok('vent.set_open',open=True);self.host.step(120);self.ok('lid.open')
        self.assertFalse(self.value('lid.closed'));self.assertEqual(self.value('lid.lift_m'),.12)
        self.assertAlmostEqual(self.value('pressure.pressure_pa'),101325)
        self.ok('lid.close');self.assertEqual(self.value('lid.lift_m'),0)

    def test_parr_open_lid_hot_temperature_interlock(self):
        self.heat(target=350);self.host.step(100);self.ok('power.set',enabled=False)
        self.ok('vent.set_open',open=True);self.host.step(100)
        self.assertGreater(self.value('thermal.temperature_k'),323.15)
        self.unchanged_rejection('lid.open','PRECONDITION_FAILED')
        self.host.step(3000);self.ok('lid.open')

    def test_parr_open_lid_heater_and_stirring_interlocks(self):
        self.ok('power.set',enabled=True);self.ok('heater.set_enabled',enabled=True)
        self.unchanged_rejection('lid.open','PRECONDITION_FAILED')
        self.ok('heater.set_enabled',enabled=False);self.ok('stirrer.set_speed',speed_rad_s=2)
        self.unchanged_rejection('lid.open','PRECONDITION_FAILED')
        self.ok('stirrer.set_speed',speed_rad_s=0);self.ok('lid.open')
        self.unchanged_rejection('heater.set_enabled','PRECONDITION_FAILED',enabled=True)
        self.unchanged_rejection('stirrer.set_speed','PRECONDITION_FAILED',speed_rad_s=2)

    def test_parr_stirring_host_only_integration_and_independent_instances(self):
        self.ok('power.set',enabled=True);self.ok('stirrer.set_speed',speed_rad_s=2.5)
        self.assertEqual(self.value('stirrer.angle_rad'),0);self.host.step(.5)
        self.assertAlmostEqual(self.value('stirrer.angle_rad'),1.25);self.assertEqual(self.value('stirrer.angle_rad','parr_b'),0)
        self.assertEqual([v for sid,v in self.calls if sid=='parr_a'][-1]['stirrer.angle_rad'],1.25)
        self.ok('stirrer.set_speed',speed_rad_s=_PARR_TEST_SPEED);self.host.step(10)
        self.assertLess(self.value('stirrer.angle_rad'),2*math.pi)
        self.unchanged_rejection('stirrer.set_speed','INVALID_ARGUMENT',speed_rad_s=_PARR_TEST_SPEED+.1)

    def test_parr_power_off_preserves_heat_and_pressure_then_passive_cooling(self):
        self.heat();self.host.step(400);before=self.value('thermal.temperature_k');p=self.value('pressure.pressure_pa')
        self.ok('stirrer.set_speed',speed_rad_s=10);self.ok('power.set',enabled=False)
        self.assertEqual(self.value('thermal.temperature_k'),before);self.assertEqual(self.value('pressure.pressure_pa'),p)
        self.assertEqual(self.value('stirrer.speed_rad_s'),0);self.assertFalse(self.value('heater.enabled'))
        self.host.step(100);self.assertAlmostEqual(self.value('thermal.temperature_k'),298.15+(before-298.15)*math.exp(-.7*100/1000),places=9)
        self.assertLess(self.value('pressure.pressure_pa'),p);self.assert_energy()

    def test_parr_lower_setpoint_is_not_instant_cooling(self):
        self.heat();self.host.step(400);self.ok('thermal.set_temperature',temperature_K=348.15)
        self.assertEqual(self.value('thermal.temperature_k'),498.15);self.host.step(100)
        self.assertGreater(self.value('thermal.temperature_k'),348.15);self.assertEqual(self.value('heater.delivered_power_w'),0);self.assert_energy()

    def test_parr_closed_thermal_step_partition_invariance(self):
        other=_ParrTestRuntime();other.create_instance('other')
        for i,(cap,args) in enumerate([('power.set',{'enabled':True}),('thermal.set_temperature',{'temperature_K':498.15}),('heater.set_enabled',{'enabled':True})]):
            other.invoke(instance_id='other',capability_id=cap,arguments=args,request_id=str(i))
        self.heat();self.host.step(500)
        for _ in range(100):other.step(5)
        second=other.debug_snapshot()['instances']['other']['state']
        for sid in ('thermal.temperature_k','pressure.pressure_pa','thermal.heater_energy_j','thermal.environment_energy_j'):
            self.assertAlmostEqual(self.value(sid),second[sid],delta=1e-6)

    def test_parr_agent_read_describe_results_logs_exclude_truth(self):
        private=[sid for sid,info in _parr_test_metadata()['asset_types'][_PARR_TEST_TYPE]['state_slots'].items() if info['visibility']!='agent']
        self.heat();self.host.step(3)
        self.ok('heater.set_enabled',enabled=False)
        public=json.dumps([self.agent.read(instance_id='parr_a'),self.agent.describe(instance_id='parr_a'),self.agent.logs()])
        for sid in private:
            self.assertNotIn('"'+sid+'"',public)
            denied=self.agent.read(instance_id='parr_a',state_ids=[sid]);self.assertEqual(denied['error']['code'],'ACCESS_DENIED');self.assertEqual(denied['observations'],{})
        for _,payload in self.calls:self.assertFalse(set(private)&set(payload))

    def test_parr_session_scope_and_host_separation(self):
        for response in [self.agent.read(instance_id='parr_b'),self.agent.describe(instance_id='parr_b'),self.agent.invoke(instance_id='parr_b',capability_id='power.set',arguments={'enabled':True},request_id='scope'),self.agent.write(instance_id='parr_b',state_id='power.enabled',value=True,request_id='scope2')]:
            self.assertEqual(response['error']['code'],'UNKNOWN_INSTANCE')
        self.assertFalse(hasattr(self.agent,'step'));self.assertFalse(hasattr(self.agent,'configure_gas_charge'))
        with self.assertRaises(ValueError):self.host.register_principal('parr_learner',visibility='evaluator')
        with self.assertRaises(TypeError):self.agent.read(instance_id='parr_a',visibility='debug')
        self.host.register_principal('nobody',visibility='agent',instance_ids=[])
        self.assertEqual(self.host.session('nobody').describe(asset_type_id=_PARR_TEST_TYPE)['error']['code'],'ACCESS_DENIED')

    def test_parr_write_routes_and_no_direct_truth_write(self):
        response=self.agent.write(instance_id='parr_a',state_id='power.enabled',value=True,request_id='write_power')
        self.assertEqual(response['status'],'succeeded')
        self.assertEqual(self.agent.write(instance_id='parr_a',state_id='thermal.target_k',value=350,request_id='write_temperature')['result'],{'setpoint_K':350.0})
        before=self.host.debug_snapshot()
        for sid in ('thermal.temperature_k','pressure.pressure_pa','lid.lift_m','gas.moles'):
            self.assertEqual(self.judge.write(instance_id='parr_a',state_id=sid,value=1,request_id='forbidden_'+sid)['error']['code'],'ACCESS_DENIED')
        self.assertEqual(before,self.host.debug_snapshot())

    def test_parr_malformed_arguments_reject_without_mutation(self):
        for value in (True,False,float('nan'),float('inf'),'350',None,[],{}):
            self.unchanged_rejection('thermal.set_temperature','INVALID_ARGUMENT',temperature_K=value)
        for value in (1,'yes',None):self.unchanged_rejection('vent.set_open','INVALID_ARGUMENT',open=value)
        for value in (-1,True,float('inf'),'5'):self.unchanged_rejection('stirrer.set_speed','INVALID_ARGUMENT',speed_rad_s=value)
        self.unchanged_rejection('power.set','INVALID_ARGUMENT',enabled=True,unknown=1)
        for ids in ('power.enabled',['power.enabled','power.enabled'],[1]):self.assertEqual(self.agent.read(instance_id='parr_a',state_ids=ids)['error']['code'],'INVALID_ARGUMENT')

    def test_parr_request_replay_and_conflict(self):
        args=dict(instance_id='parr_a',capability_id='power.set',arguments={'enabled':True},request_id='repeat')
        first=self.agent.invoke(**args);calls=len(self.calls);before=self.host.debug_snapshot()
        self.assertEqual(first,self.agent.invoke(**args));self.assertEqual(calls,len(self.calls));self.assertEqual(before,self.host.debug_snapshot())
        args['arguments']={'enabled':False};self.assertEqual(self.agent.invoke(**args)['error']['code'],'REQUEST_CONFLICT');self.assertEqual(before,self.host.debug_snapshot())

    def test_parr_two_hosts_do_not_share_state_clock_or_requests(self):
        other=_ParrTestRuntime();other.create_instance('parr_a');before=other.debug_snapshot()
        self.heat();self.host.step(10);self.assertEqual(before,other.debug_snapshot())
        self.assertEqual(self.value('thermal.temperature_k','parr_b'),298.15)
        self.assertEqual(other.read(instance_id='parr_a')['clock']['time_s'],0)

    def test_parr_configure_charge_validation_and_callback_failure_are_transactional(self):
        before=self.host.debug_snapshot();calls=len(self.calls)
        for pressure in (True,0,101324,_PARR_TEST_MAX_PA+1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.host.configure_gas_charge('parr_a',pressure_Pa=pressure,evidence='fixture')
        with self.assertRaises(ValueError):self.host.configure_gas_charge('parr_a',pressure_Pa=200000,evidence='')
        self.assertEqual(before,self.host.debug_snapshot());self.assertEqual(calls,len(self.calls))
        def fail(*_):raise RuntimeError('SYNTHETIC_PRIVATE_PARR_SECRET')
        self.host.scene_adapter=fail
        with self.assertRaises(RuntimeError) as error:self.host.configure_gas_charge('parr_a',pressure_Pa=200000,evidence='fixture')
        self.assertNotIn('SYNTHETIC_PRIVATE',str(error.exception));self.assertEqual(before,self.host.debug_snapshot())

    def test_parr_charge_requires_power_off_closed_lid_and_closed_vent(self):
        for cap,args in [('power.set',{'enabled':True}),('vent.set_open',{'open':True}),('lid.open',{})]:
            self.ok(cap,**args);before=self.host.debug_snapshot()
            with self.assertRaises(ValueError):self.host.configure_gas_charge('parr_a',pressure_Pa=200000,evidence='fixture')
            self.assertEqual(before,self.host.debug_snapshot())
            if cap=='power.set':self.ok(cap,enabled=False)
            elif cap=='vent.set_open':self.ok(cap,open=False)
            else:self.ok('lid.close')

    def test_parr_missing_scene_backend_and_callback_fault_semantics(self):
        self.host.scene_adapter=None;before=self.host.debug_snapshot()
        self.assertEqual(self.invoke('lid.open')['status'],'unsupported');self.assertEqual(before,self.host.debug_snapshot())
        self.assertEqual(self.agent.describe(instance_id='parr_a')['capabilities']['lid.open']['availability'],'unsupported')
        def fail(*_):raise RuntimeError('SYNTHETIC_PRIVATE_PARR_SECRET')
        self.host.scene_adapter=fail;response=self.invoke('power.set',enabled=True)
        self.assertEqual(response['status'],'failed');self.assertEqual(response['error']['side_effects'],'unknown')
        self.assertEqual(before,self.host.debug_snapshot());self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps([response,self.agent.logs()]))

    def test_parr_host_step_scene_failure_reports_committed_time_and_process(self):
        self.heat()
        def fail(*_):raise RuntimeError('SYNTHETIC_PRIVATE_PARR_SECRET')
        self.host.scene_adapter=fail
        with self.assertRaises(SceneSynchronizationError) as error:self.host.step(5)
        self.assertNotIn('SYNTHETIC_PRIVATE',str(error.exception));self.assertEqual(self.host.time_s,5)
        self.assertGreater(self.value('thermal.temperature_k'),298.15);self.assertEqual(len(self.host.debug_snapshot()['scene_faults']),2)

    def test_parr_host_step_is_transactional_when_pressure_forecast_is_invalid(self):
        # Trusted evaluator injects an inconsistent state to exercise the final world transaction guard.
        self.heat();bad=self.host._instances['parr_b']['state'];bad['gas.moles']*=1000
        before=self.host.debug_snapshot();calls=len(self.calls)
        with self.assertRaises(ValueError):self.host.step(10)
        self.assertEqual(before,self.host.debug_snapshot());self.assertEqual(calls,len(self.calls))

    def test_parr_host_clock_validation_and_unknown_unsupported_capabilities(self):
        before=self.host.debug_snapshot()
        for dt in (True,-1,86401,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.host.step(dt)
        self.host.step(0);self.assertEqual(before,self.host.debug_snapshot())
        for cap in ('chemistry.react','hardware.connect','robot.transfer'):
            response=self.invoke(cap);self.assertEqual(response['status'],'unsupported');self.assertEqual(response['error']['side_effects'],'none')
        self.unchanged_rejection('sim.step','UNKNOWN_CAPABILITY',dt_s=5)
        for ambient in (True,270,320,float('nan')):
            with self.assertRaises(ValueError):_ParrTestRuntime(ambient_temperature_K=ambient)
        for scope in ('parr_a',[None],['unknown']):
            with self.assertRaises(ValueError):self.host.register_principal('bad_scope',visibility='agent',instance_ids=scope)

    def test_parr_real_public_api_results_and_observations_match_schemas(self):
        spec=_parr_test_metadata()['asset_types'][_PARR_TEST_TYPE]
        for cap,info in {**spec['capabilities'],**spec['declared_capabilities']}.items():
            self.assertEqual(lint_schema(info['input_schema']),[]);self.assertEqual(lint_schema(info['output_schema']),[])
            self.assertEqual(validate_value(info['example_arguments'][0],info['input_schema']),[])
        examples=[('power.set',{'enabled':True}),('thermal.set_temperature',{'temperature_K':350}),('heater.set_enabled',{'enabled':True}),('heater.set_enabled',{'enabled':False}),('stirrer.set_speed',{'speed_rad_s':5}),('stirrer.set_speed',{'speed_rad_s':0}),('lid.open',{}),('lid.close',{}),('vent.set_open',{'open':True})]
        for cap,args in examples:
            response=self.ok(cap,**args);self.assertEqual(validate_value(response['result'],spec['capabilities'][cap]['output_schema']),[]);self.assertEqual(response['observations'],{})
        for sid,observation in self.judge.read(instance_id='parr_a')['observations'].items():
            self.assertEqual(validate_value(observation['value'],spec['state_slots'][sid]['value_schema']),[])
            self.assertEqual(observation['unit'],spec['state_slots'][sid]['unit'])
        self.assertEqual(self.value('vessel.seal_material'),'FKM');self.assertEqual(self.value('vessel.volume_m3'),25e-6)

    def test_parr_returned_state_and_metadata_are_copies(self):
        read=self.agent.read(instance_id='parr_a');read['observations']['power.enabled']['value']=True
        description=self.agent.describe(instance_id='parr_a');description['state_slots'].clear()
        snapshot=self.host.debug_snapshot();snapshot['instances']['parr_a']['state']['power.enabled']=True
        self.assertFalse(self.value('power.enabled'));self.assertIn('power.enabled',self.agent.describe(instance_id='parr_a')['state_slots'])
# END CHEM-018 Parr4591 runtime tests.

class Parr4591CoupledVentTests(unittest.TestCase):
    def run_case(self, steps, *, target=373.15, initial_pressure=101325, cooling=False):
        host=_ParrTestRuntime();host.create_instance('coupled')
        host.configure_gas_charge('coupled',pressure_Pa=initial_pressure,evidence='synthetic partition fixture')
        commands=[('power.set',{'enabled':True}),('thermal.set_temperature',{'temperature_K':target}),('heater.set_enabled',{'enabled':True})]
        for index,(cap,args) in enumerate(commands):
            self.assertEqual(host.invoke(instance_id='coupled',capability_id=cap,arguments=args,request_id=str(index))['status'],'succeeded')
        if cooling:
            host.step(1000)
            host.invoke(instance_id='coupled',capability_id='power.set',arguments={'enabled':False},request_id='off')
        host.invoke(instance_id='coupled',capability_id='vent.set_open',arguments={'open':True},request_id='vent')
        for dt in steps:host.step(dt)
        return host.debug_snapshot()['instances']['coupled']['state']

    def test_parr_vent_heating_external_step_partition_invariance(self):
        variants=[self.run_case([60]),self.run_case([1]*60),self.run_case([.1]*600)]
        for state in variants[1:]:
            for sid in ('pressure.pressure_pa','thermal.temperature_k','gas.moles','gas.exchanged_moles'):
                self.assertAlmostEqual(state[sid],variants[0][sid],delta=.00001 if sid.startswith('pressure') else 1e-9)
        self.assertGreater(variants[0]['pressure.pressure_pa'],104000)

    def test_parr_vent_cooling_and_setpoint_crossing_partition_invariance(self):
        for target,pressure,cooling in [(373.15,1000000,False),(320,1000000,True)]:
            first=self.run_case([300],target=target,initial_pressure=pressure,cooling=cooling)
            second=self.run_case([3]*100,target=target,initial_pressure=pressure,cooling=cooling)
            self.assertAlmostEqual(first['pressure.pressure_pa'],second['pressure.pressure_pa'],delta=.00002)
            self.assertAlmostEqual(first['gas.initial_moles']+first['gas.exchanged_moles'],first['gas.moles'],places=15)

    def test_parr_vent_long_step_stable_at_target(self):
        state=self.run_case([86400])
        self.assertAlmostEqual(state['thermal.temperature_k'],373.15)
        self.assertAlmostEqual(state['pressure.pressure_pa'],101325,delta=.00001)
        self.assertAlmostEqual(state['gas.initial_moles']+state['gas.exchanged_moles'],state['gas.moles'],places=15)

# BEGIN CHEM-021 Radleys Mya 4 tests (append-only batch scope).
from basic40_runtime_thermal import (RadleysMya4Runtime as _Mya4Runtime, MYA4_TYPE as _MYA4_TYPE,
    MYA4_ZONES as _MYA4_ZONES, MYA4_ASSUMPTIONS as _MYA4_A, MYA4_MIN_SPEED_RAD_S as _MYA4_MIN,
    MYA4_MAX_SPEED_RAD_S as _MYA4_MAX, export_radleys_mya4_metadata as _mya4_metadata)


class RadleysMya4RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.host=_Mya4Runtime(scene_adapter=lambda sid,payload:self.calls.append((sid,copy.deepcopy(payload))))
        self.host.create_instance('mya_a');self.host.create_instance('mya_b')
        self.host.register_principal('mya_learner',visibility='agent',instance_ids=['mya_a'])
        self.host.register_principal('mya_judge',visibility='evaluator')
        self.agent,self.judge=self.host.session('mya_learner'),self.host.session('mya_judge')
        self.counter=0
    def call(self,cap,**args):
        self.counter+=1
        return self.agent.invoke(instance_id='mya_a',capability_id=cap,arguments=args,request_id='mya_req_'+str(self.counter))
    def ok(self,cap,**args):
        result=self.call(cap,**args);self.assertEqual(result['status'],'succeeded',result);return result
    def state(self,instance='mya_a'):return self.host.debug_snapshot()['instances'][instance]['state']
    def val(self,sid,instance='mya_a'):return self.judge.read(instance_id=instance,state_ids=[sid])['observations'][sid]['value']
    def setup_thermal(self):
        self.host.configure_cooling_source(available=True,temperature_K=278.15,flow_m3_s=0.003/60)
        self.ok('power.set',enabled=True)
    def enable(self,index=0,target=373.15):
        self.ok('thermal.set_temperature',zone_index=index,temperature_K=target)
        self.ok('thermal.set_enabled',zone_index=index,enabled=True)
    def assert_energy(self):
        s=self.state();sum_cross=0.0
        for z in _MYA4_ZONES:
            cl=_MYA4_A['liquid_density_kg_m3']*_MYA4_A['liquid_specific_heat_J_kg_K']*s[z+'.vessel.fill_volume_m3']
            stored=_MYA4_A['effective_block_capacity_J_K']*(s[z+'.thermal.temperature_k']-298.15)+cl*(s[z+'.thermal.liquid_temperature_k']-298.15)
            ledger=s[z+'.thermal.control_energy_j']-s[z+'.thermal.environment_energy_j']+s[z+'.thermal.adjacent_energy_j']
            self.assertAlmostEqual(stored,ledger,delta=2e-6)
            sum_cross+=s[z+'.thermal.adjacent_energy_j']
        self.assertAlmostEqual(sum_cross,0,delta=1e-8)
    def test_mya4_target_only_read_purity_and_host_clock(self):
        self.setup_thermal();before=self.state()
        self.ok('thermal.set_temperature',zone_index=2,temperature_K=373.15)
        after=self.state();self.assertEqual([sid for sid in before if before[sid]!=after[sid]],['zone_3.thermal.target_k'])
        snapshot=self.host.debug_snapshot()
        for _ in range(3):self.agent.describe(instance_id='mya_a');self.agent.read(instance_id='mya_a');self.agent.logs()
        self.assertEqual(snapshot,self.host.debug_snapshot());self.assertEqual(self.host.time_s,0)
        self.host.step(100);self.assertEqual(self.val('zone_3.thermal.temperature_k'),298.15)
        self.ok('thermal.set_enabled',zone_index=2,enabled=True);self.host.step(1)
        self.assertGreater(self.val('zone_3.thermal.temperature_k'),298.15)
        self.assertEqual(self.judge.read(instance_id='mya_b')['clock']['time_s'],101)
    def test_mya4_schema_bounds_and_rejections_are_atomic(self):
        self.setup_thermal()
        cases=[('thermal.set_temperature',{'zone_index':0,'temperature_K':x}) for x in (True,None,float('nan'),float('inf'),243.149,453.151)]
        cases += [('stirring.set_speed',{'zone_index':0,'angular_speed_rad_s':x}) for x in (0,_MYA4_MIN-1e-6,_MYA4_MAX+1e-6,True)]
        cases += [('thermal.set_enabled',{'zone_index':x,'enabled':True}) for x in (-1,4,0.0,True,'0')]
        cases += [('power.set',{'enabled':1}),('thermal.set_temperature',{'zone_index':0,'temperature_K':333.15,'role':'evaluator'}),('vessel.configure',{'zone_index':0,'capacity_m3':1e-6,'fill_volume_m3':1e-6}),('vessel.configure',{'zone_index':0,'capacity_m3':4.0001e-4,'fill_volume_m3':5e-5}),('vessel.configure',{'zone_index':0,'capacity_m3':2e-6,'fill_volume_m3':3e-6}),('vessel.configure',{'zone_index':0,'capacity_m3':2e-6,'fill_volume_m3':0}),('thermal.set_probe_mode',{'zone_index':0,'mode':'fake'})]
        before,calls=self.host.debug_snapshot(),copy.deepcopy(self.calls)
        for cap,args in cases:
            self.assertEqual(self.call(cap,**args)['status'],'rejected',(cap,args));self.assertEqual(before,self.host.debug_snapshot());self.assertEqual(calls,self.calls)
        for value in (243.15,453.15):self.ok('thermal.set_temperature',zone_index=0,temperature_K=value)
        for value in (_MYA4_MIN,_MYA4_MAX):self.ok('stirring.set_speed',zone_index=0,angular_speed_rad_s=value)
        for value in (2e-6,4e-4):self.ok('vessel.configure',zone_index=0,capacity_m3=value,fill_volume_m3=value/2)
    def test_mya4_water_is_required_for_heating_and_cooling_and_loss_stops(self):
        self.ok('power.set',enabled=True)
        self.ok('thermal.set_temperature',zone_index=0,temperature_K=373.15)
        self.assertEqual(self.call('thermal.set_enabled',zone_index=0,enabled=True)['error']['code'],'PRECONDITION_FAILED')
        self.host.step(10);self.assertEqual(self.val('zone_1.thermal.temperature_k'),298.15)
        self.setup_thermal();self.enable();self.host.step(200);hot=self.val('zone_1.thermal.temperature_k')
        self.host.configure_cooling_source(available=False,temperature_K=278.15,flow_m3_s=0.003/60)
        self.assertFalse(self.val('zone_1.thermal.enabled'));self.assertEqual(self.val('zone_1.thermal.temperature_k'),hot)
        self.host.step(50);self.assertLess(self.val('zone_1.thermal.temperature_k'),hot);self.assert_energy()
    def test_mya4_modes_cold_source_limits_and_adjacent_temperature_difference(self):
        self.setup_thermal();self.ok('thermal.set_temperature',zone_index=0,temperature_K=243.15)
        self.assertEqual(self.call('thermal.set_temperature',zone_index=1,temperature_K=453.15)['error']['code'],'PRECONDITION_FAILED')
        self.host.configure_cooling_source(available=True,temperature_K=288.15,flow_m3_s=0.003/60)
        self.assertEqual(self.call('thermal.set_enabled',zone_index=0,enabled=True)['error']['code'],'PRECONDITION_FAILED')
        self.ok('thermal.set_temperature',zone_index=0,temperature_K=248.15)
        self.assertEqual(self.call('thermal.set_probe_mode',zone_index=0,mode='liquid')['error']['code'],'PRECONDITION_FAILED')
        self.ok('thermal.set_temperature',zone_index=0,temperature_K=253.15);self.ok('thermal.set_probe_mode',zone_index=0,mode='liquid')
        self.assertEqual(self.call('thermal.set_temperature',zone_index=0,temperature_K=423.151)['error']['code'],'PRECONDITION_FAILED')
        self.ok('thermal.set_temperature',zone_index=0,temperature_K=423.15)
    def test_mya4_four_zone_independent_controls_with_explicit_weak_cross_conduction(self):
        self.setup_thermal()
        for i,target in enumerate((373.15,353.15,333.15,313.15)):self.enable(i,target)
        self.host.step(300)
        s=self.state();temps=[s[z+'.thermal.temperature_k'] for z in _MYA4_ZONES]
        self.assertGreater(temps[0],temps[1]);self.assertGreater(temps[1],temps[2]);self.assertGreater(temps[2],temps[3])
        for i,z in enumerate(_MYA4_ZONES):self.assertEqual(s[z+'.thermal.target_k'],(373.15,353.15,333.15,313.15)[i])
        self.assertEqual(self.val('zone_1.thermal.temperature_k','mya_b'),298.15);self.assertFalse(self.val('power.enabled','mya_b'))
        self.assert_energy()
    def test_mya4_nearest_neighbor_heat_transfer_and_energy_conservation(self):
        self.setup_thermal();self.enable();self.host.step(300)
        s=self.state()
        self.assertGreater(s['zone_2.thermal.temperature_k'],s['zone_3.thermal.temperature_k'])
        self.assertGreater(s['zone_3.thermal.temperature_k'],s['zone_4.thermal.temperature_k'])
        self.assertFalse(s['zone_2.thermal.enabled']);self.assertEqual(s['zone_2.thermal.control_energy_j'],0)
        self.assert_energy()
    def test_mya4_effective_fill_load_changes_liquid_heating_without_changing_capacity(self):
        self.setup_thermal()
        self.ok('vessel.configure',zone_index=0,capacity_m3=1e-4,fill_volume_m3=1e-5)
        self.ok('vessel.configure',zone_index=3,capacity_m3=1e-4,fill_volume_m3=8e-5)
        self.enable(0);self.enable(3);self.host.step(200)
        self.assertGreater(self.val('zone_1.thermal.liquid_temperature_k'),self.val('zone_4.thermal.liquid_temperature_k'))
        self.assertGreater(self.val('zone_4.thermal.temperature_k'),self.val('zone_4.thermal.liquid_temperature_k'))
        before=self.host.debug_snapshot();self.assertEqual(self.call('vessel.configure',zone_index=0,capacity_m3=2e-4,fill_volume_m3=1e-4)['error']['code'],'PRECONDITION_FAILED');self.assertEqual(before,self.host.debug_snapshot())
        self.assert_energy()
    def test_mya4_active_cooling_and_power_loss_have_finite_rates(self):
        self.setup_thermal();self.enable(target=263.15);self.host.step(200)
        cold=self.val('zone_1.thermal.temperature_k');self.assertLess(cold,298.15);self.assertGreater(cold,263)
        self.ok('power.set',enabled=False);self.assertEqual(self.val('zone_1.thermal.temperature_k'),cold)
        self.host.step(500);self.assertGreater(self.val('zone_1.thermal.temperature_k'),cold);self.assertLess(self.val('zone_1.thermal.temperature_k'),298.15)
        self.assert_energy()
    def test_mya4_stirring_si_phase_stop_and_scene_snapshot(self):
        self.setup_thermal()
        for i,speed in enumerate((100,300,600,1000)):
            self.ok('stirring.set_speed',zone_index=i,angular_speed_rad_s=speed*2*math.pi/60)
            self.ok('stirring.set_enabled',zone_index=i,enabled=True)
        self.host.step(.123)
        for i,z in enumerate(_MYA4_ZONES):
            expected=((100,300,600,1000)[i]*2*math.pi/60*.123)%(2*math.pi)
            self.assertAlmostEqual(self.val(z+'.stirring.angle_rad'),expected)
            self.assertEqual(self.calls[-2][1][z+'.stirring.angle_rad'],self.val(z+'.stirring.angle_rad'))
        angle=self.val('zone_1.stirring.angle_rad');self.ok('stirring.set_enabled',zone_index=0,enabled=False);self.host.step(.25)
        self.assertEqual(self.val('zone_1.stirring.angle_rad'),angle);self.assertEqual(self.val('zone_1.stirring.actual_rad_s'),0)
        self.ok('power.set',enabled=False);self.assertTrue(all(self.val(z+'.stirring.actual_rad_s')==0 for z in _MYA4_ZONES))
    def test_mya4_request_replay_conflict_scopes_and_write_routes(self):
        response=self.agent.write(instance_id='mya_a',state_id='power.enabled',value=True,request_id='mya_write')
        self.assertEqual(response['status'],'succeeded')
        first=self.agent.write(instance_id='mya_a',state_id='zone_2.thermal.target_k',value=333.15,request_id='mya_replay')
        self.host.step(1);before,calls=self.host.debug_snapshot(),len(self.calls)
        self.assertEqual(first,self.agent.invoke(instance_id='mya_a',capability_id='thermal.set_temperature',arguments={'zone_index':1,'temperature_K':333.15},request_id='mya_replay'))
        self.assertEqual(self.agent.write(instance_id='mya_a',state_id='zone_2.thermal.target_k',value=334.15,request_id='mya_replay')['error']['code'],'REQUEST_CONFLICT')
        for sid in ('zone_1.thermal.temperature_k','zone_1.stirring.angle_rad','zone_1.vessel.fill_volume_m3','cooling.available'):
            self.assertEqual(self.agent.write(instance_id='mya_a',state_id=sid,value=0,request_id=sid)['error']['code'],'ACCESS_DENIED')
        self.assertEqual(before,self.host.debug_snapshot());self.assertEqual(calls,len(self.calls))
        denied=self.agent.read(instance_id='mya_b');self.assertEqual(denied['error']['code'],'UNKNOWN_INSTANCE');self.assertIsNone(denied['state_revision'])
    def test_mya4_agent_truth_filtered_in_every_response_and_scene_payload(self):
        self.setup_thermal();self.enable();self.host.step(10)
        payload=json.dumps([self.agent.describe(instance_id='mya_a'),self.agent.read(instance_id='mya_a'),self.agent.logs(),self.calls])
        for z in _MYA4_ZONES:
            for suffix in ('thermal.temperature_k','thermal.liquid_temperature_k','thermal.control_energy_j','thermal.environment_energy_j','thermal.adjacent_energy_j'):
                hidden=z+'.'+suffix;self.assertNotIn(hidden,payload)
                self.assertEqual(self.agent.read(instance_id='mya_a',state_ids=[hidden]),self.agent.read(instance_id='mya_a',state_ids=['not_known']))
        self.assertIn('zone_1.thermal.temperature_k',self.judge.read(instance_id='mya_a')['observations'])
        self.assertFalse(hasattr(self.agent,'step'));self.assertFalse(hasattr(self.agent,'configure_cooling_source'))
        self.assertNotIn('sim.step',self.agent.describe(instance_id='mya_a')['capabilities'])
        self.assertEqual(self.agent.read(instance_id='mya_a',state_ids=['zone_1.thermal.indicated_temperature_k'])['observations']['zone_1.thermal.indicated_temperature_k']['quality'],'estimated')
        response=self.agent.read(instance_id='mya_a');response['observations']['zone_1.program.segments']['value'].append({'private':'tamper'})
        self.assertEqual(self.val('zone_1.program.segments'),[])
    def test_mya4_program_holds_after_arrival_and_finishes_independently(self):
        self.setup_thermal()
        self.ok('thermal.configure_program',zone_index=0,segments=[{'temperature_K':308.15,'hold_s':4},{'temperature_K':313.15,'hold_s':3}])
        self.ok('thermal.start_program',zone_index=0);self.host.step(5)
        self.assertEqual(self.val('zone_1.program.hold_elapsed_s'),0);self.assertEqual(self.val('zone_1.program.phase'),'approaching')
        self.assertEqual(self.call('thermal.set_temperature',zone_index=0,temperature_K=310)['error']['code'],'BUSY')
        self.host.step(1000)
        self.assertEqual(self.val('zone_1.program.phase'),'completed');self.assertFalse(self.val('zone_1.program.running'));self.assertFalse(self.val('zone_1.thermal.enabled'))
        self.assertEqual(self.val('zone_1.program.segment_index'),2);self.assertEqual(self.val('zone_1.program.hold_elapsed_s'),3)
        self.assertGreater(self.val('zone_1.program.reaction_elapsed_s'),7);self.assertEqual(self.val('zone_2.program.phase'),'idle')
        self.assert_energy()
    def test_mya4_program_cancel_loss_and_rejected_segments_preserve_state(self):
        self.setup_thermal()
        invalid=[[],[{'temperature_K':True,'hold_s':1}],[{'temperature_K':298.15,'hold_s':-1}],[{'temperature_K':298.15,'hold_s':1,'x':0}],[{'temperature_K':298.15,'hold_s':1}]*17]
        before=self.host.debug_snapshot()
        for segs in invalid:self.assertEqual(self.call('thermal.configure_program',zone_index=0,segments=segs)['status'],'rejected');self.assertEqual(before,self.host.debug_snapshot())
        self.ok('thermal.configure_program',zone_index=0,segments=[{'temperature_K':373.15,'hold_s':20}]);self.ok('thermal.start_program',zone_index=0);self.host.step(5)
        hot=self.val('zone_1.thermal.temperature_k');self.ok('thermal.stop_program',zone_index=0);self.assertEqual(self.val('zone_1.thermal.temperature_k'),hot);self.assertEqual(self.val('zone_1.program.phase'),'stopped')
        self.ok('thermal.start_program',zone_index=0);self.host.configure_cooling_source(available=False,temperature_K=278.15,flow_m3_s=0.003/60)
        self.assertEqual(self.val('zone_1.program.phase'),'interrupted_cooling');self.assertFalse(self.val('zone_1.program.running'))
    def test_mya4_step_partition_invariance_heating_cooling_and_stirring(self):
        self.setup_thermal();self.enable(0,373.15);self.enable(3,273.15)
        self.ok('stirring.set_speed',zone_index=0,angular_speed_rad_s=35);self.ok('stirring.set_enabled',zone_index=0,enabled=True)
        other=_Mya4Runtime();other.create_instance('other');other.configure_cooling_source(available=True,temperature_K=278.15,flow_m3_s=.003/60)
        for cap,args in [('power.set',{'enabled':True}),('thermal.set_temperature',{'zone_index':0,'temperature_K':373.15}),('thermal.set_enabled',{'zone_index':0,'enabled':True}),('thermal.set_temperature',{'zone_index':3,'temperature_K':273.15}),('thermal.set_enabled',{'zone_index':3,'enabled':True}),('stirring.set_speed',{'zone_index':0,'angular_speed_rad_s':35}),('stirring.set_enabled',{'zone_index':0,'enabled':True})]:other.invoke(instance_id='other',capability_id=cap,arguments=args,request_id=cap+str(args))
        self.host.step(47.31)
        for _ in range(157):other.step(.3)
        other.step(.21)
        a,b=self.state(),other.debug_snapshot()['instances']['other']['state']
        for z in _MYA4_ZONES:
            for suffix in ('thermal.temperature_k','thermal.liquid_temperature_k','thermal.control_energy_j','thermal.environment_energy_j','thermal.adjacent_energy_j','stirring.angle_rad'):
                self.assertAlmostEqual(a[z+'.'+suffix],b[z+'.'+suffix],delta=1e-6)
        self.assert_energy()
    def test_mya4_scene_failure_sanitization_and_post_step_partial_effects(self):
        def fail(*_):raise RuntimeError('private_mya4_marker')
        self.host.scene_adapter=fail;before=self.host.debug_snapshot();response=self.call('power.set',enabled=True)
        self.assertEqual(response['status'],'failed');self.assertEqual(response['error']['side_effects'],'unknown');self.assertNotIn('private_mya4_marker',json.dumps(response));self.assertEqual(before,self.host.debug_snapshot())
        self.host.scene_adapter=None;self.setup_thermal();self.enable();self.host.scene_adapter=fail
        with self.assertRaises(SceneSynchronizationError):self.host.step(1)
        self.assertEqual(self.host.time_s,1);self.assertGreater(self.val('zone_1.thermal.temperature_k'),298.15);self.assertEqual(len(self.host.debug_snapshot()['scene_faults']),2)
    def test_mya4_host_validation_and_unsupported_boundaries(self):
        before=self.host.debug_snapshot()
        for dt in (-1,True,float('nan'),float('inf'),86401):
            with self.assertRaises(ValueError):self.host.step(dt)
        self.host.step(0);self.assertEqual(before,self.host.debug_snapshot())
        for temp,flow in [(278.149,.003/60),(308.151,.003/60),(288.15,.001/60),(288.15,.006/60),(True,.003/60)]:
            with self.assertRaises(ValueError):self.host.configure_cooling_source(available=True,temperature_K=temp,flow_m3_s=flow)
        self.assertEqual(before,self.host.debug_snapshot())
        for cap in ('hardware.connect','robot.transfer','chemistry.react','stirring.overhead','thermal.ramp_program'):self.assertEqual(self.call(cap)['status'],'unsupported')
        self.assertEqual(self.call('sim.step',dt_s=1)['error']['code'],'UNKNOWN_CAPABILITY')
        for scope in ('mya_a',[None],['unknown']):
            with self.assertRaises(ValueError):self.host.register_principal('bad',visibility='agent',instance_ids=scope)
    def test_mya4_metadata_schemas_real_outputs_units_and_unknown_parameters(self):
        self.setup_thermal();spec=_mya4_metadata()['asset_types'][_MYA4_TYPE]
        for name,cap in {**spec['capabilities'],**spec['declared_capabilities']}.items():
            self.assertEqual(lint_schema(cap['input_schema']),[]);self.assertEqual(lint_schema(cap['output_schema']),[])
            self.assertEqual(validate_value(cap['example_arguments'][0],cap['input_schema']),[])
            if name in spec['declared_capabilities']:continue
            if name=='vessel.configure':self.ok('thermal.set_enabled',zone_index=0,enabled=False);self.ok('stirring.set_enabled',zone_index=0,enabled=False)
            response=self.ok(name,**cap['example_arguments'][0]);self.assertEqual(validate_value(response['result'],cap['output_schema']),[]);self.assertEqual(response['observations'],{})
        for sid,obs in self.judge.read(instance_id='mya_a')['observations'].items():
            self.assertEqual(validate_value(obs['value'],spec['state_slots'][sid]['value_schema']),[]);self.assertEqual(obs['unit'],spec['state_slots'][sid]['unit'])
        for name in ('factory_zone_heater_power_W','factory_zone_cooling_power_W','factory_block_capacity_J_K','factory_block_ambient_conductance_W_K','factory_heatup_time_s','factory_cooldown_time_s'):self.assertIsNone(spec['parameters'][name]['value'])
        self.assertFalse(_mya4_metadata()['is_measured_calibration']);self.assertEqual(spec['parameters']['whole_instrument_maximum_consumption_W']['value'],2000)
# END CHEM-021 Radleys Mya 4 tests.
