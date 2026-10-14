"""KDL1 Basic runtime, finite paired transfers and actual public entrypoints."""
import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.uic_kdl1basic import *
def ready(**plant):
 r=UicKdl1basicRuntime();r.invoke('plant.configure',**{**PLANT_EXAMPLE,**plant});r.invoke('method.configure',**METHOD_EXAMPLE);r.invoke('sample.configure',**example_sample());r.invoke('power.set',enabled=True);return r
def total(r):return r.feed+r.film.sum(axis=0)+r.products.sum(axis=(0,1))+r.trap+r.exhaust
class Kdl1Tests(unittest.TestCase):
 def test_sources_50_and_formula(self):
  r=ready();self.assertGreaterEqual(len(r.templates),50)
  for sid,t in r.templates.items():
   for f in t['fits']:
    for q in f['reference_points']:self.assertAlmostEqual(r._psat(sid,f['fit_id'],q['temperature_k']),q['pressure_pa'])
 def test_basic_excludes_upgrades(self):
  r=ready();s=r.debug_snapshot()
  for cap,args in [('temperature.set',dict(temperature_k=523.15)),('vacuum.set',dict(pressure_pa=.1))]:
   with self.assertRaises(ActionRejected):r.invoke(cap,**args)
   self.assertEqual(s,r.debug_snapshot())
 def test_shared_utility_limit(self):
  r=UicKdl1basicRuntime();r.invoke('plant.configure',**{**PLANT_EXAMPLE,'vacuum_min_pa':200.})
  with self.assertRaises(ActionRejected):r.invoke('method.configure',**METHOD_EXAMPLE)
  r=UicKdl1basicRuntime();r.invoke('plant.configure',**{**PLANT_EXAMPLE,'heating_max_k':423.15})
  with self.assertRaises(ActionRejected):r.invoke('method.configure',**{**METHOD_EXAMPLE,'wall_temperature_k':450.})
 def test_explicit_boundaries(self):
  for k in ['compatible_vacuum_confirmed','compatible_heating_confirmed','cooling_confirmed','vent_confirmed','cold_trap_confirmed','low_viscosity_liquid_reviewed','ideal_mixture_reviewed']:
   r=UicKdl1basicRuntime();s=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.invoke('plant.configure',**{**PLANT_EXAMPLE,k:False})
   self.assertEqual(s,r.debug_snapshot())
 def test_targets_no_instant_change(self):
  r=ready();a=r.read();r.invoke('temperature.set',temperature_k=330.);r.invoke('condenser.set',temperature_k=280.);r.invoke('vacuum.set',pressure_pa=10.);r.invoke('wiper.set',speed_rad_s=15.);b=r.read()
  for k in ['thermal.wall_temperature_k','thermal.condenser_temperature_k','vacuum.pressure_pa','wiper.speed_rad_s','wiper.angle_rad']:self.assertEqual(a[k],b[k])
 def test_read_describe_queries_pure(self):
  r=ready();s=r.debug_snapshot();r.read();r.describe();r.invoke('product.read');r.invoke('distillation.measure');r.invoke('history.read');self.assertEqual(s,r.debug_snapshot())
 def test_invalid_and_busy_atomic(self):
  r=ready();r.invoke('acquisition.start');s=r.debug_snapshot()
  for cap,args in [('sample.configure',example_sample()),('method.configure',METHOD_EXAMPLE),('wiper.set',dict(speed_rad_s=float('nan'))),('feed.set',dict(rate_kg_s=True))]:
   with self.assertRaises(ActionRejected):r.invoke(cap,**args)
   self.assertEqual(s,r.debug_snapshot())
 def test_finite_feed_cap(self):
  r=UicKdl1basicRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('method.configure',**METHOD_EXAMPLE);s=example_sample();s['species'][0]['amount_mol']=10
  with self.assertRaises(ActionRejected):r.invoke('sample.configure',**s)
  self.assertIsNone(r.sample)
 def test_unique_species(self):
  r=UicKdl1basicRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('method.configure',**METHOD_EXAMPLE);s=example_sample();s['species'][1]=copy.deepcopy(s['species'][0])
  with self.assertRaises(ActionRejected):r.invoke('sample.configure',**s)
 def test_fit_range_no_extrapolation(self):
  r=ready();s=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('temperature.set',temperature_k=450.)
  self.assertEqual(s,r.debug_snapshot())
 def test_unknown_boiling_point_but_high_known_vapor_pressure(self):
  r=UicKdl1basicRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('method.configure',**METHOD_EXAMPLE);s=example_sample();s['species']=[dict(substance_id='ammonia',fit_id='0',amount_mol=.01,reference_molar_volume_m3_mol=.0001)];self.assertIsNone(r.templates['ammonia']['normal_boiling_point_k']);before=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('sample.configure',**s)
  self.assertEqual(before,r.debug_snapshot())
 def test_start_conditioning(self):
  r=ready();r.invoke('acquisition.start');r.step(1);self.assertEqual(float(r.film.sum()),0);np.testing.assert_allclose(r.feed,r.initial)
 def test_mass_and_products(self):
  r=ready();r.invoke('acquisition.start');r.step(100);np.testing.assert_allclose(total(r),r.initial,atol=2e-13,rtol=1e-12);self.assertGreater(r.products[0].sum(),0);self.assertGreater(r.products[1].sum(),0);self.assertGreater(r.trap.sum(),0);self.assertGreater(r.exhaust.sum(),0);self.assertGreater(r.film.sum(),0)
  self.assertTrue(np.all(r.film>=0));self.assertTrue(np.all(r.products@r.vm<=.0001+1e-12));self.assertLessEqual(float(r.feed@r.vm),.00025)
 def test_no_evap_accommodation_zero(self):
  r=ready(effective_accommodation=0.);r.invoke('acquisition.start');r.step(80);self.assertEqual(r.products[0].sum(),0);self.assertGreater(r.products[1].sum(),0);np.testing.assert_allclose(total(r),r.initial,atol=1e-13)
 def test_wiper_changes_residence(self):
  a=ready();b=ready();b.invoke('wiper.set',speed_rad_s=20.)
  for r in [a,b]:r.invoke('acquisition.start');r.step(50)
  self.assertAlmostEqual(a.tau,2*b.tau);self.assertLess(float(b.film.sum()),float(a.film.sum()))
 def test_condenser_changes_capture(self):
  a=ready();b=ready();b.invoke('condenser.set',temperature_k=323.15)
  for r in [a,b]:r.invoke('acquisition.start');r.step(80)
  self.assertGreater(a.products[0].sum(),b.products[0].sum());self.assertGreater(b.trap.sum()+b.exhaust.sum(),a.trap.sum()+a.exhaust.sum())
 def test_finite_trap_spill_ledger(self):
  r=ready(trap_capacity_m3=1e-8);r.invoke('acquisition.start');r.step(100);self.assertLessEqual(float(r.trap@r.vm),1e-8+1e-14);self.assertGreater(r.exhaust.sum(),0);np.testing.assert_allclose(total(r),r.initial,atol=1e-13)
 def test_two_cuts_plus_spare(self):
  r=ready();r.invoke('acquisition.start');r.step(50);old=r.products[:,0].copy();r.invoke('fraction.select',stream='distillate',slot=1);r.invoke('fraction.select',stream='concentrate',slot=1);r.step(40);np.testing.assert_allclose(r.products[:,0],old);self.assertTrue(np.all(r.products[:,1].sum(axis=1)>0));self.assertEqual(r.products[:,2].sum(),0)
  with self.assertRaises(ActionRejected):r.invoke('fraction.select',stream='distillate',slot=2)
  with self.assertRaises(ActionRejected):r.invoke('receiver.exchange',stream='distillate',slot=0)
  r.invoke('acquisition.stop');r.step(20);before=total(r).copy();r.invoke('receiver.exchange',stream='distillate',slot=0);self.assertEqual(r.bottles[0],[2,1]);np.testing.assert_allclose(total(r),before)
 def test_cancel_partial_preserves(self):
  r=ready();r.invoke('acquisition.start');r.step(50);r.invoke('acquisition.stop');old=(r.feed.copy(),r.film.copy(),r.products.copy(),r.trap.copy(),r.exhaust.copy());r.step(10)
  for a,b in zip(old,[r.feed,r.film,r.products,r.trap,r.exhaust]):np.testing.assert_array_equal(a,b)
  self.assertEqual(r.phase,'cancelled');self.assertEqual(r.speed,0);r.invoke('acquisition.start');r.step(10);np.testing.assert_allclose(total(r),r.initial,atol=1e-13)
 def test_completion_timeout_poweroff(self):
  r=ready();r.invoke('acquisition.start');r.step(120);self.assertEqual(r.phase,'succeeded')
  r=ready();r.invoke('method.configure',**{**METHOD_EXAMPLE,'timeout_s':20.});r.invoke('acquisition.start');r.step(20);self.assertEqual((r.phase,r.fault),('failed','TIMEOUT'))
  r=ready();r.invoke('acquisition.start');r.step(40);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');np.testing.assert_allclose(total(r),r.initial)
 def test_double_instance(self):
  a=ready();b=ready();s=b.debug_snapshot();a.invoke('acquisition.start');a.step(50);self.assertEqual(s,b.debug_snapshot())
 def test_public_identity_and_truth_filter(self):
  r=ready();r.register_principal('judge','evaluator');agent=r.session();judge=r.session('judge');self.assertNotIn('truth.feed_mol',agent.read(instance_id=r.instance_id)['observations']);self.assertIn('truth.feed_mol',judge.read(instance_id=r.instance_id)['observations'])
  before=r.debug_snapshot();v=agent.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='p');self.assertEqual(v['error']['code'],'ACCESS_DENIED');self.assertEqual(before,r.debug_snapshot())
  with self.assertRaises(ActionRejected):agent.read(instance_id='wrong')
  with self.assertRaises(ActionRejected):r.session('evaluator')
 def test_native_async_retry_and_owner(self):
  r=ready();s=r.session();v=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');self.assertEqual(v['status'],'accepted');self.assertEqual(s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start'),v);r.step(40);op=v['operation_id'];self.assertEqual(s.poll(op)['status'],'running');self.assertEqual(s.cancel(op,'stop')['status'],'cancelled')
  before=r.debug_snapshot();v=s.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='start');self.assertEqual(v['error']['code'],'REQUEST_CONFLICT');self.assertEqual(before,r.debug_snapshot())
  r.register_principal('other','agent')
  with self.assertRaises(ActionRejected):r.session('other').poll(op)
 def test_public_write_target(self):
  r=ready();s=r.session();s.write(instance_id=r.instance_id,state_id='thermal.wall_target_k',value=330.,request_id='w');self.assertEqual(r.method['wall_temperature_k'],330.)
  before=r.debug_snapshot();v=s.write(instance_id=r.instance_id,state_id='feed.target_kg_s',value=1e-5,request_id='denied');self.assertEqual(v['error']['code'],'ACCESS_DENIED');self.assertEqual(before,r.debug_snapshot())
 def test_step_partition(self):
  a=ready();b=ready()
  for r in [a,b]:r.invoke('acquisition.start')
  a.step(50)
  for i in range(500):b.step(.1)
  np.testing.assert_allclose(a.products,b.products,atol=1e-12);np.testing.assert_allclose(a.film,b.film,atol=1e-12)
if __name__=='__main__':unittest.main()
