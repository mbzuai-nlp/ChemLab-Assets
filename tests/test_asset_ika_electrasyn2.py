import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
import asset_tool
from asset_runtime.ika_electrasyn2 import *
def prepared(**changes):
 h=IkaElectrasyn2Runtime();h.invoke('power.set',enabled=True);h.invoke('cap.set_lift',lift_m=.08);h.invoke('sample.configure',**dict(EXAMPLE,**changes));h.invoke('cap.set_lift',lift_m=0.);h.invoke('electrodes.configure',**CAPS['electrodes.configure'][6]);return h
class ElectrasynTest(unittest.TestCase):
 def test_contract_schemas(self):
  meta=export_ika_electrasyn2_contract_metadata()
  for cap in meta['capabilities'].values():
   self.assertFalse(asset_tool.lint_schema(cap['input_schema']));self.assertFalse(asset_tool.lint_schema(cap['output_schema']));self.assertFalse(asset_tool.validate_value(cap['example_arguments'],cap['input_schema']))
 def test_real_library(self):
  lib=json.loads((ROOT/TEMPLATE_REL).read_text());self.assertEqual(len(lib['templates']),345);self.assertEqual(sum(x['runtime_lumped_eligible'] for x in lib['templates']),205)
  self.assertEqual(next(x for x in lib['templates'] if x['author_molecule_index']==2)['values']['first_reduction_v'],.597)
  ambiguous=next(x for x in lib['templates'] if x['author_molecule_index']==290);self.assertTrue(ambiguous['identity_conflicts']);self.assertFalse(ambiguous['runtime_lumped_eligible'])
  for x in lib['templates']:self.assertEqual(x['conditions']['reference_electrode'],'saturated calomel electrode (SCE)');self.assertEqual(x['evidence'],'measured_literature')
 def test_clock_and_target_only(self):
  h=prepared();h.invoke('stir.set_speed',speed_rad_s=601*RPM);self.assertEqual(h.actual_speed,0);self.assertAlmostEqual(h.target_speed,600*RPM);h.invoke('acquisition.start');self.assertEqual(h.charge,0);h.step(.2);self.assertGreater(h.charge,0)
  before=copy.deepcopy(h.__dict__)
  for _ in range(3):h.read();h.describe();h.invoke('electrochem.measure');h.invoke('history.read')
  self.assertEqual(before,h.__dict__)
 def test_rejections_atomic(self):
  h=IkaElectrasyn2Runtime()
  for cap,a in [('acquisition.start',{}),('stir.set_speed',{'speed_rad_s':10*RPM}),('cap.set_lift',{'lift_m':True}),('sample.configure',EXAMPLE),('method.configure',dict(CAPS['method.configure'][6],cell_voltage_v=30.))]:
   old=h.debug_snapshot()
   with self.assertRaises(ActionRejected):h.invoke(cap,**a)
   self.assertEqual(old,h.debug_snapshot())
  h=prepared();h.invoke('acquisition.start');old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('cap.set_lift',lift_m=.08)
  self.assertEqual(old,h.debug_snapshot())
 def test_current_charge_energy_and_inventories(self):
  h=prepared();h.invoke('acquisition.start');h.step(2.4);v=h.read('evaluator');self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.charge,.02,12);self.assertAlmostEqual(h.extent,.8*.02/F,14)
  self.assertAlmostEqual(v['truth.working_reduced_mol']+v['truth.working_oxidized_mol'],.001,14);self.assertAlmostEqual(v['truth.counter_reduced_mol']+v['truth.counter_oxidized_mol'],.001,14)
  self.assertAlmostEqual(v['truth.sample_charge_c'],v['truth.target_charge_c']+v['truth.side_charge_c'],13)
  self.assertAlmostEqual(v['truth.electrical_energy_j'],v['truth.ohmic_energy_j']+v['truth.redox_work_j'],12)
 def test_charge_stop_exact(self):
  h=prepared();h.invoke('method.configure',**dict(h.method,stop_by='charge',charge_target_c=.00123,duration_s=100.));h.invoke('acquisition.start');h.step(2.);self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.charge,.00123,14);self.assertAlmostEqual(h.elapsed_s,.123,12)
 def test_voltage_mode_and_current_compliance(self):
  h=prepared();h.invoke('method.configure',**dict(h.method,mode='constant_cell_voltage',cell_voltage_v=4.,duration_s=20.));h.invoke('acquisition.start');h.step(.1);self.assertGreater(h.current,0);self.assertLessEqual(h.current,.1);self.assertAlmostEqual(h.voltage,4.,8)
  h2=prepared(conductivity_s_m=100.,polarization_resistance_ohm=0.);h2.invoke('method.configure',**dict(h2.method,mode='constant_cell_voltage',cell_voltage_v=10.));h2.invoke('acquisition.start');h2.step(.1);self.assertEqual(h2.current,.1);self.assertLess(h2.voltage,10.)
 def test_compliance_cutoff(self):
  h=prepared(conductivity_s_m=.00001);h.invoke('acquisition.start');h.step(.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'voltage_limit');self.assertEqual(h.charge,0.)
 def test_capacity_exhaustion_and_timeout(self):
  h=prepared(working_mol=1e-8,counter_mol=1e-8);h.invoke('acquisition.start');h.step(2);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'formal_redox_inventory_exhausted');self.assertGreaterEqual(h.read('evaluator')['truth.counter_reduced_mol'],0)
  h=prepared();h.invoke('method.configure',**dict(h.method,mode='constant_cell_voltage',cell_voltage_v=0.,stop_by='charge',duration_s=.1));h.invoke('acquisition.start');h.step(1);self.assertEqual(h.fault,'charge_target_timeout');self.assertEqual(h.charge,0)
 def test_templates_affect_circuit(self):
  a=prepared(working_substance_id='elhajj2024_q003');b=prepared(working_substance_id='elhajj2024_q004')
  for h in [a,b]:h.invoke('acquisition.start');h.step(.2)
  self.assertNotEqual(a.voltage,b.voltage)
 def test_cancel_and_restart_residue(self):
  h=prepared();h.invoke('acquisition.start');h.step(.2);extent=h.extent;res=h._redox()[3];h.invoke('acquisition.stop');self.assertEqual(h.phase,'cancelled');self.assertEqual(h.extent,extent);self.assertEqual(h._redox()[3],res);q=h.sample_charge;h.invoke('acquisition.start');self.assertEqual(h.charge,0);h.step(.2);self.assertGreater(h.sample_charge,q)
 def test_two_instances_and_host_filter(self):
  a=prepared();b=IkaElectrasyn2Runtime('other');a.invoke('acquisition.start');a.step(.2);self.assertFalse(b.powered);s=a.session();self.assertNotIn('truth.current_a',s.read(instance_id=a.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=a.instance_id)['capabilities'])
  denied=s.invoke(instance_id=a.instance_id,capability_id='product.read',arguments={},request_id='denied');self.assertEqual(denied['error']['code'],'ACCESS_DENIED')
  self.assertFalse(any('truth' in k for p in s.invoke(instance_id=a.instance_id,capability_id='history.read',arguments={},request_id='history')['result']['records'] for k in p))
 def test_native_async_dedup_write_and_window(self):
  h=prepared();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**a);self.assertEqual(r['status'],'accepted');self.assertEqual(s.invoke(**a),r);h.step(.2);self.assertEqual(s.poll(r['operation_id'])['status'],'running');self.assertEqual(s.cancel(operation_id=r['operation_id'],request_id='cancel')['status'],'cancelled');self.assertGreater(h.charge,0)
  obs=s.read(instance_id=h.instance_id,state_ids=['electrochem.charge_c'])['observations']['electrochem.charge_c'];self.assertEqual(obs['sample_window_s'],[h.run_start_s,h.run_start_s+h.elapsed_s])
  self.assertEqual(s.write(instance_id=h.instance_id,state_id='stir.target_rad_s',value=600*RPM,request_id='speed')['status'],'succeeded')
 def test_all_actual_outputs_schema(self):
  h=IkaElectrasyn2Runtime()
  def checked(k,a):
   r=h.invoke(k,**a);self.assertFalse(asset_tool.validate_value(r,CAPS[k][1]),k);return r
  checked('cap.set_lift',{'lift_m':.08});checked('sample.configure',EXAMPLE);checked('cap.set_lift',{'lift_m':0.})
  for k in ['power.set','electrodes.configure','method.configure','stir.set_speed']:
   r=h.invoke(k,**CAPS[k][6]);self.assertFalse(asset_tool.validate_value(r,CAPS[k][1]))
  checked('acquisition.start',{});h.step(.2)
  for k in ['electrochem.measure','history.read','product.read','acquisition.stop']:
   self.assertFalse(asset_tool.validate_value(h.invoke(k),CAPS[k][1]))
  for k,v in h.read('evaluator').items():self.assertFalse(asset_tool.validate_value(v,SLOTS[k][0]),k)
  checked('stir.set_speed',{'speed_rad_s':0.});h.step(.05);checked('cap.set_lift',{'lift_m':.08});checked('sample.unload',{})
 def test_every_eligible_template_enters_circuit(self):
  templates=IkaElectrasyn2Runtime().templates;eligible=[x for x in templates.values() if x['runtime_lumped_eligible']];low=min(eligible,key=lambda x:x['values']['first_reduction_v']);high=max(eligible,key=lambda x:x['values']['first_reduction_v'])
  for rec in eligible:
   working,counter=(rec,high) if rec['values']['first_reduction_v']<high['values']['first_reduction_v'] else (low,rec)
   h=prepared(working_substance_id=working['substance_id'],counter_substance_id=counter['substance_id'],working_reduced_fraction=.5,counter_reduced_fraction=.5);h.invoke('acquisition.start');h.step(.05);self.assertGreater(h.invoke('electrochem.measure')['charge_c'],0,rec['substance_id'])
 def test_mass_volume_unload_and_unknown_yield(self):
  h=prepared();h.invoke('acquisition.start');h.step(2.);self.assertIsNone(h.invoke('electrochem.measure')['yield_fraction']);h.invoke('cap.set_lift',lift_m=.08);h.invoke('sample.unload');v=h.read('evaluator')
  for ax in ['kg','m3']:self.assertAlmostEqual(v['sample.input_'+ax],v['sample.retained_'+ax]+v['sample.removed_'+ax],14)
 def test_bad_time_and_titratable_rejected(self):
  h=IkaElectrasyn2Runtime()
  for dt in [0,-1,True,float('nan'),float('inf')]:
   with self.assertRaises(ValueError):h.step(dt)
  h.invoke('cap.set_lift',lift_m=.08)
  with self.assertRaises(ActionRejected):h.invoke('sample.configure',**dict(EXAMPLE,working_substance_id='elhajj2024_q290'))
if __name__=='__main__':unittest.main()
