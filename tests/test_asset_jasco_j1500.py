"""Actual CHEM-065 protocol, optical math, finite-resource and timing regressions."""
import copy,json,math,unittest
import numpy as np
from scripts.asset_runtime.jasco_j1500 import *
def ready(kind='sample',method=None,plant=None,sample=None):
 h=JascoJ1500Runtime();h.invoke('plant.configure',**dict(FAST_PLANT,**(plant or {})))
 h.invoke('solution.register',**BLANK_EXAMPLE);h.invoke('sample.load',sample_id='blank_01')
 h.invoke('method.configure',**dict(METHOD_EXAMPLE,**(method or {})));h.invoke('lid.set',opened=False);h.invoke('purge.set',flow_std_m3_s=2e-3/60);h.invoke('power.set',enabled=True);h.invoke('lamp.set',enabled=True);h.step(6)
 if kind=='blank':return h
 h.invoke('acquisition.start',kind='blank');h.step(200);h.invoke('lid.set',opened=True);h.invoke('sample.unload',recipient_id='rack')
 h.invoke('solution.register',**dict(SAMPLE_EXAMPLE,**(sample or {})));h.invoke('sample.load',sample_id=(sample or {}).get('sample_id','sample_01'));h.invoke('lid.set',opened=False);h.step(120)
 return h
