"""SEM Operation (front panel) tests for CHEM-151 jeol_jsmit200 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/jeol_jsmit200.py):
python3 -m pytest tests/test_panel_ui_jeol_jsmit200.py -q

Every gesture goes through the session API (panel.press / panel.turn / panel.switch / panel.touch, the runtime's normal invoke path); SEM Operation
widgets are found by id in the structured frame (panel.frame, evaluator) and tapped at their centre.  The specimen is put on the holder by the
agent's robot skill (specimen.load), the plant and the specimen by the evaluator.  Themes: the six key procedures of the menu survey; the functions
behind the windows and icons (beam blanking, scan modes, manual tool, beam shift and SRT, electron gun window with injected alignment errors, wobbler
and the aperture knobs, P.C. scale and high current mode, photo data windows, navigator, memory, coordinates, calibration, settings windows, files,
movie, snapshot, ruler, EDS window, users); panel = direct capability (same runtime state); refusals and 'not installed' messages with the state
unchanged; time-outs; render determinism and the evidence PNGs; contract entries."""
import copy
import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import jeol_jsmit200 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'jsmit200_01'
DENIED_LINES = ['Another user is acquiring, or the data belong to another user.']
D = 'monitor_screen'
OUT = ROOT / 'outputs/panel_ui/jeol_jsmit200'
SURVEY_TASKS = ['start the system', 'exchange specimen', 'start imaging', 'set observation conditions by recipe', 'save an image', 'finish']
DONE_TASKS = set()
FE = rt.SPECIMEN_EXAMPLE
WD10 = rt.WD_RANGE_M[1] - 0.010


class Rig:
    """A runtime with an agent and an evaluator session; gestures through panel.* capabilities, the specimen handled by the robot skill."""

    def __init__(self, on=True, load=True, evac=True, observe=False, seed=1, specimen=FE):
        self.h = rt.JeolJsmit200Runtime(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.spec = specimen
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed))
        self.call(self.E, 'specimen.provide', **specimen)
        if not on:
            return
        assert self.switch('main_power', 'START')
        self.step(rt.SOFTWARE_BOOT_S + 1.0)
        assert self.page() == ('main', 'main')
        if load:
            self.load()
        if evac:
            assert self.press('button_evac')
            self.step(240.0)
            assert self.val('vacuum.state') == 'hv_ready'
        if observe:
            self.observe()

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def op(self, path):
        v = self.val('operation.state')
        for k in path.split('.'):
            v = v[k]
        return v

    def step(self, dt):
        self.h.step(dt)

    # ---- gestures and screen
    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def turn(self, knob, detents):
        return self.call(self.A, 'panel.turn', input_id=knob, detents=detents)['accepted']

    def switch(self, inp, pos):
        return self.call(self.A, 'panel.switch', input_id=inp, position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][D]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=D, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid, hold=0.1, dx=0.5, dy=0.5):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + int(w * dx), y + int(h * dy), duration_s=hold)

    def dbl(self, wid, dx=0.5, dy=0.5):
        self.tap(wid, dx=dx, dy=dy)
        return self.tap(wid, dx=dx, dy=dy)

    def drag(self, wid, u1, v1, u2, v2):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + int(w * u1), y + int(h * v1), to_x=x + int(w * u2), to_y=y + int(h * v2))

    def slide(self, wid, value):
        w = self.widgets()[wid]
        lo, hi = SLIDERS[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + round((value - lo) / (hi - lo) * (ww - 1)), y + hh // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def dialog(self):
        ov = self.overlay()
        return None if ov is None else ov.get('dialog')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in self.overlay()["buttons"]]}')

    def row(self, wid, first, col=0):
        w = self.widgets()[wid]
        cells = [c[col] for c in w['cells']]
        k = cells.index(first)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def type(self, text):
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        labels = {b['label'] for b in ov['buttons']}
        if 'q' in labels or 'Q' in labels:
            for _ in range(len(self.overlay()['field']) + 1):
                self.ov('Del')
            for ch in text:
                mode = self.overlay()['mode']
                if ch.isdigit() or ch in '/_.':
                    if mode != 'sym':
                        self.ov('12@')
                elif mode == 'sym':
                    self.ov('abc')
                if ch.isalpha() and (self.overlay()['mode'] == 'upper') != ch.isupper():
                    self.ov('Shift')
                self.ov(ch)
        else:
            self.ov('C')
            for ch in text:
                self.ov(ch)
        assert self.overlay()['field'] == text, (self.overlay()['field'], text)

    def setf(self, form_widget, label, value):
        """A form row: a choice (Off / On or an option text) or a value typed on the keypad / keyboard."""
        self.row(form_widget, label)
        ov = self.overlay()
        if ov['kind'] == 'keyboard':
            self.type(value)
            return self.ov('OK')
        return self.ov(value)

    def ok(self):
        return self.ov('OK')

    # ---- procedures
    def load(self, wd=0.010):
        assert self.switch('chamber_door', 'open')
        self.step(3.0)
        self.call(self.A, 'specimen.load', specimen_id=self.spec['specimen_id'])
        z = rt.WD_RANGE_M[1] - wd - self.spec['height_m']
        assert self.turn('knob_stage_z', round(z / 1e-4))
        self.step(30.0)
        assert self.switch('chamber_door', 'closed')
        self.step(3.0)

    def observe(self):
        assert self.tap('fx_obs_l')
        self.step(rt.SATURATION_S + 1.0)
        for k in ('fx_af_l', 'fx_as_l', 'fx_acb_l'):
            assert self.tap(k), k

    def go(self, icon, tab='home'):
        if self.widgets().get('mb_' + tab, {}).get('selected') is False or ('hi_open' in self.widgets()) != (tab == 'home'):
            self.tap('mb_' + tab)
        return self.tap(('hi_' if tab == 'home' else 'si_') + icon)


SLIDERS = {'acc_slider': (500.0, 30000.0), 'pc_slider': (1.0, 99.0), 'wd_slider': (0.005, 0.048), 'pr_slider': (10.0, 100.0), 'gn_heat': (0.0, 1.0),
           'gn_coarse': (0.0, 100.0), 'gn_fine': (0.0, 100.0), 'w_tiltmag_slider': (0.0, 90.0), 'w_dynfocus_slider': (0.0, 90.0), 'mix_ratio': (0.0, 1.0)}


def mean(rows):
    return sum(sum(r) for r in rows) / (len(rows) * len(rows[0]))


