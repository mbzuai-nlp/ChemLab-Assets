"""Finite independent CPL/PL oracles, detector statistics, motion and protocol checks."""
import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.jasco_cpl300 import *

def toy_library():
 record=dict(name='private_oracle_fluorophore',conditions=dict(solvent='basic ethanol',temperature_k=None,concentration_mol_m3=None,reference_excitation_m=400e-9),pl=dict(wavelength_m=[450e-9,500e-9,520e-9,540e-9,600e-9,900e-9,1000e-9],emission_relative=[0.,.2,1.,.6,0.,.8,0.],source_bandwidth_m=5e-9,evidence='independent_test_PL'),absorption=dict(wavelength_m=[250e-9,350e-9,400e-9,450e-9,500e-9,800e-9],epsilon_m2_mol=[0.,.5,1.,.5,0.,0.],source_bandwidth_m=5e-9,evidence='independent_test_absorption'),cpl=None)
 ref=dict(name='one_material_left_configuration',raw_cpl_curve=dict(wavelength_m=[500e-9,520e-9],raw_cpl_mdeg=[1.,2.],cpl_display_angle_rad=[math.pi/180000,2*math.pi/180000],unit='rad',quantity='instrument_cpl_display_angle_not_delta_I'),cpl_glum_curve=dict(wavelength_m=[500e-9,520e-9],glum=[.01,.02],unit='1'),pl=None,cpl=None)
 return dict(compounds={'pcad_032':record},cpl_references={'sfeg_blue_l':ref})

def host(noise=False,library=None,**plant):
 h=JascoCpl300Runtime(library=toy_library() if library is None else library);h.invoke('plant.configure',**dict(PLANT_EXAMPLE,poisson_noise=noise,**plant));return h

def configure(h,method=None,sample=None):
 h.invoke('sample.load',**(sample or BLANK_EXAMPLE));h.invoke('method.configure',**(method or METHOD_EXAMPLE));h.invoke('cover.set',opened=False);h.invoke('power.set',enabled=True);h.invoke('lamp.set',enabled=True);h.step(h.plant['lamp_warmup_s']);return h

def finish(h):
 for _ in range(10001):
  if h.phase!='running':return h
  h.step(max(0.,h._due-h.time_s))
 raise AssertionError('finite fixture did not terminate')

def blank(h,method=None):configure(h,method);h.invoke('blank.start');return finish(h)
def load_sample(h,sample=None):
 h.invoke('cover.set',opened=True);h.invoke('sample.unload',recipient_id='external_rack');h.invoke('sample.load',**(sample or SAMPLE_EXAMPLE));h.invoke('cover.set',opened=False);return h

def ready(noise=False,method=None,**plant):return load_sample(blank(host(noise,**plant),method))
def run(h):h.invoke('acquisition.start');finish(h);return h.invoke('result.read')
def same(a,b):
 if isinstance(a,np.ndarray):return isinstance(b,np.ndarray) and np.array_equal(a,b)
 if isinstance(a,dict):return a.keys()==b.keys() and all(same(v,b[k]) for k,v in a.items())
 if isinstance(a,(tuple,list)):return len(a)==len(b) and all(same(v,w) for v,w in zip(a,b))
 return a==b

