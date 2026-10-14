"""CHEM-145 physical/event/protocol tests; no native scene or hardware claims."""
import copy,hashlib,io,json,math,subprocess,sys,unittest
from pathlib import Path
import numpy as np
from scipy.integrate import quad
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.asset_runtime.zwick_hit5p import *
PROCESS_EVIDENCE={};CHECKS=[]
def call(h,k,**a):
 before=h.read('debug');r=h.invoke(k,**a);validate(CAPS[k]['output_schema'],r);after=h.read('debug');delta={x for x in after if before[x]!=after[x]}
 if not delta<=set(CAPS[k]['effects']):raise AssertionError(f'{k}: undeclared {delta-set(CAPS[k]["effects"])}')
 PROCESS_EVIDENCE.setdefault(k,[]).append(dict(time_s=h.time_s,phase=h.phase,result_keys=list(r),actual_call=True));return r

def configured(plant=None,method=None):
 h=ZwickHit5pRuntime()
 for k,a in [('plant.configure',dict(PLANT_EXAMPLE,**(plant or {}))),('fixture.configure',FIXTURE_EXAMPLE),('method.configure',dict(METHOD_EXAMPLE,**(method or {}))),('power.set',dict(enabled=True)),('shield.set_open',dict(opened=False))]:call(h,k,**a)
 h.step(.6);call(h,'pendulum.raise');h.step(3.);return h

def calibrated(plant=None,method=None):
 h=configured(plant,method);call(h,'calibration.start');call(h,'pendulum.catch');h.step(2);return h

def loaded(plant=None,sample=None,method=None):
 h=calibrated(plant,method);call(h,'pendulum.raise');h.step(5);call(h,'shield.set_open',opened=True);h.step(.6);call(h,'sample.load',**dict(SAMPLE_EXAMPLE,**(sample or {})));call(h,'shield.set_open',opened=False);h.step(.6);return h

def impact(h,catch=True):
 call(h,'acquisition.start')
 if catch:call(h,'pendulum.catch')
 h.step(2);return call(h,'acquisition.result')

