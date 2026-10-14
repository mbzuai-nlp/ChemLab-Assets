import copy,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.buchi_hydrolexh import *
def configured(n=1,fast=False):
 r=BuchiHydrolexhRuntime();p=copy.deepcopy(PLANT_EXAMPLE)
 if fast:p.update(heater_capacity_J_K=100.,sample_capacity_J_K=10.,plate_UA_W_K=0.,sample_UA_W_K=0.,contact_UA_W_K=1.,filter_rate_m3_s=10e-6)
 r.invoke('plant.configure',**p);m=copy.deepcopy(METHOD_EXAMPLE)
 if fast:m['hydrolysis_s']=2.
 r.invoke('method.configure',**m);r.invoke('water.configure',volume_m3=.005);r.invoke('ventilation.set',enabled=True)
 for i in range(1,n+1):r.invoke('sample.configure',**example_sample(i));r.invoke('valve.set',position=i,open=True)
 r.invoke('power.set',enabled=True);r.invoke('thermal.set_mode',mode='preheat');r.step(600);r.invoke('rack.set_raised',raised=False);r.step(1);return r
def run_cycle(r):
 r.invoke('acquisition.start')
 for _ in range(120):
  r.step(30.)
  if r.phase!='running':break
 assert r.phase=='succeeded',(r.phase,r.elapsed_s,r.heater)
 return r
def filter_once(r):
 r.invoke('rack.set_raised',raised=True);r.step(1);r.invoke('vacuum.set',enabled=True);r.invoke('filtration.start');r.step(100);assert r.stage=='filtered';return r
def rinse_all(r):
 for _ in range(4):
  for i in r.channels:r.invoke('rinse.add',position=i,volume_m3=100e-6)
  r.invoke('filtration.start');r.step(100)
 return r
def balances(r):
 p=r.invoke('product.read');return [p['initial_fat_kg']-sum(p[k] for k in ['bound_in_vessels_kg','free_in_vessels_kg','retained_bound_kg','retained_extractable_kg','waste_fat_kg','collected_fat_kg']),p['initial_acid_mol']-p['remaining_acid_mol']-p['waste_acid_mol']-p['collected_acid_mol'],p['initial_volume_m3']-p['remaining_volume_m3']-p['waste_volume_m3']-p['collected_volume_m3'],p['initial_dry_solid_kg']-p['remaining_dry_solid_kg']-p['collected_dry_solid_kg'],p['electrical_J']+p['rinse_sensible_J']-p['storage_J']-p['loss_J']-p['boiling_J']]
