"""Real XRF protocol and numerical regressions; no geometry/OEM precision claims."""
import copy,hashlib,importlib.util,json,math,re,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.shimadzu_edx7200 import *

def configured(**plant):
 h=ShimadzuEdx7200Runtime('edx');h.invoke('plant.configure',**(PLANT_EXAMPLE|plant));h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('power.set',enabled=True);h.step(2);return h

def load(h,s):
 h.invoke('cover.set',opened=True)
 if h.sample:h.invoke('sample.unload',recipient_id='host')
 h.invoke('sample.load',**s);h.invoke('cover.set',opened=False)

def acquire(h,s):
 load(h,s);h.invoke('measurement.start');h.step(h.method['integration_s']*h.method['repeats']+.02);return h.invoke('result.read')

def calibrated():
 h=configured();acquire(h,BLANK_EXAMPLE);obs=[]
 for i in ['f','a','g','e']:
  name='blackmass_icp_'+i;r=acquire(h,STANDARD_EXAMPLE|dict(template_id=name));obs.append(dict(reference_id=name,reference_fraction=1.,acquisition_signature=r['acquisition_signature'],line_observations=r['assays'][0]['line_observations']))
 h.invoke('calibration.fit',curve_id='actual',observations=obs,accept_same_matrix_response=True);h.invoke('calibration.select',curve_id='actual');return h,obs

