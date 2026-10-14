"""Meaningful invariants for CHEM-098; uses actual schema validator and public session."""
from pathlib import Path
import copy,importlib.util,json,math,sys,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import thermo_dionexintegrion as r
spec=importlib.util.spec_from_file_location('ic_contract_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
OBSERVED=set()
class IntegrionTests(unittest.TestCase):
 def host(self,i='ic'):
  h=r.ThermoDionexintegrionRuntime(i);return h
 def call(self,h,k,**a):
  v=h.invoke(k,**a);self.assertEqual(tool.validate_value(v,r.CAPS[k][1]),[],k);OBSERVED.add(k);return v
 def configured(self,i='ic',plant=None,method=None):
  h=self.host(i);self.call(h,'plant.configure',**(plant or r.PLANT_EXAMPLE));self.call(h,'method.configure',**(method or r.METHOD_EXAMPLE));return h
 def ready(self,i='ic',plant=None,method=None,sample=None):
  h=self.configured(i,plant,method);self.call(h,'sample.load',**(sample or r.SAMPLE_EXAMPLE));self.call(h,'power.set',enabled=True);self.call(h,'pump.set',enabled=True);h.step(10);return h
 def test_schema_metadata(self):
  m=r.export_thermo_dionexintegrion_contract_metadata()
  for k,c in m['capabilities'].items():
   self.assertEqual(tool.lint_schema(c['input_schema']),[],k);self.assertEqual(tool.lint_schema(c['output_schema']),[],k);self.assertEqual(tool.validate_value(c['example_arguments'],c['input_schema']),[],k)
 def test_only_step_advances_targets_and_physics(self):
  h=self.configured();old=h.temperature;self.call(h,'power.set',enabled=True);self.call(h,'pump.set',enabled=True);self.assertEqual(h.temperature,old);self.assertEqual(h.flow,0);self.assertEqual(h.time_s,0);h.step(1);self.assertGreater(h.temperature,old);self.assertGreater(h.flow,0)
 def test_reads_and_describe_pure(self):
  h=self.ready();h.register_principal('agent');s=h.session('agent');before=copy.deepcopy(h.__dict__)
  for _ in range(3):h.read();h.describe();s.read(instance_id='ic');s.describe(instance_id='ic');self.call(h,'process.measure');self.call(h,'template.read',substance_id='cl_anion1')
  self.assertEqual(before,h.__dict__)
 def test_atomic_invalid_and_rf_constraints(self):
  h=self.configured();before=copy.deepcopy(h.__dict__)
  for args in [dict(r.METHOD_EXAMPLE,flow_m3_s=4e-6/60),dict(r.METHOD_EXAMPLE,column_k=302.15),dict(r.METHOD_EXAMPLE,suppression_current_a=0),dict(r.METHOD_EXAMPLE,flow_m3_s=True),dict(r.METHOD_EXAMPLE,extra=1)]:
   with self.assertRaises(r.ActionRejected):h.invoke('method.configure',**args)
   self.assertEqual(before,h.__dict__)
 def test_pressure_rejection_atomic(self):
  h=self.host();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,hydraulic_resistance_pa_s_m3=1e17));old=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(r.ActionRejected,'PRESSURE_LIMIT'):h.invoke('method.configure',**r.METHOD_EXAMPLE)
  self.assertEqual(old,h.__dict__)
 def test_completion_and_species_volume_conservation(self):
  h=self.ready();self.call(h,'acquisition.start')
  for _ in range(60):h.step(1);v=self.call(h,'ledger.read');self.assertLess(abs(v['volume_residual_m3']),1e-14);self.assertLess(abs(v['species_residual_mol']),1e-20)
  self.assertEqual(h.phase,'succeeded');self.assertFalse(h.pump);self.assertEqual(len(h.points),60);self.assertGreater(max(x['conductivity_s_m'] for x in h.points),0);self.assertGreater(v['column_mol'],0)
  self.call(h,'process.measure');q=self.call(h,'quantification.integrate',start_s=0.,stop_s=60.,calibration_s_m_s_per_mol_m3=.02);self.assertGreater(q['concentration_mol_m3'],0)
 def test_cancel_preserves_partial_effect(self):
  h=self.ready();self.call(h,'acquisition.start');h.step(12);before=self.call(h,'ledger.read');self.call(h,'acquisition.stop');h.step(50);after=self.call(h,'ledger.read');self.assertEqual(before,after);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.elapsed,12);self.assertFalse(h._result()['complete'])
 def test_exhaustion_is_not_success(self):
  p=dict(r.PLANT_EXAMPLE,reservoir_m3=2e-7);h=self.ready(plant=p);h.invoke('acquisition.start');h.step(60);self.assertEqual(h.phase,'failed');self.assertAlmostEqual(h.reservoir,0);self.assertLess(abs(h._ledger()['volume_residual_m3']),1e-14)
 def test_sample_unload_does_not_remove_column_solute(self):
  h=self.ready();h.invoke('acquisition.start');h.step(10);h.invoke('acquisition.stop');col=h._ledger()['column_mol'];self.call(h,'sample.unload');self.assertAlmostEqual(h._ledger()['column_mol'],col);self.assertLess(abs(h._ledger()['volume_residual_m3']),1e-14)
 def test_all_templates_and_neutral_response(self):
  h=self.host();self.assertGreaterEqual(len(h.templates),50)
  for key,v in h.templates.items():
   out=self.call(h,'template.read',substance_id=key);self.assertAlmostEqual(out['molar_conductivity_s_m2_mol'],v['charge']**2*96485.33212**2*v['diffusion_m2_s']/(8.31446261815324*298.15),15)
   if v['charge']==0:self.assertEqual(out['molar_conductivity_s_m2_mol'],0)
 def test_truth_filter_errors_and_duplicate_requests(self):
  h=self.ready();h.register_principal('a','agent');h.register_principal('e','evaluator');a=h.session('a');e=h.session('e')
  self.assertNotIn('sample.identity',a.read(instance_id='ic')['observations']);self.assertIn('sample.identity',e.read(instance_id='ic')['observations']);self.assertNotIn('sample.load',a.describe(instance_id='ic')['capabilities'])
  bad=a.invoke(instance_id='ic',capability_id='ledger.read',arguments={},request_id='bad');self.assertEqual(bad['error']['code'],'ACCESS_DENIED');self.assertNotIn('aqueous_01',json.dumps(bad))
  kw=dict(instance_id='ic',capability_id='acquisition.start',arguments={},request_id='run');x=a.invoke(**kw);self.assertEqual(x['status'],'accepted');self.assertEqual(x,a.invoke(**kw));self.assertEqual(h.serial,1)
  y=a.invoke(**dict(kw,capability_id='process.measure'));self.assertEqual(y['error']['code'],'REQUEST_CONFLICT');h.step(4);before=h.time_s;a.poll(x['operation_id']);self.assertEqual(before,h.time_s);a.cancel(x['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled')
  with self.assertRaises(r.ActionRejected):e.poll(x['operation_id'])
 def test_instances_isolated(self):
  a=self.ready('a');b=self.ready('b');old=copy.deepcopy(b.__dict__);a.invoke('acquisition.start');a.step(8);self.assertEqual(old,b.__dict__)
 def test_doors_and_power_interlocks(self):
  h=self.host();self.call(h,'door.set',angle_rad=1.);self.call(h,'display_door.set',angle_rad=.5);self.call(h,'door.set',angle_rad=0.);self.call(h,'display_door.set',angle_rad=0.)
  h=self.ready();old=copy.deepcopy(h.__dict__)
  with self.assertRaises(r.ActionRejected):h.invoke('door.set',angle_rad=1.)
  self.assertEqual(old,h.__dict__);h.invoke('acquisition.start');h.step(1);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled')
 def test_step_partition_invariance(self):
  a=self.ready('a');b=self.ready('b');a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(30)
  for _ in range(30):b.step(1)
  self.assertAlmostEqual(a.bands[0]['eluted'],b.bands[0]['eluted'],20);ia=sum(x['conductivity_s_m']*x['window_s'] for x in a.points);ib=sum(x['conductivity_s_m']*x['window_s'] for x in b.points);self.assertAlmostEqual(ia,ib,14)
 def test_no_boolean_nan_unknown_or_unsupported_fake_success(self):
  h=self.host()
  for dt in [0,-1,True,math.nan,math.inf]:
   with self.assertRaises(r.ActionRejected):h.step(dt)
  for k in r.UNSUPPORTED:
   with self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED'):h.invoke(k)
  with self.assertRaisesRegex(r.ActionRejected,'UNKNOWN_CAPABILITY'):h.invoke('fictional')
if __name__=='__main__':unittest.main()
