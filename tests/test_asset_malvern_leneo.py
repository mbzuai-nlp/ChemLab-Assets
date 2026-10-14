import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.malvern_leneo import *
def configured(fast=False,solution=False,mode=1):
 r=MalvernLeneoRuntime();p=copy.deepcopy(PLANT_EXAMPLE);m=copy.deepcopy(METHOD_EXAMPLE);s=copy.deepcopy(SAMPLE_EXAMPLE)
 if fast:
  p.update(chamber_C_J_K=100.,crucible_C_J_K=10.,receiver_C_J_K=10.,chamber_UA_W_K=.1,charge_UA_W_K=30.,fan_UA_W_K=10.,heater_W=4800.,supply_V=240.,controller_W_K=1000.,solidus_K=500.,liquidus_K=600.,latent_J_kg=10000.,dissolution_rate_s=2.,viscosity_Pa_s=.1,viscosity_activation_K=0.,pour_kg_s=.005,solution_rate_s=2.)
  m.update(segments=[dict(duration_s=6.,end_temperature_K=700.),dict(duration_s=12.,end_temperature_K=700.)],host_pour_profiles=[dict(angle_rad=a,duration_s=4.) for a in [1.6,1.8,2.,2.2]],minimum_cooling_s=2.)
 if solution:m['output']='borate_solution';s['solvent_kg']=.1
 m['pour_mode']=mode
 for k,a in [('plant.configure',p),('method.configure',m),('sample.configure',s),('power.set',dict(enabled=True)),('door.set_open',dict(open=False))]:r.invoke(k,**a)
 r.step(1);return r
def complete(r,seconds=3000):
 r.invoke('acquisition.start')
 for j in range(seconds):
  r.step(1)
  if r.phase!='running':break
 return r
def balances(r):
 q=r._product();return [q['initial_sample_kg']-sum(q[k] for k in ['crucible_sample_kg','receiver_sample_kg','lost_sample_kg','collected_sample_kg']),q['initial_flux_kg']-q['crucible_flux_kg']-q['receiver_flux_kg']-q['collected_flux_kg'],q['initial_solvent_kg']-q['receiver_solvent_kg']-q['collected_solvent_kg'],q['electrical_J']-q['storage_J']-q['loss_J']-q['advected_loss_J']-q['collected_enthalpy_J']]
