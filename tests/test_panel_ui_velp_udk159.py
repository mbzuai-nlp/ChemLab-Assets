"""front-panel tests for CHEM-048 velp_udk159 (approximate panel_ui.json + scripts/asset_runtime/_panel_ui.py on the runtime):
python3 -m pytest tests/test_panel_ui_velp_udk159.py -q

Every gesture goes through the session API (panel.touch / panel.switch); widgets are found by id in the structured frame (panel.frame, evaluator)
and tapped at their centre.  The portrait screen (480 x 688) starts on the main menu (brochure p.4 photo); the analysis screen is behind its Analysis
tile.  Themes: the survey's key procedure (Set-up > Connectivity and ERMES with the One Time Code); an analysis started and read on the analysis
screen, the sensor messages before START, STOP; the method library (select, edit, Devarda delay); archive (export and print
show the device message); users and language; panel = direct commands; power selector; render determinism and the evidence PNGs; contract."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
from asset_runtime import velp_udk159 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/velp_udk159'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['connect the UDK to the network / Ermes']
DONE_TASKS = set()


class Rig:
    def __init__(self, ready=True, close=True):
        self.h = rt.VelpUdk159Runtime()
        self.inst = self.h.instance_id
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        for cap, args in (('plant.configure', rt.PLANT_EXAMPLE), ('sample.configure', rt.example_sample()), ('stocks.configure', rt.STOCK_EXAMPLE),
                          ('coolant.set', rt.COOLANT_EXAMPLE)):
            self.call(self.E, cap, **args)
        self.switch('on')
        self.call(self.A, 'library.select', index=1)                          # the method in use (position 1)
        if ready:
            self.call(self.A, 'tube.set_clamped', clamped=True)
            self.h.step(1.0)
            if close:
                self.call(self.A, 'door.set_open', open=False)
                self.h.step(1.0)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if r['status'] != 'rejected' else r

    def val(self, sid):
        return self.E.read(instance_id=self.inst, state_ids=[sid])['observations'][sid]['value']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def widgets(self):
        return {w['id']: w for w in self.frame()['displays']['screen']['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id='screen', x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_selector', position=pos)['accepted']

    def overlay(self):
        return self.frame()['displays']['screen'].get('overlay')

    def message(self):
        return ' '.join(self.overlay()['lines'])

    def ov(self, label):
        ov = self.overlay()
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def type(self, text, clear=True):
        if clear:
            ov = self.overlay()
            labels = [b['label'] for b in ov['buttons']]
            for _ in range(70):
                self.ov('Del' if 'Del' in labels else 'C')
                if 'C' in labels:
                    break
        for ch in text:
            for _ in range(4):                                                  # switch keyboard modes until the key shows
                labels = [b['label'] for b in self.overlay()['buttons']]
                if ch in labels:
                    break
                self.ov(next(m for m in ('abc', 'Shift', '12@') if m in labels and not (m == 'Shift' and ch.islower())))
            self.ov(ch)
        self.ov('OK')

    def row(self, wid, first):
        w = self.widgets()[wid]
        cells = [c[0] for c in w['cells']]
        k = cells.index(first)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def form_row(self, wid, label):
        return self.row(wid, label)


def done(task):
    DONE_TASKS.add(task)


class KeyProcedures(unittest.TestCase):
    def test_connect_to_the_network_and_ermes(self):
        r = Rig()
        r.tap('n_setup')
        r.tap('su_conn')
        self.assertEqual(r.page(), ('app', 'connectivity'))
        r.form_row('c_form', 'IP address')
        r.type('192.168.10.20')
        r.form_row('c_form', 'Gateway')
        r.type('192.168.10.1')
        r.tap('c_save')
        self.assertEqual(r.message(), 'Connectivity saved.')
        r.ov('OK')
        self.assertEqual(r.val('network.setting')['ip_address'], '192.168.10.20')
        r.tap('c_cancel')
        r.tap('su_ermes')
        r.tap('e_yes')
        otc = r.val('network.setting')['one_time_code']
        self.assertTrue(otc.isdigit() and len(otc) == 4)
        self.assertEqual(r.text('e_otc'), 'One Time Code: ' + otc)
        r.tap('e_no')
        self.assertEqual(r.text('e_otc'), '')
        done('connect the UDK to the network / Ermes')

    def test_wifi_settings_and_bad_address(self):
        r = Rig()
        r.tap('n_setup')
        r.tap('su_conn')
        r.form_row('c_form', 'IP address')
        r.type('300.1.1.1')
        r.tap('c_save')
        self.assertEqual(r.message(), 'Invalid value.')
        r.ov('OK')
        r.form_row('c_form', 'Connection')
        r.ov('Wi-Fi (USB adapter)')
        self.assertIn('Wi-Fi name', [c[0] for c in r.widgets()['c_form']['cells']])
        r.tap('c_cancel')
        self.assertEqual(r.val('network.setting')['mode'], 'lan')


class Analysis(unittest.TestCase):
    def test_start_read_result(self):
        r = Rig()
        self.assertEqual(r.page(), ('app', 'home'))                             # power-on: the main menu
        r.tap('n_analysis')
        self.assertEqual(r.text('h_phase'), 'Ready')
        r.tap('h_start')
        self.assertEqual(r.val('cycle.phase'), 'running')
        r.h.step(5.0)
        self.assertEqual(r.text('h_stage'), 'Adding NaOH / H2O / H3BO3')
        r.h.step(400.0)
        self.assertEqual(r.text('h_phase'), 'Analysis completed')
        mg = r.val('panel.nitrogen_mg')
        self.assertEqual(r.text('r_result'), f'{mg:.2f} mg N')
        self.assertEqual(r.text('h_col'), 'Grey/Pink')
        a = r.val('archive.results')[-1]
        self.assertEqual((a['method'], a['result']), ('Protein (example)', 'succeeded'))

    def test_sensor_messages_and_stop(self):
        r = Rig(close=False)
        r.tap('n_analysis')
        r.tap('h_start')
        self.assertEqual(r.message(), 'Close the protective door.')
        r.ov('OK')
        self.assertEqual(r.widgets()['s_door']['fill'], 'error')
        r.call(r.A, 'door.set_open', open=False)
        r.h.step(1.0)
        r.call(r.E, 'coolant.set', temperature_K=288.15, flow_m3_s=0.0)
        r.tap('h_start')
        self.assertEqual(r.message(), 'Cooling water: check the flow and the temperature.')
        r.ov('OK')
        r.call(r.E, 'coolant.set', **rt.COOLANT_EXAMPLE)
        r.tap('h_start')
        r.h.step(60.0)
        r.tap('h_stop')
        self.assertEqual((r.val('cycle.phase'), r.text('h_phase')), ('cancelled', 'Analysis stopped'))
        self.assertFalse(r.switch('off') and r.val('power.enabled'))

    def test_panel_started_analysis_belongs_to_the_caller(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.tap('n_analysis')
        r.tap('h_start')                                                         # the START key: the analysis belongs to the caller
        op = r.h.operation_id
        self.assertEqual(r.h._owners.get(op), 'agent')
        r.h.step(30.0)
        before = r.h.debug_snapshot()
        before.pop('revision')
        x, y, w, h = r.widgets()['h_stop']['rect']
        self.assertFalse(r.call(B, 'panel.touch', display_id='screen', x=x + w // 2, y=y + h // 2)['accepted'])   # B taps STOP: refused
        self.assertEqual(r.message(), 'This analysis was started by another user.')                 # the device's message dialog (text ours)
        r.ov('OK')
        self.assertEqual(r.call(B, 'acquisition.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        after = r.h.debug_snapshot()
        after.pop('revision')
        runtime = lambda d: {k: v for k, v in d.items() if k not in ('_requests', 'panel')}   # noqa: E731  (the panel keeps its last gesture)
        self.assertEqual(runtime(after), runtime(before))
        self.assertEqual(r.val('cycle.phase'), 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        self.assertEqual(r.A.poll(op)['status'], 'running')                     # A polls and cancels its own analysis
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'cancelled')

    def test_power_selector_is_a_hard_switch(self):
        r = Rig()                                                                # final sync: the selector cuts the mains; never refused
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.tap('n_analysis')
        r.tap('h_start')
        op = r.h.operation_id
        r.h.step(30.0)
        self.assertTrue(r.call(B, 'panel.switch', input_id='power_selector', position='off')['accepted'])
        self.assertFalse(r.val('power.enabled'))
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')
        self.assertEqual(r.h.fault, 'power_interrupted')

    def test_method_library_select_and_edit(self):
        r = Rig()
        r.tap('n_methods')
        r.row('m_list', '2')
        self.assertEqual(r.overlay()['title'], 'Method')
        r.ov('Select')
        self.assertEqual(r.message(), 'This method position is empty.')
        r.ov('OK')
        r.row('m_list', '2')
        r.ov('Edit')
        self.assertEqual(r.page(), ('app', 'method_edit'))
        r.form_row('me_form', 'Method name')
        r.type('Devarda')
        r.form_row('me_form', 'Delay time (Devarda) (s)')
        r.type('30')
        r.form_row('me_form', 'Steam power (%)')
        r.type('80')
        r.tap('me_save')
        self.assertEqual(r.message(), 'Method saved.')
        r.ov('OK')
        self.assertEqual(r.val('library.methods')[1]['delay_s'], 30.0)
        r.row('m_list', '2')
        r.ov('Select')
        self.assertEqual(r.val('method.selected'), dict(index=2, name='Devarda'))
        r.ov('OK')
        self.assertEqual(r.page(), ('app', 'analysis'))                         # Select leads to the analysis screen
        self.assertEqual(r.text('hd_method'), 'Method: Devarda')
        self.assertEqual(r.text('r_steam'), '80 %')
        r.tap('h_start')
        r.h.step(10.0)
        self.assertEqual(r.text('h_stage'), 'Delay time (Devarda)')
        self.assertTrue(r.text('h_delay').startswith('Delay 00:'))
        r.tap('back')                                                           # Menu: back to the main menu
        self.assertFalse(r.tap('n_methods'))                                    # locked during the analysis
        self.assertTrue(r.tap('n_analysis'))


class Pages(unittest.TestCase):
    def test_archive_users_language(self):
        r = Rig()
        r.tap('n_analysis')
        r.tap('h_start')
        r.h.step(400.0)
        r.tap('back')
        r.tap('n_archive')
        cells = r.widgets()['a_list']['cells']
        self.assertEqual(cells[0][1], 'Protein (example)')
        self.assertEqual(cells[0][5], 'succeeded')
        r.row('a_list', '1')                                                    # the full entry of a row (the portrait list clips its columns)
        self.assertIn('Method: Protein (example)', r.overlay()['lines'])
        r.ov('OK')
        r.tap('a_export')
        self.assertEqual(r.message(), 'No USB pen drive connected.')
        self.assertIn('pen drive', r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.tap('a_print')
        self.assertEqual(r.message(), 'No printer connected.')
        r.ov('OK')
        r.tap('back')
        r.tap('n_setup')
        r.tap('su_users')                                                       # user management under Set-up
        r.tap('u_add')
        r.type('Ana')
        r.tap('u_save')
        self.assertEqual(r.message(), 'User added.')
        r.ov('OK')
        r.row('u_list', 'Ana')
        self.assertEqual(r.val('settings.device')['active_user'], 'Ana')
        self.assertEqual(r.text('hd_user'), 'User: Ana')
        r.tap('back')
        self.assertEqual(r.page(), ('app', 'setup'))
        r.tap('su_lang')
        r.ov('Italiano')
        self.assertEqual(r.val('settings.device')['language'], 'I')


class CheckupAndGuide(unittest.TestCase):
    def test_checkup_sensors_and_reagent_levels(self):
        r = Rig(close=False)                                                    # door still open
        r.tap('n_checkup')
        self.assertEqual(r.page(), ('app', 'checkup'))
        w = r.widgets()
        self.assertEqual((w['ck_door']['text'], w['ck_door']['fill']), ('Protective door: open', 'error'))
        self.assertEqual((w['ck_tube']['fill'], w['ck_water']['fill']), ('ok', 'ok'))
        self.assertEqual([c[0] for c in w['ck_tanks']['cells']], ['H2O', 'NaOH', 'H3BO3', 'Titrant', 'Steam gen. water'])
        self.assertEqual({c[2] for c in w['ck_tanks']['cells']}, {'OK'})
        self.assertEqual(w['ck_sum']['text'], 'All reagent levels OK')
        r.call(r.A, 'door.set_open', open=False)
        r.h.step(1.0)
        self.assertEqual(r.widgets()['ck_door']['fill'], 'ok')
        r.tap('back')
        self.assertEqual(r.page(), ('app', 'home'))
        orig = rt.STOCK_EXAMPLE
        try:                                                                    # less NaOH loaded than the method doses (70 ml)
            rt.STOCK_EXAMPLE = dict(orig, base_m3=20e-6)
            low = Rig()
        finally:
            rt.STOCK_EXAMPLE = orig
        low.tap('n_checkup')
        w = low.widgets()
        self.assertEqual((w['ck_tanks']['cells'][1][0], w['ck_tanks']['cells'][1][2]), ('NaOH', 'LOW'))
        self.assertEqual((w['ck_sum']['text'], w['ck_sum']['fill']), ('Refill the reagent tanks', 'error'))

    def test_guide_steps_follow_the_instrument(self):
        r = Rig(ready=False)                                                    # tube not clamped, door open
        r.tap('n_guide')
        self.assertEqual(r.page(), ('app', 'guide'))
        fills = lambda: [r.widgets()[f'g_step{i}']['fill'] for i in range(1, 8)]  # noqa: E731
        self.assertEqual(fills(), ['ok', 'ok', 'ok', 'panel', 'panel', 'panel', 'panel'])
        r.call(r.A, 'tube.set_clamped', clamped=True)
        r.h.step(1.0)
        r.call(r.A, 'door.set_open', open=False)
        r.h.step(1.0)
        self.assertEqual(fills()[3:5], ['ok', 'ok'])
        r.tap('g_go')
        self.assertEqual(r.page(), ('app', 'analysis'))
        r.tap('h_start')
        r.h.step(400.0)
        r.tap('back')
        r.tap('n_guide')
        self.assertEqual(fills(), ['ok'] * 7)


class EquivalenceAndRules(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        p, d = Rig(), Rig()
        p.tap('n_methods')
        p.row('m_list', '1')
        p.ov('Select')
        p.ov('OK')
        p.tap('h_start')                                                        # Select led to the analysis screen
        d.call(d.A, 'library.select', index=1)
        d.call(d.A, 'acquisition.start')
        snap = lambda h: {k: v for k, v in h.debug_snapshot().items() if k not in ('panel', '_requests', '_owners', '_principals', 'revision', '_operation_count', 'operation_id')}   # noqa: E731
        self.assertEqual(snap(p.h), snap(d.h))
        p.h.step(400.0)
        d.h.step(400.0)
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))

    def test_power_selector(self):
        r = Rig()
        r.switch('off')
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.touch(100, 100))
        self.assertEqual(int(r.h.panel.render('screen').max()), 0)
        r.switch('on')
        self.assertEqual(r.page(), ('app', 'home'))
        self.assertEqual(r.call(r.A, 'panel.touch', expect='rejected', display_id='screen', x=900, y=1)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        r = Rig()
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {'home': r.h.panel.save_png('screen', OUT / 'home.png')}     # the main menu
        r.tap('n_service')                                                      # service-only (brochure p.8): the device message
        self.assertEqual(r.message(), 'Service functions are for VELP service technicians.')
        self.assertIn('service-only', r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.tap('n_analysis')
        r.tap('h_start')
        r.h.step(200.0)
        a = r.h.panel.png_bytes('screen')[0]
        self.assertEqual(a, r.h.panel.png_bytes('screen')[0])
        self.assertEqual(copy.deepcopy(r.h).panel.png_bytes('screen')[0], a)
        renders['analysis'] = r.h.panel.save_png('screen', OUT / 'analysis.png')
        r.h.step(300.0)
        r.tap('back')
        r.tap('n_methods')
        renders['menu'] = r.h.panel.save_png('screen', OUT / 'menu.png')
        r.row('m_list', '5')
        r.ov('Select')
        renders['error'] = r.h.panel.save_png('screen', OUT / 'error.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')

    def test_capture_through_the_session(self):
        r = Rig()
        cap = r.call(r.A, 'panel.capture', display_id='screen')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (480, 688))   # portrait

    def test_spec_and_contract(self):
        spec = json.loads((rt.ASSET_DIR / 'panel_ui.json').read_text())
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS)), [])
        self.assertTrue(spec['approximate'])
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
