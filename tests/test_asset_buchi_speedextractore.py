import copy,hashlib,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.buchi_speedextractore import *
def setup(n=6,method=None,plant=None,sample=None):
 h=BuchiSpeedextractoreRuntime();h.invoke('plant.configure',**dict(EXAMPLE,**(plant or {})));h.invoke('method.configure',**dict(METHOD_EXAMPLE,**(method or {})));h.invoke('power.set',enabled=True);h.invoke('thermal.preheat');h.step(100.);h.invoke('shield.set_lift',lift_m=.18);h.invoke('drawer.set_extension',extension_m=.16)
 for i in range(n):h.invoke('sample.configure',**dict(SAMPLE_EXAMPLE,**dict(position=i,sample_id='soil_'+str(i),**(sample or {}))))
 h.invoke('drawer.set_extension',extension_m=0.);h.invoke('shield.set_lift',lift_m=0.);return h
def finish(h,dt=.25):
 if h.phase=='idle':h.invoke('acquisition.start')
 for _ in range(math.ceil(1200/dt)):
  h.step(dt)
  if h.phase!='running':break
 return h
def advance(h,stage):
 if h.phase=='idle':h.invoke('acquisition.start')
 for _ in range(4000):
  h.step(.25)
  if h.stage==stage:return
 raise AssertionError((stage,h.phase,h.stage,h.fault))
