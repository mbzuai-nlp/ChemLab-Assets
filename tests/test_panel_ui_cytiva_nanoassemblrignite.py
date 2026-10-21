"""front-panel tests for CHEM-060 cytiva_nanoassemblrignite (panel_ui.json + scripts/asset_runtime/_panel_ui.py on
scripts/asset_runtime/cytiva_nanoassemblrignite.py): python3 -m pytest tests/test_panel_ui_cytiva_nanoassemblrignite.py -q

Every gesture goes through the session API (panel.touch / panel.switch); the host (evaluator) declares the plant, installs the heating option,
inserts the cartridge, the syringes and the tubes and moves the block and the lid.  Themes: the seven key procedures of the menu survey (start the
instrument, preheat syringes, create or edit a recipe, quick run, start and monitor a formulation, interrupt a run, set date and time); recipe
delete, History re-run and pages, Settings > Syringes and the syringe-size flow effect, validation messages, the cartridge indicator and the heater
icon; panel = direct capability; refusals shown as the device message (state unchanged); time-outs; render determinism, evidence PNGs, contract."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import cytiva_nanoassemblrignite as rt  # noqa: E402

INST = 'ignite_01'
SCR = 'screen'
OUT = ROOT / 'outputs/panel_ui/cytiva_nanoassemblrignite'
SURVEY_TASKS = ['start the instrument', 'preheat syringes', 'create or edit a recipe', 'quick run', 'start and monitor a formulation', 'interrupt a run',
                'set date and time']
PARAMS = [('L', 'BD10'), ('C', 'BD3'), ('R', 'BD1'), ('Flow rate ratio', '3'), ('Total volume', '2'), ('Total flow rate', '4'), ('Dilution ratio L:(C+R)', '1'),
          ('Start waste', '0.2'), ('End waste', '0.05')]


class Rig:
    def __init__(self, on=True, load=True):
        self.h = rt.CytivaNanoassemblrigniteRuntime(INST)
        self.h.panel_capture_dir = tempfile.mkdtemp(prefix='ignite_panel_')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.call(self.E, 'heating.configure', installed=True, accept_host_attachment=True)
        if load:
            self.load()
        if on:
            assert self.switch('I')
            self.step(10.5)
            assert self.page() == ('main', 'main'), self.page()

    def load(self, dilution=True, syringes=None):
        """Host: cartridge, syringes, tubes, block down, lid closed."""
        if not self.val('lid.opened'):
            self.call(self.A, 'lid.set', opened=True)
        if not self.val('block.opened'):
            self.call(self.A, 'block.set', opened=True)
        self.call(self.E, 'cartridge.install', dilution=dilution, cartridge_id=f'chip{self.n}', accept_host_attachment=True)
        for x in (syringes or rt.SYRINGE_EXAMPLES):
            self.call(self.E, 'syringe.load', **x)
        self.call(self.E, 'tubes.install', sample_capacity_m3=50e-6, accept_host_attachment=True)
        self.call(self.A, 'block.set', opened=False)
        self.call(self.A, 'lid.set', opened=False)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        while dt > 1e-9:
            d = min(dt, 600.0)
            self.h.step(d)
            dt -= d

    def until(self, cond, limit=600.0, dt=0.5):
        t = 0.0
        while not cond():
            assert t < limit, 'time-out'
            self.h.step(dt)
            t += dt

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='switch_power', position=pos)['accepted']

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

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def labels(self):
        ov = self.overlay()
        return [b['label'] for b in ov['buttons']] if ov else []

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def msg(self):
        ov = self.overlay()
        return '\n'.join(ov['lines']) if ov and ov.get('kind') == 'dialog' else None

    def row(self, wid, text):
        for _ in range(20):
            w = self.widgets()[wid]
            for k, cells in enumerate(w['cells']):
                if any(text == c or text in c.split('\n') for c in cells):
                    return self.touch(w['rect'][0] + 10, w['rect'][1] + (k - 0) * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (text, w['cells'])
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(text)

    def type(self, text):
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        labels = {b['label'] for b in ov['buttons']}
        while self.overlay()['field']:
            self.ov('Del')
        if 'q' not in labels and 'Q' not in labels:
            for ch in text:
                assert self.ov(ch), ch
            return
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,.-':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            elif ch.isupper():
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] != 'upper':
                    self.ov('Shift')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] == 'upper':
                    self.ov('Shift')
                self.ov(ch)

    def enter(self, text):
        self.type(text)
        return self.ov('OK')

    def fill(self, params=PARAMS):
        """The parameter screen: syringes from the drop-down lists, values on the keypad."""
        for label, value in params:
            self.row('param_form', label)
            if self.overlay()['kind'] == 'keyboard':
                self.enter(value)
            else:
                self.ov(value)

    def png(self):
        return Path(self.call(self.A, 'panel.capture', display_id=SCR)['image']['path']).read_bytes()


def state(h):
    v = h.read('evaluator')
    v.pop('term.datetime', None)
    return json.dumps(v, sort_keys=True, default=float)


def quick_run(g, params=PARAMS):
    g.tap('t_quick')
    g.fill(params)
    g.tap('p_next')
    assert g.page()[1] == 'formulation', (g.page(), g.text('perr') if 'perr' in g.widgets() else None)


class KeyProcedures(unittest.TestCase):
    def test_start_the_instrument(self):
        g = Rig(on=False)
        self.assertEqual(g.page(), ('off', 'dark'))
        self.assertFalse(g.frame()['displays']['status_indicator']['on'])
        self.assertFalse(g.touch(240, 400))
        self.assertTrue(g.switch('I'))                                               # manual p.77
        self.assertEqual(g.page(), ('boot', 'starting'))
        self.assertTrue(g.frame()['displays']['status_indicator']['on'])
        g.step(9.0)
        self.assertEqual(g.page()[0], 'boot')
        g.step(1.5)
        self.assertEqual(g.page(), ('main', 'main'))
        self.assertEqual(g.text('cartridge'), 'Cartridge')                          # detected
        self.assertTrue(g.switch('O'))
        self.assertEqual(g.page(), ('off', 'dark'))

    def test_preheat_syringes(self):
        g = Rig()
        g.tap('heater')                                                              # Heater Control dialog
        self.assertEqual(g.overlay()['dialog'], 'd_heat')
        g.ov('Link')
        g.ov('Linked')
        g.ov('C')
        g.enter('65')
        self.assertTrue(g.val('heating.enabled'))
        self.assertEqual([round(t - 273.15) for t in (g.val('thermal.target_center_k'), g.val('thermal.target_right_k'))], [65, 65])
        g.ov('Close')
        self.assertIn('C 65  R 65', g.text('heater'))
        self.assertEqual(g.widgets()['heater']['fill'], 'cool')                     # below 45 C: blue
        g.until(lambda: g.val('thermal.center_k') > 273.15 + 46, limit=1200.0, dt=5.0)
        self.assertEqual(g.widgets()['heater']['fill'], 'danger')                   # above 45 C: red
        g.until(lambda: abs(g.val('thermal.center_k') - 338.15) < 5 and abs(g.val('thermal.right_k') - 338.15) < 5, limit=1200.0, dt=5.0)
        self.assertEqual(g.widgets()['heater']['fill'], 'warn')                     # within 5 C: orange
        g.tap('heater')                                                              # 30 C offset rule (manual p.83)
        g.ov('Link')
        g.ov('Not linked')
        g.ov('C')
        g.enter('25')
        self.assertEqual(round(g.h.effective[0] - 273.15), 35)
        g.ov('C')
        g.ov('Del') if g.overlay()['field'] else None
        g.ov('OK')                                                                   # empty = Off
        g.ov('R')
        while g.overlay()['field']:
            g.ov('Del')
        g.ov('OK')
        self.assertFalse(g.val('heating.enabled'))

    def test_create_or_edit_a_recipe(self):
        g = Rig()
        g.tap('t_recipes')
        g.row('recipe_list', '3')
        self.assertFalse(g.widgets()['r_run']['enabled'])                           # empty slot: Run and Delete dimmed
        g.tap('r_edit')
        self.assertEqual(g.page()[1], 'params')
        g.row('param_form', 'Recipe name')
        g.enter('LNP A')
        g.fill(PARAMS)
        self.assertIn('C: 1.50 mL  3.00 mL/min', g.text('chvals'))                  # calculated channel values
        self.assertEqual(g.text('perr'), '')
        g.tap('p_save')
        self.assertEqual(g.page()[1], 'recipes')
        r = g.val('term.recipes')[2]
        self.assertEqual((r['name'], r['params']['syringe_c'], r['params']['total_volume_ml']), ('LNP A', 'BD3', 2.0))
        g.row('recipe_list', '3')
        g.tap('r_edit')                                                              # edit: the stored values
        g.row('param_form', 'Total flow rate')
        g.enter('6')
        g.tap('p_save')
        self.assertEqual(g.val('term.recipes')[2]['params']['total_flow_ml_min'], 6.0)

    def test_quick_run(self):
        g = Rig()
        g.tap('t_quick')
        g.fill(PARAMS[:3])
        self.assertIn('Flow rate ratio is missing', g.text('perr'))                 # validation in red
        g.tap('p_next')
        self.assertIn('Correct the errors', g.msg())
        g.ov('OK')
        g.fill(PARAMS[3:])
        g.tap('p_next')
        self.assertEqual(g.page()[1], 'formulation')
        run = g.val('term.run')
        self.assertEqual((run['name'], run['dilution']), ('Quick Run', True))
        self.assertAlmostEqual(g.h.method['flow_m3_s'], 4e-6 / 60, places=15)
        self.assertEqual(g.val('cartridge.state'), 'acceptable')

    def test_start_and_monitor_a_formulation(self):
        g = Rig()
        quick_run(g)
        self.assertIn('[x] Close the lid', g.text('f_steps'))
        self.assertEqual(g.widgets()['f_start']['style'], 'ok')                      # START green
        g.tap('f_start')
        self.assertEqual(g.text('f_status'), 'Working')
        g.step(20.0)
        self.assertGreater(g.widgets()['f_progress']['fraction'], 0.0)
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertEqual(g.text('f_status'), 'Completed')
        self.assertAlmostEqual(g.widgets()['f_progress']['fraction'], 1.0, places=6)
        self.assertAlmostEqual(g.val('volume.sample_m3'), 3.47e-6, delta=1e-12)       # same routing as the direct run
        h = g.val('term.history')[0]
        self.assertEqual((h['name'], h['result'], h['cartridge']), ('Quick Run', 'SUCCESS', 'NxGen Dilution'))
        self.assertEqual(g.val('cartridge.state'), 'unacceptable')                    # used cartridge

    def test_interrupt_a_run(self):
        g = Rig()
        quick_run(g)
        g.tap('f_start')
        g.step(10.0)
        g.tap('f_stop')                                                              # STOP: all actions stop
        self.assertEqual(g.text('f_status'), 'Interrupted')
        self.assertIn('safe mode', g.text('f_errors'))
        g.call(g.A, 'lid.set', opened=True)
        g.tap('f_reset')                                                             # lid open: refused
        self.assertIn('Cannot formulate', g.msg())
        g.ov('OK')
        g.call(g.A, 'lid.set', opened=False)
        g.tap('f_reset')
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertEqual(max(g.val(f'paddle.{c}_m') for c in rt.CHANNELS), 0.0)
        self.assertEqual(g.val('term.history')[0]['result'], 'INCOMPLETE')
        g2 = Rig()                                                                   # opening the lid interrupts too
        quick_run(g2)
        g2.tap('f_start')
        g2.step(10.0)
        g2.call(g2.A, 'lid.set', opened=True)
        self.assertEqual(g2.text('f_status'), 'Interrupted')

    def test_set_date_and_time(self):
        g = Rig()
        g.tap('t_settings')
        g.tap('s_dt')
        g.row('dt_form', 'Hour')
        g.enter('9')
        g.row('dt_form', 'Minute')
        g.enter('30')
        g.row('dt_form', 'PM')
        g.ov('PM')
        g.tap('dt_save')
        self.assertEqual(g.page()[1], 'settings')
        self.assertEqual(g.val('term.datetime')[11:16], '21:30')
        self.assertIn('21:30', g.text('clock'))


class OtherFunctions(unittest.TestCase):
    def test_recipe_delete_and_run(self):
        g = Rig()
        g.call(g.A, 'recipe.save', slot=1, name='LNP B', params=dict(rt.PARAM_EXAMPLE))
        g.tap('t_recipes')
        g.row('recipe_list', '1')
        g.tap('r_run')
        self.assertEqual(g.page()[1], 'formulation')
        self.assertEqual(g.text('title') if 'title' in g.widgets() else g.disp().get('title'), 'LNP B')
        g.tap('f_home')
        g.tap('t_recipes')
        g.row('recipe_list', '1')
        g.tap('r_delete')
        g.ov('No')
        self.assertIsNotNone(g.val('term.recipes')[0])
        g.tap('r_delete')
        g.ov('Yes')
        self.assertIsNone(g.val('term.recipes')[0])

    def test_history_rerun_and_pages(self):
        g = Rig()
        quick_run(g)
        g.tap('f_start')
        g.until(lambda: g.val('cycle.phase') != 'running')
        g.tap('f_home')
        g.tap('t_history')
        self.assertEqual(len(g.widgets()['hist_list']['cells']), 1)
        self.assertIn('SUCCESS', g.widgets()['hist_list']['cells'][0][1])
        g.row('hist_list', 'Quick Run')
        g.tap('h_run')                                                               # re-run the parameters
        self.assertEqual(g.page()[1], 'formulation')
        self.assertEqual(g.val('term.run')['params'], g.val('term.history')[0]['params'])
        g.h.term['history'] = [dict(g.h.term['history'][0], operation_id=f'x{i}', result='SUCCESS') for i in range(20)]
        g.tap('f_home')
        g.tap('t_history')
        self.assertEqual(g.text('hist_page'), 'Page 1 / 3')
        g.tap('h_next')
        g.tap('h_next')
        self.assertEqual(g.text('hist_page'), 'Page 3 / 3')
        self.assertEqual(len(g.widgets()['hist_list']['cells']), 4)
        g.tap('h_prev')
        self.assertEqual(g.text('hist_page'), 'Page 2 / 3')

    def test_syringe_list_and_size_mismatch(self):
        g = Rig()
        g.tap('t_settings')
        g.tap('s_syr')
        g.row('syr_list', '[x] BD1')                                                 # remove BD1 from the drop-down lists
        g.tap('syr_back')
        self.assertNotIn('BD1', g.val('term.syringes_enabled'))
        g.tap('s_back')
        g.tap('t_quick')
        g.row('param_form', 'R')
        self.assertNotIn('BD1', g.labels())
        g.ov('BD3')                                                                  # R set to 3 mL, the loaded R syringe is 1 mL
        g.fill([p for p in PARAMS if p[0] != 'R'])
        g.tap('p_next')
        g.tap('f_start')
        g.until(lambda: g.val('cycle.phase') != 'running')
        s = g.h._product()
        self.assertAlmostEqual(g.h.scale[2], (4.7 / 9.0) ** 2, places=12)            # the pusher speed follows the selected syringe
        self.assertLess(s['initial_m3'][2] - s['syringe_m3'][2], 0.5e-6 * 0.5)

    def test_validation_messages(self):
        g = Rig()
        g.tap('t_quick')
        g.fill([('C', 'BD1'), ('R', 'BD1'), ('Flow rate ratio', '3'), ('Total volume', '2'), ('Total flow rate', '25'), ('Start waste', '0.2'),
                ('End waste', '0.05')])
        err = g.text('perr')
        self.assertIn('Total flow rate must be 0.1 to below 20 mL/min', err)
        self.assertIn('C volume exceeds the syringe size', err)
        g.fill([('L', 'BD10')])
        self.assertIn('Some fields are missing', g.text('perr'))                     # L without a dilution ratio (manual p.110)

    def test_wrong_cartridge_type(self):
        g = Rig(load=False)
        g.load(dilution=False)
        quick_run(g)                                                                 # the run needs the NxGen Dilution cartridge
        self.assertEqual(g.val('cartridge.state'), 'unacceptable')
        self.assertEqual(g.text('cartridge'), 'Cartridge X')
        self.assertFalse(g.widgets()['f_start']['enabled'])

    def test_heater_not_attached(self):
        g = Rig(on=False)
        g.h.heater_installed = False
        g.switch('I')
        g.step(10.5)
        g.tap('heater')
        self.assertIn('Heater Not Attached', g.msg())


class Contracts(unittest.TestCase):
    def test_panel_and_direct_calls_give_the_same_state(self):
        a, b = Rig(), Rig()
        quick_run(a)
        a.tap('f_start')
        a.step(60.0)
        b.call(b.A, 'run.load', source='quick', name='Quick Run', params=dict(rt.PARAM_EXAMPLE))
        b.call(b.A, 'acquisition.start')
        b.step(60.0)
        sa, sb = json.loads(state(a.h)), json.loads(state(b.h))
        for v in (sa, sb):
            v.pop('term.history')
        self.assertEqual(sa, sb)
        self.assertEqual(a.h._product(), b.h._product())

    def test_refusals_change_nothing(self):
        g = Rig()
        g.tap('t_quick')
        g.fill(PARAMS[:2])
        before = state(g.h)
        g.tap('p_next')
        self.assertIn('Correct the errors', g.msg())
        self.assertEqual(before, state(g.h))
        r = g.call(g.A, 'recipe.save', expect='rejected', slot=1, name='x', params=dict(rt.PARAM_EXAMPLE, total_volume_ml=50.))
        self.assertEqual(r['error']['code'], 'PARAMETERS_INVALID')
        self.assertEqual(before, state(g.h))

    def test_power_gate(self):
        g = Rig(on=False)
        before = state(g.h)
        self.assertFalse(g.touch(100, 100))
        self.assertFalse(g.switch('O'))
        self.assertEqual(before, state(g.h))


class CallerRules(unittest.TestCase):
    """Sync audit 2026-10-19, rule 2: a run started from the screen belongs to the session caller; another agent cannot stop it or switch the
    instrument off, through the panel or directly (state unchanged)."""
    def rig(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        g.B = g.h.session('agent_b')
        quick_run(g)
        g.tap('f_start')
        g.step(10.0)
        self.assertEqual(g.val('cycle.phase'), 'running')
        return g

    def test_a_panel_started_run_belongs_to_the_caller(self):
        g = self.rig()
        op = g.h.operation_id
        self.assertEqual(g.h._owners.get(op), 'agent')
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            g.B.poll(op)
        with self.assertRaises(rt.ActionRejected):
            g.B.cancel(op, 'b1')
        self.assertEqual(g.val('cycle.phase'), 'running')
        r = g.A.cancel(op, 'a1')
        self.assertEqual(r['status'], 'succeeded')
        self.assertEqual(g.val('cycle.phase'), 'cancelled')

    def test_another_agent_cannot_stop_or_switch_off_the_run(self):
        g = self.rig()
        before = state(g.h)
        x, y, w, h = g.widgets()['f_stop']['rect']
        self.assertFalse(g.call(g.B, 'panel.touch', display_id=SCR, x=x + w // 2, y=y + h // 2)['accepted'])     # B taps STOP
        self.assertEqual(g.msg(), "Not allowed: another user's run is in progress.")                     # the refusal shows on the screen
        g.ov('OK')
        self.assertEqual(g.call(g.B, 'acquisition.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(g.B, 'heating.set', expect='rejected', enabled=True, targets_k=[298.15, 318.15])['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(before, state(g.h))
        self.assertEqual(g.text('f_status'), 'Working')
        g.tap('f_stop')                                                                                  # the owner may stop it
        self.assertEqual(g.text('f_status'), 'Interrupted')
        g2 = self.rig()                                                                                   # the evaluator is not restricted
        g2.call(g2.E, 'acquisition.stop')
        self.assertEqual(g2.val('cycle.phase'), 'cancelled')
        g3 = self.rig()                                                                                   # the power switch is physical:
        self.assertTrue(g3.call(g3.B, 'panel.switch', input_id='switch_power', position='O')['accepted'])   # not refused, a power loss
        self.assertFalse(g3.val('power.enabled'))
        self.assertNotEqual(g3.val('cycle.phase'), 'running')


    def test_another_agent_may_open_the_lid_and_the_instrument_interrupts_the_run(self):
        """Sweep 2026-10-19: opening the lid is a physical action (not refused); the instrument's own interlock interrupts the run (safe mode)."""
        g = self.rig()
        g.call(g.B, 'lid.set', opened=True)
        self.assertTrue(g.val('lid.opened'))
        self.assertEqual(g.text('f_status'), 'Interrupted')
        self.assertIn('safe mode', g.text('f_errors'))


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        g = Rig()
        a = g.h.panel.png_bytes(SCR)[0]
        self.assertEqual(a, copy.deepcopy(g.h).panel.png_bytes(SCR)[0])
        self.assertEqual(g.png(), a)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'home.png').write_bytes(a)
        g.tap('t_quick')
        g.fill(PARAMS)
        (OUT / 'menu.png').write_bytes(g.png())
        g.row('param_form', 'Total volume')
        g.enter('40')
        (OUT / 'error.png').write_bytes(g.png())
        g.row('param_form', 'Total volume')
        g.enter('2')
        g.tap('p_next')
        g.tap('f_start')
        g.step(20.0)
        (OUT / 'formulation.png').write_bytes(g.png())
        g.tap('f_stop')
        g.tap('f_reset')
        g.until(lambda: g.val('cycle.phase') != 'running')
        g.tap('f_home')
        g.tap('heater')
        (OUT / 'heater.png').write_bytes(g.png())
        (OUT / 'renders.json').write_text(json.dumps({f.name: dict(sha256=hashlib.sha256(f.read_bytes()).hexdigest(), size=[480, 800])
                                                      for f in sorted(OUT.glob('*.png'))}, indent=1) + '\n')

    def test_spec_and_contract(self):
        spec = rt.PANEL_SPEC
        caps = set(rt.CAPS) | set(rt.TCAPS)
        self.assertEqual(rt._panel_ui.check_spec(spec, caps), [])
        self.assertTrue(set(rt._panel_ui.spec_capabilities(spec)) <= caps)
        for pid, p in spec['screens']['main']['nav']['pages'].items():
            self.assertTrue(p.get('evidence'), pid)
        meta = rt.export_cytiva_nanoassemblrignite_contract_metadata()
        self.assertEqual(set(meta['capabilities']), set(rt.ALL_CAPS))
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        if c['asset_version'] != '1.0.0':
            self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
            self.assertTrue(set(rt.ALL_CAPS) <= set(c['capabilities']))
            self.assertIn('chemistry_lab.panel_ui', c['extensions'])
            self.assertIsNone(c['artifacts']['panel_engine']['sha256'])

    def test_all_survey_procedures_done(self):
        names = {m for m in dir(KeyProcedures) if m.startswith('test_')}
        for t in SURVEY_TASKS:
            self.assertIn('test_' + t.replace(' ', '_'), names, t)


if __name__ == '__main__':
    unittest.main()
