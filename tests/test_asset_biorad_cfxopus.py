"""tests for CHEM-167 biorad_cfxopus: python3 -m unittest tests/test_asset_biorad_cfxopus.py

Topics (pipeline.md): commands only write targets, read/describe are pure, rejected preconditions leave the state unchanged, agent/evaluator isolation,
plate / run ownership, request idempotency, asynchronous run with cancel / poll, host-step independence (fixed 0.1 s grid), declared immediate and
on_step effects over scenario states, conservation ledgers, real return values validated against the declared schemas (asset_tool validate_value);
plus the instrument physics against the manufacturer data: block ramp (max 5 / average 3.3 C/s), accuracy (+-0.2 C at 90 C) and uniformity
(+-0.3 C), gradient across the rows, heated lid before the first step, calculated vs block control mode, protocol rules (Table 10), the brochure
probe example (IL-1b 1e5 copies, 10 ul, FAM, Cq 19.81 +- 0.10), SYBR standard curve and NTC, 5-plex unmixing, melt peaks at the amplicon Tm,
inhibition, denaturation / annealing / extension / hot-start limits, evaporation without seal, primer dimers, scan modes and faults.
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
from asset_runtime import biorad_cfxopus as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
INST = 'cfx_opus_01'


def _meta():
    if not _META:
        _META.update(rt.export_biorad_cfxopus_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.BioradCfxOpusRuntime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema and immediate state effects; every rejection code is recorded."""
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


rt.BioradCfxOpusRuntime.invoke = _instrumented_invoke


def check(cid, **detail):
    CHECKS.append(dict(check=cid, status='passed', **detail))


def powered(seed=1, **plant):
    h = rt.BioradCfxOpusRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    h.invoke('power.set', enabled=True)
    return h


def open_lid(h):
    h.invoke('lid.open')
    h.step(rt.LID_TIME_S + 0.2)


def close_lid(h):
    h.invoke('lid.close')
    h.step(rt.LID_TIME_S + 0.2)


def W(well, assays, chem='sybr', sample=None, dil=1.0, tv=2e-9, vol=2e-8, stype='unknown', rep=None, q=None, group=None):
    d = dict(well=well, sample_type=stype, reaction_volume_m3=vol, assays=list(assays), chemistry=chem)
    if sample is not None:
        d.update(sample_id=sample, dilution=dil, template_volume_m3=tv)
    if rep is not None:
        d['reporters'] = list(rep)
    if q is not None:
        d['quantity_copies'] = q
    if group is not None:
        d['biological_group'] = group
    return d


def protocol(h, anneal=333.15, cycles=40, melt=True, vol=2e-8, denat=368.15, activation=180.0, ext_s=30.0, read=True, extra=None):
    steps = [] if not activation else [dict(kind='temperature', temperature_k=368.15, hold_s=activation)]
    g = len(steps) + 1
    steps += [dict(kind='temperature', temperature_k=denat, hold_s=10.0), dict(kind='temperature', temperature_k=anneal, hold_s=ext_s, plate_read=read),
              dict(kind='goto', goto_step=g, cycles=cycles - 1)]
    if melt:
        steps.append(dict(kind='melt', start_k=338.15, end_k=368.15, melt_increment_k=0.5, hold_s=5.0))
    if extra:
        steps = extra
    return h.invoke('protocol.set', name='test', sample_volume_m3=vol, lid_temperature_k=378.15, steps=steps)


def run_plate(h, wells, scan='all_channels', plate_type='white_lowprofile_96', seal='optical_film', pid='p1', **pkw):
    open_lid(h)
    h.invoke('plate.prepare', plate_id=pid, plate_type=plate_type, seal=seal, wells=wells)
    h.invoke('plate.load', plate_id=pid)
    close_lid(h)
    protocol(h, **pkw)
    h.invoke('run.start', scan_mode=scan)
    while h.run['state'] in ('lid_heating', 'running'):
        h.step(60.0)
    return h


def cq_of(h, **kw):
    return {(x['well'], x['fluorophore']): x['cq'] for x in h.invoke('analysis.cq', **kw)['results']}


# ---------------------------------------------------------------- contract, libraries
class ContractAndLibraries(unittest.TestCase):
    def test_module_identity_and_export(self):
        m = _meta()
        self.assertEqual(m['asset_type_id'], 'instrument.biorad_cfxopus')
        self.assertEqual(set(m['capabilities']), set(rt.CAPS))
        self.assertEqual(set(m['state_slots']), set(rt.SLOTS))
        for k, c in m['capabilities'].items():
            self.assertTrue(c['errors'], k)
        check('export')

    def test_template_libraries(self):
        fl, asy = rt.fluorophores(), rt.assays()
        self.assertGreaterEqual(len(fl), 50)
        self.assertGreaterEqual(len(asy), 50)
        for v in fl.values():
            self.assertTrue(v['source'].startswith('FPbase'))
        for v in asy.values():
            amp, f, r = v['amplicon_sequence'], v['forward_primer']['sequence'], v['reverse_primer']['sequence']
            self.assertTrue(amp.startswith(f) and amp.endswith(r[::-1].translate(str.maketrans('ACGT', 'TGCA'))))
            self.assertEqual(len(amp), v['amplicon_length_bp'])
            self.assertTrue(345.0 < v['melt_tm_k'] < 375.0)
        ch = rt.optics()['channels']
        self.assertEqual([ch['1']['ex'], ch['1']['em'], ch['5']['ex'], ch['5']['em']], [[450, 490], [515, 530], [672, 684], [705, 730]])
        self.assertEqual(fl['fam']['best_channel'], '1')
        self.assertEqual(fl['hex']['best_channel'], '2')
        self.assertEqual(fl['texas_red']['best_channel'], '3')
        self.assertEqual(fl['cy5']['best_channel'], '4')
        self.assertEqual(fl['quasar_705']['best_channel'], '5')
        check('libraries', fluorophores=len(fl), assays=len(asy))


