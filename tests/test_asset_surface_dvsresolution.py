"""CHEM-123: sourced equilibrium, finite inventories, host protocol and schema regressions."""
import copy,importlib.util,json,math,sys,unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import surface_dvsresolution as rt
OBSERVED=[]
def call(h,k,**a):
 r=h.invoke(k,**a);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));return r
def setup(instance_id='dvs_test',plant=None,sample=None,method=None,water=None,gas=None):
 h=rt.SurfaceDvsresolutionRuntime(instance_id)
 for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,**(plant or {}))),('sample.load',dict(rt.SAMPLE_EXAMPLE,**(sample or {}))),('stock.load',dict(rt.STOCK_EXAMPLE,**(water or {}))),('gas.load',dict(rt.GAS_EXAMPLE,**(gas or {}))),('chamber.set_position',dict(distance_m=0.)),('reservoir.open',dict(distance_m=0.)),('door.set',dict(opened=False)),('power.set',dict(enabled=True)),('method.configure',dict(rt.METHOD_EXAMPLE,**(method or {}))),('gas.open',dict(enabled=True))]:call(h,k,**a)
 return h
def finish(h,seconds=120):
 for _ in range(round(seconds/rt.DT)):
  h.step(rt.DT)
  if h.phase!='running':break
 return call(h,'acquisition.result')
def expose(h):
 call(h,'gas.open',enabled=False);call(h,'door.set',opened=True);call(h,'chamber.set_position',distance_m=.135)