class Cpl300PhysicsTests(unittest.TestCase):
 def rejected_atomic(self,h,cap,code=None,**args):
  before=h.debug_snapshot()
  with self.assertRaises(ActionRejected) as ctx:h.invoke(cap,**args)
  if code:self.assertEqual(str(ctx.exception),code)
  self.assertTrue(same(before,h.debug_snapshot()),str(ctx.exception))
 def test_total_difference_and_glum_are_independent_oracle(self):
  h=ready();r=run(h);self.assertEqual(len(r['points']),5)
  expected=np.interp([p['requested_wavelength_m'] for p in r['points']],[500e-9,520e-9,540e-9],[.2,1.,.6])
  for p,pl in zip(r['points'],expected):
   self.assertAlmostEqual(p['pl'],pl);self.assertAlmostEqual(p['cpl'],.02*pl);self.assertAlmostEqual(p['glum'],.04);self.assertAlmostEqual(p['left_relative'],.51*pl);self.assertAlmostEqual(p['right_relative'],.49*pl)
 def test_manufacturer_half_sum_does_not_double_glum(self):
  h=host();r=h.invoke('glum.compute',left_relative=6.,right_relative=4.,net_counts=1000.,minimum_net_counts=25.)
  self.assertEqual(r['pl'],10.);self.assertEqual(r['cpl'],2.);self.assertAlmostEqual(r['glum'],.4)
 def test_glum_sign_and_pure_circular_boundaries(self):
  for g in [-2.,0.,2.]:
   h=load_sample(blank(host()),dict(SAMPLE_EXAMPLE,synthetic_glum=g));r=run(h)
   for p in r['points']:self.assertAlmostEqual(p['glum'],g,places=11)
 def test_zero_signal_and_low_counts_are_undefined(self):
  h=load_sample(blank(host(blank_pl_relative=0.,dark_counts_s=0.)),dict(SAMPLE_EXAMPLE,peak_pl_relative=0.));r=run(h)
  self.assertTrue(all(p['glum'] is None and p['glum_quality']=='nonpositive_pl' for p in r['points']))
  r=run(ready(counts_per_relative_unit_s=10.));self.assertTrue(all(p['glum'] is None and p['glum_quality']=='low_net_counts' for p in r['points']))
 def test_negative_blank_corrected_channel_not_clipped(self):
  h=host();r=h.invoke('glum.compute',left_relative=1.,right_relative=-.1,net_counts=1000.,minimum_net_counts=25.)
  self.assertIsNone(r['glum']);self.assertEqual(r['quality'],'outside_physical_range');self.assertEqual(r['cpl'],1.1)
 def test_glum_finite_extremes_and_overflow_reject(self):
  h=host();r=h.invoke('glum.compute',left_relative=1e-300,right_relative=5e-301,net_counts=100.,minimum_net_counts=25.);self.assertAlmostEqual(r['glum'],2/3)
  self.rejected_atomic(h,'glum.compute','ANALYSIS_RANGE',left_relative=1e308,right_relative=1e308,net_counts=1e3,minimum_net_counts=25.)
 def test_blank_correction_removes_host_background_and_dark_mean(self):
  h=ready(blank_pl_relative=.2,blank_glum=-.3,dark_counts_s=1000.);r=run(h)
  for p in r['points']:self.assertAlmostEqual(p['glum'],.04)
  self.assertAlmostEqual(r['points'][2]['pl'],1.)
 def test_blank_required_and_partial_blank_never_valid(self):
  h=configure(host(),sample=SAMPLE_EXAMPLE);self.rejected_atomic(h,'acquisition.start','MATCHED_BLANK_REQUIRED')
  h=configure(host());h.invoke('blank.start');h.step(1.56);h.invoke('acquisition.stop');self.assertFalse(h.read()['blank.valid']);self.assertIsNone(h.invoke('blank.read')['result']);self.assertEqual(len(h._points),1)
 def test_wrong_sample_kind_rejects_without_mutation(self):
  h=configure(host());self.rejected_atomic(h,'acquisition.start','WRONG_SAMPLE_KIND');h=ready();self.rejected_atomic(h,'blank.start','WRONG_SAMPLE_KIND')
 def test_method_change_preserves_result_and_invalidates_blank(self):
  h=ready();r=run(h);h.invoke('method.configure',**dict(METHOD_EXAMPLE,response_s=.04));self.assertEqual(h.invoke('result.read'),r);self.assertFalse(h.read()['blank.valid']);self.rejected_atomic(h,'acquisition.start','MATCHED_BLANK_REQUIRED')
 def test_power_or_lamp_off_invalidates_blank(self):
  for cap in ['power.set','lamp.set']:
   h=ready();h.invoke(cap,enabled=False);self.assertFalse(h.read()['blank.valid']);self.assertFalse(h.shutter)
 def test_volume_and_solute_inventory_independent_of_measurement(self):
  h=ready();old=h._ledger();run(h);new=h._ledger();self.assertEqual(old['current_volume_m3'],new['current_volume_m3']);self.assertEqual(old['current_solute_mol'],new['current_solute_mol']);h.invoke('cover.set',opened=True);h.invoke('sample.unload',recipient_id='rack');l=h._ledger();self.assertAlmostEqual(l['volume_residual_m3'],0.,places=20);self.assertAlmostEqual(l['solute_residual_mol'],0.,places=20)
 def test_concentration_is_inventory_not_invented_pl_calibration(self):
  spectra=[]
  for c in [.0001,.001]:spectra.append(run(load_sample(blank(host()),dict(SAMPLE_EXAMPLE,concentration_mol_m3=c))))
  self.assertEqual([p['pl'] for p in spectra[0]['points']],[p['pl'] for p in spectra[1]['points']])
 def test_sample_ack_and_source_solvent_gates(self):
  h=host()
  for args,code in [({'accept_synthetic_cpl':False},'SYNTHETIC_CPL_ACK_REQUIRED'),({'accept_unknown_conditions':False},'UNKNOWN_CONDITIONS_ACK_REQUIRED'),({'solvent':'water'},'SOURCE_CONDITIONS_MISMATCH')]:self.rejected_atomic(h,'sample.load',code,**dict(SAMPLE_EXAMPLE,**args))
 def test_occupied_cell_requires_unload(self):
  h=host();h.invoke('sample.load',**BLANK_EXAMPLE);self.rejected_atomic(h,'sample.load','UNLOAD_REQUIRED',**SAMPLE_EXAMPLE)
 def test_manual_sample_requires_open_cover(self):
  h=ready();self.rejected_atomic(h,'sample.unload','COVER_MUST_BE_OPEN',recipient_id='rack')
 def test_lamp_warmup_advances_only_on_step(self):
  h=host();h.invoke('sample.load',**BLANK_EXAMPLE);h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('cover.set',opened=False);h.invoke('power.set',enabled=True);h.invoke('lamp.set',enabled=True);self.rejected_atomic(h,'blank.start','OPTICS_NOT_READY');h.step(.05);self.assertFalse(h._ready());h.step(.05);self.assertTrue(h._ready())
 def test_shutter_interlocks_and_two_shared_scene_states(self):
  h=host();self.rejected_atomic(h,'shutter.set','OPTICS_NOT_READY',opened=True);h=ready();h.invoke('shutter.set',opened=True);self.assertEqual(h.read()['shutter.angle_rad'],math.pi/2);h.invoke('cover.set',opened=True);self.assertFalse(h.shutter)
 def test_finite_simultaneous_prism_repositioning(self):
  h=configure(host());h.invoke('blank.start');before=h.read();self.assertEqual(before['optics.excitation_m'],250e-9);h.step(.75);self.assertAlmostEqual(h.excitation,325e-9);self.assertAlmostEqual(h.emission,375e-9);self.assertEqual(h.stage,'moving');self.assertFalse(h.shutter)
 def test_step_gate_holds_wavelength_and_response_window(self):
  h=ready();h.invoke('acquisition.start');h.step(.24);self.assertEqual(h.stage,'integrating');self.assertAlmostEqual(h.emission,500e-9);h.step(.05);p=h._points[0];self.assertEqual(p['wavelength_window_m'],[500e-9,500e-9]);self.assertAlmostEqual(p['sample_window_s'][1]-p['sample_window_s'][0],.05)
 def test_continuous_gate_uses_trailing_window_and_exact_linear_average(self):
  m=dict(METHOD_EXAMPLE,scan_mode='continuous',scan_speed_m_s=100e-9,response_s=.05);h=ready(method=m);r=run(h);p=r['points'][1]
  np.testing.assert_allclose(p['wavelength_window_m'],[505e-9,510e-9],atol=1e-20);self.assertAlmostEqual(p['emission_m'],507.5e-9);self.assertAlmostEqual(p['pl'],.5);self.assertAlmostEqual(p['glum'],.04)
 def test_continuous_gate_integrates_an_interior_spectral_peak(self):
  m=dict(METHOD_EXAMPLE,scan_mode='continuous',start_m=490e-9,stop_m=530e-9,interval_m=40e-9,scan_speed_m_s=100e-9,response_s=.3)
  r=run(ready(method=m));p=r['points'][1]
  np.testing.assert_allclose(p['wavelength_window_m'],[500e-9,530e-9],atol=1e-20)
  self.assertAlmostEqual(p['pl'],(.6*20+.9*10)/30)
 def test_continuous_and_step_are_not_identical_sampling(self):
  step=run(ready());cont=run(ready(method=dict(METHOD_EXAMPLE,scan_mode='continuous',scan_speed_m_s=100e-9,response_s=.05)));self.assertNotAlmostEqual(step['points'][1]['pl'],cont['points'][1]['pl'],places=10)
 def test_continuous_overlap_and_short_final_interval_reject(self):
  h=host();self.rejected_atomic(h,'method.configure','RESPONSE_OVERLAPS_INTERVAL',**dict(METHOD_EXAMPLE,scan_mode='continuous',response_s=.1));self.rejected_atomic(h,'method.configure','RESPONSE_OVERLAPS_INTERVAL',**dict(METHOD_EXAMPLE,scan_mode='continuous',stop_m=541e-9,response_s=.05))
 def test_excitation_scan_uses_absorption_proxy_not_emission_axis(self):
  m=dict(METHOD_EXAMPLE,scan_axis='excitation',start_m=350e-9,stop_m=450e-9,interval_m=25e-9,emission_m=520e-9);r=run(ready(method=m));np.testing.assert_allclose([p['pl'] for p in r['points']],[.5,.75,1.,.75,.5],atol=1e-15)
 def test_kasha_approximation_requires_explicit_host_acceptance(self):
  h=ready(accept_kasha_model=False);self.rejected_atomic(h,'acquisition.start','KASHA_ACK_REQUIRED')
 def test_unsupported_detector_band_and_excitation_bounds(self):
  h=host();self.rejected_atomic(h,'method.configure','OPTION_NOT_INSTALLED',**dict(METHOD_EXAMPLE,detector_band='extended'))
  self.rejected_atomic(h,'method.configure','DETECTOR_WINDOW',**dict(METHOD_EXAMPLE,start_m=840e-9,stop_m=900e-9))
  self.rejected_atomic(h,'method.configure','EXCITATION_WINDOW',**dict(METHOD_EXAMPLE,scan_axis='excitation',start_m=800e-9,stop_m=900e-9,emission_m=1000e-9))
 def test_extended_pmt_range_requires_option_and_excludes_blue(self):
  h=host(extended_pmt_installed=True);self.rejected_atomic(h,'method.configure','DETECTOR_WINDOW',**dict(METHOD_EXAMPLE,excitation_m=250e-9,start_m=350e-9,stop_m=450e-9,detector_band='extended'))
  h=ready(method=dict(METHOD_EXAMPLE,start_m=880e-9,stop_m=940e-9,interval_m=15e-9,detector_band='extended'),extended_pmt_installed=True);self.assertEqual(run(h)['phase'],'succeeded')
 def test_anti_stokes_regime_is_explicitly_unsupported(self):
  h=host();self.rejected_atomic(h,'method.configure','STOKES_MODEL_REQUIRED',**dict(METHOD_EXAMPLE,excitation_m=520e-9))
 def test_source_bandwidth_and_range_guards(self):
  h=ready(method=dict(METHOD_EXAMPLE,em_bandwidth_m=1e-9));self.rejected_atomic(h,'acquisition.start','SOURCE_BANDWIDTH_LIMIT')
  h=ready(method=dict(METHOD_EXAMPLE,start_m=420e-9,stop_m=460e-9));self.rejected_atomic(h,'acquisition.start','SOURCE_WAVELENGTH_RANGE')
 def test_source_negative_baseline_only_clipped_in_forward_model(self):
  lib=toy_library();lib['compounds']['pcad_032']['pl']['emission_relative'][1]=-.2;h=load_sample(blank(host(library=lib)));r=run(h);self.assertAlmostEqual(r['points'][0]['pl'],0.);source=json.loads(h.invoke('template.read',substance_id='pcad_032')['template_json']);self.assertEqual(source['pl']['emission_relative'][1],-.2)
 def test_additional_bandwidth_broadens_without_deconvolution(self):
  lib=toy_library();x=np.arange(450e-9,601e-9,1e-9);lib['compounds']['pcad_032']['pl'].update(wavelength_m=x.tolist(),emission_relative=np.exp(-.5*((x-520e-9)/1e-9)**2).tolist());h=load_sample(blank(host(library=lib),dict(METHOD_EXAMPLE,em_bandwidth_m=20e-9)));r=run(h);self.assertLess(r['points'][2]['pl'],.2)
 def test_poisson_category_draws_and_detector_partition_oracle(self):
  h=configure(host(noise=True));h.invoke('blank.start');finish(h);rng=np.random.default_rng(78);expected=[]
  for _ in range(5):
   counts=rng.poisson([0.,0.,1250.,1250.,.5,.5]);expected.append((float(counts[0]+counts[2]+counts[4]),float(counts[1]+counts[3]+counts[5])))
  self.assertEqual([(p['raw_left_counts'],p['raw_right_counts']) for p in h._points],expected);self.assertEqual(h._ledger()['detector_partition_residual'],0.)
 def test_sampling_noise_partition_independent_of_step_size(self):
  h=ready(noise=True);g=ready(noise=True);h.invoke('acquisition.start');g.invoke('acquisition.start');h.step(1.)
  for _ in range(100):g.step(.01)
  self.assertEqual([(p['raw_left_counts'],p['raw_right_counts']) for p in h._points],[(p['raw_left_counts'],p['raw_right_counts']) for p in g._points]);self.assertEqual(g.phase,'succeeded')
 def test_cancel_partial_gate_does_not_fabricate_detected_events(self):
  h=ready();ledger=copy.deepcopy(h._ledger());h.invoke('acquisition.start');h.step(.26);self.assertEqual(len(h._points),0);h.invoke('acquisition.stop');self.assertEqual(h._ledger(),ledger);old=h.elapsed_s;h.step(10.);self.assertEqual(h.elapsed_s,old)
 def test_completed_gates_survive_cancellation(self):
  h=ready();h.invoke('acquisition.start');h.step(.31);self.assertEqual(len(h._points),1);h.invoke('acquisition.stop');r=h._result();h.step(10.);self.assertEqual(h._result(),r);self.assertAlmostEqual(h._ledger()['completed_gate_s'],.3)
 def test_manual_idle_open_shutter_not_claimed_as_completed_gate(self):
  h=ready();h.invoke('shutter.set',opened=True);old=h._ledger();h.step(5.);self.assertEqual(h._ledger(),old);self.assertIn('idle-open-shutter',old['evidence'])
 def test_all_interrupts_close_shutters_and_keep_completed_points(self):
  for cap,args in [('cover.set',{'opened':True}),('power.set',{'enabled':False}),('lamp.set',{'enabled':False}),('shutter.set',{'opened':False})]:
   h=ready();h.invoke('acquisition.start');h.step(.31);h.invoke(cap,**args);self.assertEqual(h.phase,'cancelled');self.assertFalse(h.shutter);self.assertEqual(len(h._points),1)
 def test_busy_method_is_atomic(self):
  h=ready();h.invoke('acquisition.start');self.rejected_atomic(h,'method.configure','BUSY',**METHOD_EXAMPLE)
 def test_resource_and_nonfinite_settings_reject_atomically(self):
  h=host()
  for value in [float('nan'),float('inf'),0.,True]:self.rejected_atomic(h,'method.configure','INVALID_ARGUMENT',**dict(METHOD_EXAMPLE,response_s=value))
  self.rejected_atomic(h,'method.configure','EVENT_RESOURCE_LIMIT',**dict(METHOD_EXAMPLE,start_m=420e-9,stop_m=850e-9,interval_m=.025e-9))
  self.rejected_atomic(h,'method.configure','SPECTRAL_GRID',**dict(METHOD_EXAMPLE,stop_m=500e-9))
 def test_queries_and_zero_step_are_pure(self):
  h=ready(noise=True);run(h);snap=h.debug_snapshot();h.step(0.)
  for k in ['process.measure','result.read','raw.read','blank.read','product.read']:h.invoke(k)
  h.invoke('template.read',substance_id='pcad_032');h.invoke('reference.read',reference_id='sfeg_blue_l');h.invoke('result.export',format='csv');h.describe();h.read();self.assertTrue(same(snap,h.debug_snapshot()))
 def test_reference_query_preserves_display_angle_not_delta_intensity(self):
  h=host();r=json.loads(h.invoke('reference.read',reference_id='sfeg_blue_l')['reference_json']);self.assertEqual(r['raw_cpl_curve']['quantity'],'instrument_cpl_display_angle_not_delta_I');self.assertIsNone(r['pl']);self.assertIsNone(r['cpl'])
 def test_actual_source_default_and_real_reference_replay(self):
  h=JascoCpl300Runtime();h.invoke('plant.configure',**dict(PLANT_EXAMPLE,poisson_noise=False));blank(h);load_sample(h);r=run(h);self.assertTrue(r['complete']);self.assertEqual(r['provenance']['cpl_evidence'],'synthetic');self.assertIsNone(r['provenance']['source_temperature_k']);reference=json.loads(h.invoke('reference.read',reference_id='sfeg_blue_l')['reference_json']);self.assertIn('raw_cpl_curve',reference)

