"""tests for CHEM-173 bmg_clariostarplus (BMG LABTECH CLARIOstar Plus multi-mode microplate reader): python3 -m unittest tests/test_asset_bmg_clariostarplus.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation and foreign-agent ownership of plates and
samples; idempotent request ids; two instances isolated; step-partition independence; rejected calls leave the state unchanged; every output
validated against its schema and every success changes only declared slots; ANSI/SLAS well positions; Beer-Lambert linearity with the well path
length and the 1 cm path-length correction; 0-4 OD range with stray-light saturation; photometric precision at 1 and 2 OD; polystyrene UV cut-off
vs UV-transparent plates; absorbance spectrum maximum; documented read times (8 / 15 / 28 s flying, 19 / 57 / 184 s with 10 flashes);
fluorescence linearity over decades (EDR), inner filter, focus, bottom vs top, bandwidth overlap, fixed-gain overflow and auto gain;
fluorescence polarisation binding curve and precision; ATP luminescence (glow decay, flash after injection); injectors (prime, back-flush, clog,
consumption); incubation and Arrhenius kinetics; evaporation (edge, seal); kinetic cycle budget; drawer / lid interlocks; MARS-like analysis
(standard curve, Z'); power cycle; events, provenance and ledger."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import bmg_clariostarplus as rt  # noqa: E402

INST = 'bmg_clariostarplus_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
FL = 'fluorescein_in_basic_ethanol'
RB = 'rhodamine_b_in_ethanol'


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return x != y


class Rig:
    def __init__(self, seed=1, room=295.15, mains=230.0, instance=INST):
        self.h = rt.BmgClariostarplusRuntime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', room_k=room, mains_voltage_v=mains, noise_seed=seed)

    def call(self, sess, cap, expect='succeeded', **args):
        """Instrumented call: outputs validated against the declared schema; a success changes only declared immediate slots and never the clock;
        a rejection leaves every slot unchanged and returns a declared error code."""
        self.n += 1
        before, t0 = self.h.read('evaluator'), self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'r{self.n}')
        after = self.h.read('evaluator')
        changed = {k for k in after if _differ(after[k], before[k])} - {'clock.time_s'}
        if expect == 'succeeded':
            assert r['status'] == 'succeeded', (cap, r['error'])
            rt.validate(rt.CAPS[cap]['output_schema'], r['result'])
            if cap != 'sim.step':
                extra = changed - set(rt.EFFECTS.get(cap, []))
                assert not extra, (cap, sorted(extra))
                assert self.h.t == t0, cap
            SEEN_CAPS.add(cap)
            PROCESS_EVIDENCE.setdefault(cap, []).append(dict(arguments=json.dumps(args, sort_keys=True, default=str)[:200], time_s=t0, status=r['status']))
            return r['result']
        assert r['status'] in ('rejected', 'unsupported'), (cap, r['status'], r.get('result'))
        assert r['error']['code'] == expect, (cap, r['error']['code'], expect)
        assert not changed, (cap, 'rejected call changed', sorted(changed))
        assert expect in [e['code'] for e in rt.export_bmg_clariostarplus_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def on(self, kits=True):
        self.call(self.A, 'power.set', on=True)
        self.step(rt.ENG['initialisation_s'] + 0.5)
        if kits:
            for k in rt.KITS:
                self.call(self.E, 'kit.provide', kit=k, volume_m3=1e-3)

    def provide(self, sid, comp, volume=1e-4, **kw):
        self.call(self.E, 'sample.provide', sample_id=sid, volume_m3=volume, composition=comp, **kw)

    def plate(self, pid='p', fmt='96', ptype='clear'):
        self.call(self.A, 'plate.prepare', plate_id=pid, format=fmt, type=ptype)
        return pid

    def fill(self, pid, wells, source, volume, diluent=None, total=None):
        if volume > 0:
            self.call(self.A, 'well.dispense', plate_id=pid, wells=wells, source_id=source, volume_m3=volume)
        if diluent and total and total - volume > 1e-12:
            self.call(self.A, 'well.dispense', plate_id=pid, wells=wells, source_id=diluent, volume_m3=total - volume)

    def load(self, pid):
        self.call(self.A, 'drawer.set', open=True)
        self.step(rt.ENG['carrier_motion_s'] + 0.2)
        self.call(self.A, 'plate.load', plate_id=pid)
        self.call(self.A, 'drawer.set', open=False)
        self.step(rt.ENG['carrier_motion_s'] + 0.2)

    def unload(self):
        self.call(self.A, 'drawer.set', open=True)
        self.step(rt.ENG['carrier_motion_s'] + 0.2)
        return self.call(self.A, 'plate.unload')

    def measure(self, **protocol):
        self.call(self.A, 'protocol.configure', **protocol)
        out = self.call(self.A, 'run.start')
        for _ in range(100000):
            if self.read('run.status')['state'] != 'running':
                break
            self.step(1.0)
        return out, self.call(self.A, 'data.read')

    def values(self, data, wl=None):
        out = {}
        for r in data['rows']:
            if wl is None or r['wavelength_m'] == wl:
                out.setdefault(r['well'], []).append(r['value'])
        return {k: v[-1] for k, v in out.items()}


def _raised_codes():
    src = Path(rt.__file__).read_text()
    funcs = {}
    for m in re.finditer(r'\n    def (\w+)\(self[^\n]*\n(.*?)(?=\n    def |\n# ----|\nclass |\n    _NOCOPY)', src, re.S):
        funcs[m.group(1)] = m.group(2)
    own = {k: set(re.findall(r"ActionRejected\('([A-Z_]+)'\)", v)) for k, v in funcs.items()}
    calls = {k: set(re.findall(r'self\.(_\w+)\(', v)) & set(funcs) for k, v in funcs.items()}
    module_level = set(re.findall(r"ActionRejected\('([A-Z_]+)'\)", src[:src.index('class BmgClariostarplusRuntime')]))
    out = {}
    for k in funcs:
        if not k.startswith('_cmd_'):
            continue
        seen, todo, codes = set(), [k], set()
        while todo:
            f = todo.pop()
            if f in seen:
                continue
            seen.add(f)
            codes |= own.get(f, set())
            todo += [g for g in calls.get(f, ()) if g not in ('_cmd_sim_step', 'step', '_tick')]
        if 'expand_wells(' in funcs[k] or '_cmd_well' in k:
            codes |= {'INVALID_WELL'} & module_level
        out[k[5:].replace('_', '.', 1)] = codes
    return out


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_and_mirrors_consistent(self):
        meta = rt.export_bmg_clariostarplus_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            for x in c['observes']:
                self.assertIn(x, slots, k)
            for pre in c['preconditions']:
                self.assertTrue(set(pre['state_ids']) <= slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
            self.assertIn(c['kind'], ('query', 'device_command', 'robot_skill', 'simulation', 'analysis', 'configuration'))
            self.assertIn(c['model_effects']['mode'], ('none', 'direct', 'coupled', 'host_required'))
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['microplate_reader_process']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_bmg_clariostarplus_contract_metadata()
        for cap, codes in _raised_codes().items():
            if cap not in meta['capabilities']:
                cap = next(k for k in meta['capabilities'] if k.replace('.', '_') == cap.replace('.', '_'))
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            r = g.A.invoke(instance_id=INST, capability_id=k, arguments={}, request_id='u-' + k)
            self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))

    def test_agent_truth_isolation(self):
        g = Rig()
        d = g.A.describe(instance_id=INST)
        self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']))
        with self.assertRaises(rt.ActionRejected):
            g.A.read(instance_id=INST, state_ids=['truth.plates'])
        for cap, args in (('truth.inspect', {}), ('sim.step', dict(dt_s=1.0)), ('kit.provide', dict(kit='water')),
                          ('sample.provide', dict(sample_id='x', volume_m3=1e-5, composition={}))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        g.call(g.E, 'fault.inject', fault='lamp_aging', active=True)
        self.assertFalse(any(e['kind'] == 'fault' for e in g.call(g.A, 'events.read', since_s=0.0)['events']))

    def test_foreign_agent_ownership(self):
        g = Rig()
        g.h.register_principal('agent2', 'agent')
        B = g.h.session('agent2')
        g.on()
        g.plate('mine')
        g.fill('mine', ['A1'], 'water', 1e-7)
        for cap, args in (('well.dispense', dict(plate_id='mine', wells=['A2'], source_id='water', volume_m3=1e-7)), ('plate.seal', dict(plate_id='mine', sealed=True))):
            r = B.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        self.assertNotIn('mine', B.read(instance_id=INST, state_ids=['plates.available'])['observations']['plates.available']['value'])
        g.load('mine')
        r = B.invoke(instance_id=INST, capability_id='run.start', arguments={}, request_id='b-run')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = B.invoke(instance_id=INST, capability_id='plate.prepare', arguments=dict(plate_id='theirs', format='384', type='black'), request_id='b-own')
        self.assertEqual(r['status'], 'succeeded')
        r = B.invoke(instance_id=INST, capability_id='plate.prepare', arguments=dict(plate_id='mine', format='96', type='clear'), request_id='b-same-id')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertIn('ACCESS_DENIED', [e['code'] for e in rt.export_bmg_clariostarplus_contract_metadata()['capabilities']['plate.prepare']['errors']])
        g.call(g.A, 'protocol.configure', mode='abs_endpoint', format='96', wells=['A1'], wavelengths_m=[450e-9])
        g.call(g.A, 'run.start')
        r = B.invoke(instance_id=INST, capability_id='run.stop', arguments={}, request_id='b-stop')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.read('run.status')['state'], 'running')

    def test_idempotent_request_ids(self):
        g = Rig()
        g.on()
        g.plate('p')
        args = dict(plate_id='p', wells=['A1'], source_id='water', volume_m3=1e-7)
        r1 = g.A.invoke(instance_id=INST, capability_id='well.dispense', arguments=args, request_id='same')
        snap = g.h.read('evaluator')
        r2 = g.A.invoke(instance_id=INST, capability_id='well.dispense', arguments=args, request_id='same')
        self.assertEqual(r1, r2)
        self.assertEqual(json.dumps(snap, sort_keys=True, default=str), json.dumps(g.h.read('evaluator'), sort_keys=True, default=str))
        r3 = g.A.invoke(instance_id=INST, capability_id='well.dispense', arguments=dict(args, wells=['A2']), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='r_a'), Rig(instance='r_b', room=298.15)
        a.call(a.A, 'power.set', on=True)
        self.assertFalse(b.read('power.on'))
        r = a.A.invoke(instance_id='r_b', capability_id='power.set', arguments=dict(on=True), request_id='z')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')

    def test_step_partition_independence(self):
        def run(steps):
            g = Rig(seed=5)
            g.on()
            g.call(g.A, 'incubator.set', temperature_k=310.15)
            g.provide('rx', {FL: 0.0, RB: 1e-3}, reactions=[dict(source=RB, product=FL, k_per_s=2e-3, ea_j_mol=5e4)])
            g.plate('p')
            g.fill('p', ['A1:A6'], 'rx', 1e-7)
            g.load('p')
            for s in steps:
                g.step(s)
            r = g.read()
            return r['chamber.temperature_k'], json.dumps(r['truth.plates'], sort_keys=True)
        a = run([1200.0])
        b = run([0.05] * 400 + [0.37, 13.11, 1166.52])
        c = run([40.0] * 30)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.BmgClariostarplusRuntime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', room_k=295.15, mains_voltage_v=230.0, noise_seed=2)
        g.call(g.A, 'drawer.set', expect='POWER_OFF', open=True)
        g.call(g.E, 'sample.provide', expect='UNKNOWN_SPECIES', sample_id='s', volume_m3=1e-5, composition={'unobtainium': 1.0})
        g.on()
        g.call(g.A, 'drawer.set', open=True)
        g.call(g.A, 'plate.load', expect='UNKNOWN_PLATE', plate_id='nope')
        g.plate('p')
        g.call(g.A, 'plate.prepare', expect='PLATE_EXISTS', plate_id='p', format='96', type='clear')
        g.call(g.A, 'well.dispense', expect='INVALID_WELL', plate_id='p', wells=['Q1'], source_id='water', volume_m3=1e-7)
        g.call(g.A, 'well.dispense', expect='UNKNOWN_ITEM', plate_id='p', wells=['A1'], source_id='nope', volume_m3=1e-7)
        g.call(g.A, 'well.dispense', expect='WELL_OVERFLOW', plate_id='p', wells=['A1'], source_id='water', volume_m3=1e-5)
        g.call(g.A, 'plate.load', expect='DRAWER_CLOSED', plate_id='p')
        g.step(3.0)
        g.call(g.A, 'plate.unload', expect='CARRIER_EMPTY')
        g.call(g.A, 'plate.load', plate_id='p')
        g.call(g.A, 'well.dispense', expect='PLATE_IN_READER', plate_id='p', wells=['A1'], source_id='water', volume_m3=1e-7)
        g.call(g.A, 'run.start', expect='DRAWER_OPEN')
        g.call(g.A, 'drawer.set', open=False)
        g.step(3.0)
        g.call(g.A, 'run.start', expect='NO_PROTOCOL')
        g.call(g.A, 'protocol.configure', expect='MISSING_SETTING', mode='abs_endpoint', format='96', wells=['A1'])
        g.call(g.A, 'protocol.configure', expect='BANDWIDTH_OVERLAP', mode='fi_endpoint', format='96', wells=['A1'], excitation_m=485e-9, emission_m=495e-9)
        g.call(g.A, 'protocol.configure', expect='WAVELENGTH_OUT_OF_RANGE', mode='fi_endpoint', format='96', wells=['A1'], excitation_m=250e-9, emission_m=520e-9)
        g.call(g.A, 'protocol.configure', expect='CYCLE_TOO_SHORT', mode='abs_endpoint', format='96', wells=['all'], wavelengths_m=[4.5e-7], cycles=5, cycle_time_s=5.0)
        g.call(g.A, 'protocol.configure', mode='abs_endpoint', format='384', wells=['A1'], wavelengths_m=[4.5e-7])
        g.call(g.A, 'run.start', expect='FORMAT_MISMATCH')
        g.call(g.A, 'focus.adjust', expect='EMPTY_WELL', well='A1')
        g.call(g.A, 'incubator.set', expect='TEMPERATURE_TOO_LOW', temperature_k=296.15)
        g.call(g.A, 'injector.load_reagent', expect='LID_CLOSED', pump='A', source_id='water', volume_m3=1e-6)
        g.call(g.A, 'injector.prime', expect='NO_REAGENT', pump='A', volume_m3=1e-6)
        g.call(g.A, 'lid.set', open=True)
        g.call(g.A, 'run.start', expect='LID_OPEN')
        g.call(g.A, 'injector.load_reagent', pump='A', source_id='water', volume_m3=1e-6)
        g.call(g.A, 'injector.load_reagent', expect='REAGENT_MISMATCH', pump='A', source_id='pbs_buffer', volume_m3=1e-6)
        g.call(g.A, 'run.stop', expect='NO_RUN')
        g.call(g.A, 'data.spectrum', expect='NO_DATA', well='A1')
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='injector_clog', active=True)
        g.call(g.E, 'plant.configure', room_k=295.15, mains_voltage_v=180.0, noise_seed=1)
        self.assertFalse(g.read('power.on'))
        g.call(g.A, 'power.set', expect='MAINS_OUT_OF_RANGE', on=True)


def _rb_conc(od, vol=2e-7, area=3.2e-5):
    l = vol / area - rt.ENG['meniscus_m']
    return (od - 0.045) / (rt.eps_at(RB, 543e-9) * l)


class Physics(unittest.TestCase):
    def test_slas_well_positions(self):
        x, y = rt.well_xy('96', 'A1')
        self.assertAlmostEqual(x, 0.01438)
        self.assertAlmostEqual(y, 0.01124)
        x2, y2 = rt.well_xy('96', 'H12')
        self.assertAlmostEqual(x2 - x, 11 * 0.009)
        self.assertAlmostEqual(y2 - y, 7 * 0.009)
        self.assertEqual(len(rt.well_names('384')), 384)
        self.assertEqual(rt.well_names('1536')[-1], 'AF48')
        self.assertAlmostEqual(rt.well_xy('384', 'B2')[0] - rt.well_xy('384', 'B1')[0], 0.0045)
        self.assertEqual(rt.expand_wells('96', ['A1:B2']), ['A1', 'A2', 'B1', 'B2'])

    def test_beer_lambert_linearity_and_pathlength_correction(self):
        g = Rig(seed=11)
        g.on()
        g.provide('rb', {RB: _rb_conc(2.0)})
        g.plate('p')
        fr = [0.0, 0.125, 0.25, 0.5, 0.75, 1.0]
        for j, f in enumerate(fr):
            g.fill('p', [f'A{j + 1}'], 'rb', 2e-7 * f, 'water', 2e-7)
        g.load('p')
        _, d = g.measure(mode='abs_endpoint', format='96', wells=['A1:A6'], wavelengths_m=[543e-9], flashes=22)
        v = g.values(d)
        a = [v[f'A{j + 1}'] - v['A1'] for j in range(6)]
        for f, x in zip(fr[1:], a[1:]):
            self.assertAlmostEqual(x / (f * a[-1]), 1.0, delta=0.025)            # photometric noise of two reads
        self.assertAlmostEqual(v['A6'], 2.0, delta=0.04)                       # accuracy < 1 % at 2 OD (+ noise)
        _, d = g.measure(mode='abs_endpoint', format='96', wells=['A6'], wavelengths_m=[543e-9], flashes=22, pathlength_correction=True)
        r = d['rows'][0]
        self.assertIn('PATHLENGTH_CORRECTED_1CM', r['flags'])
        self.assertAlmostEqual(r['value'] / (rt.eps_at(RB, 543e-9) * _rb_conc(2.0) * 0.01), 1.0, delta=0.04)

    def test_od_range_and_precision(self):
        res = {}
        for od in (1.0, 2.0):
            g = Rig(seed=12 + int(od))
            g.on()
            g.provide('rb', {RB: _rb_conc(od)})
            g.plate('p')
            g.fill('p', ['A1:A12'], 'rb', 2e-7)
            g.load('p')
            _, d = g.measure(mode='abs_endpoint', format='96', wells=['A1:A12'], wavelengths_m=[543e-9], flashes=22)
            v = list(g.values(d).values())
            m = sum(v) / len(v)
            sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
            res[od] = sd / m
        self.assertLess(res[1.0], 0.005)
        self.assertLess(res[2.0], 0.008)
        g = Rig(seed=14)
        g.on()
        g.provide('rb', {RB: _rb_conc(6.0)})
        g.plate('p')
        g.fill('p', ['A1'], 'rb', 2e-7)
        g.load('p')
        _, d = g.measure(mode='abs_endpoint', format='96', wells=['A1'], wavelengths_m=[543e-9])
        self.assertIn('OUT_OF_RANGE', d['rows'][0]['flags'])
        self.assertLess(d['rows'][0]['value'], 5.4)                            # stray light (1e-5) caps the reading near 5 OD

    def test_polystyrene_uv_cutoff_and_uv_plate(self):
        out = {}
        for ptype in ('clear', 'uv_transparent'):
            g = Rig(seed=15)
            g.on()
            g.plate('p', '96', ptype)
            g.fill('p', ['A1'], 'water', 2e-7)
            g.load('p')
            _, d = g.measure(mode='abs_endpoint', format='96', wells=['A1'], wavelengths_m=[260e-9])
            out[ptype] = d['rows'][0]['value']
        self.assertGreater(out['clear'], 1.0)
        self.assertLess(out['uv_transparent'], 0.2)

    def test_absorbance_spectrum_maximum(self):
        g = Rig(seed=16)
        g.on()
        g.provide('rb', {RB: _rb_conc(1.0)})
        g.plate('p')
        g.fill('p', ['A1'], 'rb', 2e-7)
        g.load('p')
        g.measure(mode='abs_spectrum', format='96', wells=['A1'], spectrum_range_m=[400e-9, 650e-9], resolution_m=2e-9)
        s = g.call(g.A, 'data.spectrum', well='A1')
        k = max(range(len(s['value'])), key=lambda i: s['value'][i])
        self.assertAlmostEqual(s['wavelength_m'][k], rt.spectra()[RB]['lambda_max_m'], delta=4e-9)

    def test_documented_read_times(self):
        g = Rig(seed=17)
        g.on()
        for fmt, t1, t10 in (('96', 8.0, 19.0), ('384', 15.0, 57.0), ('1536', 28.0, 184.0)):
            for n, ref in ((1, t1), (10, t10)):
                out = g.call(g.A, 'protocol.configure', mode='abs_endpoint', format=fmt, wells=['all'], wavelengths_m=[4.5e-7], flashes=n)
                self.assertAlmostEqual(out['plate_read_time_s'] - rt.ENG['read_overhead_s'], ref, places=6)

    def test_fluorescence_linearity_inner_filter_and_focus(self):
        g = Rig(seed=18)
        g.on()
        g.plate('p', '96', 'black')
        concs = [1e-6, 1e-5, 1e-4, 1e-3, 3e-2]
        for j, c in enumerate(concs):
            g.provide(f's{j}', {FL: c})
            g.fill('p', [f'A{j + 1}'], f's{j}', 2e-7)
        g.fill('p', ['B1:B4'], 'pbs_buffer', 2e-7)
        g.load('p')
        prot = dict(mode='fi_endpoint', format='96', wells=['A1:A5', 'B1:B4'], excitation_m=485e-9, emission_m=528e-9, excitation_bandwidth_m=10e-9, emission_bandwidth_m=20e-9)
        _, d0 = g.measure(**dict(prot, focal_height_m=0.0005))
        g.call(g.A, 'focus.adjust', well='A3')
        g.step(rt.ENG['focus_scan_s'] + 0.2)
        _, d = g.measure(**prot)
        v, v0 = g.values(d), g.values(d0)
        blank = sum(v[f'B{i}'] for i in range(1, 5)) / 4
        r = [(v[f'A{j + 1}'] - blank) / concs[j] for j in range(4)]
        for x in r[1:]:
            self.assertAlmostEqual(x / r[1], 1.0, delta=0.05)                  # linear over three decades (EDR)
        self.assertLess((v['A5'] - blank) / concs[4], 0.7 * r[2])             # inner filter at high absorbance
        self.assertGreater(v['A4'], 1.5 * v0['A4'])                            # autofocus beats a bad focal height
        self.assertAlmostEqual(g.read('focus.height_m') * 1e4, round(g.read('focus.height_m') * 1e4), places=6)

    def test_fixed_gain_overflow_and_auto_gain(self):
        g = Rig(seed=19)
        g.on()
        g.provide('f', {FL: 1e-3})
        g.plate('p', '96', 'black')
        g.fill('p', ['A1:A2'], 'f', 2e-7)
        g.load('p')
        prot = dict(mode='fi_endpoint', format='96', wells=['A1:A2'], excitation_m=485e-9, emission_m=528e-9, gain=4000)
        _, d = g.measure(**prot)
        self.assertIn('OVERFLOW', d['rows'][0]['flags'])
        g.call(g.A, 'protocol.configure', **dict(prot, gain=1000))
        out = g.call(g.A, 'gain.adjust', well='A1', target_fraction=0.5)
        g.step(2.5)
        self.assertEqual(g.read('protocol.current')['gain'], out['gain'])
        g.call(g.A, 'run.start')
        g.step(10.0)
        v = g.call(g.A, 'data.read')['rows'][0]['value']
        self.assertAlmostEqual(v / rt.ENG['counts_full_scale'], 0.5, delta=0.05)

    def test_bottom_reading_lower_signal(self):
        g = Rig(seed=20)
        g.on()
        g.provide('f', {FL: 1e-4})
        out = {}
        for ptype in ('black_clear_bottom', 'black'):
            g.plate(ptype, '96', ptype)
            g.fill(ptype, ['A1'], 'f', 2e-7)
            g.load(ptype)
            _, d = g.measure(mode='fi_endpoint', format='96', wells=['A1'], excitation_m=485e-9, emission_m=528e-9, optic='bottom')
            out[ptype] = d['rows'][0]['value']
            g.unload()
        self.assertGreater(out['black_clear_bottom'], 10 * out['black'])

    def test_fp_binding_curve(self):
        g = Rig(seed=21)
        g.on()
        g.plate('p', '384', 'black')
        rec = [0.0, 1e-5, 1e-4, 1e-3, 1e-2]
        for j, r in enumerate(rec):
            g.provide(f't{j}', {FL: 1e-6}, binding=dict(tracer=FL, receptor_mol_m3=r, kd_mol_m3=1e-4))
            g.fill('p', [f'A{j + 1}'], f't{j}', 2e-8)
        g.load('p')
        _, d = g.measure(mode='fp_endpoint', format='384', wells=['A1:A5'], excitation_m=482e-9, emission_m=530e-9, excitation_bandwidth_m=16e-9, emission_bandwidth_m=30e-9)
        v = [g.values(d)[f'A{j + 1}'] for j in range(5)]
        self.assertTrue(all(b > a for a, b in zip(v, v[1:])), v)
        self.assertAlmostEqual(v[0], 1000 * 3 * 0.02 / 2.02, delta=2.0)
        f_mid = (v[2] - v[0]) / (v[4] - v[0])
        self.assertTrue(0.3 < f_mid < 0.7, f_mid)                              # half-bound near receptor = Kd

    def test_luminescence_glow_and_flash_injection(self):
        g = Rig(seed=22)
        g.on()
        g.provide('atp', {'atp': 1e-4})
        g.plate('p', '96', 'white')
        g.fill('p', ['A1:A3'], 'atp', 5e-8)
        g.fill('p', ['A1:A2'], 'glow_luciferase_reagent', 5e-8)
        g.load('p')
        _, d1 = g.measure(mode='lum_endpoint', format='96', wells=['A1:A3'], integration_s=1.0)
        g.step(3600.0)
        _, d2 = g.measure(mode='lum_endpoint', format='96', wells=['A1:A3'], integration_s=1.0)
        a1, a2 = g.values(d1), g.values(d2)
        self.assertGreater(a1['A1'], 1000 * abs(a1['A3']) + 1e4)
        self.assertAlmostEqual(a2['A1'] / a1['A1'], math.exp(-3600.0 / rt.ENG['glow_decay_s']), delta=0.03)
        g.call(g.A, 'lid.set', open=True)
        g.call(g.A, 'injector.load_reagent', pump='B', source_id='flash_luciferase_reagent', volume_m3=5e-6)
        g.call(g.A, 'lid.set', open=False)
        g.call(g.A, 'protocol.configure', mode='lum_endpoint', format='96', wells=['A3'], integration_s=0.5, cycles=12, cycle_time_s=2.0,
               injections=[dict(pump='B', volume_m3=5e-8, cycle=2)])
        g.call(g.A, 'run.start', expect='INJECTOR_NOT_PRIMED')
        g.call(g.A, 'injector.prime', pump='B', volume_m3=1e-6)
        g.step(4.0)
        _, d = g.measure(mode='lum_endpoint', format='96', wells=['A3'], integration_s=0.5, cycles=12, cycle_time_s=2.0, injections=[dict(pump='B', volume_m3=5e-8, cycle=2)])
        v = [r['value'] for r in d['rows']]
        k = max(range(len(v)), key=lambda i: v[i])
        self.assertIn(k, (1, 2, 3))
        self.assertLess(v[-1], 0.5 * v[k])                                       # flash kinetics decay
        led = g.call(g.E, 'ledger.read')['ledger']
        self.assertAlmostEqual(led['injected_m3'], 5e-8, delta=1e-12)

    def test_injector_backflush_and_clog(self):
        g = Rig(seed=23)
        g.on()
        g.call(g.A, 'lid.set', open=True)
        g.call(g.A, 'injector.load_reagent', pump='A', source_id='water', volume_m3=5e-6)
        g.call(g.A, 'lid.set', open=False)
        g.call(g.A, 'injector.prime', pump='A', volume_m3=1e-6)
        g.call(g.A, 'injector.backflush', expect='BUSY', pump='A')
        g.step(5.0)
        self.assertTrue(g.read('injectors.status')['A']['primed'])
        out = g.call(g.A, 'injector.backflush', pump='A')
        self.assertAlmostEqual(out['returned_m3'], rt.ENG['line_volume_m3'])
        g.step(3.0)
        g.call(g.E, 'fault.inject', fault='injector_clog', active=True, pump='A')
        g.call(g.A, 'injector.prime', expect='INJECTOR_BLOCKED', pump='A', volume_m3=1e-6)

    def test_incubation_and_arrhenius_kinetics(self):
        def rate(T):
            g = Rig(seed=24)
            g.on()
            if T:
                g.call(g.A, 'incubator.set', temperature_k=T)
            g.provide('rx', {RB: 1e-3}, reactions=[dict(source=RB, product=FL, k_per_s=1e-4, ea_j_mol=5e4)])
            g.plate('p', '96', 'clear')
            g.fill('p', ['A1'], 'rx', 2e-7)
            g.load('p')
            g.step(3600.0)
            tp = g.read('truth.plates')['p']
            c = tp['wells']['A1']['conc_mol_m3']
            return g.read('chamber.temperature_k'), c.get(FL, 0.0) / (c.get(FL, 0.0) + c.get(RB, 0.0))
        T0, f0 = rate(None)
        T1, f1 = rate(310.15)
        self.assertAlmostEqual(T1, 310.15, delta=0.3)
        self.assertGreater(f1, f0 * 1.3)                                         # Ea 50 kJ/mol: about 2.2 x faster at 37 C (plate warm-up included)

    def test_evaporation_edge_and_seal(self):
        g = Rig(seed=25)
        g.on()
        g.call(g.A, 'incubator.set', temperature_k=310.15)
        g.plate('o', '96', 'clear')
        g.plate('s', '96', 'clear')
        for pid in ('o', 's'):
            g.fill(pid, ['all'], 'water', 2e-7)
        g.call(g.A, 'plate.seal', plate_id='s', sealed=True)
        g.load('o')
        g.step(4 * 3600.0)
        w = g.read('truth.plates')
        lost_edge = 2e-7 - w['o']['wells']['A1']['volume_m3']
        lost_mid = 2e-7 - w['o']['wells']['D6']['volume_m3']
        lost_sealed = 2e-7 - w['s']['wells']['D6']['volume_m3']
        self.assertGreater(lost_edge, 1.5 * lost_mid)
        self.assertLess(lost_sealed, 0.1 * lost_mid)

    def test_injection_triggered_reaction_kinetics(self):
        g = Rig(seed=26)
        g.on()
        g.provide('sub', {RB: 1e-3}, reactions=[dict(source=RB, product=FL, k_per_s=0.02, trigger='atp')])
        g.provide('trig', {'atp': 1e-1})
        g.plate('p', '96', 'clear')
        g.fill('p', ['A1'], 'sub', 1.5e-7)
        g.load('p')
        g.call(g.A, 'lid.set', open=True)
        g.call(g.A, 'injector.load_reagent', pump='A', source_id='trig', volume_m3=5e-6)
        g.call(g.A, 'lid.set', open=False)
        g.call(g.A, 'injector.prime', pump='A', volume_m3=5e-7)
        g.step(3.0)
        _, d = g.measure(mode='abs_endpoint', format='96', wells=['A1'], wavelengths_m=[543e-9], cycles=20, cycle_time_s=10.0,
                         injections=[dict(pump='A', volume_m3=5e-8, cycle=3)], shake=dict(mode='orbital', duration_s=2.0, before_each_cycle=False))
        v = [r['value'] for r in d['rows']]
        self.assertLess(abs(v[1] - v[0]), 0.03)                                 # nothing before the injection
        self.assertLess(v[-1], 0.6 * v[2])                                      # the dye is consumed after the trigger arrives

    def test_analysis_standard_curve_and_z_prime(self):
        g = Rig(seed=27)
        g.on()
        g.plate('p', '96', 'black')
        stds = {}
        for j, c in enumerate([0.0, 1e-5, 2e-5, 5e-5, 1e-4]):
            if c:
                g.provide(f'c{j}', {FL: c})
                g.fill('p', [f'A{j + 1}'], f'c{j}', 2e-7)
            else:
                g.fill('p', [f'A{j + 1}'], 'pbs_buffer', 2e-7)
            stds[f'A{j + 1}'] = c
        g.provide('pos', {FL: 8e-5})
        g.fill('p', ['B1:B8'], 'pos', 2e-7)
        g.fill('p', ['C1:C8'], 'pbs_buffer', 2e-7)
        g.load('p')
        g.measure(mode='fi_endpoint', format='96', wells=['A1:C8'], excitation_m=485e-9, emission_m=528e-9)
        out = g.call(g.A, 'data.analyze', blank_wells=['C1:C8'], standards=stds, fit='linear', positive_wells=['B1:B8'], negative_wells=['C1:C8'])
        self.assertGreater(out['fit']['r2'], 0.999)
        self.assertAlmostEqual(out['concentrations']['B4'] / 8e-5, 1.0, delta=0.05)
        self.assertGreater(out['z_prime'], 0.8)
        out4 = g.call(g.A, 'data.analyze', standards=stds, fit='4pl')
        self.assertEqual(out4['fit']['model'], '4pl')

    def test_power_cycle_interlocks_and_ledger(self):
        g = Rig(seed=28)
        g.call(g.A, 'power.press_switch')
        self.assertTrue(g.read('lamp.busy'))
        g.call(g.A, 'drawer.set', expect='BUSY', open=True)
        g.step(rt.ENG['initialisation_s'] + 0.5)
        g.call(g.A, 'drawer.set', open=True)
        g.step(1.0)
        self.assertTrue(0.0 < g.read('drawer.position_m') < 0.185)
        g.step(2.0)
        self.assertAlmostEqual(g.read('drawer.position_m'), 0.185)
        g.call(g.A, 'lid.set', open=True)
        self.assertAlmostEqual(g.read('lid.angle_rad'), 1.25)
        g.step(0.2)
        self.assertIn('LID_OPEN', g.read('warnings.active'))
        g.call(g.A, 'lid.set', open=False)
        for k in rt.KITS:
            g.call(g.E, 'kit.provide', kit=k, volume_m3=1e-3)
        g.plate('p', '384', 'black')
        g.fill('p', ['all'], 'water', 2e-8)
        g.call(g.A, 'plate.load', plate_id='p')
        g.call(g.A, 'drawer.set', open=False)
        g.step(3.0)
        g.call(g.A, 'protocol.configure', mode='fi_endpoint', format='384', wells=['all'], excitation_m=485e-9, emission_m=528e-9)
        g.call(g.A, 'run.start')
        g.call(g.A, 'lid.set', expect='RUN_ACTIVE', open=True)
        g.step(2.0)
        self.assertEqual(g.call(g.A, 'run.stop')['state'], 'stopped')
        g.call(g.A, 'run.start')
        g.step(2.0)
        g.call(g.A, 'power.press_switch')
        self.assertEqual(g.read('run.status')['state'], 'stopped')
        self.assertFalse(g.read('lamp.power'))
        codes = {e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']}
        self.assertTrue({'POWER_ON', 'RUN_START', 'POWER_OFF'} <= codes)
        prov = g.call(g.A, 'provenance.read')
        self.assertGreaterEqual(len(prov['species']), 150)
        led = g.call(g.E, 'ledger.read')['ledger']
        self.assertAlmostEqual(led['dispensed_m3'], 384 * 2e-8, delta=1e-12)
        out = g.call(g.E, 'truth.inspect', plate_id='p')
        self.assertIn('p', out['state']['plates'])
        g.call(g.A, 'run.stop', expect='NO_RUN')


class ReviewRegressions(unittest.TestCase):
    def test_injection_mass_balance(self):
        g = Rig(seed=31)
        g.on()
        g.plate('p')
        g.fill('p', ['A1:H12'], 'water', 1e-7)
        g.call(g.A, 'lid.set', open=True)
        g.call(g.A, 'injector.load_reagent', pump='A', source_id='water', volume_m3=2e-5)
        g.call(g.A, 'lid.set', open=False)
        g.call(g.A, 'injector.prime', pump='A', volume_m3=1e-6)
        g.step(5.0)
        g.load('p')
        g.measure(mode='abs_endpoint', format='96', wells=['A1:H12'], wavelengths_m=[450e-9], injections=[dict(pump='A', volume_m3=1e-7, cycle=1)])
        led = g.call(g.E, 'ledger.read')['ledger']
        st = g.read('injectors.status')['A']
        held = st['bottle_m3'] + st['line_m3']
        self.assertAlmostEqual(led['injected_m3'], 96 * 1e-7, delta=1e-12)
        self.assertAlmostEqual(led['reagent_loaded_m3'], held + led['primed_to_waste_m3'] + led['injected_m3'] + led['plate_waste_m3'], delta=1e-12)

    def test_luminescence_noise_scales_with_integration_and_blanks_positive(self):
        sds = {}
        for integ in (1.0, 10.0):
            g = Rig(seed=32)
            g.on()
            g.plate('p', '96', 'white')
            g.fill('p', ['A1:D12'], 'pbs_buffer', 1e-7)
            g.load('p')
            _, d = g.measure(mode='lum_endpoint', format='96', wells=['A1:D12'], integration_s=integ)
            v = list(g.values(d).values())
            self.assertTrue(all(x >= 0 for x in v))
            m = sum(v) / len(v)
            sds[integ] = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
        self.assertTrue(2.0 < sds[10.0] / sds[1.0] < 4.5, sds)               # ~sqrt(10): the dark variance grows with the integration time

    def test_fixed_low_gain_blanks_not_negative(self):
        g = Rig(seed=33)
        g.on()
        g.plate('p', '96', 'black')
        g.fill('p', ['A1:A12'], 'pbs_buffer', 2e-7)
        g.load('p')
        _, d = g.measure(mode='fi_endpoint', format='96', wells=['A1:A12'], excitation_m=485e-9, emission_m=528e-9, gain=800, flashes=1)
        self.assertTrue(all(v >= 0 for v in g.values(d).values()))

    def test_degenerate_standards_rejected(self):
        g = Rig(seed=34)
        g.on()
        g.plate('p')
        g.fill('p', ['A1:B5'], 'water', 2e-7)
        g.load('p')
        g.measure(mode='abs_endpoint', format='96', wells=['A1:B5'], wavelengths_m=[450e-9])
        g.call(g.A, 'data.analyze', expect='INSUFFICIENT_STANDARDS', standards={'A1': 0.0, 'A2': 0.0}, fit='linear')

    def test_kinetic_cycles_do_not_overlap_with_first_cycle_shake(self):
        g = Rig(seed=35)
        g.on()
        g.plate('p')
        g.fill('p', ['A1:H12'], 'water', 2e-7)
        g.load('p')
        _, d = g.measure(mode='abs_endpoint', format='96', wells=['all'], wavelengths_m=[543e-9], flashes=10, cycles=2, cycle_time_s=20.5,
                         shake=dict(mode='orbital', duration_s=60.0, before_each_cycle=False))
        t1 = max(r['time_s'] for r in d['rows'] if r['cycle'] == 1)
        t2 = min(r['time_s'] for r in d['rows'] if r['cycle'] == 2)
        self.assertGreater(t2, t1)
        _, d = g.measure(mode='abs_endpoint', format='96', wells=['all'], wavelengths_m=[543e-9], flashes=10, cycles=3, cycle_time_s=20.5,
                         shake=dict(mode='orbital', duration_s=60.0, before_each_cycle=False))
        first = {c: min(r['time_s'] for r in d['rows'] if r['cycle'] == c) for c in (1, 2, 3)}
        self.assertAlmostEqual(first[3] - first[2], 20.5, delta=0.11)        # after an overrun the configured cycle time is kept
        g.unload()
        g.plate('q')
        g.fill('q', ['A1'], 'water', 2e-7)
        g.load('q')
        _, d = g.measure(mode='abs_endpoint', format='96', wells=['A1:A2'], wavelengths_m=[450e-9], pathlength_correction=True)
        fl = {r['well']: r['flags'] for r in d['rows']}
        self.assertIn('PATHLENGTH_CORRECTED_1CM', fl['A1'])
        self.assertIn('PATHLENGTH_NOT_APPLICABLE', fl['A2'])
        self.assertIn('EMPTY', fl['A2'])

    def test_fp_blank_wells_undefined_and_opaque_pathlength(self):
        g = Rig(seed=36)
        g.on()
        g.plate('p', '96', 'black')
        g.fill('p', ['A1:A3'], 'pbs_buffer', 2e-7)
        g.load('p')
        _, d = g.measure(mode='fp_endpoint', format='96', wells=['A1:A6'], excitation_m=485e-9, emission_m=528e-9)
        for r in d['rows']:
            self.assertTrue(r['value'] is None and 'LOW_SIGNAL' in r['flags'] or -1000.0 <= r['value'] <= 1000.0, r)
        _, d = g.measure(mode='abs_endpoint', format='96', wells=['A1:A3'], wavelengths_m=[450e-9], pathlength_correction=True)
        self.assertIn('OPAQUE_PLATE', g.read('warnings.active'))
        for r in d['rows']:
            self.assertIn('PATHLENGTH_NOT_APPLICABLE', r['flags'])
            self.assertGreater(r['value'], 0.0)

    def test_grid_anchored_before_plant(self):
        h = rt.BmgClariostarplusRuntime('grid_probe')
        h.step(0.05)
        self.assertEqual(h.t_grid, 0.0)
        h.step(0.07)
        self.assertAlmostEqual(h.t_grid, 0.1)


class Settings(unittest.TestCase):
    def test_read_order_and_dual_emission(self):
        g = Rig(seed=29)
        g.on()
        g.provide('mix', {FL: 1e-4, RB: 1e-4})
        g.plate('p', '96', 'black')
        g.fill('p', ['A1:B3'], 'mix', 2e-7)
        g.load('p')
        _, d = g.measure(mode='fi_endpoint', format='96', wells=['A1:B3'], excitation_m=485e-9, emission_m=528e-9, emission2_m=580e-9, read_order='serpentine')
        first = [r['well'] for r in d['rows'] if 'EMISSION_2' not in r['flags']]
        self.assertEqual(first, ['A1', 'A2', 'A3', 'B3', 'B2', 'B1'])
        t = {r['well']: r['time_s'] for r in d['rows'] if 'EMISSION_2' not in r['flags']}
        self.assertLess(t['B3'], t['B1'])
        second = [r for r in d['rows'] if 'EMISSION_2' in r['flags']]
        self.assertEqual(len(second), 6)
        self.assertTrue(all(r['wavelength_m'] == 580e-9 for r in second))


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
