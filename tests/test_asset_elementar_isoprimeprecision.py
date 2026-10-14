"""tests for CHEM-090 elementar_isoprimeprecision (Elementar isoprime precisION IRMS with centrION): python3 -m unittest tests/test_asset_elementar_isoprimeprecision.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation (unknown samples); idempotent request
ids; two instances isolated; step-partition independence (noise included); rejected calls leave the state unchanged; every output validated
against its schema and every success changes only declared slots; pump-down within 12 h (calibrated), power failure with the isolation valve;
magnet mass range (1-76 amu at 3 kV); continuous-flow CO2 against reference pulses (17O correction, internal precision <= 0.06 permil),
normalisation with reference materials recovering an unknown; dual inlet internal precision (<= 0.010 / 0.016 permil 2 sigma at 100 bar uL);
H2 with the H3+ factor (<= 8 ppm/nA); N2; ratio linearity; non-selected inlet vented; resistor switching and saturation; faults (helium, filament,
vacuum leak, magnet drift, empty reference gas); ledgers (sample, reference and dual-inlet moles; helium) close exactly."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import elementar_isoprimeprecision as rt  # noqa: E402

INST = 'elementar_isoprimeprecision_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
CO2_REF = {'13C': -0.0367, '18O': 0.0251}


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.ElementarIsoprimeprecisionRuntime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
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
        assert expect in [e['code'] for e in rt.export_elementar_isoprimeprecision_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def ready(self, species='CO2', ref=1, deltas=None):
        self.call(self.A, 'power.set', on=True)
        self.step(600.0)
        self.call(self.A, 'source.set', filament=True)
        self.call(self.A, 'magnet.set_species', species=species)
        self.call(self.A, 'centrion.set', inlet=1, carrier_on=True)
        self.call(self.A, 'reference.assign', gas=ref, deltas=deltas or CO2_REF)

    def peaks(self, since=0):
        return self.call(self.A, 'results.read', since_peak=since)['peaks']

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']


def _raised_codes():
    src = Path(rt.__file__).read_text()
    funcs = {}
    for m in re.finditer(r'\n    def (\w+)\(self[^\n]*\n(.*?)(?=\n    def |\n# ----|\nclass |\ndef )', src, re.S):
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
        out[k] = codes
    name = {'_cmd_' + c.replace('.', '_'): c for c in rt.CAPS}
    return {name[k]: v for k, v in out.items() if k in name}


def _balance(test, L):
    for sp, inj in L['injected_mol'].items():
        tot = L['vented_mol'].get(sp, 0.0) + L['source_mol'].get(sp, 0.0) + L['ionised_mol'].get(sp, 0.0) + L['in_flight_mol'].get(sp, 0.0)
        test.assertAlmostEqual(tot, inj, delta=1e-9 * inj, msg=sp)
    for sp, inj in L['ref_injected_mol'].items():
        tot = L['ref_vented_mol'].get(sp, 0.0) + L['ref_source_mol'].get(sp, 0.0) + L['ref_ionised_mol'].get(sp, 0.0)
        test.assertAlmostEqual(tot, inj, delta=1e-12 + 1e-9 * inj, msg='ref ' + sp)
    test.assertAlmostEqual(L['di_consumed_mol'], L['di_source_mol'] + L['di_ionised_mol'], delta=1e-15 + 1e-9 * L['di_consumed_mol'])
    test.assertAlmostEqual(L['di_loaded_mol'], L['di_consumed_mol'] + L['di_left_mol'] + L['di_evacuated_mol'], delta=1e-15 + 1e-9 * L['di_loaded_mol'])


def _cf_run(g, samples, pulses=3, pulse_a=None, duration=None):
    pul = [dict(gas=1, start_s=20.0 + 40.0 * k, duration_s=20.0, **({'intensity_a': pulse_a[k]} if pulse_a else {})) for k in range(pulses)]
    dur = duration or (40.0 * pulses + 140.0 * len(samples) + 120.0)
    g.call(g.A, 'acquisition.start', duration_s=dur, reference_pulses=pul)
    for k, (kw, amount) in enumerate(samples):
        g.call(g.A, 'sample.inject', inlet=1, species=g.h.magnet['species'], amount_mol=amount, delay_s=40.0 * pulses + 10.0 + 140.0 * k, **kw)
    g.step(dur + 1.0)


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_consistent(self):
        meta = rt.export_elementar_isoprimeprecision_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
            for p in c['preconditions']:
                for s in p['state_ids']:
                    self.assertIn(s, slots, k)
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['irms_process']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_elementar_isoprimeprecision_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            g.call(g.A, k, expect='UNSUPPORTED')

    def test_agent_truth_isolation(self):
        g = Rig()
        g.ready()
        g.call(g.E, 'sample.declare', sample_id='leaf_a', deltas={'13C': -0.0281})
        _cf_run(g, [(dict(sample_id='leaf_a'), 4e-6)], pulses=1)
        self.assertFalse(any(k.startswith('truth.') for k in g.read(who=g.A)))
        p = [q for q in g.peaks() if q['type'] == 'sample'][0]
        self.assertNotIn('truth', p)
        self.assertNotIn('label', p)                                                                   # unknown sample: the label is not leaked
        for cap, a in (('truth.inspect', {}), ('ledger.read', {}), ('sim.step', dict(dt_s=1.0)), ('sample.declare', dict(sample_id='x', deltas={})),
                       ('fault.inject', dict(fault='vacuum_leak', active=True))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=a, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='magnet_drift', active=True)
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in g.call(g.A, 'events.read', since_s=0.0)['events']))
        g.call(g.E, 'truth.inspect')

    def test_idempotent_request_ids(self):
        g = Rig()
        a = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=True), request_id='same')
        b = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=True), request_id='same')
        self.assertEqual(a, b)
        c = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=False), request_id='same')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='i1'), Rig(instance='i2')
        a.call(a.A, 'power.set', on=True)
        self.assertFalse(b.read('power.on'))

    def test_step_partition_independence(self):
        runs = []
        for parts in ([400.0], [0.3, 7.7, 92.0, 300.0], [1.0] * 40 + [360.0]):
            g = Rig(seed=7)
            g.ready()
            g.call(g.A, 'acquisition.start', duration_s=380.0, reference_pulses=[dict(gas=1, start_s=20.0, duration_s=20.0)])
            g.call(g.A, 'sample.inject', inlet=1, species='CO2', amount_mol=3e-6, substance_id='usgs40', delay_s=60.0)
            for p in parts:
                g.step(p)
            v = g.read()
            v.pop('clock.time_s')
            runs.append(json.dumps([v, g.peaks()], sort_keys=True, default=str))
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0], runs[2])

    def test_rejections_change_nothing(self):
        r = rt.ElementarIsoprimeprecisionRuntime('fresh')
        r.register_principal('a', 'agent')
        self.assertEqual(r.session('a').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g = Rig(vented=True)
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=2))
        g.call(g.E, 'plant.configure', expect='CONFIG_FIXED', **dict(rt.PLANT_EXAMPLE, inlets=['gc', None, None, None, None]))
        g.call(g.E, 'sample.declare', expect='NAME_TAKEN', sample_id='usgs40', deltas={'13C': 0.0})
        g.call(g.A, 'source.set', expect='POWER_OFF', filament=True)
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'source.set', expect='VACUUM_NOT_READY', filament=True)                         # vented
        g.call(g.A, 'magnet.peak_center', expect='NO_SPECIES')
        g.call(g.A, 'magnet.set_species', expect='OUT_OF_RANGE', species='SO2', ) if g.h.source['acceleration_v'] > 3500 else None
        g.call(g.A, 'source.set', acceleration_v=5000.0)
        g.call(g.A, 'magnet.set_species', expect='OUT_OF_RANGE', species='SO2')                     # 66 u > 45.6 u at 5 kV
        g.call(g.A, 'source.set', acceleration_v=3000.0)
        g.call(g.A, 'centrion.set', expect='INLET_EMPTY', inlet=3)
        g.call(g.A, 'sample.inject', expect='NO_CARRIER', inlet=1, species='CO2', amount_mol=1e-6, substance_id='usgs40')
        g.call(g.A, 'centrion.set', inlet=1, carrier_on=True)
        g.call(g.A, 'sample.inject', expect='WRONG_INLET', inlet=5, species='CO2', amount_mol=1e-6, substance_id='usgs40')
        g.call(g.A, 'sample.inject', expect='UNKNOWN_SAMPLE', inlet=1, species='CO2', amount_mol=1e-6, sample_id='nothing')
        g.call(g.A, 'sample.inject', expect='NO_DELTA_FOR_SPECIES', inlet=1, species='N2', amount_mol=1e-6, substance_id='usgs24')
        g.call(g.A, 'sample.inject', expect='INVALID_ARGUMENT', inlet=1, species='CO2', amount_mol=1e-6)
        g.call(g.A, 'acquisition.start', expect='NO_SPECIES', duration_s=60.0)
        g.call(g.A, 'magnet.set_species', species='CO2')
        g.call(g.A, 'acquisition.start', expect='SOURCE_OFF', duration_s=60.0)
        g.call(g.A, 'reference.assign', expect='NO_REFERENCE_GAS', gas=4, deltas={'13C': 0.0})
        g.call(g.A, 'reference.assign', expect='INVALID_ARGUMENT', gas=1, deltas={'15N': 0.0})
        g.call(g.A, 'dual_inlet.start', expect='SOURCE_OFF', species='CO2', substance_id='nbs19', sample_bar_ul=100.0, reference_gas=1)
        g.call(g.A, 'h3.determine', expect='NEED_H2_PULSES', run=1)
        g.call(g.A, 'calibration.normalize', expect='UNKNOWN_PEAK', isotope='13C', standards=[dict(peak=1, substance_id='usgs40'), dict(peak=2, substance_id='usgs41')])
        g.call(g.A, 'amplifier.configure', expect='INVALID_ARGUMENT', mode='fixed')
        g.call(g.E, 'fault.inject', expect='GAS_REQUIRED', fault='reference_gas_empty', active=True)
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)


class Physics(unittest.TestCase):
    def test_pump_down_within_12_h_and_status(self):
        g = Rig(vented=True)
        self.assertEqual(g.read('vacuum.state'), 'vented')
        g.call(g.A, 'power.set', on=True)
        self.assertEqual(g.read('status.led'), 'amber')
        self.assertEqual([g.read('indicator.led_' + k) for k in ('green', 'amber', 'red')], [k == 'amber' for k in ('green', 'amber', 'red')])
        g.call(g.A, 'vacuum.pump_down')
        g.step(11.0 * 3600.0)
        self.assertEqual(g.read('vacuum.state'), 'pumping')
        g.step(1.05 * 3600.0)
        self.assertEqual(g.read('vacuum.state'), 'ready')                                             # brochure: within 12 h without bakeout
        self.assertLessEqual(g.read('truth.process')['pressure_pa'], rt.ENG['vacuum_ready_pa'])
        g.call(g.A, 'source.set', filament=True)
        self.assertEqual(g.read('status.led'), 'green')
        self.assertEqual([g.read('indicator.led_' + k) for k in ('green', 'amber', 'red')], [k == 'green' for k in ('green', 'amber', 'red')])
        g.call(g.A, 'vacuum.vent', expect='FILAMENT_ON')
        g.call(g.A, 'source.set', filament=False)
        g.call(g.A, 'vacuum.vent')
        self.assertEqual(g.read('vacuum.state'), 'vented')
        self.assertIsNone(g.read('vacuum.pressure_pa'))

    def test_power_failure_isolation_valve(self):
        g = Rig()
        g.ready()
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        self.assertEqual(g.read('vacuum.state'), 'isolated')
        self.assertFalse(g.read('source.state')['filament'])
        self.assertEqual(g.read('status.led'), 'off')
        self.assertEqual([g.read('indicator.led_' + k) for k in ('green', 'amber', 'red')], [k == 'off' for k in ('green', 'amber', 'red')])
        g.step(600.0)
        p_sealed = g.read('truth.process')['pressure_pa']
        self.assertGreater(p_sealed, 0.05)                                                            # sealed analyser: pressure rises
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertEqual(g.read('vacuum.state'), 'isolated')
        g.step(rt.ENG['turbo_spinup_s'] + 1.0)
        self.assertEqual(g.read('vacuum.state'), 'ready')                                             # turbo at speed: valve reopened
        self.assertIn('ISOLATION_OPEN', [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']])
        g.call(g.A, 'source.set', filament=True)

    def test_magnet_mass_range(self):
        self.assertLess(rt.ElementarIsoprimeprecisionRuntime()._max_mass(3000.0) - 76.0, 1e-9)
        g = Rig()
        g.ready()
        self.assertAlmostEqual(g.read('magnet.state')['field_t'], rt.b_for(45.0, 3000.0) * (1 + 4e-4), places=12)
        g.call(g.A, 'source.set', acceleration_v=4000.0)                                               # field follows the voltage
        self.assertAlmostEqual(g.read('magnet.state')['field_t'], rt.b_for(45.0, 4000.0), places=12)
        g.call(g.A, 'source.set', expect='OUT_OF_RANGE', acceleration_v=5000.0)                     # 46 u > 45.6 u
        self.assertEqual(g.read('magnet.state')['masses'], [44, 45, 46])

    def test_cf_co2_deltas_precision_and_normalisation(self):
        g = Rig()
        g.ready()
        g.call(g.E, 'sample.declare', sample_id='leaf_a', deltas={'13C': -0.0281})
        _cf_run(g, [(dict(substance_id='usgs40'), 4e-6), (dict(substance_id='usgs41'), 4e-6), (dict(sample_id='leaf_a'), 4e-6)], pulses=10)
        peaks = g.peaks()
        refs = [p for p in peaks if p['type'] == 'reference']
        smp = [p for p in peaks if p['type'] == 'sample']
        self.assertEqual((len(refs), len(smp)), (10, 3))
        # internal precision of the monitoring gas: SD of the reference pulses' 45/44 against their mean
        r45 = [p['ratios']['45/44'] for p in refs]
        m = sum(r45) / len(r45)
        sd = math.sqrt(sum((x / m - 1) ** 2 for x in r45) / (len(r45) - 1)) * 1000.0
        self.assertLess(sd, rt.DOC['cf_precision_permil']['co2_13c'])                                # brochure: <= 0.06 permil 1 sigma
        self.assertAlmostEqual(smp[0]['delta_ref']['13C'], rt.RM['usgs40']['values']['13C']['delta'], delta=2e-4)
        self.assertAlmostEqual(smp[0]['delta_ref']['18O'], rt.PLANT_EXAMPLE['combustion_o2_delta18o'], delta=3e-4)
        n = g.call(g.A, 'calibration.normalize', isotope='13C', standards=[dict(peak=smp[0]['peak'], substance_id='usgs40'),
                                                                          dict(peak=smp[1]['peak'], substance_id='usgs41')])
        self.assertAlmostEqual(n['slope'], 1.0, delta=0.01)
        leaf = [p for p in g.peaks() if p['type'] == 'sample'][2]
        self.assertAlmostEqual(leaf['delta_normalized']['13C'], -0.0281, delta=1.5e-4)
        self.assertEqual(g.read('results.count'), 13)
        _balance(self, g.ledger())

    def test_dual_inlet_precision(self):
        g = Rig()
        g.ready()
        r = g.call(g.A, 'dual_inlet.start', species='CO2', substance_id='nbs19', sample_bar_ul=100.0, reference_gas=1, cycles=10)
        self.assertEqual(g.read('acquisition.state')['mode'], 'dual_inlet')
        g.step(r['ends_s'] - g.h.t + 1.0)
        p = g.peaks()[-1]
        self.assertEqual(p['type'], 'dual_inlet')
        self.assertEqual(p['cycles'], 10)
        lim = rt.DOC['di_precision_permil_2s']
        self.assertLess(2000.0 * p['internal_se']['13C'], lim['co2_13c'])                            # brochure: <= 0.010 permil 2 sigma
        self.assertLess(2000.0 * p['internal_se']['18O'], lim['co2_18o'])
        self.assertAlmostEqual(p['delta_ref']['13C'], rt.RM['nbs19']['values']['13C']['delta'], delta=3e-5)
        self.assertAlmostEqual(p['delta_ref']['18O'], rt.RM['nbs19']['values']['18O']['delta'], delta=4e-5)
        _balance(self, g.ledger())
        g.step(0.05)                                                                                   # start between two 0.1 s grid points
        r = g.call(g.A, 'dual_inlet.start', species='CO2', substance_id='nbs19', sample_bar_ul=100.0, reference_gas=1, cycles=4)
        self.assertAlmostEqual(r['ends_s'] - (g.h.t + 0.05), 9 * (20.0 + rt.ENG['di_switch_s']), places=9)    # first block at the next grid point
        g.step(r['ends_s'] - g.h.t + 1.0)
        self.assertEqual(g.peaks()[-1]['cycles'], 4)
        _balance(self, g.ledger())

    def test_h2_h3_factor_and_normalisation(self):
        g = Rig(reference_gases=[dict(species='H2', deltas={'2H': -0.150}), None, None, None, None, None])
        g.ready('H2', deltas={'2H': -0.150})
        _cf_run(g, [], pulses=5, pulse_a=[2e-9, 4e-9, 6e-9, 8e-9, 10e-9], duration=230.0)
        k = g.call(g.A, 'h3.determine', run=1)
        self.assertLess(k['factor_ppm_na'], rt.DOC['h3_factor_max_ppm_na'])                         # brochure: <= 8.0 ppm/nA
        self.assertAlmostEqual(k['factor_ppm_na'], rt.ENG['h3_factor_ppm_na'], delta=0.3)
        _cf_run(g, [(dict(substance_id='vsmow2'), 4e-6), (dict(substance_id='slap2'), 4e-6), (dict(substance_id='gisp2'), 4e-6)], pulses=1)
        smp = [p for p in g.peaks() if p['type'] == 'sample'][-3:]
        g.call(g.A, 'calibration.normalize', isotope='2H', standards=[dict(peak=smp[0]['peak'], substance_id='vsmow2'), dict(peak=smp[1]['peak'], substance_id='slap2')])
        gisp2 = [p for p in g.peaks() if p['type'] == 'sample'][-1]
        self.assertAlmostEqual(gisp2['delta_normalized']['2H'], rt.RM['gisp2']['values']['2H']['delta'], delta=1.5e-3)
        self.assertGreater(g.read('calibration.state')['h3_factor_ppm_na'], 0.0)

    def test_n2_and_linearity(self):
        g = Rig()
        g.ready('N2', ref=2, deltas={'15N': -0.0012})
        amps = [2e-9, 4e-9, 6e-9, 8e-9, 10e-9, 12e-9]
        g.call(g.A, 'acquisition.start', duration_s=420.0, reference_pulses=[dict(gas=2, start_s=10.0 + 35.0 * k, duration_s=20.0, intensity_a=a)
                                                                             for k, a in enumerate(amps)])
        g.call(g.A, 'sample.inject', inlet=1, species='N2', amount_mol=4e-6, substance_id='usgs40', delay_s=230.0)
        g.step(421.0)
        refs = [p for p in g.peaks() if p['type'] == 'reference']
        xs = [a * 1e9 for a in amps]
        ys = [(p['ratios']['29/28'] / refs[0]['ratios']['29/28'] - 1) * 1000.0 for p in refs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)   # permil per nA (least squares)
        self.assertLess(abs(slope), rt.DOC['cf_linearity_permil_na']['n2_15n'])                       # brochure: <= 0.02 permil/nA
        smp = [p for p in g.peaks() if p['type'] == 'sample'][0]
        self.assertAlmostEqual(smp['delta_ref']['15N'], rt.RM['usgs40']['values']['15N']['delta'], delta=3e-4)

    def test_unselected_inlet_vents_and_saturation(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'acquisition.start', duration_s=300.0)
        g.call(g.A, 'sample.inject', inlet=2, species='CO2', amount_mol=4e-6, substance_id='usgs40', delay_s=20.0)   # inlet 1 selected
        g.step(301.0)
        self.assertEqual(g.peaks(), [])
        L = g.ledger()
        self.assertAlmostEqual(L['vented_mol']['CO2'], 4e-6, delta=1e-15)
        g.call(g.A, 'acquisition.start', duration_s=300.0)
        g.call(g.A, 'sample.inject', inlet=1, species='CO2', amount_mol=4e-4, substance_id='usgs40', delay_s=20.0)  # 100 times larger: auto resistor step-down
        g.step(301.0)
        p = g.peaks()[-1]
        self.assertFalse(p['saturated'])
        self.assertLess(g.read('amplifier.state')['resistors_ohm']['44'], rt.ENG['default_resistors_ohm']['CO2'][0])
        g.call(g.A, 'amplifier.configure', mode='fixed', resistors_ohm=[1e11, 1e11, 1e11])
        g.call(g.A, 'acquisition.start', duration_s=300.0)
        g.call(g.A, 'sample.inject', inlet=1, species='CO2', amount_mol=4e-6, substance_id='usgs40', delay_s=20.0)
        g.step(301.0)
        self.assertTrue(g.peaks()[-1]['saturated'])
        _balance(self, g.ledger())

    def test_faults(self):
        g = Rig()
        g.ready()
        g.call(g.E, 'fault.inject', fault='vacuum_leak', active=True)
        g.step(1.0)
        self.assertFalse(g.read('source.state')['filament'])                                          # vacuum trip
        self.assertIn('VACUUM_TRIP', [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']])
        g.call(g.E, 'fault.inject', fault='vacuum_leak', active=False)
        g.step(60.0)
        g.call(g.A, 'source.set', filament=True)
        g.call(g.E, 'fault.inject', fault='magnet_drift', active=True)
        g.step(5 * 86400.0)
        off = g.read('truth.process')['field_offset_t']
        self.assertGreater(abs(off), 1e-4)
        c = g.call(g.A, 'magnet.peak_center')
        self.assertAlmostEqual(c['offset_t'], off, delta=1e-9)
        g.call(g.E, 'fault.inject', fault='magnet_drift', active=False)
        g.call(g.E, 'fault.inject', fault='reference_gas_empty', active=True, gas=1)
        _cf_run(g, [], pulses=1, duration=60.0)
        self.assertEqual([p for p in g.peaks() if p['type'] == 'reference'], [])
        g.call(g.E, 'fault.inject', fault='reference_gas_empty', active=False, gas=1)
        g.call(g.E, 'fault.inject', fault='he_supply_empty', active=True)
        g.step(1.0)
        self.assertEqual(g.read('status.led'), 'red')
        self.assertEqual([g.read('indicator.led_' + k) for k in ('green', 'amber', 'red')], [k == 'red' for k in ('green', 'amber', 'red')])
        self.assertFalse(g.read('centrion.state')['carrier_on'])
        g.call(g.A, 'centrion.set', expect='NO_HELIUM', carrier_on=True)
        g.call(g.E, 'fault.inject', fault='he_supply_empty', active=False)
        g.call(g.E, 'fault.inject', fault='filament_burnout', active=True)
        self.assertFalse(g.read('source.state')['filament'])
        g.call(g.E, 'fault.inject', fault='filament_burnout', active=False)
        g.call(g.A, 'source.set', expect='FILAMENT_FAILED', filament=True)
        g.call(g.A, 'hood.set', angle_rad=1.9)
        self.assertEqual(g.read('hood.angle_rad'), 1.9)
        g.call(g.A, 'acquisition.stop')
        _balance(self, g.ledger())

    def test_quiet_stepping_helium_and_power_toggle(self):
        g = Rig()
        g.ready()
        n = g.h.tick
        he0 = g.ledger()['he_used_m3']
        g.step(30 * 86400.0)                                                                            # nothing measured: closed-form jump
        self.assertEqual(g.h.tick - n, int(round(30 * 86400.0 / rt.DT)))
        self.assertAlmostEqual(g.ledger()['he_used_m3'] - he0, rt.ENG['carrier_he_m3_s'] * 30 * 86400.0, delta=1e-9)
        self.assertGreater(g.read('source.state')['filament_hours'], 700.0)
        g.call(g.A, 'centrion.set', sleep=True)
        g.call(g.A, 'power.toggle')
        self.assertFalse(g.read('power.on'))


class ReviewFixes(unittest.TestCase):
    """Regressions of the round-1 review (H3+ repeat, quiet-interval events, filament replacement, dual-inlet stop, auto ranging)."""

    def test_h3_repeat_is_idempotent(self):
        g = Rig(reference_gases=[dict(species='H2', deltas={'2H': -0.150}), None, None, None, None, None])
        g.ready('H2', deltas={'2H': -0.150})
        _cf_run(g, [], pulses=5, pulse_a=[2e-9, 4e-9, 6e-9, 8e-9, 10e-9], duration=230.0)
        k1 = g.call(g.A, 'h3.determine', run=1)['factor_ppm_na']
        k2 = g.call(g.A, 'h3.determine', run=1)['factor_ppm_na']
        self.assertAlmostEqual(k1, k2, places=12)                                                    # a repeat on the same run does not add up
        _cf_run(g, [], pulses=5, pulse_a=[2e-9, 4e-9, 6e-9, 8e-9, 10e-9], duration=230.0)           # a later run processed with the factor
        k3 = g.call(g.A, 'h3.determine', run=2)['factor_ppm_na']
        self.assertAlmostEqual(k3, k1, delta=0.3)
        self.assertLess(k3, rt.DOC['h3_factor_max_ppm_na'])

    def test_quiet_events_do_not_depend_on_the_partition(self):
        out = []
        for parts in ([100.0], [0.1] * 1000, [0.05] * 2000, [7.3] * 13 + [5.1]):
            g = Rig()
            g.ready()
            g.call(g.E, 'fault.inject', fault='vacuum_leak', active=True)
            for dt in parts:
                g.step(dt)
            ev = [e for e in g.call(g.A, 'events.read', since_s=0.0)['events'] if e['code'] == 'VACUUM_TRIP']
            out.append((round(g.read('source.state')['filament_hours'], 12), ev[0]['time_s']))
        self.assertTrue(all(o == out[0] for o in out), out)
        life = []
        for parts in ([30 * 86400.0] * 6, [86400.0] * 180):
            g = Rig()
            g.ready()
            for dt in parts:
                g.step(dt)
            ev = [e for e in g.call(g.A, 'events.read', since_s=0.0)['events'] if e['code'] == 'FILAMENT_BURNT']
            life.append((ev[0]['time_s'], g.ledger()['he_used_m3']))
        self.assertAlmostEqual(life[0][0], life[1][0], places=6)
        self.assertAlmostEqual(life[0][1], life[1][1], delta=1e-15)

    def test_filament_replacement_and_dual_inlet_stop(self):
        g = Rig()
        g.ready()
        g.call(g.E, 'fault.inject', fault='filament_burnout', active=True)
        g.call(g.A, 'source.replace_filament', expect='NOT_VENTED')
        g.call(g.A, 'vacuum.vent')
        g.call(g.A, 'source.replace_filament', expect='HOOD_CLOSED')
        g.call(g.A, 'hood.set', angle_rad=1.6)
        g.call(g.A, 'source.replace_filament', expect='FAULT_ACTIVE')
        g.call(g.E, 'fault.inject', fault='filament_burnout', active=False)
        r = g.call(g.A, 'source.replace_filament')
        self.assertEqual(r['source']['filament_hours'], 0.0)
        g.call(g.A, 'hood.set', angle_rad=0.0)
        g.call(g.A, 'vacuum.pump_down')
        g.step(12 * 3600.0)
        g.call(g.A, 'source.set', filament=True)
        g.call(g.A, 'dual_inlet.start', species='CO2', substance_id='nbs19', sample_bar_ul=100.0, reference_gas=1, cycles=10)
        g.step(250.0)
        r = g.call(g.A, 'acquisition.stop')
        self.assertIsNotNone(r['run'])
        self.assertEqual(g.read('acquisition.state'), {})
        p = g.peaks()[-1]
        self.assertEqual(p['type'], 'dual_inlet')
        self.assertLess(p['cycles'], 10)
        _balance(self, g.ledger())

    def test_drifted_beams_give_no_delta_and_no_crash(self):
        g = Rig()
        g.ready()
        g.call(g.E, 'fault.inject', fault='magnet_drift', active=True)
        g.step(4 * 86400.0)
        r = g.call(g.A, 'dual_inlet.start', species='CO2', substance_id='nbs19', sample_bar_ul=100.0, reference_gas=1, cycles=4)
        g.step(r['ends_s'] - g.h.t + 1.0)
        p = g.peaks()[-1]
        json.dumps(p)                                                                                    # real numbers only
        self.assertTrue(p['delta_ref'] is None or all(isinstance(v, float) for v in p['delta_ref'].values()))
        _balance(self, g.ledger())

    def test_off_grid_run_end_and_helium_alarm_time(self):
        res = []
        for parts in ([150.0], None, [0.07] * 2200):
            g = Rig()
            g.ready()
            r = g.call(g.A, 'acquisition.start', duration_s=100.05, reference_pulses=[dict(gas=1, start_s=20.0, duration_s=20.0)])
            self.assertAlmostEqual(r['ends_s'] * 10.0, round(r['ends_s'] * 10.0), places=6)                # end on the 0.1 s grid
            for dt in (parts or [r['ends_s'] - g.h.t, 50.0]):
                g.step(dt)
            p = g.peaks()[0]
            res.append((p['area_vs'], [e['time_s'] for e in g.call(g.A, 'events.read', since_s=0.0)['events'] if e['code'] == 'RUN_END']))
        self.assertTrue(all(x == res[0] for x in res), res)
        times = []
        for parts in ([40000.0], [7.3] * 5480):
            g = Rig(he_supply_gauge_pa=5.0e4)
            g.ready()
            for dt in parts:
                g.step(dt)
            times.append([e['time_s'] for e in g.call(g.A, 'events.read', since_s=0.0)['events'] if e['code'] == 'HELIUM_EMPTY'])
        t_exp = 600.0 + (rt.ENG['he_supply_m3'] * 5.0e4 / 101325.0 - 0.0) / rt.ENG['carrier_he_m3_s']
        self.assertEqual(times[0], times[1])
        self.assertTrue(len(times[0]) == 1 and abs(times[0][0] - t_exp) < 1e-3, (times, t_exp))

    def test_auto_ranging_restarts_at_each_acquisition(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'acquisition.start', duration_s=300.0)
        g.call(g.A, 'sample.inject', inlet=1, species='CO2', amount_mol=4e-4, substance_id='usgs40', delay_s=20.0)
        g.step(301.0)
        self.assertLess(g.read('amplifier.state')['resistors_ohm']['44'], rt.ENG['default_resistors_ohm']['CO2'][0])
        g.call(g.A, 'acquisition.start', duration_s=60.0)
        self.assertEqual(g.read('amplifier.state')['resistors_ohm'], {})


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        missing = set(rt.CAPS) - set(rt.UNSUPPORTED) - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