# ---------------------------------------------------------------- thermal
class Thermal(unittest.TestCase):
    def test_ramp_accuracy_uniformity(self):
        h = powered()
        open_lid(h)
        h.invoke('plate.prepare', plate_id='e', plate_type='white_lowprofile_96', seal='optical_film', wells=[W(w, []) for w in rt.WELLS])
        h.invoke('plate.load', plate_id='e')
        close_lid(h)
        protocol(h, extra=[dict(kind='temperature', temperature_k=333.15, hold_s=20.0), dict(kind='temperature', temperature_k=368.15, hold_s=20.0),
                           dict(kind='temperature', temperature_k=363.15, hold_s=30.0)], vol=0.0)
        h.invoke('run.start', scan_mode='sybr_fam')
        while h.run['state'] == 'lid_heating':
            h.step(0.1)
        while not (h.run['k'] == 1 and h.run['phase'] == 'ramp'):
            h.step(0.1)
        t0, T0 = h.t, float(np.mean(h.T_row))
        rates = []
        while h.run['k'] == 1 and h.run['phase'] == 'ramp':
            a = float(np.mean(h.T_row))
            h.step(0.1)
            rates.append((float(np.mean(h.T_row)) - a) / 0.1)
        avg = (368.15 - T0) / (h.t - t0)
        self.assertLessEqual(max(rates), 5.0 + 1e-9)
        self.assertTrue(3.0 <= avg <= 3.7, avg)                                 # documented average 3.3 C/s
        while not (h.run['k'] == 2 and h.run['phase'] == 'hold'):
            h.step(0.1)
        h.step(10.0)
        wells = h.Ts.copy()
        self.assertLess(abs(float(np.mean(wells)) - 363.15), 0.2)              # accuracy +-0.2 C at 90 C
        self.assertLessEqual(float(wells.max() - wells.min()), 0.6)             # uniformity +-0.3 C (max - min <= 0.6 C)
        check('thermal', average_ramp=avg, spread=float(wells.max() - wells.min()))

    def test_gradient_lid_and_control_modes(self):
        h = powered()
        open_lid(h)
        close_lid(h)
        protocol(h, extra=[dict(kind='gradient', lower_k=328.15, upper_k=341.15, hold_s=60.0)], vol=0.0)
        h.invoke('run.start', scan_mode='sybr_fam')
        self.assertEqual(h.read()['run.state'], 'lid_heating')
        h.step(1.0)
        self.assertEqual(h.read()['run.state'], 'lid_heating')
        while h.run['state'] == 'lid_heating':
            h.step(1.0)
        self.assertGreater(h.lid_T, 378.15 - 1.0)                               # the lid heats before the first step
        h.step(50.0)
        rows = h.T_row.copy()
        self.assertAlmostEqual(float(rows[0]), 341.15, delta=0.15)
        self.assertAlmostEqual(float(rows[-1]), 328.15, delta=0.15)
        self.assertTrue(np.all(np.diff(rows) < 0))
        times = {}
        for vol in (0.0, 5e-8):
            k = powered()
            open_lid(k)
            close_lid(k)
            protocol(k, extra=[dict(kind='temperature', temperature_k=368.15, hold_s=5.0)], vol=vol)
            k.invoke('run.start', scan_mode='sybr_fam')
            while k.run['state'] == 'lid_heating':
                k.step(0.5)
            t0 = k.t
            while k.run['state'] == 'running':
                k.step(0.1)
            times[vol] = k.t - t0
        self.assertGreater(times[5e-8], times[0.0] + 2.0)                       # calculated mode waits for the 50 ul sample
        check('gradient_lid_modes', rows=rows.tolist(), times=times)


# ---------------------------------------------------------------- protocol rules
class ProtocolRules(unittest.TestCase):
    def test_validation_and_increment(self):
        h = powered()
        bad = [([dict(kind='gradient', lower_k=328.15, upper_k=358.15, hold_s=30.0)], 'INVALID_GRADIENT'),
               ([dict(kind='temperature', temperature_k=368.15, hold_s=10.0), dict(kind='goto', goto_step=3, cycles=5)], 'INVALID_GOTO'),
               ([dict(kind='temperature', temperature_k=368.15), dict(kind='temperature', temperature_k=333.15, hold_s=5.0)], 'INVALID_STEP'),
               ([dict(kind='melt', start_k=360.0, end_k=350.0, melt_increment_k=0.5)], 'INVALID_MELT'),
               ([dict(kind='gradient', lower_k=328.15, upper_k=338.15, hold_s=30.0, increment_k=1.0)], 'INVALID_STEP')]
        for steps, code in bad:
            with self.assertRaises(rt.ActionRejected) as e:
                h.invoke('protocol.set', name='x', sample_volume_m3=2e-8, lid_temperature_k=378.15, steps=steps)
            self.assertEqual(str(e.exception), code)
        open_lid(h)
        close_lid(h)
        h.invoke('protocol.set', name='td', sample_volume_m3=0.0, lid_temperature_k=378.15, steps=[
            dict(kind='temperature', temperature_k=368.15, hold_s=5.0), dict(kind='temperature', temperature_k=343.15, hold_s=5.0, increment_k=-1.0, extend_s=2.0,
                                                                              plate_read=True), dict(kind='goto', goto_step=1, cycles=4)])
        h.invoke('run.start', scan_mode='sybr_fam')
        targets, holds = [], []
        while h.run['state'] in ('lid_heating', 'running'):
            prev = (h.run['k'], h.run['phase'])
            h.step(0.1)
            if h.run['state'] == 'running' and prev != (h.run['k'], h.run['phase']) and h.run['k'] == 1 and h.run['phase'] == 'hold':
                targets.append(float(h.run['target'][0]))
                holds.append(h.run['hold_left'])
        self.assertEqual([round(x, 2) for x in targets], [343.15, 342.15, 341.15, 340.15, 339.15])   # touchdown
        self.assertEqual(len(h.run['reads']), 5)
        self.assertEqual(h.read()['run.state'], 'completed')
        check('protocol_rules', targets=targets)


