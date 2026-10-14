"""tests for CHEM-154 kratos_axissupra: python3 -m unittest tests/test_asset_kratos_axissupra.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances
are isolated, agent/evaluator truth isolation, conservation (sputtered atoms, argon, nitrogen, charge), real return values validated against the
declared schemas (asset_tool lint_schema / validate_value), asynchronous completion and cancellation remnants, host-step independence, declared
immediate and on_step effects; plus the instrument (vacuum, Flexi-lock, pump cycle with holder images, transfer, 5-axis stage and lens clearance,
X-ray interlocks) and the XPS physics: the acceptance performance of the related AXIS Supra (Ag 3d5/2 0.48 eV at > 760 kcps, PET C 1s 0.68 eV at
> 44 kcps with the neutraliser), charging and neutralisation, ISO 15472 energy-scale calibration on the Au/Ag/Cu reference samples, chemical
states from the NIST SRD 20 templates, quantification, SiO2/Si overlayer thickness, angle-resolved enhancement, Ta2O5/Ta depth profile with oxide
reduction and argon implantation, parallel imaging of a copper grid, faults.
"""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import kratos_axissupra as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE = [], {}
_META = {}
UNIFORM = dict(kind='uniform', feature_substance_id=None, feature_thickness_m=None, pitch_m=None, width_m=None, coverage=None)


def _meta():
    if not _META:
        _META.update(rt.export_kratos_axissupra_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.KratosAxissupraRuntime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema and immediate state effects (a call does not advance time)."""
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
        undeclared = {k for k in after if _changed(before[k], after[k])} - set(rt.EFFECTS.get(capability_id, []))
        if undeclared and rt.EFFECTS:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.KratosAxissupraRuntime.invoke = _instrumented_invoke


class QuietRng:
    """Noise-free stand-in for random.Random (gauges exact, fixed draws) so that two runs differ only by the process."""
    def gauss(self, mu, sigma):
        return mu

    def random(self):
        return 0.5

    def uniform(self, a, b):
        return 0.5 * (a + b)

    def seed(self, x):
        pass


def configured(seed=1, **plant):
    h = rt.KratosAxissupraRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def ready(seed=1, **plant):
    h = configured(seed, **plant)
    h.invoke('power.set', enabled=True)
    h.step(rt.BOOT_S + 1.0)
    return h


def provide(h, sid, substrate, layers=(), pattern=None, size=0.010, thick=5.0e-4, air=True, form=None):
    h.invoke('sample.provide', sample_id=sid, form=form or ('film_on_substrate' if layers else 'solid'), substrate_substance_id=substrate,
             layers=[dict(substance_id=a, thickness_m=b) for a, b in layers], pattern=pattern or dict(UNIFORM), lateral_size_m=size, thickness_m=thick,
             air_exposed=air)
    return sid


def on_stage(h, samples, holder='H1', slot=1):
    """Mount samples [(sid, x)] on a holder, load it, pump the Flexi-lock and transfer the holder to the stage."""
    for sid, x in samples:
        h.invoke('holder.mount_sample', holder_id=holder, sample_id=sid, x_m=x, y_m=0.0)
    if h.fl_mode != 'vented':
        h.invoke('flexilock.vent')
        h.step(90.0)
    h.invoke('flexilock.set_lid', open=True)
    h.invoke('flexilock.load_holder', holder_id=holder, slot=slot)
    h.invoke('flexilock.set_lid', open=False)
    h.invoke('flexilock.pump')
    for _ in range(200):
        h.step(10.0)
        if h.fl_mode == 'pumped':
            break
    assert h.fl_mode == 'pumped', h.p_fl
    op = h.invoke('transfer.to_stage', slot=slot)['operation_id']
    wait(h, op)
    assert h.stage_holder == holder
    return h


def wait(h, op, tmax=20000.0, dt=1.0):
    t0 = h.time_s
    while h.operations[op]['phase'] == 'running' and h.time_s - t0 < tmax:
        h.step(dt)
    return h.operations[op]['phase']


def beam(h, emission=0.015):
    h.invoke('xray.set_source', on=True, emission_a=emission)
    h.step(30.0)
    assert h._xray_at_setpoint()
    return h


def go_sample(h, x, z=None, tilt=0.0, rot=0.0):
    s = next(sid for sid, (sx, sy) in h.holders[h.stage_holder]['samples'].items() if abs(sx - x) < 1e-9)
    zz = -h.samples[s]['thickness_m'] if z is None else z
    h.invoke('stage.move', x_m=-x, y_m=0.0, z_m=zz, tilt_rad=tilt, rotation_rad=rot)
    h.step(40.0)
    return h


def spectrum(h, regions, mode='scanned'):
    op = h.invoke('acquisition.spectrum', regions=regions, mode=mode)['operation_id']
    st = wait(h, op)
    rid = h.operations[op]['record_id']
    assert st == 'completed', (st, h.operations[op])
    return rid


def region(name, lo, hi, step=0.05, dwell=0.1, sweeps=2):
    return dict(name=name, start_be_ev=lo, end_be_ev=hi, step_ev=step, dwell_s=dwell, sweeps=sweeps)


def arrays(h, rid, name):
    sp = next(s for s in h.records[rid]['spectra'] if s['name'] == name)
    return np.array(sp['binding_energy_ev']), np.array(sp['counts_per_second'])


def fwhm(be, y):
    y = np.asarray(y, float) - float(np.min(y))
    i = int(np.argmax(y))
    h = y[i] / 2
    l_ = i
    while l_ > 0 and y[l_] > h:
        l_ -= 1
    r = i
    while r < len(y) - 1 and y[r] > h:
        r += 1
    xl = be[l_] + (h - y[l_]) / (y[l_ + 1] - y[l_]) * (be[l_ + 1] - be[l_])
    xr = be[r - 1] + (h - y[r - 1]) / (y[r] - y[r - 1]) * (be[r] - be[r - 1])
    return abs(xr - xl), be[i], y[i]


def rejected(h, cap, code, **a):
    before = h.read('evaluator')
    rev = h.revision
    try:
        h.invoke(cap, **a)
    except rt.ActionRejected as exc:
        assert str(exc) == code, (cap, str(exc), code)
        assert not _changed(h.read('evaluator'), before), cap
        assert h.revision == rev, cap
        return True
    raise AssertionError(f'{cap} was not rejected (expected {code})')


def clean_reference(h, ref, seconds=60.0):
    h.invoke('stage.go_to', position='reference:' + ref)
    h.step(60.0)
    if not h.ion_on:
        h.invoke('ion_gun.set', on=True, energy_ev=3000.0, raster_m=0.003)
    op = h.invoke('ion_gun.etch', duration_s=seconds)['operation_id']
    wait(h, op)


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.kratos_axissupra')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-154')

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
        self.assertEqual(set(rt.CAP_SOURCES), set(rt.CAPS))
        comps = {p['id'] for p in json.loads((rt.ASSET / 'geometry_manifest.json').read_text())['components']}
        for k, c in meta['capabilities'].items():
            self.assertLessEqual(set(c['component_ids']), comps, k)
        for k, s in meta['state_slots'].items():
            self.assertLessEqual(set(s['component_ids']), comps, k)

    def test_every_rejection_code_is_declared(self):
        """Each ActionRejected code raised by a command method (directly or through its helpers) appears in that capability's declared errors."""
        import inspect
        import re
        src = inspect.getsource(rt.KratosAxissupraRuntime)
        meta = _meta()['capabilities']
        helpers = {'_need_plant': ['PLANT_REQUIRED'], '_need_power': ['PLANT_REQUIRED', 'POWER_OFF'],
                   '_need_ready()': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING'], '_need_ready(allow_error=True)': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY'],
                   '_need_idle': ['ACQUISITION_RUNNING'],
                   '_need_no_transfer': ['TRANSFER_RUNNING'], '_need_beam': ['XRAY_NOT_READY', 'VACUUM_NOT_READY'],
                   '_need_stage_free': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'TRANSFER_INTERRUPTED'],
                   '_need_not_stranded': ['TRANSFER_INTERRUPTED'],
                   '_need_acquire': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'XRAY_NOT_READY',
                                     'VACUUM_NOT_READY', 'STAGE_EMPTY', 'MOTION_IN_PROGRESS', 'RECORDS_FULL'],
                   '_check_regions(regions, mode)': ['DUPLICATE_REGION', 'INVALID_REGION', 'REGION_EXCEEDS_SNAPSHOT_WINDOW'],
                   "_check_regions(regions, 'scanned')": ['DUPLICATE_REGION', 'INVALID_REGION'], '_check_stage_target': ['OUT_OF_RANGE', 'COLLISION_RISK'],
                   '_rec(': ['UNKNOWN_RECORD', 'WRONG_RECORD_KIND', 'RECORD_NOT_COMPLETED'], '_region(': ['UNKNOWN_REGION'],
                   'substance(': ['UNKNOWN_TEMPLATE'], '_quantify_spectra(': ['UNKNOWN_REGION', 'UNASSIGNED_REGION']}
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
            h.rng_acq = QuietRng()
            provide(h, 'cu', 'cu')
            on_stage(h, [('cu', 0.0)])
            beam(h)
            h.invoke('stage.move', x_m=0.002, y_m=0.0, z_m=-5.0e-4, tilt_rad=0.2, rotation_rad=0.3)
            for dt in chunks:
                h.step(dt)
            h.invoke('ion_gun.set', on=True, energy_ev=2000.0, raster_m=0.003)
            h.invoke('ion_gun.etch', duration_s=33.3)
            for dt in chunks:
                h.step(dt)
            h.invoke('acquisition.spectrum', regions=[region('Cu 2p', 925.0, 960.0, 0.1, 0.05, 1)], mode='scanned')
            for dt in chunks:
                h.step(dt)
            return h.read('evaluator'), h.ledger(), h.records['SP0001']
        (a, la, ra), (b, lb, rb) = run([100.0]), run([0.37] * 270 + [0.1])
        for k in ('truth.sac_pressure_pa', 'truth.fl_pressure_pa', 'stage.x_m', 'stage.tilt_rad', 'stage.rotation_rad', 'clock.time_s'):
            self.assertAlmostEqual(a[k], b[k], delta=1e-9 * max(abs(a[k]), 1e-12), msg=k)
        for k in ('anode_energy_j', 'photons_on_sample', 'nitrogen_pumped_mol', 'sputtered_atoms', 'argon_admitted_mol'):
            self.assertAlmostEqual(la[k], lb[k], delta=1e-9 * max(abs(la[k]), 1.0), msg=k)
        self.assertEqual(ra['status'], 'completed')
        self.assertEqual(ra['spectra'], rb['spectra'])
        self.assertAlmostEqual(a['truth.samples']['cu']['craters'][0]['removed']['base'], b['truth.samples']['cu']['craters'][0]['removed']['base'], delta=1e-18)

    def test_reads_do_not_change_later_counts(self):
        h = ready(seed=4)
        provide(h, 'cu', 'cu')
        on_stage(h, [('cu', 0.0)])
        beam(h)
        go_sample(h, 0.0)
        h.invoke('acquisition.spectrum', regions=[region('Cu 2p', 925.0, 960.0, 0.1, 0.05, 1)], mode='scanned')
        a, b = copy.deepcopy(h), copy.deepcopy(h)
        for _ in range(60):
            a.step(0.5)
            a.read('agent')
            a.read('evaluator')
            b.step(0.5)
        self.assertEqual(a.records['SP0001']['status'], 'completed')
        self.assertEqual(a.records['SP0001']['spectra'], b.records['SP0001']['spectra'])
        self.assertEqual(a.ledger(), b.ledger())

    def test_set_points_only_write_targets(self):
        h = ready()
        provide(h, 'cu', 'cu')
        on_stage(h, [('cu', 0.0)])
        x0 = h.stage['x']
        h.invoke('stage.move', x_m=0.010, y_m=0.0, z_m=0.0, tilt_rad=0.0, rotation_rad=0.0)
        self.assertEqual(h.stage['x'], x0)
        h.step(1.0)
        self.assertAlmostEqual(h.stage['x'], x0 + rt.STAGE_SPEED_M_S, places=12)
        h.invoke('xray.set_source', on=True, emission_a=0.015)
        self.assertEqual(h.hv, 0.0)
        h.step(1.0)
        self.assertAlmostEqual(h.hv, rt.HV_RAMP_V_S, places=6)
        h.invoke('flexilock.vent')
        p = h.p_fl
        self.assertEqual(h.fl_mode, 'venting')
        h.step(1.0)
        self.assertGreater(h.p_fl, p)


