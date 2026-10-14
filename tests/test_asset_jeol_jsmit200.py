"""tests for CHEM-151 jeol_jsmit200: python3 -m unittest tests/test_asset_jeol_jsmit200.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two
instances are isolated, agent/evaluator truth isolation, conservation (charge, gas), real return values validated against the declared
schemas (asset_tool lint_schema / validate_value), asynchronous completion and cancellation remnants, host-step independence; plus the
instrument behaviour documented by JEOL and the facility guides (start-up, draw-out exchange, EVAC within minutes and the 10-min limit,
VENT with the stage returning to the exchange position, LV 10-100 Pa with the BED only, probe sizes reproducing the catalogue resolutions,
kV and probe-current ranges, magnification on the 128 mm print, SE/BSE contrast, charging of uncoated insulators, EDS resolution
<= 130 eV at Mn Ka, Duane-Hunt limit, count rates, standardless quantification and automatic identification, stage strokes and
collision limits, filament life, mains tolerance).
"""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/chemistry-lab-assets/scripts"))
from asset_runtime import jeol_jsmit200 as rt  # noqa: E402
import asset_tool  # noqa: E402

OUT = ROOT / 'outputs/expansion_batch04/t3_analytical/jeol_jsmit200'
CHECKS, PROCESS_EVIDENCE = [], {}
_META = {}


def _meta():
    if not _META:
        _META.update(rt.export_jeol_jsmit200_contract_metadata())
    return _META


_orig_invoke = rt.JeolJsmit200Runtime.invoke


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
        undeclared = {k for k in after if after[k] != before[k]} - set(rt.CAPS[capability_id]['effects'])
        if undeclared:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.JeolJsmit200Runtime.invoke = _instrumented_invoke
FE_SI = rt.SPECIMEN_EXAMPLE


def spec(sid, sub, coating='none', thick=0.0, wet=False, topo='flat_polished', height=0.005, diameter=0.012, grain=2e-5):
    return dict(specimen_id=sid, phases=[dict(substance_id=sub, area_fraction=1.0, grain_size_m=grain)], diameter_m=diameter, height_m=height,
                topography=topo, coating=coating, coating_thickness_m=thick, wet=wet)


def configured(seed=1, **plant):
    h = rt.JeolJsmit200Runtime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def ready(seed=1, specimen=FE_SI, wd=0.010, evacuate=True, lv=False, **plant):
    """Powered, specimen loaded at the requested working distance, door closed and (optionally) evacuated."""
    h = configured(seed, **plant)
    if specimen:
        h.invoke('specimen.provide', **specimen)
    h.invoke('power.set', enabled=True)
    h.step(rt.SOFTWARE_BOOT_S + 1.0)
    if specimen:
        h.invoke('chamber.draw_out')
        h.step(3.0)
        h.invoke('specimen.load', specimen_id=specimen['specimen_id'])
        h.invoke('stage.set_z', z_lift_m=rt.WD_RANGE_M[1] - wd - specimen['height_m'])
        h.step(30.0)
        h.invoke('chamber.draw_in')
        h.step(3.0)
    if lv:
        h.invoke('vacuum.toggle_lv')
    if evacuate:
        h.invoke('vacuum.evacuate')
        h.step(240.0)
    return h


def observing(h, kv=15000.0, ip=1e-10, det='BED_COMPO', mag=1000.0):
    h.invoke('beam.set_voltage', voltage_v=kv)
    h.invoke('beam.set_probe_current', current_a=ip)
    h.invoke('beam.observation_on')
    h.step(rt.SATURATION_S + 1.0)
    h.invoke('detector.select', detector=det)
    h.invoke('scan.set_magnification', magnification=mag)
    h.invoke('optics.autofocus')
    h.invoke('optics.auto_stigmate')
    h.invoke('optics.auto_contrast_brightness')
    return h


def acquire_image(h, pixels='320x240', dwell=1e-5):
    op = h.invoke('image.acquire', pixels=pixels, dwell_s=dwell)['operation_id']
    h.step(h.operations[op]['duration'] + 1.0)
    return h.images[h.operations[op]['record_id']]


def acquire_eds(h, mode='point', live=20.0, pt='T3'):
    op = h.invoke('eds.acquire', mode=mode, live_time_s=live, process_time=pt)['operation_id']
    h.step(h.operations[op]['duration'] + 1.0)
    return h.spectra[h.operations[op]['record_id']]


def rejected(h, cap, code, **a):
    before = h.read('evaluator')
    rev = h.revision
    try:
        h.invoke(cap, **a)
    except rt.ActionRejected as exc:
        assert str(exc) == code, (cap, str(exc), code)
        assert h.read('evaluator') == before, cap
        assert h.revision == rev, cap
        return True
    raise AssertionError(f'{cap} was not rejected (expected {code})')


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.jeol_jsmit200')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-151')

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
        self.assertEqual(set(rt.EFFECTS) | set(rt.UNSUPPORTED), set(rt.CAPS))

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
        h = ready(evacuate=False)
        h.register_principal('a', 'agent')
        t, rev = h.time_ns, h.revision
        h.read('evaluator')
        s = h.session('a')
        s.read(instance_id=h.instance_id)
        s.describe(instance_id=h.instance_id)
        self.assertEqual((h.time_ns, h.revision), (t, rev))

    def test_step_zero_and_negative(self):
        h = configured()
        h.step(0.0)
        with self.assertRaises(rt.ActionRejected):
            h.step(-1.0)

    def test_host_step_chopping_does_not_change_the_result(self):
        def run(chunks):
            h = ready(seed=5, evacuate=False)
            h.invoke('vacuum.evacuate')
            for dt in chunks:
                h.step(dt)
            return h.read('evaluator')
        a = run([150.0])
        b = run([0.37] * 405 + [0.15])
        for k in ('truth.chamber_pressure_pa', 'vacuum.state', 'vacuum.chamber_pressure_indicated_pa', 'truth.tmp_speed_fraction'):
            if isinstance(a[k], float):
                self.assertAlmostEqual(a[k], b[k], delta=1e-6 * max(abs(a[k]), 1e-12), msg=k)
            else:
                self.assertEqual(a[k], b[k], k)

    def test_set_points_only_write_targets(self):
        h = ready(evacuate=False)
        z0 = h.stage['z']
        h.invoke('stage.set_z', z_lift_m=0.0)
        self.assertEqual(h.stage['z'], z0)                      # target only; the hand moves the stage during step
        h.step(1.0)
        self.assertAlmostEqual(z0 - h.stage['z'], rt.STAGE_Z_SPEED_M_S * 1.0, places=9)
        h.invoke('vacuum.set_lv_pressure', pressure_pa=80.0)
        self.assertEqual(h.lv_set, 80.0)
        p = h.p_ch
        self.assertEqual(h.read('evaluator')['truth.chamber_pressure_pa'], p)


