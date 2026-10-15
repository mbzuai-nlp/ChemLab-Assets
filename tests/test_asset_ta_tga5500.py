"""CHEM132: actual chemistry/energy/gas/host protocol regressions, not presence-only tests."""
import copy,importlib.util,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import ta_tga5500 as rt
OBSERVED=[];STATES=[];SCHEMA_REPORT={}
def state(h):return json.dumps(h.__dict__,sort_keys=True,allow_nan=False,default=lambda x:x.item() if hasattr(x,'item') else str(x))
def call(h,k,**a):
 before=h.read();r=h.invoke(k,**a);after=h.read();json.dumps(r,allow_nan=False);json.dumps(after,allow_nan=False);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));STATES.append(after);changed={x for x in before if before[x]!=after[x]}
 if not changed<=set(rt.CAPS[k]['effects']):raise AssertionError((k,'undeclared effects',changed-set(rt.CAPS[k]['effects'])))
 return r
def step(h,t):h.step(t);STATES.append(h.read());json.dumps(h.read(),allow_nan=False)
def quick(**kw):return dict(rt.METHOD_EXAMPLE,segments=[dict(target_k=300.15,rate_k_s=.5,hold_s=1.,gas='N2')],**kw)
def setup(name='tga_test',plant=None,sample=None,gas=None,method=None):
 h=rt.TaTga5500Runtime(name);call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,**(plant or {})));call(h,'power.set',enabled=True);call(h,'sensor.calibrate',**rt.CALIBRATION_EXAMPLE);call(h,'balance.tare');call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,**(sample or {})));call(h,'gas.load',**dict(rt.GAS_EXAMPLE,**(gas or {})));call(h,'gas.load',**dict(rt.GAS_EXAMPLE,gas='O2'));call(h,'furnace.set',opened=False);step(h,2.1);call(h,'gas.set_flow',sample_flow_m3_s=25e-6/60,balance_flow_m3_s=10e-6/60);call(h,'gas.select',gas='N2');call(h,'gas.set_open',opened=True);call(h,'method.configure',**(method or quick()));return h
def run(h):
 call(h,'acquisition.start')
 for _ in range(30000):
  step(h,.1)
  if h.phase!='running':break
 if h.phase=='running':raise AssertionError('no terminal state')
 return call(h,'acquisition.result')
def cool(h):
 if h.phase=='running':call(h,'acquisition.stop')
 call(h,'thermal.disable');call(h,'thermal.set_cooling',enabled=True)
 for _ in range(10000):
  if h._cold():break
  step(h,.1)
 else:raise AssertionError('not cold')
def cleanup(h):
 cool(h);call(h,'gas.set_open',opened=False);call(h,'furnace.set',opened=True);step(h,2.1);call(h,'sample.remove');call(h,'gas.unload',gas='N2');call(h,'gas.unload',gas='O2');call(h,'power.set',enabled=False)
