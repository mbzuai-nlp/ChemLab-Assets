"""CHEM089 source-domain, chemistry, observed calibration and session regression."""
import copy,csv,io,json,math,re,sys,unittest,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.leco_onh836 import *
LIB=json.loads((ROOT/TEMPLATE_REL).read_text())
def fixture(carrier='He',**kw):
 r=LecoOnh836Runtime(library=LIB);p=copy.deepcopy(PLANT_EXAMPLE);p.update(noise_density_sqrt_s=zeros());p.update(kw);r.invoke('plant.configure',**p);m=copy.deepcopy(METHOD_EXAMPLE);m['carrier']=carrier;r.invoke('method.configure',**m);r.invoke('power.set',enabled=True);r.invoke('gas.set',enabled=True);return r
def sample(kind='sample',mass=.00013,i='leco_503558_reference'):
 a=copy.deepcopy(BLANK_EXAMPLE if kind=='blank' else SAMPLE_EXAMPLE);a['kind']=kind
 if kind!='blank':a.update(mass_kg=mass,template_id=i,declared_elements=[e for e in ELEMENTS if LIB['compounds'][i]['element_mass_fractions'].get(e) is not None])
 return a
def prepare(r,a=None):
 if not r._cold():r.step(2.)
 r.invoke('furnace.open',opened=True);r.step(.14)
 if r.crucible:r.invoke('crucible.remove',recipient_id='waste')
 r.invoke('crucible.install',**CRUCIBLE_EXAMPLE);r.invoke('furnace.open',opened=False);r.step(.14);r.invoke('loading.set',loading=True);r.step(.06);r.invoke('sample.load',**(a or sample()));r.invoke('loading.set',loading=False);r.step(.06)
def run(r,a=None,dt=.02):
 prepare(r,a);r.invoke('measurement.start')
 for _ in range(math.ceil(2.1/dt)):r.step(dt)
 return r.invoke('result.read')
def calibrate(r):
 run(r,sample('blank'));ids=[run(r,sample('standard',m))['record_id'] for m in [.0001,.00012,.00015]];c=r.invoke('calibration.fit',record_ids=ids,elements=ELEMENTS);r.invoke('calibration.select',curve_id=c['curve_id']);return c