class TestDVSResolution(unittest.TestCase):
 def assert_ledger(self,h):
  l=call(h,'ledger.read');self.assertLess(abs(l['mass_error_kg']),1e-14);self.assertLess(abs(l['water_error_kg']),1e-14);self.assertLess(abs(l['drygas_error_mol']),1e-12);self.assertLess(abs(l['heat_error_j']),1e-7)
 def rejected(self,h,k,error=None,**a):
  before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,error or '.'):
   h.invoke(k,**a)
  self.assertEqual(before,h.__dict__)
 def test_01_measured_bam_equilibrium(self):
  h=setup(sample={'dry_mass_kg':1e-7});call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertTrue(r['all_equilibrated']);self.assertTrue(all(p['dwell_s']>=2 for p in r['points']));self.assertGreater(h.water,0);x,y=rt.curve(h.reference,'adsorption');self.assertAlmostEqual(h.eq_water/h.sample['dry_mass_kg'],float(rt.np.interp(h._rh(),x,y)),delta=2e-5);self.assert_ledger(h)
 def test_02_real_desorption_cycles(self):
  h=setup(sample={'form':'film','dry_mass_kg':1e-7},method={'relative_humidity_steps':[.1,.4,.1],'cycles':2});call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(r['points']),6);self.assertEqual([p['cycle'] for p in r['points']],[0,0,0,1,1,1]);self.assertEqual(h.branch,'desorption');self.assert_ledger(h)
 def test_03_source_temperature_rh_and_usable_atomic(self):
  h=setup();self.rejected(h,'method.configure','REFERENCE_TEMPERATURE',**dict(rt.METHOD_EXAMPLE,temperature_k=298.));self.rejected(h,'method.configure','REFERENCE_RH_RANGE',**dict(rt.METHOD_EXAMPLE,relative_humidity_steps=[.01]));expose(h);h.invoke('sample.remove')
  for flag in [False,None]:
   lib=copy.deepcopy(rt.library());lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']]['runtime_usable']=flag
   with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','REFERENCE_NOT_RUNTIME_USABLE',**rt.SAMPLE_EXAMPLE)
 def test_04_adsorption_only_desorption_needs_explicit_assumption(self):
  h=setup(plant={'ambient_k':298.},sample={'reference_id':'uio66_water_298k'},method={'temperature_k':298.});self.rejected(h,'method.configure','DESORPTION_REFERENCE_REQUIRED',**dict(rt.METHOD_EXAMPLE,temperature_k=298.,relative_humidity_steps=[.4,.1]));expose(h);h.invoke('sample.remove');call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,reference_id='uio66_water_298k',accept_reversible_reference=True));call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,temperature_k=298.,relative_humidity_steps=[.4,.1]));self.assertTrue(h.sample['accept_reversible_reference'])
 def test_05_source_queries_are_pure_and_structured(self):
  h=setup();before=copy.deepcopy(h.__dict__)
  for key in rt.library()['substances']:
   r=call(h,'reference.query',reference_id=key);self.assertEqual(json.loads(r['reference_json']),rt.library()['substances'][key]);self.assertEqual(json.loads(r['evidence']),rt.library()['substances'][key]['evidence'])
  h.session().describe(instance_id=h.instance_id);h.session().read(instance_id=h.instance_id);h.invoke('acquisition.result');h.invoke('ledger.read');self.assertEqual(before,h.__dict__)
 def test_06_setpoints_and_unique_host_clock(self):
  h=setup();before=h.read();call(h,'thermal.set_temperature',temperature_k=303.15);call(h,'humidity.set',relative_humidity=.2);call(h,'vapor.set_pressure',pressure_pa=.3*rt.saturation_pressure(303.15));self.assertEqual(h.temperature,before['truth.temperature_k']);self.assertEqual(h.time_s,0.);self.assertEqual(h._rh(),0.);h.step(.09);self.assertEqual(h.time_s,0.);h.step(.01);self.assertEqual(h.time_s,.1);self.assertGreater(h.temperature,298.15)
 def test_07_partition_independence(self):
  a=setup();b=setup();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(3.)
  for _ in range(30):b.step(.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);self.assert_ledger(a)
 def test_08_two_instances(self):
  a=setup('one');b=setup('two');before=copy.deepcopy(b.__dict__);a.invoke('acquisition.start');a.step(3.);self.assertEqual(before,b.__dict__)
 def test_09_registered_agent_truth_and_recovery(self):
  h=setup();s=h.session();d=s.describe(instance_id=h.instance_id);self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']));self.assertNotIn('stock.unload',d['capabilities']);before=copy.deepcopy(h.__dict__)
  for k,a in [('sample.remove',{}),('stock.unload',{}),('gas.unload',{}),('ledger.read',{}),('sim.step',{'dt_s':1.})]:
   r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=a,request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):s.read(instance_id=h.instance_id,state_ids=['truth.sample_water_kg'])
  with self.assertRaises(rt.ActionRejected):h.register_principal('agent','evaluator')
 def test_10_owners_result_queries_cancel_and_poll(self):
  h=setup();h.register_principal('second');owner=h.session();other=h.session('second');op=owner.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];before=copy.deepcopy(h.__dict__)
  for k in ['acquisition.result','acquisition.stop']:
   q=other.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k);self.assertEqual(q['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):other.poll(op)
  q=owner.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='query');self.assertIsNone(q['operation_id']);self.assertEqual(h._owners[op],'agent');self.assertEqual(owner.cancel(op,'cancel')['status'],'succeeded')
 def test_11_request_retries_conflicts(self):
  h=setup();s=h.session();args=dict(instance_id=h.instance_id,capability_id='gas.set_flow',arguments={'reference_m3_s':50e-6/60},request_id='a');a=s.invoke(**args);before=copy.deepcopy(h.__dict__);self.assertEqual(a,s.invoke(**args));self.assertEqual(before,h.__dict__);args['arguments']={'reference_m3_s':100e-6/60};self.assertEqual(s.invoke(**args)['error']['code'],'REQUEST_CONFLICT');self.assertEqual(before,h.__dict__)
 def test_12_frozen_terminal_result_and_source_survive_new_sample(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];finish(h);saved=s.poll(op);expose(h);call(h,'sample.remove');call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,reference_id='uio66_nh2_water_298k'));h.step(1.);self.assertEqual(saved,s.poll(op));self.assertEqual(saved['result'],h.invoke('acquisition.result'));saved['result']['rows'][0]['mass_kg']=1.;self.assertNotEqual(saved,s.poll(op))
 def test_13_cancel_preserves_partial_transfer_and_history(self):
  h=setup();call(h,'acquisition.start');h.step(3.);self.assertGreater(h.water,0.);water=h.water;call(h,'acquisition.stop');r=h.invoke('acquisition.result');self.assertEqual(h.water,water);self.assertFalse(h.gas_enabled);h.step(3.);self.assertEqual(r,h.invoke('acquisition.result'));self.assert_ledger(h)
 def test_14_tiny_water_inventory_terminal_last_tick(self):
  h=setup(water={'mass_kg':1e-15});call(h,'acquisition.start');h.step(.1);r=call(h,'acquisition.result');self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'WATER_EXHAUSTED');self.assertEqual(r['elapsed_s'],.1);self.assertEqual(r['rows'][-1]['time_s'],.1);self.assertEqual(h.liquid_stock,0.);self.assertFalse(h.gas_enabled);self.assert_ledger(h)
 def test_15_tiny_drygas_inventory_terminal(self):
  h=setup(gas={'amount_mol':1e-12});call(h,'acquisition.start');h.step(.1);r=call(h,'acquisition.result');self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'DRYGAS_EXHAUSTED');self.assertEqual(h.dry_stock,0.);self.assert_ledger(h)
 def test_16_full_window_minimum_dwell_and_honest_timeout(self):
  h=setup(method={'relative_humidity_steps':[.4],'minimum_dwell_s':.1,'maximum_dwell_s':2.,'window_s':2.,'dm_dt_kg_s':1e-5});h.invoke('acquisition.start');h.step(1.9);self.assertEqual(h.phase,'running');self.assertIsNone(h.dm_dt);h.step(.1);r=h.invoke('acquisition.result');self.assertEqual(r['phase'],'succeeded');self.assertFalse(r['all_equilibrated']);self.assertEqual(r['points'][0]['termination'],'maximum_dwell')
 def test_17_stiff_coupled_vapor_sample_exchange(self):
  h=setup(plant={'chamber_volume_m3':1e-8,'water_latent_j_kg':0.},sample={'sorption_tau_s':.001,'desorption_tau_s':.001,'sorption_heat_j_kg':0.,'dry_mass_kg':1e-7},method={'relative_humidity_steps':[.4],'maximum_dwell_s':30.});call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertTrue(r['all_equilibrated']);self.assertGreater(h.water,0.);self.assertLessEqual(h.water,rt.interpolate(h.reference,.4,'adsorption')*1e-7+1e-12);self.assertGreaterEqual(h.cell_water,0.);self.assert_ledger(h)
 def test_18_stiff_thermal_controller_no_euler_instability(self):
  h=setup(plant={'thermal_capacity_j_k':1.,'controller_w_k':1000.,'heater_limit_w':10000.,'cooler_limit_w':10000.,'heat_loss_w_k':100.});h.invoke('gas.open',enabled=False);h.invoke('thermal.set_temperature',temperature_k=358.15);h.step(5.);self.assertTrue(298.15<=h.temperature<=358.15);h.invoke('thermal.set_temperature',temperature_k=278.15);h.step(5.);self.assertTrue(278.15<=h.temperature<=298.15);self.assert_ledger(h)
 def test_19_iapws_temperature_humidity_and_pressure_bounds(self):
  self.assertAlmostEqual(rt.saturation_pressure(298.15),3169.824486313973,places=7);h=setup();h.invoke('gas.open',enabled=False)
  for v in [278.15,358.15]:call(h,'thermal.set_temperature',temperature_k=v)
  self.rejected(h,'humidity.set','UNSUPPORTED_HIGH_TEMPERATURE_HUMIDITY',relative_humidity=.1);h.invoke('thermal.set_temperature',temperature_k=333.15);h.invoke('humidity.set',relative_humidity=.98);self.rejected(h,'thermal.set_temperature','UNSUPPORTED_HIGH_TEMPERATURE_HUMIDITY',temperature_k=333.16);self.rejected(h,'humidity.set',relative_humidity=.981);self.rejected(h,'thermal.set_temperature',temperature_k=358.16);self.rejected(h,'vapor.set_pressure','HUMIDITY_RANGE',pressure_pa=20000.)
 def test_20_unknown_engineering_and_organic_rejection(self):
  h=rt.SurfaceDvsresolutionRuntime();self.rejected(h,'plant.configure','MODEL_NOT_ACCEPTED',**dict(rt.PLANT_EXAMPLE,accept_engineering=False));h.invoke('plant.configure',**rt.PLANT_EXAMPLE);self.rejected(h,'sample.load','MODEL_NOT_ACCEPTED',**dict(rt.SAMPLE_EXAMPLE,accept_engineering_kinetics=False));self.rejected(h,'stock.load','UNSUPPORTED_SOLVENT',solvent='ethanol',mass_kg=.001)
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
 def test_21_mechanical_and_flow_interlocks_atomic(self):
  h=setup();self.rejected(h,'door.set','GAS_ACCESS',opened=True);self.rejected(h,'reservoir.open','GAS_ACCESS',distance_m=.045);self.rejected(h,'sample.remove','SAMPLE_ACCESS');self.rejected(h,'service.set','POWER_INTERLOCK',distance_m=.1);expose(h);call(h,'reference.set_position',distance_m=.135);self.rejected(h,'door.set','CHAMBER_POSITION',opened=False);call(h,'reference.set_position',distance_m=0.);self.rejected(h,'gas.open','CHAMBER_OPEN',enabled=True);call(h,'power.set',enabled=False);call(h,'service.set',distance_m=.1);self.rejected(h,'power.set','NOT_READY',enabled=True);call(h,'service.set',distance_m=0.)
 def test_22_invalid_inputs_busy_and_step_are_atomic(self):
  h=setup()
  for value in [True,float('nan'),float('inf'),-1.]:self.rejected(h,'gas.set_flow',reference_m3_s=value)
  before=copy.deepcopy(h.__dict__)
  for dt in [-.1,True,float('nan'),float('inf')]:
   with self.assertRaises(rt.ActionRejected):h.step(dt)
   self.assertEqual(before,h.__dict__)
  h.step(0.);self.assertEqual(before,h.__dict__);h.invoke('acquisition.start');self.rejected(h,'method.configure','BUSY',**rt.METHOD_EXAMPLE);self.rejected(h,'sample.remove','BUSY')
 def test_23_calibration_tare_resolution(self):
  h=setup(plant={'balance_gain':1.2,'balance_offset_kg':1e-6});c=call(h,'balance.calibrate',reference_masses_kg=[0.,1e-4],observed_masses_kg=[1e-6,121e-6]);self.assertAlmostEqual(c['gain'],1/1.2);self.assertAlmostEqual(c['offset_kg'],-1e-6/1.2);h.step(5.);call(h,'balance.tare');self.assertEqual(h._indicated_mass(),0.);h.step(.1);self.assertAlmostEqual(h._indicated_mass()/1e-11,round(h._indicated_mass()/1e-11),places=8);self.rejected(h,'balance.calibrate','INVALID_CALIBRATION',reference_masses_kg=[0.,1e-4],observed_masses_kg=[1e-4,0.])
 def test_24_balance_capacity_and_change_guard(self):
  h=rt.SurfaceDvsresolutionRuntime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);self.rejected(h,'sample.load','BALANCE_CAPACITY',**dict(rt.SAMPLE_EXAMPLE,dry_mass_kg=1e-3));h=setup(sample={'dry_mass_kg':2e-4});h.invoke('acquisition.start');h.step(.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'BALANCE_CHANGE_RANGE');self.assert_ledger(h)
 def test_25_external_diffusion_recovers_independent_known_coefficient(self):
  h=setup();length=1e-4;diff=2e-10;rate=math.pi**2*diff/(4*length**2);times=[10.,15.,20.,25.,30.,35.,40.];masses=[1e-6*(1-8/math.pi**2*math.exp(-rate*t)) for t in times];before=copy.deepcopy(h.__dict__);r=call(h,'analysis.diffusion',times_s=times,masses_kg=masses,initial_mass_kg=0.,equilibrium_mass_kg=1e-6,half_thickness_m=length,accept_slab_approximation=True);self.assertAlmostEqual(r['diffusivity_m2_s']/diff,1.,places=12);self.assertLess(r['residual'],1e-14);self.assertEqual(before,h.__dict__)
 def test_26_external_hysteresis_common_range(self):
  h=setup();r=call(h,'analysis.hysteresis',adsorption_rh=[0.,.5,1.],adsorption_uptake_kg_kg=[0.,.1,.2],desorption_rh=[.2,.8],desorption_uptake_kg_kg=[.14,.26]);self.assertAlmostEqual(r['signed_area'],.06);self.assertEqual(r['minimum_rh'],.2);self.assertEqual(r['maximum_rh'],.8)
 def test_27_unload_recovery_ledgers(self):
  h=setup();h.invoke('acquisition.start');finish(h);expose(h);call(h,'sample.remove');call(h,'reservoir.open',distance_m=.045);call(h,'stock.unload');call(h,'gas.unload');self.assertEqual(h.water,0.);self.assertEqual(h.liquid_stock,0.);self.assertEqual(h.dry_stock,0.);self.assert_ledger(h)
 def test_28_no_rh_or_temperature_extrapolation(self):
  h=setup();self.assertIsNone(h.eq_water);h.step(.1);self.assertIsNone(h.eq_water);self.assertEqual(h.water,0.);h.invoke('humidity.set',relative_humidity=.4);h.step(3.);self.assertGreater(h.water,0.);h.invoke('gas.open',enabled=False);h.invoke('thermal.set_temperature',temperature_k=303.15);h.step(5.);self.assertIsNone(h.eq_water)
 def test_29_state_effects_cover_actual_changes(self):
  meta=rt.export_surface_dvsresolution_contract_metadata();original=rt.SurfaceDvsresolutionRuntime.invoke
  def audited(h,k,**a):
   before=h.read();r=original(h,k,**a);after=h.read();changed={x for x in before if before[x]!=after[x]};declared={e['state_id'] for e in meta['capabilities'][k]['state_effects'] if e['timing']=='immediate'};self.assertFalse(changed-declared,(k,changed-declared));return r
  with mock.patch.object(rt.SurfaceDvsresolutionRuntime,'invoke',audited):
   h=setup();call(h,'balance.calibrate',**rt.CAPS['balance.calibrate']['example_arguments']);call(h,'balance.tare');call(h,'humidity.set',relative_humidity=.2);call(h,'gas.set_flow',reference_m3_s=50e-6/60);call(h,'acquisition.start');call(h,'sim.step',dt_s=3.);call(h,'acquisition.stop');expose(h);call(h,'sample.remove');call(h,'reference.set_position',distance_m=.135);call(h,'reservoir.open',distance_m=.045);call(h,'stock.unload');call(h,'gas.unload');call(h,'power.set',enabled=False);call(h,'service.set',distance_m=.1)
 def test_30_source_data_retains_real_branches(self):
  lib=rt.library()['substances'];self.assertTrue({'bam_p109_water_nitrogen_298p15k','uio66_water_298k','uio66_nh2_water_298k'}<=set(lib));bam=lib['bam_p109_water_nitrogen_298p15k'];self.assertEqual(bam['conditions']['temperature_k'],298.15);self.assertEqual(len(rt.curve(bam,'adsorption')[0]),19);self.assertEqual(len(rt.curve(bam,'desorption')[0]),19);self.assertNotEqual(rt.curve(bam,'adsorption')[1],rt.curve(bam,'desorption')[1]);self.assertTrue(all(r['runtime_usable'] is True for r in lib.values()))
 def test_30b_high_heat_and_finite_energy_remain_conservative(self):
  h=setup(plant={'thermal_capacity_j_k':1.,'controller_w_k':.1,'heater_limit_w':1.,'cooler_limit_w':1.,'heat_loss_w_k':0.,'chamber_volume_m3':1e-3},sample={'initial_water_kg':1e-4,'sorption_tau_s':.001,'desorption_tau_s':.001,'sorption_heat_j_kg':3e6});h.invoke('gas.set_flow',reference_m3_s=1e-5);h.invoke('acquisition.start');r=finish(h);self.assertIn(r['phase'],['succeeded','failed']);self.assertGreater(h.temperature,273.16);self.assertGreaterEqual(h.cell_water,0.);self.assertGreaterEqual(h.water,0.);self.assert_ledger(h)
 def test_31_all_actual_api_and_state_schema(self):
  spec=importlib.util.spec_from_file_location('dvs_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_surface_dvsresolution_contract_metadata();h=setup();call(h,'acquisition.start');call(h,'sim.step',dt_s=20.);call(h,'acquisition.result');call(h,'ledger.read')
  for k,c in rt.CAPS.items():
   if k in rt.UNSUPPORTED:continue
   cases=[v for v in OBSERVED if v[0]==k];self.assertTrue(cases,k)
   for _,a,r in cases:self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema']),[],k);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema']),[],k)
  for k,v in h.read().items():self.assertEqual(tool.validate_value(v,meta['state_slots'][k]['value_schema']),[],k)
 def test_32_every_runtime_record_loads_at_source_temperature_and_range(self):
  lib=rt.library()['substances'];index=rt.reference_index()
  self.assertEqual({x['reference_id'] for x in index},{k for k,v in lib.items() if v.get('runtime_usable') is True})
  self.assertGreaterEqual(len({x['identity_group'] for x in index}),50);self.assertGreaterEqual(len(index),60)
  for row in index:
   k=row['reference_id'];rec=lib[k];T=rec['conditions']['temperature_k'];lo,hi=row['relative_humidity_range'];hi=min(hi,.98)
   self.assertIn(rec['evidence']['level'],('measured','measured_literature','derived'));self.assertTrue(rec['source'].get('doi') or rec['source'].get('url'),k)
   if rec['evidence']['level']=='derived' and rec['source']['source_id']!='nist_bam_p109_reference':self.assertTrue(rec['evidence'].get('formula'),k)
   h=setup('idx_'+k,plant={'ambient_k':min(T,313.15)},sample={'reference_id':k,'dry_mass_kg':1e-7,'accept_reversible_reference':False},method={'temperature_k':T,'relative_humidity_steps':[lo+(hi-lo)*.25,lo+(hi-lo)*.75]})
   self.assertEqual(h.reference,rec);self.assertEqual(json.loads(h.invoke('reference.query',reference_id=k)['template_source']),rec['source'])
   wrong=T+1. if T+1.<=333.15 else T-1.
   self.rejected(h,'method.configure','REFERENCE_TEMPERATURE',**dict(rt.METHOD_EXAMPLE,temperature_k=wrong,relative_humidity_steps=[(lo+hi)/2]))
   if hi<.97:self.rejected(h,'method.configure','REFERENCE_RH_RANGE',**dict(rt.METHOD_EXAMPLE,temperature_k=T,relative_humidity_steps=[min(.98,hi+.01)]))
   if not row['desorption_branch']:self.rejected(h,'method.configure','DESORPTION_REFERENCE_REQUIRED',**dict(rt.METHOD_EXAMPLE,temperature_k=T,relative_humidity_steps=[lo+(hi-lo)*.75,lo+(hi-lo)*.25]))
 def test_33_new_sources_drive_equilibrium_across_material_classes(self):
  lib=rt.library()['substances']
  for k in ['nist_tppa_1_water_298k','hycool_rdsilicagel_fujidavison_water_293p15k','tu4_nh2_mil_125_water_298k','r3pack_peanut_flips_water_296p15k','borrmann2022_pvp_k25_water_298p15k','borrmann2022_asd_pvp_k25_indomethacin_dl20_water_298p15k','nist_mof_74_ni_water_328k','nist_mg_mof_74_water_298k','tu4_sifsix_3_ni_water_293k']:
   rec=lib[k];T=rec['conditions']['temperature_k'];x=rec['values']['relative_humidity'];lo=max(x[0],.001);hi=min(x[-1],.95);cyc='desorption_relative_humidity' in rec['values']
   if cyc:xx=rec['values']['desorption_relative_humidity'];lo=max(lo,xx[0]);hi=min(hi,xx[-1])
   steps=[lo+(hi-lo)*.2,lo+(hi-lo)*.6]+([lo+(hi-lo)*.2] if cyc else [])
   h=setup('cls_'+k,plant={'ambient_k':min(T,313.15)},sample={'reference_id':k,'dry_mass_kg':1e-7,'sorption_tau_s':.2,'desorption_tau_s':.2},method={'temperature_k':T,'relative_humidity_steps':steps,'maximum_dwell_s':20.})
   call(h,'acquisition.start');r=finish(h,200);self.assertEqual(r['phase'],'succeeded',k);self.assertEqual(r['reference_id'],k);self.assertEqual(len(r['points']),len(steps))
   self.assertEqual(h.branch,'desorption' if cyc else 'adsorption',k)
   xs,ys=rt.curve(h.reference,h.branch);self.assertAlmostEqual(h.eq_water/h.sample['dry_mass_kg'],float(rt.np.interp(h._rh(),xs,ys)),delta=1e-4*max(ys))
   if r['points'][-1]['equilibrated']:self.assertLess(abs(h.water-h.eq_water),.02*max(ys)*h.sample['dry_mass_kg']+1e-12,k)
   self.assert_ledger(h)
 def test_34_reference_only_records_and_licence_flags(self):
  lib=rt.library()['substances'];refs=json.loads((rt.ASSET/'dvs_isotherm_reference_library.json').read_text())['substances'];h=rt.SurfaceDvsresolutionRuntime('refonly');h.invoke('plant.configure',**rt.PLANT_EXAMPLE)
  only=[k for k,v in refs.items() if not v['runtime_counterpart_id']];self.assertGreater(len(only),0)
  for k,v in refs.items():
   self.assertTrue(v['runtime_qualification']['reason']);self.assertNotIn(k,lib)
   if v['runtime_counterpart_id']:self.assertIs(lib[v['runtime_counterpart_id']]['runtime_usable'],True)
  self.rejected(h,'sample.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id=only[0]))
  sa=[k for k,v in lib.items() if v['source'].get('licence')=='CC-BY-SA-4.0'];self.assertTrue(sa)
  for k in sa:self.assertTrue(lib[k]['source']['licence_flag'])
  for k,v in lib.items():
   if v['source']['source_id']=='nist_isodb_water' and not k.startswith('uio'):self.assertIsInstance(v['evidence']['digitized'],bool);self.assertTrue(all(0<=x<=1 for x in v['values']['relative_humidity']))
if __name__=='__main__':unittest.main()