class LeNeoTests(unittest.TestCase):
 def test_set_target_not_heat(self):
  r=configured(True);t=r.chamber;r.invoke('thermal.set_temperature',temperature_K=1473.15);self.assertEqual(r.chamber,t);r.step(2);self.assertEqual(r.chamber,t)
 def test_default_complete(self):self.assertEqual(complete(configured()).phase,'succeeded')
 def test_fast_complete(self):self.assertEqual(complete(configured(True),150).phase,'succeeded')
 def test_four_modes(self):
  for i in range(1,5):self.assertEqual(complete(configured(True,mode=i),150).phase,'succeeded')
 def test_solution(self):
  r=complete(configured(True,True),150);self.assertEqual(r.phase,'succeeded');self.assertGreater(r.rs,0);self.assertEqual(r.solvent,.1)
 def test_finite_collect(self):
  r=complete(configured(True),150);r.invoke('door.set_open',open=True);r.step(1);old=r.rs+r.rf;self.assertAlmostEqual(r.invoke('product.collect',fraction=.5,recipient_id='XRF')['transferred_kg'],old/2)
  for v in balances(r):self.assertLess(abs(v),1e-5)
 def test_ledgers(self):
  r=configured(True);r.invoke('acquisition.start')
  for i in range(80):
   r.step(1)
   for v in balances(r):self.assertLess(abs(v),1e-5)
 def test_flow_not_teleport(self):
  r=configured(True);r.invoke('acquisition.start');r.step(18);self.assertEqual(r.rs+r.rf,0);r.step(.1);self.assertLess(r.rs+r.rf,r.sample['sample_kg']+r.sample['flux_kg'])
 def test_emergency(self):
  r=configured(True);r.invoke('acquisition.start');r.step(8);r.invoke('emergency.stop');old=r.electrical;r.step(1);self.assertEqual(old,r.electrical);self.assertTrue(r.locked());self.assertEqual(r.phase,'failed')
 def test_hot_door(self):
  r=configured(True);r.invoke('acquisition.start');r.step(8);r.invoke('acquisition.stop')
  with self.assertRaises(ActionRejected):r.invoke('door.set_open',open=True)
 def test_cancel(self):
  r=configured(True);r.invoke('acquisition.start');r.step(8);mass=r.ms+r.mf;r.invoke('acquisition.stop');r.step(2);self.assertEqual(r.ms+r.mf,mass)
 def test_power_cancel(self):
  r=configured(True);r.invoke('acquisition.start');r.invoke('power.toggle');self.assertEqual(r.phase,'cancelled')
 def test_door_actual(self):
  r=configured(True);r.invoke('door.set_open',open=True)
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_missing_config(self):
  with self.assertRaises(ActionRejected):MalvernLeneoRuntime().invoke('acquisition.start')
 def test_one_position(self):
  with self.assertRaises(ActionRejected):configured(True).invoke('sample.configure',**SAMPLE_EXAMPLE)
 def test_voltage_power(self):
  p=dict(PLANT_EXAMPLE,heater_W=4800.,supply_V=208.)
  with self.assertRaises(ActionRejected):MalvernLeneoRuntime().invoke('plant.configure',**p)
 def test_bad_phase_range(self):
  with self.assertRaises(ActionRejected):MalvernLeneoRuntime().invoke('plant.configure',**dict(PLANT_EXAMPLE,liquidus_K=999.))
 def test_mode_count(self):
  with self.assertRaises(ActionRejected):MalvernLeneoRuntime().invoke('method.configure',**dict(METHOD_EXAMPLE,host_pour_profiles=[]))
 def test_acceptance_required(self):
  with self.assertRaises(ActionRejected):MalvernLeneoRuntime().invoke('plant.configure',**dict(PLANT_EXAMPLE,accept_uncalibrated_model=False))
 def test_temperature_limit(self):
  with self.assertRaises(ActionRejected):configured().invoke('thermal.set_temperature',temperature_K=1474.)
 def test_invalid_steps_atomic(self):
  r=configured(True);old=r.debug_snapshot()
  for x in [True,-1,float('nan'),float('inf'),7201]:
   with self.assertRaises(ActionRejected):r.step(x)
   self.assertEqual(old,r.debug_snapshot())
 def test_query_pure(self):
  r=configured(True);old=r.debug_snapshot()
  for k in ['fusion.measure','product.read']:r.invoke(k)
  r.invoke('reference.lookup',substance_id='janaf_al_096',temperature_K=1000.);self.assertEqual(old,r.debug_snapshot())
 def test_real_cp(self):
  r=MalvernLeneoRuntime();t=r.templates['janaf_al_096'];self.assertAlmostEqual(r.reference('janaf_al_096',298.15)['cp_J_kg_K'],79.015/t['molar_mass_kg_mol'])
 def test_reference_no_extrapolation(self):
  with self.assertRaises(ActionRejected):MalvernLeneoRuntime().reference('janaf_al_096',1501.)
 def test_unknown_reference(self):
  with self.assertRaises(ActionRejected):MalvernLeneoRuntime().reference('fake',1000.)
 def test_truth_hidden(self):
  r=configured(True);self.assertFalse(any(x.startswith('truth.') for x in r.read()));self.assertIn('truth.ledger',r.read('evaluator'))
 def test_principal_filter(self):
  r=configured(True);x=r.session().invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='p');self.assertEqual(x['error']['code'],'ACCESS_DENIED')
 def test_request_idempotency(self):
  r=configured(True);s=r.session();a=dict(instance_id=r.instance_id,capability_id='power.toggle',arguments={},request_id='x');x=s.invoke(**a);self.assertEqual(x,s.invoke(**a));self.assertFalse(r.powered)
 def test_request_conflict(self):
  r=configured(True);s=r.session();s.invoke(instance_id=r.instance_id,capability_id='power.toggle',arguments={},request_id='x');x=s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments=dict(enabled=True),request_id='x');self.assertEqual(x['error']['code'],'REQUEST_CONFLICT')
 def test_async(self):
  r=configured(True);s=r.session();x=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='x');self.assertEqual(x['status'],'accepted');self.assertEqual(s.poll(x['operation_id'])['status'],'running');s.cancel(x['operation_id'],'c');self.assertEqual(r.phase,'cancelled')
 def test_time_partition(self):
  a=configured(True);b=configured(True)
  for r in [a,b]:r.invoke('acquisition.start')
  a.step(2)
  for i in range(100):b.step(.02)
  self.assertAlmostEqual(a.chamber,b.chamber,8)
 def test_partial_fusion_failure(self):
  r=configured(True);r.method['segments']=[dict(duration_s=1,end_temperature_K=310.)];r.invoke('acquisition.start');r.step(2);self.assertEqual(r.stage,'incomplete_fusion')
if __name__=='__main__':unittest.main()
