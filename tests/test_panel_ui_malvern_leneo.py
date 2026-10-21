"""Front-panel tests for CHEM-050 malvern_leneo (approximate panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/malvern_leneo.py):
python3 -m pytest tests/test_panel_ui_malvern_leneo.py -q

Gestures only through the session API (panel.touch / panel.press); the host (evaluator) declares the fast teaching plant and loads the crucible.
Themes: the survey procedure (run a fusion with a stored program) done only on the touch screen; supervisor level and password; program edit /
delete; preheat and heat shut-off timers (time-outs); refusals and the emergency stop; unsupported items; panel = direct command; rendering."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import malvern_leneo as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'leneo_01'
TS = 'touchscreen'
OUT = ROOT / 'outputs/panel_ui/malvern_leneo'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
FAST = dict(rt.PLANT_EXAMPLE, chamber_C_J_K=100., crucible_C_J_K=10., receiver_C_J_K=10., chamber_UA_W_K=.1, charge_UA_W_K=30., fan_UA_W_K=10., heater_W=4800.,
            supply_V=240., controller_W_K=1000., solidus_K=500., liquidus_K=600., latent_J_kg=10000., dissolution_rate_s=2., viscosity_Pa_s=.1,
            viscosity_activation_K=0., pour_kg_s=.005, solution_rate_s=2.)
FAST_PROGRAM = {'Program name': 'Fast 427', 'Fusion temperature': 427, 'Heating time': 0.1, 'Fusion time': 0.2, 'Forced cooling time': 0}


class Rig:
    def __init__(self, on=True):
        self.h = rt.MalvernLeneoRuntime()
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.host('plant.configure', **FAST)
        self.host('sample.configure', **rt.SAMPLE_EXAMPLE)
        if on:
            assert self.press('key_power')
            self.h.step(1)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        return r['result'] if expect == 'succeeded' else r

    def host(self, cap, **args):
        return self.call(self.E, cap, **args)

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def widgets(self):
        return {w['id']: w for w in self.frame()['displays'][TS]['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TS, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, ww, hh = self.widgets()[wid]['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def press(self, key):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=0.1)['accepted']

    def overlay(self):
        return self.frame()['displays'][TS].get('overlay')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label and b['id'] != 'field':
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in self.overlay()["buttons"]]}')

    def ov_row(self, label):
        for b in self.overlay()['buttons']:
            if b.get('style') == 'row' and b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(label)

    def msg(self):
        return ' '.join(self.overlay()['lines'])

    def row(self, wid, first):
        for _ in range(20):                                                # back to the top of the list first
            w = self.widgets()[wid]
            if not w['scroll']:
                break
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        for _ in range(20):
            w = self.widgets()[wid]
            firsts = [c[0] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def type(self, text):
        labels = {b['label'] for b in self.overlay()['buttons']}
        if 'q' not in labels and 'Q' not in labels and '7' in labels:
            for ch in text:
                assert self.ov(ch), ch
            return
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit():
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if ch.isupper() != (self.overlay()['mode'] == 'upper'):
                    self.ov('Shift')
                self.ov(ch)

    def set_row(self, wid, label, value):
        if self.overlay() is not None and self.overlay()['kind'] == 'dialog':
            self.ov_row(label)
        else:
            self.row(wid, label)
        if self.overlay()['kind'] == 'keyboard':
            self.type(str(value))
            assert self.ov('OK'), self.overlay()
        else:
            assert self.ov(value), (label, value)

    def login(self, password='0000'):
        if self.page()[1] != 'settings':
            self.tap(f'{self.page()[1]}_hd_settings')
        self.tap('s_login')
        self.set_row(None, 'Password', password)
        return self.ov('OK')

    def new_program(self, **fields):
        if self.page()[1] != 'programs':
            self.tap(f'{self.page()[1]}_hd_programs')
        self.tap('pr_new')
        for label, value in fields.items():
            self.set_row('pg_rows', label, value)
        return self.tap('pg_save')

    def run_to_end(self, limit=600):
        t = 0
        while self.h.phase == 'running' and t < limit:
            self.h.step(1)
            t += 1


class SurveyProcedure(unittest.TestCase):
    def test_run_a_fusion_with_a_stored_program(self):
        r = Rig()
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertTrue(r.frame()['displays']['power_light']['on'])
        self.assertTrue(r.login())
        self.assertTrue(r.new_program(**FAST_PROGRAM))
        self.assertEqual(r.page()[1], 'programs')
        self.assertEqual(r.val('programs.names'), ['Fast 427'])
        r.row('pr_list', 'Fast 427')
        self.assertTrue(r.ov('Load'))
        self.assertEqual((r.val('program.current'), r.val('method.configured')), ('Fast 427', True))
        m = r.h.method
        self.assertEqual([s['end_temperature_K'] for s in m['segments']], [700.15, 700.15])
        self.assertEqual([s['duration_s'] for s in m['segments']], [6.0, 12.0])
        self.assertEqual(r.page()[1], 'home')
        self.assertIn('Fast 427', r.text('h_prog'))
        self.assertTrue(r.tap('h_door'))                                   # close the door
        r.h.step(2)
        self.assertIn('Door: closed', r.text('h_prog'))
        self.assertTrue(r.tap('h_fusion'))                                 # one-touch start
        self.assertEqual(r.val('cycle.phase'), 'running')
        self.assertTrue(r.val('door.locked'))
        self.assertIn('Door: locked', r.text('h_prog'))
        r.h.step(5)
        self.assertIn('Fusion (heating)', r.text('h_stage'))
        r.run_to_end()
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertEqual(r.text('h_stage'), 'Completed: retrieve the bead')
        self.assertFalse(r.val('door.locked'))
        self.assertTrue(r.tap('h_door'))
        r.h.step(2)
        self.assertGreater(r.host('product.collect', fraction=1.0, recipient_id='XRF')['transferred_kg'], 0)


class LevelsAndPrograms(unittest.TestCase):
    def test_supervisor_level_and_password(self):
        r = Rig()
        r.tap('home_hd_programs')
        r.tap('pr_new')
        self.assertEqual(r.msg(), 'Supervisor level required: log in with the supervisor password (Settings).')
        r.ov('OK')
        self.assertEqual(r.page()[1], 'programs')
        self.assertFalse(r.login('1111'))
        self.assertEqual(r.msg(), 'Wrong password.')
        r.ov('OK')
        r.ov('Cancel')
        self.assertTrue(r.login('0000'))
        self.assertEqual(r.val('access.level'), 'supervisor')
        self.assertIn('[Supervisor]', r.frame()['displays'][TS]['title'])
        r.tap('s_password')
        r.ov_row('New password')
        r.type('12')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['error'], '4 to 12 characters')
        r.ov('Cancel')
        r.ov('Cancel')
        r.tap('s_password')
        r.set_row(None, 'New password', '4321')
        self.assertTrue(r.ov('OK'))
        self.assertEqual(r.h.password, '4321')
        r.tap('s_login')                                                  # log out
        self.assertEqual(r.val('access.level'), 'operator')
        r.tap('s_timers')
        self.assertEqual(r.msg(), 'Supervisor level required: log in with the supervisor password (Settings).')
        r.ov('OK')
        self.assertFalse(r.login('0000'))
        r.ov('OK')
        r.ov('Cancel')
        self.assertTrue(r.login('4321'))

    def test_edit_and_delete_a_program(self):
        r = Rig()
        r.login()
        r.tap('settings_hd_home')
        r.new_program(**FAST_PROGRAM)
        r.row('pr_list', 'Fast 427')
        r.ov('Edit')
        self.assertEqual(r.page()[1], 'program_edit')
        buf = r.frame()['ui']['forms']['f_prog']
        self.assertEqual((buf['pg_name'], buf['pg_temp'], buf['pg_fusion_min'], buf['pg_heat_min']), ('Fast 427', 427, 0.2, 0.1))
        r.set_row('pg_rows', 'Fusion temperature', 450)
        r.set_row('pg_rows', 'Heating rate', 'Maximum')
        r.set_row('pg_rows', 'Preparation', 'Solution (AA / ICP)')
        self.assertTrue(r.tap('pg_save'))
        m = r.h.programs['Fast 427']
        self.assertEqual((m['segments'][0]['duration_s'], m['segments'][1]['end_temperature_K'], m['output']), (0.1, 723.15, 'borate_solution'))
        r.row('pr_list', 'Fast 427')
        r.ov('Delete')
        self.assertEqual(r.val('programs.names'), [])
        self.assertEqual(r.widgets()['pr_list']['cells'], [])

    def test_magnetic_stirring_speed(self):
        """1.2.0: the stirring speed of a program reaches the method and sets the dissolution rate of a solution program."""
        r = Rig()
        r.login()
        r.tap('settings_hd_programs')
        self.assertTrue(r.new_program(**FAST_PROGRAM, **{'Preparation': 'Solution (AA / ICP)', 'Magnetic stirring speed': 100}))
        self.assertEqual(r.h.programs['Fast 427']['stirring_fraction'], 1.0)
        r.row('pr_list', 'Fast 427')
        r.ov('Edit')
        self.assertEqual(r.frame()['ui']['forms']['f_prog']['pg_stir'], 100)
        r.set_row('pg_rows', 'Magnetic stirring speed', 0)
        self.assertTrue(r.tap('pg_save'))
        r.row('pr_list', 'Fast 427')
        self.assertTrue(r.ov('Load'))
        self.assertEqual(r.h.method['stirring_fraction'], 0.0)
        self.assertAlmostEqual(r.h.stir_factor(), rt.STIR_FLOOR)
        self.assertNotIn('stirrer', r.frame()['ui']['unsupported'] or '')

    def test_unsupported_items(self):
        r = Rig()
        r.login()
        r.tap('settings_hd_programs')
        r.tap('programs_hd_settings')
        r.tap('s_usb')
        self.assertEqual(r.msg(), 'No USB storage device connected.')
        self.assertIn('USB', r.frame()['ui']['unsupported'])


class StirringModel(unittest.TestCase):
    def test_stirring_speed_sets_the_dissolution_rate(self):
        """1.2.0: rate = solution_rate_s * max(floor, sqrt(speed / default)); without the field the 1.1.0 host rate."""
        out = {}
        for stir in (None, 0.5, 1.0, 0.0):
            r = rt.MalvernLeneoRuntime()
            m = copy.deepcopy(rt.METHOD_EXAMPLE)
            m.update(segments=[dict(duration_s=6.0, end_temperature_K=700.0), dict(duration_s=12.0, end_temperature_K=700.0)], output='borate_solution',
                     host_pour_profiles=[dict(angle_rad=a, duration_s=4.0) for a in (1.6, 1.8, 2.0, 2.2)], minimum_cooling_s=2.0)
            if stir is not None:
                m['stirring_fraction'] = stir
            s = dict(rt.SAMPLE_EXAMPLE, solvent_kg=0.1)
            for cap, args in (('plant.configure', dict(FAST, solution_rate_s=0.01)), ('method.configure', m), ('sample.configure', s),
                              ('power.set', dict(enabled=True)), ('door.set_open', dict(open=False))):
                r.invoke(cap, **args)
            r.step(1)
            r.invoke('acquisition.start')
            while r.stage != 'cooling':
                r.step(0.5)
            u0 = r.rundissolved
            r.step(20)
            out[stir] = r.rundissolved / u0
        self.assertAlmostEqual(out[None], out[0.5], 12)
        for stir, f in ((None, 1.0), (1.0, 2 ** 0.5), (0.0, rt.STIR_FLOOR)):
            self.assertAlmostEqual(-__import__('math').log(out[stir]) / 20.0, 0.01 * f, 8)


class CallerRules(unittest.TestCase):
    """Sync audit rule 2 (1.2.0): a cycle started with the Fusion tile belongs to the calling session (poll / cancel; cycle.operation_id);
    another agent can neither poll it nor stop it, switch off or change the cooling air (screen or direct: state unchanged); the emergency
    stop and the evaluator stay unrestricted."""

    def started(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        r.B = r.h.session('agent_b')
        r.login()
        r.new_program(**FAST_PROGRAM)
        r.row('pr_list', 'Fast 427')
        r.ov('Load')
        r.tap('h_door')
        r.h.step(2)
        self.assertTrue(r.tap('h_fusion'))
        op = r.val('cycle.operation_id')
        self.assertEqual(r.h._owners.get(op), 'agent')
        return r, op

    def tap_b(self, r, wid):
        x, y, w, h = r.widgets()[wid]['rect']
        return r.call(r.B, 'panel.touch', display_id=TS, x=x + w // 2, y=y + h // 2)['accepted']

    def test_owner_polls_and_cancels(self):
        r, op = self.started()
        self.assertEqual(r.A.poll(op)['status'], 'running')
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'cancelled')

    def test_other_agent_is_refused(self):
        r, op = self.started()
        r.h.step(1)
        before = r.h.read('evaluator')
        with self.assertRaises(rt.ActionRejected):
            r.B.poll(op)
        self.tap_b(r, 'h_stop')                                               # B confirms Stop on the shared screen
        x, y, w, h = next(b for b in r.overlay()['buttons'] if b['label'] == 'Yes')['rect']
        r.call(r.B, 'panel.touch', display_id=TS, x=x + w // 2, y=y + h // 2)
        self.assertEqual(r.msg(), 'This cycle was started by another user: it cannot be stopped or changed from here.')
        r.ov('OK')
        for cap, args in (('acquisition.stop', {}), ('cooling.set_air', {'fraction': 0.2})):
            self.assertEqual(r.call(r.B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h.read('evaluator'), before)
        r.tap('h_stop')
        r.ov('Yes')                                                           # the owner stops it
        self.assertEqual(r.val('cycle.phase'), 'cancelled')

    def test_main_power_switch_is_a_hard_switch(self):
        """Sweep 2026-10-19: the green main power switch cuts the power; another agent's switch-off is not refused and interrupts the cycle."""
        for how in ('key', 'power.set', 'power.toggle'):
            r, op = self.started()
            r.h.step(1)
            if how == 'key':
                self.assertTrue(r.call(r.B, 'panel.press', input_id='key_power')['accepted'])
            else:
                r.call(r.B, how, **({'enabled': False} if how == 'power.set' else {}))
            self.assertEqual((r.val('power.enabled'), r.val('cycle.phase')), (False, 'cancelled'), how)

    def test_emergency_stop_and_evaluator_stay_open(self):
        r, _ = self.started()
        r.call(r.E, 'acquisition.stop')
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        r2, _ = self.started()
        self.assertTrue(r2.call(r2.B, 'panel.press', input_id='key_emergency')['accepted'])
        self.assertEqual(r2.val('cycle.phase'), 'failed')


