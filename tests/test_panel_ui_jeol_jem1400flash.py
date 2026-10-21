"""front-panel tests for CHEM-152 jeol_jem1400flash (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/jeol_jem1400flash.py):
python3 -m pytest tests/test_panel_ui_jeol_jem1400flash.py -q

Every gesture goes through the session API (panel.press / panel.turn / panel.switch / panel.touch, the runtime's normal invoke path); TEM Center
widgets are found by id in the structured frame (panel.frame, evaluator) and clicked at their centre.  The holder is handled by the agent's robot
skills (holder.load_grid / insert_to_airlock / insert / withdraw), the specimen and the plant by the evaluator.  Themes: the seven key procedures of
the menu survey; the device functions behind the keys and windows (beam blank, sleeping OLED keys, fast / fine knobs, deflectors and the alignment
panel with injected alignment errors, wobblers, trackball, stage panel, go here, data filing, error acknowledge); panel = direct capability (same
runtime state); refusals shown as TEM Center messages with the state unchanged; render determinism and the evidence PNGs; contract entries."""
import copy
import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import jeol_jem1400flash as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'jem1400flash_01'
OUT = ROOT / 'outputs/panel_ui/jeol_jem1400flash'
TC = 'tem_center'
SURVEY_TASKS = ['switch on HT and check vacuum', 'turn on the filament', 'low-mag overview image', 'switch LOW MAG to MAG', 'eucentric height and focus',
                'set spot size 1', 'remove the holder']
DONE_TASKS = set()
AU = rt.SPECIMEN_EXAMPLE


class Rig:
    """A runtime with an agent and an evaluator session; panel gestures through panel.* capabilities, holder handling through robot skills."""

    def __init__(self, on=True, holder=True, beam=True, seed=1):
        self.h = rt.JeolJem1400flashRuntime(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed))
        self.call(self.E, 'specimen.provide', **AU)
        self.call(self.A, 'holder.load_grid', holder_id='quick_change', specimen_id=AU['specimen_id'])
        if not on:
            return
        assert self.press('main_power')
        self.step(260.0)
        assert self.page() == ('holder', 'select')
        self.tap('hp_ok')
        if holder:
            self.insert_holder()
        if beam:
            self.ht_on()
            self.beam_on()

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

    def disp(self, did=TC):
        return self.frame()['displays'][did]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TC, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid, dx=0.5, dy=0.5):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + int(w * dx), y + int(h * dy))

    def dbl(self, wid):
        self.tap(wid)
        return self.tap(wid)

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

    def row(self, wid, first):
        w = self.widgets()[wid]
        firsts = [c[0] for c in w['cells']]
        k = firsts.index(first)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def type(self, text):
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        if 'q' in labels or 'Q' in labels:
            self.ov('Del')
            if self.overlay()['field']:
                for _ in range(len(self.overlay()['field'])):
                    self.ov('Del')
            for ch in text:
                mode = self.overlay()['mode']
                if ch.isdigit() or ch in '/_':
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

    def lines(self, did):
        return [x.rstrip() for x in self.disp(did)['lines']]

    def legend(self, did):
        """The text of an OLED key window (two lines since 2026-10-21, Radboud p.2 Fig. 2) as one string."""
        return ' '.join(x.strip() for x in self.disp(did)['lines'] if x.strip())

    def lit_white(self, did):
        """An active OLED key lights white: the window is drawn inverted (display option invert_if)."""
        return bool(self.disp(did).get('inverted'))

    def led(self, did):
        return self.disp(did)['on']

    # ---- procedures
    def insert_holder(self):
        self.call(self.A, 'holder.insert_to_airlock', holder_id='quick_change')
        self.step(12.0)
        assert self.switch('airlock_lever', 'PUMP')
        assert self.page() == ('holder', 'select')                               # the holder prompt while the airlock pumps
        self.tap('hp_ok')
        self.step(60.0)
        self.call(self.A, 'holder.insert')
        self.step(5.0)

    def ht_on(self):
        assert self.dbl('tb_ht')
        self.step(125.0)

    def beam_on(self):
        assert self.dbl('tb_beam')
        self.step(25.0)

    def to_mag(self, target):
        self.call(self.E, 'panel.frame')
        mag = self.val('optics.magnification')
        steps = rt.MAG_STEPS if self.val('optics.mode') == 'MAG' else rt.LOW_MAG_STEPS
        self.turn('knob_magnification', steps.index(target) - steps.index(mag))
        assert self.val('optics.magnification') == target


