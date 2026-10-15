"""Physical, metrology, ACL and historical-operation regressions for CHEM082."""
import copy,csv,hashlib,importlib.util,io,json,math,re,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import shimadzu_icpe9800 as m

def ready(**plant):
 r=m.ShimadzuIcpe9800Runtime();r.invoke('plant.configure',**(dict(m.PLANT_EXAMPLE,detector_noise_sd=0.)|plant));r.invoke('method.configure',**m.METHOD_EXAMPLE);r.invoke('power.set',enabled=True);r.invoke('vacuum.set',enabled=True);r.step(1.);r.invoke('plasma.ignite');r.step(.5);return r

def assay(r,s,fragment=None):
 if r.sample:r.invoke('sample.unload',recipient_id='external_host')
 r.invoke('sample.load',**s);r.invoke('measurement.start');duration=sum(r.method[k] for k in ['stabilization_s','integration_s','flush_s'])*r.method['repeats']
 if fragment:
  for _ in range(math.ceil(duration/fragment)+1):r.step(fragment)
 else:r.step(duration+.02)
 return r.invoke('result.read')

def blank_ready(**plant):
 r=ready(**plant);assay(r,m.BLANK_EXAMPLE);return r

def calibrated():
 r=blank_ready();obs=[]
 for d in [100.,10.,1.]:
  result=assay(r,dict(m.STANDARD_EXAMPLE,dilution_factor=d));obs.append(dict(reference_id=m.STANDARD_EXAMPLE['template_id'],dilution_factor=d,line_integrals=[x for x in result['assays'][0]['line_integrals'] if x['line_id'] in r.method['line_ids']]))
 r.invoke('calibration.fit',curve_id='observed',observations=obs,accept_same_response_conditions=True);r.invoke('calibration.select',curve_id='observed');return r,obs

def clone(r):
 n=m.ShimadzuIcpe9800Runtime.__new__(m.ShimadzuIcpe9800Runtime);keep=['library','templates','lines','_spectral_cache'];n.__dict__.update(copy.deepcopy({k:v for k,v in r.__dict__.items() if k not in keep}));n.__dict__.update({k:getattr(r,k) for k in keep});return n

