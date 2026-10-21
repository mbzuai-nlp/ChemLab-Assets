import copy,hashlib,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.buchi_speedextractore import *
def setup(n=6,method=None,plant=None,sample=None):
 h=BuchiSpeedextractoreRuntime();h.invoke('plant.configure',**dict(EXAMPLE,**(plant or {})));h.invoke('method.configure',**dict(METHOD_EXAMPLE,**(method or {})));h.invoke('power.set',enabled=True);h.invoke('thermal.preheat');h.step(100.);h.invoke('shield.set_lift',lift_m=.18);h.invoke('drawer.set_extension',extension_m=.16)
 for i in range(n):h.invoke('sample.configure',**dict(SAMPLE_EXAMPLE,**dict(position=i,sample_id='soil_'+str(i),**(sample or {}))))
 h.invoke('drawer.set_extension',extension_m=0.);h.invoke('shield.set_lift',lift_m=0.);return h
def finish(h,dt=.25):
 if h.phase=='idle':h.invoke('acquisition.start')
 for _ in range(math.ceil(1200/dt)):
  h.step(dt)
  if h.phase!='running':break
 return h
def advance(h,stage):
 if h.phase=='idle':h.invoke('acquisition.start')
 for _ in range(4000):
  h.step(.25)
  if h.stage==stage:return
 raise AssertionError((stage,h.phase,h.stage,h.fault))