class TestJ1500(unittest.TestCase):
 def unchanged_reject(self,h,cap,**args):
  before=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke(cap,**args)
  self.assertEqual(before,h.debug_snapshot())
 def test_library_real_71_and_units(self):
  h=JascoJ1500Runtime();self.assertEqual(len(h.templates),71)
  for r in h.templates.values():
   self.assertEqual(r['evidence'],'measured_literature');self.assertEqual(len(r['delta_epsilon_m2_mol']),66);self.assertAlmostEqual(r['wavelength_m'][0],175e-9,places=20);self.assertAlmostEqual(sum(r['secondary_structure_fractions']),1.)
 def test_full_blank_then_sample(self):
  h=ready();h.invoke('acquisition.start',kind='sample');h.step(4);self.assertEqual(h.phase,'succeeded');self.assertEqual(len(h.points),41);self.assertFalse(h.shutter);self.assertEqual(h.invoke('spectrum.read')['absorbance_evidence'],'synthetic_host_supplied_independent_mean_absorption')
 def test_setting_does_not_advance(self):
  h=ready();t=h.time_s;h.invoke('method.configure',**METHOD_EXAMPLE);self.assertEqual(h.time_s,t)
 def test_read_describe_are_pure(self):
  h=ready();before=h.debug_snapshot();h.read();h.describe();h.invoke('process.measure');h.invoke('spectrum.read');h.session().read(instance_id=h.instance_id);self.assertEqual(before,h.debug_snapshot())
 def test_invalid_numeric_bool_nan_unknown_atomic(self):
  h=ready()
  for v in [True,float('nan'),float('inf'),-1]:
   self.unchanged_reject(h,'purge.set',flow_std_m3_s=v)
  self.unchanged_reject(h,'power.set',enabled=True,evil='x')
 def test_host_initialization_only(self):
  h=ready();self.unchanged_reject(h,'plant.configure',**PLANT_EXAMPLE)
 def test_purged_snapshot_explicit(self):
  h=JascoJ1500Runtime();self.unchanged_reject(h,'plant.configure',**dict(FAST_PLANT,accept_purged_snapshot=False))
 def test_no_lamp_before_purge(self):
  h=JascoJ1500Runtime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('purge.set',flow_std_m3_s=2e-3/60);self.unchanged_reject(h,'lamp.set',enabled=True);h.step(120);h.invoke('lamp.set',enabled=True);self.assertFalse(h.read()['lamp.ready']);h.step(5);self.assertTrue(h.read()['lamp.ready'])
 def test_ambient_readout_not_temp_control(self):
  h=ready(plant={'ambient_k':300.});self.assertEqual(h.read()['sample.temperature_k'],300.);self.unchanged_reject(h,'thermal.set_temperature',temperature_k=320.)
 def test_access_requires_open(self):
  h=ready();self.unchanged_reject(h,'sample.unload',recipient_id='r')
 def test_duplicate_sample_id_atomic(self):
  h=ready();self.unchanged_reject(h,'solution.register',**SAMPLE_EXAMPLE)
 def test_unknown_sample_and_template(self):
  h=ready();self.unchanged_reject(h,'sample.load',sample_id='unknown');self.unchanged_reject(h,'template.read',substance_id='unknown')
 def test_polarization_absorption_physical_bound(self):
  h=ready();self.unchanged_reject(h,'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='bad',mean_epsilon_m2_mol=[0.]*66))
 def test_rectangular_path_and_volume_proxy(self):
  h=ready()
  self.unchanged_reject(h,'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='short',path_length_m=.0001))
  self.unchanged_reject(h,'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='large',volume_m3=1e-5))
 def test_cylindrical_path_supported(self):
  h=ready();h.invoke('solution.register',**dict(SAMPLE_EXAMPLE,sample_id='cyl',cell_shape='cylindrical',path_length_m=.0001,volume_m3=1e-8));self.assertIn('cyl',h.samples)
 def test_uv_evidence_mandatory(self):
  h=ready();self.unchanged_reject(h,'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='bad',accept_synthetic_uv=False))
 def test_method_ranges_and_grid(self):
  h=ready()
  for args in [dict(start_m=162e-9),dict(stop_m=951e-9),dict(speed_m_s=10001e-9/60),dict(bandwidth_m=17e-9),dict(integration_s=31.),dict(interval_m=3e-9),dict(accumulations=True)]:
   self.unchanged_reject(h,'method.configure',**dict(METHOD_EXAMPLE,**args))
 def test_hardware_range_not_template_range(self):
  h=ready(kind='blank',method={'start_m':940e-9,'stop_m':900e-9});self.unchanged_reject(h,'acquisition.start',kind='blank')
 def test_bandpass_edge_rejected_not_extrapolated(self):
  h=ready(kind='blank',method={'start_m':239e-9,'stop_m':199e-9,'bandwidth_m':2e-9});self.unchanged_reject(h,'acquisition.start',kind='blank')
 def test_purge_threshold_mapping(self):
  h=ready();self.assertAlmostEqual(h._minimum_flow(186e-9),2e-3/60);self.assertAlmostEqual(h._minimum_flow(185e-9),5e-3/60);self.assertAlmostEqual(h._minimum_flow(180e-9),15e-3/60);self.assertGreater(h._minimum_flow(175e-9),15e-3/60)
  with self.assertRaises(ActionRejected):h._minimum_flow(170e-9)
 def test_low_wavelength_requires_more_gas(self):
  h=ready(kind='blank',method={'start_m':226e-9,'stop_m':186e-9})
  h.invoke('method.configure',**dict(METHOD_EXAMPLE,start_m=225e-9,stop_m=185e-9))
  self.unchanged_reject(h,'acquisition.start',kind='blank');h.invoke('purge.set',flow_std_m3_s=5e-3/60);h.invoke('acquisition.start',kind='blank')
 def test_blank_required(self):
  h=ready(kind='blank');h.invoke('lid.set',opened=True);h.invoke('sample.unload',recipient_id='r');h.invoke('solution.register',**SAMPLE_EXAMPLE);h.invoke('sample.load',sample_id='sample_01');h.invoke('lid.set',opened=False);h.step(120);self.unchanged_reject(h,'acquisition.start',kind='sample')
 def test_method_change_invalidates_blank(self):
  h=ready();self.assertTrue(h.read()['blank.ready']);h.invoke('method.configure',**dict(METHOD_EXAMPLE,bandwidth_m=.5e-9));self.assertFalse(h.read()['blank.ready'])
 def test_path_or_solvent_invalidates_blank(self):
  for changed in [dict(path_length_m=.002),dict(solvent_id='different')]:
   h=ready(sample=changed);self.assertFalse(h.read()['blank.ready']);self.unchanged_reject(h,'acquisition.start',kind='sample')
 def test_no_implicit_sample_fill_or_consumption(self):
  h=ready();before=h._ledger();h.invoke('acquisition.start',kind='sample');h.step(5);after=h._ledger();self.assertEqual(before['solution_registered_m3'],after['solution_registered_m3']);self.assertEqual(after['solution_residual_m3'],0.)
 def test_n2_source_and_tagged_gas_conserved(self):
  h=ready();h.step(10);h.invoke('lid.set',opened=True);h.step(11);r=h._ledger();self.assertAlmostEqual(r['nitrogen_source_residual_mol'],0.,places=12);self.assertAlmostEqual(r['purge_label_residual_mol'],0.,places=12);self.assertGreater(r['purge_label_exhaust_mol'],0.)
 def test_source_exhaustion_exact_partial(self):
  h=ready(kind='blank',plant={'initial_n2_mol':.010});h.invoke('acquisition.start',kind='blank');h.step(10);self.assertEqual(h.phase,'failed');self.assertEqual(h.n2,0.);self.assertFalse(h.lamp);self.assertFalse(h.shutter);self.assertLess(len(h.points),41);self.assertGreater(len(h.points),0)
 def test_cancel_retains_completed_points_and_resource_use(self):
  h=ready();h.invoke('acquisition.start',kind='sample');h.step(.6);n=len(h.points);used=h.throughput;h.invoke('acquisition.stop');h.step(1);self.assertEqual(h.phase,'cancelled');self.assertEqual(len(h.points),n);self.assertGreater(h.throughput,used)
 def test_open_lid_interrupts_and_recontaminates(self):
  h=ready();h.invoke('acquisition.start',kind='sample');h.step(.2);h.invoke('lid.set',opened=True);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.air,1.);self.assertFalse(h.shutter)
 def test_poweroff_interrupt_and_blank_loss(self):
  h=ready();h.invoke('acquisition.start',kind='sample');h.step(.2);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertFalse(h.lamp);self.assertIsNone(h.blank)
 def test_lamp_off_shutter_close_purge_reduction_interrupt(self):
  for cap,args in [('lamp.set',dict(enabled=False)),('shutter.set',dict(opened=False)),('purge.set',dict(flow_std_m3_s=0.))]:
   h=ready();h.invoke('acquisition.start',kind='sample');h.step(.2);h.invoke(cap,**args);self.assertIn(h.phase,['cancelled','failed']);self.assertFalse(h.shutter)
 def test_busy_rejected_no_mutation(self):
  h=ready();h.invoke('acquisition.start',kind='sample');self.unchanged_reject(h,'method.configure',**METHOD_EXAMPLE);self.unchanged_reject(h,'acquisition.start',kind='sample')
 def test_step_timing_and_accumulations(self):
  h=ready(kind='blank',method={'accumulations':2});h.invoke('acquisition.start',kind='blank');h.step(4.919);self.assertEqual(h.phase,'running');h.step(.0011);self.assertEqual(h.phase,'succeeded');self.assertEqual(len(h.points),82);self.assertAlmostEqual(h.elapsed_s,82*.06)
 def test_continuous_timing_and_filter(self):
  h=ready(kind='blank',method={'scan_mode':'continuous','speed_m_s':10000e-9/60});h.invoke('acquisition.start',kind='blank');h.step(1);self.assertAlmostEqual(h.elapsed_s,.05+40e-9/(10000e-9/60));self.assertLess(h.points[0]['raw_ellipticity_rad'],h.points[-1]['raw_ellipticity_rad'])
 def test_auto_dit_not_step_alias(self):
  a=ready(kind='blank');b=ready(kind='blank',method={'scan_mode':'auto_dit'})
  for h in [a,b]:h.invoke('acquisition.start',kind='blank');h.step(10)
  self.assertGreater(b.elapsed_s,a.elapsed_s);self.assertGreater(b.points[0]['integration_s'],.05)
 def test_partition_invariance_event_clock(self):
  a=ready(kind='blank',plant={'noise_multiplier':1.});b=copy.deepcopy(a)
  for h in [a,b]:h.invoke('acquisition.start',kind='blank')
  a.step(3)
  for _ in range(150):b.step(.02)
  self.assertEqual(a.phase,b.phase);self.assertEqual(len(a.points),len(b.points));self.assertAlmostEqual(a.n2,b.n2,places=12)
  np.testing.assert_allclose([p['ellipticity_rad'] for p in a.points],[p['ellipticity_rad'] for p in b.points],atol=1e-15)
 def test_cd_and_uv_independent(self):
  a=ready();b=ready(sample={'mean_epsilon_m2_mol':[1200.]*66})
  for h in [a,b]:h.invoke('acquisition.start',kind='sample');h.step(3)
  self.assertAlmostEqual(a.points[-1]['ellipticity_rad'],b.points[-1]['ellipticity_rad'],places=9);self.assertAlmostEqual(b.points[-1]['absorbance']-a.points[-1]['absorbance'],.12,places=8)
 def test_beer_lambert_two_channel_oracle(self):
  h=ready();w=220e-9;s=h._sample();h.templates[s['template_id']]['delta_epsilon_m2_mol']=[.5]*66;theta,a=h._spectral(w);delta=(.5*.2+.05)*.001;mean=(600*.2+50)*.001
  self.assertAlmostEqual(theta,math.atan(math.tanh(CD_FACTOR*delta)),places=13)
  expected=-math.log10((10**(-mean-delta/2)+10**(-mean+delta/2))/2);self.assertAlmostEqual(a,expected,places=13)
 def test_bandwidth_changes_fine_structure(self):
  a=ready();b=ready(method={'bandwidth_m':4e-9})
  for h in [a,b]:h.invoke('acquisition.start',kind='sample');h.step(3)
  self.assertGreater(max(abs(x['ellipticity_rad']-y['ellipticity_rad']) for x,y in zip(a.points,b.points)),1e-8)
 def test_concentration_and_path_scaling(self):
  a=ready();b=ready(sample={'residue_concentration_mol_m3':.4})
  for h in [a,b]:h.invoke('acquisition.start',kind='sample');h.step(3)
  self.assertAlmostEqual(b.points[-1]['ellipticity_rad']/a.points[-1]['ellipticity_rad'],2.,places=4)
 def test_saturation_fails_no_clipping_valid_points(self):
  h=ready(sample={'mean_epsilon_m2_mol':[50000.]*66});h.invoke('acquisition.start',kind='sample');h.step(1);self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'DETECTOR_SATURATED');self.assertEqual(len(h.points),0)
 def test_noise_reproducible_read_does_not_advance_rng(self):
  a=ready(kind='blank',plant={'noise_multiplier':1.});b=copy.deepcopy(a)
  a.read();a.describe()
  for h in [a,b]:h.invoke('acquisition.start',kind='blank');h.step(4)
  self.assertEqual(a.points,b.points)
 def test_two_instances_isolated(self):
  a=ready();b=ready();before=b.debug_snapshot();a.invoke('acquisition.start',kind='sample');a.step(1);self.assertEqual(before,b.debug_snapshot())
 def test_agent_truth_and_host_config_denied(self):
  h=ready();s=h.session();self.assertNotIn('truth.ledger',s.describe(instance_id=h.instance_id)['state_slots'])
  with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
  r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='secret');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
  self.assertNotIn('active_template_id',json.dumps(s.read(instance_id=h.instance_id)))
 def test_missing_observations_null_unavailable(self):
  h=ready();v=h.session().read(instance_id=h.instance_id,state_ids=['scan.wavelength_m'])['observations']['scan.wavelength_m']
  # Previous completed blank points remain a valid observation until the next start, so use a fresh host.
  h=JascoJ1500Runtime();v=h.session().read(instance_id=h.instance_id,state_ids=['scan.wavelength_m'])['observations']['scan.wavelength_m'];self.assertIsNone(v['value']);self.assertEqual(v['quality'],'unavailable')
 def test_request_dedup_conflict_and_owner(self):
  h=ready();s=h.session();args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'kind':'sample'},request_id='one')
  r=s.invoke(**args);h.step(.1);self.assertEqual(s.invoke(**args),r)
  bad=s.invoke(**dict(args,arguments={'kind':'blank'}));self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT')
  h.register_principal('other');other=h.session('other')
  with self.assertRaises(ActionRejected):other.poll(r['operation_id'])
  s.cancel(r['operation_id'],'cancel');self.assertEqual(s.poll(r['operation_id'])['status'],'cancelled')
 def test_wrong_instance_and_principal(self):
  h=ready()
  with self.assertRaises(ActionRejected):h.session('self_declared_evaluator')
  r=h.session().invoke(instance_id='wrong',capability_id='power.set',arguments={'enabled':True},request_id='x');self.assertEqual(r['error']['code'],'UNKNOWN_INSTANCE')
 def test_write_routes_and_unwritable_denied(self):
  h=ready();s=h.session();s.write(instance_id=h.instance_id,state_id='lid.opened',value=True,request_id='x');self.assertTrue(h.opened)
  with self.assertRaises(ActionRejected):s.write(instance_id=h.instance_id,state_id='sample.temperature_k',value=320.,request_id='x')
 def test_unsupported_reported_not_success(self):
  h=ready();r=h.session().invoke(instance_id=h.instance_id,capability_id='thermal.set_temperature',arguments={'temperature_k':320.},request_id='u');self.assertEqual(r['status'],'unsupported');self.assertEqual(r['error']['code'],'UNSUPPORTED')
 def test_fit_pure_and_template_reconstruction(self):
  h=ready();h.invoke('acquisition.start',kind='sample');h.step(3);before=h.debug_snapshot();r=h.invoke('structure.fit',residue_concentration_mol_m3=.2,path_length_m=.001);self.assertEqual(before,h.debug_snapshot());self.assertTrue(r['converged']);self.assertAlmostEqual(sum(r['fractions'].values()),1.);self.assertLess(r['rms_delta_epsilon_m2_mol'],1e-4);self.assertAlmostEqual(r['fractions']['Helix'],.72,places=3)
 def test_fit_rejects_partial_and_narrow_spectrum(self):
  h=ready();self.unchanged_reject(h,'structure.fit',residue_concentration_mol_m3=.2,path_length_m=.001)
  h=ready(method={'start_m':220e-9,'stop_m':210e-9});h.invoke('acquisition.start',kind='sample');h.step(3);self.unchanged_reject(h,'structure.fit',residue_concentration_mol_m3=.2,path_length_m=.001)
 def test_step_invalid_bool_and_negative(self):
  h=ready()
  for dt in [True,-1,float('nan')]:
   before=h.debug_snapshot()
   with self.assertRaises(ActionRejected):h.step(dt)
   self.assertEqual(before,h.debug_snapshot())
 def test_external_inputs_defensively_copied(self):
  h=ready();a=copy.deepcopy(SAMPLE_EXAMPLE);a['sample_id']='new';h.invoke('solution.register',**a);a['mean_epsilon_m2_mol'][0]=999.;self.assertEqual(h.samples['new']['mean_epsilon_m2_mol'][0],600.)
 def test_same_solvent_id_cannot_have_conflicting_definition(self):
  h=ready();self.unchanged_reject(h,'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='bad_buffer',solvent_alpha_m_inv=[100.]*66))
if __name__=='__main__':unittest.main()

