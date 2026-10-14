"""Independent numerical, event-clock, permission and negative-control regressions."""
import copy,hashlib,json,math,pickle,sys,unittest
from pathlib import Path
import numpy as np
from scipy.integrate import quad
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.edinburgh_fls1000tcspc import *
def ready(sample=None,method=None,plant=None):
 h=EdinburghFls1000tcspcRuntime()
 for k,a in [('plant.configure',plant or PLANT_EXAMPLE),('sample.load',sample or SAMPLE_EXAMPLE),('method.configure',method or METHOD_EXAMPLE),('sample.cover',{'opened':False}),('power.set',{'enabled':True}),('source.arm',{'enabled':True})]:h.invoke(k,**a)
 return h
def collect(sample=None,method=None):
 h=ready(sample,method);h.invoke('irf.start' if (sample or SAMPLE_EXAMPLE)['kind']=='scatterer' else 'acquisition.start');h.step(3);return h
class TcspcTests(unittest.TestCase):
 def rejected(self,h,k,a,code=None):
  old=pickle.dumps(h.debug_snapshot())
  with self.assertRaises(ActionRejected) as c:h.invoke(k,**a)
  if code:self.assertEqual(str(c.exception),code)
  self.assertEqual(pickle.dumps(h.debug_snapshot()),old)
 def test_01_source_templates(self):
  h=EdinburghFls1000tcspcRuntime();self.assertEqual(len(h.templates),67)
  for t in h.templates.values():
   f=t['source_file'];self.assertEqual(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest(),f['sha256']);self.assertIsNone(t['measured_photon_histogram']);self.assertGreater(t['lifetime_s'],0);self.assertLess(t['emission_peak_m'],900e-9)
  self.assertLess(abs(h.templates['life_row_003']['lifetime_s']/4.1e-9-1),1e-14)
 def test_02_not_bool_nan(self):
  h=EdinburghFls1000tcspcRuntime()
  for val in [True,float('nan'),float('inf')]:self.rejected(h,'plant.configure',dict(PLANT_EXAMPLE,dark_rate_s_inv=val),'INVALID_ARGUMENT')
 def test_03_initialization_once(self):
  h=EdinburghFls1000tcspcRuntime();h.step(.1);h.invoke('plant.configure',**PLANT_EXAMPLE);self.rejected(h,'plant.configure',PLANT_EXAMPLE,'INITIALIZATION_ONLY')
 def test_04_ack_required(self):
  h=EdinburghFls1000tcspcRuntime();self.rejected(h,'plant.configure',dict(PLANT_EXAMPLE,accept_surrogate_model=False),'EVIDENCE_ACK_REQUIRED')
 def test_05_external_load_guards(self):
  h=ready();self.rejected(h,'sample.load',SAMPLE_EXAMPLE,'EXTERNAL_LOAD_REQUIRED')
 def test_06_sample_source(self):
  h=EdinburghFls1000tcspcRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE)
  self.rejected(h,'sample.load',dict(SAMPLE_EXAMPLE,components=[{'template_id':'invented','photon_weight':1.}]),'UNKNOWN_REFERENCE')
  self.rejected(h,'sample.load',dict(SAMPLE_EXAMPLE,components=[{'template_id':'life_row_003','photon_weight':.5}]),'SAMPLE_COMPOSITION')
 def test_07_source_discrete(self):
  h=ready();self.rejected(h,'method.configure',dict(METHOD_EXAMPLE,excitation_wavelength_m=450e-9),'INSTALLED_SOURCE_MISMATCH')
 def test_08_repetition_discrete(self):
  h=ready();self.rejected(h,'method.configure',dict(METHOD_EXAMPLE,repetition_hz=15e6),'INVALID_ARGUMENT')
 def test_09_wavelength_grid(self):
  h=ready();self.rejected(h,'method.configure',dict(METHOD_EXAMPLE,emission_wavelength_m=519.005e-9),'WAVELENGTH_STEP')
 def test_10_window_period_guard(self):
  h=ready();self.rejected(h,'method.configure',dict(METHOD_EXAMPLE,time_window_s=100e-9),'PERIOD_GRID_RESOURCE')
 def test_11_latent_resource_guard(self):
  h=ready();self.rejected(h,'method.configure',dict(METHOD_EXAMPLE,repetition_hz=20e3,time_window_s=2.5e-9),'PERIOD_GRID_RESOURCE')
 def test_12_event_resource_guard(self):
  h=ready();self.rejected(h,'method.configure',dict(METHOD_EXAMPLE,max_acquisition_s=1000.),'EVENT_RESOURCE_LIMIT')
 def test_13_response_not_lifetime_bound(self):
  h=ready();self.assertGreater(h.irf_fwhm,600e-12);self.assertNotEqual(h.irf_fwhm,5e-12)
  expected=math.sqrt((75e-12)**2+(600e-12)**2+(20e-12)**2+(30e-12)**2);self.assertAlmostEqual(h.irf_fwhm/expected,1.)
 def test_14_periodic_decay_independent_integral(self):
  n=50;dt=1e-9;tau=15e-9;actual=exp_bins(n,dt,tau)
  expected=[sum(quad(lambda t:math.exp(-t/tau)/tau,(i+k*n)*dt,(i+1+k*n)*dt,epsabs=1e-13)[0] for k in range(15)) for i in range(n)]
  np.testing.assert_allclose(actual,expected,rtol=1e-12);self.assertAlmostEqual(sum(actual),1.)
 def test_15_gaussian_irf_independent_erf(self):
  n=1000;dt=50e-12;f=610e-12;c=2e-9;actual=wrapped_irf(n,dt,f,c);sig=f/math.sqrt(8*math.log(2))
  expected=np.array([.5*(math.erf(((i+1)*dt-c)/(math.sqrt(2)*sig))-math.erf((i*dt-c)/(math.sqrt(2)*sig))) for i in range(n)])
  np.testing.assert_allclose(actual,expected/expected.sum(),atol=1e-14)
 def test_16_circular_convolution_direct(self):
  a=np.arange(1.,33);a/=a.sum();b=exp_bins(32,1e-9,30e-9)
  expected=[sum(a[j]*b[(i-j)%32] for j in range(32)) for i in range(32)]
  np.testing.assert_allclose(convolve(a,b),expected,atol=1e-14)
 def test_17_first_photon_probability(self):
  mu=np.array([.1,.2,.3]);got=first_photon(mu)
  np.testing.assert_allclose(got,[(1-math.exp(-.1)),math.exp(-.1)*(1-math.exp(-.2)),math.exp(-.3)*(1-math.exp(-.3))])
  self.assertAlmostEqual(got.sum(),1-math.exp(-.6));self.assertLess(np.dot(got,np.arange(3))/got.sum(),np.dot(mu,np.arange(3))/mu.sum())
 def test_18_wraparound_long_lifetime(self):
  d=exp_bins(1000,50e-12,50e-6);self.assertLess(max(d)/min(d),1.002);self.assertAlmostEqual(sum(d),1.)
 def test_19_chunk_invariance(self):
  a=ready();b=ready()
  for h in [a,b]:h.invoke('acquisition.start')
  a.step(1.)
  for _ in range(200):b.step(.005)
  self.assertEqual(a.histograms[0]['counts'],b.histograms[0]['counts']);self.assertEqual(a._ledger()['emitted_pulses'],b._ledger()['emitted_pulses']);self.assertAlmostEqual(a.elapsed_s,b.elapsed_s)
 def test_20_finite_movement(self):
  h=ready();h.invoke('acquisition.start');h.step(.04);s=h.read()
  self.assertEqual(s['scan.stage'],'moving');self.assertEqual(s['scan.total_counts'],0);self.assertFalse(s['source.emitting']);self.assertAlmostEqual(s['slit.width_m'],.0006);h.step(.041);self.assertEqual(h.stage,'counting')
 def test_21_exact_total_stop(self):
  h=collect(method=dict(METHOD_EXAMPLE,target_count=500));self.assertEqual(h.phase,'succeeded');self.assertEqual(sum(h.histograms[0]['counts']),500)
 def test_22_exact_peak_stop(self):
  h=collect(method=dict(METHOD_EXAMPLE,stop_kind='peak_counts',target_count=20));self.assertEqual(h.phase,'succeeded');self.assertEqual(max(h.histograms[0]['counts']),20)
 def test_23_time_stop(self):
  h=collect(method=dict(METHOD_EXAMPLE,stop_kind='time',max_acquisition_s=.01));self.assertAlmostEqual(h.histograms[0]['duration_s'],.01);self.assertEqual(h.histograms[0]['pulses'],200000)
 def test_24_timeout_keeps_data(self):
  h=collect(method=dict(METHOD_EXAMPLE,target_count=100000,max_acquisition_s=.001));self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'COUNT_TIMEOUT');self.assertGreater(sum(h.active['counts']),0);self.assertFalse(h.active['complete'])
 def test_25_cancel_keeps_photons(self):
  h=ready();h.invoke('acquisition.start');h.step(.09);n=h._recorded;h.invoke('acquisition.stop');h.step(1);self.assertGreater(n,0);self.assertEqual(h._recorded,n);self.assertEqual(h.phase,'cancelled')
 def test_26_open_cover_stops_source(self):
  h=ready();h.invoke('acquisition.start');h.step(.09);h.invoke('sample.cover',opened=True);self.assertEqual(h.phase,'cancelled');self.assertFalse(h.armed);self.assertFalse(h.read()['source.emitting'])
 def test_27_power_off_stops(self):
  h=ready();h.invoke('acquisition.start');h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertFalse(h.armed)
 def test_28_source_disarm_stops(self):
  h=ready();h.invoke('acquisition.start');h.invoke('source.arm',enabled=False);self.assertEqual(h.phase,'cancelled')
 def test_29_partial_window_discard_ledger(self):
  h=collect(method=dict(METHOD_EXAMPLE,time_window_s=2.5e-9,stop_kind='time',max_acquisition_s=.005));l=h._ledger();self.assertGreater(l['out_of_window_photons'],0);self.assertEqual(l['count_residual'],0);self.assertLessEqual(l['detected_first_photons'],l['emitted_pulses'])
 def test_30_intact_volume_replacement(self):
  h=collect();h.invoke('power.set',enabled=False);h.invoke('sample.cover',opened=True);h.invoke('sample.load',**IRF_EXAMPLE);l=h._ledger();self.assertEqual(l['current_volume_m3'],0);self.assertEqual(l['removed_volume_m3'],1e-6);self.assertAlmostEqual(l['volume_residual_m3'],0)
 def test_31_irf_real_acquisition(self):
  h=collect(IRF_EXAMPLE,dict(METHOD_EXAMPLE,emission_wavelength_m=405e-9));self.assertEqual(h.phase,'succeeded');self.assertEqual(sum(h.histograms[0]['counts']),5000);self.assertEqual(h.histograms[0]['response_kind'],'IRF')
 def test_32_irf_kind_guard(self):
  h=ready();self.rejected(h,'irf.start',{},'REFERENCE_KIND')
 def test_33_scatter_wavelength_guard(self):
  h=ready(IRF_EXAMPLE);self.rejected(h,'irf.start',{},'SCATTER_WAVELENGTH')
 def test_34_scan_sequential(self):
  h=ready(method=dict(METHOD_EXAMPLE,target_count=500));h.invoke('scan.start',wavelengths_m=[510e-9,530e-9,550e-9]);h.step(3);self.assertEqual(h.phase,'succeeded');self.assertEqual(len(h.histograms),3)
  for before,after in zip(h.histograms,h.histograms[1:]):self.assertGreaterEqual(after['start_time_s']-before['start_time_s']-before['duration_s'],.04-1e-12)
 def test_35_query_clock_and_no_truth(self):
  h=collect();t=h.time_s;n=h._recorded
  for k in ['process.measure','result.read']:self.assertNotIn('life_row',json.dumps(h.invoke(k)))
  self.assertEqual(h.time_s,t);self.assertEqual(h._recorded,n);self.assertNotIn('truth.ledger',h.read());self.assertIsNotNone(h.read('evaluator')['truth.ledger'])
 def test_36_no_return_alias(self):
  h=collect();r=h.invoke('result.read');r['histograms'][0]['counts'][0]=999999;self.assertNotEqual(h.histograms[0]['counts'][0],999999)
 def test_37_caller_only_mono_fit(self):
  h=EdinburghFls1000tcspcRuntime();r=h.invoke('lifetime.fit',**CAPS['lifetime.fit'][6]);self.assertLess(abs(r['lifetimes_s'][0]/4e-9-1),.005)
 def test_38_caller_bi_fit(self):
  irfh=demo_hist();n=512;dt=50e-9/n;irf=wrapped_irf(n,dt,610e-12,2e-9);mu=.004*convolve(irf,exp_bins(n,dt,.5e-9))+.006*convolve(irf,exp_bins(n,dt,4e-9))+1e-6/n;h=demo_hist(4e-9);h['counts']=np.rint(h['pulses']*first_photon(mu)).astype(int).tolist()
  r=EdinburghFls1000tcspcRuntime().invoke('lifetime.fit',histogram=h,irf_histogram=irfh,initial_lifetimes_s=[.7e-9,3e-9]);np.testing.assert_allclose(r['lifetimes_s'],[.5e-9,4e-9],rtol=.03)
 def test_39_das_fixed_taus(self):
  h=EdinburghFls1000tcspcRuntime();r=h.invoke('das.fit',**CAPS['das.fit'][6]);self.assertEqual(len(r['wavelength_m']),2);self.assertLess(abs(r['photon_amplitudes_per_pulse'][0][0]/.01-1),.005)
 def test_40_fit_rejects_partial_or_wrong_irf(self):
  h=EdinburghFls1000tcspcRuntime();a=copy.deepcopy(CAPS['lifetime.fit'][6]);a['histogram']['complete']=False;self.rejected(h,'lifetime.fit',a,'ANALYSIS_INPUT')
  a=copy.deepcopy(CAPS['lifetime.fit'][6]);a['irf_histogram']['response_kind']='decay';self.rejected(h,'lifetime.fit',a,'IRF_GRID_MISMATCH')
 def test_41_full_period_fit_guard(self):
  h=EdinburghFls1000tcspcRuntime();a=copy.deepcopy(CAPS['lifetime.fit'][6]);a['histogram']['period_s']=100e-9;self.rejected(h,'lifetime.fit',a,'FULL_PERIOD_REQUIRED')
 def test_42_session_permissions(self):
  h=ready();s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='x');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
  with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
  self.assertNotIn('sample.load',s.describe(instance_id=h.instance_id)['capabilities'])
 def test_43_idempotency_cancel(self):
  h=ready();s=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');a=s.invoke(**kw);self.assertEqual(s.invoke(**kw),a);self.assertEqual(h._op_count,1);h.step(.09);s.cancel(a['operation_id'],'cancel');self.assertEqual(s.poll(a['operation_id'])['status'],'cancelled')
  r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id='start');self.assertEqual(r['error']['code'],'REQUEST_CONFLICT')
 def test_44_owner_only_poll(self):
  h=ready();h.register_principal('b');a=h.session().invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a')
  with self.assertRaises(ActionRejected):h.session('b').poll(a['operation_id'])
 def test_45_unsupported(self):
  h=ready()
  for cap in UNSUPPORTED:self.rejected(h,cap,CAPS[cap][6],'UNSUPPORTED')
 def test_46_actual_measurement_fit(self):
  ir=collect(IRF_EXAMPLE,dict(METHOD_EXAMPLE,emission_wavelength_m=405e-9,target_count=20000,max_acquisition_s=.3))
  signal=collect(method=dict(METHOD_EXAMPLE,target_count=20000,max_acquisition_s=.3))
  r=signal.invoke('lifetime.fit',histogram=signal.histograms[0],irf_histogram=ir.histograms[0],initial_lifetimes_s=[3e-9]);self.assertLess(abs(r['lifetimes_s'][0]/4.1e-9-1),.10)
 def test_47_all_real_templates_run(self):
  h=EdinburghFls1000tcspcRuntime()
  for sid,t in h.templates.items():
   q=collect(dict(SAMPLE_EXAMPLE,components=[{'template_id':sid,'photon_weight':1.}]),dict(METHOD_EXAMPLE,emission_wavelength_m=t['emission_peak_m'],target_count=100))
   self.assertEqual(q.phase,'succeeded');self.assertEqual(q._ledger()['count_residual'],0)
 def test_48_sample_timestamp(self):
  h=collect();h.step(1);s=h.session().read(instance_id=h.instance_id,state_ids=['scan.total_counts']);o=s['observations']['scan.total_counts'];self.assertLess(o['sample_time_s'],h.time_s);self.assertEqual(o['sample_time_s'],h.histograms[-1]['last_sample_time_s'])
 def test_49_empty_signal_not_invented(self):
  h=ready();s=h.read();self.assertEqual(s['scan.total_counts'],0);self.assertIsNone(s['scan.mean_microtime_s']);self.assertIsNone(s['scan.last_sample_time_s'])
 def test_50_irf_transfer_and_units(self):
  h=ready();self.assertAlmostEqual(h.read()['method.bin_width_s']/(50e-9/1024),1.);self.assertGreater(h.irf_fwhm,h.read()['method.bin_width_s']);self.assertEqual(SLOTS['scan.mean_microtime_s'][1],'s')
 def test_51_historical_method_snapshot(self):
  h=collect();before=h.invoke('result.read');h.invoke('method.configure',**dict(METHOD_EXAMPLE,target_count=123,emission_wavelength_m=530e-9));self.assertEqual(h.invoke('result.read')['settings_json'],before['settings_json'])
  h.invoke('acquisition.start');h.step(2);self.assertEqual(json.loads(h.invoke('result.read')['settings_json'])['target_count'],123)
 def test_52_documented_tcspc_channels_not_mcs_channels(self):
  h=ready()
  for bins in [512,1024,2048,4096,8192]:
   h.invoke('method.configure',**dict(METHOD_EXAMPLE,bins=bins));self.assertAlmostEqual(h.read()['method.bin_width_s']*bins/50e-9,1.)
  for bins in [500,1000,2000,4000,8000]:self.rejected(h,'method.configure',dict(METHOD_EXAMPLE,bins=bins),'INVALID_ARGUMENT')
  self.assertEqual(len(demo_hist()['counts']),512);self.assertEqual(METHOD_EXAMPLE['bins'],1024)
 def test_53_zero_or_underflowed_period_rejects_with_protocol_error(self):
  for cap in ['lifetime.fit','das.fit']:
   for period in [0.,5e-324]:
    with self.subTest(cap=cap,period=period):
     h=EdinburghFls1000tcspcRuntime();a=copy.deepcopy(CAPS[cap][6]);histograms=[a['histogram']] if cap=='lifetime.fit' else a['histograms']
     for hist in [*histograms,a['irf_histogram']]:hist['period_s']=period;hist['edges_s']=[i/len(hist['counts'])*period for i in range(len(hist['counts'])+1)]
     before=pickle.dumps(h.debug_snapshot());reply=h.session().invoke(instance_id=h.instance_id,capability_id=cap,arguments=a,request_id='bad-grid')
     self.assertEqual(reply['status'],'rejected');self.assertEqual(reply['error']['code'],'ANALYSIS_INPUT');self.assertEqual(before,pickle.dumps(h.debug_snapshot()));json.dumps(reply,allow_nan=False)
 def test_54_numerically_invisible_lifetime_does_not_return_initial_guess(self):
  a=copy.deepcopy(CAPS['lifetime.fit'][6]);a['initial_lifetimes_s']=[5e-12]
  for hist in [a['histogram'],a['irf_histogram']]:hist['period_s']=50e-6;hist['edges_s']=np.linspace(0,50e-6,len(hist['counts'])+1).tolist()
  self.rejected(EdinburghFls1000tcspcRuntime(),'lifetime.fit',a,'FIT_UNIDENTIFIABLE')
 def test_55_zero_photon_acquisition_has_real_observation_window(self):
  h=ready(sample=dict(SAMPLE_EXAMPLE,mean_photons_per_pulse_at_peak=1e-8),plant=dict(PLANT_EXAMPLE,dark_rate_s_inv=0.),method=dict(METHOD_EXAMPLE,stop_kind='time',max_acquisition_s=1e-5));h.invoke('acquisition.start');h.step(.081)
  hist=h.histograms[0];self.assertEqual(hist['pulses'],200);self.assertEqual(sum(hist['counts']),0);self.assertIsNone(hist['last_sample_time_s']);end=hist['start_time_s']+hist['duration_s']
  observations=h.session().read(instance_id=h.instance_id,state_ids=['scan.total_counts','scan.peak_counts','scan.mean_microtime_s'])['observations']
  for observation in observations.values():self.assertEqual(observation['sample_time_s'],end);self.assertEqual(observation['sample_window_s'],[hist['start_time_s'],end])
  self.assertEqual(observations['scan.total_counts']['value'],0);self.assertIsNone(observations['scan.mean_microtime_s']['value']);self.assertEqual(observations['scan.mean_microtime_s']['quality'],'unavailable')
 def test_56_accumulated_count_window_includes_no_photon_tail(self):
  h=collect(method=dict(METHOD_EXAMPLE,stop_kind='time',max_acquisition_s=.005));hist=h.histograms[0];end=hist['start_time_s']+hist['duration_s'];before=pickle.dumps(h.debug_snapshot())
  observations=h.session().read(instance_id=h.instance_id,state_ids=['scan.total_counts','scan.last_sample_time_s'])['observations'];total=observations['scan.total_counts']
  self.assertGreater(end,hist['last_sample_time_s']);self.assertEqual(total['sample_time_s'],end);self.assertEqual(total['sample_window_s'],[hist['start_time_s'],end]);self.assertEqual(observations['scan.last_sample_time_s']['value'],hist['last_sample_time_s']);self.assertEqual(before,pickle.dumps(h.debug_snapshot()))
 def test_57_historical_completion_preserves_public_summary_and_window(self):
  h=ready(method=dict(METHOD_EXAMPLE,stop_kind='time',max_acquisition_s=.005));h.register_principal('other');s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='first')['operation_id'];h.step(1);terminal=s.poll(op)
  self.assertEqual(terminal['status'],'succeeded');self.assertAlmostEqual(terminal['result']['elapsed_s'],.085);self.assertEqual(terminal['observations']['scan.total_counts'],s.read(instance_id=h.instance_id,state_ids=['scan.total_counts'])['observations']['scan.total_counts'])
  s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='second');before=pickle.dumps(h.debug_snapshot());self.assertEqual(s.poll(op),terminal);terminal['result']['total_counts']=-1;self.assertGreater(s.poll(op)['result']['total_counts'],0)
  self.assertNotIn('life_row',json.dumps(s.poll(op)));self.assertNotIn(SAMPLE_EXAMPLE['sample_id'],json.dumps(s.poll(op)))
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):h.session('other').poll(op)
  self.assertEqual(before,pickle.dumps(h.debug_snapshot()))
 def test_58_cancel_historical_terminal_does_not_stop_new_counting(self):
  h=ready(method=dict(METHOD_EXAMPLE,target_count=1000));s=h.session();old=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='old')['operation_id'];h.step(1)
  current=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='new')['operation_id'];h.step(.0001);revision=h.revision;ledger=h._ledger()
  reply=s.cancel(old,'cancel-old');self.assertEqual(reply['result']['phase'],'succeeded');self.assertEqual(h.phase,'running');self.assertEqual(h.operation_id,current);self.assertEqual(h.revision,revision);self.assertEqual(h._ledger(),ledger);self.assertEqual(s.cancel(old,'cancel-old'),reply)
  with self.assertRaisesRegex(ActionRejected,'REQUEST_CONFLICT'):s.cancel(current,'cancel-old')
  self.assertEqual(h.phase,'running')
 def test_59_failed_and_cancelled_partial_histories_survive_later_start(self):
  for status in ['failed','cancelled']:
   with self.subTest(status=status):
    h=ready(method=dict(METHOD_EXAMPLE,target_count=100000,max_acquisition_s=.001));s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='first')['operation_id'];h.step(.0805)
    if status=='cancelled':s.cancel(op,'cancel')
    else:h.step(.002)
    terminal=s.poll(op);self.assertEqual(terminal['status'],status);self.assertGreater(terminal['result']['total_counts'],0);self.assertFalse(h.active['complete']);counts=copy.deepcopy(h.active['counts']);ledger=h._ledger();h.step(1);self.assertEqual(h.active['counts'],counts);self.assertEqual(h._ledger(),ledger)
    s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='next');self.assertEqual(s.poll(op),terminal)
 def test_60_metadata_matches_method_targets_and_step_optics(self):
  h=ready();m=export_edinburgh_fls1000tcspc_contract_metadata();caps=m['capabilities'];before=h.read('evaluator');h.invoke('method.configure',**dict(METHOD_EXAMPLE,time_window_s=25e-9,emission_wavelength_m=520e-9));configured=h.read('evaluator');effects=caps['method.configure']['state_effects']
  self.assertTrue(all(e['timing']=='immediate' for e in effects));self.assertLessEqual({key for key in before if before[key]!=configured[key]},{e['state_id'] for e in effects});self.assertEqual(configured['mono.wavelength_m'],before['mono.wavelength_m']);self.assertNotIn('truth.ledger',{e['state_id'] for e in effects})
  h.invoke('acquisition.start');started=h.read('evaluator');immediate={e['state_id'] for e in caps['acquisition.start']['state_effects'] if e['timing']=='immediate'};later={e['state_id'] for e in caps['acquisition.start']['state_effects'] if e['timing']=='on_step'}
  self.assertLessEqual({key for key in configured if configured[key]!=started[key]},immediate);self.assertNotIn('mono.wavelength_m',immediate);h.step(.04);advanced=h.read('evaluator');self.assertLessEqual({key for key in started if started[key]!=advanced[key]},later);self.assertFalse(advanced['source.emitting']);self.assertGreater(advanced['slit.width_m'],started['slit.width_m'])
  self.assertEqual(caps['lifetime.fit']['observes'],[]);self.assertEqual(caps['das.fit']['observes'],[]);self.assertEqual(m['state_slots']['scan.total_counts']['sampling']['mode'],'integral')
if __name__=='__main__':unittest.main()

