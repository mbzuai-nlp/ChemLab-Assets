"""engine tests for scripts/asset_runtime/_panel_ui.py with small synthetic specs and a fake host: python3 -m pytest tests/test_panel_ui_engine.py -q

One spec per navigation style, written the way instrument specs are (inputs, displays, screens, menus, bindings), so each feature the 106
on-device menus of the survey need is checked without a full runtime: arrow menus on a character LCD (submenus, typed / digit-wise / character
entry, inverse cursor, wrap), softkey menus on a graphic LCD (labels, jump keys into another menu, transient messages), parameter cycles on a
segment display (select / inc / dec, auto-repeat, time-out, key chords, power-on key combinations, alternating error code), touch GUIs (pages,
buttons, toggles, choice lists, numeric and text keypads, scrolling lists, swipes, sliders, long touches, knob focus, hardware Home key),
press-duration windows, refusals through the spec's indications, spec checks and contract capability schemas."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import _panel_ui as pu  # noqa: E402


class Host:
    """Fake runtime: a flat state, capabilities that set state keys, optional refusals."""

    def __init__(self, **state):
        self.t = 0.0
        self.state = dict(dict(power=True), **state)
        self.calls = []
        self.refuse = {}

    def panel_read(self):
        return copy.deepcopy(self.state)

    def panel_invoke(self, cap, args):
        self.calls.append((cap, dict(args)))
        if cap in self.refuse:
            raise pu.PanelRejected(self.refuse[cap])
        if cap == 'set':
            self.state.update(args)
        elif cap == 'toggle':
            self.state[args['key']] = not self.state.get(args['key'])
        return {}

    def panel_time(self):
        return self.t


def W(key, src='field'):
    """write {key: <field or value>} through the fake 'set' capability"""
    return {'capability': 'set', 'args': {key: {src: True} if src == 'value' else {src: key}}}


# ---------------------------------------------------------------- arrow menu on a 2 x 16 character LCD
ARROW = {
    'spec_version': '0.1', 'ui_class': 'text_menu',
    'inputs': {k: {'kind': 'arrow_key' if k in ('up', 'down', 'left', 'right') else 'key'} for k in ('up', 'down', 'left', 'right', 'enter', 'back', 'menu')}
    | {f'k{i}': {'kind': 'key'} for i in range(10)} | {'clr': {'kind': 'key'}, 'dot': {'kind': 'key'}},
    'displays': {'lcd': {'kind': 'char_lcd', 'rows': 4, 'cols': 20, 'menu_cursor': 'inverse'}},
    'menus': {'main': {'label': 'MAIN MENU', 'wrap': True, 'items': [
        {'id': 'temp', 'label': 'Setpoint', 'type': 'number', 'min': 0, 'max': 450, 'step': 0.1, 'decimals': 1, 'unit': 'C',
         'read': {'state': 'temp'}, 'write': W('temp'), 'evidence': 'x'},
        {'id': 'mode', 'label': 'Mode', 'type': 'enum', 'options': [{'value': 'a', 'text': 'AUTO'}, {'value': 'm', 'text': 'MAN'}],
         'read': {'state': 'mode'}, 'write': W('mode'), 'evidence': 'x'},
        {'id': 'pin', 'label': 'PIN', 'type': 'number', 'entry': 'digits', 'digits': 4, 'digit_confirm': 'advance', 'min': 0, 'max': 9999,
         'initial': 0, 'write': W('pin'), 'evidence': 'x'},
        {'id': 'name', 'label': 'Name', 'type': 'text', 'charset': ' ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', 'max_len': 8,
         'read': {'state': 'name'}, 'write': W('name'), 'evidence': 'x'},
        {'id': 'setup', 'label': 'Setup', 'type': 'submenu', 'evidence': 'x', 'items': [
            {'id': 'beep', 'label': 'Beep', 'type': 'bool', 'read': {'state': 'beep'}, 'write': W('beep'), 'evidence': 'x'},
            {'id': 'flow', 'label': 'Flow', 'type': 'number', 'min': 0, 'max': 1000000, 'step': 1, 'read': {'state': 'flow'}, 'write': W('flow'),
             'evidence': 'x'}]}]}},
    'screens': {
        'home': {'style': 'static', 'layout': {'lcd': {'lines': [{'text': 'READY'}, {'src': {'state': 'temp'}, 'fmt': {'decimals': 1}}]}},
                 'bindings': [{'on': {'event': 'press', 'input': 'menu'}, 'do': [{'do': 'goto', 'screen': 'menu'}]}]},
        'menu': {'style': 'arrow_menu', 'nav': {'menu': 'main', 'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right', 'enter': 'enter',
                                               'back': 'back', 'exit': 'menu', 'exit_screen': 'home', 'repeat': {'delay_s': 0.5, 'rate_hz': 10},
                                               'keypad': {**{f'k{i}': str(i) for i in range(10)}, 'dot': '.'}, 'clear': 'clr'}}},
    'start_screen': 'home',
    'errors': {'entry': {'indication': [{'do': 'message', 'text': 'OUT OF RANGE', 'duration_s': 2.0}]}},
}


class ArrowMenu(unittest.TestCase):
    def setUp(self):
        self.h = Host(temp=25.0, mode='a', name='AB', beep=True, flow=100)
        self.p = pu.PanelUI(ARROW, self.h)

    def lines(self):
        return self.p.frame()['displays']['lcd']['lines']

    def test_list_cursor_wrap_and_submenu(self):
        p = self.p
        self.assertEqual(self.lines()[:2], ['READY', '25.0'])
        self.assertTrue(p.press('menu'))
        fr = self.p.frame()['displays']['lcd']
        self.assertEqual(fr['lines'][0], 'MAIN MENU')
        self.assertTrue(fr['lines'][1].startswith('Setpoint') and fr['lines'][1].endswith('25.0 C'))
        self.assertEqual(fr['inverse'][:2], [False, True])                                 # inverse bar on the cursor line
        self.assertTrue(p.press('up'))                                                      # wraps to the last item
        self.assertEqual(self.p.frame()['ui']['menu']['item'], 'setup')
        self.assertTrue(p.press('enter'))
        self.assertEqual(self.lines()[0], 'Setup')
        self.assertTrue(p.press('enter'))                                                   # Beep: ON -> OFF
        self.assertTrue(p.press('down'))
        self.assertTrue(p.press('enter'))
        self.assertFalse(self.h.state['beep'])
        self.assertTrue(p.press('back'))
        self.assertEqual(self.p.frame()['ui']['menu']['path'], [4])
        self.assertTrue(p.press('back'))                                                    # back at the root leaves the menu
        self.assertEqual(self.p.frame()['screen'], 'home')

    def test_typed_entry_range_check_and_message(self):
        p = self.p
        p.press('menu')
        for k in ('k1', 'k2', 'dot', 'k5'):                                                 # typing on a line opens it
            self.assertTrue(p.press(k))
        self.assertEqual(self.p.frame()['ui']['editor']['typed'], '12.5')
        self.assertIn('12.5_', self.lines()[1])
        self.assertTrue(p.press('enter'))
        self.assertEqual(self.h.state['temp'], 12.5)
        p.press('k9'), p.press('k9'), p.press('k9')
        self.assertFalse(p.press('enter'))                                                  # 999 > 450: refused, message, entry cleared
        self.assertEqual(self.h.state['temp'], 12.5)
        fr = self.p.frame()
        self.assertEqual(fr['ui']['message']['text'], 'OUT OF RANGE')
        self.assertIn('OUT OF RANGE', fr['displays']['lcd']['lines'])
        self.assertIsNone(fr['ui']['editor']['typed'])
        self.h.t = 2.0
        self.assertIsNone(self.p.frame()['ui']['message'])
        p.press('k4'), p.press('clr')                                                       # clear empties the entry
        self.assertIsNone(self.p.frame()['ui']['editor']['typed'])

    def test_arrow_change_with_auto_repeat(self):
        p = self.p
        p.press('menu')
        p.press('enter')
        self.assertTrue(p.press('up', duration_s=1.5))                                      # held: 1 + (1.5 - 0.5) * 10 steps of 0.1
        self.assertAlmostEqual(self.p.frame()['ui']['editor']['buffer']['temp'], 26.1)
        p.press('enter')
        self.assertAlmostEqual(self.h.state['temp'], 26.1)

    def test_digitwise_pin_and_character_entry(self):
        p = self.p
        p.press('menu')
        p.press('down'), p.press('down')
        p.press('enter')                                                                    # PIN 0000, first digit flashes
        line = self.p.frame()['displays']['lcd']
        self.assertEqual(line['lines'][1], '0000')
        for d in (1, 2, 3, 4):
            for _ in range(d):
                p.press('up')
            p.press('enter')                                                                # SET accepts the digit and moves on
        self.assertEqual(self.h.state['pin'], 1234)
        p.press('down')
        p.press('enter')                                                                    # name 'AB': up on the first character
        p.press('up')
        p.press('right'), p.press('right')
        p.press('down')                                                                     # third character: ' ' -> '9'
        self.assertEqual(self.p.frame()['ui']['editor']['buffer']['name'], 'BB9')
        p.press('enter')
        self.assertEqual(self.h.state['name'], 'BB9')

    def test_coarse_number_ranges_step_arithmetically(self):
        p = self.p
        p.press('menu')
        p.press('up')
        p.press('enter')
        p.press('down')
        p.press('enter')
        p.press('up', duration_s=2.5)
        p.press('enter')
        self.assertEqual(self.h.state['flow'], 121)

    def test_render_char_lcd(self):
        img = self.p.render('lcd')
        self.assertEqual(img.shape, (16 + 4 * 18, 16 + 20 * 12, 3))
        self.assertTrue((img == self.p.render('lcd')).all())


# ---------------------------------------------------------------- softkey menu on a graphic LCD
SOFT = {
    'spec_version': '0.1', 'ui_class': 'text_menu',
    'inputs': {f's{i}': {'kind': 'softkey'} for i in range(1, 5)} | {'menu': {'kind': 'key'}},
    'displays': {'g': {'kind': 'graphic_lcd', 'size': [240, 128], 'text_scale': 1}},
    'menus': {'main': {'label': 'MENU', 'items': [{'id': 'tare', 'label': 'TARE', 'type': 'action', 'write': {'capability': 'set', 'args': {'tared': {'const': True}}},
                                                   'evidence': 'x'},
                                                  {'id': 'gas', 'label': 'GAS', 'type': 'enum', 'options': [{'value': 'N2'}, {'value': 'Ar'}],
                                                   'read': {'state': 'gas'}, 'write': W('gas'), 'evidence': 'x'}]},
              'about': {'label': 'ABOUT', 'items': [{'id': 'fw', 'label': 'FW 7.1', 'type': 'info', 'evidence': 'x'},
                                                     {'id': 'sn', 'label': 'SN 123', 'type': 'info', 'evidence': 'x'}]}},
    'screens': {
        'home': {'style': 'static', 'layout': {'g': {'lines': [{'src': {'state': 'gas'}}]}},
                 'bindings': [{'on': {'event': 'press', 'input': 'menu'}, 'do': [{'do': 'goto', 'screen': 'menu'}]},
                              {'on': {'event': 'press', 'input': 's4'}, 'do': [{'do': 'goto', 'screen': 'menu', 'menu': 'about', 'path': [1]}]}]},
        'menu': {'style': 'softkey_menu', 'nav': {'menu': 'main', 'exit_screen': 'home', 'softkeys': [
            {'input': 's1', 'label': 'UP', 'op': 'up'}, {'input': 's2', 'label': 'DOWN', 'op': 'down'},
            {'input': 's3', 'label': 'SELECT', 'op': 'enter'}, {'input': 's4', 'label': 'BACK', 'op': 'back'}]}}},
    'start_screen': 'home',
}


class SoftkeyMenu(unittest.TestCase):
    def test_labels_ops_and_jump_keys(self):
        h = Host(gas='N2')
        p = pu.PanelUI(SOFT, h)
        p.press('menu')
        fr = p.frame()['displays']['g']
        self.assertEqual(fr['softkeys'], ['UP', 'DOWN', 'SELECT', 'BACK'])
        self.assertIn('SELECT', fr['lines'][-1])
        self.assertTrue(p.press('s3'))                                                      # action item: written at once
        self.assertEqual(h.calls[-1], ('set', {'tared': True}))
        p.press('s2'), p.press('s3'), p.press('s1'), p.press('s3')                      # UP = next option while editing
        self.assertEqual(h.state['gas'], 'Ar')
        p.press('s4')
        self.assertEqual(p.frame()['screen'], 'home')
        self.assertTrue(p.press('s4'))                                                      # a jump key opens another menu at an item
        m = p.frame()['ui']['menu']
        self.assertEqual((m['menu'], m['item']), ('about', 'sn'))
        self.assertEqual(p.render('g').shape, (128, 240, 3))


# ---------------------------------------------------------------- parameter cycle on a segment display (JULABO-like)
SEG = {'kind': 'seven_segment', 'size': [200, 60], 'fields': {'d': {'x': 6, 'y': 6, 'cells': 5, 'cell_w': 26, 'cell_h': 46, 'gap': 10}},
       'icons': {'set': {'kind': 'text', 'text': 'SET', 'x': 0, 'y': 52, 'scale': 1}}}
CYCLE = {
    'spec_version': '0.1', 'ui_class': 'parameter_cycle',
    'inputs': {'up': {'kind': 'arrow_key'}, 'down': {'kind': 'arrow_key'}, 'ok': {'kind': 'key', 'long_press_s': 1.0},
               'power': {'kind': 'switch', 'positions': ['off', 'on']}},
    'displays': {'seg': SEG, 'alarm': {'kind': 'led'}},
    'power': {'powered': {'state': 'power'}, 'off_screen': 'off', 'on_screen': [{'screen': 'home'}]},
    'screens': {
        'off': {'style': 'static'},
        'home': {'style': 'static',
                 'layout': {'seg': {'fields': {'d': {'cases': [
                     {'when': {'all': [{'state': 'err', 'ne': None}, {'alternate': {'period_s': 2.0, 'count': 2, 'index': 1}}]},
                      'src': {'state': 'err'}, 'fmt': {'pattern': 'E{:02d}'}},
                     {'src': {'state': 'temp'}, 'fmt': {'decimals': 2}}]}}},
                            'alarm': {'on': {'state': 'err', 'ne': None}, 'blink': True, 'period_s': 0.5}},
                 'bindings': [{'on': {'event': 'press', 'input': ['up', 'down']}, 'do': [{'do': 'goto', 'screen': 'setpoint'}]},
                              {'on': {'event': 'press', 'input': 'ok', 'long': True}, 'do': [{'do': 'invoke', 'capability': 'toggle', 'args': {'key': {'const': 'run'}}}]},
                              {'on': {'event': 'press', 'input': 'ok', 'held': ['down']}, 'do': [{'do': 'goto', 'screen': 'timer'}]}]},
        'setpoint': {'style': 'parameter_cycle', 'timeout_s': 5.0, 'timeout_screen': 'home',
                     'nav': {'inc': 'up', 'dec': 'down', 'confirm': 'ok', 'select': None, 'after_confirm': 'next', 'exit_screen': 'home',
                             'repeat': {'delay_s': 0.5, 'rate_hz': 10},
                             'params': [{'id': 'int', 'label': 'SP', 'type': 'number', 'min': -20, 'max': 150, 'step': 1, 'read': {'state': 'sp', 'round': 'floor'}},
                                        {'id': 'dec', 'label': 'SP', 'type': 'number', 'min': 0, 'max': 99, 'step': 1,
                                         'read': {'state': 'sp', 'scale': 100, 'mod': 100, 'round': 'nearest'},
                                         'write': {'capability': 'set', 'args': {'sp': {'sum': [{'field': 'int'}, {'field': 'dec', 'scale': 0.01}]}}}}]},
                     'layout': {'seg': {'fields': {'d': {'parts': [{'src': {'field': 'int'}, 'fmt': {'pattern': '{:.0f}'}, 'blink': {'editing': 'int'}},
                                                                   {'text': '.'},
                                                                   {'src': {'field': 'dec'}, 'fmt': {'pattern': '{:02.0f}'}, 'blink': {'editing': 'dec'}}]}},
                                        'icons': {'set': True}}}},
        'timer': {'style': 'parameter_cycle', 'timeout_s': 10.0, 'on_timeout': [{'do': 'nav', 'op': 'confirm'}],
                  'nav': {'inc': 'up', 'dec': 'down', 'confirm': 'ok', 'after_confirm': 'exit', 'exit_screen': 'home',
                          'params': [{'id': 'min', 'label': 't O', 'type': 'number', 'min': 0, 'max': 999, 'step': 1, 'read': {'state': 'timer'},
                                      'write': W('timer', 'value')}]},
                  'layout': {'seg': {'fields': {'d': {'src': {'current': 'text'}, 'blink': True}}}}}},
    'bindings': [{'on': {'event': 'switch', 'input': 'power', 'held': ['up']}, 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'supply': {'const': 'hHI'}}},
                                                                                  {'do': 'invoke', 'capability': 'set', 'args': {'power': {'const': True}}}]},
                 {'on': {'event': 'switch', 'input': 'power'}, 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'power': {'event': 'position', 'map': {'on': True, 'off': False}}}}]}],
}


class ParameterCycle(unittest.TestCase):
    def setUp(self):
        self.h = Host(power=False, temp=21.37, sp=25.5, err=None, run=False, timer=0)
        self.p = pu.PanelUI(CYCLE, self.h)

    def seg(self):
        return self.p.frame()['displays']['seg']['fields']['d']

    def test_power_on_key_combination(self):
        self.assertEqual(self.p.frame()['screen'], 'off')
        self.assertTrue(self.p.switch('power', 'on', held=['up']))                          # Up held while switching on
        self.assertEqual((self.h.state['supply'], self.p.frame()['screen']), ('hHI', 'home'))

    def test_setpoint_two_stage_entry_repeat_and_timeout(self):
        self.p.switch('power', 'on')
        self.assertEqual(self.seg()['text'], '21.37')
        self.assertTrue(self.p.press('up'))                                                 # shows the set point, integer part flashes
        self.assertEqual((self.seg()['text'], self.seg()['blink_mask']), ('25.50', '11000'))
        self.assertTrue(self.p.press('up', duration_s=1.0))                                 # held: 1 + 5 steps
        self.assertEqual(self.seg()['text'], '31.50')
        self.p.press('ok')                                                                  # integer accepted, the decimals flash
        self.assertEqual(self.seg()['blink_mask'], '00011')
        self.p.press('down')
        self.p.press('ok')
        self.assertAlmostEqual(self.h.state['sp'], 31.49)
        self.assertEqual(self.p.frame()['screen'], 'home')
        self.p.press('up')
        self.p.press('up')
        self.h.t += 5.0                                                                     # no key for 5 s: back, nothing written
        self.assertEqual(self.p.frame()['screen'], 'home')
        self.assertAlmostEqual(self.h.state['sp'], 31.49)

    def test_chord_timer_autoaccept_hold_and_alternating_error(self):
        self.p.switch('power', 'on')
        self.assertTrue(self.p.press('ok', held=['down']))                                  # Down + OK together
        self.assertEqual(self.p.frame()['screen'], 'timer')
        for _ in range(3):
            self.p.press('up')
        self.h.t += 10.0                                                                    # auto-accepted after the time-out
        self.assertEqual((self.p.frame()['screen'], self.h.state['timer']), ('home', 3))
        self.assertTrue(self.p.press('ok', duration_s=1.2))                                 # hold OK: start / stop
        self.assertTrue(self.h.state['run'])
        self.h.state['err'] = 7
        self.h.t = 100.0
        self.assertEqual(self.seg()['text'], '21.37')
        self.h.t = 102.0                                                                    # alternates with the error code
        self.assertEqual(self.seg()['text'], 'E07')
        led = self.p.frame()['displays']['alarm']
        self.assertEqual((led['on'], led['blink'], led['shown']), (True, True, True))
        self.h.t = 102.25
        self.assertFalse(self.p.frame()['displays']['alarm']['shown'])                      # 0.5 s blink period
        self.assertEqual(self.p.render('seg').shape, (60, 200, 3))


# ---------------------------------------------------------------- touch GUI
TOUCH = {
    'spec_version': '0.1', 'ui_class': 'touchscreen_gui',
    'inputs': {'home': {'kind': 'key'}, 'knob': {'kind': 'encoder', 'push_input': 'knob_push'}, 'knob_push': {'kind': 'key'}},
    'displays': {'tft': {'kind': 'touch_tft', 'size': [480, 272]}},
    'timing': {'long_press_s': 1.0},
    'screens': {'gui': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'main', 'knob': 'knob', 'push': 'knob_push', 'pages': {
        'main': {'title': 'Main', 'swipes': {'right': [{'do': 'page', 'page': 'settings'}]}, 'widgets': [
            {'id': 'weight', 'type': 'label', 'rect': [10, 40, 200, 30], 'text': {'src': {'state': 'w'}, 'fmt': {'decimals': 4}}},
            {'id': 'tare', 'type': 'button', 'rect': [10, 220, 100, 40], 'label': 'Tare', 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'w': {'const': 0.0}}}],
             'do_long': [{'do': 'beep'}]},
            {'id': 'settings', 'type': 'button', 'rect': [370, 220, 100, 40], 'label': 'Settings', 'do': [{'do': 'page', 'page': 'settings'}]},
            {'id': 'start', 'type': 'button', 'rect': [190, 220, 100, 40], 'label': 'Start', 'enabled': {'state': 'door_closed'},
             'do': [{'do': 'invoke', 'capability': 'set', 'args': {'running': {'const': True}}}]}]},
        'settings': {'title': 'Settings', 'widgets': [
            {'id': 'doors', 'type': 'toggle', 'rect': [10, 40, 220, 36], 'label': 'Auto doors', 'read': {'state': 'auto'}, 'write': W('auto', 'value')},
            {'id': 'unit', 'type': 'choice', 'rect': [10, 84, 220, 50], 'label': 'Unit', 'options': [{'value': 'g'}, {'value': 'mg'}, {'value': 'ct'}],
             'read': {'state': 'unit'}, 'write': W('unit', 'value')},
            {'id': 'target', 'type': 'number', 'rect': [250, 40, 220, 50], 'label': 'Target', 'min': 0, 'max': 220, 'read': {'state': 'target'},
             'write': W('target', 'value')},
            {'id': 'user', 'type': 'text', 'rect': [250, 100, 220, 50], 'label': 'User', 'read': {'state': 'user'}, 'write': W('user', 'value'),
             'keys': [['A', 'B', 'C', '<'], ['D', 'E', 'F', 'OK']]},
            {'id': 'bright', 'type': 'slider', 'rect': [10, 150, 200, 30], 'label': 'Brightness', 'min': 20, 'max': 100, 'step': 20,
             'read': {'state': 'bright'}, 'write': W('bright', 'value')},
            {'id': 'methods', 'type': 'list', 'rect': [250, 160, 220, 80], 'row_h': 40,
             'rows': [{'text': f'Method {i}', 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'method': {'const': i}}}]} for i in range(5)]},
            {'id': 'back', 'type': 'button', 'rect': [10, 230, 80, 36], 'label': 'Back', 'do': [{'do': 'page_back'}]}]}}}}},
    'start_screen': 'gui',
    'bindings': [{'on': {'event': 'press', 'input': 'home'}, 'do': [{'do': 'page', 'page': 'main', 'clear_history': True}]}],
}


class TouchGui(unittest.TestCase):
    def setUp(self):
        self.h = Host(w=1.23456, door_closed=False, auto=False, unit='g', target=10.0, user='', bright=60, method=None, running=False)
        self.p = pu.PanelUI(TOUCH, self.h)

    def tap(self, x, y, **kw):
        return self.p.touch('tft', x, y, **kw)

    def page(self):
        return self.p.frame()['displays']['tft']

    def test_buttons_disabled_long_touch_pages_and_home(self):
        self.assertEqual(self.page()['widgets'][0]['text'], '1.2346')
        self.assertFalse(self.tap(200, 230))                                               # Start dimmed while the door is open
        self.h.state['door_closed'] = True
        self.assertTrue(self.tap(200, 230))
        self.assertTrue(self.h.state['running'])
        self.assertTrue(self.tap(20, 230, duration_s=1.5))                                 # long touch: its own action
        self.assertEqual((self.p.frame()['ui']['beeps'], self.h.state['w']), (1, 1.23456))
        self.assertTrue(self.tap(20, 230))
        self.assertEqual(self.h.state['w'], 0.0)
        self.assertTrue(self.tap(400, 230))
        self.assertEqual(self.page()['page'], 'settings')
        self.assertTrue(self.p.press('home'))                                              # hardware Home from any level
        self.assertEqual(self.page()['page'], 'main')
        self.assertTrue(self.tap(100, 100, to=(300, 110)))                                 # swipe right opens the settings page
        self.assertEqual(self.page()['page'], 'settings')
        self.assertTrue(self.tap(20, 240))                                                 # Back
        self.assertEqual(self.page()['page'], 'main')
        self.assertFalse(self.tap(470, 10))                                                # nothing there

    def test_settings_widgets(self):
        self.tap(400, 230)
        self.assertTrue(self.tap(20, 50))                                                  # toggle
        self.assertTrue(self.h.state['auto'])
        self.assertTrue(self.tap(20, 100))                                                 # choice list overlay
        ov = self.page()['overlay']
        self.assertEqual(ov['keys'][-1], ['Cancel'])
        kx, ky, kw, kh = ov['rect']
        rows = len(ov['keys'])
        self.assertTrue(self.tap(kx + 5, ky + 30 + (kh - 30) * 1 // rows + 3))             # 'mg'
        self.assertEqual(self.h.state['unit'], 'mg')
        self.assertTrue(self.tap(260, 60))                                                 # numeric keypad
        ov = self.page()['overlay']
        kx, ky, kw, kh = ov['rect']

        def key(label):
            for r, row in enumerate(ov['keys']):
                if label in row:
                    c = row.index(label)
                    return self.tap(kx + c * kw // len(row) + 3, ky + 30 + r * (kh - 30) // len(ov['keys']) + 3)
            raise AssertionError(label)
        for k in ('2', '5', '0', 'OK'):
            key(k)
        self.assertEqual(self.page()['overlay']['error'], 'Out of range')                  # 250 > 220: stays open with the message
        key('C'), key('1'), key('2'), key('.'), key('5'), key('OK')
        self.assertEqual((self.h.state['target'], self.page().get('overlay')), (12.5, None))
        self.assertTrue(self.tap(260, 120))                                                # text keypad with its own keys
        ov = self.page()['overlay']
        kx, ky, kw, kh = ov['rect']
        for label in ('B', 'E', 'E', 'F', 'OK'):
            for r, row in enumerate(ov['keys']):
                if label in row:
                    self.tap(kx + row.index(label) * kw // len(row) + 3, ky + 30 + r * (kh - 30) // len(ov['keys']) + 3)
                    break
        self.assertEqual(self.h.state['user'], 'BEEF')
        self.assertTrue(self.tap(15, 160, to=(209, 165)))                                  # slider dragged to the end
        self.assertEqual(self.h.state['bright'], 100)
        self.assertTrue(self.tap(300, 170, to=(300, 100)))                                 # list scrolled up by two rows
        self.assertEqual(self.page()['widgets'][5]['rows'], ['Method 2', 'Method 3'])
        self.assertTrue(self.tap(300, 210))
        self.assertEqual(self.h.state['method'], 3)
        img = self.p.render('tft')
        self.assertEqual(img.shape, (272, 480, 3))

    def test_knob_focus(self):
        self.h.state['door_closed'] = True
        self.assertTrue(self.p.turn('knob', 2))                                            # Tare -> Settings (labels are skipped)
        self.assertEqual(self.p.frame()['ui']['focus'], 'settings')
        self.assertTrue(self.p.push('knob'))
        self.assertEqual(self.page()['page'], 'settings')


# ---------------------------------------------------------------- press-duration windows, refusals, checks
WINDOWS = {
    'spec_version': '0.1', 'ui_class': 'parameter_cycle',
    'inputs': {'btn': {'kind': 'key', 'long_press_s': 1.0}},
    'displays': {'green': {'kind': 'led'}, 'red': {'kind': 'led'}},
    'screens': {'run': {'style': 'static', 'bindings': [
        {'on': {'event': 'press', 'input': 'btn', 'long': 'any', 'duration_s': [0, 1]}, 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'f': {'const': 'none'}}}]},
        {'on': {'event': 'press', 'input': 'btn', 'long': 'any', 'duration_s': [1, 4]}, 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'f': {'const': 'zero'}}}]},
        {'on': {'event': 'press', 'input': 'btn', 'long': 'any', 'duration_s': [4, 8]}, 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'f': {'const': 'reset'}}}]}]}},
    'start_screen': 'run',
    'errors': {'runtime': {'BUSY': {'indication': [{'do': 'blink', 'target': 'red', 'times': 3}]}}},
}


class WindowsRefusalsChecks(unittest.TestCase):
    def test_duration_windows_and_refusal_indication(self):
        h = Host()
        p = pu.PanelUI(WINDOWS, h)
        for d, f in ((0.3, 'none'), (2.0, 'zero'), (5.0, 'reset')):
            self.assertTrue(p.press('btn', duration_s=d))
            self.assertEqual(h.state['f'], f)
        self.assertFalse(p.press('btn', duration_s=9.0))                                   # released after every pattern: nothing
        h.refuse['set'] = 'BUSY'
        self.assertFalse(p.press('btn', duration_s=2.0))
        fr = p.frame()
        self.assertEqual(fr['ui']['last_gesture']['rejection'], 'BUSY')
        self.assertEqual((fr['displays']['red']['blink'], fr['displays']['red']['shown']), (True, True))   # an off LED flashes on first

    def test_bad_gestures_and_spec_checks(self):
        p = pu.PanelUI(WINDOWS, Host())
        with self.assertRaises(pu.PanelError):
            p.turn('btn', 1)
        with self.assertRaises(pu.PanelError):
            p.press('nope')
        bad = copy.deepcopy(ARROW)
        bad['screens']['home']['bindings'][0]['do'] = [{'do': 'goto', 'screen': 'nowhere'}, {'do': 'invoke', 'capability': 'nope'}]
        bad['menus']['main']['items'][0].pop('evidence')
        errs = pu.check_spec(bad, capabilities={'set', 'toggle'})
        self.assertTrue(any('nowhere' in e for e in errs) and any("'nope'" in e for e in errs) and any('evidence' in e for e in errs), errs)
        for spec in (ARROW, SOFT, CYCLE, TOUCH, WINDOWS):
            self.assertEqual(pu.check_spec(spec, capabilities={'set', 'toggle'}), [])

    def test_capability_definitions(self):
        caps = pu.capability_definitions(TOUCH)
        self.assertEqual(set(caps), {'panel.press', 'panel.turn', 'panel.touch', 'panel.capture', 'panel.frame'})
        self.assertEqual(caps['panel.touch']['input_schema']['properties']['x']['maximum'], 479)
        caps = pu.capability_definitions(CYCLE)
        self.assertIn('held', caps['panel.press']['input_schema']['properties'])           # chords declared because the spec uses them
        self.assertIn('held', caps['panel.switch']['input_schema']['properties'])
        self.assertEqual(caps['panel.frame']['visibility'], 'evaluator')


# ---------------------------------------------------------------- additions for integer-then-decimal entry (JULABO CORIO pilot)
SPLIT = {
    'spec_version': '0.1', 'ui_class': 'parameter_cycle',
    'inputs': {'up': {'kind': 'arrow_key'}, 'down': {'kind': 'arrow_key'}, 'ok': {'kind': 'key'}, 'power': {'kind': 'switch', 'positions': ['off', 'on']}},
    'displays': {'seg': SEG},
    'screens': {
        'home': {'style': 'static',
                 'layout': {'seg': {'fields': {'d': {'src': {'state': 'temp'}, 'fmt': {'decimals': 2}, 'blink': {'state': 'wait'}, 'blink_only': '.'}}}},
                 'bindings': [{'on': {'event': 'press', 'input': 'up'}, 'do': [{'do': 'goto', 'screen': 'sp'}]}]},
        'sp': {'style': 'parameter_cycle',
               'nav': {'inc': 'up', 'dec': 'down', 'confirm': 'ok', 'exit_screen': 'home', 'params': [
                   {'id': 'whole', 'type': 'number', 'min': -20, 'max': 150, 'step': 0.1, 'stride': 10, 'read': {'state': 'sp'}},
                   {'id': 'tenth', 'type': 'number', 'min': -20, 'max': 150, 'step': 0.1, 'read': {'field': 'whole'},
                    'write': {'capability': 'set', 'args': {'sp_k': {'field': 'tenth', 'offset': 273.15, 'round': 2}}}}]},
               'layout': {'seg': {'fields': {'d': {'cases': [
                   {'when': {'editing': 'whole'}, 'src': {'field': 'whole'}, 'fmt': {'decimals': 1}, 'blink': True, 'blink_only': 'int'},
                   {'src': {'field': 'tenth'}, 'fmt': {'decimals': 1}, 'blink': True, 'blink_only': 'frac'}]}}}}}},
    'start_screen': 'home',
    'bindings': [{'on': {'event': 'switch', 'input': 'power', 'held': 'any'}, 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'pos': {'event': 'position'}}}]}],
}


class SplitEntry(unittest.TestCase):
    def test_stride_blink_parts_rounding_and_held_any(self):
        h = Host(temp=21.37, wait=True, sp=-0.5)
        p = pu.PanelUI(SPLIT, h)
        d = p.frame()['displays']['seg']['fields']['d']
        self.assertEqual((d['text'], d['blink_mask']), ('21.37', '00100'))            # only the point flashes
        p.press('up')                                                                   # opens on the stored value: no change yet
        d = p.frame()['displays']['seg']['fields']['d']
        self.assertEqual((d['text'], d['blink_mask']), ('-0.5', '1100'))               # integer part with its sign flashes
        self.assertTrue(p.press('up'))                                                  # stride 10: one press = 1.0 on the 0.1 grid
        self.assertEqual(p.frame()['ui']['editor']['buffer']['whole'], 0.5)
        p.press('up', duration_s=0.1)
        p.press('ok')
        d = p.frame()['displays']['seg']['fields']['d']
        self.assertEqual((d['text'], d['blink_mask']), ('1.5', '001'))                 # then the tenths flash, starting from the integer stage
        p.press('down'), p.press('down')
        p.press('ok')
        self.assertEqual(h.state['sp_k'], 274.45)                                       # round to 2 decimals: exactly the literal
        self.assertEqual(p.frame()['screen'], 'home')
        self.assertTrue(p.switch('power', 'on', held=['up', 'ok']))                     # held 'any' matches with or without held keys
        self.assertTrue(p.switch('power', 'off'))
        self.assertEqual(h.calls[-2:], [('set', {'pos': 'on'}), ('set', {'pos': 'off'})])

    def test_check_arguments(self):
        caps = pu.capability_definitions(CYCLE)
        press, switch = caps['panel.press']['input_schema'], caps['panel.switch']['input_schema']
        self.assertEqual(pu.check_arguments(press, {'input_id': 'ok', 'duration_s': 1.0, 'held': ['down']}), [])
        for bad in ({'input_id': 'nope'}, {'input_id': 'ok', 'duration_s': True}, {'input_id': 'ok', 'duration_s': 99.0}, {'input_id': 'ok', 'x': 1},
                    {'duration_s': 1.0}, {'input_id': 'ok', 'held': ['up'] * 5}, {'input_id': 'ok', 'held': 'up'}):
            self.assertTrue(pu.check_arguments(press, bad), bad)
        self.assertEqual(pu.check_arguments(switch, {'input_id': 'power', 'position': 'on', 'held': ['up']}), [])
        self.assertTrue(pu.check_arguments(caps['panel.frame']['input_schema'], {'a': 1}))



# ---------------------------------------------------------------- touch toolkit: layout conventions, forms, keyboards, pickers, dialogs, lists
def _f(fid, typ, label, **kw):
    return dict(id=fid, type=typ, label=label, evidence='x', **kw)


KIT = {
    'spec_version': '0.1', 'ui_class': 'touchscreen_gui',
    'inputs': {'home': {'kind': 'key'}, 'back': {'kind': 'key'}},
    'displays': {'tft': {'kind': 'touch_tft', 'size': [800, 480], 'brightness': {'state': 'bright'}},
                 'light': {'kind': 'led', 'size': [40, 12]}},
    'layout_base': {'light': {'on': True, 'color': {'state': 'light'}}},
    'i18n': {'language': {'state': 'lang'}, 'tables': {'de': {'Settings': 'Einstellungen', 'Save': 'Speichern', 'Active': 'Aktiv'}}},
    'forms': {
        'setup': {'write': {'capability': 'set', 'args': {'setup': {'form_values': 'setup'}}}, 'on_save': [{'do': 'page_back'}],
                  'on_cancel': [{'do': 'page_back'}], 'invalid': [{'do': 'dialog', 'dialog': 'missing'}], 'fields': [
                      _f('mode', 'enum', 'Mode', options=[{'value': 'a', 'text': 'Auto'}, {'value': 'm', 'text': 'Manual'}], read={'state': 'mode'}),
                      _f('beep', 'bool', 'Beep', options=[{'value': True, 'text': 'Active'}, {'value': False, 'text': 'Inactive'}], read={'state': 'beep'}),
                      _f('days', 'number', 'Days', min=0, max=400, step=1, unit='d', read={'state': 'days'},
                         visible_if={'field': 'beep', 'eq': True}),
                      _f('name', 'text', 'Name', max_chars=8, read={'state': 'name'}, required=True),
                      _f('pw', 'password', 'Password'),
                      _f('day', 'date', 'Date', read={'state': 'day'}, fmt={'date': {'state': 'dfmt'}}),
                      _f('clock', 'time', 'Time', read={'state': 'clock'}),
                      _f('fields', 'multi', 'Fields', options=[{'value': v, 'text': v.title()} for v in ('date', 'time', 'user')], read={'state': 'fields'}),
                      _f('info', 'info', 'Serial', read={'const': 'B123'}),
                      _f('more', 'link', 'More', do=[{'do': 'page', 'page': 'more'}]),
                      _f('print', 'action', 'Print', unsupported='no printer', do=[{'do': 'dialog', 'dialog': 'missing'}, {'do': 'unsupported', 'note': 'no printer'}])]},
        'live': {'fields': [_f('unit', 'enum', 'Unit', options=[{'value': u} for u in ('g', 'mg', 'ct')], read={'state': 'unit'},
                               write={'capability': 'set', 'args': {'unit': {'value': True}}})]},
        'pw_dialog': {'write': {'capability': 'set', 'args': {'pw': {'field': 'pw1'}}}, 'fields': [_f('pw1', 'password', 'New password')]}},
    'dialogs': {
        'delete': {'title': 'Delete', 'lines': [{'parts': [{'text': 'Delete '}, {'src': {'state': 'methods', 'key': {'var': 'sel'}, 'path': 'name'}},
                                                           {'text': '?'}]}],
                   'buttons': [{'label': 'Cancel'}, {'label': 'OK', 'style': 'primary',
                                                     'do': [{'do': 'invoke', 'capability': 'set', 'args': {'deleted': {'var': 'sel'}}}]}]},
        'missing': {'title': 'Message', 'style': 'warn', 'lines': ['Name is required'], 'buttons': [{'label': 'OK'}]},
        'pw': {'title': 'Set password', 'form': 'pw_dialog', 'buttons': [
            {'label': 'Cancel', 'do': [{'do': 'form_cancel', 'form': 'pw_dialog'}]},
            {'label': 'OK', 'keep': True, 'do': [{'do': 'form_save', 'form': 'pw_dialog'}, {'do': 'dialog_close'}]}]}},
    'screens': {'gui': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'main', 'home': 'home', 'back': 'back', 'pages': {
        'main': {'title': 'Main', 'header': [{'id': 'h_set', 'type': 'button', 'label': 'Settings',
                                              'do': [{'do': 'form_open', 'form': 'setup', 'page': 'setup'}]}],
                 'content': [{'id': 'value', 'type': 'label', 'scale': 5, 'align': 'right', 'h': 60, 'text': {'src': {'state': 'w'}, 'fmt': {'decimals': 4}}},
                             {'row': [{'id': 'lamp', 'type': 'lamp', 'color': {'state': 'light'}, 'text': 'Level'},
                                      {'id': 'trac', 'type': 'gauge', 'w': 3, 'value': {'state': 'frac'}, 'marks': [{'const': 0.4}, {'const': 0.6}]},
                                      {'id': 'bubble', 'type': 'xy', 'x': {'state': 'tilt_x'}, 'y': {'const': 0.0}}], 'h': 60},
                             {'id': 'hidden', 'type': 'label', 'text': 'never', 'visible_if': {'const': False}},
                             {'id': 'methods', 'type': 'list', 'h': 'flex', 'row_h': 40, 'items': {'state': 'methods'}, 'empty_text': 'No methods',
                              'row': {'cells': [{'src': {'row': 'name'}}, {'src': {'row': 'date'}}], 'selected': {'row': 'id', 'same': {'var': 'sel'}},
                                      'do': [{'do': 'set_var', 'var': 'sel', 'value': {'row': 'id'}}]}}],
                 'actions': [{'id': 'del', 'type': 'button', 'label': 'Delete', 'enabled': {'var': 'sel'},
                              'do': [{'do': 'dialog', 'dialog': 'delete'}]},
                             {'id': 'unit', 'type': 'button', 'label': 'Unit', 'do': [{'do': 'page', 'page': 'units'}]},
                             {'id': 'pw', 'type': 'button', 'label': 'Password', 'do': [{'do': 'form_open', 'form': 'pw_dialog'}, {'do': 'dialog', 'dialog': 'pw'}]},
                             {'id': 'start', 'type': 'button', 'label': 'Start', 'style': 'primary', 'w': 2}]},
        'setup': {'title': 'Settings', 'sidebar': [{'id': 's_gen', 'type': 'button', 'label': 'General', 'selected': {'page': 'setup'}}],
                  'content': [{'id': 'rows', 'type': 'form', 'form': 'setup', 'h': 'flex'}],
                  'actions': [{'id': 'close', 'type': 'button', 'label': 'Close', 'do': [{'do': 'form_cancel'}]},
                              {'id': 'save', 'type': 'button', 'label': 'Save', 'do': [{'do': 'form_save'}]}]},
        'more': {'title': 'More', 'content': [{'id': 'back_btn', 'type': 'button', 'label': 'Back', 'do': [{'do': 'page_back'}]}]},
        'units': {'title': 'Units', 'content': [{'id': 'live_rows', 'type': 'form', 'form': 'live', 'h': 'flex'}]}}}}},
    'start_screen': 'gui',
    'vars': {'sel': {'initial': None}},
}
# the delete button opens the dialog with the selected method as its row (a list lookup by key)


class TouchToolkit(unittest.TestCase):
    def setUp(self):
        self.h = Host(w=12.3456, mode='a', beep=True, days=30, name='Lab1', day='2024-01-31', clock='13:05', fields=['date'], unit='g', bright=100,
                      light='green', lang='en', frac=0.5, tilt_x=0.2, dfmt='DD.MM.YYYY',
                      methods=[{'id': 'm1', 'name': 'General weighing', 'date': '06/27/2024'}, {'id': 'm2', 'name': 'Density', 'date': '06/28/2024'}])
        spec = copy.deepcopy(KIT)
        self.p = pu.PanelUI(spec, self.h)
        self.spec = spec

    def disp(self):
        return self.p.frame()['displays']['tft']

    def wid(self, wid):
        return next(w for w in self.disp()['widgets'] if w['id'] == wid)

    def tap_w(self, wid, dy=None):
        x, y, w, h = self.wid(wid)['rect']
        return self.p.touch('tft', x + w // 2, y + (h // 2 if dy is None else dy))

    def tap_b(self, label):
        """Tap a button of the top overlay by its label."""
        for b in self.disp()['overlay']['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.p.touch('tft', x + w // 2, y + h // 2)
        raise AssertionError(f'{label} not in {[b["label"] for b in self.disp()["overlay"]["buttons"]]}')

    def tap_row(self, wid, label):
        w = self.wid(wid)
        for _ in range(5):                                                             # drag the rows up / down until the label shows
            if label in [c[0] for c in w['cells']]:
                break
            x, y, ww, hh = w['rect']
            up = w['scroll'] == 0 or w['scroll'] + len(w['cells']) < w['count']
            self.assertTrue(self.p.touch('tft', x + 10, y + hh - 10 if up else y + 10, to=(x + 10, y + 10 if up else y + hh - 10)))
            w = self.wid(wid)
        k = [c[0] for c in w['cells']].index(label)
        return self.p.touch('tft', w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def type_text(self, text):
        for ch in text:
            self.assertTrue(self.tap_b(ch), ch)

    def test_layout_conventions(self):
        d = self.disp()
        r = {w['id']: w['rect'] for w in d['widgets']}
        self.assertEqual(d['header_h'], 40)
        self.assertEqual(r['h_set'], [800 - 6 - 110, 4, 110, 32])                     # header button right-aligned in the title bar
        self.assertEqual(r['value'][:2], [6, 46])                                      # content below the bar, full width
        self.assertEqual(r['value'][2], 788)
        self.assertNotIn('hidden', r)                                                  # hidden widgets leave no gap
        self.assertEqual(r['lamp'][1], r['trac'][1])                                   # a row places its widgets side by side by weight
        self.assertEqual((r['lamp'][2], r['trac'][2], r['bubble'][2]), (156, 468, 156))
        self.assertEqual(r['methods'][1] + r['methods'][3] + 6, 480 - 52)              # flex fills down to the action bar
        acts = [r[k] for k in ('del', 'unit', 'pw', 'start')]
        self.assertEqual([a[1] for a in acts], [428] * 4)
        self.assertEqual(sum(a[2] for a in acts), 800)
        self.assertEqual(acts[3][2], 2 * acts[0][2])                                  # weights
        self.assertEqual(self.wid('start')['style'], 'primary')
        self.assertEqual(self.wid('value')['text'], '12.3456')
        self.assertEqual(self.wid('trac')['marks'], [0.4, 0.6])
        self.assertEqual((self.wid('trac')['fraction'], self.wid('bubble')['xy'], self.wid('lamp')['color']), (0.5, [0.2, 0.0], 'green'))
        img = self.p.render('tft')
        self.assertEqual(img.shape, (480, 800, 3))
        self.assertTrue((img[2, 2] == pu.ROLE_COLORS['header']).all())               # title bar in the header colour

    def test_dynamic_list_selection_and_row_dialog(self):
        w = self.wid('methods')
        self.assertEqual(w['cells'], [['General weighing', '06/27/2024'], ['Density', '06/28/2024']])
        self.assertFalse(self.wid('del')['enabled'])
        self.assertFalse(self.tap_w('del'))
        self.assertTrue(self.tap_row('methods', 'Density'))
        self.assertEqual(self.p.frame()['ui']['vars']['sel'], 'm2')
        self.assertEqual(self.wid('methods')['selected_rows'], [1])
        self.assertTrue(self.tap_w('del'))                                             # confirmation names the selected entity (keyed lookup)
        self.assertEqual(self.disp()['overlay']['lines'], ['Delete Density?'])
        self.assertFalse(self.p.touch('tft', 5, 470))                                 # modal: outside taps do nothing
        self.assertTrue(self.tap_b('Cancel'))
        self.assertIsNone(self.p.frame()['ui']['overlay'])
        self.tap_w('del')
        self.assertTrue(self.tap_b('OK'))
        self.assertEqual(self.h.calls[-1], ('set', {'deleted': 'm2'}))
        self.h.state['methods'] = []
        self.assertEqual(self.wid('methods')['empty_text'], 'No methods')

    def test_form_editors_save_and_required(self):
        self.assertTrue(self.tap_w('h_set'))
        d = self.disp()
        self.assertEqual((d['page'], d['title']), ('setup', 'Settings'))
        self.assertTrue(self.wid('s_gen')['selected'])                                # {'page': ...} condition
        cells = dict(map(tuple, self.wid('rows')['cells']))
        self.assertEqual((cells['Mode'], cells['Beep'], cells['Days'], cells['Date'], cells['Time']), ('Auto', 'Active', '30 d', '31.01.2024', '13:05'))
        self.assertEqual((cells['Fields'], cells['Serial'], cells['Password']), ('Date', 'B123', ''))
        self.assertEqual((self.wid('rows')['count'], len(cells)), (11, 9))              # 9 rows fit: the rest is reached by dragging
        # enum: choice list, current option highlighted
        self.tap_row('rows', 'Mode')
        ov = self.disp()['overlay']
        self.assertEqual(([b['label'] for b in ov['buttons'] if b['selected']], ov['title']), (['Auto'], 'Mode'))
        self.tap_b('Manual')
        # bool off hides the dependent field
        self.tap_row('rows', 'Beep')
        self.tap_b('Inactive')
        self.assertNotIn('Days', [c[0] for c in self.wid('rows')['cells']])
        self.tap_row('rows', 'Beep')
        self.tap_b('Active')
        # number: numeric keyboard, the shown value is replaced by typing, range check keeps the keyboard open
        self.tap_row('rows', 'Days')
        ov = self.disp()['overlay']
        self.assertEqual((ov['kind'], ov['field']), ('keyboard', '30'))
        self.assertTrue(next(b for b in ov['buttons'] if b['id'] == 'field')['selected'])
        self.type_text('500')
        self.assertFalse(self.tap_b('OK'))
        self.assertEqual(self.disp()['overlay']['error'], 'Out of range')
        self.tap_b('Del'), self.tap_b('Del'), self.tap_b('Del')
        self.type_text('45')
        self.assertTrue(self.tap_b('OK'))
        # text: alpha keyboard with shift and the symbol page
        self.tap_row('rows', 'Name')
        self.tap_b('Del')                                                              # deletes the selected text at once
        self.assertEqual(self.disp()['overlay']['field'], '')
        self.tap_b('Shift'), self.tap_b('B'), self.tap_b('Shift'), self.tap_b('a'), self.tap_b('l'), self.tap_b('12@'), self.tap_b('2')
        self.assertEqual(self.disp()['overlay']['field'], 'Bal2')
        self.tap_b('OK')
        # password: masked
        self.tap_row('rows', 'Password')
        self.tap_b('x'), self.tap_b('y')
        self.assertEqual(self.disp()['overlay']['field'], '**')
        self.tap_b('OK')
        # date: + / - columns wrap, the day follows the month length
        self.tap_row('rows', 'Date')
        ov = self.disp()['overlay']
        self.assertEqual([b['label'] for b in ov['buttons'] if b['id'].startswith('val:')], ['31', 'January', '2024'])
        btn = {b['id']: b for b in ov['buttons']}
        x, y, w, h = btn['inc:1']['rect']
        self.p.touch('tft', x + 5, y + 5)                                              # February 2024: day 31 -> 29
        self.assertEqual(self.disp()['overlay']['value'], '2024-02-29')
        x, y, w, h = btn['dec:2']['rect']
        self.p.touch('tft', x + 5, y + 5)                                              # 2023: no 29 February
        self.assertEqual(self.disp()['overlay']['value'], '2023-02-28')
        self.tap_b('OK')
        self.tap_row('rows', 'Time')
        btn = {b['id']: b for b in self.disp()['overlay']['buttons']}
        x, y, w, h = btn['dec:1']['rect']
        for _ in range(6):
            self.p.touch('tft', x + 5, y + 5)
        self.assertEqual(self.disp()['overlay']['value'], '13:59')                    # 05 - 6 wraps to 59 (no carry)
        self.tap_b('OK')
        # multi: check list
        self.tap_row('rows', 'Fields')
        self.tap_b('Select all'), self.tap_b('[x] Time')
        self.tap_b('OK')
        # unsupported row: the device's own response, gesture not accepted
        self.assertFalse(self.tap_row('rows', 'Print'))
        self.assertEqual(self.disp()['overlay']['title'], 'Message')
        self.tap_b('OK')
        self.assertEqual(self.h.calls, [])                                             # nothing written before Save
        self.assertTrue(self.tap_w('save'))
        self.assertEqual(self.h.calls[-1], ('set', {'setup': {'mode': 'm', 'beep': True, 'days': 45, 'name': 'Bal2', 'pw': 'xy', 'day': '2023-02-28',
                                                               'clock': '13:59', 'fields': ['date', 'user']}}))
        self.assertEqual((self.disp()['page'], self.p.frame()['ui']['forms']), ('main', {}))
        # required field left empty: the form's 'invalid' indication, still open
        self.tap_w('h_set')
        self.tap_row('rows', 'Name')
        self.tap_b('Del'), self.tap_b('OK')
        n = len(self.h.calls)
        self.assertFalse(self.tap_w('save'))
        self.assertEqual((self.disp()['overlay']['title'], len(self.h.calls)), ('Message', n))
        self.tap_b('OK')
        self.assertTrue(self.tap_w('close'))                                           # Close discards
        self.assertEqual(len(self.h.calls), n)

    def test_link_rows_live_forms_dialog_forms_home_and_back(self):
        self.tap_w('h_set')
        self.tap_row('rows', 'More')
        self.assertEqual(self.disp()['page'], 'more')
        self.assertTrue(self.p.press('back'))                                          # hardware Back: previous page, form still open
        self.assertEqual((self.disp()['page'], list(self.p.frame()['ui']['forms'])), ('setup', ['setup']))
        self.tap_row('rows', 'Mode')
        self.assertTrue(self.p.press('back'))                                          # Back closes the top overlay first
        self.assertIsNone(self.p.frame()['ui']['overlay'])
        self.tap_row('rows', 'Mode')
        self.assertTrue(self.p.press('home'))                                          # Home discards overlays and forms
        f = self.p.frame()
        self.assertEqual((f['ui']['page'], f['ui']['forms'], f['ui']['overlay']), ('main', {}, None))
        self.tap_w('unit')                                                             # a form that is not open applies each value at once
        self.tap_row('live_rows', 'Unit')
        self.tap_b('mg')
        self.assertEqual((self.h.calls[-1], self.h.state['unit']), (('set', {'unit': 'mg'}), 'mg'))
        self.assertEqual(self.wid('live_rows')['cells'], [['Unit', 'mg']])
        self.p.press('home')
        self.tap_w('pw')                                                               # a dialog with form rows, kept open until saved
        ov = self.disp()['overlay']
        self.assertEqual([b['label'] for b in ov['buttons']], ['New password', 'Cancel', 'OK'])
        x, y, w, h = ov['buttons'][0]['rect']
        self.p.touch('tft', x + 5, y + 5)
        self.type_text('ab')
        self.tap_b('OK')
        self.assertEqual(self.disp()['overlay']['buttons'][0]['value'], '**')
        self.assertEqual(len(self.p.frame()['ui']['overlay_stack']), 0)
        self.tap_b('OK')
        self.assertEqual((self.h.calls[-1], self.p.frame()['ui']['overlay']), (('set', {'pw': 'ab'}), None))

    def test_language_brightness_led_colour_and_glyphs(self):
        self.tap_w('h_set')
        self.assertEqual(self.wid('save')['text'], 'Save')
        img_en = self.p.render('tft')
        self.h.state['lang'] = 'de'
        self.assertEqual((self.disp()['title'], self.wid('save')['text']), ('Einstellungen', 'Speichern'))
        self.assertEqual(dict(map(tuple, self.wid('rows')['cells']))['Beep'], 'Aktiv')   # option texts too
        self.assertFalse((self.p.render('tft') == img_en).all())
        self.h.state['bright'] = 40
        self.assertEqual(self.disp()['brightness'], 40)
        dim = self.p.render('tft')
        self.assertEqual(int(dim.max()), int(255 * 0.4))
        led = self.p.frame()['displays']['light']
        self.assertEqual(led['color'], 'green')
        self.assertEqual(tuple(self.p.render('light')[6, 20]), pu.ROLE_COLORS['green'])
        self.h.state['light'] = 'red'
        self.assertEqual(tuple(self.p.render('light')[6, 20]), pu.ROLE_COLORS['red'])
        self.assertEqual(pu._glyph('é'), pu.FONT5X7['e'])
        self.assertEqual(pu._glyph('日'), pu.FONT5X7['?'])
        self.assertTrue((self.p.render('tft') == self.p.render('tft')).all())          # deterministic

    def test_toolkit_spec_checks_and_capabilities(self):
        self.assertEqual(pu.check_spec(self.spec, {'set'}), [])
        self.assertIn('set', pu.spec_capabilities(self.spec))                          # form writes count as panel capabilities
        bad = copy.deepcopy(self.spec)
        bad['forms']['setup']['fields'][0].pop('evidence')
        bad['forms']['live']['fields'][0]['type'] = 'slider'
        bad['screens']['gui']['nav']['pages']['more']['content'][0]['do'] = [{'do': 'dialog', 'dialog': 'nope'}, {'do': 'form_open', 'form': 'nope'}]
        bad['screens']['gui']['nav']['pages']['more']['content'].append({'type': 'form', 'form': 'nope'})
        errs = pu.check_spec(bad, {'set'})
        for frag in ('fields[0]: evidence missing', "type 'slider'", "dialog 'nope'", "form 'nope'", 'id missing'):
            self.assertTrue(any(frag in e for e in errs), (frag, errs))


# ---------------------------------------------------------------- touch toolkit: runtime-driven options, keyed lookups, routing, layouts
SRC = {
    'spec_version': '0.1', 'ui_class': 'touchscreen_gui',
    'inputs': {'home': {'kind': 'key'}},
    'displays': {'tft': {'kind': 'touch_tft', 'size': [800, 480]}},
    'keyboards': {'qwertz': {'height': 0.45, 'modes': {'lower': [list('qwertz') + ['{bksp}'], ['{cancel}', '{ok}']]}}},
    'keyboard_select': {'alpha': {'state': 'kb', 'map': {'German': 'qwertz'}}},
    'forms': {'f': {'fields': [
        _f('profile', 'enum', 'Profile', options_src={'items': {'state': 'profiles'}, 'value': 'id', 'text': 'name', 'none': 'Default',
                                                        'where': {'row': 'hidden', 'ne': True}}, read={'state': 'sel_profile'},
           write={'capability': 'set', 'args': {'sel_profile': {'value': True}}}),
        _f('mode', 'enum', 'Mode', options=[{'value': 'a', 'text': 'A'}, {'value': 'b', 'text': 'B', 'visible_if': {'state': 'allow_b'}}],
           read={'const': 'a'}, write={'capability': 'set', 'args': {'mode': {'value': True}}}),
        _f('dotted', 'text', {'src': {'state': 'ids', 'path': ['descr.1']}}, read={'state': 'ids', 'path': ['values', 0]},
           write={'capability': 'set', 'args': {'ids': {'object': {'descr.1': {'const': 'Sample ID'}, 'values': {'list': [{'value': True}]}}}}}),
        _f('day', 'info', 'Day', read={'state': 'now_iso', 'slice': [0, 10]})]}},
    'screens': {
        'gui': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'main', 'home': 'home', 'pages': {
            'main': {'title': 'Main', 'content': [
                {'id': 'rows', 'type': 'form', 'form': 'f', 'h': 200},
                {'id': 'res', 'type': 'label', 'h': 40, 'text': {'src': {'state': 'task', 'key_path': 'results', 'key': {'var': 'sel'}, 'key_field': 'index',
                                                                         'path': 'text'}}},
                {'id': 'note', 'type': 'label', 'h': 80, 'wrap': True, 'valign': 'top', 'text': 'A long instruction that the label wraps at blanks to fit into its rectangle, as the screen of the instrument does.'},
                {'id': 'in_pool', 'type': 'label', 'h': 30, 'text': 'pool', 'visible_if': {'const': 'x', 'in': {'src': {'state': 'pool'}}}},
                {'id': 'later', 'type': 'label', 'h': 30, 'text': 'later', 'visible_if': {'state': 'now_iso', 'gt': {'src': {'state': 'then_iso'}}}}],
                'actions': [{'id': 'sel2', 'type': 'button', 'label': 'Second', 'do': [{'do': 'set_var', 'var': 'sel', 'value': {'const': 2}}]}]}}},
              'leave': [{'when': {'state': 'blocked'}, 'to': 'blocked'}, {'when': {'state': 'busy'}, 'to': 'busy'}]},
        'blocked': {'style': 'static', 'layout': {'tft': {'lines': [{'text': 'BLOCKED'}]}}, 'leave_if': {'not': {'state': 'blocked'}}, 'leave_to': 'gui'},
        'busy': {'style': 'static', 'layout': {'tft': {'lines': [{'text': 'BUSY'}]}},
                 'leave': [{'when': {'not': {'state': 'busy'}}, 'to': 'gui'}]}},
    'start_screen': 'gui',
    'vars': {'sel': {'initial': 1}},
}


class TouchToolkitSources(unittest.TestCase):
    def setUp(self):
        self.h = Host(profiles=[{'id': 'p1', 'name': 'Fast'}, {'id': 'p2', 'name': 'Slow', 'hidden': True}], sel_profile=None, allow_b=False,
                      ids={'descr.1': 'Vial', 'values': ['V-7']}, now_iso='2026-10-18T09:30:00', then_iso='2026-10-18T09:00:00', pool=['x', 'y'],
                      task={'results': [{'index': 1, 'text': '1.0000'}, {'index': 2, 'text': '2.5000'}]}, blocked=False, busy=False, kb='English')
        self.p = pu.PanelUI(copy.deepcopy(SRC), self.h)

    def disp(self):
        return self.p.frame()['displays']['tft']

    def wid(self, wid):
        return next(w for w in self.disp()['widgets'] if w['id'] == wid)

    def tap_row(self, label):
        w = self.wid('rows')
        k = [c[0] for c in w['cells']].index(label)
        return self.p.touch('tft', w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + 5)

    def tap_ov(self, label):
        b = next(b for b in self.disp()['overlay']['buttons'] if b['label'] == label)
        return self.p.touch('tft', b['rect'][0] + 3, b['rect'][1] + 3)

    def test_runtime_options_option_visibility_keyed_lookups(self):
        self.assertEqual(pu.check_spec(SRC, {'set'}), [])
        cells = dict(map(tuple, self.wid('rows')['cells']))
        self.assertEqual((cells['Profile'], cells['Vial'], cells['Day']), ('Default', 'V-7', '2026-10-18'))   # label from the runtime, list path
        self.tap_row('Profile')
        self.assertEqual([b['label'] for b in self.disp()['overlay']['buttons']], ['Default', 'Fast', 'Cancel'])   # 'where' drops Slow
        self.tap_ov('Fast')
        self.assertEqual((self.h.state['sel_profile'], dict(map(tuple, self.wid('rows')['cells']))['Profile']), ('p1', 'Fast'))
        self.tap_row('Mode')
        self.assertEqual([b['label'] for b in self.disp()['overlay']['buttons']], ['A', 'Cancel'])   # option visible_if
        self.tap_ov('Cancel')
        self.h.state['allow_b'] = True
        self.tap_row('Mode')
        self.tap_ov('B')
        self.assertEqual(self.h.calls[-1], ('set', {'mode': 'b'}))
        self.assertEqual(self.wid('res')['text'], '1.0000')                     # key_path + key + path
        self.p.touch('tft', *[v + 5 for v in self.wid('sel2')['rect'][:2]])
        self.assertEqual(self.wid('res')['text'], '2.5000')
        ids = {w['id'] for w in self.disp()['widgets']}
        self.assertTrue({'in_pool', 'later'} <= ids)                            # 'in' and 'gt' against sources
        self.h.state['pool'] = ['y']
        self.h.state['then_iso'] = '2026-10-18T10:00:00'
        ids = {w['id'] for w in self.disp()['widgets']}
        self.assertFalse({'in_pool', 'later'} & ids)

    def test_object_list_arguments_keyboard_select_and_wrap(self):
        self.h.state['kb'] = 'German'                                           # the keyboard layout setting picks the layout
        self.tap_row('Vial')
        keys = [b['label'] for b in self.disp()['overlay']['buttons'] if b['id'].startswith('key:')]
        self.assertEqual(keys[:6], list('qwertz'))
        self.assertEqual(self.disp()['overlay']['title'], 'Vial')               # the field label binding is the keyboard title
        self.tap_ov('Del'), self.tap_ov('t'), self.tap_ov('z'), self.tap_ov('OK')
        self.assertEqual(self.h.calls[-1], ('set', {'ids': {'descr.1': 'Sample ID', 'values': ['tz']}}))   # object / list sources
        self.assertTrue(self.wid('note')['wrap'])
        img = self.p.render('tft')
        x, y, w, h = self.wid('note')['rect']
        rows_with_ink = [r for r in range(y, y + h) if (img[r, x:x + w].sum(axis=1) < 300).any()]
        self.assertGreater(max(rows_with_ink) - min(rows_with_ink), 20)        # wrapped onto a second line

    def test_leave_rules_route_between_screens(self):
        self.assertEqual(self.p.frame()['screen'], 'gui')
        self.h.state['busy'] = True
        self.assertEqual(self.p.frame()['screen'], 'busy')                     # the first matching rule wins
        self.h.state['blocked'] = True
        self.h.state['busy'] = False
        self.assertEqual(self.p.frame()['screen'], 'gui')                      # busy screen leaves to gui ...
        self.assertEqual(self.p.frame()['screen'], 'blocked')                  # ... whose first rule now holds
        self.h.state['blocked'] = False
        self.assertEqual(self.p.frame()['screen'], 'gui')
        bad = copy.deepcopy(SRC)
        bad['screens']['gui']['leave'][0]['to'] = 'nowhere'
        self.assertTrue(any("'nowhere'" in e for e in pu.check_spec(bad, {'set'})))


# ---------------------------------------------------------------- trend chart widget and widget guards (Dionex Integrion mobile app)
INTEGRION = {
    'spec_version': '0.1', 'ui_class': 'touchscreen_gui',
    'inputs': {},
    'displays': {'tab': {'kind': 'touch_tft', 'size': [400, 240]}},
    'dialogs': {'locked': {'title': 'Sequence running', 'lines': ['Interrupt?'], 'buttons': [
        {'label': 'Stop', 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'running': {'const': False}}}]}, {'label': 'Wait', 'do': []}]}},
    'screens': {'app': {'style': 'touch_gui', 'nav': {'display': 'tab', 'start': 'home', 'pages': {
        'home': {'widgets': [
            {'id': 'chart', 'type': 'plot', 'rect': [0, 0, 400, 160], 'span': {'state': 'span'}, 'x_fmt': {'decimals': 1},
             'series': [{'src': {'state': 'cd'}, 'color': 'blue', 'label': 'CD'},
                        {'src': {'state': 'p'}, 'axis': 'right', 'color': 'red', 'visible_if': {'state': 'show_p'}}],
             'y_max': {'left': {'state': 'range'}}, 'do': [{'do': 'page', 'page': 'other'}]},
            {'id': 'flow', 'type': 'toggle', 'rect': [0, 170, 200, 40], 'label': 'Flow', 'read': {'state': 'flow'}, 'write': W('flow', 'value'),
             'guard': {'when': {'state': 'running'}, 'do': [{'do': 'dialog', 'dialog': 'locked'}]}}]},
        'other': {'widgets': [{'id': 'back', 'type': 'button', 'rect': [0, 0, 100, 40], 'label': 'Back', 'do': [{'do': 'page_back'}]}]}}}}},
    'start_screen': 'app',
}


class PlotAndGuardThermoDionexintegrion(unittest.TestCase):
    def setUp(self):
        self.h = Host(cd=[[t, 10.0 + t] for t in range(0, 120, 10)], p=[[0, 5.0], [100, 7.0]], span=60.0, show_p=False, range=None, flow=False,
                      running=False)
        self.p = pu.PanelUI(INTEGRION, self.h)

    def chart(self):
        return next(w for w in self.p.frame()['displays']['tab']['widgets'] if w['id'] == 'chart')

    def test_plot_window_ranges_and_pixels(self):
        c = self.chart()
        self.assertEqual(c['x_range'], [50.0, 110.0])                           # the window ends at the newest point
        self.assertEqual([p[0] for p in c['series'][0]['points']], [50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 110.0])
        self.assertEqual(len(c['series']), 1)                                    # hidden series
        self.assertAlmostEqual(c['y_range']['left'][1], 120.0 + 0.05 * 120.0)   # automatic: newest maximum plus 5 %
        self.h.state.update(range=1000.0, show_p=True, span=300.0)
        c = self.chart()
        self.assertEqual(c['x_range'], [0.0, 300.0])                             # never before x_min + span
        self.assertEqual(c['y_range']['left'], [0.0, 1000.0])
        self.assertEqual(c['y_range']['right'], [0.0, 7.0 + 0.05 * 7.0])
        img = self.p.render('tab')
        blue, red = pu.ROLE_COLORS['blue'], pu.ROLE_COLORS['red']
        self.assertTrue(((img == blue).all(axis=2)).any() and ((img == red).all(axis=2)).any())
        self.assertTrue((self.p.render('tab') == img).all())                    # deterministic
        self.h.state['cd'] = []
        self.assertEqual(self.chart()['series'][0]['points'], [])
        self.assertTrue(self.p.touch('tab', 200, 80))                            # a chart with 'do' opens its page
        self.assertEqual(self.p.frame()['ui']['page'], 'other')

    def test_guard_shows_the_warning_instead_of_the_action(self):
        self.assertTrue(self.p.touch('tab', 100, 190))
        self.assertEqual(self.h.calls[-1], ('set', {'flow': True}))
        self.h.state['running'] = True
        n = len(self.h.calls)
        self.assertTrue(self.p.touch('tab', 100, 190))
        self.assertEqual(len(self.h.calls), n)                                  # the toggle did not write
        ov = self.p.frame()['displays']['tab']['overlay']
        self.assertEqual(ov['dialog'], 'locked')
        stop = next(b for b in ov['buttons'] if b['label'] == 'Stop')
        x, y, w, h = stop['rect']
        self.p.touch('tab', x + w // 2, y + h // 2)
        self.assertFalse(self.h.state['running'])
        self.assertIn('set', pu.spec_capabilities(INTEGRION))
        self.assertEqual(pu.check_spec(INTEGRION, {'set'}), [])
        bad = copy.deepcopy(INTEGRION)
        bad['screens']['app']['nav']['pages']['home']['widgets'][1]['guard']['do'] = [{'do': 'dialog', 'dialog': 'nowhere'}]
        self.assertTrue(any("'nowhere'" in e for e in pu.check_spec(bad, {'set'})))


# ---------------------------------------------------------------- image widget (iBright imager viewport)
IMAGER = {
    'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {},
    'displays': {'tft': {'kind': 'touch_tft', 'size': [320, 240]}},
    'screens': {'app': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'view', 'pages': {'view': {'widgets': [
        {'id': 'vp', 'type': 'image', 'rect': [0, 0, 320, 200], 'src': {'state': 'bitmap'}, 'max_px': [40, 30],
         'do': [{'do': 'invoke', 'capability': 'set', 'args': {'roi': {'list': [{'event': 'u'}, {'event': 'v'}, {'event': 'u2'}, {'event': 'v2'}]}}}]},
        {'id': 'still', 'type': 'image', 'rect': [0, 200, 320, 40], 'src': {'state': 'none'}}]}}}}},
    'start_screen': 'app',
}


class ImageWidgetThermoIbrightfl1500(unittest.TestCase):
    def setUp(self):
        grey = [[(x * 4) % 256 for x in range(80)] for _ in range(20)]               # 80 x 20 grey ramp: thinned to 40 x 20
        grey[0][0] = [255, 0, 0]                                                      # one rgb pixel
        self.h = Host(bitmap=grey, none=None, roi=None)
        self.p = pu.PanelUI(IMAGER, self.h)

    def vp(self):
        return next(w for w in self.p.frame()['displays']['tft']['widgets'] if w['id'] == 'vp')

    def test_pixels_scaling_and_touch_fractions(self):
        w = self.vp()
        self.assertEqual((w['pw'], w['ph']), (40, 20))
        self.assertEqual(w['pixels'][0][0], [255, 0, 0])
        self.assertEqual(w['pixels'][0][1], 8)
        img = self.p.render('tft')
        bx, by, bw, bh = pu._image_box(w['rect'], w)
        self.assertEqual((bw, bh), (320, 160))                                       # whole-pixel scale 8, centred
        self.assertEqual(tuple(int(v) for v in img[by + 2, bx + 2]), (255, 0, 0))
        self.assertEqual(tuple(int(v) for v in img[by - 2, bx + 2]), pu.ROLE_COLORS['black'])
        self.assertTrue(self.p.touch('tft', bx + bw // 4, by + bh // 2, to=(bx + bw * 3 // 4, by + bh - 1)))
        u, v, u2, v2 = self.h.state['roi']
        self.assertEqual((u, v, u2), (0.25, 0.5, 0.75))
        self.assertAlmostEqual(v2, 1.0, delta=0.01)
        self.assertFalse(self.p.touch('tft', 10, 220))                                 # no bitmap, no 'do': passive
        self.assertEqual(next(w for w in self.p.frame()['displays']['tft']['widgets'] if w['id'] == 'still')['pixels'], [])
        self.assertTrue((self.p.render('tft') == img).all())


# ---------------------------------------------------------------- segment LCD with a dot-matrix text line (BINDER FD 56 R4 controller)
R4 = {
    'spec_version': '0.1', 'ui_class': 'parameter_cycle', 'inputs': {'ok': {'kind': 'key'}},
    'displays': {'lcd': {'kind': 'segment_lcd', 'size': [200, 70], 'colors': {'background': [0, 0, 0], 'segment': [255, 255, 255], 'ghost': [40, 40, 40]},
                         'fields': {'main': {'x': 10, 'y': 4, 'cells': 4, 'cell_w': 20, 'cell_h': 36, 'gap': 6},
                                    'text': {'font': '5x7', 'x': 10, 'y': 50, 'cells': 10, 'scale': 1}}}},
    'screens': {'home': {'layout': {'lcd': {'fields': {'main': {'src': {'state': 'temp'}, 'fmt': {'decimals': 0}},
                                                       'text': {'parts': [{'text': 'Setp'}, {'src': {'state': 'sp'}, 'fmt': {'decimals': 0}, 'pad': 6}]}}}}}},
}


class BinderFd056DotField(unittest.TestCase):
    def test_dot_matrix_line_beside_seven_segment_digits(self):
        h = Host(temp=25.0, sp=105.0)
        p = pu.PanelUI(R4, h)
        self.assertEqual(pu.check_spec(R4), [])
        f = p.frame()['displays']['lcd']['fields']
        self.assertEqual((f['main']['shown'], f['text']['shown']), ('25', 'Setp   105'))
        img = p.render('lcd')
        for i, ch in enumerate('Setp   105'):                                 # every dot of every glyph, one pixel per dot (scale 1)
            for cx, bits in enumerate(pu.FONT5X7[ch]):
                for cy in range(7):
                    lit = int(img[50 + cy, 10 + i * 6 + cx].sum()) > 600
                    self.assertEqual(lit, bool(bits >> cy & 1), (ch, cx, cy))
        self.assertEqual(tuple(img[52, 10 + 11 * 6]), (0, 0, 0))              # right of the 10 cells: no ghost strip
        self.assertEqual(tuple(img[52, 10 + 9 * 6 + 5]), (40, 40, 40))         # the unlit strip of the line is ghost-coloured
        self.assertGreater(int(img[4 + 18, 10 + 3 * 26 + 10].sum()), 600)      # 'g' segment of the last digit ('5') still drawn as segments


# ---------------------------------------------------------------- six softkeys around a graphic LCD (Alicat-like lists, option lists, digit entry)
_ED = {'when': {'editing': True}}
SIX = {
    'spec_version': '0.1', 'ui_class': 'text_menu',
    'inputs': {k: {'kind': 'softkey'} for k in ('tl', 'tc', 'tr', 'bl', 'bc', 'br')},
    'displays': {'g': {'kind': 'graphic_lcd', 'size': [136, 100], 'text_scale': 1, 'menu_title': False, 'menu_values': 'inline',
                       'softkey_rows': {'top': ['tl', 'tc', 'tr'], 'bottom': ['bl', 'bc', 'br']}, 'rotate_if': {'state': 'inverted'}}},
    'menus': {'m': {'label': 'SETUP', 'items': [
        {'id': 'gas', 'label': 'Active Gas:', 'type': 'submenu', 'value': {'src': {'state': 'gas'}}, 'mark': '>', 'evidence': 'x', 'items': [
            {'id': 'n2', 'label': 'N2 Nitrogen', 'type': 'action', 'gas_id': 'N2', 'write': {'capability': 'set', 'args': {'gas': {'item': 'gas_id'}}},
             'evidence': 'x'}]},
        {'id': 'src', 'label': 'Source:', 'type': 'enum', 'edit_style': 'list', 'mark': '>', 'evidence': 'x', 'read': {'state': 'src'}, 'write': W('src'),
         'options': [{'value': 'serial', 'text': 'Serial'}, {'value': 'analog', 'text': 'Analog'}]},
        {'id': 'umax', 'label': 'User Max:', 'type': 'number', 'entry': 'digits', 'digits': 5, 'decimals': 3, 'min': 0, 'max': 10, 'mark': '>',
         'read': {'state': 'umax'}, 'write': W('umax'), 'evidence': 'x',
         'edit_layout': {'g': {'lines': [{'text': 'Maximum Setpoint'}, {'parts': [{'text': '+'}, {'src': {'current': 'text'}}]},
                                         {'src': {'current': 'digit'}, 'map': {str(i): ' ' * (i + 1 + (i > 1)) + '^' for i in range(5)}}]}}},
        {'id': 'mix', 'label': {'parts': [{'text': 'Mix '}, {'src': {'state': 'mixname'}}]}, 'type': 'info', 'evidence': 'x'}]}},
    'screens': {'list': {'style': 'softkey_menu', 'nav': {'menu': 'm', 'softkeys': [
        {'input': 'tl', 'label': 'SELECT\nDIGIT', 'when': {'current': 'entry', 'eq': 'digits'}, 'do': [{'do': 'nav', 'op': 'digit_right', 'wrap': True}]},
        {'input': 'tc', 'label': 'UP', 'when': {'not': {'editing': True}}, 'op': 'up'},
        {'input': 'tc', 'label': 'UP', 'when': {'current': 'type', 'eq': 'number'}, 'do': [{'do': 'nav', 'op': 'change', 'n': 1}]},
        {'input': 'tc', 'label': 'UP', **_ED, 'do': [{'do': 'nav', 'op': 'change', 'n': -1}]},
        {'input': 'tr', 'label': 'DOWN', 'when': {'not': {'editing': True}}, 'op': 'down'},
        {'input': 'tr', 'label': 'DOWN', **_ED, 'do': [{'do': 'nav', 'op': 'change', 'n': 1}]},
        {'input': 'bl', 'label': 'BACK', 'when': {'not': {'editing': True}}, 'op': 'back'},
        {'input': 'bl', 'label': 'CANCEL', **_ED, 'op': 'back'},
        {'input': 'bc', 'label': 'CLEAR', 'when': {'current': 'type', 'eq': 'number'}, 'do': [{'do': 'nav', 'op': 'set', 'value': 0}]},
        {'input': 'br', 'label': 'SELECT', 'when': {'not': {'editing': True}}, 'op': 'enter'},
        {'input': 'br', 'label': 'SET', **_ED, 'op': 'enter'}]}}},
    'start_screen': 'list',
    'layout_base': {'g': {'widgets': [{'id': 'big', 'rect': [0, 60, 136, 20], 'text': {'src': {'state': 'umax'}, 'fmt': {'decimals': 3}}, 'scale': 2,
                                       'align': 'right', 'visible_if': {'state': 'big'}}]}},
}


class AlicatSoftkeyRows(unittest.TestCase):
    def setUp(self):
        self.h = Host(gas='Air', src='serial', umax=1.0, mixname='MyGas1', inverted=False, big=False)
        self.p = pu.PanelUI(SIX, self.h)

    def lines(self):
        return self.p.frame()['displays']['g']['lines']

    def test_label_rows_inline_values_marks_and_item_sources(self):
        self.assertEqual(pu.check_spec(SIX, {'set'}), [])
        ls = self.lines()
        self.assertEqual(len(ls), 10)                                           # 100 px, 9 px per line
        self.assertEqual(ls[0].split(), ['UP', 'DOWN'])
        self.assertEqual(ls[1], '>Active Gas: Air     >')                       # no title line, 'Label: value', the submenu mark at the right
        self.assertEqual(ls[2], ' Source: Serial      >')
        self.assertEqual(ls[4], ' Mix MyGas1')                                  # a label bound to runtime state
        self.assertEqual(ls[-1].split(), ['BACK', 'SELECT'])
        self.p.press('br'), self.p.press('br')                                   # into the submenu, then the action takes its gas id from the item
        self.assertEqual(self.h.calls[-1], ('set', {'gas': 'N2'}))

    def test_option_list_editor_and_digit_entry_screen(self):
        self.p.press('tr'), self.p.press('br')                                   # Source: the options, the cursor on the current one
        self.assertEqual(self.lines()[1:3], ['>Serial', ' Analog'])
        self.assertEqual(self.lines()[-1].split(), ['CANCEL', 'SET'])
        self.p.press('tr')
        self.assertEqual(self.lines()[1:3], [' Serial', '>Analog'])
        self.p.press('br')
        self.assertEqual(self.h.state['src'], 'analog')
        self.p.press('tr'), self.p.press('br')                                   # User Max: its own entry screen, two-line SELECT DIGIT label
        ls = self.lines()
        self.assertEqual((ls[0].split(), ls[1].split()), (['SELECT', 'UP', 'DOWN'], ['DIGIT']))
        self.assertEqual(ls[2:5], ['Maximum Setpoint', '+01.000', ' ^'])
        for _ in range(6):                                                       # SELECT DIGIT wraps over the five digits
            self.p.press('tl')
        self.assertEqual(self.p.frame()['ui']['editor']['digit'], 1)
        self.p.press('tc')
        self.assertEqual(self.lines()[3:5], ['+02.000', '  ^'])
        self.p.press('bc')                                                        # CLEAR puts 0 into the field
        self.assertEqual(self.lines()[3], '+00.000')
        self.p.press('tl'), self.p.press('tl'), self.p.press('tc'), self.p.press('br')
        self.assertEqual(self.h.state['umax'], 0.1)

    def test_rotation_and_static_widget_options(self):
        a = self.p.render('g')
        self.h.state['inverted'] = True
        self.assertTrue(self.p.frame()['displays']['g']['rotated'])
        self.assertTrue((self.p.render('g') == a[::-1, ::-1]).all())
        self.h.state.update(inverted=False, big=True)
        w = self.p.frame()['displays']['g']['widgets'][0]
        self.assertEqual((w['text'], w['scale'], w['align']), ('1.000', 2, 'right'))
        img = self.p.render('g')
        self.assertTrue((img[62:78, 0:60] == img[2, 135]).all())                 # right-aligned at scale 2: the left part stays background
        self.assertFalse((img[62:78, 76:134] == img[2, 135]).all())

    def test_scale_and_offset_follow_a_unit_setting(self):
        self.h.state.update(unit='F', temp_k=300.0)
        temp = {'state': 'temp_k', 'scale': {'state': 'unit', 'map': {'C': 1, 'F': 1.8}}, 'offset': {'state': 'unit', 'map': {'C': -273.15, 'F': -459.67}}}
        self.p._cache = None
        self.assertAlmostEqual(self.p._src(temp, {}), 80.33, places=6)
        self.h.state['unit'] = 'C'
        self.p._cache = None
        self.assertAlmostEqual(self.p._src(temp, {}), 26.85, places=6)
        self.assertAlmostEqual(self.p._src({'const': 2.0, 'scale': [3, {'state': 'temp_k'}]}, {}), 1800.0)   # a list of factors
        self.assertIsNone(self.p._src({'const': 2.0, 'scale': {'state': 'missing'}}, {}))

    def test_return_to_a_remembered_line(self):
        self.p.press('tr'), self.p.press('tr')
        self.assertEqual(self.p._src({'menu_path': True}, {}), [2])
        self.p._act({'do': 'set_var', 'var': 'back_to', 'value': {'menu_path': True}}, {})
        self.p.press('tr')
        self.p._act({'do': 'goto', 'screen': 'list', 'path': {'var': 'back_to'}}, {})
        self.assertEqual(self.p.frame()['ui']['menu']['item'], 'umax')


# ---------------------------------------------------------------- field editor from a button, time-out from a setting, page from a source (Bio-Rad CFX Opus)
CFX = {
    'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {},
    'displays': {'scr': {'kind': 'touch_tft', 'size': [400, 240]}},
    'vars': {'saved': {'initial': None}},
    'forms': {'f': {'fields': [{'id': 'n', 'label': 'N', 'type': 'number', 'min': 0, 'max': 10, 'read': {'state': 'n'}, 'write': W('n', 'value'),
                                'evidence': 'x'}]}},
    'screens': {
        'app': {'style': 'touch_gui', 'timeout_s': {'cases': [{'when': {'state': 'saver'}, 'state': 'idle_s'}]}, 'timeout_screen': 'saver',
                'on_timeout': [{'do': 'set_var', 'var': 'saved', 'value': {'cases': [{'when': {'page': 'other'}, 'const': 'other'}, {'const': 'home'}]}}],
                'nav': {'display': 'scr', 'start': 'home', 'pages': {
                    'home': {'widgets': [{'id': 'edit', 'type': 'button', 'rect': [0, 0, 100, 40], 'label': 'N',
                                          'do': [{'do': 'form_edit', 'form': 'f', 'field': 'n'}]},
                                         {'id': 'go', 'type': 'button', 'rect': [0, 50, 100, 40], 'label': 'Other', 'do': [{'do': 'page', 'page': 'other'}]}]},
                    'other': {'widgets': [{'id': 'back', 'type': 'button', 'rect': [0, 0, 100, 40], 'label': 'Back', 'do': [{'do': 'page_back'}]}]}}}},
        'saver': {'style': 'static', 'bindings': [{'on': {'event': 'touch'}, 'do': [{'do': 'goto', 'screen': 'app'},
                                                                                    {'do': 'page', 'page': {'var': 'saved'}}]}]}},
    'start_screen': 'app',
}


class BioradCfxopusFieldEditTimeoutPage(unittest.TestCase):
    def setUp(self):
        self.h = Host(n=1, saver=False, idle_s=60.0)
        self.p = pu.PanelUI(CFX, self.h)

    def key(self, tok):
        b = next(b for b in self.p.frame()['displays']['scr']['overlay']['buttons'] if b['id'] == 'key:' + tok)
        x, y, w, h = b['rect']
        return self.p.touch('scr', x + w // 2, y + h // 2)

    def test_form_edit_opens_the_field_editor(self):
        self.assertTrue(self.p.touch('scr', 50, 20))
        ov = self.p.frame()['displays']['scr']['overlay']
        self.assertEqual((ov['kind'], ov['field']), ('keyboard', '1'))
        self.key('{bksp}'), self.key('7'), self.key('{ok}')
        self.assertEqual(self.h.calls[-1], ('set', {'n': 7}))                   # form not open: the field's own write
        self.p.ui['overlay'] = None
        self.p._form_open(dict(form='f'), {})
        self.assertTrue(self.p.touch('scr', 50, 20))
        self.key('{bksp}'), self.key('3'), self.key('{ok}')
        self.assertEqual(self.p.frame()['ui']['forms']['f']['n'], 3)            # open form: into its buffer
        self.assertEqual(pu.check_spec(CFX, {'set'}), [])
        bad = copy.deepcopy(CFX)
        bad['screens']['app']['nav']['pages']['home']['widgets'][0]['do'][0]['field'] = 'm'
        self.assertTrue(any("field 'm'" in e for e in pu.check_spec(bad, {'set'})))

    def test_time_out_from_a_setting_and_page_from_a_source(self):
        self.assertTrue(self.p.touch('scr', 50, 70))
        self.h.t = 1000.0
        self.assertEqual(self.p.frame()['screen'], 'app')                       # no time-out while the setting is off
        self.h.state['saver'] = True
        self.assertEqual(self.p.frame()['screen'], 'saver')
        self.assertTrue(self.p.touch('scr', 10, 10))
        self.assertEqual((self.p.frame()['screen'], self.p.frame()['ui']['page']), ('app', 'other'))
        self.h.t = 1059.0
        self.assertEqual(self.p.frame()['screen'], 'app')
        self.h.t = 1060.0
        self.assertEqual(self.p.frame()['screen'], 'saver')


# ---------------------------------------------------------------- touch screen with a knob whose push and an ENTER key both select (Keithley 2450)
K2450 = {
    'spec_version': '0.1', 'ui_class': 'touchscreen_gui',
    'inputs': {'knob': {'kind': 'encoder', 'push_input': 'knob_push'}, 'knob_push': {'kind': 'key'}, 'enter': {'kind': 'key'}},
    'displays': {'scr': {'kind': 'touch_tft', 'size': [200, 100]}},
    'screens': {'app': {'style': 'touch_gui', 'nav': {'display': 'scr', 'start': 'home', 'knob': 'knob', 'push': ['knob_push', 'enter'], 'pages': {
        'home': {'widgets': [{'id': 'a', 'type': 'button', 'label': '2 Ω', 'rect': [0, 0, 100, 40], 'do': [{'do': 'invoke', 'capability': 'set',
                                                                                                          'args': {'a': {'const': 1}}}]},
                             {'id': 'b', 'type': 'button', 'label': '2 ?', 'rect': [100, 0, 100, 40], 'do': [{'do': 'invoke', 'capability': 'set',
                                                                                                          'args': {'b': {'const': 1}}}]}]}}}}},
}


class SignatoneQuadpro2PushKeysAndOhm(unittest.TestCase):
    def test_knob_push_and_enter_both_select_the_focused_widget(self):
        h = Host()
        p = pu.PanelUI(K2450, h)
        self.assertEqual(pu.check_spec(K2450, {'set'}), [])
        self.assertTrue(p.turn('knob', 1))
        self.assertTrue(p.press('enter'))
        self.assertEqual(h.calls[-1], ('set', {'a': 1}))
        self.assertTrue(p.turn('knob', 1))
        self.assertTrue(p.push('knob'))
        self.assertEqual(h.calls[-1], ('set', {'b': 1}))

    def test_pass_through_message_does_not_take_the_touch(self):
        spec = copy.deepcopy(K2450)
        spec['screens']['app']['nav']['pages']['home']['widgets'].append({'id': 'toast', 'type': 'label', 'text': 'Error', 'rect': [0, 0, 200, 40],
                                                                          'pass_through': True})
        h = Host()
        p = pu.PanelUI(spec, h)
        self.assertTrue(p.touch('scr', 50, 20))
        self.assertEqual(h.calls[-1], ('set', {'a': 1}))
        spec['screens']['app']['nav']['pages']['home']['widgets'][-1]['pass_through'] = False
        self.assertFalse(pu.PanelUI(spec, Host()).touch('scr', 50, 20))          # without it the label on top takes the touch

    def test_ohm_glyph_is_drawn(self):
        self.assertNotEqual(pu._glyph('Ω'), pu._glyph('?'))
        self.assertEqual(pu._glyph('Ω'), pu._glyph('Ω'))              # the ohm sign normalizes to omega
        img = pu.PanelUI(K2450, Host()).render('scr')
        self.assertFalse((img[:40, :100] == img[:40, 100:]).all())


class QuorumQ150tesEditorTitle(unittest.TestCase):
    def test_field_title_heads_its_editor(self):
        spec = copy.deepcopy(CFX)
        spec['forms']['f']['fields'][0]['title'] = 'N (0 - 10)'
        p = pu.PanelUI(spec, Host(n=1, saver=False, idle_s=60.0))
        self.assertEqual(pu.check_spec(spec, {'set'}), [])
        self.assertTrue(p.touch('scr', 50, 20))
        self.assertEqual(p.frame()['displays']['scr']['overlay']['title'], 'N (0 - 10)')
        q = pu.PanelUI(CFX, Host(n=1, saver=False, idle_s=60.0))                 # without a title: the field label
        q.touch('scr', 50, 20)
        self.assertEqual(q.frame()['displays']['scr']['overlay']['title'], 'N')


# ---------------------------------------------------------------- text dialogs (Metrohm 899 style): function bar, grid entry, selection lists
DLG_NAV = dict(up='up', down='down', left='left', right='right', enter='ok', back='back', back_confirms=True, settle=True)
DLG = {
    'spec_version': '0.1', 'ui_class': 'text_menu',
    'inputs': {k: {'kind': 'arrow_key' if k in ('up', 'down', 'left', 'right') else 'key'} for k in ('up', 'down', 'left', 'right', 'ok', 'back')},
    'displays': {'g': {'kind': 'graphic_lcd', 'size': [244, 76], 'text_scale': 1, 'rows': 8, 'cols': 20, 'menu_cursor': 'inverse'}},
    'char_grids': {'txt': {'rows': [list('ABC'), list('012'), ['{accept}', '{cancel}', '{clear}', '{bksp}'], ['{left}', '{right}', '{grid:more}']]},
                   'more': {'rows': [[{'label': 'XY#', 'insert': 'XY'}]], 'return_after': True},
                   'num': {'rows': [list('0123456789.-'), ['{accept}', '{cancel}', '{set:off}']]}},
    'menus': {'m': {'label': 'Setup', 'items': [
        {'id': 'name', 'label': 'Name', 'type': 'text', 'entry': 'grid', 'grid': 'txt', 'max_len': 6, 'read': {'state': 'name'}, 'write': W('name'),
         'evidence': 'x'},
        {'id': 'lim', 'label': 'Limit', 'type': 'number', 'entry': 'grid', 'grid': 'num', 'min': 1, 'max': 99, 'step': 1, 'integer': True,
         'none_text': 'off', 'specials': {'off': None}, 'unit': 's', 'read': {'state': 'lim'}, 'write': W('lim'), 'evidence': 'x'},
        {'id': 'skip', 'label': 'Locked', 'type': 'info', 'enabled_if': {'state': 'unlocked'}, 'evidence': 'x'},
        {'id': 'mode', 'label': 'Mode', 'type': 'enum', 'edit_style': 'list', 'options': [{'value': 'a', 'text': 'auto'}, {'value': 'm', 'text': 'man'}],
         'read': {'state': 'mode'}, 'write': W('mode'), 'evidence': 'x'},
        {'id': 'pick', 'label': 'Pick', 'type': 'enum', 'edit_style': 'list', 'read': {'state': 'pick'}, 'write': W('pick'), 'evidence': 'x',
         'options_src': {'items': {'state': 'rows'}, 'value': 'v', 'text': 't', 'none': 'empty'}}]},
        'rows': {'label': 'Rows', 'items': [{'id': f'r{k}', 'label': {'src': {'state': 'rows', 'path': [k, 't']}}, 'type': 'info', 'data': k,
                                             'visible_if': {'state': 'rows', 'path': [k]}, 'evidence': 'x'} for k in range(4)]}},
    'screens': {
        'dlg': {'style': 'arrow_menu', 'nav': dict(DLG_NAV, menu='m', title={'parts': [{'src': {'menu': 'title'}, 'pad': 14, 'align': 'left'},
                                                                                      {'src': {'state': 'status'}, 'pad': 6, 'inverse': True}]})},
        'tab': {'style': 'arrow_menu', 'nav': dict(DLG_NAV, menu='rows', exit_screen='dlg', footer={'text': 'line'}, function_bar=[
            {'id': 'pick', 'label': 'Pick', 'needs_item': True, 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'picked': {'menu': 'data'}}}]},
            {'id': 'drop', 'label': 'Drop', 'visible_if': {'state': 'unlocked'}, 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'dropped': True}}]},
            {'id': 'long', 'label': 'Longlabel', 'do': [{'do': 'goto', 'screen': 'dlg'}]}])},
        'live': {'style': 'static', 'nav': dict(left='left', right='right', enter='ok', function_bar=[
            {'id': 'a', 'label': 'Alpha', 'do': [{'do': 'invoke', 'capability': 'set', 'args': {'a': 1}}]},
            {'id': 'b', 'label': {'cases': [{'when': {'state': 'on'}, 'text': 'Off'}, {'text': 'On'}]},
             'do': [{'do': 'invoke', 'capability': 'toggle', 'args': {'key': 'on'}}]}]),
                 'layout': {'g': {'lines': [{'text': 'LIVE'}], 'widgets': [{'id': 'c', 'type': 'plot', 'rect': [2, 20, 100, 40],
                                                                            'series': [{'src': {'state': 'curve'}}], 'span': 10.0}]}}}},
    'start_screen': 'dlg',
    'errors': {'entry': {'indication': [{'do': 'message', 'text': 'Invalid'}]}},
}


class TextDialogsMetrohm899(unittest.TestCase):
    def setUp(self):
        self.h = Host(name='AB', lim=None, mode='a', status='ready', unlocked=False, pick=None,
                      rows=[{'v': 'm1', 't': 'One'}, {'v': 'm2', 't': 'Two'}], on=False, curve=[[0, 0], [5, 2], [10, 3]])
        self.p = pu.PanelUI(DLG, self.h)

    def disp(self):
        return self.p.frame()['displays']['g']

    def test_title_status_span_and_values(self):
        self.assertEqual(pu.check_spec(DLG, {'set', 'toggle'}), [])
        d = self.disp()
        self.assertEqual(d['lines'][0], 'Setup          ready')
        self.assertEqual(d['inverse_spans'][0], [[14, 20]])                       # the status field is inverted
        self.assertTrue(d['lines'][2].startswith('Limit') and d['lines'][2].endswith('off'))   # switched off: no unit
        img = self.p.render('g')
        self.assertTrue((img[2:9, 2 + 15 * 6] < 100).any())                   # inverted cell drawn dark

    def test_grid_text_entry_cursor_functions_and_accept(self):
        p, h = self.p, self.h
        self.assertTrue(p.press('ok'))                                          # Name opens the character grid with the value
        ed = p.frame()['ui']['editor']
        self.assertEqual((ed['typed'], ed['digit']), ('AB', 2))
        self.assertEqual(self.disp()['lines'][1].rstrip(), 'AB_')
        p.press('right'), p.press('ok')                                        # 'B' inserted at the end
        p.press('down'), p.press('down'), p.press('down'), p.press('left'), p.press('ok')   # '<-|' moves the cursor left
        p.press('up'), p.press('right'), p.press('right'), p.press('right'), p.press('ok')  # [<-]: backspace left of the cursor
        self.assertEqual((p.frame()['ui']['editor']['typed'], p.frame()['ui']['editor']['digit']), ('AB', 1))
        p.press('down'), p.press('ok')                                         # a sub-grid (like Var), one entry, then back
        self.assertEqual(p.frame()['ui']['editor']['grid_name'], 'more')
        p.press('ok')
        self.assertEqual(p.frame()['ui']['editor']['typed'], 'AXYB')
        self.assertIsNone(p.frame()['ui']['editor']['grid_name'])
        self.assertTrue(p.press('back'))                                        # BACK = Accept
        self.assertEqual(h.state['name'], 'AXYB')
        p.press('ok'), p.press('down'), p.press('down'), p.press('right'), p.press('ok')   # Cancel leaves the value
        self.assertIsNone(p.frame()['ui']['editor'])
        self.assertEqual(h.state['name'], 'AXYB')
        p.press('ok')
        for _ in range(4):
            p.press('ok')                                                       # max_len 6: the 7th character is refused
        self.assertFalse(p.press('ok'))
        self.assertEqual(p.frame()['ui']['editor']['typed'], 'AXYBAA')

    def test_grid_number_range_special_off_and_integer(self):
        p, h = self.p, self.h
        p.press('down'), p.press('ok')
        p.press('right'), p.press('ok'), p.press('left'), p.press('ok')       # '10'
        p.press('down'), p.press('ok')                                         # Accept
        self.assertEqual(h.state['lim'], 10)
        self.assertIsInstance(h.state['lim'], int)
        p.press('ok'), p.press('ok'), p.press('ok')                             # '1000' > 99: refused, editor stays with the text
        p.press('down'), p.press('ok')
        self.assertEqual(p.frame()['ui']['message']['text'], 'Invalid')
        self.assertEqual(p.frame()['ui']['editor']['typed'], '1000')
        p.press('right'), p.press('right'), p.press('ok')                       # the 'off' entry
        self.assertIsNone(h.state['lim'])
        self.assertIsNone(p.frame()['ui']['editor'])

    def test_disabled_items_skipped_and_list_edit_direction(self):
        p, h = self.p, self.h
        p.press('down'), p.press('down')
        self.assertEqual(p.frame()['ui']['menu']['item'], 'mode')               # 'Locked' is shown but skipped
        self.assertIn('Locked', self.disp()['lines'][3])
        p.press('ok')
        self.assertEqual(self.disp()['lines'][0], 'Mode')                       # the selection list
        self.assertTrue(self.disp()['inverse'][1])
        p.press('down')
        self.assertTrue(self.disp()['inverse'][2])                              # down = the option below
        p.press('back')                                                         # BACK accepts the selection
        self.assertEqual(h.state['mode'], 'm')
        h.state['unlocked'] = True
        p.press('up')
        self.assertEqual(p.frame()['ui']['menu']['item'], 'skip')

    def test_options_from_runtime_list_with_empty(self):
        p, h = self.p, self.h
        for _ in range(3):
            p.press('down')
        self.assertTrue(self.disp()['lines'][5].endswith('empty'))
        p.press('ok')
        self.assertEqual(self.disp()['lines'][1:4], ['empty', 'One', 'Two'])
        p.press('down'), p.press('down'), p.press('ok')
        self.assertEqual(h.state['pick'], 'm2')

    def test_function_bar_row_data_footer_and_window(self):
        p, h = self.p, self.h
        p._goto('tab')
        d = self.disp()
        self.assertEqual(d['lines'][-1], 'Pick Longlabel')                      # hidden function, selected one inverted
        self.assertEqual(d['inverse_spans'][-1], [[0, 4]])
        self.assertEqual(d['lines'][-2], 'line')                                # the footer above the bar
        self.assertEqual(d['function_bar'], {'labels': ['Pick', 'Longlabel'], 'selected': 0})
        p.press('down'), p.press('ok')
        self.assertEqual(h.state['picked'], 1)                                  # the data of the row under the cursor
        h.state['rows'] = []
        self.assertFalse(p.press('ok'))                                         # no row: refused
        p.press('right'), p.press('right')
        self.assertEqual(p.frame()['displays']['g']['function_bar']['selected'], 1)
        self.assertTrue(p.press('ok'))
        self.assertEqual(p.frame()['screen'], 'dlg')

    def test_static_function_bar_label_binding_and_plot(self):
        p, h = self.p, self.h
        p._goto('live')
        d = self.disp()
        self.assertEqual(d['lines'][-1], 'Alpha On')
        self.assertEqual(d['widgets'][0]['series'][0]['points'], [[0.0, 0.0], [5.0, 2.0], [10.0, 3.0]])
        p.press('right'), p.press('ok')
        self.assertTrue(h.state['on'])
        self.assertEqual(self.disp()['lines'][-1], 'Alpha Off')
        self.assertFalse(p.press('up'))                                         # no list on the screen
        a, b = p.render('g'), p.render('g')
        self.assertTrue((a == b).all())


class BuchiLyovaporKnobOnEditors(unittest.TestCase):
    """nav.knob_edits_overlay (BUCHI navigation control): the knob steps the value in an open number keyboard or moves the highlighted option
    of a choice list, pressing it confirms; widget knob_skip keeps the rotary focus on the parameter rows."""

    def setUp(self):
        self.h = Host(p=0.3, zone='reg')
        spec = {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {'knob': {'kind': 'encoder', 'push_input': 'knob_push'},
                                                                                   'knob_push': {'kind': 'key'}},
                'displays': {'tft': {'kind': 'touch_tft', 'size': [800, 480]}},
                'forms': {'f': {'fields': [
                    _f('p', 'number', 'Pressure', min=0.1, max=1.0, step=0.01, unit='mbar', read={'state': 'p'}, write={'capability': 'set', 'args': {'p': {'value': True}}}),
                    _f('zone', 'enum', 'Zone', options=[{'value': 'reg', 'text': 'Regulated'}, {'value': 'min', 'text': 'Minimum'}], read={'state': 'zone'},
                       write={'capability': 'set', 'args': {'zone': {'value': True}}})]}},
                'screens': {'gui': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'main', 'knob': 'knob', 'push': 'knob_push',
                                                                  'knob_edits_overlay': True, 'pages': {'main': {
                    'content': [{'id': 'row_p', 'type': 'button', 'label': 'Pressure', 'do': [{'do': 'form_edit', 'form': 'f', 'field': 'p'}]},
                                {'id': 'row_z', 'type': 'button', 'label': 'Zone', 'do': [{'do': 'form_edit', 'form': 'f', 'field': 'zone'}]}],
                    'actions': [{'id': 'fn_edit', 'type': 'button', 'label': 'EDIT', 'knob_skip': True, 'do': []},
                                {'id': 'fn_back', 'type': 'button', 'label': 'Back', 'knob_skip': {'const': True}, 'do': []}]}}}}},
                'start_screen': 'gui'}
        self.assertEqual(pu.check_spec(spec, {'set'}), [])
        self.p = pu.PanelUI(spec, self.h)

    def test_knob_focus_skips_the_function_bar_and_steps_values_in_editors(self):
        p, h = self.p, self.h
        for _ in range(4):
            p.turn('knob', 1)
        self.assertEqual(p.ui['focus'], 'row_z')                               # the function bar buttons are passed over
        p.turn('knob', -1)
        self.assertTrue(p.push('knob'))                                         # opens the pressure editor
        ov = p.frame()['displays']['tft']['overlay']
        self.assertEqual((ov['kind'], ov['field']), ('keyboard', '0.3'))
        self.assertTrue(p.turn('knob', 5))
        self.assertEqual(p.frame()['displays']['tft']['overlay']['field'], '0.35')
        p.turn('knob', 100)                                                     # clamped at the maximum
        self.assertEqual(p.frame()['displays']['tft']['overlay']['field'], '1')
        p.turn('knob', -2)
        self.assertTrue(p.push('knob'))                                         # pressing saves
        self.assertAlmostEqual(h.state['p'], 0.98)
        self.assertIsNone(p.ui['overlay'])
        p.turn('knob', 1)
        p.push('knob')                                                          # choice list: knob moves the highlight, press commits
        self.assertEqual(p.ui['overlay']['kind'], 'select')
        p.turn('knob', 1)
        self.assertEqual(p.ui['overlay']['value'], 'min')
        self.assertEqual(h.state['zone'], 'reg')
        p.push('knob')
        self.assertEqual(h.state['zone'], 'min')
        self.assertTrue(p._cond({'focus': 'row_z'}, {}))                      # the rotary focus as a condition (an EDIT key acting on it)
        self.assertTrue(p._cond({'focus': ['row_p', 'row_z']}, {}))
        self.assertFalse(p._cond({'focus': 'row_p'}, {}))

    def test_without_the_option_the_knob_ignores_open_editors(self):
        p = self.p
        p.spec['screens']['gui']['nav'].pop('knob_edits_overlay')
        p.turn('knob', 1)
        p.push('knob')
        self.assertFalse(p.turn('knob', 3))
        self.assertEqual(p.frame()['displays']['tft']['overlay']['field'], '0.3')

    def test_list_rows_take_the_widget_color(self):
        """A dynamic list on a dark area (the Lyovapor Start menu while running) draws its rows in the widget's 'color'."""
        h = Host(rows=[{'n': 'Step 1'}])
        spec = {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {}, 'displays': {'tft': {'kind': 'touch_tft', 'size': [200, 100]}},
                'screens': {'gui': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'p', 'pages': {'p': {'widgets': [
                    {'id': 'bg', 'type': 'label', 'text': '', 'rect': [0, 0, 200, 100], 'fill': 'black'},
                    {'id': 'l', 'type': 'list', 'rect': [0, 0, 200, 40], 'row_h': 40, 'items': {'state': 'rows'}, 'color': {'const': 'white'},
                     'row': {'cells': [{'src': {'row': 'n'}}]}}]}}}}},
                'start_screen': 'gui'}
        img = pu.PanelUI(spec, h).render('tft')
        self.assertTrue((img[:40].reshape(-1, 3) == 255).all(axis=1).any())   # white text pixels on the black area


