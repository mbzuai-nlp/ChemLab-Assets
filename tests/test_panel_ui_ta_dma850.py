"""front-panel tests for CHEM-133 ta_dma850 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/ta_dma850.py):
python3 -m pytest tests/test_panel_ui_ta_dma850.py -q

Every gesture goes through the session API (panel.press / panel.switch / panel.touch); widgets are found by id in the structured frame
(panel.frame, evaluator) and tapped at their centre.  Hands-on steps the touch screen does not do (TRIOS clamp calibration, mounting the sample,
the clamping screws, the manual furnace) are direct capability calls.  Themes: the five key procedures of the menu survey; motor modes, set
temperature and clamp position; panel = direct capability (same runtime state); refusals shown as the device's dialog with the state unchanged;
power gate; no time-outs; signals, settings, cooling accessory, touch calibration and the unsupported items; render determinism, capture
files and the evidence PNGs; contract entries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import ta_dma850 as rt  # noqa: E402

SCREEN = 'touchscreen'
OUT = ROOT / 'outputs/panel_ui/ta_dma850'
CONTRACT = rt.ASSET / 'asset.contract.json'
SURVEY_TASKS = ['power on', 'mount and measure a sample', 'remove the sample', 'shut down', 're-tighten clamps at low temperature']
DONE_TASKS = set()


def sample(length_m=0.015, engineering=False):
    s = copy.deepcopy(rt.ENGINEERING_SAMPLE_EXAMPLE if engineering else rt.SAMPLE_EXAMPLE)
    s['length_m'] = length_m
    return s


class Rig:
    """One DMA 850 with an agent and an evaluator session; panel gestures only through panel.* capabilities."""

    def __init__(self, power=True, cooler='none', plant=None):
        self.h = rt.TaDma850Runtime('dma_panel')
        self.A, self.E = self.h.session('agent'), self.h.session('evaluator')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, cooler=cooler, controller_gain_w_k=200.0, **(plant or {})))   # plant: evaluator sensor errors (default 0)
        for g in ('air', 'N2'):
            self.call(self.E, 'gas.load', **dict(rt.GAS_EXAMPLE, gas=g, amount_mol=100.0))
        if cooler == 'NPC':
            self.call(self.E, 'coolant.load', **dict(rt.COOLANT_EXAMPLE, amount_kg=2.0))
        if power:
            assert self.press('power_button')

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=self.h.instance_id, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        while dt > 1e-9:
            d = min(600.0, dt)
            self.n += 1
            self.E.invoke(instance_id=self.h.instance_id, capability_id='sim.step', arguments={'dt_s': d}, request_id=f'q{self.n}')
            dt -= d

    def wait(self, pred, limit_s=3600.0, dt=0.5):
        t = 0.0
        while not pred():
            assert t < limit_s, 'condition not reached'
            self.step(dt)
            t += dt

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
        return f['screen'], f['ui']['page']

    def press(self, input_id, **kw):
        return self.call(self.A, 'panel.press', input_id=input_id, **kw)['accepted']

    def switch(self, position):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=position)['accepted']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCREEN, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in self.overlay()["buttons"]]}')

    def cells(self, wid):
        return {c[0]: c[1] for c in self.widgets()[wid]['cells']}

    def row(self, wid, label):
        w = self.widgets()[wid]
        k = [c[0] for c in w['cells']].index(label)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def enter(self, wid, label, text):
        """Tap a number row, type on the numeric keyboard (the shown value is replaced) and OK."""
        assert self.row(wid, label)
        assert self.overlay()['kind'] == 'keyboard'
        for ch in text:
            self.ov(ch)
        return self.ov('OK')

    def dialog(self):
        ov = self.overlay()
        return (ov['dialog'], ov['lines']) if ov and ov['kind'] == 'dialog' else None

    def home(self):
        assert self.tap('ft_home')

    def state(self):
        """Runtime state without the request bookkeeping (gestures count as requests)."""
        return json.dumps({k: v for k, v in self.h.__dict__.items() if k not in ('revision', '_requests')}, sort_keys=True, default=str)


def mount(r, s, preload=True):
    """Unlocked drive to the sample's length, clamp calibration (TRIOS), sample in, clamping screws, Measure (initial force)."""
    assert r.tap('mm_unlocked')
    assert r.tap('b_position')
    assert r.enter('f_position', 'Position', f'{(s["length_m"] - 0.010) * 1000:.2f}')
    r.wait(lambda: not r.val('drive.moving'))
    r.home()
    r.call(r.A, 'sensor.calibrate', **rt.CALIBRATION_EXAMPLE)
    r.call(r.A, 'sensor.zero')
    r.call(r.E, 'sample.load', **s)
    r.call(r.A, 'clamp.set', locked=True)
    r.wait(lambda: not r.val('clamp.moving'))
    if preload:
        assert r.tap('b_measure')
        r.wait(lambda: r.val('drive.mode') == 'hold')