class TestTGA5500(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=state(h)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,state(h))
 def ledger(self,h):
  l=call(h,'ledger.read');self.assertLess(abs(l['sample_error_kg']),1e-15);self.assertLess(abs(l['n2_error_mol']),1e-10);self.assertLess(abs(l['o2_error_mol']),1e-10);self.assertLess(abs(l['energy_error_j']),2e-6);return l
 def test_01_two_actual_source_curves_raw_noise_preserved(self):
  lib=rt.library()['substances']
  for rid in ['fsri_abs_n2_10k_r1','fsri_hdpe_n2_10k_r1']:
   r=lib[rid];self.assertTrue(r['runtime_usable']);self.assertGreater(len(r['values']['temperature_k']),5000);self.assertIn(r['conditions']['gas'],['N2','nitrogen']);self.assertAlmostEqual(r['conditions']['heating_rate_k_s'],1/6);self.assertGreater(r['values']['time_s'][0],500);self.assertIn('sha256',r['source'])
 def test_02_two_materials_drive_actual_distinct_observations(self):
  masses=[]
  for rid in ['fsri_abs_n2_10k_r1','fsri_hdpe_n2_10k_r1']:
   h=setup(sample={'reference_id':rid},method=copy.deepcopy(rt.METHOD_EXAMPLE));r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['elapsed_s'],105.);self.assertLess(h._mass(),2e-6);masses.append(r['rows'][-1]['mass_kg']);self.ledger(h)
  self.assertGreater(abs(masses[0]-masses[1]),1e-8)
 def test_03_projection_audited_raw_not_mutated(self):
  ref=dict(runtime_usable=True,values=dict(temperature_k=[300.,310.,310.,320.,305.],mass_fraction=[1.,1.1,.8,-.01,.95]));old=copy.deepcopy(ref);m=rt.prepare_reference(ref,True);self.assertEqual(ref,old);self.assertEqual(m['temperature'],[300.,305.,310.,320.]);self.assertEqual(m['mass_fraction'],[1.,.95,.95,0.]);self.assertEqual(m['stats']['duplicate_temperatures_removed'],1);self.assertEqual(m['stats']['clipped_points'],2)
  with self.assertRaisesRegex(rt.ActionRejected,'PROJECTION'):rt.prepare_reference(ref,False)
 def test_04_targets_not_truth_queries_no_clock_or_mutation(self):
  h=setup();call(h,'thermal.set_temperature',temperature_k=400.,rate_k_s=1.);self.assertEqual(h.target,298.15);self.assertEqual(h.furnace,298.15);before=state(h);h.read();h.describe();call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);call(h,'acquisition.result');call(h,'ledger.read');self.assertEqual(state(h),before);step(h,1);self.assertAlmostEqual(h.target,299.15);self.assertLess(h.furnace,h.target)
 def test_05_program_holds_switches_and_terminal_timestamps(self):
  m=quick();m['segments']=[dict(target_k=300.15,rate_k_s=.5,hold_s=1.,gas='N2'),dict(target_k=299.15,rate_k_s=.5,hold_s=.5,gas='O2')];h=setup(method=m);r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['elapsed_s'],7.5);self.assertEqual(r['rows'][-1]['elapsed_s'],r['elapsed_s']);self.assertTrue(any(x['stage']=='hold' for x in r['rows']));self.assertGreater(h.chamber[1],0);self.ledger(h)
 def test_06_conservative_cstr_analytic_and_deficit_fill(self):
  n,out=rt.mix_chamber(1.,0.,'O2',.4,1.);self.assertAlmostEqual(n[0],math.exp(-.4));self.assertAlmostEqual(n[1],1-math.exp(-.4));self.assertAlmostEqual(out[0]+out[1],.4)
  n,out=rt.mix_chamber(.4,.1,'N2',.3,1.);self.assertEqual(n,[.7,.1]);self.assertEqual(out,[0.,0.]);n,out=rt.mix_chamber(0.,0.,'N2',2.,1.);self.assertEqual(n,[1.,0.]);self.assertEqual(out,[1.,0.])
 def test_07_sample_gas_switch_not_instant_chamber_or_balance_oxygen(self):
  h=setup();before=h.chamber.copy();call(h,'gas.select',gas='O2');self.assertEqual(h.chamber,before);N=h.gas_remaining['N2'];O=h.gas_remaining['O2'];step(h,1);self.assertGreater(h.chamber[1],0);self.assertLess(h.chamber[1]/sum(h.chamber),1);self.assertAlmostEqual(N-h.gas_remaining['N2'],h.balance_flow*101325/(rt.R_GAS*298.15),places=12);self.assertAlmostEqual(O-h.gas_remaining['O2'],h.sample_flow*101325/(rt.R_GAS*298.15),places=12);self.ledger(h)
 def test_08_finite_oxygen_uptake_mass_consumption_and_pressure_deficit(self):
  h=setup(sample={'oxidation_capacity_fraction':.2,'oxidation_rate_s_inv':100.,'oxidation_activation_j_mol':0.});call(h,'gas.select',gas='O2');step(h,.1);self.assertGreater(h.uptake,0);self.assertLessEqual(h.uptake,2e-6);self.assertLess(sum(h.chamber),h.capacity);self.assertAlmostEqual(h._mass(),1e-5+h.uptake,delta=1e-12);l=self.ledger(h);self.assertAlmostEqual(l['o2_consumed_mol']*rt.M_O2,l['oxygen_uptake_kg'],places=15)
 def test_09_closed_valves_finite_oxygen_no_free_nitrogen(self):
  h=setup(sample={'oxidation_capacity_fraction':2.,'oxidation_rate_s_inv':10.,'oxidation_activation_j_mol':0.});call(h,'gas.select',gas='O2');step(h,.2);call(h,'gas.set_open',opened=False);n=h.chamber[0];o=h.chamber[1];u=h.uptake;step(h,10);self.assertEqual(n,h.chamber[0]);self.assertAlmostEqual(h.uptake-u,(o-h.chamber[1])*rt.M_O2,places=14);self.assertLessEqual(h.uptake-u,o*rt.M_O2+1e-18);self.ledger(h)
 def test_10_dual_branch_finite_n2_event_exact_terminal(self):
  h=setup(gas={'amount_mol':1e-9});rate=(h.sample_flow+h.balance_flow)*101325/(rt.R_GAS*298.15);call(h,'acquisition.start');step(h,.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'GAS_EXHAUSTED');self.assertAlmostEqual(h.elapsed,1e-9/rate,places=13);self.assertLess(h.gas_remaining['N2'],1e-24);self.assertFalse(h.thermal_enabled);self.assertEqual(h.rows[-1]['elapsed_s'],h.elapsed);self.ledger(h)
 def test_11_extreme_tiny_gas_finite_termination_not_infinite(self):
  h=setup(gas={'amount_mol':1e-18});call(h,'acquisition.start');step(h,3.);self.assertEqual(h.phase,'failed');self.assertLess(h.elapsed,1e-10);self.assertAlmostEqual(h.time_s,5.1);self.ledger(h)
 def test_12_o2_selected_balance_n2_exhaustion_stops_heater(self):
  h=setup(gas={'amount_mol':1e-9});m=quick();m['segments'][0]['gas']='O2';call(h,'method.configure',**m);call(h,'acquisition.start');step(h,.1);self.assertEqual(h.phase,'failed');self.assertGreater(h.gas_remaining['O2'],.099);self.assertFalse(h.gas_open);self.ledger(h)
 def test_13_two_node_heat_energy_closed_and_cooling_destination(self):
  h=setup(method=copy.deepcopy(rt.METHOD_EXAMPLE));run(h);self.assertGreater(h.mass_emitted,0);self.assertGreater(h.emitted_sensible,0);E=h.sample_energy+self._ef(h);cool(h);self.assertLess(h.sample_energy+self._ef(h),E);self.assertGreater(h.cooling_removed,0);self.ledger(h);cleanup(h);l=self.ledger(h);self.assertEqual(l['sample_present_kg'],0);self.assertAlmostEqual(l['sample_loaded_kg'],l['sample_recovered_kg']+l['sample_emitted_kg'],places=14)
 def _ef(self,h):return h.plant['furnace_capacity_j_k']*(h.furnace-h.plant['ambient_k'])
 def test_14_reaction_enthalpy_changes_energy_without_mass_invention(self):
  h=setup(sample={'oxidation_capacity_fraction':.3,'oxidation_rate_s_inv':2.,'oxidation_activation_j_mol':0.,'oxidation_heat_j_kg':5e5});call(h,'gas.select',gas='O2');step(h,5);self.assertGreater(h.reaction_heat,0);self.assertAlmostEqual(h.reaction_heat,h.uptake*5e5,places=10);self.assertGreater(h.sample_temperature,298.15);self.ledger(h)
 def test_15_stiff_thermal_and_stiff_chemistry_remain_finite(self):
  h=setup(plant={'furnace_capacity_j_k':1e-5,'pan_capacity_j_k':1e-7,'sample_conductance_w_k':1000.,'controller_gain_w_k':1e5},sample={'mass_kg':1e-10,'loss_relaxation_s':1e-6});r=run(h);self.assertEqual(r['phase'],'succeeded');self.assertTrue(all(298<x['sample_k']<301 for x in r['rows']));self.ledger(h)
 def test_16_cancel_freezes_history_and_retains_hot_mass(self):
  h=setup(method=copy.deepcopy(rt.METHOD_EXAMPLE));call(h,'acquisition.start');step(h,80.);mass=h._mass();call(h,'acquisition.stop');r=call(h,'acquisition.result');self.assertEqual(h._mass(),mass);self.assertFalse(h.thermal_enabled);step(h,10.);self.assertEqual(r,call(h,'acquisition.result'));self.assertLessEqual(h._mass(),mass);self.ledger(h)
 def test_17_cold_loss_target_not_latched_and_does_not_restore_mass(self):
  h=setup(method=copy.deepcopy(rt.METHOD_EXAMPLE),sample={'loss_relaxation_s':50.});call(h,'acquisition.start');step(h,75.);call(h,'acquisition.stop');call(h,'thermal.set_cooling',enabled=True);step(h,120.);loss=h.loss;step(h,30);self.assertEqual(loss,h.loss);self.assertGreater(loss,0);self.assertLess(h.sample_temperature,310);self.ledger(h)
 def test_18_balance_dynamic_independent_gain_zero_and_resolution(self):
  h=setup(plant={'mass_gain':.9,'mass_offset_kg':2e-7});step(h,2);a=h.read()['mass.indicated_kg'];self.assertAlmostEqual(a,.9*1e-5,delta=8e-12);self.assertNotEqual(a,h._mass());call(h,'sensor.calibrate',**dict(h.calibration,mass_gain_correction=1/.9));self.assertAlmostEqual(h.read()['mass.indicated_kg'],1e-5,delta=8e-12);self.assertAlmostEqual(h.read()['mass.indicated_kg']/8e-12,round(h.read()['mass.indicated_kg']/8e-12),places=7)
 def test_19_high_low_range_guard_no_clipped_truth(self):
  h=rt.TaTga5500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);self.rejected(h,'sample.load','BALANCE_OVERLOAD',**dict(rt.SAMPLE_EXAMPLE,mass_kg=.0005));call(h,'balance.set_range',range='high');call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,mass_kg=.0005));self.rejected(h,'balance.set_range','BALANCE_OVERLOAD',range='low');call(h,'power.set',enabled=True);step(h,3);self.assertAlmostEqual(h.read()['mass.indicated_kg']/6e-11,round(h.read()['mass.indicated_kg']/6e-11),places=7)
 def test_20_oxidation_overload_actual_failure_and_unavailable_observation(self):
  h=setup(sample={'mass_kg':.0001,'oxidation_capacity_fraction':.1,'oxidation_rate_s_inv':10.,'oxidation_activation_j_mol':0.});m=quick();m['segments'][0]['gas']='O2';call(h,'method.configure',**m);call(h,'acquisition.start');step(h,.1);self.assertEqual(h.error,'BALANCE_OVERLOAD');self.assertIsNone(h.read()['mass.indicated_kg']);self.ledger(h)
 def test_21_hot_mechanism_and_remove_rejection_atomic(self):
  h=setup();call(h,'thermal.set_temperature',temperature_k=400.,rate_k_s=5.);step(h,25.);call(h,'thermal.disable')
  for k,a in [('furnace.set',{'opened':True}),('sample.remove',{}),('carousel.index',{'position':2}),('punch.set',{'pressed':True}),('emergency.reset',{})]:self.rejected(h,k,'HOT_CELL',**a)
 def test_22_four_joint_host_motion_and_registered_punch(self):
  h=rt.TaTga5500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'power.set',enabled=True);call(h,'carousel.index',position=25);self.assertEqual(h.carousel_angle,0.);step(h,.1);self.assertAlmostEqual(h.carousel_angle,.1);self.rejected(h,'punch.set','MECHANISM_MOVING',pressed=True);step(h,1);self.rejected(h,'punch.set','PUNCH_DEMO_POSITION',pressed=True);call(h,'carousel.index',position=1);step(h,1);call(h,'punch.set',pressed=True);step(h,1);self.assertAlmostEqual(h.punch,.008);step(h,1.1);self.assertEqual(h.punch,.016);self.rejected(h,'carousel.index','NOT_READY',position=2);call(h,'punch.set',pressed=False);step(h,2.1);call(h,'furnace.set',opened=False);step(h,1);self.assertAlmostEqual(h.furnace_lower,.055);step(h,1.1);self.assertEqual(h.furnace_lower,0.)
 def test_23_emergency_stops_mechanisms_and_never_auto_restarts(self):
  h=rt.TaTga5500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'power.set',enabled=True);call(h,'carousel.index',position=10);step(h,.1);call(h,'emergency.stop');pos=h.carousel_angle;step(h,1);self.assertEqual(pos,h.carousel_angle);call(h,'emergency.reset');step(h,1);self.assertEqual(pos,h.carousel_angle)
 def test_24_pan_specific_temperature_limits_and_source_domain(self):
  h=setup(sample={'pan_material':'aluminum'});self.rejected(h,'thermal.set_temperature','PAN_TEMPERATURE_RANGE',temperature_k=874.,rate_k_s=1.)
  h=setup();self.rejected(h,'thermal.set_temperature','REFERENCE_TEMPERATURE_RANGE',temperature_k=1100.,rate_k_s=1.);self.rejected(h,'thermal.set_temperature','PAN_TEMPERATURE_RANGE',temperature_k=290.,rate_k_s=1.);self.assertEqual(rt.PAN_LIMITS['platinum'],1273.15)
 def test_25_rate_limits_source_exact_matching_and_explicit_transfer(self):
  h=setup();self.rejected(h,'method.configure','INVALID_ARGUMENT',**dict(h.method,segments=[dict(target_k=350.,rate_k_s=501/60,hold_s=0.,gas='N2')]))
  h=rt.TaTga5500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,accept_condition_transfer=False));self.rejected(h,'gas.select','CONDITION_TRANSFER',gas='N2');self.rejected(h,'gas.select','CONDITION_TRANSFER',gas='O2');self.rejected(h,'method.configure','CONDITION_TRANSFER',**dict(rt.METHOD_EXAMPLE,segments=[dict(target_k=323.15,rate_k_s=1/6,hold_s=0.,gas='N2')]))

 def test_26_invalid_input_and_array_schema_rejections_atomic(self):
  h=setup()
  for k,a in [('power.set',{'enabled':1}),('sim.step',{'dt_s':float('nan')}),('gas.set_flow',{'sample_flow_m3_s':501e-6/60,'balance_flow_m3_s':1e-8}),('carousel.index',{'position':0}),('method.configure',dict(h.method,sample_interval_s=.15)),('sample.remove',{'extra':True})]:self.rejected(h,k,**a)
 def test_27_external_mass_analysis_signed_blank_and_windows(self):
  h=setup();a=copy.deepcopy(rt.CAPS['analysis.mass_stages']['example_arguments']);r=call(h,'analysis.mass_stages',**a);self.assertAlmostEqual(r['net_loss_fraction'],.3);self.assertAlmostEqual(r['residue_fraction'],.7);self.assertAlmostEqual(r['stages'][0]['peak_loss_rate_kg_s'],2e-6);a['baseline_kg']=[1e-6]*3;a['initial_mass_kg']=2e-5;r=call(h,'analysis.mass_stages',**a);self.assertAlmostEqual(r['residue_fraction'],.3);self.assertAlmostEqual(r['net_loss_fraction'],.15)
 def test_28_analysis_bad_time_missing_values_or_unbracketed_indices(self):
  h=setup();a=copy.deepcopy(rt.CAPS['analysis.mass_stages']['example_arguments']);self.rejected(h,'analysis.mass_stages','ANALYSIS_DATA',**dict(a,time_s=[0.,0.,1.]));self.rejected(h,'analysis.mass_stages','ANALYSIS_BOUNDARIES',**dict(a,stage_boundaries=[1,2]));self.rejected(h,'analysis.mass_stages','INVALID_ARGUMENT',**dict(a,mass_kg=[1e-5,None,1e-6]))
 def test_29_empty_or_unknown_reference_rejected_atomically(self):
  h=rt.TaTga5500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);self.rejected(h,'sample.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id='nonexistent'));self.rejected(h,'sample.load','CURVE_PROJECTION',**dict(rt.SAMPLE_EXAMPLE,accept_curve_projection=False));self.rejected(h,'sample.load','COLD_EXTENSION',**dict(rt.SAMPLE_EXAMPLE,accept_cold_extension=False));rid=next(k for k,v in rt.library()['substances'].items() if not v.get('runtime_usable'));self.rejected(h,'sample.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id=rid))
 def test_30_power_off_blanks_and_sim_step_zero(self):
  h=setup();self.assertTrue(h.read()['panel.mass_text']);call(h,'power.set',enabled=False);self.assertEqual(h.read()['panel.temperature_text'],'');self.assertEqual(h.read()['panel.mass_text'],'');self.assertIsNone(h.read()['mass.indicated_kg']);t=h.time_s;call(h,'sim.step',dt_s=0.);self.assertEqual(h.time_s,t)
 def test_31_temperature_sensor_bias_start_guard_not_truth(self):
  h=setup(plant={'temperature_offset_k':2.});self.rejected(h,'acquisition.start','START_NOT_EQUILIBRATED');call(h,'sensor.calibrate',**dict(h.calibration,temperature_zero_k=2.));self.assertEqual(run(h)['phase'],'succeeded')
 def test_32_owner_no_query_steal_and_agent_truth_isolation(self):
  h=setup();h.register_principal('other');a=h.session();b=h.session('other');e=h.session('evaluator');r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=r['operation_id'];self.assertEqual(r['status'],'accepted');q=b.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='q');self.assertEqual(q['error']['code'],'ACCESS_DENIED')
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):b.poll(op)
  for k in ['ledger.read','sample.remove','plant.configure','sim.step']:self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id=k,arguments=rt.CAPS[k]['example_arguments'],request_id=k)['error']['code'],'ACCESS_DENIED')
  obs=a.read(instance_id=h.instance_id)['observations'];self.assertFalse(any(k.startswith('truth.') for k in obs));self.assertFalse(any(v['visibility']=='evaluator' for v in a.describe(instance_id=h.instance_id)['state_slots'].values()));self.assertTrue(e.read(instance_id=h.instance_id)['observations']['truth.sample_mass_kg'])
 def test_33_idempotent_start_conflict_and_cancel_retry(self):
  h=setup();a=h.session();args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');r=a.invoke(**args);self.assertEqual(r,a.invoke(**args));bad=a.invoke(instance_id=h.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='s');self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT');cancel=a.cancel(r['operation_id'],'c');self.assertEqual(cancel,a.cancel(r['operation_id'],'c'));new=a.invoke(**dict(args,request_id='s2'));self.assertEqual(new['status'],'accepted');self.assertEqual(cancel,a.cancel(r['operation_id'],'c'));self.assertEqual(h.phase,'running')
 def test_34_history_snapshot_survives_reload_calibration_and_new_owner(self):
  h=setup();a=h.session();r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a');step(h,6);old=a.poll(r['operation_id']);cleanup(h);call(h,'power.set',enabled=True);call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,reference_id='fsri_hdpe_n2_10k_r1'));call(h,'sensor.calibrate',**dict(rt.CALIBRATION_EXAMPLE,mass_gain_correction=1.2));self.assertEqual(old,a.poll(r['operation_id']));self.assertEqual(old['result']['reference_id'],'fsri_abs_n2_10k_r1')
 def test_35_partition_invariance_dual_instances_unique_clock(self):
  a=setup('a');b=setup('b');call(a,'acquisition.start');call(b,'acquisition.start');step(a,2.3)
  for _ in range(23):step(b,.1)
  self.assertEqual(a.read(),b.read());step(a,.03);self.assertEqual(a.time_s,b.time_s);step(a,.07);self.assertAlmostEqual(a.time_s-b.time_s,.1);self.assertNotEqual(a.elapsed,b.elapsed);self.ledger(a);self.ledger(b)
 def test_36_thermal_extreme_endotherm_fails_without_negative_kelvin(self):
  h=setup(sample={'loss_heat_j_kg':-1e7,'loss_relaxation_s':1e-6},plant={'pan_capacity_j_k':1e-7},method=copy.deepcopy(rt.METHOD_EXAMPLE));r=run(h);self.assertIn(r['phase'],['succeeded','failed']);self.assertGreater(h.sample_temperature,0);self.assertGreaterEqual(h._mass(),0);self.ledger(h)
 def test_37_tare_uses_observed_empty_pan_only(self):
  h=setup();self.rejected(h,'balance.tare','TARE_REQUIRES_EMPTY');cleanup(h);call(h,'power.set',enabled=True);self.rejected(h,'balance.tare','TARE_REQUIRES_EMPTY');step(h,3);call(h,'balance.tare');self.assertEqual(h.read()['mass.indicated_kg'],0.)
 def test_38_gas_recovery_no_free_resource_and_reference_requirement(self):
  h=setup();step(h,2);cleanup(h);l=self.ledger(h);self.assertEqual(l['n2_remaining_mol'],0);self.assertEqual(l['o2_remaining_mol'],0);self.assertGreater(l['n2_recovered_mol'],0);self.rejected(h,'gas.load','REFERENCE_CONDITIONS',**dict(rt.GAS_EXAMPLE,accept_reference_conditions=False));self.rejected(h,'gas.set_open','NO_GAS',opened=True)
 def test_39_timeout_software_stop_and_unsupported_are_honest(self):
  h=setup(method=quick(timeout_s=.15));r=run(h);self.assertEqual(r['error_code'],'TIMEOUT');self.assertAlmostEqual(r['elapsed_s'],.15);call(h,'emergency.stop');call(h,'emergency.reset')
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
 def test_40_precise_source_upper_domain_and_sampling_grid(self):
  h=setup();self.rejected(h,'method.configure','REFERENCE_TEMPERATURE',**dict(h.method,segments=[dict(target_k=h.model['temperature'][-1]+.01,rate_k_s=1.,hold_s=0.,gas='N2')]));self.rejected(h,'method.configure','SAMPLING_GRID',**dict(h.method,sample_interval_s=.19));self.rejected(h,'sample.load','ALREADY_LOADED',**rt.SAMPLE_EXAMPLE)
 def test_41_successful_actual_apis_state_schema_and_json_independent(self):
  h=setup();run(h);cleanup(h);meta=h.describe();spec=importlib.util.spec_from_file_location('tga_official_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);errors=[]
  for k,c in meta['capabilities'].items():
   errors+=tool.lint_schema(c['input_schema'],path=k)+tool.lint_schema(c['output_schema'],path=k)
  for k,args,result in OBSERVED:errors+=tool.validate_value(args,meta['capabilities'][k]['input_schema'],path=k+'.input')+tool.validate_value(result,meta['capabilities'][k]['output_schema'],path=k+'.output')
  for values in STATES:
   self.assertEqual(set(values),set(rt.SLOTS));json.dumps(values,allow_nan=False)
   for k,v in values.items():errors+=tool.validate_value(v,meta['state_slots'][k]['value_schema'],path=k)
  self.assertEqual(errors,[]);SCHEMA_REPORT['actual_calls']=len(OBSERVED)
 def test_42_agent_protocol_result_serialization_and_cross_instance_rejection(self):
  h=setup();a=h.session();r=a.invoke(instance_id='wrong',capability_id='acquisition.start',arguments={},request_id='a');self.assertEqual(r['error']['code'],'UNKNOWN_INSTANCE');r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='b');step(h,.2);q=a.poll(r['operation_id']);json.dumps(q,allow_nan=False);self.assertFalse(any('truth' in key for row in q['result']['rows'] for key in row));self.assertAlmostEqual(q['result']['rows'][-1]['elapsed_s'],.2);a.cancel(r['operation_id'],'c');self.assertEqual(a.poll(r['operation_id'])['status'],'cancelled')
