import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.velp_udk159 import *
def prepared(plant=None,method=None,sample=None,stocks=None,close=True):
 r=VelpUdk159Runtime();p=copy.deepcopy(PLANT_EXAMPLE);p.update(plant or {});m=copy.deepcopy(METHOD_EXAMPLE);m.update(method or {});s=example_sample();s.update(sample or {});st=copy.deepcopy(STOCK_EXAMPLE);st.update(stocks or {})
 for cap,args in [('plant.configure',p),('method.configure',m),('sample.configure',s),('stocks.configure',st),('coolant.set',COOLANT_EXAMPLE),('power.set',dict(enabled=True)),('tube.set_clamped',dict(clamped=True))]:r.invoke(cap,**args)
 r.step(1.)
 if close:r.invoke('door.set_open',open=False);r.step(1.)
 return r
def completed(**kw):
 r=prepared(**kw);r.invoke('acquisition.start');r.step(400.);return r
class RuntimeTests(unittest.TestCase):
 def assert_balance(self,r):
  p=r.invoke('product.read');self.assertAlmostEqual(p['initial_N_kg'],sum(p[k] for k in ['sample_N_kg','excluded_N_kg','receiver_N_kg','escaped_N_kg','waste_N_kg','collected_N_kg']),14);self.assertAlmostEqual(p['volume_initial_m3'],p['volume_remaining_m3']+p['collected_volume_m3'],12);self.assertAlmostEqual(p['absorbed_J'],p['storage_J']+p['ambient_loss_J']+p['steam_latent_J'],5)
 def test_01_success(self):
  r=completed();self.assertEqual(r.phase,'succeeded');self.assertGreater(r.result,4.9e-5);self.assertLess(r.result,r.initial_N);self.assert_balance(r)
 def test_02_steam_bounds(self):
  r=prepared()
  for v in [.099,1.001]:
   a=copy.deepcopy(METHOD_EXAMPLE);a['steam_fraction']=v
   with self.assertRaises(ActionRejected):r.invoke('method.configure',**a)
 def test_03_heat_not_teleport(self):
  r=prepared();t=r.temperature;r.invoke('acquisition.start');self.assertEqual(r.temperature,t);r.step(8);self.assertGreater(r.temperature,t);self.assertLess(r.temperature,373.15)
 def test_04_door_not_teleport(self):
  r=prepared(close=False);q=r.door_angle;r.invoke('door.set_open',open=False);self.assertEqual(q,r.door_angle);r.step(.1);self.assertLess(r.door_angle,q)
 def test_05_open_start_atomic(self):
  r=prepared(close=False);before=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
  self.assertEqual(before,r.debug_snapshot())
 def test_06_unclamped_start(self):
  r=prepared(close=False);r.invoke('tube.set_clamped',clamped=False);r.step(1);r.invoke('door.set_open',open=False);r.step(1)
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_07_hotdoor_refused(self):
  r=prepared();r.invoke('acquisition.start')
  with self.assertRaises(ActionRejected):r.invoke('door.set_open',open=True)
 def test_08_cancel_partial(self):
  r=prepared();r.invoke('acquisition.start');r.step(80);n=r.receiver_N;r.invoke('acquisition.stop');r.step(70);self.assertEqual(r.receiver_N,n);self.assertGreater(n,0);self.assertIsNone(r.result);self.assert_balance(r)
 def test_09_no_restart_reuse(self):
  r=completed()
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_10_coolant_failure(self):
  r=prepared();r.invoke('acquisition.start');r.step(90);r.invoke('coolant.set',temperature_K=288.15,flow_m3_s=0);self.assertEqual(r.phase,'failed');self.assertIsNone(r.result);self.assert_balance(r)
 def test_11_too_warm_coolant(self):
  r=prepared();r.invoke('coolant.set',temperature_K=309.15,flow_m3_s=1e-3/60)
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_12_stock_capacity_atomic(self):
  r=prepared(stocks=dict(base_m3=1e-6));b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
  self.assertEqual(b,r.debug_snapshot())
 def test_13_insufficient_base(self):
  r=prepared(method=dict(base_concentration_mol_m3=10.))
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_14_boric_capacity(self):
  r=prepared(method=dict(boric_capacity_mol_m3=1.))
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_15_sample_volume_capacity(self):
  r=prepared(method=dict(water_volume_m3=150e-6,base_volume_m3=150e-6))
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
 def test_16_nominal_N_range(self):
  for mass in [1e-9,.1]:
   with self.assertRaises(ActionRejected):prepared(sample=dict(sample_mass_kg=mass))
 def test_17_TN_needs_optin(self):
  sid=next(iter(VelpUdk159Runtime().templates))
  with self.assertRaises(ActionRejected):prepared(sample=dict(substance_id=sid))
 def test_18_TN_excluded_pool(self):
  r=VelpUdk159Runtime();sid=next(iter(r.templates));v=r.templates[sid]['value'];r=completed(sample=dict(substance_id=sid,sample_mass_kg=5e-5/v,kjeldahl_available_fraction=.7,upstream_digestion_recovery=.9,accept_TN_to_TKN_assumption=True));self.assertGreater(r.excluded_N,0);self.assert_balance(r)
 def test_19_no_predigestion(self):
  with self.assertRaises(ActionRejected):prepared(sample=dict(predigestion_confirmed=False))
 def test_20_blank(self):
  r=completed(sample=dict(sample_mass_kg=0.));self.assertEqual(r.phase,'succeeded');self.assertAlmostEqual(r.result,0,14);self.assertFalse(r.invoke('analysis.measure')['nominal_range']);self.assert_balance(r)
 def test_21_normality(self):
  r=completed();self.assertAlmostEqual(r.result,(r.titrant_volume-.1e-6)*200*.0140067,15);self.assertAlmostEqual(r.result,r.receiver_N,12)
 def test_22_collect(self):
  r=completed();v=r.receiver_volume;r.invoke('product.collect',fraction=.5,recipient_id='test');self.assertAlmostEqual(r.collected_volume,v/2,14);self.assertAlmostEqual(r.color(),0.,8);self.assert_balance(r)
 def test_23_drain(self):
  r=completed();r.invoke('residue.drain');self.assertEqual(r.sample_volume+r.receiver_volume,0);self.assertGreater(r.waste_N,0);self.assert_balance(r)
 def test_24_power_interrupt(self):
  r=prepared();r.invoke('acquisition.start');r.step(90);n=r.receiver_N;r.invoke('power.interrupt');r.step(90);self.assertEqual(r.phase,'cancelled');self.assertEqual(n,r.receiver_N);self.assert_balance(r)
 def test_25_steam_depletion(self):
  r=completed(stocks=dict(steam_water_m3=1e-6));self.assertEqual(r.phase,'failed');self.assertIn('steam_water',r.fault);self.assert_balance(r)
 def test_26_endpoint_timeout(self):
  r=completed(plant=dict(titrant_rate_m3_s=1e-9));self.assertEqual(r.phase,'failed');self.assertIn('endpoint',r.fault);self.assert_balance(r)
 def test_27_zero_transfer(self):
  r=completed(plant=dict(transfer_rate_s_inverse=0.));self.assertAlmostEqual(r.receiver_N,0);self.assertAlmostEqual(r.sample_N,r.initial_N);self.assert_balance(r)
 def test_28_all_states_validate(self):
  r=completed()
  for k,v in r.read('evaluator').items():validate(SLOTS[k][0],v)
 def test_29_curve(self):
  r=completed();p=r.invoke('curve.read');self.assertIn('synthetic',p['evidence']);self.assertGreater(len(p['points']),230);self.assertTrue(all(a['time_s']<b['time_s'] and a['titrant_volume_m3']<=b['titrant_volume_m3'] for a,b in zip(p['points'],p['points'][1:])))
 def test_30_invalid_dt_atomic(self):
  r=prepared();b=r.debug_snapshot()
  for dt in [True,-1,float('nan'),float('inf'),3601]:
   with self.assertRaises(ActionRejected):r.step(dt)
  self.assertEqual(b,r.debug_snapshot())
 def test_31_instance_isolation(self):
  a=prepared();b=prepared();before=b.debug_snapshot();a.invoke('acquisition.start');a.step(90);self.assertEqual(before,b.debug_snapshot())
 def test_32_unknown_reference_atomic(self):
  r=prepared();b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('reference.lookup',substance_id='missing')
  self.assertEqual(b,r.debug_snapshot())
 def test_33_fault_acknowledge(self):
  r=prepared();r.invoke('acquisition.start');r.invoke('coolant.set',temperature_K=288.15,flow_m3_s=0);r.step(60);r.invoke('coolant.set',**COOLANT_EXAMPLE);r.invoke('fault.acknowledge');self.assertEqual(r.phase,'cancelled')
 def test_34_pure_queries(self):
  r=completed();b=r.debug_snapshot()
  for cap in ['analysis.measure','curve.read','product.read']:r.invoke(cap)
  r.session().describe(instance_id=r.instance_id);r.session().read(instance_id=r.instance_id);self.assertEqual(b,r.debug_snapshot())
if __name__=='__main__':unittest.main()