# ---------------------------------------------------------------- Agilent 7890B: parameter-list display, per-line keys, help until the next key
GC_LIST = {
    'spec_version': '0.1',
    'inputs': {k: {'kind': 'arrow_key' if k in ('up', 'down') else 'key'} for k in ('up', 'down', 'enter', 'on', 'off', 'info', 'mode', 'clear', 'k1', 'k5', 'dot')},
    'displays': {'lcd': {'kind': 'char_lcd', 'rows': 4, 'cols': 32,
                         'param_list': {'actual_end': 21, 'value_end': 28, 'cursor': '<', 'edit_mark': '*', 'radix': {'state': 'radix'}}}},
    'menus': {'oven': {'label': 'OVEN', 'items': [
        {'id': 'temp', 'label': 'Temperature', 'type': 'number', 'min': 0, 'max': 450, 'step': 0.1, 'decimals': 1, 'read': {'state': 'temp'},
         'write': {'capability': 'count', 'args': {'temp': {'field': 'temp'}}}, 'actual': {'src': {'state': 'actual'}, 'fmt': {'decimals': 1}},
         'value_text': {'cases': [{'when': {'not': {'state': 'on'}}, 'text': 'Off', 'blink': {'state': 'fault'}}, {'src': {'field': 'temp'}, 'fmt': {'decimals': 1}}]},
         'keys': {'on': [{'do': 'invoke', 'capability': 'set', 'args': {'on': {'const': True}}}],
                  'off': [{'do': 'invoke', 'capability': 'set', 'args': {'on': {'const': False}}}],
                  'info': [{'do': 'message', 'text': 'Enter a value between 0 and 450', 'duration_s': 60, 'until_input': True}]}, 'evidence': 'p.11'},
        {'id': 'mode', 'label': 'Mode', 'type': 'enum', 'options': [{'value': 'a', 'text': 'Split'}, {'value': 'b', 'text': 'Splitless'}],
         'read': {'state': 'mode'}, 'write': {'capability': 'set', 'args': {'mode': {'field': 'mode'}}}, 'keys': {'mode': [{'do': 'nav', 'op': 'enter'}]},
         'evidence': 'p.28'},
        {'id': 'r1', 'label': 'Rate 1', 'type': 'number', 'min': 0, 'max': 120, 'step': 0.001, 'decimals': 3, 'read': {'state': 'rate'},
         'write': {'capability': 'set', 'args': {'rate': {'field': 'r1'}}}, 'evidence': 'p.11'},
        {'id': 'r2', 'label': 'Rate 2', 'type': 'number', 'min': 0, 'max': 120, 'step': 0.001, 'decimals': 3, 'read': {'state': 'rate'},
         'write': {'capability': 'set', 'args': {'rate': {'field': 'r2'}}}, 'evidence': 'p.11'}]}},
    'screens': {'oven': {'style': 'arrow_menu', 'nav': {'menu': 'oven', 'up': 'up', 'down': 'down', 'enter': 'enter', 'back': 'clear',
                                                        'keypad': {'k1': '1', 'k5': '5', 'dot': '.'}}}},
    'start_screen': 'oven',
}