class PhysicsTests(unittest.TestCase):
 def assertLedger(self,r):
  l=r.invoke('product.read')
  for row in l['elements']:self.assertLess(abs(row['residual']),1e-17)
  self.assertLess(abs(l['liquid_volume_m3']['residual']),1e-14)
  for k in ['argon_mol','vacuum_gas_mol']:self.assertLess(abs(l[k]['residual']),1e-10)
  self.assertLess(abs(l['thermal_residual_j']),1e-6)
 def test_vacuum_finite_valve_and_analytical_chamber_pressure(self):
  r=m.ShimadzuIcpe9800Runtime();p=m.PLANT_EXAMPLE;r.invoke('plant.configure',**p);r.invoke('power.set',enabled=True);r.invoke('vacuum.set',enabled=True);r.step(.15)
  self.assertAlmostEqual(r.valve,.0015);expected=p['vacuum_base_pressure_pa']+(p['gas_reference_pressure_pa']-p['vacuum_base_pressure_pa'])*math.exp(-p['vacuum_decay_s_inv']*.15*.25);self.assertAlmostEqual(r.pressure,expected,places=8);self.assertLedger(r)
 def test_isolated_vacuum_does_not_fake_purge_or_backflow(self):
  r=ready();r.invoke('vacuum.set',enabled=False);pressure=r.pressure;r.step(.4);self.assertEqual(r.pressure,pressure);self.assertEqual(r.valve,0.)
 def test_fixed_torch_and_finite_view_mirror(self):
  r=m.ShimadzuIcpe9800Runtime();r.invoke('plant.configure',**m.PLANT_EXAMPLE);r.invoke('method.configure',**m.METHOD_EXAMPLE);self.assertEqual(r.mirror,0);r.step(.1);self.assertAlmostEqual(r.mirror,.01);r.step(.3);self.assertAlmostEqual(r.mirror,.04);self.assertFalse(any('torch.angle' in k for k in r.read()))
 def test_rf_ramp_and_energy_analytical(self):
  r=ready();r.invoke('plasma.extinguish');r.step(20);r.invoke('plasma.ignite');energy=r.rf_energy;r.step(.02);self.assertAlmostEqual(r.rf,100.);self.assertAlmostEqual(r.rf_energy-energy,1.);self.assertLedger(r)
 def test_lumped_heat_matches_constant_power_solution(self):
  r=ready();t=r.temperature;p=r.plant;dt=.63;steady=p['ambient_k']+p['absorbed_rf_fraction']*r.rf/p['thermal_loss_w_k'];expected=steady+(t-steady)*math.exp(-p['thermal_loss_w_k']/p['thermal_capacity_j_k']*dt);r.step(dt);self.assertAlmostEqual(r.temperature,expected,places=9);self.assertLedger(r)
 def test_argon_reference_temperature_and_pressure_moles(self):
  r=ready();before=r.gas['consumed'];r.step(.4);p=r.plant;expected=r.method['argon_flow_m3_s']*p['gas_reference_pressure_pa']/(m.R*p['gas_reference_temperature_k'])*.4;self.assertAlmostEqual(r.gas['consumed']-before,expected,places=13)
 def test_eco_is_500w_total_5point5_not_plasma_5(self):
  r=ready();r.invoke('plasma.standby',enabled=True);r.step(.2);self.assertEqual(r.rf,500.);self.assertEqual(r._flow(),5.5e-3/60)
 def test_eco_rejects_analysis_without_side_effects(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);r.invoke('plasma.standby',enabled=True);before=r.debug_snapshot()
  with self.assertRaisesRegex(m.ActionRejected,'MEASUREMENT_INTERLOCK'):r.invoke('measurement.start')
  self.assertEqual(before,r.debug_snapshot())
 def test_door_open_extinguishes_without_prohibiting_emergency_open(self):
  r=ready();r.invoke('cover.set',opened=True);self.assertFalse(r.lit);self.assertTrue(r.opened);self.assertEqual(r.read()['cover.angle_rad'],1.5);self.assertFalse(r.invoke('maintenance.check')['allowed'])
 def test_maintenance_wait_is_separate_from_opening(self):
  r=ready();r.invoke('cover.set',opened=True);r.step(179.9);self.assertFalse(r.invoke('maintenance.check')['allowed']);r.step(.1);self.assertTrue(r.invoke('maintenance.check')['allowed'])
 def test_running_door_open_keeps_donor_loss_and_partial_windows(self):
  r=blank_ready();r.invoke('sample.unload',recipient_id='host');r.invoke('sample.load',**m.STANDARD_EXAMPLE);initial=r.sample['volume'];r.invoke('measurement.start');r.step(.25);r.invoke('cover.set',opened=True);self.assertEqual(r.phase,'cancelled');self.assertLess(r.sample['volume'],initial);self.assertTrue(r._active['points']);self.assertFalse(r._concentrations);self.assertLedger(r)
 def test_no_aspiration_after_cancel_but_lit_residue_exits(self):
  r=blank_ready();r.invoke('sample.unload',recipient_id='host');r.invoke('sample.load',**m.STANDARD_EXAMPLE);r.invoke('measurement.start');r.step(.25);r.invoke('measurement.stop');v=r.sample['volume'];exhaust=r._element('Cu')['exhaust'];r.step(.4);self.assertEqual(r.sample['volume'],v);self.assertGreater(r._element('Cu')['exhaust'],exhaust);self.assertLedger(r)
 def test_cold_cleanup_moves_residue_not_refund(self):
  r=blank_ready();r.invoke('sample.unload',recipient_id='host');r.invoke('sample.load',**m.STANDARD_EXAMPLE);r.invoke('measurement.start');r.step(.24);r.invoke('cover.set',opened=True);v=r.sample['volume'];r.step(180);before=r._element('Cu')['aerosol']+r._element('Cu')['plasma'];out=r.invoke('fluidics.clean');self.assertGreater(out['removed_mass_kg'],0);self.assertEqual(r.sample['volume'],v);self.assertEqual(r._element('Cu')['aerosol']+r._element('Cu')['plasma'],0);self.assertGreater(before,0);self.assertLedger(r)
 def test_aerosol_first_order_exact_under_constant_feed(self):
  r=blank_ready();r.invoke('sample.unload',recipient_id='host');r.invoke('sample.load',**m.STANDARD_EXAMPLE);r.invoke('measurement.start');r.step(.1);p=r.plant;c=r.sample['concentrations']['Cu'];expected=p['aerosol_fraction']*p['natural_uptake_m3_s']*c*p['aerosol_residence_s']*(1-math.exp(-.1/p['aerosol_residence_s']));self.assertAlmostEqual(r._element('Cu')['aerosol'],expected,places=22);self.assertLedger(r)
 def test_equal_residence_time_limit_conserves_without_div_zero(self):
  r=blank_ready(aerosol_residence_s=.02,plasma_residence_s=.02);assay(r,m.STANDARD_EXAMPLE);self.assertLedger(r);self.assertTrue(math.isfinite(r._element('Cu')['plasma']))
 def test_all_declared_mixture_elements_conserved_independently(self):
  r=blank_ready();assay(r,m.STANDARD_EXAMPLE);self.assertGreater(len(r._masses),25);self.assertLedger(r)
 def test_argon_exhaustion_is_partial_failure_not_rollback(self):
  r=ready(argon_inventory_mol=.006);r.invoke('sample.load',**m.BLANK_EXAMPLE);r.invoke('measurement.start');r.step(1);self.assertEqual(r.phase,'failed');self.assertFalse(r.lit);self.assertEqual(r.warning,'ARGON_EXHAUSTED');self.assertGreater(len(r._active['points']),0);self.assertLedger(r)
 def test_insufficient_donor_rejects_before_transfer(self):
  r=ready();r.invoke('sample.load',**dict(m.BLANK_EXAMPLE,volume_m3=1e-12));before=r.debug_snapshot()
  with self.assertRaisesRegex(m.ActionRejected,'INSUFFICIENT_SAMPLE_VOLUME'):r.invoke('measurement.start')
  self.assertEqual(before,r.debug_snapshot())
 def test_source_only_line_rejected_not_duplicate_feature(self):
  r=ready();r.invoke('plasma.extinguish');alias=next(k for k,v in r.lines.items() if v.get('source_reference_only'))
  with self.assertRaisesRegex(m.ActionRejected,'SOURCE_REFERENCE_ONLY'):r.invoke('method.configure',**dict(m.METHOD_EXAMPLE,line_ids=[alias]))
 def test_unknown_concentrations_not_treated_as_zero(self):
  r=ready();sample=dict(m.SAMPLE_EXAMPLE,template_id='cu_324754')
  with self.assertRaisesRegex(m.ActionRejected,'LIQUID_CONCENTRATION_REQUIRED'):r.invoke('sample.load',**sample)
 def test_organic_requires_absent_option_and_is_rejected(self):
  r=ready()
  with self.assertRaisesRegex(m.ActionRejected,'UNSUPPORTED_MATRIX'):r.invoke('sample.load',**dict(m.SAMPLE_EXAMPLE,is_aqueous=False))
 def test_invalid_coolant_and_argon_purity_reject_initialization(self):
  for change in [dict(coolant_flow_m3_s=1e-6),dict(coolant_temperature_k=310),dict(argon_purity=.99)]:
   r=m.ShimadzuIcpe9800Runtime()
   with self.assertRaises(m.ActionRejected):r.invoke('plant.configure',**dict(m.PLANT_EXAMPLE,**change))
   self.assertIsNone(r.plant)

class ObservationTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base,cls.obs=calibrated()
 def setUp(self):self.r=clone(self.base)
 def test_multi_element_calibration_recovers_two_distinct_values(self):
  result=assay(self.r,m.SAMPLE_EXAMPLE);c={x['element']:x['concentration_kg_m3'] for x in result['concentrations']};self.assertAlmostEqual(c['Cu'],.00099,places=8);self.assertAlmostEqual(c['Zn'],.00103,places=8);self.assertNotEqual(c['Cu'],c['Zn'])
 def test_external_dilution_inversion_restores_original_concentration(self):
  result=assay(self.r,dict(m.SAMPLE_EXAMPLE,dilution_factor=2.));c={x['element']:x['concentration_kg_m3'] for x in result['concentrations']};self.assertAlmostEqual(c['Cu'],.00099,places=8);self.assertAlmostEqual(c['Zn'],.00103,places=8)
 def test_fitted_slope_is_derived_from_actual_points(self):
  curve=self.r._curves['observed']
  for line in m.METHOD_EXAMPLE['line_ids']:
   xs=np.array([.002/o['dilution_factor'] for o in self.obs]);ys=np.array([next(x['integrated_intensity_s'] for x in o['line_integrals'] if x['line_id']==line) for o in self.obs]);slope,intercept=np.polyfit(xs,ys,1);self.assertAlmostEqual(curve['lines'][line]['slope'],slope,places=8);self.assertAlmostEqual(curve['lines'][line]['intercept'],intercept,places=12)
 def test_current_selected_lines_do_not_limit_stored_spectrum(self):
  assay(self.r,m.SAMPLE_EXAMPLE);s=self.r.invoke('spectrum.read');self.assertEqual(len(s['line_readouts']),68);fe=next(x for x in s['line_readouts'] if x['element']=='Fe');q=self.r.invoke('spectrum.lines',line_ids=[fe['line_id']]);self.assertEqual(q['lines'][0],fe);self.assertEqual(q['sample_window_s'],s['sample_window_s'])
 def test_eligible_features_exactly_one_per_element_not_oem_aliases(self):
  rows=self.r._physical_lines();self.assertEqual(len(rows),68);self.assertEqual(len({x['element'] for x in rows.values()}),68);self.assertTrue(all(not x['source_reference_only'] for x in rows.values()))
 def test_full_range_nonuniform_sampling_is_not_million_pixel(self):
  assay(self.r,m.SAMPLE_EXAMPLE);s=self.r.invoke('spectrum.read');x=np.array(s['wavelength_m']);self.assertEqual(x[0],167e-9);self.assertEqual(x[-1],800e-9);self.assertTrue(np.all(np.diff(x)>0));self.assertGreater(len(x),1024);self.assertLess(len(x),10000);self.assertGreater(np.max(np.diff(x))/np.min(np.diff(x)),100)
 def test_line_center_and_flanks_are_same_saved_spectral_values(self):
  assay(self.r,m.SAMPLE_EXAMPLE);s=self.r.invoke('spectrum.read');axis=np.asarray(s['wavelength_m']);signal=np.asarray(s['intensity_relative']);row=next(x for x in s['line_readouts'] if x['element']=='Cu');self.assertEqual(row['intensity_relative'],signal[np.argmin(abs(axis-row['wavelength_m']))]);self.assertAlmostEqual(row['corrected_relative'],row['intensity_relative']-row['background_relative'])
 def test_unknown_element_availability_flag_is_false(self):
  assay(self.r,m.SAMPLE_EXAMPLE);rows=self.r.invoke('spectrum.read')['line_readouts'];self.assertTrue(any(not x['element_response_available'] for x in rows));declared=self.r.sample['concentrations'];self.assertTrue(all(not x['element_response_available'] for x in rows if x['element'] not in declared))
 def test_actual_window_integral_minus_observed_blank(self):
  result=assay(self.r,m.SAMPLE_EXAMPLE)
  for line in self.r.method['line_ids']:
   assay0=result['assays'][0];integral=math.fsum(next(x['corrected_relative'] for x in p['lines'] if x['line_id']==line)*(p['sample_window_s'][1]-p['sample_window_s'][0]) for p in assay0['points'] if p['acquiring']);actual=next(x['integrated_intensity_s'] for x in assay0['line_integrals'] if x['line_id']==line);self.assertAlmostEqual(actual,integral-self.r._run_blank[line],places=11)
 def test_window_sum_exact_for_nondivisible_detector_period(self):
  r=ready(detector_period_s=.013);out=assay(r,m.BLANK_EXAMPLE);points=out['assays'][0]['points'];self.assertAlmostEqual(sum(p['sample_window_s'][1]-p['sample_window_s'][0] for p in points if p['acquiring']),.4,places=11);self.assertTrue(any(abs((p['sample_window_s'][1]-p['sample_window_s'][0])-.013)>1e-7 for p in points))
 def test_irregular_time_partition_keeps_sample_events_and_inference(self):
  a=clone(self.r);b=clone(self.r);x=assay(a,m.SAMPLE_EXAMPLE);y=assay(b,m.SAMPLE_EXAMPLE,fragment=.007)
  for ca,cb in zip(x['concentrations'],y['concentrations']):self.assertAlmostEqual(ca['concentration_kg_m3'],cb['concentration_kg_m3'],places=12)
  self.assertEqual(len(x['assays'][0]['points']),len(y['assays'][0]['points']))
 def test_zero_dt_and_pure_queries_do_not_consume_noise(self):
  assay(self.r,m.SAMPLE_EXAMPLE);before=self.r.debug_snapshot()
  for _ in range(3):self.r.invoke('result.read');self.r.invoke('spectrum.read');self.r.invoke('process.measure');self.r.step(0)
  self.assertEqual(before,self.r.debug_snapshot())
 def test_noise_draws_independent_of_clock_fragmentation(self):
  a=ready(detector_noise_sd=.001);b=clone(a);x=assay(a,m.BLANK_EXAMPLE);y=assay(b,m.BLANK_EXAMPLE,fragment=.007);self.assertEqual(a._rng.bit_generator.state,b._rng.bit_generator.state)
  for pa,pb in zip(x['assays'][0]['points'],y['assays'][0]['points']):self.assertAlmostEqual(pa['lines'][0]['intensity_relative'],pb['lines'][0]['intensity_relative'],places=11)
 def test_calibration_signature_ignores_repeat_count_and_subset(self):
  self.r.invoke('plasma.extinguish');self.r.invoke('method.configure',**dict(m.METHOD_EXAMPLE,repeats=2,line_ids=['cu_324754']));self.assertTrue(self.r._blank_valid());self.assertTrue(self.r._calibration_ready())
 def test_calibration_signature_invalidates_view_rf_and_background(self):
  for change in [dict(view='radial'),dict(rf_power_w=1200.),dict(background='off'),dict(integration_s=.6)]:
   r=clone(self.r);r.invoke('plasma.extinguish');r.invoke('method.configure',**dict(m.METHOD_EXAMPLE,**change));self.assertFalse(r._blank_valid());self.assertFalse(r._calibration_ready())
 def test_method_change_keeps_old_result_with_its_method(self):
  saved=assay(self.r,m.SAMPLE_EXAMPLE);self.r.invoke('plasma.extinguish');self.r.invoke('method.configure',**dict(m.METHOD_EXAMPLE,background='off'));self.assertEqual(self.r.invoke('result.read'),saved)
 def test_outside_calibration_never_silently_extrapolates_or_clips(self):
  result=assay(self.r,dict(m.SAMPLE_EXAMPLE,dilution_factor=1000));self.assertTrue(all(x['concentration_kg_m3'] is None for x in result['concentrations']));self.assertTrue(any('outside_calibration' in x for x in result['qc']))
 def test_saturation_leaves_raw_trace_and_null_inference(self):
  self.r.plant['saturation_relative']=.2;result=assay(self.r,m.SAMPLE_EXAMPLE);self.assertTrue(all(x['concentration_kg_m3'] is None for x in result['concentrations']));self.assertTrue(any('saturated' in x for x in result['qc']))
 def test_two_repeats_are_real_observations_and_sd_not_oem_precision(self):
  self.r.invoke('plasma.extinguish');self.r.invoke('method.configure',**dict(m.METHOD_EXAMPLE,repeats=2));self.r.invoke('plasma.ignite');self.r.step(.5);result=assay(self.r,m.SAMPLE_EXAMPLE);self.assertEqual(len(result['assays']),2);self.assertIsNotNone(result['concentrations'][0]['sd_kg_m3']);self.assertGreater(result['assays'][1]['points'][0]['time_s'],result['assays'][0]['points'][-1]['time_s'])
 def test_csv_json_exports_contain_actual_values(self):
  result=assay(self.r,m.SAMPLE_EXAMPLE);self.assertEqual(json.loads(self.r.invoke('result.export',format='json')['content']),result);rows=list(csv.DictReader(io.StringIO(self.r.invoke('result.export',format='csv')['content'])));self.assertEqual(len(rows),68*len(result['assays'][0]['points']));self.assertEqual(float(rows[0]['corrected_relative']),result['assays'][0]['points'][0]['lines'][0]['corrected_relative'])
 def test_historical_record_is_not_current_result_alias(self):
  saved=assay(self.r,m.SAMPLE_EXAMPLE);rid=list(self.r._records)[-1];assay(self.r,m.STANDARD_EXAMPLE);self.assertEqual(self.r.invoke('records.read',record_id=rid),saved)
 def test_missing_selected_element_rejected_without_assumed_zero(self):
  self.r.invoke('sample.unload',recipient_id='host');sample=dict(m.SAMPLE_EXAMPLE);record=copy.deepcopy(self.r.templates[sample['template_id']]);record['element_concentrations_kg_m3']['Cu']=None;self.r.templates=dict(self.r.templates);self.r.templates['test_missing']=record;self.r.library=dict(self.r.library,compounds=self.r.templates);sample['template_id']='test_missing';self.r.invoke('sample.load',**sample)
  with self.assertRaisesRegex(m.ActionRejected,'UNREPORTED_ELEMENT'):self.r.invoke('measurement.start')
 def test_extreme_calibration_observations_return_clear_atomic_error(self):
  obs=copy.deepcopy(self.obs)
  for i,row in enumerate(obs):
   for p in row['line_integrals']:p['integrated_intensity_s']=[1e307,5e307,1e308][i]
  before=self.r.debug_snapshot()
  with self.assertRaisesRegex(m.ActionRejected,'CALIBRATION_FIT_INVALID'):self.r.invoke('calibration.fit',curve_id='overflow',observations=obs,accept_same_response_conditions=True)
  self.assertEqual(before,self.r.debug_snapshot())
 def test_standard_reference_values_cannot_be_overridden(self):
  self.r.invoke('sample.unload',recipient_id='host')
  with self.assertRaisesRegex(m.ActionRejected,'STANDARD_REQUIRES_REPORTED_VALUES'):self.r.invoke('sample.load',**dict(m.STANDARD_EXAMPLE,assumed_elements=[dict(element='U',concentration_kg_m3=.01)],accept_assumed_elements=True))

