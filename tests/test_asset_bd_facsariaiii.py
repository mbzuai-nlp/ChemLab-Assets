"""CHEM-168 BD FACSAria III cell sorter: runtime tests (python3 -m unittest tests/test_asset_bd_facsariaiii.py, matterix env)."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import bd_facsariaiii as m  # noqa: E402
import asset_tool  # noqa: E402

rt = m
PSI = m.PSI
SEEN = set()                                     # (capability, rejection code) pairs produced by the tests
CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}


def _meta():
    if not _META:
        _META.update(m.export_bd_facsariaiii_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = m.BdFacsariaiiiRuntime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema, the clock and the declared immediate effects; every rejection code is
    recorded."""
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


m.BdFacsariaiiiRuntime.invoke = _instrumented_invoke


class Rig:
    def __init__(self, seed=7, room=295.15):
        self.R = m.BdFacsariaiiiRuntime()
        self.R.register_principal('agent', 'agent')
        self.R.register_principal('other', 'agent')
        self.R.register_principal('ev', 'evaluator')
        self.A, self.B, self.E = self.R.session('agent'), self.R.session('other'), self.R.session('ev')
        self.I = self.R.instance_id
        self.n = 0
        self.call(self.E, 'plant.configure', room_k=room, mains_voltage_v=230.0, noise_seed=seed)

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

    def ready(self, warm=True, sweet=True, preset=None):
        A = self.A
        self.call(A, 'power.press_switch')
        for L in m.LASERS:
            self.call(A, 'lasers.set', laser=L, enabled=True)
        if preset:
            self.call(A, 'door.set', door='flow_cell', angle_rad=1.2)
            self.call(A, 'nozzle.install', nozzle=m._cal()['presets'][preset]['nozzle_um'])
            self.call(A, 'door.set', door='flow_cell', angle_rad=0.0)
            self.call(A, 'sort_setup.select', preset=preset)
        self.call(A, 'fluidics.startup')
        self.step(m.STARTUP_S + 1)
        self.call(A, 'stream.set', on=True)
        self.step(30)
        if sweet:
            self.call(A, 'sweet_spot.set', enabled=True)
        if warm:
            self.step(m.WARMUP_S)

    def provide(self, sid, pops, vol=3e-6):
        self.call(self.E, 'sample.provide', sample_id=sid, volume_m3=vol, populations=pops)

    def tube(self, tid, sources, buf=0.0, s=None, ttype='12x75'):
        self.call(s or self.A, 'tube.prepare', tube_id=tid, tube_type=ttype, sources=sources, buffer_volume_m3=buf)

    def load(self, tid, s=None, wait=5.0):
        self.call(s or self.A, 'tube.install', tube_id=tid)
        self.call(s or self.A, 'sample.load')
        self.step(wait)

    def unload(self, s=None):
        self.call(s or self.A, 'sample.unload')
        self.step(4)
        self.call(s or self.A, 'tube.remove')

    def stats(self, source='live', gates=None, params=None, comp=None):
        a = dict(source=source)
        if comp is not None:
            a['compensation'] = comp
        if gates is not None:
            a['gates'] = gates
        if params is not None:
            a['parameters'] = params
        return self.call(self.A, 'statistics.read', **a)['result']

    def record(self, rid, n):
        op = self.call(self.A, 'acquisition.record', record_id=rid, events=n)['operation_id']
        for _ in range(600):
            if self.A.poll(op)['status'] != 'running':
                break
            self.step(0.5)
        return self.A.poll(op)

    def install(self, device, prefill=5e-7):
        self.call(self.A, 'door.set', door='sort_chamber', angle_rad=1.6)
        self.call(self.A, 'collection.install', device=device, prefill_m3=prefill)
        self.call(self.A, 'door.set', door='sort_chamber', angle_rad=0.0)

    def sort(self, lid, max_s=120.0):
        op = self.call(self.A, 'sort.start', layout_id=lid)['operation_id']
        t = 0.0
        while self.A.poll(op)['status'] == 'running' and t < max_s:
            self.step(1.0)
            t += 1.0
        return op

    def collected(self):
        out = {}
        for loc in self.call(self.E, 'collection.inspect')['result']['locations']:
            out[loc['location']] = {p['population_id'].split('/')[-1]: p['particles'] for p in loc['populations']}
        return out


PBMC = [dict(population_id='cd4', particle_id='human_t_cell_cd4', concentration_per_m3=6e12, stains=[dict(dye='fluorescein_fitc', molecules=45000.0, cv=0.35)]),
        dict(population_id='bcell', particle_id='human_b_cell', concentration_per_m3=4e12, stains=[dict(dye='fluorescein_fitc', molecules=300.0, cv=0.5)])]


def set_true_drop_delay(rig):
    rig.call(rig.A, 'drop_delay.set', drops=round(rig.v('truth.drop_delay_drops'), 2))     # as found by Accudrop (tested separately)


def purity(c, loc, pop):
    tot = sum(c.get(loc, {}).values())
    return c[loc].get(pop, 0.0) / tot if tot else 0.0


def two_way(rig, mode='purity', n=8000, lid='two'):
    A = rig.A
    rig.call(A, 'gate.define', gate_id='cells', kind='rectangle', x='FSC-A', y='SSC-A', x_range=[20000.0, 262143.0], y_range=[0.0, 262143.0])
    rig.call(A, 'gate.define', gate_id='singlets', parent='cells', kind='interval', x='FSC-W', x_range=[0.0, 85000.0])     # doublet discrimination
    rig.call(A, 'gate.define', gate_id='pos', parent='singlets', kind='interval', x='B530_30-A', x_range=[3000.0, 262143.0])
    rig.call(A, 'gate.define', gate_id='neg', parent='singlets', kind='interval', x='B530_30-A', x_range=[-2000.0, 1500.0])
    rig.install('2way_12x75')
    rig.call(A, 'deflection.set', voltage_on=True)
    rig.call(A, 'sort.layout', layout_id=lid, device='2way_12x75', precision_mode=mode, targets={'left': 'pos', 'right': 'neg'},
             target_events={'left': n, 'right': n})


# ---------------------------------------------------------------- libraries, calibration, contract
class ContractAndLibraries(unittest.TestCase):
    def test_contract_metadata_and_examples(self):
        meta = m.export_bd_facsariaiii_contract_metadata()
        self.assertEqual(meta['asset_type_id'], 'instrument.bd_facsariaiii')
        self.assertGreaterEqual(len(meta['capabilities']), 60)
        for k, c in meta['capabilities'].items():
            m.validate(c['input_schema'], c['example_arguments'])
            for e in c['state_effects']:
                self.assertIn(e['state_id'], meta['state_slots'])
        for k, s in meta['state_slots'].items():
            self.assertIn(s['unit'], ('1', 's', 'm', 'rad', 'K', 'Pa', 'V', 'Hz', 'W', 'm3', '1/s', 'm/s', 'K/s', '1/min', '°C', 'J', 'm3/s'), k)
        agent_caps = [k for k, c in meta['capabilities'].items() if c['visibility'] == 'agent']
        for k in agent_caps:
            for e in meta['capabilities'][k]['observes']:
                self.assertEqual(meta['state_slots'][e]['visibility'], 'agent', (k, e))

    def test_libraries_real_data(self):
        fl, pl = m.fluorochromes(), m.particles()
        self.assertGreaterEqual(len(fl), 50)
        self.assertGreaterEqual(len(pl), 25)
        self.assertTrue(all('FPbase' in d['source'] for d in fl.values()))
        self.assertTrue(all(len(p['scatter']['fsc_cross_section_m2']) == len(p['scatter']['diameter_um']) for p in pl.values()))
        pb = m._photon()
        i = pb['didx']['fluorescein_fitc']
        j = m.FL_DETS.index('B530_30')
        q = pb['q'][i, j]
        self.assertAlmostEqual(q, m.optics()['detectors']['B530_30']['q_pe_per_mesf'], places=9)
        # documented 85 MESF-FITC = 3 sqrt(background) / Q
        self.assertAlmostEqual(3 * math.sqrt(pb['bg'][j]) / q, 85.0, places=6)

    def test_fluidics_calibration_reproduces_table_3_1(self):
        cal = m._cal()['presets']
        for name, dd in (('70_high', 44.0), ('70_medium', 45.0), ('100', 29.0), ('85', 37.0), ('130', 24.0)):
            c = cal[name]
            q = m._q_sheath(c['a'] * 2, c['v'])
            self.assertAlmostEqual(c['frequency_hz'] * c['vdead'] / q + c['nb_ref'], dd, places=9)
            self.assertTrue(4.0 < c['lambda_over_d'] < 4.6, (name, c['lambda_over_d']))       # near the Rayleigh optimum 4.51
        # the documented High (60 V, attenuation off) and Medium (50 V, attenuation on) defaults give one coupling of the 70 um nozzle
        self.assertAlmostEqual(cal['70_high']['G'] / cal['70_medium']['G'], 1.0, delta=0.01)
        r = Rig()
        r.ready(warm=False, sweet=False, preset='70_medium')
        d = r.v('truth.laser_delays_s')
        self.assertAlmostEqual(d['violet'] * 1e6, 54.0, delta=0.6)
        self.assertAlmostEqual(d['red'] * 1e6, -54.0, delta=0.6)

    def test_water_properties(self):
        self.assertAlmostEqual(m.water_surface_tension(293.15), 0.07274, delta=2e-5)
        self.assertAlmostEqual(m.water_density(277.13), 999.97, delta=0.01)
        self.assertAlmostEqual(m.water_viscosity(293.15), 1.002e-3, delta=1.5e-5)


