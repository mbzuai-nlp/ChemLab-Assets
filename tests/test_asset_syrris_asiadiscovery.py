import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
import asset_tool
from asset_runtime.syrris_asiadiscovery import *
def prepared(method=None,**plant):
 h=SyrrisAsiadiscoveryRuntime();h.invoke('clamp.set_lift',lift_m=.04)
 if method:h.invoke('method.configure',**method)
 h.invoke('plant.configure',**dict(EXAMPLE,**plant));h.invoke('clamp.set_lift',lift_m=0.);h.invoke('power.set',enabled=True);return h
class AsiaTest(unittest.TestCase):
 def balance(self,h):
  v=h.read('evaluator')
  for suffix in ['volume_m3','mass_kg']:self.assertAlmostEqual(v['truth.initial_'+suffix],sum(v['truth.'+k+'_'+suffix] for k in ['retained','collected','waste','removed']),12)
  for i in range(2):self.assertAlmostEqual(v['truth.initial_tracer_mol'][i],sum(v['truth.'+k+'_tracer_mol'][i] for k in ['retained','collected','waste','removed']),14)
 def test_contract_schemas(self):
  m=export_syrris_asiadiscovery_contract_metadata();self.assertEqual(len(m['capabilities']),13)
  for cap in m['capabilities'].values():
   self.assertFalse(asset_tool.lint_schema(cap['input_schema']));self.assertFalse(asset_tool.lint_schema(cap['output_schema']));self.assertFalse(asset_tool.validate_value(cap['example_arguments'],cap['input_schema']))
 def test_all_78_templates_drive_runtime(self):
  ts=SyrrisAsiadiscoveryRuntime().templates;self.assertEqual(len(ts),78);self.assertEqual(len({t['cas'] for t in ts.values()}),78)
  for t in ts.values():
   f=t['fits'][0];lo,hi=f['runtime_temperature_range_k'];temp=(lo+hi)/2;h=prepared(substance_id=t['substance_id'],fit_id=f['fit_id'],initial_temperature_k=temp,low_temperature_available=True);p=1e5*10**(f['A']-f['B_k']/(temp+f['C_k']));self.assertAlmostEqual(h.vapor(temp)/p,1.,12);h.invoke('pressure.set',backpressure_pa=1.9e6)
   if p<2.001325e6:h.invoke('acquisition.start');h.step(.03);self.assertGreater(h.waste,0)
   else:
    with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
   self.balance(h)
 def test_window_transport_and_mass_tracer_conservation(self):
  h=prepared();h.invoke('acquisition.start');h.step(4.3);self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.elapsed_s,4.,10);self.assertAlmostEqual(sum(h.collected),sum(METHOD_EXAMPLE['flow_rates_m3_s'])*2.5,13);self.assertGreater(sum(h.collected_tracer[0]),0);self.balance(h)
  self.assertGreater(h.cells[6][0],0);self.assertGreater(h.cells[6][1],0);h.invoke('collector.unload',slot=0,container_id='fraction_0');self.balance(h)
 def test_ordered_injection_and_finite_dead_volume(self):
  h=prepared();h.invoke('acquisition.start');h.step(.2);self.assertGreater(h.cells[0][0],0);self.assertEqual(h.cells[1][1],0);self.assertGreater(h.loops[1],0);self.assertEqual(sum(h.collected),0);h.step(.4);self.assertGreater(h.cells[1][1],0);self.balance(h)
 def test_two_chip_volumes_and_RTD(self):
  for volume in [250e-9,1e-6]:
   h=prepared(chip_volume_m3=volume);r=h.invoke('residence.measure');self.assertAlmostEqual(r['chip_mean_s'],volume/sum(METHOD_EXAMPLE['flow_rates_m3_s']));self.assertGreater(r['mean_s'],r['chip_mean_s']);self.assertGreater(r['variance_s2'],r['chip_mean_s']**2/4)
 def test_each_syringe_range_and_channel_specific(self):
  for name,(lo,hi) in SYRINGES.items():
   m=copy.deepcopy(METHOD_EXAMPLE);m['flow_rates_m3_s']=[lo*ULMIN,hi*ULMIN];h=prepared(method=m,syringes=[name]*2);h.invoke('acquisition.start');h.step(.02);self.balance(h);h.invoke('acquisition.stop');bad=copy.deepcopy(m);bad['flow_rates_m3_s'][0]=max(ULMIN,(lo-1)*ULMIN) if lo>1 else (hi+1)*ULMIN
   with self.assertRaises(ActionRejected):h.invoke('method.configure',**bad)
 def test_numerical_pulse_breakthrough_RTD_moments(self):
  m=copy.deepcopy(METHOD_EXAMPLE);m.update(duration_s=20.,injection_times_s=[0.,20.],collection_windows=[]);h=prepared(method=m,loop_volumes_m3=[.1e-6]*2,loop_concentrations_mol_m3=[1.,0.]);r=h.invoke('residence.measure');h.invoke('acquisition.start');mass=first=second=0.;last=0.
  for _ in range(1000):
   h.step(.02);dm=h.waste_tracer[0]-last;last=h.waste_tracer[0];mid=h.elapsed_s-.01;mass+=dm;first+=dm*mid;second+=dm*mid*mid
  pulse=.1e-6/m['flow_rates_m3_s'][0];mean=first/mass;variance=second/mass-mean*mean
  self.assertAlmostEqual(mass,1e-7,12);self.assertAlmostEqual(mean,r['mean_s']+pulse/2,delta=.03);self.assertAlmostEqual(variance,r['variance_s2']+pulse*pulse/12,delta=.05);self.balance(h)
 def test_pressure_loss_and_guard(self):
  h=prepared();h.invoke('acquisition.start');p=h.pressures();q=METHOD_EXAMPLE['flow_rates_m3_s'];self.assertAlmostEqual(p[0],2e5+1e11*(sum(q)+q[0]));h.invoke('acquisition.stop');h.invoke('pressure.set',backpressure_pa=2e6);old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
  self.assertEqual(old,h.debug_snapshot())
 def test_vapor_guard_and_no_extrapolation(self):
  h=prepared();old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('temperature.set',target_k=423.15)
  self.assertEqual(old,h.debug_snapshot());h.invoke('temperature.set',target_k=356.7);h.invoke('pressure.set',backpressure_pa=1e5)
  with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
 def test_thermal_response_only_on_step(self):
  h=prepared();h.invoke('temperature.set',target_k=308.15);self.assertEqual(h.temp_k,298.15);h.step(1.);self.assertAlmostEqual(h.temp_k,308.15-10*math.exp(-.1),10)
 def test_cancel_and_restart_retains_holdup(self):
  h=prepared();h.invoke('acquisition.start');h.step(.5);old=copy.deepcopy(h.cells);loops=h.loops[:];h.invoke('acquisition.stop');h.step(.2);self.assertEqual(h.cells,old);self.assertEqual(h.loops,loops);h.invoke('acquisition.start');h.step(.2);self.assertNotEqual(h.cells,old);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.balance(h)
 def test_reservoir_and_collector_faults_retain_mass(self):
  h=prepared(reservoir_volumes_m3=[1e-7]*2);h.invoke('acquisition.start');h.step(3.);self.assertEqual(h.fault,'reservoir_empty');self.balance(h)
  m=copy.deepcopy(METHOD_EXAMPLE);m['duration_s']=40.;m['collection_windows']=[dict(start_s=0.,end_s=40.,slot=0)];h=prepared(method=m);h.invoke('acquisition.start');h.step(40.);self.assertEqual(h.fault,'collector_full');self.assertLessEqual(h.collected[0],5e-6+1e-15);self.balance(h)
 def test_rejected_inputs_atomic_and_interlock(self):
  h=prepared();old=copy.deepcopy(h.__dict__)
  for cap,args in [('clamp.set_lift',{'lift_m':True}),('pressure.set',{'backpressure_pa':float('nan')}),('collector.unload',{'slot':.0,'container_id':'fraction_0'}),('collector.unload',{'slot':8,'container_id':'fraction_8'})]:
   with self.assertRaises(ActionRejected):h.invoke(cap,**args)
   self.assertEqual(old,h.__dict__)
  m=copy.deepcopy(METHOD_EXAMPLE);m['collection_windows']=[dict(start_s=0.,end_s=3.,slot=0),dict(start_s=2.,end_s=4.,slot=1)]
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**m)
  self.assertEqual(old,h.__dict__);h.invoke('acquisition.start')
  with self.assertRaises(ActionRejected):h.invoke('clamp.set_lift',lift_m=.04)
 def test_reads_pure_unknown_products(self):
  h=prepared();h.invoke('acquisition.start');h.step(.3);old=copy.deepcopy(h.__dict__)
  for _ in range(3):h.read();h.describe();h.invoke('flow.measure');h.invoke('history.read');h.invoke('product.read');h.invoke('residence.measure')
  self.assertEqual(old,h.__dict__);self.assertIsNone(h.invoke('flow.measure')['yield_fraction']);self.assertIsNone(h.invoke('product.read')['reaction_products'])
 def test_isolation_identity_filter_dedup_async(self):
  h=prepared();b=SyrrisAsiadiscoveryRuntime('other');s=h.session();args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**args);self.assertEqual(r['status'],'accepted');self.assertEqual(s.invoke(**args),r);h.step(.2);self.assertEqual(s.poll(r['operation_id'])['status'],'running');self.assertNotIn('truth.cell_tracer_mol',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities']);self.assertFalse(b.powered)
  x=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(x['error']['code'],'ACCESS_DENIED');self.assertEqual(s.cancel(r['operation_id'],'cancel')['status'],'cancelled');self.balance(h)
  self.assertEqual(s.write(instance_id=h.instance_id,state_id='pressure.backpressure_pa',value=3e5,request_id='bp')['status'],'succeeded')
 def test_all_actual_outputs_and_slots_schema(self):
  h=SyrrisAsiadiscoveryRuntime()
  def ck(k,a=None):
   r=h.invoke(k,**(CAPS[k][6] if a is None else a));self.assertFalse(asset_tool.validate_value(r,CAPS[k][1]),k)
  for k in ['clamp.set_lift','plant.configure','power.set','method.configure','temperature.set','pressure.set']:ck(k)
  ck('clamp.set_lift',{'lift_m':0.});ck('acquisition.start');h.step(1.)
  for k in ['flow.measure','history.read','product.read','residence.measure','acquisition.stop']:ck(k)
  ck('collector.unload')
  for k,v in h.read('evaluator').items():self.assertFalse(asset_tool.validate_value(v,SLOTS[k][0]),k)
 def test_bad_dt(self):
  h=prepared()
  for dt in [0,-1,True,float('nan'),float('inf')]:
   with self.assertRaises(ValueError):h.step(dt)
if __name__=='__main__':unittest.main()
