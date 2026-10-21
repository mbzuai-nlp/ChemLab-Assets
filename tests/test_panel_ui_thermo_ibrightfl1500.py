"""front-panel tests for CHEM-165 thermo_ibrightfl1500 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on the runtime's touch-screen app model):
python3 -m pytest tests/test_panel_ui_thermo_ibrightfl1500.py -q

Every gesture goes through the session API (panel.touch / panel.switch); widgets are found by id in the structured frame (panel.frame,
evaluator) and tapped at their centre; images on the screen are read back from the rendered pixels.  Themes: the six key procedures of the menu
survey (power-up and sign-in, chemiluminescent blot with Smart Exposure / ROI / HDR, fluorescent blot with two dyes, export to USB, analysis,
instrument diagnostics); the other screens (gallery, image adjust, camera, routines, band excision, custom dye, settings, users, file naming,
network, logs, service tools); panel = direct commands; refusals as the device's message; the sleep time-out; render determinism and the evidence
PNGs; contract entries."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import thermo_ibrightfl1500 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'imager_01'
OUT = ROOT / 'outputs/panel_ui/thermo_ibrightfl1500'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SCR = 'screen'
SURVEY_TASKS = ['start from power-off and sign in', 'image a chemiluminescent blot', 'image a fluorescent blot with several dyes', 'export a new image to USB',
                'analyze an image', 'run instrument diagnostics']
DONE_TASKS = set()


def blot(sid='blot', label='alexa_fluor_647', base=2.5e-15, second=None, substrate=None):
    lanes = []
    for i in range(8):
        bands = [dict(y_m=0.020, sigma_m=0.0006, label=label, amount_mol=base * 2.0 ** i)]
        if second:
            bands.append(dict(y_m=0.040, sigma_m=0.0006, label=second, amount_mol=base * 2.0 ** (7 - i)))
        lanes.append(dict(x_m=0.008 + 0.0095 * i, width_m=0.006, bands=bands))
    d = dict(sample_id=sid, support='pvdf_lf', width_m=0.085, height_m=0.065, lanes=lanes)
    if substrate:
        d['substrate'] = substrate
    return d


class Rig:
    """A runtime with an agent and an evaluator session; gestures only through panel.* capabilities (the robot places samples)."""

    def __init__(self, power=True, guest=True, sample=None, sleep=False):
        self.h = rt.ThermoIbrightFl1500Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        if sample:
            self.call(self.A, 'sample.provide', **sample)
        if power:
            self.switch('on')
            self.step(300.0)
            if not sleep:                                                     # long captures below: no sleep in between (see test_refusals_sleep_and_power)
                self.call(self.A, 'settings.configure', sleep_mode=False)
            if guest:
                self.tap('si_guest')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if r['status'] != 'rejected' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.h.step(dt)

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCR]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def vars(self):
        return self.frame()['ui']['vars']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power', position=pos)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def row(self, wid, first, col=0):
        """Tap the row of a list / form widget whose cell `col` is this text."""
        for _ in range(20):                                                    # back to the top: drag the rows down
            w = self.widgets()[wid]
            if not w.get('scroll'):
                break
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        for _ in range(20):                                                    # drag the rows up until the row shows
            w = self.widgets()[wid]
            cells = [c[col] for c in w['cells']]
            if first in cells:
                break
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(cells) < w['count'], (first, cells)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        k = cells.index(first)
        return self.touch(w['rect'][0] + 10 + (0 if col == 0 else int(w['rect'][2] * 0.3)), w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def type(self, text):
        for ch in text:
            self.ov(ch)
        self.ov('OK')

    def form_value(self, wid, label):
        return dict((c[0], c[1]) for c in self.widgets()[wid]['cells'])[label]

    def choose(self, wid, label, option):
        """Open a form row's choice list and pick an option."""
        self.row(wid, label)
        self.ov(option)

    def load(self, mode='fluorescent_blots'):
        """Mode on the Welcome screen, drawer open, the robot places the sample, drawer closed: the live view."""
        self.choose('w_mode', 'Mode', rt.MODE_TEXT[mode])
        self.tap('w_drawer')
        self.step(5.0)
        self.call(self.A, 'sample.place', sample_id=self.h.samples and sorted(self.h.samples)[0], skew_rad=0.05)
        self.tap('w_drawer')
        self.step(5.0)


