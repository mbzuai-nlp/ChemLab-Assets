"""Independent process, numerical and protocol regressions for CHEM-079 only."""
import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import elementar_varioel as r

def finish(h,dt=.25):
 for _ in range(2000):
  if h.phase!='running':return
  h.step(dt)
 raise AssertionError('sequence failed to terminate')
def ready(plant=None,method=None,library=None,instance_id='test_varioel'):
 h=r.ElementarVarioelRuntime(instance_id,library=library);h.invoke('plant.configure',**dict(dict(r.PLANT_EXAMPLE,tcd_noise_sd=0.),**(plant or {})));h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,**(method or {})));h.invoke('furnace.configure',**r.FURNACE_EXAMPLE);h.invoke('power.set',enabled=True);h.step(1.5);return h
def load(h,slot=0,kind='sample',mass=2e-6,template='oem_sulfanilic_acid',**extra):
 a=copy.deepcopy(r.BLANK_EXAMPLE if kind=='blank' else r.SAMPLE_EXAMPLE);a.update(slot_index=slot,kind=kind,mass_kg=0. if kind=='blank' else mass,template_id='' if kind=='blank' else template,**extra);h.invoke('sample.load',**a)
def run(h,slots):h.invoke('sequence.configure',slot_indices=slots);h.invoke('sequence.start');finish(h);return h.invoke('result.read')
def blank(h):load(h,kind='blank');run(h,[0]);return h

def calibrated(plant=None,method=None):
 h=blank(ready(plant,method));masses=[1e-6,2e-6,3e-6]
 for slot,m in enumerate(masses,1):load(h,slot,'standard',m)
 result=run(h,[1,2,3])
 for e in r.MODES[h.method['mode']]:
  obs=[dict(reference_id='oem_sulfanilic_acid',sample_mass_kg=m,peak_area_s=q['areas_s'][e]) for m,q in zip(masses,result['completed_assays'])];h.invoke('calibration.fit',element=e,observations=obs,accept_same_method_basis=True)
 return h

