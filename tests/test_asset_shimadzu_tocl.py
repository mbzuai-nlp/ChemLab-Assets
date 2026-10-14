"""CHEM-080 independent aliquot chemistry, numerical inference and durable-in-instance records."""
import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import shimadzu_tocl as r
LIB=r._library()

def finish(h,dt=.25):
 for _ in range(20000):
  if h.phase!='running':return
  h.step(dt)
 raise AssertionError('measurement did not finish')
def until(h,stage):
 for _ in range(10000):
  if h.stage==stage:return
  if h.phase!='running':raise AssertionError('measurement ended before '+stage)
  h.step(max(1e-8,h._due()))
 raise AssertionError('stage not reached')
def ready(plant=None,method=None,instance='tocl_test',library=None):
 h=r.ShimadzuToclRuntime(instance,library=LIB if library is None else library);h.invoke('plant.configure',**dict(dict(r.PLANT_EXAMPLE,ndir_noise_sd=0.),**(plant or {})));h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,**(method or {})));h.invoke('furnace.configure',**r.FURNACE_EXAMPLE);h.invoke('power.toggle');h.step(1.5);return h
def load(h,kind='sample',**changes):
 a=copy.deepcopy(r.BLANK_EXAMPLE if kind=='blank' else r.STANDARD_EXAMPLE if kind=='standard' else r.SAMPLE_EXAMPLE);a.update(changes);a['kind']=kind;h.invoke('sample.load',**a)
def replace(h,kind='sample',**changes):
 if h.sample:h.invoke('sample.unload',recipient_id='external_rack')
 load(h,kind,**changes)
def run(h):h.invoke('measurement.start');finish(h);return h.invoke('result.read')
def blank(h):load(h,'blank');run(h);h.invoke('sample.unload',recipient_id='blank_rack');return h

def calibrated(channels=('TC','IC'),plant=None):
 h=blank(ready(plant=plant));observations={}
 for channel in channels:
  h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,mode=channel))
  if channel not in h._blanks:replace(h,'blank');run(h)
  source=r.IC_STANDARD_EXAMPLE if channel=='IC' else r.STANDARD_EXAMPLE;replace(h,'standard',**{k:v for k,v in source.items() if k!='kind'});obs=[]
  for v in [1e-8,1e-7,1e-6]:
   h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,mode=channel,injection_volume_m3=v));q=run(h)['assays'][0];obs.append(dict(reference_id=source['template_id'],reference_channel='IC' if channel=='IC' else 'TOC' if channel=='NPOC' else 'TC',injection_volume_m3=v,dilution_factor=1.,peak_area_s=q['peak_area_s']))
  h.invoke('calibration.fit',curve_id=channel,channel=channel,observations=obs,accept_source_channel_transfer=channel=='NPOC',accept_same_response_conditions=True);h.invoke('calibration.select',channel=channel,curve_id=channel);observations[channel]=obs
 h.invoke('sample.unload',recipient_id='standard_rack');h.invoke('method.configure',**r.METHOD_EXAMPLE);return h,observations

