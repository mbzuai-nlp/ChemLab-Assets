import copy,sys,unittest,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.pilodist_pilodist104 import *
def prepared(instance='p',plant=None,column=None,method=None,sample=None):
 r=PilodistPilodist104Runtime(instance)
 for cap,arg in [('plant.configure',plant or PLANT_EXAMPLE),('column.configure',column or COLUMN_EXAMPLE),('method.configure',method or METHOD_EXAMPLE),('sample.configure',sample or example_sample()),('bath.set_engaged',{'engaged':True}),('power.set',{'enabled':True})]:r.invoke(cap,**copy.deepcopy(arg))
 r.step(5.1);return r
class PilodistTests(unittest.TestCase):
 def rejected_unchanged(self,r,cap,args,code=None):
  before=r.debug_snapshot()
  with self.assertRaises(ActionRejected) as exc:r.invoke(cap,**args)
  if code:self.assertEqual(str(exc.exception),code)
  self.assertEqual(before,r.debug_snapshot())
 def test_identity(self):self.assertEqual(CATALOG_ID,'CHEM-037');self.assertEqual(len(CAPS),15)
 def test_pure_read_and_describe(self):
  r=prepared();b=r.debug_snapshot();r.read();r.describe();r.session().read(instance_id='p');self.assertEqual(b,r.debug_snapshot())
 def test_only_step_changes_temperature(self):
  r=prepared();t=r.bath_t;r.invoke('temperature.set',temperature_k=400.);self.assertEqual(t,r.bath_t);r.invoke('acquisition.start');r.step(20.);self.assertGreater(r.bath_t,t)
 def test_invalid_arguments_atomic(self):
  r=prepared()
  for cap,args in [('temperature.set',{'temperature_k':float('nan')}),('fraction.select',{'index':True}),('fraction.select',{'index':1.5}),('reflux.set',{'ratio':-1,'total_reflux':False}),('temperature.set',{'temperature_k':524.})]:self.rejected_unchanged(r,cap,args)
 def test_precondition_atomic(self):
  r=PilodistPilodist104Runtime();self.rejected_unchanged(r,'acquisition.start',{});self.rejected_unchanged(r,'plant.configure',dict(PLANT_EXAMPLE,external_cooling_confirmed=False))
 def test_charge_limits_and_duplicate(self):
  for factor in [.001,10]:
   r=prepared();r.sample=None;s=example_sample()
   for x in s['species']:x['amount_mol']*=factor
   self.rejected_unchanged(r,'sample.configure',s,'CHARGE_OR_HOLDUP')
 def test_source_ranges_and_ammonia(self):
  r=prepared();self.assertEqual(len(r.templates),81);self.assertEqual(sum(t['device_pure_boiling_eligible'] for t in r.templates.values()),80)
  with self.assertRaises(ActionRejected):r._psat('hexane','0',400.)
  r.sample=None;s=example_sample();s['species']=[dict(substance_id='ammonia',fit_id='0',amount_mol=10.,reference_molar_volume_m3_mol=.00002)]
  self.rejected_unchanged(r,'sample.configure',s,'REFERENCE_RANGE')
 def test_species_mass_volume_conservation(self):
  r=prepared();r.invoke('acquisition.start');r.step(100.)
  np.testing.assert_allclose(r.inventory.sum(axis=0)+r.fractions.sum(axis=0),r.initial,atol=2e-13,rtol=0)
  self.assertGreater(r.fractions.sum(),0);self.assertTrue(np.all(r.inventory>=0));self.assertTrue(np.all(r.fractions>=0));self.assertAlmostEqual(float((r.inventory.sum(axis=0)+r.fractions.sum(axis=0))@r.vm),float(r.initial@r.vm),13)
  mm=np.array([r.templates[s['substance_id']]['molar_mass_kg_mol'] for s in r.sample['species']]);self.assertAlmostEqual(float((r.inventory.sum(axis=0)+r.fractions.sum(axis=0))@mm),float(r.initial@mm),12)
 def test_fixed_stage_holdups(self):
  r=prepared();before=r.inventory[1:].sum(axis=1);r.invoke('acquisition.start');r.step(50.);np.testing.assert_allclose(r.inventory[1:].sum(axis=1),before,atol=1e-13,rtol=0)
 def test_equilibrium_independent_residual(self):
  r=prepared();t,x,y=r._equilibrium();p=30000.
  for j in range(len(x)):
   ps=np.array([r._psat(s['substance_id'],s['fit_id'],float(t[j])) for s in r.sample['species']]);self.assertAlmostEqual(float(x[j]@ps)/p,1.,9);self.assertAlmostEqual(float(y[j].sum()),1.,12)
 def test_reflux_changes_not_instant_composition(self):
  r=prepared();r.invoke('acquisition.start');r.step(25.);before=r.inventory.copy();f=r.reflux;r.invoke('reflux.set',ratio=20.,total_reflux=False);np.testing.assert_array_equal(r.inventory,before);self.assertEqual(r.reflux,f);r.step(.1);self.assertGreater(r.reflux,f);self.assertLess(r.reflux,20/21)
 def test_more_reflux_less_distillate(self):
  a=prepared(method=dict(METHOD_EXAMPLE,reflux_ratio=0.));b=prepared(method=dict(METHOD_EXAMPLE,reflux_ratio=9.))
  for r in [a,b]:r.invoke('acquisition.start');r.step(60.)
  self.assertGreater(a.fractions.sum(),b.fractions.sum()*2)
 def test_prewet_not_created(self):
  r=prepared();np.testing.assert_allclose(r.inventory.sum(axis=0),r.initial,rtol=0,atol=1e-14);self.assertAlmostEqual(r.inventory[1:-1].sum(),.06,14)
 def test_two_columns_require_explicit_coefficients(self):
  r=prepared(column=dict(COLUMN_EXAMPLE,column_type='wire_gauze',effective_stages=7,stage_holdup_mol=.03));self.assertEqual(r.inventory.shape,(9,2));self.assertAlmostEqual(r.inventory[1:-1].sum(),.21,12)
  self.rejected_unchanged(r,'column.configure',COLUMN_EXAMPLE,'PRECONDITION_FAILED')
 def test_finite_six_cuts_and_movement(self):
  r=prepared(method=dict(METHOD_EXAMPLE,cut_volume_m3=1e-6,duration_s=200.));r.invoke('acquisition.start');r.step(120.);self.assertEqual(r.phase,'succeeded');self.assertTrue(np.all(r.fractions@r.vm<=1e-6+1e-12));self.assertEqual(r.receiver,5);np.testing.assert_allclose(r.inventory.sum(axis=0)+r.fractions.sum(axis=0),r.initial,rtol=0,atol=1e-12)
 def test_selector_travel_total_reflux(self):
  r=prepared();r.invoke('acquisition.start');r.step(30.);f=r.fractions.copy();r.invoke('fraction.select',index=5);self.assertEqual(r.angle,0.);r.step(.1);np.testing.assert_array_equal(r.fractions,f);self.assertGreater(r.angle,0.);self.assertLess(r.angle,5*math.pi/3)
 def test_cancel_residual_and_resume(self):
  r=prepared();r.invoke('acquisition.start');r.step(30.);r.invoke('acquisition.stop');n=r.inventory.copy();f=r.fractions.copy();r.step(2.);np.testing.assert_array_equal(n,r.inventory);np.testing.assert_array_equal(f,r.fractions);r.invoke('acquisition.start');r.step(20.);self.assertGreater(r.fractions.sum(),f.sum())
 def test_completion_timeout(self):
  for method,phase in [(dict(METHOD_EXAMPLE,duration_s=1.),'succeeded'),(dict(METHOD_EXAMPLE,timeout_s=1.),'failed')]:
   r=prepared(method=method);r.invoke('acquisition.start');r.step(2.);self.assertEqual(r.phase,phase)
 def test_bath_guard_and_ramp(self):
  r=PilodistPilodist104Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('bath.set_engaged',engaged=True);self.assertEqual(r.lift,0);r.step(1.);self.assertAlmostEqual(r.lift,.03,12)
  r=prepared();r.invoke('acquisition.start');self.rejected_unchanged(r,'bath.set_engaged',{'engaged':False},'BUSY')
 def test_session_isolation_and_dedup(self):
  a=prepared('a');b=prepared('b');session=a.session();before=b.debug_snapshot();q=dict(instance_id='a',capability_id='acquisition.start',arguments={},request_id='one');first=session.invoke(**q);self.assertEqual(first,session.invoke(**q));a.step(20.);self.assertEqual(before,b.debug_snapshot());self.assertEqual(session.invoke(**dict(q,arguments={'bad':1}))['status'],'rejected')
  self.assertEqual(session.invoke(instance_id='a',capability_id='product.read',arguments={},request_id='truth')['error']['code'],'ACCESS_DENIED');self.assertNotIn('truth.boiler_mol',session.read(instance_id='a')['observations']);self.assertNotIn('sample.configure',session.describe(instance_id='a')['capabilities'])
 def test_poll_cancel_ownership(self):
  r=prepared();r.register_principal('other');s=r.session();q=s.invoke(instance_id='p',capability_id='acquisition.start',arguments={},request_id='start');op=q['operation_id'];self.assertEqual(q['status'],'accepted');r.step(20.);self.assertEqual(s.poll(op)['status'],'running')
  with self.assertRaises(ActionRejected):r.session('other').poll(op)
  self.assertEqual(s.cancel(op,'stop')['status'],'cancelled');self.assertGreater(r.fractions.sum(),0)
 def test_step_partition(self):
  a=prepared();b=prepared()
  for r in [a,b]:r.invoke('acquisition.start')
  a.step(10.)
  for _ in range(100):b.step(.1)
  np.testing.assert_allclose(a.inventory,b.inventory,rtol=0,atol=1e-12)
if __name__=='__main__':unittest.main()
