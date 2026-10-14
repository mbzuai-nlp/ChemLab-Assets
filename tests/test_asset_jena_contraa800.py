"""Behavioral regressions for contrAA800D: observed AAS, reference provenance, finite mechanisms and ACL."""
import copy,importlib.util,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import jena_contraa800 as rt
SPEC=importlib.util.spec_from_file_location('contraa_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(tool)

def make(mode='flame',noise=0.,**plant):
 h=rt.JenaContraa800Runtime();h.invoke('plant.configure',**dict(rt.PLANT_EXAMPLE,detector_noise_sd=noise,**plant));h.invoke('method.configure',**(rt.METHOD_EXAMPLE if mode=='flame' else rt.FURNACE_METHOD_EXAMPLE));h.invoke('power.set',enabled=True);h.step(.3)
 if mode=='flame':h.invoke('flame.ignite');h.step(.1)
 else:h.invoke('cover.set',opened=True)
 return h

def finish(h,dt=.02,limit=30):
 for _ in range(math.ceil(limit/dt)):
  if h.phase!='running':break
  h.step(dt)
 if h.phase!='succeeded':raise AssertionError((h.phase,h.stage,h.warning))
 return h.invoke('result.read')

def load(h,s):
 if h.sample:h.invoke('sample.unload',recipient_id='external')
 h.invoke('sample.load',**s)

def blank(h):
 load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start');return finish(h)

def calibrate(h):
 blank(h);obs=[]
 for i in [1,2,3]:
  ref=f'aj_wastewater_std{i}_cu';load(h,dict(rt.STANDARD_EXAMPLE,template_id=ref));h.invoke('measurement.start');r=finish(h);obs.append(dict(reference_id=ref,dilution_factor=1.,injection_volume_m3=h.method['injection_volume_m3'],integrated_absorbance_s=r['assays'][0]['integrated_absorbance_s']))
 result=h.invoke('calibration.fit',curve_id='observed_cu',observations=obs,accept_same_response_conditions=True);h.invoke('calibration.select',curve_id='observed_cu');return obs,result

class ContraaTests(unittest.TestCase):
 def atomic_reject(self,h,cap,code=None,**args):
  before=json.dumps(h.debug_snapshot(),sort_keys=True,default=lambda x:x.tolist() if hasattr(x,'tolist') else repr(x))
  with self.assertRaises(rt.ActionRejected) as cm:h.invoke(cap,**args)
  if code:self.assertEqual(str(cm.exception),code)
  after=json.dumps(h.debug_snapshot(),sort_keys=True,default=lambda x:x.tolist() if hasattr(x,'tolist') else repr(x));self.assertEqual(before,after)
 def balance(self,h):
  l=h.invoke('product.read');self.assertAlmostEqual(l['element_mass_kg']['residual'],0.,delta=1e-18);self.assertAlmostEqual(l['liquid_volume_m3']['residual'],0.,delta=1e-15)
  for gas in ['fuel_mol','oxidant_mol','argon_mol']:self.assertAlmostEqual(l[gas]['residual'],0.,delta=1e-12)
  self.assertAlmostEqual(l['thermal_residual_j'],0.,delta=1e-8)
 def test_identity_and_real_default(self):
  h=rt.JenaContraa800Runtime();self.assertEqual(rt.CATALOG_ID,'CHEM-081');self.assertAlmostEqual(h.templates[rt.SAMPLE_EXAMPLE['template_id']]['concentration_kg_m3'],.000083,places=15);self.assertAlmostEqual(h.lines['cu_324754']['wavelength_m'],324.754e-9,places=18)
 def test_queries_and_describe_do_not_mutate_rng_or_clock(self):
  h=make();blank(h);before=h.debug_snapshot();h.describe();h.read();h.invoke('spectrum.read');h.invoke('result.read');h.invoke('records.list');self.assertEqual(json.dumps(before,sort_keys=True),json.dumps(h.debug_snapshot(),sort_keys=True))
 def test_step_zero_is_pure(self):
  h=make();before=h.debug_snapshot();h.step(0);self.assertEqual(before,h.debug_snapshot())
 def test_step_rejects_nonfinite_bool_negative(self):
  h=make()
  for v in [True,-1.,float('nan'),float('inf')]:
   with self.assertRaises(rt.ActionRejected):h.step(v)
 def test_host_initialization_cannot_overwrite(self):self.atomic_reject(make(),'plant.configure','INITIALIZATION_ONLY',**rt.PLANT_EXAMPLE)
 def test_assumed_response_ack_required(self):self.atomic_reject(rt.JenaContraa800Runtime(),'plant.configure','EVIDENCE_ACK_REQUIRED',**dict(rt.PLANT_EXAMPLE,accept_assumed_response=False))
 def test_sample_reference_and_dilution_not_mutate_library(self):
  h=make();before=copy.deepcopy(h.library);h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,dilution_factor=10));self.assertAlmostEqual(h.sample['concentration'],.0000083,places=17);self.assertEqual(h.library,before)
 def test_censored_or_solid_concentration_rejected(self):
  h=make();ids=[k for k,v in h.templates.items() if v.get('concentration_kg_m3') is None]
  self.assertGreater(len(ids),0)
  for key in ids[:4]:self.atomic_reject(h,'sample.load','LIQUID_CONCENTRATION_REQUIRED',**dict(rt.SAMPLE_EXAMPLE,template_id=key))
 def test_line_only_query_no_fake_concentration(self):
  h=make();r=h.invoke('template.read',substance_id='cu_324754');self.assertEqual(json.loads(r['template_json'])['element'],'Cu');self.atomic_reject(h,'sample.load','LIQUID_CONCENTRATION_REQUIRED',**dict(rt.SAMPLE_EXAMPLE,template_id='cu_324754'))
 def test_unknown_conditions_requires_ack(self):self.atomic_reject(make(),'sample.load','EVIDENCE_ACK_REQUIRED',**dict(rt.SAMPLE_EXAMPLE,accept_unknown_conditions=False))
 def test_unknown_reference_error_does_not_reveal_state(self):self.atomic_reject(make(),'sample.load','UNKNOWN_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,template_id='unknown'))
 def test_replacement_requires_unload(self):
  h=make();h.invoke('sample.load',**rt.SAMPLE_EXAMPLE);self.atomic_reject(h,'sample.load','UNLOAD_REQUIRED',**rt.SAMPLE_EXAMPLE)
 def test_donor_volume_return_only_remaining(self):
  h=make();blank(h);load(h,rt.STANDARD_EXAMPLE);h.invoke('measurement.start');finish(h);initial=rt.STANDARD_EXAMPLE['volume_m3'];r=h.invoke('sample.unload',recipient_id='host');self.assertAlmostEqual(r['volume_m3'],initial-h.method['injection_volume_m3']);self.balance(h)
 def test_method_requires_matching_element_line(self):
  h=make();h.invoke('flame.extinguish');self.atomic_reject(h,'method.configure','ELEMENT_LINE_MISMATCH',**dict(rt.METHOD_EXAMPLE,element='Pb'))
 def test_unknown_line_rejected(self):
  h=make();h.invoke('flame.extinguish');self.atomic_reject(h,'method.configure','UNKNOWN_LINE',**dict(rt.METHOD_EXAMPLE,line_id='missing'))
 def test_documented_optical_bounds_and_not_sigma(self):
  h=make();h.invoke('flame.extinguish')
  for wavelength in [184e-9,901e-9]:self.atomic_reject(h,'method.configure','INVALID_ARGUMENT',**dict(rt.METHOD_EXAMPLE,wavelength_m=wavelength))
  self.assertNotEqual(rt.PLANT_EXAMPLE['line_sigma_m'],4e-13)
 def test_large_line_detuning_is_explicit_error(self):
  h=make();h.invoke('flame.extinguish');self.atomic_reject(h,'method.configure','LINE_WINDOW_MISMATCH',**dict(rt.METHOD_EXAMPLE,wavelength_m=325e-9))
 def test_standard_tube_volume_limit50ul(self):
  h=make('furnace');self.atomic_reject(h,'method.configure','STANDARD_TUBE_VOLUME_LIMIT',**dict(rt.FURNACE_METHOD_EXAMPLE,injection_volume_m3=51e-9))
 def test_one_ul_minimum_dosing(self):
  h=make('furnace');self.atomic_reject(h,'method.configure','INVALID_ARGUMENT',**dict(rt.FURNACE_METHOD_EXAMPLE,injection_volume_m3=.5e-9))
 def test_furnace_temperature_and_ramp_bounds(self):
  h=make('furnace')
  for key,value in [('temperature_k',3274.),('ramp_k_s',3001.)]:
   program=copy.deepcopy(rt.FURNACE_PROGRAM_EXAMPLE);program[2][key]=value;self.atomic_reject(h,'method.configure','INVALID_ARGUMENT',**dict(rt.FURNACE_METHOD_EXAMPLE,furnace_program=program))
 def test_furnace_stage_order_enforced(self):
  h=make('furnace');p=list(reversed(rt.FURNACE_PROGRAM_EXAMPLE));self.atomic_reject(h,'method.configure','FURNACE_PROGRAM_ORDER',**dict(rt.FURNACE_METHOD_EXAMPLE,furnace_program=p))
 def test_atomizer_and_burner_targets_are_finite(self):
  h=rt.JenaContraa800Runtime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);h.invoke('method.configure',**rt.METHOD_EXAMPLE);h.invoke('power.set',enabled=True);self.assertEqual(h.atomizer,0.);h.step(.01);self.assertGreater(h.atomizer,0.);self.assertLess(h.atomizer,math.pi/2)
  h.invoke('burner.configure',height_m=.016,angle_rad=math.pi/2);self.assertEqual(h.burner_height,.004);h.step(.01);self.assertGreater(h.burner_height,.004);self.assertLess(h.burner_height,.016)
 def test_ignition_requires_position_and_gas(self):
  h=rt.JenaContraa800Runtime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);h.invoke('method.configure',**rt.METHOD_EXAMPLE);h.invoke('power.set',enabled=True);self.atomic_reject(h,'flame.ignite','IGNITION_INTERLOCK')
 def test_ignition_delay_not_immediate_flame(self):
  h=make();h.invoke('flame.extinguish');h.invoke('flame.ignite');self.assertFalse(h.flame);h.step(.01);self.assertFalse(h.flame);h.step(.05);self.assertTrue(h.flame)
 def test_flame_requires_closed_door(self):
  h=make();self.assertEqual(h.read()['cover.lift_m'],0.);h.invoke('flame.extinguish');h.invoke('cover.set',opened=True);self.assertEqual(h.read()['cover.lift_m'],.245);self.assertNotIn('cover.angle_rad',h.read());self.atomic_reject(h,'flame.ignite','IGNITION_INTERLOCK')
  slot=rt.export_jena_contraa800_contract_metadata()['state_slots']['cover.lift_m'];self.assertEqual(slot['unit'],'m');self.assertEqual(slot['value_schema']['maximum'],.245)
 def test_furnace_sampling_requires_open_sample_door(self):
  h=make('furnace');self.assertEqual(h.read()['cover.lift_m'],.245);h.invoke('cover.set',opened=False);self.assertEqual(h.read()['cover.lift_m'],0.);h.invoke('sample.load',**rt.BLANK_EXAMPLE);self.atomic_reject(h,'measurement.start','SAMPLE_DOOR_MODE_INTERLOCK')
 def test_furnace_and_lamp_maintenance_require_power_off(self):
  h=make('furnace')
  for cap in ['furnace.open','lamp_door.set']:self.atomic_reject(h,cap,'MAINTENANCE_INTERLOCK',opened=True)
 def test_live_measurement_cannot_mutate_method(self):
  h=make();blank(h);load(h,rt.STANDARD_EXAMPLE);h.invoke('measurement.start');self.atomic_reject(h,'method.configure','BUSY',**rt.METHOD_EXAMPLE)
 def test_missing_blank_and_calibration_reject_before_consuming(self):
  h=make();load(h,rt.SAMPLE_EXAMPLE);self.atomic_reject(h,'measurement.start','MATCHED_BLANK_REQUIRED');blank(h);load(h,rt.SAMPLE_EXAMPLE);self.atomic_reject(h,'measurement.start','MATCHED_CALIBRATION_REQUIRED')
 def test_small_donor_volume_guard_is_atomic(self):
  h=make();blank(h);load(h,dict(rt.STANDARD_EXAMPLE,volume_m3=1e-9));self.atomic_reject(h,'measurement.start','SAMPLE_VOLUME_LIMIT')
 def test_cancelled_blank_does_not_become_valid(self):
  h=make();load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start');h.step(.04);h.invoke('measurement.stop');self.assertFalse(h._blank_valid());self.assertEqual(h.phase,'cancelled')
 def test_flame_mass_volume_gas_ledger(self):
  h=make();blank(h);load(h,rt.STANDARD_EXAMPLE);h.invoke('measurement.start');finish(h);self.balance(h);self.assertGreater(h._waste,0);self.assertGreater(h._exhaust,0);self.assertGreater(h._gases['fuel']['consumed'],0)
 def test_graphite_furnace_mass_and_heat_ledger(self):
  h=make('furnace');blank(h);load(h,rt.STANDARD_EXAMPLE);h.invoke('measurement.start');finish(h);self.balance(h);self.assertGreater(h.heat,0);self.assertGreater(h._gases['argon']['consumed'],0);self.assertEqual(h._gases['fuel']['consumed'],0)
 def test_furnace_ramp_never_jumps_to_setpoint(self):
  h=make('furnace');load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start')
  while h.stage!='heat_atomize':h.step(.001)
  t=h.temperature;h.step(.005);self.assertLessEqual(h.temperature-t,15.00001);self.assertLess(h.temperature,h.target)
 def test_108_sampler_angles_physically_reach_four_rings(self):
  radii=[.058]*28+[.080]*32+[.102]*40+[.030]*8
  for i,radius in enumerate(radii):
   arm,tray=rt.sampler_pick_angles(i);point=np.array([.130*math.sin(arm),-.130*math.cos(arm)]);self.assertAlmostEqual(np.linalg.norm(point-np.array([0.,-.1125])),radius,places=12);self.assertTrue(0<=tray<2*math.pi)
 def test_sampler_pick_and_injection_depths(self):
  h=make('furnace');load(h,dict(rt.BLANK_EXAMPLE,vial_index=107));h.invoke('measurement.start');seen={}
  while h.phase=='running':
   h.step(.001)
   if h.stage in ['aspirate','dispense']:seen[h.stage]=(h.injector,h.sampler_arm,h.sampler)
  self.assertAlmostEqual(seen['aspirate'][0],.052);self.assertAlmostEqual(seen['dispense'][0],.077);self.assertAlmostEqual(seen['dispense'][1],math.pi);self.assertAlmostEqual(h.sampler_arm,math.pi/2);self.assertEqual(h.injector,0.)
 def test_cancelled_sampler_requires_park_for_door(self):
  h=make('furnace');load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start')
  while h.stage!='aspirate':h.step(.005)
  h.invoke('measurement.stop');self.atomic_reject(h,'cover.set','SAMPLER_PARK_REQUIRED',opened=False);h.invoke('sampler.park');self.assertTrue(h._parking);h.step(1);self.assertFalse(h._parking);h.invoke('cover.set',opened=False)
 def test_cancel_does_not_return_aspirated_liquid(self):
  h=make('furnace');load(h,rt.STANDARD_EXAMPLE);h._blanks[h._key()]={'baseline':0.};h.invoke('measurement.start')
  while h.stage!='aspirate':h.step(.001)
  h.step(.03);v=h.sample['volume'];h.invoke('measurement.stop');self.assertLess(v,rt.STANDARD_EXAMPLE['volume_m3']);self.assertGreater(h._pipette_v,0.);h.step(.1);self.assertEqual(v,h.sample['volume']);self.balance(h)
 def test_cold_clean_transfers_residue_to_waste(self):
  h=make('furnace');load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start')
  while h.stage!='aspirate':h.step(.001)
  h.step(.03);h.invoke('measurement.stop');h.invoke('power.set',enabled=False);before=h._pipette_v;r=h.invoke('fluidics.clean');self.assertEqual(r['removed_volume_m3'],before);self.assertEqual(h._pipette_v,0.);self.balance(h)
 def test_power_loss_cancels_and_retains_peaks(self):
  h=make();blank(h);load(h,rt.STANDARD_EXAMPLE);h.invoke('measurement.start');h.step(.1);n=len(h._active['points']);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertFalse(h.flame);self.assertEqual(len(h._active['points']),n);self.balance(h)
 def test_fuel_exhaustion_fails_without_fabricated_concentration(self):
  h=make(fuel_inventory_mol=.0004);load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start');h.step(1);self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'GAS_EXHAUSTED');self.assertIsNone(h._concentration);self.balance(h)
 def test_observed_calibration_recovers_blinded_flame_sample(self):
  h=make();obs,fit=calibrate(h);load(h,rt.SAMPLE_EXAMPLE);h.invoke('measurement.start');r=finish(h);self.assertGreater(fit['r_squared'],.999999);self.assertAlmostEqual(r['concentration_kg_m3'],.000083,delta=1e-8)
 def test_observed_calibration_recovers_furnace_sample(self):
  h=make('furnace');calibrate(h);load(h,rt.SAMPLE_EXAMPLE);h.invoke('measurement.start');r=finish(h);self.assertAlmostEqual(r['concentration_kg_m3'],.000083,delta=1e-7)
 def test_independent_centered_least_squares(self):
  h=make();obs,fit=calibrate(h);x=np.array([h.templates[o['reference_id']]['concentration_kg_m3']*o['injection_volume_m3'] for o in obs]);y=np.array([o['integrated_absorbance_s'] for o in obs]);m,b=np.polyfit(x,y,1);self.assertAlmostEqual(fit['slope_s_kg']/m,1,places=12);self.assertAlmostEqual(fit['intercept_s'],b,places=15)
 def test_degenerate_and_negative_slope_calibration_atomic(self):
  h=make();obs,_=calibrate(h)
  self.atomic_reject(h,'calibration.fit','DEGENERATE_CALIBRATION',curve_id='flat',observations=[obs[0]]*3,accept_same_response_conditions=True)
  self.atomic_reject(h,'calibration.fit','CALIBRATION_FIT_INVALID',curve_id='reverse',observations=[dict(o,integrated_absorbance_s=-o['integrated_absorbance_s']) for o in obs],accept_same_response_conditions=True)
 def test_nonfinite_calibration_area_rejected(self):
  h=make();blank(h);a=copy.deepcopy(rt.CALIBRATION_EXAMPLE);a['observations'][0]['integrated_absorbance_s']=float('inf');self.atomic_reject(h,'calibration.fit','INVALID_ARGUMENT',**a)
 def test_curve_duplicate_and_capacity_no_overwrite(self):
  h=make();obs,_=calibrate(h);self.atomic_reject(h,'calibration.fit','CURVE_ID_EXISTS',curve_id='observed_cu',observations=obs,accept_same_response_conditions=True)
  for i in range(rt.CURVE_CAPACITY-1):h.invoke('calibration.fit',curve_id=f'c{i}',observations=obs,accept_same_response_conditions=True)
  self.assertEqual(len(h._curves),rt.CURVE_CAPACITY);self.atomic_reject(h,'calibration.fit','CURVE_STORE_FULL',curve_id='overflow',observations=obs,accept_same_response_conditions=True)
 def test_method_signature_rejects_wrong_background_curve(self):
  h=make();calibrate(h);h.invoke('flame.extinguish');h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,background='off'));self.assertFalse(h._calibration_ready());self.assertFalse(h._blank_valid());self.atomic_reject(h,'calibration.select','CURVE_METHOD_MISMATCH',curve_id='observed_cu')
 def test_response_changed_not_source_lookup(self):
  h=make();calibrate(h);load(h,rt.SAMPLE_EXAMPLE);h.sample['concentration']*=.8;h._registered-=rt.SAMPLE_EXAMPLE['volume_m3']*.000083*.2;h.invoke('measurement.start');r=finish(h);self.assertAlmostEqual(r['concentration_kg_m3'],.000083*.8,delta=1e-8)
 def test_saturation_returns_null_with_qc(self):
  h=make();calibrate(h);load(h,rt.SAMPLE_EXAMPLE);h.sample['background']=20.;h.invoke('measurement.start');r=finish(h);self.assertIsNone(r['concentration_kg_m3']);self.assertIn('SATURATED',r['qc'])
 def test_outside_calibration_not_extrapolated(self):
  h=make();calibrate(h);load(h,dict(rt.SAMPLE_EXAMPLE,dilution_factor=100));h.invoke('measurement.start');r=finish(h);self.assertIsNone(r['concentration_kg_m3']);self.assertIn('OUTSIDE_CALIBRATION',r['qc'])
 def test_linear_reference_removes_linear_matrix_background(self):
  areas=[]
  for bg,slope in [(0.,0.),(.2,.1)]:
   h=make();blank(h);load(h,dict(rt.STANDARD_EXAMPLE,background_absorbance=bg,background_slope=slope));h.invoke('measurement.start');areas.append(finish(h)['assays'][0]['integrated_absorbance_s'])
  self.assertAlmostEqual(areas[0],areas[1],places=12)
 def test_background_off_exposes_matrix_bias(self):
  h=make();h.invoke('flame.extinguish');h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,background='off'));h.invoke('flame.ignite');h.step(.1);blank(h);load(h,dict(rt.STANDARD_EXAMPLE,background_absorbance=.2));h.invoke('measurement.start');r=finish(h);self.assertGreater(r['assays'][0]['integrated_absorbance_s'],.1)
 def test_transmission_and_absorbance_arrays_log_relation(self):
  h=make();blank(h);load(h,rt.STANDARD_EXAMPLE);h.invoke('measurement.start');h.step(.1);r=h.invoke('spectrum.read');self.assertEqual(len(r['wavelength_m']),21);np.testing.assert_allclose(-np.log10(r['normalized_transmission']),r['apparent_absorbance'],rtol=1e-12,atol=1e-15)
 def test_repeat_consumption_and_sample_sd(self):
  h=make(noise=1e-6);calibrate(h);h.invoke('flame.extinguish');h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,repeats=3));h.invoke('flame.ignite');h.step(.1);load(h,rt.SAMPLE_EXAMPLE);h.invoke('measurement.start');r=finish(h);self.assertEqual(len(r['assays']),3);self.assertAlmostEqual(h.sample['volume'],rt.SAMPLE_EXAMPLE['volume_m3']-3*rt.METHOD_EXAMPLE['injection_volume_m3']);self.assertGreater(r['sd_kg_m3'],0.)
 def test_method_configure_preserves_prior_result_settings(self):
  h=make();blank(h);old=h.invoke('result.read');h.invoke('flame.extinguish');h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,background='off'));self.assertEqual(old,h.invoke('result.read'))
 def test_chunking_independent_complete_detector_windows(self):
  hosts=[make(noise=1e-6) for _ in range(2)]
  for h in hosts:load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start')
  hosts[0].step(.4)
  for _ in range(40):hosts[1].step(.01)
  np.testing.assert_allclose([p['corrected_absorbance'] for p in hosts[0]._active['points']],[p['corrected_absorbance'] for p in hosts[1]._active['points']],atol=1e-12);self.assertAlmostEqual(hosts[0]._active['integrated_absorbance_s'],hosts[1]._active['integrated_absorbance_s'],places=13)
 def test_no_partial_detector_sample_fabricated(self):
  h=make();load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start');h.step(.002);self.assertIsNone(h._last_point);self.assertEqual(h._active['points'],[])
 def test_agent_cannot_load_truth_or_read_private(self):
  h=make();s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='sample.load',arguments=rt.SAMPLE_EXAMPLE,request_id='forbidden');self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertNotIn('truth.ledger',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',s.describe(instance_id=h.instance_id)['capabilities'])
 def test_caller_cannot_choose_evaluator_role(self):
  h=make()
  with self.assertRaises(rt.ActionRejected):h.session('evaluator')
  h.register_principal('trusted',visibility='evaluator');self.assertIn('truth.ledger',h.session('trusted').read(instance_id=h.instance_id)['observations'])
 def test_request_dedup_and_argument_conflict(self):
  h=make();h.invoke('flame.extinguish');s=h.session();args=dict(instance_id=h.instance_id,capability_id='burner.configure',arguments=rt.BURNER_EXAMPLE,request_id='one');r=s.invoke(**args);before=h.revision;self.assertEqual(r,s.invoke(**args));self.assertEqual(h.revision,before);bad=s.invoke(**dict(args,arguments=dict(rt.BURNER_EXAMPLE,height_m=.016)));self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT')
 def test_async_history_owned_and_preserves_old_result(self):
  h=make();load(h,rt.BLANK_EXAMPLE);s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='measurement.start',arguments={},request_id='first');finish(h);op=r['operation_id'];old=s.poll(op);record=h._record_id;h.invoke('records.remove',record_id=record);h.invoke('measurement.start');finish(h);self.assertEqual(old,s.poll(op));h.register_principal('other')
  with self.assertRaises(rt.ActionRejected):h.session('other').poll(op)
 def test_cancel_history_partial_and_idempotent(self):
  h=make();load(h,rt.BLANK_EXAMPLE);s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='measurement.start',arguments={},request_id='start');h.step(.04);a=s.cancel(r['operation_id'],'stop');h.step(.1);self.assertEqual(a,s.cancel(r['operation_id'],'stop'));self.assertEqual(s.poll(r['operation_id'])['status'],'cancelled')
 def test_two_instances_isolate_rng_material_and_method(self):
  a,b=make(),make();before=b.debug_snapshot();blank(a);self.assertEqual(before,b.debug_snapshot());self.assertIsNot(a.library,b.library)
 def test_result_store_capacity_full_records(self):
  h=make(fuel_inventory_mol=1.,oxidant_inventory_mol=10.);blank(h)
  while len(h._records)<rt.RESULT_CAPACITY:
   load(h,rt.BLANK_EXAMPLE);h.invoke('measurement.start');finish(h,dt=.1)
  self.assertTrue(all(r['assays'][0]['points'] for r in h._records.values()));load(h,rt.BLANK_EXAMPLE);self.atomic_reject(h,'measurement.start','RESULT_STORE_FULL');first=next(iter(h._records));h.invoke('records.remove',record_id=first);h.invoke('measurement.start');finish(h);self.assertEqual(len(h._records),rt.RESULT_CAPACITY)
 def test_export_json_csv_real_rows(self):
  h=make();blank(h);r=h.invoke('result.read');self.assertEqual(json.loads(h.invoke('result.export',format='json')['content']),r);import csv,io;rows=list(csv.DictReader(io.StringIO(h.invoke('result.export',format='csv')['content'])));self.assertEqual(len(rows),len(r['assays'][0]['points']));self.assertAlmostEqual(float(rows[0]['time_s']),r['assays'][0]['points'][0]['time_s'])
 def test_unsupported_actions_atomic(self):
  h=make()
  for cap in rt.UNSUPPORTED:self.atomic_reject(h,cap,'UNSUPPORTED')
 def test_exported_error_codes_are_valid_and_include_real_failures(self):
  meta=rt.export_jena_contraa800_contract_metadata()
  for name,cap in meta['capabilities'].items():
   for error in cap['errors']:
    with self.subTest(capability=name,code=error['code']):self.assertRegex(error['code'],r'^[A-Z][A-Z0-9_]*\Z')
  self.assertIn('SAMPLER_PARK_REQUIRED',{e['code'] for e in meta['capabilities']['cover.set']['errors']})
  self.assertIn('GAS_EXHAUSTED',{e['code'] for e in meta['capabilities']['measurement.start']['errors']})
 def test_metadata_and_actual_values_match_schema(self):
  h=make();blank(h);meta=rt.export_jena_contraa800_contract_metadata()
  for key,value in h.read('evaluator').items():rt.validate(meta['state_slots'][key]['value_schema'],value)
  for key in ['process.measure','result.read','spectrum.read','blank.read','records.list','product.read']:rt.validate(rt.CAPS[key][1],h.invoke(key))
  for key,cap in meta['capabilities'].items():
   rt.validate(cap['input_schema'],cap['example_arguments'])
   for effect in cap['state_effects']:self.assertIn(effect['state_id'],meta['state_slots'])
 def test_source_negative_or_nonfinite_concentration_rejected(self):
  for value in [float('nan'),-.1]:
   h=make();h.templates['bad']=dict(element='Cu',concentration_kg_m3=value);self.atomic_reject(h,'sample.load','LIQUID_CONCENTRATION_REQUIRED',**dict(rt.SAMPLE_EXAMPLE,template_id='bad'))

 def test_one_celsius_program_increment(self):
  h=make('furnace');p=copy.deepcopy(rt.FURNACE_PROGRAM_EXAMPLE);p[0]['temperature_k']+=.1;self.atomic_reject(h,'method.configure','FURNACE_TEMPERATURE_INCREMENT',**dict(rt.FURNACE_METHOD_EXAMPLE,furnace_program=p))
 def test_burner_geometry_change_invalidates_conditional_calibration(self):
  h=make();calibrate(h);h.invoke('flame.extinguish');h.invoke('burner.configure',height_m=.012,angle_rad=.1);self.assertFalse(h._blank_valid());self.assertFalse(h._calibration_ready())
 def test_outside_numerical_liquid_domain_rejected(self):
  h=make();h.templates['overflow']=dict(element='Cu',concentration_kg_m3=1e308);self.atomic_reject(h,'sample.load','LIQUID_MODEL_DOMAIN',**dict(rt.SAMPLE_EXAMPLE,template_id='overflow'))
 def test_park_retract_then_rotate_respects_elapsed_time(self):
  h=make('furnace');h.injector=.1;h.sampler_arm=math.pi;h.invoke('sampler.park');h.step(.1);self.assertAlmostEqual(h.injector,.06);self.assertEqual(h.sampler_arm,math.pi);h.step(.15);self.assertAlmostEqual(h.injector,0.);self.assertEqual(h.sampler_arm,math.pi);h.step(.05);self.assertLess(h.sampler_arm,math.pi)

if __name__=='__main__':unittest.main()