class GcHost(Host):
    def panel_invoke(self, cap, args):
        if cap == 'count':
            self.calls.append((cap, dict(args)))
            self.state['writes'] = self.state.get('writes', 0) + 1
            self.state.update(args)
            return {}
        return super().panel_invoke(cap, args)


class Agilent7890bParamList(unittest.TestCase):
    def setUp(self):
        self.h = GcHost(temp=80.0, actual=79.5, on=True, fault=False, mode='a', rate=20.0, radix='.')
        self.p = pu.PanelUI(copy.deepcopy(GC_LIST), self.h)

    def lines(self):
        return [x.rstrip() for x in self.p.frame()['displays']['lcd']['shown']]

    def test_columns_cursor_typed_entry_and_single_write(self):
        self.assertEqual(pu.check_spec(GC_LIST, {'set', 'count'}), [])
        ls = self.lines()
        self.assertEqual(ls[0], '              OVEN')                             # centred title
        self.assertEqual(ls[1], 'Temperature       79.5   80.0<')                    # Actual ends at column 22, Setpoint at 29, cursor after it
        self.assertEqual(ls[3], 'Rate 1                 20.000')
        self.p.press('k1')
        self.p.press('k5')
        self.assertEqual(self.p.frame()['displays']['lcd']['lines'][1].rstrip(), 'Temperature       79.5     15*')   # typed in place, * flashes
        self.p.press('enter')
        self.assertEqual(self.h.state['temp'], 15.0)
        self.assertEqual(self.h.state['writes'], 1)                              # a one-value item is written once
        self.h.state['radix'] = ','
        self.assertIn('79,5', self.lines()[1])

    def test_line_keys_value_text_blink_and_option_list(self):
        p = self.p
        self.assertTrue(p.press('off'))                                          # On/Off keys of the active line
        self.assertFalse(self.h.state['on'])
        self.assertTrue(self.lines()[1].endswith('Off<'))
        self.h.state['fault'] = True
        self.assertTrue(p.frame()['displays']['lcd']['blink'][1])
        p.press('down')
        self.assertTrue(p.press('off') is False)                                 # no key binding on this line: not accepted
        p.press('mode')                                                          # Mode/Type: the options with the cursor after the chosen one
        self.assertEqual(self.lines()[:3], ['              Mode', 'Split <', 'Splitless'])
        p.press('up')                                                            # arrow_menu: up = next value (the GC spec rebinds the scroll keys)
        self.assertEqual(self.lines()[2], 'Splitless <')
        p.press('enter')
        self.assertEqual(self.h.state['mode'], 'b')

    def test_help_message_until_the_next_key(self):
        p = self.p
        p.press('info')
        self.assertEqual(self.lines()[3], 'Enter a value between 0 and 450')
        p.press('down')
        self.assertNotIn('Enter a value', ' '.join(self.lines()))
        self.assertEqual(pu.check_spec(dict(GC_LIST, menus={'oven': {'items': [dict(GC_LIST['menus']['oven']['items'][0], keys={'nokey': []})]}})),
                         ["menus.oven.items[0].keys input 'nokey'"])
        self.assertIn('count', pu.spec_capabilities(GC_LIST))


