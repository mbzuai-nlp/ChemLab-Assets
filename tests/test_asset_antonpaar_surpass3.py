import unittest,copy,math,importlib.util,json
from scripts.asset_runtime import antonpaar_surpass3 as r
spec=importlib.util.spec_from_file_location('asset_tool',r.ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);OBSERVED=set()
def call(h,k,a=None):
 v=h.invoke(k,**(a or {}));tool.validate_value(v,r.CAPS[k][1]);OBSERVED.add(k);return v
def setup(plant=None,method=None,liquid=None,sample=None):
 h=r.AntonpaarSurpass3Runtime()
 for k,a in [('plant.configure',dict(r.PLANT_EXAMPLE,**(plant or {}))),('method.configure',dict(r.METHOD_EXAMPLE,**(method or {}))),('lock.set',{'angle_rad':0.}),('cell.set',{'withdrawal_m':.095}),('sample.load',dict(r.SAMPLE_EXAMPLE,**(sample or {}))),('cell.set',{'withdrawal_m':0.}),('lock.set',{'angle_rad':1.2}),('gap.set',{'height_m':.0001}),('lid.set',{'lift_m':.105}),('electrolyte.load',dict(r.LIQUID_EXAMPLE,**(liquid or {}))),('lid.set',{'lift_m':0.}),('ph.calibrate',r.PHCAL_EXAMPLE),('conductivity.calibrate',r.COND_EXAMPLE),('power.set',{'enabled':True})]:call(h,k,a)
 return h
def finish(h):
 if h.phase!='running':call(h,'acquisition.start')
 for _ in range(1800):
  h.step(1.)
  if h.phase!='running':break
 return h
