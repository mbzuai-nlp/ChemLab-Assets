"""tests for CHEM-152 jeol_jem1400flash: python3 -m unittest tests/test_asset_jeol_jem1400flash.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two
instances are isolated, agent/evaluator truth isolation, conservation (electron charge, gas), real return values validated against the
declared schemas (asset_tool lint_schema / validate_value), asynchronous completion and cancellation remnants, host-step independence,
declared immediate and on_step effects covering what the process changes; plus the instrument behaviour documented by JEOL and the facility
procedures (start-up and vacuum, HT 10-120 kV with the dark current of 44 uA at 80 kV and 66 uA at 120 kV, beam current 5-7 uA above the dark
current at bias 40, specimen exchange through the airlock with stage neutral and filament off, Evac Ready, holder tilt limits, LOW MAG /
MAG / DIFF steps, camera 2048 x 2048 with 21.7 um pixels, lattice resolution 0.14 nm) and the electron-scattering physics (mass-thickness and
diffraction contrast, extinction distances, ring and spot patterns, Thon rings, beam damage).
"""
import copy
import json
import math
import random
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/chemistry-lab-assets/scripts"))
from asset_runtime import jeol_jem1400flash as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE = [], {}
_META = {}


def _meta():
    if not _META:
        _META.update(rt.export_jeol_jem1400flash_contract_metadata())
    return _META


_orig_invoke = rt.JeolJem1400flashRuntime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema and immediate state effects (read does not advance time)."""
    before = self.read('evaluator') if capability_id != 'sim.step' else None
    t0 = self.time_ns
    out = _orig_invoke(self, capability_id, **a)
    meta = _meta()['capabilities'][capability_id]
    errs = asset_tool.validate_value(out, meta['output_schema'])
    if errs:
        raise AssertionError(f'{capability_id} output violates its schema: {errs[:3]}')
    if before is not None:
        assert self.time_ns == t0, capability_id
        after = self.read('evaluator')
        undeclared = {k for k in after if after[k] != before[k]} - set(rt.CAPS[capability_id]['effects'])
        if undeclared:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.JeolJem1400flashRuntime.invoke = _instrumented_invoke
AU = rt.SPECIMEN_EXAMPLE


class QuietRng:
    """Noise-free stand-in for random.Random (gauges exact, fixed draws) so that two runs differ only by the process."""
    def gauss(self, mu, sigma):
        return mu

    def random(self):
        return 0.5

    def uniform(self, a, b):
        return 0.5 * (a + b)

    def getrandbits(self, k):
        return 12345


def grid(sid, phases, support='amorphous_carbon', t_support=1e-8, holes=0.0, mesh='200'):
    return dict(specimen_id=sid, grid_mesh=mesh, grid_material='copper', support_film=support, support_thickness_m=t_support if support != 'none' else 0.0,
                hole_fraction=holes, phases=phases)


def phase(sub, form='particles', size=2e-8, t=2e-8, cov=0.15, cryst='single_crystal', zone=None, stain=0.0):
    return dict(substance_id=sub, form=form, size_m=size, thickness_m=t, coverage=cov, crystallinity=cryst, zone_axis=zone, stain_mass_fraction=stain)


def configured(seed=1, **plant):
    h = rt.JeolJem1400flashRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def ready(seed=1, ht=True, **plant):
    """Powered, TEM Center up, column and gun at the ready pressure, HT at 120 kV (filament off)."""
    h = configured(seed, **plant)
    h.invoke('power.set', enabled=True)
    h.step(260.0)
    if ht:
        h.invoke('ht.set_on', on=True)
        h.step(125.0)
    return h


def inserted(specimen=AU, holder='quick_change', seed=1, beam=True, **plant):
    """A grid in the column (quick-change holder), filament saturated, condenser aperture in, beam spread over about 12 um, screen down."""
    h = ready(seed, **plant)
    if specimen is not None:
        h.invoke('specimen.provide', **specimen)
        h.invoke('holder.load_grid', holder_id=holder, specimen_id=specimen['specimen_id'])
    h.invoke('holder.insert_to_airlock', holder_id=holder)
    h.step(12.0)
    h.invoke('airlock.set_pump', pump=True)
    h.step(60.0)
    h.invoke('holder.insert')
    h.step(10.0)
    if specimen is not None:                                        # view an open grid square (the random mount may put the origin on a bar)
        x, y = open_square(h)
        h.invoke('stage.move_xy', x_m=x, y_m=y)
        h.step(3.0)
    if beam:
        h.invoke('filament.set_on', on=True)
        h.step(25.0)
        h.invoke('aperture.set_condenser', inserted=True)
        h.invoke('optics.set_brightness', value=0.53)
        h.step(1.0)
    return h


def open_square(h, near=(0.0, 0.0)):
    """Stage position of the centre of the grid square nearest to `near` (the host knows how the grid was mounted; an operator finds it at LOW MAG)."""
    spec = h._spec()
    mesh = rt.GRID_MESH[int(spec['grid_mesh'])]
    p, w = mesh['pitch'], mesh['bar']
    phi, oa, ob = spec['_mount']['phi'], spec['_mount']['off_a'], spec['_mount']['off_b']
    a0 = math.cos(phi) * near[0] + math.sin(phi) * near[1] + oa
    b0 = -math.sin(phi) * near[0] + math.cos(phi) * near[1] + ob
    ac = round((a0 - (p + w) / 2) / p) * p + (p + w) / 2 - oa
    bc = round((b0 - (p + w) / 2) / p) * p + (p + w) / 2 - ob
    return math.cos(phi) * ac - math.sin(phi) * bc, math.sin(phi) * ac + math.cos(phi) * bc


def camera(h, mag=30000.0, ol=2):
    h.invoke('aperture.set_objective', position=ol)
    h.invoke('optics.set_magnification', magnification=mag)
    h.invoke('screen.set', up=True)
    h.step(1.0)
    return h


def acquire_image(h, exposure=1.0, binning=1):
    op = h.invoke('image.acquire', exposure_s=exposure, binning=binning)['operation_id']
    h.step(h.operations[op]['duration'] + 1.0)
    return h.images[h.operations[op]['record_id']]


def acquire_pattern(h, exposure=0.5, binning=1):
    op = h.invoke('diffraction.acquire', exposure_s=exposure, binning=binning)['operation_id']
    h.step(h.operations[op]['duration'] + 1.0)
    return h.patterns[h.operations[op]['record_id']]


def rejected(h, cap, code, **a):
    before = h.read('evaluator')
    rev = h.revision
    try:
        h.invoke(cap, **a)
    except rt.ActionRejected as exc:
        assert str(exc) == code, (cap, str(exc), code)
        assert h.read('evaluator') == before, cap
        assert h.revision == rev, cap
        return True
    raise AssertionError(f'{cap} was not rejected (expected {code})')


def transmission(sid, t, v=120000.0, alpha=15e-3):
    """Bright-field transmission of an amorphous layer (Lenz-Wentzel)."""
    return math.exp(-rt.mu_el_outside(sid, v, alpha) * t)


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.jeol_jem1400flash')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-152')

    def test_schemas_lint_and_examples_validate(self):
        meta = _meta()
        for k, c in meta['capabilities'].items():
            self.assertEqual(asset_tool.lint_schema(c['input_schema']), [], k)
            self.assertEqual(asset_tool.lint_schema(c['output_schema']), [], k)
            if c['availability'] == 'implemented':
                self.assertEqual(asset_tool.validate_value(c['example_arguments'], c['input_schema']), [], k)
        for k, s in meta['state_slots'].items():
            self.assertEqual(asset_tool.lint_schema(s['value_schema']), [], k)

    def test_metadata_references_are_consistent(self):
        meta = _meta()
        slots = set(meta['state_slots'])
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            for x in c['observes']:
                self.assertIn(x, slots, k)
            for p in c['preconditions']:
                for x in p['state_ids']:
                    self.assertIn(x, slots, k)
            for e in c['errors']:
                self.assertIn(e['code'], rt.ERROR_ZH, k)
        self.assertEqual(set(rt.EFFECTS) | set(rt.UNSUPPORTED) | {'image.result', 'diffraction.result', 'montage.result', 'records.list', 'events.read',
                                                                  'ledger.read', 'provenance.read', 'camera.auto_exposure'}, set(rt.CAPS))
        self.assertEqual(set(rt.CAP_SOURCES), set(rt.CAPS))
        for mech in meta['mechanisms'].values():
            for x in mech['component_ids']:
                self.assertIsInstance(x, str)

    def test_every_rejection_code_is_declared(self):
        """Each ActionRejected code raised by a command method appears in that capability's declared errors."""
        import inspect
        import re
        src = inspect.getsource(rt.JeolJem1400flashRuntime)
        meta = _meta()['capabilities']
        helpers = {'_need_plant': ['PLANT_REQUIRED'], '_need_power': ['PLANT_REQUIRED', 'POWER_OFF'],
                   '_need_ready': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING'], '_need_idle': ['ACQUISITION_RUNNING'],
                   '_need_holder_still': ['HOLDER_MOVING'], '_need_camera_view': ['BEAM_NOT_READY', 'SCREEN_DOWN'],
                   '_need_stage': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING', 'HOLDER_MOVING', 'HOLDER_NOT_IN_COLUMN'],
                   '_need_acquire': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING', 'HOLDER_MOVING', 'BEAM_NOT_READY',
                                     'SCREEN_DOWN', 'STAGE_MOVING', 'RECORDS_FULL']}
        for k in rt.CAPS:
            if k in rt.UNSUPPORTED:
                continue
            m = re.search(r'    def _cmd_' + k.replace('.', '_') + r'\(.*?(?=\n    def |\Z)', src, re.S)
            self.assertIsNotNone(m, k)
            body = m.group(0)
            codes = set(re.findall(r"ActionRejected\('([A-Z_]+)'\)", body))
            for hname, hcodes in helpers.items():
                if hname + '(' in body:
                    codes |= set(hcodes)
            if '_cmd_power_set(' in body:
                codes |= {'PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'}
            if '_cmd_screen_set(' in body:
                codes |= {'PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING'}
            declared = {e['code'] for e in meta[k]['errors']}
            self.assertLessEqual(codes, declared, (k, sorted(codes - declared)))

    def test_unsupported_capabilities_report_unsupported(self):
        h = configured()
        h.register_principal('a', 'agent')
        s = h.session('a')
        for k in rt.UNSUPPORTED:
            r = s.invoke(instance_id=h.instance_id, capability_id=k, arguments={}, request_id='u-' + k)
            self.assertEqual(r['status'], 'unsupported', k)
            self.assertEqual(r['error']['code'], 'UNSUPPORTED')