class VacuumAndHandling(unittest.TestCase):
    def test_lid_opens_only_at_atmosphere_and_pump_cycle_images_holders(self):
        h = ready()
        provide(h, 'a', 'au')
        h.invoke('holder.mount_sample', holder_id='H2', sample_id='a', x_m=0.010, y_m=0.002)
        h.invoke('flexilock.set_lid', open=True)
        h.invoke('flexilock.load_holder', holder_id='H2', slot=2)
        h.invoke('flexilock.set_lid', open=False)
        rejected(h, 'flexilock.load_holder', 'LID_CLOSED', holder_id='H1', slot=1)
        h.invoke('flexilock.pump')
        h.step(20.0)
        rejected(h, 'flexilock.set_lid', 'NOT_VENTED', open=True)
        for _ in range(100):
            h.step(10.0)
            if h.fl_mode == 'pumped':
                break
        self.assertEqual(h.fl_mode, 'pumped')
        self.assertLess(h.p_fl, rt.FL_TRANSFER_MAX_PA)
        rid = h.images['H2']
        det = h.records[rid]['image']['detected']
        self.assertEqual([d['sample_id'] for d in det], ['a'])
        self.assertLess(abs(det[0]['x_m'] - 0.010), 3e-4)

    def test_transfer_needs_the_pumped_flexilock_and_the_gate_burst(self):
        h = ready()
        provide(h, 'a', 'au')
        h.invoke('holder.mount_sample', holder_id='H1', sample_id='a', x_m=0.0, y_m=0.0)
        h.invoke('flexilock.set_lid', open=True)
        h.invoke('flexilock.load_holder', holder_id='H1', slot=1)
        h.invoke('flexilock.set_lid', open=False)
        rejected(h, 'transfer.to_stage', 'FLEXILOCK_NOT_PUMPED', slot=1)
        h.invoke('flexilock.pump')
        for _ in range(100):
            h.step(10.0)
            if h.fl_mode == 'pumped':
                break
        p0 = h.p_sac
        op = h.invoke('transfer.to_stage', slot=1)['operation_id']
        rejected(h, 'flexilock.vent', 'TRANSFER_RUNNING')
        peak = p0
        h.step(0.01)                                               # the gate opens at once (stage and magazine already in place)
        peak = max(peak, h.p_sac)
        while h.operations[op]['phase'] == 'running':
            h.step(0.5)
            peak = max(peak, h.p_sac)
            self.assertLessEqual(h.arm, rt.TRANSFER_STROKE_M + 1e-12)
        self.assertEqual(h.operations[op]['phase'], 'completed')
        self.assertGreater(peak, 3 * p0)                           # the gate valve lets the Flexi-lock gas into the analysis chamber (equalised 5.1 x p0, tau 0.1 s)
        self.assertEqual((h.stage_holder, h.magazine[1], h.gate, h.arm), ('H1', None, 0.0, 0.0))
        op = h.invoke('transfer.to_magazine', slot=3)['operation_id']
        self.assertEqual(wait(h, op), 'completed')
        self.assertEqual((h.stage_holder, h.magazine[3]), (None, 'H1'))

    def test_power_loss_during_transfer_interrupts_and_latches(self):
        h = ready()
        provide(h, 'a', 'au')
        on_stage(h, [('a', 0.0)])
        op = h.invoke('transfer.to_magazine', slot=1)['operation_id']
        while h.transfer_op['phase'] != 'arm_in':
            h.step(0.5)
        h.invoke('power.set', enabled=False)
        self.assertEqual(h.operations[op]['phase'], 'failed')
        self.assertEqual(h.error, 'TRANSFER_INTERRUPTED')
        self.assertEqual(h.holders['H1']['location'], 'arm')
        h.step(5.0)
        self.assertGreater(h.gate, 0.08)                             # the extended fork blocks the pneumatic gate valve (it would close otherwise)
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1.0)
        self.assertEqual(wait(h, h.invoke('transfer.recover')['operation_id']), 'completed')
        self.assertEqual((h.stage_holder, h.magazine[1], h.holders['H1']['location'], h.gate, h.error), (None, 'H1', 'slot:1', 0.0, None))

    def test_sample_and_mount_validation(self):
        h = configured()
        rejected(h, 'sample.provide', 'UNKNOWN_TEMPLATE', sample_id='x', form='solid', substrate_substance_id='unobtainium', layers=[], pattern=dict(UNIFORM),
                 lateral_size_m=0.01, thickness_m=5e-4, air_exposed=True)
        rejected(h, 'sample.provide', 'INVALID_PATTERN', sample_id='x', form='solid', substrate_substance_id='si', layers=[],
                 pattern=dict(UNIFORM, kind='grid'), lateral_size_m=0.01, thickness_m=5e-4, air_exposed=True)
        provide(h, 'big', 'au', size=0.020)
        provide(h, 'thick', 'au', thick=0.019)
        provide(h, 'small', 'au', size=0.005)
        rejected(h, 'holder.mount_sample', 'OUTSIDE_HOLDER', holder_id='H1', sample_id='big', x_m=0.030, y_m=0.0)
        h.invoke('holder.mount_sample', holder_id='H1', sample_id='big', x_m=0.0, y_m=0.0)
        rejected(h, 'holder.mount_sample', 'POSITION_OCCUPIED', holder_id='H1', sample_id='small', x_m=0.009, y_m=0.0)
        h.invoke('holder.mount_sample', holder_id='H1', sample_id='thick', x_m=-0.025, y_m=0.0)
        rejected(h, 'holder.mount_sample', 'SAMPLE_NOT_ON_BENCH', holder_id='H2', sample_id='thick', x_m=0.0, y_m=0.0)

    def test_stage_lens_clearance_and_reference_positions(self):
        h = ready()
        provide(h, 'thick', 'au', thick=0.010)
        on_stage(h, [('thick', 0.0)])
        self.assertAlmostEqual(h.stage['z'], -0.0105, places=9)     # the transfer pose lowers a holder with a thick sample
        rejected(h, 'stage.move', 'COLLISION_RISK', x_m=0.0, y_m=0.0, z_m=0.0, tilt_rad=0.0, rotation_rad=0.0)
        h.invoke('stage.go_to', position='reference:Cu')
        h.step(60.0)
        self.assertEqual(h._analysed_target()[:2], ('reference', 'Cu'))
        self.assertLess(abs(h.read()['stage.x_indicated_m'] - (-rt.REF_BLOCK_X)), 5e-6)

    def test_auto_height_brings_the_surface_to_the_focus(self):
        h = ready()
        provide(h, 'au', 'au', thick=2.0e-3)
        on_stage(h, [('au', 0.0)])
        beam(h)
        h.invoke('stage.move', x_m=0.0, y_m=0.0, z_m=-1.2e-3, tilt_rad=0.0, rotation_rad=0.0)
        h.step(10.0)
        self.assertGreater(abs(h._focus_offset()), 5e-4)
        op = h.invoke('stage.auto_height')['operation_id']
        self.assertEqual(wait(h, op), 'completed')
        h.step(20.0)
        self.assertLess(abs(h._focus_offset()), 3e-5)


class XraySourceAndFaults(unittest.TestCase):
    def test_interlocks_and_power_limit(self):
        h = ready()
        with self.assertRaises(rt.ActionRejected):
            h.invoke('xray.set_source', on=True, emission_a=0.041)
        h.invoke('fault.inject', fault='chiller_failure', active=True)
        rejected(h, 'xray.set_source', 'COOLING_WATER_LOW', on=True, emission_a=0.015)
        h.invoke('fault.inject', fault='chiller_failure', active=False)
        beam(h)
        self.assertAlmostEqual(h._xray_power(), 225.0, places=6)
        h.invoke('fault.inject', fault='chiller_failure', active=True)
        h.step(2.0)
        self.assertFalse(h.xray_on)
        self.assertEqual((h.xray_fault, h.error), ('COOLING_WATER_LOW', 'COOLING_WATER_LOW'))
        rejected(h, 'system.acknowledge', 'CAUSE_PERSISTS')
        h.invoke('fault.inject', fault='chiller_failure', active=False)
        h.invoke('system.acknowledge')
        self.assertIsNone(h.error)

    def test_sac_leak_prevents_the_xray_source(self):
        h = ready()
        h.invoke('fault.inject', fault='sac_leak', active=True)
        h.step(60.0)
        self.assertGreater(h.p_sac, rt.XRAY_MAX_PA)
        rejected(h, 'xray.set_source', 'VACUUM_NOT_READY', on=True, emission_a=0.015)

    def test_neutraliser_filament_failure_switches_it_off(self):
        h = ready()
        h.invoke('neutraliser.set', on=True, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)
        h.invoke('fault.inject', fault='neutraliser_filament_failure', active=True)
        h.step(2.0)
        self.assertFalse(h.neut_on)
        rejected(h, 'neutraliser.set', 'FILAMENT_FAILURE', on=True, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)