class VacuumAndPanel(unittest.TestCase):
    def test_start_up_exchange_and_evacuation_time(self):
        h = ready(evacuate=False, vent_gas='room_air', relative_humidity=0.5)
        r = h.read()
        self.assertEqual((r['vacuum.state'], r['panel.vent_lamp_mode'], r['panel.evac_lamp_mode']), ('vented', 'on', 'off'))
        h.invoke('vacuum.evacuate')
        h.step(1.0)
        self.assertEqual(h.read()['panel.evac_lamp_mode'], 'blinking')
        t = 0
        while h.vac_state != 'hv_ready' and t < 600:
            h.step(1.0)
            t += 1
        self.assertEqual(h.vac_state, 'hv_ready')
        self.assertLess(t, 180)                                  # "a few minutes" (JSM-IT300 manual), well inside the 10-min limit
        self.assertGreater(t, 60)
        self.assertEqual(h.read()['panel.evac_lamp_mode'], 'on')
        self.assertLess(h.read()['vacuum.gun_pressure_indicated_pa'], 1e-2)

    def test_door_not_closed_times_out_after_ten_minutes(self):
        h = ready(evacuate=False)
        h.invoke('chamber.draw_out')
        h.step(3.0)
        h.invoke('vacuum.evacuate')
        h.step(rt.EVAC_LIMIT_S - 5)
        self.assertIsNone(h.error)
        h.step(10.0)
        self.assertEqual(h.error, 'EVAC_TIMEOUT')
        self.assertGreater(h.p_ch, 1e4)
        rejected(h, 'beam.observation_on', 'ERROR_PENDING')

    def test_vent_returns_xy_and_lamps(self):
        h = ready()
        h.invoke('stage.move_xy', x_m=0.01, y_m=-0.005)
        h.step(10.0)
        self.assertAlmostEqual(h.stage['x'], 0.01, places=9)
        h.invoke('vacuum.vent')
        h.step(1.0)
        self.assertEqual(h.read()['panel.vent_lamp_mode'], 'blinking')
        h.step(150.0)
        r = h.read('evaluator')
        self.assertEqual(r['vacuum.state'], 'vented')
        self.assertEqual((r['stage.x_m'], r['stage.y_m']), (0.0, 0.0))
        self.assertEqual(r['panel.vent_lamp_mode'], 'on')
        self.assertGreater(r['truth.chamber_pressure_pa'], rt.VENT_READY_PA)

    def test_blinking_lamp_alternates(self):
        h = ready(evacuate=False)
        h.invoke('vacuum.evacuate')
        seen = set()
        for _ in range(4):
            h.step(0.5)
            seen.add(h.read()['panel.evac_lamp'])
        self.assertEqual(seen, {True, False})

    def test_low_vacuum_regulates_and_keeps_the_gun_in_high_vacuum(self):
        for p_set in (10.0, 100.0):
            h = ready(lv=True, evacuate=False)
            h.invoke('vacuum.set_lv_pressure', pressure_pa=p_set)
            h.invoke('vacuum.evacuate')
            h.step(400.0)
            r = h.read('evaluator')
            self.assertEqual(r['vacuum.state'], 'lv_ready', p_set)
            self.assertAlmostEqual(r['vacuum.lv_pressure_indicated_pa'] / p_set, 1.0, delta=0.06)
            self.assertLess(r['truth.gun_pressure_pa'], rt.HV_READY_PA)
            self.assertTrue(r['panel.lv_lamp'])
            rejected(h, 'detector.select', 'DETECTOR_NOT_AVAILABLE', detector='SED')

    def test_wet_specimen_needs_low_vacuum(self):
        wet = spec('leaf', 'tissue_soft', wet=True, topo='rough_fracture', height=0.002)
        h = ready(specimen=wet)
        h.step(rt.EVAC_LIMIT_S)
        self.assertEqual(h.error, 'EVAC_TIMEOUT')
        h.invoke('error.acknowledge')
        h.invoke('vacuum.toggle_lv')
        h.step(200.0)
        self.assertEqual(h.vac_state, 'lv_ready')

    def test_power_cycle_and_mains(self):
        h = configured(mains_voltage_v=85.0)
        rejected(h, 'power.set', 'MAINS_OUT_OF_RANGE', enabled=True)
        h = ready()
        h.invoke('power.key_toggle')
        self.assertFalse(h.power)
        self.assertEqual(h.vac_state, 'idle')
        self.assertTrue(any(e['code'] == 'UNSAFE_POWER_OFF' for e in h.events))
        p = h.p_ch
        h.step(3600.0)
        self.assertGreater(h.p_ch, p)                            # slowly leaking up without pumps
        h.invoke('power.key_toggle')
        self.assertEqual(h.vac_state, 'roughing')                # start-up pumping of a chamber left under vacuum
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=120.0))
        h.step(2.0)
        self.assertFalse(h.power)
        self.assertEqual(h.error, 'MAINS_FAULT')

    def test_shutdown_then_key_off_is_clean(self):
        h = ready()
        h.invoke('system.shutdown')
        rejected(h, 'vacuum.vent', 'NOT_READY')
        n = len(h.events)
        h.invoke('power.set', enabled=False)
        self.assertFalse(any(e['code'] == 'UNSAFE_POWER_OFF' for e in h.events[n:]))


class StageDoorSpecimen(unittest.TestCase):
    def test_draw_out_needs_vent_and_draw_in_needs_clearance(self):
        h = ready()
        rejected(h, 'chamber.draw_out', 'CHAMBER_UNDER_VACUUM')
        h.invoke('vacuum.vent')
        h.step(150.0)
        h.invoke('chamber.draw_out')
        h.step(1.0)
        self.assertEqual(h.read()['chamber.door_state'], 'moving')
        h.step(2.0)
        self.assertEqual(h.read('evaluator')['chamber.door_position_m'], rt.DOOR_TRAVEL_M)
        h.invoke('stage.set_z', z_lift_m=0.0)
        h.step(30.0)
        h.invoke('specimen.unload')
        h.invoke('specimen.provide', **spec('tall', 'aluminium', height=0.046))
        h.invoke('specimen.load', specimen_id='tall')
        rejected(h, 'chamber.draw_in', 'STAGE_COLLISION_RISK')                  # 46 mm high: top 2 mm under the pole piece even at Z 0
        h.invoke('stage.set_z', z_lift_m=0.01)                                  # drawn out: no pole piece above the stage
        h.step(6.0)
        rejected(h, 'chamber.draw_in', 'STAGE_COLLISION_RISK')

    def test_specimen_limits_and_bookkeeping(self):
        h = configured()
        rejected(h, 'specimen.provide', 'SPECIMEN_TOO_LARGE', **spec('big', 'iron', diameter=0.16))
        rejected(h, 'specimen.provide', 'SPECIMEN_TOO_LARGE', **spec('high', 'iron', height=0.05))
        rejected(h, 'specimen.provide', 'UNKNOWN_SUBSTANCE', **spec('x', 'unobtainium'))
        bad = spec('frac', 'iron')
        bad['phases'][0]['area_fraction'] = 0.8
        rejected(h, 'specimen.provide', 'AREA_FRACTIONS_INVALID', **bad)
        rejected(h, 'specimen.provide', 'COATING_INVALID', **spec('c', 'iron', coating='gold', thick=0.0))
        h.invoke('specimen.provide', **FE_SI)
        rejected(h, 'specimen.load', 'DOOR_NOT_OPEN', specimen_id=FE_SI['specimen_id'])

    def test_stage_moves_at_its_speeds_and_tilt_limit(self):
        h = ready()
        h.invoke('stage.move_xy', x_m=0.03, y_m=0.0)
        h.step(5.0)
        self.assertAlmostEqual(h.stage['x'], rt.STAGE_XY_SPEED_M_S * 5.0, places=9)
        self.assertTrue(h.read()['stage.moving'])
        h.step(10.0)
        self.assertAlmostEqual(h.stage['x'], 0.03, places=12)
        h.invoke('stage.move_xy', x_m=0.0, y_m=0.0)
        h.step(12.0)
        rejected(h, 'stage.set_tilt', 'STAGE_COLLISION_RISK', tilt_rad=math.radians(80.0))   # WD 10 mm, 12 mm specimen
        h.invoke('stage.set_z', z_lift_m=0.0)
        h.step(25.0)
        h.invoke('stage.set_tilt', tilt_rad=math.radians(30.0))
        h.step(5.0)
        self.assertAlmostEqual(h.stage['tilt'], math.radians(30.0), places=12)
        # tilt about the holder top: the beam meets the 5 mm high top face h tan t from its centre, WD = 48 mm - z - h / cos t
        u, v, on = h._beam_point()
        self.assertTrue(on)
        self.assertAlmostEqual(math.hypot(u, v), 0.005 * math.tan(math.radians(30.0)), places=12)
        self.assertAlmostEqual(h._wd_true(), 0.048 - 0.0 - 0.005 / math.cos(math.radians(30.0)), places=12)
        h.invoke('stage.set_tilt', tilt_rad=math.radians(60.0))
        h.step(5.0)
        # at 60 deg the hit point (8.7 mm from the centre) is beyond the 6 mm radius: the beam lands on the holder plane
        self.assertFalse(h._beam_point()[2])
        self.assertAlmostEqual(h._wd_true(), 0.048, places=12)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('stage.move_xy', x_m=0.05, y_m=0.0)