class HostClockAndPurity(unittest.TestCase):
    def test_read_and_describe_do_not_advance_time(self):
        h = inserted()
        h.register_principal('a', 'agent')
        t, rev = h.time_ns, h.revision
        h.read('evaluator')
        s = h.session('a')
        s.read(instance_id=h.instance_id)
        s.describe(instance_id=h.instance_id)
        self.assertEqual((h.time_ns, h.revision), (t, rev))

    def test_step_zero_and_negative(self):
        h = configured()
        rev = h.revision
        h.step(0.0)
        self.assertEqual(h.revision, rev)
        with self.assertRaises(rt.ActionRejected):
            h.step(-1.0)

    def test_host_step_chopping_does_not_change_the_result(self):
        def run(chunks):
            h = configured(seed=5)
            h.rng = QuietRng()
            h.invoke('specimen.provide', **AU)
            h.invoke('power.set', enabled=True)
            h.invoke('holder.load_grid', holder_id='quick_change', specimen_id=AU['specimen_id'])
            h.invoke('holder.insert_to_airlock', holder_id='quick_change')
            h.invoke('airlock.set_pump', pump=True)
            for dt in chunks:
                h.step(dt)
            return h.read('evaluator'), h.ledger()
        (a, la), (b, lb) = run([300.0]), run([0.37] * 810 + [0.3])
        for k in ('truth.column_pressure_pa', 'truth.gun_pressure_pa', 'truth.airlock_pressure_pa', 'vacuum.state', 'airlock.state', 'holder.position',
                  'holder.withdrawal_m', 'truth.tmp_speed_fraction', 'system.state'):
            if isinstance(a[k], float):
                self.assertAlmostEqual(a[k], b[k], delta=1e-9 * max(abs(a[k]), 1e-12), msg=k)
            else:
                self.assertEqual(a[k], b[k], k)
        self.assertAlmostEqual(la['gas_pumped_mol'], lb['gas_pumped_mol'], delta=1e-12 * la['gas_pumped_mol'])

    def test_beam_dose_does_not_depend_on_the_host_step(self):
        def run(chunks):
            h = inserted(seed=3)
            h.rng = QuietRng()
            h.invoke('stage.move_xy', x_m=2e-5, y_m=-1e-5)
            for dt in chunks:
                h.step(dt)
            return h.read('evaluator')
        a, b = run([20.0]), run([0.25] * 80)
        for k in ('stage.x_m', 'stage.y_m', 'truth.view_center_dose_c_m2', 'truth.filament_on_time_s'):
            self.assertAlmostEqual(a[k], b[k], delta=1e-9 * max(abs(a[k]), 1e-15), msg=k)

    def test_set_points_only_write_targets(self):
        h = inserted()
        x0, y0 = h.stage['x'], h.stage['y']
        h.invoke('stage.move_xy', x_m=x0 + 1e-4, y_m=y0)
        self.assertEqual(h.stage['x'], x0)
        self.assertEqual(h.stage_target['x'], x0 + 1e-4)
        h.step(1.0)
        self.assertAlmostEqual(h.stage['x'], x0 + rt.STAGE_XY_SPEED * 1.0, places=15)
        h.invoke('ht.set_voltage', voltage_v=80000.0)
        self.assertEqual(h.kv_act, 120000.0)
        h.step(10.0)
        self.assertAlmostEqual(h.kv_act, 110000.0, places=6)
        h.invoke('screen.set', up=True)
        self.assertEqual(h.screen_angle, 0.0)
        h.step(0.25)
        self.assertAlmostEqual(h.screen_angle, rt.SCREEN_UP_ANGLE_RAD / 2.0, places=12)


class StartUpVacuumAndGun(unittest.TestCase):
    def test_start_up_times(self):
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(rt.SOFTWARE_BOOT_S - 1.0)
        self.assertEqual(h.state, 'booting')
        rejected(h, 'ht.set_on', 'NOT_READY', on=True)
        h.step(2.0)
        self.assertEqual(h.state, 'ready')
        self.assertEqual(h.vac_state, 'pumping')                   # the turbo pump is still spinning up
        rejected(h, 'ht.set_on', 'VACUUM_NOT_READY', on=True)
        h.step(150.0)
        self.assertEqual(h.vac_state, 'ready')
        self.assertLess(h.p_col, rt.COL_READY_PA)
        self.assertLess(h.p_gun, rt.GUN_READY_PA)
        self.assertAlmostEqual(h.p_col, rt.Q_COLUMN_PA_M3_S / rt.S_TMP_M3_S, delta=0.05 * rt.Q_COLUMN_PA_M3_S / rt.S_TMP_M3_S)

    def test_high_tension_ramp_and_dark_current(self):
        h = ready(ht=False)
        h.invoke('ht.set_voltage', voltage_v=80000.0)
        h.invoke('ht.set_on', on=True)
        h.step(40.0)
        self.assertAlmostEqual(h.kv_act, 40000.0, places=6)          # 1 kV/s
        h.step(45.0)
        r = h.read()
        self.assertEqual(r['ht.voltage_indicated_v'], 80000.0)
        self.assertAlmostEqual(r['gun.dark_current_indicated_a'], 44e-6, delta=2e-6)     # facility: 44 uA +- 2 uA at 80 kV
        h.invoke('ht.set_voltage', voltage_v=120000.0)
        h.step(45.0)
        self.assertAlmostEqual(h.read()['gun.dark_current_indicated_a'], 66e-6, delta=2e-6)   # 66 uA +- 2 uA at 120 kV
        self.assertEqual(h.read()['ht.voltage_indicated_v'], 120000.0)                      # the monitor shows it as 120 kV
        self.assertAlmostEqual(rt.quantize_kv(80020.0) % rt.KV_STEP_V, 0.0, delta=1e-6)
        self.assertEqual(rt.quantize_kv(5000.0), 10000.0)

    def test_filament_bias_and_beam_current(self):
        h = ready()
        h.invoke('filament.set_on', on=True)
        h.step(10.0)
        self.assertEqual(h.read()['filament.state'], 'heating')
        h.step(12.0)
        r = h.read()
        self.assertEqual(r['filament.state'], 'saturated')
        above = r['gun.beam_current_indicated_a'] - r['gun.dark_current_indicated_a']
        self.assertTrue(5e-6 <= above <= 7e-6, above)                 # facility: 5-7 uA above the dark current at bias 40
        self.assertTrue(r['panel.beam_lamp'])
        diffs = []
        for _ in range(300):                                           # the meter fluctuates, but beam - dark stays inside the SOP window every cycle
            h.step(1.0)
            r = h.read()
            diffs.append(r['gun.beam_current_indicated_a'] - r['gun.dark_current_indicated_a'])
        self.assertTrue(all(5e-6 <= d <= 7e-6 for d in diffs), (min(diffs), max(diffs)))
        self.assertLess(np.std(diffs), 0.2e-6)
        h.invoke('filament.set_bias', bias=20.0)
        h.step(1.0)
        self.assertGreater(h.read()['gun.beam_current_indicated_a'] - h.read()['gun.dark_current_indicated_a'], 9e-6)

    def test_ht_needs_cooling_water_and_trips_on_gun_vacuum(self):
        h = ready(ht=False, cooling_water_m3_s=2.0 / 60000.0)
        rejected(h, 'ht.set_on', 'COOLING_WATER_LOW', on=True)
        h = inserted()
        camera(h)
        op = h.invoke('image.acquire', exposure_s=5.0, binning=1)['operation_id']
        h.step(1.0)
        h.p_gun = 1e-3                                              # fault injection: a gun leak (no public command reaches the trip)
        h.step(1.0)
        self.assertEqual(h.error, 'HT_TRIP')
        self.assertFalse(h.ht_on)
        self.assertEqual(h.images[h.operations[op]['record_id']]['status'], 'failed')
        rejected(h, 'ht.set_on', 'ERROR_PENDING', on=True)
        h.invoke('error.acknowledge')
        h.step(60.0)
        h.invoke('ht.set_on', on=True)

    def test_power_cycle_and_mains(self):
        h = configured(mains_voltage_v=170.0)
        rejected(h, 'power.set', 'MAINS_OUT_OF_RANGE', enabled=True)
        rejected(h, 'power.key_toggle', 'MAINS_OUT_OF_RANGE')
        h = configured()
        self.assertTrue(h.invoke('power.key_toggle')['enabled'])
        self.assertTrue(h.read()['panel.power_lamp'])
        self.assertFalse(h.invoke('power.key_toggle')['enabled'])
        self.assertEqual((h.state, h.vac_state), ('off', 'idle'))
        h = inserted()
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=250.0))
        h.step(1.0)
        self.assertFalse(h.power)
        self.assertEqual(h.error, 'MAINS_FAULT')
        self.assertEqual(h.vac_state, 'idle')
        p = h.p_col
        h.step(600.0)
        self.assertGreater(h.p_col, p)                              # valves closed: the column leaks slowly
        self.assertLess(h.p_col, 0.1)

    def test_burnout_and_replacement(self):
        h = ready()
        h.invoke('filament.set_on', on=True)
        h.step(25.0)
        h.fil_used_s = h.fil_life_s - 0.5
        h.step(1.0)
        self.assertEqual((h.fil_burned, h.error), (True, 'FILAMENT_BURNOUT'))
        rejected(h, 'gun.replace_filament', 'HT_ON')
        h.invoke('ht.set_on', on=False)
        n = h.invoke('gun.replace_filament')['filament_serial']
        self.assertEqual((n, h.fil_burned, h.fil_used_s, h.error), (2, False, 0.0, None))

    def test_filament_life_is_weibull_around_150_hours(self):
        lives = []
        for seed in range(400):
            h = rt.JeolJem1400flashRuntime()
            h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed))
            lives.append(h.fil_life_s)
        self.assertAlmostEqual(np.mean(lives) / 3600.0, rt.FILAMENT_LIFE_H, delta=0.08 * rt.FILAMENT_LIFE_H)


class SpecimenExchange(unittest.TestCase):
    def test_airlock_evacuation_insertion_and_removal(self):
        h = ready()
        h.invoke('specimen.provide', **AU)
        h.invoke('holder.load_grid', holder_id='quick_change', specimen_id=AU['specimen_id'])
        h.invoke('holder.insert_to_airlock', holder_id='quick_change')
        self.assertEqual(h.holder_pos, 'entering')
        rejected(h, 'holder.insert', 'HOLDER_NOT_AT_AIRLOCK')
        h.step(11.0)
        self.assertEqual((h.holder_pos, h.read()['airlock.state']), ('airlock', 'vented'))
        rejected(h, 'holder.insert', 'AIRLOCK_NOT_EVACUATED')
        h.invoke('airlock.set_pump', pump=True)
        h.step(30.0)
        self.assertEqual(h.read()['airlock.state'], 'pumping')
        self.assertFalse(h.read()['panel.airlock_lamp'])
        h.step(15.0)
        self.assertEqual(h.read()['airlock.state'], 'evacuated')       # about 40 s to 5 Pa
        self.assertTrue(h.read()['panel.airlock_lamp'])
        p0 = h.p_col
        h.invoke('holder.insert')
        self.assertGreater(h.p_col, p0)                               # the airlock gas joins the column
        h.step(3.0)
        self.assertEqual(h.holder_pos, 'column')
        self.assertTrue(h.ht_on)                                      # the short pressure rise does not trip the gun
        h.step(20.0)
        self.assertEqual(h.vac_state, 'ready')
        h.invoke('filament.set_on', on=True)
        h.step(25.0)
        rejected(h, 'holder.withdraw', 'FILAMENT_ON')
        h.invoke('filament.set_on', on=False)
        h.invoke('stage.move_xy', x_m=1e-5, y_m=0.0)
        h.step(1.0)
        rejected(h, 'holder.withdraw', 'STAGE_NOT_NEUTRAL')
        h.invoke('stage.neutral')
        h.step(1.0)
        h.invoke('holder.withdraw')
        h.step(2.0)
        self.assertEqual(h.holder_pos, 'airlock')
        rejected(h, 'holder.remove', 'AIRLOCK_NOT_VENTED')
        h.invoke('airlock.set_pump', pump=False)
        h.step(15.0)
        self.assertEqual(h.read()['airlock.state'], 'vented')
        h.invoke('holder.remove')
        h.step(11.0)
        self.assertEqual((h.holder_pos, h.holder_in), ('bench', None))
        self.assertEqual(h.invoke('holder.unload_grid', holder_id='quick_change')['specimen_id'], AU['specimen_id'])
        self.assertIn(AU['specimen_id'], h.read()['specimen.bench_ids'])
        led = h.ledger()
        self.assertEqual(led['airlock_cycles'], 1.0)
        self.assertLess(abs(led['gas_residual_mol']), 1e-12 + 1e-9 * led['gas_admitted_mol'])

    def test_exchange_preconditions(self):
        h = ready()
        h.invoke('specimen.provide', **AU)
        rejected(h, 'holder.unload_grid', 'HOLDER_EMPTY', holder_id='quick_change')
        rejected(h, 'holder.load_grid', 'UNKNOWN_SPECIMEN', holder_id='quick_change', specimen_id='nope')
        h.invoke('holder.load_grid', holder_id='quick_change', specimen_id=AU['specimen_id'])
        rejected(h, 'holder.load_grid', 'SPECIMEN_NOT_ON_BENCH', holder_id='high_tilt', specimen_id=AU['specimen_id'])
        h.invoke('filament.set_on', on=True)
        rejected(h, 'holder.insert_to_airlock', 'FILAMENT_ON', holder_id='quick_change')
        h.invoke('filament.set_on', on=False)
        h.invoke('holder.insert_to_airlock', holder_id='quick_change')
        rejected(h, 'holder.insert_to_airlock', 'GONIOMETER_OCCUPIED', holder_id='high_tilt')
        rejected(h, 'filament.set_on', 'HOLDER_MOVING', on=True)
        rejected(h, 'stage.neutral', 'HOLDER_MOVING')
        rejected(h, 'holder.load_grid', 'HOLDER_IN_GONIOMETER', holder_id='quick_change', specimen_id=AU['specimen_id'])

    def test_tilt_limits_of_the_holders(self):
        h = inserted()
        rejected(h, 'stage.set_tilt', 'TILT_OUT_OF_RANGE', tilt_rad=math.radians(25.0))
        h.invoke('stage.set_tilt', tilt_rad=math.radians(-20.0))
        h.step(12.0)
        self.assertAlmostEqual(h.read()['stage.tilt_indicated_rad'], math.radians(-20.0), places=9)
        h2 = inserted(holder='high_tilt')
        h2.invoke('stage.set_tilt', tilt_rad=math.radians(70.0))
        h2.step(40.0)
        self.assertAlmostEqual(math.degrees(h2.stage['tilt']), 70.0, places=9)
        with self.assertRaises(rt.ActionRejected):                          # outside the input schema (+-70 deg)
            h2.invoke('stage.set_tilt', tilt_rad=math.radians(71.0))

    def test_specimen_validation(self):
        h = configured()
        rejected(h, 'specimen.provide', 'SUPPORT_REQUIRED', **grid('a', [phase('gold')], support='none'))
        rejected(h, 'specimen.provide', 'SUPPORT_INVALID', **dict(grid('b', []), support_thickness_m=0.0))
        rejected(h, 'specimen.provide', 'UNKNOWN_SUBSTANCE', **grid('c', [phase('unobtainium')]))
        rejected(h, 'specimen.provide', 'CRYSTALLINITY_INVALID', **grid('d', [phase('polystyrene', cryst='single_crystal')]))
        rejected(h, 'specimen.provide', 'ZONE_AXIS_INVALID', **grid('e', [phase('silicon', form='film', size=1e-6, cov=1.0, cryst='polycrystalline', zone=[0, 0, 1])]))
        rejected(h, 'specimen.provide', 'STAIN_INVALID', **grid('f', [phase('gold', stain=0.1)]))
        rejected(h, 'specimen.provide', 'COVERAGE_TOO_HIGH', **grid('g', [phase('gold', cov=0.5)]))
        rejected(h, 'specimen.provide', 'COVERAGE_INVALID', **grid('h', [phase('gold', cov=0.0)]))
        h.invoke('specimen.provide', **AU)
        rejected(h, 'specimen.provide', 'SPECIMEN_EXISTS', **AU)


