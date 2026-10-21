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
class TestTerminal(unittest.TestCase):
 """Touch-screen software (terminal layer) through direct capability calls: run setup, equilibration, Sample Load, run control, monitoring,
 racks, flushes, administration, settings, maintenance (Biotage Selekt User Manual)."""
 def rig(self):
  h=BiotageSelektRuntime();h.invoke('plant.configure',**EXAMPLE);h.invoke('tray.set_extension',extension_m=.12);h.invoke('rack.configure',positions=144,vessel_volume_m3=9e-6);h.invoke('tray.set_extension',extension_m=0.)
  h.invoke('column.configure',**COLUMN_EXAMPLE);h.invoke('power.set',enabled=True)
  h.invoke('solvents.setup',inlet=0,solvent='Heptane');h.invoke('solvents.setup',inlet=1,solvent='Ethyl acetate')
  h.invoke('run.setup',channel=0,settings=dict(user='System Owner',column_type='Sfar Silica D 10g',solvent_a='Heptane',solvent_b='Ethyl acetate',uv1_nm=300.,uv2_nm=350.,equilibration=False))
  h.invoke('run.setup',channel=0,settings=dict(flow_ml_min=30));return h
 def begin(self,h):
  h.invoke('run.prepare',channel=0);h.invoke('sample.configure',**SAMPLE_EXAMPLE);h.invoke('run.begin')
 def done(self,h,limit=5000):
  for _ in range(limit):
   if h.phase!='running':return h
   h.step(1.)
  raise AssertionError(h.stage)
 def test_capabilities_exported(self):
  m=export_biotage_selekt_contract_metadata();self.assertEqual(set(m['capabilities']),set(ALL_CAPS))
  for k in TERMINAL_CAPS:self.assertIn('state_effects',m['capabilities'][k]);self.assertTrue(m['capabilities'][k]['errors'])
 def test_terminal_run_conserves_and_records(self):
  h=self.rig();self.assertEqual(h.read()['run.issues'],[]);self.begin(h);self.done(h)
  self.assertEqual(h.phase,'succeeded');r=h.read()['results.list'][0];self.assertEqual((r['status'],r['channel'],r['column_type']),('finished',1,'Sfar Silica D 10g'))
  self.assertEqual(h.methods[0]['flow_m3_s'],30e-6/60);self.assertTrue(r['tubes']);TestSelekt.ledger(self,h)
 def test_equilibration_then_sample_load(self):
  h=self.rig();h.invoke('run.setup',channel=0,settings=dict(equilibration=True));r0=h.reservoir.copy();h.invoke('run.prepare',channel=0)
  self.assertEqual(h.stage,'equilibration')
  for _ in range(600):
   h.step(1.)
   if h.term['pending']==0:break
  self.assertEqual((h.phase,h.stage,h.pressure[0]),('idle','sample_load',0.))
  used=(r0-h.reservoir)/ML;self.assertAlmostEqual(used.sum(),45.,places=6);self.assertAlmostEqual(used[1]/used.sum(),.05,places=9)   # 3 CV x 15 mL at 5 % B
 def test_pause_hold_end_abort_cleanup(self):
  h=self.rig();self.begin(h);h.step(20);h.invoke('run.pause');m=h._products();h.step(30);self.assertEqual(m,h._products())
  with self.assertRaises(ActionRejected):h.invoke('run.pause')
  h.invoke('run.resume');h.invoke('run.hold',on=True);t=float(h.gradient_s[0]);h.step(10);self.assertEqual(float(h.gradient_s[0]),t);h.invoke('run.hold',on=False)
  with self.assertRaises(ActionRejected):h.invoke('run.end',option='skip_equilibration')
  h.invoke('run.end',option='abort');self.assertEqual((h.phase,h.stage),('cancelled','aborted'));self.assertGreater(h.pressure[0],0.)
  h.invoke('purge.start',channel=0);self.done(h);self.assertEqual(h.pressure[0],0.)
 def test_flush_settings_and_pressure_cap(self):
  h=self.rig();h.invoke('owner.login',user=OWNER,password=OWNER_PASSWORD);h.invoke('settings.set',changes=dict(line_flush='To Waste',detector_flush='To Waste'))
  h.invoke('columns.save',entry=dict(name='Low P',cv_ml=15.,default_flow_ml_min=30.,max_pressure_bar=5.));h.invoke('run.setup',channel=0,settings=dict(column_type='Low P'))
  h.invoke('run.setup',channel=0,settings=dict(flow_ml_min=30));self.begin(h);h.step(20);self.assertAlmostEqual(h.pressure[0],5e5,delta=1.)   # flow reduced to hold 5 bar
  self.assertLess(h.flow,30e-6/60);st=set()
  for _ in range(4000):
   st.add(h.stage);h.step(1.)
   if h.phase!='running':break
  self.assertTrue({'line_flush','purging','detector_flush'}<=st);TestSelekt.ledger(self,h)
 def test_racks_full_pause_and_assign(self):
  h=self.rig();h.invoke('run.setup',channel=0,settings=dict(racks=['A'],collect_all=True,max_fraction_ml=.5));self.begin(h)
  for _ in range(3000):
   h.step(1.)
   if h.term['paused']:break
  self.assertEqual(h.term['pause_reason'],'Load or assign more racks');self.assertEqual(int((h.fractionfluid.sum(axis=1)>0).sum()),48)
  with self.assertRaises(ActionRejected):h.invoke('run.resume')
  h.invoke('run.setup',channel=0,settings=dict(racks=['A','B']));h.invoke('run.resume');h.step(5);self.assertGreaterEqual(h.tube,48)
 def test_monitoring_and_refusals(self):
  h=self.rig();h.invoke('owner.login',user=OWNER,password=OWNER_PASSWORD);h.invoke('settings.set',changes=dict(solvent_monitoring=True));h.invoke('owner.logout')
  h.invoke('solvents.setup',inlet=0,solvent='Heptane',capacity_ml=1000.,level_ml=100.);self.assertIn('Not enough solvent in S1',h.read()['run.issues'])
  with self.assertRaises(ActionRejected):h.invoke('run.prepare',channel=0)
  for cap,a,code in [('settings.set',dict(changes=dict(auto_extend=True)),'OWNER_REQUIRED'),('owner.login',dict(user=OWNER,password='x'),'WRONG_PASSWORD'),
                     ('flush.start',dict(path='bypass',column_type=None,volume_ml=10.,flow_ml_min=10,percents=[50.,0.,0.,0.]),'PERCENT_SUM'),('run.begin',{},'NOT_WAITING')]:
   old=h.debug_snapshot()
   with self.assertRaises(ActionRejected) as e:h.invoke(cap,**a)
   self.assertEqual(str(e.exception),code);self.assertEqual(old,h.debug_snapshot())
 def test_admin_backup_restore_and_uv_calibration(self):
  h=self.rig();h.invoke('owner.login',user=OWNER,password=OWNER_PASSWORD);h.invoke('users.save',entry=dict(name='Chemist 1'))
  with self.assertRaises(ActionRejected):h.invoke('columns.save',entry=dict(name='Sfar Silica D 10g',cv_ml=99.))
  f=h.invoke('database.backup')['file'];h.invoke('users.delete',name='Chemist 1');self.assertNotIn('Chemist 1',[u['name'] for u in h.read()['library.users']])
  h.invoke('database.restore',file=f);self.assertIn('Chemist 1',[u['name'] for u in h.read()['library.users']])
  h.invoke('sensor.configure_error',uv_offset_au=[.01,.02],uv_gain=.1);self.assertEqual([round(x,6) for x in h._uv_reading([0.,0.])],[.01,.02])
  h.invoke('uv.calibrate');np.testing.assert_allclose(h._uv_reading([.1,.2]),[.1,.2],atol=1e-12)
 def test_shutdown_and_restart_applies_date(self):
  h=self.rig();h.invoke('owner.login',user=OWNER,password=OWNER_PASSWORD);r=h.invoke('settings.set',changes=dict(date='2026-05-01'));self.assertTrue(r['restart_required'])
  h.invoke('system.shutdown');self.assertTrue(h.read()['system.shutdown'])
  with self.assertRaises(ActionRejected):h.invoke('run.setup',channel=0,settings=dict(comments='x'))
  h.invoke('power.set',enabled=False);h.invoke('power.set',enabled=True);self.assertTrue(h.read()['system.datetime'].startswith('2026-05-01'));self.assertFalse(h.read()['owner.unlocked'])
if __name__=='__main__':unittest.main()