# ---------------------------------------------------------------- fluidics, breakoff, Sweet Spot
class Fluidics(unittest.TestCase):
    def test_startup_stream_and_cart(self):
        r = Rig()
        A = r.A
        r.call(A, 'stream.set', 'POWER_OFF', on=True)
        r.call(A, 'power.press_switch')
        r.call(A, 'stream.set', 'FLUIDICS_NOT_READY', on=True)
        op = r.call(A, 'fluidics.startup')['operation_id']
        r.call(A, 'fluidics.startup', 'FLUIDICS_BUSY')
        r.step(m.STARTUP_S - 1)
        self.assertEqual(A.poll(op)['status'], 'running')
        r.step(2)
        self.assertEqual(A.poll(op)['status'], 'succeeded')
        self.assertAlmostEqual(r.v('cart.air_pressure_pa') / PSI, 88.0, delta=0.5)
        self.assertAlmostEqual(r.v('cart.gauge_angle_rad'), 88.0 / 160.0 * 1.5 * math.pi, delta=0.02)
        sheath0 = r.v('truth.cart_sheath_m3')
        r.call(A, 'stream.set', on=True)
        r.step(60)
        self.assertAlmostEqual(r.v('sheath.pressure_pa') / PSI, 70.0, delta=0.05)
        used = sheath0 - r.v('truth.cart_sheath_m3')
        q = r.v('truth.jet_speed_m_s') * math.pi * 35e-6 ** 2
        self.assertAlmostEqual(used / 60.0, q, delta=0.05 * q)                      # ~6.5 ml/min at 70 psi
        self.assertAlmostEqual(q * 6e7, 6.46, delta=0.1)
        r.call(A, 'cart.refill_sheath', 'STREAM_ON', volume_m3=0.001)
        r.call(A, 'fluidics.shutdown', 'STREAM_ON')
        r.call(A, 'stream.set', on=False)
        r.call(A, 'fluidics.shutdown', 'CLOSED_LOOP_NOZZLE_REQUIRED')
        r.call(A, 'nozzle.install', 'DOOR_CLOSED', nozzle='closed_loop')
        r.call(A, 'door.set', door='flow_cell', angle_rad=1.2)
        r.call(A, 'nozzle.install', nozzle='closed_loop')
        op = r.call(A, 'fluidics.shutdown')['operation_id']
        r.step(m.SHUTDOWN_S + 1)
        self.assertEqual(A.poll(op)['status'], 'succeeded')
        self.assertEqual(r.v('fluidics.state'), 'shutdown')

    def test_drop1_jumps_by_the_drop_spacing_and_gap_is_continuous(self):
        r = Rig()
        r.ready(warm=False, sweet=False)
        A = r.A
        rows = []
        for amp in [50.0 + 0.5 * k for k in range(41)]:
            r.call(A, 'breakoff.set', amplitude_v=amp)
            o = r.rd('breakoff.drop1_px', 'breakoff.gap_px', 'breakoff.drop_spacing_px', 'truth.breakoff_length_m')
            rows.append((amp, o['breakoff.drop1_px'], o['breakoff.gap_px'], o['breakoff.drop_spacing_px'], o['truth.breakoff_length_m']))
        d1 = sorted({round(x[1], 1) for x in rows})
        self.assertGreaterEqual(len(d1), 2)
        for a, b in zip(d1, d1[1:]):
            self.assertAlmostEqual(b - a, rows[0][3], delta=0.2)                   # documented ~57 px jumps
        self.assertTrue(all(rows[i][4] > rows[i + 1][4] for i in range(len(rows) - 1)))   # higher amplitude: shorter breakoff
        self.assertTrue(all(0 < x[2] <= x[3] + 1e-6 for x in rows))

    def test_sweet_spot_holds_breakoff_and_drop_delay_while_sheath_warms(self):
        drift = {}
        for sweet in (False, True):
            r = Rig()
            r.ready(warm=False, sweet=sweet)
            r.step(300)                                                             # Sweet Spot first pulls the Gap to its 6-px target
            a0 = r.rd('breakoff.drop1_px', 'truth.drop_delay_drops', 'breakoff.amplitude_v')
            r.step(1800)
            a1 = r.rd('breakoff.drop1_px', 'breakoff.gap_px', 'truth.drop_delay_drops', 'breakoff.amplitude_v', 'truth.sheath_temperature_k', 'sweet_spot.paused')
            drift[sweet] = (a0, a1)
        off0, off1 = drift[False]
        on0, on1 = drift[True]
        self.assertGreater(abs(off1['truth.drop_delay_drops'] - off0['truth.drop_delay_drops']), 0.1)     # unattended stream drifts (~1 K warm-up)
        self.assertLess(abs(on1['truth.drop_delay_drops'] - on0['truth.drop_delay_drops']), 0.05)
        self.assertNotEqual(on1['breakoff.amplitude_v'], on0['breakoff.amplitude_v'])
        self.assertLessEqual(abs(on1['breakoff.gap_px'] - 6.0), 2.0)
        self.assertFalse(on1['sweet_spot.paused'])

    def test_sweet_spot_locks_controls_and_attenuation(self):
        r = Rig()
        r.ready(warm=False)
        A = r.A
        r.call(A, 'breakoff.set', 'SWEET_SPOT_ON', amplitude_v=40.0)
        r.call(A, 'sheath.set_pressure', 'SWEET_SPOT_ON', pressure_pa=60 * PSI)
        r.call(A, 'sort_setup.select', 'STREAM_ON', preset='100')
        b0 = r.v('truth.breakoff_length_m')
        r.call(A, 'sweet_spot.set', enabled=False)
        r.call(A, 'breakoff.set', attenuation=True)
        self.assertGreater(r.v('truth.breakoff_length_m'), b0 + 1e-3)                # attenuated drive: much longer breakoff

    def test_cart_air_leak_limits_sheath_pressure(self):
        r = Rig()
        r.ready(warm=False, sweet=False)
        r.call(r.E, 'fault.inject', fault='cart_air_leak', active=True)
        r.step(300)
        o = r.rd('cart.air_pressure_pa', 'sheath.pressure_pa', 'warnings.active')
        self.assertLess(o['cart.air_pressure_pa'] / PSI, 41.0)
        self.assertLess(o['sheath.pressure_pa'] / PSI, 36.0)
        self.assertIn('SHEATH_PRESSURE_LOW', o['warnings.active'])


