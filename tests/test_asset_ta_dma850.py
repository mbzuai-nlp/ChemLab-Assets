"""CHEM133 measured-observation DMA and finite-resource/protocol regression tests."""
import copy,hashlib,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import ta_dma850 as rt

def _need(p):
    """Raise FileNotFoundError for a missing build record or source cache (the public release skips such tests)."""
    if not Path(p).exists():
        raise FileNotFoundError(2, "not present in this tree", str(p))

OBSERVED=[];STATES=[];SCHEMA_REPORT={}
def state(h):return json.dumps(h.__dict__,sort_keys=True,allow_nan=False)
def call(h,k,**a):
 before=h.read();r=h.invoke(k,**a);after=h.read();json.dumps(r,allow_nan=False);json.dumps(after,allow_nan=False);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));STATES.append(after);changed={x for x in before if before[x]!=after[x]}
 if not changed<=set(rt.CAPS[k]['effects']):raise AssertionError((k,'undeclared state changes',changed-set(rt.CAPS[k]['effects'])))
 return r
def step(h,t):h.step(t);STATES.append(h.read());json.dumps(h.read(),allow_nan=False)
def wait(h,predicate,limit=1000):
 for _ in range(limit):
  if predicate():return
  step(h,.1)
 raise AssertionError('finite operation failed to finish')
def setup(sample=None,plant=None,method=None,preload=.05,instance='test_dma'):
 h=rt.TaDma850Runtime(instance);call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,**(plant or {})));call(h,'power.set',enabled=True)
 for g in ['air','N2']:call(h,'gas.load',**dict(rt.GAS_EXAMPLE,gas=g,amount_mol=100.))
 if h.plant['cooler']=='NPC':call(h,'coolant.load',**rt.COOLANT_EXAMPLE)
 call(h,'gas.set_open',opened=True);s=copy.deepcopy(sample or rt.SAMPLE_EXAMPLE);call(h,'drive.move',position_m=s['length_m']-.01);wait(h,lambda:not h.read()['drive.moving']);call(h,'sensor.calibrate',**rt.CALIBRATION_EXAMPLE);call(h,'sensor.zero');call(h,'sample.load',**s);call(h,'clamp.set',locked=True);wait(h,lambda:not h.read()['clamp.moving']);call(h,'furnace.set',opened=False);wait(h,lambda:not h.read()['furnace.moving']);call(h,'drive.preload',force_n=preload,ramp_s=2.);wait(h,lambda:not h.read()['drive.moving']);step(h,2.);call(h,'method.configure',**copy.deepcopy(method or rt.METHOD_EXAMPLE));return h
def run(h):
 call(h,'acquisition.start');wait(h,lambda:h.phase!='running',int(h.method['timeout_s']/.1)+2);return call(h,'acquisition.result')
def cleanup(h):
 if h.phase=='running':call(h,'acquisition.stop')
 call(h,'thermal.disable');wait(h,lambda:h._cold(),100000);call(h,'drive.unload',ramp_s=2.);wait(h,lambda:h.drive_mode=='hold' and abs(h._force())<1e-4 and abs(h._strain())<=1e-6,100000);call(h,'furnace.set',opened=True);wait(h,lambda:not h.read()['furnace.moving']);call(h,'clamp.set',locked=False);wait(h,lambda:not h.read()['clamp.moving']);call(h,'sample.remove');call(h,'gas.set_open',opened=False)
 for g in ['air','N2']:call(h,'gas.unload',gas=g)
 if h.coolant_present:call(h,'coolant.unload')
 call(h,'power.set',enabled=False)
def engineering(**kwargs):s=copy.deepcopy(rt.ENGINEERING_SAMPLE_EXAMPLE);s.update(kwargs);return s
def method(f=1.,control='strain',**kw):
 m=copy.deepcopy(rt.METHOD_EXAMPLE);m['blocks'][0].update(frequency_hz=f,control=control,**kw);return m
