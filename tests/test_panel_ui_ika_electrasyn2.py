"""Front-panel tests for CHEM-016 ika_electrasyn2 (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_ika_electrasyn2.py -q

Every gesture goes through the session API (panel.press / panel.turn, the runtime's normal invoke path); the host steps the clock and the
evaluator prepares the vial.  Each check looks at the runtime state and at the screen: the structured frame (menu lines, run-screen widgets) and
the rendered pixels (lit text in the regions of the run-screen fields, the inverse menu cursor).  Themes: the key procedures of the menu survey
(new experiment guide and start, Smart Assist, cyclic voltammetry parameters with the unsupported start, lock); panel = direct capability;
refusals; saved experiments; graph; render determinism and the PNGs of outputs/panel_ui/ika_electrasyn2/; contract entries."""
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
from asset_runtime import ika_electrasyn2 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'electrasyn2_01'
RPM = 2 * math.pi / 60
OUT = ROOT / 'outputs/panel_ui/ika_electrasyn2'
SURVEY_TASKS = ['set up and start a constant-current or constant-voltage reaction', 'use Smart Assist to find a starting current',
                'run a cyclic voltammetry measurement', 'lock the controls']
DONE_TASKS = set()
SPEC = rt.PANEL_SPEC
GUIDE = [f['id'] for f in SPEC['menus']['main']['items'][0]['fields']]


def lit(img, rect):
    """Any bright pixel inside a widget rect of the dark TFT."""
    x, y, w, h = rect
    return bool((img[y:y + h, x:x + w].astype(int).sum(axis=2) > 450).any())


class Rig:
    def __init__(self, ready=True):
        self.h = rt.IkaElectrasyn2Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if ready:
            self.call(self.A, 'cap.set_lift', lift_m=0.08)
            self.call(self.E, 'sample.configure', **rt.EXAMPLE)
            self.call(self.A, 'cap.set_lift', lift_m=0.0)
            self.call(self.E, 'electrodes.configure', **rt.CAPS['electrodes.configure'][6])

    def call(self, sess, cap, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        expect = (expect,) if isinstance(expect, str) else expect
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        if r['status'] != 'rejected':
            assert self.h.time_s == t0, cap
            return r['result']
        return r

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='knob', detents=n)['accepted']

    def push(self):
        return self.press('knob')

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        """Non-empty text lines of the menu screens, blanks collapsed."""
        return [' '.join(x.split()) for x in self.frame()['displays']['tft']['lines'] if x.strip()]

    def widgets(self):
        """Run-screen fields shown now (frame) - each non-empty one must light pixels in its rect."""
        d = self.frame()['displays']['tft']
        img = self.h.panel.render('tft')
        out = {}
        for w in d.get('widgets', []):
            if w['type'] == 'label' and w['text'].strip():
                assert lit(img, w['rect']), w
                out[w['id']] = w['text']
        return out

    def editing(self):
        ed = self.frame()['ui']['editor']
        return None if ed is None else ed['field']

    def set_field(self, value_turns=0):
        if value_turns:
            assert self.turn(value_turns)
        return self.push()

    def state(self, k):
        return self.E.read(instance_id=INST, state_ids=[k])['observations'][k]['value']

    def step(self, s):
        self.h.step(float(s))

    def on(self):
        assert self.press('key_power')
        assert self.screen() == 'home'

    def menu_to(self, item_id, menu='main'):
        ids = [it['id'] for it in SPEC['menus'][menu]['items']]
        cur = self.frame()['ui']['menu']['index']
        if ids.index(item_id) != cur:
            assert self.turn(ids.index(item_id) - cur)
        assert self.frame()['ui']['menu']['item'] == item_id


def runtime_state(h):
    r = h.read('evaluator')
    return json.dumps(dict(read=r, method=h.method, experiment=h.experiment, memory=h.memory, cv=h.cv), sort_keys=True, default=float)


def guide(g, steps):
    """Walk the new-experiment guide: {field: detents} for the fields shown in order (0 = keep), pushing after each; the last push's result."""
    ok = None
    while g.editing() is not None:
        ok = g.set_field(steps.get(g.editing(), 0))
    return ok