class SurveyProcedures(unittest.TestCase):
    def test_start_the_system(self):
        g = Rig(on=False)
        self.assertEqual(g.page(), ('off', None))
        self.assertFalse(g.touch(500, 500))                                       # nothing on the monitor while the power is off
        self.assertFalse(g.press('button_evac'))
        self.assertFalse(g.switch('main_power', 'ON'))                            # ON alone does not start the instrument
        self.assertFalse(g.val('power.enabled'))
        self.assertTrue(g.switch('main_power', 'START'))                         # key to START, released to ON (manual 3-5)
        self.assertEqual(g.page(), ('booting', 'loading'))
        g.step(rt.SOFTWARE_BOOT_S + 1.0)
        self.assertEqual(g.page(), ('main', 'main'))
        w = g.widgets()
        self.assertIn('Std.-P.C.', w['pd_pc']['text'])
        self.assertIn('HighVac.', w['pd_vac']['text'])
        self.assertIn('WAIT', w['ev_progress']['text'])
        self.assertTrue(g.frame()['displays']['lamp_vent']['on'])                 # delivered vented: VENT lit
        DONE_TASKS.add('start the system')

    def test_exchange_specimen(self):
        g = Rig(load=False)
        self.assertIn('READY', g.text('ev_progress'))
        g.tap('exchange')
        self.assertEqual(g.dialog(), 'd_exchange')
        g.ov('Yes')                                                               # confirmation vents the chamber (Iowa State p.4)
        self.assertEqual(g.page(), ('main', 'ex1'))
        self.assertEqual(g.val('vacuum.state'), 'venting')
        self.assertFalse(g.widgets()['ex1_next']['enabled'])
        g.step(150.0)
        self.assertEqual(g.val('vacuum.state'), 'vented')
        self.assertTrue(g.switch('chamber_door', 'open'))
        g.step(3.0)
        self.assertTrue(g.tap('ex1_next'))
        self.assertEqual(g.page(), ('main', 'ex2'))
        g.setf('ex2_form', 'Specimen name', 'FeSi1')
        g.setf('ex2_form', 'Specimen holder', '10mm round')
        g.setf('ex2_form', 'Height (protrusion)', '5')
        self.assertEqual(g.op('specimen.name'), '')                               # the guide writes the information on Next
        g.tap('ex2_next')
        self.assertEqual(g.op('specimen'), dict(name='FeSi1', holder='10mm_round', height_m=0.005))
        self.assertEqual(g.page(), ('main', 'ex3'))
        g.tap('ex3_metal_mineral')
        g.tap('ex3_coated')
        g.tap('ex3_exec')                                                         # recipe: metal / mineral, coated
        self.assertEqual((g.val('beam.accelerating_voltage_v'), g.val('beam.probe_current_pc'), g.val('detector.selected')), (15000.0, 60.0, 'SED'))
        self.assertEqual(g.page(), ('main', 'ex4'))
        self.assertIn('Turn the Z knob to 33 mm', g.text('ex4_t'))
        self.assertFalse(g.tap('ex4_photo'))                                      # overview photo needs the absent navigation camera
        self.assertEqual(g.dialog(), 'na_specimen_exchange___overview')
        g.ok()
        g.call(g.A, 'specimen.load', specimen_id=FE['specimen_id'])
        self.assertTrue(g.turn('knob_stage_z', 330))
        g.step(30.0)
        g.tap('ex4_next')
        self.assertFalse(g.widgets()['ex5_evac']['enabled'])                      # door still open
        g.switch('chamber_door', 'closed')
        g.step(3.0)
        self.assertTrue(g.tap('ex5_evac'))
        g.step(240.0)
        self.assertEqual(g.val('vacuum.state'), 'hv_ready')
        self.assertIn('READY', g.text('ex5_s'))
        g.tap('ex5_done')
        self.assertEqual(g.page(), ('main', 'main'))
        self.assertEqual(g.val('specimen.loaded_id'), FE['specimen_id'])
        self.assertAlmostEqual(g.h._wd_true(), 0.010, places=4)
        DONE_TASKS.add('exchange specimen')

    def test_start_imaging(self):
        g = Rig()
        self.assertEqual(g.text('fx_obs_l'), 'Observ.')
        self.assertIsNone(g.val('beam.emission_indicated_a'))
        g.tap('fx_obs_l')                                                         # Observation: the gun starts, an image forms in about 10 s
        self.assertTrue(g.val('beam.ht_on'))
        g.step(rt.SATURATION_S + 1.0)
        self.assertEqual(g.val('beam.filament_state'), 'saturated')
        self.assertEqual(g.text('fx_obs_l'), 'Obs. ON')
        live = g.h.panel_read()['host.main_image']
        self.assertEqual((len(live), len(live[0])), (rt.THUMB_H, rt.THUMB_W))
        x0 = g.val('stage.x_indicated_m')
        g.tap('nv_left')                                                          # the navigator moves the field of view (10 % of the field)
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m') - x0, 0.1 * g.val('scan.field_width_m'), places=7)
        g.tap('fx_af_l')
        self.assertLess(abs(g.h.read('evaluator')['truth.defocus_m']), 1e-4)
        g.tap('fx_as_l')
        self.assertTrue(g.val('optics.astigmatism_corrected'))
        g.tap('fx_acb_l')
        self.assertTrue(g.val('optics.acb_done'))
        mag = g.val('scan.magnification')
        self.assertTrue(g.turn('mouse_wheel', 3))                                 # wheel forward = zoom in
        self.assertEqual(g.val('scan.magnification'), next(m for m in sorted(g.h.panel.spec['values']['mag']['extra']) if m > mag * 1.9))
        g.tap('fx_manual_l')
        g.tap('mt_focus')
        wd = g.val('optics.focus_wd_m')
        g.turn('mouse_wheel', 2)                                                  # Manual open: the wheel fine-focuses
        self.assertAlmostEqual(g.val('optics.focus_wd_m'), wd - 2 * 0.02 * g.val('scan.field_width_m'), places=12)
        DONE_TASKS.add('start imaging')

    def test_recipe(self):
        g = Rig(observe=True)
        g.go('recipes')
        self.assertEqual(g.page(), ('main', 'w_recipes'))
        g.tap('rc_organic')
        g.tap('rc_lowvac')
        g.tap('rc_execute')                                                       # organic, not coated, low vacuum: BE image in LV
        self.assertEqual((g.val('vacuum.mode'), g.val('vacuum.lv_setpoint_pa'), g.val('detector.selected')), ('LV', 40.0, 'BED_COMPO'))
        self.assertEqual((g.val('beam.accelerating_voltage_v'), g.val('beam.probe_current_pc')), (10000.0, 60.0))
        self.assertFalse(g.val('beam.ht_on'))                                     # the mode change runs with the beam off
        g.tap('rc_high_mag')
        self.assertFalse(g.tap('rc_execute'))                                     # High-Mag has no low vacuum condition ('-')
        self.assertEqual(g.dialog(), 'err_recipe_not_available')
        g.ok()
        g.tap('rc_coated')
        g.tap('rc_execute')
        self.assertEqual((g.val('vacuum.mode'), g.val('beam.accelerating_voltage_v'), g.val('beam.probe_current_pc'), g.val('detector.selected')),
                         ('HV', 20000.0, 35.0, 'SED'))
        DONE_TASKS.add('set observation conditions by recipe')

    def test_save_an_image(self):
        g = Rig(observe=True)
        g.go('image', tab='settings')
        g.setf('f_save_image_w', 'Auto Save', 'On')
        g.setf('f_save_image_w', 'File name', 'site')
        self.assertTrue(g.op('settings.image_save.auto_save'))
        self.assertIn('site_0001.tif', g.text('svi_example'))
        g.tap('w_save_image_close')
        g.tap('fx_photo_l', hold=1.2)                                            # right click on Photo: resolution and scan time (Iowa State p.7)
        self.assertEqual(g.page(), ('main', 'w_photo'))
        g.setf('f_photo_w', '1 Frame', '2560 x 1920  80 s')
        g.tap('w_photo_close')
        g.tap('fx_photo_l')
        self.assertEqual(g.val('acquisition.kind'), 'image')
        rec = g.h.images[g.val('acquisition.id')]
        self.assertEqual((rec['pixels'], rec['frame_time_s']), ('2560x1920', 80.0))
        g.step(81.0)
        self.assertEqual(rec['status'], 'completed')
        f = g.op('files')[-1]
        self.assertEqual((f['file'], f['folder'], f['image_id'], f['format']), ('site_0001.tif', 'E:/DATA/IT200/user', rec['image_id'], 'TIFF'))
        self.assertEqual(f['photo_data']['accv_kv'], 15.0)
        # Auto Save off: Freeze and Save As (manual 3-53)
        g.go('image', tab='settings')
        g.setf('f_save_image_w', 'Auto Save', 'Off')
        g.tap('w_save_image_close')
        g.tap('fx_freeze_l')
        g.tap('mb_home')
        g.tap('hi_save')
        self.assertEqual(g.dialog(), 'd_save')
        g.ov('File name')
        g.type('frozen1')
        g.ok()
        g.ov('Save')
        self.assertEqual(g.op('files')[-1]['file'], 'frozen1.tif')
        self.assertIsNone(g.op('files')[-1]['image_id'])                          # the frozen live frame
        DONE_TASKS.add('save an image')

    def test_finish(self):
        g = Rig(observe=True)
        g.tap('ev_progress')                                                      # click the WAIT - READY bar, VENT, confirm (Iowa State p.7)
        self.assertEqual(g.dialog(), 'd_evac')
        g.ov('VENT')
        self.assertEqual(g.dialog(), 'd_vent')
        g.ov('Yes')
        self.assertFalse(g.val('beam.ht_on'))
        g.step(150.0)
        self.assertEqual(g.frame()['displays']['lamp_vent']['on'], True)         # VENT solid green
        self.assertEqual(g.val('panel.vent_lamp_mode'), 'on')
        g.switch('chamber_door', 'open')
        g.step(3.0)
        g.call(g.A, 'specimen.unload')
        g.turn('knob_stage_z', -400)
        g.step(30.0)
        g.switch('chamber_door', 'closed')
        g.step(3.0)
        g.tap('ev_evac')
        g.ov('Yes')
        g.step(240.0)
        self.assertEqual(g.val('vacuum.state'), 'hv_ready')
        g.tap('mb_pull')
        g.ov('Exit')
        self.assertEqual(g.dialog(), 'd_exit')
        g.ov('Yes')
        self.assertEqual(g.val('system.state'), 'shutdown')
        self.assertEqual(g.page(), ('closed', 'closed'))
        g.switch('main_power', 'OFF')
        self.assertEqual(g.page(), ('off', None))
        ev = g.call(g.A, 'events.read', since_s=0.0)['events']
        self.assertNotIn('UNSAFE_POWER_OFF', [e['code'] for e in ev])
        DONE_TASKS.add('finish')


