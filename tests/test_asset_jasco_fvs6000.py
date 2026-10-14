"""Independent finite oracles and protocol regressions for CHEM-077; no real hardware claims."""
import copy, json, math, sys, unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.jasco_fvs6000 import *

def toy_templates():
 x=[120000.,140000.,160000.,180000.,190000.]
 cond=dict(solvent='DMSO-d6',concentration_kg_m3=25.,path_length_m=100e-6,window_material='BaF2',is_aqueous=False,temperature_k=None)
 ir=dict(wavenumber_m_inv=x,absorbance=[-.02,.1,.4,.2,.0],source_resolution_m_inv=800.,evidence='measured_test_oracle',unit_evidence='inferred_from_documented_instrument_output',original_unit_explicit=None,requires_accept_inferred_units=True)
 vcd=dict(wavenumber_m_inv=x,delta_absorbance=[-2e-4,1e-4,-3e-4,2e-4,0.],source_resolution_m_inv=800.,evidence='measured_test_oracle',unit_evidence=ir['unit_evidence'],original_unit_explicit=None,requires_accept_inferred_units=True)
 return {'peptide_tk082_ss':dict(name='private_oracle_label',absolute_configuration='SECRET_SS',conditions=cond,ir=ir,vcd=vcd),'shape':dict(name='public_shape',ir=dict(wavenumber_m_inv=[85000.,100000.,130000.,160000.,190000.,200000.,220000.,260000.,320000.],absorbance_normalized=[0.,0.,.2,1.,.2,0.,.3,1.,0.],source_resolution_m_inv=None,evidence='real_shape_test_oracle'),vcd=None)}

def host(noise=False,templates=None,**plant):
 h=JascoFvs6000Runtime(templates=toy_templates() if templates is None else templates)
 p=dict(PLANT_EXAMPLE,ir_noise_sd_1s=1e-5 if noise else 0.,vcd_noise_sd_1s=1e-6 if noise else 0.);p.update(plant);h.invoke('plant.configure',**p);return h

def blank(h,method=None,sample=None):
 h.invoke('sample.load',**(sample or BLANK_EXAMPLE));h.invoke('method.configure',**(method or METHOD_EXAMPLE));h.invoke('cover.set',opened=False);h.invoke('power.set',enabled=True);h.invoke('blank.start');h.step(h.method['accumulation_s']);return h

def load_real(h,sample=None):
 h.invoke('cover.set',opened=True);h.invoke('sample.unload',recipient_id='external_rack');h.invoke('sample.load',**(sample or SAMPLE_EXAMPLE));h.invoke('cover.set',opened=False);return h

def ready(noise=False,method=None,**plant):return load_real(blank(host(noise,**plant),method))
def run(h):h.invoke('acquisition.start');h.step(h.method['accumulation_s']);return h.invoke('result.read')

def equivalent(a,b):
 if isinstance(a,np.ndarray):return isinstance(b,np.ndarray) and np.array_equal(a,b)
 if isinstance(a,dict):return a.keys()==b.keys() and all(equivalent(v,b[k]) for k,v in a.items())
 if isinstance(a,(tuple,list)):return len(a)==len(b) and all(equivalent(v,w) for v,w in zip(a,b))
 return a==b

