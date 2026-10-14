"""Deterministic synthetic process/API tests; no real sample data or hardware."""
import copy
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "chemistry-lab-assets" / "scripts"))
from basic40_runtime import (ASSET_SPECS, LabHost, RPM_TO_RAD_S,
                             SceneSynchronizationError, export_contract_metadata)
from asset_tool import lint_schema, validate_value

TYPES = {"hotplate": "instrument.ika_rct_digital", "oven": "instrument.binder_fd56",
         "pump": "instrument.ne1000_bd10ml", "hood": "instrument.esco_efa5"}


class Basic40RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.scene_calls = []
        self.host = LabHost(scene_adapter=lambda instance, updates: self.scene_calls.append((instance, copy.deepcopy(updates))))
        for instance, type_id in TYPES.items():
            self.host.create_instance(instance, type_id)
        self.host.register_principal("learner", visibility="agent")
        self.host.register_principal("judge", visibility="evaluator")
        self.agent = self.host.session("learner")
        self.judge = self.host.session("judge")
        self.request_counter = 0

    def invoke(self, instance, capability, **arguments):
        self.request_counter += 1
        return self.agent.invoke(instance_id=instance, capability_id=capability,
            arguments=arguments, request_id=f"test_{self.request_counter}")

    def ok(self, instance, capability, **arguments):
        response = self.invoke(instance, capability, **arguments)
        self.assertEqual(response["status"], "succeeded", response)
        return response

    def value(self, instance, state_id, *, debug=False):
        session = self.judge if debug else self.agent
        return session.read(instance_id=instance, state_ids=[state_id])["observations"][state_id]["value"]

    def configure_pump(self, *, syringe_volume=5e-6, external_volume=0.0,
                       external_capacity=10e-6, syringe_capacity=10e-6):
        spec = ASSET_SPECS[TYPES["pump"]]["parameters"]
        self.host.configure_syringe("pump", diameter_m=spec["syringe_diameter_m"]["value"],
            stroke_m=spec["syringe_stroke_m"]["value"], evidence="Synthetic nominal B-D 10 mL fixture from declared table geometry")
        self.host.create_vessel("synthetic_syringe", volume_m3=syringe_volume, capacity_m3=syringe_capacity,
            species_mass_kg={"synthetic_private_marker": syringe_volume * 1000})
        self.host.create_vessel("synthetic_external", volume_m3=external_volume, capacity_m3=external_capacity,
            species_mass_kg={"synthetic_other_marker": external_volume * 1000})
        self.host.connect_pump("pump", "synthetic_syringe", "synthetic_external")
        self.host.sync_scene(["pump"])
        self.ok("pump", "power.set", enabled=True)
        self.ok("pump", "pumping.set_flow_rate", flow_rate_m3_s=1e-8)

    def totals(self):
        vessels = self.host.debug_snapshot()["vessels"].values()
        total_volume = sum(v["volume_m3"] for v in vessels)
        masses = {}
        for vessel in vessels:
            for species, mass in vessel["species_mass_kg"].items():
                masses[species] = masses.get(species, 0) + mass
        return total_volume, masses

    def assert_conserved(self, before):
        after = self.totals()
        self.assertAlmostEqual(before[0], after[0], delta=1e-18)
        self.assertEqual(set(before[1]), set(after[1]))
        for species in before[1]:
            self.assertAlmostEqual(before[1][species], after[1][species], delta=1e-15)

    def test_initial_scene_receives_all_slots_and_explicit_resync(self):
        self.assertEqual(len(self.scene_calls), 4)
        for instance, updates in self.scene_calls:
            self.assertEqual(set(updates), set(ASSET_SPECS[TYPES[instance]]["state_slots"]))
        self.configure_pump()
        pump_update = [updates for instance, updates in self.scene_calls if instance == "pump"]
        self.assertTrue(any(updates.get("pumping.plunger_displacement_m", 0) > 0 for updates in pump_update))

    def test_read_is_pure_and_common_clock_has_one_authority(self):
        self.ok("hotplate", "power.set", enabled=True)
        self.ok("hotplate", "thermal.set_temperature", temperature_K=333.15)
        before = self.host.debug_snapshot()
        for _ in range(5):
            for instance in TYPES:
                self.agent.read(instance_id=instance)
                self.agent.describe(instance_id=instance)
        self.assertEqual(before, self.host.debug_snapshot())
        self.host.step(2)
        self.assertEqual(self.host.time_s, 2)
        self.assertTrue(all(self.agent.read(instance_id=instance)["clock"]["time_s"] == 2 for instance in TYPES))

    def test_two_instances_do_not_share_state(self):
        self.host.create_instance("hotplate_b", TYPES["hotplate"])
        self.ok("hotplate", "power.set", enabled=True)
        self.ok("hotplate", "thermal.set_temperature", temperature_K=333.15)
        self.ok("hotplate", "stirring.set_speed", speed_rad_s=300 * RPM_TO_RAD_S)
        self.host.step(60)
        self.assertFalse(self.value("hotplate_b", "power.enabled"))
        self.assertEqual(self.value("hotplate_b", "thermal.temperature_k", debug=True), 293.15)
        self.assertEqual(self.value("hotplate_b", "stirring.speed_rad_s", debug=True), 0)
        self.assertGreater(self.value("hotplate", "thermal.temperature_k", debug=True), 293.15)

    def test_dedup_replay_and_conflict_leave_asset_unchanged(self):
        args = dict(instance_id="hotplate", capability_id="power.set", arguments={"enabled": True}, request_id="fixed")
        response = self.agent.invoke(**args)
        self.host.step(1)
        before = self.host.debug_snapshot()
        self.assertEqual(self.agent.invoke(**args), response)
        self.assertEqual(before, self.host.debug_snapshot())
        conflict = self.agent.invoke(**{**args, "arguments": {"enabled": False}})
        self.assertEqual(conflict["error"]["code"], "REQUEST_CONFLICT")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_request_identity_is_host_bound(self):
        self.ok("hotplate", "power.set", enabled=True)
        response = self.invoke("hotplate", "thermal.set_temperature", temperature_K=333.15, role="evaluator")
        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
        with self.assertRaises(TypeError):
            self.agent.read(instance_id="hotplate", state_ids=["thermal.temperature_k"], role="evaluator")
        with self.assertRaises(ValueError):
            self.host.session("unregistered")
        with self.assertRaises(ValueError):
            self.host.register_principal("learner", visibility="evaluator")

    def test_agent_truth_filter_covers_describe_read_errors_logs_and_results(self):
        self.configure_pump()
        self.ok("pump", "pumping.start")
        self.host.step(1)
        payloads = [self.agent.describe(instance_id="pump"), self.agent.read(instance_id="pump"), self.agent.logs()]
        denied = self.agent.read(instance_id="pump", state_ids=["pumping.transferred_volume_m3"])
        unknown = self.agent.read(instance_id="pump", state_ids=["unknown.private_truth"])
        self.assertEqual(denied["error"], unknown["error"])
        payloads += [denied, unknown]
        import json
        serialized = json.dumps(payloads)
        self.assertNotIn("synthetic_private_marker", serialized)
        self.assertNotIn("synthetic_other_marker", serialized)
        self.assertNotIn("species_mass_kg", serialized)
        self.assertNotIn("pumping.transferred_volume_m3", serialized)
        self.assertGreater(self.value("pump", "pumping.transferred_volume_m3", debug=True), 0)

    def test_scope_limits_discovery_and_instance_access(self):
        self.host.register_principal("scoped", visibility="agent", instance_ids=["hotplate"])
        session = self.host.session("scoped")
        self.assertEqual(session.read(instance_id="oven")["error"]["code"], "UNKNOWN_INSTANCE")
        self.assertEqual(session.describe(asset_type_id=TYPES["oven"])["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(session.describe(instance_id="hotplate")["asset_type_id"], TYPES["hotplate"])

    def test_invalid_arguments_and_preconditions_have_no_state_effects(self):
        cases = [("hotplate", "power.set", {"enabled": 1}),
            ("hotplate", "thermal.set_temperature", {"temperature_K": 333.15}),
            ("hotplate", "stirring.set_speed", {"speed_rad_s": True}),
            ("oven", "scene.set_door", {"angle_rad": math.inf}),
            ("hood", "scene.set_sash", {"opening_m": -.1}),
            ("pump", "pumping.set_flow_rate", {"flow_rate_m3_s": math.nan}),
            ("pump", "pumping.start", {}),
            ("pump", "pumping.set_direction", {"direction": "sideways"})]
        for instance, capability, arguments in cases:
            with self.subTest(capability=capability, arguments=arguments):
                before = self.host.debug_snapshot()
                response = self.invoke(instance, capability, **arguments)
                self.assertEqual(response["status"], "rejected")
                self.assertEqual(response["error"]["side_effects"], "none")
                self.assertEqual(before, self.host.debug_snapshot())

    def test_malformed_identifiers_return_errors_without_exceptions_or_mutation(self):
        before = self.host.debug_snapshot()
        for malformed in ([], {}, True, 0):
            self.assertEqual(self.agent.read(instance_id=malformed)["status"], "rejected")
            self.assertEqual(self.agent.describe(instance_id=malformed)["status"], "rejected")
            response = self.agent.invoke(instance_id=malformed, capability_id="power.set", arguments={"enabled": True}, request_id={})
            self.assertEqual(response["status"], "rejected")
            self.assertIsNone(response["instance_id"])
            self.assertIsNone(response["request_id"])
        self.assertEqual(before, self.host.debug_snapshot())

    def test_unrepresentable_json_integer_rejects_without_mutation(self):
        huge_integer = 10**400
        self.ok("hotplate", "power.set", enabled=True)
        before = self.host.debug_snapshot()
        response = self.invoke("hotplate", "thermal.set_temperature", temperature_K=huge_integer)
        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(before, self.host.debug_snapshot())
        with self.assertRaises(ValueError):
            self.host.step(huge_integer)
        self.assertEqual(before, self.host.debug_snapshot())

    def test_write_routes_through_capability_and_cannot_write_truth(self):
        self.ok("hotplate", "power.set", enabled=True)
        response = self.agent.write(instance_id="hotplate", state_id="thermal.target_k", value=333.15, request_id="write_temp")
        self.assertEqual(response["result"], {"setpoint_K": 333.15})
        self.assertEqual(self.value("hotplate", "thermal.temperature_k", debug=True), 293.15)
        before = self.host.debug_snapshot()
        denied = self.judge.write(instance_id="hotplate", state_id="thermal.temperature_k", value=999, request_id="write_truth")
        self.assertEqual(denied["error"]["code"], "ACCESS_DENIED")
        self.assertEqual(before, self.host.debug_snapshot())
        replay = self.agent.invoke(instance_id="hotplate", capability_id="thermal.set_temperature", arguments={"temperature_K": 333.15}, request_id="write_temp")
        self.assertEqual(response, replay)

    def test_thermal_heating_is_time_dependent_power_bounded_and_cools(self):
        self.ok("hotplate", "power.set", enabled=True)
        self.ok("hotplate", "thermal.set_temperature", temperature_K=333.15)
        self.assertEqual(self.value("hotplate", "thermal.temperature_k", debug=True), 293.15)
        self.host.step(10)
        heated = self.value("hotplate", "thermal.temperature_k", debug=True)
        self.assertGreater(heated, 293.15)
        self.assertLessEqual((heated - 293.15) * 1800, 600 * 10 + 1e-9)
        self.host.step(3600)
        self.assertAlmostEqual(self.value("hotplate", "thermal.temperature_k", debug=True), 333.15)
        self.ok("hotplate", "power.set", enabled=False)
        self.host.step(60)
        self.assertLess(self.value("hotplate", "thermal.temperature_k", debug=True), 333.15)

    def test_oven_open_door_reduces_heating_by_explicit_assumption(self):
        self.host.create_instance("oven_open", TYPES["oven"])
        for instance in ("oven", "oven_open"):
            self.ok(instance, "power.set", enabled=True)
            self.ok(instance, "thermal.set_temperature", temperature_K=373.15)
        self.ok("oven_open", "scene.set_door", angle_rad=math.radians(100))
        self.host.step(300)
        self.assertLess(self.value("oven_open", "thermal.temperature_k", debug=True), self.value("oven", "thermal.temperature_k", debug=True))
        before = self.host.debug_snapshot()
        response = self.invoke("oven", "thermal.set_temperature", temperature_K=300)
        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_temperature_limits_and_speed_grid_boundaries(self):
        self.ok("hotplate", "power.set", enabled=True)
        self.ok("hotplate", "thermal.set_temperature", temperature_K=583.15)
        self.assertEqual(self.invoke("hotplate", "thermal.set_temperature", temperature_K=583.16)["status"], "rejected")
        for rpm in (0, 50, 60, 1490, 1500):
            self.ok("hotplate", "stirring.set_speed", speed_rad_s=rpm * RPM_TO_RAD_S)
        for rpm in (-1, 10, 49, 55, 1501):
            self.assertEqual(self.invoke("hotplate", "stirring.set_speed", speed_rad_s=rpm * RPM_TO_RAD_S)["status"], "rejected")

    def test_stirring_speed_has_finite_response_and_angle_is_integrated(self):
        self.ok("hotplate", "power.set", enabled=True)
        target = 300 * RPM_TO_RAD_S
        self.ok("hotplate", "stirring.set_speed", speed_rad_s=target)
        self.assertEqual(self.value("hotplate", "stirring.speed_rad_s", debug=True), 0)
        self.host.step(.5)
        speed = self.value("hotplate", "stirring.speed_rad_s", debug=True)
        self.assertGreater(speed, 0)
        self.assertLess(speed, target)
        expected_angle = (target * .5 - target * 1.5 * (1 - math.exp(-.5 / 1.5))) % (2 * math.pi)
        self.assertAlmostEqual(self.value("hotplate", "stirring.angle_rad", debug=True), expected_angle)
        self.assertTrue(any("stirring.angle_rad" in updates for _, updates in self.scene_calls))
        self.ok("hotplate", "power.set", enabled=False)
        self.host.step(.5)
        self.assertLess(self.value("hotplate", "stirring.speed_rad_s", debug=True), speed)

    def test_absent_scene_backend_is_unsupported_and_no_state_change(self):
        self.host.scene_adapter = None
        before = self.host.debug_snapshot()
        response = self.invoke("oven", "scene.set_door", angle_rad=.5)
        self.assertEqual(response["status"], "unsupported")
        self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.agent.describe(instance_id="oven")["capabilities"]["scene.set_door"]["availability"], "unsupported")

    def test_scene_command_failure_is_honest_and_process_state_unchanged(self):
        def failing_callback(instance, updates):
            raise RuntimeError("synthetic_private_marker should not leak through exception")
        self.host.scene_adapter = failing_callback
        before = self.host.debug_snapshot()
        response = self.invoke("oven", "scene.set_door", angle_rad=.5)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"]["side_effects"], "unknown")
        self.assertNotIn("synthetic_private_marker", response["error"]["message"])
        self.assertEqual(before, self.host.debug_snapshot())

    def test_step_scene_failure_retains_partial_process_and_common_time(self):
        self.ok("hotplate", "power.set", enabled=True)
        self.ok("hotplate", "thermal.set_temperature", temperature_K=333.15)
        self.host.scene_adapter = lambda instance, updates: (_ for _ in ()).throw(RuntimeError("synthetic backend failure"))
        with self.assertRaises(SceneSynchronizationError):
            self.host.step(10)
        self.assertEqual(self.host.time_s, 10)
        self.assertGreater(self.value("hotplate", "thermal.temperature_k", debug=True), 293.15)
        self.assertTrue(self.host.debug_snapshot()["scene_faults"])

    def test_pump_infuse_conserves_material_and_uses_calibrated_area(self):
        self.configure_pump()
        before = self.totals()
        initial_displacement = self.value("pump", "pumping.plunger_displacement_m")
        self.ok("pump", "pumping.start")
        self.assertEqual(self.value("pump", "pumping.transferred_volume_m3", debug=True), 0)
        self.host.step(100)
        self.assert_conserved(before)
        snapshot = self.host.debug_snapshot()
        self.assertAlmostEqual(snapshot["vessels"]["synthetic_external"]["volume_m3"], 1e-6, delta=1e-18)
        self.assertAlmostEqual(snapshot["vessels"]["synthetic_external"]["species_mass_kg"]["synthetic_private_marker"], .001, delta=1e-15)
        area = math.pi * .01443**2 / 4
        self.assertAlmostEqual(self.value("pump", "pumping.plunger_displacement_m") - initial_displacement, 1e-6 / area)
        self.assertEqual(len(snapshot["transactions"]), 1)
        self.assertEqual(snapshot["transactions"][0]["direction"], "infuse")

    def test_pump_withdraw_reverses_transaction_and_motion(self):
        self.configure_pump(syringe_volume=0, external_volume=2e-6)
        before = self.totals()
        initial_displacement = self.value("pump", "pumping.plunger_displacement_m")
        self.ok("pump", "pumping.set_direction", direction="withdraw")
        self.ok("pump", "pumping.start")
        self.host.step(100)
        self.assert_conserved(before)
        self.assertLess(self.value("pump", "pumping.plunger_displacement_m"), initial_displacement)
        snapshot = self.host.debug_snapshot()
        self.assertAlmostEqual(snapshot["vessels"]["synthetic_syringe"]["volume_m3"], 1e-6)
        self.assertEqual(snapshot["transactions"][0]["donor_id"], "synthetic_external")

    def test_pump_clips_at_recipient_capacity_and_retains_partial_transfer(self):
        self.configure_pump(external_capacity=.25e-6)
        before = self.totals()
        self.ok("pump", "pumping.start")
        self.host.step(100)
        self.assert_conserved(before)
        self.assertFalse(self.value("pump", "pumping.running"))
        self.assertAlmostEqual(self.value("pump", "pumping.transferred_volume_m3", debug=True), .25e-6)
        after = self.host.debug_snapshot()
        self.assertEqual(self.invoke("pump", "pumping.start")["status"], "rejected")
        self.assertEqual(after, self.host.debug_snapshot())

    def test_pump_clips_at_donor_empty_without_creating_liquid(self):
        self.configure_pump(syringe_volume=.1e-6)
        before = self.totals()
        self.ok("pump", "pumping.start")
        self.host.step(100)
        self.assert_conserved(before)
        self.assertFalse(self.value("pump", "pumping.running"))
        self.assertAlmostEqual(self.value("pump", "pumping.transferred_volume_m3", debug=True), .1e-6, delta=1e-18)
        self.assertAlmostEqual(self.host.debug_snapshot()["vessels"]["synthetic_syringe"]["volume_m3"], 0, delta=1e-18)

    def test_pump_withdraw_clips_at_declared_syringe_stroke(self):
        self.configure_pump(syringe_volume=9.9e-6, external_volume=2e-6, syringe_capacity=20e-6)
        before = self.totals()
        self.ok("pump", "pumping.set_direction", direction="withdraw")
        self.ok("pump", "pumping.start")
        self.host.step(100)
        self.assert_conserved(before)
        self.assertFalse(self.value("pump", "pumping.running"))
        self.assertAlmostEqual(self.host.debug_snapshot()["vessels"]["synthetic_syringe"]["volume_m3"], 10e-6, delta=1e-18)
        self.assertAlmostEqual(self.value("pump", "pumping.plunger_displacement_m"), 0, delta=1e-15)

    def test_pump_stop_and_power_off_prevent_further_transfer(self):
        self.configure_pump()
        self.ok("pump", "pumping.start")
        self.host.step(10)
        self.ok("pump", "pumping.stop")
        transferred = self.value("pump", "pumping.transferred_volume_m3", debug=True)
        self.host.step(10)
        self.assertEqual(self.value("pump", "pumping.transferred_volume_m3", debug=True), transferred)
        self.ok("pump", "pumping.start")
        self.ok("pump", "power.set", enabled=False)
        self.host.step(10)
        self.assertEqual(self.value("pump", "pumping.transferred_volume_m3", debug=True), transferred)
        self.assertFalse(self.value("pump", "pumping.running"))

    def test_pump_direction_change_requires_stop_and_flow_uses_bd10_limits(self):
        self.configure_pump()
        params = ASSET_SPECS[TYPES["pump"]]["parameters"]
        minimum, maximum = params["minimum_flow_rate_m3_s"]["value"], params["maximum_flow_rate_m3_s"]["value"]
        self.assertAlmostEqual(minimum, 6.876e-9 / 3600, delta=1e-25)
        self.assertAlmostEqual(maximum, 500.4e-6 / 3600, delta=1e-22)
        for flow in (minimum, maximum):
            self.ok("pump", "pumping.set_flow_rate", flow_rate_m3_s=flow)
        for flow in (0, minimum / 2, maximum * 1.01):
            self.assertEqual(self.invoke("pump", "pumping.set_flow_rate", flow_rate_m3_s=flow)["status"], "rejected")
        self.ok("pump", "pumping.start")
        before = self.host.debug_snapshot()
        self.assertEqual(self.invoke("pump", "pumping.set_direction", direction="withdraw")["status"], "rejected")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_shared_pump_resource_transactions_conserve_under_world_lock(self):
        self.configure_pump(external_capacity=1.5e-6)
        self.host.create_instance("pump_b", TYPES["pump"])
        p = ASSET_SPECS[TYPES["pump"]]["parameters"]
        self.host.configure_syringe("pump_b", diameter_m=p["syringe_diameter_m"]["value"], stroke_m=p["syringe_stroke_m"]["value"], evidence="Synthetic second syringe")
        self.host.create_vessel("synthetic_syringe_b", volume_m3=5e-6, capacity_m3=10e-6, species_mass_kg={"synthetic_b": .005})
        self.host.connect_pump("pump_b", "synthetic_syringe_b", "synthetic_external")
        self.ok("pump_b", "power.set", enabled=True)
        self.ok("pump_b", "pumping.set_flow_rate", flow_rate_m3_s=1e-8)
        before = self.totals()
        for instance in ("pump", "pump_b"):
            self.ok(instance, "pumping.start")
        self.host.step(100)
        self.assert_conserved(before)
        snapshot = self.host.debug_snapshot()
        self.assertAlmostEqual(snapshot["vessels"]["synthetic_external"]["volume_m3"], 1.5e-6, delta=1e-18)
        self.assertEqual(len({t["transaction_id"] for t in snapshot["transactions"]}), 2)

    def test_syringe_vessel_ownership_rejects_shared_and_cross_role_connections(self):
        self.configure_pump()
        self.host.create_instance("pump_b", TYPES["pump"])
        params = ASSET_SPECS[TYPES["pump"]]["parameters"]
        self.host.configure_syringe("pump_b", diameter_m=params["syringe_diameter_m"]["value"],
            stroke_m=params["syringe_stroke_m"]["value"], evidence="Synthetic second exclusive syringe")
        self.host.create_vessel("synthetic_syringe_b", volume_m3=5e-6, capacity_m3=10e-6,
            species_mass_kg={"synthetic_b": .005})
        self.host.create_vessel("synthetic_external_b", volume_m3=0, capacity_m3=10e-6, species_mass_kg={})
        self.host.connect_pump("pump_b", "synthetic_syringe_b", "synthetic_external_b")
        before = self.host.debug_snapshot()
        invalid_connections = [
            ("synthetic_syringe", "synthetic_external_b"),  # same syringe on two pumps
            ("synthetic_syringe_b", "synthetic_syringe"),  # another pump's syringe used as external
            ("synthetic_external", "synthetic_external_b"),  # an external vessel claimed as a syringe
        ]
        for syringe, external in invalid_connections:
            with self.subTest(syringe=syringe, external=external):
                with self.assertRaises(ValueError):
                    self.host.connect_pump("pump_b", syringe, external)
                self.assertEqual(before, self.host.debug_snapshot())
        # Shared external collection remains valid and does not couple plunger positions.
        self.host.connect_pump("pump_b", "synthetic_syringe_b", "synthetic_external")
        b_position = self.value("pump_b", "pumping.plunger_displacement_m")
        self.ok("pump", "pumping.start")
        self.host.step(100)
        self.assertEqual(self.value("pump_b", "pumping.plunger_displacement_m"), b_position)
        self.assertAlmostEqual(self.host.debug_snapshot()["vessels"]["synthetic_syringe_b"]["volume_m3"], 5e-6)

    def test_hood_estimate_is_external_boundary_not_a_sensor_or_power_effect(self):
        self.host.set_external_exhaust("hood", .25)
        observation = self.agent.read(instance_id="hood", state_ids=["ventilation.face_velocity_m_s"])["observations"]["ventilation.face_velocity_m_s"]
        self.assertIsNone(observation["value"])
        self.assertEqual(observation["quality"], "unavailable")
        self.ok("hood", "scene.set_sash", opening_m=.457)
        self.assertAlmostEqual(self.value("hood", "ventilation.face_velocity_m_s"), .25 / (1.301 * .457))
        self.ok("hood", "power.set", enabled=True)
        self.ok("hood", "power.set", enabled=False)
        self.assertEqual(self.value("hood", "ventilation.external_exhaust_m3_s"), .25)
        observation = self.agent.read(instance_id="hood", state_ids=["ventilation.face_velocity_m_s"])["observations"]["ventilation.face_velocity_m_s"]
        self.assertEqual(observation["quality"], "estimated")
        self.ok("hood", "scene.set_sash", opening_m=.670)
        self.assertEqual(self.invoke("hood", "scene.set_sash", opening_m=.671)["status"], "rejected")

    def test_declared_program_returns_unsupported_without_changes(self):
        description = self.agent.describe(instance_id="pump")
        self.assertEqual(description["capabilities"]["pumping.program"]["availability"], "declared")
        before = self.host.debug_snapshot()
        response = self.invoke("pump", "pumping.program", phases=[{"direction": "infuse", "flow_rate_m3_s": 1e-9, "duration_s": 10}])
        self.assertEqual(response["status"], "unsupported")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_real_outputs_and_observations_validate_against_exported_schemas(self):
        self.configure_pump()
        metadata = export_contract_metadata()["asset_types"]
        for instance, type_id in TYPES.items():
            self.ok(instance, "power.set", enabled=True)
            for capability, info in metadata[type_id]["capabilities"].items():
                with self.subTest(instance=instance, capability=capability):
                    self.assertEqual(lint_schema(info["input_schema"]), [])
                    self.assertEqual(lint_schema(info["output_schema"]), [])
                    args = info["example_arguments"][0]
                    self.assertEqual(validate_value(args, info["input_schema"]), [])
                    response = self.ok(instance, capability, **args)
                    self.assertEqual(validate_value(response["result"], info["output_schema"]), [])
                    self.assertEqual(set(response), {"protocol_version", "instance_id", "request_id", "operation_id", "status", "state_revision", "clock", "result", "observations", "error"})
                    self.assertEqual(response["observations"], {})
            observations = self.judge.read(instance_id=instance)["observations"]
            for state_id, observation in observations.items():
                slot = metadata[type_id]["state_slots"][state_id]
                self.assertEqual(validate_value(observation["value"], slot["value_schema"]), [])
                self.assertEqual(observation["unit"], slot["unit"])
                self.assertEqual(observation["sample_time_s"], self.host.time_s)

    def test_host_time_metadata_is_separate_from_instrument_agent_api(self):
        meta = self.host.describe_host()
        self.assertEqual(meta["sim.step"]["visibility"], "trusted_host_only")
        for instance in TYPES:
            self.assertNotIn("sim.step", self.agent.describe(instance_id=instance)["capabilities"])
        before = self.host.debug_snapshot()
        for dt in (-1, math.inf, math.nan, True, 3601):
            with self.assertRaises(ValueError):
                self.host.step(dt)
        self.host.step(0)
        self.assertEqual(before, self.host.debug_snapshot())


if __name__ == "__main__":
    unittest.main()