class DeviceFunctions(unittest.TestCase):
    def test_beam_blank_freeze_and_scan_modes(self):
        g = Rig(observe=True)
        ip = g.h.read('evaluator')['truth.probe_current_a']
        self.assertGreater(mean(g.h.panel_read()['host.main_image']), 5)
        g.tap('hi_blank')
        self.assertTrue(g.val('beam.blanked'))
        self.assertEqual(g.h.read('evaluator')['truth.probe_current_a'], 0.0)
        self.assertEqual(mean(g.h.panel_read()['host.main_image']), 0)
        self.assertIn('BEAM BLANKING', g.text('ov_state'))
        g.tap('hi_blank')
        self.assertEqual(g.h.read('evaluator')['truth.probe_current_a'], ip)
        live = g.h.panel_read()['host.main_image']
        g.tap('fx_freeze_l')                                                      # Freeze: static image, beam blanked
        self.assertEqual(g.val('scan.mode'), 'FREEZE')
        self.assertEqual(g.h.read('evaluator')['truth.probe_current_a'], 0.0)
        self.assertEqual(g.h.panel_read()['host.main_image'], live)
        g.tap('fx_slow_l')
        g.tap('fx_cf_l')
        self.assertEqual((g.val('scan.mode'), g.val('scan.charge_free')), ('SLOW', True))
        self.assertEqual(g.h._charging_factor(), rt.CF_CHARGING)
        g.tap('fx_rdc_l')
        self.assertEqual(g.h._charging_factor(), 1.0)                             # CF only in Slow / Photo
        self.assertTrue(g.widgets()['fx_rdc_l']['selected'])

    def test_manual_tool(self):
        g = Rig(observe=True)
        g.tap('co_srt')                                                           # the SRT readout opens the manual tool on SRT
        self.assertTrue(g.widgets()['mt_srt']['selected'])
        g.tap('srt_90')
        self.assertAlmostEqual(g.val('scan.rotation_rad'), math.pi / 2)
        g.tap('srt_m01')
        self.assertAlmostEqual(g.val('scan.rotation_rad'), math.pi / 2 - math.radians(0.1))
        g.tap('srt_0')
        g.tap('srt_h')
        self.assertTrue(g.drag('img_main', 0.2, 0.6, 0.8, 0.4))                   # draw a line along the object: it becomes horizontal
        self.assertAlmostEqual(g.val('scan.rotation_rad'), math.atan2(0.2 * 3, 0.6 * 4) % (2 * math.pi), delta=0.06)
        g.tap('srt_0')
        g.tap('mt_focus')
        wd, fw = g.val('optics.focus_wd_m'), g.val('scan.field_width_m')
        g.tap('fo_rough_over')
        self.assertAlmostEqual(g.val('optics.focus_wd_m'), wd - 0.5 * fw, places=12)
        g.tap('fo_rough_under')
        g.tap('fo_fine_under', hold=1.2)
        self.assertAlmostEqual(g.val('optics.focus_wd_m'), wd + 5 * 0.02 * fw, places=12)
        g.tap('mt_stigma')
        r0 = g.h.read('evaluator')['truth.astigmatism_m']
        for _ in range(3):
            g.tap('sg_+X')
        self.assertAlmostEqual(g.val('optics.stigmator_m')[0] - g.h.stig[0] + 6e-9, 6e-9, places=15)
        self.assertGreater(g.h.read('evaluator')['truth.astigmatism_m'], r0)
        self.assertFalse(g.val('optics.astigmatism_corrected'))
        g.tap('fx_as_l')
        self.assertAlmostEqual(g.h.read('evaluator')['truth.astigmatism_m'], 1e-9)
        g.tap('mt_bright')
        m0 = mean(g.h.panel_read()['host.main_image'])
        g.tap('br_brightness_p', hold=1.2)
        self.assertAlmostEqual(g.val('optics.image_adjust')['brightness'], 0.6)
        self.assertGreater(mean(g.h.panel_read()['host.main_image']), m0)
        g.tap('fx_acb_l')                                                         # ACB resets the manual adjustment
        self.assertEqual(g.val('optics.image_adjust'), dict(brightness=0.5, contrast=0.5, suppress=0.5))
        g.tap('mt_suppress')
        self.assertFalse(g.widgets()['su_p']['enabled'])                          # Suppress: BE images only (SED selected)

    def test_beam_shift_limit(self):
        g = Rig(observe=True)
        g.call(g.A, 'scan.set_magnification', magnification=5000.0)
        g.tap('fx_manual_l')
        g.tap('mt_shift')
        fw = g.val('scan.field_width_m')                                          # x5,000: 25.6 um field
        g.tap('bs_+X')
        self.assertAlmostEqual(g.val('optics.beam_shift_m')[0], 0.05 * fw, places=12)
        u0 = g.h._beam_point()[0]
        g.tap('bs_+X', hold=1.2)
        g.tap('bs_+X', hold=1.2)
        self.assertEqual(g.val('optics.beam_shift_m')[0], rt.BEAM_SHIFT_LIMIT_M)  # stops at the deflection limit
        self.assertIn('LIMIT', g.text('mt_center'))
        self.assertNotAlmostEqual(g.h._beam_point()[0], u0)
        g.tap('bs_reset')
        self.assertEqual(g.val('optics.beam_shift_m'), [0.0, 0.0])

    def test_gun_window_finds_an_injected_misalignment(self):
        g = Rig(observe=True)
        ip0 = g.h.read('evaluator')['truth.probe_current_a']
        g.call(g.E, 'alignment.configure_error', **dict(gun_shift_x=0.3, gun_shift_y=0.0, gun_tilt_x=0.0, gun_tilt_y=-0.2, aperture_x_m=0.0, aperture_y_m=0.0,
                                                        stage_x_m=0.0, stage_y_m=0.0))
        ip1 = g.h.read('evaluator')['truth.probe_current_a']
        self.assertAlmostEqual(ip1 / ip0, math.exp(-(0.3 ** 2 + 0.2 ** 2) / rt.GUN_ALIGN_WIDTH ** 2))
        g.go('gun')
        self.assertEqual(g.page(), ('main', 'w_gun'))
        g.tap('gn_agc')
        self.assertEqual(g.val('gun.auto_adjust'), 'AGC')
        self.assertFalse(g.tap('gn_agc'))                                         # already running
        g.ok()
        g.step(rt.GUN_AUTO_S + 1.0)
        self.assertIsNone(g.val('gun.auto_adjust'))
        self.assertEqual(g.val('gun.settings')['shift_x'], 0.3)
        self.assertAlmostEqual(g.h.read('evaluator')['truth.probe_current_a'], ip0)
        g.tap('gn_tab_al')                                                        # manual alignment: Shift X - by 0.1 lowers the current
        g.tap('gn_shift_x_m', hold=1.2)
        self.assertAlmostEqual(g.val('gun.settings')['shift_x'], 0.2)
        self.assertEqual(g.val('gun.settings')['mode'], 'manual')
        self.assertLess(g.h.read('evaluator')['truth.probe_current_a'], ip0)
        g.tap('gn_tab_fil')
        g.slide('gn_heat', 0.3)                                                   # below saturation: the false peak
        g.step(2.0)
        self.assertAlmostEqual(g.val('gun.settings')['heating'], 0.3)
        self.assertLess(g.val('beam.emission_indicated_a'), 0.7 * rt.EMISSION_SAT_A)
        g.slide('gn_heat', 0.7)
        self.assertIn('ORANGE AREA', g.text('gn_lc'))
        self.assertGreater(g.h._wear_factor(), 1.0)
        g.slide('gn_heat', 0.6)
        g.slide('gn_coarse', 75.0)
        g.step(2.0)
        self.assertAlmostEqual(g.val('beam.emission_indicated_a') / rt.EMISSION_SAT_A, 0.5, delta=0.03)
        g.tap('gn_save')
        g.slide('gn_coarse', 50.0)
        g.tap('gn_preset')
        self.assertEqual(g.val('gun.settings')['bias_coarse'], 75.0)
        g.call(g.A, 'vacuum.toggle_lv')
        g.step(120.0)
        g.call(g.A, 'beam.observation_on')
        g.step(11.0)
        self.assertFalse(g.tap('gn_afs'))                                         # not in the low vacuum mode (manual 3-59)
        self.assertEqual(g.dialog(), 'err_lv_mode')

    def test_wobbler_and_aperture_knobs(self):
        g = Rig(observe=True)
        g.call(g.E, 'alignment.configure_error', **dict(gun_shift_x=0.0, gun_shift_y=0.0, gun_tilt_x=0.0, gun_tilt_y=0.0, aperture_x_m=3e-5, aperture_y_m=-1e-5,
                                                        stage_x_m=0.0, stage_y_m=0.0))
        ip = g.h.read('evaluator')['truth.probe_current_a']
        g.tap('hi_wobbler')
        self.assertTrue(g.val('optics.wobbler_on'))
        self.assertIn('Wobbler: image swings', g.text('ov_wob'))
        self.assertAlmostEqual(g.h.panel_read()['host.wobble_shift_m'], rt.WOBBLE_GAIN * math.hypot(3e-5, 1e-5))
        self.assertTrue(g.turn('knob_aperture_x', 6))                             # X / Y fine adjustment knobs: minimise the wobbling
        self.assertTrue(g.turn('knob_aperture_y', -2))
        self.assertAlmostEqual(g.h.panel_read()['host.wobble_shift_m'], 0.0, places=12)
        self.assertGreater(g.h.read('evaluator')['truth.probe_current_a'], ip)
        g.tap('hi_wobbler')
        self.assertFalse(g.val('optics.wobbler_on'))

    def test_photo_data_windows(self):
        g = Rig(observe=True)
        g.tap('pd_accv')
        self.assertEqual(g.page(), ('main', 'w_accv'))
        g.tap('acc_p1')                                                           # preset 5 kV: selected, applied by Apply
        self.assertEqual(g.val('beam.accelerating_voltage_v'), 15000.0)
        g.tap('acc_apply')
        self.assertEqual(g.val('beam.accelerating_voltage_v'), 5000.0)
        g.slide('acc_slider', 7000.0)
        g.tap('acc_p4', hold=1.2)                                                  # hold the preset: it takes the selected value
        self.assertEqual(g.op('presets.accv')[4], 7000.0)
        g.tap('w_accv_close')
        g.tap('pd_pc')
        g.slide('pc_slider', 30.0)
        self.assertEqual(g.val('beam.probe_current_pc'), 30.0)
        self.assertAlmostEqual(g.val('beam.probe_current_setpoint_a'), 1e-12 * (3e4) ** (29 / 98))
        g.tap('pc_p3')
        self.assertEqual(g.val('beam.probe_current_pc'), 60.0)
        g.tap('w_pc_close')
        g.tap('pd_mag')
        g.tap('mg_p2')
        self.assertEqual(g.val('scan.magnification'), 1000.0)
        g.tap('w_mag_close')
        g.tap('pd_wd')
        g.tap('wd_p1')
        self.assertEqual(g.val('optics.focus_wd_m'), 0.010)
        g.tap('w_wd_close')
        g.tap('pd_signal')
        g.tap('sig_REF')
        self.assertEqual(g.val('detector.selected'), 'REF')
        g.tap('sig_BED_TOPO')
        self.assertFalse(g.tap('sig_aux'))
        self.assertEqual(g.dialog(), 'na_signal___aux')
        g.ok()
        g.tap('w_signal_close')
        self.assertIn('BED-T', g.text('pd_signal'))
        g.tap('pd_vac')
        g.tap('pr_lv')
        g.ov('Yes')
        g.step(120.0)
        self.assertEqual(g.val('vacuum.state'), 'lv_ready')
        g.tap('pr_p2')                                                            # 100 Pa preset: adjusted automatically
        g.step(120.0)
        self.assertAlmostEqual(g.val('vacuum.lv_pressure_indicated_pa'), 100.0, delta=5.0)
        g.tap('pr_apply')                                                         # Stop: the valve holds
        self.assertFalse(g.op('lv_auto'))
        self.assertEqual(g.text('pr_apply'), 'Apply')
        g.slide('pr_slider', 30.0)
        g.tap('pr_apply')
        self.assertEqual((g.val('vacuum.lv_setpoint_pa'), g.op('lv_auto')), (30.0, True))
        g.step(150.0)
        g.tap('pr_fine_up')
        self.assertEqual(g.val('vacuum.lv_setpoint_pa'), 29.0)
        self.assertIn('Set 29 Pa', g.text('pr_now'))
        g.tap('w_pressure_close')
        self.assertIn('29Pa', g.text('pd_vac'))

    def test_navigator_memory_coordinates_and_calibration(self):
        g = Rig(observe=True)
        g.tap('fx_manual_l')                                                      # frame travel rate 50 %
        g.go('stage', tab='settings')
        g.setf('f_stage_w', 'Frame Travel Rate', 'On')
        g.tap('w_stage_close')
        g.tap('mb_home')
        x0, fw = g.val('stage.x_indicated_m'), g.val('scan.field_width_m')
        g.tap('nv_right')
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), x0 - 0.5 * fw, places=7)
        g.tap('nv_last')                                                          # Last Move: back to the previous coordinates
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), x0, places=7)
        g.tap('nv_memory')
        g.setf('f_mem_w', 'Name (empty: date and time)', 'siteA')
        g.tap('me_rec')
        g.tap('w_memory_close')
        g.tap('co_area')
        g.setf('f_coords_w', 'X', '2.5')
        g.setf('f_coords_w', 'Y', '-1')
        g.tap('co_exec')
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), 0.0025, places=7)
        self.assertAlmostEqual(g.val('stage.y_indicated_m'), -0.001, places=7)
        g.tap('co_area')
        g.tap('co_rel')
        g.setf('f_coords_w', 'X', '1')
        g.setf('f_coords_w', 'Y', '0')
        g.tap('co_exec')
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), 0.0035, places=7)
        g.tap('nv_memory')
        g.row('me_list', 'siteA')
        g.tap('me_move')
        g.ov('Yes')
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), x0, places=6)
        self.assertTrue(g.dbl('nv_graphic', 0.25, 0.5))                           # double tap on the holder graphic: the stage goes there
        g.step(10.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), rt.HOLDER_RADIUS_M * 1.25 * 0.5, delta=1e-3)
        self.assertFalse(g.tap('nv_rplus'))                                       # no R motor
        g.ok()
        g.tap('nv_page')
        self.assertIn('Z (manual knob)', g.text('nv_tz'))
        # stage calibration with an injected coordinate error (vented, specimen removed)
        g.call(g.E, 'alignment.configure_error', **dict(gun_shift_x=0.0, gun_shift_y=0.0, gun_tilt_x=0.0, gun_tilt_y=0.0, aperture_x_m=0.0, aperture_y_m=0.0,
                                                        stage_x_m=2e-4, stage_y_m=-1e-4))
        self.assertAlmostEqual(g.val('stage.x_indicated_m') - g.h.read('evaluator')['stage.x_m'], 2e-4, places=9)
        g.go('stage', tab='settings')
        g.tap('sg_cal_exec')
        self.assertEqual(g.dialog(), 'err_vent_required')
        g.ok()
        g.call(g.A, 'vacuum.vent')
        g.step(150.0)
        g.switch('chamber_door', 'open')
        g.step(3.0)
        g.call(g.A, 'specimen.unload')
        g.tap('sg_cal_exec')
        g.step(20.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), g.h.read('evaluator')['stage.x_m'], places=9)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), 0.0, places=7)

    def test_settings_windows(self):
        g = Rig(observe=True)
        g.go('eco', tab='settings')
        g.setf('f_eco_w', 'Standby after', '5')
        g.setf('f_eco_w', 'Eco (standby power saving)', 'On')
        g.tap('w_eco_close')
        g.step(299.0)
        self.assertTrue(g.val('beam.ht_on'))
        g.step(2.0)
        self.assertFalse(g.val('beam.ht_on'))                                     # 5 min without an operation: observation off
        g.go('eos', tab='settings')
        pc = g.val('beam.probe_current_pc')
        ip = g.val('beam.probe_current_setpoint_a')
        g.setf('f_eos_w', 'High Current Mode', 'On')
        self.assertEqual(g.val('beam.probe_current_pc'), pc)
        self.assertAlmostEqual(g.val('beam.probe_current_setpoint_a') / ip, 10.0)
        self.assertIn('HCM-P.C.', g.widgets().get('pd_pc', {}).get('text', 'HCM-P.C.'))
        g.tap('w_eos_close')
        g.go('auto', tab='settings')
        g.setf('f_auto_w', 'ACB after a condition change (auto interlock)', 'On')
        g.tap('w_auto_close')
        g.tap('fx_obs_l')
        g.step(11.0)
        g.call(g.A, 'beam.set_voltage', voltage_v=10000.0)
        self.assertTrue(g.val('optics.acb_done'))                                 # ACB ran after the voltage change
        g.go('navigator', tab='settings')
        g.setf('f_layout_w', 'Navigator area', 'Off')
        g.setf('f_layout_w', 'Fixed icons from the right', 'On')
        g.tap('w_layout_close')
        w = g.widgets()
        self.assertNotIn('nv_stage', w)
        self.assertGreater(w['fx_obs_r']['rect'][0], w['fx_manual_r']['rect'][0])
        g.go('theme', tab='settings')
        g.tap('th_dark')
        g.tap('w_theme_close')
        g.go('language', tab='settings')
        g.tap('lg_Japanese')
        self.assertEqual(g.op('settings.environment.language'), 'Japanese')
        g.tap('w_language_close')
        g.go('user', tab='settings')
        g.setf('f_newuser_w', 'New user', 'alice')
        g.tap('us_add')
        self.assertEqual((g.op('user'), g.op('settings.image_save.folder')), ('alice', 'E:/DATA/IT200/alice'))
        g.row('us_list', 'user')
        self.assertEqual(g.op('user'), 'user')
        g.tap('w_user_close')
        g.go('scan', tab='settings')
        g.setf('f_scan_w', 'Photo mode', 'Integration')
        g.setf('f_scan_w', 'Frame integration', '4')
        g.tap('w_scan_close')
        self.assertEqual(g.text('fx_photo_r'), 'Integ.')
        g.tap('fx_photo_r')
        rec = g.h.images[g.val('acquisition.id')]
        self.assertEqual((rec['pixels'], rec['frame_time_s']), ('1280x960', 4.0))
        g.step(5.0)
        g.go('vacuum', tab='settings')
        g.setf('f_vacuum_w', 'Slow Leak', 'On')
        self.assertTrue(g.op('settings.vacuum.slow_leak'))

    def test_slow_leak_evacuates_slower(self):
        def evac_time(slow):
            g = Rig(evac=False)
            if slow:
                g.go('vacuum', tab='settings')
                g.setf('f_vacuum_w', 'Slow Leak', 'On')
            g.press('button_evac')
            t = 0.0
            while g.val('vacuum.state') != 'hv_ready' and t < 700:
                g.step(10.0)
                t += 10.0
            return t
        self.assertGreater(evac_time(True), evac_time(False) + 60.0)

    def test_views_lut_histogram_label(self):
        g = Rig(observe=True)
        g.go('view')
        g.tap('vw_multiple')
        g.tap('w_view_close')
        w = g.widgets()
        self.assertIn('img_multi_live', w)
        self.assertNotIn('img_main', w)
        g.go('view')
        g.tap('vw_signal_mixing')
        g.tap('w_view_close')
        mix = g.h.panel_read()['host.mix']
        self.assertIsNotNone(mix)
        g.slide('mix_ratio', 1.0)
        self.assertEqual(g.op('settings.view.mix_ratio'), 1.0)
        g.tap('hi_full')
        self.assertIn('img_full', g.widgets())
        self.assertNotIn('pd_accv', g.widgets())
        g.tap('full_exit')
        g.tap('hi_histo')
        self.assertIn('hist_plot', g.widgets())
        self.assertEqual(sum(p[1] for p in g.widgets()['hist_plot']['series'][0]['points']), rt.THUMB_W * rt.THUMB_H)
        g.go('lut')
        g.setf('f_lut_w', 'Pseudo colour', 'On')
        g.tap('w_lut_close')
        self.assertIsInstance(g.h.panel_read()['host.main_image'][0][0], list)    # pseudo colour: rgb pixels
        g.tap('lb_area')
        g.setf('f_photo_data_w', 'Label', 'Lot7')
        g.setf('f_number_w', 'No. (next image number)', '12')
        g.tap('w_label_close')
        self.assertIn('Lot7', g.text('lb_area'))
        self.assertIn('No.12', g.text('lb_area'))
        g.go('tiltmag')
        g.slide('w_tiltmag_slider', 30.0)
        self.assertEqual(g.op('settings.tilt_correction.tilt_mag_deg'), 30.0)

    def test_movie_snapshot_ruler_files_eds(self):
        g = Rig(observe=True)
        g.call(g.A, 'scan.set_magnification', magnification=1000.0)
        g.tap('hi_movie')
        self.assertIsNotNone(g.op('movie'))
        g.step(10.0)
        self.assertIn('REC', g.text('ov_rec'))
        g.tap('hi_movie')
        f = g.op('files')[-1]
        self.assertEqual((f['kind'], f['frames'], f['pixels']), ('movie', 150, '640x480'))
        self.assertEqual(f['file'], g.h._date(g.h.time_s - 10.0).strftime('%Y%m%d%H%M%S') + '_0000.avi')
        g.tap('hi_ruler')
        self.assertTrue(g.drag('img_main', 0.25, 0.5, 0.75, 0.5))                 # half the field
        self.assertAlmostEqual(g.op('measurements')[-1]['distance_m'], 0.5 * g.val('scan.field_width_m'), delta=0.02 * g.val('scan.field_width_m'))
        self.assertIn('Ruler:', g.text('ov_ruler'))
        g.tap('nv_snap')
        g.tap('sn_snap')
        self.assertEqual(g.op('snapshot.conditions.mag'), 1000.0)
        g.tap('sn_save')
        self.assertEqual(g.op('files')[-1]['file'], 'Snap_0001.jpg')
        g.call(g.A, 'scan.set_magnification', magnification=5000.0)
        g.call(g.A, 'beam.set_voltage', voltage_v=5000.0)
        g.tap('sn_repro')
        g.tap('rp_apply')                                                          # accelerating voltage and magnification checked by default
        self.assertEqual((g.val('scan.magnification'), g.val('beam.accelerating_voltage_v')), (1000.0, 15000.0))
        g.tap('hi_open')
        g.row('op_list', 'Snap_0001.jpg')
        self.assertEqual(g.op('viewer'), 'Snap_0001.jpg')
        self.assertIn('File: Snap_0001.jpg', g.text('ov_state'))
        g.tap('hi_copy')
        self.assertEqual(g.op('clipboard'), 'Snap_0001.jpg')
        g.tap('viewer_close')
        self.assertIsNone(g.op('viewer'))
        g.tap('hi_transfer')
        self.assertIsNotNone(g.op('eds_image'))
        g.tap('hi_analysis')
        g.setf('f_eds_w', 'Live time', '20')
        g.tap('ed_start')
        self.assertEqual(g.val('acquisition.kind'), 'eds')
        g.step(40.0)
        self.assertIn('Last: eds-1', g.text('ed_acq'))
        g.tap('ed_quant')
        self.assertIn('wt%', g.text('ed_acq'))
        self.assertIn('Fe', g.text('ed_acq'))

    def test_anaglyph_from_a_tilt_pair(self):
        g = Rig(observe=True)
        g.go('image', tab='settings')
        g.setf('f_save_image_w', 'Auto Save', 'On')
        g.tap('w_save_image_close')
        g.tap('mb_home')
        g.call(g.A, 'image.acquire', expect='accepted', pixels='320x240', dwell_s=1e-5)
        g.step(2.0)
        g.turn('knob_stage_tilt', 6)                                              # 3 deg
        g.step(2.0)
        g.call(g.A, 'image.acquire', expect='accepted', pixels='320x240', dwell_s=1e-5)
        g.step(2.0)
        names = [f['file'] for f in g.op('files')]
        g.tap('hi_3d')
        g.row('an_list', names[0])
        g.tap('an_left')
        g.row('an_list', names[1])
        g.tap('an_right')
        g.tap('an_make')
        self.assertEqual(g.op('files')[-1]['kind'], 'anaglyph')
        self.assertFalse(g.tap('an_measure'))
        g.ok()

    def test_unsupported_items_show_the_device_message(self):
        g = Rig(observe=True)
        before = g.h.read('evaluator')
        for wid in ('hi_report', 'hi_pcd', 'ev_llc'):
            self.assertFalse(g.tap(wid), wid)
            self.assertTrue(g.dialog().startswith('na_'), wid)
            g.ok()
        g.tap('hi_save')                                                          # Save with Auto Save off opens Save As; cancel
        g.ov('Cancel')
        g.tap('co_area')
        self.assertFalse(g.tap('co_eucr'))
        g.ok()
        after = g.h.read('evaluator')
        for k in ('beam.ht_on', 'stage.x_m', 'vacuum.state', 'operation.state'):
            self.assertEqual(before[k], after[k], k)
        names = {u['item'] for u in rt.PANEL_SPEC['unsupported']}
        self.assertIn('Home - Report', names)
        self.assertIn('Evacuation - LLC', names)


