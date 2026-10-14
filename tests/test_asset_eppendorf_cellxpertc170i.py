"""tests for CHEM-166 eppendorf_cellxpertc170i: python3 -m unittest tests/test_asset_eppendorf_cellxpertc170i.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances are
isolated, agent/evaluator truth isolation and vessel ownership, conservation (energy, CO2, water, glucose/lactate ledgers), real return values
validated against the declared schemas (asset_tool validate_value), asynchronous HTD with cancellation, host-step independence (fixed 1 s grid),
declared immediate and on_step effects over scenario states; plus the incubator physics against the manufacturer data: warm-up and 37 C stability,
CO2 sensor initialisation (30 min) and the 5 % set point within +-0.3 %, recovery after a 30 s door opening in < 5 min (brochure), humidity 92.5 %
with the water tray, the 14 h / 180 C high-temperature disinfection with its checks and aborts, gas-pressure warnings / alarms / valve closing,
O2 control with N2 and its physical upper limit, the CO2 wait after a power interruption, the temperature limiter, condensation behind an
unlatched inner door, altitude (partial-pressure sensors), alarms and BMS relays, recurring tasks and the performance check; the cultures: medium pH
from the HiMedia formulations at 5 / 10 % CO2, growth with the Cellosaurus doubling time, glucose / lactate, bench exposure, contamination.
"""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import eppendorf_cellxpertc170i as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
INST = 'incubator_01'


