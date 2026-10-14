from pathlib import Path
import sys,unittest,copy,json,importlib.util,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.agilent_1290infinity import *
sp=importlib.util.spec_from_file_location('asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(sp);sp.loader.exec_module(tool)
OBSERVED=set()
def call(r,k,**a):
 v=r.invoke(k,**a);err=tool.validate_value(v,CAPS[k][1]);assert not err,err;OBSERVED.add(k);return v
def prepared(plant=None,method=None,sample=None):
 r=Agilent1290infinityRuntime();call(r,'plant.configure',**(plant or copy.deepcopy(PLANT_EXAMPLE)));call(r,'method.configure',**(method or copy.deepcopy(METHOD_EXAMPLE)));call(r,'drawer.set',position_m=.2);call(r,'sample.load',**(sample or copy.deepcopy(SAMPLE_EXAMPLE)));call(r,'drawer.set',position_m=0.);call(r,'power.set',enabled=True);call(r,'pump.set',enabled=True);r.step(10.);return r
class SEC(unittest.TestCase):
 def test_metadata_and_schema(self):
  r=prepared();m=r.describe()
  for k,c in m['capabilities'].items():
   errors=tool.validate_value(c['example_arguments'],c['input_schema']);self.assertEqual(errors,[])
  for k,v in r.read('evaluator').items():errors=tool.validate_value(v,m['state_slots'][k]['value_schema']);self.assertEqual(errors,[])
 def test_setpoint_and_pure_queries(self):
  r=Agilent1290infinityRuntime();call(r,'plant.configure',**PLANT_EXAMPLE);t=list(r.temperature);call(r,'method.configure',**METHOD_EXAMPLE);self.assertEqual(r.temperature,t);before=copy.deepcopy(r.__dict__);r.read();r.describe();call(r,'process.measure');call(r,'ledger.read');self.assertEqual(r.__dict__,before)
 def test_pressure_derating_and_atomic_rejection(self):
  p=copy.deepcopy(PLANT_EXAMPLE)
  for c in p['columns']:c.update(resistance_pa_s_m3=1.2e15,max_pressure_pa=1.3e8)
  r=Agilent1290infinityRuntime();call(r,'plant.configure',**p);before=copy.deepcopy(r.__dict__)
  with self.assertRaisesRegex(ActionRejected,'PRESSURE_LIMIT'):r.invoke('method.configure',**dict(METHOD_EXAMPLE,flow_m3_s=5e-6/60))
  self.assertEqual(r.__dict__,before)
 def test_invalid_nan_bool_extra(self):
  r=prepared()
  for a in [{'enabled':1},{'enabled':True,'extra':3}]:
   with self.assertRaises(ActionRejected):r.invoke('pump.set',**a)
  for dt in [True,float('nan'),-1.,float('inf')]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(ActionRejected):r.step(dt)
   self.assertEqual(r.__dict__,before)
 def test_all_templates_and_families(self):
  r=Agilent1290infinityRuntime();self.assertEqual(len(r.templates),73);self.assertEqual(len({v['polymer_family'] for v in r.templates.values()}),3)
  for sid in r.templates:t=call(r,'template.read',substance_id=sid);self.assertGreater(t['mp_kg_mol'],0);self.assertTrue(t['template_source'])
 def test_configuration_and_access_motion(self):
  r=prepared()
  for k,a in [('door.set',{'angle_rad':1.}),('drawer.set',{'position_m':.2}),('column_cover.set',{'angle_rad':1.})]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaisesRegex(ActionRejected,'BUSY'):r.invoke(k,**a)
   self.assertEqual(r.__dict__,before)
  call(r,'pump.set',enabled=False);call(r,'door.set',angle_rad=1.);call(r,'door.set',angle_rad=0.);call(r,'column_cover.set',angle_rad=1.);call(r,'column_cover.set',angle_rad=0.)
 def test_complete_conservation(self):
  r=prepared();call(r,'acquisition.start')
  for _ in range(120):r.step(1.)
  self.assertEqual(r.phase,'succeeded');l=call(r,'ledger.read');self.assertLess(abs(l['volume_residual_m3']),1e-12);self.assertLess(abs(l['mass_residual_kg']),1e-20);self.assertGreater(l['eluted_kg'],0.);self.assertAlmostEqual(l['injected_kg'],2e-9)
 def test_cancel_partial_and_other_instance(self):
  r=prepared();other=prepared();before=copy.deepcopy(other.__dict__);call(r,'acquisition.start');r.step(60.);call(r,'acquisition.stop');l=r._ledger();r.step(10.);self.assertEqual(l,r._ledger());self.assertEqual(other.__dict__,before);self.assertGreater(l['column_kg'],0.);self.assertGreater(l['eluted_kg'],0.)
 def test_exhaustion_failure(self):
  p=copy.deepcopy(PLANT_EXAMPLE);p['reservoir_m3']=2e-7;r=prepared(p);call(r,'acquisition.start');r.step(120);self.assertEqual(r.phase,'failed');self.assertAlmostEqual(r.reservoir,0.);self.assertLess(abs(r._ledger()['volume_residual_m3']),1e-12)
 def test_unload_preserves_column(self):
  r=prepared();call(r,'acquisition.start');r.step(60);call(r,'acquisition.stop');x=r._ledger()['column_kg'];call(r,'drawer.set',position_m=.2);call(r,'sample.unload');self.assertEqual(r._ledger()['column_kg'],x);self.assertLess(abs(r._ledger()['volume_residual_m3']),1e-12)
 def test_calibration_moments_from_signal(self):
  r=prepared();call(r,'calibration.fit',**CAL_EXAMPLE);call(r,'acquisition.start')
  for _ in range(1200):r.step(.1)
  a=call(r,'molecular_weight.analyze');self.assertEqual(a['quality'],'estimated_relative');self.assertAlmostEqual(a['mass_kg'],2e-9,delta=1e-12)
  # Independent analytic logM mixture moments from nominal source material values.
  D=1.02;expected_mw=(20.+100.)/2*math.sqrt(D);expected_mn=2/(1/20.+1/100.)/math.sqrt(D)
  self.assertAlmostEqual(a['mw_kg_mol']/expected_mw,1,delta=.003);self.assertAlmostEqual(a['mn_kg_mol']/expected_mn,1,delta=.003)
  r.sample['solutes'][0]['concentration_kg_m3']=50.;r.sample['sample_id']='secret_changed';self.assertEqual(call(r,'molecular_weight.analyze'),a)
 def test_invalid_calibration_and_outside(self):
  r=prepared();before=copy.deepcopy(r.__dict__)
  with self.assertRaises(ActionRejected):r.invoke('calibration.fit',**dict(CAL_EXAMPLE,points=[CAL_EXAMPLE['points'][0]]*2))
  self.assertEqual(r.__dict__,before);self.assertEqual(call(r,'molecular_weight.analyze')['quality'],'incomplete');call(r,'acquisition.start');r.step(120);self.assertEqual(call(r,'molecular_weight.analyze')['quality'],'uncalibrated')
  call(r,'calibration.fit',**dict(CAL_EXAMPLE,points=[dict(elution_volume_m3=2e-7,mp_kg_mol=1e4),dict(elution_volume_m3=3e-7,mp_kg_mol=1e3)]));self.assertEqual(call(r,'molecular_weight.analyze')['quality'],'outside_calibration')
 def test_saturated_signal_is_not_quantified(self):
  s=copy.deepcopy(SAMPLE_EXAMPLE)
  for v in s['solutes']:v['concentration_kg_m3']=100.
  r=prepared(sample=s);call(r,'calibration.fit',**CAL_EXAMPLE);call(r,'acquisition.start')
  for _ in range(1200):r.step(.1)
  self.assertTrue(r.saturated);self.assertEqual(call(r,'molecular_weight.analyze')['quality'],'saturated')
 def test_session_truth_dedup_cancel_owner(self):
  r=prepared();r.register_principal('a');r.register_principal('b');r.register_principal('e','evaluator');a=r.session('a');b=r.session('b');i=r.instance_id
  self.assertNotIn('sample.identity',a.read(instance_id=i)['observations']);self.assertNotIn('ledger.read',a.describe(instance_id=i)['capabilities']);denied=a.invoke(instance_id=i,capability_id='ledger.read',arguments={},request_id='d');self.assertEqual(denied['error']['code'],'ACCESS_DENIED')
  v=a.invoke(instance_id=i,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(v['status'],'accepted');self.assertEqual(v,a.invoke(instance_id=i,capability_id='acquisition.start',arguments={},request_id='one'));op=v['operation_id']
  with self.assertRaises(ActionRejected):b.poll(op)
  r.step(60);self.assertEqual(a.poll(op)['status'],'running');a.cancel(op,'stop');self.assertEqual(a.poll(op)['status'],'cancelled')
 def test_partition_mass_and_signal_area(self):
  rs=[prepared(),prepared()]
  for r in rs:call(r,'acquisition.start')
  rs[0].step(120.)
  for _ in range(1200):rs[1].step(.1)
  for k in ['column_kg','eluted_kg','reservoir_m3','waste_m3']:self.assertAlmostEqual(rs[0]._ledger()[k],rs[1]._ledger()[k],delta=1e-12)
  area=lambda r:sum(p['delta_n']*p['window_m3'] for p in r.points);self.assertAlmostEqual(area(rs[0]),area(rs[1]),delta=1e-20)
 def test_eight_positions_and_temperature_bounds(self):
  for i in range(8):
   r=Agilent1290infinityRuntime();call(r,'plant.configure',**PLANT_EXAMPLE);call(r,'method.configure',**dict(METHOD_EXAMPLE,column_index=i));self.assertEqual(r.read()['column.selected'],i)
  with self.assertRaises(ActionRejected):r.invoke('method.configure',**dict(METHOD_EXAMPLE,column_index=8))
  with self.assertRaises(ActionRejected):r.invoke('method.configure',**dict(METHOD_EXAMPLE,zone_targets_k=[273.,300.]))
 def test_unsupported(self):
  r=prepared()
  for k in UNSUPPORTED:
   with self.assertRaisesRegex(ActionRejected,'UNSUPPORTED'):r.invoke(k)
if __name__=='__main__':unittest.main()
