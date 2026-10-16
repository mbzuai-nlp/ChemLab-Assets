"""CHEM-126 conservative dispersion, timed optical scans, data and ownership regression."""
import copy,hashlib,importlib.util,json,math,sys,unittest
from pathlib import Path
from unittest import mock
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import microtrac_turbiscanlab as rt
OBSERVED=[]
def call(h,k,**a):
 r=h.invoke(k,**a);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));return r
def setup(instance_id='turbiscan_test',plant=None,sample=None,method=None,full=False):
 h=rt.MicrotracTurbiscanlabRuntime(instance_id);m=copy.deepcopy(rt.METHOD_EXAMPLE)
 if not full:m.update(scan_end_m=.0028,scan_interval_s=.2,duration_s=.7)
 m.update(method or {})
 for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,**(plant or {}))),('sample.set_position',{'distance_m':.105}),('lid.set',{'opened':True}),('sample.load',dict(rt.SAMPLE_EXAMPLE,**(sample or {}))),('lid.set',{'opened':False}),('sample.set_position',{'distance_m':0.}),('power.set',{'enabled':True}),('method.configure',m)]:call(h,k,**a)
 return h
def finish(h,seconds=150):
 for _ in range(round(seconds/.1)):
  h.step(.1)
  if h.phase!='running':break
 return call(h,'acquisition.result')
def unload(h):
 call(h,'power.set',enabled=False);call(h,'sample.set_position',distance_m=.105);call(h,'lid.set',opened=True);call(h,'sample.remove')