class OpticsAndImaging(unittest.TestCase):
    def test_modes_and_magnification_steps(self):
        h = ready()
        h.invoke('optics.set_mode', mode='LOW_MAG')
        self.assertEqual(h.invoke('optics.set_magnification', magnification=5000.0)['magnification'], 1000.0)   # LOW MAG up to x1,000
        self.assertEqual(h.invoke('optics.set_magnification', magnification=10.0)['magnification'], 10.0)
        rejected(h, 'optics.set_mode', 'NOT_IN_MAG_MODE', mode='DIFF')
        rejected(h, 'optics.toggle_diffraction', 'NOT_IN_MAG_MODE')
        h.invoke('optics.set_mode', mode='MAG')
        self.assertEqual(h.invoke('optics.set_magnification', magnification=1.5e6)['magnification'], 1.5e6)   # HR x1,500,000
        self.assertEqual(h.invoke('optics.set_magnification', magnification=50.0)['magnification'], 200.0)                       # MAG from x200
        h.invoke('optics.toggle_diffraction')
        self.assertEqual(h.mode, 'DIFF')
        rejected(h, 'optics.set_magnification', 'DIFF_MODE', magnification=1000.0)
        self.assertEqual(h.invoke('optics.set_camera_length', camera_length_m=0.33)['camera_length_m'], 0.30)
        self.assertEqual(h.read()['display.mag_or_camera_length_text'], '300')                       # mm on the display
        out = h.invoke('optics.set_magnification_step_fraction', fraction=1.0)
        self.assertEqual((out['magnification'], out['camera_length_m']), (None, rt.CAMERA_LENGTHS_M[-1]))
        self.assertEqual(h.read()['optics.camera_length_m'], rt.CAMERA_LENGTHS_M[-1])
        self.assertEqual(h.read()['display.mag_or_camera_length_text'], '3000')
        h.invoke('optics.toggle_diffraction')
        self.assertEqual(h.mode, 'MAG')

    def test_magnification_knob_keeps_the_hand_position(self):
        h = ready()
        n = len(rt.MAG_STEPS) - 1
        frac = (rt.MAG_STEPS.index(30000) + 0.3) / n                           # a hand turn between two detents
        out = h.invoke('optics.set_magnification_step_fraction', fraction=frac)
        self.assertEqual((out['fraction'], out['magnification']), (frac, 30000.0))
        self.assertEqual(h.read()['optics.magnification_step_fraction'], frac)   # the knob stays where it was left
        h.invoke('optics.set_magnification', magnification=5000.0)             # a selected step puts the knob on its detent
        self.assertEqual(h.read()['optics.magnification_step_fraction'], rt.MAG_STEPS.index(5000) / n)

    def test_illumination_current_and_screen_reading(self):
        h = inserted()
        I1 = h._specimen_current()
        h.invoke('optics.set_spot_size', spot=3)
        self.assertAlmostEqual(h._specimen_current() / I1, 0.25, places=12)
        h.invoke('aperture.set_condenser', inserted=False)
        self.assertAlmostEqual(h._specimen_current() / I1, 0.75, places=12)
        h.invoke('optics.set_spot_size', spot=1)
        h.invoke('aperture.set_condenser', inserted=True)
        h.invoke('optics.set_brightness', value=0.5)
        d0 = h._illum_d()
        self.assertAlmostEqual(d0, rt.ILLUM_D_MIN_M, places=15)
        h.invoke('optics.set_brightness', value=1.0)
        self.assertAlmostEqual(h._illum_d(), rt.ILLUM_D_MAX_M, places=15)
        h.invoke('optics.set_brightness', value=0.53)
        h.step(2.0)
        r = h.read()
        j1 = r['illumination.screen_current_density_a_m2']
        self.assertIsNotNone(j1)
        h.invoke('optics.set_magnification', magnification=60000.0)
        h.step(1.0)
        self.assertAlmostEqual(h.read()['illumination.screen_current_density_a_m2'] / j1, (30000.0 / 60000.0) ** 2, delta=0.12)   # 1/M^2 (3 % noise)
        self.assertTrue(h.invoke('screen.toggle')['up'])                          # F1
        h.step(0.2)
        self.assertTrue(h.read()['screen.moving'])
        rejected(h, 'image.acquire', 'SCREEN_DOWN', exposure_s=0.1, binning=1)      # still turning
        h.step(0.8)
        self.assertIsNone(h.read()['illumination.screen_current_density_a_m2'])

    def test_mass_thickness_contrast(self):
        """Amorphous transmission follows exp(-t / x_k): thicker, heavier, smaller aperture and lower voltage all darken."""
        c10, c20 = transmission('amorphous_carbon', 10e-9), transmission('amorphous_carbon', 20e-9)
        self.assertAlmostEqual(c20, c10 ** 2, places=12)
        self.assertTrue(0.95 < c10 < 0.99, c10)                          # a 10 nm carbon film is nearly transparent at 120 kV
        self.assertLess(transmission('gold', 10e-9), 0.5)
        self.assertLess(transmission('amorphous_carbon', 10e-9, alpha=5e-3), transmission('amorphous_carbon', 10e-9, alpha=30e-3))
        self.assertLess(transmission('amorphous_carbon', 50e-9, v=60000.0), transmission('amorphous_carbon', 50e-9, v=120000.0))
        # a stained section is darker than an unstained one of the same thickness
        h = inserted(specimen=grid('sec', [phase('epoxy_resin', form='section', size=1e-6, t=7e-8, cov=1.0, cryst='amorphous', stain=0.15)], support='formvar',
                                   t_support=2e-8, mesh='300'))
        h2 = inserted(specimen=grid('sec', [phase('epoxy_resin', form='section', size=1e-6, t=7e-8, cov=1.0, cryst='amorphous')], support='formvar',
                                    t_support=2e-8, mesh='300'))
        f1, f2 = h._view_fractions(), h2._view_fractions()
        self.assertLess(f1['T_view'], f2['T_view'])

    def test_diffraction_contrast_and_extinction_distances(self):
        """Two-beam extinction distances from the library structure factors agree with the tabulated values (100 kV)."""
        lit = {('gold', (1, 1, 1)): 18.3e-9, ('aluminium', (1, 1, 1)): 55.6e-9, ('silicon', (2, 2, 0)): 75.7e-9}
        for (sid, hkl), ref in lit.items():
            cr = rt.substance(sid)['crystal']
            r = [x for x in cr['_refl'] if tuple(x['hkl']) == hkl][0]
            self.assertAlmostEqual(rt.extinction_distance(cr, r, 100000.0) / ref, 1.0, delta=0.08, msg=sid)
        # a crystal in the Bragg condition diffracts strongly out of a small aperture: pendellosung at t = xi / 2
        xi = 20e-9
        self.assertAlmostEqual(rt.two_beam_intensity(xi / 2.0, 0.0, xi), 1.0, places=12)
        self.assertLess(rt.two_beam_intensity(xi / 2.0, 0.2e9, xi), 0.05)

    def test_image_counts_regions_and_noise(self):
        h = camera(inserted(), mag=30000.0)
        img = acquire_image(h, exposure=1.0)
        self.assertEqual(img['status'], 'completed')
        self.assertEqual(len(img['thumbnail']), rt.THUMB)
        self.assertAlmostEqual(img['field_of_view_m'], 2048 * 21.7e-6 / 30000.0, places=15)
        self.assertAlmostEqual(img['pixel_size_m'], 21.7e-6 / 30000.0, places=15)
        self.assertGreaterEqual(len(img['regions']), 2)
        self.assertFalse(any('gold' in json.dumps(r) or 'carbon' in json.dumps(r) for r in img['regions']))
        self.assertEqual(img['template_source']['library'], rt.LIBRARY_REL)
        bright = img['regions'][0]
        dose = h._current_density() * 1.0 / rt.E_CHARGE                        # incident dose (evaluator truth)
        expected = dose * (21.7e-6 / 30000.0) ** 2 * rt.ADU_PER_ELECTRON
        self.assertTrue(0.8 * expected < bright['mean_counts'] <= expected * 1.0001, (bright['mean_counts'], expected))
        est = img['dose_estimate_e_per_m2']                                     # camera estimate: brightest 1 % of the frame (film, no holes): below the dose
        self.assertTrue(0.7 * dose < est < 1.02 * dose and est != dose, (est, dose))
        img4 = acquire_image(h, exposure=4.0)
        self.assertAlmostEqual(img4['regions'][0]['mean_counts'] / bright['mean_counts'], 4.0, delta=0.2)
        self.assertAlmostEqual(img4['regions'][0]['snr'] / bright['snr'], 2.0, delta=0.2)         # Poisson: SNR ~ sqrt(dose)
        imgb = acquire_image(h, exposure=1.0, binning=2)
        self.assertAlmostEqual(imgb['regions'][0]['mean_counts'] / bright['mean_counts'], 4.0, delta=0.2)
        h.invoke('optics.set_brightness', value=0.5)                          # condensed to the crossover: most of the frame is dark
        dark = acquire_image(h, exposure=0.001)
        self.assertGreater(max(r['area_fraction_in_view'] for r in dark['regions'] if r['mean_counts'] == 0.0), 0.9)

    def test_lattice_fringes_need_resolution_and_the_aperture(self):
        h = camera(inserted(specimen=grid('auz', [phase('gold', zone=[1, 1, 0])])), mag=300000.0, ol=0)   # [110] particles: {111} and {200} excited
        h.invoke('optics.set_brightness', value=0.5)                          # crossover (2 mrad) and the grid height away from focus: washed out
        img0 = acquire_image(h, exposure=0.5)
        self.assertFalse([x for r in img0['regions'] for x in r['lattice_spacings_m']])
        h.invoke('optics.set_brightness', value=0.505)
        h.invoke('optics.auto_focus')                                         # near focus the spatial-coherence envelope passes the fringes
        img = acquire_image(h, exposure=0.5)
        d = sorted({x for r in img['regions'] for x in r['lattice_spacings_m']})
        self.assertTrue(d, img['regions'])
        gold = {round(x['d'], 12) for x in rt.substance('gold')['crystal']['_refl']}
        self.assertTrue(all(round(x, 12) in gold for x in d), d)
        self.assertTrue(all(x >= rt.LATTICE_RESOLUTION_M for x in d))
        h.invoke('aperture.set_objective', position=4)                      # 20 um: 5 mrad < 2 theta of Au 111 (14 mrad)
        img2 = acquire_image(h, exposure=0.5)
        self.assertFalse([x for r in img2['regions'] for x in r['lattice_spacings_m']])
        h.invoke('aperture.set_objective', position=0)
        h.invoke('optics.set_magnification', magnification=50000.0)          # Nyquist 0.87 nm > 0.235 nm
        img3 = acquire_image(h, exposure=0.5)
        self.assertFalse([x for r in img3['regions'] for x in r['lattice_spacings_m']])

    def test_thon_rings_follow_the_defocus(self):
        h = camera(inserted(specimen=grid('c', [], t_support=2e-8)), mag=150000.0, ol=0)
        h.invoke('optics.set_brightness', value=0.503)
        h.invoke('optics.auto_focus')
        self.assertLess(abs(h._defocus_true()), 5 * rt.AUTOFOCUS_SD_M)
        h.invoke('optics.set_defocus', defocus_m=h.dz_display + 2e-6)
        img = acquire_image(h, exposure=1.0)
        k = img['thon_ring_frequencies_per_m']
        self.assertTrue(k, img)
        lam = rt.wavelength_m(120000.0)
        self.assertAlmostEqual(k[0], 1.0 / math.sqrt(lam * 2e-6), delta=0.05 / math.sqrt(lam * 2e-6))   # first zero ~ (lambda dz)^-1/2
        h.invoke('optics.set_defocus', defocus_m=h.dz_display - 2e-6 + 4e-6)
        k2 = acquire_image(h, exposure=1.0)['thon_ring_frequencies_per_m']
        self.assertLess(k2[0], k[0])                                          # more defocus: the rings contract

    def test_eucentric_height_tilt_shift_and_standard_focus(self):
        h = inserted()
        h.invoke('stage.set_z', z_m=2e-5)
        h.step(3.0)
        h.invoke('optics.standard_focus')
        hgt = h._spec()['_mount']['h'] + 2e-5
        self.assertAlmostEqual(h._defocus_true(), -hgt, places=15)          # specimen above the eucentric plane: overfocus
        h.invoke('stage.set_tilt', tilt_rad=math.radians(10.0))
        h.step(6.0)
        cu, cv, t, h_axis = h._geometry()
        self.assertAlmostEqual(cv - h.stage['y'], hgt * math.tan(math.radians(10.0)), places=15)   # the image moves h tan(tilt)
        self.assertAlmostEqual(h_axis, hgt / math.cos(math.radians(10.0)), places=15)

    def test_auto_focus_and_exposure_need_a_view(self):
        h = inserted()
        rejected(h, 'optics.auto_focus', 'SCREEN_DOWN')
        camera(h)
        e = h.invoke('camera.auto_exposure')
        self.assertTrue(0.001 <= e['exposure_s'] <= 60.0)
        h.invoke('optics.toggle_diffraction')
        rejected(h, 'optics.auto_focus', 'NOT_IN_MAG_MODE')
        rejected(h, 'image.acquire', 'DIFF_MODE', exposure_s=1.0, binning=1)

    def test_low_mag_shows_the_grid_and_the_objective_aperture_blocks_it(self):
        h = camera(inserted(), mag=100.0, ol=0)
        h.invoke('optics.set_mode', mode='LOW_MAG')
        h.invoke('optics.set_magnification', magnification=100.0)
        h.invoke('optics.set_brightness', value=1.0)
        img = acquire_image(h, exposure=0.1)
        dark = sum(r['area_fraction_in_view'] for r in img['regions'] if r['mean_counts'] < 0.05 * img['regions'][0]['mean_counts'])
        mesh = rt.GRID_MESH[200]
        bars = 1.0 - ((mesh['pitch'] - mesh['bar']) / mesh['pitch']) ** 2
        self.assertAlmostEqual(dark, bars, delta=0.12)                     # 200 mesh: about half of the area is bars
        h.invoke('aperture.set_objective', position=2)
        img2 = acquire_image(h, exposure=0.1)
        dark2 = sum(r['area_fraction_in_view'] for r in img2['regions'] if r['mean_counts'] < 0.05 * max(1e-9, img['regions'][0]['mean_counts']))
        self.assertGreater(dark2, 0.95)                                    # facility: no beam in LOW MAG with the objective aperture in


