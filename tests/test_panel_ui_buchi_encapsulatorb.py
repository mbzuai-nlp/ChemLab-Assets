"""front-panel tests for CHEM-052 buchi_encapsulatorb (panel_ui.json + scripts/asset_runtime/_panel_ui.py):
python3 -m pytest tests/test_panel_ui_buchi_encapsulatorb.py -q

Every gesture goes through the session API (panel.switch / panel.touch, the runtime's normal invoke path) and every check looks at the runtime
state and at the screen (the structured frame of panel.frame).  Themes: the key procedures of the menu survey (start-up, vibration 800 Hz,
air pressure 0.2 bar shown in mbar, electrostatic dispersion, heating, amplitude and stroboscope light, store), the (+) / (-) steps and range
ends, Esc keeping the value, the double press of store and the stored values at power-up, refusals (electrode without a grounded bath), the
power gate and the initialisation time-out; panel = direct capability (same runtime state); render determinism and the PNGs of
outputs/panel_ui/buchi_encapsulatorb/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import buchi_encapsulatorb as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'encapsulator_01'
T0 = 273.15
SCR = 'touch'
OUT = ROOT / 'outputs/panel_ui/buchi_encapsulatorb'
SURVEY_TASKS = ['start up', 'set vibration to 800 Hz and start it', 'set air pressure to 0.2 bar', 'set electrostatic dispersion', 'set heating',
                'adjust amplitude and strobe light', 'store settings']
DONE_TASKS = set()


def _effects():
    meta = rt.export_buchi_encapsulatorb_contract_metadata()
    return {k: {e['state_id'] for e in v['state_effects']} for k, v in meta['capabilities'].items()}


EFFECTS = _effects()


class Rig:
    def __init__(self, grounded=True):
        self.h = rt.BuchiEncapsulatorbRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        for name, ex in [('plant', rt.PLANT_EXAMPLE), ('method', rt.METHOD_EXAMPLE), ('sample', rt.SAMPLE_EXAMPLE),
                         ('bath', dict(rt.BATH_EXAMPLE, grounded=grounded)), ('air', rt.GAS_EXAMPLE)]:
            self.call(self.E if name != 'method' else self.A, name + '.configure', **ex)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            rt.validate(rt.CAPS[cap][1], r['result'])
            assert self.h.time_s == t0, cap
            if cap.startswith('panel.'):
                after = self.h.read('evaluator')
                changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
                assert not changed - EFFECTS[cap], (cap, sorted(changed - EFFECTS[cap]))
            return r['result']
        return r

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def widgets(self):
        return {w['id']: w for w in self.frame()['displays'][SCR]['widgets']}

    def text(self, wid):
        return self.widgets()[wid].get('text')

    def touch(self, x, y):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y))['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def page(self):
        return self.frame()['ui']['page']

    def step(self, s):
        self.h.step(s)

    def on(self):
        assert self.switch('on')
        self.step(3.5)
        assert (self.frame()['screen'], self.page()) == ('gui', 'start')


class KeyProcedures(unittest.TestCase):
    def test_start_up(self):
        g = Rig()
        self.assertEqual(g.frame()['screen'], 'off')
        self.assertFalse(g.touch(160, 120))                                          # dark screen: touches do nothing
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.frame()['screen'], 'startup')
        self.assertIn('initialization', g.text('init_text'))
        self.assertFalse(g.touch(160, 120))
        g.step(3.5)
        self.assertEqual((g.frame()['screen'], g.page()), ('gui', 'start'))
        w = g.widgets()
        self.assertEqual((w['st_freq_value']['text'], w['st_elect_value']['text'], w['st_heat_value']['text']), ('off', 'off', 'off'))
        self.assertEqual(w['st_p_value']['text'], '0')
        DONE_TASKS.add('start up')

    def test_set_vibration_to_800_hz_and_start_it(self):
        g = Rig()
        g.on()
        g.tap('st_freq_set')
        self.assertEqual(g.page(), 'freq')
        g.tap('freq_plus_0')                                                          # 800 -> 1800
        g.tap('freq_minus_0')
        g.tap('freq_plus_1')                                                          # 900
        g.tap('freq_minus_1')                                                         # 800
        self.assertEqual(g.h.freq_target, 800.0)
        self.assertEqual(g.text('freq_set'), '800')
        self.assertEqual(g.text('freq_status'), 'off')
        g.tap('freq_onoff')
        g.step(0.1)
        self.assertTrue(g.h.vibration)
        self.assertEqual((g.text('freq_status'), g.text('freq_cur')), ('on', '800'))
        self.assertTrue(g.widgets()['freq_onoff']['selected'])
        g.tap('freq_esc')
        self.assertEqual((g.page(), g.text('st_freq_value')), ('start', '800'))
        DONE_TASKS.add('set vibration to 800 Hz and start it')

    def test_set_air_pressure_to_0_2_bar(self):
        g = Rig()
        g.on()
        g.call(g.A, 'air.set_pressure', gauge_pa=20000.0)                            # the regulating valve knob (mechanical, om p.34)
        g.step(10.0)
        self.assertAlmostEqual(g.h.pressure, 20000.0, delta=300.0)
        self.assertEqual(g.text('st_p_value'), str(round(g.h.pressure / 100.0)))
        self.assertIn(g.text('st_p_value'), ('198', '199', '200', '201'))
        DONE_TASKS.add('set air pressure to 0.2 bar')

    def test_set_electrostatic_dispersion(self):
        g = Rig()
        g.on()
        g.tap('st_elect_set')
        g.tap('elect_plus_0')                                                         # 1000 -> 1500
        g.tap('elect_minus_2')                                                        # 1490
        g.tap('elect_plus_2')
        self.assertEqual(g.h.voltage_target, 1500.0)
        for _ in range(3):
            g.tap('elect_plus_0')                                                     # stops at 2500
        self.assertEqual(g.h.voltage_target, 2500.0)
        g.tap('elect_minus_0')
        g.tap('elect_minus_0')
        self.assertEqual(g.h.voltage_target, 1500.0)
        g.tap('elect_onoff')
        g.step(0.1)
        self.assertEqual((g.h.hv, g.text('elect_cur')), (True, '1500'))
        g.tap('elect_esc')
        self.assertEqual(g.text('st_elect_value'), '1500')
        DONE_TASKS.add('set electrostatic dispersion')

    def test_set_heating(self):
        g = Rig()
        g.on()
        g.tap('st_heat_set')
        g.tap('heat_plus_1')                                                          # 40.0 -> 41.0
        for _ in range(5):
            g.tap('heat_plus_2')                                                      # 41.5
        self.assertAlmostEqual(g.h.t_target, T0 + 41.5, places=6)
        self.assertEqual(g.text('heat_set'), '41.5')
        g.tap('heat_onoff')
        g.step(60.0)
        self.assertTrue(g.h.heating)
        self.assertEqual(g.text('heat_status'), 'on')
        self.assertGreater(float(g.text('heat_cur')), 25.0)
        for _ in range(6):
            g.tap('heat_plus_0')                                                      # stops at 80 degC
        self.assertAlmostEqual(g.h.t_target, T0 + 80.0, places=6)
        g.tap('heat_esc')
        self.assertEqual(g.text('st_heat_value'), '80.0')
        DONE_TASKS.add('set heating')

    def test_adjust_amplitude_and_strobe_light(self):
        g = Rig()
        g.on()
        g.tap('st_more')
        self.assertEqual((g.page(), g.text('more_amp'), g.text('more_light')), ('more', '3', '5'))
        for _ in range(8):
            g.tap('more_amp_plus')                                                    # 1-9 at 800 Hz
        self.assertEqual(g.h.amplitude, 9)
        self.assertEqual(g.text('more_amp_note'), 'amplitude 1-9')
        g.tap('more_light_minus')
        self.assertEqual(g.h.strobe, 4)
        g.tap('more_esc')
        g.tap('st_freq_set')
        g.tap('freq_plus_0')                                                          # 1800 Hz: amplitude up to 12
        g.tap('freq_esc')
        g.tap('st_more')
        for _ in range(4):
            g.tap('more_amp_plus')
        self.assertEqual(g.h.amplitude, 12)
        for _ in range(12):
            g.tap('more_light_minus')
        self.assertEqual(g.h.strobe, 1)
        g.tap('more_esc')
        g.tap('st_freq_set')
        g.tap('freq_minus_0')                                                         # back to 800 Hz: amplitude 9
        self.assertEqual(g.h.amplitude, 9)
        g.tap('freq_onoff')
        g.step(0.1)
        self.assertEqual((g.h.read()['strobe.flash_hz'], g.h.read()['vibration.drive_level']), (800.0, 9.0))
        DONE_TASKS.add('adjust amplitude and strobe light')

    def test_store_settings(self):
        g = Rig()
        g.on()
        g.tap('st_freq_set')
        g.tap('freq_plus_2')                                                          # 810 Hz
        g.tap('freq_esc')
        g.tap('st_store')                                                             # one touch: nothing stored
        g.step(1.5)
        g.tap('st_store')
        self.assertEqual(g.h.stored['frequency_hz'], 800.0)
        self.assertEqual(g.frame()['ui']['beeps'], 0)
        g.step(0.5)
        g.tap('st_store')                                                             # second touch within one second: stored, beep
        self.assertEqual(g.h.stored['frequency_hz'], 810.0)
        self.assertEqual(g.frame()['ui']['beeps'], 1)
        g.tap('st_freq_set')
        g.tap('freq_plus_2')                                                          # 820, not stored
        g.switch('off')
        self.assertEqual(g.frame()['screen'], 'off')
        g.on()
        self.assertEqual(g.h.freq_target, 810.0)                                     # power-up loads the stored values
        DONE_TASKS.add('store settings')


class RefusalsAndRules(unittest.TestCase):
    def test_electrode_refused_without_ground_and_no_timeouts(self):
        g = Rig(grounded=False)
        g.on()
        g.tap('st_elect_set')
        self.assertFalse(g.tap('elect_onoff'))
        self.assertFalse(g.h.hv)
        self.assertEqual(g.frame()['ui']['message']['text'], 'not possible')
        self.assertEqual(g.text('msg'), 'not possible')                                # 1.1.2: the refusal is drawn on the screen for 2 s
        g.step(600.0)
        self.assertNotIn('msg', g.widgets())
        self.assertEqual(g.page(), 'elect')                                           # no menu time-out
        g.tap('elect_esc')
        self.assertEqual(g.text('st_elect_value'), 'off')

    def test_power_gate_and_off_keeps_manual_flow(self):
        g = Rig()
        g.on()
        g.call(g.A, 'valve.set_opening', fraction=1.0)
        g.call(g.A, 'air.set_pressure', gauge_pa=40000.0)
        g.tap('st_freq_set')
        g.tap('freq_onoff')
        g.step(2.0)
        g.switch('off')
        v = g.h.emitted
        self.assertFalse(g.touch(160, 120))
        g.step(1.0)
        self.assertGreater(g.h.emitted, v)                                           # the mechanical valves do not close
        self.assertEqual(g.h.frequency, 0.0)


class CallerRules(unittest.TestCase):
    """Lead rule 10-19 (1.1.2): a run started through an agent's session belongs to it; another agent cannot stop, switch off or change it on the touch
    screen or directly (state unchanged, a short text on the screen); the owner, the evaluator, the mains switch and the mechanical knobs are free."""

    def test_another_agents_run_cannot_be_stopped_or_changed(self):
        g = Rig()
        g.on()
        g.tap('st_freq_set')
        g.tap('freq_onoff')                                                            # vibration on (agent A)
        op = g.call(g.A, 'acquisition.start')['operation_id']
        self.assertEqual((g.h._owners[op], g.A.poll(op)['status']), ('agent', 'running'))
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        before = g.h.debug_snapshot()
        x, y, w, h = g.widgets()['freq_onoff']['rect']
        self.assertFalse(g.call(B, 'panel.touch', display_id=SCR, x=x + w // 2, y=y + h // 2)['accepted'])   # B's on / off key: refused
        self.assertEqual(g.text('msg'), 'not permitted')
        for cap, args in (('acquisition.stop', {}), ('vibration.set_frequency', dict(frequency_hz=900.0)),
                          ('thermal.toggle', {}), ('electrostatic.set_voltage', dict(voltage_v=1500.0))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        after = g.h.debug_snapshot()
        for k in ('phase', 'power', 'vibration', 'freq_target', 'heating', 'hv', 'voltage_target', 'operation_id'):
            self.assertEqual(before[k], after[k], k)
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.call(B, 'valve.set_opening', fraction=0.5)                                   # the pinch valve is mechanical: free
        g.call(B, 'air.set_pressure', gauge_pa=20000.0)
        g.step(2.5)
        self.assertNotIn('msg', g.widgets())
        self.assertEqual(g.A.cancel(op, 'c1')['status'], 'cancelled')                 # the owner may stop its own run
        self.assertTrue(g.call(B, 'panel.switch', input_id='power', position='off')['accepted'])   # the mains switch is physical: never refused
        self.assertFalse(g.h.power)


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_reach_the_same_state(self):
        a, b = Rig(), Rig()
        a.on()
        a.tap('st_freq_set')
        a.tap('freq_plus_1')
        a.tap('freq_onoff')
        a.tap('freq_esc')
        a.tap('st_heat_set')
        a.tap('heat_minus_1')
        a.tap('heat_esc')
        a.tap('st_more')
        a.tap('more_light_plus')
        b.call(b.A, 'power.set', enabled=True)
        b.step(3.5)
        b.call(b.A, 'vibration.set_frequency', frequency_hz=900.0)
        b.call(b.A, 'vibration.toggle')
        b.call(b.A, 'thermal.set_temperature', temperature_k=T0 + 39.0)
        b.call(b.A, 'strobe.set_intensity', level=6)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))
        a.step(20.0)
        b.step(20.0)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.on()
            g.call(g.A, 'air.set_pressure', gauge_pa=20000.0)
            g.tap('st_freq_set')
            g.tap('freq_onoff')
            g.tap('freq_esc')
            g.step(10.0)
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render(SCR), b.h.panel.render(SCR)
        self.assertEqual(ia.dtype.name, 'uint8')
        self.assertTrue((ia == ib).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png(SCR, OUT / 'home.png')}
        a.tap('st_freq_set')
        shots['menu'] = a.h.panel.save_png(SCR, OUT / 'menu.png')
        a.tap('freq_esc')
        a.tap('st_more')
        shots['more'] = a.h.panel.save_png(SCR, OUT / 'more.png')
        c = Rig(grounded=False)
        c.on()
        c.tap('st_elect_set')
        c.tap('elect_onoff')
        shots['error'] = c.h.panel.save_png(SCR, OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')
        self.assertEqual(c.h.panel.render(SCR).shape, (240, 320, 3))

    def test_capture_and_copy(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.on()
        r2 = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        m = rt.export_buchi_encapsulatorb_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + ['vibration.set_amplitude', 'strobe.set_intensity', 'settings.store']:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/buchi_encapsulatorb.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('enc_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(DONE_TASKS, set(SURVEY_TASKS))


if __name__ == '__main__':
    unittest.main()
