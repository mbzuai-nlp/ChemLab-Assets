import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.sft_sft110 import *
def configured(plant=None,method=None,sample=None):
 h=SftSft110Runtime();h.invoke('plant.configure',**dict(EXAMPLE,**(plant or {})));h.invoke('method.configure',**dict(METHOD_EXAMPLE,**(method or {})));h.invoke('door.set_angle',angle_rad=1.2);h.invoke('cap.set_lift',lift_m=.07);h.invoke('sample.configure',**dict(SAMPLE_EXAMPLE,**(sample or {})));h.invoke('cap.set_lift',lift_m=0.);h.invoke('door.set_angle',angle_rad=0.);h.invoke('power.set',enabled=True);return h
def until(h,stage,limit=4000):
 for _ in range(limit):
  if h.stage==stage or h.phase in ['failed','succeeded']:return h
  h.step(.5)
 raise AssertionError((h.stage,h.phase))
def finish(h):
 h.invoke('acquisition.start')
 for _ in range(3000):
  h.step(.5)
  if h.phase!='running':return h
 raise AssertionError((h.stage,h.phase))
class TestSft110(unittest.TestCase):
 def ledger(self,h):
  v=h.read('evaluator');self.assertAlmostEqual(v['truth.initial_co2_kg'],sum(v['truth.'+x+'_co2_kg'] for x in ['tank','chamber','exhaust']),places=10);self.assertAlmostEqual(v['truth.initial_solute_kg'],sum(v['truth.'+x+'_solute_kg'] for x in ['residue','dissolved','collected','deposit']),places=12)
 def test_01_reference_exact_and_interpolation(self):
  h=SftSft110Runtime();self.assertEqual(len(h.templates),60)
  for t in h.templates.values():
   points=t['values']['solubility_curve']
   for p in points:self.assertAlmostEqual(h._reference(t['substance_id'],313.15,p['pressure_abs_pa'])['mole_fraction'],p['mole_fraction'],places=12)
   a,b=points[:2];self.assertAlmostEqual(h._reference(t['substance_id'],313.15,(a['pressure_abs_pa']+b['pressure_abs_pa'])/2)['mole_fraction'],math.sqrt(a['mole_fraction']*b['mole_fraction']),places=12)
 def test_02_full_cycle(self):
  h=finish(configured());self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.pressure,101325.,delta=1.);self.assertGreater(h.collected,0);self.assertGreater(h.residue,0);self.ledger(h)
 def test_03_static_no_flow(self):
  h=configured();h.invoke('acquisition.start');until(h,'static');old=h.tank;h.step(20);self.assertEqual(h.tank,old);self.assertEqual(h.qin,0.);self.assertGreater(h.dissolved,0.);self.ledger(h)
 def test_04_cancel_residual_resume(self):
  h=configured();h.invoke('acquisition.start');until(h,'dynamic');h.step(10);old=h.read('evaluator');h.invoke('acquisition.stop');h.step(10);self.assertEqual(h.collected,old['truth.collected_solute_kg']);self.assertEqual(h.mco2,old['truth.chamber_co2_kg']);self.assertGreater(h.pressure,1e7);self.assertEqual(h._valve_angle(),0.);h.invoke('acquisition.start');h.step(10);self.assertGreater(h.collected,old['truth.collected_solute_kg']);self.ledger(h)
 def test_05_manual_handling_guard_atomic(self):
  h=configured();h.invoke('acquisition.start');until(h,'static');h.invoke('acquisition.stop');old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('door.set_angle',angle_rad=1.2)
  self.assertEqual(old,h.debug_snapshot())
 def test_06_power_loss_conserves_trapped_mass(self):
  h=configured();h.invoke('acquisition.start');until(h,'dynamic');old=h.mco2;oldp=h.pressure;h.invoke('power.set',enabled=False);h.step(20);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.mco2,old);self.assertLess(h.pressure,oldp);self.assertGreater(h.pressure,1e6);self.ledger(h)
 def test_07_deposition_fault(self):
  h=finish(configured(plant={'capture_fraction':0.,'deposit_limit_kg':1e-8}));self.assertEqual(h.fault,'RESTRICTOR_DEPOSITION_LIMIT');self.assertGreater(h.pressure,1e6);self.ledger(h)
 def test_08_collection_full(self):
  h=finish(configured(plant={'collection_capacity_kg':1e-7}));self.assertEqual(h.fault,'COLLECTION_FULL');self.ledger(h)
 def test_09_finite_supply(self):
  h=finish(configured(plant={'tank_mass_kg':.0001}));self.assertEqual(h.fault,'CO2_SUPPLY_EMPTY');self.ledger(h)
 def test_10_reference_domain_reject(self):
  h=configured();old=h.debug_snapshot()
  for t,p in [(323.15,20e6),(313.15,1e6)]:
   with self.assertRaises(ActionRejected):h.invoke('reference.lookup',substance_id=SAMPLE_EXAMPLE['substance_id'],temperature_k=t,pressure_abs_pa=p)
  self.assertEqual(h.debug_snapshot(),old)
 def test_11_pure_queries_clock_and_schema(self):
  h=configured();old=h.debug_snapshot();h.read();h.describe();h.invoke('extraction.measure');self.assertEqual(old,h.debug_snapshot())
  for dt in [True,-1,float('nan')]:
   with self.assertRaises(ActionRejected):h.step(dt)
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,modifier_fraction=.1))
  self.assertEqual(old,h.debug_snapshot())
 def test_12_native_protocol(self):
  h=configured();h.register_principal('e','evaluator');a=h.session();e=h.session('e');self.assertNotIn('truth.initial_solute_kg',a.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.configure',a.describe(instance_id=h.instance_id)['capabilities']);r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(r['status'],'accepted');self.assertEqual(r,a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one'));self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='extraction.measure',arguments={},request_id='one')['error']['code'],'REQUEST_CONFLICT');self.assertEqual(a.poll(r['operation_id'])['status'],'running')
  with self.assertRaises(ActionRejected):e.poll(r['operation_id'])
  a.cancel(r['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['status'],'rejected')
 def test_13_instances_and_timeouts(self):
  a=finish(configured(method={'timeout_s':10.}));b=configured();self.assertEqual(a.fault,'TIMEOUT');self.assertEqual(b.phase,'idle');self.assertEqual(b.tank,1.);self.ledger(a)
 def test_14_pressure_drop_guard(self):
  h=SftSft110Runtime();h.invoke('plant.configure',**dict(EXAMPLE,particle_diameter_m=1e-6,porosity=.1));old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,pressure_gauge_pa=68e6))
  self.assertEqual(old,h.debug_snapshot())
 def test_15_liquid_supply_reject(self):
  h=SftSft110Runtime();old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('plant.configure',**dict(EXAMPLE,supply_temperature_k=293.15,supply_pressure_abs_pa=4e6))
  self.assertEqual(old,h.debug_snapshot())
 def test_16_explicit_vent(self):
  h=configured();h.invoke('acquisition.start');until(h,'static');h.invoke('acquisition.stop');h.invoke('pressure.vent');h.step(300);self.assertEqual(h.phase,'succeeded');self.ledger(h)
 def test_17_method_only_sets_target(self):
  h=configured();self.assertEqual(h.temperature,EXAMPLE['ambient_k']);self.assertEqual(h.pressure,EXAMPLE['ambient_pa']);self.assertEqual(h.elapsed_s,0)
if __name__=='__main__':unittest.main()
