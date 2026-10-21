"""front-panel tests for CHEM-163 biotek_405ts (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/biotek_405ts.py):
python3 -m pytest tests/test_panel_ui_biotek_405ts.py -q

Every gesture goes through the session API (panel.touch / panel.press, the runtime's normal invoke path); widgets are found by id or label in the
structured frame (panel.frame, evaluator) and tapped at their centre.  Themes: the six key procedures of the menu survey (run a protocol, create or
modify a protocol, the Verify clog test, the daily rinse, AutoPrime, protocols from a USB stick); the other screens (Quick Prime / Wash, Delay prompts
and STOP, Options with the magnet offset and the sensors, Self-Check, Set Time and Date, BioStack, scroll arrows, Help); panel = direct commands (the
same runtime state); the 1.1.0 device commands directly (results, refusals without side effects, ownership); refusals shown as the instrument's
messages; no screen time-out; power; render determinism and the evidence PNGs; contract entries."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import biotek_405ts as rt  # noqa: E402

pu = rt._panel_ui
INST = 'washer_01'
SCREEN = 'screen'
OUT = ROOT / 'outputs/panel_ui/biotek_405ts'
TWEEN = 'water_0_1pct_tween20'
PLATE = dict(rt.PLATE_EXAMPLE, liquid_id=TWEEN, fill_volume_m3=300e-9, unbound_mol=1e-12)
SURVEY_TASKS = ['run a protocol', 'create or modify a protocol', 'run the Verify clog test', 'daily rinse', 'set AutoPrime', 'import protocols from USB']
DONE_TASKS = set()


class Rig:
    """A runtime with an agent and an evaluator session; the operator's actions only through panel.* (bottles, plates and waste are host actions)."""

    def __init__(self, power=True, plate=False, liquid=TWEEN, **plant):
        self.h = rt.Biotek405tsRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, **plant))
        for b in 'ABCD':
            self.call(self.A, 'buffer.load', bottle=b, liquid_id=liquid, volume_m3=0.004)
        if power:
            self.press('power')
        if plate:
            self.load_plate()

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.time_s == t0, cap                   # commands and gestures take no simulated time
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.call(self.E, 'sim.step', dt_s=float(dt))

    def busy(self):
        return self.h.op is not None or self.h._proto_active()

    def run_until_idle(self, dt=2.0, limit_s=48 * 3600.0):
        t = 0.0
        while self.h.op is not None or (self.h.proto is not None and self.h.proto['status'] in ('running', 'waiting')):
            self.step(dt)
            t += dt
            assert t < limit_s

    def load_plate(self, plate=PLATE):
        self.call(self.A, 'plate.provide', **plate)
        self.call(self.A, 'plate.load', plate_id=plate['plate_id'])

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCREEN]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'] if f['screen'] != 'main' else f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCREEN, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        """Tap a button of the top overlay (keyboard key, option, dialog button) by its label."""
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def row(self, wid, first):
        """Tap the row of a list / form widget whose first cell is this text (scrolling the rows by dragging until it shows)."""
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

    def cells(self, wid):
        return {c[0]: (c[1] if len(c) > 1 else None) for c in self.widgets()[wid]['cells']}

    def all_cells(self, wid):
        """All rows of a scrolling form (drags back to the top first)."""
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        for _ in range(10):
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        out = {}
        for _ in range(20):
            w = self.widgets()[wid]
            out.update({c[0]: (c[1] if len(c) > 1 else None) for c in w['cells']})
            if w['scroll'] + len(w['cells']) >= w['count']:
                return out
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        return out

    def type(self, text):
        """Type on the open keyboard (the shown text is replaced): upper case through Shift, digits through 12@ on the alpha keyboard."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        if '7' in labels and 'q' not in labels and 'Q' not in labels:           # numeric keyboard
            if ov['field']:
                self.ov('Del')
            for ch in text:
                assert self.ov(ch), ch
            return
        if ov['field']:
            self.ov('Del')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch in '.-':
                if mode == 'sym':
                    self.ov('abc')
                self.ov(ch)
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,':
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
        assert self.overlay()['field'] == text, (self.overlay()['field'], text)

    def enter(self, wid, label, text):
        self.row(wid, label)
        self.type(text)
        return self.ov('OK')

    def choose(self, wid, label, option):
        self.row(wid, label)
        return self.ov(option)

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def dialog(self):
        """(title, text) of the dialog on top (its lines joined)."""
        ov = self.overlay()
        return None if ov is None or ov.get('kind') != 'dialog' else (ov['title'], ' '.join(ov['lines']))

    def spin(self, part, n):
        """Press the + of a date / time picker column n times."""
        plus = [b for b in self.overlay()['buttons'] if b['label'] == '+'][part]
        x, y, w, h = plus['rect']
        for _ in range(n):
            self.touch(x + w // 2, y + h // 2)

    def unsupported(self):
        return self.frame()['ui'].get('unsupported')

    # ---- navigation shortcuts (all by taps)
    def home(self):
        if self.page() != 'home':
            w = next(k for k in self.widgets() if k.endswith('_home'))
            self.tap(w)
        assert self.page() == 'home'

    def open_protocol(self, name):
        self.home()
        self.row('home_list', name)
        assert self.page() == 'run_setup'

    def define(self):
        self.home()
        self.tap('home_define')
        assert self.page() == 'define'

    def add_step(self, kind, fields=(), choices=(), at=None):
        """Editor: highlight a row (at = its first cell, default the selection), Add, the step button, set the fields, OK."""
        if at is not None:
            self.row('ed_steps', at)
        self.tap('ed_add')
        self.tap(f'add_{kind}')
        form = 'st_wash_disp' if kind == 'wash' else f'st_{kind}_form'
        for label, option in choices:
            self.choose_any(kind, label, option)
        for label, text in fields:
            self.enter_any(kind, label, text)
        self.tap(f'st_{kind}_ok')
        assert self.page() == 'editor', self.page()
        return form

    def _wash_form_with(self, label):
        for tab, wid in (('disp', 'st_wash_disp'), ('asp', 'st_wash_asp'), ('opt', 'st_wash_opt')):
            self.tap(f'st_wash_tab_{tab}')
            if label in self.all_cells(wid):
                return wid
        raise AssertionError(label)

    def enter_any(self, kind, label, text):
        wid = ('st_wash_cycles' if label == 'Number of Cycles' else self._wash_form_with(label)) if kind == 'wash' else f'st_{kind}_form'
        return self.enter(wid, label, text)

    def choose_any(self, kind, label, option):
        wid = self._wash_form_with(label) if kind == 'wash' else f'st_{kind}_form'
        return self.choose(wid, label, option)


def editor_lines(r):
    return [c[0] for c in r.widgets()['ed_steps']['cells']]


# ---------------------------------------------------------------- the key procedures of the menu survey, panel gestures only
class SurveyProcedures(unittest.TestCase):
    def test_run_a_protocol(self):
        r = Rig(plate=True)                                                     # 1 plate on the carrier, A1 at the left rear (host action)
        self.assertEqual(r.page(), 'home')
        self.assertEqual(r.cells('home_list')['W-Corning_FLAT'], '<n/a>')
        self.assertTrue(r.row('home_list', 'W-Corning_FLAT'))                   # 2 touch the protocol on the Home screen
        self.assertEqual(r.page(), 'run_setup')
        self.assertEqual([c[0] for c in r.widgets()['rs_steps']['cells']], ['1. Wash: 3 cycles, 300 uL, buffer A'])
        self.assertTrue(r.tap('rs_start'))                                      # 3 Start
        self.assertEqual(r.page(), 'run')
        self.assertEqual(r.text('run_status'), 'Running')
        self.assertEqual(r.text('run_step'), 'Step 1 (1 of 1): Wash')
        r.step(3.0)
        self.assertIn(r.text('run_phase'), ('Aspirating', 'Dispensing', 'Moving the plate'))
        self.assertTrue(r.text('run_cycles').startswith('Cycles remaining: '))
        r.run_until_idle(0.5)
        self.assertEqual(r.text('run_status'), 'Run complete')
        run = r.val('protocol.run')
        self.assertEqual((run['name'], run['status'], run['records']), ('W-Corning_FLAT', 'completed', ['WS0001']))
        rec = r.call(r.A, 'measurement.result', record_id='WS0001')
        self.assertEqual((rec['status'], rec['cycles_done'], rec['settings']['protocol']), ('completed', 3, 'W-Corning_FLAT'))
        self.assertLess(max(r.val('truth.residual_m3')), 5e-9)                  # the wash leaves a small residual
        self.assertTrue(r.tap('run_ok'))
        self.assertEqual(r.page(), 'home')
        self.assertEqual(r.cells('home_list')['W-Corning_FLAT'], '01 January 26 08:00')   # Last run (instrument clock)
        DONE_TASKS.add('run a protocol')

    def test_create_or_modify_a_protocol(self):
        r = Rig(plate=True)
        r.define()                                                              # 1 Home > Define
        self.assertTrue(r.tap('def_create'))                                    # 2 Create
        self.assertEqual((r.page(), r.text('ed_name')), ('editor', 'Name: <new protocol>'))
        self.assertTrue(r.tap('ed_info'))                                       # 3 Name and plate type (Info)
        self.assertEqual(r.dialog()[0], 'Info')
        r.ov('Name')
        r.type('ELISA wash')
        r.ov('OK')
        r.ov('Plate Type')
        r.ov('Costar 9017 Clear Microtiter Plates')
        self.assertTrue(r.ov('OK'))
        self.assertEqual(r.val('protocol.editor')['name'], 'ELISA wash')
        self.assertEqual(r.val('protocol.editor')['plate_type'], 'costar_9017')
        r.add_step('prime', fields=[('Volume', '50')])                          # 4 Add steps and set their parameters
        r.add_step('wash', fields=[('Number of Cycles', '2'), ('Volume', '250')], choices=[('Shake', 'Yes')], at='<end of steps>')
        r.add_step('soak', fields=[('Soak Duration', '20')], at='<end of steps>')
        self.assertEqual(editor_lines(r), ['1. Prime: 50 mL, buffer A, rate 9', '2. Wash: 2 cycles, 250 uL, buffer A, shake', '3. Soak 20 s', '<end of steps>'])
        r.row('ed_steps', '2. Wash: 2 cycles, 250 uL, buffer A, shake')        # Edit a step
        self.assertTrue(r.tap('ed_edit'))
        self.assertEqual(r.page(), 'step_wash')
        self.assertEqual(r.cells('st_wash_cycles'), {'Number of Cycles': '2'})
        r.enter('st_wash_cycles', 'Number of Cycles', '4')
        r.tap('st_wash_ok')
        self.assertEqual(r.val('protocol.editor')['steps'][1]['cycles'], 4)
        r.row('ed_steps', '3. Soak 20 s')                                       # cut and paste: the soak before the wash
        self.assertTrue(r.tap('ed_cut'))
        r.row('ed_steps', '2. Wash: 4 cycles, 250 uL, buffer A, shake')
        self.assertTrue(r.tap('ed_paste'))
        self.assertEqual([s['type'] for s in r.val('protocol.editor')['steps']], ['prime', 'soak', 'wash'])
        self.assertTrue(r.tap('ed_validate'))                                   # 5 Validate
        self.assertEqual(r.dialog(), ('Validate', 'Validation successful.'))
        r.ov('OK')
        self.assertTrue(r.tap('ed_save'))                                       # 6 Save: on the Home screen
        self.assertEqual(r.dialog(), ('Save', 'Protocol ELISA wash saved. It is on the Home screen.'))
        r.ov('OK')
        saved = next(p for p in r.val('protocols.list') if p['name'] == 'ELISA wash')
        self.assertEqual((saved['kind'], saved['locked'], len(saved['steps']), saved['plate_type']), ('user', False, 3, 'costar_9017'))
        self.assertAlmostEqual(saved['steps'][2]['dispense_volume_m3'], 250e-9)
        r.tap('editor_home')
        self.assertIsNone(r.val('protocol.editor'))                             # leaving the editor closes it
        self.assertIn('ELISA wash', r.cells('home_list'))
        r.open_protocol('ELISA wash')
        r.tap('rs_start')
        r.run_until_idle(0.5)
        run = r.val('protocol.run')
        self.assertEqual((run['status'], run['records']), ('completed', ['PR0001', 'WS0001', 'WS0002']))
        self.assertEqual(r.call(r.A, 'measurement.result', record_id='WS0002')['cycles_done'], 4)
        self.assertIn('plate_type_differs_from_protocol', r.call(r.A, 'measurement.result', record_id='WS0001')['warnings'])   # Costar 3590 loaded
        DONE_TASKS.add('create or modify a protocol')

    def test_run_the_verify_clog_test(self):
        r = Rig()
        r.tap('home_quick')
        before = r.h.read('evaluator')
        r.tap('q_verify')                                                       # Quick > Verify Manifold: "Q" models only
        self.assertEqual(r.dialog(), ('Verify', 'Verify Technology is available on "Q" models only.'))
        self.assertEqual(r.unsupported(), 'Verify Technology is on "Q" models only')
        self.assertEqual(r.h.read('evaluator'), before)
        DONE_TASKS.add('run the Verify clog test')

    def test_daily_rinse(self):
        r = Rig(liquid='water')
        r.call(r.A, 'buffer.load', bottle='A', liquid_id='water', volume_m3=0.001)   # 1 about one litre of water in bottle A
        r.tap('home_maint')                                                     # 2 Home > Maintenance
        self.assertEqual(r.page(), 'maint')
        self.assertIn('W-DAY_RINSE', r.cells('maint_list'))
        self.assertFalse(r.tap('maint_start'))                                  # nothing selected yet
        r.row('maint_list', 'W-DAY_RINSE')
        self.assertTrue(r.tap('maint_start'))                                   # 3 Start
        self.assertEqual((r.page(), r.text('run_step')), ('run', 'Step 1 (1 of 1): Prime'))
        r.run_until_idle(5.0)
        rec = r.call(r.A, 'measurement.result', record_id='PR0001')
        self.assertEqual((rec['status'], rec['settings']['volume_m3'], rec['settings']['buffer']), ('completed', 500e-6, 'A'))
        self.assertAlmostEqual(r.val('buffer.a.volume_m3'), 0.001 - 500e-6, places=9)
        self.assertEqual(r.h.read('evaluator')['truth.line_composition'], {'water': 1.0})
        r.tap('run_ok')                                                         # 4 Home when completed
        self.assertEqual(r.page(), 'home')
        DONE_TASKS.add('daily rinse')

    def test_set_autoprime(self):
        r = Rig()
        r.tap('home_inst')                                                      # 1 Home > Instrument > AutoPrime
        r.tap('cfg_autoprime')
        self.assertEqual(r.page(), 'autoprime')
        r.choose('ap_form', 'AutoPrime', 'On')
        r.enter('ap_form', 'Interval', '30')                                    # 2 idle interval (minutes)
        r.enter('ap_form', 'Submerge Duration', '10')                           # 3 submerge duration
        r.enter('ap_form', 'Flow Rate', '8')                                    # 4 rate, volume, buffer valve
        r.enter('ap_form', 'Volume', '40')
        r.choose('ap_form', 'Buffer', 'B')
        self.assertEqual(r.val('autoprime.settings'), dict(enabled=True, interval_min=30, submerge_min=10, rate=8, volume_m3=40e-6, buffer='B'))
        r.step(3600.0)                                                          # not at the Home screen: AutoPrime waits
        self.assertIsNone(r.h.op)
        r.home()                                                                # any touch restarts the interval
        r.step(29 * 60.0)
        self.assertIsNone(r.h.op)
        r.step(60.0)
        self.assertEqual(r.page(), 'run')                                       # AutoPrime runs at the Home screen after 30 idle minutes
        self.assertEqual(r.text('run_status'), 'Running')
        self.assertTrue(r.h.op['autoprime'])
        r.step(120.0)
        self.assertTrue(r.val('manifold.submerged'))                            # tubes submerged in the trough
        r.step(11 * 60.0)
        self.assertEqual(r.page(), 'home')                                      # back to Home by itself
        rec = r.call(r.A, 'measurement.result', record_id='PR0001')
        self.assertEqual((rec['status'], rec['settings']['autoprime'], rec['settings']['buffer'], rec['settings']['submerge_s']), ('completed', True, 'B', 600.0))
        r.step(30 * 60.0)                                                       # STOP interrupts it
        self.assertTrue(r.h.op['autoprime'])
        self.assertTrue(r.tap('run_stop'))
        self.assertEqual(r.call(r.A, 'measurement.result', record_id='PR0002')['status'], 'cancelled')
        self.assertEqual((r.page(), r.val('manifold.z_m')), ('home', 0.0))    # back to Home after an AutoPrime
        DONE_TASKS.add('set AutoPrime')

    def test_import_protocols_from_usb(self):
        r = Rig()
        r.tap('home_inst')                                                      # Instrument > Next > Other > Transfer Protocols
        r.tap('cfg_next')
        r.tap('next_other')
        before = r.val('protocols.list')
        r.tap('oth_transfer')
        self.assertEqual(r.dialog(), ('USB', 'No USB memory stick found in the top USB port.'))
        self.assertEqual(r.unsupported(), 'Transfer Protocols: no USB memory stick in the top USB port')
        self.assertEqual(r.val('protocols.list'), before)
        r.ov('OK')
        r.tap('oth_video')                                                      # Show Video needs the stick too
        self.assertEqual(r.dialog()[0], 'USB')
        DONE_TASKS.add('import protocols from USB')


# ---------------------------------------------------------------- the other screens
class Screens(unittest.TestCase):
    def test_quick_prime_and_quick_wash(self):
        r = Rig()
        r.tap('home_quick')
        r.tap('q_prime')
        self.assertEqual(r.cells('qprime_form'), {'Buffer': 'A', 'Volume': '300 mL', 'Flow Rate': '9'})
        r.choose('qprime_form', 'Buffer', 'C')
        r.enter('qprime_form', 'Volume', '120')
        self.assertTrue(r.tap('qprime_start'))
        self.assertEqual((r.page(), r.text('run_status'), r.frame()['displays'][SCREEN]['title']), ('run', 'Running', 'Prime'))
        r.run_until_idle(5.0)
        rec = r.call(r.A, 'measurement.result', record_id='PR0001')
        self.assertEqual((rec['settings']['buffer'], rec['settings']['volume_m3'], rec['settings']['rate']), ('C', 120e-6, 9))
        r.tap('run_ok')
        r.load_plate()
        r.tap('home_quick')
        r.tap('q_prime')
        r.tap('qprime_start')                                                   # Quick Prime keeps the 1.0.0 rule: no plate on the carrier
        self.assertEqual(r.dialog(), ('Error', 'Error PLATE_ON_CARRIER Remove the plate from the carrier before priming.'))
        r.ov('OK')
        r.tap('qprime_prev')
        r.tap('q_wash')
        r.enter('qwash_form', 'Wash Cycles', '2')
        r.enter('qwash_form', 'Fluid Volume', '200')
        self.assertTrue(r.tap('qwash_start'))
        r.run_until_idle(0.5)
        rec = r.call(r.A, 'measurement.result', record_id='WS0001')
        self.assertEqual((rec['settings']['cycles'], rec['settings']['dispense_volume_m3'], rec['cycles_done']), (2, 200e-9, 2))
        self.assertEqual({k: v for k, v in rec['settings'].items() if k not in ('cycles', 'dispense_volume_m3')},
                         {k: v for k, v in rt.WASH_EXAMPLE.items() if k not in ('cycles', 'dispense_volume_m3')})   # the defaults for the rest

    def test_delay_prompt_continue_and_stop(self):
        r = Rig()
        r.tap('home_maint')
        r.row('maint_list', 'W-DECONTAMINATE')
        r.tap('maint_start')
        self.assertEqual(r.text('run_status'), 'Waiting for Continue')         # the Delay message of the protocol
        self.assertEqual(r.text('run_prompt'), 'Connect the disinfectant to Buffers A-D, then press Continue.')
        self.assertIn('run_continue', r.widgets())
        r.step(3600.0)
        self.assertEqual(r.val('protocol.run')['status'], 'prompt')             # it waits
        r.call(r.A, 'buffer.load', bottle='A', liquid_id='naoh_0_1m', volume_m3=0.001)
        r.call(r.A, 'buffer.load', bottle='B', liquid_id='water', volume_m3=0.0)
        self.assertTrue(r.tap('run_continue'))
        self.assertEqual((r.text('run_status'), r.text('run_step')), ('Running', 'Step 2 (2 of 10): Prime'))
        r.step(60.0)
        self.assertEqual(r.val('protocol.run')['status'], 'failed')             # bottle B empty (fluid sensor on): the run stops with an error
        self.assertEqual(r.text('run_error'), 'Error BUFFER_EMPTY: Insufficient fluid in the supply bottle. Fill the bottle.')
        self.assertEqual(r.text('run_status'), 'Run stopped: error')
        r.tap('run_ok')
        r.call(r.A, 'buffer.load', bottle='B', liquid_id=TWEEN, volume_m3=0.004)
        r.tap('home_maint')
        r.row('maint_list', 'W-OVERNIGHT_LOOP')
        r.tap('maint_start')
        r.step(3 * 3600.0)
        self.assertEqual((r.text('run_step'), r.text('run_phase')), ('Step 2 (1 of 999): Prime', 'Tubes submerged'))
        self.assertTrue(r.tap('run_stop'))                                      # STOP: manifold up, carrier home
        self.assertEqual((r.val('protocol.run')['status'], r.text('run_status'), r.val('manifold.z_m')), ('cancelled', 'Run stopped (STOP)', 0.0))
        self.assertFalse(r.val('manifold.submerged'))

    def test_options_magnet_offset_sensors_and_self_check(self):
        r = Rig()
        r.tap('home_inst')
        r.tap('cfg_options')
        self.assertEqual(r.cells('opt_washer'), {'Manifold': '96-tube', 'Plate Carrier': 'Standard', 'Magnet in use': 'No', 'Height Offset': '0 mm'})
        r.choose('opt_washer', 'Magnet in use', 'Yes')
        r.enter('opt_washer', 'Height Offset', '1.9')
        self.assertEqual((r.val('washer.settings')['magnet_in_use'], r.val('washer.settings')['magnet_offset_mm']), (True, 1.9))
        r.tap('opt_tab_sensors')
        self.assertEqual(r.cells('opt_sensors'), {'Fluid': 'On', 'Flow': 'On', 'Waste': 'On', 'Vac Filtr': 'Off'})
        r.choose('opt_sensors', 'Waste', 'Off')
        r.tap('opt_tab_advanced')
        r.enter('opt_advanced', 'Vacuum Dissipate', '8')
        self.assertEqual(r.val('washer.settings'), dict(rt.WASHER_DEFAULT, magnet_in_use=True, magnet_offset_mm=1.9, sensor_waste=False, vacuum_dissipate_s=8.0))
        r.row('opt_advanced', 'Vacuum Filtration')                              # the module is not installed
        self.assertEqual(r.dialog(), ('Vacuum Filtration', 'The vacuum filtration module is not installed.'))
        r.ov('OK')
        # the offset raises every wash height: more residual than the same wash without it
        a, b = Rig(plate=True), Rig(plate=True)
        a.call(a.A, 'washer.configure', **dict(rt.WASHER_DEFAULT, magnet_in_use=True, magnet_offset_mm=1.9))
        for g in (a, b):
            g.call(g.A, 'wash.run', protocol=rt.WASH_EXAMPLE)
            g.run_until_idle(0.5)
        self.assertGreater(sum(a.val('truth.residual_m3')), 10 * sum(b.val('truth.residual_m3')))
        # waste sensor off: no 'waste full' refusal at 90 %
        c = Rig(plate=True)
        c.h.waste = 0.92 * c.h.plant['waste_capacity_m3']
        self.assertEqual(c.call(c.A, 'wash.run', expect='rejected', protocol=rt.WASH_EXAMPLE)['error']['code'], 'WASTE_FULL')
        c.call(c.A, 'washer.configure', **dict(rt.WASHER_DEFAULT, sensor_waste=False))
        c.call(c.A, 'wash.run', protocol=rt.WASH_EXAMPLE)
        c.run_until_idle(0.5)
        self.assertEqual(c.call(c.A, 'measurement.result', record_id='WS0001')['status'], 'completed')
        # Self-Check: passes; a 192-tube setting with the 96-tube manifold fails
        r.tap('opt_tab_washer')
        r.choose('opt_washer', 'Manifold', '192-tube')
        r.tap('options_prev')
        self.assertTrue(r.tap('cfg_selfcheck'))
        self.assertEqual(r.dialog(), ('Self-Check', 'Error: MANIFOLD_SETTING_MISMATCH Manifold setting does not match.'))
        r.ov('OK')
        self.assertEqual(r.text('cfg_sc'), 'Self-Check: Failed: MANIFOLD_SETTING_MISMATCH  01 January 26 08:00')
        r.tap('cfg_options')
        r.choose('opt_washer', 'Manifold', '96-tube')
        r.tap('options_prev')
        r.tap('cfg_selfcheck')
        self.assertEqual(r.dialog(), ('Self-Check', 'Self-Check passed. '))

    def test_set_time_and_date(self):
        r = Rig()
        self.assertEqual((r.text('home_date'), r.text('home_time')), ('01 January 26', '08:00'))
        r.tap('home_inst')
        r.tap('cfg_next')
        r.tap('next_other')
        r.tap('oth_time')
        self.assertEqual(r.cells('td_form'), {'Time Zone': 'GMT+00:00', 'Date': '2026-01-01', 'Time': '08:00'})
        r.choose('td_form', 'Time Zone', 'GMT+02:00')
        r.row('td_form', 'Date')                                                # the calendar: day / month / year spinners (+ / -)
        r.spin(1, 9)
        r.spin(0, 18)
        r.ov('OK')
        r.row('td_form', 'Time')                                                # 24 hour clock: hours / minutes
        r.spin(0, 6)
        r.spin(1, 5)
        r.ov('OK')
        self.assertEqual(r.cells('td_form'), {'Time Zone': 'GMT+02:00', 'Date': '2026-10-19', 'Time': '14:05'})
        self.assertTrue(r.tap('td_save'))
        self.assertEqual(r.page(), 'other')
        self.assertEqual(r.val('clock.settings')['time_zone_h'], 2)
        r.home()
        self.assertEqual((r.text('home_date'), r.text('home_time')), ('19 October 26', '14:05'))
        r.step(3600.0)
        self.assertEqual(r.text('home_time'), '15:05')

    def test_time_picker_spinners(self):
        r = Rig()
        r.tap('home_inst')
        r.tap('cfg_next')
        r.tap('next_other')
        r.tap('oth_time')
        r.row('td_form', 'Time')
        ov = r.overlay()
        self.assertEqual(ov['kind'], 'spinner')
        labels = [b['label'] for b in ov['buttons']]
        self.assertIn('OK', labels)
        r.ov('+')                                                               # the hour up by one
        r.ov('OK')
        self.assertEqual(r.cells('td_form')['Time'], '09:00')
        r.tap('td_save')
        r.home()
        self.assertEqual(r.text('home_time'), '09:00')

    def test_define_delete_locked_and_copy(self):
        r = Rig()
        r.define()
        self.assertEqual(r.cells('def_list')['W-DAY_RINSE'], 'Maintenance (locked)')
        r.row('def_list', 'W-DAY_RINSE')
        r.tap('def_delete')
        r.ov('Delete')
        self.assertEqual(r.dialog(), ('Error', 'Error PROTOCOL_LOCKED The protocol is locked. Save it under a new name (Info).'))
        r.ov('OK')
        r.tap('def_edit')                                                       # a copy: edit the locked protocol, save it under a new name
        r.tap('ed_save')
        self.assertEqual(r.dialog()[1], 'Error PROTOCOL_LOCKED The protocol is locked. Save it under a new name (Info).')
        r.ov('OK')
        r.tap('ed_info')
        r.ov('Name')
        r.type('MY_RINSE')
        r.ov('OK')
        r.ov('OK')
        r.tap('ed_save')
        r.ov('OK')
        r.tap('editor_prev')
        self.assertEqual(r.cells('def_list')['MY_RINSE'], 'User')
        r.row('def_list', 'MY_RINSE')
        r.tap('def_delete')
        self.assertEqual(r.dialog(), ('Delete', 'Delete MY_RINSE? The protocol file will be permanently deleted.'))
        r.ov('Delete')
        self.assertNotIn('MY_RINSE', [p['name'] for p in r.val('protocols.list')])

    def test_editor_refusals_loop_and_test_run(self):
        r = Rig(plate=True)
        r.define()
        r.tap('def_create')
        r.tap('ed_validate')                                                    # an empty protocol
        self.assertEqual(r.dialog()[1], 'Error PROTOCOL_EMPTY The protocol has no steps to run.')
        r.ov('OK')
        r.add_step('loop', fields=[('Number of Loops', '3')])
        r.add_step('dispense', fields=[('Volume', '100'), ('Vacuum Delay', '100')], at='2. End Loop')
        r.add_step('aspirate', fields=[('Aspirate Z', '40')], at='3. End Loop')
        r.add_step('delay', fields=[('Delay', '10'), ('Message', 'next')], at='<end of steps>')
        r.add_step('remark', fields=[('Remark', 'done')], at='<end of steps>')
        self.assertEqual(editor_lines(r), ['1. Loop 3 times', '2.   Dispense: 100 uL, rate 7, buffer A', '3.   Aspirate: Z 40, rate 3', '4. End Loop',
                                           '5. Delay: 10 s - next', '6. Remark: done', '<end of steps>'])
        r.tap('ed_save')                                                        # no name yet
        self.assertEqual(r.dialog()[1], 'Error NAME_REQUIRED Enter a protocol name (Info).')
        r.ov('OK')
        r.tap('ed_add')                                                         # BioStack and AutoClean steps need absent hardware
        r.tap('add_biostack')
        self.assertEqual(r.dialog()[0], 'BioStack')
        r.ov('OK')
        r.tap('add_autoclean')
        self.assertEqual(r.dialog()[0], 'AutoClean')
        r.ov('OK')
        r.tap('add_exit')
        self.assertTrue(r.tap('ed_test'))                                       # Test Run without saving
        self.assertEqual(r.frame()['displays'][SCREEN]['title'], '<new protocol> (Test Run)')
        r.run_until_idle(0.5)
        run = r.val('protocol.run')
        self.assertEqual((run['status'], len(run['records']), run['test']), ('completed', 6, True))
        self.assertTrue(r.tap('run_ok'))
        self.assertEqual(r.page(), 'editor')                                    # back in the editor after a Test Run
        r.row('ed_steps', '1. Loop 3 times')                                    # Cut on Loop takes the whole loop
        r.tap('ed_cut')
        self.assertEqual(editor_lines(r), ['1. Delay: 10 s - next', '2. Remark: done', '<end of steps>'])
        r.tap('ed_validate')                                                    # a Delay step is a step to run
        self.assertEqual(r.dialog(), ('Validate', 'Validation successful.'))

    def test_cell_wash_option_and_validation(self):
        r = Rig(plate=True)
        r.open_protocol('W-CELLWASH_96')
        r.tap('rs_start')                                                       # rates 1-2 and CW travel need the cell wash option
        self.assertEqual(r.dialog()[1], 'Error CELL_WASH_OPTION_ABSENT Cell wash rates (1-2, CW travel rates) need the cell wash option.')
        r.ov('OK')
        g = Rig(plate=True, cell_wash_installed=True)
        g.open_protocol('W-CELLWASH_96')
        g.tap('rs_start')
        g.run_until_idle(0.5)
        self.assertEqual(g.val('protocol.run')['status'], 'completed')
        self.assertGreater(min(v for v in g.val('truth.residual_m3')), 50e-9)    # aspirate height 50 steps: about 100 uL left

    def test_scroll_arrows_and_help(self):
        r = Rig()
        for i in range(12):                                                     # user protocols (direct commands) fill the Home list
            r.call(r.A, 'protocol.edit_open', name=None)
            r.call(r.A, 'protocol.edit_info', name=f'P{i:02d}', plate_type='costar_3590')
            r.call(r.A, 'protocol.edit_step', action='insert', index=1, step=rt.STEP_DEFAULTS['wash'])
            r.call(r.A, 'protocol.save')
        r.call(r.A, 'protocol.edit_close')
        w = r.widgets()['home_list']
        self.assertEqual((w['count'], w['scroll'], w['cells'][0][0]), (14, 0, 'W-CELLWASH_96'))
        self.assertTrue(r.tap('home_down'))
        self.assertEqual(r.widgets()['home_list']['scroll'], 4)                 # clamped: 10 rows shown
        r.tap('home_up')
        self.assertEqual(r.widgets()['home_list']['scroll'], 0)
        for pid in ('home', 'quick', 'define', 'maint', 'config', 'options', 'autoprime', 'next', 'other', 'biostack'):
            dl = rt.PANEL_SPEC['dialogs'][f'd_help_{pid}']
            self.assertEqual(dl['title'], 'Help')
        r.tap('home_help')
        self.assertEqual(r.dialog()[0], 'Help')
        self.assertTrue(r.dialog()[1].startswith('Touch a protocol to select it'))
        r.ov('OK')
        r.tap('home_inst')
        r.tap('cfg_next')
        r.tap('next_biostack')
        for b in ('bs_installed', 'bs_basecode', 'bs_align'):
            r.tap(b)
            self.assertEqual(r.dialog(), ('BioStack', 'No BioStack found. Check the cabling and that the BioStack is turned on.'))
            r.ov('OK')

    def test_every_page_renders(self):
        r = Rig(plate=True)
        for pid in rt.PANEL_SPEC['screens']['main']['nav']['pages']:
            if pid == 'editor' or pid.startswith('step_'):
                r.call(r.A, 'protocol.edit_open', name='W-Corning_FLAT')
            r.h.panel.ui['page'] = pid
            img = r.h.panel.render(SCREEN)
            self.assertEqual(img.shape, (480, 640, 3), pid)
            self.assertGreater(len(r.disp()['widgets']), 1, pid)


# ---------------------------------------------------------------- panel = direct commands
class Equivalence(unittest.TestCase):
    def test_protocol_editor_and_run_equal_direct_commands(self):
        a, b = Rig(plate=True), Rig(plate=True)
        a.define()
        a.tap('def_create')
        a.tap('ed_info')
        a.ov('Name')
        a.type('EQ')
        a.ov('OK')
        a.ov('OK')
        a.add_step('wash', fields=[('Number of Cycles', '2'), ('Dispense X', '-45'), ('Bottom Wash Volume', '300')], choices=[('Bottom Wash', 'Yes'),
                                                                                                                              ('Travel Rate', '2')])
        a.add_step('prime', fields=[('Volume', '20'), ('Submerge Duration', '30')], choices=[('Submerge Tips', 'Yes')], at='<end of steps>')
        a.tap('ed_save')
        a.ov('OK')
        b.call(b.A, 'protocol.edit_open', name=None)
        b.call(b.A, 'protocol.edit_info', name='EQ', plate_type='costar_3590')
        b.call(b.A, 'protocol.edit_step', action='insert', index=1, step=dict(rt.STEP_DEFAULTS['wash'], cycles=2, dispense_x_steps=-45, bottom_wash=True,
                                                                              bottom_wash_volume_m3=300e-9, travel_rate='2'))
        b.call(b.A, 'protocol.edit_step', action='insert', index=2, step=dict(rt.STEP_DEFAULTS['prime'], volume_m3=20e-6, submerge=True, submerge_s=30.0))
        b.call(b.A, 'protocol.save')
        self.assertEqual(a.val('protocols.list'), b.val('protocols.list'))
        a.tap('editor_home')
        b.call(b.A, 'protocol.edit_close')
        a.open_protocol('EQ')
        a.tap('rs_start')
        b.call(b.A, 'protocol.run', name='EQ')
        for g in (a, b):
            g.run_until_idle(0.5)
        va, vb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual({k: v for k, v in va.items() if k != 'protocol.run'}, {k: v for k, v in vb.items() if k != 'protocol.run'})
        self.assertEqual(a.h.ledger(), b.h.ledger())
        self.assertEqual(a.val('protocol.run')['status'], 'completed')

    def test_settings_and_stop_equal_direct_commands(self):
        a, b = Rig(), Rig()
        a.tap('home_inst')
        a.tap('cfg_autoprime')
        a.choose('ap_form', 'AutoPrime', 'On')
        a.enter('ap_form', 'Interval', '15')
        b.call(b.A, 'autoprime.configure', **dict(rt.AUTOPRIME_DEFAULT, enabled=True))
        b.call(b.A, 'autoprime.configure', **dict(rt.AUTOPRIME_DEFAULT, enabled=True, interval_min=15))
        self.assertEqual(a.val('autoprime.settings'), b.val('autoprime.settings'))
        a.home()
        a.tap('home_maint')
        a.row('maint_list', 'PRIME_ALL_BUFFRS')
        a.tap('maint_start')
        b.call(b.A, 'protocol.run', name='PRIME_ALL_BUFFRS')
        a.step(20.0)
        b.step(20.0)
        a.tap('run_stop')
        b.call(b.A, 'run.stop')
        va, vb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual({k: v for k, v in va.items() if k != 'protocol.run'}, {k: v for k, v in vb.items() if k != 'protocol.run'})
        self.assertEqual((va['protocol.run']['status'], vb['protocol.run']['status']), ('cancelled', 'cancelled'))


# ---------------------------------------------------------------- the 1.1.0 device commands directly
class DeviceCommands(unittest.TestCase):
    def test_refusals_leave_the_state_unchanged(self):
        r = Rig()
        v0 = json.dumps(r.h.read('evaluator'), sort_keys=True)
        for cap, args, code in [('protocol.edit_info', dict(name='X', plate_type='costar_3590'), 'EDITOR_CLOSED'),
                                ('protocol.save', {}, 'EDITOR_CLOSED'), ('protocol.run', dict(name='nope'), 'UNKNOWN_PROTOCOL'),
                                ('protocol.run', dict(name='W-Corning_FLAT'), 'NO_PLATE'), ('protocol.delete', dict(name='W-DAY_RINSE'), 'PROTOCOL_LOCKED'),
                                ('protocol.resume', {}, 'NOT_WAITING'), ('run.stop', {}, 'NOT_RUNNING'),
                                ('autoprime.configure', dict(rt.AUTOPRIME_DEFAULT, rate=2), 'CELL_WASH_OPTION_ABSENT'),
                                ('clock.set', dict(time_zone_h=0, date='2026-02-30', time='10:00'), 'INVALID_ARGUMENT')]:
            res = r.call(r.A, cap, expect='rejected', **args)
            self.assertEqual(res['error']['code'], code, cap)
        self.assertEqual(json.dumps(r.h.read('evaluator'), sort_keys=True), v0)
        r.call(r.A, 'protocol.edit_open', name=None)
        v1 = json.dumps(r.h.read('evaluator'), sort_keys=True)
        for cap, args, code in [('protocol.edit_info', dict(name='bad/name', plate_type='costar_3590'), 'INVALID_NAME'),
                                ('protocol.edit_info', dict(name='X', plate_type='costar_3575'), 'PLATE_INCOMPATIBLE'),
                                ('protocol.edit_step', dict(action='paste', index=1, step=None), 'CLIPBOARD_EMPTY'),
                                ('protocol.edit_step', dict(action='replace', index=1, step=rt.STEP_DEFAULTS['wash']), 'INVALID_ARGUMENT'),
                                ('protocol.edit_step', dict(action='insert', index=1, step=rt.STEP_DEFAULTS['end_loop']), 'INVALID_ARGUMENT'),
                                ('protocol.validate', {}, 'PROTOCOL_EMPTY'), ('protocol.save', {}, 'NAME_REQUIRED')]:
            self.assertEqual(r.call(r.A, cap, expect='rejected', **args)['error']['code'], code, (cap, args))
        self.assertEqual(json.dumps(r.h.read('evaluator'), sort_keys=True), v1)
        r.call(r.A, 'protocol.edit_step', action='insert', index=1, step=dict(rt.STEP_DEFAULTS['soak'], soak=False))
        self.assertEqual(r.call(r.A, 'protocol.validate', expect='rejected')['error']['code'], 'SOAK_STEP_EMPTY')
        r.call(r.A, 'protocol.edit_step', action='replace', index=1, step=dict(rt.STEP_DEFAULTS['soak'], soak=True))
        for _ in range(rt.MAX_STEPS - 1):
            r.call(r.A, 'protocol.edit_step', action='insert', index=1, step=rt.STEP_DEFAULTS['remark'])
        self.assertEqual(r.call(r.A, 'protocol.edit_step', expect='rejected', action='insert', index=1, step=rt.STEP_DEFAULTS['remark'])['error']['code'],
                         'TOO_MANY_STEPS')
        self.assertEqual(r.call(r.A, 'protocol.validate')['steps'], rt.MAX_STEPS)

    def test_power_cycle_drops_the_edit_and_ends_a_run(self):
        r = Rig()
        r.call(r.A, 'protocol.edit_open', name='W-DAY_RINSE')
        r.call(r.A, 'protocol.run', name='W-RINSE_AND_SOAK')
        r.step(100.0)
        self.assertTrue(r.val('manifold.submerged'))
        self.assertTrue(r.press('power'))                                       # power off during the soak: the tubes stay submerged
        self.assertEqual((r.val('protocol.run')['status'], r.val('protocol.run')['error_code']), ('failed', 'POWER_LOST'))
        self.assertIsNone(r.val('protocol.editor'))
        self.assertTrue(r.val('manifold.submerged'))
        f = r.frame()
        self.assertEqual((f['screen'], f['displays'][SCREEN]['brightness']), ('dark', 0))
        self.assertFalse(r.touch(300, 200))                                     # the screen ignores touches while off
        idle = r.h.idle_H
        r.step(24 * 3600.0)
        self.assertEqual(r.h.idle_H, idle)                                      # submerged tips do not dry
        self.assertTrue(r.press('power'))
        self.assertEqual(r.page(), 'home')
        self.assertFalse(r.val('manifold.submerged'))                           # power-up homes the manifold; the trough keeps its fluid
        self.assertGreater(r.h.read('evaluator')['truth.trough_m3'], 0)
        r.call(r.A, 'instrument.self_check')                                    # Self-Check empties the trough (p.82)
        self.assertEqual(r.h.read('evaluator')['truth.trough_m3'], 0.0)

    def test_ownership_and_session_cancel(self):
        r = Rig(plate=True)
        r.h.register_principal('other', 'agent')
        O = r.h.session('other')
        oid = r.call(r.A, 'protocol.run', name='W-Corning_FLAT')['operation_id']
        r.step(3.0)
        res = O.invoke(instance_id=INST, capability_id='run.stop', arguments={}, request_id='x1')
        self.assertEqual(res['error']['code'], 'ACCESS_DENIED')                 # another agent's run
        self.assertTrue(r.A.read(instance_id=INST, state_ids=['operation.started_by_other_agent'])['observations']['operation.started_by_other_agent']['value']
                        is False)
        self.assertTrue(O.read(instance_id=INST, state_ids=['operation.started_by_other_agent'])['observations']['operation.started_by_other_agent']['value'])
        self.assertEqual(r.A.poll(oid)['status'], 'running')
        r.A.cancel(oid, 'c1')
        self.assertEqual((r.A.poll(oid)['status'], r.val('protocol.run')['status'], r.h.records['WS0001']['status']), ('cancelled', 'cancelled', 'cancelled'))
        oid = r.call(r.A, 'protocol.run', name='W-Corning_FLAT')['operation_id']
        r.run_until_idle(0.5)
        self.assertEqual(r.A.poll(oid)['status'], 'succeeded')
        d = r.A.describe(instance_id=INST)
        self.assertEqual(d['asset_version'], rt.ASSET_VERSION)
        self.assertTrue({'protocol.run', 'run.stop', 'panel.touch', 'panel.press'} <= set(d['capabilities']))

    def test_another_agents_run_is_view_only_on_the_panel(self):
        r = Rig(plate=True)
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.open_protocol('W-Corning_FLAT')
        r.tap('rs_start')                                                       # agent A starts the run on the screen: the run is A's
        r.step(3.0)
        oid = r.val('protocol.run')['operation_id']
        self.assertEqual(r.A.poll(oid)['status'], 'running')                    # A owns it (poll is owner-only)

        def b_tap(wid):
            x, y, w, h = r.widgets()[wid]['rect']
            res = B.invoke(instance_id=INST, capability_id='panel.touch', arguments=dict(display_id=SCREEN, x=x + w // 2, y=y + h // 2), request_id=f'b{wid}')
            return res['result']['accepted']
        before = json.dumps(r.h.read('evaluator'), sort_keys=True)
        self.assertFalse(b_tap('run_stop'))                                     # agent B: STOP of A's run is refused with the device's message
        self.assertEqual(r.dialog(), ('Error', 'Error ACCESS_DENIED This run was started by another user. It can be viewed, not changed.'))
        self.assertEqual(json.dumps(r.h.read('evaluator'), sort_keys=True), before)   # state unchanged
        self.assertEqual(B.invoke(instance_id=INST, capability_id='run.stop', arguments={}, request_id='b_direct')['error']['code'], 'ACCESS_DENIED')
        self.assertIn('Wash', r.text('run_step'))                               # the screen still shows the run to everybody
        r.ov('OK')
        self.assertTrue(r.tap('run_stop'))                                      # agent A may stop its own run
        self.assertEqual(r.val('protocol.run')['status'], 'cancelled')

    def test_power_switch_is_physical_for_everyone(self):
        """Lead clarification 10-19: the rear power switch is a hard switch; another agent may switch it off and the run ends as on a power
        loss (protocol run failed)."""
        r = Rig(plate=True)
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.open_protocol('W-Corning_FLAT')
        r.tap('rs_start')                                                       # agent A's run
        r.step(3.0)
        self.assertEqual(r.val('protocol.run')['status'], 'running')
        res = B.invoke(instance_id=INST, capability_id='panel.press', arguments=dict(input_id='power'), request_id='b_power')
        self.assertTrue(res['result']['accepted'])
        self.assertFalse(r.val('power.enabled'))
        self.assertNotEqual(r.val('protocol.run')['status'], 'running')

    def test_hand_skills_are_physical_with_device_rules(self):
        """Lead clarification 10-19: bottles, plates, waste and tube cleaning are physical acts: another agent is not refused for ownership;
        the washer's own rules answer (OPERATION_RUNNING for a plate move, waste emptying, tube cleaning or the bottle in use during a run)."""
        r = Rig(plate=True)
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.open_protocol('W-Corning_FLAT')
        r.tap('rs_start')                                                       # agent A's run
        r.step(3.0)
        self.assertEqual(r.val('protocol.run')['status'], 'running')
        used = r.h.op['buffer']
        free = next(b for b in rt.BOTTLES if b != used)

        def b(cap, **a):
            return B.invoke(instance_id=INST, capability_id=cap, arguments=a, request_id=f'b_{cap}_{sorted(a.items())}')
        self.assertEqual(b('buffer.load', bottle=free, liquid_id='water', volume_m3=0.001)['status'], 'succeeded')
        self.assertEqual(b('plate.provide', **dict(rt.PLATE_EXAMPLE, plate_id='b_plate'))['status'], 'succeeded')
        for cap, a in (('buffer.load', dict(bottle=used, liquid_id='water', volume_m3=0.001)), ('waste.empty', {}),
                       ('plate.unload', dict(plate_id=r.h.on_carrier)), ('maintenance.clean_tubes', {})):
            self.assertEqual(b(cap, **a)['error']['code'], 'OPERATION_RUNNING', cap)   # the device's own reason, not ACCESS_DENIED
        self.assertEqual(r.val('protocol.run')['status'], 'running')

    def test_fluid_sensor_off_runs_air(self):
        r = Rig()
        r.call(r.A, 'buffer.load', bottle='C', liquid_id='water', volume_m3=0.0)   # W-LONG_SHUTDOWN: air through Buffer C
        r.call(r.A, 'protocol.run', name='PRIME_ALL_BUFFRS')                    # D, then C: refused at C with the fluid sensor on
        r.run_until_idle(5.0)
        self.assertEqual((r.val('protocol.run')['status'], r.val('protocol.run')['error_code']), ('failed', 'BUFFER_EMPTY'))
        r.call(r.A, 'washer.configure', **dict(rt.WASHER_DEFAULT, sensor_fluid=False, sensor_flow=False))
        r.call(r.A, 'protocol.run', name='PRIME_ALL_BUFFRS')
        r.run_until_idle(5.0)
        run = r.val('protocol.run')
        self.assertEqual((run['status'], len(run['records'])), ('completed', 4))
        self.assertEqual(r.call(r.A, 'measurement.result', record_id=run['records'][1])['status'], 'completed')
        self.assertEqual(r.val('buffer.c.volume_m3'), 0.0)


