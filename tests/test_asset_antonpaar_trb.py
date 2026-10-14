"""CHEM128 real-pair finite tribology and protocol regression tests."""
import copy,hashlib,importlib.util,json,math,sys,unittest
from pathlib import Path
from unittest import mock
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import antonpaar_trb as rt
OBSERVED=[];STATES=[]
def canonical(v):
 if isinstance(v,dict):return {str(k):canonical(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [canonical(x) for x in v]
 if isinstance(v,np.ndarray):return v.tolist()
 return v
def state(h):return json.dumps(canonical(h.__dict__),sort_keys=True,allow_nan=False)
def call(h,k,**a):
 before=h.read();r=h.invoke(k,**a);after=h.read();OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));STATES.append(after)
 changed={x for x in before if before[x]!=after[x]};declared=set(rt.CAPS[k]['effects'])
 if not changed<=declared:raise AssertionError((k,'undeclared changed state',changed-declared))
 return r
def setup(name='trb_test',plant=None,sample=None,pin=None,drive=None,method=None,lubricant=None,load=10.,radius=.01):
 h=rt.AntonpaarTrbRuntime(name)
 for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,**(plant or {}))),('specimen.load',dict(rt.SAMPLE_EXAMPLE,**(sample or {}))),('pin.load',dict(rt.PIN_EXAMPLE,**(pin or {}))),('normal_load.set',{'force_n':load}),('radius.set',{'radius_m':radius}),('drive.configure',dict(rt.DRIVE_EXAMPLE,**(drive or {})))]:call(h,k,**a)
 if lubricant is not None:call(h,'lubricant.load',**dict(rt.LUBRICANT_EXAMPLE,**lubricant))
 call(h,'power.set',enabled=True);call(h,'sensor.zero');call(h,'contact.set',engaged=True);call(h,'lid.set',opened=False);call(h,'method.configure',**dict(dict(rt.METHOD_EXAMPLE,duration_s=2.),**(method or {})));return h
def run(h,seconds=None):
 call(h,'acquisition.start');h.step(h.method['duration_s'] if seconds is None else seconds);return call(h,'acquisition.result')
def access(h):
 if h.phase=='running':call(h,'acquisition.stop')
 call(h,'lid.set',opened=True);call(h,'contact.set',engaged=False)
def cleanup(h):
 access(h)
 for k in ['specimen','pin','lubricant']:
  if getattr(h,k) is not None:call(h,k+'.remove')
 call(h,'debris.collect');call(h,'power.set',enabled=False)
