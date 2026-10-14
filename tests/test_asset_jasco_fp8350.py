"""FP-8350 process, source-data, physics, protocol, async, and negative control tests."""
import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.jasco_fp8350 import *
def ready(kind='sample',method=None,plant=None,sample=None):
 h=JascoFp8350Runtime();h.invoke('plant.configure',**(plant or FAST_PLANT));m=copy.deepcopy(method or METHOD_EXAMPLE);s=copy.deepcopy(sample or SAMPLE_EXAMPLE);b=dict(s,sample_id='blank_01',kind='blank',template_id='',concentration_mol_m3=0.)
 h.invoke('solution.register',**b);h.invoke('sample.load',sample_id=b['sample_id']);h.invoke('method.configure',**m);h.invoke('lid.set',opened=False);h.invoke('power.set',enabled=True);h.invoke('lamp.set',enabled=True);h.step(h.plant['lamp_warmup_s']+.01)
 if kind=='blank':return h
 h.invoke('acquisition.start',kind='blank');h.step(1+len(h._grid())*max(m['integration_s'],m['interval_s']));h.invoke('lid.set',opened=True);h.invoke('sample.unload',recipient_id='rack');h.invoke('solution.register',**s);h.invoke('sample.load',sample_id=s['sample_id']);h.invoke('lid.set',opened=False)
 return h
