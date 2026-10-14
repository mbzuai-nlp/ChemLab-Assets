"""Independent scientific and protocol checks for isotope-counting ICPMS-2030."""
import copy,csv,importlib.util,io,json,math,re,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import shimadzu_icpms2030 as m

def ready(method=None,**plant):
 r=m.ShimadzuIcpms2030Runtime();r.invoke('plant.configure',**(m.PLANT_EXAMPLE|plant));r.invoke('method.configure',**(method or m.METHOD_EXAMPLE));r.invoke('power.set',enabled=True);r.invoke('vacuum.set',enabled=True);r.step(1);r.invoke('plasma.ignite');r.step(.5);return r

def duration(r):return (r.method['stabilization_s']+len(r.method['mass_numbers'])*(r.method['dwell_s']+r.method['settling_s'])*r.method['sweeps']+r.method['flush_s'])*r.method['repeats']
def assay(r,s,fragment=None):
 if r.sample:r.invoke('sample.unload',recipient_id='host')
 r.invoke('sample.load',**s);r.invoke('measurement.start');d=duration(r)
 if fragment:
  for _ in range(math.ceil(d/fragment)+1):r.step(fragment)
 else:r.step(d+.02)
 return r.invoke('result.read')
def calibrated(method=None,**plant):
 r=ready(method,**plant);assay(r,m.BLANK_EXAMPLE);obs=[];internal=dict(internal_standard_reference_id='infant_internal_delivery') if r.method['internal_standard_isotope_id'] else {}
 for d in [100.,10.,1.]:obs.append(dict(reference_id='infant_cal4',dilution_factor=d,channels=assay(r,m.STANDARD_EXAMPLE|internal|dict(dilution_factor=d))['assays'][0]['channels']))
 r.invoke('calibration.fit',curve_id='observed',observations=obs,accept_same_response_conditions=True);r.invoke('calibration.select',curve_id='observed');return r,obs

def clone(r):
 n=m.ShimadzuIcpms2030Runtime.__new__(m.ShimadzuIcpms2030Runtime);keep=['library','templates','isotopes'];n.__dict__.update(copy.deepcopy({k:v for k,v in r.__dict__.items() if k not in keep}));n.__dict__.update({k:getattr(r,k) for k in keep});return n
IS_METHOD=m.METHOD_EXAMPLE|dict(mass_numbers=[65,66,72],internal_standard_isotope_id='ge_72',internal_standard_concentration_kg_m3=5e-5,helium_flow_m3_s=3e-6/60)