# ---------------------------------------------------------------- acquisition physics
class Acquisition(unittest.TestCase):
    def test_event_rate_follows_concentration_and_flow(self):
        r = Rig()
        r.ready(sweet=False)
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        conc = (6e12 + 4e12) * 2e-6 / 3e-6
        r.load('t')
        for setting in (1.0, 6.0):
            r.call(r.A, 'acquisition.flow_rate', setting=setting)
            r.step(3)
            q = r.R._flow_m3_s()
            self.assertAlmostEqual(r.v('acquisition.threshold_rate_eps'), conc * q, delta=0.06 * conc * q)
        self.assertAlmostEqual(r.R._flow_m3_s() * 6e10, 65.0, delta=0.01)          # setting 6: 65 ul/min (linear 10-120)

    def test_cv_rises_with_flow_rate_core_width(self):
        cv = {}
        for setting in (1.0, 11.0):
            r = Rig()
            r.ready(sweet=False)
            r.tube('b', [dict(bead_id='cst_bead_bright', volume_m3=3e-7)], 7e-7)
            r.call(r.A, 'acquisition.flow_rate', setting=setting)
            r.load('b', wait=8.0)
            st = r.stats(params=['B530_30-A'])
            cv[setting] = st['rows'][0]['parameters'][0]['rcv']
        self.assertLess(cv[1.0], 0.04)
        self.assertGreater(cv[11.0], cv[1.0] + 0.015)

    def test_shutter_lasers_warmup_and_threshold(self):
        r = Rig()
        r.ready(warm=False, sweet=False)
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.load('t')
        self.assertIn('LASERS_WARMING', r.v('warnings.active'))
        p_cold = r.v('lasers.power_w')['blue']
        cold = r.stats(params=['B530_30-A'])['rows'][0]['parameters'][0]['median']
        r.call(r.A, 'door.set', door='flow_cell', angle_rad=0.5)
        r.step(2)
        self.assertEqual(r.v('acquisition.threshold_rate_eps'), 0.0)               # shutter blocks the lasers
        r.call(r.A, 'door.set', door='flow_cell', angle_rad=0.0)
        r.step(1800)
        r.step(2)
        warm = r.stats(params=['B530_30-A'])['rows'][0]['parameters'][0]['median']
        self.assertGreater(r.v('lasers.power_w')['blue'], p_cold * 1.04)               # power rises during the 30-min warm-up
        self.assertGreater(warm, cold * 1.02)
        self.assertNotIn('LASERS_WARMING', r.v('warnings.active'))
        rate = r.v('acquisition.threshold_rate_eps')
        r.call(r.A, 'cytometer.settings', thresholds=[dict(parameter='FSC', value=150000.0)])
        r.step(2)
        self.assertLess(r.v('acquisition.threshold_rate_eps'), 0.2 * rate)

    def test_doublets_widen_and_double_area(self):
        r = Rig()
        r.ready(sweet=False)
        r.provide('c', [dict(population_id='cen', particle_id='chicken_erythrocyte_nucleus', concentration_per_m3=4e12, doublet_fraction=0.2,
                             stains=[dict(dye='propidium_iodide', molecules=8.0e8, cv=0.015)])])
        r.tube('t', [dict(sample_id='c', volume_m3=2e-6)], 1e-6)
        r.call(r.A, 'cytometer.settings', voltages={'B695_40': 420.0})
        r.load('t', wait=10)
        p = r.call(r.A, 'record.read', record_id=r.record('cen', 4000)['result']['record_id'], parameters=['B695_40-A', 'FSC-W'], max_events=4000)['result']
        import numpy as np
        X = np.array(p['events'])
        a = X[:, 0]
        med = np.median(a)
        single = (a > 0.8 * med) & (a < 1.25 * med)
        double = (a > 1.6 * med) & (a < 2.5 * med)
        ratio = np.median(a[double]) / np.median(a[single])
        self.assertTrue(1.95 <= ratio <= 2.05, ratio)                              # documented linearity 1.95-2.05
        self.assertGreater(np.median(X[double, 1]), 1.15 * np.median(X[single, 1]))
        self.assertLess(np.std(a[single]) / np.mean(a[single]), 0.03)             # documented PI-CEN CV < 3 %

    def test_dim_bead_sensitivity_matches_85_mesf(self):
        r = Rig()
        r.ready(sweet=False)
        r.tube('b', [dict(bead_id='ps_bead_3um', volume_m3=2e-7, stains=[dict(dye='fluorescein_fitc', molecules=85.0, cv=0.02)]),
                     dict(bead_id='ps_bead_3um', volume_m3=2e-7)], 6e-7)
        r.call(r.A, 'cytometer.settings', voltages={'B530_30': 650.0})
        r.load('b', wait=6)
        import numpy as np
        rec = r.record('dim', 6000)['result']['record_id']
        X = np.array(r.call(r.A, 'record.read', record_id=rec, parameters=['B530_30-A'], max_events=5000)['result']['events'])[:, 0]
        lo, hi = np.percentile(X, [25, 75])
        # two populations: blank and 85 MESF; the separation is ~3 SD of the blank (definition of the documented sensitivity)
        blank = X[X < np.median(X)]
        dim = X[X >= np.median(X)]
        sep = (np.median(dim) - np.median(blank)) / np.std(blank)
        self.assertTrue(2.0 < sep < 4.5, sep)

    def test_rate_limit_and_aborts(self):
        r = Rig()
        r.ready(sweet=False)
        self.assertAlmostEqual(r.v('cytometer.max_event_rate_eps'), 70000.0, delta=1.0)               # documented maximum (not extrapolated above it)
        S = {d: {d: 1.0} for d in m.FL_DETS}
        pairs = [('V450_50', 'V510_50'), ('B530_30', 'YG582_15'), ('YG582_15', 'YG610_20'), ('YG610_20', 'YG582_15'), ('YG582_15', 'YG710_50'), ('YG710_50', 'YG780_60'),
                 ('R660_20', 'R780_60'), ('R780_60', 'R660_20'), ('YG780_60', 'R780_60'), ('B695_40', 'YG710_50'), ('V510_50', 'B530_30'), ('B530_30', 'V510_50')]
        for a, b in pairs:
            S[a][b] = 0.1
        r.call(r.A, 'compensation.set', enabled=True, spillover=S)
        self.assertAlmostEqual(r.v('cytometer.max_event_rate_eps'), 70000.0 * 14 / 18, delta=1.0)     # 12 detectors + 12 pairs: fewer events (assumption)
        r.call(r.A, 'cytometer.settings', detectors_enabled=['FSC', 'SSC', 'V450_50', 'B530_30', 'YG582_15', 'YG610_20', 'R660_20', 'R780_60'])
        self.assertAlmostEqual(r.v('cytometer.max_event_rate_eps'), 70000.0, delta=1.0)   # documented: 8 parameters, 12 pairs
        r.provide('s', [dict(population_id='p', particle_id='jurkat', concentration_per_m3=1.6e14)], vol=1e-5)
        r.tube('t', [dict(sample_id='s', volume_m3=1.5e-6)], 0.0)
        r.call(r.A, 'acquisition.flow_rate', setting=8.0)
        r.load('t', wait=5)
        o = r.rd('acquisition.threshold_rate_eps', 'acquisition.processed_rate_eps', 'acquisition.abort_rate_eps')
        self.assertGreater(o['acquisition.threshold_rate_eps'], 72000.0)
        self.assertAlmostEqual(o['acquisition.processed_rate_eps'], 70000.0, delta=100.0)
        self.assertGreater(o['acquisition.abort_rate_eps'], 1000.0)


# ---------------------------------------------------------------- compensation, gates
class CompensationAndGates(unittest.TestCase):
    def test_single_stain_compensation(self):
        r = Rig()
        r.ready(sweet=False)
        A = r.A
        r.tube('u', [dict(bead_id='ps_bead_6um', volume_m3=3e-7)], 7e-7)
        r.tube('pe', [dict(bead_id='ps_bead_6um', volume_m3=3e-7, stains=[dict(dye='pe_r_pe_r_phycoerythrin', molecules=5.0e4, cv=0.1)])], 7e-7)
        r.call(A, 'cytometer.settings', voltages={'YG582_15': 420.0, 'YG610_20': 420.0})
        r.load('u')
        r.record('unst', 3000)
        r.unload()
        r.load('pe')
        r.record('pe_ctrl', 3000)
        r.unload()
        res = r.call(A, 'compensation.calculate', unstained_record='unst', controls={'YG582_15': 'pe_ctrl'})['result']
        spill = res['spillover']['YG582_15']['YG610_20']
        fl = m.fluorochromes()['pe_r_pe_r_phycoerythrin']['detector_response']
        self.assertAlmostEqual(spill, fl['YG610_20'] / fl['YG582_15'], delta=0.03)      # spectral spill from FPbase x band-pass
        st = r.stats(source='pe_ctrl', params=['YG610_20-A'], comp='current')
        un = r.stats(source='unst', params=['YG610_20-A'], comp='current')
        self.assertAlmostEqual(st['rows'][0]['parameters'][0]['median'], un['rows'][0]['parameters'][0]['median'], delta=80.0)
        raw = r.stats(source='pe_ctrl', params=['YG610_20-A'])                    # default: the compensation stored with the (uncompensated) record
        self.assertFalse(raw['compensated'])
        self.assertGreater(raw['rows'][0]['parameters'][0]['median'], st['rows'][0]['parameters'][0]['median'] + 500.0)
        rd = r.call(A, 'record.read', record_id='pe_ctrl', parameters=['YG610_20-A'], max_events=200)['result']
        rc = r.call(A, 'record.read', record_id='pe_ctrl', parameters=['YG610_20-A'], max_events=200, compensation='current')['result']
        self.assertEqual((rd['compensated'], rc['compensated']), (False, True))
        self.assertFalse(r.call(A, 'data.export', record_id='pe_ctrl', kind='fcs_csv')['result']['compensated'])
        r.load('pe')
        r.record('pe_after', 1000)                                                    # recorded with compensation on: stored with the record
        self.assertTrue(r.stats(source='pe_after', params=['YG610_20-A'])['compensated'])
        self.assertTrue([x for x in r.call(A, 'records.list')['result']['records'] if x['record_id'] == 'pe_after'][0]['compensated'])
        r.call(A, 'compensation.set', 'INVALID_SPILLOVER', enabled=True, spillover={'B530_30': {'B530_30': 0.5}})
        offd = [v for d, row in res['spillover'].items() for e, v in row.items() if e != d]
        self.assertTrue(offd and min(offd) >= m.SPILL_MIN)                          # noise-level spillover is not a compensation pair
        self.assertEqual(res['pairs'], len(offd))

    def test_gate_hierarchy_polygon_and_errors(self):
        r = Rig()
        r.ready(sweet=False)
        A = r.A
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.load('t', wait=4)
        r.call(A, 'gate.define', gate_id='p1', kind='polygon', x='FSC-A', y='SSC-A', vertices=[[1.0, 1.0], [262000.0, 1.0], [262000.0, 262000.0], [1.0, 262000.0]])
        r.call(A, 'gate.define', gate_id='pos', parent='p1', kind='interval', x='B530_30-A', x_range=[3000.0, 262143.0])
        r.call(A, 'gate.define', 'INVALID_GATE', gate_id='bad', kind='rectangle', x='FSC-A', x_range=[0.0, 1.0])
        r.call(A, 'gate.define', 'UNKNOWN_GATE', gate_id='orphan', parent='nope', kind='interval', x='FSC-A', x_range=[0.0, 1.0])
        st = r.stats(gates=['p1', 'pos'])
        rows = {x['gate']: x for x in st['rows']}
        self.assertAlmostEqual(rows['pos']['pct_parent'], 60.0, delta=3.0)        # 6e12 : 4e12 populations
        r.call(A, 'gate.delete', 'GATE_IN_USE', gate_id='p1')
        r.call(A, 'gate.delete', gate_id='pos')
        r.call(A, 'gate.delete', gate_id='p1')


