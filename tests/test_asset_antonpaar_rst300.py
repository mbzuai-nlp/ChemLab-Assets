"""CHEM129 finite scratch, observed pre/post profiles and protocol regressions."""
import copy,hashlib,importlib.util,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import antonpaar_rst300 as rt
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
def setup(name='rst_test',plant=None,sample=None,stylus=None,method=None):
 h=rt.AntonpaarRst300Runtime(name)
 for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,**(plant or {}))),('specimen.load',dict(rt.SAMPLE_EXAMPLE,**(sample or {}))),('stylus.load',dict(rt.STYLUS_EXAMPLE,**(stylus or {})))]:call(h,k,**a)
 call(h,'power.set',enabled=True);call(h,'clamp.set',locked=True);call(h,'sensor.zero');call(h,'head.set_position',distance_m=0.);step(h,4.);call(h,'contact.set',engaged=True);call(h,'method.configure',**dict(dict(rt.METHOD_EXAMPLE,length_m=.0002),**(method or {})));return h
def run(h,kind=None):
 if kind is not None:call(h,'method.configure',**dict(h.method,pass_kind=kind))
 call(h,'acquisition.start')
 for _ in range(4000):
  step(h,.1)
  if h.phase!='running':break
 if h.phase=='running':raise AssertionError('operation never terminated')
 return call(h,'acquisition.result')
def park(h,x=0.,y=0.):
 if h.phase=='running':call(h,'acquisition.stop')
 step(h,2.);call(h,'contact.set',engaged=False);call(h,'head.set_position',distance_m=.04);step(h,4.);call(h,'stage.move',x_m=x,y_m=y,speed_m_s=.01);step(h,10.)
def at_start(h):
 park(h,h.method['start_x_m'],h.method['path_y_m']);call(h,'head.set_position',distance_m=0.);step(h,4.);call(h,'contact.set',engaged=True)
def prescanned(**kwargs):
 h=setup(**kwargs);r=run(h,'prescan')
 if r['phase']!='succeeded':raise AssertionError(r['error_code'])
 at_start(h);call(h,'method.configure',**dict(h.method,pass_kind='scratch'));return h
def cleanup(h):
 park(h);call(h,'clamp.set',locked=False)
 for name in ['specimen','stylus']:
  if getattr(h,name) is not None:call(h,name+'.remove')
 call(h,'debris.collect');call(h,'power.set',enabled=False)
def rows(values,channel='friction_force_n',loads=None):
 result=[]
 for i,v in enumerate(values):
  r=copy.deepcopy(rt._example_row);r.update(distance_m=i*.001,stage_x_m=i*.001,normal_force_n=(loads[i] if loads else 10.+i));r[channel]=v;result.append(r)
 return result