class Timers(unittest.TestCase):
    def test_preheat_and_heat_shut_off(self):
        r = Rig()
        r.login()
        r.tap('s_timers')
        r.set_row('tm_rows', 'Preheat timer', 'On')
        r.set_row('tm_rows', 'Preheat starts in', 1)
        r.set_row('tm_rows', 'Preheat temperature', 300)
        r.set_row('tm_rows', 'Heat shut-off after', 2)
        self.assertTrue(r.tap('tm_save'))
        self.assertEqual((r.val('timers.preheat_at_s'), r.val('timers.shutoff_after_s')), (r.h.time_s + 60.0, 120.0))
        r.tap('settings_hd_home')
        self.assertEqual(r.text('h_preheat'), 'Preheat starts in 1 min')
        t0 = r.h.chamber
        r.h.step(59)
        self.assertAlmostEqual(r.h.chamber, t0, delta=0.5)                # waiting: no heating
        self.assertFalse(r.val('heating.preheat_active'))
        r.h.step(60)
        self.assertTrue(r.val('heating.preheat_active'))
        self.assertEqual(r.text('h_preheat'), 'Preheat to 300 °C')
        self.assertAlmostEqual(r.val('panel.temperature_c'), 300.0, delta=5.0)
        r.h.step(70)
        self.assertFalse(r.val('heating.preheat_active'))                 # heat shut-off timer elapsed
        self.assertEqual(r.text('h_preheat'), 'Heat shut-off timer elapsed: heating off')
        e = r.h.electrical
        r.h.step(30)
        self.assertEqual(r.h.electrical, e)
        self.assertLess(r.val('panel.temperature_c'), 300.0)