# ---------------------------------------------------------------- sorting
class Sorting(unittest.TestCase):
    def test_purity_mode_two_way_and_post_sort_check(self):
        r = Rig()
        r.ready()
        set_true_drop_delay(r)
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.call(r.A, 'acquisition.flow_rate', setting=3.0)
        r.load('t')
        two_way(r, n=6000)
        op = r.sort('two')
        self.assertEqual(r.A.poll(op)['status'], 'succeeded')
        o = r.rd('sort.counts', 'sort.state', 'sort.drawer_open')
        self.assertEqual(o['sort.counts'], {'left': 6000, 'right': 6000})
        self.assertEqual(o['sort.state'], 'completed')
        self.assertFalse(o['sort.drawer_open'])
        c = r.collected()
        self.assertGreater(purity(c, 'left', 'cd4'), 0.97)
        self.assertGreater(purity(c, 'right', 'bcell'), 0.97)
        # post-sort purity check: the sorted fraction is a new sample
        r.unload()
        r.call(r.A, 'door.set', door='sort_chamber', angle_rad=1.6)
        samples = r.call(r.A, 'collection.remove')['result']['samples']
        left = [s for s in samples if s.endswith('_left')][0]
        r.tube('chk', [dict(sample_id=left, volume_m3=4.5e-7)], 0.0)
        r.call(r.A, 'acquisition.flow_rate', setting=11.0)
        r.load('chk', wait=20)
        st = r.stats(gates=['cells', 'singlets', 'pos'])
        rows = {x['gate']: x for x in st['rows']}
        self.assertGreater(rows['pos']['events'], 100)
        self.assertGreater(rows['pos']['pct_parent'], 94.0)

    def test_yield_vs_purity_mode_tradeoff(self):
        res = {}
        for mode in ('yield', 'purity', '4way_purity'):
            r = Rig(seed=3)
            r.ready()
            set_true_drop_delay(r)
            r.provide('s', [dict(population_id='rare', particle_id='human_t_cell_cd4', concentration_per_m3=2e12, stains=[dict(dye='fluorescein_fitc', molecules=45000.0)]),
                            dict(population_id='bulk', particle_id='human_b_cell', concentration_per_m3=2.0e13)], vol=4e-6)
            r.tube('t', [dict(sample_id='s', volume_m3=3e-6)], 0.0)
            r.call(r.A, 'acquisition.flow_rate', setting=6.0)
            r.load('t')
            two_way(r, mode=mode, n=3000)
            r.sort('two', max_s=60)
            c = r.collected()
            led = r.call(r.E, 'ledger.read')['result']['ledger']
            res[mode] = (purity(c, 'left', 'rare'), c['left'].get('rare', 0.0) / max(1.0, r.v('sort.counts')['left']), r.v('sort.efficiency'))
        self.assertGreater(res['purity'][0], res['yield'][0] + 0.03)              # yield mode: extra drops carry bulk cells
        self.assertGreater(res['4way_purity'][0], 0.97)
        self.assertGreater(res['yield'][2], res['purity'][2])                     # purity mode rejects conflicts

    def test_conflicts_grow_with_event_rate_poisson(self):
        eff = {}
        for conc in (3e12, 3e13):
            r = Rig(seed=5)
            r.ready()
            set_true_drop_delay(r)
            r.provide('s', [dict(population_id='cd4', particle_id='human_t_cell_cd4', concentration_per_m3=conc * 0.5, stains=[dict(dye='fluorescein_fitc', molecules=45000.0)]),
                            dict(population_id='bcell', particle_id='human_b_cell', concentration_per_m3=conc * 0.5)], vol=4e-6)
            r.tube('t', [dict(sample_id='s', volume_m3=3e-6)], 0.0)
            r.call(r.A, 'acquisition.flow_rate', setting=6.0)
            r.load('t')
            two_way(r, mode='4way_purity', n=4000)
            r.sort('two', max_s=60)
            rate = r.v('acquisition.threshold_rate_eps')
            eff[conc] = (r.v('sort.efficiency'), rate)
        lo, hi = eff[3e12], eff[3e13]
        self.assertGreater(lo[0], hi[0])
        # 4-way purity (1 drop, purity mask 32): the drop and the adjacent half-drops (2 drop periods) must be free of events of the other population
        # (half of the events); pulse pile-up merges neighbours closer than ~5 us, so the free window is effectively 2 / f - 5 us
        r_o = 0.5 * hi[1]
        self.assertGreater(hi[0], math.exp(-2.0 * r_o / 90000.0) - 0.02)
        self.assertLess(hi[0], math.exp(-r_o * (2.0 / 90000.0 - 6e-6)) + 0.03)

    def test_drop_delay_error_and_auto_drop_delay(self):
        r = Rig(seed=11)
        r.ready()
        A = r.A
        true_dd = r.v('truth.drop_delay_drops')
        self.assertGreater(abs(true_dd - 44.0), 0.05)                             # this instrument differs from the factory default
        r.tube('acc', [dict(bead_id='accudrop_bead', volume_m3=1e-6)], 0.0)
        r.call(A, 'sort.auto_drop_delay', 'PLATES_OFF')
        r.call(A, 'deflection.set', voltage_on=True)
        r.call(A, 'sort.auto_drop_delay', 'ACCUDROP_BEADS_REQUIRED')
        r.call(A, 'tube.install', tube_id='acc')
        r.call(A, 'sample.load')
        r.call(A, 'sort.auto_drop_delay', 'NOT_ACQUIRING')                        # the port is still rising
        r.step(5)
        op = r.call(A, 'sort.auto_drop_delay')['operation_id']
        seen = []
        for _ in range(19):
            r.step(5.0)
            seen.append(r.v('accudrop.left_stream_fraction'))
        r.step(4.5)
        self.assertEqual(A.poll(op)['status'], 'running')
        r.step(1.0)                                                                 # 7 + 13 points x 5 s = 100 s
        self.assertGreater(max(x for x in seen if x is not None), 0.7)                # the camera sees the beads move into the left stream
        self.assertLess(min(x for x in seen if x is not None), 0.3)
        p = A.poll(op)
        self.assertEqual(p['status'], 'succeeded')
        self.assertAlmostEqual(r.v('drop_delay.drops'), r.v('truth.drop_delay_drops'), delta=0.1)
        # manual Accudrop check: fine tune mode, left stream fraction at the found delay vs. one drop off
        r.call(A, 'gate.define', gate_id='all', kind='interval', x='FSC-A', x_range=[-2000.0, 262143.0])
        r.install('2way_12x75')
        r.call(A, 'sort.layout', layout_id='acc', device='2way_12x75', precision_mode='fine_tune', targets={'left': 'all'})
        r.call(A, 'deflection.set', optical_filter_in=True)
        sop = r.call(A, 'sort.start', layout_id='acc')['operation_id']
        r.step(3)
        good = r.v('accudrop.left_stream_fraction')
        r.call(A, 'drop_delay.set', drops=r.v('drop_delay.drops') + 0.5)
        r.step(2)
        half = r.v('accudrop.left_stream_fraction')
        self.assertGreater(good, 0.93)
        self.assertAlmostEqual(half, 0.5, delta=0.12)                            # fine tune: 1 - |error|
        r.R.step(0.0)
        A.cancel(sop, 'c1')
        self.assertEqual(A.poll(sop)['status'], 'cancelled')

    def test_single_cell_plate_sort_counts(self):
        r = Rig()
        r.ready()
        set_true_drop_delay(r)
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.call(r.A, 'acquisition.flow_rate', setting=1.0)
        r.load('t')
        r.call(r.A, 'gate.define', gate_id='pos', kind='interval', x='B530_30-A', x_range=[3000.0, 262143.0])
        r.call(r.A, 'door.set', door='sort_chamber', angle_rad=1.6)
        r.call(r.A, 'collection.install', device='plate_96', prefill_m3=1e-7)
        r.call(r.A, 'deflection.set', voltage_on=True)
        r.call(r.A, 'sort.layout', layout_id='pl', device='plate_96', precision_mode='single_cell', targets={'plate': 'pos'}, events_per_well=1, wells=['A1', 'A2', 'A3', 'B1'])
        r.call(r.A, 'sort.start', 'DOOR_OPEN', layout_id='pl')
        r.call(r.A, 'door.set', door='sort_chamber', angle_rad=0.0)
        op = r.sort('pl', max_s=30)
        self.assertEqual(r.A.poll(op)['status'], 'succeeded')
        c = {w: x for w, x in r.collected().items() if sum(x.values()) > 0}
        self.assertEqual(sorted(c), ['A1', 'A2', 'A3', 'B1'])
        n1 = sum(1 for w in c.values() if sum(w.values()) == 1)
        self.assertGreaterEqual(n1, 3)                                            # single-cell mode: one cell per well (rare doublets aside)
        self.assertTrue(all(set(w) <= {'cd4'} for w in c.values()))

    def test_side_stream_aim_drawer_and_sub_threshold_contamination(self):
        r = Rig()
        r.ready()
        set_true_drop_delay(r)
        A = r.A
        r.provide('s', PBMC + [dict(population_id='debris', particle_id='human_platelet', concentration_per_m3=2e13)], vol=3e-6)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.call(A, 'acquisition.flow_rate', setting=3.0)
        r.load('t')
        two_way(r, n=3000)
        r.call(A, 'deflection.set', test_sort=True)
        ss = {x['stream']: x for x in r.call(A, 'sort.side_streams')['result']['streams']}
        self.assertLess(abs(ss['left']['offset_from_target_m']), 1e-3)
        self.assertTrue(r.call(A, 'sort.side_streams')['result']['centre_in_aspirator'])
        r.call(A, 'deflection.set', test_sort=False, side_stream_fraction={'left': 0.25})
        op = r.sort('two', max_s=60)
        c = r.collected()
        self.assertLess(sum(c.get('left', {}).values()), 0.1 * r.v('sort.counts')['left'])     # mis-aimed left stream misses the tube
        self.assertGreater(c['right'].get('debris', 0.0), 0.0)                    # platelets below the FSC threshold ride along unseen
        self.assertEqual(r.A.poll(op)['status'], 'succeeded')

    def test_clog_protection_and_partial_clog_contamination(self):
        r = Rig()
        r.ready()
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.load('t')
        two_way(r, n=10 ** 6)
        op = r.call(r.A, 'sort.start', layout_id='two')['operation_id']
        r.step(3)
        r.call(r.E, 'fault.inject', fault='clog_full', active=True)
        r.step(1)
        o = r.rd('stream.on', 'deflection.voltage_on', 'sort.drawer_open', 'sample.port_state', 'errors.active')
        self.assertEqual((o['stream.on'], o['deflection.voltage_on'], o['sort.drawer_open']), (False, False, False))   # documented Sweet Spot response
        self.assertEqual(o['sample.port_state'], 'unloading')
        self.assertIn('CLOG', o['errors.active'])
        self.assertEqual(r.A.poll(op)['status'], 'failed')
        self.assertEqual(r.A.poll(op)['result']['error'], 'CLOG')
        r.step(4)
        r.call(r.A, 'door.set', door='flow_cell', angle_rad=1.2)
        r.call(r.A, 'nozzle.install', nozzle='70')                                   # sonicated / new nozzle
        self.assertNotIn('CLOG', r.v('errors.active'))
        # a partial clog is not detected: the bent centre stream runs into the right tube
        r2 = Rig()
        r2.ready()
        r2.provide('s', PBMC)
        r2.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r2.load('t')
        two_way(r2, n=10 ** 6)
        r2.call(r2.A, 'sort.start', layout_id='two')
        r2.step(3)
        before = r2.collected().get('right', {}).get('cd4', 0.0)
        r2.call(r2.E, 'fault.inject', fault='clog_partial', active=True)
        r2.step(5)
        self.assertTrue(r2.v('stream.on'))
        self.assertGreater(r2.collected()['right'].get('cd4', 0.0), before + 200)

    def test_emergency_stop(self):
        r = Rig()
        r.ready(warm=False)
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.call(r.A, 'tube.install', tube_id='t')
        r.call(r.A, 'sample.load')
        r.step(1)
        self.assertTrue(r.v('lamp.estop_light'))                                  # lit while a tube is loading
        r.step(3)
        self.assertFalse(r.v('lamp.estop_light'))
        r.call(r.A, 'deflection.set', voltage_on=True)
        r.call(r.A, 'safety.emergency_stop')
        o = r.rd('safety.estop_latched', 'safety.estop_position_m', 'stream.on', 'deflection.voltage_on', 'sample.port_state', 'lasers.on', 'power.enabled')
        self.assertEqual((o['safety.estop_latched'], o['stream.on'], o['deflection.voltage_on']), (True, False, False))
        self.assertAlmostEqual(o['safety.estop_position_m'], m.ESTOP_TRAVEL)
        self.assertTrue(o['power.enabled'] and all(o['lasers.on'].values()))        # lasers and main power stay on
        self.assertTrue(r.v('lamp.estop_light'))                                  # User's Guide p.42-43: lit while latched
        r.step(5)
        self.assertTrue(r.v('lamp.estop_light'))
        r.call(r.A, 'stream.set', 'EMERGENCY_STOP_LATCHED', on=True)
        r.call(r.A, 'safety.reset_emergency_stop')
        self.assertFalse(r.v('lamp.estop_light'))                                 # turning the button clockwise: light off
        r.call(r.A, 'stream.set', on=True)
        r.step(30)
        r.call(r.A, 'sample.load')                                                   # the tube stayed on the lowered port
        r.step(4)
        op = r.call(r.A, 'acquisition.record', record_id='e1', events=10 ** 5)['operation_id']
        r.step(1)
        r.call(r.B, 'safety.emergency_stop')                                          # another agent may always press the hardware button
        self.assertEqual((r.A.poll(op)['status'], r.A.poll(op)['result']['error']), ('failed', 'EMERGENCY_STOP'))
        self.assertTrue(any(e['code'] == 'EMERGENCY_STOP' and 'agent按下' in e['message_zh'] for e in r.call(r.A, 'events.read', since_s=0.0)['result']['events']))
        r.call(r.B, 'safety.reset_emergency_stop')