class SpectroscopyPhysics(unittest.TestCase):
    def test_ag_3d52_acceptance_performance(self):
        """Related AXIS Supra acceptance value: Ag 3d5/2 > 760 kcps at 0.48 eV FWHM (slot, PE 10 eV, 600 W, sputter-cleaned Ag)."""
        h = ready(seed=2)
        provide(h, 'dummy', 'au')
        on_stage(h, [('dummy', 0.0)])
        beam(h, emission=0.040)
        clean_reference(h, 'Ag')
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=10.0)
        rid = spectrum(h, [region('Ag', 364.0, 373.0, 0.02, 0.1, 1)])
        be, y = arrays(h, rid, 'Ag')
        w, c, top = fwhm(be[be < 371.5], y[be < 371.5])
        self.assertAlmostEqual(w, 0.48, delta=0.03)
        self.assertGreater(top, 760e3)

    def test_pet_c1s_on_insulators_needs_the_neutraliser(self):
        """Related AXIS Supra value on insulators: PET C 1s 0.68 eV FWHM (> 44 kcps) with the electron-only neutraliser; without it the peaks shift
        to higher binding energy by several volts and broaden."""
        h = ready(seed=3)
        provide(h, 'pet', 'pet', thick=1.0e-4)
        on_stage(h, [('pet', 0.0)])
        beam(h, emission=0.040)
        go_sample(h, 0.0)
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=20.0)
        rid = spectrum(h, [region('C 1s wide', 270.0, 330.0, 0.1, 0.02, 1)])
        be, y = arrays(h, rid, 'C 1s wide')
        shift_off = float(be[int(np.argmax(y))]) - 284.8
        self.assertGreater(shift_off, 5.0)
        h.invoke('neutraliser.set', on=True, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)
        h.step(20.0)
        rid = spectrum(h, [region('C 1s', 274.0, 292.0, 0.02, 0.1, 1)])
        be, y = arrays(h, rid, 'C 1s')
        cc = h.invoke('data.charge_correct', record_id=rid, region='C 1s', reference_be_ev=284.8)
        self.assertLess(cc['shift_ev'], 6.0)
        self.assertGreater(cc['shift_ev'], 1.0)                        # the neutraliser overcompensates slightly (negative surface potential)
        est = 288.8 - cc['shift_ev']                                    # the ester component in the uncorrected spectrum
        m = (be > est - 0.9) & (be < est + 0.9)
        w, c, top = fwhm(be[m], y[m])
        self.assertAlmostEqual(w, 0.68, delta=0.06)
        self.assertGreater(top, 44e3)
        fit = h.invoke('data.fit', record_id=rid, region='C 1s', shift_ev=cc['shift_ev'],
                       components=[dict(label='C-C', be_ev=284.8, fwhm_ev=0.8, fix_position=False), dict(label='C-O', be_ev=286.4, fwhm_ev=0.8, fix_position=False),
                                   dict(label='O-C=O', be_ev=288.8, fwhm_ev=0.8, fix_position=False)])
        p = {c_['label']: c_['percent'] for c_ in fit['components']}
        self.assertAlmostEqual(p['C-C'] / p['O-C=O'], 3.0, delta=0.9)     # PET repeat unit: 6 C-C : 2 C-O : 2 O-C=O (plus adventitious C-C)
        self.assertAlmostEqual(p['C-O'] / p['O-C=O'], 1.0, delta=0.35)

    def test_iso15472_energy_scale_calibration(self):
        h = ready(seed=6)
        provide(h, 'dummy', 'au')
        on_stage(h, [('dummy', 0.0)])
        beam(h, emission=0.030)
        h.invoke('ion_gun.set', on=True, energy_ev=3000.0, raster_m=0.003)
        err0 = abs(h.scale_true['offset_ev'] + h.scale_true['gain'] * 932.62)
        op = h.invoke('calibration.energy_scale')['operation_id']
        self.assertEqual(wait(h, op), 'completed')
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual(len(rec['spectra']), 3)
        self.assertEqual(h.read()['calibration.energy_scale']['calibration_id'], rec['record_id'])
        # after calibration, clean Au / Cu peaks come at the ISO 15472 energies
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=10.0)
        for ref, (lo, hi, want) in (('Au', (81.0, 87.0, 83.96)), ('Cu', (929.0, 936.0, 932.62)), ('Ag', (365.0, 371.0, 368.21))):
            clean_reference(h, ref, 20.0)
            rid = spectrum(h, [region(ref, lo, hi, 0.02, 0.1, 2)])
            got = h._peak_position(dict(counts=np.array(next(s for s in h.records[rid]['spectra'])['counts']),
                                        be=next(s for s in h.records[rid]['spectra'])['binding_energy_ev'], step=0.02))
            self.assertLess(abs(got - want), 0.05, (ref, got, want, err0))

    def test_chemical_states_and_oxide_thickness_of_sio2_on_si(self):
        h = ready(seed=7)
        provide(h, 'ox', 'si', layers=[('sio2', 4.0e-9)])
        on_stage(h, [('ox', 0.0)])
        beam(h, emission=0.040)
        go_sample(h, 0.0)
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=20.0)
        rid = spectrum(h, [region('Si 2p', 95.0, 108.0, 0.05, 0.1, 2)])
        be, y = arrays(h, rid, 'Si 2p')
        fit = h.invoke('data.fit', record_id=rid, region='Si 2p', shift_ev=0.0,
                       components=[dict(label='Si0 2p3/2', be_ev=float(be[int(np.argmax(y))]), fwhm_ev=0.5, fix_position=False),
                                   dict(label='Si0 2p1/2', be_ev=float(be[int(np.argmax(y))]) + 0.6, fwhm_ev=0.5, fix_position=False),
                                   dict(label='SiO2', be_ev=float(be[int(np.argmax(y))]) + 4.0, fwhm_ev=1.4, fix_position=False)])
        c = {x['label']: x for x in fit['components']}
        self.assertAlmostEqual(c['Si0 2p1/2']['be_ev'] - c['Si0 2p3/2']['be_ev'], 0.6, delta=0.12)      # spin-orbit splitting (xraydb levels)
        self.assertAlmostEqual(c['SiO2']['be_ev'] - c['Si0 2p3/2']['be_ev'], 103.4 - 99.31, delta=0.3)  # NIST SRD 20 chemical shift
        R = c['SiO2']['area_cps_ev'] / (c['Si0 2p3/2']['area_cps_ev'] + c['Si0 2p1/2']['area_cps_ev'])
        lam = rt.imfp_nm(rt.substance('sio2'), rt.HV_EV - 103.4)
        n_si_ox = rt.composition_atoms(rt.substance('sio2'))['Si']
        n_si = rt.composition_atoms(rt.substance('si'))['Si']
        d = lam * math.log(1 + R * n_si / n_si_ox)                      # standard overlayer formula (attenuation length ~ IMFP, normal emission)
        self.assertAlmostEqual(d, 4.0, delta=1.2)

    def test_quantification_of_sputter_cleaned_oxides(self):
        h = ready(seed=8)
        provide(h, 'tio2', 'tio2', thick=1e-3)
        on_stage(h, [('tio2', 0.0)])
        beam(h, emission=0.040)
        go_sample(h, 0.0)
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=40.0)
        regs = [region('Ti 2p', 450.0, 470.0, 0.1, 0.05, 1), region('O 1s', 524.0, 540.0, 0.1, 0.05, 1), region('C 1s', 280.0, 294.0, 0.1, 0.05, 1)]
        rid0 = spectrum(h, regs)
        q0 = {r['element']: r['atomic_percent'] for r in h.invoke('data.quantify', record_id=rid0, regions=['Ti 2p', 'O 1s', 'C 1s'], shift_ev=0.0)['results']}
        self.assertGreater(q0['C'], 8.0)                                # adventitious carbon on the air-exposed surface
        h.invoke('ion_gun.set', on=True, energy_ev=3000.0, raster_m=0.003)
        wait(h, h.invoke('ion_gun.etch', duration_s=120.0)['operation_id'])
        rid1 = spectrum(h, regs)
        q1 = {r['element']: r['atomic_percent'] for r in h.invoke('data.quantify', record_id=rid1, regions=['Ti 2p', 'O 1s', 'C 1s'], shift_ev=0.0)['results']}
        self.assertLess(q1['C'], 1.5)
        self.assertAlmostEqual(q1['O'] / q1['Ti'], 2.0, delta=0.5)     # theoretical sensitivity factors, some preferential oxygen loss

    def test_angle_resolved_surface_enhancement(self):
        h = ready(seed=9)
        provide(h, 'ox', 'si', layers=[('sio2', 2.0e-9)])
        on_stage(h, [('ox', 0.0)])
        beam(h, emission=0.040)
        ratios = []
        for tilt in (0.0, math.radians(60.0)):
            go_sample(h, 0.0, tilt=tilt)
            rid = spectrum(h, [region('Si 2p', 95.0, 108.0, 0.1, 0.05, 1)])
            be, y = arrays(h, rid, 'Si 2p')
            ox = y[(be > 102.5) & (be < 106.0)].sum()
            bulk = y[(be > 98.0) & (be < 101.5)].sum()
            ratios.append(ox / bulk)
        self.assertGreater(ratios[1], 1.8 * ratios[0])

    def test_small_spots_scale_with_area_and_snapshot_window(self):
        h = ready(seed=10)
        provide(h, 'au', 'au')
        on_stage(h, [('au', 0.0)])
        beam(h, emission=0.005)                                     # 75 W: far below the DLD saturation
        go_sample(h, 0.0)
        tops = {}
        for ap in ('slot', 'd110', 'd27'):
            h.invoke('analyser.configure', lens_mode='hybrid', aperture=ap, pass_energy_ev=10.0)
            tops[ap] = float(h._spectrum_model(np.arange(82.0, 86.0, 0.05), 0.05).max())
        area = {'slot': 700e-6 * 300e-6, 'd110': math.pi * 55e-6 ** 2, 'd27': math.pi * 13.5e-6 ** 2}
        self.assertAlmostEqual(tops['d110'] / tops['slot'], area['d110'] / area['slot'], delta=0.25 * area['d110'] / area['slot'])
        self.assertLess(tops['d27'], tops['d110'])
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=40.0)
        rejected(h, 'acquisition.spectrum', 'REGION_EXCEEDS_SNAPSHOT_WINDOW', regions=[region('Au', 78.0, 90.0)], mode='snapshot')
        rid = spectrum(h, [dict(name='Au snap', start_be_ev=81.0, end_be_ev=87.0, step_ev=0.1, dwell_s=1.0, sweeps=1)], mode='snapshot')
        be, y = arrays(h, rid, 'Au snap')
        self.assertAlmostEqual(be[1] - be[0], 0.18 * 40.0 / 128, places=9)
        self.assertLess(abs(be[int(np.argmax(y))] - 84.0), 0.6)


