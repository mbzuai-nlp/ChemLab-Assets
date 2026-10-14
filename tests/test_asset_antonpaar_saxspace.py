"""tests for CHEM-153 antonpaar_saxspace: python3 -m unittest tests/test_asset_antonpaar_saxspace.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances
are isolated, agent/evaluator truth isolation, conservation (photons, liquid volume, solute mass, tube energy), real return values validated
against the declared schemas (asset_tool lint_schema / validate_value), asynchronous completion and cancellation remnants, host-step
independence, declared immediate and on_step effects covering what the process changes; plus the instrument behaviour (generator interlocks and
ramps, vacuum below 3 mbar, TCStage range and heating rate, q range 0.03-40.7 nm^-1 and SWAXS to 60 deg, MYTHEN2 R 1K strips) and the scattering
physics (absolute scale from water cross-checked against the NIST SRM 3600 certified curve, Guinier Rg and molecular weight of SASBDB proteins
after desmearing, Percus-Yevick concentration effect, silver behenate calibration, silicon Ka doublet, NIST gold nanoparticles, sample states,
faults and the autosampler carry-over).
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
from asset_runtime import antonpaar_saxspace as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE = [], {}
_META = {}
RNGS = ('rng', 'rng_acq', 'rng_mount', 'rng_cal')
LYS = 'sasbdb_sasdrm8_lysozyme_c'
BSA = 'sasbdb_sasdeb5_bsa'
GC = 'nist_srm3600_glassy_carbon'
AU30 = 'nist_rm8012_gold_30nm'


def _meta():
    if not _META:
        _META.update(rt.export_antonpaar_saxspace_contract_metadata())
    return _META


_orig_invoke = rt.AntonpaarSaxspaceRuntime.invoke


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
        undeclared = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)
                      and _changed(before[k], after[k])} - set(rt.CAPS[capability_id]['effects'])
        if undeclared and rt.EFFECTS:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.AntonpaarSaxspaceRuntime.invoke = _instrumented_invoke


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

    def seed(self, x):
        pass


def configured(seed=1, **plant):
    h = rt.AntonpaarSaxspaceRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def ready(seed=1, xray=True, **plant):
    """Powered, SAXSdrive up, chiller flowing, key at I and (optionally) X-rays at 40 kV / 50 mA."""
    h = configured(seed, **plant)
    h.invoke('power.set', enabled=True)
    h.step(61.0)
    h.invoke('generator.set_key', on=True)
    if xray:
        h.invoke('generator.xray_on')
        h.step(21.0)
    return h


def liquid(h, sid, comps=(), volume=500e-9):
    h.invoke('sample.provide', sample_id=sid, form='solution', solvent='water', volume_m3=volume, thickness_m=None,
             components=[dict(template_id=t, concentration_kg_m3=c, model_parameters=None) for t, c in comps])
    return sid


def solid(h, sid, tid, thickness, form='solid'):
    h.invoke('sample.provide', sample_id=sid, form=form, solvent='none', volume_m3=0.0, thickness_m=thickness,
             components=[dict(template_id=tid, concentration_kg_m3=None, model_parameters=None)])
    return sid


def load(h, sid, holder='capillary_1mm', pump_s=220.0, temp=None, align=True):
    """Vent, open the door, exchange the holder, close, pump below 3 mbar, (optionally) set the temperature and TrueFocus-align."""
    if not h.door_open:
        h.invoke('vacuum.set_pump', on=False)
        h.step(80.0)
        h.invoke('chamber.set_door', open=True)
    if h.holder is not None or h.holder_broken:
        h.invoke('sample.unload')
    h.invoke('sample.load', sample_id=sid, holder=holder)
    h.invoke('chamber.set_door', open=False)
    h.invoke('vacuum.set_pump', on=True)
    if temp is not None:
        h.invoke('stage.set_temperature', temperature_k=temp)
    h.step(pump_s)
    if align and holder in ('capillary_1mm', 'mu_cell') and sid is not None:
        h.invoke('stage.auto_align')
        h.step(rt.ALIGN_S + 2.0)
    return h


def acquire(h, exposure=300.0, frames=1):
    op = h.invoke('acquisition.start', exposure_s=exposure, frames=frames)['operation_id']
    h.step(exposure + 1.0)
    rec = h.records[h.operations[op]['record_id']]
    assert rec['status'] == 'completed', (rec['status'], rec['error_code'])
    return rec['record_id']


def empty_beam(h, pump_s=220.0):
    """Vent, open the door, remove the holder, close and pump: the beam passes only air and the collimation (empty-beam background)."""
    if not h.door_open:
        h.invoke('vacuum.set_pump', on=False)
        h.step(80.0)
        h.invoke('chamber.set_door', open=True)
    if h.holder is not None or h.holder_broken:
        h.invoke('sample.unload')
    h.invoke('chamber.set_door', open=False)
    h.invoke('vacuum.set_pump', on=True)
    h.step(pump_s)
    return h


def calibrated(seed=1, collimation='line'):
    """Ready instrument with the zero angle and the SDD calibrated on silver behenate (paste cell)."""
    h = ready(seed)
    if collimation != 'line':
        h.invoke('collimation.set_mode', mode=collimation)
    solid(h, 'agbe', 'silver_behenate', 1e-3, form='powder')
    load(h, 'agbe', holder='paste_cell')
    r = acquire(h, 60.0 if collimation == 'line' else 600.0)
    h.invoke('calibration.zero_angle', record_id=r)
    h.invoke('calibration.q_scale', record_id=r, calibrant='silver_behenate')
    return h


def water_calibration(h, temp=293.15, exposure=600.0):
    liquid(h, 'water_std')
    load(h, 'water_std', temp=temp)
    rw = acquire(h, exposure)
    load(h, None, temp=temp)
    re_ = acquire(h, exposure)
    return h.invoke('calibration.absolute', record_id=rw, background_record_id=re_, standard='water', thickness_m=1e-3)['calibration_id']


def rejected(h, cap, code, **a):
    before = h.read('evaluator')
    rev = h.revision
    try:
        h.invoke(cap, **a)
    except rt.ActionRejected as exc:
        assert str(exc) == code, (cap, str(exc), code)
        assert json.dumps(h.read('evaluator'), sort_keys=True, default=str) == json.dumps(before, sort_keys=True, default=str), cap
        assert h.revision == rev, cap
        return True
    raise AssertionError(f'{cap} was not rejected (expected {code})')


def reduced_arrays(r):
    return np.asarray(r['q_per_m']) * 1e-9, np.asarray(r['intensity']) / 100.0, np.asarray(r['sigma']) / 100.0      # 1/nm, 1/cm


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.antonpaar_saxspace')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-153')

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
        queries = {k for k, v in rt.CAPS.items() if v['kind'] in ('query', 'analysis')}
        self.assertEqual(set(rt.EFFECTS) | set(rt.UNSUPPORTED) | queries, set(rt.CAPS))
        self.assertEqual(set(rt.CAP_SOURCES), set(rt.CAPS))
        comps = {p['id'] for p in json.loads((rt.ASSET / 'geometry_manifest.json').read_text())['components']}
        for k, c in meta['capabilities'].items():
            self.assertLessEqual(set(c['component_ids']) - {'monitor'}, comps, k)
        for k, s in meta['state_slots'].items():
            self.assertLessEqual(set(s['component_ids']) - {'monitor'}, comps, k)

    def test_every_rejection_code_is_declared(self):
        """Each ActionRejected code raised by a command method appears in that capability's declared errors."""
        import inspect
        import re
        src = inspect.getsource(rt.AntonpaarSaxspaceRuntime)
        meta = _meta()['capabilities']
        helpers = {'_need_plant': ['PLANT_REQUIRED'], '_need_power': ['PLANT_REQUIRED', 'POWER_OFF'], '_need_gen_power': ['PLANT_REQUIRED', 'POWER_OFF'],
                   '_need_ready': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING'], '_need_idle': ['ACQUISITION_RUNNING'],
                   '_need_door_open': ['DOOR_CLOSED'], '_need_stage': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING'],
                   '_need_beam_ready': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING', 'DOOR_OPEN', 'XRAY_NOT_READY',
                                        'VACUUM_NOT_READY', 'MOTION_IN_PROGRESS', 'RECORDS_FULL'],
                   '_need_asx': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ASX_DOOR_OPEN', 'ASX_BUSY', 'ACQUISITION_RUNNING'],
                   '_rec(': ['UNKNOWN_RECORD', 'RECORD_NOT_COMPLETED'], '_norm(': ['NO_PRIMARY_BEAM'], '_red(': ['UNKNOWN_REDUCED'],
                   '_cmd_analysis_guinier(': ['UNKNOWN_REDUCED', 'GUINIER_FAILED']}
        for k in rt.CAPS:
            if k in rt.UNSUPPORTED:
                continue
            m = re.search(r'    def _cmd_' + k.replace('.', '_') + r'\(.*?(?=\n    def |\Z)', src, re.S)
            self.assertIsNotNone(m, k)
            body = m.group(0)
            codes = set(re.findall(r"ActionRejected\('([A-Z_]+)'\)", body))
            for hname, hcodes in helpers.items():
                if hname in body:
                    codes |= set(hcodes)
            codes.discard('INVALID_ARGUMENT')
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
        h = ready()
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
            h = ready(seed=5)
            h.rng = QuietRng()
            liquid(h, 'lys', [(LYS, 5.0)])
            load(h, 'lys', temp=283.15)
            h.invoke('acquisition.swaxs_scan', arm_angles_rad=[0.0, 0.3], exposure_s=30.0)
            h.invoke('asx.set_door', open=True)
            h.invoke('asx.set_door', open=False)
            for dt in chunks:
                h.step(dt)
            rec = h.records['sx-%d' % h.seq['sx']]
            return h.read('evaluator'), h.ledger(), rec
        (a, la, ra), (b, lb, rb) = run([90.0]), run([0.37] * 243 + [0.09])
        for k in ('truth.pressure_pa', 'truth.stage_temperature_k', 'detector.arm_angle_rad', 'stage.y_m', 'clock.time_s'):
            self.assertAlmostEqual(a[k], b[k], delta=1e-9 * max(abs(a[k]), 1e-12), msg=k)
        self.assertEqual(ra['status'], rb['status'])
        self.assertEqual(len(ra['segments']), len(rb['segments']))
        for k in ('photons_incident', 'photons_scattered_detected', 'tube_energy_j'):
            self.assertAlmostEqual(la[k], lb[k], delta=1e-9 * max(la[k], 1.0), msg=k)

    def test_reads_do_not_change_later_counts(self):
        """Reading (agent and evaluator, incl. detector.total_counts that evaluates the rate model) during an exposure leaves the record and the
        ledgers bit-identical to a run without reads (the rate cache holds values that depend on its quantised key only)."""
        h = ready(seed=4)
        liquid(h, 'w')
        load(h, 'w')
        h.invoke('acquisition.start', exposure_s=30.0, frames=1)
        a, b = copy.deepcopy(h), copy.deepcopy(h)
        for _ in range(70):
            a.step(0.5)
            a.read('agent')
            a.read('evaluator')
            b.step(0.5)
        ra, rb = a.records['sx-1'], b.records['sx-1']
        self.assertEqual(ra['status'], 'completed')
        self.assertEqual(ra['primary_beam_counts'], rb['primary_beam_counts'])
        self.assertEqual(ra['segments'][0]['counts'], rb['segments'][0]['counts'])
        self.assertEqual(a.ledger(), b.ledger())

    def test_set_points_only_write_targets(self):
        h = ready()
        h.invoke('detector.move_arm', angle_rad=0.5)
        self.assertEqual(h.arm, 0.0)
        h.step(1.0)
        self.assertAlmostEqual(h.arm, rt.ARM_SPEED_RAD_S, places=12)
        h.invoke('stage.set_temperature', temperature_k=333.15)
        t0 = h.t_stage
        self.assertEqual(h.t_set, 333.15)
        h.step(60.0)
        self.assertAlmostEqual(h.t_stage - t0, rt.HOT_STAGE_RAMP_K_S * 60.0, places=6)          # 10 C/min while far from the set point
        h.invoke('generator.set_power', voltage_v=30000.0, current_a=0.02)
        self.assertEqual(h.kv, 40000.0)
        h.step(2.0)
        self.assertAlmostEqual(h.kv, 40000.0 - 2.0 * rt.HV_RAMP_V_S, places=6)


