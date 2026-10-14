import unittest,copy,json,math,importlib.util
from pathlib import Path
from scripts.asset_runtime import frontier_egapy as r
spec=importlib.util.spec_from_file_location('asset_tool',r.ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
OBSERVED=set()
def call(h,k,a=None):
 v=h.invoke(k,**(a or {}));tool.validate_value(v,r.CAPS[k][1]);OBSERVED.add(k);return v
def setup(mode='single',**method):
 h=r.FrontierEgapyRuntime();call(h,'plant.configure',r.PLANT_EXAMPLE);call(h,'method.configure',dict(r.METHOD_EXAMPLE,mode=mode,**method));call(h,'sample.load',r.SAMPLE_EXAMPLE);call(h,'power.set',{'enabled':True});call(h,'gas.set',{'enabled':True});call(h,'gc.ready',{'ready':True});return h
def until(h,stage,max_s=1200):
 for _ in range(max_s):
  if h.stage==stage:return
  h.step(1.)
 raise AssertionError((stage,h.stage,h.T,h.phase,h.failure))
def dropped(h):
 call(h,'acquisition.start');until(h,'wait_drop');call(h,'cup.drop');call(h,'acquisition.confirm');return h
class Tests(unittest.TestCase):
 def assertledger(self,h):
  l=call(h,'ledger.read');self.assertLess(abs(l['mass_residual_kg']),1e-18);self.assertLess(abs(l['energy_residual_j']),max(1e-7,l['heater_energy_j']*1e-10));self.assertLess(abs(l['carrier_residual_mol']),1e-11);self.assertLess(abs(l['cool_residual_mol']),1e-11)
  for x in l['species']:self.assertLess(abs(x['residual_kg']),1e-18)
 def test_01_schema_and_real_library(self):
  m=r.export_frontier_egapy_contract_metadata();self.assertEqual(len(m['capabilities']),22);h=setup();v=call(h,'template.reference',{'substance_id':'nist_pps_73'});self.assertEqual(v['name'],'Styrene');self.assertEqual(v['ri'],892.7);self.assertIn([104,999.],v['peaks']);self.assertEqual(len(h.library['substances']),164);self.assertEqual(len(h.library['polymers']),11)
 def test_02_single_and_mass_energy(self):
  h=dropped(setup());h.step(6.);self.assertEqual(h.stage,'cool');self.assertGreater(sum(h.generated.values()),1e-8);until(h,'cool');h.step(300.);self.assertEqual(h.phase,'succeeded');self.assertLessEqual(h.T[0],323.15);self.assertledger(h);v=call(h,'products.collect');self.assertGreater(sum(x['mass_kg'] for x in v['species']),0);self.assertFalse(call(h,'products.collect')['species']);self.assertledger(h)
 def test_03_double_manual_order(self):
  h=setup('double',td_end_k=373.15);call(h,'acquisition.start');until(h,'wait_insert');self.assertEqual(h.sampler,0.)
  with self.assertRaises(r.ActionRejected):call(h,'acquisition.confirm')
  call(h,'sampler.set',{'down_m':.08});call(h,'acquisition.confirm');until(h,'wait_retract');self.assertEqual(h.sampler,.08)
  with self.assertRaises(r.ActionRejected):call(h,'acquisition.confirm')
  call(h,'sampler.set',{'down_m':0.});call(h,'acquisition.confirm');until(h,'wait_drop')
  with self.assertRaises(r.ActionRejected):call(h,'cup.drop')
  call(h,'gc.ready',{'ready':True});call(h,'cup.drop');call(h,'acquisition.confirm');self.assertEqual(h.triggers,2);h.step(10);self.assertEqual(sum(x['gc_trigger'] for x in h.trace),2);self.assertledger(h)
 def test_04_ega_requires_retract(self):
  h=setup('ega',td_end_k=373.15);call(h,'acquisition.start');until(h,'wait_insert');call(h,'sampler.set',{'down_m':.08});call(h,'acquisition.confirm');until(h,'wait_retract');h.step(1);self.assertEqual(h.stage,'wait_retract');call(h,'sampler.set',{'down_m':0.});call(h,'acquisition.confirm');h.step(.1);self.assertEqual(h.stage,'cool');self.assertEqual(h.triggers,1);self.assertledger(h)
 def test_05_cancel_hot_continues_chemistry(self):
  h=dropped(setup(pyrolysis_s=60.));h.step(.2);call(h,'acquisition.stop');before=sum(h.generated.values());h.step(2.);self.assertEqual(h.phase,'cancelled');self.assertGreater(sum(h.generated.values()),before);self.assertledger(h)
 def test_06_power_off_energy(self):
  h=dropped(setup(pyrolysis_s=60.));before=h.heat;call(h,'power.set',{'enabled':False});h.step(5);self.assertEqual(h.heat,before);self.assertEqual(h.phase,'cancelled');self.assertledger(h)
 def test_07_atomic_invalid(self):
  h=setup();old=copy.deepcopy(h.read('evaluator'))
  for k,a in [('method.configure',dict(r.METHOD_EXAMPLE,hold_s=7.)),('method.configure',dict(r.METHOD_EXAMPLE,ramp_k_s=10.1)),('guard.set',{'lift_m':float('nan')}),('power.set',{'enabled':1})]:
   with self.assertRaises(r.ActionRejected):call(h,k,a)
   self.assertEqual(h.read('evaluator'),old)
 def test_08_fixed_step_partition(self):
  a=dropped(setup());b=copy.deepcopy(a);a.step(1.7)
  for _ in range(17):b.step(.1)
  self.assertEqual(a.T,b.T);self.assertEqual(a.generated,b.generated);self.assertEqual(a.read('evaluator'),b.read('evaluator'))
 def test_09_isolation_pure_queries(self):
  a=setup();b=setup();a.T[0]=350.;before=a.read('evaluator');call(a,'process.measure');call(a,'ledger.read');self.assertEqual(before,a.read('evaluator'));self.assertEqual(b.T[0],293.15)
 def test_10_capacity_and_mass_fraction(self):
  h=setup();h.sample=None
  for s in [dict(r.SAMPLE_EXAMPLE,channels=[dict(r.SAMPLE_EXAMPLE['channels'][0],char_fraction=.2)]),dict(r.SAMPLE_EXAMPLE,channels=[dict(r.SAMPLE_EXAMPLE['channels'][0],mass_kg=1e-4)],density_kg_m3=10.)]:
   with self.assertRaises(r.ActionRejected):call(h,'sample.load',s)
 def test_11_guard_and_unload(self):
  h=setup();call(h,'guard.set',{'lift_m':.18})
  with self.assertRaises(r.ActionRejected):call(h,'acquisition.start')
  call(h,'guard.set',{'lift_m':0.});dropped(h)
  with self.assertRaises(r.ActionRejected):call(h,'guard.set',{'lift_m':.18})
  h.step(500.);self.assertLess(h.T[2],313.15);call(h,'sample.unload');self.assertFalse(h.sample);self.assertledger(h)
 def test_12_species_exact_flow(self):
  h=setup();h.channels=[];h.loaded=1e-8;h.generated={'nist_pps_73':1e-8};h.chamber={'nist_pps_73':1e-8};h.step(1.);self.assertAlmostEqual(h.chamber['nist_pps_73'],1e-8*math.exp(-.02),places=20);self.assertledger(h)
 def test_13_depletion(self):
  h=setup();h.carrier=1e-12;call(h,'acquisition.start');h.step(1);self.assertEqual(h.failure,'CARRIER_DEPLETED');self.assertGreaterEqual(h.carrier,0.)
 def test_14_interface_auto(self):
  h=setup(interface_auto=True);call(h,'acquisition.start');h.step(10.);self.assertLessEqual(h.T[1],h.T[0]+100.1);self.assertledger(h)
 def test_15_permissions_idempotency(self):
  h=setup();h.register_principal('a');h.register_principal('b');h.register_principal('eval','evaluator');s=h.session('a');b=h.session('b');v=s.describe(instance_id=h.instance_id);self.assertNotIn('sample.identity',v['state_slots']);self.assertNotIn('sample.load',v['capabilities']);self.assertNotIn('cup.temperature_k',s.read(instance_id=h.instance_id)['observations']);args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='req');x=s.invoke(**args);self.assertEqual(x,s.invoke(**args));self.assertEqual(s.invoke(**dict(args,capability_id='acquisition.stop'))['error']['code'],'REQUEST_CONFLICT')
  with self.assertRaises(r.ActionRejected):b.poll(x['operation_id'])
  self.assertEqual(s.cancel(x['operation_id'],'cancel')['status'],'succeeded');self.assertEqual(s.poll(x['operation_id'])['status'],'cancelled');self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='private')['error']['code'],'ACCESS_DENIED')
 def test_16_invalid_time_and_unsupported(self):
  h=setup()
  for dt in [-1.,True,float('inf'),float('nan')]:
   with self.assertRaises(r.ActionRejected):h.step(dt)
  for cap in r.UNSUPPORTED:
   with self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED'):h.invoke(cap)
if __name__=='__main__':unittest.main()
