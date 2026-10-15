"""CHEM-125 finite porometry, published-curve transfer and host protocol regression."""
import copy,hashlib,importlib.util,json,math,sys,unittest
from pathlib import Path
from unittest import mock
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import porometer_poroluxrevo as rt
OBSERVED=[]
def call(h,k,**a):
 r=h.invoke(k,**a);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));return r
def setup(mode='wet_dry',instance_id='porometry_test',plant=None,sample=None,liquid=None,gas=None,method=None):
 h=rt.PorometerPoroluxrevoRuntime(instance_id)
 for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,**(plant or {}))),('sample.load',dict(rt.SAMPLE_EXAMPLE,**(sample or {}))),('liquid.load',dict(rt.LIQUID_EXAMPLE,**(liquid or {}))),('sample.wet',{}),('gas.load',dict(rt.GAS_EXAMPLE,**(gas or {}))),('holder.set',{'opened':False}),('power.set',{'enabled':True}),('method.configure',dict(rt.METHOD_EXAMPLE,mode=mode,**(method or {}))),('gas.open',{'enabled':True})]:call(h,k,**a)
 return h
def finish(h,seconds=150):
 for _ in range(round(seconds/.1)):
  h.step(.1)
  if h.phase!='running':break
 return call(h,'acquisition.result')
def vent(h):
 call(h,'pressure.vent')
 for _ in range(1000):
  h.step(.1)
  if h._pressure()<=h.plant['pressure_safe_open_pa']:return
 raise AssertionError('vent did not reach explicit safe pressure')
def cleanup(h):
 vent(h);call(h,'power.set',enabled=False);call(h,'holder.set',opened=True);call(h,'sample.remove');call(h,'liquid.unload');call(h,'gas.unload')
def analytical():
 return dict(wet_pressure_pa=[1000.,10000.,20000.,40000.],wet_flow_m3_s=[0.,1e-6,1e-5,4e-5],dry_pressure_pa=[1000.,10000.,20000.,40000.],dry_flow_m3_s=[1e-6,1e-5,2e-5,4e-5],surface_tension_n_m=.016,contact_angle_rad=0.,bubble_fraction=.05,accept_monotone_projection=False)
