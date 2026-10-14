"""Invariant and failure-state checks for the explicitly synthetic GC model."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gc_runtime import ActionRejected, GC, Method, Species, component_for_action


def prepared(method=None, species=None):
    kwargs = {"method": method}
    if species is not None:
        kwargs["species"] = species
    gc = GC(**kwargs)
    gc.perform("plug_power", connected=True)
    gc.perform("power", enabled=True)
    for gas in gc.gases:
        gc.perform("connect_gas", gas=gas, connected=True)
    gc.step(1200)
    gc.perform("ignite")
    return gc


def inject(gc):
    gc.perform("start")
    gc.perform("aspirate", fraction=.01)
    gc.perform("insert_syringe")
    gc.perform("depress_plunger")
    gc.perform("retract_syringe")


class GCRuntimeTest(unittest.TestCase):
    def test_readiness_and_embodied_preconditions(self):
        gc = GC()
        with self.assertRaises(ActionRejected):
            gc.perform("power", enabled=True)
        self.assertFalse(gc.powered)
        with self.assertRaises(ActionRejected):
            gc.perform("start")
        gc = prepared()
        gc.perform("start")
        gc.perform("aspirate", fraction=.01)
        before = dict(gc.syringe)
        with self.assertRaises(ActionRejected):
            gc.perform("depress_plunger")
        self.assertEqual(before, gc.syringe)
        gc.perform("insert_syringe")
        gc.perform("door", open=True)
        with self.assertRaises(ActionRejected):
            gc.perform("depress_plunger")
        self.assertEqual(before, gc.syringe)

    def test_split_mass_conservation_and_fid_charge(self):
        gc = prepared()
        inject(gc)
        integrated = 0
        for _ in range(90):
            state = gc.step(10)
            integrated += state["fid_current_A"] * 10
            for ledger in state["mass_balance_kg"].values():
                self.assertLess(abs(ledger["residual_kg"]), 1e-20)
        expected_charge = 0
        for name, ledger in gc.mass_balance().items():
            expected_column = ledger["injected_kg"] / 21
            self.assertAlmostEqual(ledger["column_loaded_kg"] / expected_column, 1, places=10)
            self.assertAlmostEqual(ledger["eluted_kg"] / expected_column, 1, places=10)
            self.assertAlmostEqual(ledger["split_vent_kg"] / expected_column, 20, places=10)
            expected_charge += expected_column * gc.species[name].response_C_kg
        self.assertAlmostEqual(gc.fid_integral_C / expected_charge, 1, places=10)
        self.assertAlmostEqual(integrated / gc.fid_integral_C, 1, places=12)

    def test_splitless_purge_transition_and_analytic_transfer(self):
        method = Method(mode="splitless", purge_time_s=30.3)
        gc = prepared(method)
        inject(gc)
        self.assertEqual(gc.flows()["split_vent"], 0)
        gc.step(30)
        self.assertEqual(gc.flows()["split_vent"], 0)
        gc.step(.3)
        self.assertEqual(gc.flows()["split_vent"], method.purge_flow_m3_s)
        gc.step(150)
        remaining_before_purge = math.exp(-method.column_flow_m3_s * method.purge_time_s / method.liner_volume_m3)
        expected_fraction = (1 - remaining_before_purge + remaining_before_purge *
                             method.column_flow_m3_s / (method.column_flow_m3_s + method.purge_flow_m3_s))
        for ledger in gc.mass_balance().values():
            self.assertAlmostEqual(ledger["column_loaded_kg"] / ledger["injected_kg"], expected_fraction, places=10)
            self.assertLess(abs(ledger["residual_kg"]), 1e-20)

    def test_septum_purge_is_distinct_from_split_flow(self):
        gc = prepared(Method(septum_sample_loss_fraction=.1))
        inject(gc)
        gc.step(100)
        flow = gc.observe()
        self.assertAlmostEqual(flow["carrier_total_flow_m3_s"],
                               sum(flow["flows_m3_s"][k] for k in ("column", "split_vent", "septum_purge")))
        for ledger in gc.mass_balance().values():
            self.assertAlmostEqual(ledger["septum_purge_kg"] / ledger["injected_kg"], .1, places=12)
            self.assertAlmostEqual(ledger["column_loaded_kg"] / ledger["injected_kg"], .9 / 21, places=12)
            self.assertLess(abs(ledger["residual_kg"]), 1e-20)

    def test_fractional_plunger_strokes_do_not_restart_purge_timer(self):
        gc = prepared(Method(mode="splitless", purge_time_s=10))
        gc.perform("start")
        gc.perform("aspirate", fraction=.01)
        gc.perform("insert_syringe")
        gc.perform("depress_plunger", fraction=.5)
        first_injection_time = gc.injection_time_s
        gc.step(6)
        gc.perform("depress_plunger")
        self.assertEqual(gc.injection_time_s, first_injection_time)
        gc.step(4)
        self.assertGreater(gc.flows()["split_vent"], 0)
        for ledger in gc.mass_balance().values():
            self.assertLess(abs(ledger["residual_kg"]), 1e-20)

    def test_carrier_loss_stops_transport_without_destroying_mass(self):
        gc = prepared()
        inject(gc)
        gc.step(30)
        before = gc.mass_balance()
        gc.perform("connect_gas", gas="carrier", connected=False)
        self.assertIn("NO_CARRIER_FLOW", gc.observe()["not_ready_reasons"])
        gc.step(100)
        after = gc.mass_balance()
        for name in before:
            for quantity in ("liner_kg", "column_remaining_kg", "eluted_kg"):
                self.assertEqual(before[name][quantity], after[name][quantity])

    def test_flame_loss_elutes_sample_but_produces_no_charge(self):
        gc = prepared()
        inject(gc)
        gc.perform("connect_gas", gas="hydrogen", connected=False)
        state = gc.step(900)
        self.assertFalse(state["flame_lit"])
        self.assertEqual(state["fid_current_A"], 0)
        self.assertEqual(state["fid_integral_C"], 0)
        for ledger in state["mass_balance_kg"].values():
            self.assertGreater(ledger["eluted_kg"], 0)
            self.assertEqual(ledger["eluted_kg"], ledger["flame_off_eluted_kg"])

    def test_open_door_cools_oven_and_clears_readiness(self):
        gc = prepared()
        initial = gc.zones["oven"].temperature_K
        gc.perform("door", open=True)
        state = gc.step(60)
        self.assertEqual(state["heater_power_W"]["oven"], 0)
        self.assertLess(state["temperatures_K"]["oven"], initial)
        self.assertIn("OVEN_DOOR_OPEN", state["not_ready_reasons"])
        gc.perform("door", open=False)
        gc.step(1200)
        self.assertTrue(gc.observe()["ready"])

    def test_temperature_changes_retention_in_expected_direction(self):
        species = Species("synthetic_probe", 3, -35000, 1)
        self.assertLess(species.retention_factor(393.15), species.retention_factor(353.15))
        self.assertGreater(species.retention_factor(313.15), species.retention_factor(353.15))
        result = []
        for temperature in (353.15, 393.15):
            gc = prepared(Method(oven_temperature_K=temperature), (species,))
            inject(gc)
            max_signal = -1
            peak_time = 0
            for _ in range(500):
                state = gc.step(1)
                if state["fid_current_A"] > max_signal:
                    max_signal = state["fid_current_A"]
                    peak_time = state["run_time_s"]
            result.append(peak_time)
        self.assertLess(result[1], result[0])

    def test_zero_response_species_does_not_become_a_peak(self):
        gc = prepared(species=(Species("synthetic_nonresponsive", 1, -30000, 0),))
        inject(gc)
        state = gc.step(400)
        self.assertGreater(state["mass_balance_kg"]["synthetic_nonresponsive"]["eluted_kg"], 0)
        self.assertEqual(state["fid_integral_C"], 0)

    def test_stop_ends_acquisition_but_not_chromatographic_transport(self):
        gc = prepared(species=(Species("synthetic_probe", 1, -30000, 1),))
        inject(gc)
        gc.perform("stop")
        state = gc.step(400)
        self.assertEqual(state["fid_integral_C"], 0)
        self.assertEqual(state["run_time_s"], 0)
        self.assertGreater(state["mass_balance_kg"]["synthetic_probe"]["eluted_kg"], 0)

    def test_invalid_numerics_and_sample_transfer(self):
        gc = GC()
        for invalid in (-1, 0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                gc.step(invalid)
        with self.assertRaises(ValueError):
            Method(column_flow_m3_s=0)
        with self.assertRaises(ValueError):
            Species("invalid", 1, float("nan"), 1)
        before = dict(gc.vial)
        gc.perform("aspirate", fraction=.2)
        for name in before:
            self.assertAlmostEqual(gc.vial[name] + gc.syringe[name], before[name], places=20)
        with self.assertRaises(ValueError):
            gc.perform("aspirate", fraction=2)

    def test_gas_connection_events_target_matching_physical_ports(self):
        gc = GC()
        for gas in ("carrier", "hydrogen", "air", "makeup"):
            self.assertEqual(component_for_action("connect_gas", gas=gas), f"{gas}_port")
            gc.perform("connect_gas", gas=gas, connected=True)
            self.assertEqual(gc.events[-1]["component_id"], f"{gas}_port")


if __name__ == "__main__":
    unittest.main()