class TestBuchiHydrolexh(unittest.TestCase):
 def reject(self,r,cap,**a):
  before=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke(cap,**a)
  self.assertEqual(before,r.debug_snapshot())
 def test_schema_counts(self):self.assertEqual(len(CAPS),19);self.assertEqual(len(SLOTS),25)
 def test_real_library(self):
  r=BuchiHydrolexhRuntime();self.assertEqual(len(r.templates),54);self.assertAlmostEqual(r.invoke('reference.lookup',substance_id='buchi_milk_powder')['fat_mass_fraction'],.2427)
 def test_config_required(self):self.reject(BuchiHydrolexhRuntime(),'acquisition.start')
 def test_plant_assumption_consent(self):
  a=dict(PLANT_EXAMPLE,accept_uncalibrated_model=False);self.reject(BuchiHydrolexhRuntime(),'plant.configure',**a)
 def test_temperature_target_not_invented(self):self.reject(BuchiHydrolexhRuntime(),'thermal.set_temperature',temperature_K=500.)
 def test_settings_no_time(self):
  r=BuchiHydrolexhRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);self.assertEqual(r.time_s,0);self.assertEqual(r.heater,PLANT_EXAMPLE['ambient_K'])
 def test_samples_range(self):
  r=BuchiHydrolexhRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE)
  for a in [dict(example_sample(),position=7),dict(example_sample(),sample_mass_kg=.011),dict(example_sample(),sample_liquid_m3=51e-6),dict(example_sample(),position=True)]:self.reject(r,'sample.configure',**a)
 def test_duplicate_load(self):
  r=BuchiHydrolexhRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('sample.configure',**example_sample());self.reject(r,'sample.configure',**example_sample())
 def test_unknown_reference(self):
  r=BuchiHydrolexhRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);self.reject(r,'sample.configure',**dict(example_sample(),substance_id='fake'))
 def test_preheat_guard(self):
  r=configured(fast=True);r.preheat=599.;self.reject(r,'acquisition.start')
 def test_unused_valve_guard(self):
  r=configured(fast=True);r.invoke('valve.set',position=6,open=True);self.reject(r,'acquisition.start')
 def test_joint_only_step(self):
  r=BuchiHydrolexhRuntime();r.invoke('rack.set_raised',raised=False);self.assertEqual(r.rack_fraction,1);r.step(.1);self.assertAlmostEqual(r.rack_fraction,.8)
 def test_six_position_default_cycle(self):
  r=run_cycle(configured(6));self.assertAlmostEqual(r.elapsed_s,1800.,places=6);self.assertLess(r.time_s,3500);self.assertEqual(r.mode,'off');self.assertTrue(all(c['free']>0 for c in r.channels.values()))
 def test_no_fat_measurement(self):
  r=run_cycle(configured(fast=True));self.assertEqual(set(r.invoke('hydrolysis.measure')),set(MEASURE['properties']));self.assertNotIn('fat_mass_fraction',r.invoke('hydrolysis.measure'))
 def test_all_ledgers(self):
  r=rinse_all(filter_once(run_cycle(configured(6,True))));r.invoke('product.collect',position=1,fraction=.5,recipient_id='out')
  for v in balances(r)[:4]:self.assertLess(abs(v),1e-9)
  self.assertLess(abs(balances(r)[4]),1e-5)
 def test_matrix_specific_release(self):
  r=configured(2,True);r.channels[2]['config']['release_rate_s_inverse']=0.;b=r.channels[2]['bound'];run_cycle(r);self.assertEqual(r.channels[2]['bound'],b);self.assertLess(r.channels[1]['bound'],b)
 def test_cancel_preserves_partial(self):
  r=configured(fast=True);r.invoke('acquisition.start');r.step(.1);s=r._product();r.invoke('acquisition.stop');self.assertEqual(r.phase,'cancelled');self.assertEqual(s,r._product());self.assertEqual(r.mode,'off')
 def test_power_interrupt(self):
  r=configured(fast=True);r.invoke('acquisition.start');r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.assertEqual(r.mode,'off')
 def test_ventilation_fault(self):
  r=configured(fast=True);r.invoke('acquisition.start');r.invoke('ventilation.set',enabled=False);self.assertEqual(r.phase,'failed');self.assertFalse(r.vacuum)
 def test_raise_busy(self):
  r=configured(fast=True);r.invoke('acquisition.start');self.reject(r,'rack.set_raised',raised=True)
 def test_vacuum_early_reject(self):
  r=configured(fast=True);r.invoke('acquisition.start');self.reject(r,'vacuum.set',enabled=True)
 def test_filter_requires_raise(self):
  r=run_cycle(configured(fast=True));self.reject(r,'filtration.start')
 def test_flow_only_step(self):
  r=run_cycle(configured(fast=True));r.invoke('rack.set_raised',raised=True);r.step(1);p=r._product();r.invoke('filtration.start');self.assertEqual(p,r._product());r.step(1);self.assertGreater(r.waste_volume,0)
 def test_collect_before_rinse(self):
  r=filter_once(run_cycle(configured(fast=True)));self.reject(r,'product.collect',position=1,fraction=1.,recipient_id='out')
 def test_water_finite(self):
  r=filter_once(run_cycle(configured(fast=True)));r.water=0.;self.reject(r,'rinse.add',position=1,volume_m3=100e-6)
 def test_filtered_holdup(self):
  r=filter_once(run_cycle(configured(fast=True)));self.assertAlmostEqual(r.channels[1]['tube_volume'],r.plant['tube_holdup_m3']);self.assertLessEqual(r.channels[1]['tube_volume'],64e-6)
 def test_cstr_wash(self):
  r=filter_once(run_cycle(configured(fast=True)));a=r.channels[1]['tube_acid'];r.invoke('rinse.add',position=1,volume_m3=100e-6);r.invoke('filtration.start');r.step(100);self.assertAlmostEqual(r.channels[1]['tube_acid'],a*math.exp(-100e-6/r.plant['tube_holdup_m3']),places=12)
 def test_collect_repeat_finite(self):
  r=rinse_all(filter_once(run_cycle(configured(fast=True))));r.invoke('product.collect',position=1,fraction=1.,recipient_id='out');v=r.collected_fat;r.invoke('product.collect',position=1,fraction=1.,recipient_id='out');self.assertEqual(v,r.collected_fat)
 def test_agent_truth_filter(self):
  r=configured(fast=True);s=r.session();self.assertFalse(any(k.startswith('truth.') for k in r.read()));out=s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='x');self.assertEqual(out['status'],'rejected');self.assertEqual(out['result'],{})
 def test_idempotent_request(self):
  r=BuchiHydrolexhRuntime();s=r.session();a=dict(instance_id=r.instance_id,capability_id='power.toggle',arguments={},request_id='x');self.assertEqual(s.invoke(**a),s.invoke(**a));self.assertTrue(r.powered)
 def test_unknown_instance(self):
  r=BuchiHydrolexhRuntime();s=r.session();self.assertEqual(s.invoke(instance_id='other',capability_id='power.toggle',arguments={},request_id='x')['error']['code'],'UNKNOWN_INSTANCE')
 def test_instance_isolation(self):
  a=configured(fast=True);b=BuchiHydrolexhRuntime();a.step(1);self.assertEqual(b.time_s,0);self.assertFalse(b.powered);self.assertEqual(len(b.channels),0)
 def test_step_invalid(self):
  r=BuchiHydrolexhRuntime()
  for v in [-1,float('nan'),float('inf'),True,7201]:
   with self.assertRaises(ActionRejected):r.step(v)
 def test_read_never_steps(self):
  r=configured(fast=True);t=r.time_s
  for _ in range(10):r.session().read(instance_id=r.instance_id)
  self.assertEqual(r.time_s,t)
if __name__=='__main__':unittest.main()
