import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.retsch_as200 import *
def prepared(i='as200_01',**sample_changes):
 r=RetschAs200Runtime(i);r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('method.configure',**METHOD_EXAMPLE);s=example_sample();s.update(sample_changes);r.invoke('sample.configure',**s);r.invoke('cover.set',lift_m=0.);r.step(1.5);r.invoke('mount.configure',clamped=True);r.invoke('power.set',enabled=True);return r
class TestAS200(unittest.TestCase):
 def reject(self,r,cap,**a):
  old=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke(cap,**a)
  self.assertEqual(old,r.debug_snapshot())
 def test_01_pure_reads(self):
  r=prepared();s=r.debug_snapshot();r.read();r.describe();r.invoke('sieving.measure');r.invoke('reference.lookup',substance_id='quartz');self.assertEqual(s,r.debug_snapshot())
 def test_02_targets_not_teleports(self):
  r=prepared();r.invoke('sieving.set_lift',total_lift_m=.003);self.assertEqual(r.total_lift,0);r.invoke('acquisition.start');self.assertEqual(r.total_lift,0);r.step(.1);self.assertAlmostEqual(r.total_lift,.0005)
 def test_03_lift_peak_to_peak(self):
  r=prepared();r.invoke('acquisition.start');r.step(.5);self.assertAlmostEqual(r.z,r.total_lift/2*math.sin(r.wave_phase));self.assertLessEqual(abs(r.z),r.total_lift/2)
 def test_04_si_acceleration(self):
  r=prepared();r.invoke('sieving.set_acceleration',acceleration_m_s2=50.);self.assertAlmostEqual(r._target(),100/(math.tau*47.5)**2);r.invoke('acquisition.start');r.step(1);self.assertAlmostEqual(r.read()['sieving.indicated_acceleration_m_s2'],50.)
 def test_05_acceleration_envelope_rejected(self):
  r=prepared();self.reject(r,'sieving.set_acceleration',acceleration_m_s2=15.1*9.81)
 def test_06_mass_conservation(self):
  r=prepared();r.invoke('acquisition.start');r.step(60.5);self.assertEqual(r.phase,'succeeded');self.assertAlmostEqual(sum(map(sum,r.ledger)),.03,13);self.assertTrue(all(x>=0 for row in r.ledger for x in row));self.assertGreater(sum(r.ledger[6]),0.)
 def test_07_oversize_never_passes(self):
  r=prepared();r.invoke('acquisition.start');r.step(60.5);self.assertAlmostEqual(r.ledger[0][-1],.003);self.assertTrue(all(row[-1]==0 for row in r.ledger[1:]))
 def test_08_fixed_interval_counts_total(self):
  r=prepared();r.invoke('acquisition.start');r.step(4.);self.assertAlmostEqual(r.elapsed_s,4);self.assertAlmostEqual(r.driven_s,3);r.step(56.5);self.assertAlmostEqual(r.elapsed_s,60);self.assertAlmostEqual(r.driven_s,45)
 def test_09_pause_freezes_mass_timer(self):
  r=prepared();r.invoke('acquisition.start');r.step(2.);r.invoke('acquisition.pause');old=copy.deepcopy(r.ledger);r.step(10);self.assertEqual(r.ledger,old);self.assertAlmostEqual(r.elapsed_s,2);self.assertEqual(r.total_lift,0);r.invoke('acquisition.resume');r.step(1.);self.assertAlmostEqual(r.elapsed_s,3)
 def test_10_cancel_preserves_partial(self):
  r=prepared();r.invoke('acquisition.start');r.step(8);r.invoke('acquisition.stop');old=copy.deepcopy(r.ledger);r.step(2);self.assertEqual(old,r.ledger);self.assertEqual(r.phase,'cancelled');self.assertEqual(r.total_lift,0);self.reject(r,'acquisition.start')
 def test_11_reject_open_clamped(self):
  r=prepared();self.reject(r,'cover.set',lift_m=.14);r.invoke('acquisition.start');self.reject(r,'mount.configure',clamped=False)
 def test_12_cover_target_then_step(self):
  r=prepared();r.invoke('mount.configure',clamped=False);r.invoke('cover.set',lift_m=.14);self.assertEqual(r.cover,0);r.step(.5);self.assertAlmostEqual(r.cover,.05)
 def test_13_grade_order(self):
  r=RetschAs200Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE);s=example_sample();s['sieve_apertures_m'][1]=.003;self.reject(r,'sample.configure',**s)
 def test_14_load_bounds(self):
  r=RetschAs200Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE)
  for k,v in [('mass_kg',3.01),('stack_mass_kg',6.1),('maximum_sieve_load_m3',1e-9)]:
   s=example_sample();s[k]=v;self.reject(r,'sample.configure',**s)
 def test_15_fraction_sum_and_nonfinite(self):
  r=RetschAs200Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE);s=example_sample();s['bins'][0]['mass_fraction']=.7;self.reject(r,'sample.configure',**s);self.reject(r,'method.configure',**{**METHOD_EXAMPLE,'duration_s':float('nan')})
 def test_16_agent_truth_boundary(self):
  r=prepared();s=r.session();self.assertFalse(any(k.startswith('truth.') for k in r.read()));self.assertNotIn('product.read',r.describe()['capabilities']);reply=s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='bad');self.assertEqual(reply['error']['code'],'ACCESS_DENIED')
  with self.assertRaises(ActionRejected):s.read(instance_id=r.instance_id,state_ids=['truth.fraction_mass_kg'])
 def test_17_instances(self):
  a=prepared('a');b=prepared('b');old=b.debug_snapshot();a.invoke('acquisition.start');a.step(1);self.assertEqual(old,b.debug_snapshot())
 def test_18_library_count_density(self):
  r=prepared();self.assertGreaterEqual(len(r.templates),50);q=r.invoke('reference.lookup',substance_id='quartz');self.assertAlmostEqual(r.skeletal_volume,.03/q['density_kg_m3']);self.assertGreater(q['density_kg_m3'],2600);self.assertLess(q['density_kg_m3'],2700)
 def test_19_nist_true_values_no_tails(self):
  r=prepared();v=r.invoke('reference.cdf',reference_id='nist_srm_1017b',diameter_m=.0002);self.assertAlmostEqual(v['cumulative_mass_fraction'],.556);self.reject(r,'reference.cdf',reference_id='nist_srm_1017b',diameter_m=.00001)
 def test_20_external_balance_not_truth(self):
  r=prepared();self.assertIsNone(r.invoke('weighing.read')['recovery_fraction']);a=CAPS['weighing.record'][6];r.invoke('weighing.record',**a);self.assertEqual(r.invoke('weighing.read')['fraction_mass_kg'],[.001]*7);self.assertNotEqual([.001]*7,[sum(x) for x in r.ledger]);r.invoke('acquisition.start');r.step(.1);self.assertFalse(r.invoke('weighing.read')['available'])
 def test_21_collect_conserves(self):
  r=prepared();r.invoke('acquisition.start');r.step(61);r.invoke('mount.configure',clamped=False);r.invoke('cover.set',lift_m=.14);r.step(1.5);o=r.invoke('product.collect',fraction_index=6,fraction=.5,recipient_id='receiver');self.assertGreater(o['transferred_kg'],0);self.assertAlmostEqual(sum(map(sum,r.ledger))+r.collected,r.mass,13)
 def test_22_zero_rate_and_humidity(self):
  for args in [dict(passage_rate_s=0),dict(relative_humidity=1.,humidity_exponent=2.)]:
   r=prepared(**args);old=copy.deepcopy(r.ledger);r.invoke('acquisition.start');r.step(61);self.assertEqual(r.ledger,old)
 def test_23_native_dedup_owner(self):
  r=prepared();s=r.session();a=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');x=s.invoke(**a);self.assertEqual(x,s.invoke(**a));r.step(2);before=r.debug_snapshot();s.poll(x['operation_id']);self.assertEqual(before,r.debug_snapshot());r.register_principal('other','agent')
  with self.assertRaises(ActionRejected):r.session('other').poll(x['operation_id'])
  s.cancel(x['operation_id'],'cancel');self.assertEqual(r.phase,'cancelled')
 def test_24_power_off(self):
  r=prepared();r.invoke('acquisition.start');r.step(2);r.invoke('power.set',enabled=False);old=copy.deepcopy(r.ledger);r.step(2);self.assertEqual(old,r.ledger);self.assertEqual(r.phase,'cancelled');self.assertEqual(r.total_lift,0)
 def test_25_fault_injection_1_2_0(self):
  r=prepared();r.invoke('acquisition.start');r.step(2);o=r.invoke('fault.inject',code='E26');self.assertEqual((o['phase'],r.read()['fault.code']),('cancelled','E26'));old=copy.deepcopy(r.ledger);r.step(2);self.assertEqual(old,r.ledger);self.assertEqual(r.total_lift,0)
  for cap,a in (('acquisition.start',{}),('cycle.rearm',{}),('sieving.adjust_lift',dict(direction=1)),('signal.set',dict(enabled=False))):self.reject(r,cap,**a)
  r.invoke('fault.clear');r.invoke('power.set',enabled=False);r.step(29);r.invoke('power.set',enabled=True);self.assertEqual(r.read()['fault.code'],'E26')
  r.invoke('power.set',enabled=False);r.step(30);r.invoke('power.set',enabled=True);self.assertIsNone(r.read()['fault.code']);r.invoke('cycle.rearm');r.invoke('acquisition.start');self.assertEqual(r.phase,'running')
 def test_26_run_owner_guards_1_2_0(self):
  r=prepared();r.register_principal('a','agent');r.register_principal('b','agent');a,b=r.session('a'),r.session('b');x=a.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='s');r.step(1);before=r.debug_snapshot()
  for cap,args in (('acquisition.stop',{}),('acquisition.pause',{}),('sieving.adjust_lift',dict(direction=1))):self.assertEqual(b.invoke(instance_id=r.instance_id,capability_id=cap,arguments=args,request_id='b'+cap)['error']['code'],'ACCESS_DENIED')
  after=r.debug_snapshot();before.pop('_requests');after.pop('_requests');self.assertEqual(before,after);self.assertEqual(a.cancel(x['operation_id'],'c')['status'],'cancelled')
  r.step(5);r.invoke('cycle.rearm');x=a.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='s2');r.step(1)   # the main switch is a hard
  self.assertEqual(b.invoke(instance_id=r.instance_id,capability_id='power.set',arguments=dict(enabled=False),request_id='boff')['status'],'succeeded')   # switch (1.2.1)
  self.assertEqual((r.powered,r.phase),(False,'cancelled'))
if __name__=='__main__':unittest.main()