class ToclTests(unittest.TestCase):
 def reject(self,h,code,cap,**a):
  before=repr(h.debug_snapshot())
  with self.assertRaisesRegex(r.ActionRejected,code):h.invoke(cap,**a)
  self.assertEqual(before,repr(h.debug_snapshot()))
 def closed(self,h):
  q=h.invoke('product.read');self.assertLess(max(abs(v) for v in q['carbon_kg']['residual'].values()),1e-16);self.assertLess(abs(q['volume_m3']['residual']),1e-15);self.assertLess(abs(q['oxygen_residual_mol']),1e-12);self.assertLess(abs(q['acid_residual_mol']),1e-12);self.assertLess(abs(q['thermal_residual_j']),1e-7)
 def test_source_tc_ic_and_toc_are_distinct_measured_fields(self):
  h=ready();q=json.loads(h.invoke('template.read',substance_id='shimadzu_pfos_1')['template_json']);c=q['carbon_concentrations_kg_m3'];self.assertEqual(c['TC'],.001898);self.assertAlmostEqual(c['IC'],.000058,places=16);self.assertAlmostEqual(c['TOC'],.001840,places=16);self.assertIsNone(c['NPOC'])
 def test_recovery_only_reference_is_queryable_not_replayable(self):
  h=ready();key=next(k for k,v in h.templates.items() if not any(x is not None for x in v['carbon_concentrations_kg_m3'].values()));q=json.loads(h.invoke('template.read',substance_id=key)['template_json']);self.assertFalse(q['reported_channels']);self.reject(h,'INVALID_REFERENCE','sample.load',**dict(r.SAMPLE_EXAMPLE,template_id=key))
 def test_unreported_source_channel_not_invented(self):
  h=ready();self.reject(h,'UNREPORTED_SOURCE_CHANNEL','sample.load',**dict(r.SAMPLE_EXAMPLE,template_id='usgs_nwisma_01_01800952',template_channel='IC'))
 def test_toc_only_requires_ic_and_poc_partition(self):
  h=ready();a=dict(r.SAMPLE_EXAMPLE,template_id='usgs_nwisma_01_01800952',template_channel='TOC',assumed_ic_kg_m3=None);self.reject(h,'CARBON_PARTITION_REQUIRED','sample.load',**a);a['assumed_ic_kg_m3']=0.;a['assumed_poc_kg_m3']=None;self.reject(h,'CARBON_PARTITION_REQUIRED','sample.load',**a)
 def test_partition_assumptions_require_host_ack(self):
  h=ready();self.reject(h,'PARTITION_ACK_REQUIRED','sample.load',**dict(r.SAMPLE_EXAMPLE,accept_partition_assumptions=False))
 def test_measured_ic_cannot_be_overwritten(self):
  h=ready();self.reject(h,'REPORTED_VALUE_OVERRIDE','sample.load',**dict(r.SAMPLE_EXAMPLE,assumed_ic_kg_m3=0.))
 def test_impossible_organic_partition_rejects(self):
  h=ready();self.reject(h,'CARBON_PARTITION_RANGE','sample.load',**dict(r.SAMPLE_EXAMPLE,assumed_poc_kg_m3=1.))
 def test_source_unknown_conditions_require_ack(self):
  h=ready();self.reject(h,'UNKNOWN_CONDITIONS','sample.load',**dict(r.SAMPLE_EXAMPLE,accept_unknown_conditions=False))
 def test_expired_assigned_standard_evidence_is_preserved(self):
  h,_=calibrated(('TC',));q=json.loads(h.invoke('calibration.read',curve_id='TC')['curve_json']);self.assertTrue(q['source_evidence'][0]['certificate']['assigned_not_individual_measurement']);self.assertIn('expired',q['source_evidence'][0]['certificate']['current_physical_validity'])
 def test_blank_cannot_contain_source_or_added_carbon(self):
  h=ready();self.reject(h,'BLANK_COMPOSITION','sample.load',**dict(r.BLANK_EXAMPLE,assumed_poc_kg_m3=.01))
 def test_connected_bottle_requires_explicit_unload(self):
  h=ready();load(h);self.reject(h,'UNLOAD_REQUIRED','sample.load',**r.SAMPLE_EXAMPLE)
 def test_documented_injection_limits_are_si(self):
  h=ready()
  for volume in [9e-9,2.001e-6]:self.reject(h,'INVALID_ARGUMENT','method.configure',**dict(r.METHOD_EXAMPLE,injection_volume_m3=volume))
  for volume in [1e-8,2e-6]:h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,injection_volume_m3=volume))
 def test_no_dilution_is_distinct_from_auto_range(self):
  h=ready();self.reject(h,'DILUTION_RANGE','method.configure',**dict(r.METHOD_EXAMPLE,dilution_factor=1.5))
  for d in [1.,2.,50.]:h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,dilution_factor=d))
 def test_tc_range_guard_is_not_lod_threshold(self):
  lib=copy.deepcopy(LIB);lib['compounds']['shimadzu_pfos_1']['carbon_concentrations_kg_m3']['TC']=30.01;h=blank(ready(library=lib));load(h);self.reject(h,'TC_RANGE','measurement.start');h2=ready();self.assertEqual(r.SAMPLE['properties']['assumed_ic_kg_m3']['anyOf'][0]['minimum'],0)
 def test_ic_range_does_not_use_tc_limit(self):
  lib=copy.deepcopy(LIB);lib['compounds']['crm_altus_bicarbonate_1mg']['carbon_concentrations_kg_m3']['IC']=35.;h=blank(ready(method={'mode':'IC'},library=lib));load(h,'standard',**{k:v for k,v in r.IC_STANDARD_EXAMPLE.items() if k!='kind'});h.invoke('measurement.start');self.assertEqual(h.phase,'running')
 def test_furnace_target_and_exact_finite_heat(self):
  h=ready();old=h.temperature;h.invoke('power.toggle');h.step(.1);self.assertAlmostEqual(h.temperature,298.15+(old-298.15)*math.exp(-.5),places=10);self.closed(h)
 def test_setting_furnace_is_not_instantaneous_temperature(self):
  h=ready();old=h.temperature;h.invoke('furnace.configure',temperature_k=500.);self.assertEqual(h.temperature,old);self.assertFalse(h._ready());self.reject(h,'INVALID_ARGUMENT','furnace.configure',temperature_k=1000.)
 def test_tc_needs_680c_but_ic_is_unburned_route(self):
  h=blank(ready(method={'mode':'IC'}));h.invoke('furnace.configure',temperature_k=298.15);h.step(2);load(h,'standard',**{k:v for k,v in r.IC_STANDARD_EXAMPLE.items() if k!='kind'});run(h);self.assertEqual(h._oxygen_consumed,0);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,mode='TC'));self.reject(h,'FURNACE_NOT_READY','measurement.start')
 def test_top_cover_requires_cold_power_off(self):
  h=ready();self.reject(h,'COLD_MAINTENANCE_REQUIRED','top_cover.set',opened=True);h.invoke('power.toggle');h.step(2);h.invoke('top_cover.set',opened=True);self.assertEqual(h.read()['top_cover.lift_m'],.15);self.reject(h,'POWER_INTERLOCK','power.toggle');h.invoke('top_cover.set',opened=False)
 def test_front_door_interrupts_and_cools(self):
  h=blank(ready());load(h);h.invoke('measurement.start');until(h,'combust');h.invoke('cover.set',opened=True);self.assertEqual(h.phase,'cancelled');old=h.temperature;h.step(.1);self.assertLess(h.temperature,old);self.assertEqual(h.read()['cover.angle_rad'],1.6)
 def test_preconditions_reject_before_consuming_sample(self):
  h=ready();load(h);self.reject(h,'MATCHED_BLANK_REQUIRED','measurement.start');h2=ready(plant={'carrier_ready':False});load(h2,'blank');self.reject(h2,'GAS_POWER_INTERLOCK','measurement.start')
 def test_sufficient_volume_checked_for_both_toc_aliquots(self):
  h=blank(ready());load(h,volume_m3=7e-7);self.reject(h,'SAMPLE_VOLUME_LIMIT','measurement.start')
 def test_diluent_and_acid_supply_checked_atomically(self):
  for plant,method in [({'diluent_volume_m3':0.},{'dilution_factor':2.}),({'acid_volume_m3':0.},{'mode':'IC'})]:
   h=ready(plant=plant,method=method);load(h,'blank');self.reject(h,'REAGENT_VOLUME_LIMIT','measurement.start')
 def test_finite_syringe_aspiration_consumes_real_volume(self):
  h=blank(ready());load(h);before=h.sample['volume'];h.invoke('measurement.start');until(h,'aspirate_sample');h.step(.025);self.assertGreater(h.read()['syringe.position_m'],0);self.assertLess(h.read()['syringe.position_m'],.06);self.assertAlmostEqual(before-h.sample['volume'],5e-8,places=16);self.closed(h)
 def test_dilution_and_acid_addition_close_aqueous_volume(self):
  h=blank(ready(method={'dilution_factor':5.}));load(h);before=h.sample['volume'];run(h);self.assertAlmostEqual(before-h.sample['volume'],2*5e-7/5,places=15);self.assertAlmostEqual(h._waste_v,2*5e-7+r.METHOD_EXAMPLE['acid_volume_m3']+2*5e-7+r.METHOD_EXAMPLE['acid_volume_m3'],places=15);self.closed(h)
 def test_toc_uses_two_independent_unburned_aliquots(self):
  h=blank(ready());load(h);before=h.sample['volume'];q=run(h);self.assertEqual([x['channel'] for x in q['assays']],['TC','IC']);self.assertAlmostEqual(before-h.sample['volume'],1e-6,places=15);self.assertGreater(q['assays'][0]['peak_area_s'],q['assays'][1]['peak_area_s']*20);self.closed(h)
 def test_ic_does_not_consume_organic_carbon_or_oxygen(self):
  h=blank(ready(method={'mode':'IC'}));load(h);q=run(h);self.assertEqual(h._oxygen_consumed,0);self.assertGreater(h._waste[1],0);self.assertEqual(h._exhaust[1],0);self.closed(h)
 def test_npoc_finite_poc_stripping_differs_from_total_organic(self):
  outputs=[]
  for rate in [0.,100.]:
   h=blank(ready(plant={'poc_strip_rate_s_inv':rate},method={'mode':'NPOC'}));load(h,template_id='usgs_nwisma_01_01800952',template_channel='TOC',assumed_ic_kg_m3=.001,assumed_poc_kg_m3=.001);q=run(h);outputs.append(q['assays'][0]['peak_area_s']);self.closed(h)
  self.assertGreater(outputs[0],outputs[1]*1.3)
 def test_source_toc_to_npoc_assumptions_are_public_without_identity(self):
  h=blank(ready(method={'mode':'NPOC'}));load(h,template_id='usgs_nwisma_01_01800952',template_channel='TOC',assumed_ic_kg_m3=0.);q=run(h);self.assertIn('source_TOC_conditioned_as_NPOC_is_not_measured_source_NPOC',q['model_assumptions']);self.assertIn('sample_IC_partition_is_explicit_host_assumption',q['model_assumptions']);self.assertNotIn('usgs_nwisma_01_01800952',json.dumps(q))
 def test_acid_limited_ic_release_keeps_carbon_in_liquid_waste(self):
  h=blank(ready(plant={'acid_concentration_mol_m3':.001},method={'mode':'IC'}));load(h);run(h);self.assertGreater(h._waste[0],1e-11);self.assertLess(h._exhaust[0]+h._tail[0],1e-14);self.closed(h)
 def test_oxygen_limited_conversion_keeps_unconverted_carbon(self):
  h=blank(ready(plant={'oxygen_inventory_mol':0.},method={'mode':'TC'}));load(h);run(h);self.assertGreater(h._waste[1],1e-10);self.assertEqual(h._exhaust[1],0);self.closed(h)
 def test_peak_area_is_independent_integral_of_returned_windows(self):
  h=blank(ready());load(h);q=run(h)
  for a in q['assays']:self.assertAlmostEqual(a['peak_area_s'],sum(p['corrected_signal']*(p['sample_window_s'][1]-p['sample_window_s'][0]) for p in a['peak']),places=14)
 def test_observed_blank_offset_cancels_without_duration_bias(self):
  out=[]
  for offset in [.01,.8]:
   h=blank(ready(plant={'ndir_baseline':offset}));load(h);out.append([q['peak_area_s'] for q in run(h)['assays']])
  np.testing.assert_allclose(out[0],out[1],atol=1e-14,rtol=0)
 def test_terminal_gas_tail_remains_accounted(self):
  h=blank(ready(method={'baseline_threshold':.01}));load(h);run(h);old=float(h._tail.sum());self.assertGreater(old,0);h.step(.1);self.assertLess(float(h._tail.sum()),old);self.closed(h)
 def test_peak_timeout_preserves_partial_data_and_material(self):
  h=blank(ready(method={'maximum_peak_s':.03}));load(h);run(h);self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'NDIR_BASELINE_TIMEOUT');self.assertTrue(h._active['peak']);self.assertIsNone(h._active['concentration_kg_m3']);self.closed(h)
 def test_cancel_after_aspiration_and_external_drain(self):
  h=blank(ready());load(h);h.invoke('measurement.start');until(h,'aspirate_sample');h.step(.04);h.invoke('measurement.stop');self.assertGreater(h._syringe_v,0);self.reject(h,'DRAIN_CANCELLED_RESIDUE','measurement.start');self.reject(h,'POWER_OFF_REQUIRED','fluidics.drain');h.invoke('power.toggle');q=h.invoke('fluidics.drain');self.assertGreater(q['drained_volume_m3'],0);self.assertEqual(h._syringe_v,0);self.closed(h)
 def test_unload_returns_only_unconsumed_bottle_liquid(self):
  h=blank(ready());load(h);run(h);expected=r.SAMPLE_EXAMPLE['volume_m3']-1e-6;q=h.invoke('sample.unload',recipient_id='rack');self.assertAlmostEqual(q['volume_m3'],expected,places=15);self.closed(h)
 def test_incomplete_blank_cannot_enable_any_new_channel(self):
  h=ready();load(h,'blank');h.invoke('measurement.start')
  while len(h.assays)<1:h.step(max(1e-8,h._due()))
  h.invoke('measurement.stop');self.assertEqual(h._blanks,{})
 def test_calibration_from_observed_standards_recovers_pfos(self):
  h,_=calibrated();load(h);q=run(h)['concentrations_kg_m3']
  for key,expected in [('TC',.001898),('IC',.000058),('TOC',.001840)]:self.assertAlmostEqual(q[key],expected,delta=3e-7)
 def test_cross_quantity_calibration_requires_and_retains_ack(self):
  h,obs=calibrated(('TC',));a=dict(curve_id='cross',channel='TC',observations=[dict(o,reference_channel='TOC') for o in obs['TC']],accept_source_channel_transfer=False,accept_same_response_conditions=True);self.reject(h,'SOURCE_CHANNEL_TRANSFER_ACK','calibration.fit',**a);a['accept_source_channel_transfer']=True;self.assertTrue(h.invoke('calibration.fit',**a)['source_channel_transfer'])
 def test_npoc_calibration_transfer_is_visible_in_result_quality(self):
  h,_=calibrated(('TC','IC','NPOC'));h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,mode='NPOC'));load(h);q=run(h);self.assertIn('NPOC_source_channel_transfer_assumption',q['qc']);self.assertAlmostEqual(q['concentrations_kg_m3']['NPOC'],.001840,delta=3e-7)
 def test_toc_negative_difference_is_preserved_not_clipped(self):
  h,obs=calibrated();biased=[dict(q,peak_area_s=q['peak_area_s']*.03) for q in obs['IC']];h.invoke('calibration.fit',curve_id='biased_IC',channel='IC',observations=biased,accept_source_channel_transfer=False,accept_same_response_conditions=True);h.invoke('calibration.select',channel='IC',curve_id='biased_IC');load(h);q=run(h);self.assertLess(q['concentrations_kg_m3']['TOC'],0);self.assertIn('negative_TOC_difference_preserved',q['qc'])
 def test_repeat_statistics_use_sample_sd_and_retain_every_peak(self):
  h,_=calibrated(plant={'ndir_noise_sd':2e-7});h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,repeats=3));load(h);q=run(h);self.assertEqual(len(q['assays']),6)
  for c in ['TC','IC']:
   a=[x['concentration_kg_m3'] for x in q['assays'] if x['channel']==c];self.assertAlmostEqual(q['sd_kg_m3'][c],float(np.std(a,ddof=1)),places=14);self.assertAlmostEqual(q['cv'][c],float(np.std(a,ddof=1)/np.mean(a)),places=11)
 def test_one_repeat_sd_is_unknown_not_zero(self):
  h,_=calibrated();load(h);q=run(h);self.assertIsNone(q['sd_kg_m3']['TC']);self.assertIsNone(q['cv']['TC'])
 def test_uncalibrated_and_extrapolated_results_are_null(self):
  h=blank(ready());load(h);q=run(h);self.assertIsNone(q['concentrations_kg_m3']['TC']);h,_=calibrated();h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,injection_volume_m3=1e-8));load(h);q=run(h);self.assertIsNone(q['concentrations_kg_m3']['IC']);self.assertIn('IC_outside_calibrated_mass_range',q['qc'])
 def test_calibration_overflow_degenerate_and_wrong_channel(self):
  h,obs=calibrated(('TC',));a=dict(curve_id='bad',channel='TC',observations=[obs['TC'][0]]*3,accept_source_channel_transfer=False,accept_same_response_conditions=True);self.reject(h,'DEGENERATE_CALIBRATION','calibration.fit',**a);a['observations']=[dict(o,peak_area_s=v) for o,v in zip(obs['TC'],[-1e308,0,1e308])];self.reject(h,'CALIBRATION_RANGE','calibration.fit',**a);self.reject(h,'CURVE_CHANNEL_MISMATCH','calibration.select',channel='IC',curve_id='TC')
 def test_response_key_allows_mode_volume_dilution_and_repeats(self):
  h,_=calibrated();key=h._key();h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,mode='TC',injection_volume_m3=2e-7,dilution_factor=2.,repeats=2));self.assertEqual(h._key(),key);self.assertTrue(h._blank_valid());self.assertTrue(h._calibration_ready())
 def test_treatment_change_invalidates_response_not_historical_results(self):
  h,_=calibrated();load(h);old=run(h);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,sparging_s=.4));self.assertFalse(h._blank_valid());self.assertFalse(h._calibration_ready());self.assertEqual(h._result(),old)
 def test_event_sampling_is_partition_invariant(self):
  a=blank(ready(plant={'seed':91,'ndir_noise_sd':1e-7}));b=blank(ready(plant={'seed':91,'ndir_noise_sd':1e-7}))
  for h in [a,b]:load(h);h.invoke('measurement.start')
  finish(a,.25);finish(b,.007);pa=[p['signal'] for q in a.assays for p in q['peak']];pb=[p['signal'] for q in b.assays for p in q['peak']];np.testing.assert_allclose(pa,pb,rtol=1e-8,atol=1e-9);self.closed(a);self.closed(b)
 def test_public_reads_and_exports_are_pure(self):
  h=blank(ready());load(h);run(h);before=repr(h.debug_snapshot())
  for k,a in [('process.measure',{}),('result.read',{}),('peak.read',{}),('blank.read',{}),('calibration.list',{}),('records.list',{'offset':0,'limit':2}),('records.read',{'record_id':h._record_id}),('result.export',{'format':'json'}),('result.export',{'format':'csv'}),('product.read',{})]:h.invoke(k,**a)
  h.describe();self.assertEqual(before,repr(h.debug_snapshot()));self.assertEqual(json.loads(h.invoke('result.export',format='json')['content']),h._result());self.assertIn('peak_json',h.invoke('result.export',format='csv')['content'])
 def test_public_agent_cannot_read_source_identity_or_warehouse(self):
  h=ready();load(h);s=h.session();d=s.describe(instance_id=h.instance_id);self.assertNotIn('sample.load',d['capabilities']);self.assertNotIn('truth.ledger',d['state_slots']);q=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(q['error']['code'],'ACCESS_DENIED');self.assertNotIn('shimadzu_pfos_1',json.dumps(s.read(instance_id=h.instance_id)))
 def test_trusted_session_write_uses_power_interlock(self):
  h=r.ShimadzuToclRuntime(library=LIB);h.register_principal('host','evaluator');s=h.session('host');self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='plant.configure',arguments=r.PLANT_EXAMPLE,request_id='plant')['status'],'succeeded');self.assertIn('truth.ledger',s.read(instance_id=h.instance_id)['observations']);self.assertEqual(s.write(instance_id=h.instance_id,state_id='power.enabled',value=True,request_id='power')['status'],'succeeded')
 def test_request_replay_and_conflict_do_not_toggle_twice(self):
  h=ready();s=h.session();a=dict(instance_id=h.instance_id,capability_id='power.toggle',arguments={},request_id='toggle');q=s.invoke(**a);revision=h.revision;self.assertFalse(h.powered);self.assertEqual(q,s.invoke(**a));self.assertEqual(h.revision,revision);a.update(capability_id='power.set',arguments={'enabled':True});self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT');self.assertFalse(h.powered)
 def test_terminal_history_survives_record_removal_and_new_run(self):
  h=blank(ready());load(h);s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='measurement.start',arguments={},request_id='first')['operation_id'];finish(h);old=s.poll(op);h.invoke('records.remove',record_id=h._record_id);self.assertEqual(s.poll(op),old);new=s.invoke(instance_id=h.instance_id,capability_id='measurement.start',arguments={},request_id='second')['operation_id'];s.cancel(op,'cancel_old');self.assertEqual(h.operation_id,new);self.assertEqual(h.phase,'running');self.assertEqual(s.poll(op),old)
 def test_cancelled_history_partial_peak_and_acl(self):
  h=blank(ready());load(h);s=h.session();h.register_principal('other');op=s.invoke(instance_id=h.instance_id,capability_id='measurement.start',arguments={},request_id='start')['operation_id'];until(h,'detect');h.step(.01);s.cancel(op,'cancel');q=s.poll(op);self.assertEqual(q['status'],'cancelled');self.assertTrue(q['result']['active_assay']['peak'])
  with self.assertRaisesRegex(r.ActionRejected,'ACCESS_DENIED'):h.session('other').poll(op)
 def test_actual_sampling_windows_survive_idle_time(self):
  h,_=calibrated();load(h);run(h);s=h.session();keys=['detector.ndir_signal','result.toc_kg_m3'];before=s.read(instance_id=h.instance_id,state_ids=keys)['observations'];h.step(2);after=s.read(instance_id=h.instance_id,state_ids=keys)['observations'];self.assertEqual(before,after);self.assertLess(before['result.toc_kg_m3']['sample_window_s'][0],before['result.toc_kg_m3']['sample_window_s'][1])
 def test_instances_isolate_volume_calibration_and_records(self):
  a=blank(ready(instance='a'));b=blank(ready(instance='b'));load(a);load(b);before=repr(b.debug_snapshot());run(a);self.assertEqual(before,repr(b.debug_snapshot()))
 def test_record_returns_defensive_full_peak_copy(self):
  h=blank(ready());load(h);q=run(h);record=h.invoke('records.read',record_id=q['record_id']);record['assays'][0]['peak'].clear();self.assertTrue(h.invoke('records.read',record_id=q['record_id'])['assays'][0]['peak']);self.reject(h,'UNKNOWN_RECORD','records.read',record_id='absent')
 def test_capacity_2000_actual_complete_results_no_eviction(self):
  method=dict(mode='TC',injection_volume_m3=1e-8,combustion_s=.001,minimum_peak_s=.001,maximum_peak_s=.03,baseline_samples=1,baseline_threshold=.01);plant=dict(liquid_rate_m3_s=1e-4,selector_speed_rad_s=100.,conversion_rate_s_inv=1000.,transport_tau_s=.001,detector_period_s=.001)
  h=blank(ready(plant=plant,method=method));load(h,'standard',volume_m3=100e-6);first=h._records[next(iter(h._records))]
  for _ in range(1999):h.invoke('measurement.start');h.step(1.)
  self.assertEqual(h.invoke('records.list',offset=0,limit=100)['count'],2000);self.assertEqual(h._records[first['record_id']],first);self.assertTrue(all(v['assays'] and all(a['peak'] for a in v['assays']) for v in h._records.values()));before=h.sample['volume'];revision=h.revision
  with self.assertRaisesRegex(r.ActionRejected,'RESULT_STORE_FULL'):h.invoke('measurement.start')
  self.assertEqual(h.sample['volume'],before);self.assertEqual(h.revision,revision);h.invoke('records.remove',record_id=first['record_id']);h.invoke('measurement.start');h.step(1.);self.assertEqual(len(h._records),2000);self.closed(h)
 def test_capacity_60_full_fits_and_no_duplicate_overwrite(self):
  h,obs=calibrated(('TC',));a=dict(channel='TC',observations=obs['TC'],accept_source_channel_transfer=False,accept_same_response_conditions=True)
  for i in range(59):h.invoke('calibration.fit',curve_id='curve'+str(i),**a)
  self.assertEqual(h.invoke('calibration.list')['count'],60);self.assertEqual(len(json.loads(h.invoke('calibration.read',curve_id='curve58')['curve_json'])['observations']),3);self.reject(h,'CURVE_STORE_FULL','calibration.fit',curve_id='overflow',**a);self.reject(h,'CURVE_ID_EXISTS','calibration.fit',curve_id='TC',**a)
 def test_unsupported_options_metadata_and_real_button_bindings(self):
  h=ready()
  for k in r.UNSUPPORTED:self.reject(h,'UNSUPPORTED',k)
  m=r.export_shimadzu_tocl_contract_metadata();self.assertEqual(len(m['capabilities']),29);self.assertEqual(len(m['state_slots']),32);self.assertIn('button_start',m['capabilities']['measurement.start']['component_ids']);self.assertIn('button_power',m['capabilities']['power.toggle']['component_ids'])
  for k,v in m['capabilities'].items():
   if v['kind']=='query' or k in r.UNSUPPORTED:self.assertEqual(v['state_effects'],[])
 def test_unknown_fields_nonfinite_and_invalid_clock_atomic(self):
  h=ready();self.reject(h,'INVALID_ARGUMENT','method.configure',**dict(r.METHOD_EXAMPLE,unknown=0));self.reject(h,'INVALID_ARGUMENT','sample.load',**dict(r.SAMPLE_EXAMPLE,volume_m3=float('nan')));before=repr(h.debug_snapshot())
  for dt in [True,-1,float('inf'),float('nan')]:
   with self.assertRaises(r.ActionRejected):h.step(dt)
   self.assertEqual(before,repr(h.debug_snapshot()))
  h.step(0);self.assertEqual(before,repr(h.debug_snapshot()))
 def test_event_budget_rejects_large_method_before_consumption(self):
  h=ready(plant={'detector_period_s':.001},method={'maximum_peak_s':3600.});load(h,'blank');self.reject(h,'EVENT_RESOURCE_LIMIT','measurement.start')

if __name__=='__main__':unittest.main()