# ---------------------------------------------------------------- chemistry
class Chemistry(unittest.TestCase):
    def test_sybr_standard_curve_ntc_and_melt(self):
        h = powered()
        h.invoke('sample.provide', sample_id='std', kind='standard', copies_per_m3={'hs_gapdh': 5e14}, declared_copies_per_m3={'hs_gapdh': 5e14})
        wells = []
        for k, dil in enumerate([1, 1e-1, 1e-2, 1e-3, 1e-4, 1e-5]):
            for rep in range(3):
                wells.append(W('ABCDEF'[k] + str(rep + 1), ['hs_gapdh'], sample='std', dil=dil, stype='standard', q=5e14 * dil * 2e-9))
        wells += [W(f'G{r + 1}', ['hs_gapdh'], stype='ntc') for r in range(3)]
        run_plate(h, wells, scan='sybr_fam')
        sc = h.invoke('analysis.standard_curve')['curves'][0]
        self.assertTrue(-3.8 < sc['slope'] < -3.1 and sc['r2'] > 0.99 and 0.85 < sc['efficiency'] < 1.05, sc)
        cq = cq_of(h)
        self.assertTrue(12.0 < cq[('A1', 'sybr_green')] < 17.0)
        for r in range(3):
            self.assertTrue(cq[(f'G{r + 1}', 'sybr_green')] is None or cq[(f'G{r + 1}', 'sybr_green')] > 34.0)
        peaks = {p['well']: p['peaks_k'] for p in h.invoke('analysis.melt_peaks')['peaks']}
        tm = rt.assays()['hs_gapdh']['melt_tm_k'] + rt.CHEM['melt_tm_offset_k']           # effective amplicon Tm in the reaction
        self.assertEqual(len(peaks['A1']), 1)
        self.assertAlmostEqual(peaks['A1'][0], tm, delta=1.0)
        ex = h.invoke('data.export', kind='cq_csv')
        self.assertEqual(ex['rows'], 21)
        amp = h.invoke('data.amplification', wells=['A1', 'G1'])['curves']
        self.assertEqual([c['well'] for c in amp], ['A1', 'G1'])
        self.assertEqual(len(amp[0]['rfu']), 40)
        self.assertGreater(amp[0]['baseline_subtracted'][-1], 1000.0)                # plateau of a 1e6-copy well
        mc = h.invoke('data.melt', wells=['A1'])['curves'][0]
        self.assertEqual(len(mc['temperature_k']), 61)
        self.assertGreater(mc['rfu'][0], mc['rfu'][-1])                              # dsDNA melts: SYBR signal falls
        open_lid(h)
        self.assertEqual(h.invoke('plate.remove')['plate_id'], 'p1')
        self.assertFalse(h.read()['plate.loaded'])
        self.assertEqual(h.read()['plates.on_bench'], ['p1'])
        check('sybr_standard_curve', slope=sc['slope'], efficiency=sc['efficiency'], r2=sc['r2'], melt_peak=peaks['A1'][0])

    def test_gene_expression_delta_delta_cq(self):
        h = powered()
        h.invoke('sample.provide', sample_id='ctrl', kind='template', copies_per_m3={'hs_gapdh': 2.5e13, 'hs_il6': 2.5e11})
        h.invoke('sample.provide', sample_id='lps', kind='template', copies_per_m3={'hs_gapdh': 2.5e13, 'hs_il6': 8.0e12})
        wells = [W(f'{row}{k + 1}', [a], sample=s, group=s) for row, s in (('A', 'ctrl'), ('B', 'lps')) for k, a in enumerate(['hs_gapdh', 'hs_gapdh', 'hs_il6', 'hs_il6'])]
        run_plate(h, wells, scan='sybr_fam', melt=False)
        res = h.invoke('analysis.gene_expression', reference_targets=['hs_gapdh'], control_group='ctrl')['results']
        il6 = {x['sample']: x for x in res if x['target'] == 'hs_il6'}
        self.assertAlmostEqual(il6['ctrl']['delta_delta_cq'], 0.0, places=9)
        self.assertTrue(16.0 < il6['lps']['relative_quantity'] < 64.0, il6['lps'])      # 32-fold more IL-6 template
        with self.assertRaises(rt.ActionRejected) as e:
            h.invoke('analysis.gene_expression', reference_targets=['hs_gapdh'], control_group='nope')
        self.assertEqual(str(e.exception), 'UNKNOWN_GROUP')
        check('gene_expression', relative_quantity=il6['lps']['relative_quantity'])

    def test_brochure_probe_example(self):
        h = powered()
        h.invoke('sample.provide', sample_id='il1b', kind='template', copies_per_m3={'hs_il1b': 1e14})
        wells = [W(w, ['hs_il1b'], chem='probe', sample='il1b', tv=1e-9, vol=1e-8, rep=['fam']) for w in rt.WELLS]
        run_plate(h, wells, scan='sybr_fam', melt=False, vol=1e-8)
        v = np.array([c for c in cq_of(h).values() if c is not None])
        self.assertEqual(len(v), 96)
        self.assertAlmostEqual(float(v.mean()), 19.81, delta=0.3)              # Bulletin 7279 p.6: Cq 19.81 +- 0.10
        self.assertLess(float(v.std(ddof=1)), 0.2)
        check('probe_example', mean=float(v.mean()), sd=float(v.std(ddof=1)))

    def test_five_plex_unmixing_crosstalk_and_competition(self):
        dyes = [('hs_actb', 'fam'), ('hs_tbp', 'hex'), ('hs_gapdh', 'texas_red'), ('hs_b2m', 'cy5'), ('hs_hprt1', 'quasar_705')]

        def single(a, d, n):
            h = powered()
            h.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={a: n / 2e-9})
            run_plate(h, [W('A1', [a], chem='probe', sample='s', rep=[d])], melt=False)
            return cq_of(h)[('A1', d)]

        def multi(pairs, copies):
            h = powered()
            h.invoke('sample.provide', sample_id='mix', kind='template', copies_per_m3={a: n / 2e-9 for (a, d), n in zip(pairs, copies) if n > 0})
            run_plate(h, [W('A1', [a for a, d in pairs], chem='probe', sample='mix', rep=[d for a, d in pairs])], melt=False)
            return h, cq_of(h)
        one = {d: single(a, d, 1e5) for a, d in dyes}
        h, five = multi(dyes, [1e5] * 5)
        for a, d in dyes:
            self.assertAlmostEqual(five[('A1', d)], one[d], delta=0.6)          # equal-abundance 5-plex: colour compensation recovers each dye
        # crosstalk: the Texas Red target is absent; Cy5 bleeds into channel 3 (raw rise) but the unmixed Texas Red curve stays flat
        h, three = multi([dyes[0], dyes[2], dyes[3]], [1e5, 0.0, 1e5])
        self.assertIsNone(three[('A1', 'texas_red')])
        raw3 = [rd['raw'][0][2] for rd in h.run['reads'] if rd['kind'] == 'amp']
        self.assertGreater(raw3[-1] - float(np.mean(raw3[2:8])), 100.0)
        # competition: an abundant target (1e6) saturates the shared polymerase before a rare one (1e3) reaches the threshold (primer limiting is the fix)
        h, duo = multi([dyes[0], dyes[3]], [1e6, 1e3])
        rare = single('hs_b2m', 'cy5', 1e3)
        self.assertTrue(duo[('A1', 'cy5')] is None or duo[('A1', 'cy5')] > rare + 0.5)
        check('five_plex', single=one, multi={d: five[('A1', d)] for a, d in dyes}, rare_single=rare, rare_with_abundant=duo[('A1', 'cy5')])

    def test_inhibition_and_cycling_limits(self):
        def cq(**kw):
            h = powered()
            h.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={'hs_hprt1': 5e13}, inhibitor=kw.pop('inh', 0.0))
            run_plate(h, [W('A1', ['hs_hprt1'], sample='s')], scan='sybr_fam', melt=False, **kw)
            return cq_of(h)[('A1', 'sybr_green')], h
        base, _ = cq()
        inh, _ = cq(inh=0.4)
        self.assertGreater(inh, base + 1.0)
        low_den, _ = cq(denat=rt.assays()['hs_hprt1']['melt_tm_k'] + rt.CHEM['melt_tm_offset_k'] - 6.0)
        self.assertIsNone(low_den)                                              # the amplicon is never denatured
        hot_anneal, _ = cq(anneal=rt.assays()['hs_hprt1']['primer_tm_k'] + 10.0)
        self.assertTrue(hot_anneal is None or hot_anneal > base + 5.0)          # the primers do not anneal
        no_act, _ = cq(activation=0)
        self.assertGreater(no_act, base + 0.3)                                  # hot-start enzyme activated only during the first cycles
        check('limits', base=base, inhibited=inh, no_activation=no_act)

    def test_annealing_gradient_direction_and_primer_dimer(self):
        h = powered()
        h.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={'hs_gapdh': 5e14})
        upper = round(rt.assays()['hs_gapdh']['primer_tm_k'] + 2.0 - 273.15) + 273.15     # near the optimum (primer Tm + 2 K)
        steps = [dict(kind='temperature', temperature_k=368.15, hold_s=180.0), dict(kind='temperature', temperature_k=368.15, hold_s=10.0),
                 dict(kind='gradient', lower_k=upper - 10.0, upper_k=upper, hold_s=30.0, plate_read=True), dict(kind='goto', goto_step=2, cycles=39)]
        wells = []
        for row in 'AH':
            for k, dil in enumerate([1, 1e-1, 1e-2, 1e-3]):
                for rep in range(3):
                    wells.append(W(f'{row}{3 * k + rep + 1}', ['hs_gapdh'], sample='s', dil=dil, stype='standard', q=1e6 * dil))
        run_plate(h, wells, scan='sybr_fam', extra=steps)
        cq = cq_of(h)
        eff = {}
        for row in 'AH':
            xs = np.repeat(np.log10([1e6, 1e5, 1e4, 1e3]), 3)
            ys = np.array([cq[(f'{row}{c + 1}', 'sybr_green')] for c in range(12)])
            s = np.polyfit(xs, ys, 1)[0]
            eff[row] = 10 ** (-1 / s) - 1
        self.assertGreater(eff['A'], eff['H'] + 0.02)                           # brochure GAPDH gradient: E 98.7 % at 62 C vs 93.6 % at 56 C
        k = powered()
        run_plate(k, [W(f'A{c + 1}', ['hs_gapdh'], stype='ntc') for c in range(3)], scan='sybr_fam', cycles=50)
        pk = [p for p in k.invoke('analysis.melt_peaks')['peaks'] if p['peaks_k']]
        self.assertTrue(pk)                                                     # late primer-dimer product in the NTC after 50 cycles
        self.assertTrue(all(abs(x - rt.CHEM['pd_tm_k']) < 3.0 for p in pk for x in p['peaks_k']))
        check('gradient_dimer', efficiency=eff, ntc_peaks=len(pk))

    def test_evaporation_scan_modes_and_faults(self):
        h = powered()
        h.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={'hs_gapdh': 5e13})
        run_plate(h, [W('A1', ['hs_gapdh'], sample='s')], seal='none', scan='sybr_fam', melt=False)
        v = h.invoke('well.inspect', well='A1')['truth']['volume_m3']
        self.assertLess(v, 2e-8 * 0.995)                                        # unsealed: water lost while hot
        self.assertAlmostEqual(h.ledger()['water_evaporated_m3'], 2e-8 - v, delta=1e-15)
        raw = h.run['reads'][0]['raw'][0]
        self.assertIsNotNone(raw[0])
        self.assertTrue(all(x is None for x in raw[1:]))                        # fast scan: channel 1 only
        k = powered()
        k.invoke('fault.inject', fault='led_failure_channel_1', active=True)
        run_plate(k, [W('A1', ['hs_gapdh'])], scan='sybr_fam', cycles=3, melt=False)
        self.assertIn('LED_FAILURE_CHANNEL_1', k.read()['errors.active'])
        self.assertTrue(k.read()['lamp.status_red'])
        self.assertFalse(k.read()['lamp.status_blue'] or k.read()['lamp.status_green'])   # one colour at a time: the error wins
        k.invoke('fault.inject', fault='led_failure_channel_1', active=False)
        self.assertFalse(k.read()['lamp.status_red'])
        self.assertTrue(k.read()['lamp.status_blue'])                              # completed, lid not opened yet
        m = powered()
        open_lid(m)
        m.invoke('fault.inject', fault='lid_obstructed', active=True)
        m.invoke('lid.close')
        m.step(10.0)
        self.assertEqual(m.read()['lid.state'], 'stopped')
        self.assertIn('LID_OBSTRUCTED', m.read()['errors.active'])
        p = powered()
        open_lid(p)
        close_lid(p)
        protocol(p, cycles=5, melt=False)
        p.invoke('run.start', scan_mode='sybr_fam')
        p.step(420.0)
        reads0 = len(p.run['reads'])
        p.invoke('fault.inject', fault='power_loss', active=True)                  # guide p.121: the run resumes when the power returns
        self.assertEqual(p.read()['run.state'], 'power_failed')
        self.assertEqual(p.read()['run.warnings'], ['POWER_FAILURE'])
        self.assertFalse(any(p.read()[k] for k in ('lamp.status_green', 'lamp.status_blue', 'lamp.status_red')))
        p.step(300.0)
        p.invoke('fault.inject', fault='power_loss', active=False)
        self.assertEqual(p.read()['run.state'], 'lid_heating')
        while p.run['state'] in ('lid_heating', 'running'):
            p.step(60.0)
        self.assertEqual(p.run['state'], 'completed')
        self.assertEqual(p.run['cycle'], 5)
        self.assertGreaterEqual(reads0, 1)
        self.assertEqual(len(p.run['reads']), 5)                                   # no read lost or duplicated by the outage
        self.assertEqual(p.invoke('runs.list')['runs'][0]['warnings'], ['POWER_FAILURE'])
        check('evaporation_scan_faults')