class TestTRB(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=state(h)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,state(h))
 def ledger(self,h):
  l=call(h,'ledger.read')
  for k in ['specimen_error_kg','pin_error_kg','lubricant_error_kg']:self.assertLess(abs(l[k]),1e-13,k)
  self.assertLess(abs(l['thermal_error_j']),1e-7);return l
 def test_01_real_pair_sources_and_integrated_work(self):
  energies=[]
  for rid in ['cr100_leadbronze_dry_003','cr100_leadbronze_dry_005']:
   h=setup(sample={'reference_id':rid});original=copy.deepcopy(h.reference);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(r['rows']),21);self.assertAlmostEqual(r['sliding_distance_m'],.2);x=np.array(original['values']['sliding_distance_m']);y=np.array(original['values']['friction_coefficient']);q=np.r_[0,x[(x>0)&(x<.2)],.2];expected=10*np.trapz(np.interp(q,x,y),q);self.assertAlmostEqual(h.friction_energy,expected,places=10);self.assertEqual(original,h.reference);self.assertEqual(len(x),5002);self.assertEqual(original['conditions']['normal_force_n'],10);self.assertAlmostEqual(original['conditions']['counterbody_radius_m'],.00476);energies.append(h.friction_energy);self.ledger(h)
  self.assertNotEqual(*energies)
 def test_02_high_speed_rotary_full_turns_not_endpoint_alias(self):
  h=setup(drive={'angular_speed_rad_s':rt.OMEGA_MAX},method={'duration_s':.3});r=run(h);self.assertAlmostEqual(r['sliding_distance_m'],2*math.pi*.01*10,places=11);self.assertAlmostEqual(h.disk_angle,0.,places=9);self.assertGreater(h.friction_energy,0)
 def test_03_linear_10hz_reversals_have_nonzero_distance(self):
  h=setup(drive={'mode':'linear','frequency_hz':10.,'stroke_m':.01},method={'duration_s':.3});r=run(h);self.assertAlmostEqual(r['sliding_distance_m'],.06,places=11);self.assertAlmostEqual(h.linear_position,0.,places=10);self.assertGreater(h.friction_energy,0);self.ledger(h)
 def test_04_angular_reciprocal_signed_force_and_path(self):
  h=setup(drive={'mode':'angular','frequency_hz':1.,'angular_amplitude_rad':.5},method={'duration_s':1.});r=run(h);self.assertAlmostEqual(r['sliding_distance_m'],.02,places=11);self.assertLess(min(x['friction_force_n'] for x in r['rows'] if x['friction_force_n'] is not None),0);self.assertGreater(min(x['friction_coefficient'] for x in r['rows'] if x['friction_coefficient'] is not None),0);self.ledger(h)
 def test_05_exact_turning_point_zero_speed_no_fake_mu(self):
  h=setup(drive={'mode':'linear','frequency_hz':2.5,'stroke_m':.01});call(h,'acquisition.start');h.step(.1);self.assertAlmostEqual(h.signed_speed,0.,places=10);self.assertIsNone(h.read()['friction.coefficient']);self.assertEqual(h.read()['friction.unavailable_reason'],'REVERSAL_ZERO_SPEED')
 def test_06_combined_limits_and_atomic_reject(self):
  h=setup();access(h)
  for args,error in [(dict(rt.DRIVE_EXAMPLE,mode='linear',frequency_hz=10.,stroke_m=.06),'LINEAR_SPEED_RANGE'),(dict(rt.DRIVE_EXAMPLE,mode='angular',frequency_hz=8.),'ANGULAR_COMBINATION_RANGE'),(dict(rt.DRIVE_EXAMPLE,mode='angular',frequency_hz=.01,angular_amplitude_rad=math.pi/36),'ANGULAR_COMBINATION_RANGE')]:self.rejected(h,'drive.configure',error,**args)
  self.rejected(h,'normal_load.set',force_n=60.0001);self.rejected(h,'radius.set',radius_m=.040001);self.rejected(h,'drive.configure',**dict(rt.DRIVE_EXAMPLE,angular_speed_rad_s=rt.OMEGA_MAX+1))
 def test_07_deadweight_manual_access_and_actual_force_limit(self):
  h=setup();self.rejected(h,'normal_load.set','ARM_LOADED',force_n=5.);call(h,'acquisition.start');self.rejected(h,'contact.set','BUSY',engaged=False);self.rejected(h,'normal_load.set','BUSY',force_n=5.);access(h);call(h,'normal_load.set',force_n=4.);self.assertEqual(h._normal_true(),0.);call(h,'contact.set',engaged=True);self.assertEqual(h._normal_true(),4.)
  h=setup(plant={'normal_force_factor':2.});access(h);self.rejected(h,'normal_load.set','NORMAL_FORCE_RANGE',force_n=31.)
 def test_08_torque_start_and_first_crossing_terminal(self):
  h=setup(load=60.,radius=.04,method={'source_start_distance_m':2.});self.rejected(h,'acquisition.start','DRIVE_FORCE_RANGE')
  h=setup(radius=.04);h.ref_x=np.array([0.,.1,1.]);h.ref_mu=np.array([.1,2.,2.]);r=run(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'DRIVE_FORCE_RANGE');self.assertAlmostEqual(r['sliding_distance_m'],(.45/(10*.04)-.1)/1.9*.1,places=11);self.assertGreater(r['elapsed_s'],0);self.assertEqual(r['rows'][-1]['elapsed_s'],r['elapsed_s']);self.ledger(h)
 def test_09_source_endpoint_finite_and_no_extrapolation(self):
  h=setup(method={'source_start_distance_m':500.});r=run(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'REFERENCE_DISTANCE_RANGE');self.assertAlmostEqual(r['sliding_distance_m'],.009,places=9);self.rejected(h,'acquisition.start','REFERENCE_DISTANCE_RANGE');self.ledger(h)
 def test_10_origin_history_survives_restart_and_method(self):
  h=setup();run(h);distance=h.sample_distance;call(h,'method.configure',**dict(h.method,duration_s=.5));run(h);self.assertAlmostEqual(h.sample_distance,distance+.05);self.rejected(h,'method.configure','SOURCE_ORIGIN_LOCKED',**dict(h.method,source_start_distance_m=1.))
 def test_11_finite_both_wear_inventories_and_recovery(self):
  h=setup();run(h);l=self.ledger(h);self.assertAlmostEqual(l['specimen_wear_volume_m3'],1e-5*10*.2/2e9,places=20);self.assertAlmostEqual(l['pin_wear_volume_m3'],1e-6*10*.2/4e9,places=20);self.assertGreater(l['specimen_debris_kg'],0);cleanup(h);l=self.ledger(h);self.assertEqual(l['specimen_remaining_kg'],0);self.assertEqual(l['pin_remaining_kg'],0);self.assertEqual(l['specimen_debris_kg'],0);self.assertGreater(l['specimen_debris_recovered_kg'],0)
 def test_12_extremely_small_specimen_and_pin_stop(self):
  for item,label in [('sample','SPECIMEN_EXHAUSTED'),('pin','PIN_EXHAUSTED')]:
   h=setup(**{item:{'volume_m3':1e-15,'hardness_pa':1e3,'archard_coefficient':.1}});r=run(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],label);self.assertLess(r['elapsed_s'],1e-9);self.assertLessEqual(getattr(h,'specimen_volume' if item=='sample' else 'pin_volume'),1e-28);self.ledger(h)
 def test_13_finite_lubricant_preserves_catch_inventory(self):
  h=setup(lubricant={'volume_m3':1e-12,'loss_per_distance_m2':1e-6});r=run(h);self.assertEqual(r['error_code'],'LUBRICANT_EXHAUSTED');self.assertAlmostEqual(r['sliding_distance_m'],1e-6,places=15);self.assertLess(h.lubricant_volume,1e-24);l=self.ledger(h);self.assertAlmostEqual(l['lubricant_loaded_kg'],l['lubricant_dragout_kg'],places=18);cleanup(h);self.ledger(h)
 def test_14_differential_common_mode_rejection(self):
  a=setup();b=setup(plant={'common_drift_n_s':.7,'thermal_common_drift_n_k':.4});run(a);run(b);self.assertGreater(abs(a.left-b.left),1.);self.assertAlmostEqual(a.observed_work,b.observed_work,places=11)
  for x,y in zip(a.rows,b.rows):
   if x['friction_force_n'] is not None:self.assertAlmostEqual(x['friction_force_n'],y['friction_force_n'],places=11)
 def test_15_observed_load_and_sensor_gain_not_true_mu(self):
  h=setup(plant={'normal_read_gain':2.,'friction_gain_left':1.5,'friction_gain_right':1.5});call(h,'acquisition.start');h.step(1.);s=h.read();self.assertAlmostEqual(s['friction.coefficient'],abs(s['friction.indicated_n'])/20.);self.assertNotAlmostEqual(s['friction.coefficient'],h._mu(),places=4);self.assertEqual(s['truth.normal_force_n'],10.)
 def test_16_low_load_and_sensor_overrange_are_unavailable(self):
  h=setup(load=.001);call(h,'acquisition.start');h.step(.1);self.assertIsNone(h.read()['friction.coefficient']);self.assertEqual(h.read()['friction.unavailable_reason'],'LOW_LOAD')
  h=setup(plant={'friction_gain_left':100.,'friction_gain_right':100.});r=run(h);self.assertEqual(r['error_code'],'FRICTION_SENSOR_OVER_RANGE');self.assertIsNone(r['rows'][-1]['friction_force_n']);self.assertGreater(h.friction_energy,0);self.ledger(h)
 def test_17_external_calibration_zero_and_optional_wear(self):
  h=setup(plant={'friction_offset_left_n':.5,'wear_sensor_enabled':True,'temperature_sensor_offset_k':2.});access(h);h.step(.1);call(h,'sensor.zero');self.assertAlmostEqual(h.calibration['friction_zero_n'],.5);cal=dict(rt.CAPS['sensor.calibrate']['example_arguments'],temperature_zero_k=2.,friction_zero_n=.5);call(h,'sensor.calibrate',**cal);call(h,'contact.set',engaged=True);call(h,'lid.set',opened=False);run(h);self.assertGreater(h.rows[-1]['wear_depth_m'],0);self.assertAlmostEqual(h.rows[-1]['temperature_k'],h.temperature);self.assertIsNone(setup().read()['wear.indicated_m'])
 def test_18_stiff_optional_heater_exact_and_finite(self):
  h=setup(plant={'thermal_capacity_j_k':.01,'heater_installed':True,'heater_max_w':1000.,'heater_gain_w_k':1e6});call(h,'thermal.set_temperature',temperature_k=303.15);self.assertEqual(h.temperature,296.15);h.step(1.);expected=(1e6*303.15+296.15)/(1e6+1);self.assertAlmostEqual(h.temperature,expected,places=8);self.assertLess(h.temperature,303.15);self.ledger(h)
 def test_19_friction_heat_adiabatic_energy_and_passive_cooling(self):
  h=setup(plant={'heat_loss_w_k':0.});run(h);self.assertAlmostEqual(h.temperature,296.15+h.friction_energy/100.,places=9);self.assertEqual(h.heater_energy,0);self.ledger(h)
  h=setup(plant={'thermal_capacity_j_k':10.});run(h);start=h.temperature;call(h,'power.set',enabled=False);h.step(3.);self.assertAlmostEqual(h.temperature,296.15+(start-296.15)*math.exp(-.3),places=10);self.ledger(h)
 def test_20_thermal_permissions_and_liquid_limits_atomic(self):
  h=setup();self.rejected(h,'thermal.set_temperature','HEATER_NOT_INSTALLED',temperature_k=300.);self.rejected(h,'thermal.set_temperature','NO_ACTIVE_COOLING',temperature_k=295.)
  h=setup(plant={'heater_installed':True,'heater_max_w':100.,'temperature_max_k':723.15},lubricant={});self.rejected(h,'thermal.set_temperature','LIQUID_TEMPERATURE_RANGE',temperature_k=423.16)
 def test_21_hot_access_and_temperature_terminal_preserve_work(self):
  h=setup(plant={'thermal_capacity_j_k':.01,'heat_loss_w_k':0.,'temperature_max_k':296.16,'safe_access_temperature_k':296.151});r=run(h);self.assertEqual(r['error_code'],'THERMAL_MODEL_RANGE');self.assertGreater(h.temperature,296.16);self.assertGreater(h.friction_energy,0);access(h);self.rejected(h,'specimen.remove','HOT_SPECIMEN');self.ledger(h)
 def test_22_profile_loss_pileup_net_and_radial_weight(self):
  h=setup();a=dict(rt.CAPS['analysis.wear_profile']['example_arguments'],geometry='linear',x_m=[0.,.001,.002],height_m=[.0001,0.,-.0001],track_length_m=.01);r=call(h,'analysis.wear_profile',**a);self.assertAlmostEqual(r['loss_volume_m3'],5e-10,places=18);self.assertAlmostEqual(r['pileup_volume_m3'],5e-10,places=18);self.assertAlmostEqual(r['net_volume_m3'],0.,places=18)
  a.update(geometry='circular');r=call(h,'analysis.wear_profile',**a);self.assertGreater(r['net_volume_m3'],0.);self.assertAlmostEqual(r['net_volume_m3'],2*math.pi*(.002**3/(12)*.1),places=18);self.rejected(h,'analysis.wear_profile','MODEL_NOT_ACCEPTED',**dict(a,accept_uniform_profile=False));self.rejected(h,'analysis.wear_profile','PROFILE_GRID',**dict(a,x_m=[0.,0.,.002]))
 def test_23_hertz_force_contact_and_pressure_integral(self):
  h=setup();a=rt.CAPS['analysis.hertz']['example_arguments'];r=call(h,'analysis.hertz',**a);radius=r['contact_radius_m'];self.assertAlmostEqual(4*r['effective_modulus_pa']*radius**3/(3*a['radius_m']),a['normal_force_n'],places=11);self.assertAlmostEqual(2*math.pi*r['peak_pressure_pa']*radius**2/3,a['normal_force_n'],places=11);self.rejected(h,'analysis.hertz','MODEL_NOT_ACCEPTED',**dict(a,accept_elastic_homogeneous=False));self.rejected(h,'analysis.hertz','HERTZ_MODEL_DOMAIN',**dict(a,young_sample_pa=1e5,young_pin_pa=1e5,radius_m=1e-5))
 def test_24_noquerytime_owner_result_and_freeze(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s')['operation_id'];h.step(.4);before=state(h);h.read();h.describe();call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);call(h,'ledger.read');s.read(instance_id=h.instance_id);s.poll(op);self.assertEqual(state(h),before);q=s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='r');self.assertIsNone(q['operation_id']);self.assertEqual(h._owners[op],'agent');s.cancel(op,'cancel');frozen=s.poll(op);mass=h.specimen_volume;h.step(1.);self.assertEqual(mass,h.specimen_volume);cleanup(h);self.assertEqual(s.poll(op),frozen)
 def test_25_owner_poll_result_cancel_and_idempotence(self):
  h=setup();h.register_principal('other');a=h.session();b=h.session('other');args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');r=a.invoke(**args);self.assertEqual(r,a.invoke(**args));op=r['operation_id'];self.assertEqual(h._counter,1)
  for k in ['acquisition.result','acquisition.stop']:self.assertEqual(b.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k)['error']['code'],'ACCESS_DENIED')
  for method in [lambda:b.poll(op),lambda:b.cancel(op,'bad')]:
   with self.assertRaises(rt.ActionRejected):method()
  self.assertEqual(a.invoke(**dict(args,capability_id='acquisition.result'))['error']['code'],'REQUEST_CONFLICT');self.assertEqual(a.cancel(op,'cancel')['status'],'succeeded');self.assertEqual(a.cancel(op,'cancel')['status'],'succeeded')
 def test_26_truth_recovery_and_simclock_permissions(self):
  h=setup();s=h.session();d=s.describe(instance_id=h.instance_id);self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']));self.assertFalse(any(k.startswith('truth.') for k in s.read(instance_id=h.instance_id)['observations']))
  for k in ['plant.configure','specimen.load','specimen.remove','pin.load','pin.remove','lubricant.load','lubricant.remove','debris.collect','ledger.read','sim.step']:
   before=state(h);r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=rt.CAPS[k]['example_arguments'],request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(state(h),before)
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.specimen_volume_m3'])
  run(h);text=json.dumps(h._result());self.assertNotIn('hardness_pa',text);self.assertNotIn('archard_coefficient',text);self.assertNotIn('specimen_volume_m3',text)
 def test_27_two_instances_fixed_host_partition(self):
  a=setup(name='same');b=setup(name='same');call(a,'acquisition.start');call(b,'acquisition.start');a.step(1.)
  for _ in range(100):b.step(.01)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);before=state(b);a.step(.1);self.assertEqual(before,state(b));before=state(a);a.step(0.);self.assertEqual(before,state(a))
 def test_28_emergency_and_manual_home_after_cancel(self):
  h=setup(drive={'mode':'linear','frequency_hz':1.});call(h,'acquisition.start');h.step(.1);pos=h.linear_position;call(h,'emergency.stop');self.assertEqual(h.phase,'failed');frozen=h._result();h.step(1.);self.assertEqual(pos,h.linear_position);self.assertEqual(frozen,h._result());call(h,'emergency.reset');self.rejected(h,'acquisition.start','HOME_REQUIRED');access(h);call(h,'motion.home');self.assertEqual(h.linear_position,pos);h.step(.1);self.assertLess(abs(h.linear_position),abs(pos));h.step(.2);self.assertFalse(h.homing);call(h,'contact.set',engaged=True);call(h,'lid.set',opened=False);run(h)
 def test_29_strict_source_conditions_and_reference_eligibility(self):
  h=setup(sample={'accept_condition_transfer':False});run(h);h=setup(sample={'accept_condition_transfer':False},drive={'mode':'linear'});self.rejected(h,'acquisition.start','REFERENCE_CONDITIONS')
  h=setup(sample={'accept_condition_transfer':False},lubricant={});self.rejected(h,'acquisition.start','REFERENCE_LUBRICATION')
  h=setup();access(h);call(h,'specimen.remove');record=next(v for v in rt.library()['substances'].values() if not v.get('runtime_usable'));self.rejected(h,'specimen.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id=next(k for k,v in rt.library()['substances'].items() if v==record)))
 def test_30_unknown_temperature_requires_explicit_acceptance(self):
  h=setup(sample={'accept_unknown_conditions':False});h.reference['conditions']['temperature_k']=None;self.rejected(h,'acquisition.start','UNKNOWN_REFERENCE_CONDITIONS')
 def test_31_zero_radius_stop_distance_and_sampling(self):
  h=setup(radius=0.);self.rejected(h,'acquisition.start','ZERO_TRACK_RADIUS');h=setup(method={'stop_distance_m':.012345});r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['sliding_distance_m'],.012345,places=12);self.assertAlmostEqual(r['elapsed_s'],.12345,places=10);self.assertEqual(r['rows'][-1]['elapsed_s'],r['elapsed_s']);self.assertGreater(h.time_s,r['elapsed_s']);self.rejected(h,'method.configure','SAMPLING_GRID',**dict(h.method,sample_interval_s=.15))
 def test_32_schema_input_nonfinite_and_unsupported(self):
  h=setup()
  for v in [float('nan'),float('inf'),-1,True]:
   before=state(h)
   with self.assertRaises(rt.ActionRejected):h.step(v)
   self.assertEqual(state(h),before)
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
  self.rejected(h,'method.configure',**dict(rt.METHOD_EXAMPLE,bogus=1));self.rejected(h,'power.set',enabled=1)
 def test_33_history_stable_on_new_sample_and_new_operation(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a')['operation_id'];h.step(2.);old=s.poll(op);access(h);call(h,'specimen.remove');call(h,'specimen.load',**dict(rt.SAMPLE_EXAMPLE,reference_id='cr100_leadbronze_dry_005'));call(h,'contact.set',engaged=True);call(h,'lid.set',opened=False);s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='b');h.step(.1);self.assertEqual(s.poll(op),old)
 def test_34_negative_source_not_silently_made_dissipative(self):
  r=copy.deepcopy(rt.library()['substances'][rt.SAMPLE_EXAMPLE['reference_id']]);r['values']['friction_coefficient'][0]=-.1
  with self.assertRaisesRegex(rt.ActionRejected,'INVALID_REFERENCE'):rt.curve(r)
 def test_35_plant_initial_only_and_explicit_assumptions(self):
  h=setup();self.rejected(h,'plant.configure','INITIALIZATION_ONLY',**rt.PLANT_EXAMPLE)
  h=rt.AntonpaarTrbRuntime();self.rejected(h,'plant.configure','MODEL_NOT_ACCEPTED',**dict(rt.PLANT_EXAMPLE,accept_engineering=False));self.rejected(h,'plant.configure','THERMAL_CONFIGURATION',**dict(rt.PLANT_EXAMPLE,heater_max_w=10.))
 def test_35b_strict_temperature_is_checked_during_friction(self):
  h=setup(plant={'thermal_capacity_j_k':.01,'heat_loss_w_k':0.},sample={'accept_condition_transfer':False},method={'temperature_tolerance_k':.01});r=run(h);self.assertEqual(r['error_code'],'REFERENCE_CONDITIONS');self.assertEqual(r['phase'],'failed');self.assertGreater(h.friction_energy,0);self.ledger(h)
 def test_35c_cancel_retry_old_operation_cannot_cancel_new(self):
  h=setup();s=h.session();first=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='first')['operation_id'];h.step(.2);r=s.cancel(first,'cancel');second=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='second')['operation_id'];before=state(h);self.assertEqual(s.cancel(first,'cancel'),r);self.assertEqual(state(h),before);self.assertEqual(h.phase,'running')
  with self.assertRaisesRegex(rt.ActionRejected,'REQUEST_CONFLICT'):s.cancel(second,'cancel')
  self.assertEqual(state(h),before)
 def test_36_actual_all_api_outputs_and_state_schema(self):
  h=setup();call(h,'sim.step',dt_s=.1);call(h,'acquisition.start');h.step(.3);call(h,'acquisition.stop');access(h);call(h,'motion.home');h.step(4.);call(h,'thermal.set_temperature',temperature_k=296.15);call(h,'sensor.calibrate',**rt.CAPS['sensor.calibrate']['example_arguments']);call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);call(h,'analysis.hertz',**rt.CAPS['analysis.hertz']['example_arguments']);call(h,'analysis.wear_profile',**rt.CAPS['analysis.wear_profile']['example_arguments']);call(h,'emergency.stop');call(h,'emergency.reset');cleanup(h)
  h=setup(lubricant={});cleanup(h);call(h,'ledger.read');call(h,'acquisition.result');meta=rt.export_antonpaar_trb_contract_metadata();seen={k for k,a,r in OBSERVED};self.assertEqual(set(rt.CAPS)-set(rt.UNSUPPORTED)-seen,set())
  p=ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py';sp=importlib.util.spec_from_file_location('trb_asset_tool',p);m=importlib.util.module_from_spec(sp);sys.modules[sp.name]=m;sp.loader.exec_module(m)
  for k,a,r in OBSERVED:
   errors=m.validate_value(a,meta['capabilities'][k]['input_schema'],path=k+'.input')+m.validate_value(r,meta['capabilities'][k]['output_schema'],path=k+'.output');self.assertEqual(errors,[],k)
  for snapshot in STATES:
   self.assertEqual(set(snapshot),set(meta['state_slots']))
   for k,v in snapshot.items():
    errors=m.validate_value(v,meta['state_slots'][k]['value_schema'],path=k);self.assertEqual(errors,[],k)
  for k,c in meta['capabilities'].items():
   errors=m.lint_schema(c['input_schema'],path=k+'.input')+m.lint_schema(c['output_schema'],path=k+'.output');self.assertEqual(errors,[],k)
   for e in c['state_effects']:self.assertIn(e['state_id'],meta['state_slots'])

if __name__=='__main__':unittest.main()
