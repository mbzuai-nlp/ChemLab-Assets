"""CHEM-064 independent conservation, timing, interlock and protocol checks."""
import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.arradiance_gemstarxt import *
def ready(law='alumina_tma_water',warm=True,plant=None,kinetics=None,supply=None,method=None,sample=True):
 h=ArradianceGemstarxtRuntime();p=dict(WARM_EXAMPLE if warm else PLANT_EXAMPLE)
 if law in ['zinc_dez_water','titania_ttip_water']:p['initial_k']=[423.15,423.15,373.15] if warm else [298.15]*3
 p.update(plant or {});h.invoke('plant.configure',**p)
 su=copy.deepcopy(SUPPLY_EXAMPLE);su['bottle_species'][0]=LAWS[law]['a'];su.update(supply or {});h.invoke('gas.install',**su)
 ke=dict(KINETICS_EXAMPLE,law_id=law)
 if law=='zinc_dez_water':ke.update(density_kg_m3=5600.,adsorption_pa_s_inv=1.4,conversion_pa_s_inv=1.8)
 if law=='titania_ttip_water':ke.update(density_kg_m3=3900.,adsorption_pa_s_inv=.7,conversion_pa_s_inv=.9)
 if LAWS[law]['plasma']:ke.update(plasma_site_mol_m2=3e-6,adsorption_pa_s_inv=1.,conversion_pa_s_inv=1.)
 ke.update(kinetics or {});h.invoke('kinetics.configure',**ke)
 if sample:h.invoke('sample.install',**(HOT_SAMPLE_EXAMPLE if warm else SAMPLE_EXAMPLE))
 m=copy.deepcopy(METHOD_EXAMPLE)
 if law in ['zinc_dez_water','titania_ttip_water']:m.update(reactor_k=423.15,door_k=423.15)
 if LAWS[law]['plasma']:m.update(rf_w=150.,pulse_s=[.2,.5],exposure_s=[.5,2.])
 m.update(method or {});h.invoke('method.configure',**m)
 if not warm:h.invoke('door.set',opened=False)
 h.invoke('power.set',enabled=True)
 if LAWS[law]['plasma']:h.invoke('rf.power_set',enabled=True)
 return h
def run(law='alumina_tma_water',**kwargs):
 h=ready(law,**kwargs);h.invoke('acquisition.start');h.step(100);return h
def at_stage(h,stage,limit=20000):
 for _ in range(limit):
  if h.stage==stage:return
  h.step(.02)
 raise AssertionError((h.stage,h.phase,stage))