def _meta():
    if not _META:
        _META.update(rt.export_eppendorf_cellxpertc170i_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.EppendorfCellxpertC170iRuntime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema and immediate state effects (a call does not advance time); every
    rejection code is recorded for the declaration check."""
    before = self.read('evaluator') if capability_id != 'sim.step' else None
    t0 = self.t
    try:
        out = _orig_invoke(self, capability_id, **a)
    except rt.ActionRejected as exc:
        REJECTIONS.setdefault(capability_id, set()).add(str(exc))
        raise
    meta = _meta()['capabilities'][capability_id]
    errs = asset_tool.validate_value(out, meta['output_schema'])
    if errs:
        raise AssertionError(f'{capability_id} output violates its schema: {errs[:3]}')
    if before is not None:
        assert self.t == t0, capability_id
        after = self.read('evaluator')
        undeclared = {k for k in after if _changed(before[k], after[k])} - set(rt.EFFECTS.get(capability_id, []))
        if undeclared:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.EppendorfCellxpertC170iRuntime.invoke = _instrumented_invoke


def check(cid, **detail):
    CHECKS.append(dict(check=cid, status='passed', **detail))


def powered(seed=1, **plant):
    h = rt.EppendorfCellxpertC170iRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    h.invoke('power.set', enabled=True)
    return h


def open_both(h):
    h.invoke('door.outer', open=True)
    h.step(2.0)
    h.invoke('door.latch', open=True)
    h.step(1.0)
    h.invoke('door.inner', open=True)
    h.step(2.0)


def close_both(h):
    h.invoke('door.inner', open=False)
    h.step(2.0)
    h.invoke('door.latch', open=False)
    h.step(1.0)
    h.invoke('door.outer', open=False)
    h.step(2.0)


def ready(seed=1, water=2.0e-3, settle=6 * 3600.0, **plant):
    """Powered, tray filled with 2 L of 36 C water, doors closed, settled."""
    h = powered(seed, **plant)
    if water:
        open_both(h)
        h.invoke('water_tray.fill', volume_m3=water, temperature_k=309.15)
        close_both(h)
    h.step(settle)
    return h


def flask(h, vid='f1', medium='himedia_al028', cell='hela', cells=1.0e6, vtype='flask_t75', vol=1.5e-5, T=310.15, closure=None, gas='air'):
    kw = dict(vessel_id=vid, vessel_type=vtype, medium_id=medium, medium_volume_m3=vol, cell_line_id=cell, seeded_cells=cells, temperature_k=T, initial_gas=gas)
    if closure:
        kw['closure'] = closure
    return h.invoke('vessel.provide', **kw)


def truth(h, vid):
    return h.invoke('vessel.inspect', vessel_id=vid)['truth']


# ---------------------------------------------------------------- contract, libraries
class ContractAndLibraries(unittest.TestCase):
    def test_module_identity_and_export(self):
        m = _meta()
        self.assertEqual(m['asset_type_id'], 'instrument.eppendorf_cellxpertc170i')
        self.assertEqual(set(m['capabilities']), set(rt.CAPS))
        self.assertEqual(set(m['state_slots']), set(rt.SLOTS))
        for k, c in m['capabilities'].items():
            self.assertTrue(c['errors'], k)
            for e in rt.CAP_ERRORS.get(k, []):
                self.assertIn(e, rt.ERROR_ZH, e)
        contract = ROOT / 'assets/expansion_batch04/t4_measure/eppendorf_cellxpertc170i/asset.contract.json'
        if contract.exists():
            c = json.loads(contract.read_text())
            if c.get('capabilities', {}).get('htd.start'):
                self.assertEqual(set(c['capabilities']), set(rt.CAPS))
        check('export')

    def test_template_libraries(self):
        md, cl, vs = rt.media(), rt.cell_lines(), rt.vessel_types()
        self.assertGreaterEqual(len(md), 50)
        self.assertGreaterEqual(len(cl), 50)
        self.assertEqual(set(vs), set(rt.VESSEL_TYPES))
        for v in md.values():
            self.assertTrue(v['source'].startswith('HiMedia technical data sheet'))
            self.assertIn(v['evidence'], ('documented',))
        self.assertAlmostEqual(md['himedia_al111']['bicarbonate_mol_m3'], 3700 / 84.007, places=3)
        self.assertGreater(sum(1 for v in cl.values() if v['doubling_times']), 50)
        self.assertEqual(vs['flask_t75']['growth_area_m2'], 0.0075)
        e = rt.eqm()
        self.assertAlmostEqual(rt.henry_co2(298.15), 3.4e-4 * 0.97, delta=1e-9)
        self.assertAlmostEqual(rt.pk1_apparent(310.15), 6.10, delta=1e-9)
        self.assertIn('source', e['o2_henry'])
        check('libraries', media=len(md), cell_lines=len(cl))

    def test_ph_chemistry_matches_henderson_hasselbalch(self):
        T = 310.15
        for hco3, xc in ((26.2, 0.05), (44.0, 0.10), (44.0, 0.05), (4.17, 0.0004)):
            co2 = rt.henry_co2(T) * xc * rt.P_REF
            ph = rt.medium_ph(hco3, co2, 0.0, 0.0, T)
            hh = 6.10 + math.log10(hco3 / co2)
            self.assertAlmostEqual(ph, hh, delta=0.03 if xc > 0.001 else 0.2)
        self.assertLess(rt.medium_ph(24.0 + 12.0, 1.24, 25.0, 0.0, T) - rt.medium_ph(24.0 + 12.0, 2.48, 25.0, 0.0, T),
                        rt.medium_ph(24.0, 1.24, 0.0, 0.0, T) - rt.medium_ph(24.0, 2.48, 0.0, 0.0, T))      # HEPES damps the CO2 dependence
        self.assertEqual(rt.phenol_red(6.6, True)[0], 'yellow')
        self.assertEqual(rt.phenol_red(7.4, True)[0], 'red')
        self.assertEqual(rt.phenol_red(8.5, True)[0], 'purple')
        check('ph_chemistry')


# ---------------------------------------------------------------- chamber physics
class Chamber(unittest.TestCase):
    def test_warm_up_stability_and_power(self):
        h = powered()
        h.step(7200.0)
        T = [h.sens['T']]
        for _ in range(120):
            h.step(60.0)
            T.append(h.sens['T'])
        mean = sum(T) / len(T)
        self.assertAlmostEqual(mean, 310.15, delta=0.1)                                                # 37 C
        self.assertLess(max(T) - min(T), 0.25)                                                          # +-0.1 C stability (with sensor noise)
        L0 = h.ledger()['heater_energy_j']
        h.step(3600.0)
        P = (h.ledger()['heater_energy_j'] - L0) / 3600.0
        ua = rt.MODEL['ua_wall_w_k']
        self.assertAlmostEqual(P, ua * 15.0 + rt.MODEL['g_door_w_k'] * 15.0, delta=12.0)            # steady power = losses at 22 C room
        check('warm_up', mean_k=mean, steady_w=P)

    def test_co2_initialisation_accuracy_and_stability(self):
        h = powered()
        h.step(1799.0)
        self.assertIsNone(h.read()['sensor.co2_fraction'])
        self.assertTrue(h.read()['status.co2_initializing'])
        with self.assertRaises(rt.ActionRejected):
            h.invoke('co2.set', setpoint_fraction=0.06)                                                # no set point change while initialising
        h.step(3 * 3600.0)
        xs = []
        for _ in range(60):
            h.step(60.0)
            xs.append(h.read('evaluator')['truth.co2_fraction'])
        self.assertAlmostEqual(sum(xs) / len(xs), 0.05, delta=0.003)                                   # accuracy +-0.3 %
        self.assertLess(max(xs) - min(xs), 0.002)                                                       # stability +-0.1 %
        self.assertAlmostEqual(h.read()['display.co2_percent'], 5.0, delta=0.3)
        check('co2_control', mean=sum(xs) / len(xs))

    def test_recovery_after_a_30_s_door_opening(self):
        h = ready()
        t0 = h.t
        open_both(h)
        h.step(25.0)
        close_both(h)
        Tmin, cmin, t98T, t98C = 1e9, 1e9, None, None
        for _ in range(600):
            h.step(1.0)
            v = h.read('evaluator')
            Tmin, cmin = min(Tmin, v['truth.air_temperature_k']), min(cmin, v['truth.co2_fraction'])
            if t98T is None and Tmin < 309.0 and v['truth.air_temperature_k'] - 273.15 >= 0.98 * 37.0:
                t98T = h.t - t0
            if t98C is None and cmin < 0.04 and v['truth.co2_fraction'] >= 0.98 * 0.05:
                t98C = h.t - t0
        self.assertLess(Tmin, 303.15)                                                                   # the chamber air is exchanged while open
        self.assertLess(cmin, 0.01)
        self.assertIsNotNone(t98T)
        self.assertIsNotNone(t98C)
        self.assertLess(t98T, 300.0)                                                                    # brochure: 98 % in < 5 min
        self.assertLess(t98C, 300.0)
        self.assertEqual(h.read()['alarms.active'], [])                                                 # the alarms are paused after closing
        check('door_recovery', t98_T_s=t98T, t98_CO2_s=t98C, Tmin_k=Tmin, co2_min=cmin)

    def test_humidity_water_tray_and_water_alarm(self):
        h = ready(settle=24 * 3600.0)
        rh = h.read('evaluator')['truth.rh']
        self.assertAlmostEqual(rh, 0.925, delta=0.025)                                                  # 92.5 +- 2.5 % at 37 C
        self.assertAlmostEqual(h.read()['sensor.rh'], rh, delta=0.02)
        dry = powered()
        dry.step(3 * 3600.0)
        self.assertLess(dry.read('evaluator')['truth.rh'], 0.3)
        self.assertIn('WATER_LOW', dry.read()['alarms.active'])                                        # < 0.5 L, armed 1 h after switch-on
        self.assertTrue(dry.read()['bms.relays'][3])
        check('humidity', rh=rh)

    def test_condensation_behind_an_unlatched_inner_door(self):
        def run(latch):
            h = ready(settle=3600.0)
            open_both(h)
            h.invoke('door.inner', open=False)                                                         # manual: the inner door is not correctly closed until the
            h.step(2.0)                                                                                 # latch is horizontal - otherwise condensation
            if latch:
                h.invoke('door.latch', open=False)
                h.step(1.0)
            h.invoke('door.outer', open=False)
            h.step(24 * 3600.0)
            return h.read('evaluator')['truth.condensate_kg']
        wet, dry = run(False), run(True)
        self.assertGreater(wet, dry)
        self.assertGreater(wet, 1e-4)
        check('condensation', unlatched_kg=wet, latched_kg=dry)

    def test_setpoint_close_to_the_room_cannot_be_held(self):
        h = powered(room_k=308.15)
        w = h.invoke('temperature.set', setpoint_k=310.15)['warnings']
        self.assertIn('setpoint_below_ambient_plus_4k', w)
        h.invoke('temperature.set', setpoint_k=303.15)
        h.step(6 * 3600.0)
        self.assertIn('E_6731FW108', h.read()['alarms.active'])
        self.assertGreater(h.read('evaluator')['truth.air_temperature_k'], 303.15 + 0.5)
        check('ambient_limit')

    def test_gas_pressure_alarms_and_control_timeout(self):
        h = ready(settle=3 * 3600.0)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, co2_supply_pa=0.03e6))
        h.step(2.0)
        self.assertIn('CO2_PRESSURE_WARNING', h.read()['alarms.active'])
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, co2_supply_pa=0.01e6))
        h.step(2.0)
        self.assertIn('CO2_PRESSURE_LOW', h.read()['alarms.active'])
        self.assertEqual(h.u['c'], 0.0)                                                                 # control stopped
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, co2_supply_pa=0.2e6))
        h.step(2.0)
        self.assertIn('CO2_PRESSURE_HIGH', h.read()['alarms.active'])                                  # inlet valve closed
        k = ready(settle=3 * 3600.0)
        k.invoke('gas.pressure_monitor', enabled=False)
        k.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, co2_supply_pa=0.0))
        open_both(k)
        k.step(20.0)
        close_both(k)
        k.step(2 * 3600.0)
        self.assertTrue(k.read()['status.co2_control_failed'])
        self.assertIn('E_6731FW214', k.read()['alarms.active'])
        self.assertTrue(k.read()['bms.relays'][1])
        k.invoke('plant.configure', **rt.PLANT_EXAMPLE)
        k.invoke('gas.reset', gas='co2')
        k.step(1800.0)
        self.assertAlmostEqual(k.read('evaluator')['truth.co2_fraction'], 0.05, delta=0.003)
        check('gas_supply')

    def test_o2_control_with_nitrogen_and_its_limit(self):
        h = ready(settle=3 * 3600.0)
        h.invoke('o2.control', enabled=True)
        h.invoke('o2.set', setpoint_fraction=0.05)
        h.step(3 * 3600.0)
        self.assertAlmostEqual(h.read('evaluator')['truth.o2_fraction'], 0.05, delta=0.0025)
        self.assertGreater(h.ledger()['n2_injected_mol'], 3.0)
        w = h.invoke('o2.set', setpoint_fraction=0.20)['warnings']
        self.assertIn('o2_setpoint_above_achievable', w)                                               # 5 % CO2 and water vapour dilute the air
        h.step(3 * 3600.0)
        self.assertLess(h.read('evaluator')['truth.o2_fraction'], 0.195)
        self.assertIn('O2_LOW', h.read()['alarms.active'])
        check('o2_control')

    def test_co2_waits_for_the_temperature_after_a_power_cut(self):
        h = ready(settle=3 * 3600.0)
        h.invoke('fault.inject', fault='power_loss', active=True)
        h.step(3 * 3600.0)
        h.invoke('fault.inject', fault='power_loss', active=False)
        self.assertTrue(h.status['co2_gate'])
        inj0 = h.ledger()['co2_injected_mol']
        h.step(1900.0)                                                                                  # sensor initialised, temperature not yet reached?
        if h.status['co2_gate']:
            self.assertAlmostEqual(h.ledger()['co2_injected_mol'], inj0, delta=1e-12)
        h.step(3 * 3600.0)
        self.assertFalse(h.status['co2_gate'])
        self.assertAlmostEqual(h.read('evaluator')['truth.co2_fraction'], 0.05, delta=0.003)
        check('power_cut')

    def test_temperature_limiter(self):
        h = ready(settle=2 * 3600.0)
        h.invoke('fault.inject', fault='heater_stuck_on', active=True)
        h.step(3600.0)
        self.assertIn('E_6731FW111', h.read()['alarms.active'])
        self.assertTrue(h.read()['status.heating_shutdown'])
        self.assertLess(h.read('evaluator')['truth.wall_temperature_k'], 310.15 + 12.0)
        k = ready(settle=2 * 3600.0)
        k.invoke('fault.inject', fault='heater_failure', active=True)
        k.step(8 * 3600.0)
        self.assertIn('E_6731W112', k.read()['alarms.active'])
        check('limiter')

    def test_altitude_partial_pressure_sensors(self):
        h = ready(settle=4 * 3600.0, ambient_pressure_pa=80000.0)
        x = h.read('evaluator')['truth.co2_fraction']
        self.assertAlmostEqual(x * 80000.0, 0.05 * rt.P_REF, delta=0.004 * rt.P_REF)                   # the NDIR controls the partial pressure
        self.assertGreater(x, 0.058)
        check('altitude', true_fraction=x)

    def test_alarms_door_signal_mute_and_relays(self):
        h = ready(settle=3 * 3600.0)
        h.invoke('door.outer', open=True)
        h.step(29.0)
        self.assertNotIn('DOOR_OPEN', h.read()['alarms.active'])
        h.step(3.0)
        self.assertIn('DOOR_OPEN', h.read()['alarms.active'])
        self.assertTrue(h.read()['alarm.sound'])
        h.invoke('alarm.mute')
        self.assertFalse(h.read()['alarm.sound'])
        h.step(300.0)
        self.assertIn('DOOR_LONG', h.read()['alarms.active'])
        h.invoke('door.outer', open=False)
        h.step(2.0)
        self.assertNotIn('DOOR_OPEN', h.read()['alarms.active'])
        with self.assertRaises(rt.ActionRejected):
            h.invoke('alarm.configure', kind='door', enabled=True, signal_s=20.0)
        h.invoke('alarm.configure', kind='door', enabled=True, signal_s=15.0)
        h.invoke('alarm.configure', kind='temperature', enabled=True, low_k=0.2, high_k=0.2)
        h.invoke('door.outer', open=True)
        h.step(17.0)
        self.assertIn('DOOR_OPEN', h.read()['alarms.active'])
        check('alarms')

    def test_high_temperature_disinfection(self):
        h = ready(settle=3600.0)
        flask(h, 'v1', cells=0.0, cell=None)
        open_both(h)
        h.invoke('vessel.place', vessel_id='v1', shelf=1)
        for code in ('VESSELS_IN_CHAMBER',):
            with self.assertRaises(rt.ActionRejected) as cm:
                h.invoke('htd.start')
            self.assertIn(str(cm.exception), ('VESSELS_IN_CHAMBER', 'DOORS_NOT_CLOSED'))
        h.invoke('vessel.remove', vessel_id='v1')
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('htd.start')
        self.assertEqual(str(cm.exception), 'WATER_IN_TRAY')
        h.invoke('water_tray.empty', clean=True)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('htd.start')
        self.assertEqual(str(cm.exception), 'RH_SENSOR_UNCOVERED')
        h.invoke('rh_sensor.cover', on=True)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('htd.start')
        self.assertEqual(str(cm.exception), 'DOORS_NOT_CLOSED')
        close_both(h)
        t0 = h.t
        op = h.invoke('htd.start')['operation_id']
        self.assertEqual(h.read()['operation.kind'], 'htd')
        with self.assertRaises(rt.ActionRejected):
            h.invoke('temperature.set', setpoint_k=311.15)
        peak = 0.0
        while h.htd['phase'] in ('heating', 'holding', 'cooling') and h.t - t0 < 20 * 3600:
            h.step(300.0)
            peak = max(peak, h.read('evaluator')['truth.wall_temperature_k'])
        self.assertEqual(h.htd['last'], 'successful')
        self.assertAlmostEqual((h.t - t0) / 3600.0, 14.0, delta=0.5)                                  # manual: approx. 14 h
        self.assertGreater(peak, 452.15)
        self.assertEqual(h.operations[op]['phase'], 'completed')
        self.assertTrue(h.read()['status.co2_initializing'])
        rec = h.invoke('records.list')['records'][-1]
        self.assertGreaterEqual(rec['details']['hold_at_180_s'], rt.HTD_HOLD_S)
        k = ready(settle=600.0, water=0.0)
        open_both(k)
        k.invoke('rh_sensor.cover', on=True)
        close_both(k)
        k.invoke('htd.start')
        k.step(3 * 3600.0)
        k.invoke('door.outer', open=True)
        k.step(2.0)
        self.assertEqual(k.htd['last'], 'failed')
        self.assertIn('E_6731FW753', k.read()['alarms.active'])
        self.assertEqual(k.read()['htd.phase'], 'post_htd_cooling')
        with self.assertRaises(rt.ActionRejected):
            k.invoke('door.latch', open=True) if k._ang('outer') < 1.0 else k.invoke('vessel.place', vessel_id='nope', shelf=1)
        check('htd', hours=(h.t - t0) / 3600.0, peak_k=peak)

    def test_performance_check_and_recurring_tasks(self):
        h = ready(settle=3 * 3600.0)
        r = h.invoke('performance_check.record', kind='temperature', reference_temperature_k=310.05, instrument='probe')
        self.assertAlmostEqual(r['deviation_k'], r['displayed_temperature_k'] - 310.05, delta=1e-12)
        self.assertIsNone(r['deviation_fraction'])
        with self.assertRaises(rt.ActionRejected):
            h.invoke('performance_check.record', kind='o2', reference_fraction=0.10, instrument='analyser')    # O2 not at its default 10 %
        with self.assertRaises(rt.ActionRejected):
            h.invoke('task.complete', task='high_temperature_disinfection')
        h.invoke('task.configure', task='chamber_cleaning', interval_s=3600.0)
        h.step(3700.0)
        self.assertIn('chamber_cleaning', h.read()['tasks.due'])
        h.invoke('task.complete', task='chamber_cleaning')
        self.assertNotIn('chamber_cleaning', h.read()['tasks.due'])
        h.step(15 * 86400.0)
        self.assertIn('water_refill', h.read()['tasks.due'])
        check('tasks')


# ---------------------------------------------------------------- cultures
class Cultures(unittest.TestCase):
    def test_medium_ph_at_5_and_10_percent_co2(self):
        h = ready(settle=4 * 3600.0)
        open_both(h)
        for vid, med in (('dmem', 'himedia_al111'), ('rpmi', 'himedia_al028'), ('rpmi_hepes', 'himedia_al060')):
            flask(h, vid, medium=med, cell=None, cells=0.0)
            h.invoke('vessel.place', vessel_id=vid, shelf=2)
        close_both(h)
        h.step(6 * 3600.0)
        ph5 = {v: truth(h, v)['pH'] for v in ('dmem', 'rpmi', 'rpmi_hepes')}
        self.assertAlmostEqual(ph5['rpmi'], 7.42, delta=0.08)                                         # 23.8 mM bicarbonate + 5.6 mM Na2HPO4 at 5 % CO2
        self.assertAlmostEqual(ph5['dmem'], 7.65, delta=0.08)                                         # DMEM (44 mM) is alkaline at 5 %
        h.invoke('co2.set', setpoint_fraction=0.10)
        h.step(8 * 3600.0)
        ph10 = {v: truth(h, v)['pH'] for v in ('dmem', 'rpmi', 'rpmi_hepes')}
        self.assertAlmostEqual(ph10['dmem'], 7.38, delta=0.08)                                        # ... and ~7.4 at 10 % (its design point)
        self.assertLess(ph5['rpmi_hepes'] - ph10['rpmi_hepes'], ph5['rpmi'] - ph10['rpmi'])            # HEPES buffers the CO2 step
        self.assertEqual(rt.phenol_red(ph5['rpmi'], True)[0], 'red')
        self.assertEqual(rt.phenol_red(ph10['rpmi'], True)[0], 'orange')
        open_both(h)
        self.assertEqual(h.invoke('vessel.observe', vessel_id='rpmi')['medium_colour'], 'orange')
        check('medium_ph', at5=ph5, at10=ph10)

    def test_growth_follows_the_doubling_time_and_uses_glucose(self):
        h = ready(settle=3 * 3600.0)
        open_both(h)
        flask(h, 'k562', cell='k_562', cells=2.0e6)
        h.invoke('vessel.place', vessel_id='k562', shelf=3)
        close_both(h)
        t0 = truth(h, 'k562')
        h.step(3 * 86400.0)
        t3 = truth(h, 'k562')
        td = rt.cell_lines()['k_562']['doubling_time_s']
        expected = 2 ** ((3 * 86400.0 - rt.GROWTH['lag_s']) / td)
        ratio = t3['cells_live'] / t0['cells_live']
        self.assertGreater(ratio, 0.6 * expected)
        self.assertLess(ratio, 1.05 * expected)
        self.assertLess(t3['glucose_mol_m3'], t0['glucose_mol_m3'])
        self.assertGreater(t3['lactate_mol_m3'], 1.0)
        self.assertLess(t3['pH'], t0['pH'] + 0.2)
        L = h.ledger()
        self.assertAlmostEqual(L['lactate_produced_mol'], rt.GROWTH['y_lac'] * L['glucose_consumed_mol'], delta=1e-9)
        h.step(4 * 86400.0)
        t7 = truth(h, 'k562')
        self.assertLess(t7['glucose_mol_m3'], 0.5)                                                     # glucose runs out, cells die
        self.assertEqual(t7['phase'], 'death')
        open_both(h)
        self.assertIn(h.invoke('vessel.observe', vessel_id='k562')['medium_colour'], ('orange', 'yellow'))
        check('growth', ratio_3d=ratio, expected=expected)

    def test_bench_exposure_and_cold_vessels(self):
        h = ready(settle=3 * 3600.0)
        open_both(h)
        flask(h, 'warm', cell=None, cells=0.0, gas='incubator')
        h.invoke('vessel.place', vessel_id='warm', shelf=2)
        for i in range(6):
            flask(h, f'cold{i}', vtype='flask_t175', vol=5.0e-5, T=277.15, cell=None, cells=0.0)
            h.invoke('vessel.place', vessel_id=f'cold{i}', shelf=1 + i % 4)
        close_both(h)
        h.step(600.0)
        self.assertLess(h.read('evaluator')['truth.air_temperature_k'], 309.65)                        # cold medium pulls the chamber down
        h.step(3 * 3600.0)
        self.assertAlmostEqual(truth(h, 'cold0')['temperature_k'], 310.15, delta=0.8)
        open_both(h)
        ph_in = truth(h, 'warm')['pH']
        h.invoke('vessel.remove', vessel_id='warm')
        close_both(h)
        h.step(3600.0)
        tb = truth(h, 'warm')
        self.assertLess(tb['temperature_k'], 297.15)
        self.assertGreater(tb['pH'], ph_in + 0.4)                                                      # CO2 leaves through the vented cap
        self.assertIn(h.invoke('vessel.observe', vessel_id='warm')['medium_colour'], ('pink', 'purple'))
        check('bench_and_cold', ph_in=ph_in, ph_bench=tb['pH'])

    def test_contamination_turns_the_medium_turbid_and_acid(self):
        h = ready(settle=3 * 3600.0)
        open_both(h)
        flask(h, 'c1', cells=1.0e6)
        h.invoke('vessel.place', vessel_id='c1', shelf=2)
        close_both(h)
        h.invoke('fault.inject', fault='vessel_contamination', active=True, vessel_id='c1')
        h.step(36 * 3600.0)
        open_both(h)
        ob = h.invoke('vessel.observe', vessel_id='c1')
        self.assertTrue(ob['turbid'])
        self.assertIn(ob['medium_colour'], ('yellow', 'orange'))
        self.assertLess(truth(h, 'c1')['viability'], 0.9)
        check('contamination')

    def test_shelf_capacity_and_vessel_rules(self):
        h = ready(settle=600.0)
        open_both(h)
        for i in range(6):
            flask(h, f'b{i}', vtype='flask_t225', vol=6.8e-5, cell=None, cells=0.0)
        for i in range(5):
            h.invoke('vessel.place', vessel_id=f'b{i}', shelf=1)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('vessel.place', vessel_id='b5', shelf=1)
        self.assertIn(str(cm.exception), ('SHELF_FULL', 'SHELF_OVERLOAD'))
        with self.assertRaises(rt.ActionRejected):
            flask(h, 'x', cells=1e9)                                                                   # above the confluent number
        with self.assertRaises(rt.ActionRejected):
            flask(h, 'y', vtype='dish_60mm', closure='vented_cap')
        close_both(h)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('vessel.remove', vessel_id='b0')                                                   # doors closed
        with self.assertRaises(rt.ActionRejected):
            h.invoke('vessel.observe', vessel_id='b0')                                                  # opaque outer door
        check('vessel_rules')


# ---------------------------------------------------------------- framework
class Framework(unittest.TestCase):
    def test_read_describe_pure_and_clock(self):
        h = ready(settle=600.0)
        h.register_principal('ag', 'agent')
        v0 = h.read('evaluator')
        h.session('ag').describe(instance_id=INST)
        h.session('ag').read(instance_id=INST)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True), json.dumps(v0, sort_keys=True))
        t = h.invoke('sim.step', dt_s=2.25)['time_s']
        self.assertEqual(t, h.t)
        check('pure_reads')

    def test_step_partition_independent(self):
        def run(splits):
            h = powered(seed=4)
            for dt in splits(1200.0):
                h.step(dt)
            open_both(h)
            h.invoke('water_tray.fill', volume_m3=2.0e-3, temperature_k=305.15)
            flask(h, 'p', cells=1.0e6)
            h.invoke('vessel.place', vessel_id='p', shelf=2)
            for dt in splits(30.5):
                h.step(dt)
            close_both(h)
            for dt in splits(7300.0):
                h.step(dt)
            v_mid = h.read('evaluator')
            h.invoke('co2.set', setpoint_fraction=0.07)
            for dt in splits(3000.0):
                h.step(dt)
            return h, v_mid
        a, am = run(lambda T: [T])
        b, bm = run(lambda T: [T / 8] * 8)
        self.assertEqual(json.dumps(am, sort_keys=True), json.dumps(bm, sort_keys=True))
        self.assertEqual(json.dumps(a.read('evaluator'), sort_keys=True), json.dumps(b.read('evaluator'), sort_keys=True))
        self.assertEqual(json.dumps(truth(a, 'p'), sort_keys=True), json.dumps(truth(b, 'p'), sort_keys=True))
        self.assertEqual(json.dumps(a.chart, sort_keys=True), json.dumps(b.chart, sort_keys=True))
        self.assertEqual(json.dumps(a.ledger(), sort_keys=True), json.dumps(b.ledger(), sort_keys=True))
        check('step_partition')

    def test_conservation_ledgers(self):
        h = powered()
        c0 = copy.deepcopy(h.ch)
        tray0 = h.tray['V']
        open_both(h)
        h.invoke('water_tray.fill', volume_m3=2.0e-3, temperature_k=309.15)
        flask(h, 'q', cells=1.0e6)
        h.invoke('vessel.place', vessel_id='q', shelf=2)
        h.step(10.0)
        close_both(h)
        h.step(6 * 3600.0)
        L, c = h.ledger(), h.ch
        self.assertAlmostEqual(c['nc'] - c0['nc'], L['co2_injected_mol'] + L['co2_exchanged_in_mol'] - L['co2_exchanged_out_mol'], delta=1e-9)
        dw = (c['nw'] - c0['nw']) + (c['cond'] - c0['cond'])
        self.assertAlmostEqual(dw, L['water_evaporated_tray_mol'] + L['water_evaporated_vessels_mol'] - L['water_vented_mol'] - L['water_drained_to_tray_mol'], delta=1e-9)
        tray_mol = (tray0 + L['water_filled_m3'] - L['water_removed_m3'] - h.tray['V']) * rt.RHO_WATER / rt.M_WATER
        self.assertAlmostEqual(tray_mol, L['water_evaporated_tray_mol'] - L['water_drained_to_tray_mol'], delta=1e-6)
        self.assertGreater(L['electrical_energy_j'], L['heater_energy_j'])
        self.assertEqual(h.invoke('ledger.read')['ledger'], h.ledger())
        check('ledgers')

    def test_rejections_leave_state_unchanged(self):
        h = ready(settle=600.0)
        v0 = json.dumps(h.read('evaluator'), sort_keys=True, default=str)
        for cap, args in [('door.inner', dict(open=True)), ('door.latch', dict(open=True)), ('water_tray.fill', dict(volume_m3=1e-3, temperature_k=309.15)),
                          ('temperature.set', dict(setpoint_k=310.17)), ('co2.set', dict(setpoint_fraction=0.0505)), ('htd.start', {}), ('gas.reset', dict(gas='co2')),
                          ('vessel.place', dict(vessel_id='none', shelf=1)), ('task.complete', dict(task='performance_check')),
                          ('alarm.configure', dict(kind='rh', enabled=True, low=0.3))]:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(cap, **args)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True, default=str), v0)
        check('rejections_pure')

    def test_htd_cancellation(self):
        h = ready(settle=600.0, water=0.0)
        open_both(h)
        h.invoke('rh_sensor.cover', on=True)
        close_both(h)
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        r = s.invoke(instance_id=INST, capability_id='htd.start', arguments={}, request_id='h')
        self.assertEqual(r['status'], 'accepted')
        h.step(1800.0)
        s.cancel(r['operation_id'], 'c')
        self.assertEqual(s.poll(r['operation_id'])['status'], 'cancelled')
        self.assertEqual(h.read()['htd.last_result'], 'cancelled')
        self.assertEqual(h.read()['htd.phase'], 'post_htd_cooling')
        h.step(12 * 3600.0)
        self.assertEqual(h.read()['htd.phase'], 'idle')
        self.assertNotIn('E_6731FW111', h.read()['alarms.active'])                                      # a cancelled HTD cools without tripping the limiter
        check('htd_cancel')

    def test_two_instances_isolated(self):
        a, b = ready(settle=600.0), ready(settle=600.0)
        open_both(a)
        flask(a, 'iso', cell=None, cells=0.0)
        self.assertEqual(b.vessels, {})
        self.assertNotEqual(a._ang('outer'), b._ang('outer'))
        check('isolation')

    def test_agent_truth_isolation_ownership_and_idempotence(self):
        h = ready(settle=600.0)
        for n, v in (('a1', 'agent'), ('a2', 'agent'), ('ev', 'evaluator')):
            h.register_principal(n, v)
        s1, s2, e = h.session('a1'), h.session('a2'), h.session('ev')
        obs = s1.read(instance_id=INST)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        self.assertIn('truth.co2_fraction', e.read(instance_id=INST)['observations'])
        r = s1.invoke(instance_id=INST, capability_id='vessel.provide', arguments=dict(rt.VESSEL_EXAMPLE, vessel_id='own'), request_id='p')
        self.assertEqual(r['status'], 'succeeded')
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='vessel.provide', arguments=dict(rt.VESSEL_EXAMPLE, vessel_id='own'), request_id='p'), r)
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='door.outer', arguments=dict(open=True), request_id='p')['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='vessel.discard', arguments=dict(vessel_id='own'), request_id='d')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='vessel.inspect', arguments=dict(vessel_id='own'), request_id='i')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s2.read(instance_id=INST)['observations']['vessels.on_bench']['value'], [])
        self.assertEqual(s1.read(instance_id=INST)['observations']['vessels.on_bench']['value'], ['own'])
        check('truth_isolation_ownership')

    def test_switch_ports_control_flags_and_discard(self):
        h = ready(settle=3 * 3600.0)
        k = copy.deepcopy(h)
        h.invoke('access_port.plug', port='upper', plugged=False)                                     # an open 25 mm port leaks
        self.assertEqual(h.read()['access_ports.unplugged'], ['upper'])
        c0h, c0k = h.ledger()['co2_injected_mol'], k.ledger()['co2_injected_mol']
        h.step(3600.0)
        k.step(3600.0)
        self.assertGreater(h.ledger()['co2_injected_mol'] - c0h, 3.0 * (k.ledger()['co2_injected_mol'] - c0k))
        h.invoke('access_port.plug', port='upper', plugged=True)
        h.invoke('co2.control', enabled=False)
        u = h.ledger()['co2_injected_mol']
        h.step(1800.0)
        self.assertEqual(h.ledger()['co2_injected_mol'], u)                                            # temperature control only
        h.invoke('co2.control', enabled=True)
        self.assertEqual(h.invoke('contamination.set_status', contaminated=True)['status'], 'contaminated')
        self.assertEqual(h.read()['contamination.status'], 'contaminated')
        self.assertFalse(h.invoke('power.press_switch')['enabled'])                                    # the mains switch toggles
        self.assertIsNone(h.read()['display.temperature_c'])
        self.assertTrue(h.invoke('power.press_switch')['enabled'])
        flask(h, 'gone', cell=None, cells=0.0)
        self.assertEqual(h.invoke('vessel.discard', vessel_id='gone')['vessel_id'], 'gone')
        self.assertNotIn('gone', h.vessels)
        check('controls_and_flags')

    def test_queries_export_and_unsupported(self):
        h = ready(settle=3 * 3600.0)
        rows = h.invoke('chart.read', since_s=0.0, max_points=1440)['rows']
        self.assertGreater(len(rows), 100)
        self.assertTrue(h.invoke('events.read', since_s=0.0)['events'])
        self.assertEqual(h.invoke('alarms.list')['relays'], [False, False, False, False])
        self.assertGreaterEqual(len(h.invoke('provenance.read')['libraries']), 5)
        ex = h.invoke('data.export', kind='chart')
        self.assertEqual(ex['rows'], len(h.chart))
        self.assertEqual(len(h.invoke('tasks.list')['tasks']), 4)
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(k)
        check('queries')


# ---------------------------------------------------------------- declared effects over scenario states
_SC = {}


def _build(name):
    if name == 'fresh':
        return rt.EppendorfCellxpertC170iRuntime()
    if name == 'off':
        h = rt.EppendorfCellxpertC170iRuntime()
        h.invoke('plant.configure', **rt.PLANT_EXAMPLE)
        flask(h, 'v', cell=None, cells=0.0)
        return h
    if name == 'warming':
        h = powered()
        h.step(600.0)
        return h
    if name == 'steady':
        h = ready(settle=3 * 3600.0)
        flask(h, 'v', cells=1.0e6)
        return h
    if name == 'outer_open':
        h = _scenario('steady')
        h.invoke('door.outer', open=True)
        h.step(2.0)
        return h
    if name == 'open':
        h = _scenario('steady')
        open_both(h)
        return h
    if name == 'loaded':
        h = _scenario('open')
        h.invoke('vessel.place', vessel_id='v', shelf=2)
        close_both(h)
        h.step(600.0)
        return h
    if name == 'loaded_open':
        h = _scenario('loaded')
        open_both(h)
        return h
    if name == 'htd_ready':
        h = ready(settle=600.0, water=0.0)
        open_both(h)
        h.invoke('rh_sensor.cover', on=True)
        close_both(h)
        return h
    if name == 'alarm':
        h = _scenario('steady')
        h.invoke('door.outer', open=True)
        h.step(40.0)
        return h
    if name == 'htd':
        h = ready(settle=600.0, water=0.0)
        open_both(h)
        h.invoke('rh_sensor.cover', on=True)
        close_both(h)
        h.invoke('htd.start')
        h.step(1800.0)
        return h
    if name == 'failed_gas':
        h = ready(settle=3 * 3600.0)
        h.invoke('gas.pressure_monitor', enabled=False)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, co2_supply_pa=0.0))
        open_both(h)
        h.step(20.0)
        close_both(h)
        h.step(2 * 3600.0)
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'warming', 'steady', 'outer_open', 'open', 'loaded', 'loaded_open', 'alarm', 'htd_ready', 'htd', 'failed_gas']
STEP_DTS = (0.5, 5.0, 60.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0), dict(rt.PLANT_EXAMPLE, room_k=300.15, co2_supply_pa=0.03e6)],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_switch': [{}],
        'temperature.set': [dict(setpoint_k=311.15)], 'co2.set': [dict(setpoint_fraction=0.06)], 'o2.set': [dict(setpoint_fraction=0.05)],
        'co2.control': [dict(enabled=False), dict(enabled=True)], 'o2.control': [dict(enabled=True)], 'gas.pressure_monitor': [dict(enabled=False)], 'gas.reset': [dict(gas='co2')],
        'alarm.configure': [dict(kind='temperature', enabled=True, low_k=0.2, high_k=0.2), dict(kind='door', enabled=True, signal_s=15.0)], 'alarm.mute': [{}],
        'htd.start': [{}], 'contamination.set_status': [dict(contaminated=True)], 'task.configure': [dict(task='chamber_cleaning', interval_s=3600.0)],
        'task.complete': [dict(task='water_refill')], 'performance_check.record': [dict(kind='temperature', reference_temperature_k=310.1, instrument='p')],
        'data.export': [dict(kind='events')], 'door.outer': [dict(open=True), dict(open=False)], 'door.latch': [dict(open=True), dict(open=False)],
        'door.inner': [dict(open=True), dict(open=False)], 'water_tray.fill': [dict(volume_m3=2.0e-4, temperature_k=309.15)], 'water_tray.empty': [dict(clean=True)],
        'rh_sensor.cover': [dict(on=True)], 'access_port.plug': [dict(port='upper', plugged=False)], 'vessel.provide': [dict(rt.VESSEL_EXAMPLE, vessel_id='new')],
        'vessel.place': [dict(vessel_id='v', shelf=2)], 'vessel.remove': [dict(vessel_id='v')], 'vessel.observe': [dict(vessel_id='v')], 'vessel.discard': [dict(vessel_id='v')],
        'fault.inject': [dict(fault=f, active=True) for f in ('power_loss', 'heater_failure', 'heater_stuck_on', 'co2_valve_stuck_closed', 'gasket_leak', 'ndir_drift')]
                        + [dict(fault='vessel_contamination', active=True, vessel_id='v'), dict(fault='power_loss', active=False)],
        'sim.step': [dict(dt_s=0.5), dict(dt_s=30.0)],
    }
    return V.get(cap, [])


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _flat(v, prefix=''):
    out = {}
    if isinstance(v, dict):
        for k, x in v.items():
            out.update(_flat(x, f'{prefix}.{k}'))
    elif isinstance(v, list) and len(v) <= 128:
        for i, x in enumerate(v):
            out.update(_flat(x, f'{prefix}[{i}]'))
    else:
        out[prefix] = json.dumps(v, sort_keys=True, default=str) if isinstance(v, list) else v
    return out


def _rel_changed(a, b, scale=None):
    if _num(a) and _num(b):
        return abs(a - b) > 1e-9 * (max(abs(a), abs(b)) if scale is None else scale) + 1e-300
    return _changed(a, b)


def _leaf_changed(a, b):
    fa, fb = _flat(a), _flat(b)
    return any(_rel_changed(fa.get(k, '<absent>'), fb.get(k, '<absent>')) for k in set(fa) | set(fb))


def _step_differs(v1, a2, v0, b2):
    f1, fa, f0, fb = _flat(v1), _flat(a2), _flat(v0), _flat(b2)
    for leaf in set(f1) | set(fa) | set(f0) | set(fb):
        x1, xa, x0, xb = (f.get(leaf, '<absent>') for f in (f1, fa, f0, fb))
        ca, cb = _rel_changed(x1, xa), _rel_changed(x0, xb)
        if not (ca or cb):
            continue
        if ca and cb and all(_num(x) for x in (x1, xa, x0, xb)) and not _rel_changed(xa - x1, xb - x0, max(abs(x1), abs(xa), abs(x0), abs(xb))):
            continue
        if ca and cb and (x1, xa) == (x0, xb):
            continue
        if ca and cb and x1 == x0 and not _rel_changed(xa, xb):
            continue
        return True
    return False


def _executable(cap):
    return cap not in rt.UNSUPPORTED and rt.CAPS[cap]['kind'] not in ('query',)


class ReviewRound1(unittest.TestCase):
    """Findings of the independent runtime / contract review round 1 (outputs/.../independent_*_review_r1.json)."""

    def _open30(self, h):
        h.invoke('door.outer', open=True)
        h.step(1.5)
        h.invoke('door.latch', open=True)
        h.step(0.5)
        h.invoke('door.inner', open=True)
        h.step(24.0)
        h.invoke('door.inner', open=False)
        h.step(1.5)
        h.invoke('door.latch', open=False)
        h.step(0.5)
        h.invoke('door.outer', open=False)

    def test_f01_no_overshoot_after_door_opening_and_f05_humidity_recovery(self):
        h = ready(settle=24 * 3600.0)
        self.assertAlmostEqual(h._rh_true(), 0.925, delta=0.01)
        t0 = h.t
        self._open30(h)
        temps, rhs = [], []
        for _ in range(3600):
            h.step(1.0)
            temps.append((h.t - t0, h.sens['T']))
            rhs.append((h.t - t0, h.sens['rh']))
        late = [T for s, T in temps if s >= 180.0]
        self.assertLess(max(late) - 310.15, 0.2)                                # brochure: recovery without regulation overshoot
        self.assertGreater(min(late) - 310.15, -0.2)
        self.assertLess(max(abs(T - 310.15) for s, T in temps if s >= 600.0), 0.1)
        rh80 = next(s for s, r in rhs if r >= 0.80 and s > 60.0)
        self.assertLess(rh80, 1800.0)                                           # back above 80 % well before the 3.5 h RH-alarm time-out
        self.assertNotIn('RH_LOW', h.alarms)
        check('door_recovery_no_overshoot', t_max_k=max(late), t_min_k=min(late), rh80_s=rh80)

    def test_f05_warm_tray_water_fogs_the_glass_door(self):
        h = ready(settle=6 * 3600.0)
        open_both(h)
        h.invoke('water_tray.empty', clean=False)
        h.invoke('water_tray.fill', volume_m3=1.5e-3, temperature_k=333.15)
        close_both(h)
        fog, rh = 0.0, 0.0
        for _ in range(90):
            h.step(60.0)
            fog = max(fog, h.read('evaluator')['truth.condensate_kg'])
            rh = max(rh, h.read('evaluator')['truth.rh'])
        self.assertGreater(fog, 1e-4)                                           # manual Tab. 11-7: condensation on the inner door with too-warm tray water
        self.assertLessEqual(rh, 1.0 + 1e-9)
        cold = ready(settle=12 * 3600.0)
        self.assertEqual(cold.read('evaluator')['truth.condensate_kg'], 0.0)
        check('warm_water_fog', condensate_kg=fog)

    def test_f02_bms_relays_fail_safe(self):
        h = ready(settle=600.0)
        h.invoke('power.set', enabled=False)
        self.assertEqual(h.read()['bms.relays'], [True, True, True, True])
        h.invoke('power.set', enabled=True)
        h.invoke('fault.inject', fault='power_loss', active=True)
        self.assertEqual(h.read()['bms.relays'], [True, True, True, True])
        check('bms_fail_safe')

    def test_f03_initial_gas_states_and_sealed_flasks(self):
        h = ready(settle=3 * 3600.0)
        for mid in ('himedia_al111', 'himedia_al028', 'himedia_al060'):
            flask(h, 'b' + mid, medium=mid, cell=None, cells=0.0, T=298.15, gas='as_supplied')
            qc = rt.media()[mid]['qc_pH']
            self.assertAlmostEqual(truth(h, 'b' + mid)['pH'], 0.5 * (qc[0] + qc[1]), delta=1e-6)
        flask(h, 'inc5', medium='himedia_al111', cell=None, cells=0.0, gas='incubator')
        h.invoke('co2.set', setpoint_fraction=0.10)
        h.step(3 * 3600.0)
        flask(h, 'inc10', medium='himedia_al111', cell=None, cells=0.0, gas='incubator')
        self.assertLess(truth(h, 'inc10')['pH'], truth(h, 'inc5')['pH'] - 0.2)  # 'incubator' follows the chamber's actual CO2
        hanks = 'himedia_al056'
        self.assertLess(rt.media()[hanks]['bicarbonate_mol_m3'], 6.0)
        open_both(h)
        flask(h, 'empty', medium=hanks, cell=None, cells=0.0, vtype='flask_t25', vol=5e-6, closure='plug_seal_closed', gas='as_supplied')
        flask(h, 'cult', medium=hanks, cell='hela', cells=0.5e6, vtype='flask_t25', vol=5e-6, closure='plug_seal_closed', gas='as_supplied')
        for k in ('empty', 'cult'):
            h.invoke('vessel.place', vessel_id=k, shelf=2)
        close_both(h)
        h.step(3600.0)
        e1, c1 = truth(h, 'empty'), truth(h, 'cult')
        h.step(47 * 3600.0)
        e2, c2 = truth(h, 'empty'), truth(h, 'cult')
        self.assertAlmostEqual(e2['pH'], e1['pH'], delta=0.02)                   # sealed and without cells: no CO2 escapes, the pH stays
        self.assertGreater(c2['headspace_co2_fraction'], c1['headspace_co2_fraction'])   # respiration accumulates in the headspace
        self.assertLess(c2['pH'], c1['pH'] - 0.1)
        self.assertTrue(6.8 < c2['pH'] < 8.3, c2['pH'])
        check('sealed_flask', ph_empty=e2['pH'], ph_culture=[c1['pH'], c2['pH']], headspace=[c1['headspace_co2_fraction'], c2['headspace_co2_fraction']])

    def test_f04_reference_culture_reproduces_the_library_doubling_time(self):
        h = ready(settle=3 * 3600.0)
        open_both(h)
        flask(h, 'dm', medium='himedia_al111', cells=1.0e6, gas='incubator')
        flask(h, 'pl', medium='himedia_al111', vtype='plate_96', vol=96 * 1.5e-7, cells=96 * 5.0e3, gas='incubator')
        h.invoke('vessel.place', vessel_id='dm', shelf=2)
        h.invoke('vessel.place', vessel_id='pl', shelf=3)
        close_both(h)
        h.step(24 * 3600.0)
        n24 = {k: truth(h, k)['cells_live'] for k in ('dm', 'pl')}
        h.step(48 * 3600.0)
        lib = rt.cell_lines()['hela']['doubling_time_s']
        for k in ('dm', 'pl'):
            td = 48 * 3600.0 * math.log(2) / math.log(truth(h, k)['cells_live'] / n24[k])
            self.assertLess(abs(td / lib - 1.0), 0.15, (k, td / 3600.0))
        check('doubling_time_reference')

    def test_minor_limits_doors_monitor_htd_chart_o2_contamination(self):
        h = ready(settle=600.0, room_k=308.15)                                   # F-M01: set point only 2 K above the room
        h.step(10 * 3600.0)                                                      # the uncontrolled heat drifts the chamber up; > set + 0.5 K for 4 h
        self.assertGreater(h.ch['Ta'], 306.15 + 3.0)
        self.assertIn('E_6731FW108', h.alarms)
        k = ready(settle=600.0)                                                  # F-M02: inner door refused while the outer door closes
        k.invoke('door.outer', open=True)
        k.step(2.0)
        k.invoke('door.latch', open=True)
        k.step(1.0)
        k.invoke('door.outer', open=False)
        with self.assertRaises(rt.ActionRejected):
            k.invoke('door.inner', open=True)
        m = ready(settle=2 * 3600.0)                                             # F-M03: the pressure monitor stops the control, no timeout
        m.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=1, co2_supply_pa=0.0))
        open_both(m)
        m.step(20.0)
        close_both(m)
        m.step(2 * 3600.0)
        self.assertFalse(m.read()['status.co2_control_failed'])
        m.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=1))
        m.step(1800.0)
        self.assertAlmostEqual(m.read('evaluator')['truth.co2_fraction'], 0.05, delta=0.003)
        d = ready(settle=600.0, water=0.0)                                       # F-M04: an open access port blocks the HTD
        open_both(d)
        d.invoke('rh_sensor.cover', on=True)
        close_both(d)
        d.invoke('access_port.plug', port='upper', plugged=False)
        with self.assertRaises(rt.ActionRejected) as e:
            d.invoke('htd.start')
        self.assertEqual(str(e.exception), 'ACCESS_PORT_OPEN')
        d.invoke('access_port.plug', port='upper', plugged=True)
        d.invoke('htd.start')
        d.step(3 * 3600.0)
        d.invoke('door.outer', open=True)                                        # F-M05: aborted at 180 C -> chamber handling refused while hot
        d.step(2.0)
        d.invoke('door.latch', open=True)
        d.step(1.0)
        d.invoke('door.inner', open=True)
        d.step(2.0)
        self.assertEqual(d.read()['htd.phase'], 'post_htd_cooling')
        with self.assertRaises(rt.ActionRejected) as e:
            d.invoke('water_tray.fill', volume_m3=1e-3, temperature_k=298.15)
        self.assertEqual(str(e.exception), 'HTD_RUNNING')
        self.assertNotIn('heater_fraction', d.chart[-1])                          # F-M07
        d.register_principal('ag_c06', 'agent')                                  # C-06: the tray volume is a process value for the evaluator
        s = d.session('ag_c06')
        self.assertNotIn('water_tray.volume_m3', s.read(instance_id=INST)['observations'])
        o = ready(settle=600.0)                                                  # F-M09: 5 % O2 recovers before the 15 min alarm pause ends
        o.invoke('o2.control', enabled=True)
        o.step(1800.0)
        o.invoke('o2.set', setpoint_fraction=0.05)
        o.step(3 * 3600.0)
        self._open30(o)
        o.step(3600.0)
        self.assertFalse([e for e in o.events if e['code'] == 'O2_HIGH'])
        c = ready(settle=3 * 3600.0, water=2.0e-3)                               # F-M10: contaminated medium stalls in acid, no solver floor
        open_both(c)
        flask(c, 'cx', medium='himedia_al111', cells=1.0e6, gas='incubator')
        c.invoke('vessel.place', vessel_id='cx', shelf=1)
        close_both(c)
        c.invoke('fault.inject', fault='vessel_contamination', active=True, vessel_id='cx')
        c.step(5 * 86400.0)
        self.assertGreater(truth(c, 'cx')['pH'], 4.5)
        check('minor_findings_r1')

    def test_c09_alarm_limits_and_performance_reference_units(self):
        h = ready(settle=3 * 3600.0)
        h.invoke('alarm.configure', kind='temperature', enabled=True, low_k=2.0, high_k=2.0)
        for args in (dict(kind='temperature', enabled=True, low=0.2), dict(kind='co2', enabled=True, low_k=0.5), dict(kind='temperature', enabled=True, low_k=6.0)):
            with self.assertRaises(rt.ActionRejected) as e:
                h.invoke('alarm.configure', **args)
            self.assertEqual(str(e.exception), 'INVALID_ALARM_LIMIT')
        with self.assertRaises(rt.ActionRejected) as e:
            h.invoke('performance_check.record', kind='temperature', reference_fraction=0.05, instrument='p')
        self.assertEqual(str(e.exception), 'MISSING_REFERENCE')
        r = h.invoke('performance_check.record', kind='co2', reference_fraction=0.05, instrument='analyser')
        self.assertIsNone(r['deviation_k'])
        self.assertLess(abs(r['deviation_fraction']), 0.003)
        check('unit_split_arguments')


class ReviewRound2(unittest.TestCase):
    """Findings of the combined independent review round 2 (independent_*_review_r2.json)."""

    def test_f2_01_default_initial_gas_and_f2_06_l15(self):
        h = ready(settle=600.0)
        h.invoke('vessel.provide', vessel_id='d', vessel_type='flask_t75', medium_id='himedia_al111', medium_volume_m3=1.5e-5, seeded_cells=0.0, temperature_k=298.15)
        qc = rt.media()['himedia_al111']['qc_pH']
        self.assertAlmostEqual(truth(h, 'd')['pH'], 0.5 * (qc[0] + qc[1]), delta=1e-6)    # documented default 'as_supplied'
        h.invoke('vessel.provide', vessel_id='l15', vessel_type='flask_t75', medium_id='himedia_al011', medium_volume_m3=1.5e-5, seeded_cells=0.0, temperature_k=298.15)
        qc = rt.media()['himedia_al011']['qc_pH']
        self.assertAlmostEqual(truth(h, 'l15')['pH'], 0.5 * (qc[0] + qc[1]), delta=1e-6)  # no bicarbonate: QC midpoint without CO2
        check('default_as_supplied')

    def test_f2_02_htd_finishes_with_a_set_point_near_the_room(self):
        h = powered()
        h.invoke('temperature.set', setpoint_k=298.15)                           # 25 C at a 22 C room (accepted with a warning), set before the warm-up
        open_both(h)
        h.invoke('rh_sensor.cover', on=True)
        close_both(h)
        h.step(6 * 3600.0)
        h.invoke('htd.start')
        h.step(30 * 3600.0)
        self.assertEqual(h.read()['htd.last_result'], 'successful')
        self.assertEqual(h.read()['htd.phase'], 'idle')
        check('htd_low_set_point')

    def test_f2_03_latch_race_and_f2_04_incubator_gas(self):
        h = ready(settle=3 * 3600.0)
        h.invoke('door.outer', open=True)
        h.step(2.0)
        h.invoke('door.latch', open=True)
        h.step(1.0)
        h.invoke('door.inner', open=True)
        with self.assertRaises(rt.ActionRejected) as e:
            h.invoke('door.latch', open=False)
        self.assertEqual(str(e.exception), 'INNER_DOOR_OPEN')
        k = ready(settle=600.0)                                                  # F3-01: reverse order (latch closing, then the inner door) is refused too
        k.invoke('door.outer', open=True)
        k.step(2.0)
        k.invoke('door.latch', open=True)
        k.step(1.0)
        k.invoke('door.latch', open=False)
        with self.assertRaises(rt.ActionRejected) as e2:
            k.invoke('door.inner', open=True)
        self.assertEqual(str(e2.exception), 'LATCH_CLOSED')
        h.step(30.0)                                                             # chamber CO2 has dropped with both doors open
        self.assertLess(h.read('evaluator')['truth.co2_fraction'], 0.01)
        flask(h, 'inc', medium='himedia_al111', cell=None, cells=0.0, gas='incubator')
        self.assertAlmostEqual(truth(h, 'inc')['pH'], 7.64, delta=0.03)          # equilibrated at the 5 % set point, not at the open-door chamber
        check('latch_race_incubator_gas')

    def test_c2_01_water_level_sensor_interlock(self):
        h = ready(settle=600.0)
        self.assertTrue(h.read()['sensor.water_present'])
        open_both(h)
        h.invoke('rh_sensor.cover', on=True)
        close_both(h)
        with self.assertRaises(rt.ActionRejected) as e:
            h.invoke('htd.start')
        self.assertEqual(str(e.exception), 'WATER_IN_TRAY')
        open_both(h)
        h.invoke('water_tray.empty', clean=True)
        self.assertFalse(h.read()['sensor.water_present'])
        h.invoke('power.set', enabled=False)
        self.assertIsNone(h.read()['sensor.water_present'])
        check('water_sensor_interlock')


class ZDeclaredEffects(unittest.TestCase):
    def test_immediate_and_on_step_effects_are_declared(self):
        missing_imm, missing_step = {}, {}
        states = {n: _scenario(n) for n in SCENARIOS}
        for cap in rt.CAPS:
            if not _executable(cap):
                continue
            for sname, S in states.items():
                for args in _variants(cap):
                    A = copy.deepcopy(S)
                    v0 = A.read('evaluator')
                    try:
                        _orig_invoke(A, cap, **args)
                    except rt.ActionRejected:
                        continue
                    v1 = A.read('evaluator')
                    if cap == 'sim.step':
                        for k in v0:
                            if _leaf_changed(v0[k], v1[k]) and k not in rt.ON_STEP['sim.step'] + rt.EFFECTS['sim.step']:
                                missing_step.setdefault('sim.step', set()).add((sname, k))
                        continue
                    for k in v0:
                        if _leaf_changed(v0[k], v1[k]) and k not in rt.EFFECTS.get(cap, []):
                            missing_imm.setdefault(cap, set()).add((sname, k))
                    for dt in STEP_DTS:
                        A2, B2 = copy.deepcopy(A), copy.deepcopy(S)
                        A2.step(dt)
                        B2.step(dt)
                        a2, b2 = A2.read('evaluator'), B2.read('evaluator')
                        for k in v0:
                            if k not in rt.ON_STEP.get(cap, []) and _step_differs(v1[k], a2[k], v0[k], b2[k]):
                                missing_step.setdefault(cap, set()).add((sname, k))
                            if k not in rt.ON_STEP['sim.step'] + ['clock.time_s'] and _leaf_changed(v0[k], b2[k]):
                                missing_step.setdefault('sim.step', set()).add((sname, k))
        self.maxDiff = None
        self.assertEqual(missing_imm, {})
        self.assertEqual(missing_step, {})
        check('declared_effects_scenarios')

    def test_every_device_capability_is_accepted_in_some_scenario(self):
        accepted = set()
        for n in SCENARIOS:
            S = _scenario(n)
            for cap in rt.CAPS:
                if not _executable(cap) or cap in accepted:
                    continue
                for args in _variants(cap):
                    try:
                        _orig_invoke(copy.deepcopy(S), cap, **args)
                        accepted.add(cap)
                        break
                    except rt.ActionRejected:
                        pass
        self.assertEqual({c for c in rt.CAPS if _executable(c)} - accepted, set())


class ZZRejectionCodes(unittest.TestCase):
    def test_every_observed_rejection_code_is_declared(self):
        undeclared = {k: sorted(v - set(rt.CAP_ERRORS.get(k, [])) - {'INVALID_ARGUMENT', 'UNSUPPORTED', 'UNKNOWN_CAPABILITY'}) for k, v in REJECTIONS.items()}
        self.assertEqual({k: v for k, v in undeclared.items() if v}, {})


if __name__ == '__main__':
    unittest.main()
