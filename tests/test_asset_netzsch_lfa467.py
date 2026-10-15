"""Actual LFA process/protocol/analysis regression tests; no Isaac required."""
import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import netzsch_lfa467 as r
OBSERVED=[];STATES=[];SCHEMA_REPORT=[]
class Host(r.NetzschLfa467Runtime):
 def invoke(self,k,**a):
  before=self.read();out=super().invoke(k,**a);after=self.read();changed={s for s in after if after[s]!=before[s]}
  if changed-set(r.CAPS[k]['effects']):raise AssertionError((k,'undeclared effects',changed-set(r.CAPS[k]['effects'])))
  OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(out)));STATES.append(after);json.dumps(out,allow_nan=False);return out
 def step(self,dt_s):
  super().step(dt_s);STATES.append(self.read());json.dumps(STATES[-1],allow_nan=False)
def make(plant=None,sample=None,method=None):
 h=Host();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,cells=32,furnace_speed_m_s=.1,sampler_speed_m_s=.1,**(plant or {})));h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,**(sample or {})));h.invoke('power.set',enabled=True)
 for g in r.CHANNELS:h.invoke('gas.load',**dict(r.GAS_EXAMPLE,channel=g));h.invoke('gas.set_open',channel=g,opened=True)
 h.invoke('coolant.load',**r.COOLANT_EXAMPLE);h.invoke('detector.set_enabled',enabled=True);h.invoke('pulse.charge',energy_j=1.,accept_optical_budget=True);h.invoke('furnace.set',opened=False);h.step(1.2);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,**(method or {})));return h

def run(h):h.invoke('acquisition.start');h.step(1.);return h.invoke('acquisition.result')
def close(h):
 if h.phase=='running':h.invoke('acquisition.stop')
 h.invoke('thermal.disable');h.invoke('detector.set_enabled',enabled=False)
 for g in r.CHANNELS:h.invoke('gas.set_open',channel=g,opened=False)
 h.invoke('furnace.set',opened=True);h.step(1.2);h.invoke('sample.remove');h.invoke('pulse.discharge')
 for g in r.CHANNELS:h.invoke('gas.unload',channel=g)
 for tank in ['detector','furnace']:
  if h.coolant_present[tank]:h.invoke('coolant.unload',reservoir=tank)
 h.invoke('power.set',enabled=False);return h.invoke('ledger.read')

def usable_references():
 return {k:v for k,v in r.library()['substances'].items() if v.get('runtime_usable')}
def reference_case(rec):
 """Ambient/method temperature inside the record domain, thickness for ~30 ms half-rise, ~0.5 K adiabatic rise."""
 d=rec['runtime_domain'];T=rec['values']['temperature_k'];ext=d.get('constant_alpha_extension_k',0.)
 lo=max(T[0]-ext,d.get('minimum_temperature_k',-1e9),283.15);hi=min(T[-1]+ext,d.get('maximum_temperature_k',1e9),313.15)
 assert lo<=hi,(lo,hi);amb=min(max(298.15,lo),hi);alpha=r.source_alpha(rec,amb);v=rec['values']
 rho=v.get('density_kg_m3') or v.get('density_20C_kg_m3');rho=rho[0] if isinstance(rho,list) else rho
 cp=v.get('specific_heat_j_kg_k') or v.get('heat_capacity_22C_j_kg_k');cp=cp[0] if isinstance(cp,list) else cp
 if rho is None or cp is None:
  vol=v.get('volumetric_heat_capacity_22C_j_m3_k');rho=8000. if vol else 2600.;cp=vol/rho if vol else 800.
 L=min(math.sqrt(alpha*.03/r.PARKER_HALF),.004);area=math.pi*.0127**2/4;energy=min(.5*rho*cp*area*L/.8,1.)
 return amb,alpha,dict(reference_id=None,thickness_m=L,density_kg_m3=float(rho),heat_capacity_j_kg_k=float(cp),front_loss_w_m2_k=0.,rear_loss_w_m2_k=0.),dict(temperature_k=amb,pulse_energy_j=energy,equilibrium_dwell_s=0.)