class OpticsAndImaging(unittest.TestCase):
    def test_probe_model_reproduces_the_catalogue_resolutions(self):
        fit = json.loads((OUT / 'optics_fit.json').read_text())
        self.assertAlmostEqual(fit['fitted']['c_s'], rt.C_S_COEF, places=12)
        self.assertAlmostEqual(fit['fitted']['c_c'], rt.C_C_COEF, places=12)
        for v, d, wd in ((30000.0, 3.0e-9, 0.008), (3000.0, 8.0e-9, 0.006), (1000.0, 15.0e-9, 0.006)):
            dp, a = rt.probe(v, 3e-12, wd)
            self.assertAlmostEqual(rt.K_RES * dp / d, 1.0, delta=0.05)
        # more current, bigger probe; longer WD, bigger probe
        self.assertGreater(rt.probe(15000.0, 1e-9, 0.01)[0], rt.probe(15000.0, 1e-11, 0.01)[0])
        self.assertGreater(rt.probe(15000.0, 1e-10, 0.04)[0], rt.probe(15000.0, 1e-10, 0.008)[0])

    def test_voltage_steps_and_ranges(self):
        h = ready(evacuate=False)
        self.assertEqual(h.invoke('beam.set_voltage', voltage_v=2345.0)['voltage_v'], 2300.0)
        self.assertEqual(h.invoke('beam.set_voltage', voltage_v=12345.0)['voltage_v'], 12000.0)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('beam.set_voltage', voltage_v=400.0)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('beam.set_probe_current', current_a=5e-7)
        self.assertAlmostEqual(h.invoke('scan.set_magnification', magnification=1000.0)['field_width_m'], 128e-6, places=12)
        rejected(h, 'beam.observation_on', 'VACUUM_NOT_READY')

    def test_bse_composition_contrast_follows_atomic_number(self):
        mix = dict(FE_SI, specimen_id='au_c', phases=[dict(substance_id='gold', area_fraction=0.5, grain_size_m=2e-5),
                                                      dict(substance_id='carbon_graphite', area_fraction=0.5, grain_size_m=2e-5)])
        h = observing(ready(specimen=mix))
        img = acquire_image(h)
        self.assertEqual(img['status'], 'completed')
        self.assertEqual([r['region_id'] for r in img['regions']], ['region_1', 'region_2'])   # grey-level regions, no phase names
        r1, r2 = img['regions']
        self.assertGreater(r1['mean_grey'], r2['mean_grey'] + 100)
        self.assertNotIn('signal_per_electron', r1)
        fr = h._frame_signals(dwell=1e-5, nx=320, ny=240, noise=False)            # evaluator-side truth behind the regions
        y = {r['substance_id']: r for r in fr['regions']}
        self.assertGreater(y['gold']['y'], 3 * y['carbon_graphite']['y'])
        self.assertAlmostEqual(r1['area_fraction_in_view'], y['gold']['fraction'], places=12)   # the bright region is the gold phase
        self.assertEqual(img['template_source'], dict(library=rt.LIBRARY_REL, substances=[]))
        self.assertEqual(len(img['thumbnail']), rt.THUMB_H)
        self.assertTrue(all(len(row) == rt.THUMB_W for row in img['thumbnail']))
        self.assertAlmostEqual(img['field_of_view_m'][0], 128e-6, places=12)
        self.assertEqual(img['template_source']['library'], rt.LIBRARY_REL)

    def test_noise_falls_with_dwell_and_current(self):
        h = observing(ready())
        a = {r['region_id']: r['snr'] for r in acquire_image(h, dwell=1e-6)['regions']}
        b = {r['region_id']: r['snr'] for r in acquire_image(h, dwell=1e-4)['regions']}
        self.assertEqual(set(a), set(b))
        for k in a:
            self.assertGreater(b[k], 5 * a[k])

    def test_defocus_blurs_and_autofocus_recovers(self):
        h = observing(ready(), mag=20000.0)
        r0 = acquire_image(h, pixels='1280x960')['effective_resolution_m']
        h.invoke('optics.set_focus', focus_wd_m=0.013)
        r1 = acquire_image(h, pixels='1280x960')['effective_resolution_m']
        self.assertGreater(r1, 3 * r0)
        h.invoke('optics.autofocus')
        self.assertAlmostEqual(h.focus_wd, h._wd_true(), delta=2e-5)

    def test_charging_of_uncoated_insulators_in_high_vacuum_only(self):
        q = spec('quartz', 'quartz', topo='particles', height=0.003, grain=5e-5)
        h = observing(ready(specimen=q), det='SED', ip=1e-9)
        img = acquire_image(h)
        self.assertTrue(img['charging_artifacts'])
        sp = acquire_eds(h)
        u, v, on = h._beam_point()
        sub, th, az, k = h._phase_sub(h._spec(), u, v)
        land = h._landing_ev(sub, h._spec(), k)                                  # truth: E0 minus the potential of that phase
        self.assertLess(land, 0.5 * 15000.0)
        self.assertLess(abs(sp['duane_hunt_limit_ev'] - land), 500.0)           # the agent sees the charging only through the Duane-Hunt limit
        self.assertEqual(max(i for i, c in enumerate(sp['counts']) if c > 0) * rt.EDS_CHANNEL_EV <= sp['duane_hunt_limit_ev'] + 300.0, True)
        # the pre-charged surface (about 11 kV) mirrors a 1 kV beam; a fresh specimen at 1 kV (below E2) does not charge
        h.invoke('beam.set_voltage', voltage_v=1000.0)
        h.step(2.0)
        h.invoke('optics.auto_contrast_brightness')
        self.assertTrue(acquire_image(h)['charging_artifacts'])
        h3 = observing(ready(specimen=q), kv=1000.0, det='SED')
        self.assertFalse(acquire_image(h3)['charging_artifacts'])
        self.assertEqual(h3.v_surface.get(0, 0.0), 0.0)
        # carbon coating or low vacuum: no charging
        for kw in (dict(specimen=spec('qc', 'quartz', coating='carbon', thick=2e-8, height=0.003)), dict(specimen=q, lv=True)):
            h2 = observing(ready(**kw), det='BED_COMPO')
            self.assertFalse(acquire_image(h2)['charging_artifacts'])
            self.assertLess(abs(acquire_eds(h2, live=60.0)['duane_hunt_limit_ev'] - 15000.0), 600.0)
            self.assertEqual(h2.v_surface, {})

    def test_sed_rejected_in_lv_and_image_needs_beam(self):
        h = ready(lv=True)
        rejected(h, 'image.acquire', 'BEAM_NOT_READY', pixels='320x240', dwell_s=1e-5)
        h.invoke('beam.observation_on')
        h.step(rt.SATURATION_S + 1)
        self.assertEqual(h.detector, 'BED_COMPO')
        rejected(h, 'detector.select', 'DETECTOR_NOT_AVAILABLE', detector='SED')


class EdsPhysics(unittest.TestCase):
    def setUp(self):
        self.psi, self.omega = rt.eds_geometry(0.010, 0.0)

    def test_resolution_meets_the_catalogue(self):
        self.assertLessEqual(rt.fwhm_ev(5898.75, rt.EDS_PROCESS_TIMES['T4']['noise_ev']), rt.EDS_FWHM_MN_SPEC_EV)
        self.assertAlmostEqual(rt.fwhm_ev(5898.75, rt.EDS_PROCESS_TIMES['T4']['noise_ev']), 127.0, delta=0.5)
        self.assertGreater(rt.fwhm_ev(5898.75, rt.EDS_PROCESS_TIMES['T1']['noise_ev']), 140.0)

    def test_geometry_matches_the_model_detector(self):
        psi, om = rt.eds_geometry(0.010, 0.0)
        self.assertAlmostEqual(math.degrees(psi), 35.0, places=9)
        self.assertAlmostEqual(om, 25e-6 / 0.040 ** 2, places=12)
        self.assertLess(rt.eds_geometry(0.030, 0.0)[1], om)
        self.assertLess(rt.eds_geometry(0.010, math.radians(40))[0], psi)

    def test_line_positions_and_duane_hunt(self):
        rates, gen, em = rt.spectrum_rates(rt.substance('iron')['composition_mass_fraction'], 15000.0, self.psi, self.omega, 60.0)
        k = int(np.argmax(rates[300:]) + 300)
        self.assertAlmostEqual((k + 0.5) * rt.EDS_CHANNEL_EV, 6403.84, delta=15.0)      # Fe Ka1 (Elam)
        self.assertEqual(float(rates[int(15000 / rt.EDS_CHANNEL_EV) + 2:].sum()), 0.0)   # nothing above E0
        self.assertGreater(gen, em)                                                      # absorption

    def test_detector_efficiency_shape(self):
        eff = rt.detector_efficiency
        self.assertAlmostEqual(eff(5000.0), rt.WINDOW['grid_open'], delta=0.02)          # the Si support grid bounds the efficiency
        self.assertLess(eff(277.0), eff(1500.0))
        self.assertLess(eff(30000.0), eff(10000.0))                                     # 0.45 mm Si becomes transparent
        self.assertGreater(eff(240.0), eff(300.0))                                      # window C K edge (284 eV)

    def test_count_rate_scales_with_current_and_dead_time(self):
        h = observing(ready(), ip=1e-10)
        a = acquire_eds(h, mode='area', live=10.0)
        h.invoke('beam.set_probe_current', current_a=1e-9)
        b = acquire_eds(h, mode='area', live=10.0)
        self.assertAlmostEqual(b['input_count_rate_cps'] / a['input_count_rate_cps'], 10.0, delta=0.01)
        self.assertGreater(b['dead_time_fraction'], a['dead_time_fraction'])
        self.assertAlmostEqual(b['real_time_s'], 10.0 / (1 - b['dead_time_fraction']), places=6)
        self.assertAlmostEqual(sum(b['counts']) / (b['input_count_rate_cps'] * 10.0), 1.0, delta=0.01)

    def test_standardless_quantification_of_pure_and_binary_compounds(self):
        rng = np.random.default_rng(3)
        noise = rt.EDS_PROCESS_TIMES['T3']['noise_ev']
        for sid, tol in (('iron', 1e-9), ('gallium_arsenide', 0.05), ('galena', 0.05), ('cadmium_telluride', 0.05), ('halite', 0.12)):
            comp = rt.substance(sid)['composition_mass_fraction']
            rates, _, _ = rt.spectrum_rates(comp, 20000.0, self.psi, self.omega, noise)
            y = rng.poisson(rates * 1e-9 / rt.E_CHARGE * 60.0)
            res = {r['element']: r for r in rt.quantify(y, 20000.0, self.psi, noise, list(comp))}
            self.assertAlmostEqual(sum(r['mass_fraction'] for r in res.values()), 1.0, places=9)
            for e, w in comp.items():
                self.assertAlmostEqual(res[e]['mass_fraction'] / w, 1.0, delta=tol, msg=(sid, e))
            self.assertEqual(sorted(rt.identify_elements(y, 20000.0, noise)), sorted(comp), sid)

    def test_oxygen_is_underestimated_as_in_standardless_practice(self):
        rng = np.random.default_rng(4)
        noise = rt.EDS_PROCESS_TIMES['T3']['noise_ev']
        comp = rt.substance('quartz')['composition_mass_fraction']
        rates, _, _ = rt.spectrum_rates(comp, 15000.0, self.psi, self.omega, noise)
        res = {r['element']: r['mass_fraction'] for r in rt.quantify(rng.poisson(rates * 1e-9 / rt.E_CHARGE * 60.0), 15000.0, self.psi, noise, ['Si', 'O'])}
        self.assertLess(res['O'], comp['O'])
        self.assertGreater(res['O'], 0.8 * comp['O'])

    def test_eds_through_the_runtime(self):
        h = observing(ready(specimen=spec('ga', 'gallium_arsenide')), ip=1e-9)
        sp = acquire_eds(h, mode='area', live=30.0)
        self.assertEqual(sp['status'], 'completed')
        self.assertEqual(len(sp['counts']), rt.EDS_CHANNELS)
        q = h.invoke('eds.quantify', spectrum_id=sp['spectrum_id'], elements=[])
        self.assertEqual(sorted(q['elements_identified']), ['As', 'Ga'])
        w = {r['element']: r['mass_fraction'] for r in q['results']}
        self.assertAlmostEqual(w['Ga'], 0.482, delta=0.03)
        rejected(h, 'eds.quantify', 'UNKNOWN_ELEMENT', spectrum_id=sp['spectrum_id'], elements=['Xx'])
        rejected(h, 'eds.quantify', 'UNKNOWN_RECORD', spectrum_id='eds-99', elements=[])


