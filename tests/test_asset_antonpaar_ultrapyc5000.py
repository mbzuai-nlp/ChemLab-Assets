import unittest,copy,math,importlib.util,statistics,json
from scripts.asset_runtime import antonpaar_ultrapyc5000 as r
spec=importlib.util.spec_from_file_location('asset_tool',r.ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);OBSERVED=set()
def call(h,k,a=None):
 v=h.invoke(k,**(a or {}));tool.validate_value(v,r.CAPS[k][1]);OBSERVED.add(k);return v
def open_cup(h):
 for k,a in [('seal.set',{'angle_rad':0.}),('lock.set',{'angle_rad':0.}),('lid.set',{'lift_m':.13}),('cup.set',{'lift_m':.12})]:call(h,k,a)
def close_cup(h):
 for k,a in [('cup.set',{'lift_m':0.}),('lid.set',{'lift_m':0.}),('lock.set',{'angle_rad':1.1}),('seal.set',{'angle_rad':1.4})]:call(h,k,a)
def calibrate(h):
 c=r.CELLS[h.method['cell']];v=8e-6 if h.method['reference']=='small' or (h.method['reference']=='auto' and c<=10e-6) else 50e-6;s=c*.2
 a=dict(blank_ratio=c/v,sphere_ratio=(c-s)/v,sphere_volume_m3=s) if h.method['direction']=='powder_protect' else dict(blank_ratio=v/c,sphere_ratio=v/(c-s),sphere_volume_m3=s);return call(h,'volume.calibrate',a)
def setup(plant=None,method=None,sample=None,load=True):
 h=r.AntonpaarUltrapyc5000Runtime();call(h,'plant.configure',dict(r.PLANT_EXAMPLE,**(plant or {})));call(h,'method.configure',dict(r.METHOD_EXAMPLE,**(method or {})));calibrate(h)
 if load:open_cup(h);call(h,'sample.load',dict(r.SAMPLE_EXAMPLE,**(sample or {})));call(h,'sample.set_mass',{'mass_kg':h.sample['true_mass_kg']});close_cup(h)
 call(h,'power.set',{'enabled':True});return h
def finish(h,limit=1800):
 if h.phase!='running':call(h,'acquisition.start')
 for _ in range(limit):
  h.step(1.)
  if h.phase!='running':break
 return h
def until(h,stage,limit=1000):
 if h.phase!='running':call(h,'acquisition.start')
 for _ in range(limit*10):
  if h.stage==stage:return
  if h.phase!='running':raise AssertionError((h.phase,h.stage,h.failure))
  h.step(.1)
 raise AssertionError((stage,h.stage,h.failure))