class Tests(unittest.TestCase):
 def ledger(self,h):
  l=call(h,'ledger.read')
  for k in ['volume_residual_m3','k_residual_mol','cl_residual_mol','electroneutral_residual_mol','titrant_residual_m3','gas_mole_residual']:self.assertLess(abs(l[k]),1e-13,k)
  self.assertLess(abs(l['work_residual_j']),1e-10);self.assertGreaterEqual(h.V-sum(h.chamber),-1e-15)
 def test_01_original_references(self):
  h=setup();self.assertEqual(len(h.library['substances']),70);self.assertAlmostEqual(call(h,'template.reference',{'substance_id':'nanomile_2'})['zeta_v'],-.00121);self.assertFalse(call(h,'template.reference',{'substance_id':'nanomile_2'})['applicable_to_surpass']);self.assertEqual(call(h,'template.reference',{'substance_id':'cotton_agcl'})['zeta_v'],-.019)
  with self.assertRaisesRegex(r.ActionRejected,'PARTICLE_REFERENCE_ONLY'):setup(sample={'reference_id':'nanomile_2'})
 def test_02_complete_ols_and_iep(self):
  h=finish(setup());self.assertEqual(h.phase,'succeeded');self.assertEqual(len(h.fits),16);self.assertAlmostEqual(h._iep(),4.1,places=6)
  for f in h.fits:
   expected=-.019-.01*(f['ph']-6);self.assertAlmostEqual(f['zeta_from_i_v'],expected,places=10);self.assertAlmostEqual(f['zeta_from_v_v'],expected,places=10);self.assertGreater(f['r2_i'],.999999)
  self.ledger(h);self.assertEqual({x['direction'] for x in call(h,'process.measure')['trace']},{1,-1})
 def test_03_surface_conduction_bias(self):
  h=finish(setup(plant={'surface_conductance_s':1e-6},method={'ph_targets':[6.],'cycles':1}));f=h.fits[0];self.assertAlmostEqual(f['zeta_from_i_v'],-.019,places=12);self.assertLess(abs(f['zeta_from_v_v']),abs(f['zeta_from_i_v']));self.ledger(h)
 def test_04_ideal_chemistry_titration(self):
  h=finish(setup(method={'ph_targets':[4.,8.,5.],'cycles':1}));self.assertEqual(h.phase,'succeeded');self.assertGreater(h.added[0],0);self.assertGreater(h.added[1],0);self.assertAlmostEqual(h.chemistry()[0],5.,places=6);self.ledger(h)
 def test_05_independent_pressure_work(self):
  h=setup(method={'ph_targets':[6.],'cycles':1});call(h,'acquisition.start');h.step(1.);self.assertEqual(h.stage,'ramp');self.ledger(h);p=h.plant;pv=(h.method['start_pressure_pa']+p['ambient_pa'])*p['gas_volume_m3'];expected=pv*math.log((p['gas_volume_m3']+h.moved)/p['gas_volume_m3'])-p['ambient_pa']*h.moved;self.assertAlmostEqual(h.work,expected,places=10);self.assertAlmostEqual(h.gas_n,(h.pressure+p['ambient_pa'])*(h.gas_v+h.moved)/8.314462618/p['temperature_k'],places=12)
 def test_06_cancel_partial_dose_and_poweroff(self):
  h=setup(method={'ph_targets':[3.],'cycles':1});call(h,'acquisition.start');h.step(30.);self.assertGreater(h.added[0],0);call(h,'acquisition.stop');before=[h.V,h.nk,h.ncl];h.step(20);self.assertEqual(before,[h.V,h.nk,h.ncl]);self.assertEqual(h.phase,'cancelled');self.ledger(h)
  h=setup(method={'ph_targets':[6.],'cycles':1});call(h,'acquisition.start');h.step(1);call(h,'power.set',{'enabled':False});self.assertEqual(h.phase,'cancelled');self.assertEqual(h.pressure,0);self.ledger(h)
 def test_07_stock_depletion(self):
  h=finish(setup(method={'ph_targets':[3.],'cycles':1},liquid={'acid_stock_m3':1e-7}));self.assertEqual(h.failure,'TITRANT_DEPLETED');self.assertGreater(h.added[0],0);self.ledger(h)
 def test_08_capacity_failure(self):
  h=finish(setup(method={'ph_targets':[3.],'cycles':1},liquid={'volume_m3':.00025}));self.assertEqual(h.failure,'BEAKER_CAPACITY');self.ledger(h)
 def test_09_blocked_pressure_timeout(self):
  h=finish(setup(plant={'blocked_fraction':1.},method={'ph_targets':[6.],'cycles':1,'ramp_timeout_s':10.}));self.assertEqual(h.failure,'PRESSURE_TIMEOUT');self.assertEqual(h.circulated,0);self.assertTrue(all(x['streaming_a']==0 for x in h.trace));self.ledger(h)
 def test_10_chamber_empty_failure(self):
  h=finish(setup(plant={'gas_volume_m3':1e-4},method={'ph_targets':[6.],'cycles':1,'fill_volume_m3':2e-5}));self.assertEqual(h.failure,'CHAMBER_EMPTY');self.ledger(h)
 def test_11_read_pure_isolation_partition(self):
  a=setup();b=copy.deepcopy(a);call(a,'acquisition.start');call(b,'acquisition.start');a.step(7.3)
  for _ in range(73):b.step(.1)
  self.assertEqual(a.read('evaluator'),b.read('evaluator'));old=a.read('evaluator');call(a,'process.measure');a.describe();call(a,'ledger.read');self.assertEqual(a.read('evaluator'),old);c=setup();self.assertEqual(c.time_s,0)
 def test_12_invalid_atomic_interlocks(self):
  h=setup();old=h.read('evaluator')
  for k,a in [('cell.set',{'withdrawal_m':.09}),('method.configure',dict(r.METHOD_EXAMPLE,start_pressure_pa=20000.,stop_pressure_pa=30000.)),('gap.set',{'height_m':.0001005}),('power.set',{'enabled':1}),('ph.calibrate',{'buffer_ph':[4.,4.],'measured_v':[-.2,-.2]})]:
   with self.assertRaises(r.ActionRejected):call(h,k,a)
   self.assertEqual(h.read('evaluator'),old)
  call(h,'acquisition.start')
  with self.assertRaisesRegex(r.ActionRejected,'BUSY'):call(h,'lid.set',{'lift_m':.1})
 def test_13_calibration_bias_correction(self):
  h=setup(plant={'ph_slope_v':-.06,'ph_offset_v':.01,'conductivity_gain':1.08},method={'ph_targets':[6.],'cycles':1});call(h,'ph.calibrate',{'buffer_ph':[4.,7.],'measured_v':[-.23,-.41]});call(h,'conductivity.calibrate',{'reference_s_m':.1413,'indicated_s_m':.1413*1.08});self.assertAlmostEqual(h.indicated()[0],6.,places=8);self.assertAlmostEqual(h.indicated()[1],h.chemistry()[1],places=12);h=finish(h);self.assertAlmostEqual(h.fits[0]['zeta_from_v_v'],-.019,places=10)
 def test_14_no_unbracketed_iep_and_rinse(self):
  h=finish(setup(method={'ph_targets':[6.,7.],'cycles':1}));self.assertIsNone(h._iep());h=finish(setup(method={'ph_targets':[6.],'cycles':1,'mode':'rinse'}));self.assertEqual(h.phase,'succeeded');self.assertFalse(h.fits);self.assertGreater(h.circulated,0);self.ledger(h)
 def test_15_drain_and_sample_unload(self):
  h=finish(setup(method={'ph_targets':[6.],'cycles':1}));call(h,'lid.set',{'lift_m':.105});v=call(h,'electrolyte.drain');self.assertGreater(v['volume_m3'],0);self.assertEqual(call(h,'electrolyte.drain')['volume_m3'],0);call(h,'lock.set',{'angle_rad':0.});call(h,'cell.set',{'withdrawal_m':.095});call(h,'sample.unload');self.assertFalse(h.sample);self.ledger(h)
 def test_16_electrical_limits_and_sensor_offsets(self):
  h=finish(setup(plant={'voltage_gain':1.3,'current_gain':.8,'voltage_offset_v':.01,'current_offset_a':1e-7},method={'ph_targets':[6.],'cycles':1}));self.assertAlmostEqual(h.fits[0]['zeta_from_v_v'],-.019*1.3,places=10);self.assertAlmostEqual(h.fits[0]['zeta_from_i_v'],-.019*.8,places=10)
  h=finish(setup(method={'ph_targets':[8.],'cycles':1},liquid={'kcl_mol_m3':.01},sample={'ph_slope_v':-.05}));self.assertEqual(h.failure,'ELECTRICAL_RANGE');self.ledger(h)
 def test_17_gap_flow_and_tubing(self):
  a=setup(method={'ph_targets':[6.],'cycles':1});b=setup(plant={'extra_resistance_pa_s_m3':5e10},method={'ph_targets':[6.],'cycles':1});call(a,'acquisition.start');call(b,'acquisition.start');a.step(1.);b.step(1.);self.assertGreater(a.circulated,b.circulated);self.assertLess(b.cell_pressure,b.pressure);finish(b);self.assertEqual(b.phase,'succeeded');self.assertAlmostEqual(b.fits[0]['zeta_from_i_v'],-.019,places=12)
 def test_18_permissions_dedup_schema(self):
  h=setup();h.register_principal('a');h.register_principal('b');h.register_principal('eval','evaluator');s=h.session('a');q=h.session('b');self.assertNotIn('sample.identity',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',s.describe(instance_id=h.instance_id)['capabilities']);args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');v=s.invoke(**args);self.assertEqual(v,s.invoke(**args));self.assertEqual(s.invoke(**dict(args,capability_id='acquisition.stop'))['error']['code'],'REQUEST_CONFLICT')
  with self.assertRaises(r.ActionRejected):q.poll(v['operation_id'])
  self.assertEqual(s.cancel(v['operation_id'],'cancel')['status'],'succeeded');self.assertEqual(s.poll(v['operation_id'])['status'],'cancelled');self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='forbidden')['error']['code'],'ACCESS_DENIED')
  for k in r.UNSUPPORTED:
   with self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED'):h.invoke(k)
  for dt in [True,-1,float('nan'),float('inf')]:
   with self.assertRaises(r.ActionRejected):h.step(dt)
if __name__=='__main__':unittest.main()