class JeolJem1400flashOledInverse(unittest.TestCase):
    """Inverse lines on a second display of a touch screen (an OLED key legend lit white while the touch monitor shows pages)."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'parameter_cycle',
            'inputs': {'key': {'kind': 'key'}},
            'displays': {'monitor': {'kind': 'touch_tft', 'size': [320, 240]}, 'oled': {'kind': 'graphic_lcd', 'size': [96, 24], 'text_scale': 1}},
            'layout_base': {'oled': {'lines': [{'text': 'Beam Blank', 'inverse': {'state': 'blank'}}]}},
            'screens': {'main': {'style': 'touch_gui', 'nav': {'display': 'monitor', 'start': 'p', 'pages': {'p': {'title': 'Page', 'widgets': [
                {'id': 'b', 'type': 'button', 'label': 'B', 'rect': [0, 0, 100, 40], 'do': []}]}}},
                'bindings': [{'on': {'event': 'press', 'input': 'key'}, 'do': [{'do': 'invoke', 'capability': 'toggle', 'args': {'key': {'const': 'blank'}}}]}]}},
            'start_screen': 'main'}

    def test_inverse_line_rendered(self):
        h = Host(blank=False)
        p = pu.PanelUI(copy.deepcopy(self.SPEC), h)
        d = p.frame()['displays']['oled']
        self.assertEqual(d['lines'][0], 'Beam Blank')
        self.assertNotIn('inverse', d)                                            # no inverse line: the frame is as before
        self.assertEqual((d['title'], d.get('header_h')), ('', None))             # the page title bar belongs to the touch display only
        dark = p.render('oled')
        self.assertTrue(p.press('key'))
        d = p.frame()['displays']['oled']
        self.assertEqual(d['inverse'][0], True)
        lit = p.render('oled')
        self.assertLess(int(lit.sum()), int(dark.sum()))                          # the legend's line is drawn as a filled bar (text colour)
        self.assertEqual(p.frame()['displays']['monitor']['widgets'][0]['id'], 'b')


class KnauerK7400sRhombusGlyph(unittest.TestCase):
    """The rhombus field of a character LCD menu (K-7400S) is drawn with its own 5x7 glyph, not as a blank cell."""

    def test_rhombus_is_drawn(self):
        spec = {'spec_version': '0.1', 'ui_class': 'text_menu', 'inputs': {'k': {'kind': 'key'}},
                'displays': {'lcd': {'kind': 'char_lcd', 'rows': 2, 'cols': 24}},
                'screens': {'s': {'style': 'static', 'layout': {'lcd': {'lines': ['', '◆']}}}, 'b': {'style': 'static', 'layout': {'lcd': {'lines': ['', ' ']}}}},
                'start_screen': 's'}
        self.assertEqual(pu.FONT5X7['◆'], bytes.fromhex('081c3e1c08'))
        a = pu.PanelUI(copy.deepcopy(spec), Host()).render('lcd')
        b = pu.PanelUI(dict(copy.deepcopy(spec), start_screen='b'), Host()).render('lcd')
        self.assertFalse((a == b).all())


class LakeshoreMeasurereadyM91UnitGlyphs(unittest.TestCase):
    """Units of the M91 FastHall result screens (Ω/□, cm²/(V·s), m⁻³) are drawn with their own glyphs, not as '?'."""

    def test_unit_glyphs_are_drawn(self):
        for ch in '□²³⁻·':
            self.assertIn(ch, pu.FONT5X7)
            self.assertNotEqual(pu.FONT5X7[ch], pu.FONT5X7['?'], ch)
            self.assertEqual(len(pu.FONT5X7[ch]), 5, ch)
        spec = {'spec_version': '0.1', 'ui_class': 'text_menu', 'inputs': {'k': {'kind': 'key'}},
                'displays': {'lcd': {'kind': 'char_lcd', 'rows': 1, 'cols': 16}},
                'screens': {'s': {'style': 'static', 'layout': {'lcd': {'lines': ['Ω/□ cm²/(V·s)']}}},
                            'b': {'style': 'static', 'layout': {'lcd': {'lines': ['Ω/? cm?/(V?s)']}}}},
                'start_screen': 's'}
        a = pu.PanelUI(copy.deepcopy(spec), Host()).render('lcd')
        b = pu.PanelUI(dict(copy.deepcopy(spec), start_screen='b'), Host()).render('lcd')
        self.assertFalse((a == b).all())


class Aqualab4teLabelOnlySoftkey(unittest.TestCase):
    """A softkey that only names its key (label_only): OK of a character grid on a three-key graphic LCD is taken by the grid (nav.enter)."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'text_menu',
            'inputs': {k: {'kind': 'arrow_key'} for k in ('up', 'down', 'left', 'right')} | {k: {'kind': 'softkey'} for k in ('b1', 'b2', 'b3')},
            'displays': {'g': {'kind': 'graphic_lcd', 'size': [128, 64], 'text_scale': 1, 'rows': 6, 'cols': 21,
                               'softkey_rows': {'bottom': ['b1', 'b2', 'b3']}}},
            'char_grids': {'kb': {'rows': [{'tokens': list('ABC'), 'sep': ' '}]}},
            'menus': {'m': {'label': 'Name', 'items': [{'id': 'name', 'label': 'Name:', 'type': 'text', 'entry': 'grid', 'grid': 'kb', 'max_len': 8,
                                                        'read': {'state': 'name'}, 'write': W('name', 'value'), 'evidence': 'x'}]}},
            'screens': {'s': {'style': 'softkey_menu', 'on_enter': [{'do': 'nav', 'op': 'enter'}],
                              'nav': {'menu': 'm', 'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right', 'enter': 'b2', 'softkeys': [
                                  {'input': 'b1', 'label': 'SAVE', 'op': 'enter'}, {'input': 'b2', 'label': 'OK', 'label_only': True},
                                  {'input': 'b3', 'label': 'CANCEL', 'op': 'cancel'}]}}},
            'start_screen': 's'}

    def test_label_shown_key_left_to_the_grid(self):
        h = Host(name='')
        p = pu.PanelUI(copy.deepcopy(self.SPEC), h)
        self.assertEqual(pu.check_spec(self.SPEC, {'set'}), [])
        self.assertEqual(p.frame()['displays']['g']['lines'][-1].split(), ['SAVE', 'OK', 'CANCEL'])
        self.assertTrue(p.press('right'))
        self.assertTrue(p.press('b2'))                                             # OK enters the selected character
        self.assertEqual(p.frame()['ui']['editor']['typed'], 'B')
        self.assertTrue(p.press('b1'))                                             # SAVE confirms the typed text
        self.assertEqual(h.state['name'], 'B')