class RefusalsTimeoutsAndEquivalence(unittest.TestCase):
    def test_refusals_show_messages_and_change_nothing(self):
        g = Rig()
        before = g.h.read('evaluator')
        self.assertFalse(g.tap('fx_af_l'))                                       # no beam
        self.assertEqual(g.dialog(), 'err_beam_not_ready')
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'BEAM_NOT_READY')
        g.ok()
        self.assertFalse(g.tap('fx_photo_l'))
        g.ok()
        self.assertFalse(g.tap('hi_movie'))
        self.assertEqual(g.dialog(), 'err_beam_not_ready')
        g.ok()
        self.assertFalse(g.tap('nv_last'))
        self.assertEqual(g.dialog(), 'err_no_last_move')
        g.ok()
        after = g.h.read('evaluator')
        for k in before:
            if not k.startswith('clock'):
                self.assertEqual(before[k], after[k], k)
        g.tap('co_area')
        g.setf('f_coords_w', 'X', '50')                                           # outside the 80 mm stroke: the keypad refuses
        self.assertIsNotNone(g.overlay())
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='no_such_key')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.touch', expect='rejected', display_id=D, x=1, y=1, to_x=3)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_booting_and_vented_refusals(self):
        g = Rig(on=False)
        g.switch('main_power', 'START')
        g.step(10.0)
        self.assertEqual(g.page(), ('booting', 'loading'))
        self.assertFalse(g.press('button_evac'))
        self.assertEqual(g.val('vacuum.state'), 'vented')
        g.step(rt.SOFTWARE_BOOT_S)
        self.assertFalse(g.tap('fx_obs_l'))                                      # vented: no observation
        self.assertEqual(g.dialog(), 'err_vacuum_not_ready')
        g.ok()
        self.assertTrue(g.turn('knob_stage_tilt', 4))                            # the manual knobs work at any time

    def test_time_outs(self):
        g = Rig(observe=True)
        g.tap('hi_copy')
        self.assertIsNotNone(g.frame()['ui']['message'])                         # the message shows for 3 s
        g.step(3.5)
        self.assertIsNone(g.frame()['ui']['message'])
        x0 = g.val('stage.x_indicated_m')
        g.tap('img_main', dx=0.75)
        g.step(1.0)                                                               # the second tap comes too late: no Click Center
        g.tap('img_main', dx=0.75)
        g.step(5.0)
        self.assertEqual(g.val('stage.x_indicated_m'), x0)
        g.dbl('img_main', dx=0.75)
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_indicated_m'), x0 - 0.25 * g.val('scan.field_width_m'), delta=0.02 * g.val('scan.field_width_m'))

    def test_panel_equals_direct(self):
        def script(direct):
            g = Rig()
            if direct:
                g.call(g.A, 'beam.observation_on')
                g.step(11.0)
                g.call(g.A, 'beam.set_pc', pc=40.0)
                g.call(g.A, 'scan.set_mode', mode='SLOW', charge_free=False)
                g.call(g.A, 'optics.set_stigmator', x_m=g.h.stig[0] + 2e-9, y_m=g.h.stig[1])
                g.call(g.A, 'beam.blank', on=True)
                g.call(g.A, 'scan.set_rotation', rotation_rad=math.pi)
                g.call(g.A, 'stage.click_center', u=0.4, v=0.5)
                g.call(g.A, 'recipe.execute', category='inorganic', coated=True, method='low_kv', eds=True)
                g.call(g.A, 'aperture.set_position', x_m=1e-5, y_m=0.0)
            else:
                g.tap('fx_obs_l')
                g.step(11.0)
                g.tap('pd_pc')
                g.slide('pc_slider', 40.0)
                g.tap('w_pc_close')
                g.tap('fx_slow_l')
                g.tap('fx_manual_l')
                g.tap('mt_stigma')
                g.tap('sg_+X')
                g.tap('hi_blank')
                g.tap('mt_srt')
                g.tap('srt_180')
                g.tap('nv_left')
                g.go('recipes')
                g.tap('rc_inorganic')
                g.tap('rc_coated')
                g.tap('rc_eds')
                g.tap('rc_execute')
                g.tap('rc_eds')
                g.tap('w_recipes_close')
                g.turn('knob_aperture_x', 2)
            g.step(3.0)
            return g.h.read('evaluator')
        a, b = script(False), script(True)
        for k in a:
            if k == 'operation.state':                                           # the panel run also leaves its last-move bookkeeping
                continue
            self.assertEqual(json.dumps(a[k], sort_keys=True, default=str), json.dumps(b[k], sort_keys=True, default=str), k)


