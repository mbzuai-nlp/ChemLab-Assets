import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
from scipy.linalg import expm
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.applied_sx20 import *
def ready(plant=None,sample=None,method=None):
 r=AppliedSx20Runtime();r.invoke('plant.configure',**(plant or PLANT_EXAMPLE));r.invoke('sample.load',**(sample or SAMPLE_EXAMPLE));r.invoke('method.configure',**(method or METHOD_EXAMPLE))
 for k,a in [('bath.cover',{'opened':False}),('valves.set',{'drive':True}),('gas.set',{'enabled':True}),('power.set',{'enabled':True})]:r.invoke(k,**a)
 return r
def flush(r):
 r.invoke('gas.set',enabled=False);r.invoke('bath.cover',opened=True);r.invoke('valves.set',drive=False);r.invoke('fluid.flush');r.invoke('valves.set',drive=True);r.invoke('bath.cover',opened=False);r.invoke('gas.set',enabled=True)
class TestSx20(unittest.TestCase):
 def assertReject(self,r,k,args,code):
  before=r.debug_snapshot()
  with self.assertRaisesRegex(ActionRejected,code):r.invoke(k,**args)
  self.assertEqual(before,r.debug_snapshot())
 def test_01_references(self):
  t=_templates() if '_templates' in globals() else AppliedSx20Runtime().templates
  self.assertEqual(len(t),63);self.assertEqual(len({q['name'] for q in t.values()}),63)
  for q in t.values():self.assertIsNone(q['kinetic_constants_s_inv']);self.assertIsNone(q['measured_time_trace'])
 def test_02_guard_ack(self):
  r=AppliedSx20Runtime();self.assertReject(r,'plant.configure',dict(PLANT_EXAMPLE,accept_surrogate_model=False),'EVIDENCE_ACK')
 def test_03_initialization_at_nonzero_clock(self):
  r=AppliedSx20Runtime();r.step(.02);r.invoke('plant.configure',**PLANT_EXAMPLE);self.assertReject(r,'plant.configure',PLANT_EXAMPLE,'INITIALIZATION')
 def test_04_guard_solvent(self):
  r=AppliedSx20Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE);self.assertReject(r,'sample.load',dict(SAMPLE_EXAMPLE,solvent='water'),'SOLVENT')
 def test_05_guard_hypothetical_ack(self):
  r=AppliedSx20Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE);self.assertReject(r,'sample.load',dict(SAMPLE_EXAMPLE,accept_hypothetical_kinetics=False),'EVIDENCE_ACK')
 def test_06_source_coverage(self):
  r=ready(method=dict(METHOD_EXAMPLE,wavelength_m=850e-9));self.assertReject(r,'acquisition.start',{},'SOURCE_COVERAGE')
 def test_07_physical_mixing_ratio(self):
  r=ready();self.assertReject(r,'method.configure',dict(METHOD_EXAMPLE,mix_ratio_a_b=2),'PHYSICAL_RATIO')
 def test_08_asymmetric_capacity(self):
  for cap in [1e-6,.5e-6,.25e-6,.1e-6]:
   ratio=round(2.5e-6/cap,10);r=ready(plant=dict(PLANT_EXAMPLE,drive_b_capacity_m3=cap),method=dict(METHOD_EXAMPLE,mix_ratio_a_b=ratio));r.invoke('acquisition.start');r.step(.03)
   useda=2.5e-6-r._va;usedb=cap-r._vb;self.assertAlmostEqual(useda/usedb,ratio,places=9)
 def test_09_standard_path(self):
  r=ready();self.assertReject(r,'method.configure',dict(METHOD_EXAMPLE,path_m=.005),'POWER_OFF')
  r.invoke('power.set',enabled=False);self.assertReject(r,'method.configure',dict(METHOD_EXAMPLE,path_m=.005),'CELL_PATH')
 def test_10_rapid_option(self):
  r=ready(plant=dict(PLANT_EXAMPLE,rapid_cell_installed=True),method=dict(METHOD_EXAMPLE,path_m=.005,stop_volume_m3=35e-9))
  self.assertEqual(r.cell_volume,5e-9);self.assertEqual(r.stop_capacity,1e-6);r.invoke('acquisition.start');r.step(1);self.assertEqual(r.points[0]['reaction_age_s'],.0005)
 def test_11_no_early_observation(self):
  r=ready();r.invoke('acquisition.start');r.step(.069);self.assertEqual(r.points,[]);r.step(.001);self.assertEqual(len(r.points),1);self.assertAlmostEqual(r.points[0]['sample_time_s'],.07);self.assertEqual(r.points[0]['reaction_age_s'],.001)
 def test_12_delay_not_double_clock(self):
  r=ready(method=dict(METHOD_EXAMPLE,delay_s=.005));r.invoke('acquisition.start');r.step(.074);self.assertEqual(r.points,[]);r.step(.001)
  self.assertAlmostEqual(r.time_s,.075);self.assertAlmostEqual(r.points[0]['sample_time_s'],.075);self.assertAlmostEqual(r.points[0]['reaction_age_s'],.006)
 def test_13_partition_invariance(self):
  a=ready();b=ready()
  for r in [a,b]:r.invoke('acquisition.start')
  a.step(.7)
  for _ in range(700):b.step(.001)
  self.assertEqual(len(a.points),len(b.points));np.testing.assert_allclose([[p['signal'],p['sample_time_s']] for p in a.points],[[p['signal'],p['sample_time_s']] for p in b.points],rtol=1e-10,atol=1e-12)
  np.testing.assert_allclose([a._va,a._nc,a._ns],[b._va,b._nc,b._ns],rtol=1e-10,atol=1e-20)
 def test_14_conservation_partial_full(self):
  r=ready();r.invoke('acquisition.start')
  for dt in [.013,.027,.031,.5]:
   r.step(dt);d=r.invoke('product.read');self.assertLess(abs(d['volume_residual_m3']),1e-19);self.assertLess(abs(d['moiety_residual_mol']),1e-21)
 def test_15_cell_washout_exact(self):
  r=ready();r.invoke('acquisition.start');r.step(.035);v=.035*PLANT_EXAMPLE['flow_m3_s'];cin=SAMPLE_EXAMPLE['stock_concentration_mol_m3']/2
  self.assertAlmostEqual(r._nc/(cin*20e-9),1-math.exp(-v/20e-9),places=12)
 def test_16_cancel_retains_consumption(self):
  r=ready();r.invoke('acquisition.start');r.step(.03);v=r._va;r.invoke('acquisition.stop');r.step(1);self.assertEqual(r._va,v);self.assertLess(v,2.5e-6);self.assertTrue(r.needs_flush);self.assertEqual(r.points,[])
 def test_17_cover_cancel(self):
  r=ready();r.invoke('acquisition.start');r.step(.09);n=len(r.points);r.invoke('bath.cover',opened=True);r.step(1);self.assertEqual(len(r.points),n);self.assertFalse(r.gas);self.assertEqual(r.phase,'cancelled')
 def test_18_power_cancel(self):
  r=ready();r.invoke('acquisition.start');r.step(.08);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled')
 def test_19_gas_cancel(self):
  r=ready();r.invoke('acquisition.start');r.step(.03);r.invoke('gas.set',enabled=False);self.assertEqual(r.phase,'cancelled')
 def test_20_new_start_needs_flush(self):
  r=ready();r.invoke('acquisition.start');r.step(1);self.assertReject(r,'acquisition.start',{},'EXTERNAL_FLUSH')
 def test_21_flush_and_autoempty(self):
  r=ready();r.invoke('acquisition.start');r.step(1);oldstop=r._vs;oldn=r._ns;flush(r)
  self.assertEqual(r._vs,oldstop);self.assertEqual(r._ns,oldn);self.assertEqual(r._nc,0);r.invoke('acquisition.start');self.assertEqual(r.stage,'returning');self.assertEqual(r.read()['stop.valve_angle_rad'],math.pi)
  r.step(.02);self.assertLess(r._vs,oldstop);self.assertGreater(r._nw,0);r.step(1);self.assertEqual(r.phase,'succeeded');self.assertLess(abs(r._ledger()['moiety_residual_mol']),1e-21)
 def test_22_equal_rates_exponential_oracle(self):
  for k1,k2 in [(20,20),(20,20.000001),(40,8),(8,40)]:
   for t in [0,.001,.04,1,1e3]:
    actual=AppliedSx20Runtime.fractions(t,k1,k2);expected=expm(np.array([[-k1,0,0],[k1,-k2,0],[0,k2,0]],float)*t)@np.array([1.,0,0]);np.testing.assert_allclose(actual,expected,atol=5e-9,rtol=5e-7);self.assertAlmostEqual(sum(actual),1)
 def test_23_intermediate_peak(self):
  r=ready();r.invoke('acquisition.start');r.step(1);signals=[p['signal'] for p in r.points];self.assertGreater(max(signals),signals[0]);self.assertLess(signals[-1],signals[0])
 def test_24_absorbance_independent(self):
  r=ready();r.invoke('acquisition.start');r.step(1);t=r.templates[SAMPLE_EXAMPLE['template_id']];epsilon=np.interp(482.5e-9,t['abs_wavelength_m'],t['epsilon_m2_mol']);p=r.points[20];f=expm(np.array([[-40.,0,0],[40.,-8.,0],[0,8.,0]])*p['reaction_age_s'])@np.array([1,0,0]);expected=epsilon*.001*(1-math.exp(-7))*.01*np.dot(f,[1,2,.15]);self.assertAlmostEqual(p['signal'],expected,places=11)
 def test_25_fluor_filter_independent(self):
  r=ready(method=FLUOR_METHOD);r.invoke('acquisition.start');r.step(1);t=r.templates['pcad_010'];x=np.array(t['em_wavelength_m']);y=np.maximum(t['emission_relative'],0);lo=505e-9;hi=535e-9;grid=np.r_[lo,x[(x>lo)&(x<hi)],hi]
  fraction=np.trapezoid(np.interp(grid,x,y),grid)/np.trapezoid(y,x);eps=np.interp(425e-9,t['abs_wavelength_m'],t['epsilon_m2_mol']);f=AppliedSx20Runtime.fractions(.001,40,8)
  expected=10*(1-10**(-eps*.001*(1-math.exp(-7))*.002))*.79*fraction*np.dot(f,[1,2,.15]);self.assertAlmostEqual(r.points[0]['signal'],expected,places=11);self.assertEqual(r.points[0]['unit'],'V')
 def test_26_fluor_excitation_guard(self):
  r=ready(method=dict(FLUOR_METHOD,wavelength_m=430e-9));self.assertReject(r,'acquisition.start',{},'EXCITATION_REFERENCE')
 def test_27_fluor_rapid_path(self):
  r=ready(plant=dict(PLANT_EXAMPLE,rapid_cell_installed=True),method=dict(FLUOR_METHOD,path_m=.005));r.invoke('acquisition.start');r.step(1);self.assertTrue(r.points)
 def test_28_fluor_path_reject(self):
  r=ready();r.invoke('power.set',enabled=False);self.assertReject(r,'method.configure',dict(FLUOR_METHOD,path_m=.01),'FLUOR_EXCITATION')
 def test_29_log_schedule(self):
  r=ready(method=dict(METHOD_EXAMPLE,timebase='log'));r.invoke('acquisition.start');r.step(1);times=np.array([p['trigger_time_s'] for p in r.points]);self.assertEqual(len(times),101);self.assertEqual(times[0],0);self.assertAlmostEqual(times[-1],.5);self.assertTrue(np.all(np.diff(times)>0))
 def test_30_noise_deterministic_partition(self):
  a=ready(plant=dict(PLANT_EXAMPLE,abs_noise_sd=.001));b=ready(plant=dict(PLANT_EXAMPLE,abs_noise_sd=.001))
  for r in [a,b]:r.invoke('acquisition.start')
  a.step(1)
  for _ in range(100):b.step(.01)
  np.testing.assert_allclose([p['signal'] for p in a.points],[p['signal'] for p in b.points],atol=1e-12)
 def test_31_fit_only_observations(self):
  r=AppliedSx20Runtime();x=np.linspace(0,1,60);y=.2+.7*np.exp(-7*x);before=r.debug_snapshot();o=r.invoke('kinetics.fit',time_s=x.tolist(),signal=y.tolist());self.assertAlmostEqual(o['k_s_inv'],7,places=6);self.assertLess(o['rmse'],1e-8);self.assertEqual(before,r.debug_snapshot())
 def test_32_fit_invalid(self):
  r=ready();self.assertReject(r,'kinetics.fit',dict(time_s=[0]*6,signal=[1]*6),'ANALYSIS')
 def test_33_blank_caller_value(self):
  r=ready();r.invoke('calibration.zero',observed_blank=.02,mode='absorbance');self.assertEqual(r._correction['absorbance'],-.02)
 def test_34_readonly_deepcopy(self):
  r=ready();r.invoke('acquisition.start');r.step(1);snap=r.debug_snapshot();z=r.invoke('result.read');z['points'].clear();d=r.invoke('template.read',substance_id='pcad_010');d['epsilon_m2_mol'][0]=999;self.assertEqual(snap,r.debug_snapshot());self.assertNotEqual(r.templates['pcad_010']['epsilon_m2_mol'][0],999)
 def test_35_acl(self):
  r=ready();s=r.session();reply=s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='secret');self.assertEqual(reply['error']['code'],'ACCESS_DENIED');self.assertNotIn('truth.ledger',s.describe(instance_id=r.instance_id)['state_slots'])
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):s.read(instance_id=r.instance_id,state_ids=['truth.ledger'])
 def test_36_request_dedup(self):
  r=ready();s=r.session();args=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='one');a=s.invoke(**args);r.step(.01);v=r._va;b=s.invoke(**args);self.assertEqual(a,b);self.assertEqual(v,r._va)
  args.update(capability_id='acquisition.stop');self.assertEqual(s.invoke(**args)['error']['code'],'REQUEST_CONFLICT')
 def test_37_poll_owner_cancel(self):
  r=ready();r.register_principal('other');s=r.session();rep=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start')
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):r.session('other').poll(rep['operation_id'])
  self.assertEqual(s.cancel(rep['operation_id'],'cancel')['status'],'succeeded');self.assertEqual(s.poll(rep['operation_id'])['status'],'cancelled')
 def test_38_schema_bool_nan_extra(self):
  r=ready()
  for value in [True,float('nan'),float('inf')]:self.assertReject(r,'calibration.zero',dict(observed_blank=value,mode='absorbance'),'INVALID_ARGUMENT')
  self.assertReject(r,'power.set',dict(enabled=True,unexpected=3),'INVALID_ARGUMENT')
 def test_39_zero_step_stability(self):
  r=ready();s=r.debug_snapshot();r.step(0);self.assertEqual(s,r.debug_snapshot())
 def test_40_busy_guard_atomic(self):
  r=ready();r.invoke('acquisition.start');self.assertReject(r,'method.configure',METHOD_EXAMPLE,'BUSY')
 def test_41_unsupported(self):
  r=ready()
  for k in UNSUPPORTED:self.assertReject(r,k,CAPS[k][6],'UNSUPPORTED')
 def test_42_timestamp_not_read_time(self):
  r=ready();r.invoke('acquisition.start');r.step(2);rep=r.session().read(instance_id=r.instance_id,state_ids=['scan.signal']);self.assertAlmostEqual(rep['observations']['scan.signal']['sample_time_s'],.57);self.assertEqual(rep['clock']['time_s'],2.)
 def test_43_all_templates_actual_output(self):
  for t in AppliedSx20Runtime().templates.values():
   peak=t['derived']['emission_peak_m'];lo=max(300e-9,t['em_wavelength_m'][0],peak-10e-9);hi=min(650e-9,t['em_wavelength_m'][-1],peak+10e-9)
   sample=dict(SAMPLE_EXAMPLE,template_id=t['substance_id'],solvent=t['conditions']['solvent'])
   method=dict(FLUOR_METHOD,wavelength_m=t['conditions']['reference_excitation_m'],filter_low_m=lo,filter_high_m=hi,points=6,duration_s=.01)
   r=ready(sample=sample,method=method);r.invoke('acquisition.start');r.step(1);self.assertEqual(r.phase,'succeeded');self.assertTrue(all(math.isfinite(p['signal']) for p in r.points));self.assertGreater(max(p['signal'] for p in r.points),0)
 def test_44_no_factory_dimensions(self):
  g=json.loads((ROOT/'assets/expansion_batch04/t3_analytical/applied_sx20/geometry_manifest.json').read_text());self.assertIsNone(g['documented_dimensions_m']);self.assertIsNone(g['total_mass_kg'])
 def test_45_physical_unit_slots(self):
  r=ready(method=FLUOR_METHOD);r.invoke('acquisition.start');r.step(1);out=r.session().read(instance_id=r.instance_id);self.assertEqual(out['observations']['scan.fluorescence_v']['unit'],'V');self.assertIsNone(out['observations']['scan.absorbance']['value']);self.assertEqual(out['observations']['scan.fluorescence_v']['value'],r.points[-1]['signal'])
 def test_46_reconfigure_preserves_old_observation_unit(self):
  r=ready();r.invoke('acquisition.start');r.step(1);r.invoke('power.set',enabled=False);r.invoke('method.configure',**FLUOR_METHOD)
  self.assertEqual(r.read()['scan.unit'],'1');self.assertIsNone(r.read()['scan.fluorescence_v']);self.assertIsNotNone(r.read()['scan.absorbance'])
 def test_47_fit_time_and_signal_scale_equivariance(self):
  base=np.linspace(0,1,60);r=AppliedSx20Runtime()
  for time_scale,signal_scale in [(1.,1e-20),(1.,1e200),(1e12,1.),(1e-12,1.)]:
   with self.subTest(time_scale=time_scale,signal_scale=signal_scale):
    before=r.debug_snapshot();out=r.invoke('kinetics.fit',time_s=(base*time_scale).tolist(),signal=((.2+.7*np.exp(-7*base))*signal_scale).tolist())
    self.assertAlmostEqual(out['k_s_inv']*time_scale,7.,places=7);self.assertAlmostEqual(out['amplitude']/signal_scale,.7,places=8);self.assertAlmostEqual(out['offset']/signal_scale,.2,places=8);self.assertLess(out['rmse']/signal_scale,1e-8)
    json.dumps(out,allow_nan=False);self.assertEqual(before,r.debug_snapshot())
 def test_48_fit_unrepresentable_rate_is_clear_atomic_rejection(self):
  r=AppliedSx20Runtime();x=np.linspace(0,1,60);args=dict(time_s=(x*1e-309).tolist(),signal=(.2+.7*np.exp(-7*x)).tolist());before=r.debug_snapshot()
  reply=r.session().invoke(instance_id=r.instance_id,capability_id='kinetics.fit',arguments=args,request_id='too-fast-for-number')
  self.assertEqual(reply['status'],'rejected');self.assertEqual(reply['error']['code'],'FIT_UNIDENTIFIABLE');self.assertEqual(before,r.debug_snapshot());json.dumps(reply,allow_nan=False)
 def test_49_result_method_retained_until_next_acquisition(self):
  r=ready();r.invoke('acquisition.start');r.step(1);old=r.invoke('result.read');new=dict(METHOD_EXAMPLE,duration_s=.2,delay_s=.01)
  r.invoke('method.configure',**new);self.assertEqual(r.invoke('result.read'),old);self.assertEqual(json.loads(old['settings_json']),METHOD_EXAMPLE)
  flush(r);r.invoke('acquisition.start');empty=r.invoke('result.read');self.assertEqual(empty['points'],[]);self.assertEqual(json.loads(empty['settings_json']),new);r.step(1)
  result=r.invoke('result.read');self.assertAlmostEqual(result['points'][-1]['trigger_time_s'],.21);self.assertEqual(json.loads(result['settings_json']),new)
 def test_50_depleted_stocks_nonnegative_and_conserved(self):
  r=ready(method=dict(METHOD_EXAMPLE,stop_volume_m3=2.5e-6))
  for shot in range(2):
   if shot:flush(r)
   r.invoke('acquisition.start')
   while r.phase=='running':r.step(.007)
  ledger=r.invoke('product.read')
  for key in ['stock_a_volume_m3','stock_b_volume_m3','cell_volume_m3','stop_volume_m3','waste_volume_m3','stock_moiety_mol','cell_moiety_mol','stop_moiety_mol','waste_moiety_mol']:self.assertGreaterEqual(ledger[key],0.)
  self.assertLess(ledger['stock_a_volume_m3'],1e-18);self.assertLess(ledger['stock_b_volume_m3'],1e-18);self.assertLess(abs(ledger['volume_residual_m3']),1e-18);self.assertLess(abs(ledger['moiety_residual_mol']),1e-21)
  self.assertAlmostEqual(ledger['modeled_mass_residual_kg'],ledger['volume_residual_m3']*PLANT_EXAMPLE['fluid_density_kg_m3'])
  for key,value in r.read('evaluator').items():validate(SLOTS[key][0],value)
  flush(r);self.assertReject(r,'acquisition.start',{},'INSUFFICIENT_REAGENT')
 def test_51_historical_completion_snapshot_has_final_elapsed_and_owner(self):
  r=ready();r.register_principal('other');s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='first')['operation_id'];r.step(1)
  terminal=s.poll(op);self.assertEqual(terminal['status'],'succeeded');self.assertAlmostEqual(terminal['result']['elapsed_s'],.57);self.assertEqual(terminal['result']['completed_points'],101)
  flush(r);s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='second');before=r.debug_snapshot();self.assertEqual(s.poll(op),terminal)
  terminal['result']['completed_points']=999;self.assertEqual(s.poll(op)['result']['completed_points'],101)
  self.assertNotIn(SAMPLE_EXAMPLE['sample_id'],json.dumps(s.poll(op)));self.assertNotIn(SAMPLE_EXAMPLE['template_id'],json.dumps(s.poll(op)))
  with self.assertRaisesRegex(ActionRejected,'ACCESS_DENIED'):r.session('other').poll(op)
  self.assertEqual(before,r.debug_snapshot())
 def test_52_cancel_historical_terminal_does_not_cancel_current(self):
  r=ready();s=r.session();old=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='old')['operation_id'];r.step(1);flush(r)
  current=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='new')['operation_id'];r.step(.02);revision=r.revision;volumes=[r._va,r._vb,r._vs,r._vw]
  v=s.cancel(old,'cancel-old');self.assertEqual(v['result']['phase'],'succeeded');self.assertEqual(r.phase,'running');self.assertEqual(r.operation_id,current);self.assertEqual(r.revision,revision);self.assertEqual(volumes,[r._va,r._vb,r._vs,r._vw]);self.assertEqual(s.cancel(old,'cancel-old'),v)
  with self.assertRaisesRegex(ActionRejected,'REQUEST_CONFLICT'):s.cancel(current,'cancel-old')
  self.assertEqual(r.phase,'running')
 def test_53_cancelled_observations_and_transferred_liquid_stay_retained(self):
  r=ready();s=r.session();op=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='partial')['operation_id'];r.step(.083);points=copy.deepcopy(r.points);volumes=[r._va,r._vb,r._vs,r._vw]
  s.cancel(op,'stop-partial');terminal=s.poll(op);self.assertEqual(terminal['status'],'cancelled');self.assertEqual(terminal['result']['completed_points'],len(points));self.assertGreater(len(points),0);self.assertLess(len(points),101)
  r.step(1);self.assertEqual(r.points,points);self.assertEqual([r._va,r._vb,r._vs,r._vw],volumes);self.assertTrue(r.needs_flush)
  flush(r);s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='next');self.assertEqual(s.poll(op),terminal)
 def test_54_metadata_tracks_actual_calibration_and_drive_timing(self):
  r=ready();m=export_applied_sx20_contract_metadata();caps=m['capabilities'];before=r.read();r.invoke('calibration.zero',observed_blank=.02,mode='absorbance');after=r.read()
  self.assertEqual({key for key in before if before[key]!=after[key]},{'calibration.absorbance_correction'});self.assertEqual(after['calibration.absorbance_correction'],-.02);self.assertEqual(after['calibration.fluorescence_correction_v'],0.)
  self.assertEqual(m['state_slots']['calibration.absorbance_correction']['unit'],'1');self.assertEqual(m['state_slots']['calibration.fluorescence_correction_v']['unit'],'V');self.assertTrue(all(e['timing']=='immediate' for e in caps['calibration.zero']['state_effects']))
  before=r.read('evaluator');r.invoke('acquisition.start');started=r.read('evaluator');immediate={v['state_id'] for v in caps['acquisition.start']['state_effects'] if v['timing']=='immediate'};later={v['state_id'] for v in caps['acquisition.start']['state_effects'] if v['timing']=='on_step'}
  self.assertLessEqual({key for key in before if before[key]!=started[key]},immediate);self.assertNotIn('drive.stroke_m',immediate);r.step(.02);advanced=r.read('evaluator');self.assertLessEqual({key for key in started if started[key]!=advanced[key]},later);self.assertGreater(advanced['drive.stroke_m'],0.)
  self.assertEqual(caps['kinetics.fit']['observes'],[]);self.assertEqual(caps['power.set']['observes'],['power.enabled'])
if __name__=='__main__':unittest.main()


