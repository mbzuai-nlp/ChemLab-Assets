"""CHEM127 finite real-source Langmuir/Wilhelmy/deposition protocol regressions."""
import copy,hashlib,importlib.util,json,math,sys,unittest
from pathlib import Path
from unittest import mock
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import biolin_mediuml as rt

def _need(p):
    """Raise FileNotFoundError for a missing build record or source cache (the public release skips such tests)."""
    if not Path(p).exists():
        raise FileNotFoundError(2, "not present in this tree", str(p))

OBSERVED=[]
def call(h,k,**a):
 r=h.invoke(k,**a);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(r)));return r
def setup(name='mediuml_test',plant=None,sample=None,substrate=False,preimmerse=False):
 h=rt.BiolinMediumlRuntime(name)
 for k,a in [('plant.configure',dict(rt.PLANT_EXAMPLE,**(plant or {}))),('subphase.load',rt.SUBPHASE_EXAMPLE),('power.set',{'enabled':True})]:call(h,k,**a)
 if substrate or preimmerse:
  call(h,'substrate.load',**rt.SUBSTRATE_EXAMPLE);call(h,'clamp.set',locked=True)
 if preimmerse:call(h,'dipper.move',position_m=.119,speed_m_s=.0018);h.step(66.2)
 call(h,'balance.tare');call(h,'solution.load',**dict(rt.SAMPLE_EXAMPLE,**(sample or {})));call(h,'film.spread',volume_m3=50e-9);h.step(4.)
 return h
def finish(h,limit=300):
 for _ in range(round(limit/.1)):
  h.step(.1)
  if h.phase!='running':break
 return call(h,'acquisition.result')
def compress(h):
 call(h,'method.configure',**rt.METHOD_EXAMPLE);call(h,'acquisition.start');return finish(h)
def deposition_ready(plant=None):
 h=setup(plant=plant,preimmerse=True);m=dict(rt.METHOD_EXAMPLE,mode='hold',duration_s=100.)
 call(h,'method.configure',**m);call(h,'acquisition.start');finish(h)
 m.update(mode='deposition',duration_s=45.,dipper_positions_m=[.063]);call(h,'method.configure',**m);return h

def cleanup(h):
 if h.phase=='running':call(h,'acquisition.stop')
 if h.control:call(h,'pressure.control',enabled=False,target_n_m=.02,speed_m_s=.0045)
 if h.substrate:
  call(h,'dipper.move',position_m=0.,speed_m_s=.0018);h.step(70.);call(h,'clamp.set',locked=False);call(h,'substrate.remove')
 call(h,'film.clean');call(h,'solution.remove');call(h,'probe.move',distance_m=.04);call(h,'subphase.remove');call(h,'power.set',enabled=False)