def grey_noise(g):
    """rms frame-to-frame difference of the live image over one instrument cycle / sqrt(2): the noise of the shown image."""
    a = g.h.panel_read()['host.main_image']
    g.step(1.0)
    b = g.h.panel_read()['host.main_image']
    d = [x - y for ra, rb in zip(a, b) for x, y in zip(ra, rb)]
    return math.sqrt(sum(v * v for v in d) / len(d) / 2.0)


class SyncAudit20261019(unittest.TestCase):
    """Sync-audit fixes (1.1.1): panel gestures act as the session caller (runs started on the screen belong to it, another agent's data stay
    refused); the live scan combinations, Tilt Mag / Dynamic Focus and the selected holder / entered height act on the instrument; the language
    selection is kept while the texts stay English."""

    @staticmethod
    def tap_as(g, sess, wid, expect='succeeded'):
        x, y, w, h = g.widgets()[wid]['rect']
        g.n += 1
        r = sess.invoke(instance_id=INST, capability_id='panel.touch', arguments=dict(display_id=D, x=x + w // 2, y=y + h // 2), request_id=f'b{g.n}')
        assert r['status'] == expect, (wid, r['status'], r['error'])
        return r

    @staticmethod
    def unchanged(test, before, after):
        for k in before:
            if not k.startswith('clock'):
                test.assertEqual(before[k], after[k], k)

    def test_panel_started_runs_belong_to_the_caller(self):
        g = Rig(observe=True)
        g.h.register_principal('agent2', visibility='agent')
        B = g.h.session('agent2')
        g.tap('hi_analysis')
        g.setf('f_eds_w', 'Live time', '20')
        g.tap('ed_start')                                                          # EDS started on the screen by agent A
        op, rid = g.h.acq['operation_id'], g.h.acq['record_id']
        self.assertEqual((g.h._owners.get(op), g.h._owners.get(rid)), ('agent', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        before = g.h.read('evaluator')
        g.n += 1
        r = B.invoke(instance_id=INST, capability_id='panel.press', arguments=dict(input_id='button_vent', duration_s=0.1), request_id=f'b{g.n}')
        self.assertFalse(r['result']['accepted'])                                  # B's VENT key would stop A's analysis: the command is refused,
        self.assertEqual(g.dialog(), 'err_access_denied')                         # the screen says why (sweep: the gesture itself is not refused)
        self.assertEqual(g.overlay()['lines'], DENIED_LINES)
        g.ok()
        self.assertFalse(self.tap_as(g, B, 'ed_start')['result']['accepted'])
        self.assertEqual(g.dialog(), 'err_access_denied')
        g.ok()
        g.n += 1
        r = B.invoke(instance_id=INST, capability_id='beam.observation_off', arguments={}, request_id=f'b{g.n}')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.unchanged(self, before, g.h.read('evaluator'))
        g.step(40.0)
        self.assertEqual(g.A.read(instance_id=INST, state_ids=['result.last_spectrum_id'])['observations']['result.last_spectrum_id']['value'], rid)
        g.n += 1
        self.assertNotIn(rid, B.invoke(instance_id=INST, capability_id='records.list', arguments={}, request_id=f'b{g.n}')['result']['spectra'])
        g.tap('w_analysis_close')
        g.tap('fx_photo_l')                                                        # a photo started on the screen: A may cancel it
        op2 = g.h.acq['operation_id']
        self.assertEqual(g.h._owners.get(op2), 'agent')
        with self.assertRaises(rt.ActionRejected):
            B.cancel(op2, 'b-cancel')
        self.assertEqual(g.A.cancel(op2, 'a-cancel')['result']['phase'], 'cancelled')
        self.assertIsNone(g.h.acq)

    def test_navigation_and_hands_on_actions_are_free(self):
        """Sweep 2026-10-19: during another agent's acquisition, B may open windows (only the commands a gesture invokes are guarded) and may act on
        the hardware by hand (Z knob, aperture knobs, MAIN POWER key switch); a command such as a magnification change or the software shutdown
        stays guarded."""
        g = Rig(observe=True)
        g.h.register_principal('agent2', visibility='agent')
        B = g.h.session('agent2')
        g.tap('hi_analysis')
        g.setf('f_eds_w', 'Live time', '20')
        g.tap('ed_start')
        op = g.h.acq['operation_id']
        g.tap('w_analysis_close')
        self.assertTrue(self.tap_as(g, B, 'hi_analysis')['result']['accepted'])   # B opens the analysis window: navigation
        self.assertIsNone(g.dialog())
        self.assertIn('ed_start', g.widgets())
        z = g.h.read('evaluator')['stage.z_lift_m']
        for cap, args in (('stage.set_z', dict(z_lift_m=z - 0.001)), ('aperture.set_position', dict(x_m=0.0, y_m=0.0))):
            codes = []
            for s in (B, g.A):                                                     # hand knobs: accepted for both, the scan goes on
                g.n += 1
                r = s.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'h{g.n}')
                codes.append(r['error'] and r['error']['code'])
            self.assertEqual(codes, [None, None], cap)                              # nothing stops a hand turning a knob during a scan
        g.n += 1
        r = B.invoke(instance_id=INST, capability_id='scan.set_magnification', arguments=dict(magnification=500.0), request_id=f'b{g.n}')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')                      # a command stays guarded
        self.assertEqual(g.A.poll(op)['status'], 'running')
        g.n += 1
        r = B.invoke(instance_id=INST, capability_id='system.shutdown', arguments={}, request_id=f'b{g.n}')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')                      # the software shutdown is a soft power control: guarded
        g.n += 1
        r = B.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='main_power', position='OFF'), request_id=f'b{g.n}')
        self.assertTrue(r['result']['accepted'])                                   # the MAIN POWER key switch cuts the power: never refused,
        self.assertFalse(g.h.power)                                                # the acquisition fails as on a power loss
        self.assertEqual(g.A.poll(op)['status'], 'failed')

    def test_injected_fault_shows_the_manual_message(self):
        """Sweep 2026-10-19 (1.2.0): a fault of manual 3.14 injected by the evaluator shows its message with the Error ID at the foot of the
        screen; after the acknowledge the refused command shows the same text in a dialog; the remedy (here: service, fault.clear) frees it."""
        g = Rig(observe=True)
        g.call(g.E, 'fault.inject', code='stage_motor_failure')
        self.assertEqual(g.text('err_text'), 'Error: Error ID 0250: Stage stopped due to motor failure')
        self.assertTrue(g.tap('err_ack'))
        self.assertNotIn('err_text', g.widgets())
        x = g.val('stage.x_indicated_m')
        self.assertFalse(g.tap('nv_left'))                                         # the X/Y motors are inoperable
        self.assertEqual(g.dialog(), 'err_stage_motor_failure')
        self.assertEqual(g.val('stage.x_indicated_m'), x)
        g.ok()
        h = Rig(evac=False)
        h.call(h.E, 'fault.inject', code='gun_chamber_open')
        self.assertFalse(h.press('button_evac'))                                   # Error ID 0331: EVAC refused
        self.assertEqual(h.dialog(), 'err_gun_chamber_open')
        self.assertEqual(h.val('vacuum.state'), 'vented')
        h.ok()
        h.call(h.E, 'fault.clear', code='gun_chamber_open')                        # the gun chamber closed
        self.assertTrue(h.press('button_evac'))

    def test_panel_quantify_of_another_agents_spectrum_is_refused(self):
        g = Rig(observe=True)
        g.h.register_principal('agent2', visibility='agent')
        B = g.h.session('agent2')
        g.tap('hi_analysis')
        g.setf('f_eds_w', 'Live time', '20')
        g.tap('ed_start')
        g.step(40.0)
        rid = g.h._last_spectrum()['spectrum_id']
        r = self.tap_as(g, B, 'ed_quant')                                          # B's Quantify acts on A's spectrum: the device refuses
        self.assertFalse(r['result']['accepted'])
        self.assertEqual(g.dialog(), 'err_access_denied')
        self.assertEqual(g.h.quants, {})
        g.ok()
        g.n += 1
        r = B.invoke(instance_id=INST, capability_id='eds.quantify', arguments=dict(spectrum_id=rid, elements=[]), request_id=f'b{g.n}')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')                      # same as the direct call
        self.assertTrue(g.tap('ed_quant'))                                         # the owner quantifies
        q = copy.deepcopy(g.h.quants[rid])
        self.assertFalse(self.tap_as(g, B, 'ed_quant')['result']['accepted'])
        g.ok()
        self.assertEqual(g.h.quants[rid], q)
        self.assertEqual(g.call(g.E, 'eds.result', spectrum_id=rid)['spectrum_id'], rid)   # the evaluator is not an agent

    def test_scan_combinations_set_the_live_noise_and_the_slow_repaint(self):
        g = Rig(observe=True)
        g.go('scan', tab='settings')
        g.setf('f_scan_w', 'RDC', '320 x 240  0.075 s')
        g.setf('f_scan_w', 'Frame integration', '1')
        self.assertEqual((g.op('settings.scan.rdc'), g.op('settings.scan.frames')), (1, 1))
        g.tap('w_scan_close')
        g.tap('fx_rdc_l')
        self.assertEqual(g.h._live_scan(), dict(mode='RDC', pixels='320x240', frame_time_s=0.075, frames=1))
        n_rdc = grey_noise(g)
        g.tap('fx_fast_l')                                                         # Fast 2: 640 x 480, 0.5 s
        n_fast1 = grey_noise(g)
        g.go('scan', tab='settings')
        g.setf('f_scan_w', 'Frame integration', '8')
        g.tap('w_scan_close')
        n_fast8 = grey_noise(g)
        g.tap('fx_slow_l')                                                         # Slow 2: 1280 x 960, 20 s, single frames
        g.step(25.0)
        n_slow = grey_noise(g)
        self.assertGreater(n_rdc, n_fast1)
        self.assertGreater(n_fast1, 2.0 * n_fast8)                                 # sqrt(8) less noise with 8 integrated frames
        self.assertGreater(n_fast8, n_slow)
        # Slow: a brightness change is repainted from the top over the 20 s frame
        before = g.h.panel_read()['host.main_image']
        g.tap('fx_manual_l')
        g.tap('mt_bright')
        g.tap('br_brightness_p', hold=1.2)
        self.assertAlmostEqual(g.val('optics.image_adjust')['brightness'], 0.6)
        self.assertEqual(g.h.panel_read()['host.main_image'], before)              # nothing repainted yet
        g.step(10.0)
        now = g.h.panel_read()['host.main_image']
        self.assertEqual(now[rt.THUMB_H // 2:], before[rt.THUMB_H // 2:])         # lower half: still the old frame
        self.assertGreater(mean(now[:rt.THUMB_H // 2]), mean(before[:rt.THUMB_H // 2]) + 15)
        g.step(10.5)
        self.assertGreater(mean(g.h.panel_read()['host.main_image'][rt.THUMB_H // 2:]), mean(before[rt.THUMB_H // 2:]) + 15)
        g.tap('fx_fast_l')                                                         # Fast refreshes at once
        m = mean(g.h.panel_read()['host.main_image'])
        g.tap('br_brightness_m', hold=1.2)
        self.assertLess(mean(g.h.panel_read()['host.main_image']), m - 15)

    def test_tilt_mag_and_dynamic_focus_act_on_a_tilted_specimen(self):
        g = Rig(observe=True)
        self.assertTrue(g.turn('knob_stage_z', -100))                             # 10 mm lower (WD 20 mm), then tilt 30 deg
        g.step(10.0)
        self.assertTrue(g.turn('knob_stage_tilt', 60))
        g.step(5.0)
        g.tap('fx_af_l')
        g.call(g.A, 'scan.set_magnification', magnification=3000.0)

        def res():
            return g.h._frame_signals(dwell=1e-5, nx=1280, ny=960)['eff_res']
        r0 = res()                                                                 # top and bottom of the field out of focus
        g.go('dynfocus')
        g.slide('w_dynfocus_slider', 30.0)
        self.assertEqual(g.op('settings.tilt_correction.dynamic_focus_deg'), 30.0)
        self.assertLess(res(), 0.8 * r0)                                           # Dynamic Focus = tilt: the whole field in focus
        g.slide('w_dynfocus_slider', 60.0)
        self.assertGreater(res(), r0)                                              # over-corrected
        g.slide('w_dynfocus_slider', 0.0)
        g.tap('w_dynfocus_close')
        self.assertEqual(res(), r0)
        y0 = g.h.stage_target['y']
        g.call(g.A, 'stage.click_center', u=0.5, v=0.25)
        dy0 = g.h.stage_target['y'] - y0
        g.step(5.0)
        g.go('tiltmag')
        g.slide('w_tiltmag_slider', 30.0)                                          # Tilt Mag = tilt: the Y scan shrinks by cos 30
        g.tap('w_tiltmag_close')
        y1 = g.h.stage_target['y']
        g.call(g.A, 'stage.click_center', u=0.5, v=0.25)
        self.assertAlmostEqual((g.h.stage_target['y'] - y1) / dy0, math.cos(math.radians(30.0)), places=6)
        self.assertAlmostEqual(abs(g.h.stage_target['y'] - y1), 0.25 * 0.75 * g.val('scan.field_width_m'), places=12)   # true to scale

    def test_holder_and_height_set_the_motor_stage_limit(self):
        g = Rig(observe=True)
        g.turn('knob_stage_z', -100)
        g.step(10.0)
        g.turn('knob_stage_tilt', 60)                                              # 30 deg: the Y travel raises the specimen by y sin 30
        g.step(5.0)

        def holder_px():
            return sum(1 for r in g.h.panel_read()['host.holder_graphic'] for v in r if v in (110, 170))
        n32 = holder_px()
        g.tap('hold_area')
        g.setf('f_specimen_w', 'Height (protrusion)', '10')                       # 10 mm entered, the specimen is 5 mm high
        self.assertEqual(g.op('specimen.height_m'), 0.010)
        g.tap('w_holder_close')
        before = g.h.read('evaluator')
        r = g.call(g.A, 'stage.move_xy', expect='rejected', x_m=0.0, y_m=0.015)
        self.assertEqual(r['error']['code'], 'STAGE_COLLISION_RISK')               # the software limit (entered height) stops the motor
        g.tap('co_area')
        g.setf('f_coords_w', 'Y', '15')
        self.assertFalse(g.tap('co_exec'))
        self.assertEqual(g.dialog(), 'err_stage_collision_risk')
        g.ok()
        self.unchanged(self, before, g.h.read('evaluator'))
        g.tap('co_cancel')
        g.tap('hold_area')
        g.setf('f_specimen_w', 'Height (protrusion)', '5')                        # the true height: the move is inside the limit
        g.tap('w_holder_close')
        g.call(g.A, 'stage.move_xy', x_m=0.0, y_m=0.015)
        g.tap('hold_area')
        g.setf('f_specimen_w', 'Specimen holder', '10mm round')
        g.tap('w_holder_close')
        self.assertIn('10 mm round', g.text('hold_area'))
        self.assertLess(holder_px(), n32)                                          # the graphic shows the selected holder

    def test_language_selection_is_kept_and_texts_stay_english(self):
        g = Rig()
        g.go('language', tab='settings')
        g.tap('lg_Japanese')
        self.assertEqual(g.op('settings.environment.language'), 'Japanese')
        self.assertTrue(g.widgets()['lg_Japanese']['selected'])
        self.assertIn('Selected: Japanese', g.text('lg_note'))
        g.tap('w_language_close')
        self.assertEqual(g.text('fx_obs_l'), 'Observ.')                           # no invented translation


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig(observe=True)
            g.tap('hi_histo')
            return g
        a, b = scenario(), scenario()
        for did in rt.PANEL_SPEC['displays']:
            ia, ib = a.h.panel.render(did), b.h.panel.render(did)
            self.assertEqual(ia.dtype.name, 'uint8')
            self.assertTrue((ia == ib).all(), did)
        self.assertEqual(a.h.panel.png_bytes(D), b.h.panel.png_bytes(D))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png(D, OUT / 'home.png')}
        a.tap('mb_settings')
        shots['menu'] = a.h.panel.save_png(D, OUT / 'menu.png')
        a.tap('si_scan')
        shots['scan_settings'] = a.h.panel.save_png(D, OUT / 'scan_settings.png')
        a.tap('w_scan_close')
        a.tap('mb_home')
        a.tap('hi_gun')
        shots['electron_gun'] = a.h.panel.save_png(D, OUT / 'electron_gun.png')
        a.tap('w_gun_close')
        a.tap('fx_manual_l')
        shots['manual_tool'] = a.h.panel.save_png(D, OUT / 'manual_tool.png')
        a.tap('hi_pcd')
        shots['error'] = a.h.panel.save_png(D, OUT / 'error.png')
        a.ok()
        a.tap('ev_vent')
        a.ov('Yes')
        a.step(150.0)
        a.tap('fx_obs_l')
        shots['refusal'] = a.h.panel.save_png(D, OUT / 'refusal.png')
        self.assertEqual(a.dialog(), 'err_vacuum_not_ready')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=D)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('main_power', 'START')
        r2 = g.call(g.A, 'panel.capture', display_id=D)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])

    def test_copy_keeps_the_panel_bound(self):
        g = Rig(load=False, evac=False)
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        x, y, w, h = {e['id']: e for e in c.panel.frame()['displays'][D]['widgets']}['hi_blank']['rect']
        c.panel.touch(D, x + w // 2, y + h // 2)
        self.assertTrue(c.blanked)
        self.assertFalse(g.h.blanked)

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.turn', 'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        c = json.loads((rt.ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        m = rt.export_jeol_jsmit200_contract_metadata()
        for k in list(rt.PANEL_CAPS) + ['beam.blank', 'gun.set', 'recipe.execute', 'settings.configure', 'stage.click_center', 'snapshot.reproduce']:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/jeol_jsmit200.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('sem_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