class GeneratorAndInterlocks(unittest.TestCase):
    def test_start_up_key_ramp_and_lamps(self):
        h = configured()
        rejected(h, 'generator.xray_on', 'POWER_OFF')
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S - 1.0)
        self.assertEqual(h.state, 'booting')
        rejected(h, 'generator.xray_on', 'NOT_READY')
        h.step(2.0)
        self.assertEqual(h.state, 'ready')
        rejected(h, 'generator.xray_on', 'KEY_OFF')
        h.invoke('generator.set_key', on=True)
        self.assertTrue(h.read()['panel.xray_off_lamp'])
        h.invoke('generator.xray_on')
        self.assertTrue(h.read()['panel.warning_lamp'])
        h.step(10.0)
        self.assertAlmostEqual(h.kv, 20000.0, places=6)
        self.assertAlmostEqual(h.ma, 0.05, places=9)
        h.step(11.0)
        self.assertEqual(h.read()['generator.voltage_indicated_v'], 40000.0)
        self.assertAlmostEqual(h.read()['generator.current_indicated_a'], 0.05, places=9)
        h.invoke('generator.set_key', on=False)
        self.assertFalse(h.xray_on)
        self.assertEqual(h.kv, 0.0)

    def test_chiller_needs_30_s_and_its_failure_trips_the_tube(self):
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(20.0)
        self.assertFalse(h.read()['chiller.flow_ok'])
        h.step(45.0)
        h.invoke('generator.set_key', on=True)
        h.invoke('generator.xray_on')
        h.step(25.0)
        h.invoke('fault.inject', fault='chiller_failure', active=True)
        h.step(1.0)
        r = h.read()
        self.assertFalse(r['generator.xray_on'])
        self.assertEqual(r['generator.fault_code'], 'COOLING_WATER_LOW')
        self.assertTrue(r['panel.standby_lamp'])
        rejected(h, 'generator.xray_on', 'GENERATOR_FAULT')
        rejected(h, 'generator.acknowledge', 'COOLING_WATER_LOW')
        h.invoke('fault.inject', fault='chiller_failure', active=False)
        h.invoke('generator.acknowledge')
        h.invoke('generator.xray_on')
        self.assertTrue(h.xray_on)

    def test_emergency_stop_fails_the_acquisition_and_latches(self):
        h = ready()
        liquid(h, 'w')
        load(h, 'w')
        op = h.invoke('acquisition.start', exposure_s=100.0, frames=1)['operation_id']
        h.step(10.0)
        h.invoke('generator.emergency_stop')
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['error_code']), ('failed', 'XRAY_OFF'))
        self.assertEqual(rec['segments'], [])
        self.assertFalse(h.read()['shutter.open'])
        rejected(h, 'generator.xray_on', 'EMERGENCY_STOP')
        rejected(h, 'generator.acknowledge', 'EMERGENCY_STOP')
        h.invoke('generator.release_emergency_stop')
        h.invoke('generator.acknowledge')
        h.invoke('generator.xray_on')
        self.assertTrue(h.xray_on)

    def test_mains_fault_and_power_cycle(self):
        h = ready()
        h.invoke('plant.configure', room_k=295.15, mains_voltage_v=180.0, noise_seed=9)
        r = h.read()
        self.assertFalse(r['power.enabled'])
        self.assertEqual(r['system.error_code'], 'MAINS_FAULT')
        rejected(h, 'power.set', 'MAINS_OUT_OF_RANGE', enabled=True)
        h.invoke('plant.configure', room_k=295.15, mains_voltage_v=230.0, noise_seed=9)
        h.invoke('power.set', enabled=True)
        self.assertIsNone(h.read()['system.error_code'])


class VacuumDoorAndStage(unittest.TestCase):
    def test_pump_down_below_3_mbar_and_door_interlock(self):
        h = ready()
        liquid(h, 'w')
        h.invoke('chamber.set_door', open=True)
        h.invoke('sample.load', sample_id='w', holder='capillary_1mm')
        rejected(h, 'vacuum.set_pump', 'DOOR_OPEN', on=True)
        h.invoke('chamber.set_door', open=False)
        h.invoke('vacuum.set_pump', on=True)
        rejected(h, 'chamber.set_door', 'PUMP_ON', open=True)
        h.step(20.0)
        rejected(h, 'chamber.set_door', 'CHAMBER_UNDER_VACUUM', open=True)
        t_ready = rt.V_VAC_M3 / rt.S_PUMP_M3_S * math.log((rt.P_ATM - rt.P_ULT_PA) / (rt.VACUUM_READY_PA - rt.P_ULT_PA))
        h.step(t_ready - 25.0)
        self.assertFalse(h.read()['vacuum.ready'])
        rejected(h, 'acquisition.start', 'VACUUM_NOT_READY', exposure_s=10.0, frames=1)
        h.step(10.0)
        self.assertTrue(h.read()['vacuum.ready'])
        self.assertLess(h.p, rt.VACUUM_READY_PA)
        h.invoke('vacuum.set_pump', on=False)
        h.step(60.0)
        self.assertGreater(h.p, rt.DOOR_OPEN_MIN_PA)
        h.invoke('chamber.set_door', open=True)

    def test_air_measurement_is_allowed_but_air_scatters(self):
        h = ready()
        liquid(h, 'w')
        load(h, 'w', align=False)
        r_vac = h.records[acquire(h, 60.0)]
        h.invoke('vacuum.set_pump', on=False)
        h.step(90.0)
        r_air = h.records[acquire(h, 60.0)]
        mid = slice(200, 400)
        self.assertGreater(sum(r_air['segments'][0]['counts'][mid]), 1.5 * sum(r_vac['segments'][0]['counts'][mid]))
        self.assertLess(r_air['primary_beam_counts'], r_vac['primary_beam_counts'])

    def test_stage_temperature_range_ramp_and_mount(self):
        h = ready()
        rejected(h, 'stage.set_temperature', 'TEMPERATURE_OUT_OF_RANGE', temperature_k=473.15)
        h.invoke('stage.set_temperature', temperature_k=253.15)
        h.step(600.0)
        self.assertAlmostEqual(h.t_stage, 253.15, delta=rt.TEMP_ACCURACY_K)
        h.invoke('chamber.set_door', open=True)
        rejected(h, 'stage.mount', 'STAGE_NOT_AMBIENT', stage_type='tcstage_extended')
        h.invoke('stage.set_temperature', temperature_k=295.15)
        h.step(600.0)
        h.invoke('stage.mount', stage_type='tcstage_extended')
        h.invoke('stage.set_temperature', temperature_k=773.15)
        h.step(60.0)
        self.assertAlmostEqual(h.t_stage, 295.15 + 10.0, delta=0.01)
        self.assertEqual(h.read()['stage.type'], 'tcstage_extended')