class Fvs6000PhysicsTests(unittest.TestCase):
 def rejected_atomic(self,h,cap,code=None,**args):
  before=h.debug_snapshot()
  with self.assertRaises(ActionRejected) as ctx:h.invoke(cap,**args)
  if code:self.assertEqual(str(ctx.exception),code)
  self.assertTrue(equivalent(before,h.debug_snapshot()),str(ctx.exception))
 def test_full_blank_then_reference_and_signed_delta(self):
  h=ready();r=run(h);x=np.array(r['wavenumber_m_inv']);t=toy_templates()['peptide_tk082_ss']
  self.assertTrue(r['complete']);self.assertEqual(r['frames'],4)
  np.testing.assert_allclose(r['ir_absorbance'],np.interp(x,t['ir']['wavenumber_m_inv'],t['ir']['absorbance']),atol=1e-16)
  np.testing.assert_allclose(r['vcd_delta_absorbance'],np.interp(x,t['vcd']['wavenumber_m_inv'],t['vcd']['delta_absorbance']),atol=1e-19)
 def test_polarized_transmittance_log_identity(self):
  r=run(ready());left=np.asarray(r['transmittance_left']);right=np.asarray(r['transmittance_right'])
  np.testing.assert_allclose(np.log10(right/left),r['vcd_delta_absorbance'],atol=2e-16)
  np.testing.assert_allclose(-.5*np.log10(left*right),r['ir_absorbance'],atol=2e-16)
  self.assertIn('not measured raw',r['provenance']['limitations'][-1])
 def test_negative_reference_values_preserved(self):
  h=ready(method=dict(METHOD_EXAMPLE,wavenumber_start_m_inv=120000.,wavenumber_stop_m_inv=140000.));r=run(h)
  self.assertLess(r['ir_absorbance'][0],0);self.assertGreater(r['transmittance_left'][0],1.)
 def test_center_readout_uses_true_wavenumber_midpoint(self):
  h=ready();r=run(h);m=h.invoke('process.measure');center=(r['wavenumber_m_inv'][0]+r['wavenumber_m_inv'][-1])/2
  self.assertAlmostEqual(m['ir_center'],np.interp(center,r['wavenumber_m_inv'],r['ir_absorbance']))
  self.assertNotEqual(m['ir_center'],r['ir_absorbance'][len(r['ir_absorbance'])//2])
 def test_blank_is_observed_and_subtracted(self):
  h=ready(blank_ir_absorbance=.3,blank_vcd_bias=-.002);b=h.invoke('blank.read')['result'];r=run(h)
  np.testing.assert_allclose(b['ir_absorbance'],.3);np.testing.assert_allclose(b['vcd_delta_absorbance'],-.002)
  self.assertAlmostEqual(r['ir_absorbance'][0],.25);self.assertAlmostEqual(r['vcd_delta_absorbance'][0],-.0001)
 def test_complete_blank_required(self):
  h=host();h.invoke('sample.load',**SAMPLE_EXAMPLE);h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('cover.set',opened=False)
  self.rejected_atomic(h,'acquisition.start','MATCHED_BLANK_REQUIRED')
 def test_partial_blank_never_becomes_background(self):
  h=host();h.invoke('sample.load',**BLANK_EXAMPLE);h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('cover.set',opened=False);h.invoke('blank.start');h.step(.3);h.invoke('acquisition.stop')
  self.assertEqual(h.invoke('result.read')['frames'],1);self.assertFalse(h.read()['blank.valid']);self.assertIsNone(h.invoke('blank.read')['result'])
 def test_blank_accumulation_may_differ_but_grid_must_match(self):
  h=ready();h.invoke('method.configure',**dict(METHOD_EXAMPLE,accumulation_s=.5));self.assertTrue(h.read()['blank.valid']);self.assertEqual(run(h)['frames'],2)
  h.invoke('method.configure',**dict(METHOD_EXAMPLE,spacing_m_inv=500.));self.assertFalse(h.read()['blank.valid']);self.rejected_atomic(h,'acquisition.start','MATCHED_BLANK_REQUIRED')
 def test_power_off_invalidates_blank(self):
  h=ready();h.invoke('power.set',enabled=False);h.invoke('power.set',enabled=True);self.assertFalse(h.read()['blank.valid'])
 def test_known_conditions_reject_without_mutating_inventory(self):
  for field,value in [('solvent','water'),('concentration_kg_m3',24.),('is_aqueous',True)]:
   h=host();self.rejected_atomic(h,'sample.load',**dict(SAMPLE_EXAMPLE,**{field:value}))
 def test_unit_inference_and_unknown_temperature_require_host_ack(self):
  for flag,code in [('accept_inferred_units','INFERRED_UNITS_ACK_REQUIRED'),('accept_unknown_conditions','UNKNOWN_CONDITIONS_ACK_REQUIRED')]:
   h=host();self.rejected_atomic(h,'sample.load',code,**dict(SAMPLE_EXAMPLE,**{flag:False}))
 def test_provenance_retains_unit_inference_and_unknown_temperature(self):
  r=run(ready());p=r['provenance'];self.assertEqual(p['unit_evidence'],'inferred_from_documented_instrument_output');self.assertIsNone(p['original_unit_explicit']);self.assertIsNone(p['source_temperature_k']);self.assertTrue(p['inferred_units_accepted']);self.assertTrue(p['unknown_conditions_accepted'])
 def test_source_resolution_not_improved(self):
  h=ready(method=dict(METHOD_EXAMPLE,resolution_m_inv=400.));self.rejected_atomic(h,'acquisition.start','SOURCE_RESOLUTION_LIMIT')
 def test_source_axis_intersection_not_extrapolated(self):
  t=toy_templates();t['peptide_tk082_ss']['vcd']['wavenumber_m_inv'][0]=130000.
  h=host(templates=t);load_real(blank(h,dict(METHOD_EXAMPLE,wavenumber_start_m_inv=120000.)));self.rejected_atomic(h,'acquisition.start','SOURCE_WAVENUMBER_RANGE')
 def test_broader_resolution_reduces_independent_peak(self):
  x=np.arange(120000.,190001.,100.);y=np.exp(-.5*((x-160000.)/100.)**2)
  t=toy_templates();t['peptide_tk082_ss']['ir'].update(wavenumber_m_inv=x.tolist(),absorbance=y.tolist(),source_resolution_m_inv=800.)
  h=host(templates=t);r=run(load_real(blank(h,dict(METHOD_EXAMPLE,resolution_m_inv=1600.))));self.assertLess(max(r['ir_absorbance']),.3)
 def test_synthetic_ir_is_explicit_and_no_hidden_vcd(self):
  h=blank(host());s=dict(SAMPLE_EXAMPLE,kind='synthetic_ir',template_id='shape',ir_peak_absorbance=.4,synthetic_vcd_ratio=-4e-5,accept_surrogate=True,accept_inferred_units=False)
  r=run(load_real(h,s));np.testing.assert_allclose(r['vcd_delta_absorbance'],np.asarray(r['ir_absorbance'])*-4e-5,atol=2e-20);self.assertEqual(r['provenance']['vcd_evidence'],'synthetic')
 def test_normalized_ir_cannot_be_claimed_measured_vcd(self):
  h=host();self.rejected_atomic(h,'sample.load','MEASURED_VCD_REQUIRED',**dict(SAMPLE_EXAMPLE,template_id='shape'))
 def test_synthetic_mode_requires_explicit_acceptance(self):
  h=host();self.rejected_atomic(h,'sample.load','EVIDENCE_ACK_REQUIRED',**dict(SAMPLE_EXAMPLE,kind='synthetic_ir',template_id='shape',accept_surrogate=False))
 def test_baf2_rejects_water(self):
  h=host();self.rejected_atomic(h,'sample.load','BAF2_WATER_INCOMPATIBLE',**dict(BLANK_EXAMPLE,solvent='water',is_aqueous=True))
 def test_caf2_and_filter_windows(self):
  h=host();h.invoke('path.configure',path_length_m=25e-6,window_material='CaF2');h.invoke('sample.load',**dict(BLANK_EXAMPLE,solvent='water',is_aqueous=True));h.invoke('method.configure',**dict(METHOD_EXAMPLE,wavenumber_start_m_inv=100000.));h.invoke('cover.set',opened=False);h.invoke('power.set',enabled=True);self.rejected_atomic(h,'blank.start','CAF2_WINDOW')
  h.invoke('method.configure',**dict(METHOD_EXAMPLE,wavenumber_start_m_inv=180000.,wavenumber_stop_m_inv=220000.));self.rejected_atomic(h,'blank.start','FILTER_WINDOW')
 def test_finite_filter_motion_and_power_hold(self):
  h=host();h.invoke('filter.select',selection='high');h.step(.25);self.assertEqual(h.read()['filter.angle_rad'],0.);h.invoke('power.set',enabled=True);h.step(.25);self.assertAlmostEqual(h.read()['filter.angle_rad'],math.pi/4);h.step(.75);self.assertEqual(h.read()['filter.angle_rad'],math.pi)
 def test_filter_motion_blocks_acquisition(self):
  h=blank(host());h.invoke('filter.select',selection='high');self.rejected_atomic(h,'blank.start','FILTER_NOT_SETTLED')
 def test_three_position_option_and_empty_side_slots(self):
  h=host(three_position_installed=False);self.rejected_atomic(h,'stage.select','OPTION_NOT_INSTALLED',position=1)
  h=ready();h.invoke('stage.select',position=1);h.step(.2);self.assertAlmostEqual(h.read()['sample.position_m'],.01);self.rejected_atomic(h,'acquisition.start','CENTER_POSITION_REQUIRED');h.step(.5);self.assertAlmostEqual(h.read()['sample.position_m'],.035)
 def test_thermal_closed_form_and_energy_accounting(self):
  h=host();h.invoke('power.set',enabled=True);h.invoke('temperature.set',target_k=308.15);h.step(2.)
  self.assertAlmostEqual(h.temperature,308.15-10*math.exp(-.5));self.assertAlmostEqual(h._ledger()['heat_residual_j'],0.,places=11)
  h.invoke('power.set',enabled=False);t=h.temperature;h.step(3.);self.assertAlmostEqual(h.temperature,298.15+(t-298.15)*math.exp(-.75));self.assertAlmostEqual(h._ledger()['heat_residual_j'],0.,places=11)
 def test_temperature_option_and_water_interlocks(self):
  h=host(temperature_installed=False);self.rejected_atomic(h,'temperature.set','OPTION_NOT_INSTALLED',target_k=303.15)
  h=host(water_ready=False);self.rejected_atomic(h,'temperature.set','COOLING_WATER_REQUIRED',target_k=303.15)
 def test_unsettled_temperature_blocks_measurement(self):
  h=ready();h.invoke('temperature.set',target_k=308.15);self.rejected_atomic(h,'acquisition.start','TEMPERATURE_NOT_SETTLED');self.assertFalse(h.read()['blank.valid'])
 def test_load_unload_volume_and_solute_conserved(self):
  h=ready();before=h._ledger();run(h);after=h._ledger();self.assertEqual(before['current_volume_m3'],after['current_volume_m3']);self.assertEqual(before['current_solute_kg'],after['current_solute_kg'])
  h.invoke('cover.set',opened=True);h.invoke('sample.unload',recipient_id='rack');l=h._ledger();self.assertAlmostEqual(l['volume_residual_m3'],0.,places=20);self.assertAlmostEqual(l['solute_residual_kg'],0.,places=20);self.assertEqual(l['current_volume_m3'],0.)
 def test_sample_load_does_not_overwrite_occupied_cell(self):
  h=host();h.invoke('sample.load',**BLANK_EXAMPLE);self.rejected_atomic(h,'sample.load','UNLOAD_REQUIRED',**SAMPLE_EXAMPLE)
 def test_host_utilities_and_cover_gates(self):
  for flag in ['detector_ready','purge_ready']:
   h=host(**{flag:False});h.invoke('sample.load',**BLANK_EXAMPLE);h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('cover.set',opened=False);self.rejected_atomic(h,'blank.start','OPTICS_NOT_READY')
 def test_noise_time_weighting_independent_rng_oracle(self):
  h=host(noise=True);m=dict(METHOD_EXAMPLE,accumulation_s=.6);blank(h,m);r=h.invoke('result.read');rng=np.random.default_rng(77);n=len(r['wavenumber_m_inv']);expected_a=np.zeros(n);expected_d=np.zeros(n)
  for dt in [.25,.25,.1]:
   expected_a+=dt*(.05+rng.normal(0,1e-5/math.sqrt(dt),n))/.6;expected_d+=dt*(2e-6+rng.normal(0,1e-6/math.sqrt(dt),n))/.6
  np.testing.assert_allclose(r['ir_absorbance'],expected_a,atol=3e-17);np.testing.assert_allclose(r['vcd_delta_absorbance'],expected_d,atol=2e-20)
 def test_step_partition_keeps_same_rng_frames_and_observations(self):
  h=ready(noise=True);g=ready(noise=True);h.invoke('acquisition.start');g.invoke('acquisition.start');h.step(1.)
  for dt in [.1]*10:g.step(dt)
  np.testing.assert_array_equal(h._mean_ir,g._mean_ir);np.testing.assert_array_equal(h._mean_vcd,g._mean_vcd);self.assertEqual(g.phase,'succeeded');self.assertAlmostEqual(h.time_s,g.time_s)
 def test_decimal_frame_duration_does_not_create_tiny_extra_frame(self):
  h=ready(noise=True,frame_duration_s=.1);h.invoke('acquisition.start');h.step(1.);self.assertEqual(h._frames,10);self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h._sampled_s,1.)
 def test_synthetic_path_scaling_does_not_invent_concentration_response(self):
  results=[]
  for path,concentration in [(100e-6,25.),(50e-6,100.)]:
   h=host();h.invoke('path.configure',path_length_m=path,window_material='BaF2');blank(h)
   sample=dict(SAMPLE_EXAMPLE,kind='synthetic_ir',template_id='shape',ir_peak_absorbance=.4,synthetic_vcd_ratio=4e-5,accept_surrogate=True,concentration_kg_m3=concentration)
   results.append(run(load_real(h,sample)))
  np.testing.assert_allclose(results[0]['ir_absorbance'],2*np.array(results[1]['ir_absorbance']),atol=2e-16)
 def test_no_early_future_frame_or_backward_clock(self):
  h=ready();h.invoke('acquisition.start');h.step(.25-5e-13);self.assertEqual(h._frames,0);t=h.time_s;h.step(5e-13);self.assertGreaterEqual(h.time_s,t);self.assertLessEqual(h._last_sample_time,h.time_s)
 def test_frame_windows_and_cancel_partial_accumulation(self):
  h=ready();h.invoke('acquisition.start');start=h.time_s;h.step(.4);h.invoke('acquisition.stop');r=h.invoke('result.read');self.assertEqual(r['frames'],1);self.assertEqual(r['sample_window_s'],[start,start+.25]);self.assertAlmostEqual(h.elapsed_s,.4);before=h._result();h.step(10);self.assertEqual(h._result(),before)
 def test_cover_and_power_cancel_without_fabricating_frame(self):
  for cap,arg in [('cover.set',{'opened':True}),('power.set',{'enabled':False})]:
   h=ready();h.invoke('acquisition.start');h.step(.4);h.invoke(cap,**arg);self.assertEqual(h.phase,'cancelled');self.assertEqual(h._frames,1);self.assertAlmostEqual(h.elapsed_s,.4)
 def test_busy_reconfiguration_is_atomic(self):
  h=ready();h.invoke('acquisition.start')
  for cap,args in [('method.configure',METHOD_EXAMPLE),('filter.select',{'selection':'high'}),('temperature.set',{'target_k':303.15}),('stage.select',{'position':1})]:self.rejected_atomic(h,cap,'BUSY',**args)
 def test_result_method_snapshot_survives_reconfiguration(self):
  h=ready();r=run(h);h.invoke('method.configure',**dict(METHOD_EXAMPLE,accumulation_s=2.,spacing_m_inv=800.));self.assertEqual(h.invoke('result.read'),r);self.assertEqual(h.read()['method.accumulation_s'],2.)
 def test_zero_step_and_queries_do_not_mutate_runtime_or_rng(self):
  h=ready(noise=True);run(h);snap=h.debug_snapshot();h.step(0)
  for k in ['process.measure','result.read','blank.read','product.read']:h.invoke(k)
  h.invoke('result.export',format='json');h.invoke('template.read',substance_id='shape');h.read();h.describe();self.assertTrue(equivalent(snap,h.debug_snapshot()))
 def test_reference_compare_sign_and_no_configuration_assignment(self):
  h=host();same=h.invoke('reference.compare',observed=[2.,-2.,1.],reference=[1.,-1.,.5]);opposite=h.invoke('reference.compare',observed=[-2.,2.,-1.],reference=[1.,-1.,.5]);self.assertEqual(same['nonnegative_scale'],2.);self.assertAlmostEqual(same['signed_cosine'],1.);self.assertEqual(opposite['nonnegative_scale'],0.);self.assertAlmostEqual(opposite['signed_cosine'],-1.);self.assertEqual(opposite['configuration_assignment'],'not_assigned')
 def test_reference_comparison_numeric_overflow_and_zero_rejection(self):
  h=host();r=h.invoke('reference.compare',observed=[1e300,-1e300],reference=[1e300,-1e300]);self.assertEqual(r['nonnegative_scale'],1.)
  self.rejected_atomic(h,'reference.compare','ANALYSIS_RANGE',observed=[1e300,1e300],reference=[1e-300,1e-300]);self.rejected_atomic(h,'reference.compare','ANALYSIS_INPUT',observed=[0.,0.],reference=[1.,2.])
 def test_nonfinite_boolean_and_resource_inputs_reject_atomically(self):
  h=host()
  for x in [float('nan'),float('inf'),-1.,True]:self.rejected_atomic(h,'method.configure','INVALID_ARGUMENT',**dict(METHOD_EXAMPLE,accumulation_s=x))
  self.rejected_atomic(h,'method.configure','SPECTRAL_GRID',**dict(METHOD_EXAMPLE,wavenumber_stop_m_inv=150000.))
  g=host(frame_duration_s=.01);self.rejected_atomic(g,'method.configure','EVENT_RESOURCE_LIMIT',**dict(METHOD_EXAMPLE,accumulation_s=86400.,spacing_m_inv=25.))
 def test_template_query_keeps_absence_and_unit_markers(self):
  h=host();r=json.loads(h.invoke('template.read',substance_id='peptide_tk082_ss')['template_json']);self.assertTrue(r['vcd']['requires_accept_inferred_units']);self.assertIsNone(r['conditions']['temperature_k']);r=json.loads(h.invoke('template.read',substance_id='shape')['template_json']);self.assertIsNone(r['vcd'])
 def test_actual_library_source_ranges_and_unit_flags(self):
  h=JascoFvs6000Runtime();h.invoke('plant.configure',**dict(PLANT_EXAMPLE,ir_noise_sd_1s=0.,vcd_noise_sd_1s=0.));blank(h);load_real(h);r=run(h);self.assertEqual(r['provenance']['vcd_evidence'],'measured_literature');self.assertTrue(r['provenance']['inferred_units_accepted']);self.assertTrue(all(math.isfinite(x) for x in r['vcd_delta_absorbance']))

class Fvs6000ProtocolTests(unittest.TestCase):
 def sessions(self):
  h=ready();h.register_principal('host','evaluator');h.register_principal('other');return h,h.session(),h.session('host'),h.session('other')
 def invoke(self,s,h,k,a=None,rid='unique'):return s.invoke(instance_id=h.instance_id,capability_id=k,arguments=a or {},request_id=rid)
 def test_agent_cannot_escalate_or_read_truth(self):
  h,a,e,o=self.sessions();before=h.debug_snapshot();r=self.invoke(a,h,'sample.load',SAMPLE_EXAMPLE);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertTrue(equivalent(before,h.debug_snapshot()));self.assertNotIn('truth.ledger',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.ledger',e.read(instance_id=h.instance_id)['observations'])
  with self.assertRaises(ActionRejected):a.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
  with self.assertRaises(ActionRejected):h.read('owner')
 def test_public_result_never_leaks_current_sample_identity(self):
  h,a,e,o=self.sessions();run(h)
  for k in ['process.measure','result.read','result.export']:
   r=self.invoke(a,h,k,{'format':'json'} if k=='result.export' else {},rid=k);text=json.dumps(r)
   for hidden in ['private_oracle_label','SECRET_SS','external_reference_cell','peptide_tk082_ss']:self.assertNotIn(hidden,text)
 def test_idempotent_start_and_request_conflict(self):
  h,a,e,o=self.sessions();r=self.invoke(a,h,'acquisition.start',rid='start');snap=h.debug_snapshot();self.assertEqual(self.invoke(a,h,'acquisition.start',rid='start'),r);self.assertTrue(equivalent(snap,h.debug_snapshot()));bad=self.invoke(a,h,'acquisition.stop',rid='start');self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT')
 def test_historical_full_result_owned_and_method_bound(self):
  h,a,e,o=self.sessions();r=self.invoke(a,h,'acquisition.start');op=r['operation_id'];h.step(1);old=a.poll(op);self.assertEqual(old['result'],h.invoke('result.read'));h.invoke('method.configure',**dict(METHOD_EXAMPLE,accumulation_s=.5));self.invoke(a,h,'acquisition.start',rid='next');h.step(.25);self.assertEqual(a.poll(op),old)
  with self.assertRaises(ActionRejected):o.poll(op)
 def test_historical_cancel_does_not_stop_new_acquisition(self):
  h,a,e,o=self.sessions();op=self.invoke(a,h,'acquisition.start')['operation_id'];h.step(1);new=self.invoke(a,h,'acquisition.start',rid='next')['operation_id'];r=a.cancel(op,'cancel-old');self.assertEqual(r['result']['phase'],'succeeded');self.assertEqual(h.phase,'running');self.assertEqual(h.operation_id,new)
 def test_cancel_history_and_mean_windows_match_read(self):
  h,a,e,o=self.sessions();op=self.invoke(a,h,'acquisition.start')['operation_id'];h.step(.4);a.cancel(op,'cancel');p=a.poll(op);read=a.read(instance_id=h.instance_id)
  for k in ['scan.ir_center','scan.vcd_center']:self.assertEqual(p['observations'][k],read['observations'][k]);self.assertEqual(read['observations'][k]['sample_window_s'],[1.,1.25])
  self.invoke(a,h,'acquisition.start',rid='next');self.assertEqual(a.poll(op),p)
 def test_new_acquisition_has_unavailable_observation_not_stale_window(self):
  h,a,e,o=self.sessions();run(h);self.invoke(a,h,'acquisition.start');r=a.read(instance_id=h.instance_id)['observations']['scan.ir_center'];self.assertIsNone(r['value']);self.assertEqual(r['quality'],'unavailable');self.assertIsNone(r['sample_window_s'])
 def test_csv_export_preserves_metadata_and_row_count(self):
  h=ready();r=run(h);out=h.invoke('result.export',format='csv')['content'];lines=out.splitlines();meta=json.loads(lines[0][2:]);self.assertEqual(meta['provenance'],r['provenance']);self.assertEqual(len(lines)-2,len(r['wavenumber_m_inv']))
 def test_all_current_outputs_and_slots_match_declared_schemas(self):
  h,a,e,o=self.sessions();run(h)
  for k in ['process.measure','result.read','blank.read','product.read','result.export','template.read','reference.compare']:
   args=CAPS[k][6];validate(CAPS[k][1],h.invoke(k,**args))
  for k,v in h.read('evaluator').items():validate(SLOTS[k][0],v)
 def test_metadata_is_precise_and_queries_effect_free(self):
  meta=export_jasco_fvs6000_contract_metadata();self.assertEqual(len(meta['capabilities']),22);self.assertEqual(len(meta['state_slots']),32)
  for k,c in meta['capabilities'].items():
   if c['kind'] in ['query','analysis'] or k in UNSUPPORTED:self.assertEqual(c['state_effects'],[])
   self.assertTrue(set(c['observes'])<=SLOTS.keys())
  effects=meta['capabilities']['filter.select']['state_effects'];self.assertIn({'state_id':'filter.angle_rad','timing':'on_step','description_zh':'仅后续宿主step推进有限运动、热学代理或完成全谱积分帧'},effects);self.assertFalse(any(x['state_id']=='scan.ir_center' for x in effects))
  self.assertEqual(JascoFvs6000Runtime().describe()['asset_version'],'1.0.1')
 def test_unsupported_capabilities_reject_without_effect(self):
  h,a,e,o=self.sessions()
  for k in UNSUPPORTED:
   before=h.debug_snapshot();r=self.invoke(a,h,k,rid=k);self.assertEqual(r['status'],'unsupported');self.assertTrue(equivalent(before,h.debug_snapshot()))

if __name__=='__main__':unittest.main()