class GunAndFilament(unittest.TestCase):
    def test_saturation_time_and_emission(self):
        h = ready()
        h.invoke('beam.observation_on')
        h.step(5.0)
        self.assertEqual(h.read()['beam.filament_state'], 'heating')
        rejected(h, 'image.acquire', 'BEAM_NOT_READY', pixels='320x240', dwell_s=1e-5)
        h.step(6.0)
        r = h.read()
        self.assertEqual(r['beam.filament_state'], 'saturated')
        self.assertAlmostEqual(r['beam.emission_indicated_a'], rt.EMISSION_SAT_A, delta=0.05 * rt.EMISSION_SAT_A)

    def test_burnout_and_replacement(self):
        h = observing(ready())
        h.fil_used_s = h.fil_life_s - 1.0
        h.step(3.0)
        self.assertTrue(h.fil_burned)
        self.assertEqual(h.error, 'FILAMENT_BURNOUT')
        self.assertEqual(h.read()['beam.filament_state'], 'burned_out')
        rejected(h, 'gun.replace_filament', 'VENT_REQUIRED')
        h.invoke('error.acknowledge')
        h.invoke('vacuum.vent')
        h.step(150.0)
        n = h.invoke('gun.replace_filament')['filament_serial']
        self.assertEqual(n, 2)
        self.assertFalse(h.fil_burned)
        self.assertEqual(h.fil_used_s, 0.0)


