import unittest, math, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from balance_runtime import Balance, foot_extension_m

class RuntimeTest(unittest.TestCase):
    def settled(self,b,m):
        b.update(m*b.g_cal,0)
        return b.update(m*b.g_cal,b.settling_s+.01)
    def test_tare_sample_and_negative_after_removal(self):
        b=Balance.for_model('PX224E'); b.press('zero')
        self.settled(b,.02); b.press('tare')
        self.assertAlmostEqual(self.settled(b,.0212345)['net_kg'],.0012345,places=8)
        self.assertAlmostEqual(self.settled(b,0)['net_kg'],-.02)
    def test_overload_uses_gross_even_after_tare(self):
        b=Balance.for_model('PX224E'); b.press('zero'); self.settled(b,.20); b.press('tare')
        self.assertEqual(self.settled(b,.221)['status'],'OVERLOAD')
    def test_door_invalidates_stability_without_making_mass_zero(self):
        b=Balance.for_model('PX224E'); b.press('zero'); self.settled(b,.01)
        r=b.update(.01*b.g_cal,4,door_fraction=.5)
        self.assertEqual(r['status'],'UNSTABLE'); self.assertAlmostEqual(r['net_kg'],.01)
    def test_transport_lock_and_bayonet_access(self):
        b=Balance.for_model('SPX2201')
        with self.assertRaises(ValueError): b.set_transport_lock(0)
        with self.assertRaises(ValueError): b.remove_pan(0)
        b.remove_pan(-math.radians(20)); b.set_transport_lock(0); b.install_pan(); b.press('zero')
        self.assertEqual(self.settled(b,.1)['status'],'TRANSPORT_LOCKED')
    def test_closing_door_requires_resettling(self):
        b=Balance.for_model('PX224E'); b.press('zero'); self.settled(b,.01)
        b.update(.01*b.g_cal,10,door_fraction=1)
        self.assertEqual(b.update(.01*b.g_cal,4,door_fraction=0)['status'],'UNSTABLE')
        self.assertEqual(b.update(.01*b.g_cal,3.1,door_fraction=0)['status'],'STABLE')
    def test_quantization_and_units(self):
        b=Balance.for_model('SPX2201'); b.press('zero')
        self.assertAlmostEqual(self.settled(b,.01236)['value'],12.4)
        b.press('print',3); self.assertAlmostEqual(b.read()['value'],.0124)
    def test_foot_coupling(self):
        self.assertAlmostEqual(foot_extension_m(2*math.pi),.001)
    def test_unstable_tare_rejected(self):
        b=Balance.for_model('PX224E'); b.press('zero')
        with self.assertRaises(ValueError): b.press('tare')
    def test_calibration_gate(self):
        b=Balance.for_model('SPX2201'); b.press('zero'); self.settled(b,1)
        self.assertFalse(b.read()['task_ready']); b.calibrate_span(1,9.81)
        self.assertTrue(self.settled(b,1)['task_ready'])

if __name__=='__main__': unittest.main()