class Diffraction(unittest.TestCase):
    def test_polycrystalline_rings_and_camera_length_calibration(self):
        al = grid('al', [phase('aluminium', form='film', size=2e-8, t=3e-8, cov=1.0, cryst='polycrystalline')])
        h = inserted(specimen=al)
        h.invoke('aperture.set_selected_area', position=1)
        h.invoke('optics.set_brightness', value=0.9)
        h.invoke('optics.toggle_diffraction')
        h.invoke('screen.set', up=True)
        h.step(1.0)
        p = acquire_pattern(h)
        self.assertGreaterEqual(len(p['rings']), 5)
        self.assertEqual(p['spots'], [])
        refl = rt.substance('aluminium')['crystal']['_refl']
        ratios = []
        for ring in p['rings'][:5]:
            true_d = min((x['d'] for x in refl), key=lambda d: abs(d - ring['d_spacing_display_m'] * (1 + h.cl_err)))
            ratios.append(ring['d_spacing_display_m'] / true_d)
        self.assertLess(max(ratios) - min(ratios), 2e-3)                    # one calibration constant for all rings
        self.assertAlmostEqual(np.mean(ratios), 1.0 / (1.0 + h.cl_err), delta=2e-3)
        strongest = max(p['rings'], key=lambda r: r['relative_intensity'])
        self.assertAlmostEqual(strongest['d_spacing_display_m'] * (1 + h.cl_err), 0.2338e-9, delta=0.003e-9)   # Al 111
        self.assertEqual(p['halos'][0]['relative_intensity'] > 0.0, True)
        self.assertAlmostEqual(p['selected_area_diameter_m'], 100e-6 / 30.0, places=15)

    def test_single_crystal_zone_axis_spots(self):
        si = grid('si', [phase('silicon', form='film', size=1e-6, t=5e-8, cov=1.0, cryst='single_crystal', zone=[0, 0, 1])], support='none')
        h = inserted(specimen=si)
        h.invoke('aperture.set_selected_area', position=3)
        h.invoke('optics.set_brightness', value=0.9)
        h.invoke('optics.toggle_diffraction')
        h.invoke('screen.set', up=True)
        h.step(1.0)
        p = acquire_pattern(h)
        self.assertEqual(p['rings'], [])
        self.assertGreater(len(p['spots']), 4)
        lam, L = rt.wavelength_m(120000.0), h._camera_length_true()
        radii = sorted({round(math.hypot(s['x_m'], s['y_m']) / (L * lam), -6) for s in p['spots']})
        g220 = 1.0 / 0.19201e-9
        self.assertTrue(any(abs(r - g220) < 0.02 * g220 for r in radii), radii)            # {220} of the [001] zone
        g200 = 1.0 / 0.27155e-9
        self.assertFalse(any(abs(r - g200) < 0.02 * g200 for r in radii))                 # 200 is kinematically forbidden in diamond Si

    def test_amorphous_halo_and_objective_aperture_truncation(self):
        h = inserted(specimen=grid('pc', [phase('polystyrene', form='film', size=1e-6, t=3e-8, cov=1.0, cryst='amorphous')]))
        h.invoke('optics.toggle_diffraction')
        h.invoke('screen.set', up=True)
        h.step(1.0)
        p = acquire_pattern(h)
        self.assertEqual((p['rings'], p['spots']), ([], []))
        self.assertTrue(p['halos'])
        h2 = inserted()
        h2.invoke('optics.set_brightness', value=0.9)
        h2.invoke('optics.toggle_diffraction')
        h2.invoke('screen.set', up=True)
        h2.step(1.0)
        full = acquire_pattern(h2)
        h2.invoke('aperture.set_objective', position=4)
        cut = acquire_pattern(h2)
        self.assertLess(len(cut['rings']), len(full['rings']))
        self.assertTrue(all(r['radius_m'] < h2._camera_length_true() * math.tan(20e-6 / (2 * rt.OBJ_FOCAL_M)) for r in cut['rings']))


class BeamDamage(unittest.TestCase):
    def test_formvar_loses_mass_under_an_intense_beam(self):
        h = inserted(specimen=grid('fv', [], support='formvar', t_support=5e-8, mesh='400'))
        camera(h, mag=30000.0)
        x, y = open_square(h, (3e-4, 3e-4))
        h.invoke('optics.set_brightness', value=1.0)                          # beam spread to 200 um while the stage travels (negligible dose)
        h.invoke('stage.move_xy', x_m=x, y_m=y)                               # a fresh area
        h.step(10.0)
        h.invoke('optics.set_brightness', value=0.53)
        d0 = h.read('evaluator')['truth.view_center_dose_c_m2']
        t0 = acquire_image(h, exposure=1.0)['regions'][0]['mean_counts']      # spread beam (about 12 um): a few C/m^2
        h.invoke('optics.set_brightness', value=0.505)                        # condensed to about 2 um for 30 s
        h.step(30.0)
        h.invoke('optics.set_brightness', value=0.53)
        d1 = h.read('evaluator')['truth.view_center_dose_c_m2']
        self.assertLess(d0, 50.0)
        self.assertGreater(d1, 1e4)                                          # far above Formvar's characteristic dose (~400 C/m^2 at 120 kV)
        t1 = acquire_image(h, exposure=1.0)['regions'][0]['mean_counts']
        T0, T1 = transmission('formvar', 5e-8), transmission('formvar', 0.7 * 5e-8)
        self.assertAlmostEqual(t1 / t0, T1 / T0, delta=0.01)                 # 30 % mass loss (residual 0.7): the film transmits more

    def test_halide_crystallinity_fades_gold_does_not(self):
        def rings(sub):
            h = inserted(specimen=grid(sub, [phase(sub, form='film', size=2e-8, t=4e-8, cov=1.0, cryst='polycrystalline')]))
            h.invoke('optics.set_brightness', value=0.51)                     # about 4 um: the diffracting area is the damaged area
            h.invoke('optics.toggle_diffraction')
            h.invoke('screen.set', up=True)
            h.step(1.0)
            p0 = acquire_pattern(h)
            h.step(300.0)                                                     # about 3 characteristic doses of the halide at 120 kV
            p1 = acquire_pattern(h)
            halo = lambda p: sum(x['relative_intensity'] for x in p['halos'])  # noqa: E731
            return p0, p1, halo(p0), halo(p1)
        _, _, h0, h1 = rings('halite')
        self.assertGreater(h1, h0)                                           # radiolysis turns Bragg scattering into diffuse scattering
        g0, g1, gh0, gh1 = rings('gold')
        self.assertEqual(len(g0['rings']), len(g1['rings']))
        self.assertAlmostEqual(gh0, gh1, places=9)