class ProtocolTests(unittest.TestCase):
 def test_metadata_schemas_examples_and_error_codes(self):
  spec=importlib.util.spec_from_file_location('asset_tool_icpe',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=m.export_shimadzu_icpe9800_contract_metadata()
  for cap in meta['capabilities'].values():
   self.assertFalse(tool.lint_schema(cap['input_schema']));self.assertFalse(tool.lint_schema(cap['output_schema']));self.assertFalse(tool.validate_value(cap['example_arguments'],cap['input_schema']))
   for error in cap['errors']:self.assertRegex(error['code'],r'^[A-Z][A-Z0-9_]*$')
   for effect in cap['state_effects']:self.assertIn(effect['state_id'],m.SLOTS)
  for state in meta['state_slots'].values():self.assertFalse(tool.lint_schema(state['value_schema']))
 def test_true_all_capability_error_codes_are_no_source_fragments(self):
  meta=m.export_shimadzu_icpe9800_contract_metadata();codes={x['code'] for cap in meta['capabilities'].values() for x in cap['errors']};self.assertIn('UNREPORTED_ELEMENT',codes);self.assertIn('SOURCE_REFERENCE_ONLY',codes);self.assertTrue(all(len(x)<80 and '\n' not in x for x in codes))
 def test_agent_cannot_access_host_sample_truth(self):
  r=ready();r.register_principal('host','evaluator');agent=r.session();out=agent.invoke(instance_id=r.instance_id,capability_id='sample.load',arguments=m.SAMPLE_EXAMPLE,request_id='sample');self.assertEqual(out['error']['code'],'ACCESS_DENIED');self.assertNotIn('truth.ledger',agent.read(instance_id=r.instance_id)['observations']);self.assertNotIn('product.read',agent.describe(instance_id=r.instance_id)['capabilities'])
 def test_read_truth_filter_rejects_without_partial_response(self):
  r=ready()
  with self.assertRaisesRegex(m.ActionRejected,'ACCESS_DENIED'):r.session().read(instance_id=r.instance_id,state_ids=['power.enabled','truth.ledger'])
 def test_request_retries_do_not_double_consume_or_restart(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);s=r.session();args=dict(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='start');a=s.invoke(**args);r.step(.1);before=r.debug_snapshot();b=s.invoke(**args);self.assertEqual(a,b);self.assertEqual(before,r.debug_snapshot())
 def test_conflicting_request_id_is_atomic(self):
  r=ready();s=r.session();s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='power');before=r.debug_snapshot();bad=s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='power');self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT');self.assertEqual(before,r.debug_snapshot())
 def test_historical_poll_survives_new_acquisition_and_record_delete(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='one')['operation_id'];r.step(1);saved=s.poll(op);r.invoke('records.remove',record_id=list(r._records)[-1]);r.invoke('measurement.start');r.step(1);self.assertEqual(s.poll(op),saved);self.assertNotIn('truth.ledger',saved['observations'])
 def test_operation_poll_owner_is_enforced(self):
  r=ready();r.register_principal('other');r.invoke('sample.load',**m.BLANK_EXAMPLE);op=r.session().invoke(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='one')['operation_id']
  with self.assertRaisesRegex(m.ActionRejected,'ACCESS_DENIED'):r.session('other').poll(op)
 def test_cancel_snapshot_reflects_atomic_door_extinction(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='one')['operation_id'];r.step(.24);r.invoke('cover.set',opened=True);snapshot=s.poll(op);self.assertFalse(snapshot['observations']['plasma.lit']['value']);self.assertTrue(snapshot['observations']['cover.opened']['value']);self.assertEqual(snapshot['status'],'cancelled')
 def test_detector_read_timing_matches_saved_window(self):
  r=ready();assay(r,m.BLANK_EXAMPLE);s=r.session().read(instance_id=r.instance_id,state_ids=['detector.total_relative'])['observations']['detector.total_relative'];self.assertEqual(s['sample_window_s'],r.invoke('spectrum.read')['sample_window_s']);self.assertEqual(s['sample_time_s'],r._last_point['time_s'])
 def test_instances_do_not_share_clock_storage_or_truth(self):
  a=ready();b=ready();assay(a,m.BLANK_EXAMPLE);self.assertNotEqual(a.time_s,b.time_s);self.assertFalse(b._records);self.assertIsNone(b.sample)
 def test_invalid_numeric_values_and_unsupported_commands_atomic(self):
  r=ready();before=r.debug_snapshot()
  for x in [float('nan'),float('inf'),True,-1.]:
   with self.assertRaises(m.ActionRejected):r.step(x)
  for cap in m.UNSUPPORTED:
   with self.assertRaisesRegex(m.ActionRejected,'UNSUPPORTED'):r.invoke(cap)
  self.assertEqual(before,r.debug_snapshot())
 def test_storage_bound_does_not_discard_existing_results(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);r._records={str(i):{} for i in range(m.MAX_RESULTS)};before=r.debug_snapshot()
  with self.assertRaisesRegex(m.ActionRejected,'RESULT_CAPACITY'):r.invoke('measurement.start')
  self.assertEqual(before,r.debug_snapshot())
 def test_fractional_stage_windows_cannot_overrun_trace_capacity(self):
  r=ready(detector_period_s=.01);r.invoke('plasma.extinguish');before=r.debug_snapshot()
  # Total-time division gives19999.9, but3+19995+4 complete/partial windows need20002slots.
  with self.assertRaisesRegex(m.ActionRejected,'TRACE_CAPACITY'):r.invoke('method.configure',**dict(m.METHOD_EXAMPLE,stabilization_s=.021,integration_s=199.947,flush_s=.031))
  self.assertEqual(before,r.debug_snapshot())
 def test_curve_capacity_and_duplicate_id_never_overwrite(self):
  r,obs=calibrated();before=r.debug_snapshot()
  with self.assertRaisesRegex(m.ActionRejected,'CURVE_ID_CONFLICT'):r.invoke('calibration.fit',curve_id='observed',observations=obs,accept_same_response_conditions=True)
  self.assertEqual(before,r.debug_snapshot());r._curves={str(i):{} for i in range(m.MAX_CURVES)}
  with self.assertRaisesRegex(m.ActionRejected,'CURVE_CAPACITY'):r.invoke('calibration.fit',curve_id='new',observations=obs,accept_same_response_conditions=True)
 def test_direct_writable_state_uses_same_power_guard(self):
  r=ready();r.invoke('cover.set',opened=True);out=r.session().write(instance_id=r.instance_id,state_id='power.enabled',value=True,request_id='bad_power');self.assertEqual(out['error']['code'],'POWER_INTERLOCK')
 def test_agent_result_does_not_disclose_loaded_template_identity(self):
  r=ready();r.invoke('sample.load',**dict(m.BLANK_EXAMPLE,sample_id='SECRET_SAMPLE_LABEL'));assay_result=r.invoke('result.read');serialized=json.dumps([assay_result,r.session().read(instance_id=r.instance_id)]);self.assertNotIn('SECRET_SAMPLE_LABEL',serialized)

