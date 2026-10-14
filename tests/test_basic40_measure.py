"""CHEM-001 force filtering and uniform adapter checks; synthetic fixtures only."""
import copy
import json
import math
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/chemistry-lab-assets/scripts"))
from asset_tool import lint_schema, validate_value
from basic40_runtime_measure import (FilteredAnalyticalBalance, XPR204ERuntime,
    XPR204E_FILTERS, XPR204E_TYPE, export_xpr204e_contract_metadata, xpr204e_filter_experiments)


class FilteredAnalyticalBalanceTests(unittest.TestCase):
    def settled(self, balance, mass, seconds=30):
        for _ in range(int(seconds * 100)):
            value = balance.update(mass * balance.g_cal, 0.01)
        return value

    def test_specified_gaussian_noise_all_levels_reaches_stable(self):
        report = xpr204e_filter_experiments(seeds=[0])
        self.assertEqual(report["result"], "passed")
        self.assertEqual(len(report["trials"]), 9)
        for trial in report["trials"]:
            self.assertLessEqual(trial["first_stable_s"], 60)
            band = XPR204E_FILTERS[trial["level"]]["stability_divisions"] * 1e-7
            self.assertLessEqual(trial["filtered_peak_to_peak_at_stable_kg"], band + 1e-15)
        self.assertTrue(all(row["first_stable_s"] is None for row in report["baseline_unmodified_px224e"]))

    def test_lowpass_closed_form_and_exact_time_hold(self):
        balance = FilteredAnalyticalBalance(filter_level="Low")
        balance.set_power(True)
        balance.update(0, 0.01)
        balance.update(0.1 * 9.80665, 0.1)
        self.assertAlmostEqual(balance.gross_kg, 0.1 * (1 - math.exp(-0.1 / 0.25)), places=14)
        self.assertFalse(balance.stable)
        balance = FilteredAnalyticalBalance(filter_level="Low")
        balance.set_power(True)
        for _ in range(150):
            balance.update(0.1 * 9.80665, 0.01)
        self.assertFalse(balance.stable)
        balance.update(0.1 * 9.80665, 0.01)
        self.assertTrue(balance.stable)

    def test_window_rejects_slow_drift_with_small_adjacent_changes(self):
        balance = FilteredAnalyticalBalance(filter_level="Low")
        balance.set_power(True)
        self.settled(balance, 0.1, 2)
        self.assertTrue(balance.stable)
        # Each 10ms input delta is only 10d, but total drift is 1500d per window.
        for k in range(1, 501):
            balance.update((0.1 + k * 1e-6) * 9.80665, 0.01)
        self.assertFalse(balance.stable)
        self.assertGreater(balance._window_range_kg, 512 * balance.readability_kg)

    def test_door_tilt_vibration_and_pan_gates_force_resettling(self):
        balance = FilteredAnalyticalBalance(filter_level="Low")
        balance.set_power(True)
        for kwargs in ({"door_fraction": 0.5}, {"tilt_rad": 0.01}, {"vibration": True}):
            self.settled(balance, 0.1, 2)
            self.assertTrue(balance.stable)
            result = balance.update(0.1 * 9.80665, 0.01, **kwargs)
            self.assertFalse(result["stable"])
            self.assertIsNotNone(result["net_kg"])
            balance.update(0.1 * 9.80665, 0.01)
            self.assertFalse(balance.stable)
        balance.pan_installed = False
        self.assertEqual(balance.update(0, 0.01)["status"], "PAN_REMOVED")

    def test_raw_overload_is_immediate_despite_filter_and_tare(self):
        balance = FilteredAnalyticalBalance(filter_level="High")
        balance.set_power(True)
        self.settled(balance, 0.2)
        balance.press("tare")
        result = balance.update(0.221 * 9.80665, 0.01)
        self.assertLess(balance.gross_kg, 0.220)
        self.assertEqual(result["status"], "OVERLOAD")
        self.assertIsNone(result["net_kg"])
        self.assertFalse(result["stable"])

    def test_tare_negative_net_and_force_gain_calibration(self):
        balance = FilteredAnalyticalBalance(filter_level="Low")
        balance.set_power(True)
        self.settled(balance, 0.02, 4)
        balance.press("tare")
        self.assertAlmostEqual(self.settled(balance, 0.0212345, 5)["net_kg"], 0.0012345)
        self.assertAlmostEqual(self.settled(balance, 0.0, 8)["net_kg"], -0.02)
        self.settled(balance, 0.1, 8)
        balance.calibrate_known_mass(0.1)
        self.assertTrue(balance.calibrated)
        self.assertFalse(balance.stable)
        self.assertEqual(balance.tare_kg, 0)
        self.assertTrue(self.settled(balance, 0.1, 3)["task_ready"])

    def test_invalid_core_inputs_preserve_state_and_zero_time_is_pure(self):
        balance = FilteredAnalyticalBalance()
        balance.set_power(True)
        balance.update(0.1, 0.01)
        invalid = [(True, 0.01, {}), (math.nan, 0.01, {}), (math.inf, 0.01, {}), (10 ** 400, 0.01, {}),
            (1, True, {}), (1, -0.1, {}), (1, 0.01, {"door_fraction": 2}),
            (1, 0.01, {"tilt_rad": math.nan}), (1, 0.01, {"vibration": 1})]
        for force, dt, kwargs in invalid:
            before = copy.deepcopy(balance.__dict__)
            with self.assertRaises(ValueError):
                balance.update(force, dt, **kwargs)
            self.assertEqual(before, balance.__dict__)
        before = copy.deepcopy(balance.__dict__)
        balance.update(1, 0)
        self.assertEqual(before, balance.__dict__)

    def test_model_difference_and_filter_restart(self):
        self.assertEqual(FilteredAnalyticalBalance.for_model("XPR204E").settling_s, 1.5)
        self.assertEqual(FilteredAnalyticalBalance.for_model("PX224E").settling_s, 3)
        balance = FilteredAnalyticalBalance(filter_level="Low")
        balance.set_power(True)
        self.settled(balance, 0.1, 3)
        balance.set_filter("High")
        self.assertFalse(balance.stable)
        self.assertIsNone(balance.filtered_force_N)
        self.settled(balance, 0.1, 19.9)
        self.assertFalse(balance.stable)
        self.settled(balance, 0.1, 0.11)
        self.assertTrue(balance.stable)

    def test_direct_core_calibration_rejects_nonfinite_gain_atomically(self):
        balance = FilteredAnalyticalBalance(filter_level="Low")
        balance.set_power(True)
        self.settled(balance, 0.1, 3)
        for mass, force in [(1e-320, 0.98), (0, 0.98), (True, 0.98), (math.nan, 0.98),
            (0.1, math.nan), (0.1, math.inf), (0.1, True), (0.1, 0), (1e-12, 1e308)]:
            before = copy.deepcopy(balance.__dict__)
            with self.assertRaises(ValueError):
                balance.calibrate_span(mass, force)
            self.assertEqual(balance.__dict__, before)
        before = copy.deepcopy(balance.__dict__)
        with self.assertRaises(ValueError):
            balance.calibrate_known_mass(1e-320)
        self.assertEqual(balance.__dict__, before)
        balance.calibrate_span(0.1, 0.98)
        self.assertAlmostEqual(balance.g_cal, 9.8)
        self.assertFalse(balance.stable)
        self.assertEqual(balance.elapsed_s, 0)
        self.assertFalse(balance._stability_samples)

    def test_abrupt_unload_and_overload_recovery_are_not_latched(self):
        for level in XPR204E_FILTERS:
            with self.subTest(level=level):
                balance = FilteredAnalyticalBalance(filter_level=level)
                balance.set_power(True)
                self.settled(balance, 0.1, 30)
                balance.press("tare")
                initial = balance.update(0, 0.01)
                self.assertEqual(initial["status"], "UNSTABLE")
                self.assertLess(initial["net_kg"], 0)
                for _ in range(6000):
                    result = balance.update(0, 0.01)
                    if result["stable"]:
                        break
                self.assertTrue(result["stable"])
                self.assertLess(abs(result["net_kg"] + 0.1), 6e-5)
                for _ in range(100):
                    overload = balance.update(0.3 * 9.80665, 0.01)
                self.assertEqual(overload["status"], "OVERLOAD")
                self.assertIsNone(overload["net_kg"])
                for _ in range(6000):
                    result = balance.update(0.1 * 9.80665, 0.01)
                    if result["stable"]:
                        break
                self.assertTrue(result["stable"])
                self.assertEqual(result["status"], "STABLE")
                self.assertLess(abs(result["net_kg"]), 6e-5)

    def test_empty_pan_noise_can_zero_and_negative_indications_are_not_clipped(self):
        for level in XPR204E_FILTERS:
            for sigma in (1e-6, 1e-4, 1e-3):
                with self.subTest(level=level, sigma=sigma):
                    balance = FilteredAnalyticalBalance(filter_level=level)
                    balance.set_power(True)
                    rng = random.Random(0)
                    for _ in range(6000):
                        result = balance.update(rng.gauss(0, sigma), 0.01)
                        if result["stable"]:
                            break
                    self.assertTrue(result["stable"])
                    self.assertEqual(balance.press("zero")["net_kg"], 0)
        balance = FilteredAnalyticalBalance(filter_level="Low")
        balance.set_power(True)
        inside_band = balance.update(-0.25 * balance.stability_band_kg * 9.80665, 0.01)
        self.assertEqual(inside_band["status"], "UNSTABLE")
        self.assertLess(inside_band["net_kg"], 0)
        below_band = balance.update(-2 * balance.stability_band_kg * 9.80665, 2)
        self.assertEqual(below_band["status"], "UNDERLOAD")
        self.assertFalse(below_band["stable"])


class XPR204ERuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = XPR204ERuntime(scene_adapter=lambda instance, updates: self.calls.append((instance, copy.deepcopy(updates))))
        self.host.create_instance("balance_a")
        self.host.create_instance("balance_b")
        self.host.register_principal("learner", visibility="agent", instance_ids=["balance_a"])
        self.host.register_principal("judge", visibility="evaluator")
        self.agent = self.host.session("learner")
        self.judge = self.host.session("judge")
        self.counter = 0

    def invoke(self, cap, **arguments):
        self.counter += 1
        return self.agent.invoke(instance_id="balance_a", capability_id=cap, arguments=arguments, request_id=f"req_{self.counter}")

    def ok(self, cap, **arguments):
        response = self.invoke(cap, **arguments)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, sid, instance="balance_a"):
        return self.judge.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def settle(self, mass=0.1, seconds=12):
        self.ok("power.set", enabled=True)
        self.host.step(seconds, pan_load_forces_N={"balance_a": mass * 9.80665})
        self.assertTrue(self.value("balance.stable"))

    def test_uniform_discovery_read_and_reading_are_pure(self):
        self.ok("power.set", enabled=True)
        self.assertIsNone(self.value("balance.net_kg"))
        self.assertIsNone(self.value("balance.gross_kg"))
        self.assertEqual(self.agent.read(instance_id="balance_a")["observations"]["balance.net_kg"]["quality"], "unavailable")
        self.settle()
        before, calls = self.host.debug_snapshot(), len(self.calls)
        for _ in range(5):
            self.agent.read(instance_id="balance_a")
            self.agent.describe(instance_id="balance_a")
        reading = self.ok("balance.reading")["result"]
        self.assertEqual(reading, {"status": "STABLE", "stable": True, "gross_kg": 0.1, "net_kg": 0.1, "synthetic": True})
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(calls, len(self.calls))

    def test_independent_instances_and_common_host_clock(self):
        self.settle(0.1)
        self.assertFalse(self.value("power.enabled", "balance_b"))
        self.assertEqual(self.value("balance.raw_force_n", "balance_b"), 0)
        self.assertAlmostEqual(self.value("balance.gross_kg"), 0.1)
        for instance in ("balance_a", "balance_b"):
            self.assertEqual(self.judge.read(instance_id=instance)["clock"]["time_s"], 12)
        self.assertEqual(self.agent.read(instance_id="balance_b")["error"]["code"], "UNKNOWN_INSTANCE")

    def test_reject_invalid_args_without_state_time_or_scene_effects(self):
        self.settle()
        cases = [("power.set", {"enabled": 1}), ("balance.set_filter", {"level": []}),
            ("balance.set_filter", {"level": "Ultra"}), ("balance.set_display_unit", {"unit": "mg"}),
            ("scene.set_door", {"side": "left", "opening_m": True}), ("scene.set_door", {"side": "rear", "opening_m": 0.1}),
            ("scene.set_door", {"side": "left", "opening_m": 0.110}),
            ("scene.set_pan", {"installed": 1}), ("balance.reading", {"role": "evaluator"})]
        cases += [("balance.calibrate_span", {"known_mass_kg": value}) for value in (0, -1, True, math.nan, math.inf, "0.1", 10 ** 400, 0.221)]
        for cap, args in cases:
            before, calls = self.host.debug_snapshot(), len(self.calls)
            response = self.invoke(cap, **args)
            self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT", response)
            self.assertEqual(response["error"]["side_effects"], "none")
            self.assertEqual(before, self.host.debug_snapshot())
            self.assertEqual(calls, len(self.calls))

    def test_host_input_validation_is_atomic_and_zero_step_is_pure(self):
        self.settle()
        for dt, forces in [(True, None), (-1, None), (61, None), (math.inf, None), (math.nan, None),
            (0.01, {"unknown": 0}), (0.01, {"balance_a": math.nan}), (0.01, {"balance_a": True}),
            (0.01, {"balance_a": 10 ** 400}), (0.01, [1])]:
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.step(dt, pan_load_forces_N=forces)
            self.assertEqual(before, self.host.debug_snapshot())
        before = self.host.debug_snapshot()
        self.host.step(0, pan_load_forces_N={"balance_a": 100})
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.invoke("sim.step", dt_s=10)["error"]["code"], "UNKNOWN_CAPABILITY")

    def test_extreme_finite_force_saturates_status_without_numeric_failure(self):
        self.ok("power.set", enabled=True)
        self.host.step(0.01, pan_load_forces_N={"balance_a": 1e308})
        self.assertEqual(self.value("balance.status"), "OVERLOAD")
        self.assertIsNone(self.value("balance.net_kg"))
        self.host.step(0.01, pan_load_forces_N={"balance_a": -1e308})
        self.assertEqual(self.value("balance.status"), "OVERLOAD")
        self.assertTrue(math.isfinite(self.value("balance.filtered_force_n")))

    def test_agent_truth_filtered_from_describe_read_results_errors_and_logs(self):
        self.settle(0.123456789)
        hidden = ("balance.raw_force_n", "balance.filtered_force_n", "balance.gain_n_kg", "balance.zero_kg")
        for session in (self.agent, self.host):
            for sid in hidden:
                self.assertNotIn(sid, json.dumps(session.describe(instance_id="balance_a")))
                self.assertNotIn(sid, json.dumps(session.read(instance_id="balance_a")))
                denied = session.read(instance_id="balance_a", state_ids=[sid])
                unknown = session.read(instance_id="balance_a", state_ids=["unknown"])
                self.assertEqual(denied["error"], unknown["error"])
                self.assertNotIn(sid, json.dumps(session.invoke(instance_id="balance_a", capability_id="balance.reading", arguments={}, request_id="read")))
        for sid in hidden:
            self.assertNotIn(sid, json.dumps(self.agent.logs()))
            self.assertIn(sid, self.judge.read(instance_id="balance_a")["observations"])
        reading = self.agent.read(instance_id="balance_a")["observations"]["balance.net_kg"]
        self.assertEqual(reading["quality"], "estimated")
        self.assertTrue(reading["synthetic"])
        self.assertNotEqual(reading["value"], 0.123456789)
        with self.assertRaises(TypeError):
            self.agent.read(instance_id="balance_a", role="evaluator")
        with self.assertRaises(ValueError):
            self.host.register_principal("learner", visibility="evaluator")

    def test_capability_write_routes_to_named_side_and_fixed_parameters(self):
        response = self.agent.write(instance_id="balance_a", state_id="door.right.opening_m", value=0.04, request_id="write")
        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(self.value("door.right.opening_m"), 0.04)
        self.assertEqual(self.value("door.left.opening_m"), 0)
        self.assertEqual(self.calls[-1][1]["door.right.opening_m"], 0.04)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.write(instance_id="balance_a", state_id="balance.raw_force_n", value=5, request_id="denied")["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_door_scene_callback_and_measurement_gate_share_authority(self):
        self.settle()
        self.ok("scene.set_door", side="left", opening_m=0.109)
        self.assertEqual(self.calls[-1][1]["door.left.opening_m"], 0.109)
        self.assertFalse(self.value("balance.stable"))
        self.host.step(10)
        self.assertEqual(self.value("balance.status"), "UNSTABLE")
        self.assertEqual(self.value("balance.gross_kg"), 0.1)
        self.ok("scene.set_door", side="left", opening_m=0)
        self.host.step(1.5)
        self.assertFalse(self.value("balance.stable"))
        self.host.step(0.02)
        self.assertTrue(self.value("balance.stable"))

    def test_missing_scene_and_failing_callback_are_not_false_success(self):
        self.host.scene_adapter = None
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("scene.set_door", side="left", opening_m=0.03)["status"], "unsupported")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.describe(instance_id="balance_a")["capabilities"]["scene.set_door"]["availability"], "unsupported")
        def broken(*_):
            raise RuntimeError("sensitive internal force 123.456")
        self.host.scene_adapter = broken
        response = self.invoke("scene.set_door", side="left", opening_m=0.03)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("sensitive", json.dumps(response))
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(RuntimeError):
            self.host.step(0.1)
        self.assertEqual(self.host.time_s, 0.1)

    def test_request_dedup_conflict_isolation_and_return_copy(self):
        args = {"instance_id": "balance_a", "capability_id": "power.set", "arguments": {"enabled": True}, "request_id": "dedup"}
        response = self.agent.invoke(**args)
        self.host.step(0.2)
        before, calls = self.host.debug_snapshot(), len(self.calls)
        self.assertEqual(response, self.agent.invoke(**args))
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(calls, len(self.calls))
        self.assertEqual(self.agent.invoke(**{**args, "arguments": {"enabled": False}})["error"]["code"], "REQUEST_CONFLICT")
        response["result"]["enabled"] = False
        self.assertTrue(self.agent.invoke(**args)["result"]["enabled"])

    def test_tare_zero_calibration_unit_and_removal_preconditions(self):
        before = self.host.debug_snapshot()
        for cap in ("balance.tare", "balance.zero"):
            self.assertEqual(self.invoke(cap)["error"]["code"], "PRECONDITION_FAILED")
            self.assertEqual(before, self.host.debug_snapshot())
        self.ok("scene.set_pan", installed=False)
        self.ok("scene.set_pan", installed=True)
        self.settle(0.1)
        self.assertEqual(self.invoke("scene.set_pan", installed=False)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.invoke("balance.zero")["error"]["code"], "PRECONDITION_FAILED")
        self.ok("balance.tare")
        self.assertEqual(self.value("balance.net_kg"), 0)
        self.ok("balance.set_display_unit", unit="kg")
        self.assertEqual(self.value("balance.gross_kg"), 0.1)
        self.ok("balance.set_display_unit", unit="g")
        self.assertEqual(self.value("balance.gross_kg"), 0.1)
        self.ok("balance.calibrate_span", known_mass_kg=0.1)
        self.assertTrue(self.value("balance.calibrated"))
        self.assertFalse(self.value("balance.stable"))
        self.host.step(2)
        self.assertTrue(self.value("balance.task_ready"))
        self.host.step(30, pan_load_forces_N={"balance_a": 0.001 * 9.80665})
        self.ok("balance.zero")
        self.assertEqual(self.value("balance.net_kg"), 0)
        self.ok("power.set", enabled=False)
        self.assertEqual(self.value("balance.status"), "OFF")
        self.assertIsNone(self.value("balance.net_kg"))

    def test_host_environment_reset_and_state_copy(self):
        self.settle()
        self.host.set_environment("balance_a", tilt_rad=0.02)
        self.assertFalse(self.value("balance.stable"))
        self.host.step(3)
        self.assertFalse(self.value("balance.stable"))
        self.host.set_environment("balance_a", vibration=True)
        self.host.step(3)
        self.assertFalse(self.value("balance.stable"))
        response = self.agent.read(instance_id="balance_a")
        response["observations"]["power.enabled"]["value"] = False
        self.assertTrue(self.value("power.enabled"))

    def test_metadata_input_examples_and_actual_output_schemas(self):
        metadata = export_xpr204e_contract_metadata()["asset_types"][XPR204E_TYPE]
        for name, cap in metadata["capabilities"].items():
            for schema_name in ("input_schema", "output_schema"):
                errors = []
                lint_schema(cap[schema_name], f"{name}.{schema_name}", errors)
                self.assertEqual(errors, [])
            for example in cap["example_arguments"]:
                errors = []
                validate_value(example, cap["input_schema"], name, errors)
                self.assertEqual(errors, [])
        for cap, args in [("power.set", {"enabled": True}), ("balance.set_filter", {"level": "Low"}),
            ("balance.set_display_unit", {"unit": "kg"}), ("balance.reading", {}),
            ("scene.set_door", {"side": "top", "opening_m": 0.01})]:
            response = self.ok(cap, **args)
            errors = []
            validate_value(response["result"], metadata["capabilities"][cap]["output_schema"], cap, errors)
            self.assertEqual(errors, [])

    def test_every_capability_output_and_complete_immediate_state_effects(self):
        metadata = export_xpr204e_contract_metadata()["asset_types"][XPR204E_TYPE]
        checked = set()
        def verify(cap, **args):
            before = self.host.debug_snapshot()["instances"]["balance_a"]["state"]
            response = self.ok(cap, **args)
            after = self.host.debug_snapshot()["instances"]["balance_a"]["state"]
            changed = {sid for sid in after if after[sid] != before[sid]}
            declared = {effect["state_id"] for effect in metadata["capabilities"][cap]["state_effects"]}
            self.assertLessEqual(changed, declared, (cap, changed - declared))
            errors = []
            validate_value(response["result"], metadata["capabilities"][cap]["output_schema"], cap, errors)
            self.assertEqual(errors, [])
            checked.add(cap)
        verify("scene.set_pan", installed=False)
        verify("scene.set_pan", installed=True)
        verify("power.set", enabled=True)
        verify("balance.set_filter", level="Low")
        verify("balance.set_display_unit", unit="kg")
        self.host.step(5, pan_load_forces_N={"balance_a": 0.1 * 9.80665})
        verify("balance.reading")
        verify("balance.tare")
        verify("balance.calibrate_span", known_mass_kg=0.1)
        self.host.step(6, pan_load_forces_N={"balance_a": 0.001 * 9.80665})
        verify("balance.zero")
        verify("scene.set_door", side="left", opening_m=0.05)
        verify("power.set", enabled=False)
        self.assertEqual(checked, set(metadata["capabilities"]))

    def test_scene_callback_is_a_fixed_typed_public_snapshot(self):
        metadata = export_xpr204e_contract_metadata()
        self.settle()
        self.ok("scene.set_door", side="top", opening_m=0.02)
        self.ok("balance.set_filter", level="Low")
        for _, values in self.calls:
            self.assertEqual(set(values), set(metadata["scene_snapshot_keys"]))
            errors = []
            validate_value(values, metadata["scene_snapshot_schema"], "scene", errors)
            self.assertEqual(errors, [])
            self.assertFalse(any(sid in values for sid in ("balance.raw_force_n", "balance.filtered_force_n", "balance.gain_n_kg", "balance.zero_kg")))
        self.calls[-1][1]["power.enabled"] = False
        self.assertTrue(self.value("power.enabled"))

    def test_calibration_floor_rejection_has_no_process_or_scene_side_effect(self):
        self.settle()
        before, calls = self.host.debug_snapshot(), len(self.calls)
        response = self.invoke("balance.calibrate_span", known_mass_kg=1e-320)
        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(response["error"]["side_effects"], "none")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(calls, len(self.calls))



