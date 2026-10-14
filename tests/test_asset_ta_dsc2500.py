"""DSC: real thermodynamics, finite enthalpy, observation analysis and owner protocol."""
import copy,hashlib,importlib.util,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import ta_dsc2500 as rt
OBSERVED=[];STATES=[];SCHEMA_REPORT={}
def canonical(v):
 if isinstance(v,dict):return {str(k):canonical(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)):return [canonical(x) for x in v]
 return v
def state(h):return json.dumps(canonical(h.__dict__),sort_keys=True,allow_nan=False)
def call(h,k,**a):
 before=h.read();r=h.invoke(k,**a);after=h.read();json.dumps(r,allow_nan=False);json.dumps(after,allow_nan=False);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));STATES.append(after);changed={x for x in before if before[x]!=after[x]}
 if not changed<=set(rt.CAPS[k]['effects']):raise AssertionError((k,'undeclared immediate effects',changed-set(rt.CAPS[k]['effects'])))
 return r
def step(h,t):h.step(t);STATES.append(h.read());json.dumps(h.read(),allow_nan=False)
def setup(name='dsc_test',plant=None,sample=None,gas=None,method=None,warm=True):
 h=rt.TaDsc2500Runtime(name);call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,**(plant or {})));call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,**(sample or {})));call(h,'gas.load',**dict(rt.GAS_EXAMPLE,**(gas or {})));call(h,'power.set',enabled=True);call(h,'sensor.calibrate',**rt.CALIBRATION_EXAMPLE);call(h,'lid.set',opened=False);step(h,2.1);call(h,'gas.set_flow',flow_m3_s=50e-6/60);call(h,'gas.set_open',opened=True);call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,**(method or {})))
 if warm:
  call(h,'thermal.set_temperature',temperature_k=h.method['start_temperature_k'],rate_k_s=min(1.,h.plant['maximum_program_rate_k_s']))
  for _ in range(3000):
   step(h,.1)
   if max(abs(h.read()[k]-h.method['start_temperature_k']) for k in ['furnace.indicated_k','sample.indicated_k','reference.indicated_k'])<h.method['start_tolerance_k']:break
  else:raise AssertionError('preconditioning did not settle')
 return h
def run(h):
 call(h,'acquisition.start')
 for _ in range(30000):
  step(h,.1)
  if h.phase!='running':break
 if h.phase=='running':raise AssertionError('no terminal state')
 return call(h,'acquisition.result')
def cool(h):
 if h.phase=='running':call(h,'acquisition.stop')
 call(h,'thermal.disable')
 for _ in range(3000):
  if h._cold():break
  step(h,.1)
 else:raise AssertionError('failed cooling')
def cleanup(h):
 cool(h);call(h,'gas.set_open',opened=False);call(h,'lid.set',opened=True);step(h,2.1);call(h,'sample.remove');call(h,'gas.unload');call(h,'power.set',enabled=False)
def quick(**changes):
 m=dict(start_temperature_k=298.15,segments=[dict(target_k=300.15,rate_k_s=.2,hold_s=1.)]);m.update(changes);return m