class LedgersIsolationSessions(unittest.TestCase):
    def test_charge_and_gas_ledgers_close(self):
        h = camera(inserted())
        acquire_image(h)
        h.invoke('optics.toggle_diffraction')
        acquire_pattern(h)
        h.invoke('optics.toggle_diffraction')
        h.invoke('aperture.set_objective', position=0)
        h.invoke('filament.set_on', on=False)
        h.invoke('stage.neutral')
        h.step(1.0)
        h.invoke('holder.withdraw')
        h.step(5.0)
        h.invoke('airlock.set_pump', pump=False)
        h.step(20.0)
        h.invoke('holder.remove')
        h.step(15.0)
        led = h.ledger()
        self.assertLess(abs(led['charge_residual_c']), 1e-18 + 1e-9 * led['beam_charge_c'])
        self.assertLess(abs(led['gas_residual_mol']), 1e-15 + 1e-9 * led['gas_admitted_mol'])
        self.assertGreater(led['grid_stopped_charge_c'], 0.0)
        self.assertGreater(led['detector_charge_c'], 0.0)
        self.assertGreater(led['gas_released_mol'], 0.0)
        self.assertAlmostEqual(led['specimen_plane_charge_c'], led['grid_stopped_charge_c'] + led['objective_aperture_stopped_charge_c'] + led['detector_charge_c'],
                               delta=1e-9 * led['specimen_plane_charge_c'])

    def test_redeclared_room_temperature_keeps_the_gas_ledger(self):
        h = inserted(beam=False)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, room_k=300.15))
        h.step(30.0)
        led = h.ledger()
        self.assertLess(abs(led['gas_residual_mol']), 1e-15 + 1e-9 * led['gas_admitted_mol'])

    def test_two_instances_are_isolated(self):
        a, b = inserted(seed=1), inserted(seed=1)
        camera(a)
        acquire_image(a)
        self.assertEqual(b.images, {})
        self.assertNotEqual(a.read('evaluator'), b.read('evaluator'))

    def test_agent_does_not_see_truth(self):
        h = inserted()
        h.register_principal('a', 'agent')
        h.register_principal('e', 'evaluator')
        a, e = h.session('a'), h.session('e')
        ra = a.read(instance_id=h.instance_id)['observations']
        re_ = e.read(instance_id=h.instance_id)['observations']
        self.assertFalse(any(k.startswith('truth.') for k in ra))
        self.assertTrue(any(k.startswith('truth.') for k in re_))
        with self.assertRaises(rt.ActionRejected):
            a.read(instance_id=h.instance_id, state_ids=['truth.column_pressure_pa'])
        d = a.describe(instance_id=h.instance_id)
        self.assertNotIn('ledger.read', d['capabilities'])
        self.assertNotIn('specimen.provide', d['capabilities'])
        self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']))
        r = a.invoke(instance_id=h.instance_id, capability_id='ledger.read', arguments={}, request_id='x')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        camera(h)
        rid = a.invoke(instance_id=h.instance_id, capability_id='image.acquire', arguments=dict(exposure_s=0.5, binning=1), request_id='i')['operation_id']
        h.step(2.0)
        out = a.poll(rid)['result']
        self.assertEqual(out['template_source']['library'], rt.LIBRARY_REL)
        self.assertNotIn('truth', json.dumps(out))

    def test_records_and_control_belong_to_their_agent(self):
        h = camera(inserted())
        for p in ('a1', 'a2'):
            h.register_principal(p, 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        I = h.instance_id
        r = s1.invoke(instance_id=I, capability_id='image.acquire', arguments=dict(exposure_s=3.0, binning=1), request_id='r1')
        self.assertEqual(r['status'], 'accepted')
        self.assertEqual(s1.invoke(instance_id=I, capability_id='image.acquire', arguments=dict(exposure_s=3.0, binning=1), request_id='r1'), r)
        self.assertEqual(s1.invoke(instance_id=I, capability_id='image.acquire', arguments=dict(exposure_s=1.0, binning=1),
                                   request_id='r1')['error']['code'], 'REQUEST_CONFLICT')
        for cap, args in (('ht.set_voltage', dict(voltage_v=80000.0)), ('filament.set_on', dict(on=False)), ('aperture.set_objective', dict(position=0)),
                          ('holder.load_grid', dict(holder_id='high_tilt', specimen_id='x'))):
            self.assertEqual(s2.invoke(instance_id=I, capability_id=cap, arguments=args, request_id='g-' + cap)['error']['code'], 'ACCESS_DENIED')
        o2 = s2.read(instance_id=I)['observations']
        self.assertTrue(o2['acquisition.started_by_other_agent']['value'])
        self.assertIsNone(o2['acquisition.id']['value'])
        h.step(5.0)
        rid = h.operations[r['operation_id']]['record_id']
        self.assertEqual(s2.invoke(instance_id=I, capability_id='image.result', arguments=dict(image_id=rid), request_id='x2')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s2.invoke(instance_id=I, capability_id='image.result', arguments=dict(image_id='img-99'), request_id='x3')['error']['code'],
                         'UNKNOWN_RECORD')
        self.assertEqual(s1.invoke(instance_id=I, capability_id='image.result', arguments=dict(image_id=rid), request_id='x4')['status'], 'succeeded')
        self.assertEqual(s1.read(instance_id=I)['observations']['result.last_image_id']['value'], rid)
        self.assertIsNone(s2.read(instance_id=I)['observations']['result.last_image_id']['value'])
        self.assertEqual(s2.invoke(instance_id=I, capability_id='records.list', arguments={}, request_id='x5')['result']['images'], [])
        with self.assertRaises(rt.ActionRejected):
            s2.poll(r['operation_id'])

    def test_cancel_keeps_the_dose_and_discards_the_frame(self):
        h = camera(inserted())
        h.register_principal('a', 'agent')
        s = h.session('a')
        r = s.invoke(instance_id=h.instance_id, capability_id='image.acquire', arguments=dict(exposure_s=10.0, binning=1), request_id='c1')
        h.step(4.0)
        q0 = h.ledger()['beam_charge_c']
        d0 = h.read('evaluator')['truth.view_center_dose_c_m2']
        c = s.cancel(r['operation_id'], 'c2')
        self.assertEqual(c['result']['phase'], 'cancelled')
        rec = h.images[h.operations[r['operation_id']]['record_id']]
        self.assertEqual((rec['status'], rec['error_code'], rec['regions']), ('cancelled', 'USER_CANCEL', []))
        self.assertGreater(q0, 0.0)
        self.assertEqual(h.ledger()['beam_charge_c'], q0)
        self.assertEqual(h.read('evaluator')['truth.view_center_dose_c_m2'], d0)
        self.assertIsNone(h.acq)
        with self.assertRaises(rt.ActionRejected):
            s.cancel(r['operation_id'], 'c3')

    def test_beam_off_and_power_off_fail_running_acquisitions(self):
        h = camera(inserted())
        op = h.invoke('image.acquire', exposure_s=10.0, binning=1)['operation_id']
        h.step(2.0)
        h.invoke('filament.set_on', on=False)
        h.step(1.0)
        rec = h.images[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['error_code']), ('failed', 'BEAM_OFF'))
        h = camera(inserted())
        h.invoke('optics.set_magnification', magnification=5000.0)
        op = h.invoke('montage.acquire', rows=2, cols=2, overlap=0.1, exposure_s=0.5, binning=2)['operation_id']
        h.step(3.0)
        h.invoke('power.set', enabled=False)
        rec = h.montages[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['error_code']), ('failed', 'POWER_OFF'))

    def test_montage_path_duration_and_return(self):
        h = camera(inserted(), mag=5000.0)
        x0 = h.stage['x']
        op = h.invoke('montage.acquire', rows=2, cols=3, overlap=0.1, exposure_s=0.5, binning=2)['operation_id']
        o = h.operations[op]
        fov = 2048 * 21.7e-6 / 5000.0
        step = fov * 0.9
        self.assertAlmostEqual(h.montages[o['record_id']]['tile_step_m'], step, places=15)
        moves = [max(abs(a['x'] - b['x']), abs(a['y'] - b['y'])) for a, b in zip([dict(x=x0, y=h.stage['y'])] + o['schedule'][:-1], o['schedule'])]
        self.assertAlmostEqual(o['duration'], sum(m / rt.STAGE_XY_SPEED for m in moves) + 6 * (rt.MONTAGE_SETTLE_S + 0.5 + rt.CAMERA_READOUT_S), places=9)
        h.step(1.0)
        self.assertAlmostEqual(h.stage['x'], o['schedule'][0]['x'], places=15)          # the stage drives the field to the first tile
        self.assertAlmostEqual(h.stage['y'], o['schedule'][0]['y'], places=15)
        h.step(o['duration'] + 10.0)
        rec = h.montages[o['record_id']]
        self.assertEqual(rec['status'], 'completed')
        self.assertEqual(h.invoke('montage.result', montage_id=o['record_id'])['status'], 'completed')
        self.assertEqual((len(rec['mosaic']), len(rec['mosaic'][0])), (2 * rt.TILE_PX, 3 * rt.TILE_PX))
        self.assertAlmostEqual(h.stage['x'], x0, places=15)
        h.invoke('optics.set_mode', mode='LOW_MAG')
        h.invoke('optics.set_magnification', magnification=50.0)
        rejected(h, 'montage.acquire', 'MONTAGE_OUT_OF_RANGE', rows=8, cols=8, overlap=0.0, exposure_s=0.1, binning=1)

    def test_query_and_bookkeeping_capabilities(self):
        h = camera(inserted())
        out = h.invoke('sim.step', dt_s=2.0)
        self.assertAlmostEqual(out['time_s'], h.time_s, places=12)
        img = acquire_image(h)
        h.invoke('optics.toggle_diffraction')
        pat = acquire_pattern(h)
        self.assertEqual(h.invoke('image.result', image_id=img['image_id'])['status'], 'completed')
        self.assertEqual(h.invoke('diffraction.result', pattern_id=pat['pattern_id'])['status'], 'completed')
        self.assertEqual(h.invoke('records.list'), dict(images=[img['image_id']], patterns=[pat['pattern_id']], montages=[]))
        codes = [e['code'] for e in h.invoke('events.read', since_s=0.0)['events']]
        for c in ('POWER_ON', 'SOFTWARE_READY', 'VACUUM_READY', 'HT_ON', 'GRID_LOADED', 'HOLDER_AT_AIRLOCK', 'HOLDER_IN_COLUMN', 'FILAMENT_ON', 'IMAGE_START',
                  'DIFF_START', 'ACQ_COMPLETED'):
            self.assertIn(c, codes)
        prov = h.invoke('provenance.read')
        self.assertEqual((prov['library'], prov['substances']), (rt.LIBRARY_REL, ['amorphous_carbon', 'gold']))
        led = h.invoke('ledger.read')
        self.assertEqual(set(led), set(rt.LEDGER_KEYS))
        self.assertGreater(led['beam_charge_c'], 0.0)


