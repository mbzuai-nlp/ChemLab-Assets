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
  m=export_thinky_are310s_contract_metadata();self.assertEqual(len(m['capabilities']),14+len(CONDITION_CAPS)+len(FAULT_CAPS)+len(PANEL_CAPS));self.assertEqual(set(m['state_slots']),set(ThinkyAre310sRuntime().read('evaluator')))
 def test_panel_conditions_memories(self):
  r=ThinkyAre310sRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);self.reject(r,'conditions.set_mode',dict(mode='STEP'),'PRECONDITION_FAILED');r.invoke('power.set',enabled=True)
  self.assertEqual(r.read()['conditions.current'],dict(mix_time_s=30.,mix_rpm=2000.,defoam_time_s=30.,defoam_rpm=2200.))
  self.reject(r,'conditions.set_step',dict(mix_time_s=10.,mix_rpm=1500.,defoam_time_s=0.,defoam_rpm=2200.),'INVALID_ARGUMENT');self.reject(r,'conditions.select_step',dict(number=2),'PRECONDITION_FAILED')
  r.invoke('conditions.set_mode',mode='STEP');r.invoke('conditions.select_memory',number=3);self.assertEqual(r.read()['conditions.current'],zero_step('STEP'))
  for n,t in [(1,10.),(2,20.),(3,5.)]:r.invoke('conditions.select_step',number=n);r.invoke('conditions.set_step',mix_time_s=t,mix_rpm=1500.,defoam_time_s=0.,defoam_rpm=0.)
  self.reject(r,'conditions.set_step',dict(mix_time_s=1.,mix_rpm=150.,defoam_time_s=0.,defoam_rpm=0.),'INVALID_ARGUMENT');self.reject(r,'conditions.set_step',dict(mix_time_s=1.5,mix_rpm=1500.,defoam_time_s=0.,defoam_rpm=0.),'INVALID_ARGUMENT')
  r.invoke('conditions.select_step',number=2);self.assertEqual(r.invoke('conditions.register'),dict(mode='STEP',number=3,steps=2));self.assertEqual([x['mix_time_s'] for x in r.memories['STEP'][2]],[10.,20.,0.,0.,0.])
  self.assertEqual([x['duration_s'] for x in r.panel_program()],[10.,20.,5.]);r.invoke('power.set',enabled=False);r.invoke('power.set',enabled=True)
  self.assertEqual(r.read()['conditions.mode'],'STEP');self.assertEqual([x['duration_s'] for x in r.panel_program()],[10.,20.])  # unregistered STEP 3 lost at power-off
  r.invoke('conditions.select_memory',number=4);self.assertEqual(r.panel_program(),[]);r.invoke('conditions.select_memory',number=3);self.assertEqual(r.step_no,1)
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
  for cap,a in [('balance.set',dict(gross_mass_kg=.14)),('method.configure',METHOD_EXAMPLE),('lid.set_open',dict(open=True))]:self.reject(r,cap,a)
  r.invoke('power.set',enabled=False);self.assertEqual((r.phase,r.mode,r.powered),('cancelled','power_loss_braking',False))   # the I/O switch is a hard switch (final sync)
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
class FixRoundB(unittest.TestCase):
 def test_wrong_counterbalance_names_the_cause(self):
  """no IMBALANCE code is declared, so a start with a wrong rotation balance keeps PRECONDITION_FAILED but says why."""
  r=prepared(closed=False);ag=r.session();n=[0]
  def why(cap='acquisition.start',**args):
   n[0]+=1;old=r.debug_snapshot();x=ag.invoke(instance_id=r.instance_id,capability_id=cap,arguments=args,request_id='ag2_%d'%n[0])
   self.assertEqual(x['error']['code'],'PRECONDITION_FAILED',x);self.assertEqual(old['phase'],r.phase);return x['error']['message']
  self.assertIn('lid is not closed',why());r.invoke('balance.set',gross_mass_kg=r.gross()+.01)
  r.invoke('lid.set_open',open=False);r.step(1.1)
  m=why();self.assertIn('rotation balance does not match',m);self.assertNotIn(repr(r.gross())[:6],m)   # the message gives no masses
  self.assertIn('open the lid fully',why('balance.set',gross_mass_kg=r.gross()));self.assertIn('no run is in progress',why('acquisition.stop'))
  r.invoke('lid.set_open',open=True);r.step(1.6);r.invoke('balance.set',gross_mass_kg=r.gross());r.invoke('lid.set_open',open=False);r.step(1.6)
  self.assertEqual(ag.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='ag2_ok')['status'],'accepted')
  r.invoke('power.set',enabled=False);self.assertIn('switched off',why());self.assertEqual(ASSET_VERSION,'1.2.1')
if __name__=='__main__':unittest.main()
