"""CHEM-071 runtime verification; all samples/results are synthetic fixtures."""
import copy as _dr6000_test_copy
import json as _dr6000_test_json
import math as _dr6000_test_math
import sys as _dr6000_test_sys
import unittest as _dr6000_test_unittest
from pathlib import Path as _Dr6000TestPath

_dr6000_test_sys.path.insert(0, str(_Dr6000TestPath(__file__).resolve().parents[1] / "scripts"))
from basic40_runtime_analytical import HachDR6000, HACH_DR6000_LID_MAX_RAD, export_hach_dr6000_contract_metadata


class TestHachDR6000Runtime(_dr6000_test_unittest.TestCase):
    def setUp(self):
        self.scene_calls = []
        self.time_s = 17.0
        self.device = HachDR6000("dr6000_a", scene_adapter=self.scene, clock=lambda: self.time_s)
        self.device.register_sample("synthetic_sample", truth={"private_species": "unexposed_secret_marker", "concentration_mol_m3": 17.3})
        self.device.register_sample("synthetic_blank", kind="blank")
        self.device.register_principal("trusted_judge", visibility="evaluator")
        self.judge = self.device.session("trusted_judge")
        self.request_counter = 0

    def scene(self, instance_id, updates):
        self.scene_calls.append((instance_id, _dr6000_test_copy.deepcopy(updates)))

    def call(self, capability_id, **arguments):
        self.request_counter += 1
        return self.device.invoke(instance_id="dr6000_a", capability_id=capability_id, arguments=arguments, request_id="dr6000_test_" + str(self.request_counter))

    def ok(self, capability_id, **arguments):
        response = self.call(capability_id, **arguments)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, state_id):
        return self.device.read(state_ids=[state_id])["observations"][state_id]["value"]

    def ready(self, sample="synthetic_sample"):
        self.ok("power.set", enabled=True)
        self.ok("lid.set_angle", angle_rad=HACH_DR6000_LID_MAX_RAD)
        self.ok("sample.load", sample_id=sample)
        self.ok("lid.set_angle", angle_rad=0.0)

    def assert_rejected_unchanged(self, capability_id, arguments, expected="rejected"):
        before = self.device.debug_snapshot()
        before_scene = _dr6000_test_copy.deepcopy(self.scene_calls)
        result = self.call(capability_id, **arguments)
        self.assertEqual(result["status"], expected, result)
        self.assertEqual(result["error"]["side_effects"], "none")
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(before_scene, self.scene_calls)
        return result

    def test_absorbance_scan_is_synthetic_and_si(self):
        self.ready()
        self.ok("spectroscopy.set_scan", start_m=190e-9, stop_m=1100e-9, step_m=10e-9)
        spectrum = self.ok("spectroscopy.measure")["result"]["spectrum"]
        self.assertTrue(spectrum["synthetic"])
        self.assertEqual(spectrum["quality"], "estimated")
        self.assertEqual(spectrum["axis_unit"], "m")
        self.assertEqual(spectrum["signal_unit"], "1")
        self.assertIsNone(spectrum["measurement_duration_s"])
        self.assertEqual(len(spectrum["wavelength_m"]), 92)
        self.assertEqual(spectrum["wavelength_m"][0], 190e-9)
        self.assertEqual(spectrum["wavelength_m"][-1], 1100e-9)
        self.assertEqual(spectrum["signal"], spectrum["absorbance"])
        self.assertEqual(self.value("measurement.count"), 1)
        self.assertEqual(self.value("measurement.spectrum"), spectrum)
        self.assertTrue(all(_dr6000_test_math.isfinite(x) for x in spectrum["absorbance"]))

    def test_transmittance_is_consistent_with_absorbance(self):
        self.ready()
        self.ok("spectroscopy.set_mode", mode="transmittance")
        spectrum = self.ok("spectroscopy.measure")["result"]["spectrum"]
        self.assertEqual(spectrum["reported_mode"], "transmittance")
        self.assertEqual(spectrum["signal"], spectrum["transmittance"])
        for absorbance, transmittance in zip(spectrum["absorbance"], spectrum["transmittance"]):
            self.assertAlmostEqual(transmittance, 10 ** -absorbance, places=14)

    def test_single_wavelength_routes_to_one_point_acquisition(self):
        self.ready()
        self.ok("spectroscopy.set_wavelength", wavelength_m=400e-9)
        result = self.ok("spectroscopy.measure")["result"]["spectrum"]
        self.assertEqual(result["wavelength_m"], [400e-9])
        self.assertEqual(self.value("scan.start_m"), self.value("scan.stop_m"))

    def test_sample_and_blank_flow_includes_load_close_measure_unload(self):
        self.ready(sample="synthetic_blank")
        spectrum = self.ok("spectroscopy.measure")["result"]["spectrum"]
        self.assertEqual(spectrum["sample_kind"], "blank")
        self.assertTrue(all(value == 0 for value in spectrum["absorbance"]))
        self.assertTrue(all(value == 1 for value in spectrum["transmittance"]))
        self.ok("lid.set_angle", angle_rad=HACH_DR6000_LID_MAX_RAD)
        self.ok("sample.unload")
        self.assertFalse(self.value("sample.loaded"))
        self.assertIsNone(self.value("sample.id"))
        self.assertFalse(self.scene_calls[-1][1]["sample.loaded"])

    def test_measure_requires_power_closed_lid_and_sample(self):
        self.assert_rejected_unchanged("spectroscopy.measure", {})
        self.ok("power.set", enabled=True)
        self.assert_rejected_unchanged("spectroscopy.measure", {})
        self.ok("lid.set_angle", angle_rad=HACH_DR6000_LID_MAX_RAD)
        self.ok("sample.load", sample_id="synthetic_sample")
        self.assert_rejected_unchanged("spectroscopy.measure", {})
        self.ok("lid.set_angle", angle_rad=0)
        self.ok("spectroscopy.measure")
        self.ok("power.set", enabled=False)
        self.assert_rejected_unchanged("spectroscopy.measure", {})

    def test_loading_requires_clearance_registered_sample_and_empty_slot(self):
        self.assert_rejected_unchanged("sample.load", {"sample_id": "synthetic_sample"})
        self.ok("lid.set_angle", angle_rad=_dr6000_test_math.pi / 4)
        self.assert_rejected_unchanged("sample.load", {"sample_id": "synthetic_sample"})
        self.ok("lid.set_angle", angle_rad=HACH_DR6000_LID_MAX_RAD)
        self.assert_rejected_unchanged("sample.load", {"sample_id": "unknown_sample"})
        self.assert_rejected_unchanged("sample.unload", {})
        self.ok("sample.load", sample_id="synthetic_sample")
        self.assert_rejected_unchanged("sample.load", {"sample_id": "synthetic_blank"})
        self.ok("lid.set_angle", angle_rad=0)
        self.assert_rejected_unchanged("sample.unload", {})

    def test_si_ranges_invalid_numbers_and_extra_arguments_are_atomic(self):
        self.ok("power.set", enabled=True)
        cases = [("power.set", {"enabled": 1}), ("lid.set_angle", {"angle_rad": True}),
            ("lid.set_angle", {"angle_rad": -0.1}), ("lid.set_angle", {"angle_rad": _dr6000_test_math.inf}),
            ("lid.set_angle", {"angle_rad": _dr6000_test_math.nan}),
            ("spectroscopy.set_wavelength", {"wavelength_m": 500}),
            ("spectroscopy.set_wavelength", {"wavelength_m": 189e-9}),
            ("spectroscopy.set_wavelength", {"wavelength_m": 1101e-9}),
            ("spectroscopy.set_wavelength", {"wavelength_m": 10 ** 400}),
            ("spectroscopy.set_scan", {"start_m": 700e-9, "stop_m": 200e-9, "step_m": 10e-9}),
            ("spectroscopy.set_scan", {"start_m": 190e-9, "stop_m": 1100e-9, "step_m": 0}),
            ("spectroscopy.set_mode", {"mode": "unknown"}),
            ("spectroscopy.measure", {"role": "evaluator"}),
            ("sample.load", {"sample_id": ""})]
        for capability, arguments in cases:
            with self.subTest(capability=capability, arguments=arguments):
                self.assert_rejected_unchanged(capability, arguments)

    def test_numeric_range_endpoints_and_nondivisible_scan(self):
        self.ready()
        for wavelength in (190e-9, 1100e-9):
            self.ok("spectroscopy.set_wavelength", wavelength_m=wavelength)
            self.assertEqual(self.ok("spectroscopy.measure")["result"]["spectrum"]["wavelength_m"], [wavelength])
        self.ok("spectroscopy.set_scan", start_m=200e-9, stop_m=211e-9, step_m=4e-9)
        axis = self.ok("spectroscopy.measure")["result"]["spectrum"]["wavelength_m"]
        self.assertEqual(len(axis), 3)
        self.assertLessEqual(axis[-1], 211e-9)

    def test_unsupported_modes_calibration_robot_and_hardware_never_succeed(self):
        self.ok("power.set", enabled=True)
        for capability in ("spectroscopy.calibrate", "spectroscopy.configure_method", "spectroscopy.time_scan", "hardware.connect", "robot.load_sample"):
            self.assert_rejected_unchanged(capability, {}, expected="unsupported")
        self.assert_rejected_unchanged("spectroscopy.set_mode", {"mode": "concentration"}, expected="unsupported")

    def test_missing_scene_adapter_does_not_claim_mechanical_success(self):
        device = HachDR6000("unbound")
        before = device.debug_snapshot()
        result = device.invoke(capability_id="lid.set_angle", arguments={"angle_rad": 1}, request_id="no_scene")
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(before, device.debug_snapshot())
        with self.assertRaises(RuntimeError):
            device.sync_scene()

    def test_scene_callback_receives_correct_public_state_and_instance(self):
        self.ready()
        self.ok("spectroscopy.measure")
        self.assertTrue(all(instance == "dr6000_a" for instance, _ in self.scene_calls))
        self.assertTrue(any(update["lid.angle_rad"] == HACH_DR6000_LID_MAX_RAD for _, update in self.scene_calls))
        self.assertTrue(self.scene_calls[-1][1]["sample.loaded"])
        self.assertTrue(self.scene_calls[-1][1]["measurement.spectrum"]["synthetic"])
        self.assertNotIn("sample.truth", _dr6000_test_json.dumps(self.scene_calls))
        self.device.sync_scene()
        self.assertEqual(self.scene_calls[-1], self.scene_calls[-2])

    def test_scene_failure_does_not_commit_process_or_leak_exception(self):
        def broken_scene(instance, updates):
            raise RuntimeError("unexposed_secret_marker")
        device = HachDR6000("broken", scene_adapter=broken_scene)
        before = device.debug_snapshot()
        response = device.invoke(capability_id="lid.set_angle", arguments={"angle_rad": 1}, request_id="failed_scene")
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertEqual(before, device.debug_snapshot())
        self.assertNotIn("unexposed_secret_marker", _dr6000_test_json.dumps(response))

    def test_agent_truth_filter_covers_describe_read_invoke_errors_and_scene(self):
        self.ready()
        outputs = [self.device.describe(), self.device.read(), self.ok("spectroscopy.measure"), self.scene_calls]
        denied = self.device.read(state_ids=["sample.truth"])
        unknown = self.device.read(state_ids=["nonexistent.truth"])
        self.assertEqual(denied["error"], unknown["error"])
        outputs += [denied, unknown, self.call("spectroscopy.measure", role="evaluator")]
        serialized = _dr6000_test_json.dumps(outputs)
        for secret in ("unexposed_secret_marker", "private_species", "concentration_mol_m3", "sample.truth"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(self.judge.read(state_ids=["sample.truth"])["observations"]["sample.truth"]["value"]["private_species"], "unexposed_secret_marker")
        with self.assertRaises(TypeError):
            self.device.read(state_ids=["sample.truth"], role="evaluator")
        with self.assertRaises(ValueError):
            self.device.session("unregistered")

    def test_public_mutable_results_are_copies(self):
        self.ready()
        measured = self.ok("spectroscopy.measure")
        measured["result"]["spectrum"]["absorbance"][0] = 999
        read = self.device.read()
        read["observations"]["measurement.spectrum"]["value"]["absorbance"][0] = 999
        describe = self.device.describe()
        describe["capabilities"]["power.set"]["availability"] = "unsupported"
        self.assertNotEqual(self.value("measurement.spectrum")["absorbance"][0], 999)
        self.assertEqual(self.device.describe()["capabilities"]["power.set"]["availability"], "implemented")

    def test_read_describe_do_not_advance_host_clock_or_change_state(self):
        self.ready()
        before = self.device.debug_snapshot()
        before_scene = _dr6000_test_copy.deepcopy(self.scene_calls)
        for _ in range(3):
            self.assertEqual(self.device.read()["clock"]["time_s"], 17)
            self.device.describe()
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(before_scene, self.scene_calls)
        self.time_s = 33.0
        self.assertEqual(self.device.read()["clock"]["time_s"], 33)

    def test_double_instances_keep_sample_lid_spectrum_requests_isolated(self):
        other_calls = []
        other = HachDR6000("dr6000_b", scene_adapter=lambda instance, updates: other_calls.append((instance, updates)))
        self.ready()
        self.ok("spectroscopy.measure")
        self.assertEqual(other.debug_snapshot()["state"]["measurement.count"], 0)
        self.assertFalse(other.debug_snapshot()["state"]["sample.loaded"])
        self.assertFalse(other.debug_snapshot()["state"]["power.enabled"])
        self.assertEqual(other_calls, [])
        bad = self.device.invoke(instance_id="dr6000_b", capability_id="power.set", arguments={"enabled": True}, request_id="cross_instance")
        self.assertEqual(bad["error"]["code"], "UNKNOWN_INSTANCE")

    def test_successful_request_replay_conflict_and_write_routing(self):
        kwargs = {"capability_id": "power.set", "arguments": {"enabled": True}, "request_id": "fixed_request"}
        original = self.device.invoke(**kwargs)
        before = self.device.debug_snapshot()
        scene_count = len(self.scene_calls)
        self.assertEqual(self.device.invoke(**kwargs), original)
        self.assertEqual(len(self.scene_calls), scene_count)
        conflict = self.device.invoke(**{**kwargs, "arguments": {"enabled": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.device.debug_snapshot())
        written = self.device.write(state_id="measurement.wavelength_m", value=600e-9, request_id="write_wavelength")
        invoked = self.device.invoke(capability_id="spectroscopy.set_wavelength", arguments={"wavelength_m": 600e-9}, request_id="write_wavelength")
        self.assertEqual(written, invoked)
        denied = self.judge.write(state_id="sample.truth", value={"fake": True}, request_id="write_truth")
        self.assertEqual(denied["error"]["code"], "ACCESS_DENIED")
        denied = self.device.write(state_id="scan.start_m", value=600e-9, request_id="write_scan_fragment")
        self.assertEqual(denied["error"]["code"], "ACCESS_DENIED")

    def test_malformed_identifiers_are_rejected_without_exceptions_or_mutation(self):
        before = self.device.debug_snapshot()
        for malformed in ([], {}, True, 0):
            self.assertEqual(self.device.read(instance_id=malformed)["status"], "rejected")
            self.assertEqual(self.device.describe(instance_id=malformed)["status"], "rejected")
            self.assertEqual(self.device.invoke(instance_id=malformed, capability_id="power.set", arguments={"enabled": True}, request_id="bad")["status"], "rejected")
            self.assertEqual(self.device.invoke(capability_id=malformed, arguments={}, request_id="bad")["status"], "rejected")
            self.assertEqual(self.device.invoke(capability_id="power.set", arguments={"enabled": True}, request_id=malformed)["status"], "rejected")
        self.assertEqual(before, self.device.debug_snapshot())

    def test_unknown_real_quantities_are_null_and_conditions_are_preserved(self):
        metadata = export_hach_dr6000_contract_metadata()
        parameters = metadata["parameters"]
        self.assertIsNone(parameters["measurement_duration_s"]["value"])
        self.assertIsNone(parameters["cuvette_path_length_m"]["value"])
        self.assertIn("200e-9..900e-9", parameters["wavelength_accuracy_m"]["conditions"])
        self.assertIn("200e-9..900e-9", parameters["photometric_range_absorbance"]["conditions"])
        self.assertEqual(parameters["synthetic_gaussian_peaks"]["evidence"], "assumption")

    def test_registration_rejects_invalid_fixture_without_side_effects(self):
        before = self.device.debug_snapshot()
        for fields in ({"peaks": [[500e-9, 0, 1]]}, {"baseline_absorbance": float("nan")}, {"kind": "gas"}, {"truth": "bad"}):
            with self.assertRaises(ValueError):
                self.device.register_sample("invalid", **fields)
            self.assertEqual(before, self.device.debug_snapshot())

    def test_preparation_changes_invalidate_spectrum_without_resetting_count(self):
        self.ready()
        self.ok("spectroscopy.measure")
        self.ok("spectroscopy.set_mode", mode="transmittance")
        self.assertIsNone(self.value("measurement.spectrum"))
        self.assertEqual(self.value("measurement.count"), 1)
        self.ok("spectroscopy.measure")
        self.ok("lid.set_angle", angle_rad=HACH_DR6000_LID_MAX_RAD)
        self.assertIsNone(self.value("measurement.spectrum"))
        self.assertEqual(self.value("measurement.count"), 2)

    def test_rejected_request_replay_retains_original_result_and_conflicts(self):
        request = {"capability_id": "spectroscopy.measure", "arguments": {}, "request_id": "rejected_once"}
        rejected = self.device.invoke(**request)
        self.assertEqual(rejected["status"], "rejected")
        self.ready()
        before = self.device.debug_snapshot()
        self.assertEqual(self.device.invoke(**request), rejected)
        conflict = self.device.invoke(**{**request, "capability_id": "power.set", "arguments": {"enabled": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.device.debug_snapshot())

    def test_discovery_hides_unavailable_capabilities_and_truth(self):
        bound = self.device.describe()
        self.assertNotIn("hardware.connect", bound["capabilities"])
        self.assertNotIn("sample.truth", bound["state_slots"])
        self.assertIn("lid.set_angle", bound["capabilities"])
        unbound = HachDR6000("unbound").describe()
        self.assertNotIn("lid.set_angle", unbound["capabilities"])
        self.assertNotIn("sample.load", unbound["capabilities"])


if __name__ == "__main__":
    _dr6000_test_unittest.main()


# CHEM-096 GC8890B appended regression and public-contract verification.
import unittest as _gc8890b_unittest
import json as _gc8890b_test_json
import copy as _gc8890b_test_copy
import math as _gc8890b_test_math
from scripts.basic40_runtime_analytical import (
    AgilentGC8890B, GC8890BProcess, GC8890BLegacySession,
    GC8890B_LEGACY_API_DEFINITIONS, GC8890B_ZONE_MAX_K,
    export_agilent_gc8890b_contract_metadata,
)
from scripts.gc_runtime import Method as _gc8890b_test_Method


class TestGC8890BProcess(_gc8890b_unittest.TestCase):
    def powered(self):
        p = GC8890BProcess()
        p.perform("plug_power", connected=True)
        p.perform("power", enabled=True)
        return p

    def test_review_b_closed_door_cooling_is_four_to_five_minutes(self):
        p = self.powered()
        p.zones["oven"].temperature_K = 723.15
        p.perform("set_temperature", zone="oven", temperature_K=323.15)
        self.assertTrue(p.zones["oven"].cooling_damper_open)
        self.assertEqual(p.zones["oven"].heat_capacity_J_K, 1000)
        temperatures = []
        while p.zones["oven"].temperature_K > 323.15 and p.time_s < 600:
            p.step(0.5)
            temperatures.append(p.zones["oven"].temperature_K)
            self.assertFalse(p.door_open)
            self.assertEqual(p.zones["oven"].heater_power_W, 0)
        self.assertGreaterEqual(p.time_s, 240)
        self.assertLessEqual(p.time_s, 300)
        self.assertAlmostEqual(p.time_s, 270, places=7)
        self.assertTrue(all(b < a for a, b in zip(temperatures, temperatures[1:])))

    def test_heating_never_exceeds_135_kelvin_per_minute(self):
        p = self.powered()
        p.zones["oven"].temperature_K = 323.15
        p.perform("set_temperature", zone="oven", temperature_K=723.15)
        initial = p.zones["oven"].temperature_K
        rates = []
        for _ in range(450):
            old = p.zones["oven"].temperature_K
            p.step(1)
            rates.append((p.zones["oven"].temperature_K - old) * 60)
        self.assertGreater(max(rates), 130)
        self.assertLessEqual(max(rates), 135 + 1e-9)
        self.assertGreater(p.zones["oven"].temperature_K, initial)

    def test_review_c_zone_limits_and_constructor_cannot_bypass(self):
        p = self.powered()
        for zone, maximum in GC8890B_ZONE_MAX_K.items():
            p.perform("set_temperature", zone=zone, temperature_K=maximum)
            self.assertEqual(p.zones[zone].setpoint_K, maximum)
            for invalid in (maximum + 0.01, 273.15, True, "400", float("nan"), float("inf")):
                before = _gc8890b_test_copy.deepcopy(p.__dict__)
                with self.assertRaises(ValueError):
                    p.perform("set_temperature", zone=zone, temperature_K=invalid)
                self.assertEqual(p.__dict__, before)
            with self.assertRaises(ValueError):
                GC8890BProcess(method=_gc8890b_test_Method(**{zone + "_temperature_K": maximum + 0.01}))
        p.perform("set_temperature", zone="oven", temperature_K=302.15)
        with self.assertRaises(ValueError):
            p.perform("set_temperature", zone="oven", temperature_K=302.14)

    def test_actual_temperature_moves_only_with_host_step(self):
        p = self.powered()
        before = p.zones["oven"].temperature_K
        p.perform("set_temperature", zone="oven", temperature_K=573.15)
        self.assertEqual(p.zones["oven"].temperature_K, before)
        for _ in range(3):
            p.observe()
        self.assertEqual(p.time_s, 0)
        p.step(1)
        self.assertGreater(p.zones["oven"].temperature_K, before)
        for invalid in (False, "1", 0, -1, float("nan")):
            previous = p.observe()
            with self.assertRaises(ValueError):
                p.step(invalid)
            self.assertEqual(p.observe(), previous)

    def test_inherited_mass_transport_and_fid_charge_conserve(self):
        p = self.powered()
        for gas in p.gases:
            p.perform("connect_gas", gas=gas, connected=True)
        p.step(1200)
        p.perform("ignite")
        self.assertEqual(p.not_ready_reasons(), [])
        p.perform("start")
        total = {s: p.vial[s] + p.syringe[s] for s in p.species}
        p.perform("aspirate", fraction=0.01)
        self.assertEqual(total, {s: p.vial[s] + p.syringe[s] for s in p.species})
        p.perform("insert_syringe")
        p.perform("depress_plunger", fraction=1.0)
        p.perform("retract_syringe")
        state = p.step(900)
        self.assertGreater(state["fid_integral_C"], 0)
        for balance in p.mass_balance().values():
            self.assertLess(abs(balance["residual_kg"]), 1e-20)
        charge = sum(p.ledger[s]["eluted_kg"] * p.species[s].response_C_kg for s in p.species)
        self.assertAlmostEqual(state["fid_integral_C"], charge, delta=1e-20)
        self.assertAlmostEqual(state["fid_current_A"] * 900, charge, delta=1e-20)

    def test_legacy_all_39_rows_are_callable_and_configuration_resets(self):
        session = GC8890BLegacySession()
        called = set()
        def invoke(api_id, arguments=None):
            output = session.invoke(api_id, {} if arguments is None else arguments)
            called.add(api_id)
            return output
        invoke("RT01")
        invoke("RT02")
        invoke("RT04")
        for api_id in ("RT06", "RT08", "RT10", "RT12", "RT14", "RT15"):
            invoke(api_id)
        for api_id, value in (("RT16",353.15),("RT17",523.15),("RT18",573.15)):
            invoke(api_id, {"temperature_K": value})
        invoke("RT27", {"dt_s":1200})
        invoke("RT19")
        invoke("RT21")
        invoke("RT23", {"fraction":0.01})
        invoke("RT24")
        invoke("RT25", {"fraction":1.0})
        invoke("RT26")
        invoke("RT27", {"dt_s":300})
        for api_id in ("RT28", "RT29", "RT30", "RT31", "RT22", "RT20", "RT07", "RT09", "RT11", "RT13", "RT05", "RT03"):
            invoke(api_id)
        method = _gc8890b_test_Method()
        for index in range(32, 40):
            api_id = "RT%02d" % index
            fields = GC8890B_LEGACY_API_DEFINITIONS[api_id]["fields"]
            arguments = {field: getattr(method, field) for field in fields}
            if api_id == "RT33": arguments["mode"] = "splitless"
            invoke(api_id, arguments)
            self.assertEqual(session.process.time_s, 0)
            self.assertFalse(session.process.powered)
        self.assertEqual(called, {"RT%02d" % index for index in range(1,40)})

    def test_legacy_rejection_and_two_session_isolation(self):
        a, b = GC8890BLegacySession(), GC8890BLegacySession()
        a.invoke("RT02", {})
        a.invoke("RT04", {})
        a.invoke("RT27", {"dt_s":2})
        self.assertEqual(b.process.time_s, 0)
        self.assertFalse(b.process.powered)
        for api_id, args in (("RT17",{"temperature_K":723.15}), ("RT38",{"oven_temperature_K":353.15,"inlet_temperature_K":723.15,"fid_temperature_K":573.15}), ("RT02",{"role":"evaluator"}), ("RT27",{"dt_s":True})):
            before = _gc8890b_test_copy.deepcopy(a.process.__dict__)
            with self.assertRaises(ValueError): a.invoke(api_id,args)
            self.assertEqual(a.process.__dict__, before)


class TestAgilentGC8890BPublic(_gc8890b_unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.device = AgilentGC8890B("gc_a", scene_adapter=lambda instance,state: self.calls.append((instance,state)))
        self.device.register_sample("fixture", truth={"private_species":"PRIVATE_GC_MARKER", "mass_kg":1e-7})
        self.device.register_principal("judge", visibility="evaluator")
        self.judge = self.device.session("judge")
        self.sequence = 0

    def call(self, cid, **arguments):
        self.sequence += 1
        return self.device.invoke(capability_id=cid,arguments=arguments,request_id="gc_request_%d" % self.sequence)

    def ok(self, cid, **arguments):
        response = self.call(cid, **arguments)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def ready(self):
        self.ok("lid.set_angle", angle_rad=1.4)
        self.ok("sample.load", sample_id="fixture")
        self.ok("lid.set_angle", angle_rad=0)
        self.ok("power.set", enabled=True)

    def reject_unchanged(self, cid, expected="rejected", **arguments):
        before = self.device.debug_snapshot()
        scene_before = _gc8890b_test_copy.deepcopy(self.calls)
        response = self.call(cid, **arguments)
        self.assertEqual(response["status"], expected)
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(scene_before, self.calls)
        return response

    def test_load_close_measure_returns_synthetic_chromatogram_without_time_advance(self):
        self.reject_unchanged("chromatography.measure")
        self.ready()
        measured = self.ok("chromatography.measure")["result"]["chromatogram"]
        self.assertTrue(measured["synthetic"])
        self.assertEqual(measured["quality"], "estimated")
        self.assertEqual(len(measured["time_s"]), 301)
        self.assertEqual(measured["time_s"], list(range(301)))
        self.assertEqual(len(measured["fid_current_A"]), 301)
        self.assertEqual(measured["retention_time_s"], [60,125,220])
        self.assertTrue(all(area > 0 for area in measured["peak_area_C"]))
        self.assertIsNone(measured["calibrated_concentration_mol_m3"])
        self.assertIsNone(measured["measurement_duration_s"])
        self.assertEqual(self.device.read()["clock"]["time_s"], 0)
        # Peaks isolated sufficiently for sampled numerical area to match exact fixture integral.
        numerical = sum((a+b)/2 for a,b in zip(measured["fid_current_A"], measured["fid_current_A"][1:]))
        self.assertAlmostEqual(numerical, sum(measured["peak_area_C"]), delta=1e-15)

    def test_loading_and_measurement_preconditions_and_invalidation(self):
        self.reject_unchanged("sample.load", sample_id="fixture")
        self.ok("lid.set_angle", angle_rad=1.4)
        self.reject_unchanged("sample.load", sample_id="unregistered")
        self.ok("sample.load", sample_id="fixture")
        self.reject_unchanged("sample.load", sample_id="fixture")
        self.ok("power.set", enabled=True)
        self.reject_unchanged("chromatography.measure")
        self.ok("lid.set_angle", angle_rad=0)
        self.ok("chromatography.measure")
        self.ok("thermal.set_temperature", zone="oven", temperature_K=393.15)
        obs = self.device.read()["observations"]
        self.assertIsNone(obs["measurement.chromatogram"]["value"])
        self.assertEqual(obs["measurement.count"]["value"], 1)
        self.reject_unchanged("sample.unload")
        self.ok("lid.set_angle", angle_rad=1.4)
        self.ok("sample.unload")
        self.reject_unchanged("sample.unload")

    def test_public_limits_strict_types_and_unsupported_no_side_effects(self):
        for zone, temperature in (("inlet",723.15),("oven",302.14),("fid",723.16)):
            self.reject_unchanged("thermal.set_temperature", zone=zone, temperature_K=temperature)
        for fields in ({"enabled":1}, {"enabled":"true"}, {"enabled":True,"role":"evaluator"}):
            self.reject_unchanged("power.set", **fields)
        for value in (True,float("nan"),float("inf"),-0.1,2):
            self.reject_unchanged("lid.set_angle", angle_rad=value)
        for cid, definition in export_agilent_gc8890b_contract_metadata()["capabilities"].items():
            if definition["availability"] == "unsupported":
                self.reject_unchanged(cid, expected="unsupported")
        self.reject_unchanged("RT28")
        self.reject_unchanged("simulation.step", dt_s=1)

    def test_truth_filter_describe_read_invoke_scene_and_identity_binding(self):
        self.ready()
        measured = self.ok("chromatography.measure")
        denied = self.device.read(state_ids=["sample.truth"])
        nonexistent = self.device.read(state_ids=["hidden.other"])
        self.assertEqual(denied["error"], nonexistent["error"])
        payload = _gc8890b_test_json.dumps([self.device.describe(),self.device.read(),measured,self.calls,denied])
        for secret in ("PRIVATE_GC_MARKER", "private_species", "sample.truth", "process.thermal_truth", "vial_masses_kg", "mass_balance_kg"):
            self.assertNotIn(secret, payload)
        self.assertEqual(self.judge.read(state_ids=["sample.truth"])["observations"]["sample.truth"]["value"]["private_species"],"PRIVATE_GC_MARKER")
        self.assertIn("process.thermal_truth", self.judge.describe()["state_slots"])
        with self.assertRaises(TypeError): self.device.read(state_ids=["sample.truth"],role="evaluator")
        with self.assertRaises(ValueError): self.device.session("unknown")

    def test_request_replay_conflict_and_write_routes_zone_without_truth_write(self):
        kwargs = {"capability_id":"power.set","arguments":{"enabled":True},"request_id":"same"}
        result = self.device.invoke(**kwargs)
        before = self.device.debug_snapshot()
        count = len(self.calls)
        self.assertEqual(self.device.invoke(**kwargs),result)
        self.assertEqual(len(self.calls),count)
        conflict = self.device.invoke(**{**kwargs,"arguments":{"enabled":False}})
        self.assertEqual(conflict["error"]["code"],"REQUEST_CONFLICT")
        self.assertEqual(before,self.device.debug_snapshot())
        written = self.device.write(state_id="thermal.inlet.setpoint_k", value=633.15, request_id="write_zone")
        invoked = self.device.invoke(capability_id="thermal.set_temperature", arguments={"zone":"inlet","temperature_K":633.15}, request_id="write_zone")
        self.assertEqual(written,invoked)
        self.assertEqual(self.device.read(state_ids=["thermal.inlet.setpoint_k"])["observations"]["thermal.inlet.setpoint_k"]["value"],633.15)
        self.assertEqual(self.judge.write(state_id="sample.truth",value={},request_id="deny")["error"]["code"],"ACCESS_DENIED")

    def test_rejected_request_replay_and_mutable_returns_are_independent(self):
        request = {"capability_id":"chromatography.measure","arguments":{},"request_id":"initial"}
        denied = self.device.invoke(**request)
        self.ready()
        self.assertEqual(self.device.invoke(**request),denied)
        measured = self.ok("chromatography.measure")
        measured["result"]["chromatogram"]["fid_current_A"][0] = 999
        observed = self.device.read()
        observed["observations"]["measurement.chromatogram"]["value"]["fid_current_A"][0] = 999
        self.assertNotEqual(self.device.read()["observations"]["measurement.chromatogram"]["value"]["fid_current_A"][0],999)

    def test_host_clock_read_is_pure_and_instances_are_isolated(self):
        other = AgilentGC8890B("gc_b",scene_adapter=lambda i,s:None)
        self.ready()
        before = self.device.debug_snapshot()
        for _ in range(3): self.device.describe(); self.device.read()
        self.assertEqual(before,self.device.debug_snapshot())
        self.device.host_step(10)
        self.assertEqual(self.device.read()["clock"]["time_s"],10)
        self.assertEqual(other.read()["clock"]["time_s"],0)
        self.assertFalse(other.read()["observations"]["sample.loaded"]["value"])
        self.assertFalse(other.read()["observations"]["power.enabled"]["value"])
        self.assertGreater(self.device.read()["observations"]["thermal.oven.indicated_k"]["value"],298.15)
        self.assertEqual(self.device.invoke(instance_id="gc_b",capability_id="power.set",arguments={"enabled":True},request_id="cross")["error"]["code"],"UNKNOWN_INSTANCE")

    def test_unbound_scene_and_failing_callback_never_claim_success(self):
        unbound = AgilentGC8890B("unbound")
        self.assertNotIn("lid.set_angle",unbound.describe()["capabilities"])
        self.assertEqual(unbound.invoke(capability_id="lid.set_angle",arguments={"angle_rad":1},request_id="no_scene")["status"],"unsupported")
        def broken(i,s): raise RuntimeError("PRIVATE_GC_MARKER")
        broken_device = AgilentGC8890B("broken",scene_adapter=broken)
        before = broken_device.debug_snapshot()
        response = broken_device.invoke(capability_id="power.set",arguments={"enabled":True},request_id="broken")
        self.assertEqual(response["status"],"failed")
        self.assertEqual(response["error"]["side_effects"],"unknown")
        self.assertEqual(before,broken_device.debug_snapshot())
        self.assertNotIn("PRIVATE_GC_MARKER",_gc8890b_test_json.dumps(response))

    def test_fixture_registration_is_validated_before_mutation(self):
        before = self.device.debug_snapshot()
        for fields in ({"peaks":[[1,0,1]]},{"peaks":[[1,1,True]]},{"truth":{"bad":float("nan")}}, {"truth":"bad"}):
            with self.assertRaises(ValueError): self.device.register_sample("invalid",**fields)
            self.assertEqual(before,self.device.debug_snapshot())

    def test_metadata_unknown_measurements_are_null_and_parameter_evidence_is_honest(self):
        meta = export_agilent_gc8890b_contract_metadata()
        for sid in meta["state_slots"]:
            self.assertEqual(sid,sid.lower())
        self.assertIsNone(meta["parameters"]["measurement_duration_s"]["value"])
        self.assertIsNone(meta["parameters"]["calibrated_concentration_mol_m3"]["value"])
        self.assertEqual(meta["parameters"]["oven_heat_capacity_J_K"]["evidence"],"engineering_assumption")
        self.assertIn("50..70",meta["parameters"]["oven_maximum_ramp_K_s"]["conditions"])
        self.assertIn("30..40",meta["parameters"]["oven_cooling_conductance_W_K"]["conditions"])



# CHEM-072 FT/IR-4700 append-only tests; synthetic sample fixtures only.
import copy as _ftir_test_copy
import json as _ftir_test_json
import math as _ftir_test_math
import unittest as _ftir_test_unittest
from basic40_runtime_analytical import JascoFTIR4700, JASCO_FTIR4700_LID_MAX_RAD, export_jasco_ftir4700_contract_metadata


class TestJascoFTIR4700Runtime(_ftir_test_unittest.TestCase):
    def setUp(self):
        self.scene_calls = []
        self.now = 17.0
        self.device = JascoFTIR4700("ftir_a", scene_adapter=self.scene, clock=lambda: self.now)
        self.device.register_sample("synthetic_sample", phase="solid", truth={"private_species": "ftir_private_marker", "private_amount": 0.7})
        self.device.register_sample("synthetic_liquid", phase="liquid")
        self.device.register_sample("synthetic_gas", phase="gas")
        self.device.register_principal("evaluator_identity", visibility="evaluator")
        self.judge = self.device.session("evaluator_identity")
        self.counter = 0

    def scene(self, instance_id, updates):
        self.scene_calls.append((instance_id, _ftir_test_copy.deepcopy(updates)))

    def call(self, capability, **arguments):
        self.counter += 1
        return self.device.invoke(instance_id="ftir_a", capability_id=capability, arguments=arguments, request_id="ftir_test_" + str(self.counter))

    def ok(self, capability, **arguments):
        result = self.call(capability, **arguments)
        self.assertEqual(result["status"], "succeeded", result)
        return result

    def value(self, state_id):
        return self.device.read(state_ids=[state_id])["observations"][state_id]["value"]

    def method(self, **updates):
        method = {"start_wavenumber_m_inv": 35000.0, "stop_wavenumber_m_inv": 780000.0,
            "resolution_m_inv": 400.0, "scan_count": 16, "output_mode": "absorbance"}
        method.update(updates)
        return method

    def prepared(self, mode="atr", method=None):
        self.ok("power.set", enabled=True)
        if method:
            self.ok("method.configure", **method)
        self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
        self.ok("accessory.install", mode=mode)
        self.ok("lid.set_angle", angle_rad=0.0)
        self.ok("background.acquire")

    def ready(self, sample="synthetic_sample", mode="atr", method=None):
        self.prepared(mode=mode, method=method)
        self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
        self.ok("sample.load", sample_id=sample)
        self.ok("lid.set_angle", angle_rad=0.0)

    def assert_unchanged(self, capability, arguments, status="rejected", code=None):
        before = self.device.debug_snapshot()
        scene = _ftir_test_copy.deepcopy(self.scene_calls)
        result = self.call(capability, **arguments)
        self.assertEqual(result["status"], status, result)
        if code:
            self.assertEqual(result["error"]["code"], code)
        self.assertEqual(result["error"]["side_effects"], "none")
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(scene, self.scene_calls)
        return result

    def test_full_atr_background_load_close_measure_workflow(self):
        self.ready()
        result = self.ok("measure")["result"]["spectrum"]
        self.assertTrue(result["synthetic"])
        self.assertEqual(result["quality"], "estimated")
        self.assertEqual(result["axis_unit"], "m^-1")
        self.assertEqual(result["wavenumber_m_inv"][0], 35000)
        self.assertEqual(result["wavenumber_m_inv"][-1], 780000)
        self.assertEqual(len(result["absorbance"]), 512)
        self.assertEqual(self.value("measurement.spectrum"), result)
        self.assertEqual(self.value("measurement.count"), 1)
        self.assertEqual(self.value("background.count"), 1)
        self.assertIsNone(result["measurement_duration_s"])

    def test_all_output_modes_return_finite_consistent_synthetic_arrays(self):
        for mode in ("absorbance", "transmittance", "interferogram"):
            with self.subTest(mode=mode):
                self.setUp()
                self.ready(method=self.method(output_mode=mode))
                result = self.ok("measure")["result"]["spectrum"]
                self.assertEqual(result["reported_mode"], mode)
                for absorbance, transmittance in zip(result["absorbance"], result["transmittance"]):
                    self.assertTrue(_ftir_test_math.isfinite(absorbance))
                    self.assertAlmostEqual(transmittance, 10 ** -absorbance, places=14)
                interferogram = result["interferogram"]
                self.assertTrue(interferogram["synthetic"])
                self.assertEqual(len(interferogram["relative_intensity"]), 257)
                self.assertEqual(interferogram["optical_path_difference_m"][128], 0)
                self.assertAlmostEqual(interferogram["relative_intensity"][128], sum(result["transmittance"]) / 512, places=14)
                for left, right in zip(interferogram["relative_intensity"], reversed(interferogram["relative_intensity"])):
                    self.assertTrue(_ftir_test_math.isfinite(left))
                    self.assertAlmostEqual(left, right, places=14)

    def test_resolution_and_scan_count_are_explicit_settings_only(self):
        self.ready(method=self.method(resolution_m_inv=40, scan_count=1))
        first = self.ok("measure")["result"]["spectrum"]
        self.assertEqual(first["resolution_m_inv"], 40)
        self.assertEqual(first["scan_count"], 1)
        self.assertFalse(first["spectral_resolution_applied"])
        self.assertFalse(first["scan_averaging_applied"])
        self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
        self.ok("sample.unload")
        self.ok("method.configure", **self.method(resolution_m_inv=1600, scan_count=1024))
        self.ok("lid.set_angle", angle_rad=0)
        self.ok("background.acquire")
        self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
        self.ok("sample.load", sample_id="synthetic_sample")
        self.ok("lid.set_angle", angle_rad=0)
        second = self.ok("measure")["result"]["spectrum"]
        self.assertEqual(first["wavenumber_m_inv"], second["wavenumber_m_inv"])
        self.assertEqual(first["absorbance"], second["absorbance"])
        self.assertEqual(second["scan_count"], 1024)

    def test_transmission_handles_registered_solid_liquid_and_gas(self):
        self.prepared(mode="transmission")
        for sample, phase in (("synthetic_sample", "solid"), ("synthetic_liquid", "liquid"), ("synthetic_gas", "gas")):
            self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
            self.ok("sample.load", sample_id=sample)
            self.assertEqual(self.value("sample.phase"), phase)
            self.ok("lid.set_angle", angle_rad=0)
            self.ok("measure")
            self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
            self.ok("sample.unload")
        self.assertEqual(self.value("measurement.count"), 3)
        self.assertIsNone(self.value("sample.id"))

    def test_atr_rejects_gas_and_accepts_liquid(self):
        self.prepared(mode="atr")
        self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
        self.assert_unchanged("sample.load", {"sample_id": "synthetic_gas"})
        self.ok("sample.load", sample_id="synthetic_liquid")
        self.ok("lid.set_angle", angle_rad=0)
        self.ok("measure")

    def test_method_si_limits_types_and_extra_fields_reject_atomically(self):
        self.ok("power.set", enabled=True)
        updates = [{"start_wavenumber_m_inv": 350}, {"stop_wavenumber_m_inv": 780001},
            {"start_wavenumber_m_inv": 780000}, {"start_wavenumber_m_inv": 500000, "stop_wavenumber_m_inv": 100000},
            {"resolution_m_inv": 39.9}, {"resolution_m_inv": True}, {"resolution_m_inv": 10 ** 400},
            {"resolution_m_inv": float("inf")}, {"resolution_m_inv": float("nan")},
            {"scan_count": 0}, {"scan_count": 1025}, {"scan_count": 1.5}, {"scan_count": True},
            {"output_mode": "library_match"}, {"unknown_parameter": 2}]
        for update in updates:
            with self.subTest(update=update):
                self.assert_unchanged("method.configure", self.method(**update), code="INVALID_ARGUMENT")
        self.assert_unchanged("method.configure", {"resolution_m_inv": 40})
        self.ok("method.configure", **self.method(start_wavenumber_m_inv=100000, stop_wavenumber_m_inv=200000, resolution_m_inv=40, scan_count=1024))

    def test_primitive_types_and_malformed_identifiers_reject_without_mutation(self):
        for capability, arguments in (("power.set", {"enabled": 1}), ("lid.set_angle", {"angle_rad": True}),
            ("lid.set_angle", {"angle_rad": -0.1}), ("lid.set_angle", {"angle_rad": float("nan")}),
            ("accessory.install", {"mode": "raman"}), ("sample.load", {"sample_id": ""}), ("measure", {"role": "evaluator"})):
            self.assert_unchanged(capability, arguments)
        before = self.device.debug_snapshot()
        for malformed in ([], {}, True, 0):
            self.assertEqual(self.device.read(instance_id=malformed)["status"], "rejected")
            self.assertEqual(self.device.describe(instance_id=malformed)["status"], "rejected")
            self.assertEqual(self.device.invoke(capability_id=malformed, arguments={}, request_id="malformed")["status"], "rejected")
            self.assertEqual(self.device.invoke(capability_id="power.set", arguments={"enabled": True}, request_id=malformed)["status"], "rejected")
        self.assertEqual(before, self.device.debug_snapshot())

    def test_measure_requires_power_accessory_background_sample_and_closed_lid(self):
        self.assert_unchanged("measure", {})
        self.ok("power.set", enabled=True)
        self.assert_unchanged("measure", {})
        self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
        self.ok("accessory.install", mode="atr")
        self.ok("sample.load", sample_id="synthetic_sample")
        self.assert_unchanged("measure", {})
        self.ok("lid.set_angle", angle_rad=0)
        self.assert_unchanged("measure", {})
        self.assert_unchanged("background.acquire", {})

    def test_accessory_and_sample_loading_clearance_slot_preconditions(self):
        self.assert_unchanged("accessory.install", {"mode": "atr"})
        self.assert_unchanged("sample.load", {"sample_id": "synthetic_sample"})
        self.ok("lid.set_angle", angle_rad=_ftir_test_math.pi / 4)
        self.assert_unchanged("accessory.install", {"mode": "atr"})
        self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
        self.assert_unchanged("sample.load", {"sample_id": "synthetic_sample"})
        self.ok("accessory.install", mode="atr")
        self.assert_unchanged("sample.load", {"sample_id": "unregistered"})
        self.assert_unchanged("sample.unload", {})
        self.ok("sample.load", sample_id="synthetic_sample")
        self.assert_unchanged("accessory.install", {"mode": "transmission"})
        self.assert_unchanged("sample.load", {"sample_id": "synthetic_liquid"})
        self.ok("lid.set_angle", angle_rad=0)
        self.assert_unchanged("sample.unload", {})

    def test_background_invalidates_on_method_accessory_power_not_lid_or_sample(self):
        self.ready()
        self.assertTrue(self.value("background.ready"))
        self.ok("measure")
        self.ok("lid.set_angle", angle_rad=JASCO_FTIR4700_LID_MAX_RAD)
        self.assertIsNone(self.value("measurement.spectrum"))
        self.assertTrue(self.value("background.ready"))
        self.ok("sample.unload")
        self.assertTrue(self.value("background.ready"))
        self.ok("accessory.install", mode="transmission")
        self.assertFalse(self.value("background.ready"))
        self.ok("lid.set_angle", angle_rad=0)
        self.ok("background.acquire")
        self.ok("method.configure", **self.method(output_mode="transmittance"))
        self.assertFalse(self.value("background.ready"))
        self.ok("background.acquire")
        self.ok("power.set", enabled=False)
        self.assertFalse(self.value("background.ready"))
        self.assertIsNone(self.value("background.spectrum"))
        self.assertEqual(self.value("measurement.count"), 1)
        self.assertEqual(self.value("background.count"), 3)

    def test_agent_filter_covers_description_reads_results_errors_scene_and_identity(self):
        self.ready()
        responses = [self.device.describe(), self.device.read(), self.ok("measure"), self.scene_calls]
        denied = self.device.read(state_ids=["sample.truth"])
        unknown = self.device.read(state_ids=["unknown.truth"])
        self.assertEqual(denied["error"], unknown["error"])
        responses.extend([denied, unknown, self.call("measure", role="evaluator")])
        serialized = _ftir_test_json.dumps(responses)
        for secret in ("sample.truth", "private_species", "private_amount", "ftir_private_marker"):
            self.assertNotIn(secret, serialized)
        truth = self.judge.read(state_ids=["sample.truth"])["observations"]["sample.truth"]["value"]
        self.assertEqual(truth["private_species"], "ftir_private_marker")
        with self.assertRaises(TypeError):
            self.device.read(state_ids=["sample.truth"], role="evaluator")
        with self.assertRaises(ValueError):
            self.device.session("unregistered_evaluator")

    def test_read_and_describe_are_time_pure_and_observations_are_defensive_copies(self):
        self.ready()
        measured = self.ok("measure")
        before = self.device.debug_snapshot()
        scene = _ftir_test_copy.deepcopy(self.scene_calls)
        for _ in range(3):
            response = self.device.read()
            self.assertEqual(response["clock"]["time_s"], 17)
            self.device.describe()
        measured["result"]["spectrum"]["absorbance"][0] = 999
        response["observations"]["measurement.spectrum"]["value"]["absorbance"][0] = 999
        metadata = self.device.describe()
        metadata["capabilities"]["power.set"]["availability"] = "unsupported"
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(scene, self.scene_calls)
        self.assertEqual(self.device.describe()["capabilities"]["power.set"]["availability"], "implemented")
        self.now = 42.0
        self.assertEqual(self.device.read()["clock"]["time_s"], 42)

    def test_two_instances_and_request_caches_remain_isolated(self):
        other_calls = []
        other = JascoFTIR4700("ftir_b", scene_adapter=lambda instance, updates: other_calls.append((instance, updates)))
        self.ready()
        self.ok("measure")
        self.assertFalse(other.read()["observations"]["power.enabled"]["value"])
        self.assertEqual(other.debug_snapshot()["registered_samples"], {})
        self.assertEqual(other_calls, [])
        cross = self.device.invoke(instance_id="ftir_b", capability_id="power.set", arguments={"enabled": True}, request_id="cross")
        self.assertEqual(cross["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(other.invoke(capability_id="power.set", arguments={"enabled": True}, request_id="ftir_test_1")["status"], "succeeded")
        self.assertEqual(other_calls[0][0], "ftir_b")

    def test_success_replay_conflict_and_write_routes(self):
        request = {"capability_id": "power.set", "arguments": {"enabled": True}, "request_id": "once"}
        first = self.device.invoke(**request)
        before = self.device.debug_snapshot()
        calls = len(self.scene_calls)
        self.assertEqual(self.device.invoke(**request), first)
        self.assertEqual(calls, len(self.scene_calls))
        conflict = self.device.invoke(**{**request, "arguments": {"enabled": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.device.debug_snapshot())
        written = self.device.write(state_id="lid.angle_rad", value=JASCO_FTIR4700_LID_MAX_RAD, request_id="write_lid")
        invoked = self.device.invoke(capability_id="lid.set_angle", arguments={"angle_rad": JASCO_FTIR4700_LID_MAX_RAD}, request_id="write_lid")
        self.assertEqual(written, invoked)
        self.assertEqual(self.device.write(state_id="method.resolution_m_inv", value=40, request_id="fragment")["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.judge.write(state_id="sample.truth", value={"fake": True}, request_id="truth")["error"]["code"], "ACCESS_DENIED")

    def test_rejected_and_failed_requests_replay_without_execution(self):
        request = {"capability_id": "measure", "arguments": {}, "request_id": "not_ready_once"}
        rejected = self.device.invoke(**request)
        self.ready()
        before = self.device.debug_snapshot()
        self.assertEqual(self.device.invoke(**request), rejected)
        self.assertEqual(before, self.device.debug_snapshot())
        calls = []
        def failure(instance, updates):
            calls.append(instance)
            raise RuntimeError("ftir_private_marker")
        device = JascoFTIR4700("broken", scene_adapter=failure)
        action = {"capability_id": "lid.set_angle", "arguments": {"angle_rad": 1}, "request_id": "failed_once"}
        before = device.debug_snapshot()
        failed = device.invoke(**action)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["side_effects"], "unknown")
        self.assertNotIn("ftir_private_marker", _ftir_test_json.dumps(failed))
        self.assertEqual(device.invoke(**action), failed)
        self.assertEqual(calls, ["broken"])
        self.assertEqual(before, device.debug_snapshot())

    def test_scene_receives_public_state_and_explicit_sync_is_idempotent(self):
        self.ready()
        self.ok("measure")
        self.assertTrue(all(instance == "ftir_a" for instance, updates in self.scene_calls))
        self.assertTrue(self.scene_calls[-1][1]["measurement.spectrum"]["synthetic"])
        self.assertNotIn("sample.truth", _ftir_test_json.dumps(self.scene_calls))
        self.device.sync_scene()
        self.assertEqual(self.scene_calls[-1], self.scene_calls[-2])

    def test_missing_scene_is_unsupported_and_filtered_from_discovery(self):
        device = JascoFTIR4700("unbound")
        before = device.debug_snapshot()
        for capability, arguments in (("lid.set_angle", {"angle_rad": 1}), ("accessory.install", {"mode": "atr"}), ("sample.load", {"sample_id": "synthetic_sample"}), ("sample.unload", {})):
            result = device.invoke(capability_id=capability, arguments=arguments, request_id=capability)
            self.assertEqual(result["status"], "unsupported")
            self.assertNotIn(capability, device.describe()["capabilities"])
        self.assertEqual(before, device.debug_snapshot())
        with self.assertRaises(RuntimeError):
            device.sync_scene()

    def test_unimplemented_library_fast_scan_hardware_robot_return_unsupported(self):
        for capability in ("library.match", "fast_scan.start", "hardware.connect", "robot.load_sample"):
            self.assert_unchanged(capability, {}, status="unsupported")
            self.assertNotIn(capability, self.device.describe()["capabilities"])

    def test_host_registration_is_atomic_and_defensively_copies_truth(self):
        before = self.device.debug_snapshot()
        for arguments in ({"phase": "plasma"}, {"peaks": [[170000, 0, 1]]}, {"peaks": [[780001, 100, 1]]}, {"baseline_absorbance": float("nan")}, {"truth": "not a dictionary"}):
            with self.assertRaises(ValueError):
                self.device.register_sample("bad", **arguments)
            self.assertEqual(before, self.device.debug_snapshot())
        truth = {"secret": [1]}
        self.device.register_sample("copy_fixture", truth=truth)
        truth["secret"][0] = 999
        self.assertEqual(self.device.debug_snapshot()["registered_samples"]["copy_fixture"]["truth"]["secret"], [1])

    def test_metadata_preserves_unknowns_conditions_and_synthetic_bounds(self):
        metadata = export_jasco_ftir4700_contract_metadata()
        parameters = metadata["parameters"]
        self.assertIsNone(parameters["measurement_duration_s"]["value"])
        self.assertIsNone(parameters["sample_path_length_m"]["value"])
        self.assertIsNone(parameters["atr_penetration_depth_m"]["value"])
        self.assertEqual(parameters["highest_resolution_m_inv"]["value"], 40)
        self.assertIn("conditions", parameters["signal_to_noise_ratio"])
        self.assertIn("16 cm^-1", parameters["optional_fast_scan_frequency_hz"]["conditions"])
        self.assertEqual(parameters["scan_count_limit"]["evidence"], "assumption")
        self.assertEqual(parameters["synthetic_gaussian_peaks"]["evidence"], "assumption")

# CHEM-108 Agilent 8697: append-only verification of synthetic runtime.
import copy as _hs8697_test_copy
import json as _hs8697_test_json
import math as _hs8697_test_math
import importlib.util as _hs8697_test_importlib
import unittest as _hs8697_test_unittest
from pathlib import Path as _HS8697TestPath
from basic40_runtime_analytical import AgilentHS8697, AgilentHS8697Session, AGILENT_HS8697_LID_MAX_RAD, export_agilent_hs8697_contract_metadata

_hs8697_tool_spec = _hs8697_test_importlib.spec_from_file_location('hs8697_asset_tool', _HS8697TestPath(__file__).resolve().parents[1] / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
_hs8697_asset_tool = _hs8697_test_importlib.module_from_spec(_hs8697_tool_spec)
_hs8697_tool_spec.loader.exec_module(_hs8697_asset_tool)


class TestAgilentHS8697Runtime(_hs8697_test_unittest.TestCase):
    def setUp(self):
        self.scene_calls, self.time_s, self.counter = [], 17.0, 0
        self.device = AgilentHS8697('hs_a', scene_adapter=self.scene, clock=lambda: self.time_s, ambient_temperature_K=293.15)
        self.device.register_sample('fixture_01', volume_m3=20e-6, truth={'private_species': 'headspace_secret_marker', 'quantity': 17.3})
        self.device.register_sample('fixture_02', volume_m3=10e-6)
        self.device.register_principal('judge', visibility='evaluator')
        self.judge = self.device.session('judge')
        self.metadata = export_agilent_hs8697_contract_metadata()

    def scene(self, instance_id, updates):
        self.scene_calls.append((instance_id, _hs8697_test_copy.deepcopy(updates)))

    def invoke(self, cid, args=None, request_id=None):
        self.counter += 1
        return self.device.invoke(capability_id=cid, arguments=args or {}, request_id=request_id or 'req_%d' % self.counter)

    def ok(self, cid, args=None):
        args = args or {}
        cap = self.metadata['capabilities'][cid]
        self.assertEqual(_hs8697_asset_tool.validate_value(args, cap['input_schema']), [])
        result = self.invoke(cid, args)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(_hs8697_asset_tool.validate_value(result['result'], cap['output_schema']), [])
        for sid, observation in self.judge.read()['observations'].items():
            self.assertEqual(_hs8697_asset_tool.validate_value(observation['value'], self.metadata['state_slots'][sid]['value_schema']), [], sid)
        return result

    def unchanged(self, cid, args=None, status='rejected', code=None):
        before, calls = self.device.debug_snapshot(), len(self.scene_calls)
        result = self.invoke(cid, args)
        self.assertEqual(result['status'], status, result)
        self.assertEqual(result['error']['side_effects'], 'none')
        if code:
            self.assertEqual(result['error']['code'], code)
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(calls, len(self.scene_calls))
        return result

    def ready(self):
        self.ok('power.set', {'enabled': True})
        self.ok('lid.set_angle', {'angle_rad': AGILENT_HS8697_LID_MAX_RAD})
        self.ok('sample.load', {'sample_id': 'fixture_01', 'slot_id': 'tray_01'})
        self.ok('sample.transfer', {'source_slot': 'tray_01', 'target_slot': 'oven_01'})
        self.ok('lid.set_angle', {'angle_rad': 0})
        for z in ('oven', 'loop_valve', 'transfer_line'):
            self.ok('thermal.set_temperature', {'zone': z, 'temperature_K': 333.15})
        self.ok('method.set_equilibration_time', {'duration_s': 60})
        self.device.register_gc('gc_fixture')

    def test_complete_workflow_all_actual_api_input_output_and_state_schemas(self):
        self.ready()
        result = self.ok('measure', {'oven_slot': 'oven_01'})
        ch = result['result']['chromatogram']
        self.assertTrue(result['result']['synthetic'])
        self.assertTrue(ch['synthetic'])
        self.assertEqual(ch['quality'], 'estimated')
        self.assertEqual(ch['gc_instance_id'], 'gc_fixture')
        self.assertEqual(ch['signal_unit'], '1')
        self.assertEqual(len(ch['time_s']), 121)
        self.assertEqual(ch['time_s'][0], 0)
        self.assertEqual(ch['time_s'][-1], 120)
        self.assertTrue(all(_hs8697_test_math.isfinite(x) for x in ch['signal']))
        self.assertIsNone(ch['physical_transfer_mol'])
        self.assertIsNone(ch['measurement_duration_s'])
        state = self.device.debug_snapshot()['state']
        self.assertEqual(state['vial.status'][0]['status'], 'measured_synthetic')
        self.assertEqual(state['measurement.count'], 1)
        self.assertTrue(state['injection.log'][0]['synthetic'])
        self.assertEqual(state['injection.log'][0]['time_s'], 17)
        self.ok('lid.set_angle', {'angle_rad': AGILENT_HS8697_LID_MAX_RAD})
        self.ok('sample.transfer', {'source_slot': 'oven_01', 'target_slot': 'tray_01'})
        self.ok('sample.unload', {'slot_id': 'tray_01'})
        state = self.device.debug_snapshot()['state']
        self.assertEqual(state['vial.status'][0]['status'], 'unloaded')
        self.assertEqual(state['sample.truth'], {})
        self.assertEqual(sum(x is not None for x in state['tray.occupancy'] + state['oven.occupancy']), 0)
        self.assertIsNone(state['measurement.chromatogram'])
        self.assertEqual(state['measurement.count'], 1)

    def test_exact_48_tray_12_oven_capacity_and_unique_vial_conservation(self):
        self.ok('power.set', {'enabled': True})
        self.ok('lid.set_angle', {'angle_rad': AGILENT_HS8697_LID_MAX_RAD})
        for i in range(1, 61):
            self.device.register_sample('capacity_%02d' % i, volume_m3=(10e-6, 20e-6, 22e-6)[i % 3])
        for i in range(1, 49):
            self.ok('sample.load', {'sample_id': 'capacity_%02d' % i, 'slot_id': 'tray_%02d' % i})
        self.unchanged('sample.load', {'sample_id': 'capacity_49', 'slot_id': 'tray_01'})
        for i in range(1, 13):
            self.ok('sample.transfer', {'source_slot': 'tray_%02d' % i, 'target_slot': 'oven_%02d' % i})
            self.ok('sample.load', {'sample_id': 'capacity_%02d' % (i + 48), 'slot_id': 'tray_%02d' % i})
        state = self.device.debug_snapshot()['state']
        self.assertEqual(len(state['tray.occupancy']), 48)
        self.assertEqual(len(state['oven.occupancy']), 12)
        self.assertEqual(len(set(state['tray.occupancy'] + state['oven.occupancy'])), 60)
        self.assertEqual(state['manipulator.position_slot'], 'oven_12')
        self.unchanged('sample.transfer', {'source_slot': 'tray_01', 'target_slot': 'oven_01'})
        self.unchanged('sample.load', {'sample_id': 'fixture_01', 'slot_id': 'tray_49'}, code='INVALID_ARGUMENT')
        self.unchanged('sample.transfer', {'source_slot': 'tray_01', 'target_slot': 'oven_13'}, code='INVALID_ARGUMENT')

    def test_sealed_vial_loading_clearance_duplicate_and_transfer_preconditions(self):
        self.unchanged('sample.load', {'sample_id': 'fixture_01', 'slot_id': 'tray_01'})
        self.ok('lid.set_angle', {'angle_rad': 0.5})
        self.unchanged('sample.load', {'sample_id': 'fixture_01', 'slot_id': 'tray_01'})
        self.ok('lid.set_angle', {'angle_rad': AGILENT_HS8697_LID_MAX_RAD})
        self.device.register_sample('unsealed', volume_m3=22e-6, sealed=False)
        self.unchanged('sample.load', {'sample_id': 'unsealed', 'slot_id': 'tray_01'})
        self.unchanged('sample.load', {'sample_id': 'missing', 'slot_id': 'tray_01'})
        self.ok('sample.load', {'sample_id': 'fixture_01', 'slot_id': 'tray_01'})
        self.unchanged('sample.load', {'sample_id': 'fixture_01', 'slot_id': 'tray_02'})
        self.unchanged('sample.unload', {'slot_id': 'tray_02'})
        self.unchanged('sample.transfer', {'source_slot': 'tray_01', 'target_slot': 'oven_01'})
        self.ok('power.set', {'enabled': True})
        self.unchanged('sample.transfer', {'source_slot': 'tray_01', 'target_slot': 'tray_01'})
        self.unchanged('sample.transfer', {'source_slot': 'tray_02', 'target_slot': 'oven_01'})

    def test_temperature_three_zones_ambient_relative_minimum_and_maximum(self):
        self.unchanged('thermal.set_temperature', {'zone': 'oven', 'temperature_K': 333.15})
        self.ok('power.set', {'enabled': True})
        for zone in ('oven', 'loop_valve', 'transfer_line'):
            self.ok('thermal.set_temperature', {'zone': zone, 'temperature_K': 298.15})
            self.ok('thermal.set_temperature', {'zone': zone, 'temperature_K': 573.15})
            self.unchanged('thermal.set_temperature', {'zone': zone, 'temperature_K': 298.14})
            self.unchanged('thermal.set_temperature', {'zone': zone, 'temperature_K': 573.16}, code='INVALID_ARGUMENT')
        self.device.set_ambient_temperature(571)
        self.unchanged('thermal.set_temperature', {'zone': 'oven', 'temperature_K': 573.15})
        observations = self.device.read()['observations']
        for zone in ('oven', 'loop_valve', 'transfer_line'):
            self.assertIsNone(observations['thermal.' + zone + '.actual_k']['value'])
            self.assertEqual(observations['thermal.' + zone + '.actual_k']['quality'], 'unavailable')

    def test_unknown_ambient_has_no_invented_temperature_default(self):
        device = AgilentHS8697('unknown_ambient')
        self.assertIsNone(device.read()['observations']['ambient.temperature_k']['value'])
        device.invoke(capability_id='power.set', arguments={'enabled': True}, request_id='on')
        before = device.debug_snapshot()
        result = device.invoke(capability_id='thermal.set_temperature', arguments={'zone': 'oven', 'temperature_K': 300}, request_id='set')
        self.assertEqual(result['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(before, device.debug_snapshot())

    def test_time_range_step_and_rejected_operations_do_not_mutate(self):
        self.ok('power.set', {'enabled': True})
        for value in (0, 0.6, 60, 59999.4):
            self.ok('method.set_equilibration_time', {'duration_s': value})
        for value in (-0.6, 0.1, 60.01, 59999.5, 60000, True, '60', float('nan'), float('inf')):
            self.unchanged('method.set_equilibration_time', {'duration_s': value}, code='INVALID_ARGUMENT')

    def test_measure_requires_gc_carrier_lid_sample_power_and_configured_method(self):
        self.unchanged('measure', {'oven_slot': 'oven_01'})
        self.ready()
        self.device.disconnect_gc()
        self.unchanged('measure', {'oven_slot': 'oven_01'})
        self.device.register_gc('gc_fixture', carrier_gas_available=False)
        self.unchanged('measure', {'oven_slot': 'oven_01'})
        self.device.register_gc('gc_fixture')
        self.unchanged('measure', {'oven_slot': 'oven_02'})
        self.ok('lid.set_angle', {'angle_rad': 0.01})
        self.unchanged('measure', {'oven_slot': 'oven_01'})
        self.ok('lid.set_angle', {'angle_rad': 0})
        self.device.set_ambient_temperature(400)
        self.unchanged('measure', {'oven_slot': 'oven_01'})
        self.device.set_ambient_temperature(293.15)
        self.ok('power.set', {'enabled': False})
        self.unchanged('measure', {'oven_slot': 'oven_01'})

    def test_measure_refuses_missing_zone_or_equilibration_targets(self):
        self.ok('power.set', {'enabled': True})
        self.ok('lid.set_angle', {'angle_rad': AGILENT_HS8697_LID_MAX_RAD})
        self.ok('sample.load', {'sample_id': 'fixture_01', 'slot_id': 'tray_01'})
        self.ok('sample.transfer', {'source_slot': 'tray_01', 'target_slot': 'oven_01'})
        self.ok('lid.set_angle', {'angle_rad': 0})
        self.device.register_gc('gc_fixture')
        for zone in ('oven', 'loop_valve', 'transfer_line'):
            self.unchanged('measure', {'oven_slot': 'oven_01'})
            self.ok('thermal.set_temperature', {'zone': zone, 'temperature_K': 333.15})
        self.unchanged('measure', {'oven_slot': 'oven_01'})
        self.ok('method.set_equilibration_time', {'duration_s': 0})
        self.ok('measure', {'oven_slot': 'oven_01'})

    def test_read_and_measure_do_not_advance_host_time_and_reads_do_not_write(self):
        self.ready()
        before = self.device.debug_snapshot()
        observations = self.device.read()
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(observations['clock']['time_s'], 17)
        self.assertTrue(all(item['sample_time_s'] == 17 for item in observations['observations'].values()))
        self.ok('measure', {'oven_slot': 'oven_01'})
        self.assertEqual(self.time_s, 17)
        after = self.device.debug_snapshot()
        self.time_s = 99
        self.assertEqual(self.device.read()['clock']['time_s'], 99)
        self.assertEqual(after, self.device.debug_snapshot())

    def test_request_deduplication_conflict_and_result_mutation_isolation(self):
        self.ready()
        args = {'capability_id': 'measure', 'arguments': {'oven_slot': 'oven_01'}, 'request_id': 'one_measurement'}
        first = self.device.invoke(**args)
        state, calls = self.device.debug_snapshot(), len(self.scene_calls)
        self.time_s = 100
        self.assertEqual(first, self.device.invoke(**args))
        self.assertEqual(state, self.device.debug_snapshot())
        self.assertEqual(calls, len(self.scene_calls))
        first['result']['chromatogram']['signal'][0] = -999
        self.assertNotEqual(self.device.invoke(**args)['result']['chromatogram']['signal'][0], -999)
        conflict = self.device.invoke(**{**args, 'arguments': {'oven_slot': 'oven_02'}})
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(state, self.device.debug_snapshot())
        self.assertEqual(state['state']['measurement.count'], 1)
        self.assertEqual(len(state['state']['injection.log']), 1)

    def test_rejected_request_is_cached_after_preconditions_change(self):
        args = {'capability_id': 'sample.load', 'arguments': {'sample_id': 'fixture_01', 'slot_id': 'tray_01'}, 'request_id': 'initially_closed'}
        first = self.device.invoke(**args)
        self.ok('lid.set_angle', {'angle_rad': AGILENT_HS8697_LID_MAX_RAD})
        state = self.device.debug_snapshot()
        self.assertEqual(first, self.device.invoke(**args))
        self.assertEqual(state, self.device.debug_snapshot())
        self.assertEqual(first['error']['side_effects'], 'none')

    def test_truth_is_filtered_from_describe_read_measure_scene_and_errors(self):
        self.ready()
        measurement = self.ok('measure', {'oven_slot': 'oven_01'})
        public = [self.device.describe(), self.device.read(), measurement, self.scene_calls]
        self.assertNotIn('headspace_secret_marker', _hs8697_test_json.dumps(public))
        self.assertNotIn('sample.truth', _hs8697_test_json.dumps(public))
        denied = self.device.read(state_ids=['sample.truth'])
        self.assertEqual(denied['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(denied['observations'], {})
        self.assertEqual(self.judge.read(state_ids=['sample.truth'])['observations']['sample.truth']['value']['fixture_01']['private_species'], 'headspace_secret_marker')
        self.unchanged('measure', {'oven_slot': 'oven_01', 'visibility': 'evaluator'}, code='INVALID_ARGUMENT')
        for state_id in ('sample.truth', 'tray.occupancy', 'gc.connected', 'measurement.count', 'not_a_state'):
            before = self.device.debug_snapshot()
            self.assertEqual(self.device.write(state_id=state_id, value=1, request_id=state_id)['error']['code'], 'ACCESS_DENIED')
            self.assertEqual(before, self.device.debug_snapshot())
        with self.assertRaises(ValueError):
            self.device.session('forged')
        with self.assertRaises(ValueError):
            AgilentHS8697Session(self.device, 'forged')

    def test_write_routes_fixed_zone_and_reuses_invoke_deduplication(self):
        result = self.device.write(state_id='power.enabled', value=True, request_id='write_power')
        self.assertEqual(result['status'], 'succeeded')
        same = self.device.invoke(capability_id='power.set', arguments={'enabled': True}, request_id='write_power')
        self.assertEqual(result, same)
        for zone in ('oven', 'loop_valve', 'transfer_line'):
            result = self.device.write(state_id='thermal.' + zone + '.setpoint_k', value=333.15, request_id='write_' + zone)
            self.assertEqual(result['result'], {'zone': zone, 'temperature_K': 333.15})
            self.assertEqual(self.device.read(state_ids=['thermal.' + zone + '.setpoint_k'])['observations']['thermal.' + zone + '.setpoint_k']['value'], 333.15)
        self.assertEqual(self.device.write(state_id='method.equilibration_time_s', value=0.2, request_id='write_bad')['error']['code'], 'INVALID_ARGUMENT')

    def test_double_instance_occupancy_log_requests_and_return_values_are_isolated(self):
        other = AgilentHS8697('hs_b', scene_adapter=lambda *args: None, ambient_temperature_K=293.15)
        other.register_sample('fixture_01', volume_m3=22e-6)
        other_initial = other.debug_snapshot()
        self.ready()
        self.ok('measure', {'oven_slot': 'oven_01'})
        self.assertEqual(other_initial, other.debug_snapshot())
        denied = self.device.invoke(instance_id='hs_b', capability_id='power.set', arguments={'enabled': False}, request_id='cross')
        self.assertEqual(denied['error']['code'], 'UNKNOWN_INSTANCE')
        self.assertEqual(self.device.read(instance_id='hs_b')['error']['code'], 'UNKNOWN_INSTANCE')
        self.assertEqual(self.device.write(instance_id='hs_b', state_id='power.enabled', value=False, request_id='cross')['error']['code'], 'UNKNOWN_INSTANCE')
        public = self.device.read()
        public['observations']['oven.occupancy']['value'][0] = 'tampered'
        self.assertEqual(self.device.read()['observations']['oven.occupancy']['value'][0], 'fixture_01')
        metadata = self.device.describe()
        metadata['state_slots'].clear()
        self.assertTrue(self.device.describe()['state_slots'])

    def test_scene_callback_failure_has_no_process_commit_is_cached_and_redacted(self):
        calls = []
        def fail(instance, state):
            calls.append(instance)
            raise RuntimeError('private_scene_failure_marker')
        device = AgilentHS8697('broken', scene_adapter=fail)
        args = {'capability_id': 'lid.set_angle', 'arguments': {'angle_rad': 1}, 'request_id': 'failed'}
        before = device.debug_snapshot()
        failed = device.invoke(**args)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['error']['side_effects'], 'unknown')
        self.assertEqual(failed['error']['code'], 'DEVICE_ERROR')
        self.assertNotIn('private_scene_failure_marker', _hs8697_test_json.dumps(failed))
        self.assertEqual(before, device.debug_snapshot())
        self.assertEqual(failed, device.invoke(**args))
        self.assertEqual(calls, ['broken'])

    def test_scene_complete_state_explicit_sync_and_unbound_capabilities(self):
        self.ready()
        self.assertTrue(all(call[0] == 'hs_a' for call in self.scene_calls))
        self.assertEqual(self.scene_calls[-1][1]['manipulator.position_slot'], 'oven_01')
        self.assertEqual(self.scene_calls[-1][1]['oven.occupancy'][0], 'fixture_01')
        self.device.sync_scene()
        self.device.sync_scene()
        self.assertEqual(self.scene_calls[-1], self.scene_calls[-2])
        unbound = AgilentHS8697('unbound')
        before = unbound.debug_snapshot()
        for cid, arguments in [('lid.set_angle', {'angle_rad': 1}), ('sample.load', {'sample_id': 'x', 'slot_id': 'tray_01'}), ('sample.unload', {'slot_id': 'tray_01'}), ('sample.transfer', {'source_slot': 'tray_01', 'target_slot': 'oven_01'})]:
            self.assertNotIn(cid, unbound.describe()['capabilities'])
            self.assertEqual(unbound.invoke(capability_id=cid, arguments=arguments, request_id=cid)['status'], 'unsupported')
        self.assertEqual(before, unbound.debug_snapshot())
        with self.assertRaises(RuntimeError):
            unbound.sync_scene()

    def test_invalid_input_types_units_and_unsupported_operations(self):
        for cid, args in [('power.set', {'enabled': 1}), ('lid.set_angle', {'angle_rad': True}), ('lid.set_angle', {'angle_rad': -1}), ('lid.set_angle', {'angle_rad': 90}), ('thermal.set_temperature', {'zone': 'bad', 'temperature_K': 333.15}), ('thermal.set_temperature', {'zone': 'oven', 'temperature_C': 60}), ('sample.load', {'sample_id': '', 'slot_id': 'tray_01'}), ('sample.load', {'sample_id': 'fixture_01', 'slot_id': 1})]:
            self.unchanged(cid, args, code='INVALID_ARGUMENT')
        self.unchanged('missing.capability', {}, code='UNKNOWN_CAPABILITY')
        for cid in ('pressure.set', 'shaking.set', 'hardware.connect', 'robot.execute', 'headspace.physical_inject'):
            self.unchanged(cid, {}, status='unsupported', code='UNSUPPORTED')
            self.assertNotIn(cid, self.device.describe()['capabilities'])
        self.assertEqual(self.device.describe(asset_type_id='wrong')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(self.device.describe(asset_type_id='instrument.agilent_hs8697', instance_id='hs_a')['error']['code'], 'INVALID_ARGUMENT')

    def test_host_registration_validation_and_unknown_specifications(self):
        before = self.device.debug_snapshot()
        for kwargs in ({'volume_m3': 20}, {'volume_m3': True}, {'volume_m3': 20e-6, 'sealed': 1}, {'volume_m3': 20e-6, 'truth': []}, {'volume_m3': float('nan')}):
            with self.assertRaises(ValueError):
                self.device.register_sample('invalid', **kwargs)
            self.assertEqual(before, self.device.debug_snapshot())
        truth = {'private': [1]}
        self.device.register_sample('copied', volume_m3=22e-6, truth=truth)
        truth['private'][0] = 999
        self.assertEqual(self.device.debug_snapshot()['registered_samples']['copied']['truth']['private'], [1])
        for args in [('', {}), ('gc', {'carrier_gas_available': 1})]:
            with self.assertRaises(ValueError):
                self.device.register_gc(args[0], **args[1])
        for value in (float('nan'), -1, True):
            with self.assertRaises(ValueError):
                self.device.set_ambient_temperature(value)
        parameters = self.metadata['parameters']
        self.assertEqual(parameters['tray_capacity']['value'], 48)
        self.assertEqual(parameters['oven_capacity']['value'], 12)
        self.assertEqual(parameters['vial_volumes_m3']['value'], [10e-6, 20e-6, 22e-6])
        for name in ('pressure_pa', 'shaking_hz', 'loop_volume_m3', 'physical_transfer_mol', 'thermal_response_s', 'measurement_duration_s'):
            self.assertIsNone(parameters[name]['value'])
            self.assertEqual(parameters[name]['evidence'], 'unknown')


# CHEM-097 appended verification; no vendor-calibrated sample response is claimed.
import unittest as _hplc_test_unittest
import copy as _hplc_test_copy
import json as _hplc_test_json
from basic40_runtime_analytical import (AgilentHPLC1260Runtime, AGILENT_HPLC1260_DOOR_MAX_RAD,
    AGILENT_HPLC1260_FLOW_MIN_M3_S, AGILENT_HPLC1260_FLOW_MAX_M3_S,
    export_agilent_hplc1260_contract_metadata)


class TestAgilentHPLC1260Runtime(_hplc_test_unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.device = AgilentHPLC1260Runtime("lc_a", scene_adapter=lambda instance, state: self.calls.append((instance, state)))
        self.device.register_sample("sample", truth={"private_species": "hplc_private_marker", "private_mass_kg": 1e-9})
        self.device.register_principal("judge", visibility="evaluator")
        self.judge = self.device.session("judge")
        self.request = 0

    def call(self, capability, **arguments):
        self.request += 1
        return self.device.invoke(capability_id=capability, arguments=arguments, request_id="hplc_" + str(self.request))

    def ok(self, capability, **arguments):
        response = self.call(capability, **arguments)
        self.assertIn(response["status"], ("succeeded", "accepted"), response)
        return response

    def method(self, **overrides):
        result = _hplc_test_copy.deepcopy(export_agilent_hplc1260_contract_metadata()["capabilities"]["chromatography.set_method"]["example_arguments"][0])
        result.update(overrides)
        return result

    def value(self, sid):
        return self.device.read(state_ids=[sid])["observations"][sid]["value"]

    def ready(self, **method):
        self.ok("power.set", enabled=True)
        self.ok("door.set_angle", angle_rad=AGILENT_HPLC1260_DOOR_MAX_RAD)
        self.ok("sample.load", sample_id="sample")
        self.ok("door.set_angle", angle_rad=0)
        self.ok("mobile_phase.set_available", available=True)
        self.ok("chromatography.set_method", **self.method(**method))

    def unchanged(self, capability, arguments, code=None, status="rejected"):
        before = self.device.debug_snapshot()
        calls = _hplc_test_copy.deepcopy(self.calls)
        response = self.call(capability, **arguments)
        self.assertEqual(response["status"], status, response)
        if code:
            self.assertEqual(response["error"]["code"], code)
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(calls, self.calls)
        return response

    def test_method_enforces_catalog_si_bounds_and_ambient_relative_lower_limit(self):
        self.ok("power.set", enabled=True)
        for flow in (AGILENT_HPLC1260_FLOW_MIN_M3_S, AGILENT_HPLC1260_FLOW_MAX_M3_S):
            self.ok("chromatography.set_method", **self.method(flow_m3_s=flow))
            self.assertEqual(self.value("method.flow_m3_s"), flow)
        for volume in (1e-10, 1e-7):
            self.ok("chromatography.set_method", **self.method(injection_volume_m3=volume))
        for temp in (288.15, 358.15):
            self.ok("chromatography.set_method", **self.method(column_temperature_K=temp))
            self.assertEqual(self.value("method.column_temperature_k"), temp)
        self.ok("chromatography.set_method", **self.method(pressure_limit_Pa=60_000_000))
        invalid = [{"flow_m3_s": 0}, {"flow_m3_s": AGILENT_HPLC1260_FLOW_MAX_M3_S * 1.0001},
            {"injection_volume_m3": 0.09e-9}, {"injection_volume_m3": 1.001e-7},
            {"column_temperature_K": 288.14}, {"column_temperature_K": 358.16},
            {"pressure_limit_Pa": 60_000_001}, {"pressure_limit_Pa": 0}, {"wavelength_m": 0},
            {"run_duration_s": 0}, {"sample_interval_s": 0}, {"sample_interval_s": 61},
            {"sample_interval_s": 0.0001}, {"integration_method": "calibrated"}]
        for overrides in invalid:
            self.unchanged("chromatography.set_method", self.method(**overrides), "INVALID_ARGUMENT")
        warm = AgilentHPLC1260Runtime("warm", ambient_K=308.15)
        warm.invoke(capability_id="power.set", arguments={"enabled": True}, request_id="on")
        response = warm.invoke(capability_id="chromatography.set_method", arguments=self.method(column_temperature_K=298.14), request_id="cold")
        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")

    def test_malformed_nonfinite_boolean_and_gradient_parameters_reject_atomically(self):
        self.ok("power.set", enabled=True)
        for value in (True, "1", float("nan"), float("inf"), -float("inf"), None):
            self.unchanged("chromatography.set_method", self.method(flow_m3_s=value), "INVALID_ARGUMENT")
        for gradient in ([], [{"time_s": 1, "fraction_b": 0}], [{"time_s": 0, "fraction_b": 1.1}],
            [{"time_s": 0, "fraction_b": 0}, {"time_s": 0, "fraction_b": 1}],
            [{"time_s": 0, "fraction_b": 0}, {"time_s": 61, "fraction_b": 1}],
            [{"time_s": 0, "fraction_b": 0, "truth": "secret"}]):
            self.unchanged("chromatography.set_method", self.method(gradient=gradient), "INVALID_ARGUMENT")
        self.unchanged("power.set", {"enabled": True, "role": "evaluator"}, "INVALID_ARGUMENT")
        self.unchanged("door.set_angle", {"angle_rad": True}, "INVALID_ARGUMENT")

    def test_loading_and_run_preconditions_and_scene_updates(self):
        self.unchanged("sample.load", {"sample_id": "sample"}, "PRECONDITION_FAILED")
        self.unchanged("chromatography.measure", {}, "PRECONDITION_FAILED")
        self.ok("power.set", enabled=True)
        self.ok("door.set_angle", angle_rad=AGILENT_HPLC1260_DOOR_MAX_RAD)
        self.unchanged("sample.unload", {}, "PRECONDITION_FAILED")
        self.unchanged("sample.load", {"sample_id": "unknown"}, "PRECONDITION_FAILED")
        self.ok("sample.load", sample_id="sample")
        self.unchanged("sample.load", {"sample_id": "sample"}, "PRECONDITION_FAILED")
        self.ok("chromatography.set_method", **self.method())
        self.unchanged("chromatography.measure", {}, "PRECONDITION_FAILED")
        self.ok("mobile_phase.set_available", available=True)
        self.unchanged("chromatography.measure", {}, "PRECONDITION_FAILED")
        self.ok("door.set_angle", angle_rad=0)
        run = self.ok("chromatography.measure")
        self.assertEqual(run["status"], "accepted")
        self.assertEqual(self.calls[-1][1]["run.status"], "running")
        self.assertEqual(self.calls[-1][1]["door.angle_rad"], 0)
        self.assertTrue(self.calls[-1][1]["sample.loaded"])

    def test_async_run_completes_only_on_host_time_and_integrates_synthetic_signal(self):
        self.ready()
        started = self.ok("chromatography.measure")
        op = started["operation_id"]
        self.assertEqual(self.device.poll(operation_id=op)["status"], "running")
        self.assertEqual(self.value("measurement.count"), 0)
        self.device.host_advance(20.5)
        partial = self.ok("chromatography.get_chromatogram")["result"]["chromatogram"]
        self.assertEqual(partial["time_s"], list(range(21)))
        self.assertFalse(partial["complete"])
        self.assertGreater(partial["signal"][20], 0.99)
        self.device.host_advance(60)
        completed = self.device.poll(operation_id=op)
        result = completed["result"]["chromatogram"]
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(result["time_s"], list(range(61)))
        self.assertEqual(self.value("measurement.count"), 1)
        self.assertTrue(result["synthetic"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["signal_unit"], "1")
        self.assertAlmostEqual(result["integrated_signal_s"], (2.0 + 0.7 * 3.0) * (2 * _dr6000_test_math.pi) ** 0.5, places=5)
        self.assertIsNone(result["purity_fraction"])
        self.assertIsNone(result["calibrated_concentration_mol_m3"])
        self.device.host_advance(90)
        self.assertEqual(self.value("measurement.count"), 1)

    def test_read_poll_describe_retrieval_do_not_advance_time_or_mutate_state(self):
        self.ready()
        op = self.ok("chromatography.measure")["operation_id"]
        before = self.device.debug_snapshot()
        for _ in range(3):
            self.device.read()
            self.device.describe()
            self.device.poll(operation_id=op)
            self.ok("chromatography.get_chromatogram")
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(self.value("run.elapsed_s"), 0)
        self.assertEqual(self.device.read()["clock"]["time_s"], 0)

    def test_busy_controls_rejected_without_process_or_scene_effects(self):
        self.ready()
        self.ok("chromatography.measure")
        for capability, arguments in (("power.set", {"enabled": False}), ("door.set_angle", {"angle_rad": 1}),
            ("sample.unload", {}), ("sample.load", {"sample_id": "sample"}),
            ("mobile_phase.set_available", {"available": False}), ("chromatography.set_method", self.method()),
            ("chromatography.measure", {})):
            self.unchanged(capability, arguments, "BUSY")

    def test_cancel_keeps_partial_data_and_old_operations_are_stable(self):
        self.ready()
        first_op = self.ok("chromatography.measure")["operation_id"]
        self.device.host_advance(12.5)
        before = self.value("measurement.chromatogram")
        cancelled = self.device.cancel(operation_id=first_op, request_id="cancel_once")
        self.assertEqual(cancelled["result"]["run_status"], "cancelled")
        self.assertEqual(self.value("measurement.chromatogram"), before)
        self.assertEqual(self.value("measurement.count"), 0)
        self.assertEqual(self.device.cancel(operation_id=first_op, request_id="cancel_once"), cancelled)
        self.device.host_advance(20)
        self.assertEqual(self.value("run.elapsed_s"), 12.5)
        second_op = self.ok("chromatography.measure")["operation_id"]
        self.assertNotEqual(first_op, second_op)
        self.device.host_advance(80)
        self.assertEqual(self.device.poll(operation_id=first_op)["result"]["chromatogram"], before)
        self.assertEqual(self.device.poll(operation_id=first_op)["status"], "cancelled")
        self.assertEqual(self.device.poll(operation_id=second_op)["status"], "succeeded")

    def test_stop_then_unload_and_write_routes(self):
        self.ready()
        self.ok("chromatography.measure")
        self.device.host_advance(5)
        self.ok("chromatography.stop")
        response = self.device.write(state_id="door.angle_rad", value=AGILENT_HPLC1260_DOOR_MAX_RAD, request_id="write_door")
        self.assertEqual(response["status"], "succeeded")
        self.ok("sample.unload")
        self.assertFalse(self.value("sample.loaded"))
        self.assertIsNone(self.value("sample.id"))
        self.assertFalse(self.calls[-1][1]["sample.loaded"])
        self.assertEqual(self.device.write(state_id="method.flow_m3_s", value=1e-8, request_id="partial_method")["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.judge.write(state_id="sample.truth", value={}, request_id="truth_write")["error"]["code"], "ACCESS_DENIED")

    def test_agent_truth_filter_applies_to_all_public_surfaces(self):
        self.ready()
        op = self.ok("chromatography.measure")["operation_id"]
        self.device.host_advance(10)
        results = [self.device.describe(), self.device.read(), self.device.poll(operation_id=op), self.calls,
            self.device.scene_bindings(), self.ok("chromatography.get_chromatogram")]
        denied = self.device.read(state_ids=["sample.truth"])
        unknown = self.device.read(state_ids=["nonexistent.truth"])
        self.assertEqual(denied["error"], unknown["error"])
        results += [denied, unknown, self.call("chromatography.measure", role="evaluator")]
        encoded = _hplc_test_json.dumps(results)
        for secret in ("sample.truth", "private_species", "private_mass_kg", "hplc_private_marker"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(self.judge.read(state_ids=["sample.truth"])["observations"]["sample.truth"]["value"]["private_species"], "hplc_private_marker")
        with self.assertRaises(TypeError):
            self.device.read(role="evaluator")
        with self.assertRaises(ValueError):
            self.device.session("unregistered")

    def test_operations_bound_to_instance_and_principal(self):
        self.ready()
        op = self.ok("chromatography.measure")["operation_id"]
        self.assertEqual(self.judge.poll(operation_id=op)["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.judge.cancel(operation_id=op, request_id="foreign_cancel")["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.judge.invoke(capability_id="chromatography.stop", arguments={}, request_id="foreign_stop")["error"]["code"], "ACCESS_DENIED")
        other = AgilentHPLC1260Runtime("lc_b")
        self.assertEqual(other.poll(operation_id=op)["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.device.poll(operation_id=op, instance_id="lc_b")["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(self.device.invoke(instance_id="lc_b", capability_id="power.set", arguments={"enabled": True}, request_id="wrong_instance")["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertFalse(other.read()["observations"]["power.enabled"]["value"])
        self.assertEqual(other.debug_snapshot()["registered_samples"], {})
        self.assertEqual(other.read()["clock"]["time_s"], 0)

    def test_request_replay_conflict_rejections_and_defensive_copies(self):
        request = {"capability_id": "chromatography.measure", "arguments": {}, "request_id": "rejected_once"}
        rejected = self.device.invoke(**request)
        self.ready()
        self.assertEqual(self.device.invoke(**request), rejected)
        request["request_id"] = "start_once"
        started = self.device.invoke(**request)
        before, count = self.device.debug_snapshot(), len(self.calls)
        self.assertEqual(self.device.invoke(**request), started)
        self.assertEqual(len(self.calls), count)
        conflict = self.device.invoke(capability_id="power.set", arguments={"enabled": False}, request_id="start_once")
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        observed = self.device.read()
        observed["observations"]["measurement.chromatogram"]["value"]["signal"][0] = 99
        metadata = self.device.describe()
        metadata["capabilities"]["power.set"]["availability"] = "unsupported"
        self.assertEqual(before, self.device.debug_snapshot())
        self.assertEqual(self.device.describe()["capabilities"]["power.set"]["availability"], "implemented")

    def test_host_time_validation_partition_independence_and_failed_sync_atomicity(self):
        self.ready(sample_interval_s=7)
        self.ok("chromatography.measure")
        for t in (1, 2.5, 17, 28, 50, 60):
            self.device.host_advance(t)
        expected = self.value("measurement.chromatogram")
        other = AgilentHPLC1260Runtime("lc_partition", scene_adapter=lambda *args: None)
        other.register_sample("sample")
        for i, (cap, args) in enumerate((("power.set", {"enabled": True}), ("door.set_angle", {"angle_rad": AGILENT_HPLC1260_DOOR_MAX_RAD}),
            ("sample.load", {"sample_id": "sample"}), ("door.set_angle", {"angle_rad": 0}),
            ("mobile_phase.set_available", {"available": True}), ("chromatography.set_method", self.method(sample_interval_s=7)), ("chromatography.measure", {}))):
            self.assertIn(other.invoke(capability_id=cap, arguments=args, request_id=str(i))["status"], ("succeeded", "accepted"))
        other.host_advance(60)
        self.assertEqual(expected, other.read(state_ids=["measurement.chromatogram"])["observations"]["measurement.chromatogram"]["value"])
        self.assertEqual(expected["time_s"][-1], 60)
        for invalid in (True, float("nan"), float("inf"), -1, 59):
            before = self.device.debug_snapshot()
            with self.assertRaises(ValueError):
                self.device.host_advance(invalid)
            self.assertEqual(before, self.device.debug_snapshot())

    def test_scene_failure_does_not_commit_and_failed_request_does_not_repeat(self):
        attempts = []
        def failure(instance, state):
            attempts.append(instance)
            raise RuntimeError("hplc_private_marker")
        broken = AgilentHPLC1260Runtime("broken_lc", scene_adapter=failure)
        request = {"capability_id": "door.set_angle", "arguments": {"angle_rad": 1}, "request_id": "failure_once"}
        before = broken.debug_snapshot()
        response = broken.invoke(**request)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("hplc_private_marker", _hplc_test_json.dumps(response))
        self.assertEqual(broken.invoke(**request), response)
        self.assertEqual(attempts, ["broken_lc"])
        self.assertEqual(before, broken.debug_snapshot())
        self.ready()
        self.ok("chromatography.measure")
        self.device._scene_adapter = failure
        before = self.device.debug_snapshot()
        with self.assertRaises(RuntimeError):
            self.device.host_advance(10)
        self.assertEqual(before, self.device.debug_snapshot())

    def test_unbound_scene_and_unimplemented_quantitation_do_not_report_success(self):
        unbound = AgilentHPLC1260Runtime("unbound_lc")
        for capability, arguments in (("door.set_angle", {"angle_rad": 1}), ("sample.load", {"sample_id": "sample"}), ("sample.unload", {})):
            self.assertNotIn(capability, unbound.describe()["capabilities"])
            self.assertEqual(unbound.invoke(capability_id=capability, arguments=arguments, request_id=capability)["status"], "unsupported")
        for capability in ("chromatography.calibrate", "chromatography.quantify", "chromatography.purity", "hardware.connect", "robot.load_sample"):
            self.unchanged(capability, {}, status="unsupported")
            self.assertNotIn(capability, self.device.describe()["capabilities"])
        self.assertIsNone(self.value("pump.actual_pressure_pa"))
        self.assertIsNone(self.value("column_oven.actual_temperature_k"))
        with self.assertRaises(RuntimeError):
            unbound.sync_scene()

    def test_host_fixture_registration_is_atomic_and_copies_private_data(self):
        for arguments in ({"peaks": [[0, 0, 1]]}, {"peaks": [[0, 1, float("nan")]]}, {"truth": "secret"}, {"peaks": [[0, 1, -1]]}):
            before = self.device.debug_snapshot()
            with self.assertRaises(ValueError):
                self.device.register_sample("invalid", **arguments)
            self.assertEqual(before, self.device.debug_snapshot())
        truth = {"secret": [1]}
        self.device.register_sample("copied", truth=truth)
        truth["secret"][0] = 999
        self.assertEqual(self.device.debug_snapshot()["registered_samples"]["copied"]["truth"]["secret"], [1])
        metadata = export_agilent_hplc1260_contract_metadata()
        self.assertIsNone(metadata["parameters"]["wavelength_range_m"]["value"])
        self.assertIsNone(metadata["parameters"]["acquisition_duration_s"]["value"])
        self.assertTrue(all(sid == sid.lower() for sid in metadata["state_slots"]))
        self.assertEqual(self.device.describe(asset_type_id=metadata["asset_type_id"])["asset_type_id"], metadata["asset_type_id"])


# CHEM-010 append-only runtime tests. The fixtures below are explicitly synthetic.
import importlib.util as _epp_test_importlib
import json as _epp_test_json
import math as _epp_test_math
from basic40_runtime_analytical import Eppendorf5910Ri, Eppendorf5910RiSession, EPPENDORF_5910RI_TYPE, EPPENDORF_5910RI_LID_MAX_RAD, export_eppendorf_5910ri_contract_metadata

_epp_asset_tool_path = _Dr6000TestPath(__file__).resolve().parents[1] / "skills/chemistry-lab-assets/scripts/asset_tool.py"
_epp_asset_tool_spec = _epp_test_importlib.spec_from_file_location("_epp_independent_asset_tool", _epp_asset_tool_path)
_epp_asset_tool = _epp_test_importlib.module_from_spec(_epp_asset_tool_spec)
_epp_asset_tool_spec.loader.exec_module(_epp_asset_tool)


class TestEppendorf5910RiRuntime(_dr6000_test_unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = self.make_host("centrifuge_a", self.calls)
        self.api = self.host.session()
        self.counter = 0
        self.metadata = export_eppendorf_5910ri_contract_metadata()

    def make_host(self, instance_id, calls=None, process=True):
        callback = lambda iid, state: calls.append((iid, _dr6000_test_copy.deepcopy(state))) if calls is not None else None
        host = Eppendorf5910Ri(instance_id, scene_adapter=callback)
        for configuration, limit in (("large_capacity", 100.0), ("fixed_angle", 50.0)):
            host.configure_synthetic_rotor(configuration, slot_positions_xy_m={"slot_1": [0.1, 0], "slot_2": [-0.1, 0], "slot_3": [0, 0.1], "slot_4": [0, -0.1]},
                operating_speed_limit_rad_s=limit, imbalance_threshold_kg_m=1e-9, evidence="synthetic")
        if process:
            host.configure_synthetic_process(acceleration_rad_s2=[4.0] * 10, braking_rad_s2=[5.0] * 10,
                poweroff_deceleration_rad_s2=2.0, temperature_time_constant_s=20.0, ambient_temperature_k=295.15,
                initial_temperature_k=293.15, sample_interval_s=1.0, evidence="synthetic")
        for sample_id, mass in (("sample_a", 0.02), ("sample_b", 0.02), ("sample_unequal", 0.021), ("sample_unknown", None)):
            host.register_sample(sample_id, mass_kg=mass, truth={"private_marker": "CHEM010_SECRET_NEVER_PUBLIC", "species": ["private"]})
        return host

    def method(self, **updates):
        values = {"speed_rad_s": 20.0, "temperature_k": 283.15, "duration_s": 10.0, "acceleration_level": 1, "braking_level": 1}
        values.update(updates)
        return values

    def call(self, capability, arguments=None, **kwargs):
        self.counter += 1
        return self.api.invoke(capability_id=capability, arguments=arguments if arguments is not None else kwargs, request_id="epp_" + str(self.counter))

    def success(self, capability, arguments=None, **kwargs):
        response = self.call(capability, arguments, **kwargs)
        self.assertIn(response["status"], ("succeeded", "accepted"), response)
        schema = self.metadata["capabilities"][capability]["output_schema"]
        self.assertEqual(_epp_asset_tool.validate_value(response["result"], schema), [])
        return response

    def value(self, state_id):
        return self.api.read(state_ids=[state_id])["observations"][state_id]["value"]

    def ready(self, *, second="sample_b", configuration="large_capacity", speed_rad_s=20.0):
        self.success("power.set", enabled=True)
        self.success("lid.set_angle", angle_rad=EPPENDORF_5910RI_LID_MAX_RAD)
        self.success("rotor.select", configuration=configuration)
        self.success("sample.load", slot_id="slot_1", sample_id="sample_a")
        self.success("sample.load", slot_id="slot_2", sample_id=second)
        self.success("lid.set_angle", angle_rad=0.0)
        self.success("centrifuge.set_method", self.method(speed_rad_s=speed_rad_s))

    def rejected_unchanged(self, capability, arguments, code):
        snapshot, calls = self.host.debug_snapshot(), _dr6000_test_copy.deepcopy(self.calls)
        response = self.call(capability, arguments)
        self.assertIn(response["status"], ("rejected", "unsupported"), response)
        self.assertEqual(response["error"]["code"], code, response)
        self.assertEqual(response["error"]["side_effects"], "none")
        self.assertEqual(snapshot, self.host.debug_snapshot())
        self.assertEqual(calls, self.calls)
        return response

    def test_metadata_unknown_evidence_configuration_separation_and_schema_validity(self):
        metadata = self.metadata
        self.assertEqual(metadata["parameters"]["duration_range_s"]["value"], [10, 359940])
        self.assertAlmostEqual(metadata["parameters"]["speed_range_rad_s"]["value"][1], 14000 * 2 * _epp_test_math.pi / 60)
        for key in ("rotor_radius_m", "rotor_mass_kg", "rotor_inertia_kg_m2", "loaded_sample_mass_kg", "imbalance_threshold_kg_m", "temperature_time_constant_s", "slot_count"):
            self.assertIsNone(metadata["parameters"][key]["value"])
            self.assertEqual(metadata["parameters"][key]["evidence"], "unknown")
        self.assertEqual(set(metadata["rotor_configurations"]), {"large_capacity", "fixed_angle"})
        for profile in metadata["rotor_configurations"].values():
            self.assertTrue(all(value is None for value in profile.values()))
        for cap in metadata["capabilities"].values():
            self.assertEqual(_epp_asset_tool.lint_schema(cap["input_schema"]), [])
            self.assertEqual(_epp_asset_tool.lint_schema(cap["output_schema"]), [])
        self.assertEqual(self.api.describe(asset_type_id=EPPENDORF_5910RI_TYPE)["asset_type_id"], EPPENDORF_5910RI_TYPE)
        self.assertNotIn("host_api", self.api.describe())
        self.assertNotIn("sample.truth", self.api.describe()["state_slots"])
        self.assertNotIn("process.truth", self.api.describe()["state_slots"])

    def test_exact_si_method_bounds_and_strict_invalid_inputs(self):
        self.success("power.set", enabled=True)
        for speed, temp, duration, level in ((100 * 2 * _epp_test_math.pi / 60, 262.15, 10, 1), (14000 * 2 * _epp_test_math.pi / 60, 313.15, 359940, 10)):
            self.success("centrifuge.set_method", self.method(speed_rad_s=speed, temperature_k=temp, duration_s=duration, acceleration_level=level, braking_level=level))
        for key, invalids in {"speed_rad_s": [0, 1500, True, "100", float("nan"), float("inf")], "temperature_k": [262.14, 313.16, False],
            "duration_s": [9.999, 359941, True], "acceleration_level": [0, 11, 1.0, True], "braking_level": [0, 11, False]}.items():
            for invalid in invalids:
                self.rejected_unchanged("centrifuge.set_method", self.method(**{key: invalid}), "INVALID_ARGUMENT")
        self.rejected_unchanged("centrifuge.set_method", dict(self.method(), truth={"secret": 1}), "INVALID_ARGUMENT")
        self.rejected_unchanged("centrifuge.set_method", {}, "INVALID_ARGUMENT")
        self.rejected_unchanged("power.set", {"enabled": 1}, "INVALID_ARGUMENT")
        self.rejected_unchanged("lid.set_angle", {"angle_rad": -1}, "INVALID_ARGUMENT")

    def test_host_parameters_required_no_invented_defaults(self):
        fresh = Eppendorf5910Ri("unconfigured", scene_adapter=lambda *args: None)
        self.assertIsNone(fresh.read()["observations"]["display.temperature_k"]["value"])
        self.assertEqual(fresh.read()["observations"]["display.speed_rad_s"]["quality"], "unavailable")
        fresh.invoke(capability_id="lid.set_angle", arguments={"angle_rad": EPPENDORF_5910RI_LID_MAX_RAD}, request_id="open")
        self.assertEqual(fresh.invoke(capability_id="rotor.select", arguments={"configuration": "large_capacity"}, request_id="select")["error"]["code"], "MISSING_PARAMETER")
        self.host = self.make_host("no_process", self.calls, process=False)
        self.api = self.host.session()
        self.ready()
        self.rejected_unchanged("centrifuge.measure", {}, "MISSING_PARAMETER")

    def test_unknown_sample_mass_blocks_run_without_disclosure(self):
        self.ready(second="sample_unknown")
        self.assertIsNone(self.value("alarm.imbalance"))
        response = self.rejected_unchanged("centrifuge.measure", {}, "MISSING_PARAMETER")
        self.assertNotIn("sample_unknown", _epp_test_json.dumps(response))
        self.assertNotIn("CHEM010_SECRET_NEVER_PUBLIC", _epp_test_json.dumps(response))

    def test_balanced_normal_run_exact_linear_motion_temperature_and_curve(self):
        self.ready()
        self.assertFalse(self.value("alarm.imbalance"))
        start = self.success("centrifuge.measure")
        self.assertEqual(start["status"], "accepted")
        self.assertTrue(self.value("lid.locked"))
        self.host.host_advance(2.0)
        self.assertAlmostEqual(self.value("display.speed_rad_s"), 8.0)
        self.assertAlmostEqual(self.value("rotor.angle_rad"), 8.0)
        self.assertAlmostEqual(self.value("display.temperature_k"), 283.15 + 10 * _epp_test_math.exp(-0.1))
        self.host.host_advance(10)
        self.assertEqual(self.value("run.status"), "stopping")
        self.assertAlmostEqual(self.value("rotor.angle_rad"), 150.0)
        self.host.host_advance(14)
        self.assertEqual(self.value("run.status"), "succeeded")
        self.assertEqual(self.value("display.speed_rad_s"), 0.0)
        self.assertFalse(self.value("lid.locked"))
        self.assertAlmostEqual(self.value("rotor.angle_rad"), 190.0)
        curve = self.success("centrifuge.get_curve")["result"]["curve"]
        self.assertEqual(curve["time_s"], list(range(15)))
        self.assertTrue(curve["complete"] and curve["synthetic"])
        self.assertEqual(curve["quality"], "estimated")
        self.assertIsNone(curve["physical_separation_model"])
        self.assertEqual(self.api.poll(operation_id=start["operation_id"])["status"], "succeeded")
        self.success("lid.set_angle", angle_rad=EPPENDORF_5910RI_LID_MAX_RAD)
        self.success("sample.unload", slot_id="slot_1")
        self.success("sample.unload", slot_id="slot_2")
        self.assertEqual(self.value("sample.occupied_slots"), [])

    def test_interlocks_reject_open_load_select_method_while_rotating(self):
        self.ready()
        self.success("centrifuge.measure")
        for cap, args in (("lid.set_angle", {"angle_rad": 1}), ("sample.load", {"slot_id": "slot_3", "sample_id": "sample_unequal"}),
            ("sample.unload", {"slot_id": "slot_1"}), ("rotor.select", {"configuration": "fixed_angle"}), ("centrifuge.set_method", self.method()), ("centrifuge.measure", {})):
            self.rejected_unchanged(cap, args, "BUSY")
        self.host.host_advance(2)
        self.rejected_unchanged("lid.set_angle", {"angle_rad": 1}, "BUSY")

    def test_imbalance_alarm_is_preexisting_and_rejected_run_has_no_side_effects(self):
        self.ready(second="sample_unequal")
        self.assertTrue(self.value("alarm.imbalance"))
        rejected = self.rejected_unchanged("centrifuge.measure", {}, "IMBALANCE")
        payload = _epp_test_json.dumps([rejected, self.api.read(), self.api.describe(), self.calls])
        self.assertNotIn("CHEM010_SECRET_NEVER_PUBLIC", payload)
        self.assertNotIn("imbalance_kg_m", payload)
        self.assertNotIn("mass_kg", _epp_test_json.dumps(rejected))

    def test_rotor_categories_enforce_separate_explicit_synthetic_speed_limits(self):
        self.ready(configuration="fixed_angle", speed_rad_s=75)
        self.rejected_unchanged("centrifuge.measure", {}, "PRECONDITION_FAILED")
        self.success("centrifuge.set_method", self.method(speed_rad_s=50))
        self.success("centrifuge.measure")
        self.assertEqual(self.value("rotor.configuration"), "fixed_angle")
        self.rejected_unchanged("centrifuge.set_rcf", {"rcf": 22132}, "UNSUPPORTED")

    def test_cancel_retains_partial_results_and_continues_braking_until_unlock(self):
        self.ready()
        op = self.success("centrifuge.measure")["operation_id"]
        self.host.host_advance(2.5)
        before = self.value("measurement.curve")
        cancelled = self.api.cancel(operation_id=op, request_id="cancel_once")
        self.assertEqual(cancelled["status"], "succeeded")
        self.assertEqual(self.value("run.status"), "stopping")
        self.assertTrue(self.value("lid.locked"))
        self.assertEqual(self.value("measurement.curve"), before)
        self.assertEqual(self.api.cancel(operation_id=op, request_id="cancel_once"), cancelled)
        self.host.host_advance(4)
        self.assertTrue(self.value("lid.locked"))
        self.host.host_advance(4.5)
        curve = self.value("measurement.curve")
        self.assertEqual(curve["time_s"][:len(before["time_s"])], before["time_s"])
        self.assertEqual(curve["speed_rad_s"][:len(before["speed_rad_s"])], before["speed_rad_s"])
        self.assertEqual(curve["time_s"][-1], 4.5)
        self.assertEqual(self.api.poll(operation_id=op)["status"], "cancelled")
        self.assertFalse(self.value("lid.locked"))
        self.assertEqual(self.value("display.speed_rad_s"), 0.0)

    def test_stop_direct_api_schema_and_partial_effects(self):
        self.ready()
        self.success("centrifuge.measure")
        self.host.host_advance(3)
        result = self.success("centrifuge.stop")["result"]
        self.assertEqual(result["run_status"], "stopping")
        angle_before = self.value("rotor.angle_rad")
        self.host.host_advance(5.4)
        self.assertEqual(self.value("run.status"), "cancelled")
        self.assertGreater(self.value("rotor.angle_rad"), angle_before)
        self.assertFalse(self.value("lid.locked"))

    def test_power_off_cannot_release_rotating_lid_and_preserves_curve(self):
        self.ready()
        op = self.success("centrifuge.measure")["operation_id"]
        self.host.host_advance(2)
        before = self.value("measurement.curve")
        self.success("power.set", enabled=False)
        self.assertFalse(self.value("power.enabled"))
        self.assertTrue(self.value("lid.locked"))
        self.assertEqual(self.value("display.speed_rad_s"), 8)
        self.rejected_unchanged("lid.set_angle", {"angle_rad": EPPENDORF_5910RI_LID_MAX_RAD}, "BUSY")
        self.host.host_advance(5)
        self.assertAlmostEqual(self.value("display.speed_rad_s"), 2)
        self.assertTrue(self.value("lid.locked"))
        self.host.host_advance(6)
        self.assertFalse(self.value("lid.locked"))
        self.assertEqual(self.api.poll(operation_id=op)["status"], "cancelled")
        self.assertEqual(self.value("measurement.curve")["speed_rad_s"][:3], before["speed_rad_s"])
        self.success("lid.set_angle", angle_rad=EPPENDORF_5910RI_LID_MAX_RAD)

    def test_reads_polls_queries_no_clock_progress_and_defensive_copies(self):
        self.ready()
        op = self.success("centrifuge.measure")["operation_id"]
        self.host.host_advance(2)
        before = self.host.debug_snapshot()
        for _ in range(3):
            read = self.api.read()
            self.api.poll(operation_id=op)
            self.success("centrifuge.get_curve")
            self.api.describe()
        read["observations"]["measurement.curve"]["value"]["speed_rad_s"][0] = 999
        self.assertEqual(before, self.host.debug_snapshot())
        for state_id, observation in self.api.read()["observations"].items():
            self.assertEqual(_epp_asset_tool.validate_value(observation["value"], self.metadata["state_slots"][state_id]["value_schema"]), [], state_id)

    def test_request_replay_conflict_and_failed_preconditions_are_cached(self):
        request = {"capability_id": "centrifuge.measure", "arguments": {}, "request_id": "old_failed"}
        rejected = self.api.invoke(**request)
        self.ready()
        self.assertEqual(self.api.invoke(**request), rejected)
        request["request_id"] = "start_once"
        accepted = self.api.invoke(**request)
        snapshot, count = self.host.debug_snapshot(), len(self.calls)
        self.assertEqual(self.api.invoke(**request), accepted)
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.api.invoke(capability_id="power.set", arguments={"enabled": False}, request_id="start_once")["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(snapshot, self.host.debug_snapshot())

    def test_principal_operation_and_instance_isolation_all_return_paths_filtered(self):
        self.ready()
        op = self.success("centrifuge.measure")["operation_id"]
        self.host.register_principal("other_agent")
        other_session = self.host.session("other_agent")
        self.assertEqual(other_session.poll(operation_id=op)["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(other_session.cancel(operation_id=op, request_id="foreign_cancel")["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(other_session.invoke(capability_id="centrifuge.stop", arguments={}, request_id="foreign_stop")["error"]["code"], "ACCESS_DENIED")
        other_host = self.make_host("centrifuge_b")
        self.assertEqual(other_host.poll(operation_id=op)["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(other_host.read()["observations"]["sample.loaded_slots"]["value"], [])
        self.assertEqual(other_host.read()["clock"]["time_s"], 0)
        self.assertEqual(self.api.read(instance_id="centrifuge_b")["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(self.api.invoke(instance_id="centrifuge_b", capability_id="power.set", arguments={"enabled": True}, request_id="cross")["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(self.api.read(state_ids=["process.truth"])["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(self.api.write(state_id="sample.truth", value={"secret": 1}, request_id="truth")["error"]["code"], "ACCESS_DENIED")
        with self.assertRaises(ValueError):
            Eppendorf5910RiSession(self.host, "unregistered")
        payload = _epp_test_json.dumps([self.api.describe(), self.api.read(), self.api.poll(operation_id=op), self.calls])
        self.assertNotIn("CHEM010_SECRET_NEVER_PUBLIC", payload)
        self.assertFalse(hasattr(self.api, "host_advance"))
        self.assertFalse(hasattr(self.api, "register_sample"))
        self.assertFalse(hasattr(self.api, "debug_snapshot"))

    def test_host_clock_partition_independence_and_nonmonotonic_rejection(self):
        self.ready()
        self.success("centrifuge.measure")
        for time_s in (0.1, 0.7, 2.2, 7.25, 10, 13.5, 14, 30):
            self.host.host_advance(time_s)
        curve, angle, temperature = self.value("measurement.curve"), self.value("rotor.angle_rad"), self.value("display.temperature_k")
        coarse = self.make_host("coarse")
        for i, (cap, args) in enumerate((("power.set", {"enabled": True}), ("lid.set_angle", {"angle_rad": EPPENDORF_5910RI_LID_MAX_RAD}),
            ("rotor.select", {"configuration": "large_capacity"}), ("sample.load", {"slot_id": "slot_1", "sample_id": "sample_a"}),
            ("sample.load", {"slot_id": "slot_2", "sample_id": "sample_b"}), ("lid.set_angle", {"angle_rad": 0}),
            ("centrifuge.set_method", self.method()), ("centrifuge.measure", {}))):
            self.assertIn(coarse.invoke(capability_id=cap, arguments=args, request_id=str(i))["status"], ("succeeded", "accepted"))
        coarse.host_advance(30)
        obs = coarse.read()["observations"]
        self.assertEqual(curve, obs["measurement.curve"]["value"])
        self.assertEqual(angle, obs["rotor.angle_rad"]["value"])
        self.assertAlmostEqual(temperature, obs["display.temperature_k"]["value"])
        for invalid in (True, float("nan"), float("inf"), -1, 29):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.host_advance(invalid)
            self.assertEqual(before, self.host.debug_snapshot())
        before = self.host.debug_snapshot()
        self.host.host_advance(30)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_scene_failure_atomicity_sanitization_and_same_request_no_retry(self):
        failures = []
        def fail(instance_id, state):
            failures.append(instance_id)
            raise RuntimeError("CHEM010_SCENE_PRIVATE_EXCEPTION")
        broken = Eppendorf5910Ri("broken", scene_adapter=fail)
        before = broken.debug_snapshot()
        request = {"capability_id": "lid.set_angle", "arguments": {"angle_rad": 1}, "request_id": "scene_failure"}
        response = broken.invoke(**request)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertEqual(before, broken.debug_snapshot())
        self.assertEqual(broken.invoke(**request), response)
        self.assertEqual(failures, ["broken"])
        self.assertNotIn("CHEM010_SCENE_PRIVATE_EXCEPTION", _epp_test_json.dumps(response))
        self.ready()
        self.success("centrifuge.measure")
        self.host._scene_adapter = fail
        before = self.host.debug_snapshot()
        with self.assertRaisesRegex(RuntimeError, "Scene synchronization failed"):
            self.host.host_advance(2)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_unsupported_unbound_scene_strict_shapes_write_routing_and_loading_preconditions(self):
        unbound = Eppendorf5910Ri("unbound")
        for capability, arguments in (("lid.set_angle", {"angle_rad": 1}), ("sample.load", {"slot_id": "slot_1", "sample_id": "x"}), ("centrifuge.measure", {})):
            self.assertNotIn(capability, unbound.describe()["capabilities"])
            self.assertEqual(unbound.invoke(capability_id=capability, arguments=arguments, request_id=capability)["status"], "unsupported")
        with self.assertRaises(RuntimeError):
            unbound.sync_scene()
        for capability in ("centrifuge.predict_sedimentation", "hardware.connect", "robot.load_sample"):
            self.rejected_unchanged(capability, {}, "UNSUPPORTED")
            self.assertNotIn(capability, self.api.describe()["capabilities"])
        self.assertEqual(self.api.write(state_id="power.enabled", value=True, request_id="write_power")["status"], "succeeded")
        self.assertTrue(self.value("power.enabled"))
        self.assertEqual(self.api.read(state_ids="power.enabled")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.api.invoke(capability_id="power.set", arguments=[], request_id="bad")["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.api.invoke(capability_id="power.set", arguments={"enabled": True}, request_id="")["error"]["code"], "INVALID_ARGUMENT")
        self.rejected_unchanged("sample.load", {"slot_id": "slot_1", "sample_id": "sample_a"}, "PRECONDITION_FAILED")
        self.ready()
        self.success("lid.set_angle", angle_rad=EPPENDORF_5910RI_LID_MAX_RAD)
        self.rejected_unchanged("sample.load", {"slot_id": "slot_3", "sample_id": "sample_a"}, "PRECONDITION_FAILED")
        self.rejected_unchanged("rotor.select", {"configuration": "fixed_angle"}, "PRECONDITION_FAILED")

    def test_host_fixture_validation_and_private_defensive_copies(self):
        with self.assertRaises(ValueError):
            self.host.configure_synthetic_rotor("large_capacity", slot_positions_xy_m={"a": [0, 0], "b": [1, 0]},
                operating_speed_limit_rad_s=50, imbalance_threshold_kg_m=1e-9, evidence="synthetic")
        for mass in (False, -1, float("nan"), float("inf")):
            before = self.host.debug_snapshot()
            with self.assertRaises(ValueError):
                self.host.register_sample("bad", mass_kg=mass)
            self.assertEqual(before, self.host.debug_snapshot())
        truth = {"nested": [1]}
        self.host.register_sample("copied", mass_kg=0.01, truth=truth)
        truth["nested"][0] = 99
        self.assertEqual(self.host.debug_snapshot()["registered_samples"]["copied"]["truth"]["nested"], [1])
        self.ready()
        before = self.host.debug_snapshot()
        with self.assertRaises(RuntimeError):
            self.host.register_sample("sample_a", mass_kg=0.1)
        self.assertEqual(before, self.host.debug_snapshot())


class TestEppendorf5910RiTerminalPrecision(_dr6000_test_unittest.TestCase):
    def test_terminal_event_zero_speed_exactly_for_nonbinary_synthetic_rates(self):
        from basic40_runtime_analytical import _epp_motion
        for acceleration in (0.123, 7.77, 40.0):
            for braking in (0.017, 3.3, 99.0):
                for speed in (100 * 2 * _epp_test_math.pi / 60, 32.123, 14000 * 2 * _epp_test_math.pi / 60):
                    operation = {"acceleration": acceleration, "braking": braking, "speed_target": speed,
                        "decel_start_s": 10.0, "end_s": 10.0 + min(speed, acceleration * 10.0) / braking}
                    self.assertEqual(_epp_motion(operation, operation["end_s"])[0], 0.0)
                    self.assertEqual(_epp_motion(operation, operation["end_s"] + 1.0)[0], 0.0)