class TestDSC2500(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=state(h)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(state(h),before)
 def ledger(self,h):
  l=call(h,'ledger.read');self.assertLess(abs(l['sample_error_kg']),1e-17);self.assertLess(abs(l['gas_error_mol']),1e-11);self.assertLess(abs(l['energy_error_j']),1e-6);return l
 def test_01_real_indium_naphthalene_source_and_no_raw_curve_fabrication(self):
  lib=rt.library()['substances'];a=lib['indium_srm2232a'];b=lib['naphthalene_nist'];self.assertEqual(a['values']['melting_temperature_k'],429.7485);self.assertEqual(a['values']['fusion_enthalpy_j_kg'],28580.);self.assertEqual(b['values']['melting_temperature_k'],353.44);self.assertAlmostEqual(b['values']['fusion_enthalpy_j_kg'],18811/.1281705);self.assertTrue(a['runtime_usable']);self.assertTrue(b['runtime_usable']);self.assertTrue(a.get('source'));self.assertGreater(len(lib),2)
 def test_02_real_two_materials_drive_different_observed_peaks(self):
  peaks=[]
  for rid,tm in [('indium_srm2232a',429.7485),('naphthalene_nist',353.44)]:
   method=dict(start_temperature_k=tm-5,segments=[dict(target_k=tm+7,rate_k_s=1/6,hold_s=20.)]);h=setup(sample={'reference_id':rid},method=method);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['elapsed_s'],92.,places=7);self.assertGreater(h._fraction(),.99);peaks.append(max(r['rows'],key=lambda x:x['heatflow_w'])['sample_k']);self.ledger(h)
  self.assertGreater(peaks[0]-peaks[1],70)
 def test_03_enthalpy_inverse_and_fraction_bounded(self):
  for c in [1e-7,.02,100.]:
   for latent in [0.,.05716,100.]:
    for t in np.linspace(290.,320.,101):
     e=rt.enthalpy(t,298.15,c,latent,305.,.2);v=rt.enthalpy_temperature(e,298.15,c,latent,305.,.2);self.assertAlmostEqual(v,t,delta=1e-7);self.assertGreaterEqual(rt.phase_fraction(v,305.,.2),0);self.assertLessEqual(rt.phase_fraction(v,305.,.2),1)
 def test_04_targets_are_not_actual_and_queries_do_not_advance(self):
  h=setup(warm=False,method=quick());t=h.furnace;call(h,'thermal.set_temperature',temperature_k=320.,rate_k_s=.2);self.assertEqual(h.furnace,t);self.assertEqual(h.target,298.15);before=state(h);h.read();h.describe();call(h,'acquisition.result');call(h,'ledger.read');call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);self.assertEqual(state(h),before);step(h,1.);self.assertAlmostEqual(h.target,298.35);self.assertLess(h.furnace,h.target)
 def test_05_program_rates_holds_and_terminal_record_alignment(self):
  h=setup(warm=False,method=quick(segments=[dict(target_k=300.15,rate_k_s=.2,hold_s=1.),dict(target_k=299.15,rate_k_s=.1,hold_s=.5)]));r=run(h);self.assertAlmostEqual(r['elapsed_s'],21.5);self.assertAlmostEqual(r['rows'][-1]['elapsed_s'],r['elapsed_s']);self.assertTrue(any(abs(x['elapsed_s']-10)<1e-8 for x in r['rows']));self.assertTrue(any(x['stage']=='hold' for x in r['rows']));self.assertEqual(r['rows'][-1]['segment_index'],1);self.assertFalse(h.thermal_enabled);self.ledger(h)
 def test_06_observed_baseline_enthalpy_not_free_source_value(self):
  h=setup();r=run(h);rows=r['rows'];t=[x['elapsed_s'] for x in rows];q=[x['heatflow_w'] for x in rows];T=[x['sample_k'] for x in rows];baseline=[0.]*len(t);a=call(h,'analysis.integrate',time_s=t,heatflow_w=q,temperature_k=T,baseline_w=baseline,sample_mass_kg=2e-6);self.assertAlmostEqual(a['energy_j'],float(np.trapezoid(q,t)));self.assertNotEqual(a['specific_enthalpy_j_kg'],28580.);b=call(h,'analysis.integrate',time_s=t,heatflow_w=q,temperature_k=T,baseline_w=[.0001]*len(t),sample_mass_kg=4e-6);self.assertAlmostEqual(b['energy_j'],a['energy_j']-.0001*92.,places=12);self.assertNotEqual(a['specific_enthalpy_j_kg'],b['specific_enthalpy_j_kg']);self.assertIn('not extrapolated onset',a['evidence'])
 def test_07_sample_pan_reference_energy_and_latent_inventory(self):
  h=setup();before=self.ledger(h);r=run(h);after=self.ledger(h);m=h.sample['mass_kg'];deltaS=after['sample_stored_j']-before['sample_stored_j'];expected=h._cp()*(h.sample_temperature-r['rows'][0]['sample_k'])+m*28580.;self.assertAlmostEqual(deltaS,expected,places=10);self.assertEqual(after['sample_present_kg'],before['sample_present_kg']);self.assertGreater(after['heater_input_j'],before['heater_input_j']);self.assertGreater(after['ambient_loss_j'],before['ambient_loss_j'])
 def test_08_full_cooling_preserves_mass_and_exports_heat(self):
  h=setup();r=run(h);fraction=h._fraction();energy=h._ledger()['sample_stored_j'];cool(h);self.assertEqual(fraction,1.);self.assertEqual(h._fraction(),0.);self.assertLess(h.sample_energy,energy);l=self.ledger(h);self.assertEqual(l['sample_present_kg'],2e-6);self.assertGreater(l['ambient_loss_j'],0);cleanup(h);l=self.ledger(h);self.assertEqual(l['sample_recovered_kg'],2e-6);self.assertEqual(l['sample_present_kg'],0.)
 def test_09_cancel_retains_partial_melt_and_frozen_history(self):
  h=setup();call(h,'acquisition.start')
  for _ in range(800):
   step(h,.1)
   if .2<h._fraction()<.8:break
  f=h._fraction();self.assertGreater(f,0);self.assertLess(f,1);call(h,'acquisition.stop');r=call(h,'acquisition.result');self.assertEqual(h._fraction(),f);self.assertFalse(h.thermal_enabled);step(h,10.);self.assertEqual(r,call(h,'acquisition.result'));self.assertNotEqual(h._fraction(),f);self.ledger(h)
 def test_10_facs_cannot_cool_below_ambient_or_use_ln2_range(self):
  h=setup(warm=False,method=quick());self.rejected(h,'thermal.set_temperature','COOLER_TEMPERATURE_RANGE',temperature_k=200.,rate_k_s=1.);call(h,'thermal.set_temperature',temperature_k=320.,rate_k_s=1.);step(h,30.);call(h,'thermal.set_temperature',temperature_k=298.15,rate_k_s=1.);step(h,100.);self.assertGreaterEqual(h.furnace,298.15-1e-10);self.assertGreater(h.cooler_removed,0);self.ledger(h)
 def test_11_external_cooler_distinct_ranges_and_signed_ambient_heat(self):
  for name,lo,hi in [('RCS120',153.15,673.15),('RCS90',183.15,823.15),('RCS40',233.15,673.15),('LN2P',93.15,823.15)]:
   h=setup(plant={'cooler':name},warm=False,method=quick());self.rejected(h,'thermal.set_temperature','COOLER_TEMPERATURE_RANGE',temperature_k=hi+.01,rate_k_s=1.);self.assertEqual(h._limits(),(lo,hi));call(h,'thermal.set_temperature',temperature_k=283.15,rate_k_s=1.);step(h,30.);self.assertLess(h.furnace,298.15);self.assertLess(h.ambient_loss,0.);self.assertGreater(h.cooler_removed,0.);self.ledger(h)
 def test_12_stiff_small_capacity_stable_and_conservative(self):
  h=setup(plant={'furnace_capacity_j_k':1e-5,'sample_pan_capacity_j_k':1e-7,'reference_pan_capacity_j_k':1e-7,'sample_conductance_w_k':1000.,'reference_conductance_w_k':1000.,'controller_gain_w_k':1e5},sample={'mass_kg':1e-10},method=quick(),warm=False);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertTrue(all(298.14<x['sample_k']<301 for x in r['rows']));self.ledger(h)
 def test_13_finite_gas_event_exact_time_and_terminal_partial(self):
  amount=1e-9;h=setup(gas={'amount_mol':amount},method=quick(),warm=False);rate=h.flow*101325/(rt.R_GAS*298.15);call(h,'acquisition.start');step(h,.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'GAS_EXHAUSTED');self.assertAlmostEqual(h.elapsed,amount/rate,places=13);self.assertEqual(h.gas_remaining,0.);self.assertFalse(h.gas_open);self.assertFalse(h.thermal_enabled);self.assertEqual(h.rows[-1]['elapsed_s'],h.elapsed);self.assertEqual(h.time_s,2.2);self.ledger(h)
 def test_14_tiny_gas_amount_and_large_dt_has_finite_terminal(self):
  h=setup(gas={'amount_mol':1e-18},method=quick(),warm=False);r=run(h);self.assertEqual(r['error_code'],'GAS_EXHAUSTED');self.assertLess(r['elapsed_s'],1e-10);self.assertEqual(h.gas_remaining,0.);self.ledger(h)
 def test_15_gas_is_consumed_only_when_open_and_mass_recovered(self):
  h=setup(method=quick(),warm=False);call(h,'gas.set_open',opened=False);left=h.gas_remaining;step(h,1.);self.assertEqual(left,h.gas_remaining);call(h,'gas.set_open',opened=True);step(h,1.);rate=h.flow*101325/(rt.R_GAS*298.15);self.assertAlmostEqual(left-h.gas_remaining,rate,places=13);call(h,'gas.set_open',opened=False);call(h,'gas.unload');l=self.ledger(h);self.assertAlmostEqual(l['gas_loaded_mol'],l['gas_recovered_mol']+l['gas_exhausted_mol'],places=13)
 def test_16_hot_lid_sample_remove_and_sampler_guards_are_atomic(self):
  h=setup();self.rejected(h,'lid.set','HOT_CELL',opened=True);self.rejected(h,'sample.remove','HOT_CELL');self.rejected(h,'sampler.move','HOT_CELL',x_m=.1,y_m=.05,z_m=.06);self.rejected(h,'emergency.reset','HOT_CELL');self.ledger(h)
 def test_17_lid_and_sampler_finite_motion_clearance(self):
  h=setup(warm=False,method=quick());self.rejected(h,'sampler.move','SAMPLER_CLEARANCE',x_m=.165,y_m=0.,z_m=.06);call(h,'lid.set',opened=True);self.assertEqual(h.lid_lift,0.);step(h,2.1);call(h,'sampler.move',x_m=.165,y_m=0.,z_m=.06);self.assertEqual(h.sampler,[0.,0.,.06]);step(h,.5);self.assertAlmostEqual(h.sampler[0],.025);self.rejected(h,'lid.set','SAMPLER_MOVING',opened=False);step(h,3.);self.rejected(h,'lid.set','SAMPLER_NOT_PARKED',opened=False);call(h,'sampler.move',x_m=.165,y_m=0.,z_m=0.);step(h,1.3);self.rejected(h,'sampler.move','SAMPLER_CLEARANCE',x_m=0.,y_m=0.,z_m=.06);call(h,'sampler.move',x_m=.165,y_m=0.,z_m=.06);step(h,1.3);call(h,'sampler.move',x_m=0.,y_m=0.,z_m=.06);step(h,3.4);call(h,'lid.set',opened=False);step(h,2.1);self.assertEqual(h.lid_lift,0.)
 def test_18_rejected_input_atomic_and_strict_schema(self):
  h=setup(warm=False,method=quick())
  for k,a in [('power.set',{'enabled':1}),('gas.set_flow',{'flow_m3_s':float('nan')}),('sampler.move',{'x_m':.331,'y_m':0.,'z_m':.06}),('lid.set',{'opened':False,'extra':0}),('sim.step',{'dt_s':-.1}),('sim.step',{'dt_s':True})]:self.rejected(h,k,**a)
  self.rejected(h,'method.configure','SAMPLING_GRID',**dict(h.method,sample_interval_s=.15));self.rejected(h,'method.configure','PROGRAM_RATE',**dict(h.method,segments=[dict(target_k=300,rate_k_s=3.,hold_s=0.)]));self.rejected(h,'method.configure','COOLER_TEMPERATURE_RANGE',**dict(h.method,start_temperature_k=298.15,modulation_amplitude_k=1.))
 def test_19_zero_from_observations_and_independent_calibration(self):
  h=rt.TaDsc2500Runtime();call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,sample_flow_offset_w=.001,reference_flow_offset_w=-.002));call(h,'power.set',enabled=True);step(h,5.);self.assertAlmostEqual(h.read()['heatflow.indicated_w'],.003);call(h,'sensor.zero');self.assertAlmostEqual(h.read()['heatflow.indicated_w'],0.);self.assertAlmostEqual(h.calibration['sample_flow_zero_w'],.001);self.assertAlmostEqual(h.calibration['reference_flow_zero_w'],-.002);call(h,'sample.load',**rt.SAMPLE_EXAMPLE);self.rejected(h,'sensor.zero','ZERO_REQUIRES_EMPTY_EQUILIBRIUM')
 def test_20_flow_gain_bias_changes_output_not_truth_energy(self):
  a=setup(method=quick(),warm=False);b=setup(plant={'sample_flow_gain':1.1},method=quick(),warm=False);ra=run(a);rb=run(b);self.assertNotEqual(ra['rows'][-1]['heatflow_w'],rb['rows'][-1]['heatflow_w']);self.assertEqual(a._ledger(),b._ledger());self.assertEqual(a.sample_temperature,b.sample_temperature)
 def test_21_baseline_peak_sign_and_bad_observed_input(self):
  h=setup(method=quick(),warm=False);a=copy.deepcopy(rt.CAPS['analysis.integrate']['example_arguments']);r=call(h,'analysis.integrate',**a);self.assertEqual(r['energy_j'],.001);self.assertAlmostEqual(r['specific_enthalpy_j_kg'],500.);a['heatflow_w']=[0.,-.001,0.];r=call(h,'analysis.integrate',**a);self.assertEqual(r['energy_j'],-.001);self.assertEqual(r['peak_temperature_k'],401.);self.rejected(h,'analysis.integrate','ANALYSIS_DATA',**dict(a,time_s=[0,0,2]));self.rejected(h,'analysis.integrate','ANALYSIS_DATA',**dict(a,baseline_w=[0,0,0,0]))
 def test_22_complex_cp_known_observed_harmonic_and_loss_convention(self):
  h=setup(method=quick(),warm=False);t=np.linspace(0,120,1201);w=2*np.pi/60;m=2e-6;cp=complex(1000,-100);T=300+np.sin(w*t);q=np.real(1j*w*(-1j)*cp*m*np.exp(1j*w*t));a=dict(time_s=t.tolist(),heatflow_w=q.tolist(),temperature_k=T.tolist(),sample_mass_kg=m,period_s=60.,accept_apparatus_response=True);r=call(h,'analysis.modulated_cp',**a);self.assertAlmostEqual(r['apparent_cp_real_j_kg_k'],1000.,places=8);self.assertAlmostEqual(r['apparent_cp_loss_j_kg_k'],100.,places=8);self.assertAlmostEqual(r['temperature_amplitude_k'],1.)
 def test_23_fourier_rejects_missing_phase_short_irregular_window(self):
  h=setup(method=quick(),warm=False);a=copy.deepcopy(rt.CAPS['analysis.modulated_cp']['example_arguments']);self.rejected(h,'analysis.modulated_cp','APPARATUS_RESPONSE_NOT_ACCEPTED',**dict(a,accept_apparatus_response=False));self.rejected(h,'analysis.modulated_cp','FOURIER_WINDOW',**dict(a,period_s=59.));times=a['time_s'].copy();times[1]=1.1;self.rejected(h,'analysis.modulated_cp','FOURIER_WINDOW',**dict(a,time_s=times));self.rejected(h,'analysis.modulated_cp','TEMPERATURE_AMPLITUDE',**dict(a,temperature_k=[300.]*121))
 def test_24_actual_modulation_is_not_target_temperature_copy(self):
  method=dict(start_temperature_k=301.15,segments=[dict(target_k=301.15,rate_k_s=.1,hold_s=120.)],modulation_amplitude_k=.5,modulation_period_s=60.);h=setup(method=method);r=run(h);rows=r['rows'];self.assertTrue(any(abs(x['target_k']-x['sample_k'])>.05 for x in rows));self.assertGreater(np.ptp([x['sample_k'] for x in rows]),.8);a=call(h,'analysis.modulated_cp',time_s=[x['elapsed_s'] for x in rows],heatflow_w=[x['heatflow_w'] for x in rows],temperature_k=[x['sample_k'] for x in rows],sample_mass_kg=2e-6,period_s=60.,accept_apparatus_response=True);self.assertTrue(math.isfinite(a['apparent_cp_real_j_kg_k']));self.ledger(h)
 def test_25_power_off_blanks_and_passive_heat_persists(self):
  h=setup();call(h,'thermal.disable');before=h.furnace;call(h,'power.set',enabled=False);self.assertEqual(h.furnace,before);self.assertIsNone(h.read()['heatflow.indicated_w']);self.assertEqual(h.read()['panel.temperature_text'],'');step(h,1.);self.assertLess(h.furnace,before);self.ledger(h)
 def test_26_emergency_freezes_partial_and_requires_cold_reset(self):
  h=setup();call(h,'acquisition.start');step(h,1.);call(h,'emergency.stop');self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'SOFTWARE_STOP');self.assertFalse(h.gas_open);r=call(h,'acquisition.result');self.rejected(h,'emergency.reset','HOT_CELL');cool(h);call(h,'emergency.reset');self.assertEqual(r,call(h,'acquisition.result'));self.assertEqual(h.phase,'failed');self.ledger(h)
 def test_27_timeout_and_sampling_terminal_at_actual_event(self):
  h=setup(method=quick(timeout_s=.235),warm=False);r=run(h);self.assertEqual(r['error_code'],'TIMEOUT');self.assertAlmostEqual(r['elapsed_s'],.235);self.assertAlmostEqual(r['rows'][-1]['elapsed_s'],.235);self.ledger(h)
 def test_28_frozen_result_survives_material_method_calibration_changes(self):
  h=setup(method=quick(),warm=False);r=run(h);cleanup(h);call(h,'sensor.calibrate',**dict(rt.CALIBRATION_EXAMPLE,sample_flow_gain_correction=2.));call(h,'method.configure',**dict(rt.METHOD_EXAMPLE));call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,reference_id='naphthalene_nist'));self.assertEqual(r,call(h,'acquisition.result'));self.assertEqual(r,h.records[r['operation_id']])
 def test_29_queries_do_not_take_owner(self):
  h=setup(method=quick(),warm=False);s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];step(h,.3);before=state(h);s.poll(op);s.read(instance_id=h.instance_id);h.describe();call(h,'ledger.read');self.assertEqual(before,state(h));r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='query');self.assertIsNone(r['operation_id']);self.assertEqual(h._owners[op],'agent')
 def test_30_session_idempotence_other_owner_and_conflict(self):
  h=setup(method=quick(),warm=False);h.register_principal('other');a=h.session();b=h.session('other');args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=a.invoke(**args);self.assertEqual(r,a.invoke(**args));op=r['operation_id'];self.assertEqual(b.invoke(**dict(args,capability_id='acquisition.result',request_id='query'))['error']['code'],'ACCESS_DENIED')
  for fn in [lambda:b.poll(op),lambda:b.cancel(op,'x')]:
   with self.assertRaises(rt.ActionRejected):fn()
  self.assertEqual(a.invoke(**dict(args,capability_id='power.set',arguments={'enabled':False}))['error']['code'],'REQUEST_CONFLICT');c=a.cancel(op,'cancel');self.assertEqual(c,a.cancel(op,'cancel'))
 def test_31_stale_cancel_does_not_cancel_new_run(self):
  h=setup(method=quick(),warm=False);s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s1');op=s.invoke(**a)['operation_id'];c=s.cancel(op,'cancel');op2=s.invoke(**dict(a,request_id='s2'))['operation_id'];self.assertNotEqual(op,op2);self.assertEqual(c,s.cancel(op,'cancel'));self.assertEqual(h.phase,'running')
  with self.assertRaisesRegex(rt.ActionRejected,'REQUEST_CONFLICT'):s.cancel(op2,'cancel')
 def test_32_agent_truth_recovery_and_json_boundaries(self):
  h=setup(method=quick(),warm=False);s=h.session();d=s.describe(instance_id=h.instance_id);self.assertTrue(all(not k.startswith('truth.') for k in d['state_slots']));self.assertNotIn('sample.remove',d['capabilities']);self.assertNotIn('ledger.read',d['capabilities'])
  for k in ['sample.load','sample.remove','gas.load','ledger.read','sim.step','plant.configure']:
   before=state(h);r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=rt.CAPS[k]['example_arguments'],request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(state(h),before)
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.liquid_fraction'])
  with self.assertRaises(rt.ActionRejected):s.write(instance_id=h.instance_id,state_id='thermal.target_k',value=400.,request_id='write')
  op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s')['operation_id'];step(h,12.);r=s.poll(op);payload=json.dumps(r,allow_nan=False)
  for hidden in ['truth.','liquid_fraction','sample_mass_kg','fusion_enthalpy_j_kg']:self.assertNotIn('"'+hidden,payload)
 def test_33_two_instances_fixed_partition_and_zero_host_step(self):
  a=setup('a',method=quick(),warm=False);b=setup('b',method=quick(),warm=False);call(a,'acquisition.start');call(b,'acquisition.start');step(a,1.3)
  for dt in [.03,.07,.25,.15,.4,.4]:step(b,dt)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);before=state(b);step(a,.1);self.assertEqual(state(b),before);call(a,'sim.step',dt_s=0.)
 def test_34_large_host_step_keeps_event_rows_and_exact_time(self):
  a=setup('a',method=quick(),warm=False);b=setup('b',method=quick(),warm=False);call(a,'acquisition.start');call(b,'acquisition.start');step(a,20.)
  for _ in range(200):step(b,.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);self.assertEqual(a.phase,'succeeded');self.assertEqual(a.elapsed,11.);self.ledger(a)
 def test_35_reference_only_unknown_and_assumptions_rejected(self):
  h=rt.TaDsc2500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);self.rejected(h,'sample.load','MODEL_NOT_ACCEPTED',**dict(rt.SAMPLE_EXAMPLE,accept_engineering=False));self.rejected(h,'sample.load','MODEL_NOT_ACCEPTED',**dict(rt.SAMPLE_EXAMPLE,accept_reversible_phase_change=False));self.rejected(h,'sample.load','CONDITION_TRANSFER_NOT_ACCEPTED',**dict(rt.SAMPLE_EXAMPLE,accept_condition_transfer=False));self.rejected(h,'sample.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id='missing'));rid=next(k for k,v in rt.library()['substances'].items() if not v.get('runtime_usable'));self.rejected(h,'sample.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id=rid));self.rejected(h,'gas.load','REFERENCE_CONDITIONS_NOT_ACCEPTED',**dict(rt.GAS_EXAMPLE,accept_reference_conditions=False))
 def test_36_loading_external_heat_and_removal_keeps_pan_energy(self):
  h=rt.TaDsc2500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,initial_temperature_k=308.15));expected=2e-6*233*10;self.assertAlmostEqual(h.loaded_energy,expected);self.assertGreater(h.sample_temperature,298.15);self.assertLess(h.sample_temperature,308.15);self.ledger(h);call(h,'sample.remove');self.assertGreater(h.sample_energy,0.);self.ledger(h)
 def test_37_start_requires_observed_equilibrium_and_resource(self):
  h=setup(warm=False);self.rejected(h,'acquisition.start','START_NOT_EQUILIBRATED');call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,**quick()));call(h,'gas.set_open',opened=False);self.rejected(h,'acquisition.start','NO_GAS');call(h,'lid.set',opened=True);step(h,2.1);self.rejected(h,'thermal.set_temperature','NOT_READY',temperature_k=400.,rate_k_s=1.)
 def test_38_program_modulation_bounds_unknown_methods_explicit(self):
  h=setup(warm=False,method=quick());self.rejected(h,'method.configure','MODULATION_SAMPLING',**dict(h.method,modulation_amplitude_k=.1,start_temperature_k=300.,modulation_period_s=1.,sample_interval_s=.1));self.rejected(h,'method.configure','PROGRAM_RATE',**dict(h.method,modulation_amplitude_k=1.,start_temperature_k=300.,modulation_period_s=2.));call(h,'method.configure',**dict(h.method,segments=[dict(target_k=298.15,rate_k_s=.1,hold_s=0.)]));self.rejected(h,'acquisition.start','EMPTY_PROGRAM')
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
 def test_39_run_repeated_phase_history_without_free_reset(self):
  h=setup();r=run(h);self.assertEqual(h._fraction(),1.);call(h,'method.configure',**dict(h.method,start_temperature_k=h.read()['sample.indicated_k'],segments=[dict(target_k=h.read()['sample.indicated_k'],rate_k_s=.1,hold_s=.2)],start_tolerance_k=1.));r2=run(h);self.assertEqual(h._fraction(),1.);self.assertNotEqual(r['operation_id'],r2['operation_id']);self.assertEqual(r,h.records[r['operation_id']]);self.ledger(h)
 def test_40_sampler_registered_descent_and_interrupted_path_are_atomic(self):
  h=setup(warm=False,method=quick());call(h,'lid.set',opened=True);step(h,2.1);call(h,'sampler.move',x_m=.1,y_m=.05,z_m=.06);self.rejected(h,'sampler.move','SAMPLER_MOVING',x_m=0.,y_m=0.,z_m=.06);step(h,2.1);self.rejected(h,'sampler.move','SAMPLER_TARGET_NOT_REGISTERED',x_m=.1,y_m=.05,z_m=0.);self.assertEqual(len(rt.SAMPLER_REGISTERED_XY),56)
  for x,y in rt.SAMPLER_REGISTERED_XY:self.assertTrue(0<=x<=.330 and 0<=y<=.110)
 def test_41_tiny_gas_at_maximum_molar_rate_consumed_without_loop(self):
  h=setup(gas={'amount_mol':1e-18,'reference_pressure_pa':1e7,'reference_temperature_k':200.},method=quick(),warm=False);call(h,'gas.set_flow',flow_m3_s=1e-5);r=run(h);self.assertEqual(r['error_code'],'GAS_EXHAUSTED');self.assertEqual(h.gas_remaining,0.);self.assertGreater(r['elapsed_s'],0.);self.ledger(h)
 def test_99_every_actual_api_state_and_metadata_schema(self):
  spec=importlib.util.spec_from_file_location('asset_tool_dsc',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_ta_dsc2500_contract_metadata();checked=0
  for k,c in meta['capabilities'].items():
   for key in ['input_schema','output_schema']:self.assertEqual(tool.lint_schema(c[key],path=k+'.'+key),[])
   self.assertEqual(tool.validate_value(c['example_arguments'],c['input_schema'],path=k),[])
  counts={k:0 for k in rt.CAPS if k not in rt.UNSUPPORTED}
  for k,a,r in OBSERVED:
   self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema'],path=k+'.arguments'),[]);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema'],path=k+'.return'),[]);counts[k]+=1;checked+=1
  self.assertTrue(all(counts.values()),counts)
  for state_values in STATES:
   self.assertEqual(set(state_values),set(meta['state_slots']))
   for k,v in state_values.items():self.assertEqual(tool.validate_value(v,meta['state_slots'][k]['value_schema'],path=k),[]);checked+=1
  SCHEMA_REPORT.update(actual_capability_calls=len(OBSERVED),state_snapshots=len(STATES),schema_checks=checked,capability_counts=counts)
if __name__=='__main__':unittest.main()