class LedgersIsolationSessions(unittest.TestCase):
    def test_charge_and_gas_ledgers_close(self):
        q = spec('quartz', 'quartz', topo='particles', height=0.003)
        h = observing(ready(specimen=q, vent_gas='room_air'), det='SED')
        acquire_image(h)
        acquire_eds(h)
        h.invoke('vacuum.toggle_lv')                             # the mode change switches the beam off
        h.step(120.0)
        h.invoke('beam.observation_on')
        h.step(rt.SATURATION_S + 1.0)
        acquire_image(h)
        led = h.ledger()
        self.assertLess(abs(led['charge_residual_c']), 1e-15 + 1e-9 * led['beam_charge_c'])
        self.assertLess(abs(led['gas_residual_mol']), 1e-12 + 1e-9 * led['gas_pumped_mol'])
        self.assertGreater(led['gas_pumped_mol'], 0.3)
        self.assertGreater(led['xray_generated'], led['xray_detected'] > 0)
        self.assertAlmostEqual(led['electrons_on_specimen'] * rt.E_CHARGE, led['beam_charge_c'], delta=1e-9 * led['beam_charge_c'])

    def test_two_instances_are_isolated(self):
        a, b = ready(seed=1), ready(seed=1)
        observing(a)
        acquire_image(a)
        self.assertFalse(b.ht)
        self.assertEqual(b.images, {})
        self.assertNotEqual(a.read('evaluator'), b.read('evaluator'))

    def test_agent_does_not_see_truth(self):
        h = ready()
        h.register_principal('a', 'agent')
        h.register_principal('e', 'evaluator')
        a, e = h.session('a'), h.session('e')
        ra = a.read(instance_id=h.instance_id)['observations']
        re_ = e.read(instance_id=h.instance_id)['observations']
        self.assertFalse(any(k.startswith('truth.') for k in ra))
        self.assertTrue(any(k.startswith('truth.') for k in re_))
        with self.assertRaises(rt.ActionRejected):
            a.read(instance_id=h.instance_id, state_ids=['truth.chamber_pressure_pa'])
        d = a.describe(instance_id=h.instance_id)
        self.assertNotIn('ledger.read', d['capabilities'])
        self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']))
        r = a.invoke(instance_id=h.instance_id, capability_id='ledger.read', arguments={}, request_id='x')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')

    def test_records_and_control_belong_to_their_agent(self):
        h = observing(ready())
        for p in ('a1', 'a2'):
            h.register_principal(p, 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        I = h.instance_id
        r = s1.invoke(instance_id=I, capability_id='image.acquire', arguments=dict(pixels='640x480', dwell_s=2e-5), request_id='r1')
        self.assertEqual(r['status'], 'accepted')
        self.assertEqual(s1.invoke(instance_id=I, capability_id='image.acquire', arguments=dict(pixels='640x480', dwell_s=2e-5), request_id='r1'), r)
        self.assertEqual(s1.invoke(instance_id=I, capability_id='image.acquire', arguments=dict(pixels='320x240', dwell_s=2e-5),
                                   request_id='r1')['error']['code'], 'REQUEST_CONFLICT')
        # the other agent cannot steer the beam, the stage or the vacuum while the first agent acquires
        for cap, args in (('beam.set_voltage', dict(voltage_v=5000.0)), ('stage.move_xy', dict(x_m=0.0, y_m=0.0)), ('vacuum.vent', {})):
            self.assertEqual(s2.invoke(instance_id=I, capability_id=cap, arguments=args, request_id='g-' + cap)['error']['code'], 'ACCESS_DENIED')
        o2 = s2.read(instance_id=I)['observations']
        self.assertTrue(o2['acquisition.started_by_other_agent']['value'])
        self.assertIsNone(o2['acquisition.id']['value'])
        h.step(10.0)
        rid = h.operations[r['operation_id']]['record_id']
        self.assertEqual(s2.invoke(instance_id=I, capability_id='image.result', arguments=dict(image_id=rid), request_id='x2')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(s2.invoke(instance_id=I, capability_id='image.result', arguments=dict(image_id='img-99'), request_id='x3')['error']['code'],
                         'UNKNOWN_RECORD')
        self.assertEqual(s1.invoke(instance_id=I, capability_id='image.result', arguments=dict(image_id=rid), request_id='x4')['status'], 'succeeded')
        self.assertEqual(s1.read(instance_id=I)['observations']['result.last_image_id']['value'], rid)
        self.assertIsNone(s2.read(instance_id=I)['observations']['result.last_image_id']['value'])
        self.assertEqual(s2.invoke(instance_id=I, capability_id='records.list', arguments={}, request_id='x5')['result']['images'], [])
        with self.assertRaises(rt.ActionRejected):
            s2.poll(r['operation_id'])

    def test_cancel_keeps_the_dose_and_discards_the_frame(self):
        h = observing(ready())
        h.register_principal('a', 'agent')
        s = h.session('a')
        r = s.invoke(instance_id=h.instance_id, capability_id='image.acquire', arguments=dict(pixels='2560x1920', dwell_s=1e-5), request_id='c1')
        h.step(5.0)
        q0 = h.ledger()['beam_charge_c']
        c = s.cancel(r['operation_id'], 'c2')
        self.assertEqual(c['result']['phase'], 'cancelled')
        rec = h.images[h.operations[r['operation_id']]['record_id']]
        self.assertEqual((rec['status'], rec['error_code']), ('cancelled', 'USER_CANCEL'))
        self.assertGreater(q0, 0.0)
        self.assertEqual(h.ledger()['beam_charge_c'], q0)
        self.assertIsNone(h.acq)
        with self.assertRaises(rt.ActionRejected):
            s.cancel(r['operation_id'], 'c3')

    def test_query_and_bookkeeping_capabilities(self):
        h = observing(ready())
        h.invoke('stage.rotate', rotation_rad=math.pi / 2)
        out = h.invoke('sim.step', dt_s=4.0)
        self.assertAlmostEqual(out['time_s'], h.time_s, places=12)
        self.assertAlmostEqual(h.stage['rot'], min(math.pi / 2, rt.STAGE_ROT_SPEED * 4.0), places=12)
        h.invoke('sim.step', dt_s=4.0)
        self.assertAlmostEqual(h.read()['stage.rotation_indicated_rad'], math.radians(90.0), places=9)
        img = acquire_image(h)
        sp = acquire_eds(h)
        self.assertEqual(h.invoke('image.result', image_id=img['image_id'])['status'], 'completed')
        res = h.invoke('eds.result', spectrum_id=sp['spectrum_id'])
        self.assertEqual((res['status'], res['total_counts']), ('completed', sum(res['counts'])))
        self.assertEqual(h.invoke('records.list'), dict(images=[img['image_id']], spectra=[sp['spectrum_id']]))
        codes = [e['code'] for e in h.invoke('events.read', since_s=0.0)['events']]
        for c in ('POWER_ON', 'SOFTWARE_READY', 'EVAC', 'EVAC_COMPLETE', 'HT_ON', 'IMAGE_START', 'EDS_START', 'ACQ_COMPLETED', 'LOAD'):
            self.assertIn(c, codes)
        prov = h.invoke('provenance.read')
        self.assertEqual((prov['library'], prov['substances']), (rt.LIBRARY_REL, ['iron', 'silicon']))
        led = h.invoke('ledger.read')
        self.assertEqual(set(led), set(rt.LEDGER_KEYS))
        self.assertGreater(led['beam_charge_c'], 0.0)

    def test_beam_off_fails_a_running_acquisition(self):
        h = observing(ready())
        op = h.invoke('eds.acquire', mode='point', live_time_s=60.0, process_time='T3')['operation_id']
        h.step(5.0)
        h.invoke('beam.observation_off')
        rec = h.spectra[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['error_code']), ('failed', 'BEAM_OFF'))



class ReviewRoundOneRegressions(unittest.TestCase):
    """Independent runtime review round 1 (F01-F23): each fixed behaviour is pinned here."""

    def test_power_loss_while_venting_keeps_the_door_locked(self):                                  # F01
        for wait in (0.0, 1.0):
            h = ready()
            h.invoke('vacuum.vent')
            h.step(wait)
            h.invoke('power.set', enabled=False)
            self.assertEqual(h.vac_state, 'idle')
            self.assertLess(h.p_ch, rt.VENT_READY_PA)
            rejected(h, 'chamber.draw_out', 'CHAMBER_UNDER_VACUUM')
            rejected(h, 'gun.replace_filament', 'VENT_REQUIRED')
            h.step(60.0)
            self.assertEqual(h._door_state(), 'closed')
            h.invoke('power.set', enabled=True)                   # start-up pumps the chamber that was left under vacuum
            h.step(rt.SOFTWARE_BOOT_S + 1.0)
            h.invoke('vacuum.vent')
            h.step(150.0)
            self.assertEqual(h.vac_state, 'vented')
            h.invoke('chamber.draw_out')

    def test_records_do_not_reveal_truth(self):                                                     # F02
        mix = dict(FE_SI, specimen_id='qz_pb', phases=[dict(substance_id='quartz', area_fraction=0.6, grain_size_m=2e-5),
                                                       dict(substance_id='galena', area_fraction=0.4, grain_size_m=2e-5)])
        h = observing(ready(specimen=mix))
        h.register_principal('a', 'agent')
        s = h.session('a')
        op = s.invoke(instance_id=h.instance_id, capability_id='image.acquire', arguments=dict(pixels='320x240', dwell_s=1e-5), request_id='i')['operation_id']
        h.step(h.operations[op]['duration'] + 1.0)
        img = s.invoke(instance_id=h.instance_id, capability_id='image.result', arguments=dict(image_id=h.operations[op]['record_id']), request_id='r')['result']
        text = json.dumps(img)
        for name in ('quartz', 'galena', 'substance_id', 'signal_per_electron'):
            self.assertNotIn(name, text)
        self.assertEqual(img['template_source']['substances'], [])
        self.assertEqual(img['working_distance_m'], h.focus_wd)            # the displayed WD, not the true one
        self.assertIsNone(img['probe_diameter_m'])
        op = s.invoke(instance_id=h.instance_id, capability_id='eds.acquire', arguments=dict(mode='point', live_time_s=10.0, process_time='T3'),
                      request_id='e')['operation_id']
        h.step(h.operations[op]['duration'] + 1.0)
        sp = s.invoke(instance_id=h.instance_id, capability_id='eds.result', arguments=dict(spectrum_id=h.operations[op]['record_id']), request_id='s')['result']
        self.assertNotIn('landing_energy_ev', sp)
        self.assertEqual(sp['template_source']['substances'], [])
        self.assertEqual(sp['working_distance_m'], h.focus_wd)
        self.assertAlmostEqual(sp['takeoff_angle_rad'], rt.eds_geometry(h.focus_wd, 0.0)[0], places=12)
        h2 = observing(ready())
        h2.fil_used_s = h2.fil_life_s - 1.0
        h2.step(3.0)
        msg = [e['message_zh'] for e in h2.events if e['code'] == 'FILAMENT_BURNOUT'][0]
        self.assertNotRegex(msg, r'\d')

    def test_collision_check_covers_the_swept_path(self):                                          # F03
        big = spec('big', 'iron', height=0.001, diameter=0.040)
        h = ready(specimen=big, wd=0.005)
        self.assertAlmostEqual(h.stage['z'], 0.042, places=9)
        h.invoke('stage.set_z', z_lift_m=0.0)
        rejected(h, 'stage.set_tilt', 'STAGE_COLLISION_RISK', tilt_rad=math.radians(90.0))    # the edge would sweep through the pole piece
        h.step(25.0)
        h.invoke('stage.set_tilt', tilt_rad=math.radians(90.0))                             # once lowered, the tilt is safe
        top = max(h._top_z_rel(h.stage['z'], math.radians(t)) for t in range(0, 91))
        self.assertLessEqual(top, 0.044 + 1e-12)
        g = ready(wd=0.012)
        g.invoke('stage.set_z', z_lift_m=0.015)
        g.step(25.0)
        g.invoke('stage.set_tilt', tilt_rad=math.radians(60.0))
        g.step(8.0)
        rejected(g, 'stage.move_xy', 'STAGE_COLLISION_RISK', x_m=0.0, y_m=0.02)             # Y rides on the tilted cradle (+17 mm at 60 deg)
        g.invoke('stage.move_xy', x_m=0.0, y_m=-0.02)                                       # moving down-slope is safe

    def test_charging_follows_the_incidence_angle(self):                                           # F05
        charge = {}
        for tilt in (0.0, 60.0):
            q = spec('q', 'quartz', height=0.001, diameter=0.004)
            h = ready(specimen=q, wd=0.010)
            if tilt:
                h.invoke('stage.set_z', z_lift_m=0.0)
                h.step(25.0)
                h.invoke('stage.set_tilt', tilt_rad=math.radians(tilt))
                h.step(8.0)
            observing(h, det='SED')
            h.step(60.0)
            charge[tilt] = h.read('evaluator')['truth.specimen_charge_c']
        e2_0, e2_60 = rt.second_crossover_kev(rt.substance('quartz'), 0.0), rt.second_crossover_kev(rt.substance('quartz'), math.radians(60.0))
        self.assertTrue(3.0 < e2_0 < 4.5, e2_0)                     # F13: about 3-4 keV for quartz (no double-counted SE2)
        self.assertGreater(e2_60, 3.0 * e2_0)                      # tilting raises E2 (the IT300 manual's remedy against charge-up)
        self.assertGreater(charge[0.0], 3.0 * charge[60.0])
        self.assertGreater(charge[60.0], 0.0)

    def test_each_insulating_phase_keeps_its_own_potential(self):                                   # F12
        mix = dict(FE_SI, specimen_id='qz_pm', phases=[dict(substance_id='quartz', area_fraction=0.5, grain_size_m=2e-5),
                                                       dict(substance_id='pmma', area_fraction=0.5, grain_size_m=2e-5)])
        h = observing(ready(specimen=mix), kv=20000.0, det='SED')
        h.step(600.0)
        self.assertEqual(len(h.v_surface), 2)
        for k, ph in enumerate(mix['phases']):
            sub = rt.substance(ph['substance_id'])
            self.assertAlmostEqual(h._landing_ev(sub, h._spec(), k), 20000.0 - h.v_surface[k], places=9)
            v_eq = h._v_equilibrium(sub, 0.0)
            self.assertLessEqual(h.v_surface[k], v_eq + 1e-6)
            self.assertAlmostEqual(h.v_surface[k], v_eq, delta=0.02 * v_eq)
        self.assertNotAlmostEqual(h.v_surface[0], h.v_surface[1], delta=1000.0)      # quartz E2 ~3.8 keV, PMMA ~1.4 keV
        led = h.ledger()
        self.assertLess(abs(led['charge_residual_c']), 1e-15 + 1e-9 * led['beam_charge_c'])

    def test_automatic_identification_on_library_spectra(self):                                   # F06
        psi, om = rt.eds_geometry(0.010, 0.0)
        noise = rt.EDS_PROCESS_TIMES['T3']['noise_ev']
        cases = [(15000.0, 'chromium', ['Cr']), (15000.0, 'nickel', ['Ni']), (15000.0, 'bismuth', ['Bi']), (20000.0, 'rutile', ['O', 'Ti']),
                 (20000.0, 'bone_cortical', ['C', 'Ca', 'O', 'P']), (15000.0, 'galena', ['Pb', 'S']), (15000.0, 'alanine', ['C', 'N', 'O']),
                 (15000.0, 'gold', ['Au'])]
        rng = np.random.default_rng(7)
        for kv, sid, expect in cases:
            comp = rt.substance(sid)['composition_mass_fraction']
            rates, _, _ = rt.spectrum_rates(comp, kv, psi, om, noise)
            y = rng.poisson(rates * 1e-9 / rt.E_CHARGE * 60.0)
            found = sorted(rt.identify_elements(y, kv, noise))
            self.assertEqual([e for e in found if comp.get(e, 0.0) >= 0.02], expect, (sid, found))
            self.assertFalse(set(found) - set(comp), (sid, found))
        self.assertTrue({'Ne', 'Be', 'B'} <= rt.ID_EXCLUDED)

    def test_mode_change_switches_the_beam_off_with_an_event(self):                                 # F07
        h = observing(ready(lv=True))
        self.assertTrue(h.ht)
        h.invoke('vacuum.toggle_lv')
        self.assertFalse(h.ht)
        self.assertIsNone(h.error)
        self.assertEqual([e['code'] for e in h.events][-2:], ['HT_OFF', 'MODE_HV'])
        h.step(240.0)
        self.assertEqual(h.vac_state, 'hv_ready')

    def test_declared_effects_and_errors(self):                                                     # F08, F09, F22
        h = observing(ready())
        h.invoke('system.shutdown')                              # instrumented invoke: the beam truth is a declared effect now
        errs = {k: {e['code'] for e in v['errors']} for k, v in _meta()['capabilities'].items()}
        for k in ('beam.observation_on', 'optics.autofocus', 'optics.auto_stigmate', 'optics.auto_contrast_brightness', 'image.acquire', 'eds.acquire'):
            self.assertNotIn('DOOR_OPEN', errs[k], k)
            self.assertNotIn('DETECTOR_NOT_AVAILABLE', errs[k], k)
        self.assertNotIn('HT_ON', errs['gun.replace_filament'])
        self.assertIn('STAGE_COLLISION_RISK', errs['stage.move_xy'])
        self.assertIn('COUNT_RATE_TOO_HIGH', errs['eds.acquire'])
        g = observing(ready(), ip=3e-7)
        rejected(g, 'eds.acquire', 'COUNT_RATE_TOO_HIGH', mode='point', live_time_s=600.0, process_time='T4')
        w = configured()
        w.invoke('specimen.provide', **spec('wet', 'bone_cortical', wet=True, height=0.003))
        w.invoke('power.set', enabled=True)
        w.step(rt.SOFTWARE_BOOT_S + 1.0)
        w.invoke('chamber.draw_out')
        w.step(3.0)
        w.invoke('specimen.load', specimen_id='wet')
        w.invoke('vacuum.evacuate')                               # pumping with the door out: roughing never reaches crossover
        w.step(2.0)
        w.invoke('specimen.unload')                               # unloading the wet specimen changes the gas load (declared effect)

    def test_queries_and_zero_steps_keep_the_revision(self):                                        # F10
        h = observing(ready())
        rev = h.revision
        h.invoke('records.list')
        h.invoke('events.read', since_s=0.0)
        h.invoke('ledger.read')
        h.invoke('provenance.read')
        h.invoke('sim.step', dt_s=0.0)
        h.step(0.0)
        self.assertEqual(h.revision, rev)
        h.step(1.0)
        self.assertEqual(h.revision, rev + 1)

    def test_redeclared_room_temperature_keeps_the_gas_ledger(self):                                 # F11
        h = configured()
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, room_k=300.15))
        self.assertLess(abs(h.ledger()['gas_residual_mol']), 1e-12)
        self.assertAlmostEqual(h.p_ch, rt.P_ATM * 300.15 / 295.15, places=6)

    def test_bed_factor_and_filament_life(self):                                                     # F14, F20, F23
        d, _ = rt.probe(30000.0, 3e-12, 0.005)
        self.assertAlmostEqual(rt.K_RES * d * rt.BED_RES_FACTOR, 4.0e-9, delta=0.02e-9)
        self.assertEqual(rt.FILAMENT_LIFE_H, 300.0)
        rt.second_crossover_kev(rt.substance('quartz'), 0.3)
        self.assertFalse(any(k.startswith('_') for k in rt.library()['substances']['quartz']))