# ---------------------------------------------------------------- declared effects cover what the process changes
def _scenario(name):
    """Scenario states for the with/without-call comparison (built with a seeded noise stream; the comparison replaces it by QuietRng)."""
    R = rt.JeolJem1400flashRuntime()
    inv = R.invoke
    if name == 'fresh':
        return R
    inv('plant.configure', **rt.PLANT_EXAMPLE)
    inv('specimen.provide', **AU)
    inv('specimen.provide', **grid('stained_section', [phase('epoxy_resin', form='section', size=1e-6, t=7e-8, cov=0.8, cryst='amorphous', stain=0.1)],
                                   support='formvar', t_support=2e-8, mesh='300'))
    inv('specimen.provide', **grid('hal', [phase('halite', form='film', size=2e-8, t=4e-8, cov=1.0, cryst='polycrystalline')]))
    if name == 'off':
        return R
    inv('power.set', enabled=True)
    if name == 'booting':
        R.step(10.0)
        return R
    R.step(260.0)
    if name == 'ready':
        return R
    if name == 'power_cycled_off':
        inv('power.set', enabled=False)
        R.step(5.0)
        return R
    inv('ht.set_on', on=True)
    if name == 'ht_ramping':
        R.step(30.0)
        return R
    R.step(125.0)
    if name == 'ht_ready':
        return R
    if name in ('high_tilt_screen', 'sec_beam_screen', 'sec_moving', 'halite_diffracting'):
        sid, holder = {'high_tilt_screen': (AU['specimen_id'], 'high_tilt'), 'halite_diffracting': ('hal', 'quick_change')}.get(name, ('stained_section', 'quick_change'))
        inv('holder.load_grid', holder_id=holder, specimen_id=sid)
        inv('holder.insert_to_airlock', holder_id=holder)
        R.step(12.0)
        inv('airlock.set_pump', pump=True)
        R.step(60.0)
        inv('holder.insert')
        R.step(5.0)
        inv('filament.set_on', on=True)
        R.step(25.0)
        inv('aperture.set_condenser', inserted=True)
        inv('optics.set_brightness', value=0.53)
        R.step(1.0)
        if name == 'high_tilt_screen':
            inv('stage.set_tilt', tilt_rad=math.radians(60.0))
            R.step(12.7)
        elif name == 'sec_moving':
            inv('stage.move_xy', x_m=1e-4, y_m=0.0)
            R.step(0.4)
        elif name == 'halite_diffracting':
            inv('screen.set', up=True)
            inv('optics.toggle_diffraction')
            R.step(1.0)
            inv('diffraction.acquire', exposure_s=3.0, binning=1)
            R.step(1.4)
        return R
    inv('holder.load_grid', holder_id='quick_change', specimen_id=AU['specimen_id'])
    if name == 'bench_loaded':
        return R
    inv('holder.insert_to_airlock', holder_id='quick_change')
    if name in ('holder_entering', 'entering_unpowered'):
        R.step(3.0)
        if name == 'entering_unpowered':
            inv('power.set', enabled=False)
            R.step(0.5)
        return R
    R.step(12.0)
    if name == 'airlock_vented':
        return R
    inv('airlock.set_pump', pump=True)
    if name == 'airlock_pumping':
        R.step(10.0)
        return R
    R.step(60.0)
    if name == 'airlock_evacuated':
        return R
    if name == 'airlock_unpowered':
        inv('power.set', enabled=False)
        R.step(2.0)
        return R
    inv('holder.insert')
    if name == 'turning_in':
        R.step(0.5)
        return R
    R.step(5.0)
    if name == 'column_dark':
        return R
    inv('filament.set_on', on=True)
    if name == 'heating':
        R.step(5.0)
        return R
    R.step(25.0)
    inv('aperture.set_condenser', inserted=True)
    inv('optics.set_brightness', value=0.52)
    R.step(1.0)
    if name == 'beam_screen':
        return R
    if name == 'cooling_error':
        inv('plant.configure', **dict(rt.PLANT_EXAMPLE, cooling_water_m3_s=2.0 / 60000.0))
        R.step(1.0)
        return R
    inv('screen.set', up=True)
    R.step(1.0)
    if name == 'beam_camera':
        return R
    tail = {
        'imaging': [('image.acquire', dict(exposure_s=2.0, binning=1)), 0.5],
        'stage_moving': [('stage.move_xy', dict(x_m=2e-4, y_m=-1e-4)), 0.3],
        'tilting': [('stage.set_tilt', dict(tilt_rad=math.radians(10.0))), 0.5],
        'diff': [('optics.toggle_diffraction', {}), 1.0],
        'diffracting': [('optics.toggle_diffraction', {}), 1.0, ('diffraction.acquire', dict(exposure_s=2.0, binning=1)), 0.5],
        'montage': [('optics.set_magnification', dict(magnification=5000.0)), ('montage.acquire', dict(rows=2, cols=2, overlap=0.1, exposure_s=0.3, binning=2)), 0.7],
        'low_mag': [('optics.set_mode', dict(mode='LOW_MAG')), ('aperture.set_objective', dict(position=2)), 1.0],
        'withdrawing': [('filament.set_on', dict(on=False)), ('holder.withdraw', {}), 0.5],
        'airlock_back': [('filament.set_on', dict(on=False)), ('holder.withdraw', {}), 3.0],
        'venting': [('filament.set_on', dict(on=False)), ('holder.withdraw', {}), 3.0, ('airlock.set_pump', dict(pump=False)), 0.3],
        'far_from_eucentric': [('stage.set_z', dict(z_m=1.9e-4)), 25.0],
        'screen_flipping': [('screen.set', dict(up=False)), 0.2],
        'long_move': [('stage.move_xy', dict(x_m=8e-4, y_m=0.0)), 2.5],
        'removing': [('filament.set_on', dict(on=False)), ('holder.withdraw', {}), 3.0, ('airlock.set_pump', dict(pump=False)), 10.0, ('holder.remove', {}), 2.0],
        'removed': [('filament.set_on', dict(on=False)), ('holder.withdraw', {}), 3.0, ('airlock.set_pump', dict(pump=False)), 10.0, ('holder.remove', {}), 12.0],
        'defocused': [('stage.set_z', dict(z_m=2e-5)), 3.0, ('optics.set_defocus', dict(defocus_m=3e-6)), ('optics.set_magnification', dict(magnification=200000.0)),
                      1.0],
        'power_off_in_column': [('power.set', dict(enabled=False)), 10.0],
    }
    if name in tail:
        for item in tail[name]:
            if isinstance(item, float):
                R.step(item)
            else:
                inv(item[0], **item[1])
        return R
    if name == 'error_pending':
        R.p_gun = 1e-3
        R.step(1.0)
        return R
    if name in ('burned_out', 'burned_out_ht_off'):
        R.fil_used_s = R.fil_life_s + 1.0
        R.step(1.0)
        if name == 'burned_out_ht_off':
            inv('ht.set_on', on=False)
            R.step(1.0)
        return R
    raise KeyError(name)


SCENARIOS = ['fresh', 'off', 'booting', 'ready', 'ht_ramping', 'ht_ready', 'bench_loaded', 'holder_entering', 'airlock_vented', 'airlock_pumping',
             'airlock_evacuated', 'turning_in', 'column_dark', 'heating', 'beam_screen', 'beam_camera', 'imaging', 'stage_moving', 'tilting', 'diff',
             'diffracting', 'montage', 'low_mag', 'withdrawing', 'airlock_back', 'removing', 'removed', 'defocused', 'error_pending', 'power_off_in_column',
             'burned_out', 'burned_out_ht_off', 'power_cycled_off', 'entering_unpowered', 'airlock_unpowered', 'venting', 'far_from_eucentric', 'cooling_error',
             'high_tilt_screen', 'sec_beam_screen', 'sec_moving', 'halite_diffracting', 'screen_flipping', 'long_move']
STEP_DTS = (0.4, 1.0, 3.0, 15.0, 120.0)


def _variants(cap):
    ex = copy.deepcopy(rt.CAPS[cap]['example_arguments'])
    extra = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE, room_k=300.15), dict(rt.PLANT_EXAMPLE, mains_voltage_v=150.0), dict(rt.PLANT_EXAMPLE, cooling_water_m3_s=1.0 / 60000.0)],
        'specimen.provide': [dict(AU, specimen_id='new_one')],
        'holder.load_grid': [dict(holder_id='high_tilt', specimen_id='stained_section'), dict(holder_id='quick_change', specimen_id='stained_section')],
        'holder.unload_grid': [dict(holder_id='high_tilt')], 'holder.insert_to_airlock': [dict(holder_id='high_tilt')], 'airlock.set_pump': [dict(pump=False)],
        'power.set': [dict(enabled=False)], 'ht.set_voltage': [dict(voltage_v=80000.0)], 'ht.set_on': [dict(on=False)], 'filament.set_on': [dict(on=False)],
        'filament.set_bias': [dict(bias=20.0)], 'optics.set_mode': [dict(mode='LOW_MAG'), dict(mode='DIFF')],
        'optics.set_magnification': [dict(magnification=300000.0), dict(magnification=100.0)], 'optics.set_camera_length': [dict(camera_length_m=2.0)],
        'optics.set_magnification_step_fraction': [dict(fraction=0.9), dict(fraction=0.0)], 'optics.set_brightness': [dict(value=0.5), dict(value=1.0)],
        'optics.set_spot_size': [dict(spot=5)], 'optics.set_defocus': [dict(defocus_m=-5e-6)], 'aperture.set_condenser': [dict(inserted=False)],
        'aperture.set_objective': [dict(position=0), dict(position=4)], 'aperture.set_selected_area': [dict(position=0), dict(position=4)],
        'screen.set': [dict(up=False)], 'stage.move_xy': [dict(x_m=-3e-4, y_m=2e-4)], 'stage.set_z': [dict(z_m=5e-5)],
        'stage.set_tilt': [dict(tilt_rad=-math.radians(15.0))], 'image.acquire': [dict(exposure_s=0.1, binning=2)],
        'diffraction.acquire': [dict(exposure_s=0.1, binning=2)], 'montage.acquire': [dict(rows=1, cols=2, overlap=0.2, exposure_s=0.1, binning=4)],
        'sim.step': [dict(dt_s=2.0)],
    }
    return [ex] + extra.get(cap, [])


def _flat(v, pre=''):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            out.update(_flat(x, f'{pre}/{k}'))
        return out or {pre: '{}'}
    if isinstance(v, list):
        out = {}
        for i, x in enumerate(v):
            out.update(_flat(x, f'{pre}[{i}]'))
        return out or {pre: '[]'}
    return {pre: v}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _changed(a, b, scale=None):
    """Relative comparison (no absolute floor: slots in A/m^2 or Pa change by less than 1e-9 in absolute terms)."""
    if _num(a) and _num(b):
        return abs(a - b) > 1e-9 * (max(abs(a), abs(b)) if scale is None else scale) + 1e-300
    return a != b


def _step_differs(v1, a2, v0, b2):
    """Does the step change the value differently with the call (v1 -> a2) than without it (v0 -> b2)?  (leaf-wise, numeric increments)"""
    f1, fa, f0, fb = _flat(v1), _flat(a2), _flat(v0), _flat(b2)
    for leaf in set(f1) | set(fa) | set(f0) | set(fb):
        x1, xa, x0, xb = (f.get(leaf, '<absent>') for f in (f1, fa, f0, fb))
        ca, cb = _changed(x1, xa), _changed(x0, xb)
        if not (ca or cb):
            continue
        if ca and cb and all(_num(x) for x in (x1, xa, x0, xb)) and not _changed(xa - x1, xb - x0, max(abs(x1), abs(xa), abs(x0), abs(xb))):
            continue
        if ca and cb and (x1, xa) == (x0, xb):
            continue
        return True
    return False


