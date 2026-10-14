import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.tousimis_autosamdri815 import *
def ready(plant=None,sample=None,method=None):
 h=TousimisAutosamdri815Runtime();p=copy.deepcopy(EXAMPLE);p.update(plant or {});s=copy.deepcopy(SAMPLE_EXAMPLE);s.update(sample or {});m=copy.deepcopy(METHOD_EXAMPLE);m.update(method or {})
 h.invoke('plant.configure',**p);h.invoke('lid.set_lift',lift_m=.065);h.invoke('holder.set_lift',lift_m=.035);h.invoke('sample.configure',**s);h.invoke('holder.set_lift',lift_m=0.);h.invoke('lid.set_lift',lift_m=0.);h.invoke('method.configure',**m);h.invoke('power.set',enabled=True);return h
class CriticalDryingTests(unittest.TestCase):
 def balance(self,h):
  v=h._inventory();self.assertAlmostEqual(v['initial_co2'],sum(v[k] for k in ['tank_co2','chamber_co2','exhaust_co2']),places=10);self.assertAlmostEqual(v['initial_solvent'],sum(v[k] for k in ['bath_solvent','pore_solvent','exhaust_solvent','removed_solvent']),places=12);self.assertAlmostEqual(v['initial_solid'],v['retained_solid']+v['removed_solid'],places=12)
  self.assertTrue(all(x>=-1e-12 for x in v.values()));self.assertTrue(0<=h.retention<=1)
 def test_01_all59_real_correlations_not_measured_retention(self):
  h=TousimisAutosamdri815Runtime();self.assertEqual(len(h.templates),59);self.assertEqual(len({t['cas'] for t in h.templates.values()}),59)
  for sid,t in h.templates.items():
   r=h.invoke('reference.lookup',substance_id=sid,temperature_k=298.15);validate(REFERENCE,r);self.assertAlmostEqual(r['surface_tension_n_m'],t['values']['surface_tension_n_m'],places=12);self.assertIn('derived',r['evidence']);self.assertFalse(r['fit_range_verified']);self.assertIsNone(t['structure_retention_fraction'])
  self.assertFalse(h.invoke('reference.lookup',substance_id='water',temperature_k=298.15)['cpd_solvent_supported'])
 def test_02_reference_range_and_finite_inputs(self):
  h=ready();before=h.debug_snapshot()
  for sid,t in [('unknown',298.15),('carbondioxide',310.),('water',250.),('methanol',513.),('ethanol',math.nan)]:
   with self.assertRaises(ActionRejected):h.invoke('reference.lookup',substance_id=sid,temperature_k=t)
  for dt in [0,-1,True,math.inf,math.nan,7201]:
   with self.assertRaises(ValueError):h.step(dt)
  self.assertEqual(before,h.debug_snapshot())
 def test_03_pure_CO2_phase_and_abs_pressure(self):
  # Rounded published critical pressure versus numerical EOS critical solution (1.63 Pa).
  self.assertAlmostEqual(cp().PropsSI('Tcrit','CO2'),TC,places=4);self.assertAlmostEqual(cp().PropsSI('pcrit','CO2'),PC,delta=2.)
  p,phase,q=co2_state(273.15,500.);self.assertEqual(phase,'twophase');self.assertTrue(0<q<1);self.assertAlmostEqual(p,3485140.,delta=200.)
  p,phase,q=co2_state(309.15,700.);self.assertGreater(p,PC);self.assertEqual(phase,'supercritical');self.assertIsNone(q)
 def test_04_rejected_plant_and_sample_do_not_mutate(self):
  h=TousimisAutosamdri815Runtime();before=h.debug_snapshot()
  for a in [dict(EXAMPLE,dip_tube_liquid_confirmed=False),dict(EXAMPLE,fill_m3_s=EXAMPLE['purge_m3_s']),dict(EXAMPLE,supply_gauge_pa=1500*PSI),dict(EXAMPLE,pressure_guard_gauge_pa=2000*PSI)]:
   with self.assertRaises(ActionRejected):h.invoke('plant.configure',**a)
   self.assertEqual(before,h.debug_snapshot())
  h.invoke('plant.configure',**EXAMPLE);h.invoke('lid.set_lift',lift_m=.065);h.invoke('holder.set_lift',lift_m=.035);before=h.debug_snapshot()
  for a in [dict(SAMPLE_EXAMPLE,water_replaced_confirmed=False),dict(SAMPLE_EXAMPLE,bath_volume_m3=20e-6,pore_volume_m3=10e-6),dict(SAMPLE_EXAMPLE,solvent_id='water')]:
   with self.assertRaises(ActionRejected):h.invoke('sample.configure',**a)
   self.assertEqual(before,h.debug_snapshot())
 def test_05_setting_and_read_do_not_advance(self):
  h=ready();t=h.time_s;h.invoke('method.configure',purge_time_s=300.,bleed_rate_pa_s=100*PSI/60)
  for _ in range(10):h.describe();h.read();h.invoke('cpd.measure')
  self.assertEqual(h.time_s,t);self.assertEqual(h.temperature,EXAMPLE['ambient_k']);self.assertEqual(h.method['purge_time_s'],300.)
 def test_06_full_cycle_real_timers_and_conservation(self):
  h=ready();h.invoke('acquisition.start');h.step(2100);self.assertEqual(h.phase,'succeeded');self.assertTrue(h.controller_pass);self.balance(h);r=h.invoke('product.read');validate(PRODUCT,r);self.assertTrue(r['dry_within_host_threshold']);self.assertIsNone(r['measured_structure_retention']);self.assertIn('synthetic',r['evidence'])
  stages={}
  for row in h.history:stages.setdefault(row['stage'],row['time_s'])
  for stage in ['warmup','cool','fill','purge','postfill','heat','equilibrium','bleed','vent','complete']:self.assertIn(stage,stages)
  for a,b,want in [('fill','purge',120),('purge','postfill',600),('postfill','heat',120),('equilibrium','bleed',240),('vent','complete',180)]:self.assertAlmostEqual(stages[b]-stages[a],want,delta=.21)
  self.assertLess(h.pressure-h._ambient(),50);self.assertGreater(h.co2_out,0);self.assertGreater(h.solvent_out,0)
 def test_07_zero_purge_is_not_guaranteed_clean(self):
  h=ready(method={'purge_time_s':0.});h.invoke('acquisition.start');h.step(700);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'insufficient_solvent_exchange');self.assertFalse(h._product()['dry_within_host_threshold']);self.balance(h)
 def test_08_finite_supply_failure(self):
  h=ready(plant={'tank_mass_kg':.001});h.invoke('acquisition.start');h.step(300);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'co2_supply_empty');self.balance(h)
 def test_09_cancel_keeps_pressure_and_thermal_evolution(self):
  h=ready();h.invoke('acquisition.start');h.step(350);h.invoke('acquisition.stop');m=h.mco2;b=h.bath;stage=h.stage;t=h.temperature;h.step(10);self.assertEqual(h.mco2,m);self.assertEqual(h.bath,b);self.assertEqual(h.stage,stage);self.assertGreater(h.temperature,t);self.assertGreater(h.pressure-h._ambient(),50);self.balance(h)
  before=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('lid.set_lift',lift_m=.065)
  self.assertEqual(before,h.debug_snapshot())
 def test_10_resume_does_not_reset_material(self):
  h=ready();h.invoke('acquisition.start');h.step(400);h.invoke('acquisition.stop');v=h.solvent_out;h.step(.2);h.invoke('acquisition.start');self.assertEqual(h.solvent_out,v);h.step(1800);self.assertEqual(h.phase,'succeeded');self.balance(h)
 def test_11_open_unload_only_after_depressurized(self):
  h=ready();h.invoke('acquisition.start');h.step(2100)
  with self.assertRaises(ActionRejected):h.invoke('sample.unload',sample_id='wet_die_1')
  h.invoke('lid.set_lift',lift_m=.065);h.invoke('holder.set_lift',lift_m=.035);h.invoke('sample.unload',sample_id='wet_die_1');self.balance(h)
  with self.assertRaises(ActionRejected):h.invoke('sample.unload',sample_id='wet_die_1')
 def test_12_condition_structure_model_responds_to_strength(self):
  a=ready(sample={'yield_stress_pa':1e5,'damage_rate_s_inv':.01});b=ready(sample={'yield_stress_pa':1e8,'damage_rate_s_inv':.01})
  for h in (a,b):h.invoke('acquisition.start');h.step(2100);self.balance(h)
  self.assertLess(a.retention,b.retention);self.assertLess(a.retention,.99);self.assertEqual(b.retention,1.)
 def test_13_safety_guard_is_not_gauge_range(self):
  h=ready(plant={'pressure_guard_gauge_pa':1250*PSI});h.invoke('acquisition.start');h.step(1200);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'host_pressure_guard');self.balance(h)
  low=ready(plant={'fill_m3_s':1.1e-9,'purge_m3_s':1e-9});low.invoke('acquisition.start');low.step(1200);self.assertEqual(low.phase,'failed');self.assertEqual(low.fault,'critical_signal_order_failure');self.assertFalse(low.controller_pass);self.balance(low)
 def test_14_session_ownership_idempotency_visibility(self):
  h=ready();h.register_principal('a');h.register_principal('b');h.register_principal('judge','evaluator');a=h.session('a');b=h.session('b');args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=a.invoke(**args);self.assertEqual(r['status'],'accepted');self.assertEqual(a.invoke(**args),r)
  with self.assertRaises(ActionRejected):b.poll(r['operation_id'])
  h.step(200);a.cancel(r['operation_id'],'cancel');self.assertEqual(a.poll(r['operation_id'])['status'],'cancelled');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='cpd.measure',arguments={},request_id='start')['status'],'rejected');self.assertFalse(any(k.startswith('truth.') for k in a.read(instance_id=h.instance_id)['observations']));self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='p')['status'],'rejected')
 def test_15_instance_schema_isolation_and_partition(self):
  a=ready();b=ready();a.invoke('acquisition.start');a.step(250);self.assertEqual(b.phase,'idle');self.assertEqual(b.mco2,0);meta=export_tousimis_autosamdri815_contract_metadata();self.assertEqual(set(meta['state_slots']),set(a.read('evaluator')))
  for key,v in a.read('evaluator').items():validate(SLOTS[key][0],v)
  for cap in ['cpd.measure','product.read','history.read']:validate(CAPS[cap][1],a.invoke(cap))
  b.invoke('acquisition.start')
  for _ in range(25):b.step(10)
  self.assertAlmostEqual(a.mco2,b.mco2,places=10);self.assertAlmostEqual(a.temperature,b.temperature,places=9)
if __name__=='__main__':unittest.main()