# ---------------------------------------------------------------- framework
class Framework(unittest.TestCase):
    def test_read_describe_pure_and_clock(self):
        h = powered()
        h.register_principal('ag', 'agent')
        v0 = h.read('evaluator')
        h.session('ag').describe(instance_id=INST)
        h.session('ag').read(instance_id=INST)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True), json.dumps(v0, sort_keys=True))
        t = h.invoke('sim.step', dt_s=2.25)['time_s']
        self.assertEqual(t, h.t)
        check('pure_reads')

    def test_step_partition_independent(self):
        def go(splits):
            h = powered(seed=4)
            open_lid(h)
            h.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={'hs_gapdh': 5e13})
            h.invoke('plate.prepare', plate_id='p', plate_type='white_lowprofile_96', seal='optical_film', wells=[W('A1', ['hs_gapdh'], sample='s'), W('B2', ['hs_gapdh'])])
            h.invoke('plate.load', plate_id='p')
            close_lid(h)
            protocol(h, cycles=6)
            h.invoke('run.start', scan_mode='all_channels')
            for dt in splits(900.0):
                h.step(dt)
            mid = h.read('evaluator')
            for dt in splits(1500.0):
                h.step(dt)
            return h, mid
        a, am = go(lambda T: [T])
        b, bm = go(lambda T: [0.375] * int(T / 0.375))                       # 0.375 s host steps (exact in binary, off the 0.1 s grid)
        self.assertEqual(json.dumps(am, sort_keys=True), json.dumps(bm, sort_keys=True))
        self.assertEqual(json.dumps(a.read('evaluator'), sort_keys=True), json.dumps(b.read('evaluator'), sort_keys=True))
        self.assertEqual(json.dumps(a.run['reads'], sort_keys=True), json.dumps(b.run['reads'], sort_keys=True))
        self.assertEqual(json.dumps(a.ledger(), sort_keys=True), json.dumps(b.ledger(), sort_keys=True))
        check('step_partition')

    def test_conservation_ledgers(self):
        h = powered()
        h.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={'hs_gapdh': 5e13, 'hs_actb': 5e12})
        run_plate(h, [W('A1', ['hs_gapdh'], sample='s'), W('A2', ['hs_actb'], sample='s'), W('A3', ['hs_actb', 'hs_tbp'], chem='probe', sample='s', rep=['fam', 'hex'])],
                  cycles=30, melt=False)
        L = h.ledger()
        made = sum(float(h.N[i, j] - h.plates['p1']['wells'][w]['copies'][a]) for w in ('A1', 'A2', 'A3') for i in [rt.WIDX[w]]
                   for j, a in enumerate(h.plates['p1']['wells'][w]['assays']))
        self.assertAlmostEqual(L['amplicon_copies_made'], made, delta=1e-6 * made)
        primers = float((h.p0 - h.P)[h.active].sum())
        self.assertAlmostEqual(L['primer_consumed_mol'] * rt.AVOGADRO / 2.0, primers, delta=1e-6 * primers)
        self.assertGreater(L['electrical_energy_j'], L['lid_heater_energy_j'])
        self.assertEqual(h.invoke('ledger.read')['ledger'], h.ledger())
        check('ledgers')

    def test_rejections_leave_state_unchanged(self):
        h = powered()
        v0 = json.dumps(h.read('evaluator'), sort_keys=True, default=str)
        for cap, args in [('plate.load', dict(plate_id='none')), ('run.start', dict(scan_mode='all_channels')), ('run.pause', {}), ('run.resume', {}),
                          ('plate.remove', {}), ('analysis.cq', {}), ('protocol.set', dict(name='x', sample_volume_m3=0.0, lid_temperature_k=378.15,
                                                                                            steps=[dict(kind='goto', goto_step=1, cycles=2)]))]:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(cap, **args)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True, default=str), v0)
        check('rejections_pure')

    def test_agent_isolation_ownership_idempotency_and_cancel(self):
        h = powered()
        h.register_principal('ag', 'agent')
        h.register_principal('ag2', 'agent')
        s, s2 = h.session('ag'), h.session('ag2')
        obs = s.read(instance_id=INST)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        self.assertEqual(s.invoke(instance_id=INST, capability_id='well.inspect', arguments=dict(well='A1'), request_id='x')['error']['code'], 'ACCESS_DENIED')
        r1 = s.invoke(instance_id=INST, capability_id='lid.open', arguments={}, request_id='o')
        self.assertEqual(r1['status'], 'succeeded')
        self.assertEqual(s.invoke(instance_id=INST, capability_id='lid.open', arguments={}, request_id='o'), r1)
        self.assertEqual(s.invoke(instance_id=INST, capability_id='lid.close', arguments={}, request_id='o')['error']['code'], 'REQUEST_CONFLICT')
        h.step(7.0)
        s.invoke(instance_id=INST, capability_id='plate.prepare', arguments=dict(plate_id='mine', plate_type='white_lowprofile_96', seal='optical_film',
                                                                                 wells=[W('A1', ['hs_gapdh'])]), request_id='p')
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='plate.load', arguments=dict(plate_id='mine'), request_id='l')['error']['code'], 'ACCESS_DENIED')
        s.invoke(instance_id=INST, capability_id='plate.load', arguments=dict(plate_id='mine'), request_id='l')
        s.invoke(instance_id=INST, capability_id='lid.close', arguments={}, request_id='c')
        h.step(7.0)
        s.invoke(instance_id=INST, capability_id='protocol.set', arguments=rt.PROTOCOL_EXAMPLE, request_id='pr')
        st = s.invoke(instance_id=INST, capability_id='run.start', arguments=dict(scan_mode='sybr_fam'), request_id='r')
        self.assertEqual(st['status'], 'accepted')
        h.step(300.0)
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='run.pause', arguments={}, request_id='z')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='analysis.cq', arguments={}, request_id='q')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s.poll(st['operation_id'])['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            s2.poll(st['operation_id'])
        c = s.cancel(st['operation_id'], 'cx')
        self.assertEqual(c['result']['phase'], 'cancelled')                         # protocol.md operation status
        self.assertEqual(s.poll(st['operation_id'])['status'], 'cancelled')
        self.assertEqual(h.read()['run.state'], 'stopped')
        self.assertEqual(s.invoke(instance_id=INST, capability_id='user.login', arguments={}, request_id='u')['status'], 'unsupported')
        check('isolation')

    def test_switch_lamps_skip_and_screen(self):
        h = powered()
        self.assertTrue(h.read()['power.enabled'])
        h.invoke('power.press_switch')
        self.assertFalse(h.read()['power.enabled'])
        self.assertIsNone(h.read()['display.block_temperature_c'])
        h.invoke('power.press_switch')
        out = h.invoke('screen.tilt', angle_rad=40 * math.pi / 180)
        self.assertAlmostEqual(h.read()['screen.tilt_rad'], 28 * math.pi / 180, places=9)
        self.assertAlmostEqual(out['tilt_from_reference_rad'], h.read()['screen.tilt_rad'], places=12)
        open_lid(h)
        self.assertEqual(h.read()['lid.state'], 'open')
        self.assertAlmostEqual(h.read()['lid.position_m'], rt.LID_TRAVEL)
        close_lid(h)
        protocol(h, extra=[dict(kind='temperature', temperature_k=368.15, hold_s=5.0), dict(kind='temperature', temperature_k=277.15)])
        h.invoke('run.start', scan_mode='sybr_fam')
        while h.run['state'] == 'lid_heating':
            h.step(1.0)
        self.assertTrue(h.read()['lamp.status_green'])
        h.invoke('run.pause')
        self.assertTrue(h.read()['status_bar.flashing'])
        h.invoke('run.resume')
        h.step(120.0)
        self.assertEqual(h.read()['run.step'], 2)                               # infinite hold at 4 C
        self.assertIsNone(h.read()['run.remaining_s'])
        h.invoke('run.skip_step')
        self.assertEqual(h.read()['run.state'], 'completed')
        self.assertTrue(h.read()['lamp.status_blue'])
        open_lid(h)
        self.assertFalse(h.read()['lamp.status_blue'])                          # blue until the lid is opened
        self.assertEqual(len(h.invoke('runs.list')['runs']), 1)
        self.assertTrue(h.invoke('events.read', since_s=0.0)['events'])
        self.assertEqual(len(h.invoke('provenance.read')['libraries']), 3)
        check('switch_lamps')


# ---------------------------------------------------------------- review round 1 (findings F-01..F-16, C-09)
class ReviewRound1(unittest.TestCase):
    def test_reporters_need_spectra_and_empty_wells_and_end_point_runs(self):
        h = powered()
        open_lid(h)
        with self.assertRaises(rt.ActionRejected) as e:                         # F-01: VIC has no FPbase spectra -> no channel response
            h.invoke('plate.prepare', plate_id='v', plate_type='white_lowprofile_96', seal='optical_film', wells=[W('A1', ['hs_actb'], chem='probe', rep=['vic'])])
        self.assertEqual(str(e.exception), 'INVALID_REPORTERS')
        h.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={'hs_gapdh': 5e13})
        out = h.invoke('plate.prepare', plate_id='p', plate_type='white_lowprofile_96', seal='none',
                       wells=[W('A1', []), W('A2', ['hs_gapdh'], sample='s'), W('A3', [], chem='probe')])
        self.assertEqual(out['warnings'], ['unsealed_plate_evaporation'])
        h.invoke('plate.load', plate_id='p')
        close_lid(h)
        h.invoke('protocol.set', name='endpoint', sample_volume_m3=2e-8, steps=[dict(kind='temperature', temperature_k=368.15, hold_s=30.0),
                 dict(kind='temperature', temperature_k=368.15, hold_s=10.0), dict(kind='temperature', temperature_k=333.15, hold_s=30.0), dict(kind='goto', goto_step=2, cycles=9)])
        h.invoke('run.start', scan_mode='all_channels')
        while h.run['state'] in ('lid_heating', 'running'):
            h.step(60.0)                                                          # F-16: wells without assays do not break the reads
        self.assertEqual(h.invoke('analysis.cq')['results'], [])                 # F-15: no amplification read -> empty results, no exception
        self.assertEqual(h.invoke('data.amplification')['curves'], [])
        self.assertEqual(h.invoke('analysis.standard_curve')['curves'], [])
        self.assertEqual(h.invoke('data.export', kind='cq_csv')['rows'], 0)
        k = powered()
        open_lid(k)
        k.invoke('plate.prepare', plate_id='q', plate_type='white_lowprofile_96', seal='optical_film', wells=[W('A1', []), W('B1', ['hs_gapdh'])])
        k.invoke('plate.load', plate_id='q')
        close_lid(k)
        protocol(k, cycles=3, melt=False)
        k.invoke('run.start', scan_mode='sybr_fam')
        while k.run['state'] in ('lid_heating', 'running'):
            k.step(60.0)
        amp = {c['well']: c for c in k.invoke('data.amplification')['curves']}
        self.assertEqual(set(amp), {'A1', 'B1'})                                  # a SYBR well without primers still shows the dye background
        self.assertAlmostEqual(amp['A1']['rfu'][0], amp['B1']['rfu'][0], delta=30.0)
        check('robust_wells_and_reads')

    def test_operation_statuses_lid_idle_and_progress(self):
        h = powered()
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        open_lid(h)
        close_lid(h)
        protocol(h, extra=[dict(kind='temperature', temperature_k=368.15, hold_s=5.0), dict(kind='temperature', temperature_k=277.15)])
        op = s.invoke(instance_id=INST, capability_id='run.start', arguments=dict(scan_mode='sybr_fam'), request_id='r')['operation_id']
        h.step(400.0)
        v = h.read()
        self.assertEqual((v['run.step'], v['run.remaining_s'], v['operation.progress']), (2, None, 1.0))   # F-11: waiting in the final infinite hold
        self.assertEqual(v['setpoint.lid_k'], rt.LID_IDLE_K)                    # F-03: guide p.102, 31 C during an infinite hold below 30 C
        self.assertAlmostEqual(v['sensor.lid_temperature_k'], rt.LID_IDLE_K, delta=0.5)
        self.assertEqual(s.poll(op)['status'], 'running')
        h.invoke('run.skip_step')
        self.assertEqual(s.poll(op)['status'], 'succeeded')                     # F-02: protocol.md statuses
        check('statuses_lid_idle_progress')

    def test_estimates_follow_the_thermal_path(self):
        for prot, scan in ((rt.PROTOCOL_EXAMPLE, 'sybr_fam'), (dict(name='g', sample_volume_m3=0.0, steps=[dict(kind='temperature', temperature_k=368.15, hold_s=120.0),
                            dict(kind='temperature', temperature_k=368.15, hold_s=10.0), dict(kind='gradient', lower_k=328.15, upper_k=341.15, hold_s=30.0, plate_read=True),
                            dict(kind='goto', goto_step=2)]), 'all_channels')):
            h = powered()
            open_lid(h)
            close_lid(h)
            est = h.invoke('protocol.set', **prot)['estimated_duration_s']
            est = h._estimate(h.protocol, scan)
            t0 = h.t
            h.invoke('run.start', scan_mode=scan)
            h.step(1500.0)
            rem = h.read()['run.remaining_s']
            while h.run['state'] in ('lid_heating', 'running'):
                h.step(30.0)
            act = h.run['finished_s'] - t0
            self.assertLess(abs(est - act) / act, 0.02, (est, act))                 # F-07: within 2 %
            self.assertLess(abs(1500.0 + rem - act) / act, 0.02, (rem, act))
        check('estimates')

    def test_skip_in_a_goto_loop_and_protocol_editor(self):
        h = powered()
        open_lid(h)
        close_lid(h)
        p = h.invoke('protocol.set', name='defaults', sample_volume_m3=2e-8, steps=[dict(kind='temperature', temperature_k=368.15, hold_s=5.0),
                     dict(kind='temperature', temperature_k=333.149, hold_s=30.4, plate_read=True), dict(kind='temperature', temperature_k=345.15, hold_s=20.0),
                     dict(kind='goto', goto_step=1), dict(kind='gradient', lower_k=328.17, upper_k=335.13, hold_s=10.0)])
        st = h.protocol['steps']
        self.assertEqual((h.protocol['lid_temperature_k'], st[3]['cycles'], st[1]['temperature_k'], st[1]['hold_s']), (rt.LID_DEFAULT_K, 39, 333.15, 30.0))
        self.assertEqual((round(st[4]['lower_k'], 2), round(st[4]['upper_k'], 2)), (328.15, 335.15))   # F-10: 0.1 C rounding and the documented defaults
        with self.assertRaises(rt.ActionRejected) as e:                          # Table 10: beep only on temperature steps
            h.invoke('protocol.set', name='x', sample_volume_m3=0.0, steps=[dict(kind='gradient', lower_k=328.15, upper_k=335.15, hold_s=10.0, beep=True)])
        self.assertEqual(str(e.exception), 'INVALID_STEP')
        with self.assertRaises(rt.ActionRejected) as e:
            h.invoke('protocol.set', name='x', sample_volume_m3=5e-10, steps=[dict(kind='temperature', temperature_k=368.15, hold_s=5.0)])
        self.assertEqual(str(e.exception), 'INVALID_SAMPLE_VOLUME')
        h.invoke('run.start', scan_mode='sybr_fam')
        while not (h.run['state'] == 'running' and h.run['k'] == 1):
            h.step(1.0)
        h.invoke('run.skip_step')                                                 # guide p.122: inside a GOTO loop -> next cycle
        self.assertEqual((h.run['k'], h.run['loops']), (0, {3: 1}))
        self.assertEqual(len(h.run['reads']), 0)
        check('skip_loop_editor')

    def test_primer_dimer_ntc_and_effective_melting(self):
        h = powered()
        dim = [a for a, v in rt.assays().items() if v['primer_dimer_score'] == 3][:3]
        clean = [a for a, v in rt.assays().items() if v['primer_dimer_score'] == 1][:3]
        run_plate(h, [W(f'A{c + 1}', [a], stype='ntc') for c, a in enumerate(dim + clean)], scan='sybr_fam')
        cq = cq_of(h)
        pk = {p['well']: p['peaks_k'] for p in h.invoke('analysis.melt_peaks')['peaks']}
        late = [cq[(f'A{c + 1}', 'sybr_green')] for c in range(3)]               # F-12: dimer-prone primers give a late NTC signal at the dimer Tm
        self.assertGreaterEqual(sum(1 for x in late if x is not None and 35.0 < x < 40.5), 2, late)
        for c in range(3):
            self.assertTrue(pk[f'A{c + 1}'] and abs(pk[f'A{c + 1}'][0] - rt.CHEM['pd_tm_k']) < 3.0)
            self.assertIsNone(cq[(f'A{c + 4}', 'sybr_green')])
        k = powered()
        k.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={'hs_pou5f1': 5e13, 'hs_esr1': 5e13})
        run_plate(k, [W('A1', ['hs_pou5f1'], sample='s'), W('A2', ['hs_esr1'], sample='s'), W('A3', ['hs_esr1'], sample='s')], scan='sybr_fam')
        cq = cq_of(k)
        self.assertTrue(all(cq[(w, 'sybr_green')] is not None and cq[(w, 'sybr_green')] < 25.0 for w in ('A1', 'A2', 'A3')))   # F-06: GC-rich amplicons denature at 95 C
        check('dimers_melting')