class Refusals(unittest.TestCase):
    def test_refusals_stop_and_emergency(self):
        r = Rig()
        self.assertFalse(r.tap('h_fusion'))                                # no program loaded, door open
        self.assertIn('Not possible now', r.msg())
        r.ov('OK')
        r.tap('h_stop')
        self.assertEqual(r.msg(), 'No fusion cycle is running.')
        r.ov('OK')
        r.login()
        r.tap('settings_hd_home')
        r.new_program(**FAST_PROGRAM)
        r.row('pr_list', 'Fast 427')
        r.ov('Load')
        r.tap('h_door')
        r.h.step(2)
        r.tap('h_fusion')
        r.h.step(3)
        self.assertFalse(r.tap('h_door'))                                  # door locked while hot / running
        self.assertIn('Not possible now', r.msg())
        r.ov('OK')
        r.tap('h_stop')
        r.ov('No')
        self.assertEqual(r.val('cycle.phase'), 'running')
        r.tap('h_stop')
        r.ov('Yes')
        self.assertEqual((r.val('cycle.phase'), r.text('h_stage')), ('cancelled', 'Stopped'))
        self.assertTrue(r.val('door.locked'))
        r.press('key_emergency')
        self.assertEqual(r.text('h_stage'), 'EMERGENCY STOP')
        r.press('key_power')
        self.assertEqual(r.page(), ('off', 'dark'))
        self.assertFalse(r.tap('dark'))
        self.assertFalse(r.frame()['displays']['power_light']['on'])