class Cpl300ProtocolTests(unittest.TestCase):
 def sessions(self):
  h=ready();h.register_principal('host','evaluator');h.register_principal('other');return h,h.session(),h.session('host'),h.session('other')
 def invoke(self,s,h,cap,args=None,rid='unique'):return s.invoke(instance_id=h.instance_id,capability_id=cap,arguments=args or {},request_id=rid)
 def test_private_capabilities_and_truth_are_filtered(self):
  h,a,e,o=self.sessions();snap=h.debug_snapshot();r=self.invoke(a,h,'sample.load',SAMPLE_EXAMPLE);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertTrue(same(snap,h.debug_snapshot()));self.assertNotIn('truth.ledger',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.ledger',e.read(instance_id=h.instance_id)['observations'])
  with self.assertRaises(ActionRejected):a.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
  with self.assertRaises(ActionRejected):h.read('administrator')
 def test_agent_cannot_self_register_via_arguments(self):
  h,a,e,o=self.sessions();r=self.invoke(a,h,'power.set',{'enabled':True,'visibility':'evaluator'});self.assertEqual(r['status'],'rejected');self.assertEqual(r['error']['side_effects'],'none')
 def test_no_hidden_sample_identity_in_observations_or_exports(self):
  h,a,e,o=self.sessions();run(h)
  for cap in ['process.measure','result.read','raw.read','result.export']:
   r=self.invoke(a,h,cap,{'format':'json'} if cap=='result.export' else {},rid=cap);text=json.dumps(r)
   for hidden in ['private_oracle_fluorophore','external_cpl_cell','pcad_032']:self.assertNotIn(hidden,text)
 def test_idempotent_start_and_conflicting_request(self):
  h,a,e,o=self.sessions();r=self.invoke(a,h,'acquisition.start');snap=h.debug_snapshot();self.assertEqual(self.invoke(a,h,'acquisition.start'),r);self.assertTrue(same(snap,h.debug_snapshot()));bad=self.invoke(a,h,'acquisition.stop');self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT')
 def test_terminal_result_and_windows_survive_later_operation(self):
  h,a,e,o=self.sessions();op=self.invoke(a,h,'acquisition.start')['operation_id'];finish(h);old=a.poll(op);self.assertEqual(old['result'],h._result());self.invoke(a,h,'acquisition.start',rid='next');h.step(.3);self.assertEqual(a.poll(op),old)
  with self.assertRaises(ActionRejected):o.poll(op)
 def test_cancel_old_terminal_never_cancels_current(self):
  h,a,e,o=self.sessions();old=self.invoke(a,h,'acquisition.start')['operation_id'];finish(h);new=self.invoke(a,h,'acquisition.start',rid='next')['operation_id'];a.cancel(old,'cancel-old');self.assertEqual(h.operation_id,new);self.assertEqual(h.phase,'running')
 def test_cancellation_snapshot_matches_current_read(self):
  h,a,e,o=self.sessions();op=self.invoke(a,h,'acquisition.start')['operation_id'];h.step(.31);a.cancel(op,'cancel');p=a.poll(op);obs=a.read(instance_id=h.instance_id)['observations']
  for k in ['scan.pl','scan.cpl','scan.glum']:self.assertEqual(p['observations'][k],obs[k]);self.assertEqual(obs[k]['sample_window_s'],h._points[-1]['sample_window_s'])
 def test_new_run_observations_are_unavailable_without_stale_windows(self):
  h,a,e,o=self.sessions();run(h);self.invoke(a,h,'acquisition.start');obs=a.read(instance_id=h.instance_id)['observations']
  for k in ['scan.pl','scan.cpl','scan.glum']:self.assertIsNone(obs[k]['value']);self.assertIsNone(obs[k]['sample_window_s']);self.assertEqual(obs[k]['quality'],'unavailable')
 def test_observed_glum_null_keeps_actual_gate_window(self):
  h,a,e,o=self.sessions();h.invoke('cover.set',opened=True);h.invoke('sample.unload',recipient_id='rack');h.invoke('sample.load',**dict(SAMPLE_EXAMPLE,peak_pl_relative=0.));h.invoke('cover.set',opened=False);run(h);obs=a.read(instance_id=h.instance_id)['observations']['scan.glum'];self.assertIsNone(obs['value']);self.assertEqual(obs['sample_window_s'],h._points[-1]['sample_window_s'])
 def test_csv_null_and_provenance_roundtrip(self):
  h=ready(counts_per_relative_unit_s=10.);r=run(h);out=h.invoke('result.export',format='csv')['content'].splitlines();meta=json.loads(out[0][2:]);self.assertEqual(meta['provenance'],r['provenance']);self.assertEqual(len(out)-2,5);self.assertEqual(out[2].split(',')[7],'')
 def test_real_results_validate_against_schema(self):
  h=ready();run(h)
  for k in ['process.measure','result.read','raw.read','blank.read','product.read','result.export','template.read','reference.read','glum.compute']:validate(CAPS[k][1],h.invoke(k,**CAPS[k][6]))
  for k,value in h.read('evaluator').items():validate(SLOTS[k][0],value)
 def test_metadata_queries_effects_timing_and_version(self):
  h=ready();m=export_jasco_cpl300_contract_metadata();self.assertEqual(len(m['capabilities']),22);self.assertEqual(len(m['state_slots']),29);self.assertEqual(h.describe()['asset_version'],'1.0.1')
  for k,c in m['capabilities'].items():
   if c['kind'] in ['query','analysis'] or k in UNSUPPORTED:self.assertEqual(c['state_effects'],[])
   self.assertTrue(set(c['observes'])<=SLOTS.keys())
  self.assertFalse(any(x['state_id']=='optics.excitation_m' and x['timing']=='immediate' for x in m['capabilities']['method.configure']['state_effects']))
 def test_unsupported_capabilities_are_atomic(self):
  h,a,e,o=self.sessions()
  for k in UNSUPPORTED:
   snap=h.debug_snapshot();r=self.invoke(a,h,k,rid=k);self.assertEqual(r['status'],'unsupported');self.assertTrue(same(snap,h.debug_snapshot()))

REAL=None
def real_library():
 global REAL
 if REAL is None:REAL=JascoCpl300Runtime().library
 return REAL

def measured_run(template_id,solvent,ex,start,stop,interval,em_bw=1e-9,accept=True,noise=False,peak=1.,mode='step'):
 h=JascoCpl300Runtime(library=real_library());h.invoke('plant.configure',**dict(PLANT_EXAMPLE,poisson_noise=noise))
 method=dict(METHOD_EXAMPLE,excitation_m=ex,start_m=start,stop_m=stop,interval_m=interval,em_bandwidth_m=em_bw,ex_bandwidth_m=5e-9,scan_mode=mode)
 sample=dict(SAMPLE_EXAMPLE,kind='measured_cpl',template_id=template_id,solvent=solvent,synthetic_glum=0.,accept_synthetic_cpl=False,accept_unknown_conditions=accept,peak_pl_relative=peak)
 configure(h,method,dict(BLANK_EXAMPLE,solvent=solvent));h.invoke('blank.start');finish(h)
 load_sample(h,sample);return h,run(h)

class Cpl300MeasuredLiteratureTests(unittest.TestCase):
 """Real literature g_lum emitters (cpl_measured): sign per enantiomer, windows, conditions, curve replay."""
 @classmethod
 def setUpClass(cls):cls.lib=real_library();cls.m=cls.lib['cpl_measured']
 def test_library_counts_are_honest(self):
  c=self.lib['counts'];usable=[k for k,v in self.m.items() if v['runtime_enantiomers']]
  self.assertEqual(c['literature_glum_emitters'],len(self.m));self.assertEqual(c['runtime_usable_real_cpl_emitters'],len(usable));self.assertGreaterEqual(len(usable),50)
  self.assertEqual(len({v['name'] for v in self.m.values()}),len(self.m))
  for v in self.m.values():
   self.assertTrue(v['source']['doi'] and v['source']['raw_sha256']);self.assertIn(v['source']['licence_flag'],['permissive','non_commercial','licence_not_stated_in_cached_PMC_XML_RSC_open_access'])
   for cd in v['conditions']:
    for pts in cd['enantiomers'].values():
     for q in pts:self.assertTrue(q['quote'] and q['locator'] and -2<=q['glum']<=2 and q['digitized'] is False)
 def test_camphorquinone_source_curves_replay_both_enantiomers(self):
  for lab,sign in [('S',1),('R',-1)]:
   cv=self.m['cpl_lit_001']['curves'][lab];x=np.array(cv['wavelength_m']);g=np.array(cv['glum']);c=cv['pl_max_wavelength_m']
   h,r=measured_run('cpl_lit_001:'+lab,'any solvent (unknown in source)',450e-9,c-10e-9,c+10e-9,5e-9,em_bw=.1e-9)
   self.assertEqual(r['provenance']['glum_evidence'],'measured_literature');self.assertIn('source_data_curve',r['provenance']['pl_evidence'])
   for p in r['points']:
    self.assertEqual(np.sign(p['glum']),sign);self.assertAlmostEqual(p['glum'],float(np.interp(p['emission_m'],x,g)),delta=4e-4)
   pl=[p['pl'] for p in r['points']];self.assertAlmostEqual(max(pl),1.,delta=.06)
 def test_scalar_lanthanide_line_sign_per_enantiomer(self):
  for lab,g in [('RR',.09),('SS',-.09)]:
   h,r=measured_run('cpl_lit_035:'+lab,'acetonitrile',340e-9,597.5e-9,598.5e-9,.5e-9,em_bw=1e-9)
   self.assertEqual(len(r['points']),3)
   for p in r['points']:self.assertAlmostEqual(p['glum'],g,places=10);self.assertAlmostEqual(p['pl'],1.,places=10)
   self.assertEqual(r['provenance']['glum_evidence'],'measured_literature' if lab=='RR' else 'measured_literature_with_derived_sign')
 def test_sample_across_classes_noise_free(self):
  cases=[('cpl_lit_043:P','dichloromethane',None,420e-9,1.0e-3),('cpl_lit_045:PP','ethanol/dichloromethane 1:1',350e-9,769e-9,-.11),('cpl_lit_045:MM','ethanol/dichloromethane 1:1',350e-9,729e-9,-.08),
   ('cpl_lit_032:S','0.1 M HEPES buffer, pH 7.3',340e-9,595e-9,-.240),('cpl_lit_036:induced','(1R)-alpha-pinene',None,537e-9,.0044),('cpl_lit_036:induced','(1S)-alpha-pinene',None,537e-9,-.0049),
   ('cpl_lit_050:SS','dichloromethane',320e-9,382e-9,1.2e-3),('cpl_lit_050:RR','dichloromethane',320e-9,382e-9,-1.2e-3),('cpl_lit_022:gamma_CD_natural','water',280e-9,454e-9,-.019),
   ('cpl_lit_008:R','dichloromethane',355e-9,530e-9,-2.7e-3),('cpl_lit_017:pS','hexane',306e-9,422e-9,-6.10e-3),('cpl_lit_002:minus_first_eluted','acetonitrile',None,490e-9,.009),('cpl_lit_042:R','toluene',None,515e-9,-2.0e-3),('cpl_lit_042:R','dichloromethane',None,515e-9,1.9e-3)]
  for tid,solv,ex,lam,g in cases:
   with self.subTest(tid=tid,solvent=solv):
    h,r=measured_run(tid,solv,ex if ex else 300e-9,lam-.4e-9,lam+.4e-9,.4e-9,em_bw=.5e-9)
    for p in r['points']:self.assertAlmostEqual(p['glum'],g,places=10);self.assertEqual(p['glum_quality'],'valid')
 def test_every_runtime_record_replays_its_value(self):
  n=0
  for rid,v in self.m.items():
   for lab in v['runtime_enantiomers']:
    if v.get('curves'):continue
    cd=next(c for c in v['conditions'] if lab in c['runtime_enantiomers']);q=cd['enantiomers'][lab][0];lo,hi=q['validity_window_m'];c=(lo+hi)/2;d=min(.2e-9,(hi-lo)/4)
    ex=cd['excitation_m'] or max(250e-9,lo-60e-9)
    h,r=measured_run(f'{rid}:{lab}',cd['solvent'] or 'unknown',ex,c-d,c+d,d,em_bw=min(1e-9,hi-lo))
    hits=[p['glum'] for p in cd['enantiomers'][lab] if p['validity_window_m'][0]<=c-d and c+d<=p['validity_window_m'][1]]
    for p in r['points']:self.assertAlmostEqual(p['glum'],hits[0],places=10,msg=f'{rid}:{lab}')
    n+=1
  self.assertEqual(n,sum(len(v['runtime_enantiomers']) for v in self.m.values() if not v.get('curves')));self.assertGreaterEqual(n,80)
 def test_measured_poisson_noise_converges_to_literature_value(self):
  h,r=measured_run('cpl_lit_032:S','0.1 M HEPES buffer, pH 7.3',340e-9,590e-9,600e-9,1e-9,em_bw=5e-9,noise=True)
  g=np.array([p['glum'] for p in r['points']]);self.assertTrue(np.all(np.isfinite(g)));self.assertAlmostEqual(float(g.mean()),-.240,delta=.01)
 def test_measured_conditions_are_enforced(self):
  h=JascoCpl300Runtime(library=real_library());h.invoke('plant.configure',**PLANT_EXAMPLE)
  base=dict(SAMPLE_EXAMPLE,kind='measured_cpl',template_id='cpl_lit_035:RR',solvent='acetonitrile',synthetic_glum=0.,accept_synthetic_cpl=False,accept_unknown_conditions=True)
  for patch,code in [(dict(solvent='toluene'),'SOURCE_CONDITIONS_MISMATCH'),(dict(template_id='cpl_lit_035:PP'),'UNKNOWN_REFERENCE'),(dict(template_id='cpl_lit_035'),'UNKNOWN_REFERENCE'),(dict(synthetic_glum=.1),'SAMPLE_COMPOSITION'),(dict(accept_unknown_conditions=False),'UNKNOWN_CONDITIONS_ACK_REQUIRED'),(dict(template_id='cpl_lit_001:S',solvent='x',accept_unknown_conditions=False),'UNKNOWN_CONDITIONS_ACK_REQUIRED')]:
   with self.subTest(code=code):
    snap=h.debug_snapshot()
    with self.assertRaises(ActionRejected) as ctx:h.invoke('sample.load',**dict(base,**patch))
    self.assertEqual(str(ctx.exception),code);self.assertTrue(same(snap,h.debug_snapshot()))
  ok=dict(METHOD_EXAMPLE,excitation_m=340e-9,start_m=597.5e-9,stop_m=598.5e-9,interval_m=.5e-9,em_bandwidth_m=1e-9)
  for method,code in [(dict(ok,start_m=596e-9),'REFERENCE_WAVELENGTH_RANGE'),(dict(ok,excitation_m=360e-9),'SOURCE_CONDITIONS_MISMATCH'),(dict(ok,em_bandwidth_m=3e-9),'SOURCE_BANDWIDTH_LIMIT'),
                      (dict(ok,scan_axis='excitation',emission_m=598e-9,start_m=330e-9,stop_m=350e-9,interval_m=10e-9),'REFERENCE_AXIS_UNSUPPORTED')]:
   with self.subTest(code=code):
    h=JascoCpl300Runtime(library=real_library());h.invoke('plant.configure',**dict(PLANT_EXAMPLE,poisson_noise=False))
    configure(h,method,dict(BLANK_EXAMPLE,solvent='acetonitrile'));h.invoke('blank.start');finish(h);load_sample(h,base)
    snap=h.debug_snapshot()
    with self.assertRaises(ActionRejected) as ctx:h.invoke('acquisition.start')
    self.assertEqual(str(ctx.exception),code);self.assertTrue(same(snap,h.debug_snapshot()))
 def test_reference_read_exposes_measured_record_unchanged(self):
  h=JascoCpl300Runtime(library=real_library());r=h.invoke('reference.read',reference_id='cpl_lit_045')
  self.assertEqual(json.loads(r['reference_json']),self.m['cpl_lit_045'])

if __name__=='__main__':unittest.main()
