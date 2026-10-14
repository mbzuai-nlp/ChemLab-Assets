"""Independent numerical, lifecycle, protocol and all60 template-path regression tests."""
import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.bruker_magnettechesr5000 import *
def configured(plant=None,method=None,sample=None,blank=True):
 r=BrukerMagnettechesr5000Runtime();p=copy.deepcopy(plant or PLANT_EXAMPLE);m=copy.deepcopy(method or METHOD_EXAMPLE);s=copy.deepcopy(sample or SAMPLE_EXAMPLE)
 r.invoke('plant.configure',**p);r.invoke('method.configure',**m);r.invoke('power.set',enabled=True)
 if blank:
  b=dict(s,sample_id='blank_01',kind='blank',template_id='',concentration_mol_m3=0.,solid_spin_count=0.,mass_kg=0.)
  r.invoke('sample.register',**b);r.invoke('sample.load',sample_id=b['sample_id']);r.invoke('lid.set',opened=False);r.invoke('microwave.set',enabled=True);r.invoke('acquisition.start',kind='blank');r.step(m['points']*m['dwell_s'])
  r.invoke('lid.set',opened=True);r.invoke('sample.unload',recipient_id='rack')
 r.invoke('sample.register',**s);r.invoke('sample.load',sample_id=s['sample_id']);r.invoke('lid.set',opened=False);r.invoke('microwave.set',enabled=True);return r
def measured(**kw):
 r=configured(**kw);r.invoke('acquisition.start',kind='sample');r.step(r.method['points']*r.method['dwell_s']);return r