class PanelEqualsDirect(unittest.TestCase):
    def test_program_run_equals_commands(self):
        a, b = Rig(), Rig()
        a.login()
        a.tap('settings_hd_home')
        a.new_program(**FAST_PROGRAM)
        a.row('pr_list', 'Fast 427')
        a.ov('Load')
        a.tap('h_door')
        b.call(b.A, 'access.unlock', password='0000')
        method = copy.deepcopy(rt.METHOD_EXAMPLE)
        method.update(segments=[dict(duration_s=6.0, end_temperature_K=700.15), dict(duration_s=12.0, end_temperature_K=700.15)], minimum_cooling_s=0.0,
                      rocking_rad=round(10.3 * 3.141592653589793 / 180.0, 4), stirring_fraction=0.5)   # 1.2.0: the form writes the shown speed
        b.call(b.A, 'program.save', name='Fast 427', method=method)
        b.call(b.A, 'program.load', name='Fast 427')
        b.call(b.A, 'door.set_open', open=False)
        self.assertEqual(a.h.programs, b.h.programs)
        for r in (a, b):
            r.h.step(2)
        a.tap('h_fusion')
        b.call(b.A, 'acquisition.start')
        for r in (a, b):
            r.run_to_end()
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))

    def test_keys_equal_commands(self):
        a, b = Rig(on=False), Rig(on=False)
        a.press('key_power')
        b.call(b.A, 'power.toggle')
        a.press('key_emergency')
        b.call(b.A, 'emergency.stop')
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))


