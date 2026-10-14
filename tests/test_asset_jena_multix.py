"""Finite combustion and observed calibration regressions; fixtures are expressly synthetic."""
import copy,hashlib,importlib.util,json,math,re,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.jena_multix import *
LIB={'compounds':{'standard':{'chlorine_equivalent_mass_kg':9.63e-9,'calibration_eligible':True,'evidence':'synthetic_test_fixture'},'unknown':{'chlorine_equivalent_mass_kg':9.05e-9,'calibration_eligible':False,'evidence':'synthetic_test_fixture'},'reference_only':{'reported_recovery_fraction':.95,'evidence':'synthetic_test_fixture'}}}
def ready(horizontal=False,mode=None,**plant):
 r=JenaMultixRuntime('unit',LIB);r.invoke('plant.configure',**(PLANT_EXAMPLE|dict(optional_boat_drive_installed=horizontal,optional_pox_installed=True)|plant))
 if horizontal:
  for d in ['main_left','main_right']:r.invoke('cover.set',door=d,opened=True)
  r.invoke('direction.set',direction='horizontal');r.invoke('tube.configure',tube_type='multi_purpose');r.step(.6)
  for d in ['main_left','main_right']:r.invoke('cover.set',door=d,opened=False)
  r.invoke('abd.power',enabled=True)
 r.invoke('method.configure',**(METHOD_EXAMPLE|dict(mode=mode or ('TX' if horizontal else 'AOX'))))
 if mode=='EOX' and not horizontal:
  for d in ['main_left','main_right']:r.invoke('cover.set',door=d,opened=True)
  r.invoke('tube.configure',tube_type='multi_purpose')
  for d in ['main_left','main_right']:r.invoke('cover.set',door=d,opened=False)
 r.invoke('cover.set',door='chlorine',opened=True);r.invoke('cell.prepare',cell_type='sensitive');r.invoke('cover.set',door='chlorine',opened=False);r.invoke('power.set',enabled=True);r.invoke('gas.set',enabled=True);r.step(.6);return r
def load(r,kind='sample',fraction=.2,**extra):
 form={'AOX':'loaded_carbon','EOX':'extract','POX':'purged_gas','TX':'solid'}[r.method['mode']]
 if r.direction=='horizontal':
  r.step(1.)
  if not r.port_open:r.invoke('sample_port.toggle');r.step(.5)
 if r.sample:r.invoke('sample.unload',recipient_id='returned')
 r.invoke('product.drain',recipient_id='waste')
 a=SAMPLE_EXAMPLE|dict(template_id='' if kind=='blank' else 'standard' if kind=='standard' else 'unknown',kind=kind,reference_fraction=fraction,input_form=form,volume_m3=1e-8 if form=='extract' else 0.)|extra
 r.invoke('sample.load',**a)
 if r.direction=='horizontal':r.invoke('sample_port.toggle');r.step(.5)
 return a
def measure(r,kind='sample',fraction=.2,**extra):
 load(r,kind,fraction,**extra);r.invoke('measurement.start');r.step(r.method['integration_s']);return r.invoke('result.read')
def calibrated(**kwargs):
 r=ready(**kwargs);measure(r,'blank');ids=[measure(r,'standard',f)['record_id'] for f in [.12,.2,.3]];r.invoke('calibration.fit',curve_id='observed',record_ids=ids);r.invoke('calibration.select',curve_id='observed');return r