class DeclaredEffects(unittest.TestCase):
    def test_on_step_effects_cover_what_the_process_changes(self):
        """For every capability accepted in any scenario state: the slots it changes at once are declared effects, the slots whose change during the
        following step differs from an identical run without the call are declared on_step effects, and the step itself only changes slots declared
        as on_step effects of sim.step (noise-free and noisy runs; both runs draw identical noise during the step)."""
        missing_imm, missing_step = {}, {}
        states = {n: _scenario(n) for n in SCENARIOS}
        for noisy in (False, True):
            for n, S in states.items():
                for i, name in enumerate(('rng', 'rng_acq', 'rng_life', 'rng_mount', 'rng_af')):
                    setattr(S, name, random.Random(7 + i) if noisy else QuietRng())
            for cap in rt.CAPS:
                if cap in rt.UNSUPPORTED or rt.CAPS[cap]['kind'] in ('query', 'analysis'):
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
                                if _changed(v0[k], v1[k]) and k not in rt.ON_STEP['sim.step'] + rt.EFFECTS['sim.step']:
                                    missing_step.setdefault('sim.step', set()).add(k)
                            continue
                        for k in v0:
                            if json.dumps(v0[k], sort_keys=True, default=str) != json.dumps(v1[k], sort_keys=True, default=str) and _changed(v0[k], v1[k]) \
                                    and k not in rt.EFFECTS[cap]:
                                missing_imm.setdefault(cap, set()).add(k)
                        for dt in STEP_DTS:
                            A2, B2 = copy.deepcopy(A), copy.deepcopy(S)
                            for name in ('rng', 'rng_acq', 'rng_life', 'rng_mount', 'rng_af'):
                                setattr(A2, name, copy.deepcopy(getattr(B2, name)))
                            A2.step(dt)
                            B2.step(dt)
                            a2, b2 = A2.read('evaluator'), B2.read('evaluator')
                            for k in v0:
                                if k not in rt.ON_STEP.get(cap, []) and _step_differs(v1[k], a2[k], v0[k], b2[k]):
                                    missing_step.setdefault(cap, set()).add(k)
                                if k not in rt.ON_STEP['sim.step'] + ['clock.time_s'] and _changed(v0[k], b2[k]):
                                    missing_step.setdefault('sim.step', set()).add(k)
        self.assertEqual(missing_imm, {})
        self.assertEqual(missing_step, {})
        self.assertFalse([s for v in rt.ON_STEP.values() for s in v if rt.SLOTS[s]['origin'] == 'setpoint'])

    def test_units_and_sampling(self):
        meta = _meta()
        si = {'1', 's', 'm', 'rad', 'Pa', 'V', 'A', 'C', 'mol', 'K', 'J', 'm^2', '1/s', 'Pa*m^3/s', 'm^3', 'm^3/s', 'm/s', 'V/s', 'A/m^2', 'C/m^2', '1/m',
              '1/m^2', 'ohm'}
        units = {v['unit'] for v in meta['state_slots'].values()}
        for mech in meta['mechanisms'].values():
            units |= {p_['unit'] for p_ in mech['parameters'].values()}
        self.assertEqual(units - si, set(), units - si)
        h = camera(inserted())
        h.register_principal('e', 'evaluator')
        h.invoke('stage.move_xy', x_m=1e-4, y_m=0.0)
        h.step(0.5)
        obs = h.session('e').read(instance_id=h.instance_id)['observations']
        self.assertIsNone(obs['stage.x_indicated_m']['sample_window_s'])
        self.assertGreater(obs['stage.x_indicated_m']['value'], 0.0)                         # continuous between the cycles
        self.assertEqual(obs['vacuum.column_pressure_indicated_pa']['sample_window_s'], rt.CYCLE_S)
        self.assertEqual(obs['vacuum.column_pressure_indicated_pa']['sample_time_s'], math.floor(h.time_s))
        g0 = h.read()['vacuum.column_pressure_indicated_pa']
        h.step(0.2)
        self.assertEqual(h.read()['vacuum.column_pressure_indicated_pa'], g0)                 # held until the next cycle


class ReviewRound1(unittest.TestCase):
    """Regression tests for the findings F01-F20 of the first independent runtime review."""

    def test_f01_records_do_not_name_the_specimen(self):
        h = camera(inserted())
        h.register_principal('a', 'agent')
        a = h.session('a')
        op = a.invoke(instance_id=h.instance_id, capability_id='image.acquire', arguments=dict(exposure_s=0.5, binning=1), request_id='f01')['operation_id']
        h.step(2.0)
        img = a.poll(op)['result']
        self.assertEqual(img['template_source'], dict(library=rt.LIBRARY_REL, substances=[]))
        r = a.invoke(instance_id=h.instance_id, capability_id='provenance.read', arguments={}, request_id='f01p')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')

    def test_f02_indistinguishable_classes_form_one_region(self):
        h = camera(inserted(specimen=grid('thin', [], t_support=2e-10, holes=0.5)))      # 0.2 nm carbon over 50 % holes: invisible
        img = acquire_image(h, exposure=0.2)
        self.assertEqual(len(img['regions']), 1, img['regions'])
        self.assertAlmostEqual(img['regions'][0]['area_fraction_in_view'], 1.0, places=9)
        h2 = camera(inserted(specimen=grid('thick', [], t_support=5e-8, holes=0.5)))     # 50 nm carbon over holes: two regions
        self.assertGreaterEqual(len(acquire_image(h2, exposure=0.5)['regions']), 2)

    def test_f03_coherence_envelope_removes_fringes_and_thon_rings(self):
        c = camera(inserted(specimen=grid('c', [], t_support=2e-8)), mag=150000.0, ol=0)
        c.invoke('optics.set_brightness', value=0.5)                           # crossover: 2 mrad convergence
        c.invoke('optics.set_defocus', defocus_m=2e-5)
        self.assertEqual(acquire_image(c, exposure=1.0)['thon_ring_frequencies_per_m'], [])
        lam = rt.wavelength_m(120000.0)
        self.assertLess(rt.spatial_envelope(1.0 / math.sqrt(lam * 2e-5), 2e-5, 120000.0, 2e-3), 1e-100)
        self.assertGreater(rt.spatial_envelope(1.0 / math.sqrt(lam * 2e-6), 2e-6, 120000.0, 1e-5), 0.99)

    def test_f04_uranyl_stain_keeps_its_uranium(self):
        dmg = rt.substance(rt.STAIN_ID)['damage']
        w_u = rt.substance(rt.STAIN_ID)['composition_mass_fraction']['U']
        self.assertGreaterEqual(dmg['residual_fraction'], w_u)
        self.assertEqual(dmg['evidence'], 'assumption')
        v, a = 120000.0, 7.5e-3
        full = rt.mu_damaged(rt.STAIN_ID, v, a, dmg['residual_fraction'])
        rho = rt.substance(rt.STAIN_ID)['density_g_cm3'] * 1000.0
        mu_u = rho * w_u / (rt.element('U')['molar_mass_g_mol'] * 1e-3) * rt.N_AVOGADRO * rt.sigma_el_outside(92, v, a)
        self.assertGreaterEqual(float(full), mu_u)                              # the stain never scatters less than its uranium
        self.assertAlmostEqual(float(rt.mu_damaged(rt.STAIN_ID, v, a, 1.0)), rt.mu_el_outside(rt.STAIN_ID, v, a), places=6)

    def test_f06_tilt_does_not_reduce_the_damage(self):
        out = []
        for tilt in (0.0, 60.0):
            h = inserted(specimen=grid('fv', [], support='formvar', t_support=5e-8), holder='high_tilt')
            h.invoke('stage.set_tilt', tilt_rad=math.radians(tilt))
            h.step(35.0)
            h.invoke('optics.set_brightness', value=0.51)
            f0 = h.read('evaluator')['truth.view_center_dose_c_m2']
            h.step(20.0)
            out.append(h.read('evaluator')['truth.view_center_dose_c_m2'] - f0)
        self.assertAlmostEqual(out[1] / out[0], 1.0, delta=0.01)                 # fluence through each molecule, not the plane dose

    def test_f07_long_beam_on_step_is_fast(self):
        import time
        h = camera(inserted(specimen=grid('hap', [phase('hydroxyapatite', form='film', size=2e-8, t=4e-8, cov=1.0, cryst='single_crystal')])))
        h.invoke('optics.set_brightness', value=0.52)
        t0 = time.perf_counter()
        h.step(3600.0)
        self.assertLess(time.perf_counter() - t0, 60.0)

    def test_f08_holder_arrival_is_exact_for_any_chopping(self):
        runs = []
        for chop in (30.0, 0.1, 0.7):
            h = inserted(beam=False)
            h.invoke('filament.set_on', on=False)
            h.invoke('stage.neutral')
            h.step(20.0)
            h.invoke('holder.withdraw')
            h.step(3.0)
            h.invoke('holder.insert')
            t_ins = h.time_s
            left = 30.0
            while left > 1e-9:
                d = min(chop, left)
                h.step(d)
                left -= d
            ev = [e for e in h.events if e['code'] == 'HOLDER_IN_COLUMN'][-1]
            runs.append((ev['time_s'] - t_ins, h.p_col, h.p_gun))
        for r in runs[1:]:
            self.assertAlmostEqual(r[0], runs[0][0], places=9)
            self.assertAlmostEqual(r[1] / runs[0][1], 1.0, places=9)
        self.assertAlmostEqual(runs[0][0], rt.HOLDER_STROKE_M['airlock'] / rt.HOLDER_SPEED_M_S, places=9)

    def test_f09_thon_zeros_include_the_chi_zero_root(self):
        v, dz = 120000.0, 20e-9
        lam = rt.wavelength_m(v)
        k0 = math.sqrt(2.0 * dz / (rt.CS_M * lam * lam))
        zeros = rt.thon_zeros(dz, v, 3.2e9, 8)
        self.assertTrue(any(abs(k - k0) < 1e3 for k in zeros), (zeros, k0))

    def test_f10_rejected_auto_focus_changes_nothing_later(self):
        a, b = camera(inserted()), camera(inserted())
        for h in (a, b):
            h.invoke('stage.set_z', z_m=1.9e-4)
            h.step(25.0)
        self.assertEqual(a.read(), b.read())
        with self.assertRaises(rt.ActionRejected):
            a.invoke('optics.auto_focus')
        a.step(5.0)
        b.step(5.0)
        self.assertEqual(a.read(), b.read())

    def test_f11_integer_valued_floats_are_accepted(self):
        h = camera(inserted(), mag=5000.0)
        out = h.invoke('montage.acquire', rows=2.0, cols=2, overlap=0.1, exposure_s=0.2, binning=2.0)
        self.assertIn('operation_id', out)
        self.assertIsInstance(h.acq['rows'], int)

    def test_f12_aperture_sinks_in_the_charge_ledger(self):
        h = inserted(specimen=None)                                           # holder without a grid
        h.invoke('aperture.set_objective', position=2)
        h.invoke('optics.set_mode', mode='LOW_MAG')
        h.step(10.0)
        led = h.ledger()
        self.assertEqual(led['grid_stopped_charge_c'], 0.0)
        self.assertGreater(led['objective_aperture_stopped_charge_c'], 0.0)
        g = camera(inserted(), ol=0)
        g.invoke('optics.set_brightness', value=0.7)
        g.invoke('optics.toggle_diffraction')
        g.invoke('aperture.set_selected_area', position=4)
        l0 = g.ledger()
        g.step(10.0)
        l1 = g.ledger()
        det = l1['detector_charge_c'] - l0['detector_charge_c']
        sa = l1['selected_area_aperture_stopped_charge_c'] - l0['selected_area_aperture_stopped_charge_c']
        self.assertGreater(sa, 100.0 * det)                                   # a 0.33 um area of an 80 um beam
        self.assertLess(abs(l1['charge_residual_c']), 1e-9 * l1['beam_charge_c'])

    def test_f13_agent_outputs_are_estimates(self):
        h = camera(inserted())
        self.assertEqual(set(h.invoke('optics.set_brightness', value=0.53)), {'value'})
        h.invoke('stage.set_tilt', tilt_rad=0.1750565)
        h.step(10.0)
        img = acquire_image(h, exposure=0.5)
        self.assertEqual(img['tilt_rad'], h.read()['stage.tilt_indicated_rad'])
        self.assertNotEqual(img['dose_estimate_e_per_m2'], h._current_density() * 0.5 / rt.E_CHARGE)

    def test_f16_dose_follows_the_stage_path_continuously(self):
        h = camera(inserted(specimen=grid('fv', [], support='formvar', t_support=2e-8)))
        h.invoke('optics.set_brightness', value=0.505)
        x0, y0 = h.stage['x'], h.stage['y']
        xs = x0 + np.linspace(1e-5, 9e-5, 33)
        f0 = rt.dose_at(h._spec(), xs, np.full(len(xs), y0))[0]              # earlier exposures (the spread beam while the filament heated)
        h.invoke('stage.move_xy', x_m=x0 + 1e-4, y_m=y0)
        h.step(3.0)
        d = rt.dose_at(h._spec(), xs, np.full(len(xs), y0))[0] - f0
        J, r = h._current_density(), h._illum_d() / 2.0
        self.assertTrue(np.all(np.abs(d / (J * 2.0 * r / rt.STAGE_XY_SPEED) - 1.0) < 0.02), d / (J * 2.0 * r / rt.STAGE_XY_SPEED))

    def test_f17_central_disc_saturation(self):
        h = inserted(specimen=None)
        h.invoke('optics.set_brightness', value=0.5)
        h.invoke('screen.set', up=True)
        h.invoke('optics.toggle_diffraction')
        h.invoke('optics.set_camera_length', camera_length_m=3.0)
        h.step(1.0)
        p = acquire_pattern(h, exposure=0.5)
        self.assertTrue(p['central_beam_saturated'])

    def test_f18_spheres_and_grid_bars_under_tilt(self):
        h = inserted(holder='high_tilt')
        spec = copy.deepcopy(h._spec())
        p = spec['phases'][0]
        U = np.linspace(0.0, 2e-6, 400)
        V = np.linspace(0.0, 2e-6, 400)
        UU, VV = np.meshgrid(U, V)
        mean_t = []
        for tilt in (0.0, math.radians(60.0)):
            _, _, _, _, tl = rt._particle_at(UU.ravel(), VV.ravel() / math.cos(tilt), p, rt._phase_seed(spec, 0), tilt)
            mean_t.append(float(np.mean(tl[tl > 0])))
        self.assertAlmostEqual(mean_t[1] / mean_t[0], 1.0, delta=0.05)          # a sphere looks the same at any tilt
        spec['_mount'] = dict(h=0.0, phi=0.0, off_a=0.0, off_b=0.0)
        spec['phases'] = []
        g = np.linspace(-5e-4, 5e-4, 801)
        GU, GV = np.meshgrid(g, g)
        opens = []
        for tilt in (0.0, math.radians(60.0)):
            o = rt.sample_specimen(spec, GU.ravel(), GV.ravel() / math.cos(tilt), 1.25e-6, 120000.0, 0.06, tilt)
            opens.append(float(np.mean(o['open'])))
        mesh = rt.GRID_MESH[200]
        self.assertAlmostEqual(opens[0], (1 - mesh['bar'] / mesh['pitch']) ** 2, delta=0.01)
        w60 = mesh['bar'] + rt.GRID_THICKNESS_M * math.tan(math.radians(60.0))
        self.assertAlmostEqual(opens[1], (1 - mesh['bar'] / mesh['pitch']) * (1 - w60 / mesh['pitch']), delta=0.01)

    def test_f19_unpowered_labels(self):
        h = camera(inserted())
        h.invoke('screen.set', up=False)
        h.step(0.2)
        h.invoke('power.set', enabled=False)
        h.step(60.0)
        self.assertFalse(h.read()['screen.moving'])
        g = ready()
        g.invoke('specimen.provide', **AU)
        g.invoke('holder.load_grid', holder_id='quick_change', specimen_id=AU['specimen_id'])
        g.invoke('holder.insert_to_airlock', holder_id='quick_change')
        g.step(12.0)
        g.invoke('airlock.set_pump', pump=True)
        g.step(60.0)
        g.invoke('power.set', enabled=False)
        g.step(1.0)
        r = g.read()
        self.assertEqual((r['airlock.state'], r['panel.airlock_lamp'], r['airlock.pressure_indicated_pa']), ('idle', False, None))

    def test_f20_mirrored_states_need_the_host(self):
        meta = _meta()
        self.assertEqual(meta['capabilities']['screen.set']['model_effects']['mode'], 'host_required')
        self.assertEqual(meta['capabilities']['camera.auto_exposure']['model_effects']['mode'], 'none')
        for k, c in meta['capabilities'].items():
            me = c['model_effects']
            if me['mode'] == 'none':
                self.assertNotIn('绑定', me['description_zh'], k)