class DepthProfile(unittest.TestCase):
    def test_ta2o5_on_ta_profile_reduction_and_argon(self):
        h = ready(seed=11)
        provide(h, 'bcr', 'ta', layers=[('ta2o5', 30.0e-9)])
        on_stage(h, [('bcr', 0.0)])
        beam(h, emission=0.040)
        go_sample(h, 0.0)
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=80.0)
        h.invoke('ion_gun.set', on=True, energy_ev=4000.0, raster_m=0.003)
        regs = [region('Ta 4f', 18.0, 34.0, 0.2, 0.02, 1), region('O 1s', 524.0, 540.0, 0.2, 0.02, 1)]    # (Ar 2p overlaps Ta 4d3/2)
        op = h.invoke('depth_profile.start', levels=14, etch_s=90.0, regions=regs)['operation_id']
        self.assertEqual(wait(h, op), 'completed')
        rid = h.operations[op]['record_id']
        tab = h.invoke('data.depth_profile', record_id=rid, shift_ev=0.0)
        els = tab['elements']
        o = np.array([row[els.index('O')] for row in tab['atomic_percent']])
        self.assertGreater(o[1], 50.0)                                  # the oxide (Ta2O5: 71 at% O, with preferential O loss and sensitivity factors)
        self.assertLess(o[-1], 10.0)                                    # the metal below
        cr = h.samples['bcr']['craters'][0]
        j = rt.ion_current_a(4000.0) / rt.E_CHARGE / 0.003 ** 2
        rate = rt.sputter_yield(rt.substance('ta2o5'), 4000.0) * j / rt.atoms_per_m3(rt.substance('ta2o5'))
        t_half = tab['etch_time_s'][int(np.argmax(o < 0.5 * o[1]))]
        self.assertAlmostEqual(t_half, 30.0e-9 / rate, delta=0.35 * 30.0e-9 / rate)
        self.assertGreater(cr['reduction'], 0.1)
        self.assertGreater(cr['implant'], 0.005)
        led = h.ledger()
        self.assertLess(abs(led['sputter_residual_atoms']), 1e-6 * led['sputtered_atoms'])
        self.assertGreater(led['argon_implanted_mol'], 0.0)
        self.assertLess(abs(led['argon_residual_mol']), 1e-9 * max(led['argon_admitted_mol'], 1e-12) + 1e-18)


class Imaging(unittest.TestCase):
    def test_copper_grid_image_pitch_and_resolution(self):
        h = ready(seed=12)
        provide(h, 'grid', 'si', pattern=dict(kind='grid', feature_substance_id='cu', feature_thickness_m=50e-9, pitch_m=50e-6, width_m=12e-6, coverage=None))
        on_stage(h, [('grid', 0.0)])
        beam(h, emission=0.040)
        go_sample(h, 0.0)
        op = h.invoke('acquisition.image', be_ev=932.6, fov='fov400', pass_energy_ev=160.0, duration_s=120.0)['operation_id']
        self.assertEqual(wait(h, op), 'completed')
        img = np.array(h.records[h.operations[op]['record_id']]['image']['counts'], float)
        prof = img[40:88, :].mean(0)
        f = np.abs(np.fft.rfft(prof - prof.mean()))
        k = int(np.argmax(f[2:])) + 2
        pitch = 400e-6 / k
        self.assertAlmostEqual(pitch, 50e-6, delta=3e-6)
        self.assertGreater(img.max(), 5.0)
        op = h.invoke('acquisition.image', be_ev=932.6, fov='fov80', pass_energy_ev=160.0, duration_s=60.0)['operation_id']
        wait(h, op)
        im = h.records[h.operations[op]['record_id']]['image']
        self.assertAlmostEqual(im['resolution_m'], 1.25e-6, delta=0.3e-6)


