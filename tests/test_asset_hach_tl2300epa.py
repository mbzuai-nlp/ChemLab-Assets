import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime.hach_tl2300epa import HachTl2300epaRuntime as Runtime,CAPS,SLOTS,ActionRejected,export_hach_tl2300epa_contract_metadata
from asset_tool import validate_value,lint_schema
class TestTL2300(unittest.TestCase):
 def ready(self,**overrides):
  h=Runtime();h.invoke('power.set',enabled=True);h.step(3600);h.invoke('lid.set_angle',angle_rad=1.7)
  a=copy.deepcopy(CAPS['sample.configure'][6]);a.update(overrides);h.invoke('sample.configure',**a);h.invoke('lid.set_angle',angle_rad=0.);return h
 def measure(self,h,n=1,ratio=True,mode='single'):
  h.invoke('method.configure',ratio_enabled=ratio,average_count=n,mode=mode);h.invoke('acquisition.start');h.step(6.8 if n==1 else 14.);return h.invoke('turbidity.measure')
 def reject_unchanged(self,h,cap,a):
  before=copy.deepcopy(h.__dict__)
  with self.assertRaises(ActionRejected):h.invoke(cap,**a)
  self.assertEqual(before,h.__dict__)
 def test_read_describe_pure_targets_no_time(self):
  h=self.ready();before=copy.deepcopy(h.__dict__);h.read();h.describe();h.session().read(instance_id=h.instance_id);h.invoke('turbidity.measure');self.assertEqual(before,h.__dict__)
  h.invoke('method.configure',ratio_enabled=False,average_count=10,mode='continuous');self.assertEqual(h.time_s,3600.);self.assertIsNone(h.indicated)
 def test_vendor_timing_resolution_and_ranges(self):
  for n,duration in [(1,6.8),(10,14.)]:
   h=self.ready();h.invoke('method.configure',ratio_enabled=True,average_count=n,mode='single');h.invoke('acquisition.start');h.step(duration-.01);self.assertEqual(h.phase,'running');self.assertIsNone(h.indicated);h.step(.01);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.indicated,20.);self.assertAlmostEqual(h.window[1]-h.window[0],duration)
  for sid,ratio,expected in [('stablcal_4000',True,4000.),('stablcal_200',False,None),('stablcal_20',False,20.)]:self.assertEqual(self.measure(self.ready(substance_id=sid),ratio=ratio)['ntu'],expected)
  self.assertAlmostEqual(self.measure(self.ready(substance_id='stablcal_1000'))['reference_accuracy_ntu'],20.01)
  self.assertEqual(self.measure(self.ready(substance_id='stablcal_4000'))['reference_accuracy_ntu'],200.)
 def test_warmup_filter_clean_cell_and_bounds(self):
  h=Runtime();self.reject_unchanged(h,'acquisition.start',{});h.invoke('power.set',enabled=True);h.step(1799.9);self.assertFalse(h.read()['warmup.ready']);h.step(.1);self.assertTrue(h.read()['warmup.ready']);h.invoke('method.configure',ratio_enabled=False,average_count=1,mode='single');self.assertFalse(h.read()['warmup.ready'])
  self.reject_unchanged(h,'filter.set_installed',{'installed':False})
  h.invoke('power.set',enabled=False);h.invoke('filter.set_installed',installed=False);self.assertFalse(h.filter_installed)
  self.reject_unchanged(self.ready(clean_indexed_cell=False),'acquisition.start',{})
  for key,value in [('volume_m3',19e-6),('volume_m3',31e-6),('temperature_k',343.16),('mass_kg',True),('color_absorbance',float('nan'))]:
   h=Runtime();h.invoke('lid.set_angle',angle_rad=1.7);a=copy.deepcopy(CAPS['sample.configure'][6]);a[key]=value;self.reject_unchanged(h,'sample.configure',a)
 def test_integer_unknown_and_busy_atomic(self):
  h=self.ready()
  for n in [True,1.,1.5,2,0]:self.reject_unchanged(h,'method.configure',{'ratio_enabled':True,'average_count':n,'mode':'single'})
  self.reject_unchanged(h,'method.configure',{'ratio_enabled':True,'average_count':1,'mode':'rst'})
  h.invoke('acquisition.start');self.reject_unchanged(h,'sample.unload',{});self.reject_unchanged(h,'method.configure',CAPS['method.configure'][6])
 def test_mass_volume_conservation_and_unload(self):
  h=self.ready();self.measure(h);h.invoke('lid.set_angle',angle_rad=1.7);h.invoke('sample.unload');v=h.read('evaluator')
  for ax in ['kg','m3']:self.assertAlmostEqual(v['sample.input_'+ax],v['sample.retained_'+ax]+v['sample.removed_'+ax])
  self.assertIsNone(h.indicated);self.assertIsNone(h.invoke('turbidity.measure')['tss_kg_m3'])
 def test_optical_color_particle_and_exact_bubble_integral(self):
  for ratio in [True,False]:
   h=self.ready(color_absorbance=1.,particle_response_gain=.5,bubble_excess_ntu=10.,bubble_tau_s=30.);r=self.measure(h,ratio=ratio);v=(10+10*30/6.8*(1-math.exp(-6.8/30)))*(1 if ratio else math.exp(-1));self.assertEqual(r['ntu'],round(v,3));self.assertFalse(r['stable'])
  a=self.ready(bubble_excess_ntu=10.);b=self.ready(bubble_excess_ntu=10.);a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(6.8)
  for _ in range(68):b.step(.1)
  self.assertAlmostEqual(a.raw_mean,b.raw_mean,places=10)
 def test_calibration_reference_and_persistence(self):
  h=self.ready(particle_response_gain=.8);self.measure(h);self.assertEqual(h.indicated,16.);h.invoke('calibration.rapid');self.assertAlmostEqual(h.gain,1.25);self.assertEqual(h.indicated,20.);self.assertTrue(h.invoke('calibration.verify',reference_ntu=20.)['within_five_percent']);self.assertEqual(h._cal_status(),'rapid_0_40')
  h.invoke('power.set',enabled=False);self.assertTrue(h.calibrated);self.assertFalse(h.stable)
  x=self.ready(substance_id='stablcal_200');self.measure(x);self.reject_unchanged(x,'calibration.rapid',{})
 def test_async_complete_cancel_and_owner(self):
  h=self.ready(bubble_excess_ntu=4.);s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=r['operation_id'];self.assertEqual(r['status'],'accepted');h.step(2.);before=h.bubbles;s.cancel(op,'cancel');self.assertEqual(s.poll(op)['status'],'cancelled');self.assertEqual(h.bubbles,before);self.assertEqual(h.elapsed_s,2.)
  h.register_principal('second');other=h.session('second')
  with self.assertRaises(ActionRejected):other.poll(op)
  h=self.ready();s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='x');h.step(6.8);self.assertEqual(s.poll(r['operation_id'])['status'],'succeeded')
 def test_continuous_lid_power_cancel(self):
  h=self.ready();self.measure(h,mode='continuous');self.assertEqual(h.phase,'running');first=h.window;h.step(6.8);self.assertGreater(h.window[1],first[1]);h.invoke('lid.set_angle',angle_rad=.2);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.indicated,20.)
  h=self.ready();h.invoke('acquisition.start');h.step(1.);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.elapsed_s,1.)
 def test_principal_truth_isolation_dual_instances_idempotency(self):
  h=self.ready();other=Runtime();s=h.session();desc=s.describe(instance_id=h.instance_id);self.assertNotIn('sample.configure',desc['capabilities']);self.assertNotIn('sample.reference_ntu',desc['state_slots'])
  with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['sample.reference_ntu'])
  r=s.invoke(instance_id=h.instance_id,capability_id='sample.configure',arguments=CAPS['sample.configure'][6],request_id='deny');self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertNotIn('stablcal',json.dumps(r))
  h.register_principal('eval','evaluator');self.assertIn('sample.reference_ntu',h.session('eval').read(instance_id=h.instance_id)['observations']);self.assertFalse(other.powered);self.assertIsNone(other.sample)
  r=s.write(instance_id=h.instance_id,state_id='power.enabled',value=True,request_id='same');rev=h.revision;self.assertEqual(r,s.write(instance_id=h.instance_id,state_id='power.enabled',value=True,request_id='same'));self.assertEqual(h.revision,rev);self.assertEqual(s.write(instance_id=h.instance_id,state_id='power.enabled',value=False,request_id='same')['error']['code'],'REQUEST_CONFLICT')
 def test_all_capability_outputs_and_slot_schema(self):
  h=self.ready();results={'power.set':h.invoke('power.set',enabled=True),'lid.set_angle':h.invoke('lid.set_angle',angle_rad=0.),'sample.configure':{'configured':True},'method.configure':h.invoke('method.configure',**CAPS['method.configure'][6]),'acquisition.start':h.invoke('acquisition.start')};h.step(6.8);results['calibration.rapid']=h.invoke('calibration.rapid');results['calibration.verify']=h.invoke('calibration.verify',reference_ntu=20.);results['turbidity.measure']=h.invoke('turbidity.measure');results['acquisition.stop']=h.invoke('acquisition.stop');h.invoke('lid.set_angle',angle_rad=1.7);results['sample.unload']=h.invoke('sample.unload');h.invoke('power.set',enabled=False);results['filter.set_installed']=h.invoke('filter.set_installed',installed=True)
  configured=Runtime();configured.invoke('lid.set_angle',angle_rad=1.7);results['sample.configure']=configured.invoke('sample.configure',**CAPS['sample.configure'][6])
  self.assertEqual(set(results),set(CAPS))
  for key,row in CAPS.items():self.assertEqual(validate_value(results[key],row[1]),[]);self.assertEqual(lint_schema(row[0]),[]);self.assertEqual(lint_schema(row[1]),[])
  for k,v in h.read('evaluator').items():self.assertEqual(validate_value(v,SLOTS[k][0]),[],k)
  self.assertEqual(len(export_hach_tl2300epa_contract_metadata()['capabilities']),11)
 def test_every_template_real_or_censored(self):
  base=Runtime();counts={'water':0,'nominal':0,'blocked':0}
  for sid,row in base.templates.items():
   if not row['runtime_eligible']:
    h=Runtime();h.invoke('lid.set_angle',angle_rad=1.7);a=copy.deepcopy(CAPS['sample.configure'][6]);a['substance_id']=sid;self.reject_unchanged(h,'sample.configure',a);counts['blocked']+=1;continue
   h=self.ready(substance_id=sid);r=self.measure(h);self.assertEqual(r['ntu'],round(row['value'],3));self.assertIsNone(r['tss_kg_m3']);self.assertTrue(row['source']);counts['water' if row['material_class']=='environmental_water_mixture' else 'nominal']+=1
  self.assertEqual(counts,{'water':79,'nominal':4,'blocked':6})
 def test_mean_observation_retains_sampling_window(self):
  h=self.ready();self.measure(h,n=10);s=h.session();a=s.read(instance_id=h.instance_id,state_ids=['turbidity.indicated_ntu'])['observations']['turbidity.indicated_ntu'];h.step(7.);b=s.read(instance_id=h.instance_id,state_ids=['turbidity.indicated_ntu'])['observations']['turbidity.indicated_ntu'];self.assertEqual(a,b);self.assertAlmostEqual(b['sample_window_s'][1]-b['sample_window_s'][0],14.);self.assertEqual(b['sample_time_s'],h.window[1]);h.invoke('power.set',enabled=False);self.assertEqual(s.read(instance_id=h.instance_id,state_ids=['turbidity.indicated_ntu'])['observations']['turbidity.indicated_ntu']['quality'],'stale')
 def test_trusted_initial_condition_is_not_clock_advance(self):
  h=Runtime(initial_powered=True,prior_powered_time_s=3600.);self.assertEqual(h.time_s,0.);self.assertEqual(h.warmup_s,3600.);self.assertTrue(h.read()['warmup.ready']);h.step(1.);self.assertEqual(h.time_s,1.);self.assertEqual(h.warmup_s,3601.);h.invoke('power.set',enabled=False);self.assertEqual(h.warmup_s,0.)
  for kwargs in [{'prior_powered_time_s':1.},{'initial_powered':1},{'initial_powered':True,'prior_powered_time_s':float('nan')},{'initial_powered':True,'prior_powered_time_s':-1.}]:
   with self.assertRaises(ValueError):Runtime(**kwargs)
if __name__=='__main__':unittest.main()