class TestE916(unittest.TestCase):
 def ledger(self,h):
  q=h._inventory()
  for initial,terms in [('initial_target_kg',['sorbed_target_kg','dissolved_target_kg','extract_target_kg','degraded_target_kg','removed_target_kg']),('initial_solvent_m3',['reservoir_solvent_m3','cell_solvent_m3','extract_solvent_m3','waste_solvent_m3','removed_solvent_m3']),('initial_nitrogen_mol',['remaining_nitrogen_mol','exhaust_nitrogen_mol'])]:self.assertAlmostEqual(q[initial],sum(q[k] for k in terms),places=12)
  for c in h.cells.values():self.assertAlmostEqual(c['spec']['target_mass_kg'],c['sorbed']+c['dissolved']+c['extract']+c['degraded'],places=15)
 def test_01_contract_and_no_clock_queries(self):
  h=setup();m=export_buchi_speedextractore_contract_metadata();self.assertEqual(set(m['capabilities']),set(CAPS));t=h.time_s
  for c in ['extraction.measure','product.read']:validate(CAPS[c][1],h.invoke(c))
  for k,v in h.read('evaluator').items():validate(SLOTS[k][0],v)
  self.assertEqual(t,h.time_s);self.assertNotIn('truth.initial_target_kg',h.read())
 def test_02_ranges_and_atomic_rejections(self):
  h=BuchiSpeedextractoreRuntime()
  for updates in [dict(cycles=True),dict(cycles=1.5),dict(cycles=11),dict(pressure_gauge_pa=16e6),dict(temperature_k=474.),dict(pump_total_m3_s=51e-6/60),dict(solvent_fractions=[.5,.4,0,0]),dict(nitrogen_flush_s=59.)]:
   old=h.read('evaluator');self.assertRaises(ActionRejected,h.invoke,'method.configure',**dict(METHOD_EXAMPLE,**updates));self.assertEqual(old,h.read('evaluator'))
  self.assertRaises(ActionRejected,h.invoke,'method.configure',**dict(METHOD_EXAMPLE,temperature_k=473.15,pressure_gauge_pa=5e6,solvent_fractions=[1,0,0,0]))
 def test_03_preheat_same_cells_and_clearance(self):
  h=BuchiSpeedextractoreRuntime();h.invoke('plant.configure',**EXAMPLE);h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('shield.set_lift',lift_m=.18);h.invoke('drawer.set_extension',extension_m=.16)
  self.assertRaises(ActionRejected,h.invoke,'sample.configure',**SAMPLE_EXAMPLE);h.invoke('thermal.preheat');h.step(100);h.invoke('sample.configure',**SAMPLE_EXAMPLE)
  for extra in [dict(position=1,cell_volume_m3=40e-6),dict(position=True),dict(position=1,substance_id='unknown'),dict(position=1,gripper_confirmed=False)]:self.assertRaises(ActionRejected,h.invoke,'sample.configure',**dict(SAMPLE_EXAMPLE,**extra))
  self.assertRaises(ActionRejected,h.invoke,'shield.set_lift',lift_m=0.);self.assertRaises(ActionRejected,h.invoke,'method.configure',**METHOD_EXAMPLE)
 def test_04_complete_six_cell_conservation(self):
  h=finish(setup());self.assertEqual(h.phase,'succeeded');self.ledger(h);self.assertEqual(len(h._product()['items']),6);self.assertEqual(h.heater,0.)
  self.assertTrue({'sealing','tightness','fill','heatup_1','heatup_2','heatup_3','hold','discharge','nitrogen_flush','release','complete'}<={x['stage'] for x in h.history})
  for q in h._product()['items']:self.assertGreater(q['modeled_absolute_recovery_fraction'],.1);self.assertLessEqual(q['modeled_absolute_recovery_fraction'],1);self.assertIsNone(q['measured_e916_recovery_fraction'])
 def test_05_single_total_pump_and_inactive_flow(self):
  for n in (1,6):
   h=setup(n);advance(h,'fill');before=sum(h.reservoir);h.step(1.);self.assertAlmostEqual(before-sum(h.reservoir),METHOD_EXAMPLE['pump_total_m3_s']*n/6,places=13);self.ledger(h)
 def test_06_independent_positions(self):
  h=setup(2);h.cells[1]['spec']['kd_m3_kg_by_solvent']=[100.]*4;finish(h);q=h._product()['items'];self.assertGreater(q[0]['extract_target_kg'],q[1]['extract_target_kg']*100);self.ledger(h)
 def test_07_real54references_and_140vapor_points(self):
  h=BuchiSpeedextractoreRuntime();self.assertEqual(len(h.templates),54);self.assertTrue(any(q['values']['ase_to_soxtec_recovery_ratio']>1 for q in h.templates.values()))
  for sid,q in h.templates.items():
   r=h.invoke('reference.lookup',substance_id=sid);validate(REFERENCE,r);self.assertIsNone(q['cas']);self.assertIn('ASE',r['conditions']);self.assertIsNone(r['measured_e916_recovery_fraction'])
  for i,sid in enumerate(SOLVENTS):
   s=h.solvents[sid];self.assertEqual(len(s['vapor_pressure_reference_points']),35)
   for point in s['vapor_pressure_reference_points']:
    f=[0.]*4;f[i]=1.;self.assertLess(abs(h.saturation_pressure(point['temperature_k'],f)/point['pressure_pa']-1),1e-12)
  self.assertAlmostEqual(h.invoke('reference.lookup',substance_id='phenol')['ase_soxtec_ratio'],1.02)
 def test_08_cycles_increase_recovery(self):
  a=finish(setup(1,method={'cycles':1}));b=finish(setup(1,method={'cycles':3}));self.assertGreater(b.cells[0]['extract'],a.cells[0]['extract']);self.ledger(b)
 def test_09_transfer_and_hold_sensitivity(self):
  a=finish(setup(1,sample={'mass_transfer_s_inv':1e-5}));b=finish(setup(1,sample={'mass_transfer_s_inv':.1}));self.assertGreater(b.cells[0]['extract'],a.cells[0]['extract']*10)
  c=finish(setup(1,method={'hold_s':100},sample={'mass_transfer_s_inv':1e-5}));self.assertGreater(c.cells[0]['extract'],a.cells[0]['extract'])
 def test_10_solubility_degradation_bookkeeping(self):
  h=finish(setup(1,sample={'solubility_kg_m3':.00001,'degradation_s_inv':.001}));self.assertGreater(h.cells[0]['degraded'],0);self.assertLess(h.cells[0]['extract'],1e-8);self.ledger(h)
 def test_11_pause_residual_and_ordered_abort(self):
  h=setup(1);advance(h,'hold');p=h._maxp();m=h.cells[0]['dissolved'];stock=sum(h.reservoir);h.invoke('acquisition.stop');h.step(2.);self.assertEqual(h._maxp(),p);self.assertEqual(sum(h.reservoir),stock);self.assertNotEqual(h.cells[0]['dissolved'],m);self.assertRaises(ActionRejected,h.invoke,'shield.set_lift',lift_m=.18);h.invoke('acquisition.abort');finish(h);self.assertEqual((h.phase,h.stage),('cancelled','complete'));self.assertLessEqual(h._maxp(),1e5);self.ledger(h)
 def test_12_finite_supply_failure_retains_material(self):
  h=finish(setup(6,plant={'reservoir_m3':[1e-6]*4}));self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'solvent_supply_empty');self.ledger(h);self.assertGreater(sum(sum(c['liquid']) for c in h.cells.values()),0)
  h=finish(setup(1,plant={'nitrogen_mol':.001}));self.assertEqual(h.fault,'nitrogen_supply_empty');self.ledger(h)
 def test_13_vial_limit_no_disappearing_extract(self):
  h=finish(setup(1,method={'cycles':10},plant={'vial_capacity_m3':60e-6}));self.assertEqual(h.fault,'collection_vial_full');self.assertLessEqual(sum(h.cells[0]['vial']),60e-6+1e-14);self.ledger(h)
 def test_14_identity_idempotency_async(self):
  h=setup(1);h.register_principal('eval','evaluator');h.register_principal('other');s=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**kw);self.assertEqual(r['status'],'accepted');self.assertEqual(r,s.invoke(**kw));self.assertEqual(h._operation_count,1);self.assertRaises(ActionRejected,h.session('other').poll,r['operation_id']);self.assertEqual(s.poll(r['operation_id'])['status'],'running');s.cancel(r['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled');r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertRaises(ActionRejected,s.read,instance_id=h.instance_id,state_ids=['truth.initial_target_kg']);self.assertEqual(s.invoke(**dict(kw,capability_id='power.set',arguments={'enabled':False}))['error']['code'],'REQUEST_CONFLICT')
 def test_15_convergence_power_unload_and_no_n2_discharge(self):
  a=finish(setup(1),.25);b=finish(setup(1),.125);self.assertLess(abs(a.cells[0]['extract']/b.cells[0]['extract']-1),.01)
  h=setup(1);advance(h,'discharge');n=h.nitrogen;h.step(1.);self.assertEqual(n,h.nitrogen);h.invoke('power.set',enabled=False);p=h._maxp();h.step(1.);self.assertEqual(h._maxp(),p);self.assertIsNone(h.read()['thermal.indicated_k']);self.ledger(h)
  a.invoke('shield.set_lift',lift_m=.18);a.invoke('drawer.set_extension',extension_m=.16);a.invoke('sample.unload',position=0,sample_id='soil_0',gripper_confirmed=True);self.ledger(a);self.assertEqual(a._inventory()['sorbed_target_kg'],0)
  for dt in (0,-1,True,float('nan')):self.assertRaises(ActionRejected,a.step,dt)
 def test_16_cell_capacity_variants_and_solvent_flush(self):
  for volume in (10e-6,40e-6):
   h=finish(setup(1,method={'cycles':1,'solvent_flush_s':5.},sample={'cell_volume_m3':volume}));self.assertEqual(h.phase,'succeeded');self.assertIn('solvent_flush',[x['stage'] for x in h.history]);self.ledger(h)
def device(blanks=(),powered=True):
 h=BuchiSpeedextractoreRuntime();h.invoke('plant.configure',**EXAMPLE)
 if powered:h.invoke('power.set',enabled=True)
 if blanks:
  h.invoke('shield.set_lift',lift_m=.18);h.invoke('drawer.set_extension',extension_m=.16)
  for p in blanks:h.invoke('cell.insert_empty',position=p,cell_volume_m3=20e-6,gripper_confirmed=True)
  h.invoke('drawer.set_extension',extension_m=0.);h.invoke('shield.set_lift',lift_m=0.)
 return h
def run_until(h,cond,limit=5000.):
 t=0.
 while not cond():
  assert t<limit,'not reached';h.step(1.);t+=1.
def solvent_ledger(h):
 q=h._inventory();return q['initial_solvent_m3']-sum(q[k] for k in ['reservoir_solvent_m3','cell_solvent_m3','extract_solvent_m3','waste_solvent_m3','removed_solvent_m3'])
class DeviceFunctions(unittest.TestCase):
 """1.1 firmware functions behind the front-panel menus, called directly (om = operation manual 093218)."""
 def test_17_positions_method_store_and_extras(self):
  h=device();self.assertIsNone(h.read()['positions.active'])
  self.assertEqual(h.invoke('positions.set',active=[True]*6)['active'],[True]*6);self.assertEqual(h.invoke('positions.set',position=6,enabled=False)['active'],[True]*5+[False])
  for bad in [dict(),dict(position=7,enabled=True),dict(active=[True]*5),dict(position=1)]:self.assertRaises(ActionRejected,h.invoke,'positions.set',**bad)
  steps=[dict(hold_s=60.,discharge_s=30.,vial_change=True,port=0),dict(hold_s=30.,discharge_s=30.,vial_change=False,port=2)]
  h.invoke('method.configure',**dict(METHOD_EXAMPLE,vial='60',cell_volume_m3=20e-6,solvent_names=['Acetone','Ethanol','',''],cycle_steps=steps))
  self.assertTrue(h.read()['method.modified']);self.assertEqual(h.read()['method.current']['cycle_steps'],steps)
  self.assertRaises(ActionRejected,h.invoke,'method.configure',**dict(METHOD_EXAMPLE,cycle_steps=steps[:1]))
  self.assertEqual(h.invoke('method.save',slot=7,name='PAH-01'),dict(slot=7,name='PAH-01'));self.assertEqual((h.method_name,h.method_modified),('PAH-01',False))
  for bad in ['','X'*17,'bad#name']:self.assertRaises(ActionRejected,h.invoke,'method.save',slot=1,name=bad)
  h.invoke('method.configure',**METHOD_EXAMPLE);self.assertTrue(h.method_modified);h.invoke('method.open',slot=7);self.assertEqual(h.method['vial'],'60');self.assertFalse(h.method_modified)
  self.assertRaises(ActionRejected,h.invoke,'method.open',slot=8);h.invoke('method.delete',slot=7);self.assertEqual(h.read()['method.library'],[]);self.assertRaises(ActionRejected,h.invoke,'method.delete',slot=7)
 def test_18_preheat_setpoint_eco_and_settings(self):
  h=device();h.invoke('thermal.preheat',temperature_k=353.15,enabled=False);self.assertEqual((h.preheat,h.preheat_k),(False,353.15))
  h.invoke('thermal.preheat',temperature_k=353.15);run_until(h,lambda:abs(h.temperature-353.15)<.5);self.assertIsNone(h.read()['thermal.target_k'])
  h.invoke('thermal.set_eco_mode',enabled=True);self.assertTrue(h.read()['thermal.eco_mode']);h.invoke('power.set',enabled=False);self.assertFalse(h.eco)
  h.invoke('power.set',enabled=True);h.invoke('thermal.preheat',enabled=False);self.assertFalse(h.preheat)
  h.invoke('settings.configure',key='language',value='Deutsch');h.invoke('settings.configure',key='contrast',value=55)
  for k,v in [('contrast',101),('contrast',True),('language','Klingon'),('beep_key',1)]:self.assertRaises(ActionRejected,h.invoke,'settings.configure',key=k,value=v)
  self.assertEqual(h.read()['device.settings']['contrast'],55);h.invoke('solvents.set_name',index=4,name='DCM');h.invoke('solvents.set_name',index=11,name='Chlorobenzene')
  self.assertRaises(ActionRejected,h.invoke,'solvents.set_name',index=1,name='x'*21);h.invoke('solvents.load_default');self.assertEqual(h.solvent_list[3],'Dichloromethane');self.assertEqual(h.solvent_list[10],'Chlorobenzene')
  h.invoke('fan.set',fan='extraction',percent=60);self.assertEqual(h.read()['service.fans'],dict(extraction=60,electronic=30))
 def test_19_leak_test_with_empty_cells_and_seal_leak(self):
  h=device(blanks=range(1,7));h.invoke('positions.set',active=[True]*6);h.invoke('leak_test.configure',solvent_fractions=[0.,1.,0.,0.],hold_s=60.)
  self.assertRaises(ActionRejected,h.invoke,'leak_test.start');h.invoke('thermal.preheat',temperature_k=373.15);run_until(h,lambda:abs(h.temperature-373.15)<1)
  h.invoke('seal.configure_leak',position=2,leak_pa_s=5e4);h.invoke('leak_test.start')
  self.assertRaises(ActionRejected,h.invoke,'acquisition.start');self.assertRaises(ActionRejected,h.invoke,'valve.set',valve='solvent_1',open=True)
  run_until(h,lambda:h.aux is None);r=h.read()['leak_test.status']['result']
  self.assertTrue(r['completed']);self.assertFalse(r['passed']);self.assertLess(r['pressures_pa'][1],r['pressures_pa'][0]-15e5);self.assertAlmostEqual(solvent_ledger(h),0.,places=12)
  self.assertEqual((h.heater,h.rack,h.counters['leak_tests']),(0.,0.,1));h.invoke('seal.configure_leak',position=2,leak_pa_s=0.)
  h.invoke('leak_test.configure',temperature_k=473.15);self.assertRaises(ActionRejected,h.invoke,'leak_test.start')           # 100 K away: Error 2
  h.invoke('leak_test.configure',temperature_k=363.15);self.assertRaises(ActionRejected,h.invoke,'leak_test.start')           # 10 K too hot: Error 3
  h.invoke('leak_test.start',start_anyway=True);run_until(h,lambda:h.aux['stage']=='hold');h.invoke('leak_test.stop');self.assertEqual(h.aux['stage'],'discharge')
  self.assertFalse(h.aux['result']['completed']);self.assertRaises(ActionRejected,h.invoke,'leak_test.stop');run_until(h,lambda:h.aux is None)
  h2=device(blanks=[1,2]);h2.invoke('positions.set',active=[True,True,True,False,False,False]);h2.invoke('thermal.preheat',temperature_k=373.15);run_until(h2,lambda:abs(h2.temperature-373.15)<1)
  h2.invoke('leak_test.start');run_until(h2,lambda:h2.aux is None);self.assertEqual(h2.notice['code'],'position_pressure_too_low:3');self.assertEqual(h2.aux_last['leak_test']['fault'],'position_pressure_too_low:3')
 def test_20_flush_into_waste_and_stop(self):
  h=device(blanks=[1]);h.invoke('positions.set',active=[True]+[False]*5);h.invoke('flush.configure',solvent_s=60.,gas_s=60.,flush_into='waste')
  stock=sum(h.reservoir);h.invoke('flush.start');run_until(h,lambda:h.aux is None)
  self.assertAlmostEqual(stock-sum(h.reservoir),50e-6/60*60,delta=1e-9);self.assertGreater(sum(h.waste),49e-6);self.assertAlmostEqual(solvent_ledger(h),0.,places=12)
  h.invoke('flush.configure',solvent_fractions=[.5,.4,0.,0.]);self.assertRaises(ActionRejected,h.invoke,'flush.start');h.invoke('flush.configure',solvent_fractions=[.5,.5,0.,0.])
  h.invoke('flush.start');h.step(30.);self.assertEqual(h.aux['stage'],'solvent');h.invoke('flush.stop');self.assertEqual(h.aux['stage'],'release');run_until(h,lambda:h.aux is None)
  self.assertTrue(h.read()['flush.status']['stage']=='release');self.assertAlmostEqual(solvent_ledger(h),0.,places=12)
  h.invoke('positions.set',active=[False]*6);self.assertRaises(ActionRejected,h.invoke,'flush.start');h.invoke('shield.set_lift',lift_m=.1);h.invoke('positions.set',active=[True]+[False]*5)
  self.assertRaises(ActionRejected,h.invoke,'flush.start')
 def test_21_service_valves_pump_flow_test_sensors_lifts(self):
  h=device(blanks=[1]);self.assertRaises(ActionRejected,h.invoke,'pump.run',enabled=True)
  self.assertRaises(ActionRejected,h.invoke,'flow_test.start');h.invoke('lift.move',lift='cell',direction='up');h.invoke('lift.move',lift='vial',direction='up');h.step(5.)
  self.assertEqual((h.heater,h.rack),(.035,.035));self.assertEqual(h.read()['service.counters']['current_max_a'],dict(cell=.35,vial=.35))
  for v in ['solvent_1','media_solvent','position_1','outlet_waste']:h.invoke('valve.set',valve=v,open=True)
  h.invoke('pump.run',enabled=True,flow_m3_s=20e-6/60);h.step(30.);self.assertAlmostEqual(h.pump['actual_m3_s'],20e-6/60,delta=1e-9);self.assertGreater(sum(h.waste),0.);self.assertAlmostEqual(solvent_ledger(h),0.,places=12)
  h.invoke('valve.set',valve='outlet_waste',open=False);self.assertFalse(h.pump['on']);self.assertEqual(h.notice['code'],'pump_pressure_too_high')
  h.invoke('valve.set',valve='media_gas',open=True);self.assertEqual(h.valves['media'],'gas')                     # rotary valve: one path at a time
  h.invoke('valve.calibrate',valve='media');self.assertIsNone(h.valves['media']);h.invoke('valve.set',valve='position_all',open=True);self.assertEqual(h.valves['position'],[True]*6)
  h.invoke('flow_test.configure',flush_into='waste',port=2,position=1);h.invoke('flow_test.start');h.step(20.);self.assertAlmostEqual(h._readings()[1],3e5,delta=1.)
  self.assertEqual(h.read()['flow_test.status']['kind'],'flow_test');h.invoke('flow_test.stop');self.assertIsNone(h.aux);h.invoke('flow_test.configure',position=3)
  self.assertRaises(ActionRejected,h.invoke,'flow_test.start');h.invoke('sensor.configure_error',sensor=4,offset_pa=4e4);self.assertAlmostEqual(h._readings()[4],4e4)
  h.invoke('pressure.calibrate');self.assertAlmostEqual(h._readings()[4],0.);self.assertAlmostEqual(h.read()['pressure.sensors']['zero_pa'][4],4e4)
  h.invoke('service.reset_current_max');self.assertEqual(h.current_max,dict(cell=0.,vial=0.))
  h.invoke('lift.move',lift='cell',direction='down');h.step(1.);h.invoke('lift.move',lift='cell',direction='stop');x=h.heater;h.step(2.);self.assertEqual(h.heater,x)
 def test_22_skip_vial_change_halt_and_ledgers(self):
  h=setup(2,method={'cycles':2,'cycle_steps':[dict(hold_s=300.,discharge_s=30.,vial_change=True,port=0),dict(hold_s=30.,discharge_s=30.,vial_change=False,port=1)]})
  self.assertRaises(ActionRejected,h.invoke,'acquisition.skip');advance(h,'hold');h.invoke('acquisition.skip')
  self.assertEqual(h.stage,'discharge');run_until(h,lambda:h.stage=='vial_change');self.assertEqual((h.phase,h.notice['code']),('cancelled','change_vials'))
  run_until(h,lambda:h.rack==0.);h.invoke('vials.exchange',gripper_confirmed=True);self.assertEqual(sum(sum(c['vial']) for c in h.cells.values()),0.)
  h.invoke('acquisition.start');run_until(h,lambda:h.stage=='fill');self.assertEqual(h.cycle,2);self.assertEqual(h._fractions(),[1.,0.,0.,0.])
  for c in h.cells.values():self.assertAlmostEqual(c['spec']['target_mass_kg'],c['sorbed']+c['dissolved']+c['extract']+c['degraded']+c['wasted']+c['collected'],places=15)
  advance(h,'hold');h.invoke('acquisition.halt');self.assertEqual((h.phase,h.stage),('cancelled','halted'))
  p=h._maxp();h.step(5.);self.assertEqual(h._maxp(),p);self.assertRaises(ActionRejected,h.invoke,'acquisition.start');self.assertRaises(ActionRejected,h.invoke,'acquisition.abort')
  for v in ['position_all','outlet_vial']:h.invoke('valve.set',valve=v,open=True)
  run_until(h,lambda:h._maxp()<=1e5);h.invoke('lift.move',lift='cell',direction='down');h.invoke('lift.move',lift='vial',direction='down');run_until(h,lambda:h.heater==0. and h.rack==0.)
  h.invoke('shield.set_lift',lift_m=.18);h.invoke('drawer.set_extension',extension_m=.16);h.invoke('sample.unload',position=0,sample_id='soil_0',gripper_confirmed=True)
  q=h._inventory();self.assertAlmostEqual(q['initial_target_kg'],sum(q[k] for k in ['sorbed_target_kg','dissolved_target_kg','extract_target_kg','degraded_target_kg','removed_target_kg']),places=15)
  self.assertAlmostEqual(solvent_ledger(h),0.,places=12)
 def test_23_waste_vial_empty_cell_unload_and_atomic_rejections(self):
  h=finish(setup(1,method={'vial':'waste'}));self.assertEqual(h.phase,'succeeded');self.assertEqual(h.cells[0]['extract'],0.);self.assertGreater(h.cells[0]['wasted'],0.);self.assertAlmostEqual(solvent_ledger(h),0.,places=12)
  h=device(blanks=[2]);self.assertEqual(h.read()['cells.occupancy'],[None,'empty',None,None,None,None]);h.invoke('shield.set_lift',lift_m=.18);h.invoke('drawer.set_extension',extension_m=.16)
  self.assertRaises(ActionRejected,h.invoke,'cell.insert_empty',position=2,cell_volume_m3=20e-6,gripper_confirmed=True);h.invoke('cell.remove_empty',position=2,gripper_confirmed=True)
  self.assertEqual(h.read()['cells.occupancy'],[None]*6);self.assertRaises(ActionRejected,h.invoke,'cell.remove_empty',position=2,gripper_confirmed=True)
  for cap,a in [('valve.set',dict(valve='solvent_9',open=True)),('lift.move',dict(lift='cell',direction='up')),('fan.set',dict(fan='extraction',percent=101)),('method.open',dict(slot=3)),('acquisition.skip',{}),('acquisition.halt',{}),('vials.exchange',dict(gripper_confirmed=False))]:
   b=h.read('evaluator');self.assertRaises(ActionRejected,h.invoke,cap,**a);self.assertEqual(b,h.read('evaluator'))
  m=export_buchi_speedextractore_contract_metadata();self.assertTrue(set(DEVICE_CAPS)<=set(m['capabilities']))
  for k in ('settings.configure','solvents.load_default','fan.set','sensor.configure_error','seal.configure_leak','service.reset_current_max','thermal.set_eco_mode'):validate(CAPS[k][1],h.invoke(k,**CAPS[k][6]))
if __name__=='__main__':unittest.main()