class LedgersIsolationSessions(unittest.TestCase):
    def test_ledgers_close(self):
        h = ready(seed=13)
        provide(h, 'pet', 'pet', thick=1.0e-4)
        on_stage(h, [('pet', 0.0)])
        beam(h)
        go_sample(h, 0.0)
        h.invoke('neutraliser.set', on=True, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)
        h.step(30.0)
        h.invoke('ion_gun.set', on=True, energy_ev=1000.0, raster_m=0.004)
        wait(h, h.invoke('ion_gun.etch', duration_s=30.0)['operation_id'])
        h.invoke('ion_gun.set', on=False, energy_ev=1000.0, raster_m=0.004)
        h.invoke('flexilock.vent')
        h.step(200.0)
        led = h.ledger()
        self.assertGreater(led['charge_neutraliser_c'], 0.0)
        self.assertLess(abs(led['charge_residual_c']), 1e-6 * led['charge_photoemitted_c'])
        self.assertLess(abs(led['nitrogen_residual_mol']), 1e-9 * led['nitrogen_admitted_mol'])
        self.assertLess(abs(led['argon_residual_mol']), 1e-9 * led['argon_admitted_mol'])
        self.assertLess(abs(led['sputter_residual_atoms']), 1e-6 * led['sputtered_atoms'])
        self.assertGreater(led['anode_energy_j'], 0.0)

    def test_two_instances_are_isolated(self):
        a, b = ready(seed=1), ready(seed=2)
        provide(a, 'x', 'au')
        self.assertIn('x', a.samples)
        self.assertNotIn('x', b.samples)
        self.assertNotEqual(a.scale_true, b.scale_true)

    def test_agent_does_not_see_truth_or_faults(self):
        h = ready()
        h.register_principal('a', 'agent')
        h.register_principal('e', 'evaluator')
        s, e = h.session('a'), h.session('e')
        obs = s.read(instance_id=h.instance_id)['observations']
        self.assertFalse(any(k.startswith('truth.') for k in obs))
        with self.assertRaises(rt.ActionRejected):
            s.read(instance_id=h.instance_id, state_ids=['truth.scale_error'])
        r = s.invoke(instance_id=h.instance_id, capability_id='fault.inject', arguments=dict(fault='chiller_failure', active=True), request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        e.invoke(instance_id=h.instance_id, capability_id='fault.inject', arguments=dict(fault='chiller_failure', active=True), request_id='f2')
        ev = s.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e1')['result']['events']
        self.assertFalse(any(x['kind'] == 'fault' for x in ev))
        desc = s.describe(instance_id=h.instance_id)
        self.assertNotIn('truth.samples', desc['state_slots'])
        self.assertNotIn('ledger.read', desc['capabilities'])

    def test_records_and_control_belong_to_their_agent(self):
        h = ready()
        provide(h, 'au', 'au')
        on_stage(h, [('au', 0.0)])
        beam(h)
        go_sample(h, 0.0)
        h.register_principal('a', 'agent')
        h.register_principal('b', 'agent')
        sa, sb = h.session('a'), h.session('b')
        r = sa.invoke(instance_id=h.instance_id, capability_id='acquisition.spectrum', arguments=dict(regions=[region('Au', 80.0, 90.0, 0.1, 0.05, 1)],
                                                                                                         mode='scanned'), request_id='s1')
        self.assertEqual(r['status'], 'accepted')
        rb = sb.invoke(instance_id=h.instance_id, capability_id='stage.move', arguments=dict(x_m=0.0, y_m=0.0, z_m=0.0, tilt_rad=0.0, rotation_rad=0.0),
                       request_id='m1')
        self.assertEqual(rb['error']['code'], 'ACCESS_DENIED')
        self.assertTrue(sb.read(instance_id=h.instance_id)['observations']['acquisition.started_by_other_agent']['value'])
        wait(h, r['operation_id'])
        rid = h.operations[r['operation_id']]['record_id']
        rb = sb.invoke(instance_id=h.instance_id, capability_id='acquisition.result', arguments=dict(record_id=rid), request_id='r1')
        self.assertEqual(rb['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(sa.read(instance_id=h.instance_id)['observations']['result.last_record_id']['value'], rid)
        again = sa.invoke(instance_id=h.instance_id, capability_id='acquisition.spectrum', arguments=dict(regions=[region('Au', 80.0, 90.0, 0.1, 0.05, 1)],
                                                                                                             mode='scanned'), request_id='s1')
        self.assertEqual(again['operation_id'], r['operation_id'])
        conflict = sa.invoke(instance_id=h.instance_id, capability_id='acquisition.spectrum', arguments=dict(regions=[region('Au', 80.0, 91.0, 0.1, 0.05, 1)],
                                                                                                                mode='scanned'), request_id='s1')
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')

    def test_cancel_keeps_finished_regions_and_power_off_fails(self):
        h = ready()
        provide(h, 'au', 'au')
        on_stage(h, [('au', 0.0)])
        beam(h)
        go_sample(h, 0.0)
        h.register_principal('a', 'agent')
        s = h.session('a')
        r = s.invoke(instance_id=h.instance_id, capability_id='acquisition.spectrum',
                     arguments=dict(regions=[region('Au', 80.0, 90.0, 0.1, 0.05, 1), region('C', 280.0, 292.0, 0.1, 0.5, 2)], mode='scanned'), request_id='s1')
        h.step(30.0)
        s.cancel(r['operation_id'], 'c1')
        rec = h.records[h.operations[r['operation_id']]['record_id']]
        self.assertEqual(rec['status'], 'cancelled')
        self.assertEqual([x['name'] for x in rec['spectra']], ['Au'])
        op = h.invoke('acquisition.spectrum', regions=[region('Au', 80.0, 90.0, 0.1, 0.5, 2)], mode='scanned')['operation_id']
        h.step(10.0)
        h.invoke('power.set', enabled=False)
        self.assertEqual(h.operations[op]['phase'], 'failed')
        self.assertEqual(h.records[h.operations[op]['record_id']]['reason'], 'POWER_OFF')

    def test_queries_and_bookkeeping(self):
        h = ready()
        provide(h, 'au', 'au')
        on_stage(h, [('au', 0.0)])
        beam(h)
        go_sample(h, 0.0)
        rid = spectrum(h, [region('survey', 0.0, 1200.0, 1.0, 0.02, 1)])
        ids = h.invoke('data.identify', record_id=rid, region='survey', shift_ev=0.0)
        self.assertIn('Au', ids['elements'])
        self.assertIn('C', ids['elements'])
        self.assertIn(rid, h.invoke('records.list')['records'])
        self.assertEqual(h.invoke('acquisition.result', record_id=rid)['status'], 'completed')
        rejected(h, 'data.quantify', 'UNKNOWN_REGION', record_id=rid, regions=['nope'], shift_ev=0.0)
        rejected(h, 'acquisition.result', 'UNKNOWN_RECORD', record_id='SP9999')
        prov = h.invoke('provenance.read')
        self.assertIn('au', prov['substances'])
        self.assertEqual(prov['library'], rt.LIBRARY_REL)



class HoldersFaradayCupAndHostCalls(unittest.TestCase):
    def test_holder_round_trip_faraday_cup_current_ledger_and_host_step(self):
        h = ready(seed=3)
        provide(h, 'au', 'au')
        provide(h, 'cu', 'cu')
        h.invoke('holder.mount_sample', holder_id='H1', sample_id='au', x_m=0.0, y_m=0.0)
        h.invoke('holder.mount_sample', holder_id='H1', sample_id='cu', x_m=0.02, y_m=0.0)
        out = h.invoke('holder.unmount_sample', holder_id='H1', sample_id='cu')
        self.assertEqual(out['location'], 'bench')
        self.assertIn('cu', h.read()['sample.bench_ids'])
        self.assertNotIn('cu', h.holders['H1']['samples'])
        on_stage(h, [])
        h.invoke('ion_gun.set', on=True, energy_ev=2000.0, raster_m=0.004)
        rejected(h, 'ion_gun.measure_current', 'NOT_ON_FARADAY_CUP')
        h.invoke('stage.go_to', position='reference:faraday_cup')
        h.step(60.0)
        m = h.invoke('ion_gun.measure_current')
        i_true = rt.ion_current_a(2000.0)
        self.assertAlmostEqual(m['current_a'], i_true, delta=0.05 * i_true)
        self.assertAlmostEqual(m['current_density_a_m2'], m['current_a'] / 0.004 ** 2, delta=1e-9 * m['current_density_a_m2'])
        h.invoke('ion_gun.set', on=False, energy_ev=2000.0, raster_m=0.004)
        wait(h, h.invoke('transfer.to_magazine', slot=1)['operation_id'])
        self.assertEqual(h.magazine[1], 'H1')
        h.invoke('flexilock.vent')
        h.step(90.0)
        h.invoke('flexilock.set_lid', open=True)
        out = h.invoke('flexilock.unload_holder', slot=1)
        self.assertEqual((out['holder_id'], h.holders['H1']['location'], h.magazine[1]), ('H1', 'bench', None))
        self.assertGreater(h.samples['au']['contamination_m'], 0.0)                    # back in laboratory air
        led = h.invoke('ledger.read')
        self.assertGreater(led['argon_admitted_mol'], 0.0)
        self.assertLess(abs(led['argon_residual_mol']), 1e-9 * led['argon_admitted_mol'])
        self.assertLess(abs(led['nitrogen_residual_mol']), 1e-9 * led['nitrogen_admitted_mol'])
        t0 = h.time_s
        self.assertAlmostEqual(h.invoke('sim.step', dt_s=2.5)['time_s'], t0 + 2.5, places=9)
        self.assertAlmostEqual(h.time_s, t0 + 2.5, places=9)


def _chunks(T):
    n = int(T / 0.37)
    return [0.37] * n + [T - 0.37 * n]


class ReviewRound1Regressions(unittest.TestCase):
    """Regression tests for the findings of the first independent runtime review (F01-F13)."""

    def test_f01_f02_gas_ledgers_close_through_transfers_with_argon_and_the_open_lid(self):
        h = ready(seed=31)
        provide(h, 'au', 'au')
        on_stage(h, [('au', 0.0)])
        h.invoke('ion_gun.set', on=True, energy_ev=3000.0, raster_m=0.003)          # argon in the analysis chamber
        h.step(30.0)
        wait(h, h.invoke('transfer.to_magazine', slot=1)['operation_id'])          # the gate burst shares the chamber argon with the Flexi-lock
        led = h.ledger()
        self.assertLess(abs(led['argon_residual_mol']), 1e-9 * led['argon_admitted_mol'])
        wait(h, h.invoke('transfer.to_stage', slot=1)['operation_id'])             # a second burst with argon already in the Flexi-lock
        wait(h, h.invoke('transfer.to_magazine', slot=1)['operation_id'])
        h.invoke('ion_gun.set', on=False, energy_ev=3000.0, raster_m=0.003)
        h.invoke('flexilock.vent')
        h.step(90.0)
        h.invoke('flexilock.set_lid', open=True)
        h.step(5.0)                                                                # Flexi-lock nitrogen and argon escape into the laboratory
        led = h.ledger()
        self.assertGreater(led['nitrogen_released_mol'], 0.0)
        self.assertLess(abs(led['nitrogen_residual_mol']), 1e-9 * led['nitrogen_admitted_mol'])
        self.assertLess(abs(led['argon_residual_mol']), 1e-9 * led['argon_admitted_mol'])

    def test_f04_interrupted_transfer_keeps_the_gate_open_and_recovers(self):
        h = ready(seed=32)
        provide(h, 'au', 'au')
        h.invoke('holder.mount_sample', holder_id='H1', sample_id='au', x_m=0.0, y_m=0.0)
        h.invoke('flexilock.set_lid', open=True)
        h.invoke('flexilock.load_holder', holder_id='H1', slot=1)
        h.invoke('flexilock.set_lid', open=False)
        h.invoke('flexilock.pump')
        for _ in range(200):
            h.step(10.0)
            if h.fl_mode == 'pumped':
                break
        op = h.invoke('transfer.to_stage', slot=1)['operation_id']
        while h.transfer_op['phase'] != 'arm_out':
            h.step(0.5)
        h.step(3.0)
        self.assertEqual(h.holders['H1']['location'], 'arm')
        h.invoke('power.set', enabled=False)
        h.step(10.0)
        self.assertGreater(h.gate, 0.08)                                           # the extended fork blocks the gate valve
        self.assertGreater(h.arm, 0.0)
        self.assertEqual(h.operations[op]['phase'], 'failed')
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1.0)
        self.assertEqual(h.error, 'TRANSFER_INTERRUPTED')
        rejected(h, 'system.acknowledge', 'CAUSE_PERSISTS')
        rejected(h, 'flexilock.vent', 'ERROR_PENDING')
        rejected(h, 'transfer.to_stage', 'ERROR_PENDING', slot=2)
        rop = h.invoke('transfer.recover')['operation_id']
        self.assertEqual(wait(h, rop), 'completed')
        self.assertEqual((h.stage_holder, h.holders['H1']['location'], h.error, h.gate, h.arm), ('H1', 'stage', None, 0.0, 0.0))
        rejected(h, 'transfer.recover', 'NO_INTERRUPTED_TRANSFER')

    def test_f05_snapshot_count_rates_do_not_depend_on_dwell(self):
        h = ready(seed=33)
        provide(h, 'au', 'au')
        on_stage(h, [('au', 0.0)])
        beam(h, emission=0.005)
        go_sample(h, 0.0)
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=40.0)
        means = []
        for dwell in (0.01, 5.0):
            rid = spectrum(h, [dict(name='Au', start_be_ev=81.0, end_be_ev=87.0, step_ev=0.1, dwell_s=dwell, sweeps=1)], mode='snapshot')
            be, y = arrays(h, rid, 'Au')
            means.append(float(y.mean()))
        self.assertAlmostEqual(means[0] / means[1], 1.0, delta=0.1)

    def test_f06_f07_host_chopping_while_moving_and_during_charging_transients(self):
        def run(chunks):
            h = ready(seed=34)
            h.rng = QuietRng()
            h.rng_acq = QuietRng()
            provide(h, 'p1', 'pet', thick=1e-4)
            provide(h, 'p2', 'al2o3', thick=1e-4)                      # sapphire: a second insulator that does not outgas
            on_stage(h, [('p1', -0.02), ('p2', 0.02)])
            beam(h, emission=0.040)
            h.invoke('stage.move', x_m=0.02, y_m=0.0, z_m=-1e-4, tilt_rad=0.0, rotation_rad=0.0)
            for dt in chunks(30.0):
                h.step(dt)
            h.invoke('stage.move', x_m=-0.02, y_m=0.0, z_m=-1e-4, tilt_rad=0.0, rotation_rad=0.0)   # the beam leaves p1 and reaches p2 while moving
            for dt in chunks(30.0):
                h.step(dt)
            h.invoke('neutraliser.set', on=True, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)
            h.invoke('acquisition.spectrum', regions=[region('C 1s', 280.0, 300.0, 0.05, 0.02, 1)], mode='scanned')
            for dt in chunks(4.0):
                h.step(dt)
            h.invoke('neutraliser.set', on=False, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)   # transient during the scan
            for dt in chunks(30.0):
                h.step(dt)
            return {s_: h.v_surface[s_] for s_ in ('p1', 'p2')}, h.records['SP0001'], h.ledger()
        (va, ra, la), (vb, rb, lb) = run(lambda T: [T]), run(_chunks)
        for k in va:
            self.assertAlmostEqual(va[k], vb[k], delta=1e-9 * max(1.0, abs(va[k])), msg=k)
        self.assertEqual(ra['status'], 'completed')
        ya, yb = np.array(ra['spectra'][0]['counts']), np.array(rb['spectra'][0]['counts'])
        self.assertLess(float(np.max(np.abs(ya - yb))), 1e-6 * float(ya.max()) + 1.0)
        self.assertAlmostEqual(la['photons_on_sample'], lb['photons_on_sample'], delta=1e-9 * la['photons_on_sample'])
        self.assertAlmostEqual(la['charge_photoemitted_c'], lb['charge_photoemitted_c'], delta=1e-9 * la['charge_photoemitted_c'])

    def test_f08_flexilock_state_changes_at_exact_crossings(self):
        def run(chunks):
            h = ready(seed=35)
            provide(h, 'au', 'au')
            h.invoke('holder.mount_sample', holder_id='H1', sample_id='au', x_m=0.0, y_m=0.0)
            h.invoke('flexilock.set_lid', open=True)
            h.invoke('flexilock.load_holder', holder_id='H1', slot=1)
            h.invoke('flexilock.set_lid', open=False)
            h.invoke('flexilock.pump')
            for dt in chunks(400.0):
                h.step(dt)
            h.invoke('flexilock.vent')
            for dt in chunks(60.0):
                h.step(dt)
            return {e['code']: e['time_s'] for e in h.events if e['code'] in ('FL_PUMPED', 'FL_VENTED')}, h
        (a, ha), (b, hb) = run(lambda T: [T]), run(_chunks)
        self.assertEqual(set(a), {'FL_PUMPED', 'FL_VENTED'})
        for k in a:
            self.assertAlmostEqual(a[k], b[k], delta=1e-6, msg=k)
        t_vent0 = next(e['time_s'] for e in ha.events if e['code'] == 'FL_VENTING')
        self.assertAlmostEqual(a['FL_VENTED'] - t_vent0, rt.VENT_TAU_S * math.log(1.0 / 0.02), delta=1e-3)   # from vacuum to 98 % of atmosphere

    def test_f10_combination_holder_limits_the_sample_thickness(self):
        h = configured()
        provide(h, 'thick', 'au', thick=0.010)
        rejected(h, 'holder.mount_sample', 'SAMPLE_TOO_THICK', holder_id='H3', sample_id='thick', x_m=0.0, y_m=0.0)
        h.invoke('holder.mount_sample', holder_id='H1', sample_id='thick', x_m=0.0, y_m=0.0)
        self.assertEqual((h.read()['holders']['H3']['type'], h.read()['holders']['H1']['type']), ('combination', 'dual_height'))

    def test_f12_spin_orbit_splittings_follow_photoemission_records(self):
        """Doublet partners follow paired NIST SRD 20 records (element default and per chemical state), not X-ray absorption-edge differences."""
        lib = json.loads(Path(rt.LIBRARY_PATH).read_text())
        so = {el: next(iter(e['spin_orbit'].values())) for el, e in lib['elements'].items() if e.get('spin_orbit')}
        self.assertAlmostEqual(so['Ag']['splitting_ev'], 6.0, delta=0.05)
        self.assertAlmostEqual(so['Cl']['splitting_ev'], 1.6, delta=0.05)                   # the L2-L3 edge difference is 2.0 eV
        nio = next(c for c in next(x for x in lib['substances'] if x['substance_id'] == 'nio')['components'] if c['element'] == 'Ni')
        self.assertGreater(nio['doublet']['splitting_ev'], so['Ni']['splitting_ev'] + 0.5)   # NiO 2p splitting exceeds the metal's
        h = ready(seed=36)
        provide(h, 'dummy', 'au')
        on_stage(h, [('dummy', 0.0)])
        beam(h, emission=0.040)
        clean_reference(h, 'Ag')
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='slot', pass_energy_ev=10.0)
        rid = spectrum(h, [region('Ag 3d', 364.0, 378.0, 0.02, 0.1, 1)])
        fit = h.invoke('data.fit', record_id=rid, region='Ag 3d', shift_ev=0.0, components=[
            dict(label='3d5/2', be_ev=368.2, fwhm_ev=0.5, fix_position=False), dict(label='3d3/2', be_ev=374.2, fwhm_ev=0.5, fix_position=False)])
        c = {x['label']: x for x in fit['components']}
        self.assertAlmostEqual(c['3d3/2']['be_ev'] - c['3d5/2']['be_ev'], 6.0, delta=0.06)
        self.assertAlmostEqual(c['3d3/2']['area_cps_ev'] / c['3d5/2']['area_cps_ev'], 2.0 / 3.0, delta=0.08)

    def test_f09_f11_texts_quote_the_implemented_numbers_and_label_related_model_data(self):
        texts = []

        def walk(o):
            if isinstance(o, str):
                texts.append(o)
            elif isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(_meta())
        joined = '\n'.join(texts)
        for stale in ('0.20 eV', '≈0.0085×通能（slot', '向后欧拉', 'backward Euler', '能散0.15 eV', '混合长度1 nm', '释放注入的氩'):
            self.assertNotIn(stale, joined)
        related = ('0.48 eV', '0.68 eV', '760 kcps', '44 kcps', '精度2 µm', '2 µm精度', '误差2 µm')
        bad = [t_ for t_ in texts if any(r in t_ for r in related) and '相关型号' not in t_]
        self.assertEqual(bad, [])