class TestDMA850(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=state(h)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,state(h))
 def ledger(self,h):
  l=call(h,'ledger.read')
  for k in ['sample_error_kg','coolant_error_kg','mechanical_error_j','coolant_cold_error_j','air_error_mol','n2_error_mol']:self.assertLess(abs(l[k]),1e-8,(k,l[k]))
  self.assertLess(abs(l['thermal_error_j']),2e-6);return l
 def test_01_source_raw_and_conservative_domain(self):
  rid=rt.SAMPLE_EXAMPLE['response']['reference_id'];r=rt.library()['substances'][rid];self.assertTrue(r['runtime_usable']);self.assertEqual(len(r['values']['temperature_k']),1634);self.assertLess(min(r['values']['temperature_k']),173.15);self.assertGreater(max(r['values']['temperature_k']),393.15);old=copy.deepcopy(r);E,D=rt.source_pair(r,298.15,1);self.assertGreater(E,0);self.assertGreater(D,0);self.assertEqual(r,old)
  for T,f in [(172.9,1),(400.,1),(298.15,2)]:
   with self.assertRaises(rt.ActionRejected):rt.source_pair(r,T,f)
 def test_02_source_anchor_actual_observed_moduli(self):
  h=setup();r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(r['rows']),1025);a=r['blocks'][0]['analysis'];E,D=rt.source_pair(h.reference,298.15,1);self.assertAlmostEqual(a['storage_modulus_pa']/E,1,delta=.003);self.assertAlmostEqual(a['loss_modulus_pa']/D,1,delta=.003);self.assertAlmostEqual(a['cycles'],3.,places=8);self.ledger(h)
 def test_03_force_setting_honored_and_tracking_export(self):
  h=setup(method=method(control='force',force_amplitude_n=.01));r=run(h);a=r['blocks'][0];self.assertAlmostEqual(a['analysis']['force_amplitude_n'],.01,delta=1e-5);self.assertEqual(a['force_target_n'],.01);self.assertAlmostEqual(a['force_tracking_error_n'],a['analysis']['force_amplitude_n']-.01);self.ledger(h)
 def test_04_high_frequency_internal_sampling_not_host_alias(self):
  h=setup(sample=engineering(),method=method(200,settle_cycles=20,measure_cycles=10),preload=.2);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(r['rows']),3841);a=r['blocks'][0]['analysis'];z=rt.maxwell_modulus(200,h.E,h.branches);self.assertAlmostEqual(a['storage_modulus_pa']/z.real,1,delta=.006);self.assertAlmostEqual(a['loss_modulus_pa']/z.imag,1,delta=.15);self.assertLess(max(np.diff([x['time_s'] for x in r['rows']])),.00004);self.ledger(h)
 def test_05_low_frequency_full_integer_cycles(self):
  h=setup(sample=engineering(),method=method(.001,settle_cycles=1,measure_cycles=2),preload=.2);call(h,'acquisition.start');step(h,3000.1);r=call(h,'acquisition.result');self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['elapsed_s'],3000.,places=6);self.assertAlmostEqual(r['blocks'][0]['analysis']['cycles'],2.,places=7);self.ledger(h)
 def test_06_exact_branch_relaxation_and_work(self):
  b=[dict(modulus_pa=2e8,relaxation_s=.2)];r=rt.maxwell_step(.001,.001,[1e5],1e8,b,.3,1e-8);self.assertAlmostEqual(r['stress_pa'][0],1e5*math.exp(-1.5));self.assertGreater(r['dissipated_j'],0);self.assertAlmostEqual(r['work_j'],0,places=15)
 def test_07_stiff_tau_stable_and_passive(self):
  for tau in [1e-8,1e-4,.1,1e8]:
   r=rt.maxwell_step(0,.001,[0.],1e8,[dict(modulus_pa=2e8,relaxation_s=tau)],.01,1e-8);self.assertGreaterEqual(r['dissipated_j'],0);self.assertTrue(math.isfinite(r['work_j']));self.assertAlmostEqual(r['work_j'],r['stored_j']+r['dissipated_j'],places=14)
 def test_08_mechanical_dissipation_heat_and_recovery(self):
  h=setup(sample=engineering(),preload=.2);run(h);l=self.ledger(h);self.assertGreater(l['dissipated_j'],0);self.assertGreater(l['mechanical_input_j'],l['elastic_stored_j']);cleanup(h);l=self.ledger(h);self.assertGreater(l['mechanical_recovered_j'],0);self.assertEqual(l['sample_loaded_kg'],l['sample_recovered_kg'])
 def test_09_cancel_retains_stress_and_freezes_history(self):
  h=setup(sample=engineering(),preload=.2);call(h,'acquisition.start');step(h,.3);q=h.stress.copy();u=h._elastic();x=h.drive;call(h,'acquisition.stop');r=call(h,'acquisition.result');self.assertEqual(h.stress,q);self.assertEqual(h.drive,x);self.assertEqual(h._elastic(),u);step(h,2);self.assertEqual(r,call(h,'acquisition.result'));self.assertNotEqual(h.stress,q);self.assertGreater(h._elastic(),0);self.ledger(h)
 def test_10_unilateral_release_no_compressive_clipping(self):
  h=setup(sample=engineering(),preload=.2);call(h,'drive.unload',ramp_s=.01);forces=[]
  for _ in range(100):step(h,.1);forces.append(h._force())
  self.assertGreaterEqual(min(forces),-1e-10);self.assertLess(abs(h._strain()),1e-6);self.assertGreater(h.mechanical_recovered,0);self.ledger(h)
 def test_11_preload_required_and_tension_limit_atomic(self):
  h=setup();call(h,'drive.unload',ramp_s=2);wait(h,lambda:h.drive_mode=='hold');self.rejected(h,'acquisition.start','NO_PRELOAD');self.rejected(h,'drive.preload','LINEAR_FORCE_DOMAIN',force_n=18.,ramp_s=2.);call(h,'drive.preload',force_n=.001,ramp_s=1.);wait(h,lambda:h.drive_mode=='hold');call(h,'method.configure',**method(control='force',force_amplitude_n=.01));self.rejected(h,'acquisition.start','TENSION_FORCE_DOMAIN')
 def test_12_geometry_length_matches_actual_jaw_gap(self):
  s=engineering(length_m=.015);h=setup(sample=s,preload=.2);self.assertAlmostEqual(h._base(),.005);self.assertGreater(h.read()['drive.displacement_m'],.005);cleanup(h);self.assertAlmostEqual(h.drive,.005,delta=1e-8);self.ledger(h)
 def test_13_fiber_area_different_from_film(self):
  h=setup(sample=engineering(form='fiber',diameter_m=.0008),preload=.05);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(h._area(),math.pi*.0008**2/4);self.ledger(h)
 def test_14_query_no_clock_revision_or_owner_mutation(self):
  h=setup();before=state(h);h.read();h.describe();call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['response']['reference_id']);call(h,'acquisition.result');call(h,'ledger.read');self.assertEqual(state(h),before)
 def test_15_temperature_target_not_instant_truth(self):
  h=setup(sample=engineering(),preload=.2);call(h,'thermal.set_temperature',temperature_k=303.15,rate_k_s=10/60);self.assertAlmostEqual(h.furnace,298.15,places=5);step(h,3);self.assertAlmostEqual(h.target,298.65,places=7);self.assertLess(h.furnace,h.target);self.assertLess(h.sample_temperature,h.furnace);call(h,'gas.set_open',opened=False);self.assertFalse(h.thermal_enabled);self.ledger(h)
 def test_16_no_cooler_below_ambient_and_rate_guards(self):
  h=setup();self.rejected(h,'thermal.set_temperature','COOLER_TEMPERATURE_RANGE',temperature_k=273.15,rate_k_s=.1);self.rejected(h,'method.configure','INVALID_ARGUMENT',**dict(rt.METHOD_EXAMPLE,heating_rate_k_s=.5));self.rejected(h,'method.configure','INVALID_ARGUMENT',**dict(rt.METHOD_EXAMPLE,cooling_rate_k_s=.2))
 def test_17_npc_finite_cooling_enthalpy_and_n2(self):
  h=setup(sample=engineering(),plant={'cooler':'NPC'},preload=.2);call(h,'thermal.set_temperature',temperature_k=297.15,rate_k_s=10/60);step(h,20);self.assertLess(h.sample_temperature,298.15);self.assertGreater(h.cooling_removed,0);self.assertGreater(h.coolant_used,0);self.assertGreater(h.gas_used['N2'],.2);self.ledger(h);call(h,'thermal.disable');call(h,'coolant.unload');self.ledger(h)
 def test_18_tiny_coolant_exhaustion_terminal(self):
  h=setup(sample=engineering(),plant={'cooler':'NPC'},preload=.2);call(h,'coolant.unload');call(h,'coolant.load',amount_kg=1e-12,accept_engineering_latent=True);m=method();m['blocks'][0]['temperature_k']=297.15;call(h,'method.configure',**m);call(h,'acquisition.start');step(h,.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'COOLANT_EXHAUSTED');self.ledger(h)
 def test_19_air_exhaustion_finite_endpoint(self):
  h=setup();call(h,'gas.set_open',opened=False);call(h,'gas.unload',gas='air');call(h,'gas.load',**dict(rt.GAS_EXAMPLE,amount_mol=1e-9));call(h,'gas.set_open',opened=True);call(h,'acquisition.start');step(h,.1);self.assertEqual(h.error,'GAS_EXHAUSTED');self.assertAlmostEqual(h.elapsed,1e-9/(2e-3/60*101325/(rt.R_GAS*298.15)),places=12);self.assertEqual(h.rows[-1]['elapsed_s'],h.elapsed);self.ledger(h)
 def test_20_extreme_tiny_n2_no_infinite_loop(self):
  h=setup();call(h,'gas.set_open',opened=False);call(h,'gas.unload',gas='N2');call(h,'gas.load',**dict(rt.GAS_EXAMPLE,gas='N2',amount_mol=1e-18));call(h,'gas.set_open',opened=True);call(h,'acquisition.start');step(h,1);self.assertEqual(h.error,'GAS_EXHAUSTED');self.assertLess(h.elapsed,1e-10);self.ledger(h)
 def test_21_sensor_gain_bias_changes_measured_modulus(self):
  h=setup(plant={'force_gain':.8,'displacement_gain':1.1});r=run(h);E,D=rt.source_pair(h.reference,298.15,1);self.assertAlmostEqual(r['blocks'][0]['analysis']['storage_modulus_pa']/E,.8/1.1,delta=.003);self.assertNotEqual(h.read()['force.indicated_n'],h._force());call(h,'sensor.calibrate',**dict(rt.CALIBRATION_EXAMPLE,force_gain_correction=1/.8,displacement_gain_correction=1/1.1));r=run(h);self.assertAlmostEqual(r['blocks'][0]['analysis']['storage_modulus_pa']/E,1,delta=.003)
 def test_22_force_overrange_returns_unavailable_not_clipped(self):
  h=setup(plant={'force_gain':100.});call(h,'drive.preload',force_n=.1,ramp_s=2);step(h,3);call(h,'sensor.calibrate',**dict(rt.CALIBRATION_EXAMPLE,force_gain_correction=10));self.assertIsNone(h.read()['force.indicated_n']);r=run(h);self.assertIsNone(r['blocks'][0]['analysis']);self.assertIsNotNone(r['blocks'][0]['analysis_error'])
 def test_23_external_harmonic_analysis_exact_fixture(self):
  h=rt.TaDma850Runtime();a=rt.CAPS['analysis.modulus']['example_arguments'];r=call(h,'analysis.modulus',**a);self.assertAlmostEqual(r['storage_modulus_pa'],1.25e8,delta=1e-5);self.assertAlmostEqual(r['loss_modulus_pa'],2.5e7,delta=1e-5);self.assertAlmostEqual(r['tan_delta'],.2,places=12)
 def test_24_bad_harmonic_windows_and_missing_samples(self):
  h=rt.TaDma850Runtime();a=copy.deepcopy(rt.CAPS['analysis.modulus']['example_arguments']);a['time_s'][-1]+=.001;self.rejected(h,'analysis.modulus','HARMONIC_WINDOW',**a);a=copy.deepcopy(rt.CAPS['analysis.modulus']['example_arguments']);a['force_n']=a['force_n'][:-1];self.rejected(h,'analysis.modulus','ANALYSIS_DATA',**a)
 def test_25_negative_observed_loss_preserved(self):
  h=rt.TaDma850Runtime();a=copy.deepcopy(rt.CAPS['analysis.modulus']['example_arguments']);t=np.array(a['time_s']);a['force_n']=(.2+.01*np.sin(2*np.pi*t)-.002*np.cos(2*np.pi*t)).tolist();r=call(h,'analysis.modulus',**a);self.assertLess(r['loss_modulus_pa'],0);self.assertFalse(r['passive_compatible'])
 def test_26_transition_bracketed_only_and_fixed_frequency(self):
  h=rt.TaDma850Runtime();a=rt.CAPS['analysis.transition']['example_arguments'];r=call(h,'analysis.transition',**a);self.assertEqual(r['transition_temperature_k'],300.);r=call(h,'analysis.transition',**dict(a,tan_delta=[.3,.2,.1]));self.assertIsNone(r['transition_temperature_k']);self.rejected(h,'analysis.transition','TRANSITION_DATA',**dict(a,frequency_hz=[1.,2.,1.]))
 def test_27_source_anchor_no_frequency_or_temperature_extrapolation(self):
  h=setup();self.rejected(h,'method.configure','SOURCE_ANCHOR_ONLY',**method(2));m=method();m['blocks'][0]['temperature_k']=303.15;self.rejected(h,'method.configure','SOURCE_ANCHOR_ONLY',**m)
 def test_28_engineering_spectrum_requires_explicit_acceptance(self):
  h=rt.TaDma850Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);s=engineering();s['response']['accept_engineering_spectrum']=False;self.rejected(h,'sample.load','MODEL_NOT_ACCEPTED',**s);s=copy.deepcopy(rt.SAMPLE_EXAMPLE);s['response']['accept_condition_transfer']=False;self.rejected(h,'sample.load','CONDITION_TRANSFER',**s)
 def test_29_atomic_unknown_nan_extra_and_boolean_numeric(self):
  h=setup()
  for k,a in [('drive.move',{'position_m':True}),('thermal.set_temperature',{'temperature_k':float('nan'),'rate_k_s':.1}),('power.set',{'enabled':True,'extra':0}),('unknown',{})]:self.rejected(h,k,'.',**a)
 def test_30_two_instances_fixed_partition_clock(self):
  a=setup(instance='a');b=setup(instance='b');call(a,'acquisition.start');call(b,'acquisition.start');step(a,.07);step(a,.23);step(b,.3);self.assertEqual(a.read(),b.read());self.assertEqual(a.stress,b.stress);step(a,.1);self.assertNotEqual(a.time_s,b.time_s)
 def test_31_agent_truth_and_evaluator_parameters_denied(self):
  h=setup();s=h.session();d=s.describe(instance_id=h.instance_id);self.assertNotIn('plant.configure',d['capabilities']);self.assertNotIn('truth.force_n',d['state_slots']);self.assertNotIn('truth.force_n',s.read(instance_id=h.instance_id)['observations']);before=state(h);r=s.invoke(instance_id=h.instance_id,capability_id='sample.remove',arguments={},request_id='deny');self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,state(h))
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):s.read(instance_id=h.instance_id,state_ids=['truth.force_n'])
 def test_32_owner_poll_result_stop_and_query_cannot_steal(self):
  h=setup();h.register_principal('other');a=h.session();b=h.session('other');r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=r['operation_id'];step(h,.2);b.invoke(instance_id=h.instance_id,capability_id='reference.query',arguments={'reference_id':rt.SAMPLE_EXAMPLE['response']['reference_id']},request_id='query')
  for k in ['acquisition.result','acquisition.stop']:self.assertEqual(b.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k)['error']['code'],'ACCESS_DENIED')
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):b.poll(op)
  self.assertEqual(a.poll(op)['status'],'running');self.assertEqual(a.cancel(op,'cancel')['status'],'succeeded')
 def test_33_idempotent_requests_cancel_and_stale_history(self):
  h=setup();s=h.session();args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');a=s.invoke(**args);self.assertEqual(a,s.invoke(**args));op=a['operation_id'];step(h,.2);c=s.cancel(op,'c');self.assertEqual(c,s.cancel(op,'c'));frozen=s.poll(op);s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s2');step(h,.1);self.assertEqual(s.poll(op),frozen)
  with self.assertRaisesRegex(rt.ActionRejected,'NOT_RUNNING'):s.cancel(op,'late')
  self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='s')['error']['code'],'REQUEST_CONFLICT')
 def test_34_four_actual_joint_motion_not_instant(self):
  h=rt.TaDma850Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'power.set',enabled=True)
  for g in ['air','N2']:call(h,'gas.load',**dict(rt.GAS_EXAMPLE,gas=g))
  call(h,'gas.set_open',opened=True);call(h,'drive.move',position_m=.01);self.assertEqual(h.drive,0);step(h,.1);self.assertAlmostEqual(h.drive,.0002);self.rejected(h,'furnace.set','MECHANISM_MOVING',opened=False);step(h,5);call(h,'clamp.set',locked=True);step(h,.1);self.assertAlmostEqual(h.clamp,.0019);step(h,2);call(h,'furnace.set',opened=False);step(h,.1);self.assertAlmostEqual(h.furnace_lift,.145)
 def test_35_emergency_preserves_memory_and_no_auto_restart(self):
  h=setup();call(h,'acquisition.start');step(h,.3);q=h.drive;call(h,'emergency.stop');self.assertEqual(h.error,'SOFTWARE_STOP');self.rejected(h,'furnace.set','EMERGENCY_LATCHED',opened=True);self.rejected(h,'drive.preload','NOT_READY',force_n=.05,ramp_s=1.);step(h,1);self.assertEqual(q,h.drive);call(h,'emergency.reset');step(h,1);self.assertEqual(q,h.drive);self.assertFalse(h.gas_open);self.ledger(h)
 def test_36_hot_access_rejected_atomically(self):
  h=setup(sample=engineering(),plant={'safe_access_temperature_k':298.2},preload=.2);call(h,'thermal.set_temperature',temperature_k=303.15,rate_k_s=20/60);step(h,3);call(h,'thermal.disable');self.rejected(h,'furnace.set','HOT_CELL',opened=True);self.rejected(h,'clamp.set','HOT_CELL',locked=False);self.rejected(h,'emergency.reset','HOT_CELL')
 def test_37_force_and_dynamic_displacement_limits(self):
  h=setup(sample=engineering(),preload=.2);m=method(strain_amplitude=1e-8);call(h,'method.configure',**m);self.rejected(h,'acquisition.start','LINEAR_AMPLITUDE_DOMAIN');m=method(strain_amplitude=.01);call(h,'method.configure',**m);self.rejected(h,'acquisition.start','TENSION_FORCE_DOMAIN');self.rejected(h,'drive.preload','INVALID_ARGUMENT',force_n=18.1,ramp_s=1)
 def test_38_stiff_thermal_model_positive_and_closed(self):
  h=setup(sample=engineering(mass_kg=1e-12),plant={'furnace_capacity_j_k':1e-5,'fixture_capacity_j_k':1e-7,'sample_conductance_w_k':1000.,'controller_gain_w_k':1e5},preload=.2);call(h,'thermal.set_temperature',temperature_k=303.15,rate_k_s=20/60);step(h,20);self.assertGreater(h.sample_temperature,300.);self.assertLess(h.sample_temperature,303.16);self.ledger(h)
 def test_39_finite_timeout_and_multiple_blocks(self):
  m=method();m['timeout_s']=.25;h=setup(method=m);r=run(h);self.assertEqual(r['error_code'],'TIMEOUT');self.assertAlmostEqual(r['elapsed_s'],.25);self.assertEqual(r['rows'][-1]['elapsed_s'],.25)
  m=method();m['blocks'].append(dict(m['blocks'][0],control='force'));h=setup(method=m);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(r['blocks']),2);self.assertTrue(all(abs(a['analysis']['cycles']-3)<1e-8 for a in r['blocks']))
 def test_40_panel_power_blank_and_no_derived_truth_leak(self):
  h=setup();self.assertEqual(h.read()['panel.temperature_text'],'25.0');self.assertTrue(h.read()['panel.displacement_text']);cleanup(h);self.assertEqual(h.read()['panel.temperature_text'],'');self.assertEqual(h.read()['panel.displacement_text'],'');self.assertIsNone(h.read()['force.indicated_n']);self.ledger(h)
 def test_41_all_unsupported_explicit_and_empty_initial_step(self):
  h=rt.TaDma850Runtime();step(h,.1);self.assertEqual(h.time_s,.1)
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
  self.rejected(h,'plant.configure','INITIALIZATION_ONLY',**rt.PLANT_EXAMPLE)
 def test_42_actual_api_and_slot_schemas_json(self):
  h=setup();call(h,'sim.step',dt_s=0.);run(h);cleanup(h);meta=rt.export_ta_dma850_contract_metadata();missing=set(rt.CAPS)-set(rt.UNSUPPORTED)-{k for k,a,r in OBSERVED};self.assertEqual(missing,set())
  for k,a,r in OBSERVED:rt.validate(rt.CAPS[k]['input_schema'],a);rt.validate(rt.CAPS[k]['output_schema'],r)
  for values in STATES:
   self.assertEqual(set(values),set(rt.SLOTS));json.dumps(values,allow_nan=False)
   for k,v in values.items():rt.validate(rt.SLOTS[k]['schema'],v)
  self.assertTrue(all(not c['state_effects'] for k,c in meta['capabilities'].items() if c['kind'] in ['query','analysis']));SCHEMA_REPORT.update(actual_returns=len(OBSERVED),states=len(STATES))
