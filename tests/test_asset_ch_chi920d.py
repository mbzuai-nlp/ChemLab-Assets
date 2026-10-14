from pathlib import Path
import sys,unittest,copy,importlib.util,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.ch_chi920d import *
sp=importlib.util.spec_from_file_location('asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(sp);sp.loader.exec_module(tool)
OBSERVED=set()
def call(r,k,**a):
 v=r.invoke(k,**a);err=tool.validate_value(v,CAPS[k][1]);assert not err,err;OBSERVED.add(k);return v
def prepared(plant=None,method=None,sample=None):
 r=ChChi920dRuntime();call(r,'plant.configure',**(plant or copy.deepcopy(PLANT_EXAMPLE)));call(r,'method.configure',**(method or copy.deepcopy(METHOD_EXAMPLE)));call(r,'sample.load',**(sample or copy.deepcopy(SAMPLE_EXAMPLE)));call(r,'power.set',enabled=True);return r
class SECM(unittest.TestCase):
 def test_metadata_schema(self):
  r=prepared();m=r.describe()
  for k,c in m['capabilities'].items():self.assertEqual(tool.validate_value(c['example_arguments'],c['input_schema']),[],k)
  for k,v in r.read('evaluator').items():self.assertEqual(tool.validate_value(v,m['state_slots'][k]['value_schema']),[],k)
 def test_pure_queries_and_targets(self):
  r=prepared();q=r.q[:];call(r,'stage.move',x_m=.01,y_m=.01,z_m=.02);self.assertEqual(r.q,q);before=copy.deepcopy(r.__dict__);r.read();r.describe();call(r,'process.measure');call(r,'ledger.read');self.assertEqual(r.__dict__,before);r.step(1.);self.assertLess(math.dist(r.q,r.target),math.dist(q,r.target))
 def test_position_quantization_and_range(self):
  for fine,res in [(True,1.6e-9),(False,8e-9)]:
   r=prepared(plant=dict(PLANT_EXAMPLE,fine_enabled=fine));out=call(r,'stage.move',x_m=11e-9,y_m=0,z_m=.005);self.assertAlmostEqual(out['x_m']/res,round(11e-9/res));r.step(1);self.assertAlmostEqual(r.q[0],out['x_m'])
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(ActionRejected):r.invoke('stage.move',x_m=.025001,y_m=0,z_m=.005)
   self.assertEqual(r.__dict__,before)
 def test_limits_atomic_and_bad_types(self):
  r=prepared()
  for k,a in [('method.configure',dict(METHOD_EXAMPLE,tip_potential_v=10.01)),('method.configure',dict(METHOD_EXAMPLE,substrate_potential_v=-10.01)),('power.set',{'enabled':1}),('stage.move',dict(x_m=0.,y_m=0.,z_m=float('nan')))]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(ActionRejected):r.invoke(k,**a)
   self.assertEqual(r.__dict__,before)
  for dt in [True,float('nan'),-1.,float('inf')]:
   with self.assertRaises(ActionRejected):r.step(dt)
 def test_all_59_templates(self):
  r=prepared();self.assertEqual(len(r.templates),59)
  for sid in r.templates:self.assertGreater(call(r,'template.read',substance_id=sid)['diffusion_m2_s'],0)
 def test_complete_conservation_and_map(self):
  r=prepared();call(r,'acquisition.start');r.step(20);self.assertEqual(r.phase,'succeeded');l=call(r,'ledger.read');self.assertLess(abs(l['total_residual_mol']),1e-19);self.assertLess(abs(l['charge_residual_c']),1e-18);self.assertEqual(len(call(r,'process.measure')['points']),25);self.assertGreater(r.reduced,0.);self.assertGreater(max(p['tip_current_a'] for p in r.points),min(p['tip_current_a'] for p in r.points))
 def test_cancel_partial_and_unload(self):
  r=prepared();call(r,'acquisition.start');r.step(4.);self.assertGreater(r.tip_charge,0.);call(r,'acquisition.stop');l=r._ledger();q=r.q[:];r.step(5);self.assertEqual(l,r._ledger());self.assertEqual(q,r.q)
  with self.assertRaisesRegex(ActionRejected,'RETRACT_REQUIRED'):r.invoke('sample.unload')
  call(r,'stage.move',x_m=0,y_m=0,z_m=.005);r.step(5);call(r,'sample.unload');self.assertLess(abs(r._ledger()['total_residual_mol']),1e-19);self.assertGreater(r.tip_charge,0)
 def test_partition_invariance(self):
  rs=[prepared(),prepared()]
  for r in rs:call(r,'acquisition.start')
  rs[0].step(20.)
  for _ in range(2000):rs[1].step(.01)
  for k in ['oxidized_mol','reduced_mol','tip_charge_c','substrate_charge_c']:self.assertAlmostEqual(rs[0]._ledger()[k],rs[1]._ledger()[k],delta=1e-18)
  self.assertEqual([p['tip_current_a'] for p in rs[0].points],[p['tip_current_a'] for p in rs[1].points])
 def test_approach_current_and_guard(self):
  path=[dict(x_m=0,y_m=0,z_m=z) for z in [50e-6,20e-6,10e-6,5e-6,2e-6]];r=prepared(method=dict(METHOD_EXAMPLE,mode='approach',path=path));call(r,'acquisition.start');r.step(20);self.assertEqual(len(r.points),5);self.assertGreater(r.points[-1]['tip_current_a'],r.points[0]['tip_current_a'])
  call(r,'method.configure',**dict(METHOD_EXAMPLE,path=[dict(x_m=0,y_m=0,z_m=0.)]));before=copy.deepcopy(r.__dict__)
  with self.assertRaisesRegex(ActionRejected,'COLLISION_GUARD'):r.invoke('acquisition.start')
  self.assertEqual(r.__dict__,before)
 def test_negative_feedback(self):
  r=prepared(sample=dict(SAMPLE_EXAMPLE,base_rate_m_s=0.,patch_rate_m_s=0.));call(r,'acquisition.start');r.step(20);self.assertTrue(all(p['substrate_current_a']==0 for p in r.points));self.assertEqual(r.sub_charge,0.)
 def test_steady_dwell_and_current_limit(self):
  r=prepared(method=dict(METHOD_EXAMPLE,dwell_s=.001))
  with self.assertRaisesRegex(ActionRejected,'STEADY_STATE_DWELL'):r.invoke('acquisition.start')
  r=prepared(plant=dict(PLANT_EXAMPLE,current_range_a=1e-11));call(r,'acquisition.start');r.step(20);self.assertEqual(r.phase,'failed');self.assertEqual(r.failure,'CURRENT_LIMIT');self.assertEqual(r.tip_charge,0.)
 def test_total_two_channel_continuous_limit(self):
  p=dict(PLANT_EXAMPLE,tip_radius_m=1e-4,current_range_a=.25);m=dict(METHOD_EXAMPLE,path=[dict(x_m=0.,y_m=0.,z_m=11e-6)],dwell_s=30.);sample=dict(SAMPLE_EXAMPLE,concentration_mol_m3=5e4);r=prepared(p,m,sample);call(r,'acquisition.start');r.step(60.);self.assertEqual(r.phase,'failed');self.assertEqual(r.failure,'CURRENT_LIMIT');self.assertEqual(r.tip_charge,0.);self.assertEqual(r.reduced,0.)
 def test_kinetics_uses_observed_currents(self):
  r=prepared(plant=dict(PLANT_EXAMPLE,current_range_a=1e-7));call(r,'acquisition.start');r.step(20);D=r.templates[SAMPLE_EXAMPLE['substance_id']]['diffusion_m2_s'];a=call(r,'kinetics.estimate',**dict(KIN_EXAMPLE,diffusion_m2_s=D));self.assertEqual(a['quality'],'estimated_surrogate');self.assertAlmostEqual(a['rate_m_s'][12]/.001001,1.,delta=.02)
  r.sample['base_rate_m_s']=.9;r.sample['patch_rate_m_s']=0;r.sample['sample_id']='changed_secret';self.assertEqual(call(r,'kinetics.estimate',**dict(KIN_EXAMPLE,diffusion_m2_s=D)),a)
 def test_session_truth_owner_stale(self):
  r=prepared();r.register_principal('a');r.register_principal('b');a=r.session('a');b=r.session('b');i=r.instance_id;self.assertNotIn('sample.identity',a.read(instance_id=i)['observations']);self.assertNotIn('ledger.read',a.describe(instance_id=i)['capabilities']);v=a.invoke(instance_id=i,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(v,a.invoke(instance_id=i,capability_id='acquisition.start',arguments={},request_id='one'))
  with self.assertRaises(ActionRejected):b.poll(v['operation_id'])
  r.step(4);a.cancel(v['operation_id'],'stop');obs=a.read(instance_id=i,state_ids=['tip.current_a'])['observations']['tip.current_a'];self.assertEqual(obs['quality'],'stale');r.step(10);later=a.read(instance_id=i,state_ids=['tip.current_a'])['observations']['tip.current_a'];self.assertEqual(obs,later)
 def test_two_instances_and_busy(self):
  r=prepared();other=prepared();before=copy.deepcopy(other.__dict__);call(r,'acquisition.start')
  with self.assertRaisesRegex(ActionRejected,'BUSY'):r.invoke('stage.move',x_m=0,y_m=0,z_m=.005)
  r.step(10);self.assertEqual(other.__dict__,before)
 def test_unsupported(self):
  r=prepared()
  for k in UNSUPPORTED:
   with self.assertRaisesRegex(ActionRejected,'UNSUPPORTED'):r.invoke(k)
if __name__=='__main__':unittest.main()