class SamplesAndHolders(unittest.TestCase):
    def test_sample_validation(self):
        h = configured()
        bad = [dict(form='solution', solvent='none'), dict(form='solid', thickness_m=None), dict(form='powder', components=[dict(template_id=LYS, concentration_kg_m3=5.0,
                                                                                                                                model_parameters=None)])]
        base = dict(sample_id='x', form='solution', solvent='water', volume_m3=1e-7, thickness_m=None, components=[])
        for b in bad:
            a = dict(base, **b)
            if a['form'] != 'solution':
                a['solvent'], a['volume_m3'] = 'none', 0.0
                if 'thickness_m' not in b:
                    a['thickness_m'] = 1e-3
            try:
                h.invoke('sample.provide', **a)
                raise AssertionError(b)
            except rt.ActionRejected as exc:
                self.assertIn(str(exc), ('SAMPLE_FORM_INVALID', 'CONCENTRATION_REQUIRED'), b)
        rejected(h, 'sample.provide', 'CONCENTRATION_REQUIRED', **dict(base, components=[dict(template_id=LYS, concentration_kg_m3=None, model_parameters=None)]))
        rejected(h, 'sample.provide', 'UNKNOWN_TEMPLATE', **dict(base, components=[dict(template_id='nope', concentration_kg_m3=1.0, model_parameters=None)]))
        rejected(h, 'sample.provide', 'SAMPLE_FORM_INVALID', **dict(base, form='solid', solvent='none', volume_m3=0.0, thickness_m=2e-3,
                                                                    components=[dict(template_id=GC, concentration_kg_m3=None, model_parameters=None)]))
        rejected(h, 'sample.provide', 'MODEL_PARAMETERS_INVALID', **dict(base, form='solid', solvent='none', volume_m3=0.0, thickness_m=1e-3,
                                                                         components=[dict(template_id='model_lamellar', concentration_kg_m3=None,
                                                                                          model_parameters=dict(d_spacing_m=1e-6))]))

    def test_load_volumes_holders_and_empty_capillary(self):
        h = ready(xray=False)
        liquid(h, 'lys', [(LYS, 5.0)], volume=100e-9)
        solid(h, 'gc', GC, 1.055e-3)
        rejected(h, 'sample.load', 'DOOR_CLOSED', sample_id='lys', holder='capillary_1mm')
        h.invoke('chamber.set_door', open=True)
        rejected(h, 'sample.load', 'HOLDER_UNSUITABLE', sample_id='lys', holder='solid_holder')
        rejected(h, 'sample.load', 'HOLDER_UNSUITABLE', sample_id='gc', holder='capillary_1mm')
        h.invoke('sample.load', sample_id='lys', holder='capillary_1mm')
        self.assertAlmostEqual(h.samples['lys']['bench_volume_m3'], 40e-9, places=18)
        rejected(h, 'sample.load', 'HOLDER_OCCUPIED', sample_id='gc', holder='solid_holder')
        h.invoke('sample.unload')
        rejected(h, 'sample.load', 'INSUFFICIENT_VOLUME', sample_id='lys', holder='capillary_1mm')
        h.invoke('sample.load', sample_id='lys', holder='mu_cell')
        h.invoke('sample.unload')
        h.invoke('sample.load', sample_id=None, holder='capillary_1mm')
        self.assertIsNone(h.read()['holder.sample_id'])
        self.assertEqual(h.read()['holder.type'], 'capillary_1mm')

    def test_truefocus_finds_the_capillary_axis(self):
        h = ready(seed=4)
        liquid(h, 'w')
        load(h, 'w', align=False)
        off = h.cap_offset
        self.assertGreater(abs(off), 0.05e-3)
        g0 = h._geometry()
        h.invoke('stage.auto_align')
        h.step(rt.ALIGN_S + 1.0)
        h.step(2.0)
        self.assertAlmostEqual(h.stage['y'], off, delta=4e-5)
        g1 = h._geometry()
        self.assertGreater(g1['scat_path_m'], g0['scat_path_m'])
        self.assertLess(g1['t_total'], g0['t_total'])
        h.invoke('stage.move', y_m=5e-3, z_m=0.0)
        h.step(6.0)
        self.assertEqual(h._geometry()['pos'], 'between')
        h.invoke('stage.auto_align')
        h.step(rt.ALIGN_S + 1.0)
        op = [o for o in h.operations.values() if o['kind'] == 'align'][-1]
        self.assertEqual(op.get('reason'), 'NO_CAPILLARY_FOUND')


class ScatteringPhysics(unittest.TestCase):
    def test_water_scale_reproduces_the_nist_glassy_carbon(self):
        """Absolute scale from water (compressibility) applied to a measured SRM 3600 coupon reproduces the certified curve (line collimation, desmeared)."""
        h = calibrated(seed=2)
        cal = water_calibration(h)
        solid(h, 'gc', GC, 1.055e-3)
        load(h, 'gc', holder='solid_holder')
        rg = acquire(h, 300.0)
        empty_beam(h)
        rb = acquire(h, 300.0)
        red = h.invoke('data.reduce', record_id=rg, background_record_id=rb, calibration_id=cal, thickness_m=1.055e-3, desmear=True)
        q, i, s = reduced_arrays(red)
        t = rt.template(GC)
        m = (q > 0.1) & (q < 2.0)
        ratio = i[m] / rt.srm3600_cm(t, q[m])
        self.assertAlmostEqual(float(np.median(ratio)), 1.0, delta=0.08)
        CHECKS.append(dict(id='water_scale_vs_srm3600', status='passed', median_ratio=float(np.median(ratio))))

    def test_lysozyme_rg_and_molecular_weight(self):
        h = calibrated(seed=3)
        cal = water_calibration(h)
        liquid(h, 'buf')
        liquid(h, 'lys', [(LYS, 5.0)])
        load(h, 'buf', temp=283.15)
        rb = acquire(h, 900.0)
        load(h, 'lys', temp=283.15)
        rl = acquire(h, 900.0)
        red = h.invoke('data.reduce', record_id=rl, background_record_id=rb, calibration_id=cal, thickness_m=1e-3, desmear=True)
        g = h.invoke('analysis.guinier', reduced_id=red['reduced_id'], qrg_max=1.3)
        rg_true = rt.template(LYS)['rg_template_nm'] * 1e-9
        self.assertAlmostEqual(g['rg_m'], rg_true, delta=max(3 * g['rg_sigma_m'], 0.06 * rg_true))
        mw = h.invoke('analysis.molecular_weight', reduced_id=red['reduced_id'], concentration_kg_m3=5.0, drho_m_m_kg=rt.DRHO_M_PROTEIN_M_KG)
        self.assertAlmostEqual(mw['molecular_weight_kg_mol'], 14.3, delta=0.15 * 14.3)
        smeared = h.invoke('data.reduce', record_id=rl, background_record_id=rb, calibration_id=cal, thickness_m=1e-3, desmear=False)
        g2 = h.invoke('analysis.guinier', reduced_id=smeared['reduced_id'], qrg_max=1.3)
        self.assertLess(g2['i0'], 0.85 * g['i0'])                              # line-collimation data must be desmeared for I(0)
        CHECKS.append(dict(id='lysozyme_rg_mw', status='passed', rg_nm=g['rg_m'] * 1e9, mw_kda=mw['molecular_weight_kg_mol']))

    def test_concentration_effect_and_template_scale(self):
        t = rt.template(BSA)
        q = np.array([0.02, 0.1, 0.3])
        lo = rt.component_cm(dict(kind='curve', template_id=BSA, concentration_mg_ml=2.0), q, 293.15, 0.0) / 2.0
        hi = rt.component_cm(dict(kind='curve', template_id=BSA, concentration_mg_ml=50.0), q, 293.15, 0.0) / 50.0
        self.assertLess(hi[0], 0.8 * lo[0])                                    # repulsive structure factor lowers I(0)/c at 50 mg/mL
        self.assertAlmostEqual(lo[0] / t['_i0c'], 1.0, delta=0.02)
        self.assertAlmostEqual(t['_i0c'] / (t['abs_scale']['mass_da'] * 1e-3), 0.00080, delta=0.00008)     # I(0)/c per kDa of a protein

    def test_silver_behenate_orders_and_q_calibration(self):
        h = calibrated(seed=6, collimation='point')
        self.assertAlmostEqual(h.sdd_cal, h.sdd_true, delta=0.002 * h.sdd_true)
        self.assertAlmostEqual(h.zero_cal, h.zero_true, delta=2.5e-5)
        red = h.invoke('data.reduce', record_id='sx-1', background_record_id=None, calibration_id=None, thickness_m=1e-3, desmear=False)
        peaks = [p['q_per_m'] * 1e-9 for p in h.invoke('analysis.peaks', reduced_id=red['reduced_id'])['peaks']]
        for n in (1, 2, 3):
            qn = 2 * math.pi * n / 5.838
            self.assertTrue(any(abs(p - qn) < 0.01 * qn + 0.02 for p in peaks), (n, peaks))

    def test_silicon_111_ka_doublet_in_waxs(self):
        h = ready(seed=7)
        h.invoke('collimation.set_mode', mode='point')
        solid(h, 'si', 'silicon_powder', 0.3e-3, form='powder')
        load(h, 'si', holder='paste_cell')
        tt111 = 2 * math.asin(rt.mean_wavelength_m() / (2 * 0.3135601e-9))
        arm = tt111 - math.atan(0.030 / rt.SDD_NOMINAL_M)
        op = h.invoke('acquisition.swaxs_scan', arm_angles_rad=[0.0, arm], exposure_s=60.0)['operation_id']
        h.step(200.0)
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual(rec['status'], 'completed')
        seg = rec['segments'][1]
        c = np.asarray(seg['counts'], float)
        tt = np.asarray(seg['two_theta_rad'])
        l1, l2, _ = rt.ka_lines()
        t1 = 2 * math.asin(l1 / (2 * 0.3135601e-9))
        t2 = 2 * math.asin(l2 / (2 * 0.3135601e-9))
        j1 = int(np.argmax(np.where(np.abs(tt - t1) < math.radians(0.03), c, 0)))
        j2 = int(np.argmax(np.where(np.abs(tt - t2) < math.radians(0.03), c, 0)))
        mid = int((j1 + j2) / 2)
        self.assertGreater(c[j1], c[j2])                                       # Ka1 : Ka2 = 2 : 1
        self.assertLess(c[mid], 0.95 * c[j2])                                  # the doublet is resolved (0.07 deg apart)
        self.assertGreater(abs(j2 - j1), 3)

    def test_gold_rm8012_guinier_radius(self):
        t = rt.template(AU30)
        q = np.geomspace(0.02, 0.2, 200)
        i = rt.component_cm(dict(kind='sphere_model', template_id=AU30, concentration_mg_ml=0.048), q, 293.15, 0.0)
        m = q < 0.08
        a, b = np.polyfit(q[m] ** 2, np.log(i[m]), 1)
        rg = math.sqrt(-3 * a)
        r = t['mean_diameter_nm'] / 2
        z = 1 / t['schulz_rel_sigma'] ** 2 - 1
        mom = lambda k: math.exp(math.lgamma(z + 1 + k) - math.lgamma(z + 1)) / (z + 1) ** k * r ** k  # noqa: E731
        rg_expected = math.sqrt(0.6 * mom(8) / mom(6))
        self.assertAlmostEqual(rg, rg_expected, delta=0.05 * rg_expected)

    def test_swaxs_reaches_60_degrees_and_the_water_halo(self):
        h = ready(seed=8)
        liquid(h, 'w')
        load(h, 'w')
        op = h.invoke('acquisition.swaxs_scan', arm_angles_rad=[0.0, 0.2, 0.4, 0.6, rt.ARM_MAX_RAD], exposure_s=60.0)['operation_id']
        h.step(600.0)
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual(rec['status'], 'completed')
        tt_max = max(max(s['two_theta_rad']) for s in rec['segments'])
        q_max = max(max(s['q_per_m']) for s in rec['segments'])
        self.assertAlmostEqual(math.degrees(tt_max), 60.0, delta=0.6)
        self.assertGreater(q_max, 40.0e9)
        red = h.invoke('data.reduce', record_id=rec['record_id'], background_record_id=None, calibration_id=None, thickness_m=1e-3, desmear=False)
        q, i, _ = reduced_arrays(red)
        self.assertGreater(np.interp(20.0, q, i), 1.8 * np.interp(8.0, q, i))    # water structure peak near 20 nm^-1

    def test_q_min_and_resolution(self):
        h = calibrated(seed=9)
        liquid(h, 'w')
        load(h, 'w')
        r = acquire(h, 60.0)
        red = h.invoke('data.reduce', record_id=r, background_record_id=None, calibration_id=None, thickness_m=1e-3, desmear=False)
        self.assertLess(red['q_min_valid_per_m'], 0.035e9)
        self.assertGreater(red['q_min_valid_per_m'], 0.015e9)