class TestPoroluxRevo(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,h.__dict__)
 def ledger(self,h):
  x=call(h,'ledger.read');self.assertLess(abs(x['gas_error_mol']),1e-11);self.assertLess(abs(x['liquid_error_kg']),1e-15);self.assertLess(abs(x['mass_error_kg']),1e-12);self.assertLess(abs(x['gas_energy_error_j']),1e-7);return x
 def test_01_complete_paired_actual_finite_transfer(self):
  h=setup();call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertIsNone(r['analysis_error_code']);self.assertEqual((len(r['wet_points']),len(r['dry_points'])),(6,6));self.assertLess(r['elapsed_s'],60);self.assertEqual(h.sample_liquid,0);self.assertGreater(h._pressure(),1000);self.assertLess(h.gas_stock,rt.GAS_EXAMPLE['amount_mol']);self.assertTrue(all(p['stable'] for p in r['wet_points']+r['dry_points']));self.assertGreater(r['analysis']['mean_flow_diameter_m'],1e-6);self.ledger(h)
 def test_02_separate_wet_then_dry_same_specimen(self):
  h=setup('wet');call(h,'acquisition.start');wet=finish(h);self.assertEqual(wet['phase'],'succeeded');self.assertEqual(h.sample_liquid,0);vent(h);call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,mode='dry'));call(h,'gas.open',enabled=True);call(h,'acquisition.start');dry=finish(h);self.assertEqual(dry['phase'],'succeeded');self.assertEqual(dry['wet_points'],wet['wet_points']);self.assertIsNotNone(dry['analysis']);self.ledger(h)
 def test_03_analytic_curve_difference_intersection_and_laplace(self):
  h=rt.PorometerPoroluxrevoRuntime();a=analytical();r=call(h,'analysis.porometry',**a);self.assertAlmostEqual(r['bubble_pressure_pa'],1818.1818181818182,places=9);self.assertAlmostEqual(r['mean_flow_pressure_pa'],20000.);self.assertAlmostEqual(r['bubble_diameter_m'],.064/r['bubble_pressure_pa']);self.assertAlmostEqual(r['mean_flow_diameter_m'],3.2e-6);self.assertAlmostEqual(sum(r['differential_flow_fraction']),r['covered_flow_fraction']);self.assertEqual(r['unobserved_flow_fraction'],0.)
 def test_04_unbracketed_and_truncated_not_renormalized(self):
  a=analytical();a['wet_flow_m3_s']=[1e-7,2e-6,6e-6,1.6e-5];h=rt.PorometerPoroluxrevoRuntime();r=call(h,'analysis.porometry',**a);self.assertIsNone(r['bubble_pressure_pa']);self.assertIsNone(r['mean_flow_pressure_pa']);self.assertIn('bubble_not_bracketed',r['reasons']);self.assertIn('mean_flow_not_bracketed',r['reasons']);self.assertAlmostEqual(r['covered_flow_fraction'],.3);self.assertAlmostEqual(r['unobserved_flow_fraction'],.7)
 def test_05_original_curves_preserved_projection_explicit(self):
  path=rt.ASSET/'porometry_library.json';digest=hashlib.sha256(path.read_bytes()).hexdigest();record=copy.deepcopy(rt.library()['substances']['pvdf_fc43_2016']);h=setup();self.assertEqual(h.reference,record);self.assertGreater(h.projection_changes,0);call(h,'acquisition.start');r=finish(h);self.assertIn('changes='+str(h.projection_changes),r['evidence']);self.assertEqual(digest,hashlib.sha256(path.read_bytes()).hexdigest());self.assertEqual(h.reference,record)
 def test_06_source_union_grid_bp_halfdry_and_low_threshold(self):
  rec=rt.library()['substances']['pvdf_fc43_2016'];v=rec['values'];a=dict(wet_pressure_pa=v['wet_pressure_pa'],wet_flow_m3_s=v['wet_flow_m3_s'],dry_pressure_pa=v['dry_pressure_pa'],dry_flow_m3_s=v['dry_flow_m3_s'],surface_tension_n_m=.016,contact_angle_rad=0.,bubble_fraction=.05,accept_monotone_projection=True);h=rt.PorometerPoroluxrevoRuntime();r=call(h,'analysis.porometry',**a)
  # Independent piecewise-linear curve difference, not interpolation of Qwet/Qdry.
  grid=sorted(set(a['wet_pressure_pa']+a['dry_pressure_pa']));grid=[p for p in grid if 0<p<=min(a['wet_pressure_pa'][-1],a['dry_pressure_pa'][-1])];w=np.interp(grid,a['wet_pressure_pa'],a['wet_flow_m3_s']);d=np.interp(grid,a['dry_pressure_pa'],a['dry_flow_m3_s'])
  for f,key in [(.05,'bubble_pressure_pa'),(.5,'mean_flow_pressure_pa')]:
   delta=w-f*d;i=next(i for i in range(len(grid)-1) if delta[i]<0<=delta[i+1]);expected=grid[i]-delta[i]*(grid[i+1]-grid[i])/(delta[i+1]-delta[i]);self.assertAlmostEqual(r[key],expected,places=8)
  a['bubble_fraction']=.01;r=call(h,'analysis.porometry',**a);self.assertIsNone(r['bubble_pressure_pa']);self.assertIn('bubble_not_bracketed',r['reasons']);self.assertGreater(r['unobserved_flow_fraction'],0)
 def test_07_cancel_retains_residual_pressure_liquid_and_frozen_result(self):
  h=setup();call(h,'acquisition.start');h.step(1.);self.assertGreater(h._pressure(),50);self.assertGreater(h.sample_liquid,0);before=(h.sample_liquid,h.gas_stock,h._pressure());call(h,'acquisition.stop');self.assertEqual(before,(h.sample_liquid,h.gas_stock,h._pressure()));saved=call(h,'acquisition.result');h.step(1.);self.assertEqual(saved,h.invoke('acquisition.result'));self.assertFalse(h.inlet);self.assertEqual(saved['phase'],'cancelled');self.ledger(h)
 def test_08_pressure_open_interlock_and_host_driven_vent(self):
  h=setup();call(h,'pressure.set',pressure_pa=20000.);self.assertEqual(h._pressure(),0);self.assertEqual(h.time_s,0);h.step(.5);self.rejected(h,'holder.set','PRESSURIZED',opened=True);call(h,'gas.open',enabled=False);self.rejected(h,'holder.set','PRESSURIZED',opened=True);p=h._pressure();call(h,'pressure.vent');self.assertEqual(h._pressure(),p);vent(h);call(h,'holder.set',opened=True);self.assertTrue(h.holder_open)
 def test_09_incomplete_wetting_and_dry_order_atomic(self):
  h=setup();call(h,'acquisition.start');h.step(1.);call(h,'acquisition.stop');call(h,'gas.open',enabled=True);self.rejected(h,'acquisition.start','COMPLETE_WETTING_REQUIRED');call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,mode='dry'));self.rejected(h,'acquisition.start','SAME_DRY_SPECIMEN_REQUIRED')
 def test_10_insufficient_pressure_does_not_clear_residual_liquid(self):
  h=setup(method={'pressure_steps_pa':[5000.,15000.,24000.]});call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'INCOMPLETE_DRAINAGE');self.assertGreater(h.sample_liquid,0);self.assertEqual(r['dry_points'],[]);self.ledger(h)
 def test_11_tiny_gas_finite_failure_has_last_observation(self):
  h=setup(gas={'amount_mol':1e-15});call(h,'acquisition.start');h.step(.1);r=call(h,'acquisition.result');self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'GAS_EXHAUSTED');self.assertEqual(r['elapsed_s'],.1);self.assertEqual(r['rows'][-1]['time_s'],.1);self.assertEqual(h.gas_stock,0);self.assertFalse(h.inlet);self.assertGreaterEqual(h.cell,h.initial_cell);self.ledger(h)
 def test_12_liquid_exact_inventory_no_tolerance_overspend(self):
  h=rt.PorometerPoroluxrevoRuntime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,liquid_capacity_kg=1e-12+1e-20));h.invoke('liquid.load',**dict(rt.LIQUID_EXAMPLE,mass_kg=1e-12));self.rejected(h,'sample.wet','FINITE_LIQUID_INVENTORY');self.assertEqual(h.liquid_stock,1e-12);self.ledger(h)
 def test_13_small_liquid_inventory_conserved_after_full_sequence(self):
  h=setup(sample={'liquid_capacity_kg':1e-12},liquid={'mass_kg':1e-12});h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(h.sample_liquid,0.);self.assertEqual(h.liquid_stock,0.);self.assertAlmostEqual(h.liquid_recovered,1e-12,places=24);self.ledger(h)
 def test_14_pure_reads_no_time_and_target_not_truth(self):
  h=setup();before=copy.deepcopy(h.__dict__);h.describe();h.session().read(instance_id=h.instance_id);h.invoke('acquisition.result');h.invoke('ledger.read');call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);h.invoke('analysis.porometry',**analytical());self.assertEqual(before,h.__dict__);call(h,'pressure.set',pressure_pa=15000.);self.assertEqual(h._pressure(),0.);h.step(.1);self.assertGreater(h._pressure(),0.);self.assertLess(h._pressure(),15000.)
 def test_15_host_partition_and_stiff_relaxation_stability(self):
  args={'regulator_tau_s':.001,'vent_tau_s':.001,'pressure_sensor_tau_s':.001,'flow_sensor_tau_s':.001};a=setup(plant=args,sample={'drain_tau_s':.001});b=setup(plant=args,sample={'drain_tau_s':.001});a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(35.)
  for _ in range(350):b.step(.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);self.assertTrue(math.isfinite(a.cell));self.assertGreaterEqual(a.cell,a.initial_cell);self.assertEqual(a.phase,'succeeded');self.ledger(a);before=copy.deepcopy(a.__dict__);a.step(0.);self.assertEqual(before,a.__dict__)
 def test_16_double_instances_isolated(self):
  a=setup(instance_id='a');b=setup(instance_id='b');before=copy.deepcopy(b.__dict__);a.invoke('acquisition.start');a.step(10.);self.assertEqual(before,b.__dict__)
 def test_17_agent_truth_and_recovery_access_denied(self):
  h=setup();s=h.session();meta=s.describe(instance_id=h.instance_id);self.assertFalse(any(k.startswith('truth.') for k in meta['state_slots']));self.assertNotIn('sample.remove',meta['capabilities']);before=copy.deepcopy(h.__dict__)
  for k,a in [('sample.remove',{}),('liquid.unload',{}),('gas.unload',{}),('ledger.read',{}),('sim.step',{'dt_s':1.})]:
   r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=a,request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.sample_liquid_kg'])
  with self.assertRaises(rt.ActionRejected):h.register_principal('agent','evaluator')
 def test_18_owner_result_poll_cancel_and_query_no_steal(self):
  h=setup();h.register_principal('other');s=h.session();other=h.session('other');op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];before=copy.deepcopy(h.__dict__)
  for k in ['acquisition.result','acquisition.stop']:
   r=other.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaises(rt.ActionRejected):other.poll(op)
  with self.assertRaises(rt.ActionRejected):other.cancel(op,'cancel')
  q=s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='query');self.assertIsNone(q['operation_id']);self.assertEqual(h._owners[op],'agent');self.assertEqual(s.cancel(op,'cancel')['status'],'succeeded')
 def test_19_request_idempotency_and_conflict(self):
  h=setup();s=h.session();a=dict(instance_id=h.instance_id,capability_id='pressure.set',arguments={'pressure_pa':15000.},request_id='x');first=s.invoke(**a);before=copy.deepcopy(h.__dict__);self.assertEqual(first,s.invoke(**a));self.assertEqual(before,h.__dict__);a['arguments']={'pressure_pa':16000.};self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT');self.assertEqual(before,h.__dict__)
 def test_20_historical_source_result_after_remove_and_new_specimen(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];finish(h);saved=s.poll(op);cleanup(h);call(h,'sample.load',**rt.SAMPLE_EXAMPLE);self.assertEqual(saved,s.poll(op));self.assertEqual(saved['result'],h.invoke('acquisition.result'));saved['result']['rows'][0]['pressure_pa']=999.;self.assertNotEqual(saved,s.poll(op));call(h,'power.set',enabled=True);call(h,'holder.set',opened=False);call(h,'gas.load',**rt.GAS_EXAMPLE);call(h,'gas.open',enabled=True);call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,mode='dry'));self.rejected(h,'acquisition.start','SAME_DRY_SPECIMEN_REQUIRED')
 def test_21_unknown_properties_runtime_usable_and_aperture(self):
  h=rt.PorometerPoroluxrevoRuntime();self.rejected(h,'plant.configure','MODEL_NOT_ACCEPTED',**dict(rt.PLANT_EXAMPLE,accept_engineering=False));h.invoke('plant.configure',**rt.PLANT_EXAMPLE)
  for a,error in [(dict(accept_engineering=False),'MODEL_NOT_ACCEPTED'),(dict(reference_id='missing'),'INVALID_REFERENCE'),(dict(accept_unknown_conditions=False),'UNKNOWN_REFERENCE_CONDITIONS'),(dict(active_diameter_m=.025),'REFERENCE_APERTURE'),(dict(diameter_m=.013),'REFERENCE_APERTURE'),(dict(accept_monotone_projection=False),'NONMONOTONE_FLOW_RATIO')]:self.rejected(h,'sample.load',error,**dict(rt.SAMPLE_EXAMPLE,**a))
  lib=copy.deepcopy(rt.library());lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']]['runtime_usable']=False
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','INVALID_REFERENCE',**rt.SAMPLE_EXAMPLE)
 def test_22_reference_temperature_and_liquid_transform_acceptance(self):
  h=rt.PorometerPoroluxrevoRuntime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);lib=copy.deepcopy(rt.library());lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']]['conditions']['temperature_k']=303.15
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','REFERENCE_TEMPERATURE',**rt.SAMPLE_EXAMPLE)
  h.invoke('sample.load',**rt.SAMPLE_EXAMPLE);h.invoke('liquid.load',**dict(rt.LIQUID_EXAMPLE,surface_tension_n_m=.020));self.rejected(h,'sample.wet','REFERENCE_LIQUID_MISMATCH');h.invoke('sample.remove');h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,accept_pressure_transform=True));call(h,'sample.wet');self.assertAlmostEqual(h.pressure_scale,1.25)
 def test_23_hardware_reference_and_temperature_ranges(self):
  h=setup();self.rejected(h,'pressure.set',pressure_pa=rt.MAX_PRESSURE+1.);self.rejected(h,'pressure.set','REFERENCE_PRESSURE_RANGE',pressure_pa=148351.);self.rejected(h,'method.configure','REFERENCE_PRESSURE_RANGE',**dict(rt.METHOD_EXAMPLE,pressure_steps_pa=[1000.,50000.,149000.]));self.rejected(h,'method.configure','INVALID_PRESSURE_STEPS',**dict(rt.METHOD_EXAMPLE,pressure_steps_pa=[1000.,1000.,3000.]));self.rejected(h,'method.configure','INVALID_DWELL',**dict(rt.METHOD_EXAMPLE,minimum_dwell_s=4.));fresh=rt.PorometerPoroluxrevoRuntime()
  for t in [278.14,323.16]:self.rejected(fresh,'plant.configure',**dict(rt.PLANT_EXAMPLE,temperature_k=t))
  fresh.invoke('plant.configure',**rt.PLANT_EXAMPLE);fresh.invoke('gas.load',**rt.GAS_EXAMPLE);fresh.invoke('holder.set',opened=False);fresh.invoke('power.set',enabled=True);fresh.invoke('pressure.set',pressure_pa=rt.MAX_PRESSURE);fresh.invoke('gas.open',enabled=True);fresh.step(5.);self.assertLessEqual(fresh._pressure(),rt.MAX_PRESSURE+1e-6);self.assertGreater(fresh._pressure(),rt.MAX_PRESSURE-1.)
 def test_24_maxdwell_and_complete_stability_window(self):
  h=setup(plant={'regulator_tau_s':10.},method={'maximum_dwell_s':.5,'minimum_dwell_s':.3});h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'STABILITY_TIMEOUT');self.assertEqual(r['wet_points'],[])
  h=setup(method={'maximum_dwell_s':.5,'minimum_dwell_s':.3});h.invoke('acquisition.start')
  with mock.patch.object(h,'_indications',side_effect=lambda:(h.target,1e-4 if h._ticks%4==2 else 0.)):
   h.step(.4);self.assertEqual(h.wet_points,[]);self.assertEqual(h.phase,'running');h.step(.1);self.assertEqual(h.error,'STABILITY_TIMEOUT')
 def test_25_flow_range_failure_caps_actual_finite_transfer(self):
  h=setup('wet');h.invoke('acquisition.start');finish(h);h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,mode='dry'));h.invoke('gas.open',enabled=True);h.dry_values=[v*100 for v in h.dry_values];h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'FLOW_RANGE');self.assertLessEqual(h.flow,rt.MAX_FLOW+1e-15);self.assertFalse(h.inlet);self.ledger(h)
 def test_26_zero_offsets_are_external_not_truth_calibration(self):
  h=setup();call(h,'sensor.calibrate',pressure_zero_pa=13.,flow_zero_m3_s=1e-7);self.assertEqual(h.read()['pressure.indicated_pa'],-13.);self.assertEqual(h.read()['flow.indicated_m3_s'],-1e-7);self.assertEqual(h._pressure(),0.);self.assertEqual(h.time_s,0.)
 def test_27_invalid_observed_flow_retained_with_analysis_error(self):
  h=setup();h.invoke('sensor.calibrate',pressure_zero_pa=0.,flow_zero_m3_s=1e-6);h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertLess(r['wet_points'][0]['flow_m3_s'],0.);self.assertIsNone(r['analysis']);self.assertEqual(r['analysis_error_code'],'INVALID_TIME_SERIES')
 def test_28_atomic_invalid_numbers_busy_and_sample_access(self):
  h=setup()
  for value in [True,float('nan'),float('inf'),-1.]:self.rejected(h,'pressure.set',pressure_pa=value)
  before=copy.deepcopy(h.__dict__)
  for dt in [True,float('nan'),float('inf'),-1.]:
   with self.assertRaises(rt.ActionRejected):h.step(dt)
   self.assertEqual(before,h.__dict__)
  self.rejected(h,'sample.remove','PRESSURIZED');h.invoke('acquisition.start');self.rejected(h,'method.configure','BUSY',**rt.METHOD_EXAMPLE);self.rejected(h,'power.set','BUSY',enabled=False)
 def test_29_cleanup_mass_energy_and_service_interlock(self):
  h=setup();self.rejected(h,'service.set','PRESSURIZED',distance_m=.08);h.invoke('acquisition.start');finish(h);cleanup(h);call(h,'service.set',distance_m=.08);self.rejected(h,'power.set','NOT_READY',enabled=True);call(h,'service.set',distance_m=0.);self.assertEqual(h.membrane_loaded,h.membrane_recovered);self.assertEqual(h.liquid_stock,0);self.assertEqual(h.sample_liquid,0);l=self.ledger(h);self.assertAlmostEqual(l['gas_enthalpy_in_j']-l['gas_enthalpy_out_j']-l['isothermal_heat_removed_j'],l['gas_stored_energy_j'],places=7)
 def test_30_unsupported_and_honest_model_scope(self):
  h=setup()
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
  meta=rt.export_porometer_poroluxrevo_contract_metadata();self.assertEqual(meta['capabilities']['algorithm.mp2']['availability'],'unsupported');text=json.dumps(meta);self.assertIn('13nm',text);self.assertIn('reference',text);self.assertEqual(rt.MAX_PRESSURE,3.45e6);self.assertGreater(.064/rt.MAX_PRESSURE,13e-9)
 def test_31_actual_state_effects_declared(self):
  meta=rt.export_porometer_poroluxrevo_contract_metadata();original=rt.PorometerPoroluxrevoRuntime.invoke
  def audited(h,k,**a):
   before=h.read();r=original(h,k,**a);after=h.read();changed={x for x in before if before[x]!=after[x]};declared={e['state_id'] for e in meta['capabilities'][k]['state_effects'] if e['timing']=='immediate'};self.assertFalse(changed-declared,(k,changed-declared));return r
  with mock.patch.object(rt.PorometerPoroluxrevoRuntime,'invoke',audited):
   h=setup();call(h,'sensor.calibrate',pressure_zero_pa=1.,flow_zero_m3_s=1e-8);call(h,'pressure.set',pressure_pa=0.);call(h,'acquisition.start');call(h,'sim.step',dt_s=.2);call(h,'acquisition.stop');cleanup(h);call(h,'service.set',distance_m=.08)
 def test_31b_disjoint_curve_domains_reject_without_extrapolation(self):
  h=rt.PorometerPoroluxrevoRuntime();a=analytical();a['dry_pressure_pa']=[50000.,60000.,70000.,80000.];self.rejected(h,'analysis.porometry','INSUFFICIENT_COMMON_RANGE',**a);lib=copy.deepcopy(rt.library());r=lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']];r['values']['dry_pressure_pa']=[p+200000. for p in r['values']['dry_pressure_pa']];h.invoke('plant.configure',**rt.PLANT_EXAMPLE)
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','INSUFFICIENT_COMMON_RANGE',**rt.SAMPLE_EXAMPLE)
 def test_31c_vent_timeout_is_terminal_and_conserved(self):
  h=setup(plant={'vent_tau_s':100.,'gas_volume_m3':.001});h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'VENT_TIMEOUT');self.assertGreater(h._pressure(),h.plant['pressure_safe_open_pa']);self.assertFalse(h.inlet);self.rejected(h,'holder.set','PRESSURIZED',opened=True);self.ledger(h)
 def test_31d_dry_analysis_preserves_wetting_liquid_snapshot(self):
  h=setup('wet');h.invoke('acquisition.start');wet=finish(h);vent(h);h.invoke('holder.set',opened=True);h.invoke('liquid.unload');h.invoke('liquid.load',**dict(rt.LIQUID_EXAMPLE,name='other engineering stock',surface_tension_n_m=.03));h.invoke('holder.set',opened=False);h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,mode='dry'));h.invoke('gas.open',enabled=True);h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(r['wet_points'],wet['wet_points']);self.assertEqual(h.operation_liquid['surface_tension_n_m'],.016);self.assertAlmostEqual(r['analysis']['mean_flow_diameter_m']*r['analysis']['mean_flow_pressure_pa'],.064)
 # ---- 2026-10 extension: published capillary-flow-porometry scalars of >=50 distinct membranes ----
 def test_31e_published_scalar_coverage_and_provenance(self):
  lib=rt.library();sub=lib['substances'];scal={k:v for k,v in sub.items() if v.get('scalar_usable')}
  self.assertGreaterEqual(len(scal),50);self.assertGreaterEqual(len({v['material_group'] for v in scal.values()}),30);self.assertGreaterEqual(len({v['membrane_class'] for v in scal.values()}),10)
  self.assertEqual([k for k,v in sub.items() if v['runtime_usable']],['pvdf_fc43_2016'])
  for k,v in sub.items():
   if v.get('extension')!='2026-10':continue
   src=v['source'];self.assertEqual(hashlib.sha256((ROOT/src['file']).read_bytes()).hexdigest(),src['sha256'],k);self.assertIn(v['licence'],['CC-BY-4.0','CC0-1.0'],k);self.assertEqual(v['evidence'],'measured_literature')
   self.assertFalse(any(x.endswith('flow_m3_s') for x in v['values']),k);self.assertTrue(any(v['values'].get(f) for f in ['published_bubble_point_pore_diameter_m','published_mean_flow_pore_diameter_m','published_mean_pore_diameter_m','published_smallest_pore_diameter_m']),k)
   for f,x in v['values'].items():
    if f.endswith('_m'):self.assertTrue(1e-9<x<1e-3,(k,f,x))
    if f.endswith('_pa'):self.assertTrue(1e2<x<1e7,(k,f,x))
  ot=json.loads((rt.ASSET/'output_templates.json').read_text());self.assertEqual(ot['scalar_usable_count'],len(scal));self.assertEqual(ot['coverage_status'],'meets_50_distinct_membranes_scalar_level')
 def test_31f_pore_scalars_laplace_across_classes(self):
  h=rt.PorometerPoroluxrevoRuntime('scalars')
  for ref in ['glass_fibre_whatman_s14','alumina_ceramic_element','cnt_buckypaper_i','pa6_membrane_m0','ptfe_md_membrane_commercial','pvdf_fc43_2016','pu_washed_2025','pp_microporous_membrane_pristine','nonwoven_novatexx_2483','os_cse_fbp_membrane']:
   rec=rt.library()['substances'][ref]
   for gamma,theta in [(.016,0.),(.0217,.2)]:
    out=call(h,'reference.pore_scalars',reference_id=ref,surface_tension_n_m=gamma,contact_angle_rad=theta);self.assertEqual(out['material_group'],rec['material_group']);self.assertTrue(out['scalars'],ref)
    for row in out['scalars']:
     self.assertAlmostEqual(row['pressure_pa'],4*gamma*math.cos(theta)/row['diameter_m'],delta=1e-9*row['pressure_pa']);self.assertEqual(row['within_instrument_range'],row['pressure_pa']<=rt.MAX_PRESSURE)
     field={'bubble_point':'published_bubble_point_pore_diameter_m','mean_flow':'published_mean_flow_pore_diameter_m','mean_pore':'published_mean_pore_diameter_m','smallest_pore':'published_smallest_pore_diameter_m'}[row['kind']];self.assertEqual(row['diameter_m'],rec['values'][field])
  out=call(h,'reference.pore_scalars',reference_id='alumina_ceramic_element',surface_tension_n_m=.016,contact_angle_rad=0.);bp=next(r for r in out['scalars'] if r['kind']=='bubble_point');self.assertAlmostEqual(bp['published_pressure_pa'],2.98e5)
  w=call(h,'reference.pore_scalars',reference_id='cnt_buckypaper_i',surface_tension_n_m=.072,contact_angle_rad=0.);self.assertFalse(w['scalars'][0]['within_instrument_range']);self.assertTrue(any('3.45 MPa' in x for x in w['warnings']))
  pp=call(h,'reference.pore_scalars',reference_id='pp_microporous_membrane_pristine',surface_tension_n_m=.0159,contact_angle_rad=0.);self.assertTrue(any('published pressure differs' in x for x in pp['warnings']))
 def test_31g_scalar_records_never_drive_flow_acquisition(self):
  h=rt.PorometerPoroluxrevoRuntime('scalar_guard');call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'holder.set',opened=True)
  self.rejected(h,'sample.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id='glass_fibre_whatman_s14'))
  for ref in ['pvdf_galpore_2016','pu_washed_ratio50_2025','unknown_membrane']:self.rejected(h,'reference.pore_scalars','INVALID_REFERENCE',reference_id=ref,surface_tension_n_m=.016,contact_angle_rad=0.)
 def test_31h_scalar_values_recheck_cached_xml(self):
  import xml.etree.ElementTree as ET
  rec=rt.library()['substances']['glass_fibre_whatman_s14'];root=ET.parse(ROOT/rec['source']['file']).getroot()
  tw=next(t for t in root.iter('table-wrap') if ''.join(t.find('label').itertext()).strip()=='Table 2');rows=[[' '.join(''.join(c.itertext()).split()) for c in tr] for tr in tw.iter('tr')]
  row=next(r for r in rows if r[0].startswith('Largest pore diameter'));self.assertAlmostEqual(rec['values']['published_bubble_point_pore_diameter_m'],float(row[1])*1e-6)
  rep=json.loads((ROOT/'outputs/expansion_batch03/t4_measure/porometer_poroluxrevo/extension_scalar_validation.json').read_text());self.assertEqual(rep['status'],'passed');self.assertGreaterEqual(rep['records'],50)
 def test_32_all_actual_api_and_state_schema(self):
  spec=importlib.util.spec_from_file_location('porolux_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_porometer_poroluxrevo_contract_metadata();h=setup();call(h,'acquisition.start');call(h,'sim.step',dt_s=35.);call(h,'acquisition.result');self.ledger(h)
  for k,c in rt.CAPS.items():
   if k in rt.UNSUPPORTED:continue
   cases=[x for x in OBSERVED if x[0]==k];self.assertTrue(cases,k)
   for _,a,r in cases:self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema']),[],k);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema']),[],k)
  for k,v in h.read().items():self.assertEqual(tool.validate_value(v,meta['state_slots'][k]['value_schema']),[],k)
if __name__=='__main__':unittest.main()