class SurveyProcedures(unittest.TestCase):
    def test_switch_on_ht_and_check_vacuum(self):
        g = Rig(on=False)
        self.assertEqual(g.page(), ('off', None))
        self.assertFalse(g.press('key_beam_blank'))                             # keys do nothing while the power is off
        self.assertTrue(g.press('main_power'))
        self.assertEqual(g.page(), ('booting', 'loading'))
        self.assertTrue(g.led('power_lamp'))
        g.step(260.0)
        self.assertEqual(g.page(), ('holder', 'select'))                        # holder selection at start-up (user guide p.3)
        g.tap('hp_ok')
        self.assertEqual(g.page(), ('main', 'home'))
        vac = g.lines('vacuum_monitor')
        self.assertEqual(vac[1].split()[-2:], ['Evac', 'Ready'])                 # gun
        self.assertEqual(vac[2].split()[-2:], ['Evac', 'Ready'])                 # column
        self.assertEqual(vac[3].split()[-2:], ['Not', 'Ready'])                  # specimen chamber (no holder) - as the guide expects
        self.assertTrue(g.tap('tb_ht'))                                          # one click arms, the second within 0.5 s switches
        self.assertFalse(g.val('ht.on'))
        g.step(1.0)
        self.assertTrue(g.tap('tb_ht'))
        self.assertFalse(g.val('ht.on'))                                         # too late: a new first click
        self.assertTrue(g.tap('tb_ht'))
        self.assertTrue(g.val('ht.on'))
        self.assertEqual(g.text('tb_ht'), 'HT on')
        g.step(125.0)
        self.assertEqual(g.text('tb_kv'), '120.0 kV')
        DONE_TASKS.add('switch on HT and check vacuum')

    def test_turn_on_the_filament(self):
        g = Rig(beam=False)
        self.assertEqual(g.lines('vacuum_monitor')[3].split()[-2:], ['Evac', 'Ready'])
        self.assertFalse(g.widgets()['tb_beam']['enabled'])                     # Beam Not Ready while HT is off
        self.assertFalse(g.dbl('tb_beam'))
        g.ht_on()
        self.assertEqual(g.text('tb_beam'), 'Beam Ready')
        self.assertTrue(g.dbl('tb_beam'))
        self.assertTrue(g.val('filament.on'))
        self.assertEqual(g.text('tb_beam'), 'Beam heating')
        g.step(25.0)
        self.assertEqual(g.text('tb_beam'), 'Beam ON')
        self.assertTrue(g.led('beam_lamp'))
        self.assertGreater(g.val('gun.beam_current_indicated_a'), g.val('gun.dark_current_indicated_a'))
        DONE_TASKS.add('turn on the filament')

    def test_low_mag_overview_image(self):
        g = Rig()
        self.assertTrue(g.press('button_low_mag'))
        self.assertEqual(g.val('optics.mode'), 'LOW_MAG')
        self.assertTrue(g.led('lamp_button_low_mag'))
        self.assertTrue(g.turn('ol_aperture_dial', -4))                          # objective aperture out
        self.assertEqual(g.val('aperture.objective_position'), 0)
        g.to_mag(200)
        self.assertEqual(g.text('tb_mag'), 'x200')
        b0 = g.val('optics.brightness')
        g.turn('knob_brightness', 10)                                           # clockwise spreads the beam
        self.assertGreater(g.val('optics.brightness'), b0)
        self.assertTrue(g.press('button_screen_up'))
        g.step(1.0)
        self.assertEqual(g.text('cam_state'), 'Camera: live')
        self.assertTrue(g.tap('cam_single'))
        g.step(2.0)
        imgs = g.call(g.A, 'records.list')['images']
        self.assertEqual(len(imgs), 1)
        self.assertEqual(g.call(g.A, 'image.result', image_id=imgs[0])['mode'], 'LOW_MAG')
        self.assertEqual(g.val('tem_center.state')['files'][-1]['image_id'], imgs[0])
        self.assertIn('Last image img-1', g.text('img_title'))
        DONE_TASKS.add('low-mag overview image')

    def test_switch_low_mag_to_mag(self):
        g = Rig()
        g.press('button_low_mag')
        g.press('button_screen_up')
        g.step(1.0)
        self.assertTrue(g.press('button_screen_up'))                            # screen down: camera off
        g.step(1.0)
        self.assertFalse(g.val('screen.up'))
        g.turn('ol_aperture_dial', 2)
        self.assertEqual(g.val('aperture.objective_position'), 2)
        self.assertTrue(g.press('button_mag1'))
        self.assertEqual(g.val('optics.mode'), 'MAG')
        self.assertTrue(g.led('lamp_button_mag1'))
        self.assertFalse(g.led('lamp_button_low_mag'))
        DONE_TASKS.add('switch LOW MAG to MAG')

    def test_eucentric_height_and_focus(self):
        g = Rig(seed=3)
        g.to_mag(20000)
        g.call(g.A, 'optics.set_defocus', defocus_m=2e-6)
        self.assertTrue(g.press('button_standard_focus'))
        self.assertEqual(g.val('optics.defocus_display_m'), 0.0)
        self.assertTrue(g.press('key_img_wob'))
        self.assertTrue(g.press('key_z_focus'))
        self.assertTrue(g.led('lamp_key_img_wob') and g.led('lamp_key_z_focus'))
        h = g.val('truth.specimen_height_m')
        self.assertIn('Wobbler: image moves', g.text('wob_text'))
        w0 = g.h._wobble_m()
        self.assertAlmostEqual(w0, 2 * rt.WOBBLER_TILT_RAD * abs(h), places=12)
        g.turn('knob_focus', -int(round(h / 1e-6)))                              # fast: 1 um per detent of stage Z
        g.step(30.0)
        g.press('push_focus')                                                    # fine: 0.1 um
        self.assertFalse(g.led('speed_knob_focus'))
        g.turn('knob_focus', -int(round(g.val('truth.specimen_height_m') / 1e-7)))
        g.step(10.0)
        self.assertLess(g.h._wobble_m(), w0)
        self.assertLess(abs(g.val('truth.specimen_height_m')), 0.06e-6)
        g.press('key_img_wob')
        g.press('key_z_focus')
        self.assertEqual(g.val('optics.wobbler'), 'off')
        self.assertEqual(g.text('wob_text'), '')
        d0 = g.val('optics.defocus_display_m')
        g.turn('knob_focus', 4)                                                  # the knob focuses again (5 nm fine)
        self.assertAlmostEqual(g.val('optics.defocus_display_m') - d0, 20e-9, places=15)
        DONE_TASKS.add('eucentric height and focus')

    def test_set_spot_size_1(self):
        g = Rig()
        self.assertEqual(g.legend('oled_key_spot_size'), 'Spot Size')
        self.assertTrue(g.lit_white('oled_key_spot_size'))                       # the multifunction key reads Spot Size (beginner)
        g.turn('knob_multifunction_x', 4)
        self.assertEqual(g.val('optics.spot_size'), 5)
        self.assertEqual(g.text('tb_spot'), 'Spot size 5')
        g.turn('knob_multifunction_x', -9)
        self.assertEqual(g.val('optics.spot_size'), 1)
        DONE_TASKS.add('set spot size 1')

    def test_remove_the_holder(self):
        g = Rig()
        g.to_mag(10000)
        g.press('button_screen_up')
        g.call(g.A, 'stage.move_xy', x_m=1e-5, y_m=0.0)
        g.step(2.0)
        beeps = g.frame()['ui']['beeps']
        self.assertTrue(g.press('button_exchange_holder'))                      # one press only arms the key
        self.assertTrue(g.val('filament.on'))
        self.assertTrue(g.press('button_exchange_holder'))                      # double press: the exchange sequence
        self.assertFalse(g.val('filament.on'))
        self.assertFalse(g.val('screen.up'))
        self.assertEqual(g.frame()['ui']['beeps'], beeps + 1)
        self.assertTrue(g.lit_white('oled_button_exchange_holder'))             # the key turns white for a few seconds
        self.assertEqual(g.lines('vacuum_monitor')[-1], 'OK to exchange holder')
        g.step(30.0)
        self.assertLess(abs(g.val('stage.x_indicated_m')), 1e-7)
        self.assertFalse(g.lit_white('oled_button_exchange_holder'))
        g.call(g.A, 'holder.withdraw')
        g.step(3.0)
        self.assertTrue(g.switch('airlock_lever', 'AIR'))
        g.step(10.0)
        self.assertEqual(g.val('airlock.state'), 'vented')
        self.assertFalse(g.led('airlock_lamp'))
        DONE_TASKS.add('remove the holder')