class KeyProcedures(unittest.TestCase):
    def test_constant_current_reaction_by_the_guide(self):
        g = Rig()
        g.on()
        self.assertEqual(g.widgets()['mode'], 'Constant Current')
        self.assertTrue(g.press('key_menu'))
        self.assertEqual(g.lines()[:2], ['Menu', 'New experiment'])
        self.assertTrue(g.push())                                                       # new experiment
        self.assertEqual(g.editing(), 'type')
        self.assertTrue(g.frame()['displays']['tft']['blink'][1])                        # the value being chosen flashes
        guide(g, {'current': 50, 'duration': -2, 'mmol': -40, 'equiv': -10, 'alternate': 1, 'interval': -30, 'save': 1})
        self.assertEqual(g.screen(), 'edit')                                            # edit screen: start / edit
        self.assertEqual(g.frame()['ui']['menu']['item'], 'start')
        m = g.state('method.current_a'), g.state('method.stop_by'), g.state('method.charge_target_c')
        self.assertAlmostEqual(m[0], 0.015)                                             # 10 mA + 50 x 0.1 mA
        self.assertEqual(m[1], 'charge')
        self.assertAlmostEqual(m[2], 0.1 / 1000 * 1.0 * rt.F, places=6)                 # 0.10 mmol x 1.0 F/mol
        self.assertEqual(g.state('experiment.settings'), dict(duration_mode='charge', substrate_mmol=0.1, equivalents=1.0, alternate_polarity=True, interval_s=30.0))
        self.assertEqual(g.state('experiment.memory')[0]['method']['current_a'], 0.015)
        self.assertTrue(g.push())                                                       # start
        self.assertEqual(g.state('cycle.phase'), 'running')
        self.assertEqual(g.screen(), 'home')
        g.step(31.0)
        w = g.widgets()
        self.assertEqual((w['big_right'], w['big_right_unit'], w['big_charge_unit']), ('15.0', 'set mA', 'Charge Left F/mol'))
        self.assertEqual(w['pol_left'], '-')                                            # second 30 s interval: polarity reversed
        self.assertIn('charge_icon', w)
        self.assertAlmostEqual(float(w['big_charge']), (9.648533212 - 0.015 * 31) / 9.648533212, places=2)
        self.assertTrue(g.push())                                                       # push the knob: stop
        self.assertEqual(g.state('cycle.phase'), 'cancelled')
        DONE_TASKS.add('set up and start a constant-current or constant-voltage reaction')

    def test_constant_voltage_timer_and_speed_knob(self):
        g = Rig()
        g.on()
        self.assertTrue(g.turn(1))                                                      # 0 -> 50 rpm on the run screen
        self.assertTrue(g.turn(55))
        self.assertAlmostEqual(g.state('stir.target_rad_s'), 600 * RPM)
        self.assertEqual(g.widgets()['big_rpm'], '600')
        g.press('key_menu')
        g.push()
        guide(g, {'type': -1, 'voltage': 0, 'duration': 0, 'minutes': 5})
        self.assertEqual((g.state('method.mode'), g.state('method.stop_by'), g.state('method.duration_s')), ('constant_cell_voltage', 'time', 300.0))
        g.push()
        g.step(10.0)
        w = g.widgets()
        self.assertEqual((w['mode'], w['big_left_unit'], w['big_right_unit']), ('Constant Voltage', 'set V', 'act mA'))
        self.assertEqual(w['big_charge'], '0:04:50')
        self.assertIn('motor_icon', w)

    def test_smart_assist(self):
        g = Rig()
        g.on()
        g.press('key_menu')
        g.menu_to('assist')
        self.assertTrue(g.push())
        self.assertEqual(g.lines()[1], 'Power level medium (4 V)')
        guide(g, {'mmol': 50, 'equiv': 0})                                              # medium, 1.00 mmol, 2.0 F/mol
        self.assertEqual(g.screen(), 'edit')
        wr, cr, emf, res = g.h._redox()
        self.assertAlmostEqual(g.state('method.current_a'), round((4.0 - emf) / res / 1e-4) * 1e-4)
        self.assertAlmostEqual(g.state('method.charge_target_c'), 1.0 / 1000 * 2.0 * rt.F, places=6)
        self.assertEqual(g.state('method.mode'), 'constant_current')
        g.push()
        self.assertEqual(g.state('cycle.phase'), 'running')
        DONE_TASKS.add('use Smart Assist to find a starting current')

    def test_cyclic_voltammetry_parameters(self):
        g = Rig()
        g.on()
        self.assertTrue(g.press('key_cv'))                                              # CV button: CV parameters (p.20 example values)
        self.assertEqual(g.lines()[:4], ['Cyclic Voltammetry', 'Segment 3', 'Initial V 0.00 V', 'Direction Rising'])
        g.menu_to('upper_v', 'cv')
        g.push()
        g.turn(20)                                                                        # 1.50 -> 1.70 V
        g.push()
        self.assertEqual(g.state('cv.parameters')['upper_v'], 1.7)
        g.menu_to('sweep', 'cv')
        g.push()
        g.turn(-10)
        g.push()
        self.assertAlmostEqual(g.state('cv.parameters')['sweep_v_s'], 0.3)
        g.menu_to('lower_v', 'cv')
        g.push()
        g.turn(300)                                                                       # above the upper limit: refused
        self.assertFalse(g.push())
        self.assertEqual(g.lines()[-1], 'Invalid value')
        self.assertEqual(g.state('cv.parameters')['lower_v'], -1.0)
        g.menu_to('cv_start', 'cv')
        self.assertFalse(g.push())                                                        # needs the CV electrode set
        self.assertEqual(g.lines()[-1], 'Connect CV electrodes')
        self.assertIn('pro package', g.frame()['ui']['unsupported'])
        g.press('key_menu')
        g.press('key_menu')
        g.push()
        g.turn(1)                                                                          # Type: Cyclic Voltammetry in the guide
        g.push()
        self.assertEqual(g.screen(), 'cv')
        DONE_TASKS.add('run a cyclic voltammetry measurement')

    def test_lock_the_controls(self):
        g = Rig()
        g.on()
        self.assertTrue(g.press('key_lock'))
        self.assertTrue(g.state('controls.locked'))
        self.assertIn('lock_icon', g.widgets())
        self.assertFalse(g.press('key_menu'))
        self.assertFalse(g.turn(5))
        self.assertEqual(g.screen(), 'home')
        self.assertTrue(g.press('key_lock'))
        self.assertFalse(g.state('controls.locked'))
        self.assertTrue(g.press('key_menu'))
        DONE_TASKS.add('lock the controls')