class PhysicsTests(unittest.TestCase):
 def ledger(self,r):
  l=r.invoke('product.read')
  for x in l['isotopes']:self.assertLess(abs(x['residual']),1e-17)
  self.assertLess(abs(l['liquid_volume_m3']['residual']),1e-14)
  for k in ['argon_mol','helium_mol','vacuum_gas_mol']:self.assertLess(abs(l[k]['residual']),1e-10)
  self.assertLess(abs(l['thermal_residual_j']),1e-6)
 def test_atomic_number_fraction_is_not_mass_fraction(self):
  r=ready();iso=r._iso('cu_65');self.assertGreater(abs(iso['abundance_fraction']-iso['isotope_mass_fraction_of_element']),.005);self.assertAlmostEqual(iso['atomic_mass_kg']/iso['relative_atomic_mass'],1.66053906892e-27,places=38)
 def test_all_reference_isotope_mass_fractions_sum_to_one(self):
  r=ready()
  for e in {v['element'] for v in r.isotopes.values()}:self.assertAlmostEqual(sum(v['isotope_mass_fraction_of_element'] for v in r.isotopes.values() if v['element']==e),1.,places=10)
 def test_element_to_isotope_registered_mass_preserved(self):
  r=ready();r.invoke('sample.load',**m.SAMPLE_EXAMPLE);source=m._values(r._reference(m.SAMPLE_EXAMPLE['template_id']))
  for e,c in source.items():self.assertAlmostEqual(sum(v['registered'] for k,v in r._masses.items() if r.isotopes[k]['element']==e),c/20*m.SAMPLE_EXAMPLE['volume_m3'],places=20)
  self.ledger(r)
 def test_exact_aerosol_first_order_transfer(self):
  r=ready();assay(r,m.BLANK_EXAMPLE);r.invoke('sample.unload',recipient_id='host');r.invoke('sample.load',**m.STANDARD_EXAMPLE);r.invoke('measurement.start');r.step(.1);p=r.plant;c=r.sample['concentrations']['cu_65'];expected=p['aerosol_fraction']*p['uptake_m3_s']*c*p['aerosol_residence_s']*(1-math.exp(-.1/p['aerosol_residence_s']));self.assertAlmostEqual(r._masses['cu_65']['aerosol'],expected,places=23);self.ledger(r)
 def test_equal_compartment_residence_times_limit(self):
  r=ready(aerosol_residence_s=.02,plasma_residence_s=.02);assay(r,m.BLANK_EXAMPLE);assay(r,m.STANDARD_EXAMPLE);self.ledger(r)
 def test_pump_rotation_corresponds_to_consumed_final_volume(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);v=r.sample['volume'];r.invoke('measurement.start');r.step(.123);used=v-r.sample['volume'];self.assertAlmostEqual(r.pump_angle,(used/r.plant['pump_volume_per_rad_m3'])%(2*math.pi),places=9)
 def test_cancel_stops_donor_pump_preserves_residues(self):
  r=ready();assay(r,m.BLANK_EXAMPLE);r.invoke('sample.unload',recipient_id='host');r.invoke('sample.load',**m.STANDARD_EXAMPLE);r.invoke('measurement.start');r.step(.25);r.invoke('measurement.stop');v=r.sample['volume'];angle=r.pump_angle;r.step(.5);self.assertEqual(r.sample['volume'],v);self.assertEqual(r.pump_angle,angle);self.ledger(r)
 def test_cold_clean_does_not_refund_transferred_isotopes(self):
  r=ready();assay(r,m.BLANK_EXAMPLE);r.invoke('sample.unload',recipient_id='host');r.invoke('sample.load',**m.STANDARD_EXAMPLE);r.invoke('measurement.start');r.step(.25);r.invoke('cover.set',opened=True);v=r.sample['volume'];r.step(12);result=r.invoke('fluidics.clean');self.assertGreater(result['removed_mass_kg'],0);self.assertEqual(r.sample['volume'],v);self.ledger(r)
 def test_constant_rf_heat_matches_closed_form(self):
  r=ready();p=r.plant;t=r.temperature;steady=p['ambient_k']+p['absorbed_rf_fraction']*r.rf/p['thermal_loss_w_k'];expected=steady+(t-steady)*math.exp(-p['thermal_loss_w_k']/p['thermal_capacity_j_k']*.4);r.step(.4);self.assertAlmostEqual(r.temperature,expected,places=9);self.ledger(r)
 def test_helium_flow_ramps_not_instantaneous(self):
  r=ready();r.invoke('plasma.extinguish');r.invoke('method.configure',**(m.METHOD_EXAMPLE|dict(helium_flow_m3_s=10e-6/60)));r.invoke('plasma.ignite');r.step(.02);self.assertGreater(r.he_flow,0);self.assertLess(r.he_flow,10e-6/60)
 def test_he_and_ar_moles_use_explicit_reference_conditions(self):
  r=ready(IS_METHOD);p=r.plant;a=r.gas['consumed'];h=r.helium['consumed'];r.step(.2);f=p['gas_reference_pressure_pa']/(m.R*p['gas_reference_temperature_k']);self.assertAlmostEqual(r.gas['consumed']-a,r.method['argon_flow_m3_s']*.2*f,places=13);self.assertAlmostEqual(r.helium['consumed']-h,r.method['helium_flow_m3_s']*.2*f,places=13);self.ledger(r)
 def test_empty_helium_rejects_ignition_atomically(self):
  r=m.ShimadzuIcpms2030Runtime();r.invoke('plant.configure',**(m.PLANT_EXAMPLE|dict(helium_inventory_mol=0.)));r.invoke('method.configure',**IS_METHOD);r.invoke('power.set',enabled=True);r.invoke('vacuum.set',enabled=True);r.step(1);before=r.debug_snapshot()
  with self.assertRaisesRegex(m.ActionRejected,'IGNITION_INTERLOCK'):r.invoke('plasma.ignite')
  self.assertEqual(before,r.debug_snapshot())
 def test_mass_and_he_rf_hardware_ranges(self):
  r=ready();r.invoke('plasma.extinguish')
  for change in [dict(mass_numbers=[4]),dict(mass_numbers=[261]),dict(helium_flow_m3_s=10.001e-6/60),dict(rf_power_w=1400.1)]:
   with self.assertRaises(m.ActionRejected):r.invoke('method.configure',**(m.METHOD_EXAMPLE|change))
 def test_missing_mass_and_duplicate_target_isobar_guard(self):
  r=ready();r.invoke('plasma.extinguish')
  with self.assertRaisesRegex(m.ActionRejected,'REQUIRED_MASS_CHANNEL_MISSING'):r.invoke('method.configure',**(m.METHOD_EXAMPLE|dict(mass_numbers=[65])))
  with self.assertRaisesRegex(m.ActionRejected,'UNRESOLVED_ISOBAR_QUANTIFICATION'):r.invoke('method.configure',**(m.METHOD_EXAMPLE|dict(mass_numbers=[58],quantifications=[dict(isotope_id='ni_58',basis='element'),dict(isotope_id='fe_58',basis='element')])))
 def test_torch_target_has_finite_xyz_response(self):
  r=ready();r.invoke('torch.position',x_m=.003,y_m=-.003,z_m=.002);self.assertEqual(r.torch,[0,0,0]);r.step(.05);self.assertTrue(all(abs(v)<=.000500001 for v in r.torch));r.step(.3);self.assertEqual(r.torch,[.003,-.003,.002])
 def test_cover_is_host_positioning_and_safely_extinguishes(self):
  r=ready();t=r.time_s;r.invoke('cover.set',opened=True);self.assertEqual(r.time_s,t);self.assertEqual(r.read()['cover.angle_rad'],1.7);self.assertFalse(r.lit)
 def test_source_ambiguous_ppb_is_query_only(self):
  r=ready();x=json.loads(r.invoke('template.read',substance_id='shimadzu_epa_dw1')['template_json']);self.assertTrue(all(v is None for v in x['element_concentrations_kg_m3'].values()))
  with self.assertRaises(m.ActionRejected):r.invoke('sample.load',**(m.SAMPLE_EXAMPLE|dict(template_id='shimadzu_epa_dw1')))
 def test_no_borrowed_180second_wait_for_cold_maintenance(self):
  r=ready();r.invoke('plasma.extinguish');r.step(12);self.assertLess(r.time_s,20);self.assertTrue(r._cold());r.invoke('fluidics.clean')

class CountTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base,cls.obs=calibrated();cls.isbase,cls.isobs=calibrated(IS_METHOD)
 def setUp(self):self.r=clone(self.base)
 def test_actual_count_calibration_recovers_two_elements(self):
  result=assay(self.r,m.SAMPLE_EXAMPLE);c={x['element']:x['concentration_kg_m3'] for x in result['concentrations']};self.assertAlmostEqual(c['Cu']/7.888e-5,1.,places=3);self.assertAlmostEqual(c['Zn']/5.824e-4,1.,places=3)
 def test_element_and_isotope_basis_are_distinct_mass_concentrations(self):
  method=m.METHOD_EXAMPLE|dict(quantifications=[dict(isotope_id='cu_65',basis='isotope'),dict(isotope_id='zn_66',basis='isotope')]);r,_=calibrated(method);result=assay(r,m.SAMPLE_EXAMPLE)
  for row,source in zip(result['concentrations'],[7.888e-5,5.824e-4]):self.assertAlmostEqual(row['concentration_kg_m3']/(source*r._iso(row['isotope_id'])['isotope_mass_fraction_of_element']),1.,places=3)
 def test_internal_standard_actual_ratio_corrects_common_matrix_loss(self):
  r=clone(self.isbase);source=m.SAMPLE_EXAMPLE|dict(internal_standard_reference_id='infant_internal_delivery',matrix_transmission=.6);result=assay(r,source)
  for row,expected in zip(result['concentrations'],[7.888e-5,5.824e-4]):self.assertAlmostEqual(row['concentration_kg_m3']/expected,1.,places=3);self.assertIsNotNone(row['internal_standard_ratio']);self.assertIsNotNone(row['net_count_rate_s_inv'])
 def test_missing_or_changed_internal_standard_dose_is_rejected(self):
  for update in [{},dict(internal_standard_reference_id='infant_internal_delivery',internal_standard_dilution_factor=2.)]:
   r=clone(self.isbase);r.invoke('sample.unload',recipient_id='host');r.invoke('sample.load',**(m.SAMPLE_EXAMPLE|update));before=r.debug_snapshot()
   with self.assertRaises(m.ActionRejected):r.invoke('measurement.start')
   self.assertEqual(before,r.debug_snapshot())
 def test_blank_cannot_include_internal_standard_and_subtract_it_away(self):
  r=ready(IS_METHOD)
  with self.assertRaisesRegex(m.ActionRejected,'BLANK_MUST_NOT_CONTAIN_INTERNAL_STANDARD'):r.invoke('sample.load',**(m.BLANK_EXAMPLE|dict(internal_standard_reference_id='infant_internal_delivery')))
 def test_integer_counts_and_exact_dwell_rate(self):
  result=assay(self.r,m.SAMPLE_EXAMPLE)
  for p in result['assays'][0]['points']:
   self.assertIs(type(p['counts']),int);self.assertAlmostEqual(p['count_rate_s_inv'],p['counts']/(p['sample_window_s'][1]-p['sample_window_s'][0]),places=5)
 def test_channels_are_sequential_and_settle_does_not_count(self):
  result=assay(self.r,m.SAMPLE_EXAMPLE);points=result['assays'][0]['points'];self.assertEqual([p['mass_number'] for p in points],[65,66]*10)
  for a,b in zip(points,points[1:]):self.assertAlmostEqual(b['sample_window_s'][0]-a['sample_window_s'][1],.002,places=10)
 def test_dwell_partition_does_not_change_integer_count_events(self):
  a=clone(self.r);b=clone(self.r);x=assay(a,m.SAMPLE_EXAMPLE);y=assay(b,m.SAMPLE_EXAMPLE,fragment=.007);self.assertEqual([p['counts'] for p in x['assays'][0]['points']],[p['counts'] for p in y['assays'][0]['points']])
 def test_poisson_rng_only_on_complete_dwell_and_partition_invariant(self):
  a=ready(poisson_noise=True);b=clone(a);x=assay(a,m.BLANK_EXAMPLE);y=assay(b,m.BLANK_EXAMPLE,fragment=.007);self.assertEqual([p['counts'] for p in x['assays'][0]['points']],[p['counts'] for p in y['assays'][0]['points']]);self.assertEqual(a._rng.bit_generator.state,b._rng.bit_generator.state)
 def test_partial_dwell_cancel_keeps_only_finished_windows(self):
  self.r.invoke('sample.unload',recipient_id='host');self.r.invoke('sample.load',**m.SAMPLE_EXAMPLE);self.r.invoke('measurement.start');self.r.step(.231);self.r.invoke('measurement.stop');p=self.r.invoke('result.read')['active_assay']['points'];self.assertEqual(len(p),1);self.assertAlmostEqual(p[0]['sample_window_s'][1]-p[0]['sample_window_s'][0],.02);self.assertFalse(self.r._concentrations)
 def test_channel_corrected_rate_is_weighted_observation_minus_blank(self):
  result=assay(self.r,m.SAMPLE_EXAMPLE);a=result['assays'][0]
  for row in a['channels']:
   p=[x for x in a['points'] if x['mass_number']==row['mass_number']];total=sum((x['sample_window_s'][1]-x['sample_window_s'][0])*x['corrected_count_rate_s_inv'] for x in p);expected=total/row['dwell_time_s']-self.r._run_blank[str(row['mass_number'])];self.assertTrue(math.isclose(expected,row['corrected_count_rate_s_inv'],rel_tol=1e-12,abs_tol=1e-8))
 def test_deadtime_response_and_inverse_have_correct_dimensions(self):
  r=ready(detector_dead_time_s=1e-8);assay(r,m.BLANK_EXAMPLE);result=assay(r,m.STANDARD_EXAMPLE)
  for p in result['assays'][0]['points']:
   if p['corrected_count_rate_s_inv'] is not None:self.assertAlmostEqual(p['corrected_count_rate_s_inv'],p['count_rate_s_inv']/(1-p['count_rate_s_inv']*1e-8),places=7)
 def test_helium_suppresses_assumed_polyatomic_more_than_analyte(self):
  r0=ready();r1=ready(m.METHOD_EXAMPLE|dict(helium_flow_m3_s=5e-6/60));s=m.BLANK_EXAMPLE|dict(interferences=[dict(mass_number=65,incident_count_rate_s_inv=100000.)]);a=assay(r0,s);b=assay(r1,s);ratio=b['assays'][0]['channels'][0]['corrected_count_rate_s_inv']/a['assays'][0]['channels'][0]['corrected_count_rate_s_inv'];self.assertLess(ratio,.3)
 def test_poisson_excursion_clipping_is_flagged_even_below_mean_limit(self):
  r=ready(m.METHOD_EXAMPLE|dict(dwell_s=1.),background_count_rate_s_inv=9.,detector_saturation_rate_s_inv=10.,poisson_noise=True);out=assay(r,m.BLANK_EXAMPLE);points=out['assays'][0]['points'];clipped=[p for p in points if p['saturated']];self.assertTrue(clipped);self.assertTrue(all(p['corrected_count_rate_s_inv'] is None for p in clipped));self.assertFalse(r._blank_valid())
 def test_public_catalog_isobar_advisory_does_not_inspect_hidden_sample(self):
  #58Ni and58Fe share a unit-mass channel; both pure and mixed samples stay conservatively unavailable.
  q=m.METHOD_EXAMPLE|dict(mass_numbers=[58],quantifications=[dict(isotope_id='ni_58',basis='element')]);r=ready(q);assay(r,m.BLANK_EXAMPLE);r._curves['x']=dict(key=r._key(),fits={'ni_58':dict(slope=1.,intercept=0.,concentration_range_kg_m3=[0,100])});r._selected='x';ref=copy.deepcopy(r.templates['nist_srm1643f_certified']);ref['element_concentrations_kg_m3']={'Ni':1e-5};r.templates=dict(r.templates,pure_nickel=ref);out=assay(r,m.SAMPLE_EXAMPLE|dict(template_id='pure_nickel',dilution_factor=1.));self.assertIsNone(out['concentrations'][0]['concentration_kg_m3']);self.assertIn('public isotope catalog',out['concentrations'][0]['quality'])
 def test_changed_helium_rf_xyz_and_is_dose_invalidate_signature(self):
  self.r.invoke('plasma.extinguish');old=self.r._key();self.r.invoke('method.configure',**(m.METHOD_EXAMPLE|dict(helium_flow_m3_s=1e-6/60)));self.assertNotEqual(old,self.r._key());self.assertFalse(self.r._calibration_ready());self.r.invoke('method.configure',**m.METHOD_EXAMPLE);self.r.invoke('torch.position',x_m=.001,y_m=0,z_m=0);self.assertFalse(self.r._blank_valid())
 def test_method_change_preserves_result_acquisition_method(self):
  saved=assay(self.r,m.SAMPLE_EXAMPLE);self.r.invoke('plasma.extinguish');self.r.invoke('method.configure',**(m.METHOD_EXAMPLE|dict(dwell_s=.03)));self.assertEqual(saved,self.r.invoke('result.read'))
 def test_saturation_keeps_counts_but_no_false_concentration(self):
  self.r.plant['detector_saturation_rate_s_inv']=100.;out=assay(self.r,m.SAMPLE_EXAMPLE);self.assertTrue(all(x['concentration_kg_m3'] is None for x in out['concentrations']));self.assertTrue(any(p['saturated'] for p in out['assays'][0]['points']))
 def test_interpolation_only_no_negative_zero_clipping(self):
  out=assay(self.r,m.SAMPLE_EXAMPLE|dict(dilution_factor=10000.));self.assertTrue(all(x['concentration_kg_m3'] is None for x in out['concentrations']))
 def test_repeats_independent_real_windows_not_copied(self):
  self.r.invoke('plasma.extinguish');self.r.invoke('method.configure',**(m.METHOD_EXAMPLE|dict(repeats=2)));self.r.invoke('plasma.ignite');self.r.step(.5);out=assay(self.r,m.SAMPLE_EXAMPLE);self.assertEqual(len(out['assays']),2);self.assertIsNotNone(out['concentrations'][0]['sd_kg_m3']);self.assertLess(out['assays'][0]['points'][-1]['time_s'],out['assays'][1]['points'][0]['time_s'])
 def test_csv_and_json_roundtrip_actual_integer_counts(self):
  out=assay(self.r,m.SAMPLE_EXAMPLE);self.assertEqual(json.loads(self.r.invoke('result.export',format='json')['content']),out);rows=list(csv.DictReader(io.StringIO(self.r.invoke('result.export',format='csv')['content'])));self.assertEqual(int(rows[0]['counts']),out['assays'][0]['points'][0]['counts']);self.assertEqual(len(rows),20)

