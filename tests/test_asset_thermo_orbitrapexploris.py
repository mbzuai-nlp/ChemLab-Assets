"""Numerical, source-domain and authenticated-protocol regression for CHEM-088."""
import copy,io,json,math,re,sys,unittest,csv
from pathlib import Path
import numpy as np
import importlib.util
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.thermo_orbitrapexploris import *
LIB=json.loads((ROOT/TEMPLATE_REL).read_text())
def fixture(**kw):
 r=ThermoOrbitrapexplorisRuntime(library=LIB);p=copy.deepcopy(PLANT_EXAMPLE);p.update(noise_relative=0.,mass_drift_fraction_s=0.,**kw);r.invoke('plant.configure',**p);m=copy.deepcopy(METHOD_EXAMPLE);m['transfer_temperature_k']=p['ambient_temperature_k'];r.invoke('method.configure',**m);r.invoke('power.set',enabled=True);return r
def sample(i='sm808001',kind='sample',amount=1e-8):return dict(label='host_declared_solution',kind=kind,volume_m3=1e-7,components=[] if kind=='blank' else [{'template_id':i,'amount_mol':amount}],accept_transferred_reference=True)
def run(r,i='sm808001',kind='sample',amount=1e-8):
 r.invoke('source.set',enabled=False);r.invoke('sample.load',**sample(i,kind,amount));r.invoke('source.set',enabled=True);r.invoke('scan.start');r.step(1.);return r.invoke('result.read')
def calibrated():
 r=fixture();run(r,kind='blank');ids=[]
 for i in ['sm800003','sm808001','sm814103']:ids.append(run(r,i,'standard')['record_id'])
 r.invoke('calibration.fit',curve_id='axis',record_ids=ids);r.invoke('calibration.select',curve_id='axis');return r
