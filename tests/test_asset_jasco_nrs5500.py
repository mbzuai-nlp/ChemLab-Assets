"""Independent numerical and protocol tests for NRS5500, not factory validation."""
import copy,hashlib,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.jasco_nrs5500 import *
def configured(plant=None,method=None,sample=None):
 r=JascoNrs5500Runtime();r.invoke('plant.configure',**copy.deepcopy(plant or PLANT_EXAMPLE));r.invoke('sample.register',**copy.deepcopy(sample or SAMPLE_EXAMPLE));r.invoke('sample.load',sample_id=(sample or SAMPLE_EXAMPLE)['sample_id']);r.invoke('method.configure',**copy.deepcopy(method or METHOD_EXAMPLE));r.invoke('power.set',enabled=True);r.invoke('door.set',opened=False);r.invoke('laser.set',enabled=True);return r
def measured(**kw):
 r=configured(**kw);r.invoke('acquisition.start');r.step(2);return r
class TestRaman(unittest.TestCase):
 def reject(self,r,cap,args,code=None):
  before=r.debug_snapshot()
  with self.assertRaises(ActionRejected) as c:r.invoke(cap,**args)
  if code:self.assertEqual(str(c.exception),code)
  self.assertEqual(before,r.debug_snapshot())
 def test_71_real_unique_phases_and_formulae(self):
  ts=JascoNrs5500Runtime().templates;self.assertEqual(len(ts),71);self.assertEqual(len({t['formula_original'] for t in ts.values()}),71)
  for t in ts.values():
   self.assertEqual(t['excitation_wavelength_m'],532e-9);self.assertEqual(t['source']['headers_processed']['NAMES'],t['name']);self.assertEqual(t['evidence'],'measured_reference_spectrum')
   for kind in ['raw','processed']:
    self.assertEqual(hashlib.sha256((ROOT/t['source'][kind+'_file']).read_bytes()).hexdigest(),t['source'][kind+'_sha256'])
 def test_all71_complete_and_independent_interp(self):
  for sid,t in JascoNrs5500Runtime().templates.items():
   with self.subTest(id=sid):
    sample=copy.deepcopy(SAMPLE_EXAMPLE);sample['regions'][0]['template_id']=sid
    r=measured(sample=sample);x=np.arange(25000,120001,100);shape=np.maximum(t['source_processed_intensity'],0);shape=shape/max(shape);expected=np.interp(x,t['raman_shift_m_inv'],shape)*10000
    self.assertEqual(r.phase,'succeeded');np.testing.assert_allclose(r.points[0]['count_rate_s_inv'],expected,atol=1e-10);self.assertEqual(len(r.points[0]['counts']),951)
 def test_not_dispersion_as_linewidth(self):
  r=measured();t=r.templates['rruff_r040031'];x=np.array(r.points[0]['raman_shift_m_inv']);e=np.interp(x,t['raman_shift_m_inv'],t['normalized_shape'])*10000
  np.testing.assert_allclose(r.points[0]['count_rate_s_inv'],e,atol=1e-10);self.assertEqual(r.plant['additional_fwhm_m_inv'],0.)
 def test_power_scaling(self):
  a=measured();b=measured(method=dict(METHOD_EXAMPLE,power_w=.005))
  np.testing.assert_allclose(b.points[0]['counts'],np.array(a.points[0]['counts'])*.5)
 def test_exposure_counts_not_rate_scaling(self):
  a=measured();b=measured(method=dict(METHOD_EXAMPLE,integration_s=.04))
  np.testing.assert_allclose(b.points[0]['counts'],np.array(a.points[0]['counts'])*2);np.testing.assert_allclose(a.points[0]['count_rate_s_inv'],b.points[0]['count_rate_s_inv'])
 def test_defocus_gaussian_oracle(self):
  a=measured();r=configured();r.invoke('door.set',opened=True);r.invoke('stage.manual_position',position_m=[0.,0.,3e-6]);r.invoke('door.set',opened=False);r.invoke('laser.set',enabled=True);r.invoke('acquisition.start');r.step(1)
  np.testing.assert_allclose(r.points[0]['count_rate_s_inv'],np.array(a.points[0]['count_rate_s_inv'])*math.exp(-.5),rtol=1e-12)
 def test_two_phase_center_half_weights(self):
  r=configured(plant=AUTO_PLANT,sample=MAP_SAMPLE);r.invoke('mapping.start',**MAPPING_EXAMPLE);r.step(1)
  np.testing.assert_allclose(r.points[1]['count_rate_s_inv'],(np.array(r.points[0]['count_rate_s_inv'])+r.points[2]['count_rate_s_inv'])/2,atol=1e-10)
  self.assertAlmostEqual(r.elapsed_s,.12,places=12)
 def test_stage_sign_sample_coordinates(self):
  r=configured(plant=AUTO_PLANT,sample=MAP_SAMPLE);r.invoke('mapping.start',**MAPPING_EXAMPLE);r.step(1)
  x=np.array(r.points[0]['raman_shift_m_inv']);calc=r.templates['rruff_r040070'];expected=np.interp(x,calc['raman_shift_m_inv'],calc['normalized_shape'])*10000
  np.testing.assert_allclose(r.points[0]['count_rate_s_inv'],expected,atol=1e-9)
 def test_standard_manual_no_autostage(self):
  r=configured();self.reject(r,'stage.move',{'position_m':[0.,0.,0.]},'AUTOSTAGE_NOT_INSTALLED');self.reject(r,'mapping.start',MAPPING_EXAMPLE,'AUTOSTAGE_NOT_INSTALLED')
 def test_manual_external_only_open(self):
  r=configured();self.reject(r,'stage.manual_position',{'position_m':[0.,0.,0.]},'SAMPLE_ACCESS_REQUIRED');r.invoke('door.set',opened=True);r.invoke('stage.manual_position',position_m=[.0375,.025,.015]);self.assertEqual(r.position.tolist(),[.0375,.025,.015])
  self.reject(r,'stage.manual_position',{'position_m':[.038,0.,0.]},'STAGE_RANGE')
 def test_optional_range_boundaries(self):
  r=configured(plant=AUTO_PLANT);r.invoke('stage.move',position_m=[.05,.035,.015]);r.step(50);self.assertEqual(r.position.tolist(),[.05,.035,.015]);self.assertEqual(r.phase,'succeeded')
  self.reject(r,'stage.move',{'position_m':[0.,.036,0.]},'STAGE_RANGE')
 def test_move_is_finite_and_shutter_off(self):
  r=configured(plant=AUTO_PLANT);r.invoke('stage.move',position_m=[.001,0.,0.]);r.step(.25);self.assertAlmostEqual(r.position[0],.00025);self.assertFalse(r.read()['laser.emitting']);self.assertEqual(r._incident_energy_j,0.)
 def test_scan_waits_for_position(self):
  r=configured(plant=AUTO_PLANT);r.invoke('mapping.start',positions_m=[[.001,0.,0.]]);r.step(.5);self.assertEqual(r.windows,[]);self.assertEqual(r._laser_on_s,0.);r.step(.5);self.assertEqual(r.windows,[]);r.step(.02);self.assertEqual(len(r.points),1)
 def test_1024pixel_sequential_windows(self):
  r=configured(method=dict(METHOD_EXAMPLE,start_m_inv=20000.,stop_m_inv=125000.));r.invoke('acquisition.start');r.step(.02);self.assertEqual(len(r.windows),1);self.assertEqual(len(r.windows[0]['counts']),1024);self.assertEqual(r.points,[]);r.step(.02)
  self.assertEqual(len(r.windows[1]['counts']),27);self.assertEqual(len(r.points[0]['counts']),1051);self.assertEqual(r.points[0]['exposures'],2);self.assertAlmostEqual(r.points[0]['sample_time_s'],.04)
 def test_cancel_keeps_full_windows_not_partial(self):
  r=configured(method=dict(METHOD_EXAMPLE,start_m_inv=20000.,stop_m_inv=125000.));r.invoke('acquisition.start');r.step(.03);r.invoke('acquisition.stop');self.assertEqual(len(r.windows),1);self.assertEqual(r.points,[]);self.assertAlmostEqual(r._incident_energy_j,.0003);r.step(1);self.assertAlmostEqual(r._incident_energy_j,.0003)
 def test_door_interrupt(self):
  r=configured();r.invoke('acquisition.start');r.step(.01);r.invoke('door.set',opened=True);self.assertEqual(r.phase,'cancelled');self.assertFalse(r.laser);self.assertFalse(r.read()['laser.emitting']);self.assertEqual(r.points,[])
 def test_power_interrupt(self):
  r=configured();r.invoke('acquisition.start');r.step(.01);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.assertFalse(r.laser)
 def test_laser_permission_is_not_idle_emission(self):
  r=configured();self.assertTrue(r.laser);self.assertFalse(r.read()['laser.emitting']);r.step(3);self.assertEqual(r._incident_energy_j,0.)
 def test_mass_and_incident_energy(self):
  r=measured();v=r.invoke('product.read');self.assertAlmostEqual(v['incident_energy_j'],.01*.02);self.assertEqual(v['mass_residual_kg'],0.);r.invoke('door.set',opened=True);r.invoke('sample.unload',recipient_id='rack');v=r.invoke('product.read');self.assertEqual(v['attached_mass_kg'],0.);self.assertEqual(v['external_mass_kg'],.001)
 def test_source_coverage_not_full_factory_range(self):
  r=configured(method=dict(METHOD_EXAMPLE,start_m_inv=5000.,stop_m_inv=800000.));self.reject(r,'acquisition.start',{},'SOURCE_COVERAGE')
 def test_all_mapping_positions_validated_before_start(self):
  r=configured(plant=AUTO_PLANT);self.reject(r,'mapping.start',{'positions_m':[[0.,0.,0.],[0.,.04,0.]]},'STAGE_RANGE')
 def test_reject_other_laser_power_grating(self):
  r=configured()
  for patch in [dict(laser_wavelength_m=785e-9),dict(power_w=.051),dict(grating_lines_m_inv=2400000)]:self.reject(r,'method.configure',dict(METHOD_EXAMPLE,**patch),'INVALID_ARGUMENT')
 def test_invalid_pixel_interval(self):
  r=configured();self.reject(r,'method.configure',dict(METHOD_EXAMPLE,stop_m_inv=120050.),'PIXEL_GRID')
 def test_reject_regions_overlap_and_degenerate(self):
  r=configured();s=copy.deepcopy(MAP_SAMPLE);s['regions'][1]['x_min_m']=-.001;self.reject(r,'sample.register',s,'OVERLAPPING_REGIONS')
  s=copy.deepcopy(MAP_SAMPLE);s['regions'][0]['x_max_m']=s['regions'][0]['x_min_m'];self.reject(r,'sample.register',s,'INVALID_REGION')
 def test_immutable_public_data(self):
  r=measured();x=r.invoke('template.read',substance_id='rruff_r040031');x['normalized_shape'][0]=123;self.assertNotEqual(r.templates['rruff_r040031']['normalized_shape'][0],123)
  y=r.invoke('result.read');y['spectra'][0]['counts'][0]=123;self.assertNotEqual(r.points[0]['counts'][0],123)
 def test_peaks_only_caller_observations(self):
  r=configured();a=CAPS['spectrum.peaks'][6];v=r.invoke('spectrum.peaks',**a);self.assertEqual(v['peak_shift_m_inv'],[40100.]);self.assertEqual(v['peak_rate_s_inv'],[2.])
  self.reject(r,'spectrum.peaks',dict(a,raman_shift_m_inv=[40000.,40000.,40200.]),'ANALYSIS_INPUT')
 def test_band_edge_interpolation_independent(self):
  r=configured();a=copy.deepcopy(CAPS['map.band'][6]);a.update(low_m_inv=40050.,high_m_inv=40150.);v=r.invoke('map.band',**a);self.assertEqual(v['band_integrals_s_inv_m_inv'],[150.])
 def test_band_map_discriminates_not_concentration(self):
  r=configured(plant=AUTO_PLANT,sample=MAP_SAMPLE);r.invoke('mapping.start',**MAPPING_EXAMPLE);r.step(1);v=r.invoke('map.band',spectra=r.points,low_m_inv=45800.,high_m_inv=47000.);a=v['band_integrals_s_inv_m_inv'];self.assertGreater(a[2],a[0]*5);self.assertAlmostEqual(a[1],(a[0]+a[2])/2,places=7)
 def test_axis_calibration_uses_caller_and_additive(self):
  r=measured(plant=dict(PLANT_EXAMPLE,axis_bias_m_inv=200.));self.assertEqual(r.points[0]['raman_shift_m_inv'][0],25200.)
  r.invoke('calibration.axis',observed_peak_m_inv=46600.,certified_peak_m_inv=46400.,accept_external_reference=True);r.invoke('acquisition.start');r.step(1);self.assertEqual(r.points[0]['raman_shift_m_inv'][0],25000.)
  r.invoke('calibration.axis',observed_peak_m_inv=46400.,certified_peak_m_inv=46400.,accept_external_reference=True);self.assertEqual(r.axis_correction,-200.)
 def test_step_partition_independent(self):
  a=configured(plant=AUTO_PLANT,sample=MAP_SAMPLE);b=configured(plant=AUTO_PLANT,sample=MAP_SAMPLE)
  for r in [a,b]:r.invoke('mapping.start',**MAPPING_EXAMPLE)
  a.step(1)
  for _ in range(100):b.step(.01)
  for x,y in zip(a.points,b.points):
   np.testing.assert_allclose(x['counts'],y['counts'],atol=1e-9);self.assertAlmostEqual(x['sample_time_s'],y['sample_time_s'],places=12)
  self.assertAlmostEqual(a._incident_energy_j,b._incident_energy_j)
 def test_seed_noise_reproducible(self):
  p=dict(PLANT_EXAMPLE,shot_noise=True,read_noise_counts=2.,dark_rate_s_inv=3.);a=measured(plant=p);b=measured(plant=p);np.testing.assert_array_equal(a.points[0]['counts'],b.points[0]['counts']);c=measured(plant=dict(p,seed=74));self.assertFalse(np.array_equal(a.points[0]['counts'],c.points[0]['counts']))
 def test_noise_statistics(self):
  values=[]
  for seed in range(120):
   r=measured(plant=dict(PLANT_EXAMPLE,seed=seed,shot_noise=True));values.append(r.points[0]['counts'][214])
  r=measured();mu=r.points[0]['counts'][214];self.assertLess(abs(np.mean(values)-mu),5*math.sqrt(mu/120));self.assertLess(abs(np.var(values)-mu),.6*mu)
 def test_added_broadening_lowers_peak(self):
  a=measured();b=measured(plant=dict(PLANT_EXAMPLE,additional_fwhm_m_inv=1000.));self.assertLess(max(b.points[0]['counts']),max(a.points[0]['counts']))
 def test_boolean_nan_extra_atomic(self):
  r=configured()
  for val in [True,float('nan'),float('inf')]:self.reject(r,'method.configure',dict(METHOD_EXAMPLE,power_w=val),'INVALID_ARGUMENT')
  self.reject(r,'laser.set',dict(enabled=True,private=True),'INVALID_ARGUMENT')
 def test_step_invalid_atomic(self):
  r=configured()
  for dt in [-1,True,float('nan')]:
   before=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.step(dt)
   self.assertEqual(before,r.debug_snapshot())
 def test_read_only_no_clock_revision_rng_change(self):
  r=measured();before=r.debug_snapshot()
  for _ in range(2):r.read();r.invoke('process.measure');r.invoke('result.read');r.invoke('spectrum.peaks',**CAPS['spectrum.peaks'][6])
  self.assertEqual(before,r.debug_snapshot())
 def test_busy_and_failed_action_rollback(self):
  r=configured();r.invoke('acquisition.start');self.reject(r,'method.configure',METHOD_EXAMPLE,'BUSY')
  r.invoke('acquisition.stop');self.reject(r,'calibration.axis',dict(observed_peak_m_inv=100000.,certified_peak_m_inv=102100.,accept_external_reference=True),'CALIBRATION_RANGE')
 def test_initialization_after_world_tick_allowed(self):
  r=JascoNrs5500Runtime();r.step(.5);r.invoke('plant.configure',**PLANT_EXAMPLE);self.reject(r,'plant.configure',PLANT_EXAMPLE,'INITIALIZATION_ONLY')
 def test_unimplemented_rejected(self):
  r=configured()
  for k in UNSUPPORTED:self.reject(r,k,{},'UNSUPPORTED')
 def test_session_privacy_and_role_not_self_claimed(self):
  r=configured();s=r.session();d=s.describe(instance_id=r.instance_id);self.assertNotIn('truth.ledger',d['state_slots']);self.assertNotIn('plant.configure',d['capabilities'])
  for cap,args in [('product.read',{}),('sample.register',MAP_SAMPLE),('stage.manual_position',{'position_m':[0.,0.,0.]})]:
   v=s.invoke(instance_id=r.instance_id,capability_id=cap,arguments=args,request_id=cap);self.assertEqual(v['error']['code'],'ACCESS_DENIED')
  self.assertNotIn('rruff_r040031',json.dumps(r.invoke('result.read')));self.assertNotIn('active_sample_id',json.dumps(s.read(instance_id=r.instance_id)))
 def test_request_dedup_conflict_and_instance_isolation(self):
  r=configured();s=r.session();args=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='a');a=s.invoke(**args);b=s.invoke(**args);self.assertEqual(a,b);self.assertEqual(r._op_count,1)
  c=s.invoke(**dict(args,capability_id='acquisition.stop'));self.assertEqual(c['error']['code'],'REQUEST_CONFLICT')
  other=configured();other.step(.3);self.assertEqual(r.time_s,0.);self.assertNotEqual(other.instance_id+'_op_1',r.operation_id) if other.instance_id!=r.instance_id else self.assertEqual(other.phase,'idle')
 def test_operation_owner_cancel_and_poll(self):
  r=configured();r.register_principal('second');a=r.session();b=r.session('second');res=a.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=res['operation_id']
  with self.assertRaises(ActionRejected):b.poll(op)
  r.step(.01);v=a.cancel(op,'cancel');self.assertEqual(v['result']['phase'],'cancelled')
 def test_observation_time_not_world_time(self):
  r=measured();s=r.session();v=s.read(instance_id=r.instance_id,state_ids=['scan.maximum_rate_s_inv'])['observations']['scan.maximum_rate_s_inv'];self.assertAlmostEqual(v['sample_time_s'],.02);np.testing.assert_allclose(v['sample_window_s'],[0.,.02]);self.assertEqual(r.time_s,2.)
 def test_band_unrepresentable_output_rejects_without_effects(self):
  r=configured();a=copy.deepcopy(CAPS['map.band'][6]);a['spectra'][0]['count_rate_s_inv']=[1e308]*3
  self.reject(r,'map.band',a,'ANALYSIS_INPUT');before=r.debug_snapshot()
  v=r.session().invoke(instance_id=r.instance_id,capability_id='map.band',arguments=a,request_id='overflow')
  self.assertEqual(v['status'],'rejected');self.assertEqual(v['error']['code'],'ANALYSIS_INPUT');json.dumps(v,allow_nan=False);self.assertEqual(before,r.debug_snapshot())
 def test_band_large_finite_integral_avoids_intermediate_overflow(self):
  r=configured();a=copy.deepcopy(CAPS['map.band'][6]);a.update(low_m_inv=0.,high_m_inv=1.)
  a['spectra'][0].update(raman_shift_m_inv=[0.,.5,1.],count_rate_s_inv=[1e308]*3)
  v=r.invoke('map.band',**a);self.assertEqual(v['band_integrals_s_inv_m_inv'],[1e308]);json.dumps(v,allow_nan=False)
 def test_old_spectra_keep_acquisition_method_after_reconfigure(self):
  r=measured();old=r.invoke('result.read');replacement=dict(METHOD_EXAMPLE,integration_s=.04,power_w=.005)
  r.invoke('method.configure',**replacement);retained=r.invoke('result.read')
  self.assertEqual(retained,old);self.assertEqual(json.loads(retained['settings_json']),METHOD_EXAMPLE)
  r.invoke('acquisition.start');self.assertEqual(r.invoke('result.read')['spectra'],[]);r.step(1)
  fresh=r.invoke('result.read');self.assertEqual(json.loads(fresh['settings_json']),replacement);self.assertEqual(fresh['spectra'][0]['integration_s'],.04)
  np.testing.assert_allclose(fresh['spectra'][0]['counts'],old['spectra'][0]['counts'])
 def test_historical_operation_summary_owned_immutable_and_read_only(self):
  r=configured();r.register_principal('second');s=r.session();other=r.session('second')
  op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='first')['operation_id'];r.step(1);terminal=s.poll(op)
  s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='second');before=r.debug_snapshot()
  self.assertEqual(s.poll(op),terminal);self.assertEqual(terminal['status'],'succeeded');self.assertEqual(terminal['result']['completed_points'],1)
  terminal['result']['completed_points']=999;self.assertEqual(s.poll(op)['result']['completed_points'],1)
  self.assertNotIn(SAMPLE_EXAMPLE['sample_id'],json.dumps(s.poll(op)));self.assertNotIn('rruff_r040031',json.dumps(s.poll(op)))
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):other.poll(op)
  self.assertEqual(before,r.debug_snapshot())
 def test_cancel_completed_operation_preserves_new_running_operation(self):
  r=configured();s=r.session();old=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='old')['operation_id'];r.step(1)
  current=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='new')['operation_id'];r.step(.01);revision=r.revision;ledger=r.invoke('product.read')
  v=s.cancel(old,'cancel-old');self.assertEqual(v['result']['phase'],'succeeded');self.assertEqual(r.phase,'running');self.assertEqual(r.operation_id,current);self.assertEqual(r.revision,revision);self.assertEqual(r.invoke('product.read'),ledger)
  self.assertEqual(s.cancel(old,'cancel-old'),v)
  with self.assertRaisesRegex(ActionRejected,'REQUEST_CONFLICT'):s.cancel(current,'cancel-old')
  self.assertEqual(r.phase,'running');r.step(.01);self.assertEqual(s.poll(current)['status'],'succeeded')
 def test_partial_cancel_terminal_survives_later_move_without_phantom_exposure(self):
  r=configured(plant=AUTO_PLANT,method=dict(METHOD_EXAMPLE,start_m_inv=20000.,stop_m_inv=125000.));s=r.session()
  op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='wide')['operation_id'];r.step(.03);s.cancel(op,'cancel-wide');old=s.poll(op)
  self.assertEqual(old['status'],'cancelled');self.assertEqual(old['result']['completed_windows'],1);self.assertEqual(old['result']['completed_points'],0);self.assertAlmostEqual(old['result']['elapsed_s'],.03)
  energy=r.invoke('product.read')['incident_energy_j'];s.invoke(instance_id=r.instance_id,capability_id='stage.move',arguments={'position_m':[.001,0.,0.]},request_id='move');r.step(.5)
  self.assertEqual(s.poll(op),old);self.assertEqual(r.invoke('product.read')['incident_energy_j'],energy);self.assertAlmostEqual(energy,.0003)
 def test_move_metadata_matches_immediate_target_and_step_motion(self):
  r=configured(plant=AUTO_PLANT);m=export_jasco_nrs5500_contract_metadata()['capabilities'];effects=m['stage.move']['state_effects']
  immediate={v['state_id'] for v in effects if v['timing']=='immediate'};later={v['state_id'] for v in effects if v['timing']=='on_step'}
  before=r.read('evaluator');r.invoke('stage.move',position_m=[.001,0.,0.]);after=r.read('evaluator')
  self.assertLessEqual({k for k in before if before[k]!=after[k]},immediate);self.assertNotIn('stage.x_m',immediate);self.assertNotIn('truth.ledger',{v['state_id'] for v in effects})
  r.step(.25);moved=r.read('evaluator');self.assertLessEqual({k for k in after if after[k]!=moved[k]},later);self.assertAlmostEqual(moved['stage.x_m'],.00025)
  self.assertEqual(m['spectrum.peaks']['observes'],[]);self.assertEqual(m['map.band']['observes'],[]);self.assertEqual(m['method.configure']['observes'],['method.configured'])
 def test_contract_numeric_units_and_examples(self):
  m=export_jasco_nrs5500_contract_metadata();self.assertEqual(len(m['capabilities']),23)
  def visit(v):
   if isinstance(v,dict):
    if v.get('type') in ['number','integer']:self.assertIn('x-unit',v)
    for x in v.values():visit(x)
   elif isinstance(v,list):
    for x in v:visit(x)
  visit(m)
  for k,v in CAPS.items():validate(v[0],v[6])
if __name__=='__main__':unittest.main()