class Equivalence(unittest.TestCase):
    def test_guide_equals_direct(self):
        a, b = Rig(), Rig()
        a.on()
        b.call(b.A, 'power.set', enabled=True)
        a.press('key_menu')
        a.push()
        guide(a, {'current': 20, 'duration': 0, 'minutes': 2, 'save': 2})
        b.call(b.A, 'method.configure', mode='constant_current', current_a=0.012, cell_voltage_v=4.0, voltage_limit_v=30.0, stop_by='time', duration_s=120.0,
               charge_target_c=0.02)
        b.call(b.A, 'experiment.configure', duration_mode='timer', substrate_mmol=None, equivalents=None, alternate_polarity=False, interval_s=60.0)
        b.call(b.A, 'experiment.save', slot=2)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.push()
        b.call(b.A, 'acquisition.start')
        a.step(60.0)
        b.step(60.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


class Rules(unittest.TestCase):
    def test_refusals_and_standby(self):
        g = Rig(ready=False)
        self.assertFalse(g.press('key_menu'))                                             # standby: only ON/OFF
        g.on()
        g.press('key_menu')
        g.push()
        self.assertFalse(guide(g, {'re': 1}))                                              # reference electrode: not in this package
        self.assertEqual(g.lines()[-1], 'No reference electrode')
        self.assertEqual(g.screen(), 'menu')
        g.press('key_menu')
        g.push()
        self.assertFalse(g.push())                                                         # start without vial and electrodes
        self.assertEqual(g.lines()[-1], 'Check vial/electrodes/lid')
        self.assertEqual(g.state('cycle.phase'), 'idle')
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='nope')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_saved_experiments_graph_and_back(self):
        g = Rig()
        g.on()
        g.press('key_menu')
        g.menu_to('saved')
        g.push()
        self.assertEqual(g.lines()[1:3], ['Memory 1 empty', 'Memory 2 empty'])
        self.assertFalse(g.push())                                                         # empty memory
        g.call(g.A, 'method.configure', mode='constant_current', current_a=0.005, cell_voltage_v=4.0, voltage_limit_v=30.0, stop_by='time', duration_s=60.0,
               charge_target_c=0.02)
        g.call(g.A, 'experiment.save', slot=3)
        g.call(g.A, 'method.configure', **rt.CAPS['method.configure'][6])
        g.turn(2)
        self.assertTrue(g.push())                                                          # load memory 3, edit screen
        self.assertEqual(g.screen(), 'edit')
        self.assertEqual(g.state('method.current_a'), 0.005)
        self.assertTrue(g.press('key_back'))                                              # back one level: the run screen
        self.assertEqual(g.screen(), 'home')
        g.push()                                                                           # edit screen, start
        g.push()
        g.step(20.0)
        self.assertTrue(g.press('key_graph'))
        plot = g.frame()['displays']['tft']['widgets'][0]
        self.assertEqual(plot['type'], 'plot')
        self.assertGreater(len(plot['series'][0]['points']), 10)
        self.assertTrue(g.press('key_graph'))
        self.assertEqual(g.screen(), 'home')

    def test_no_timeout(self):
        g = Rig()
        g.on()
        g.press('key_menu')
        g.push()
        g.step(600.0)
        self.assertEqual(g.screen(), 'menu')
        self.assertEqual(g.editing(), 'type')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.on()
            g.turn(56)
            g.press('key_menu')
            g.push()
            guide(g, {'current': 40, 'duration': -2, 'alternate': 0})
            g.push()
            g.step(45.0)
            return g
        a, b = scenario(), scenario()
        self.assertTrue((a.h.panel.render('tft') == b.h.panel.render('tft')).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('tft', OUT / 'home.png')}
        a.press('key_graph')
        shots['graph'] = a.h.panel.save_png('tft', OUT / 'graph.png')
        a.press('key_graph')
        a.push()
        a.press('key_menu')
        a.push()
        shots['menu'] = a.h.panel.save_png('tft', OUT / 'menu.png')
        a.press('key_back')
        a.press('key_cv')
        a.menu_to('cv_start', 'cv')
        a.push()
        shots['error'] = a.h.panel.save_png('tft', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_and_copies(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='tft')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        self.assertNotIn('panel', vars(g.h))

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.turn', 'panel.capture', 'panel.frame'})
        m = rt.export_ika_electrasyn2_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k, e in m['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'errors', 'state_effects', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], e[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/ika_electrasyn2.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('es2_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class CallerRule(unittest.TestCase):
    def test_a_run_started_on_the_panel_belongs_to_the_caller(self):
        g = Rig()
        g.on()
        g.turn(56)
        g.press('key_menu')
        g.push()
        guide(g, {'current': 40, 'duration': -2, 'alternate': 0})
        self.assertTrue(g.push())                                                        # started on the panel by the agent
        op = g.h.operation_id
        self.assertEqual((g.state('cycle.phase'), g.h._owners.get(op)), ('running', 'agent'))
        g.A.poll(op)                                                                      # the caller may follow it
        g.h.register_principal('agent_b', 'agent')
        for act in (lambda: g.h.session('agent_b').poll(op), lambda: g.h.session('agent_b').cancel(op, 'b1')):
            with self.assertRaises(rt.ActionRejected):                                    # another agent may not follow or cancel it
                act()
        self.assertEqual(g.state('cycle.phase'), 'running')
        g.A.cancel(op, 'a1')                                                              # the caller may cancel it
        self.assertNotEqual(g.state('cycle.phase'), 'running')

    def test_another_agent_cannot_stop_or_switch_off_the_run(self):
        """Sync audit 2026-10-19: another agent's knob push (stop) and ON/OFF key, and its direct acquisition.stop / power.set, are refused
        (state unchanged); the owner's knob push stops the run."""
        g = Rig()
        g.on()
        g.turn(56)
        g.press('key_menu')
        g.push()
        guide(g, {'current': 40, 'duration': -2, 'alternate': 0})
        self.assertTrue(g.push())
        g.step(5.0)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        before = runtime_state(g.h)
        self.assertFalse(g.call(B, 'panel.press', input_id='knob')['accepted'])
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED')
        self.assertEqual(g.lines()[-1], 'Not allowed: other user')                     # the refusal shows on the screen
        self.assertFalse(g.call(B, 'panel.press', input_id='key_power')['accepted'])
        self.assertFalse(g.call(B, 'panel.turn', input_id='knob', detents=5)['accepted'])                   # run screen: stirring speed
        for cap, args in (('acquisition.stop', {}), ('power.set', dict(enabled=False)), ('stir.set_speed', dict(speed_rad_s=600 * rt.RPM))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(runtime_state(g.h), before)
        self.assertTrue(g.push())                                                        # the owner's knob push: stop
        self.assertEqual(g.state('cycle.phase'), 'cancelled')


class AlternatingPolarity(unittest.TestCase):
    """Sync audit 2026-10-19 (manual p.15 step 7): with Alternate polarity the device swaps the two graphite electrodes every interval, so the
    fouling film grows on each electrode in turn and the passivation resistance stays lower than with a fixed polarity."""
    def run_cell(self, alternate, mode):
        g = Rig(ready=False)
        g.call(g.A, 'cap.set_lift', lift_m=0.08)
        g.call(g.E, 'sample.configure', **dict(rt.EXAMPLE, passivation_ohm_per_c=100.0))
        g.call(g.A, 'cap.set_lift', lift_m=0.0)
        g.call(g.E, 'electrodes.configure', **rt.CAPS['electrodes.configure'][6])
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'method.configure', mode=mode, current_a=0.01, cell_voltage_v=6.0, voltage_limit_v=30.0, stop_by='time', duration_s=300.0,
               charge_target_c=0.02)
        g.call(g.A, 'experiment.configure', duration_mode='timer', substrate_mmol=None, equivalents=None, alternate_polarity=alternate, interval_s=30.0)
        g.call(g.A, 'acquisition.start')
        g.step(265.0)                                                                    # 9th interval: normal polarity again
        mid = (g.state('electrochem.indicated_cell_voltage_v'), g.h.read('evaluator')['truth.resistance_ohm'], g.state('display.run')['polarity_reversed'])
        g.step(40.0)
        r = g.h.read('evaluator')
        return mid, r, list(g.h.film)

    def test_alternation_mitigates_passivation(self):
        (v_off, r_off, rev_off), end_off, film_off = self.run_cell(False, 'constant_current')
        (v_on, r_on, rev_on), end_on, film_on = self.run_cell(True, 'constant_current')
        self.assertEqual((rev_off, rev_on), (False, False))
        self.assertEqual(film_off[1], 0.0)                                               # fixed polarity: one electrode carries all the film
        self.assertAlmostEqual(film_off[0], end_off['truth.sample_charge_c'], places=9)
        self.assertAlmostEqual(film_on[0], film_on[1], delta=0.01)                       # alternating: half each
        self.assertAlmostEqual(film_on[0] + film_on[1], end_on['truth.sample_charge_c'], places=9)
        self.assertLess(r_on, r_off - 100.0)
        self.assertLess(v_on, v_off - 1.0)                                               # constant current: lower cell voltage
        self.assertAlmostEqual(end_on['truth.extent_mol'], end_off['truth.extent_mol'], places=12)   # same charge, same formal chemistry
        (_, _, _), cv_off, _ = self.run_cell(False, 'constant_cell_voltage')
        (_, _, _), cv_on, _ = self.run_cell(True, 'constant_cell_voltage')
        self.assertGreater(cv_on['truth.sample_charge_c'], cv_off['truth.sample_charge_c'] * 1.02)    # constant voltage: more current flows

    def test_reversed_interval_uses_the_other_electrode(self):
        g = Rig(ready=False)
        g.call(g.A, 'cap.set_lift', lift_m=0.08)
        g.call(g.E, 'sample.configure', **dict(rt.EXAMPLE, passivation_ohm_per_c=100.0))
        g.call(g.A, 'cap.set_lift', lift_m=0.0)
        g.call(g.E, 'electrodes.configure', **rt.CAPS['electrodes.configure'][6])
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'method.configure', mode='constant_current', current_a=0.01, cell_voltage_v=6.0, voltage_limit_v=30.0, stop_by='time',
               duration_s=300.0, charge_target_c=0.02)
        g.call(g.A, 'experiment.configure', duration_mode='timer', substrate_mmol=None, equivalents=None, alternate_polarity=True, interval_s=30.0)
        g.call(g.A, 'acquisition.start')
        g.step(29.0)
        r_before = g.h.read('evaluator')['truth.resistance_ohm']
        g.step(2.0)                                                                      # second interval: the fresh electrode works
        self.assertTrue(g.state('display.run')['polarity_reversed'])
        r_after = g.h.read('evaluator')['truth.resistance_ohm']
        self.assertAlmostEqual(r_before - r_after, 100.0 * (0.29 - 0.01), delta=0.2)    # film of electrode 0 (29 s x 10 mA) -> electrode 1 (1 s)


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