class DeviceFunctions(unittest.TestCase):
    def test_beam_blank(self):
        g = Rig()
        i0 = g.val('truth.specimen_current_a')
        self.assertGreater(i0, 0.0)
        self.assertTrue(g.press('key_beam_blank'))
        self.assertTrue(g.val('beam.blanked'))
        self.assertEqual(g.val('truth.specimen_current_a'), 0.0)
        self.assertGreater(g.val('gun.beam_current_indicated_a'), 6e-5)          # the emission goes on (the beam current reading is unchanged)
        self.assertTrue(g.lit_white('oled_key_beam_blank'))
        self.assertEqual(g.text('tb_blank'), 'BEAM BLANKED')
        g.press('key_beam_blank')
        self.assertEqual(g.val('truth.specimen_current_a'), i0)

    def test_sleeping_keys_wake_first(self):
        g = Rig(beam=False)
        g.step(1801.0)
        self.assertEqual(g.legend('oled_key_beam_blank'), '')                    # no text: sleep mode (UniDue p.9)
        self.assertTrue(g.press('key_beam_blank'))                              # the first press only wakes the keys
        self.assertFalse(g.val('beam.blanked'))
        self.assertEqual(g.legend('oled_key_beam_blank'), 'Beam Blank')
        g.press('key_beam_blank')
        self.assertTrue(g.val('beam.blanked'))
        g.step(1801.0)
        self.assertTrue(g.turn('knob_brightness', 1))                           # a knob still turns and wakes the keys
        self.assertEqual(g.legend('oled_key_beam_blank'), 'Beam Blank')

    def test_fast_fine_knobs(self):
        g = Rig()
        self.assertTrue(g.led('speed_knob_brightness'))
        b = g.val('optics.brightness')
        g.turn('knob_brightness', 3)
        self.assertAlmostEqual(g.val('optics.brightness') - b, 0.03, places=9)
        self.assertTrue(g.press('knob_brightness'))                              # pressing the knob: fine (indicator off)
        self.assertFalse(g.led('speed_knob_brightness'))
        b = g.val('optics.brightness')
        g.turn('knob_brightness', 3)
        self.assertAlmostEqual(g.val('optics.brightness') - b, 0.003, places=9)

    def test_alignment_panel_corrects_injected_errors(self):
        g = Rig()
        g.call(g.E, 'alignment.configure_error', target='cl_shift', x=0.3, y=0.0)
        g.call(g.E, 'alignment.configure_error', target='gun_shift', x=0.0, y=0.2)
        self.assertAlmostEqual(g.val('truth.illumination_offset_m')[0], -0.3 * rt.CL_SHIFT_RANGE_M, places=15)
        i_bad = g.val('truth.specimen_current_a')
        g.tap('home_align')
        self.assertEqual(g.page(), ('main', 'alignment'))
        g.tap('al_bright')                                                       # Bright Tilt: the SHIFT knobs move the condenser shift
        self.assertTrue(g.widgets()['al_bright']['selected'])
        g.turn('knob_shift_x', 15)                                               # fast 0.02 per detent
        self.assertAlmostEqual(g.val('truth.illumination_offset_m')[0], 0.0, places=15)
        g.tap('al_gun')                                                          # Gun Align: SHIFT knobs = gun shift
        g.turn('knob_shift_y', 10)
        self.assertAlmostEqual(g.val('optics.deflectors')['gun_shift'][1], 0.2, places=12)
        self.assertAlmostEqual(g.val('truth.specimen_current_a') / i_bad, math.exp(0.2 ** 2 / rt.GUN_ALIGN_WIDTH ** 2), places=9)
        g.tap('al_gun')
        self.assertEqual(g.text('al_assign'), 'SHIFT knobs: beam shift   MULTI FUNCTION X: spot size')
        g.turn('knob_shift_x', 5)                                                # beam shift moves the beam off the centre again
        self.assertAlmostEqual(g.val('truth.illumination_offset_m')[0], 0.1 * rt.BEAM_SHIFT_RANGE_M, places=15)
        g.tap('al_load')
        g.ov('OK')                                                               # Load: every deflector back to the file value (0)
        self.assertEqual(g.val('optics.deflectors')['beam_shift'], [0.0, 0.0])
        self.assertAlmostEqual(g.val('truth.illumination_offset_m')[0], -0.3 * rt.CL_SHIFT_RANGE_M, places=15)

    def test_condenser_stigmator_rounds_the_beam(self):
        """Emory JEM-1400 usage step 19: if the beam is not round, Cond Stig assigns the MULTI FUNCTION knobs to the condenser stigmator; the
        BRIGHTNESS knob takes the beam through the crossover and the knobs make it round (1.1.1).  The agent sees the beam in camera images."""
        def shape(g):
            g.tap('cam_single')
            g.step(3.0)
            rid = g.A.read(instance_id=INST, state_ids=['result.last_image_id'])['observations']['result.last_image_id']['value']
            th = np.array(g.call(g.A, 'image.result', image_id=rid)['thumbnail'], float)
            ys, xs = np.nonzero(th > 0.5 * th.max())                            # the lit area of the condensed beam
            ev, vec = np.linalg.eigh(np.cov(np.vstack([xs, ys])))
            return math.sqrt(ev[1] / ev[0]), math.degrees(math.atan2(vec[1, 1], vec[0, 1])) % 180.0

        g = Rig()
        g.call(g.E, 'alignment.configure_error', target='cond_stig', x=0.12, y=-0.16)
        d1, d2, phi = g.h._illum_axes()
        self.assertLess(abs(d1 / d2 - 1.0), 0.02)                               # spread beam (BRIGHTNESS 0.8): nearly round
        self.assertAlmostEqual(math.degrees(phi), 63.43, places=2)               # axis of the astigmatism (-error: -0.12, +0.16)
        g.to_mag(10000)
        g.press('button_screen_up')
        g.step(1.0)
        g.turn('knob_brightness', -30)                                          # fast: to the crossover region (0.5)
        g.press('knob_brightness')                                               # fine 0.001 per detent
        g.turn('knob_brightness', 2)
        r1, a1 = shape(g)                                                        # one line focus: a line ~6 x longer than wide
        g.turn('knob_brightness', -4)
        r2, a2 = shape(g)                                                        # the other line focus: turned by 90 deg
        self.assertGreater(min(r1, r2), 4.0)
        self.assertAlmostEqual(abs(a1 - a2), 90.0, delta=3.0)
        self.assertAlmostEqual(a2, 63.43, delta=3.0)
        g.turn('knob_brightness', 4)
        g.tap('home_align')
        g.tap('al_stig')
        self.assertEqual(g.text('al_assign'), 'MULTI FUNCTION knobs: condenser stigmator')
        g.turn('knob_multifunction_x', 6)                                        # fast 0.02 per detent
        g.turn('knob_multifunction_y', -8)
        self.assertEqual(g.val('optics.deflectors')['cond_stig'], g.val('truth.alignment_error')['cond_stig'])
        d1, d2, _ = g.h._illum_axes()
        self.assertAlmostEqual(d1, d2, places=15)
        g.tap('h_alignm')                                                        # back to the camera window
        r3, _ = shape(g)
        self.assertLess(r3, 1.3)                                                 # round at the same BRIGHTNESS
        g2 = Rig()                                                               # no error: the stigmator at its file value keeps the beam round
        self.assertEqual(g2.h._illum_axes()[0], g2.h._illum_axes()[1])
        g2.call(g2.A, 'deflector.set', target='cond_stig', x=0.1, y=0.0)        # and a stigmator turned off its file value makes it elliptical
        self.assertNotEqual(g2.h._illum_axes()[0], g2.h._illum_axes()[1])

    def test_ht_wobbler_voltage_centre(self):
        g = Rig()
        g.call(g.E, 'alignment.configure_error', target='voltage_center', x=0.1, y=-0.1)
        g.tap('home_align')
        g.tap('al_htw')
        self.assertEqual(g.val('optics.wobbler'), 'ht')
        w0 = g.h._wobble_m()
        self.assertAlmostEqual(w0, rt.HT_WOBBLE_M * math.hypot(0.1, 0.1), places=15)
        g.turn('knob_multifunction_x', 5)
        g.turn('knob_multifunction_y', -5)
        self.assertAlmostEqual(g.h._wobble_m(), 0.0, places=15)
        g.tap('al_htw')
        self.assertEqual(g.val('optics.wobbler'), 'off')

    def test_stage_panel_trackball_and_go_here(self):
        g = Rig()
        g.to_mag(10000)
        fov = rt.CAMERA_PIXELS * rt.CAMERA_PIXEL_M / 10000
        g.turn('trackball_x', 20)
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_m'), 20 * fov / 200, places=12)
        g.press('button_screen_up')
        g.step(1.0)
        g.tap('cam_single')
        g.step(2.0)
        x0 = g.val('stage.x_m')
        g.tap('img_area', 0.75, 0.5)                                             # a point right of the centre, "go here"
        self.assertEqual(g.dialog(), 'go_here')
        g.ov('go here')
        g.step(5.0)
        self.assertAlmostEqual(g.val('stage.x_m') - x0, 0.25 * fov, delta=fov / 300)
        g.tap('home_stage')
        g.tap('st_tx_plus')
        g.step(2.0)
        self.assertAlmostEqual(g.val('stage.tilt_indicated_rad'), math.radians(1.0), places=6)
        g.tap('st_tx_neutral')
        g.tap('st_z_up')
        g.step(2.0)
        self.assertAlmostEqual(g.val('stage.z_indicated_m'), 1e-6, places=9)
        g.tap('st_neutral')
        g.step(30.0)
        self.assertEqual(g.text('st_x'), 'X 0.0 um')

    def test_data_filing_and_new_project(self):
        g = Rig()
        g.tap('home_project')
        g.ov('Project name')
        g.type('run1')
        g.ov('OK')
        g.ov('OK')
        self.assertEqual(g.val('tem_center.state')['project'], 'run1')
        g.tap('home_filing')
        g.row('f_filing_w', 'Free Text')
        g.type('grid')
        g.ov('OK')
        g.row('f_filing_w', 'File format')
        g.ov('Tiff (Raw) + Jpeg(High)')
        self.assertEqual(g.text('fi_example'), 'Example: grid_0001_30000x.tif')
        g.tap('h_data_f')
        g.press('button_screen_up')
        g.step(1.0)
        g.tap('cam_single')
        g.step(2.0)
        f = g.val('tem_center.state')['files'][-1]
        self.assertEqual((f['folder'], f['names']), ('F//run1', ['grid_0001_30000x.tif', 'grid_0001_30000x.jpg']))

    def test_error_banner_and_acknowledge(self):
        g = Rig()
        g.call(g.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, cooling_water_m3_s=1.0 / 60000.0))
        g.step(2.0)
        self.assertEqual(g.val('system.error_code'), 'COOLING_WATER_LOW')
        self.assertEqual(g.text('err_text'), 'Error: cooling water low')
        self.assertFalse(g.press('button_low_mag'))                              # refused while an error is pending
        self.assertEqual(g.dialog(), 'err_error_pending')
        g.ov('OK')
        g.tap('err_ack')
        self.assertIsNone(g.val('system.error_code'))
        self.assertNotIn('err_text', g.widgets())