# ---- 2026-10-15 real-data library: every runtime-usable record must drive an actual observed run ----
def source_rows(ref,f):
 v=ref['values'];return sorted((T,E,D) for T,F,E,D in zip(v['temperature_k'],v['frequency_hz'],v['storage_modulus_pa'],v['loss_modulus_pa']) if math.isclose(F,f,rel_tol=1e-7,abs_tol=1e-9))
def pick_point(ref):
 """Most populated source frequency; an actual source row inside the runtime domain closest to 298.15 K with E'>0, E''>=0."""
 freqs=rt.source_frequencies(ref);f=max(freqs,key=lambda q:len(source_rows(ref,q)));d=ref['runtime_domain']
 rows=[r for r in source_rows(ref,f) if d['minimum_temperature_k']<=r[0]<=d['maximum_temperature_k'] and r[1]>0 and r[2]>=0 and 113.15<=r[0]<=873.15]
 T=min(rows,key=lambda r:(abs(r[0]-298.15),r[0]))[0];return T,f
def adaptive_sample(rid,T,f,mode='source_anchor',strain_bias=2e-3):
 """Explicit engineering geometry chosen so preload/dynamic force respect 1e-4..18 N and the 2 % linear strain domain."""
 ref=rt.library()['substances'][rid];E,D=rt.source_pair(ref,T,f);W=.004;P=.05;A=P/(E*strain_bias);t=A/W
 if t>.002:W,t=.008,.002;A=W*t;P=A*E*strain_bias
 if t<1e-6:t=1e-6;A=W*t;P=A*E*strain_bias
 s=copy.deepcopy(rt.SAMPLE_EXAMPLE);s.update(width_m=W,thickness_m=t,mass_kg=A*.01*1200.);s['response']=dict(s['response'],mode=mode,reference_id=rid,temperature_k=T,frequency_hz=f)
 return s,P,E,D