class OssilaDipcoaterTimeDigitsAndLeftRight(unittest.TestCase):
    """Digit-wise time entry ('places' [place, radix] per digit + 'mask': HH:MM:SS.s held in seconds, minutes and seconds tens 0-5) and an enum
    changed with left / right while editing ('lr_change'), on an arrow menu."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'text_menu',
            'inputs': {k: {'kind': 'arrow_key'} for k in ('up', 'down', 'left', 'right')} | {'ok': {'kind': 'key'}},
            'displays': {'lcd': {'kind': 'char_lcd', 'rows': 3, 'cols': 20}},
            'menus': {'m': {'label': 'Program', 'items': [
                {'id': 'dwell', 'label': 'Dwell', 'type': 'number', 'entry': 'digits', 'digits': 7, 'min': 0, 'max': 359999.9, 'step': 0.1, 'mask': 'DD:DD:DD.D',
                 'places': [[36000, 10], [3600, 10], [600, 6], [60, 10], [10, 6], [1, 10], [0.1, 10]], 'read': {'state': 'dwell'},
                 'write': W('dwell', 'value'), 'evidence': 'x'},
                {'id': 'unit', 'label': 'Unit', 'type': 'enum', 'lr_change': True, 'options': [{'value': 's', 'text': 'mm/s'}, {'value': 'm', 'text': 'mm/min'}],
                 'read': {'state': 'unit'}, 'write': W('unit', 'value'), 'evidence': 'x'}]}},
            'screens': {'s': {'style': 'arrow_menu', 'nav': {'menu': 'm', 'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right', 'enter': 'ok'}}},
            'start_screen': 's'}

    def test_time_digits_and_left_right_enum(self):
        h = Host(dwell=3725.5, unit='s')
        p = pu.PanelUI(copy.deepcopy(self.SPEC), h)
        self.assertEqual(pu.check_spec(self.SPEC, {'set'}), [])
        self.assertIn('01:02:05.5', p.frame()['displays']['lcd']['lines'][1])
        self.assertTrue(p.press('ok'))
        for _ in range(2):
            p.press('right')
        for _ in range(5):
            p.press('up')                                                          # tens of minutes: 0 -> 5
        self.assertEqual(p.frame()['ui']['editor']['buffer']['dwell'], 6725.5)   # 01:52:05.5
        p.press('up')
        self.assertEqual(p.frame()['ui']['editor']['buffer']['dwell'], 3725.5)   # 5 -> 0 (radix 6), no carry
        line = p.frame()['displays']['lcd']
        self.assertEqual(line['lines'][1].strip(), '01:02:05.5')
        self.assertEqual((line['blink'][1], line['shown'][1][3] in '0 '), (True, True))   # the tens of minutes flash (mask position 3)
        for _ in range(9):
            p.press('right')                                                       # the cursor stops at the tenths
        p.press('up')
        self.assertTrue(p.press('ok'))
        self.assertAlmostEqual(h.state['dwell'], 3725.6)
        p.press('down')
        self.assertTrue(p.press('ok'))
        self.assertTrue(p.press('right'))                                          # left / right change the unit
        self.assertEqual(p.frame()['ui']['editor']['buffer']['unit'], 'm')
        self.assertTrue(p.press('ok'))
        self.assertEqual(h.state['unit'], 'm')


class OssilaDoctorbladeEditInline(unittest.TestCase):
    """Display option edit_inline: a single value is edited on its own line of the page (the other lines stay; only the digit under the cursor
    flashes, a whole enum flashes); without the option the editor view replaces the page as before."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'text_menu',
            'inputs': {k: {'kind': 'arrow_key'} for k in ('up', 'down', 'left', 'right')} | {'ok': {'kind': 'key'}},
            'displays': {'lcd': {'kind': 'char_lcd', 'rows': 4, 'cols': 26, 'menu_values': 'inline', 'edit_inline': True}},
            'menus': {'m': {'label': 'Program Mode', 'items': [
                {'id': 'len', 'label': 'Coating length:', 'type': 'number', 'entry': 'digits', 'digits': 5, 'decimals': 2, 'min': 0, 'max': 290, 'step': 0.01,
                 'unit': 'mm', 'read': {'state': 'len'}, 'write': W('len', 'value'), 'evidence': 'x'},
                {'id': 'ref', 'label': 'Home ref. :', 'type': 'enum', 'options': [{'value': 'back', 'text': 'BACK'}, {'value': 'front', 'text': 'FRONT'}],
                 'read': {'state': 'ref'}, 'write': W('ref', 'value'), 'evidence': 'x'}]}},
            'screens': {'s': {'style': 'arrow_menu', 'nav': {'menu': 'm', 'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right', 'enter': 'ok',
                                                             'left_right': 'none'}}},
            'start_screen': 's'}

    def test_value_edited_in_place(self):
        h = Host(len=290.0, ref='back')
        p = pu.PanelUI(copy.deepcopy(self.SPEC), h)
        self.assertEqual(pu.check_spec(self.SPEC, {'set'}), [])
        before = p.frame()['displays']['lcd']['lines']
        self.assertEqual(before[:3], ['Program Mode', '>Coating length: 290.00 mm', ' Home ref. : BACK'])
        p.press('ok'), p.press('right'), p.press('down')                          # tens digit 9 -> 8
        lcd = p.frame()['displays']['lcd']
        self.assertEqual(lcd['lines'][:3], ['Program Mode', '>Coating length: 280.00 mm', ' Home ref. : BACK'])
        self.assertTrue(lcd['blink'][1])
        line = lcd['lines'][1]
        self.assertIn(lcd['shown'][1], (line, line[:18] + ' ' + line[19:]))  # only that digit flashes
        self.assertTrue(p.press('ok'))
        self.assertEqual(h.state['len'], 280.0)
        p.press('down'), p.press('ok'), p.press('up')
        lcd = p.frame()['displays']['lcd']
        self.assertEqual(lcd['lines'][2], '>Home ref. : FRONT')
        self.assertTrue(lcd['blink'][2] and not lcd['blink'][1])
        spec = copy.deepcopy(self.SPEC)
        del spec['displays']['lcd']['edit_inline']
        p2 = pu.PanelUI(spec, Host(len=290.0, ref='back'))
        p2.press('ok')
        self.assertEqual(p2.frame()['displays']['lcd']['lines'][0], 'Coating length:')       # the editor view as before