# ---------------------------------------------------------------- temperatures, cart warnings, CS&T, power
class Instrument(unittest.TestCase):
    def test_sample_and_collection_temperature(self):
        r = Rig()
        r.ready(warm=False, sweet=False)
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.load('t')
        r.call(r.A, 'acquisition.set', acquiring=False)
        r.call(r.A, 'sample.temperature', 'INVALID_ARGUMENT', setpoint_k='277.15')
        r.call(r.A, 'sample.temperature', 'INVALID_ARGUMENT', setpoint_k=280.0)       # only the four software set points
        r.call(r.A, 'sample.temperature', setpoint_k=277.15)
        r.call(r.A, 'collection.temperature', setpoint_k=277.15)
        r.step(2700)
        o = r.rd('sample.chamber_temperature_k', 'collection.temperature_k', 'truth.sample_temperature_k')
        self.assertLess(abs(o['sample.chamber_temperature_k'] - 277.15), 0.06 * (295.15 - 277.15))   # ~45 min (documented)
        self.assertLess(abs(o['collection.temperature_k'] - 277.15), 0.2)
        self.assertLess(o['truth.sample_temperature_k'], 279.0)

    def test_level_warning_auto_stream_off(self):
        r = Rig()
        r.ready(warm=False, sweet=False)
        r.R.cart['waste'] = 0.0095
        r.step(1)
        self.assertIn('WASTE_FULL', r.v('warnings.active'))
        r.step(600)
        r.call(r.A, 'warnings.dismiss')
        r.step(600)
        self.assertTrue(r.v('stream.on'))
        r.step(900)
        self.assertFalse(r.v('stream.on'))                                       # documented: 15 min without dismissal turns the stream off
        r.call(r.A, 'stream.set', on=True)                                           # still full: a fresh warning period, not an immediate stop
        r.step(60)
        self.assertTrue(r.v('stream.on'))
        r.step(900)
        self.assertFalse(r.v('stream.on'))
        r.R.cart['waste'] = 0.0101                                                    # overflow
        r.call(r.A, 'stream.set', on=True)
        r.step(1)
        self.assertIn('WASTE_OVERFLOW', r.v('errors.active'))
        r.call(r.A, 'stream.set', on=False)
        r.call(r.A, 'cart.empty_waste', bleach_m3=0.001)
        self.assertAlmostEqual(r.v('cart.waste_level_fraction'), 0.1, delta=1e-9)
        self.assertNotIn('WASTE_OVERFLOW', r.v('errors.active'))
        r.R.cart['sheath'] = 1.2e-5                                                   # runs dry (~0.1 ml/s at 70 psi)
        r.call(r.A, 'stream.set', on=True)
        r.step(150)
        self.assertIn('SHEATH_EMPTY', r.v('errors.active'))
        self.assertFalse(r.v('stream.on'))
        r.call(r.A, 'cart.refill_sheath', volume_m3=0.009)
        self.assertNotIn('SHEATH_EMPTY', r.v('errors.active'))

    def test_cst_check(self):
        r = Rig()
        r.ready()
        r.tube('cst', [dict(bead_id='cst_bead_bright', volume_m3=2e-7), dict(bead_id='cst_bead_mid', volume_m3=2e-7), dict(bead_id='cst_bead_dim', volume_m3=2e-7)], 4e-7)
        r.call(r.A, 'cst.performance_check', 'CST_BEADS_REQUIRED')
        r.call(r.A, 'tube.install', tube_id='cst')
        r.call(r.A, 'sample.load')
        r.call(r.A, 'cst.performance_check', 'NOT_ACQUIRING')
        r.step(5)
        r.call(r.A, 'cytometer.settings', laser_delays_s={'violet': 30e-6})
        op = r.call(r.A, 'cst.performance_check')['operation_id']
        r.step(301)
        self.assertEqual(r.A.poll(op)['status'], 'succeeded')
        self.assertEqual(r.v('cst.status'), 'passed')
        self.assertAlmostEqual(r.v('cytometer.laser_delays_s')['violet'], r.v('truth.laser_delays_s')['violet'], delta=1e-6)
        r.call(r.E, 'fault.inject', fault='laser_failure_red', active=True)
        op = r.call(r.A, 'cst.performance_check')['operation_id']
        r.step(301)
        self.assertEqual(r.v('cst.status'), 'failed')
        self.assertIn('LASER_FAILURE_RED', r.v('errors.active'))

    def test_power_loss_and_restore(self):
        r = Rig()
        r.ready(warm=True, sweet=True)
        r.call(r.E, 'fault.inject', fault='power_loss', active=True)
        o = r.rd('power.enabled', 'stream.on', 'lasers.on', 'fluidics.state', 'cart.air_pressure_pa')
        self.assertFalse(o['power.enabled'] or o['stream.on'] or any(o['lasers.on'].values()))
        self.assertEqual(o['fluidics.state'], 'ready')
        r.call(r.E, 'fault.inject', fault='power_loss', active=False)
        self.assertTrue(r.v('power.enabled'))
        self.assertFalse(any(r.v('lasers.warm').values()))                        # lasers must be switched on and warmed again

    def test_settling_without_agitation(self):
        r = Rig()
        r.ready(sweet=False)
        r.tube('si', [dict(bead_id='silica_bead_1um', volume_m3=1e-6)], 0.0)
        tb = r.R.tubes['si']
        tb['settle'] = {k: 0.0 for k in tb['counts']}
        k = list(tb['counts'])[0]
        r.R.popspec[k]['diameter_m'] = 2e-5                                       # heavy particle: settles in minutes
        r.R.popspec[k]['density_kg_m3'] = 2200.0
        r.load('si')
        r.step(2)
        rate0 = r.v('acquisition.threshold_rate_eps')
        r.step(900)
        self.assertLess(r.v('acquisition.threshold_rate_eps'), 0.1 * rate0)
        r.call(r.A, 'sample.agitation', 'INVALID_ARGUMENT', speed='250_rpm')
        r.call(r.A, 'sample.agitation', speed='300_rpm')
        self.assertAlmostEqual(r.v('sample.agitation_rate_hz'), 5.0)
        r.step(120)
        self.assertLess(r.v('acquisition.threshold_rate_eps'), 0.1 * rate0)      # facility: agitation even at 300 rpm is pretty ineffective
        d0 = r.call(r.E, 'tube.inspect', tube_id='si')['result']['settled_depth_m'][k]
        r.step(10)
        d1 = r.call(r.E, 'tube.inspect', tube_id='si')['result']['settled_depth_m'][k]
        v_s = 2.0 / 9.0 * (2200.0 - m.water_density(r.R.tubes['si']['T'])) * 9.80665 * 1e-10 / m.water_viscosity(r.R.tubes['si']['T'])
        self.assertLess(d1 - d0, 0.45 * v_s * 10.0)                                 # agitation only slows the settling front (40 % of Stokes)
        self.assertGreater(d1 - d0, 0.35 * v_s * 10.0)                              # ... and does not stop it (no resuspension)
        r.call(r.A, 'tube.mix', 'TUBE_NOT_ON_BENCH', tube_id='si')
        r.unload()
        r.call(r.A, 'tube.mix', tube_id='si')                                        # pause, take the tube out and mix it (facility advice)
        self.assertEqual(max(r.call(r.E, 'tube.inspect', tube_id='si')['result']['settled_depth_m'].values()), 0.0)
        r.load('si', wait=5)
        self.assertGreater(r.v('acquisition.threshold_rate_eps'), 0.5 * rate0)