class RuntimeTests(unittest.TestCase):
 def reject(self,r,cap,code,**a):
  snap=r.debug_snapshot()
  with self.assertRaises(ActionRejected) as ex:r.invoke(cap,**a)
  self.assertEqual(str(ex.exception),code);self.assertEqual(r.debug_snapshot(),snap)
 def ledger(self,r):
  l=r.invoke('product.read')
  for v in json.loads(l['elements_json']).values():self.assertLess(abs(v['residual_kg']),1e-15)
  for k in ['carbon_residual_kg','unknown_matrix_residual_kg']:self.assertLess(abs(l[k]),1e-15)
  for k,v in json.loads(l['species_json']).items():
   if k.endswith('residual_mol'):self.assertLess(abs(v),1e-13)
  for v in json.loads(l['gas_inventory_json']).values():self.assertLess(abs(v['residual_mol']),1e-10)
  self.assertLess(abs(l['cuo_oxygen_residual_mol']),1e-10);self.assertLess(abs(l['energy_residual_j']),1e-8)
 def test_01_source_units(self):self.assertEqual(LIMITS['He']['O'],[5e-11,5e-5]);self.assertEqual(LIMITS['Ar']['N'][0],2e-10);self.assertEqual(LIMITS['He']['H'][1],2.5e-6)
 def test_02_nominal_not_hardware_max(self):self.assertIn('not a claimed maximumhardwarecapacity',LIMITATIONS[0])
 def test_03_reject_unknown_argument(self):self.reject(fixture(),'gas.set','INVALID_ARGUMENT',enabled=True,extra=1)
 def test_04_reject_bool_number(self):r=fixture();a=copy.deepcopy(r.method);a['fusion_power_w']=True;self.reject(r,'method.configure','INVALID_ARGUMENT',**a)
 def test_05_reject_nan(self):r=fixture();a=copy.deepcopy(r.method);a['fusion_s']=float('nan');self.reject(r,'method.configure','INVALID_ARGUMENT',**a)
 def test_06_initialization_only(self):r=fixture();self.reject(r,'plant.configure','INITIALIZATION_ONLY',**PLANT_EXAMPLE)
 def test_07_unique_host_clock(self):r=fixture();r.step(.1);self.assertAlmostEqual(r.time_s,.1)
 def test_08_invalid_step(self):
  r=fixture()
  for d in [-1,True,float('inf')]:
   before=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.step(d)
   self.assertEqual(before,r.debug_snapshot())
 def test_09_zero_step_pure(self):r=fixture();s=r.debug_snapshot();r.step(0);self.assertEqual(r.debug_snapshot(),s)
 def test_10_read_pure(self):r=fixture();s=r.debug_snapshot();r.read();r.read('evaluator');r.describe();self.assertEqual(s,r.debug_snapshot())
 def test_11_no_self_promotion(self):
  r=fixture()
  with self.assertRaises(ActionRejected):r.register_principal('__public_agent__','evaluator')
 def test_12_truth_filter(self):r=fixture();self.assertNotIn('truth.ledger',r.read());self.assertIn('truth.ledger',r.read('evaluator'))
 def test_13_capability_acl(self):r=fixture();x=r.session().invoke(instance_id=r.instance_id,capability_id='sample.load',arguments=sample(),request_id='x');self.assertEqual(x['error']['code'],'ACCESS_DENIED')
 def test_14_describe_acl(self):r=fixture();d=r.describe();self.assertNotIn('sample.load',d['capabilities']);self.assertNotIn('truth.ledger',d['state_slots'])
 def test_15_cross_instance(self):r=fixture();x=r.session().invoke(instance_id='other',capability_id='power.set',arguments={'enabled':True},request_id='x');self.assertEqual(x['error']['code'],'UNKNOWN_INSTANCE')
 def test_16_dedup_after_time(self):r=fixture();s=r.session();a=dict(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='one');x=s.invoke(**a);r.step(.1);self.assertEqual(s.invoke(**a),x)
 def test_17_request_conflict(self):r=fixture();s=r.session();a=dict(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='one');s.invoke(**a);a['arguments']['enabled']=False;self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT');self.assertTrue(r.powered)
 def test_18_requested_truth_read_denied(self):
  r=fixture()
  with self.assertRaises(ActionRejected):r.session().read(instance_id=r.instance_id,state_ids=['truth.ledger'])
 def test_19_independent_instances(self):r=fixture();q=fixture();run(r);self.assertEqual(q.time_s,0);self.assertFalse(q.crucible);self.assertEqual(q._records,{})
 def test_20_finite_lower_electrode(self):r=fixture();r.invoke('furnace.open',opened=True);self.assertEqual(r.q['furnace.opening_m'],0);r.step(.04);self.assertGreater(r.q['furnace.opening_m'],0);self.assertLess(r.q['furnace.opening_m'],.1)
 def test_21_finite_loading(self):r=fixture();r.invoke('loading.set',loading=True);r.step(.02);self.assertLess(r.q['loading.position_m'],.045);self.assertGreater(r.q['loading.position_m'],0)
 def test_22_maintenance_cover_power_guard(self):r=fixture();self.reject(r,'cover.set','POWER_OFF_REQUIRED',opened=True);r.invoke('power.set',enabled=False);r.invoke('cover.set',opened=True);r.step(.10);self.assertGreater(r.q['cover.lift_m'],0);self.assertLess(r.q['cover.lift_m'],.160);r.step(.12);self.assertEqual(r.q['cover.lift_m'],.160)
 def test_23_install_requires_open(self):self.reject(fixture(),'crucible.install','FURNACE_OPEN_COLD_REQUIRED',**CRUCIBLE_EXAMPLE)
 def test_24_duplicate_crucible(self):r=fixture();r.invoke('furnace.open',opened=True);r.step(.14);r.invoke('crucible.install',**CRUCIBLE_EXAMPLE);self.reject(r,'crucible.install','CRUCIBLE_PRESENT',**CRUCIBLE_EXAMPLE)
 def test_25_loading_requires_funnel(self):r=fixture();self.reject(r,'sample.load','LOAD_POSITION_REQUIRED',**sample())
 def test_26_load_held_no_release(self):r=fixture();prepare(r);self.assertFalse(r.dropped);self.assertGreater(r.held['O'],0);r.step(.2);self.assertEqual(r.detected['O'],0)
 def test_27_return_before_drop(self):r=fixture();prepare(r);r.invoke('loading.set',loading=True);r.step(.06);x=r.invoke('sample.unload',recipient_id='returned');self.assertEqual(x['returned_mass_kg'],.00013);self.ledger(r)
 def test_28_no_return_after_fusion(self):r=fixture();run(r);self.reject(r,'sample.unload','SAMPLE_ALREADY_FUSED',recipient_id='returned')
 def test_29_drop_gate_held_interlock(self):r=fixture();prepare(r);self.reject(r,'sample_gate.set','SAMPLE_RETAINED',opened=True)
 def test_30_cold_lower_interlock(self):r=fixture();prepare(r);r.invoke('measurement.start');r.step(.1);r.invoke('measurement.stop');self.reject(r,'furnace.open','HOT_MAINTENANCE',opened=True)
 def test_31_carrier_switch_requires_off(self):r=fixture();m=copy.deepcopy(r.method);m['carrier']='Ar';self.reject(r,'method.configure','CARRIER_CHANGE_REQUIRES_OFF',**m)
 def test_32_duplicate_selected_element(self):r=fixture();m=copy.deepcopy(r.method);m['selected_elements']=['O','O'];self.reject(r,'method.configure','DUPLICATE_ELEMENT',**m)
 def test_33_sampling_window_guard(self):r=fixture();m=copy.deepcopy(r.method);m['window_s']=1;self.reject(r,'method.configure','WINDOW_TOO_LONG',**m)
 def test_34_power_7500_bound(self):r=fixture();m=copy.deepcopy(r.method);m['fusion_power_w']=7501;self.reject(r,'method.configure','INVALID_ARGUMENT',**m)
 def test_35_blank_nonzero_rejected(self):
  r=fixture();r.invoke('furnace.open',opened=True);r.step(.14);r.invoke('crucible.install',**CRUCIBLE_EXAMPLE);r.invoke('furnace.open',opened=False);r.step(.14);r.invoke('loading.set',loading=True);r.step(.06);a=sample('blank');a['mass_kg']=.0001;self.reject(r,'sample.load','INVALID_BLANK',**a)
 def test_36_source_ack_required(self):
  r=fixture();prepare(r);r.invoke('loading.set',loading=True);r.step(.06);r.invoke('sample.unload',recipient_id='r');a=sample();a['accept_source_condition_mismatch']=False;self.reject(r,'sample.load','SOURCE_CONDITIONS_REQUIRED',**a)
 def test_37_no_truth_in_sample_result(self):r=fixture();x=run(r);self.assertIsNone(x['standard_reference']);self.assertNotIn('leco_503558_reference',json.dumps(x));self.assertEqual(x['element_mass_kg'],nulls())
 def test_38_atomic_absent_result(self):self.reject(fixture(),'result.read','NO_RESULT')
 def test_39_all_elements_same_run(self):r=fixture();x=run(r);self.assertEqual(x['carrier'],'He');self.assertTrue(all(x['integrals_s'][e]>0 for e in ELEMENTS));self.assertTrue(r.dropped)
 def test_40_measured_baseline_not_constant(self):r=fixture();x=run(r);self.assertNotEqual(x['baseline_relative'],r.plant['baseline_relative']);self.assertTrue(any(w['stage']=='baseline' for w in x['windows']))
 def test_41_integral_independent(self):r=fixture();x=run(r);expected={e:sum((w['end_s']-w['start_s'])*w['corrected_relative'][e] for w in x['windows'] if w['stage'] in ['fusion','integration']) for e in ELEMENTS};np.testing.assert_allclose(list(expected.values()),list(x['integrals_s'].values()),rtol=1e-12,atol=1e-13)
 def test_42_blank_actual_record(self):r=fixture();x=run(r,sample('blank'));b=r.invoke('blank.read');self.assertEqual(b['record_id'],x['record_id']);self.assertEqual(b['integrals_s'],x['integrals_s'])
 def test_43_fitted_standard_observations(self):r=fixture();c=calibrate(r);self.assertEqual(len(c['record_ids']),3);self.assertTrue(all(c['parameters'][e]['r_squared']>.999999 for e in ELEMENTS))
 def test_44_unknown_inferred_from_observations(self):r=fixture();c=calibrate(r);x=run(r);expected=LIB['compounds']['leco_503558_reference']['element_mass_fractions'];np.testing.assert_allclose([x['element_mass_fractions'][e] for e in ELEMENTS],[expected[e] for e in ELEMENTS],rtol=1e-6)
 def test_45_NO_direct_truth_return(self):r=fixture();c=calibrate(r);x=run(r);e='O';self.assertAlmostEqual(x['element_mass_kg'][e],(x['net_integrals_s'][e]-c['parameters'][e]['intercept_s'])/c['parameters'][e]['slope_s_kg'],places=16)
 def test_46_calibration_wrong_kind(self):r=fixture();ids=[run(r)['record_id'] for _ in range(3)];self.reject(r,'calibration.fit','INVALID_STANDARD_OBSERVATION',record_ids=ids,elements=ELEMENTS)
 def test_47_duplicate_standard_records(self):r=fixture();x=run(r);self.reject(r,'calibration.fit','DUPLICATE_OBSERVATION',record_ids=[x['record_id']]*3,elements=ELEMENTS)
 def test_48_calibration_carrier_signature(self):r=fixture();c=calibrate(r);r.invoke('gas.set',enabled=False);m=copy.deepcopy(r.method);m['carrier']='Ar';r.invoke('method.configure',**m);self.reject(r,'calibration.select','CALIBRATION_MISMATCH',curve_id=c['curve_id']);self.assertFalse(r.read()['blank.valid'])
 def test_49_calibration_power_signature(self):r=fixture();c=calibrate(r);m=copy.deepcopy(r.method);m['fusion_power_w']=4500;r.invoke('method.configure',**m);self.assertIsNone(r._curve());self.assertIsNone(r._blank())
 def test_50_argon_independent_calibration(self):r=fixture('Ar');c=calibrate(r);x=run(r);self.assertTrue(all(v is not None for v in x['element_mass_kg'].values()));self.assertLess(c['parameters']['N']['slope_s_kg'],PLANT_EXAMPLE['response_gain_s_kg']['N'])
 def test_51_mass_balance_complete(self):r=fixture();run(r);self.ledger(r)
 def test_52_reagent_oxygen_separate(self):r=fixture();run(r);s=json.loads(r._ledger()['species_json']);self.assertGreater(s['reagent_oxygen_atoms_mol'],0);self.assertGreater(2*s['cumulative_outlet_mol']['CO2_post'],s['sample_oxygen_atoms_mol']);self.ledger(r)
 def test_53_DFC_same_carrier(self):r=fixture();run(r);l=r._ledger();self.assertAlmostEqual(l['dfc_supplied_mol'],l['co2_captured_mol']+l['water_captured_mol'],places=13);self.assertEqual(r.gas_used['Ar'],0)
 def test_54_carbon_finite_consumption(self):r=fixture();run(r);self.assertLess(r.carbon,CRUCIBLE_EXAMPLE['graphite_mass_kg']);self.assertGreater(r.carbon_gas,0);self.ledger(r)
 def test_55_sample_unknown_matrix_no_O_inference(self):r=fixture();run(r);self.assertGreater(r.matrix_remaining,.00012);self.ledger(r)
 def test_56_dispose_not_restore(self):r=fixture();run(r);r.invoke('furnace.open',opened=True);r.step(.14);r.invoke('crucible.remove',recipient_id='waste');self.assertFalse(r.crucible);self.assertGreater(r.disposed['O'],0);self.assertGreater(r.matrix_disposed,0);self.ledger(r)
 def test_57_cancel_partial_window(self):r=fixture();prepare(r);r.invoke('measurement.start');r.step(.633);r.invoke('measurement.stop');x=r.invoke('result.read');self.assertFalse(x['complete']);self.assertFalse(x['windows'][-1]['complete']);self.assertTrue(r.dropped);self.assertTrue(all(v is None for v in x['element_mass_kg'].values()));self.ledger(r)
 def test_58_cancel_preserves_inventory(self):r=fixture();prepare(r);before=r.held['O'];r.invoke('measurement.start');r.step(.7);r.invoke('measurement.stop');self.assertLess(r.remaining['O'],before);self.assertGreater(r.detected['O'],0);self.assertGreater(r.gas_used['He'],0);self.ledger(r)
 def test_59_coolant_loss(self):r=fixture();prepare(r);r.invoke('measurement.start');r.step(.65);r.invoke('coolant.set',ready=False);self.assertEqual(r.phase,'failed');self.assertEqual(r.actual_power,0);self.ledger(r)
 def test_60_power_loss(self):r=fixture();prepare(r);r.invoke('measurement.start');r.step(.65);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.assertEqual(r.actual_power,0);self.ledger(r)
 def test_61_gas_loss(self):r=fixture();prepare(r);r.invoke('measurement.start');r.step(.65);r.invoke('gas.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.ledger(r)
 def test_62_CuO_exhaustion(self):r=fixture(cuo_oxygen_inventory_mol=1e-8);x=run(r);self.assertEqual(x['status'],'failed');self.assertIn('CUO_EXHAUSTED',r.warning);self.ledger(r)
 def test_63_scrubber_exhaustion(self):r=fixture(co2_scrubber_capacity_mol=1e-8);x=run(r);self.assertEqual(x['status'],'failed');self.assertEqual(r.warning,'SCRUBBER_BREAKTHROUGH');self.ledger(r)
 def test_64_DFC_deficit(self):r=fixture(dfc_maximum_mol_s=0);x=run(r);self.assertEqual(x['status'],'failed');self.assertEqual(r.warning,'DFC_FLOW_DEFICIT');self.ledger(r)
 def test_65_noise_is_host_not_spec(self):r=fixture(noise_density_sqrt_s={'O':1e-5,'N':1e-5,'H':1e-5});x=run(r);self.assertTrue(all(math.isfinite(v) for w in x['windows'] for v in w['raw_relative'].values()));self.ledger(r)
 def test_66_time_partition_consistency(self):r=fixture();q=fixture();a=run(r,dt=.02);b=run(q,dt=.01);np.testing.assert_allclose(list(a['integrals_s'].values()),list(b['integrals_s'].values()),rtol=1e-10,atol=1e-12);self.ledger(r);self.ledger(q)
 def test_67_window_times_are_observation_times(self):r=fixture();run(r);o=r.session().read(instance_id=r.instance_id,state_ids=['detector.total_relative'])['observations']['detector.total_relative'];self.assertLess(o['sample_time_s'],r.time_s);self.assertEqual(o['sample_window_s'],[r._last_window['start_s'],r._last_window['end_s']])
 def test_68_csv_actual_array(self):r=fixture();x=run(r);rows=list(csv.DictReader(io.StringIO(r.invoke('result.export',format='csv')['data'])));self.assertEqual(len(rows),len(x['windows']));self.assertEqual(float(rows[0]['O_raw_relative']),x['windows'][0]['raw_relative']['O'])
 def test_69_record_returns_copy(self):r=fixture();x=run(r);y=r.invoke('records.read',record_id=x['record_id']);y['windows'].clear();self.assertTrue(r._records[x['record_id']]['windows'])
 def test_70_record_in_use(self):r=fixture();c=calibrate(r);self.reject(r,'records.remove','RECORD_IN_USE',record_id=c['record_ids'][0])
 def test_71_delete_unused_record(self):r=fixture();x=run(r);r.invoke('records.remove',record_id=x['record_id']);self.assertNotIn(x['record_id'],r._records)
 def test_72_async_owner_and_cancel(self):
  r=fixture();prepare(r);s=r.session();x=s.invoke(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='start');op=x['operation_id'];r.register_principal('other','agent')
  with self.assertRaises(ActionRejected):r.session('other').poll(op)
  r.step(.633);s.cancel(op,'cancel');self.assertEqual(s.poll(op)['status'],'cancelled');self.ledger(r)
 def test_73_metadata_errorcodes_legal(self):
  m=export_leco_onh836_contract_metadata()
  for c in m['capabilities'].values():
   for e in c['errors']:self.assertRegex(e['code'],r'^[A-Z][A-Z0-9_]*$')
 def test_74_metadata_schemas_examples(self):
  spec=importlib.util.spec_from_file_location('tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);m=export_leco_onh836_contract_metadata();errors=[]
  for k,c in m['capabilities'].items():tool.lint_schema(c['input_schema'],'input'+k,errors);tool.lint_schema(c['output_schema'],'output'+k,errors);tool.validate_value(c['example_arguments'],c['input_schema'],'example'+k,errors)
  self.assertEqual(errors,[])
 def test_75_runtime_output_schema(self):
  r=fixture();x=run(r);validate(RESULT,x);validate(LEDGER,r._ledger())
  for k,v in r.read('evaluator').items():validate(SLOTS[k][0],v)
 def test_76_unsupported_no_success(self):
  r=fixture()
  for k in UNSUPPORTED:self.reject(r,k,'UNSUPPORTED')
 def test_77_bound_capacity(self):
  r=fixture();prepare(r);r._records={str(i):{} for i in range(MAX_RECORDS)};self.reject(r,'measurement.start','RESULT_STORE_FULL')
 def test_78_no_hidden_positive_precondition(self):
  r=fixture();prepare(r);r.held=zeros();r.sample['amount']=zeros();r.invoke('measurement.start');r.step(1.3);self.assertEqual(r.phase,'succeeded');self.assertIsNone(r.invoke('result.read')['element_mass_kg']['O'])
 def test_79_missing_source_channel_public_null(self):
  lib=copy.deepcopy(LIB);ref=lib['compounds']['leco_503558_reference'];ref['element_mass_fractions']['H']=None;ref['calibration_eligible_elements']=['O','N'];r=LecoOnh836Runtime(library=lib);p=copy.deepcopy(PLANT_EXAMPLE);p['noise_density_sqrt_s']=zeros();r.invoke('plant.configure',**p);r.invoke('power.set',enabled=True);r.invoke('gas.set',enabled=True);a=sample();a['declared_elements']=['O','N'];x=run(r,a);self.assertIn('unreported_element_unknown_not_zero',x['element_quality']['H']);self.assertIsNone(x['element_mass_fractions']['H'])
 def test_80_finite_sample_gate(self):r=fixture();prepare(r);r.invoke('measurement.start');r.step(.38);self.assertEqual(r.stage,'gate_open');self.assertGreater(r.q['sample_gate.position_m'],0);self.assertLess(r.q['sample_gate.position_m'],.04)
 def test_81_no_repeat_used_crucible(self):r=fixture();run(r);self.reject(r,'measurement.start','INSTRUMENT_NOT_READY')
 def test_82_calibration_outside_mass_domain_null(self):r=fixture();calibrate(r);x=run(r,sample(mass=.00018));self.assertTrue(all(v is None for v in x['element_mass_kg'].values()));self.assertTrue(all('outside_calibration_domain' in x['element_quality'][e] for e in ELEMENTS))
 def test_83_zero_power_no_fabricated_outgas(self):
  r=fixture();m=copy.deepcopy(r.method);m['outgas_power_w']=0.;r.invoke('method.configure',**m);prepare(r);before=r.background.copy();r.invoke('measurement.start');r.step(.14);self.assertEqual(r.background,before);self.assertEqual(r.actual_power,0.);self.ledger(r)
 def test_84_graphite_exhaustion_conserves(self):
  lib=copy.deepcopy(LIB);lib['compounds']['leco_503558_reference']['element_mass_fractions']={'O':1.,'N':0.,'H':0.};r=LecoOnh836Runtime(library=lib);p=copy.deepcopy(PLANT_EXAMPLE);p['noise_density_sqrt_s']=zeros();p['response_gain_s_kg']={e:1. for e in ELEMENTS};p['dfc_maximum_mol_s']=.1;r.invoke('plant.configure',**p);r.invoke('power.set',enabled=True);r.invoke('gas.set',enabled=True);prepare(r);r.carbon_registered-=r.carbon-1e-5;r.carbon=1e-5;r.invoke('measurement.start');r.step(1.3);self.assertEqual(r.phase,'failed');self.assertEqual(r.warning,'GRAPHITE_EXHAUSTED');self.ledger(r)
 def test_85_carrier_empty_finite(self):
  r=fixture(helium_inventory_mol=.0004);x=run(r);self.assertEqual(x['status'],'failed');self.assertGreaterEqual(r.gas['He'],0);self.assertIn(r.warning,['UTILITY_LOSS','DFC_FLOW_DEFICIT']);self.ledger(r)
 def test_86_metadata_effects_all_states_defined(self):
  m=export_leco_onh836_contract_metadata()
  for v in m['capabilities'].values():
   self.assertTrue(all(s['state_id'] in m['state_slots'] for s in v['state_effects']))
 def test_87_accepted_ack_provenance(self):
  r=fixture();x=run(r);self.assertTrue(json.loads(x['provenance_json'])['source_condition_transfer_acknowledged'])
 def test_88_highCO2_before_CuO_lowCO2_after(self):
  r=fixture();x=run(r)
  for w in x['windows']:
   ch=w['gas_channels_relative'];self.assertAlmostEqual(ch['co2_high']+ch['co'],ch['co2_low']-ch['co'],places=12)
  ch=max(x['windows'],key=lambda w:w['gas_channels_relative']['co2_low'])['gas_channels_relative'];self.assertGreater(ch['co2_low'],ch['co2_high']);self.assertNotAlmostEqual(ch['co2_high'],.5*ch['co2_low'],places=8)
 def test_89_window_storage_bound(self):
  r=fixture();m=copy.deepcopy(r.method);m.update(outgas_s=600.,purge_s=600.,baseline_s=600.,fusion_s=600.,integration_s=600.,window_s=.005);self.reject(r,'method.configure','WINDOW_CAPACITY',**m)
 def test_90_whole_source_pin_not_arbitrary_fraction(self):
  r=fixture();prepare(r);r.invoke('loading.set',loading=True);r.step(.06);r.invoke('sample.unload',recipient_id='r');a=sample(i='bam_d267_reference',mass=.0005);self.reject(r,'sample.load','WHOLE_SOURCE_PIN_REQUIRED',**a)
 def test_91_idempotent_enable_preserves_active_power(self):
  r=fixture();prepare(r);r.invoke('measurement.start');r.step(.08);p=r.actual_power;r.invoke('power.set',enabled=True);self.assertEqual(r.actual_power,p);self.assertEqual(r.phase,'running')
 def test_92_cancel_gate_does_not_continue_command(self):
  r=fixture();prepare(r);r.invoke('measurement.start');r.step(.38);q=r.q['sample_gate.position_m'];r.invoke('measurement.stop');r.step(.1);self.assertEqual(r.q['sample_gate.position_m'],q);self.assertFalse(r.dropped);self.ledger(r)
if __name__=='__main__':unittest.main()