# ---------------------------------------------------------------- rules: refusals, time-outs, power, gestures
class Rules(unittest.TestCase):
    def test_refusals_show_the_instrument_message(self):
        r = Rig()
        r.open_protocol('W-Corning_FLAT')
        self.assertFalse(r.tap('rs_start'))                                     # no plate
        self.assertEqual(r.dialog(), ('Error', 'Error NO_PLATE No plate on the carrier. Put the plate on the carrier (A1 at the left rear).'))
        self.assertIsNone(r.val('protocol.run'))
        r.ov('OK')
        r.load_plate()
        r.h.waste = r.h.plant['waste_capacity_m3']
        r.tap('rs_start')
        self.assertEqual(r.dialog()[1], 'Error WASTE_FULL Waste bottle full. Empty the waste bottles.')
        for code in rt.DEVICE_CAP_ERRORS['protocol.run'] + rt.CAP_ERRORS['wash.run'] + rt.CAP_ERRORS['prime.run']:
            if code not in ('UNKNOWN_PROTOCOL',):
                self.assertIn(code, rt.PANEL_SPEC['errors']['runtime'], code)

    def test_busy_run_screen_and_no_time_out(self):
        r = Rig(plate=True)
        r.call(r.A, 'wash.run', protocol=rt.WASH_EXAMPLE)                       # a run started by a direct command shows the run screen too
        self.assertEqual((r.page(), r.frame()['displays'][SCREEN]['title']), ('run', 'Wash'))
        self.assertNotIn('run_ok', r.widgets())
        r.run_until_idle(0.5)
        self.assertEqual(r.text('run_status'), 'Run complete')
        r.tap('run_ok')
        r.tap('home_inst')
        r.step(6 * 3600.0)
        self.assertEqual(r.page(), 'config')                                    # no screen time-out

    def test_unknown_gestures_are_invalid(self):
        r = Rig()
        for cap, args in (('panel.press', dict(input_id='nope')), ('panel.touch', dict(display_id='nope', x=1, y=1))):
            self.assertEqual(r.call(r.A, cap, expect='rejected', **args)['error']['code'], 'INVALID_ARGUMENT')