def trace(r):return dict(field_t=[p['field_t'] for p in r.points],intensity=[p['intensity'] for p in r.points])
class TestEPR(unittest.TestCase):
 def test_real_templates(self):
  r=BrukerMagnettechesr5000Runtime();self.assertEqual(len(r.templates),60);self.assertEqual(sum(x['g_iso'] is not None for x in r.templates.values()),11)
  for t in r.templates.values():self.assertEqual(t['hyperfine_t'],[float(t['source']['raw_record']['AN'])*1e-4,float(t['source']['raw_record']['AH'])*1e-4]);self.assertIsNone(t['raw_signal']);self.assertIsNone(t['source_linewidth_pp_t'])
 def test_blank_sample_complete(self):
  r=measured();self.assertEqual(r.phase,'succeeded');self.assertEqual(len(r.points),129);self.assertFalse(r.microwave);self.assertGreater(np.ptp([p['intensity'] for p in r.points]),1e-4)
 def test_all60_real_template_acquisitions(self):
  for t in BrukerMagnettechesr5000Runtime().templates.values():
   with self.subTest(id=t['substance_id']):
    s=dict(SAMPLE_EXAMPLE,template_id=t['substance_id'],solvent_code=t['conditions']['solvent_code'],g_assumed=None if t['g_iso'] is not None else 2.0055)
    r=measured(sample=s);self.assertEqual(r.phase,'succeeded');self.assertTrue(np.isfinite([p['intensity'] for p in r.points]).all());self.assertGreater(np.ptp([p['intensity'] for p in r.points]),0)
 def test_resonance_formula(self):
  self.assertAlmostEqual(H_PLANCK*9.5e9/(MU_B*2.0055),.3384420,places=5)
 def test_lorentz_derivative_oracle(self):
  s=dict(SAMPLE_EXAMPLE,material_phase='solid',template_id='',solvent_code='',volume_m3=0.,mass_kg=.001,concentration_mol_m3=0.,solid_spin_count=1e12,g_assumed=2.0055,accept_synthetic_solid=True,saturation_power_w=100.)
  m=dict(METHOD_EXAMPLE,modulation_pp_t=1e-7);r=configured(sample=s,method=m);b=r._grid();center=m['field_center_t'];sat=m['microwave_power_w']/s['saturation_power_w'];gamma=math.sqrt(3)*s['linewidth_pp_t']/2*math.sqrt(1+sat);x=b-center
  expected=-2*gamma*x/(math.pi*(x*x+gamma*gamma)**2)*1/math.sqrt(1+sat)*PLANT_EXAMPLE['shape_scale_t2'];np.testing.assert_allclose(r._profile(b),expected,rtol=4e-7,atol=1e-10)
 def test_six_stick_first_order_oracle(self):
  r=configured(method=dict(METHOD_EXAMPLE,modulation_pp_t=1e-7));b=r._grid();s=r._sample();an,ah=r.templates[s['template_id']]['hyperfine_t'];gamma=math.sqrt(3)*s['linewidth_pp_t']/2*math.sqrt(1+.001/.1);expected=np.zeros(len(b))
  for mn in [-1,0,1]:
   for mh in [-.5,.5]:
    x=b-(r.method['field_center_t']-mn*an-mh*ah);expected+=-2*gamma*x/(math.pi*(x*x+gamma*gamma)**2)/6
  expected*=r._spins(s)/r.plant['reference_spin_count']/math.sqrt(1.01)*r.plant['shape_scale_t2'];np.testing.assert_allclose(r._profile(b),expected,rtol=1e-6,atol=1e-10)
 def test_finite_modulation_converges(self):
  r=configured(method=dict(METHOD_EXAMPLE,modulation_pp_t=.001));a=r._profile(r._grid(),256);b=r._profile(r._grid(),1024);np.testing.assert_allclose(a,b,rtol=1e-9,atol=1e-10)
 def test_modulation_pp_not_peak(self):
  r=configured(method=dict(METHOD_EXAMPLE,modulation_pp_t=.0005));a=r._profile(r._grid());r.method['modulation_pp_t']=.001;b=r._profile(r._grid());self.assertGreater(float(np.max(np.abs(a-b))),1e-4)
 def test_concentration_doubles_observation(self):
  a=measured();b=measured(sample=dict(SAMPLE_EXAMPLE,concentration_mol_m3=.0002));np.testing.assert_allclose(trace(b)['intensity'],2*np.array(trace(a)['intensity']),rtol=1e-11,atol=1e-13)
 def test_relative_from_observations_only(self):
  r=measured();a=trace(r);b=dict(field_t=a['field_t'],intensity=[2*x for x in a['intensity']]);ans=r.invoke('quantify.relative',reference=a,sample=b,reference_volume_m3=1e-7,sample_volume_m3=2e-7,same_settings_confirmed=True);self.assertAlmostEqual(ans['relative_spin_number'],2);self.assertAlmostEqual(ans['relative_spin_concentration'],1)
 def test_g_centroid_and_multiplet_width_null(self):
  r=measured();v=r.invoke('spectrum.analyze',trace=trace(r),microwave_frequency_hz=9.5e9,isolated_line_assertion=False);self.assertAlmostEqual(v['g_apparent'],2.0055,places=7);self.assertIsNone(v['linewidth_pp_t'])
 def test_solid_explicit_isotropic_linewidth(self):
  s=dict(SAMPLE_EXAMPLE,material_phase='solid',template_id='',solvent_code='',volume_m3=0.,mass_kg=.001,concentration_mol_m3=0.,solid_spin_count=1e12,g_assumed=2.0055,accept_synthetic_solid=True,saturation_power_w=100.)
  m=dict(METHOD_EXAMPLE,points=1025,modulation_pp_t=1e-7);r=measured(sample=s,method=m);v=r.invoke('spectrum.analyze',trace=trace(r),microwave_frequency_hz=9.5e9,isolated_line_assertion=True);self.assertAlmostEqual(v['linewidth_pp_t'],s['linewidth_pp_t'],delta=m['sweep_width_t']/(m['points']-1)*2);self.assertEqual(r.invoke('product.read')['registered_solid_kg'],.001)
 def test_substep_clock_and_noise_equivalence(self):
  p=dict(PLANT_EXAMPLE,noise_density_sqrt_s=.001);m=dict(METHOD_EXAMPLE,lockin_time_constant_s=.013);a=configured(plant=p,method=m);b=configured(plant=p,method=m)
  for r in [a,b]:r.invoke('acquisition.start',kind='sample')
  a.step(1.29)
  for _ in range(1290):b.step(.001)
  np.testing.assert_allclose(trace(a)['intensity'],trace(b)['intensity'],rtol=1e-10,atol=1e-10);self.assertAlmostEqual(a.time_s,b.time_s,places=10)
 def test_lockin_interval_mean(self):
  r=configured(method=dict(METHOD_EXAMPLE,lockin_time_constant_s=.02));r.invoke('acquisition.start',kind='sample');target=r._schedule[0]['target'];r.step(.01);self.assertAlmostEqual(r.points[0]['raw_intensity'],target*(1-.02/.01*(1-math.exp(-.01/.02))),places=12)
 def test_pp_saturation_reduces_area(self):
  r=configured();b=np.linspace(.2,.5,4096);low=r._profile(b);r.method['microwave_power_w']=.1;high=r._profile(b)
  self.assertLess(np.max(np.abs(high))/np.max(np.abs(low)),math.sqrt(100))
 def test_thermal_energy_and_partition(self):
  a=BrukerMagnettechesr5000Runtime();b=BrukerMagnettechesr5000Runtime()
  for r in [a,b]:r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('power.set',enabled=True);r.invoke('thermal.set_temperature',temperature_k=308.)
  a.step(20)
  for _ in range(2000):b.step(.01)
  self.assertAlmostEqual(a.temperature_k,b.temperature_k,places=9);self.assertAlmostEqual(a._ledger()['thermal_residual_j'],0,places=8);self.assertGreater(a.temperature_k,298.15)
 def test_material_ledger_unload_conserves(self):
  r=measured();before=r._ledger();r.invoke('lid.set',opened=True);r.invoke('sample.unload',recipient_id='rack');after=r._ledger()
  for k in ['registered_volume_m3','registered_solute_mol','registered_spins']:self.assertEqual(before[k],after[k])
  for k in ['volume_residual_m3','solute_residual_mol','mass_residual_kg','spin_residual']:self.assertEqual(after[k],0)
 def test_cancel_partial_then_pure_read(self):
  r=configured();r.invoke('acquisition.start',kind='sample');r.step(.035);r.invoke('acquisition.stop');self.assertEqual(len(r.points),3);self.assertEqual(r.phase,'cancelled');old=r.debug_snapshot()
  for _ in range(3):r.invoke('process.measure');r.invoke('spectrum.read');r.read()
  self.assertEqual(old,r.debug_snapshot())
 def test_opening_interrupts_without_faking_complete(self):
  r=configured();r.invoke('acquisition.start',kind='sample');r.step(.025);r.invoke('lid.set',opened=True);self.assertFalse(r.microwave);self.assertEqual(r.phase,'cancelled');self.assertEqual(len(r.points),2)
 def test_power_off_interrupts(self):
  r=configured();r.invoke('acquisition.start',kind='sample');r.step(.025);r.invoke('power.set',enabled=False);self.assertFalse(r.microwave);self.assertFalse(r._blank_ok())
 def test_busy_rejection_atomic(self):
  r=configured();r.invoke('acquisition.start',kind='sample');before=r.debug_snapshot()
  with self.assertRaisesRegex(ActionRejected,'BUSY'):r.invoke('method.configure',**METHOD_EXAMPLE)
  self.assertEqual(before,r.debug_snapshot())
 def test_schema_bool_nan_unknown_rejected(self):
  r=configured()
  for args in [dict(METHOD_EXAMPLE,points=True),dict(METHOD_EXAMPLE,microwave_power_w=float('nan')),dict(METHOD_EXAMPLE,alien=1),dict(METHOD_EXAMPLE,modulation_frequency_hz=20000.)]:
   old=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.invoke('method.configure',**args)
   self.assertEqual(old,r.debug_snapshot())
 def test_field_extents_and_factory_limits(self):
  r=configured()
  for args in [dict(METHOD_EXAMPLE,field_center_t=.65),dict(METHOD_EXAMPLE,microwave_power_w=.10001),dict(METHOD_EXAMPLE,microwave_power_w=0),dict(METHOD_EXAMPLE,points=250000)]:
   with self.assertRaises(ActionRejected):r.invoke('method.configure',**args)
  r.invoke('method.configure',**dict(METHOD_EXAMPLE,field_center_t=.32,sweep_width_t=.66))
 def test_missing_blank(self):
  r=configured(blank=False)
  with self.assertRaisesRegex(ActionRejected,'BLANK_REQUIRED'):r.invoke('acquisition.start',kind='sample')
 def test_changed_method_invalidates_blank(self):
  r=configured();r.invoke('method.configure',**dict(METHOD_EXAMPLE,gain=2.));self.assertFalse(r._blank_ok())
 def test_solvent_and_g_guards(self):
  for change,msg in [({'solvent_code':'WRONG'},'SOURCE_SOLVENT_MISMATCH'),({'g_assumed':None},'MISSING_G_ASSUMPTION'),({'template_id':'niehs_122','solvent_code':'AQ08P5','g_assumed':2.0023},'SOURCE_G_CONFLICT')]:
   with self.assertRaisesRegex(ActionRejected,msg):configured(sample=dict(SAMPLE_EXAMPLE,**change))
 def test_source_g_kept_without_assumed_override(self):
  t=BrukerMagnettechesr5000Runtime().templates['niehs_122'];r=measured(sample=dict(SAMPLE_EXAMPLE,template_id='niehs_122',solvent_code=t['conditions']['solvent_code'],g_assumed=None));self.assertEqual(r.phase,'succeeded')
 def test_solid_requires_explicit_ack(self):
  with self.assertRaisesRegex(ActionRejected,'SYNTHETIC_SOLID_ACK_REQUIRED'):configured(sample=dict(SAMPLE_EXAMPLE,material_phase='solid'),blank=False)
 def test_missing_temperature_accessory(self):
  r=configured(plant=dict(PLANT_EXAMPLE,temperature_accessory=False))
  with self.assertRaisesRegex(ActionRejected,'ACCESSORY_NOT_INSTALLED'):r.invoke('thermal.set_temperature',temperature_k=298.15)
 def test_template_temperature_guard(self):
  r=configured();r.invoke('thermal.set_temperature',temperature_k=310.);r.step(100)
  with self.assertRaisesRegex(ActionRejected,'REFERENCE_TEMPERATURE_MISMATCH'):r.invoke('acquisition.start',kind='sample')
 def test_initialization_once_after_clock_advance(self):
  r=BrukerMagnettechesr5000Runtime();r.step(5);r.invoke('plant.configure',**PLANT_EXAMPLE)
  with self.assertRaisesRegex(ActionRejected,'INITIALIZATION_ONLY'):r.invoke('plant.configure',**PLANT_EXAMPLE)
 def test_no_return_aliases(self):
  r=measured();d=r.invoke('spectrum.read');d['points'][0]['intensity']=999;self.assertNotEqual(r.points[0]['intensity'],999);x=r.read('evaluator');x['truth.ledger']['registered_spins']=0;self.assertGreater(r._ledger()['registered_spins'],0)
 def test_no_input_aliases(self):
  r=BrukerMagnettechesr5000Runtime();p=copy.deepcopy(PLANT_EXAMPLE);r.invoke('plant.configure',**p);p['ambient_k']=300;self.assertEqual(r.plant['ambient_k'],298.15)
 def test_session_acl_and_no_identity_leak(self):
  r=configured();s=r.session();d=s.describe(instance_id=r.instance_id);self.assertNotIn('sample.register',d['capabilities']);self.assertNotIn('truth.ledger',d['state_slots'])
  denied=s.invoke(instance_id=r.instance_id,capability_id='sample.register',arguments=SAMPLE_EXAMPLE,request_id='private');self.assertEqual(denied['error']['code'],'ACCESS_DENIED')
  self.assertNotIn('niehs_109',json.dumps(s.read(instance_id=r.instance_id)))
 def test_request_dedup_and_conflict(self):
  r=configured();s=r.session();kw=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='a');a=s.invoke(**kw);b=s.invoke(**kw);self.assertEqual(a,b);self.assertEqual(r._op_count,2)
  c=s.invoke(**dict(kw,arguments={'kind':'blank'}));self.assertEqual(c['error']['code'],'REQUEST_CONFLICT')
 def test_operation_ownership_and_cancel(self):
  r=configured();r.register_principal('other');s=r.session();a=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='a')
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):r.session('other').poll(a['operation_id'])
  s.cancel(a['operation_id'],'cancel');self.assertEqual(s.poll(a['operation_id'])['status'],'cancelled')
 def test_session_observation_window_and_null(self):
  r=BrukerMagnettechesr5000Runtime();s=r.session();v=s.read(instance_id=r.instance_id)['observations']['scan.intensity'];self.assertIsNone(v['value']);self.assertEqual(v['quality'],'unavailable')
  r=configured();r.invoke('acquisition.start',kind='sample');r.step(.025);v=r.session().read(instance_id=r.instance_id)['observations']['scan.intensity'];self.assertAlmostEqual(v['sample_time_s'],r.points[-1]['sample_time_s']);self.assertAlmostEqual(v['sample_window_s'][1]-v['sample_window_s'][0],.01)
 def test_unsupported_envelope_and_layers(self):
  r=configured();meta=export_bruker_magnettechesr5000_contract_metadata()
  for c in UNSUPPORTED:self.assertEqual(r.session().invoke(instance_id=r.instance_id,capability_id=c,arguments={},request_id=c)['status'],'unsupported');self.assertTrue(all(v=='absent' for v in meta['capabilities'][c]['implementation'].values()));self.assertIsNone(meta['capabilities'][c]['binding'])
 def test_analysis_zero_and_reference_rejections(self):
  r=configured();zero=dict(field_t=np.linspace(.3,.4,16).tolist(),intensity=[0.]*16);v=r.invoke('spectrum.analyze',trace=zero,microwave_frequency_hz=9.5e9,isolated_line_assertion=True);self.assertIsNone(v['g_apparent']);self.assertIsNone(v['linewidth_pp_t'])
  with self.assertRaises(ActionRejected):r.invoke('quantify.relative',reference=zero,sample=zero,reference_volume_m3=1e-7,sample_volume_m3=1e-7,same_settings_confirmed=True)
 def test_public_write_only_setpoint(self):
  r=configured();s=r.session()
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):s.write(instance_id=r.instance_id,state_id='scan.intensity',value=1.,request_id='bad')
  a=s.write(instance_id=r.instance_id,state_id='lid.opened',value=True,request_id='open');self.assertEqual(a['status'],'succeeded');self.assertTrue(r.opened)
 def test_nonuniform_field_linear_background_has_no_resonance(self):
  r=BrukerMagnettechesr5000Runtime();b=.3+.1*np.linspace(0,1,16)**2
  result=r.invoke('spectrum.analyze',trace=dict(field_t=b.tolist(),intensity=(3*b+.2).tolist()),microwave_frequency_hz=9.5e9,isolated_line_assertion=True)
  self.assertEqual(result['double_integral_t2'],0.);self.assertIsNone(result['g_apparent']);self.assertIsNone(result['linewidth_pp_t'])
 def test_nonuniform_trace_baseline_does_not_change_integral(self):
  r=BrukerMagnettechesr5000Runtime();b=.33+.02*np.linspace(0,1,257)**1.3;x=(b-.34)/.002;y=-x*np.exp(-x*x)
  clean=r.invoke('spectrum.analyze',trace=dict(field_t=b.tolist(),intensity=y.tolist()),microwave_frequency_hz=9.5e9,isolated_line_assertion=False)
  shifted=r.invoke('spectrum.analyze',trace=dict(field_t=b.tolist(),intensity=(y+3*b+.2).tolist()),microwave_frequency_hz=9.5e9,isolated_line_assertion=False)
  self.assertAlmostEqual(clean['double_integral_t2'],shifted['double_integral_t2'],places=16);self.assertAlmostEqual(clean['g_apparent'],shifted['g_apparent'],places=12)
 def test_relative_overflow_is_rejected_without_effects(self):
  r=BrukerMagnettechesr5000Runtime();ref=dict(field_t=DEMO_TRACE['field_t'],intensity=[v*1e-18 for v in DEMO_TRACE['intensity']]);sample=dict(field_t=DEMO_TRACE['field_t'],intensity=[v*1e308 for v in DEMO_TRACE['intensity']]);before=r.debug_snapshot()
  result=r.session().invoke(instance_id=r.instance_id,capability_id='quantify.relative',arguments=dict(reference=ref,sample=sample,reference_volume_m3=1e-3,sample_volume_m3=1e-12,same_settings_confirmed=True),request_id='overflow')
  self.assertEqual(result['status'],'rejected');self.assertEqual(result['error']['code'],'INVALID_REFERENCE_INTEGRAL');self.assertEqual(before,r.debug_snapshot());json.dumps(result,allow_nan=False)
 def test_analysis_unrepresentable_area_is_rejected(self):
  r=BrukerMagnettechesr5000Runtime();trace=dict(field_t=np.linspace(1.,1e200,33).tolist(),intensity=DEMO_TRACE['intensity']);before=r.debug_snapshot()
  result=r.session().invoke(instance_id=r.instance_id,capability_id='spectrum.analyze',arguments=dict(trace=trace,microwave_frequency_hz=9.5e9,isolated_line_assertion=True),request_id='area_overflow')
  self.assertEqual(result['status'],'rejected');self.assertEqual(result['error']['code'],'ANALYSIS_INPUT');self.assertEqual(before,r.debug_snapshot());json.dumps(result,allow_nan=False)
 def test_completed_operation_survives_later_scan_with_ownership(self):
  r=configured();s=r.session();start=lambda rid:s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id=rid)
  old=start('first')['operation_id'];r.step(2);completed=s.poll(old);r.invoke('microwave.set',enabled=True);start('second');before=r.debug_snapshot()
  self.assertEqual(s.poll(old),completed);self.assertEqual(before,r.debug_snapshot());self.assertEqual(completed['status'],'succeeded');self.assertEqual(completed['result']['completed_points'],129)
  completed['result']['intensity']=-999;self.assertNotEqual(s.poll(old)['result']['intensity'],-999);r.register_principal('other')
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):r.session('other').poll(old)
  self.assertNotIn('niehs_',json.dumps(s.poll(old)));self.assertNotIn('truth.',json.dumps(s.poll(old)))
 def test_cancel_old_operation_preserves_active_scan_and_dedup(self):
  r=configured();s=r.session();start=lambda rid:s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id=rid)
  old=start('first')['operation_id'];r.step(2);r.invoke('microwave.set',enabled=True);current=start('second')['operation_id'];revision=r.revision
  result=s.cancel(old,'cancel_old');self.assertEqual(result['result']['phase'],'succeeded');self.assertEqual(r.phase,'running');self.assertTrue(r.microwave);self.assertEqual(r.operation_id,current);self.assertEqual(r.revision,revision)
  r.step(.02);self.assertEqual(s.cancel(old,'cancel_old'),result)
  with self.assertRaisesRegex(ActionRejected,'REQUEST_CONFLICT'):s.cancel(current,'cancel_old')
  self.assertEqual(r.phase,'running')
 def test_cancelled_operation_survives_invalidation(self):
  r=configured();s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='first')['operation_id'];r.step(.035);s.cancel(op,'stop');frozen=s.poll(op)
  r.invoke('lid.set',opened=True);r.invoke('method.configure',**METHOD_EXAMPLE);self.assertEqual(s.poll(op),frozen);self.assertEqual(frozen['result']['completed_points'],3);self.assertEqual(frozen['result']['warning'],'CANCELLED')
 def test_temperature_metadata_separates_setting_from_process(self):
  r=configured();meta=export_bruker_magnettechesr5000_contract_metadata();cap=meta['capabilities']['thermal.set_temperature'];immediate={e['state_id'] for e in cap['state_effects'] if e['timing']=='immediate'};later={e['state_id'] for e in cap['state_effects'] if e['timing']=='on_step'}
  before=r.read('evaluator');r.invoke('thermal.set_temperature',temperature_k=303.15);after=r.read('evaluator');self.assertLessEqual({k for k in before if before[k]!=after[k]},immediate);self.assertNotIn('thermal.temperature_k',immediate);self.assertNotIn('power.enabled',immediate);self.assertNotIn('lid.opened',immediate)
  self.assertEqual(before['thermal.temperature_k'],after['thermal.temperature_k']);r.step(.1);self.assertNotEqual(r.temperature_k,before['thermal.temperature_k']);self.assertIn('thermal.temperature_k',later);self.assertIn('scan.intensity',meta['capabilities']['process.measure']['observes']);self.assertEqual(meta['capabilities']['process.measure']['state_effects'],[])
if __name__=='__main__':unittest.main()