class ReviewRound2(unittest.TestCase):
    """Regression tests for the second independent runtime review (F07 revisited, F21-F23)."""

    def test_f07_hydroxyapatite_and_many_dose_events_stay_fast(self):
        import time
        h = camera(inserted(specimen=grid('hap', [phase('hydroxyapatite', form='film', size=2e-8, t=4e-8, cov=1.0, cryst='single_crystal')])))
        h.invoke('screen.set', up=False)                                       # the screen reading needs the view fractions every cycle
        t0 = time.perf_counter()
        h.step(3600.0)
        self.assertLess(time.perf_counter() - t0, 60.0)
        g = inserted(specimen=grid('sec', [phase('epoxy_resin', form='section', size=1e-6, t=7e-8, cov=0.8, cryst='amorphous', stain=0.1)],
                                   support='formvar', t_support=2e-8, mesh='300'))
        x0, y0 = g.stage['x'], g.stage['y']
        for i in range(300):                                                   # 600 overlapping dose events around the view
            g.invoke('stage.move_xy', x_m=x0 + (3e-6 if i % 2 else -3e-6), y_m=y0 + 1e-8 * i)
            g.step(1.0)
        self.assertGreater(len(g._spec()['_dose']), 300)
        t0 = time.perf_counter()
        g.step(3600.0)
        self.assertLess(time.perf_counter() - t0, 60.0)

    def test_f21_dose_events_do_not_depend_on_the_host_step(self):
        out = []
        for chop in (1.0, 0.001):
            h = camera(inserted(specimen=grid('fv', [], support='formvar', t_support=2e-8), holder='high_tilt'))
            h.invoke('optics.set_brightness', value=0.51)
            n0 = len(h._spec()['_dose'])
            h.invoke('stage.set_tilt', tilt_rad=math.radians(20.0))
            left = 12.0
            while left > 1e-9:
                d = min(chop, left)
                h.step(d)
                left -= d
            cu, cv, _, _ = h._geometry()
            out.append((len(h._spec()['_dose']) - n0, h.read('evaluator')['truth.view_center_dose_c_m2']))
        self.assertEqual(out[0][0], out[1][0])
        self.assertAlmostEqual(out[1][1] / out[0][1], 1.0, delta=1e-4)
        sweep = []
        for chop in (1.0, 1.0 / 60.0):
            h = inserted()
            h.invoke('optics.set_mode', mode='LOW_MAG')
            n0 = len(h._spec()['_dose'])
            h.invoke('stage.move_xy', x_m=h.stage['x'] + 8e-4, y_m=h.stage['y'])
            left = 17.0
            while left > 1e-9:
                d = min(chop, left)
                h.step(d)
                left -= d
            sweep.append(len(h._spec()['_dose']) - n0)
        self.assertEqual(sweep[0], sweep[1])
        self.assertLessEqual(sweep[1], 3)                                      # one swept run (+ the stays before and after)

    def test_f21_tilt_ramp_at_a_height_offset_is_chopping_independent(self):
        tracks = []
        for chop in (1.0, 0.01):
            h = camera(inserted(specimen=grid('fv', [], support='formvar', t_support=2e-8), holder='high_tilt'))
            h.invoke('optics.set_brightness', value=0.505)
            h.invoke('stage.set_z', z_m=2e-4)
            h.invoke('stage.set_tilt', tilt_rad=math.radians(60.0))
            h.step(50.0)
            f0 = None
            ts = np.radians(np.arange(60.25, 70.0, 0.5))
            hh = h._spec()['_mount']['h'] + h.stage['z']
            vs = h.stage['y'] + hh * np.tan(ts)
            us = np.full(len(ts), h.stage['x'])
            f0 = rt.dose_at(h._spec(), us, vs)[0]
            h.invoke('stage.set_tilt', tilt_rad=math.radians(70.0))
            left = 6.0
            while left > 1e-9:
                d = min(chop, left)
                h.step(d)
                left -= d
            tracks.append(rt.dose_at(h._spec(), us, vs)[0] - f0)
        self.assertTrue(np.all(np.abs(tracks[1] / tracks[0] - 1.0) < 1e-3), tracks[1] / tracks[0])

    def test_f26_out_and_back_within_a_cycle_doses_both_passes(self):
        h = camera(inserted(specimen=grid('fv', [], support='formvar', t_support=2e-8)))
        h.invoke('optics.set_brightness', value=0.505)
        h.step(1.0 - (h.time_s % 1.0))                                         # start at a cycle boundary
        x0, y0 = h.stage['x'], h.stage['y']
        xs = x0 + np.array([-15e-6, -10e-6, 7.5e-6, 10e-6])
        f0 = rt.dose_at(h._spec(), xs, np.full(len(xs), y0))[0]
        h.invoke('stage.move_xy', x_m=x0 + 20e-6, y_m=y0)                     # out 20 um (0.4 s) ...
        for _ in range(40):
            h.step(0.01)
        h.invoke('stage.move_xy', x_m=x0 - 20e-6, y_m=y0)                     # ... and back past the start, inside the same cycle
        h.step(2.0)                                                            # past the next cycle end (the dose is deposited per cycle)
        d = rt.dose_at(h._spec(), xs, np.full(len(xs), y0))[0] - f0
        one = h._current_density() * 2.0 * (h._illum_d() / 2.0) / rt.STAGE_XY_SPEED
        self.assertTrue(np.allclose(d / one, [1.0, 1.0, 2.0, 2.0], rtol=0.02), d / one)   # the excursion is crossed twice

    def test_f22_regions_follow_the_observable_layers(self):
        films = [phase('polystyrene', form='film', size=1e-6, t=5e-8, cov=0.6, cryst='amorphous'),
                 phase('polystyrene', form='film', size=1e-6, t=5e-8, cov=0.6, cryst='amorphous')]
        h = camera(inserted(specimen=grid('twin', films, t_support=1e-9, holes=0.0)), mag=3000.0)
        img = acquire_image(h, exposure=2.0)
        counts = sorted(r['mean_counts'] for r in img['regions'])
        self.assertLessEqual(len(img['regions']), 3, img['regions'])          # support-only, one layer (either film), two layers
        spec = h._spec()
        U = np.linspace(0, 20e-6, 200)
        UU, VV = np.meshgrid(U, U)
        o = rt.sample_specimen(spec, UU.ravel(), VV.ravel(), 1e-7, 120000.0, h._alpha(), 0.0)
        both = np.mean(o['label'] == 1 + 0b10011)
        self.assertGreater(both, 0.2)                                           # the overlap is its own class

    def test_f23_no_fringes_beyond_the_axial_information_limit(self):
        h = camera(inserted(specimen=grid('auz', [phase('gold', zone=[1, 1, 0])])), mag=1.5e6, ol=0)
        h.invoke('optics.set_brightness', value=0.505)
        h.invoke('optics.auto_focus')
        img = acquire_image(h, exposure=0.5)
        d = {x for r in img['regions'] for x in r['lattice_spacings_m']}
        lam = rt.wavelength_m(120000.0)
        self.assertTrue(all(math.exp(-0.5 * (math.pi * lam * rt.focus_spread_m(120000.0)) ** 2 / x ** 4) > 0.05 for x in d), d)
        self.assertNotIn(round(0.144e-9, 12), {round(x, 12) for x in d})


if __name__ == '__main__':
    unittest.main()
