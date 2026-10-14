import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.syrris_asiafllex import *
def configured(plant=None,membrane=None,method=None,sample=None):
 h=SyrrisAsiaFllexRuntime();h.invoke('plant.configure',**dict(EXAMPLE,**(plant or {})));h.invoke('tray.set_extension',extension_m=.07);h.invoke('separator.set_opening',opening_m=.04);h.invoke('membrane.configure',**dict(MEMBRANE_EXAMPLE,**(membrane or {})));h.invoke('separator.set_opening',opening_m=0.);h.invoke('tray.set_extension',extension_m=0.);h.invoke('method.configure',**dict(METHOD_EXAMPLE,**(method or {})));h.invoke('sample.configure',**dict(SAMPLE_EXAMPLE,**(sample or {})));h.invoke('power.set',enabled=True);return h
def until(h,stage,limit=4000):
 for _ in range(limit):
  if h.stage==stage or h.phase in ['failed','succeeded']:return h
  h.step(.25)
 raise AssertionError((h.stage,h.phase))
def finish(h,dt=.25):
 h.invoke('acquisition.start')
 for _ in range(20000):
  h.step(dt)
  if h.phase!='running':return h
 raise AssertionError((h.stage,h.phase))
class TestAsiaFllex(unittest.TestCase):
 def ledger(self,h):
  self.assertAlmostEqual(float(h.mass.sum()),h.initial_mass,delta=1e-15)
  for k in range(2):self.assertAlmostEqual(float(h.vol[:,k].sum()),float(h.initial_vol[k]),delta=1e-15)
  self.assertGreaterEqual(float(h.mass.min()),0.);self.assertGreaterEqual(float(h.vol.min()),0.)
 def test_01_reference60(self):
  h=SyrrisAsiaFllexRuntime();self.assertEqual(len(h.templates),60)
  for sid,t in h.templates.items():
   r=h.invoke('reference.lookup',substance_id=sid);self.assertEqual(r['kow'],10**t['values']['log10_kow']);self.assertIsNone(r['temperature_k']);self.assertIsNone(r['ph'])
 def test_02_full_extraction(self):
  h=finish(configured());self.assertEqual(h.phase,'succeeded');self.assertGreater(h.mass[4],0.);self.assertGreater(h.mass[5],0.);self.assertGreater(h.mass[2],0.);self.assertGreater(h.mass[3],0.);self.assertEqual(h.bpr,2e5);self.assertAlmostEqual(h.processed_s,120.);self.assertEqual(h.purity,[1.,1.]);self.ledger(h)
 def test_03_zero_transfer_analytic(self):
  h=finish(configured(plant={'mass_transfer_coeff_m_s':0.}));q=h.method['aqueous_flow_m3_s'];v=h.vol[1,0];c=h.feed_conc[0];expected=v*c*(1-math.exp(-q*120/v));self.assertAlmostEqual(h.mass[2],expected,delta=1e-16);self.assertEqual(h.mass[5],0.);self.ledger(h)
 def test_04_cancel_resume_residual(self):
  h=configured();h.invoke('acquisition.start');until(h,'extracting');h.step(10);mass=h.mass.copy();vol=h.vol.copy();h.invoke('acquisition.stop');h.step(5);np.testing.assert_array_equal(h.mass,mass);np.testing.assert_array_equal(h.vol,vol);self.assertEqual(h.bpr,2e5);h=finish(h);self.assertEqual(h.phase,'succeeded');self.ledger(h)
 def test_05_shutdown_traps_pressure(self):
  h=configured();h.invoke('acquisition.start');until(h,'extracting');h.step(10);p=h.bpr;mass=h.mass.copy();h.invoke('power.set',enabled=False);h.step(50);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.bpr,p);np.testing.assert_array_equal(mass,h.mass);self.ledger(h)
 def test_06_organic_breakthrough_partial(self):
  h=finish(configured(method={'cross_membrane_pa':1000.}));self.assertEqual(h.fault,'ORGANIC_BREAKTHROUGH');self.assertGreater(h.vol[2,1],0.);self.assertGreater(h.vol[3,1],0.);self.ledger(h)
 def test_07_membrane_wetting_partial(self):
  h=finish(configured(method={'cross_membrane_pa':4e4}));self.assertEqual(h.fault,'MEMBRANE_WETTING');self.assertTrue(h.wetted);self.assertGreater(h.vol[3,0],0.);self.ledger(h)
 def test_08_continue_degraded(self):
  h=finish(configured(method={'cross_membrane_pa':1000.,'stop_on_breakthrough':False}));self.assertEqual(h.phase,'succeeded');self.assertLess(h.purity[0],1.);self.assertGreater(h.vol[2,1],0.);self.ledger(h)
 def test_09_finite_feed(self):
  h=finish(configured(sample={'aqueous_feed_volume_m3':30e-9,'aqueous_solute_mass_kg':1e-8}));self.assertEqual(h.fault,'FEED_EMPTY');self.assertAlmostEqual(h.vol[0,0],0.,delta=1e-15);self.ledger(h)
 def test_10_collection_full(self):
  h=finish(configured(plant={'aqueous_collection_capacity_m3':30e-9}));self.assertEqual(h.fault,'COLLECTION_FULL');self.assertAlmostEqual(h.vol[2].sum(),30e-9,delta=1e-15);self.ledger(h)
 def test_11_service_guard_atomic(self):
  h=configured();old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('tray.set_extension',extension_m=.07)
  self.assertEqual(h.debug_snapshot(),old)
  h2=SyrrisAsiaFllexRuntime();h2.invoke('tray.set_extension',extension_m=.07);h2.invoke('separator.set_opening',opening_m=.04);old=h2.debug_snapshot()
  with self.assertRaises(ActionRejected):h2.invoke('tray.set_extension',extension_m=0.)
  self.assertEqual(h2.debug_snapshot(),old)
 def test_12_method_gas_flow_atomic(self):
  h=SyrrisAsiaFllexRuntime();h.invoke('plant.configure',**EXAMPLE);old=h.debug_snapshot()
  for patch in [{'bpr_gauge_pa':7e5},{'organic_flow_m3_s':500e-9/60}]:
   with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,**patch))
   self.assertEqual(h.debug_snapshot(),old)
 def test_13_pure_read_clock_schema(self):
  h=configured();old=h.debug_snapshot();h.read();h.describe();h.invoke('separation.measure');h.invoke('product.read');self.assertEqual(old,h.debug_snapshot())
  for dt in [True,-1,float('nan'),4000]:
   with self.assertRaises(ActionRejected):h.step(dt)
  with self.assertRaises(ActionRejected):h.invoke('panel.select',mode='invalid')
  self.assertEqual(old,h.debug_snapshot())
 def test_14_protocol_identity_async(self):
  h=configured();h.register_principal('e','evaluator');a=h.session();e=h.session('e');self.assertNotIn('truth.initial_solute_kg',a.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.configure',a.describe(instance_id=h.instance_id)['capabilities']);r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(r['status'],'accepted');self.assertEqual(r,a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one'));self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='separation.measure',arguments={},request_id='one')['error']['code'],'REQUEST_CONFLICT')
  with self.assertRaises(ActionRejected):e.poll(r['operation_id'])
  h.step(200);self.assertEqual(a.poll(r['operation_id'])['status'],'succeeded');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['status'],'rejected');self.ledger(h)
 def test_15_instances_timeout(self):
  a=finish(configured(method={'duration_s':10.,'timeout_s':10.}));b=configured();self.assertEqual(a.fault,'TIMEOUT');self.assertEqual(b.phase,'idle');self.assertEqual(b.processed_s,0.);self.ledger(a)
 def test_16_release_retains_holdup(self):
  h=finish(configured());mass=h.mass.copy();vol=h.vol.copy();h.invoke('pressure.release');h.step(100);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.bpr,0.);np.testing.assert_array_equal(h.mass,mass);np.testing.assert_array_equal(h.vol,vol);self.ledger(h)
 def test_17_single_panel_modes(self):
  h=finish(configured());self.assertEqual(h.read()['panel.value'],100.);h.invoke('panel.select',mode='bpr');self.assertEqual(h.read()['panel.value'],2.);self.assertEqual(h.read()['pressure.organic_gauge_pa'],190000.)
 def test_18_targets_not_instant_pressure(self):
  h=configured();self.assertEqual(h.bpr,0.);self.assertEqual(h.cmp,0.);self.assertEqual(h.processed_s,0.);self.assertEqual(h.mass[2],0.)
 def test_19_step_convergence(self):
  a=finish(configured(),.25);b=finish(configured(),.1);np.testing.assert_allclose(a.mass,b.mass,rtol=1e-9,atol=1e-16);np.testing.assert_allclose(a.vol,b.vol,rtol=1e-9,atol=1e-16);self.ledger(a);self.ledger(b)
 def test_20_membrane_and_phase_preconditions(self):
  h=SyrrisAsiaFllexRuntime();old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('plant.configure',**dict(EXAMPLE,pre_equilibrated_immiscible_phases=False))
  self.assertEqual(old,h.debug_snapshot());h.invoke('plant.configure',**EXAMPLE);old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('membrane.configure',**MEMBRANE_EXAMPLE)
  self.assertEqual(old,h.debug_snapshot())
 def test_21_native_cancellation_owner(self):
  h=configured();a=h.session();r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');h.step(50);mass=h.mass.copy();a.cancel(r['operation_id'],'cancel');self.assertEqual(a.poll(r['operation_id'])['status'],'cancelled');h.step(5);np.testing.assert_array_equal(mass,h.mass);self.ledger(h)
if __name__=='__main__':unittest.main()