class FaultsAndStates(unittest.TestCase):
    def test_vacuum_leak_keeps_the_path_above_3_mbar(self):
        h = ready()
        h.invoke('fault.inject', fault='vacuum_leak', active=True)
        liquid(h, 'w')
        load(h, 'w', align=False, pump_s=600.0)
        self.assertFalse(h.read()['vacuum.ready'])
        rejected(h, 'acquisition.start', 'VACUUM_NOT_READY', exposure_s=10.0, frames=1)

    def test_aged_tube_and_misaligned_beamstop(self):
        h = ready(seed=2)
        liquid(h, 'w')
        load(h, 'w')
        p0 = h.records[acquire(h, 30.0)]['primary_beam_counts']
        h.invoke('fault.inject', fault='tube_aged', active=True)
        p1 = h.records[acquire(h, 30.0)]['primary_beam_counts']
        self.assertAlmostEqual(p1 / p0, 0.6, delta=0.02)
        h.invoke('fault.inject', fault='beamstop_misaligned', active=True)
        op = h.invoke('acquisition.start', exposure_s=30.0, frames=1)['operation_id']
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['error_code']), ('failed', 'DETECTOR_OVEREXPOSURE'))
        self.assertFalse(h.read()['shutter.open'])

    def test_capillary_bubble_lowers_the_signal(self):
        h = ready(seed=2)
        liquid(h, 'w')
        load(h, 'w')
        g0 = h._geometry()
        h.invoke('fault.inject', fault='capillary_bubble', active=True)
        g1 = h._geometry()
        self.assertLess(g1['scat_path_m'], 0.8 * g0['scat_path_m'])            # path x transmission: 0.4 d exp(-0.4 mu d) against d exp(-mu d)
        self.assertGreater(g1['t_total'], g0['t_total'])

    def test_freezing_boiling_burst_and_aggregation(self):
        h = ready(seed=3)
        liquid(h, 'lys', [(LYS, 5.0)])
        load(h, 'lys', temp=258.15, pump_s=300.0)
        self.assertTrue(h.content['frozen'])
        h.invoke('stage.set_temperature', temperature_k=343.15)
        h.step(700.0)
        self.assertFalse(h.content['frozen'])
        a = h.content['agg'][LYS]
        self.assertGreater(a, 0.1)
        led0 = h.ledger()
        h.invoke('stage.set_temperature', temperature_k=373.15)
        h.step(400.0)
        r = h.read('evaluator')
        self.assertTrue(r['holder.broken'])
        self.assertEqual(r['system.error_code'], 'SAMPLE_BURST')
        led = h.ledger()
        self.assertAlmostEqual(led['liquid_residual_m3'], 0.0, delta=1e-18)
        self.assertAlmostEqual(led['solute_residual_kg'], 0.0, delta=1e-18)
        self.assertGreater(led['liquid_waste_m3'], led0['liquid_waste_m3'])
        rejected(h, 'system.acknowledge', 'HOLDER_BROKEN')

    def test_aggregation_raises_the_apparent_rg(self):
        q = np.geomspace(0.02, 0.5, 100)
        base = dict(kind='curve', template_id=LYS, concentration_mg_ml=5.0)
        i0 = rt.component_cm(dict(base, aggregated_fraction=0.0), q, 293.15, 0.0)
        i1 = rt.component_cm(dict(base, aggregated_fraction=0.2), q, 293.15, 0.0)
        self.assertGreater(i1[0], 5 * i0[0])


class Autosampler(unittest.TestCase):
    def test_injection_carry_over_cleaning_and_conservation(self):
        h = ready(seed=2)
        liquid(h, 'bsa', [(BSA, 10.0)], volume=200e-9)
        liquid(h, 'buf', volume=300e-9)
        rejected(h, 'asx.load_well', 'ASX_DOOR_CLOSED', sample_id='bsa', well='AA01', volume_m3=100e-9)
        h.invoke('asx.set_door', open=True)
        h.invoke('asx.load_well', sample_id='bsa', well='AA01', volume_m3=100e-9)
        h.invoke('asx.load_well', sample_id='buf', well='AA02', volume_m3=100e-9)
        rejected(h, 'asx.inject', 'ASX_DOOR_OPEN', well='AA01')
        h.invoke('asx.set_door', open=False)
        h.invoke('asx.inject', well='AA01')
        self.assertEqual(h.read()['asx.busy'], 'inject')
        h.step(60.0)
        self.assertIsNone(h.read()['asx.busy'])
        self.assertAlmostEqual(h.flow_cell['conc'][BSA], 10.0, places=9)
        h.invoke('asx.inject', well='AA02')
        h.step(60.0)
        self.assertAlmostEqual(h.flow_cell['conc'][BSA], 10.0 * rt.ASX_CARRY_OVER, places=9)
        h.invoke('asx.clean')
        h.step(150.0)
        self.assertEqual(h.read()['asx.flow_cell_state'], 'rinsed')
        h.invoke('asx.inject', well='AA02')
        h.step(60.0)
        self.assertAlmostEqual(h.flow_cell['conc'][BSA], 10.0 * rt.ASX_CARRY_OVER * rt.ASX_CARRY_OVER_CLEAN, delta=1e-12)
        led = h.ledger()
        self.assertAlmostEqual(led['liquid_residual_m3'], 0.0, delta=1e-18)
        self.assertAlmostEqual(led['solute_residual_kg'], 0.0, delta=1e-18)

    def test_flow_cell_measurement_and_cancel(self):
        h = ready(seed=2)
        liquid(h, 'bsa', [(BSA, 10.0)], volume=200e-9)
        h.invoke('asx.set_door', open=True)
        h.invoke('asx.load_well', sample_id='bsa', well='BA01', volume_m3=100e-9)
        h.invoke('asx.set_door', open=False)
        h.register_principal('e', 'evaluator')
        s = h.session('e')
        op = s.invoke(instance_id=h.instance_id, capability_id='asx.inject', arguments=dict(well='BA01'), request_id='i1')['operation_id']
        h.step(12.0)
        s.cancel(op, 'c1')
        self.assertEqual(h.operations[op]['phase'], 'cancelled')
        led = h.ledger()
        self.assertAlmostEqual(led['liquid_residual_m3'], 0.0, delta=1e-18)
        h.invoke('asx.inject', well='BA01')
        h.step(60.0)
        h.invoke('stage.select_position', position='flow_cell')
        h.step(10.0)
        self.assertEqual(h._beam_content()[0], 'flow_cell')
        h.invoke('vacuum.set_pump', on=True)
        h.step(220.0)
        r = h.records[acquire(h, 60.0)]
        self.assertEqual(r['sample_label'], 'BA01')
        self.assertEqual(r['sample_position'], 'flow_cell')


