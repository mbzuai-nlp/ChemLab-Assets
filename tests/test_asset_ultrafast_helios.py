
"""Independent optics/motion/protocol tests for CHEM-076, no hardware claims."""
import copy,json,math,pickle,unittest
import numpy as np
from scipy.integrate import quad
from scripts.asset_runtime.ultrafast_helios import *
def ready(plant=None,sample=None,method=None):
 h=UltrafastHeliosRuntime();h.invoke('plant.configure',**(plant or PLANT_EXAMPLE));h.invoke('sample.load',**(sample or SAMPLE_EXAMPLE));h.invoke('sample.cover',opened=False);h.invoke('power.set',enabled=True);h.invoke('laser.arm',enabled=True);h.invoke('method.configure',**(method or METHOD_EXAMPLE));return h
def collect(plant=None,sample=None,method=None):
 h=ready(plant,sample,method);h.invoke('acquisition.start');h.step_clock(10000);return h
def snap(h):
 return json.dumps(h.debug_snapshot(),sort_keys=True,default=lambda x:x.tolist() if hasattr(x,'tolist') else str(x))
def short_method(**kw):
 return dict(METHOD_EXAMPLE,delay_sequence_s=[100e-12],averages=3,sample_pattern_xy_m=[[0.,0.]])|kw
