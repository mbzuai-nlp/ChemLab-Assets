"""front-panel tests for CHEM-177 azenta_intellixcap_s1 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_automation.py):
python3 -m pytest tests/test_panel_ui_azenta_intellixcap_s1_46_0001.py -q

Every gesture goes through the session API (panel.press on the Power / Selection button and on the tube-trigger disc).  Checks look at the runtime
state and at the six LEDs (frame and rendered pixels).  Themes: the three key procedures of the menu survey (select Decap Only, decap / recap a tube,
change the cycle time), the other modes (Hold Cap keeps the cap and recaps with it, Release Cap alternates, Cap Only, delayed release), panel = direct
capability, refusals (switched off, busy), render determinism and the PNGs, contract entries."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_automation as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'ixs_panel_01'
OUT = ROOT / 'outputs/panel_ui/azenta_intellixcap_s1_46_0001'
ASSET = rt.INTELLIXCAP_S1_ASSET_DIR
SPEC = rt._ixs_panel()['spec']
LEDS = list(SPEC['displays'])
SURVEY_TASKS = ['Select Decap Only mode', 'Decap or recap a tube', 'Change the cycle time']
DONE_TASKS = set()
MOTION = dict(open_gap_m=0.045, closed_gap_m=0.010, rotation_delta_rad=6.0, initial_rotation_rad=0.0, clamp_duration_s=0.3, rotation_duration_s=1.0,
              release_duration_s=0.3, evidence='synthetic motion for the panel tests')


class Rig:
    def __init__(self, on=True):
        self.h = rt.IntelliXcapS1Runtime()
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.h.host_register_consumable('tube15', cap_diameter_m=0.02, compatibility_evidence='synthetic 15 mL screw-cap tube')
        self.h.host_configure_motion(INST, **MOTION)
        self.h.host_present_sample(INST, tube_id='tube_1', cap_id='cap_1', consumable_id='tube15', evidence='synthetic placement')
        if on:
            assert self.hold()
        self.call(self.A, 'consumable.select', consumable_id='tube15')

    def call(self, sess, cap, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def press(self, key='button', d=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=d)['accepted']

    def hold(self):
        return self.press('button', 3.0)

    def trigger(self):
        return self.press('trigger')

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lit(self):
        """LEDs lit now, from the frame and from the pixels (both must agree)."""
        fr = self.frame()['displays']
        on = {k for k in LEDS if fr[k]['shown']}
        px = {k for k in LEDS if tuple(self.h.panel(INST).render(k)[16, 16]) == tuple(SPEC['displays'][k]['colors']['on'])}
        assert on == px, (on, px)
        return on

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def step(self, s):
        self.h.host_step(s)

    def op(self):
        ops = [o for o in self.h.debug_snapshot()['operations'].values()]
        return ops[-1]

    def run_cycle(self, succeeded=True, release=True):
        """The host side of one cycle: clamp, attach, rotate, thread result, release (the panel only triggers it)."""
        self.step(0.5)
        op = self.op()
        if op['phase'] == 'awaiting_attach':
            ev = [e for e in self.h.host_events(INST) if not e['acknowledged']][-1]
            self.h.host_confirm_attachment(INST, operation_id=op['operation_id'], event_id=ev['event_id'], constraint_id=f'grip_{self.n}', evidence='synthetic')
        self.step(5.0)
        op = self.op()
        self.h.host_confirm_thread_result(INST, operation_id=op['operation_id'], succeeded=succeeded, evidence='synthetic thread result')
        if release and self.op()['phase'] == 'awaiting_release':
            ev = [e for e in self.h.host_events(INST) if not e['acknowledged']][-1]
            self.h.host_confirm_release(INST, operation_id=op['operation_id'], event_id=ev['event_id'],
                                        constraint_id=self.state('attachment.constraint_id'), evidence='synthetic')
            self.step(2.0)


def runtime_state(h):
    s = h.debug_snapshot()['instances'][INST]
    return json.dumps(dict(values=s['values'], last=s['last_action']), sort_keys=True)


def mode_to(g, mode):
    for _ in range(6):
        if g.state('cycle.operation_mode') == mode:
            return
        g.press()
    raise AssertionError(mode)


class KeyProcedures(unittest.TestCase):
    def test_select_decap_only_mode(self):
        g = Rig(on=False)
        self.assertEqual(g.lit(), set())
        self.assertFalse(g.press())                                                      # off: a short press does nothing
        self.assertTrue(g.hold())                                                        # 3 s: on
        self.assertEqual(g.lit(), {'led_decap_only', 'led_decap'})
        g.press()
        self.assertEqual((g.state('cycle.operation_mode'), g.lit()), ('cap_only', {'led_cap_only', 'led_cap'}))
        g.press()
        self.assertEqual(g.lit(), {'led_release_cap', 'led_decap_only', 'led_decap'})   # delayed release: two LEDs
        for _ in range(3):
            g.press()
        self.assertEqual((g.state('cycle.operation_mode'), g.lit()), ('decap_only', {'led_decap_only', 'led_decap'}))
        DONE_TASKS.add('Select Decap Only mode')

    def test_decap_or_recap_a_tube(self):
        g = Rig()
        self.assertTrue(g.trigger())                                                    # decap
        self.assertEqual(g.state('cycle.status'), 'clamping')
        g.run_cycle()
        self.assertEqual(g.state('cycle.status'), 'succeeded')
        self.assertEqual(g.h.debug_snapshot()['instances'][INST]['values']['truth.thread_state'], 'open')
        mode_to(g, 'cap_only')
        self.assertTrue(g.trigger())                                                    # recap
        g.run_cycle()
        self.assertEqual(g.h.debug_snapshot()['instances'][INST]['values']['truth.thread_state'], 'closed')
        self.assertTrue(g.hold())                                                       # 3 s: off
        self.assertEqual((g.state('power.enabled'), g.lit()), (False, set()))
        DONE_TASKS.add('Decap or recap a tube')

    def test_change_the_cycle_time(self):
        g = Rig()
        mode_to(g, 'hold_cap')
        g.trigger()
        g.run_cycle()                                                                    # Hold Cap: the cap stays in the jaws
        self.assertEqual(g.lit(), {'led_hold_cap', 'led_cap'})
        self.assertTrue(g.state('cycle.holding_cap'))
        g.hold()                                                                         # 3 s: the cycle-time setting, LEDs blink
        fr = g.frame()
        self.assertEqual(fr['screen'], 'setting')
        self.assertEqual((fr['displays']['led_hold_cap']['blink'], fr['displays']['led_release_cap']['on']), (False, True))   # setting 2: Release Cap
        g.hold()                                                                         # setting 3: both blink
        fr = g.frame()['displays']
        self.assertTrue(fr['led_hold_cap']['blink'] and fr['led_release_cap']['blink'])
        g.step((0.75 - g.h.time_s % 1.0) % 1.0 or 1.0)                                 # to the off phase of the 1 s blink
        self.assertEqual(g.lit() & {'led_hold_cap', 'led_release_cap'}, set())
        g.trigger()                                                                      # store, and the motor recaps with the held cap
        self.assertEqual((g.state('cycle.time_setting'), g.frame()['screen'], g.state('cycle.status')), (3, 'on', 'rotating'))
        g.step(1.0)
        self.assertEqual(g.state('cycle.status'), 'rotating')                            # setting 3 recap 1.8 s (x1.8 of setting 1)
        g.step(0.81)
        self.assertEqual(g.state('cycle.status'), 'awaiting_thread_result')
        g.hold()
        g.hold()
        self.assertEqual(g.state('cycle.time_setting'), 3)                               # kept after a reboot
        DONE_TASKS.add('Change the cycle time')


class OtherModes(unittest.TestCase):
    def test_release_cap_alternates_and_delayed_release(self):
        g = Rig()
        mode_to(g, 'release_cap')
        g.trigger()
        g.run_cycle()
        self.assertEqual((g.state('cycle.mode'), g.lit()), ('recap', {'led_release_cap', 'led_cap'}))
        g.trigger()
        g.run_cycle()
        self.assertEqual(g.state('cycle.mode'), 'decap')
        mode_to(g, 'delayed_release')
        g.trigger()
        g.run_cycle(release=False)
        op = g.op()
        ev = [e for e in g.h.host_events(INST) if not e['acknowledged']][-1]
        g.h.host_confirm_release(INST, operation_id=op['operation_id'], event_id=ev['event_id'], constraint_id=g.state('attachment.constraint_id'), evidence='synthetic')
        g.step(0.9)
        self.assertEqual(g.state('jaw_gap_m'), MOTION['closed_gap_m'])                  # the delay: the jaws are still closed
        g.step(0.25)
        self.assertGreater(g.state('jaw_gap_m'), MOTION['closed_gap_m'])
        g.step(1.0)
        self.assertEqual(g.state('cycle.status'), 'succeeded')

    def test_hold_cap_blocks_other_keys_until_recapped(self):
        g = Rig()
        mode_to(g, 'hold_cap')
        g.trigger()
        g.run_cycle()
        self.assertFalse(g.press())                                                      # holding a cap: mode change refused
        self.assertEqual(g.state('cycle.operation_mode'), 'hold_cap')
        g.trigger()
        g.run_cycle()
        self.assertEqual(g.state('cycle.holding_cap', 'cycle.mode', 'attachment.constraint_id'),
                         {'cycle.holding_cap': False, 'cycle.mode': 'decap', 'attachment.constraint_id': None})


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        mode_to(a, 'release_cap')
        b.call(b.A, 'cycle.set_operation_mode', mode='release_cap')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.trigger()
        b.call(b.A, 'cycle.start')
        for g in (a, b):
            g.run_cycle()
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


class Rules(unittest.TestCase):
    def test_refusals_and_access(self):
        g = Rig()
        g.trigger()
        self.assertFalse(g.press())                                                      # busy: a cycle runs
        r = g.call(g.A, 'panel.frame', expect=('rejected',))
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        caps = g.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.press', 'panel.capture'})
        r = g.call(g.A, 'panel.press', expect=('rejected',), input_id='nope')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')

    def test_legacy_mode_command_sets_the_panel_mode(self):
        g = Rig()
        g.call(g.A, 'cycle.set_mode', mode='recap')
        self.assertEqual((g.state('cycle.operation_mode'), g.lit()), ('cap_only', {'led_cap_only', 'led_cap'}))


class Callers(unittest.TestCase):
    """A gesture acts as the session that makes it (sync audit 2026-10-19): a cycle started on the trigger belongs to that agent."""
    def test_panel_started_cycle_belongs_to_the_gesturing_agent(self):
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.trigger()
        op = g.op()
        self.assertEqual(op['principal_id'], 'agent')
        self.assertEqual(g.A.poll(operation_id=op['operation_id'])['status'], 'running')
        before = runtime_state(g.h)
        self.assertEqual(B.poll(operation_id=op['operation_id'])['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(B.cancel(operation_id=op['operation_id'], request_id='b_cancel')['error']['code'], 'ACCESS_DENIED')
        for d in (0.1, 3.0):                                                             # B's mode key and power-off hold are refused
            self.assertFalse(g.call(B, 'panel.press', input_id='button', duration_s=d)['accepted'])
        self.assertEqual(runtime_state(g.h), before)
        self.assertEqual(g.call(B, 'power.set', expect='rejected', enabled=False)['error']['code'], 'ACCESS_DENIED')   # direct path too (lead
        for cap, args in (('cycle.set_mode', dict(mode='recap')), ('cycle.set_time_setting', dict(setting=2))):           # rule 2026-10-19); settings
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'BUSY')                         # of the next cycle: busy
        self.assertEqual(runtime_state(g.h), before)
        self.assertEqual(g.call(g.A, 'power.set', expect='rejected', enabled=False)['error']['code'], 'BUSY')   # the owner: the cycle runs
        self.assertEqual(g.call(g.E, 'power.set', expect='rejected', enabled=False)['error']['code'], 'BUSY')   # evaluator: no ownership limit
        self.assertEqual(g.A.cancel(operation_id=op['operation_id'], request_id='a_cancel')['status'], 'cancelled')
        self.assertEqual(g.state('cycle.status'), 'cancelled')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        for k in LEDS:
            self.assertTrue((a.h.panel(INST).render(k) == b.h.panel(INST).render(k)).all())
        OUT.mkdir(parents=True, exist_ok=True)
        p = a.h.panel(INST)
        shots = {'home': p.save_png('led_decap_only', OUT / 'home.png'), 'menu': p.save_png('led_decap', OUT / 'menu.png')}
        a.press()
        shots['error'] = p.save_png('led_decap_only', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='led_decap_only')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())

    def test_spec_and_contract(self):
        meta = rt.export_intellixcap_s1_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities'])), [])
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['ui_class'], 'parameter_cycle')
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('ixs_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 1:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