CLASS_SAMPLE={'flavonoid':'zenodo23013247_quercetin_n2','volatile_terpene':'zenodo23013247_limonene_n2','vitamin_oxidative':'zenodo23013247_ascorbic_acid_o2','phenolic_additive':'zenodo23013247_irganox_1010_n2','alkaloid':'zenodo23013247_caffeine_n2','chlorinated_polymer':'fsri_pvc_n2_10k_r1','wood':'fsri_oak_flooring_n2_10k_r1','mineral_board':'fsri_gypsum_wallboard_gypsum_core_n2_10k_r1','protein_fibre':'fsri_wool_rug_n2_10k_r1'}
class TestTGA5500ExtendedLibrary(unittest.TestCase):
 def test_43_every_runtime_record_is_real_curve_with_provenance_and_50_materials(self):
  lib=rt.library()['substances'];ids=rt.runtime_reference_ids();self.assertGreaterEqual(len(ids),150);self.assertEqual(ids,sorted(k for k,r in lib.items() if r['runtime_usable']))
  groups=set();cas=set();permissive=set()
  for rid in ids:
   r=lib[rid];self.assertEqual(r['kind'],'measured_tga_curve');self.assertEqual(r['evidence'],'measured');self.assertFalse(r['native_instrument']);self.assertEqual(len(r['source']['sha256']),64);self.assertIn('accessed',r['source']);self.assertIn('license',r['source'])
   m=rt.prepare_reference(r,True);self.assertGreaterEqual(len(m['temperature']),3);self.assertEqual(m['mass_fraction'],sorted(m['mass_fraction'],reverse=True));self.assertLess(m['mass_fraction'][-1],.99)
   if r['material_identity']['counted_chemical_identity']:groups.add(r['material_identity']['identity_group'])
   if r.get('cas'):cas.add(r['cas'])
   if r['source']['license']=='CC-BY-4.0':permissive.add(r['material_identity']['identity_group'])
  self.assertGreaterEqual(len(groups),50);self.assertGreaterEqual(len(cas),50);self.assertGreaterEqual(len(permissive),50)
  for rid,r in lib.items():
   if r['kind']!='measured_tga_curve':self.assertFalse(r['runtime_usable'],rid)
 def test_44_material_classes_drive_actual_runs_with_template_source(self):
  finals={}
  for cls,rid in CLASS_SAMPLE.items():
   h=setup(name='tga_'+cls,sample={'reference_id':rid},method=copy.deepcopy(rt.METHOD_EXAMPLE));r=run(h);self.assertEqual(r['phase'],'succeeded',cls);self.assertEqual(r['reference_id'],rid)
   src=json.loads(r['template_source']);ref=rt.library()['substances'][rid];self.assertEqual(src,ref['source']);self.assertIn(src['license'],['CC-BY-4.0','not stated']);self.assertIn('Measured source T/mass curve',r['evidence'])
   proj=json.loads(r['projection_json']);self.assertEqual(proj['original_points'],len(ref['values']['temperature_k']))
   self.assertLess(r['rows'][-1]['mass_kg'],r['rows'][0]['mass_kg']);self.assertLess(h._mass(),rt.SAMPLE_EXAMPLE['mass_kg']);finals[cls]=h._mass();TestTGA5500.ledger(self,h);cleanup(h)
  self.assertGreater(len({round(x,12) for x in finals.values()}),len(finals)-1)
  self.assertLess(finals['volatile_terpene'],.05*rt.SAMPLE_EXAMPLE['mass_kg']);self.assertGreater(finals['mineral_board'],.5*rt.SAMPLE_EXAMPLE['mass_kg'])
 def test_45_unmatched_new_sources_require_explicit_condition_transfer(self):
  for rid in ['zenodo23013247_kaempferol_n2','zenodo23013247_quercetin_o2','fsri_oak_flooring_n2_10k_r1']:
   h=rt.TaTga5500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);ref=rt.library()['substances'][rid];mass=ref['conditions']['initial_mass_kg'] or 1e-5
   call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,reference_id=rid,mass_kg=mass,accept_condition_transfer=False));self.assertEqual(h.sample['reference_id'],rid)
   with self.assertRaisesRegex(rt.ActionRejected,'CONDITION_TRANSFER'):h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,segments=[dict(target_k=323.15,rate_k_s=1/6,hold_s=0.,gas='N2')]))
 def test_46_scalar_records_query_only_and_cache_follows_file(self):
  h=rt.TaTga5500Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE)
  for rid in ['smetana_2025_1a_td','native_spu_ha_td5','calcium_oxalate_manufacturer_8reps']:
   q=call(h,'reference.query',reference_id=rid);self.assertEqual(json.loads(q['template_source'])['source_id'],rt.library()['substances'][rid]['source']['source_id'])
   with self.assertRaisesRegex(rt.ActionRejected,'INVALID_REFERENCE'):h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,reference_id=rid))
  self.assertIs(rt.library(),rt.library())
if __name__=='__main__':unittest.main()