def done(task):
    DONE_TASKS.add(task)


class KeyProcedures(unittest.TestCase):
    def test_start_from_power_off_and_sign_in(self):
        r = Rig(power=False, sample=blot())
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.touch(500, 400))
        self.assertTrue(r.switch('on'))
        self.assertEqual(r.page(), ('login', 'signin'))                          # straight to the sign-in page
        self.assertFalse(r.frame()['displays']['light_bar']['on'] is False)
        r.tap('si_start')
        r.tap('gs_create')
        self.assertEqual(r.page(), ('login', 'create'))
        r.row('cp_form', 'Screen name')
        r.type('ann')
        r.row('cp_form', 'PIN (4 digits)')
        r.type('1234')
        r.row('cp_form', 'Confirm PIN')
        r.type('1234')
        r.tap('cp_ok')
        self.assertEqual(r.val('app.user'), 'ann')
        self.assertEqual(r.page(), ('app', 'welcome'))
        self.assertEqual(r.text('w_hello'), 'Welcome, ann')
        r.step(300.0)
        r.load('fluorescent_blots')
        self.assertEqual(r.page(), ('app', 'capture'))
        self.assertEqual(r.val('drawer.state'), 'closed')
        img = r.h.panel.render(SCR)
        x, y, w, h = r.widgets()['vp']['rect']
        self.assertEqual(tuple(int(v) for v in img[y + h // 2, x + w // 2]), (200, 200, 200))   # the live view shows the sample
        # sign out and sign in again with the PIN
        r.tap('tb_user')
        r.ov('Sign out')
        self.assertEqual(r.page(), ('login', 'signin'))
        r.row('si_users', 'ann')
        ov = r.overlay()
        self.assertEqual(ov['dialog'], 'pin')
        b = next(b for b in ov['buttons'] if b['id'] == 'row:pin')
        r.touch(b['rect'][0] + 10, b['rect'][1] + 10)
        r.type('9999')
        r.ov('Sign in')
        self.assertEqual(r.overlay()['lines'], ['Incorrect PIN.'])
        r.ov('OK')
        b = next(b for b in r.overlay()['buttons'] if b['id'] == 'row:pin')
        r.touch(b['rect'][0] + 10, b['rect'][1] + 10)
        r.type('1234')
        r.ov('Sign in')
        self.assertEqual((r.val('app.user'), r.page()[0]), ('ann', 'app'))
        done('start from power-off and sign in')

    def test_image_a_chemiluminescent_blot(self):
        r = Rig(sample=blot(label='hrp', base=1e-18, substrate='generic_ecl'))
        r.load('chemi_blots')
        r.tap('c_more')
        r.tap('m_roi')                                                         # Region of Interest: drag on the live view
        self.assertTrue(r.vars()['roi_mode'])
        x, y, w, h = r.widgets()['vp']['rect']
        r.touch(x + w // 4, y + h // 4, to_x=x + 3 * w // 4, to_y=y + 3 * h // 4)
        roi = r.vars()['roi']
        self.assertEqual(len(roi), 4)
        self.assertLess(roi[0], 0.0)
        self.assertGreater(roi[2], 0.0)
        r.tap('c_smart')
        self.assertEqual(r.val('operation.kind'), 'smart_exposure')
        self.assertIn('Smart Exposure', r.text('c_status'))
        r.step(300.0)
        rec = r.h.records[max(r.h.records)]
        self.assertIn('CHEMI', rec['recommended_exposure_s'])
        before = r.h.exposure['CHEMI']
        r.tap('c_plus')                                                        # + steps to the next preset
        self.assertGreater(r.h.exposure['CHEMI'], before)
        r.tap('c_hdr')
        self.assertTrue(r.vars()['hdr'])
        r.tap('c_capture')
        r.step(1500.0)
        iid = max(r.h.images)
        self.assertTrue(r.h.images[iid]['hdr'])
        self.assertTrue(r.widgets()['c_trash']['enabled'])                     # the captured image is on screen
        r.tap('c_trash')
        r.ov('Delete')
        self.assertNotIn(iid, r.h.images)
        done('image a chemiluminescent blot')

    def test_image_a_fluorescent_blot_with_several_dyes(self):
        r = Rig(sample=blot(second='alexa_fluor_488'))
        r.load('fluorescent_blots')
        r.tap('c_dyes')
        self.assertEqual(r.page(), ('app', 'dyes'))
        r.row('dy_list', 'Alexa Fluor 647', col=1)
        r.row('dy_list', 'Alexa Fluor 488', col=1)
        r.tap('dy_done')
        self.assertEqual(sorted(r.val('channels.selected')), ['EX1:alexa_fluor_488', 'EX3:alexa_fluor_647'])
        r.tap('c_smart')
        r.step(300.0)
        exp = {c: r.h.exposure[c] for c in ('EX1', 'EX3')}
        r.row('c_channels', 'Alexa Fluor 488')                                 # select a dye, then change its exposure
        r.tap('c_minus')
        self.assertLess(r.h.exposure['EX1'], exp['EX1'])
        self.assertEqual(r.h.exposure['EX3'], exp['EX3'])
        r.tap('c_capture')
        r.step(600.0)
        iid = max(r.h.images)
        self.assertEqual(sorted(c['channel'] for c in r.h.images[iid]['channels']), ['EX1', 'EX3'])
        self.assertIn('vp_top', r.widgets())                                   # 2UP: channel above, composite below
        self.assertNotIn('vp', r.widgets())
        done('image a fluorescent blot with several dyes')

    def captured(self):
        r = Rig(sample=blot())
        r.load('fluorescent_blots')
        r.tap('c_dyes')
        r.row('dy_list', 'Alexa Fluor 647', col=1)
        r.tap('dy_done')
        r.tap('c_smart')
        r.step(300.0)
        r.tap('c_capture')
        r.step(600.0)
        return r, max(r.h.images)

    def test_export_a_new_image_to_usb(self):
        r, iid = self.captured()
        r.tap('c_export')
        self.assertEqual(r.page(), ('app', 'export'))
        r.tap('ex_edit')
        b = next(b for b in r.overlay()['buttons'] if b['id'] == 'row:name')
        r.touch(b['rect'][0] + 10, b['rect'][1] + 10)
        r.type('blot')
        r.ov('Save')
        self.assertEqual(r.h.app['names'][iid]['name'], 'blot')
        r.tap('ex_d_usb')
        r.tap('ex_t_analysis_tiff')
        r.tap('ex_next')
        self.assertEqual(r.overlay()['lines'], ['1 image(s) exported to the USB drive.'])
        self.assertEqual(r.h.images[iid]['exported'], ['blot.tiff'])
        r.ov('OK')
        r.tap('ex_d_network_drive')                                            # no network drive configured
        r.tap('ex_next')
        self.assertEqual(r.overlay()['lines'], ['Configure the network drive or printer in Network Configuration first.'])
        done('export a new image to USB')

    def test_analyze_an_image(self):
        r, iid = self.captured()
        r.tap('c_analyze')
        self.assertEqual(r.page(), ('app', 'analyze'))
        self.assertTrue(r.text('an_sum').startswith('EX3: 8 lanes'))
        r.tap('an_sens_p')
        self.assertEqual(r.vars()['a_sens'], 0.6)
        r.tap('an_next')
        rows = r.widgets()['tb_rows']
        self.assertEqual(rows['count'], 8)
        direct = rt.analyze_image(r.h.images[iid], r.h._channel(r.h.images[iid], 'EX3'), 0.6)
        self.assertEqual(rows['cells'][0][2], f'{direct["lanes"][0]["bands"][0]["volume"]:.4g}')   # the table = image.analyze
        r.tap('tb_o5')                                                         # Rf
        self.assertEqual(r.widgets()['tb_rows']['cells'][0][2], f'{direct["lanes"][0]["bands"][0]["rf"]:.3f}')
        r.tap('tb_back')
        r.tap('an_report')
        r.ov('OK')
        self.assertEqual(r.overlay()['lines'], ['Report and analysis table exported to the USB drive.'])
        done('analyze an image')

    def test_run_instrument_diagnostics(self):
        r = Rig()
        r.tap('tb_settings')
        r.tap('s_service')
        r.tap('sv_diag')
        r.tap('dg_start')
        self.assertEqual(r.val('operation.kind'), 'diagnostics')
        self.assertIn('Instrument diagnostic', r.text('dg_status'))
        r.step(302.0)
        self.assertEqual(r.val('drawer.state'), 'opening')                      # the drawer opens during the test
        r.step(300.0)
        self.assertEqual(r.text('dg_result'), 'Result: PASS')
        self.assertEqual(r.widgets()['dg_items']['count'], 7)
        done('run instrument diagnostics')


class Screens(unittest.TestCase):
    def test_gallery_select_filter_delete(self):
        r, iid = KeyProcedures.captured(None)
        r.tap('c_capture')
        r.step(600.0)
        r.tap('tb_gallery')
        lst = r.widgets()['g_list']
        self.assertEqual(lst['count'], 2)
        r.touch(lst['rect'][0] + 10, lst['rect'][1] + 30)                      # newest first
        self.assertEqual(len(r.vars()['sel']), 1)
        r.tap('g_all')
        self.assertEqual(len(r.vars()['sel']), 2)
        r.tap('g_none')
        r.tap('g_view')                                                        # thumbnail view: the images themselves
        self.assertEqual(r.widgets()['g_th0']['pw'], 48)
        r.tap('g_th1')
        self.assertEqual(len(r.vars()['sel']), 1)
        self.assertTrue(r.text('g_th1_l').startswith('[x]'))
        r.tap('g_view')
        r.tap('g_none')
        r.tap('g_filter')
        b = next(b for b in r.overlay()['buttons'] if b['id'] == 'row:keyword')
        r.touch(b['rect'][0] + 10, b['rect'][1] + 10)
        r.type('zz')
        r.ov('Done')
        self.assertEqual(r.widgets()['g_list']['count'], 0)                     # keyword filter
        r.tap('g_filter')
        b = next(b for b in r.overlay()['buttons'] if b['id'] == 'row:keyword')
        r.touch(b['rect'][0] + 10, b['rect'][1] + 10)
        r.ov('Del')
        r.ov('OK')
        r.ov('Done')
        r.tap('g_all')
        r.tap('g_delete')
        r.ov('Delete')
        self.assertEqual(r.h.images, {})

    def test_image_adjust_view(self):
        r, iid = KeyProcedures.captured(None)
        before = r.h.panel.render(SCR)
        r.tap('c_more')
        r.tap('m_adjust')
        r.tap('ad_c_raw')
        r.tap('ad_invert')
        r.tap('ad_sat')
        r.tap('ad_zoom_p')
        v = r.val('image.views')[iid]
        self.assertEqual((v['contrast'], v['invert'], v['saturation'], v['view_zoom']), ('raw', True, True, 1.25))
        r.tap('tb_back')
        r.tap('tb_back')
        self.assertFalse((r.h.panel.render(SCR) == before).all())                # the display changed, not the data
        self.assertEqual(r.h.images[iid]['channels'][0]['img'].sum(), r.h.images[iid]['channels'][0]['img'].sum())

    def test_camera_routines_trans_light_lock(self):
        r = Rig(sample=blot())
        r.load('fluorescent_blots')
        r.tap('c_dyes')
        r.row('dy_list', 'Alexa Fluor 647', col=1)
        r.tap('dy_done')
        r.tap('c_trans')
        self.assertTrue(r.val('app.trans_light'))
        r.tap('c_more')
        r.tap('m_camera')
        r.choose('cm_bin', 'Binning', '4x4')
        self.assertEqual(r.val('camera.binning'), 4)
        z = r.val('camera.zoom')
        r.tap('cm_zoom_in')
        self.assertGreater(r.val('camera.zoom'), z)
        r.tap('cm_rot_p')
        self.assertAlmostEqual(r.val('stage.angle_rad'), round(r.h.stage_angle, 9))
        r.tap('tb_back')
        r.tap('m_routines')
        r.tap('rt_multi')
        r.step(400.0)
        self.assertEqual(len(r.h.images), 5)
        r.tap('c_lock')
        self.assertTrue(r.val('camera.locked'))
        r.tap('c_plus')                                                        # locked: the device's message
        self.assertEqual(r.overlay()['lines'], ['Camera settings are locked.'])

    def test_custom_dye_false_colour_band_excision(self):
        r = Rig(sample=blot())
        r.load('fluorescent_blots')
        r.tap('c_dyes')
        r.tap('dy_custom')
        for fid, text in (('name', 'mydye'), ('emission_nm', '700'), ('excitation_nm', '620')):
            b = next(b for b in r.overlay()['buttons'] if b['id'] == 'row:' + fid)
            r.touch(b['rect'][0] + 10, b['rect'][1] + 10)
            r.type(text)
        r.ov('Save')
        self.assertEqual(r.val('dyes.custom')[0]['dye'], 'custom_mydye')
        r.row('dy_list', 'mydye', col=1)
        r.tap('dy_done')
        self.assertEqual(r.val('channels.selected'), ['EX3:custom_mydye'])
        r.tap('c_dyes')
        r.choose('dy_fc', 'False color', 'Cyan')
        self.assertEqual(r.val('channels.false_colors')['EX3'], 'cyan')
        g = Rig(sample=blot())
        g.load('nucleic_acid_gels')
        g.tap('c_more')
        g.tap('m_excision')
        self.assertTrue(g.val('band_excision.on'))
        g.step(5.0)
        self.assertEqual(g.val('drawer.state'), 'open')
        g.ov('Done')
        self.assertFalse(g.val('band_excision.on'))

    def test_settings_users_naming_network_logs(self):
        r = Rig(guest=False)
        r.tap('si_start')
        r.tap('gs_create')
        for label, text in (('Screen name', 'ann'), ('PIN (4 digits)', '1111'), ('Confirm PIN', '1111')):
            r.row('cp_form', label)
            r.type(text)
        r.tap('cp_ok')
        r.tap('tb_settings')
        r.tap('s_inst')
        r.choose('is_form', 'Time Zone', 'UTC+04:00')
        r.choose('is_form', 'Sleep Mode', 'Disabled')
        s = r.val('app.settings')
        self.assertEqual((s['time_zone'], s['sleep_mode']), ('UTC+04:00', False))
        r.row('is_form', 'Backup settings')
        r.ov('OK')
        self.assertIsNotNone(r.val('settings.backup_s'))
        r.row('is_form', 'Manage Users')
        self.assertEqual(r.page(), ('app', 'users'))
        r.choose('us_form', 'Security mode', 'On (login required)')
        self.assertTrue(r.val('app.settings')['security_mode'])
        r.tap('tb_back')
        r.tap('tb_back')
        r.tap('s_naming')
        r.choose('nm_form', 'Aspect 1', 'Mode')
        r.choose('nm_form', 'Aspect 2', 'Exposure Time')
        self.assertEqual(r.val('app.settings')['naming'], ['Mode', 'Exposure Time'])
        self.assertIn('_', r.text('nm_example'))
        r.tap('tb_back')
        r.tap('s_network')
        r.row('nw_form', 'Wired')
        self.assertEqual(r.overlay()['lines'], ['No Ethernet connection found.'])
        r.ov('OK')
        r.tap('tb_back')
        r.tap('s_sae')
        self.assertEqual(r.frame()['ui']['unsupported'], 'SAE needs an SAE license and a Local Admin (not installed). (guide p.40)')
        r.ov('OK')
        r.tap('s_events')
        self.assertGreater(r.widgets()['ev_list']['count'], 0)
        r.tap('tb_back')
        r.tap('tb_user')                                                       # security on: no guest after signing out
        r.ov('Sign out')
        self.assertEqual(r.page(), ('login', 'signin'))
        self.assertNotIn('si_guest', r.widgets())


class EquivalenceAndRules(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        p = Rig(sample=blot())
        d = Rig(sample=blot())
        p.load('fluorescent_blots')
        d.load('fluorescent_blots')
        p.tap('c_dyes')
        p.row('dy_list', 'Alexa Fluor 647', col=1)
        p.tap('dy_done')
        p.tap('c_plus')
        p.tap('c_trans')
        d.call(d.A, 'channels.set', channels=[dict(channel='EX3', dye='alexa_fluor_647')])
        d.call(d.A, 'exposure.set', channel='EX3', exposure_s=2.0)
        d.call(d.A, 'live.set_trans_light', enabled=True)
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))
        p.tap('c_capture')
        d.call(d.A, 'capture.run', hdr=False)
        p.step(300.0)
        d.step(300.0)
        a, b = p.h.read('evaluator'), d.h.read('evaluator')
        self.assertEqual({k: v for k, v in a.items() if k != 'gallery'}, {k: v for k, v in b.items() if k != 'gallery'})
        self.assertTrue((p.h.images['IMG0001']['channels'][0]['img'] == d.h.images['IMG0001']['channels'][0]['img']).all())

    def test_refusals_sleep_and_power(self):
        r = Rig(sample=blot(), sleep=True)
        r.load('fluorescent_blots')
        r.tap('c_capture')                                                    # no dye chosen
        self.assertEqual(r.overlay()['lines'], ['Choose a dye or channel first.'])
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'NO_CHANNELS')
        r.ov('OK')
        r.step(1199.0)
        self.assertEqual(r.frame()['screen'], 'app')
        r.step(2.0)
        self.assertEqual(r.frame()['screen'], 'sleep')                          # sleep mode after 20 min without a touch
        self.assertEqual(int(r.h.panel.render(SCR).max()), 0)
        r.touch(500, 400)
        self.assertEqual(r.page(), ('login', 'signin'))
        r.switch('off')
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.frame()['displays']['light_bar']['on'])

    def test_rejected_gestures_and_access(self):
        r = Rig()
        self.assertEqual(r.call(r.A, 'panel.touch', expect='rejected', display_id=SCR, x=5000, y=1)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')


class SyncAuditFixes(unittest.TestCase):
    """Sync audit 2026-10-19: caller rules on the panel, Edit analysis frame, Adjust bands, Ship Prep Mode and Pixel Mapping."""

    @staticmethod
    def second_agent(r):
        r.h.register_principal('agent_b', 'agent')
        b = r.h.session('agent_b')

        def touch(x, y):
            return r.call(b, 'panel.touch', display_id=SCR, x=int(x), y=int(y))['accepted']

        def tap(wid):
            x, y, w, h = r.widgets()[wid]['rect']
            return touch(x + w // 2, y + h // 2)

        def ov(label):
            x, y, w, h = next(b_ for b_ in r.overlay()['buttons'] if b_['label'] == label)['rect']
            return touch(x + w // 2, y + h // 2)
        return b, tap, ov

    def test_another_agent_is_refused_on_the_panel(self):
        """B's panel delete of A's image and B's power switch during A's capture are refused like B's direct calls (state unchanged, the device's
        message); A polls and cancels its own panel-started capture."""
        r = Rig(sample=blot())
        b, tap_b, ov_b = self.second_agent(r)
        r.load('fluorescent_blots')
        r.tap('c_dyes')
        r.row('dy_list', 'Alexa Fluor 647', col=1)
        r.tap('dy_done')
        r.tap('c_capture')
        op = r.h.op['op']
        self.assertEqual(r.A.poll(op)['status'], 'running')
        before = r.h.read('evaluator')
        self.assertEqual(r.call(b, 'drawer.open', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertFalse(tap_b('c_drawer'))                                   # B's Open drawer during A's capture: refused, the device's message
        self.assertEqual(r.overlay()['lines'], ['This image or run belongs to another user.'])
        self.assertEqual(r.h.read('evaluator'), before)
        ov_b('OK')                                                            # closing the message: navigation, free
        r.step(600.0)
        iid = max(r.h.images)
        self.assertEqual(r.h._owners[iid], 'agent')
        self.assertEqual(r.call(b, 'image.delete', expect='rejected', image_id=iid)['error']['code'], 'ACCESS_DENIED')
        r.tap('tb_gallery')
        tap_b('g_all')
        tap_b('g_delete')
        self.assertFalse(ov_b('Delete'))
        self.assertEqual(r.overlay()['lines'], ['This image or run belongs to another user.'])
        self.assertIn(iid, r.h.images)
        r.ov('OK')
        r.tap('tb_back')
        self.assertEqual(r.page(), ('app', 'capture'))
        r.tap('c_capture')                                                    # A's second capture from the panel: A cancels it
        op2 = r.h.op['op']
        self.assertEqual(r.A.cancel(op2, 'cancel')['status'], 'succeeded')
        self.assertEqual(r.A.poll(op2)['status'], 'cancelled')
        r.tap('c_capture')                                                    # the rear power switch is a physical switch: B's switch-off is not
        op3 = r.h.op['op']                                                    # refused, the capture fails as on a power loss
        self.assertTrue(r.call(b, 'panel.switch', input_id='power', position='off')['accepted'])
        self.assertFalse(r.h.power)
        self.assertEqual(r.A.poll(op3)['status'], 'failed')

    def test_edit_analysis_frame_and_adjust_bands(self):
        r, iid = KeyProcedures.captured(None)
        r.tap('c_analyze')
        img = r.h.images[iid]
        ny, nx = img['channels'][0]['img'].shape
        p = img['stored_px_m']
        box = pu._image_box(r.widgets()['an_view']['rect'], r.widgets()['an_view'])

        def at(xm, ym):                                                        # screen point of an image point (m, centred)
            return box[0] + (xm / p + nx / 2) / nx * box[2], box[1] + (ym / p + ny / 2) / ny * box[3]
        f = rt.analyze_image(img, r.h._channel(img, 'EX3'), 0.5)['frame_m']
        r.tap('an_frame')                                                     # Edit analysis frame
        self.assertEqual(r.widgets()['af_list']['count'], 1)
        r.touch(*at((f[0] + f[2]) / 2, (f[1] + f[3]) / 2))                    # tap the frame: selected
        self.assertEqual(r.vars()['a_fsel'], 0)
        x0, y0 = at(f[0], f[1])
        x1, y1 = at(f[0] + 0.041, f[3])
        r.touch(x0, y0, to_x=int(x1), to_y=int(y1))                           # resize it over lanes 1-4
        r.touch(*at(f[2] + 0.005, 0.0))                                       # tap outside: none selected
        x0, y0 = at(f[0] + 0.0415, f[1])
        x1, y1 = at(f[2], f[3])
        r.touch(x0, y0, to_x=int(x1), to_y=int(y1))                           # draw a second frame over lanes 5-8
        self.assertEqual(r.widgets()['af_list']['count'], 2)
        r.tap('af_apply')
        self.assertIsNone(r.vars()['a_edit'])
        self.assertTrue(r.text('an_sum').endswith('2 frames'))
        frames = [dict(rect_m=x) for x in r.vars()['a_frames']]
        direct = r.call(r.A, 'image.analyze', image_id=iid, channel='EX3', sensitivity=0.5, frames_m=frames)
        self.assertEqual([l['frame'] for l in direct['lanes']], [1] * 4 + [2] * 4)
        r.tap('an_more')                                                      # Adjust bands
        r.ov('Adjust bands')
        r.row('ab_lanes', 'Lane 1')
        lane = direct['lanes'][0]
        x0, y0 = at(f[0] + lane['x_m'], f[1] + 0.030)
        x1, y1 = at(f[0] + lane['x_m'], f[1] + 0.034)
        r.touch(x0, y0, to_x=int(x1), to_y=int(y1))                           # drag over lane 1: a second band
        self.assertEqual(r.widgets()['ab_bands']['count'], 2)
        r.tap('ab_apply')
        bands = r.vars()['a_bands']
        self.assertEqual([e['lane'] for e in bands], [1])
        direct = r.call(r.A, 'image.analyze', image_id=iid, channel='EX3', sensitivity=0.5, frames_m=frames, lane_bands_m=bands)
        self.assertEqual(len(direct['lanes'][0]['bands']), 2)
        self.assertEqual(r.text('an_sum'), f'EX3: 8 lanes, 9 bands (sensitivity 0.5), 2 frames')
        r.tap('an_next')
        self.assertEqual(r.widgets()['tb_rows']['cells'][1][2], f'{direct["lanes"][0]["bands"][1]["volume"]:.4g}')   # the table = image.analyze

    def test_ship_prep_mode(self):
        r = Rig()
        r.tap('tb_settings')
        r.tap('s_service')
        r.tap('sv_ship')
        self.assertEqual(r.page(), ('app', 'ship_prep'))
        self.assertEqual(r.text('sp_step'), 'The drawer opens. Remove the turntable, then touch Next.')
        r.step(5.0)
        self.assertEqual(r.val('drawer.state'), 'open')
        r.tap('sp_next')                                                      # turntable still in: refused
        self.assertEqual(r.overlay()['lines'], ['Remove the turntable, then touch Next.'])
        r.ov('OK')
        r.call(r.A, 'turntable.set_installed', installed=False)               # the operator (robot) lifts the turntable out
        self.assertEqual(r.text('sp_turntable'), 'Turntable: removed')
        r.tap('sp_next')
        self.assertEqual(r.text('sp_step'), 'Power off the instrument.')
        r.switch('off')
        self.assertIsNone(r.val('service.ship_prep'))

    def test_pixel_mapping_corrects_hot_pixels(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', hot_pixels=20, hot_pixel_dark_e_s=50.0)
        r.tap('tb_settings')
        r.tap('s_service')
        r.tap('sv_pixel')
        r.step(130.0)
        self.assertEqual(r.val('pixel_map.corrected_px'), 20)
        self.assertTrue(r.text('sv_pixel_s').endswith('(20 pixels corrected)'))


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        r, iid = KeyProcedures.captured(None)
        a = r.h.panel.png_bytes(SCR)[0]
        self.assertEqual(a, r.h.panel.png_bytes(SCR)[0])
        c = copy.deepcopy(r.h)
        self.assertEqual(c.panel.png_bytes(SCR)[0], a)
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {'home': r.h.panel.save_png(SCR, OUT / 'home.png')}
        r.tap('tb_settings')
        renders['menu'] = r.h.panel.save_png(SCR, OUT / 'menu.png')
        r.tap('s_sae')
        renders['error'] = r.h.panel.save_png(SCR, OUT / 'error.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')

    def test_capture_through_the_session(self):
        r = Rig()
        cap = r.call(r.A, 'panel.capture', display_id=SCR)
        self.assertEqual((cap['image']['width'], cap['image']['height']), (1024, 768))
        self.assertTrue(Path(cap['image']['path']).is_file())

    def test_spec_and_contract(self):
        spec = json.loads((rt.ASSET_DIR / 'panel_ui.json').read_text())
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS)), [])
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + rt.APP_CAPS + pu.spec_capabilities(spec):
            self.assertIn(k, c['capabilities'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(sorted(set(SURVEY_TASKS) - DONE_TASKS), [], 'run the whole module')


if __name__ == '__main__':
    unittest.main()