def close_furnace(r, opened=False):
    r.call(r.A, 'furnace.set', opened=opened)
    r.wait(lambda: not r.val('furnace.moving'))


class TestKeyProcedures(unittest.TestCase):
    def test_power_on(self):
        r = Rig(power=False)
        self.assertEqual(r.page(), ('dark', None))
        self.assertFalse(r.tap('hd_bar') if 'hd_bar' in r.widgets() else r.touch(400, 240))   # the dark screen ignores touches
        self.assertTrue(r.press('power_button'))                                # round button on the right side
        self.assertTrue(r.val('power.enabled'))
        self.assertEqual(r.page(), ('gui', 'home'))
        self.assertEqual(r.text('hd_status'), 'Status: Idle')
        self.assertEqual(r.text('rd_temperature'), '25.00 °C')
        self.assertTrue(r.widgets()['mm_lock']['selected'])                     # air bearing off: drive locked
        DONE_TASKS.add('power on')

    def test_mount_and_measure_a_sample(self):
        r = Rig()
        s = sample(0.015)
        assert r.tap('mm_unlocked')
        self.assertTrue(r.val('gas.opened'))
        self.assertTrue(r.widgets()['mm_unlocked']['selected'])
        assert r.tap('b_position')
        self.assertEqual(r.page(), ('gui', 'position'))
        self.assertTrue(r.enter('f_position', 'Position', '5'))
        self.assertAlmostEqual(r.val('drive.target_m'), 0.005)
        r.wait(lambda: not r.val('drive.moving'))
        self.assertEqual(r.cells('f_position')['Actual position'], '5.000 mm')
        r.home()
        self.assertEqual(r.text('rd_position'), '5.00 mm')
        r.call(r.A, 'sensor.calibrate', **rt.CALIBRATION_EXAMPLE)              # TRIOS clamp calibration, then the sample by hand
        r.call(r.A, 'sensor.zero')
        r.call(r.E, 'sample.load', **s)
        r.call(r.A, 'clamp.set', locked=True)
        r.wait(lambda: not r.val('clamp.moving'))
        self.assertTrue(r.tap('b_measure'))                                     # motor on with the initial force
        self.assertAlmostEqual(r.val('drive.preload_n'), 0.05)
        self.assertEqual(r.text('rd_measure'), 'Active')
        r.wait(lambda: r.val('drive.mode') == 'hold')
        self.assertEqual(r.text('rd_measure'), f"L {(0.010 + r.val('drive.displacement_m')) * 1000:.3f} mm")   # length under the initial force
        self.assertTrue(15.0 < (0.010 + r.val('drive.displacement_m')) * 1000 < 15.5)
        self.assertTrue(r.widgets()['mm_preload']['selected'])
        close_furnace(r)
        self.assertTrue(r.tap('ft_start'))
        self.assertEqual(r.val('acquisition.phase'), 'running')
        self.assertEqual(r.text('hd_status'), 'Status: Running')
        assert r.tap('ft_signals')
        self.assertTrue(r.cells('f_signals')['Step'].startswith('1 '))
        self.assertEqual(r.cells('f_signals')['Frequency'], '1.000 Hz')
        r.wait(lambda: r.val('acquisition.phase') != 'running', 120)
        self.assertEqual(r.text('hd_status'), 'Status: Complete')
        res = r.call(r.A, 'acquisition.result')
        a = res['blocks'][0]['analysis']
        self.assertEqual(r.cells('f_signals')['Storage modulus'], f'{a["storage_modulus_pa"] * 1e-6:.2f} MPa')
        self.assertEqual(r.cells('f_signals')['Tan delta'], f'{a["tan_delta"]:.4f}')
        DONE_TASKS.add('mount and measure a sample')

    def test_remove_the_sample(self):
        r = Rig()
        mount(r, sample(0.015))
        close_furnace(r)
        assert r.tap('ft_start')
        r.wait(lambda: r.val('acquisition.phase') != 'running', 120)
        close_furnace(r, opened=True)                                            # room temperature: unlock and open the furnace
        self.assertTrue(r.tap('mm_float'))                                      # zero force (static force released)
        self.assertEqual(r.val('drive.mode'), 'release')
        r.wait(lambda: r.val('drive.mode') == 'hold' and abs(r.val('truth.force_n')) < 1e-4 and abs(r.val('truth.strain')) <= 1e-6, 600)
        self.assertTrue(r.widgets()['mm_float']['selected'])
        self.assertTrue(r.tap('mm_lock'))                                       # Lock before loosening the clamping screws
        self.assertFalse(r.val('gas.opened'))
        self.assertTrue(r.widgets()['mm_lock']['selected'])
        r.call(r.A, 'clamp.set', locked=False)
        r.wait(lambda: not r.val('clamp.moving'))
        r.call(r.E, 'sample.remove')
        self.assertFalse(r.val('sample.present'))
        DONE_TASKS.add('remove the sample')

    def test_shut_down(self):
        r = Rig()
        assert r.tap('mm_unlocked')
        assert r.tap('mm_lock')                                                  # Lock before removing the clamp
        self.assertTrue(r.press('power_button'))                                # shutdown sequence
        self.assertFalse(r.val('power.enabled'))
        self.assertEqual(r.page(), ('dark', None))
        img = r.h.panel.render(SCREEN)
        self.assertEqual(int(img.max()), 0)                                     # dark screen
        self.assertTrue(r.switch('0'))                                          # Power Control Box switch OFF (0)
        self.assertFalse(r.val('power.enabled'))
        self.assertTrue(r.switch('I'))                                          # and on again: the screen restarts on home
        self.assertTrue(r.val('power.enabled'))
        self.assertEqual(r.page(), ('gui', 'home'))
        DONE_TASKS.add('shut down')

    def test_re_tighten_clamps_at_low_temperature(self):
        r = Rig(cooler='NPC')
        s = sample(0.015, engineering=True)
        mount(r, s, preload=False)                                               # the clamp is re-tightened without load
        close_furnace(r)
        assert r.tap('b_temperature')
        self.assertTrue(r.enter('f_temperature', 'Rate', '10'))
        self.assertTrue(r.enter('f_temperature', 'Setpoint', '-50'))
        self.assertAlmostEqual(r.val('thermal.final_target_k'), 223.15)
        self.assertEqual(r.cells('f_temperature')['Temperature control'], 'On')
        r.wait(lambda: abs(r.val('sample.indicated_k') - 223.15) < 0.5, 3600, 5.0)   # wait until the sample is at the set temperature
        self.assertTrue(r.tap('t_off'))                                         # heaters off before the furnace is opened
        close_furnace(r, opened=True)
        r.call(r.A, 'clamp.set', locked=True)                                   # re-tighten (the clamp stays locked)
        close_furnace(r)
        self.assertTrue(r.enter('f_temperature', 'Setpoint', '-50'))
        r.wait(lambda: abs(r.val('sample.indicated_k') - 223.15) < 0.3, 3600, 1.0)
        m = copy.deepcopy(rt.METHOD_EXAMPLE)
        m['blocks'][0].update(temperature_k=223.15)
        r.call(r.A, 'method.configure', **m)                                    # procedure from TRIOS
        r.home()
        self.assertTrue(r.tap('b_measure'))
        r.wait(lambda: r.val('drive.mode') == 'hold')
        self.assertTrue(r.tap('ft_start'))
        r.wait(lambda: r.val('acquisition.phase') != 'running', 600)
        self.assertEqual(r.val('acquisition.phase'), 'succeeded')
        DONE_TASKS.add('re-tighten clamps at low temperature')