class OssilaSlotdieSignedDigits(unittest.TestCase):
    """Digit-wise entry with a sign ('signed': '+05', the sign is the first cursor position and up / down flip it) and the flashing position of
    a digit after the decimal point (the cursor skips '.')."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'text_menu',
            'inputs': {k: {'kind': 'arrow_key'} for k in ('up', 'down', 'left', 'right')} | {'ok': {'kind': 'key'}},
            'displays': {'lcd': {'kind': 'char_lcd', 'rows': 3, 'cols': 24}},
            'menus': {'m': {'label': 'Settings Mode', 'items': [
                {'id': 'off', 'label': 'Temp offset', 'type': 'number', 'entry': 'digits', 'signed': True, 'digits': 2, 'min': -10, 'max': 10, 'step': 1,
                 'read': {'state': 'off'}, 'write': W('off', 'value'), 'evidence': 'x'},
                {'id': 'len', 'label': 'Length', 'type': 'number', 'entry': 'digits', 'digits': 6, 'decimals': 3, 'min': 0, 'max': 150, 'step': 0.001,
                 'read': {'state': 'len'}, 'write': W('len', 'value'), 'evidence': 'x'}]}},
            'screens': {'s': {'style': 'arrow_menu', 'nav': {'menu': 'm', 'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right', 'enter': 'ok'}}},
            'start_screen': 's'}

    def test_sign_and_decimal_digits(self):
        h = Host(off=5.0, len=50.0)
        p = pu.PanelUI(copy.deepcopy(self.SPEC), h)
        self.assertEqual(pu.check_spec(self.SPEC, {'set'}), [])
        self.assertTrue(p.frame()['displays']['lcd']['lines'][1].endswith('+05'))
        p.press('ok')
        p.press('down')                                                            # on the sign: +05 -> -05
        self.assertEqual(p.frame()['ui']['editor']['buffer']['off'], -5.0)
        h.t = 0.6                                                                  # the dark blink phase: only the sign is blanked
        self.assertEqual(p.frame()['displays']['lcd']['shown'][1].strip(), '05')
        p.press('right'), p.press('right'), p.press('up')                          # ones digit: -05 -> -06
        self.assertEqual(p.frame()['ui']['editor']['buffer']['off'], -6.0)
        p.press('right')                                                           # the cursor stops at the last digit
        p.press('left'), p.press('up'), p.press('up')                              # tens: -06 -> -16 is out of range and does not move
        self.assertEqual(p.frame()['ui']['editor']['buffer']['off'], -6.0)
        self.assertTrue(p.press('ok'))
        self.assertEqual(h.state['off'], -6.0)
        p.press('down')
        p.press('ok')
        for _ in range(4):
            p.press('right')                                                       # 050.000: digit 4 is the hundredths
        line = p.frame()['displays']['lcd']
        self.assertEqual(line['lines'][1].strip(), '050.000')
        self.assertEqual(line['shown'][1].strip(), '050.0 0')                        # the flashing digit is the hundredths, not the '.'

    def test_footer_stays_while_editing_inline(self):
        spec = copy.deepcopy(self.SPEC)
        spec['displays']['lcd'].update(rows=4, edit_inline=True)
        spec['screens']['s']['nav']['footer'] = {'parts': [{'src': {'state': 'len'}, 'fmt': {'pattern': '{:.0f}'}}, {'text': ' mm'}]}
        spec['screens']['s']['nav']['title'] = {'parts': [{'text': 'Settings'}, {'src': {'state': 'size'}, 'pad': 8}]}
        p = pu.PanelUI(spec, Host(off=5.0, len=50.0, size='5 ml'))
        self.assertEqual(p.frame()['displays']['lcd']['lines'][0], 'Settings    5 ml')
        self.assertEqual(p.frame()['displays']['lcd']['lines'][3], '50 mm')
        p.press('ok')
        self.assertEqual(p.frame()['displays']['lcd']['lines'][3], '50 mm')         # the footer stays while a value is edited on its line
        self.assertEqual(p.frame()['displays']['lcd']['lines'][0], 'Settings    5 ml')  # and the title binding too
        spec['displays']['gauge'] = {'kind': 'char_lcd', 'rows': 1, 'cols': 8}
        spec['layout_base'] = {'gauge': {'lines': ['0.080']}}
        self.assertEqual(pu.PanelUI(spec, Host(off=5.0, len=50.0, size='5 ml')).frame()['displays']['gauge']['lines'], ['0.080'])   # not on other displays
        del spec['displays']['lcd']['edit_inline']
        p2 = pu.PanelUI(spec, Host(off=5.0, len=50.0))
        p2.press('ok')
        self.assertNotIn('50 mm', p2.frame()['displays']['lcd']['lines'])            # the editor view as before


class TextDialogsMetrohm914(unittest.TestCase):
    """Holding OK on a grid character enters its variant (capitals); forced exits chain within one update (start image -> main -> message)."""

    def test_long_ok_enters_the_capital(self):
        spec = copy.deepcopy(DLG)
        spec['inputs']['ok']['long_press_s'] = 1.0
        spec['char_grids']['txt']['long'] = {'a': 'A'}
        spec['char_grids']['txt']['rows'][0] = ['a', 'b', {'label': 'c', 'insert': 'c', 'long': 'Ç'}]
        h = Host(name='', lim=None, mode='a', status='ready', unlocked=False, pick=None, rows=[])
        p = pu.PanelUI(spec, h)
        p.press('ok')
        self.assertTrue(p.press('ok', duration_s=1.0))                          # held: 'A'
        p.press('ok')                                                           # briefly: 'a'
        p.press('right'), p.press('right'), p.press('ok', duration_s=1.0)        # a token's own variant
        self.assertEqual(p.frame()['ui']['editor']['typed'], 'AaÇ')

    def test_forced_exits_chain(self):
        spec = {'spec_version': '0.1', 'inputs': {'k': {'kind': 'key'}}, 'displays': {'d': {'kind': 'char_lcd', 'rows': 1, 'cols': 8}},
                'screens': {'boot': {'leave': [{'when': {'state': 'ready'}, 'to': 'main'}], 'layout': {'d': {'lines': [{'text': 'BOOT'}]}}},
                            'main': {'leave': [{'when': {'state': 'msg'}, 'to': 'msg'}], 'layout': {'d': {'lines': [{'text': 'MAIN'}]}}},
                            'msg': {'layout': {'d': {'lines': [{'text': 'MSG'}]}}}},
                'start_screen': 'boot', 'leave_chain': True}
        h = Host(ready=False, msg=False)
        p = pu.PanelUI(spec, h)
        self.assertEqual(p.frame()['screen'], 'boot')
        h.state.update(ready=True, msg=True)
        self.assertEqual(p.frame()['screen'], 'msg')                            # both exits in one update


class RetschPm100MenuLanguage(unittest.TestCase):
    def test_menu_title_and_item_labels_follow_the_language(self):
        spec = {'spec_version': '0.1', 'inputs': {'k': {'kind': 'encoder', 'push_input': 'p'}, 'p': {'kind': 'key'}},
                'displays': {'d': {'kind': 'tft', 'size': [240, 80], 'text_scale': 1}},
                'i18n': {'language': {'state': 'lang'}, 'tables': {'de': {'Speed': 'Drehzahl', 'Settings': 'Einstellungen'}}},
                'menus': {'m': {'label': 'Settings', 'items': [{'id': 'speed', 'label': 'Speed', 'type': 'number', 'min': 0, 'max': 9, 'read': {'state': 'n'},
                                                                'evidence': 'x'}]}},
                'screens': {'s': {'style': 'encoder_menu', 'nav': {'menu': 'm', 'turn': 'k', 'push': 'p'}}}, 'start_screen': 's'}
        self.assertEqual(pu.check_spec(spec), [])
        h = Host(lang='en', n=3)
        p = pu.PanelUI(spec, h)
        lines = p.frame()['displays']['d']['lines']
        self.assertEqual((lines[0], lines[1].split()[:2]), ('Settings', ['>Speed', '3']))
        h.state['lang'] = 'de'
        lines = p.frame()['displays']['d']['lines']
        self.assertEqual((lines[0], lines[1].split()[:2]), ('Einstellungen', ['>Drehzahl', '3']))


class SyrrisAsiadiscoveryMenuMarker(unittest.TestCase):
    def test_arrow_cursor_and_character_display_glyphs(self):
        spec = {'spec_version': '0.1', 'inputs': {'k': {'kind': 'encoder', 'push_input': 'p'}, 'p': {'kind': 'key'}},
                'displays': {'d': {'kind': 'char_lcd', 'rows': 2, 'cols': 20, 'menu_title': False, 'menu_marker': '→'}},
                'menus': {'m': {'label': 'M', 'items': [{'id': 'a', 'label': '1) INITIALISE 1', 'type': 'action', 'evidence': 'x'},
                                                        {'id': 'b', 'label': '2) STOP 1', 'type': 'action', 'evidence': 'x'}]}},
                'screens': {'s': {'style': 'encoder_menu', 'nav': {'menu': 'm', 'turn': 'k', 'push': 'p'}}}, 'start_screen': 's'}
        self.assertEqual(pu.check_spec(spec), [])
        p = pu.PanelUI(spec, Host())
        self.assertEqual(p.frame()['displays']['d']['lines'], ['→1) INITIALISE 1', ' 2) STOP 1'])
        p.turn('k', 1)
        self.assertEqual(p.frame()['displays']['d']['lines'], [' 1) INITIALISE 1', '→2) STOP 1'])
        del spec['displays']['d']['menu_marker']
        self.assertEqual(pu.PanelUI(spec, Host()).frame()['displays']['d']['lines'][0], '>1) INITIALISE 1')     # the default cursor
        for ch in '→←ψ🔒':
            self.assertNotEqual(pu._glyph(ch), pu._glyph('?'))
        self.assertNotEqual(pu._glyph('→'), pu._glyph('←'))


class InficonUl1000fabStaticGaugeAndInvert(unittest.TestCase):
    def test_bar_graph_on_a_graphic_lcd_and_inverted_display(self):
        spec = {'spec_version': '0.1', 'inputs': {'k': {'kind': 'key'}},
                'displays': {'d': {'kind': 'graphic_lcd', 'size': [120, 40], 'text_scale': 1, 'invert_if': {'state': 'inv'},
                                   'colors': {'background': [200, 210, 190], 'text': [20, 30, 20], 'accent': [20, 30, 20], 'muted': [20, 30, 20]}}},
                'screens': {'s': {'style': 'static', 'layout': {'d': {'lines': ['LR'], 'widgets': [
                    {'id': 'bar', 'type': 'gauge', 'rect': [4, 20, 100, 10], 'value': {'state': 'f'}, 'marks': [{'state': 't1'}, {'state': 'none'}]}]}}}},
                'start_screen': 's'}
        self.assertEqual(pu.check_spec(spec), [])
        h = Host(f=0.5, t1=0.25, inv=False)
        p = pu.PanelUI(spec, h)
        bar = p.frame()['displays']['d']['widgets'][0]
        self.assertEqual((bar['fraction'], bar['marks']), (0.5, [0.25]))
        img = p.render('d')
        self.assertEqual(tuple(img[25, 30]), (20, 30, 20))                    # the filled part of the bar
        self.assertEqual(tuple(img[25, 90]), (200, 210, 190))                 # the empty part
        h.state['inv'] = True
        self.assertTrue(p.frame()['displays']['d']['inverted'])
        inv = p.render('d')
        self.assertEqual((tuple(inv[25, 30]), tuple(inv[25, 90]), tuple(inv[2, 118])), ((200, 210, 190), (20, 30, 20), (20, 30, 20)))
        h.state['f'] = None
        self.assertIsNone(p.frame()['displays']['d']['widgets'][0]['fraction'])


class BuchiSpeedextractoreStrike(unittest.TestCase):
    def test_crossed_out_text_part_on_a_graphic_lcd(self):
        spec = {'spec_version': '0.1', 'inputs': {'k': {'kind': 'key'}},
                'displays': {'d': {'kind': 'graphic_lcd', 'size': [120, 30], 'text_scale': 1,
                                   'colors': {'background': [200, 210, 190], 'text': [20, 30, 20]}}},
                'screens': {'s': {'style': 'static', 'layout': {'d': {'lines': [{'parts': [{'text': 'AB '}, {'text': 'XYZ', 'strike': {'state': 'mod'}}]},
                                                                                {'text': 'plain'}]}}}},
                'start_screen': 's'}
        self.assertEqual(pu.check_spec(spec), [])
        h = Host(mod=False)
        p = pu.PanelUI(spec, h)
        self.assertNotIn('strike_spans', p.frame()['displays']['d'])
        x, y = 2 + 3 * 6 + 5, 2 + 3                                             # the gap after X, at the height of the stroke
        self.assertEqual(tuple(p.render('d')[y, x]), (200, 210, 190))
        h.state['mod'] = True
        d = p.frame()['displays']['d']
        self.assertEqual((d['lines'][0], d['strike_spans']), ('AB XYZ', [[[3, 6]], []]))
        img = p.render('d')
        self.assertEqual(tuple(img[y, x]), (20, 30, 20))
        self.assertEqual(tuple(img[y, 2 + 2 * 6 + 3]), (200, 210, 190))      # the space before the struck part stays clear


class Biotek405tsScrollArrows(unittest.TestCase):
    """Down / up arrow buttons scroll a list by whole rows ({'do': 'scroll'}), clamped to its rows (a touch screen without swipe gestures)."""

    def test_arrows_scroll_a_dynamic_list(self):
        spec = {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {},
                'displays': {'tft': {'kind': 'touch_tft', 'size': [200, 140]}},
                'screens': {'s': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'p', 'pages': {'p': {'title': 'List', 'content': [
                    {'id': 'l', 'type': 'list', 'h': 60, 'row_h': 20, 'items': {'state': 'rows'}, 'row': {'cells': [{'src': {'row': 'name'}}]}},
                    {'row': [{'id': 'dn', 'type': 'button', 'label': 'Down', 'do': [{'do': 'scroll', 'widget': 'l', 'rows': 2}]},
                             {'id': 'up', 'type': 'button', 'label': 'Up', 'do': [{'do': 'scroll', 'widget': 'l', 'rows': -2}]}], 'h': 30}]}}}}},
                'start_screen': 's'}
        self.assertEqual(pu.check_spec(spec), [])
        h = Host(rows=[{'name': f'P{i}'} for i in range(7)])
        p = pu.PanelUI(spec, h)
        wid = lambda i: next(w for w in p.frame()['displays']['tft']['widgets'] if w['id'] == i)   # noqa: E731
        tap = lambda i: p.touch('tft', wid(i)['rect'][0] + 5, wid(i)['rect'][1] + 5)   # noqa: E731
        self.assertEqual([c[0] for c in wid('l')['cells']], ['P0', 'P1', 'P2'])
        self.assertTrue(tap('dn'))
        self.assertEqual([c[0] for c in wid('l')['cells']], ['P2', 'P3', 'P4'])
        tap('dn')
        tap('dn')
        self.assertEqual(wid('l')['scroll'], 4)                                  # clamped: the last rows stay in view
        tap('up')
        tap('up')
        tap('up')
        self.assertEqual(wid('l')['scroll'], 0)


class MtQuantosQ2OrderedChecklist(unittest.TestCase):
    """Check lists whose order matters (function keys numbered in the order they are touched), [STD] for the factory selection and a maximum
    number of entries (max. 4 information fields)."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {},
            'displays': {'tft': {'kind': 'touch_tft', 'size': [640, 480], 'text_scale': 2}},
            'forms': {'keys': {'fields': [
                _f('fk', 'multi', 'Function Keys', ordered=True, std=['Start', 'Info'], options=[{'value': v} for v in ('Start', 'Quantity', 'Info', 'Tapper')],
                   read={'state': 'fk'}, write={'capability': 'set', 'args': {'fk': {'value': True}}}),
                _f('info', 'multi', 'Info Field', ordered=True, max_items=2, options=[{'value': v} for v in ('Target', 'Tolerance', 'User ID')],
                   read={'state': 'info'}, write={'capability': 'set', 'args': {'info': {'value': True}}})]}},
            'screens': {'s': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'p', 'pages': {
                'p': {'title': 'Setup', 'content': [{'id': 'rows', 'type': 'form', 'form': 'keys', 'h': 'flex'}]}}}}},
            'start_screen': 's'}

    def setUp(self):
        self.h = Host(fk=['Quantity', 'Start'], info=['Target'])
        self.p = pu.PanelUI(copy.deepcopy(self.SPEC), self.h)

    def disp(self):
        return self.p.frame()['displays']['tft']

    def tap_b(self, label):
        for b in self.disp()['overlay']['buttons']:
            if b['label'].strip().split(' ', 1)[-1] == label or b['label'] == label:
                x, y, w, h = b['rect']
                return self.p.touch('tft', x + w // 2, y + h // 2)
        raise AssertionError(label)

    def open(self, row):
        w = next(x for x in self.disp()['widgets'] if x['id'] == 'rows')
        i = [c[0] for c in w['cells']].index(row)
        x, y, ww, hh = w['rect']
        rh = hh // max(1, len(w['cells'])) if not w.get('row_h') else w['row_h']
        return self.p.touch('tft', x + 10, y + i * rh + rh // 2)

    def test_order_numbers_std_and_maximum(self):
        self.assertEqual(pu.check_spec(self.SPEC, {'set'}), [])
        w = next(x for x in self.disp()['widgets'] if x['id'] == 'rows')
        self.assertEqual(w['cells'][0][1], 'Quantity, Start')                           # shown in the stored order
        self.open('Function Keys')
        labels = [b['label'] for b in self.disp()['overlay']['buttons']]
        self.assertEqual(labels[:4], [' 2 Start', ' 1 Quantity', ' - Info', ' - Tapper'])
        self.assertIn('STD', labels)
        self.assertNotIn('Select all', labels)
        self.tap_b('Quantity'), self.tap_b('Tapper'), self.tap_b('Quantity')           # off, then appended at the end
        self.tap_b('OK')
        self.assertEqual(self.h.state['fk'], ['Start', 'Tapper', 'Quantity'])
        self.open('Function Keys')
        self.tap_b('STD'), self.tap_b('OK')
        self.assertEqual(self.h.state['fk'], ['Start', 'Info'])
        self.open('Info Field')
        self.assertTrue(self.tap_b('Tolerance'))
        self.assertFalse(self.tap_b('User ID'))                                          # full: refused
        self.tap_b('OK')
        self.assertEqual(self.h.state['info'], ['Target', 'Tolerance'])

    def test_labels_and_gauges_with_do_are_touch_zones(self):
        spec = {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {}, 'displays': {'tft': {'kind': 'touch_tft', 'size': [200, 120]}},
                'screens': {'s': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'p', 'pages': {'p': {'widgets': [
                    {'id': 'w', 'type': 'label', 'text': '1.000 mg', 'rect': [0, 0, 200, 40], 'do': [{'do': 'invoke', 'capability': 'toggle', 'args': {'key': {'const': 'big'}}}]},
                    {'id': 'g', 'type': 'gauge', 'rect': [0, 50, 200, 20], 'value': {'const': 0.5}, 'do': [{'do': 'invoke', 'capability': 'toggle', 'args': {'key': {'const': 'trac'}}}]},
                    {'id': 'plain', 'type': 'label', 'text': 'x', 'rect': [0, 80, 200, 30]}]}}}}}, 'start_screen': 's'}
        self.assertEqual(pu.check_spec(spec, {'toggle'}), [])
        h = Host()
        p = pu.PanelUI(spec, h)
        self.assertTrue(p.touch('tft', 100, 20))
        self.assertTrue(p.touch('tft', 100, 60))
        self.assertFalse(p.touch('tft', 100, 90))                                      # a label without 'do' stays passive
        self.assertEqual((h.state.get('big'), h.state.get('trac')), (True, True))