# ---------------------------------------------------------------- render, evidence, contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig(plate=True)
            g.call(g.A, 'protocol.run', name='W-Corning_FLAT')
            g.run_until_idle(0.5)
            g.tap('run_ok')
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render(SCREEN), b.h.panel.render(SCREEN)
        self.assertEqual((ia.dtype.name, ia.shape), ('uint8', (480, 640, 3)))
        self.assertTrue((ia == ib).all())
        self.assertEqual(a.h.panel.png_bytes(SCREEN), b.h.panel.png_bytes(SCREEN))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = dict(home=a.h.panel.save_png(SCREEN, OUT / 'home.png'))
        a.tap('home_inst')
        a.tap('cfg_options')
        shots['menu'] = a.h.panel.save_png(SCREEN, OUT / 'menu.png')            # Instrument > Options
        a.home()
        a.define()
        a.row('def_list', 'W-CELLWASH_96')
        a.tap('def_edit')
        shots['define'] = a.h.panel.save_png(SCREEN, OUT / 'define.png')
        a.row('ed_steps', a.widgets()['ed_steps']['cells'][0][0])
        a.tap('ed_edit')
        shots['wash_step'] = a.h.panel.save_png(SCREEN, OUT / 'wash_step.png')
        a.tap('st_wash_cancel')
        a.tap('ed_add')
        shots['add_step'] = a.h.panel.save_png(SCREEN, OUT / 'add_step.png')
        a.tap('add_exit')
        a.tap('ed_info')
        a.ov('Name')
        shots['keyboard'] = a.h.panel.save_png(SCREEN, OUT / 'keyboard.png')
        a.ov('Cancel')
        a.ov('Cancel')
        a.tap('editor_home')
        a.call(a.A, 'plate.unload', plate_id='elisa_plate')
        a.open_protocol('W-Corning_FLAT')
        a.tap('rs_start')
        shots['error'] = a.h.panel.save_png(SCREEN, OUT / 'error.png')
        a.ov('OK')
        a.home()
        a.tap('home_maint')
        a.row('maint_list', 'W-DECONTAMINATE')
        a.tap('maint_start')
        shots['run_prompt'] = a.h.panel.save_png(SCREEN, OUT / 'run_prompt.png')
        for name, s in shots.items():
            data = (OUT / f'{name}.png').read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s['sha256'])
            self.assertEqual((s['width'], s['height']), (640, 480))
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(power=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=SCREEN)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.press('power')
        r2 = g.call(g.A, 'panel.capture', display_id=SCREEN)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id=SCREEN)['image'], r2['image'])

    def test_copied_runtime_gets_its_own_panel(self):
        g = Rig()
        g.tap('home_quick')
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        self.assertEqual(c.panel.frame()['ui']['page'], 'home')                 # a copy starts with a fresh screen
        self.assertEqual(g.page(), 'quick')

    def test_spec_and_contract(self):
        spec = rt.PANEL_SPEC
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS) | set(rt.DEVICE_CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.touch', 'panel.capture', 'panel.frame'})
        self.assertTrue(spec['approximate'])
        for fid, f in spec['forms'].items():
            for fld in f['fields']:
                self.assertTrue(fld.get('evidence') or fld.get('assumption'), (fid, fld['id']))
                for w in pu._writes(fld.get('write')):
                    self.assertIn(w['capability'], rt.ALL_CAPS)
        for scr in ('main', 'run'):
            for pid, p in spec['screens'][scr]['nav']['pages'].items():
                self.assertTrue(p.get('evidence'), pid)
        for did, d in spec['dialogs'].items():
            self.assertTrue(d.get('evidence'), did)
        text = (rt.ASSET_DIR / 'panel_ui.json').read_text()
        self.assertNotIn(str(Path.home()), text)
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        meta = dict(rt.export_device_contract_metadata(), **rt.export_panel_contract_metadata())
        for k, e in meta.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], e[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        m = rt.export_biotek_405ts_contract_metadata()
        for k in rt.CAPS:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
        self.assertEqual(set(c['state_slots']), set(rt.SLOTS))
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/biotek_405ts.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])            # the shared engine is not pinned per asset
        self.assertIsNone(c['artifacts']['panel_engine_tests']['sha256'])
        spec_ = importlib.util.spec_from_file_location('biotek405_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(sorted(set(SURVEY_TASKS) - DONE_TASKS), [])


if __name__ == '__main__':
    unittest.main()