class LfaTests(unittest.TestCase):
 def assertBudget(self,h,tol=2e-8):
  d=h.invoke('ledger.read')
  for k,v in d.items():
   if 'error' in k:self.assertLess(abs(v),tol,(k,v))
 def test_01_idle_clock_configuration(self):
  h=Host();h.step(.000005);self.assertEqual(h.time_s,.000005);self.assertIsNone(h.plant);h.invoke('plant.configure',**r.PLANT_EXAMPLE);self.assertFalse(h.read()['power.enabled'])
 def test_02_zero_step_pure(self):
  h=make(method={'equilibrium_dwell_s':0.,'pretrigger_s':0.});h.invoke('acquisition.start');before=h.read();h.invoke('sim.step',dt_s=0.);self.assertEqual(before,h.read());self.assertEqual(h.stage,'equilibrating')
 def test_03_real_sic_observations(self):
  h=make();out=run(h);self.assertEqual(out['phase'],'succeeded');self.assertGreater(max(x['temperature_k'] for x in out['rows'])-out['rows'][0]['temperature_k'],.1);self.assertNotIn('diffusivity_m2_s',out);self.assertBudget(h)
 def test_04_real_alumina_slower(self):
  a=make();b=make(sample={'reference_id':'nist_alumina','density_kg_m3':3984.,'heat_capacity_j_kg_k':755.});run(a);run(b);self.assertGreater(a.rows[210]['temperature_k']-a.rows[0]['temperature_k'],b.rows[210]['temperature_k']-b.rows[0]['temperature_k']);self.assertBudget(b)
 def test_05_microsecond_cancel(self):
  h=make(method={'equilibrium_dwell_s':0.,'pretrigger_s':0.,'pulse_width_s':10e-6,'sample_interval_s':.5e-6,'record_duration_s':.001});h.invoke('acquisition.start');h.step(5e-6);self.assertEqual(h.stage,'pulse');h.invoke('acquisition.stop');self.assertAlmostEqual(h.optical_emitted,.05,12);self.assertAlmostEqual(h.optical_stored,.95,12);self.assertGreater(len(h.rows),5);self.assertBudget(h)
 def test_06_two_mhz_internal_clock(self):
  h=make(method={'equilibrium_dwell_s':0.,'pretrigger_s':0.,'sample_interval_s':.5e-6,'record_duration_s':.001});start=h.time_s;h.invoke('acquisition.start');h.step(.1);self.assertEqual(h.time_s,start+.1);self.assertEqual(len(h.rows),2001);self.assertAlmostEqual(h.rows[1]['time_s']-h.rows[0]['time_s'],.5e-6,12)
 def test_07_partition_invariance(self):
  a=make(method={'equilibrium_dwell_s':0.});b=make(method={'equilibrium_dwell_s':0.});a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(.3)
  for dt in [.003,.008,.027,.062,.2]:b.step(dt)
  np.testing.assert_allclose(a.theta,b.theta,atol=1e-10,rtol=1e-9);self.assertEqual(a.rows,b.rows);self.assertBudget(b)
 def test_08_neumann_constant_stiff(self):
  for hface in [0.,10.]:
   m=r.thermal_modes(1e-5,1e-4,3000,1000,.002,hface,hface,64,20,0);q=np.full(65,5.);out=r.propagate_modes(m,q,0,0,120);np.testing.assert_allclose(out['temperature_delta_k'],q,atol=1e-11,rtol=0);self.assertLess(abs(out['stored_change_j']),1e-10)
 def test_09_neumann_continuum_convergence(self):
  t=np.array([.0005,.001,.003,.01]);exact=r.parker_response(5e-5*t/.002**2);errs=[]
  for n in [32,64]:
   m=r.thermal_modes(.002,1,3000,1000,5e-5,0,0,n,20,0);q=np.zeros(n+1);q[1]=1/m['C'][1];out=r.propagate_modes(m,q,0,0,float(t[-1]),sample_times_s=t);errs.append(max(abs(out['rear_delta_k']*.002*3e6-exact)))
  self.assertLess(errs[1],errs[0]*.3)
 def test_10_robin_conservation(self):
  m=r.thermal_modes(.002,1e-4,3000,1000,5e-5,25,60,64,20,.1);q=np.linspace(1,10,65);o=r.propagate_modes(m,q,3,2,.25);self.assertAlmostEqual(o['heat_input_j']-o['ambient_j'],o['stored_change_j'],places=9)
 def test_11_detector_resonance(self):
  tau=.02;A,B,_,_=r.detector_kernels(np.array([-1/tau,0.]),[.001,.1],tau);np.testing.assert_allclose(A[0],np.array([.001,.1])/tau*np.exp(-np.array([.001,.1])/tau),rtol=1e-12);self.assertTrue(np.all(np.isfinite(B)))
 def test_12_sensor_saturation_not_clip(self):
  h=make(plant={'detector_maximum_k':298.16});out=run(h);self.assertTrue(any(x['temperature_k'] is None for x in out['rows']));self.assertGreater(h._rear(),298.16)
 def test_13_gain_calibration_independent(self):
  h=make(plant={'detector_gain':2.,'detector_offset_k':3.});h.invoke('sensor.calibrate',**dict(r.CALIBRATION_EXAMPLE,temperature_gain_correction=.5,temperature_zero_k=3.));out=run(h);self.assertAlmostEqual(out['rows'][0]['temperature_k'],298.15,places=5)
 def test_14_parker_external(self):
  h=Host();t=np.linspace(0,.1,1001);args=dict(time_s=t.tolist(),temperature_k=(300+r.parker_response(5e-5*t/.002**2)).tolist(),thickness_m=.002,pulse_time_s=0.,baseline_k=300.,plateau_k=301.,accept_adiabatic_instant_pulse=True);a=h.invoke('analysis.parker',**args);self.assertLess(abs(a['diffusivity_m2_s']/5e-5-1),1e-4)
 def test_15_parker_missing_gap(self):
  out=r.analyze_parker([0,.01,.02,.03],[300,300.1,None,301],.002,0,300,301,True);self.assertIsNone(out['diffusivity_m2_s'])
 def test_16_finite_pulse_external_fit(self):
  h=Host();t=np.linspace(-.001,.08,301);a=dict(r.FIT_EXAMPLE,time_s=t.tolist(),temperature_k=(300+1000*r.finite_profile(t,4e-5,.002,2.5e6,30,15,.001,.0002,32)).tolist(),pulse_width_s=.001,volumetric_capacity_j_m3_k=2.5e6,front_loss_w_m2_k=30.,rear_loss_w_m2_k=15.,detector_tau_s=.0002,cells=32);out=h.invoke('analysis.finite_pulse',**a);self.assertLess(abs(out['diffusivity_m2_s']/4e-5-1),1e-5);self.assertLess(out['fit_rms_k'],1e-7)
 def test_17_conductivity_external(self):
  h=Host();o=h.invoke('analysis.conductivity',**r.CAPS['analysis.conductivity']['example_arguments']);self.assertAlmostEqual(o['conductivity_w_m_k'],112.97)
 def test_18_reference_exact_and_domain(self):
  h=make(plant={'cooler':'LN2'});out=h.invoke('reference.query',reference_id='nist_sic');self.assertEqual(json.loads(out['reference_json'])['values'],r.library()['substances']['nist_sic']['values']);self.assertRaisesRegex(r.ActionRejected,'REFERENCE_TEMPERATURE',h.invoke,'method.configure',**dict(r.METHOD_EXAMPLE,temperature_k=280.))
 def test_19_size_and_unknown_atomic(self):
  h=Host();h.invoke('plant.configure',**r.PLANT_EXAMPLE);before=h.read()
  for patch in [{'thickness_m':.007},{'diameter_m':.005},{'bad':1},{'density_kg_m3':float('nan')}]:self.assertRaises(r.ActionRejected,h.invoke,'sample.load',**dict(r.SAMPLE_EXAMPLE,**patch));self.assertEqual(before,h.read())
 def test_20_pulse_grid_and_record_limit(self):
  h=make()
  for patch in [{'pulse_width_s':10.5e-6},{'sample_interval_s':.5e-6,'record_duration_s':1.},{'pulse_width_s':.0015,'record_duration_s':.001}]:self.assertRaises(r.ActionRejected,h.invoke,'method.configure',**dict(r.METHOD_EXAMPLE,**patch))
 def test_21_detector_inventory_fault(self):
  h=make(method={'equilibrium_dwell_s':0.});h.invoke('detector.set_enabled',enabled=False);h.invoke('coolant.unload',reservoir='detector');h.invoke('coolant.load',**dict(r.COOLANT_EXAMPLE,amount_kg=1e-9));h.invoke('detector.set_enabled',enabled=True);out=run(h);self.assertEqual(out['error_code'],'DETECTOR_COOLANT_EXHAUSTED');self.assertEqual(h.coolant_remaining['detector'],0.);self.assertBudget(h)
 def test_22_gas_fault_and_channels(self):
  h=make(method={'equilibrium_dwell_s':0.});h.invoke('gas.set_open',channel='protective',opened=False);h.invoke('gas.unload',channel='protective');h.invoke('gas.load',**dict(r.GAS_EXAMPLE,channel='protective',amount_mol=1e-12));h.invoke('gas.set_open',channel='protective',opened=True);o=run(h);self.assertEqual(o['error_code'],'GAS_EXHAUSTED');self.assertBudget(h)
 def test_23_furnace_ln2_finite(self):
  h=make(plant={'cooler':'LN2'});h.invoke('coolant.load',**dict(r.COOLANT_EXAMPLE,reservoir='furnace',amount_kg=1e-10));h.invoke('thermal.set_temperature',temperature_k=295.,rate_k_s=.5);h.step(.1);self.assertEqual(h.coolant_remaining['furnace'],0.);self.assertFalse(h.thermal_enabled);self.assertBudget(h)
 def test_24_detector_cooler_domain(self):
  h=make(plant={'cooler':'LN2','detector':'InSb'});self.assertRaisesRegex(r.ActionRejected,'CONFIGURATION_TEMPERATURE_RANGE',h.invoke,'thermal.set_temperature',temperature_k=295.,rate_k_s=.5)
 def test_25_passive_cooling_no_ambient_clip(self):
  h=make();h.invoke('thermal.set_temperature',temperature_k=303.15,rate_k_s=50/60);h.step(1.);self.assertGreater(h._furnace(),298.15);h.invoke('thermal.disable');h.invoke('thermal.set_cooling',enabled=True);a=h._furnace();h.step(1.);self.assertLess(h._furnace(),a);self.assertGreaterEqual(h._furnace(),298.15);self.assertBudget(h)
 def test_26_optical_finite_capacity(self):
  h=make();before=h._ledger();self.assertRaisesRegex(r.ActionRejected,'OPTICAL_CAPACITY',h.invoke,'pulse.charge',energy_j=10.,accept_optical_budget=True);self.assertEqual(before,h._ledger());h.invoke('pulse.discharge');self.assertRaisesRegex(r.ActionRejected,'INSUFFICIENT_OPTICAL',h.invoke,'acquisition.start')
 def test_27_heat_domain_rejection_preserves_energy(self):
  h=make(method={'pulse_energy_j':1.},plant={'maximum_local_temperature_rise_k':.01});out=run(h);self.assertEqual(out['error_code'],'PULSE_LINEAR_DOMAIN');self.assertEqual(h.optical_emitted,0.);self.assertBudget(h)
 def test_28_timeout_preserves_heating(self):
  h=make(method={'temperature_k':303.15,'timeout_s':.05});out=run(h);self.assertEqual(out['error_code'],'TIMEOUT');self.assertGreater(h.heater_input,0);self.assertBudget(h)
 def test_29_frozen_cancel_restart(self):
  h=make();h.invoke('acquisition.start');h.step(.21);h.invoke('acquisition.stop');old=h.invoke('acquisition.result');op=old['operation_id'];h.step(.2);self.assertEqual(old,h.invoke('acquisition.result'));h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,equilibrium_tolerance_k=.5));h.invoke('acquisition.start');h.step(1);self.assertEqual(old,h.records[op]);self.assertEqual(h.phase,'succeeded')
 def test_30_emergency_and_reset(self):
  h=make();h.invoke('acquisition.start');h.step(.21);h.invoke('emergency.stop');self.assertEqual(h.error,'SOFTWARE_STOP');self.assertFalse(h._detector_ready());h.invoke('emergency.reset');self.assertFalse(h.emergency)
 def test_31_motion_16slots_and_access(self):
  h=make();self.assertRaisesRegex(r.ActionRejected,'FURNACE_NOT_OPEN',h.invoke,'sampler.select',position=16);h.invoke('furnace.set',opened=True);h.step(1.2);h.invoke('sampler.select',position=16);self.assertNotEqual(h.sampler_x,h.sampler_target_x);h.step(1);self.assertEqual((h.sampler_x,h.sampler_y),(-.028,-.028));self.assertRaisesRegex(r.ActionRejected,'SAMPLE_ACCESS',h.invoke,'sample.remove');h.invoke('sampler.select',position=1);h.step(1);self.assertEqual((h.sampler_x,h.sampler_y),(.028,.028))
 def test_32_mass_heat_cleanup(self):
  h=make();run(h);d=close(h);self.assertEqual(d['sample_present_kg'],0);self.assertGreater(d['recovered_sensible_j'],0);self.assertBudget(h)
 def test_33_session_truth_permissions(self):
  h=make();s=h.session();desc=s.describe(instance_id=h.instance_id);self.assertNotIn('truth.alpha_m2_s',desc['state_slots']);self.assertNotIn('plant.configure',desc['capabilities']);self.assertRaisesRegex(r.ActionRejected,'ACCESS_DENIED',s.read,instance_id=h.instance_id,state_ids=['truth.alpha_m2_s']);out=s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='x');self.assertEqual(out['error']['code'],'ACCESS_DENIED')
 def test_34_owner_poll_cancel_result(self):
  h=make();h.register_principal('other');a=h.session();b=h.session('other');out=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');op=out['operation_id'];self.assertRaisesRegex(r.ActionRejected,'ACCESS_DENIED',b.poll,op);self.assertRaisesRegex(r.ActionRejected,'ACCESS_DENIED',b.cancel,op,'c');self.assertEqual(b.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='r')['error']['code'],'ACCESS_DENIED');self.assertEqual(a.cancel(op,'c')['status'],'succeeded');self.assertEqual(a.poll(op)['status'],'cancelled')
 def test_35_idempotence_conflict(self):
  h=make();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='same');x=s.invoke(**a);self.assertEqual(s.invoke(**a),x);bad=s.invoke(**dict(a,capability_id='acquisition.stop'));self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT');s.cancel(x['operation_id'],'c');self.assertEqual(s.cancel(x['operation_id'],'c')['status'],'succeeded')
 def test_36_no_query_clock(self):
  h=make();before=h.read();rev=h.revision;h.invoke('reference.query',reference_id='nist_sic');h.invoke('acquisition.result');h.invoke('ledger.read');h.session().read(instance_id=h.instance_id);self.assertEqual(h.read(),before);self.assertEqual(h.revision,rev)
 def test_37_instances_isolated(self):
  a=make();b=make();a.invoke('acquisition.start');a.step(.4);self.assertEqual(b.phase,'idle');self.assertEqual(b.time_s,1.2);self.assertEqual(b.optical_emitted,0.)
 def test_38_unknown_roles_targets_writes(self):
  h=make();s=h.session();self.assertRaises(r.ActionRejected,h.session,'unknown');self.assertRaises(r.ActionRejected,s.read,instance_id='bad');self.assertRaises(r.ActionRejected,s.write,instance_id=h.instance_id,state_id='thermal.target_k',value=300.,request_id='w');self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='wrong',arguments={},request_id='a')['status'],'rejected')
 def test_39_unsupported_and_atomic(self):
  h=make();before=h.read()
  for k in r.UNSUPPORTED:self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED',h.invoke,k)
  self.assertEqual(before,h.read())
 def test_40_all_schema_json(self):
  meta=r.export_netzsch_lfa467_contract_metadata();self.assertEqual(set(meta['state_slots']),set(r.SLOTS));json.dumps(meta,allow_nan=False);h=make();run(h);json.dumps(h.session('evaluator').read(instance_id=h.instance_id),allow_nan=False)
 def test_41_resource_subns(self):
  h=make();h.invoke('gas.set_open',channel='purge2',opened=False);h.invoke('gas.unload',channel='purge2');h.invoke('gas.load',**dict(r.GAS_EXAMPLE,channel='purge2',amount_mol=1e-18));h.invoke('gas.set_open',channel='purge2',opened=True);h.step(1e-9);self.assertEqual(h.gas_remaining['purge2'],0);self.assertBudget(h)
 def test_42_flow_public_and_condition_acceptance(self):
  h=make();h.invoke('gas.set_flow',channel='purge1',flow_m3_s=50e-6/60);self.assertRaises(r.ActionRejected,h.invoke,'method.configure',**dict(r.METHOD_EXAMPLE,accept_condition_transfer=False));self.assertEqual(h.gas_flow['purge1'],50e-6/60)
 def test_43_power_loss_partial(self):
  h=make(method={'equilibrium_dwell_s':0.,'pretrigger_s':0.,'pulse_width_s':10e-6});h.invoke('acquisition.start');h.step(5e-6);h.invoke('power.set',enabled=False);self.assertEqual(h.error,'POWER_LOSS');self.assertAlmostEqual(h.optical_emitted,.05,12);self.assertBudget(h)
 def test_44_every_runtime_reference_flash_and_parker_recovery(self):
  refs=usable_references();self.assertGreaterEqual(len({v['material_identity'] for v in refs.values()}),50)
  for rid,rec in refs.items():
   with self.subTest(reference=rid):
    amb,alpha,sample,method=reference_case(rec);sample['reference_id']=rid
    h=make(plant={'ambient_k':amb,'safe_access_temperature_k':max(amb,313.15)},sample=sample,method=method);out=run(h)
    self.assertEqual(out['phase'],'succeeded',out['error_code']);self.assertEqual(out['reference_id'],rid)
    self.assertEqual(json.loads(out['template_source']),rec['source']);self.assertIn('Reference alpha record evidence: '+rec['evidence']['level'],out['evidence'])
    rows=[x for x in out['rows'] if x['temperature_k'] is not None];base=rows[0]['temperature_k'];top=max(x['temperature_k'] for x in rows);self.assertGreater(top-base,.3)
    a=h.invoke('analysis.parker',time_s=[x['time_s'] for x in rows],temperature_k=[x['temperature_k'] for x in rows],thickness_m=sample['thickness_m'],pulse_time_s=out['pulse_time_s'],baseline_k=base,plateau_k=top,accept_adiabatic_instant_pulse=True)
    self.assertTrue(a['bracketed']);self.assertLess(abs(a['diffusivity_m2_s']/alpha-1),.01,(rid,a['diffusivity_m2_s'],alpha))
 def test_45_reference_only_records_rejected(self):
  lib=r.library()['substances']
  for rid in ['usgs_sodalite','usgs_spessartite','usgs_cerussite','pulse_tungsten_200','pulse_alumina_10171']:
   self.assertFalse(lib[rid]['runtime_usable']);h=Host();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,furnace_speed_m_s=.1,sampler_speed_m_s=.1));before=h.read()
   self.assertRaisesRegex(r.ActionRejected,'INVALID_REFERENCE',h.invoke,'sample.load',**dict(r.SAMPLE_EXAMPLE,reference_id=rid));self.assertEqual(before,h.read())
 def test_46_single_temperature_constant_band_is_explicit(self):
  lib=r.library()['substances'];q=lib['usgs_quartz'];T=q['values']['temperature_k'][0];a=q['values']['thermal_diffusivity_m2_s'][0]
  self.assertEqual(q['runtime_domain']['constant_alpha_extension_k'],15.);self.assertEqual(r.source_alpha(q,T-15.),a);self.assertEqual(r.source_alpha(q,T+15.),a)
  self.assertRaisesRegex(r.ActionRejected,'REFERENCE_TEMPERATURE_RANGE',r.source_alpha,q,T+15.01)
  bad=copy.deepcopy(lib['nist_sic']);bad['runtime_domain']['constant_alpha_extension_k']=5.;self.assertRaisesRegex(r.ActionRejected,'INVALID_REFERENCE',r.source_alpha,bad,300.)
  self.assertRaisesRegex(r.ActionRejected,'REFERENCE_TEMPERATURE_RANGE',r.source_alpha,lib['nist_sic'],292.)
  self.assertRaisesRegex(r.ActionRejected,'REFERENCE_TEMPERATURE_RANGE',r.source_alpha,lib['native_lfa467_an048'],298.15)
 def test_47_derived_records_state_formula_and_inputs(self):
  for rid,rec in usable_references().items():
   with self.subTest(reference=rid):
    v=rec['values'];self.assertIn(rec['evidence']['level'],['measured','measured_literature','derived','assumption']);self.assertTrue(rec['source'].get('file') and rec['source'].get('sha256') and rec['source'].get('locator'))
    if rec['evidence']['level']=='derived':
     self.assertEqual(rec['evidence']['alpha_formula'],'alpha=k/(rho*cp)');self.assertTrue(rec['source']['derivation_sources'])
     np.testing.assert_allclose(v['thermal_diffusivity_m2_s'],np.asarray(v['thermal_conductivity_w_m_k'])/(np.asarray(v['density_kg_m3'])*np.asarray(v['specific_heat_j_kg_k'])),rtol=1e-12)
 def test_48_class_sample_alpha_ordering_and_temperature_lookup(self):
  lib=r.library()['substances']
  # Metals (measured Parker, derived elements) diffuse faster than ceramics, minerals and the native Pyroceram curve.
  self.assertGreater(r.source_alpha(lib['parker1961_copper_ofhc'],298.15),r.source_alpha(lib['element_w'],298.15))
  self.assertGreater(r.source_alpha(lib['element_w'],298.15),r.source_alpha(lib['nist_sic'],298.15))
  self.assertGreater(r.source_alpha(lib['nist_sic'],298.15),r.source_alpha(lib['usgs_pyrite'],298.15))
  self.assertGreater(r.source_alpha(lib['usgs_pyrite'],298.15),r.source_alpha(lib['usgs_phlogopite'],298.15))
  self.assertAlmostEqual(r.source_alpha(lib['element_mo'],650.),(lib['element_mo']['values']['thermal_diffusivity_m2_s'][4]+lib['element_mo']['values']['thermal_diffusivity_m2_s'][5])/2,places=15)
  h=make(plant={'ambient_k':303.84053708439893},sample={'reference_id':'native_lfa467_an048','density_kg_m3':2600.,'heat_capacity_j_kg_k':800.,'thickness_m':.001},method={'temperature_k':303.84053708439893,'equilibrium_dwell_s':0.})
  out=run(h);self.assertEqual(out['phase'],'succeeded');self.assertIn('(plot-digitized)',out['evidence'])
if __name__=='__main__':unittest.main()
