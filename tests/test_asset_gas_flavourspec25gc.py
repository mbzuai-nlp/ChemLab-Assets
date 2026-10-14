from pathlib import Path
import sys,unittest,copy,json,importlib.util,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.gas_flavourspec25gc import *
sp=importlib.util.spec_from_file_location('asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(sp);sp.loader.exec_module(tool)
OBSERVED=set()
def call(r,k,**a):
 v=r.invoke(k,**a);err=tool.validate_value(v,CAPS[k][1]);assert not err,err;OBSERVED.add(k);return v
def prepared(plant=None,method=None,sample=None):
 r=GasFlavourspec25gcRuntime();call(r,'plant.configure',**(plant or copy.deepcopy(PLANT_EXAMPLE)));call(r,'method.configure',**(method or copy.deepcopy(METHOD_EXAMPLE)));call(r,'sample.load',**(sample or copy.deepcopy(SAMPLE_EXAMPLE)));call(r,'power.set',enabled=True);r.step(20.);call(r,'gas.set',enabled=True);return r
class GCIMS(unittest.TestCase):
 def test_metadata_schema(self):
  r=prepared();m=r.describe()
  for k,c in m['capabilities'].items():self.assertEqual(tool.validate_value(c['example_arguments'],c['input_schema']),[],k)
  for k,v in r.read('evaluator').items():self.assertEqual(tool.validate_value(v,m['state_slots'][k]['value_schema']),[],k)
 def test_pure_queries_and_targets(self):
  r=GasFlavourspec25gcRuntime();call(r,'plant.configure',**PLANT_EXAMPLE);t=copy.deepcopy(r.temperature);call(r,'method.configure',**METHOD_EXAMPLE);self.assertEqual(r.temperature,t);before=copy.deepcopy(r.__dict__);r.read();r.describe();call(r,'process.measure');call(r,'ledger.read');self.assertEqual(r.__dict__,before)
 def test_manufacturer_bounds_atomic(self):
  r=GasFlavourspec25gcRuntime();call(r,'plant.configure',**PLANT_EXAMPLE)
  for field,value in [('gc_k',373.16),('ims_k',373.16),('injector_k',473.16),('column_length_m',60.01),('accept_method_transfer',False)]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(ActionRejected):r.invoke('method.configure',**dict(METHOD_EXAMPLE,**{field:value}))
   self.assertEqual(r.__dict__,before)
 def test_invalid_inputs(self):
  r=prepared()
  for a in [{'enabled':1},{'enabled':True,'extra':3}]:
   with self.assertRaises(ActionRejected):r.invoke('gas.set',**a)
  for dt in [True,float('nan'),-1,float('inf')]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(ActionRejected):r.step(dt)
   self.assertEqual(r.__dict__,before)
 def test_all_templates_77_unique_80_channels(self):
  r=GasFlavourspec25gcRuntime();self.assertEqual(len(r.templates),77);self.assertEqual(sum(len(t['channels']) for t in r.templates.values()),80)
  for sid in r.templates:
   t=call(r,'template.read',substance_id=sid);self.assertGreater(min(t['retention_times_s']),0);self.assertGreater(min(t['drift_ratios']),.5)
 def test_sixty_positions_and_bad_sample(self):
  self.assertEqual(len({tuple(vial_target(i)) for i in range(60)}),60)
  for idx in [0,9,50,59]:
   r=prepared(sample=dict(SAMPLE_EXAMPLE,vial_index=idx));call(r,'acquisition.start');r.step(11);self.assertEqual(r.q,vial_target(idx))
  r=GasFlavourspec25gcRuntime();call(r,'plant.configure',**PLANT_EXAMPLE)
  for s in [dict(SAMPLE_EXAMPLE,vial_index=60),dict(SAMPLE_EXAMPLE,headspace_m3=15e-6,vial_m3='10mL'),dict(SAMPLE_EXAMPLE,solutes=[dict(substance_id='fake',mole_fraction=.2)])]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(ActionRejected):r.invoke('sample.load',**s)
   self.assertEqual(r.__dict__,before)
 def test_ready_interlock_and_motion(self):
  r=prepared();call(r,'gas.set',enabled=False);call(r,'cover.set',angle_rad=1.)
  with self.assertRaises(ActionRejected):r.invoke('gas.set',enabled=True)
  call(r,'cover.set',angle_rad=0);call(r,'gantry.move',x_m=.1,y_m=.2,z_m=.1);self.assertEqual(r.q,[.1,.2,.1]);call(r,'gas.set',enabled=True);call(r,'acquisition.start')
  for k,a in [('cover.set',dict(angle_rad=1.)),('gantry.move',dict(x_m=0.,y_m=0.,z_m=0.)),('sample.unload',{})]:
   with self.assertRaisesRegex(ActionRejected,'BUSY'):r.invoke(k,**a)
 def test_complete_moles_and_signal(self):
  r=prepared();call(r,'acquisition.start')
  for _ in range(198):r.step(1.)
  self.assertEqual(r.phase,'succeeded');l=call(r,'ledger.read');self.assertLess(abs(l['total_residual_mol']),1e-12);self.assertLess(abs(l['analyte_residual_mol']),1e-20);self.assertEqual(l['syringe_mol'],0.);self.assertGreater(l['exhaust_mol'],0.);area=call(r,'signal.integrate',**ROI_EXAMPLE)['area_s'];expected=sum(b['eluted'] for b in r.bands)*r.plant['gain_s_per_mol'];self.assertAlmostEqual(area,expected,delta=1e-6);self.assertTrue(call(r,'process.measure')['complete'])
 def test_cancel_preserves_syringe_and_purge(self):
  r=prepared();call(r,'acquisition.start');r.step(13.);self.assertIsNotNone(r.syringe);call(r,'acquisition.stop');l=r._ledger();r.step(100);self.assertEqual(l,r._ledger());call(r,'sample.unload');self.assertEqual(r._ledger()['syringe_mol'],l['syringe_mol']);call(r,'syringe.purge');self.assertEqual(r._ledger()['syringe_mol'],0.);self.assertLess(abs(r._ledger()['total_residual_mol']),1e-12)
 def test_cancel_after_injection_preserves_column(self):
  r=prepared();call(r,'acquisition.start');r.step(50);call(r,'acquisition.stop');l=r._ledger();self.assertGreater(l['column_mol'],0);r.step(30);self.assertEqual(l,r._ledger());call(r,'sample.unload');self.assertEqual(r._ledger()['column_mol'],l['column_mol']);self.assertLess(abs(r._ledger()['total_residual_mol']),1e-12)
 def test_finite_gas_failure(self):
  r=prepared(plant=dict(PLANT_EXAMPLE,gas_supply_mol=1e-4));call(r,'acquisition.start');r.step(500);self.assertEqual(r.phase,'failed');self.assertLess(r.supply,1e-15);self.assertLess(abs(r._ledger()['total_residual_mol']),1e-12)
 def test_partition_invariance(self):
  rs=[prepared(),prepared()]
  for r in rs:call(r,'acquisition.start')
  rs[0].step(198)
  for _ in range(1980):rs[1].step(.1)
  for k in ['column_mol','exhaust_mol','sample_remaining_mol','gas_remaining_mol']:self.assertAlmostEqual(rs[0]._ledger()[k],rs[1]._ledger()[k],delta=1e-11)
  self.assertAlmostEqual(call(rs[0],'signal.integrate',**ROI_EXAMPLE)['area_s'],call(rs[1],'signal.integrate',**ROI_EXAMPLE)['area_s'],delta=1e-7)
 def test_observed_calibration_and_fingerprint(self):
  r=prepared();call(r,'calibration.fit',**CAL_EXAMPLE);call(r,'acquisition.start')
  for _ in range(198):r.step(1.)
  a=call(r,'concentration.analyze');area=call(r,'signal.integrate',**ROI_EXAMPLE)['area_s'];self.assertEqual(a['quality'],'estimated');self.assertAlmostEqual(a['concentration_mol_m3'],area/500.)
  r.sample['sample_id']='hidden';r.sample['species']={};self.assertEqual(call(r,'concentration.analyze'),a)
  features=call(r,'fingerprint.compare',reference=[0.]*1920)['features'];self.assertAlmostEqual(call(r,'fingerprint.compare',reference=features)['cosine_similarity'],1.)
 def test_invalid_calibration_and_incomplete(self):
  r=prepared();self.assertEqual(call(r,'concentration.analyze')['quality'],'incomplete');before=copy.deepcopy(r.__dict__)
  with self.assertRaises(ActionRejected):r.invoke('calibration.fit',**dict(CAL_EXAMPLE,standards=[CAL_EXAMPLE['standards'][0]]*2))
  self.assertEqual(r.__dict__,before)
 def test_session_owner_dedup_and_isolation(self):
  r=prepared();other=prepared();before=copy.deepcopy(other.__dict__);r.register_principal('a');r.register_principal('b');a=r.session('a');b=r.session('b');i=r.instance_id
  self.assertNotIn('sample.identity',a.read(instance_id=i)['observations']);self.assertNotIn('ledger.read',a.describe(instance_id=i)['capabilities']);self.assertEqual(a.invoke(instance_id=i,capability_id='ledger.read',arguments={},request_id='d')['error']['code'],'ACCESS_DENIED')
  v=a.invoke(instance_id=i,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(v['status'],'accepted');self.assertEqual(v,a.invoke(instance_id=i,capability_id='acquisition.start',arguments={},request_id='one'));op=v['operation_id']
  with self.assertRaises(ActionRejected):b.poll(op)
  r.step(13);a.cancel(op,'stop');self.assertEqual(a.poll(op)['status'],'cancelled');self.assertEqual(other.__dict__,before)
 def test_unsupported(self):
  r=prepared()
  for k in UNSUPPORTED:
   with self.assertRaisesRegex(ActionRejected,'UNSUPPORTED'):r.invoke(k)
if __name__=='__main__':unittest.main()