class ProtocolTests(unittest.TestCase):
 def test_metadata_schemas_examples_units_and_error_literals(self):
  spec=importlib.util.spec_from_file_location('asset_tool_icpms',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=m.export_shimadzu_icpms2030_contract_metadata()
  for c in meta['capabilities'].values():
   self.assertFalse(tool.lint_schema(c['input_schema']));self.assertFalse(tool.lint_schema(c['output_schema']));self.assertFalse(tool.validate_value(c['example_arguments'],c['input_schema']))
   for e in c['errors']:self.assertRegex(e['code'],r'^[A-Z][A-Z0-9_]*$')
   for e in c['state_effects']:self.assertIn(e['state_id'],m.SLOTS)
  for k,v in meta['state_slots'].items():self.assertFalse(tool.lint_schema(v['value_schema']))
 def test_query_and_describe_are_clock_rng_pure(self):
  r=ready();assay(r,m.BLANK_EXAMPLE);before=r.debug_snapshot();r.describe();r.invoke('result.read');r.invoke('spectrum.read');r.invoke('process.measure');r.step(0);self.assertEqual(before,r.debug_snapshot())
 def test_agent_filter_all_entry_points(self):
  r=ready();s=r.session();self.assertNotIn('truth.ledger',s.read(instance_id=r.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=r.instance_id)['capabilities']);out=s.invoke(instance_id=r.instance_id,capability_id='sample.load',arguments=m.SAMPLE_EXAMPLE,request_id='x');self.assertEqual(out['error']['code'],'ACCESS_DENIED')
 def test_agent_explicit_truth_state_is_rejected(self):
  r=ready()
  with self.assertRaisesRegex(m.ActionRejected,'ACCESS_DENIED'):r.session().read(instance_id=r.instance_id,state_ids=['power.enabled','truth.ledger'])
 def test_retry_idempotence_and_conflict(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);s=r.session();a=dict(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='one');out=s.invoke(**a);r.step(.1);before=r.debug_snapshot();self.assertEqual(s.invoke(**a),out);self.assertEqual(before,r.debug_snapshot());conf=s.invoke(**(a|dict(capability_id='measurement.stop')));self.assertEqual(conf['error']['code'],'REQUEST_CONFLICT')
 def test_operation_owner_and_historical_immutable_result(self):
  r=ready();r.register_principal('other');r.invoke('sample.load',**m.BLANK_EXAMPLE);s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='one')['operation_id'];r.step(1);saved=s.poll(op);r.invoke('records.remove',record_id=list(r._records)[-1]);assay(r,m.BLANK_EXAMPLE);self.assertEqual(s.poll(op),saved)
  with self.assertRaisesRegex(m.ActionRejected,'ACCESS_DENIED'):r.session('other').poll(op)
 def test_cancel_snapshot_reflects_whole_atomic_extinction(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='one')['operation_id'];r.step(.23);r.invoke('cover.set',opened=True);out=s.poll(op);self.assertFalse(out['observations']['plasma.lit']['value']);self.assertTrue(out['observations']['cover.opened']['value']);self.assertNotIn('truth.ledger',out['observations'])
 def test_detector_timing_uses_last_dwell_not_next_mass_setpoint(self):
  r=ready();r.invoke('sample.load',**m.BLANK_EXAMPLE);r.invoke('measurement.start');r.step(.223);obs=r.session().read(instance_id=r.instance_id,state_ids=['detector.counts'])['observations']['detector.counts'];self.assertEqual(obs['sample_window_s'],r._last_point['sample_window_s']);self.assertEqual(r._last_point['mass_number'],65)
 def test_instances_own_independent_samples_counts_and_clocks(self):
  a=ready();b=ready();assay(a,m.BLANK_EXAMPLE);self.assertNotEqual(a.time_s,b.time_s);self.assertFalse(b._records);self.assertIsNone(b.sample)
 def test_trace_result_and_curve_capacity_prevent_overflow(self):
  r=ready();r.invoke('plasma.extinguish')
  with self.assertRaisesRegex(m.ActionRejected,'TRACE_CAPACITY'):r.invoke('method.configure',**(m.METHOD_EXAMPLE|dict(sweeps=1000,repeats=20)))
  r.invoke('plasma.ignite');r.step(.5);r.invoke('sample.load',**m.BLANK_EXAMPLE);r._records={str(i):{} for i in range(m.MAX_RESULTS)}
  with self.assertRaisesRegex(m.ActionRejected,'RESULT_CAPACITY'):r.invoke('measurement.start')
 def test_nan_infinite_boolean_and_negative_step_are_atomic(self):
  r=ready();before=r.debug_snapshot()
  for v in [True,float('nan'),float('inf'),-.1]:
   with self.assertRaises(m.ActionRejected):r.step(v)
  self.assertEqual(before,r.debug_snapshot())
 def test_unsupported_options_never_fake_success(self):
  r=ready();before=r.debug_snapshot()
  for k in m.UNSUPPORTED:
   with self.assertRaisesRegex(m.ActionRejected,'UNSUPPORTED'):r.invoke(k)
  self.assertEqual(before,r.debug_snapshot())
 def test_writable_power_state_uses_same_closed_cover_guard(self):
  r=ready();r.invoke('cover.set',opened=True);out=r.session().write(instance_id=r.instance_id,state_id='power.enabled',value=True,request_id='blocked');self.assertEqual(out['error']['code'],'POWER_INTERLOCK')
 def test_invalid_fit_observations_cannot_overflow_into_nonfinite_output(self):
  r,obs=calibrated()
  for i,o in enumerate(obs):
   for c in o['channels']:c['corrected_count_rate_s_inv']=[1e307,5e307,1e308][i]
  before=r.debug_snapshot()
  with self.assertRaisesRegex(m.ActionRejected,'CALIBRATION_FIT_INVALID'):r.invoke('calibration.fit',curve_id='overflow',observations=obs,accept_same_response_conditions=True)
  self.assertEqual(before,r.debug_snapshot())

if __name__=='__main__':unittest.main()