class TestMediumL(unittest.TestCase):
 def rejected(self,h,k,error='.',**a):
  before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(k,**a)
  self.assertEqual(before,h.__dict__)
 def ledger(self,h):
  x=call(h,'ledger.read')
  for k in ['solute_error_mol','solute_error_kg','solvent_error_m3']:self.assertLess(abs(x[k]),1e-18,k)
  self.assertLess(abs(x['subphase_error_kg']),1e-12);self.assertLess(abs(x['thermal_error_j']),1e-10);return x
 def test_01_real_source_compression_and_same_number_area(self):
  h=setup();n=h.surface_mol;r=compress(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(len(r['rows']),169);self.assertAlmostEqual(r['elapsed_s'],16.8);self.assertAlmostEqual(h.area,.016);self.assertEqual(h.surface_mol,n);a,p=rt.curve(h.reference);self.assertAlmostEqual(h._eos()[0],np.interp(h.area/n/rt.NA,a,p));self.assertGreater(r['rows'][-1]['surface_pressure_n_m'],r['rows'][0]['surface_pressure_n_m']);self.ledger(h)
 def test_02_three_real_references_preserved_and_distinct(self):
  lib=rt.library();ids=['statins2020_dmpc_control','statins2020_dmpe_control','statins2020_dmps_control'];pressures=[]
  for rid in ids:
   rec=copy.deepcopy(lib['substances'][rid]);h=setup(sample={'reference_id':rid});compress(h);pressures.append(h._eos()[0]);self.assertEqual(h.reference,rec);self.assertGreater(len(rec['values']['molecular_area_m2']),600);self.assertEqual(rec['conditions']['temperature_k'],294.15);self.assertEqual(h.source,json.dumps(rec['source'],ensure_ascii=False));self.ledger(h)
  self.assertEqual(len(set(round(p,8) for p in pressures)),3);self.assertGreaterEqual(len(lib['substances']),46);self.assertTrue(all(lib['substances'][rid]['runtime_usable'] for rid in ids));self.assertEqual(sum(v.get('runtime_usable') is True for v in lib['substances'].values()),lib['response_coverage']['runtime_records'])
 def test_03_negative_noise_and_local_derivative_not_clipped(self):
  rec=rt.library()['substances']['statins2020_dmpc_control'];a,p=rt.curve(rec);self.assertLess(min(p),0);found=False
  for j in range(len(a)-1):
   x=(a[j]+a[j+1])/2
   if x<rec['runtime_domain']['minimum_molecular_area_m2']:continue
   q,c=rt.eos(rec,x);self.assertAlmostEqual(q,(p[j]+p[j+1])/2,places=12);self.assertAlmostEqual(c,-x*(p[j+1]-p[j])/(a[j+1]-a[j]),places=10)
   if c<0:found=True
  self.assertTrue(found)
 def test_04_collapse_and_extrapolation_domains_atomic(self):
  h=setup();lower=h.reference['runtime_domain']['minimum_molecular_area_m2'];self.rejected(h,'barrier.move','EOS_AREA_RANGE',area_m2=lower*h.surface_mol*rt.NA*.99,speed_m_s=.0045)
  for a in [lower*.99,max(h.reference['values']['molecular_area_m2'])*1.01]:
   with self.assertRaises(rt.ActionRejected):rt.eos(h.reference,a)
  self.assertEqual(rt.eos(h.reference,max(h.reference['values']['molecular_area_m2'])*1.01,True),(0.,0.))
 def test_05_evaporation_finite_latent_and_not_instant(self):
  h=rt.BiolinMediumlRuntime()
  for k,a in [('plant.configure',rt.PLANT_EXAMPLE),('subphase.load',rt.SUBPHASE_EXAMPLE),('power.set',{'enabled':True}),('balance.tare',{}),('solution.load',rt.SAMPLE_EXAMPLE),('film.spread',{'volume_m3':50e-9})]:call(h,k,**a)
  self.assertEqual(h.surface_solvent,50e-9);self.rejected(h,'acquisition.start','SOLVENT_NOT_EVAPORATED');h.step(1.);self.assertAlmostEqual(h.surface_solvent,50e-9*math.exp(-2.));self.assertAlmostEqual(h.latent_energy,(50e-9-h.surface_solvent)*1480*250000);h.step(3.);compress(h);self.ledger(h)
 def test_06_noisy_wilhelmy_has_wetting_bias(self):
  h=setup(plant={'plate_contact_cosine':.7,'force_gain':1.2});p=h._eos()[0];self.assertAlmostEqual(h._observed(),p*.7*1.2,places=9);self.assertNotAlmostEqual(h._observed(),p,places=5);self.rejected(h,'balance.tare','CLEAN_BASELINE_REQUIRED');self.ledger(h)
 def test_07_buoyancy_level_and_fixed_tare_observation(self):
  h=setup(substrate=True);start=h._observed();n=h.surface_mol;call(h,'dipper.move',position_m=.119,speed_m_s=.0018);h.step(66.2);self.assertEqual(h.surface_mol,n);rise=.052*.003*.056/rt.AREA_MAX;self.assertAlmostEqual(h._liquid_rise(),rise);self.assertAlmostEqual(h.read()['subphase.level_delta_m'],rise);self.assertGreater(h._observed(),start);self.ledger(h)
 def test_08_full_hydrophilic_first_upstroke_actual_transfer_ratio(self):
  h=deposition_ready();before=h.surface_mol;call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(r['completed_strokes'],1);s=r['strokes'][0];self.assertEqual(s['direction'],'up');self.assertAlmostEqual(s['swept_area_m2'],2*.052*.056,places=12);self.assertAlmostEqual(s['transfer_ratio'],(s['start_area_m2']-s['end_area_m2'])/s['swept_area_m2']);self.assertGreater(h.deposited_mol,0);self.assertAlmostEqual(before-h.surface_mol,h.deposited_mol,places=18);self.assertNotEqual(s['transfer_ratio'],h.substrate['transfer_efficiency']);self.assertTrue(h.first_favorable_transferred);self.ledger(h)
 def test_09_cancel_keeps_partial_layer_inventory_and_history(self):
  h=deposition_ready();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];h.step(3.);s.cancel(op,'cancel');r=s.poll(op);self.assertEqual(r['result']['completed_strokes'],0);self.assertFalse(r['result']['strokes'][0]['completed']);self.assertGreater(h.deposited_mol,0);before=h.deposited_mol;z=h.dipper;h.step(1.);self.assertEqual(h.dipper,z);self.assertEqual(h.deposited_mol,before);self.assertEqual(s.poll(op),r);self.ledger(h)
 def test_10_blank_correction_uses_input_not_hidden_true_leak(self):
  h=deposition_ready();m=dict(h.method,blank_leak_area_rate_m2_s=1e-6);call(h,'method.configure',**m);call(h,'acquisition.start');r=finish(h);s=r['strokes'][0];expected=s['transfer_ratio']-1e-6*(s['end_time_s']-s['start_time_s'])/s['swept_area_m2'];self.assertAlmostEqual(s['blank_corrected_transfer_ratio'],expected);self.assertEqual(h.leaked_mol,0)
 def test_11_first_favorable_wetting_direction(self):
  h=setup(substrate=True);call(h,'dipper.move',position_m=.119,speed_m_s=.0018);h.step(66.2);self.assertEqual(h.deposited_mol,0)
  call(h,'dipper.move',position_m=.063,speed_m_s=.0018);h.step(31.2);self.assertGreater(h.deposited_mol,0);first=h.deposited_mol;call(h,'dipper.move',position_m=.119,speed_m_s=.0018);h.step(31.2);call(h,'dipper.move',position_m=.063,speed_m_s=.0018);h.step(31.2);self.assertEqual(h.deposited_mol,first);self.ledger(h)
 def test_12_leakage_stiff_finite_domain_failure_preserved(self):
  h=setup(plant={'leak_rate_s':.001});before=h.surface_mol;h.step(10);self.assertAlmostEqual(h.surface_mol,before*math.exp(-.01),places=18);self.assertGreater(h.leaked_mol,0);self.ledger(h)
  h=setup(plant={'leak_rate_s':100.});self.assertGreater(h.leaked_mol,0);self.assertGreaterEqual(h.surface_mol,0);self.rejected(h,'acquisition.start','EOS_AREA_RANGE');self.ledger(h)
 def test_13_true_geometry_speeds_positions_and_no_teleport(self):
  h=setup(substrate=True);start=h.read();call(h,'barrier.move',area_m2=.020,speed_m_s=.0045);self.assertEqual(h.area,rt.AREA_MAX);h.step(.1);self.assertAlmostEqual(h.read()['barrier.inward_m'],.00045);self.assertAlmostEqual(h.area,rt.AREA_MAX-.0000675)
  h.step(20.);call(h,'dipper.move',position_m=.119,speed_m_s=.0018);self.assertEqual(h.dipper,0);h.step(.1);self.assertAlmostEqual(h.dipper,.00018);self.assertEqual(start['clock.time_s'],4.);self.ledger(h)
 def test_14_hardware_extremes_and_negative_gamma_atomic(self):
  h=setup()
  for k,a in [('barrier.move',{'area_m2':rt.AREA_MIN-1e-9,'speed_m_s':.0045}),('barrier.move',{'area_m2':.02,'speed_m_s':.0045001}),('dipper.move',{'position_m':.125001,'speed_m_s':.0018}),('pressure.control',{'enabled':True,'target_n_m':.3,'speed_m_s':.0045})]:self.rejected(h,k,**a)
  self.rejected(h,'plant.configure','INITIALIZATION_ONLY',**rt.PLANT_EXAMPLE)
 def test_15_host_time_partition_and_two_instances(self):
  a=setup(name='same');b=setup(name='same');a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(5.)
  for _ in range(50):b.step(.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows);before=copy.deepcopy(b.__dict__);a.step(1.);self.assertEqual(before,b.__dict__);before=copy.deepcopy(a.__dict__);a.step(0.);self.assertEqual(before,a.__dict__)
 def test_16_queries_are_pure_no_owner_stealing(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];before=copy.deepcopy(h.__dict__);h.read();h.describe();s.read(instance_id=h.instance_id);h.invoke('ledger.read');h.invoke('acquisition.result');call(h,'reference.query',reference_id=rt.SAMPLE_EXAMPLE['reference_id']);self.assertEqual(before,h.__dict__);q=s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='query');self.assertIsNone(q['operation_id']);self.assertEqual(h._owners[op],'agent')
 def test_17_truth_permissions_and_no_load_recovery_leak(self):
  h=setup();s=h.session();meta=s.describe(instance_id=h.instance_id);self.assertFalse(any(k.startswith('truth.') for k in meta['state_slots']))
  for k,a in [('solution.load',rt.SAMPLE_EXAMPLE),('solution.remove',{}),('substrate.remove',{}),('subphase.remove',{}),('film.clean',{}),('ledger.read',{}),('sim.step',{'dt_s':1.})]:
   before=copy.deepcopy(h.__dict__);r=s.invoke(instance_id=h.instance_id,capability_id=k,arguments=a,request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.deposited_mol'])
 def test_18_owner_result_poll_cancel_and_idempotency(self):
  h=setup();h.register_principal('other');s=h.session();other=h.session('other');args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**args);self.assertEqual(r,s.invoke(**args));op=r['operation_id']
  for k in ['acquisition.result','acquisition.stop']:self.assertEqual(other.invoke(instance_id=h.instance_id,capability_id=k,arguments={},request_id=k)['error']['code'],'ACCESS_DENIED')
  with self.assertRaises(rt.ActionRejected):other.poll(op)
  with self.assertRaises(rt.ActionRejected):other.cancel(op,'bad')
  self.assertEqual(s.cancel(op,'stop')['status'],'succeeded');self.assertEqual(s.poll(op)['status'],'cancelled')
  self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='start')['error']['code'],'REQUEST_CONFLICT')
 def test_19_frozen_result_after_method_and_source_changes(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];finish(h);saved=s.poll(op);cleanup(h);call(h,'subphase.load',**rt.SUBPHASE_EXAMPLE);self.assertEqual(saved,s.poll(op));self.assertEqual(saved['result'],h.invoke('acquisition.result'));saved['result']['rows'][0]['area_m2']=999;self.assertNotEqual(saved,s.poll(op))
 def test_20_conditions_and_runtime_reference_only_guards(self):
  h=setup();cleanup(h);call(h,'subphase.load',**dict(rt.SUBPHASE_EXAMPLE,ph=5.));self.rejected(h,'solution.load','REFERENCE_CONDITIONS',**dict(rt.SAMPLE_EXAMPLE,accept_condition_transfer=False));lib=rt.library();ref=next(k for k,v in lib['substances'].items() if not v.get('runtime_usable'));self.rejected(h,'solution.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id=ref))
  lib=copy.deepcopy(lib);lib['substances'][rt.SAMPLE_EXAMPLE['reference_id']]['conditions']['temperature_k']=None
  with mock.patch.object(rt,'library',return_value=lib):self.rejected(h,'solution.load','UNKNOWN_REFERENCE_CONDITIONS',**dict(rt.SAMPLE_EXAMPLE,accept_unknown_conditions=False))
 def test_21_small_stock_excess_rejected_without_tolerance(self):
  h=setup();self.rejected(h,'film.spread','INSUFFICIENT_SOLUTION',volume_m3=h.stock_volume+1e-20);self.ledger(h)
 def test_22_calibration_probe_and_clean_tare_guard(self):
  h=setup();call(h,'balance.calibrate',force_gain_correction=1.2,force_zero_n=1e-5,assumed_contact_cosine=.8);self.assertFalse(h.tared);self.assertIsNone(h._observed());call(h,'probe.move',distance_m=.04);self.assertIsNone(h._observed());self.rejected(h,'balance.tare','CLEAN_BASELINE_REQUIRED');call(h,'film.clean');call(h,'probe.move',distance_m=0.);call(h,'balance.tare');self.assertEqual(h._observed(),0);self.ledger(h)
 def test_23_expansion_requires_explicit_branch_reuse(self):
  h=setup(sample={'accept_quasistatic_branch_reuse':False});compress(h);self.rejected(h,'barrier.move','BRANCH_REUSE_NOT_ACCEPTED',area_m2=.02,speed_m_s=.0045);m=dict(rt.METHOD_EXAMPLE,mode='expansion',target_area_m2=.02);call(h,'method.configure',**m);self.rejected(h,'acquisition.start','BRANCH_REUSE_NOT_ACCEPTED')
 def test_24_external_modulus_input_and_apparent_label(self):
  h=setup();a=dict(area_m2=[.01,.02,.03],surface_pressure_n_m=[.03,.02,.01],constant_molecule_number=True);r=call(h,'analysis.isotherm',**a);self.assertTrue(np.allclose(r['modulus_n_m'],[.01,.02,.03]));a['constant_molecule_number']=False;self.assertIn('apparent',call(h,'analysis.isotherm',**a)['interpretation']);a['area_m2']=[.01,.01,.03];self.rejected(h,'analysis.isotherm','INVALID_ARRAYS',**a)
 def test_25_invalid_input_time_rejection_atomic(self):
  h=setup()
  for value in [True,float('nan'),float('inf'),-1.]:
   self.rejected(h,'sim.step',dt_s=value);before=copy.deepcopy(h.__dict__)
   with self.assertRaises(rt.ActionRejected):h.step(value)
   self.assertEqual(before,h.__dict__)
  call(h,'acquisition.start');self.rejected(h,'method.configure','BUSY',**rt.METHOD_EXAMPLE);self.rejected(h,'power.set','BUSY',enabled=False)
 def test_26_timeout_preserves_partial_and_no_overrun(self):
  h=setup();call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,duration_s=.5));call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'METHOD_TIMEOUT');self.assertAlmostEqual(r['elapsed_s'],.5);self.assertEqual(r['rows'][-1]['time_s'],h.time_s);self.assertLess(h.area,rt.AREA_MAX);self.assertGreater(h.area,.016);saved=copy.deepcopy(r);h.step(1.);self.assertEqual(h.invoke('acquisition.result'),saved);self.ledger(h)
 def test_27_pressure_control_is_observed_and_stops(self):
  h=setup();call(h,'pressure.control',enabled=True,target_n_m=.02,speed_m_s=.0045);self.assertEqual(h.area,rt.AREA_MAX);h.step(60.);self.assertLess(abs(h._observed()-.02),.0005);call(h,'pressure.control',enabled=False,target_n_m=.02,speed_m_s=.0045);a=h.area;h.step(1.);self.assertEqual(h.area,a);self.ledger(h)
 def test_28_geometry_access_and_hot_state_guards(self):
  h=setup(substrate=True);self.rejected(h,'substrate.remove','SUBSTRATE_ACCESS');call(h,'dipper.move',position_m=.01,speed_m_s=.0018);self.rejected(h,'clamp.set','MOTION_ACTIVE',locked=False);h.step(6.);self.rejected(h,'clamp.set','SUBSTRATE_ACCESS',locked=False);self.rejected(h,'subphase.remove','SUBPHASE_ACCESS')
  hot=rt.BiolinMediumlRuntime();call(hot,'plant.configure',**dict(rt.PLANT_EXAMPLE,temperature_k=323.15));self.rejected(hot,'subphase.load','HOT_SUBPHASE',**rt.SUBPHASE_EXAMPLE)
 def test_29_complete_cleanup_solute_solvent_subphase_recovered(self):
  h=deposition_ready();call(h,'acquisition.start');finish(h);cleanup(h);r=self.ledger(h);self.assertEqual(r['solute_present_kg'],0);self.assertAlmostEqual(r['solute_loaded_mol'],r['solute_recovered_mol'],places=18);self.assertEqual(r['subphase_present_kg'],0);self.assertAlmostEqual(r['subphase_loaded_kg'],r['subphase_recovered_kg']);self.assertAlmostEqual(r['solvent_loaded_m3'],r['solvent_recovered_m3']+r['solvent_evaporated_m3'],places=18)
 def test_30_LCD_blank_and_real_units(self):
  h=setup();d=h.read();self.assertEqual(d['panel.area_text'],'273.0');self.assertEqual(d['panel.pressure_text'],f'{d["pressure.indicated_n_m"]*1000:.1f}');call(h,'power.set',enabled=False);self.assertEqual(h.read()['panel.area_text'],'');self.assertEqual(h.read()['panel.pressure_text'],'')
 def test_31_unsupported_and_actual_methods_schemas(self):
  h=setup()
  for k in rt.UNSUPPORTED:self.rejected(h,k,'UNSUPPORTED')
  meta=h.describe();self.assertEqual(meta['capabilities']['film.collapse_prediction']['availability'],'unsupported');self.assertEqual(rt.PLANT['properties']['controller_gain_m3_n_s']['x-unit'],'m^3/(N*s)')
 def test_32_wet_probe_includes_liquid_rise(self):
  h=setup(preimmerse=True);h.probe_lift=h.plant['plate_immersion_m']+h._liquid_rise()/2
  self.assertIsNotNone(h._observed());h.probe_lift=h.plant['plate_immersion_m']+h._liquid_rise();self.assertIsNone(h._observed())
 def test_33_balance_physical_load_limit_atomic(self):
  h=rt.BiolinMediumlRuntime();call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,plate_mass_kg=.001));self.rejected(h,'subphase.load','BALANCE_OVERLOAD',**rt.SUBPHASE_EXAMPLE)
 def test_34_actual_effects_cover_changed_states(self):
  meta=rt.export_biolin_mediuml_contract_metadata();original=rt.BiolinMediumlRuntime.invoke
  def audited(h,k,**a):
   before=h.read();r=original(h,k,**a);after=h.read();changed={x for x in before if before[x]!=after[x]};declared={e['state_id'] for e in meta['capabilities'][k]['state_effects'] if e['timing']=='immediate'};self.assertFalse(changed-declared,(k,changed-declared));return r
  with mock.patch.object(rt.BiolinMediumlRuntime,'invoke',audited):
   h=setup(substrate=True);call(h,'sim.step',dt_s=.1);compress(h);call(h,'balance.calibrate',**rt.CAPS['balance.calibrate']['example_arguments']);cleanup(h)
 def test_35_agent_result_does_not_serialize_private_parameters(self):
  h=setup(plant={'plate_contact_cosine':.73456789,'force_gain':1.23456789});r=compress(h);text=json.dumps(r)
  for forbidden in ['concentration_mol_m3','force_gain','plate_contact_cosine','leak_rate_s','transfer_efficiency',str(.73456789),str(1.23456789)]:self.assertNotIn(forbidden,text)
  self.assertNotIn('truth.',json.dumps(h.session().read(instance_id=h.instance_id)))
 def test_36_sampling_aligned_to_start_and_terminal_derivative(self):
  h=setup();h.step(.1);call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,sample_interval_s=.3));call(h,'acquisition.start');r=finish(h);times=[p['time_s'] for p in r['rows']];self.assertTrue(np.allclose(np.diff(times)[:-1],.3,atol=1e-12));self.assertIsNotNone(r['rows'][-1]['apparent_modulus_n_m'])
 def test_37_actual_api_and_state_schemas(self):
  spec=importlib.util.spec_from_file_location('mediuml_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_biolin_mediuml_contract_metadata();h=setup();call(h,'acquisition.start');call(h,'sim.step',dt_s=.7);call(h,'acquisition.stop');call(h,'acquisition.result');self.ledger(h)
  for k,c in rt.CAPS.items():
   if k in rt.UNSUPPORTED:continue
   cases=[v for v in OBSERVED if v[0]==k];self.assertTrue(cases,k)
   for _,a,r in cases:self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema']),[],k);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema']),[],k)
  for state in [rt.BiolinMediumlRuntime().read(),h.read(),deposition_ready().read()]:
   self.assertEqual(set(state),set(meta['state_slots']))
   for k,v in state.items():self.assertEqual(tool.validate_value(v,meta['state_slots'][k]['value_schema']),[],k)

 def test_38_partial_layers_are_normalized_by_whole_substrate(self):
  h=deposition_ready();call(h,'acquisition.start');h.step(3.);call(h,'acquisition.stop');r=call(h,'acquisition.result');s=r['strokes'][0];full=rt.SUBSTRATE_EXAMPLE['width_m']*rt.SUBSTRATE_EXAMPLE['height_m']*rt.SUBSTRATE_EXAMPLE['faces'];self.assertAlmostEqual(r['equivalent_layers_from_area'],s['transfer_ratio']*s['swept_area_m2']/full);self.assertGreater(r['equivalent_layers_from_area'],0);self.assertLess(r['equivalent_layers_from_area'],.2);self.assertEqual(r['completed_strokes'],0)

 def _run_reference(self,rid,gamma=.0728):
  """Load any runtime-usable record at an in-domain spreading amount, compress inside its runtime window."""
  rec=rt.library()['substances'][rid];dom=rec['runtime_domain'];amax=dom['maximum_molecular_area_m2'];amin=dom['minimum_molecular_area_m2']
  n=rt.AREA_MAX/(.9*amax*rt.NA);a_target=max(1.05*amin,1.01*.9*amax/10.8);target=a_target*n*rt.NA
  h=rt.BiolinMediumlRuntime('expansion_'+rid)
  for k,a in [('plant.configure',rt.PLANT_EXAMPLE),('subphase.load',dict(rt.SUBPHASE_EXAMPLE,surface_tension_n_m=gamma)),('power.set',{'enabled':True}),('balance.tare',{}),
              ('solution.load',dict(rt.SAMPLE_EXAMPLE,reference_id=rid,concentration_mol_m3=n/50e-9)),('film.spread',{'volume_m3':50e-9})]:call(h,k,**a)
  h.step(4.);self.assertAlmostEqual(h.surface_mol,n,delta=n*1e-12)
  call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,target_area_m2=target,duration_s=120.));call(h,'acquisition.start');r=finish(h,130)
  return h,rec,r,n,target
 def test_39_expansion_records_run_across_material_classes(self):
  lib=rt.library()['substances']
  sample=['uw2026_dppc_water','uw2025_dopc','uw2026_cpc_pbs','uw2025_dspe_peg2000_pbs','repod2023_palmitic_acid','rodare2023_oleic_acid','rodare2023_heptadecyl_punicine','zenodo2021_pbd_b_peo',
          'epmc3682106_hepc','epmc10671155_oleanolic_acid','epmc8280729_cyclosporin_a','epmc9483982_f10h10oh','epmc12080323_dopg']
  kinds=set()
  for rid in sample:
   h,rec,r,n,target=self._run_reference(rid);kinds.add(rec['kind'])
   self.assertEqual(r['phase'],'succeeded',rid);self.assertAlmostEqual(h.area,target,delta=1e-9);self.assertEqual(r['reference_id'],rid)
   x=h.area/(n*rt.NA);p,c=rt.eos(rec,x);self.assertAlmostEqual(h._eos()[0],p,places=12)
   if rt.is_scalar_model(rec):
    a0,s,w,lo,hi=rt.model_parameters(rec);self.assertAlmostEqual(p,s*w*math.log1p(math.exp((a0-x)/w)),places=12);self.assertIn('Derived engineering EOS',r['evidence'])
   else:
    a,pp=rt.curve(rec);self.assertAlmostEqual(p,float(np.interp(x,a,pp)),places=12);self.assertIn('Measured source compression arrays',r['evidence'])
   self.assertGreater(r['rows'][-1]['surface_pressure_n_m'],r['rows'][0]['surface_pressure_n_m'],rid);self.assertEqual(h.source,json.dumps(rec['source'],ensure_ascii=False));self.ledger(h)
  self.assertEqual(kinds,{'measured_compression_isotherm','measured_scalar_isotherm_parameters'})
 def test_40_scalar_eos_reproduces_published_constraints(self):
  lib=rt.library()['substances'];models=[r for r in lib.values() if rt.is_scalar_model(r)];self.assertGreaterEqual(len(models),13)
  for rec in models:
   a0,s,w,lo,hi=rt.model_parameters(rec);sc=rec['scalars'];alo=sc['lift_off_area']['value_si'];pc=sc['collapse_pressure']['value_si']
   self.assertAlmostEqual(rt.model_eos(a0,s,w,alo)[0],1e-4,delta=1e-9,msg=rec['substance_id'])
   self.assertLessEqual(rt.eos(rec,lo)[0],pc*(1+1e-6));self.assertAlmostEqual(hi,2*alo,places=30)
   if 'collapse_area' in sc and sc['collapse_area']['value_si']>=lo*(1-1e-12):self.assertAlmostEqual(rt.eos(rec,sc['collapse_area']['value_si'])[0],pc,delta=pc*1e-3)
   else:self.assertAlmostEqual(rt.eos(rec,lo)[0],pc,delta=pc*1e-6)
   grid=np.linspace(lo,alo,4001);cs=[rt.eos(rec,x)[1] for x in grid]
   if 'max_compression_modulus' in sc:self.assertAlmostEqual(max(cs),sc['max_compression_modulus']['value_si'],delta=.011*sc['max_compression_modulus']['value_si'],msg=rec['substance_id'])
   if 'limiting_area' in sc:self.assertAlmostEqual(a0,sc['limiting_area']['value_si'],places=30)
   x=.5*(lo+alo);d=x*1e-7;fd=-(rt.eos(rec,x+d)[0]-rt.eos(rec,x-d)[0])/(2*d)*x;self.assertAlmostEqual(rt.eos(rec,x)[1],fd,delta=abs(fd)*1e-5+1e-9)
   a,p=rt.curve(rec);self.assertTrue(np.all(np.diff(a)>0) and np.all(np.diff(p)<0));self.assertEqual(rec['runtime_model']['evidence'],'derived')
   for q in sc.values():self.assertEqual(q['evidence'],'measured_literature')
 def test_41_coverage_and_reference_only_guards(self):
  lib=rt.library();cov=lib['response_coverage'];subs=lib['substances']
  self.assertGreaterEqual(cov['distinct_reference_materials'],50);self.assertGreaterEqual(cov['runtime_materials'],35);self.assertFalse(cov['minimum_50_runtime_materials_met'])
  self.assertEqual(cov['runtime_materials'],len({r['material_identity'] for r in subs.values() if r['runtime_usable']}));self.assertEqual(cov['digitized_points'],0)
  for rid in ['rodare2023_sodium_oleate','epmc9654498_cholesterol','epmc10305212_amphotericin_b','epmc10249408_gmo']:self.assertFalse(subs[rid]['runtime_usable']);self.assertIn('Reference only',subs[rid]['runtime_reason'])
  for rid,r in subs.items():
   _need(ROOT/r['source']['file']);self.assertEqual(hashlib.sha256((ROOT/r['source']['file']).read_bytes()).hexdigest(),r['source']['sha256'],rid)
   if r['runtime_usable'] and r['values']['molecular_area_m2']:self.assertTrue(all(x>y for x,y in zip(r['values']['molecular_area_m2'],r['values']['molecular_area_m2'][1:])),rid)
  h=setup();cleanup(h);call(h,'subphase.load',**rt.SUBPHASE_EXAMPLE);self.rejected(h,'solution.load','INVALID_REFERENCE',**dict(rt.SAMPLE_EXAMPLE,reference_id='epmc9654498_cholesterol'))
 def test_42_scalar_eos_domain_and_dilute_extension(self):
  rec=rt.library()['substances']['epmc3682106_ocpc'];a0,s,w,lo,hi=rt.model_parameters(rec)
  for x in [lo*.999,hi*1.001,float('nan')]:
   with self.assertRaises(rt.ActionRejected):rt.eos(rec,x)
  self.assertEqual(rt.eos(rec,hi*1.5,True),(0.,0.));bad=copy.deepcopy(rec);bad['runtime_model']['parameters_si']['limiting_area_m2']=hi*2
  with self.assertRaises(rt.ActionRejected):rt.model_parameters(bad)
  h=setup(sample={'reference_id':'epmc3682106_ocpc','concentration_mol_m3':rt.AREA_MAX/(.9*hi*rt.NA)/50e-9});x=h.area/(h.surface_mol*rt.NA);self.assertLess(x,hi);self.rejected(h,'barrier.move','EOS_AREA_RANGE',area_m2=lo*h.surface_mol*rt.NA*.99,speed_m_s=.0045)

if __name__=='__main__':unittest.main()