def single_block(T,f,control='strain',**kw):
 m=copy.deepcopy(rt.METHOD_EXAMPLE);m['blocks'][0].update(temperature_k=T,frequency_hz=f,control=control,strain_amplitude=5e-4,settle_cycles=3,measure_cycles=2,**kw);m['heating_rate_k_s']=20/60;m['cooling_rate_k_s']=10/60;m['timeout_s']=20000.;return m
def plant_for(T):return dict(cooler='NPC',ambient_k=283.15,controller_gain_w_k=200.) if T<283.15 else dict(ambient_k=max(283.15,min(298.15,T)),controller_gain_w_k=200.)
RUNTIME_SUMMARY={}
class TestDMA850RealLibrary(unittest.TestCase):
 rejected=TestDMA850.rejected;ledger=TestDMA850.ledger
 def test_43_every_runtime_record_drives_observed_run(self):
  ids=rt.runtime_reference_ids();lib=rt.library()['substances'];self.assertGreaterEqual(len(ids),1);done={}
  for rid in ids:
   ref=lib[rid];T,f=pick_point(ref);s,P,E,D=adaptive_sample(rid,T,f);h=setup(sample=s,plant=plant_for(T),method=single_block(T,f),preload=P);r=run(h)
   self.assertEqual(r['phase'],'succeeded',(rid,T,f,r['error_code']));a=r['blocks'][0]['analysis'];mag=math.hypot(E,D)
   self.assertAlmostEqual(a['storage_modulus_pa']/E,1,delta=.006,msg=rid);self.assertLess(abs(a['loss_modulus_pa']-D),.006*mag,rid);self.assertEqual(r['reference_id'],rid);self.ledger(h)
   done[rid]=dict(identity=ref['material_identity'],T=T,f=f,mode=ref['conditions'].get('mode'))
  RUNTIME_SUMMARY.update(done);identities={v['identity'] for v in done.values()}
  self.assertEqual(len(identities),len({lib[k]['material_identity'] for k in ids}))
 def test_44_curve_temperature_sweep_reproduces_source_and_transition(self):
  rid='polycarbonate_ta_dma850_single_cantilever';ref=rt.library()['substances'][rid];temps=[421.15,425.15,429.15,433.15,437.15,441.15];f=ref['values']['frequency_hz'][0]
  s=copy.deepcopy(rt.SOURCE_CURVE_SAMPLE_EXAMPLE);s['response']=dict(s['response'],reference_id=rid,temperature_k=temps[0],frequency_hz=1.)
  m=copy.deepcopy(rt.METHOD_EXAMPLE);m['blocks']=[dict(m['blocks'][0],temperature_k=T,frequency_hz=1.,control='force',force_amplitude_n=.01) for T in temps];m['heating_rate_k_s']=20/60
  m['timeout_s']=20000.;h=setup(sample=s,plant=dict(controller_gain_w_k=200.),method=m,preload=.03);r=run(h);self.assertEqual(r['phase'],'succeeded',r['error_code']);src=[]
  for b,T in zip(r['blocks'],temps):
   E,D=rt.source_pair(ref,T,1.);src.append(D/E);a=b['analysis'];mag=math.hypot(E,D);self.assertAlmostEqual(a['storage_modulus_pa']/E,1,delta=.01,msg=T);self.assertLess(abs(a['loss_modulus_pa']-D),.01*mag,T)
  self.assertGreater(max(src)/min(src),5.)
  tr=call(h,'analysis.transition',temperature_k=[b['mean_temperature_k'] for b in r['blocks']],tan_delta=[b['analysis']['tan_delta'] for b in r['blocks']],frequency_hz=[b['frequency_hz'] for b in r['blocks']])
  i=int(np.argmax(src));self.assertTrue(tr['bracketed']);self.assertAlmostEqual(tr['transition_temperature_k'],r['blocks'][i]['mean_temperature_k'],places=9)
  l=self.ledger(h);self.assertNotEqual(l['constitutive_update_j'],0.)
 def test_45_curve_frequency_sweep_at_source_temperature(self):
  rid='foam_pe_table4';ref=rt.library()['substances'][rid];self.assertEqual(ref['conditions']['mode'],'compression');self.assertEqual(ref['runtime_transfer']['source_mode'],'compression')
  s,P,E,D=adaptive_sample(rid,295.15,1.,mode='source_curve');m=single_block(295.15,1.);m['blocks']=[dict(m['blocks'][0],frequency_hz=f) for f in [.1,1.,10.]]
  h=setup(sample=s,plant=dict(ambient_k=295.15),method=m,preload=P);r=run(h);self.assertEqual(r['phase'],'succeeded',r['error_code'])
  for b in r['blocks']:
   E,D=rt.source_pair(ref,295.15,b['frequency_hz']);a=b['analysis'];self.assertAlmostEqual(a['storage_modulus_pa']/E,1,delta=.006);self.assertLess(abs(a['loss_modulus_pa']-D),.006*math.hypot(E,D))
  self.ledger(h)
 def test_46_curve_guards_atomic_and_reference_only_rejected(self):
  rid='polycarbonate_ta_dma850_single_cantilever';s=copy.deepcopy(rt.SOURCE_CURVE_SAMPLE_EXAMPLE);s['response']=dict(s['response'],reference_id=rid,temperature_k=310.15,frequency_hz=1.)
  m=single_block(310.15,1.);h=setup(sample=s,method=m,preload=.05);bad=copy.deepcopy(m);bad['blocks'][0]['temperature_k']=470.15;self.rejected(h,'method.configure','SOURCE_TEMPERATURE_RANGE',**bad)
  bad=copy.deepcopy(m);bad['blocks'][0]['frequency_hz']=2.;self.rejected(h,'method.configure','SOURCE_FREQUENCY_RANGE',**bad)
  lib=rt.library()['substances'];h=rt.TaDma850Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE)
  for ref_only in [k for k,v in lib.items() if not v.get('runtime_usable')][:8]+['dif72_protein_dma850_reference','darus_pla_dry_mastercurve','spu_ha_dma_tg']:
   if ref_only in lib and not lib[ref_only].get('runtime_usable'):
    s2=copy.deepcopy(s);s2['response']['reference_id']=ref_only;self.rejected(h,'sample.load','.',**s2)
  s2=copy.deepcopy(s);s2['response']['accept_condition_transfer']=False;self.rejected(h,'sample.load','CONDITION_TRANSFER',**s2)
 def test_47_runtime_records_traceable_licensed_and_not_scalar(self):
  lib=rt.library();subs=lib['substances'];ids=rt.runtime_reference_ids()
  for rid in ids:
   r=subs[rid];src=r['source'];self.assertTrue(src.get('url') or src.get('doi'),rid);self.assertTrue(src.get('locator'),rid);self.assertNotIn('scalar',r['kind'],rid)
   f=ROOT/src['file'];_need(f);self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(),src['sha256'],rid)
   self.assertIn(r['conditions'].get('mode'),lib['runtime_transfer_policy']['transferable_modes'],rid);self.assertNotIn('shear',r['conditions'].get('mode'),rid);self.assertNotIn('torsion',r['conditions'].get('mode'),rid);self.assertEqual(r['runtime_transfer']['source_mode'],r['conditions'].get('mode'),rid)
   if r.get('evidence',{}).get('loss_modulus_derived_from_tan_delta') if isinstance(r.get('evidence'),dict) else False:self.assertTrue(any('tan' in t for t in r['evidence']['transformations']),rid)
  cov=lib['coverage'];self.assertEqual(cov['runtime_records'],len(ids));self.assertEqual(cov['runtime_materials'],len({subs[k]['material_identity'] for k in ids}))
if __name__=='__main__':unittest.main(verbosity=2)