class ReviewRoundTwoRegressions(unittest.TestCase):
    """Independent runtime review round 2 (F24-F30)."""

    def test_vent_keeps_y_when_the_return_would_lift_the_rim(self):                              # F24
        thin = spec('thin', 'iron', height=0.001, diameter=0.010)
        h = ready(specimen=thin, wd=0.030)
        h.invoke('stage.set_z', z_lift_m=0.0)
        h.step(20.0)
        h.invoke('stage.set_tilt', tilt_rad=math.radians(60.0))
        h.step(8.0)
        h.invoke('stage.move_xy', x_m=0.01, y_m=-0.02)
        h.step(15.0)
        h.invoke('stage.set_z', z_lift_m=0.043)                                 # rim is safe at Y -20 mm, not at Y 0
        h.step(25.0)
        h.invoke('vacuum.vent')
        codes = [e['code'] for e in h.events][-3:]
        self.assertIn('Y_RETURN_SKIPPED', codes)
        h.step(30.0)
        self.assertEqual(h.stage['x'], 0.0)
        self.assertAlmostEqual(h.stage['y'], -0.02, places=12)
        self.assertLessEqual(h._top_z_rel(h.stage['z'], h.stage['tilt'], h.stage['y']), 0.044 + 1e-12)

    def test_lowering_is_always_allowed_from_a_too_high_pose(self):                               # F25
        h = ready(wd=0.010)                                                      # 5 mm specimen at Z 33 mm
        h.invoke('vacuum.vent')
        h.step(150.0)
        h.invoke('chamber.draw_out')
        h.step(3.0)
        h.invoke('specimen.unload')
        h.invoke('specimen.provide', **spec('tall20', 'aluminium', height=0.020))
        h.invoke('specimen.load', specimen_id='tall20')                          # top now 53 mm above the WD-48 plane (door out)
        rejected(h, 'chamber.draw_in', 'STAGE_COLLISION_RISK')
        h.invoke('stage.set_z', z_lift_m=0.010)
        h.step(15.0)
        h.invoke('chamber.draw_in')
        h.step(3.0)
        rejected(h, 'stage.set_z', 'STAGE_COLLISION_RISK', z_lift_m=0.025)       # inside: raising the top to 45 mm is refused
        h.invoke('stage.set_z', z_lift_m=0.005)                                  # lowering stays possible

    def test_lowered_voltage_below_the_surface_potential_mirrors_the_beam(self):                  # F26
        q = spec('q', 'quartz', height=0.001, diameter=0.004)
        h = observing(ready(specimen=q, wd=0.010), kv=15000.0, det='SED')
        h.step(120.0)
        v0 = dict(h.v_surface)
        self.assertGreater(max(v0.values()), 10000.0)
        h.invoke('beam.set_voltage', voltage_v=5000.0)
        e0 = h.ledger()['beam_energy_j']
        h.step(5.0)
        self.assertEqual(h.v_surface, v0)                                        # nothing lands, the potential stays
        self.assertEqual(h.ledger()['beam_energy_j'], e0)
        led = h.ledger()
        self.assertLess(abs(led['charge_residual_c']), 1e-15 + 1e-9 * led['beam_charge_c'])
        u, v, on = h._beam_point()
        sub, th, az, k = h._phase_sub(h._spec(), u, v)
        self.assertEqual(h._landing_ev(sub, h._spec(), k), 0.0)

    def test_regions_merge_indistinguishable_phases_and_resolution_is_measured(self):              # F28
        twin = dict(FE_SI, specimen_id='fe_fe', phases=[dict(substance_id='iron', area_fraction=0.3, grain_size_m=2e-5),
                                                        dict(substance_id='iron', area_fraction=0.7, grain_size_m=2e-5)])
        h = observing(ready(specimen=twin))
        img = acquire_image(h)
        self.assertEqual([r['region_id'] for r in img['regions']], ['region_1'])
        self.assertAlmostEqual(img['regions'][0]['area_fraction_in_view'], 1.0, places=12)
        fr = h._frame_signals(dwell=1e-5, nx=320, ny=240, noise=False)
        self.assertNotEqual(img['effective_resolution_m'], fr['eff_res'])
        self.assertEqual(img['effective_resolution_m'], float(f"{img['effective_resolution_m']:.2g}"))

    def test_duane_hunt_limit_validity(self):                                                    # F29
        psi, om = rt.eds_geometry(0.010, 0.0)
        rates, _, _ = rt.spectrum_rates(rt.substance('iron')['composition_mass_fraction'], 15000.0, psi, om, 60.0)
        rng = np.random.default_rng(3)
        y = rng.poisson(rates * 1e-9 / rt.E_CHARGE * 30.0)
        dh = rt.duane_hunt_limit(y, 60.0, 15000.0)
        self.assertLessEqual(dh, 15000.0)
        self.assertLess(abs(dh - 15000.0), 200.0)
        self.assertIsNone(rt.duane_hunt_limit(rng.poisson(rates * 1e-12 / rt.E_CHARGE * 5.0), 60.0, 15000.0))      # too few counts
        r30, _, _ = rt.spectrum_rates(rt.substance('iron')['composition_mass_fraction'], 30000.0, psi, om, 60.0)
        self.assertIsNone(rt.duane_hunt_limit(rng.poisson(r30 * 1e-9 / rt.E_CHARGE * 30.0), 60.0, 30000.0))       # tail beyond 20.48 keV


    def test_door_commanded_in_counts_as_inside(self):                                             # F31
        h = ready(wd=0.010)
        h.invoke('vacuum.vent')
        h.step(150.0)
        h.invoke('chamber.draw_out')
        h.step(3.0)
        h.invoke('stage.set_z', z_lift_m=0.030)                                  # drawn out: free
        h.step(20.0)
        h.invoke('stage.set_z', z_lift_m=0.020)
        h.step(10.0)
        h.invoke('chamber.draw_in')                                              # accepted at the safe pose, the door has not moved yet
        rejected(h, 'stage.set_z', 'STAGE_COLLISION_RISK', z_lift_m=0.043)       # judged as inside now
        rejected(h, 'specimen.unload', 'DOOR_NOT_OPEN')
        h.invoke('specimen.provide', **spec('tall30', 'aluminium', height=0.030))
        rejected(h, 'specimen.load', 'DOOR_NOT_OPEN', specimen_id='tall30')
        meta = _meta()['capabilities']['chamber.draw_in']['errors']
        self.assertIn('严格', next(e['meaning_zh'] for e in meta if e['code'] == 'STAGE_COLLISION_RISK'))


    def test_separable_phases_stay_separate_regions(self):                                         # F33
        ti_zn = dict(FE_SI, specimen_id='ti_zn', phases=[dict(substance_id='titanium', area_fraction=0.5, grain_size_m=2e-5),
                                                         dict(substance_id='zinc', area_fraction=0.5, grain_size_m=2e-5)])
        h = observing(ready(specimen=ti_zn), ip=1e-10)
        img = acquire_image(h, dwell=1e-6)
        self.assertEqual(len(img['regions']), 2, img['regions'])
        five = dict(FE_SI, specimen_id='five', phases=[dict(substance_id=s_, area_fraction=0.2, grain_size_m=2e-5)
                                                       for s_ in ('carbon_graphite', 'aluminium', 'titanium', 'copper', 'gold')])
        g = observing(ready(specimen=five), ip=1e-10)
        self.assertEqual(len(acquire_image(g, dwell=1e-5)['regions']), 5)


    def test_lv_skirt_compresses_the_region_greys_like_the_thumbnail(self):                         # F34
        ti_zn = dict(FE_SI, specimen_id='ti_zn_lv', phases=[dict(substance_id='titanium', area_fraction=0.5, grain_size_m=2e-5),
                                                            dict(substance_id='zinc', area_fraction=0.5, grain_size_m=2e-5)])
        h = ready(specimen=ti_zn, lv=True)
        h.invoke('vacuum.set_lv_pressure', pressure_pa=100.0)
        h.step(60.0)
        observing(h, kv=20000.0)
        img = acquire_image(h, dwell=1e-5)
        greys = sorted(r['mean_grey'] for r in img['regions'])
        self.assertEqual(len(greys), 2)
        self.assertTrue(10.0 < greys[0] < greys[1] < 245.0, greys)                              # not clipped: the ACB was set on the skirted frame
        thumb = np.asarray(img['thumbnail'], float)
        cut = 0.5 * (greys[0] + greys[1])
        self.assertAlmostEqual(float(thumb[thumb > cut].mean()), greys[1], delta=25.0)
        self.assertAlmostEqual(float(thumb[thumb <= cut].mean()), greys[0], delta=25.0)


    def test_texture_and_topo_noise(self):                                                          # F35
        def two(sid, a, b, topo):
            return dict(FE_SI, specimen_id=sid, topography=topo, phases=[dict(substance_id=a, area_fraction=0.5, grain_size_m=3e-5),
                                                                         dict(substance_id=b, area_fraction=0.5, grain_size_m=3e-5)])
        for det, ip, dwell in (('BED_TOPO', 1e-12, 1e-6), ('SED', 1e-9, 1e-5), ('BED_COMPO', 1e-8, 1e-4)):
            h = observing(ready(specimen=two('ti_ti_rough', 'titanium', 'titanium', 'rough_fracture')), kv=20000.0, ip=ip, det=det, mag=500.0)
            img = acquire_image(h, pixels='640x480', dwell=dwell)
            self.assertEqual(len(img['regions']), 1, (det, img['regions']))                        # texture of the grains, not two phases
            self.assertAlmostEqual(img['regions'][0]['area_fraction_in_view'], 1.0, places=12)
            if det == 'BED_TOPO':
                self.assertIsNone(img['contrast'])                                                  # signed difference signal
        h = observing(ready(specimen=two('ti_zn_flat', 'titanium', 'zinc', 'flat_polished')), kv=20000.0, ip=1e-12, det='BED_TOPO', mag=500.0)
        self.assertLess(h.acb['gain'], 1e-6)                                                         # stretched to the noise, not to 1e32
        img = acquire_image(h, pixels='640x480', dwell=1e-6)
        self.assertGreater(float(np.std(img['thumbnail'])), 5.0)                                     # the sum signal's shot noise remains
        g = observing(ready(specimen=two('fe_si_rough', 'iron', 'silicon', 'rough_fracture')), kv=20000.0, ip=1e-9, det='BED_COMPO', mag=500.0)
        self.assertEqual(len(acquire_image(g, pixels='640x480', dwell=1e-5)['regions']), 2)          # composition contrast still separates



