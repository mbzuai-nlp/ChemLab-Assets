import unittest,copy,math,importlib.util
from scripts.asset_runtime import markes_unityxr as r
spec=importlib.util.spec_from_file_location('asset_tool',r.ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);OBSERVED=set()
def call(h,k,a=None):
 v=h.invoke(k,**(a or {}));tool.validate_value(v,r.CAPS[k][1]);OBSERVED.add(k);return v
def setup(plant=None,method=None,sample=None):
 h=r.MarkesUnityxrRuntime()
 for k,a in [('plant.configure',dict(r.PLANT_EXAMPLE,**(plant or {}))),('method.configure',dict(r.METHOD_EXAMPLE,**(method or {}))),('lid.set',{'lift_m':.18}),('sample_lever.set',{'angle_rad':1.1}),('recollect_lever.set',{'angle_rad':1.1}),('sample.load',sample or r.SAMPLE_EXAMPLE),('recollection.install',{'installed':True}),('sample_lever.set',{'angle_rad':0.}),('recollect_lever.set',{'angle_rad':0.}),('lid.set',{'lift_m':0.}),('power.set',{'enabled':True})]:call(h,k,a)
 return h
def until(h,stage,max_s=600):
 for _ in range(max_s*2):
  if h.stage==stage:return
  if h.phase=='failed':raise AssertionError(h.failure)
  h.step(.5)
 raise AssertionError((stage,h.stage,h.T,h.phase))
def finish(h):
 if h.phase!='running':call(h,'acquisition.start')
 call(h,'gc.ready',{'ready':True})
 for _ in range(1200):
  h.step(.5)
  if h.phase!='running':break
 assert h.phase=='succeeded',(h.phase,h.stage,h.T,h.failure)
 return h
