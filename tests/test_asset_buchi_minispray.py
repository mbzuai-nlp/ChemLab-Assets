"""Real reference identities, finite physical bounds, conservative drying and sessions."""
import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.buchi_minispray import *
def ready(volume=2e-6,plant=None,method=None,sample=None):
 h=BuchiMinisprayRuntime();p=copy.deepcopy(EXAMPLE);p.update(plant or {});m=copy.deepcopy(METHOD_EXAMPLE);m.update(method or {});s=copy.deepcopy(SAMPLE_EXAMPLE);s['volume_m3']=volume;s.update(sample or {})
 h.invoke('plant.configure',**p);h.invoke('sample.configure',**s);h.invoke('method.configure',**m);h.invoke('power.set',enabled=True);h.invoke('drying.prepare');h.step(20);return h
class SprayDryingTests(unittest.TestCase):
 def balance(self,h):
  i=h._inventory();self.assertAlmostEqual(i['input'],i['retained']+i['vapor']+i['exhaust']+i['removed'],places=12)
  pools=[h.stock,*h.bins,*h.pools.values()];self.assertAlmostEqual(h.initial_solid,sum(x['s'] for x in pools),places=12)
  for x in pools:self.assertGreaterEqual(x['w'],-1e-15);self.assertGreaterEqual(x['s'],-1e-15);self.assertGreaterEqual(x['a'],-1e-15);self.assertLessEqual(x['a'],x['s']+1e-15)
 def test_01_fifty_actual_original_condition_references(self):
  h=BuchiMinisprayRuntime();self.assertEqual(len(h.templates),50);self.assertEqual(sum(t['values']['glass_former'] for t in h.templates.values()),24)
  self.assertEqual(sum(t['values']['stable_on_storage'] is True for t in h.templates.values()),15)
  for sid,t in h.templates.items():
   r=h.invoke('reference.lookup',substance_id=sid);validate(REFERENCE,r);self.assertEqual(r['glass_former'],t['values']['glass_former']);self.assertIsNone(r['s300_yield_fraction']);self.assertIn('B-290',r['conditions']);self.assertTrue(r['source'].startswith('https://doi.org/'))
  self.assertEqual(h.templates['salicylic_acid']['original']['tg_c'],-38);self.assertIn('derived',h.templates['bezafibrate']['quantity_evidence']['glass_transition_k']);self.assertIn('uncertain',h.templates['albendazole']['quantity_evidence']['glass_transition_k'])
 def test_02_rejects_invalid_and_atomic_configuration(self):
  h=BuchiMinisprayRuntime();s=h.debug_snapshot()
  for a in [dict(EXAMPLE,heater_power_w=2200.),dict(EXAMPLE,gas_connected=False),dict(EXAMPLE,gas_density_kg_m3=math.nan)]:
   with self.assertRaises(ActionRejected):h.invoke('plant.configure',**a)
   self.assertEqual(s,h.debug_snapshot())
  for dt in [0,-1,True,math.nan,math.inf,3601]:
   with self.assertRaises(ValueError):h.step(dt)
 def test_03_basic_no_organic_or_ultrasonic(self):
  h=ready();before=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,inlet_target_k=523.15))
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,nozzle='ultrasonic'))
  with self.assertRaises(ActionRejected):h.invoke('sample.configure',**dict(SAMPLE_EXAMPLE,solvent='ethanol'))
  self.assertEqual(before,h.debug_snapshot())
 def test_04_warmup_and_read_are_not_time(self):
  h=BuchiMinisprayRuntime();h.invoke('plant.configure',**EXAMPLE);h.invoke('sample.configure',**SAMPLE_EXAMPLE);h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('drying.prepare')
  with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
  t=h.time_s
  for _ in range(10):h.read();h.invoke('drying.measure')
  self.assertEqual(t,h.time_s);h.step(20);self.assertLess(abs(h.inlet-423.15),3)
 def test_05_complete_mass_water_solid_and_loss_paths(self):
  h=ready();h.invoke('acquisition.start');h.step(30);self.assertEqual(h.phase,'succeeded');self.balance(h);r=h.invoke('product.read');validate(PRODUCT,r)
  for k in ['product','wall','filter','exhaust']:self.assertGreater(h.mass(h.pools[k]),0)
  self.assertGreater(h.vapor,0);self.assertLess(r['yield_dry_solid_fraction'],1);self.assertGreater(r['mass_weighted_particle_diameter_m'],0);self.assertLess(r['water_mass_fraction'],.15)
 def test_06_wet_reject_not_dry_yield(self):
  h=ready(plant={'d2_rate_m2_s':1e-13});h.invoke('acquisition.start');h.step(30);self.balance(h);self.assertGreater(h.mass(h.pools['wet_reject']),0);self.assertEqual(h.pools['product']['s'],0);self.assertIsNone(h._product()['mass_weighted_particle_diameter_m'])
 def test_07_cancel_drains_but_never_refeeds(self):
  h=ready();h.invoke('acquisition.start');h.step(.3);h.invoke('acquisition.stop');stock=copy.deepcopy(h.stock);v=h.vapor;self.assertTrue(h.bins);h.step(5);self.assertEqual(h.stock,stock);self.assertGreater(h.vapor,v);self.assertFalse(h.bins);self.assertEqual(h.phase,'cancelled');self.balance(h)
  h.invoke('acquisition.start');h.step(30);self.assertEqual(h.phase,'succeeded');self.balance(h)
 def test_08_poweroff_cools_preserves_inventory(self):
  h=ready();h.invoke('acquisition.start');h.step(.3);h.invoke('power.set',enabled=False);s=h.stock.copy();w=sum(b['w'] for b in h.bins);t=h.inlet;h.step(5);self.assertEqual(s,h.stock);self.assertAlmostEqual(w,sum(b['w'] for b in h.bins));self.assertLess(h.inlet,t);self.assertEqual(h.phase,'cancelled');self.balance(h)
 def test_09_active_label_damage_conserves_solids(self):
  h=ready(sample={'damage_rate_ref_s_inv':1.,'damage_activation_j_mol':0.});h.invoke('acquisition.start');h.step(30);self.balance(h);self.assertLess(h._product()['active_fraction'],.9)
 def test_10_parameters_change_collection_and_filter(self):
  a=ready(plant={'cyclone_d50_m':1e-6});b=ready(plant={'cyclone_d50_m':40e-6})
  for h in (a,b):h.invoke('acquisition.start');h.step(30);self.balance(h)
  self.assertGreater(a._product()['yield_dry_solid_fraction'],b._product()['yield_dry_solid_fraction']);self.assertGreater(b.mass(b.pools['filter']),a.mass(a.pools['filter']))
 def test_11_cooldown_open_unload_is_once(self):
  h=ready();h.invoke('acquisition.start');h.step(30)
  with self.assertRaises(ActionRejected):h.invoke('vessel.set_lower',lower_m=.05)
  h.invoke('drying.shutdown');h.step(20);h.invoke('airflow.stop');h.invoke('vessel.set_lower',lower_m=.05);r=h.invoke('product.unload',sample_id='feed_1');self.assertTrue(r['unloaded']);self.balance(h);self.assertGreater(h.mass(h.pools['removed']),0)
  with self.assertRaises(ActionRejected):h.invoke('product.unload',sample_id='feed_1')
 def test_12_session_privacy_async_ownership_and_retry(self):
  h=ready();h.register_principal('a');h.register_principal('b');h.register_principal('judge','evaluator');a=h.session('a');b=h.session('b');v=a.read(instance_id=h.instance_id);self.assertFalse(any(k.startswith('truth.') for k in v['observations']))
  denied=a.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='p');self.assertEqual(denied['status'],'rejected')
  args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');r=a.invoke(**args);self.assertEqual(r['status'],'accepted');self.assertEqual(r,a.invoke(**args))
  with self.assertRaises(ActionRejected):b.poll(r['operation_id'])
  h.step(.3);a.cancel(r['operation_id'],'cancel');self.assertEqual(a.poll(r['operation_id'])['status'],'cancelled');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='drying.shutdown',arguments={},request_id='s')['status'],'rejected')
 def test_13_metadata_schemas_and_instance_isolation(self):
  a=ready();b=ready();a.invoke('acquisition.start');a.step(2);self.assertEqual(b.phase,'idle');self.assertEqual(b.vapor,0);meta=export_buchi_minispray_contract_metadata();self.assertEqual(set(meta['state_slots']),set(a.read('evaluator')))
  for key,v in a.read('evaluator').items():validate(SLOTS[key][0],v)
  for cap in ['drying.measure','product.read','history.read']:validate(CAPS[cap][1],a.invoke(cap))
 def test_14_time_partition_consistency(self):
  a=ready();b=ready()
  for h in (a,b):h.invoke('acquisition.start')
  a.step(5)
  for _ in range(50):b.step(.1)
  self.balance(a);self.balance(b);self.assertAlmostEqual(a.vapor,b.vapor,places=10);self.assertAlmostEqual(a.pools['product']['s'],b.pools['product']['s'],places=10)
if __name__=='__main__':unittest.main()
