import copy,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.ika_t25 import *
def prepared(i='t25_01',plant=None,sample=None,method=None):
 r=IkaT25Runtime(i);r.invoke('plant.configure',**(plant or PLANT_EXAMPLE));r.invoke('method.configure',**(method or METHOD_EXAMPLE));r.invoke('sample.configure',**(sample or example_sample()));r.invoke('mount.configure',head_locked=True,vessel_clamped=True);r.invoke('power.set',enabled=True);r.step(2);return r
class T25Test(unittest.TestCase):
 def test_target_not_teleport(self):
  r=prepared();r.invoke('acquisition.start');r.step(.2);w=r.speed;r.invoke('motor.set_speed',speed_rad_s=MAX_SPEED);self.assertEqual(w,r.speed);r.step(.1);self.assertGreater(r.speed,w)
 def test_queries_pure(self):
  r=prepared();s=r.debug_snapshot();r.describe();r.read();r.invoke('dispersion.measure');r.invoke('product.read');self.assertEqual(s,r.debug_snapshot())
 def test_rejected_atomic(self):
  r=prepared();r.invoke('acquisition.start');s=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('mount.set_immersion',immersion_m=.065)
  self.assertEqual(s,r.debug_snapshot())
 def test_missing_mount(self):
  r=prepared();r.invoke('mount.configure',head_locked=False,vessel_clamped=True)
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_speed_si(self):
  r=prepared()
  for w in [2999*math.tau/60,25001*math.tau/60,True,float('nan'),float('inf')]:
   with self.assertRaises(ActionRejected):r.invoke('motor.set_speed',speed_rad_s=w)
 def test_step_invalid(self):
  r=prepared()
  for x in [True,-1,float('nan'),float('inf'),3601]:
   with self.assertRaises(ActionRejected):r.step(x)
 def test_viscosity_si(self):
  s=example_sample();s['viscosity_Pa_s']=5000
  with self.assertRaises(ActionRejected):prepared(sample=s)
 def test_head_not_st(self):
  m=copy.deepcopy(METHOD_EXAMPLE);m['head']='s25_n18g_st'
  with self.assertRaises(ActionRejected):prepared(method=m)
 def test_no_timer_invention(self):
  m=copy.deepcopy(METHOD_EXAMPLE);m['duration_s']=61
  with self.assertRaises(ActionRejected):prepared(method=m)
 def test_bottom_clearance(self):
  r=prepared();s=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('mount.set_immersion',immersion_m=.10)
  self.assertEqual(s,r.debug_snapshot())
 def test_lift_on_step(self):
  r=prepared();r.invoke('mount.set_immersion',immersion_m=.065);self.assertEqual(r.lift,0.)
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
  r.step(.2);self.assertAlmostEqual(r.lift,-.005);r.invoke('acquisition.start')
 def test_too_small_vessel_volume(self):
  s=example_sample();s['carrier_mass_kg']=.001;s['dispersed_mass_kg']=0.
  with self.assertRaises(ActionRejected):prepared(sample=s)
 def test_mass_conservation(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);self.assertEqual(r.mass,r.initial);r.invoke('product.collect',fraction=.3,recipient_id='vial')
  for a,b,c in zip(r.initial,r.mass,r.collected):self.assertAlmostEqual(a,b+c)
 def test_energy_balance(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);self.assertAlmostEqual(r.heat,r.storage+r.loss,places=8);self.assertLessEqual(r.heat,r.energy);self.assertGreater(r.temperature,298.15)
 def test_breakup_no_mass_creation(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);self.assertLess(r.size,r.sample['initial_size_m']);self.assertGreaterEqual(r.size,r.plant['minimum_size_m']);self.assertEqual(r.mass,r.initial)
 def test_cancel_coast_partial(self):
  r=prepared();r.invoke('acquisition.start');r.step(1);e=r.energy;d=r.size;mass=r.mass.copy();r.invoke('acquisition.stop');self.assertGreater(r.speed,0);r.step(2);self.assertEqual(r.speed,0);self.assertEqual(r.phase,'cancelled');self.assertGreater(r.energy,e);self.assertLessEqual(r.size,d);self.assertEqual(mass,r.mass)
 def test_completion(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.speed,0);self.assertAlmostEqual(r.elapsed_s,5)
 def test_power_no_restart(self):
  r=prepared();r.invoke('acquisition.start');r.step(1);r.invoke('power.set',enabled=False);r.step(2);r.invoke('power.set',enabled=True);r.step(1);self.assertFalse(r.motor);self.assertEqual(r.speed,0);self.assertEqual(r.phase,'cancelled')
 def test_dual_isolation(self):
  a=prepared('a');b=prepared('b');snap=b.debug_snapshot();a.invoke('acquisition.start');a.step(5);self.assertEqual(snap,b.debug_snapshot())
 def test_permissions(self):
  r=prepared();s=r.session();self.assertNotIn('truth.mass_kg',s.read(instance_id=r.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=r.instance_id)['capabilities']);self.assertEqual(s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='bad')['error']['code'],'ACCESS_DENIED')
 def test_idempotency(self):
  r=prepared();s=r.session();kw=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='a');a=s.invoke(**kw);snap=r.debug_snapshot();self.assertEqual(a,s.invoke(**kw));self.assertEqual(snap,r.debug_snapshot())
 def test_poll_cancel_ownership(self):
  r=prepared();s=r.session();r.register_principal('second','agent');op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];r.step(1)
  with self.assertRaises(ActionRejected):r.session('second').poll(op)
  self.assertEqual(s.cancel(op,'cancel')['status'],'cancelled');self.assertEqual(s.cancel(op,'cancel')['status'],'cancelled')
 def test_external_observation(self):
  r=prepared();self.assertIsNone(r.invoke('observation.read')['temperature_K']);r.invoke('observation.record',temperature_K=299.,size_m=.0001,measurement_source='external');self.assertEqual(r.invoke('observation.read')['temperature_K'],299.);self.assertEqual(r.temperature,298.15)
 def test_reference_real_drives_capacity(self):
  r=prepared();cp=r.invoke('reference.lookup',substance_id='water');self.assertTrue(4100<cp['cp_J_kg_K']<4300);self.assertAlmostEqual(r.capacity,.9*cp['cp_J_kg_K']+.08*2000+100)
 def test_reference_range_rejected(self):
  s=example_sample();s['temperature_K']=310.
  with self.assertRaises(ActionRejected):prepared(sample=s)
 def test_reference_failure_honest(self):
  p=copy.deepcopy(PLANT_EXAMPLE);p.update(power_number=100.,heat_fraction=1.,cooling_W_K=0.,vessel_heat_capacity_J_K=0.)
  s=example_sample();s['temperature_K']=303.14;m=copy.deepcopy(METHOD_EXAMPLE);m['speed_rad_s']=MAX_SPEED;r=prepared(plant=p,sample=s,method=m);r.invoke('acquisition.start');r.step(10);self.assertEqual(r.phase,'failed');self.assertEqual(r.mode,'reference_limit');self.assertLessEqual(r.temperature,303.15);self.assertEqual(r.mass,r.initial)
 def test_display_resolution(self):
  r=prepared();r.speed=13590*math.tau/60;r.motor=True;self.assertAlmostEqual(r._values()['panel.speed_krpm'],13.6);r.motor=False;self.assertEqual(r.indicated(),0.)
 def test_half_step(self):
  a=prepared();b=prepared();a.invoke('acquisition.start');b.invoke('acquisition.start')
  for _ in range(400):a.step(.02)
  for _ in range(800):b.step(.01)
  for k in ['temperature','size','energy','heat','loss','storage','angle']:self.assertAlmostEqual(getattr(a,k),getattr(b,k),places=7)
if __name__=='__main__':unittest.main()