class Tests(unittest.TestCase):
 def ledger(self,h):
  v=call(h,'ledger.read');self.assertLess(abs(v['carrier_residual_mol']),2e-10);self.assertLess(abs(v['dry_residual_mol']),2e-10);self.assertLess(abs(v['energy_residual_j']),1e-6);self.assertLessEqual(v['peak_electrical_w'],650.)
  for x in v['species']:self.assertLess(abs(x['residual_kg']),max(1e-19,x['loaded_kg']*1e-11))
 def test_01_real_values_and_metadata(self):
  h=setup();m=r.export_markes_unityxr_contract_metadata();self.assertEqual(len(m['capabilities']),26);self.assertEqual(len(h.library['substances']),197);self.assertEqual(sum(x['kinetics_enabled'] for x in h.library['substances'].values()),178);v=call(h,'template.reference',{'substance_id':'tenax_toluene'});self.assertEqual(v['retention_volume_m3'],.076);self.assertEqual(v['evidence'],'derived');self.assertTrue(call(h,'template.reference',{'substance_id':'tenax_tetrachloromethane'})['flags'])
 def test_02_complete_conservation(self):
  h=finish(setup());self.assertEqual(h.triggers,1);self.assertEqual(sum(x['gc_trigger'] for x in h.trace),1);self.assertGreater(sum(h.output.values()),0.);self.assertGreater(sum(h.recollect.values()),0.);self.ledger(h);v=call(h,'products.collect');self.assertGreater(sum(x['mass_kg'] for x in v['species']),0);self.assertFalse(call(h,'products.collect')['species']);self.ledger(h)
 def test_03_gc_waits_without_loss(self):
  h=setup();call(h,'acquisition.start');until(h,'wait_gc');before=h.trap.copy();h.step(10);self.assertEqual(h.stage,'wait_gc');self.assertEqual(h.trap,before);self.assertEqual(h.triggers,0);finish(h);self.ledger(h)
 def test_04_common_capacity_and_recollection(self):
  h=finish(setup(plant={'trap_capacity_kg':1e-10,'recollect_capacity_kg':2e-10}));self.assertLessEqual(sum(h.trap.values()),1e-10+1e-20);self.assertLessEqual(sum(h.recollect.values()),2e-10+1e-20);self.assertGreater(sum(h.waste.values()),1e-8);self.ledger(h)
 def test_05_capture_not_species_order_dependent(self):
  a=setup(plant={'trap_capacity_kg':3e-9});b=copy.deepcopy(a);a._capture({'tenax_toluene':4e-9,'tenax_hexane':2e-9},'trap',3e-9);b._capture({'tenax_hexane':2e-9,'tenax_toluene':4e-9},'trap',3e-9);self.assertEqual(a.trap,b.trap);self.assertAlmostEqual(a.trap['tenax_toluene'],2e-9,places=20)
 def test_06_failed_leak_preserves_sample(self):
  h=setup(plant={'leak_rate_s_inv':.2});before=h.tube.copy();call(h,'acquisition.start');h.step(5);self.assertEqual(h.failure,'LEAK_TEST_FAILED');self.assertEqual(h.tube,before);self.assertFalse(h.output);self.assertEqual(h.T[0],293.15);self.ledger(h)
 def test_07_gas_depletion(self):
  h=setup(plant={'dry_supply_mol':.001});before=h.tube.copy();call(h,'acquisition.start');h.step(20.);self.assertEqual(h.failure,'DRY_GAS_DEPLETED');self.assertEqual(before,h.tube);self.ledger(h)
  h=setup(plant={'carrier_supply_mol':.001});call(h,'acquisition.start');h.step(100.);self.assertEqual(h.failure,'CARRIER_DEPLETED');self.ledger(h)
 def test_08_cancel_keeps_partial_effects(self):
  h=setup();call(h,'acquisition.start');until(h,'primary');h.step(55.);self.assertGreater(sum(h.recollect.values()),0);call(h,'acquisition.stop');before=[x.copy() for x in [h.tube,h.trap,h.recollect]];h.step(10.);self.assertEqual(before,[h.tube,h.trap,h.recollect]);self.assertEqual(h.phase,'cancelled');self.ledger(h)
 def test_09_poweroff_still_cools(self):
  h=setup();call(h,'acquisition.start');until(h,'primary');h.step(30);t=h.T[0];heat=h.heat;cool=h.cooling;call(h,'power.set',{'enabled':False});h.step(10);self.assertLess(h.T[0],t);self.assertEqual(heat,h.heat);self.assertEqual(cool,h.cooling);self.ledger(h)
 def test_10_invalid_atomic(self):
  h=setup();old=h.read('evaluator')
  for k,a in [('method.configure',dict(r.METHOD_EXAMPLE,tube_hold_s=7.)),('method.configure',dict(r.METHOD_EXAMPLE,tube_k=698.15)),('method.configure',dict(r.METHOD_EXAMPLE,outlet_split_m3_s=0.,column_flow_m3_s=2e-6/60)),('lid.set',{'lift_m':float('nan')}),('power.set',{'enabled':1})]:
   with self.assertRaises(r.ActionRejected):call(h,k,a)
   self.assertEqual(h.read('evaluator'),old)
 def test_11_partition(self):
  a=setup();call(a,'acquisition.start');b=copy.deepcopy(a);a.step(7.3)
  for _ in range(73):b.step(.1)
  self.assertEqual(a.read('evaluator'),b.read('evaluator'));self.assertEqual(a.T,b.T);self.assertEqual(a.tube,b.tube)
 def test_12_pure_queries_and_instances(self):
  a=setup();b=setup();call(a,'acquisition.start');a.step(40);old=a.read('evaluator');call(a,'process.measure');call(a,'ledger.read');a.describe();self.assertEqual(a.read('evaluator'),old);self.assertEqual(b.time_s,0.)
 def test_13_hot_and_access_interlocks(self):
  h=setup()
  with self.assertRaises(r.ActionRejected):call(h,'sample_lever.set',{'angle_rad':1.1})
  call(h,'acquisition.start');until(h,'primary');h.step(30)
  with self.assertRaises(r.ActionRejected):call(h,'lid.set',{'lift_m':.18})
  call(h,'acquisition.stop')
  with self.assertRaises(r.ActionRejected):call(h,'shield.set',{'lift_m':.12})
 def test_14_real_reference_transport(self):
  h=setup();n=h.tube['tenax_toluene'];q=100e-6/60;out=h._release(h.tube,293.15,.0002,q);self.assertAlmostEqual(out['tenax_toluene'],n*(-math.expm1(-q/.076*r.DT)),places=24)
 def test_15_cold_unload_recollection(self):
  h=finish(setup());call(h,'lid.set',{'lift_m':.18});call(h,'sample_lever.set',{'angle_rad':1.1});call(h,'recollect_lever.set',{'angle_rad':1.1});v=call(h,'recollection.collect');self.assertGreater(sum(x['mass_kg'] for x in v['species']),0);self.assertFalse(call(h,'recollection.collect')['species']);call(h,'sample.unload');call(h,'recollection.install',{'installed':False});self.ledger(h)
  h=setup();call(h,'power.set',{'enabled':False});call(h,'rear.set',{'slide_m':.15});call(h,'shield.set',{'lift_m':.12})
 def test_16_conditioning_routes(self):
  h=finish(setup(method={'mode':'tube_condition','recollect':False}));self.assertFalse(h.output);self.assertFalse(h.trap);self.assertEqual(h.triggers,0);self.assertGreater(sum(h.waste.values()),1e-8);self.ledger(h)
  h=setup();call(h,'acquisition.start');until(h,'primary');h.step(60);call(h,'acquisition.stop');self.assertGreater(sum(h.trap.values()),0);call(h,'method.configure',dict(r.METHOD_EXAMPLE,mode='trap_condition',recollect=False));finish(h);self.assertFalse(h.output);self.assertEqual(h.triggers,0);self.ledger(h)
 def test_17_ballistic_and_nitrogen(self):
  h=setup(plant={'carrier':'nitrogen'})
  with self.assertRaises(r.ActionRejected):call(h,'method.configure',dict(r.METHOD_EXAMPLE,trap_flow_m3_s=300e-6/60))
  a=setup();call(a,'acquisition.start');until(a,'wait_gc');b=copy.deepcopy(a);b.method['trap_heating']='ballistic';call(a,'gc.ready',{'ready':True});call(b,'gc.ready',{'ready':True});a.step(1.);b.step(1.);self.assertGreater(b.T[1],a.T[1]+40);self.assertLessEqual(b.T[1]-263.15,100.1);self.ledger(a);self.ledger(b)
 def test_18_permissions_dedup_unsupported(self):
  h=setup();h.register_principal('agent');h.register_principal('second');h.register_principal('eval','evaluator');s=h.session('agent');q=h.session('second');self.assertNotIn('sample.identity',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',s.describe(instance_id=h.instance_id)['capabilities']);args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');v=s.invoke(**args);self.assertEqual(v,s.invoke(**args));self.assertEqual(s.invoke(**dict(args,capability_id='acquisition.stop'))['error']['code'],'REQUEST_CONFLICT')
  with self.assertRaises(r.ActionRejected):q.poll(v['operation_id'])
  self.assertEqual(s.cancel(v['operation_id'],'cancel')['status'],'succeeded');self.assertEqual(s.poll(v['operation_id'])['status'],'cancelled');self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='forbidden')['error']['code'],'ACCESS_DENIED')
  for k in r.UNSUPPORTED:
   with self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED'):h.invoke(k)
  for dt in [True,-1,float('nan'),float('inf')]:
   with self.assertRaises(r.ActionRejected):h.step(dt)
if __name__=='__main__':unittest.main()