class Edx7200Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.ready=configured();cls.cal,cls.observations=calibrated()
 def reject(self,h,k,a,code=None):
  before=copy.deepcopy(h.read('evaluator'));time=h.time_s;state=copy.deepcopy(h._rng.bit_generator.state)
  with self.assertRaises(ActionRejected) as exc:h.invoke(k,**a)
  if code:self.assertEqual(str(exc.exception),code)
  self.assertEqual(h.read('evaluator'),before);self.assertEqual(h.time_s,time);self.assertEqual(h._rng.bit_generator.state,state)
 def test_01_actual_real_three_element_calibration(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE)
  for x,y in zip(r['mass_fractions'],[.0522,.0204,.1668]):self.assertLess(abs(x['mass_fraction']/y-1),.001)
 def test_02_real_reference_and_model_counts_separate(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE);self.assertIn('synthetic',r['evidence']);self.assertNotIn('blackmass_ec_a',json.dumps(r));self.assertIsNone(h.templates['blackmass_ec_a']['raw_count_arrays'])
 def test_03_energy_axis_J(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE);self.assertAlmostEqual(r['energy_edges_j'][-1]/E_CHARGE,50000);self.assertTrue(np.all(np.diff(r['energy_edges_j'])>0))
 def test_04_edge_not_emission_threshold(self):
  h=copy.deepcopy(self.ready);q=h.lines['cu_ka1'];voltage=(q['energy_j']+q['edge_energy_j'])/(2*E_CHARGE);self.reject(h,'method.configure',METHOD_EXAMPLE|dict(selected_lines=['cu_ka1'],voltage_v=voltage),'LINE_NOT_EXCITABLE')
 def test_05_high_Z_K_unexcitable(self):self.reject(copy.deepcopy(self.ready),'method.configure',METHOD_EXAMPLE|dict(selected_lines=['u_ka1']),'LINE_NOT_EXCITABLE')
 def test_06_high_Z_L_available(self):
  h=copy.deepcopy(self.ready);h.invoke('method.configure',**(METHOD_EXAMPLE|dict(selected_lines=['u_la1'])));self.assertEqual(h.method['selected_lines'],['u_la1'])
 def test_07_outside_range_line_guard(self):self.reject(copy.deepcopy(self.ready),'method.configure',METHOD_EXAMPLE|dict(energy_max_j=5000*E_CHARGE),'LINE_OUTSIDE_SPECTRUM')
 def test_08_invalid_source_line_query_only(self):
  h=copy.deepcopy(self.ready);self.assertFalse(json.loads(h.invoke('template.read',substance_id='na_kb1')['template_json'])['spectrum_eligible']);self.reject(h,'method.configure',METHOD_EXAMPLE|dict(selected_lines=['na_kb1']),'LINE_EDGE_UNAVAILABLE')
 def test_09_nan_bool_voltage_rejected(self):
  for v in [float('nan'),float('inf'),True,3999,50001]:self.reject(copy.deepcopy(self.ready),'method.configure',METHOD_EXAMPLE|dict(voltage_v=v),'INVALID_ARGUMENT')
 def test_10_current_SI_boundaries(self):
  for v in [0.,.01,True]:self.reject(copy.deepcopy(self.ready),'method.configure',METHOD_EXAMPLE|dict(current_a=v),'INVALID_ARGUMENT')
  for v in [1e-6,.001]:copy.deepcopy(self.ready).invoke('method.configure',**(METHOD_EXAMPLE|dict(current_a=v)))
 def test_11_duplicate_lines_rejected(self):self.reject(copy.deepcopy(self.ready),'method.configure',METHOD_EXAMPLE|dict(selected_lines=['cu_ka1','cu_ka1']),'INVALID_METHOD')
 def test_12_window_capacity(self):self.reject(copy.deepcopy(self.ready),'method.configure',METHOD_EXAMPLE|dict(integration_s=200.,window_s=.01,repeats=20),'INVALID_METHOD')
 def test_13_voltage_finite(self):
  h=ShimadzuEdx7200Runtime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('power.set',enabled=True);self.assertEqual(h.voltage,0);h.step(.02);self.assertGreater(h.voltage,0);self.assertLess(h.voltage,METHOD_EXAMPLE['voltage_v'])
 def test_14_SDD_thermal_lag(self):
  h=ShimadzuEdx7200Runtime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('power.set',enabled=True);h.step(.02);self.assertGreater(h.temperature,PLANT_EXAMPLE['detector_target_k']);self.assertLess(h.temperature,298.15)
 def test_15_filter_real_linear_motion(self):
  h=copy.deepcopy(self.ready);h.invoke('filter.select',filter_id='F5');self.assertEqual(h.filter_position,0);h.step(.02);self.assertGreater(h.filter_position,0);self.assertLess(h.filter_position,.060);h.step(1);self.assertAlmostEqual(h.filter_position,.060)
 def test_16_collimator_finite_and_standard_spots(self):
  h=copy.deepcopy(self.ready);h.invoke('collimator.select',diameter_m=.001);h.step(.02);self.assertTrue(0<h.collimator_position<.045);self.reject(h,'collimator.select',dict(diameter_m=.002),'UNSUPPORTED_APERTURE')
 def test_17_optional_He_absent(self):self.reject(copy.deepcopy(self.ready),'atmosphere.set',dict(mode='helium'),'OPTION_NOT_INSTALLED')
 def test_18_optional_vacuum_absent(self):self.reject(copy.deepcopy(self.ready),'atmosphere.set',dict(mode='vacuum'),'OPTION_NOT_INSTALLED')
 def test_19_optional_He_needs_facility(self):self.reject(ShimadzuEdx7200Runtime(),'plant.configure',PLANT_EXAMPLE|dict(optional_helium_installed=True,helium_flow_m3_s=0),'HELIUM_SERVICE_REQUIRED')
 def test_20_He_finite_and_species_conservation(self):
  h=configured(optional_helium_installed=True);h.invoke('atmosphere.set',mode='helium');h.step(.1);self.assertTrue(0<h._he_fraction()<.99);h.step(3);self.assertTrue(h._atmosphere_ready());g=json.loads(h._ledger()['gas_ledger_json']);self.assertLess(abs(g['air_residual_mol']),1e-12);self.assertLess(abs(g['helium_residual_mol']),1e-12)
 def test_21_vacuum_finite_gas_removal(self):
  h=configured(optional_vacuum_installed=True);h.invoke('atmosphere.set',mode='vacuum');h.step(.1);self.assertTrue(1000<h._pressure()<101325);h.step(3);self.assertTrue(h._atmosphere_ready());self.assertLess(abs(h._ledger()['gas_residual_mol']),1e-12)
 def test_22_vacuum_liquid_rejected(self):
  h=configured(optional_vacuum_installed=True);load(h,SAMPLE_EXAMPLE|dict(phase='liquid'));self.reject(h,'atmosphere.set',dict(mode='vacuum'),'LIQUID_VACUUM_UNSUPPORTED')
 def test_23_vacuum_open_requires_vent(self):
  h=configured(optional_vacuum_installed=True);h.invoke('atmosphere.set',mode='vacuum');h.step(2);self.reject(h,'cover.set',dict(opened=True),'VENT_BEFORE_OPENING');h.invoke('atmosphere.set',mode='air');h.step(2);h.invoke('cover.set',opened=True);self.assertAlmostEqual(h._pressure(),101325)
 def test_24_near_ambient_open_fills_gas(self):
  h=configured(optional_vacuum_installed=True);h.air_mol*=.995;removed=h.initial_gas_mol-h.air_mol;h.air_out_mol=removed;h.gas_out_mol=removed;h.invoke('cover.set',opened=True);self.assertAlmostEqual(h._pressure(),101325);self.assertLess(abs(h._ledger()['gas_residual_mol']),1e-14)
 def test_25_He_open_mixing_species(self):
  h=configured(optional_helium_installed=True);h.invoke('atmosphere.set',mode='helium');h.step(3);h.invoke('cover.set',opened=True);self.assertEqual(h.he_mol,0);self.assertAlmostEqual(h._pressure(),101325);g=json.loads(h._ledger()['gas_ledger_json']);self.assertLess(abs(g['helium_residual_mol']),1e-12)
 def test_26_mass_limit_and_envelope(self):
  h=copy.deepcopy(self.ready);h.invoke('cover.set',opened=True)
  self.reject(h,'sample.load',SAMPLE_EXAMPLE|dict(mass_kg=5.001),'INVALID_ARGUMENT');self.reject(h,'sample.load',SAMPLE_EXAMPLE|dict(dimensions_m=[.02,.28,.01]),'SAMPLE_ENVELOPE')
 def test_27_geometry_ack(self):
  h=copy.deepcopy(self.ready);h.invoke('cover.set',opened=True);self.reject(h,'sample.load',SAMPLE_EXAMPLE|dict(accept_geometric_clearance_assumption=False),'GEOMETRIC_CLEARANCE_ACK_REQUIRED')
 def test_28_response_ack(self):
  h=copy.deepcopy(self.ready);h.invoke('cover.set',opened=True);self.reject(h,'sample.load',SAMPLE_EXAMPLE|dict(accept_conditional_response=False),'CONDITIONAL_RESPONSE_ACK_REQUIRED')
 def test_29_liquid_requires_sealed_cup(self):
  h=copy.deepcopy(self.ready);h.invoke('cover.set',opened=True);self.reject(h,'sample.load',SAMPLE_EXAMPLE|dict(phase='liquid',sealed_cup=False),'LIQUID_CONFIGURATION')
 def test_30_query_only_composition(self):
  h=copy.deepcopy(self.ready);h.invoke('cover.set',opened=True);self.assertIsNotNone(h.invoke('template.read',substance_id='copper_unknown_repeat_mean'));self.reject(h,'sample.load',SAMPLE_EXAMPLE|dict(template_id='copper_unknown_repeat_mean'),'REFERENCE_QUERY_ONLY')
 def test_31_non_destructive_mass_all_elements(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE);self.assertEqual(h.sample['mass'],.01);l=h._ledger();self.assertLess(abs(l['sample_mass_residual_kg']),1e-15)
  for row in json.loads(l['element_ledger_json']).values():self.assertLess(abs(row['residual_kg']),1e-15)
 def test_32_complete_sample_return(self):
  h=copy.deepcopy(self.cal);acquire(h,SAMPLE_EXAMPLE);h.invoke('cover.set',opened=True);self.assertEqual(h.invoke('sample.unload',recipient_id='host')['returned_mass_kg'],.01);self.assertLess(abs(h._ledger()['sample_mass_residual_kg']),1e-15)
 def test_33_absorbed_energy_without_mass_consumption(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);e=h.electrical_energy;h.invoke('measurement.start');h.step(.5);self.assertAlmostEqual(h.electrical_energy-e,2.5,places=9);self.assertLess(abs(h._ledger()['energy_residual_j']),1e-10)
 def test_34_cancel_keeps_complete_windows_and_energy(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);h.invoke('measurement.start');h.step(.14);e=h.electrical_energy;h.invoke('measurement.stop');r=h._result();self.assertEqual(len(r['active_assay']['windows']),1);self.assertFalse(r['complete']);self.assertEqual(h.sample['mass'],.01);h.step(.2);self.assertEqual(h.electrical_energy,e)
 def test_35_open_cover_turns_off_xray(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);h.invoke('measurement.start');self.assertTrue(h.read()['xray.emitting']);h.step(.1);h.invoke('cover.set',opened=True);self.assertFalse(h.read()['xray.emitting']);self.assertEqual(h.phase,'cancelled')
 def test_36_poweroff_cancels(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);h.invoke('measurement.start');h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertFalse(h.read()['xray.emitting'])
 def test_37_no_hidden_element_guard(self):
  h=copy.deepcopy(self.cal);record=copy.deepcopy(h.templates['blackmass_ec_a']);record['element_mass_fractions']={'Cu':.02};h.templates['public_test']=record;load(h,SAMPLE_EXAMPLE|dict(template_id='public_test'));h.invoke('measurement.start');h.step(.5);self.assertEqual(h.phase,'succeeded')
 def test_38_uncalibrated_counts_are_available(self):
  h=copy.deepcopy(self.ready);acquire(h,BLANK_EXAMPLE);r=acquire(h,SAMPLE_EXAMPLE);self.assertGreater(sum(r['assays'][0]['counts']),0);self.assertTrue(all(x['mass_fraction'] is None for x in r['mass_fractions']))
 def test_39_method_signature_and_old_result(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE);h.invoke('filter.select',filter_id='F1');self.assertFalse(h._blank_valid());self.assertEqual(h._result(),r);self.reject(h,'calibration.select',dict(curve_id='actual'),'CALIBRATION_MISMATCH')
 def test_40_repeats_do_not_change_response_signature(self):
  h=copy.deepcopy(self.cal);h.invoke('method.configure',**(METHOD_EXAMPLE|dict(repeats=2)));self.assertTrue(h._calibration_ready());self.assertTrue(h._blank_valid())
 def test_41_observation_signature_guard(self):
  h=copy.deepcopy(self.cal);obs=copy.deepcopy(self.observations);obs[0]['acquisition_signature']='other';self.reject(h,'calibration.fit',dict(curve_id='x',observations=obs,accept_same_matrix_response=True),'OBSERVATION_SIGNATURE_MISMATCH')
 def test_42_observation_ROI_guard(self):
  h=copy.deepcopy(self.cal);obs=copy.deepcopy(self.observations);obs[0]['line_observations'][0]['roi_low_j']*=.9;self.reject(h,'calibration.fit',dict(curve_id='x',observations=obs,accept_same_matrix_response=True),'OBSERVATION_ROI_MISMATCH')
 def test_43_degenerate_standard_fails_atomically(self):
  h=copy.deepcopy(self.cal);self.reject(h,'calibration.fit',dict(curve_id='x',observations=[self.observations[0]]*3,accept_same_matrix_response=True),'DEGENERATE_CALIBRATION')
 def test_44_outside_calibration_not_clipped(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE|dict(reference_fraction=.001));self.assertTrue(all(x['mass_fraction'] is None for x in r['mass_fractions']))
 def test_45_counts_integer_and_windows(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE);windows=r['assays'][0]['windows'];self.assertEqual(len(windows),5)
  for w in windows:self.assertTrue(all(type(v) is int for v in w['counts']));self.assertAlmostEqual(w['end_s']-w['start_s'],.1)
 def test_46_final_short_window_exact_duration(self):
  h=copy.deepcopy(self.cal);h.invoke('method.configure',**(METHOD_EXAMPLE|dict(integration_s=.23)));r=acquire(h,SAMPLE_EXAMPLE);self.assertEqual(len(r['assays'][0]['windows']),3);self.assertAlmostEqual(r['assays'][0]['windows'][-1]['end_s']-r['assays'][0]['windows'][-1]['start_s'],.03)
 def test_47_arbitrary_read_does_not_advance_RNG(self):
  h=copy.deepcopy(self.cal);before=h.time_s;state=copy.deepcopy(h._rng.bit_generator.state);h.read();h.session().describe(instance_id='edx');h.invoke('result.read');self.assertEqual(before,h.time_s);self.assertEqual(state,h._rng.bit_generator.state)
 def test_48_fixed_process_step_partition(self):
  a=copy.deepcopy(self.cal);load(a,SAMPLE_EXAMPLE);a.invoke('measurement.start');b=copy.deepcopy(a);a.step(.5)
  for _ in range(25):b.step(.02)
  self.assertEqual(a.assays[0]['counts'],b.assays[0]['counts']);self.assertAlmostEqual(a.electrical_energy,b.electrical_energy)
 def test_49_actual_histogram_inverse_independent(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE);a=r['assays'][0];profile=json.loads(a['response_profile_json']);edges=np.asarray(r['energy_edges_j']);shapes=[];proj=[]
  for key in profile['line_ids']:
   q=h.lines[key];sigma=profile['fwhm_j']/2.354820045;cdf=np.array([.5*(1+math.erf((v-q['energy_j'])/(sigma*math.sqrt(2)))) for v in edges]);shapes.append(np.diff(cdf));proj.append([float(q['element']==e) for e in profile['candidate_elements']])
  basis=np.asarray(shapes).T@(np.asarray(proj)*np.asarray(profile['mean_coefficients'])[:,None]);basis/=np.asarray(profile['normalization']);rate=sum((np.asarray(w['counts'])/(1-w['count_rate_s_inv']*profile['dead_time_s']) for w in a['windows']))/a['elapsed_s'];rate-=np.asarray(h._run_blank['rates_s_inv']);amps=np.linalg.lstsq(basis,rate,rcond=profile['least_squares_rcond'])[0]
  for row in a['line_observations']:self.assertAlmostEqual(row['net_rate_s_inv'],amps[profile['candidate_elements'].index(h.lines[row['line_id']]['element'])],places=6)
 def test_50_profile_does_not_reveal_composition(self):
  h=copy.deepcopy(self.cal);a=acquire(h,SAMPLE_EXAMPLE)['assays'][0];text=a['response_profile_json'];self.assertNotIn('blackmass',text);self.assertNotIn('mass_fraction',text);self.assertGreater(len(json.loads(text)['candidate_elements']),50)
 def test_51_principal_truth_filter(self):
  h=copy.deepcopy(self.cal);h.register_principal('eval',visibility='evaluator');agent=h.session();self.assertNotIn('truth.ledger',agent.read(instance_id='edx')['observations']);self.assertNotIn('sample.load',agent.describe(instance_id='edx')['capabilities']);r=agent.invoke(instance_id='edx',capability_id='product.read',arguments={},request_id='a');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
 def test_52_request_idempotence_conflict(self):
  h=copy.deepcopy(self.cal);s=h.session();r=s.invoke(instance_id='edx',capability_id='spectrum.cursor',arguments={'energy_j':8000*E_CHARGE},request_id='same');self.assertEqual(r,s.invoke(instance_id='edx',capability_id='spectrum.cursor',arguments={'energy_j':8000*E_CHARGE},request_id='same'));self.assertEqual(s.invoke(instance_id='edx',capability_id='spectrum.cursor',arguments={'energy_j':9000*E_CHARGE},request_id='same')['error']['code'],'REQUEST_CONFLICT')
 def test_53_historical_async_owner_snapshot(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);s=h.session();op=s.invoke(instance_id='edx',capability_id='measurement.start',arguments={},request_id='one')['operation_id'];h.step(.5);old=s.poll(op);acquire(h,SAMPLE_EXAMPLE);self.assertEqual(old,s.poll(op));h.register_principal('other');self.assertRaises(ActionRejected,h.session('other').poll,op)
 def test_54_session_cancel_snapshot(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);s=h.session();op=s.invoke(instance_id='edx',capability_id='measurement.start',arguments={},request_id='one')['operation_id'];h.step(.14);s.cancel(op,'cancel');old=s.poll(op);self.assertEqual(old['status'],'cancelled');self.assertEqual(old,s.poll(op));self.assertEqual(len(old['result']['active_assay']['windows']),1)
 def test_55_two_instance_isolation(self):
  a=copy.deepcopy(self.cal);b=copy.deepcopy(self.cal);before=b._result();a.invoke('filter.select',filter_id='F5');a.step(.5);self.assertEqual(before,b._result());self.assertNotEqual(a.filter_position,b.filter_position)
 def test_56_unsupported_no_effect(self):
  for cap in UNSUPPORTED:self.reject(copy.deepcopy(self.ready),cap,{},'UNSUPPORTED')
 def test_57_public_schema_and_metadata_shape(self):
  spec=importlib.util.spec_from_file_location('asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=export_shimadzu_edx7200_contract_metadata();schema=json.loads((ROOT/'skills/chemistry-lab-assets/assets/asset-contract.schema.json').read_text())
  for key,cap in meta['capabilities'].items():
   self.assertEqual(tool.validate_value(cap,{'$ref':'#/$defs/capability'},schema),[],key)
   for field in ['input_schema','output_schema']:self.assertEqual(tool.lint_schema(cap[field]),[],key)
   self.assertEqual(tool.validate_value(cap['example_arguments'],cap['input_schema']),[],key)
   for effect in cap['state_effects']:self.assertEqual(set(effect),{'state_id','description_zh','timing'});self.assertIn(effect['state_id'],meta['state_slots'])
   for error in cap['errors']:self.assertRegex(error['code'],r'^[A-Z][A-Z0-9_]*$')
  for slot in meta['state_slots'].values():self.assertEqual(tool.validate_value(slot,{'$ref':'#/$defs/slot'},schema),[])
 def test_58_json_csv_real_arrays(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE);self.assertEqual(json.loads(h.invoke('result.export',format='json')['content']),r);rows=list(csv.DictReader(io.StringIO(h.invoke('result.export',format='csv')['content'])));self.assertEqual(len(rows),5*2048);self.assertEqual(sum(int(x['counts']) for x in rows),sum(r['assays'][0]['counts']))
 def test_59_storage_history_and_remove(self):
  h=copy.deepcopy(self.cal);r=acquire(h,SAMPLE_EXAMPLE);key=h.invoke('records.list')['record_ids'][-1];self.assertEqual(h.invoke('records.read',record_id=key),r);h.invoke('records.remove',record_id=key);self.reject(h,'records.read',dict(record_id=key),'UNKNOWN_RECORD')
 def test_60_busy_config_rejected(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);h.invoke('measurement.start');self.reject(h,'filter.select',dict(filter_id='F2'),'BUSY')
 def test_61_saturation_disables_quantification(self):
  h=configured(max_count_rate_s_inv=1000.);acquire(h,BLANK_EXAMPLE);r=acquire(h,SAMPLE_EXAMPLE);self.assertTrue(any(w['saturated'] for w in r['assays'][0]['windows']));self.assertTrue(all(x['mass_fraction'] is None for x in r['mass_fractions']))
 def test_62_poisson_seed_and_zero_step(self):
  h=configured(poisson_counts=True);acquire(h,BLANK_EXAMPLE);load(h,SAMPLE_EXAMPLE);h.invoke('measurement.start');b=copy.deepcopy(h);state=copy.deepcopy(h._rng.bit_generator.state);h.step(0);self.assertEqual(state,h._rng.bit_generator.state);h.step(.5);b.step(.5);self.assertEqual(h.assays[0]['counts'],b.assays[0]['counts'])

 def test_63_cross_response_signature_rejected(self):
  h=configured(line_rate_s_inv=4e6);acquire(h,BLANK_EXAMPLE);self.reject(h,'calibration.fit',dict(curve_id='foreign',observations=self.observations,accept_same_matrix_response=True),'OBSERVATION_SIGNATURE_MISMATCH')
 def test_64_He_response_tracks_actual_gas_during_window(self):
  h=configured(optional_helium_installed=True)
  def atmosphere_ready():
   h.invoke('atmosphere.set',mode='helium')
   for _ in range(200):
    if h._atmosphere_ready():break
    h.step(.02)
  load(h,BLANK_EXAMPLE);atmosphere_ready();h.invoke('measurement.start');h.step(.5)
  load(h,SAMPLE_EXAMPLE);atmosphere_ready();h.invoke('measurement.start');initial=h._response()[1].copy();initial_he=h._he_fraction();h.step(.5);profile=json.loads(h.assays[0]['response_profile_json']);mean=np.asarray(profile['mean_coefficients']);self.assertGreater(float(np.max(abs(mean-initial))),1e-5);self.assertGreater(h._he_fraction(),initial_he);gas=json.loads(h._ledger()['gas_ledger_json']);self.assertLess(abs(gas['air_residual_mol']),1e-12);self.assertLess(abs(gas['helium_residual_mol']),1e-12)
 def test_65_complete_window_public_sampling(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);h.invoke('measurement.start');start=h.time_s;h.step(.14);row=h.session().read(instance_id='edx',state_ids=['detector.count_rate_s_inv'])['observations']['detector.count_rate_s_inv'];self.assertAlmostEqual(row['sample_window_s'][0],start);self.assertAlmostEqual(row['sample_window_s'][1],start+.1);self.assertLess(row['sample_time_s'],h.time_s)
 def test_66_result_capacity_precheck(self):
  h=copy.deepcopy(self.cal);load(h,SAMPLE_EXAMPLE);h._records={str(i):{} for i in range(MAX_RESULTS)};self.reject(h,'measurement.start',{},'RESULT_CAPACITY')
 def test_67_curve_capacity_precheck(self):
  h=copy.deepcopy(self.cal);h._curves={str(i):{} for i in range(MAX_CURVES)};h._selected='';self.reject(h,'calibration.fit',dict(curve_id='overflow',observations=self.observations,accept_same_matrix_response=True),'CURVE_CAPACITY')
 def test_68_profile_candidates_independent_of_hidden_elements(self):
  a=copy.deepcopy(self.cal);load(a,SAMPLE_EXAMPLE);a.invoke('measurement.start');b=copy.deepcopy(self.cal);record=copy.deepcopy(b.templates['blackmass_ec_a']);record['element_mass_fractions']={'Cu':.01};b.templates['host_fixture']=record;load(b,SAMPLE_EXAMPLE|dict(template_id='host_fixture'));b.invoke('measurement.start');self.assertEqual(a._candidate_elements,b._candidate_elements);self.assertTrue(np.array_equal(a._shapes,b._shapes));self.assertEqual(a._key(),b._key())

class CrmLibraryTests(unittest.TestCase):
 """2026-10-14: solid CRM records from assets/reactions/crm_element_library (NIST certificates, USGS information sheets)."""
 SHARED=ROOT/'assets/reactions/crm_element_library/templates.json'
 @classmethod
 def setUpClass(cls):
  cls.lib=json.loads(cls.SHARED.read_text());cls.byid={r['substance_id']:r for r in cls.lib['templates']};cls.sha=hashlib.sha256(cls.SHARED.read_bytes()).hexdigest();cls.rt=ShimadzuEdx7200Runtime('crm')
 def run_crm(self,standards,target,lines,phase='powder'):
  h=ShimadzuEdx7200Runtime('edx');h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('method.configure',**dict(METHOD_EXAMPLE,selected_lines=lines,integration_s=2.,window_s=1.));h.invoke('power.set',enabled=True);h.step(2)
  acquire(h,BLANK_EXAMPLE|dict(phase=phase));obs=[]
  for rid in standards:
   r=acquire(h,STANDARD_EXAMPLE|dict(template_id=rid,phase=phase));obs.append(dict(reference_id=rid,reference_fraction=1.,acquisition_signature=r['acquisition_signature'],line_observations=r['assays'][0]['line_observations']))
  h.invoke('calibration.fit',curve_id='crm',observations=obs,accept_same_matrix_response=True);h.invoke('calibration.select',curve_id='crm')
  r=acquire(h,SAMPLE_EXAMPLE|dict(template_id=target,phase=phase));truth=h.templates[target]['element_mass_fractions']
  return {x['element']:(x['mass_fraction'],truth[x['element']]) for x in r['mass_fractions']},r
 def assertRecovered(self,got,tol):
  for e,(x,y) in got.items():
   self.assertIsNotNone(x,e);self.assertLess(abs(x/y-1),tol,(e,x,y))
 def test_crm_records_trace_to_shared_certificate_values(self):
  crm={k:v for k,v in self.rt.templates.items() if k.startswith('crm_')};self.assertEqual(len(crm),32)
  for rid,r in crm.items():
   self.assertEqual(r['evidence']['source_sha256'],self.sha);self.assertEqual(r['source'],'crm_element_library');self.assertFalse(r['source_normalization_applied'])
   total=sum(r['element_mass_fractions'].values());self.assertLessEqual(total,1.);self.assertAlmostEqual(r['unmodelled_remainder_mass_fraction'],1-total,places=12)
   for e,v in r['element_mass_fractions'].items():
    p=r['crm_values'][e];x=self.byid[p['shared_substance_id']];self.assertEqual((x['element'],x['value'],x['source_sha256']),(e,v,p['source_sha256']))
    self.assertIsNone(p['licence_flag']);self.assertTrue(11<=x['atomic_number']<=92)
   for e in r['calibration_eligible_elements']:
    c=r['crm_values'][e];self.assertTrue(c['value_category'] in ('certified','reference','non_certified') or c['value_category']=='literature_estimate' and self.byid[c['shared_substance_id']]['uncertainty'] is not None)
 def test_crm_bulk_element_coverage_reaches_fifty(self):
  cov=self.rt.library['coverage'];els={e for k,v in self.rt.templates.items() if k.startswith('crm_') for e in v['element_mass_fractions']}
  self.assertGreaterEqual(len(els),50);self.assertEqual(cov['measured_bulk_element_gap_to_50'],0);self.assertEqual(cov['manufacturer_measured_bulk_elements'],12);self.assertEqual(cov['crm_licence_flagged_values'],0)
 def test_soil_crm_measured_against_other_soil_crms(self):
  got,r=self.run_crm(['crm_nist_srm_2709a','crm_nist_srm_2710a','crm_nist_srm_2586','crm_nist_srm_2780a','crm_nist_srm_1633c'],'crm_nist_srm_2711a',['k_ka1','ti_ka1','ba_ka1','mn_ka1','zn_ka1','sr_ka1'])
  self.assertRecovered(got,.05);self.assertIn('synthetic',r['evidence']);self.assertNotIn('2711a',json.dumps(r))
 def test_usgs_rock_crm_measured_against_other_rocks(self):
  got,_=self.run_crm(['crm_usgs_agv_2','crm_usgs_bcr_2','crm_usgs_bir_1a','crm_usgs_g_3'],'crm_usgs_bhvo_2',['ca_ka1','mn_ka1','fe_ka1','zn_ka1','sr_ka1','zr_ka1'])
  self.assertRecovered(got,.05)
 def test_nist_glass_crm_measured_against_other_glasses(self):
  got,_=self.run_crm(['crm_nist_srm_610','crm_nist_srm_614','crm_nist_srm_616'],'crm_nist_srm_612',['sr_ka1','rb_ka1','th_la1','u_la1'],phase='solid')
  self.assertRecovered(got,.10)  # trace glass: unselected certified/added elements overlap the U/Th L lines in the synthetic spectrum
 def test_information_value_is_loadable_but_not_a_calibrant(self):
  rec=json.loads(self.rt.invoke('template.read',substance_id='crm_nist_srm_610')['template_json']);self.assertEqual(rec,self.rt.templates['crm_nist_srm_610']);self.assertIn('Co',rec['element_mass_fractions']);self.assertNotIn('Co',rec['calibration_eligible_elements']);self.assertEqual(rec['crm_values']['Co']['value_category'],'information')
  h=ShimadzuEdx7200Runtime('edx');h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('method.configure',**dict(METHOD_EXAMPLE,selected_lines=['co_ka1']));h.invoke('power.set',enabled=True);h.step(2)
  acquire(h,BLANK_EXAMPLE|dict(phase='solid'));obs=[]
  for rid in ['crm_nist_srm_610','crm_nist_srm_612','crm_nist_srm_614']:
   x=acquire(h,STANDARD_EXAMPLE|dict(template_id=rid,phase='solid'));obs.append(dict(reference_id=rid,reference_fraction=1.,acquisition_signature=x['acquisition_signature'],line_observations=x['assays'][0]['line_observations']))
  with self.assertRaisesRegex(ActionRejected,'REFERENCE_NOT_CALIBRATION_ELIGIBLE'):h.invoke('calibration.fit',curve_id='co',observations=obs,accept_same_matrix_response=True)

if __name__=='__main__':unittest.main()