class LedgersIsolationSessions(unittest.TestCase):
    def test_photon_ledger_closes_and_scattering_comes_from_attenuation(self):
        h = ready(seed=2)
        liquid(h, 'lys', [(LYS, 5.0)])
        load(h, 'lys')
        acquire(h, 120.0)
        led = h.ledger()
        self.assertAlmostEqual(led['photons_residual'], 0.0, delta=1e-6 * led['photons_incident'])
        self.assertLess(led['photons_scattered_detected'], led['photons_attenuated_sample'] + led['photons_attenuated_windows'] + led['photons_attenuated_air'])
        self.assertAlmostEqual(led['tube_on_time_s'], h.time_s - 61.0, delta=1e-6)
        self.assertGreater(led['tube_energy_j'], 0.9 * 40000.0 * 0.05 * (h.time_s - 61.0 - 20.0))

    def test_two_instances_are_isolated(self):
        a, b = ready(seed=1), ready(seed=1)
        a.invoke('detector.move_arm', angle_rad=0.3)
        a.step(10.0)
        self.assertEqual(b.arm, 0.0)
        self.assertNotEqual(a.arm, b.arm)

    def test_agent_does_not_see_truth(self):
        h = ready()
        liquid(h, 'lys', [(LYS, 5.0)])
        load(h, 'lys')
        rid = acquire(h, 30.0)
        h.register_principal('a', 'agent')
        s = h.session('a')
        obs = s.read(instance_id=h.instance_id)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        r = s.invoke(instance_id=h.instance_id, capability_id='acquisition.result', arguments=dict(record_id=rid), request_id='r1')
        text = json.dumps(r)
        self.assertNotIn('lysozyme', text.lower())
        self.assertNotIn(LYS, text)
        r = s.invoke(instance_id=h.instance_id, capability_id='provenance.read', arguments={}, request_id='r2')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = s.describe(instance_id=h.instance_id)
        self.assertFalse([k for k in d['state_slots'] if k.startswith('truth.')])

    def test_records_and_control_belong_to_their_agent(self):
        h = ready()
        liquid(h, 'w')
        load(h, 'w')
        for p in ('a', 'b'):
            h.register_principal(p, 'agent')
        sa, sb = h.session('a'), h.session('b')
        r = sa.invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments=dict(exposure_s=30.0, frames=1), request_id='x1')
        self.assertEqual(r['status'], 'accepted')
        rb = sb.invoke(instance_id=h.instance_id, capability_id='detector.move_arm', arguments=dict(angle_rad=0.2), request_id='y1')
        self.assertEqual(rb['error']['code'], 'ACCESS_DENIED')
        self.assertTrue(sb.read(instance_id=h.instance_id)['observations']['acquisition.started_by_other_agent']['value'])
        h.step(40.0)
        rid = h.operations[r['operation_id']]['record_id']
        self.assertEqual(sb.invoke(instance_id=h.instance_id, capability_id='acquisition.result', arguments=dict(record_id=rid), request_id='y2')['error']['code'],
                         'ACCESS_DENIED')
        self.assertEqual(sb.invoke(instance_id=h.instance_id, capability_id='data.reduce', arguments=dict(record_id=rid, background_record_id=None, calibration_id=None,
                                                                                                          thickness_m=1e-3, desmear=False), request_id='y3')['error']['code'],
                         'ACCESS_DENIED')
        self.assertEqual(sa.read(instance_id=h.instance_id)['observations']['result.last_record_id']['value'], rid)
        again = sa.invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments=dict(exposure_s=30.0, frames=1), request_id='x1')
        self.assertEqual(again['operation_id'], r['operation_id'])
        conflict = sa.invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments=dict(exposure_s=31.0, frames=1), request_id='x1')
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')

    def test_cancel_keeps_finished_segments_and_power_off_fails(self):
        h = ready()
        liquid(h, 'w')
        load(h, 'w')
        h.register_principal('a', 'agent')
        s = h.session('a')
        r = s.invoke(instance_id=h.instance_id, capability_id='acquisition.swaxs_scan', arguments=dict(arm_angles_rad=[0.0, 0.4], exposure_s=30.0), request_id='s1')
        h.step(45.0)
        s.cancel(r['operation_id'], 'c1')
        rec = h.records[h.operations[r['operation_id']]['record_id']]
        self.assertEqual((rec['status'], rec['error_code'], len(rec['segments'])), ('cancelled', 'USER_CANCEL', 1))
        self.assertEqual(h.arm_target, h.arm)
        op = h.invoke('acquisition.start', exposure_s=60.0, frames=1)
        h.step(5.0)
        h.invoke('power.set', enabled=False)
        rec = h.records[h.operations[op['operation_id']]['record_id']]
        self.assertEqual((rec['status'], rec['error_code']), ('failed', 'POWER_OFF'))
        led = h.ledger()
        self.assertAlmostEqual(led['photons_residual'], 0.0, delta=1e-6 * led['photons_incident'])

    def test_tray_well_xray_off_result_and_ledger_calls(self):
        """Capabilities otherwise reached only through helpers: tray temperature, well removal (liquid to waste), X-RAY OFF, data.result and
        ledger.read, each through invoke with its declared output."""
        h = ready(seed=11)
        liquid(h, 'w', volume=300e-9)
        h.invoke('asx.set_tray_temperature', temperature_k=277.15)
        h.step(1500.0)
        self.assertAlmostEqual(h.read('evaluator')['truth.tray_temperature_k'], 277.15, delta=0.2)
        h.invoke('asx.set_door', open=True)
        h.invoke('asx.load_well', sample_id='w', well='AB03', volume_m3=100e-9)
        waste0 = h.invoke('ledger.read')['liquid_waste_m3']
        h.invoke('asx.unload_well', well='AB03')
        led = h.invoke('ledger.read')
        self.assertAlmostEqual(led['liquid_waste_m3'] - waste0, 100e-9, delta=1e-15)
        self.assertLessEqual(abs(led['liquid_residual_m3']), 1e-18)
        self.assertEqual(h.read('evaluator')['asx.wells'], {})
        h.invoke('asx.set_door', open=False)
        load(h, 'w')
        rid = acquire(h, 30.0)
        red = h.invoke('data.reduce', record_id=rid, background_record_id=None, calibration_id=None, thickness_m=1e-3, desmear=False)
        again = h.invoke('data.result', reduced_id=red['reduced_id'])
        self.assertEqual(again, red)
        h.invoke('generator.xray_off')
        v = h.read('evaluator')
        self.assertFalse(v['generator.xray_on'])
        self.assertEqual(v['truth.voltage_v'], 0.0)
        self.assertFalse(v['panel.warning_lamp'])

    def test_queries_and_bookkeeping(self):
        h = ready()
        self.assertEqual(h.invoke('records.list'), dict(records=[], reduced=[], calibrations=[]))
        ev = h.invoke('events.read', since_s=0.0)
        self.assertTrue(any(e['code'] == 'SOFTWARE_READY' for e in ev['events']))
        self.assertEqual(h.invoke('provenance.read')['templates'], [])
        self.assertEqual(h.invoke('sim.step', dt_s=1.0)['time_s'], h.time_s)