class RefusalsAndEquivalence(unittest.TestCase):
    def test_refusals_show_messages_and_change_nothing(self):
        g = Rig()
        before = g.val('acquisition.id'), g.call(g.A, 'records.list')
        self.assertFalse(g.tap('cam_single'))                                    # screen down
        self.assertEqual(g.dialog(), 'err_screen_down')
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'SCREEN_DOWN')
        g.ov('OK')
        self.assertEqual((g.val('acquisition.id'), g.call(g.A, 'records.list')), before)
        g.tap('home_stage')
        for _ in range(21):
            g.tap('st_tx_plus')
            g.step(1.0)
        self.assertEqual(g.dialog(), 'err_tilt_out_of_range')                    # the quick-change holder stops at 20 deg
        self.assertAlmostEqual(g.val('stage.tilt_indicated_rad'), math.radians(20.0), places=6)
        g.ov('OK')
        g.press('button_low_mag')
        self.assertFalse(g.press('key_auto_focus'))
        self.assertIn(g.dialog(), ('err_screen_down', 'err_not_in_mag_mode'))
        g.ov('OK')
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='no_such_key')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.touch', expect='rejected', display_id=TC, x=1, y=1, to_x=3)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_booting_refuses_and_unassigned_knob(self):
        g = Rig(on=False)
        g.press('main_power')
        g.step(10.0)
        self.assertFalse(g.press('button_low_mag'))
        self.assertEqual(g.dialog(), 'err_not_ready')
        g.step(260.0)
        g.tap('hp_ok')
        self.assertFalse(g.turn('knob_multifunction_y', 3))                     # nothing assigned in the beginner configuration
        self.assertEqual(g.val('optics.deflectors')['cond_stig'], [0.0, 0.0])

    def test_panel_equals_direct(self):
        def script(direct):
            g = Rig(beam=False)
            if direct:
                g.call(g.A, 'ht.set_on', on=True)
                g.step(125.0)
                g.call(g.A, 'filament.set_on', on=True)
                g.step(25.0)
                g.call(g.A, 'optics.set_mode', mode='LOW_MAG')
                g.call(g.A, 'optics.step_magnification', steps=-3)
                g.call(g.A, 'optics.set_brightness', value=0.85)
                g.call(g.A, 'beam.blank', on=True)
                g.call(g.A, 'deflector.set', target='beam_shift', x=0.04, y=0.0)
                g.call(g.A, 'screen.toggle')
            else:
                g.dbl('tb_ht')
                g.step(125.0)
                g.dbl('tb_beam')
                g.step(25.0)
                g.press('button_low_mag')
                g.turn('knob_magnification', -3)
                g.turn('knob_brightness', 5)
                g.press('key_beam_blank')
                g.turn('knob_shift_x', 2)
                g.press('button_screen_up')
            g.step(3.0)
            return g.h.read('evaluator')
        a, b = script(False), script(True)
        for k in a:
            self.assertEqual(json.dumps(a[k], sort_keys=True, default=str), json.dumps(b[k], sort_keys=True, default=str), k)