class ElementarProcessTests(unittest.TestCase):
 def reject_atomic(self,h,code,cap,**args):
  before=repr(h.debug_snapshot())
  with self.assertRaisesRegex(r.ActionRejected,code):h.invoke(cap,**args)
  self.assertEqual(before,repr(h.debug_snapshot()))
 def closed(self,h):
  q=h.invoke('product.read');self.assertLess(max(abs(x) for x in q['tracked_elements_kg']['residual'].values()),1e-17);self.assertLess(abs(q['unmeasured_residual_kg']),1e-17);self.assertLess(abs(q['oxygen_residual_mol']),1e-12);self.assertLess(abs(q['thermal_residual_j']),1e-7)
 def test_source_measurements_are_not_calculated_values(self):
  h=ready();q=json.loads(h.invoke('template.read',substance_id='oem_sulfanilic_acid')['template_json']);self.assertEqual(q['element_mass_fractions']['C'],.4168);self.assertIsNone(q['calculated_reference_percent']['C']);self.assertIsNone(q['peak_trace'])
 def test_unknown_template_atomic(self):
  h=ready();self.reject_atomic(h,'UNKNOWN_REFERENCE','template.read',substance_id='not-a-source')
 def test_reported_source_sum_rejected_not_normalized(self):
  for fractions in [{e:.4 for e in r.ELEMENTS},{'C':.5,'H':.5,'N':5e-13,'S':0.}]:
   lib=r._library();lib['compounds']['oem_sulfanilic_acid']['element_mass_fractions']=fractions;h=ready(library=lib);self.reject_atomic(h,'SOURCE_COMPOSITION_RANGE','sample.load',**r.SAMPLE_EXAMPLE)
 def test_source_unknown_ack_required(self):
  h=ready();self.reject_atomic(h,'UNKNOWN_CONDITIONS','sample.load',**dict(r.SAMPLE_EXAMPLE,accept_unknown_conditions=False))
 def test_reported_values_cannot_be_host_overwritten(self):
  h=ready();a=dict(r.SAMPLE_EXAMPLE,assumed_unreported_fractions=dict(r.SAMPLE_EXAMPLE['assumed_unreported_fractions'],C=.8),accept_assumed_missing_elements=True);self.reject_atomic(h,'REPORTED_VALUE_OVERRIDE','sample.load',**a)
 def test_source_basis_mismatch(self):
  h=ready();self.reject_atomic(h,'SOURCE_BASIS_MISMATCH','sample.load',**dict(r.SAMPLE_EXAMPLE,mass_basis='assumed_dry'))
 def test_organic_mass_limit(self):
  h=ready();self.reject_atomic(h,'SAMPLE_MASS_LIMIT','sample.load',**dict(r.SAMPLE_EXAMPLE,mass_kg=20.01e-6))
 def test_each_absolute_element_capacity(self):
  for j,e in enumerate(r.ELEMENTS):
   with self.subTest(element=e):
    lib=r._library();lib['compounds']['oem_sulfanilic_acid']['element_mass_fractions']={z:1. if z==e else 0. for z in r.ELEMENTS};h=ready(library=lib);self.reject_atomic(h,'ELEMENT_MASS_LIMIT','sample.load',**dict(r.SAMPLE_EXAMPLE,sample_class='inorganic',mass_kg=float(r.ELEMENT_LIMITS[j]*1.01)))
 def test_unknown_s_capacity_uses_remainder_bound(self):
  h=ready();self.reject_atomic(h,'ELEMENT_MASS_LIMIT','sample.load',**dict(r.SAMPLE_EXAMPLE,template_id='ep0459505_compound_01',sample_class='inorganic',mass_kg=1.4e-3))
 def test_chn_missing_s_retained_as_unknown(self):
  h=blank(ready(method={'mode':'CHN'}));load(h,template='ep0459505_compound_01');q=run(h,[0])['completed_assays'][0];self.assertIsNone(q['areas_s']['S']);self.assertIsNone(q['fractions']['S']);self.assertEqual(q['source_fraction_evidence']['S'],'unreported_unknown_not_zero');self.assertGreater(h._ledger()['unmeasured_retained_kg'],0);self.closed(h)
 def test_chns_missing_s_rejected_before_consumption(self):
  h=blank(ready());load(h,template='ep0459505_compound_01');h.invoke('sequence.configure',slot_indices=[0]);self.reject_atomic(h,'UNREPORTED_MODE_ELEMENT','sequence.start');self.assertIn(0,h.samples)
 def test_explicit_missing_s_assumption_has_evidence(self):
  h=blank(ready());a=dict(r.SAMPLE_EXAMPLE,template_id='ep0459505_compound_01',assumed_unreported_fractions=dict(r.SAMPLE_EXAMPLE['assumed_unreported_fractions'],S=.01));self.reject_atomic(h,'MISSING_ELEMENT_ACK_REQUIRED','sample.load',**a);a['accept_assumed_missing_elements']=True;h.invoke('sample.load',**a);q=run(h,[0])['completed_assays'][0];self.assertIn('host_assumption',q['source_fraction_evidence']['S'])
 def test_zero_sample_mass_not_blank(self):
  h=ready();self.reject_atomic(h,'SAMPLE_MASS_LIMIT','sample.load',**dict(r.SAMPLE_EXAMPLE,mass_kg=0.))
 def test_blank_cannot_hide_sample_mass(self):
  h=ready();self.reject_atomic(h,'BLANK_COMPOSITION','sample.load',**dict(r.BLANK_EXAMPLE,mass_kg=1e-6))
 def test_top_loading_needs_no_front_door(self):
  h=ready();self.assertFalse(h.opened);load(h);self.assertIn(0,h.samples)
 def test_duplicate_slot_rejects_without_inventory_change(self):
  h=ready();load(h);self.reject_atomic(h,'SLOT_OCCUPIED','sample.load',**r.SAMPLE_EXAMPLE)
 def test_tray_unload_preserves_all_mass(self):
  h=ready();load(h);q=h.invoke('sample.unload',slot_index=0,recipient_id='rack');self.assertEqual(q['mass_kg'],2e-6);self.assertFalse(h.samples);self.closed(h)
 def test_thermal_exact_first_order_and_net_heat(self):
  h=ready();h.invoke('power.set',enabled=False);old=h.temperature.copy();h.step(.17);expected=298.15+(old-298.15)*math.exp(-5*.17);np.testing.assert_allclose(h.temperature,expected,rtol=1e-14);self.closed(h)
 def test_furnace_target_does_not_teleport_temperature(self):
  h=ready();old=h.temperature.copy();h.invoke('furnace.configure',combustion_k=1473.15,reduction_k=1200.);np.testing.assert_array_equal(h.temperature,old);self.assertFalse(h._ready());h.step(.1);self.assertLess(h.temperature[0],1473.15)
 def test_tin_flash_is_not_allowed_furnace_setting(self):
  h=ready();self.reject_atomic(h,'INVALID_ARGUMENT','furnace.configure',combustion_k=2073.15,reduction_k=1123.15)
 def test_room_temperature_cannot_run_combustion(self):
  h=ready();h.invoke('furnace.configure',combustion_k=298.15,reduction_k=298.15);h.step(2);load(h,kind='blank');h.invoke('sequence.configure',slot_indices=[0]);self.reject_atomic(h,'REACTION_TEMPERATURE_LIMIT','sequence.start')
 def test_hot_powered_maintenance_rejected(self):
  h=ready();self.reject_atomic(h,'COLD_SERVICE_REQUIRED','furnace.extend',extension_m=.12)
 def test_cold_slide_requires_door_and_return_before_closing(self):
  h=ready();h.invoke('power.set',enabled=False);h.step(2);self.reject_atomic(h,'COLD_SERVICE_REQUIRED','furnace.extend',extension_m=.12);h.invoke('cover.set',opened=True);h.invoke('furnace.extend',extension_m=.12);self.reject_atomic(h,'RETRACT_FURNACE_REQUIRED','cover.set',opened=False);self.reject_atomic(h,'POWER_INTERLOCK','power.set',enabled=True);h.invoke('furnace.extend',extension_m=0.);h.invoke('cover.set',opened=False)
 def test_unique_queue_consumes_once(self):
  h=blank(ready());load(h);self.reject_atomic(h,'DUPLICATE_SLOT','sequence.configure',slot_indices=[0,0]);run(h,[0]);self.assertNotIn(0,h.samples);self.reject_atomic(h,'EMPTY_SLOT','sequence.start')
 def test_empty_and_unblanked_queue_atomic(self):
  h=ready();self.reject_atomic(h,'EMPTY_SLOT','sequence.start');load(h);h.invoke('sequence.configure',slot_indices=[0]);self.reject_atomic(h,'MATCHED_BLANK_REQUIRED','sequence.start')
 def test_blank_first_with_sample_in_same_queue(self):
  h=ready();load(h,kind='blank');load(h,slot=4);q=run(h,[0,4]);self.assertEqual([x['kind'] for x in q['completed_assays']],['blank','sample']);self.assertTrue(h._blank_valid());self.closed(h)
 def test_sample_before_first_blank_rejected(self):
  h=ready();load(h,slot=0);load(h,slot=1,kind='blank');h.invoke('sequence.configure',slot_indices=[0,1]);self.reject_atomic(h,'MATCHED_BLANK_REQUIRED','sequence.start')
 def test_finite_carousel_and_ball_transfer(self):
  h=blank(ready());load(h,slot=40);h.invoke('sequence.configure',slot_indices=[40]);h.invoke('sequence.start');h.step(.01);self.assertGreater(h.carousel,0);self.assertLess(h.carousel,math.pi);self.assertIn(40,h.samples)
  while h.stage!='ball_open':h.step(max(1e-8,h._due()))
  h.step(.005);self.assertGreater(h.ball,0);self.assertLess(h.ball,math.pi/2);self.assertIn(40,h.samples);h.step(.02);self.assertNotIn(40,h.samples);self.assertIsNotNone(h._current)
 def test_apt_order_and_nonoverlapping_assigned_gates(self):
  h=blank(ready());load(h);q=run(h,[0])['completed_assays'][0];groups=[]
  for p in q['chromatogram']:
   if p['element']!='baseline' and (not groups or groups[-1]!=p['element']):groups.append(p['element'])
  self.assertEqual(groups,['N','C','H','S']);points=q['chromatogram'];self.assertTrue(all(a['time_s']<=b['sample_window_s'][0]+1e-12 for a,b in zip(points,points[1:])))
 def test_peak_area_independently_integrates_samples(self):
  h=blank(ready());load(h);q=run(h,[0])['completed_assays'][0]
  for e in r.ELEMENTS:self.assertAlmostEqual(q['areas_s'][e],sum(p['corrected_signal']*(p['sample_window_s'][1]-p['sample_window_s'][0]) for p in q['chromatogram'] if p['element']==e),places=14)
 def test_baseline_offset_does_not_change_corrected_areas(self):
  results=[]
  for baseline in [.01,.7]:
   h=blank(ready(plant={'tcd_baseline':baseline}));load(h);results.append(run(h,[0])['completed_assays'][0]['areas_s'])
  np.testing.assert_allclose(list(results[0].values()),list(results[1].values()),atol=1e-14,rtol=0)
 def test_finite_conversion_and_oxygen_stoichiometry(self):
  h=blank(ready(method={'combustion_s':.001}));load(h);q=run(h,[0]);led=h._ledger();inputm=np.array([h.templates['oem_sulfanilic_acid']['element_mass_fractions'][e]*2e-6 for e in r.ELEMENTS]);converted=inputm*(-math.expm1(-.12));self.assertAlmostEqual(led['oxygen_consumed_mol'],float(converted@r.O2_PER_ELEMENT_KG),places=15);np.testing.assert_allclose(list(led['tracked_elements_kg']['retained_residue'].values()),inputm-converted,atol=1e-20);self.closed(h)
 def test_oxygen_limited_conversion_preserves_unconverted_mass(self):
  h=blank(ready(method={'oxygen_dose_mol':1e-9}));load(h);run(h,[0]);q=h._ledger();self.assertLessEqual(q['oxygen_consumed_mol'],1.000001e-9);self.assertGreater(sum(q['tracked_elements_kg']['retained_residue'].values()),1e-6);self.closed(h)
 def test_oxygen_supply_checked_for_entire_queue(self):
  h=ready(plant={'oxygen_inventory_mol':.0005});load(h,kind='blank');load(h,slot=1,kind='blank');h.invoke('sequence.configure',slot_indices=[0,1]);self.reject_atomic(h,'OXYGEN_SUPPLY_LIMIT','sequence.start')
 def test_carrier_and_heated_line_interlocks(self):
  for parameter in ['carrier_ready','heated_line_ready']:
   h=ready(plant={parameter:False});load(h,kind='blank');h.invoke('sequence.configure',slot_indices=[0]);self.reject_atomic(h,'GAS_PATH_NOT_READY','sequence.start')
 def test_prior_peak_tails_remain_real_inventory(self):
  h=blank(ready(method={'baseline_threshold':.01}));load(h);run(h,[0]);q=h._ledger();self.assertGreater(sum(q['tracked_elements_kg']['tails'].values()),0);old=sum(q['tracked_elements_kg']['exhaust'].values());h.step(.1);self.assertGreater(sum(h._ledger()['tracked_elements_kg']['exhaust'].values()),old);self.closed(h)
 def test_unselected_elements_have_explicit_mass_account(self):
  h=blank(ready(method={'mode':'CN'}));load(h);q=run(h,[0])['completed_assays'][0];self.assertIsNone(q['areas_s']['H']);self.assertIsNone(q['areas_s']['S']);self.assertGreater(h._ledger()['tracked_elements_kg']['unselected_exhaust']['S'],0);self.closed(h)
 def test_apt_timeout_keeps_partial_observations_and_mass(self):
  h=blank(ready(method={'maximum_release_s':.03}));load(h);run(h,[0]);self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'APT_BASELINE_TIMEOUT');self.assertGreater(len(h._active['chromatogram']),0);self.assertIn('failed',h._active['status']);self.assertFalse(h._result()['complete']);self.closed(h)
 def test_cancel_after_transfer_quarantines_and_cold_recovers(self):
  h=blank(ready());load(h);h.invoke('sequence.configure',slot_indices=[0]);h.invoke('sequence.start')
  while h.stage!='combustion':h.step(max(1e-8,h._due()))
  h.step(.05);h.invoke('sequence.stop');self.assertEqual(h.phase,'cancelled');self.assertEqual(h.ball,0);self.assertNotIn(0,h.samples);self.reject_atomic(h,'RECOVER_CANCELLED','sequence.start');self.reject_atomic(h,'COLD_SERVICE','sample.unload',slot_index=0,recipient_id='waste');h.invoke('power.set',enabled=False);h.step(2);h.invoke('cover.set',opened=True);h.invoke('furnace.extend',extension_m=.12);amount=h.invoke('sample.unload',slot_index=0,recipient_id='waste')['mass_kg'];self.assertGreater(amount,0);self.assertLessEqual(amount,2e-6);self.assertIsNone(h._current);self.closed(h)
 def test_door_interrupts_without_resurrecting_sample(self):
  h=blank(ready());load(h);h.invoke('sequence.configure',slot_indices=[0]);h.invoke('sequence.start')
  while h.stage!='release':h.step(max(1e-8,h._due()))
  h.invoke('cover.set',opened=True);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.warning,'MAINTENANCE_DOOR_OPEN');old=h.temperature.copy();h.step(.2);self.assertTrue(np.all(h.temperature<old));self.closed(h)
 def test_uncalibrated_sample_has_areas_but_no_fake_fractions(self):
  h=blank(ready());load(h);q=run(h,[0])['completed_assays'][0];self.assertTrue(all(v>0 for v in q['areas_s'].values()));self.assertEqual(q['fractions'],r._nulls())
 def test_observed_three_standard_fit_recovers_unknown(self):
  h=calibrated();self.assertTrue(h.invoke('calibration.read')['ready']);load(h);q=run(h,[0])['completed_assays'][0]
  for e in r.ELEMENTS:self.assertAlmostEqual(q['fractions'][e],h.templates['oem_sulfanilic_acid']['element_mass_fractions'][e],delta=2e-6)
 def test_calibration_uses_observed_area_not_hidden_truth(self):
  h=calibrated();h._calibration['C']['slope_s_kg']*=2;h._calibration['C']['mass_range_kg'][0]=0.;load(h);q=run(h,[0])['completed_assays'][0];self.assertAlmostEqual(q['fractions']['C'],.4168/2,delta=2e-6)
 def test_calibration_rejects_degenerate_nonfinite_and_negative_slope(self):
  h=blank(ready());a=copy.deepcopy(r.CALIBRATION_EXAMPLE);a['observations']=[a['observations'][0]]*3;self.reject_atomic(h,'DEGENERATE_CALIBRATION','calibration.fit',**a)
  a=copy.deepcopy(r.CALIBRATION_EXAMPLE)
  for p in a['observations']:p['peak_area_s']=-p['peak_area_s']
  self.reject_atomic(h,'CALIBRATION_RANGE','calibration.fit',**a);a['observations'][0]['peak_area_s']=float('inf');self.reject_atomic(h,'INVALID_ARGUMENT','calibration.fit',**a)
 def test_calibration_overflow_rejects_atomically(self):
  h=blank(ready());a=copy.deepcopy(r.CALIBRATION_EXAMPLE)
  for p,v in zip(a['observations'],[-1e308,0,1e308]):p['peak_area_s']=v
  self.reject_atomic(h,'CALIBRATION_RANGE','calibration.fit',**a)
 def test_calibration_missing_source_channel_cannot_fit(self):
  h=blank(ready());a=copy.deepcopy(r.CALIBRATION_EXAMPLE);a['element']='S'
  for p in a['observations']:p['reference_id']='ep0459505_compound_01'
  self.reject_atomic(h,'MEASURED_REFERENCE_REQUIRED','calibration.fit',**a)
 def test_calibration_outside_range_returns_null(self):
  h=calibrated();load(h,mass=5e-6);q=run(h,[0])['completed_assays'][0];self.assertIsNone(q['fractions']['C']);self.assertEqual(q['fraction_quality']['C'],'outside_calibrated_element_mass_range')
 def test_method_invalidates_blank_and_calibration_retains_old_data(self):
  h=calibrated();old=h._result();h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,combustion_s=.2));self.assertFalse(h._blank_valid());self.assertFalse(h._calibration_ready());self.assertEqual(old,h._result());load(h);h.invoke('sequence.configure',slot_indices=[0]);self.reject_atomic(h,'MATCHED_BLANK_REQUIRED','sequence.start')
 def test_sequence_results_reset_only_on_next_start(self):
  h=calibrated();self.assertEqual(len(h.completed_assays),3);load(h);h.invoke('sequence.configure',slot_indices=[0]);self.assertEqual(len(h.completed_assays),3);h.invoke('sequence.start');self.assertEqual(h.completed_assays,[]);finish(h);self.assertEqual(len(h.completed_assays),1)
 def test_partitioned_steps_preserve_observations_and_mass(self):
  a=blank(ready(plant={'seed':11,'tcd_noise_sd':1e-7}));b=blank(ready(plant={'seed':11,'tcd_noise_sd':1e-7}))
  for h in [a,b]:load(h);h.invoke('sequence.configure',slot_indices=[0]);h.invoke('sequence.start')
  finish(a,.25);finish(b,.007);qa=a.completed_assays[0];qb=b.completed_assays[0];self.assertEqual(len(qa['chromatogram']),len(qb['chromatogram']));np.testing.assert_allclose([p['signal'] for p in qa['chromatogram']],[p['signal'] for p in qb['chromatogram']],rtol=1e-9,atol=1e-9);self.closed(a);self.closed(b)
 def test_query_purity_and_export_roundtrip(self):
  h=blank(ready());load(h);run(h,[0]);before=repr(h.debug_snapshot())
  for cap,args in [('process.measure',{}),('result.read',{}),('chromatogram.read',{}),('blank.read',{}),('calibration.read',{}),('product.read',{}),('template.read',{'substance_id':'oem_sulfanilic_acid'}),('result.export',{'format':'json'}),('result.export',{'format':'csv'})]:h.invoke(cap,**args)
  self.assertEqual(before,repr(h.debug_snapshot()));self.assertEqual(json.loads(h.invoke('result.export',format='json')['content']),h._result());self.assertIn('mass_fraction',h.invoke('result.export',format='csv')['content'])
 def test_public_access_filters_sample_truth_and_host_commands(self):
  h=ready();load(h);public=h.session();desc=public.describe(instance_id=h.instance_id);self.assertNotIn('truth.ledger',desc['state_slots']);self.assertNotIn('sample.load',desc['capabilities']);reply=public.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(reply['status'],'rejected');self.assertEqual(reply['error']['code'],'ACCESS_DENIED');self.assertNotIn('oem_sulfanilic_acid',json.dumps(public.read(instance_id=h.instance_id)))
 def test_host_role_and_read_write_binding(self):
  h=r.ElementarVarioelRuntime();h.register_principal('host','evaluator');session=h.session('host');reply=session.invoke(instance_id=h.instance_id,capability_id='plant.configure',arguments=r.PLANT_EXAMPLE,request_id='plant');self.assertEqual(reply['status'],'succeeded');self.assertIn('truth.ledger',session.read(instance_id=h.instance_id)['observations']);self.assertEqual(session.write(instance_id=h.instance_id,state_id='power.enabled',value=True,request_id='on')['status'],'succeeded')
 def test_request_replay_and_conflict_are_atomic(self):
  h=ready();s=h.session();args=dict(instance_id=h.instance_id,capability_id='method.configure',arguments=r.METHOD_EXAMPLE,request_id='method');first=s.invoke(**args);revision=h.revision;self.assertEqual(s.invoke(**args),first);self.assertEqual(h.revision,revision);args['arguments']=dict(r.METHOD_EXAMPLE,mode='CHN');self.assertEqual(s.invoke(**args)['error']['code'],'REQUEST_CONFLICT');self.assertEqual(h.revision,revision)
 def test_history_poll_and_cancel_old_do_not_touch_new(self):
  h=blank(ready());s=h.session();load(h);h.invoke('sequence.configure',slot_indices=[0]);start=s.invoke(instance_id=h.instance_id,capability_id='sequence.start',arguments={},request_id='first');op=start['operation_id'];finish(h);old=s.poll(op);load(h,slot=4);h.invoke('sequence.configure',slot_indices=[4]);new=s.invoke(instance_id=h.instance_id,capability_id='sequence.start',arguments={},request_id='second');self.assertEqual(s.poll(op),old);s.cancel(op,'cancel_old');self.assertEqual(h.phase,'running');self.assertEqual(h.operation_id,new['operation_id']);self.assertEqual(s.poll(op),old)
 def test_cancelled_history_retains_partial_data_and_ownership(self):
  h=blank(ready());h.register_principal('other');s=h.session();load(h);h.invoke('sequence.configure',slot_indices=[0]);op=s.invoke(instance_id=h.instance_id,capability_id='sequence.start',arguments={},request_id='start')['operation_id'];h.step(.2);s.cancel(op,'cancel');old=s.poll(op);self.assertEqual(old['status'],'cancelled');self.assertGreater(len(old['result']['active_assay']['chromatogram']),0)
  with self.assertRaisesRegex(r.ActionRejected,'ACCESS_DENIED'):h.session('other').poll(op)
 def test_actual_detector_windows_survive_later_idle_time(self):
  h=calibrated();load(h);run(h,[0]);before=h.session().read(instance_id=h.instance_id,state_ids=['detector.tcd_signal','result.c_fraction']);h.step(1);after=h.session().read(instance_id=h.instance_id,state_ids=['detector.tcd_signal','result.c_fraction']);self.assertEqual(before['observations'],after['observations']);self.assertLess(after['observations']['result.c_fraction']['sample_window_s'][0],after['observations']['result.c_fraction']['sample_window_s'][1])
 def test_independent_instances_do_not_share_consumption(self):
  a=blank(ready(instance_id='a'));b=blank(ready(instance_id='b'));load(a);load(b);before=repr(b.debug_snapshot());run(a,[0]);self.assertEqual(before,repr(b.debug_snapshot()));self.assertIn(0,b.samples)
 def test_invalid_step_never_changes_state(self):
  h=ready();before=repr(h.debug_snapshot())
  for dt in [-1,float('nan'),float('inf'),True]:
   with self.assertRaises(r.ActionRejected):h.step(dt)
   self.assertEqual(before,repr(h.debug_snapshot()))
  t=h.time_s;h.step(1e-10);self.assertGreater(h.time_s,t)
 def test_unsupported_options_and_metadata_query_purity(self):
  h=ready()
  for cap in r.UNSUPPORTED:self.reject_atomic(h,'UNSUPPORTED',cap)
  meta=r.export_elementar_varioel_contract_metadata();self.assertEqual(len(meta['capabilities']),23);self.assertEqual(len(meta['state_slots']),31)
  for k,c in meta['capabilities'].items():
   if c['kind']=='query' or k in r.UNSUPPORTED:self.assertEqual(c['state_effects'],[])
  self.assertEqual(meta['capabilities']['sequence.start']['execution']['mode'],'asynchronous')
 def test_schema_rejects_unknown_fields_and_nonfinite(self):
  h=ready();self.reject_atomic(h,'INVALID_ARGUMENT','method.configure',**dict(r.METHOD_EXAMPLE,extra=3));self.reject_atomic(h,'INVALID_ARGUMENT','sample.load',**dict(r.SAMPLE_EXAMPLE,mass_kg=float('nan')))
 def test_event_budget_rejected_before_sample_consumption(self):
  h=ready(plant={'detector_period_s':.001},method={'maximum_release_s':3600.});load(h,kind='blank');h.invoke('sequence.configure',slot_indices=[0]);self.reject_atomic(h,'EVENT_RESOURCE_LIMIT','sequence.start')

if __name__=='__main__':unittest.main()
