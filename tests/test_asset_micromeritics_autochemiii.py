import copy, importlib.util, json, math, sys, unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import micromeritics_autochemiii as rt
OBSERVED=[]

def call(h,k,**a):
 result=h.invoke(k,**a);OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(result)));return result

def setup(mode='tpd',instance_id='autochemiii_01',plant=None,sample=None):
 h=rt.MicromeriticsAutochemiiiRuntime(instance_id);call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,**(plant or {})));s=dict(rt.SAMPLE_EXAMPLE,mode=mode);s.update(sample or {})
 if mode=='pulse':s['initial_bound_mol']=0.
 call(h,'sample.load',**s);gas=dict(rt.GAS_EXAMPLE)
 if mode in ('tpr','tpo'):
  fractions=[.9]+[0.]*8;fractions[rt.SPECIES.index('h2' if mode=='tpr' else 'o2')]=.1;gas['mole_fractions']=fractions
 call(h,'gas.load',**gas)
 if mode=='pulse':
  fractions=[0.]*9;fractions[rt.SPECIES.index(s['adsorbate'])]=1.;call(h,'gas.load',**dict(rt.GAS_EXAMPLE,bank='loop',mole_fractions=fractions))
 call(h,'furnace.set_open',opened=False);call(h,'power.set',enabled=True);call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,mode=mode));call(h,'gas.open',enabled=True);return h

def finish(h,seconds=100):
 for _ in range(round(seconds/rt.DT)):
  h.step(rt.DT)
  if h.phase!='running':break
 return h.invoke('acquisition.result')

