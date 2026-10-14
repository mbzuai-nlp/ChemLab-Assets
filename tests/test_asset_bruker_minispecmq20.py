"""CHEM-066 real entry, Bloch, event timing, evidence and protocol regressions."""
import copy,json,math,unittest
import numpy as np
from scripts.asset_runtime.bruker_minispecmq20 import *
def ready(method=None,plant=None,sample=None):
 h=BrukerMinispecMq20Runtime();h.invoke('plant.configure',**dict(FAST_PLANT,**(plant or {})))
 h.invoke('solution.register',**dict(SAMPLE_EXAMPLE,**(sample or {})));h.invoke('sample.load',sample_id=(sample or {}).get('sample_id','sample_01'));h.invoke('method.configure',**dict(METHOD_EXAMPLE,**(method or {})));h.invoke('power.set',enabled=True);h.invoke('lid.set',opened=False);return h
def run(h,dt=100.):
 h.invoke('acquisition.start');h.step(dt);return h
class TestMq20(unittest.TestCase):
 def reject(self,h,k,**a):
  b=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke(k,**a)
  self.assertEqual(b,h.debug_snapshot())
 def test_library55_measured_pair26(self):
  h=BrukerMinispecMq20Runtime();self.assertEqual(len(h.templates),55);self.assertEqual(sum(v['r2_m3_mol_s'] is not None for v in h.templates.values()),26)
  for r in h.templates.values():self.assertEqual(r['conditions']['frequency_hz'],20e6);self.assertEqual(r['evidence'],'measured_literature')
 def test_no_false_r2_default(self):
  h=ready();s=dict(SAMPLE_EXAMPLE,sample_id='extra',template_id='pmc4095929_gd3_dotalap',medium_id='pbs',accept_unvalidated_concentration=True)
  self.reject(h,'solution.register',**s);h.invoke('solution.register',**dict(s,missing_r2_m3_mol_s=8.))
 def test_bloch_rate_consistency(self):
  self.reject(ready(),'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='bad',baseline_r1_s_inv=100.,baseline_r2_s_inv=.01))
 def test_no_measured_override(self):self.reject(ready(),'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='x',missing_r2_m3_mol_s=9.))
 def test_medium_condition(self):self.reject(ready(),'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='x',medium_id='plasma'))
 def test_concentration_range(self):self.reject(ready(),'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='x',metal_concentration_mol_m3=.2))
 def test_explicit_unknowns(self):self.reject(ready(),'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='x',accept_incomplete_source_conditions=False))
 def test_baseline_explicit(self):self.reject(ready(),'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='x',accept_synthetic_baselines=False))
 def test_tube_envelope(self):self.reject(ready(),'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='x',tube_od_m=.018))
 def test_nullable_not_garbage(self):self.reject(ready(),'solution.register',**dict(SAMPLE_EXAMPLE,sample_id='x',pH='7'))
 def test_numeric_invalid_atomic(self):
  for v in [True,float('nan'),float('inf'),-1,301.]:self.reject(ready(),'method.configure',**dict(METHOD_EXAMPLE,rf_power_w=v))
 def test_unknown_argument(self):self.reject(ready(),'power.set',enabled=True,unexpected=1)
 def test_negative_step(self):
  h=ready();b=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.step(-1)
  self.assertEqual(b,h.debug_snapshot())
 def test_dt_zero_pure(self):
  h=ready();b=h.debug_snapshot();h.step(0);self.assertEqual(b,h.debug_snapshot())
 def test_config_at_existing_clock(self):
  h=BrukerMinispecMq20Runtime();h.step(1);h.invoke('plant.configure',**FAST_PLANT);self.assertEqual(h.time_s,1)
 def test_reconfigure_blocked(self):self.reject(ready(),'plant.configure',**FAST_PLANT)
 def test_settings_no_time(self):
  h=ready();t=h.time_s;h.invoke('method.configure',**CPMG_METHOD);self.assertEqual(t,h.time_s)
 def test_pure_read_describe_query(self):
  h=run(ready());b=h.debug_snapshot();h.read();h.describe();h.invoke('relaxation.fit');h.invoke('process.measure');h.invoke('signal.read');h.session().read(instance_id=h.instance_id);self.assertEqual(b,h.debug_snapshot())
 def test_fixed_magnet_when_off(self):
  h=ready();h.invoke('power.set',enabled=False);self.assertEqual(h.read()['magnet.field_t'],.47);self.reject(h,'magnet.set_frequency',frequency_hz=60e6)
 def test_diffusion_unsupported(self):self.reject(ready(),'diffusion.measure')
 def test_vt_required(self):self.reject(ready(plant={'vt_installed':False}),'thermal.set_temperature',temperature_k=298.15)
 def test_vt_not_instant_and_energy(self):
  h=ready();h.invoke('thermal.set_temperature',temperature_k=300.);self.assertEqual(h.temperature,310.15);h.step(1);self.assertAlmostEqual(h.temperature,300+10.15/math.e);self.assertAlmostEqual(h._ledger()['thermal_residual_j'],0)
 def test_vt_range(self):self.reject(ready(),'thermal.set_temperature',temperature_k=338.16)
 def test_temperature_matching(self):
  h=ready();h.invoke('thermal.set_temperature',temperature_k=298.15);h.step(20);self.reject(h,'acquisition.start')
 def test_timing20ns(self):self.reject(ready(),'method.configure',**dict(METHOD_EXAMPLE,pulse90_s=50.01e-6))
 def test_receiver_dead_time_grid(self):
  h=BrukerMinispecMq20Runtime();self.reject(h,'plant.configure',**dict(FAST_PLANT,receiver_dead_s=10.01e-6))
 def test_cpmg_half_delay_grid(self):self.reject(ready(),'method.configure',**dict(CPMG_METHOD,echo_spacing_s=.00500002))
 def test_delay_grid(self):self.reject(ready(),'method.configure',**dict(METHOD_EXAMPLE,points=7))
 def test_cpmg_overlap(self):self.reject(ready(),'method.configure',**dict(CPMG_METHOD,echo_spacing_s=20e-6))
 def test_t1_observed_fit(self):
  h=run(ready());f=h.invoke('relaxation.fit');self.assertEqual(f['quantity'],'T1');self.assertAlmostEqual(f['rate_s_inv'],4.2,delta=.015);self.assertGreater(f['r_squared'],.999)
 def test_t2_observed_fit(self):
  h=run(ready(method=CPMG_METHOD));f=h.invoke('relaxation.fit');self.assertEqual(f['quantity'],'T2');self.assertAlmostEqual(f['rate_s_inv'],5.,delta=.02)
 def test_fitter_does_not_read_hidden_rate(self):
  h=run(ready());a=h.invoke('relaxation.fit');h.samples[h.active]['baseline_r1_s_inv']=999;self.assertEqual(a,h.invoke('relaxation.fit'))
 def test_no_points_at_start(self):
  h=ready();h.invoke('acquisition.start');self.assertEqual(h.points,[]);self.assertEqual(h.phase,'running')
 def test_partial_cancel(self):
  h=ready(method=CPMG_METHOD);h.invoke('acquisition.start');h.step(2.13);n=len(h.points);self.assertGreater(n,0);h.invoke('acquisition.stop');h.step(1);self.assertEqual(len(h.points),n);self.assertEqual(h.phase,'cancelled');self.reject(h,'relaxation.fit')
 def test_open_cancels(self):
  h=ready();h.invoke('acquisition.start');h.invoke('lid.set',opened=True);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.rf,0)
 def test_off_cancels(self):
  h=ready();h.invoke('acquisition.start');h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.rf,0)
 def test_busy_atomic(self):
  h=ready();h.invoke('acquisition.start');self.reject(h,'method.configure',**METHOD_EXAMPLE);self.reject(h,'thermal.set_temperature',temperature_k=310.15)
 def test_rf_budget_exhaustion(self):
  h=run(ready(plant={'rf_budget_j':.003}));self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'RF_BUDGET_EXHAUSTED');self.assertAlmostEqual(h.rf_delivered,.003);self.assertAlmostEqual(h._ledger()['rf_residual_j'],0)
 def test_finite_pulse_energy(self):
  h=run(ready(method=CPMG_METHOD));want=100*(50e-6+80*100e-6);self.assertAlmostEqual(h.rf_delivered,want,places=9)
 def test_exact_bloch_free_recovery(self):
  h=ready();h.mag[:]=[.4,.3,-.2];h.step(.1);np.testing.assert_allclose(h.mag[0],[.4*math.exp(-.5),.3*math.exp(-.5),1-1.2*math.exp(-.42)],atol=1e-12)
 def test_finite_rf_rotation(self):
  h=ready();h.rf=100;h.axis='x';h._advance(50e-6);self.assertAlmostEqual(-h.mag[0,1],1,delta=.001);self.assertAlmostEqual(h.rf_delivered,.005)
 def test_phase_refocus_static_offsets(self):
  h=run(ready(method=CPMG_METHOD,plant={'offset_hz':50}));f=h.invoke('relaxation.fit');self.assertAlmostEqual(f['rate_s_inv'],5,delta=.03)
 def test_rf_amplitude_affects_observed_signal(self):
  a=run(ready(method=CPMG_METHOD));b=run(ready(method=dict(CPMG_METHOD,rf_power_w=25)));self.assertNotEqual(a.points[0]['signal'],b.points[0]['signal'])
 def test_chunk_invariance(self):
  a=ready(method=CPMG_METHOD);b=ready(method=CPMG_METHOD);a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(3)
  for _ in range(300):b.step(.01)
  self.assertEqual([p['signal'] for p in a.points],[p['signal'] for p in b.points]);np.testing.assert_allclose(a.mag,b.mag,atol=1e-10);self.assertAlmostEqual(a.rf_delivered,b.rf_delivered,places=9)
 def test_two_instances(self):
  a=ready();b=ready();s=b.debug_snapshot();a.invoke('acquisition.start');a.step(10);self.assertEqual(s,b.debug_snapshot())
 def test_input_alias(self):
  h=ready();method=copy.deepcopy(METHOD_EXAMPLE);h.invoke('method.configure',**method);method['delays_s'][0]=999;self.assertEqual(h.method['delays_s'][0],.001)
 def test_output_alias(self):
  h=run(ready());x=h.invoke('signal.read');x['points'][0]['signal']=999;self.assertNotEqual(h.points[0]['signal'],999)
 def test_volume_ledger_unload(self):
  h=ready();h.invoke('lid.set',opened=True);h.invoke('sample.unload',recipient_id='rack');r=h._ledger();self.assertEqual(r['attached_volume_m3'],0);self.assertEqual(r['external_volume_m3'],5e-7);self.assertEqual(r['volume_residual_m3'],0)
 def test_agent_isolation(self):
  h=ready();s=h.session();self.assertNotIn('truth.ledger',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('solution.register',s.describe(instance_id=h.instance_id)['capabilities'])
  x=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='forbidden');self.assertEqual(x['error']['code'],'ACCESS_DENIED');self.assertNotIn(SAMPLE_EXAMPLE['template_id'],json.dumps(x))
 def test_idempotency_conflict(self):
  h=ready();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='req');x=s.invoke(**a);h.step(.1);self.assertEqual(s.invoke(**a),x);a['capability_id']='acquisition.stop';self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT')
 def test_operation_ownership(self):
  h=ready();s=h.session();h.register_principal('other');x=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')
  with self.assertRaises(ActionRejected):h.session('other').poll(x['operation_id'])
  s.cancel(x['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled')
 def test_wrong_instance(self):
  h=ready()
  with self.assertRaises(ActionRejected):h.session().read(instance_id='wrong')
 def test_unsupported_protocol(self):
  h=ready();s=h.session()
  for k in UNSUPPORTED:self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id=k,arguments=CAPS[k][6],request_id=k)['status'],'unsupported')
 def test_write_routes(self):
  h=ready();x=h.session().write(instance_id=h.instance_id,state_id='lid.opened',value=True,request_id='lid');self.assertEqual(x['status'],'succeeded');self.assertTrue(h.opened)
 def test_no_truth_write(self):
  h=ready()
  with self.assertRaises(ActionRejected):h.session().write(instance_id=h.instance_id,state_id='truth.ledger',value={},request_id='bad')
 def test_receiver_overflow(self):
  h=run(ready(plant={'receiver_gain':10.}));self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'RECEIVER_OVERFLOW')
 def test_schema_layers_unsupported(self):
  m=export_bruker_minispecmq20_contract_metadata()
  for k in UNSUPPORTED:self.assertTrue(all(v=='absent' for v in m['capabilities'][k]['implementation'].values()))
if __name__=='__main__':unittest.main()

