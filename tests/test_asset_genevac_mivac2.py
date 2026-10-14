import copy,hashlib,importlib.util,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.genevac_mivac2 import *
def prepared(instance='mivac_01',plant=None,method=None,sample=None):
 r=GenevacMivac2Runtime(instance);r.invoke('plant.configure',**(plant or PLANT_EXAMPLE));r.invoke('rotor.configure',configuration='two_swing_six_shallow',accessory_compatibility_reviewed=True);r.invoke('method.configure',**(method or METHOD_EXAMPLE));r.invoke('sample.configure',**(sample or example_sample()));r.invoke('lid.set_open',open=False);r.invoke('power.set',enabled=True);return r
class MiVacTests(unittest.TestCase):
 def conserve(self,r):
  for row in r.records:self.assertAlmostEqual(row['initial_mol'],row['liquid_mol']+row['captured_mol']+row['exhaust_mol'],places=13)
  self.assertLessEqual(r._mass('captured_mol'),r.plant['trap_capacity_kg']+1e-12)
 def reject_unchanged(self,r,cap,args):
  before=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke(cap,**args)
  self.assertEqual(before,r.debug_snapshot())
 def test_01_real_templates_raw_sha_and_ranges(self):
  r=GenevacMivac2Runtime();self.assertGreaterEqual(len(r.templates),50);self.assertEqual(len(r.templates),len({x['cas'] for x in r.templates.values()}))
  for row in r.templates.values():
   self.assertEqual(row['sha256'],hashlib.sha256((ROOT/row['raw_source']).read_bytes()).hexdigest())
   for fit in row['fits']:
    for point in fit['reference_points']:self.assertAlmostEqual(r._psat(row['substance_id'],fit['fit_id'],point['temperature_k']),point['pressure_pa'],places=7)
 def test_02_all_templates_execute_without_extrapolation(self):
  library=GenevacMivac2Runtime().templates
  for sid,row in library.items():
   f=row['fits'][0];t=sum(f['runtime_temperature_range_k'])/2;s=example_sample()
   for p in s['plates']:p['species']=[dict(substance_id=sid,fit_id=f['fit_id'],amount_mol=.01)]
   r=prepared(plant=dict(PLANT_EXAMPLE,initial_temperature_k=t),method=dict(METHOD_EXAMPLE,temperature_k=t,duration_s=15.),sample=s);r.invoke('acquisition.start');r.step(15.);self.assertEqual(r.phase,'succeeded',sid);self.conserve(r)
 def test_03_multisolvent_mass_nonvolatile_conserved(self):
  r=prepared();r.invoke('acquisition.start');r.step(60.);self.conserve(r);self.assertLess(r._fraction(),1.);self.assertGreater(r._mass('captured_mol'),0.);self.assertAlmostEqual(r.invoke('product.read')['nonvolatile_mass_kg'],.0006)
 def test_04_finite_trap_exhaust_not_lost(self):
  r=prepared(plant=dict(PLANT_EXAMPLE,trap_capacity_kg=1e-6,trap_efficiency=1.));r.invoke('acquisition.start');r.step(60.);self.conserve(r);self.assertAlmostEqual(r._mass('captured_mol'),1e-6);self.assertGreater(r._mass('exhaust_mol'),0.)
 def test_05_lid_interlock_and_spin_down(self):
  r=prepared();r.invoke('acquisition.start');r.step(20.);r.invoke('acquisition.stop');self.reject_unchanged(r,'lid.set_open',dict(open=True));r.step(30.);self.assertFalse(r._locked());r.invoke('lid.set_open',open=True);self.assertTrue(r.open)
 def test_06_cancel_preserves_partial_effects(self):
  r=prepared();r.invoke('acquisition.start');r.step(30.);before=copy.deepcopy(r.records);r.invoke('acquisition.stop');r.step(30.);self.assertEqual(before,r.records);self.assertLess(r._fraction(),1.);self.conserve(r)
 def test_07_resume_accumulated_runtime(self):
  r=prepared(method=dict(METHOD_EXAMPLE,duration_s=30.));r.invoke('acquisition.start');r.step(20.);r.invoke('acquisition.stop');r.step(20.);elapsed=r.elapsed_s;r.invoke('acquisition.start');r.step(10.);self.assertAlmostEqual(elapsed,20.);self.assertEqual(r.phase,'succeeded');self.conserve(r)
 def test_08_read_does_not_step(self):
  r=prepared();before=r.debug_snapshot();r.read();r.session().read(instance_id=r.instance_id);r.invoke('evaporation.measure');r.invoke('history.read');self.assertEqual(before,r.debug_snapshot())
 def test_09_temperature_set_not_instant(self):
  r=prepared();t=r.temperature;r.invoke('temperature.set',temperature_k=323.15);self.assertEqual(r.temperature,t);r.invoke('acquisition.start');r.step(1.);self.assertGreater(r.temperature,t);self.assertLess(r.temperature,323.15)
 def test_10_pressure_controller_optional(self):
  r=GenevacMivac2Runtime();r.invoke('plant.configure',**dict(PLANT_EXAMPLE,pressure_controller_installed=False));self.reject_unchanged(r,'method.configure',METHOD_EXAMPLE);r.invoke('method.configure',**dict(METHOD_EXAMPLE,target_pressure_pa=None));self.assertIsNone(r.method['target_pressure_pa'])
 def test_11_limits_nan_bool_and_integer(self):
  r=prepared()
  for t in [303.14,353.16,float('nan'),float('inf'),True]:self.reject_unchanged(r,'temperature.set',dict(temperature_k=t))
  for dt in [-1.,float('nan'),float('inf'),True]:
   before=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.step(dt)
   self.assertEqual(before,r.debug_snapshot())
  bad=example_sample();bad['plates'][0]['plate_index']=.5
  with self.assertRaises(ActionRejected):validate(SAMPLE,bad)
 def test_12_reference_boundary_and_unknown(self):
  r=prepared();self.reject_unchanged(r,'temperature.set',dict(temperature_k=353.15));self.reject_unchanged(r,'reference.lookup',dict(substance_id='water',fit_id='0',temperature_k=303.15));self.reject_unchanged(r,'reference.lookup',dict(substance_id='unobtainium',fit_id='0',temperature_k=313.15))
 def test_13_start_and_busy_guards(self):
  r=GenevacMivac2Runtime();self.reject_unchanged(r,'acquisition.start',{});r=prepared();r.invoke('lid.set_open',open=True);self.reject_unchanged(r,'acquisition.start',{});r.invoke('lid.set_open',open=False);r.invoke('acquisition.start');self.reject_unchanged(r,'temperature.set',dict(temperature_k=313.15));self.reject_unchanged(r,'rotor.configure',dict(configuration='two_swing_six_shallow',accessory_compatibility_reviewed=True))
 def test_14_balance_rejects_before_start(self):
  s=example_sample();s['plates'][0]['tare_mass_kg']+=.002;r=prepared(sample=s);self.reject_unchanged(r,'acquisition.start',{})
 def test_15_poweroff_spin_down_retains_solvent(self):
  r=prepared();r.invoke('acquisition.start');r.step(30.);before=copy.deepcopy(r.records);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');r.step(20.);self.assertEqual(before,r.records);self.assertEqual(r.speed,0.)
 def test_16_timeout_and_duration(self):
  for timeout,duration,phase in [(10.,20.,'failed'),(20.,10.,'succeeded')]:
   r=prepared(method=dict(METHOD_EXAMPLE,timeout_s=timeout,duration_s=duration));r.invoke('acquisition.start');r.step(25.);self.assertEqual(r.phase,phase);self.conserve(r)
 def test_17_native_auth_filter_and_write(self):
  r=prepared();s=r.session();meta=s.describe(instance_id=r.instance_id);self.assertNotIn('plant.configure',meta['capabilities']);self.assertFalse(any(k.startswith('truth.') for k in s.read(instance_id=r.instance_id)['observations']))
  response=s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(response['status'],'rejected');self.assertNotIn('records',json.dumps(response));self.assertEqual(s.write(instance_id=r.instance_id,state_id='thermal.target_k',value=313.15,request_id='write')['status'],'succeeded')
  with self.assertRaises(ActionRejected):s.read(instance_id=r.instance_id,state_ids=['truth.liquid_mol'])
 def test_18_native_dedup_conflict_async_owner(self):
  r=prepared();s=r.session();args=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');a=s.invoke(**args);self.assertEqual(a,s.invoke(**args));op=a['operation_id'];self.assertEqual(a['status'],'accepted');r.step(20.);self.assertEqual(s.poll(op)['status'],'running');r.register_principal('other');
  with self.assertRaises(ActionRejected):r.session('other').poll(op)
  b=s.invoke(instance_id=r.instance_id,capability_id='acquisition.stop',arguments={},request_id='start');self.assertEqual(b['status'],'rejected');self.assertEqual(s.cancel(op,'cancel')['status'],'cancelled')
 def test_19_dual_instance_independent(self):
  a=prepared('a');b=prepared('b');old=b.debug_snapshot();a.invoke('acquisition.start');a.step(20.);self.assertEqual(b.debug_snapshot(),old);self.assertNotEqual(a.records,b.records)
 def test_20_programs_explicit_duty(self):
  pressures=[]
  for program in ['water','alcohol','other']:
   r=prepared(plant=dict(PLANT_EXAMPLE,water_pump_duty=.2,alcohol_pump_duty=.5,program_period_s=20.),method=dict(METHOD_EXAMPLE,program=program));r.invoke('acquisition.start');r.step(18.);pressures.append(r.pressure);self.conserve(r)
  self.assertGreater(pressures[0],pressures[2]);self.assertGreater(pressures[1],pressures[2])
 def test_21_heater_timer_freezes_prescribed_temperature(self):
  r=prepared(method=dict(METHOD_EXAMPLE,heating_time_s=1.));r.invoke('acquisition.start');r.step(2.);t=r.temperature;r.step(2.);self.assertAlmostEqual(t,r.temperature)
 def test_22_endpoint_partial_residual_not_dry_claim(self):
  r=prepared(method=dict(METHOD_EXAMPLE,endpoint_remaining_fraction=.99));r.invoke('acquisition.start');r.step(60.);self.assertEqual(r.phase,'succeeded');self.assertGreater(r._fraction(),0.);self.conserve(r)
 def test_23_schema_actual_results_and_metadata(self):
  spec=importlib.util.spec_from_file_location('mivac_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);meta=export_genevac_mivac2_contract_metadata();r=prepared()
  for cap in ['evaporation.measure','product.read','history.read','reference.lookup']:
   args=CAPS[cap][6];out=r.invoke(cap,**args);self.assertEqual(tool.validate_value(out,CAPS[cap][1]),[],cap)
  for k,v in r._values().items():self.assertEqual(tool.validate_value(v,SLOTS[k][0]),[],k)
  self.assertEqual(len(meta['capabilities']),13)
 def test_24_step_partition_close(self):
  a=prepared();b=prepared();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(30.)
  for _ in range(300):b.step(.1)
  self.assertAlmostEqual(a._fraction(),b._fraction(),places=10);self.assertAlmostEqual(a.pressure,b.pressure,places=7);self.conserve(a);self.conserve(b)
if __name__=='__main__':unittest.main()