class TestAutoChemIII(unittest.TestCase):
 def assert_ledger(self,h):
  ledger=h._ledger();self.assertLess(abs(ledger['mass_error_kg']),1e-12);self.assertLess(max(map(abs,ledger['element_error_mol'])),1e-10);self.assertLess(abs(ledger['heat_error_j']),1e-6)
 def test_01_short_actual_tpd(self):
  h=setup();initial=h.bound;call(h,'acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertGreater(len(r['rows']),10);self.assertLess(h.bound,initial);self.assertGreater(h.tcd,0.);self.assertGreater(r['integral_v_s'],0.);self.assert_ledger(h)
 def test_02_tpr_and_tpo_atoms(self):
  for mode,product in [('tpr','h2o'),('tpo','co2')]:
   h=setup(mode);initial=h.bound;h.invoke('acquisition.start');finish(h);self.assertLess(h.bound,initial);self.assertGreater(h.exhaust[rt.SPECIES.index(product)],0.);self.assert_ledger(h)
 def test_03_pulse_finite_sites(self):
  h=setup('pulse',sample={'preexponential_s':2.,'activation_j_mol':0.});h.invoke('acquisition.start');r=finish(h);self.assertEqual(r['phase'],'succeeded');self.assertEqual(h.pulses,3);self.assertGreater(h.bound,0.);self.assertLessEqual(h.bound,h.sample['capacity_mol']);self.assert_ledger(h)
 def test_04_pure_reads_and_host_clock(self):
  h=setup();s=h.session();before=copy.deepcopy(h.__dict__);s.describe(instance_id=h.instance_id);s.read(instance_id=h.instance_id);h.invoke('acquisition.result');h.invoke('ledger.read');self.assertEqual(before,h.__dict__);h.invoke('thermal.set_temperature',temperature_k=303.15,ramp_k_s=1.);self.assertEqual(h.temperature,298.15);self.assertEqual(h.time_s,0.);h.step(.1);self.assertGreater(h.temperature,298.15)
 def test_05_partition_independence(self):
  a=setup();b=setup();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(3.)
  for _ in range(30):b.step(.1)
  self.assertEqual(a.read(),b.read());self.assertEqual(a.rows,b.rows)
 def test_06_two_instances(self):
  a=setup(instance_id='a');b=setup(instance_id='b');before=copy.deepcopy(b.__dict__);a.invoke('acquisition.start');a.step(3);self.assertEqual(before,b.__dict__)
 def test_07_agent_truth_boundary(self):
  h=setup();s=h.session();d=s.describe(instance_id=h.instance_id);self.assertNotIn('truth.bound_mol',d['state_slots']);self.assertNotIn('gas.load',d['capabilities']);before=copy.deepcopy(h.__dict__)
  for cap,args in [('ledger.read',{}),('sample.remove',{}),('gas.unload',{'bank':'carrier','inlet':1}),('sim.step',{'dt_s':1.})]:
   r=s.invoke(instance_id=h.instance_id,capability_id=cap,arguments=args,request_id=cap);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(before,h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):s.read(instance_id=h.instance_id,state_ids=['truth.stock_mol'])
 def test_08_ownership_and_queries(self):
  h=setup();h.register_principal('other');a=h.session();b=h.session('other');op=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];q=b.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='query');self.assertIsNone(q['operation_id'])
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):b.poll(op)
  self.assertEqual(b.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id='stop')['error']['code'],'ACCESS_DENIED');self.assertEqual(a.cancel(op,'cancel')['status'],'succeeded')
 def test_09_retry(self):
  h=setup();s=h.session();a=dict(instance_id=h.instance_id,capability_id='gas.set_flow',arguments={'reference_m3_s':20e-6/60},request_id='x');self.assertEqual(s.invoke(**a),s.invoke(**a));a['arguments']={'reference_m3_s':50e-6/60};self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT')
 def test_10_terminal_history_and_return_copies(self):
  h=setup();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];finish(h);saved=s.poll(op);h.invoke('gas.open',enabled=False);h.invoke('furnace.set_open',opened=True);h.invoke('sample.remove');h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,mode='tpr'));self.assertEqual(saved,s.poll(op));self.assertEqual(saved['result'],h.invoke('acquisition.result'));saved['result']['rows'][0]['tcd_v']=123.;self.assertNotEqual(saved,s.poll(op))
 def test_11_cancel_keeps_partial_effects(self):
  h=setup('pulse');s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];h.step(1.);bound=h.bound;stock=h.read()['truth.stock_mol'];s.cancel(op,'cancel');frozen=s.poll(op);h.step(1.);self.assertEqual(frozen,s.poll(op));self.assertFalse(h.gas_enabled);self.assertLessEqual(h.read()['truth.stock_mol'],stock);self.assertGreaterEqual(h.bound,bound);self.assert_ledger(h)
 def test_12_depletion_terminal_snapshot_includes_last_tick(self):
  h=setup();h.invoke('gas.open',enabled=False);h.invoke('gas.unload',bank='carrier',inlet=1);h.invoke('gas.load',**dict(rt.GAS_EXAMPLE,amount_mol=1e-9));h.invoke('gas.open',enabled=True);h.invoke('acquisition.start');h.step(.1);r=h.invoke('acquisition.result');self.assertEqual(r['phase'],'failed');self.assertEqual(r['error_code'],'GAS_EXHAUSTED');self.assertEqual(r['elapsed_s'],.1);self.assertEqual(r['rows'][-1]['time_s'],h.time_s);self.assertEqual(h.read()['truth.stock_mol'],0.);self.assert_ledger(h)
 def test_13_pulse_shortage_atomic(self):
  h=setup('pulse');h.invoke('gas.open',enabled=False);h.invoke('gas.unload',bank='loop',inlet=1);h.invoke('gas.load',**dict(rt.GAS_EXAMPLE,bank='loop',amount_mol=1e-9));h.invoke('gas.open',enabled=True);before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'FINITE_INVENTORY'):h.invoke('acquisition.start')
  self.assertEqual(before,h.__dict__)
 def test_14_ramp_three_temperature_bands(self):
  h=setup();h.invoke('gas.open',enabled=False)
  for target,rate in [(1073.15,100/60),(1273.15,50/60),(1473.15,25/60)]:h.invoke('thermal.set_temperature',temperature_k=target,ramp_k_s=rate)
  for target,rate in [(1073.25,100/60),(1273.25,50/60),(1473.15,26/60)]:
   before=copy.deepcopy(h.__dict__)
   with self.assertRaisesRegex(rt.ActionRejected,'RAMP_LIMIT'):h.invoke('thermal.set_temperature',temperature_k=target,ramp_k_s=rate)
   self.assertEqual(before,h.__dict__)
 def test_15_temperature_increment_lower_bound_and_path(self):
  h=setup();h.invoke('thermal.set_temperature',temperature_k=293.15,ramp_k_s=1/60)
  for args,error in [(dict(temperature_k=293.05,ramp_k_s=1/60),'TEMPERATURE_RANGE'),(dict(temperature_k=300.16,ramp_k_s=1/60),'TEMPERATURE_INCREMENT'),(dict(temperature_k=300.15,ramp_k_s=.5/60),'INVALID_ARGUMENT')]:
   before=copy.deepcopy(h.__dict__)
   with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke('thermal.set_temperature',**args)
   self.assertEqual(before,h.__dict__)
  call(h,'path.set_temperature',temperature_k=423.15)
  with self.assertRaises(rt.ActionRejected):h.invoke('path.set_temperature',temperature_k=423.25)
 def test_16_downramp_start_temperature(self):
  h=setup(plant={'thermal_capacity_j_k':1.,'heat_loss_w_k':0.,'controller_w_k':1000.},sample={'preexponential_s':0.});h.invoke('gas.open',enabled=False);h.invoke('thermal.set_temperature',temperature_k=1473.15,ramp_k_s=25/60);h.step(3000.);self.assertGreater(h.programmed,1273.15)
  with self.assertRaisesRegex(rt.ActionRejected,'RAMP_LIMIT'):h.invoke('thermal.set_temperature',temperature_k=298.15,ramp_k_s=100/60)
  h.invoke('thermal.set_temperature',temperature_k=298.15,ramp_k_s=25/60)
 def test_17_all_inlets_and_flow_limits(self):
  h=setup();h.invoke('gas.open',enabled=False)
  for bank in rt.BANKS:
   for inlet in range(1,7):call(h,'gas.select',bank=bank,inlet=inlet)
  call(h,'gas.route',bank='pretreatment')
  for q in [0.,1e-6/60,100e-6/60]:call(h,'gas.set_flow',reference_m3_s=q)
  for q in [.5e-6/60,101e-6/60,-1.]:
   before=copy.deepcopy(h.__dict__)
   with self.assertRaises(rt.ActionRejected):h.invoke('gas.set_flow',reference_m3_s=q)
   self.assertEqual(before,h.__dict__)
 def test_18_engineering_and_unknown_rejection(self):
  h=rt.MicromeriticsAutochemiiiRuntime();before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'MODEL_NOT_ACCEPTED'):h.invoke('plant.configure',**dict(rt.PLANT_EXAMPLE,accept_engineering=False))
  self.assertEqual(before,h.__dict__);h.invoke('plant.configure',**rt.PLANT_EXAMPLE);before=copy.deepcopy(h.__dict__)
  for change in [{'accept_engineering_kinetics':False},{'accept_unknown_conditions':False},{'particle_diameter_m':.0091},{'initial_bound_mol':.2}]:
   with self.assertRaises(rt.ActionRejected):h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,**change))
   self.assertEqual(before,h.__dict__)
 def test_19_invalid_numbers_and_busy_atomic(self):
  h=setup();before=copy.deepcopy(h.__dict__)
  for value in [True,float('nan'),float('inf')]:
   with self.assertRaises(rt.ActionRejected):h.invoke('gas.set_flow',reference_m3_s=value)
   self.assertEqual(before,h.__dict__)
  h.invoke('acquisition.start');before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'BUSY'):h.invoke('method.configure',**rt.METHOD_EXAMPLE)
  self.assertEqual(before,h.__dict__)
 def test_20_independent_integral_calibration_dispersion(self):
  h=setup();c=call(h,'detector.calibrate',known_moles_mol=[1e-6,2e-6],observed_areas_v_s=[.1,.2]);self.assertAlmostEqual(c['mol_per_v_s'],1e-5);r=call(h,'analysis.integrate',times_s=[0.,1.,2.],signals_v=[0.,.1,0.],baseline_v=0.,mol_per_v_s=c['mol_per_v_s']);self.assertAlmostEqual(r['amount_mol'],1e-6);d=call(h,'analysis.dispersion',**rt.CAPS['analysis.dispersion']['example_arguments']);self.assertAlmostEqual(d['fraction'],.00586934)
 def test_21_stiff_thermal_inputs_remain_stable(self):
  h=setup(plant={'thermal_capacity_j_k':1.,'controller_w_k':1000.,'heater_limit_w':10000.,'cooler_limit_w':10000.,'heat_loss_w_k':100.},sample={'preexponential_s':0.});h.invoke('thermal.set_temperature',temperature_k=373.15,ramp_k_s=100/60);h.step(60.);self.assertTrue(298.15<=h.temperature<=373.15);h.invoke('thermal.set_temperature',temperature_k=293.15,ramp_k_s=100/60);h.step(100.);self.assertTrue(293.15<=h.temperature<=298.15);self.assert_ledger(h)
 def test_22_timeout_and_independent_queries(self):
  h=setup();h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,maximum_duration_s=.2));h.invoke('acquisition.start');h.step(1.);r=h.invoke('acquisition.result');self.assertEqual(r['error_code'],'TIMEOUT');before=copy.deepcopy(h.__dict__);h.invoke('reference.query',reference_id='parlett2023_nio_sio2');self.assertEqual(before,h.__dict__)
 def test_23_mechanical_access_and_actual_commands(self):
  h=setup();h.invoke('gas.open',enabled=False);call(h,'furnace.open',opened=True);call(h,'front_cover.set',distance_m=.16)
  before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'SAMPLE_CONNECTION'):h.invoke('sample.set_position',distance_m=.12)
  self.assertEqual(before,h.__dict__);call(h,'sample.release',angle_rad=.6);call(h,'sample.set_position',distance_m=.12)
  with self.assertRaisesRegex(rt.ActionRejected,'SAMPLE_CONNECTION'):h.invoke('furnace.set_open',opened=False)
  call(h,'sample.set_position',distance_m=0.);call(h,'sample.release',angle_rad=0.);call(h,'sample.remove');call(h,'gas.unload',bank='carrier',inlet=1);call(h,'power.set',enabled=False);call(h,'service.set',distance_m=.1);self.assert_ledger(h)
 def test_24_reference_curves_and_summary_are_not_tcd(self):
  h=setup();a=call(h,'reference.query',reference_id='parlett2023_nio_sio2');b=call(h,'reference.query',reference_id='rodbuk2025_cu_ti_itq2_tpd');c=call(h,'reference.query',reference_id='garidzirai2021_pt_alumina');self.assertEqual(json.loads(b['reference_json'])['values']['signal_unit'],'Pa');self.assertEqual(json.loads(c['reference_json'])['values']['metal_dispersion_fraction'],.38);self.assertIn('source',json.loads(a['reference_json']))
 def test_25_terminal_source_is_snapshot(self):
  h=setup(sample={'reference_id':'parlett2023_nio_sio2'});h.invoke('acquisition.start');r=finish(h);h.invoke('gas.open',enabled=False);h.invoke('furnace.set_open',opened=True);h.invoke('sample.remove');h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,reference_id='garidzirai2021_pt_alumina'));self.assertEqual(r,h.invoke('acquisition.result'))
 def test_26_invalid_composition(self):
  h=setup();h.invoke('gas.open',enabled=False);before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'INVALID_COMPOSITION'):h.invoke('gas.load',**dict(rt.GAS_EXAMPLE,inlet=2,mole_fractions=[.5]*9))
  self.assertEqual(before,h.__dict__)
 def test_27_unsupported_and_identity(self):
  h=setup();self.assertEqual(rt.CATALOG_ID,'CHEM-122')
  for k in rt.UNSUPPORTED:
   with self.assertRaisesRegex(rt.ActionRejected,'UNSUPPORTED'):h.invoke(k)
  with self.assertRaises(rt.ActionRejected):h.session('unregistered')
  with self.assertRaises(rt.ActionRejected):h.register_principal('agent','evaluator')
 def test_28_state_effects_cover_real_changes(self):
  meta=rt.export_micromeritics_autochemiii_contract_metadata();original=rt.MicromeriticsAutochemiiiRuntime.invoke
  def audited(h,k,**a):
   before=h.read();r=original(h,k,**a);after=h.read();changed={x for x in before if before[x]!=after[x]};declared={e['state_id'] for e in meta['capabilities'][k]['state_effects'] if e['timing']=='immediate'};self.assertFalse(changed-declared,(k,changed-declared));return r
  with mock.patch.object(rt.MicromeriticsAutochemiiiRuntime,'invoke',audited):
   h=setup();call(h,'path.set_temperature',temperature_k=323.15);call(h,'acquisition.start');call(h,'sim.step',dt_s=2.);call(h,'acquisition.stop');call(h,'furnace.set_open',opened=True);call(h,'sample.remove');call(h,'power.set',enabled=False);call(h,'service.set',distance_m=.1)
 def test_28b_invalid_time_and_hot_access_are_atomic(self):
  h=setup();before=copy.deepcopy(h.__dict__)
  for dt in [-.1,True,float('nan'),float('inf')]:
   with self.assertRaises(rt.ActionRejected):h.step(dt)
   self.assertEqual(before,h.__dict__)
  h.step(0.);self.assertEqual(before,h.__dict__);h.invoke('gas.open',enabled=False);h.invoke('thermal.set_temperature',temperature_k=343.15,ramp_k_s=100/60);h.step(40.);self.assertGreater(h.temperature,323.15);before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'HOT_OR_FLOWING'):h.invoke('furnace.set_open',opened=True)
  self.assertEqual(before,h.__dict__)
 def test_29_all_actual_api_and_state_schema(self):
  spec=importlib.util.spec_from_file_location('autochem_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=rt.export_micromeritics_autochemiii_contract_metadata()
  h=setup();call(h,'acquisition.start');call(h,'sim.step',dt_s=20.);call(h,'acquisition.result');call(h,'ledger.read');call(h,'thermal.set_temperature',temperature_k=313.15,ramp_k_s=1.)
  for k,c in rt.CAPS.items():
   if k in rt.UNSUPPORTED:continue
   cases=[x for x in OBSERVED if x[0]==k];self.assertTrue(cases,k)
   for _,a,r in cases:self.assertEqual(tool.validate_value(a,meta['capabilities'][k]['input_schema']),[],k);self.assertEqual(tool.validate_value(r,meta['capabilities'][k]['output_schema']),[],k)
  for key,value in h.read().items():self.assertEqual(tool.validate_value(value,meta['state_slots'][key]['value_schema']),[],key)
if __name__=='__main__':unittest.main()
