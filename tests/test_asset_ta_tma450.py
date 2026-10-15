"""CHEM134 physical/observation/protocol and strict actual-schema regressions."""
import copy,hashlib,json,math,pickle,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import ta_tma450 as rt
OBSERVED=[];STATES=[];SCHEMA_REPORT={}
def state(h):return hashlib.sha256(pickle.dumps(h.__dict__)).hexdigest()
def call(h,k,**a):
 before=h.read();r=h.invoke(k,**a);after=h.read();json.dumps(r,allow_nan=False);json.dumps(after,allow_nan=False);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));STATES.append(after);changed={x for x in before if before[x]!=after[x]}
 if not changed<=set(rt.CAPS[k]['effects']):raise AssertionError((k,'undeclared effects',changed-set(rt.CAPS[k]['effects'])))
 return r
def step(h,t):h.step(t);STATES.append(h.read());json.dumps(h.read(),allow_nan=False)
def wait(h,predicate,limit=20000):
 for _ in range(limit):
  if predicate():return
  step(h,.1)
 raise AssertionError(('TIMEOUT',h.read()))
def setup(sample=None,plant=None,method=None,instance='test_tma'):
 h=rt.TaTma450Runtime(instance);call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,**(plant or {})));call(h,'power.set',enabled=True);call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,**(sample or {})));call(h,'gas.load',**rt.GAS_EXAMPLE);call(h,'gas.set_flow',flow_m3_s=100e-6/60);call(h,'gas.set_open',opened=True)
 if h.plant['cooler']=='LN2':call(h,'coolant.load',**rt.COOLANT_EXAMPLE)
 call(h,'load.set',motor_force_n=.05,rate_n_s=.1);call(h,'probe.contact',engaged=True);wait(h,lambda:h.probe_mode=='contact');step(h,.5);call(h,'displacement.zero');call(h,'furnace.set',opened=False);wait(h,lambda:not h.read()['furnace.moving']);call(h,'sensor.calibrate',**rt.CALIBRATION_EXAMPLE);call(h,'method.configure',**copy.deepcopy(method or rt.METHOD_EXAMPLE));return h
def run(h):call(h,'acquisition.start');wait(h,lambda:h.phase!='running');return call(h,'acquisition.result')
def cleanup(h):
 if h.phase=='running':call(h,'acquisition.stop')
 call(h,'thermal.disable');call(h,'thermal.set_cooling',enabled=True);wait(h,lambda:h._cold());call(h,'thermal.set_cooling',enabled=False);call(h,'furnace.set',opened=True);wait(h,lambda:not h.read()['furnace.moving'])
 if h.probe_mode=='contact':call(h,'probe.contact',engaged=False);wait(h,lambda:h.probe_mode=='hold')
 call(h,'sample.remove');call(h,'gas.set_open',opened=False)
 for port in list(h.gases):call(h,'gas.unload',port=port)
 if h.coolant_present:call(h,'coolant.unload')
 call(h,'power.set',enabled=False)
def copper(**kw):return dict(rt.SAMPLE_EXAMPLE,reference_id='copper_nist_srm736',length_m=.01,diameter_m=.005,**kw)
LIB=rt.library()['substances'];EVIDENCE={'measured','measured_literature','derived','assumption'}
def source_arrays(r):
 v=r.get('runtime_values') or r['values'];return np.asarray(v['temperature_k'],float),np.asarray(v['expansion_ratio'],float)
def expected_length(r,L,T,Tref):
 x,y=source_arrays(r);e=float(np.interp(T,x,y));e0=float(np.interp(Tref,x,y))
 return L*(1+e)/(1+e0) if r['conditions'].get('reference_temperature_k') is not None else L*(1+e-e0)
def record_plan(key,heat=True):
 """Ambient/loading temperature inside the record domain and the plant range; heat 10 K or cool 10 K with LN2."""
 r=LIB[key];d=r.get('runtime_domain',{});x,_=source_arrays(r);lo=max(x[0],d.get('minimum_temperature_k',-math.inf));hi=min(x[-1],d.get('maximum_temperature_k',math.inf))
 amb=min(max(303.15,lo),hi,313.15);assert 283.15<=amb,(key,lo,hi)
 heat=heat and hi-amb>=10;target=amb+10 if heat else max(amb-10,lo)
 plant=dict(ambient_k=amb,safe_access_temperature_k=max(amb,313.15),**({} if heat else {'cooler':'LN2'}))
 method=copy.deepcopy(rt.METHOD_EXAMPLE);method['start_temperature_k']=amb;method['segments'][0].update(target_temperature_k=target,temperature_rate_k_s=1.,hold_s=5.)
 return dict(sample=dict(rt.SAMPLE_EXAMPLE,reference_id=key,reference_temperature_k=amb,length_m=.005),plant=plant,method=method,ambient=amb,target=target)