class HeliosTests(unittest.TestCase):
 def rejected_unchanged(self,h,k,code=None,**a):
  before=snap(h)
  with self.assertRaises(ActionRejected) as cm:h.invoke(k,**a)
  if code:self.assertEqual(str(cm.exception),code)
  self.assertEqual(snap(h),before)
 def test_source_count_and_types(self):
  ts=_templates_for_test();self.assertEqual(sum(t['kind']=='ground_state' for t in ts.values()),63);self.assertEqual(sum(t['kind']=='transient' for t in ts.values()),1)
 def test_measured_ta_units_and_bounds(self):
  t=_templates_for_test()['ru_dppz_acn']['measured_transient_absorption'];self.assertEqual(len(t['delay_s']),183);self.assertEqual(len(t['wavelength_m']),446);self.assertAlmostEqual(t['wavelength_m'][0],335e-9);self.assertAlmostEqual(t['delay_s'][0],-10.0001e-12);self.assertEqual(t['delta_absorbance'][0][0],.002350935)
 def test_template_read_is_copy(self):
  h=UltrafastHeliosRuntime();r=h.invoke('template.read',substance_id='ru_dppz_acn');r['name']='changed';self.assertNotEqual(h.templates['ru_dppz_acn']['name'],'changed')
 def test_source_truth_not_public(self):
  h=ready();r=json.dumps(h.read())+json.dumps(h.invoke('result.read'));self.assertNotIn('pcad_009',r);self.assertNotIn('rhodamine_b_demo',r);self.assertNotIn('truth.ledger',h.read())
 def test_null_before_measurement(self):
  h=UltrafastHeliosRuntime();self.assertIsNone(h.invoke('process.measure')['delta_absorbance_center']);self.assertEqual(h.invoke('raw.read')['off_sample'],[])
 def test_atomic_nan(self):
  h=ready();m=copy.deepcopy(METHOD_EXAMPLE);m['delay_sequence_s'][0]=float('nan');self.rejected_unchanged(h,'method.configure',**m)
 def test_atomic_bool_number(self):
  h=ready();self.rejected_unchanged(h,'method.configure',**dict(METHOD_EXAMPLE,pump_average_power_W=True))
 def test_extra_argument(self):
  self.rejected_unchanged(ready(),'power.set',enabled=True,unexpected=1)
 def test_unknown_capability(self):
  self.rejected_unchanged(ready(),'unknown')
 def test_optional_unsupported(self):
  for k in UNSUPPORTED:self.rejected_unchanged(ready(),k,'UNSUPPORTED',**CAPS[k][6])
 def test_once_installation(self):
  h=ready();self.rejected_unchanged(h,'plant.configure','INITIALIZATION_ONLY',**PLANT_EXAMPLE)
 def test_configuration_after_clock_ticks(self):
  h=UltrafastHeliosRuntime();h.step_clock(10);h.invoke('plant.configure',**PLANT_EXAMPLE);self.assertIsNotNone(h.plant)
 def test_no_assumptions_without_ack(self):
  self.rejected_unchanged(UltrafastHeliosRuntime(),'plant.configure','EVIDENCE_ACK_REQUIRED',**dict(PLANT_EXAMPLE,accept_surrogate_model=False))
 def test_solvent_mismatch(self):
  h=ready();h.invoke('sample.cover',opened=True);self.rejected_unchanged(h,'sample.load','SOURCE_CONDITIONS_MISMATCH',**dict(SAMPLE_EXAMPLE,solvent='water'))
 def test_no_ta_matrix_as_epsilon(self):
  h=ready();h.invoke('sample.cover',opened=True);self.rejected_unchanged(h,'sample.load','GROUND_STATE_TEMPLATE_REQUIRED',**dict(SAMPLE_EXAMPLE,template_id='ru_dppz_acn'))
 def test_weights_normalize(self):
  h=ready();h.invoke('sample.cover',opened=True);self.rejected_unchanged(h,'sample.load','SAMPLE_COMPOSITION',**dict(SAMPLE_EXAMPLE,kinetic_weights=[.9]))
 def test_external_laser_match(self):
  self.rejected_unchanged(ready(),'method.configure','EXTERNAL_LASER_MISMATCH',**dict(METHOD_EXAMPLE,pump_average_power_W=1e-6))
 def test_option_gate(self):
  h=ready(plant=dict(PLANT_EXAMPLE,reference_installed=False),method=dict(METHOD_EXAMPLE,reference_enabled=False));self.rejected_unchanged(h,'method.configure','OPTION_NOT_INSTALLED',**METHOD_EXAMPLE)
 def test_xy_gate(self):
  h=ready(plant=dict(PLANT_EXAMPLE,xy_installed=False),method=dict(METHOD_EXAMPLE,sample_pattern_xy_m=[[0.,0.]]));self.rejected_unchanged(h,'method.configure','OPTION_NOT_INSTALLED',**METHOD_EXAMPLE)
 def test_manual_filter_gate(self):
  self.rejected_unchanged(ready(),'method.configure','MANUAL_FILTER_REQUIRED',**dict(METHOD_EXAMPLE,wavelength_stop_m=820e-9))
 def test_delay_window(self):
  self.rejected_unchanged(ready(),'method.configure','DELAY_WINDOW',**dict(METHOD_EXAMPLE,delay_sequence_s=[7.9e-9]))
 def test_grid_rejected(self):
  self.rejected_unchanged(ready(),'method.configure','SPECTRAL_GRID',**dict(METHOD_EXAMPLE,wavelength_step_m=7e-9))
 def test_interlock_closed_and_power(self):
  for k,a in [('sample.cover',dict(opened=True)),('power.set',dict(enabled=False)),('laser.arm',dict(enabled=False))]:
   h=ready();h.invoke(k,**a);self.rejected_unchanged(h,'acquisition.start','OPTICS_NOT_READY')
 def test_busy_atomic(self):
  h=ready();h.invoke('acquisition.start');self.rejected_unchanged(h,'method.configure','BUSY',**METHOD_EXAMPLE)
 def test_high_excitation_atomic(self):
  p=dict(PLANT_EXAMPLE,laser_average_power_W=.01,pump_radius_m=10e-6);h=ready(plant=p,method=dict(METHOD_EXAMPLE,pump_average_power_W=.01));self.rejected_unchanged(h,'acquisition.start','WEAK_EXCITATION_LIMIT')
 def test_photon_cross_section(self):
  h=ready();h.invoke('acquisition.start');p=h.plant;s=h.sample;t=h.templates[s['template_id']];epsilon=np.interp(p['laser_wavelength_m'],t['abs_wavelength_m'],np.maximum(t['epsilon_m2_mol'],0));sigma=np.log(10)*epsilon/6.02214076e23;photons=p['laser_average_power_W']/p['laser_repetition_hz']/(6.62607015e-34*299792458/p['laser_wavelength_m']);expected=1-np.exp(-sigma*photons/(np.pi*p['pump_radius_m']**2));self.assertAlmostEqual(h._excited,expected,14)
 def test_irf_not_delay_step(self):
  h=ready();self.assertGreater(h.irf_fwhm,2.8e-15*10);self.assertAlmostEqual(h.irf_fwhm**2,(35e-15)**2+(50e-15)**2+(14e-15)**2,36)
 def test_convolution_independent_integral(self):
  sig=.08;tau=.3
  for t in [-.2,0,.1,1]:
   expected=quad(lambda u:math.exp(-u/tau)*math.exp(-.5*((t-u)/sig)**2)/(sig*math.sqrt(2*math.pi)),0,10)[0];self.assertAlmostEqual(float(gaussian_decay(t,tau,sig*math.sqrt(8*math.log(2)))),expected,10)
 def test_triangle_profile(self):
  v=12e-9;a=270e-9;d=.1e-9;r,f,total=delay_profile(d,v,a);self.assertEqual(f,0);self.assertAlmostEqual(total,2*math.sqrt(d/a));self.assertAlmostEqual(moved(total/2,d,v,a),d/2,22)
 def test_trapezoid_profile(self):
  v=12e-9;a=270e-9;d=8e-9;r,f,total=delay_profile(d,v,a);self.assertGreater(f,0);self.assertAlmostEqual(total,d/v+v/a);self.assertAlmostEqual(moved(total/2,-d,v,a),-d/2,22)
 def test_position_and_xy_finite(self):
  m=dict(METHOD_EXAMPLE,delay_sequence_s=[4e-9],sample_pattern_xy_m=[[.006,.006]]);h=ready(method=m);h.invoke('acquisition.start');h.step_clock(.001);self.assertGreater(h.optical_delay,0);self.assertLess(h.optical_delay,4e-9);self.assertAlmostEqual(float(np.linalg.norm(h.xy)),.002*.001,14);self.assertEqual(h._probe_pulses,0)
 def test_quantization(self):
  h=collect(method=dict(METHOD_EXAMPLE,delay_sequence_s=[1.001e-12]));a=h.points[0]['actual_delay_s']+h.plant['delay_zero_offset_s'];self.assertAlmostEqual(a/2.8e-15,round(a/2.8e-15),7);self.assertLessEqual(abs(h.points[0]['actual_delay_s']-1.001e-12),1.4e-15)
 def test_delay_not_acquisition_duration(self):
  h=collect(method=short_method());self.assertGreater(h.elapsed_s,.01);self.assertLess(h.points[0]['actual_delay_s'],1e-9)
 def test_paired_log_reference_formula(self):
  h=collect(method=short_method(averages=1));r=h.invoke('raw.read');expected=np.log10(np.array(r['off_sample'])/r['on_sample'])+np.log10(np.array(r['on_reference'])/r['off_reference']);np.testing.assert_allclose(expected,h.invoke('result.read')['points'][0]['delta_absorbance'],atol=1e-15)
 def test_single_probe_formula(self):
  h=collect(method=short_method(averages=1,reference_enabled=False));r=h.invoke('raw.read');self.assertEqual(r['off_reference'],[]);np.testing.assert_allclose(np.log10(np.array(r['off_sample'])/r['on_sample']),h.invoke('result.read')['points'][0]['delta_absorbance'])
 def test_chunk_invariance(self):
  a=ready(method=short_method());b=ready(method=short_method());a.invoke('acquisition.start');b.invoke('acquisition.start');a.step_clock(1)
  for _ in range(100):b.step_clock(.01)
  self.assertEqual(a.invoke('result.read'),b.invoke('result.read'));self.assertEqual(a.invoke('raw.read'),b.invoke('raw.read'));self.assertEqual(a._pump_pulses,b._pump_pulses)
 def test_no_clock_in_query(self):
  h=ready();h.invoke('acquisition.start');before=snap(h)
  for _ in range(5):h.invoke('result.read');h.invoke('process.measure');h.invoke('raw.read')
  self.assertEqual(snap(h),before)
 def test_exposure_and_volume(self):
  h=collect();r=h.invoke('product.read');self.assertEqual(r['pump_pulses'],120);self.assertEqual(r['probe_pulses'],240);self.assertAlmostEqual(r['pump_exposure_J'],6e-6,17);self.assertAlmostEqual(r['energy_residual_J'],0,17);self.assertEqual(r['volume_residual_m3'],0)
 def test_partial_pair_cancel(self):
  h=ready(method=short_method());h.invoke('acquisition.start');h.step_clock(h._next+1.0001/h.plant['laser_repetition_hz']);self.assertEqual(h._probe_pulses,1);self.assertEqual(h._pump_pulses,0);h.invoke('acquisition.stop');self.assertEqual(h.phase,'cancelled');self.assertEqual(h._energy,0);self.assertIsNotNone(h.invoke('result.read')['active_point'])
 def test_partial_data_cancel(self):
  h=ready(method=short_method());h.invoke('acquisition.start');h.step_clock(h._next+.0021);self.assertEqual(h._pairs,1);before=h.invoke('raw.read');h.invoke('acquisition.stop');h.step_clock(20);self.assertEqual(h._pairs,1);self.assertEqual(h.invoke('raw.read'),before);self.assertFalse(h.invoke('result.read')['complete'])
 def test_interlock_retains_exposure(self):
  h=ready(method=short_method());h.invoke('acquisition.start');h.step_clock(h._next+.0021);energy=h._energy;h.invoke('power.set',enabled=False);h.step_clock(10);self.assertEqual(h._energy,energy);self.assertEqual(h.phase,'cancelled')
 def test_scan_axis_and_shape(self):
  h=collect(method=dict(METHOD_EXAMPLE,scans=2));r=h.invoke('result.read');self.assertEqual(len(r['points']),12);self.assertEqual([p['scan_index'] for p in r['points']],[0]*6+[1]*6);self.assertEqual(len(r['points'][0]['delta_absorbance']),len(r['wavelength_m']))
 def test_frozen_run_settings(self):
  h=collect(method=short_method());old=h.invoke('result.read')['settings_json'];h.invoke('method.configure',**METHOD_EXAMPLE);self.assertEqual(h.invoke('result.read')['settings_json'],old)
 def test_saturation_null_not_fake_zero(self):
  h=collect(plant=dict(PLANT_EXAMPLE,adc_max_photoelectrons=100),method=short_method());p=h.invoke('result.read')['points'][0];self.assertTrue(all(v is None for v in p['delta_absorbance']));self.assertTrue(all(v==0 for v in p['valid_pairs']))
 def test_caller_fit_mono(self):
  h=UltrafastHeliosRuntime();r=h.invoke('kinetics.fit',**CAPS['kinetics.fit'][6]);self.assertAlmostEqual(r['lifetimes_s'][0]/150e-12,1.,7)
 def test_caller_fit_bi_signed(self):
  t=np.linspace(-.3e-12,2e-9,180);y=.006*gaussian_decay(t,80e-12,80e-15)-.004*gaussian_decay(t,500e-12,80e-15)+.0002;r=fit_kinetics(dict(delay_s=t.tolist(),delta_absorbance=y.tolist(),irf_fwhm_s=80e-15,time_zero_s=0.,initial_lifetimes_s=[60e-12,600e-12]));np.testing.assert_allclose(r['lifetimes_s'],[80e-12,500e-12],rtol=1e-5);np.testing.assert_allclose(r['amplitudes'],[.006,-.004],rtol=1e-5)
 def test_caller_fit_not_hidden_truth(self):
  h=ready();h.sample['lifetimes_s']=[999e-12];a=h.invoke('kinetics.fit',**CAPS['kinetics.fit'][6]);h.sample=None;b=h.invoke('kinetics.fit',**CAPS['kinetics.fit'][6]);self.assertEqual(a,b)
 def test_degenerate_fit_rejected(self):
  a=copy.deepcopy(CAPS['kinetics.fit'][6]);a['delta_absorbance']=[0.]*80;self.rejected_unchanged(ready(),'kinetics.fit','ANALYSIS_INPUT',**a)
 def test_session_permission(self):
  h=ready();s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='a');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
  with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
 def test_session_idempotency(self):
  h=ready();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='x');r=s.invoke(**a);h.step_clock(.001);r2=s.invoke(**a);self.assertEqual(r,r2);self.assertEqual(h._op_count,1)
 def test_session_request_conflict(self):
  h=ready();s=h.session();s.invoke(instance_id=h.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='x');r=s.invoke(instance_id=h.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='x');self.assertEqual(r['error']['code'],'REQUEST_CONFLICT');self.assertTrue(h.powered)
 def test_session_owner_cancel(self):
  h=ready();a=h.session();r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='x');h.register_principal('b');b=h.session('b')
  with self.assertRaises(ActionRejected):b.poll(r['operation_id'])
  a.cancel(r['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled')
 def test_slot_schemas(self):
  h=collect()
  for k,v in h.read('evaluator').items():validate(SLOTS[k][0],v)
 def test_capability_schemas(self):
  h=collect()
  for k in ['result.read','raw.read','process.measure','product.read','template.read','kinetics.fit']:
   r=h.invoke(k,**CAPS[k][6]);validate(CAPS[k][1],r)
 def test_closed_sample_replacement_atomic(self):
  self.rejected_unchanged(ready(),'sample.load','EXTERNAL_LOAD_REQUIRED',**SAMPLE_EXAMPLE)
 def test_sample_volume_replacement(self):
  h=ready();h.invoke('sample.cover',opened=True);h.invoke('sample.load',**dict(SAMPLE_EXAMPLE,volume_m3=2e-6));l=h.invoke('product.read');self.assertAlmostEqual(l['registered_volume_m3'],3e-6);self.assertAlmostEqual(l['removed_volume_m3'],1e-6);self.assertAlmostEqual(l['volume_residual_m3'],0,20)
 def test_fit_rejects_constant_baseline_and_unused_exponential(self):
  a=copy.deepcopy(CAPS['kinetics.fit'][6]);a['delta_absorbance']=[.1]*len(a['delay_s']);self.rejected_unchanged(UltrafastHeliosRuntime(),'kinetics.fit','ANALYSIS_INPUT',**a)
  a=copy.deepcopy(CAPS['kinetics.fit'][6]);a['initial_lifetimes_s']=[100e-12,500e-12];self.rejected_unchanged(UltrafastHeliosRuntime(),'kinetics.fit','FIT_UNIDENTIFIABLE',**a)
 def test_fit_scaled_observations_preserve_lifetime_and_amplitude(self):
  h=UltrafastHeliosRuntime();a=copy.deepcopy(CAPS['kinetics.fit'][6]);original=h.invoke('kinetics.fit',**a);scale=1e-20;a['delta_absorbance']=(np.asarray(a['delta_absorbance'])*scale).tolist();before=snap(h);tiny=h.invoke('kinetics.fit',**a)
  self.assertAlmostEqual(tiny['lifetimes_s'][0]/150e-12,1.,7);self.assertAlmostEqual(tiny['amplitudes'][0]/scale/original['amplitudes'][0],1.,7);self.assertAlmostEqual(tiny['baseline']/scale/original['baseline'],1.,7);self.assertLess(tiny['relative_rmse'],1e-9);self.assertEqual(snap(h),before);json.dumps(tiny,allow_nan=False)
 def test_zero_step_preserves_due_move_and_revision(self):
  h=ready(plant=dict(PLANT_EXAMPLE,delay_zero_offset_s=0.),method=short_method(delay_sequence_s=[0.]));h.invoke('acquisition.start');self.assertEqual(h._next,h.time_s);before=snap(h)
  h.step(0);h.step_clock(0);self.assertEqual(snap(h),before);h.step(.0005);self.assertEqual(h.stage,'counting');self.assertEqual(h._probe_pulses,0)
 def test_unknown_visibility_rejects_without_returning_private_ledger(self):
  h=ready();before=snap(h)
  for visibility in ['other','Agent','',None]:
   with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):h.read(visibility)
  self.assertEqual(snap(h),before);self.assertNotIn('truth.ledger',h.read('agent'));self.assertIn('truth.ledger',h.read('evaluator'))
 def test_average_observation_window_covers_complete_on_off_pairs(self):
  h=ready(method=short_method(averages=3));h.invoke('acquisition.start');movement_end=h._next;h.step(movement_end+.0061);before=snap(h);obs=h.session().read(instance_id=h.instance_id,state_ids=['scan.delta_absorbance_center'])['observations']['scan.delta_absorbance_center']
  np.testing.assert_allclose(obs['sample_window_s'],[movement_end+.001,movement_end+.006],atol=1e-15);self.assertAlmostEqual(obs['sample_time_s'],movement_end+.006);self.assertIsNotNone(obs['value']);self.assertEqual(snap(h),before)
 def test_unobserved_next_point_does_not_inherit_old_average_timestamp(self):
  h=ready(method=short_method(averages=2,delay_sequence_s=[100e-12,500e-12]));h.invoke('acquisition.start');h.step(h._next+.0041);self.assertEqual(len(h.points),1);self.assertEqual(h.active['pairs'],0)
  observations=h.session().read(instance_id=h.instance_id,state_ids=['scan.delta_absorbance_center','scan.last_sample_time_s'])['observations'];average=observations['scan.delta_absorbance_center']
  self.assertIsNone(average['value']);self.assertIsNone(average['sample_window_s']);self.assertEqual(average['sample_time_s'],h.time_s);self.assertLess(observations['scan.last_sample_time_s']['value'],average['sample_time_s'])
 def test_historical_terminal_summary_keeps_window_and_owner(self):
  h=ready(method=short_method());h.register_principal('other');s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='first')['operation_id'];h.step(1);terminal=s.poll(op);self.assertEqual(terminal['status'],'succeeded');self.assertEqual(terminal['result']['elapsed_s'],h.elapsed_s)
  self.assertEqual(terminal['observations']['scan.delta_absorbance_center'],s.read(instance_id=h.instance_id,state_ids=['scan.delta_absorbance_center'])['observations']['scan.delta_absorbance_center'])
  h.invoke('method.configure',**short_method(wavelength_step_m=4e-9));s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='second');before=pickle.dumps(h.debug_snapshot());self.assertEqual(s.poll(op),terminal);terminal['result']['pairs']=-1;self.assertEqual(s.poll(op)['result']['pairs'],3)
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):h.session('other').poll(op)
  self.assertNotIn(SAMPLE_EXAMPLE['sample_id'],json.dumps(s.poll(op)));self.assertNotIn(SAMPLE_EXAMPLE['template_id'],json.dumps(s.poll(op)));self.assertEqual(pickle.dumps(h.debug_snapshot()),before)
 def test_cancel_old_terminal_keeps_current_exposure_and_request_identity(self):
  h=ready(method=short_method());s=h.session();old=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='old')['operation_id'];h.step(1);current=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='new')['operation_id'];h.step(.0021);ledger=h._ledger();revision=h.revision
  reply=s.cancel(old,'cancel-old');self.assertEqual(reply['result']['phase'],'succeeded');self.assertEqual(h.phase,'running');self.assertEqual(h.operation_id,current);self.assertEqual(h._ledger(),ledger);self.assertEqual(h.revision,revision);self.assertEqual(s.cancel(old,'cancel-old'),reply)
  with self.assertRaisesRegex(ActionRejected,'REQUEST_CONFLICT'):s.cancel(current,'cancel-old')
  self.assertEqual(h.phase,'running')
 def test_cancelled_unpaired_probe_history_does_not_invent_pump_or_pair(self):
  h=ready(method=short_method());s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='partial')['operation_id'];h.step(h._next+.0011);s.cancel(op,'cancel');terminal=s.poll(op);ledger=h._ledger()
  self.assertEqual(terminal['status'],'cancelled');self.assertEqual(terminal['result']['pairs'],0);self.assertEqual(ledger['probe_pulses'],1);self.assertEqual(ledger['pump_pulses'],0);self.assertEqual(ledger['pump_exposure_J'],0.);self.assertIsNone(terminal['observations']['scan.delta_absorbance_center']['value']);h.step(1);self.assertEqual(h._ledger(),ledger)
  s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='next');self.assertEqual(s.poll(op),terminal)
 def test_metadata_matches_configure_initialization_and_motion(self):
  h=ready(method=short_method());m=export_ultrafast_helios_contract_metadata();caps=m['capabilities'];self.assertTrue(all(e['timing']=='immediate' for e in caps['method.configure']['state_effects']));self.assertNotIn('delay.position_m',{e['state_id'] for e in caps['method.configure']['state_effects']})
  before=h.read('evaluator');h.invoke('acquisition.start');started=h.read('evaluator');immediate={e['state_id'] for e in caps['acquisition.start']['state_effects'] if e['timing']=='immediate'};later={e['state_id'] for e in caps['acquisition.start']['state_effects'] if e['timing']=='on_step'}
  self.assertLessEqual({key for key in before if before[key]!=started[key]},immediate);self.assertNotIn('delay.optical_s',immediate);self.assertNotIn('scan.pump_exposure_j',immediate);h.step(.001);moved_state=h.read('evaluator');self.assertLessEqual({key for key in started if started[key]!=moved_state[key]},later);self.assertFalse(moved_state['laser.emitting']);self.assertEqual(moved_state['scan.pump_exposure_j'],0.)
  self.assertEqual(caps['kinetics.fit']['observes'],[]);self.assertEqual(caps['raw.read']['observes'],[]);self.assertEqual(m['state_slots']['scan.delta_absorbance_center']['sampling']['mode'],'interval_mean')
def _templates_for_test():return UltrafastHeliosRuntime().templates
if __name__=='__main__':unittest.main()
