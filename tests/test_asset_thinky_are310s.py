import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.thinky_are310s import *
def prepared(instance_id='test',plant=None,sample=None,method=None,closed=True):
 r=ThinkyAre310sRuntime(instance_id);p=copy.deepcopy(PLANT_EXAMPLE);p.update(plant or {});r.invoke('plant.configure',**p);m=copy.deepcopy(METHOD_EXAMPLE);m.update(method or {});r.invoke('method.configure',**m);s=example_sample();s.update(sample or {});r.invoke('sample.configure',**s);r.invoke('power.set',enabled=True);r.invoke('balance.set',gross_mass_kg=r.gross())
 if closed:r.invoke('lid.set_open',open=False);r.step(1.1)
 return r
class ThinkyTest(unittest.TestCase):
 def reject(self,r,cap,args,code=None):
  old=r.debug_snapshot()
  with self.assertRaises(ActionRejected) as x:r.invoke(cap,**args)
  if code:self.assertEqual(str(x.exception),code)
  self.assertEqual(old,r.debug_snapshot())
 def test_metadata(self):
  m=export_thinky_are310s_contract_metadata();self.assertEqual(len(m['capabilities']),14);self.assertEqual(set(m['state_slots']),set(ThinkyAre310sRuntime().read('evaluator')))
 def test_defaults_are_not_loaded(self):self.reject(ThinkyAre310sRuntime(),'acquisition.start',{})
 def test_only_step_moves_lid(self):
  r=prepared(closed=False);r.invoke('lid.set_open',open=False);self.assertEqual(r.lid_angle,1.5);r.step(.5);self.assertAlmostEqual(r.lid_angle,.75)
 def test_only_step_moves_rotor(self):
  r=prepared();r.invoke('acquisition.start');self.assertEqual(r.rpm,0);r.step(.5);self.assertAlmostEqual(r.rpm,1000);self.assertAlmostEqual(r.spin,400)
 def test_program_brake_completion(self):
  r=prepared();r.invoke('acquisition.start');r.step(7.1);self.assertEqual(r.phase,'running');self.assertGreater(r.rpm,0);r.step(1);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.rpm,0);self.assertAlmostEqual(r.elapsed_s,7)
 def test_family_mode_ratio(self):
  r=prepared();r.invoke('acquisition.start');r.step(2);self.assertAlmostEqual(r.spin,800);r.step(3);self.assertAlmostEqual(r.rpm,2200);self.assertAlmostEqual(r.spin,60)
 def test_zero_rpm_program(self):
  r=prepared(method=dict(program=[dict(mode='mix',duration_s=2.,revolution_rpm=0.)]));r.invoke('acquisition.start');r.step(3);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.heat,0);self.assertEqual(r.mixed,0)
 def test_wrong_mode_speed_atomic(self):
  r=prepared();m=copy.deepcopy(METHOD_EXAMPLE);m['program'][0]['revolution_rpm']=2100;self.reject(r,'method.configure',m,'INVALID_ARGUMENT')
 def test_bad_step_size_atomic(self):
  r=prepared();m=copy.deepcopy(METHOD_EXAMPLE);m['program'][0]['revolution_rpm']=205;self.reject(r,'method.configure',m)
 def test_noninteger_duration_atomic(self):
  r=prepared();m=copy.deepcopy(METHOD_EXAMPLE);m['program'][0]['duration_s']=1.5;self.reject(r,'method.configure',m)
 def test_total_30minute_limit(self):
  r=prepared();m=copy.deepcopy(METHOD_EXAMPLE);m['program'][0]['duration_s']=1800;self.reject(r,'method.configure',m)
 def test_gross_net_limits_distinct(self):
  for change in [dict(carrier_mass_kg=.25,solid_mass_kg=.01),dict(carrier_mass_kg=.20,solid_mass_kg=.02,cup_tare_kg=.1)]:
   r=ThinkyAre310sRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);a=example_sample();a.update(change);self.reject(r,'sample.configure',a,'CAPACITY_LIMIT')
 def test_volume_limit(self):
  r=ThinkyAre310sRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);a=example_sample();a.update(carrier_mass_kg=.14,solid_mass_kg=0.,carrier_density_kg_m3=500.);self.reject(r,'sample.configure',a,'CAPACITY_LIMIT')
 def test_150_cup_adapter(self):
  r=prepared(sample=dict(cup_volume_m3=.00015,adapter_mass_kg=.025,cup_tare_kg=.025));self.assertEqual(r.sample['cup_volume_m3'],.00015);self.assertAlmostEqual(r.gross(),.15)
 def test_150_missing_adapter_rejected(self):
  r=ThinkyAre310sRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);a=example_sample();a['cup_volume_m3']=.00015;self.reject(r,'sample.configure',a,'PRECONDITION_FAILED')
 def test_balance_guard(self):
  r=prepared(closed=False);r.invoke('balance.set',gross_mass_kg=.10);r.invoke('lid.set_open',open=False);r.step(1.1);self.reject(r,'acquisition.start',{})
 def test_start_open_guard(self):self.reject(prepared(closed=False),'acquisition.start',{})
 def test_running_reconfiguration_atomic(self):
  r=prepared();r.invoke('acquisition.start');r.step(1)
  for cap,a in [('balance.set',dict(gross_mass_kg=.14)),('method.configure',METHOD_EXAMPLE),('lid.set_open',dict(open=True)),('power.set',dict(enabled=False))]:self.reject(r,cap,a)
 def test_cancel_braking_lid_lock(self):
  r=prepared();r.invoke('acquisition.start');r.step(2);r.invoke('acquisition.stop');self.reject(r,'lid.set_open',dict(open=True));r.step(1);r.invoke('lid.set_open',open=True);r.step(1);self.assertAlmostEqual(r.lid_angle,1.5,12)
 def test_power_loss_no_auto_restart(self):
  r=prepared();r.invoke('acquisition.start');r.step(2);r.invoke('power.interrupt');r.step(1);self.assertEqual(r.phase,'cancelled');self.reject(r,'lid.set_open',dict(open=True));r.invoke('power.set',enabled=True);self.assertEqual(r.phase,'cancelled')
 def test_mass_gas_heat_conservation(self):
  r=prepared();r.invoke('acquisition.start');r.step(9);self.assertGreater(r.mixed,0);self.assertLess(r.gas,r.sample['entrained_gas_mol']);self.assertAlmostEqual(r.gas+r.headgas+r.collected_gas,r.sample['entrained_gas_mol'],14);self.assertAlmostEqual(r.heat,r.loss+r.storage+r.collected_heat,9);self.assertEqual(r.mass,r.initial)
 def test_transfer_heat_and_gas(self):
  r=prepared();r.invoke('acquisition.start');r.step(9);r.invoke('lid.set_open',open=True);r.step(1.1);r.invoke('product.collect',fraction=.25,recipient_id='receiver');self.assertAlmostEqual(sum(r.mass)+sum(r.collected),sum(r.initial));self.assertAlmostEqual(r.gas+r.headgas+r.collected_gas,r.sample['entrained_gas_mol'],14);self.assertAlmostEqual(r.heat,r.loss+r.storage+r.collected_heat,9);self.assertGreater(r.collected_heat,0)
 def test_higher_viscosity_slower(self):
  a=prepared();b=prepared(sample=dict(viscosity_Pa_s=10.))
  for r in [a,b]:r.invoke('acquisition.start');r.step(9)
  self.assertGreater(a.mixed,b.mixed);self.assertLess(a.gas,b.gas)
 def test_zero_kinetics(self):
  r=prepared(plant=dict(mixing_rate_s_1=0.,bubble_escape_rate_s_1=0.));r.invoke('acquisition.start');r.step(9);self.assertEqual(r.mixed,0);self.assertEqual(r.gas,r.sample['entrained_gas_mol'])
 def test_reference_boundary_failure(self):
  r=prepared(plant=dict(reference_dissipation_W=200.,vessel_heat_capacity_J_K=1.,cooling_W_K=0.),sample=dict(carrier_mass_kg=.001,solid_mass_kg=0.));r.invoke('acquisition.start');r.step(8);self.assertEqual(r.phase,'failed');self.assertLessEqual(r.temperature,r.bounds[1]+1e-8)
 def test_panel_missing_temperature(self):self.assertIsNone(prepared().invoke('mixing.measure')['temperature_K'])
 def test_public_truth_filter(self):
  r=prepared();s=r.session();self.assertFalse(any(k.startswith('truth.') for k in r.read()));self.assertEqual(s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='deny')['error']['code'],'ACCESS_DENIED')
 def test_query_is_pure(self):
  r=prepared();old=r.debug_snapshot();r.read();r.session().read(instance_id=r.instance_id);r.invoke('mixing.measure');self.assertEqual(old,r.debug_snapshot())
 def test_two_instances_isolated(self):
  a=prepared('a');b=prepared('b');old=b.debug_snapshot();a.invoke('acquisition.start');a.step(2);self.assertEqual(old,b.debug_snapshot())
 def test_79_references(self):
  r=prepared();self.assertEqual(len(r.templates),79)
  for sid,t in r.templates.items():x=r.invoke('reference.lookup',substance_id=sid);self.assertEqual(x['cp_J_kg_K'],t['value']);self.assertTrue(x['source'].startswith('https://webbook.nist.gov/'))
 def test_invalid_time_atomic(self):
  r=prepared()
  for v in [True,-1,float('nan'),float('inf'),3601]:
   old=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.step(v)
   self.assertEqual(old,r.debug_snapshot())
 def test_partition_consistency(self):
  a=prepared();b=prepared()
  for r in [a,b]:r.invoke('acquisition.start')
  a.step(9)
  for _ in range(90):b.step(.1)
  for k in ['temperature','mixed','gas','heat','angle','cup_angle']:self.assertAlmostEqual(getattr(a,k),getattr(b,k),7)
if __name__=='__main__':unittest.main()
