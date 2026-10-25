"""The Agilent 8697 headspace sampler's event clock.

The 8697 runtime has no step / advance call: it reads a clock callable and moves its timed events only in host_update().  Its
host recipe has the advance mode "clock_host_update" (clock= passed, host_update() once after each clock step),
which chemlab runs in hosts.RuntimeHost.advance; the lab's clock hook (labbackend._build) is then off.  A recipe without an advance
mode (the old one) still works through the clock hook.  Either way there must be exactly one host_update() per clock step - in the
default lab and in the sample_intro class world (which also attaches the 8697 scene) - and the default-lab workflow must finish.

Both modes are tested by setting the lab-config entry's "recipe" key (it overrides the loaded RECIPES record).

    cd chemlab/tests_compile && python -m unittest test_hs8697_clock
"""
import copy
import json
import os
import shutil
import sys
import unittest

from helpers import ROOT, tmpdir  # noqa: F401  (puts chemlab on sys.path)
from chemlab import recipes as R
from chemlab.labbackend import LabBackend, load_config
from chemlab.glab import single
from chemlab.compile.tasks import sample_intro_world as SI

HS = 'agilent_hs8697'
MODES = (R.CLOCK_HOST_UPDATE, None)                 # the current host recipe, and the old recipe (no advance mode)


def lab_config(world, mode):
    cfg = single([HS], load_config())
    if world == 'sample_intro':
        cfg = SI.config(cfg)
        cfg['instruments'] = [e for e in cfg['instruments'] if e['id'] == HS]
    e = cfg['instruments'][0]
    e['recipe'] = dict(e.get('recipe') or {}, advance=mode)
    return cfg


class HS8697Clock(unittest.TestCase):
    def test_0_mode_is_known(self):
        self.assertIn(R.CLOCK_HOST_UPDATE, R.ADVANCE_MODES)
        self.assertIn(R.CLOCK_HOST_UPDATE, R.CLOCK_MODES)
        self.assertEqual(R.all_recipes()['instrument.' + HS].get('advance'), R.CLOCK_HOST_UPDATE,
                         'host recipes: the 8697 recipe is expected to use clock_host_update')

    def test_1_one_host_update_per_clock_step(self):
        for world in ('default', 'sample_intro'):
            for mode in MODES:
                with self.subTest(world=world, mode=mode):
                    t = tmpdir()
                    try:
                        b = LabBackend(lab_config(world, mode), run_dir=os.path.join(t, 'b'))
                        inst = b.instruments[HS]
                        rt = inst.host.host
                        self.assertEqual(inst.clock_hook, 'host_update' if mode is None else None)
                        if world == 'sample_intro':
                            self.assertIsNotNone(inst.scene)
                            self.assertFalse(inst.scene.wrapped, 'the scene must not add a second host_update')
                        calls = []
                        orig = rt.host_update

                        def counted(*a, **k):
                            calls.append(inst.host.local_s)
                            return orig(*a, **k)
                        rt.host_update = counted
                        inst.host.flush()
                        n0 = inst.host.clock_counter
                        b.advance(7.5)                              # 7 steps of 1 s and one of 0.5 s
                        inst.host.flush()
                        steps = inst.host.clock_counter - n0
                        self.assertEqual(steps, 8)
                        self.assertEqual(len(calls), steps, 'host_update calls per clock step')
                        # the runtime sees the new time when it updates (the clock callable is set first)
                        self.assertAlmostEqual(calls[-1], inst.host.local_s)
                        self.assertAlmostEqual(rt.clock() if callable(getattr(rt, 'clock', None)) else inst.host.local_s,
                                               inst.host.local_s)
                    finally:
                        shutil.rmtree(t, ignore_errors=True)

    def test_2_default_lab_workflow_finishes_in_both_modes(self):
        sys.path.insert(0, os.path.join(ROOT, 'tests_generic'))
        from test_default_world import SPEC, run_spec
        with open(SPEC, encoding='utf-8') as f:
            sp = json.load(f)[HS]
        for mode in MODES:
            with self.subTest(mode=mode):
                cfg = load_config()
                e = next(x for x in cfg['instruments'] if x['id'] == HS)
                e['recipe'] = dict(e.get('recipe') or {}, advance=mode)
                res = run_spec(cfg, HS, sp)
                self.assertTrue(res['ok'], json.dumps(res['rows'][-3:])[:600])
                self.assertIn('measurement.count = 1', res['check']['out'])


if __name__ == '__main__':
    unittest.main()