# ---------------------------------------------------------------- declared effects (scenario states for the effect discovery)
def _scenario(name):
    if name == 'fresh':
        return rt.AntonpaarSaxspaceRuntime()
    if name == 'off':
        h = configured()
        liquid(h, 'w')
        return h
    if name == 'booting':
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(10.0)
        liquid(h, 'w')
        return h
    if name == 'ready':
        h = ready(xray=False)
        liquid(h, 'w')
        liquid(h, 'lys', [(LYS, 5.0)])
        return h
    if name == 'xray_ramping':
        h = ready(xray=False)
        liquid(h, 'w')
        h.invoke('generator.xray_on')
        h.step(3.0)
        return h
    if name == 'door_open_loaded':
        h = ready()
        liquid(h, 'w')
        liquid(h, 'lys', [(LYS, 5.0)])
        solid(h, 'gc', GC, 1.055e-3)
        h.invoke('chamber.set_door', open=True)
        h.invoke('sample.load', sample_id='w', holder='capillary_1mm')
        return h
    if name == 'pumping':
        h = ready()
        liquid(h, 'w')
        h.invoke('chamber.set_door', open=True)
        h.invoke('sample.load', sample_id='w', holder='capillary_1mm')
        h.invoke('chamber.set_door', open=False)
        h.invoke('vacuum.set_pump', on=True)
        h.step(20.0)
        return h
    if name == 'loaded_vacuum':
        h = ready()
        liquid(h, 'w')
        liquid(h, 'lys', [(LYS, 5.0)])
        load(h, 'lys', temp=283.15)
        acquire(h, 5.0)
        return h
    if name == 'measuring':
        h = _scenario('loaded_vacuum')
        h.invoke('acquisition.start', exposure_s=60.0, frames=1)
        h.step(2.3)
        return h
    if name == 'scanning':
        h = _scenario('loaded_vacuum')
        h.invoke('acquisition.swaxs_scan', arm_angles_rad=[0.0, 0.3], exposure_s=4.0)
        h.step(5.5)
        return h
    if name == 'aligning':
        h = ready()
        liquid(h, 'w')
        load(h, 'w', align=False)
        h.invoke('stage.auto_align')
        h.step(3.0)
        return h
    if name == 'asx_loaded':
        h = ready()
        liquid(h, 'w')
        liquid(h, 'bsa', [(BSA, 10.0)])
        h.invoke('asx.set_door', open=True)
        h.invoke('asx.load_well', sample_id='bsa', well='AA01', volume_m3=100e-9)
        h.invoke('asx.load_well', sample_id='w', well='AA02', volume_m3=100e-9)
        h.invoke('asx.set_door', open=False)
        h.invoke('asx.inject', well='AA01')
        h.step(60.0)
        return h
    if name == 'asx_injecting':
        h = _scenario('asx_loaded')
        h.invoke('asx.inject', well='AA02')
        h.step(7.5)
        return h
    if name == 'estop':
        h = ready()
        liquid(h, 'w')
        h.invoke('generator.emergency_stop')
        return h
    if name == 'cooling_fault':
        h = ready()
        h.invoke('fault.inject', fault='chiller_failure', active=True)
        h.step(1.0)
        return h
    if name == 'hot_protein':
        h = ready()
        liquid(h, 'lys', [(LYS, 5.0)])
        load(h, 'lys', temp=353.15, pump_s=500.0)
        return h
    if name == 'burst':
        h = _scenario('hot_protein')
        h.invoke('stage.set_temperature', temperature_k=378.15)
        h.step(300.0)
        return h
    if name == 'burst_cleared':
        h = _scenario('burst')
        h.invoke('vacuum.set_pump', on=False)
        h.step(700.0)
        h.invoke('chamber.set_door', open=True)
        h.invoke('sample.unload')
        h.invoke('system.acknowledge')
        h.invoke('stage.set_temperature', temperature_k=295.15)
        return h
    if name == 'door_open_empty':
        h = ready()
        liquid(h, 'w')
        liquid(h, 'lys', [(LYS, 5.0)])
        solid(h, 'gc', GC, 1.055e-3)
        h.invoke('chamber.set_door', open=True)
        return h
    if name == 'asx_door_open':
        h = ready()
        liquid(h, 'w')
        h.invoke('asx.set_door', open=True)
        h.invoke('asx.load_well', sample_id='w', well='AA02', volume_m3=100e-9)
        return h
    if name == 'estop_released':
        h = ready()
        h.invoke('generator.emergency_stop')
        h.invoke('generator.release_emergency_stop')
        return h
    if name == 'measuring_air':
        h = ready()
        liquid(h, 'lys', [(LYS, 5.0)])
        h.invoke('chamber.set_door', open=True)
        h.invoke('sample.load', sample_id='lys', holder='capillary_1mm')
        h.invoke('chamber.set_door', open=False)
        h.invoke('acquisition.start', exposure_s=60.0, frames=1)
        h.step(10.0)
        return h
    if name == 'door_open_pump_on':
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(65.0)
        h.invoke('vacuum.set_pump', on=True)
        h.invoke('power.set', enabled=False)
        h.invoke('chamber.set_door', open=True)
        h.invoke('power.set', enabled=True)
        h.step(65.0)
        return h
    if name == 'extended_hot_setpoint':
        h = ready()
        h.invoke('chamber.set_door', open=True)
        h.invoke('stage.mount', stage_type='tcstage_extended')
        h.invoke('stage.set_temperature', temperature_k=873.15)
        return h
    if name == 'vented_closed':
        h = ready()
        liquid(h, 'w')
        load(h, 'w', align=False)
        h.invoke('vacuum.set_pump', on=False)
        h.step(80.0)
        return h
    if name == 'solid_open':
        h = ready()
        solid(h, 'gc', GC, 1.055e-3)
        h.invoke('chamber.set_door', open=True)
        h.invoke('sample.load', sample_id='gc', holder='solid_holder')
        return h
    if name == 'burst_open':
        h = _scenario('burst')
        h.invoke('vacuum.set_pump', on=False)
        h.step(700.0)
        h.invoke('chamber.set_door', open=True)
        return h
    if name == 'burst_unloaded':
        h = _scenario('burst_open')
        h.invoke('sample.unload')
        return h
    if name == 'mains_fault_restored':
        h = ready()
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=180.0))
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE))
        return h
    if name == 'cooling_fault_cleared':
        h = _scenario('cooling_fault')
        h.invoke('fault.inject', fault='chiller_failure', active=False)
        return h
    if name == 'faults_measuring':
        h = ready()
        liquid(h, 'w')
        load(h, 'w')
        for f in ('vacuum_leak', 'tube_aged', 'capillary_bubble'):
            h.invoke('fault.inject', fault=f, active=True)
        h.invoke('acquisition.start', exposure_s=60.0, frames=1)
        h.step(5.0)
        return h
    if name == 'estop_unpowered':
        h = configured()
        h.invoke('generator.emergency_stop')
        return h
    if name == 'agbe_measured':
        h = ready()
        solid(h, 'agbe', 'silver_behenate', 1e-3, form='powder')
        load(h, 'agbe', holder='paste_cell')
        acquire(h, 30.0)
        return h
    raise KeyError(name)


SCENARIOS = ['fresh', 'off', 'booting', 'ready', 'xray_ramping', 'door_open_loaded', 'door_open_empty', 'pumping', 'loaded_vacuum', 'measuring', 'scanning',
             'aligning', 'asx_loaded', 'asx_door_open', 'asx_injecting', 'estop', 'estop_released', 'cooling_fault', 'hot_protein', 'burst', 'burst_cleared',
             'agbe_measured', 'measuring_air', 'door_open_pump_on', 'extended_hot_setpoint', 'vented_closed', 'solid_open', 'burst_open', 'burst_unloaded',
             'mains_fault_restored', 'cooling_fault_cleared', 'faults_measuring', 'estop_unpowered']
STEP_DTS = (0.4, 1.0, 3.0, 15.0, 120.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(room_k=300.15, mains_voltage_v=180.0, noise_seed=2)],
        'sample.provide': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')],
        'fault.inject': [dict(fault=f, active=True) for f in rt.FAULTS] + [dict(fault=f, active=False) for f in rt.FAULTS],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'system.acknowledge': [{}],
        'generator.set_key': [dict(on=True), dict(on=False)], 'generator.xray_on': [{}], 'generator.xray_off': [{}], 'generator.acknowledge': [{}],
        'generator.emergency_stop': [{}], 'generator.release_emergency_stop': [{}],
        'generator.set_power': [dict(voltage_v=30000.0, current_a=0.03), dict(voltage_v=40000.0, current_a=0.05)],
        'vacuum.set_pump': [dict(on=True), dict(on=False)], 'chamber.set_door': [dict(open=True), dict(open=False)],
        'stage.mount': [dict(stage_type='tcstage_extended'), dict(stage_type='tcstage_peltier')], 'sample.load': [dict(sample_id='lys', holder='capillary_1mm'), dict(sample_id=None, holder='capillary_1mm'),
                                                                               dict(sample_id='gc', holder='solid_holder')],
        'sample.unload': [{}], 'stage.set_temperature': [dict(temperature_k=313.15), dict(temperature_k=253.15)],
        'stage.move': [dict(y_m=2e-3, z_m=1e-3)], 'stage.select_position': [dict(position='flow_cell'), dict(position='capillary')],
        'stage.auto_align': [{}], 'collimation.set_mode': [dict(mode='point'), dict(mode='line')], 'detector.move_arm': [dict(angle_rad=0.4), dict(angle_rad=0.0)],
        'acquisition.start': [dict(exposure_s=2.0, frames=2)], 'acquisition.swaxs_scan': [dict(arm_angles_rad=[0.0, 0.2], exposure_s=2.0)],
        'asx.set_door': [dict(open=True), dict(open=False)], 'asx.load_well': [dict(sample_id='w', well='AB01', volume_m3=50e-9)],
        'asx.unload_well': [dict(well='AA02')], 'asx.set_tray_temperature': [dict(temperature_k=277.15)], 'asx.inject': [dict(well='AA02')],
        'asx.clean': [{}], 'calibration.zero_angle': [dict(record_id='sx-1')], 'calibration.q_scale': [dict(record_id='sx-1', calibrant='silver_behenate')],
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
    elif isinstance(v, list) and len(v) <= 64:
        for i, x in enumerate(v):
            out.update(_flat(x, f'{prefix}[{i}]'))
    else:
        out[prefix] = json.dumps(v, sort_keys=True, default=str) if isinstance(v, list) else v
    return out


def _changed(a, b, scale=None):
    """Relative comparison (no absolute floor)."""
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