class Tests(unittest.TestCase):
 def ledger(self,h):
  x=call(h,'ledger.read');self.assertLess(max(abs(v) for v in x['species_residual_mol']),1e-12);self.assertLess(abs(x['sample_residual_kg']),1e-12);self.assertLess(abs(x['energy_residual_j']),max(1e-8,2e-11*abs(h.energy())));self.assertGreaterEqual(x['source_mol'],0.)
 def test_01_real_reference_provenance(self):
  h=setup();self.assertEqual(len(h.library['substances']),73);a=call(h,'template.reference',{'substance_id':'nist_al'});self.assertEqual(a['density_kg_m3'],2699.);self.assertEqual(a['evidence'],'assumption');self.assertEqual(call(h,'template.reference',{'substance_id':'measured_cu'})['evidence'],'measured_literature');self.assertEqual(call(h,'template.reference',{'substance_id':'measured_174ph'})['density_kg_m3'],7780.)
  self.assertTrue(all(v['conditions']['temperature_k'] is None for v in h.library['substances'].values()));self.assertNotIn('nist_hg',h.library['substances']);self.assertNotIn('nist_at',h.library['substances'])
 def test_02_both_directions_pressure_inversion(self):
  for d in ['sample_first','powder_protect']:
   h=finish(setup(method={'direction':d}));self.assertEqual(h.phase,'succeeded',h.failure);res=call(h,'process.measure');self.assertEqual(len(res['runs']),3);self.assertAlmostEqual(res['mean_density_kg_m3'],2699.,delta=.02);self.assertAlmostEqual(res['mean_volume_m3'],.05/2699.,delta=2e-10);self.ledger(h)
   self.assertAlmostEqual(res['standard_deviation_m3'],statistics.stdev(v['volume_m3'] for v in res['runs']),places=15)
 def test_03_closed_and_open_pores(self):
  a=finish(setup(sample={'open_pore_m3':0.}));b=finish(setup(sample={'open_pore_m3':20e-6}));c=finish(setup(sample={'closed_pore_m3':3e-6}));self.assertAlmostEqual(a._stats()[0],b._stats()[0],places=13);self.assertAlmostEqual(c._stats()[0]-a._stats()[0],3e-6,delta=2e-10);self.assertLess(c._stats()[1],a._stats()[1]);self.ledger(c)
 def test_04_entered_mass_not_truth(self):
  h=setup();call(h,'sample.set_mass',{'mass_kg':.055});finish(h);self.assertEqual(h.sample['true_mass_kg'],.05);self.assertAlmostEqual(h._stats()[1],2699.*1.1,delta=.03);self.ledger(h)
 def test_05_two_actual_pressure_runs_calibration(self):
  for d in ['sample_first','powder_protect']:
   h=setup(plant={'cell_volume_factor':1.01,'reference_volume_factor':.99},method={'mode':'calibration','direction':d,'max_runs':1,'runs_to_average':1},load=False);finish(h);blank=h.runs[0]['ratio'];h.step(20);open_cup(h);call(h,'sample.load',dict(r.SAMPLE_EXAMPLE,true_mass_kg=.02699,open_pore_m3=0.));close_cup(h);finish(h);sphere=h.runs[0]['ratio'];v=call(h,'volume.calibrate',dict(blank_ratio=blank,sphere_ratio=sphere,sphere_volume_m3=1e-5));self.assertAlmostEqual(v['cell_volume_m3'],50.5e-6,delta=2e-10);self.assertAlmostEqual(v['reference_volume_m3'],49.5e-6,delta=2e-10)
   call(h,'method.configure',dict(h.method,mode='measure'));call(h,'sample.set_mass',{'mass_kg':.02699});finish(h);self.assertAlmostEqual(h._stats()[1],2699.,delta=.1);self.ledger(h)
 def test_06_pressure_gain_offset_calibration(self):
  h=setup(plant={'pressure_gain':[1.03,.98],'pressure_offset_pa':[100.,-70.]});call(h,'pressure.calibrate',dict(reference_pa=[101325.,201325.],sample_measured_pa=[101325.*1.03+100.,201325.*1.03+100.],reference_measured_pa=[101325.*.98-70.,201325.*.98-70.]));calibrate(h);finish(h);self.assertAlmostEqual(h._stats()[1],2699.,delta=.03);self.ledger(h)
 def test_07_flow_purge_exact_residual(self):
  h=setup(method={'preparation':'flow','preparation_s':60.});until(h,'flow');old=h.ns[1]+h.nr[1];total=sum(h.ns)+sum(h.nr);h.step(1.);dn=h.method['flow_m3_s']*101325./r.R/273.15;self.assertAlmostEqual(h.ns[1]+h.nr[1],old*math.exp(-dn/total),places=12);self.assertGreater(h.source_initial-h.source,0.);finish(h);self.assertEqual(h.phase,'succeeded',h.failure);self.ledger(h)
 def test_08_pulse_preparation(self):
  h=finish(setup(method={'preparation':'pulse','purge_pulses':3}));self.assertEqual(h.pulses,3);self.assertEqual(h.phase,'succeeded',h.failure);self.assertAlmostEqual(h._stats()[1],2699.,delta=.03);self.ledger(h)
 def test_09_vacuum_and_missing_pump(self):
  h=finish(setup(method={'preparation':'vacuum','preparation_s':60.}));self.assertEqual(h.phase,'succeeded',h.failure);self.assertGreater(sum(h.evacuated),0);self.ledger(h)
  h=setup(plant={'pump_available':False},method={'preparation':'vacuum','preparation_s':60.});old=h.read('evaluator')
  with self.assertRaisesRegex(r.ActionRejected,'PUMP_UNAVAILABLE'):call(h,'acquisition.start')
  self.assertEqual(old,h.read('evaluator'))
 def test_10_finite_gas_depletion(self):
  h=finish(setup(plant={'source_volume_m3':1e-5}));self.assertEqual(h.phase,'failed');self.assertEqual(h.failure,'GAS_DEPLETED');self.assertGreater(h.source_initial-h.source,0);self.ledger(h)
 def test_11_leak_observation_is_not_random_fault(self):
  good=finish(setup(method={'mode':'leak'},load=False));bad=finish(setup(plant={'leak_conductance_mol_s_pa':1e-11},method={'mode':'leak'},load=False));self.assertEqual(good.phase,'succeeded');self.assertEqual(bad.phase,'succeeded',bad.failure);self.assertTrue(good.leak_pass);self.assertFalse(bad.leak_pass);self.assertGreater(bad.leak_rate,1.);self.ledger(bad)
 def test_12_setpoint_energy_cooling_and_heating(self):
  h=setup(load=False);t=h.temp;call(h,'thermal.set_temperature',{'temperature_k':276.15});self.assertEqual(h.temp,t);h.step(3600);self.assertAlmostEqual(h.temp,276.15,delta=.02);self.ledger(h);call(h,'thermal.set_temperature',{'temperature_k':323.15});h.step(7200);self.assertAlmostEqual(h.temp,323.15,delta=.02);self.ledger(h)
 def test_13_partial_cancel_and_poweroff_vent(self):
  h=setup();until(h,'expand');consumed=h.source_initial-h.source;peak=max(h.pressure(0),h.pressure(1));call(h,'acquisition.stop');self.assertEqual(h.phase,'cancelled');self.assertEqual(h.source_initial-h.source,consumed);self.assertGreater(peak,h.plant['ambient_pa']+1000);h.step(30.);self.assertLess(max(h.pressure(0),h.pressure(1)),peak);self.assertEqual(h.sample['true_mass_kg'],.05);self.ledger(h)
  h=setup();until(h,'expand');call(h,'power.set',{'enabled':False});self.assertEqual(h.phase,'cancelled');h.step(30.);self.assertEqual(h.power_w,0);self.ledger(h)
 def test_14_pure_reads_partition_and_instances(self):
  a=setup();b=copy.deepcopy(a);call(a,'acquisition.start');call(b,'acquisition.start');a.step(7.3)
  for _ in range(73):b.step(.1)
  self.assertEqual(a.read('evaluator'),b.read('evaluator'));old=a.read('evaluator');call(a,'process.measure');call(a,'ledger.read');a.describe();self.assertEqual(a.read('evaluator'),old);self.assertEqual(setup().time_s,0.)
 def test_15_atomic_mechanical_sequence(self):
  h=setup();old=h.read('evaluator')
  for k,a in [('lock.set',{'angle_rad':0.}),('lid.set',{'lift_m':.13}),('cup.set',{'lift_m':.12}),('power.set',{'enabled':1}),('volume.calibrate',dict(r.CAL_EXAMPLE,sphere_ratio=2.)),('method.configure',dict(r.METHOD_EXAMPLE,max_runs=1,runs_to_average=3))]:
   with self.assertRaises(r.ActionRejected):call(h,k,a)
   self.assertEqual(old,h.read('evaluator'))
  until(h,'expand')
  with self.assertRaisesRegex(r.ActionRejected,'BUSY'):call(h,'lid.set',{'lift_m':.13})
  call(h,'acquisition.stop')
  with self.assertRaisesRegex(r.ActionRejected,'PRESSURE_INTERLOCK'):call(h,'seal.set',{'angle_rad':0.})
 def test_16_capacity_and_nominal_optin(self):
  for sample in [{'true_mass_kg':.5},{'accept_reference_transfer':False},{'reference_id':'invalid_secret'}]:
   h=setup(load=False);open_cup(h);old=copy.deepcopy(h.read('evaluator'))
   with self.assertRaises(r.ActionRejected):call(h,'sample.load',dict(r.SAMPLE_EXAMPLE,**sample))
   self.assertEqual(old,h.read('evaluator'));self.ledger(h)
 def test_17_six_semantic_cells_and_dual_references(self):
  for key,v in r.CELLS.items():
   h=finish(setup(method={'cell':key,'max_runs':1,'runs_to_average':1},sample={'true_mass_kg':2699.*v*.4,'open_pore_m3':v*.1}));self.assertEqual(h.phase,'succeeded',(key,h.failure));self.assertAlmostEqual(h._stats()[1],2699.,delta=.05);self.assertAlmostEqual(h.ref,8e-6 if v<=10e-6 else 50e-6);self.ledger(h)
 def test_18_automatic_and_declared_thermal_expansion(self):
  h=setup(method={'max_runs':8,'runs_to_average':3,'automatic':True},sample={'reference_temperature_k':288.15,'volume_expansion_per_k':.0001});finish(h);self.assertEqual(len(h.runs),3);self.assertAlmostEqual(h._stats()[1],2699./1.001,delta=.06);self.ledger(h)
 def test_19_permission_dedup_poll_and_schemas(self):
  h=setup();h.register_principal('a');h.register_principal('b');h.register_principal('e','evaluator');s=h.session('a');q=h.session('b');self.assertNotIn('sample.identity',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',s.describe(instance_id=h.instance_id)['capabilities']);args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');v=s.invoke(**args);self.assertEqual(v,s.invoke(**args));self.assertEqual(s.invoke(**dict(args,capability_id='acquisition.stop'))['error']['code'],'REQUEST_CONFLICT')
  with self.assertRaises(r.ActionRejected):q.poll(v['operation_id'])
  self.assertEqual(s.cancel(v['operation_id'],'cancel')['status'],'succeeded');self.assertEqual(s.poll(v['operation_id'])['status'],'cancelled');self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='deny')['error']['code'],'ACCESS_DENIED');self.assertNotIn('private_al_powder',json.dumps(s.poll(v['operation_id'])))
  for k in r.UNSUPPORTED:
   with self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED'):h.invoke(k)
  for dt in [True,-1,float('nan'),float('inf')]:
   with self.assertRaises(r.ActionRejected):h.step(dt)
 def test_20_unload_mass_and_home_release(self):
  h=finish(setup());h.step(30.);open_cup(h);self.assertEqual(call(h,'sample.unload')['returned_mass_kg'],.05);self.assertFalse(h.sample);self.ledger(h);self.assertEqual(call(h,'panel.home')['page'],'home');self.assertEqual(h.home,.0015);h.step(.1);self.assertEqual(h.home,.0015);h.step(.1);self.assertEqual(h.home,0.);call(h,'power.set',{'enabled':False});self.assertEqual(h.read()['display.pressure_text'],'')
 def test_21_three_inert_gases(self):
  for gas in ['He','N2','Ar']:
   h=finish(setup(plant={'source_gas':gas},method={'max_runs':1,'runs_to_average':1}));self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h._stats()[1],2699.,delta=.03);self.ledger(h)
 def test_22_blocked_valve_timeout(self):
  h=finish(setup(plant={'connect_conductance_mol_s_pa':1e-11},method={'stage_timeout_s':30.}));self.assertEqual(h.phase,'failed');self.assertEqual(h.failure,'STAGE_TIMEOUT');self.ledger(h)
 def test_23_reconfigure_does_not_implicitly_vent(self):
  h=setup();until(h,'expand');call(h,'acquisition.stop');before=[h.ns.copy(),h.nr.copy(),h.source];call(h,'method.configure',dict(h.method,direction='sample_first'));self.assertEqual(before,[h.ns,h.nr,h.source]);self.assertIsNone(h.cal_volume);self.ledger(h)
  empty=setup(load=False)
  with self.assertRaisesRegex(r.ActionRejected,'INSERT_ACCESS'):call(empty,'method.configure',dict(empty.method,cell='small'))
  open_cup(empty);call(empty,'method.configure',dict(empty.method,cell='small'));self.assertEqual(empty.cell,10e-6);self.ledger(empty)
 def test_24_completed_result_keeps_run_mass(self):
  h=finish(setup());old=call(h,'process.measure')['mean_density_kg_m3'];call(h,'sample.set_mass',{'mass_kg':.07});self.assertEqual(call(h,'process.measure')['mean_density_kg_m3'],old);h.step(30.);open_cup(h);call(h,'sample.unload');self.assertEqual(call(h,'process.measure')['mean_density_kg_m3'],old)
if __name__=='__main__':unittest.main()