def _stranded(seed, cancel=False):
    """A to_stage transfer interrupted with the holder on the extended fork (power loss, or a user cancel)."""
    h = ready(seed=seed)
    provide(h, 'au', 'au')
    h.invoke('holder.mount_sample', holder_id='H1', sample_id='au', x_m=0.0, y_m=0.0)
    h.invoke('flexilock.set_lid', open=True)
    h.invoke('flexilock.load_holder', holder_id='H1', slot=1)
    h.invoke('flexilock.load_holder', holder_id='H2', slot=2)
    h.invoke('flexilock.set_lid', open=False)
    h.invoke('flexilock.pump')
    for _ in range(200):
        h.step(10.0)
        if h.fl_mode == 'pumped':
            break
    h.register_principal('op', 'agent')
    r = h.session('op').invoke(instance_id=h.instance_id, capability_id='transfer.to_stage', arguments=dict(slot=1), request_id='t1')
    while h.transfer_op['phase'] != 'arm_out':
        h.step(0.5)
    h.step(3.0)
    if cancel:
        h.session('op').cancel(r['operation_id'], 'c1')
    else:
        h.invoke('power.set', enabled=False)
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1.0)
    assert h.holders['H1']['location'] == 'arm' and h.transfer_stranded is not None
    return h


class ReviewRound2Regressions(unittest.TestCase):
    """Regression tests for the findings of the second independent runtime review (F08 remainder, F14-F16)."""

    def test_f14_the_stranded_fork_survives_other_errors(self):
        h = _stranded(41, cancel=True)
        self.assertEqual(h.error, 'TRANSFER_INTERRUPTED')
        h.error = 'COOLING_WATER_LOW'                                   # a later latched error (cause already gone) takes the slot
        self.assertEqual(h.invoke('system.acknowledge')['cleared'], 'COOLING_WATER_LOW')
        self.assertEqual(h.error, 'TRANSFER_INTERRUPTED')              # re-latched while the fork is stranded
        h.error = None                                                  # even with the slot cleared by hand the refusals stay
        rejected(h, 'transfer.to_stage', 'TRANSFER_INTERRUPTED', slot=2)
        rejected(h, 'flexilock.vent', 'TRANSFER_INTERRUPTED')
        rejected(h, 'flexilock.pump', 'TRANSFER_INTERRUPTED')
        rejected(h, 'stage.move', 'TRANSFER_INTERRUPTED', x_m=0.01, y_m=0.0, z_m=0.0, tilt_rad=0.0, rotation_rad=0.0)
        self.assertEqual(wait(h, h.invoke('transfer.recover')['operation_id']), 'completed')
        self.assertEqual((h.stage_holder, h.holders['H1']['location'], h.gate, h.transfer_stranded), ('H1', 'stage', 0.0, None))

    def test_f14_mains_fault_while_stranded(self):
        h = _stranded(42)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=170.0))
        self.assertEqual(h.error, 'MAINS_FAULT')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE))
        h.invoke('power.set', enabled=True)
        self.assertEqual(h.error, 'TRANSFER_INTERRUPTED')
        h.step(rt.BOOT_S + 1.0)
        rejected(h, 'transfer.to_stage', 'ERROR_PENDING', slot=2)
        self.assertEqual(wait(h, h.invoke('transfer.recover')['operation_id']), 'completed')
        self.assertEqual((h.stage_holder, h.error), ('H1', None))

    def test_f15_transfer_timeline_does_not_depend_on_host_chopping(self):
        def run(chunks):
            h = ready(seed=43)
            provide(h, 'au', 'au')
            h.invoke('holder.mount_sample', holder_id='H1', sample_id='au', x_m=0.0, y_m=0.0)
            h.invoke('flexilock.set_lid', open=True)
            h.invoke('flexilock.load_holder', holder_id='H1', slot=1)
            h.invoke('flexilock.set_lid', open=False)
            h.invoke('flexilock.pump')
            for dt in chunks(600.0):
                h.step(dt)
            op = h.invoke('transfer.to_stage', slot=1)['operation_id']
            for dt in chunks(40.0):
                h.step(dt)
            return h.operations[op]['finished_s'], [(e['code'], e['time_s']) for e in h.events if e['kind'] in ('transfer', 'flexilock')]
        (fa, ea), (fb, eb) = run(lambda T: [T]), run(_chunks)
        self.assertAlmostEqual(fa, fb, delta=1e-6)
        self.assertEqual([c for c, _ in ea], [c for c, _ in eb])
        for (c, ta), (_, tb) in zip(ea, eb):
            self.assertAlmostEqual(ta, tb, delta=1e-6, msg=c)

    def test_f16_neutraliser_only_charging_while_moving_does_not_depend_on_host_chopping(self):
        def run(chunks):
            h = ready(seed=44)
            provide(h, 'p1', 'pet', thick=1e-4)
            provide(h, 'p2', 'al2o3', thick=1e-4)
            on_stage(h, [('p1', -0.02), ('p2', 0.02)])
            h.invoke('neutraliser.set', on=True, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)     # X-ray off
            h.invoke('stage.move', x_m=0.02, y_m=0.0, z_m=-1e-4, tilt_rad=0.0, rotation_rad=0.0)
            for dt in chunks(15.0):
                h.step(dt)
            h.invoke('stage.move', x_m=-0.02, y_m=0.0, z_m=-1e-4, tilt_rad=0.0, rotation_rad=0.0)
            for dt in chunks(30.0):
                h.step(dt)
            return {s_: h.v_surface[s_] for s_ in ('p1', 'p2')}, h.ledger()['charge_neutraliser_c']
        (va, qa), (vb, qb) = run(lambda T: [T]), run(_chunks)
        for k in va:
            self.assertAlmostEqual(va[k], vb[k], delta=1e-9 * max(1.0, abs(va[k])), msg=k)
        self.assertAlmostEqual(qa, qb, delta=1e-9 * qa)

    def test_f08_empty_magazine_pump_down_crossings(self):
        def run(chunks):
            h = ready(seed=8)
            h.invoke('flexilock.pump')
            for dt in chunks(100.0):
                h.step(dt)
            h.invoke('flexilock.vent')
            for dt in chunks(100.0):
                h.step(dt)
            return {e['code']: e['time_s'] for e in h.events if e['code'] in ('FL_PUMPED', 'FL_VENTED')}
        a, b = run(lambda T: [T]), run(_chunks)
        self.assertEqual(set(a), {'FL_PUMPED', 'FL_VENTED'})
        for k in a:
            self.assertAlmostEqual(a[k], b[k], delta=1e-6, msg=k)

# ---------------------------------------------------------------- declared effects (scenario states for the effect discovery)
RNGS = ['rng', 'rng_acq', 'rng_mount', 'rng_cal', 'rng_sample']
_SC = {}


def _bench(h):
    """Samples on the bench: SiO2/Si, Ta2O5/Ta, PET (insulator) and a gold foil; SiO2/Si, Ta2O5/Ta and PET on holder H1."""
    provide(h, 'sio2', 'si', [('sio2', 4.0e-9)], thick=5.25e-4)
    provide(h, 'ta', 'ta', [('ta2o5', 30.0e-9)], thick=5.0e-4)
    provide(h, 'pet', 'pet', thick=1.0e-4)
    provide(h, 'au', 'au', form='foil', thick=1.0e-4)
    for sid, x in (('sio2', -0.025), ('ta', 0.0), ('pet', 0.025)):
        h.invoke('holder.mount_sample', holder_id='H1', sample_id=sid, x_m=x, y_m=0.0)
    return h


def _pumped(h, tmax=3000.0):
    for _ in range(int(tmax / 10.0)):
        h.step(10.0)
        if h.fl_mode == 'pumped':
            return h
    raise AssertionError(h.p_fl)