class TestFunctions(unittest.TestCase):
    def test_motor_modes(self):
        r = Rig()
        self.assertTrue(r.tap('mm_float'))
        self.assertTrue(r.val('gas.opened'))
        self.assertTrue(r.widgets()['mm_float']['selected'])
        self.assertTrue(r.tap('mm_unlocked'))
        self.assertTrue(r.widgets()['mm_unlocked']['selected'])
        self.assertFalse(r.widgets()['mm_float']['selected'])
        self.assertTrue(r.tap('mm_lock'))
        self.assertFalse(r.val('gas.opened'))
        mount(r, sample(0.010), preload=False)
        assert r.tap('b_position')
        self.assertTrue(r.enter('f_position', 'Preload force', '0.02'))         # Preload uses the stored static force
        r.home()
        self.assertTrue(r.tap('mm_preload'))
        self.assertAlmostEqual(r.val('drive.preload_n'), 0.02)
        r.wait(lambda: r.val('drive.mode') == 'hold')
        self.assertTrue(r.widgets()['mm_preload']['selected'])

    def test_set_temperature_and_rate(self):
        r = Rig()
        assert r.tap('mm_unlocked')
        close_furnace(r)
        assert r.tap('b_temperature')
        self.assertEqual(r.cells('f_temperature')['Setpoint'], '25.0 °C')
        self.assertTrue(r.enter('f_temperature', 'Rate', '20'))
        self.assertTrue(r.enter('f_temperature', 'Setpoint', '60'))
        self.assertAlmostEqual(r.val('thermal.final_target_k'), 333.15)
        self.assertAlmostEqual(r.h.rate, 20 / 60)
        r.step(30)
        self.assertGreater(r.val('furnace.indicated_k'), 300.0)
        self.assertTrue(r.tap('t_off'))
        self.assertFalse(r.val('thermal.enabled'))

    def test_signals_settings_cooling_touch_calibration(self):
        r = Rig(cooler='NPC')
        assert r.tap('ft_signals')
        self.assertEqual(r.cells('f_signals')['Temperature'], '25.00 °C')
        self.assertEqual(r.cells('f_signals')['Step'], '---')
        assert r.tap('ft_settings')
        c = r.cells('f_settings')
        self.assertEqual((c['Serial Number'], c['IP Address']), ('dma_panel', 'Not connected'))
        assert r.tap('s_cooling')
        self.assertEqual(r.cells('f_cooling')['Cooling accessory'], 'NPC (liquid nitrogen)')
        assert r.tap('c_back')
        assert r.tap('s_touch')
        self.assertEqual(r.page(), ('gui', 'touch_cal'))
        self.assertFalse(r.touch(400, 420))                                      # outside the target: nothing
        for i in range(3):
            self.assertTrue(r.tap(f'tc_target{i}'))
        self.assertEqual(r.dialog(), ('tcal_done', ['Calibration complete.']))
        r.ov('OK')
        self.assertEqual(r.page(), ('gui', 'settings'))

    def test_unsupported_items_show_the_device_response(self):
        r = Rig()
        assert r.tap('ft_settings')
        self.assertFalse(r.tap('s_network'))
        self.assertEqual(r.dialog(), ('no_network', ['No network connection.']))
        self.assertTrue(r.frame()['ui']['unsupported'])
        r.ov('OK')
        self.assertFalse(r.tap('s_service'))
        self.assertEqual(r.dialog()[0], 'service_only')