class ReviewRound1Regressions(unittest.TestCase):
    """Regression tests for the findings of the first independent runtime review (F01-F19)."""

    def _session_invoke(self, h, principal, cap, args, rid):
        return h.session(principal).invoke(instance_id=h.instance_id, capability_id=cap, arguments=args, request_id=rid)

    def test_f01_agent_events_hide_faults_and_the_frozen_state(self):
        h = ready(seed=21)
        h.register_principal('ag', 'agent')
        h.register_principal('ev', 'evaluator')
        h.invoke('fault.inject', fault='tube_aged', active=True)
        liquid(h, 's1')
        load(h, 's1', align=False)
        h.invoke('stage.set_temperature', temperature_k=253.15)
        h.step(900.0)
        self.assertTrue(h.read('evaluator')['truth.samples']['holder']['frozen'])
        ag = {e['code'] for e in self._session_invoke(h, 'ag', 'events.read', dict(since_s=0.0), 'e1')['result']['events']}
        ev = {e['code'] for e in self._session_invoke(h, 'ev', 'events.read', dict(since_s=0.0), 'e2')['result']['events']}
        self.assertFalse([c for c in ag if c.startswith('FAULT_')] + [c for c in ag if c == 'SAMPLE_FROZEN'])
        self.assertIn('FAULT_TUBE_AGED', ev)
        self.assertIn('SAMPLE_FROZEN', ev)

    def test_f02_records_do_not_reveal_the_composition(self):
        h = ready(seed=22)
        liquid(h, 'buf')
        liquid(h, 'mix', [(LYS, 5.0), (BSA, 2.0)])
        rs = []
        for sid in ('buf', 'mix'):
            load(h, sid)
            rs.append(h.records[acquire(h, 10.0)]['template_source'])
        self.assertEqual(rs[0], rs[1])
        self.assertEqual(set(rs[0]), {'library', 'note_zh'})

    def test_f03_f04_ledger_split_by_optical_depth_and_real_closure(self):
        h = ready(seed=23)
        load(h, None)
        rid = acquire(h, 60.0)
        led = h.invoke('ledger.read')
        self.assertEqual(led['photons_attenuated_sample'], 0.0)             # an empty capillary absorbs only in its quartz walls
        self.assertGreater(led['photons_attenuated_windows'], 0.0)
        self.assertLess(abs(led['photons_residual']), 1e-9 * led['photons_incident'])
        self.assertGreater(led['photons_direct_off_detector'], 0.0)          # 20 mm line beam vs 8 mm strips
        self.assertGreater(led['photons_direct_stopped'], led['photons_direct_detected'])
        g = h._geometry()
        liquid(h, 'w')
        load(h, 'w')
        g = h._geometry()
        # 1 mm water in a 10 um quartz capillary: windows take ~14 % of the absorbed share at the beam centre, not a fixed 35 %
        share = g['att_window'] / (g['att_window'] + g['att_sample'])
        self.assertLess(share, 0.25)
        self.assertGreater(share, 0.05)
        self.assertTrue(rid)

    def test_f05_water_calibration_in_air_equals_vacuum(self):
        """Air and collimator scattering pass through the sample: after normalising by the transmitted primary beam and subtracting the empty
        capillary, the water scale measured in air equals the one measured under vacuum."""
        def measure(h, sid, vac):
            if h.pump_on:
                h.invoke('vacuum.set_pump', on=False)
                h.step(80.0)
            if not h.door_open:
                h.invoke('chamber.set_door', open=True)
            if h.holder is not None:
                h.invoke('sample.unload')
            h.invoke('sample.load', sample_id=sid, holder='capillary_1mm')
            h.invoke('chamber.set_door', open=False)
            if vac:
                h.invoke('vacuum.set_pump', on=True)
                h.step(220.0)
            h.invoke('stage.auto_align')
            h.step(rt.ALIGN_S + 4.0)
            return acquire(h, 600.0)
        f = {}
        for vac in (True, False):
            h = ready(seed=24)
            liquid(h, 'w')
            rw = measure(h, 'w', vac)
            re_ = measure(h, None, vac)
            f[vac] = h.invoke('calibration.absolute', record_id=rw, background_record_id=re_, standard='water', thickness_m=1e-3)['factor']
        self.assertAlmostEqual(f[False] / f[True], 1.0, delta=0.05)

    def test_f06_primary_normalisation_does_not_depend_on_the_zero_calibration(self):
        h = ready(seed=1)
        load(h, None)
        r_empty = acquire(h, 300.0)
        liquid(h, 'w')
        load(h, 'w')
        r_w = acquire(h, 300.0)
        before = h.invoke('calibration.absolute', record_id=r_w, background_record_id=r_empty, standard='water', thickness_m=1e-3)['factor']
        h.invoke('calibration.zero_angle', record_id=r_empty)
        after = h.invoke('calibration.absolute', record_id=r_w, background_record_id=r_empty, standard='water', thickness_m=1e-3)['factor']
        self.assertAlmostEqual(after / before, 1.0, delta=0.02)

    def test_f07_overexposure_during_an_exposure_fails_the_acquisition(self):
        h = ready(seed=25)
        liquid(h, 'w')
        load(h, 'w')
        op = h.invoke('acquisition.start', exposure_s=300.0, frames=1)['operation_id']
        h.step(10.0)
        h.invoke('fault.inject', fault='beamstop_misaligned', active=True)
        rid = h.operations[op]['record_id']
        self.assertEqual(h.records[rid]['status'], 'failed')
        self.assertEqual(h.records[rid]['error_code'], 'DETECTOR_OVEREXPOSURE')
        self.assertFalse(h.read('evaluator')['shutter.open'])

    def test_f08_model_spheres_carry_over_one_percent(self):
        h = ready(seed=26)
        sph = dict(radius_m=10e-9, rel_sigma=0.1, volume_fraction=0.01, material='silica', matrix='water')
        h.invoke('sample.provide', sample_id='sph', form='dispersion', solvent='water', volume_m3=200e-9, thickness_m=None,
                 components=[dict(template_id='model_spheres', concentration_kg_m3=None, model_parameters=sph)])
        liquid(h, 'buf', volume=200e-9)
        h.invoke('asx.set_door', open=True)
        h.invoke('asx.load_well', sample_id='sph', well='AA01', volume_m3=100e-9)
        h.invoke('asx.load_well', sample_id='buf', well='AA02', volume_m3=100e-9)
        h.invoke('asx.set_door', open=False)
        for well in ('AA01', 'AA02'):
            h.invoke('asx.inject', well=well)
            h.step(60.0)
        comps = [c for c in h.flow_cell['components'] if c['kind'] == 'model_spheres']
        self.assertEqual(len(comps), 1)
        self.assertAlmostEqual(comps[0]['parameters']['volume_fraction'], 0.01 * rt.ASX_CARRY_OVER, delta=1e-12)

    def test_f09_stage_mount_on_step_effects_are_declared(self):
        h = ready(seed=27)
        h.invoke('chamber.set_door', open=True)
        h.invoke('stage.mount', stage_type='tcstage_extended')
        h.invoke('stage.set_temperature', temperature_k=773.15)
        a, b = copy.deepcopy(h), copy.deepcopy(h)
        a.invoke('stage.mount', stage_type='tcstage_peltier')
        a.step(1500.0)
        b.step(1500.0)
        va, vb = a.read('evaluator'), b.read('evaluator')
        changed = {k for k in ('truth.stage_temperature_k', 'stage.temperature_indicated_k') if va[k] != vb[k]}
        self.assertEqual(changed, {'truth.stage_temperature_k', 'stage.temperature_indicated_k'})
        self.assertLessEqual(changed, set(rt.ON_STEP['stage.mount']))

    def test_f13_emergency_stop_pressed_unpowered_needs_standby(self):
        h = configured(seed=28)
        h.invoke('generator.emergency_stop')
        h.invoke('power.set', enabled=True)
        h.step(61.0)
        h.invoke('generator.set_key', on=True)
        h.invoke('generator.release_emergency_stop')
        self.assertTrue(rejected(h, 'generator.xray_on', 'GENERATOR_FAULT'))
        h.invoke('generator.acknowledge')
        h.invoke('generator.xray_on')

    def test_f14_zero_angle_from_silver_behenate_is_unbiased(self):
        h = ready(seed=29)
        solid(h, 'agbe', 'silver_behenate', 1e-3, form='powder')
        load(h, 'agbe', holder='paste_cell')
        z = h.invoke('calibration.zero_angle', record_id=acquire(h, 60.0))['zero_m']
        self.assertLess(abs(z - h.zero_true), 3e-6)

    def test_f17_point_collimation_desmear_and_molecular_weight(self):
        h = ready(seed=30)
        h.invoke('collimation.set_mode', mode='point')
        liquid(h, 'bsa', [(BSA, 5.0)])
        liquid(h, 'buf')
        load(h, 'bsa')
        rs = acquire(h, 1800.0)
        load(h, 'buf')
        rb = acquire(h, 1800.0)
        liquid(h, 'water_std')
        load(h, 'water_std')
        rw = acquire(h, 1800.0)
        load(h, None)
        re_ = acquire(h, 1800.0)
        cal = h.invoke('calibration.absolute', record_id=rw, background_record_id=re_, standard='water', thickness_m=1e-3)['calibration_id']
        smeared = h.invoke('data.reduce', record_id=rs, background_record_id=rb, calibration_id=cal, thickness_m=1e-3, desmear=False)
        self.assertTrue(rejected(h, 'analysis.molecular_weight', 'DESMEAR_REQUIRED', reduced_id=smeared['reduced_id'], concentration_kg_m3=5.0,
                                 drho_m_m_kg=rt.DRHO_M_PROTEIN_M_KG))
        red = h.invoke('data.reduce', record_id=rs, background_record_id=rb, calibration_id=cal, thickness_m=1e-3, desmear=True)
        mw = h.invoke('analysis.molecular_weight', reduced_id=red['reduced_id'], concentration_kg_m3=5.0, drho_m_m_kg=rt.DRHO_M_PROTEIN_M_KG)
        m_template = rt.template(BSA)['abs_scale']['mass_da'] / 1000.0
        self.assertAlmostEqual(mw['molecular_weight_kg_mol'] / m_template, 1.0, delta=0.12)

    def test_f18_truefocus_finds_the_empty_flow_cell(self):
        h = ready(seed=31)
        h.invoke('stage.select_position', position='flow_cell')
        h.step(10.0)
        op = h.invoke('stage.auto_align')['operation_id']
        h.step(rt.ALIGN_S + 4.0)
        self.assertEqual(h.operations[op]['phase'], 'completed')
        self.assertIsNone(h.operations[op].get('reason'))
        self.assertAlmostEqual(h.stage['y'], rt.FLOW_CELL_Y_M + h.fc_offset, delta=3e-5)

    def test_f19_room_temperature_change_is_not_cache_dependent(self):
        h = ready(seed=32)
        load(h, None)
        h.invoke('vacuum.set_pump', on=False)
        h.step(80.0)
        h.invoke('acquisition.start', exposure_s=60.0, frames=1)
        h.step(20.0)
        a, b = copy.deepcopy(h), copy.deepcopy(h)
        for x in (a, b):
            x.invoke('plant.configure', room_k=305.15, mains_voltage_v=230.0, noise_seed=1)
        b.invoke('fault.inject', fault='chiller_failure', active=False)       # a no-op call that used to clear the rate cache
        a.step(45.0)
        b.step(45.0)
        self.assertEqual(a.records['sx-1']['segments'][0]['counts'], b.records['sx-1']['segments'][0]['counts'])