class TestTMA450(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=state(h)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,state(h))
 def ledger(self,h):
  l=call(h,'ledger.read')
  for k,v in l.items():
   if '_error_' in k:self.assertLess(abs(v),2e-7,(k,v))
  return l
 def test_01_original_branch_retained_not_sorted(self):
  r=rt.library()['substances'][rt.SAMPLE_EXAMPLE['reference_id']];self.assertEqual(len(r['values']['temperature_k']),20408);self.assertEqual(len(r['runtime_values']['temperature_k']),336);m=rt.prepare_reference(r,True);self.assertTrue(np.all(np.diff(m['T'])>0));self.assertLess(m['minimum_k'],303.15);self.assertGreater(m['maximum_k'],470)
  with self.assertRaisesRegex(rt.ActionRejected,'PROCESSING'):rt.prepare_reference(r,False)
 def test_02_measured_temperature_displacement_actual_cte(self):
  h=setup();r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['elapsed_s'],13.,places=7);self.assertEqual(len(r['rows']),131);a=dict(temperature_k=[x['temperature_k'] for x in r['rows']],displacement_m=[x['displacement_m'] for x in r['rows']],blank_m=[0.]*len(r['rows']),initial_length_m=rt.SAMPLE_EXAMPLE['length_m'],start_index=20,end_index=100);q=call(h,'analysis.cte',**a);self.assertGreater(q['linear_cte_k_inv'],5e-5);self.assertLess(q['linear_cte_k_inv'],3e-4);self.ledger(h)
 def test_03_copper_physical_normalization_and_negative_low_cte(self):
  h=setup(sample=copper());r=run(h);self.assertEqual(r['phase'],'succeeded');T=h.sample_temperature;ref=h.model;eps=float(np.interp(T,ref['T'],ref['y']));eps0=float(np.interp(303.15,ref['T'],ref['y']));self.assertAlmostEqual(h.natural,.01*(1+eps)/(1+eps0),places=15);self.assertNotAlmostEqual(h.natural,.01*(1+eps-eps0),places=14);self.ledger(h)
 def test_04_program_force_ramp_finite_true_vs_target(self):
  h=setup(sample={'elastic_stiffness_n_m':1e5,'retarded_stiffness_n_m':2e5,'relaxation_s':.5});F=h.force;call(h,'load.set',motor_force_n=.5,rate_n_s=.1);self.assertEqual(h.force,F);step(h,1);self.assertAlmostEqual(h.motor_program,.15,places=8);self.assertLess(h.force,h.motor_program);self.assertGreater(h.retarded,0);self.assertLess(h._height(),h.natural);self.ledger(h)
 def test_05_kelvin_analytical_hold_recovery_and_passivity(self):
  for tau in [1e-8,.001,1.,1e8]:
   r=rt.kelvin_step(.1,.1,0.,1e5,2e5,tau,.1);self.assertAlmostEqual(r['retarded_m'],.1/2e5*(-math.expm1(-.1/tau)),places=14);self.assertGreaterEqual(r['dissipated_j'],0);self.assertAlmostEqual(r['compression_work_j'],r['stored_j']-r['previous_stored_j']+r['dissipated_j'],places=16)
 def test_06_force_work_recovery_and_no_material_loss(self):
  h=setup(sample={'elastic_stiffness_n_m':1e5,'retarded_stiffness_n_m':2e5,'relaxation_s':.1});call(h,'load.set',motor_force_n=.5,rate_n_s=1);step(h,2);stored=h._elastic();cleanup(h);l=self.ledger(h);self.assertGreater(l['mechanical_recovered_j'],stored*.5);self.assertEqual(l['sample_recovered_kg'],rt.SAMPLE_EXAMPLE['mass_kg']);self.assertEqual(l['sample_present_kg'],0)
 def test_07_cancel_keeps_load_and_freezes_history(self):
  h=setup();call(h,'acquisition.start');step(h,2);F=h.force;T=h.sample_temperature;x=h.retarded;call(h,'acquisition.stop');r=call(h,'acquisition.result');self.assertEqual(h.force,F);self.assertEqual(h.sample_temperature,T);self.assertEqual(h.retarded,x);step(h,2);self.assertEqual(r,call(h,'acquisition.result'));self.assertNotEqual(h.sample_temperature,T);self.ledger(h)
 def test_08_cleanup_after_hot_run_exports_sensible(self):
  h=setup();run(h);self.rejected(h,'furnace.set','HOT_CELL',opened=True);cleanup(h);l=self.ledger(h);self.assertGreater(l['recovered_sensible_j'],0);self.assertEqual(h.read()['panel.temperature_text'],'');self.assertEqual(h.read()['panel.displacement_text'],'')
 def test_09_geometry_probe_no_load_teleport(self):
  h=rt.TaTma450Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'power.set',enabled=True);call(h,'sample.load',**rt.SAMPLE_EXAMPLE);q=h.probe;call(h,'probe.contact',engaged=True);self.assertEqual(h.probe,q);step(h,.1);self.assertAlmostEqual(h.probe,q-.0002);self.assertEqual(h.force,0);wait(h,lambda:h.probe_mode=='contact');step(h,.5);self.assertAlmostEqual(h.probe,h._height(),places=14)
 def test_10_probe_guard_and_manual_deadweight(self):
  h=rt.TaTma450Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'power.set',enabled=True);call(h,'load.deadweight',force_n=1.);call(h,'probe.select',kind='penetration');call(h,'probe.select',kind='expansion');call(h,'sample.load',**rt.SAMPLE_EXAMPLE);self.rejected(h,'probe.move','PROBE_COLLISION',position_m=.001);call(h,'probe.move',position_m=.029);wait(h,lambda:not h.read()['probe.moving']);call(h,'probe.contact',engaged=True);wait(h,lambda:h.probe_mode=='contact');step(h,.5);self.assertAlmostEqual(h.force,1.05,places=7);self.rejected(h,'load.deadweight','LIFT',force_n=.5);self.rejected(h,'load.set','INVALID_ARGUMENT',motor_force_n=1.01,rate_n_s=.1);self.ledger(h)
 def test_11_pure_reads_analysis_do_not_take_time_or_owner(self):
  h=setup();before=state(h);h.read();h.describe();call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);call(h,'analysis.cte',**rt.CAPS['analysis.cte']['example_arguments']);call(h,'acquisition.result');call(h,'ledger.read');self.assertEqual(before,state(h))
 def test_12_observation_gain_and_calibration(self):
  h=setup(plant={'force_gain':.8,'displacement_gain':1.2});r=run(h);self.assertAlmostEqual(r['rows'][-1]['force_n'],.04,places=6);D=r['rows'][-1]['displacement_m'];call(h,'sensor.calibrate',**dict(rt.CALIBRATION_EXAMPLE,force_gain_correction=1/.8,displacement_gain_correction=1/1.2));self.assertAlmostEqual(h.read()['force.indicated_n'],.05,places=6);self.assertAlmostEqual(h.read()['displacement.indicated_m'],D/1.2,delta=1e-9)
 def test_13_observation_overload_is_unavailable(self):
  h=setup(plant={'force_gain':100.});self.assertIsNone(h.read()['force.indicated_n']);r=run(h);self.assertTrue(all(x['force_n'] is None for x in r['rows']));self.assertLess(h.force,.1)
 def test_14_furnace_setpoint_not_temperature(self):
  h=setup();call(h,'thermal.set_temperature',temperature_k=323.15,rate_k_s=2.5);self.assertAlmostEqual(h.furnace,303.15,places=6);step(h,1);self.assertAlmostEqual(h.target,305.65,places=7);self.assertLess(h.furnace,h.target);self.assertLess(h.sample_temperature,h.furnace);self.ledger(h)
 def test_15_no_cooler_domain_rate_bounds_atomic(self):
  h=setup();self.rejected(h,'thermal.set_temperature','COOLER_TEMPERATURE_RANGE',temperature_k=293.15,rate_k_s=1.);self.rejected(h,'thermal.set_temperature','INVALID_ARGUMENT',temperature_k=323.15,rate_k_s=2.500001);self.rejected(h,'thermal.set_temperature','INVALID_ARGUMENT',temperature_k=323.15,rate_k_s=.0001);self.rejected(h,'thermal.set_temperature','REFERENCE_TEMPERATURE_RANGE',temperature_k=600.,rate_k_s=1.)
 def test_16_ln2_finite_cooling_and_ledger(self):
  h=setup(sample=copper(),plant={'cooler':'LN2'});call(h,'thermal.set_temperature',temperature_k=293.15,rate_k_s=1.);step(h,20);self.assertLess(h.sample_temperature,298.15);self.assertGreater(h.coolant_used,0);self.assertGreater(h.cooling_removed,0);self.ledger(h);cleanup(h);self.ledger(h)
 def test_17_tiny_ln2_terminal_not_infinite(self):
  h=setup(sample=copper(),plant={'cooler':'LN2'});call(h,'coolant.unload');call(h,'coolant.load',amount_kg=1e-12,accept_engineering_latent=True);m=copy.deepcopy(rt.METHOD_EXAMPLE);m['segments'][0]['target_temperature_k']=293.15;call(h,'method.configure',**m);call(h,'acquisition.start');step(h,.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'COOLANT_EXHAUSTED');self.assertEqual(h.coolant_remaining,0);self.ledger(h)
 def test_18_finite_gas_last_tick_snapshot(self):
  h=setup();call(h,'gas.set_open',opened=False);call(h,'gas.unload',port=1);call(h,'gas.load',**dict(rt.GAS_EXAMPLE,amount_mol=1e-9));call(h,'gas.set_open',opened=True);call(h,'acquisition.start');step(h,.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'GAS_EXHAUSTED');self.assertAlmostEqual(h.elapsed,1e-9/(100e-6/60*101325/(rt.R_GAS*298.15)),places=12);self.assertEqual(h.rows[-1]['elapsed_s'],h.elapsed);self.ledger(h)
 def test_19_subfloating_gas_finite_termination(self):
  h=setup();call(h,'gas.set_open',opened=False);call(h,'gas.unload',port=1);call(h,'gas.load',**dict(rt.GAS_EXAMPLE,amount_mol=1e-18));call(h,'gas.set_open',opened=True);call(h,'acquisition.start');step(h,1);self.assertEqual(h.error,'GAS_EXHAUSTED');self.assertLess(h.elapsed,1e-10);self.ledger(h)
 def test_20_dual_gas_selection_and_inventory(self):
  h=setup();call(h,'gas.load',**dict(rt.GAS_EXAMPLE,port=2,gas='He')) if not h.gas_open else None
  self.rejected(h,'gas.select','GAS_VALVE_OPEN',port=2);call(h,'gas.set_open',opened=False);call(h,'gas.load',**dict(rt.GAS_EXAMPLE,port=2,gas='He'));call(h,'gas.select',port=2);call(h,'gas.set_open',opened=True);n=h.gas_remaining[1];step(h,1);self.assertEqual(h.gas_remaining[1],n);self.assertGreater(h.gas_used[2],0);cleanup(h);self.ledger(h)
 def test_21_flow_minimum_and_reference_state(self):
  h=setup();call(h,'gas.set_flow',flow_m3_s=50e-6/60);self.rejected(h,'acquisition.start','PURGE_NOT_READY');call(h,'gas.set_flow',flow_m3_s=100e-6/60);call(h,'thermal.set_temperature',temperature_k=313.15,rate_k_s=.1);self.rejected(h,'gas.set_flow','PURGE_FLOW_TOO_LOW',flow_m3_s=0.);call(h,'gas.set_open',opened=False);self.assertFalse(h.thermal_enabled)
 def test_22_sample_assumptions_and_unknown_reference_atomic(self):
  h=rt.TaTma450Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE)
  for kw,err in [({'accept_condition_transfer':False},'CONDITION_TRANSFER'),({'accept_engineering_compliance':False},'MODEL_NOT_ACCEPTED'),({'accept_reference_processing':False},'REFERENCE_PROCESSING'),({'reference_id':'missing'},'INVALID_REFERENCE')]:self.rejected(h,'sample.load',err,**dict(rt.SAMPLE_EXAMPLE,**kw))
 def test_23_source_temperature_and_geometry_atomic(self):
  h=rt.TaTma450Runtime();call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,ambient_k=298.15));self.rejected(h,'sample.load','REFERENCE_TEMPERATURE_RANGE',**rt.SAMPLE_EXAMPLE);self.rejected(h,'sample.load','INVALID_ARGUMENT',**dict(rt.SAMPLE_EXAMPLE,length_m=.027));self.rejected(h,'sample.load','INVALID_ARGUMENT',**dict(rt.SAMPLE_EXAMPLE,diameter_m=.011))
 def test_24_cte_negative_blank_and_external_length(self):
  h=rt.TaTma450Runtime();a=copy.deepcopy(rt.CAPS['analysis.cte']['example_arguments']);a['displacement_m']=[0.,-1e-6,-2e-6];a['blank_m']=[0.,1e-7,2e-7];r=call(h,'analysis.cte',**a);self.assertAlmostEqual(r['linear_cte_k_inv'],-11e-6,places=13);a['initial_length_m']=.02;r=call(h,'analysis.cte',**a);self.assertAlmostEqual(r['linear_cte_k_inv'],-5.5e-6,places=13)
 def test_25_softening_bracket_and_boundary_null(self):
  h=rt.TaTma450Runtime();a=copy.deepcopy(rt.CAPS['analysis.softening']['example_arguments']);r=call(h,'analysis.softening',**a);self.assertAlmostEqual(r['softening_temperature_k'],330.,places=8);a['displacement_m']=[0.,1e-6,2e-6,3e-6,4e-6,5e-6,6e-6];r=call(h,'analysis.softening',**a);self.assertFalse(r['bracketed']);self.assertIsNone(r['softening_temperature_k'])
 def test_26_analysis_rejects_missing_reversed_or_bad_windows(self):
  h=rt.TaTma450Runtime();a=copy.deepcopy(rt.CAPS['analysis.cte']['example_arguments']);self.rejected(h,'analysis.cte','ANALYSIS_DATA',**dict(a,blank_m=[0.,0.,0.,0.]));self.rejected(h,'analysis.cte','ANALYSIS_TEMPERATURE_WINDOW',**dict(a,temperature_k=[300.,320.,310.]));self.rejected(h,'analysis.cte','INVALID_ARGUMENT',**dict(a,initial_length_m=0.));b=copy.deepcopy(rt.CAPS['analysis.softening']['example_arguments']);self.rejected(h,'analysis.softening','ANALYSIS_WINDOWS',**dict(b,first_window=[0,4]))
 def test_27_session_auth_principals_and_no_hidden_state(self):
  h=setup();s=h.session();d=s.describe(instance_id=h.instance_id);self.assertNotIn('sample.load',d['capabilities']);self.assertNotIn('truth.force_n',d['state_slots']);self.assertNotIn('sample.height_m',d['state_slots']);self.assertNotIn('probe.position_m',d['state_slots']);r=s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='deny');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.force_n'])
  with self.assertRaises(rt.ActionRejected):h.session('fake')
  with self.assertRaises(rt.ActionRejected):s.write(instance_id=h.instance_id,state_id='thermal.target_k',value=350,request_id='w')
 def test_28_owner_result_poll_cancel(self):
  h=setup();h.register_principal('other');a=h.session();b=h.session('other');r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');op=r['operation_id'];self.assertEqual(r['status'],'accepted')
  for cap in ['acquisition.result','acquisition.stop']:self.assertEqual(b.invoke(instance_id=h.instance_id,capability_id=cap,arguments={},request_id=cap)['error']['code'],'ACCESS_DENIED')
  for fn in [lambda:b.poll(op),lambda:b.cancel(op,'x')]:
   with self.assertRaises(rt.ActionRejected):fn()
  self.assertEqual(a.cancel(op,'c')['status'],'succeeded');self.assertEqual(a.poll(op)['status'],'cancelled')
 def test_29_request_dedup_conflict_and_cancel_idempotence(self):
  h=setup();s=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');a=s.invoke(**kw);self.assertEqual(a,s.invoke(**kw));self.assertEqual(h._counter,1);self.assertEqual(s.invoke(**dict(kw,capability_id='acquisition.result'))['error']['code'],'REQUEST_CONFLICT');c=s.cancel(a['operation_id'],'c');self.assertEqual(c,s.cancel(a['operation_id'],'c'))
 def test_30_frozen_previous_operation_and_source(self):
  h=setup();s=h.session();a=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='1');step(h,.2);s.cancel(a['operation_id'],'cancel');old=s.poll(a['operation_id']);h.reference['name']='mutated';h.operation_source='mutated';step(h,.1);m=copy.deepcopy(rt.METHOD_EXAMPLE);m['start_temperature_k']=h.sample_temperature;call(h,'method.configure',**m);b=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='2');self.assertEqual(b['status'],'accepted');self.assertEqual(old,s.poll(a['operation_id']));s.cancel(b['operation_id'],'cancel2')
 def test_31_fixed_clock_partition_and_two_instance(self):
  a=setup(instance='a');b=setup(instance='b');call(a,'acquisition.start');call(b,'acquisition.start');step(a,.3)
  for _ in range(6):step(b,.05)
  self.assertEqual(a.read(),b.read());before=b.read();step(a,.1);self.assertEqual(before,b.read());self.assertNotEqual(a.time_s,b.time_s)
 def test_32_sim_step_actual_api_and_zero_noop(self):
  h=setup();t=h.time_s;r=call(h,'sim.step',dt_s=.1);self.assertAlmostEqual(r['time_s'],t+.1);before=state(h);call(h,'sim.step',dt_s=0.);self.assertEqual(before,state(h));self.rejected(h,'sim.step','INVALID_ARGUMENT',dt_s=-1.)
 def test_33_strict_inputs_bool_nan_extra_unknown(self):
  h=setup()
  for a in [dict(motor_force_n=True,rate_n_s=.1),dict(motor_force_n=float('nan'),rate_n_s=.1),dict(motor_force_n=.1,rate_n_s=.1,extra=1)]:self.rejected(h,'load.set','INVALID_ARGUMENT',**a)
  self.rejected(h,'made.up','UNKNOWN_CAPABILITY');self.rejected(h,'plant.configure','INITIALIZATION_ONLY',**rt.PLANT_EXAMPLE)
 def test_34_em_modes_and_unimplemented_not_false_success(self):
  h=setup();self.assertIn('isostrain.native',rt.UNSUPPORTED)
  for cap in rt.UNSUPPORTED:self.rejected(h,cap,'UNSUPPORTED')
 def test_35_software_stop_and_explicit_reset(self):
  h=setup();call(h,'acquisition.start');step(h,.2);F=h.force;call(h,'emergency.stop');self.assertEqual(h.error,'SOFTWARE_STOP');self.assertEqual(h.force,F);self.assertFalse(h.gas_open);self.rejected(h,'power.set','EMERGENCY_LATCHED',enabled=True);call(h,'emergency.reset');self.assertFalse(h.emergency);self.assertEqual(h.phase,'failed');self.ledger(h)
 def test_36_method_atomic_clock_and_timeout(self):
  h=setup();self.rejected(h,'method.configure','SAMPLE_CLOCK_ALIGNMENT',**dict(rt.METHOD_EXAMPLE,sample_interval_s=.15));self.rejected(h,'method.configure','CONDITION_TRANSFER',**dict(rt.METHOD_EXAMPLE,accept_condition_transfer=False));m=copy.deepcopy(rt.METHOD_EXAMPLE);m['timeout_s']=.25;call(h,'method.configure',**m);r=run(h);self.assertEqual(r['error_code'],'TIMEOUT');self.assertAlmostEqual(r['elapsed_s'],.25,places=9)
 def test_37_multiple_segments_and_zero_hold(self):
  h=setup();m=copy.deepcopy(rt.METHOD_EXAMPLE);m['segments']=[dict(m['segments'][0],target_temperature_k=305.65,hold_s=0.),dict(m['segments'][0],target_temperature_k=308.15,hold_s=.3)];call(h,'method.configure',**m);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['elapsed_s'],2.3,places=8);self.assertEqual(r['rows'][-1]['segment_index'],1);self.ledger(h)
 def test_38_stiff_heat_capacity_implicit_stable(self):
  p=dict(rt.PLANT_EXAMPLE,furnace_capacity_j_k=1e-5,fixture_capacity_j_k=1e-7,sample_conductance_w_k=1000.,controller_gain_w_k=1e5);r=rt.coupled_thermal_step(p,303.15,0.,1e-7,.1,310.,True,False,0.,0.,0.,0.,lambda T:0.);self.assertTrue(303.15<=r['sample_k']<=310.);error=r['heater_j']-r['ambient_j']-1e-5*(r['furnace_k']-303.15)-r['sample_energy_j'];self.assertLess(abs(error),1e-9)
 def test_39_force_domain_failure_keeps_energy_and_can_recover(self):
  h=setup(sample={'elastic_stiffness_n_m':500.,'retarded_stiffness_n_m':1e10,'maximum_compression_fraction':.1});call(h,'load.set',motor_force_n=1.,rate_n_s=1.);step(h,1);self.assertEqual(h.model_fault,'SAMPLE_COMPRESSION_DOMAIN');self.assertGreater(h._height(),0);self.ledger(h);cleanup(h);self.ledger(h)
 def test_40_schema_actual_states_returns_and_json(self):
  h=setup();run(h);meta=h.describe();self.assertEqual(set(h.read()),set(meta['state_slots']))
  for key,value in h.read().items():rt.validate(meta['state_slots'][key]['value_schema'],value)
  json.dumps(h.read(),allow_nan=False);json.dumps(call(h,'acquisition.result'),allow_nan=False);self.assertEqual(meta['catalog_id'],'CHEM-134');self.assertEqual(meta['asset_type_id'],'instrument.ta_tma450')
 def test_41_preconfiguration_and_cold_manual_interlocks(self):
  h=rt.TaTma450Runtime();h.step(0);call(h,'sim.step',dt_s=.1);self.assertIsNone(h.plant);self.rejected(h,'power.set','NOT_CONFIGURED',enabled=True);call(h,'plant.configure',**rt.PLANT_EXAMPLE);self.rejected(h,'probe.move','POWER_OFF',position_m=.02);call(h,'power.set',enabled=True);call(h,'furnace.set',opened=False);self.rejected(h,'sample.load','MECHANISM_MOVING',**rt.SAMPLE_EXAMPLE)
 def test_42_force_and_cte_analysis_not_ground_truth_answers(self):
  h=setup(plant={'frame_drift_m_k':1e-6});r=run(h);a=dict(temperature_k=[x['temperature_k'] for x in r['rows']],displacement_m=[x['displacement_m'] for x in r['rows']],blank_m=[0.]*len(r['rows']),initial_length_m=rt.SAMPLE_EXAMPLE['length_m'],start_index=20,end_index=100);q=call(h,'analysis.cte',**a);self.assertGreater(q['linear_cte_k_inv'],5e-4);a['blank_m']=[1e-6*(x['furnace_k']-r['rows'][0]['furnace_k']) for x in r['rows']];q=call(h,'analysis.cte',**a);self.assertLess(q['linear_cte_k_inv'],3e-4)
 def test_43_unconfigured_host_clock_then_explicit_configuration(self):
  a=rt.TaTma450Runtime('idle_a');b=rt.TaTma450Runtime('idle_b');initial=a.read();step(a,.35)
  for _ in range(7):step(b,.05)
  self.assertEqual(a.read(),b.read());self.assertAlmostEqual(a.time_s,.3);self.assertIsNone(a.plant);self.assertFalse(a.power);self.assertFalse(a.gases);self.assertEqual(a.gas_used,{1:0.,2:0.});self.assertEqual({k:v for k,v in a.read().items() if k!='clock.time_s'},{k:v for k,v in initial.items() if k!='clock.time_s'});self.rejected(a,'power.set','NOT_CONFIGURED',enabled=True)
  call(a,'plant.configure',**rt.PLANT_EXAMPLE);self.assertAlmostEqual(a.time_s,.3);step(a,.05);self.assertAlmostEqual(a.time_s,.4);self.assertAlmostEqual(a.sample_temperature,303.15);self.assertIsNone(b.plant);self.assertAlmostEqual(b.time_s,.3);self.ledger(a)
 def test_44_library_runtime_coverage_and_provenance(self):
  usable={k:r for k,r in LIB.items() if r['runtime_usable']};ids={r['material_identity'] for r in usable.values()}
  self.assertGreaterEqual(len(ids),50);self.assertGreaterEqual(len({r['material_identity'] for r in LIB.values()}),len(ids))
  for k,r in LIB.items():
   self.assertIn(r['evidence'].get('category'),EVIDENCE,k);self.assertTrue(r['source'].get('url') and r['source'].get('locator') and r['source'].get('accessed'),k);self.assertIn('cas',r,k)
   if r['runtime_usable']:
    m=rt.prepare_reference(r,True);self.assertLess(m['minimum_k'],m['maximum_k']);self.assertGreaterEqual(m['maximum_k'],283.15,k);self.assertTrue(m['processing']['response_basis'],k)
   else:self.assertTrue(r.get('runtime_exclusion_reason'),k);self.assertRaisesRegex(rt.ActionRejected,'INVALID_REFERENCE',rt.prepare_reference,r,True)
  bases={r['runtime_processing']['response_basis'] for r in usable.values()}
  self.assertTrue({'manufacturer_measured_curve','certified_reference_table','published_fit_curve','published_fit_table','trapezoid_integrated_published_cte','xrd_lattice_axis_oriented_single_crystal','xrd_derived_polycrystal_average','reported_mean_cte_secant','author_linear_lattice_fit'}<=bases)
  self.assertEqual(LIB['nbs1950_cu']['cas'],'7440-50-8');self.assertEqual(LIB['nist_boride_m01_a']['cas'],'12045-63-5')
 def test_45_every_usable_record_loads_with_source_normalization(self):
  for k,r in LIB.items():
   if not r['runtime_usable']:continue
   plan=record_plan(k);h=rt.TaTma450Runtime('load_'+k);call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,**plan['plant']));call(h,'power.set',enabled=True)
   if r.get('runtime_values') is not None:self.rejected(h,'sample.load','REFERENCE_PROCESSING',**dict(plan['sample'],accept_reference_processing=False))
   call(h,'sample.load',**plan['sample']);self.assertAlmostEqual(h.natural,expected_length(r,.005,plan['ambient'],plan['ambient']),places=15)
   self.assertAlmostEqual(h._length(plan['target']),expected_length(r,.005,plan['target'],plan['ambient']),places=15,msg=k)
   q=json.loads(call(h,'reference.query',reference_id=k)['reference_json']);self.assertEqual(q['source'],r['source']);self.assertEqual(q['values'],r.get('runtime_values',r['values']))
 def test_46_class_sample_full_acquisitions_follow_real_reference(self):
  for k in ['nbs1950_al','pba_mn_coiii','nist_boride_m01_a','nist_boride_m06_poly','nist_cryo_teflon','nist_cryo_invar36','nist_cryo_beryllium_polycrystalline','nist_cryo_sapphire','silicon_nist_cryogenic_cte','nist2013_stycast_2850ft','nist2013_phosphor_bronze_cte','tungsten_nist_srm737','fused_silica_nist_srm739','borosilicate_glass_nist_srm731','stainless_steel_aisi446_nist_srm738']:
   r=LIB[k];plan=record_plan(k,heat=k not in ('nist_cryo_teflon','pba_mn_coiii','silicon_nist_cryogenic_cte'));h=setup(sample=plan['sample'],plant=plan['plant'],method=plan['method'],instance='class_'+k);res=run(h)
   self.assertEqual(res['phase'],'succeeded',k);self.assertEqual(res['reference_id'],k);self.assertEqual(json.loads(res['template_source']),r['source'])
   proc=json.loads(res['processing_json']);self.assertEqual(proc['response_basis'],r['runtime_processing']['response_basis']);self.assertEqual(proc['reference_evidence'],r['evidence']);self.assertIn('response basis',res['evidence'])
   self.assertAlmostEqual(h.natural,expected_length(r,.005,h.sample_temperature,plan['ambient']),places=15,msg=k)
   expected=expected_length(r,.005,h.sample_temperature,plan['ambient'])-.005;observed=res['rows'][-1]['displacement_m']-res['rows'][0]['displacement_m']
   if abs(expected)>5e-9:self.assertEqual(np.sign(observed),np.sign(expected),k)
   cleanup(h);self.ledger(h)
 def test_47_reference_only_records_rejected_atomically(self):
  h=rt.TaTma450Runtime();call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,ambient_k=293.15));call(h,'power.set',enabled=True)
  for k in ['nbs1950_se','nist_cryo_pvc','nist_cryo_nickel_steels']:
   self.assertFalse(LIB[k]['runtime_usable']);self.rejected(h,'sample.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id=k,reference_temperature_k=293.15))
  self.assertIn('Footnote c',LIB['nbs1950_se']['runtime_exclusion_reason']);self.assertIn('below the minimum plant ambient',LIB['nist_cryo_pvc']['runtime_exclusion_reason'])
 def test_48_mean_cte_secant_and_cryogenic_ratio_forms_exact(self):
  r=LIB['nbs1950_al'];a=r['values']['linear_cte_k_inv'];plan=record_plan('nbs1950_al');h=rt.TaTma450Runtime();call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,**plan['plant']));call(h,'power.set',enabled=True);call(h,'sample.load',**plan['sample'])
  self.assertAlmostEqual(h._length(343.15),.005*(1+a*(343.15-plan['ambient'])),places=16);self.rejected(h,'method.configure','REFERENCE_TEMPERATURE_RANGE',**dict(plan['method'],segments=[dict(plan['method']['segments'][0],target_temperature_k=380.)]))
  r=LIB['nist_cryo_teflon'];x,y=source_arrays(r);i=list(x).index(293.)
  coeffs=[float(r['raw_values']['pieces'][0]['published_cells'][q].replace(' ','')) for q in 'abcde'];self.assertAlmostEqual(y[i],1e-5*sum(c*293.**n for n,c in enumerate(coeffs)),places=15)
if __name__=='__main__':unittest.main()
