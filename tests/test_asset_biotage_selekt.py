import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.biotage_selekt import *
def configured(plant=None,column=None,sample=None,method=None,rack=None,second=False):
 h=BiotageSelektRuntime();h.invoke('plant.configure',**dict(EXAMPLE,**(plant or {})));h.invoke('tray.set_extension',extension_m=.12);h.invoke('rack.configure',**dict(dict(positions=144,vessel_volume_m3=9e-6),**(rack or {})));h.invoke('tray.set_extension',extension_m=0.)
 for i in range(2 if second else 1):
  h.invoke('column.configure',**dict(COLUMN_EXAMPLE,**dict(column or {},channel=i)));h.invoke('sample.configure',**dict(SAMPLE_EXAMPLE,**dict(sample or {},channel=i)))
 for i in range(2 if second else 1):h.invoke('method.configure',**dict(METHOD_EXAMPLE,**dict(method or {},channel=i)))
 h.invoke('power.set',enabled=True);return h
def finish(h,dt=1.):
 h.invoke('acquisition.start')
 for _ in range(10000):
  h.step(dt)
  if h.phase!='running':return h
 raise AssertionError((h.phase,h.stage))
class TestSelekt(unittest.TestCase):
 def ledger(self,h):
  np.testing.assert_allclose(h.initialmol,h.colmol.sum(axis=(0,2))+h.detmol+h.fractionmol.sum(axis=0)+h.wastemol,rtol=0,atol=1e-15)
  np.testing.assert_allclose(h.initialfluid,h.reservoir+h.mixer+h.colfluid.sum(axis=(0,2))+h.detfluid+h.fractionfluid.sum(axis=0)+h.wastefluid,rtol=0,atol=1e-14)
  for a in [h.reservoir,h.mixer,h.colfluid,h.detfluid,h.colmol,h.detmol,h.fractionmol,h.wastemol]:self.assertGreaterEqual(float(a.min()),-1e-20)
 def test_01_real60_spectral_interpolation(self):
  h=BiotageSelektRuntime();self.assertEqual(len(h.templates),60)
  for sid,r in h.templates.items():
   x=r['values']['wavelength_m'];y=r['values']['molar_decadic_absorptivity_m2_mol'];j=len(x)//2;v=h.invoke('reference.lookup',substance_id=sid,wavelength_m=x[j]);self.assertEqual(v['epsilon_m2_mol'],y[j])
 def test_02_full_cycle(self):
  h=finish(configured());self.assertEqual(h.phase,'succeeded');self.assertEqual(h.pressure[0],0.);self.assertGreater(h.fractionmol.sum(),0.);self.assertGreater(h.wastemol.sum(),0.);self.assertAlmostEqual(h.gradient_s[0],240.,delta=1e-8);self.assertGreater(h.colmol.sum()+h.detmol.sum(),0.);self.ledger(h)
 def test_03_increments_rejected_atomic(self):
  h=configured();old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,flow_m3_s=1.5e-6/60))
  self.assertEqual(old,h.debug_snapshot())
  for cap,arguments in [('channel.select',{'channel':v}) for v in [True,.5,-1,2]]+[('rack.configure',dict(positions=v,vessel_volume_m3=9e-6)) for v in [True,1.5,0,145]]+[('column.configure',dict(COLUMN_EXAMPLE,solvent_pair=[0,1.5]))]:
   with self.assertRaises(ActionRejected):h.invoke(cap,**arguments)
   self.assertEqual(old,h.debug_snapshot())
 def test_04_only_targets_until_step(self):
  h=configured();self.assertEqual(h.flow,0.);self.assertEqual(h.pressure[0],0.);m=h.colmol.copy();h.invoke('acquisition.start');np.testing.assert_array_equal(m,h.colmol);self.assertEqual(h.flow,0.);h.step(.1);self.assertGreater(h.flow,0.)
 def test_05_read_pure_and_schema(self):
  h=configured();old=h.debug_snapshot();h.read();h.describe();h.invoke('chromatography.measure');h.invoke('product.read');self.assertEqual(old,h.debug_snapshot())
  for bad in [True,float('nan'),0,4000]:
   with self.assertRaises(ActionRejected):h.step(bad)
  self.assertEqual(old,h.debug_snapshot())
 def test_06_cancel_retains_pressure_and_mol(self):
  h=configured();h.invoke('acquisition.start');h.step(45);old=h.colmol.copy();p=h.pressure.copy();h.invoke('acquisition.stop');h.step(10);np.testing.assert_array_equal(old,h.colmol);np.testing.assert_array_equal(p,h.pressure);h=finish(h);self.assertEqual(h.phase,'succeeded');self.ledger(h)
 def test_07_poweroff_residual(self):
  h=configured();h.invoke('acquisition.start');h.step(40);old=h._products();p=h.pressure.copy();h.invoke('power.set',enabled=False);h.step(100);self.assertEqual(h.phase,'cancelled');self.assertEqual(old,h._products());np.testing.assert_array_equal(p,h.pressure);self.ledger(h)
 def test_08_two_channels_sequential_shared(self):
  h=configured(second=True);h.invoke('acquisition.start');h.step(30);old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('channel.select',channel=1)
  self.assertEqual(old,h.debug_snapshot());self.assertEqual(h.gradient_s[1],0.);self.assertEqual(h.colmol[1].sum(),3e-6);h.step(300);self.assertTrue(h.done[0]);h.invoke('channel.select',channel=1);h=finish(h);self.assertEqual(h.phase,'succeeded');self.assertTrue(all(h.done));self.ledger(h)
 def test_09_finite_solvent(self):
  h=finish(configured(plant={'solvent_volumes_m3':[3e-6,.002,.002,.002]}));self.assertEqual(h.fault,'SOLVENT_EMPTY');self.assertLess(h.gradient_s[0],10.);self.ledger(h)
 def test_10_finite_waste(self):
  h=finish(configured(plant={'waste_capacity_m3':2e-6},method={'threshold_absorbance':6.4}));self.assertEqual(h.fault,'WASTE_FULL');self.assertAlmostEqual(h.wastefluid.sum(),2e-6,delta=1e-15);self.ledger(h)
 def test_11_finite_144_rack_subset(self):
  h=finish(configured(rack={'positions':1},method={'collection_mode':'all','fraction_volume_m3':1e-6}));self.assertEqual(h.fault,'COLLECTION_FULL');self.assertAlmostEqual(h.fractionfluid[0].sum(),1e-6,delta=1e-15);self.ledger(h)
 def test_12_pressure_limit_partial(self):
  h=finish(configured(column={'pressure_limit_pa':5e5}));self.assertEqual(h.fault,'OVERPRESSURE');self.assertEqual(h.gradient_s[0],0.);self.assertEqual(h.colmol.sum(),3e-6);self.assertGreater(h.pressure[0],5e5);self.ledger(h)
 def test_13_tray_pressure_guards(self):
  h=configured();h.invoke('acquisition.start');h.step(1);h.invoke('acquisition.stop');old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('tray.set_extension',extension_m=.12)
  self.assertEqual(old,h.debug_snapshot());h.invoke('pressure.release');h.step(50);h.invoke('tray.set_extension',extension_m=.12);self.assertEqual(h.tray,.12);self.ledger(h)
 def test_14_reference_no_extrapolation(self):
  h=configured();old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,wavelengths_m=[200e-9,350e-9]))
  self.assertEqual(old,h.debug_snapshot())
 def test_15_identity_async_dedupe(self):
  h=configured();h.register_principal('eval','evaluator');a=h.session();e=h.session('eval');self.assertNotIn('sample.configure',a.describe(instance_id=h.instance_id)['capabilities']);self.assertFalse(any(k.startswith('truth.') for k in a.read(instance_id=h.instance_id)['observations']));r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(r['status'],'accepted');self.assertEqual(r,a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one'));self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='chromatography.measure',arguments={},request_id='one')['error']['code'],'REQUEST_CONFLICT')
  with self.assertRaises(ActionRejected):e.poll(r['operation_id'])
  h.step(300);self.assertEqual(a.poll(r['operation_id'])['status'],'succeeded');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['status'],'rejected')
 def test_16_native_cancel(self):
  h=configured();a=h.session();r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');h.step(30);v=h._products();self.assertEqual(a.cancel(r['operation_id'],'c')['status'],'cancelled');h.step(10);self.assertEqual(v,h._products())
 def test_17_instances(self):
  a=configured();b=configured();a.invoke('acquisition.start');a.step(20);self.assertEqual(b.time_s,0.);self.assertEqual(b.pressure[0],0.);self.assertFalse(np.array_equal(a.colmol,b.colmol))
 def test_18_no_method_rack_or_plant_start(self):
  h=BiotageSelektRuntime();old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
  self.assertEqual(old,h.debug_snapshot())
 def test_19_gradient_delay(self):
  h=configured(method={'gradient':[dict(time_s=0.,strong_fraction=1.),dict(time_s=10.,strong_fraction=1.)]});h.invoke('acquisition.start');self.assertEqual(h._values()['gradient.mixer_fraction'],0.);h.step(.1);self.assertGreater(h._values()['gradient.mixer_fraction'],0.);self.assertLess(h._values()['gradient.mixer_fraction'],1.);self.ledger(h)
 def test_20_sample_load_limit_atomic(self):
  h=BiotageSelektRuntime();h.invoke('plant.configure',**EXAMPLE);h.invoke('column.configure',**dict(COLUMN_EXAMPLE,max_sample_amount_mol=1e-6));old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('sample.configure',**SAMPLE_EXAMPLE)
  self.assertEqual(old,h.debug_snapshot())
 def test_21_xy_fraction_assignment(self):
  h=configured(method={'collection_mode':'all','fraction_volume_m3':1e-6});h.invoke('acquisition.start');h.step(30);self.assertGreater(h.tube,5);self.assertTrue(np.all(h.fractionfluid.sum(axis=1)<=1e-6+1e-15));self.assertGreater(h.wastefluid.sum(),0.);self.ledger(h)
 def test_22_step_subdivision(self):
  a=finish(configured(),1.);b=finish(configured(),.1);np.testing.assert_allclose(a.fractionmol,b.fractionmol,rtol=1e-7,atol=1e-13);np.testing.assert_allclose(a.colmol,b.colmol,rtol=1e-7,atol=1e-13);self.ledger(a);self.ledger(b)
if __name__=='__main__':unittest.main()