class ReviewRound1(unittest.TestCase):
    def test_unload_ends_sort_and_records(self):
        r = Rig()
        r.ready()
        set_true_drop_delay(r)
        r.provide('s', PBMC, vol=4e-6)
        r.tube('t', [dict(sample_id='s', volume_m3=1.5e-6)], 5e-7)
        r.tube('t2', [dict(sample_id='s', volume_m3=1.5e-6)], 5e-7)
        r.load('t')
        two_way(r, n=10 ** 6)
        op = r.call(r.A, 'sort.start', layout_id='two')['operation_id']
        rec = r.call(r.A, 'acquisition.record', record_id='rr', events=10 ** 5)['operation_id']
        r.step(3)
        n0 = r.v('sort.counts')['left']
        self.assertGreater(n0, 100)
        r.call(r.A, 'sample.unload')
        o = r.rd('sort.state', 'sort.drawer_open', 'operations.running')
        self.assertEqual((o['sort.state'], o['sort.drawer_open']), ('stopped', False))
        self.assertEqual((r.A.poll(op)['status'], r.A.poll(op)['result']['error']), ('failed', 'SAMPLE_UNLOADED'))
        self.assertEqual(r.A.poll(rec)['status'], 'failed')
        r.step(4)
        r.call(r.A, 'tube.remove')
        r.load('t2')
        r.step(3)
        self.assertEqual(r.v('sort.counts')['left'], n0)                           # nothing continues on the next tube
        op2 = r.call(r.A, 'sort.start', layout_id='two')['operation_id']
        r.step(2)
        self.assertLess(r.v('sort.counts')['left'], n0)                            # a new sort counts from zero
        r.A.cancel(op2, 'c2')

    def test_overflow_spill_ledger_and_truth_event(self):
        r = Rig()
        r.ready()
        set_true_drop_delay(r)
        r.provide('s', PBMC, vol=4e-6)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.load('t')
        two_way(r, n=10 ** 6)
        r.call(r.A, 'door.set', door='sort_chamber', angle_rad=1.6)
        r.call(r.A, 'collection.remove')
        r.call(r.A, 'collection.install', device='2way_12x75', prefill_m3=5.0e-6)      # full tubes
        r.call(r.A, 'door.set', door='sort_chamber', angle_rad=0.0)
        led0 = r.call(r.E, 'ledger.read')['result']['ledger']
        op = r.call(r.A, 'sort.start', layout_id='two')['operation_id']
        r.step(5)
        led = {k: v - led0[k] for k, v in r.call(r.E, 'ledger.read')['result']['ledger'].items()}
        self.assertGreater(led['particles_spilled_from_collection'], 0.0)
        resid = led['particles_drawn'] - led['particles_sorted'] - led['particles_to_waste'] - led['particles_lost_off_target'] - led['particles_leaked_into_collection']
        self.assertAlmostEqual(resid, 0.0, delta=1e-6)
        tot = sum(sum(x.values()) for x in r.collected().values())
        self.assertAlmostEqual(tot, led['particles_sorted'] - led['particles_spilled_from_collection'], delta=1e-6)
        self.assertAlmostEqual(led['collection_spilled_m3'], led['collection_added_m3'], delta=1e-12)   # full tubes: every added drop spills over
        frac = led['collection_spilled_m3'] / (2 * 5.0e-6)
        self.assertLess(led['particles_spilled_from_collection'], 3.0 * frac * tot + 30.0)     # proportional (binomial), not >= 1 per population per tick
        ev_a = [e for e in r.call(r.A, 'events.read', since_s=0.0)['result']['events'] if e['code'] == 'COLLECTION_OVERFLOW']
        ev_e = [e for e in r.call(r.E, 'events.read', since_s=0.0)['result']['events'] if e['code'] == 'COLLECTION_OVERFLOW']
        self.assertEqual(ev_a, [])                                                  # truth: the instrument has no level sensor
        self.assertEqual(len(ev_e), 2)                                              # once per location
        r.A.cancel(op, 'c3')

    def test_custom_non_sorting_setup_and_plates(self):
        r = Rig()
        r.call(r.A, 'power.press_switch')
        for L in m.LASERS:
            r.call(r.A, 'lasers.set', laser=L, enabled=True)
        r.call(r.A, 'sort_setup.select', preset='70_custom')                       # Table 3-1 Custom: 70 um non-sorting, high sensitivity
        o = r.rd('sheath.pressure_setpoint_pa', 'breakoff.frequency_hz', 'breakoff.amplitude_v', 'deflection.plate_voltage_v', 'cytometer.laser_delays_s',
                 'cytometer.window_extension_s', 'cytometer.area_scaling', 'deflection.side_stream_fraction', 'breakoff.attenuation')
        self.assertAlmostEqual(o['sheath.pressure_setpoint_pa'] / PSI, 20.0, places=3)
        self.assertEqual((o['breakoff.frequency_hz'], o['breakoff.amplitude_v'], o['deflection.plate_voltage_v'], o['breakoff.attenuation']), (30000.0, 10.0, 0.0, False))
        self.assertAlmostEqual(o['cytometer.laser_delays_s']['violet'], 82e-6)
        self.assertAlmostEqual(o['cytometer.laser_delays_s']['red'], -82e-6)
        self.assertAlmostEqual(o['cytometer.window_extension_s'], 4e-6)
        self.assertEqual((o['cytometer.area_scaling']['blue'], o['cytometer.area_scaling']['red'], o['cytometer.area_scaling']['violet']), (0.75, 0.5, 0.25))
        self.assertEqual(set(o['deflection.side_stream_fraction'].values()), {0.0})
        r.call(r.A, 'fluidics.startup')
        r.step(m.STARTUP_S + 1)
        r.call(r.A, 'stream.set', on=True)
        r.step(30)
        o = r.rd('truth.laser_delays_s', 'truth.jet_speed_m_s', 'breakoff.drop1_px')
        self.assertAlmostEqual(o['truth.laser_delays_s']['violet'], 82e-6, delta=1e-6)   # the fitted flow reproduces the documented delay
        self.assertLess(o['truth.jet_speed_m_s'], 0.5 * 28.0)
        hi = Rig()
        hi.ready(sweet=False)
        rates = {}
        for name, rig in (('custom', r), ('high', hi)):
            rig.tube('b', [dict(bead_id='ps_bead_3um', volume_m3=2e-7, stains=[dict(dye='fluorescein_fitc', molecules=300.0, cv=0.02)])], 8e-7)
            rig.call(rig.A, 'cytometer.settings', voltages={'B530_30': 550.0})
            rig.step(m.WARMUP_S if name == 'custom' else 0.0)
            rig.load('b', wait=6)
            rig.record('dimb', 2000)
            rates[name] = rig.stats(source='dimb', params=['B530_30-A'])['rows'][0]['parameters'][0]['median']
        self.assertGreater(rates['custom'], 1.8 * rates['high'])                   # ~2.3 x longer transit at 20 psi: more photons per particle
        # ACDU 24-well plate: one cell per well
        h = Rig()
        h.ready()
        set_true_drop_delay(h)
        h.provide('s', PBMC)
        h.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        h.call(h.A, 'acquisition.flow_rate', setting=1.0)
        h.load('t')
        h.call(h.A, 'gate.define', gate_id='pos', kind='interval', x='B530_30-A', x_range=[3000.0, 262143.0])
        h.call(h.A, 'door.set', door='sort_chamber', angle_rad=1.6)
        h.call(h.A, 'collection.install', device='plate_24', prefill_m3=5e-7)
        h.call(h.A, 'door.set', door='sort_chamber', angle_rad=0.0)
        h.call(h.A, 'deflection.set', voltage_on=True)
        h.call(h.A, 'sort.layout', layout_id='p24', device='plate_24', precision_mode='single_cell', targets={'plate': 'pos'}, events_per_well=2, wells=['A1', 'B6', 'D6'])
        op = h.sort('p24', max_s=30)
        self.assertEqual(h.A.poll(op)['status'], 'succeeded')
        W3 = ('A1', 'B6', 'D6')
        v_single = sum(x['volume_m3'] - 5e-7 for x in h.call(h.E, 'collection.inspect')['result']['locations'] if x['location'] in W3)
        h.call(h.A, 'door.set', door='sort_chamber', angle_rad=1.6)
        h.call(h.A, 'collection.remove')
        h.call(h.A, 'collection.install', device='plate_24', prefill_m3=5e-7)
        h.call(h.A, 'door.set', door='sort_chamber', angle_rad=0.0)
        h.call(h.A, 'sort.layout', layout_id='p24y', device='plate_24', precision_mode='purity', targets={'plate': 'pos'}, events_per_well=40, wells=['A1', 'B6', 'D6'])
        op2 = h.sort('p24y', max_s=60)
        self.assertEqual(h.A.poll(op2)['status'], 'succeeded')
        v_yield = sum(x['volume_m3'] - 5e-7 for x in h.call(h.E, 'collection.inspect')['result']['locations'] if x['location'] in W3)
        self.assertGreater(v_yield / 120.0, 1.5 * v_single / 6.0)                  # purity mode (yield mask 32): two drops per decision, single cell one
        c = {w: sum(x.values()) for w, x in h.collected().items() if sum(x.values()) > 0}
        self.assertEqual(sorted(c), ['A1', 'B6', 'D6'])

    def test_rate_window_and_si_units(self):
        r = Rig()
        r.ready(warm=False, sweet=False)
        ob = r.A.read(instance_id=r.I, state_ids=['acquisition.threshold_rate_eps', 'sort.sort_rate_eps', 'sample.agitation_rate_hz'])['observations']
        w = ob['acquisition.threshold_rate_eps']['sample_window_s']
        self.assertEqual(len(w), 2)
        self.assertAlmostEqual(w[1] - w[0], 1.0)
        self.assertAlmostEqual(w[1], r.R.t, delta=0.1)
        self.assertEqual(ob['sample.agitation_rate_hz']['unit'], '1/s')
        ob2 = r.A.read(instance_id=r.I, state_ids=['acquisition.events_processed'])['observations']['acquisition.events_processed']
        self.assertIsNone(ob2['sample_window_s'])                                  # a running total, not a 1-s rate
        out = r.call(r.A, 'amo.set', enabled=True, mode='rapid')['result']
        self.assertAlmostEqual(out['chamber_exchange_rate_per_s'], 69.0 / 60.0)
        meta = m.export_bd_facsariaiii_contract_metadata()
        units = {k: v['unit'] for k, v in meta['state_slots'].items()}
        self.assertFalse([k for k, u in units.items() if u in ('1/min', 'SI', '%')])
        params = meta['mechanisms']['flow_sorting_process']['parameters']
        self.assertFalse([k for k, p in params.items() if p['unit'] in ('1/min', 'SI', '%')])


