"""CHEM-169 MicroCal PEAQ-ITC (standard, with the washing module): runtime tests (python3 -m unittest tests/test_asset_malvern_microcalpeaq.py, matterix env)."""
import copy
import json
import math
import sys
import unittest
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import malvern_microcalpeaq as m  # noqa: E402
import asset_tool  # noqa: E402

warnings.filterwarnings('ignore', category=RuntimeWarning)
rt = m
UC = m.UCAL
SEEN = set()                                     # (capability, rejection code) pairs produced by the tests
CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}


def _meta():
    if not _META:
        _META.update(m.export_malvern_microcalpeaq_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = m.MalvernMicrocalpeaqRuntime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema, the clock and the declared immediate effects; every rejection code is
    recorded and must leave the state unchanged."""
    before = self.read('evaluator') if capability_id != 'sim.step' else None
    t0 = self.t
    try:
        out = _orig_invoke(self, capability_id, **a)
    except m.ActionRejected as exc:
        REJECTIONS.setdefault(capability_id, set()).add(str(exc))
        if before is not None:
            after = self.read('evaluator')
            ch = {k for k in after if _changed(before[k], after[k])}
            if ch:
                raise AssertionError(f'{capability_id} rejected but changed state: {sorted(ch)}')
        raise
    meta = _meta()['capabilities'][capability_id]
    errs = asset_tool.validate_value(out, meta['output_schema'])
    if errs:
        raise AssertionError(f'{capability_id} output violates its schema: {errs[:3]}')
    if before is not None:
        assert self.t == t0, capability_id
        after = self.read('evaluator')
        undeclared = {k for k in after if _changed(before[k], after[k])} - set(m.EFFECTS.get(capability_id, []))
        if undeclared:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=json.loads(json.dumps(a, default=str)), result_keys=sorted(out)))
    return out


m.MalvernMicrocalpeaqRuntime.invoke = _instrumented_invoke


def INJ(n, v=2e-9, sp=150.0, d=4.0, first=4e-10):
    return [dict(volume_m3=first, duration_s=0.8, spacing_s=sp)] + [dict(volume_m3=v, duration_s=d, spacing_s=sp)] * n


class Rig:
    def __init__(self, seed=7, room=295.15, age=0.0, warm=3000.0, power=True):
        self.R = m.MalvernMicrocalpeaqRuntime()
        self.R.register_principal('agent', 'agent')
        self.R.register_principal('other', 'agent')
        self.R.register_principal('ev', 'evaluator')
        self.A, self.B, self.E = self.R.session('agent'), self.R.session('other'), self.R.session('ev')
        self.I = self.R.instance_id
        self.n = 0
        self.call(self.E, 'plant.configure', room_k=room, mains_voltage_v=230.0, noise_seed=seed, reference_age_s=age)
        if power:
            self.call(self.A, 'power.set', enabled=True)
            self.call(self.A, 'wash.power_set', enabled=True)
            self.step(warm)

    def call(self, s, cap, expect=None, **a):
        self.n += 1
        r = s.invoke(instance_id=self.I, capability_id=cap, arguments=a, request_id=f'q{self.n}')
        if expect is None:
            assert r['status'] in ('succeeded', 'accepted'), (cap, r['error'])
        else:
            assert r['status'] in ('rejected', 'unsupported') and r['error']['code'] == expect, (cap, expect, r['status'], r['error'])
            SEEN.add((cap, expect))
        return r

    def rd(self, *ids):
        return {k: v['value'] for k, v in self.E.read(instance_id=self.I, state_ids=list(ids))['observations'].items()}

    def v(self, k):
        return self.rd(k)[k]

    def step(self, dt):
        self.call(self.E, 'sim.step', dt_s=float(dt))

    def until(self, pred, maxs=40000.0, dt=1.0):
        t = 0.0
        while not pred() and t < maxs:
            self.R.step(dt)
            t += dt
        return t

    def method(self, inj, T=298.15, pref=5.0, fb='high', stir=12.5, delay=60.0, cell=0.1, syr=1.0, s=None):
        return self.call(s or self.A, 'method.set', temperature_k=T, reference_power_w=pref * UC, feedback=fb, stir_rate_hz=stir, initial_delay_s=delay, injections=inj,
                         cell_concentration_mol_m3=cell, syringe_concentration_mol_m3=syr)['result']

    def prep(self, sid, buf=None, solutes=None, vol=1e-6, buffer_from=None, degas=True, s=None):
        a = dict(solution_id=sid, volume_m3=vol)
        if buf:
            a['buffer'] = dict(buffer_id=buf[0], concentration_mol_m3=buf[1], ph=buf[2])
        if buffer_from:
            a['buffer_from'] = buffer_from
        if solutes:
            a['solutes'] = [dict(species_id=k, concentration_mol_m3=v) for k, v in solutes.items()]
        r = self.call(s or self.A, 'solution.prepare', **a)['result']
        if degas:
            self.call(s or self.A, 'solution.degas', solution_id=sid)
        return r

    def stock(self, sid, buf, comps, vol=1e-6):
        self.call(self.E, 'stock.provide', stock_id=sid, volume_m3=vol, buffer=dict(buffer_id=buf[0], concentration_mol_m3=buf[1], ph=buf[2]) if buf else None,
                  components=[dict(species_id=k, concentration_mol_m3=v) for k, v in comps.items()])

    def move(self, loc, s=None):
        self.call(s or self.A, 'pipette.move', location=loc)
        self.until(lambda: self.R.pip['phase'] is None)

    def away(self, s=None):
        if self.R.pip['loc'] == 'cell':
            self.move('rest', s)

    def load_syringe(self, sol, tube=6e-8, s=None):
        s = s or self.A
        self.away(s)
        if self.R.tube is not None:
            if self.R.pip['loc'] == 'load':
                self.move('rest', s)
            self.call(s, 'tube.remove')
        self.call(s, 'tube.place', solution_id=sol, volume_m3=tube)
        if not self.R.fpa:
            self.call(s, 'fpa.attach')
        self.call(s, 'syringe.plunger_down')
        self.until(lambda: self.R.plunger_target is None)
        self.move('load', s)
        op = self.call(s, 'syringe.load')['operation_id']
        self.until(lambda: self.R.operations[op]['phase'] != 'running')
        self.move('rest', s)
        self.call(s, 'fpa.detach')
        return op

    def load(self, cell_sol, syr_sol, tube=6e-8, cellvol=3e-7, rinse=True, s=None):
        s = s or self.A
        self.away(s)
        self.call(s, 'cell.load', solution_id=cell_sol, volume_m3=cellvol, rinse=rinse)
        self.load_syringe(syr_sol, tube, s)
        self.move('cell', s)

    def run(self, eid, s=None, maxs=60000.0):
        op = self.call(s or self.A, 'run.start', experiment_id=eid)['operation_id']
        self.until(lambda: self.R.operations[op]['phase'] != 'running', maxs=maxs, dt=2.0)
        return (s or self.A).poll(op)

    def an(self, eid, model='one_set_of_sites', s=None, **kw):
        return self.call(s or self.A, 'data.analyze', experiment_id=eid, model=model, **kw)['result']

    def clean(self, cell_method='wash', syringe_method='rinse', stop_after=None):
        self.away()
        if cell_method != 'none' and not self.R.tool_in:
            self.call(self.A, 'cleaning_tool.insert')
        if syringe_method != 'none':
            self.move('clean')
            if not self.R.fpa:
                self.call(self.A, 'fpa.attach')
        op = self.call(self.A, 'clean.start', cell_method=cell_method, syringe_method=syringe_method)['operation_id']
        if stop_after is not None:
            self.R.step(stop_after)
            self.call(self.A, 'instrument.stop')
        else:
            self.until(lambda: self.R.operations[op]['phase'] != 'running')
        return self.R.operations[op]

    def ca_edta(self, inj=None, eid='e1', pref=5.0, T=298.15, rinse=True):
        self.method(inj or INJ(19), cell=0.1, syr=1.0, pref=pref, T=T)
        self.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1}, vol=3e-6)
        self.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        self.load('edta', 'ca', rinse=rinse)
        return self.run(eid)


def pv(res):
    return {p['name']: p['value'] for p in res['parameters']}


# ---------------------------------------------------------------- contract and libraries
class ContractAndLibraries(unittest.TestCase):
    def test_contract_metadata_and_examples(self):
        meta = m.export_malvern_microcalpeaq_contract_metadata()
        self.assertEqual(len(meta['capabilities']), len(m.CAPS))
        for k, c in meta['capabilities'].items():
            self.assertEqual(asset_tool.validate_value(c['example_arguments'], c['input_schema']), [], k)
            m.validate(m.CAPS[k]['input_schema'], c['example_arguments'])
        for k, v in m.EFFECTS.items():
            self.assertTrue(set(v) <= set(m.SLOTS), k)
        for k, v in m.ON_STEP.items():
            self.assertTrue(set(v) <= set(m.SLOTS), k)
        self.assertTrue(all(k in m.PRECONDITIONS or k in m.UNSUPPORTED or m.CAPS[k]['kind'] == 'query' or k in ('plant.configure', 'sim.step') for k in m.CAPS))
        CHECKS.append(dict(check='contract_examples_validate', status='passed', capabilities=len(m.CAPS)))

    def test_buffer_library_real_data(self):
        b = m.buffers()
        documented = [k for k, v in b.items() if v['evidence'] == 'documented' and any(s['pK'] is not None and s['dH_J_mol'] is not None for s in v['steps'])]
        self.assertGreaterEqual(len(b), 69)
        self.assertGreaterEqual(len(documented), 50)
        for v in b.values():
            self.assertTrue(v['source'] and v['evidence'])
        # Goldberg 2002 section 8 values
        self.assertAlmostEqual(b['mes']['steps'][0]['pK'], 6.27)
        self.assertAlmostEqual(b['tris']['steps'][0]['dH_J_mol'], 47450.0)
        self.assertAlmostEqual(b['hepes']['steps'][-1]['pK'], 7.564)
        self.assertAlmostEqual(b['phosphate']['steps'][1]['dH_J_mol'], 3600.0)
        self.assertAlmostEqual(b['water']['steps'][0]['dH_J_mol'], 55810.0)
        CHECKS.append(dict(check='buffer_library', status='passed', buffers=len(b), documented=len(documented)))

    def test_acid_base_solver(self):
        T = 298.15
        th = m.proton_excess_for_ph({'tris': 0.02}, 8.0, 0.0, T)
        r0, r1 = m._speciate({'tris': 0.02}, th, 0.0, T), m._speciate({'tris': 0.02}, th + 1e-4, 0.0, T)
        self.assertAlmostEqual(r0['ph'], 8.0, places=6)
        self.assertAlmostEqual((r1['enthalpy'] - r0['enthalpy']) / 1e-4, -47450.0, delta=600.0)      # protonation of Tris
        # phosphate pK2 at I ~ 0.1 drops by ~4 A f(I) (Debye-Hueckel; the familiar ~6.8)
        th = m.proton_excess_for_ph({'phosphate': 0.05}, 7.0, 0.0, T)
        sp = m._speciate({'phosphate': 0.05}, th, 0.0, T)
        ratio = sp['species'][('phosphate', 1)] / sp['species'][('phosphate', 2)]
        pk2_mixed = 7.0 - math.log10(ratio)                                          # pH (activity) - log([HPO4]/[H2PO4]): the familiar ~6.86 at I ~ 0.1
        self.assertTrue(6.75 < pk2_mixed < 6.95, pk2_mixed)
        # van 't Hoff with dCp: Tris pK falls ~0.028 per K
        self.assertAlmostEqual(m._vh(8.072, 47450.0, -59.0, 308.15)[0] - 8.072, -0.25, delta=0.03)
        CHECKS.append(dict(check='acid_base_solver', status='passed'))

    def test_dilution_and_binding_libraries(self):
        hxx, hxxx = m._dilution_params('propan_1_ol', 298.15)
        self.assertAlmostEqual(-(hxx * 1.849 + hxxx * 1.849 ** 2), -1572.0, delta=2.0)              # IUPAC check value
        A, B = m._dilution_params('sucrose', 298.15)
        self.assertAlmostEqual(A, -834.95 + 4.719 * 298.15)
        bl = m.bindings()
        self.assertAlmostEqual(bl['ca_edta_mes_ph56']['kd_m'], 573e-9)
        self.assertAlmostEqual(bl['ba_18crown6']['dh_j_mol'], -31420.0)
        self.assertAlmostEqual(1 / bl['ba_18crown6']['kd_m'], 5900.0, places=3)
        CHECKS.append(dict(check='dilution_binding_libraries', status='passed'))

    def test_design_simulator_ca_edta(self):
        r = Rig(power=False)
        out = r.call(r.A, 'design.simulate', n=1.01, kd_mol_m3=5.73e-4, dh_j_mol=-16359.44, cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0,
                     injection_volumes_m3=[4e-10] + [2e-9] * 19)['result']
        self.assertAlmostEqual(out['second_injection_heat_j'] / UC, -7.9, delta=0.3)              # TN240816: injection 2 -7.9 ucal
        self.assertAlmostEqual(out['total_heat_j'] / UC, -78.2, delta=0.05 * 78.2)                # TN240816: total -78.2 ucal (measured total includes dilution heats)
        self.assertAlmostEqual(out['c_value'], 176.3, delta=1.0)
        low = r.call(r.A, 'design.simulate', n=1.0, kd_mol_m3=1e-3, dh_j_mol=-4000.0, cell_concentration_mol_m3=0.002, syringe_concentration_mol_m3=0.05,
                     injection_volumes_m3=[2e-9] * 19)['result']
        self.assertTrue(any('1 µcal' in g for g in low['guidance']) and any('c值' in g for g in low['guidance']))
        CHECKS.append(dict(check='design_simulator_tn240816', status='passed', inj2_ucal=out['second_injection_heat_j'] / UC))


# ---------------------------------------------------------------- calibration and instrument physics
class Calibration(unittest.TestCase):
    def test_ca_edta_test_kit_reproduces_tn240816(self):
        r = Rig(seed=1)
        poll = r.ca_edta()
        self.assertEqual(poll['status'], 'succeeded')
        res = r.an('e1', exclude_injections=[1], control='fitted_offset')
        p = pv(res)
        self.assertAlmostEqual(p['n'], 1.01, delta=0.03)
        self.assertAlmostEqual(p['kd'] * 1e6 / 1000, 573e-3, delta=0.573e-1 * 1.5)       # KD 573 nM +-15 %
        self.assertAlmostEqual(p['dh'], -16359.44, delta=0.03 * 16359.44)
        inj = res['injections']
        self.assertAlmostEqual(inj[1]['heat_j'] / UC, -7.9, delta=0.4)
        self.assertAlmostEqual(sum(i['heat_j'] for i in inj) / UC, -78.2, delta=0.05 * 78.2)
        self.assertEqual(res['bin'], 'Binding')
        self.assertLess(abs(inj[0]['ndh_j_mol']), 0.9 * abs(inj[1]['ndh_j_mol']))                  # first injection artifact (needle-tip diffusion)
        d = res['derived']
        self.assertAlmostEqual(d['dg_j_mol'], m.R_GAS * 298.15 * math.log(p['kd'] / 1000.0), places=6)
        self.assertAlmostEqual(d['minus_tds_j_mol'], d['dg_j_mol'] - p['dh'], places=6)
        CHECKS.append(dict(check='ca_edta_tn240816', status='passed', n=p['n'], kd_mol_m3=p['kd'], dh_j_mol=p['dh'], inj2_ucal=inj[1]['heat_j'] / UC))

    def test_feedback_response_noise_and_stirring_baseline(self):
        out = {}
        for fb, sp in (('high', 150.0), ('none', 400.0)):
            r = Rig(seed=3)
            r.method(INJ(2, sp=sp), fb=fb)
            r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
            r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
            r.load('edta', 'ca')
            r.run('e')
            e = r.R.experiments['e']
            t, dp = np.array(e['t']), np.array(e['dp'])
            s2 = e['injections'][1]['start_s']
            base = dp[(t > s2 - 10) & (t < s2)].mean()
            tau0 = m.ENG['response_time_s'][fb]
            w = (t > s2 + 25) & (t < s2 + 25 + 3 * tau0)
            tau = -1 / np.polyfit(t[w], np.log(np.abs(dp[w] - base)), 1)[0]
            pre = dp[t < 55]
            out[fb] = (tau, np.std(np.diff(pre)) / math.sqrt(2), pre.mean())
        self.assertAlmostEqual(out['high'][0], 8.0, delta=0.5)                      # documented: response time 8 s (63 % of the peak)
        self.assertAlmostEqual(out['none'][0], 60.0, delta=4.0)
        self.assertAlmostEqual(out['high'][1] / 4.184e-9, 0.15, delta=0.03)         # documented: 0.15 ncal/s
        self.assertLess(out['none'][1], out['high'][1])
        self.assertAlmostEqual((out['high'][2] - 5 * UC) / UC, -0.25, delta=0.03)   # stirring friction lowers the baseline below the reference power
        CHECKS.append(dict(check='response_noise_stirring', status='passed', tau_high_s=out['high'][0], tau_none_s=out['none'][0]))

    def test_iupac_ba_18_crown_6_and_heat_capacity(self):
        dh = {}
        for T in (288.15, 308.15):
            r = Rig(seed=11)
            inj = [dict(volume_m3=4e-10, duration_s=2.0, spacing_s=180.0)] + [dict(volume_m3=1.5e-9, duration_s=8.0, spacing_s=180.0)] * 24
            r.method(inj, T=T, pref=10.0, cell=1.0, syr=10.0)
            r.prep('crown', None, {'18_crown_6': 1.0})
            r.prep('ba', None, {'ba2+': 10.0})
            r.step(2400)
            r.load('crown', 'ba')
            r.run('b')
            res = r.an('b', control='fitted_offset')
            p = pv(res)
            dh[T] = p['dh']
            kd_ref = 1 / 5900.0 * 1000 * math.exp(-31420.0 / m.R_GAS * (1 / T - 1 / 298.15))     # van 't Hoff from the IUPAC values (approximate)
            self.assertAlmostEqual(p['kd'] / kd_ref, 1.0, delta=0.2)
            self.assertAlmostEqual(p['n'], 1.0, delta=0.03)
        dcp = (dh[308.15] - dh[288.15]) / 20.0
        self.assertAlmostEqual((dh[288.15] + dh[308.15]) / 2, -31420.0, delta=1500.0)    # IUPAC 2001: -31.42 kJ/mol at 298.15 K
        self.assertTrue(60.0 < dcp < 190.0, dcp)                                          # IUPAC 2001: dCp = 126 J/K/mol
        CHECKS.append(dict(check='iupac_ba_crown', status='passed', dcp_j_k_mol=dcp))


class Models(unittest.TestCase):
    def test_competitive_displacement(self):
        r = Rig(seed=5)
        r.method(INJ(19), cell=0.02, syr=0.2)
        r.prep('cell', ('phosphate', 20.0, 7.5), {'carbonic_anhydrase_ii': 0.02, 'sulfanilamide': 1.0})
        r.prep('az', buffer_from='cell', solutes={'acetazolamide': 0.2})
        r.load('cell', 'az')
        r.run('c')
        direct = pv(r.an('c', control='fitted_offset'))
        comp = pv(r.an('c', model='competitive', control='fitted_offset', competitor=dict(kd_mol_m3=0.15, dh_j_mol=-33472.0, concentration_mol_m3=1.0)))
        self.assertGreater(direct['kd'], 4 * 2e-5)                        # the weak competitor makes acetazolamide look weaker
        self.assertAlmostEqual(comp['kd'] / 2e-5, 1.0, delta=0.25)        # design example: KD 20 nM
        self.assertAlmostEqual(comp['dh'], -50208.0, delta=2500.0)
        self.assertGreater(direct['dh'], comp['dh'] + 15000.0)            # apparent dH includes the displaced sulfanilamide
        CHECKS.append(dict(check='competitive', status='passed', kd_mol_m3=comp['kd']))

    def test_two_sets_and_sequential_sites(self):
        r = Rig(seed=5)
        r.call(r.E, 'interaction.define', interaction_id='a', model='sites', receptor='p2', ligand='l2', n=1.0, kd_mol_m3=1e-5, dh_j_mol=-40000.0, site='a')
        r.call(r.E, 'interaction.define', interaction_id='b', model='sites', receptor='p2', ligand='l2', n=1.0, kd_mol_m3=5e-3, dh_j_mol=-15000.0, site='b')
        r.call(r.E, 'interaction.define', interaction_id='sq', model='sequential', receptor='p3', ligand='l3', ks_mol_m3=[1e-3, 5e-2], dhs_j_mol=[-30000.0, -10000.0])
        r.stock('p2s', ('hepes', 20.0, 7.5), {'p2': 0.03})
        r.stock('l2s', ('hepes', 20.0, 7.5), {'l2': 0.6})
        r.stock('p3s', ('hepes', 20.0, 7.5), {'p3': 0.05})
        r.stock('l3s', ('hepes', 20.0, 7.5), {'l3': 1.0})
        r.method(INJ(19), cell=0.03, syr=0.6)
        r.load('p2s', 'l2s')
        r.run('t2')
        two = pv(r.an('t2', model='two_sets_of_sites', control='fitted_offset'))
        kds = sorted([(two['kd1'], two['dh1']), (two['kd2'], two['dh2'])])
        self.assertAlmostEqual(kds[0][0] / 1e-5, 1.0, delta=0.4)
        self.assertAlmostEqual(kds[1][0] / 5e-3, 1.0, delta=0.4)
        self.assertAlmostEqual(kds[0][1], -40000.0, delta=2500.0)
        self.assertAlmostEqual(kds[1][1], -15000.0, delta=3000.0)
        r.clean()
        r.call(r.A, 'cleaning_tool.remove')
        r.method(INJ(19), cell=0.05, syr=1.0)
        r.load('p3s', 'l3s')
        r.run('sq')
        sq = pv(r.an('sq', model='sequential_binding_sites', sites=2, control='fitted_offset'))
        self.assertAlmostEqual(sq['kd1'] / 1e-3, 1.0, delta=0.3)
        self.assertAlmostEqual(sq['kd2'] / 5e-2, 1.0, delta=0.5)
        self.assertAlmostEqual(sq['dh1'], -30000.0, delta=2000.0)
        CHECKS.append(dict(check='two_sets_sequential', status='passed'))

    def test_dimer_dissociation(self):
        r = Rig(seed=5)
        r.call(r.E, 'interaction.define', interaction_id='dm', model='dimer', receptor='dimer_x', kd_mol_m3=0.1, dh_j_mol=40000.0)
        r.stock('dx', ('hepes', 20.0, 7.5), {'dimer_x': 2.0}, vol=5e-7)
        r.prep('buf', ('hepes', 20.0, 7.5))
        r.method(INJ(19, v=1.5e-9), cell=1e-6, syr=2.0)
        r.load('buf', 'dx')
        r.run('d')
        res = r.an('d', model='dissociation', control='fitted_offset')
        p = pv(res)
        self.assertTrue(res['injections'][1]['heat_j'] > res['injections'][-1]['heat_j'] > 0)      # endothermic dissociation, heats decrease
        self.assertAlmostEqual(p['kd_dimer'] / 0.1, 1.0, delta=0.15)
        self.assertAlmostEqual(p['dh_dissociation'], 40000.0, delta=2000.0)
        CHECKS.append(dict(check='dimer_dissociation', status='passed'))

    def test_enzyme_kinetics_single_and_multiple_injection(self):
        r = Rig(seed=5)
        r.method([dict(volume_m3=1e-8, duration_s=30.0, spacing_s=7200.0)], pref=10.0, cell=3.3e-5, syr=262.0)
        r.prep('inv', ('acetate', 100.0, 5.65), {'invertase': 3.3e-5})
        r.prep('suc', buffer_from='inv', solutes={'sucrose': 262.0})
        r.load('inv', 'suc')
        r.run('sim')
        sim = r.an('sim', model='enzyme_kinetics_single', exclude_injections=[])
        p = pv(sim)
        self.assertAlmostEqual(p['dh'], -14950.0, delta=0.06 * 14950.0)     # IUPAC 2001: sucrose hydrolysis -14.95 kJ/mol (+ sucrose dilution)
        self.assertAlmostEqual(p['km'] / 25.0, 1.0, delta=0.35)
        self.assertAlmostEqual(p['kcat'] / 600.0, 1.0, delta=0.35)
        r2 = Rig(seed=6)
        r2.method([dict(volume_m3=2e-9, duration_s=4.0, spacing_s=240.0)] * 10, cell=1e-6, syr=200.0)
        r2.prep('inv', ('acetate', 100.0, 5.65), {'invertase': 1e-6})
        r2.prep('suc', buffer_from='inv', solutes={'sucrose': 200.0})
        r2.load('inv', 'suc')
        r2.run('mim')
        r2.call(r2.A, 'data.analyze', 'ENZYME_DH_REQUIRED', experiment_id='mim', model='enzyme_kinetics_multiple')
        mim = pv(r2.an('mim', model='enzyme_kinetics_multiple', exclude_injections=[], enzyme_dh_j_mol=-14950.0))
        self.assertAlmostEqual(mim['km'] / 25.0, 1.0, delta=0.35)
        self.assertAlmostEqual(mim['kcat'] / 600.0, 1.0, delta=0.3)
        CHECKS.append(dict(check='enzyme_kinetics', status='passed', sim=p, mim=mim))

    def test_proton_linkage_buffer_ionization_heat(self):
        dh = {}
        for b in ('tris', 'phosphate'):
            r = Rig(seed=9)
            r.call(r.E, 'interaction.define', interaction_id='ph1', model='sites', receptor='p4', ligand='l4', n=1.0, kd_mol_m3=1e-3, dh_j_mol=-20000.0, n_h=1.0)
            r.stock('p4', (b, 50.0, 7.5), {'p4': 0.05})
            r.stock('l4', (b, 50.0, 7.5), {'l4': 0.6}, vol=5e-7)
            r.method(INJ(19), cell=0.05, syr=0.6)
            r.load('p4', 'l4')
            r.run('x')
            dh[b] = pv(r.an('x', control='fitted_offset'))['dh']
        expected = -(47450.0 - 3600.0)                                     # released protons taken up by Tris vs phosphate (Goldberg 2002 dH_ion)
        self.assertAlmostEqual((dh['tris'] - dh['phosphate']) / expected, 1.0, delta=0.12)
        CHECKS.append(dict(check='proton_linkage', status='passed', dh_tris=dh['tris'], dh_phosphate=dh['phosphate']))

    def test_propanol_dilution_heat(self):
        r = Rig(seed=5)
        r.method(INJ(8, sp=120.0), cell=1e-6, syr=100.0)
        r.prep('w', None, None)
        r.call(r.E, 'stock.provide', stock_id='prop', volume_m3=1e-6, buffer=None, components=[dict(species_id='propan_1_ol', concentration_mol_m3=100.0)])
        r.load('w', 'prop')
        r.run('p')
        res = r.an('p', control='none')
        hxx, hxxx = m._dilution_params('propan_1_ol', 298.15)
        expected = -(hxx * 0.1 + hxxx * 0.01) * 2e-9 * 100.0                # to infinite dilution (IUPAC virial coefficients)
        self.assertAlmostEqual(res['injections'][1]['heat_j'] / expected, 1.0, delta=0.08)
        CHECKS.append(dict(check='propanol_dilution', status='passed', heat_ucal=res['injections'][1]['heat_j'] / UC))


class Artifacts(unittest.TestCase):
    def test_buffer_mismatch_and_matched_control(self):
        r = Rig(seed=5)
        r.method(INJ(8), cell=1e-3, syr=1e-3, pref=10.0)
        r.prep('tris', ('tris', 20.0, 8.0), vol=2e-6)
        r.prep('hep', ('hepes', 20.0, 7.0))
        r.prep('tris2', buffer_from='tris')
        r.load('tris', 'hep')
        r.run('mm')
        mis = r.an('mm')
        self.assertLess(mis['injections'][2]['heat_j'] / UC, -50.0)           # HEPES releases protons to Tris
        self.assertIn('Possible Buffer Mismatch', mis['bin_comments'])
        r.clean(cell_method='none', syringe_method='rinse')                  # otherwise the HEPES film left in the syringe contaminates the control
        r.load('tris', 'tris2')
        r.run('ok')
        ok = r.an('ok')
        self.assertLess(max(abs(i['heat_j']) for i in ok['injections'][1:]) / UC, 0.2)
        ctl = r.an('mm', control='experiment', control_experiment_id='ok')
        self.assertAlmostEqual(ctl['injections'][2]['heat_j'], mis['injections'][2]['heat_j'] - ok['injections'][2]['heat_j'], places=12)
        CHECKS.append(dict(check='buffer_mismatch', status='passed'))

    def test_methanol_residue_after_interrupted_drying(self):
        heats = {}
        for stop in (None, 400.0):
            r = Rig(seed=5)
            op = r.clean(cell_method='none', syringe_method='rinse', stop_after=stop)
            self.assertEqual(op['phase'], 'succeeded' if stop is None else 'cancelled')
            self.assertEqual(r.v('truth.syringe_residue'), 'none' if stop is None else 'methanol')
            r.method(INJ(5, sp=120.0), cell=1e-6, syr=1e-6)
            r.prep('w', None, None)
            r.prep('w2', None, None)
            r.load('w', 'w2')
            r.run('ww')
            heats[stop] = [i['heat_j'] for i in r.an('ww')['injections'][1:]]
        self.assertLess(max(abs(h) for h in heats[None]) / UC, 0.1)             # water into water: small blank heats
        self.assertLess(max(heats[400.0]) / UC, -10.0)                         # manual: residual methanol gives consistently large peaks
        CHECKS.append(dict(check='methanol_residue', status='passed'))

    def test_reference_underfill_low_reference_power_spacing_and_poor_load(self):
        r = Rig(seed=5, age=60 * 86400.0)
        r.ca_edta(inj=INJ(10), eid='old_ref')
        self.assertIn('High Initial Baseline', r.an('old_ref', control='fitted_offset')['bin_comments'])
        r.away()
        r.call(r.A, 'reference.refill')
        self.assertAlmostEqual(r.v('truth.reference_volume_m3'), m.ENG['reference_full_m3'])
        r2 = Rig(seed=5)
        r2.ca_edta(inj=INJ(10), eid='lo', pref=0.5)
        self.assertIn('Data Below Minimum', r2.an('lo', control='fitted_offset')['bin_comments'])
        r3 = Rig(seed=5)
        r3.call(r3.A, 'method.set', temperature_k=298.15, reference_power_w=5 * UC, feedback='none', stir_rate_hz=12.5, initial_delay_s=60.0, injections=INJ(12, sp=20.0),
                cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)
        r3.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r3.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r3.load('edta', 'ca')
        r3.run('sp')
        self.assertIn('Injection Spacing Too Short', r3.an('sp', control='fitted_offset')['bin_comments'])
        r4 = Rig(seed=5)
        r4.method(INJ(10))
        r4.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r4.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r4.away()
        res = r4.call(r4.A, 'cell.load', solution_id='edta', volume_m3=1.5e-7)['result']
        self.assertIn('UNDERFILLED', res['warnings'])
        r4.load_syringe('ca')
        r4.move('cell')
        r4.run('uf')
        self.assertIn('Possible Poor Load', r4.an('uf', control='fitted_offset')['bin_comments'])
        CHECKS.append(dict(check='bin_comments', status='passed'))

    def test_outgassing_of_non_degassed_sample(self):
        dev = {}
        for deg in (True, False):
            r = Rig(seed=5)
            r.method(INJ(6, sp=300.0), T=308.15)
            r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1}, degas=deg)
            r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0}, degas=deg)
            r.away()
            res = r.call(r.A, 'cell.load', solution_id='edta', volume_m3=3e-7)['result']
            self.assertEqual('NOT_DEGASSED' in res['warnings'], not deg)
            r.load_syringe('ca')
            r.move('cell')
            r.run('og')
            e = r.R.experiments['og']
            t, dp = np.array(e['t']), np.array(e['dp'])
            quiet = np.zeros(len(t), bool)
            for i in e['injections']:
                quiet |= (t > i['start_s'] + 60) & (t < i['start_s'] + 300)
            x = dp[quiet]
            dev[deg] = float(np.max(np.abs(x - np.median(x))))
        self.assertLess(dev[True], 0.004 * UC)
        self.assertGreater(dev[False], 0.01 * UC)
        CHECKS.append(dict(check='outgassing', status='passed'))

    def test_syringe_bubble_from_short_tube_and_purge(self):
        r = Rig(seed=5)
        r.method(INJ(8))
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.away()
        r.call(r.A, 'cell.load', solution_id='edta', volume_m3=3e-7, rinse=True)
        r.load_syringe('ca', tube=4.5e-8)
        self.assertGreater(r.v('truth.syringe_bubble_m3'), 5e-10)
        r.call(r.A, 'tube.remove')
        r.call(r.A, 'tube.place', solution_id='ca', volume_m3=6e-8)
        r.call(r.A, 'fpa.attach')
        r.move('load')
        op = r.call(r.A, 'syringe.purge_refill')['operation_id']
        r.until(lambda: r.R.operations[op]['phase'] != 'running')
        self.assertEqual(r.v('truth.syringe_bubble_m3'), 0.0)
        r.move('rest')
        r.call(r.A, 'fpa.detach')
        r.move('cell')
        r.run('b')
        vols = r.R.experiments['b']['truth']['volumes']
        self.assertAlmostEqual(vols[1] / 2e-9, 1.0, delta=0.02)
        CHECKS.append(dict(check='syringe_bubble_purge', status='passed'))

    def test_carryover_rinse_and_cleaning(self):
        r = Rig(seed=5)
        r.ca_edta(inj=INJ(5), eid='a')
        r.away()
        r.call(r.A, 'cell.load', solution_id='edta', volume_m3=3e-7, rinse=False)
        no_rinse = r.v('truth.cell_contamination_fraction')
        r.call(r.A, 'cell.load', solution_id='edta', volume_m3=3e-7, rinse=True)
        self.assertAlmostEqual(no_rinse, m.ENG['cell_residual_m3'] / m.V0)
        self.assertAlmostEqual(r.v('truth.cell_contamination_fraction'), no_rinse * m.ENG['rinse_residual_factor'])
        # the syringe keeps a film of the previous titrant unless it is cleaned
        r.prep('w', None, None)
        r.load_syringe('w')
        self.assertGreater(r.v('truth.syringe_species_mol_m3').get('ca2+', 0.0), 0.01)
        r.clean(cell_method='wash', syringe_method='rinse')
        r.call(r.A, 'cleaning_tool.remove')
        r.load_syringe('w')
        self.assertLess(r.v('truth.syringe_species_mol_m3').get('ca2+', 0.0), 1e-3)
        CHECKS.append(dict(check='carryover', status='passed'))

    def test_wash_module_bottles_overflow_and_no_flow(self):
        r = Rig(seed=5, warm=10.0)
        codes = []
        for _ in range(60):
            op = r.clean(cell_method='wash', syringe_method='none')
            if op['phase'] == 'failed':
                codes.append(op['error'])
                if op['error'] == 'NO_FLOW':
                    r.call(r.A, 'wash.refill', bottle='water')
                else:
                    break
        self.assertIn('NO_FLOW', codes)
        self.assertEqual(codes[-1], 'WASH_OVERFLOW')
        self.assertFalse(r.v('wash.pump_ok'))
        self.assertIn('WASH_PUMP_SHUTDOWN', r.v('errors.active'))
        r.call(r.A, 'clean.start', 'WASH_PUMP_SHUTDOWN', cell_method='wash', syringe_method='none')
        r.call(r.A, 'wash.empty_waste')
        r.call(r.A, 'wash.empty_overflow')
        self.assertTrue(r.v('wash.pump_ok'))
        led = r.call(r.E, 'ledger.read')['result']['ledger']
        self.assertAlmostEqual(led['waste_added_m3'] + led['overflow_added_m3'], led['water_used_m3'] + led['detergent_used_m3'] + led['methanol_used_m3']
                               + sum(0 for _ in ()), delta=1e-6)
        CHECKS.append(dict(check='wash_module', status='passed', failures=codes))

    def test_power_loss_interrupts_run(self):
        r = Rig(seed=5)
        r.method(INJ(10))
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.load('edta', 'ca')
        op = r.call(r.A, 'run.start', experiment_id='p')['operation_id']
        r.until(lambda: r.R.run is not None and r.R.run['inj'] >= 2)
        r.call(r.E, 'fault.inject', fault='power_loss', active=True)
        self.assertEqual(r.A.poll(op)['status'], 'failed')
        self.assertEqual(r.A.poll(op)['result']['error'], 'POWER_OFF_INTERRUPT')
        self.assertIsNone(r.v('dp.power_w'))
        self.assertFalse(r.v('lamp.status'))
        r.call(r.E, 'fault.inject', fault='power_loss', active=False)
        self.assertTrue(r.v('power.enabled'))
        CHECKS.append(dict(check='power_loss', status='passed'))


class ReviewRound1(unittest.TestCase):
    """Regression tests for the round-1 runtime review (F-01 / F-02 / F-03 / F-05 / F-06 / F-07)."""

    def _ramp(self, rest, kon=None, seed=21):
        r = Rig(seed=seed, warm=1800.0)
        if kon:
            r.call(r.E, 'interaction.define', interaction_id='slow', model='sites', receptor='p_s', ligand='l_s', n=1.0, kd_mol_m3=1e-3, dh_j_mol=-40000.0,
                   kon_m3_mol_s=kon)
            r.stock('cell', ('tris', 50.0, 8.0), {'p_s': 0.05})
            r.stock('syr', ('tris', 50.0, 8.0), {'l_s': 0.5}, vol=5e-7)
            cell, syr = 'cell', 'syr'
        else:
            r.prep('cell', ('tris', 50.0, 8.0))
            r.prep('syr', buffer_from='cell')
            cell, syr = 'cell', 'syr'
        r.load(cell, syr)
        if rest:
            r.move('rest')
        h0 = r.R.ledger()['chemical_heat_j']
        r.call(r.A, 'temperature.set', temperature_k=318.15)
        r.step(3600)
        return r, r.R.ledger()['chemical_heat_j'] - h0

    def test_temperature_ramp_books_no_reaction_heat(self):
        _, in_cell = self._ramp(rest=False)
        _, at_rest = self._ramp(rest=True)
        self.assertLess(abs(in_cell) / UC, 0.05)                    # F-01: the stored-enthalpy change with temperature is sensible heat
        self.assertLess(abs(at_rest) / UC, 0.05)
        CHECKS.append(dict(check='ramp_no_reaction_heat', status='passed', in_cell_ucal=in_cell / UC, at_rest_ucal=at_rest / UC))

    def test_slow_binding_heat_bounded_during_warm_up(self):
        r, heat = self._ramp(rest=False, kon=1.0)
        leaked = r.R.syr['leak']                                     # at most the leaked ligand can bind: 0.5 mol/m3 x leak x 40 kJ/mol
        bound = 0.5 * leaked * 40000.0
        self.assertLess(abs(heat), bound * 1.05 + 0.05 * UC)         # F-02: no heat from nothing while the temperature moves
        CHECKS.append(dict(check='slow_binding_bounded', status='passed', heat_ucal=heat / UC, bound_ucal=bound / UC))

    def test_equilibrium_follows_temperature_without_mixing(self):
        r = Rig(seed=22, warm=1800.0)
        r.prep('crown', None, {'18_crown_6': 5.0})
        r.prep('ba', None, {'ba2+': 5.0})
        r.prep('mix', None, None, vol=1e-6)
        r.call(r.A, 'solution.prepare', solution_id='cb', volume_m3=1e-6, sources=[dict(source_id='crown', volume_m3=5e-7), dict(source_id='ba', volume_m3=5e-7)])
        r.away()
        r.call(r.A, 'cell.load', solution_id='cb', volume_m3=3e-7)
        r.step(1800)
        b298 = r.v('truth.cell_bound_mol_m3')['ba_18crown6']
        r.call(r.A, 'temperature.set', temperature_k=318.15)
        r.step(3600)
        b318 = r.v('truth.cell_bound_mol_m3')['ba_18crown6']
        ka, _ = m._k_at(1 / 5900.0, -31420.0, 126.0, 298.15, r.v('truth.cell_content_temperature_k'))
        sp = r.v('truth.cell_species_mol_m3')
        Mt, Lt = sp['18_crown_6'] / 1000.0, sp['ba2+'] / 1000.0
        b = Mt + Lt + 1 / ka
        eq = (b - math.sqrt(b * b - 4 * Mt * Lt)) / 2 * 1000.0
        self.assertLess(b318, b298)                                  # F-03: exothermic binding weakens at 318 K and the cell follows
        self.assertAlmostEqual(b318 / eq, 1.0, delta=0.01)
        CHECKS.append(dict(check='equilibrium_follows_temperature', status='passed', bound_298=b298, bound_318=b318, eq_318=eq))

    def test_passive_feedback_first_injection_not_distorted(self):
        heats = {}
        for fb in ('high', 'none'):
            r = Rig(seed=23)
            inj = [dict(volume_m3=4e-10, duration_s=0.8, spacing_s=400.0)] + [dict(volume_m3=2e-9, duration_s=4.0, spacing_s=400.0)] * 2
            r.method(inj, fb=fb)
            r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
            r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
            r.load('edta', 'ca')
            r.run('e')
            heats[fb] = [i['heat_j'] for i in r.an('e', exclude_injections=[])['injections']]
        self.assertAlmostEqual(heats['none'][0] / heats['high'][0], 1.0, delta=0.15)     # F-05: the stirring-start transient has settled
        self.assertAlmostEqual(heats['none'][1] / heats['high'][1], 1.0, delta=0.05)
        CHECKS.append(dict(check='passive_first_injection', status='passed', ratio=heats['none'][0] / heats['high'][0]))

    def test_syringe_bubble_conserves_volume_and_injects_air_last(self):
        r = Rig(seed=24)
        r.method(INJ(18))
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.away()
        r.call(r.A, 'cell.load', solution_id='edta', volume_m3=3e-7, rinse=True)
        r.load_syringe('ca', tube=3.0e-8)
        liquid, bubble = r.v('truth.syringe_liquid_m3'), r.v('truth.syringe_bubble_m3')
        self.assertAlmostEqual(liquid + bubble, m.V_SYR, delta=1e-12)
        r.move('cell')
        r.run('b')
        vols = r.R.experiments['b']['truth']['volumes']
        self.assertLessEqual(sum(vols), liquid + 1e-12)                         # F-06: no more liquid delivered than loaded
        self.assertAlmostEqual(vols[1] / 2e-9, 1.0, delta=0.05)                 # delivery follows the plunger while liquid remains
        self.assertTrue(r.v('truth.cell_bubble'))                               # the air is injected last (cell bubble)
        left = r.v('truth.syringe_liquid_m3') + r.v('truth.syringe_bubble_m3')
        cap = (1 - r.v('syringe.plunger_position_m') / m.PLUNGER_STROKE) * m.V_SYR
        self.assertLessEqual(left, cap + 1e-12)
        res = r.an('b', control='fitted_offset')
        self.assertTrue({'Possible Poor Load', 'Check Baseline'} & set(res['bin_comments']) or res['bin'] == 'Check data', res['bin_comments'])
        CHECKS.append(dict(check='bubble_conservation', status='passed', delivered_m3=sum(vols), liquid_m3=liquid))

    def test_ba_crown_with_control_subtraction(self):
        dh = {}
        for T in (288.15, 308.15):
            r = Rig(seed=25)
            inj = [dict(volume_m3=4e-10, duration_s=2.0, spacing_s=180.0)] + [dict(volume_m3=1.5e-9, duration_s=8.0, spacing_s=180.0)] * 24
            r.method(inj, T=T, pref=10.0, cell=1.0, syr=10.0)
            r.prep('crown', None, {'18_crown_6': 1.0})
            r.prep('ba', None, {'ba2+': 10.0}, vol=2e-6)
            r.prep('w', None, None)
            r.step(2400)
            r.load('w', 'ba')
            r.run('ctl')
            r.clean(cell_method='rinse', syringe_method='rinse')
            r.call(r.A, 'cleaning_tool.remove')
            r.load('crown', 'ba')
            r.run('b')
            dh[T] = pv(r.an('b', control='experiment', control_experiment_id='ctl'))['dh']
        dcp = (dh[308.15] - dh[288.15]) / 20.0
        self.assertTrue(95.0 < dcp < 165.0, dcp)                                # F-07: IUPAC 126 J/K/mol once the BaCl2 dilution is subtracted
        CHECKS.append(dict(check='ba_crown_control_dcp', status='passed', dcp_j_k_mol=dcp))


class ReviewRound2(unittest.TestCase):
    """Regression tests for the round-2 runtime review (F2-01 / F2-02 / F2-03 / F2-05 / F2-06 / F2-07 / F2-08)."""

    def test_fix_bounds_and_models(self):
        r = Rig(seed=31)
        r.ca_edta(inj=INJ(8), eid='e')
        r.call(r.A, 'data.analyze', 'INVALID_ARGUMENT', experiment_id='e', model='one_set_of_sites', fix=dict(kd=0.0))          # F2-01: schema bounds
        r.call(r.A, 'data.analyze', 'INVALID_ARGUMENT', experiment_id='e', model='two_sets_of_sites', fix=dict(kd2=-1.0))
        r.call(r.A, 'data.analyze', 'INVALID_ARGUMENT', experiment_id='e', model='sequential_binding_sites', fix=dict(dh=-1e4))    # fix not applied there
        r.call(r.A, 'data.analyze', 'INVALID_ARGUMENT', experiment_id='e', model='one_set_of_sites', fix=dict(n2=1.0))
        p = pv(r.an('e', fix=dict(n=1.0), control='fitted_offset'))
        self.assertEqual(p['n'], 1.0)
        CHECKS.append(dict(check='fix_bounds', status='passed'))

    def test_temperature_set_effects_declared_with_a_complex(self):
        r = Rig(seed=32)
        r.method(INJ(4))
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.call(r.A, 'solution.prepare', solution_id='mix', volume_m3=1e-6, sources=[dict(source_id='edta', volume_m3=5e-7), dict(source_id='ca', volume_m3=5e-8)])
        r.away()
        r.call(r.A, 'cell.load', solution_id='mix', volume_m3=3e-7)
        r.step(1800)
        ZDeclaredEffects._check(self, r, 'temperature.set', dict(temperature_k=310.15))          # F2-07: pH and bound amounts follow the temperature
        CHECKS.append(dict(check='temperature_set_declared', status='passed'))

    def test_kinetic_complex_relaxes_after_a_temperature_change(self):
        r = Rig(seed=33, warm=1800.0)
        r.call(r.E, 'interaction.define', interaction_id='slow2', model='sites', receptor='p_k', ligand='l_k', n=1.0, kd_mol_m3=1e-3, dh_j_mol=-40000.0,
               kon_m3_mol_s=1.0, temperature_k=298.15)
        r.stock('pk', ('hepes', 20.0, 7.5), {'p_k': 0.05, 'l_k': 0.05})
        r.away()
        r.call(r.A, 'cell.load', solution_id='pk', volume_m3=3e-7)
        r.step(3600)
        b0 = r.v('truth.cell_bound_mol_m3')['slow2']
        r.call(r.A, 'temperature.set', temperature_k=313.15)
        r.step(3600)
        b1 = r.v('truth.cell_bound_mol_m3')['slow2']
        info = r.R.cell['eq']
        self.assertLess(b1, b0)                                                       # F2-03: the slow complex relaxes at rest
        self.assertAlmostEqual(b1 / (info['kin_eq']['slow2'] * 1000.0), 1.0, delta=0.02)
        CHECKS.append(dict(check='kinetic_relaxation_after_ramp', status='passed', bound_298=b0, bound_313=b1))

    def test_bubble_keeps_the_programmed_spacing(self):
        r = Rig(seed=34)
        r.method(INJ(8, sp=40.0))
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.away()
        r.call(r.A, 'cell.load', solution_id='edta', volume_m3=3e-7, rinse=True)
        r.load_syringe('ca', tube=3.0e-8)                                           # ~11 uL of air in the syringe
        self.assertGreater(r.v('truth.syringe_bubble_m3'), 5e-9)
        r.move('cell')
        r.run('b')
        st = [i['start_s'] for i in r.R.experiments['b']['injections']]
        self.assertTrue(all(abs((b - a) - 40.0) < 1e-6 for a, b in zip(st, st[1:])), st)   # F2-06
        CHECKS.append(dict(check='bubble_spacing', status='passed'))

    def test_passive_feedback_short_spacing_flagged(self):
        r = Rig(seed=35)
        r.call(r.A, 'method.set', temperature_k=298.15, reference_power_w=5 * UC, feedback='none', stir_rate_hz=12.5, initial_delay_s=60.0, injections=INJ(12, sp=150.0),
               cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.load('edta', 'ca')
        r.run('n')
        self.assertIn('Injection Spacing Too Short', r.an('n', control='fitted_offset')['bin_comments'])          # F2-08: 2.5 response times
        CHECKS.append(dict(check='passive_spacing_flag', status='passed'))

    def test_first_injection_tip_heat_booked(self):
        r = Rig(seed=36)
        r.ca_edta(inj=INJ(4), eid='t')
        tr = r.R.experiments['t']['truth']
        led = r.R.ledger()['chemical_heat_j']
        self.assertAlmostEqual(sum(tr['heat_j']) / led, 1.0, delta=0.05)              # F2-02: heats per injection add up to the booked chemical heat
        self.assertGreater(tr['heat_j'][0], 0.0)
        CHECKS.append(dict(check='tip_heat', status='passed'))


class ReviewRound3(unittest.TestCase):
    """Regression tests for the round-3 runtime review (F3-01..F3-05, F2-02 kinetic slug)."""

    def test_clean_dilution_run_has_no_poor_load_and_aligned_starts(self):
        r = Rig(seed=41)
        r.method(INJ(12, sp=120.0), cell=1e-6, syr=100.0)
        r.prep('w', None, None)
        r.call(r.E, 'stock.provide', stock_id='prop', volume_m3=1e-6, buffer=None, components=[dict(species_id='propan_1_ol', concentration_mol_m3=100.0)])
        r.load('w', 'prop')
        r.run('p')
        st = [i['start_s'] for i in r.R.experiments['p']['injections']]
        self.assertTrue(all(x == round(x, 6) for x in st), st)                         # F3-01: on the 1e-6 s grid of the samples
        self.assertNotIn('Possible Poor Load', r.an('p')['bin_comments'])
        CHECKS.append(dict(check='aligned_starts_no_false_poor_load', status='passed'))

    def test_passive_long_spacing_not_flagged(self):
        r = Rig(seed=42)
        r.call(r.A, 'method.set', temperature_k=298.15, reference_power_w=5 * UC, feedback='none', stir_rate_hz=12.5, initial_delay_s=60.0, injections=INJ(18, sp=300.0),
               cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.load('edta', 'ca')
        r.run('n')
        self.assertNotIn('Injection Spacing Too Short', r.an('n', control='fitted_offset')['bin_comments'])      # F3-02: 5 response times
        CHECKS.append(dict(check='passive_long_spacing', status='passed'))

    def test_fix_kd_range_and_offset_control(self):
        r = Rig(seed=43)
        r.ca_edta(inj=INJ(8), eid='e')
        r.call(r.A, 'data.analyze', 'INVALID_ARGUMENT', experiment_id='e', model='one_set_of_sites', fix=dict(kd=5e-12))     # F3-03
        r.call(r.A, 'data.analyze', 'INVALID_ARGUMENT', experiment_id='e', model='one_set_of_sites', fix=dict(offset=-5000.0), control='none')   # F3-04
        p = pv(r.an('e', fix=dict(offset=-5000.0), control='fitted_offset'))
        self.assertEqual(p['offset'], -5000.0)
        CHECKS.append(dict(check='fix_kd_offset', status='passed'))

    def test_bubble_volume_expelled_at_the_run_end(self):
        r = Rig(seed=44)
        inj = [dict(volume_m3=4e-10, duration_s=0.8, spacing_s=60.0)] + [dict(volume_m3=2e-9, duration_s=4.0, spacing_s=60.0)] * 7 + [dict(volume_m3=2e-9, duration_s=4.0, spacing_s=4.0)]
        r.method(inj)
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.away()
        r.call(r.A, 'cell.load', solution_id='edta', volume_m3=3e-7, rinse=True)
        r.load_syringe('ca', tube=3.0e-8)
        r.move('cell')
        r.run('b')
        left = r.v('truth.syringe_liquid_m3') + r.v('truth.syringe_bubble_m3')
        cap = (1 - r.v('syringe.plunger_position_m') / m.PLUNGER_STROKE) * m.V_SYR
        self.assertLessEqual(left, cap + 1e-12)                                         # F3-05: nothing left beyond the free volume
        self.assertAlmostEqual(r.R.experiments['b']['truth']['volumes'][-1] / 2e-9, 1.0, delta=0.02)
        CHECKS.append(dict(check='bubble_flush_at_end', status='passed'))

    def test_preformed_slow_complex_in_the_syringe_only_dissociates(self):
        heats = {}
        for kon in (None, 0.1):
            r = Rig(seed=45)
            a = dict(interaction_id='pc', model='sites', receptor='p_c', ligand='l_c', n=1.0, kd_mol_m3=5e-3, dh_j_mol=-40000.0)
            if kon:
                a['kon_m3_mol_s'] = kon
            r.call(r.E, 'interaction.define', **a)
            r.stock('cell', ('hepes', 20.0, 7.5), {})
            r.stock('syr', ('hepes', 20.0, 7.5), {'p_c': 0.2, 'l_c': 0.2}, vol=5e-7)
            r.method(INJ(4, sp=600.0), cell=1e-6, syr=0.2)
            r.load('cell', 'syr')
            r.run('d')
            heats[kon] = [i['heat_j'] for i in r.an('d', exclude_injections=[])['injections']]
        self.assertTrue(all(h > 0 for h in heats[None][1:]))                           # dilution of the complex: endothermic dissociation
        self.assertTrue(all(h > -0.02 * UC for h in heats[0.1][1:]), heats[0.1])         # F2-02: the slow complex only dissociates (no exothermic re-formation)
        self.assertLessEqual(sum(heats[0.1][1:]), sum(heats[None][1:]) * 1.05)
        CHECKS.append(dict(check='slow_complex_dissociation', status='passed', instant=heats[None], slow=heats[0.1]))


# ---------------------------------------------------------------- workflow preconditions
class Workflow(unittest.TestCase):
    def test_load_workflow_preconditions(self):
        r = Rig(seed=5, warm=10.0)
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0}, vol=5e-7)
        r.call(r.A, 'cell.load', 'PIPETTE_IN_CELL', solution_id='edta', volume_m3=3e-7)
        r.call(r.A, 'fpa.attach', 'PIPETTE_IN_CELL')
        r.call(r.A, 'run.start', 'NO_METHOD', experiment_id='x')
        r.method(INJ(4))
        r.call(r.A, 'run.start', 'CELL_EMPTY' if r.R.cell['mix']['V'] <= 0 else 'SYRINGE_NOT_LOADED', experiment_id='x')
        r.move('rest')
        r.call(r.A, 'fpa.detach', 'FPA_NOT_ATTACHED')
        r.call(r.A, 'fpa.attach')
        r.call(r.A, 'fpa.attach', 'FPA_ATTACHED')
        r.call(r.A, 'pipette.move', 'FPA_ATTACHED', location='cell')
        r.call(r.A, 'syringe.load', 'PLUNGER_NOT_DOWN')
        r.call(r.A, 'syringe.plunger_down')
        r.call(r.A, 'pipette.move', 'SYRINGE_BUSY', location='load')
        r.until(lambda: r.R.plunger_target is None)
        r.call(r.A, 'syringe.load', 'PIPETTE_NOT_AT_LOAD')
        r.call(r.A, 'pipette.move', location='load')
        r.call(r.A, 'pipette.move', 'PIPETTE_MOVING', location='rest')
        r.until(lambda: r.R.pip['phase'] is None)
        r.call(r.A, 'syringe.load', 'NO_TUBE')
        r.call(r.A, 'tube.place', 'PIPETTE_IN_STATION', solution_id='ca', volume_m3=6e-8)
        r.move('rest')
        r.call(r.A, 'tube.remove', 'NO_TUBE')
        r.call(r.A, 'tube.place', solution_id='ca', volume_m3=6e-8)
        r.call(r.A, 'tube.place', 'TUBE_PRESENT', solution_id='ca', volume_m3=6e-8)
        r.move('load')
        r.call(r.A, 'tube.remove', 'PIPETTE_IN_STATION')
        r.call(r.A, 'syringe.purge_refill', 'SYRINGE_NOT_LOADED')
        r.call(r.A, 'wash.power_set', enabled=False)
        r.call(r.A, 'syringe.load', 'WASH_MODULE_OFF')
        r.call(r.A, 'wash.press_switch')
        op = r.call(r.A, 'syringe.load')['operation_id']
        r.call(r.A, 'cell.load', 'INSTRUMENT_BUSY', solution_id='edta', volume_m3=3e-7)
        r.call(r.A, 'wash.empty_waste', 'INSTRUMENT_BUSY')
        r.call(r.A, 'fpa.detach', 'INSTRUMENT_BUSY')
        r.until(lambda: r.R.operations[op]['phase'] != 'running')
        self.assertEqual(r.v('syringe.state'), 'loaded')
        r.call(r.A, 'cleaning_tool.insert')
        r.call(r.A, 'cleaning_tool.insert', 'CLEANING_TOOL_INSERTED')
        r.call(r.A, 'cell.load', 'CLEANING_TOOL_INSERTED', solution_id='edta', volume_m3=3e-7)
        r.call(r.A, 'clean.start', 'PIPETTE_NOT_AT_CLEAN', cell_method='none', syringe_method='rinse')
        r.call(r.A, 'clean.start', 'NOTHING_TO_CLEAN', cell_method='none', syringe_method='none')
        r.call(r.A, 'cleaning_tool.remove')
        r.call(r.A, 'cleaning_tool.remove', 'CLEANING_TOOL_NOT_INSERTED')
        r.call(r.A, 'clean.start', 'CLEANING_TOOL_NOT_INSERTED', cell_method='wash', syringe_method='none')
        r.call(r.A, 'cell.load', 'INSUFFICIENT_SOLUTION', solution_id='ca', volume_m3=4e-7, rinse=True)
        r.call(r.A, 'cell.load', solution_id='edta', volume_m3=3e-7, rinse=True)
        r.move('rest')
        r.call(r.A, 'fpa.detach')
        r.call(r.A, 'cleaning_tool.insert')
        r.call(r.A, 'pipette.move', 'CLEANING_TOOL_INSERTED', location='cell')
        r.call(r.A, 'cleaning_tool.remove')
        r.call(r.A, 'run.start', 'PIPETTE_NOT_IN_CELL', experiment_id='x')
        r.move('cell')
        r.call(r.A, 'run.start', experiment_id='x')
        r.call(r.A, 'run.start', 'RUN_IN_PROGRESS', experiment_id='y')
        r.call(r.A, 'method.set', 'RUN_IN_PROGRESS', temperature_k=298.15, reference_power_w=5 * UC, feedback='high', stir_rate_hz=12.5, initial_delay_s=60.0,
               injections=INJ(4), cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)
        r.call(r.A, 'temperature.set', 'RUN_IN_PROGRESS', temperature_k=300.0)
        r.call(r.A, 'pipette.move', 'RUN_IN_PROGRESS', location='rest')
        r.call(r.A, 'reference.refill', 'RUN_IN_PROGRESS')
        r.call(r.A, 'syringe.plunger_down', 'RUN_IN_PROGRESS')
        r.call(r.A, 'clean.start', 'RUN_IN_PROGRESS', cell_method='wash', syringe_method='none')
        r.call(r.A, 'cell.empty', 'RUN_IN_PROGRESS')
        r.call(r.A, 'cleaning_tool.insert', 'RUN_IN_PROGRESS')
        r.call(r.A, 'fpa.attach', 'RUN_IN_PROGRESS')
        out = r.call(r.A, 'instrument.stop')['result']
        self.assertIn('run.start', out['stopped'])
        r.call(r.A, 'run.start', 'ITEM_EXISTS', experiment_id='x')
        r.call(r.A, 'data.analyze', 'NO_DATA', experiment_id='x', model='one_set_of_sites')
        r.call(r.A, 'data.analyze', 'UNKNOWN_EXPERIMENT', experiment_id='nope', model='one_set_of_sites')
        r.call(r.A, 'data.read', 'UNKNOWN_EXPERIMENT', experiment_id='nope')
        r.call(r.A, 'method.set', 'METHOD_EXCEEDS_SYRINGE', temperature_k=298.15, reference_power_w=5 * UC, feedback='high', stir_rate_hz=12.5, initial_delay_s=60.0,
               injections=[dict(volume_m3=2e-9, duration_s=4.0, spacing_s=150.0)] * 20, cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)
        r.call(r.A, 'method.set', 'INVALID_SPACING', temperature_k=298.15, reference_power_w=5 * UC, feedback='high', stir_rate_hz=12.5, initial_delay_s=60.0,
               injections=[dict(volume_m3=2e-9, duration_s=40.0, spacing_s=20.0)], cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)
        r.call(r.A, 'autosampler.run_queue', 'UNSUPPORTED')
        r.call(r.A, 'cell.auto_fill', 'UNSUPPORTED')
        r.call(r.A, 'dsc.temperature_scan', 'UNSUPPORTED')
        CHECKS.append(dict(check='load_workflow_preconditions', status='passed'))

    def test_solutions_species_and_dialysis(self):
        r = Rig(seed=5, warm=10.0)
        r.call(r.A, 'solution.prepare', 'UNKNOWN_SPECIES', solution_id='x', volume_m3=1e-6, solutes=[dict(species_id='mes', concentration_mol_m3=1.0)])
        r.call(r.A, 'solution.prepare', 'UNKNOWN_SPECIES', solution_id='x', volume_m3=1e-6, solutes=[dict(species_id='unobtainium', concentration_mol_m3=1.0)])
        r.call(r.A, 'solution.prepare', 'UNKNOWN_SOLUTION', solution_id='x', volume_m3=1e-6, buffer_from='nope')
        r.call(r.A, 'solution.prepare', 'INVALID_ARGUMENT', solution_id='x', volume_m3=1e-6, buffer_from='nope',
               buffer=dict(buffer_id='mes', concentration_mol_m3=10.0, ph=5.6))
        r.prep('a', ('tris', 20.0, 8.0), {'nacl': 150.0})
        r.call(r.A, 'solution.prepare', 'ITEM_EXISTS', solution_id='a', volume_m3=1e-6)
        r.call(r.A, 'solution.prepare', 'VOLUME_OUT_OF_RANGE', solution_id='b', volume_m3=1e-7, sources=[dict(source_id='a', volume_m3=2e-7)])
        r.call(r.A, 'solution.prepare', 'INSUFFICIENT_SOLUTION', solution_id='b', volume_m3=5e-6, sources=[dict(source_id='a', volume_m3=3e-6)])
        r.call(r.E, 'stock.provide', stock_id='prot', volume_m3=2e-6, buffer=dict(buffer_id='phosphate', concentration_mol_m3=50.0, ph=7.0),
               components=[dict(species_id='rnase_a', concentration_mol_m3=0.19)], label=[dict(species_id='rnase_a', concentration_mol_m3=0.2)])
        r.call(r.E, 'stock.provide', 'ITEM_EXISTS', stock_id='prot', volume_m3=1e-6, buffer=None, components=[])
        r.call(r.E, 'stock.provide', 'UNKNOWN_SPECIES', stock_id='p9', volume_m3=1e-6, buffer=None, components=[dict(species_id='tris', concentration_mol_m3=1.0)])
        out = r.call(r.A, 'solution.dialyze', solution_id='prot', buffer=dict(buffer_id='acetate', concentration_mol_m3=200.0, ph=5.5, nacl_mol_m3=200.0),
                     dialysate_id='dial')['result']
        self.assertEqual(out['dialysate_id'], 'dial')
        s = r.R.solutions['prot']['c']
        self.assertEqual(set(s['buf']), {'acetate'})                                 # buffer exchanged, protein kept
        self.assertAlmostEqual(s['n']['rnase_a'] / s['V'], 0.19, places=9)
        r.call(r.A, 'solution.dialyze', 'ITEM_EXISTS', solution_id='prot', buffer=dict(buffer_id='acetate', concentration_mol_m3=200.0, ph=5.5), dialysate_id='dial')
        r.call(r.A, 'solution.degas', 'UNKNOWN_SOLUTION', solution_id='nope')
        lig = r.prep('cmp', buffer_from='dial', solutes={'2_cmp': 2.0})
        self.assertEqual(lig['ph_nominal'], 5.5)
        r.call(r.E, 'interaction.define', 'INVALID_INTERACTION', interaction_id='bad', model='sites', receptor='x1')
        r.call(r.E, 'interaction.define', 'INVALID_INTERACTION', interaction_id='bad', model='sites', receptor='tris', ligand='x2', kd_mol_m3=1.0)
        r.call(r.E, 'interaction.define', 'ITEM_EXISTS', interaction_id='ca_edta_mes_ph56', model='dimer', receptor='x3', kd_mol_m3=1.0)
        prov = r.call(r.A, 'provenance.read')['result']
        self.assertIn('ca_edta_mes_ph56', [i['interaction_id'] for i in prov['interactions']])
        self.assertGreaterEqual(len(prov['shelf_species']), 80)
        self.assertEqual(r.call(r.A, 'experiments.list')['result']['experiments'], [])
        CHECKS.append(dict(check='solutions_dialysis', status='passed'))


# ---------------------------------------------------------------- framework
class Framework(unittest.TestCase):
    def test_sessions_isolation_and_idempotency(self):
        r = Rig(seed=5)
        A, B, E = r.A, r.B, r.E
        r.call(A, 'fault.inject', 'ACCESS_DENIED', fault='cell_bubble', active=True)
        r.call(A, 'stock.provide', 'ACCESS_DENIED', stock_id='s', volume_m3=1e-6, buffer=None, components=[])
        with self.assertRaises(m.ActionRejected):
            A.read(instance_id=r.I, state_ids=['truth.cell_ph'])
        r.method(INJ(6))
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.call(B, 'solution.degas', 'ACCESS_DENIED', solution_id='edta')
        self.assertNotIn('edta', B.read(instance_id=r.I, state_ids=['solutions.available'])['observations']['solutions.available']['value'])
        r.load('edta', 'ca')
        op = r.call(A, 'run.start', experiment_id='mine')['operation_id']
        r.step(5)
        r.call(B, 'instrument.stop', 'ACCESS_DENIED')
        self.assertTrue(B.read(instance_id=r.I, state_ids=['operation.started_by_other_agent'])['observations']['operation.started_by_other_agent']['value'])
        with self.assertRaises(m.ActionRejected):
            B.poll(op)
        r.until(lambda: r.R.operations[op]['phase'] != 'running', dt=2.0)
        r.call(B, 'data.read', 'ACCESS_DENIED', experiment_id='mine')
        r.call(B, 'data.analyze', 'ACCESS_DENIED', experiment_id='mine', model='one_set_of_sites')
        self.assertEqual(r.call(B, 'experiments.list')['result']['experiments'], [])
        self.assertEqual(len(r.call(A, 'experiments.list')['result']['experiments']), 1)
        d = r.call(A, 'data.read', experiment_id='mine', max_points=200)['result']
        self.assertLessEqual(len(d['dp_w']), 200)
        self.assertTrue(d['completed'])
        ev = r.call(B, 'events.read', since_s=0.0)['result']['events']
        self.assertFalse(any(e['kind'] == 'fault' for e in ev))
        r1 = A.invoke(instance_id=r.I, capability_id='design.simulate', arguments=dict(n=1.0, kd_mol_m3=1e-3, dh_j_mol=-1e4, cell_concentration_mol_m3=0.1,
                                                                                      syringe_concentration_mol_m3=1.0, injection_volumes_m3=[2e-9] * 5), request_id='same')
        r2 = A.invoke(instance_id=r.I, capability_id='design.simulate', arguments=dict(n=1.0, kd_mol_m3=1e-3, dh_j_mol=-1e4, cell_concentration_mol_m3=0.1,
                                                                                      syringe_concentration_mol_m3=1.0, injection_volumes_m3=[2e-9] * 5), request_id='same')
        self.assertEqual(r1, r2)
        r3 = A.invoke(instance_id=r.I, capability_id='design.simulate', arguments=dict(n=2.0, kd_mol_m3=1e-3, dh_j_mol=-1e4, cell_concentration_mol_m3=0.1,
                                                                                      syringe_concentration_mol_m3=1.0, injection_volumes_m3=[2e-9] * 5), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')
        SEEN.add(('design.simulate', 'REQUEST_CONFLICT'))
        with self.assertRaises(m.ActionRejected):
            A.write(instance_id=r.I, state_id='dp.power_w', value=1.0, request_id='w')
        self.assertEqual(A.describe(instance_id=r.I)['asset_type_id'], m.ASSET_TYPE_ID)
        self.assertNotIn('truth.cell_ph', A.describe(asset_type_id=m.ASSET_TYPE_ID)['state_slots'])
        CHECKS.append(dict(check='sessions', status='passed'))

    def test_cancel_operations(self):
        r = Rig(seed=5)
        r.method(INJ(6))
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        r.load('edta', 'ca')
        op = r.call(r.A, 'run.start', experiment_id='c')['operation_id']
        r.step(10)
        out = r.A.cancel(op, 'cx')
        self.assertEqual(out['result']['phase'], 'cancelled')
        self.assertEqual(r.R.experiments['c']['state'], 'stopped')
        with self.assertRaises(m.ActionRejected):
            r.A.cancel(op, 'cy')
        CHECKS.append(dict(check='cancel', status='passed'))

    def test_atomic_rejection(self):
        r = Rig(seed=5, warm=10.0)
        r.prep('a', ('mes', 10.0, 5.6), {'edta': 0.1})
        before = copy.deepcopy(r.R.read('evaluator'))
        sol = copy.deepcopy(r.R.solutions)
        r.call(r.A, 'solution.prepare', 'INSUFFICIENT_SOLUTION', solution_id='b', volume_m3=5e-6, sources=[dict(source_id='a', volume_m3=2e-6)])
        r.call(r.A, 'cell.load', 'PIPETTE_IN_CELL', solution_id='a', volume_m3=3e-7)
        r.call(r.A, 'syringe.load', 'PLUNGER_NOT_DOWN')
        self.assertEqual(r.R.read('evaluator'), before)
        self.assertEqual(json.dumps(r.R.solutions, sort_keys=True, default=str), json.dumps(sol, sort_keys=True, default=str))
        CHECKS.append(dict(check='atomic_rejection', status='passed'))

    def test_step_partition_independence(self):
        out = []
        for parts in ([90.0], [0.5] * 180, [2.0, 0.25, 0.25, 7.5, 80.0]):
            r = Rig(seed=9, warm=1000.0)
            r.method(INJ(3, sp=30.0))
            r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1})
            r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
            r.load('edta', 'ca')
            r.call(r.A, 'run.start', experiment_id='s')
            r.until(lambda: r.R.run is not None and r.R.run['phase'] != 'equilibrating', dt=0.2)
            for p in parts:
                r.R.step(p)
            out.append((r.R.read('evaluator'), r.R.ledger(), list(r.R.experiments['s']['dp'])))
        self.assertEqual(out[0], out[1])
        self.assertEqual(out[0], out[2])
        CHECKS.append(dict(check='step_partition', status='passed'))


class MoreRejections(unittest.TestCase):
    def test_power_plant_and_misc(self):
        r = Rig(seed=5, power=False)
        r.call(r.A, 'method.set', 'POWER_OFF', temperature_k=298.15, reference_power_w=5 * UC, feedback='high', stir_rate_hz=12.5, initial_delay_s=60.0,
               injections=INJ(4), cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)
        r.call(r.A, 'temperature.set', 'POWER_OFF', temperature_k=300.0)
        r.call(r.A, 'syringe.plunger_down', 'POWER_OFF')
        r.call(r.A, 'instrument.stop', 'POWER_OFF')
        r.call(r.E, 'plant.configure', 'SEED_FIXED', room_k=295.15, mains_voltage_v=230.0, noise_seed=99)
        r.call(r.E, 'plant.configure', room_k=295.15, mains_voltage_v=60.0, noise_seed=5)
        r.call(r.A, 'power.set', 'MAINS_OUT_OF_RANGE', enabled=True)
        r.call(r.A, 'power.press_switch', 'MAINS_OUT_OF_RANGE')
        r.call(r.A, 'wash.power_set', 'MAINS_OUT_OF_RANGE', enabled=True)
        r.call(r.A, 'wash.press_switch', 'MAINS_OUT_OF_RANGE')
        r.call(r.E, 'plant.configure', room_k=295.15, mains_voltage_v=230.0, noise_seed=5)
        r.call(r.A, 'power.press_switch')
        r.call(r.A, 'power.press_switch')
        self.assertFalse(r.v('power.enabled'))
        r.call(r.A, 'power.set', enabled=True)
        r.call(r.A, 'clean.start', 'WASH_MODULE_OFF', cell_method='wash', syringe_method='none')
        r.call(r.A, 'reference.refill', 'UNKNOWN_SOLUTION', solution_id='nope')
        r.call(r.A, 'tube.place', 'UNKNOWN_SOLUTION', solution_id='nope', volume_m3=6e-8)
        r.prep('t', ('mes', 10.0, 5.6), vol=1e-7)
        r.call(r.A, 'reference.refill', 'INSUFFICIENT_SOLUTION', solution_id='t')
        r.call(r.A, 'tube.place', 'INSUFFICIENT_SOLUTION', solution_id='t', volume_m3=2e-7)
        r0 = m.MalvernMicrocalpeaqRuntime()
        r0.register_principal('a', 'agent')
        s = r0.session('a')
        for cap, args in [('power.set', dict(enabled=True)), ('solution.prepare', dict(solution_id='x', volume_m3=1e-6)), ('cell.load', dict(solution_id='x', volume_m3=3e-7)),
                          ('cell.empty', {}), ('reference.refill', {}), ('fpa.attach', {}), ('fpa.detach', {}), ('pipette.move', dict(location='rest')),
                          ('tube.place', dict(solution_id='x', volume_m3=6e-8)), ('tube.remove', {}), ('cleaning_tool.insert', {}), ('cleaning_tool.remove', {}),
                          ('wash.empty_waste', {}), ('wash.empty_overflow', {}), ('wash.refill', dict(bottle='water')), ('solution.degas', dict(solution_id='x')),
                          ('solution.dialyze', dict(solution_id='x', buffer=dict(buffer_id='mes', concentration_mol_m3=1.0, ph=6.0), dialysate_id='d')),
                          ('syringe.load', {}), ('syringe.purge_refill', {}), ('run.start', dict(experiment_id='x')), ('clean.start', dict(cell_method='wash', syringe_method='none')),
                          ('wash.power_set', dict(enabled=True)), ('wash.press_switch', {}), ('power.press_switch', {}), ('temperature.set', dict(temperature_k=300.0))]:
            res = s.invoke(instance_id=r0.instance_id, capability_id=cap, arguments=args, request_id='p' + cap)
            self.assertEqual(res['error']['code'], 'PLANT_REQUIRED', cap)
            SEEN.add((cap, 'PLANT_REQUIRED'))
            REJECTIONS.setdefault(cap, set()).add('PLANT_REQUIRED')
        ev = m.MalvernMicrocalpeaqRuntime()
        ev.register_principal('e', 'evaluator')
        se = ev.session('e')
        for cap, args in [('stock.provide', dict(stock_id='s', volume_m3=1e-6, buffer=None, components=[])),
                          ('interaction.define', dict(interaction_id='i', model='dimer', receptor='x', kd_mol_m3=1.0)), ('fault.inject', dict(fault='cell_bubble', active=True))]:
            res = se.invoke(instance_id=ev.instance_id, capability_id=cap, arguments=args, request_id='p' + cap)
            self.assertEqual(res['error']['code'], 'PLANT_REQUIRED', cap)
            SEEN.add((cap, 'PLANT_REQUIRED'))
        CHECKS.append(dict(check='more_rejections', status='passed'))


class Coverage(unittest.TestCase):
    def test_remaining_capabilities(self):
        r = Rig(seed=5)
        r.call(r.A, 'temperature.set', temperature_k=303.15)
        r.step(2400)
        self.assertAlmostEqual(r.v('truth.jacket_temperature_k'), 303.15, delta=0.01)
        r.call(r.E, 'interaction.define', interaction_id='ix', model='enzyme', receptor='e9', ligand='s9', km_mol_m3=1.0, kcat_s=10.0, dh_j_mol=-1e4)
        r.away()
        r.prep('w', None, None)
        r.call(r.A, 'cell.load', solution_id='w', volume_m3=3e-7)
        removed = r.call(r.A, 'cell.empty')['result']['removed_m3']
        self.assertGreater(removed, 2e-7)
        r.call(r.A, 'reference.refill', solution_id=None)
        r.call(r.A, 'wash.refill', bottle='methanol')
        r.call(r.A, 'wash.refill', bottle='detergent')
        r.call(r.A, 'wash.empty_overflow')
        r.call(r.A, 'wash.empty_waste')
        self.assertTrue(r.call(r.E, 'cell.inspect')['result']['volume_m3'] >= 0)
        self.assertIn('residue', r.call(r.E, 'syringe.inspect')['result'])
        self.assertIn('electrical_energy_j', r.call(r.E, 'ledger.read')['result']['ledger'])
        r.call(r.E, 'fault.inject', fault='stir_motor_failure', active=True)
        r.call(r.E, 'fault.inject', fault='stir_motor_failure', active=False)
        r.call(r.E, 'fault.inject', fault='bottle_filter_clogged', active=True)
        r.call(r.E, 'fault.inject', fault='reference_cell_empty', active=True)
        r.step(1)
        self.assertEqual(r.v('truth.reference_volume_m3'), 0.0)
        r.call(r.A, 'reference.refill')
        r.call(r.E, 'fault.inject', fault='syringe_damaged', active=True)
        r.prep('ca', ('mes', 10.0, 5.6), {'ca2+': 1.0})
        r.load_syringe('ca')
        self.assertIn('SYRINGE_FILL_FAILURE', r.v('errors.active'))
        r.call(r.E, 'fault.inject', fault='wash_pump_failure', active=True)
        r.call(r.A, 'cleaning_tool.insert')
        op = r.call(r.A, 'clean.start', cell_method='rinse', syringe_method='none')['operation_id']
        r.step(2)
        self.assertEqual(r.R.operations[op]['error'], 'NO_FLOW')
        r.call(r.A, 'syringe.purge_refill', 'PIPETTE_NOT_AT_LOAD')
        r.move('clean')
        r.call(r.A, 'clean.start', 'FPA_NOT_ATTACHED', cell_method='none', syringe_method='rinse')
        r.call(r.A, 'fpa.attach')
        r.call(r.A, 'cleaning_tool.remove')
        r.call(r.A, 'syringe.plunger_down')
        CHECKS.append(dict(check='coverage', status='passed'))


# ---------------------------------------------------------------- declared effects (with / without-call discovery)
class ZDeclaredEffects(unittest.TestCase):
    """Immediate state changes stay within EFFECTS; changes after a step stay within EFFECTS | ON_STEP (compared with the same host not called)."""

    def _check(self, rig, cap, args, session=None):
        meta = m.export_malvern_microcalpeaq_contract_metadata()['capabilities'][cap]
        eff = {e['state_id'] for e in meta['state_effects'] if e['timing'] == 'immediate'}
        on_step = {e['state_id'] for e in meta['state_effects']}
        base = copy.deepcopy(rig.R)
        s0 = rig.R.read('evaluator')
        res = (session or rig.A).invoke(instance_id=rig.I, capability_id=cap, arguments=args, request_id=f'eff_{cap}_{rig.n}')
        rig.n += 1
        self.assertIn(res['status'], ('succeeded', 'accepted'), (cap, res['error']))
        s1 = rig.R.read('evaluator')
        changed = {k for k in s0 if s0[k] != s1[k]} - {'operation.started_by_other_agent'}
        self.assertTrue(changed <= eff, (cap, sorted(changed - eff)))
        for dt in (2.0, 28.0, 270.0, 600.0):
            rig.R.step(dt)
            base.step(dt)
            a, b = rig.R.read('evaluator'), base.read('evaluator')
            diff = {k for k in a if a[k] != b[k]}
            self.assertTrue(diff <= (eff | on_step), (cap, dt, sorted(diff - (eff | on_step))))

    def _ctx(self):
        r = Rig(seed=3, warm=1800.0)
        r.method(INJ(5, sp=60.0))
        r.prep('edta', ('mes', 10.0, 5.6), {'edta': 0.1}, vol=2e-6)
        r.prep('ca', buffer_from='edta', solutes={'ca2+': 1.0})
        return r

    def test_declared_effects_bench_and_load(self):
        r = self._ctx()
        for cap, args in [('method.set', dict(temperature_k=303.15, reference_power_w=8 * UC, feedback='low', stir_rate_hz=10.0, initial_delay_s=120.0, injections=INJ(5),
                                              cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)),
                          ('temperature.set', dict(temperature_k=298.15)), ('solution.prepare', dict(solution_id='s2', volume_m3=1e-6, buffer=dict(buffer_id='tris', concentration_mol_m3=20.0, ph=8.0))),
                          ('solution.degas', dict(solution_id='edta')), ('reference.refill', {}), ('pipette.move', dict(location='rest'))]:
            self._check(r, cap, args)
        r.until(lambda: r.R.pip['phase'] is None)
        for cap, args in [('cell.load', dict(solution_id='edta', volume_m3=3e-7, rinse=True)), ('tube.place', dict(solution_id='ca', volume_m3=6e-8)), ('fpa.attach', {}),
                          ('syringe.plunger_down', {}), ('cleaning_tool.insert', {}), ('cleaning_tool.remove', {})]:
            self._check(r, cap, args)
        r.until(lambda: r.R.plunger_target is None)
        self._check(r, 'pipette.move', dict(location='load'))
        r.until(lambda: r.R.pip['phase'] is None)
        self._check(r, 'syringe.load', {})
        r.until(lambda: not r.R._running_ops())
        self._check(r, 'syringe.purge_refill', {})
        r.until(lambda: not r.R._running_ops())
        r.move('rest')
        for cap, args in [('tube.remove', {}), ('fpa.detach', {}), ('cell.empty', {}),
                          ('solution.dialyze', dict(solution_id='s2', buffer=dict(buffer_id='hepes', concentration_mol_m3=20.0, ph=7.5), dialysate_id='dial')),
                          ('wash.power_set', dict(enabled=False)), ('wash.press_switch', {})]:
            self._check(r, cap, args)
        self._check(r, 'stock.provide', dict(stock_id='st', volume_m3=1e-6, buffer=None, components=[dict(species_id='propan_1_ol', concentration_mol_m3=500.0)]), session=r.E)
        self._check(r, 'interaction.define', dict(interaction_id='ix', model='sites', receptor='px', ligand='ly', n=1.0, kd_mol_m3=1e-3, dh_j_mol=-4e4), session=r.E)

    def test_declared_effects_run_and_clean(self):
        r = self._ctx()
        r.load('edta', 'ca')
        r.step(300)
        self._check(r, 'run.start', dict(experiment_id='x1'))
        r.until(lambda: r.R.run is not None and r.R.run['inj'] >= 1)
        self._check(r, 'instrument.stop', {})
        r.away()
        r.call(r.A, 'cleaning_tool.insert')
        r.move('clean')
        r.call(r.A, 'fpa.attach')
        self._check(r, 'clean.start', dict(cell_method='wash', syringe_method='rinse'))
        r.until(lambda: not r.R._running_ops())
        for cap, args in [('wash.empty_waste', {}), ('wash.empty_overflow', {}), ('wash.refill', dict(bottle='water'))]:
            self._check(r, cap, args)
        self._check(r, 'fault.inject', dict(fault='cell_bubble', active=True), session=r.E)
        self._check(r, 'power.press_switch', {})
        self._check(r, 'power.set', dict(enabled=True))
        self._check(r, 'plant.configure', dict(room_k=297.15, mains_voltage_v=230.0, noise_seed=3), session=r.E)


class ZZRejectionCodes(unittest.TestCase):
    def test_codes_are_declared(self):
        meta = m.export_malvern_microcalpeaq_contract_metadata()['capabilities']
        missing = [(c, e) for c, e in sorted(SEEN) if e not in {x['code'] for x in meta[c]['errors']}]
        missing += [(c, e) for c, es in sorted(REJECTIONS.items()) for e in sorted(es) if e not in {x['code'] for x in meta[c]['errors']}]
        self.assertEqual(missing, [])
        self.assertGreaterEqual(len(SEEN), 40)
        CHECKS.append(dict(check='rejection_codes_declared', status='passed', codes=len(SEEN)))

    def test_zz_every_implemented_capability_called(self):
        missing = sorted(k for k in m.CAPS if k not in m.UNSUPPORTED and k not in PROCESS_EVIDENCE)
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