class VacuubrandPc3001DragTarget(unittest.TestCase):
    """Widget drag_target (VACUU·SELECT set-point marker): a drag that starts on it moves the widget instead of swiping the page."""

    def spec(self, drag):
        slider = {'id': 'marker', 'type': 'slider', 'rect': [0, 100, 400, 40], 'min': 0, 'max': 1.0, 'step': 0.01, 'read': {'state': 'p'},
                  'write': {'capability': 'set', 'args': {'p': {'value': True}}}}
        if drag:
            slider['drag_target'] = True
        return {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {}, 'displays': {'tft': {'kind': 'touch_tft', 'size': [400, 400]}},
                'screens': {'gui': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'main', 'pages': {
                    'main': {'widgets': [slider], 'swipes': {'right': [{'do': 'page', 'page': 'menu'}]}}, 'menu': {'widgets': []}}}}},
                'start_screen': 'gui'}

    def test_drag_on_the_marker_moves_it_and_elsewhere_swipes(self):
        h = Host(p=0.3, zone='reg')
        self.assertEqual(pu.check_spec(self.spec(True), {'set'}), [])
        p = pu.PanelUI(self.spec(True), h)
        self.assertTrue(p.touch('tft', 20, 120, to=(300, 120)))
        self.assertEqual(p.frame()['ui']['page'], 'main')
        self.assertAlmostEqual(h.state['p'], 0.75)
        self.assertTrue(p.touch('tft', 20, 300, to=(300, 300)))
        self.assertEqual(p.frame()['ui']['page'], 'menu')

    def test_without_the_option_a_drag_on_the_widget_swipes(self):
        h = Host(p=0.3, zone='reg')
        p = pu.PanelUI(self.spec(False), h)
        p.touch('tft', 20, 120, to=(300, 120))
        self.assertEqual(p.frame()['ui']['page'], 'menu')
        self.assertEqual(h.state['p'], 0.3)


