import unittest,copy,pickle,math,sys
from pathlib import Path
from unittest import mock
import numpy as np,cv2
from scripts.asset_runtime import antonpaar_litesizerdia as r
O=Path(__file__).resolve().parents[1]/'outputs/expansion_batch03/t4_measure/antonpaar_litesizerdia';sys.path.insert(0,str(O));from example_setup import build
class TestDia(unittest.TestCase):
 def done(self,h,dt=6.):h.invoke('acquisition.start');h.step(dt);self.assertEqual(h.phase,'succeeded',h.error);return h
 def test01_source(self):
  self.assertEqual(r.LIB['coverage']['raw_binary_images'],384);self.assertEqual(r.LIB['coverage']['original_particle_records'],6000);self.assertEqual(r.LIB['coverage']['named_valid_powder_records'],97)
 def test02_rectangle_feret(self):
  a=np.ones((30,40),np.uint8);v=r.descriptors(a,2e-6);self.assertAlmostEqual(v['area_m2'],1200*4e-12);self.assertAlmostEqual(v['feret_min_m'],60e-6);self.assertAlmostEqual(v['feret_max_m'],100e-6);self.assertAlmostEqual(v['aspect_ratio'],.6)
 def test03_circle(self):
  a=np.zeros((221,221),np.uint8);cv2.circle(a,(110,110),100,1,-1);v=r.descriptors(a,1e-6);self.assertLess(abs(v['circularity']-1),.01);self.assertLess(abs(v['equivalent_diameter_m']/200e-6-1),.005)
 def test04_rle_exact(self):
  a=np.random.default_rng(0).integers(0,2,(18,29));self.assertTrue(np.array_equal(a,r.decode_rle(a.shape,r.rle(a))))
 def test05_real_outline_metadata(self):
  ratios=[]
  for sid in ['grain_capecod','grain_fijipink','grain_hawaii','grain_ottawa1220','grain_peaceriver','grain_vieques']:
   for i in [0,20,40,63]:
    mask,pitch,*_=r.particle(sid,i);d=r.descriptors(mask,pitch);ref=r.LIB['substances'][sid]['value']['images'][i]['metadata'];ratios.append(abs(d['feret_max_m']/ref['FERET_MAX']-1));self.assertLess(abs(d['aspect_ratio']-ref['ASPECT_RATIO']),.035)
  self.assertLess(max(ratios),.04)
 def test06_three_modes(self):
  for mode in ['liquid','dry','freefall']:
   h=self.done(build(mode));self.assertEqual(len(h.images),32);self.assertGreater(h.summary['d50_m'],0.);self.assertEqual(h.frames,880);self.assertLess(abs(h._ledger()['solid_error_kg']),1e-15)
 def test07_read_pure(self):
  h=build();before=pickle.dumps(h.__dict__)
  for _ in range(5):h.read();h.describe();h._result()
  self.assertEqual(before,pickle.dumps(h.__dict__))
 def test08_setpoint_only(self):
  h=build();t=h.time_s;temp=h.temperature;h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,ultrasound_w=50.));self.assertEqual(h.temperature,temp);self.assertEqual(h.time_s,t)
 def test09_wet_recirculate(self):
  h=self.done(build(count=8));self.assertEqual(len(h.population),8);self.assertEqual(h.waste_n,0);self.assertEqual(h.pass_index,32)
 def test10_dry_finite(self):
  h=self.done(build('dry',count=12));self.assertEqual(h.waste_n,12);self.assertEqual(len(h.population),0);self.assertEqual(len(h.images),12);self.assertGreater(h.air_used,0)
 def test11_freefall_recover(self):
  h=self.done(build('freefall',count=12));self.assertEqual(len(h.collected),12);h.invoke('power.set',enabled=False);h.invoke('lid.set',angle_rad=1.55);h.invoke('sample.recover');self.assertEqual(h.returned_n,12);self.assertAlmostEqual(h.loaded_m,h.returned_m);self.assertEqual(h.air_used,0.)
 def test12_drain_mass_heat(self):
  h=self.done(build(method={'ultrasound_w':50.}));self.assertGreater(h.temperature,293.15);h.invoke('liquid.drain');h.step(1.);self.assertGreater(h.waste_n,0);self.assertLess(h.waste_n,128);self.assertEqual(h._ledger()['particles_error'],0);h.step(10.);self.assertEqual(h.liquid,0);self.assertEqual(h.waste_n,128);l=h._ledger();self.assertLess(abs(l['solid_error_kg']),1e-15);self.assertLess(abs(l['heat_error_j']),1e-6)
 def test13_cancel_partial(self):
  h=build('dry');h.invoke('acquisition.start');h.step(.5);mass=h.waste_m;air=h.air_used;n=len(h.images);h.invoke('acquisition.stop');h.step(1.);self.assertEqual(h.waste_m,mass);self.assertEqual(h.air_used,air);self.assertEqual(len(h.images),n);self.assertGreater(n,0)
 def test14_air_exhaust(self):
  h=build('dry');h.air=.001;h.air_added=.001;h.invoke('acquisition.start');h.step(1.);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'AIR_EXHAUSTED');self.assertAlmostEqual(h.air,0.)
 def test15_atomic_reject(self):
  h=build()
  for k,a in [('power.set',{'enabled':1}),('air.add',{'amount_mol':float('nan')}),('module.withdraw',{'distance_m':.3}),('method.configure',dict(r.METHOD_EXAMPLE,mode='dry'))]:
   before=pickle.dumps(h.__dict__)
   with self.assertRaises(r.ActionRejected):h.invoke(k,**a)
   self.assertEqual(before,pickle.dumps(h.__dict__))
 def test16_access(self):
  h=build();h.invoke('lid.set',angle_rad=1.55)
  with self.assertRaisesRegex(r.ActionRejected,'CLOSURE'):h.invoke('acquisition.start')
  h.invoke('lid.set',angle_rad=0.);h.invoke('acquisition.start')
  with self.assertRaisesRegex(r.ActionRejected,'BUSY'):h.invoke('lid.set',angle_rad=1.55)
 def test17_truth_and_instances(self):
  h=build();g=build('freefall');self.done(h);self.assertEqual(g.phase,'idle');a=g.session();d=a.describe(instance_id=g.instance_id);self.assertNotIn('plant.configure',d['capabilities']);self.assertNotIn('truth.solid_kg',a.read(instance_id=g.instance_id)['observations'])
  with self.assertRaises(r.ActionRejected):a.read(instance_id=g.instance_id,state_ids=['truth.solid_kg'])
  self.assertEqual(a.invoke(instance_id=g.instance_id,capability_id='ledger.read',arguments={},request_id='x')['status'],'rejected')
 def test18_principal_registration(self):
  h=r.AntonpaarLitesizerdiaRuntime();h.register_principal('matterix_agent');h.register_principal('matterix_evaluator','evaluator')
  with self.assertRaises(r.ActionRejected):h.register_principal('matterix_agent','evaluator')
  with self.assertRaises(r.ActionRejected):h.session('unknown')
 def test19_idempotent(self):
  h=build();a=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='x');first=a.invoke(**kw);h.step(.5);second=a.invoke(**kw);self.assertEqual(first,second);self.assertEqual(a.invoke(**dict(kw,capability_id='power.set',arguments={'enabled':False}))['error']['code'],'REQUEST_CONFLICT')
 def test20_async_ownership(self):
  h=build();h.register_principal('other');a=h.session();op=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='x')['operation_id'];h.step(.5)
  with self.assertRaises(r.ActionRejected):h.session('other').poll(op)
  a.cancel(op,'cancel');self.assertEqual(a.poll(op)['status'],'cancelled');partial=len(a.poll(op)['result']['particles']);self.assertGreater(partial,0);h.invoke('acquisition.start');self.assertEqual(len(a.poll(op)['result']['particles']),partial)
 def test21_external_calibration(self):
  h=build(plant={'true_pixel_at_1x_m':3.3e-6});h.invoke('calibration.pixel',reference_length_m=330e-6,observed_pixels=100.);self.done(h);ref=self.done(build());self.assertLess(abs(h.summary['d50_m']/ref.summary['d50_m']-1),.01)
 def test22_scale_bias_independent(self):
  h=self.done(build(method={'pixel_size_m':6e-6}));ref=self.done(build());self.assertAlmostEqual(h.summary['d50_m']/ref.summary['d50_m'],2.)
 def test23_high_throughput(self):
  h=self.done(build('dry',method={'feed_particles_s':500.,'fps':10.,'target_images':200,'duration_s':1.},count=1000),1.);self.assertGreater(h.missed,0);self.assertLessEqual(len(h.images),10);self.assertEqual(h.waste_n,500)
 def test24_resolution_clipping(self):
  h=build(method={'magnification':6.,'pixel_size_m':.5e-6,'target_images':256,'duration_s':8.});h.invoke('acquisition.start');h.step(8.);self.assertGreater(h.rejected,0)
 def test25_reconstruction_explicit(self):
  with self.assertRaises(r.ActionRejected):build(sid='powder_001')
  h=self.done(build(sid='powder_001',method={'accept_shape_reconstruction':True}));self.assertIn('synthetic_outline',h.images[0]['evidence'])
  with self.assertRaises(r.ActionRejected):build(sid='powder_050',method={'accept_shape_reconstruction':True})
  with self.assertRaises(r.ActionRejected):build(method={'accept_inferred_source_scale':False})
 def test26_clock_partition(self):
  h=build();g=build();h.invoke('acquisition.start');g.invoke('acquisition.start');h.step(1.23)
  for dt in [.333,.121,.449,.327]:g.step(dt)
  self.assertEqual(h.read(),g.read());self.assertEqual(h._result(),g._result())
 def test27_unsupported_no_fake_sensor(self):
  h=build()
  for k in r.UNSUPPORTED:
   with self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED'):h.invoke(k)
  self.assertNotIn('temperature.measured_k',h.read());self.assertNotIn('truth.temperature_k',h.read('agent'))
 def test28_query_cannot_steal_operation(self):
  h=build();h.register_principal('observer');a=h.session();o=h.session('observer');op=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='owner')['operation_id'];reply=o.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='read');self.assertEqual(reply['status'],'succeeded');self.assertIsNone(reply['operation_id'])
  with self.assertRaises(r.ActionRejected):o.poll(op)
  self.assertEqual(a.poll(op)['status'],'running')
 def test29_result_source_stable(self):
  h=self.done(build('freefall'));source=h._result()['template_source'];original=copy.deepcopy(h._result());h.invoke('power.set',enabled=False);h.invoke('lid.set',angle_rad=1.55);h.invoke('sample.recover');h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,mode='freefall',accept_shape_reconstruction=True));h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,substance_id='powder_001'));self.assertEqual(h._result()['template_source'],source);h.invoke('calibration.pixel',reference_length_m=600e-6,observed_pixels=100.);self.assertEqual(h._result(),original)
 def test30_recovery_truth_requires_evaluator(self):
  h=self.done(build('freefall',count=12));h.invoke('power.set',enabled=False);h.invoke('lid.set',angle_rad=1.55);agent=h.session();evaluator=h.session('evaluator');self.assertNotIn('sample.recover',agent.describe(instance_id=h.instance_id)['capabilities']);before=pickle.dumps(h.__dict__)
  denied=agent.invoke(instance_id=h.instance_id,capability_id='sample.recover',arguments={},request_id='denied');self.assertEqual(denied['error']['code'],'ACCESS_DENIED');self.assertEqual(before,pickle.dumps(h.__dict__));recovered=evaluator.invoke(instance_id=h.instance_id,capability_id='sample.recover',arguments={},request_id='recover');self.assertEqual(recovered['status'],'succeeded');self.assertEqual(recovered['result']['particles'],12);self.assertAlmostEqual(recovered['result']['mass_kg'],h.loaded_m);self.assertLess(abs(h._ledger()['solid_error_kg']),1e-15)
 def test31_metadata_covers_actual_state_changes(self):
  metadata=r.export_antonpaar_litesizerdia_contract_metadata();original=r.AntonpaarLitesizerdiaRuntime.invoke;observed=set()
  def audited_invoke(h,cap,**arguments):
   before=h.read();result=original(h,cap,**arguments);after=h.read();changed={state for state in before if before[state]!=after[state]};declared={effect['state_id'] for effect in metadata['capabilities'][cap]['state_effects'] if effect['timing']=='immediate'}
   self.assertFalse(changed-declared,(cap,changed-declared));observed.add(cap);return result
  with mock.patch.object(r.AntonpaarLitesizerdiaRuntime,'invoke',audited_invoke):
   h=build(method={'ultrasound_w':50.});h.invoke('acquisition.start');h.invoke('sim.step',dt_s=6.);h.invoke('liquid.drain');h.invoke('sim.step',dt_s=11.);h.invoke('lid.set',angle_rad=1.55);h.invoke('liquid.fill',volume_m3=.0003);h.invoke('calibration.pixel',reference_length_m=.000600,observed_pixels=100.);h.invoke('method.configure',**dict(h.method,magnification=6.,pixel_size_m=.5e-6,threshold=.6));h.invoke('power.set',enabled=False);h.invoke('service.set',distance_m=.16);h.invoke('service.set',distance_m=0.)
   for mode in ['dry','freefall']:
    d=build(mode);d.invoke('acquisition.start');d.invoke('sim.step',dt_s=.5);d.invoke('acquisition.stop');d.invoke('power.set',enabled=False);d.invoke('lid.set',angle_rad=1.55);d.invoke('sample.recover')
   q=r.AntonpaarLitesizerdiaRuntime();q.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,ambient_k=298.15));q.invoke('power.set',enabled=True);q.invoke('background.capture');q.invoke('power.set',enabled=False);q.invoke('latch.set',angle_rad=.65);q.invoke('module.withdraw',distance_m=.32);q.invoke('module.select',mode='dry')
  self.assertTrue(set(r.IMMEDIATE_EFFECTS)<=observed)
if __name__=='__main__':unittest.main()