def _build(name):
    if name == 'fresh':
        return rt.KratosAxissupraRuntime()
    if name == 'off':
        return _bench(configured())
    if name == 'booting':
        h = _scenario('off')
        h.invoke('power.set', enabled=True)
        h.step(10.0)
        return h
    if name == 'ready':
        h = _scenario('off')
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1.0)
        return h
    if name == 'lid_open':
        h = _scenario('ready')
        h.invoke('flexilock.set_lid', open=True)
        h.invoke('flexilock.load_holder', holder_id='H2', slot=2)
        return h
    if name == 'vented_loaded':
        h = _scenario('lid_open')
        h.invoke('flexilock.load_holder', holder_id='H1', slot=1)
        h.invoke('flexilock.set_lid', open=False)
        return h
    if name == 'fl_pumping':
        h = _scenario('vented_loaded')
        h.invoke('flexilock.pump')
        h.step(30.0)
        return h
    if name == 'fl_pumped':
        h = _scenario('vented_loaded')
        h.invoke('flexilock.pump')
        return _pumped(h)
    if name == 'fl_venting':
        h = _scenario('fl_pumped')
        h.invoke('flexilock.vent')
        h.step(5.0)
        return h
    if name == 'fl_leak':
        h = _scenario('fl_pumped')
        h.invoke('fault.inject', fault='flexilock_leak', active=True)
        h.step(20.0)
        return h
    if name == 'transferring':
        h = _scenario('fl_pumped')
        h.invoke('transfer.to_stage', slot=1)
        h.step(8.0)
        return h
    if name == 'on_stage':
        h = _scenario('fl_pumped')
        wait(h, h.invoke('transfer.to_stage', slot=1)['operation_id'])
        return h
    if name == 'to_magazine':
        h = _scenario('on_stage')
        h.invoke('transfer.to_magazine', slot=1)
        h.step(8.0)
        return h
    if name == 'arm_stranded':
        h = _scenario('fl_pumped')
        op = h.invoke('transfer.to_stage', slot=1)['operation_id']
        while h.transfer_op is not None and h.transfer_op['phase'] != 'arm_out':
            h.step(0.5)
        h.step(1.0)
        h.invoke('power.set', enabled=False)
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1.0)
        assert h.operations[op]['phase'] == 'failed'
        return h
    if name == 'arm_stranded_outage':           # contract review r2: a 10 s outage lets both chambers drift before the fork is recovered
        h = _scenario('fl_pumped')
        op = h.invoke('transfer.to_stage', slot=1)['operation_id']
        while h.transfer_op is not None and h.transfer_op['phase'] != 'arm_out':
            h.step(0.5)
        h.step(3.0)
        h.invoke('power.set', enabled=False)
        h.step(10.0)
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1.0)
        assert h.operations[op]['phase'] == 'failed' and h.transfer_stranded is not None
        return h
    if name == 'unpowered_on_stage':
        h = _scenario('on_stage')
        h.invoke('power.set', enabled=False)
        return h
    if name == 'xray_ramping':
        h = _scenario('on_stage')
        h.invoke('xray.set_source', on=True, emission_a=0.015)
        h.step(2.0)
        return h
    if name == 'beam':
        h = beam(_scenario('on_stage'))
        return go_sample(h, -0.025)
    if name == 'beam_ion':
        h = _scenario('beam')
        h.step(3000.0)                    # PET outgassing decays first: argon on top of it would trip the X-ray vacuum interlock (1e-5 Pa)
        h.invoke('ion_gun.set', on=True, energy_ev=3000.0, raster_m=0.003)
        return h
    if name == 'faraday':
        h = _scenario('beam_ion')
        h.invoke('stage.go_to', position='reference:faraday_cup')
        h.step(60.0)
        return h
    if name == 'stage_moving':
        h = _scenario('beam')
        h.invoke('stage.move', x_m=0.0, y_m=0.0, z_m=-5.0e-4, tilt_rad=0.3, rotation_rad=0.2)
        h.step(1.0)
        return h
    if name == 'acquiring':
        h = _scenario('beam')
        h.invoke('acquisition.spectrum', regions=[region('Si 2p', 96.0, 108.0, 0.1, 0.1, 1), region('O 1s', 526.0, 538.0, 0.1, 0.1, 1)], mode='scanned')
        h.step(3.0)
        return h
    if name == 'snapshot':
        h = _scenario('beam')
        h.invoke('acquisition.spectrum', regions=[region('Si 2p', 98.0, 101.0, 0.05, 2.0, 3)], mode='snapshot')
        h.step(1.5)
        return h
    if name == 'imaging':
        h = _scenario('beam')
        h.invoke('acquisition.image', be_ev=103.3, fov='fov400', pass_energy_ev=160.0, duration_s=60.0)
        h.step(5.0)
        return h
    if name == 'auto_height':
        h = _scenario('beam')
        h.invoke('stage.auto_height')
        h.step(2.0)
        return h
    if name == 'etching':
        h = _scenario('beam_ion')
        h.invoke('ion_gun.etch', duration_s=60.0)
        h.step(5.0)
        return h
    if name == 'depth_profiling':
        h = _scenario('beam_ion')
        go_sample(h, 0.0)
        h.invoke('depth_profile.start', levels=3, etch_s=20.0, regions=[region('Ta 4f', 20.0, 34.0, 0.2, 0.05, 1), region('O 1s', 526.0, 538.0, 0.2, 0.05, 1)])
        h.step(5.0)
        return h
    if name == 'depth_profiling_etch':          # contract review r2: an etch stage of the depth profile (beam on)
        h = _scenario('depth_profiling')
        while not h.etching:
            h.step(1.0)
        h.step(2.0)
        assert h.etching and h.acq['kind'] == 'depth_profile'
        return h
    if name == 'calibrating_etch':              # contract review r2: the sputter clean of a reference sample during the calibration (beam on)
        h = _scenario('calibrating')
        while not h.etching:
            h.step(1.0)
        h.step(2.0)
        assert h.etching and h.acq['kind'] == 'calibration'
        return h
    if name == 'calibrating':
        h = _scenario('beam_ion')
        h.invoke('calibration.energy_scale')
        h.step(5.0)
        return h
    if name == 'calibrating_d110':
        h = _scenario('beam_ion')
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='d110', pass_energy_ev=40.0)
        h.invoke('calibration.energy_scale')
        h.step(5.0)
        return h
    if name == 'beam_ion_d110':
        h = _scenario('beam_ion')
        h.invoke('analyser.configure', lens_mode='hybrid', aperture='d110', pass_energy_ev=40.0)
        return h
    if name == 'fl_pumped_stage_away':
        h = _scenario('fl_pumped')
        h.invoke('stage.move', x_m=0.020, y_m=0.005, z_m=-1.0e-3, tilt_rad=0.2, rotation_rad=0.3)
        h.step(60.0)
        return h
    if name == 'xray_tripped':
        h = _scenario('beam')
        h.invoke('ion_gun.set', on=True, energy_ev=3000.0, raster_m=0.003)
        h.step(5.0)
        h.invoke('ion_gun.set', on=False, energy_ev=3000.0, raster_m=0.003)
        h.step(30.0)
        assert h.xray_fault == 'VACUUM_INTERLOCK' and not h.xray_on and h.p_sac < rt.XRAY_MAX_PA
        return h
    if name == 'fl_nearly_vented':
        h = _scenario('fl_pumped')
        h.invoke('flexilock.vent')
        h.step(40.0)
        assert h.fl_mode == 'venting' and h.p_fl >= rt.LID_OPEN_MIN_PA
        return h
    if name == 'charging':
        h = _scenario('beam')
        go_sample(h, 0.025)
        return h
    if name == 'neutralised':
        h = _scenario('charging')
        h.invoke('neutraliser.set', on=True, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)
        h.step(30.0)
        return h
    if name == 'chiller_fault':
        h = _scenario('beam')
        h.invoke('fault.inject', fault='chiller_failure', active=True)
        h.step(2.0)
        return h
    if name == 'chiller_fault_cleared':
        h = _scenario('chiller_fault')
        h.invoke('fault.inject', fault='chiller_failure', active=False)
        h.step(2.0)
        return h
    if name == 'sac_leak':
        h = _scenario('beam')
        h.invoke('fault.inject', fault='sac_leak', active=True)
        h.step(30.0)
        return h
    if name == 'mains_fault':
        h = _scenario('beam')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=170.0))      # outside AC 200 V +-10 %: the system drops out
        assert h.error == 'MAINS_FAULT' and not h.power
        return h
    if name == 'mains_restored':
        h = _scenario('mains_fault')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE))
        return h
    if name == 'faults_measuring':
        h = _scenario('neutralised')
        h.invoke('acquisition.spectrum', regions=[region('C 1s', 280.0, 296.0, 0.1, 0.1, 1)], mode='scanned')
        for f in ('neutraliser_filament_failure', 'flexilock_leak'):
            h.invoke('fault.inject', fault=f, active=True)
        h.step(5.0)
        assert h.acq is not None
        return h
    if name == 'measured':
        h = _scenario('beam')
        spectrum(h, [region('Si 2p', 96.0, 108.0, 0.2, 0.05, 1)])
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'booting', 'ready', 'lid_open', 'vented_loaded', 'fl_pumping', 'fl_pumped', 'fl_venting', 'fl_leak', 'transferring', 'on_stage',
             'to_magazine', 'arm_stranded', 'unpowered_on_stage', 'xray_ramping', 'beam', 'beam_ion', 'faraday', 'stage_moving', 'acquiring', 'snapshot', 'imaging',
             'auto_height', 'etching', 'depth_profiling', 'calibrating', 'calibrating_d110', 'beam_ion_d110', 'fl_pumped_stage_away', 'xray_tripped',
             'fl_nearly_vented', 'charging', 'neutralised', 'chiller_fault', 'chiller_fault_cleared', 'sac_leak', 'mains_fault',
             'mains_restored', 'faults_measuring', 'measured', 'arm_stranded_outage', 'depth_profiling_etch', 'calibrating_etch']