class VacuubrandPc3001Rotation(unittest.TestCase):
    """Display 'rotation' (VACUU·SELECT screen orientation 0 / 90 / 180 / 270 deg): the frame is drawn turned clockwise and touches on the turned
    screen are mapped back to the layout."""

    SPEC = {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {},
            'displays': {'tft': {'kind': 'touch_tft', 'size': [100, 200], 'rotation': {'state': 'zone'}}},
            'screens': {'gui': {'style': 'touch_gui', 'nav': {'display': 'tft', 'start': 'main', 'pages': {'main': {'widgets': [
                {'id': 'corner', 'type': 'button', 'label': 'C', 'rect': [0, 0, 20, 20], 'do': [{'do': 'set_var', 'var': 'hit', 'value': {'const': 'corner'}}]},
                {'id': 'bottom', 'type': 'button', 'label': 'B', 'rect': [0, 180, 100, 20], 'do': [{'do': 'set_var', 'var': 'hit', 'value': {'const': 'bottom'}}]}]}}}}},
            'vars': {'hit': {'initial': None}}, 'start_screen': 'gui'}

    def run_at(self, rotation, x, y):
        h = Host(p=0.3, zone=rotation)
        p = pu.PanelUI(self.SPEC, h)
        ok = p.touch('tft', x, y)
        return p, ok, p.frame()['ui']['vars']['hit']

    def test_touches_follow_the_turned_screen(self):
        self.assertEqual(self.run_at(0, 5, 5)[2], 'corner')
        self.assertEqual(self.run_at(90, 194, 5)[2], 'corner')                 # layout top-left is at the screen's top-right
        self.assertEqual(self.run_at(90, 5, 50)[2], 'bottom')
        self.assertEqual(self.run_at(180, 94, 194)[2], 'corner')
        self.assertEqual(self.run_at(270, 5, 94)[2], 'corner')
        self.assertEqual(self.run_at(270, 194, 50)[2], 'bottom')
        with self.assertRaises(pu.PanelError):
            self.run_at(0, 150, 50)                                            # outside the portrait screen
        self.assertEqual(self.run_at(90, 150, 50)[2], None)                   # inside the landscape screen, no widget there

    def test_frame_render_and_touch_schema(self):
        p = self.run_at(90, 1, 1)[0]
        self.assertEqual(p.frame()['displays']['tft']['rotation'], 90)
        self.assertEqual(p.render_image('tft').size, (200, 100))
        self.assertEqual(self.run_at(180, 1, 1)[0].render_image('tft').size, (100, 200))
        self.assertEqual(self.run_at('bad', 1, 1)[0].frame()['displays']['tft']['rotation'], 0)
        schema = pu.capability_definitions(self.SPEC)['panel.touch']['input_schema']['properties']
        self.assertEqual((schema['x']['maximum'], schema['y']['maximum']), (199, 199))


class ViciEuta2sd6mweOwnTitles(unittest.TestCase):
    """Display option menu_titles 'own': each list shows its submenu's own title (the manual remote's 'Valve Menu' under 'Valve Setup'), and a
    value editor the item's title ('Set positions:') instead of the breadcrumb / label."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'text_menu', 'inputs': {k: {'kind': 'arrow_key' if k != 'home' else 'key'} for k in ('up', 'down', 'home')},
            'displays': {'lcd': {'kind': 'char_lcd', 'rows': 4, 'cols': 20, 'menu_cursor': 'inverse', 'menu_titles': 'own'}},
            'menus': {'main': {'label': 'Main Menu', 'items': [
                {'id': 'valve', 'label': '1. Valve Setup', 'title': 'Valve Menu', 'type': 'submenu', 'evidence': 'x', 'items': [
                    {'id': 'np', 'label': '1. Set Positions', 'title': 'Set positions:', 'type': 'number', 'min': 2, 'max': 40, 'step': 2,
                     'read': {'state': 'np'}, 'write': W('np', 'value'), 'evidence': 'x'}]},
                {'id': 'info', 'label': '2. Information', 'type': 'submenu', 'evidence': 'x', 'items': [{'id': 'fw', 'label': '1. Firmware', 'type': 'info', 'evidence': 'x'}]}]}},
            'screens': {'m': {'style': 'arrow_menu', 'nav': {'menu': 'main', 'up': 'up', 'down': 'down', 'enter': 'home'}}}, 'start_screen': 'm'}

    def test_own_titles(self):
        self.assertEqual(pu.check_spec(self.SPEC, {'set'}), [])
        h = Host(np=6)
        p = pu.PanelUI(copy.deepcopy(self.SPEC), h)
        lines = lambda: p.frame()['displays']['lcd']['lines']               # noqa: E731
        self.assertEqual(lines()[0], 'Main Menu')
        p.press('home')
        self.assertEqual(lines()[0], 'Valve Menu')                                       # not 'Main Menu > 1. Valve Setup'
        p.press('home')
        self.assertEqual(lines()[:2], ['Set positions:', '6'])
        p.press('up'), p.press('home')
        self.assertEqual(h.state['np'], 8)
        p2 = pu.PanelUI(dict(copy.deepcopy(self.SPEC), menus={'main': dict(self.SPEC['menus']['main'])}), Host(np=6))
        p2.press('down'), p2.press('home')
        self.assertEqual(p2.frame()['displays']['lcd']['lines'][0], '2. Information')    # no 'title': the item label


class SignatoneQuadpro2PlotDrawStyle(unittest.TestCase):
    """Plot option 'draw' (source or literal): 'line' (default, as before), 'marker' (points without connecting lines) or 'both' (Keithley 2450
    Graph > Data > Draw Style).  Without the option the frame has no 'draw' key and the picture is unchanged."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {}, 'displays': {'tab': {'kind': 'touch_tft', 'size': [200, 120]}},
            'screens': {'app': {'style': 'touch_gui', 'nav': {'display': 'tab', 'start': 'home', 'pages': {'home': {'widgets': [
                {'id': 'chart', 'type': 'plot', 'rect': [0, 0, 200, 120], 'span': 40.0, 'series': [{'src': {'state': 'pts'}, 'color': 'green'}]}]}}}}},
            'start_screen': 'app'}

    def test_draw_styles(self):
        h = Host(pts=[[0.0, 0.0], [20.0, 10.0], [40.0, 0.0]], style='marker')
        plain = pu.PanelUI(copy.deepcopy(self.SPEC), h)
        self.assertNotIn('draw', next(w for w in plain.frame()['displays']['tab']['widgets'] if w['id'] == 'chart'))
        spec = copy.deepcopy(self.SPEC)
        chart = spec['screens']['app']['nav']['pages']['home']['widgets'][0]
        chart['draw'] = {'state': 'style'}
        p = pu.PanelUI(spec, h)
        green = pu.ROLE_COLORS['green']
        count = lambda img: int(((img == green).all(axis=2)).sum())             # noqa: E731
        self.assertEqual(next(w for w in p.frame()['displays']['tab']['widgets'] if w['id'] == 'chart')['draw'], 'marker')
        marker = count(p.render('tab'))
        h.state['style'] = 'line'
        line = p.render('tab')
        self.assertTrue((line == plain.render('tab')).all())                    # 'line' draws exactly what the default draws
        h.state['style'] = 'both'
        both = count(p.render('tab'))
        self.assertGreater(count(line), marker)                                  # lines across the chart cover more pixels than 3 markers
        self.assertGreater(both, count(line))                                    # markers on top of the lines
        self.assertGreater(marker, 0)


class BuchiMinisprayDarkPalette(unittest.TestCase):
    """Display 'dark_if' + 'colors_dark' (S-300 System > Display > Dark Mode: light texts on a dark background): the frame reports 'dark' and the
    rendering uses the dark palette for the roles it lists.  Without the option the frame has no 'dark' key and the picture is unchanged."""
    SPEC = {'spec_version': '0.1', 'ui_class': 'touchscreen_gui', 'inputs': {},
            'displays': {'tab': {'kind': 'touch_tft', 'size': [120, 60], 'colors': {'background': [246, 247, 249], 'text': [30, 34, 40], 'panel': [228, 232, 238]}}},
            'screens': {'app': {'style': 'touch_gui', 'nav': {'display': 'tab', 'start': 'home', 'pages': {'home': {'widgets': [
                {'id': 'b', 'type': 'button', 'label': 'OK', 'rect': [10, 10, 60, 30]}]}}}}},
            'start_screen': 'app'}

    def test_dark_palette(self):
        h = Host(dark=False)
        plain = pu.PanelUI(copy.deepcopy(self.SPEC), h)
        self.assertNotIn('dark', plain.frame()['displays']['tab'])
        spec = copy.deepcopy(self.SPEC)
        spec['displays']['tab'].update(dark_if={'state': 'dark'}, colors_dark={'background': [24, 26, 30], 'text': [235, 238, 242], 'panel': [52, 57, 66]})
        self.assertEqual(pu.check_spec(spec), [])
        p = pu.PanelUI(spec, h)
        self.assertFalse(p.frame()['displays']['tab']['dark'])
        self.assertTrue((p.render('tab') == plain.render('tab')).all())       # light mode: the picture of the plain spec
        h.state['dark'] = True
        self.assertTrue(p.frame()['displays']['tab']['dark'])
        img = p.render('tab')
        self.assertEqual(tuple(img[55, 110]), (24, 26, 30))                   # dark background
        self.assertEqual(tuple(img[25, 15]), (52, 57, 66))                    # the button drawn in the dark panel colour


if __name__ == '__main__':
    unittest.main()
