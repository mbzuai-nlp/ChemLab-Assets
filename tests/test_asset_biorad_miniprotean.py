"""tests for CHEM-164 biorad_miniprotean: python3 -m unittest tests/test_asset_biorad_miniprotean.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances are
isolated, agent/evaluator truth isolation, conservation (charge, electrical energy -> heat, buffer volume, protein mass), real return values validated
against the declared schemas (asset_tool validate_value), asynchronous completion and cancellation, host-step independence, declared immediate and
on_step effects over scenario states; plus the electrophoresis physics against the manufacturer data: TGX SDS-PAGE start / final current per gel, run
time and buffer temperature at 100 / 200 / 300 V (bulletin 1658100 Tables 3.1-3.2), 200 V hand-cast Tris-HCl runs of 35-45 min (Tetra manual),
Precision Plus bands at the chart positions (TGX gels), 700 / 1000 mL buffer for 2 / 4 gels; PowerPac regulation (CV, CC crossover, 75 W limit), timer,
keys, display accuracy, error codes E1 / E2 / E7 / E9 / E5; cell circuit rules (lid interlock, leads, tape, buffer dam, companion module overheating),
reducing vs non-reducing samples, run-off, reversed polarity, overload broadening, smile, native PAGE of basic proteins, well overflow, dye-front
observation and prestained markers.
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
from asset_runtime import biorad_miniprotean as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
INST = 'electrophoresis_01'
SDS = 'tris_glycine_sds_1x'
TGX = 'tgx_4_20_10w_30ul'


def _meta():
    if not _META:
        _META.update(rt.export_biorad_miniprotean_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.BioradMiniproteanRuntime.invoke


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


rt.BioradMiniproteanRuntime.invoke = _instrumented_invoke


def check(cid, **detail):
    CHECKS.append(dict(check=cid, status='passed', **detail))


def configured(seed=1, **plant):
    h = rt.BioradMiniproteanRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def assembled(gel=TGX, n=2, buffer=SDS, outer=None, inner=None, samples=True, tape=False, dam=False, seed=1, temperature_k=295.15, **plant):
    """Cell with n gels (positions 1..n; companion module for n > 2), buffer, marker in lane 1 and a test sample in lane 2 of gel 1, lid on,
    leads connected, supply on and programmed 200 V / 400 mA untimed."""
    h = configured(seed, **plant)
    h.invoke('leads.disconnect')
    h.invoke('lid.remove')
    if n > 2:
        h.invoke('companion.install')
    for i in range(n):
        gid = f'g{i + 1}'
        h.invoke('gel.provide', gel_id=gid, gel_type=gel)
        h.invoke('gel.prepare', gel_id=gid, remove_comb=True, remove_tape=not tape, rinse_wells=True)
        h.invoke('gel.install', item=gid, position=i + 1)
    if dam:
        h.invoke('gel.install', item='buffer_dam', position=n + 1)
    mods = 2 if n > 2 else 1
    h.invoke('buffer.fill', chamber='inner', buffer_id=buffer, volume_m3=inner or 150e-6 * mods, temperature_k=temperature_k)
    h.invoke('buffer.fill', chamber='outer', buffer_id=buffer, volume_m3=outer or (680e-6 if n > 2 else 550e-6), temperature_k=temperature_k)
    if samples:
        h.invoke('sample.prepare', sample_id='marker', proteins_kg={}, marker='precision_plus_unstained', marker_volume_m3=10e-9, sample_buffer='laemmli_2x_reducing',
                 heat='95C_5min', volume_m3=20e-9)
        h.invoke('sample.load', sample_id='marker', position=1, lane=1, volume_m3=10e-9)
        h.invoke('sample.prepare', sample_id='s', proteins_kg=dict(bsa=2e-09, insulin_bovine=2e-09, lysozyme_hen=1e-09), marker=None, marker_volume_m3=0.0,
                 sample_buffer='laemmli_2x_reducing', heat='95C_5min', volume_m3=20e-9)
        h.invoke('sample.load', sample_id='s', position=1, lane=2, volume_m3=10e-9)
    h.invoke('lid.place')
    h.invoke('leads.connect', polarity='normal')
    h.invoke('power.set', enabled=True)
    h.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
    return h


def run_to_front(h, gel='g1', dt=5.0, tmax=4 * 3600.0):
    """Start and run until the dye front of `gel` reaches the reference line; returns (start current per gel A, final, run time s)."""
    n = len(h._installed_gels())
    h.invoke('psu.start')
    t0 = h.t
    h.step(dt)
    I0 = h.I / n
    while h.gels[gel]['s'] < h.gels[gel]['L_run'] - 1e-6 and h.t - t0 < tmax and h.running:
        h.step(dt)
    return I0, h.I / n, h.t - t0


class ContractAndLibraries(unittest.TestCase):
    def test_module_identity_and_export(self):
        meta = rt.export_biorad_miniprotean_contract_metadata()
        self.assertEqual((rt.ASSET_TYPE_ID, rt.CATALOG_ID), ('instrument.biorad_miniprotean', 'CHEM-164'))
        for k, c in meta['capabilities'].items():
            for sname in ('input_schema', 'output_schema'):
                self.assertEqual(asset_tool.lint_schema(c[sname]) if hasattr(asset_tool, 'lint_schema') else [], [], (k, sname))
            self.assertEqual(asset_tool.validate_value(c['example_arguments'], c['input_schema']), [], k)
        self.assertEqual(set(meta['state_slots']), set(rt.SLOTS))
        for k, v in rt.SLOTS.items():
            self.assertFalse(v['origin'] == 'process_truth' and v['visibility'] == 'agent', k)
        check('export', capabilities=len(meta['capabilities']), slots=len(meta['state_slots']))

    def test_template_libraries(self):
        lib = rt.protein_lib()
        prot = {k: v for k, v in lib['substances'].items() if v['kind'] == 'protein'}
        self.assertGreaterEqual(len(prot), 50)
        self.assertAlmostEqual(prot['bsa']['mass_da'] / 1000, 66.43, delta=0.05)               # mature BSA (UniProt P02769 25-607)
        self.assertAlmostEqual(prot['lysozyme_hen']['mass_da'] / 1000, 14.31, delta=0.02)
        self.assertEqual(prot['insulin_bovine']['n_chains'], 2)
        self.assertEqual(prot['insulin_bovine']['interchain_disulfides'], 2)
        self.assertTrue(all(v['source'].startswith('UniProtKB') for v in prot.values()))
        self.assertGreaterEqual(len(rt.gel_lib()['gels']), 100)
        f = rt.model()['ferguson']
        self.assertLess(f['rms_mm'], 1.5)
        self.assertEqual(f['n_bands'], 53)                                               # Precision Plus bands of the six chart lanes
        check('libraries', proteins=len(prot), gels=len(rt.gel_lib()['gels']), ferguson_rms_mm=f['rms_mm'])


class Electrophoresis(unittest.TestCase):
    def test_manufacturer_run_conditions_tgx(self):
        """bulletin 1658100 Tables 3.1 / 3.2: per gel start / final current, run time, temperature at 100, 200 and 300 V (tolerance 10 %)."""
        rows = {}
        for V, outer, I0r, I1r, tr, Tr in ((100.0, 550e-6, (15, 20), (5, 10), (85, 95), (23, 27)), (200.0, 550e-6, (25, 50), (20, 31), (30, 40), (25, 35)),
                                           (300.0, 680e-6, (55, 75), (45, 70), (15, 20), None)):
            h = assembled(outer=outer)
            h.invoke('psu.program', constant='voltage', voltage_v=V, current_a=0.400, time_s=0.0, power_failure_detection=False)
            I0, I1, t = run_to_front(h)
            rows[V] = dict(I0_mA=I0 * 1e3, I1_mA=I1 * 1e3, t_min=t / 60, T_C=h.Tb - 273.15)
            self.assertTrue(0.9 * I0r[0] <= I0 * 1e3 <= 1.1 * I0r[1], (V, rows[V]))
            self.assertTrue(0.9 * I1r[0] <= I1 * 1e3 <= 1.1 * I1r[1], (V, rows[V]))
            self.assertTrue(0.9 * tr[0] <= t / 60 <= 1.1 * tr[1], (V, rows[V]))
            if Tr:
                self.assertTrue(Tr[0] - 1 <= h.Tb - 273.15 <= Tr[1], (V, rows[V]))
        check('manufacturer_tables', **{f'{int(k)}V': v for k, v in rows.items()})

    def test_handcast_tris_hcl_200V_35_to_45_min(self):
        """Tetra manual p.13: 35-45 min at 200 V for Tris-HCl gels, depending on the acrylamide percentage (every thickness)."""
        mins = {}
        for T in (4, 12, 17):
            for th in ('0p75', '1p00', '1p50'):
                h = assembled(gel=f'handcast_trishcl_{T}pct_{th}mm_10w')
                I0, I1, t = run_to_front(h)
                mins[f'{T}pct_{th}'] = t / 60
                self.assertTrue(35 <= t / 60 <= 45, (T, th, t / 60))
        self.assertLess(mins['4pct_1p00'], mins['12pct_1p00'])
        self.assertLess(mins['12pct_1p00'], mins['17pct_1p00'])
        check('handcast_200V', minutes=mins)

    def test_buffer_volumes_2_and_4_gels(self):
        two, four = assembled(n=2), assembled(n=4)
        self.assertAlmostEqual(two.read()['buffer.inner_m3'] + two.read()['buffer.outer_m3'], 700e-6, delta=1e-12)
        self.assertAlmostEqual(four.read()['buffer.inner_m3'] + four.read()['buffer.outer_m3'], 980e-6, delta=30e-6)
        I0, _, _ = run_to_front(four, gel='g3')
        self.assertTrue(25e-3 <= I0 <= 55e-3, I0)                                    # the per-gel current stays the same with 4 gels (manual: multiply)
        check('buffer_volumes', four_gel_current_per_gel_mA=I0 * 1e3)

    def test_precision_plus_bands_at_the_chart_positions(self):
        dig = json.loads((ROOT / 'outputs/expansion_batch03/t4_measure/biorad_miniprotean/migration_chart_digitized.json').read_text())
        worst = {}
        for gel, key in (('tgx_4_20_10w_30ul', '4–20%'), ('tgx_7.5_10w_30ul', '7.5%'), ('tgx_12_10w_30ul', '12%'), ('tgx_any_kd_10w_30ul', 'Any kD')):
            h = assembled(gel=gel)
            run_to_front(h)
            bands = {round(b['apparent_molar_mass_kg_mol'], 3): b for b in h.invoke('gel.inspect', gel_id='g1')['lanes'][0]['bands']}
            lane = next(x for x in dig['sets']['precision_plus_unstained'] if x['gel'] == key)
            err = [abs(bands[round(b['mw_kda'], 3)]['distance_m'] - (rt.STACK_LEN_M + b['rf'] * 0.062)) * 1e3 for b in lane['bands']]   # mm
            worst[key] = max(err)
            self.assertLess(max(err), 3.5, key)
        check('chart_positions', max_error_m={k: v * 1e-3 for k, v in worst.items()})

    def test_reducing_splits_disulfide_linked_chains(self):
        h = assembled(samples=False)
        for sid, buf, lane in (('red', 'laemmli_2x_reducing', 1), ('nonred', 'laemmli_2x_nonreducing', 2)):
            h.invoke('sample.prepare', sample_id=sid, proteins_kg=dict(insulin_bovine=3e-09, chymotrypsinogen_a_bovine=3e-09, concanavalin_a=3e-09), marker=None,
                     marker_volume_m3=0.0, sample_buffer=buf, heat='95C_5min', volume_m3=10e-9)
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        h.invoke('sample.load', sample_id='red', position=1, lane=1, volume_m3=10e-9)
        h.invoke('sample.load', sample_id='nonred', position=1, lane=2, volume_m3=10e-9)
        h.invoke('lid.place')
        h.invoke('leads.connect', polarity='normal')
        run_to_front(h, dt=5.0)
        h.invoke('psu.stop')
        lanes = h.invoke('gel.inspect', gel_id='g1')['lanes']
        red = {b['species']: b for b in lanes[0]['bands']}
        nonred = {b['species']: b for b in lanes[1]['bands']}
        self.assertEqual(sorted(k for k in red if k.startswith('insulin')), ['insulin_bovine:Insulin A chain', 'insulin_bovine:Insulin B chain'])   # split
        self.assertEqual([k for k in nonred if k.startswith('insulin')], ['insulin_bovine'])                  # held together by two disulfides
        for pid in ('chymotrypsinogen_a_bovine', 'concanavalin_a'):                   # single chains: one ~25.6 kDa band either way
            self.assertIn(pid, red)
            self.assertIn(pid, nonred)
            self.assertAlmostEqual(red[pid]['apparent_molar_mass_kg_mol'], 25.6, delta=0.2)
        check('reducing', reduced=sorted(red), nonreduced=sorted(nonred))

    def test_overrun_runs_small_proteins_off_and_peptides_ride_the_front(self):
        h = assembled()
        run_to_front(h)
        lane2 = {b['species']: b for b in h.invoke('gel.inspect', gel_id='g1')['lanes'][1]['bands']}
        self.assertGreater(lane2['insulin_bovine:Insulin A chain']['distance_m'], 0.95 * (rt.STACK_LEN_M + 0.062))   # at the dye front
        h.step(40 * 60.0)                                                            # keep running 40 min past the front
        lane2 = {b['species']: b for b in h.invoke('gel.inspect', gel_id='g1')['lanes'][1]['bands']}
        self.assertEqual(lane2['lysozyme_hen']['state'], 'ran_off')
        self.assertEqual(lane2['bsa']['state'], 'resolving')
        self.assertGreater(h.ledger()['protein_run_off_kg'], 0.0)
        check('run_off', run_off_kg=h.ledger()['protein_run_off_kg'])

    def test_reversed_polarity_loses_the_proteins_upward(self):
        h = assembled()
        h.invoke('leads.disconnect')
        h.invoke('leads.connect', polarity='reversed')
        h.invoke('psu.start')
        h.step(600.0)
        g = h.gels['g1']
        self.assertEqual(g['s'], 0.0)
        self.assertTrue(all(b['state'] == 'upper_buffer' for bands in g['lanes'].values() for b in bands))
        self.assertGreater(h.ledger()['protein_upper_buffer_kg'], 0.0)
        check('reversed')

    def test_overload_broadens_and_smile_grows_with_power(self):
        light, heavy = assembled(samples=False), assembled(samples=False)
        for h, ug in ((light, 1.0), (heavy, 40.0)):
            h.invoke('leads.disconnect')
            h.invoke('lid.remove')
            h.invoke('sample.prepare', sample_id='x', proteins_kg=dict(bsa=ug * 1e-9), marker=None, marker_volume_m3=0.0, sample_buffer='laemmli_2x_reducing', heat='95C_5min', volume_m3=10e-9)
            h.invoke('sample.load', sample_id='x', position=1, lane=5, volume_m3=10e-9)
            h.invoke('lid.place')
            h.invoke('leads.connect', polarity='normal')
            run_to_front(h)
        s1 = light.invoke('gel.inspect', gel_id='g1')['lanes'][4]['bands'][0]['sigma_m']
        s2 = heavy.invoke('gel.inspect', gel_id='g1')['lanes'][4]['bands'][0]['sigma_m']
        self.assertGreater(s2, 1.5 * s1)
        cool, hot = assembled(), assembled(buffer='tris_glycine_sds_2x')
        hot.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        run_to_front(cool)
        run_to_front(hot)
        self.assertGreater(hot.gels['g1']['smile'], 5 * cool.gels['g1']['smile'])
        self.assertGreater(hot.Tb, cool.Tb)
        check('overload_smile', sigma_1ug_m=s1, sigma_40ug_m=s2, smile_cool=cool.gels['g1']['smile'], smile_hot=hot.gels['g1']['smile'])

    def test_native_page_basic_protein_needs_reversed_leads(self):
        """bulletin p.12: strongly basic proteins do not enter a native TGX gel; reversing the jacks lets them migrate in; SDS-treated samples keep the
        SDS mobility in a native buffer."""
        res = {}
        for pol in ('normal', 'reversed'):
            h = assembled(buffer='tris_glycine_native_1x', samples=False)
            h.invoke('leads.disconnect')
            h.invoke('lid.remove')
            h.invoke('sample.prepare', sample_id='n', proteins_kg=dict(bsa=2e-09, lysozyme_hen=2e-09), marker=None, marker_volume_m3=0.0, sample_buffer='native_sample_buffer',
                     heat='none', volume_m3=10e-9)
            h.invoke('sample.load', sample_id='n', position=1, lane=1, volume_m3=10e-9)
            h.invoke('sample.prepare', sample_id='d', proteins_kg=dict(lysozyme_hen=2e-09), marker=None, marker_volume_m3=0.0, sample_buffer='laemmli_2x_reducing',
                     heat='95C_5min', volume_m3=10e-9)
            h.invoke('sample.load', sample_id='d', position=1, lane=2, volume_m3=10e-9)
            h.invoke('lid.place')
            h.invoke('leads.connect', polarity=pol)
            if pol == 'normal':
                run_to_front(h)
            else:
                h.invoke('psu.start')
                h.step(1800.0)
            lanes = h.invoke('gel.inspect', gel_id='g1')['lanes']
            res[pol] = ({x['species']: x['state'] for x in lanes[0]['bands']}, {x['species']: x['state'] for x in lanes[1]['bands']})
        self.assertEqual(res['normal'][0]['lysozyme_hen'], 'upper_buffer')            # pI 9.2: positive at pH 8.8, leaves toward the cathode, not into the gel
        self.assertEqual(res['normal'][0]['bsa'], 'resolving')
        self.assertEqual(res['normal'][1]['lysozyme_hen'], 'resolving')               # SDS-treated: an anion whatever the running buffer
        self.assertEqual(res['reversed'][0]['lysozyme_hen'], 'resolving')             # reversed jacks: the cation migrates into the gel
        self.assertEqual(res['reversed'][0]['bsa'], 'upper_buffer')
        check('native', states=res)

    def test_well_overflow_contaminates_neighbours(self):
        h = assembled(samples=False)
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        h.invoke('sample.prepare', sample_id='big', proteins_kg=dict(ovalbumin=5e-09), marker=None, marker_volume_m3=0.0, sample_buffer='laemmli_2x_reducing', heat='95C_5min', volume_m3=60e-9)
        out = h.invoke('sample.load', sample_id='big', position=1, lane=5, volume_m3=50e-9)
        self.assertAlmostEqual(out['loaded_m3'], 30e-9, delta=1e-18)                  # 10-well 30 uL comb
        self.assertAlmostEqual(out['spilled_m3'], 20e-9, delta=1e-18)
        self.assertTrue(h.gels['g1']['lanes'][4] and h.gels['g1']['lanes'][6])
        check('overflow')

    def test_observe_dye_front_and_prestained_markers(self):
        h = assembled(samples=False)
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        h.invoke('sample.prepare', sample_id='pre', proteins_kg=dict(bsa=2e-09), marker='precision_plus_prestained', marker_volume_m3=10e-9, sample_buffer='laemmli_2x_reducing',
                 heat='95C_5min', volume_m3=20e-9)
        h.invoke('sample.load', sample_id='pre', position=1, lane=1, volume_m3=20e-9)
        h.invoke('lid.place')
        h.invoke('leads.connect', polarity='normal')
        h.invoke('psu.start')
        h.step(900.0)
        ob = h.invoke('gel.observe', position=1)
        self.assertAlmostEqual(ob['dye_front_m'], h.gels['g1']['s'], delta=2.5e-3)
        self.assertEqual(len(ob['visible_bands']), 10)                               # 10 prestained bands, BSA (unstained) invisible
        check('observe', dye_front_m=ob['dye_front_m'])


class PowerSupplyAndCell(unittest.TestCase):
    def test_regulation_cv_cc_and_power_limit(self):
        h = assembled()
        h.invoke('psu.program', constant='current', voltage_v=300.0, current_a=0.040, time_s=0.0, power_failure_detection=False)
        h.invoke('psu.start')
        h.step(10.0)
        self.assertEqual(h.psu['active'], 'CC')
        self.assertAlmostEqual(h.I, 0.040, delta=1e-12)
        h.invoke('psu.stop')
        h.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.050, time_s=0.0, power_failure_detection=False)
        h.invoke('psu.start')
        h.step(10.0)
        self.assertEqual(h.psu['active'], 'CC')                                      # crossover at the 50 mA limit
        h.invoke('psu.stop')
        k = assembled(buffer='tris_glycine_sds_10x', n=4)
        k.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        k.invoke('psu.start')
        k.step(10.0)
        self.assertEqual(k.psu['active'], 'CP')
        self.assertAlmostEqual(k.V * k.I, rt.P_MAX_W, delta=1e-9)
        check('regulation', cp_voltage=k.V, cp_current=k.I)

    def test_timer_keys_display_and_accuracy(self):
        h = assembled()
        h.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=120.0, power_failure_detection=False)
        self.assertEqual(h.invoke('psu.press_key', key='display_mode')['display_mode'], 'mA')
        h.invoke('psu.press_key', key='display_mode')
        self.assertEqual(h.invoke('psu.press_key', key='up')['display_value'], 3)
        h.invoke('psu.press_key', key='down')
        h.invoke('psu.press_key', key='display_mode')                              # back to V
        st = h.invoke('psu.press_key', key='run_pause')
        self.assertEqual(st['state'], 'running')
        h.step(30.0)
        v = h.read()['display.value']
        self.assertLessEqual(abs(v - h.V), max(0.02 * h.V, 3.0) + 0.5)               # displayed within the specified accuracy
        h.invoke('psu.press_key', key='run_pause')
        s0 = h.gels['g1']['s']
        h.step(60.0)
        self.assertEqual(h.gels['g1']['s'], s0)                                      # paused: no field
        h.invoke('psu.press_key', key='run_pause')
        h.step(200.0)
        rec = h.records['EP0001']
        self.assertEqual(rec['status'], 'completed')
        self.assertAlmostEqual(rec['run_time_s'], 120.0, delta=0.5)
        check('timer_keys_display', displayed_v=v, true_v=h.V)

    def test_error_codes(self):
        h = assembled()
        h.invoke('fault.inject', fault='lead_break', active=True)
        h.invoke('psu.start')
        self.assertEqual(h.psu['error'], 'E1')                                       # no load at the start
        h.invoke('fault.inject', fault='lead_break', active=False)
        h.invoke('psu.start')
        h.step(5.0)
        h.invoke('fault.inject', fault='lead_break', active=True)
        h.step(1.0)
        self.assertEqual(h.psu['error'], 'E9')                                       # sudden change of the load
        h.invoke('fault.inject', fault='lead_break', active=False)
        h.invoke('psu.press_key', key='stop')
        h.invoke('fault.inject', fault='short_circuit', active=True)
        h.invoke('psu.start')
        h.step(1.0)
        self.assertEqual(h.psu['error'], 'E2')
        h.invoke('fault.inject', fault='short_circuit', active=False)
        h.invoke('psu.stop')
        h.invoke('psu.start')
        h.step(5.0)
        h.invoke('power.press_switch')                                               # switch off mid untimed run
        h.invoke('power.press_switch')
        self.assertEqual(h.psu['error'], 'E7')
        k = assembled()
        k.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=180.0, power_failure_detection=True)
        k.invoke('psu.start')
        k.step(30.0)
        k.invoke('fault.inject', fault='power_loss', active=True)
        k.step(30.0)
        k.invoke('fault.inject', fault='power_loss', active=False)
        k.step(200.0)
        rec = k.records['EP0001']
        self.assertEqual((rec['status'], rec['error_codes']), ('completed', ['E5']))  # power failure detection: the timed run completed
        check('error_codes')

    def test_circuit_rules_tape_dam_and_companion(self):
        taped = assembled(tape=True)
        taped.invoke('psu.start')
        self.assertEqual(taped.psu['error'], 'E1')                                   # the bottom tape of a precast cassette blocks the current
        one = assembled(n=1, samples=False)                                          # one gel without the buffer dam: the inner chamber drains
        one.step(120.0)
        self.assertLess(one.inner['electrode'], rt.INNER_CONTACT_M3)
        one.invoke('psu.start')
        self.assertEqual(one.psu['error'], 'E1')
        dam = assembled(n=1, dam=True, samples=False)
        dam.step(120.0)
        dam.invoke('psu.start')
        self.assertEqual(dam.psu['state'], 'running')
        hot = assembled(samples=False)
        hot.invoke('leads.disconnect')
        hot.invoke('lid.remove')
        hot.invoke('companion.install')                                              # 2 gels with the companion module in the tank
        hot.invoke('lid.place')
        hot.invoke('leads.connect', polarity='normal')
        ref = assembled(samples=False)
        for x in (hot, ref):
            x.invoke('psu.start')
            x.step(600.0)
        self.assertGreater(hot.I, 3 * ref.I)                                        # the open companion module is a buffer path between its electrodes
        self.assertGreater(hot.Tb, ref.Tb + 2.0)                                     # excessive heat (manual caution)
        self.assertGreater(hot.V * hot.I, 3 * ref.V * ref.I)
        with self.assertRaises(rt.ActionRejected):
            ref.invoke('lid.remove')                                                 # leads still in the supply
        hot.invoke('psu.stop')
        hot.invoke('leads.disconnect')
        hot.invoke('lid.remove')
        self.assertFalse(hot.invoke('companion.remove')['companion'])
        check('circuit_rules', companion_current_A=hot.I, ref_current_A=ref.I)

    def test_cold_buffer_and_room_cooling(self):
        h = assembled(temperature_k=277.15, samples=False)
        Ct, Cb = rt.M_TANK_KG * rt.CP_PC, 700e-6 * rt.RHO_W * rt.CP_W
        self.assertAlmostEqual(h.Tb, (Ct * 295.15 + Cb * 277.15) / (Ct + Cb), delta=1e-9)       # pre-chilled buffer mixes with the room-temperature tank
        h.step(3600.0)
        self.assertGreater(h.Tb, 277.15)
        self.assertLess(h.Tb, 295.15)
        check('cold_buffer', T_after_1h=h.Tb)


class ReviewFindings(unittest.TestCase):
    """Behaviour fixed after the independent runtime review round 1 (F-01 ... F-M13)."""

    def test_power_loss_holds_the_supply_off_and_e5_is_displayed(self):
        h = assembled()
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        h.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=600.0, power_failure_detection=True)
        h.invoke('psu.start')
        h.step(100.0)
        h.invoke('fault.inject', fault='power_loss', active=True)
        r = s.invoke(instance_id=INST, capability_id='power.set', arguments=dict(enabled=True), request_id='on')
        self.assertEqual((r['status'], r['error']['code']), ('rejected', 'MAINS_OUT_OF_RANGE'))       # no mains: the supply cannot be switched on
        ev = s.invoke(instance_id=INST, capability_id='events.read', arguments=dict(since_s=0.0), request_id='ev')['result']['events']
        self.assertEqual([e['code'] for e in ev if e['kind'] == 'power'][-1], 'POWER_OFF')          # the agent sees a dark panel, not the cause
        self.assertIsNone(h.read()['psu.active_mode'])
        s_out = h.gels['g1']['s']
        h.step(50.0)
        self.assertEqual(h.gels['g1']['s'], s_out)                                                  # no field during the outage
        h.invoke('fault.inject', fault='power_loss', active=False)                                  # mains back, switch still on: automatic restart
        self.assertTrue(h.power)
        h.step(700.0)
        rec = h.records['EP0001']
        self.assertEqual((rec['status'], rec['error_codes'], rec['run_time_s']), ('completed', ['E5'], 600.0))
        self.assertEqual(h.read()['display.error_code'], 'E5')                                      # manual 4.2: E5 displayed, run completed
        self.assertIsNone(h.read()['display.value'])
        h.invoke('psu.press_key', key='display_mode')                                               # any key clears the code
        self.assertIsNone(h.read()['display.error_code'])
        self.assertFalse(h.psu['pfd'])                                                              # Pfd is de-activated after the run
        check('power_loss_e5')

    def test_e9_hold_resumes_the_interrupted_run(self):
        h = assembled()
        h.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=1800.0, power_failure_detection=False)
        h.invoke('psu.start')
        h.step(1500.0)
        h.invoke('fault.inject', fault='lead_break', active=True)
        h.step(10.0)
        self.assertEqual((h.psu['state'], h.read()['display.error_code']), ('error', 'E9'))
        self.assertEqual(h.records['EP0001']['status'], 'running')                                   # held, not ended
        h.invoke('fault.inject', fault='lead_break', active=False)
        st = h.invoke('psu.press_key', key='run_pause')                                             # manual 4.2: run/pause resumes the run
        self.assertEqual((st['state'], st['operation_id']), ('running', 'op0001'))
        h.step(400.0)
        rec = h.records['EP0001']
        self.assertEqual(list(h.records), ['EP0001'])
        self.assertEqual((rec['status'], rec['run_time_s'], rec['error_codes']), ('completed', 1800.0, ['E9']))
        k = assembled()
        k.invoke('psu.start')
        k.step(60.0)
        k.invoke('fault.inject', fault='short_circuit', active=True)
        k.step(1.0)
        self.assertEqual(k.read()['display.error_code'], 'E2')
        k.invoke('fault.inject', fault='short_circuit', active=False)
        k.invoke('psu.press_key', key='up')                                                         # any other key clears the code and ends the run
        self.assertEqual((k.psu['state'], k.records['EP0001']['status'], k.records['EP0001']['error_codes']), ('stopped', 'error', ['E2']))
        check('e9_resume')

    def test_standard_300V_smile_is_small_and_lanes_stay_in_the_gel(self):
        h = assembled(n=4, outer=680e-6)
        h.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        h.invoke('sample.prepare', sample_id='m5', proteins_kg={}, marker='precision_plus_unstained', marker_volume_m3=10e-9, sample_buffer='laemmli_2x_reducing',
                 heat='95C_5min', volume_m3=10e-9)
        h.invoke('sample.load', sample_id='m5', position=1, lane=5, volume_m3=10e-9)
        h.invoke('lid.place')
        h.invoke('leads.connect', polarity='normal')
        run_to_front(h)
        h.invoke('psu.stop')
        ins = h.invoke('gel.inspect', gel_id='g1')
        self.assertLess(ins['smile_fraction'], 0.015)
        res = ins['resolving_height_m']
        front = ins['dye_front_m']
        for lane in ins['lanes']:
            inside = [b for b in lane['bands'] if b['state'] == 'resolving']
            for b in inside:
                self.assertLess(b['distance_m'], rt.STACK_LEN_M + res)                  # no band below the gel bottom
            # the profile holds every band that is not cut by the gel end (bands riding the front at the bottom lose their tail only)
            clear = [b for b in inside if b['distance_m'] - rt.STACK_LEN_M < res - 3 * b['sigma_m']]
            mass = sum(b['amount_kg'] for b in clear)
            if clear and len(clear) == len(inside):
                self.assertGreater(sum(lane['profile']) / mass, 0.99, lane['lane'])
        self.assertLessEqual(front, rt.STACK_LEN_M + res + 1e-12)
        check('smile_300V', smile=ins['smile_fraction'])

    def test_overheating_is_bounded_and_flagged(self):
        h = assembled(n=4, outer=680e-6)
        h.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        h.invoke('psu.start')
        h.step(3 * 3600.0)
        self.assertLessEqual(h.Tb, rt.BOIL_K + 1e-9)
        self.assertLessEqual(max(g['T'] for g in h._installed_gels()), rt.BOIL_K + 1e-9)
        rec = h.records['EP0001']
        self.assertIn('buffer_boiling', rec['warnings'])                                             # visible to the operator
        self.assertNotIn('buffer_hot', rec['warnings'])
        codes = {e['code'] for e in h.invoke('events.read', since_s=0.0)['events']}
        self.assertTrue({'BUFFER_HOT', 'GEL_OVERHEATED'} <= codes)                                  # evaluator view
        h.register_principal('ag', 'agent')
        seen = {e['code'] for e in h.session('ag').invoke(instance_id=INST, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e')['result']['events']}
        self.assertFalse({'BUFFER_HOT', 'GEL_OVERHEATED'} & seen)                                   # temperatures are not displayed to the agent
        ins = h.invoke('gel.inspect', gel_id='g1')
        self.assertTrue(ins['heat_damaged'])
        L = h.ledger()
        C = h._heat_capacity()
        self.assertGreater(L['output_energy_j'], L['heat_to_room_j'])
        check('overheat', T_buffer_C=h.Tb - 273.15, warnings=rec['warnings'], evaporated_m3=L['buffer_evaporated_m3'], C=C)

    def test_native_table_4_1_both_rows(self):
        rows = {}
        for gel, (I0r, I1r, tr, Tr) in (('tgx_7.5_10w_30ul', ((35, 37), (17, 20), (38, 40), (28, 30))), ('tgx_4_20_10w_30ul', ((50, 55), (25, 28), (30, 34), (30, 33))),
                                        ('tgx_any_kd_10w_30ul', ((50, 55), (25, 28), (30, 34), (30, 33)))):
            h = assembled(gel=gel, n=1, dam=True, buffer='tris_glycine_native_1x', samples=False)
            I0, I1, t = run_to_front(h, dt=2.0)
            Tg = h.gels['g1']['T'] - 273.15
            rows[gel] = dict(I0_mA=I0 * 1e3, I1_mA=I1 * 1e3, t_min=t / 60, T_gel_C=Tg)
            self.assertTrue(0.97 * I0r[0] <= I0 * 1e3 <= 1.03 * I0r[1], rows[gel])
            self.assertTrue(0.95 * I1r[0] <= I1 * 1e3 <= 1.05 * I1r[1], rows[gel])
            self.assertTrue(tr[0] - 1 <= t / 60 <= tr[1] + 1, rows[gel])
            self.assertTrue(Tr[0] - 1 <= Tg <= Tr[1] + 1, rows[gel])
        check('native_table_4_1', rows=rows)

    def test_tgx_formats_share_the_manufacturer_tables(self):
        t = {}
        for gel in ('tgx_4_20_10w_30ul', 'tgx_4_20_10w_50ul'):
            h = assembled(gel=gel)
            h.invoke('psu.program', constant='voltage', voltage_v=100.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
            I0, I1, tt = run_to_front(h)
            t[gel] = tt / 60
            self.assertTrue(85 * 0.97 <= tt / 60 <= 95, (gel, tt / 60))
        k = assembled(gel='tgx_4_20_10w_50ul', n=4, outer=680e-6)
        k.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        k.invoke('psu.start')
        k.step(2.0)
        self.assertTrue(55e-3 <= k.I / 4 <= 75e-3, k.I / 4)
        check('tgx_formats', run_min_100V=t, start_300V_per_gel_A=k.I / 4)

    def test_protein_ledger_on_reinstall_and_edge_overflow(self):
        h = assembled(samples=False)
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        h.invoke('sample.prepare', sample_id='e', proteins_kg=dict(bsa=6e-09), marker=None, marker_volume_m3=0.0, sample_buffer='laemmli_2x_reducing', heat='95C_5min', volume_m3=60e-9)
        out = h.invoke('sample.load', sample_id='e', position=1, lane=1, volume_m3=60e-9)          # 30 uL well at the gel edge
        self.assertAlmostEqual(out['spilled_m3'], 30e-9, delta=1e-18)
        L = h.ledger()
        self.assertAlmostEqual(L['protein_loaded_kg'], L['protein_in_gels_kg'] + L['protein_spilled_kg'], delta=1e-21)
        self.assertAlmostEqual(L['protein_spilled_kg'], 1.5e-9, delta=1e-21)                       # half over the gel edge into the buffer
        h.invoke('lid.place')
        h.invoke('leads.connect', polarity='normal')
        h.invoke('psu.start')
        h.step(600.0)
        h.invoke('psu.stop')
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        h.invoke('buffer.drain')
        h.invoke('gel.remove', position=1)
        h.invoke('gel.install', item='g1', position=1)                                            # the same gel back in
        L = h.ledger()
        sinks = L['protein_in_gels_kg'] + L['protein_on_bench_gels_kg'] + L['protein_run_off_kg'] + L['protein_upper_buffer_kg'] + L['protein_spilled_kg']
        self.assertAlmostEqual(L['protein_loaded_kg'], sinks, delta=1e-21)
        self.assertEqual(L['protein_on_bench_gels_kg'], 0.0)
        check('protein_ledger', ledger={k: v for k, v in L.items() if k.startswith('protein')})

    def test_power_record_uses_displayed_readings(self):
        vals = {}
        for seed in (1, 3):
            h = assembled(seed=seed)
            h.invoke('psu.start')
            h.step(60.0)
            h.invoke('psu.stop')
            r = h.records['EP0001']
            self.assertAlmostEqual(r['max_power_w'], round(r['max_voltage_v'] * r['max_current_a'], 1), delta=1e-9)
            vals[seed] = r['max_power_w']
        self.assertNotEqual(vals[1], vals[3])                                                         # carries the per-instrument display bias
        check('power_record', max_power_w=vals)

    def test_step_partition_with_the_run_ending_inside_a_step(self):
        a, b = assembled(seed=3), assembled(seed=3)
        for h in (a, b):
            h.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=600.0, power_failure_detection=False)
            h.invoke('psu.start')
        a.step(1200.0)
        b.step(599.75)
        b.step(600.25)
        self.assertEqual(json.dumps(a.read('evaluator'), sort_keys=True), json.dumps(b.read('evaluator'), sort_keys=True))
        self.assertEqual(json.dumps(a.ledger(), sort_keys=True), json.dumps(b.ledger(), sort_keys=True))
        self.assertEqual(json.dumps(a.records, sort_keys=True), json.dumps(b.records, sort_keys=True))
        self.assertEqual(a.records['EP0001']['finished_s'], 600.0)                                    # the tick time, not the step end
        check('partition_run_end')

    def test_constant_change_needs_pause_and_key_started_runs_are_owned(self):
        h = assembled()
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        r = s1.invoke(instance_id=INST, capability_id='psu.press_key', arguments=dict(key='run_pause'), request_id='k')
        oid = r['result']['operation_id']
        self.assertEqual(s1.poll(oid)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            s2.poll(oid)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('psu.program', constant='current', voltage_v=200.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        s1.cancel(oid, 'c')
        w = assembled(buffer='deionized_water')
        w.register_principal('a1', 'agent')
        w.register_principal('a2', 'agent')
        w.session('a1').invoke(instance_id=INST, capability_id='psu.press_key', arguments=dict(key='run_pause'), request_id='k')
        self.assertEqual(w.records['EP0001']['error_codes'], ['E1'])
        lst = w.session('a2').invoke(instance_id=INST, capability_id='records.list', arguments={}, request_id='l')['result']['records']
        self.assertEqual(lst, [])                                                                     # an immediate-E1 record still belongs to its caller
        check('ownership')

    def test_first_tick_current_and_cold_buffer(self):
        warm, cold = assembled(), assembled(temperature_k=277.15)
        for h in (warm, cold):
            h.invoke('psu.start')
            h.step(0.5)
        self.assertLess(cold.I, 0.8 * warm.I)                                                         # a gel in 4 C buffer conducts less
        k = assembled(n=4, outer=680e-6)
        k.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        k.invoke('psu.start')
        k.step(0.5)
        self.assertTrue(55e-3 <= k.I / 4 <= 75e-3, k.I / 4)                                        # Table 3.1 start current from the first tick
        check('first_tick', warm_A=warm.I, cold_A=cold.I, start_300V_per_gel_A=k.I / 4)

    def test_comb_on_installed_gel_and_unrinsed_wells(self):
        h = assembled(samples=False)
        h.invoke('psu.start')
        with self.assertRaises(rt.ActionRejected):
            h.invoke('gel.prepare', gel_id='g1', remove_comb=True, remove_tape=False, rinse_wells=False)     # installed, lid on, running
        sig = {}
        for rinse in (True, False):
            k = configured()
            k.invoke('leads.disconnect')
            k.invoke('lid.remove')
            for q in (1, 2):
                k.invoke('gel.provide', gel_id=f'g{q}', gel_type=TGX)
                k.invoke('gel.prepare', gel_id=f'g{q}', remove_comb=False, remove_tape=True, rinse_wells=False)
                k.invoke('gel.install', item=f'g{q}', position=q)
            k.invoke('buffer.fill', chamber='inner', buffer_id=SDS, volume_m3=150e-6, temperature_k=295.15)
            k.invoke('buffer.fill', chamber='outer', buffer_id=SDS, volume_m3=550e-6, temperature_k=295.15)
            k.invoke('lid.place')
            with self.assertRaises(rt.ActionRejected):
                k.invoke('gel.prepare', gel_id='g1', remove_comb=True, remove_tape=False, rinse_wells=False)   # lid on
            k.invoke('lid.remove')
            for q in (1, 2):
                k.invoke('gel.prepare', gel_id=f'g{q}', remove_comb=True, remove_tape=False, rinse_wells=rinse)
            k.invoke('sample.prepare', sample_id='b', proteins_kg=dict(bsa=1e-09), marker=None, marker_volume_m3=0.0, sample_buffer='laemmli_2x_reducing', heat='95C_5min', volume_m3=10e-9)
            k.invoke('sample.load', sample_id='b', position=1, lane=3, volume_m3=10e-9)
            k.invoke('lid.place')
            k.invoke('leads.connect', polarity='normal')
            k.invoke('power.set', enabled=True)
            k.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
            run_to_front(k)
            sig[rinse] = k.invoke('gel.inspect', gel_id='g1')['lanes'][2]['bands'][0]['sigma_m']
        self.assertGreater(sig[False], 1.04 * sig[True])                               # sigma0 x 1.2 at the start, diluted by the diffusion of the run
        check('comb_rinse', sigma_m=sig)

    def test_companion_bypass_starves_the_gels(self):
        ref = assembled()
        hot = assembled()
        hot.invoke('leads.disconnect')
        hot.invoke('lid.remove')
        hot.invoke('companion.install')
        hot.invoke('lid.place')
        hot.invoke('leads.connect', polarity='normal')
        for x in (hot, ref):
            x.invoke('psu.start')
            x.step(1800.0)
        self.assertLess(hot.gels['g1']['s'], 0.9 * ref.gels['g1']['s'])                             # the supply limits: less field across the gels
        self.assertGreater(hot.Tb, ref.Tb + 10.0)
        self.assertIn('companion_module_bypass', hot.records['EP0001']['warnings'])
        check('bypass', front_hot_m=hot.gels['g1']['s'], front_ref_m=ref.gels['g1']['s'], T_hot_C=hot.Tb - 273.15)

    def test_observe_is_stable_for_an_unchanged_gel(self):
        h = assembled()
        h.invoke('psu.start')
        h.step(600.0)
        h.invoke('psu.stop')
        reads = []
        for _ in range(20):
            reads.append(h.invoke('gel.observe', position=1)['dye_front_m'])
            h.step(10.0)
        self.assertEqual(len(set(reads)), 1)                                                          # averaging repeated reads gains nothing
        check('observe_stable', front_m=reads[0])

    def test_round2_hold_ownership_rinse_profile_and_power_loss(self):
        h = assembled()
        h.register_principal('ag', 'agent')
        h.register_principal('ev', 'evaluator')
        a, e = h.session('ag'), h.session('ev')
        r = a.invoke(instance_id=INST, capability_id='psu.start', arguments={}, request_id='s')
        h.step(60.0)
        h.invoke('fault.inject', fault='lead_break', active=True)
        h.step(1.0)
        h.invoke('fault.inject', fault='lead_break', active=False)
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        for cap, args in (('buffer.drain', {}), ('gel.remove', dict(position=2)), ('sample.load', dict(sample_id='s', position=1, lane=5, volume_m3=2e-9))):
            with self.assertRaises(rt.ActionRejected):
                h.invoke(cap, **args)                                                                # the held run is still open (F2-01)
        h.invoke('lid.place')
        h.invoke('leads.connect', polarity='normal')
        e.invoke(instance_id=INST, capability_id='psu.start', arguments={}, request_id='resume')   # the evaluator resumes the agent's run
        self.assertEqual(a.poll(r['operation_id'])['status'], 'running')                           # the owner keeps poll / cancel (F2-02)
        h.step(600.0)
        h.invoke('fault.inject', fault='lead_break', active=True)
        h.step(1.0)
        h.invoke('power.set', enabled=False)                                                         # power lost during the E9 hold (F2-08)
        h.invoke('fault.inject', fault='lead_break', active=False)
        h.invoke('power.set', enabled=True)
        self.assertEqual(h.read()['display.error_code'], 'E7')
        self.assertEqual(h.records['EP0001']['status'], 'power_failed')
        self.assertIn('power_failure_during_run', h.records['EP0001']['warnings'])
        k = assembled(samples=True)
        k.invoke('leads.disconnect')
        k.invoke('lid.remove')
        with self.assertRaises(rt.ActionRejected):
            k.invoke('gel.prepare', gel_id='g1', remove_comb=False, remove_tape=False, rinse_wells=True)   # samples loaded (F2-04)
        k.invoke('lid.place')
        k.invoke('leads.connect', polarity='normal')
        k.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        run_to_front(k)
        k.invoke('psu.stop')
        for lane in k.invoke('gel.inspect', gel_id='g1')['lanes']:
            inside = sum(b['amount_kg'] for b in lane['bands'] if b['state'] == 'resolving')
            self.assertAlmostEqual(sum(lane['profile']), inside, delta=1e-9 * max(inside, 1e-18))    # the profile holds every in-gel band (F2-05)
        check('round2')

    def test_contract_review_power_codes_mains_bands_and_poll(self):
        """PowerPac manual 4.2: E6 = mains failure in a timed run, E7 = untimed run or the switch turned off before stop; input 90-132 / 198-264 VAC."""
        for cause, code in (('mains', 'E6'), ('switch', 'E7')):
            h = assembled()
            h.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=600.0, power_failure_detection=False)
            h.invoke('psu.start')
            h.step(60.0)
            if cause == 'mains':
                h.invoke('fault.inject', fault='power_loss', active=True)
                h.invoke('fault.inject', fault='power_loss', active=False)
            else:
                h.invoke('power.press_switch')
                h.invoke('power.press_switch')
            self.assertEqual(h.read()['display.error_code'], code, cause)
        for v, ok in ((120.0, True), (133.0, False), (150.0, False), (198.0, True), (230.0, True), (60.0, False)):
            k = configured(mains_voltage_v=v)
            if ok:
                self.assertTrue(k.invoke('power.set', enabled=True)['enabled'])
            else:
                with self.assertRaises(rt.ActionRejected):
                    k.invoke('power.set', enabled=True)
        h = assembled(n=4, outer=680e-6)
        h.register_principal('ag', 'agent')
        a = h.session('ag')
        h.invoke('psu.program', constant='voltage', voltage_v=300.0, current_a=0.400, time_s=0.0, power_failure_detection=False)
        r = a.invoke(instance_id=INST, capability_id='psu.start', arguments={}, request_id='s')
        h.step(3600.0)
        res = a.poll(r['operation_id'])['result']
        self.assertFalse([k for k in res if k.startswith('_')])                                     # no evaluator-only flags in the agent's poll
        self.assertEqual(asset_tool.validate_value(res, _meta()['capabilities']['measurement.result']['output_schema']), [])
        check('power_codes_mains_poll')

    def test_reversed_field_moves_sds_bands_back(self):
        h = assembled()
        h.invoke('psu.start')
        h.step(900.0)
        h.invoke('psu.stop')
        d0 = {b['species']: b['distance_m'] for b in h.invoke('gel.inspect', gel_id='g1')['lanes'][1]['bands'] if b['state'] == 'resolving'}
        h.invoke('leads.disconnect')
        h.invoke('leads.connect', polarity='reversed')
        h.invoke('psu.start')
        h.step(300.0)
        h.invoke('psu.stop')
        d1 = {b['species']: (b['distance_m'], b['state']) for b in h.invoke('gel.inspect', gel_id='g1')['lanes'][1]['bands']}
        self.assertTrue(all(d1[k][1] == 'upper_buffer' or d1[k][0] < d0[k] for k in d0))
        check('reverse_back', before=d0)


class Framework(unittest.TestCase):
    def test_read_describe_pure_and_clock(self):
        h = assembled()
        h.register_principal('ag', 'agent')
        v0 = h.read('evaluator')
        h.session('ag').describe(instance_id=INST)
        h.session('ag').read(instance_id=INST)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True), json.dumps(v0, sort_keys=True))
        t = h.invoke('sim.step', dt_s=2.25)['time_s']
        self.assertEqual(t, h.t)
        check('pure_reads')

    def test_step_partition_independent(self):
        a, b = assembled(seed=3), assembled(seed=3)
        for h in (a, b):
            h.invoke('psu.start')
        a.step(600.0)
        for _ in range(240):
            b.step(2.5)
        ra, rb = a.read('evaluator'), b.read('evaluator')
        for k in ra:
            if isinstance(ra[k], float):
                self.assertAlmostEqual(ra[k], rb[k], delta=1e-9 * max(1.0, abs(ra[k])), msg=k)
            else:
                self.assertEqual(ra[k], rb[k], k)
        ia, ib = a.invoke('gel.inspect', gel_id='g1'), b.invoke('gel.inspect', gel_id='g1')
        self.assertEqual(json.dumps(ia, sort_keys=True), json.dumps(ib, sort_keys=True))
        check('step_partition')

    def test_conservation(self):
        h = assembled()
        e0 = h.ledger()
        run_to_front(h)
        h.invoke('psu.stop')
        L = h.ledger()
        led_t0 = sum(len(g['runs']) for g in h.gels.values())
        self.assertGreater(L['charge_c'], 0)
        rec = h.records['EP0001']
        self.assertTrue(0.3 * rec['max_current_a'] < L['charge_c'] / rec['run_time_s'] <= rec['max_current_a'] * 1.03)                    # mean current below the start maximum
        C = h._heat_capacity()
        self.assertAlmostEqual(L['output_energy_j'] - L['heat_to_room_j'], C * (h.Tb - 295.15), delta=1e-6 * L['output_energy_j'] + 1e-6)   # Joule heat -> buffer + room
        self.assertAlmostEqual(L['protein_loaded_kg'], L['protein_in_gels_kg'] + L['protein_run_off_kg'] + L['protein_upper_buffer_kg'] + L['protein_on_bench_gels_kg'], delta=1e-18)
        self.assertAlmostEqual(L['buffer_added_m3'], 700e-6, delta=1e-12)
        self.assertEqual(h.invoke('ledger.read')['ledger'], h.ledger())
        h.step(3600.0)
        L2 = h.ledger()
        self.assertEqual(L2['output_energy_j'], L['output_energy_j'])
        self.assertAlmostEqual(L2['output_energy_j'] - L2['heat_to_room_j'], h._heat_capacity() * (h.Tb - 295.15), delta=1e-6 * L2['output_energy_j'])
        self.assertTrue(led_t0 >= 2 and e0['charge_c'] == 0.0)
        check('conservation', charge_c=L['charge_c'], energy_j=L['output_energy_j'])

    def test_rejections_leave_state_unchanged(self):
        h = assembled()
        v0 = json.dumps(h.read('evaluator'), sort_keys=True, default=str)
        for cap, args in [('lid.remove', {}), ('gel.install', dict(item='g1', position=3)), ('sample.load', dict(sample_id='marker', position=1, lane=1, volume_m3=1e-9)),
                          ('buffer.fill', dict(chamber='outer', buffer_id=SDS, volume_m3=10e-6, temperature_k=295.15)), ('companion.install', {}),
                          ('psu.program', dict(constant='voltage', voltage_v=200.5, current_a=0.400, time_s=0.0, power_failure_detection=False)),
                          ('gel.provide', dict(gel_id='g1', gel_type=TGX)), ('sample.prepare', dict(rt.SAMPLE_EXAMPLE, proteins_kg=dict(nope=1e-09)))]:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(cap, **args)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True, default=str), v0)
        check('rejections_pure')

    def test_cancellation_and_gel_removal(self):
        h = assembled()
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        r = s.invoke(instance_id=INST, capability_id='psu.start', arguments={}, request_id='run')
        self.assertEqual(r['status'], 'accepted')
        h.step(300.0)
        s0 = h.gels['g1']['s']
        s.cancel(r['operation_id'], 'c')
        self.assertEqual(s.poll(r['operation_id'])['status'], 'cancelled')
        self.assertEqual(h.records['EP0001']['status'], 'stopped')
        self.assertEqual(h.gels['g1']['s'], s0)                                      # the migration so far is kept
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        with self.assertRaises(rt.ActionRejected):
            h.invoke('gel.remove', position=1)                                       # pour off the buffer first
        h.invoke('buffer.drain')
        h.invoke('gel.remove', position=1)
        self.assertIn('g1', h.read()['gel.bench_ids'])
        check('cancel_remove')

    def test_two_instances_isolated(self):
        a, b = assembled(), assembled()
        run_to_front(a)
        self.assertEqual(b.records, {})
        self.assertEqual(b.gels['g1']['s'], 0.0)
        check('isolation')

    def test_agent_truth_isolation_and_idempotence(self):
        h = assembled()
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        h.register_principal('ev', 'evaluator')
        s1, s2, e = h.session('a1'), h.session('a2'), h.session('ev')
        obs = s1.read(instance_id=INST)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.') or k.startswith('psu.output')])
        self.assertIn('truth.buffer_temperature_k', e.read(instance_id=INST)['observations'])
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='gel.inspect', arguments=dict(gel_id='g1'), request_id='i')['error']['code'], 'ACCESS_DENIED')
        r1 = s1.invoke(instance_id=INST, capability_id='psu.start', arguments={}, request_id='r')
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='psu.start', arguments={}, request_id='r'), r1)
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='psu.stop', arguments={}, request_id='r')['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='psu.stop', arguments={}, request_id='x')['error']['code'], 'ACCESS_DENIED')
        h.step(60.0)
        s1.invoke(instance_id=INST, capability_id='psu.stop', arguments={}, request_id='stop')
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='measurement.result', arguments=dict(record_id='EP0001'), request_id='m')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s1.read(instance_id=INST)['observations']['result.last_record_id']['value'], 'EP0001')
        check('truth_isolation_idempotence')

    def test_queries_and_unsupported(self):
        h = assembled()
        run_to_front(h)
        h.invoke('psu.stop')
        self.assertEqual(h.invoke('records.list')['records'], ['EP0001'])
        r = h.invoke('measurement.result', record_id='EP0001')
        self.assertEqual(r['status'], 'stopped')
        self.assertGreater(r['max_current_a'], 0)
        self.assertTrue(h.invoke('events.read', since_s=0.0)['events'])
        p = h.invoke('provenance.read')
        self.assertEqual(len(p['libraries']), 3)
        ins = h.invoke('gel.inspect', gel_id='g1')
        self.assertEqual(len(ins['lanes']), 10)
        self.assertEqual(len(ins['lanes'][0]['profile']), 200)
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(k)
        check('queries')


# ---------------------------------------------------------------- declared effects over scenario states
_SC = {}


def _build(name):
    if name == 'fresh':
        return rt.BioradMiniproteanRuntime()
    if name == 'off':
        return configured()
    if name == 'open':
        h = assembled()
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        return h
    if name == 'ready':
        return assembled()
    if name == 'running':
        h = assembled()
        h.invoke('psu.start')
        h.step(30.0)
        return h
    if name == 'paused':
        h = _scenario('running')
        h.invoke('psu.press_key', key='run_pause')
        return h
    if name == 'timed':
        h = assembled()
        h.invoke('psu.program', constant='voltage', voltage_v=200.0, current_a=0.400, time_s=60.0, power_failure_detection=True)
        h.invoke('psu.start')
        h.step(20.0)
        return h
    if name == 'done':
        h = assembled()
        run_to_front(h)
        h.invoke('psu.stop')
        return h
    if name == 'error':
        h = assembled()
        h.invoke('fault.inject', fault='lead_break', active=True)
        h.invoke('psu.start')
        return h
    if name == 'one_gel':
        return assembled(n=1, samples=False)
    if name == 'open_one':
        h = assembled(n=1, samples=False)
        h.invoke('leads.disconnect')
        h.invoke('lid.remove')
        return h
    if name == 'drained':
        h = _scenario('open')
        h.invoke('buffer.drain')
        return h
    if name == 'open_companion':
        h = _scenario('open')
        h.invoke('companion.install')
        return h
    if name == 'unplugged':
        h = assembled()
        h.invoke('leads.disconnect')
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'open', 'ready', 'running', 'paused', 'timed', 'done', 'error', 'one_gel', 'open_one', 'unplugged', 'drained', 'open_companion']
STEP_DTS = (0.5, 5.0, 60.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0)],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_switch': [{}],
        'psu.program': [dict(rt.PSU_EXAMPLE), dict(rt.PSU_EXAMPLE, constant='current', current_a=0.030, time_s=0.0)],
        'psu.press_key': [dict(key=k) for k in rt.PANEL_KEYS], 'psu.start': [{}], 'psu.stop': [{}],
        'lid.place': [{}], 'lid.remove': [{}], 'leads.connect': [dict(polarity='normal'), dict(polarity='reversed')], 'leads.disconnect': [{}],
        'companion.install': [{}], 'companion.remove': [{}], 'gel.provide': [dict(gel_id='new', gel_type=TGX)],
        'gel.prepare': [dict(gel_id='g1', remove_comb=True, remove_tape=True, rinse_wells=True), dict(gel_id='g1', remove_comb=True, remove_tape=False, rinse_wells=True)],
        'gel.install': [dict(item='buffer_dam', position=2), dict(item='buffer_dam', position=3)], 'gel.remove': [dict(position=1)],
        'buffer.fill': [dict(chamber='outer', buffer_id=SDS, volume_m3=50e-6, temperature_k=280.0), dict(chamber='inner', buffer_id=SDS, volume_m3=20e-6, temperature_k=295.15)],
        'buffer.drain': [{}], 'sample.prepare': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')],
        'sample.load': [dict(sample_id='marker', position=1, lane=3, volume_m3=5e-9), dict(sample_id='s', position=1, lane=4, volume_m3=5e-9)],
        'fault.inject': [dict(fault=f, active=True) for f in ('power_loss', 'lead_break', 'inner_leak', 'short_circuit')] + [dict(fault='lead_break', active=False)],
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
    return cap not in rt.UNSUPPORTED and rt.CAPS[cap]['kind'] not in ('query', 'analysis')


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
        self.assertEqual(missing_imm, {})
        self.maxDiff = None
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