# CHEM-103 append-only tests: no Blender, no physical sample or hardware claims.
from basic40_runtime_measure import (GamryReference620Runtime, GAMRY_REFERENCE620_TYPE,
    GAMRY_REFERENCE620_PORTS, GAMRY_REFERENCE620_MIN_INTERVAL_S,
    GAMRY_REFERENCE620_MAX_SAMPLES, export_gamry_reference620_contract_metadata)


class GamryReference620RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.host = GamryReference620Runtime()
        self.host.create_instance("gamry_a")
        self.host.create_instance("gamry_b")
        self.host.register_principal("learner", visibility="agent", instance_ids=["gamry_a"])
        self.host.register_principal("judge", visibility="evaluator")
        self.agent = self.host.session("learner")
        self.judge = self.host.session("judge")
        self.counter = 0

    def invoke(self, cap, arguments=None):
        self.counter += 1
        return self.agent.invoke(instance_id="gamry_a", capability_id=cap,
            arguments={} if arguments is None else arguments, request_id="gamry_request_" + str(self.counter))

    def ok(self, cap, arguments=None):
        result = self.invoke(cap, arguments)
        self.assertEqual(result["status"], "succeeded", result)
        return result

    def value(self, sid, instance_id="gamry_a"):
        return self.judge.read(instance_id=instance_id, state_ids=[sid])["observations"][sid]["value"]

    def ready(self, mode="potential", values=None, interval=0.1):
        self.ok("power.set", {"enabled": True})
        for port in GAMRY_REFERENCE620_PORTS:
            self.ok("slot.set_connection", {"port_id": port, "connection_id": "fixture_" + port})
        program = {"mode": mode, "sample_interval_s": interval, "values": values if values is not None else [-1.0, 0.0, 1.0]}
        self.ok("electrochem.configure_waveform", {"program": program})
        return program

    def test_catalog_limits_and_unknown_values(self):
        meta = export_gamry_reference620_contract_metadata()["asset_types"][GAMRY_REFERENCE620_TYPE]
        self.assertEqual(meta["catalog_id"], "CHEM-103")
        for name, expected in (("potential_limit_v", 11), ("current_limit_a", 0.6),
                               ("minimum_time_base_s", 3.333e-6), ("eis_frequency_range_hz", [1e-5, 5e6])):
            self.assertEqual(meta["parameters"][name]["value"], expected)
        self.assertEqual(meta["parameters"]["maximum_program_samples"]["evidence"], "engineering_assumption")
        unknown = ["measurement.potential_v", "measurement.current_a", "method.scan_rate_v_s",
                   "method.frequency_hz", "method.amplitude_v", "method.ir_compensation_ohm"]
        result = self.agent.read(instance_id="gamry_a", state_ids=unknown)
        for obs in result["observations"].values():
            self.assertIsNone(obs["value"])
            self.assertEqual(obs["quality"], "unavailable")
            self.assertTrue(obs["reason"])

    def test_each_required_port_is_individually_enforced(self):
        self.ready()
        for port in GAMRY_REFERENCE620_PORTS:
            self.ok("slot.set_connection", {"port_id": port, "connection_id": None})
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke("electrochem.start")["error"]["code"], "PRECONDITION_FAILED")
            self.assertEqual(before, self.host.debug_snapshot())
            self.ok("slot.set_connection", {"port_id": port, "connection_id": "fixture_" + port})
        self.ok("electrochem.start")
        self.assertTrue(self.value("waveform.running"))

    def test_start_read_describe_and_query_do_not_advance_or_sample(self):
        self.ready()
        self.ok("electrochem.start")
        before = self.host.debug_snapshot()
        self.assertEqual(self.value("acquisition.series"), [])
        for _ in range(4):
            self.agent.describe(asset_type_id=GAMRY_REFERENCE620_TYPE)
            self.agent.describe(instance_id="gamry_a")
            self.agent.read(instance_id="gamry_a")
            self.assertEqual(self.ok("electrochem.read_series")["result"]["samples"], [])
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_potential_sequence_timestamps_and_completion(self):
        self.ready(values=[-11.0, 0.0, 11.0])
        self.ok("electrochem.start")
        self.host.step(0.05)
        self.assertEqual(self.value("output.commanded_potential_v"), -11.0)
        self.assertIsNone(self.value("output.commanded_current_a"))
        self.assertEqual(self.value("waveform.sample_count"), 1)
        self.host.step(0.15)
        result = self.ok("electrochem.read_series")["result"]
        self.assertEqual([s["time_s"] for s in result["samples"]], [0.0, 0.1, 0.2])
        self.assertEqual([s["commanded_potential_v"] for s in result["samples"]], [-11.0, 0.0, 11.0])
        for sample in result["samples"]:
            self.assertTrue(sample["synthetic"])
            self.assertIsNone(sample["commanded_current_a"])
            self.assertIsNone(sample["measured_current_a"])
            self.assertIsNone(sample["measured_potential_v"])
        self.assertIsNone(result["response_model"])
        self.assertEqual(result["status"], "completed")
        self.assertFalse(self.value("waveform.running"))
        self.assertIsNone(self.value("output.commanded_potential_v"))
        self.assertEqual(self.value("waveform.elapsed_s"), 0.2)
        series = self.agent.read(instance_id="gamry_a", state_ids=["acquisition.series"])["observations"]["acquisition.series"]
        self.assertEqual(series["quality"], "estimated")
        self.assertEqual(series["sample_window_s"], [0.0, 0.2])

    def test_current_boundary_and_minimum_interval(self):
        interval = GAMRY_REFERENCE620_MIN_INTERVAL_S
        self.ready(mode="current", values=[-0.6, 0, 0.6], interval=interval)
        self.ok("electrochem.start")
        self.host.step(interval * 3)
        samples = self.value("acquisition.series")
        self.assertEqual([s["commanded_current_a"] for s in samples], [-0.6, 0, 0.6])
        self.assertTrue(all(s["commanded_potential_v"] is None for s in samples))
        self.assertEqual([s["elapsed_s"] for s in samples], [0, interval, 2 * interval])

    def test_host_chunking_preserves_sampling_schedule(self):
        self.ready(values=list(range(11)), interval=0.01)
        self.ok("electrochem.start")
        self.host.step(0.05)
        self.host.step(0.05)
        expected = self.value("acquisition.series")
        host = GamryReference620Runtime()
        host.create_instance("other")
        n = 0
        for cap, args in [("power.set", {"enabled": True})] + [
                ("slot.set_connection", {"port_id": p, "connection_id": p}) for p in GAMRY_REFERENCE620_PORTS] + [
                ("electrochem.configure_waveform", {"program": {"mode": "potential", "sample_interval_s": 0.01, "values": list(range(11))}}),
                ("electrochem.start", {})]:
            n += 1
            self.assertEqual(host.invoke(instance_id="other", capability_id=cap, arguments=args, request_id=str(n))["status"], "succeeded")
        host.step(0.1)
        self.assertEqual(host.read(instance_id="other", state_ids=["acquisition.series"])["observations"]["acquisition.series"]["value"], expected)

    def test_invalid_inputs_rejected_atomically(self):
        self.ready()
        program = {"mode": "potential", "sample_interval_s": 0.1, "values": [0, 1]}
        cases = [("power.set", {"enabled": 1}), ("power.set", {"enabled": True, "role": "evaluator"}),
            ("slot.set_connection", {"port_id": [], "connection_id": "a"}),
            ("slot.set_connection", {"port_id": "working_electrode", "connection_id": "  "}),
            ("slot.set_connection", {"port_id": "unknown", "connection_id": "a"}),
            ("slot.set_connection", {"port_id": "cell", "connection_id": True}),
            ("electrochem.configure_waveform", {"program": None}),
            ("electrochem.configure_waveform", {"program": {**program, "mode": []}}),
            ("electrochem.configure_waveform", {"program": {**program, "values": []}}),
            ("electrochem.configure_waveform", {"program": {**program, "values": [0] * (GAMRY_REFERENCE620_MAX_SAMPLES + 1)}}),
            ("electrochem.configure_waveform", {"program": {**program, "values": [11.00001]}}),
            ("electrochem.configure_waveform", {"program": {**program, "mode": "current", "values": [0.60001]}}),
            ("electrochem.configure_waveform", {"program": {**program, "sample_interval_s": 1e308, "values": [0, 0, 0]}})]
        for value in (True, -1, 0, math.nan, math.inf, "0.1", 10 ** 400, 3.332e-6):
            cases.append(("electrochem.configure_waveform", {"program": {**program, "sample_interval_s": value}}))
        for value in (True, math.nan, math.inf, "0.1", 10 ** 400):
            cases.append(("electrochem.configure_waveform", {"program": {**program, "values": [value]}}))
        for cap, args in cases:
            before = self.host.debug_snapshot()
            response = self.invoke(cap, args)
            self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT", response)
            self.assertEqual(response["error"]["side_effects"], "none")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_host_invalid_steps_are_atomic_and_hidden_from_agent(self):
        self.ready()
        self.ok("electrochem.start")
        for dt in (True, -1, math.inf, math.nan, "1", 10 ** 400):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.step(dt)
            self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.invoke("sim.step", {"dt_s": 1})["error"]["code"], "UNKNOWN_CAPABILITY")
        self.assertEqual(self.host.describe_host()["sim.step"]["visibility"], "trusted_host_only")

    def test_busy_configuration_and_stop_preserve_partial_sequence(self):
        self.ready()
        self.ok("electrochem.start")
        self.host.step(0.05)
        for cap, args in (("slot.set_connection", {"port_id": "cell", "connection_id": None}),
                          ("electrochem.configure_waveform", {"program": {"mode": "current", "sample_interval_s": 1, "values": [0]}}),
                          ("electrochem.start", {})):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, args)["error"]["code"], "BUSY")
            self.assertEqual(before, self.host.debug_snapshot())
        samples = self.value("acquisition.series")
        self.ok("electrochem.stop")
        self.assertEqual(self.value("waveform.status"), "stopped")
        self.assertIsNone(self.value("output.commanded_potential_v"))
        self.host.step(1)
        self.assertEqual(self.value("acquisition.series"), samples)
        self.assertEqual(self.value("waveform.elapsed_s"), 0.05)
        self.ok("electrochem.start")
        self.assertEqual(self.value("acquisition.series"), [])
        self.host.step(0.01)
        self.assertEqual(self.value("acquisition.series")[0]["time_s"], 1.05)

    def test_power_off_stops_keeps_sequence_and_gates_restart(self):
        self.ready()
        self.ok("electrochem.start")
        self.host.step(0.05)
        samples = self.value("acquisition.series")
        self.ok("power.set", {"enabled": False})
        self.assertEqual(self.value("waveform.status"), "stopped")
        self.assertFalse(self.value("waveform.running"))
        self.assertIsNone(self.value("output.commanded_potential_v"))
        self.host.step(1)
        self.assertEqual(self.value("acquisition.series"), samples)
        self.assertEqual(self.invoke("electrochem.start")["error"]["code"], "PRECONDITION_FAILED")

    def test_truth_and_instance_access_filtering(self):
        self.ready()
        self.ok("electrochem.start")
        hidden = "scheduler.started_at_s"
        for session in (self.agent, self.host):
            self.assertNotIn(hidden, json.dumps(session.describe(instance_id="gamry_a")))
            self.assertNotIn(hidden, json.dumps(session.read(instance_id="gamry_a")))
            denied = session.read(instance_id="gamry_a", state_ids=[hidden])
            unknown = session.read(instance_id="gamry_a", state_ids=["secret.unknown"])
            self.assertEqual(denied["error"], unknown["error"])
            self.assertNotIn(hidden, json.dumps(denied))
        self.assertNotIn(hidden, json.dumps(self.agent.logs()))
        self.assertIn(hidden, self.judge.read(instance_id="gamry_a")["observations"])
        for response in (self.agent.read(instance_id="gamry_b"), self.agent.describe(instance_id="gamry_b"),
                         self.agent.invoke(instance_id="gamry_b", capability_id="power.set", arguments={"enabled": True}, request_id="denied")):
            self.assertEqual(response["error"]["code"], "UNKNOWN_INSTANCE")
        with self.assertRaises(TypeError):
            self.agent.read(instance_id="gamry_a", role="evaluator")
        with self.assertRaises(ValueError):
            self.host.register_principal("learner", visibility="evaluator")

    def test_two_instances_share_only_host_clock(self):
        self.ready()
        self.ok("electrochem.start")
        b_before = self.host.debug_snapshot()["instances"]["gamry_b"]
        self.host.step(0.1)
        self.assertEqual(b_before, self.host.debug_snapshot()["instances"]["gamry_b"])
        self.assertFalse(self.value("power.enabled", "gamry_b"))
        self.assertEqual(self.value("acquisition.series", "gamry_b"), [])
        for instance in ("gamry_a", "gamry_b"):
            self.assertEqual(self.judge.read(instance_id=instance)["clock"]["time_s"], 0.1)

    def test_write_routes_all_writable_slots_and_readonly_denied(self):
        for port in GAMRY_REFERENCE620_PORTS:
            response = self.agent.write(instance_id="gamry_a", state_id="slot." + port + ".connection_id", value=port, request_id="write_" + port)
            self.assertEqual(response["status"], "succeeded")
            self.assertEqual(self.value("slot." + port + ".connection_id"), port)
        program = {"mode": "current", "sample_interval_s": 0.1, "values": [0.1]}
        for sid, value in (("power.enabled", True), ("waveform.program", program)):
            self.assertEqual(self.agent.write(instance_id="gamry_a", state_id=sid, value=value, request_id="write_" + sid)["status"], "succeeded")
        for sid in ("waveform.running", "measurement.current_a", "scheduler.started_at_s", "nonexistent"):
            before = self.host.debug_snapshot()
            response = self.agent.write(instance_id="gamry_a", state_id=sid, value=42, request_id="write_" + sid)
            self.assertEqual(response["error"]["code"], "ACCESS_DENIED")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_retries_conflicts_and_return_values_are_isolated(self):
        program = self.ready()
        program["values"][0] = 11
        self.assertEqual(self.value("waveform.program")["values"][0], -1)
        args = dict(instance_id="gamry_a", capability_id="electrochem.start", arguments={}, request_id="repeat")
        response = self.agent.invoke(**args)
        self.host.step(0.05)
        before = self.host.debug_snapshot()
        self.assertEqual(response, self.agent.invoke(**args))
        self.assertEqual(before, self.host.debug_snapshot())
        response["result"]["status"] = "changed"
        self.assertEqual(self.agent.invoke(**args)["result"]["status"], "running")
        conflict = self.agent.invoke(**{**args, "capability_id": "electrochem.stop"})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        series = self.ok("electrochem.read_series")["result"]["samples"]
        series[0]["commanded_potential_v"] = 999
        self.assertEqual(self.value("acquisition.series")[0]["commanded_potential_v"], -1)

    def test_unsupported_eis_rejects_without_state_or_clock_changes(self):
        self.ready()
        before = self.host.debug_snapshot()
        result = self.invoke("electrochem.eis")
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["error"]["code"], "UNSUPPORTED")
        self.assertEqual(result["error"]["side_effects"], "none")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.describe(instance_id="gamry_a")["capabilities"]["electrochem.eis"]["availability"], "unsupported")

    def test_clock_precision_and_extreme_duration_fail_atomically(self):
        self.ready()
        self.host.step(1e20)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("electrochem.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(ValueError):
            self.host.step(0.001)
        self.assertEqual(before, self.host.debug_snapshot())
        other = GamryReference620Runtime()
        other.step(1e308)
        before = other.debug_snapshot()
        with self.assertRaises(ValueError):
            other.step(1e308)
        self.assertEqual(before, other.debug_snapshot())

    def test_metadata_schemas_examples_actual_outputs_and_state_effects(self):
        metadata = export_gamry_reference620_contract_metadata()["asset_types"][GAMRY_REFERENCE620_TYPE]
        for cid, cap in metadata["capabilities"].items():
            for field in ("input_schema", "output_schema"):
                errors = []
                lint_schema(cap[field], cid + "." + field, errors)
                self.assertEqual(errors, [])
            for example in cap["example_arguments"]:
                errors = []
                validate_value(example, cap["input_schema"], cid, errors)
                self.assertEqual(errors, [])
        checked = set()
        def verify(cid, arguments=None):
            before = self.host.debug_snapshot()["instances"]["gamry_a"]["state"]
            response = self.ok(cid, arguments)
            after = self.host.debug_snapshot()["instances"]["gamry_a"]["state"]
            changed = {sid for sid in after if before[sid] != after[sid]}
            declared = {effect["state_id"] for effect in metadata["capabilities"][cid]["state_effects"]}
            self.assertLessEqual(changed, declared, (cid, changed - declared))
            errors = []
            validate_value(response["result"], metadata["capabilities"][cid]["output_schema"], cid, errors)
            self.assertEqual(errors, [])
            checked.add(cid)
        verify("power.set", {"enabled": True})
        for port in GAMRY_REFERENCE620_PORTS:
            verify("slot.set_connection", {"port_id": port, "connection_id": port})
        verify("electrochem.configure_waveform", {"program": {"mode": "potential", "sample_interval_s": 0.1, "values": [-11, 11]}})
        verify("electrochem.start")
        self.host.step(0.05)
        verify("electrochem.read_series")
        verify("electrochem.stop")
        verify("power.set", {"enabled": False})
        self.assertEqual(checked, set(metadata["capabilities"]) - {"electrochem.eis"})
        for sid, value in self.host.debug_snapshot()["instances"]["gamry_a"]["state"].items():
            errors = []
            validate_value(value, metadata["state_slots"][sid]["value_schema"], sid, errors)
            self.assertEqual(errors, [])



# BEGIN CHEM-005 METROHM 914 APPEND-ONLY TESTS
import copy as _m914_test_copy
import json as _m914_test_json
import math as _m914_test_math
import unittest as _m914_unittest
from basic40_runtime_measure import Metrohm914Runtime, METROHM914_TYPE, export_metrohm914_contract_metadata


class Metrohm914RuntimeTests(_m914_unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = Metrohm914Runtime(scene_adapter=lambda n, updates: self.calls.append((n, _m914_test_copy.deepcopy(updates))))
        self.host.create_instance("m914_a")
        self.host.create_instance("m914_b")
        self.host.register_principal("m914_learner", visibility="agent", instance_ids=["m914_a"])
        self.host.register_principal("m914_judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("m914_learner"), self.host.session("m914_judge")
        self.counter = 0

    def invoke(self, cap, **args):
        self.counter += 1
        return self.agent.invoke(instance_id="m914_a", capability_id=cap, arguments=args, request_id=f"m914_{self.counter}")

    def ok(self, cap, **args):
        response = self.invoke(cap, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, sid, instance="m914_a"):
        return self.judge.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    def prepare(self, name="ph", hold=2, tolerance=0):
        self.ok("probe.set_installed", slot=name, installed=True)
        self.ok("electrochemistry.select_probe", slot=name)
        self.ok("electrochemistry.configure_endpoint", hold_s=hold, tolerance_fraction=tolerance)
        self.ok("power.set", enabled=True)

    def sample(self, name="ph", value=7.0, **extra):
        sample = {"ph": None, "conductance_s": None, "do_kg_m3": None, "temperature_k": 298.15,
                  "reference_ph": 7.0, "reference_potential_v": 0.0}
        sample[{"ph": "ph", "conductance": "conductance_s", "do": "do_kg_m3"}[name]] = value
        sample.update(extra)
        return sample

    def step(self, sample, interval=1.0):
        self.host.host_step(self.host.time_s + interval, inputs={"m914_a": sample})

    def settle(self, sample):
        for _ in range(3):
            self.step(sample)
        self.assertTrue(self.value("endpoint.stable"))

    def test_initial_unknowns_and_discovery_are_truth_filtered(self):
        self.assertFalse(self.value("probe.ph.installed"))
        self.assertIsNone(self.value("endpoint.hold_s"))
        self.assertIsNone(self.value("measurement.ph"))
        self.assertEqual(self.value("acquisition.interval_s"), 1)
        d = self.agent.describe(instance_id="m914_a")
        self.assertEqual(d["asset_type_id"], METROHM914_TYPE)
        self.assertFalse(any(n.startswith("truth.") for n in d["state_slots"]))
        self.assertNotIn("sim.host_step", d["capabilities"])
        self.assertEqual(self.agent.describe(instance_id="m914_b")["error"]["code"], "UNKNOWN_INSTANCE")
        for p in ("cell_constant_m_inv", "probe_mass_kg", "contamination_coefficient", "drift_rate"):
            self.assertIsNone(d["parameters"][p]["value"])

    def test_ph_nernst_sign_resolution_and_manual_temperature(self):
        self.prepare()
        sample = self.sample(value=4.12346)
        self.step(sample)
        self.assertEqual(self.value("measurement.ph"), 4.123)
        factor = _m914_test_math.log(10) * 8.31446261815324 * 298.15 / 96485.33212331001
        self.assertAlmostEqual(self.value("measurement.potential_v"), factor * (7 - 4.12346), places=14)
        self.assertIsNone(self.value("measurement.conductance_s"))
        self.ok("electrochemistry.configure_temperature", mode="manual", manual_temperature_k=310.0)
        self.step(sample)
        expected = round(7 + 298.15 / 310 * (4.12346 - 7), 3)
        self.assertEqual(self.value("measurement.ph"), expected)
        self.assertEqual(self.value("temperature.compensation_k"), 310.0)

    def test_all_hard_range_boundaries_and_no_accuracy_claim(self):
        for name, lo, hi in (("ph", -13, 20), ("conductance", 1e-7, 0.5), ("do", 0, 0.05)):
            with self.subTest(slot=name):
                host = Metrohm914Runtime()
                host.create_instance("fixture")
                for index, (cap, arguments) in enumerate((("probe.set_installed", {"slot": name, "installed": True}), ("electrochemistry.select_probe", {"slot": name}), ("power.set", {"enabled": True}))):
                    self.assertEqual(host.invoke(instance_id="fixture", capability_id=cap, arguments=arguments, request_id=f"setup{index}")["status"], "succeeded")
                for time, value in enumerate((lo, hi, hi + 1, lo - 1), 1):
                    host.host_step(time, inputs={"fixture": self.sample(name, value)})
                    response = host.invoke(instance_id="fixture", capability_id="electrochemistry.reading", arguments={}, request_id=f"read{time}")["result"]
                    self.assertEqual(response["status"], "UNSTABLE" if time < 3 else "OUT_OF_RANGE")
                    self.assertTrue(response["synthetic"])
        metadata = export_metrohm914_contract_metadata()["asset_types"][METROHM914_TYPE]
        self.assertIn("未用作仿真精度", metadata["parameters"]["ph_instrument_accuracy"]["source"])
        self.assertEqual(metadata["state_slots"]["measurement.conductance_s"]["unit"], "S")
        self.assertEqual(metadata["state_slots"]["measurement.do_kg_m3"]["unit"], "kg/m3")

    def test_read_and_query_do_not_advance_host_time_or_scene(self):
        self.prepare()
        self.settle(self.sample())
        before, calls = self.host.debug_snapshot(), len(self.calls)
        for _ in range(3):
            self.agent.describe(instance_id="m914_a")
            self.agent.read(instance_id="m914_a")
            self.ok("electrochemistry.reading")
            self.ok("acquisition.records")
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertEqual(len(self.calls), calls)

    def test_shared_clock_two_instance_and_complete_sample_semantics(self):
        self.prepare()
        self.step(self.sample(value=6))
        self.assertEqual(self.value("measurement.ph"), 6)
        self.assertIsNone(self.value("truth.ph", "m914_b"))
        for instance in ("m914_a", "m914_b"):
            self.assertEqual(self.judge.read(instance_id=instance)["clock"]["time_s"], 1)
        self.step({"ph": 5})
        self.assertIsNone(self.value("truth.reference_ph"))
        self.assertIsNone(self.value("measurement.ph"))
        self.assertEqual(self.value("measurement.status"), "MISSING_CONFIGURATION")

    def test_stable_window_rejects_drift_and_stale_samples(self):
        self.prepare(hold=2, tolerance=0.001)
        self.step(self.sample(value=7))
        self.step(self.sample(value=7.03))
        self.step(self.sample(value=7.06))
        self.assertFalse(self.value("endpoint.stable"))
        self.assertEqual(self.value("endpoint.elapsed_s"), 0)
        self.settle(self.sample(value=7.06))
        self.host.host_step(self.host.time_s + 1)
        self.assertFalse(self.value("endpoint.stable"))
        self.assertEqual(self.value("measurement.status"), "STALE")
        obs = self.agent.read(instance_id="m914_a", state_ids=["measurement.ph"])["observations"]["measurement.ph"]
        self.assertEqual(obs["quality"], "stale")
        self.assertLess(obs["sample_time_s"], self.host.time_s)
        self.step(self.sample(value=7.06))
        self.assertFalse(self.value("endpoint.stable"))

    def test_ph_do_installation_mutex_and_independent_calibrations(self):
        self.prepare()
        for args in ({"slot": "do", "installed": True}, {"slot": "ph", "installed": False}):
            self.assertEqual(self.invoke("probe.set_installed", **args)["error"]["code"], "PRECONDITION_FAILED")
        self.settle(self.sample(value=6.9, standard_slot="ph", standard_value=7))
        self.ok("electrochemistry.calibrate", slot="ph", standard_value=7)
        self.assertTrue(self.value("probe.ph.calibrated"))
        self.assertFalse(self.value("endpoint.stable"))
        self.step(self.sample(value=6.9))
        self.assertEqual(self.value("measurement.ph"), 7)
        self.ok("power.set", enabled=False)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("probe.set_installed", slot="do", installed=True)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("probe.set_installed", slot="ph", installed=False)
        self.assertFalse(self.value("probe.ph.calibrated"))
        self.ok("probe.set_installed", slot="do", installed=True)
        self.ok("probe.set_installed", slot="conductance", installed=True)
        self.ok("electrochemistry.select_probe", slot="do")
        self.ok("power.set", enabled=True)
        self.settle(self.sample("do", 0.008, standard_slot="do", standard_value=0.009))
        self.ok("electrochemistry.calibrate", slot="do", standard_value=0.009)
        self.ok("electrochemistry.select_probe", slot="conductance")
        self.settle(self.sample("conductance", 0.02, standard_slot="conductance", standard_value=0.03))
        self.ok("electrochemistry.calibrate", slot="conductance", standard_value=0.03)
        self.step(self.sample("conductance", 0.02))
        self.assertAlmostEqual(self.value("measurement.conductance_s"), 0.03)
        self.assertTrue(self.value("probe.do.calibrated"))
        self.assertTrue(self.value("probe.conductance.calibrated"))

    def test_calibration_requires_host_standard_fresh_stable_and_bounded(self):
        self.prepare()
        self.assertEqual(self.invoke("electrochemistry.calibrate", slot="ph", standard_value=7)["error"]["code"], "PRECONDITION_FAILED")
        self.settle(self.sample())
        for cap, args, code in (("electrochemistry.calibrate", {"slot": "ph", "standard_value": 7}, "PRECONDITION_FAILED"),
            ("electrochemistry.calibrate", {"slot": "ph", "standard_value": 21}, "INVALID_ARGUMENT"),
            ("electrochemistry.calibrate", {"slot": "do", "standard_value": 0.051}, "INVALID_ARGUMENT")):
            before = self.host.debug_snapshot()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], code)
            self.assertEqual(before, self.host.debug_snapshot())

    def test_optional_conductance_temperature_compensation_no_cell_constant(self):
        self.prepare("conductance")
        self.step(self.sample("conductance", 0.02, temperature_k=308.15))
        self.assertEqual(self.value("measurement.conductance_s"), 0.02)
        self.step(self.sample("conductance", 0.02, temperature_k=308.15, conductance_temp_coefficient_k_inv=0.02, conductance_reference_temperature_k=298.15))
        self.assertAlmostEqual(self.value("measurement.conductance_s"), 0.02 / 1.2)
        self.step(self.sample("conductance", 0.02, conductance_temp_coefficient_k_inv=0.02))
        self.assertEqual(self.value("measurement.status"), "MISSING_CONFIGURATION")

    def test_record_interval_actual_timestamps_no_backfill_and_stop_preserves(self):
        self.prepare()
        self.ok("acquisition.set_interval", interval_s=2)
        self.ok("acquisition.start")
        self.step(self.sample())
        self.assertEqual(self.value("acquisition.record_count"), 0)
        self.step(self.sample())
        self.assertEqual(self.value("acquisition.record_count"), 1)
        self.step(self.sample(), interval=8)
        records = self.ok("acquisition.records")["result"]["records"]
        self.assertEqual([x["sample_time_s"] for x in records], [2, 10])
        self.assertEqual(self.invoke("acquisition.set_interval", interval_s=3)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.invoke("electrochemistry.select_probe", slot="ph")["error"]["code"], "PRECONDITION_FAILED")
        self.ok("acquisition.stop")
        self.step(self.sample(), interval=10)
        self.assertEqual(self.ok("acquisition.records")["result"]["records"], records)

    def test_invalid_arguments_no_process_time_or_scene_mutation(self):
        cases = [("power.set", {"enabled": 1}), ("power.set", {"enabled": True, "role": "evaluator"}),
            ("probe.set_installed", {"slot": [], "installed": True}), ("probe.set_installed", {"slot": "ph", "installed": 1}),
            ("electrochemistry.select_probe", {"slot": "bad"}),
            ("electrochemistry.configure_temperature", {"mode": "manual", "manual_temperature_k": 0}),
            ("electrochemistry.configure_temperature", {"mode": "automatic", "manual_temperature_k": 300}),
            ("electrochemistry.configure_endpoint", {"hold_s": 0, "tolerance_fraction": 0}),
            ("electrochemistry.configure_endpoint", {"hold_s": 2, "tolerance_fraction": 1.1}),
            ("acquisition.set_interval", {"interval_s": 1.5}), ("electrochemistry.reading", {"role": "debug"})]
        cases += [("electrochemistry.configure_endpoint", {"hold_s": v, "tolerance_fraction": 0}) for v in (True, _m914_test_math.nan, _m914_test_math.inf, 10**400, "2", -1)]
        for cap, args in cases:
            before, calls = self.host.debug_snapshot(), len(self.calls)
            response = self.invoke(cap, **args)
            self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT", (cap, args, response))
            self.assertEqual(response["error"]["side_effects"], "none")
            self.assertEqual(before, self.host.debug_snapshot())
            self.assertEqual(calls, len(self.calls))

    def test_host_rejection_is_atomic_for_all_instances(self):
        self.prepare()
        self.step(self.sample())
        invalid = [(0, {}), (1, {"m914_a": self.sample()}), (2, {"unknown": {}}),
            (2, {"m914_a": {"ph": True}}), (2, {"m914_a": {"ph": _m914_test_math.nan}}),
            (2, {"m914_a": {"temperature_k": 0}}), (2, {"m914_a": {"standard_slot": "ph"}}),
            (2, {"m914_a": {"ph": 7}, "m914_b": {"bad": 2}}),
            (2, {"m914_a": {"ph": 7, "standard_slot": "ph", "standard_value": 21}})]
        for time, inputs in invalid:
            before, calls = self.host.debug_snapshot(), len(self.calls)
            with self.assertRaises(ValueError):
                self.host.host_step(time, inputs=inputs)
            self.assertEqual(before, self.host.debug_snapshot())
            self.assertEqual(calls, len(self.calls))
        before = self.host.debug_snapshot()
        self.host.host_step(self.host.time_s)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_identity_truth_filter_errors_logs_records_and_copies(self):
        self.prepare()
        self.step(self.sample(value=7.123456789, reference_potential_v=0.123456789))
        self.assertEqual(self.agent.read(instance_id="m914_a", state_ids=["truth.ph"])["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.agent.write(instance_id="m914_a", state_id="truth.ph", value=8, request_id="denied")["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.value("truth.ph"), 7.123456789)
        all_agent = _m914_test_json.dumps([self.agent.read(instance_id="m914_a"), self.agent.logs(), self.ok("acquisition.records")])
        self.assertNotIn('"truth.', all_agent)
        self.assertNotIn("7.123456789", all_agent)
        self.assertNotIn("0.123456789", all_agent)
        response = self.agent.read(instance_id="m914_a")
        response["observations"]["power.enabled"]["value"] = False
        self.assertTrue(self.value("power.enabled"))
        with self.assertRaises(ValueError):
            self.host.session("unregistered")

    def test_request_dedup_conflict_and_write_route(self):
        args = {"instance_id": "m914_a", "capability_id": "probe.set_installed", "arguments": {"slot": "ph", "installed": True}, "request_id": "same"}
        first = self.agent.invoke(**args)
        before, calls = self.host.debug_snapshot(), len(self.calls)
        self.assertEqual(first, self.agent.invoke(**args))
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(calls, len(self.calls))
        args["arguments"] = {"slot": "ph", "installed": False}
        self.assertEqual(self.agent.invoke(**args)["error"]["code"], "REQUEST_CONFLICT")
        response = self.agent.write(instance_id="m914_a", state_id="power.enabled", value=True, request_id="write")
        self.assertEqual(response["status"], "succeeded")
        self.assertTrue(self.value("power.enabled"))

    def test_scene_whitelist_and_callback_failure_boundaries(self):
        self.prepare()
        self.step(self.sample())
        allowed = {"power.enabled", "probe.selected", "display.text", "probe.ph.installed", "probe.conductance.installed", "probe.do.installed"}
        self.assertTrue(all(set(values) == allowed for _, values in self.calls))
        def broken(instance, updates):
            raise RuntimeError("private debug detail must not escape")
        self.host.scene_adapter = broken
        before = self.host.debug_snapshot()
        response = self.invoke("power.set", enabled=False)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertNotIn("private", _m914_test_json.dumps(response))
        with self.assertRaises(RuntimeError):
            self.step(self.sample())
        self.assertEqual(self.host.time_s, 2)
        self.assertEqual(self.value("measurement.sample_time_s"), 2)

    def test_every_capability_schema_output_and_complete_state_effects(self):
        from asset_tool import lint_schema as lint, validate_value as validate
        metadata = export_metrohm914_contract_metadata()["asset_types"][METROHM914_TYPE]
        checked = set()
        for cap, definition in metadata["capabilities"].items():
            self.assertEqual(lint(definition["input_schema"]), [], cap)
            self.assertEqual(lint(definition["output_schema"]), [], cap)
            for example in definition["example_arguments"]:
                self.assertEqual(validate(example, definition["input_schema"]), [], cap)
        def verify(cap, **args):
            before = self.host.debug_snapshot()["instances"]["m914_a"]["state"]
            response = self.ok(cap, **args)
            after = self.host.debug_snapshot()["instances"]["m914_a"]["state"]
            changed = {n for n in after if after[n] != before[n]}
            declared = {e["state_id"] for e in metadata["capabilities"][cap]["state_effects"]}
            self.assertLessEqual(changed, declared, (cap, changed - declared))
            self.assertEqual(validate(response["result"], metadata["capabilities"][cap]["output_schema"]), [], cap)
            checked.add(cap)
        verify("probe.set_installed", slot="ph", installed=True)
        verify("probe.set_installed", slot="conductance", installed=True)
        verify("electrochemistry.select_probe", slot="conductance")
        verify("electrochemistry.select_probe", slot="ph")
        verify("electrochemistry.configure_endpoint", hold_s=2, tolerance_fraction=0)
        verify("electrochemistry.configure_temperature", mode="automatic", manual_temperature_k=None)
        verify("acquisition.set_interval", interval_s=1)
        verify("power.set", enabled=True)
        self.settle(self.sample(value=6.9, standard_slot="ph", standard_value=7))
        verify("electrochemistry.calibrate", slot="ph", standard_value=7)
        verify("electrochemistry.reading")
        verify("acquisition.start")
        self.step(self.sample())
        verify("acquisition.records")
        verify("acquisition.stop")
        verify("power.set", enabled=False)
        self.assertEqual(checked, set(metadata["capabilities"]))
        all_values = self.host.debug_snapshot()["instances"]["m914_a"]["state"]
        self.assertEqual(set(all_values), set(metadata["state_slots"]))
        for sid, definition in metadata["state_slots"].items():
            self.assertEqual(validate(all_values[sid], definition["value_schema"]), [], sid)
# END CHEM-005 METROHM 914 APPEND-ONLY TESTS

# BEGIN CHEM-102 METROHM 899 APPEND-ONLY TESTS
from basic40_runtime_measure import (Metrohm899Host, Metrohm899Session, METROHM899_TYPE,
    KF899_KG_PER_COULOMB, KF899_START_DRIFT_MIN_KG_S, KF899_START_DRIFT_MAX_KG_S,
    export_metrohm899_contract_metadata)


class Metrohm899RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = Metrohm899Host(lambda iid, values: self.calls.append((iid, values)))
        self.host.create_instance("kf_a")
        self.host.create_instance("kf_b")
        self.host.register_principal("operator", visibility="agent", instance_ids=["kf_a"])
        self.host.register_principal("judge", visibility="evaluator")
        self.agent = self.host.session("operator")
        self.judge = self.host.session("judge")
        self.request = 0

    def invoke(self, cap, **args):
        self.request += 1
        return self.agent.invoke(instance_id="kf_a", capability_id=cap, arguments=args, request_id=f"req{self.request}")

    def ok(self, cap, **args):
        value = self.invoke(cap, **args)
        self.assertEqual(value["status"], "succeeded", value)
        return value

    def value(self, sid, iid="kf_a"):
        return self.judge.read(instance_id=iid, state_ids=[sid])["observations"][sid]["value"]

    def prepare(self, water=1e-6, drift=0.0, current=0.1, hold=1.0, extraction=0.0, compensation=None, mass=None):
        for name in ("cell", "generator", "indicator"):
            self.ok("slot.set_installed", slot=name, installed=True)
        self.host.load_sample(instance_id="kf_a", water_mass_kg=water, background_drift_kg_s=drift, reagent_ready=True, request_id="sample")
        self.ok("coulometry.configure", current_a=current, drift_compensation_kg_s=drift if compensation is None else compensation,
                endpoint_hold_s=hold, extraction_time_s=extraction, sample_mass_kg=mass)
        self.ok("power.set", enabled=True)

    def test_unknowns_explicit_defaults_and_catalogue_ranges(self):
        for sid in ("measurement.water_mass_kg", "measurement.water_fraction", "measurement.drift_kg_s", "settings.endpoint_hold_s", "settings.sample_mass_kg", "settings.drift_compensation_kg_s"):
            self.assertIsNone(self.value(sid))
        self.assertEqual(self.value("settings.start_drift_threshold_kg_s"), 20e-9 / 60)
        self.assertEqual(self.value("settings.stirring_level"), 8)
        self.assertEqual(self.value("settings.current_a"), 0.1)
        self.assertFalse(self.value("slot.cell.installed"))
        meta = export_metrohm899_contract_metadata()
        for key in ("cell_capacity_m3", "electrode_response_s", "current_efficiency", "endpoint_hold_s"):
            self.assertIsNone(meta["parameters"][key]["value"])
        self.assertEqual(meta["parameters"]["initial_current_a"]["evidence"], "engineering_assumption")
        for args in ({"current_a": 0.1}, {"current_a": 0.2}, {"current_a": 0.4},
                     {"start_drift_threshold_kg_s": KF899_START_DRIFT_MIN_KG_S}, {"start_drift_threshold_kg_s": KF899_START_DRIFT_MAX_KG_S},
                     {"extraction_time_s": 999999}, {"extraction_time_s": 0}, {"stirring_level": -15}, {"stirring_level": 15}):
            self.ok("coulometry.configure", **args)

    def test_charge_water_stoichiometry_all_manual_currents(self):
        for current in (0.1, 0.2, 0.4):
            with self.subTest(current=current):
                self.setUp()
                self.prepare(water=1e-4, current=current, mass=0.01)
                self.ok("coulometry.start")
                self.host.step(10)
                self.assertAlmostEqual(self.value("measurement.charge_c"), current * 10, places=14)
                expected = current * 10 * KF899_KG_PER_COULOMB
                self.assertAlmostEqual(self.value("measurement.water_mass_kg"), expected, places=17)
                self.assertAlmostEqual(self.value("measurement.water_fraction"), expected / 0.01, places=17)
                self.assertAlmostEqual(self.value("truth.water_remaining_kg"), 1e-4 - expected, places=17)
                self.assertTrue(self.value("measurement.synthetic"))

    def test_drift_corrected_result_and_analytic_endpoint_timing(self):
        water, drift, current, hold, extraction = 2e-6, 2e-10, 0.2, 3.0, 7.0
        self.prepare(water=water, drift=drift, current=current, hold=hold, extraction=extraction, mass=0.001)
        self.ok("coulometry.start")
        rate = current * KF899_KG_PER_COULOMB
        drive = (water + drift * extraction) / (rate - drift)
        elapsed = extraction + drive + hold
        self.host.step(elapsed + 100)
        self.assertEqual(self.value("coulometry.status"), "COMPLETED")
        self.assertFalse(self.value("coulometry.running"))
        self.assertEqual(self.value("truth.water_remaining_kg"), 0)
        self.assertAlmostEqual(self.value("measurement.water_mass_kg"), water, places=18)
        self.assertAlmostEqual(self.value("measurement.water_fraction"), 0.002, places=15)
        self.assertAlmostEqual(self.value("measurement.elapsed_s"), elapsed, places=10)
        self.assertAlmostEqual(self.value("measurement.sample_time_s"), elapsed, places=10)
        self.assertAlmostEqual(self.value("measurement.charge_c") * KF899_KG_PER_COULOMB, water + drift * elapsed, places=18)
        self.assertAlmostEqual(self.host.time_s, elapsed + 100, places=10)
        observed = self.agent.read(instance_id="kf_a", state_ids=["measurement.water_mass_kg"])["observations"]["measurement.water_mass_kg"]
        self.assertAlmostEqual(observed["sample_window_s"][0], 0, places=10)
        self.assertAlmostEqual(observed["sample_window_s"][1], elapsed, places=10)

    def test_partition_invariance_extraction_and_hold(self):
        self.prepare(water=2e-7, drift=1e-10, extraction=5, hold=3)
        self.ok("coulometry.start")
        self.host.step(100)
        one = {sid: self.value(sid) for sid in ("measurement.elapsed_s", "measurement.charge_c", "measurement.water_mass_kg", "measurement.sample_time_s")}
        self.setUp()
        self.prepare(water=2e-7, drift=1e-10, extraction=5, hold=3)
        self.ok("coulometry.start")
        for _ in range(100):
            self.host.step(1)
        for sid, value in one.items():
            self.assertAlmostEqual(self.value(sid), value, places=11, msg=sid)

    def test_extraction_accumulates_drift_without_generating_charge(self):
        self.prepare(water=1e-6, drift=1e-10, extraction=10)
        self.ok("coulometry.start")
        self.host.step(4)
        self.assertEqual(self.value("measurement.charge_c"), 0)
        self.assertAlmostEqual(self.value("truth.water_remaining_kg"), 1e-6 + 4e-10, places=18)
        self.assertEqual(self.value("coulometry.status"), "EXTRACTING")
        self.assertEqual(self.value("measurement.water_mass_kg"), 0)

    def test_zero_water_and_zero_hold_completes_without_negative_mass(self):
        self.prepare(water=0, hold=0, compensation=1e-5)
        self.ok("coulometry.start")
        self.host.step(1)
        self.assertEqual(self.value("coulometry.status"), "COMPLETED")
        self.assertEqual(self.value("measurement.elapsed_s"), 0)
        self.assertEqual(self.value("measurement.water_mass_kg"), 0)
        self.assertEqual(self.value("measurement.charge_c"), 0)

    def test_start_requires_slots_reagent_method_and_low_drift(self):
        self.assertEqual(self.invoke("coulometry.start")["error"]["code"], "PRECONDITION_FAILED")
        self.prepare(drift=21e-9 / 60)
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("coulometry.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.ok("coulometry.configure", start_drift_threshold_kg_s=22e-9 / 60)
        self.ok("coulometry.start")
        self.assertEqual(self.invoke("coulometry.start")["error"]["code"], "BUSY")
        self.ok("coulometry.stop")
        self.assertEqual(self.invoke("coulometry.start")["error"]["code"], "PRECONDITION_FAILED")
        self.host.load_sample(instance_id="kf_a", water_mass_kg=0, background_drift_kg_s=0, reagent_ready=False, request_id="no_reagent")
        self.assertEqual(self.invoke("coulometry.start")["error"]["code"], "PRECONDITION_FAILED")
        self.host.load_sample(instance_id="kf_a", water_mass_kg=0, background_drift_kg_s=0, reagent_ready=True, request_id="ready")
        self.ok("coulometry.configure", endpoint_hold_s=None)
        self.assertEqual(self.invoke("coulometry.start")["error"]["code"], "PRECONDITION_FAILED")

    def test_stop_and_power_loss_preserve_consumed_charge(self):
        self.prepare()
        self.ok("coulometry.start")
        self.host.step(5)
        before = {sid: self.value(sid) for sid in ("truth.water_remaining_kg", "measurement.charge_c", "measurement.water_mass_kg", "measurement.elapsed_s")}
        curve = self.ok("coulometry.curve")["result"]
        self.ok("coulometry.stop")
        self.host.step(20)
        for sid, value in before.items():
            self.assertEqual(self.value(sid), value)
        self.assertEqual(self.ok("coulometry.curve")["result"], curve)
        self.ok("power.set", enabled=False)
        self.assertEqual(self.value("coulometry.status"), "OFF")
        self.ok("power.set", enabled=True)
        self.assertEqual(self.invoke("coulometry.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.value("measurement.charge_c"), before["measurement.charge_c"])

    def test_slot_dependencies_and_running_configuration_rejections(self):
        for slot in ("generator", "indicator"):
            self.assertEqual(self.invoke("slot.set_installed", slot=slot, installed=True)["error"]["code"], "PRECONDITION_FAILED")
        self.ok("slot.set_installed", slot="cell", installed=True)
        self.ok("slot.set_installed", slot="generator", installed=True)
        self.assertEqual(self.invoke("slot.set_installed", slot="cell", installed=False)["error"]["code"], "PRECONDITION_FAILED")
        self.ok("slot.set_installed", slot="generator", installed=False)
        self.ok("slot.set_installed", slot="cell", installed=False)
        self.prepare()
        self.ok("coulometry.start")
        before, calls = self.host.debug_snapshot(), len(self.calls)
        self.assertEqual(self.invoke("coulometry.configure", current_a=0.4)["error"]["code"], "BUSY")
        self.assertEqual(self.invoke("slot.set_installed", slot="generator", installed=False)["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(calls, len(self.calls))

    def test_reads_queries_and_discovery_do_not_advance_time(self):
        self.prepare()
        self.ok("coulometry.start")
        self.host.step(3)
        before, calls = self.host.debug_snapshot(), len(self.calls)
        for _ in range(4):
            self.agent.read(instance_id="kf_a")
            self.agent.describe(instance_id="kf_a")
            self.ok("coulometry.reading")
            self.ok("coulometry.curve")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(calls, len(self.calls))
        curve = self.ok("coulometry.curve")["result"]["records"]
        self.assertEqual([x["sample_time_s"] for x in curve], [3])

    def test_shared_clock_and_instance_isolation(self):
        self.prepare()
        self.ok("coulometry.start")
        self.host.step(4)
        self.assertEqual(self.judge.read(instance_id="kf_b")["clock"]["time_s"], 4)
        self.assertIsNone(self.value("truth.sample_water_kg", "kf_b"))
        self.assertEqual(self.value("measurement.charge_c", "kf_b"), 0)
        self.assertEqual(self.agent.read(instance_id="kf_b")["error"]["code"], "UNKNOWN_INSTANCE")

    def test_truth_filter_all_returns_logs_and_copies(self):
        self.prepare(water=0.00123456789)
        self.ok("coulometry.start")
        self.host.step(1)
        self.assertEqual(self.agent.read(instance_id="kf_a", state_ids=["truth.sample_water_kg"])["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.agent.write(instance_id="kf_a", state_id="truth.sample_water_kg", value=0, request_id="write_truth")["error"]["code"], "ACCESS_DENIED")
        public = [self.agent.describe(instance_id="kf_a"), self.agent.read(instance_id="kf_a"), self.agent.logs(), self.ok("coulometry.curve")]
        encoded = json.dumps(public)
        self.assertNotIn('"truth.', encoded)
        self.assertNotIn("0.00123456789", encoded)
        self.assertNotIn("sim.load_sample", self.agent.describe(instance_id="kf_a")["capabilities"])
        response = self.agent.read(instance_id="kf_a")
        response["observations"]["power.enabled"]["value"] = False
        self.assertTrue(self.value("power.enabled"))
        with self.assertRaises(ValueError):
            self.host.session("fake_judge")
        self.assertEqual(self.invoke("sim.step", dt_s=1)["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.invoke("hardware.connect")["status"], "unsupported")
        self.assertEqual(self.invoke("coulometry.auto_current")["status"], "unsupported")

    def test_request_idempotency_conflict_and_write_routing(self):
        args = {"instance_id": "kf_a", "capability_id": "slot.set_installed", "arguments": {"slot": "cell", "installed": True}, "request_id": "same"}
        response = self.agent.invoke(**args)
        before, calls = self.host.debug_snapshot(), len(self.calls)
        self.assertEqual(self.agent.invoke(**args), response)
        self.assertEqual(self.host.debug_snapshot(), before)
        self.assertEqual(len(self.calls), calls)
        args["arguments"] = {"slot": "cell", "installed": False}
        self.assertEqual(self.agent.invoke(**args)["error"]["code"], "REQUEST_CONFLICT")
        response = self.agent.write(instance_id="kf_a", state_id="settings.current_a", value=0.4, request_id="write")
        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(self.value("settings.current_a"), 0.4)
        params = {"instance_id": "kf_a", "water_mass_kg": 1e-6, "background_drift_kg_s": 0, "reagent_ready": True, "request_id": "hostsame"}
        self.host.load_sample(**params)
        before = self.host.debug_snapshot()
        self.host.load_sample(**params)
        self.assertEqual(before, self.host.debug_snapshot())
        params["water_mass_kg"] = 2e-6
        with self.assertRaisesRegex(ValueError, "REQUEST_CONFLICT"):
            self.host.load_sample(**params)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_invalid_arguments_reject_without_mutation(self):
        cases = [("power.set", {"enabled": 1}), ("power.set", {"enabled": True, "role": "debug"}),
                 ("slot.set_installed", {"slot": [], "installed": True}), ("slot.set_installed", {"slot": "cell", "installed": 1}),
                 ("coulometry.configure", {}), ("coulometry.configure", {"current_a": 0.3}),
                 ("coulometry.configure", {"sample_mass_kg": 0}), ("coulometry.configure", {"stirring_level": 1.5}),
                 ("coulometry.configure", {"extraction_time_s": 1000000}),
                 ("coulometry.configure", {"start_drift_threshold_kg_s": 0}), ("coulometry.start", {"role": "evaluator"})]
        cases.extend(("coulometry.configure", {"endpoint_hold_s": value}) for value in (True, -1, math.nan, math.inf, 10**400, "1"))
        for cap, args in cases:
            before, calls = self.host.debug_snapshot(), len(self.calls)
            response = self.invoke(cap, **args)
            self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT", (cap, args, response))
            self.assertEqual(response["error"]["side_effects"], "none")
            self.assertEqual(before, self.host.debug_snapshot())
            self.assertEqual(calls, len(self.calls))

    def test_host_invalid_inputs_and_overflow_are_atomic(self):
        self.prepare()
        self.ok("coulometry.start")
        for dt in (-1, True, math.nan, math.inf, "1", 10**400):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.step(dt)
            self.assertEqual(before, self.host.debug_snapshot())
        before = self.host.debug_snapshot()
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(ValueError):
            self.host.load_sample(instance_id="kf_a", water_mass_kg=1e-7, background_drift_kg_s=0, reagent_ready=True, request_id="running")
        self.assertEqual(before, self.host.debug_snapshot())
        self.setUp()
        for mass, drift, ready in ((True, 0, True), (math.nan, 0, True), (0, -1, True), (0, 0, 1)):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.load_sample(instance_id="kf_a", water_mass_kg=mass, background_drift_kg_s=drift, reagent_ready=ready, request_id="invalid")
            self.assertEqual(before, self.host.debug_snapshot())

    def test_scene_callback_whitelist_and_failure_boundaries(self):
        self.prepare()
        self.ok("coulometry.start")
        self.host.step(1)
        expected = set(export_metrohm899_contract_metadata()["scene_callback_keys"])
        self.assertTrue(all(set(row) == expected for _, row in self.calls))
        def broken(iid, updates):
            raise RuntimeError("secret chemical truth")
        self.host.scene_adapter = broken
        before = self.host.debug_snapshot()
        result = self.invoke("power.set", enabled=False)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["side_effects"], "unknown")
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(RuntimeError):
            self.host.step(1)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_every_capability_schema_output_state_and_effects(self):
        meta = export_metrohm899_contract_metadata()
        for cap, definition in meta["capabilities"].items():
            self.assertEqual(lint_schema(definition["input_schema"]), [], cap)
            self.assertEqual(lint_schema(definition["output_schema"]), [], cap)
            for example in definition["example_arguments"]:
                self.assertEqual(validate_value(example, definition["input_schema"]), [], cap)
        checked = set()
        def verify(cap, **args):
            before = self.host.debug_snapshot()["instances"]["kf_a"]["state"]
            response = self.ok(cap, **args)
            after = self.host.debug_snapshot()["instances"]["kf_a"]["state"]
            changed = {sid for sid in after if after[sid] != before[sid]}
            self.assertLessEqual(changed, {e["state_id"] for e in meta["capabilities"][cap]["state_effects"]}, cap)
            self.assertEqual(validate_value(response["result"], meta["capabilities"][cap]["output_schema"]), [], cap)
            checked.add(cap)
        for slot in ("cell", "generator", "indicator"):
            verify("slot.set_installed", slot=slot, installed=True)
        self.host.load_sample(instance_id="kf_a", water_mass_kg=1e-6, background_drift_kg_s=0, reagent_ready=True, request_id="sample")
        verify("coulometry.configure", current_a=0.2, drift_compensation_kg_s=0, endpoint_hold_s=1)
        verify("power.set", enabled=True)
        verify("coulometry.start")
        self.host.step(1)
        verify("coulometry.reading")
        verify("coulometry.curve")
        verify("coulometry.stop")
        verify("power.set", enabled=False)
        self.assertEqual(checked, set(meta["capabilities"]))
        state = self.host.debug_snapshot()["instances"]["kf_a"]["state"]
        self.assertEqual(set(state), set(meta["state_slots"]))
        for sid, value in state.items():
            self.assertEqual(validate_value(value, meta["state_slots"][sid]["value_schema"]), [], sid)
# END CHEM-102 METROHM 899 APPEND-ONLY TESTS

# CHEM-102 append-only edge-case additions.
def _kf899_test_finite_intermediate_overflow(self):
    self.prepare(water=1e-6, compensation=1e308)
    self.ok("coulometry.start")
    before, calls = self.host.debug_snapshot(), len(self.calls)
    with self.assertRaisesRegex(ValueError, "finite numerical range"):
        self.host.step(10)
    self.assertEqual(before, self.host.debug_snapshot())
    self.assertEqual(calls, len(self.calls))


def _kf899_test_power_loss_while_running(self):
    self.prepare()
    self.ok("coulometry.start")
    self.host.step(2)
    charge = self.value("measurement.charge_c")
    remaining = self.value("truth.water_remaining_kg")
    self.ok("power.set", enabled=False)
    self.host.step(100)
    self.assertFalse(self.value("coulometry.running"))
    self.assertEqual(self.value("coulometry.status"), "OFF")
    self.assertEqual(self.value("measurement.charge_c"), charge)
    self.assertEqual(self.value("truth.water_remaining_kg"), remaining)


Metrohm899RuntimeTests.test_finite_intermediate_overflow = _kf899_test_finite_intermediate_overflow
Metrohm899RuntimeTests.test_power_loss_while_running = _kf899_test_power_loss_while_running


# BEGIN CHEM-101 METROHM OMNIS APPEND-ONLY TESTS
from basic40_runtime_measure import (MetrohmOmnisRuntime, METROHM_OMNIS_TYPE,
    METROHM_OMNIS_CYLINDERS_M3, METROHM_OMNIS_MAX_SPEED_RAD_S,
    export_metrohm_omnis_contract_metadata)


class MetrohmOmnisRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = MetrohmOmnisRuntime(scene_adapter=lambda name, scene: self.calls.append((name, copy.deepcopy(scene))))
        self.host.create_instance("omnis_a")
        self.host.create_instance("omnis_b")
        self.host.register_principal("omnis_student", visibility="agent", instance_ids=["omnis_a"])
        self.host.register_principal("omnis_evaluator", visibility="evaluator")
        self.agent, self.evaluator = self.host.session("omnis_student"), self.host.session("omnis_evaluator")
        self.counter = 0

    def invoke(self, cap, **args):
        self.counter += 1
        return self.agent.invoke(instance_id="omnis_a", capability_id=cap, arguments=args, request_id="omnis_%d" % self.counter)

    def ok(self, cap, **args):
        response = self.invoke(cap, **args)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, sid, instance="omnis_a"):
        return self.evaluator.read(instance_id=instance, state_ids=[sid])["observations"][sid]["value"]

    @staticmethod
    def sample(**kwargs):
        result = {"volume_m3": 2.5e-5, "acid_moles": 0.001, "temperature_k": 298.15, "reference_ph": 7.0, "reference_potential_v": 0.0}
        result.update(kwargs)
        return result

    def prepare(self, *, mode="endpoint", target=0.0, maximum=1.5e-5, increment=1e-7, rate=5e-7, hold=0.5):
        self.ok("slot.set_connection", port_id="electrode", connection_id="electrode_01")
        self.ok("slot.set_connection", port_id="titration_cell", connection_id="beaker_01")
        self.ok("dosing.configure_cylinder", volume_m3=2e-5)
        self.ok("titration.configure_titrant", concentration_mol_m3=100.0)
        self.ok("titration.configure_method", mode=mode, dose_increment_m3=increment, maximum_volume_m3=maximum, endpoint_potential_v=target if mode == "endpoint" else None)
        self.ok("dosing.set_rate", rate_m3_s=rate)
        self.ok("titration.configure_stability", hold_s=hold, tolerance_v=0.0)
        self.ok("stirring.set_speed", angular_speed_rad_s=10.0)
        self.ok("power.set", enabled=True)
        self.host.host_load_sample("omnis_a", self.sample())

    def state(self, instance="omnis_a"):
        return copy.deepcopy(self.host._instances[instance])

    def test_defaults_and_catalog_parameters_do_not_invent_missing_values(self):
        metadata = export_metrohm_omnis_contract_metadata()["asset_types"][METROHM_OMNIS_TYPE]
        self.assertEqual(metadata["catalog_id"], "CHEM-101")
        self.assertEqual(metadata["parameters"]["steps_per_cylinder"]["value"], 100000)
        self.assertAlmostEqual(METROHM_OMNIS_MAX_SPEED_RAD_S, 1800 * 2 * math.pi / 60)
        for key in ("manufacturer_max_flow_rate", "electrode_response_time", "electrode_mass", "cylinder_actual_stroke", "calibration_accuracy"):
            self.assertIsNone(metadata["parameters"][key]["value"])
            self.assertEqual(metadata["parameters"][key]["evidence"], "unknown")
        for sid in ("dosing.rate_m3_s", "stability.hold_s", "stability.tolerance_v", "measurement.ph", "measurement.potential_v", "titration.endpoint_volume_m3"):
            self.assertIsNone(self.value(sid))
        before = self.state()
        self.assertEqual(self.invoke("titration.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(self.state(), before)

    def test_catalog_cylinder_quantum_and_speed_boundaries(self):
        for volume in METROHM_OMNIS_CYLINDERS_M3:
            self.ok("dosing.configure_cylinder", volume_m3=volume)
            self.assertEqual(self.value("dosing.quantum_m3"), volume / 100000)
        self.ok("stirring.set_speed", angular_speed_rad_s=METROHM_OMNIS_MAX_SPEED_RAD_S)
        for cap, args in (("dosing.configure_cylinder", {"volume_m3": 3e-5}), ("stirring.set_speed", {"angular_speed_rad_s": METROHM_OMNIS_MAX_SPEED_RAD_S + 1e-6})):
            before = self.state()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(self.state(), before)

    def test_endpoint_curve_and_synthetic_content(self):
        self.prepare()
        self.ok("titration.start")
        self.assertEqual(self.value("titration.curve"), [])
        self.assertEqual(self.value("dosing.delivered_volume_m3"), 0)
        self.host.host_step(200)
        self.assertEqual(self.value("titration.status"), "completed")
        self.assertAlmostEqual(self.value("titration.endpoint_volume_m3"), 1e-5, delta=1e-9)
        curve = self.ok("titration.read_curve")["result"]
        self.assertTrue(curve["synthetic"])
        self.assertGreater(len(curve["samples"]), 50)
        self.assertTrue(all(p["synthetic"] for p in curve["samples"]))
        self.assertEqual(curve["samples"][0]["volume_m3"], 0)
        self.assertLess(curve["samples"][0]["ph"], 2)
        self.assertTrue(all(a["time_s"] < b["time_s"] and a["volume_m3"] < b["volume_m3"] for a, b in zip(curve["samples"], curve["samples"][1:])))
        result = self.ok("titration.calculate_content", sample_volume_m3=2.5e-5, stoichiometric_ratio=1)["result"]
        self.assertTrue(result["synthetic"])
        self.assertAlmostEqual(result["content_mol_m3"], 40.0, delta=0.004)
        # The analysis must use the explicitly supplied user sample volume, not truth.
        result2 = self.ok("titration.calculate_content", sample_volume_m3=5e-5, stoichiometric_ratio=2)["result"]
        self.assertEqual(result2["content_mol_m3"], result["content_mol_m3"])

    def test_dynamic_equivalent_is_derived_from_observed_curve(self):
        self.prepare(mode="dynamic_equivalent", increment=2e-7)
        self.ok("titration.start")
        self.host.host_step(500)
        self.assertEqual(self.value("titration.status"), "completed")
        endpoint = self.value("titration.endpoint_volume_m3")
        curve = self.value("titration.curve")
        slopes = [abs((b["potential_v"] - a["potential_v"]) / (b["volume_m3"] - a["volume_m3"])) for a, b in zip(curve, curve[1:])]
        peak = max(range(len(slopes)), key=slopes.__getitem__)
        self.assertEqual(endpoint, (curve[peak]["volume_m3"] + curve[peak + 1]["volume_m3"]) / 2)
        self.assertAlmostEqual(endpoint, 1e-5, delta=2e-7)
        increments = [round((b["volume_m3"] - a["volume_m3"]) / self.value("dosing.quantum_m3")) for a, b in zip(curve, curve[1:])]
        self.assertGreater(len(set(increments)), 1)
        self.assertTrue(all(x >= 1 for x in increments))

    def test_hold_and_integer_steps_then_stop_preserve_partial_effects(self):
        self.prepare(rate=1e-7, hold=1)
        self.ok("titration.start")
        self.host.host_step(0.9)
        self.assertEqual(self.value("titration.curve"), [])
        self.assertEqual(self.value("dosing.delivered_volume_m3"), 0)
        self.host.host_step(1.501)
        partial = self.value("dosing.delivered_volume_m3")
        self.assertGreater(partial, 0)
        self.assertLess(partial, 1e-7)
        self.assertAlmostEqual(partial / self.value("dosing.quantum_m3"), round(partial / self.value("dosing.quantum_m3")))
        curve = self.value("titration.curve")
        self.ok("titration.stop")
        self.host.host_step(100)
        self.assertEqual(self.value("dosing.delivered_volume_m3"), partial)
        self.assertEqual(self.value("titration.curve"), curve)
        self.assertEqual(self.value("titration.status"), "stopped")
        self.assertEqual(self.invoke("titration.start")["error"]["code"], "PRECONDITION_FAILED")
        self.host.host_load_sample("omnis_a", self.sample())
        self.assertEqual(self.value("dosing.delivered_volume_m3"), 0)
        self.assertEqual(self.value("titration.curve"), [])

    def test_power_off_stops_and_preserves_dosed_volume(self):
        self.prepare(rate=1e-7)
        self.ok("titration.start")
        self.host.host_step(1)
        volume, angle = self.value("dosing.delivered_volume_m3"), self.value("stirring.angle_rad")
        self.ok("power.set", enabled=False)
        self.host.host_step(10)
        self.assertEqual(self.value("titration.status"), "stopped")
        self.assertEqual(self.value("dosing.delivered_volume_m3"), volume)
        self.assertEqual(self.value("stirring.angle_rad"), angle)

    def test_read_describe_query_do_not_advance_or_call_scene(self):
        self.prepare()
        self.ok("titration.start")
        before, clock, calls = self.state(), self.host.time_s, len(self.calls)
        for _ in range(4):
            self.agent.describe(instance_id="omnis_a")
            self.agent.read(instance_id="omnis_a")
            self.ok("titration.read_curve")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.host.time_s, clock)
        self.assertEqual(len(self.calls), calls)
        self.host.host_step(0)
        self.assertEqual(self.state(), before)

    def test_host_time_partition_equivalence_and_two_instance_isolation(self):
        self.prepare()
        self.ok("titration.start")
        inactive = self.state("omnis_b")
        clone = MetrohmOmnisRuntime()
        clone.create_instance("omnis_a")
        clone._instances["omnis_a"] = self.state()
        self.host.host_step(200)
        for i in range(1, 801):
            clone.host_step(i / 4)
        result, other = self.state(), clone._instances["omnis_a"]
        for key in ("steps", "curve", "endpoint", "status", "running", "ph", "potential"):
            self.assertEqual(result[key], other[key], key)
        self.assertAlmostEqual(result["angle"], other["angle"], places=9)
        self.assertEqual(self.state("omnis_b"), inactive)
        self.assertEqual(self.evaluator.read(instance_id="omnis_b")["clock"]["time_s"], 200)

    def test_truth_and_identity_scope_do_not_leak(self):
        self.prepare()
        self.ok("titration.start")
        self.host.host_step(10)
        for response in (self.agent.describe(instance_id="omnis_a"), self.agent.read(instance_id="omnis_a"), self.ok("titration.read_curve"), self.agent.logs(), self.host.scene_snapshot("omnis_a")):
            text = json.dumps(response)
            self.assertNotIn('"truth.', text)
            self.assertNotIn('"acid_moles"', text)
            self.assertNotIn('"reference_potential_v"', text)
        for sid in ("truth.acid_moles", "truth.reference_potential_v"):
            self.assertEqual(self.agent.read(instance_id="omnis_a", state_ids=[sid])["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.value("truth.acid_moles"), 0.001)
        self.assertEqual(self.agent.read(instance_id="omnis_b")["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(self.agent.invoke(instance_id="omnis_b", capability_id="power.set", arguments={"enabled": True}, request_id="cross")["error"]["code"], "UNKNOWN_INSTANCE")
        with self.assertRaises(ValueError):
            self.host.session("unregistered_evaluator")
        self.assertFalse(hasattr(self.agent, "host_load_sample"))
        self.assertFalse(hasattr(self.agent, "host_step"))
        self.assertNotIn("sim.host_step", self.agent.describe(instance_id="omnis_a")["capabilities"])

    def test_dedup_conflict_and_return_copy_isolation(self):
        request = dict(instance_id="omnis_a", capability_id="power.set", arguments={"enabled": True}, request_id="same")
        first = self.agent.invoke(**request)
        before, calls = self.state(), len(self.calls)
        self.assertEqual(self.agent.invoke(**request), first)
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.calls), calls)
        first["result"]["enabled"] = False
        self.assertTrue(self.agent.invoke(**request)["result"]["enabled"])
        request["arguments"] = {"enabled": False}
        self.assertEqual(self.agent.invoke(**request)["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(self.state(), before)
        view = self.agent.read(instance_id="omnis_a")
        view["observations"]["power.enabled"]["value"] = False
        self.assertTrue(self.value("power.enabled"))

    def test_invalid_numeric_unknown_arguments_and_rejection_atomicity(self):
        self.prepare()
        cases = [("dosing.set_rate", {"rate_m3_s": x}) for x in (True, 0, -1, float("nan"), float("inf"), "1")]
        cases += [("power.set", {"enabled": 1}), ("slot.set_connection", {"port_id": "electrode", "connection_id": ""}), ("titration.configure_stability", {"hold_s": 0, "tolerance_v": 0}), ("titration.configure_stability", {"hold_s": 1, "tolerance_v": -1}), ("titration.start", {"visibility": "evaluator"}), ("dosing.set_rate", {"rate_m3_s": 1e-7, "extra": 0})]
        for cap, args in cases:
            with self.subTest(cap=cap, args=args):
                before = self.state()
                response = self.invoke(cap, **args)
                self.assertEqual(response["status"], "rejected", response)
                self.assertEqual(response["error"]["side_effects"], "none")
                self.assertEqual(self.state(), before)
        self.assertEqual(self.invoke("electrochem.eis")["error"]["code"], "UNKNOWN_CAPABILITY")
        self.ok("titration.start")
        for cap, args in (("dosing.set_rate", {"rate_m3_s": 2e-7}), ("slot.set_connection", {"port_id": "electrode", "connection_id": None}), ("stirring.set_speed", {"angular_speed_rad_s": 0})):
            before = self.state()
            self.assertEqual(self.invoke(cap, **args)["status"], "rejected")
            self.assertEqual(self.state(), before)

    def test_method_consistency_quantization_and_cylinder_capacity(self):
        self.ok("dosing.configure_cylinder", volume_m3=5e-6)
        for args in ({"mode": "endpoint", "dose_increment_m3": 1e-7, "maximum_volume_m3": 6e-6, "endpoint_potential_v": 0},
                     {"mode": "endpoint", "dose_increment_m3": 1e-7, "maximum_volume_m3": 4e-6, "endpoint_potential_v": None},
                     {"mode": "dynamic_equivalent", "dose_increment_m3": 1e-7, "maximum_volume_m3": 4e-6, "endpoint_potential_v": 0},
                     {"mode": "endpoint", "dose_increment_m3": 1.01e-10, "maximum_volume_m3": 4e-6, "endpoint_potential_v": 0}):
            before = self.state()
            self.assertEqual(self.invoke("titration.configure_method", **args)["status"], "rejected")
            self.assertEqual(self.state(), before)

    def test_potential_limits_return_null_without_clipping_or_fake_completion(self):
        for potential in (2.4, -2.4, 2.40001, -2.40001):
            self.setUp()
            self.prepare()
            self.host.host_load_sample("omnis_a", self.sample(acid_moles=0, reference_potential_v=potential))
            self.ok("titration.start")
            self.host.host_step(0.5)
            if abs(potential) <= 2.4:
                self.assertAlmostEqual(self.value("measurement.potential_v"), potential)
                self.assertNotEqual(self.value("titration.status"), "out_of_range")
            else:
                self.assertIsNone(self.value("measurement.potential_v"))
                self.assertIsNone(self.value("measurement.ph"))
                self.assertEqual(self.value("titration.status"), "out_of_range")
                self.assertIsNone(self.value("titration.endpoint_volume_m3"))
                self.assertEqual(self.value("titration.curve"), [])

    def test_maximum_volume_without_endpoint_and_no_fake_content(self):
        self.prepare(maximum=1e-6)
        self.ok("titration.start")
        self.host.host_step(200)
        self.assertEqual(self.value("titration.status"), "limit_reached")
        self.assertAlmostEqual(self.value("dosing.delivered_volume_m3"), 1e-6)
        self.assertIsNone(self.value("titration.endpoint_volume_m3"))
        self.assertEqual(self.invoke("titration.calculate_content", sample_volume_m3=2.5e-5, stoichiometric_ratio=1)["error"]["code"], "PRECONDITION_FAILED")

    def test_scene_snapshot_mapping_and_explicit_apply(self):
        self.prepare()
        self.ok("titration.start")
        self.host.host_step(1.0)
        snapshot = self.host.scene_snapshot("omnis_a")
        self.assertEqual(set(snapshot), set(export_metrohm_omnis_contract_metadata()["scene_snapshot_keys"]))
        self.assertEqual(snapshot["dosing.piston_fraction"], self.value("dosing.delivered_volume_m3") / self.value("dosing.cylinder_volume_m3"))
        self.assertAlmostEqual(snapshot["stirring.angle_rad"], 10 % (2 * math.pi))
        self.assertEqual(self.host.apply_scene("omnis_a"), snapshot)
        self.assertEqual(self.calls[-1], ("omnis_a", snapshot))
        snapshot["power.enabled"] = False
        self.assertTrue(self.host.scene_snapshot("omnis_a")["power.enabled"])
        self.host.scene_adapter = None
        with self.assertRaises(RuntimeError):
            self.host.apply_scene("omnis_a")

    def test_scene_failure_keeps_process_and_host_clock_uncommitted(self):
        def fail(name, snapshot):
            raise RuntimeError("external_scene_private_diagnostic")
        before = self.state()
        self.host.scene_adapter = fail
        response = self.invoke("power.set", enabled=True)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["code"], "DEVICE_ERROR")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("private_diagnostic", json.dumps(response))
        self.assertEqual(self.state(), before)
        self.host.scene_adapter = None
        self.prepare()
        self.ok("titration.start")
        before, other = self.state(), self.state("omnis_b")
        self.host.scene_adapter = fail
        with self.assertRaises(RuntimeError):
            self.host.host_step(1)
        self.assertEqual(self.host.time_s, 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.state("omnis_b"), other)

    def test_host_load_and_clock_reject_atomically(self):
        before = self.state()
        for sample in ({"volume_m3": 1}, self.sample(volume_m3=0), self.sample(acid_moles=-1), self.sample(temperature_k=float("nan")), self.sample(reference_ph=True)):
            with self.assertRaises(ValueError):
                self.host.host_load_sample("omnis_a", sample)
            self.assertEqual(self.state(), before)
        for time_s in (True, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.host.host_step(time_s)
            self.assertEqual(self.host.time_s, 0)
            self.assertEqual(self.state(), before)
        self.host.host_step(1)
        with self.assertRaises(ValueError):
            self.host.host_step(0.5)

    def test_write_routes_share_validation_and_truth_is_not_writable(self):
        response = self.agent.write(instance_id="omnis_a", state_id="power.enabled", value=True, request_id="write_power")
        self.assertEqual(response["status"], "succeeded")
        self.assertTrue(self.value("power.enabled"))
        response = self.agent.write(instance_id="omnis_a", state_id="stirring.angular_speed_rad_s", value=METROHM_OMNIS_MAX_SPEED_RAD_S + 1, request_id="write_speed")
        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
        for sid in ("truth.acid_moles", "titration.endpoint_volume_m3", "unlisted"):
            self.assertEqual(self.agent.write(instance_id="omnis_a", state_id=sid, value=1, request_id="write_" + sid)["error"]["code"], "ACCESS_DENIED")

    def test_every_capability_schema_effects_and_all_state_schemas(self):
        metadata = export_metrohm_omnis_contract_metadata()["asset_types"][METROHM_OMNIS_TYPE]
        checked = set()
        for name, definition in metadata["capabilities"].items():
            self.assertEqual(lint_schema(definition["input_schema"]), [], name)
            self.assertEqual(lint_schema(definition["output_schema"]), [], name)
            for example in definition["example_arguments"]:
                self.assertEqual(validate_value(example, definition["input_schema"]), [], name)
        def verify(name, **args):
            before = self.host._values(self.state())
            response = self.ok(name, **args)
            after = self.host._values(self.state())
            changed = {k for k in after if before[k] != after[k]}
            declared = {e["state_id"] for e in metadata["capabilities"][name]["state_effects"]}
            self.assertLessEqual(changed, declared, (name, changed - declared))
            self.assertEqual(validate_value(response["result"], metadata["capabilities"][name]["output_schema"]), [], name)
            checked.add(name)
        verify("slot.set_connection", port_id="electrode", connection_id="electrode_01")
        verify("slot.set_connection", port_id="titration_cell", connection_id="beaker_01")
        verify("dosing.configure_cylinder", volume_m3=2e-5)
        verify("titration.configure_titrant", concentration_mol_m3=100)
        verify("titration.configure_method", mode="endpoint", dose_increment_m3=1e-7, maximum_volume_m3=1.5e-5, endpoint_potential_v=0)
        verify("dosing.set_rate", rate_m3_s=5e-7)
        verify("titration.configure_stability", hold_s=0.5, tolerance_v=0)
        verify("stirring.set_speed", angular_speed_rad_s=10)
        verify("power.set", enabled=True)
        self.host.host_load_sample("omnis_a", self.sample())
        verify("titration.start")
        verify("titration.read_curve")
        self.host.host_step(200)
        calls = len(self.calls)
        verify("titration.calculate_content", sample_volume_m3=2.5e-5, stoichiometric_ratio=1)
        self.assertEqual(len(self.calls), calls)
        verify("titration.read_curve")
        verify("titration.stop")
        verify("power.set", enabled=False)
        self.assertEqual(checked, set(metadata["capabilities"]))
        values = self.host._values(self.state())
        self.assertEqual(set(values), set(metadata["state_slots"]))
        for sid, val in values.items():
            self.assertEqual(validate_value(val, metadata["state_slots"][sid]["value_schema"]), [], sid)
# END CHEM-101 METROHM OMNIS APPEND-ONLY TESTS


# CHEM-101 append-only regression checks for cross-configuration and numeric guards.
def _omnis_test_clock_finite_rotation_and_cylinder_method_consistency(self):
    self.ok("power.set", enabled=True)
    self.ok("stirring.set_speed", angular_speed_rad_s=METROHM_OMNIS_MAX_SPEED_RAD_S)
    before = self.state()
    with self.assertRaises(ValueError):
        self.host.host_step(1e308)
    self.assertEqual(self.state(), before)
    self.assertEqual(self.host.time_s, 0)
    self.ok("dosing.configure_cylinder", volume_m3=5e-6)
    self.ok("titration.configure_method", mode="endpoint", dose_increment_m3=5e-11, maximum_volume_m3=1e-6, endpoint_potential_v=0)
    before = self.state()
    self.assertEqual(self.invoke("dosing.configure_cylinder", volume_m3=2e-5)["error"]["code"], "PRECONDITION_FAILED")
    self.assertEqual(self.state(), before)
    metadata = export_metrohm_omnis_contract_metadata()
    self.assertEqual(len(metadata["mechanisms"]), 3)
    for mechanism in metadata["mechanisms"].values():
        self.assertIn("engineering_assumption", mechanism["meaning_zh"])
        for prop in mechanism["parameters"].values():
            self.assertIn(prop["evidence"], ("documented", "assumption", "unknown"))


MetrohmOmnisRuntimeTests.test_clock_finite_rotation_and_cylinder_method_consistency = _omnis_test_clock_finite_rotation_and_cylinder_method_consistency

# BEGIN CHEM-068 MAGRITEK SPINSOLVE 80 APPEND-ONLY TESTS
from basic40_runtime_measure import (MagritekSpinsolve80Runtime, SPINSOLVE80_TYPE,
    SPINSOLVE80_SCENE_KEYS, export_magritek_spinsolve80_contract_metadata)


class MagritekSpinsolve80RuntimeTests(unittest.TestCase):
    """Deterministic synthetic fixtures, not real samples or calibration records."""
    @staticmethod
    def fixture(proton=8, carbon=-12):
        return {"1H": [{"frequency_offset_hz": proton, "amplitude": 2.0, "t2_s": 0.4}],
            "13C": [{"frequency_offset_hz": carbon, "amplitude": 3.0, "t2_s": 0.3}]}

    @staticmethod
    def method(**updates):
        result = {"nucleus": "1H", "pulse_sequence": "single_pulse", "scans": 2,
            "relaxation_delay_s": 1.0, "acquisition_time_s": 0.5, "points": 64,
            "decoupling": False, "gradient_enabled": False}
        result.update(updates)
        return result

    def setUp(self):
        self.calls = []
        self.host = MagritekSpinsolve80Runtime(scene_adapter=lambda iid, snapshot: self.calls.append((iid, copy.deepcopy(snapshot))))
        for iid in ("nmr_a", "nmr_b"):
            self.host.create_instance(iid)
            self.host.register_sample(iid, "tube_01", self.fixture() if iid == "nmr_a" else self.fixture(proton=4, carbon=6))
        self.host.register_principal("operator_a", visibility="agent", instance_ids=["nmr_a"])
        self.host.register_principal("evaluation", visibility="evaluator", instance_ids=["nmr_a", "nmr_b"])
        self.agent, self.evaluator = self.host.session("operator_a"), self.host.session("evaluation")
        self.request_number = 0

    def state(self, iid="nmr_a"):
        return copy.deepcopy(self.host._instances[iid])

    def value(self, sid, iid="nmr_a"):
        return self.evaluator.read(instance_id=iid, state_ids=[sid])["observations"][sid]["value"]

    def invoke(self, cap, **args):
        self.request_number += 1
        return self.agent.invoke(instance_id="nmr_a", capability_id=cap, arguments=args, request_id="sp80_test_%d" % self.request_number)

    def ok(self, cap, **args):
        result = self.invoke(cap, **args)
        self.assertEqual(result["status"], "succeeded", result)
        return result

    def prepare(self, **method_updates):
        self.ok("power.set", enabled=True)
        self.ok("sample.insert", sample_id="tube_01")
        self.ok("acquisition.configure", **self.method(**method_updates))

    def test_start_preconditions_no_time_and_full_scan_boundary(self):
        self.assertEqual(self.invoke("acquisition.start")["error"]["code"], "PRECONDITION_FAILED")
        self.prepare()
        self.ok("acquisition.start")
        self.assertEqual(self.host.time_s, 0)
        self.assertEqual(self.value("measurement.fid"), [])
        self.host.host_step(1.499)
        self.assertEqual(self.value("acquisition.completed_scans"), 0)
        self.assertEqual(self.value("measurement.fid"), [])
        self.host.host_step(1.5)
        self.assertEqual(self.value("acquisition.completed_scans"), 1)
        self.assertEqual(self.value("measurement.sample_time_s"), 1.5)
        self.assertEqual(len(self.value("measurement.fid")), 64)
        self.assertEqual(self.value("acquisition.status"), "running")
        self.host.host_step(3)
        self.assertEqual(self.value("acquisition.status"), "completed")
        self.assertEqual(self.value("acquisition.progress"), 1)
        self.assertEqual(self.value("acquisition.completed_scans"), 2)
        self.assertEqual(self.value("measurement.sample_time_s"), 3)
        snapshot = self.state()
        self.host.host_step(30)
        self.assertEqual(self.state(), snapshot)

    def test_synthetic_fid_equation_and_proton_dft_peak(self):
        self.prepare(scans=1)
        self.ok("acquisition.start")
        self.host.host_step(1.5)
        fid = self.ok("nmr.read_fid")["result"]
        self.assertTrue(fid["synthetic"])
        self.assertTrue(all(p["synthetic"] for p in fid["samples"]))
        self.assertEqual(fid["samples"][0]["real"], 2)
        self.assertEqual(fid["samples"][0]["imaginary"], 0)
        point = fid["samples"][8]
        t = 8 * 0.5 / 64
        self.assertEqual(point["time_s"], t)
        self.assertAlmostEqual(point["real"], 2 * math.exp(-t / .4) * math.cos(2 * math.pi * 8 * t))
        self.assertAlmostEqual(point["imaginary"], 2 * math.exp(-t / .4) * math.sin(2 * math.pi * 8 * t))
        spectrum = self.ok("nmr.read_spectrum")["result"]
        self.assertEqual(spectrum["carrier_frequency_hz"], 80e6)
        self.assertEqual(max(spectrum["samples"], key=lambda p: p["amplitude"])["frequency_offset_hz"], 8)
        self.assertTrue(all(a["frequency_offset_hz"] < b["frequency_offset_hz"] for a, b in zip(spectrum["samples"], spectrum["samples"][1:])))

    def test_carbon_is_synthetic_and_unknown_carrier_stays_null(self):
        self.prepare(nucleus="13C", scans=1)
        self.ok("acquisition.start")
        self.host.host_step(1.5)
        response = self.ok("nmr.read_spectrum")["result"]
        self.assertEqual(response["nucleus"], "13C")
        self.assertIsNone(response["carrier_frequency_hz"])
        self.assertEqual(max(response["samples"], key=lambda p: p["amplitude"])["frequency_offset_hz"], -12)
        self.assertTrue(response["synthetic"])
        self.assertNotIn("ppm", json.dumps(response))

    def test_stop_partial_scan_preserves_complete_scans(self):
        self.prepare(scans=3)
        self.ok("acquisition.start")
        self.host.host_step(1.9)
        before = self.state()
        result = self.ok("acquisition.stop")["result"]
        self.assertEqual(result["completed_scans"], 1)
        self.assertEqual(result["status"], "stopped")
        self.host.host_step(100)
        for key in ("fid", "spectrum", "sample_time", "completed_scans", "elapsed", "progress"):
            self.assertEqual(self.state()[key], before[key], key)
        self.ok("acquisition.start")
        self.assertEqual(self.value("measurement.fid"), [])
        self.assertEqual(self.value("acquisition.completed_scans"), 0)
        self.assertEqual(self.host.time_s, 100)

    def test_stop_before_first_scan_and_power_loss(self):
        self.prepare()
        self.ok("acquisition.start")
        self.host.host_step(.5)
        self.ok("acquisition.stop")
        self.assertEqual(self.value("measurement.fid"), [])
        self.ok("acquisition.start")
        self.host.host_step(2)
        before = self.value("measurement.fid")
        self.ok("power.set", enabled=False)
        self.assertEqual(self.value("acquisition.status"), "stopped")
        self.host.host_step(20)
        self.assertEqual(self.value("measurement.fid"), before)
        self.assertEqual(self.invoke("acquisition.start")["error"]["code"], "PRECONDITION_FAILED")

    def test_sample_slot_uniqueness_busy_and_remove_reset(self):
        self.prepare()
        self.assertEqual(self.invoke("sample.insert", sample_id="tube_01")["error"]["code"], "PRECONDITION_FAILED")
        self.ok("acquisition.start")
        for cap, args in (("sample.remove", {}), ("sample.insert", {"sample_id": "tube_01"}), ("acquisition.configure", self.method()), ("acquisition.start", {})):
            before = self.state()
            self.assertEqual(self.invoke(cap, **args)["error"]["code"], "BUSY")
            self.assertEqual(self.state(), before)
        self.host.host_step(3)
        self.ok("sample.remove")
        self.assertIsNone(self.value("slot.sample_tube.connection_id"))
        self.assertEqual(self.value("sample.insertion_fraction"), 0)
        self.assertEqual(self.value("measurement.fid"), [])
        self.assertEqual(self.value("acquisition.status"), "idle")
        self.assertEqual(self.invoke("sample.insert", sample_id="unregistered")["error"]["code"], "PRECONDITION_FAILED")

    def test_numerical_integral_uses_observed_spectrum_with_clipped_endpoints(self):
        self.prepare(scans=1)
        self.ok("acquisition.start")
        self.host.host_step(1.5)
        spectrum = self.value("measurement.spectrum")
        p = next(p for p in spectrum if p["frequency_offset_hz"] == 8)
        q = next(p for p in spectrum if p["frequency_offset_hz"] == 10)
        expected = (p["amplitude"] + q["amplitude"]) / 2
        state, calls, clock = self.state(), len(self.calls), self.host.time_s
        area = self.ok("nmr.integrate", frequency_min_hz=8.5, frequency_max_hz=9.5)["result"]
        self.assertAlmostEqual(area["integral_hz"], expected)
        self.assertTrue(area["synthetic"])
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.host.time_s, clock)
        self.host._instances["nmr_a"]["samples"]["tube_01"]["1H"][0]["amplitude"] = 1000
        area2 = self.ok("nmr.integrate", frequency_min_hz=8.5, frequency_max_hz=9.5)["result"]
        self.assertEqual(area, area2)
        for a, b in ((5, 5), (10, -10), (-1000, 1), (1, 1000)):
            self.assertEqual(self.invoke("nmr.integrate", frequency_min_hz=a, frequency_max_hz=b)["error"]["code"], "PRECONDITION_FAILED")

    def test_read_describe_and_queries_never_advance_or_sync_scene(self):
        self.prepare()
        self.ok("acquisition.start")
        before, calls = self.state(), len(self.calls)
        for _ in range(3):
            self.agent.read(instance_id="nmr_a")
            self.agent.describe(instance_id="nmr_a")
            self.ok("nmr.read_fid")
            self.ok("nmr.read_spectrum")
        self.host.host_step(0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.host.time_s, 0)
        self.assertEqual(len(self.calls), calls)

    def test_host_time_partition_and_instances_are_independent(self):
        self.prepare()
        self.ok("acquisition.start")
        clone = MagritekSpinsolve80Runtime()
        clone.create_instance("nmr_a")
        clone._instances["nmr_a"] = self.state()
        other = self.state("nmr_b")
        self.host.host_step(3)
        for k in range(1, 31):
            clone.host_step(k / 10)
        final, partition = self.state(), clone._instances["nmr_a"]
        for k in final:
            if k != "revision":
                self.assertEqual(final[k], partition[k], k)
        self.assertEqual(self.state("nmr_b"), other)
        self.assertEqual(self.evaluator.read(instance_id="nmr_b")["clock"]["time_s"], 3)
        self.assertEqual(other["samples"]["tube_01"]["1H"][0]["frequency_offset_hz"], 4)

    def test_truth_access_scope_and_scene_filtering(self):
        self.prepare()
        self.ok("acquisition.start")
        self.host.host_step(1.5)
        for response in (self.agent.describe(instance_id="nmr_a"), self.agent.read(instance_id="nmr_a"), self.ok("nmr.read_fid"), self.ok("nmr.read_spectrum"), self.agent.logs(), self.host.scene_snapshot("nmr_a")):
            text = json.dumps(response)
            for secret in ('"truth.', '"signals"', '"t2_s"'):
                self.assertNotIn(secret, text)
        self.assertEqual(self.agent.read(instance_id="nmr_a", state_ids=["truth.registered_samples"])["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.value("truth.registered_samples")[0]["signals"]["1H"][0]["t2_s"], .4)
        for operation in (lambda: self.agent.read(instance_id="nmr_b"), lambda: self.agent.invoke(instance_id="nmr_b", capability_id="power.set", arguments={"enabled": True}, request_id="cross")):
            self.assertEqual(operation()["error"]["code"], "UNKNOWN_INSTANCE")
        for name in ("host_step", "register_sample", "register_principal"):
            self.assertFalse(hasattr(self.agent, name))
        with self.assertRaises(ValueError):
            self.host.session("evaluator_not_registered")

    def test_request_dedup_conflict_and_mutable_return_isolation(self):
        request = dict(instance_id="nmr_a", capability_id="power.set", arguments={"enabled": True}, request_id="same")
        first = self.agent.invoke(**request)
        before, calls = self.state(), len(self.calls)
        self.assertEqual(self.agent.invoke(**request), first)
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.calls), calls)
        first["result"]["enabled"] = False
        self.assertTrue(self.agent.invoke(**request)["result"]["enabled"])
        request["arguments"] = {"enabled": False}
        self.assertEqual(self.agent.invoke(**request)["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(self.state(), before)
        observation = self.agent.read(instance_id="nmr_a")
        observation["observations"]["power.enabled"]["value"] = False
        self.assertTrue(self.value("power.enabled"))

    def test_invalid_parameters_unsupported_settings_and_atomic_rejections(self):
        self.prepare()
        invalid = [("power.set", {"enabled": 1}), ("sample.insert", {"sample_id": ""}), ("acquisition.start", {"visibility": "evaluator"})]
        for key, values in (("points", (True, 31, 513, 64.0)), ("scans", (True, 0, 4097, 2.0)), ("acquisition_time_s", (0, float("nan"), float("inf"), True, "1")), ("relaxation_delay_s", (-1, float("nan"), True))):
            invalid += [("acquisition.configure", self.method(**{key: val})) for val in values]
        invalid.append(("acquisition.configure", self.method(extra=0)))
        for cap, args in invalid:
            with self.subTest(cap=cap, args=args):
                before = self.state()
                result = self.invoke(cap, **args)
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(result["error"]["side_effects"], "none")
                self.assertEqual(self.state(), before)
        for settings in ({"decoupling": True}, {"gradient_enabled": True}):
            before = self.state()
            self.assertEqual(self.invoke("acquisition.configure", **self.method(**settings))["error"]["code"], "UNSUPPORTED")
            self.assertEqual(self.state(), before)
        self.assertEqual(self.invoke("nmr.read_2d")["error"]["code"], "UNKNOWN_CAPABILITY")

    def test_host_register_and_clock_validation_preserve_state(self):
        for signals in ({}, self.fixture(proton=float("nan")), {"1H": [], "13C": [{"frequency_offset_hz": 1, "amplitude": True, "t2_s": .1}]}):
            before = self.state()
            with self.assertRaises(ValueError):
                self.host.register_sample("nmr_a", "extra", signals)
            self.assertEqual(self.state(), before)
        fixture = self.fixture()
        self.host.register_sample("nmr_a", "extra", fixture)
        fixture["1H"][0]["amplitude"] = 1111
        self.assertEqual(self.host._instances["nmr_a"]["samples"]["extra"]["1H"][0]["amplitude"], 2)
        for time in (True, -1, float("nan"), float("inf")):
            before = self.state()
            with self.assertRaises(ValueError):
                self.host.host_step(time)
            self.assertEqual(self.state(), before)
            self.assertEqual(self.host.time_s, 0)
        self.host.host_step(1)
        with self.assertRaises(ValueError):
            self.host.host_step(.5)

    def test_empty_nucleus_nyquist_and_large_clock_preconditions(self):
        self.host.register_sample("nmr_a", "empty", {"1H": [], "13C": []})
        self.ok("sample.insert", sample_id="empty")
        self.ok("power.set", enabled=True)
        self.ok("acquisition.configure", **self.method())
        self.assertEqual(self.invoke("acquisition.start")["error"]["code"], "PRECONDITION_FAILED")
        self.ok("sample.remove")
        self.host.register_sample("nmr_a", "aliased", self.fixture(proton=64))
        self.ok("sample.insert", sample_id="aliased")
        self.assertEqual(self.invoke("acquisition.start")["error"]["code"], "PRECONDITION_FAILED")
        self.ok("sample.remove")
        self.ok("sample.insert", sample_id="tube_01")
        self.host.host_step(1e308)
        self.assertEqual(self.invoke("acquisition.start")["error"]["code"], "PRECONDITION_FAILED")

    def test_scene_snapshot_callback_and_failure_preserve_process(self):
        self.prepare()
        scene = self.host.scene_snapshot("nmr_a")
        self.assertEqual(set(scene), set(SPINSOLVE80_SCENE_KEYS))
        self.assertEqual(scene["sample.insertion_fraction"], 1)
        self.assertEqual(self.host.apply_scene("nmr_a"), scene)
        self.assertEqual(self.calls[-1], ("nmr_a", scene))
        def fail(iid, snapshot):
            raise RuntimeError("private_scene_diagnostic")
        before = self.state()
        self.host.scene_adapter = fail
        response = self.invoke("power.set", enabled=False)
        self.assertEqual(response["error"]["code"], "DEVICE_ERROR")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("private_scene_diagnostic", json.dumps(response))
        self.assertEqual(self.state(), before)
        self.host.scene_adapter = None
        self.ok("acquisition.start")
        before, other = self.state(), self.state("nmr_b")
        self.host.scene_adapter = fail
        with self.assertRaises(RuntimeError):
            self.host.host_step(1.5)
        self.assertEqual(self.host.time_s, 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.state("nmr_b"), other)
        self.host.scene_adapter = None
        with self.assertRaises(RuntimeError):
            self.host.apply_scene("nmr_a")

    def test_write_routes_apply_same_validation(self):
        self.assertEqual(self.agent.write(instance_id="nmr_a", state_id="power.enabled", value=True, request_id="write")["status"], "succeeded")
        self.assertTrue(self.value("power.enabled"))
        self.assertEqual(self.agent.write(instance_id="nmr_a", state_id="power.enabled", value=1, request_id="bad")["error"]["code"], "INVALID_ARGUMENT")
        for sid in ("truth.registered_samples", "measurement.fid", "unknown"):
            self.assertEqual(self.agent.write(instance_id="nmr_a", state_id=sid, value=1, request_id=sid)["error"]["code"], "ACCESS_DENIED")

    def test_every_api_input_output_state_schema_and_declared_effects(self):
        metadata = export_magritek_spinsolve80_contract_metadata()["asset_types"][SPINSOLVE80_TYPE]
        for name, spec in metadata["capabilities"].items():
            self.assertEqual(lint_schema(spec["input_schema"]), [], name)
            self.assertEqual(lint_schema(spec["output_schema"]), [], name)
            for example in spec["example_arguments"]:
                self.assertEqual(validate_value(example, spec["input_schema"]), [], name)
        checked = set()
        def verify(name, **args):
            before = self.host._values(self.state())
            response = self.ok(name, **args)
            after = self.host._values(self.state())
            changed = {k for k in after if before[k] != after[k]}
            declared = {e["state_id"] for e in metadata["capabilities"][name]["state_effects"]}
            self.assertLessEqual(changed, declared, (name, changed - declared))
            self.assertEqual(validate_value(response["result"], metadata["capabilities"][name]["output_schema"]), [], name)
            checked.add(name)
        verify("power.set", enabled=True)
        verify("sample.insert", sample_id="tube_01")
        verify("acquisition.configure", **self.method())
        verify("acquisition.start")
        verify("nmr.read_fid")
        verify("nmr.read_spectrum")
        self.host.host_step(1.5)
        verify("nmr.integrate", frequency_min_hz=-10, frequency_max_hz=10)
        verify("acquisition.stop")
        verify("sample.remove")
        verify("power.set", enabled=False)
        self.assertEqual(checked, set(metadata["capabilities"]))
        for inst in (self.state(), self.state("nmr_b")):
            values = self.host._values(inst)
            self.assertEqual(set(values), set(metadata["state_slots"]))
            for sid, value in values.items():
                self.assertEqual(validate_value(value, metadata["state_slots"][sid]["value_schema"]), [], sid)
# END CHEM-068 MAGRITEK SPINSOLVE 80 APPEND-ONLY TESTS

# CHEM-068 regression: exact scan boundaries and fully standard contract sections.
def _sp80_test_every_api_input_output_state_schema_and_declared_effects(self):
    metadata = export_magritek_spinsolve80_contract_metadata()["asset_types"][SPINSOLVE80_TYPE]
    contract_schema = json.loads((ROOT / "skills/chemistry-lab-assets/assets/asset-contract.schema.json").read_text())
    for name, spec in metadata["capabilities"].items():
        self.assertEqual(lint_schema(spec["input_schema"]), [], name)
        self.assertEqual(lint_schema(spec["output_schema"]), [], name)
        self.assertEqual(validate_value(spec["example_arguments"], spec["input_schema"]), [], name)
        self.assertEqual(validate_value(spec, contract_schema["$defs"]["capability"], contract_schema), [], name)
    checked = set()
    def verify(name, **args):
        before = self.host._values(self.state())
        response = self.ok(name, **args)
        after = self.host._values(self.state())
        changed = {k for k in after if before[k] != after[k]}
        declared = {e["state_id"] for e in metadata["capabilities"][name]["state_effects"]}
        self.assertLessEqual(changed, declared, (name, changed - declared))
        self.assertEqual(validate_value(response["result"], metadata["capabilities"][name]["output_schema"]), [], name)
        checked.add(name)
    verify("power.set", enabled=True)
    verify("sample.insert", sample_id="tube_01")
    verify("acquisition.configure", **self.method())
    verify("acquisition.start")
    verify("nmr.read_fid")
    verify("nmr.read_spectrum")
    self.host.host_step(1.5)
    verify("nmr.integrate", frequency_min_hz=-10, frequency_max_hz=10)
    verify("acquisition.stop")
    verify("sample.remove")
    verify("power.set", enabled=False)
    self.assertEqual(checked, set(metadata["capabilities"]))
    for inst in (self.state(), self.state("nmr_b")):
        values = self.host._values(inst)
        self.assertEqual(set(values), set(metadata["state_slots"]))
        for sid, value in values.items():
            spec = metadata["state_slots"][sid]
            self.assertEqual(validate_value(spec, contract_schema["$defs"]["slot"], contract_schema), [], sid)
            self.assertEqual(validate_value(value, spec["value_schema"]), [], sid)
    for name, mechanism in export_magritek_spinsolve80_contract_metadata()["mechanisms"].items():
        self.assertEqual(validate_value(mechanism, contract_schema["$defs"]["mechanism"], contract_schema), [], name)


def _sp80_test_decimal_scan_boundary_and_host_effects(self):
    self.prepare(scans=4, relaxation_delay_s=0, acquisition_time_s=.1)
    self.ok("acquisition.start")
    before = self.host._values(self.state())
    self.host.host_step(.3)
    self.assertEqual(self.value("acquisition.completed_scans"), 3)
    self.assertEqual(self.value("measurement.sample_time_s"), .3)
    self.assertAlmostEqual(self.value("acquisition.progress"), .75)
    after = self.host._values(self.state())
    changed = {k for k in after if before[k] != after[k]}
    declared = set(export_magritek_spinsolve80_contract_metadata()["host_capabilities"]["sim.host_step"]["state_effects"])
    self.assertLessEqual(changed, declared)
    self.host.host_step(.4)
    self.assertEqual(self.value("acquisition.completed_scans"), 4)
    self.assertEqual(self.value("acquisition.status"), "completed")


MagritekSpinsolve80RuntimeTests.test_every_api_input_output_state_schema_and_declared_effects = _sp80_test_every_api_input_output_state_schema_and_declared_effects
MagritekSpinsolve80RuntimeTests.test_decimal_scan_boundary_and_host_effects = _sp80_test_decimal_scan_boundary_and_host_effects

# BEGIN CHEM-160 RIGAKU MINIFLEX600 APPEND-ONLY TESTS
from basic40_runtime_measure import (RigakuMiniFlex600Runtime, RIGAKU_MINIFLEX600_TYPE,
    RIGAKU_MINIFLEX600_MIN_RAD, RIGAKU_MINIFLEX600_MAX_RAD, RIGAKU_MINIFLEX600_STEP_RAD,
    RIGAKU_MINIFLEX600_DOOR_MAX_RAD, export_rigaku_miniflex600_contract_metadata)


class RigakuMiniFlex600RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = RigakuMiniFlex600Runtime(scene_adapter=lambda name, data: self.calls.append((name, copy.deepcopy(data))))
        self.host.create_instance("mini_a")
        self.host.create_instance("mini_b")
        self.host.register_principal("agent_a", visibility="agent", instance_ids=["mini_a"])
        self.host.register_principal("evaluator", visibility="evaluator")
        self.agent = self.host.session("agent_a")
        self.evaluator = self.host.session("evaluator")
        self.sequence = 0

    def invoke(self, cap, **args):
        self.sequence += 1
        return self.agent.invoke(instance_id="mini_a", capability_id=cap, arguments=args, request_id="mf_%d" % self.sequence)

    def ok(self, cap, **args):
        value = self.invoke(cap, **args)
        self.assertEqual(value["status"], "succeeded", value)
        return value

    def state(self, name="mini_a"):
        return copy.deepcopy(self.host._instances[name])

    def value(self, sid, name="mini_a"):
        return self.evaluator.read(instance_id=name, state_ids=[sid])["observations"][sid]["value"]

    @staticmethod
    def response():
        # Public deterministic workflow fixture. No actual mineral or phase truth.
        return [{"two_theta_rad": math.radians(0), "intensity_cps": 10},
                {"two_theta_rad": math.radians(2), "intensity_cps": 30}]

    def prepare(self, integration=1):
        self.ok("power.set", enabled=True)
        self.ok("access.set_door_angle", angle_rad=0.8)
        self.ok("slot.set_sample", connection_id="powder_01")
        self.ok("access.set_door_angle", angle_rad=0)
        self.ok("xray.configure", voltage_v=40000, current_a=0.015)
        self.ok("xrd.set_slit", slit_id="explicit_fixture_slit")
        self.ok("xrd.configure_scan", start_rad=0, stop_rad=math.radians(2), step_rad=math.radians(0.5), integration_time_s=integration)
        self.host.host_set_cooling("mini_a", connection_id="external_chiller_01", ready=True)
        self.host.host_load_synthetic_response("mini_a", self.response())

    def assert_rejected_unchanged(self, cap, code=None, **args):
        before, clock, calls = self.state(), self.host.time_s, len(self.calls)
        response = self.invoke(cap, **args)
        self.assertIn(response["status"], ("rejected", "unsupported"), response)
        if code:
            self.assertEqual(response["error"]["code"], code, response)
        self.assertEqual(response["error"]["side_effects"], "none")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.host.time_s, clock)
        self.assertEqual(len(self.calls), calls)
        return response

    def test_initial_unknown_values_and_uniform_entrypoints(self):
        self.assertEqual(self.host.describe(asset_type_id=RIGAKU_MINIFLEX600_TYPE)["catalog_id"], "CHEM-160")
        self.assertEqual(self.agent.describe(instance_id="mini_a")["asset_type_id"], RIGAKU_MINIFLEX600_TYPE)
        self.assertEqual(self.agent.describe()["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.agent.describe(asset_type_id=RIGAKU_MINIFLEX600_TYPE, instance_id="mini_a")["error"]["code"], "INVALID_ARGUMENT")
        observations = self.host.read(instance_id="mini_a")["observations"]
        for sid in ("xray.voltage_v", "xray.current_a", "scan.integration_time_s", "measurement.last_intensity_cps", "slit.configuration_id", "cooling.connection_id"):
            self.assertIsNone(observations[sid]["value"])
            self.assertEqual(observations[sid]["quality"], "unavailable")
        self.assertEqual(self.host.invoke(instance_id="mini_a", capability_id="power.set", arguments={"enabled": True}, request_id="direct")["status"], "succeeded")
        self.assertTrue(self.value("power.enabled"))
        self.assertEqual(self.host.write(instance_id="mini_a", state_id="power.enabled", value=False, request_id="direct_write")["status"], "succeeded")
        self.assertFalse(self.value("power.enabled"))

    def test_all_scan_preconditions_and_host_response_required(self):
        self.assert_rejected_unchanged("xrd.start_scan", "PRECONDITION_FAILED")
        self.prepare()
        self.ok("power.set", enabled=False)
        self.assert_rejected_unchanged("xrd.start_scan", "PRECONDITION_FAILED")
        self.ok("power.set", enabled=True)
        self.ok("access.set_door_angle", angle_rad=0.1)
        self.assert_rejected_unchanged("xrd.start_scan", "PRECONDITION_FAILED")
        self.ok("access.set_door_angle", angle_rad=0)
        self.host.host_set_cooling("mini_a", connection_id="external_chiller_01", ready=False)
        self.assert_rejected_unchanged("xrd.start_scan", "PRECONDITION_FAILED")
        self.host.host_set_cooling("mini_a", connection_id="external_chiller_01", ready=True)
        self.ok("xray.configure", voltage_v=30000, current_a=0.01)
        self.assert_rejected_unchanged("xrd.start_scan", "PRECONDITION_FAILED")
        self.host.host_load_synthetic_response("mini_a", self.response())
        self.ok("xrd.set_slit", slit_id="another_explicit_slit")
        self.assert_rejected_unchanged("xrd.start_scan", "PRECONDITION_FAILED")
        self.host.host_load_synthetic_response("mini_a", self.response())
        self.ok("xrd.start_scan")
        self.assertTrue(self.value("xray.active"))
        self.assertEqual(self.value("measurement.pattern"), [])
        self.assertEqual(self.host.time_s, 0)

    def test_scan_numeric_integration_windows_and_end_pose(self):
        self.prepare(integration=2)
        self.ok("xrd.start_scan")
        self.host.host_step(1.99)
        self.assertEqual(self.value("scan.completed_points"), 0)
        self.host.host_step(2)
        self.assertEqual(self.value("scan.completed_points"), 1)
        self.assertAlmostEqual(self.value("goniometer.two_theta_rad"), math.radians(0.5))
        self.assertAlmostEqual(self.value("goniometer.sample_theta_rad"), math.radians(0.25))
        self.host.host_step(10)
        result = self.ok("xrd.read_pattern")["result"]
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["synthetic"])
        self.assertEqual([p["intensity_cps"] for p in result["samples"]], [10, 15, 20, 25, 30])
        self.assertEqual([p["expected_counts"] for p in result["samples"]], [20, 30, 40, 50, 60])
        self.assertEqual([p["sample_window_s"] for p in result["samples"]], [[0, 2], [2, 4], [4, 6], [6, 8], [8, 10]])
        self.assertFalse(self.value("xray.active"))
        self.assertFalse(self.value("scan.running"))
        self.assertAlmostEqual(self.value("goniometer.two_theta_rad"), math.radians(2))
        observation = self.agent.read(instance_id="mini_a", state_ids=["measurement.last_intensity_cps"])["observations"]["measurement.last_intensity_cps"]
        self.assertEqual(observation["sample_time_s"], 10)
        self.assertEqual(observation["sample_window_s"], [8, 10])
        self.assertEqual(observation["quality"], "estimated")

    def test_read_describe_query_never_advance_or_write_scene(self):
        self.prepare()
        self.ok("xrd.start_scan")
        before, clock, calls = self.state(), self.host.time_s, len(self.calls)
        for _ in range(4):
            self.agent.read(instance_id="mini_a")
            self.agent.describe(instance_id="mini_a")
            self.ok("xrd.read_pattern")
        self.host.host_step(0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.host.time_s, clock)
        self.assertEqual(len(self.calls), calls)

    def test_host_time_partition_equivalence_and_instances(self):
        self.prepare(integration=0.2)
        self.ok("xrd.start_scan")
        other = self.state("mini_b")
        clone = RigakuMiniFlex600Runtime()
        clone.create_instance("mini_a")
        clone._instances["mini_a"] = self.state()
        self.host.host_step(1.0)
        for i in range(1, 101):
            clone.host_step(i / 100)
        for key in ("pattern", "two_theta", "running", "status"):
            self.assertEqual(self.state()[key], clone._instances["mini_a"][key], key)
        self.assertEqual(self.state("mini_b"), other)
        self.assertEqual(self.evaluator.read(instance_id="mini_b")["clock"]["time_s"], 1.0)

    def test_door_sample_slot_and_running_busy_rejection_atomicity(self):
        self.assert_rejected_unchanged("slot.set_sample", "PRECONDITION_FAILED", connection_id="holder")
        self.prepare()
        self.ok("xrd.start_scan")
        for cap, args in (("access.set_door_angle", {"angle_rad": 0.5}), ("slot.set_sample", {"connection_id": None}),
                ("xray.configure", {"voltage_v": 20000, "current_a": 0.002}), ("motion.set_two_theta", {"angle_rad": 0}),
                ("xrd.set_slit", {"slit_id": "other"}), ("xrd.start_scan", {})):
            self.assert_rejected_unchanged(cap, "BUSY", **args)
        self.host.host_step(1.5)
        self.ok("xrd.stop_scan")
        self.ok("access.set_door_angle", angle_rad=1)
        self.ok("slot.set_sample", connection_id="powder_02")
        self.assertIsNone(self.value("truth.response"))
        self.assertIsNone(self.value("truth.sample_connection_id"))
        self.assertEqual(self.value("measurement.pattern"), [])
        self.ok("access.set_door_angle", angle_rad=0)
        self.assert_rejected_unchanged("xrd.start_scan", "PRECONDITION_FAILED")

    def test_stop_discards_unfinished_interval_preserves_complete_points(self):
        self.prepare()
        self.ok("xrd.start_scan")
        self.host.host_step(2.5)
        partial = self.value("measurement.pattern")
        self.assertEqual(len(partial), 2)
        self.ok("xrd.stop_scan")
        self.host.host_step(100)
        self.assertEqual(self.value("measurement.pattern"), partial)
        self.assertEqual(self.value("scan.status"), "stopped")
        self.ok("xrd.start_scan")
        self.assertEqual(self.value("measurement.pattern"), [])
        self.host.host_step(101)
        self.assertEqual(self.value("measurement.pattern")[0]["sample_window_s"], [100, 101])

    def test_cooling_loss_and_power_loss_stop_without_erasing_history(self):
        for cause in ("cooling", "power"):
            with self.subTest(cause=cause):
                self.setUp()
                self.prepare()
                self.ok("xrd.start_scan")
                self.host.host_step(1.5)
                partial = self.value("measurement.pattern")
                if cause == "cooling":
                    self.host.host_set_cooling("mini_a", connection_id=None, ready=False)
                    expected = "cooling_lost"
                else:
                    self.ok("power.set", enabled=False)
                    expected = "power_lost"
                self.assertFalse(self.value("scan.running"))
                self.assertFalse(self.value("xray.active"))
                self.assertEqual(self.value("scan.status"), expected)
                self.host.host_step(10)
                self.assertEqual(self.value("measurement.pattern"), partial)

    def test_catalog_voltage_current_quantization_and_power_limits(self):
        for voltage, current in ((20000, 0.002), (40000, 0.015), (31000, 0.009)):
            self.ok("xray.configure", voltage_v=voltage, current_a=current)
            self.assertAlmostEqual(self.value("xray.power_w"), voltage * current)
        for voltage, current in ((19000, 0.002), (41000, 0.002), (20500, 0.002), (20000, 0.001), (40000, 0.016), (20000, 0.0025)):
            self.assert_rejected_unchanged("xray.configure", "INVALID_ARGUMENT", voltage_v=voltage, current_a=current)

    def test_si_motion_bounds_quantization_and_theta_mapping(self):
        self.assert_rejected_unchanged("motion.set_two_theta", "PRECONDITION_FAILED", angle_rad=0)
        self.ok("power.set", enabled=True)
        for angle in (RIGAKU_MINIFLEX600_MIN_RAD, 0, RIGAKU_MINIFLEX600_MAX_RAD):
            self.ok("motion.set_two_theta", angle_rad=angle)
            self.assertEqual(self.value("goniometer.two_theta_rad"), angle)
            self.assertEqual(self.value("goniometer.sample_theta_rad"), angle / 2)
        for angle in (RIGAKU_MINIFLEX600_MIN_RAD - 0.001, RIGAKU_MINIFLEX600_MAX_RAD + 0.001, math.radians(0.0025)):
            self.assert_rejected_unchanged("motion.set_two_theta", "INVALID_ARGUMENT", angle_rad=angle)
        self.ok("access.set_door_angle", angle_rad=0.1)
        self.assert_rejected_unchanged("motion.set_two_theta", "PRECONDITION_FAILED", angle_rad=0)
        self.ok("access.set_door_angle", angle_rad=RIGAKU_MINIFLEX600_DOOR_MAX_RAD)
        for angle in (-0.001, RIGAKU_MINIFLEX600_DOOR_MAX_RAD + 0.001):
            self.assert_rejected_unchanged("access.set_door_angle", "INVALID_ARGUMENT", angle_rad=angle)

    def test_scan_grid_range_single_point_and_full_catalog_grid(self):
        self.ok("xrd.configure_scan", start_rad=RIGAKU_MINIFLEX600_MIN_RAD, stop_rad=RIGAKU_MINIFLEX600_MAX_RAD, step_rad=RIGAKU_MINIFLEX600_STEP_RAD, integration_time_s=1)
        self.assertEqual(self.value("scan.total_points"), 29601)
        for args in ({"start_rad": 0.1, "stop_rad": 0, "step_rad": RIGAKU_MINIFLEX600_STEP_RAD, "integration_time_s": 1},
                {"start_rad": 0, "stop_rad": math.radians(1), "step_rad": math.radians(0.3), "integration_time_s": 1},
                {"start_rad": 0, "stop_rad": math.radians(1), "step_rad": math.radians(0.0025), "integration_time_s": 1},
                {"start_rad": 0, "stop_rad": math.radians(1), "step_rad": math.radians(0.5), "integration_time_s": 1e308}):
            self.assert_rejected_unchanged("xrd.configure_scan", "INVALID_ARGUMENT", **args)
        self.prepare()
        self.ok("xrd.configure_scan", start_rad=0, stop_rad=0, step_rad=RIGAKU_MINIFLEX600_STEP_RAD, integration_time_s=1)
        self.ok("xrd.start_scan")
        self.host.host_step(1)
        self.assertEqual(self.value("scan.status"), "completed")
        self.assertEqual(self.value("scan.completed_points"), 1)

    def test_numeric_booleans_unknown_arguments_and_finite_guards(self):
        self.prepare()
        for invalid in (True, float("nan"), float("inf"), "0"):
            self.assert_rejected_unchanged("motion.set_two_theta", "INVALID_ARGUMENT", angle_rad=invalid)
        for cap, args in (("power.set", {"enabled": 1}), ("xrd.set_slit", {"slit_id": ""}), ("slot.set_sample", {"connection_id": {}}),
                ("xrd.start_scan", {"visibility": "evaluator"}), ("xray.configure", {"voltage_v": 40000, "current_a": 0.015, "extra": 0})):
            self.assert_rejected_unchanged(cap, "INVALID_ARGUMENT", **args)
        before = self.state()
        for t in (-1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.host.host_step(t)
            self.assertEqual(self.state(), before)
        self.host.host_step(2)
        with self.assertRaises(ValueError):
            self.host.host_step(1)
        self.ok("xrd.configure_scan", start_rad=0, stop_rad=math.radians(1), step_rad=math.radians(1), integration_time_s=1e-300)
        self.assert_rejected_unchanged("xrd.start_scan", "INVALID_ARGUMENT")

    def test_full_range_minimum_step_has_29601_finite_points(self):
        self.prepare()
        self.ok("xrd.configure_scan", start_rad=RIGAKU_MINIFLEX600_MIN_RAD, stop_rad=RIGAKU_MINIFLEX600_MAX_RAD,
            step_rad=RIGAKU_MINIFLEX600_STEP_RAD, integration_time_s=1)
        self.host.host_load_synthetic_response("mini_a", [{"two_theta_rad": RIGAKU_MINIFLEX600_MIN_RAD, "intensity_cps": 2},
            {"two_theta_rad": 0, "intensity_cps": 3}, {"two_theta_rad": RIGAKU_MINIFLEX600_MAX_RAD, "intensity_cps": 5}])
        self.ok("xrd.start_scan")
        self.host.host_step(29601)
        pattern = self.value("measurement.pattern")
        self.assertEqual(len(pattern), 29601)
        self.assertEqual(pattern[0]["two_theta_rad"], RIGAKU_MINIFLEX600_MIN_RAD)
        self.assertEqual(pattern[-1]["two_theta_rad"], RIGAKU_MINIFLEX600_MAX_RAD)
        self.assertEqual(pattern[0]["intensity_cps"], 2)
        self.assertEqual(pattern[-1]["intensity_cps"], 5)
        self.assertTrue(all(math.isfinite(p["intensity_cps"]) for p in pattern))
        self.assertTrue(all(a["two_theta_rad"] < b["two_theta_rad"] for a, b in zip(pattern, pattern[1:])))
        self.assertEqual(self.value("scan.status"), "completed")

    def test_host_response_validation_and_no_extrapolation(self):
        self.prepare()
        before = self.state()
        for data in ([], self.response()[:1], list(reversed(self.response())), [self.response()[0]] * 2,
                [{"two_theta_rad": 0, "intensity_cps": float("inf")}, self.response()[1]],
                [{"two_theta_rad": 0, "intensity_cps": -1}, self.response()[1]]):
            with self.assertRaises(ValueError):
                self.host.host_load_synthetic_response("mini_a", data)
            self.assertEqual(self.state(), before)
        for connection_id, ready in ((None, True), ("", False), ("chiller", 1)):
            with self.assertRaises(ValueError):
                self.host.host_set_cooling("mini_a", connection_id=connection_id, ready=ready)
            self.assertEqual(self.state(), before)
        narrow = [{"two_theta_rad": 0, "intensity_cps": 1}, {"two_theta_rad": math.radians(1), "intensity_cps": 2}]
        self.host.host_load_synthetic_response("mini_a", narrow)
        self.assert_rejected_unchanged("xrd.start_scan", "PRECONDITION_FAILED")
        huge = [{"two_theta_rad": 0, "intensity_cps": 1e308}, {"two_theta_rad": math.radians(2), "intensity_cps": 1e308}]
        self.host.host_load_synthetic_response("mini_a", huge)
        self.ok("xrd.configure_scan", start_rad=0, stop_rad=math.radians(2), step_rad=math.radians(1), integration_time_s=10)
        self.assert_rejected_unchanged("xrd.start_scan", "INVALID_ARGUMENT")

    def test_request_dedup_conflict_copy_isolation_and_scope(self):
        request = {"instance_id": "mini_a", "capability_id": "power.set", "arguments": {"enabled": True}, "request_id": "retry"}
        first = self.agent.invoke(**request)
        before, calls = self.state(), len(self.calls)
        self.assertEqual(self.agent.invoke(**request), first)
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.calls), calls)
        first["result"]["enabled"] = False
        self.assertTrue(self.agent.invoke(**request)["result"]["enabled"])
        request["arguments"] = {"enabled": False}
        self.assertEqual(self.agent.invoke(**request)["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(self.state(), before)
        response = self.agent.read(instance_id="mini_a")
        response["observations"]["power.enabled"]["value"] = False
        self.assertTrue(self.value("power.enabled"))
        self.assertEqual(self.agent.read(instance_id="mini_b")["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(self.agent.describe(instance_id="mini_b")["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(self.agent.invoke(instance_id="mini_b", capability_id="power.set", arguments={"enabled": True}, request_id="cross")["error"]["code"], "UNKNOWN_INSTANCE")

    def test_truth_filtering_identity_host_and_unsupported_analysis(self):
        self.prepare()
        self.ok("xrd.start_scan")
        self.host.host_step(1)
        for response in (self.agent.describe(instance_id="mini_a"), self.agent.read(instance_id="mini_a"), self.ok("xrd.read_pattern"), self.agent.logs(), self.host.scene_snapshot("mini_a")):
            text = json.dumps(response)
            self.assertNotIn('"truth.', text)
            self.assertNotIn('"response_configuration"', text)
        for sid in ("truth.response", "truth.sample_connection_id"):
            self.assertEqual(self.agent.read(instance_id="mini_a", state_ids=[sid])["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.value("truth.response"), self.response())
        for cap in ("xrd.phase_search", "xrd.refine"):
            response = self.assert_rejected_unchanged(cap, "UNSUPPORTED")
            self.assertEqual(response["status"], "unsupported")
            self.assertEqual(response["result"], {})
        self.assert_rejected_unchanged("sim.host_step", "UNKNOWN_CAPABILITY")
        self.assertFalse(hasattr(self.agent, "host_step"))
        self.assertFalse(hasattr(self.agent, "host_load_synthetic_response"))
        with self.assertRaises(ValueError):
            self.host.session("unregistered_evaluator")

    def test_scene_snapshot_mapping_and_failed_callback_transaction(self):
        self.prepare()
        self.ok("xrd.start_scan")
        self.host.host_step(1)
        snapshot = self.host.scene_snapshot("mini_a")
        self.assertEqual(set(snapshot), set(export_rigaku_miniflex600_contract_metadata()["scene_snapshot_keys"]))
        self.assertEqual(snapshot["goniometer.sample_theta_rad"], snapshot["goniometer.two_theta_rad"] / 2)
        self.assertEqual(self.host.apply_scene("mini_a"), snapshot)
        self.assertEqual(self.calls[-1], ("mini_a", snapshot))
        def failure(name, data):
            raise RuntimeError("private_scene_diagnostic")
        self.host.scene_adapter = failure
        before, other, clock = self.state(), self.state("mini_b"), self.host.time_s
        with self.assertRaises(RuntimeError):
            self.host.host_step(5)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.state("mini_b"), other)
        self.assertEqual(self.host.time_s, clock)
        response = self.invoke("power.set", enabled=False)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["code"], "DEVICE_ERROR")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("private_scene_diagnostic", json.dumps(response))
        self.assertEqual(self.state(), before)
        self.host.scene_adapter = None
        with self.assertRaises(RuntimeError):
            self.host.apply_scene("mini_a")

    def test_write_routes_and_restricted_slots(self):
        response = self.agent.write(instance_id="mini_a", state_id="power.enabled", value=True, request_id="write_power")
        self.assertEqual(response["status"], "succeeded")
        response = self.agent.write(instance_id="mini_a", state_id="goniometer.two_theta_rad", value=math.radians(30), request_id="write_angle")
        self.assertEqual(response["status"], "succeeded")
        self.assertAlmostEqual(self.value("goniometer.sample_theta_rad"), math.radians(15))
        for sid in ("truth.response", "cooling.ready", "xray.current_a", "scan.running", "measurement.pattern", "missing"):
            self.assertEqual(self.agent.write(instance_id="mini_a", state_id=sid, value=True, request_id="write_" + sid)["error"]["code"], "ACCESS_DENIED")

    def test_all_metadata_schemas_capabilities_and_declared_effects(self):
        metadata = export_rigaku_miniflex600_contract_metadata()["asset_types"][RIGAKU_MINIFLEX600_TYPE]
        for name, cap in metadata["capabilities"].items():
            self.assertEqual(lint_schema(cap["input_schema"]), [], name)
            self.assertEqual(lint_schema(cap["output_schema"]), [], name)
            for example in cap["example_arguments"]:
                self.assertEqual(validate_value(example, cap["input_schema"]), [], name)
        checked = set()
        def verify(name, **args):
            before = self.host._values(self.state())
            response = self.ok(name, **args)
            after = self.host._values(self.state())
            changed = {s for s in after if after[s] != before[s]}
            declared = {e["state_id"] for e in metadata["capabilities"][name]["state_effects"]}
            self.assertLessEqual(changed, declared, (name, changed - declared))
            self.assertEqual(validate_value(response["result"], metadata["capabilities"][name]["output_schema"]), [], name)
            checked.add(name)
        verify("power.set", enabled=True)
        verify("access.set_door_angle", angle_rad=1)
        verify("slot.set_sample", connection_id="fixture")
        verify("access.set_door_angle", angle_rad=0)
        verify("xray.configure", voltage_v=40000, current_a=0.015)
        verify("xrd.set_slit", slit_id="fixture")
        verify("xrd.configure_scan", start_rad=0, stop_rad=math.radians(2), step_rad=math.radians(0.5), integration_time_s=1)
        verify("motion.set_two_theta", angle_rad=math.radians(1))
        self.host.host_set_cooling("mini_a", connection_id="external", ready=True)
        self.host.host_load_synthetic_response("mini_a", self.response())
        verify("xrd.start_scan")
        self.host.host_step(5)
        verify("xrd.read_pattern")
        verify("xrd.stop_scan")
        self.assertEqual(checked, {k for k, d in metadata["capabilities"].items() if d["availability"] == "implemented"})
        values = self.host._values(self.state())
        self.assertEqual(set(values), set(metadata["state_slots"]))
        for sid, value in values.items():
            self.assertEqual(lint_schema(metadata["state_slots"][sid]["value_schema"]), [], sid)
            self.assertEqual(validate_value(value, metadata["state_slots"][sid]["value_schema"]), [], sid)
        for key, param in metadata["parameters"].items():
            if param["evidence"] == "unknown":
                self.assertIsNone(param["value"], key)
# END CHEM-160 RIGAKU MINIFLEX600 APPEND-ONLY TESTS

# CHEM-102 independent review regression cases, appended after other assets.
def _kf899_review_test_failed_callback_request_does_not_replay(self):
    self.prepare()
    attempts = []
    def fail_after_scene_effect(iid, updates):
        attempts.append((iid, updates))
        raise RuntimeError("private host detail")
    self.host.scene_adapter = fail_after_scene_effect
    args = {"instance_id": "kf_a", "capability_id": "power.set", "arguments": {"enabled": False}, "request_id": "callback_failure"}
    before = self.host.debug_snapshot()
    first = self.agent.invoke(**args)
    self.assertEqual(first["error"]["code"], "DEVICE_ERROR")
    self.assertEqual(first["error"]["side_effects"], "unknown")
    self.assertEqual(self.agent.invoke(**args), first)
    self.assertEqual(len(attempts), 1)
    self.assertEqual(before, self.host.debug_snapshot())
    args["arguments"] = {"enabled": True}
    self.assertEqual(self.agent.invoke(**args)["error"]["code"], "REQUEST_CONFLICT")
    self.assertEqual(len(attempts), 1)


def _kf899_review_test_unrepresentable_clock_increments(self):
    self.host.step(1e20)
    before, calls = self.host.debug_snapshot(), len(self.calls)
    with self.assertRaisesRegex(ValueError, "representable"):
        self.host.step(1)
    self.assertEqual(before, self.host.debug_snapshot())
    self.assertEqual(calls, len(self.calls))
    self.prepare()
    self.ok("coulometry.start")
    before, calls = self.host.debug_snapshot(), len(self.calls)
    # World step is representable, but the analytically resolved endpoint is not.
    with self.assertRaisesRegex(ValueError, "representable"):
        self.host.step(20000)
    self.assertEqual(before, self.host.debug_snapshot())
    self.assertEqual(calls, len(self.calls))


def _kf899_review_test_null_drift_preconditions(self):
    before = self.host.debug_snapshot()
    with self.assertRaises(ValueError):
        self.host.load_sample(instance_id="kf_a", water_mass_kg=1e-6, background_drift_kg_s=None, reagent_ready=True, request_id="null_drift")
    self.assertEqual(before, self.host.debug_snapshot())
    self.prepare()
    self.ok("coulometry.configure", drift_compensation_kg_s=None)
    before = self.host.debug_snapshot()
    self.assertEqual(self.invoke("coulometry.start")["error"]["code"], "PRECONDITION_FAILED")
    self.assertEqual(before, self.host.debug_snapshot())
    self.ok("coulometry.configure", drift_compensation_kg_s=0)
    self.ok("coulometry.start")
    before = self.host.debug_snapshot()
    self.assertEqual(self.invoke("coulometry.configure", drift_compensation_kg_s=None)["error"]["code"], "BUSY")
    self.assertEqual(before, self.host.debug_snapshot())


def _kf899_review_test_two_running_instances_shared_step(self):
    self.prepare(water=1e-6, current=0.1)
    self.ok("coulometry.start")
    for n, (cap, args) in enumerate([
        ("slot.set_installed", {"slot": "cell", "installed": True}),
        ("slot.set_installed", {"slot": "generator", "installed": True}),
        ("slot.set_installed", {"slot": "indicator", "installed": True}),
        ("coulometry.configure", {"current_a": 0.4, "endpoint_hold_s": 1, "drift_compensation_kg_s": 0}),
        ("power.set", {"enabled": True})]):
        self.assertEqual(self.judge.invoke(instance_id="kf_b", capability_id=cap, arguments=args, request_id=f"b{n}")["status"], "succeeded")
    self.host.load_sample(instance_id="kf_b", water_mass_kg=2e-6, background_drift_kg_s=0, reagent_ready=True, request_id="sample_b")
    self.assertEqual(self.judge.invoke(instance_id="kf_b", capability_id="coulometry.start", arguments={}, request_id="bstart")["status"], "succeeded")
    self.host.step(2)
    self.assertEqual(self.value("measurement.charge_c"), 0.2)
    self.assertEqual(self.value("measurement.charge_c", "kf_b"), 0.8)
    for iid in ("kf_a", "kf_b"):
        self.assertEqual(self.value("measurement.sample_time_s", iid), 2)
        self.assertEqual(self.judge.read(instance_id=iid)["clock"]["time_s"], 2)
    # Per-instance callbacks can change one external scene before another fails.
    external_changes = []
    def second_fails(iid, updates):
        if iid == "kf_b":
            raise RuntimeError("second scene failed")
        external_changes.append(iid)
    self.host.scene_adapter = second_fails
    before = self.host.debug_snapshot()
    with self.assertRaises(RuntimeError):
        self.host.step(1)
    self.assertEqual(self.host.debug_snapshot(), before)
    self.assertEqual(external_changes, ["kf_a"])
    self.assertTrue(any("not transactional" in s for s in export_metrohm899_contract_metadata()["boundaries"]))


def _kf899_review_test_curve_copies_and_observed_fields_only(self):
    self.prepare(water=0.00123456789)
    self.ok("coulometry.start")
    self.host.step(1)
    first = self.ok("coulometry.curve")["result"]["records"]
    self.assertEqual(set(first[0]), {"status", "running", "elapsed_s", "charge_c", "water_mass_kg", "water_fraction", "drift_kg_s", "synthetic", "sample_time_s"})
    self.assertNotIn("0.00123456789", json.dumps(first))
    first[0]["water_mass_kg"] = 999
    self.assertNotEqual(self.ok("coulometry.curve")["result"]["records"][0]["water_mass_kg"], 999)


Metrohm899RuntimeTests.test_failed_callback_request_does_not_replay = _kf899_review_test_failed_callback_request_does_not_replay
Metrohm899RuntimeTests.test_unrepresentable_clock_increments = _kf899_review_test_unrepresentable_clock_increments
Metrohm899RuntimeTests.test_null_drift_preconditions = _kf899_review_test_null_drift_preconditions
Metrohm899RuntimeTests.test_two_running_instances_shared_step = _kf899_review_test_two_running_instances_shared_step
Metrohm899RuntimeTests.test_curve_copies_and_observed_fields_only = _kf899_review_test_curve_copies_and_observed_fields_only



# CHEM-103 clock regressions appended after independent numerical review.
class GamryReference620ClockRegressionTests(unittest.TestCase):
    def prepared(self, *, origin_steps=(), interval=0.1, values=None):
        host = GamryReference620Runtime()
        host.create_instance("clock_fixture")
        for dt in origin_steps:
            host.step(dt)
        self.counter = 0
        def invoke(capability, arguments=None):
            self.counter += 1
            return host.invoke(instance_id="clock_fixture", capability_id=capability,
                arguments={} if arguments is None else arguments, request_id="clock_" + str(self.counter))
        self.assertEqual(invoke("power.set", {"enabled": True})["status"], "succeeded")
        for port in GAMRY_REFERENCE620_PORTS:
            self.assertEqual(invoke("slot.set_connection", {"port_id": port, "connection_id": port})["status"], "succeeded")
        program = {"mode": "potential", "sample_interval_s": interval, "values": [0, 1, 2, 3] if values is None else values}
        self.assertEqual(invoke("electrochem.configure_waveform", {"program": program})["status"], "succeeded")
        return host, invoke

    def series(self, host):
        return host.read(instance_id="clock_fixture", state_ids=["acquisition.series"])["observations"]["acquisition.series"]["value"]

    def test_decimal_host_partitions_have_identical_final_timestamps_and_status(self):
        outcomes = []
        for partitions in ((0.3,), (0.1, 0.2), (0.2, 0.1), (0.1, 0.1, 0.1), (0.15, 0.15)):
            host, invoke = self.prepared()
            self.assertEqual(invoke("electrochem.start")["status"], "succeeded")
            for dt in partitions:
                host.step(dt)
            values = host.debug_snapshot()["instances"]["clock_fixture"]["state"]
            outcomes.append((host.time_s, values["waveform.status"], values["waveform.sample_count"],
                             values["waveform.elapsed_s"], self.series(host)))
        self.assertTrue(all(outcome == outcomes[0] for outcome in outcomes))
        self.assertEqual(outcomes[0][:4], (0.3, "completed", 4, 0.3))
        self.assertEqual([sample["time_s"] for sample in outcomes[0][4]], [0.0, 0.1, 0.2, 0.3])

    def test_near_future_point_is_not_emitted_early_by_boundary_tolerance(self):
        host, invoke = self.prepared()
        self.assertEqual(invoke("electrochem.start")["status"], "succeeded")
        host.step(0.29999999999999)
        self.assertEqual(len(self.series(host)), 3)
        host.step(1e-14)
        self.assertEqual(len(self.series(host)), 4)
        self.assertEqual(host.time_s, 0.3)
        self.assertTrue(all(sample["time_s"] <= host.time_s for sample in self.series(host)))

    def test_late_decimal_origin_and_partitioned_origin_share_the_same_clock(self):
        outcomes = []
        for origin, partitions in (((12.4,), (0.3,)), ((12.0, 0.4), (0.1, 0.2))):
            host, invoke = self.prepared(origin_steps=origin)
            self.assertEqual(invoke("electrochem.start")["status"], "succeeded")
            for dt in partitions:
                host.step(dt)
            outcomes.append((host.time_s, self.series(host)))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0], 12.7)
        self.assertEqual([sample["time_s"] for sample in outcomes[0][1]], [12.4, 12.5, 12.6, 12.7])

    def test_whole_plan_rejects_later_duplicate_timestamp_atomically(self):
        host, invoke = self.prepared(origin_steps=(1e16,), interval=1.1)
        self.assertGreater(1e16 + 1.1, 1e16)  # First increment alone falsely appears resolvable.
        before = host.debug_snapshot()
        private_before = copy.deepcopy(host._instances)
        clock_before = host._time_fraction
        result = invoke("electrochem.start")
        self.assertEqual(result["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(result["error"]["side_effects"], "none")
        self.assertEqual(before, host.debug_snapshot())
        self.assertEqual(private_before, host._instances)
        self.assertEqual(clock_before, host._time_fraction)

    def test_late_resolvable_plan_has_strictly_increasing_timestamps(self):
        host, invoke = self.prepared(origin_steps=(1e16,), interval=2.0)
        self.assertEqual(invoke("electrochem.start")["status"], "succeeded")
        host.step(6)
        stamps = [sample["time_s"] for sample in self.series(host)]
        self.assertEqual(stamps, [1e16, 1e16 + 2, 1e16 + 4, 1e16 + 6])
        self.assertTrue(all(b > a for a, b in zip(stamps, stamps[1:])))
        self.assertTrue(all(stamp <= host.time_s for stamp in stamps))

    def test_single_point_plan_has_no_spurious_interpoint_precision_requirement(self):
        for origin in (0.0, 1e16):
            host, invoke = self.prepared(origin_steps=(origin,), interval=0.1, values=[-11])
            self.assertEqual(invoke("electrochem.start")["status"], "succeeded")
            before = host.debug_snapshot()
            host.step(0)
            self.assertEqual(before, host.debug_snapshot())
            host.step(2)
            samples = self.series(host)
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]["time_s"], origin)
            self.assertEqual(samples[0]["elapsed_s"], 0)
            self.assertTrue(samples[0]["synthetic"])

    def test_unrepresentable_end_rejects_before_start_and_host_failure_is_atomic(self):
        host, invoke = self.prepared(origin_steps=(1e308,), interval=1e308, values=[0, 1])
        before, fraction_before = host.debug_snapshot(), host._time_fraction
        self.assertEqual(invoke("electrochem.start")["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(before, host.debug_snapshot())
        with self.assertRaises(ValueError):
            host.step(1e308)
        self.assertEqual(before, host.debug_snapshot())
        self.assertEqual(fraction_before, host._time_fraction)