class RuntimeTests(unittest.TestCase):
 def reject(self,r,cap,code=None,**a):
  before=copy.deepcopy(r.read('evaluator'));rev=r.revision;time=r.time_s
  with self.assertRaises(ActionRejected) as cm:r.invoke(cap,**a)
  if code:self.assertEqual(str(cm.exception),code)
  self.assertEqual(before,r.read('evaluator'));self.assertEqual(rev,r.revision);self.assertEqual(time,r.time_s)
 def test_01_schema_and_metadata(self):
  meta=export_thermo_orbitrapexploris_contract_metadata();schema=json.loads(Path(__file__).resolve().parents[1].joinpath('skills/chemistry-lab-assets/assets/asset-contract.schema.json').read_text())
  spec=importlib.util.spec_from_file_location('orbitrap_asset_tool',Path(__file__).resolve().parents[1].joinpath('skills/chemistry-lab-assets/scripts/asset_tool.py'));tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
  for key,kind in [('capabilities','capability'),('state_slots','slot'),('mechanisms','mechanism')]:
   for name,x in meta[key].items():self.assertEqual(tool.validate_value(x,schema['$defs'][kind],schema,path=name),[],name)
  for name,c in CAPS.items():validate(c[0],c[6])
 def test_02_metadata_error_literals(self):
  meta=export_thermo_orbitrapexploris_contract_metadata()
  for c in meta['capabilities'].values():
   for e in c['errors']:self.assertRegex(e['code'],r'^[A-Z][A-Z0-9_]*$')
 def test_03_no_old_instrument_physics(self):
  text=Path(__file__).read_text();self.assertEqual(CATALOG_ID,'CHEM-088');self.assertEqual(len(RESOLUTIONS),7);self.assertNotIn('faraday_cup',[i for c in CAPS.values() for i in c[2]])
 def test_04_zero_clock_queries(self):
  r=fixture();before=copy.deepcopy(r.read('evaluator'));rev=r.revision;r.describe();r.invoke('template.read',substance_id='sm808001');r.invoke('records.list');r.invoke('process.measure');self.assertEqual(r.read('evaluator'),before);self.assertEqual(r.revision,rev)
 def test_05_step_invalid(self):
  r=fixture()
  for x in [0,-.1,float('nan'),float('inf'),True]:
   with self.assertRaises(ActionRejected):r.step(x)
  self.assertEqual(r.time_s,0)
 def test_06_configuration_initial_only(self):
  r=fixture();self.reject(r,'plant.configure','INITIALIZATION_ONLY',**PLANT_EXAMPLE)
 def test_07_initial_clock_not_resettable(self):
  r=ThermoOrbitrapexplorisRuntime(library=LIB);r.step(.04);self.reject(r,'plant.configure','INITIALIZATION_ONLY',**PLANT_EXAMPLE)
 def test_08_parameter_boolean_rejected(self):
  r=fixture();m=copy.deepcopy(r.method);m['mass_min']=True;self.reject(r,'method.configure','INVALID_ARGUMENT',**m)
 def test_09_nan_rejected(self):
  r=fixture();m=copy.deepcopy(r.method);m['spray_voltage_v']=float('nan');self.reject(r,'method.configure','INVALID_ARGUMENT',**m)
 def test_10_unexpected_argument(self):
  r=fixture();self.reject(r,'power.set','INVALID_ARGUMENT',enabled=True,secret=True)
 def test_11_fullrange_ratio(self):
  r=fixture();m=copy.deepcopy(r.method);m['mass_max']=3000;self.reject(r,'method.configure','MASS_RANGE_RATIO',**m);m['mass_min']=200;r.invoke('method.configure',**m)
 def test_12_range_order(self):
  r=fixture();m=copy.deepcopy(r.method);m['mass_min']=m['mass_max'];self.reject(r,'method.configure','MASS_RANGE_RATIO',**m)
 def test_13_exact_resolution_settings(self):
  r=fixture()
  for value in RESOLUTIONS:
   m=copy.deepcopy(r.method);m['resolution']=value;r.invoke('method.configure',**m);self.assertEqual(r.read()['resolution.actual_at_200'],value)
  m['resolution']=35000;self.reject(r,'method.configure','INVALID_ARGUMENT',**m)
 def test_14_polarity_voltage(self):
  r=fixture();m=copy.deepcopy(r.method);m['polarity']='negative';self.reject(r,'method.configure','POLARITY_VOLTAGE',**m);m['spray_voltage_v']=-3500;r.invoke('method.configure',**m)
 def test_15_spray_limits(self):
  r=fixture();m=copy.deepcopy(r.method);m['spray_voltage_v']=6001;self.reject(r,'method.configure','INVALID_ARGUMENT',**m)
 def test_16_ms2_precursor_limit(self):
  r=fixture();m=copy.deepcopy(r.method);m.update(mode='ms2',precursor_mz=2600);self.reject(r,'method.configure','INVALID_ARGUMENT',**m)
 def test_17_segmented_isolation_width(self):
  r=fixture()
  for mass,minimum in [(400,.4),(500,.7),(800,1),(1200,1.5),(1800,2),(2300,3)]:
   m=copy.deepcopy(r.method);m.update(mode='ms2',precursor_mz=mass,isolation_width_relative=minimum);r.invoke('method.configure',**m)
   if minimum>.4:m['isolation_width_relative']=minimum-.01;self.reject(r,'method.configure','ISOLATION_WIDTH',**m)
 def test_18_ms2_width_max_not_sim(self):
  r=fixture();m=copy.deepcopy(r.method);m.update(mode='ms2',isolation_width_relative=2000);self.reject(r,'method.configure','INVALID_ARGUMENT',**m)
 def test_19_agc_mode_domain(self):
  r=fixture();m=copy.deepcopy(r.method);m['agc_normalized']=20;self.reject(r,'method.configure','AGC_RANGE',**m);m['mode']='ms2';r.invoke('method.configure',**m)
 def test_20_agc_fraction_conversion(self):
  r=fixture();run(r);self.assertEqual(r.scans[0]['agc_target_charges'],100000.);self.assertAlmostEqual(r.scans[0]['agc_fill_charges'],100000.,places=4)
 def test_21_agc_maximum_injection(self):
  r=fixture();result=run(r,amount=1e-13);s=result['scans'][0];self.assertTrue(s['injection_limited']);self.assertAlmostEqual(s['fill_end_s']-s['fill_start_s'],r.method['maximum_injection_s'])
 def test_22_blank_fills_no_charge(self):
  r=fixture();s=run(r,kind='blank')['scans'][0];self.assertEqual(s['agc_fill_charges'],0);self.assertTrue(s['injection_limited']);self.assertFalse(s['centroids'])
 def test_23_needle_domain(self):
  r=fixture();m=copy.deepcopy(r.method);m['flow_m3_s']=1e-9/60;self.reject(r,'method.configure','NEEDLE_FLOW_RANGE',**m)
 def test_24_lowflow_needle(self):
  r=ThermoOrbitrapexplorisRuntime(library=LIB);p=copy.deepcopy(PLANT_EXAMPLE);p['needle']='low_flow';r.invoke('plant.configure',**p);m=copy.deepcopy(METHOD_EXAMPLE);m['flow_m3_s']=5e-9/60;r.invoke('method.configure',**m)
 def test_25_auxiliary_heat_guard(self):
  r=fixture();m=copy.deepcopy(r.method);m['vaporizer_temperature_k']=473.15;self.reject(r,'method.configure','AUXILIARY_GAS_REQUIRED',**m);m['auxiliary_gas_arbitrary']=5;r.invoke('method.configure',**m)
 def test_26_heat_two_nodes(self):
  r=fixture();m=copy.deepcopy(r.method);m.update(transfer_temperature_k=373.15,vaporizer_temperature_k=423.15,auxiliary_gas_arbitrary=5);r.invoke('method.configure',**m);r.invoke('source.set',enabled=True);r.step(.1);self.assertGreater(r.temperature,298.15);self.assertGreater(r.vaporizer_temperature,r.temperature);self.assertLess(r.temperature,373.15);self.assertAlmostEqual(r._ledger()['energy_residual_j'],0,places=10)
 def test_27_source_transfer_ack(self):
  r=fixture();a=sample();a['accept_transferred_reference']=False;self.reject(r,'sample.load','SOURCE_TRANSFER_ACCEPTANCE_REQUIRED',**a)
 def test_28_unknown_source(self):
  r=fixture();self.reject(r,'sample.load','UNKNOWN_TEMPLATE',**sample('invented'))
 def test_29_duplicate_species(self):
  r=fixture();a=sample();a['components']*=2;self.reject(r,'sample.load','DUPLICATE_COMPONENT',**a)
 def test_30_nonzero_sample(self):
  r=fixture();a=sample();a['volume_m3']=0;self.reject(r,'sample.load','INVALID_ARGUMENT',**a)
 def test_31_blank_truth_guard(self):
  r=fixture();a=sample();a['kind']='blank';self.reject(r,'sample.load','INVALID_SAMPLE_KIND',**a)
 def test_32_source_enabled_loading_rejected(self):
  r=fixture();r.invoke('source.set',enabled=True);self.reject(r,'sample.load','SOURCE_ACTIVE',**sample())
 def test_33_evaluator_filter(self):
  r=fixture();self.assertNotIn('truth.ledger',r.read());self.assertIn('truth.ledger',r.read('evaluator'));self.assertNotIn('sample.components',r.read())
 def test_34_roles_cannot_self_promote(self):
  r=fixture()
  with self.assertRaises(ActionRejected):r.register_principal('__public_agent__','evaluator')
  response=r.session().invoke(instance_id=r.instance_id,capability_id='sample.load',arguments=sample(),request_id='a');self.assertEqual(response['error']['code'],'ACCESS_DENIED')
 def test_35_session_instance_guard(self):
  r=fixture();res=r.session().invoke(instance_id='other',capability_id='power.set',arguments={'enabled':True},request_id='a');self.assertEqual(res['error']['code'],'UNKNOWN_INSTANCE')
 def test_36_read_protected_slot(self):
  r=fixture()
  with self.assertRaises(ActionRejected):r.session().read(instance_id=r.instance_id,state_ids=['truth.ledger'])
 def test_37_describe_filtered(self):
  r=fixture();d=r.describe();self.assertNotIn('sample.load',d['capabilities']);self.assertNotIn('truth.ledger',d['state_slots'])
 def test_38_request_id_dedup(self):
  r=fixture();s=r.session();kwargs=dict(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='once');a=s.invoke(**kwargs);r.step(.04);self.assertEqual(s.invoke(**kwargs),a)
 def test_39_request_conflict(self):
  r=fixture();s=r.session();s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='once');a=s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='once');self.assertEqual(a['error']['code'],'REQUEST_CONFLICT');self.assertTrue(r.powered)
 def test_40_operation_owner(self):
  r=fixture();r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);s=r.session();a=s.invoke(instance_id=r.instance_id,capability_id='scan.start',arguments={},request_id='a');r.register_principal('other','agent')
  with self.assertRaises(ActionRejected):r.session('other').poll(a['operation_id'])
  r.step(.2);self.assertEqual(s.poll(a['operation_id'])['status'],'succeeded')
 def test_41_cancel_dedup(self):
  r=fixture();r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);s=r.session();a=s.invoke(instance_id=r.instance_id,capability_id='scan.start',arguments={},request_id='start');r.step(.03);c=s.cancel(a['operation_id'],'stop');self.assertEqual(s.cancel(a['operation_id'],'stop'),c);self.assertEqual(r.phase,'cancelled')
 def test_42_session_write_alias(self):
  r=fixture();res=r.session().write(instance_id=r.instance_id,state_id='power.enabled',value=False,request_id='off');self.assertEqual(res['status'],'succeeded');self.assertFalse(r.powered)
 def test_43_unsupported_all(self):
  r=fixture()
  for k in UNSUPPORTED:self.reject(r,k,'UNSUPPORTED')
 def test_44_strict_collision_no_false_blank(self):
  r=fixture();m=copy.deepcopy(r.method);m['mode']='ms2';r.invoke('method.configure',**m);r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);self.reject(r,'scan.start','REFERENCE_CONDITION_REQUIRED')
 def test_45_ms2_ack_actual_profile(self):
  r=fixture();m=copy.deepcopy(r.method);m.update(mode='ms2',reference_condition_policy='acknowledged_fixed_pattern');r.invoke('method.configure',**m);res=run(r);self.assertGreater(len(res['scans'][0]['centroids']),3);self.assertIn('SOURCE_INSTRUMENT_RESOLUTION_AND_COLLISION_CONFLICT_TRANSFER',res['quality'])
 def test_46_nce_no_eV_mapping(self):
  r=fixture();m=copy.deepcopy(r.method);m.update(mode='ms2',reference_condition_policy='acknowledged_fixed_pattern',collision_type='absolute',collision_potential_v=30);r.invoke('method.configure',**m);res=run(r);self.assertIn('no conversion',json.loads(res['provenance_json'])['collision_energy'].lower())
 def test_47_wrong_polarity_not_truth_rejection(self):
  r=fixture();m=copy.deepcopy(r.method);m.update(polarity='negative',spray_voltage_v=-3500);r.invoke('method.configure',**m);res=run(r);self.assertFalse(res['scans'][0]['centroids']);self.assertIn('UNKNOWN_ADDUCTS_AND_UNLISTED_COMPOUNDS_UNMODELED',res['quality'])
 def test_48_isotope_only_window(self):
  r=fixture();m=copy.deepcopy(r.method);m.update(mass_min=123.,mass_max=124.);r.invoke('method.configure',**m);res=run(r);self.assertGreater(len(res['scans'][0]['centroids']),0);self.assertTrue(all(123<=p['mass_to_charge_relative']<=124.01 for p in res['scans'][0]['centroids']))
 def test_49_grid_independent_hidden_sample(self):
  r=fixture();a=r._grid(r.method).copy();r.invoke('sample.load',**sample('sm800003'));b=r._grid(r.method);self.assertTrue(np.array_equal(a,b));r.invoke('sample.load',**sample('sm814103'));self.assertTrue(np.array_equal(a,r._grid(r.method)))
 def test_50_si_axis(self):
  r=fixture();res=run(r);s=res['scans'][0];np.testing.assert_allclose(np.asarray(s['profile_mass_to_charge_relative'])*U_KG/E_C,s['profile_mass_to_charge_kg_c'],rtol=1e-15)
 def test_51_centroid_is_observed_drift(self):
  r=fixture();res=run(r);p=max(res['scans'][0]['centroids'],key=lambda x:x['intensity_relative']);self.assertGreater(p['raw_mass_to_charge_relative']-LIB['compounds']['sm808001']['derived_precursor_mz'],.001)
 def test_52_resolution_actual_peak_width(self):
  widths=[]
  for resolution in [15000,120000]:
   r=fixture();m=copy.deepcopy(r.method);m.update(resolution=resolution,mass_min=120.,mass_max=125.);r.invoke('method.configure',**m);p=max(run(r)['scans'][0]['centroids'],key=lambda x:x['intensity_relative']);widths.append(p['fwhm_relative'])
  self.assertGreater(widths[0]/widths[1],6);self.assertLess(widths[0]/widths[1],10)
 def test_53_rate_ceilings(self):
  r=fixture()
  for resolution,rate in RATE_CEILINGS.items():
   m=copy.deepcopy(r.method);m['resolution']=resolution;self.assertGreaterEqual(r._transient(m),1/rate)
 def test_54_step_partition_consistency(self):
  a=fixture();b=fixture()
  for r in [a,b]:r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);r.invoke('scan.start')
  a.step(.2)
  for _ in range(20):b.step(.01)
  np.testing.assert_allclose(a.scans[0]['profile_intensity_relative'],b.scans[0]['profile_intensity_relative'],atol=1e-12,rtol=1e-10);self.assertAlmostEqual(a._ledger()['waste_mol'],b._ledger()['waste_mol'],places=20)
 def test_55_complete_ledger(self):
  r=fixture();run(r);l=r._ledger()
  for key in ['mass_residual_kg','residual_mol','volume_residual_m3','source_nitrogen_residual_mol','collision_nitrogen_residual_mol','charge_residual_c','energy_residual_j']:self.assertAlmostEqual(l[key],0,places=12,msg=key)
 def test_56_cancel_filling(self):
  r=fixture();r.invoke('sample.load',**sample(amount=1e-12));r.invoke('source.set',enabled=True);r.invoke('scan.start');r.step(.005);before=r.volume;r.invoke('measurement.stop');self.assertLess(before,1e-7);self.assertEqual(r.volume,before);self.assertFalse(r.scans);self.assertEqual(r._charge(),0);self.assertGreater(r.charge_neutral,0)
 def test_57_cancel_transient_preserves_window(self):
  r=fixture();r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);r.invoke('scan.start');r.step(.03);r.invoke('measurement.stop');res=r.invoke('result.read');self.assertFalse(res['complete']);self.assertFalse(res['scans'][0]['complete']);self.assertAlmostEqual(res['scans'][0]['end_s'],.03)
 def test_58_observation_timestamp_not_query_clock(self):
  r=fixture();run(r);old=r.scans[-1]['end_s'];r.step(.1);obs=r.session().read(instance_id=r.instance_id,state_ids=['detector.tic_relative'])['observations']['detector.tic_relative'];self.assertEqual(obs['sample_time_s'],old);self.assertLess(old,r.time_s)
 def test_59_mass_calibration_actual_observation(self):
  r=calibrated();c=r._curves['axis'];self.assertGreater(abs(c['slope']-1),1e-6);self.assertLess(c['rms_fraction'],1e-6);result=run(r);p=max(result['scans'][0]['centroids'],key=lambda x:x['intensity_relative']);self.assertLess(abs(p['mass_to_charge_relative']/LIB['compounds']['sm808001']['derived_precursor_mz']-1),1e-6)
 def test_60_no_blank_calibration(self):
  r=fixture();ids=[run(r,i,'standard')['record_id'] for i in ['sm800003','sm808001','sm814103']];self.reject(r,'calibration.fit','BLANK_REQUIRED',curve_id='x',record_ids=ids)
 def test_61_fake_calibration_records(self):
  r=fixture();self.reject(r,'calibration.fit','ACTUAL_STANDARD_RECORD_REQUIRED',curve_id='x',record_ids=['a','b','c'])
 def test_62_duplicate_calibration_records(self):
  r=fixture();self.reject(r,'calibration.fit','DUPLICATE_RECORD',curve_id='x',record_ids=['a','a','b'])
 def test_63_calibration_polarity_invalidates(self):
  r=calibrated();m=copy.deepcopy(r.method);m.update(polarity='negative',spray_voltage_v=-3500);r.invoke('method.configure',**m);self.assertFalse(r.read()['calibration.ready']);self.reject(r,'calibration.select','CALIBRATION_SIGNATURE',curve_id='axis')
 def test_64_calibration_expiry(self):
  r=calibrated();r.invoke('source.set',enabled=False);r.step(86401);self.assertFalse(r.read()['calibration.ready'])
 def test_65_formula_from_observation_only(self):
  r=calibrated();res=run(r);p=res['scans'][0]['centroids'];index=max(range(len(p)),key=lambda i:p[i]['intensity_relative']);r.reservoir={'sm800003':1e-8};out=r.invoke('formula.search',record_id=res['record_id'],scan_index=0,peak_index=index,tolerance_fraction=5e-6);self.assertIn('C7H7NO',[x['formula'] for x in json.loads(out['candidates_json'])]);self.assertTrue(all(not x['identity_confirmed'] for x in json.loads(out['candidates_json'])))
 def test_66_formula_uncalibrated_reject(self):
  r=fixture();res=run(r);self.reject(r,'formula.search','CALIBRATED_OBSERVATION_REQUIRED',record_id=res['record_id'],scan_index=0,peak_index=0,tolerance_fraction=5e-6)
 def test_67_mass_curve_range_can_change(self):
  r=calibrated();m=copy.deepcopy(r.method);m.update(mass_min=100.,mass_max=200.);r.invoke('method.configure',**m);self.assertTrue(r.read()['calibration.ready']);self.assertFalse(r.read()['blank.valid'])
 def test_68_maintenance_double_latch(self):
  r=fixture();r.invoke('source.fluid',connected=False);r.invoke('source.latch',side='left',unlocked=True);r.step(.4);self.reject(r,'source.withdraw','BOTH_LATCHES_REQUIRED',withdrawn=True)
 def test_69_maintenance_fluid(self):
  r=fixture();self.reject(r,'source.latch','FLUID_DISCONNECT_REQUIRED',side='left',unlocked=True)
 def test_70_hot_source_maintenance(self):
  r=fixture();r.temperature=400.;r.invoke('source.fluid',connected=False);self.reject(r,'source.latch','COLD_SOURCE_REQUIRED',side='left',unlocked=True)
 def test_71_finite_source_motion(self):
  r=fixture();r.invoke('source.fluid',connected=False)
  for side in ['left','right']:r.invoke('source.latch',side=side,unlocked=True)
  r.step(.4);r.invoke('source.withdraw',withdrawn=True);self.assertEqual(r.q['source.withdrawal_m'],0);r.step(.1);self.assertGreater(r.q['source.withdrawal_m'],0);self.assertLess(r.q['source.withdrawal_m'],.18);r.step(1);self.assertEqual(r.q['source.withdrawal_m'],.18)
 def test_72_accessory_lid_distinct_from_source(self):
  r=fixture();r.invoke('accessory_lid.set',opened=True);r.step(.1);self.assertGreater(r.q['accessory_lid.angle_rad'],0);self.assertEqual(r.q['source.withdrawal_m'],0);r.invoke('source.set',enabled=True)
 def test_73_cleaning_cancel_volume_and_residue(self):
  r=fixture();run(r);r.invoke('source.set',enabled=False);r.invoke('power.set',enabled=False);r.invoke('source.fluid',connected=False)
  for side in ['left','right']:r.invoke('source.latch',side=side,unlocked=True)
  r.step(.4);r.invoke('source.withdraw',withdrawn=True);r.step(.7);before=sum(r.residue.values());r.invoke('maintenance.clean',solvent_volume_m3=1e-6);r.step(.1);r.invoke('measurement.stop');self.assertLess(sum(r.residue.values()),before);self.assertGreater(r._clean_remaining,0);self.assertAlmostEqual(r._ledger()['volume_residual_m3'],0,places=20)
 def test_74_gas_exhaustion_stops(self):
  r=fixture(source_nitrogen_mol=1e-5);r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);r.invoke('scan.start');r.step(.1);self.assertEqual(r.phase,'failed');self.assertFalse(r.enabled);self.assertEqual(r.warning,'GAS_EXHAUSTED')
 def test_75_led_ms1_ms2_and_off(self):
  r=fixture();r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);r.invoke('scan.start');self.assertEqual(r.read()['led.scan_rgb'],[0.,.2,1.]);r.invoke('measurement.stop');m=copy.deepcopy(r.method);m.update(mode='ms2',reference_condition_policy='acknowledged_fixed_pattern');r.invoke('method.configure',**m);r.invoke('scan.start');self.assertEqual(r.read()['led.scan_rgb'],[.6,0.,1.]);r.invoke('power.set',enabled=False);self.assertEqual(r.read()['led.scan_rgb'],[0.,0.,0.])
 def test_76_history_return_isolation(self):
  r=fixture();a=run(r);rid=a['record_id'];a['scans'][0]['centroids'].clear();self.assertTrue(r.invoke('records.read',record_id=rid)['scans'][0]['centroids']);run(r,'sm800003');self.assertEqual(r.invoke('records.read',record_id=rid)['method_json'],a['method_json'])
 def test_77_csv_numerical_roundtrip(self):
  r=fixture();res=run(r);out=r.invoke('result.export',format='csv');rows=list(csv.DictReader(io.StringIO(out['content'])));self.assertEqual(len(rows),len(res['scans'][0]['profile_mass_to_charge_relative']));self.assertEqual(float(rows[100]['intensity_relative']),res['scans'][0]['profile_intensity_relative'][100])
 def test_78_cache_capacity_reject(self):
  r=fixture();r._records={str(i):{} for i in range(MAX_RECORDS)};r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);self.reject(r,'scan.start','RESULT_CAPACITY')
 def test_79_dual_instances_independent(self):
  a=fixture();b=fixture();run(a);self.assertEqual(b.time_s,0);self.assertEqual(b.volume,0);self.assertFalse(b._records);self.assertEqual(b.read()['detector.tic_relative'],None)
 def test_80_busy_method_atomic(self):
  r=fixture();r.invoke('sample.load',**sample());r.invoke('source.set',enabled=True);r.invoke('scan.start');self.reject(r,'method.configure','BUSY',**r.method)
 def test_81_default_room_temperature_maintenance(self):
  r=fixture();m=copy.deepcopy(r.method);m['transfer_temperature_k']=323.15;r.invoke('method.configure',**m);r.invoke('source.set',enabled=True);r.step(2);r.invoke('source.set',enabled=False);r.invoke('source.fluid',connected=False);self.reject(r,'source.latch','COLD_SOURCE_REQUIRED',side='left',unlocked=True);r.step(1.6);self.assertLess(r.temperature,r.plant['maintenance_temperature_k']);r.invoke('source.latch',side='left',unlocked=True)
if __name__=='__main__':unittest.main()
