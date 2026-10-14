"""tests for CHEM-165 thermo_ibrightfl1500: python3 -m unittest tests/test_asset_thermo_ibrightfl1500.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances are
isolated, agent/evaluator truth isolation, conservation (energy ledger, exposure time, photons), real return values validated against the declared
schemas (asset_tool validate_value), asynchronous completion and cancellation, host-step independence, declared immediate and on_step effects over
scenario states; plus the imaging physics against the manufacturer data and first principles: 22.5 x 18.0 cm field, 16-bit saturation at 65 535,
binning 1-8 and 1-2x optical / digital zoom (object-side NA of the 25 mm f/0.95 lens), CCD cooling before capture, FPbase spectra with the FL1500
filter sets (channel mismatch, EX3/EX4 crosstalk), Smart Exposure to 80 % of full scale, linear quantification of dilution series (fluorescence,
chemiluminescence, visible Coomassie), chemiluminescent substrate decay and Smart Range HDR, nucleic-acid stain and free-stain background, automatic
alignment / zoom and the camera lock, band analysis outputs of the user guide (volume, density, local background, Rf, purity, molar mass from a
marker), faults (LED failure, smudged glass, filter wheel jam, blocked drawer, mains loss during a capture).
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
from asset_runtime import thermo_ibrightfl1500 as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
INST = 'imager_01'


def _meta():
    if not _META:
        _META.update(rt.export_thermo_ibrightfl1500_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.ThermoIbrightFl1500Runtime.invoke


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


rt.ThermoIbrightFl1500Runtime.invoke = _instrumented_invoke


def check(cid, **detail):
    CHECKS.append(dict(check=cid, status='passed', **detail))


def powered(seed=1, cool=True, **plant):
    h = rt.ThermoIbrightFl1500Runtime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    h.invoke('power.set', enabled=True)
    if cool:
        h.step(300.0)
    return h


def blot(sid='blot', n=8, label='alexa_fluor_647', base=2.5e-15, factor=2.0, support='pvdf_lf', y=0.020, second=None, W=0.085, H=0.065, **kw):
    lanes = []
    for i in range(n):
        bands = [dict(y_m=y, sigma_m=0.0006, label=label, amount_mol=base * factor ** i)]
        if second:
            bands.append(dict(second))
        lanes.append(dict(x_m=0.008 + 0.0095 * i, width_m=0.006, bands=bands))
    return dict(sample_id=sid, support=support, width_m=W, height_m=H, lanes=lanes, **kw)


def loaded(sample, skew=0.0, seed=1, mode='fluorescent_blots', channels=(('EX3', 'alexa_fluor_647'),), **plant):
    h = powered(seed, **plant)
    h.invoke('sample.provide', **sample)
    h.invoke('drawer.open')
    h.step(5.0)
    h.invoke('sample.place', sample_id=sample['sample_id'], skew_rad=skew)
    h.invoke('drawer.close')
    h.step(5.0)
    h.invoke('mode.select', mode=mode)
    if channels is not None and mode in ('fluorescent_blots', 'universal'):
        h.invoke('channels.set', channels=[dict(channel=c, dye=d) for c, d in channels])
    return h


def smart_capture(h, hdr=False, smart=True):
    if smart:
        h.invoke('smart_exposure.run')
        h.step(120.0)
    h.invoke('capture.run', hdr=hdr)
    h.step(1200.0)
    return max(h.images)


def first_band_volumes(an):
    return [l['bands'][0]['lbc_volume'] if l['bands'] else None for l in an['lanes']]


class ContractAndLibrary(unittest.TestCase):
    def test_module_identity_and_export(self):
        meta = rt.export_thermo_ibrightfl1500_contract_metadata()
        self.assertEqual((rt.ASSET_TYPE_ID, rt.CATALOG_ID), ('instrument.thermo_ibrightfl1500', 'CHEM-165'))
        for k, c in meta['capabilities'].items():
            for sname in ('input_schema', 'output_schema'):
                self.assertEqual(asset_tool.lint_schema(c[sname]) if hasattr(asset_tool, 'lint_schema') else [], [], (k, sname))
            self.assertEqual(asset_tool.validate_value(c['example_arguments'], c['input_schema']), [], k)
        self.assertEqual(set(meta['state_slots']), set(rt.SLOTS))
        for k, v in rt.SLOTS.items():
            self.assertFalse(v['origin'] == 'process_truth' and v['visibility'] == 'agent', k)
        check('export', capabilities=len(meta['capabilities']), slots=len(meta['state_slots']))

    def test_fluorophore_library(self):
        lib = rt.fluor_lib()
        S = lib['substances']
        self.assertGreaterEqual(len(S), 50)
        self.assertAlmostEqual(S['alexa_fluor_647']['ext_coeff_m2_mol'], 27000.0, delta=1.0)     # 270 000 M-1 cm-1 (FPbase)
        self.assertAlmostEqual(S['alexa_fluor_647']['quantum_yield'], 0.33, delta=1e-9)
        self.assertIn('CC BY-SA', json.dumps(lib['license']))
        g = rt._grid()
        self.assertEqual(len(S['alexa_fluor_488']['spectra']['em']), len(g))
        em = np.array(S['alexa_fluor_488']['spectra']['em'])
        self.assertTrue(505e-9 < g[int(np.argmax(em))] < 530e-9)                                  # AF488 emission maximum ~517 nm
        check('library', dyes=len(S))

    def test_optics_geometry(self):
        g1, g2 = rt.working_geometry(1.0), rt.working_geometry(2.0)
        self.assertAlmostEqual(rt.SENSOR_PX[0] * g1['object_pixel_m'], 0.225, delta=1e-12)         # 22.5 cm field at zoom 1
        self.assertAlmostEqual(g1['object_pixel_m'], 66.4e-6, delta=0.2e-6)
        self.assertTrue(0.02 < g1['na'] < 0.04)
        self.assertAlmostEqual(g2['collection'] / g1['collection'], 3.6, delta=0.15)               # moving the camera closer collects more light
        h = powered()
        self.assertTrue(np.allclose(h.read()['camera.field_of_view_m'], [0.225, 0.18], atol=2e-4))                # 3388 x 2712 pixels: 22.5 x 18.01 cm
        h.invoke('camera.set', binning=1, zoom=4.0, auto_focus=True, lock=False)
        fov = h.read()['camera.field_of_view_m']
        self.assertAlmostEqual(fov[0], 0.225 / 4, delta=1e-4)
        self.assertAlmostEqual(h._geometry()['obj_px_m'], g2['object_pixel_m'], delta=1e-12)     # digital zoom crops: pixel size of the 2x optics
        check('optics', na_zoom1=g1['na'], collection_ratio=g2['collection'] / g1['collection'])


class Imaging(unittest.TestCase):
    def test_ccd_cooling_before_capture(self):
        h = powered(cool=False)
        h.invoke('mode.select', mode='chemi_blots')
        self.assertFalse(h.read()['camera.ready'])
        with self.assertRaises(rt.ActionRejected):
            h.invoke('capture.run', hdr=False)
        t = 0.0
        while not h.read()['camera.ready']:
            h.step(1.0)
            t += 1.0
        self.assertTrue(200.0 <= t <= 300.0, t)                                                    # 90 s x ln(30 K / 2 K) = 244 s
        self.assertAlmostEqual(h.ccd_temperature(), h._room() - rt.CCD_DELTA_K + 2.0, delta=0.05)
        h.invoke('capture.run', hdr=False)
        check('ccd_cooling', ready_after_s=t)

    def test_fluorescence_dilution_series_and_quantification(self):
        h = loaded(blot())
        iid = smart_capture(h)
        an = h.invoke('image.analyze', image_id=iid, channel='EX3', sensitivity=0.5)
        self.assertEqual(len(an['lanes']), 8)
        v = first_band_volumes(an)
        for a, b in zip(v, v[1:]):
            self.assertAlmostEqual(b / a, 2.0, delta=0.12)                                          # linear 2-fold series
        for l in an['lanes']:
            self.assertAlmostEqual(l['bands'][0]['rf'], 0.020 / 0.065, delta=0.01)
            self.assertAlmostEqual(l['bands'][0]['band_purity_pct'], 100.0, delta=1e-9)
        q = h.invoke('image.quantify', image_id=iid, channel='EX3', standards=[dict(lane=i, band=1, amount_kg=1e-10 * 2 ** (i - 1)) for i in (1, 2, 3)], sensitivity=0.5)
        est = {u['lane']: u['amount_kg'] for u in q['unknowns']}
        for lane in range(4, 9):
            self.assertAlmostEqual(est[lane] / (1e-10 * 2 ** (lane - 1)), 1.0, delta=0.05)
        self.assertEqual({u['flag'] for u in q['unknowns']}, {'above_range'})
        self.assertGreater(q['r2'], 0.999)
        check('fluorescence_quant', ratios=[b / a for a, b in zip(v, v[1:])], r2=q['r2'])

    def test_smart_exposure_puts_the_brightest_pixel_near_80_percent(self):
        for ch, dye in (('EX3', 'alexa_fluor_647'), ('EX2', 'alexa_fluor_555'), ('EX5', 'irdye_800cw')):
            h = loaded(blot(label=dye), channels=((ch, dye),))
            iid = smart_capture(h)
            mx = h.invoke('measurement.result', image_id=iid)['channels'][0]['max_adu']
            self.assertTrue(0.65 * rt.ADC_MAX < mx < 0.95 * rt.ADC_MAX, (ch, mx))
        check('smart_exposure')

    def test_saturation_and_binning(self):
        h = loaded(blot())
        h.invoke('exposure.set', channel='EX3', exposure_s=60.0)
        iid = smart_capture(h, smart=False)
        r = h.invoke('measurement.result', image_id=iid)
        self.assertGreater(r['channels'][0]['saturated_px'], 0)
        self.assertTrue(any(w.startswith('saturated_pixels') for w in r['warnings']))
        an = h.invoke('image.analyze', image_id=iid, channel='EX3', sensitivity=0.5)
        self.assertTrue(an['lanes'][-1]['bands'][0]['saturated'])
        self.assertFalse(an['lanes'][0]['bands'][0]['saturated'])
        peaks = {}
        for b in (1, 2, 4):
            k = loaded(blot())
            k.invoke('camera.set', binning=b, zoom=k.camera['zoom'], auto_focus=True, lock=False)
            k.invoke('exposure.set', channel='EX3', exposure_s=0.5)
            peaks[b] = k.invoke('measurement.result', image_id=smart_capture(k, smart=False))['channels'][0]['max_adu'] - rt.BIAS_ADU
        self.assertAlmostEqual(peaks[2] / peaks[1], 4.0, delta=0.6)                                  # binning sums the charge of b x b pixels
        self.assertAlmostEqual(peaks[4] / peaks[2], 4.0, delta=0.6)
        check('saturation_binning', peaks=peaks)

    def test_filter_sets_crosstalk_and_mismatch(self):
        h = loaded(blot(), channels=(('EX3', 'alexa_fluor_647'), ('EX4', 'alexa_fluor_680')))
        self.assertIn('ex3_ex4_crosstalk', h.records and [] or h.invoke('channels.set', channels=[dict(channel='EX3', dye='alexa_fluor_647'),
                                                                                                  dict(channel='EX4', dye='alexa_fluor_680')])['warnings'])
        iid = smart_capture(h)
        a4 = h.invoke('image.analyze', image_id=iid, channel='EX4', sensitivity=0.6)
        self.assertGreaterEqual(len(a4['lanes']), 4)                                                # AF647 bleeds into the EX4/EM4 channel
        self.assertIn('ex3_ex4_crosstalk', h.invoke('measurement.result', image_id=iid)['warnings'])
        with self.assertRaises(rt.ActionRejected):
            h.invoke('channels.set', channels=[dict(channel='EX5', dye='alexa_fluor_488')])             # AF488 is not seen through 800-850 nm
        with self.assertRaises(rt.ActionRejected):
            h.invoke('channels.set', channels=[dict(channel=c, dye=d) for c, d in (('EX1', 'alexa_fluor_488'), ('EX2', 'alexa_fluor_555'), ('EX3', 'alexa_fluor_647'),
                                                                                  ('EX4', 'alexa_fluor_680'), ('EX5', 'irdye_800cw'))])
        self.assertGreater(rt.dye_rate('alexa_fluor_647', 'EX3'), 5 * rt.dye_rate('alexa_fluor_647', 'EX1'))
        check('filters')

    def test_chemiluminescence_decay_and_hdr(self):
        s = blot(label='hrp', base=1e-18, factor=3.0, support='nitrocellulose', substrate='supersignal_west_pico_plus')
        h = loaded(s, mode='chemi_blots')
        h.invoke('exposure.set', channel='CHEMI', exposure_s=10.0)
        plain = smart_capture(h, smart=False)
        h.invoke('capture.run', hdr=True)
        h.step(600.0)
        hdr = max(h.images)
        rp, rh = h.invoke('measurement.result', image_id=plain), h.invoke('measurement.result', image_id=hdr)
        self.assertGreater(rp['channels'][0]['saturated_px'], 0)
        self.assertEqual(rh['channels'][0]['saturated_px'], 0)                                      # Smart Range HDR: no saturated pixels, still 16-bit
        vh = first_band_volumes(h.invoke('image.analyze', image_id=hdr, channel='CHEMI', sensitivity=0.7))
        strong = [x for x in vh if x][-3:]
        for a, b in zip(strong, strong[1:]):
            self.assertAlmostEqual(b / a, 3.0, delta=0.25)                                           # linear across the lanes that saturate without HDR
        k = loaded(s, mode='chemi_blots')
        k.invoke('exposure.set', channel='CHEMI', exposure_s=1.0)
        i1 = smart_capture(k, smart=False)
        t1 = k.images[i1]['captured_s']
        early = k.invoke('image.analyze', image_id=i1, channel='CHEMI', sensitivity=0.7)
        k.step(6 * 3600.0)
        i2 = smart_capture(k, smart=False)
        late = k.invoke('image.analyze', image_id=i2, channel='CHEMI', sensitivity=0.7)
        self.assertEqual(k.invoke('measurement.result', image_id=i1)['channels'][0]['saturated_px'], 0)
        e, l = first_band_volumes(early)[-1], first_band_volumes(late)[-1]
        expect = math.exp(-(k.images[i2]['captured_s'] - t1) / rt.SUBSTRATE_TAU_S['supersignal_west_pico_plus'])
        self.assertAlmostEqual(l / e, expect, delta=0.04)                                          # substrate decay
        with self.assertRaises(rt.ActionRejected):
            loaded(blot()).invoke('capture.run', hdr=True)
        check('chemi', decay=l / e)

    def test_visible_coomassie_gel_and_marker_molar_mass(self):
        mk = [250, 150, 100, 75, 50, 37, 25, 20, 15, 10]
        rf = [0.08, 0.15, 0.22, 0.30, 0.40, 0.50, 0.62, 0.70, 0.80, 0.90]
        lanes = [dict(x_m=0.008, width_m=0.005, bands=[dict(y_m=0.068 * f, sigma_m=0.0006, label='coomassie_r250', amount_kg=0.5e-9, molar_mass_kg_mol=float(k))
                                                         for k, f in zip(mk, rf)])]
        for i in range(1, 6):
            lanes.append(dict(x_m=0.008 + 0.0085 * i, width_m=0.005, bands=[dict(y_m=0.068 * 0.35, sigma_m=0.0007, label='coomassie_r250', amount_kg=0.1e-9 * i)]))
        h = loaded(dict(sample_id='gel', support='polyacrylamide_gel', width_m=0.086, height_m=0.068, lanes=lanes), mode='protein_gels')
        iid = smart_capture(h)
        an = h.invoke('image.analyze', image_id=iid, channel='VISIBLE', sensitivity=0.6, marker_lane=1, marker_molar_mass_kg_mol=[float(k) for k in mk])
        self.assertTrue(an['inverted'])
        self.assertEqual(len(an['lanes']), 6)
        self.assertEqual(len(an['lanes'][0]['bands']), 10)
        v = first_band_volumes(an)[1:]
        self.assertTrue(all(b > a for a, b in zip(v, v[1:])))                                        # absorbed light grows with the stained mass
        self.assertAlmostEqual(v[1] / v[0], 2.0, delta=0.2)
        for l in an['lanes'][1:]:
            self.assertAlmostEqual(l['bands'][0]['molar_mass_kg_mol'], 10 ** (math.log10(75) + 0.5 * math.log10(50 / 75)), delta=2.0)
        check('coomassie', volumes=v)

    def test_nucleic_acid_gel_and_free_stain(self):
        lanes = [dict(x_m=0.008 + 0.0095 * i, width_m=0.006, bands=[dict(y_m=0.030, sigma_m=0.0008, label='sybr_safe', amount_kg=2e-12 * (i + 1))]) for i in range(6)]   # 2-12 ng DNA
        clean = loaded(dict(sample_id='dna', support='agarose_gel', width_m=0.07, height_m=0.065, lanes=lanes), mode='nucleic_acid_gels')
        dirty = loaded(dict(sample_id='dna', support='agarose_gel', width_m=0.07, height_m=0.065, lanes=lanes, free_stain_mol_m2=2e-8), mode='nucleic_acid_gels')
        for h in (clean, dirty):
            h.invoke('exposure.set', channel='GREEN_TRANS', exposure_s=0.5)
        ic, idr = smart_capture(clean, smart=False), smart_capture(dirty, smart=False)
        mc, md = clean.invoke('measurement.result', image_id=ic), dirty.invoke('measurement.result', image_id=idr)
        self.assertGreater(md['channels'][0]['mean_adu'], mc['channels'][0]['mean_adu'] + 20)           # unbound stain raises the gel background
        an = clean.invoke('image.analyze', image_id=ic, channel='GREEN_TRANS', sensitivity=0.6)
        v = first_band_volumes(an)
        self.assertEqual(len(v), 6)
        for i in range(1, 6):
            self.assertAlmostEqual(v[i] / v[0], i + 1, delta=0.15 * (i + 1))
        check('nucleic_acid', background_clean=mc['channels'][0]['mean_adu'], background_free_stain=md['channels'][0]['mean_adu'])

    def test_auto_align_zoom_and_lock(self):
        h = loaded(blot(), skew=0.12)
        self.assertAlmostEqual(h.stage_angle, 0.12, delta=1e-12)                                    # the stage squares the sample
        self.assertEqual(h.read('evaluator')['truth.sample_skew_rad'], 0.0)
        self.assertAlmostEqual(h.camera['zoom'], 2.0, delta=1e-12)                                  # a mini blot fills the 2x field
        big = loaded(blot(W=0.15, H=0.11), skew=0.0)
        self.assertTrue(1.0 < big.camera['zoom'] < 1.5, big.camera['zoom'])
        k = loaded(blot(), skew=0.25)
        self.assertAlmostEqual(k.stage_angle, rt.STAGE_ROT_RAD, delta=1e-12)                        # at most 10 deg
        iid = smart_capture(k)
        self.assertIn('sample_skewed', k.invoke('measurement.result', image_id=iid)['warnings'])
        k.invoke('camera.set', binning=2, zoom=1.5, auto_focus=True, lock=True)
        k.invoke('drawer.open')
        k.step(5.0)
        k.invoke('sample.remove', sample_id='blot')
        k.invoke('sample.place', sample_id='blot', skew_rad=0.0)
        k.invoke('drawer.close')
        k.step(5.0)
        self.assertEqual((k.camera['zoom'], k.stage_angle), (1.5, rt.STAGE_ROT_RAD))                 # locked: no auto rotation / zoom (user guide p.14)
        with self.assertRaises(rt.ActionRejected):
            k.invoke('exposure.set', channel='EX3', exposure_s=3.0)
        h.invoke('drawer.open')
        h.step(5.0)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('sample.place', sample_id='blot', skew_rad=0.0)                                  # already on the stage
        big2 = powered()
        big2.invoke('sample.provide', **blot(sid='huge', W=0.22, H=0.17))
        big2.invoke('drawer.open')
        big2.step(5.0)
        with self.assertRaises(rt.ActionRejected):
            big2.invoke('sample.place', sample_id='huge', skew_rad=0.3)                             # beyond the 24 x 20 cm glass when skewed
        big2.invoke('sample.place', sample_id='huge', skew_rad=0.0)
        check('auto_align', zoom_big=big.camera['zoom'])

    def test_faults(self):
        base = loaded(blot())
        base.invoke('exposure.set', channel='EX3', exposure_s=2.0)
        ref = base.invoke('measurement.result', image_id=smart_capture(base, smart=False))['channels'][0]
        led = loaded(blot())
        led.invoke('exposure.set', channel='EX3', exposure_s=2.0)
        led.invoke('fault.inject', fault='led_failure', active=True)
        dark = led.invoke('measurement.result', image_id=smart_capture(led, smart=False))['channels'][0]
        self.assertLess(dark['max_adu'], rt.BIAS_ADU + 50)
        sm = loaded(blot())
        sm.invoke('exposure.set', channel='EX3', exposure_s=2.0)
        sm.invoke('fault.inject', fault='stage_smudge', active=True)
        smr = sm.invoke('measurement.result', image_id=smart_capture(sm, smart=False))['channels'][0]
        self.assertGreater(smr['min_adu'], ref['min_adu'])
        fj = loaded(blot())
        fj.invoke('fault.inject', fault='filter_wheel_jam', active=True)
        fj.invoke('capture.run', hdr=False)
        fj.step(10.0)
        self.assertEqual(list(fj.records.values())[-1]['status'], 'failed')
        self.assertEqual(fj.images, {})
        db = loaded(blot())
        db.invoke('fault.inject', fault='drawer_blocked', active=True)
        db.invoke('drawer.open')
        db.step(10.0)
        self.assertEqual((db.drawer['state'], db.read()['drawer.y_m']), ('stopped', 0.0))
        pl = loaded(blot())
        pl.invoke('exposure.set', channel='EX3', exposure_s=20.0)
        pl.invoke('capture.run', hdr=False)
        pl.step(5.0)
        pl.invoke('fault.inject', fault='power_loss', active=True)
        self.assertEqual((list(pl.records.values())[-1]['status'], pl.images), ('failed', {}))
        with self.assertRaises(rt.ActionRejected):
            pl.invoke('power.set', enabled=True)
        pl.step(900.0)
        pl.invoke('fault.inject', fault='power_loss', active=False)
        self.assertTrue(pl.power)
        self.assertFalse(pl.read()['camera.ready'])                                                 # the CCD warmed while off
        check('faults', ref_min=ref['min_adu'], smudge_min=smr['min_adu'])


class ReviewRound1(unittest.TestCase):
    """Behaviour fixed after the independent runtime review round 1 (F-01 ... F-M09)."""

    def test_stopped_drawer_state_is_declared(self):
        for how in ('blocked', 'power'):
            h = loaded(blot())
            h.register_principal('ag', 'agent')
            if how == 'blocked':
                h.invoke('fault.inject', fault='drawer_blocked', active=True)
            h.invoke('drawer.open')
            h.step(2.0)
            if how == 'power':
                h.invoke('fault.inject', fault='power_loss', active=True)
            h.step(5.0)
            obs = h.session('ag').read(instance_id=INST)['observations']
            self.assertEqual(obs['drawer.state']['value'], 'stopped')
            for k, o in obs.items():
                self.assertEqual(asset_tool.validate_value(o['value'], _meta()['state_slots'][k]['value_schema']), [], k)
        check('stopped_state')

    def test_hdr_is_linear_and_not_flagged_saturated(self):
        lanes = [dict(x_m=0.02, width_m=0.006, bands=[dict(y_m=0.03, sigma_m=0.0007, label='hrp', amount_mol=1e-16)]),
                 dict(x_m=0.05, width_m=0.006, bands=[dict(y_m=0.03, sigma_m=0.0007, label='hrp', amount_mol=1e-18)])]
        h = loaded(dict(sample_id='c', support='nitrocellulose', width_m=0.086, height_m=0.068, lanes=lanes, substrate='supersignal_west_pico_plus'), mode='chemi_blots')
        h.invoke('exposure.set', channel='CHEMI', exposure_s=600.0)
        h.invoke('capture.run', hdr=True)
        h.step(700.0)
        iid = max(h.images)
        r = h.invoke('measurement.result', image_id=iid)['channels'][0]
        self.assertLess(r['max_adu'], rt.ADC_MAX)
        an = h.invoke('image.analyze', image_id=iid, channel='CHEMI', sensitivity=0.7)
        self.assertEqual(len(an['lanes']), 2)
        self.assertFalse(any(b['saturated'] for l in an['lanes'] for b in l['bands']))
        v = first_band_volumes(an)
        self.assertAlmostEqual(v[0] / v[1], 100.0, delta=8.0)                                        # linear across two decades
        self.assertIn('frame_from_live_view', an['warnings'])                                      # no membrane channel: the live-view outline
        self.assertAlmostEqual(an['lanes'][0]['bands'][0]['rf'], 0.03 / 0.068, delta=0.02)
        check('hdr', ratio=v[0] / v[1])

    def test_visible_white_clipping_is_flagged(self):
        lanes = [dict(x_m=0.01 + 0.012 * i, width_m=0.006, bands=[dict(y_m=0.03, sigma_m=0.0007, label='coomassie_r250', amount_kg=0.5e-9 * 2 ** i)]) for i in range(5)]
        h = loaded(dict(sample_id='g', support='polyacrylamide_gel', width_m=0.086, height_m=0.068, lanes=lanes), mode='protein_gels')
        ok = h.invoke('measurement.result', image_id=smart_capture(h, smart=False))['channels'][0]       # default visible exposure
        self.assertLess(ok['saturated_fraction'], 0.01)
        h.invoke('smart_exposure.run')
        h.step(30.0)
        t = h.exposure['VISIBLE']
        h.invoke('exposure.set', channel='VISIBLE', exposure_s=2.0 * t)
        iid = smart_capture(h, smart=False)
        an = h.invoke('image.analyze', image_id=iid, channel='VISIBLE', sensitivity=0.6)
        self.assertIn('saturated_bands', an['warnings'])
        self.assertTrue(any(b['saturated'] for l in an['lanes'] for b in l['bands']))
        check('white_clip')

    def test_narrow_lane_gaps_and_empty_lanes(self):
        for w, g, n in ((5.3, 2.2, 10), (3.4, 1.6, 15)):
            lanes = [dict(x_m=0.004 + w / 2e3 + i * (w + g) / 1e3, width_m=w / 1e3, bands=[dict(y_m=0.02, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=1e-14)])
                     for i in range(n)]
            h = loaded(dict(sample_id='b', support='pvdf_lf', width_m=lanes[-1]['x_m'] + w / 2e3 + 0.004, height_m=0.065, lanes=lanes))
            an = h.invoke('image.analyze', image_id=smart_capture(h), channel='EX3', sensitivity=0.5)
            self.assertEqual(len(an['lanes']), n, (w, g))
            v = first_band_volumes(an)
            self.assertLess(max(v) / min(v), 1.1)
        lanes = [dict(x_m=0.008 + 0.0095 * i, width_m=0.006, bands=([] if i in (1, 3) else [dict(y_m=0.02, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=1e-14)]))
                 for i in range(6)]
        h = loaded(dict(sample_id='e', support='pvdf_lf', width_m=0.065, height_m=0.065, lanes=lanes))
        iid = smart_capture(h)
        an = h.invoke('image.analyze', image_id=iid, channel='EX3', sensitivity=0.5)
        self.assertEqual([len(l['bands']) for l in an['lanes']], [1, 0, 1, 0, 1, 1])                 # physical order, empty lanes kept
        cen = [l['x_m'] for l in an['lanes']]
        an2 = h.invoke('image.analyze', image_id=iid, channel='EX3', sensitivity=0.5, lane_centres_m=cen[:3])
        self.assertEqual(len(an2['lanes']), 3)
        self.assertIn('lanes_from_user', an2['warnings'])
        check('lanes')

    def test_template_source_lists_only_the_chosen_dyes(self):
        lanes = [dict(x_m=0.02, width_m=0.006, bands=[dict(y_m=0.02, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=1e-14)]),
                 dict(x_m=0.04, width_m=0.006, bands=[dict(y_m=0.02, sigma_m=0.0006, label='alexa_fluor_790', amount_mol=1e-14)])]
        h = loaded(dict(sample_id='hid', support='pvdf_lf', width_m=0.06, height_m=0.065, lanes=lanes))
        r = h.invoke('measurement.result', image_id=smart_capture(h))
        self.assertEqual(r['template_source'], ['fluorophore_library.json#alexa_fluor_647'])
        check('template_source')

    def test_pixel_integration_and_zoom_independence(self):
        vols = []
        for dy in (0.0, 0.1e-3, 0.2e-3, 0.3e-3):
            lanes = [dict(x_m=0.03, width_m=0.006, bands=[dict(y_m=0.03 + dy, sigma_m=0.0001, label='alexa_fluor_647', amount_mol=1e-14)])]
            h = loaded(dict(sample_id='n', support='pvdf_lf', width_m=0.06, height_m=0.065, lanes=lanes))
            h.invoke('camera.set', binning=2, zoom=1.0, auto_focus=True, lock=False)
            h.invoke('exposure.set', channel='EX3', exposure_s=1.0)
            vols.append(first_band_volumes(h.invoke('image.analyze', image_id=smart_capture(h, smart=False), channel='EX3', sensitivity=0.5))[0])
        self.assertLess(max(vols) / min(vols), 1.08)                                                  # narrow bands no longer alias
        vz = {}
        for zoom in (2.0, 8.0):
            lanes = [dict(x_m=0.03, width_m=0.006, bands=[dict(y_m=0.03, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=2e-15)])]
            h = loaded(dict(sample_id='z', support='pvdf_lf', width_m=0.06, height_m=0.065, lanes=lanes))
            h.invoke('camera.set', binning=2, zoom=zoom, auto_focus=True, lock=False)
            h.invoke('exposure.set', channel='EX3', exposure_s=1.0)
            vz[zoom] = first_band_volumes(h.invoke('image.analyze', image_id=smart_capture(h, smart=False), channel='EX3', sensitivity=0.5))[0]
        self.assertAlmostEqual(vz[8.0] / vz[2.0], 1.0, delta=0.12)                                   # digital zoom only crops
        check('pixel_integration', aliasing=max(vols) / min(vols), zoom_ratio=vz[8.0] / vz[2.0])

    def test_fault_mid_capture_leaves_earlier_channels(self):
        h = loaded(blot(), channels=(('EX3', 'alexa_fluor_647'), ('EX1', 'alexa_fluor_488')))
        for c in ('EX3', 'EX1'):
            h.invoke('exposure.set', channel=c, exposure_s=10.0)
        h.invoke('capture.run', hdr=False)
        h.step(12.0)                                                                                  # EX3 read out at 11.5 s
        h.invoke('fault.inject', fault='led_failure', active=True)
        h.step(20.0)
        r = {c['channel']: c for c in h.invoke('measurement.result', image_id=max(h.images))['channels']}
        self.assertGreater(r['EX3']['max_adu'], 10 * rt.BIAS_ADU)
        self.assertLess(r['EX1']['max_adu'], rt.BIAS_ADU + 60)
        check('fault_mid_capture')

    def test_gallery_delete_ledger_export_and_smart_target(self):
        h = loaded(blot())
        L0 = h.ledger()
        h.invoke('smart_exposure.run')
        h.step(30.0)
        L1 = h.ledger()
        self.assertGreater(L1['exposure_time_s'], L0['exposure_time_s'])                              # test shots are booked
        self.assertGreater(L1['led_energy_j'], L0['led_energy_j'])
        h.invoke('exposure.set', channel='EX3', exposure_s=20.0)
        h.invoke('capture.run', hdr=False)
        h.step(1.5 + 5.0)
        h.register_principal('ag', 'agent')
        L2 = h.ledger()
        h.invoke('fault.inject', fault='power_loss', active=True)
        L3 = h.ledger()
        self.assertAlmostEqual(L3['led_energy_j'] - L2['led_energy_j'], rt.POWER_LED_W * 5.0, delta=1e-6)   # the exposed part of the aborted capture
        k = loaded(blot())
        iid = smart_capture(k)
        mx = k.invoke('measurement.result', image_id=iid)['channels'][0]['max_adu']
        self.assertTrue(0.75 * rt.ADC_MAX < mx < 0.9 * rt.ADC_MAX, mx)
        a16 = k.invoke('image.export', image_id=iid, fmt='tiff', analysis_16bit=True)['bytes']
        pub = k.invoke('image.export', image_id=iid, fmt='tiff', analysis_16bit=False)['bytes']
        r = k.invoke('measurement.result', image_id=iid)
        self.assertAlmostEqual(pub, 3 * r['width_px'] * r['height_px'] + 8192, delta=1)               # 24-bit publication composite
        self.assertNotEqual(a16, pub)
        k.invoke('image.delete', image_id=iid)
        self.assertEqual(k.images, {})
        self.assertIsNone(k.read()['result.last_image_id'])
        big = powered()
        big.invoke('sample.provide', **blot(sid='wide', W=0.235, H=0.19))
        check('gallery_ledger_export')


def chemi(amounts, spacing=0.023, x0=0.02):
    lanes = [dict(x_m=x0 + spacing * i, width_m=0.006, bands=[dict(y_m=0.03, sigma_m=0.0007, label='hrp', amount_mol=a)]) for i, a in enumerate(amounts)]
    return dict(sample_id='c', support='nitrocellulose', width_m=0.086, height_m=0.068, lanes=lanes, substrate='supersignal_west_pico_plus')


class ReviewRound2(unittest.TestCase):
    """Regression tests for the round-2 review findings (F2-01 ... F2-08)."""

    def test_resolved_bands_are_split_at_the_valley(self):
        for gap in (2.4e-3, 3.0e-3, 3.6e-3):                                                            # 4, 5, 6 sigma
            s = dict(sample_id='two', support='pvdf_lf', width_m=0.086, height_m=0.068, lanes=[dict(x_m=0.043, width_m=0.006, bands=[
                dict(y_m=0.030, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=1e-14), dict(y_m=0.030 + gap, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=1e-14)])])
            h = loaded(s)
            b = h.invoke('image.analyze', image_id=smart_capture(h), channel='EX3', sensitivity=0.5)['lanes'][0]['bands']
            self.assertEqual(len(b), 2, gap)
            self.assertAlmostEqual(b[0]['lbc_volume'] / b[1]['lbc_volume'], 1.0, delta=0.12)
        mk = [250, 150, 100, 75, 50, 37, 25, 20, 15, 10]                                                # the reviewer's marker spacing (2.9-6.7 mm)
        ys = [0.006]
        for d in (6.66, 5.28, 3.75, 5.28, 3.92, 5.11, 2.91, 3.75, 5.28):
            ys.append(ys[-1] + d * 1e-3)
        y60 = ys[3] + (math.log10(75) - math.log10(60)) / (math.log10(75) - math.log10(50)) * (ys[4] - ys[3])
        lanes = [dict(x_m=0.010, width_m=0.005, bands=[dict(y_m=y, sigma_m=0.0006, label='coomassie_r250', amount_kg=0.5e-9) for y in ys]),
                 dict(x_m=0.020, width_m=0.005, bands=[dict(y_m=y60, sigma_m=0.0006, label='coomassie_r250', amount_kg=0.5e-9)])]
        h = loaded(dict(sample_id='gel', support='polyacrylamide_gel', width_m=0.086, height_m=0.068, lanes=lanes), mode='protein_gels')
        an = h.invoke('image.analyze', image_id=smart_capture(h), channel='VISIBLE', sensitivity=0.6, marker_lane=1, marker_molar_mass_kg_mol=[float(k) for k in mk])
        self.assertEqual(len(an['lanes'][0]['bands']), 10)
        self.assertAlmostEqual(an['lanes'][1]['bands'][0]['molar_mass_kg_mol'], 60.0, delta=4.0)
        check('band_resolution', gaps_mm=[2.4, 3.0, 3.6])

    def test_room_change_during_an_exposure(self):
        def run(dT, at):
            h = loaded(chemi((1e-16,)), mode='chemi_blots')
            h.invoke('exposure.set', channel='CHEMI', exposure_s=600.0)
            h.invoke('capture.run', hdr=False)
            h.step(at)
            if dT:
                h.invoke('plant.configure', room_k=rt.PLANT_EXAMPLE['room_k'] + dT, mains_voltage_v=230.0, noise_seed=1)
            h.step(700.0)
            self.assertIsNone(h.op)
            self.assertEqual(len(h.images), 1)
            return h.invoke('measurement.result', image_id=max(h.images))['channels'][0]['mean_adu']
        m0 = run(0.0, 300.0)
        self.assertLess(abs(run(-1.0, 300.0) - m0), 3.0)                                               # the room change does not create dark charge
        self.assertLess(abs(run(-10.0, 595.0) - m0), 3.0)
        warm = run(+5.0, 100.0)                                                                         # warmer room: warmer CCD, a little more dark charge
        self.assertTrue(m0 < warm < m0 + 40.0, (m0, warm))
        check('room_change_mid_exposure', mean_adu=m0, warm=warm)

    def test_user_lane_centres_are_validated(self):
        h = loaded(blot(n=6))
        iid = smart_capture(h)
        an = h.invoke('image.analyze', image_id=iid, channel='EX3', sensitivity=0.5)
        fw = an['frame_m'][2] - an['frame_m'][0]
        xs = [l['x_m'] for l in an['lanes']]
        for bad, code in (([0.01, 0.02, fw + 0.01], 'LANE_OUTSIDE_FRAME'), ([0.02, 0.02], 'LANES_OVERLAP'), ([0.02, 0.0205], 'LANES_OVERLAP')):
            for cap_ in ('image.analyze', 'image.quantify'):
                kw = dict(standards=[dict(lane=1, band=1, amount_kg=1e-10), dict(lane=2, band=1, amount_kg=2e-10)]) if cap_ == 'image.quantify' else {}
                with self.assertRaises(rt.ActionRejected) as cm:
                    h.invoke(cap_, image_id=iid, channel='EX3', sensitivity=0.5, lane_centres_m=bad, **kw)
                self.assertEqual(str(cm.exception), code)
        srt = h.invoke('image.analyze', image_id=iid, channel='EX3', sensitivity=0.5, lane_centres_m=xs)
        uns = h.invoke('image.analyze', image_id=iid, channel='EX3', sensitivity=0.5, lane_centres_m=list(reversed(xs)))
        self.assertIn('lane_centres_sorted', uns['warnings'])
        self.assertEqual([l['width_m'] for l in srt['lanes']], [l['width_m'] for l in uns['lanes']])
        self.assertGreater(min(l['width_m'] for l in srt['lanes']), 0.004)
        vol = lambda a: [l['total_lane_volume'] for l in a['lanes']]                                    # noqa: E731
        self.assertEqual(vol(srt), vol(uns))
        self.assertAlmostEqual(sum(vol(srt)) / sum(vol(an)), 1.0, delta=0.05)
        check('lane_centres_validation')

    def test_image_delete_ownership_power_busy_and_events(self):
        h = loaded(blot())
        for n, v in (('a', 'agent'), ('b', 'agent'), ('ev', 'evaluator')):
            h.register_principal(n, v)
        a, b, ev = h.session('a'), h.session('b'), h.session('ev')

        def shoot(s, rid):
            r = s.invoke(instance_id=INST, capability_id='capture.run', arguments=dict(hdr=False), request_id=rid)
            h.step(60.0)
            return s.poll(r['operation_id'])['result']['image_id']

        def delete(s, iid, rid):
            return s.invoke(instance_id=INST, capability_id='image.delete', arguments=dict(image_id=iid), request_id=rid)
        ia, ie = shoot(a, 'ca'), shoot(ev, 'ce')
        self.assertEqual(delete(a, ie, 'd1')['error']['code'], 'ACCESS_DENIED')                          # evaluator images are read-only for agents
        self.assertEqual(delete(b, ia, 'd2')['error']['code'], 'ACCESS_DENIED')
        a.invoke(instance_id=INST, capability_id='capture.run', arguments=dict(hdr=False), request_id='c2')
        self.assertEqual(delete(a, ia, 'd3')['error']['code'], 'BUSY')
        h.step(60.0)
        self.assertEqual(delete(a, ia, 'd4')['status'], 'succeeded')
        seen_b = [e['message_zh'] for e in b.invoke(instance_id=INST, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e')['result']['events']]
        seen_ev = [e['message_zh'] for e in ev.invoke(instance_id=INST, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e')['result']['events']]
        self.assertFalse([m for m in seen_b if ia in m])
        self.assertTrue([m for m in seen_ev if ia in m and '删除' in m])
        h.invoke('power.set', enabled=False)
        self.assertEqual(delete(ev, ie, 'd5')['error']['code'], 'POWER_OFF')
        check('image_delete_rules')

    def test_lbc_does_not_depend_on_digital_zoom(self):
        s = dict(sample_id='z', support='pvdf_lf', width_m=0.086, height_m=0.068, lanes=[dict(x_m=0.043, width_m=0.005, bands=[dict(y_m=0.034, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=2e-15)])])
        res = {}
        for z in (2.0, 8.0):
            v = []
            for seed in (1, 2, 3):
                h = loaded(s, seed=seed)
                h.invoke('camera.set', binning=2, zoom=z, auto_focus=True, lock=False)
                h.invoke('capture.run', hdr=False)
                h.step(60.0)
                an = h.invoke('image.analyze', image_id=max(h.images), channel='EX3', sensitivity=0.5)
                v.append(max(an['lanes'][0]['bands'], key=lambda q: q['lbc_volume'])['lbc_volume'])
            res[z] = float(np.mean(v))
        self.assertAlmostEqual(res[8.0] / res[2.0], 1.0, delta=0.04)
        check('lbc_zoom', ratio=res[8.0] / res[2.0])

    def test_hdr_keeps_the_faint_lane_and_reports_its_scale(self):
        h = loaded(chemi((1e-15, 1e-18, 1e-19)), mode='chemi_blots')
        h.invoke('exposure.set', channel='CHEMI', exposure_s=600.0)
        h.invoke('capture.run', hdr=True)
        h.step(700.0)
        iid = max(h.images)
        an = h.invoke('image.analyze', image_id=iid, channel='CHEMI', sensitivity=0.5)
        self.assertEqual(len(an['lanes']), 3)
        mr = h.invoke('measurement.result', image_id=iid)
        self.assertTrue(0.0 < mr['hdr_scale'] < 1.0)
        self.assertEqual(an['hdr_scale'], mr['hdr_scale'])
        check('hdr_faint_lane', hdr_scale=mr['hdr_scale'])

    def test_large_dna_ladders_are_accepted(self):
        bp = [10000, 8000, 6000, 5000, 4000, 3000, 2000, 1500, 1000, 500]
        lanes = [dict(x_m=0.010, width_m=0.006, bands=[dict(y_m=0.006 + 0.005 * i, sigma_m=0.0007, label='sybr_safe', amount_kg=20e-12) for i in range(len(bp))])]
        h = loaded(dict(sample_id='dna', support='agarose_gel', width_m=0.07, height_m=0.065, lanes=lanes), mode='nucleic_acid_gels')
        h.invoke('exposure.set', channel='GREEN_TRANS', exposure_s=0.5)
        an = h.invoke('image.analyze', image_id=smart_capture(h, smart=False), channel='GREEN_TRANS', sensitivity=0.5, marker_lane=1,
                      marker_molar_mass_kg_mol=[0.650 * n for n in bp])
        self.assertEqual(an['lanes'][0]['bands'][0]['molar_mass_kg_mol'], 6500.0)
        check('dna_ladder_10kb')

    def test_fault_timing_in_ledger_and_smart_exposure(self):
        h = loaded(blot())
        h.register_principal('ev', 'evaluator')
        ev = h.session('ev')
        h.invoke('fault.inject', fault='led_failure', active=True)
        h.invoke('exposure.set', channel='EX3', exposure_s=20.0)
        r = ev.invoke(instance_id=INST, capability_id='capture.run', arguments=dict(hdr=False), request_id='c')
        h.step(rt.CAPTURE_OVERHEAD_S + 3.0)
        led0 = h.ledger()['led_energy_j']
        ev.cancel(r['operation_id'], 'x')
        self.assertEqual(h.ledger()['led_energy_j'], led0)                                             # a failed LED books no energy
        ok, bad = loaded(blot()), loaded(blot())
        for k in (ok, bad):
            k.invoke('smart_exposure.run')
        bad.step(0.2)
        bad.invoke('fault.inject', fault='led_failure', active=True)                                   # before the first test shot
        for k in (ok, bad):
            k.step(120.0)
        rec = lambda k: k.records[max(k.records)]                                                       # noqa: E731
        self.assertNotIn('weak_signal:EX3', rec(ok)['warnings'])
        self.assertIn('weak_signal:EX3', rec(bad)['warnings'])
        self.assertGreater(rec(bad)['recommended_exposure_s']['EX3'], 10 * rec(ok)['recommended_exposure_s']['EX3'])
        check('fault_timing')


class ReviewRound3(unittest.TestCase):
    """Regression tests for the round-3 review findings (F3-01 ... F3-04)."""

    def test_edge_lane_centre_keeps_a_finite_lane(self):
        h = loaded(blot(n=4))
        h.invoke('camera.set', binning=2, zoom=1.0, auto_focus=True, lock=False)
        h.invoke('capture.run', hdr=False)
        h.step(60.0)
        an = h.invoke('image.analyze', image_id=max(h.images), channel='EX3', sensitivity=0.5, lane_centres_m=[0.0, 0.0012])   # schema-checked by the wrapper
        self.assertTrue(all(l['width_m'] > 0 and math.isfinite(l['background_volume']) for l in an['lanes']))
        check('edge_lane_finite')

    def test_equal_bands_share_the_valley_row(self):
        s = dict(sample_id='two', support='pvdf_lf', width_m=0.086, height_m=0.068, lanes=[dict(x_m=0.043, width_m=0.006, bands=[
            dict(y_m=0.030, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=1e-14), dict(y_m=0.030 + 3.5 * 0.0006, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=1e-14)])])
        ratios = []
        for seed in (1, 2, 3):
            h = loaded(s, seed=seed)
            b = h.invoke('image.analyze', image_id=smart_capture(h), channel='EX3', sensitivity=0.5)['lanes'][0]['bands']
            self.assertEqual(len(b), 2)
            ratios.append(b[0]['lbc_volume'] / b[1]['lbc_volume'])
        self.assertAlmostEqual(float(np.mean(ratios)), 1.0, delta=0.06)
        check('valley_row_shared', ratio=float(np.mean(ratios)))

    def test_many_room_declarations_during_an_exposure(self):
        def run(n):
            h = loaded(chemi((1e-16,)), mode='chemi_blots')
            h.invoke('exposure.set', channel='CHEMI', exposure_s=600.0)
            h.invoke('capture.run', hdr=False)
            for _ in range(n):
                h.step(5.0)
                h.invoke('plant.configure', **rt.PLANT_EXAMPLE)                                         # the same room re-declared: a new CCD piece each time
            h.step(700.0)
            return h.invoke('measurement.result', image_id=max(h.images))['channels'][0]['mean_adu']
        self.assertAlmostEqual(run(100), run(0), delta=0.05)                                           # trapezoid rule per piece: numerical difference only
        check('ccd_history_pieces', pieces=100)

    def test_no_band_below_its_local_background(self):
        h = loaded(blot(n=3))
        iid = smart_capture(h)
        an = h.invoke('image.analyze', image_id=iid, channel='EX3', sensitivity=1.0, lane_centres_m=[0.050, 0.060, 0.070, 0.080])   # empty columns
        self.assertTrue(all(b['lbc_volume'] > 0 for l in an['lanes'] for b in l['bands']))
        check('no_negative_bands')


class ReviewRound4(unittest.TestCase):
    """Regression tests for the round-4 review findings (F4-03 ... F4-05)."""

    def test_degenerate_standard_curve_is_refused(self):
        h = loaded(blot(n=4))
        iid = smart_capture(h)
        for amounts in ((0.0, 1e-300), (0.0, 1e-200), (0.0, 5e-324)):                              # a finite (schema-checked) fit or a declared refusal, never a crash
            try:
                r = h.invoke('image.quantify', image_id=iid, channel='EX3', sensitivity=0.5,
                             standards=[dict(lane=1, band=1, amount_kg=amounts[0]), dict(lane=2, band=1, amount_kg=amounts[1])])
                self.assertTrue(math.isfinite(r['slope']) and math.isfinite(r['intercept']))
            except rt.ActionRejected as exc:
                self.assertEqual(str(exc), 'ANALYSIS_FAILED')
        ok = h.invoke('image.quantify', image_id=iid, channel='EX3', sensitivity=0.5, standards=[dict(lane=1, band=1, amount_kg=1e-10), dict(lane=2, band=1, amount_kg=2e-10)])
        self.assertTrue(math.isfinite(ok['slope']))
        check('degenerate_standards')

    def test_clipped_lanes_do_not_overlap(self):
        h = loaded(blot(n=4))
        h.invoke('camera.set', binning=8, zoom=1.0, auto_focus=True, lock=False)
        h.invoke('capture.run', hdr=False)
        h.step(60.0)
        an = h.invoke('image.analyze', image_id=max(h.images), channel='EX3', sensitivity=0.5, lane_centres_m=[0.0, 0.001, 0.02])
        edges = [(l['x_m'] - l['width_m'] / 2, l['x_m'] + l['width_m'] / 2) for l in an['lanes']]
        self.assertTrue(all(b0 >= a1 - 1e-12 for (a0, a1), (b0, b1) in zip(edges, edges[1:])))
        check('lanes_not_overlapping')

    def test_power_toggles_at_one_instant_keep_one_ccd_piece(self):
        h = powered()
        n0 = len(h.ccd_hist)
        for _ in range(200):
            h.invoke('power.press_switch')
        self.assertLessEqual(len(h.ccd_hist), n0 + 1)
        check('ccd_pieces_bounded')


class Framework(unittest.TestCase):
    def test_read_describe_pure_and_clock(self):
        h = loaded(blot())
        h.register_principal('ag', 'agent')
        v0 = h.read('evaluator')
        h.session('ag').describe(instance_id=INST)
        h.session('ag').read(instance_id=INST)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True), json.dumps(v0, sort_keys=True))
        t = h.invoke('sim.step', dt_s=2.25)['time_s']
        self.assertEqual(t, h.t)
        check('pure_reads')

    def test_step_partition_independent(self):
        def run(splits):
            h = powered(seed=4, cool=False)
            h.invoke('sample.provide', **blot())
            for dt in splits(300.0):
                h.step(dt)
            h.invoke('drawer.open')
            for dt in splits(5.0):
                h.step(dt)
            h.invoke('sample.place', sample_id='blot', skew_rad=0.03)
            h.invoke('drawer.close')
            for dt in splits(2.0):
                h.step(dt)
            v_mid = h.read('evaluator')
            for dt in splits(3.0):
                h.step(dt)
            h.invoke('mode.select', mode='fluorescent_blots')
            h.invoke('channels.set', channels=[dict(channel='EX3', dye='alexa_fluor_647')])
            h.invoke('smart_exposure.run')
            for dt in splits(30.0):
                h.step(dt)
            h.invoke('capture.run', hdr=False)
            for dt in splits(40.0):
                h.step(dt)
            return h, v_mid
        a, am = run(lambda T: [T])
        b, bm = run(lambda T: [T / 8] * 8)
        self.assertEqual(json.dumps(am, sort_keys=True), json.dumps(bm, sort_keys=True))
        for k, v in a.read('evaluator').items():
            w = b.read('evaluator')[k]
            if isinstance(v, float):
                self.assertAlmostEqual(v, w, delta=1e-9 * max(1.0, abs(v)), msg=k)
            else:
                self.assertEqual(v, w, k)
        self.assertEqual(json.dumps(a.records, sort_keys=True), json.dumps(b.records, sort_keys=True))
        self.assertTrue(np.array_equal(a.images['IMG0001']['channels'][0]['img'], b.images['IMG0001']['channels'][0]['img']))
        la, lb = a.ledger(), b.ledger()
        for k in la:
            self.assertAlmostEqual(la[k], lb[k], delta=1e-9 * max(1.0, abs(la[k])), msg=k)
        check('step_partition')

    def test_energy_and_exposure_ledger(self):
        h = powered(cool=False)
        h.step(3600.0)
        L = h.ledger()
        cool = rt.POWER_COOL_W * rt.CCD_TAU_S * (1 - math.exp(-3600.0 / rt.CCD_TAU_S))
        self.assertAlmostEqual(L['electrical_energy_j'], rt.POWER_IDLE_W * 3600.0 + cool, delta=1e-6)
        self.assertEqual(h.invoke('ledger.read')['ledger'], h.ledger())
        self.assertFalse(h.invoke('power.press_switch')['enabled'])                                 # the rear switch toggles
        h.step(600.0)
        self.assertAlmostEqual(h.ledger()['electrical_energy_j'], L['electrical_energy_j'], delta=1e-6)   # off: no draw
        self.assertTrue(h.invoke('power.press_switch')['enabled'])
        h.invoke('power.set', enabled=False)
        k = loaded(blot(), channels=(('EX3', 'alexa_fluor_647'), ('EX1', 'alexa_fluor_488')))
        for c, t in (('EX3', 2.0), ('EX1', 3.0)):
            k.invoke('exposure.set', channel=c, exposure_s=t)
        smart_capture(k, smart=False)
        L = k.ledger()
        self.assertAlmostEqual(L['exposure_time_s'], 5.0, delta=1e-12)
        self.assertAlmostEqual(L['led_energy_j'], rt.POWER_LED_W * 5.0, delta=1e-9)
        self.assertEqual(L['images'], 1.0)
        rec = list(k.records.values())[-1]
        self.assertAlmostEqual(rec['finished_s'] - rec['started_s'], 2 * rt.CAPTURE_OVERHEAD_S + 5.0, delta=1e-9)
        check('ledger', energy_1h_j=L['electrical_energy_j'])

    def test_rejections_leave_state_unchanged(self):
        h = loaded(blot())
        v0 = json.dumps(h.read('evaluator'), sort_keys=True, default=str)
        for cap, args in [('sample.place', dict(sample_id='blot', skew_rad=0.0)), ('drawer.close', {}), ('exposure.set', dict(channel='EX5', exposure_s=1.0)),
                          ('camera.set', dict(binning=7, zoom=1.0, auto_focus=True, lock=False)), ('channels.set', dict(channels=[dict(channel='CHEMI', dye=None)])),
                          ('capture.run', dict(hdr=True)), ('sample.provide', dict(blot(), sample_id='x', lanes=[dict(x_m=0.01, width_m=0.005, bands=[dict(y_m=0.01, sigma_m=0.001, label='nope', amount_mol=1e-15)])])),
                          ('image.analyze', dict(image_id='IMG9999', channel='EX3', sensitivity=0.5)), ('mode.select', dict(mode='fluorescent_blots')) if False else ('drawer.close', {})]:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(cap, **args)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True, default=str), v0)
        check('rejections_pure')

    def test_cancellation(self):
        h = loaded(blot())
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        h.invoke('exposure.set', channel='EX3', exposure_s=30.0)
        r = s.invoke(instance_id=INST, capability_id='capture.run', arguments=dict(hdr=False), request_id='c')
        self.assertEqual(r['status'], 'accepted')
        h.step(5.0)
        s.cancel(r['operation_id'], 'x')
        self.assertEqual(s.poll(r['operation_id'])['status'], 'cancelled')
        h.step(60.0)
        self.assertEqual(h.images, {})
        d = s.invoke(instance_id=INST, capability_id='drawer.open', arguments={}, request_id='d')
        with self.assertRaises(rt.ActionRejected):
            s.cancel(d['operation_id'], 'y')
        h.step(5.0)
        self.assertEqual(s.poll(d['operation_id'])['result']['drawer_state'], 'open')
        check('cancel')

    def test_two_instances_isolated(self):
        a, b = loaded(blot()), loaded(blot())
        smart_capture(a)
        self.assertEqual(b.images, {})
        self.assertEqual(b.records, {})
        check('isolation')

    def test_agent_truth_isolation_ownership_and_idempotence(self):
        h = loaded(blot())
        for n, v in (('a1', 'agent'), ('a2', 'agent'), ('ev', 'evaluator')):
            h.register_principal(n, v)
        s1, s2, e = h.session('a1'), h.session('a2'), h.session('ev')
        obs = s1.read(instance_id=INST)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.') or k == 'camera.temperature_k'])
        self.assertIn('truth.sample_skew_rad', e.read(instance_id=INST)['observations'])
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='sample.inspect', arguments=dict(sample_id='blot'), request_id='i')['error']['code'], 'ACCESS_DENIED')
        r1 = s1.invoke(instance_id=INST, capability_id='capture.run', arguments=dict(hdr=False), request_id='r')
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='capture.run', arguments=dict(hdr=False), request_id='r'), r1)
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='drawer.open', arguments={}, request_id='r')['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='drawer.open', arguments={}, request_id='x')['error']['code'], 'ACCESS_DENIED')
        self.assertTrue(s2.read(instance_id=INST)['observations']['operation.started_by_other_agent']['value'])
        h.step(60.0)
        iid = s1.poll(r1['operation_id'])['result']['image_id']
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='measurement.result', arguments=dict(image_id=iid), request_id='m')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='records.list', arguments={}, request_id='l')['result'], dict(records=[], images=[]))
        self.assertEqual(s1.read(instance_id=INST)['observations']['result.last_image_id']['value'], iid)
        self.assertIsNone(s2.read(instance_id=INST)['observations']['result.last_image_id']['value'])
        check('truth_isolation_ownership')

    def test_queries_export_and_unsupported(self):
        h = loaded(blot())
        iid = smart_capture(h)
        lst = h.invoke('records.list')
        self.assertEqual(lst['images'], [iid])
        r = h.invoke('measurement.result', image_id=iid)
        self.assertLessEqual(len(r['channels'][0]['preview']), 64)
        self.assertTrue(h.invoke('events.read', since_s=0.0)['events'])
        self.assertEqual(len(h.invoke('provenance.read')['libraries']), 1)
        ex = h.invoke('image.export', image_id=iid, fmt='tiff', analysis_16bit=True)
        self.assertGreater(ex['bytes'], 2 * r['width_px'] * r['height_px'])
        with self.assertRaises(rt.ActionRejected):
            h.invoke('image.export', image_id=iid, fmt='jpg', analysis_16bit=True)
        ins = h.invoke('sample.inspect', sample_id='blot')
        self.assertEqual(len(ins['lanes']['lanes']), 8)
        self.assertGreater(ins['expected_signal']['EX3']['lane_8'][0]['electrons_per_s'], 0)
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(k)
        check('queries')


# ---------------------------------------------------------------- declared effects over scenario states
_SC = {}


def _build(name):
    if name == 'fresh':
        return rt.ThermoIbrightFl1500Runtime()
    if name == 'off':
        h = rt.ThermoIbrightFl1500Runtime()
        h.invoke('plant.configure', **rt.PLANT_EXAMPLE)
        h.invoke('sample.provide', **blot())
        return h
    if name == 'cold':
        h = powered(cool=False)
        h.invoke('sample.provide', **blot())
        h.step(30.0)
        return h
    if name == 'ready':
        return loaded(blot())
    if name == 'open':
        h = loaded(blot())
        h.invoke('drawer.open')
        h.step(5.0)
        return h
    if name == 'open_empty':
        h = _scenario('open')
        h.invoke('sample.remove', sample_id='blot')
        return h
    if name == 'moving':
        h = loaded(blot())
        h.invoke('drawer.open')
        h.step(1.5)
        return h
    if name == 'capturing':
        h = loaded(blot())
        h.invoke('exposure.set', channel='EX3', exposure_s=20.0)
        h.invoke('capture.run', hdr=False)
        h.step(3.0)
        return h
    if name == 'smart':
        h = loaded(blot())
        h.invoke('smart_exposure.run')
        h.step(1.0)
        return h
    if name == 'imaged':
        h = loaded(blot())
        smart_capture(h)
        return h
    if name == 'locked':
        h = loaded(blot())
        h.invoke('camera.set', binning=2, zoom=2.0, auto_focus=True, lock=True)
        return h
    if name == 'chemi':
        return loaded(blot(label='hrp', base=1e-18, factor=3.0, substrate='generic_ecl'), mode='chemi_blots')
    if name == 'stopped':
        h = _scenario('moving')
        h.invoke('fault.inject', fault='power_loss', active=True)
        h.invoke('fault.inject', fault='power_loss', active=False)
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'cold', 'ready', 'open', 'open_empty', 'moving', 'capturing', 'smart', 'imaged', 'locked', 'chemi', 'stopped']
STEP_DTS = (0.5, 5.0, 60.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0), dict(rt.PLANT_EXAMPLE, room_k=300.15)],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_switch': [{}],
        'mode.select': [dict(mode='fluorescent_blots'), dict(mode='chemi_blots')], 'drawer.open': [{}], 'drawer.close': [{}],
        'sample.provide': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')], 'sample.place': [dict(sample_id='blot', skew_rad=0.05)], 'sample.remove': [dict(sample_id='blot')],
        'channels.set': [dict(channels=[dict(channel='EX3', dye='alexa_fluor_647')]), dict(channels=[dict(channel='EX3', dye='alexa_fluor_647'), dict(channel='VISIBLE', dye=None)])],
        'exposure.set': [dict(channel='EX3', exposure_s=2.0), dict(channel='CHEMI', exposure_s=5.0)],
        'camera.set': [dict(binning=4, zoom=1.0, auto_focus=True, lock=False), dict(binning=2, zoom=2.0, auto_focus=True, lock=True, rotation_rad=0.05)],
        'smart_exposure.run': [{}], 'capture.run': [dict(hdr=False), dict(hdr=True)],
        'image.export': [dict(image_id='IMG0001', fmt='tiff', analysis_16bit=True)], 'image.delete': [dict(image_id='IMG0001')],
        'fault.inject': [dict(fault=f, active=True) for f in ('power_loss', 'led_failure', 'drawer_blocked', 'filter_wheel_jam', 'stage_smudge')] + [dict(fault='power_loss', active=False)],
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