class HITTest(unittest.TestCase):
 def check(self,condition,key):self.assertTrue(condition,key);CHECKS.append(dict(id=key,status='passed',layer='process'))
 def balanced(self,h):
  b=call(h,'ledger.read');self.check(abs(b['energy_residual_j'])<2e-9,'mechanical_thermal_energy_balance');self.check(abs(b['mass_residual_kg'])<1e-15,'mass_balance');self.check(abs(b['volume_residual_m3'])<1e-18,'volume_balance');return b
 def reject(self,h,k,code=None,**a):
  state=copy.deepcopy({key:value for key,value in h.__dict__.items() if key!='_segment'});seg=h._segment
  with self.assertRaises(ActionRejected) as e:h.invoke(k,**a)
  if code:self.assertEqual(str(e.exception),code)
  self.assertEqual(state,{key:value for key,value in h.__dict__.items() if key!='_segment'});self.assertIs(seg,h._segment)
 def test01_fixed_config_and_units(self):
  self.assertAlmostEqual(GRAVITY_COEFFICIENT_NM*(1-math.cos(RELEASE_ANGLE_RAD)),.5);self.assertAlmostEqual(math.sqrt(2*.5/INERTIA_KG_M2)*.25,2.9);self.assertAlmostEqual(math.degrees(ENCODER_RESOLUTION_RAD),.018);self.assertEqual(SUPPORT_SPAN_M,.062);h=configured();self.reject(h,'fixture.configure','INVALID_ARGUMENT',**dict(FIXTURE_EXAMPLE,pendulum_energy_j=5));self.check(True,'nominal0_5J_not5J')
 def test02_raise_is_finite_host_work(self):
  h=ZwickHit5pRuntime();call(h,'plant.configure',**PLANT_EXAMPLE);call(h,'fixture.configure',**FIXTURE_EXAMPLE);call(h,'shield.set_open',opened=False);h.step(.6);call(h,'pendulum.raise');self.assertEqual(h.theta,0);h.step(.1);self.assertAlmostEqual(h.theta,.1,delta=1e-9);self.assertAlmostEqual(h.omega,1.,delta=1e-9);h.step(3);self.assertAlmostEqual(h.theta,RELEASE_ANGLE_RAD);b=self.balanced(h);self.assertAlmostEqual(b['host_work_j'],.5+.001*RELEASE_ANGLE_RAD,places=10)
 def test03_independent_lossless_period_quadrature(self):
  h=configured(dict(friction_torque_nm=0.));call(h,'calibration.start');call(h,'pendulum.catch');h.step(2);a=RELEASE_ANGLE_RAD;k=math.sin(a/2);quarter=math.sqrt(INERTIA_KG_M2/GRAVITY_COEFFICIENT_NM)*quad(lambda u:1/math.sqrt(1-k*k*math.sin(u)**2),0,math.pi/2,epsabs=1e-12)[0];self.assertAlmostEqual(h.elapsed,2*quarter,delta=2e-8);self.assertAlmostEqual(abs(h.theta),a,places=10);self.assertAlmostEqual(h.calibration,0.,places=12);self.balanced(h)
 def test04_actual_empty_calibration(self):
  h=calibrated();r=call(h,'acquisition.result');alpha=r['release_angle_rad'];beta=abs(r['rebound_angle_rad']);tau=GRAVITY_COEFFICIENT_NM*(math.cos(beta)-math.cos(alpha))/(alpha+beta);self.assertEqual(h.calibration,tau);self.assertNotEqual(h.calibration,PLANT_EXAMPLE['friction_torque_nm']);self.assertAlmostEqual(h.calibration,.001,delta=3e-6);self.check(True,'calibration_from_quantized_external_angles')
 def test05_impact_reference_default(self):
  h=loaded();r=impact(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(r['outcome'],'break');self.assertAlmostEqual(r['corrected_energy_j'],.16,delta=.0001);self.assertAlmostEqual(r['impact_strength_j_m2'],5000,delta=4);self.assertTrue(2.61<=r['observed_impact_speed_m_s']<=3.19);self.assertTrue(h.broken);self.balanced(h)
 def test06_independent_impact_kinetic_drop(self):
  h=loaded();call(h,'acquisition.start');h._new_segment();event=h._segment['end_ns'];h.step((event-h.time_ns)/1e9);expected_available=.5-PLANT_EXAMPLE['friction_torque_nm']*RELEASE_ANGLE_RAD;self.assertAlmostEqual(.5*INERTIA_KG_M2*h.omega*h.omega,expected_available-.16,delta=1e-10);self.assertAlmostEqual(h.absorbed,.16,places=14);self.balanced(h)
 def test07_fracture_heat_damage_partition(self):
  h=loaded();impact(h);b=self.balanced(h);self.assertAlmostEqual(b['sample_absorbed_j'],.16,places=13);self.assertAlmostEqual(b['sample_heat_j'],.128,places=13);self.assertAlmostEqual(b['sample_damage_stored_j'],.032,places=13);self.assertAlmostEqual(b['sample_thermal_j']+b['sample_heat_export_j'],.128,places=11)
 def test08_no_break_is_not_full_strength(self):
  h=loaded(sample=dict(behavior='unbreakable_proxy',no_break_restitution=.7));r=impact(h);self.assertEqual(r['outcome'],'no_break');self.assertEqual(r['validity'],'no_break');self.assertIsNone(r['impact_strength_j_m2']);self.assertFalse(h.broken);self.assertTrue(h.spent);self.assertGreater(h.theta,0);self.balanced(h)
 def test09_capacity_upper_and_no_break(self):
  h=loaded(sample=dict(host_charpy_strength_j_m2=20000.));r=impact(h);self.assertEqual(r['outcome'],'no_break');self.assertEqual(r['validity'],'above_working_range');self.assertEqual(r['phase'],'failed');self.assertIsNone(r['impact_strength_j_m2']);self.balanced(h)
 def test10_capacity_lower(self):
  h=loaded(sample=dict(host_charpy_strength_j_m2=1000.));r=impact(h);self.assertEqual(r['outcome'],'break');self.assertEqual(r['validity'],'below_working_range');self.assertEqual(r['phase'],'failed');self.assertIsNone(r['impact_strength_j_m2']);self.assertAlmostEqual(r['corrected_energy_j'],.032,delta=.00015)
 def test11_complete_break_above_validrange(self):
  h=loaded(sample=dict(host_charpy_strength_j_m2=14000.));r=impact(h);self.assertTrue(h.broken);self.assertEqual(r['outcome'],'break');self.assertEqual(r['validity'],'above_working_range');self.assertIsNone(r['impact_strength_j_m2']);self.balanced(h)
 def test12_external_area_prior_required(self):
  h=loaded(method=dict(remaining_area_prior_m2=None));r=impact(h);self.assertEqual(r['validity'],'valid');self.assertAlmostEqual(r['corrected_energy_j'],.16,delta=.0001);self.assertIsNone(r['impact_strength_j_m2']);self.check(True,'no_unknown_area_inversion')
 def test13_external_analysis_independent_units(self):
  h=configured();a=RELEASE_ANGLE_RAD;beta=math.acos(math.cos(a)+.2/GRAVITY_COEFFICIENT_NM);r=call(h,'analysis.impact',release_angle_rad=a,rebound_angle_rad=-beta,friction_torque_nm=0.,remaining_area_prior_m2=2e-5,outcome='break');self.assertAlmostEqual(r['corrected_energy_j'],.2);self.assertAlmostEqual(r['impact_strength_j_m2'],10000.);self.assertEqual(r['validity'],'valid');self.check(True,'ISO179_J_per_m2_not_J_per_m')
 def test14_method_only_changes_sampling(self):
  a=loaded(method=dict(sample_interval_s=.001));b=loaded(method=dict(sample_interval_s=.01));ra=impact(a);rb=impact(b);self.assertEqual(a.absorbed,b.absorbed);self.assertAlmostEqual(a.theta,b.theta,places=12);self.assertEqual(ra['corrected_energy_j'],rb['corrected_energy_j']);self.assertGreater(len(ra['rows']),len(rb['rows']));self.check(True,'sampling_not_physics_time_scale')
 def test15_60Hz_partition(self):
  a=loaded();b=loaded();call(a,'acquisition.start');call(b,'acquisition.start');call(a,'pendulum.catch');call(b,'pendulum.catch');a.step(2.)
  for _ in range(120):b.step(1/60)
  self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.rows,b.rows);self.assertEqual(a.theta,b.theta);self.assertAlmostEqual(a.sample_U,b.sample_U,places=12);self.balanced(a);self.balanced(b)
 def test16_natural_free_swing_partition(self):
  a=configured();b=configured();call(a,'calibration.start');call(b,'calibration.start');a.step(5.)
  for _ in range(137):b.step(5/137)
  self.assertEqual(a.time_ns,b.time_ns);self.assertAlmostEqual(a.theta,b.theta,places=12);self.assertAlmostEqual(a.omega,b.omega,places=12);self.assertEqual(a.rows,b.rows);self.assertEqual(a.mechanical,'free');self.balanced(a)
 def test17_completed_measurement_does_not_stop_motion(self):
  h=loaded();r=impact(h,catch=False);self.assertEqual(r['phase'],'succeeded');self.assertEqual(h.mechanical,'free');theta=h.theta;h.step(.1);self.assertNotEqual(h.theta,theta);self.assertEqual(h.rows,r['rows']);self.balanced(h)
 def test18_cancel_before_impact_still_breaks(self):
  h=loaded();call(h,'acquisition.start');h.step(.1);call(h,'acquisition.stop');snapshot=call(h,'acquisition.result');theta=h.theta;call(h,'pendulum.catch');h.step(1);self.assertTrue(h.broken);self.assertTrue(h.spent);self.assertNotEqual(h.theta,theta);self.assertEqual(call(h,'acquisition.result'),snapshot);self.assertEqual(h.phase,'cancelled');self.balanced(h)
 def test19_power_off_keeps_physics(self):
  h=loaded();call(h,'acquisition.start');h.step(.1);call(h,'power.set',enabled=False);call(h,'pendulum.catch');h.step(1);self.assertTrue(h.broken);self.assertEqual(h.phase,'cancelled');self.assertIsNone(h.read()['encoder.angle_rad']);self.assertIsNone(h.read()['measurement.energy_j']);self.balanced(h)
 def test20_catch_only_at_future_turn(self):
  h=configured();call(h,'calibration.start');h.step(.1);theta=h.theta;omega=h.omega;call(h,'pendulum.catch');self.assertEqual(h.theta,theta);self.assertEqual(h.omega,omega);self.assertEqual(h.mechanical,'free');h.step(1);self.assertEqual(h.mechanical,'held');self.assertEqual(h.omega,0.);self.assertGreater(h._energy(),.49);self.balanced(h)
 def test21_free_decay_static_friction(self):
  h=configured(dict(friction_torque_nm=.05));call(h,'calibration.start');h.step(20);self.assertEqual(h.mechanical,'held');self.assertAlmostEqual(h.omega,0);self.assertLessEqual(abs(GRAVITY_COEFFICIENT_NM*math.sin(h.theta)),.05+1e-10);self.balanced(h)
 def test22_sample_removal_carries_energy(self):
  h=loaded();impact(h);call(h,'pendulum.raise');h.step(5);call(h,'shield.set_open',opened=True);h.step(.6);m=call(h,'sample.remove');self.assertEqual(m['removed_mass_kg'],SAMPLE_EXAMPLE['mass_kg']);self.assertGreater(h.book['internal_removed_j'],0);self.assertGreater(h.book['sensible_removed_j'],0);self.assertFalse(h.sample);self.balanced(h)
 def test23_sensible_load_no_energy_creation(self):
  h=loaded(sample=dict(initial_temperature_k=297.15));self.assertAlmostEqual(h.book['sensible_loaded_j'],.00415*1200);self.balanced(h)
 def test24_temperature_after_impact_invalid(self):
  h=loaded(sample=dict(temperature_tolerance_k=.001));r=impact(h);self.assertTrue(h.broken);self.assertEqual(r['validity'],'temperature_invalid');self.assertEqual(r['phase'],'failed');self.assertIsNone(r['impact_strength_j_m2']);self.balanced(h)
 def test25_temperature_source_guard(self):
  h=calibrated();call(h,'pendulum.raise');h.step(5);call(h,'shield.set_open',opened=True);h.step(.6);self.reject(h,'sample.load','REFERENCE_TEMPERATURE_RANGE',**dict(SAMPLE_EXAMPLE,initial_temperature_k=310.,temperature_tolerance_k=1.))
 def test26_shield_and_loading_geometry(self):
  h=configured();self.reject(h,'sample.load','LOADING_GEOMETRY',**SAMPLE_EXAMPLE);call(h,'calibration.start');self.reject(h,'shield.set_open','BUSY',opened=True);h.step(2);self.reject(h,'shield.set_open','PENDULUM_MOVING',opened=True);self.reject(h,'pendulum.raise','PENDULUM_MOVING');self.balanced(h)
 def test27_no_calibration_no_measurement(self):
  h=loaded();call(h,'calibration.clear');self.reject(h,'acquisition.start','CALIBRATION_REQUIRED');self.check(True,'empty_calibration_required')
 def test28_spent_sample_cannot_repeat(self):
  h=loaded();impact(h);call(h,'pendulum.raise');h.step(5);self.reject(h,'acquisition.start','SAMPLE_SPENT');self.check(True,'irreversible_sample_state')
 def test29_non_numeric_inputs_atomic(self):
  h=loaded();self.reject(h,'power.set','INVALID_ARGUMENT',enabled=1);self.reject(h,'method.configure','INVALID_ARGUMENT',**dict(METHOD_EXAMPLE,sample_interval_s=float('nan')));self.reject(h,'power.set','INVALID_ARGUMENT',enabled=True,unknown=0)
  for dt in [True,-1,float('inf'),float('nan')]:
   before=h.time_ns
   with self.assertRaises(ActionRejected):h.step(dt)
   self.assertEqual(h.time_ns,before)
  self.check(True,'input_schema_and_rejection_atomicity')
 def test30_missing_reference_never_zero(self):
  h=calibrated();call(h,'pendulum.raise');h.step(5);call(h,'shield.set_open',opened=True);h.step(.6);self.reject(h,'sample.load','MISSING_CHARPY_REFERENCE_OR_PRIOR',**dict(SAMPLE_EXAMPLE,notch='unnotched',notch_depth_m=0.));call(h,'sample.load',**dict(SAMPLE_EXAMPLE,notch='unnotched',notch_depth_m=0.,behavior='unbreakable_proxy'));self.assertIsNone(h.break_energy);self.check(True,'not_broken_N_not_zero')
 def test31_real61_grade_references(self):
  records=library();self.assertGreaterEqual(len(records),50)
  for r in records.values():self.assertTrue(r['source']);self.assertEqual(r['reference_temperature_K'],296.15);self.assertTrue('charpy_notched_impact_strength_J_m2' in r)
  self.check(True,'real_material_library_evidence_and_units')
 def test32_declared_geometry_guards(self):
  h=calibrated();call(h,'pendulum.raise');h.step(5);call(h,'shield.set_open',opened=True);h.step(.6);self.reject(h,'sample.load','INVALID_NOTCH_GEOMETRY',**dict(SAMPLE_EXAMPLE,notch_depth_m=.01));self.reject(h,'sample.load','INVALID_VOLUME',**dict(SAMPLE_EXAMPLE,volume_m3=1e-5));self.reject(h,'sample.load','ENGINEERING_ACCEPTANCE_REQUIRED',**dict(SAMPLE_EXAMPLE,accept_reference_transfer_and_fracture_proxy=False));self.check(True,'remaining_notch_area_explicit')
 def test33_sensor_quantization_not_exact_truth(self):
  h=configured();call(h,'calibration.start');h.step(.123);value=h.read()['encoder.angle_rad'];self.assertAlmostEqual(value/ENCODER_RESOLUTION_RAD,round(value/ENCODER_RESOLUTION_RAD));self.assertNotEqual(value,h.theta);self.assertNotIn('pendulum.angle_rad',h.read());self.assertNotIn('truth.sample_absorbed_j',h.read());self.check(True,'exact_scene_states_restricted')
 def test34_all_public_roles_redacted(self):
  h=loaded();impact(h)
  for role in ['agent','evaluator','debug']:
   h.register_principal(role,role);h._owners[h.operation_id]=role;s=h.session(role);r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='r');payload=json.dumps(r);self.assertNotIn(SAMPLE_EXAMPLE['reference_id'],payload);self.assertNotIn('Ultradur',payload);self.assertNotIn('B4520',payload);self.assertNotIn(SAMPLE_EXAMPLE['reference_id'],json.dumps(s.poll(h.operation_id)))
  self.check(True,'privileged_bridge_public_result_filter')
 def test35_private_provenance_snapshot(self):
  h=loaded();impact(h);source=call(h,'provenance.read')['operation_source'];h.register_principal('a','agent');s=h.session('a');r=s.invoke(instance_id=h.instance_id,capability_id='provenance.read',arguments={},request_id='p');self.assertEqual(r['error']['code'],'ACCESS_DENIED');call(h,'pendulum.raise');h.step(5);call(h,'shield.set_open',opened=True);h.step(.6);call(h,'sample.remove');p=call(h,'provenance.read');self.assertIsNone(p['current_sample']);self.assertEqual(p['operation_source'],source)
 def test36_idempotence_owner_and_cancel(self):
  h=loaded();h.register_principal('a','agent');h.register_principal('b','agent');a=h.session('a');b=h.session('b');r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start'),r);h.step(.1)
  with self.assertRaises(ActionRejected):b.poll(r['operation_id'])
  c=a.cancel(r['operation_id'],'cancel');self.assertEqual(a.cancel(r['operation_id'],'cancel'),c);bad=a.invoke(instance_id=h.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='start');self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT');self.check(True,'identity_owner_and_request_retry')
 def test37_read_poll_do_not_advance(self):
  h=loaded();h.register_principal('a','agent');s=h.session('a');r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');t=h.time_ns;theta=h.theta;rows=copy.deepcopy(h.rows)
  for _ in range(10):s.read(instance_id=h.instance_id);s.poll(r['operation_id']);s.describe(instance_id=h.instance_id)
  self.assertEqual(h.time_ns,t);self.assertEqual(h.theta,theta);self.assertEqual(h.rows,rows);self.check(True,'pure_read_describe_poll')
 def test38_instance_isolation(self):
  a=loaded();b=loaded(sample=dict(host_charpy_strength_j_m2=8000.));ra=impact(a);self.assertFalse(b.spent);rb=impact(b);self.assertNotEqual(ra['corrected_energy_j'],rb['corrected_energy_j']);self.assertAlmostEqual(b.absorbed,.256);self.check(True,'independent_instances')
 def test39_no_fake_instrumented_modes(self):
  h=configured()
  for k in UNSUPPORTED:self.reject(h,k,'UNSUPPORTED')
  self.check(True,'unsupported_is_negative_only')
 def test40_schema_examples_and_all_reads(self):
  meta=export_zwick_hit5p_contract_metadata();schemas=[v[s] for v in CAPS.values() for s in ['input_schema','output_schema']]+[v['schema'] for v in SLOTS.values()];r=subprocess.run([sys.executable,'-c','import sys,json;from jsonschema import Draft202012Validator;[Draft202012Validator.check_schema(s) for s in json.load(sys.stdin)]'],input=json.dumps(schemas),text=True,capture_output=True);self.assertEqual(r.returncode,0,r.stderr)
  for k,c in CAPS.items():validate(c['input_schema'],c['example_arguments']);self.assertEqual(meta['capabilities'][k]['availability'],'unsupported' if k in UNSUPPORTED else 'implemented')
  for k,v in configured().read('debug').items():validate(SLOTS[k]['schema'],v)
  self.check(True,'all_contract_schemas_units_examples')
 def test41_step_cap_and_fractional_ns(self):
  h=configured();t=h.time_ns
  for _ in range(60):call(h,'sim.step',dt_s=1/60)
  self.assertEqual(h.time_ns-t,1_000_000_000);self.check(True,'fractional_ns_no_drift')
 def test42_external_negative_energy_not_clipped(self):
  h=configured();r=call(h,'analysis.impact',**dict(CAPS['analysis.impact']['example_arguments'],rebound_angle_rad=-RELEASE_ANGLE_RAD,friction_torque_nm=.001));self.assertLess(r['corrected_energy_j'],0);self.assertEqual(r['validity'],'negative_corrected_energy');self.assertIsNone(r['impact_strength_j_m2']);self.check(True,'negative_corrected_energy_not_hidden')
 def test43_empty_requires_no_sample(self):
  h=loaded();self.reject(h,'calibration.start','EMPTY_REQUIRED');self.reject(h,'sample.load','SAMPLE_PRESENT',**SAMPLE_EXAMPLE);self.check(True,'calibration_empty_guard')
 def test44_sample_grid_and_event_labels(self):
  h=loaded();r=impact(h);times=[x['time_s'] for x in r['rows'] if x['kind']=='sample'];self.assertTrue(np.allclose(np.diff(times),.002,atol=1e-12));self.assertEqual([x['kind'] for x in r['rows'] if x['kind']!='sample'],['impact','apex']);self.assertGreater(r['elapsed_s'],.5);self.check(True,'real_time_encoder_samples_and_events')
 def test45_passive_heat_independent_solution(self):
  h=loaded();impact(h);U=h.sample_U;C=.01+.00415*1200.;h.step(1.234);self.assertAlmostEqual(h.sample_U,U*math.exp(-.03*1.234/C),places=13);self.balanced(h)

 def test46_stiff_thermal_configuration_rejected_atomically(self):
  h=ZwickHit5pRuntime();self.reject(h,'plant.configure','THERMAL_TIME_CONSTANT_OUTSIDE_NUMERICAL_DOMAIN',**dict(PLANT_EXAMPLE,local_sample_heat_capacity_j_k=1e-6,sample_conductance_w_k=1e4));self.reject(h,'plant.configure','THERMAL_TIME_CONSTANT_OUTSIDE_NUMERICAL_DOMAIN',**dict(PLANT_EXAMPLE,bearing_heat_capacity_j_k=.001,bearing_conductance_w_k=1e4));h=configured(dict(local_sample_heat_capacity_j_k=.001,sample_conductance_w_k=1.,bearing_heat_capacity_j_k=.001,bearing_conductance_w_k=1.));call(h,'calibration.start');call(h,'pendulum.catch');h.step(2);self.assertEqual(h.phase,'succeeded');self.balanced(h);self.check(True,'thermal_minimum1ms_is_numerical_domain_not_OEM')

if __name__=='__main__':unittest.main(verbosity=2)
