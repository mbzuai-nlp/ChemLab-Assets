"""FT4 powder-only process, metrology schema and owner/host regression tests."""
import copy,importlib.util,json,math,sys,unittest
from pathlib import Path
from unittest import mock
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import freeman_ft4powder as rt
OBSERVED=[]
def call(h,k,**a):
 r=h.invoke(k,**a);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));return r
def setup(mode='dynamic',instance_id='ft4_test',plant=None,sample=None,method=None,gas=None):
 h=rt.FreemanFt4powderRuntime(instance_id)
 for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,**(plant or {}))),('tool.select',{'tool_type':{'dynamic':'blade','shear':'shear','compression':'piston','permeability':'permeability'}[mode]}),('sample.load',dict(rt.SAMPLE_EXAMPLE,**(sample or {}))),('gas.load',dict(rt.GAS_EXAMPLE,**(gas or {}))),('clamp.set',{'locked':True}),('door.set',{'opened':False}),('power.set',{'enabled':True}),('method.configure',dict(rt.METHOD_EXAMPLE,mode=mode,**(method or {}))),('gas.set_flow',{'flow_m3_s':(method or {}).get('gas_flow_m3_s',rt.METHOD_EXAMPLE['gas_flow_m3_s'])})]:call(h,k,**a)
 if mode=='permeability':call(h,'gas.open',enabled=True)
 return h
def finish(h,seconds=300):
 for _ in range(round(seconds/.1)):
  h.step(.1)
  if h.phase!='running':break
 return call(h,'acquisition.result')
def park(h):
 call(h,'spindle.set_speed',angular_speed_rad_s=0.);call(h,'gas.open',enabled=False);call(h,'axis.move',position_m=0.,speed_m_s=.03);h.step(7.);call(h,'door.set',opened=True);call(h,'clamp.set',locked=False)
