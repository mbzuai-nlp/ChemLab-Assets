import unittest,copy,math,json,sys
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'outputs/expansion_batch03/t4_measure/biolin_sigma700'));import example_setup as ex
rt=ex.rt
class Tests(unittest.TestCase):
 def h(self,**kw):return ex.build(**kw)
 def test_01_source(self):self.assertEqual(sum(v['kind']=='pure' for v in rt.LIB['substances'].values()),413);self.assertAlmostEqual(rt.tension('cas_64_17_5',293.15,0),.022386)
 def test_02_pure_read(self):
  h=self.h();s=h.session();before=copy.deepcopy(h.__dict__);s.describe(instance_id=h.instance_id);s.read(instance_id=h.instance_id);h.invoke('acquisition.result');self.assertEqual(before,h.__dict__)
 def test_03_target_not_advance(self):
  h=self.h();t=h.time_s;h.invoke('stage.move',position_m=.06);self.assertEqual(h.q,0.);self.assertEqual(h.time_s,t);h.step(.1);self.assertAlmostEqual(h.q,.0008)
 def test_04_plate(self):
  h=self.h();ex.call(h,'acquisition.start');h.step(30);r=ex.call(h,'acquisition.result');self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['tension_n_m'],.022454,delta=3e-6);self.assertGreater(len(r['rows']),200);self.assertEqual(h.q,0.)
 def test_05_force_buoyancy(self):
  h=self.h();h.invoke('stage.move',position_m=.101);h.step(14);d=h._depth();expected=.002*rt.G+2*(.0195+.00012)*.022454-791.01243*rt.G*.0195*.00012*d;self.assertAlmostEqual(h.trueforce,expected,places=10)
 def test_06_ring(self):
  h=self.h(probe='ring',method={'speed_m_s':.001,'sample_interval_s':.02});h.invoke('acquisition.start');h.step(210);self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.peak,.022454,delta=.00010)
 def test_07_ring_acceptance(self):
  h=self.h(probe='ring');h.method['accept_ring_proxy']=False
  with self.assertRaisesRegex(rt.ActionRejected,'RING_MODEL'):h.invoke('acquisition.start')
 def test_08_two_instances(self):
  a=self.h();b=self.h(sid='cas_64_17_5');a.invoke('acquisition.start');a.step(30);self.assertEqual(b.phase,'idle');self.assertNotEqual(a.gamma,b.gamma)
 def test_09_atomic(self):
  h=self.h();before=copy.deepcopy(h.__dict__)
  with self.assertRaises(rt.ActionRejected):h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,speed_m_s=float('nan')))
  self.assertEqual(before,h.__dict__)
 def test_10_busy(self):
  h=self.h();h.invoke('acquisition.start');before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'BUSY'):h.invoke('door.set',angle_rad=2.35)
  self.assertEqual(before,h.__dict__)
 def test_11_cancel(self):
  h=self.h();ex.call(h,'acquisition.start');h.step(4);q=h.q;ex.call(h,'acquisition.stop');n=len(h.rows);h.step(2);self.assertEqual(h.q,q);self.assertEqual(len(h.rows),n);self.assertEqual(h.phase,'cancelled')
 def test_12_heat(self):
  h=self.h();h.invoke('thermal.set_temperature',temperature_k=313.15);before=h.temperature;h.step(100);self.assertGreater(h.temperature,before);self.assertLess(h.temperature,313.15);self.assertAlmostEqual(h._ledger()['heat_error_j'],0.,delta=1e-7)
 def test_13_dose(self):
  h=self.h(sid='apg_fig1_2',plant={'ambient_k':298.15},temp=298.15);ex.call(h,'stock.load',volume_m3=2e-5,concentration_kg_m3=1.);ex.call(h,'dose.start',volume_m3=1e-6,flow_m3_s=1e-7);h.step(3);self.assertAlmostEqual(h.volume,30.3e-6);ex.call(h,'acquisition.stop');v=h.volume;h.step(1);self.assertEqual(h.volume,v);l=ex.call(h,'ledger.read');self.assertAlmostEqual(l['mass_error_kg'],0.,delta=1e-12);self.assertAlmostEqual(l['solute_error_kg'],0.,delta=1e-12);self.assertAlmostEqual(l['heat_error_j'],0.,delta=1e-7)
 def test_14_finite_stock(self):
  h=self.h();h.invoke('stock.load',volume_m3=1e-6,concentration_kg_m3=0.);h.invoke('dose.start',volume_m3=1e-6,flow_m3_s=1e-7);h.step(11);self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.stock,0.,delta=1e-12)
  with self.assertRaisesRegex(rt.ActionRejected,'FINITE_STOCK'):h.invoke('dose.start',volume_m3=1e-6,flow_m3_s=1e-7)
 def test_15_truth(self):
  h=self.h();s=h.session();self.assertNotIn('truth.solute_kg',s.describe(instance_id=h.instance_id)['state_slots']);self.assertNotIn('truth.solute_kg',s.read(instance_id=h.instance_id)['observations']);self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='x')['error']['code'],'ACCESS_DENIED')
 def test_16_principals(self):
  h=self.h()
  with self.assertRaises(rt.ActionRejected):h.session('intruder')
  with self.assertRaises(rt.ActionRejected):h.register_principal('agent','evaluator')
 def test_17_dedupe(self):
  h=self.h();s=h.session();a=dict(instance_id=h.instance_id,capability_id='stage.move',arguments={'position_m':.05},request_id='x');r=s.invoke(**a);self.assertEqual(r,s.invoke(**a));a['arguments']={'position_m':.1};self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT')
 def test_18_ownership_query(self):
  h=self.h();h.register_principal('b');a=h.session();b=h.session('b');r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r2=b.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='read');self.assertIsNone(r2['operation_id'])
  with self.assertRaises(rt.ActionRejected):b.cancel(r['operation_id'],'cancel')
  h.step(.5);self.assertEqual(a.poll(r['operation_id'])['status'],'running');self.assertEqual(a.cancel(r['operation_id'],'cancel')['status'],'succeeded')
 def test_19_calibration_independent(self):
  h=self.h(plant={'force_gain':1.02,'force_offset_n':.00002});ex.call(h,'balance.calibrate',reference_forces_n=[0.,.1],observed_raw_forces_n=[.00002,.10202]);h.step(2);h.invoke('balance.tare');h.invoke('acquisition.start');h.step(30);self.assertAlmostEqual(h.peak,.022454,delta=3e-6)
 def test_20_wrong_calibration(self):
  h=self.h(plant={'force_gain':1.1});h.invoke('acquisition.start');h.step(30);self.assertGreater(abs(h.peak-.022454),.001)
 def test_21_cmc_real(self):
  r=rt.LIB['substances']['apg_fig1_2']['values'];h=self.h();out=ex.call(h,'analysis.cmc',concentrations_kg_m3=r['concentration_kg_m3'][1:],tensions_n_m=r['tension_n_m'][1:]);self.assertTrue(.025<out['cmc_kg_m3']<.09)
 def test_22_no_false_cmc(self):
  cs=[.001,.002,.004,.008,.016,.032,.064,.128]
  with self.assertRaisesRegex(rt.ActionRejected,'NO_BREAKPOINT'):rt.fit_cmc(cs,[.05-.002*math.log(x) for x in cs])
 def test_23_no_extrapolation(self):
  with self.assertRaisesRegex(rt.ActionRejected,'TEMPERATURE_RANGE'):rt.tension('cas_64_17_5',400,0)
  with self.assertRaisesRegex(rt.ActionRejected,'CONCENTRATION_RANGE'):rt.tension('apg_fig1_2',298.15,2.)
  with self.assertRaisesRegex(rt.ActionRejected,'UNKNOWN_CONDITIONS'):rt.tension('sds_silica_hexane',293.15,.01)
 def test_24_dt_partition(self):
  a=self.h();b=self.h();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(4)
  for _ in range(40):b.step(.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows)
 def test_25_removal_mass(self):
  h=self.h();h.invoke('thermal.set_temperature',temperature_k=310);h.step(50);h.invoke('door.set',angle_rad=2.35);ex.call(h,'sample.remove');self.assertAlmostEqual(h._ledger()['mass_error_kg'],0.,delta=1e-12);self.assertAlmostEqual(h._ledger()['heat_error_j'],0.,delta=1e-7)
 def test_26_bool_unknown(self):
  h=self.h()
  for args in [{'angle_rad':True},{'angle_rad':0.,'hidden':1}]:
   with self.assertRaises(rt.ActionRejected):h.invoke('door.set',**args)
 def test_27_all_unsupported(self):
  h=self.h()
  for k in rt.UNSUPPORTED:
   with self.assertRaisesRegex(rt.ActionRejected,'UNSUPPORTED'):h.invoke(k)
 def test_28_result_provenance(self):
  h=self.h();h.invoke('acquisition.start');h.step(30);source=h._result()['template_source'];h.invoke('door.set',angle_rad=2.35);h.invoke('sample.remove');h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,substance_id='cas_67_56_1'));self.assertEqual(source,h._result()['template_source'])
 def test_29_overimmersion(self):
  h=self.h();h.invoke('door.set',angle_rad=2.35);h.invoke('sample.remove');h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,volume_m3=5e-5));h.invoke('door.set',angle_rad=0.);before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'OVERIMMERSION'):h.invoke('stage.move',position_m=rt.STAGE_MAX_M)
  self.assertEqual(before,h.__dict__)
 def test_30_direct_stop_owner(self):
  h=self.h();h.register_principal('b');a=h.session();b=h.session('b');a.invoke(instance_id=h.instance_id,capability_id='stage.move',arguments={'position_m':.05},request_id='start');r=b.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id='stop');self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(h.phase,'running')
 def test_31_dose_shortage_rejected_atomically(self):
  h=self.h();h.invoke('stock.load',volume_m3=1e-6,concentration_kg_m3=0.);before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'FINITE_STOCK_OR_CAPACITY'):h.invoke('dose.start',volume_m3=1.0000005e-6,flow_m3_s=1e-7)
  self.assertEqual(before,h.__dict__);self.assertAlmostEqual(h._ledger()['mass_error_kg'],0.,delta=1e-12)
 def test_32_stock_exhaustion_retains_partial_transfer(self):
  h=self.h(sid='apg_fig1_2',plant={'ambient_k':298.15},temp=298.15);h.invoke('stock.load',volume_m3=1e-6,concentration_kg_m3=1.);s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='dose.start',arguments={'volume_m3':1e-6,'flow_m3_s':1e-7},request_id='dose')['operation_id']
  # Host fault injection: account for lost stock in waste, preserving all ledgers.
  lost=5e-7;h.stock-=lost;h.waste+=lost*h.plant['liquid_density_kg_m3'];h.solute_waste+=lost*h.stock_c;h.step(20.);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'STOCK_EXHAUSTED');self.assertAlmostEqual(h.volume,30.5e-6,delta=1e-14);self.assertEqual(h.stock,0.)
  frozen=s.poll(op);volume=h.volume;h.step(20.);self.assertEqual(h.volume,volume);self.assertEqual(frozen,s.poll(op))
  for key in ['mass_error_kg','solute_error_kg','heat_error_j']:self.assertAlmostEqual(h._ledger()[key],0.,delta=1e-7 if key=='heat_error_j' else 1e-12)
 def test_33_stage_scene_envelope_and_reachability(self):
  h=self.h();before=copy.deepcopy(h.__dict__)
  for position in [.110,.125]:
   with self.assertRaisesRegex(rt.ActionRejected,'INVALID_ARGUMENT'):h.invoke('stage.move',position_m=position)
   self.assertEqual(before,h.__dict__)
  h.invoke('stage.move',position_m=rt.STAGE_MAX_M);h.step(14.);self.assertEqual(h.q,rt.STAGE_MAX_M);self.assertEqual(h.phase,'succeeded');h.invoke('stage.move',position_m=0.);h.step(14.)
  h.invoke('door.set',angle_rad=2.35);h.invoke('sample.remove');h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,volume_m3=1e-6));h.invoke('door.set',angle_rad=0.);before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'STAGE_RANGE'):h.invoke('acquisition.start')
  self.assertEqual(before,h.__dict__)
  metadata=rt.export_biolin_sigma700_contract_metadata();self.assertEqual(metadata['capabilities']['stage.move']['input_schema']['properties']['position_m']['maximum'],.109)
  for slot in ['stage.position_m','stage.target_m']:self.assertEqual(metadata['state_slots'][slot]['value_schema']['maximum'],.109)
 def test_34_metadata_covers_actual_state_changes(self):
  metadata=rt.export_biolin_sigma700_contract_metadata();original=rt.BiolinSigma700Runtime.invoke;observed=set()
  def audited_invoke(h,cap,**arguments):
   before=h.read();result=original(h,cap,**arguments);after=h.read();changed={state for state in before if before[state]!=after[state]};declared={effect['state_id'] for effect in metadata['capabilities'][cap]['state_effects'] if effect['timing']=='immediate'}
   self.assertFalse(changed-declared,(cap,changed-declared));observed.add(cap);return result
  with mock.patch.object(rt.BiolinSigma700Runtime,'invoke',audited_invoke):
   h=self.h();h.invoke('balance.calibrate',reference_forces_n=[0.,.1],observed_raw_forces_n=[0.,.1]);h.invoke('balance.tare');h.invoke('stage.move',position_m=.005);h.invoke('sim.step',dt_s=1.);h.invoke('stage.move',position_m=0.);h.invoke('sim.step',dt_s=1.);h.invoke('stirrer.set_speed',speed_rad_s=20.);h.invoke('thermal.set_temperature',temperature_k=295.15);h.invoke('acquisition.start');h.invoke('sim.step',dt_s=30.);h.invoke('door.set',angle_rad=2.35);h.invoke('probe.select',probe='ring');h.invoke('sample.remove');h.invoke('power.set',enabled=False);h.invoke('service.set',distance_m=.1)
   d=self.h(sid='apg_fig1_2',plant={'ambient_k':298.15},temp=298.15);d.invoke('stock.load',volume_m3=2e-5,concentration_kg_m3=1.);d.invoke('dose.start',volume_m3=1e-6,flow_m3_s=1e-7);d.invoke('sim.step',dt_s=3.);d.invoke('acquisition.stop');d.invoke('door.set',angle_rad=2.35);d.invoke('sample.remove')
  self.assertTrue(set(rt.IMMEDIATE_EFFECTS)<=observed)
if __name__=='__main__':unittest.main()