class ReviewRound2(unittest.TestCase):
    def _run(self, h, prot, scan='sybr_fam'):
        open_lid(h)
        close_lid(h)
        h.invoke('protocol.set', **prot)
        h.invoke('run.start', scan_mode=scan)

    def test_outages_resume_the_interrupted_step(self):
        prot = dict(name='p14', sample_volume_m3=2e-8, steps=[dict(kind='temperature', temperature_k=368.15, hold_s=30.0), dict(kind='temperature', temperature_k=368.15, hold_s=10.0),
                    dict(kind='temperature', temperature_k=333.15, hold_s=30.0, plate_read=True), dict(kind='goto', goto_step=2, cycles=13),
                    dict(kind='melt', start_k=338.15, end_k=343.15, melt_increment_k=0.5, hold_s=5.0)])
        h = powered()
        self._run(h, prot)
        while h.run['protocol']['steps'][h.run['k']]['kind'] != 'melt' if h.run['k'] < 5 else True:
            h.step(10.0)
        h.step(12.0)
        h.invoke('fault.inject', fault='power_loss', active=True)
        h.step(60.0)
        h.invoke('fault.inject', fault='power_loss', active=False)
        h.step(20.0)                                                              # still re-heating the lid ...
        self.assertEqual(h.read()['run.state'], 'lid_heating')
        h.invoke('fault.inject', fault='power_loss', active=True)                  # F2-01: ... when the second outage comes
        h.step(60.0)
        h.invoke('fault.inject', fault='power_loss', active=False)
        while h.run['state'] in ('lid_heating', 'running'):
            h.step(30.0)
        amp = [x for x in h.run['reads'] if x['kind'] == 'amp']
        melt = [x for x in h.run['reads'] if x['kind'] == 'melt']
        self.assertEqual((h.run['state'], len(amp), len(melt), h.run['warnings']), ('completed', 14, 11, ['POWER_FAILURE', 'POWER_FAILURE']))
        k = powered()
        self._run(k, prot)
        k.step(500.0)
        k.invoke('fault.inject', fault='power_loss', active=True)
        k.invoke('power.press_switch')                                           # F2-05: switched off during the outage -> the run ends
        self.assertEqual(k.read('evaluator')['run.state'], 'stopped')
        check('double_outage')

    def test_drifted_sensor_and_large_panels_and_export_guard(self):
        h = powered()
        h.invoke('fault.inject', fault='block_sensor_drift', active=True)        # F2-02: calculated mode follows the (drifted) sensor - the run completes
        self._run(h, dict(name='d', sample_volume_m3=2e-8, steps=[dict(kind='temperature', temperature_k=368.15, hold_s=10.0),
                                                                    dict(kind='temperature', temperature_k=333.15, hold_s=10.0), dict(kind='goto', goto_step=1, cycles=2)]))
        while h.run['state'] in ('lid_heating', 'running') and h.t < 3000.0:
            h.step(30.0)
        self.assertEqual(h.run['state'], 'completed')
        k = powered()
        open_lid(k)
        targets = list(rt.assays())[:64]
        k.invoke('sample.provide', sample_id='s', kind='standard', copies_per_m3={'hs_gapdh': 1.0})
        wells = [W(w, [a], stype='standard', sample='s', q=1.0) for w, a in zip(rt.WELLS, targets)]
        k.invoke('plate.prepare', plate_id='panel', plate_type='white_lowprofile_96', seal='optical_film', wells=wells)
        k.invoke('plate.load', plate_id='panel')
        close_lid(k)
        protocol(k, cycles=3, melt=False)
        k.invoke('run.start', scan_mode='sybr_fam')
        while k.run['state'] in ('lid_heating', 'running'):
            k.step(60.0)
        self.assertEqual(len(k.invoke('analysis.standard_curve')['curves']), 64)  # F2-03: the schema allows one curve per target (validated by the wrapper)
        m = powered()
        m.register_principal('a1', 'agent')
        m.register_principal('a2', 'agent')
        s1, s2 = m.session('a1'), m.session('a2')
        open_lid(m)
        close_lid(m)
        s1.invoke(instance_id=INST, capability_id='protocol.set', arguments=dict(name='t', sample_volume_m3=0.0, steps=[dict(kind='temperature', temperature_k=333.15,
                                                                                hold_s=5.0, plate_read=True)]), request_id='p1')
        s1.invoke(instance_id=INST, capability_id='run.start', arguments=dict(scan_mode='sybr_fam'), request_id='r1')
        m.step(300.0)
        s2.invoke(instance_id=INST, capability_id='run.start', arguments=dict(scan_mode='sybr_fam'), request_id='r2')
        m.step(5.0)
        out = s1.invoke(instance_id=INST, capability_id='data.export', arguments=dict(kind='cq_csv', run_id='R001'), request_id='x1')
        self.assertEqual(out['status'], 'succeeded')                               # F2-06: exporting one's own finished run is not run control
        check('drift_panels_export')

    def test_touchdown_after_a_skip_keeps_the_cycle_index(self):
        h = powered()
        self._run(h, dict(name='td', sample_volume_m3=0.0, steps=[dict(kind='temperature', temperature_k=368.15, hold_s=5.0),
                     dict(kind='temperature', temperature_k=343.15, hold_s=5.0, increment_k=-1.0, plate_read=True), dict(kind='temperature', temperature_k=345.15, hold_s=5.0),
                     dict(kind='goto', goto_step=1, cycles=3)]))
        seen = []
        skipped = False
        while h.run['state'] in ('lid_heating', 'running'):
            h.step(0.1)
            if h.run['state'] == 'running' and h.run['k'] == 1 and h.run['phase'] == 'hold' and (not seen or seen[-1][1] != h.run['loops'].get(3, 0)):
                seen.append((float(h.run['target'][0]), h.run['loops'].get(3, 0)))
                if len(seen) == 2 and not skipped:
                    h.invoke('run.skip_step')                                     # skip the rest of cycle 2
                    skipped = True
        self.assertEqual([round(t, 2) for t, _ in seen], [343.15, 342.15, 341.15, 340.15])   # F2-04: increments follow the GOTO pass number
        check('touchdown_skip')