class CallerRules(unittest.TestCase):
    def test_panel_started_acquisition_belongs_to_the_caller(self):
        """A Single acquisition started from TEM Center belongs to the agent that clicked it (1.1.1): it polls and cancels it, its guards hold
        against a second agent on the panel and directly, and the image is its own record."""
        g = Rig()
        g.h.register_principal('agent2', visibility='agent')
        B = g.h.session('agent2')
        g.press('button_screen_up')
        g.step(1.0)
        self.assertTrue(g.tap('cam_single'))
        op = g.h.acq['operation_id']
        rid = g.h.acq['record_id']
        self.assertEqual((g.h._owners.get(op), g.h._owners.get(rid)), ('agent', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        before = g.h.read('evaluator')
        for cap, args in (('beam.blank', dict(on=True)), ('optics.set_spot_size', dict(spot=2))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        for key in ('key_beam_blank',):                                          # B's keys: the invoked command is refused, TEM Center says why
            self.assertFalse(g.call(B, 'panel.press', input_id=key, duration_s=0.1)['accepted'], key)
            self.assertEqual(g.dialog(), 'err_access_denied', key)
            self.assertEqual(g.overlay()['lines'], ['Another user is acquiring.'])
            g.ov('OK')
        for name, call in (('poll', lambda: B.poll(op)), ('cancel', lambda: B.cancel(op, 'b-cancel'))):
            with self.assertRaises(rt.ActionRejected, msg=name) as cm:
                call()
            self.assertEqual(str(cm.exception), 'ACCESS_DENIED')
        after = g.h.read('evaluator')
        self.assertEqual(json.dumps(before, sort_keys=True, default=str), json.dumps(after, sort_keys=True, default=str))
        self.assertTrue(B.read(instance_id=INST, state_ids=['acquisition.started_by_other_agent'])['observations']
                        ['acquisition.started_by_other_agent']['value'])
        g.step(3.0)
        self.assertEqual(g.A.poll(op)['status'], 'completed')
        last = lambda s: s.read(instance_id=INST, state_ids=['result.last_image_id'])['observations']['result.last_image_id']['value']  # noqa: E731
        self.assertEqual((last(g.A), last(B)), (rid, None))
        self.assertEqual(g.call(B, 'records.list')['images'], [])
        self.assertEqual(g.call(g.A, 'records.list')['images'], [rid])
        self.assertEqual(g.call(B, 'image.result', expect='rejected', image_id=rid)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(g.A, 'image.result', image_id=rid)['status'], 'completed')
        self.assertTrue(g.tap('cam_single'))                                     # A cancels its own panel-started acquisition
        op2 = g.h.acq['operation_id']
        self.assertEqual(g.A.cancel(op2, 'a-cancel')['result']['phase'], 'cancelled')
        self.assertIsNone(g.h.acq)
        self.assertEqual(g.h.images[g.h.operations[op2]['record_id']]['status'], 'cancelled')

    def test_navigation_and_hands_on_actions_are_free(self):
        """Sweep 2026-10-19: during another agent's acquisition, B may change TEM Center pages (only the commands a gesture invokes are guarded)
        and may act on the hardware by hand (aperture lever, airlock lever, MAIN POWER), also during the exposure; the device's own conditions still apply."""
        g = Rig()
        g.h.register_principal('agent2', visibility='agent')
        B = g.h.session('agent2')
        g.press('button_screen_up')
        g.step(1.0)
        self.assertTrue(g.tap('cam_single'))
        op = g.h.acq['operation_id']
        x, y, w, hh = g.widgets()['home_align']['rect']
        r = g.call(B, 'panel.touch', display_id=TC, x=x + w // 2, y=y + hh // 2)     # B opens the alignment page: navigation, not refused
        self.assertTrue(r['accepted'])
        self.assertIsNone(g.dialog())
        self.assertIn('h_alignm', g.widgets())
        for s, ins in ((B, False), (g.A, True)):                                 # the aperture lever is a hand action: nothing stops a hand
            self.assertEqual(g.call(s, 'aperture.set_condenser', inserted=ins)['inserted'], ins)   # during an exposure, for B as for the owner
        self.assertTrue(g.call(B, 'airlock.set_pump', pump=True)['pump'])        # the airlock PUMP/AIR lever
        self.assertEqual(g.A.poll(op)['status'], 'running')
        self.assertEqual(g.call(B, 'optics.set_spot_size', expect='rejected', spot=2)['error']['code'], 'ACCESS_DENIED')   # a command stays guarded
        self.assertTrue(g.call(B, 'panel.press', input_id='main_power', duration_s=0.1)['accepted'])   # MAIN POWER is a hard switch:
        self.assertFalse(g.val('power.enabled'))                                                      # never refused, the acquisition
        self.assertEqual(g.A.poll(op)['status'], 'failed')                                            # fails as on a power loss


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.press('button_screen_up')
            g.step(1.0)
            g.tap('cam_single')
            g.step(2.0)
            return g
        a, b = scenario(), scenario()
        for did in rt.PANEL_SPEC['displays']:
            ia, ib = a.h.panel.render(did), b.h.panel.render(did)
            self.assertEqual(ia.dtype.name, 'uint8')
            self.assertTrue((ia == ib).all(), did)
        self.assertEqual(a.h.panel.png_bytes(TC), b.h.panel.png_bytes(TC))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png(TC, OUT / 'home.png'), 'vacuum': a.h.panel.save_png('vacuum_monitor', OUT / 'vacuum.png'),
                 'oled_spot_size': a.h.panel.save_png('oled_key_spot_size', OUT / 'oled_spot_size.png')}
        a.tap('home_align')
        shots['menu'] = a.h.panel.save_png(TC, OUT / 'menu.png')
        a.tap('h_alignm')
        a.press('button_screen_up')
        a.tap('cam_single')
        shots['error'] = a.h.panel.save_png(TC, OUT / 'error.png')
        self.assertEqual(a.dialog(), 'err_screen_down')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=TC)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.press('main_power')
        r2 = g.call(g.A, 'panel.capture', display_id=TC)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])

    def test_copy_keeps_the_panel_bound(self):
        g = Rig(holder=False, beam=False)
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        c.panel.press('key_beam_blank')
        self.assertTrue(c.blanked)
        self.assertFalse(g.h.blanked)

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.turn', 'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        c = json.loads((rt.ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        m = rt.export_jeol_jem1400flash_contract_metadata()
        for k in list(rt.PANEL_CAPS) + ['beam.blank', 'deflector.set', 'alignment.load', 'optics.wobbler', 'stage.go_here', 'tem_center.data_filing']:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'parameter_cycle', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/jeol_jem1400flash.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('jem_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