STEP_DTS = (0.4, 1.0, 3.0, 15.0, 120.0)
_NEUT = dict(filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(room_k=300.15, mains_voltage_v=170.0, noise_seed=2)],
        'sample.provide': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')],
        'fault.inject': [dict(fault=f, active=True) for f in rt.FAULTS] + [dict(fault=f, active=False) for f in rt.FAULTS],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'system.acknowledge': [{}],
        'flexilock.vent': [{}], 'flexilock.pump': [{}], 'flexilock.set_lid': [dict(open=True), dict(open=False)],
        'holder.mount_sample': [dict(holder_id='H2', sample_id='au', x_m=0.0, y_m=0.0), dict(holder_id='H1', sample_id='au', x_m=0.0125, y_m=0.011)],
        'holder.unmount_sample': [dict(holder_id='H1', sample_id='sio2')],
        'flexilock.load_holder': [dict(holder_id='H1', slot=1), dict(holder_id='H3', slot=3)], 'flexilock.unload_holder': [dict(slot=1), dict(slot=2)],
        'transfer.to_stage': [dict(slot=1), dict(slot=2)], 'transfer.to_magazine': [dict(slot=1), dict(slot=3)], 'transfer.recover': [{}],
        'stage.move': [dict(x_m=0.0, y_m=0.0, z_m=-5.0e-4, tilt_rad=0.0, rotation_rad=0.0), dict(x_m=-0.025, y_m=0.002, z_m=-2.0e-4, tilt_rad=1.0, rotation_rad=0.5)],
        'stage.go_to': [dict(position='transfer'), dict(position='reference:Au'), dict(position='reference:faraday_cup')],
        'stage.auto_height': [{}],
        'xray.set_source': [dict(on=True, emission_a=0.015), dict(on=True, emission_a=0.040), dict(on=False, emission_a=0.015)],
        'analyser.configure': [dict(lens_mode='hybrid', aperture='slot', pass_energy_ev=20.0), dict(lens_mode='electrostatic', aperture='d110', pass_energy_ev=160.0)],
        'neutraliser.set': [dict(on=True, **_NEUT), dict(on=False, **_NEUT), dict(on=True, filament_current_a=1.0, charge_balance_v=5.0, filament_bias_v=2.0)],
        'ion_gun.set': [dict(on=True, energy_ev=4000.0, raster_m=0.003), dict(on=False, energy_ev=500.0, raster_m=0.002)],
        'ion_gun.etch': [dict(duration_s=30.0)], 'ion_gun.measure_current': [{}],
        'acquisition.spectrum': [dict(regions=[region('C 1s', 280.0, 296.0, 0.1, 0.1, 1), region('O 1s', 526.0, 538.0, 0.2, 0.05, 1)], mode='scanned'),
                                 dict(regions=[region('Si 2p', 98.0, 101.0, 0.05, 0.5, 2)], mode='snapshot')],
        'acquisition.image': [dict(be_ev=932.6, fov='fov400', pass_energy_ev=160.0, duration_s=20.0), dict(be_ev=103.3, fov='fov200', pass_energy_ev=80.0, duration_s=20.0)],
        'depth_profile.start': [dict(levels=2, etch_s=10.0, regions=[region('O 1s', 526.0, 538.0, 0.2, 0.05, 1)])],
        'calibration.energy_scale': [{}],
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


def _rel_changed(a, b, scale=None):
    """Relative comparison (no absolute floor)."""
    if _num(a) and _num(b):
        return abs(a - b) > 1e-9 * (max(abs(a), abs(b)) if scale is None else scale) + 1e-300
    return _changed(a, b)


def _leaf_changed(a, b):
    fa, fb = _flat(a), _flat(b)
    return any(_rel_changed(fa.get(k, '<absent>'), fb.get(k, '<absent>')) for k in set(fa) | set(fb))


def _step_differs(v1, a2, v0, b2):
    """Does the step change the value differently with the call (v1 -> a2) than without it (v0 -> b2)?  (leaf-wise, numeric increments)"""
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
        if ca and cb and x1 == x0 and not _rel_changed(xa, xb):        # a value that appears during the step: equal up to float rounding of the cuts
            continue
        return True
    return False


class DeclaredEffects(unittest.TestCase):
    def test_immediate_and_on_step_effects_are_declared(self):
        """For every capability accepted in any scenario state: the slots it changes at once are declared effects, the slots whose change during the
        following step differs from an identical run without the call are declared on_step effects, and the step itself only changes slots declared
        as on_step effects of sim.step (noise-free and noisy runs; both runs draw identical noise during the step)."""
        import random
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
                                if _leaf_changed(v0[k], v1[k]) and k not in rt.ON_STEP['sim.step'] + rt.EFFECTS['sim.step']:
                                    missing_step.setdefault('sim.step', set()).add(k)
                            continue
                        for k in v0:
                            if _changed(v0[k], v1[k]) and _leaf_changed(v0[k], v1[k]) and k not in rt.EFFECTS[cap]:
                                missing_imm.setdefault(cap, set()).add((sname, k))
                        for dt in STEP_DTS:
                            A2, B2 = copy.deepcopy(A), copy.deepcopy(S)
                            for name in RNGS:
                                setattr(A2, name, copy.deepcopy(getattr(B2, name)))
                            A2.step(dt)
                            B2.step(dt)
                            a2, b2 = A2.read('evaluator'), B2.read('evaluator')
                            for k in v0:
                                if k not in rt.ON_STEP.get(cap, []) and _step_differs(v1[k], a2[k], v0[k], b2[k]):
                                    missing_step.setdefault(cap, set()).add((sname, k))
                                if k not in rt.ON_STEP['sim.step'] + ['clock.time_s'] and _leaf_changed(v0[k], b2[k]):
                                    missing_step.setdefault('sim.step', set()).add((sname, k))
        self.assertEqual(missing_imm, {})
        self.assertEqual(missing_step, {})
        self.assertFalse([s for v in rt.ON_STEP.values() for s in v if rt.SLOTS[s]['origin'] == 'setpoint'])

    def test_every_device_capability_is_accepted_in_some_scenario(self):
        """The discovery covers every executable capability (else its declared effects would be untested)."""
        accepted = set()
        for n in SCENARIOS:
            S = _scenario(n)
            for cap in rt.CAPS:
                if cap in rt.UNSUPPORTED or rt.CAPS[cap]['kind'] in ('query', 'analysis') or cap in accepted:
                    continue
                for args in _variants(cap):
                    try:
                        _orig_invoke(copy.deepcopy(S), cap, **args)
                        accepted.add(cap)
                        break
                    except rt.ActionRejected:
                        pass
        todo = {c for c in rt.CAPS if c not in rt.UNSUPPORTED and rt.CAPS[c]['kind'] not in ('query', 'analysis')}
        self.assertEqual(todo - accepted, set())



class ContractReviewRound1Regressions(unittest.TestCase):
    def _follow(self, sname, cap, args, chunk=2.0, tmax=3600.0, tail=30.0):
        """Run the call to completion (plus a tail) against an identical control run, comparing at every chunk boundary; returns the slots whose
        evolution differs (transient changes included) and the control run's own changes."""
        S = _scenario(sname)
        for name in RNGS:
            setattr(S, name, QuietRng())
        A, B = copy.deepcopy(S), copy.deepcopy(S)
        v0 = A.read('evaluator')
        res = _orig_invoke(A, cap, **args)
        v1 = A.read('evaluator')
        oid = res.get('operation_id') if isinstance(res, dict) else None
        differs, control, t, t_end = set(), set(), 0.0, tmax
        while t < t_end - 1e-9:
            A.step(chunk)
            B.step(chunk)
            t += chunk
            a, b = A.read('evaluator'), B.read('evaluator')
            differs |= {k for k in v0 if _step_differs(v1[k], a[k], v0[k], b[k])}
            control |= {k for k in v0 if k != 'clock.time_s' and _leaf_changed(v0[k], b[k])}
            if oid and A.operations[oid]['phase'] != 'running' and t_end == tmax:
                t_end = t + tail
        return differs, control, (A.operations[oid]['phase'] if oid else None)

    def test_C01_depth_profile_declares_the_transient_etch_stage(self):
        """C01: the ion beam of the etch stages is on only for etch_s (here 4-14 s): it is an on-step effect even though it is off again at the
        end, and so are its lamp and the ion current."""
        differs, control, phase = self._follow('beam_ion', 'depth_profile.start', _variants('depth_profile.start')[0])
        self.assertEqual(phase, 'completed')
        self.assertTrue({'ion_gun.beam_on', 'panel.ion_gun_lamp', 'truth.ion_current_a'} <= differs)
        self.assertEqual(differs - set(rt.ON_STEP['depth_profile.start']) - set(rt.EFFECTS['depth_profile.start']), set())
        self.assertEqual(control - set(rt.ON_STEP['sim.step']), set())

    def test_C01_energy_calibration_declares_its_completion_effects(self):
        """C01: the calibration result, the detector reading and the internal acquisitions appear only after the 120 s discovery windows."""
        differs, control, phase = self._follow('beam_ion', 'calibration.energy_scale', {})
        self.assertEqual(phase, 'completed')
        self.assertTrue({'calibration.energy_scale', 'detector.count_rate_cps', 'acquisition.id', 'acquisition.kind'} <= differs)
        self.assertEqual(differs - set(rt.ON_STEP['calibration.energy_scale']) - set(rt.EFFECTS['calibration.energy_scale']), set())
        self.assertIn('calibration.energy_scale', rt.ON_STEP['sim.step'])

    def test_C01_ion_gun_is_locked_by_a_running_depth_profile_or_calibration(self):
        """Switching the gun off (or changing energy / raster) during a depth profile or calibration would let the routine continue with etch
        stages without ion current: refused with ACQUISITION_RUNNING (ION_BEAM_ON while the beam is on), state unchanged."""
        for sname in ('depth_profiling', 'calibrating'):
            S = _scenario(sname)
            for name in RNGS:
                setattr(S, name, QuietRng())
            self.assertIn(S.acq['kind'], ('depth_profile', 'calibration'))
            for args in (dict(on=False, energy_ev=500.0, raster_m=0.002), dict(on=True, energy_ev=500.0, raster_m=0.002)):
                A = copy.deepcopy(S)
                self.assertTrue(rejected(A, 'ion_gun.set', 'ION_BEAM_ON' if A.etching else 'ACQUISITION_RUNNING', **args))
            A = copy.deepcopy(S)
            while A.acq is not None and A.acq['kind'] in ('depth_profile', 'calibration'):
                A.step(5.0)
            _orig_invoke(A, 'ion_gun.set', on=False, energy_ev=500.0, raster_m=0.002)      # accepted once the routine has finished
            self.assertFalse(A.ion_on)
        self.assertIn('ACQUISITION_RUNNING', rt.CAP_ERRORS['ion_gun.set'])

    def test_C08_xray_off_during_an_etch_stage_stops_the_beam_at_the_call(self):
        """Contract review r2 C08: switching the X-ray source off while a depth profile or calibration is in an etch stage fails the routine and
        stops the ion beam at the call (immediate effects of xray.set_source, not on-step ones)."""
        for sname in ('depth_profiling_etch', 'calibrating_etch'):
            A = _scenario(sname)
            for name in RNGS:
                setattr(A, name, QuietRng())
            before = A.read('evaluator')
            self.assertTrue(before['ion_gun.beam_on']['value'] if isinstance(before['ion_gun.beam_on'], dict) else before['ion_gun.beam_on'])
            op = A.acq['op']
            _orig_invoke(A, 'xray.set_source', on=False, emission_a=0.015)
            after = A.read('evaluator')
            val = (lambda v: v['value'] if isinstance(v, dict) and 'value' in v else v)
            self.assertIs(val(after['ion_gun.beam_on']), False)
            self.assertIs(val(after['panel.ion_gun_lamp']), False)
            self.assertEqual(val(after['truth.ion_current_a']), 0.0)
            self.assertEqual(A.operations[op]['phase'], 'failed')
            self.assertTrue({'ion_gun.beam_on', 'panel.ion_gun_lamp', 'truth.ion_current_a'} <= set(rt.EFFECTS['xray.set_source']))


if __name__ == '__main__':
    unittest.main()
