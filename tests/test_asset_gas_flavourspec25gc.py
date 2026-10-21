from pathlib import Path
import sys,unittest,copy,json,importlib.util,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.gas_flavourspec25gc import *
from asset_runtime.gas_flavourspec25gc import _ramp_volume
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
def run_to_end(r,limit=400):
 for _ in range(limit):
  if r.phase!='running':break
  r.step(1.)
class Firmware(unittest.TestCase):
 """Touch-screen firmware 4.80 (manual section 6): Defaults, programs, recording, files, modes, settings, snapshot, clock, error information."""
 def test_defaults_follow_the_method_and_drive_the_heaters(self):
  r=prepared();d=r.read()['device.defaults'];self.assertEqual((d['t1_c'],d['t2_c'],d['t3_c'],d['epc_ims_ml_min'],d['epc_gc_ml_min']),(45.,60.,85.,150.,2.))
  call(r,'defaults.configure',t1_c=50.,epc_ims_ml_min=100.,t4_c=None,averaging=0,trigger_us=200,polarity='negative');self.assertAlmostEqual(r.method['ims_k'],323.15);self.assertAlmostEqual(r.method['drift_flow_m3_s'],100*ML)
  r.step(30.);p=r.read()['device.parameters'];self.assertAlmostEqual(p['t1_c'],50.,delta=.01);self.assertAlmostEqual(p['t4_c'],25.,delta=.01);self.assertAlmostEqual(p['epc_ims_ml_min'],100.)
  self.assertEqual((p['polarity'],r._ion_factor()),('negative',0.));call(r,'defaults.configure',polarity='positive');self.assertEqual(r._ion_factor(),2.)
  for a in [dict(t3_c=250.),dict(averaging=100),dict(epc_gc_ml_min=.5)]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(ActionRejected):r.invoke('defaults.configure',**a)
   self.assertEqual(r.__dict__,before)
 def test_program_run_with_flow_ramp_and_file(self):
  r=prepared();call(r,'program.manage',op='create',name='QC');call(r,'program.action',op='add',time_s=60.,e_gc=10.);call(r,'program.action',op='set_end',end_s=120.)
  prof=r._profile(r._selected());self.assertEqual(prof['gc'],[(0.,2.),(60.,10.)]);self.assertAlmostEqual(_ramp_volume(prof['gc'],0,120),(2+10)/2*60+10*60)
  v=call(r,'program.start');self.assertEqual(v['phase'],'running');run_to_end(r);self.assertEqual(r.phase,'succeeded')
  f=r.read()['files.stored'];self.assertEqual(len(f),1);self.assertEqual((f[0]['duration_s'],f[0]['program'],f[0]['averaging']),(120.,'QC',6));self.assertEqual(f[0]['size_b'],int(120*RAW_HZ/6*SPECTRUM_B))
  self.assertLess(abs(r._ledger()['total_residual_mol']),1e-12);self.assertIsNone(r.fw['run'])
  call(r,'program.manage',op='copy');call(r,'program.manage',op='rename',name='QC2');call(r,'program.manage',op='move_up');call(r,'program.manage',op='set_averages',averages=99)
  self.assertEqual([p['name'] for p in r.read()['programs.list']['programs']],['QC2','QC']);call(r,'program.action',op='copy',line=0);call(r,'program.action',op='edit',line=1,time_s=10.,rec='stop')
  self.assertEqual(r._rec_intervals(r._selected()),[(0.,10.)]);call(r,'program.action',op='delete',line=1);call(r,'program.manage',op='select',index=1);call(r,'program.manage',op='delete')
  self.assertEqual(len(r.fw['programs']),1)
  for k,a in [('program.action',dict(op='set_end',end_s=30.)),('program.manage',dict(op='rename',name='bad/name')),('program.action',dict(op='add',time_s=200.))]:
   with self.assertRaises(ActionRejected):r.invoke(k,**a)
 def test_program_start_refusals(self):
  r=prepared()
  for setup,code in [(lambda:None,'NO_PROGRAM'),(lambda:(call(r,'program.manage',op='create'),call(r,'program.action',op='delete',line=0)),'INVALID_PROGRAM'),
                     (lambda:(call(r,'program.action',op='add',time_s=0.,rec='rec'),call(r,'recording.set',on=True)),'RECORDING_ACTIVE'),
                     (lambda:(call(r,'recording.set',on=False),call(r,'gas.set',enabled=False)),'DRIFT_FLOW_LOW'),(lambda:(call(r,'gas.set',enabled=True),call(r,'mode.set',mode='trigger')),'MODE_ACTIVE')]:
   setup()
   with self.assertRaisesRegex(ActionRejected,code):r.invoke('program.start')
 def test_recording_files_and_delete(self):
  r=prepared();call(r,'recording.set',on=True);r.step(60.);v=call(r,'recording.set',on=False);f=r.read()['files.stored'][0];self.assertEqual((f['name'],f['duration_s'],f['kind']),(v['file'],60.,'recording'))
  self.assertTrue(f['name'].startswith('20260101_00002'));self.assertLess(r.read()['recording.state']['free_min'],STORAGE_B/(RAW_HZ/6*SPECTRUM_B)/60)
  call(r,'files.delete',names=[f['name']]);self.assertEqual(r.read()['files.stored'],[])
  for a,code in [(dict(all=True),'NO_MEASUREMENTS')]:
   with self.assertRaisesRegex(ActionRejected,code):r.invoke('files.delete',**a)
 def test_modes_cleaning_standby_remote_trigger(self):
  r=prepared();call(r,'mode.set',mode='cleaning',hours=1);self.assertEqual((r._target_k('ims'),r._target_k('injector'),r._ion_factor()),(393.15,473.15,0.))
  call(r,'mode.set',mode='cleaning',hv_on=True);self.assertEqual(r._ion_factor(),1.)
  with self.assertRaisesRegex(ActionRejected,'BUSY'):r.invoke('acquisition.start')
  r.step(3600.);self.assertEqual(r.read()['device.mode']['mode'],'normal');call(r,'mode.set',mode='standby',hours=None);self.assertEqual(r.read()['device.parameters']['epc_ims_ml_min'],10.)
  call(r,'mode.set',mode='remote');[self.assertAlmostEqual(a,b,delta=1e-15) for a,b in zip(r._flows(),(r.method['drift_flow_m3_s'],2*ML))]
  with self.assertRaisesRegex(ActionRejected,'BUSY'):r.invoke('acquisition.start')
  call(r,'mode.set',mode='normal');r.step(600.);call(r,'program.manage',op='create',name='T');call(r,'program.action',op='set_end',end_s=60.);call(r,'mode.set',mode='trigger')
  self.assertEqual(call(r,'sample_names.generate')['count'],999);call(r,'acquisition.start');run_to_end(r);f=r.read()['files.stored'][-1]
  self.assertEqual((f['program'],f['sample_name'],f['kind'],f['duration_s']),('T','mea_1','trigger',60.));self.assertEqual(r.read()['sample_names.state']['current'],'mea_2')
 def test_settings_snapshot_and_errors(self):
  r=prepared();r.step(1.);self.assertIn('snapshot_missing',[e['key'] for e in r.fw['errors'] if e['resolved'] is None])
  call(r,'snapshot.take');r.step(1.);self.assertFalse([e for e in r.fw['errors'] if e['resolved'] is None])
  call(r,'settings.configure',settings={'column.id_mm':.32,'epc.gc_gas':'helium'});r.step(1.);self.assertIn('epc_gc_pressure',r.read()['snapshot.state']['out_of_range'])
  self.assertEqual([e['key'] for e in r.fw['errors'] if e['resolved'] is None],['snapshot_range'])
  for s,code in [({'nope':1},'UNKNOWN_SETTING'),({'network.ip':'300.1.1.1'},'INVALID_SETTING'),({'fan.start_c':60.,'fan.max_c':50.},'INVALID_SETTING'),({'display.brightness':9},'INVALID_ARGUMENT')]:
   with self.assertRaisesRegex(ActionRejected,code):r.invoke('settings.configure',settings=s)
  call(r,'program.manage',op='create');call(r,'settings.reset_default');self.assertEqual((r.fw['programs'],r.fw['settings']['column.id_mm']),([],.53))
  call(r,'clock.set',date='2027-05-06',time='07:08:09',timezone='Asia/Tokyo');self.assertEqual(r.read()['device.clock'],'2027-05-06T07:08:09')
  with self.assertRaisesRegex(ActionRejected,'INVALID_DATE'):r.invoke('clock.set',date='2027-02-30')
  call(r,'power.set',enabled=False);call(r,'power.set',enabled=True);self.assertEqual(r.fw['errors'],[])   # a restart deletes the error events
 def test_epc_gas_type_must_match_the_connected_gas(self):
  """1.2.0 (sync audit R3): EPC gas type acts on the real flows (manual p.75, p.120); defaults nitrogen / nitrogen keep the old numbers."""
  a,b=prepared(),prepared();self.assertEqual(a._epc_factors(),(1.,1.))
  with self.assertRaisesRegex(ActionRejected,'INVALID_ARGUMENT'):b.invoke('gas.configure_supply')
  call(b,'gas.configure_supply',ims_gas='helium',gc_gas='helium');self.assertEqual(b.read('evaluator')['gas.supplied'],dict(ims='helium',gc='helium'))
  f=GAS_VISCOSITY['nitrogen']/GAS_VISCOSITY['helium'];self.assertEqual(b._epc_factors(),(f,f))
  a0,b0=a.consumed,b.consumed;a.step(60.);b.step(60.)
  self.assertAlmostEqual((b.consumed-b0)/(a.consumed-a0),f,places=9)                       # EPCs set to N2, He connected: real flows x eta(N2)/eta(He)
  self.assertEqual(a.read()['device.parameters']['epc_gc_ml_min'],b.read()['device.parameters']['epc_gc_ml_min'])   # the screen shows the set flows
  for r in (a,b):call(r,'acquisition.start')
  with self.assertRaisesRegex(ActionRejected,'BUSY'):b.invoke('gas.configure_supply',gc_gas='air')
  for r in (a,b):run_to_end(r)
  self.assertAlmostEqual(b.gc_volume/a.gc_volume,f,places=6)
  peak=lambda r:max(r.points,key=lambda p:sum(p['signal']))['time_s'];self.assertGreater(peak(b),peak(a))   # less carrier: the analytes elute later
  call(b,'settings.configure',settings={'epc.ims_gas':'helium','epc.gc_gas':'helium'});self.assertEqual(b._epc_factors(),(1.,1.))   # set right: flows as set
 def test_low_pressure_switches_the_heaters_off(self):
  r=prepared(plant=dict(PLANT_EXAMPLE,gas_supply_mol=2e-4));r.step(400.);self.assertTrue(r._gas_empty())
  r.step(20.);self.assertIn('low_pressure',[e['key'] for e in r.fw['errors'] if e['resolved'] is None]);r.step(300.);self.assertTrue(r.fw['heaters_off'])
  r.step(60.);self.assertAlmostEqual(r.temperature['ims'],PLANT_EXAMPLE['ambient_k'],delta=1e-6)
 def test_device_errors_high_pressure_and_drift_supply(self):
  """1.3.0: manual p.39-40 overpressure (temperature and flow controllers off, reset by the confirmation once the cause is gone) and the drift
  supply errors of p.244-247 (start refused)."""
  r=prepared();call(r,'fault.inject',fault='high_pressure',active=True);r.step(5.);self.assertTrue(r.read('evaluator')['safety.high_pressure'])
  self.assertEqual(r._flows(),(0.,0.));self.assertEqual(r._target_k('ims'),r.plant['ambient_k'])
  with self.assertRaises(ActionRejected) as e:r.invoke('acquisition.start')
  self.assertEqual(str(e.exception),'HIGH_PRESSURE');self.assertEqual(call(r,'pressure_alarm.confirm'),{'alarm':True})
  call(r,'fault.inject',fault='high_pressure',active=False);self.assertEqual(call(r,'pressure_alarm.confirm'),{'alarm':False});self.assertGreater(r._flows()[0],0.)
  call(r,'fault.inject',fault='drift_supply_init',active=True)
  with self.assertRaises(ActionRejected) as e:r.invoke('acquisition.start')
  self.assertEqual(str(e.exception),'DRIFT_SUPPLY_INIT')

if __name__=='__main__':unittest.main()
