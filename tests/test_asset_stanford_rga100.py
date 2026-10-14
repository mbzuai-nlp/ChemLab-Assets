"""Independent numerical and protocol checks for CHEM-087; no hardware or geometry claim."""
import copy,csv,hashlib,importlib.util,io,json,math,pickle,re,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.stanford_rga100 import *

def ready(plant=None,method=None,library=None):
 r=StanfordRga100Runtime('rga_test',library=library);r.invoke('plant.configure',**(copy.deepcopy(PLANT_EXAMPLE)|dict(adsorbed_gases=[])|(plant or {})));r.invoke('method.configure',**(copy.deepcopy(METHOD_EXAMPLE)|(method or {})));r.invoke('power.set',enabled=True);return r

def scan(r,sample=None,dt=.02):
 r.invoke('filament.set',enabled=False)
 if sample is not None:r.invoke('sample.load',**copy.deepcopy(sample))
 r.invoke('filament.set',enabled=True);r.invoke('scan.start')
 for _ in range(200000):
  if r.phase!='running':break
  r.step(dt)
 else:raise AssertionError('nonterminating scan')
 return r.invoke('result.read')

def calibrated(candidates=None,method=None,plant=None,library=None):
 candidates=candidates or ['nitrogen','argon','carbon_dioxide'];r=ready(plant,dict(candidates=candidates)|(method or {}),library);scan(r,BLANK_EXAMPLE);curves=[]
 for s in candidates:
  ids=[]
  for pressure in [5e-5,2e-4,5e-4]:
   a=STANDARD_EXAMPLE|dict(components=[gas_for_pressure(s,pressure,r.plant)],standard_species_id=s);ids.append(scan(r,a)['record_id'])
  r.invoke('calibration.fit',curve_id=s,species_id=s,record_ids=ids);curves.append(s)
 r.invoke('calibration.select',curve_ids=curves);return r

