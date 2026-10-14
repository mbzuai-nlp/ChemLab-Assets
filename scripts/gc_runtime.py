"""Synthetic GC-FID process semantics, SI units, Python standard library only.

This is NOT calibrated Agilent firmware, a pressure-flow network, or a contact
solver. The host simulator owns joints, contacts, grasping, alignment and thermal
contact. Every numerical default below is an explicit educational assumption.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

R_J_MOL_K = 8.31446261815324
PARAMETER_PROVENANCE = "synthetic_assumption_not_manufacturer_calibration"
ACTION_COMPONENTS = {
    "plug_power": "power_inlet", "power": "power_switch",
    "connect_gas": "carrier_port", "door": "oven_door",
    "set_temperature": "control_panel", "ignite": "fid_igniter",
    "extinguish": "fid_body", "start": "start_key", "stop": "stop_key",
    "aspirate": "sample_vial", "insert_syringe": "septum",
    "depress_plunger": "plunger", "retract_syringe": "syringe",
}


def component_for_action(action, **kwargs):
    """Resolve the scene component, including the selected gas supply port."""
    if action == "connect_gas":
        gas = kwargs.get("gas")
        if gas not in {"carrier", "hydrogen", "air", "makeup"}:
            raise ValueError("Unknown gas port")
        return f"{gas}_port"
    if action not in ACTION_COMPONENTS:
        raise ValueError(f"Unknown action: {action}")
    return ACTION_COMPONENTS[action]


def finite(value, name, *, positive=False):
    value = float(value)
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return value


class ActionRejected(ValueError):
    """An embodied task precondition was not satisfied; state is unchanged."""


@dataclass(frozen=True)
class Species:
    name: str
    k_ref: float
    delta_h_J_mol: float
    response_C_kg: float
    reference_temperature_K: float = 353.15

    def __post_init__(self):
        if not self.name:
            raise ValueError("Species requires an identifier")
        finite(self.k_ref, "k_ref")
        finite(self.response_C_kg, "response_C_kg")
        finite(self.reference_temperature_K, "reference_temperature_K", positive=True)
        if not math.isfinite(self.delta_h_J_mol):
            raise ValueError("delta_h_J_mol must be finite")

    def retention_factor(self, temperature_K):
        temperature_K = finite(temperature_K, "temperature_K", positive=True)
        exponent = -self.delta_h_J_mol / R_J_MOL_K * (
            1 / temperature_K - 1 / self.reference_temperature_K)
        return self.k_ref * math.exp(max(-100, min(100, exponent)))


SYNTHETIC_SPECIES = (
    Species("synthetic_A", 1.0, -30000, 1.0),
    Species("synthetic_B", 2.5, -38000, 1.2),
    Species("synthetic_C", 5.0, -45000, 0.8),
)


@dataclass(frozen=True)
class Method:
    mode: str = "split"
    split_ratio: float = 20.0
    purge_time_s: float = 30.0
    purge_flow_m3_s: float = 8.333333333333334e-7
    # Both column and liner flow use this SAME assumed representative state.
    # This omits real pressure/temperature conversion between inlet and column.
    column_flow_m3_s: float = 1.6666666666666667e-8
    septum_purge_flow_m3_s: float = 5e-8
    # Septum purge is a distinct branch, not a perfectly mixed liner outlet.
    # Zero means ideal separation of injected sample from the septum-purge path.
    septum_sample_loss_fraction: float = 0.0
    hydrogen_flow_m3_s: float = 5e-7
    air_flow_m3_s: float = 5e-6
    makeup_flow_m3_s: float = 4.166666666666667e-7
    liner_volume_m3: float = 8e-7
    column_length_m: float = 30.0
    column_inner_diameter_m: float = 2.5e-4
    column_film_thickness_m: float = 2.5e-7
    longitudinal_B_m2_s: float = 3e-5
    mass_transfer_C_s: float = 4e-4
    oven_temperature_K: float = 353.15
    inlet_temperature_K: float = 523.15
    fid_temperature_K: float = 573.15
    temperature_tolerance_K: float = 1.0
    ready_dwell_s: float = 5.0
    fid_ignition_minimum_K: float = 423.15

    def __post_init__(self):
        if self.mode not in {"split", "splitless"}:
            raise ValueError("mode must be split or splitless")
        for key, value in asdict(self).items():
            if key != "mode":
                finite(value, key)
        for key in ("liner_volume_m3", "column_length_m", "column_inner_diameter_m",
                    "column_flow_m3_s", "oven_temperature_K", "inlet_temperature_K",
                    "fid_temperature_K", "fid_ignition_minimum_K"):
            finite(getattr(self, key), key, positive=True)
        if self.septum_sample_loss_fraction > 1:
            raise ValueError("septum_sample_loss_fraction must be in [0, 1]")
        if self.column_film_thickness_m >= self.column_inner_diameter_m / 2:
            raise ValueError("Stationary film must leave an open mobile-phase core")


@dataclass
class ThermalZone:
    temperature_K: float
    setpoint_K: float
    heat_capacity_J_K: float
    conductance_W_K: float
    maximum_power_W: float
    control_gain_W_K: float
    heater_power_W: float = 0.0

    def advance(self, dt_s, ambient_K, enabled, open_door=False):
        conductance = self.conductance_W_K * (12 if open_door else 1)
        demand = (self.control_gain_W_K * (self.setpoint_K - self.temperature_K)
                  + conductance * (self.setpoint_K - ambient_K))
        self.heater_power_W = min(self.maximum_power_W, max(0, demand)) if enabled else 0.0
        equilibrium = ambient_K + self.heater_power_W / conductance
        decay = math.exp(-conductance * dt_s / self.heat_capacity_J_K)
        self.temperature_K = equilibrium + (self.temperature_K - equilibrium) * decay


@dataclass
class Band:
    species: str
    injected_mass_kg: float
    sigma_progress: float
    progress: float = 0.0
    released_fraction: float = 0.0

    def advance(self, progress_delta):
        self.progress += progress_delta
        # Gaussian residence-time kernel conditioned on nonnegative residence.
        sigma = self.sigma_progress
        initial = .5 * math.erfc(1 / (sigma * math.sqrt(2)))
        cdf = .5 * math.erfc((1 - self.progress) / (sigma * math.sqrt(2)))
        released = min(1.0, max(self.released_fraction, (cdf - initial) / (1 - initial)))
        mass = self.injected_mass_kg * (released - self.released_fraction)
        self.released_fraction = released
        return mass


class GC:
    """Agilent 7890B-inspired, single SSL/FID task state and process abstraction."""

    def __init__(self, method=None, species=SYNTHETIC_SPECIES, vial_masses_kg=None):
        self.method = method or Method()
        species = tuple(species)
        self.species = {item.name: item for item in species}
        if not species or len(self.species) != len(species):
            raise ValueError("Species identifiers must be nonempty and unique")
        self.ambient_K = 298.15
        self.zones = {
            "oven": ThermalZone(self.ambient_K, self.method.oven_temperature_K, 6000, 3, 2000, 200),
            "inlet": ThermalZone(self.ambient_K, self.method.inlet_temperature_K, 100, .4, 200, 8),
            "fid": ThermalZone(self.ambient_K, self.method.fid_temperature_K, 150, .35, 200, 8),
        }
        self.power_connected = False
        self.powered = False
        self.gases = {gas: False for gas in ("carrier", "hydrogen", "air", "makeup")}
        self.door_open = False
        self.flame_lit = False
        self.syringe_inserted = False
        self.running = False
        self.time_s = self.run_time_s = self.stable_time_s = 0.0
        self.injection_time_s = None
        self.fid_current_A = self.fid_integral_C = 0.0
        self.vial = self._masses(vial_masses_kg if vial_masses_kg is not None else
                                 {name: 1e-7 for name in self.species})
        self.syringe = self._masses({})
        self.liner = self._masses({})
        self.ledger = {name: dict(injected_kg=0.0, split_vent_kg=0.0,
                                 septum_purge_kg=0.0, column_loaded_kg=0.0,
                                 eluted_kg=0.0, flame_off_eluted_kg=0.0)
                       for name in self.species}
        self.bands = []
        self.events = []

    def _masses(self, mapping):
        if set(mapping) - set(self.species):
            raise ValueError("Unknown species in mass mapping")
        return {name: finite(mapping.get(name, 0), name) for name in self.species}

    def flows(self):
        m = self.method
        carrier = self.powered and self.gases["carrier"]
        split_open = m.mode == "split" or self.injection_time_s is None or (
            self.time_s - self.injection_time_s >= m.purge_time_s - 1e-10)
        split = m.split_ratio * m.column_flow_m3_s if m.mode == "split" else m.purge_flow_m3_s
        return {
            "column": m.column_flow_m3_s if carrier else 0.0,
            "split_vent": split if carrier and split_open else 0.0,
            "septum_purge": m.septum_purge_flow_m3_s if carrier else 0.0,
            "hydrogen": m.hydrogen_flow_m3_s if self.powered and self.gases["hydrogen"] else 0.0,
            "air": m.air_flow_m3_s if self.powered and self.gases["air"] else 0.0,
            "makeup": m.makeup_flow_m3_s if self.powered and self.gases["makeup"] else 0.0,
        }

    def _thermal_ready(self):
        return all(abs(z.temperature_K - z.setpoint_K) <= self.method.temperature_tolerance_K
                   for z in self.zones.values())

    def not_ready_reasons(self):
        reasons = []
        if not self.powered:
            reasons.append("POWER_OFF")
        if self.door_open:
            reasons.append("OVEN_DOOR_OPEN")
        flow = self.flows()
        for gas, key in (("carrier", "column"), ("hydrogen", "hydrogen"), ("air", "air")):
            if flow[key] <= 0:
                reasons.append(f"NO_{gas.upper()}_FLOW")
        if self.method.makeup_flow_m3_s > 0 and flow["makeup"] <= 0:
            reasons.append("NO_MAKEUP_FLOW")
        if not self._thermal_ready() or self.stable_time_s < self.method.ready_dwell_s:
            reasons.append("TEMPERATURE_NOT_READY")
        if not self.flame_lit:
            reasons.append("FID_FLAME_OFF")
        return reasons

    def perform(self, action, **kwargs):
        """Apply a semantic event AFTER the host has verified its physical contact.

        Insert/retract are symbolic acknowledgments, never collision detection.
        All booleans are explicit; arguments with the wrong type are rejected.
        """
        def boolean(key):
            value = kwargs[key]
            if type(value) is not bool:
                raise ValueError(f"{key} must be bool")
            return value

        def fraction():
            value = finite(kwargs.get("fraction", 1.0), "fraction", positive=True)
            if value > 1:
                raise ValueError("fraction must be <= 1")
            return value

        if action == "plug_power":
            self.power_connected = boolean("connected")
            if not self.power_connected:
                self.powered = self.running = self.flame_lit = False
        elif action == "power":
            enabled = boolean("enabled")
            if enabled and not self.power_connected:
                raise ActionRejected("Connect power_inlet first")
            self.powered = enabled
            if not enabled:
                self.running = self.flame_lit = False
        elif action == "connect_gas":
            gas = kwargs["gas"]
            if gas not in self.gases:
                raise ValueError("Unknown gas port")
            self.gases[gas] = boolean("connected")
            if gas in {"hydrogen", "air"} and not self.gases[gas]:
                self.flame_lit = False
        elif action == "door":
            self.door_open = boolean("open")
        elif action == "set_temperature":
            if kwargs["zone"] not in self.zones:
                raise ValueError("Unknown thermal zone")
            temperature = finite(kwargs["temperature_K"], "temperature_K", positive=True)
            if not 273.15 <= temperature <= 723.15:
                raise ValueError("Outside synthetic model's supported thermal interval")
            self.zones[kwargs["zone"]].setpoint_K = temperature
            self.stable_time_s = 0.0
        elif action == "ignite":
            flow = self.flows()
            if not self.powered or flow["hydrogen"] <= 0 or flow["air"] <= 0:
                raise ActionRejected("FID ignition requires power, hydrogen and air")
            if self.zones["fid"].temperature_K < self.method.fid_ignition_minimum_K:
                raise ActionRejected("FID below synthetic ignition readiness temperature")
            self.flame_lit = True
        elif action == "extinguish":
            self.flame_lit = False
        elif action == "start":
            if self.running:
                raise ActionRejected("A run is already acquiring")
            reasons = self.not_ready_reasons()
            if reasons:
                raise ActionRejected(", ".join(reasons))
            self.running = True
            self.run_time_s = self.fid_integral_C = 0.0
            self.injection_time_s = None
        elif action == "stop":
            self.running = False
        elif action == "aspirate":
            f = fraction()
            if self.syringe_inserted:
                raise ActionRejected("Syringe must be at the vial, not inside the inlet")
            if not any(self.vial.values()):
                raise ActionRejected("Sample vial is empty")
            for name in self.species:
                amount = self.vial[name] * f
                self.vial[name] -= amount
                self.syringe[name] += amount
        elif action == "insert_syringe":
            if self.syringe_inserted:
                raise ActionRejected("Syringe is already inserted")
            self.syringe_inserted = True
        elif action == "depress_plunger":
            f = fraction()
            reasons = self.not_ready_reasons()
            if not self.running or not self.syringe_inserted or reasons:
                raise ActionRejected("Injection requires acquiring, aligned syringe and ready state: "
                                     + ", ".join(reasons))
            if not any(self.syringe.values()):
                raise ActionRejected("Syringe is empty")
            for name in self.species:
                amount = self.syringe[name] * f
                purge_loss = amount * self.method.septum_sample_loss_fraction
                self.syringe[name] -= amount
                self.liner[name] += amount - purge_loss
                self.ledger[name]["injected_kg"] += amount
                self.ledger[name]["septum_purge_kg"] += purge_loss
            if self.injection_time_s is None:
                self.injection_time_s = self.time_s
        elif action == "retract_syringe":
            self.syringe_inserted = False
        else:
            raise ValueError(f"Unknown action: {action}")
        if not self.powered or self.door_open or not self._thermal_ready():
            self.stable_time_s = 0.0
        if not self.flame_lit:
            self.fid_current_A = 0.0
        self.events.append({"time_s": self.time_s, "action": action,
                            "component_id": component_for_action(action, **kwargs), "arguments": kwargs})
        return self.observe()

    def _advance(self, dt_s):
        for name, zone in self.zones.items():
            zone.advance(dt_s, self.ambient_K, self.powered and not (name == "oven" and self.door_open),
                         open_door=name == "oven" and self.door_open)
        if self.powered and not self.door_open and self._thermal_ready():
            self.stable_time_s += dt_s
        else:
            self.stable_time_s = 0.0
        flow = self.flows()
        if not self.powered or flow["hydrogen"] <= 0 or flow["air"] <= 0:
            self.flame_lit = False
        if self.zones["fid"].temperature_K < self.method.fid_ignition_minimum_K:
            self.flame_lit = False
        m = self.method
        core_diameter = m.column_inner_diameter_m - 2 * m.column_film_thickness_m
        velocity = flow["column"] / (math.pi * core_diameter ** 2 / 4)
        charge = 0.0
        survivors = []
        for band in self.bands:
            k = self.species[band.species].retention_factor(self.zones["oven"].temperature_K)
            eluted = band.advance(dt_s * velocity / (m.column_length_m * (1 + k)))
            self.ledger[band.species]["eluted_kg"] += eluted
            if self.flame_lit:
                charge += eluted * self.species[band.species].response_C_kg
            else:
                self.ledger[band.species]["flame_off_eluted_kg"] += eluted
            if band.released_fraction < 1:
                survivors.append(band)
        self.bands = survivors
        # A well-mixed vapor compartment transfers mass by competing outflows.
        # Newly transferred bands enter at the END of this small numerical step.
        total = flow["column"] + flow["split_vent"]
        if total > 0:
            fraction_out = -math.expm1(-total * dt_s / m.liner_volume_m3)
            column_fraction = flow["column"] / total
            H = m.longitudinal_B_m2_s / velocity + m.mass_transfer_C_s * velocity
            sigma = math.sqrt(max(H / m.column_length_m, 1e-12))
            for name, mass in self.liner.items():
                out = mass * fraction_out
                loaded = out * column_fraction
                self.liner[name] -= out
                self.ledger[name]["column_loaded_kg"] += loaded
                self.ledger[name]["split_vent_kg"] += out - loaded
                if loaded > 0:
                    self.bands.append(Band(name, loaded, sigma))
        self.time_s += dt_s
        if self.running:
            self.run_time_s += dt_s
            self.fid_integral_C += charge
        return charge

    def step(self, dt_s):
        """Advance time; returned current is the average over this whole step."""
        dt_s = finite(dt_s, "dt_s", positive=True)
        remaining = dt_s
        charge = 0.0
        while remaining > 1e-12:
            h = min(.5, remaining)
            # Align the numerical grid with the splitless purge transition.
            if self.method.mode == "splitless" and self.injection_time_s is not None:
                until_purge = self.injection_time_s + self.method.purge_time_s - self.time_s
                if until_purge > 1e-10:
                    h = min(h, until_purge)
            charge += self._advance(h)
            remaining -= h
        self.fid_current_A = charge / dt_s
        return self.observe()

    def mass_balance(self):
        result = {}
        for name, ledger in self.ledger.items():
            remaining = sum(b.injected_mass_kg * (1 - b.released_fraction)
                            for b in self.bands if b.species == name)
            accounted = (self.liner[name] + remaining + ledger["split_vent_kg"]
                         + ledger["septum_purge_kg"] + ledger["eluted_kg"])
            result[name] = dict(ledger, liner_kg=self.liner[name], column_remaining_kg=remaining,
                               residual_kg=ledger["injected_kg"] - accounted)
        return result

    def observe(self):
        reasons = self.not_ready_reasons()
        flow = self.flows()
        return {
            "parameter_provenance": PARAMETER_PROVENANCE, "time_s": self.time_s,
            "run_time_s": self.run_time_s, "powered": self.powered,
            "door_open": self.door_open, "syringe_inserted": self.syringe_inserted,
            "flame_lit": self.flame_lit, "running": self.running,
            "ready": not reasons, "not_ready_reasons": reasons,
            "temperatures_K": {name: z.temperature_K for name, z in self.zones.items()},
            "heater_power_W": {name: z.heater_power_W for name, z in self.zones.items()},
            "flows_m3_s": flow,
            "carrier_total_flow_m3_s": sum(flow[name] for name in ("column", "split_vent", "septum_purge")),
            "fid_current_A": self.fid_current_A, "fid_integral_C": self.fid_integral_C,
            "mass_balance_kg": self.mass_balance(),
            "vial_masses_kg": dict(self.vial), "syringe_masses_kg": dict(self.syringe),
        }


def synthetic_demo(output_dir):
    """Write explicitly synthetic chromatogram, provenance and action history."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    gc = GC()
    gc.perform("plug_power", connected=True)
    gc.perform("power", enabled=True)
    for gas in gc.gases:
        gc.perform("connect_gas", gas=gas, connected=True)
    gc.step(1200)
    gc.perform("ignite")
    gc.perform("start")
    gc.perform("aspirate", fraction=.01)
    gc.perform("insert_syringe")
    gc.perform("depress_plunger")
    gc.perform("retract_syringe")
    rows = []
    for _ in range(1800):
        state = gc.step(.5)
        rows.append({"synthetic_run_time_s": state["run_time_s"],
                     "synthetic_fid_current_A": state["fid_current_A"],
                     "synthetic_fid_integral_C": state["fid_integral_C"],
                     "oven_temperature_K": state["temperatures_K"]["oven"]})
    csv_path = output_dir / "synthetic_chromatogram.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    metadata = {"parameter_provenance": PARAMETER_PROVENANCE, "method": asdict(gc.method),
                "synthetic_species": [asdict(item) for item in gc.species.values()],
                "final_state": gc.observe(), "events": gc.events,
                "integration": "Each CSV current is an interval mean; integral = sum(current * 0.5 s)."}
    (output_dir / "synthetic_run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir", type=Path, required=True)
    args = parser.parse_args()
    print(synthetic_demo(args.demo_dir))