class Rendering(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            r.login()
            r.tap('settings_hd_home')
            r.new_program(**FAST_PROGRAM)
            r.row('pr_list', 'Fast 427')
            r.ov('Load')
            r.tap('h_door')
            r.h.step(2)
            r.tap('h_fusion')
            r.h.step(8)
        self.assertEqual(a.call(a.A, 'panel.capture', display_id=TS)['image']['sha256'], b.call(b.A, 'panel.capture', display_id=TS)['image']['sha256'])
        cap = a.call(a.A, 'panel.capture', display_id='power_light')
        self.assertEqual(validate_value(cap, rt.CAPS['panel.capture'][1]), [])
        a.h.panel.save_png(TS, OUT / 'home.png')
        a.tap('home_hd_programs')
        a.row('pr_list', 'Fast 427')
        a.ov('Edit')
        a.h.panel.save_png(TS, OUT / 'menu.png')
        a.tap('pg_cancel')
        a.tap('programs_hd_home')
        a.tap('h_door')
        a.h.panel.save_png(TS, OUT / 'error.png')
        (OUT / 'renders.json').write_text(json.dumps({k: str(OUT.relative_to(ROOT) / f'{k}.png') for k in ('home', 'menu', 'error')}, indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_panel_layer(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + ['program.save', 'program.load', 'program.delete', 'timers.configure', 'access.unlock', 'access.lock', 'access.set_password']:
            self.assertEqual(c['capabilities'][k]['input_schema'], rt.CAPS[k][0])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])

    def test_slot_values_match_schemas(self):
        r = Rig()
        r.login()
        for k, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, rt.SLOTS[k][0]), [], k)


if __name__ == '__main__':
    unittest.main()
