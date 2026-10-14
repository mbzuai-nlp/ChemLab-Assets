import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.scp_digiprepms import *
def ready(sample=True,method=None,plant=None):
 r=ScpDigiprepmsRuntime();r.invoke('plant.configure',**(plant or PLANT_EXAMPLE));r.invoke('method.configure',**(method or METHOD_EXAMPLE))
 if sample:r.invoke('sample.configure',**example_sample())
 r.invoke('power.set',enabled=True);return r
def cold_method():return dict(knots=[dict(time_s=0.,temperature_k=293.15)],hold_s=8.,timeout_s=30.)
def material(r):return [{k:v for k,v in t.items() if k!='temperature_k'} for t in r.invoke('product.read')['records']]
class Tests(unittest.TestCase):
 def test_setpoint_only(self):
  r=ready();before=r.block_t;r.invoke('thermal.set_temperature',temperature_k=363.15);self.assertEqual(r.block_t,before);self.assertEqual(r.time_s,0);self.assertEqual(r.target,363.15)
 def test_query_pure(self):
  r=ready();before=r.debug_snapshot();r.describe();r.read();r.invoke('reference.lookup',substance_id='gibbsite',temperature_k=368.15);r.invoke('history.read');r.session().read(instance_id=r.instance_id);self.assertEqual(before,r.debug_snapshot())
 def test_invalid_atomic(self):
  r=ready()
  for a in [dict(temperature_k=True),dict(temperature_k=float('nan')),dict(temperature_k=500.),dict(temperature_k=350.,extra=1)]:
   b=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.invoke('thermal.set_temperature',**a)
   self.assertEqual(b,r.debug_snapshot())
 def test_program_order(self):
  r=ready();m=copy.deepcopy(METHOD_EXAMPLE);m['knots']=[dict(time_s=1.,temperature_k=310.)];b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('method.configure',**m)
  self.assertEqual(b,r.debug_snapshot())
 def test_piecewise_program(self):
  m=dict(knots=[dict(time_s=0.,temperature_k=293.15),dict(time_s=20.,temperature_k=313.15),dict(time_s=40.,temperature_k=303.15)],hold_s=60.,timeout_s=300.);r=ready(method=m);r.invoke('acquisition.start');r.step(10);self.assertAlmostEqual(r.target,303.15);self.assertLess(r.block_t,r.target);r.step(20);self.assertAlmostEqual(r.target,308.15)
 def test_historical_heatup_not_tenfold(self):
  r=ready(sample=False,method=dict(knots=[dict(time_s=0.,temperature_k=368.15)],hold_s=2000.,timeout_s=9000.));r.invoke('acquisition.start');r.step(2100);self.assertGreater(r.block_t,367.);self.assertLess(r.block_t,368.3)
 def test_load_slows_heatup(self):
  empty=ready(sample=False);loaded=ready(sample=False);s=example_sample();s['tubes']=[dict(s['tubes'][0],position=i,volume_m3=50e-6) for i in range(48)];loaded.invoke('sample.configure',**s)
  for r in [empty,loaded]:r.invoke('acquisition.start');r.step(900)
  self.assertLess(loaded.block_t,empty.block_t);self.assertEqual(len(loaded.tubes),48)
 def test_capacity_and_duplicate_atomic(self):
  r=ready(sample=False)
  for s in [dict(example_sample(),tubes=[dict(example_sample()['tubes'][0],volume_m3=51e-6)]),dict(example_sample(),tubes=example_sample()['tubes']*2)]:
   b=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.invoke('sample.configure',**s)
   self.assertEqual(b,r.debug_snapshot())
 def test_unknown_reference_atomic(self):
  r=ready(sample=False);s=example_sample();s['tubes'].append(dict(s['tubes'][0],position=1,substance_id='made_up'));b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('sample.configure',**s)
  self.assertEqual(b,r.debug_snapshot())
 def test_reference_range_and_real_data(self):
  r=ScpDigiprepmsRuntime();self.assertGreaterEqual(len(r.templates),50)
  for t in r.templates.values():self.assertAlmostEqual(r._logk(t['substance_id'],298.15),t['values'][0]['log10_k']);self.assertIsNone(t['measured_recovery'])
  for temperature in [293.14,453.16]:
   with self.assertRaisesRegex(ActionRejected,'REFERENCE_RANGE'):r._logk('gibbsite',temperature)
 def test_energy_closure(self):
  r=ready();r.invoke('acquisition.start');r.step(240);self.assertAlmostEqual(r._energy(),r.input_j-r.loss_j,places=6);r.invoke('acquisition.stop');r.step(50);self.assertAlmostEqual(r._energy(),r.input_j-r.loss_j,places=6)
 def test_finite_three_ledgers(self):
  r=ready();r.invoke('acquisition.start');r.step(1200)
  for t in r.tubes:
   self.assertAlmostEqual(t['initial_solid_mol'],t['solid_mol']+t['dissolved_mol'],places=14);self.assertAlmostEqual(t['initial_acid_mol'],sum(t[k] for k in ['acid_mol','acid_consumed_mol','acid_exhaust_mol']),places=14);self.assertAlmostEqual(t['initial_blank_mol'],t['blank_remaining_mol']+t['blank_dissolved_mol'],places=18);self.assertGreater(t['dissolved_mol'],0);self.assertGreater(t['acid_exhaust_mol'],0);self.assertGreater(t['blank_dissolved_mol'],0)
 def test_acid_limits_dissolution(self):
  r=ready(sample=False,method=cold_method());s=example_sample();s['tubes'][0].update(acid_mol=1e-8,rate_298_s=1.,activation_j_mol=0.,acid_evaporation_298_s=0.);r.invoke('sample.configure',**s);r.invoke('acquisition.start');r.step(20);t=r.tubes[0];self.assertLessEqual(t['dissolved_mol'],1e-8/3+1e-15);self.assertGreater(t['solid_mol'],0)
 def test_program_completion_not_complete_digestion(self):
  r=ready(method=cold_method());r.invoke('acquisition.start');r.step(12);self.assertEqual(r.phase,'succeeded');self.assertGreater(r.tubes[0]['solid_mol'],0)
 def test_timeout_partial(self):
  r=ready(method=dict(METHOD_EXAMPLE,timeout_s=3.));r.invoke('acquisition.start');r.step(10);self.assertEqual(r.phase,'failed');self.assertEqual(r.fault,'TIMEOUT');self.assertEqual(r.elapsed_s,3.)
 def test_cancel_resume(self):
  r=ready(method=cold_method());a=r.session().invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');self.assertEqual(a['status'],'accepted');r.step(3);op=a['operation_id'];r.session().cancel(op,'stop');before=material(r);r.step(10);self.assertEqual(before,material(r));b=r.session().invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='resume');self.assertNotEqual(op,b['operation_id']);r.step(8);self.assertEqual(r.session().poll(b['operation_id'])['status'],'succeeded')
 def test_power_cancel(self):
  r=ready();r.invoke('acquisition.start');r.step(5);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');b=material(r);r.step(5);self.assertEqual(b,material(r));self.assertEqual(r.heater_w,0.)
 def test_probe_connection_and_feedback(self):
  r=ready();b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('probe.configure',mode='external',position=0,connected=False)
  self.assertEqual(b,r.debug_snapshot());r.invoke('probe.configure',mode='external',position=0,connected=True);r.invoke('acquisition.start');r.step(50);self.assertEqual(r._indicated(),r.tubes[0]['temperature_k']);self.assertLess(r._indicated(),r.block_t)
 def test_rack_goal_only_and_start_guard(self):
  r=ready();r.invoke('rack.set',side='left',lift_m=.1);self.assertEqual(r.lift[0],0.)
  with self.assertRaises(ActionRejected):r.invoke('acquisition.start')
  r.step(3);self.assertAlmostEqual(r.lift[0],.06);r.invoke('rack.set',side='left',lift_m=0.);r.step(3);self.assertAlmostEqual(r.lift[0],0.)
 def test_busy_and_hot_guards(self):
  r=ready();r.invoke('acquisition.start');b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('rack.set',side='left',lift_m=.1)
  self.assertEqual(b,r.debug_snapshot());r.step(1800);r.invoke('acquisition.stop');b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('rack.set',side='left',lift_m=.1)
  self.assertEqual(b,r.debug_snapshot())
 def test_tube_limit(self):
  r=ready();b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('thermal.set_temperature',temperature_k=453.15)
  self.assertEqual(b,r.debug_snapshot())
 def test_two_instances(self):
  a=ready();b=ready();before=b.debug_snapshot();a.invoke('acquisition.start');a.step(50);self.assertEqual(before,b.debug_snapshot())
 def test_privacy_and_request_retry(self):
  r=ready();s=r.session();self.assertNotIn('product.read',s.describe(instance_id=r.instance_id)['capabilities']);self.assertFalse(any(k.startswith('truth.') for k in r.read()));bad=s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='truth');self.assertEqual(bad['error']['code'],'ACCESS_DENIED')
  with self.assertRaises(ActionRejected):s.read(instance_id=r.instance_id,state_ids=['truth.solid_mol'])
  q=dict(instance_id=r.instance_id,capability_id='power.set',arguments=dict(enabled=True),request_id='same');a=s.invoke(**q);b=r.debug_snapshot();self.assertEqual(a,s.invoke(**q));self.assertEqual(b,r.debug_snapshot());q['arguments']['enabled']=False;self.assertEqual(s.invoke(**q)['error']['code'],'REQUEST_CONFLICT');self.assertEqual(b,r.debug_snapshot())
 def test_owner_poll_denied(self):
  r=ready();r.register_principal('other','agent');op=r.session().invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='s')['operation_id']
  with self.assertRaises(ActionRejected):r.session('other').poll(op)
 def test_zero_step_and_invalid_dt(self):
  r=ready();b=r.debug_snapshot();r.step(0);self.assertEqual(b,r.debug_snapshot())
  for x in [-1.,True,float('inf')]:
   with self.assertRaises(ActionRejected):r.step(x)
   self.assertEqual(b,r.debug_snapshot())
if __name__=='__main__':unittest.main()