class TestRulesAndRefusals(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        assert a.tap('mm_unlocked')
        b.call(b.A, 'gas.set_open', opened=True)
        assert a.tap('b_position')
        assert a.enter('f_position', 'Position', '3.5')
        b.call(b.A, 'drive.move', position_m=0.0035)
        for x in (a, b):
            x.step(5)
        close_furnace(a)
        close_furnace(b)
        assert a.tap('ft_home')
        assert a.tap('b_temperature')
        assert a.enter('f_temperature', 'Setpoint', '40')
        b.call(b.A, 'thermal.set_temperature', temperature_k=313.15, rate_k_s=10 / 60)
        for x in (a, b):
            x.step(20)
        assert a.tap('t_off')
        b.call(b.A, 'thermal.disable')
        self.assertEqual(a.h.read(), b.h.read())
        self.assertEqual(a.call(a.E, 'ledger.read'), b.call(b.E, 'ledger.read'))

    def test_refusals_show_the_device_dialog_and_change_nothing(self):
        r = Rig()
        before = r.state()
        self.assertFalse(r.tap('ft_start'))                                     # no sample, furnace open, air off
        self.assertEqual(r.dialog(), ('err_not_ready', ['Not ready: check sample, clamp, furnace and air.']))
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'NOT_READY')
        self.assertEqual(r.state(), before)
        r.ov('OK')
        mount(r, sample(0.015))
        assert r.tap('b_position')
        before = r.state()
        self.assertFalse(r.enter('f_position', 'Position', '2'))                # a sample is clamped: positioning refused
        self.assertEqual(r.dialog()[0], 'err_empty_drive_only')
        self.assertEqual(r.state(), before)
        r.ov('OK')
        r.home()
        close_furnace(r)
        assert r.tap('ft_start')
        before = r.state()
        self.assertFalse(r.tap('mm_lock'))                                      # not during an experiment
        self.assertEqual(r.dialog(), ('err_busy', ['Not available while an experiment is running.']))
        r.ov('OK')
        self.assertFalse(r.press('power_button'))                               # no shutdown during a run
        self.assertTrue(r.val('power.enabled'))
        self.assertEqual(r.state(), before)
        r.ov('OK')
        self.assertTrue(r.tap('ft_stop'))
        self.assertEqual(r.val('acquisition.phase'), 'cancelled')
        self.assertEqual(r.text('hd_status'), 'Status: Stopped')
        self.assertFalse(r.tap('ft_stop'))                                      # nothing running: ignored, no dialog
        self.assertIsNone(r.dialog())

    def test_another_operators_run_is_refused_on_the_panel(self):
        """A run belongs to the operator who pressed Start: a second agent's Stop on the touch screen is refused like its direct call (state
        unchanged, the device's dialog); the starter polls and cancels its own panel-started run."""
        r = Rig()
        r.h.register_principal('agent_b')
        b = r.h.session('agent_b')
        mount(r, sample(0.015))
        close_furnace(r)
        assert r.tap('ft_start')
        op = r.h.operation_id
        r.step(1)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        before = r.state()
        self.assertEqual(r.call(b, 'acquisition.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        x, y, w, h = r.widgets()['ft_stop']['rect']
        self.assertFalse(r.call(b, 'panel.touch', display_id=SCREEN, x=x + w // 2, y=y + h // 2)['accepted'])
        self.assertEqual(r.dialog(), ('err_access_denied', ['The running experiment belongs to another operator.']))
        r.ov('OK')
        self.assertEqual(r.state(), before)
        self.assertEqual(r.A.cancel(op, 'cancel')['status'], 'succeeded')
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_power_switch_is_a_hard_switch(self):
        """Final sync 2026-10-19: the power switch cuts the power like a plug; another agent's switch-off is not refused, the running experiment
        ends as on a power loss (failed, POWER_LOSS) and the screen goes dark."""
        r = Rig()
        r.h.register_principal('agent_b')
        b = r.h.session('agent_b')
        mount(r, sample(0.015))
        close_furnace(r)
        assert r.tap('ft_start')
        op = r.h.operation_id
        r.step(1)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        self.assertTrue(r.call(b, 'panel.switch', input_id='power_switch', position='0')['accepted'])
        self.assertFalse(r.h.power)
        self.assertEqual(r.A.poll(op)['status'], 'failed')
        self.assertEqual(r.A.poll(op)['result']['error_code'], 'POWER_LOSS')
        self.assertEqual(r.page(), ('dark', None))

    def test_position_reads_and_holds_the_encoder(self):
        """Actual position, clamp gap and the Position set point are the device's encoder reading: with a displacement gain error the drive
        stops where the reading shows the requested position (the true shaft position differs); default: unchanged."""
        r = Rig(plant=dict(displacement_gain=1.1))
        assert r.tap('mm_unlocked')
        assert r.tap('b_position')
        assert r.enter('f_position', 'Position', '5')
        r.wait(lambda: not r.val('drive.moving'))
        r.step(0.5)
        c = r.cells('f_position')
        self.assertEqual((c['Actual position'], c['Clamp gap']), ('5.000 mm', '15.000 mm'))
        self.assertAlmostEqual(r.val('drive.displacement_m'), 0.005 / 1.1, places=12)

    def test_keypad_range_and_cancel(self):
        r = Rig()
        assert r.tap('b_temperature')
        before = r.state()
        assert r.row('f_temperature', 'Setpoint')
        for ch in '700':
            r.ov(ch)
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.ov('Cancel')
        self.assertIsNone(r.overlay())
        self.assertEqual(r.state(), before)

    def test_power_gate_and_no_timeouts(self):
        r = Rig()
        assert r.tap('b_temperature')
        r.step(1800)
        self.assertEqual(r.page(), ('gui', 'temperature'))                       # no screen time-out
        r.call(r.A, 'power.set', enabled=False)
        self.assertEqual(r.page(), ('dark', None))
        before = r.state()
        self.assertFalse(r.touch(100, 100))
        self.assertEqual(r.state(), before)
        r.call(r.A, 'power.set', enabled=True)
        self.assertEqual(r.page(), ('gui', 'home'))                              # power cycle: home page

    def test_render_determinism_and_capture(self):
        a, b = Rig(), Rig()
        for x in (a, b):
            assert x.tap('mm_unlocked')
            assert x.tap('ft_signals')
        self.assertEqual(a.h.panel.png_bytes(SCREEN), b.h.panel.png_bytes(SCREEN))
        cap = a.call(a.A, 'panel.capture', display_id=SCREEN)
        self.assertEqual((cap['image']['width'], cap['image']['height']), (800, 480))
        self.assertEqual(hashlib.sha256(Path(cap['image']['path']).read_bytes()).hexdigest(), cap['image']['sha256'])
        r = a.call(a.A, 'panel.frame', expect='rejected')                       # evaluator only
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = a.A.describe(instance_id=a.h.instance_id)
        self.assertIn('panel.touch', d['capabilities'])
        self.assertNotIn('panel.frame', d['capabilities'])
        self.assertEqual(d['asset_version'], rt.ASSET_VERSION)

    def test_evidence_renders(self):
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        mount(r, sample(0.015))
        close_furnace(r)
        shots = {'home': r.h.panel.save_png(SCREEN, OUT / 'home.png')}
        assert r.tap('ft_settings')
        shots['menu'] = r.h.panel.save_png(SCREEN, OUT / 'menu.png')
        assert r.tap('ft_home')
        assert r.tap('b_position')
        assert not r.enter('f_position', 'Position', '2')
        shots['error'] = r.h.panel.save_png(SCREEN, OUT / 'error.png')
        r.ov('OK')
        assert r.tap('ft_home')
        assert r.tap('ft_start')
        r.step(4)
        assert r.tap('ft_signals')
        shots['signals'] = r.h.panel.save_png(SCREEN, OUT / 'signals.png')
        assert r.tap('ft_home')
        assert r.tap('b_temperature')
        assert r.row('f_temperature', 'Setpoint')
        shots['keyboard'] = r.h.panel.save_png(SCREEN, OUT / 'keyboard.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_ta_dma850_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        spec = rt.PANEL_SPEC
        for k, v in list(spec['inputs'].items()) + list(spec['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        self.assertEqual((c['components']['power_button']['geometry_status'], c['components']['power_button']['geometry']), ('absent', []))
        self.assertIn('panel.touch', c['components']['touchscreen']['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version'], ext['approximate']), ('panel_ui', 'touch_gui', '0.1', True))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])            # the shared engine is not pinned per asset
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=True), [])


if __name__ == '__main__':
    unittest.main()
