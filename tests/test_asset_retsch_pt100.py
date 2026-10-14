import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.retsch_pt100 import *
def prepared(i='pt100_01',substance_id=None):
 r=RetschPt100Runtime(i)
 for c in ['plant.configure','method.configure']:r.invoke(c,**CAPS[c][6])
 s=example_sample()
 if substance_id:s['portions']=[dict(s['portions'][0],substance_id=substance_id,mass_kg=.03)]
 r.invoke('sample.configure',**s)
 for c in ['mount.configure','power.set','feeder.set_power']:r.invoke(c,**CAPS[c][6])
 return r
def conserved(r):return all(abs(sum(row[j] for row in r.ledger)+r.remaining[j]+r.lost[j]+r.overflow[j]+r.collected[j]-v)<1e-10 for j,v in enumerate(r.initial))
class TestPt100(unittest.TestCase):
 def test_default_safe(self):
  r=RetschPt100Runtime();self.assertFalse(r.powered);self.assertEqual(r.phase,'idle');self.assertEqual(r.read()['rotor.speed_rad_s'],0)
 def test_read_pure_and_copy(self):
  r=prepared();a=r.debug_snapshot();d=r.read('debug');d['truth.hopper_mass_kg'][0]=999;self.assertEqual(a,r.debug_snapshot());r.describe();self.assertEqual(a,r.debug_snapshot())
 def test_setpoint_no_time(self):
  r=prepared();r.invoke('hopper.set_gap',gap_m=.04);self.assertEqual(r.gap,.03);r.step(.1);self.assertAlmostEqual(r.gap,.032)
 def test_nominal_interlock(self):
  r=prepared();r.invoke('acquisition.start');r.step(1);self.assertEqual(r.remaining,r.initial);self.assertLess(r.speed,OMEGA);r.step(2);self.assertLess(sum(r.remaining),sum(r.initial));self.assertEqual(r.speed,OMEGA)
 def test_fixed_speed(self):
  r=prepared();r.invoke('acquisition.start');r.step(3);r.invoke('feeder.set_intensity',intensity=99);r.step(1);self.assertEqual(r.speed,OMEGA)
 def test_pause_stops_feed_freezes_timer(self):
  r=prepared();r.invoke('acquisition.start');r.step(4);r.invoke('acquisition.pause');a=copy.deepcopy(r.ledger);t=r.elapsed_s;r.step(4);self.assertEqual(a,r.ledger);self.assertEqual(t,r.elapsed_s);self.assertEqual(r.speed,0)
 def test_resume_retains_partial(self):
  r=prepared();r.invoke('acquisition.start');r.step(4);r.invoke('acquisition.pause');r.step(3);m=sum(map(sum,r.ledger));r.invoke('acquisition.resume');r.step(4);self.assertGreater(sum(map(sum,r.ledger)),m);self.assertTrue(conserved(r))
 def test_stop_button_twice(self):
  r=prepared();r.invoke('acquisition.start');r.step(3);r.invoke('acquisition.press_stop');self.assertEqual(r.mode,'paused');r.invoke('acquisition.press_stop');self.assertEqual(r.phase,'cancelled')
 def test_finite_completion(self):
  r=prepared();r.invoke('acquisition.start');r.step(63);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.speed,0);self.assertEqual(r.elapsed_s,60);self.assertTrue(conserved(r))
 def test_power_loss(self):
  r=prepared();r.invoke('acquisition.start');r.step(5);r.invoke('power.set',enabled=False);m=copy.deepcopy(r.ledger);r.step(5);self.assertEqual(r.phase,'cancelled');self.assertEqual(m,r.ledger)
 def test_feeder_power_independent(self):
  r=prepared();r.invoke('acquisition.start');r.step(3);r.invoke('feeder.set_power',enabled=False);m=copy.deepcopy(r.ledger);r.step(2);self.assertEqual(r.speed,OMEGA);self.assertEqual(m,r.ledger)
 def test_zero_intensity(self):
  r=prepared();r.invoke('feeder.set_intensity',intensity=0);r.invoke('acquisition.start');r.step(5);self.assertEqual(r.initial,r.remaining)
 def test_empty_hopper_does_not_invent_stop_sensor(self):
  r=prepared(substance_id='quartz');r.invoke('acquisition.start');r.step(10);self.assertEqual(sum(r.remaining),0.);self.assertTrue(r.read()['feeder.running']);self.assertEqual(r.phase,'running')
 def test_atomic_bad_grain(self):
  r=prepared();s=example_sample();s['portions'][0]['maximum_grain_m']=.011;a=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('sample.refill',**s)
  self.assertEqual(a,r.debug_snapshot())
 def test_gap_guard(self):
  r=prepared();r.invoke('hopper.set_gap',gap_m=.003);r.step(2);r.portions[0]['maximum_grain_m']=.01
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_capacity_atomic(self):
  r=prepared();s=example_sample();s['portions'][0]['mass_kg']=10;a=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('sample.refill',**s)
  self.assertEqual(a,r.debug_snapshot())
 def test_refill_total_limit(self):
  r=prepared();r.invoke('sample.refill',**example_sample());self.assertEqual(len(r.initial),4);self.assertTrue(conserved(r))
 def test_timer_discrete(self):
  r=prepared();r.invoke('timer.adjust',direction=1);self.assertEqual(r.method['duration_s'],180)
  with self.assertRaises(ActionRejected):r.invoke('method.configure',duration_s=120.,intensity=50)
 def test_reject_busy_atomic(self):
  r=prepared();r.invoke('acquisition.start');r.step(3);a=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('hopper.set_gap',gap_m=.04)
  self.assertEqual(a,r.debug_snapshot())
 def test_collect_finite(self):
  r=prepared();r.invoke('acquisition.start');r.step(63);old=sum(r.ledger[0]);a=r.invoke('product.collect',recipient_index=0,fraction=.5,recipient_id='r');self.assertAlmostEqual(a['transferred_kg'],old/2);self.assertTrue(conserved(r))
 def test_external_weighing_not_truth(self):
  r=prepared();self.assertIsNone(r.invoke('weighing.read')['recovery_fraction']);r.invoke('weighing.record',**CAPS['weighing.record'][6]);self.assertEqual(r.invoke('weighing.read')['recipient_mass_kg'],[.01]*10)
 def test_public_truth_filtered(self):
  r=prepared();s=r.session();self.assertFalse(any(k.startswith('truth.') for k in s.read(instance_id=r.instance_id)['observations']));self.assertEqual(s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='x')['status'],'rejected')
 def test_instance_isolation(self):
  a=prepared('a');b=prepared('b');before=b.debug_snapshot();a.invoke('acquisition.start');a.step(4);self.assertEqual(before,b.debug_snapshot())
 def test_invalid_dt(self):
  r=prepared()
  for dt in [-1,True,float('nan'),float('inf'),3601]:
   before=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.step(dt)
   self.assertEqual(before,r.debug_snapshot())
 def test_async_resume_owner(self):
  r=prepared();s=r.session();r.register_principal('other','agent');op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='s')['operation_id'];r.step(3);r.invoke('acquisition.pause');r.session('other').invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='resume');self.assertEqual(s.poll(op)['operation_id'],op)
  with self.assertRaises(ActionRejected):r.session('other').poll(op)
if __name__=='__main__':unittest.main()
