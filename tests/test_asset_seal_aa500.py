"""tests for CHEM-172 seal_aa500 (SEAL AA500 segmented continuous flow analyzer, 4 nutrient channels + AS2 sampler):
python3 -m unittest tests/test_asset_seal_aa500.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation and foreign-agent ownership; idempotent
request ids; two instances isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its
schema and every success changes only declared slots; EPA stock recipes against PubChem formulas; manifold design rule (top of range about 0.8 AU)
and the EPA 366.0 silicate manifold flows; start-up / shutdown programs, motorised platen and bath warm-up; transport delay and steady-state peak;
linear calibration and recovery of a mixed water sample; carry-over (EPA 366.0 high / low / low) grows when the wash time is cut; throughput limit
(120 samples / h); auto-dilution of over-range samples; quadratic fit above 1.2 AU; drift correction; cadmium reduction efficiency (nitrite /
nitrate check), air in the cadmium coil and activation; chlorine poisoning; arsenate and iron on phosphate; salinity refractive-index blank and
colour; bath temperature and colour development; LED wavelength mismatch and flow-cell path length; pump-tube wear; reagent empty, leak, probe
blocked, LED failure, bath runaway; power cut during a run; raw trace bubbles; interlocks (lid, platen, cover, lamp); manifold / method changes."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import seal_aa500 as rt  # noqa: E402

INST = 'seal_aa500_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
TOPS = dict(nitrate_stock=0.0028, ammonium_stock=0.006, phosphate_stock=0.008, silicate_stock=0.026)        # kg/m3 element at the top standard
LEVELS = [0.0, 0.1, 0.25, 0.5, 0.75, 1.0]
CH_OF = dict(nitrate_stock='ch1', ammonium_stock='ch2', phosphate_stock='ch3', silicate_stock='ch4')


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return x != y


class Rig:
    def __init__(self, seed=1, room=295.15, mains=230.0, instance=INST):
        self.h = rt.SealAa500Runtime(instance)
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
        assert expect in [e['code'] for e in rt.export_seal_aa500_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def kits(self, *names, volume=3e-3):
        for k in names or rt.KITS:
            self.call(self.E, 'kit.provide', kit=k, volume_m3=volume)

    def reagents(self, volume=4e-4):
        for c, ch in self.h.ch.items():
            for rg in ch['reagents']:
                self.call(self.A, 'reagent.load', channel=c, reagent=rg, volume_m3=volume)

    def ready(self, power=True, kits=True):
        if kits:
            self.kits()
            self.reagents()
        if power:
            self.call(self.A, 'power.set', on=True)
        self.call(self.A, 'system.startup')
        for _ in range(90):
            self.step(60.0)
            if self.read('system.state') == 'ready':
                return self.h.t
        raise AssertionError('never ready')

    def standards(self, tops=None, levels=LEVELS, rack='S', first=1, prefix='std'):
        tops = dict(TOPS if tops is None else tops)
        for i, f in enumerate(levels):
            comps = [dict(source_id=k, volume_m3=1e-4 * v / rt.STOCK_NOMINAL[k] * f) for k, v in tops.items() if f > 0]
            comps.append(dict(source_id='wash_water', volume_m3=1e-4 - sum(x['volume_m3'] for x in comps)))
            self.call(self.A, 'standard.prepare', standard_id=f'{prefix}{i}', volume_m3=1e-4, components=comps)
            self.call(self.A, 'sampler.load', rack=rack, position=first + i, item_id=f'{prefix}{i}', volume_m3=1e-5)
        return [dict(rack=rack, position=first + i, type='calibrant') for i in range(len(levels))]

    def sample(self, sid, pos, rack='A', volume=1e-5, **comp):
        self.call(self.E, 'sample.provide', sample_id=sid, volume_m3=max(5e-5, volume), composition=comp)
        self.call(self.A, 'sampler.load', rack=rack, position=pos, item_id=sid, volume_m3=volume)
        return dict(rack=rack, position=pos, type='sample')

    def wash_cup(self, pos=22):
        if ('S', pos) not in self.h.racks:
            self.call(self.A, 'sampler.load', rack='S', position=pos, item_id='wash_water', volume_m3=1e-5)
        return dict(rack='S', position=pos, type='baseline')

    def run(self, cups, sample_s=60.0, wash_s=30.0, **kw):
        self.call(self.A, 'run.start', cups=cups, sample_s=sample_s, wash_s=wash_s, **kw)
        for _ in range(2000):
            if self.read('run.status')['state'] not in ('running', 'draining'):
                break
            self.step(60.0)
        return self.call(self.A, 'data.results')

    def conc(self, res, cup, ch):
        return next(r for r in res['results'] if r['cup'] == cup and r['channel'] == ch)


def _raised_codes():
    """Error codes each command can raise: its own ActionRejected calls plus those of the helpers it calls (static scan of the module source)."""
    src = Path(rt.__file__).read_text()
    funcs = {}
    for m in re.finditer(r'\n    def (\w+)\(self[^\n]*\n(.*?)(?=\n    def |\n# ----|\nclass |\n    _NOCOPY)', src, re.S):
        funcs[m.group(1)] = m.group(2)
    own = {k: set(re.findall(r"ActionRejected\('([A-Z_]+)'\)", v)) for k, v in funcs.items()}
    calls = {k: set(re.findall(r'self\.(_\w+)\(', v)) & set(funcs) for k, v in funcs.items()}
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
        out[k[5:].replace('_', '.', 1)] = codes
    return out


_READY = {}


def ready_rig(seed=1):
    """A started-up rig with the mixed calibrants on the standard rack (re-used read-only by several tests through deep copies would be costly;
    built fresh per call)."""
    g = Rig(seed=seed)
    g.ready()
    cal = g.standards()
    return g, cal


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_and_mirrors_consistent(self):
        meta = rt.export_seal_aa500_contract_metadata()
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
            self.assertTrue(c['function_zh'])
            self.assertIn(c['kind'], ('query', 'device_command', 'robot_skill', 'simulation', 'analysis', 'configuration'))
            self.assertIn(c['model_effects']['mode'], ('none', 'direct', 'coupled', 'host_required'))
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for k in rt.SCENE_MIRRORED:
            self.assertEqual(meta['state_slots'][k]['visibility'], 'agent', k)
        mech = meta['mechanisms']['segmented_flow_process']
        for name, p in mech['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)
        self.assertEqual(len(meta['capabilities']), 40)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_seal_aa500_contract_metadata()
        for cap, codes in _raised_codes().items():
            if cap not in meta['capabilities']:
                cap = next(k for k in meta['capabilities'] if k.replace('.', '_') == cap.replace('.', '_'))
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            r = g.A.invoke(instance_id=INST, capability_id=k, arguments={}, request_id='u-' + k)
            self.assertEqual(r['status'], 'unsupported')
            self.assertEqual(r['error']['code'], 'UNSUPPORTED')

    def test_agent_truth_isolation(self):
        g = Rig()
        d = g.A.describe(instance_id=INST)
        self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']))
        self.assertFalse(any(c['visibility'] != 'agent' for c in d['capabilities'].values()))
        for c in d['capabilities'].values():
            self.assertFalse(any(e['state_id'].startswith('truth.') for e in c['state_effects']))
        with self.assertRaises(rt.ActionRejected):
            g.A.read(instance_id=INST, state_ids=['truth.streams'])
        for cap, args in (('truth.inspect', {}), ('sim.step', dict(dt_s=1.0)), ('kit.provide', dict(kit='wash_water')),
                          ('sample.provide', dict(sample_id='x', volume_m3=1e-5, composition={}))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        self.assertIn('truth.streams', g.E.describe(instance_id=INST)['state_slots'])
        g.call(g.E, 'fault.inject', fault='probe_blocked', active=True)
        ev = g.call(g.A, 'events.read', since_s=0.0)['events']
        self.assertFalse(any(e['kind'] == 'fault' for e in ev))
        self.assertTrue(any(e['kind'] == 'fault' for e in g.call(g.E, 'events.read', since_s=0.0)['events']))

    def test_foreign_agent_ownership(self):
        g = Rig()
        g.h.register_principal('agent2', 'agent')
        B = g.h.session('agent2')
        g.kits('nitrate_stock', 'wash_water')
        g.call(g.A, 'standard.prepare', standard_id='mine', volume_m3=1e-4, components=[dict(source_id='nitrate_stock', volume_m3=1e-7)])
        g.call(g.A, 'sampler.load', rack='S', position=1, item_id='mine', volume_m3=1e-5)
        r = B.invoke(instance_id=INST, capability_id='sampler.load', arguments=dict(rack='S', position=2, item_id='mine', volume_m3=1e-5), request_id='b1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = B.invoke(instance_id=INST, capability_id='standard.prepare', arguments=dict(standard_id='b', volume_m3=1e-4,
                                                                                        components=[dict(source_id='mine', volume_m3=1e-5)]), request_id='b2')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = B.invoke(instance_id=INST, capability_id='sampler.unload', arguments=dict(rack='S', position=1), request_id='b3')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = B.invoke(instance_id=INST, capability_id='run.start', arguments=dict(cups=[dict(rack='S', position=1, type='sample')], sample_s=40.0, wash_s=20.0),
                     request_id='b4')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('mine', B.read(instance_id=INST, state_ids=['samples.available'])['observations']['samples.available']['value'])
        # evaluator-provided stocks are shared; agent 2 prepares its own standard
        r = B.invoke(instance_id=INST, capability_id='standard.prepare', arguments=dict(standard_id='theirs', volume_m3=1e-4,
                                                                                        components=[dict(source_id='nitrate_stock', volume_m3=1e-7)]), request_id='b5')
        self.assertEqual(r['status'], 'succeeded')
        g.call(g.A, 'sampler.unload', rack='S', position=1)
        r = B.invoke(instance_id=INST, capability_id='sampler.load', arguments=dict(rack='S', position=1, item_id='theirs', volume_m3=1e-5), request_id='b6')
        self.assertEqual(r['status'], 'succeeded')

    def test_idempotent_request_ids(self):
        g = Rig()
        g.kits('wash_water')
        args = dict(rack='A', position=3, item_id='wash_water', volume_m3=5e-6)
        r1 = g.A.invoke(instance_id=INST, capability_id='sampler.load', arguments=args, request_id='same')
        snap = g.h.read('evaluator')
        r2 = g.A.invoke(instance_id=INST, capability_id='sampler.load', arguments=args, request_id='same')
        self.assertEqual(r1, r2)
        self.assertEqual(snap, g.h.read('evaluator'))
        r3 = g.A.invoke(instance_id=INST, capability_id='sampler.load', arguments=dict(args, position=4), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')
        r4 = g.A.invoke(instance_id=INST, capability_id='sampler.load', arguments=args, request_id='other')
        self.assertEqual(r4['error']['code'], 'POSITION_OCCUPIED')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='aa500_a'), Rig(instance='aa500_b', room=300.15)
        a.call(a.A, 'power.set', on=True)
        self.assertFalse(b.read('power.on'))
        with self.assertRaises(rt.ActionRejected):
            a.A.read(instance_id='aa500_b')
        r = a.A.invoke(instance_id='aa500_b', capability_id='power.set', arguments=dict(on=True), request_id='z')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')

    def test_step_partition_independence(self):
        def run(steps):
            g = Rig(seed=5)
            g.kits()
            g.reagents()
            g.call(g.A, 'power.set', on=True)
            g.call(g.A, 'system.startup')
            for s in steps:
                g.step(s)
            r = g.read()
            return r['detector.absorbance'], r['baths.temperature_k'], r['system.state'], r['truth.streams']['ch1']['equivalent_mol_m3']
        total = 1500.0
        a = run([total])
        b = run([7.0, 53.0, 1000.0, 0.5, 439.5])
        c = run([total / 30.0] * 30)
        d = run([0.05] * 2000 + [1400.0])                 # the scene advances the runtime in 0.05 s environment steps
        e = run([0.37, 13.11, 486.52, 1000.0])
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertEqual(a, d)
        self.assertEqual(a, e)

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.SealAa500Runtime('fresh')
        r.register_principal('e', 'evaluator')
        out = r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')
        self.assertEqual(out['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', room_k=295.15, mains_voltage_v=230.0, noise_seed=2)
        g.call(g.A, 'system.startup', expect='POWER_OFF')
        g.call(g.A, 'sampler.move', expect='POWER_OFF', target='wash')
        g.kits('wash_water', 'reagents_nitrate_nitrite')
        g.kits('nitrate_stock', volume=1e-6)
        g.call(g.A, 'standard.prepare', expect='VOLUME_OUT_OF_RANGE', standard_id='s', volume_m3=1e-5, components=[dict(source_id='wash_water', volume_m3=2e-5)])
        g.call(g.A, 'standard.prepare', expect='UNKNOWN_ITEM', standard_id='s', volume_m3=1e-4, components=[dict(source_id='nope', volume_m3=1e-5)])
        g.call(g.A, 'standard.prepare', expect='INSUFFICIENT_VOLUME', standard_id='s', volume_m3=1e-4, components=[dict(source_id='nitrate_stock', volume_m3=2e-6)])
        g.call(g.A, 'sampler.load', expect='UNKNOWN_ITEM', rack='A', position=1, item_id='nitrate_nitrite:colour_reagent', volume_m3=1e-6)
        g.call(g.A, 'sampler.load', expect='INVALID_ARGUMENT', rack='S', position=30, item_id='wash_water', volume_m3=1e-6)
        g.call(g.A, 'sampler.load', rack='S', position=1, item_id='wash_water', volume_m3=1e-6)
        g.call(g.A, 'sampler.load', expect='POSITION_OCCUPIED', rack='S', position=1, item_id='wash_water', volume_m3=1e-6)
        g.call(g.A, 'sampler.unload', expect='POSITION_EMPTY', rack='S', position=2)
        g.call(g.A, 'reagent.load', expect='WRONG_REAGENT', channel='ch2', reagent='colour_reagent', volume_m3=1e-4)
        g.call(g.A, 'reagent.load', expect='UNKNOWN_ITEM', channel='ch2', reagent='complexing_reagent', volume_m3=1e-4)
        g.call(g.A, 'reagent.load', expect='BOTTLE_FULL', channel='ch1', reagent='colour_reagent', volume_m3=1e-3)
        g.call(g.A, 'cadmium.activate', expect='NO_CADMIUM', channel='ch3')
        g.call(g.A, 'cadmium.activate', expect='UNKNOWN_ITEM', channel='ch1')
        g.call(g.A, 'pumptube.install', expect='UNKNOWN_METHOD', channel='ch1', method_id='nope')
        g.call(g.A, 'pumptube.install', expect='METHOD_NOT_SUPPORTED', channel='ch1', method_id='MT508_A085_chloride')
        g.call(g.A, 'pumptube.install', expect='WRONG_MANIFOLD', channel='ch1', method_id='MT507_A026_nitrate_nitrite')
        g.call(g.A, 'pumptube.install', expect='PUMP_LID_CLOSED', channel='ch1', method_id='MT508_A047_nitrite')
        g.call(g.A, 'led.install', expect='COVER_CLOSED', channel='ch1', wavelength_m=660e-9)
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'platen.set', engaged=True)
        g.call(g.A, 'pump.lid_set', expect='PLATEN_ENGAGED', open=True)
        g.call(g.A, 'channel.configure', expect='TUBES_MISMATCH', channel='ch1', method_id='MT508_A047_nitrite')
        g.call(g.A, 'run.start', expect='PUMP_NOT_FLOWING', cups=[dict(rack='S', position=1, type='baseline')], sample_s=40.0, wash_s=20.0)
        g.call(g.A, 'lamp.set', channel='ch1', on=True)
        g.call(g.A, 'cover.set', expect='LAMP_ON', channel='ch1', open=True)
        g.call(g.A, 'run.stop', expect='NO_RUN')
        g.call(g.A, 'data.recalculate', expect='NO_RUN', fit='auto', carryover_correction=True, drift_correction=True)
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='bath_failure', active=True, channel='ch1')
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='led_failure', active=True)
        g.call(g.E, 'fault.inject', expect='NO_CADMIUM', fault='cadmium_air', active=True, channel='ch2')
        g.call(g.E, 'plant.configure', room_k=295.15, mains_voltage_v=180.0, noise_seed=1)
        self.assertFalse(g.read('power.on'))
        g.call(g.A, 'power.set', expect='MAINS_OUT_OF_RANGE', on=True)
        g.call(g.A, 'power.press_switch', expect='MAINS_OUT_OF_RANGE')


class Physics(unittest.TestCase):
    def test_epa_stock_recipes_and_nominal_labels(self):
        for kit, (sid, species, mass) in rt.STOCKS.items():
            e = rt.substances()[sid]
            true = mass * e['element_mass_fraction']
            self.assertAlmostEqual(true / rt.STOCK_NOMINAL[kit], 1.0, delta=1.5e-3, msg=kit)
        g = Rig()
        g.kits('nitrate_stock', 'wash_water')
        out = g.call(g.A, 'standard.prepare', standard_id='n1', volume_m3=1e-4, components=[dict(source_id='nitrate_stock', volume_m3=1e-7),
                                                                                             dict(source_id='wash_water', volume_m3=9.99e-5)])
        self.assertAlmostEqual(out['nominal_kg_m3']['ch1'], 0.001, places=12)
        self.assertAlmostEqual(g.read('truth.items')['n1']['composition']['nitrate_n'], 0.001, delta=2e-6)

    def test_manifold_design_rule_and_epa366_silicate_flows(self):
        for c, m in rt.DEFAULT_METHOD.items():
            tb = rt.design_manifold(m, 0.010)
            eps = rt.methods()[m]['chemistry']['molar_absorptivity_m2_mol']
            a_top = eps * 0.010 * rt.method_range(m)['high_mol'] * tb['sample_fraction']
            self.assertTrue(0.5 < a_top < 1.2, (c, a_top))
        tb = rt.design_manifold('MT508_A030_silicate', 0.010)
        q = {k: v * 6e7 for k, v in tb['reagents_m3_s'].items()}
        self.assertAlmostEqual(q['molybdate'], 0.41, places=9)
        self.assertAlmostEqual(q['oxalic_acid'], 0.32, places=9)
        self.assertAlmostEqual(tb['air_m3_s'] * 6e7, 0.25, places=9)
        # the 50 mm cell for the low range: the design picks a smaller sample fraction for the same top absorbance
        self.assertLess(rt.design_manifold('MT508_A029_phosphate', 0.050)['sample_fraction'], rt.design_manifold('MT508_A029_phosphate', 0.010)['sample_fraction'])

    def test_startup_platen_baths_and_shutdown(self):
        g = Rig()
        g.kits()
        g.reagents()
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'system.startup')
        self.assertEqual(g.read('pump.platen_position_m'), 0.0)
        self.assertEqual(g.read('pump.rotor_velocity_rad_s'), 0.0)
        g.step(4.0)
        self.assertAlmostEqual(g.read('pump.platen_position_m'), 0.004, places=9)
        g.step(1.0)
        self.assertAlmostEqual(g.read('pump.rotor_velocity_rad_s'), 2 * math.pi * 10 / 60, places=9)
        self.assertEqual(g.read('system.state'), 'starting')
        g.step(590.0)
        self.assertEqual(g.read('channels.status')['ch1']['valve'], 'wash')
        g.step(10.0)
        self.assertEqual(g.read('channels.status')['ch1']['valve'], 'reagent')
        t = None
        for _ in range(60):
            g.step(60.0)
            if g.read('system.state') == 'ready':
                t = g.h.t
                break
        self.assertTrue(t is not None and 1200.0 <= t <= 2700.0, t)
        self.assertTrue(all(abs(v - 310.15) <= 0.5 for v in g.read('baths.temperature_k').values()))
        st = g.read('channels.status')
        self.assertTrue(all(s['led_warm'] for s in st.values()))
        g.call(g.A, 'system.shutdown')
        self.assertEqual(g.read('pump.speed'), 'fast')
        g.step(899.0)
        self.assertEqual(g.read('system.state'), 'shutting_down')
        g.step(10.0)
        r = g.read()
        self.assertEqual(r['system.state'], 'idle')
        self.assertFalse(r['pump.running'])
        self.assertFalse(r['pump.platen_engaged'])
        self.assertFalse(any(s['led_on'] for s in r['channels.status'].values()))
        g.step(10.0)
        self.assertEqual(g.read('pump.platen_position_m'), 0.0)
        g.call(g.A, 'pump.lid_set', open=True)
        self.assertAlmostEqual(g.read('pump.lid_angle_rad'), 1.2)
        g.call(g.A, 'system.startup', expect='PUMP_LID_OPEN')

    def test_transport_delay_and_steady_state_peak(self):
        g, cal = ready_rig()
        g.call(g.A, 'sampler.move', target='position', rack='S', position=6, probe_down=True)
        t0 = g.h.t
        fl = g.h._flows('ch3')
        delay = g.h._delay_s(fl)
        self.assertTrue(40.0 < delay < 200.0, delay)
        base = g.read('detector.absorbance')['ch3']
        g.step(delay - 10.0)
        self.assertLess(g.read('detector.absorbance')['ch3'] - base, 0.01)
        g.step(120.0)
        a = g.read('detector.absorbance')['ch3']
        g.step(60.0)
        b = g.read('detector.absorbance')['ch3']
        self.assertGreater(a - base, 0.5)
        self.assertAlmostEqual(a, b, delta=0.01)
        tr = g.call(g.E, 'truth.inspect')['state']['streams']['ch3']
        eq = 0.008 / (rt.MW['P'] * 1e-3) * fl['fraction']
        self.assertAlmostEqual(tr['equivalent_mol_m3'] / eq, 1.0, delta=0.04)
        self.assertGreater(g.h.t, t0)
        # probe position mirrors
        x, y = rt.rack_position_xy('S', 6)
        self.assertAlmostEqual(g.read('sampler.probe_x_m'), x)
        self.assertAlmostEqual(g.read('sampler.probe_y_m'), y)
        self.assertAlmostEqual(g.read('sampler.probe_z_m'), rt.PROBE_TRAVEL_Z)
        g.call(g.A, 'sampler.unload', expect='PROBE_IN_TUBE', rack='S', position=6)
        g.call(g.A, 'sampler.move', target='home')
        g.step(5.0)
        self.assertEqual((g.read('sampler.probe_x_m'), g.read('sampler.probe_z_m')), (0.0, 0.0))

    def test_calibration_and_mixed_sample_recovery(self):
        g, cal = ready_rig(seed=3)
        s = g.sample('river', 1, nitrate_n=0.0012, nitrite_n=0.0001, ammonium_n=0.0015, phosphate_p=0.002, silicate_si=0.012)
        res = g.run([g.wash_cup()] + cal + [s, dict(rack='S', position=4, type='drift'), g.wash_cup()])
        for c, v in res['calibration'].items():
            self.assertEqual(v['order'], 1, c)
            self.assertGreater(v['r2'], 0.999, c)
        truth = dict(ch1=0.0013 * 0.99, ch2=0.0015, ch3=0.002, ch4=0.012)
        for c, tv in truth.items():
            found = g.conc(res, 8, c)['concentration_kg_m3']
            self.assertAlmostEqual(found / tv, 1.0, delta=0.05, msg=c)            # single injection, RSD about 1 % (EPA 366.0)
        self.assertEqual(res['state'], 'completed')
        led = g.call(g.E, 'ledger.read')['ledger']
        q_s = sum(g.h._flows(c)['sample'] for c in rt.CHANNELS)
        self.assertAlmostEqual(led['sample_used_m3'] / (q_s * 60.0 * 10), 1.0, delta=0.05)
        self.assertGreater(led['nitrate_reduced_mol'], 0.0)

    def test_repeatability_and_detection_limit(self):
        g, cal = ready_rig(seed=11)
        mid = [dict(rack='S', position=4, type='sample') for _ in range(7)]
        blanks = [dict(rack='S', position=1, type='sample') for _ in range(7)]
        res = g.run([g.wash_cup()] + cal + mid + blanks + [g.wash_cup()], sample_s=40.0, wash_s=20.0)
        for c in rt.CHANNELS:
            v = [g.conc(res, 8 + i, c)['concentration_kg_m3'] for i in range(7)]
            m = sum(v) / 7
            sd = math.sqrt(sum((x - m) ** 2 for x in v) / 6)
            self.assertLess(sd / m, 0.02, c)
            self.assertGreater(sd / m, 0.0005, c)
            b = [g.conc(res, 15 + i, c)['concentration_kg_m3'] for i in range(7)]
            mb = sum(b) / 7
            mdl = 3.14 * math.sqrt(sum((x - mb) ** 2 for x in b) / 6)
            self.assertLess(mdl, 0.03 * rt.method_range(rt.DEFAULT_METHOD[c])['high_mol'] * {'ch4': 0.028, 'ch3': 0.031}.get(c, 0.014), c)
        PROCESS_EVIDENCE.setdefault('repeatability', []).append(dict(note='7 replicate mid standards, 7 blanks'))

    def test_carryover_grows_with_shorter_wash_and_throughput_limit(self):
        def k_of(sample_s, wash_s):
            g, _ = ready_rig(seed=4)
            hi = dict(rack='S', position=6, type='carryover_high')
            lo = dict(rack='S', position=1, type='carryover_low')
            res = g.run([g.wash_cup()] + [hi, lo, dict(lo)] + [g.wash_cup()], sample_s=sample_s, wash_s=wash_s)
            g.call(g.A, 'run.start', expect='RATE_TOO_HIGH', cups=[lo], sample_s=20.0, wash_s=5.0)
            return res, g
        res, g = k_of(60.0, 30.0)
        self.assertEqual(res['calibration'], {})
        k_long = g.h._corrected(g.h.last_run)['ch3']['carryover']
        _, g2 = k_of(25.0, 7.0)
        k_short = g2.h._corrected(g2.h.last_run)['ch3']['carryover']
        self.assertLess(k_long, 0.02)
        self.assertGreater(k_short, k_long)
        self.assertGreater(k_short, 0.005)

    def test_auto_dilution_and_over_range(self):
        g, cal = ready_rig(seed=6)
        s = g.sample('hot', 1, nitrate_n=0.010, ammonium_n=0.002, phosphate_p=0.003, silicate_si=0.010)
        res = g.run([g.wash_cup()] + cal + [s, g.wash_cup()], auto_dilute=True, dilution_factor=10.0)
        rows = [r for r in res['results'] if r['rack'] == 'A']
        orig = next(r for r in rows if r['channel'] == 'ch1' and 'AUTO_DILUTED' not in r['flags'])
        redo = next(r for r in rows if r['channel'] == 'ch1' and 'AUTO_DILUTED' in r['flags'])
        self.assertIn('OVER_RANGE', orig['flags'])
        self.assertEqual(redo['dilution'], 10.0)
        self.assertAlmostEqual(redo['concentration_kg_m3'] / (0.010 * 0.99), 1.0, delta=0.05)
        codes = [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']]
        self.assertIn('AUTO_DILUTION', codes)

    def test_quadratic_fit_above_threshold_and_recalculate(self):
        g = Rig(seed=8)
        g.ready()
        g.call(g.A, 'run.start', expect='POSITION_EMPTY', cups=[dict(rack='S', position=1, type='baseline')], sample_s=40.0, wash_s=20.0)
        tops = dict(TOPS, phosphate_stock=0.016)                    # twice the range: the top calibrants pass 1.2 AU (Beer-Lambert stays linear here)
        cal = g.standards(tops=tops)
        res = g.run([g.wash_cup()] + cal + [g.wash_cup()])
        self.assertEqual(res['calibration']['ch3']['order'], 2)
        lin = g.call(g.A, 'data.recalculate', fit='linear', carryover_correction=True, drift_correction=True)
        self.assertEqual(lin['calibration']['ch3']['order'], 1)
        self.assertEqual(g.call(g.A, 'data.results')['calibration']['ch3']['order'], 1)

    def test_drift_correction(self):
        g = Rig(seed=9)
        g.ready()
        cal = g.standards()
        g.call(g.E, 'fault.inject', fault='pump_tube_worn', active=True, channel='ch2')
        g.call(g.E, 'fault.inject', fault='pump_tube_worn', active=False, channel='ch2')
        s = g.sample('w', 1, ammonium_n=0.003)
        drift = dict(rack='S', position=4, type='drift')
        # sensitivity drops after the calibration (sample tube wear): drift standards before and after the sample correct it
        cups = [g.wash_cup()] + cal + [drift, s, drift, g.wash_cup()]
        g.call(g.A, 'run.start', cups=cups, sample_s=60.0, wash_s=30.0)
        while g.h.run['index'] < 8:                                  # the sampler is about to take the sample cup
            g.step(1.0)
        g.call(g.E, 'fault.inject', fault='pump_tube_worn', active=True, channel='ch2')
        while g.read('run.status')['state'] in ('running', 'draining'):
            g.step(60.0)
        on = g.conc(g.call(g.A, 'data.results'), 9, 'ch2')['concentration_kg_m3']
        off = g.call(g.A, 'data.recalculate', fit='auto', carryover_correction=True, drift_correction=False)
        off = g.conc(g.call(g.A, 'data.results'), 9, 'ch2')['concentration_kg_m3']
        self.assertLess(abs(on / 0.003 - 1.0), abs(off / 0.003 - 1.0))
        self.assertLess(off / 0.003, 0.95)

    def test_cadmium_efficiency_check_air_and_activation(self):
        def eff(prep=None):
            g = Rig(seed=12)
            g.ready()
            if prep:
                prep(g)
            g.call(g.A, 'standard.prepare', standard_id='no2', volume_m3=1e-4, components=[dict(source_id='nitrite_stock', volume_m3=2e-7),
                                                                                             dict(source_id='wash_water', volume_m3=9.98e-5)])
            g.call(g.A, 'standard.prepare', standard_id='no3', volume_m3=1e-4, components=[dict(source_id='nitrate_stock', volume_m3=2e-7),
                                                                                             dict(source_id='wash_water', volume_m3=9.98e-5)])
            g.call(g.A, 'sampler.load', rack='S', position=1, item_id='no2', volume_m3=1e-5)
            g.call(g.A, 'sampler.load', rack='S', position=2, item_id='no3', volume_m3=1e-5)
            res = g.run([g.wash_cup(), dict(rack='S', position=1, type='nitrite_check'), dict(rack='S', position=2, type='nitrate_check'), g.wash_cup()])
            return g.h.last_run['calibration'] if g.h.last_run.get('calibration') else None, g, res
        _, g, _ = eff()
        e0 = g.h._corrected(g.h.last_run)['ch1']['peaks']
        r0 = e0[2] / e0[1]
        self.assertAlmostEqual(r0, 0.99, delta=0.04)
        self.assertNotIn('CADMIUM_EFFICIENCY_LOW', g.read('warnings.active'))

        def air(g):
            g.call(g.E, 'fault.inject', fault='cadmium_air', active=True, channel='ch1')
            g.step(300.0)
        _, g2, _ = eff(air)
        e1 = g2.h._corrected(g2.h.last_run)['ch1']['peaks']
        self.assertLess(e1[2] / e1[1], 0.8)
        g2.call(g2.A, 'cadmium.activate', expect='REAGENTS_FLOWING', channel='ch1')
        g2.call(g2.A, 'valve.set', channel='ch1', position='wash')
        g2.call(g2.A, 'cadmium.activate', channel='ch1')
        self.assertAlmostEqual(g2.h.ch['ch1']['cd_eff'], 0.99, delta=0.01)
        self.assertNotIn('cadmium_air:ch1', g2.read('truth.faults'))

    def test_cadmium_efficiency_low_warning_from_results(self):
        g = Rig(seed=13)
        g.ready()
        g.h.ch['ch1']['cd_eff'] = 0.6                                  # an exhausted coil (test set-up)
        for sid, kit in (('no2', 'nitrite_stock'), ('no3', 'nitrate_stock')):
            g.call(g.A, 'standard.prepare', standard_id=sid, volume_m3=1e-4, components=[dict(source_id=kit, volume_m3=2e-7)])
        g.call(g.A, 'sampler.load', rack='S', position=1, item_id='no2', volume_m3=1e-5)
        g.call(g.A, 'sampler.load', rack='S', position=2, item_id='no3', volume_m3=1e-5)
        res = g.run([g.wash_cup(), dict(rack='S', position=1, type='nitrite_check'), dict(rack='S', position=2, type='nitrate_check'), g.wash_cup()])
        self.assertIn('CADMIUM_EFFICIENCY_LOW', g.read('warnings.active'))
        self.assertIsNotNone(res)

    def test_chlorine_poisons_cadmium(self):
        g = Rig(seed=14)
        g.ready()
        s = g.sample('cl', 1, nitrate_n=0.001, chlorine=0.05)
        e0 = g.h.ch['ch1']['cd_eff']
        g.run([g.wash_cup(), s, g.wash_cup()])
        self.assertLess(g.h.ch['ch1']['cd_eff'], e0 - 0.02)

    def test_phosphate_interferences_arsenate_and_iron(self):
        g, cal = ready_rig(seed=15)
        s0 = g.sample('p', 1, phosphate_p=0.002)
        s1 = g.sample('pas', 2, phosphate_p=0.002, arsenate_as=0.004)
        s2 = g.sample('pfe', 3, phosphate_p=0.002, iron=0.02)
        s3 = g.sample('psal', 4, phosphate_p=0.002, salinity=35.0)
        res = g.run([g.wash_cup()] + cal + [s0, s1, s2, s3, g.wash_cup()])
        p0, p1, p2, p3 = (g.conc(res, 8 + i, 'ch3')['concentration_kg_m3'] for i in range(4))
        self.assertAlmostEqual(p0 / 0.002, 1.0, delta=0.05)
        self.assertAlmostEqual((p1 - p0) / (0.004 / rt.MW['As'] * rt.MW['P']), 1.0, delta=0.1)       # equimolar arsenate response
        self.assertLess(p2, 0.8 * p0)
        self.assertGreater(p3, p0 + 0.00003)                                                          # refractive-index blank adds

    def test_bath_temperature_controls_colour(self):
        g = Rig(seed=16)
        g.ready()
        hot = g.h.ch['ch2']['phi']
        g.call(g.A, 'bath.set', channel='ch2', temperature_k=None)
        g.step(3600.0)
        self.assertLess(g.h.ch['ch2']['bath_T'], 296.0)
        self.assertLess(g.h.ch['ch2']['phi'], hot * 0.9)
        g.call(g.A, 'bath.set', channel='ch2', temperature_k=310.15)
        g.call(g.E, 'fault.inject', fault='bath_failure', active=True, channel='ch2')
        g.step(3600.0)
        self.assertGreater(g.read('baths.temperature_k')['ch2'], 314.0)

    def test_led_wavelength_and_flowcell_path(self):
        def peak(setup):
            g = Rig(seed=17)
            g.kits()
            g.reagents()
            g.call(g.A, 'power.set', on=True)
            setup(g)
            g.call(g.A, 'system.startup')
            for _ in range(60):
                g.step(60.0)
                if g.read('system.state') == 'ready':
                    break
            cal = g.standards(levels=[0.0, 0.25])
            g.call(g.A, 'sampler.move', target='position', rack='S', position=2, probe_down=True)
            base = g.read('detector.absorbance')['ch3']
            g.step(300.0)
            return g.read('detector.absorbance')['ch3'] - base

        def swap(wl=None, cell=None):
            def f(g):
                g.call(g.A, 'cover.set', channel='ch3', open=True)
                self.assertAlmostEqual(g.read('cover.ch3_angle_rad'), 1.3)
                g.call(g.A, 'lamp.set', expect='COVER_OPEN', channel='ch3', on=True)
                if wl:
                    g.call(g.A, 'led.install', channel='ch3', wavelength_m=wl)
                if cell:
                    g.call(g.A, 'flowcell.install', channel='ch3', path_m=cell)
                g.call(g.A, 'cover.set', channel='ch3', open=False)
            return f
        ref = peak(lambda g: None)
        wrong = peak(swap(wl=540e-9))
        long = peak(swap(cell=0.030))
        self.assertLess(wrong, 0.1 * ref)
        self.assertAlmostEqual(long / ref, 3.0, delta=0.15)

    def test_pumptube_change_method_and_configure(self):
        g = Rig(seed=18)
        g.call(g.A, 'pump.lid_set', open=True)
        out = g.call(g.A, 'pumptube.install', channel='ch1', method_id='MT508_A047_nitrite')
        self.assertGreater(out['sample_fraction'], 0.0)
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'channel.configure', channel='ch1', method_id='MT508_A047_nitrite')
        st = g.read('channels.status')['ch1']
        self.assertEqual((st['chemistry'], st['seal_method']), ('nitrite', 'A047'))
        self.assertAlmostEqual(st['range_kg_m3'][1], 0.0008)
        self.assertEqual(st['result_basis'], 'N')
        self.assertEqual(g.read('channels.status')['ch4']['range_basis'], 'SiO2')
        g.call(g.A, 'platen.set', expect='PUMP_LID_OPEN', engaged=True)
        g.call(g.A, 'pump.set', expect='PUMP_LID_OPEN', running=True)
        g.call(g.A, 'pump.lid_set', open=False)
        g.call(g.A, 'platen.set', engaged=True)
        g.call(g.A, 'pump.set', running=True, speed='normal')
        g.step(5.0)
        g.call(g.A, 'flowcell.install', expect='COVER_CLOSED', channel='ch2', path_m=0.05)
        g.call(g.A, 'cover.set', channel='ch2', open=True)
        g.call(g.A, 'flowcell.install', expect='PUMP_RUNNING', channel='ch2', path_m=0.05)
        g.call(g.A, 'pump.set', running=False)
        g.call(g.A, 'flowcell.install', channel='ch2', path_m=0.05)
        g.call(g.A, 'lamp.set', channel='ch2', on=False)
        g.call(g.A, 'cover.set', channel='ch2', open=False)
        self.assertEqual(g.read('cover.ch2_angle_rad'), 0.0)

    def test_faults_reagent_empty_leak_probe_led(self):
        g, cal = ready_rig(seed=19)
        a0 = g.read('detector.absorbance')['ch2']
        g.call(g.E, 'fault.inject', fault='reagent_empty', active=True, channel='ch2')
        g.step(400.0)
        self.assertLess(g.read('detector.absorbance')['ch2'], a0 - 0.02)            # reagent blank disappears
        g.call(g.E, 'fault.inject', fault='reagent_empty', active=False, channel='ch2')
        g.call(g.E, 'fault.inject', fault='led_failure', active=True, channel='ch4')
        g.step(1.0)
        self.assertIsNone(g.read('detector.absorbance')['ch4'])
        g.call(g.E, 'fault.inject', fault='led_failure', active=False, channel='ch4')
        g.call(g.E, 'fault.inject', fault='probe_blocked', active=True)
        g.call(g.A, 'sampler.move', target='position', rack='S', position=6, probe_down=True)
        b0 = g.read('detector.absorbance')['ch3']
        g.step(300.0)
        self.assertLess(abs(g.read('detector.absorbance')['ch3'] - b0), 0.03)
        g.call(g.E, 'fault.inject', fault='probe_blocked', active=False)
        g.call(g.E, 'fault.inject', fault='leak', active=True)
        g.step(1.0)
        self.assertIn('LEAK', g.read('warnings.active'))
        self.assertFalse(g.read('pump.running'))
        self.assertEqual(g.read('pump.rotor_velocity_rad_s'), 0.0)
        g.call(g.A, 'pump.set', expect='LEAK_DETECTED', running=True)

    def test_reagent_consumption_and_low_warning(self):
        g = Rig(seed=20)
        g.kits()
        g.reagents(volume=5e-6)
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'system.startup')
        g.step(1500.0)
        lv = g.read('reagents.levels_m3')['ch1']
        self.assertTrue(any(v < 1e-6 for v in lv.values()))
        self.assertIn('REAGENT_LOW_ch1', g.read('warnings.active'))
        led = g.call(g.E, 'ledger.read')['ledger']
        self.assertAlmostEqual(led['reagent_used_m3'], 5e-6 * sum(len(ch['reagents']) for ch in g.h.ch.values()) - sum(sum(v.values()) for v in g.read('reagents.levels_m3').values()),
                               delta=1e-12)

    def test_power_cut_during_run_keeps_completed_cups(self):
        g, cal = ready_rig(seed=21)
        g.call(g.A, 'run.start', cups=[g.wash_cup()] + cal, sample_s=60.0, wash_s=30.0)
        while g.read('run.status')['completed'] < 3:
            g.step(30.0)
        g.call(g.A, 'power.press_switch')
        r = g.read('run.status')
        self.assertEqual(r['state'], 'stopped')
        self.assertGreaterEqual(r['completed'], 3)
        self.assertEqual(g.read('system.state'), 'off')
        res = g.call(g.A, 'data.results')
        self.assertEqual(res['state'], 'stopped')
        self.assertEqual(len({x['cup'] for x in res['results']}), r['completed'])            # R1-03: completed cups keep their results
        self.assertFalse(g.read('pump.running'))                                          # R1-04: the pump motor stops with the power
        g.call(g.A, 'power.press_switch')
        self.assertTrue(g.read('lamp.power'))
        self.assertFalse(g.read('pump.running'))

    def test_run_stop_and_raw_trace_bubbles(self):
        g, cal = ready_rig(seed=22)
        g.call(g.A, 'run.start', cups=[g.wash_cup()] + cal, sample_s=60.0, wash_s=30.0)
        g.call(g.A, 'sampler.move', expect='RUN_ACTIVE', target='home')
        g.call(g.A, 'sampler.unload', expect='RUN_ACTIVE', rack='S', position=6)
        g.call(g.A, 'system.startup', expect='BUSY')
        g.step(600.0)
        out = g.call(g.A, 'run.stop')
        self.assertEqual(out['state'], 'stopped')
        deb = g.call(g.A, 'data.read_trace', channel='ch3', kind='debubbled', max_points=500)
        raw = g.call(g.A, 'data.read_trace', channel='ch3', kind='raw', max_points=500)
        self.assertEqual(len(deb['time_s']), 500)
        spikes = sum(1 for a, b in zip(deb['absorbance'], raw['absorbance']) if b - a > 0.5)
        self.assertGreater(spikes, 20)
        self.assertLess(max(deb['absorbance']), 1.0)

    def test_led_warmup_drift_settles(self):
        g = Rig(seed=23)
        g.kits()
        g.reagents()
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'system.startup')
        g.step(10.0)
        early = g.h.ch['ch1']['A_meas'] - g.h.ch['ch1']['A_true']
        g.step(1800.0)
        late = g.h.ch['ch1']['A_meas'] - g.h.ch['ch1']['A_true'] - g.h.ch['ch1']['drift']
        self.assertGreater(early, 5e-4)
        self.assertLess(abs(late), 1e-3)

    def test_events_provenance_ledger(self):
        g, cal = ready_rig(seed=24)
        res = g.run([g.wash_cup()] + cal[:2] + [g.wash_cup()], sample_s=40.0, wash_s=20.0)
        codes = [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']]
        for c in ('POWER_ON', 'STARTUP', 'REAGENTS_ON', 'READY', 'RUN_START', 'RUN_COMPLETED'):
            self.assertIn(c, codes)
        prov = g.call(g.A, 'provenance.read')
        self.assertEqual({x['name'] for x in prov['libraries']}, {'methods', 'substances', 'instrument'})
        self.assertGreaterEqual(len(prov['methods']), 70)
        led = g.call(g.E, 'ledger.read')['ledger']
        self.assertGreater(led['waste_m3'], led['sample_used_m3'] + led['reagent_used_m3'] - 1e-12)
        out = g.call(g.A, 'sampler.unload', rack='S', position=1)
        self.assertGreater(out['removed_m3'], 0.0)
        self.assertEqual(res['run_id'], 'run001')


class ReviewRound1(unittest.TestCase):
    def test_mains_drop_during_run_declared_effects(self):                    # R1-02
        g, cal = ready_rig(seed=31)
        g.call(g.A, 'run.start', cups=[g.wash_cup()] + cal[:2], sample_s=40.0, wash_s=20.0)
        g.step(30.0)
        g.call(g.E, 'plant.configure', room_k=295.15, mains_voltage_v=150.0, noise_seed=31)
        self.assertEqual(g.read('run.status')['state'], 'stopped')
        self.assertEqual(g.read('pump.rotor_velocity_rad_s'), 0.0)

    def test_run_stop_sets_cadmium_warning_declared(self):                    # R1-07
        g = Rig(seed=32)
        g.ready()
        g.h.ch['ch1']['cd_eff'] = 0.6
        for sid, kit in (('no2', 'nitrite_stock'), ('no3', 'nitrate_stock')):
            g.call(g.A, 'standard.prepare', standard_id=sid, volume_m3=1e-4, components=[dict(source_id=kit, volume_m3=2e-7)])
        g.call(g.A, 'sampler.load', rack='S', position=1, item_id='no2', volume_m3=1e-5)
        g.call(g.A, 'sampler.load', rack='S', position=2, item_id='no3', volume_m3=1e-5)
        g.call(g.A, 'run.start', cups=[g.wash_cup(), dict(rack='S', position=1, type='nitrite_check'), dict(rack='S', position=2, type='nitrate_check'), g.wash_cup()],
               sample_s=40.0, wash_s=20.0)
        while g.read('run.status')['completed'] < 3:
            g.step(30.0)
        g.call(g.A, 'run.stop')
        self.assertIn('CADMIUM_EFFICIENCY_LOW', g.read('warnings.active'))

    def test_detector_saturates_and_flags(self):                              # R1-13
        g, cal = ready_rig(seed=33)
        s = g.sample('conc', 1, nitrate_n=0.5, ammonium_n=0.5, phosphate_p=0.5, silicate_si=0.5)
        g.call(g.A, 'sampler.move', target='position', rack='A', position=1, probe_down=True)
        g.step(300.0)
        self.assertTrue(all(v <= rt.ENG['detector_full_scale_au'] + 1e-12 for v in g.read('detector.absorbance').values()))
        g.call(g.A, 'sampler.move', target='home')
        g.step(200.0)
        res = g.run([g.wash_cup()] + cal + [s, g.wash_cup()])
        row = g.conc(res, 8, 'ch1')
        self.assertIn('DETECTOR_SATURATED', row['flags'])
        self.assertIsNone(row['concentration_kg_m3'])

    def test_missing_tube_and_short_sample_flags(self):                       # R1-05
        g, cal = ready_rig(seed=34)
        s1 = g.sample('a', 1, phosphate_p=0.002)
        s2 = g.sample('b', 2, volume=4e-7, phosphate_p=0.002)                  # 0.4 ml: the 0.7 ml/min sample lines run it dry during a 60 s sample
        g.call(g.A, 'run.start', cups=[g.wash_cup()] + cal + [s1, s2, dict(rack='B', position=5, type='sample'), g.wash_cup()], expect='POSITION_EMPTY',
               sample_s=60.0, wash_s=30.0)
        res = g.run([g.wash_cup()] + cal + [s1, s2, g.wash_cup()])
        self.assertIn('SAMPLE_SHORT', g.conc(res, 9, 'ch3')['flags'])
        g2, cal2 = ready_rig(seed=35)
        s = g2.sample('a', 1, phosphate_p=0.002)
        g2.call(g2.A, 'run.start', cups=[g2.wash_cup()] + cal2 + [s, g2.wash_cup()], sample_s=40.0, wash_s=20.0)
        g2.call(g2.A, 'sampler.unload', expect='RUN_ACTIVE', rack='A', position=1)
        g2.h.racks.pop(('A', 1))                                                 # a tube taken away by hand (test set-up)
        while g2.read('run.status')['state'] in ('running', 'draining'):
            g2.step(60.0)
        self.assertIn('NO_TUBE', g2.conc(g2.call(g2.A, 'data.results'), 8, 'ch3')['flags'])

    def test_liquid_ledger_closes_with_probe_up(self):                         # R1-06
        g = Rig(seed=36)
        g.ready()
        led0 = g.call(g.E, 'ledger.read')['ledger']
        g.call(g.A, 'sampler.move', target='home')
        g.step(600.0)
        led1 = g.call(g.E, 'ledger.read')['ledger']
        d = {k: led1[k] - led0[k] for k in led0}
        self.assertAlmostEqual(d['waste_m3'], d['wash_used_m3'] + d['reagent_used_m3'] + d['sample_used_m3'], delta=1e-10)

    def test_epa366_cycle_is_60_per_hour(self):                                # R1-10
        g, cal = ready_rig(seed=37)
        out = g.call(g.A, 'run.start', cups=[g.wash_cup()], sample_s=40.0, wash_s=20.0)
        self.assertAlmostEqual(out['throughput_per_s'] * 3600.0, 60.0, places=9)
        t0 = g.h.t
        while g.read('run.status')['state'] in ('running', 'draining'):
            g.step(0.5)
        cups = g.h.last_run['cups']
        self.assertAlmostEqual(cups[0]['t_end'] - cups[0]['t_start'], 40.0, places=6)


class ReviewRound2(unittest.TestCase):
    def test_clipped_calibrant_excluded_and_flagged(self):                     # R2-06
        g = Rig(seed=41)
        g.ready()
        tops = dict(TOPS, nitrate_stock=0.016)                                  # 0 / 3.2 / ... / 16 mg N/l: the top calibrants clip at full scale
        cal = g.standards(tops=tops, levels=[0.0, 0.25, 0.5, 0.75, 1.0])
        s = g.sample('mid', 1, nitrate_n=0.006)
        res = g.run([g.wash_cup()] + cal + [s, g.wash_cup()], sample_s=40.0, wash_s=20.0)
        flagged = [r for r in res['results'] if r['type'] == 'calibrant' and r['channel'] == 'ch1' and 'DETECTOR_SATURATED' in r['flags']]
        self.assertGreaterEqual(len(flagged), 1)
        self.assertLess(res['calibration']['ch1']['max_conc'], 0.0159)
        row = g.conc(res, 7, 'ch1')
        self.assertAlmostEqual(row['concentration_kg_m3'] / (0.006 * 0.99), 1.0, delta=0.06)

    def test_no_tube_row_has_no_concentration(self):                           # R2-07
        g, cal = ready_rig(seed=42)
        s = g.sample('a', 1, phosphate_p=0.002)
        g.call(g.A, 'run.start', cups=[g.wash_cup()] + cal + [s, g.wash_cup()], sample_s=40.0, wash_s=20.0)
        g.h.racks.pop(('A', 1))
        while g.read('run.status')['state'] in ('running', 'draining'):
            g.step(60.0)
        row = g.conc(g.call(g.A, 'data.results'), 8, 'ch3')
        self.assertIn('NO_TUBE', row['flags'])
        self.assertIsNone(row['concentration_kg_m3'])


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
