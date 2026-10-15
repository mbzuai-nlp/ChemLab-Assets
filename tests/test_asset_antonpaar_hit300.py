"""CHEM130 empirical indentation, finite work, OP observation and session tests."""
import copy,hashlib,importlib.util,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import antonpaar_hit300 as rt
OBSERVED=[];STATES=[]
def canonical(v):
 if isinstance(v,dict):return {str(k):canonical(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [canonical(x) for x in v]
 if isinstance(v,np.ndarray):return v.tolist()
 return v
def state(h):return json.dumps(canonical(h.__dict__),sort_keys=True,allow_nan=False)
def call(h,k,**a):
 before=h.read();r=h.invoke(k,**a);after=h.read();json.dumps(r,allow_nan=False);json.dumps(after,allow_nan=False);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));STATES.append(after)
 changed={x for x in before if before[x]!=after[x]}
 if not changed<=set(rt.CAPS[k]['effects']):raise AssertionError((k,'undeclared state effects',changed-set(rt.CAPS[k]['effects'])))
 return r
def step(h,t):h.step(t);STATES.append(h.read());json.dumps(h.read(),allow_nan=False)
def setup(name='hit_test',plant=None,sample=None,method=None,calibration=None):
 h=rt.AntonpaarHit300Runtime(name)
 for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,**(plant or {}))),('specimen.load',dict(rt.SAMPLE_EXAMPLE,**(sample or {}))),('power.set',{'enabled':True}),('clamp.set',{'locked':True}),('stage.set_y',{'position_m':.02,'accept_manual':True}),('stage.move_x',{'position_m':.02,'speed_m_s':.005}),('sensor.zero',{}),('sensor.calibrate',dict(rt.CALIBRATION_EXAMPLE,**(calibration or {}))),('head.set_position',{'distance_m':0.})]:call(h,k,**a)
 step(h,9.);call(h,'contact.set',engaged=True);call(h,'method.configure',**dict(dict(rt.METHOD_EXAMPLE,x_positions_m=[.02],hold_s=.2),**(method or {})));return h
def run(h):
 call(h,'acquisition.start')
 for _ in range(4000):
  step(h,.1)
  if h.phase!='running':break
 if h.phase=='running':raise AssertionError('no finite terminal state')
 return call(h,'acquisition.result')
def park(h):
 if h.phase=='running':call(h,'acquisition.stop')
 step(h,3.);call(h,'contact.set',engaged=False);call(h,'head.set_position',distance_m=.09);step(h,9.)
def cleanup(h):park(h);call(h,'clamp.set',locked=False);call(h,'specimen.remove');call(h,'power.set',enabled=False)
def fixture(m=1.5,frame=0.,n=101):
 x=np.linspace(1.,.05,n);p=.005*x**m;h=1e-7+1e-7*x+frame*p;return p.tolist(),h.tolist()