class ReviewRound3(unittest.TestCase):
    def test_calculated_temperature_restarts_after_an_outage(self):
        h = powered()
        open_lid(h)
        close_lid(h)
        h.invoke('protocol.set', name='c', sample_volume_m3=2e-8, steps=[dict(kind='temperature', temperature_k=368.15, hold_s=10.0),
                 dict(kind='temperature', temperature_k=333.15, hold_s=60.0, plate_read=True), dict(kind='goto', goto_step=1, cycles=1)])
        h.invoke('run.start', scan_mode='sybr_fam')
        while not (h.run['state'] == 'running' and h.run['k'] == 1 and h.run['phase'] == 'hold'):
            h.step(0.1)
        h.step(5.0)
        h.invoke('fault.inject', fault='power_loss', active=True)
        h.step(7200.0)                                                            # 2 h outage: the block cools to the room
        h.invoke('fault.inject', fault='power_loss', active=False)
        self.assertAlmostEqual(h.read()['display.block_temperature_c'], rt.PLANT_EXAMPLE['room_k'] - 273.15, delta=0.3)   # F3-01: not frozen at 60 C
        while not (h.run['state'] == 'running' and h.run['phase'] == 'hold'):
            h.step(0.1)
        self.assertLess(abs(float(np.mean(h.run['calc_T'])) - 333.15), 0.3)        # the resumed step waits for the modelled sample
        self.assertLess(abs(float(np.mean(h.T_row)) - 333.15), 0.5)
        while h.run['state'] in ('lid_heating', 'running'):
            h.step(10.0)
        self.assertEqual(h.run['state'], 'completed')
        check('calc_restart')

    def test_foreign_agents_cannot_switch_off_during_an_outage_and_other_editor_rules(self):
        h = powered()
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        open_lid(h)
        close_lid(h)
        s1.invoke(instance_id=INST, capability_id='protocol.set', arguments=rt.PROTOCOL_EXAMPLE, request_id='p')
        op = s1.invoke(instance_id=INST, capability_id='run.start', arguments=dict(scan_mode='sybr_fam'), request_id='r')['operation_id']
        h.step(300.0)
        h.invoke('fault.inject', fault='power_loss', active=True)
        for cap, args in (('power.set', dict(enabled=False)), ('power.press_switch', {})):       # F3-02: no switching OFF of another agent's run
            self.assertEqual(s2.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x' + cap)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s1.poll(op)['status'], 'running')
        for st in ([dict(kind='temperature', temperature_k=368.15, hold_s=5.0), dict(kind='goto', goto_step=1, cycles=2, beep=True)],
                   [dict(kind='melt', start_k=338.15, end_k=348.15, melt_increment_k=0.5, beep=True)]):
            with self.assertRaises(rt.ActionRejected) as e:                      # F3-03: beep only on temperature steps
                powered().invoke('protocol.set', name='b', sample_volume_m3=0.0, steps=st)
            self.assertEqual(str(e.exception), 'INVALID_STEP')
        k = powered()
        k.invoke('sample.provide', sample_id='s', kind='template', copies_per_m3={'hs_gapdh': 5e13})
        run_plate(k, [W('A1', ['hs_gapdh'], sample='s')], scan='sybr_fam', cycles=3)
        open_lid(k)
        k.step(1200.0)                                                            # F3-04: the film holds the water also with the lid open
        self.assertEqual(k.invoke('well.inspect', well='A1')['truth']['volume_m3'], 2e-8)
        check('foreign_switch_beep_seal')


# ---------------------------------------------------------------- declared effects over scenarios
_SC = {}


def _build(name):
    if name == 'fresh':
        h = rt.BioradCfxOpusRuntime()
        h.invoke('plant.configure', **rt.PLANT_EXAMPLE)
        return h
    if name == 'idle':
        return powered()
    if name == 'off':
        h = powered()
        h.invoke('power.set', enabled=False)
        return h
    if name == 'lid_open':
        h = powered()
        open_lid(h)
        h.invoke('sample.provide', sample_id='sb', kind='template', copies_per_m3={'hs_gapdh': 5e13})
        h.invoke('plate.prepare', plate_id='pb', plate_type='white_lowprofile_96', seal='optical_film', wells=[W('A1', ['hs_gapdh'], sample='sb'), W('A2', ['hs_gapdh'])])
        return h
    if name == 'loaded_open':
        h = _scenario('lid_open')
        h.invoke('plate.load', plate_id='pb')
        return h
    if name == 'ready':
        h = _scenario('loaded_open')
        close_lid(h)
        protocol(h, cycles=4)
        return h
    if name == 'lid_heating':
        h = _scenario('ready')
        h.invoke('run.start', scan_mode='sybr_fam')
        h.step(5.0)
        return h
    if name == 'running':
        h = _scenario('lid_heating')
        h.step(400.0)
        return h
    if name == 'paused':
        h = _scenario('running')
        h.invoke('run.pause')
        return h
    if name == 'melting':
        h = _scenario('running')
        while h.run['state'] == 'running' and h.run['protocol']['steps'][h.run['k']]['kind'] != 'melt':
            h.step(10.0)
        h.step(30.0)
        return h
    if name == 'completed':
        h = _scenario('melting')
        while h.run['state'] == 'running':
            h.step(30.0)
        return h
    if name == 'obstructed':
        h = _scenario('lid_open')
        h.invoke('fault.inject', fault='lid_obstructed', active=True)
        h.invoke('lid.close')
        h.step(8.0)
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'idle', 'off', 'lid_open', 'loaded_open', 'ready', 'lid_heating', 'running', 'paused', 'melting', 'completed', 'obstructed']
STEP_DTS = (0.5, 5.0, 60.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0), dict(rt.PLANT_EXAMPLE, room_k=300.15)],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_switch': [{}], 'lid.open': [{}], 'lid.close': [{}],
        'screen.tilt': [dict(angle_rad=0.6)], 'sample.provide': [dict(sample_id='sx', kind='template', copies_per_m3={'hs_actb': 1e12})],
        'plate.prepare': [dict(plate_id='px', plate_type='clear_lowprofile_96', seal='heat_seal', wells=[W('H12', ['hs_actb'])])],
        'plate.load': [dict(plate_id='pb')], 'plate.remove': [{}], 'protocol.set': [rt.PROTOCOL_EXAMPLE],
        'run.start': [dict(scan_mode='all_channels')], 'run.pause': [{}], 'run.resume': [{}], 'run.skip_step': [{}],
        'fault.inject': [dict(fault='lid_obstructed', active=True), dict(fault='power_loss', active=True), dict(fault='led_failure_channel_1', active=True),
                         dict(fault='fan_failure', active=True), dict(fault='block_sensor_drift', active=True), dict(fault='lid_obstructed', active=False)],
        'sim.step': [dict(dt_s=1.0)],
    }
    return V.get(cap, [copy.deepcopy(rt.CAPS[cap]['example_arguments'])])


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