class TestALD(unittest.TestCase):
 def conserved(self,h):
  d=h._ledger();self.assertLess(max(map(abs,d['atom_residual_mol'])),1e-10);self.assertLess(abs(d['thermal_residual_j']),1e-5)
  for x in [h.gas,h.exhaust,h.film,h.collected]:self.assertTrue(all(n>=0 for n in x.values()))
  self.assertGreaterEqual(h.adsorbed,0);self.assertLessEqual(h.adsorbed,h.capacity+1e-14)
 def rejects_unchanged(self,h,cap,**a):
  old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke(cap,**a)
  self.assertEqual(old,h.debug_snapshot())
 def test_01_primary_three_and_curated_not_50(self):
  h=ArradianceGemstarxtRuntime();self.assertEqual(len(h.templates),3);self.assertEqual(len(h.references),109)
  self.assertEqual(len({r['material'] for r in h.references.values() if r['material']}),45)
  self.assertTrue(all(not r['runnable'] for r in h.references.values()))
 def test_02_anchors_have_distinct_precursors_conditions_sources(self):
  h=ArradianceGemstarxtRuntime();self.assertEqual({r['value'] for r in h.templates.values()},{1.05e-10,1.8e-10,.5e-10})
  self.assertEqual(h.templates['titania_ttip_water']['precursor'],'TTIP')
  for r in h.templates.values():self.assertEqual(r['evidence'],'measured_literature');self.assertTrue(r['source']['sha256'])
 def test_03_all_net_reactions_element_balanced(self):
  for law in LAWS.values():
   for i in range(8):
    lhs=law['na']*ATOMS[law['a']][i]+law['nb']*ATOMS[law['b']][i]
    rhs=ATOMS[law['film']][i]+sum(c*ATOMS[s][i] for s,c in law['products'].items());self.assertEqual(lhs,rhs)
 def test_04_initial_warm_snapshot_explicit(self):
  h=ArradianceGemstarxtRuntime();self.rejects_unchanged(h,'plant.configure',**dict(WARM_EXAMPLE,warm_snapshot=False))
  h.invoke('plant.configure',**WARM_EXAMPLE);self.assertGreater(h.thermal_initial,0);self.conserved(h);self.rejects_unchanged(h,'plant.configure',**WARM_EXAMPLE)
 def test_05_cold_configuration_not_teleport(self):
  h=ready(warm=False);self.assertEqual(h.temp,[298.15]*3);self.assertEqual(h.target,[523.15,523.15,373.15]);self.assertFalse(h.opened)
 def test_06_hot_sample_only_same_initialization_clock(self):
  h=ready(sample=False);h.step(.01);self.rejects_unchanged(h,'sample.install',**HOT_SAMPLE_EXAMPLE)
 def test_07_substrate_200mm_max(self):
  h=ready(sample=False);self.rejects_unchanged(h,'sample.install',**dict(HOT_SAMPLE_EXAMPLE,area_m2=math.pi*.101**2))
 def test_08_hardware_temperature_limits(self):
  h=ready()
  for n,t in [('reactor_k',573.16),('door_k',573.16),('manifold_k',473.16)]:self.rejects_unchanged(h,'method.configure',**dict(METHOD_EXAMPLE,**{n:t}))
 def test_09_carrier_rf_hard_limits(self):
  h=ready();self.rejects_unchanged(h,'method.configure',**dict(METHOD_EXAMPLE,carrier_std_m3_s=CARRIER_MAX*1.01));self.rejects_unchanged(h,'method.configure',**dict(METHOD_EXAMPLE,rf_w=300.1))
 def test_10_temperature_anchor_not_extrapolated(self):
  h=ready();self.rejects_unchanged(h,'method.configure',**dict(METHOD_EXAMPLE,reactor_k=423.15))
 def test_11_plasma_not_thermal_fixed_gpc(self):
  h=ready(sample=False);self.rejects_unchanged(h,'kinetics.configure',**dict(KINETICS_EXAMPLE,law_id='plasma_tma_oxygen'))
  h=run('plasma_tma_oxygen');self.assertEqual(h.phase,'succeeded');self.assertGreater(h.film['Al2O3'],0);self.conserved(h)
  e=h.invoke('film.estimate');self.assertFalse(e['measured']);self.assertIn('no_measured_GPC',e['template_source'])
 def test_12_three_materials_complete_and_thickness_formula(self):
  results=[]
  for law in list(LAWS)[:3]:
   h=run(law);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.completed,2);self.conserved(h)
   ratio=h._thickness()/(2*h.templates[law]['value']);self.assertAlmostEqual(ratio,1,delta=1e-6);results.append(h._thickness())
  self.assertEqual(len(set(round(x,14) for x in results)),3)
 def test_13_finite_feed_pulse_integral(self):
  h=run();self.assertAlmostEqual(.01-h.remaining[0],2*.2*2e-4,delta=1e-12);self.assertAlmostEqual(.03-h.remaining[1],2*.2*3e-4,delta=1e-12)
 def test_14_substep_short_pulse_no_extra_dose(self):
  h=run(method={'pulse_s':[.001,.003]});self.assertAlmostEqual(.01-h.remaining[0],2*.001*2e-4,delta=1e-12);self.assertAlmostEqual(.03-h.remaining[1],2*.003*3e-4,delta=1e-12);self.conserved(h)
 def test_15_saturation_caps_growth_not_pulse_linear(self):
  a=run(method={'pulse_s':[.2,.2],'cycles':1});b=run(method={'pulse_s':[2.,2.],'cycles':1})
  self.assertLessEqual(b._thickness(),b.templates['alumina_tma_water']['value']*(1+1e-12));self.assertAlmostEqual(a._thickness(),b._thickness(),delta=1e-17);self.assertGreater(.01-b.remaining[0],8*(.01-a.remaining[0]))
 def test_16_underexposure_not_forced_nominal_gpc(self):
  h=run(kinetics={'adsorption_pa_s_inv':1e-5,'conversion_pa_s_inv':1e-5})
  self.assertLess(h._thickness(),2*h.templates['alumina_tma_water']['value']*.01);self.assertGreater(h.adsorbed,0);self.conserved(h)
 def test_17_a_half_reaction_bound_no_film(self):
  h=ready();h.invoke('acquisition.start');at_stage(h,'purge_a');self.assertGreater(h.adsorbed,0);self.assertEqual(sum(h.film.values()),0.);self.conserved(h)
 def test_18_cancel_preserves_adsorbate_residual(self):
  h=ready();h.invoke('acquisition.start');at_stage(h,'exposure_a');before=h.adsorbed;gas=h.gas['TMA'];h.invoke('acquisition.stop')
  self.assertEqual(h.phase,'cancelled');self.assertEqual(h.adsorbed,before);self.assertGreater(gas,0);h.step(1);self.assertEqual(h.adsorbed,before);self.assertLess(h.gas['TMA'],gas);self.conserved(h)
 def test_19_cancel_after_b_preserves_partial_film(self):
  h=ready(kinetics={'conversion_pa_s_inv':.001});h.invoke('acquisition.start');at_stage(h,'purge_b');f=h._thickness();self.assertGreater(f,0);h.invoke('acquisition.stop');h.step(1);self.assertEqual(h._thickness(),f);self.assertEqual(h.completed,0);self.conserved(h)
 def test_20_purge_extends_and_clears_residual(self):
  h=ready(method={'purge_s':[.001,.001],'cycles':1});h.invoke('acquisition.start');at_stage(h,'purge_a');h.step(.05);self.assertEqual(h.stage,'purge_a');at_stage(h,'pulse_b');self.assertLessEqual(h._p('TMA'),h.plant['residual_limit_pa']);self.conserved(h)
 def test_21_purge_timeout_not_fake_success(self):
  h=run(plant={'pump_m3_s':1e-5,'purge_timeout_s':1.},method={'purge_s':[.001,.001]});self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'PURGE_RESIDUAL_TIMEOUT');self.assertEqual(h.completed,0);self.conserved(h)
 def test_22_empty_source_fail_finite(self):
  h=run(supply={'bottle_mol':[1e-8,.03,0.,0.]});self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'SOURCE_EXHAUSTED');self.assertEqual(h.remaining[0],0);self.assertFalse(any(h.valves));self.conserved(h)
 def test_23_shared_carrier_not_double_count(self):
  h=ready('plasma_tma_oxygen',method={'cycles':1});h.invoke('acquisition.start');at_stage(h,'pulse_b');n=h.carrier;t=h.elapsed_s;h.step(.1)
  expected=h.method['carrier_std_m3_s']*STD_P/(R*STD_T)*(h.elapsed_s-t);self.assertAlmostEqual(n-h.carrier,expected,delta=1e-12);self.assertGreater(h.rf,0);self.conserved(h)
 def test_24_rf_independent_power_interlock(self):
  h=ready('plasma_tma_oxygen');h.invoke('rf.power_set',enabled=False);self.rejects_unchanged(h,'acquisition.start')
 def test_25_powerloss_closes_all_keeps_material(self):
  h=ready();h.invoke('acquisition.start');at_stage(h,'exposure_a');before=h.adsorbed;h.invoke('power.set',enabled=False);self.assertEqual(h.rf,0);self.assertEqual(h.valves,[0.]*4);self.assertEqual(h.adsorbed,before);self.conserved(h)
 def test_26_emo_reset_requires_both_off(self):
  h=ready();h.invoke('safety.emo');self.assertTrue(h.emo);self.rejects_unchanged(h,'safety.reset');h.invoke('power.set',enabled=False);h.invoke('rf.power_set',enabled=False);h.invoke('safety.reset');self.assertFalse(h.emo)
 def test_27_watchdog_failure_and_heartbeat(self):
  h=ready(plant={'watchdog_s':.1});h.invoke('acquisition.start');h.step(.05);h.invoke('control.heartbeat');h.step(.05);self.assertFalse(h.emo);h.step(.1);self.assertTrue(h.emo);self.assertEqual(h.warning,'WATCHDOG_TIMEOUT');self.assertEqual(h.phase,'failed')
 def test_28_hot_or_vacuum_door_rejected(self):
  h=ready();self.rejects_unchanged(h,'door.set',opened=True);h.invoke('power.set',enabled=False);self.rejects_unchanged(h,'door.set',opened=True)
 def test_29_cold_vent_collect_conserves(self):
  h=ready(warm=False);h.invoke('power.set',enabled=False);h.invoke('door.set',opened=True);h.invoke('sample.collect',recipient_id='tray');self.assertIsNone(h.sample);self.conserved(h)
 def test_30_collect_bound_and_film_after_cooling(self):
  h=ready(plant={'heat_capacity_j_k':[100.]*3,'heat_loss_w_k':[10.]*3});h.invoke('acquisition.start');at_stage(h,'purge_b');h.invoke('acquisition.stop');h.invoke('power.set',enabled=False);h.step(300);h.invoke('vacuum.vent');h.step(10);h.invoke('door.set',opened=True);h.invoke('sample.collect',recipient_id='tray');self.assertGreater(sum(h.collected.values()),0);self.conserved(h)
 def test_31_read_describe_stepzero_pure(self):
  h=ready();old=h.debug_snapshot();h.read();h.describe();h.invoke('process.measure');h.invoke('product.read');h.invoke('film.estimate');h.step(0);self.assertEqual(old,h.debug_snapshot())
 def test_32_rejection_atomic_and_numeric_boolean(self):
  h=ready()
  for x in [True,float('nan'),float('inf'),-1]:self.rejects_unchanged(h,'method.configure',**dict(METHOD_EXAMPLE,carrier_std_m3_s=x))
  h.invoke('acquisition.start');self.rejects_unchanged(h,'method.configure',**METHOD_EXAMPLE)
 def test_33_two_instance_independence(self):
  a=ready();b=ready();old=b.debug_snapshot();a.invoke('acquisition.start');a.step(2);self.assertEqual(b.debug_snapshot(),old)
 def test_34_truth_filter_and_host_only_identity(self):
  h=ready();s=h.session();self.assertNotIn('truth.ledger',s.describe(instance_id=h.instance_id)['state_slots']);self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities'])
  r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
  with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
  with self.assertRaises(ActionRejected):h.session('evaluator')
 def test_35_idempotency_conflict_operation_owner(self):
  h=ready();s=h.session();args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a')
  r=s.invoke(**args);self.assertEqual(s.invoke(**args),r);self.assertEqual(h._operation_count,1)
  bad=s.invoke(instance_id=h.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='a');self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT')
  h.register_principal('other');other=h.session('other')
  with self.assertRaises(ActionRejected):other.poll(r['operation_id'])
  s.cancel(r['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled')
 def test_36_template_reference_raw_not_runnable(self):
  h=ready();r=h.invoke('reference.read',reference_id='gold_002');self.assertFalse(r['runnable']);self.assertIn('not independently',r['evidence'])
  self.rejects_unchanged(h,'template.read',substance_id='gold_002')
 def test_37_cold_full_cycle_actually_heats(self):
  h=ready(warm=False,plant={'heat_capacity_j_k':[100.]*3},method={'cycles':1});h.invoke('acquisition.start');h.step(.1);self.assertLess(h.temp[0],310);h.step(180);self.assertEqual(h.phase,'succeeded');self.assertGreater(h._thickness(),0);self.conserved(h)
 def test_38_pulse_chunking_stability(self):
  a=ready(method={'pulse_s':[.003,.005]});b=ready(method={'pulse_s':[.003,.005]})
  for h in [a,b]:h.invoke('acquisition.start')
  a.step(60)
  for _ in range(6000):b.step(.01)
  self.assertAlmostEqual(a.remaining[0],b.remaining[0],delta=1e-12);self.assertAlmostEqual(a.remaining[1],b.remaining[1],delta=1e-12);self.conserved(a);self.conserved(b)
 def test_39_heater_electrical_upper_bound(self):
  h=ArradianceGemstarxtRuntime();self.rejects_unchanged(h,'plant.configure',**dict(WARM_EXAMPLE,heater_max_w=[1500.,600.,400.]))
  self.assertLessEqual(sum(PLANT_EXAMPLE['heater_max_w']),120*20)
 def test_40_bad_step_does_not_advance(self):
  h=ready()
  for dt in [True,-1.,float('nan'),float('inf')]:
   old=h.debug_snapshot()
   with self.assertRaises(ActionRejected):h.step(dt)
   self.assertEqual(old,h.debug_snapshot())
 def test_41_standard_bottles_not_four_filled(self):
  self.assertEqual(sum(n>0 for n in SUPPLY_EXAMPLE['bottle_mol']),2)
  h=ArradianceGemstarxtRuntime();h.invoke('plant.configure',**WARM_EXAMPLE)
  self.rejects_unchanged(h,'gas.install',**dict(SUPPLY_EXAMPLE,bottle_species=['TMA','TMA','EMPTY','EMPTY']))
 def test_42_session_write_routes_to_checked_command(self):
  h=ready();s=h.session();result=s.write(instance_id=h.instance_id,state_id='power.enabled',value=False,request_id='power-write');self.assertEqual(result['status'],'succeeded');self.assertFalse(h.powered)
  with self.assertRaises(ActionRejected):s.write(instance_id=h.instance_id,state_id='truth.ledger',value={},request_id='private')
 def test_43_power_EMO_watchdog_close_vacuum_NC(self):
  for cap,a in [('power.set',{'enabled':False}),('safety.emo',{})]:
   h=ready();h.invoke('acquisition.start');h.step(.1);self.assertTrue(h.pump);h.invoke(cap,**a);self.assertFalse(h.pump);self.assertEqual(h.read()['vacuum.angle_rad'],0.);self.conserved(h)
  h=ready(plant={'watchdog_s':.1});h.invoke('acquisition.start');h.step(.2);self.assertFalse(h.pump);self.conserved(h)
 def test_44_chemistry_change_invalidates_old_method(self):
  h=ready(sample=False);self.assertTrue(h.method);h.invoke('kinetics.configure',**dict(KINETICS_EXAMPLE,law_id='zinc_dez_water'));self.assertIsNone(h.method)
  self.rejects_unchanged(h,'method.configure',**METHOD_EXAMPLE)
if __name__=='__main__':unittest.main()