class ReviewRound2Regressions(unittest.TestCase):
    """Regression tests for the second independent runtime review (F20-F22)."""

    def _spheres_solid(self, h, sid, radius, phi, thick):
        h.invoke('sample.provide', sample_id=sid, form='solid', solvent='none', volume_m3=0.0, thickness_m=thick,
                 components=[dict(template_id='model_spheres', concentration_kg_m3=None,
                                  model_parameters=dict(radius_m=radius, rel_sigma=0.1, volume_fraction=phi, material='silica', matrix='polystyrene'))])

    def test_f20_primary_peak_is_found_or_refused_never_misplaced(self):
        for seed, radius, phi, thick in ((51, 50e-9, 0.02, 1e-3), (52, 10e-9, 0.3, 2e-3)):
            h = ready(seed=seed)
            self._spheres_solid(h, 's', radius, phi, thick)
            load(h, 's', holder='solid_holder')
            rid = acquire(h, 30.0)
            pc = h.records[rid]['primary_beam_counts']
            g = h._geometry()
            expected = h._flux() * g['t_total'] * rt.detector_efficiency() * rt.STRIP_LENGTH_M / rt.LINE_LENGTH_M * rt.BEAMSTOP_T * 30.0
            self.assertIsNotNone(pc)                                  # the attenuated peak is beam-shaped even when weaker than the scattering outside
            self.assertAlmostEqual(pc / expected, 1.0, delta=0.05)
            z = h.invoke('calibration.zero_angle', record_id=rid)['zero_m']
            self.assertLess(abs(z - h.zero_true), 10e-6)

    def test_f20_ordinary_samples_keep_their_primary_beam(self):
        h = ready(seed=54)
        solid(h, 'gc', GC, 1.055e-3)
        load(h, 'gc', holder='solid_holder')
        rid = acquire(h, 60.0)
        self.assertIsNotNone(h.records[rid]['primary_beam_counts'])
        z = h.invoke('calibration.zero_angle', record_id=rid)['zero_m']
        self.assertLess(abs(z - h.zero_true), 5e-6)

    def test_f23_zero_beyond_three_sigma_is_still_found(self):
        """Instruments whose true zero lies 0.15-0.21 mm from nominal (seeds 1377 and 129 of the review) still find their primary beam before
        the first zero calibration, and after it the normalisation uses the calibrated zero."""
        for seed in (1377, 129):
            h = ready(seed=seed)
            self.assertGreater(abs(h.zero_true), 0.14e-3)
            empty_beam(h)
            rid = acquire(h, 60.0)
            self.assertIsNotNone(h.records[rid]['primary_beam_counts'])
            z = h.invoke('calibration.zero_angle', record_id=rid)['zero_m']
            self.assertLess(abs(z - h.zero_true), 5e-6)
            h.invoke('data.reduce', record_id=rid, background_record_id=None, calibration_id=None, thickness_m=1e-3, desmear=False)

    def test_f24_far_zero_with_steep_scatterers(self):
        """Far-zero instruments before the zero calibration with steeply scattering solids (review round 4): the attenuated primary, not a scatter
        maximum near the hint, is found."""
        for seed, radius, phi in ((129, 100e-9, 0.003), (129, 80e-9, 0.01), (1377, 100e-9, 0.003)):
            h = ready(seed=seed)
            self._spheres_solid(h, 's', radius, phi, 1e-3)
            load(h, 's', holder='solid_holder')
            rid = acquire(h, 30.0)
            g = h._geometry()
            expected = h._flux() * g['t_total'] * rt.detector_efficiency() * rt.STRIP_LENGTH_M / rt.LINE_LENGTH_M * rt.BEAMSTOP_T * 30.0
            self.assertAlmostEqual(h.records[rid]['primary_beam_counts'] / expected, 1.0, delta=0.05)
            z = h.invoke('calibration.zero_angle', record_id=rid)['zero_m']
            self.assertLess(abs(z - h.zero_true), 5e-6)

    def test_f21_alignment_is_protected_against_overexposure(self):
        h = ready(seed=53)
        liquid(h, 'l', [(LYS, 5.0)])
        load(h, 'l', align=False)
        h.invoke('fault.inject', fault='beamstop_misaligned', active=True)
        op = h.invoke('stage.auto_align')['operation_id']
        h.step(2.0)
        self.assertEqual(h.operations[op]['phase'], 'failed')
        self.assertEqual(h.operations[op]['reason'], 'DETECTOR_OVEREXPOSURE')
        self.assertFalse(h.read('evaluator')['shutter.open'])

    def test_f22_model_spheres_leave_the_rinse_residue(self):
        h = ready(seed=55)
        h.invoke('sample.provide', sample_id='sph', form='dispersion', solvent='water', volume_m3=200e-9, thickness_m=None,
                 components=[dict(template_id='model_spheres', concentration_kg_m3=None,
                                  model_parameters=dict(radius_m=10e-9, rel_sigma=0.1, volume_fraction=0.01, material='silica', matrix='water'))])
        liquid(h, 'buf', volume=200e-9)
        h.invoke('asx.set_door', open=True)
        h.invoke('asx.load_well', sample_id='sph', well='AA01', volume_m3=50e-9)
        h.invoke('asx.load_well', sample_id='buf', well='AA02', volume_m3=50e-9)
        h.invoke('asx.set_door', open=False)
        h.invoke('asx.inject', well='AA01')
        h.step(60.0)
        h.invoke('asx.clean')
        h.step(130.0)
        h.invoke('asx.inject', well='AA02')
        h.step(60.0)
        comps = [c for c in h.flow_cell['components'] if c['kind'] == 'model_spheres']
        self.assertEqual(len(comps), 1)
        self.assertAlmostEqual(comps[0]['parameters']['volume_fraction'], 0.01 * rt.ASX_CARRY_OVER_CLEAN, delta=1e-15)


class DeclaredEffects(unittest.TestCase):
    def test_on_step_effects_cover_what_the_process_changes(self):
        """For every capability accepted in any scenario state: the slots it changes at once are declared effects, the slots whose change during the
        following step differs from an identical run without the call are declared on_step effects, and the step itself only changes slots declared
        as on_step effects of sim.step (noise-free and noisy runs; both runs draw identical noise during the step)."""
        missing_imm, missing_step = {}, {}
        states = {n: _scenario(n) for n in SCENARIOS}
        for noisy in (False, True):
            for n, S in states.items():
                for i, name in enumerate(RNGS):
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
                            for name in RNGS:
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
        si = {'1', 's', 'm', 'rad', 'Pa', 'V', 'A', 'K', 'J', 'W', 'kg', 'm^3', '1/s', 'Pa*m^3/s', 'm^3/s', 'm/s', 'V/s', 'A/s', 'K/s', 'rad/s', '1/m', 'kg/m^3',
              'kg/mol', 'm^2/kg', 'm/kg', '1/sr'}
        units = {v['unit'] for v in meta['state_slots'].values()}
        for mech in meta['mechanisms'].values():
            units |= {p_['unit'] for p_ in mech['parameters'].values()}
        self.assertEqual(units - si, set(), units - si)
        h = ready()
        h.register_principal('e', 'evaluator')
        h.invoke('detector.move_arm', angle_rad=0.3)
        h.step(0.5)
        obs = h.session('e').read(instance_id=h.instance_id)['observations']
        self.assertIsNone(obs['detector.arm_indicated_rad']['sample_window_s'])
        self.assertGreater(obs['detector.arm_indicated_rad']['value'], 0.0)
        self.assertEqual(obs['vacuum.pressure_indicated_pa']['sample_window_s'], rt.CYCLE_S)
        g0 = h.read()['vacuum.pressure_indicated_pa']
        h.step(0.2)
        self.assertEqual(h.read()['vacuum.pressure_indicated_pa'], g0)


if __name__ == '__main__':
    unittest.main()