class TestE916(unittest.TestCase):
 def ledger(self,h):
  q=h._inventory()
  for initial,terms in [('initial_target_kg',['sorbed_target_kg','dissolved_target_kg','extract_target_kg','degraded_target_kg','removed_target_kg']),('initial_solvent_m3',['reservoir_solvent_m3','cell_solvent_m3','extract_solvent_m3','waste_solvent_m3','removed_solvent_m3']),('initial_nitrogen_mol',['remaining_nitrogen_mol','exhaust_nitrogen_mol'])]:self.assertAlmostEqual(q[initial],sum(q[k] for k in terms),places=12)
  for c in h.cells.values():self.assertAlmostEqual(c['spec']['target_mass_kg'],c['sorbed']+c['dissolved']+c['extract']+c['degraded'],places=15)
 def test_01_contract_and_no_clock_queries(self):
  h=setup();m=export_buchi_speedextractore_contract_metadata();self.assertEqual(set(m['capabilities']),set(CAPS));t=h.time_s
  for c in ['extraction.measure','product.read']:validate(CAPS[c][1],h.invoke(c))
  for k,v in h.read('evaluator').items():validate(SLOTS[k][0],v)
  self.assertEqual(t,h.time_s);self.assertNotIn('truth.initial_target_kg',h.read())
 def test_02_ranges_and_atomic_rejections(self):
  h=BuchiSpeedextractoreRuntime()
  for updates in [dict(cycles=True),dict(cycles=1.5),dict(cycles=11),dict(pressure_gauge_pa=16e6),dict(temperature_k=474.),dict(pump_total_m3_s=51e-6/60),dict(solvent_fractions=[.5,.4,0,0]),dict(nitrogen_flush_s=59.)]:
   old=h.read('evaluator');self.assertRaises(ActionRejected,h.invoke,'method.configure',**dict(METHOD_EXAMPLE,**updates));self.assertEqual(old,h.read('evaluator'))
  self.assertRaises(ActionRejected,h.invoke,'method.configure',**dict(METHOD_EXAMPLE,temperature_k=473.15,pressure_gauge_pa=5e6,solvent_fractions=[1,0,0,0]))
 def test_03_preheat_same_cells_and_clearance(self):
  h=BuchiSpeedextractoreRuntime();h.invoke('plant.configure',**EXAMPLE);h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('shield.set_lift',lift_m=.18);h.invoke('drawer.set_extension',extension_m=.16)
  self.assertRaises(ActionRejected,h.invoke,'sample.configure',**SAMPLE_EXAMPLE);h.invoke('thermal.preheat');h.step(100);h.invoke('sample.configure',**SAMPLE_EXAMPLE)
  for extra in [dict(position=1,cell_volume_m3=40e-6),dict(position=True),dict(position=1,substance_id='unknown'),dict(position=1,gripper_confirmed=False)]:self.assertRaises(ActionRejected,h.invoke,'sample.configure',**dict(SAMPLE_EXAMPLE,**extra))
  self.assertRaises(ActionRejected,h.invoke,'shield.set_lift',lift_m=0.);self.assertRaises(ActionRejected,h.invoke,'method.configure',**METHOD_EXAMPLE)
 def test_04_complete_six_cell_conservation(self):
  h=finish(setup());self.assertEqual(h.phase,'succeeded');self.ledger(h);self.assertEqual(len(h._product()['items']),6);self.assertEqual(h.heater,0.)
  self.assertTrue({'sealing','tightness','fill','heatup_1','heatup_2','heatup_3','hold','discharge','nitrogen_flush','release','complete'}<={x['stage'] for x in h.history})
  for q in h._product()['items']:self.assertGreater(q['modeled_absolute_recovery_fraction'],.1);self.assertLessEqual(q['modeled_absolute_recovery_fraction'],1);self.assertIsNone(q['measured_e916_recovery_fraction'])
 def test_05_single_total_pump_and_inactive_flow(self):
  for n in (1,6):
   h=setup(n);advance(h,'fill');before=sum(h.reservoir);h.step(1.);self.assertAlmostEqual(before-sum(h.reservoir),METHOD_EXAMPLE['pump_total_m3_s']*n/6,places=13);self.ledger(h)
 def test_06_independent_positions(self):
  h=setup(2);h.cells[1]['spec']['kd_m3_kg_by_solvent']=[100.]*4;finish(h);q=h._product()['items'];self.assertGreater(q[0]['extract_target_kg'],q[1]['extract_target_kg']*100);self.ledger(h)
 def test_07_real54references_and_140vapor_points(self):
  h=BuchiSpeedextractoreRuntime();self.assertEqual(len(h.templates),54);self.assertTrue(any(q['values']['ase_to_soxtec_recovery_ratio']>1 for q in h.templates.values()))
  for sid,q in h.templates.items():
   r=h.invoke('reference.lookup',substance_id=sid);validate(REFERENCE,r);self.assertIsNone(q['cas']);self.assertIn('ASE',r['conditions']);self.assertIsNone(r['measured_e916_recovery_fraction'])
  for i,sid in enumerate(SOLVENTS):
   s=h.solvents[sid];self.assertEqual(len(s['vapor_pressure_reference_points']),35)
   for point in s['vapor_pressure_reference_points']:
    f=[0.]*4;f[i]=1.;self.assertLess(abs(h.saturation_pressure(point['temperature_k'],f)/point['pressure_pa']-1),1e-12)
  self.assertAlmostEqual(h.invoke('reference.lookup',substance_id='phenol')['ase_soxtec_ratio'],1.02)
 def test_08_cycles_increase_recovery(self):
  a=finish(setup(1,method={'cycles':1}));b=finish(setup(1,method={'cycles':3}));self.assertGreater(b.cells[0]['extract'],a.cells[0]['extract']);self.ledger(b)
 def test_09_transfer_and_hold_sensitivity(self):
  a=finish(setup(1,sample={'mass_transfer_s_inv':1e-5}));b=finish(setup(1,sample={'mass_transfer_s_inv':.1}));self.assertGreater(b.cells[0]['extract'],a.cells[0]['extract']*10)
  c=finish(setup(1,method={'hold_s':100},sample={'mass_transfer_s_inv':1e-5}));self.assertGreater(c.cells[0]['extract'],a.cells[0]['extract'])
 def test_10_solubility_degradation_bookkeeping(self):
  h=finish(setup(1,sample={'solubility_kg_m3':.00001,'degradation_s_inv':.001}));self.assertGreater(h.cells[0]['degraded'],0);self.assertLess(h.cells[0]['extract'],1e-8);self.ledger(h)
 def test_11_pause_residual_and_ordered_abort(self):
  h=setup(1);advance(h,'hold');p=h._maxp();m=h.cells[0]['dissolved'];stock=sum(h.reservoir);h.invoke('acquisition.stop');h.step(2.);self.assertEqual(h._maxp(),p);self.assertEqual(sum(h.reservoir),stock);self.assertNotEqual(h.cells[0]['dissolved'],m);self.assertRaises(ActionRejected,h.invoke,'shield.set_lift',lift_m=.18);h.invoke('acquisition.abort');finish(h);self.assertEqual((h.phase,h.stage),('cancelled','complete'));self.assertLessEqual(h._maxp(),1e5);self.ledger(h)
 def test_12_finite_supply_failure_retains_material(self):
  h=finish(setup(6,plant={'reservoir_m3':[1e-6]*4}));self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'solvent_supply_empty');self.ledger(h);self.assertGreater(sum(sum(c['liquid']) for c in h.cells.values()),0)
  h=finish(setup(1,plant={'nitrogen_mol':.001}));self.assertEqual(h.fault,'nitrogen_supply_empty');self.ledger(h)
 def test_13_vial_limit_no_disappearing_extract(self):
  h=finish(setup(1,method={'cycles':10},plant={'vial_capacity_m3':60e-6}));self.assertEqual(h.fault,'collection_vial_full');self.assertLessEqual(sum(h.cells[0]['vial']),60e-6+1e-14);self.ledger(h)
 def test_14_identity_idempotency_async(self):
  h=setup(1);h.register_principal('eval','evaluator');h.register_principal('other');s=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**kw);self.assertEqual(r['status'],'accepted');self.assertEqual(r,s.invoke(**kw));self.assertEqual(h._operation_count,1);self.assertRaises(ActionRejected,h.session('other').poll,r['operation_id']);self.assertEqual(s.poll(r['operation_id'])['status'],'running');s.cancel(r['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled');r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertRaises(ActionRejected,s.read,instance_id=h.instance_id,state_ids=['truth.initial_target_kg']);self.assertEqual(s.invoke(**dict(kw,capability_id='power.set',arguments={'enabled':False}))['error']['code'],'REQUEST_CONFLICT')
 def test_15_convergence_power_unload_and_no_n2_discharge(self):
  a=finish(setup(1),.25);b=finish(setup(1),.125);self.assertLess(abs(a.cells[0]['extract']/b.cells[0]['extract']-1),.01)
  h=setup(1);advance(h,'discharge');n=h.nitrogen;h.step(1.);self.assertEqual(n,h.nitrogen);h.invoke('power.set',enabled=False);p=h._maxp();h.step(1.);self.assertEqual(h._maxp(),p);self.assertIsNone(h.read()['thermal.indicated_k']);self.ledger(h)
  a.invoke('shield.set_lift',lift_m=.18);a.invoke('drawer.set_extension',extension_m=.16);a.invoke('sample.unload',position=0,sample_id='soil_0',gripper_confirmed=True);self.ledger(a);self.assertEqual(a._inventory()['sorbed_target_kg'],0)
  for dt in (0,-1,True,float('nan')):self.assertRaises(ActionRejected,a.step,dt)
 def test_16_cell_capacity_variants_and_solvent_flush(self):
  for volume in (10e-6,40e-6):
   h=finish(setup(1,method={'cycles':1,'solvent_flush_s':5.},sample={'cell_volume_m3':volume}));self.assertEqual(h.phase,'succeeded');self.assertIn('solvent_flush',[x['stage'] for x in h.history]);self.ledger(h)
if __name__=='__main__':unittest.main()