def state(r):return json.dumps(r.read('evaluator'),sort_keys=True),r.time_s,r.revision,json.dumps(r._rng.bit_generator.state,sort_keys=True)
class MultiX(unittest.TestCase):
 def rejected(self,r,cap,code=None,**args):
  before=state(r)
  with self.assertRaises(ActionRejected) as ex:r.invoke(cap,**args)
  if code:self.assertEqual(str(ex.exception),code)
  self.assertEqual(before,state(r))
 def test_01_actual_blank_three_standard_inference(self):
  r=calibrated();q=measure(r);self.assertAlmostEqual(q['chlorine_equivalent_mass_kg'],9.05e-9*.2,places=19);self.assertGreater(q['observed_charge_c'],0);self.assertNotEqual(q['faraday_equivalent_mass_kg'],q['chlorine_equivalent_mass_kg'])
 def test_02_faraday_silver_charge(self):
  r=ready();measure(r,'blank');measure(r);L=r._ledger();self.assertAlmostEqual(L['charge_generated_c'],(L['silver_precipitate_mol']+L['silver_removed_precipitate_mol'])*FARADAY,places=13)
 def test_03_complete_window_integrals(self):
  r=ready();q=measure(r,'blank');self.assertAlmostEqual(sum((w['end_s']-w['start_s'])*w['current_a'] for w in q['windows']),q['observed_charge_c'],places=15);self.assertEqual(len(q['windows']),60)
 def test_04_cancel_partial_window_preserved(self):
  r=ready();measure(r,'blank');load(r);r.invoke('measurement.start');r.step(.735);r.invoke('measurement.stop');q=r._result();self.assertEqual(q['phase'],'cancelled');self.assertAlmostEqual(q['windows'][-1]['end_s']-q['windows'][-1]['start_s'],.135,places=10);self.assertGreater(r.sample['burned'],0);self.assertLess(r.sample['burned'],1)
 def test_05_cancel_not_fake_return(self):
  r=ready();measure(r,'blank');load(r);r.invoke('measurement.start');r.step(.7);r.invoke('measurement.stop');ret=r.invoke('sample.unload',recipient_id='host');self.assertAlmostEqual(ret['unburned_mass_kg'],2.5e-5,places=13);self.assertLess(ret['unburned_mass_kg']+ret['residue_mass_kg'],5e-5)
 def test_06_mass_balance(self):
  r=ready();measure(r,'blank');measure(r);L=r._ledger();self.assertLess(abs(L['mass_residual_kg']),1e-16);self.assertGreater(L['reaction_products_kg'],0)
 def test_07_equivalent_balance_cancel_drain(self):
  r=ready();measure(r,'blank');load(r);r.invoke('measurement.start');r.step(.7);r.invoke('measurement.stop');r.invoke('sample.unload',recipient_id='host');r.invoke('product.drain',recipient_id='waste');L=json.loads(r._ledger()['equivalent_ledger_json']);self.assertLess(abs(L['residual_mol']),1e-16);self.assertGreater(L['drained_mol'],0)
 def test_08_gas_balance_and_Ar_mode(self):
  r=ready(horizontal=True);measure(r,'blank');measure(r);g=json.loads(r._ledger()['gas_ledger_json']);self.assertLess(abs(g['oxygen_residual_mol']),1e-9);self.assertLess(abs(g['argon_residual_mol']),1e-9);self.assertGreater(g['argon_exhaust_mol'],0)
 def test_09_thermal_balance_cooling(self):
  r=ready();r.invoke('power.set',enabled=False);r.step(1.);L=r._ledger();self.assertLess(abs(L['energy_residual_j']),1e-7);self.assertAlmostEqual(r.temperature,298.15)
 def test_10_template_query_only(self):
  r=ready();self.assertIn('reported_recovery',r.invoke('template.read',substance_id='reference_only')['template_json']);self.rejected(r,'sample.load','REFERENCE_QUERY_ONLY',**(SAMPLE_EXAMPLE|dict(template_id='reference_only')))
 def test_11_unknown_not_standard(self):
  r=ready();self.rejected(r,'sample.load','REFERENCE_NOT_CALIBRATION_ELIGIBLE',**(SAMPLE_EXAMPLE|dict(template_id='unknown',kind='standard')))
 def test_12_missing_preparation_ack(self):
  r=ready();self.rejected(r,'sample.load','PREPARATION_ACK_REQUIRED',**(SAMPLE_EXAMPLE|dict(template_id='unknown',accept_prepared_input=False)))
 def test_13_wrong_mode_input(self):
  r=ready();self.rejected(r,'sample.load','INPUT_FORM_MISMATCH',**(SAMPLE_EXAMPLE|dict(template_id='unknown',input_form='liquid')))
 def test_14_invalid_liquid_volume(self):
  r=ready(mode='EOX');self.rejected(r,'sample.load','INVALID_VOLUME',**(SAMPLE_EXAMPLE|dict(template_id='unknown',input_form='extract',volume_m3=0.)))
 def test_15_excess_sample100mg(self):
  r=ready();self.rejected(r,'sample.load','INVALID_ARGUMENT',**(SAMPLE_EXAMPLE|dict(template_id='unknown',mass_kg=.000101)))
 def test_16_excess_volume100uL(self):
  r=ready(mode='EOX');self.rejected(r,'sample.load','INVALID_ARGUMENT',**(SAMPLE_EXAMPLE|dict(template_id='unknown',input_form='extract',volume_m3=1.01e-7)))
 def test_17_no_blank_no_measure(self):
  r=ready();load(r);self.rejected(r,'measurement.start','BLANK_REQUIRED')
 def test_18_consumed_no_repeat(self):
  r=ready();measure(r,'blank');self.rejected(r,'measurement.start','SAMPLE_ALREADY_CONSUMED')
 def test_19_thermal_target_finite(self):
  r=ready();old=r.temperature;r.invoke('temperature.set',temperature_k=1373.15);self.assertEqual(r.temperature,old);r.step(.02);self.assertGreater(r.temperature,old);self.assertLess(r.temperature,1373.15)
 def test_20_low_temperature_configurable_not_ready(self):
  r=ready();r.invoke('method.configure',**(METHOD_EXAMPLE|dict(temperature_k=973.15)));r.step(.3);self.assertFalse(r._ready())
 def test_21_overheated_setting_rejected(self):
  r=ready();self.rejected(r,'temperature.set','INVALID_ARGUMENT',temperature_k=1373.16)
 def test_22_cold_swap_requires_doors(self):
  r=JenaMultixRuntime(library=LIB);r.invoke('plant.configure',**PLANT_EXAMPLE);self.rejected(r,'direction.set','MAINTENANCE_DOOR_CLOSED',direction='vertical')
 def test_23_hot_door_rejected(self):
  r=ready();self.rejected(r,'cover.set','COLD_ISOLATED_MAINTENANCE_REQUIRED',door='main_left',opened=True)
 def test_24_independent_doors(self):
  r=JenaMultixRuntime(library=LIB);r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('cover.set',door='main_left',opened=True);v=r.read();self.assertEqual(v['cover.main_left_angle_rad'],1.6);self.assertEqual(v['cover.main_right_angle_rad'],0)
 def test_25_horizontal_requires_option(self):
  r=JenaMultixRuntime(library=LIB);r.invoke('plant.configure',**PLANT_EXAMPLE)
  for d in ['main_left','main_right']:r.invoke('cover.set',door=d,opened=True)
  self.rejected(r,'direction.set','OPTION_NOT_INSTALLED',direction='horizontal')
 def test_26_pox_requires_option(self):
  r=JenaMultixRuntime(library=LIB);r.invoke('plant.configure',**PLANT_EXAMPLE);self.rejected(r,'method.configure','OPTION_NOT_INSTALLED',**(METHOD_EXAMPLE|dict(mode='POX')))
 def test_27_TX_requires_horizontal(self):
  r=ready();self.rejected(r,'method.configure','DIRECTION_MODE_MISMATCH',**(METHOD_EXAMPLE|dict(mode='TX')))
 def test_28_POX_conditioned_gas_chain(self):
  r=ready(mode='POX');measure(r,'blank');q=measure(r);self.assertEqual(q['mode'],'POX');self.assertGreater(q['observed_charge_c'],0);self.assertIsNone(q['chlorine_equivalent_mass_kg'])
 def test_29_EOX_conditioned_extract_chain(self):
  r=ready(mode='EOX');measure(r,'blank');q=measure(r);self.assertEqual(q['mode'],'EOX');self.assertGreater(q['observed_charge_c'],0)
 def test_30_TX_actual_calibration(self):
  r=calibrated(horizontal=True);q=measure(r);self.assertAlmostEqual(q['chlorine_equivalent_mass_kg'],1.81e-9,places=17)
 def test_31_F_not_detected(self):
  r=ready();measure(r,'blank');q=measure(r,template_id='',halogen_moles=[{'element':'F','amount_mol':1e-8}]);self.assertAlmostEqual(q['blank_corrected_charge_c'],0.,places=15);L=json.loads(r._ledger()['species_ledger_json']);self.assertEqual(L['F']['registered_mol'],1e-8)
 def test_32_ClBrI_same_moles_charge(self):
  charges=[]
  for e in ['Cl','Br','I']:
   r=ready();measure(r,'blank');q=measure(r,template_id='',halogen_moles=[{'element':e,'amount_mol':4e-8}]);charges.append(q['blank_corrected_charge_c'])
  self.assertTrue(np.allclose(charges,charges[0],rtol=1e-12,atol=0))
 def test_33_cell_current_cap(self):
  r=ready();measure(r,'blank');q=measure(r,fraction=1.);self.assertTrue(all(w['current_a']<=.001*PLANT_EXAMPLE['detector_gain']+PLANT_EXAMPLE['baseline_current_a']+1e-12 for w in q['windows']));self.assertGreater(r.cell_pending,0)
 def test_34_cell_ranges_not_merged(self):
  self.assertEqual(CELLS['high_sensitive']['max_kg'],1e-8);self.assertEqual(CELLS['sensitive']['min_kg'],1e-9);self.assertEqual(CELLS['high_concentration']['volume_m3'],.00012)
 def test_35_high_sensitive_open_tube_forbidden(self):
  r=JenaMultixRuntime(library=LIB);r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('cover.set',door='chlorine',opened=True);self.rejected(r,'cell.prepare','CELL_TUBE_MISMATCH',cell_type='high_sensitive')
 def test_36_silver_exhaustion_real_failure(self):
  r=ready(silver_initial_mol=1e-9);measure(r,'blank');q=measure(r);self.assertEqual(q['phase'],'failed');self.assertGreater(r.cell_pending,0);self.assertIsNone(q['chlorine_equivalent_mass_kg'])
 def test_37_gas_stop_cancels(self):
  r=ready();measure(r,'blank');load(r);r.invoke('measurement.start');r.step(.7);r.invoke('gas.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.assertGreater(r.sample['burned'],0)
 def test_38_power_stop_cancels(self):
  r=ready();measure(r,'blank');load(r);r.invoke('measurement.start');r.step(.7);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled')
 def test_39_PORT_locked_running(self):
  r=ready(horizontal=True);load(r,'blank');r.invoke('measurement.start');self.rejected(r,'sample_port.toggle','BUSY')
 def test_40_ABD_stop_requires_cycle(self):
  r=ready(horizontal=True);r.invoke('abd.stop');self.assertTrue(r.abd_stopped);r.invoke('abd.power',enabled=True);self.assertTrue(r.abd_stopped);r.invoke('abd.power',enabled=False);r.invoke('abd.power',enabled=True);self.assertFalse(r.abd_stopped)
 def test_41_ABD_STOP_stops_motion(self):
  r=ready(horizontal=True);load(r,'blank');r.invoke('measurement.start');r.step(.2);q=r.injection;r.invoke('abd.stop');r.step(.2);self.assertEqual(r.injection,q);self.assertEqual(r.phase,'cancelled')
 def test_42_liquid_cooldown_not_accelerated(self):
  r=ready(horizontal=True);q=measure(r,'blank',input_form='liquid',volume_m3=1e-8);self.assertEqual(r.cooldown,200);r.step(1);self.assertAlmostEqual(r.cooldown,199);self.rejected(r,'sample_port.toggle','ABD_NOT_READY')
 def test_43_method_history_frozen(self):
  r=ready();q=measure(r,'blank');r.invoke('method.configure',**(METHOD_EXAMPLE|dict(window_s=.3)));self.assertEqual(r._result(),q)
 def test_44_foreign_config_calibration_rejected(self):
  r=calibrated();r.invoke('method.configure',**(METHOD_EXAMPLE|dict(integration_s=13.)));self.rejected(r,'calibration.select','CALIBRATION_SIGNATURE_MISMATCH',curve_id='observed')
 def test_45_fake_standard_record_rejected(self):
  r=ready();q=measure(r,'blank');self.rejected(r,'calibration.fit','ACTUAL_STANDARD_REQUIRED',curve_id='bad',record_ids=[q['record_id'],'other','third'])
 def test_46_duplicate_standard_not_fit(self):
  r=ready();self.rejected(r,'calibration.fit','DUPLICATE_STANDARD',curve_id='bad',record_ids=['a','a','a'])
 def test_47_agent_hidden_sample_denied(self):
  r=ready();s=r.session();before=state(r);q=s.invoke(instance_id=r.instance_id,capability_id='sample.load',arguments=SAMPLE_EXAMPLE,request_id='x');self.assertEqual(q['error']['code'],'ACCESS_DENIED');self.assertEqual(before,state(r));self.assertNotIn('truth.ledger',s.read(instance_id=r.instance_id)['observations'])
 def test_48_discovery_query_pure(self):
  r=ready();before=state(r);r.describe();r.session().read(instance_id=r.instance_id);r.invoke('template.read',substance_id='unknown');self.assertEqual(before,state(r))
 def test_49_de_duplicate_and_conflict(self):
  r=ready();s=r.session();q=s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='r');rev=r.revision;self.assertEqual(q,s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='r'));self.assertEqual(rev,r.revision);self.assertEqual(s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='r')['error']['code'],'REQUEST_CONFLICT')
 def test_50_history_poll_and_ownership(self):
  r=ready();r.register_principal('other');load(r,'blank');s=r.session();q=s.invoke(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='op');r.step(12);old=s.poll(q['operation_id']);measure(r,'blank');self.assertEqual(old,s.poll(q['operation_id']));self.assertRaises(ActionRejected,r.session('other').poll,q['operation_id'])
 def test_51_poll_cancellation_idempotent(self):
  r=ready();load(r,'blank');s=r.session();q=s.invoke(instance_id=r.instance_id,capability_id='measurement.start',arguments={},request_id='start');r.step(.135);a=s.cancel(q['operation_id'],'cancel');self.assertEqual(a,s.cancel(q['operation_id'],'cancel'));self.assertEqual(s.poll(q['operation_id'])['status'],'cancelled')
 def test_52_dual_instances(self):
  a=ready();b=ready();before=state(b);measure(a,'blank');self.assertEqual(before,state(b))
 def test_53_finite_input_rejection(self):
  r=ready()
  for value in [True,float('nan'),float('inf'),-1.,1400.]:self.rejected(r,'temperature.set','INVALID_ARGUMENT',temperature_k=value)
 def test_54_step_partition(self):
  a=ready();b=ready();load(a,'blank');load(b,'blank');a.invoke('measurement.start');b.invoke('measurement.start');a.step(1.)
  for _ in range(50):b.step(.02)
  self.assertAlmostEqual(a.charge,b.charge,places=15);self.assertAlmostEqual(a.temperature,b.temperature,places=10)
 def test_55_sampling_window_not_read_time(self):
  r=ready();q=measure(r,'blank');end=q['windows'][-1]['end_s'];r.step(.3);v=r.session().read(instance_id=r.instance_id)['observations']['detector.current_a'];self.assertAlmostEqual(v['sample_time_s'],end);self.assertLess(v['sample_time_s'],r.time_s)
 def test_56_JSON_CSV_roundtrip(self):
  import csv,io
  r=ready();q=measure(r,'blank');self.assertEqual(json.loads(r.invoke('result.export',format='json')['content']),q);rows=list(csv.DictReader(io.StringIO(r.invoke('result.export',format='csv')['content'])));self.assertEqual(len(rows),len(q['windows']));self.assertAlmostEqual(sum(float(v['observed_charge_c']) for v in rows),q['observed_charge_c'])
 def test_57_complete_metadata_schema(self):
  p=ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py';sp=importlib.util.spec_from_file_location('asset_tool_multix',p);t=importlib.util.module_from_spec(sp);sp.loader.exec_module(t);schema=json.loads((ROOT/'skills/chemistry-lab-assets/assets/asset-contract.schema.json').read_text());m=export_jena_multix_contract_metadata()
  for key,group in [('capability',m['capabilities']),('slot',m['state_slots'])]:
   for name,value in group.items():
    errors=t.validate_value(value,{'$ref':'#/$defs/'+key},schema);self.assertEqual(errors,[],(name,errors))
  for k,v in m['capabilities'].items():
   validate(v['input_schema'],v['example_arguments']);self.assertTrue(all(re.fullmatch('[A-Z][A-Z0-9_]*',e['code']) for e in v['errors']))
 def test_58_actual_return_schemas(self):
  r=ready();q=measure(r,'blank');validate(RESULT,q)
  for k,a in [('process.measure',{}),('signal.read',{}),('blank.read',{}),('records.list',{}),('product.read',{}),('result.export',{'format':'json'})]:validate(CAPS[k][1],r.invoke(k,**a))
 def test_59_unsupported_never_success(self):
  r=ready()
  for k in UNSUPPORTED:self.rejected(r,k,'UNSUPPORTED')
 def test_60_no_hidden_truth_in_result(self):
  r=ready();measure(r,'blank');q=measure(r);j=json.dumps(q);self.assertNotIn('reference_mass_kg',j);self.assertNotIn('species',q);self.assertNotIn('truth',q);self.assertIsNone(q['chlorine_equivalent_mass_kg'])
 def test_61_oxygen_depletion_failure(self):
  r=ready(oxygen_supply_mol=.001);load(r,'blank');r.invoke('measurement.start');r.step(1);self.assertEqual(r.phase,'failed');g=json.loads(r._ledger()['gas_ledger_json']);self.assertLess(abs(g['oxygen_residual_mol']),1e-12)
 def test_62_records_remove_preserves_live(self):
  r=ready();q=measure(r,'blank');r.invoke('records.remove',record_id=q['record_id']);self.assertEqual(r._result(),q);self.assertEqual(r.invoke('records.list')['record_ids'],[])
 def test_63_late_mode_change_does_not_reinterpret_sample(self):
  r=ready();load(r,'blank');r.invoke('method.configure',**(METHOD_EXAMPLE|dict(mode='POX')));self.rejected(r,'measurement.start','INPUT_FORM_MISMATCH')
 def test_64_impossible_window_rejected(self):
  r=ready();self.rejected(r,'method.configure','INVALID_TIME_WINDOW',**(METHOD_EXAMPLE|dict(integration_s=.5)))
 def test_65_horizontal_AOX_requires_argon(self):
  r=ready(horizontal=True,mode='AOX');self.assertTrue(r._needs_argon());a=r.argon_remaining;r.step(.1);self.assertLess(r.argon_remaining,a)
 def test_66_source_stage_cannot_be_forged(self):
  r=ready(mode='EOX');r.templates['carbon']={'chlorine_equivalent_mass_kg':1e-9,'input_form':'loaded_carbon','supported_modes':['AOX'],'physical_input_eligible':True};self.rejected(r,'sample.load','SOURCE_STAGE_MISMATCH',**(SAMPLE_EXAMPLE|dict(template_id='carbon',input_form='extract',volume_m3=1e-8)))
 def test_67_equivalent_cannot_exceed_gross_mass(self):
  r=ready();self.rejected(r,'sample.load','EQUIVALENT_EXCEEDS_SAMPLE_MASS',**(SAMPLE_EXAMPLE|dict(template_id='unknown',mass_kg=1e-9,reference_fraction=1.)))
 def test_68_nonliquid_volume_not_source_water(self):
  r=ready();self.rejected(r,'sample.load','LIQUID_VOLUME_ONLY',**(SAMPLE_EXAMPLE|dict(template_id='unknown',volume_m3=1e-8)))
 def test_69_original_water_query_only(self):
  r=ready(mode='EOX');r.templates['water']={'basis':'chlorine_equivalent','concentration_kg_m3':.01,'physical_input_eligible':False};self.rejected(r,'sample.load','REFERENCE_QUERY_ONLY',**(SAMPLE_EXAMPLE|dict(template_id='water',input_form='extract',volume_m3=1e-8)))
 def test_70_liquid_volume_cancel_balance(self):
  r=ready(mode='EOX');measure(r,'blank');load(r);r.invoke('measurement.start');r.step(.7);r.invoke('measurement.stop');r.invoke('sample.unload',recipient_id='host');L=json.loads(r._ledger()['volume_ledger_json']);self.assertLess(abs(L['residual_m3']),1e-20);self.assertGreater(L['consumed_original_liquid_m3'],0);self.assertGreater(L['returned_m3'],0)
 def test_71_sample_form_part_of_calibration(self):
  r=calibrated(horizontal=True);before=r._key();load(r,kind='blank',input_form='liquid',volume_m3=1e-8);self.assertNotEqual(before,r._key());self.rejected(r,'calibration.select','CALIBRATION_SIGNATURE_MISMATCH',curve_id='observed')
 def test_72_finite_boat_feed_cannot_burn_early(self):
  r=ready(horizontal=True,feed_speed_m_s=.01);load(r,'blank');self.rejected(r,'measurement.start','FEED_TIME_TOO_SHORT')
 def test_73_actual_private_library_stages(self):
  j=_load_actual_library();r=ready();r.templates=j['compounds'];r.invoke('sample.load',**(SAMPLE_EXAMPLE|dict(template_id='aox_linear_100_r1_c1')));self.assertGreater(r.sample['equiv'],0);self.assertEqual(j['compounds']['tx_paper_ecf']['input_form'],'solid')
 def test_74_agent_readonly_direction_and_host_invocation(self):
  r=ready();meta=export_jena_multix_contract_metadata()['state_slots']['furnace.direction'];self.assertEqual(meta['access'],'read_only');self.assertIsNone(meta['write_via']);self.assertEqual(r.session().read(instance_id=r.instance_id,state_ids=['furnace.direction'])['observations']['furnace.direction']['value'],'vertical');self.assertRaises(ActionRejected,r.session().write,instance_id=r.instance_id,state_id='furnace.direction',value='horizontal',request_id='forbidden')
 def test_75_observed_current_endpoint_tail_rejected(self):
  r=ready();measure(r,'blank');q=measure(r,kind='standard',fraction=1.);self.assertTrue(any('endpoint unresolved' in x for x in q['quality']));self.assertIsNone(q['chlorine_equivalent_mass_kg']);self.assertGreater(q['windows'][-1]['current_a'],.0005);self.rejected(r,'calibration.fit','INVALID_STANDARD_OBSERVATION',curve_id='bad',record_ids=[q['record_id'],'x','y'])
 def test_76_liquid_cooling_waits_for_return_to_sluice(self):
  r=ready(horizontal=True);load(r,'blank',input_form='liquid',volume_m3=1e-8);r.invoke('measurement.start');r.step(.6);self.assertGreater(r.injection,.2);r.invoke('measurement.stop');r.step(.1);self.assertGreater(r.injection,0);self.assertEqual(r.cooldown,200.)
def _load_actual_library():return json.loads((ROOT/TEMPLATE_REL).read_text())
if __name__=='__main__':unittest.main()