class TestRST300(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=state(h)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,state(h))
 def ledger(self,h):
  l=call(h,'ledger.read')
  for n in ['coating','substrate','stylus']:self.assertLess(abs(l[n+'_error_kg']),1e-13)
  self.assertLess(abs(l['thermal_error_j']),1e-8);self.assertLess(abs(l['normal_error_j']),1e-12);self.assertAlmostEqual(l['heat_input_j']+l['wear_work_j'],l['tangential_work_j'],places=12);return l
 def test_01_raw_reference_command_load_negative_force_and_null_residual(self):
  lib=rt.library();r=lib['substances']['borofloat33_10n_r1'];before=json.dumps(r,sort_keys=True);x,mu,count=rt.curve(r,True)
  self.assertEqual(len(x),1763);self.assertGreater(count,0);self.assertLess(r['values']['tangential_force_n'][0],0);self.assertIsNone(r['values']['residual_depth_m']);self.assertTrue(all(v==0 for v in r['values']['raw_residual_depth_m']));self.assertAlmostEqual(r['values']['normal_force_n'][0],9.99999938964844);self.assertGreater(x[0],0);self.assertTrue(np.all(mu>=0));self.assertEqual(before,json.dumps(r,sort_keys=True));self.assertGreaterEqual(sum(bool(v.get('runtime_usable')) for v in lib['substances'].values()),16)
  with self.assertRaisesRegex(rt.ActionRejected,'NEGATIVE_SOURCE_FORCE'):rt.curve(r)
 def test_02_three_real_passes_residual_not_penetration(self):
  h=prescanned(method={'length_m':.002});scratch=run(h);self.assertEqual(scratch['phase'],'succeeded');s=[r for r in scratch['rows'] if r['phase']=='scanning'];self.assertTrue(all(r['residual_depth_m'] is None for r in s));self.assertGreater(s[-1]['penetration_depth_m'],h.local_groove);at_start(h);post=run(h,'postscan');p=[r for r in post['rows'] if r['phase']=='scanning'];self.assertEqual(post['phase'],'succeeded');self.assertTrue(all(r['penetration_depth_m'] is None for r in p));self.assertGreater(p[-1]['residual_depth_m'],0);self.assertLess(abs(p[-1]['residual_depth_m']-h.local_groove),1e-9);self.ledger(h)
 def test_03_force_ramp_endpoint_and_integral_moments(self):
  from scipy.integrate import quad
  for tau in [1e-5,.05,100.]:
   for dt in [.000001,.01,.1,1.]:
    f0,target,slope=3.,8.,2.;got=rt.force_ramp(f0,target,slope,tau,dt);f=lambda t:target+slope*(t-tau)+(f0-target+slope*tau)*math.exp(-t/tau);pts=sorted({v for v in [tau,5*tau,30*tau,60*tau] if v<dt});expected=[f(dt),quad(f,0,dt,points=pts,epsabs=1e-12)[0],quad(lambda t:t*f(t),0,dt,points=pts,epsabs=1e-12)[0]]
    for a,b in zip(got,expected):self.assertAlmostEqual(a,b,delta=max(1e-10,abs(b)*1e-9))
 def test_04_target_is_not_force_and_motion_requires_host_step(self):
  h=setup();call(h,'method.configure',**dict(h.method,pass_kind='prescan'));call(h,'acquisition.start');self.assertEqual(h.force,0.);self.assertEqual(h.distance,0.);self.assertEqual(h.force_target,.1);t=h.time_s
  for _ in range(5):call(h,'acquisition.result');h.read()
  self.assertEqual(h.time_s,t);step(h,.1);self.assertGreater(h.force,0);self.assertLess(h.force,.1);self.assertEqual(h.distance,0.)
 def test_05_friction_work_uses_source_axis_and_actual_force(self):
  from scipy.integrate import quad
  h=prescanned(method={'length_m':.0005,'initial_force_n':2.,'final_force_n':12.});w=h.tangential_work;r=run(h);s=[v for v in r['rows'] if v['phase']=='scanning'];start=s[0]['time_s'];finish=s[-1]['time_s'];duration=finish-start;v=h.method['speed_m_s'];load0=h.plant['force_response_tau_s'];f0=2.-2.*math.exp(-(start-h.operation_start)/load0);slope=10/duration;x0=h.method['source_start_distance_m'];knots=[(x-x0)/v for x in h.ref_x if x0<x<x0+h.method['length_m']]
  f=lambda t:(2.+slope*(t-load0)+(f0-2.+slope*load0)*math.exp(-t/load0))*float(np.interp(x0+v*t,h.ref_x,h.ref_mu))*v
  expected=quad(f,0,duration,points=knots,epsabs=1e-12,limit=2000)[0];self.assertAlmostEqual(h.tangential_work-w,expected,delta=2e-10);self.assertAlmostEqual(r['distance_m'],.0005);self.ledger(h)
 def test_06_source_distance_and_condition_rejections_atomic(self):
  h=prescanned();call(h,'method.configure',**dict(h.method,source_start_distance_m=0.));self.rejected(h,'acquisition.start','REFERENCE_DISTANCE_RANGE');call(h,'method.configure',**dict(h.method,source_start_distance_m=float(h.ref_x[-1])-.00001));self.rejected(h,'acquisition.start','REFERENCE_DISTANCE_RANGE')
  h=prescanned(sample={'accept_condition_transfer':False},stylus={'cone_angle_rad':math.pi/2});self.rejected(h,'acquisition.start','REFERENCE_CONDITIONS')
  h=prescanned(sample={'accept_unknown_conditions':False});self.rejected(h,'acquisition.start','UNKNOWN_REFERENCE_CONDITIONS')
 def test_07_stage_head_access_and_software_microscope(self):
  h=setup();self.rejected(h,'stage.move','HEAD_NOT_CLEAR',x_m=.01,y_m=0.,speed_m_s=.001);self.rejected(h,'specimen.remove','HEAD_NOT_CLEAR');self.rejected(h,'head.set_position','CONTACT_ENGAGED',distance_m=.04);park(h);call(h,'tool.select',mode='microscope');self.assertTrue(h.clamped);call(h,'stage.move',x_m=.01,y_m=-.027,speed_m_s=.01);self.assertEqual(h.stage_x,0.);step(h,3.);self.assertAlmostEqual(h.stage_y,-.027);call(h,'tool.select',mode='stylus');self.ledger(h)
 def test_08_all_manufacturer_ranges_and_bad_inputs_are_atomic(self):
  h=setup()
  for field,value in [('initial_force_n',200.0001),('final_force_n',200.0001),('speed_m_s',rt.SPEED_MIN*.9),('speed_m_s',.01001),('length_m',.07001),('sample_interval_s',.15)]:self.rejected(h,'method.configure',**dict(h.method,**{field:value}))
  self.rejected(h,'method.configure','STAGE_PATH_RANGE',**dict(h.method,start_x_m=.0699,length_m=.002));self.rejected(h,'method.configure','DECREASING_LOAD_UNSUPPORTED',**dict(h.method,initial_force_n=20.,final_force_n=10.))
  for value in [True,float('nan'),float('inf'),-.1]:self.rejected(h,'sim.step',dt_s=value)
  self.rejected(h,'power.set',enabled=1);self.rejected(h,'method.configure',**dict(h.method,extra=1));self.rejected(h,'head.set_position',distance_m=.04001)
 def test_09_baseline_required_calibration_and_probe_load_match(self):
  h=setup();self.rejected(h,'acquisition.start','PRESCAN_REQUIRED');h=prescanned();call(h,'sensor.calibrate',**dict(rt.CALIBRATION_EXAMPLE,depth_zero_m=1e-6));self.rejected(h,'acquisition.start','BASELINE_CALIBRATION_MISMATCH');call(h,'sensor.calibrate',**rt.CALIBRATION_EXAMPLE);call(h,'method.configure',**dict(h.method,scan_force_n=.2));self.rejected(h,'acquisition.start','BASELINE_LOAD_MISMATCH')
 def test_10_damaged_track_reuse_rejected_new_y_allowed(self):
  h=prescanned();run(h);at_start(h);self.rejected(h,'acquisition.start','TRACK_ALREADY_DAMAGED');call(h,'method.configure',**dict(h.method,pass_kind='prescan'));self.rejected(h,'acquisition.start','TRACK_ALREADY_DAMAGED');park(h,0.,.001);call(h,'method.configure',**dict(h.method,path_y_m=.001));call(h,'head.set_position',distance_m=0.);step(h,4.);call(h,'contact.set',engaged=True);self.assertEqual(run(h)['phase'],'succeeded');self.ledger(h)
 def test_11_finite_coating_then_substrate_and_segment_volume(self):
  h=prescanned(sample={'coating_thickness_m':1e-8});l0=h._ledger();r=run(h);self.assertEqual(r['phase'],'succeeded');l=self.ledger(h);self.assertGreater(l['coating_removed_m3'],0);self.assertGreater(l['substrate_removed_m3'],0);volume=sum((s['x1']-s['x0'])*s['depth_m']*h.specimen['track_width_m'] for s in h.active_track['segments']);self.assertAlmostEqual(volume,l['coating_removed_m3']+l['substrate_removed_m3'],places=23);self.assertAlmostEqual(l['coating_removed_m3']*1e9+l['substrate_removed_m3']*1e9+l['stylus_removed_m3']*1e10,l['wear_work_j']-l0['wear_work_j'],places=14)
 def test_12_tiny_stylus_exhaustion_exact_partial_and_ledger(self):
  h=prescanned(stylus={'volume_m3':1e-15,'removal_energy_j_m3':1e3,'wear_energy_fraction':.5});r=run(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'STYLUS_EXHAUSTED');self.assertGreater(r['distance_m'],0);self.assertLess(r['distance_m'],h.method['length_m']);self.assertGreaterEqual(h.volumes['stylus'],0);self.assertLess(h.volumes['stylus'],1e-23);self.assertEqual(r['rows'][-1]['elapsed_s'],r['elapsed_s']);self.ledger(h)
 def test_13_local_depth_force_and_temperature_finite_boundaries(self):
  cases=[({'contact_stiffness_n_m':1e3},None,None,'DEPTH_RANGE'),({'thermal_capacity_j_k':.001,'heat_loss_w_k':0.,'temperature_max_k':296.16},None,None,'THERMAL_MODEL_RANGE'),({}, {'substrate_thickness_m':1e-9},None,'LOCAL_SPECIMEN_THICKNESS')]
  for plant,sample,stylus,error in cases:
   h=prescanned(plant=plant,sample=sample,stylus=stylus);r=run(h);self.assertEqual(r['error_code'],error);self.assertLessEqual(h._depth(),rt.DEPTH_MAX+1e-15);self.assertLessEqual(h.temperature,h.plant['temperature_max_k']+1e-12);self.ledger(h)
  h=prescanned(method={'initial_force_n':1.,'final_force_n':150.});h.ref_mu=np.full_like(h.ref_mu,2.);r=run(h);self.assertEqual(r['error_code'],'FRICTION_FORCE_RANGE');self.assertLessEqual(h.force,100.+1e-10);self.ledger(h)
 def test_14_energy_fractions_reject_without_loading(self):
  h=setup(sample={'sample_wear_energy_fraction':.9},stylus={'wear_energy_fraction':.2});call(h,'method.configure',**dict(h.method,pass_kind='prescan'));self.rejected(h,'acquisition.start','ENERGY_FRACTIONS');self.assertEqual(h.force,0.)
 def test_15_heat_adiabatic_cooling_stiff_stability_and_spring_return(self):
  h=prescanned(plant={'heat_loss_w_k':0.,'thermal_capacity_j_k':.01});run(h);l=self.ledger(h);self.assertAlmostEqual(h.temperature,296.15+l['heat_input_j']/.01,places=8);self.assertEqual(h.force,0.);self.assertAlmostEqual(l['normal_loading_work_j'],l['normal_recovered_work_j'],places=13)
  h=prescanned(plant={'thermal_capacity_j_k':.001,'heat_loss_w_k':1000.});run(h);self.assertGreaterEqual(h.temperature,296.15);self.assertLess(h.temperature,296.16);self.ledger(h)
 def test_16_passive_cooling_and_hot_access(self):
  h=prescanned(plant={'thermal_capacity_j_k':.01,'heat_loss_w_k':0.,'safe_access_temperature_k':296.150001});run(h);park(h);call(h,'clamp.set',locked=False);self.rejected(h,'specimen.remove','HOT_SPECIMEN');self.ledger(h)
  h=prescanned(plant={'thermal_capacity_j_k':1.,'heat_loss_w_k':.1});run(h);start=h.temperature;step(h,3.);self.assertAlmostEqual(h.temperature,296.15+(start-296.15)*math.exp(-.3),places=9)
 def test_17_nondestructive_prescan_postscan_do_not_replenish_damage(self):
  h=setup();v=copy.deepcopy(h.volumes);run(h,'prescan');self.assertEqual(v,h.volumes);at_start(h);run(h,'scratch');v=copy.deepcopy(h.volumes);at_start(h);run(h,'postscan');self.assertEqual(v,h.volumes);self.ledger(h)
 def test_18_sensor_bias_coefficient_and_external_calibration(self):
  h=prescanned(plant={'force_gain':2.,'friction_gain':1.5});r=run(h);s=[v for v in r['rows'] if v['phase']=='scanning' and v['friction_coefficient'] is not None][-1];self.assertAlmostEqual(s['friction_coefficient'],abs(s['friction_force_n'])/s['normal_force_n']);self.assertAlmostEqual(s['normal_force_n'],20.,places=8);self.assertNotAlmostEqual(s['friction_coefficient'],float(np.interp(h.method['source_start_distance_m']+h.distance,h.ref_x,h.ref_mu)),places=4)
  h=setup(plant={'force_offset_n':.2,'friction_offset_n':.3,'depth_offset_m':1e-6});park(h);call(h,'sensor.zero');self.assertAlmostEqual(h.calibration['normal_zero_n'],.2);self.assertAlmostEqual(h.calibration['friction_zero_n'],.3);call(h,'sensor.calibrate',**rt.CALIBRATION_EXAMPLE)
 def test_19_sensor_overrange_unavailable_and_lowload_reason(self):
  h=prescanned(plant={'force_gain':10.},method={'initial_force_n':30.,'final_force_n':30.});r=run(h);self.assertEqual(r['error_code'],'SENSOR_OVER_RANGE');self.assertIsNone(r['rows'][-1]['normal_force_n']);self.ledger(h)
  h=setup(plant={'force_gain':.1},method={'pass_kind':'prescan','scan_force_n':.001});call(h,'acquisition.start');step(h,.2);self.assertEqual(h.read()['observation.unavailable_reason'],'LOW_LOAD');self.assertIsNone(h.read()['friction.coefficient'])
 def test_20_tilted_same_coordinate_baseline_cancels_surface_profile(self):
  a=prescanned();b=prescanned(sample={'surface_slope_x':.001});run(a);run(b);at_start(a);at_start(b);ra=run(a,'postscan');rb=run(b,'postscan');pa=[r for r in ra['rows'] if r['phase']=='scanning'];pb=[r for r in rb['rows'] if r['phase']=='scanning'];self.assertAlmostEqual(pa[-1]['residual_depth_m'],pb[-1]['residual_depth_m'],delta=1e-10);self.assertNotEqual(pa[-1]['raw_depth_m'],pb[-1]['raw_depth_m'])
 def test_21_critical_bracket_persistence_missing_and_leftcensor(self):
  a=rows([0.,1.,3.,4.]);r=rt.critical_load(a,'friction_force_n',2.,2);self.assertAlmostEqual(r['position_m'],.0015);self.assertAlmostEqual(r['critical_load_n'],11.5)
  for values in [[0.,None,3.,4.],[0.,1.,3.,None],[3.,4.,5.]]:
   r=rt.critical_load(rows(values),'friction_force_n',2.,2);self.assertIsNone(r['critical_load_n'])
  self.assertEqual(rt.critical_load(rows([3.,4.]),'friction_force_n',2.,1)['reason'],'LEFT_CENSORED')
 def test_22_postscan_lc_maps_scratch_load_never_probe_load(self):
  post=rows([0.,1e-6,3e-6,4e-6],'residual_depth_m',loads=[.1]*4);scratch=rows([0.,1.,2.,3.],loads=[5.,15.,25.,35.]);r=rt.critical_load(post,'residual_depth_m',2e-6,2,scratch);self.assertAlmostEqual(r['critical_load_n'],20.);self.assertNotEqual(r['critical_load_n'],.1)
  self.assertIsNone(rt.critical_load(post,'residual_depth_m',2e-6,2)['critical_load_n']);scratch[1]['normal_force_n']=None;self.assertIsNone(rt.critical_load(post,'residual_depth_m',2e-6,2,scratch)['critical_load_n'])
  h=setup();args=dict(rt.CAPS['analysis.critical_load']['example_arguments'],rows=post,load_rows=rows([0.,1.,2.,3.],loads=[5.,15.,25.,35.]),channel='residual_depth_m',threshold_m=2e-6,persistence=2);self.assertAlmostEqual(call(h,'analysis.critical_load',**args)['critical_load_n'],20.)
 def test_23_criterion_stop_preserves_partial_track_and_released_load(self):
  h=prescanned(method={'length_m':.002,'criterion_threshold_n':.5,'criterion_persistence':1});call(h,'method.configure',**dict(h.method,stop_on_criterion=True));r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertGreater(r['distance_m'],0.);self.assertLess(r['distance_m'],.002);self.assertEqual(h.force,0.);self.assertEqual(h.active_track['distance'],r['distance_m']);self.ledger(h)
 def test_24_cancel_keeps_damage_and_force_relaxes_only_by_step(self):
  h=prescanned();call(h,'acquisition.start');step(h,.8);f=h.force;v=copy.deepcopy(h.volumes);call(h,'acquisition.stop');r=call(h,'acquisition.result');self.assertEqual(h.force,f);self.assertEqual(v,h.volumes);self.rejected(h,'contact.set','LOAD_NOT_RELEASED',engaged=False);step(h,2.);self.assertEqual(h.force,0.);self.assertEqual(v,h.volumes);self.assertEqual(r,call(h,'acquisition.result'));self.ledger(h)
 def test_25_timeout_and_software_stop_keep_partial_effects(self):
  h=setup(method={'pass_kind':'prescan','timeout_s':.1});r=run(h);self.assertEqual(r['error_code'],'TIMEOUT');self.assertAlmostEqual(r['elapsed_s'],.1)
  h=prescanned();call(h,'acquisition.start');step(h,.8);call(h,'emergency.stop');self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'SOFTWARE_STOP');self.rejected(h,'emergency.reset','LOAD_NOT_RELEASED');step(h,2.);call(h,'emergency.reset');self.assertFalse(h.emergency);self.assertEqual(h.phase,'failed');self.ledger(h)
 def test_26_history_frozen_after_method_clean_and_new_sample(self):
  h=prescanned();r=run(h);op=r['operation_id'];frozen=copy.deepcopy(h.records[op]);cleanup(h);call(h,'method.configure',**dict(h.method,pass_kind='postscan',criterion_threshold_m=1e-4));call(h,'specimen.load',**rt.SAMPLE_EXAMPLE);self.assertEqual(h.records[op],frozen);self.assertEqual(call(h,'acquisition.result'),frozen);self.assertEqual(json.loads(frozen['method_json'])['pass_kind'],'scratch');self.assertIn('derived_nominal_load_ratio',frozen['evidence']);self.assertGreater(frozen['source_projection_count'],0)
 def test_27_queries_pure_and_owner_not_stolen(self):
  h=setup(method={'pass_kind':'prescan'});s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];step(h,.3);before=state(h);h.read();h.describe();s.read(instance_id=h.instance_id);s.poll(op);call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);call(h,'image.reference',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);call(h,'ledger.read');self.assertEqual(state(h),before);q=s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='result');self.assertIsNone(q['operation_id']);self.assertEqual(h._owners[op],'agent');json.dumps(q,allow_nan=False)
 def test_28_session_owner_access_poll_cancel_and_idempotence(self):
  h=setup(method={'pass_kind':'prescan'});h.register_principal('other');a=h.session();b=h.session('other');args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=a.invoke(**args);self.assertEqual(r,a.invoke(**args));op=r['operation_id'];self.assertEqual(h._counter,1)
  for k in ['acquisition.result','acquisition.stop']:self.assertEqual(b.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k)['error']['code'],'ACCESS_DENIED')
  for fn in [lambda:b.poll(op),lambda:b.cancel(op,'stop')]:
   with self.assertRaises(rt.ActionRejected):fn()
  c=a.cancel(op,'stop');self.assertEqual(c,a.cancel(op,'stop'));self.assertEqual(a.invoke(**dict(args,capability_id='power.set',arguments={'enabled':False}))['error']['code'],'REQUEST_CONFLICT')
 def test_29_stale_cancel_retry_cannot_cancel_new_operation(self):
  h=setup(method={'pass_kind':'prescan'});s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');op1=s.invoke(**a)['operation_id'];c=s.cancel(op1,'cancel');step(h,2.);op2=s.invoke(**dict(a,request_id='two'))['operation_id'];self.assertEqual(s.cancel(op1,'cancel'),c);self.assertEqual(h.phase,'running');self.assertEqual(h.operation_id,op2)
  with self.assertRaisesRegex(rt.ActionRejected,'REQUEST_CONFLICT'):s.cancel(op2,'cancel')
 def test_30_no_agent_truth_or_recovery_capabilities(self):
  h=setup();s=h.session();meta=s.describe(instance_id=h.instance_id);self.assertTrue(all(not k.startswith('truth.') for k in meta['state_slots']));self.assertNotIn('ledger.read',meta['capabilities']);self.assertNotIn('specimen.remove',meta['capabilities']);self.assertTrue(all(not k.startswith('truth.') for k in s.read(instance_id=h.instance_id)['observations']))
  for k in ['ledger.read','specimen.remove','stylus.remove','debris.collect','sim.step','plant.configure']:
   args=rt.CAPS[k]['example_arguments'];before=state(h);r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=args,request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(state(h),before)
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.normal_force_n'])
  with self.assertRaises(rt.ActionRejected):s.write(instance_id=h.instance_id,state_id='load.target_n',value=100.,request_id='x')
 def test_31_two_instances_and_fractional_host_time_partition(self):
  a=setup('one',method={'pass_kind':'prescan'});b=setup('two',method={'pass_kind':'prescan'});call(a,'acquisition.start');call(b,'acquisition.start');step(a,1.3)
  for dt in [.03,.07,.25,.15,.4,.4]:step(b,dt)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);snapshot=state(b);step(a,.2);self.assertEqual(state(b),snapshot);self.assertNotEqual(a.time_s,b.time_s);call(a,'sim.step',dt_s=0.)
 def test_32_extreme_dt_and_stiff_force_stable(self):
  a=setup('a',plant={'force_response_tau_s':1e-5,'sensor_tau_s':1e-5},method={'pass_kind':'prescan'});b=setup('b',plant={'force_response_tau_s':1e-5,'sensor_tau_s':1e-5},method={'pass_kind':'prescan'});call(a,'acquisition.start');call(b,'acquisition.start');step(a,10.)
  for _ in range(100):step(b,.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);self.assertEqual(a.phase,'succeeded');self.ledger(a)
 def test_33_full_recovery_mass_ledger_and_no_resurrection(self):
  h=prescanned(sample={'coating_thickness_m':1e-8});run(h);cleanup(h);l=self.ledger(h)
  for n in ['coating','substrate','stylus']:self.assertEqual(l[n+'_remaining_kg'],0.);self.assertEqual(l[n+'_debris_kg'],0.)
  self.assertGreater(l['substrate_debris_recovered_kg'],0.);self.assertGreater(l['stylus_debris_recovered_kg'],0.);self.rejected(h,'specimen.remove','NOT_LOADED')
 def test_34_no_hidden_truth_in_agent_results(self):
  h=setup(method={'pass_kind':'prescan'});s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];step(h,5.);r=s.poll(op);json.dumps(r,allow_nan=False)
  def visit(value):
   if isinstance(value,dict):
    for k,v in value.items():self.assertFalse(k.startswith('truth.') or k in ['local_groove_m','coating_remaining_kg','substrate_remaining_kg','stylus_remaining_kg']);visit(v)
   elif isinstance(value,list):
    for v in value:visit(v)
  visit(r);visit(s.read(instance_id=h.instance_id))
 def test_35_declared_unsupported_and_reference_only_reject(self):
  h=setup()
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
  park(h);call(h,'clamp.set',locked=False);call(h,'specimen.remove');lib=rt.library();rid=next(k for k,r in lib['substances'].items() if not r.get('runtime_usable'));self.rejected(h,'specimen.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id=rid));self.rejected(h,'reference.query','INVALID_REFERENCE',reference_id='missing');self.rejected(h,'specimen.load','NEGATIVE_SOURCE_FORCE',**dict(rt.SAMPLE_EXAMPLE,accept_negative_force_projection=False))
 def test_36_protocol_describe_registration_errors_and_json(self):
  h=setup();s=h.session();self.assertEqual(s.describe(asset_type_id=rt.ASSET_TYPE_ID)['asset_type_id'],rt.ASSET_TYPE_ID)
  for fn in [lambda:s.describe(),lambda:s.describe(instance_id='bad'),lambda:h.session('bad'),lambda:h.register_principal('agent'),lambda:s.read(instance_id=h.instance_id,state_ids='bad')]:
   with self.assertRaises(rt.ActionRejected):fn()
  for capability,arguments in [('unknown',{}),('power.set',{'enabled':'yes'}),('acoustic_emission.acquire',{})]:json.dumps(s.invoke(instance_id=h.instance_id,capability_id=capability,arguments=arguments,request_id=capability),allow_nan=False)
 def test_37_failed_method_does_not_mutate_existing_results(self):
  h=prescanned();r=run(h);self.rejected(h,'method.configure','STAGE_PATH_RANGE',**dict(h.method,start_x_m=.07));self.assertEqual(r,call(h,'acquisition.result'));before=state(h);s=h.session();rejected=s.invoke(instance_id=h.instance_id,capability_id='stage.move',arguments={'x_m':.1,'y_m':0.,'speed_m_s':.01},request_id='bad');self.assertEqual(rejected['status'],'rejected');self.assertEqual(state(h),before)
 def test_38_phases_actual_sampling_time_and_endpoint_snapshot(self):
  h=prescanned(method={'length_m':.0005,'speed_m_s':.001,'sample_interval_s':.2});r=run(h);scan=[x for x in r['rows'] if x['phase']=='scanning'];self.assertAlmostEqual(scan[-1]['time_s']-scan[0]['time_s'],.5,places=9);self.assertAlmostEqual(scan[-1]['distance_m'],.0005);self.assertEqual(r['rows'][-1]['elapsed_s'],r['elapsed_s']);self.assertEqual(r['rows'][-1]['normal_force_n'],h.rows[-1]['normal_force_n']);self.assertGreater(r['elapsed_s'],.5);self.assertEqual(h.force,0.);self.assertTrue(all(y['time_s']>=x['time_s'] for x,y in zip(r['rows'],r['rows'][1:])))
 def test_39_accepted_source_variants_change_actual_work(self):
  usable=[(k,r) for k,r in rt.library()['substances'].items() if r.get('runtime_usable') and k.startswith(('borofloat33_','b4c_','al2o3_'))];works=[]
  for rid,ref in usable[:2]:
   h=prescanned(sample={'reference_id':rid},method={'source_start_distance_m':ref['values']['distance_m'][0]});r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(r['reference_id'],rid);works.append(h.tangential_work)
  self.assertEqual(len(works),2);self.assertNotEqual(*works)
 def lc_run(self,rid,thickness=2e-6,initial=5.,final=45.,length=.004,channel='penetration_depth_m',threshold_m=1e-4,sample=None):
  s=dict({'reference_id':rid,'coating_thickness_m':thickness,'coating_density_kg_m3':2000.},**(sample or {}))
  h=prescanned(sample=s,method={'length_m':length,'initial_force_n':initial,'final_force_n':final,'speed_m_s':.01/60,'criterion_channel':channel,'criterion_threshold_m':threshold_m,'criterion_persistence':2})
  return h,run(h)
 def test_41_published_lc_events_trigger_at_actual_load_and_spall_the_coating(self):
  rid='bcr692_dlc_steel_cert2007';lib=rt.library()['substances'];rs=lib[rid]['runtime_scalars'];k=rt.PLANT_EXAMPLE['contact_stiffness_n_m']
  self.assertEqual([e['failure_class'] for e in rs['critical_load_events']],['cohesive','adhesive','delamination'])
  # penetration threshold placed 1 um above the elastic spring depth at Lc3: only the 2 um spallation step can cross it near Lc3
  h,r=self.lc_run(rid,threshold_m=28./k+1e-6);self.assertEqual(r['phase'],'succeeded')
  ev=json.loads(call(h,'evaluation.failure_events')['events_json']);self.assertEqual([e['label'] for e in ev],['Lc1','Lc2','Lc3']);self.assertTrue(all(e['triggered'] for e in ev))
  for e in ev:self.assertGreaterEqual(e['actual_normal_force_n'],e['source_critical_load_n']);self.assertLess(e['actual_normal_force_n']-e['source_critical_load_n'],.2)
  self.assertEqual([e['starts_coating_removal'] for e in ev],[False,False,True]);self.assertTrue(h.read()['truth.coating_spalled']);self.assertEqual(h.read()['truth.failure_events'],3)
  self.assertIsNotNone(r['critical']['critical_load_n']);self.assertAlmostEqual(r['critical']['critical_load_n'],28.,delta=1.)
  l=self.ledger(h);self.assertGreater(l['coating_debris_kg'],0.);cap=rt.SAMPLE_EXAMPLE['track_width_m']*2e-6;self.assertGreaterEqual(l['coating_removed_m3'],cap*(r['distance_m']-ev[2]['distance_m'])-1e-18);self.assertLessEqual(l['coating_removed_m3'],cap*r['distance_m']+1e-18)
  self.assertIn('Published scalar critical loads',r['evidence']);self.assertIn('plant.scan_friction_coefficient',r['evidence'])
  # same load program without spallation (bare curve reference) never crosses the threshold before 38 N
  scan=[x for x in r['rows'] if x['phase']=='scanning' and x['penetration_depth_m'] is not None];before=[x for x in scan if x['normal_force_n'] is not None and x['normal_force_n']<27.5];after=[x for x in scan if x['normal_force_n'] is not None and x['normal_force_n']>29.]
  self.assertLess(max(x['penetration_depth_m'] for x in before),28./k+1e-6);self.assertGreater(min(x['penetration_depth_m']-x['normal_force_n']/k for x in after),1.5e-6)
 def test_42_delamination_not_adhesive_starts_removal_and_untriggered_events_stay_false(self):
  lib=rt.library()['substances'];rid='dlc_k340_not_nitrided';rs=lib[rid]['runtime_scalars']
  lc2,lc3=[e['normal_force_n'] for e in rs['critical_load_events']]
  h,r=self.lc_run(rid,initial=10.,final=(lc2+lc3)/2,length=.003);ev=json.loads(call(h,'evaluation.failure_events')['events_json'])
  self.assertEqual([(e['label'],e['triggered']) for e in ev],[('LC2',True),('LC3',False)]);self.assertFalse(h.read()['truth.coating_spalled']);self.assertEqual(self.ledger(h)['coating_removed_m3']>0,True)
  self.assertLess(self.ledger(h)['coating_removed_m3'],rt.SAMPLE_EXAMPLE['track_width_m']*2e-6*.003*.5)
  h,r=self.lc_run('ni_ti_5p4um_annealed',thickness=5.4e-6,initial=1.,final=60.,length=.003);ev=json.loads(call(h,'evaluation.failure_events')['events_json'])
  self.assertEqual(len(ev),1);self.assertEqual(ev[0]['failure_class'],'adhesive');self.assertTrue(ev[0]['starts_coating_removal']);self.assertTrue(h.read()['truth.coating_spalled'])
 def test_43_lc_reference_conditions_and_evaluator_only_events(self):
  rid='bcr692_dlc_steel_cert2007'
  h=prescanned(sample={'reference_id':rid,'coating_thickness_m':2e-6,'accept_condition_transfer':False},method={'length_m':.004,'initial_force_n':5.,'final_force_n':40.,'speed_m_s':.01/60})
  self.rejected(h,'acquisition.start','REFERENCE_CONDITIONS')
  h=prescanned(sample={'reference_id':'dlc_hws_nitrided','coating_thickness_m':2e-6,'accept_unknown_conditions':False},method={'length_m':.002,'initial_force_n':5.,'final_force_n':40.})
  self.rejected(h,'acquisition.start','UNKNOWN_REFERENCE_CONDITIONS')
  s=h.session();q=s.invoke(instance_id=h.instance_id,capability_id='evaluation.failure_events',arguments={},request_id='ev');self.assertEqual(q['error']['code'],'ACCESS_DENIED')
  self.assertNotIn('evaluation.failure_events',s.describe(instance_id=h.instance_id)['capabilities']);self.assertTrue(all(not k.startswith('truth.') for k in s.read(instance_id=h.instance_id)['observations']))
  self.assertEqual(json.loads(call(h,'evaluation.failure_events')['events_json']),[])
  bad=copy.deepcopy(rt.library()['substances'][rid]);bad['runtime_scalars']['critical_load_events'][0]['normal_force_n']=250.
  with self.assertRaisesRegex(rt.ActionRejected,'INVALID_REFERENCE'):rt.curve(bad,True,.03)
 def test_44_coverage_counts_and_reference_only_records(self):
  lib=rt.library();cov=lib['response_coverage'];subs=lib['substances']
  self.assertEqual(cov['distinct_reference_materials'],len({r['material_identity'] for r in subs.values()}));self.assertEqual(cov['runtime_materials'],len({r['material_identity'] for r in subs.values() if r['runtime_usable']}))
  self.assertEqual(cov['minimum_50_runtime_materials_met'],cov['runtime_materials']>=50);self.assertFalse(subs['ni_ti_10p8um_annealed_observation']['runtime_usable'])
  for r in subs.values():
   if r['kind']=='published_coating_critical_load':
    self.assertTrue(r['source']['sha256']);self.assertTrue(r['source']['verbatim_snippet']);self.assertTrue(r['runtime_usable'] or r.get('runtime_block_reason'))
   if r.get('runtime_basis')==rt.EVENT_BASIS:self.assertNotIn('distance_m',r['values'])
 def test_45_open_access_coating_systems_across_classes_trigger_their_published_lc(self):
  lib=rt.library()['substances'];oa=sorted(k for k,r in lib.items() if r['kind']=='published_coating_critical_load' and r['runtime_usable'])
  self.assertGreaterEqual(len({lib[k]['material_identity'] for k in oa}),40)
  picks=[k for k in ['oa_peo_we43b','oa_wcco45_tialsin','oa_knee_ti64_acchw','oa_s355_cu','oa_w360_crn_dlc','oa_sus304_dlc'] if k in oa]
  self.assertGreaterEqual(len(picks),5)
  for rid in picks:
   rs=lib[rid]['runtime_scalars'];top=max(e['normal_force_n'] for e in rs['critical_load_events']);th=rs.get('coating_thickness_m') or 2e-6
   h,r=self.lc_run(rid,thickness=th,initial=.5,final=min(200.,top*1.2+1.),length=.003);self.assertEqual(r['phase'],'succeeded',rid)
   ev=json.loads(call(h,'evaluation.failure_events')['events_json']);self.assertTrue(all(e['triggered'] for e in ev),rid)
   for e in ev:self.assertLess(abs(e['actual_normal_force_n']-e['source_critical_load_n']),.5,rid)
   self.assertEqual(h.read()['truth.coating_spalled'],any(e['starts_coating_removal'] for e in ev));self.ledger(h)
 def test_zz_all_real_capability_returns_and_state_schemas(self):
  spec=importlib.util.spec_from_file_location('rst_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_antonpaar_rst300_contract_metadata();json.dumps(meta,allow_nan=False)
  for k,c in meta['capabilities'].items():
   self.assertEqual(tool.lint_schema(c['input_schema'],path=k+'.input'),[]);self.assertEqual(tool.lint_schema(c['output_schema'],path=k+'.output'),[])
  covered=set()
  for k,a,r in OBSERVED:
   self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema'],path=k+'.input'),[]);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema'],path=k+'.output'),[]);covered.add(k)
  self.assertEqual(covered,set(rt.CAPS)-set(rt.UNSUPPORTED))
  for state_values in STATES:
   self.assertEqual(set(state_values),set(rt.SLOTS));json.dumps(state_values,allow_nan=False)
   for k,v in state_values.items():self.assertEqual(tool.validate_value(v,meta['state_slots'][k]['value_schema'],path=k),[])
  for k,c in meta['capabilities'].items():self.assertTrue(set(rt.CAPS[k]['effects'])<={e['state_id'] for e in c['state_effects']})
if __name__=='__main__':unittest.main()