class TestTurbiscanLab(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,h.__dict__)
 def ledger(self,h):
  r=call(h,'ledger.read');self.assertLess(abs(r['particle_error_kg']),1e-12);self.assertLess(abs(r['liquid_error_kg']),1e-12);self.assertLess(abs(r['particle_volume_error_m3']),1e-15);self.assertLess(abs(r['thermal_error_j']),1e-7);return r
 def test_01_full_source_anchored_timed_scans(self):
  h=setup(full=True);call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(r['profiles']),4);self.assertLess(r['elapsed_s'],rt.METHOD_EXAMPLE['duration_s']);self.assertGreater(r['tsi'],0);self.assertIsNone(r['migration_velocity_m_s']);self.assertIn('migration_front_not_bracketed_in_two_scans',r['analysis_reasons'])
  for p in r['profiles']:
   self.assertEqual(len(p['height_m']),1871);self.assertTrue(np.allclose(np.diff(p['height_m']),20e-6,atol=1e-14));self.assertTrue(np.allclose(np.diff(p['point_time_s']),.002,atol=1e-12));self.assertGreater(p['end_time_s'],p['start_time_s']);self.assertAlmostEqual(p['point_time_s'][0],p['start_time_s']);self.assertAlmostEqual(p['point_time_s'][-1],p['end_time_s']);self.assertTrue(all(0<=x<=1 for x in p['transmission_fraction']+p['backscatter_fraction']))
  self.ledger(h)
 def test_02_raw_reference_unchanged_and_unknown_T_not_zero(self):
  path=rt.ASSET/'turbiscan_library.json';digest=hashlib.sha256(path.read_bytes()).hexdigest();h=setup();record=copy.deepcopy(h.reference);self.assertIsNone(record['values']['transmission_fraction']);self.assertEqual(len(record['values']['height_m']),1253);t,b,_=h._optical_point(.020);expected=float(np.interp(.020,record['values']['height_m'],record['values']['backscatter_fraction'][0]));self.assertAlmostEqual(b,expected,places=12);self.assertGreater(t,0);h.invoke('acquisition.start');r=finish(h);self.assertIn('T=unmeasured; engineering KM output',r['evidence']);self.assertEqual(h.reference,record);self.assertEqual(digest,hashlib.sha256(path.read_bytes()).hexdigest())
 def test_03_stokes_creaming_and_sedimentation_conserve(self):
  for rho,direction in [(920.,1),(1800.,-1)]:
   h=setup(sample={'particle_density_kg_m3':rho,'volume_fraction':.05,'aggregation_rate_s':0.,'accept_template_condition_transfer':True});initial=np.dot(h.phi,h.centers);h.step(30.);self.assertGreater(direction*(np.dot(h.phi,h.centers)-initial),0.);self.assertGreaterEqual(min(h.phi),0);self.assertLessEqual(max(h.phi),h.sample['maximum_packing_fraction']);self.ledger(h)
 def test_04_noflux_packing_limiter_long_step_no_mass_clipping(self):
  h=setup(sample={'volume_fraction':.5,'maximum_packing_fraction':.6,'particle_density_kg_m3':1800.,'particle_diameter_m':2e-4,'dynamic_viscosity_pa_s':.05,'aggregation_rate_s':0.,'extra_diffusion_m2_s':1e-7,'accept_template_condition_transfer':True});h.step(120.);self.assertGreaterEqual(min(h.phi),0.);self.assertLessEqual(max(h.phi),.6+1e-13);self.assertGreater(max(h.phi),.5);self.assertAlmostEqual(sum(h.phi),.5*len(h.phi),places=11);self.ledger(h)
 def test_05_equal_density_stationary_no_random_TSI(self):
  h=setup(sample={'particle_density_kg_m3':1000.,'aggregation_rate_s':0.});h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertAlmostEqual(r['tsi'],0.,places=12);self.assertEqual(h.phi,[.75]*len(h.phi));self.ledger(h)
 def test_06_aggregation_changes_optics_without_creating_mass(self):
  h=setup(sample={'particle_density_kg_m3':1000.,'aggregation_rate_s':.1});before=h._optical_point(.02);mass=h._masses();h.step(10.);self.assertGreater(h.diameter,h.sample['particle_diameter_m']);self.assertNotEqual(before,h._optical_point(.02));self.assertEqual(mass,h._masses());self.ledger(h)
 def test_07_two_flux_limits_and_large_optical_depth(self):
  h=rt.MicrotracTurbiscanlabRuntime()
  for k,s,L in [(0.,0.,.01),(0.,100.,.01),(100.,0.,.01),(1e6,1e6,.1),(1e-14,1e-14,.01)]:
   x=call(h,'analysis.two_flux',absorption_m_inv=k,scattering_m_inv=s,path_m=L);self.assertTrue(all(math.isfinite(v) and 0<=v<=1 for v in x.values()));self.assertAlmostEqual(sum(x.values()),1.,places=12)
   if k==0:self.assertAlmostEqual(x['transmittance'],1/(1+s*L));self.assertAlmostEqual(x['absorptance'],0.,places=12)
   if s==0:self.assertAlmostEqual(x['transmittance'],math.exp(-k*L));self.assertEqual(x['hemispherical_reflectance'],0.)
 def test_08_TSI_formula_fixed_auto_mask_and_no_100_clip(self):
  h=rt.MicrotracTurbiscanlabRuntime();a=dict(transmission_fraction=[[.001,.1],[.003,.2],[.0005,.15]],backscatter_fraction=[[.6,.2],[.5,.4],[.55,.3]],channel='auto_baseline');r=call(h,'analysis.tsi',**a);self.assertTrue(np.allclose(r['tsi_series'],[0,10,15]));self.assertEqual(r['channels'],['backscatter','transmission']);a=dict(transmission_fraction=None,backscatter_fraction=[[0.],[1.],[0.],[1.]],channel='backscatter');self.assertEqual(call(h,'analysis.tsi',**a)['tsi'],300.)
 def test_09_TSI_missing_channel_and_shape_atomic(self):
  h=rt.MicrotracTurbiscanlabRuntime();self.rejected(h,'analysis.tsi','CHANNEL_UNAVAILABLE',transmission_fraction=None,backscatter_fraction=[[0.],[1.]],channel='auto_baseline');self.rejected(h,'analysis.tsi','INVALID_PROFILE_SHAPE',transmission_fraction=[[0.],[1.,2.]],backscatter_fraction=None,channel='transmission')
 def test_10_setpoints_and_queries_do_not_advance_or_teleport(self):
  h=setup(sample={'accept_template_condition_transfer':True});before=copy.deepcopy(h.__dict__);h.describe();h.session().read(instance_id=h.instance_id);h.invoke('acquisition.result');h.invoke('ledger.read');call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);self.assertEqual(before,h.__dict__);call(h,'thermal.set_temperature',temperature_k=300.15);call(h,'scan.move',position_m=.04);self.assertEqual(h.temperature,298.15);self.assertEqual(h.head,0);self.assertEqual(h.time_s,0);h.step(.1);self.assertGreater(h.temperature,298.15);self.assertAlmostEqual(h.head,.002)
 def test_11_stiff_heater_exact_energy_and_no_overshoot(self):
  h=setup(plant={'thermal_capacity_j_k':.1,'heater_gain_w_k':1e5,'heat_loss_w_k':0.,'temperature_sensor_tau_s':.001},sample={'accept_template_condition_transfer':True});call(h,'thermal.set_temperature',temperature_k=333.15);h.step(.1);self.assertGreater(h.temperature,298.15);self.assertLessEqual(h.temperature,333.15);h.step(3.);self.assertAlmostEqual(h.temperature,333.15,places=8);self.assertGreater(h.heater_energy,0);self.ledger(h);self.rejected(h,'sample.set_position','HOT_SAMPLE',distance_m=.105)
 def test_12_passive_cooling_and_no_refrigeration(self):
  h=setup(plant={'thermal_capacity_j_k':1.,'heat_loss_w_k':1.},sample={'accept_template_condition_transfer':True});self.rejected(h,'thermal.set_temperature','NO_ACTIVE_COOLING',temperature_k=298.14);h.invoke('thermal.set_temperature',temperature_k=305.15);h.step(2.);before=h.temperature;call(h,'power.set',enabled=False);h.step(1.);self.assertLess(h.temperature,before);self.assertGreaterEqual(h.temperature,298.15);self.ledger(h)
 def test_13_host_partition_and_zero_step(self):
  a=setup(instance_id='same');b=setup(instance_id='same');a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(.7)
  for _ in range(7):b.step(.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.profiles,b.profiles);before=copy.deepcopy(a.__dict__);a.step(0.);self.assertEqual(before,a.__dict__)
 def test_14_dual_instance_independent(self):
  a=setup(instance_id='a');b=setup(instance_id='b');before=copy.deepcopy(b.__dict__);a.invoke('acquisition.start');a.step(1.);self.assertEqual(before,b.__dict__)
 def test_15_agent_truth_recovery_permissions(self):
  h=setup();s=h.session();meta=s.describe(instance_id=h.instance_id);self.assertFalse(any(k.startswith('truth.') for k in meta['state_slots']));self.assertNotIn('sample.remove',meta['capabilities']);before=copy.deepcopy(h.__dict__)
  for k,a in [('sample.remove',{}),('sample.load',rt.SAMPLE_EXAMPLE),('ledger.read',{}),('sim.step',{'dt_s':1.})]:
   r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=a,request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.particle_mass_kg'])
  with self.assertRaises(rt.ActionRejected):h.register_principal('agent','evaluator')
 def test_16_owner_poll_cancel_result_and_query_no_steal(self):
  h=setup();h.register_principal('other');s=h.session();other=h.session('other');op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];before=copy.deepcopy(h.__dict__)
  for k in ['acquisition.result','acquisition.stop']:
   r=other.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaises(rt.ActionRejected):other.poll(op)
  with self.assertRaises(rt.ActionRejected):other.cancel(op,'cancel')
  self.assertIsNone(s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='q')['operation_id']);self.assertEqual(h._owners[op],'agent');self.assertEqual(s.cancel(op,'stop')['status'],'succeeded')
 def test_17_idempotency_conflict_and_unknown_instance(self):
  h=setup();s=h.session();a=dict(instance_id=h.instance_id,capability_id='scan.move',arguments={'position_m':.01},request_id='id');r=s.invoke(**a);before=copy.deepcopy(h.__dict__);self.assertEqual(r,s.invoke(**a));self.assertEqual(before,h.__dict__);a['arguments']={'position_m':.02};self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT');self.assertEqual(before,h.__dict__);a['instance_id']='other';self.assertEqual(s.invoke(**a)['error']['code'],'UNKNOWN_INSTANCE')
 def test_18_cancel_freezes_partial_but_gravity_heat_continue(self):
  h=setup(full=True,sample={'volume_fraction':.05,'particle_density_kg_m3':1800.,'accept_template_condition_transfer':True});h.invoke('thermal.set_temperature',temperature_k=298.2);h.invoke('acquisition.start');h.step(1.);mass=h._masses();phi=list(h.phi);temperature=h.temperature;head=h.head;call(h,'acquisition.stop');r=call(h,'acquisition.result');self.assertIsNotNone(r['partial_profile']);self.assertGreater(len(r['partial_profile']['height_m']),1);h.step(1.);self.assertEqual(h.head,head);self.assertNotEqual(h.phi,phi);self.assertGreater(h.temperature,temperature);self.assertEqual(r,h.invoke('acquisition.result'));self.assertTrue(np.allclose(mass,h._masses()));self.ledger(h)
 def test_19_history_frozen_after_remove_reload_and_return_copy(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];finish(h);saved=s.poll(op);unload(h);call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,aggregation_rate_s=.1));self.assertEqual(saved,s.poll(op));self.assertEqual(saved['result'],h.invoke('acquisition.result'));saved['result']['profiles'][0]['backscatter_fraction'][0]=99.;self.assertNotEqual(saved,s.poll(op))
 def test_20_geometry_volume_consistency_and_access(self):
  h=setup();self.rejected(h,'lid.set','SAMPLE_ACCESS',opened=True);self.rejected(h,'sample.remove','SAMPLE_ACCESS');unload(h);self.rejected(h,'sample.load','VESSEL_VOLUME_HEIGHT',**dict(rt.SAMPLE_EXAMPLE,height_m=.002));volume=2e-6;call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,volume_m3=volume,height_m=volume/(math.pi*(.0251/2)**2)));self.assertEqual(h.sample['volume_m3'],2e-6);self.rejected(h,'sample.set_position','LID_OPEN',distance_m=0.);self.ledger(h)
 def test_21_source_flags_and_unknown_conditions_atomic(self):
  h=setup();unload(h)
  for extra,error in [(dict(accept_engineering=False),'MODEL_NOT_ACCEPTED'),(dict(accept_unknown_conditions=False),'UNKNOWN_REFERENCE_CONDITIONS'),(dict(reference_id='missing'),'INVALID_REFERENCE'),(dict(accept_concentrated_stokes_proxy=False),'CONCENTRATED_MODEL_NOT_ACCEPTED')]:self.rejected(h,'sample.load',error,**dict(rt.SAMPLE_EXAMPLE,**extra))
  lib=copy.deepcopy(rt.library());lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']]['runtime_usable']=False
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','INVALID_REFERENCE',**rt.SAMPLE_EXAMPLE)
 def test_22_true_reference_temperature_and_wavelength(self):
  h=setup();unload(h);lib=copy.deepcopy(rt.library());r=lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']];r['conditions']['wavelength_m']=600e-9
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','REFERENCE_WAVELENGTH',**rt.SAMPLE_EXAMPLE)
  r['conditions']['wavelength_m']=880e-9;r['conditions']['temperature_k']=308.15
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','REFERENCE_CONDITIONS',**rt.SAMPLE_EXAMPLE)
 def test_23_packing_size_and_stokes_model_domain(self):
  h=setup();unload(h)
  for extra,error in [(dict(maximum_packing_fraction=.6),'PACKING_RANGE'),(dict(particle_diameter_m=.001,particle_density_kg_m3=20000.,dynamic_viscosity_pa_s=.0001),'STOKES_REGIME'),(dict(scattering_m_inv=0.),'OPTICAL_ANCHOR_ZERO')]:self.rejected(h,'sample.load',error,**dict(rt.SAMPLE_EXAMPLE,**extra))
 def test_24_scan_domain_resolution_and_short_interval(self):
  h=setup()
  for extra,error in [(dict(scan_end_m=.055),'SCAN_RANGE'),(dict(scan_start_m=.00198),'SCAN_RANGE'),(dict(scan_end_m=.03942),'SCAN_RANGE'),(dict(scan_end_m=.00281),'SCAN_GRID_ALIGNMENT'),(dict(scan_interval_s=.1),'SCAN_INTERVAL_TOO_SHORT'),(dict(duration_s=.2),'INSUFFICIENT_SCANS'),(dict(tsi_channel='auto_baseline'),'UNMEASURED_TRANSMISSION_SELECTOR')]:self.rejected(h,'method.configure',error,**dict(h.method,**extra))
  self.rejected(h,'scan.move',position_m=.055001);self.rejected(h,'thermal.set_temperature',temperature_k=333.16)
 def test_25_failure_elapsed_and_partial_profile_preserved(self):
  h=setup(full=True,sample={'particle_diameter_m':.00099,'particle_density_kg_m3':1000.,'aggregation_rate_s':1.});h.invoke('acquisition.start');h.step(.1);r=call(h,'acquisition.result');self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'PARTICLE_SIZE_MODEL_RANGE');self.assertAlmostEqual(r['elapsed_s'],.1);self.assertEqual(r['profiles'],[]);self.ledger(h);self.rejected(h,'acquisition.start','PARTICLE_SIZE_MODEL_RANGE')
 def test_26_external_calibration_saturation_count(self):
  h=setup();call(h,'sensor.calibrate',temperature_zero_k=.1,transmission_gain=1.,transmission_zero_fraction=0.,backscatter_gain=100.,backscatter_zero_fraction=0.);self.assertAlmostEqual(h.read()['thermal.indicated_k'],298.05);self.assertEqual(h.temperature,298.15);h.invoke('acquisition.start');r=finish(h);self.assertGreater(sum(x['saturated_points'] for x in r['profiles']),0);self.assertTrue(all(v==1 for p in r['profiles'] for v in p['backscatter_fraction']))
 def test_27_actual_front_timestamp_and_OLS(self):
  h=setup();h.method['front_threshold_fraction']=.05;baseline=dict(height_m=[0.,.001,.002],point_time_s=[0.,1.,2.],backscatter_fraction=[.5,.5,.5]);h.profiles=[baseline];current=dict(height_m=[0.,.001,.002],point_time_s=[10.,11.,12.],backscatter_fraction=[.6,.55,.5]);front=h._front(current);self.assertAlmostEqual(front,.001);self.assertAlmostEqual(float(np.interp(front,current['height_m'],current['point_time_s'])),11.)
 def test_28_invalid_numbers_busy_atomic(self):
  h=setup()
  for x in [True,float('nan'),float('inf'),-1.]:self.rejected(h,'scan.move',position_m=x)
  before=copy.deepcopy(h.__dict__)
  for dt in [True,float('nan'),float('inf'),-1.]:
   with self.assertRaises(rt.ActionRejected):h.step(dt)
   self.assertEqual(before,h.__dict__)
  h.invoke('acquisition.start');self.rejected(h,'sample.set_position','BUSY',distance_m=.105);self.rejected(h,'method.configure','BUSY',**h.method)
 def test_29_cleanup_service_and_truth_ledgers(self):
  h=setup();self.rejected(h,'service.set','POWER_INTERLOCK',distance_m=.06);h.invoke('acquisition.start');finish(h);unload(h);call(h,'service.set',distance_m=.06);self.rejected(h,'power.set','NOT_READY',enabled=True);call(h,'service.set',distance_m=0.);self.assertAlmostEqual(h.particle_loaded,h.particle_recovered);self.assertAlmostEqual(h.liquid_loaded,h.liquid_recovered);self.ledger(h)
 def test_30_all_unsupported_are_honest(self):
  h=setup()
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
  m=h.describe();self.assertEqual(m['capabilities']['particle_size.inverse_mie']['availability'],'unsupported');self.assertIn('20um',json.dumps(m));self.assertEqual(rt.WAVELENGTH,880e-9)
 def test_31_actual_effects_declared(self):
  meta=rt.export_microtrac_turbiscanlab_contract_metadata();original=rt.MicrotracTurbiscanlabRuntime.invoke
  def audited(h,k,**a):
   before=h.read();r=original(h,k,**a);after=h.read();changed={x for x in before if before[x]!=after[x]};declared={e['state_id'] for e in meta['capabilities'][k]['state_effects'] if e['timing']=='immediate'};self.assertFalse(changed-declared,(k,changed-declared));return r
  with mock.patch.object(rt.MicrotracTurbiscanlabRuntime,'invoke',audited):
   h=setup();call(h,'sensor.calibrate',**dict(rt.CAPS['sensor.calibrate']['example_arguments'],temperature_zero_k=.1));call(h,'thermal.set_temperature',temperature_k=298.2);call(h,'scan.move',position_m=.001);call(h,'acquisition.start');call(h,'sim.step',dt_s=.2);call(h,'acquisition.stop');unload(h);call(h,'service.set',distance_m=.06)
 def test_31b_preset_heating_cannot_bypass_reference_temperature(self):
  h=rt.MicrotracTurbiscanlabRuntime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);h.invoke('thermal.set_temperature',temperature_k=300.15);h.invoke('sample.set_position',distance_m=.105);h.invoke('lid.set',opened=True);lib=copy.deepcopy(rt.library());lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']]['conditions']['temperature_k']=298.15
  with mock.patch.object(rt,'library',return_value=lib):h.invoke('sample.load',**rt.SAMPLE_EXAMPLE)
  h.invoke('lid.set',opened=False);h.invoke('sample.set_position',distance_m=0.);h.invoke('power.set',enabled=True);h.step(10.);self.rejected(h,'acquisition.start','REFERENCE_TEMPERATURE')
 def test_31c_source_cell_path_matches_library_schema(self):
  h=setup();unload(h);lib=copy.deepcopy(rt.library());lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']]['conditions']['cell_path_m']=.01
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'sample.load','REFERENCE_CONDITIONS',**rt.SAMPLE_EXAMPLE)
 def test_31d_catalog_lists_and_every_runtime_record_loads(self):
  h=rt.MicrotracTurbiscanlabRuntime('catalog');refs=call(h,'reference.catalog')['references'];lib=rt.library()['substances']
  self.assertEqual({r['reference_id'] for r in refs},set(lib));usable=[r for r in refs if r['runtime_usable']]
  self.assertEqual({r['reference_id'] for r in usable},{k for k,v in lib.items() if v['runtime_usable']});self.assertEqual({r['runtime_mode'] for r in usable},{'optical_anchor','stokes_inputs'})
  self.assertGreaterEqual(len({r['material_identity'] for r in usable}),17);self.assertFalse(any(r['runtime_mode'] for r in refs if not r['runtime_usable']))
  stokes=[r['reference_id'] for r in usable if r['runtime_mode']=='stokes_inputs'];self.assertGreaterEqual(len(stokes),40)
  for rid in stokes:
   rec=lib[rid];T=rec['conditions']['temperature_k'] or 298.15;g=rt.MicrotracTurbiscanlabRuntime('load_'+rid)
   for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,ambient_k=T,safe_access_temperature_k=max(313.15,T))),('sample.set_position',{'distance_m':.105}),('lid.set',{'opened':True}),('sample.load',rt.reference_sample_arguments(rid))]:g.invoke(k,**a)
   self.assertEqual({k:g.sample[k] for k in rt.INPUT_KEYS},rec['values']['physical_inputs'],rid);self.assertIsNone(g.baseline_b);self.assertIsNone(g.baseline_t);g.invoke('sample.remove');self.ledger(g)
 def test_31e_reference_predict_across_material_classes(self):
  h=rt.MicrotracTurbiscanlabRuntime('predict');lib=rt.library()['substances'];before=copy.deepcopy(h.__dict__)
  for rid,sign in [('pone0210690_mct_osa_dissolved_starch',1),('ijms221910591_dolomite_rl0',-1),('foods12010078_cinnamaldehyde_spi',-1),('membranes13040400_tio2_water_0p25wt',-1),('hvi2486635_squalene_tpgs_nea',1),('foods13040513_flaxseed_quillaja_saponin_25wt_5to1',1),('polym15010113_corn_oil_bsa',1)]:
   r=call(h,'reference.predict',reference_id=rid,column_height_m=.04,hindered_exponent=4.65);p=lib[rid]['values']['physical_inputs']
   v0=(p['liquid_density_kg_m3']-p['particle_density_kg_m3'])*9.80665*p['particle_diameter_m']**2/(18*p['dynamic_viscosity_pa_s']);vh=v0*(1-p['volume_fraction'])**4.65
   self.assertEqual(sign,1 if v0>0 else -1,rid);self.assertAlmostEqual(r['stokes_velocity_m_s'],v0,delta=abs(v0)*1e-12);self.assertAlmostEqual(r['hindered_velocity_m_s'],vh,delta=abs(vh)*1e-12);self.assertAlmostEqual(r['clarification_time_s'],.04/abs(vh),delta=1e-9*.04/abs(vh))
   self.assertAlmostEqual(lib[rid]['derived']['stokes_velocity_m_s'],v0,delta=abs(v0)*1e-12);self.assertEqual(r['published_tsi'],lib[rid]['values']['published_tsi']);self.assertEqual(json.loads(r['inputs_json']),p);self.assertIn('not as validation',r['evidence'])
  dol=call(h,'reference.predict',reference_id='ijms221910591_dolomite_rl0',column_height_m=.04,hindered_exponent=4.65);self.assertAlmostEqual(dol['measured_migration_velocity_m_s'][0],-6.66e-3/3600,places=15);self.assertEqual(dol['published_tsi'],[35.4,73.6]);self.assertEqual(dol['published_tsi_time_s'],[3600.,10800.])
  self.assertEqual(before,h.__dict__)
  for rid,err in [('foods11141998_amaranth_alpha_la_r1','REFERENCE_INPUTS_INCOMPLETE'),('lentil_soybean_hipe_untreated','REFERENCE_INPUTS_INCOMPLETE'),('milk_tsi_s1_c24','REFERENCE_INPUTS_INCOMPLETE'),('missing','INVALID_REFERENCE')]:self.rejected(h,'reference.predict',err,reference_id=rid,column_height_m=.04,hindered_exponent=4.65)
  with self.assertRaisesRegex(rt.ActionRejected,'REFERENCE_INPUTS_INCOMPLETE'):rt.reference_sample_arguments('foods12010078_cinnamaldehyde_xgm')
 def test_31f_published_inputs_drive_transport_and_timed_scans(self):
  for rid,direction in [('ijms221910591_dolomite_rl0',-1),('pone0210690_mct_osa_dissolved_starch',1),('polym14112195_sunflower_zein_s1',1),('foods12010078_cinnamaldehyde_spi',0)]:
   rec=rt.library()['substances'][rid];T=rec['conditions']['temperature_k'] or 298.15;a=rt.reference_sample_arguments(rid)
   h=setup('pub',plant={'ambient_k':T,'safe_access_temperature_k':max(313.15,T)},sample=a);initial=float(np.dot(h.phi,h.centers));call(h,'acquisition.start');r=finish(h)
   self.assertEqual(r['phase'],'succeeded');self.assertEqual(r['reference_id'],rid);self.assertIn('Published physical inputs of reference '+rid+' were used unchanged',r['evidence']);self.assertIn('T=unmeasured; engineering KM output, BS=unmeasured; engineering KM output',r['evidence'])
   self.assertTrue(all(0<=x<=1 for p in r['profiles'] for x in p['backscatter_fraction']+p['transmission_fraction']))
   if direction:self.assertGreater(direction*(float(np.dot(h.phi,h.centers))-initial),0,rid)
   self.ledger(h)
   if rid.startswith('polym14112195'):unload(h);self.rejected(h,'sample.load','CONCENTRATED_MODEL_NOT_ACCEPTED',**dict(a,accept_concentrated_stokes_proxy=False))
  h=setup('mismatch');unload(h);a=rt.reference_sample_arguments('ijms221910591_dolomite_rl0',particle_diameter_m=2e-5)
  self.rejected(h,'sample.load','REFERENCE_INPUTS',**a);self.rejected(h,'sample.load','INVALID_REFERENCE',**dict(a,reference_id='foods11141998_amaranth_alpha_la_r1',accept_template_condition_transfer=True))
  call(h,'sample.load',**dict(a,accept_template_condition_transfer=True))
  for k,x in [('lid.set',{'opened':False}),('sample.set_position',{'distance_m':0.}),('power.set',{'enabled':True})]:call(h,k,**x)
  call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertIn('were overridden under accept_template_condition_transfer',r['evidence']);self.ledger(h)
 def test_31g_library_provenance_licences_and_counts(self):
  lib=rt.library();subs=lib['substances'];c=lib['response_coverage'];allowed={'measured','measured_literature','derived','assumption'}
  new=[r for r in subs.values() if r['kind']=='published_dispersion_stability_summary'];self.assertEqual(len(new),c['stability_records'])
  for r in new:
   s=r['source'];self.assertTrue(s['doi'] and s['url'] and s['locator'] and s['accessed']);self.assertEqual(hashlib.sha256((rt.ROOT/s['file']).read_bytes()).hexdigest(),s['sha256'])
   self.assertEqual(r['licence'] not in ('CC BY 4.0','CC BY 3.0','CC BY 2.0','CC0 1.0','CC BY (ACS AuthorChoice CC-BY, version not stated)'),bool(r['licence_flag']),r['substance_id']);self.assertEqual(r['evidence'],'measured_literature');self.assertFalse(r.get('digitized',False))
   if r['runtime_usable']:
    self.assertEqual(r['runtime_mode'],'stokes_inputs');self.assertEqual(set(r['input_provenance'])>=set(rt.INPUT_KEYS),True)
    for k in rt.INPUT_KEYS:self.assertIn(r['input_provenance'][k]['evidence'],allowed,(r['substance_id'],k))
    self.assertEqual(r['derived']['evidence'],'derived');self.assertLessEqual(r['derived']['particle_reynolds_number'],.1);self.assertEqual(sorted(k for k in r['input_provenance'] if r['input_provenance'][k].get('evidence')=='assumption'),r['inputs_assumed'])
   else:self.assertTrue(r['reference_only_reasons'])
  ident=lambda rs:{r['material_identity'] for r in rs}
  self.assertEqual(c['distinct_reference_materials'],len(ident(subs.values())));self.assertEqual(c['runtime_materials'],len(ident(r for r in subs.values() if r['runtime_usable'])))
  self.assertEqual(c['stokes_runtime_materials'],len(ident(r for r in new if r['runtime_usable'])));self.assertEqual(c['minimum_50_runtime_materials_met'],c['runtime_materials']>=50);self.assertEqual(c['digitized_points'],0)
  self.assertEqual(c['stokes_runtime_materials_with_published_stability']+c['stokes_runtime_materials_inputs_only'],c['stokes_runtime_materials'])
 def test_31h_measured_carrier_viscosity_and_hsdb_densities(self):
  subs=rt.library()['substances'];r=subs['fochx2020_limonene_acacia_r2'];p=r['input_provenance']
  self.assertTrue(r['runtime_usable']);self.assertEqual(p['dynamic_viscosity_pa_s']['evidence'],'measured_literature');self.assertAlmostEqual(r['values']['physical_inputs']['dynamic_viscosity_pa_s'],4.1e-3,places=12);self.assertNotIn('dynamic_viscosity_pa_s',r['inputs_assumed']);self.assertIn('liquid_density_kg_m3',r['inputs_assumed'])
  self.assertEqual(r['values']['published_other_stability_metrics'][0]['name'],'creaming_index_24h_percent');self.assertAlmostEqual(r['values']['published_other_stability_metrics'][0]['value'],5.42)
  soy=subs['molecules29215209_soybean_tween80_span80_r1'];self.assertTrue(soy['runtime_usable']);self.assertEqual(soy['input_provenance']['particle_density_kg_m3']['evidence'],'derived');self.assertIn('HSDB',soy['input_provenance']['particle_density_kg_m3']['source']['locator'])
  self.assertFalse(subs['molecules29215209_soybean_chitosan_r5']['runtime_usable']);self.assertTrue(subs['foods14111974_fish_oil_tween20']['runtime_usable']);self.assertAlmostEqual(subs['foods14111974_fish_oil_tween20']['values']['physical_inputs']['particle_density_kg_m3'],920.)
  h=rt.MicrotracTurbiscanlabRuntime('visc');x=call(h,'reference.predict',reference_id='fochx2020_limonene_acacia_r2',column_height_m=.04,hindered_exponent=4.65);q=r['values']['physical_inputs']
  self.assertAlmostEqual(x['stokes_velocity_m_s'],(q['liquid_density_kg_m3']-q['particle_density_kg_m3'])*9.80665*q['particle_diameter_m']**2/(18*4.1e-3),delta=1e-20)
  ref=subs['pone0160140_mct_osa_quinoa_starch_dissolved_r1'];self.assertFalse(ref['runtime_usable']);self.assertIsNone(ref['values']['published_particle_diameter_m']);self.assertAlmostEqual(ref['values']['measured_migration_velocity_m_s'][0],13e-6)
 def test_31i_third_pass_records_across_classes(self):
  """2026-10-15 third pass: new open-access dispersions load, predict and drive timed scans; provenance files verified."""
  lib=rt.library();subs=lib['substances'];c=lib['response_coverage'];allowed={'measured','measured_literature','derived','assumption'}
  third=[r for r in subs.values() if r.get('curation_pass')=='2026-10-15'];run=[r for r in third if r['runtime_usable']]
  earlier={r['material_identity'] for r in subs.values() if r.get('curation_pass')!='2026-10-15'}
  self.assertGreaterEqual(len({r['material_identity'] for r in run}-earlier),45);self.assertGreaterEqual(c['runtime_materials'],50);self.assertTrue(c['minimum_50_runtime_materials_met'])
  h=rt.MicrotracTurbiscanlabRuntime('third');before=copy.deepcopy(h.__dict__);seen=set()
  for r in sorted(run,key=lambda r:r['substance_id']):
   rid=r['substance_id'];p=r['values']['physical_inputs']
   for k in rt.INPUT_KEYS:
    pv=r['input_provenance'][k];self.assertIn(pv['evidence'],allowed,(rid,k))
    src=pv.get('source') if isinstance(pv.get('source'),dict) else pv
    if src.get('file'):self.assertEqual(hashlib.sha256((rt.ROOT/src['file']).read_bytes()).hexdigest(),src['sha256'],(rid,k))
   if r['system_class'] in seen:continue
   seen.add(r['system_class']);x=call(h,'reference.predict',reference_id=rid,column_height_m=.04,hindered_exponent=4.65)
   v0=(p['liquid_density_kg_m3']-p['particle_density_kg_m3'])*9.80665*p['particle_diameter_m']**2/(18*p['dynamic_viscosity_pa_s']);vh=v0*(1-p['volume_fraction'])**4.65
   self.assertAlmostEqual(x['stokes_velocity_m_s'],v0,delta=abs(v0)*1e-12);self.assertAlmostEqual(x['hindered_velocity_m_s'],vh,delta=abs(vh)*1e-12);self.assertLessEqual(x['particle_reynolds_number'],.1)
   self.assertEqual(x['published_tsi'],r['values']['published_tsi']);self.assertEqual(x['measured_migration_velocity_m_s'],r['values']['measured_migration_velocity_m_s']);self.assertEqual(json.loads(x['inputs_json']),p)
  self.assertGreaterEqual(len(seen),3);self.assertEqual(before,h.__dict__)
  for direction in (1,-1):
   cand=[r for r in sorted(run,key=lambda r:r['substance_id']) if r['values']['physical_inputs']['volume_fraction']<=.1 and direction*r['derived']['stokes_velocity_m_s']>1e-7]
   self.assertTrue(cand,direction);rec=cand[0];rid=rec['substance_id'];T=rec['conditions']['temperature_k'] or 298.15;a=rt.reference_sample_arguments(rid)
   g=setup('third_scan',plant={'ambient_k':T,'safe_access_temperature_k':max(313.15,T)},sample=a);initial=float(np.dot(g.phi,g.centers));call(g,'acquisition.start');res=finish(g)
   self.assertEqual(res['phase'],'succeeded');self.assertEqual(res['reference_id'],rid);self.assertIn('Published physical inputs of reference '+rid+' were used unchanged',res['evidence'])
   self.assertGreater(direction*(float(np.dot(g.phi,g.centers))-initial),0,rid);self.ledger(g)
 def test_31j_third_pass_special_inputs(self):
  """Agglomerate effective-density fraction, W/O oil carrier, all-measured brine record and author-model metrics."""
  subs=rt.library()['substances'];c=rt.library()['response_coverage']
  al=subs['pft2014_al2o3_rpmi_fbs'];p=al['values']['physical_inputs'];q=al['input_provenance']['volume_fraction']['inputs']
  phi_s=(1810.-p['liquid_density_kg_m3'])/(3970.-p['liquid_density_kg_m3']);self.assertAlmostEqual(q['phi_s'],phi_s,places=14);self.assertAlmostEqual(p['volume_fraction'],50e-6*1000./(3970.*phi_s),delta=1e-18)
  self.assertEqual(p['particle_density_kg_m3'],1810.);self.assertTrue(all(m['author_model_output'] for m in al['values']['published_other_stability_metrics']))
  self.assertNotIn('suspension|alumina|rpmi_10pct_serum',{r['material_identity'] for r in subs.values() if r['runtime_usable'] and (r['values'].get('published_tsi') or r['values'].get('measured_migration_velocity_m_s') or any(not m.get('author_model_output') for m in r['values'].get('published_other_stability_metrics') or []))})
  self.assertGreater(c['published_author_model_values'],0)
  wo=subs['langmuir2021_wo_water_nacl_0p05M_dodecane_psma_ptfema'];w=wo['values']['physical_inputs']
  self.assertEqual(wo['material_identity'].split('|')[0],'wo');self.assertAlmostEqual(w['volume_fraction'],.1,places=15);self.assertLess(w['liquid_density_kg_m3'],800.);self.assertGreater(w['dynamic_viscosity_pa_s'],1.2e-3)
  self.assertEqual(wo['inputs_assumed'],['dynamic_viscosity_pa_s','liquid_density_kg_m3']);self.assertIn('nist_C112403',wo['input_provenance']['liquid_density_kg_m3']['file'])
  h=rt.MicrotracTurbiscanlabRuntime('wo');x=call(h,'reference.predict',reference_id=wo['substance_id'],column_height_m=.04,hindered_exponent=4.65);self.assertLess(x['stokes_velocity_m_s'],0)
  zn=subs['pone2020_zno500_brine_sdbs_0p1wt'];self.assertEqual(zn['inputs_assumed'],[]);self.assertEqual(zn['input_provenance']['dynamic_viscosity_pa_s']['evidence'],'measured_literature');self.assertAlmostEqual(zn['values']['physical_inputs']['dynamic_viscosity_pa_s'],1.02e-3,places=12)
  for k in [k for k in subs if k.startswith('langmuir2018_')]:self.assertGreater(call(h,'reference.predict',reference_id=k,column_height_m=.04,hindered_exponent=4.65)['stokes_velocity_m_s'],0,k)
 def test_32_actual_api_state_schemas(self):
  spec=importlib.util.spec_from_file_location('turbiscan_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_microtrac_turbiscanlab_contract_metadata();h=setup();call(h,'acquisition.start');call(h,'sim.step',dt_s=.7);call(h,'acquisition.result');self.ledger(h)
  for k,c in rt.CAPS.items():
   if k in rt.UNSUPPORTED:continue
   cases=[v for v in OBSERVED if v[0]==k];self.assertTrue(cases,k)
   for _,a,r in cases:self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema']),[],k);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema']),[],k)
  for k,v in h.read().items():self.assertEqual(tool.validate_value(v,meta['state_slots'][k]['value_schema']),[],k)
if __name__=='__main__':unittest.main()