class _QuietRng:
    """Noise-free stand-in for the runtime's random.Random (gauges exact, fixed draws) so that two runs differ only by the process."""
    def gauss(self, mu, sigma):
        return mu

    def random(self):
        return 0.5

    def uniform(self, a, b):
        return 0.5 * (a + b)

    def getrandbits(self, k):
        return 12345


class ContractReviewRegressions(unittest.TestCase):
    def test_on_step_effects_cover_what_the_process_changes(self):                              # C20
        """For each timed capability: the slots whose trajectories differ from an otherwise identical run without the call are declared
        (immediate or on_step); the step itself only changes slots declared as on_step effects of sim.step."""
        def lv_ready():
            h = ready(lv=True)
            return h

        def drawn_out():
            h = ready(evacuate=False)
            h.invoke('chamber.draw_out')
            h.step(10.0)
            return h

        def moved():
            h = ready()
            h.invoke('stage.move_xy', x_m=0.01, y_m=0.005)
            h.step(30.0)
            return h
        cases = [('power.set', lambda: configured(), dict(enabled=True), 300.0), ('power.key_toggle', lambda: configured(), {}, 300.0),
                 ('vacuum.evacuate', lambda: ready(evacuate=False), {}, 300.0), ('vacuum.vent', moved, {}, 200.0),
                 ('vacuum.toggle_lv', ready, {}, 300.0), ('vacuum.set_lv_pressure', lv_ready, dict(pressure_pa=80.0), 120.0),
                 ('chamber.draw_out', lambda: ready(evacuate=False), {}, 10.0), ('chamber.draw_in', drawn_out, {}, 10.0),
                 ('stage.move_xy', ready, dict(x_m=0.01, y_m=-0.005), 60.0), ('stage.set_z', ready, dict(z_lift_m=0.030), 60.0),
                 ('stage.set_tilt', ready, dict(tilt_rad=0.3), 60.0), ('stage.rotate', ready, dict(rotation_rad=1.0), 60.0),
                 ('beam.observation_on', ready, {}, 120.0), ('image.acquire', lambda: observing(ready()), dict(pixels='640x480', dwell_s=2e-5), 12.0),
                 ('eds.acquire', lambda: observing(ready()), dict(mode='point', live_time_s=10.0, process_time='T3'), 40.0)]
        # the same calls while the beam is on (conductive specimen, charging uncoated quartz, low vacuum): consequences on the beam process count too
        def quartz_on():
            return observing(ready(specimen=spec('quartz', 'quartz', topo='particles', height=0.003)), kv=15000.0)

        def lv_on():
            return observing(ready(lv=True))
        for make in (lambda: observing(ready()), quartz_on):
            cases += [('power.set', make, dict(enabled=False), 30.0), ('power.key_toggle', make, {}, 30.0), ('vacuum.vent', make, {}, 60.0),
                      ('vacuum.toggle_lv', make, {}, 60.0), ('stage.move_xy', make, dict(x_m=0.002, y_m=0.001), 30.0),
                      ('stage.set_z', make, dict(z_lift_m=0.030), 30.0), ('stage.set_tilt', make, dict(tilt_rad=0.35), 30.0),
                      ('stage.rotate', make, dict(rotation_rad=1.0), 30.0), ('image.acquire', make, dict(pixels='640x480', dwell_s=2e-5), 12.0),
                      ('eds.acquire', make, dict(mode='point', live_time_s=10.0, process_time='T3'), 40.0),
                      ('eds.acquire', make, dict(mode='area', live_time_s=10.0, process_time='T3'), 40.0)]
        def moving_on():
            h = quartz_on()
            h.invoke('stage.move_xy', x_m=0.004, y_m=0.002)
            h.step(0.5)
            return h
        def lv_imaging():
            h = observing(ready(lv=True))
            h.invoke('image.acquire', pixels='2560x1920', dwell_s=2e-5)
            h.step(2.0)
            return h
        cases += [('vacuum.set_lv_pressure', lv_on, dict(pressure_pa=80.0), 60.0), ('vacuum.toggle_lv', lv_on, {}, 120.0), ('vacuum.vent', lv_on, {}, 60.0),
                  ('vacuum.set_lv_pressure', lv_imaging, dict(pressure_pa=90.0), 30.0),                          # the frame fails with VACUUM_LOST
                  ('power.set', moving_on, dict(enabled=False), 30.0), ('power.key_toggle', moving_on, {}, 30.0)]    # the X/Y motors stop without power
        # calls without a process of their own whose consequences appear on the step (beam off, landing energy/current/field on a charging insulator,
        # a mains trip or new room conditions)
        for make in (lambda: observing(ready()), quartz_on):
            cases += [('beam.observation_off', make, {}, 30.0), ('system.shutdown', make, {}, 30.0), ('beam.set_voltage', make, dict(voltage_v=5000.0), 30.0),
                      ('beam.set_probe_current', make, dict(current_a=1e-9), 30.0), ('scan.set_magnification', make, dict(magnification=5000.0), 30.0),
                      ('plant.configure', make, dict(rt.PLANT_EXAMPLE, mains_voltage_v=120.0), 30.0)]
        def roughing_door_out(load_wet):
            def make():
                h = ready(specimen=None, evacuate=False)
                h.invoke('specimen.provide', **spec('wet_leaf', 'tissue_soft', wet=True, height=0.002))
                h.invoke('chamber.draw_out')
                h.step(10.0)
                if load_wet:
                    h.invoke('specimen.load', specimen_id='wet_leaf')
                h.invoke('vacuum.evacuate')
                h.step(5.0)
                return h
            return make
        cases += [('specimen.load', roughing_door_out(False), dict(specimen_id='wet_leaf'), 60.0), ('specimen.unload', roughing_door_out(True), {}, 60.0),
                  ('chamber.draw_in', roughing_door_out(True), {}, 300.0), ('chamber.draw_in', roughing_door_out(False), {}, 300.0)]
        cases += [('plant.configure', lambda: ready(evacuate=False), dict(rt.PLANT_EXAMPLE, room_k=303.15), 60.0),
                  ('plant.configure', moving_on, dict(rt.PLANT_EXAMPLE, mains_voltage_v=80.0), 30.0)]
        declared_all = {k: set(rt.EFFECTS[k]) | set(rt.ON_STEP.get(k, [])) for k in rt.EFFECTS}
        missing = {}
        for cap, make, args, horizon in cases:
            runs = []
            for call in (True, False):
                h = make()
                h.rng = _QuietRng()
                if call:
                    h.invoke(cap, **args)
                traj = []
                for dt in (1.0, 5.0) + (horizon / 8.0,) * 8:
                    h.step(dt)
                    traj.append(h.read('evaluator'))
                runs.append(traj)
            changed = {k for a, b in zip(*runs) for k in a if a[k] != b[k]}
            for k in changed - declared_all[cap] - {'clock.time_s'}:
                missing.setdefault(cap, set()).add(k)
            step_changed = {k for a, b in zip(runs[0], runs[0][1:]) for k in a if a[k] != b[k]}
            for k in step_changed - set(rt.ON_STEP['sim.step']):
                missing.setdefault('sim.step', set()).add(k)
        self.assertEqual(missing, {})
        self.assertFalse([s for v in rt.ON_STEP.values() for s in v if rt.SLOTS[s]['origin'] == 'setpoint'])

    def test_gun_vacuum_loss_trips_the_high_voltage(self):                                      # C05
        h = observing(ready())
        op = h.invoke('image.acquire', pixels='1280x960', dwell_s=1e-4)['operation_id']
        h.step(2.0)
        h.p_ch = 50.0                                   # fault injection: a sudden chamber leak (no public command reaches the gun trip)
        h.step(1.0)
        v = h.read('evaluator')
        self.assertFalse(v['beam.ht_on'])
        self.assertEqual(v['system.error_code'], 'HT_TRIP')
        self.assertGreater(v['truth.gun_pressure_pa'], rt.GUN_TRIP_PA)
        self.assertIn('HT_TRIP', [e['code'] for e in h.events])
        self.assertEqual(h.images[h.operations[op]['record_id']]['status'], 'failed')
        rejected(h, 'beam.observation_on', 'ERROR_PENDING')

    def test_units_and_sampling(self):                                                          # C07, C08, C11
        meta = _meta()
        si = {'1', 's', 'm', 'rad', 'Pa', 'V', 'A', 'C', 'J', 'mol', 'kg', 'K', 'W', 'F', 'm^2', 'sr', '1/s', 'Pa*m^3/s', 'm^3', 'm^3/s', 'm/s',
              'rad/s', 'Hz', 'A/(m^2 sr V)'}
        units = {v['unit'] for v in meta['state_slots'].values()}
        for mech in meta['mechanisms'].values():
            units |= {p_['unit'] for p_ in mech['parameters'].values()}
        self.assertLessEqual(units - si, {'eV', '1/eV'}, units - si)
        h = ready()
        h.register_principal('e', 'evaluator')
        h.invoke('stage.move_xy', x_m=0.01, y_m=0.0)
        h.step(0.5)
        obs = h.session('e').read(instance_id=h.instance_id)['observations']
        self.assertIsNone(obs['stage.x_indicated_m']['sample_window_s'])
        self.assertGreater(obs['stage.x_indicated_m']['value'], 0.0)                         # continuous between the cycles
        self.assertEqual(obs['vacuum.chamber_pressure_indicated_pa']['sample_window_s'], rt.CYCLE_S)
        self.assertEqual(obs['vacuum.chamber_pressure_indicated_pa']['sample_time_s'], math.floor(h.time_s))
        self.assertEqual(obs['truth.filament_life_s']['unit'], 's')
        self.assertNotIn('Pure read of the latest 1 s instrument cycle', json.dumps(meta['state_slots']))


    def test_wet_specimen_outgasses_whenever_it_is_in_the_pumped_chamber(self):                   # F38
        wet = spec('leaf', 'tissue_soft', wet=True, topo='rough_fracture', height=0.002)
        h = ready(specimen=None, evacuate=False)
        h.invoke('specimen.provide', **wet)
        h.invoke('chamber.draw_out')
        h.step(10.0)
        h.invoke('vacuum.evacuate')                                              # EVAC pressed with the door out, the specimen loaded afterwards
        h.step(5.0)
        h.invoke('specimen.load', specimen_id='leaf')
        h.step(20.0)
        self.assertEqual(h._spec()['_wet_exposed_s'], 0.0)                      # drawn out: in room air, not drying
        h.invoke('stage.set_z', z_lift_m=rt.WD_RANGE_M[1] - 0.010 - 0.002)
        h.step(30.0)
        h.invoke('chamber.draw_in')
        h.step(rt.EVAC_LIMIT_S)
        self.assertEqual(h.error, 'EVAC_TIMEOUT')                               # the water load keeps high vacuum out of reach, as on the normal path
        self.assertNotEqual(h.vac_state, 'hv_ready')
        dried = h._spec()['_wet_exposed_s']
        self.assertGreater(dried, 400.0)
        h.invoke('error.acknowledge')
        h.invoke('vacuum.vent')
        h.step(200.0)
        self.assertEqual(h._spec()['_wet_exposed_s'], dried)                    # venting neither dries nor re-wets it
        h.invoke('vacuum.toggle_lv')
        h.invoke('vacuum.evacuate')
        h.step(3600.0)                                                          # an hour in low vacuum dries it
        h.invoke('vacuum.vent')
        h.step(200.0)
        h.invoke('vacuum.toggle_lv')
        h.invoke('vacuum.evacuate')
        h.step(400.0)
        self.assertEqual(h.vac_state, 'hv_ready')
        led = h.ledger()
        self.assertLess(abs(led['gas_residual_mol']), 1e-9 * max(1.0, led['gas_admitted_mol']))


if __name__ == '__main__':
    unittest.main()