class TestFT4Powder(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,h.__dict__)
 def assert_ledger(self,h):
  l=call(h,'ledger.read');self.assertLess(abs(l['powder_error_kg']),1e-14);self.assertLess(abs(l['gas_error_mol']),1e-12);self.assertLess(abs(l['mass_error_kg']),1e-14);self.assertLess(abs(l['energy_error_j']),1e-7)
 def test_01_dynamic_real_scalar_anchor_and_seventh_bfe(self):
  h=setup();call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertLess(r['elapsed_s'],60);self.assertEqual(len(r['points']),14);self.assertEqual(r['basic_flowability_energy_j'],[p for p in r['points'] if p['kind']=='down'][6]['energy_j']);self.assertAlmostEqual(r['specific_energy_j_kg'],r['points'][-1]['energy_j']/h.method['weighed_mass_kg']);self.assertLess(abs(r['basic_flowability_energy_j']/2.107-1),.05);self.assert_ledger(h)
 def test_02_single_downstroke_not_automatically_bfe(self):
  for cond,cycles in [(1,1),(0,7)]:
   h=setup(method={'conditioning_cycles':cond,'measurement_cycles':cycles});h.invoke('acquisition.start');r=finish(h);self.assertIsNone(r['basic_flowability_energy_j']);self.assertIsNotNone(r['specific_energy_j_kg'])
 def test_03_independent_segment_work_integral(self):
  h=setup();h.invoke('acquisition.start');r=finish(h)
  for p in r['points']:
   rows=[x for x in r['rows'] if x['segment']=='measure_'+p['kind'] and x['cycle']==p['cycle']];z=np.array([x['position_m'] for x in rows]);theta=np.array([x['rotation_rad'] for x in rows]);force=np.array([x['force_n'] for x in rows]);torque=np.array([x['torque_nm'] for x in rows]);expected=float(np.sum((force[1:]+force[:-1])/2*np.diff(z)+(torque[1:]+torque[:-1])/2*np.diff(theta)));self.assertAlmostEqual(p['energy_j'],expected,places=12)
  a=rt.CAPS['analysis.energy']['example_arguments'];self.assertAlmostEqual(call(h,'analysis.energy',**a)['energy_j'],.04)
 def test_04_true_seventh_work_matches_scalar_before_sensor_response(self):
  h=setup(sample={'initial_history_factor':1.});h.invoke('acquisition.start')
  while not (h.segment=='measure_down' and h.cycle==6):h.step(.1)
  before=h.mechanical
  while h.segment=='measure_down':h.step(.1)
  self.assertAlmostEqual(h.mechanical-before,2.107,places=7)
 def test_05_filling_history_conditioning_and_mass(self):
  h=setup(sample={'mass_kg':.045,'initial_packing_ratio':.95,'initial_history_factor':2.},method={'measurement_cycles':1});mass=h.sample['mass_kg'];initial=h.packing;h.invoke('acquisition.start');finish(h);self.assertGreater(h.packing,initial);self.assertLess(abs(h.packing-1),1e-3);self.assertLess(h.history,1.001);self.assertEqual(h.sample['mass_kg'],mass);self.assert_ledger(h)
 def test_06_shear_continuum_and_external_yield_fit(self):
  h=setup('shear');call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(r['points']),3);self.assertTrue(all(p['shear_stress_pa']>0 for p in r['points']));a=rt.CAPS['analysis.shear_yield']['example_arguments'];fit=call(h,'analysis.shear_yield',**a);self.assertAlmostEqual(fit['cohesion_pa'],500.,places=7);self.assertAlmostEqual(fit['friction_angle_rad'],math.atan(.5),places=10);self.assertLess(fit['residual_pa'],1e-8);self.assert_ledger(h)
 def test_07_compression_geometry_mass_and_monotonic_density(self):
  h=setup('compression');height=h._height();call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');density=[p['bulk_density_kg_m3'] for p in r['points']];self.assertEqual(density,sorted(density));self.assertLess(h._height(),height);self.assertTrue(all(0<p['compressibility_fraction']<1 for p in r['points']));self.assert_ledger(h)
 def test_08_permeability_darcy_finite_gas_and_external_check(self):
  h=setup('permeability');before=h.gas_stock;call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertLess(h.gas_stock,before);self.assertGreater(h.pressure,0);self.assertTrue(all(p['permeability_m2']>0 for p in r['points']));a=rt.CAPS['analysis.permeability']['example_arguments'];res=call(h,'analysis.permeability',**a);self.assertAlmostEqual(res['permeability_m2'],a['gas_viscosity_pa_s']*a['flow_m3_s']*a['bed_height_m']/a['area_m2']/a['pressure_drop_pa'],places=22);self.assert_ledger(h)
 def test_09_true_setpoints_and_pure_queries(self):
  h=setup();before=copy.deepcopy(h.__dict__);h.describe();h.session().read(instance_id=h.instance_id);h.invoke('acquisition.result');h.invoke('ledger.read');call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);self.assertEqual(before,h.__dict__);call(h,'axis.move',position_m=.03,speed_m_s=.03);call(h,'spindle.set_speed',angular_speed_rad_s=4.);self.assertEqual(h.position,0.);self.assertEqual(h.rotation,0.);self.assertEqual(h.time_s,0.);h.step(.1);self.assertEqual(h.position,.003);self.assertAlmostEqual(h.rotation,.4)
 def test_10_host_partition_independence_and_zero_dt(self):
  a=setup();b=setup();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(7.)
  for _ in range(70):b.step(.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);before=copy.deepcopy(a.__dict__);a.step(0.);self.assertEqual(before,a.__dict__)
 def test_11_two_instances(self):
  a=setup(instance_id='a');b=setup(instance_id='b');before=copy.deepcopy(b.__dict__);a.invoke('acquisition.start');a.step(8.);self.assertEqual(before,b.__dict__)
 def test_12_registered_agent_truth_and_recovery_permissions(self):
  h=setup();s=h.session();d=s.describe(instance_id=h.instance_id);self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']));self.assertNotIn('sample.remove',d['capabilities']);before=copy.deepcopy(h.__dict__)
  for k,a in [('sample.remove',{}),('gas.unload',{}),('ledger.read',{}),('sim.step',{'dt_s':1.})]:
   r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=a,request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.powder_mass_kg'])
  with self.assertRaises(rt.ActionRejected):h.register_principal('agent','evaluator')
 def test_13_operation_owner_queries_poll_and_cancel(self):
  h=setup();h.register_principal('other');a=h.session();b=h.session('other');op=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];before=copy.deepcopy(h.__dict__)
  for k in ['acquisition.result','acquisition.stop']:
   r=b.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaises(rt.ActionRejected):b.poll(op)
  q=a.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='q');self.assertIsNone(q['operation_id']);self.assertEqual(h._owners[op],'agent');self.assertEqual(a.cancel(op,'cancel')['status'],'succeeded')
 def test_14_idempotency_and_request_conflict(self):
  h=setup();s=h.session();a=dict(instance_id=h.instance_id,capability_id='gas.set_flow',arguments={'flow_m3_s':1e-6},request_id='x');r=s.invoke(**a);before=copy.deepcopy(h.__dict__);self.assertEqual(r,s.invoke(**a));self.assertEqual(before,h.__dict__);a['arguments']={'flow_m3_s':2e-6};self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT');self.assertEqual(before,h.__dict__)
 def test_15_history_source_freeze_and_return_copy(self):
  h=setup(method={'measurement_cycles':1});s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];finish(h);saved=s.poll(op);park(h);call(h,'sample.remove');call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,reference_id='retalac_2026'));self.assertEqual(saved,s.poll(op));self.assertEqual(saved['result'],h.invoke('acquisition.result'));saved['result']['rows'][0]['force_n']=999.;self.assertNotEqual(saved,s.poll(op))
 def test_16_cancel_keeps_dissipation_packing_and_frozen_result(self):
  h=setup(sample={'initial_history_factor':2.});h.invoke('acquisition.start');h.step(8.);self.assertGreater(h.mechanical,0.);history=h.history;energy=h.mechanical;call(h,'acquisition.stop');saved=h.invoke('acquisition.result');self.assertEqual(h.history,history);self.assertEqual(h.mechanical,energy);h.step(1.);self.assertEqual(saved,h.invoke('acquisition.result'));self.assertEqual(h.omega,0.);self.assert_ledger(h)
 def test_17_safety_door_stops_immediately_and_close_does_not_resume(self):
  h=setup();h.invoke('acquisition.start');h.step(8.);old=(h.position,h.rotation);call(h,'door.set',opened=True);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'DOOR_INTERLOCK');self.assertEqual(h.omega,0.);saved=h.invoke('acquisition.result');h.step(1.);self.assertEqual(old,(h.position,h.rotation));call(h,'door.set',opened=False);h.step(.2);self.assertEqual(saved,h.invoke('acquisition.result'));self.assertEqual(old,(h.position,h.rotation));call(h,'acquisition.start');self.assertEqual(h.phase,'running')
 def test_18_tiny_finite_gas_terminal_includes_last_tick(self):
  h=setup('permeability',gas={'amount_mol':1e-15});h.invoke('acquisition.start');h.step(.1);r=call(h,'acquisition.result');self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'GAS_EXHAUSTED');self.assertEqual(h.gas_stock,0.);self.assertFalse(h.gas_enabled);self.assertEqual(r['elapsed_s'],.1);self.assertEqual(r['rows'][-1]['time_s'],.1);self.assert_ledger(h)
 def test_19_manufacturer_speed_stroke_force_bounds_atomic(self):
  h=setup()
  for k,a in [('axis.move',{'position_m':.1851,'speed_m_s':.03}),('axis.move',{'position_m':.1,'speed_m_s':.03001}),('spindle.set_speed',{'angular_speed_rad_s':4*math.pi+1e-5})]:self.rejected(h,k,**a)
  self.rejected(h,'method.configure','FORCE_RANGE',**dict(rt.METHOD_EXAMPLE,normal_stresses_pa=[30000.]));call(h,'spindle.set_speed',angular_speed_rad_s=4*math.pi);h.step(.1);self.assertAlmostEqual(h.omega,4*math.pi);call(h,'spindle.set_speed',angular_speed_rad_s=0.)
 def test_20_invalid_engineering_unknown_source_and_overfill(self):
  h=rt.FreemanFt4powderRuntime();self.rejected(h,'plant.configure','MODEL_NOT_ACCEPTED',**dict(rt.PLANT_EXAMPLE,accept_engineering=False));h.invoke('plant.configure',**rt.PLANT_EXAMPLE)
  for a in [dict(accept_engineering=False),dict(reference_id='unknown'),dict(mass_kg=.5),dict(solid_density_kg_m3=100.)]:self.rejected(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,**a))
  lib=copy.deepcopy(rt.library());lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']]['runtime_usable']=False
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','INVALID_REFERENCE',**rt.SAMPLE_EXAMPLE)
 def test_21_condition_scaling_needs_explicit_acceptance(self):
  h=rt.FreemanFt4powderRuntime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,accept_condition_scaling=False));self.rejected(h,'method.configure','CONDITION_SCALING_NOT_ACCEPTED',**rt.METHOD_EXAMPLE)
 def test_22_tool_access_interlocks_and_no_liquid_substitution(self):
  h=setup();self.rejected(h,'tool.select','TOOL_ACCESS',tool_type='shear');self.rejected(h,'clamp.set','SAMPLE_ACCESS',locked=False);self.rejected(h,'service.set','POWER_INTERLOCK',distance_m=.06);h.invoke('door.set',opened=True);h.invoke('tool.select',tool_type='piston');h.invoke('door.set',opened=False);self.rejected(h,'acquisition.start','ATTACHMENT_REQUIRED')
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
 def test_23_force_torque_resolution_and_supplied_zero_offsets(self):
  h=setup();call(h,'sensor.calibrate',force_zero_n=.001,torque_zero_nm=.00001,pressure_zero_pa=2.);h.invoke('acquisition.start');h.step(8.);f,t,_=h._indications();self.assertAlmostEqual(f/1e-4,round(f/1e-4),places=7);self.assertAlmostEqual(t/2e-6,round(t/2e-6),places=7)
 def test_24_force_overload_is_explicit_partial_failure(self):
  h=setup(sample={'initial_history_factor':10.,'conditioning_rate_s':0.});h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'FORCE_OVERLOAD');self.assertLessEqual(abs(h._indications()[0]),50);self.assertGreater(h.mechanical,0.);self.assert_ledger(h)
 def test_25_stiff_compression_limited_by_axial_rate(self):
  h=setup('compression',plant={'compression_tau_s':.001},sample={'compression_modulus_pa':100.},method={'normal_stresses_pa':[20000.],'hold_s':2.});h.invoke('acquisition.start');positions=[]
  while h.phase=='running':positions.append(h.position);h.step(.1)
  self.assertLessEqual(max(abs(b-a) for a,b in zip(positions,positions[1:])),.003+1e-12);self.assertGreater(h._height(),0.);self.assertLess(h._density(),h.sample['solid_density_kg_m3']);self.assert_ledger(h)
 def test_26_pressure_range_guard(self):
  h=setup('permeability',sample={'permeability_m2':1e-16});h.invoke('acquisition.start');h.step(.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'PRESSURE_MODEL_RANGE');self.assertFalse(h.gas_enabled);self.assert_ledger(h)
 def test_27_invalid_numbers_busy_and_dt_atomic(self):
  h=setup()
  for value in [True,float('nan'),float('inf'),-1.]:self.rejected(h,'gas.set_flow',flow_m3_s=value)
  before=copy.deepcopy(h.__dict__)
  for dt in [True,float('nan'),-1.,float('inf')]:
   with self.assertRaises(rt.ActionRejected):h.step(dt)
   self.assertEqual(before,h.__dict__)
  h.invoke('acquisition.start');self.rejected(h,'method.configure','BUSY',**rt.METHOD_EXAMPLE)
 def test_28_powder_gas_recovery_conservation(self):
  h=setup();h.invoke('acquisition.start');finish(h);park(h);call(h,'sample.remove');call(h,'gas.unload');call(h,'power.set',enabled=False);call(h,'service.set',distance_m=.06);self.assertEqual(h.powder_recovered,h.powder_loaded);self.assertEqual(h.gas_stock,0.);self.assert_ledger(h)
 def test_29_environment_bounds_and_no_empirical_claims(self):
  h=rt.FreemanFt4powderRuntime()
  for t in [283.14,313.16]:self.rejected(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,ambient_k=t))
  meta=rt.export_freeman_ft4powder_contract_metadata();self.assertIn('DEM',' '.join(meta['mechanisms']['powder_mechanics']['limitations']));self.assertEqual(meta['capabilities']['noise.empirical']['availability'],'unsupported')
 def test_30_state_effects_cover_actual_changes(self):
  meta=rt.export_freeman_ft4powder_contract_metadata();original=rt.FreemanFt4powderRuntime.invoke
  def audited(h,k,**a):
   before=h.read();r=original(h,k,**a);after=h.read();changed={x for x in before if before[x]!=after[x]};declared={e['state_id'] for e in meta['capabilities'][k]['state_effects'] if e['timing']=='immediate'};self.assertFalse(changed-declared,(k,changed-declared));return r
  with mock.patch.object(rt.FreemanFt4powderRuntime,'invoke',audited):
   h=setup();call(h,'sensor.calibrate',**rt.CAPS['sensor.calibrate']['example_arguments']);call(h,'acquisition.start');call(h,'sim.step',dt_s=8.);call(h,'acquisition.stop');park(h);call(h,'sample.remove');call(h,'gas.unload');call(h,'power.set',enabled=False);call(h,'service.set',distance_m=.06)
 def test_31_source_summary_not_invented_native_trace(self):
  lib=rt.library()['substances'];r=lib['tablettose_70_2026'];self.assertEqual(r['values']['basic_flowability_energy_j'],2.107);self.assertEqual(r['values']['specific_energy_j_kg'],6.41);self.assertEqual(r['conditions']['sample_volume_m3'],85e-6);self.assertIsNone(r['values']['conditioned_bulk_density_kg_m3']);self.assertNotIn('force_n',r['values']);self.assertTrue(r['runtime_usable'])
 def test_31b_stiff_thermal_and_large_host_step(self):
  h=setup(plant={'thermal_capacity_j_k':.01,'heat_loss_w_k':100.});call(h,'acquisition.start');h.step(70.);self.assertEqual(h.phase,'succeeded');self.assertTrue(math.isfinite(h.temperature));self.assertGreaterEqual(h.temperature,h.plant['ambient_k']);self.assertLess(h.temperature,h.plant['ambient_k']+1.);self.assert_ledger(h)
 def test_31c_thermal_domain_excess_explicitly_fails(self):
  h=setup(plant={'thermal_capacity_j_k':.01,'heat_loss_w_k':0.});h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'THERMAL_MODEL_RANGE');self.assertGreater(h.temperature-h.plant['ambient_k'],h.plant['maximum_temperature_rise_k']);self.assertEqual(h.omega,0.);self.assert_ledger(h);self.rejected(h,'acquisition.start','THERMAL_MODEL_RANGE')
 def test_32_all_actual_capability_and_state_schemas(self):
  spec=importlib.util.spec_from_file_location('ft4_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_freeman_ft4powder_contract_metadata();h=setup();call(h,'acquisition.start');call(h,'sim.step',dt_s=60.);call(h,'acquisition.result');call(h,'ledger.read')
  for k,c in rt.CAPS.items():
   if k in rt.UNSUPPORTED:continue
   cases=[v for v in OBSERVED if v[0]==k];self.assertTrue(cases,k)
   for _,a,r in cases:self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema']),[],k);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema']),[],k)
  for k,v in h.read().items():self.assertEqual(tool.validate_value(v,meta['state_slots'][k]['value_schema']),[],k)
if __name__=='__main__':unittest.main()
