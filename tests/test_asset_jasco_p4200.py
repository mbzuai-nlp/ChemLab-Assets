"""Independent polarimetry, real-reference identity, calibration, conservation and protocol regression."""
import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.jasco_p4200 import *
def swap(r,s):
 r.invoke('lid.set',opened=True)
 if r.active:r.invoke('sample.unload',recipient_id='rack')
 r.invoke('sample.register',**s);r.invoke('sample.load',sample_id=s['sample_id']);r.invoke('lid.set',opened=False);r.invoke('lamp.set',enabled=True)
def run(r,kind):
 r.invoke('acquisition.start',kind=kind);r.step(40+r.method['integration_s']*r.method['repeats']);return r
def configured(plant=None,method=None,sample=None,blank=True,quartz=True):
 r=JascoP4200Runtime();p=copy.deepcopy(plant or PLANT_EXAMPLE);m=copy.deepcopy(method or METHOD_EXAMPLE);s=copy.deepcopy(sample or SAMPLE_EXAMPLE)
 r.invoke('plant.configure',**p);r.invoke('method.configure',**m);r.invoke('power.set',enabled=True)
 if blank:
  b=dict(s,sample_id='blank_01',kind='blank',template_id='',concentration_kg_m3=0.,quartz_angle_rad=None)
  swap(r,b);run(r,'blank')
 if quartz:
  q=dict(QUARTZ_EXAMPLE,reference_temperature_k=s['reference_temperature_k'])
  swap(r,q);run(r,'quartz');r.invoke('calibration.quartz',certified_angle_rad=math.radians(10),accept_simulated_standard=True)
 swap(r,s);return r