class TestHIT300(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=state(h)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,state(h))
 def ledger(self,h):
  l=call(h,'ledger.read');self.assertLess(abs(l['sample_error_kg']),1e-13);self.assertLess(abs(l['volume_error_m3']),1e-19);self.assertLess(abs(l['energy_error_j']),1e-19);self.assertLess(abs(l['thermal_error_j']),1e-19);self.assertGreaterEqual(l['pristine_volume_m3'],0.);self.assertAlmostEqual(l['plastic_work_j'],l['heat_input_j']+l['defect_stored_j'],places=20);return l
 def test_01_raw_source_time_padding_and_branch_preservation(self):
  r=rt.library()['substances']['borofloat33_5mn_rep1'];original=json.dumps(r,sort_keys=True);m=rt.source_model(r,True);self.assertEqual(len(r['values']['load_n']),652);self.assertIsNone(r['values']['time_s']);self.assertEqual(m['projection']['padding_nodes_excluded'],540);self.assertEqual(m['projection']['active_last_index'],111);self.assertGreater(m['projection']['elastic_release_below_observed_min_load_n'],0);self.assertEqual(original,json.dumps(r,sort_keys=True));self.assertGreater(m['maximum_load'],.005);self.assertTrue(np.all(np.diff(m['plastic'])>=0))
 def test_02_two_real_materials_drive_different_observed_outputs(self):
  moduli=[]
  for rid in ['borofloat33_5mn_rep1','soda_lime_glass_5mn_rep1']:
   h=setup(sample={'reference_id':rid});r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(r['completed_indents'],1);self.assertIsNotNone(r['points'][0]['analysis']);self.assertEqual(r['reference_id'],rid);moduli.append(r['points'][0]['analysis']['sample_modulus_pa']);self.ledger(h)
  self.assertNotEqual(*moduli)
 def test_03_full_array_load_release_retract_move_and_history(self):
  h=setup(method={'x_positions_m':[.02,.022]});r=run(h);self.assertEqual(r['completed_indents'],2);self.assertEqual(len(r['points']),2);self.assertEqual(len(h.damage),2);self.assertGreater(h.damage[0]['plastic_depth_m'],0);self.assertEqual(h.stage_y,.02);self.assertAlmostEqual(h.stage_x,.022);phases={x['phase'] for x in r['rows']};self.assertTrue({'loading','holding','unloading','release','retracting','moving','approaching'}<=phases);self.assertEqual(h.force,0.);self.ledger(h)
 def test_04_actual_load_is_not_target_and_read_never_steps(self):
  h=setup();call(h,'acquisition.start');self.assertEqual(h.force,0.);before=state(h)
  for _ in range(3):h.read();call(h,'acquisition.result');h.describe()
  self.assertEqual(state(h),before);step(h,.1);self.assertGreater(h.force,0.);self.assertLess(h.force,h.force_target);self.assertAlmostEqual(h.force_target,.1*h.method['loading_rate_n_s']);self.assertGreater(h.plastic_depth,0.)
 def test_05_op_analytic_powerlaw_hardness_and_reduced_vs_sample_modulus(self):
  for exponent in [1.2,1.5,2.,2.8]:
   p,h=fixture(exponent);c=rt.CALIBRATION_EXAMPLE;r=rt.oliver_pharr(p,h,c);s=.005*exponent/1e-7;hc=2e-7-c['epsilon']*.005/s;area=24.5*hc*hc;er=math.sqrt(math.pi)*s/(2*c['beta']*math.sqrt(area));e=(1-c['poisson_sample']**2)/(1/er-(1-c['poisson_tip']**2)/c['young_tip_pa']);self.assertAlmostEqual(r['unloading_exponent'],exponent,delta=1e-6);self.assertAlmostEqual(r['hardness_pa'],.005/area,delta=1000);self.assertAlmostEqual(r['sample_modulus_pa'],e,delta=10000);self.assertNotEqual(r['sample_modulus_pa'],r['reduced_modulus_pa'])
 def test_06_frame_compliance_corrected_once(self):
  p,h=fixture(frame=3e-7);base=rt.oliver_pharr(*fixture(),rt.CALIBRATION_EXAMPLE);good=rt.oliver_pharr(p,h,dict(rt.CALIBRATION_EXAMPLE,frame_compliance_m_n=3e-7));bad=rt.oliver_pharr(p,h,rt.CALIBRATION_EXAMPLE);self.assertAlmostEqual(good['stiffness_n_m'],base['stiffness_n_m'],delta=.01);self.assertAlmostEqual(good['sample_modulus_pa'],base['sample_modulus_pa'],delta=1000);self.assertGreater(abs(bad['stiffness_n_m']-good['stiffness_n_m']),100)
 def test_07_op_rejects_loading_creep_nose_and_bad_window(self):
  hst=setup();p,h=fixture();a=dict(load_n=p,depth_m=h,calibration=rt.CALIBRATION_EXAMPLE);self.rejected(hst,'analysis.oliver_pharr','NOT_UNLOADING',**dict(a,load_n=p[::-1],depth_m=h[::-1]));nose=h.copy();nose[2]=h[0]*1.1;self.rejected(hst,'analysis.oliver_pharr','UNLOADING_NOSE',**dict(a,depth_m=nose));self.rejected(hst,'analysis.oliver_pharr','FIT_DATA',**dict(a,depth_m=h[:-1]));self.rejected(hst,'analysis.oliver_pharr','CALIBRATION_DOMAIN',**dict(a,calibration=dict(rt.CALIBRATION_EXAMPLE,fit_min_fraction=.8,fit_max_fraction=.5)));self.rejected(hst,'analysis.oliver_pharr','ELASTIC_UNLOADING_NOT_ACCEPTED',**dict(a,calibration=dict(rt.CALIBRATION_EXAMPLE,accept_elastic_unloading=False)))
 def test_08_op_peak_depth_is_paired_not_global_max(self):
  p,h=fixture();h[1]=h[0]*1.001;r=rt.oliver_pharr(p,h,rt.CALIBRATION_EXAMPLE);self.assertEqual(r['max_depth_m'],h[0]);self.assertNotEqual(r['max_depth_m'],max(h))
 def test_09_area_domain_and_tip_poisson_correction_fail_explicitly(self):
  h=setup();p,d=fixture();a=dict(load_n=p,depth_m=d,calibration=dict(rt.CALIBRATION_EXAMPLE,area_min_depth_m=1e-6));self.rejected(h,'analysis.oliver_pharr','CONTACT_AREA_DOMAIN',**a);a['calibration']=dict(rt.CALIBRATION_EXAMPLE,young_tip_pa=1e9);self.rejected(h,'analysis.oliver_pharr','MODULUS_DOMAIN',**a)
 def test_10_live_analysis_uses_only_observed_unloading(self):
  h=setup();r=run(h);rr=[x for x in r['rows'] if x['phase']=='unloading'];external=call(h,'analysis.oliver_pharr',load_n=[x['force_n'] for x in rr],depth_m=[x['depth_m'] for x in rr],calibration=rt.CALIBRATION_EXAMPLE);self.assertEqual(external,r['points'][0]['analysis']);self.assertIn('h_corrected',external['evidence'])
 def test_11_no_fake_success_on_sparse_analysis(self):
  h=setup(method={'sample_interval_s':2.});r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertIsNone(r['points'][0]['analysis']);self.assertIn(r['points'][0]['analysis_error'],['FIT_POINTS','FIT_DATA']);self.assertEqual(r['completed_indents'],1)
 def test_12_known_force_program_exact_and_actual_phase_times(self):
  h=setup(method={'hold_s':1.});r=run(h);phases={k:[x for x in r['rows'] if x['phase']==k] for k in ['loading','holding','unloading','release']};self.assertAlmostEqual(phases['holding'][0]['time_s']-phases['loading'][0]['time_s'],3.,places=9);self.assertAlmostEqual(phases['unloading'][0]['time_s']-phases['holding'][0]['time_s'],1.,places=9);self.assertAlmostEqual(phases['release'][0]['time_s']-phases['unloading'][0]['time_s'],3.,places=9);self.assertGreater(r['elapsed_s'],7.);self.assertAlmostEqual(r['rows'][-1]['elapsed_s'],r['elapsed_s'])
 def test_13_piecewise_plastic_work_matches_independent_source_integral(self):
  h=setup();r=run(h);pmax=h.method['peak_load_n'];p=h.model['load'];hp=h.model['plastic'];grid=np.r_[0.,p[(p>0)&(p<pmax)],pmax];plastic=np.interp(grid,p,hp);expected=float(np.sum(.5*(grid[1:]+grid[:-1])*np.diff(plastic)));l=self.ledger(h);self.assertAlmostEqual(l['plastic_work_j'],expected,delta=1e-19);self.assertAlmostEqual(h.plastic_depth,float(np.interp(pmax,p,hp)),places=17);m=h.model['exponent'];elastic=pmax*(pmax/h.model['coefficient'])**(1/m)/(m+1);self.assertAlmostEqual(l['elastic_recovered_j'],elastic,delta=1e-17);self.assertEqual(l['elastic_stored_j'],0.)
 def test_14_finite_mass_persistent_deformation_and_recovery(self):
  h=setup();mass=h._ledger()['sample_present_kg'];run(h);l=self.ledger(h);self.assertEqual(l['sample_present_kg'],mass);self.assertGreater(l['deformed_volume_m3'],0);self.assertGreater(l['pristine_volume_m3'],0);self.assertAlmostEqual(l['deformed_volume_m3'],h.specimen['deformation_shape_factor']*h.specimen['deformation_area_c2']*h.plastic_depth**3,delta=1e-32);cleanup(h);l=self.ledger(h);self.assertEqual(l['sample_present_kg'],0.);self.assertEqual(l['sample_recovered_kg'],mass);self.assertEqual(l['deformed_volume_m3'],0.)
 def test_15_reject_same_position_overlap_and_allow_new_manual_y(self):
  h=setup();run(h);self.rejected(h,'acquisition.start','POSITION_ALREADY_DAMAGED');park(h);call(h,'stage.set_y',position_m=.021,accept_manual=True);call(h,'head.set_position',distance_m=0.);step(h,9.);call(h,'contact.set',engaged=True);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(h.damage),2)
  h=setup(method={'x_positions_m':[.02,.02001]});self.rejected(h,'acquisition.start','ARRAY_OVERLAP')
 def test_16_atomic_hardware_limits_and_sampling_grid(self):
  h=setup()
  for field,v in [('peak_load_n',.500001),('peak_load_n',0.),('x_positions_m',[.04001]),('sample_interval_s',.15),('sample_interval_s',.16)]:self.rejected(h,'method.configure',**dict(h.method,**{field:v}))
  for dt in [True,-.1,float('nan'),float('inf')]:self.rejected(h,'sim.step',dt_s=dt)
  self.rejected(h,'stage.move_x',position_m=.05,speed_m_s=.01);self.rejected(h,'head.set_position',distance_m=.09001);self.rejected(h,'power.set',enabled=1);self.rejected(h,'method.configure',**dict(h.method,unexpected=1))
 def test_17_source_load_range_unknown_conditions_and_explicit_transfer(self):
  h=setup(method={'peak_load_n':.01});self.rejected(h,'acquisition.start','REFERENCE_LOAD_DOMAIN');h=setup(sample={'accept_unknown_conditions':False});self.rejected(h,'acquisition.start','UNKNOWN_REFERENCE_CONDITIONS');h=setup(sample={'accept_condition_transfer':False},method={'hold_s':5.,'peak_load_n':.002});self.rejected(h,'acquisition.start','REFERENCE_CONDITIONS');h=rt.AntonpaarHit300Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);self.rejected(h,'specimen.load','MODEL_NOT_ACCEPTED',**dict(rt.SAMPLE_EXAMPLE,accept_elastic_release_extrapolation=False))
 def test_18_dynamic_strain_rate_hold_and_noise_unsupported(self):
  h=setup()
  for key,v in [('mode','dynamic'),('control','strain_rate')]:self.rejected(h,'method.configure','UNSUPPORTED',**dict(h.method,**{key:v}))
  self.rejected(h,'method.configure','HOLD_MODEL_NOT_ACCEPTED',**dict(h.method,accept_no_creep_hold=False))
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
 def test_19_manual_y_requires_clearance_acceptance_and_no_automatic_y(self):
  h=setup();self.rejected(h,'stage.set_y','HEAD_NOT_CLEAR',position_m=.01,accept_manual=True);park(h);self.rejected(h,'stage.set_y','MANUAL_ACTION_NOT_ACCEPTED',position_m=.01,accept_manual=False);t=h.time_s;call(h,'stage.set_y',position_m=.01,accept_manual=True);self.assertEqual(h.time_s,t);self.assertEqual(h.stage_y,.01);call(h,'stage.move_x',position_m=.03,speed_m_s=.005);self.assertEqual(h.stage_x,.02);step(h,1.);self.assertAlmostEqual(h.stage_x,.025);self.assertEqual(h.stage_y,.01)
 def test_20_common_reference_ring_drift_cancels_not_differential(self):
  a=setup();b=setup(plant={'common_drift_m_s':5e-9});c=setup(plant={'differential_drift_m_s':5e-9});ra=run(a);rb=run(b);rc=run(c)
  for x,y in zip(ra['rows'],rb['rows']):
   if x['depth_m'] is not None:self.assertAlmostEqual(x['depth_m'],y['depth_m'],places=19)
  self.assertNotEqual(ra['rows'][-1]['tip_indicated_m'],rb['rows'][-1]['tip_indicated_m']);self.assertGreater(abs(rc['points'][0]['analysis']['hardness_pa']-ra['points'][0]['analysis']['hardness_pa']),1e8)
 def test_21_frame_compliance_and_sensor_gain_change_observations(self):
  a=setup();b=setup(plant={'frame_compliance_m_n':3e-7},calibration={'frame_compliance_m_n':3e-7});c=setup(plant={'force_gain':.9,'depth_gain':1.03});ra=run(a)['points'][0]['analysis'];rb=run(b)['points'][0]['analysis'];rc=run(c)['points'][0]['analysis'];self.assertLess(abs(rb['hardness_pa']/ra['hardness_pa']-1),.002);self.assertGreater(abs(rc['hardness_pa']/ra['hardness_pa']-1),.05);self.assertAlmostEqual(a.plastic_work,b.plastic_work,places=20);self.assertAlmostEqual(a.plastic_work,c.plastic_work,places=20)
 def test_22_external_sensor_zero_no_free_truth(self):
  h=setup(plant={'force_offset_n':1e-5,'depth_offset_m':2e-9});park(h);call(h,'sensor.zero');self.assertAlmostEqual(h.calibration['force_zero_n'],1e-5,places=14);self.assertAlmostEqual(h.calibration['depth_zero_m'],2e-9,places=16);self.assertEqual(h.read()['depth.indicated_m'],None);self.assertNotIn('sample_modulus_pa',h.__dict__)
 def test_23_nanowatt_heat_stable_energy_and_defect_partition(self):
  h=setup(plant={'heat_loss_w_k':0.,'plastic_heat_fraction':.7});run(h);l=self.ledger(h);self.assertGreater(l['thermal_stored_j'],1e-11);self.assertAlmostEqual(l['thermal_stored_j'],.7*l['plastic_work_j'],delta=1e-23);self.assertAlmostEqual(l['defect_stored_j'],.3*l['plastic_work_j'],delta=1e-23)
  h=setup(plant={'thermal_capacity_j_k':1e-6,'heat_loss_w_k':1000.});run(h);self.assertGreaterEqual(h.temperature,296.15);self.assertLess(h.temperature,296.15001);self.ledger(h)
 def test_24_passive_cooling_tracks_nanojoules_without_absolute_T_loss(self):
  h=setup();run(h);e=h.thermal_excess_j;step(h,10.);self.assertAlmostEqual(h.thermal_excess_j,e*math.exp(-.1),delta=1e-23);self.ledger(h)
 def test_25_finite_thickness_failure_and_no_material_deletion(self):
  h=setup(sample={'thickness_m':1e-8});r=run(h);self.assertEqual(r['error_code'],'SPECIMEN_THICKNESS');self.assertLess(r['elapsed_s'],3.);self.assertLessEqual(h._depth(),1e-8+1e-16);self.assertEqual(r['completed_indents'],0);self.assertEqual(r['rows'][-1]['elapsed_s'],r['elapsed_s']);self.ledger(h)
 def test_26_force_sensor_overrange_and_tiny_thermal_domain(self):
  h=setup(plant={'depth_gain':10.,'depth_offset_m':.000199});r=run(h);self.assertEqual(r['error_code'],'SENSOR_OVER_RANGE');self.assertIsNone(r['rows'][-1]['depth_m']);self.ledger(h)
  h=setup(plant={'thermal_capacity_j_k':1e-6,'heat_loss_w_k':0.,'temperature_max_k':296.150001});r=run(h);self.assertEqual(r['error_code'],'THERMAL_MODEL_RANGE');self.assertLessEqual(h.temperature,296.150001);self.ledger(h)
 def test_27_cancel_partial_array_keeps_damage_and_force(self):
  h=setup(method={'x_positions_m':[.02,.022]});call(h,'acquisition.start');step(h,7.);self.assertEqual(h.completed,1);step(h,2.);call(h,'acquisition.stop');r=call(h,'acquisition.result');self.assertEqual(r['completed_indents'],1);self.assertEqual(r['points'][-1]['phase'],'cancelled');force=h.force;depth=h.damage[-1]['plastic_depth_m'];self.assertGreater(force,0);self.rejected(h,'contact.set','LOAD_NOT_RELEASED',engaged=False);step(h,3.);self.assertEqual(h.force,0.);self.assertEqual(h.damage[-1]['plastic_depth_m'],depth);self.assertEqual(r,call(h,'acquisition.result'));self.ledger(h)
 def test_28_emergency_timeout_and_explicit_no_auto_resume(self):
  h=setup();call(h,'acquisition.start');step(h,.5);call(h,'emergency.stop');self.assertEqual(h.error,'SOFTWARE_STOP');self.rejected(h,'emergency.reset','LOAD_NOT_RELEASED');step(h,3.);call(h,'emergency.reset');self.assertEqual(h.phase,'failed');self.ledger(h)
  h=setup(method={'timeout_s':.2});r=run(h);self.assertEqual(r['error_code'],'TIMEOUT');self.assertAlmostEqual(r['elapsed_s'],.2)
 def test_29_array_reject_prevalidated_later_damage_and_overlap(self):
  h=setup();run(h);park(h);call(h,'stage.move_x',position_m=.022,speed_m_s=.005);step(h,1.);call(h,'head.set_position',distance_m=0.);step(h,9.);call(h,'contact.set',engaged=True);call(h,'method.configure',**dict(h.method,x_positions_m=[.022,.02]));self.rejected(h,'acquisition.start','POSITION_ALREADY_DAMAGED');self.assertEqual(len(h.damage),1)
 def test_30_frozen_result_survives_sample_calibration_and_method(self):
  h=setup();r=run(h);op=r['operation_id'];cleanup(h);call(h,'sensor.calibrate',**dict(rt.CALIBRATION_EXAMPLE,area_c2=30.));call(h,'method.configure',**dict(h.method,peak_load_n=.001));call(h,'specimen.load',**dict(rt.SAMPLE_EXAMPLE,reference_id='soda_lime_glass_5mn_rep1'));self.assertEqual(r,call(h,'acquisition.result'));self.assertEqual(r,h.records[op]);self.assertEqual(r['reference_id'],'borofloat33_5mn_rep1')
 def test_31_pure_queries_and_owner_not_stolen(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];step(h,.3);before=state(h);h.read();h.describe();s.read(instance_id=h.instance_id);s.poll(op);call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);call(h,'ledger.read');self.assertEqual(state(h),before);q=s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='result');self.assertIsNone(q['operation_id']);self.assertEqual(h._owners[op],'agent');json.dumps(q,allow_nan=False)
 def test_32_session_identity_owner_and_request_idempotence(self):
  h=setup();h.register_principal('other');a=h.session();b=h.session('other');args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=a.invoke(**args);self.assertEqual(r,a.invoke(**args));op=r['operation_id']
  for k in ['acquisition.result','acquisition.stop']:self.assertEqual(b.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k)['error']['code'],'ACCESS_DENIED')
  for fn in [lambda:b.poll(op),lambda:b.cancel(op,'cancel')]:
   with self.assertRaises(rt.ActionRejected):fn()
  self.assertEqual(a.invoke(**dict(args,capability_id='power.set',arguments={'enabled':False}))['error']['code'],'REQUEST_CONFLICT');c=a.cancel(op,'cancel');self.assertEqual(c,a.cancel(op,'cancel'))
 def test_33_stale_cancel_retry_keeps_new_operation_running(self):
  h=setup();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');op=s.invoke(**a)['operation_id'];c=s.cancel(op,'cancel');step(h,.1);op2=s.invoke(**dict(a,request_id='two'))['operation_id'];self.assertEqual(c,s.cancel(op,'cancel'));self.assertEqual(h.phase,'running');self.assertEqual(h.operation_id,op2)
  with self.assertRaisesRegex(rt.ActionRejected,'REQUEST_CONFLICT'):s.cancel(op2,'cancel')
 def test_34_agent_truth_recovery_and_writes_denied(self):
  h=setup();s=h.session();meta=s.describe(instance_id=h.instance_id);self.assertTrue(all(not k.startswith('truth.') for k in meta['state_slots']));self.assertNotIn('specimen.remove',meta['capabilities']);self.assertNotIn('ledger.read',meta['capabilities']);self.assertTrue(all(not k.startswith('truth.') for k in s.read(instance_id=h.instance_id)['observations']))
  for k in ['specimen.remove','ledger.read','plant.configure','sim.step']:
   before=state(h);r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=rt.CAPS[k]['example_arguments'],request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,state(h))
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.plastic_depth_m'])
  with self.assertRaises(rt.ActionRejected):s.write(instance_id=h.instance_id,state_id='force.target_n',value=.4,request_id='x')
 def test_35_two_instances_fractional_and_large_host_steps(self):
  a=setup('a');b=setup('b');call(a,'acquisition.start');call(b,'acquisition.start');step(a,1.3)
  for dt in [.03,.07,.25,.15,.4,.4]:step(b,dt)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);before=state(b);step(a,.1);self.assertEqual(before,state(b));call(a,'sim.step',dt_s=0.)
  a=setup('a',plant={'force_response_tau_s':1e-5,'sensor_tau_s':1e-5});b=setup('b',plant={'force_response_tau_s':1e-5,'sensor_tau_s':1e-5});call(a,'acquisition.start');call(b,'acquisition.start');step(a,8.)
  for _ in range(80):step(b,.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);self.assertEqual(a.phase,'succeeded')
 def test_36_reference_only_and_projection_acceptance(self):
  h=setup();park(h);call(h,'clamp.set',locked=False);call(h,'specimen.remove');records=rt.library()['substances'];rid=next(k for k,r in records.items() if not r.get('runtime_usable'));self.rejected(h,'specimen.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id=rid));self.rejected(h,'reference.query','INVALID_REFERENCE',reference_id='missing');r=copy.deepcopy(records[rt.SAMPLE_EXAMPLE['reference_id']]);r['values']['depth_m'][5]=-1e-9
  with self.assertRaisesRegex(rt.ActionRejected,'PROJECTION_NOT_ACCEPTED'):rt.source_model(r,False)
  self.assertGreater(rt.source_model(r,True)['projection']['plastic_projection_nodes'],0)
 def test_37_no_agent_hidden_elastic_or_deformation_result_fields(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s')['operation_id'];step(h,8.);r=s.poll(op);json.dumps(r,allow_nan=False)
  def visit(v):
   if isinstance(v,dict):
    for k,x in v.items():self.assertFalse(k.startswith('truth.') or k in ['plastic_depth_m','elastic_depth_m','deformed_volume_m3','sample_present_kg']);visit(x)
   elif isinstance(v,list):
    for x in v:visit(x)
  visit(r);visit(s.read(instance_id=h.instance_id))
 def test_38_laser_power_interlock_and_protocol_errors(self):
  h=setup();call(h,'laser.set',enabled=True);park(h);call(h,'power.set',enabled=False);self.assertFalse(h.laser);self.rejected(h,'laser.set','NOT_READY',enabled=True);s=h.session();self.assertEqual(s.describe(asset_type_id=rt.ASSET_TYPE_ID)['asset_type_id'],rt.ASSET_TYPE_ID)
  for fn in [lambda:s.describe(),lambda:h.session('unknown'),lambda:h.register_principal('agent'),lambda:s.read(instance_id='unknown')]:
   with self.assertRaises(rt.ActionRejected):fn()
  for cap,args in [('unknown',{}),('power.set',{'enabled':3}),('dynamic.acquire',{})]:json.dumps(s.invoke(instance_id=h.instance_id,capability_id=cap,arguments=args,request_id=cap),allow_nan=False)
 def test_39_analysis_incomplete_cancel_and_history_owner(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s')['operation_id'];step(h,1.);s.cancel(op,'cancel');result=s.poll(op);self.assertEqual(result['result']['completed_indents'],0);self.assertIsNone(result['result']['points'][0]['analysis']);self.assertEqual(result['result']['points'][0]['analysis_error'],'INCOMPLETE_UNLOADING');cleanup(h);self.assertEqual(s.poll(op),result);self.ledger(h)
 def test_40_zero_thermal_margin_rejects_sub_ulp_heat(self):
  h=setup(plant={'temperature_max_k':296.15});r=run(h);self.assertEqual(r['error_code'],'THERMAL_MODEL_RANGE');self.assertEqual(h.thermal_excess_j,0.);self.assertEqual(h.plastic_work,0.);self.assertEqual(r['completed_indents'],0);self.ledger(h)
 def test_41_synthesized_scalar_records_reproduce_source_H_Er_and_are_labelled(self):
  lib=rt.library()['substances'];synth=[k for k,r in lib.items() if r.get('runtime_usable') and r.get('runtime_basis')==rt.SYNTHESIS_BASIS]
  self.assertGreaterEqual(len(synth),5)
  # one record per material class (native 316L plus every open-access class present), deterministic order
  picks={};[picks.setdefault(lib[k].get('material_class') or lib[k]['material_identity'],k) for k in sorted(synth)]
  seen=set()
  for cls,rid in sorted(picks.items()):
   r=lib[rid];s=r['runtime_scalars'];peak=min(.005,s['load_domain_max_n'])
   h=setup(sample={'reference_id':rid},method={'peak_load_n':peak});res=run(h);a=res['points'][0]['analysis']
   self.assertEqual(res['phase'],'succeeded',rid);self.assertIsNotNone(a,rid)
   self.assertAlmostEqual(a['hardness_pa']/s['hardness_pa'],1.,delta=.03,msg=rid);self.assertAlmostEqual(a['reduced_modulus_pa']/s['reduced_modulus_pa'],1.,delta=.03,msg=rid)
   proj=json.loads(res['projection_json']);self.assertEqual(proj['basis'],rt.SYNTHESIS_BASIS);self.assertTrue(proj['synthesized_curve']);self.assertFalse(proj['measured_curve']);self.assertAlmostEqual(proj['synthesis_peak_load_n'],peak)
   self.assertTrue(res['evidence'].startswith('SYNTHESIZED'));self.assertIsNone(r['values']['load_n']);self.assertIsNone(r['values']['depth_m'])
   seen.add(round(a['reduced_modulus_pa']/1e9,1));self.ledger(h)
  self.assertEqual(len(seen),len(picks))
 def test_42_synthesis_requires_projection_acceptance_and_source_load_domain(self):
  lib=rt.library()['substances'];rid='hit300_316l_h1_mean';r=lib[rid]
  h=rt.AntonpaarHit300Runtime('synth_reject');call(h,'plant.configure',**rt.PLANT_EXAMPLE)
  self.rejected(h,'specimen.load','PROJECTION_NOT_ACCEPTED',**dict(rt.SAMPLE_EXAMPLE,reference_id=rid,accept_projection=False))
  with self.assertRaisesRegex(rt.ActionRejected,'REFERENCE_LOAD_DOMAIN'):rt.synthesis_model(r,True,r['runtime_scalars']['load_domain_max_n']*1.01)
  h=setup(sample={'reference_id':rid},method={'peak_load_n':.11});self.rejected(h,'acquisition.start','REFERENCE_LOAD_DOMAIN')
  bad=copy.deepcopy(r);bad['runtime_scalars']['unloading_exponent']=2.5
  with self.assertRaisesRegex(rt.ActionRejected,'INVALID_REFERENCE'):rt.synthesis_model(bad,True)
  # peak load sets the synthesis point; two peaks give the same H/Er (self-similar ideal Berkovich)
  out=[]
  for peak in [.002,.02]:
   h=setup('peak'+str(peak),sample={'reference_id':rid},method={'peak_load_n':peak,'loading_rate_n_s':peak/3,'unloading_rate_n_s':peak/3});a=run(h)['points'][0]['analysis'];out.append((a['hardness_pa'],a['reduced_modulus_pa'],a['max_depth_m']))
  self.assertAlmostEqual(out[0][0]/out[1][0],1.,delta=.02);self.assertAlmostEqual(out[0][1]/out[1][1],1.,delta=.02);self.assertAlmostEqual(out[1][2]/out[0][2],10**.5,delta=.05)
 def test_43_selected_measured_replicates_cover_all_brittle_materials(self):
  lib=rt.library()['substances'];measured={}
  for k,r in lib.items():
   if r.get('runtime_usable') and r.get('runtime_basis')!=rt.SYNTHESIS_BASIS:measured.setdefault(r['material_identity'],[]).append(k)
  self.assertEqual(set(measured),{'alumina','boron_carbide','silicon_carbide','granite','borofloat33','soda_lime_glass'})
  moduli={}
  for mat in ['alumina','boron_carbide','silicon_carbide','granite']:
   rid=sorted(measured[mat],key=lambda k:lib[k]['conditions']['source_group'])[0];r=lib[rid];d=r['runtime_domain']
   self.assertIn('selection_rule',d);self.assertIn(rid,d['valid_replicates']);self.assertEqual(rt.source_model(r,False)['projection']['plastic_projection_nodes'],0)
   h=setup(sample={'reference_id':rid},method={'peak_load_n':min(.004,r['conditions']['peak_load_n']*.9)});res=run(h);self.assertEqual(res['phase'],'succeeded',rid);self.assertNotIn('SYNTHESIZED',res['evidence']);moduli[mat]=res['points'][0]['analysis']['reduced_modulus_pa'];self.ledger(h)
  self.assertEqual(len({round(v/1e9) for v in moduli.values()}),4)
 def test_44_library_coverage_is_counted_not_padded(self):
  lib=rt.library();cov=lib['coverage'];subs=lib['substances']
  mats={r['material_identity'] for r in subs.values() if r.get('identity_count_eligible',True)};run_mats={r['material_identity'] for r in subs.values() if r['runtime_usable'] and r.get('identity_count_eligible',True)}
  self.assertEqual(cov['distinct_reference_materials'],len(mats));self.assertEqual(cov['runtime_materials'],len(run_mats));self.assertEqual(cov['minimum_50_runtime_materials_met'],len(run_mats)>=50)
  for r in subs.values():
   if r['kind']=='published_scalar_hardness_modulus':
    self.assertIn(r['evidence'],['measured_literature','derived']);self.assertTrue(r['source']['sha256']);self.assertTrue(r['source']['verbatim_snippet']);self.assertIsNone(r['values']['load_n'])
    self.assertTrue(r['runtime_usable'] or r.get('runtime_block_reason'))
  self.assertFalse(subs['p3ht_20240118_constant_loading_rate']['runtime_usable']);self.assertFalse(any(r['runtime_usable'] for r in subs.values() if r['kind']=='author_processed_indentation_scalar_series'))
 def test_zz_real_inputs_outputs_states_metadata_and_effects(self):
  spec=importlib.util.spec_from_file_location('hit_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_antonpaar_hit300_contract_metadata();json.dumps(meta,allow_nan=False)
  for k,c in meta['capabilities'].items():self.assertEqual(tool.lint_schema(c['input_schema'],path=k+'.input'),[]);self.assertEqual(tool.lint_schema(c['output_schema'],path=k+'.output'),[])
  covered=set()
  for k,a,r in OBSERVED:
   self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema'],path=k+'.input'),[]);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema'],path=k+'.output'),[]);covered.add(k)
  self.assertEqual(covered,set(rt.CAPS)-set(rt.UNSUPPORTED))
  for values in STATES:
   self.assertEqual(set(values),set(rt.SLOTS));json.dumps(values,allow_nan=False)
   for k,v in values.items():self.assertEqual(tool.validate_value(v,meta['state_slots'][k]['value_schema'],path=k),[])
  for k,c in meta['capabilities'].items():self.assertTrue(set(rt.CAPS[k]['effects'])<={e['state_id'] for e in c['state_effects']})
if __name__=='__main__':unittest.main()