class TestFp8350(unittest.TestCase):
 def done(self,**kw):
  h=ready(**kw);h.invoke('acquisition.start',kind='sample');h.step(10);self.assertEqual(h.phase,'succeeded');return h
 def reject(self,h,cap,code='INVALID_ARGUMENT',**a):
  old=h.debug_snapshot()
  with self.assertRaisesRegex(ActionRejected,code):h.invoke(cap,**a)
  self.assertEqual(old,h.debug_snapshot())
 def test_01_library_paired(self):
  h=JascoFp8350Runtime();self.assertGreaterEqual(len(h.templates),50)
  for r in h.templates.values():self.assertGreater(len(r['epsilon_m2_mol']),20);self.assertGreater(len(r['emission_relative']),20);self.assertGreater(r['quantum_yield'],0)
 def test_02_distinct_names(self):
  rows=JascoFp8350Runtime().templates.values();self.assertEqual(len(rows),len({r['canonical_source_name_key'] for r in rows}))
 def test_03_source_sixg_units(self):
  r=JascoFp8350Runtime().templates['pcad_083'];eps=np.interp(529.8e-9,r['abs_wavelength_m'],r['epsilon_m2_mol']);self.assertLess(abs(eps-11600)/11600,.005);self.assertAlmostEqual(r['quantum_yield'],.95)
 def test_04_unknown_temp_null(self):self.assertTrue(all(r['conditions']['temperature_k'] is None for r in JascoFp8350Runtime().templates.values()))
 def test_05_initial_null(self):
  v=JascoFp8350Runtime().read();self.assertIsNone(v['scan.intensity']);self.assertIsNone(v['thermal.setpoint_k'])
 def test_06_initialization_only(self):
  h=ready();self.reject(h,'plant.configure','INITIALIZATION_ONLY',**FAST_PLANT)
 def test_07_init_after_host_time(self):
  h=JascoFp8350Runtime();h.step(.02);h.invoke('plant.configure',**FAST_PLANT);self.assertTrue(h.plant)
 def test_08_bool_numeric(self):
  h=ready();self.reject(h,'method.configure',**dict(METHOD_EXAMPLE,gain=True))
 def test_09_nan_numeric(self):
  h=ready();self.reject(h,'thermal.set_temperature',temperature_k=float('nan'))
 def test_10_unknown_argument(self):
  h=ready();self.reject(h,'power.set',enabled=True,secret=1)
 def test_11_extra_type_guard(self):
  h=ready();self.reject(h,'lid.set',opened=1)
 def test_12_detached_arguments(self):
  p=copy.deepcopy(FAST_PLANT);h=JascoFp8350Runtime();h.invoke('plant.configure',**p);p['incident_photons_s_inv']=1.;self.assertNotEqual(h.plant['incident_photons_s_inv'],1.)
 def test_13_unknown_solvent(self):
  h=ready();self.reject(h,'solution.register','SOURCE_SOLVENT_MISMATCH',**dict(SAMPLE_EXAMPLE,sample_id='new',solvent_id='water'))
 def test_14_evidence_ack(self):
  h=ready();self.reject(h,'solution.register','EVIDENCE_ACK_REQUIRED',**dict(SAMPLE_EXAMPLE,sample_id='new',accept_unknown_temperature=False))
 def test_15_blank_nonzero(self):
  h=ready();self.reject(h,'solution.register',**dict(BLANK_EXAMPLE,sample_id='bad',concentration_mol_m3=.001))
 def test_16_duplicate_sample(self):
  h=ready();self.reject(h,'solution.register','REQUEST_CONFLICT',**SAMPLE_EXAMPLE)
 def test_17_no_access_through_lid(self):
  h=ready();self.reject(h,'sample.unload','SAMPLE_ACCESS_REQUIRED',recipient_id='rack')
 def test_18_lamp_warmup(self):
  h=ready(kind='blank');h.invoke('lamp.set',enabled=False);h.invoke('lamp.set',enabled=True);self.reject(h,'acquisition.start','OPTICS_NOT_READY',kind='blank')
 def test_19_blank_required(self):
  h=ready();h.blank=None;self.reject(h,'acquisition.start','BLANK_REQUIRED',kind='sample')
 def test_20_wrong_kind(self):
  h=ready(kind='blank');self.reject(h,'acquisition.start','SAMPLE_KIND_MISMATCH',kind='sample')
 def test_21_completed_emission(self):
  h=self.done();self.assertEqual(len(h.points),15);self.assertGreater(max(x['intensity'] for x in h.points),0);self.assertFalse(h.shutter)
 def test_22_excitation(self):
  h=self.done(method=dict(METHOD_EXAMPLE,mode='excitation',start_m=450e-9,stop_m=510e-9));self.assertEqual(len({p['emission_m'] for p in h.points}),1);self.assertGreater(len({p['excitation_m'] for p in h.points}),1)
 def test_23_time_trace_constant(self):
  h=self.done(method=dict(METHOD_EXAMPLE,mode='time'));y=[p['intensity'] for p in h.points];self.assertEqual(len(y),11);self.assertLess(np.ptp(y),1e-12);self.assertAlmostEqual(h.points[-1]['elapsed_s'],1.04)
 def test_24_blank_subtraction(self):
  h=self.done();rate=h._rates(h.points[0]['excitation_m'],h.points[0]['emission_m']);self.assertAlmostEqual(h.points[0]['intensity'],rate['detected']/h.plant['reference_counts_s_inv'])
 def test_25_concentration_response(self):
  a=self.done();b=self.done(sample=dict(SAMPLE_EXAMPLE,concentration_mol_m3=.0001));ratio=max(p['intensity'] for p in a.points)/max(p['intensity'] for p in b.points);self.assertGreater(ratio,1.9);self.assertLess(ratio,2.001)
 def test_26_gain_response(self):
  a=self.done();b=self.done(method=dict(METHOD_EXAMPLE,gain=2.));self.assertAlmostEqual(max(p['intensity'] for p in b.points),2*max(p['intensity'] for p in a.points))
 def test_27_dilute_limit(self):
  h=ready(sample=dict(SAMPLE_EXAMPLE,concentration_mol_m3=.05));self.reject(h,'acquisition.start','DILUTE_MODEL_LIMIT',kind='sample')
 def test_28_no_template_extrapolation(self):
  h=ready(method=dict(METHOD_EXAMPLE,mode='excitation',start_m=210e-9,stop_m=250e-9,excitation_m=210e-9));self.reject(h,'acquisition.start','TEMPLATE_RANGE',kind='sample')
 def test_29_no_900nm(self):
  h=ready();self.reject(h,'method.configure',**dict(METHOD_EXAMPLE,emission_m=800e-9));self.reject(h,'optics.install_900nm','UNSUPPORTED')
 def test_30_no_unsupported_lifetime(self):
  h=ready();self.reject(h,'phosphorescence.measure','UNSUPPORTED')
 def test_31_scatter_guard(self):
  h=ready();self.reject(h,'method.configure','SCATTER_OR_ANTISTOKES_UNSUPPORTED',**dict(METHOD_EXAMPLE,excitation_m=550e-9))
 def test_32_grid_guard(self):
  h=ready();self.reject(h,'method.configure','GRID_INTERVAL_MISMATCH',**dict(METHOD_EXAMPLE,interval_m=3e-9))
 def test_33_bandwidth_enum(self):
  h=ready();self.reject(h,'method.configure',**dict(METHOD_EXAMPLE,emission_bandwidth_m=3e-9))
 def test_34_time_overlap(self):
  h=ready();self.reject(h,'method.configure','INTEGRATION_OVERLAP',**dict(METHOD_EXAMPLE,mode='time',interval_s=.02))
 def test_35_busy_guard(self):
  h=ready();h.invoke('acquisition.start',kind='sample');self.reject(h,'method.configure','BUSY',**METHOD_EXAMPLE)
 def test_36_read_purity(self):
  h=ready();before=h.debug_snapshot();h.describe();h.session().read(instance_id=h.instance_id);h.invoke('spectrum.read');h.invoke('template.read',substance_id='pcad_083');h.invoke('quantify.fit',**CAPS['quantify.fit'][6]);self.assertEqual(before,h.debug_snapshot())
 def test_37_cancel_partial(self):
  h=ready();h.invoke('acquisition.start',kind='sample');h.step(.10);h.invoke('acquisition.stop');n=len(h.points);h.step(20);self.assertEqual(n,2);self.assertEqual(len(h.points),2);self.assertEqual(h.phase,'cancelled')
 def test_38_lid_interrupt(self):
  h=ready();h.invoke('acquisition.start',kind='sample');h.step(.10);h.invoke('lid.set',opened=True);self.assertFalse(h.shutter);self.assertEqual(h.phase,'cancelled');self.assertEqual(len(h.points),2)
 def test_39_power_interrupt(self):
  h=ready();h.invoke('acquisition.start',kind='sample');h.invoke('power.set',enabled=False);self.assertFalse(h.lamp);self.assertFalse(h.shutter);self.assertEqual(h.phase,'cancelled')
 def test_40_photon_conservation(self):
  h=self.done();r=h.invoke('product.read')
  for k in ['excitation_residual','excited_event_residual','collection_residual']:self.assertLess(abs(r[k]),1e-5)
  self.assertGreater(r['absorbed_photons'],r['emitted_photons']);self.assertGreater(r['emitted_photons'],r['detected_photons'])
 def test_41_solute_conservation(self):
  h=ready();r=h.invoke('product.read');h.invoke('lid.set',opened=True);h.invoke('sample.unload',recipient_id='rack');v=h.invoke('product.read');self.assertEqual(r['registered_solute_mol'],v['external_solute_mol']);self.assertEqual(v['attached_solute_mol'],0);self.assertEqual(v['solute_residual_mol'],0)
 def test_42_chunk_invariance(self):
  a=ready();b=ready()
  for h in [a,b]:h.invoke('acquisition.start',kind='sample')
  a.step(1.)
  for _ in range(100):b.step(.01)
  self.assertEqual(len(a.points),len(b.points));np.testing.assert_allclose([p['intensity'] for p in a.points],[p['intensity'] for p in b.points],rtol=1e-12);self.assertAlmostEqual(a.time_s,b.time_s)
 def test_43_noise_seed(self):
  a=self.done(plant=PLANT_EXAMPLE);b=self.done(plant=PLANT_EXAMPLE);self.assertEqual(a.points,b.points);self.assertGreater(a.points[0]['estimated_sigma'],0)
 def test_44_accessory_required(self):
  h=ready();self.reject(h,'thermal.set_temperature','ACCESSORY_NOT_INSTALLED',temperature_k=300.)
 def test_45_thermal_setting_vs_control(self):
  h=ready(plant=dict(FAST_PLANT,temperature_accessory=True));h.invoke('thermal.set_temperature',temperature_k=343.15);self.assertFalse(h.read()['thermal.within_documented_control']);h.invoke('thermal.set_temperature',temperature_k=303.15);self.assertTrue(h.read()['thermal.within_documented_control'])
 def test_46_thermal_energy(self):
  h=ready(plant=dict(FAST_PLANT,temperature_accessory=True));h.invoke('thermal.set_temperature',temperature_k=333.15);h.step(100);r=h.invoke('product.read');self.assertGreater(h.temperature_k,320);self.assertLess(abs(r['thermal_residual_j']),1e-8)
 def test_47_thermal_chunk_invariance(self):
  a=ready(plant=dict(FAST_PLANT,temperature_accessory=True));b=ready(plant=dict(FAST_PLANT,temperature_accessory=True))
  for h in [a,b]:h.invoke('thermal.set_temperature',temperature_k=333.15)
  a.step(80)
  for _ in range(800):b.step(.1)
  self.assertAlmostEqual(a.temperature_k,b.temperature_k,places=8);self.assertAlmostEqual(a.controller_energy_j,b.controller_energy_j,places=7)
 def test_48_no_thermal_transfer_spectra(self):
  h=ready(plant=dict(FAST_PLANT,temperature_accessory=True));h.invoke('thermal.set_temperature',temperature_k=318.15);h.step(20);self.reject(h,'acquisition.start','REFERENCE_TEMPERATURE_MISMATCH',kind='sample')
 def test_49_fit_known(self):
  h=ready();q=h.invoke('quantify.fit',**CAPS['quantify.fit'][6]);self.assertAlmostEqual(q['estimated_concentration_mol_m3'],.00015);self.assertAlmostEqual(q['r_squared'],1.)
 def test_50_fit_extrapolation_null(self):
  q=ready().invoke('quantify.fit',**dict(CAPS['quantify.fit'][6],unknown_intensity=5.));self.assertIsNone(q['estimated_concentration_mol_m3']);self.assertFalse(q['within_calibration_range'])
 def test_51_fit_degenerate(self):
  h=ready();self.reject(h,'quantify.fit','CALIBRATION_INPUT',**dict(CAPS['quantify.fit'][6],concentrations_mol_m3=[0.,0.,0.]))
 def test_52_agent_no_truth(self):
  h=ready();s=h.session();self.assertNotIn('truth.ledger',h.read());self.assertNotIn('solution.register',s.describe(instance_id=h.instance_id)['capabilities'])
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
 def test_53_no_self_evaluator(self):
  h=ready();r=h.session().invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='t');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
 def test_54_dedup(self):
  h=ready();s=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='same');a=s.invoke(**kw);b=s.invoke(**kw);self.assertEqual(a,b);self.assertEqual(h._op_count,2)
 def test_55_conflicting_request(self):
  h=ready();s=h.session();kw=dict(instance_id=h.instance_id,capability_id='power.set',request_id='same');s.invoke(**kw,arguments={'enabled':True});r=s.invoke(**kw,arguments={'enabled':False});self.assertEqual(r['error']['code'],'REQUEST_CONFLICT');self.assertTrue(h.powered)
 def test_56_operation_owner(self):
  h=ready();s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='start');h.register_principal('other')
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):h.session('other').poll(r['operation_id'])
  h.step(.1);self.assertEqual(s.cancel(r['operation_id'],'stop')['result']['phase'],'cancelled')
 def test_57_write_routes(self):
  h=ready();h.session().write(instance_id=h.instance_id,state_id='lid.opened',value=True,request_id='write');self.assertTrue(h.opened)
 def test_58_two_instances(self):
  a=ready();b=ready();a.invoke('power.set',enabled=False);self.assertTrue(b.powered);self.assertFalse(a.powered)
 def test_59_sampling_window(self):
  h=self.done();v=h.session().read(instance_id=h.instance_id,state_ids=['scan.intensity'])['observations']['scan.intensity'];self.assertAlmostEqual(v['sample_window_s'][1]-v['sample_window_s'][0],.04);self.assertLess(v['sample_time_s'],h.time_s)
 def test_60_return_isolation(self):
  h=self.done();p=h.invoke('spectrum.read');p['points'][0]['intensity']=-123;self.assertNotEqual(h.points[0]['intensity'],-123)
 def test_61_wrong_instance(self):
  h=ready();r=h.session().invoke(instance_id='foreign',capability_id='power.set',arguments={'enabled':False},request_id='x');self.assertEqual(r['error']['code'],'UNKNOWN_INSTANCE')
 def test_62_negative_step(self):
  h=ready();old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.step(-1)
  self.assertEqual(old,h.debug_snapshot())
 def test_63_fit_overflow_rejected_without_effects(self):
  h=ready();s=h.session();before=h.debug_snapshot()
  q=s.invoke(instance_id=h.instance_id,capability_id='quantify.fit',arguments=dict(CAPS['quantify.fit'][6],intensities=[-1e308,0.,1e308],unknown_intensity=0.),request_id='overflow')
  self.assertEqual(q['status'],'rejected');self.assertEqual(q['error']['code'],'CALIBRATION_INPUT');self.assertEqual(before,h.debug_snapshot());json.dumps(q,allow_nan=False)
 def test_64_fit_subnormal_concentration_rejected(self):
  h=ready();before=h.debug_snapshot();s=h.session()
  q=s.invoke(instance_id=h.instance_id,capability_id='quantify.fit',arguments=dict(CAPS['quantify.fit'][6],concentrations_mol_m3=[0.,1e-320,2e-320]),request_id='subnormal')
  self.assertEqual(q['status'],'rejected');self.assertEqual(q['error']['code'],'CALIBRATION_INPUT');self.assertEqual(before,h.debug_snapshot())
 def test_65_fit_scaled_finite_units(self):
  h=ready();q=h.invoke('quantify.fit',**dict(CAPS['quantify.fit'][6],concentrations_mol_m3=[0.,1e-150,2e-150]))
  self.assertAlmostEqual(q['slope_m3_mol']/1e150,1.);self.assertAlmostEqual(q['estimated_concentration_mol_m3']/1.5e-150,1.);json.dumps(q,allow_nan=False)
 def test_66_fit_small_domain_extrapolation_is_null(self):
  h=ready();q=h.invoke('quantify.fit',**dict(CAPS['quantify.fit'][6],concentrations_mol_m3=[0.,1e-16,2e-16],unknown_intensity=3.))
  self.assertIsNone(q['estimated_concentration_mol_m3']);self.assertFalse(q['within_calibration_range'])
 def test_67_completed_operation_survives_next_scan(self):
  h=ready();s=h.session();start=lambda rid:s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id=rid)
  old=start('first')['operation_id'];h.step(2);completed=s.poll(old);start('second');before=h.debug_snapshot()
  self.assertEqual(s.poll(old),completed);self.assertEqual(h.debug_snapshot(),before);self.assertEqual(completed['status'],'succeeded');self.assertEqual(completed['result']['completed_points'],15)
  completed['result']['intensity']=-123;self.assertNotEqual(s.poll(old)['result']['intensity'],-123)
  h.register_principal('other')
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):h.session('other').poll(old)
  self.assertNotIn('pcad_083',json.dumps(s.poll(old)));self.assertNotIn('truth.',json.dumps(s.poll(old)))
 def test_68_cancel_completed_operation_keeps_new_scan(self):
  h=ready();s=h.session();start=lambda rid:s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id=rid)
  old=start('first')['operation_id'];h.step(2);current=start('second')['operation_id'];revision=h.revision
  result=s.cancel(old,'cancel_completed');self.assertEqual(result['result']['phase'],'succeeded');self.assertEqual(h.phase,'running');self.assertEqual(h.operation_id,current);self.assertTrue(h.shutter);self.assertEqual(h.revision,revision)
  h.step(.02);self.assertEqual(s.cancel(old,'cancel_completed'),result)
  with self.assertRaisesRegex(ActionRejected,'REQUEST_CONFLICT'):s.cancel(current,'cancel_completed')
  self.assertEqual(h.phase,'running')
 def test_69_cancelled_operation_snapshot_is_stable(self):
  h=ready();s=h.session();old=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='first')['operation_id'];h.step(.1)
  s.cancel(old,'cancel');frozen=s.poll(old);h.invoke('lid.set',opened=True);h.invoke('method.configure',**METHOD_EXAMPLE)
  self.assertEqual(frozen,s.poll(old));self.assertEqual(frozen['result']['completed_points'],2);self.assertEqual(frozen['status'],'cancelled');self.assertEqual(frozen['result']['warning'],'CANCELLED')
 def test_70_temperature_contract_matches_target_and_time(self):
  h=ready(plant=dict(FAST_PLANT,temperature_accessory=True));meta=export_jasco_fp8350_contract_metadata();cap=meta['capabilities']['thermal.set_temperature']
  effects={timing:{e['state_id'] for e in cap['state_effects'] if e['timing']==timing} for timing in ['immediate','on_step']}
  before=h.read('evaluator');h.invoke('thermal.set_temperature',temperature_k=303.15);after=h.read('evaluator');changed={k for k in before if before[k]!=after[k]}
  self.assertLessEqual(changed,effects['immediate']);self.assertNotIn('thermal.temperature_k',effects['immediate']);self.assertNotIn('power.enabled',effects['immediate']);self.assertNotIn('lid.opened',effects['immediate']);self.assertEqual(before['thermal.temperature_k'],after['thermal.temperature_k'])
  h.step(.1);self.assertNotEqual(h.temperature_k,before['thermal.temperature_k']);self.assertIn('thermal.temperature_k',effects['on_step']);self.assertIn('scan.intensity',meta['capabilities']['process.measure']['observes']);self.assertEqual(meta['capabilities']['process.measure']['state_effects'],[])
if __name__=='__main__':unittest.main()