# ---------------------------------------------------------------- framework
class Framework(unittest.TestCase):
    def test_sessions_isolation_and_idempotency(self):
        r = Rig()
        A, B, E = r.A, r.B, r.E
        r.call(A, 'fault.inject', 'ACCESS_DENIED', fault='clog_full', active=True)
        with self.assertRaises(m.ActionRejected):
            A.read(instance_id=r.I, state_ids=['truth.faults'])
        r.ready(warm=False, sweet=False)
        r.provide('s', PBMC)
        r.tube('ta', [dict(sample_id='s', volume_m3=1e-6)], 0.0)
        r.call(B, 'tube.install', 'ACCESS_DENIED', tube_id='ta')
        self.assertNotIn('ta', B.read(instance_id=r.I, state_ids=['tubes.on_bench'])['observations']['tubes.on_bench']['value'])
        r.load('ta')
        op = r.call(A, 'acquisition.record', record_id='ra', events=100000)['operation_id']
        r.step(1)
        r.call(B, 'stream.set', 'ACCESS_DENIED', on=False)                       # another agent's operation is running
        r.call(B, 'statistics.read', 'ACCESS_DENIED', source='ra')
        self.assertTrue(B.read(instance_id=r.I, state_ids=['operation.started_by_other_agent'])['observations']['operation.started_by_other_agent']['value'])
        with self.assertRaises(m.ActionRejected):
            B.poll(op)
        # idempotency
        r1 = A.invoke(instance_id=r.I, capability_id='drop_delay.set', arguments=dict(drops=43.5), request_id='same')
        r2 = A.invoke(instance_id=r.I, capability_id='drop_delay.set', arguments=dict(drops=43.5), request_id='same')
        self.assertEqual(r1, r2)
        r3 = A.invoke(instance_id=r.I, capability_id='drop_delay.set', arguments=dict(drops=42.0), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')
        SEEN.add(('drop_delay.set', 'REQUEST_CONFLICT'))
        A.cancel(op, 'cx')
        self.assertEqual(A.poll(op)['status'], 'cancelled')
        r.call(A, 'laser.uv_355nm', 'UNSUPPORTED')

    def test_atomic_rejection(self):
        r = Rig()
        r.ready(warm=False, sweet=False)
        r.provide('s', PBMC)
        before = copy.deepcopy(r.R.read('evaluator'))
        samples = copy.deepcopy(r.R.samples)
        r.call(r.A, 'tube.prepare', 'VOLUME_OUT_OF_RANGE', tube_id='x', tube_type='1.5ml', sources=[dict(sample_id='s', volume_m3=1e-6)], buffer_volume_m3=1e-6)
        r.call(r.A, 'tube.prepare', 'INSUFFICIENT_SAMPLE', tube_id='x', tube_type='15ml', sources=[dict(sample_id='s', volume_m3=2e-6), dict(sample_id='s', volume_m3=2e-6)])
        r.call(r.A, 'sort.layout', 'INVALID_LAYOUT', layout_id='l', device='2way_12x75', precision_mode='purity', targets={'far_left': 'x'})
        r.call(r.A, 'cytometer.settings', 'INVALID_THRESHOLD', thresholds=[dict(parameter='FSC', value=1.0), dict(parameter='FSC', value=2.0)])
        self.assertEqual(r.R.read('evaluator'), before)
        self.assertEqual(r.R.samples, samples)

    def test_step_partition_independence(self):
        out = []
        for parts in ([8.0], [0.5] * 16, [2.0, 0.25, 0.25, 0.5, 5.0]):
            r = Rig(seed=9)
            r.ready(warm=False, sweet=True)
            r.provide('s', PBMC)
            r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
            r.load('t', wait=0.0)
            for p in parts:
                r.R.step(p)
            out.append((r.R.read('evaluator'), r.R.ledger()))
        self.assertEqual(out[0], out[1])
        self.assertEqual(out[0], out[2])


class MoreRejections(unittest.TestCase):
    def test_preconditions(self):
        r = Rig()
        A = r.A
        r.call(A, 'lasers.set', 'POWER_OFF', laser='blue', enabled=True)
        r.ready(warm=False, sweet=False)
        r.provide('s', PBMC)
        r.call(A, 'sample.load', 'NO_TUBE')
        r.tube('t', [dict(sample_id='s', volume_m3=1e-6)], 0.0)
        r.tube('t2', [dict(sample_id='s', volume_m3=1e-6)], 0.0)
        r.call(A, 'tube.prepare', 'ITEM_EXISTS', tube_id='t', tube_type='12x75', sources=[dict(sample_id='s', volume_m3=1e-7)])
        r.call(A, 'tube.prepare', 'UNKNOWN_SAMPLE', tube_id='t3', tube_type='12x75', sources=[dict(sample_id='nope', volume_m3=1e-7)])
        r.call(A, 'tube.prepare', 'INVALID_SOURCE', tube_id='t3', tube_type='12x75', sources=[dict(volume_m3=1e-7)])
        r.call(A, 'tube.install', tube_id='t')
        r.call(A, 'tube.install', 'PORT_OCCUPIED', tube_id='t2')
        r.call(A, 'acquisition.record', 'NOT_ACQUIRING', record_id='x', events=10)
        r.call(A, 'collection.install', 'DOOR_CLOSED', device='2way_12x75')
        r.call(A, 'door.set', door='sort_chamber', angle_rad=0.3)
        r.call(A, 'amo.set', 'DOOR_OPEN', enabled=True)
        r.call(A, 'sort.start', 'UNKNOWN_LAYOUT', layout_id='none')
        r.call(A, 'sort.pause', 'NOT_SORTING')
        r.call(A, 'sweet_spot.set', enabled=True)
        r.call(A, 'sort_setup.select', 'STREAM_ON', preset='100')
        r.call(A, 'nozzle.install', 'STREAM_ON', nozzle='100')
        r.call(r.E, 'sample.provide', 'UNKNOWN_PARTICLE', sample_id='z', volume_m3=1e-6, populations=[dict(population_id='p', particle_id='unicorn', concentration_per_m3=1.0)])
        r.call(r.E, 'tube.inspect', 'UNKNOWN_ITEM', tube_id='zz')
        r.call(A, 'statistics.read', 'UNKNOWN_RECORD', source='nope')
        r.call(A, 'sample.unload', 'NOT_LOADED')


class Coverage(unittest.TestCase):
    def test_remaining_capabilities(self):
        r = Rig()
        A, E = r.A, r.E
        r.call(A, 'power.set', enabled=True)
        r.call(A, 'fluidics.startup')
        r.step(m.STARTUP_S + 1)
        op = r.call(A, 'cleaning.run', mode='clean_flow_cell')['operation_id']
        self.assertEqual(r.v('fluidics.state'), 'cleaning')
        r.step(m.CLEAN_S['clean_flow_cell'] + 1)
        self.assertEqual(A.poll(op)['status'], 'succeeded')
        r.call(A, 'door.set', door='flow_cell', angle_rad=1.2)
        r.call(A, 'nozzle.install', nozzle='closed_loop')
        op = r.call(A, 'cleaning.run', mode='prepare_aseptic_sort')['operation_id']
        A.cancel(op, 'cc')
        self.assertEqual(r.v('fluidics.state'), 'ready')
        r.call(A, 'nozzle.install', nozzle='70')
        r.call(A, 'door.set', door='flow_cell', angle_rad=0.0)
        r.call(A, 'cart.refill_cleaning')
        r.call(A, 'cart.refill_sheath', volume_m3=float(0.010 - r.v('truth.cart_sheath_m3')))
        r.call(A, 'cart.refill_sheath', 'CONTAINER_OVERFLOW', volume_m3=0.001)
        r.call(A, 'sheath.set_pressure', pressure_pa=60 * PSI)
        self.assertEqual(r.v('sort_setup.preset'), 'custom')
        r.call(A, 'sort_setup.select', preset='70_high')
        r.call(A, 'stream.set', on=True)
        r.step(30)
        r.call(A, 'amo.set', enabled=True, mode='rapid')
        self.assertTrue(r.v('amo.on'))
        for L in m.LASERS:
            r.call(A, 'lasers.set', laser=L, enabled=True)
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.load('t')
        rid = r.record('r1', 2000)['result']['record_id']
        lst = r.call(A, 'records.list')['result']['records']
        self.assertEqual(lst[0]['events'], 2000)
        ex = r.call(A, 'data.export', record_id=rid, kind='fcs_csv')['result']
        self.assertEqual(ex['rows'], 2000)
        self.assertTrue(r.R.exports['r1.csv'].startswith('FSC-A,FSC-H,FSC-W'))
        ev = r.call(A, 'events.read', since_s=0.0)['result']['events']
        self.assertTrue(any(e['code'] == 'RECORD_DONE' for e in ev))
        pv = r.call(A, 'provenance.read')['result']
        self.assertEqual(len(pv['detectors']), 10)
        two_way(r, n=10 ** 6)
        r.call(A, 'sweet_spot.set', enabled=True)
        r.step(0.2)
        self.assertTrue(r.v('sweet_spot.paused'))                                 # Gap still off target: sorting would pause
        r.step(60)
        self.assertFalse(r.v('sweet_spot.paused'))
        tb = r.call(E, 'tube.inspect', tube_id='t')['result']
        self.assertLess(tb['volume_m3'], 3e-6)
        sop = r.call(A, 'sort.start', layout_id='two')['operation_id']
        r.step(3)
        r.call(A, 'sort.pause')
        n0 = r.v('sort.counts')['left']
        r.step(2)
        self.assertEqual(r.v('sort.counts')['left'], n0)
        r.call(A, 'sort.resume')
        r.step(2)
        self.assertGreater(r.v('sort.counts')['left'], n0)
        A.cancel(sop, 'cs')
        self.assertEqual(r.v('sort.state'), 'stopped')
        r.call(A, 'drawer.set', open=True)
        r.step(2)
        self.assertAlmostEqual(r.v('sort.drawer_position_m'), m.DRAWER_TRAVEL)
        missing = sorted(k for k in m.CAPS if k not in m.UNSUPPORTED and k not in PROCESS_EVIDENCE)
        self.assertLessEqual(len(missing), 40)


class ZDeclaredEffects(unittest.TestCase):
    """Immediate state changes stay within EFFECTS; changes after a step stay within EFFECTS | ON_STEP (compared with the same host not called)."""

    def _check(self, rig, cap, args, session=None):
        meta = m.export_bd_facsariaiii_contract_metadata()['capabilities'][cap]
        eff = {e['state_id'] for e in meta['state_effects'] if e['timing'] == 'immediate'}
        on_step = {e['state_id'] for e in meta['state_effects']}
        base = copy.deepcopy(rig.R)
        s0 = rig.R.read('evaluator')
        (session or rig.A).invoke(instance_id=rig.I, capability_id=cap, arguments=args, request_id=f'eff_{cap}')
        s1 = rig.R.read('evaluator')
        changed = {k for k in s0 if s0[k] != s1[k]} - {'operation.started_by_other_agent'}
        self.assertTrue(changed <= eff, (cap, sorted(changed - eff)))
        for dt in (2.0, 28.0):
            rig.R.step(dt)
            base.step(dt)
            a, b = rig.R.read('evaluator'), base.read('evaluator')
            diff = {k for k in a if a[k] != b[k]}
            self.assertTrue(diff <= (eff | on_step), (cap, dt, sorted(diff - (eff | on_step))))

    def test_declared_effects(self):
        r = Rig()
        r.ready(warm=False, sweet=False)
        r.provide('s', PBMC)
        r.tube('t', [dict(sample_id='s', volume_m3=2e-6)], 1e-6)
        r.tube('t2', [dict(sample_id='s', volume_m3=5e-7)], 1e-6)
        for cap, args in [('lasers.set', dict(laser='red', enabled=False)), ('breakoff.set', dict(amplitude_v=55.0)), ('drop_delay.set', dict(drops=43.0)),
                          ('deflection.set', dict(voltage_on=True, plate_voltage_v=5000.0)), ('acquisition.flow_rate', dict(setting=3.0)),
                          ('cytometer.settings', dict(voltages={'B530_30': 550.0})), ('sample.chamber_light', {}), ('tube.install', dict(tube_id='t')),
                          ('sample.load', {}), ('sweet_spot.set', dict(enabled=True)), ('door.set', dict(door='side', angle_rad=1.0)),
                          ('gate.define', dict(gate_id='g1', kind='interval', x='FSC-A', x_range=[1000.0, 262143.0])),
                          ('compensation.set', dict(enabled=True)), ('sample.agitation', dict(speed='100_rpm')), ('drawer.set', dict(open=True)),
                          ('acquisition.set', dict(acquiring=False)), ('acquisition.set', dict(acquiring=True)), ('sample.temperature', dict(setpoint_k=310.15)),
                          ('collection.temperature', dict(setpoint_k=277.15)), ('amo.set', dict(enabled=True)), ('warnings.dismiss', {}),
                          ('sort.layout', dict(layout_id='lx', device='2way_12x75', precision_mode='purity', targets={'left': 'g1'})),
                          ('safety.emergency_stop', {}), ('safety.reset_emergency_stop', {}), ('tube.mix', dict(tube_id='t2')),
                          ('cart.refill_cleaning', {})]:
            self._check(r, cap, args)
        self._check(r, 'fault.inject', dict(fault='power_loss', active=True), session=r.E)


class ZZRejectionCodes(unittest.TestCase):
    def test_codes_are_declared(self):
        meta = m.export_bd_facsariaiii_contract_metadata()['capabilities']
        missing = [(c, e) for c, e in sorted(SEEN) if e not in {x['code'] for x in meta[c]['errors']}]
        missing += [(c, e) for c, es in sorted(REJECTIONS.items()) for e in sorted(es) if e not in {x['code'] for x in meta[c]['errors']}]
        self.assertEqual(missing, [])
        self.assertGreaterEqual(len(SEEN), 30)
        CHECKS.append(dict(check='rejection_codes_declared', status='passed', codes=len(SEEN)))

    def test_zz_every_implemented_capability_called(self):
        missing = sorted(k for k in m.CAPS if k not in m.UNSUPPORTED and k not in PROCESS_EVIDENCE)
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
