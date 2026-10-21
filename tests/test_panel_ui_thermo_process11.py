"""front-panel tests for CHEM-057 thermo_process11 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on the runtime's HMI model):
python3 -m pytest tests/test_panel_ui_thermo_process11.py -q

Every gesture goes through the session API (panel.touch / panel.switch); widgets are found by id in the structured frame (panel.frame, evaluator)
and tapped at their centre.  Themes: the seven key procedures of the menu survey (heating, screw rotation, feeder from the Overview, loading and
creating recipes, all zones to one temperature, pressure-probe tare); feeder overview / set-up / maximum output; temperature profile and trends;
alarms (viewer, summary, Mute/Reset, emergency stop); time & date; engineering refused; panel = direct commands; refusals as the device's
message; render determinism and the evidence PNGs; contract entries."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import thermo_process11 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'process11_01'
OUT = ROOT / 'outputs/panel_ui/thermo_process11'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
HMI = 'hmi'
RPM = 60 / (2 * math.pi)
SURVEY_TASKS = ['start barrel heating at a set temperature', 'start screw rotation', 'start a feeder from the Overview page', 'load a recipe into the extruder',
                'create or edit a recipe', 'set all zones to the same temperature', 'tare the pressure probe']
DONE_TASKS = set()


class Rig:
    def __init__(self, power=True, material=True):
        self.h = rt.ThermoProcess11Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.call(self.A, 'method.configure', **rt.METHOD_EXAMPLE)
        if material:
            self.call(self.E, 'material.configure', **rt.MATERIAL_EXAMPLE)
        self.call(self.E, 'coolant.set_connected', connected=True)
        if power:
            self.switch('isolator', '1')
            self.touch(400, 300)                                                 # the introductory screen: touch to reach the Overview

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if r['status'] != 'rejected' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][HMI]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=HMI, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def switch(self, inp, pos):
        return self.call(self.A, 'panel.switch', input_id=inp, position=pos)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def message(self):
        """The text of the message dialog (its lines are wrapped to the dialog width)."""
        return ' '.join(self.overlay()['lines'])

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def type(self, text):
        for ch in text:
            self.ov(ch)
        self.ov('OK')

    def menu(self, bar, item):
        self.tap(bar)
        self.ov(item)

    def set_temp(self, zone_widget, digits, finish='ti_enter'):
        self.tap(zone_widget)
        for d in digits:
            self.tap('ti_d' + d)
        self.tap(finish)

    def row(self, wid, first):
        """Tap the row of a list / form widget whose first cell is this text (scrolls by dragging)."""
        for _ in range(20):                                                    # back to the top: drag the rows down
            w = self.widgets()[wid]
            if not w.get('scroll'):
                break
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        for _ in range(20):                                                    # drag the rows up until the row shows
            w = self.widgets()[wid]
            cells = [c[0] for c in w['cells']]
            if first in cells:
                break
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(cells) < w['count'], (first, cells)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        k = cells.index(first)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def hot(self, c=165):
        self.set_temp('ov_set0', str(c), 'ti_all')
        self.tap('ov_heat')
        self.h.step(60)


def done(task):
    DONE_TASKS.add(task)


def c2k(c):
    return c + 273.15


class KeyProcedures(unittest.TestCase):
    def test_start_barrel_heating_at_a_set_temperature(self):
        r = Rig(power=False)
        self.assertEqual(r.frame()['screen'], 'off')
        r.switch('isolator', '1')
        self.assertEqual(r.frame()['screen'], 'intro')                         # introductory screen while starting up
        r.touch(400, 300)
        self.assertEqual(r.page(), ('app', 'overview'))
        r.set_temp('ov_set0', '170')                                           # Zone 2 setpoint, Enter moves on to zone 3
        self.assertEqual(r.val('thermal.targets_k')[0], c2k(170))
        self.assertEqual(r.page(), ('app', 'temp_input'))
        self.assertEqual(r.frame()['ui']['vars']['tz'], 1)
        r.tap('ti_esc')
        self.assertEqual(r.text('ov_heat'), 'Heating Off')
        r.tap('ov_heat')                                                       # Heating Off -> On (red -> green)
        self.assertTrue(r.val('thermal.enabled'))
        self.assertEqual(r.widgets()['ov_heat']['fill'], 'green')
        r.h.step(60)
        self.assertAlmostEqual(r.val('thermal.zone_0_k'), c2k(170), delta=1.0)
        self.assertEqual(r.text('ov_act0'), '170')
        r.tap('ov_heat')
        self.assertFalse(r.val('thermal.enabled'))
        done('start barrel heating at a set temperature')

    def test_start_screw_rotation(self):
        r = Rig(material=False)
        r.tap('ov_setrpm')
        r.type('50')
        self.assertAlmostEqual(r.val('drive.target_rad_s'), 50 / RPM)
        r.tap('ov_drive')
        self.assertTrue(r.val('drive.enabled'))
        self.assertEqual(r.text('ov_drive'), 'Extruder On')
        r.h.step(5)
        self.assertEqual(r.text('ov_speed'), '50')
        for unit, text in (('Nm', ' Nm'), ('kW', ' kW'), ('%', ' %')):         # the torque field cycles % -> Nm -> kW -> %
            r.tap('ov_torque')
            self.assertEqual(r.val('display.torque_unit'), unit)
            self.assertTrue(r.text('ov_torque').endswith(text))
        r.tap('ov_drive')
        self.assertFalse(r.val('drive.enabled'))
        r.tap('ov_setrpm')
        r.type('0')                                                            # 0 rpm is refused by the drive (10-1000 rpm)
        self.assertEqual(r.message(), 'Invalid value.')
        done('start screw rotation')

    def test_start_a_feeder_from_the_overview_page(self):
        r = Rig()
        r.hot()
        r.tap('ov_feeder')
        r.ov('Start Feeder')                                                   # extruder not running: the device's message
        self.assertEqual(r.message(), 'Feeder started without the extruder running. Start the extruder drive first.')
        r.ov('OK')
        r.tap('ov_feed_set')
        r.type('0.8')
        self.assertAlmostEqual(r.val('feed.target_kg_s'), 0.8 / 3600)
        r.tap('ov_drive')
        r.tap('ov_feeder')
        r.ov('Start Feeder')
        self.assertTrue(r.val('feeder.state')['running'])
        self.assertEqual(r.widgets()['ov_feeder']['fill'], 'green')
        h0 = sum(r.h.hopper)
        r.h.step(20)
        self.assertAlmostEqual(h0 - sum(r.h.hopper), 0.8 / 3600 * 20, 6)
        self.assertEqual(r.text('ov_feed_act'), '0.800 kg/h')
        r.tap('ov_feeder')
        r.ov('Stop Feeder')
        self.assertFalse(r.val('feeder.state')['running'])
        self.assertEqual(r.widgets()['ov_feeder']['fill'], 'neutral')
        done('start a feeder from the Overview page')

    def make_recipe(self, r, number='3', name='pva', rpm='120', temp='160'):
        r.menu('mb_menu', 'RECIPES')
        self.assertEqual(r.page(), ('app', 'recipes'))
        r.tap('rc_' + number)
        r.tap('rc_editor')
        self.assertEqual(r.page(), ('app', 'recipe_edit'))
        r.row('re_form', 'Editing Recipe Name')
        r.type(name)
        r.row('re_form', 'Set Speed (rpm)')
        r.type(rpm)
        for z in range(8):                                                     # zone temperatures (the form scrolls)
            r.row('re_form', f'{rt.HMI_ZONES[z]} temperature (°C)')
            r.type(temp)
        r.row('re_form', 'Feeder 1 feed rate (kg/h)')
        r.type('0.6')
        r.tap('re_save')
        self.assertEqual(r.message(), 'Data saved.')
        r.ov('OK')

    def test_create_or_edit_a_recipe(self):
        r = Rig()
        self.make_recipe(r)
        rec = r.val('recipes')[2]
        self.assertEqual((rec['name'], round(rec['speed_rad_s'] * RPM), rec['temperatures_k'], round(rec['feed_kg_s'] * 3600, 3)), ('pva', 120, [c2k(160)] * 8, 0.6))
        self.assertEqual(rec['alarm_deviation_k'], [rt.ALARM_DEFAULT_K] * 8)
        r.tap('re_index')
        self.assertEqual(r.text('rc_3'), '3  pva')
        r.tap('rc_viewer')
        self.assertIn('pva', r.text('rv_name'))
        done('create or edit a recipe')

    def test_load_a_recipe_into_the_extruder(self):
        r = Rig()
        self.make_recipe(r)
        r.tap('re_index')
        r.tap('rc_write')
        r.ov('YES')                                                            # the confirmation
        self.assertEqual(r.val('recipe.loaded'), 3)
        self.assertAlmostEqual(r.val('drive.target_rad_s'), 120 / RPM)
        self.assertEqual(r.val('thermal.targets_k'), [c2k(160)] * 7)
        self.assertEqual(r.val('die.target_k'), c2k(160))
        self.assertFalse(r.val('drive.enabled'))
        r.tap('close')
        self.assertEqual(r.page(), ('app', 'overview'))
        self.assertEqual(r.text('hd_recipe'), 'Recipe Name: pva')
        self.assertEqual(r.text('ov_set7'), '160')
        r.menu('mb_menu', 'RECIPES')
        r.tap('rc_7')
        r.tap('rc_write')
        r.ov('YES')
        self.assertEqual(r.message(), 'This recipe is empty. Edit and save it first.')
        done('load a recipe into the extruder')

    def test_set_all_zones_to_the_same_temperature(self):
        r = Rig()
        r.set_temp('ov_set3', '150', 'ti_follow')                              # zone 5 and the following zones
        self.assertEqual(r.val('thermal.targets_k'), [298.15] * 3 + [c2k(150)] * 4)
        self.assertEqual(r.val('die.target_k'), c2k(150))
        r.tap('ov_set0')
        r.tap('ti_d1'), r.tap('ti_d8'), r.tap('ti_d0')
        r.tap('ti_m10')                                                         # -10
        r.tap('ti_p5')                                                          # +5
        self.assertEqual(r.text('ti_val'), '175')
        r.tap('ti_all')                                                         # Apply to all temperature zones
        self.assertEqual(r.val('thermal.targets_k'), [c2k(175)] * 7)
        self.assertEqual(r.val('die.target_k'), c2k(175))
        r.set_temp('ov_set0', '290', 'ti_all')                                  # above the 280 °C entry maximum
        self.assertEqual(r.message(), 'Setpoint maximum setting 280 °C')
        self.assertEqual(r.val('die.target_k'), c2k(175))
        done('set all zones to the same temperature')

    def test_tare_the_pressure_probe(self):
        r = Rig()
        r.hot()
        r.tap('ov_drive')
        r.tap('ov_feeder')
        r.ov('Start Feeder')
        r.h.step(20)
        self.assertGreater(r.val('extrusion.die_pressure_gauge_pa'), rt.TARE_MAX_PA)
        r.menu('mb_setup', 'Pressure Probe')
        r.tap('pp_tare')                                                        # die pressure too high: the device's message
        self.assertEqual(r.message(), 'Pressure in the die too high for taring. Clear the fault and repeat.')
        r.ov('OK')
        r.tap('close')
        r.tap('ov_feeder')
        r.ov('Stop Feeder')
        r.h.step(30)                                                            # the screws empty the die
        r.menu('mb_setup', 'Pressure Probe')
        r.tap('pp_tare')
        self.assertEqual(r.val('pressure.offset_pa'), r.h.pressure)
        self.assertEqual(r.text('pp_p'), '0.0 bar')
        done('tare the pressure probe')


class Pages(unittest.TestCase):
    def test_feeder_overview_setup_and_max_output(self):
        r = Rig()
        r.menu('mb_menu', 'Feeder Overview')
        self.assertEqual(r.text('fo_v0'), f'{rt.MATERIAL_EXAMPLE["base_kg"] + rt.MATERIAL_EXAMPLE["additive_kg"]:.3f} kg')
        r.tap('fo_mode')
        r.ov('Volumetric Feeding')
        self.assertEqual(r.val('feeder.state')['mode'], 'volumetric')
        r.menu('mb_setup', 'Feeder 1')
        r.tap('fs_tare')
        self.assertAlmostEqual(r.val('feeder.state')['hopper_kg'], 0.0)
        r.tap('fs_test')                                                        # test run with the extruder stopped
        self.assertTrue(r.val('feeder.state')['running'])
        r.h.step(10)
        self.assertGreater(r.val('feeder.state')['throughput_kg'], 0)
        r.tap('close')                                                          # leaving the page stops the test run
        self.assertFalse(r.val('feeder.state')['running'])
        r.menu('mb_setup', 'Feeder 1')
        r.tap('fs_reset')
        self.assertEqual(r.val('feeder.state')['throughput_kg'], 0.0)
        r.tap('fs_calc')
        self.assertEqual(r.page(), ('app', 'maxout'))
        r.tap('mo_tare')
        r.tap('mo_next')
        r.tap('mo_next')
        r.tap('mo_start')
        r.h.step(5)
        r.tap('mo_stop')
        r.tap('mo_next')
        r.tap('mo_calc')
        self.assertEqual(r.page(), ('app', 'feeder_setup'))
        self.assertAlmostEqual(r.val('feeder.state')['max_output_kg_s'], rt.FEED_MAX)

    def test_profile_trends_alarms_emergency(self):
        r = Rig()
        r.hot(160)
        r.tap('ov_drive')
        r.h.step(30)
        r.tap('sb_next')
        self.assertEqual(r.page(), ('app', 'process_trend'))
        plot = r.widgets()['pt_plot']
        self.assertEqual([s['label'] for s in plot['series']], ['Speed RPM/10', 'Torque %', 'Feeder %'])
        self.assertGreater(len(plot['series'][0]['points']), 20)
        r.tap('sb_next')
        self.assertEqual(r.page(), ('app', 'temp_trend'))
        self.assertEqual(len(r.widgets()['tt_plot']['series']), 8)
        r.tap('tt_b')
        self.assertEqual(r.text('tt_pos'), '1 min back')
        r.tap('tt_pause')
        self.assertTrue(r.val('trend.paused'))
        r.tap('tt_clr')
        self.assertEqual(r.widgets()['tt_plot']['series'][0]['points'], [])
        r.tap('sb_next')
        self.assertEqual(r.page(), ('app', 'temp_profile'))
        self.assertGreater(r.widgets()['tp_bar0']['fraction'], 0.5)
        self.assertEqual(r.text('sb_alarm'), 'Temperature reached')
        r.switch('emergency_stop', 'pressed')
        self.assertTrue(r.val('safety.latched'))
        self.assertEqual(r.val('alarms.active'), 'Emergency stop pressed')
        r.tap('sb_mute')
        self.assertIsNone(r.val('alarms.active'))
        r.menu('mb_alarms', 'Alarm Viewer')
        self.assertEqual(r.widgets()['av_list']['cells'][0][1], 'Emergency stop pressed')
        r.menu('mb_alarms', 'Alarm Summary')
        self.assertIn(['Emergency stop pressed', '1'], r.widgets()['as_list']['cells'])
        r.switch('emergency_stop', 'released')
        self.assertFalse(r.val('safety.pressed'))

    def test_time_date_engineering_info(self):
        r = Rig()
        r.menu('mb_config', 'Time & Date')
        r.row('td_form', 'HMI time (hh.mm.ss)')
        self.assertEqual(r.overlay()['kind'], 'keyboard')
        r.ov('C')
        r.type('09.30.00')
        self.assertTrue(r.val('clock')['hmi'].startswith('09.30.00'))
        self.assertTrue(r.val('clock')['plc'].startswith('08.00.00'))
        r.tap('td_sync')
        self.assertTrue(r.val('clock')['plc'].startswith('09.30.00'))
        r.menu('mb_config', 'Engineering')
        b = next(b for b in r.overlay()['buttons'] if b['id'] == 'row:code')
        r.touch(b['rect'][0] + 10, b['rect'][1] + 10)
        r.type('1234')
        r.ov('Enter')
        self.assertEqual(r.message(), 'Access denied: wrong engineering code.')
        self.assertIn('Read First', r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.menu('mb_info', 'Print Screen')
        self.assertEqual(r.message(), 'No PictBridge printer connected.')
        r.ov('OK')
        r.menu('mb_info', 'About')
        self.assertEqual(r.page(), ('app', 'about'))


class EquivalenceAndRules(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        p, d = Rig(), Rig()
        p.set_temp('ov_set0', '165', 'ti_all')
        p.tap('ov_heat')
        p.tap('ov_setrpm')
        p.type('150')
        p.tap('ov_torque')
        for z in range(7):
            d.call(d.A, 'thermal.set_zone', zone=z, temperature_k=c2k(165))
        d.call(d.A, 'die.set_temperature', temperature_k=c2k(165))
        d.call(d.A, 'thermal.set_enabled', enabled=True)
        d.call(d.A, 'drive.set_speed', speed_rad_s=150 * (1 / RPM))       # the panel scales rpm by 1/RPM
        d.call(d.A, 'display.configure', torque_unit='Nm')
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))
        p.h.step(40)
        d.h.step(40)
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))

    def test_power_off_and_rejected_gestures(self):
        r = Rig()
        r.switch('isolator', '0')
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.touch(100, 100))
        self.assertEqual(int(r.h.panel.render(HMI).max()), 0)
        self.assertEqual(r.call(r.A, 'panel.touch', expect='rejected', display_id=HMI, x=900, y=1)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        r = Rig()
        r.hot(160)
        r.tap('ov_drive')
        r.h.step(10)
        a = r.h.panel.png_bytes(HMI)[0]
        self.assertEqual(a, r.h.panel.png_bytes(HMI)[0])
        self.assertEqual(copy.deepcopy(r.h).panel.png_bytes(HMI)[0], a)
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {'home': r.h.panel.save_png(HMI, OUT / 'home.png')}
        r.tap('mb_menu')
        renders['menu'] = r.h.panel.save_png(HMI, OUT / 'menu.png')
        r.ov('Close')
        r.tap('ov_feeder')
        r.ov('Close')
        r.tap('ov_drive')
        r.tap('ov_feeder')
        r.ov('Start Feeder')
        renders['error'] = r.h.panel.save_png(HMI, OUT / 'error.png')
        r.ov('OK')
        r.tap('sb_next')
        renders['trend'] = r.h.panel.save_png(HMI, OUT / 'trend.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')

    def test_capture_through_the_session(self):
        r = Rig()
        cap = r.call(r.A, 'panel.capture', display_id=HMI)
        self.assertEqual((cap['image']['width'], cap['image']['height']), (800, 600))

    def test_spec_and_contract(self):
        spec = json.loads((rt.ASSET_DIR / 'panel_ui.json').read_text())
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS)), [])
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + rt.APP_CAPS + pu.spec_capabilities(spec):
            self.assertIn(k, c['capabilities'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


class SyncAuditFixes(unittest.TestCase):
    """1.4.0 (sync audit 2026-10-19): one pressure-probe reading (true pressure + zero drift - tare) on every path; the feeder screw model (true bulk
    density, gravimetric control gain, bulk density estimate and the timed Calc.Max.Output)."""

    def readings(self, r):
        return [r.val('extrusion.die_pressure_gauge_pa'), r.val('pressure.display_pa'), r.call(r.A, 'extrusion.measure')['die_pressure_gauge_pa']]

    def test_probe_reading_and_tare_on_every_path(self):
        r = Rig()
        amb = rt.PLANT_EXAMPLE['ambient_k']
        self.assertEqual(self.readings(r), [0.0] * 3)
        r.call(r.E, 'sensor.configure_error', pressure_zero_drift_pa_per_k=1000.)
        r.hot()                                                                 # die at 165 C: the probe zero drifts with the heating
        drift = 1000. * (r.h.die_k - amb)
        self.assertEqual(r.val('truth.die_pressure_pa'), 0.0)
        for v in self.readings(r):
            self.assertAlmostEqual(v, drift, 6)
        r.call(r.A, 'pressure.set_limit', limit_pa=0.5 * drift)                 # the alarm acts on the device's reading, not on the true pressure
        r.h.step(1)
        self.assertEqual(r.val('alarms.active'), 'Die pressure high')
        r.call(r.A, 'alarms.acknowledge')
        r.call(r.A, 'pressure.set_limit', limit_pa=rt.PRESSURE_MAX_PA)
        r.menu('mb_setup', 'Pressure Probe')
        self.assertEqual(r.text('pp_p'), f'{drift / 1e5:.1f} bar')
        r.tap('pp_tare')                                                        # tare at temperature (manual 6.14)
        self.assertAlmostEqual(r.val('pressure.offset_pa'), drift, 6)
        for v in self.readings(r):
            self.assertAlmostEqual(v, 0.0, 6)
        self.assertEqual(r.text('pp_p'), '0.0 bar')
        r.tap('close')
        r.tap('ov_drive')
        r.tap('ov_feeder')
        r.ov('Start Feeder')
        r.h.step(10)
        true_p = r.val('truth.die_pressure_pa')
        self.assertGreater(true_p, 1e4)
        expected = true_p + 1000. * (r.h.die_k - amb) - r.val('pressure.offset_pa')
        for v in self.readings(r):
            self.assertAlmostEqual(v, expected, 6)
        self.assertNotIn('truth.die_pressure_pa', r.A.read(instance_id=INST)['observations'])

    def feed(self, gain, t, bulk=650.):
        r = Rig()
        r.call(r.E, 'feeder.configure_material', bulk_density_kg_m3=bulk)      # true powder 30 % denser than the stored maximum output assumes
        r.menu('mb_setup', 'Feeder 1')
        r.row('fs_form', 'Control gain')
        r.type(str(gain))
        self.assertEqual(r.val('feeder.state')['gain'], gain)
        r.tap('close')
        r.hot()
        r.tap('ov_drive')
        r.tap('ov_feeder')
        r.ov('Start Feeder')
        r.h.step(t)
        return r

    def test_control_gain_drives_the_gravimetric_correction(self):
        target = rt.METHOD_EXAMPLE['feed_kg_s']
        r0 = self.feed(0, 30)                                                   # gain 0: no correction, the feeder over-feeds by the density error
        self.assertAlmostEqual(r0.val('feed.actual_kg_s'), 1.3 * target, 9)
        self.assertAlmostEqual(r0.val('feeder.state')['deviation_pct'], 30.0, 3)
        r0.h.step(31)                                                           # more than 60 s beyond the 10 % maximum deviation: feeder stop + alarm
        self.assertFalse(r0.val('feeder.state')['running'])
        self.assertEqual(r0.val('alarms.active'), 'Feeder 1 deviation alarm')
        r1, r2 = self.feed(1, 10), self.feed(2, 10)
        d1, d2 = abs(r1.val('feeder.state')['deviation_pct']), abs(r2.val('feeder.state')['deviation_pct'])
        self.assertLess(d2, d1)                                                 # a higher gain reacts more aggressively (manual p.6-31)
        self.assertLess(d1, 30.0)
        self.assertLess(r2.val('feeder.state')['screw_speed_pct'], r1.val('feeder.state')['screw_speed_pct'])
        r1.h.step(50)
        self.assertAlmostEqual(r1.val('feed.actual_kg_s'), target, delta=0.002 * target)
        self.assertTrue(r1.val('feeder.state')['running'])

    def test_bulk_density_and_max_output_calculation(self):
        r = Rig()
        r.call(r.E, 'feeder.configure_material', bulk_density_kg_m3=650.)
        r.menu('mb_setup', 'Feeder 1')
        r.row('fs_form', 'Volumetric setpoint')
        r.type('20')
        r.tap('fs_calc')
        r.tap('mo_tare')
        r.tap('mo_next')
        r.row('mo_bulk', 'Bulk density')                                        # the operator enters 400 kg/m3: the controller's estimate follows
        r.type('400')
        self.assertAlmostEqual(r.val('feeder.state')['max_output_kg_s'], rt.FEED_MAX * 0.8, 12)
        r.tap('mo_next')
        r.tap('mo_start')
        r.h.step(3)
        r.tap('mo_stop')
        r.tap('mo_next')
        h0 = sum(r.h.hopper)
        r.tap('mo_calc')
        self.assertEqual(r.page(), ('app', 'feeder_setup'))
        self.assertEqual(r.text('fs_calc_msg'), 'Calculating Max Output. Please wait')
        r.h.step(30)
        self.assertTrue(r.val('feeder.state')['calculating'])
        self.assertAlmostEqual(r.val('feeder.state')['max_output_kg_s'], rt.FEED_MAX * 0.8, 12)
        r.h.step(31)                                                            # after one minute: weighed mass scaled to 100 % screw speed
        st = r.val('feeder.state')
        self.assertFalse(st['calculating'])
        self.assertNotIn('fs_calc_msg', r.widgets())
        self.assertAlmostEqual(h0 - sum(r.h.hopper), rt.FEED_MAX * 1.3 * 0.2 * 60, 9)
        self.assertAlmostEqual(st['max_output_kg_s'], rt.FEED_MAX * 1.3, 12)
        r.tap('fs_mode')                                                        # volumetric: 20 % screw speed x screw volume x true density
        r.ov('Volumetric Feeding')
        r.tap('fs_test')
        r.h.step(5)
        self.assertAlmostEqual(r.val('feeder.state')['actual_kg_s'], rt.FEED_MAX * 1.3 * 0.2, 12)
        self.assertAlmostEqual(r.val('feeder.state')['screw_speed_pct'], 20.0, 9)
        r.tap('fs_test')
        r.tap('close')
        r.tap('ov_feeder')                                                      # gravimetric with the measured maximum output: exact feed-forward even at gain 0
        r.ov('Close')
        r.call(r.A, 'feeder.configure', mode='gravimetric', gain=0.)
        r.hot()
        r.tap('ov_drive')
        r.tap('ov_feeder')
        r.ov('Start Feeder')
        r.h.step(10)
        self.assertAlmostEqual(r.val('feed.actual_kg_s'), rt.METHOD_EXAMPLE['feed_kg_s'], 12)

    def test_calc_refusals_and_power_off(self):
        r = Rig()
        r.call(r.A, 'feeder.configure', setpoint_pct=0.)
        self.assertEqual(r.call(r.A, 'feeder.action', expect='rejected', action='calc_max_output')['error']['code'], 'PRECONDITION_FAILED')
        r.call(r.A, 'feeder.configure', setpoint_pct=50.)
        r.call(r.A, 'feeder.action', action='calc_max_output')
        self.assertTrue(r.val('feeder.state')['calculating'])
        r.switch('isolator', '0')                                               # power off aborts the calculation, the old value stays
        self.assertFalse(r.val('feeder.state')['calculating'])
        self.assertEqual(r.val('feeder.state')['max_output_kg_s'], rt.FEED_MAX)




class DocumentedAlarms(unittest.TestCase):
    """Operating manual 6.19 Alarm Help: alarm codes without a cause in the model come from the evaluator (fault.inject / fault.clear)."""

    def hot(self):
        r = Rig()
        for i in range(7):
            r.call(r.A, 'thermal.set_zone', zone=i, temperature_k=438.15)
        r.call(r.A, 'thermal.set_enabled', enabled=True)
        r.h.step(40.0)
        return r

    def test_drive_breaker_stops_the_extruder_until_reset(self):
        r = self.hot()
        r.call(r.A, 'drive.set_enabled', enabled=True)
        op = r.call(r.A, 'acquisition.start')['operation_id']
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='ME002')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        r.call(r.E, 'fault.inject', code='ME002')
        self.assertEqual(r.text('sb_alarm'), 'ME002 Extruder drive circuit breaker tripped')
        self.assertFalse(r.val('drive.enabled'))
        self.assertEqual(r.A.poll(op)['status'], 'failed')
        self.assertFalse(r.tap('ov_drive'))                                    # Extruder On refused until the breaker is reset
        r.ov('OK')
        r.tap('sb_mute')                                                       # Mute/Reset closes the message, the cause stays
        self.assertEqual(r.text('sb_alarm'), '')
        self.assertEqual(r.call(r.A, 'drive.set_enabled', expect='rejected', enabled=True)['error']['code'], 'PRECONDITION_FAILED')
        r.call(r.E, 'fault.clear', code='ME002')                                # the electrician resets circuit breaker 03Q2
        self.assertTrue(r.tap('ov_drive'))
        self.assertTrue(r.val('drive.enabled'))

    def test_heating_breaker_cuts_the_heaters(self):
        r = self.hot()
        r.call(r.E, 'fault.inject', code='ME003')
        r.h.step(5.0)
        self.assertEqual(max(r.val('heater.power_fraction')[:7]), 0.0)
        r.call(r.E, 'fault.clear', code='ME003')
        r.call(r.A, 'thermal.set_zone', zone=0, temperature_k=460.0)
        r.h.step(5.0)
        self.assertGreater(r.val('heater.power_fraction')[0], 0.0)

    def test_mute_reset_alarms_and_notices(self):
        r = self.hot()
        r.call(r.E, 'fault.inject', code='FE002')                               # 'press Mute/Reset to continue'
        self.assertEqual(r.call(r.E, 'fault.inject', code='HE003')['latched'], ['FE002', 'HE003'])
        self.assertEqual(r.text('sb_alarm'), 'HE003 Extruder calibration/service due')
        r.tap('sb_mute')
        self.assertEqual(r.h.alarm_latched, ['HE003'])                         # the service notice stays until serviced
        r.call(r.A, 'drive.set_enabled', enabled=True)                          # the machine functions as normal

class CallerRules(unittest.TestCase):
    """Final sync 2026-10-19: a timed run belongs to the session that started it; another agent's HMI gesture or direct call that would stop or
    change it is refused with the HMI's message; the isolator stays a hard switch (a power loss); the owner polls and cancels its run."""

    def test_another_agent_cannot_stop_or_change_the_run(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        b = r.h.session('agent_b')
        for i in range(7):
            r.call(r.A, 'thermal.set_zone', zone=i, temperature_k=438.15)
        r.call(r.A, 'thermal.set_enabled', enabled=True)
        r.h.step(40.0)
        r.call(r.A, 'drive.set_enabled', enabled=True)
        op = r.call(r.A, 'acquisition.start')['operation_id']
        r.h.step(2.0)
        before = r.h.read('evaluator')
        for cap, args in (('acquisition.stop', {}), ('drive.set_enabled', dict(enabled=False)), ('feed.set_rate', dict(mass_rate_kg_s=1e-4)),
                          ('thermal.set_zone', dict(zone=0, temperature_k=450.0))):
            self.assertEqual(r.call(b, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        x, y, w, h = r.widgets()['ov_drive']['rect']
        self.assertFalse(r.call(b, 'panel.touch', display_id=HMI, x=x + w // 2, y=y + h // 2)['accepted'])   # B's Extruder Off: refused
        self.assertEqual(r.message(), 'The timed run was started by another user.')
        self.assertEqual(r.h.read('evaluator'), before)
        r.ov('OK')
        with self.assertRaises(rt.ActionRejected):
            b.poll(op)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        self.assertTrue(r.call(b, 'panel.switch', input_id='isolator', position='0')['accepted'])   # the isolator is a hard switch
        self.assertFalse(r.val('power.enabled'))
        self.assertNotEqual(r.h.phase, 'running')

    def test_owner_cancels_its_run(self):
        r = Rig()
        for i in range(7):
            r.call(r.A, 'thermal.set_zone', zone=i, temperature_k=438.15)
        r.call(r.A, 'thermal.set_enabled', enabled=True)
        r.h.step(40.0)
        r.call(r.A, 'drive.set_enabled', enabled=True)
        op = r.call(r.A, 'acquisition.start')['operation_id']
        r.call(r.A, 'feed.set_rate', mass_rate_kg_s=1e-4)                       # the owner may change its run
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'succeeded')

class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(sorted(set(SURVEY_TASKS) - DONE_TASKS), [], 'run the whole module')


if __name__ == '__main__':
    unittest.main()