class RgaTests(unittest.TestCase):
 def rejected(self,r,k,a,code=None):
  before=pickle.dumps(r.__dict__)
  with self.assertRaises(ActionRejected) as ctx:r.invoke(k,**a)
  if code:self.assertEqual(str(ctx.exception),code)
  self.assertEqual(pickle.dumps(r.__dict__),before)
 def test_01_full_contract_metadata_schema(self):
  p=Path(__file__).resolve().parents[1].joinpath('skills/chemistry-lab-assets');sp=importlib.util.spec_from_file_location('rga_asset_tool',p/'scripts/asset_tool.py');tool=importlib.util.module_from_spec(sp);sp.loader.exec_module(tool);schema=json.loads((p/'assets/asset-contract.schema.json').read_text());m=export_stanford_rga100_contract_metadata()
  errors=[]
  for group,kind in [('capabilities','capability'),('state_slots','slot'),('mechanisms','mechanism')]:
   for k,v in m[group].items():errors.extend(tool.validate_value(v,schema['$defs'][kind],schema,path=k))
  self.assertEqual(errors,[])
 def test_02_error_codes_are_real_identifiers(self):
  for c in export_stanford_rga100_contract_metadata()['capabilities'].values():
   self.assertTrue(c['errors'])
   for e in c['errors']:self.assertRegex(e['code'],r'^[A-Z][A-Z0-9_]*$')
 def test_03_state_effects_resolve(self):
  m=export_stanford_rga100_contract_metadata()
  for c in m['capabilities'].values():
   for e in c['state_effects']:self.assertIn(e['state_id'],SLOTS)
 def test_04_read_types(self):
  r=ready()
  for k,v in r.read('evaluator').items():validate(SLOTS[k][0],v)
 def test_05_read_describe_pure(self):
  r=ready();before=pickle.dumps(r.__dict__);r.read();r.describe();r.invoke('template.read',substance_id='nitrogen');self.assertEqual(pickle.dumps(r.__dict__),before)
 def test_06_invalid_argument_nan_atomic(self):
  r=ready();a=copy.deepcopy(METHOD_EXAMPLE);a['electron_energy_v']=float('nan');self.rejected(r,'method.configure',a,'INVALID_ARGUMENT')
 def test_07_bool_not_number(self):
  r=ready();a=copy.deepcopy(METHOD_EXAMPLE);a['emission_current_a']=True;self.rejected(r,'method.configure',a,'INVALID_ARGUMENT')
 def test_08_unknown_extra_argument(self):self.rejected(ready(),'power.set',dict(enabled=True,truth={}), 'INVALID_ARGUMENT')
 def test_09_missing_argument(self):self.rejected(ready(),'power.set',{},'INVALID_ARGUMENT')
 def test_10_initialization_only(self):self.rejected(ready(),'plant.configure',PLANT_EXAMPLE,'INITIALIZATION_ONLY')
 def test_11_all_unsupported_atomic(self):
  r=ready()
  for c in UNSUPPORTED:self.rejected(r,c,{},'UNSUPPORTED')
 def test_12_source_unknown_energy_requires_ack(self):
  r=ready();self.rejected(r,'method.configure',METHOD_EXAMPLE|dict(accept_unknown_ei_conditions=False),'SOURCE_CONDITION_ACK_REQUIRED')
 def test_13_query_only_hydrogen_peroxide(self):
  r=ready();id_=next(k for k,v in r.templates.items() if v.get('ei') is None);self.assertIsNone(json.loads(r.invoke('template.read',substance_id=id_)['template_json'])['ei']);self.rejected(r,'sample.load',SAMPLE_EXAMPLE|dict(components=[{'species_id':id_,'amount_mol':1e-12}]),'REFERENCE_QUERY_ONLY')
 def test_14_duplicate_species(self):
  r=ready();self.rejected(r,'sample.load',SAMPLE_EXAMPLE|dict(components=SAMPLE_EXAMPLE['components']*2),'DUPLICATE_SPECIES')
 def test_15_nonzero_blank_rejected(self):self.rejected(ready(),'sample.load',BLANK_EXAMPLE|dict(components=SAMPLE_EXAMPLE['components']),'NONZERO_BLANK')
 def test_16_standard_must_be_pure(self):self.rejected(ready(),'sample.load',STANDARD_EXAMPLE|dict(components=SAMPLE_EXAMPLE['components']),'PURE_STANDARD_REQUIRED')
 def test_17_unknown_sample_no_hidden_candidate_guard(self):
  r=ready(method=dict(candidates=['argon']));o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',2e-4)]));self.assertTrue(o['complete']);self.assertIsNone(o['species'][0]['partial_pressure_pa'])
 def test_18_loading_filament_on_rejects(self):
  r=ready();r.invoke('filament.set',enabled=True);self.rejected(r,'sample.load',SAMPLE_EXAMPLE,'SEALED_FILAMENT_OFF_REQUIRED')
 def test_19_batch_exchange_mass_closes(self):
  r=ready();r.invoke('sample.load',**SAMPLE_EXAMPLE);n=sum(r.gases.values());r.invoke('sample.load',**STANDARD_EXAMPLE);self.assertAlmostEqual(sum(r.returned.values()),n,25);self.assertLess(abs(r.invoke('product.read')['mass_residual_kg']),1e-24)
 def test_20_unload_does_not_recreate_pumped_gas(self):
  r=ready();r.invoke('sample.load',**SAMPLE_EXAMPLE);r.invoke('vacuum.valve',opened=True);r.invoke('vacuum.pump',enabled=True);r.step(.5);pumped=sum(r.pumped.values());r.invoke('vacuum.valve',opened=False);r.step(.3);n=sum(r.gases.values());o=r.invoke('sample.unload',recipient_id='host');self.assertAlmostEqual(o['transferred_mol'],n,25);self.assertGreater(sum(r.pumped.values()),pumped);self.assertEqual(r.gases,{})
 def test_21_gate_finite(self):
  r=ready();r.invoke('vacuum.valve',opened=True);self.assertEqual(r.valve,0);r.step(.02);self.assertTrue(0<r.valve<.04);r.step(.3);self.assertEqual(r.valve,.04)
 def test_22_ecu_finite_and_power_interlock(self):
  r=ready();r.invoke('power.set',enabled=False);r.invoke('ecu.set',detached=True);r.step(.02);self.assertTrue(0<r.ecu<.08);self.rejected(r,'power.set',dict(enabled=True),'ECU_DETACHED');r.step(.5);self.assertEqual(r.ecu,.08)
 def test_23_ecu_live_rejected(self):self.rejected(ready(),'ecu.set',dict(detached=True),'COLD_POWER_OFF_REQUIRED')
 def test_24_supplied_voltage_guard(self):
  r=StanfordRga100Runtime();r.invoke('plant.configure',**(PLANT_EXAMPLE|dict(supply_voltage_v=28.)));self.rejected(r,'power.set',dict(enabled=True),'SUPPLY_VOLTAGE')
 def test_25_zero_emission_cannot_enable(self):self.rejected(ready(method={'emission_current_a':0.}),'filament.set',dict(enabled=True),'ZERO_EMISSION')
 def test_26_operating_pressure_different_from_linear(self):
  r=ready();r.invoke('sample.load',**(SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',OPERATING_MAX_PA*1.1)])));self.rejected(r,'filament.set',dict(enabled=True),'VACUUM_OR_POWER')
 def test_27_dark_scan_allowed(self):
  r=ready();r.invoke('sample.load',**SAMPLE_EXAMPLE);r.invoke('scan.start');r.step(1.);o=r.invoke('result.read');self.assertTrue(o['complete']);self.assertIn('filament_off_dark_scan',o['quality']);self.assertTrue(all(p['current_a']==0 for p in o['points']))
 def test_28_analog_inclusive_grid_timing(self):
  r=ready(method=dict(mass_min=27,mass_max=29,points_per_u=20));o=scan(r,BLANK_EXAMPLE);self.assertEqual(len(o['points']),41);self.assertEqual(o['points'][0]['nominal_mass_to_charge'],27);self.assertEqual(o['points'][-1]['nominal_mass_to_charge'],29);self.assertAlmostEqual(sum(p['end_s']-p['start_s'] for p in o['points']),41*NF_ANALOG_S_U[7]/20,12)
 def test_29_mid_seven_real_subwindows_and_peak(self):
  r=ready(method=dict(mode='mid',mid_masses=[28,44]));o=scan(r,SAMPLE_EXAMPLE);self.assertEqual(len(o['points']),14);self.assertEqual(len(o['reported_peaks']),2)
  for i,p in enumerate(o['reported_peaks']):self.assertEqual(p['current_a'],max(x['current_a'] for x in o['points'][7*i:7*i+7]));self.assertAlmostEqual(p['end_s']-p['start_s'],NF_SINGLE_S[7],12)
 def test_30_histogram_integer_range(self):
  o=scan(ready(method=dict(mode='histogram',mass_min=27,mass_max=29)),SAMPLE_EXAMPLE);self.assertEqual([x['nominal_mass_to_charge'] for x in o['reported_peaks']],[27,28,29])
 def test_31_mid_cycles_timestamp(self):
  o=scan(ready(method=dict(mode='mid',mid_masses=[28],cycles=3)),SAMPLE_EXAMPLE);self.assertEqual(len(o['reported_peaks']),3);self.assertEqual([x['cycle'] for x in o['reported_peaks']],[0,1,2]);self.assertLess(o['reported_peaks'][0]['end_s'],o['reported_peaks'][2]['start_s'])
 def test_32_nf_table_uses_source_times(self):
  for nf in range(8):
   r=ready(method=dict(mode='mid',mid_masses=[28],noise_floor=nf));o=scan(r,SAMPLE_EXAMPLE);self.assertAlmostEqual(sum(p['end_s']-p['start_s'] for p in o['points']),NF_SINGLE_S[nf],10)
 def test_33_grid_invalid_atomic(self):self.rejected(ready(),'method.configure',METHOD_EXAMPLE|dict(mass_min=40,mass_max=12),'INVALID_SCAN_GRID')
 def test_34_window_capacity_atomic(self):self.rejected(ready(),'method.configure',METHOD_EXAMPLE|dict(mass_min=1,mass_max=100,points_per_u=25,cycles=20),'WINDOW_CAPACITY')
 def test_35_width_is_tenth_not_fwhm(self):
  r=ready();p=r._shape('argon',[40,40+.5]);self.assertAlmostEqual(p[1]/p[0],.1,12)
 def test_36_full_source_normalization_not_recropped(self):
  r=ready();s=next(k for k,v in r.templates.items() if v.get('ei') and max(v['ei']['mass_to_charge_relative'])>100 and max(zip(v['ei']['relative_intensity'],v['ei']['mass_to_charge_relative']))[1]>100);ei=r.templates[s]['ei'];x=np.asarray(ei['mass_to_charge_relative']);y=np.asarray(ei['relative_intensity']);self.assertLess(max(y[(x>=1)&(x<=100)]),1.);self.assertLess(max(r._shape(s,list(range(1,101)))),1.00001)
 def test_37_mass_to_charge_si_conversion(self):
  o=scan(ready(method=dict(mode='mid',mid_masses=[28])),SAMPLE_EXAMPLE);p=o['points'][0];self.assertEqual(p['mass_to_charge_kg_c'],p['nominal_mass_to_charge']*U_KG/E_C);self.assertGreater(p['mass_to_charge_kg_c'],1e-8)
 def test_38_window_integration_partition_invariance(self):
  a=scan(ready(),SAMPLE_EXAMPLE,dt=.02);b=scan(ready(),SAMPLE_EXAMPLE,dt=.007);np.testing.assert_allclose([p['current_a'] for p in a['points']],[p['current_a'] for p in b['points']],rtol=1e-12,atol=1e-25);np.testing.assert_allclose([p['end_s'] for p in a['points']],[p['end_s'] for p in b['points']],atol=1e-11)
 def test_39_noise_seed_reproducible(self):
  a=scan(ready(plant={'noise_scale':.1}),SAMPLE_EXAMPLE);b=scan(ready(plant={'noise_scale':.1}),SAMPLE_EXAMPLE);self.assertEqual(a['points'],b['points'])
 def test_40_noise_is_nonzero_if_requested(self):
  a=scan(ready(plant={'noise_scale':1.}),BLANK_EXAMPLE);self.assertGreater(np.std([p['current_a'] for p in a['points']]),1e-14)
 def test_41_zero_is_not_gas_blank(self):
  r=ready();r.invoke('sample.load',**SAMPLE_EXAMPLE);r.invoke('filament.set',enabled=True);o=r.invoke('detector.zero');self.assertEqual(o['zero_current_a'],r.plant['dark_current_a']);self.assertFalse(r.invoke('blank.read')['valid']);self.assertGreater(r._pressure(),0)
 def test_42_partial_cancel_window_conserved(self):
  r=ready(method={'noise_floor':0});r.invoke('sample.load',**SAMPLE_EXAMPLE);r.invoke('filament.set',enabled=True);r.invoke('scan.start');r.step(.035);n=dict(r.gases);r.invoke('measurement.stop');o=r.invoke('result.read');self.assertFalse(o['complete']);self.assertGreater(len(o['points']),0);self.assertLess(o['points'][-1]['end_s']-o['points'][-1]['start_s'],.2);self.assertEqual(r.gases,n)
 def test_43_cancel_does_not_complete_partial_miniscan(self):
  r=ready(method=dict(mode='mid',mid_masses=[28],noise_floor=0));r.invoke('filament.set',enabled=True);r.invoke('scan.start');r.step(.05);r.invoke('measurement.stop');self.assertEqual(r.peaks,[]);self.assertEqual(len(r.points),1)
 def test_44_power_off_saves_incomplete(self):
  r=ready();r.invoke('filament.set',enabled=True);r.invoke('scan.start');r.step(.04);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.assertFalse(r.filament);self.assertTrue(r.points)
 def test_45_changing_method_running_rejected(self):
  r=ready();r.invoke('scan.start');self.rejected(r,'method.configure',METHOD_EXAMPLE,'BUSY')
 def test_46_live_illumination_change_null(self):
  r=calibrated();r.invoke('filament.set',enabled=False);r.invoke('sample.load',**SAMPLE_EXAMPLE);r.invoke('filament.set',enabled=True);r.invoke('scan.start');r.step(.04);r.invoke('filament.set',enabled=False);r.step(1.);self.assertIn('illumination_changed_during_scan',r.invoke('result.read')['quality']);self.assertIsNone(r.invoke('result.read')['total_partial_pressure_pa'])
 def test_47_pump_exact_exponential_and_species_balance(self):
  r=ready();r.invoke('sample.load',**SAMPLE_EXAMPLE);r.invoke('vacuum.valve',opened=True);r.step(.3);n=dict(r.gases);r.invoke('vacuum.pump',enabled=True);r.step(.4)
  for s,v in n.items():self.assertAlmostEqual(r.gases[s]/v,math.exp(-2),12)
  self.assertLess(abs(r.invoke('product.read')['residual_mol']),1e-24)
 def test_48_pump_step_partition(self):
  a=ready();b=ready()
  for r in [a,b]:r.invoke('sample.load',**SAMPLE_EXAMPLE);r.invoke('vacuum.valve',opened=True);r.step(.3);r.invoke('vacuum.pump',enabled=True)
  a.step(.5)
  for _ in range(50):b.step(.01)
  for s in a.gases:self.assertAlmostEqual(a.gases[s],b.gases[s],25)
 def test_49_degas_source_ramp(self):
  r=ready();r.invoke('degas.start',duration_min=2);r.step(30);self.assertAlmostEqual(r.emission,.0105,5);self.assertEqual(r.energy_v,400.);self.assertAlmostEqual(r.degas_power,4.2,2);r.step(31.);self.assertAlmostEqual(r.emission,.02,12);self.assertAlmostEqual(r.degas_power,8.,12)
 def test_50_degas_cancellation_retains_material(self):
  r=ready(plant=dict(adsorbed_gases=[{'species_id':'water','amount_mol':1e-10}],desorption_rate_mol_j=1e-12));r.invoke('degas.start',duration_min=1);r.step(.3);n=r.gases['water'];r.invoke('measurement.stop');self.assertGreater(n,0);self.assertEqual(r.gases['water'],n);self.assertLess(r.adsorbed['water'],1e-10);self.assertLess(abs(r.invoke('product.read')['residual_mol']),1e-24)
 def test_51_degas_completes_only_after_source_minute(self):
  r=ready();r.invoke('filament.set',enabled=True);r.invoke('degas.start',duration_min=1);r.step(59.);self.assertEqual(r.phase,'running');r.step(1.02);self.assertEqual(r.phase,'succeeded');self.assertTrue(r.filament);self.assertEqual(r.degas_power,0.)
 def test_52_overpressure_actual_degas_trip(self):
  r=ready(plant=dict(adsorbed_gases=[{'species_id':'water','amount_mol':1e-8}],desorption_rate_mol_j=1e-6));r.invoke('degas.start',duration_min=1);r.step(.1);self.assertEqual(r.phase,'failed');self.assertTrue(r.read()['led.overpressure']);self.assertFalse(r.filament);self.assertEqual(r.emission,0.);self.assertLess(abs(r.invoke('product.read')['mass_residual_kg']),1e-24)
 def test_53_heat_balance(self):
  r=ready();r.invoke('degas.start',duration_min=1);r.step(2.);r.invoke('measurement.stop');r.step(2.);o=r.invoke('product.read');self.assertGreater(o['electrical_energy_j'],0);self.assertGreater(o['thermal_lost_j'],0);self.assertLess(abs(o['energy_residual_j']),1e-10)
 def test_54_three_gas_observed_quantification(self):
  r=calibrated();o=scan(r,SAMPLE_EXAMPLE);self.assertEqual(o['quality'],[]);expected=dict(nitrogen=2e-4,argon=1e-4,carbon_dioxide=1.5e-4)
  for row in o['species']:self.assertAlmostEqual(row['partial_pressure_pa'],expected[row['species_id']],13)
 def test_55_calibration_slope_independent_anchor(self):
  r=calibrated(['nitrogen']);self.assertAlmostEqual(r._curves['nitrogen']['sensitivity_a_pa'],2e-4/(101325/760),17)
 def test_56_calibration_only_actual_records(self):self.rejected(ready(),'calibration.fit',dict(curve_id='x',species_id='nitrogen',record_ids=['a','b','c']),'BLANK_REQUIRED')
 def test_57_duplicate_standard_record_rejected(self):
  r=ready();scan(r,BLANK_EXAMPLE);o=scan(r,STANDARD_EXAMPLE);self.rejected(r,'calibration.fit',dict(curve_id='x',species_id='nitrogen',record_ids=[o['record_id']]*3),'DUPLICATE_RECORD')
 def test_58_nonstandard_record_cannot_calibrate(self):
  r=ready();scan(r,BLANK_EXAMPLE);ids=[scan(r,SAMPLE_EXAMPLE)['record_id'] for _ in range(3)];self.rejected(r,'calibration.fit',dict(curve_id='x',species_id='nitrogen',record_ids=ids),'ACTUAL_STANDARD_RECORD_REQUIRED')
 def test_59_method_voltage_invalidates_observed_curve(self):
  r=calibrated(['nitrogen']);r.invoke('method.configure',**(r.method|dict(electron_energy_v=80.)));self.assertFalse(r.read()['calibration.ready']);self.rejected(r,'calibration.select',dict(curve_ids=['nitrogen']),'CALIBRATION_SIGNATURE')
 def test_60_blank_missing_null(self):
  o=scan(ready(),SAMPLE_EXAMPLE);self.assertIn('blank_missing',o['quality']);self.assertIsNone(o['total_partial_pressure_pa'])
 def test_61_unresolved_N2_CO_returns_null(self):
  r=calibrated(['nitrogen','carbon_monoxide'],dict(mode='mid',mid_masses=[28]));o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',1e-4),gas_for_pressure('carbon_monoxide',2e-4)]));self.assertIn('ambiguous_candidate_matrix',o['quality']);self.assertTrue(all(x['partial_pressure_pa'] is None for x in o['species']))
 def test_62_N2_CO_multiple_fragments_resolve(self):
  r=calibrated(['nitrogen','carbon_monoxide'],dict(mode='mid',mid_masses=[12,14,16,28]));o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',1e-4),gas_for_pressure('carbon_monoxide',2e-4)]));self.assertFalse(o['quality']);self.assertAlmostEqual(o['species'][0]['partial_pressure_pa'],1e-4,12);self.assertAlmostEqual(o['species'][1]['partial_pressure_pa'],2e-4,12)
 def test_63_missing_candidate_leaves_observed_residual(self):
  r=calibrated(['nitrogen']);o=scan(r,SAMPLE_EXAMPLE);self.assertIn('unexplained_signal_residual',o['quality']);self.assertIsNone(o['species'][0]['partial_pressure_pa'])
 def test_64_outside_calibration_preserves_null(self):
  r=calibrated(['nitrogen']);o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',8e-4)]));self.assertIsNone(o['species'][0]['partial_pressure_pa']);self.assertIn('outside_observed_calibration_or_Faraday_range',o['species'][0]['quality'])
 def test_65_concentration_is_inferred_pressure_conversion(self):
  r=calibrated(['nitrogen']);o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',2e-4)]));q=o['species'][0];self.assertAlmostEqual(q['mass_concentration_kg_m3'],q['partial_pressure_pa']*.028014/(R_GAS*298.15),20)
 def test_66_FC_nonlinear_pressure_QC(self):
  r=calibrated(['nitrogen']);o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',LINEAR_MAX_PA*2)]));self.assertTrue(o['complete']);self.assertIn('above_Faraday_linear_partial_pressure_domain',o['quality']);self.assertIsNone(o['total_partial_pressure_pa'])
 def test_67_saturation_QC(self):
  r=ready(plant={'maximum_current_a':1e-12});o=scan(r,SAMPLE_EXAMPLE);self.assertIn('detector_saturated',o['quality']);self.assertTrue(any(p['saturated'] for p in o['points']))
 def test_68_pressure_change_during_scan_QC(self):
  r=ready();r.invoke('sample.load',**SAMPLE_EXAMPLE);r.invoke('filament.set',enabled=True);r.invoke('scan.start');r.invoke('vacuum.valve',opened=True);r.invoke('vacuum.pump',enabled=True);r.step(1.);self.assertIn('host_pressure_changed_during_scan',r.invoke('result.read')['quality'])
 def test_69_standard_pressure_changed_before_scan_rejects_fit(self):
  r=ready();scan(r,BLANK_EXAMPLE);r.invoke('filament.set',enabled=False);r.invoke('sample.load',**STANDARD_EXAMPLE);r.invoke('vacuum.valve',opened=True);r.invoke('vacuum.pump',enabled=True);r.step(.5);r.invoke('vacuum.valve',opened=False);r.step(.3);r.invoke('filament.set',enabled=True);r.invoke('scan.start');r.step(1.);self.assertIn('standard_preparation_pressure_changed',r.invoke('result.read')['quality'])
 def test_70_export_real_csv_roundtrip(self):
  r=ready();o=scan(r,SAMPLE_EXAMPLE);csv_rows=list(csv.DictReader(io.StringIO(r.invoke('result.export',format='csv')['content'])));self.assertEqual(len(csv_rows),len(o['points']))
  for raw,row in zip(csv_rows,o['points']):self.assertEqual(float(raw['current_a']),row['current_a']);self.assertEqual(float(raw['mass_to_charge_kg_c']),row['mass_to_charge_kg_c'])
  self.assertEqual(json.loads(r.invoke('result.export',format='json')['content']),o)
 def test_71_record_freeze_after_method_change(self):
  r=ready();o=scan(r,SAMPLE_EXAMPLE);r.invoke('method.configure',**(r.method|dict(mode='mid')));self.assertEqual(r.invoke('records.read',record_id=o['record_id']),o)
 def test_72_record_capacity_atomic(self):
  r=ready();r._records={str(i):{} for i in range(MAX_RECORDS)};self.rejected(r,'scan.start',{},'RECORD_CAPACITY')
 def test_73_session_ACL_hidden_composition(self):
  r=ready();s=r.session();result=s.invoke(instance_id=r.instance_id,capability_id='sample.load',arguments=SAMPLE_EXAMPLE,request_id='x');self.assertEqual(result['error']['code'],'ACCESS_DENIED');self.assertNotIn('truth.ledger',s.read(instance_id=r.instance_id)['observations']);self.assertNotIn('sample.load',s.describe(instance_id=r.instance_id)['capabilities'])
 def test_74_same_request_dedup(self):
  r=ready();s=r.session();a=s.invoke(instance_id=r.instance_id,capability_id='scan.start',arguments={},request_id='once');revision=r.revision;b=s.invoke(instance_id=r.instance_id,capability_id='scan.start',arguments={},request_id='once');self.assertEqual(a,b);self.assertEqual(r.revision,revision)
 def test_75_request_conflict_atomic(self):
  r=ready();s=r.session();s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='x');before=pickle.dumps(r.__dict__);o=s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='x');self.assertEqual(o['error']['code'],'REQUEST_CONFLICT');self.assertEqual(before,pickle.dumps(r.__dict__))
 def test_76_poll_owner_and_historical_receipt(self):
  r=ready();r.register_principal('other','agent');s=r.session();o=s.invoke(instance_id=r.instance_id,capability_id='scan.start',arguments={},request_id='x');op=o['operation_id'];r.step(1.);a=s.poll(op)
  with self.assertRaises(ActionRejected):r.session('other').poll(op)
  r.invoke('scan.start');r.step(1.);self.assertEqual(s.poll(op),a)
 def test_77_session_cancel_idempotent(self):
  r=ready();s=r.session();o=s.invoke(instance_id=r.instance_id,capability_id='scan.start',arguments={},request_id='x');r.step(.04);a=s.cancel(o['operation_id'],'cancel');b=s.cancel(o['operation_id'],'cancel');self.assertEqual(a,b);self.assertEqual(s.poll(o['operation_id'])['status'],'cancelled')
 def test_78_two_instance_isolation(self):
  a=ready();b=ready();before=pickle.dumps(b.__dict__);scan(a,SAMPLE_EXAMPLE);self.assertEqual(pickle.dumps(b.__dict__),before)
 def test_79_real_sample_time_not_query_time(self):
  r=ready();scan(r,SAMPLE_EXAMPLE);last=r.points[-1];r.step(.5);obs=r.session().read(instance_id=r.instance_id)['observations']['detector.current_a'];self.assertEqual(obs['sample_time_s'],last['end_s']);self.assertEqual(obs['sample_window_s'],[last['start_s'],last['end_s']]);self.assertLess(obs['sample_time_s'],r.time_s)
 def test_80_invalid_step_is_atomic(self):
  r=ready()
  for value in [0,-1,True,float('nan')]:
   before=pickle.dumps(r.__dict__)
   with self.assertRaises(ActionRejected):r.step(value)
   self.assertEqual(pickle.dumps(r.__dict__),before)
 def test_81_leds_and_RS232_unknown(self):
  r=ready();self.assertTrue(r.read()['led.power']);self.assertIsNone(r.read()['led.rs232_active']);self.assertFalse(r.read()['led.em_active']);r.invoke('degas.start',duration_min=1);r.step(.02);self.assertTrue(r.read()['led.degas']);self.assertTrue(r.read()['led.filament']);r.invoke('power.set',enabled=False);self.assertFalse(any(r.read()[k] for k in SLOTS if k.startswith('led.') and k!='led.rs232_active'))
 def test_82_host_filament_fault_is_declared_not_inferred(self):
  r=ready();r.invoke('power.set',enabled=False);r.invoke('host.filament_fault',faulted=True);r.invoke('power.set',enabled=True);self.assertTrue(r.read()['led.filament_fault']);self.rejected(r,'filament.set',dict(enabled=True),'FILAMENT_FAULT')
 def test_83_unknown_instance_and_visibility(self):
  r=ready();o=r.session().invoke(instance_id='other',capability_id='power.set',arguments={'enabled':False},request_id='x');self.assertEqual(o['error']['code'],'UNKNOWN_INSTANCE')
  with self.assertRaises(ActionRejected):r.read('administrator')
 def test_84_degas_operation_does_not_reuse_old_scan_result(self):
  r=ready();scan(r,SAMPLE_EXAMPLE);r.invoke('filament.set',enabled=False);s=r.session();o=s.invoke(instance_id=r.instance_id,capability_id='degas.start',arguments={'duration_min':1},request_id='d');r.step(.04);s.cancel(o['operation_id'],'c');receipt=s.poll(o['operation_id']);self.assertIn('degas_elapsed_s',receipt['result']);self.assertNotIn('record_id',receipt['result']);self.assertNotIn('truth',json.dumps(receipt))

 def test_85_native_mid_one_mass_has_one_observation(self):
  r=calibrated(['nitrogen','carbon_monoxide'],dict(mode='mid',mid_masses=[28]));o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',1e-4),gas_for_pressure('carbon_monoxide',2e-4)]));self.assertEqual(o['matrix_rank'],1);self.assertEqual(json.loads(o['provenance_json'])['analysis_observation_count'],1);self.assertEqual(len(o['points']),7)
 def test_86_extra_miniscan_currents_not_analysis_observations(self):
  r=calibrated(['nitrogen'],dict(mode='mid',mid_masses=[14,28,29]));o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('nitrogen',2e-4)]));b=copy.deepcopy(o)
  for p in b['points']:p['current_a']=1e-5
  a=r._inverse_observations(o,r._blank(),['nitrogen'],r._curves_now());c=r._inverse_observations(b,r._blank(),['nitrogen'],r._curves_now());np.testing.assert_array_equal(a[0],c[0]);self.assertEqual(a[1:],c[1:])
 def test_87_native_peak_mixture_uses_max_of_sum(self):
  library=_library_fixture=copy.deepcopy(StanfordRga100Runtime().library)
  for name,mz,intensity in [('test_peak_a',[27.8,29,30],[1,.2,.1]),('test_peak_b',[28.2,29,30],[1,.6,.7])]:
   record=copy.deepcopy(library['compounds']['nitrogen']);record['ei']['mass_to_charge_relative']=mz;record['ei']['relative_intensity']=intensity;record['evidence']='synthetic_unit_test_fixture';library['compounds'][name]=record
  r=calibrated(['test_peak_a','test_peak_b'],dict(mode='mid',mid_masses=[28,29,30]),plant={'peak_width_tenth_u':.45},library=library);o=scan(r,SAMPLE_EXAMPLE|dict(components=[gas_for_pressure('test_peak_a',1e-4),gas_for_pressure('test_peak_b',2e-4)]));self.assertEqual(o['quality'],[])
  for row,pressure in zip(o['species'],[1e-4,2e-4]):self.assertAlmostEqual(row['partial_pressure_pa'],pressure,12)
  values,profile,groups,raw=r._observation_space(o,['test_peak_a','test_peak_b']);coeff=np.array([r._gain('test_peak_a')*1e-4,r._gain('test_peak_b')*2e-4]);self.assertGreater((profile@coeff)[0]-np.max(raw[groups[0]]@coeff),1e-11)

if __name__=='__main__':unittest.main()
