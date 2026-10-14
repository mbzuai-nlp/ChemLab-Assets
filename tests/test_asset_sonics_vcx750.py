import copy,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.sonics_vcx750 import *
def prepared(i='vcx750_01',plant=None,sample=None,method=None,temp_probe=True):
 r=SonicsVcx750Runtime(i);r.invoke('plant.configure',**(plant or PLANT_EXAMPLE));r.invoke('method.configure',**(method or METHOD_EXAMPLE));r.invoke('sample.configure',**(sample or example_sample()));r.invoke('mount.configure',converter_clamped_upper=True,probe_tight=True,vessel_fixed=True,temperature_probe_present=temp_probe);r.invoke('power.set',enabled=True);r.step(2);return r
class Vcx750Test(unittest.TestCase):
 def test_setting_temperature_no_teleport(self):
  r=prepared();before=r.temperature;r.invoke('thermal.set_temperature',temperature_K=300.15);self.assertEqual(r.temperature,before);self.assertEqual(r.limit,300.15)
 def test_queries_pure(self):
  r=prepared();s=r.debug_snapshot();r.describe();r.read();r.invoke('sonication.measure');r.invoke('product.read');self.assertEqual(s,r.debug_snapshot())
 def test_rejected_atomic(self):
  r=prepared();r.invoke('acquisition.start');s=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('mount.set_immersion',fraction=.5)
  self.assertEqual(s,r.debug_snapshot())
 def test_missing_mount(self):
  r=prepared();r.invoke('mount.configure',converter_clamped_upper=False,probe_tight=True,vessel_fixed=True,temperature_probe_present=True)
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_amplitude_range(self):
  r=prepared()
  for x in [.19,1.01,True,float('nan'),float('inf')]:
   with self.assertRaises(ActionRejected):r.invoke('sonication.set_amplitude',amplitude_fraction=x)
 def test_step_invalid(self):
  r=prepared()
  for x in [True,-1,float('nan'),float('inf'),3601]:
   with self.assertRaises(ActionRejected):r.step(x)
 def test_not750_times_amplitude(self):
  r=prepared();self.assertAlmostEqual(r.load(),90*.4**2);self.assertNotEqual(r.load(),750*.4)
 def test_standard_probe_only(self):
  m=copy.deepcopy(METHOD_EXAMPLE);m['probe']='630-0219'
  with self.assertRaises(ActionRejected):prepared(method=m)
 def test_no_organic_standard_tip(self):
  s=example_sample();s['carrier_id']='ethanol'
  with self.assertRaises(ActionRejected):prepared(sample=s)
 def test_pulse_limits(self):
  for value in [0,60,1.5]:
   m=copy.deepcopy(METHOD_EXAMPLE);m['pulse_on_s']=value
   with self.assertRaises(ActionRejected):prepared(method=m)
 def test_lift_on_step(self):
  r=prepared();r.invoke('mount.set_immersion',fraction=.6);self.assertEqual(r.lift,0.)
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
  r.step(1);self.assertAlmostEqual(r.immersion()/(r.volume()/AREA),.6);r.invoke('acquisition.start')
 def test_too_small_large_volume(self):
  for mass in [.01,.30]:
   s=example_sample();s.update(carrier_mass_kg=mass,solid_mass_kg=0.)
   with self.assertRaises(ActionRejected):prepared(sample=s)
 def test_mass_conservation(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);r.invoke('product.collect',fraction=.3,recipient_id='vial')
  for a,b,c in zip(r.initial,r.mass,r.collected):self.assertAlmostEqual(a,b+c)
 def test_collection_energy_export(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);before=r.storage;r.invoke('product.collect',fraction=.5,recipient_id='vial');self.assertGreater(r.collected_heat,0);self.assertLess(r.storage,before);r.step(1);self.assertAlmostEqual(r.heat,r.storage+r.loss+r.collected_heat,places=8)
 def test_energy_balance(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);self.assertAlmostEqual(r.heat,r.storage+r.loss,places=8);self.assertAlmostEqual(r.heat,.6*r.total_energy);self.assertGreater(r.temperature,298.15)
 def test_breakup_no_mass_creation(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);self.assertLess(r.size,r.sample['initial_size_m']);self.assertGreaterEqual(r.size,r.plant['minimum_size_m']);self.assertEqual(r.mass,r.initial)
 def test_cancel_partial(self):
  r=prepared();r.invoke('acquisition.start');r.step(.5);e=r.total_energy;d=r.size;r.invoke('acquisition.stop');r.step(2);self.assertEqual(r.phase,'cancelled');self.assertEqual(r.total_energy,e);self.assertEqual(r.size,d);self.assertEqual(r.mass,r.initial)
 def test_on_timer_not_wall(self):
  r=prepared();r.invoke('acquisition.start');r.step(7.5);self.assertEqual(r.phase,'succeeded');self.assertAlmostEqual(r.on_time,4);self.assertAlmostEqual(r.elapsed_s,7)
 def test_energy_endpoint_exact(self):
  m=copy.deepcopy(METHOD_EXAMPLE);m.update(duration_s=100.,energy_endpoint_J=20.);r=prepared(method=m);r.invoke('acquisition.start');r.step(8);self.assertEqual(r.phase,'succeeded');self.assertAlmostEqual(r.cycle_energy,20);self.assertAlmostEqual(r.on_time,20/(90*.4**2))
 def test_endpoint_first(self):
  m=copy.deepcopy(METHOD_EXAMPLE);m.update(duration_s=1.,energy_endpoint_J=100.);r=prepared(method=m);r.invoke('acquisition.start');r.step(8);self.assertAlmostEqual(r.cycle_energy,14.4);self.assertAlmostEqual(r.elapsed_s,1)
 def test_pause_preserves_pulse(self):
  r=prepared();r.invoke('acquisition.start');r.step(.3);r.invoke('sonication.pause');e=r.cycle_energy;phase=r.pulse_time;r.step(2);self.assertEqual(e,r.cycle_energy);self.assertEqual(phase,r.pulse_time);self.assertAlmostEqual(r.elapsed_s,2.3);r.invoke('sonication.pause');r.step(.7);self.assertAlmostEqual(r.on_time,1)
 def test_temperature_hold_resume(self):
  p=copy.deepcopy(PLANT_EXAMPLE);p.update(cooling_W_K=10.,temperature_hysteresis_K=.01);m=copy.deepcopy(METHOD_EXAMPLE);m.update(duration_s=100.,pulse_enabled=False);r=prepared(plant=p,method=m);r.invoke('thermal.set_temperature',temperature_K=298.16);r.invoke('acquisition.start');r.step(2);self.assertTrue(r.thermal_hold);self.assertLessEqual(r.temperature,298.16+1e-9);e=r.total_energy;r.invoke('thermal.set_temperature',temperature_K=300.15);r.step(.2);self.assertGreater(r.total_energy,e)
 def test_optional_temperature_missing(self):
  r=prepared(temp_probe=False);self.assertIsNone(r.invoke('sonication.measure')['temperature_K'])
  with self.assertRaises(ActionRejected):r.invoke('thermal.set_temperature',temperature_K=300.15)
 def test_temperature_auto_resume(self):
  p=copy.deepcopy(PLANT_EXAMPLE);p.update(cooling_W_K=100.,temperature_hysteresis_K=.01);m=copy.deepcopy(METHOD_EXAMPLE);m.update(duration_s=100.,pulse_enabled=False);r=prepared(plant=p,method=m);r.invoke('thermal.set_temperature',temperature_K=298.19);r.invoke('acquisition.start');seen_hold=seen_resume=False
  for _ in range(500):
   r.step(.02)
   if r.thermal_hold:seen_hold=True
   elif seen_hold:seen_resume=True
  self.assertTrue(seen_hold and seen_resume);self.assertLessEqual(r.temperature,298.19+1e-9)
 def test_power_stop_first(self):
  r=prepared();r.invoke('acquisition.start')
  with self.assertRaises(ActionRejected):r.invoke('power.set',enabled=False)
 def test_power_loss_wait_no_restart(self):
  r=prepared();r.invoke('acquisition.start');r.step(.2);r.invoke('power.interrupt')
  with self.assertRaises(ActionRejected):r.invoke('power.set',enabled=True)
  r.step(180.);r.invoke('power.set',enabled=True);self.assertFalse(r.emitting());self.assertEqual(r.phase,'cancelled')
 def test_cycle_counter_resets_not_heat(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);total=r.total_energy;heat=r.heat;r.invoke('acquisition.start');self.assertEqual(r.cycle_energy,0);self.assertEqual(r.total_energy,total);self.assertEqual(r.heat,heat)
 def test_dual_isolation(self):
  a=prepared('a');b=prepared('b');snap=b.debug_snapshot();a.invoke('acquisition.start');a.step(5);self.assertEqual(snap,b.debug_snapshot())
 def test_permissions(self):
  r=prepared();s=r.session();self.assertNotIn('truth.mass_kg',s.read(instance_id=r.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=r.instance_id)['capabilities']);self.assertEqual(s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='bad')['error']['code'],'ACCESS_DENIED')
 def test_idempotency(self):
  r=prepared();s=r.session();kw=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='a');a=s.invoke(**kw);snap=r.debug_snapshot();self.assertEqual(a,s.invoke(**kw));self.assertEqual(snap,r.debug_snapshot())
 def test_poll_cancel_ownership(self):
  r=prepared();s=r.session();r.register_principal('second','agent');op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];r.step(.2)
  with self.assertRaises(ActionRejected):r.session('second').poll(op)
  self.assertEqual(s.cancel(op,'cancel')['status'],'cancelled');self.assertEqual(s.cancel(op,'cancel')['status'],'cancelled')
 def test_reference_real_water_capacity(self):
  r=prepared();cp=r.invoke('reference.lookup',substance_id='water');self.assertTrue(4100<cp['cp_J_kg_K']<4300);self.assertAlmostEqual(r.capacity,.099*cp['cp_J_kg_K']+.002*800+30);self.assertFalse(r.invoke('reference.lookup',substance_id='ethanol')['installed_probe_eligible'])
 def test_reference_range_rejected(self):
  s=example_sample();s['temperature_K']=310.
  with self.assertRaises(ActionRejected):prepared(sample=s)
 def test_reference_failure_honest(self):
  p=copy.deepcopy(PLANT_EXAMPLE);p.update(full_amplitude_probe_power_W=750.,absorbed_fraction=1.,cooling_W_K=0.,vessel_heat_capacity_J_K=0.);s=example_sample();s['temperature_K']=303.14;m=copy.deepcopy(METHOD_EXAMPLE);m['amplitude_fraction']=1.;r=prepared(plant=p,sample=s,method=m);r.invoke('acquisition.start');r.step(10);self.assertEqual(r.phase,'failed');self.assertEqual(r.mode,'reference_limit');self.assertLessEqual(r.temperature,303.15);self.assertEqual(r.mass,r.initial)
 def test_amplitude_button_limits(self):
  r=prepared();r.invoke('sonication.set_amplitude',amplitude_fraction=1.);r.invoke('sonication.amplitude_up');self.assertEqual(r.amplitude(),1.);r.invoke('sonication.amplitude_down');self.assertAlmostEqual(r.amplitude(),.99)
 def test_half_step(self):
  a=prepared();b=prepared();a.invoke('acquisition.start');b.invoke('acquisition.start')
  for _ in range(400):a.step(.02)
  for _ in range(800):b.step(.01)
  for k in ['temperature','size','total_energy','heat','loss','storage','on_time','elapsed_s']:self.assertAlmostEqual(getattr(a,k),getattr(b,k),places=7)
if __name__=='__main__':unittest.main()