def measured(**kw):return run(configured(**kw),'sample')
class TestPolarimetry(unittest.TestCase):
 def test_real_67_unique_stereochemical_parameters(self):
  ts=JascoP4200Runtime().templates;self.assertEqual(len(ts),67);self.assertEqual(len({t['identity']['InChIKey'] for t in ts.values()}),67)
  for t in ts.values():
   self.assertTrue(t['stereocenters']);self.assertTrue(all(x[1]!='?' for x in t['stereocenters']));self.assertNotIn('\ufffd',json.dumps(t,ensure_ascii=False));self.assertAlmostEqual(t['specific_rotation_rad_m2_kg'],t['specific_rotation_deg_ml_g_dm']*math.pi/18000,places=15)
 def test_all67_complete_and_rotation_formula(self):
  for t in JascoP4200Runtime().templates.values():
   with self.subTest(id=t['substance_id']):
    cond=t['conditions'];c=cond['concentration_kg_m3'] if cond['concentration_kg_m3'] is not None else 100.
    s=dict(SAMPLE_EXAMPLE,template_id=t['substance_id'],solvent=cond['solvent'],reference_temperature_k=cond['temperature_k'],concentration_kg_m3=c)
    r=measured(sample=s,plant=dict(PLANT_EXAMPLE,ambient_k=cond['temperature_k']))
    self.assertEqual(r.phase,'succeeded');self.assertEqual(len(r.points),3)
    for p in r.points:self.assertAlmostEqual(p['rotation_rad'],t['specific_rotation_rad_m2_kg']*c*.1,places=12)
 def test_sucrose_unit_oracle(self):
  r=measured();self.assertAlmostEqual(math.degrees(r.points[-1]['rotation_rad']),6.637,places=11)
 def test_malus_quadrature_oracle_signed(self):
  for degrees in [-80,-10,0,10,80]:
   theta=math.radians(degrees);channels=[.7*math.cos(psi-theta)**2+.15 for psi in [0,math.pi/4,math.pi/2,3*math.pi/4]]
   self.assertAlmostEqual(JascoP4200Runtime.demodulate(channels),theta,places=12)
 def test_unpolarized_signal_rejected(self):
  with self.assertRaisesRegex(ActionRejected,'LOW_POLARIZED_SIGNAL'):JascoP4200Runtime.demodulate([1,1,1,1])
 def test_real_serine_mass_fraction_not_fabricated(self):
  t=JascoP4200Runtime().templates['pubchem_5951'];self.assertIsNone(t['conditions']['concentration_kg_m3']);self.assertIn('15 G',t['conditions']['source_condition_text'])
 def test_alanine_specific_condition(self):
  t=JascoP4200Runtime().templates['pubchem_5950'];self.assertEqual(t['specific_rotation_deg_ml_g_dm'],2.8);self.assertEqual(t['conditions']['concentration_kg_m3'],60.)
 def test_source_wrong_solvent_atomic(self):
  r=configured();before=r.debug_snapshot()
  with self.assertRaisesRegex(ActionRejected,'SOURCE_SOLVENT_MISMATCH'):r.invoke('sample.register',**dict(SAMPLE_EXAMPLE,sample_id='wrong',solvent='ethanol'))
  self.assertEqual(before,r.debug_snapshot())
 def test_source_wrong_temperature_rejected(self):
  r=configured()
  with self.assertRaisesRegex(ActionRejected,'SOURCE_TEMPERATURE_MISMATCH'):r.invoke('sample.register',**dict(SAMPLE_EXAMPLE,sample_id='wrong',reference_temperature_k=298.15))
 def test_known_source_concentration_no_extrapolation(self):
  r=configured();t=r.templates['pubchem_5950']
  with self.assertRaisesRegex(ActionRejected,'SOURCE_CONCENTRATION_MISMATCH'):r.invoke('sample.register',**dict(SAMPLE_EXAMPLE,sample_id='wrong',template_id='pubchem_5950',reference_temperature_k=298.15,concentration_kg_m3=10.))
 def test_unknown_concentration_ack(self):
  r=configured()
  with self.assertRaisesRegex(ActionRejected,'EVIDENCE_ACK_REQUIRED'):r.invoke('sample.register',**dict(SAMPLE_EXAMPLE,sample_id='wrong',accept_missing_source_concentration=False))
 def test_standard_configuration_only(self):
  r=configured()
  for m in [dict(METHOD_EXAMPLE,lamp='Hg'),dict(METHOD_EXAMPLE,wavelength_m=546e-9),dict(METHOD_EXAMPLE,integration_s=.5),dict(METHOD_EXAMPLE,integration_s=101)]:
   before=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.invoke('method.configure',**m)
   self.assertEqual(before,r.debug_snapshot())
 def test_cell_minimum_and_path_configuration(self):
  r=configured()
  for s in [dict(SAMPLE_EXAMPLE,sample_id='bad',volume_m3=.5e-6),dict(SAMPLE_EXAMPLE,sample_id='bad',path_m=.05)]:
   with self.assertRaises(ActionRejected):r.invoke('sample.register',**s)
 def test_blank_required(self):
  r=configured(blank=False,quartz=False)
  with self.assertRaisesRegex(ActionRejected,'BLANK_REQUIRED'):r.invoke('acquisition.start',kind='sample')
 def test_quartz_required(self):
  r=configured(quartz=False)
  with self.assertRaisesRegex(ActionRejected,'QUARTZ_CALIBRATION_REQUIRED'):r.invoke('acquisition.start',kind='sample')
 def test_calibration_recovers_observed_gain_not_true_read(self):
  r=measured(plant=dict(PLANT_EXAMPLE,instrument_gain=1.17,zero_offset_rad=.027));self.assertAlmostEqual(r.calibration['gain'],1.17,places=12);self.assertAlmostEqual(math.degrees(r.points[0]['rotation_rad']),6.637,places=11)
 def test_wrong_certified_quartz_has_observable_bias(self):
  r=configured();q=dict(QUARTZ_EXAMPLE,sample_id='quartz_02');swap(r,q);run(r,'quartz');r.invoke('calibration.quartz',certified_angle_rad=math.radians(20),accept_simulated_standard=True)
  r.invoke('lid.set',opened=True);r.invoke('sample.unload',recipient_id='rack');r.invoke('sample.load',sample_id='sample_01');r.invoke('lid.set',opened=False);r.invoke('lamp.set',enabled=True);run(r,'sample')
  self.assertAlmostEqual(math.degrees(r.points[0]['rotation_rad']),2*6.637,places=10)
 def test_analysis_uses_declared_concentration(self):
  r=measured();angles=[p['rotation_rad'] for p in r.points];before=r.debug_snapshot()
  a=r.invoke('quantify.specific',rotation_rad=angles,declared_concentration_kg_m3=50.,declared_path_m=.1)
  self.assertAlmostEqual(a['specific_rotation_rad_m2_kg']*18000/math.pi,132.74,places=9);self.assertEqual(before,r.debug_snapshot())
 def test_concentration_from_caller_inputs(self):
  r=JascoP4200Runtime();a=r.invoke('quantify.concentration',**CAPS['quantify.concentration'][6]);self.assertAlmostEqual(a['concentration_kg_m3'],100.,places=12)
 def test_analysis_bad_reference(self):
  r=JascoP4200Runtime()
  for a in [dict(CAPS['quantify.concentration'][6],same_conditions_confirmed=False),dict(CAPS['quantify.concentration'][6],reference_specific_rotation_rad_m2_kg=0.)]:
   with self.assertRaises(ActionRejected):r.invoke('quantify.concentration',**a)
 def test_queries_do_not_advance_or_mutate(self):
  r=measured();old=r.debug_snapshot()
  for _ in range(3):r.read();r.invoke('process.measure');r.invoke('result.read');r.invoke('template.read',substance_id='pubchem_5988');r.invoke('product.read')
  self.assertEqual(old,r.debug_snapshot())
 def test_slew_rate_and_integration_timing(self):
  r=configured();start=r.time_s;initial=r.analyzer_rad;target=math.radians(6.637)*1.02+.001;duration=abs(target-initial)/math.radians(6)
  r.invoke('acquisition.start',kind='sample');r.step(duration/2)
  self.assertEqual(len(r.points),0);self.assertAlmostEqual(r.analyzer_rad,(initial+target)/2,places=12)
  r.step(duration/2+.5);self.assertEqual(len(r.points),0);r.step(.5)
  self.assertEqual(len(r.points),1);self.assertAlmostEqual(r.points[0]['sample_time_s'],start+duration+1,places=10);self.assertEqual(r.points[0]['integration_s'],1.)
 def test_step_partition_deterministic(self):
  a=configured();b=configured();a.invoke('acquisition.start',kind='sample');b.invoke('acquisition.start',kind='sample');a.step(5.)
  for _ in range(500):b.step(.01)
  np.testing.assert_allclose([p['rotation_rad'] for p in a.points],[p['rotation_rad'] for p in b.points],atol=1e-13);self.assertAlmostEqual(a.time_s,b.time_s,places=9);self.assertEqual(a.phase,b.phase)
 def test_seed_reproducibility_and_noise(self):
  p=dict(PLANT_EXAMPLE,noise_density_sqrt_s=.0001);a=measured(plant=p);b=measured(plant=p)
  self.assertEqual(a.points,b.points);self.assertGreater(np.std([x['rotation_rad'] for x in a.points]),0)
 def test_noise_scaling_density_not_display_resolution(self):
  p=dict(PLANT_EXAMPLE,noise_density_sqrt_s=.001);a=measured(plant=p);b=measured(plant=p,method=dict(METHOD_EXAMPLE,integration_s=4.))
  self.assertAlmostEqual(a.points[0]['detector_sigma_rad']/b.points[0]['detector_sigma_rad'],2.,places=12)
 def test_partial_cancel_preserved(self):
  r=configured();r.invoke('acquisition.start',kind='sample');r.step(r._remaining+1.2);self.assertEqual(len(r.points),1);points=copy.deepcopy(r.points);angle=r.analyzer_rad
  r.invoke('acquisition.stop');r.step(2);self.assertEqual(r.phase,'cancelled');self.assertEqual(points,r.points);self.assertEqual(angle,r.analyzer_rad)
 def test_open_lid_interlock(self):
  r=configured();r.invoke('acquisition.start',kind='sample');r.step(.1);r.invoke('lid.set',opened=True);self.assertEqual(r.phase,'cancelled');self.assertFalse(r.lamp)
  with self.assertRaises(ActionRejected):r.invoke('lamp.set',enabled=True)
 def test_power_off_invalidates(self):
  r=configured();r.invoke('acquisition.start',kind='sample');r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.assertIsNone(r.blank);self.assertIsNone(r.calibration)
 def test_busy_rejects_method_without_mutation(self):
  r=configured();r.invoke('acquisition.start',kind='sample');old=r.debug_snapshot()
  with self.assertRaisesRegex(ActionRejected,'BUSY'):r.invoke('method.configure',**METHOD_EXAMPLE)
  self.assertEqual(old,r.debug_snapshot())
 def test_signed_temperature_standard_and_energy(self):
  r=JascoP4200Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('power.set',enabled=True);old=r.temperature_k;r.invoke('thermal.set_temperature',temperature_k=303.15);self.assertEqual(r.temperature_k,old);r.step(150);self.assertAlmostEqual(r.temperature_k,303.15,places=8);self.assertAlmostEqual(r._ledger()['thermal_residual_j'],0,places=8)
  r.invoke('thermal.set_temperature',temperature_k=288.15);r.step(200);self.assertAlmostEqual(r.temperature_k,288.15,places=7);self.assertAlmostEqual(r._ledger()['thermal_residual_j'],0,places=7)
 def test_thermal_partition(self):
  a=JascoP4200Runtime();b=JascoP4200Runtime()
  for r in [a,b]:r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('power.set',enabled=True);r.invoke('thermal.set_temperature',temperature_k=313.15)
  a.step(50)
  for _ in range(500):b.step(.1)
  self.assertAlmostEqual(a.temperature_k,b.temperature_k,places=9);self.assertAlmostEqual(a.controller_energy_j,b.controller_energy_j,places=7)
 def test_source_temperature_gate_not_full_factory_range(self):
  r=configured();r.invoke('thermal.set_temperature',temperature_k=303.15);r.step(100);r.invoke('lamp.set',enabled=True)
  with self.assertRaisesRegex(ActionRejected,'REFERENCE_TEMPERATURE_MISMATCH'):r.invoke('acquisition.start',kind='sample')
 def test_angle_aliasing_outside_range_rejected(self):
  r=configured(sample=dict(SAMPLE_EXAMPLE,concentration_kg_m3=1000.));r.plant['instrument_gain']=1.5
  with self.assertRaisesRegex(ActionRejected,'ANGLE_BRANCH_RANGE'):r.invoke('acquisition.start',kind='sample')
 def test_intact_cell_material_conservation(self):
  r=measured();a=r.invoke('product.read');self.assertAlmostEqual(a['attached_solute_kg'],.0001)
  r.invoke('lid.set',opened=True);r.invoke('sample.unload',recipient_id='rack');b=r.invoke('product.read')
  self.assertEqual(a['registered_solute_kg'],b['registered_solute_kg']);self.assertEqual(b['attached_volume_m3'],0);self.assertEqual(b['mass_residual_kg'],0);self.assertEqual(b['volume_residual_m3'],0)
 def test_two_instance_isolation(self):
  a=configured();b=configured();old=b.debug_snapshot();a.invoke('acquisition.start',kind='sample');a.step(2);self.assertEqual(old,b.debug_snapshot())
 def test_identity_acl_description_read_and_invoke(self):
  r=configured();agent=r.session();r.register_principal('host','evaluator');host=r.session('host')
  self.assertNotIn('truth.ledger',agent.describe(instance_id=r.instance_id)['state_slots']);self.assertNotIn('sample.register',agent.describe(instance_id=r.instance_id)['capabilities'])
  with self.assertRaises(ActionRejected):agent.read(instance_id=r.instance_id,state_ids=['truth.ledger'])
  denied=agent.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='private');self.assertEqual(denied['error']['code'],'ACCESS_DENIED')
  self.assertIn('truth.ledger',host.read(instance_id=r.instance_id)['observations'])
 def test_public_result_no_current_identity_or_concentration(self):
  r=measured();public=json.dumps([r.read(),r.invoke('result.read')]);self.assertNotIn('pubchem_5988',public);self.assertNotIn('active_template_id',public);self.assertNotIn('instrument_gain',public)
 def test_protocol_idempotency_conflict(self):
  r=configured();s=r.session();a=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='one');r.step(.1);rev=r.revision
  b=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='one');self.assertEqual(a,b);self.assertEqual(rev,r.revision)
  c=s.invoke(instance_id=r.instance_id,capability_id='acquisition.stop',arguments={},request_id='one');self.assertEqual(c['error']['code'],'REQUEST_CONFLICT')
 def test_protocol_operation_owner_cancel(self):
  r=configured();r.register_principal('other');s=r.session();a=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='one');op=a['operation_id']
  with self.assertRaises(ActionRejected):r.session('other').poll(op)
  self.assertEqual(s.poll(op)['status'],'running');self.assertEqual(s.cancel(op,'cancel')['result']['phase'],'cancelled')
 def test_null_quality_and_last_sample_window(self):
  r=configured();s=r.session();a=s.read(instance_id=r.instance_id,state_ids=['scan.rotation_rad']);self.assertEqual(a['observations']['scan.rotation_rad']['quality'],'unavailable')
  run(r,'sample');r.step(5);o=s.read(instance_id=r.instance_id,state_ids=['scan.rotation_rad'])['observations']['scan.rotation_rad'];self.assertEqual(o['sample_time_s'],r.points[-1]['sample_time_s']);self.assertEqual(o['sample_window_s'],[o['sample_time_s']-1,o['sample_time_s']])
 def test_argument_deepcopy_and_no_nan_bool_numbers(self):
  r=JascoP4200Runtime();p=copy.deepcopy(PLANT_EXAMPLE);r.invoke('plant.configure',**p);p['ambient_k']=300;self.assertEqual(r.plant['ambient_k'],293.15)
  for v in [True,float('nan'),float('inf')]:
   with self.assertRaises(ActionRejected):r.invoke('thermal.set_temperature',temperature_k=v)
 def test_query_deepcopy(self):
  r=measured();value=r.invoke('result.read');value['points'][0]['rotation_rad']=99;self.assertLess(r.points[0]['rotation_rad'],1)
 def test_unsupported_never_success(self):
  r=configured()
  for cap in UNSUPPORTED:
   a=r.session().invoke(instance_id=r.instance_id,capability_id=cap,arguments={},request_id=cap);self.assertEqual(a['status'],'unsupported')
 def test_host_clock_before_configuration(self):
  r=JascoP4200Runtime();r.step(10);r.invoke('plant.configure',**PLANT_EXAMPLE);self.assertEqual(r.time_s,10)
 def test_noisy_principal_angle_wrap_fails_and_preserves_points(self):
  alpha=JascoP4200Runtime().templates[SAMPLE_EXAMPLE['template_id']]['specific_rotation_rad_m2_kg'];concentration=(math.pi/2-1e-6-.001)/(1.4*alpha*.1)
  r=configured(plant=dict(PLANT_EXAMPLE,instrument_gain=1.4,noise_density_sqrt_s=.001,seed=70),sample=dict(SAMPLE_EXAMPLE,concentration_kg_m3=concentration,transmittance=1.));s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='branch')['operation_id'];r.step(40)
  self.assertEqual(r.phase,'failed');self.assertEqual(r.warning,'ANGLE_BRANCH_RANGE');self.assertEqual(len(r.points),2);self.assertTrue(all(p['rotation_rad']>0 for p in r.points));self.assertFalse(r.lamp)
  failed=s.poll(op);self.assertEqual(failed['status'],'failed');self.assertEqual(failed['result']['completed_points'],2);before=copy.deepcopy(r.points);r.step(10);self.assertEqual(r.points,before);self.assertEqual(s.poll(op),failed)
 def test_near_branch_without_wrap_remains_measurable(self):
  alpha=JascoP4200Runtime().templates[SAMPLE_EXAMPLE['template_id']]['specific_rotation_rad_m2_kg'];concentration=(math.pi/2-1e-6-.001)/(1.4*alpha*.1)
  r=measured(plant=dict(PLANT_EXAMPLE,instrument_gain=1.4,noise_density_sqrt_s=.001,seed=71),sample=dict(SAMPLE_EXAMPLE,concentration_kg_m3=concentration,transmittance=1.))
  self.assertEqual(r.phase,'succeeded');self.assertEqual(len(r.points),3);self.assertTrue(all(p['rotation_rad']>0 for p in r.points));self.assertGreater(math.degrees(r._target_angle),89.99)
 def test_raw_angle_window_matches_corrected_angle_and_poll(self):
  r=configured();s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='sample')['operation_id'];r.step(10)
  observations=s.read(instance_id=r.instance_id,state_ids=['scan.raw_rotation_rad','scan.rotation_rad'])['observations'];raw=observations['scan.raw_rotation_rad'];corrected=observations['scan.rotation_rad']
  self.assertEqual(raw['sample_window_s'],corrected['sample_window_s']);self.assertEqual(raw['sample_window_s'],[r.points[-1]['sample_time_s']-1.,r.points[-1]['sample_time_s']]);self.assertEqual(s.poll(op)['observations'],observations)
 def test_completed_operation_and_windows_survive_next_scan(self):
  r=configured();s=r.session();start=lambda rid:s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id=rid)
  old=start('first')['operation_id'];r.step(10);frozen=s.poll(old);r.invoke('lamp.set',enabled=True);start('second');r.step(1.2);before=r.debug_snapshot()
  self.assertEqual(s.poll(old),frozen);self.assertEqual(before,r.debug_snapshot());self.assertEqual(frozen['status'],'succeeded');self.assertEqual(frozen['result']['completed_points'],3)
  current=s.read(instance_id=r.instance_id,state_ids=['scan.raw_rotation_rad'])['observations']['scan.raw_rotation_rad'];self.assertGreater(current['sample_time_s'],frozen['observations']['scan.raw_rotation_rad']['sample_time_s'])
  frozen['observations']['scan.raw_rotation_rad']['sample_window_s'][0]=-999;self.assertGreater(s.poll(old)['observations']['scan.raw_rotation_rad']['sample_window_s'][0],0);r.register_principal('other')
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):r.session('other').poll(old)
  self.assertNotIn('pubchem_',json.dumps(s.poll(old)));self.assertNotIn('truth.',json.dumps(s.poll(old)))
 def test_cancel_old_operation_preserves_current_acquisition(self):
  r=configured();s=r.session();start=lambda rid:s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id=rid)
  old=start('first')['operation_id'];r.step(10);r.invoke('lamp.set',enabled=True);current=start('second')['operation_id'];revision=r.revision
  result=s.cancel(old,'cancel_old');self.assertEqual(result['result']['phase'],'succeeded');self.assertEqual(r.phase,'running');self.assertTrue(r.lamp);self.assertEqual(r.operation_id,current);self.assertEqual(r.revision,revision)
  r.step(.1);self.assertEqual(s.cancel(old,'cancel_old'),result)
  with self.assertRaisesRegex(ActionRejected,'REQUEST_CONFLICT'):s.cancel(current,'cancel_old')
  self.assertEqual(r.phase,'running')
 def test_cancelled_snapshot_survives_result_reset(self):
  r=configured();s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='first')['operation_id'];r.step(r._remaining+1.2);s.cancel(op,'stop');frozen=s.poll(op)
  r.invoke('lid.set',opened=True);r.invoke('method.configure',**METHOD_EXAMPLE);self.assertEqual(s.poll(op),frozen);self.assertEqual(frozen['result']['completed_points'],1);self.assertEqual(frozen['result']['warning'],'CANCELLED')
 def test_temperature_metadata_separates_target_from_process(self):
  r=configured();meta=export_jasco_p4200_contract_metadata();cap=meta['capabilities']['thermal.set_temperature'];immediate={e['state_id'] for e in cap['state_effects'] if e['timing']=='immediate'};later={e['state_id'] for e in cap['state_effects'] if e['timing']=='on_step'}
  before=r.read('evaluator');r.invoke('thermal.set_temperature',temperature_k=303.15);after=r.read('evaluator');self.assertLessEqual({k for k in before if before[k]!=after[k]},immediate);self.assertNotIn('thermal.temperature_k',immediate);self.assertNotIn('optics.analyzer_rad',immediate);self.assertNotIn('power.enabled',immediate)
  self.assertEqual(before['thermal.temperature_k'],after['thermal.temperature_k']);r.step(.1);self.assertNotEqual(r.temperature_k,before['thermal.temperature_k']);self.assertIn('thermal.temperature_k',later);self.assertIn('scan.raw_rotation_rad',meta['capabilities']['process.measure']['observes']);self.assertEqual(meta['capabilities']['process.measure']['state_effects'],[])
if __name__=='__main__':unittest.main()