class CrmLibraryTests(unittest.TestCase):
 """2026-10-14: CRM-derived records from assets/reactions/crm_element_library (NIST/USGS certificate values)."""
 SHARED=ROOT/'assets/reactions/crm_element_library/templates.json'
 @classmethod
 def setUpClass(cls):
  cls.lib=json.loads(cls.SHARED.read_text());cls.byid={r['substance_id']:r for r in cls.lib['templates']};cls.sha=hashlib.sha256(cls.SHARED.read_bytes()).hexdigest()
  cls.rt=m.ShimadzuIcpe9800Runtime();cls.line={v['element']:k for k,v in cls.rt.lines.items() if v.get('spectrum_eligible')}
 def run_crm(self,standard,dilutions,sample,sample_df,elements):
  r=ready();method=dict(m.METHOD_EXAMPLE,line_ids=[self.line[e] for e in elements]);r.invoke('plasma.extinguish');r.step(.1);r.invoke('method.configure',**method);r.invoke('plasma.ignite');r.step(.5)
  assay(r,m.BLANK_EXAMPLE);obs=[]
  for d in dilutions:
   res=assay(r,dict(m.STANDARD_EXAMPLE,template_id=standard,dilution_factor=d,sample_id='crm_std_'+str(d)));obs.append(dict(reference_id=standard,dilution_factor=d,line_integrals=[x for x in res['assays'][0]['line_integrals'] if x['line_id'] in method['line_ids']]))
  r.invoke('calibration.fit',curve_id='crm',observations=obs,accept_same_response_conditions=True);r.invoke('calibration.select',curve_id='crm')
  res=assay(r,dict(m.SAMPLE_EXAMPLE,template_id=sample,dilution_factor=sample_df,sample_id='crm_unknown'));return r,{x['element']:x for x in res['concentrations']}
 def test_every_crm_value_traces_to_shared_certificate_record(self):
  crm={k:v for k,v in self.rt.templates.items() if k.startswith('crm_')};self.assertEqual(len(crm),34)
  for rid,r in crm.items():
   self.assertEqual(r['source']['sha256'],self.sha,rid);self.assertTrue(r['certified_assigned'])
   for el,c in r['element_concentrations_kg_m3'].items():
    p=r['crm_values'][el];shared=self.byid[p['shared_substance_id']];self.assertEqual(shared['element'],el);self.assertEqual(p['source_sha256'],shared['source_sha256'])
    if r['record_kind']=='crm_certified_water':self.assertEqual(c,shared['mass_concentration']['value_kg_m3'])
    else:
     prep=r['preparation'];m_kg=prep.get('sample_mass_kg') or next(a['aliquot_mass_kg'] for a in prep['aliquots'] if a['element']==el)
     self.assertAlmostEqual(c,shared['value']*m_kg/prep['final_volume_m3'],delta=1e-15*max(1,c))
 def test_crm_coverage_reaches_fifty_line_elements(self):
  els={e for k,v in self.rt.templates.items() if k.startswith('crm_') for e,c in v['element_concentrations_kg_m3'].items() if c is not None}
  self.assertGreaterEqual(len(els&set(self.line)),50);self.assertGreaterEqual(self.rt.library['coverage']['distinct_elements_with_measured_concentration'],50)
 def test_nitric_mix_calibration_recovers_certified_1640a_water(self):
  r,got=self.run_crm('crm_nist3100_mix_nitric_main',[5000.,1000.,200.],'crm_nist_srm_1640a',1.,['Sr','Ba','Cu','Zn'])
  truth=self.rt.templates['crm_nist_srm_1640a']['element_concentrations_kg_m3']
  for e in ['Sr','Ba','Cu','Zn']:self.assertLess(abs(got[e]['concentration_kg_m3']-truth[e])/truth[e],.02,e)
  self.assertEqual(r.invoke('result.read')['template_source'],m.TEMPLATE_REL)
 def test_rare_earth_mix_measures_usgs_bhvo2_digest(self):
  _,got=self.run_crm('crm_nist3100_mix_rare_earth',[20000.,1000.,200.],'crm_digest_usgs_bhvo_2',1.,['La','Ce','Nd','Y'])
  truth=self.rt.templates['crm_digest_usgs_bhvo_2']['element_concentrations_kg_m3']
  for e in ['La','Ce','Nd','Y']:self.assertLess(abs(got[e]['concentration_kg_m3']-truth[e])/truth[e],.02,e)
 def test_noble_metal_and_hf_mixes_are_calibration_standards(self):
  for rid,e in [('crm_nist3100_mix_hcl_matrix','Pt'),('crm_nist3100_mix_hf_matrix','Hf'),('crm_nist3100_mix_anion_forming','P')]:
   tr=json.loads(self.rt.invoke('template.read',substance_id=rid)['template_json']);self.assertIn(e,tr['calibration_eligible_elements']);self.assertEqual(tr['record_kind'],'crm_gravimetric_mixed_standard')
  _,got=self.run_crm('crm_nist3100_mix_hcl_matrix',[10000.,1000.,100.],'crm_nist3100_mix_hcl_matrix',500.,['Pt','Pd','Au'])
  for e in ['Pt','Pd','Au']:self.assertIsNotNone(got[e]['concentration_kg_m3'])
 def test_water_srm_is_not_a_calibration_standard(self):
  r=ready();before=r.debug_snapshot()
  with self.assertRaisesRegex(m.ActionRejected,'CALIBRATION_REFERENCE_REQUIRED'):r.invoke('sample.load',**dict(m.STANDARD_EXAMPLE,template_id='crm_nist_srm_1643f'))
  self.assertEqual(before,r.debug_snapshot())

if __name__=='__main__':unittest.main()
