from pathlib import Path
import sys,unittest,copy,math,importlib.util,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import knauer_k7400s as m
OBSERVED=set()
def call(r,k,**a):
 v=r.invoke(k,**a);OBSERVED.add(k);return v
def ready(plant=None,volume=150e-9):
 r=m.KnauerK7400sRuntime();call(r,'plant.configure',**(plant or m.PLANT_EXAMPLE));call(r,'method.configure',**dict(m.METHOD_EXAMPLE,sample_volume_m3=volume));call(r,'power.set',enabled=True);r.step(300);return r
def load(r,sample):call(r,'head.position',position='raised');call(r,'sample.load',**sample);call(r,'head.position',position='measuring')
def unload(r):call(r,'head.position',position='raised');call(r,'sample.unload')
def calibrate(r,values=(0.,.4,.85)):
 for v in values:
  load(r,m.reference(v,r.method['sample_volume_m3']));call(r,'acquisition.start',purpose='calibration');r.step(130);assert r.phase=='succeeded',r.failure;call(r,'calibration.accept',known_osmolality_mol_kg=v);unload(r)
 return r
class Tests(unittest.TestCase):
 def test_01_reference_curves(self):
  r=ready();self.assertEqual(len(r.templates),61);self.assertEqual(sum(len(v['rows']) for v in r.templates.values()),529)
  for sid,t in r.templates.items():
   for row in t['rows']:
    x=call(r,'template.evaluate',substance_id=sid,molality_mol_kg=row['molality_mol_kg']);self.assertAlmostEqual(x['osmotic_coefficient'],row['osmotic_coefficient']);self.assertIn('10.1063/1.3253108',x['template_source'])
  self.assertAlmostEqual(r.phi('NaCl',.1),.933);self.assertAlmostEqual(r.phi('NaBr',.1),.935)
 def test_02_three_point_observed_measurement(self):
  r=calibrate(ready());load(r,m.SAMPLE_EXAMPLE);call(r,'acquisition.start',purpose='sample');r.step(130);x=call(r,'process.measure');self.assertEqual(x['phase'],'succeeded');self.assertAlmostEqual(x['osmolality_mol_kg'],.187,places=3);self.assertTrue(110<x['elapsed_s']<125);self.assertLess(min(v['temperature_k'] for v in x['points']),x['freezing_temperature_k']-4)
 def test_03_inventory_heat(self):
  r=calibrate(ready(),(0,.4));load(r,m.SAMPLE_EXAMPLE);call(r,'acquisition.start',purpose='sample');r.step(70);self.assertGreater(r.ice,0)
  l=call(r,'ledger.read');self.assertAlmostEqual(l['water_loaded_kg'],l['water_returned_kg']+l['water_remaining_kg'],places=15);self.assertLess(abs(l['energy_residual_j']),1e-8);self.assertLess(l['solute_residual_mol'],1e-15);r.step(60);unload(r);self.assertEqual(r.water,0);self.assertLess(abs(r._ledger()['energy_residual_j']),1e-8)
 def test_04_freeze_interlock_and_cancel(self):
  r=ready();load(r,m.reference(0));call(r,'acquisition.start',purpose='calibration');r.step(70);old=(r.water,r.H,copy.deepcopy(r.solutes));call(r,'acquisition.stop');self.assertEqual(old,(r.water,r.H,r.solutes))
  call(r,'head.position',position='raised')   # 1.1.2 sweep: lifting the head with the frozen tube on it is a hand action, not refused
  with self.assertRaises(m.ActionRejected):r.invoke('sample.unload')   # taking the frozen tube off the thermistor stays refused (manual: device defect)
  r.step(50);self.assertTrue(r.thawed);self.assertEqual(r.phase,'cancelled');unload(r)
 def test_head_lifted_during_a_run(self):
  # sweep 10-19: nothing stops a hand lifting the head during a run; out of the cooling cavity the sample is not cooled
  r=ready();load(r,m.reference(0));call(r,'acquisition.start',purpose='calibration');r.step(10);t=r.temp
  call(r,'head.position',position='raised');r.step(30);self.assertGreater(r.temp,t);self.assertEqual(r.phase,'running')
  r.step(270);self.assertEqual((r.phase,r.failure),('failed','COOLER_TIMEOUT'))   # manual 9.1: >5 min from START
  def run(lift):
   q=calibrate(ready());load(q,m.SAMPLE_EXAMPLE);call(q,'acquisition.start',purpose='sample')
   while q.stage!='measuring':q.step(1)
   if lift:call(q,'head.position',position='raised')
   q.step(130);return q
  a,b=run(False),run(True)
  self.assertEqual(a.phase,'succeeded');self.assertGreater(b.freezing,a.freezing+1.)   # the lifted sample melts: the peak is far above the freezing point
  self.assertEqual((b.phase,b.failure,b.osm),('failed','OUT_OF_RANGE',None))   # manual 9.1: measured value outside the acceptable range
 def test_05_power_pause(self):
  r=ready();load(r,m.reference(.4));call(r,'acquisition.start',purpose='calibration');r.step(67);call(r,'power.set',enabled=False);h=r.H;r.step(100);self.assertEqual(h,r.H);self.assertEqual(r.phase,'cancelled');call(r,'power.set',enabled=True);r.step(60);self.assertTrue(r.thawed)
 def test_06_warmup_and_uncalibrated(self):
  r=ready();r.invoke('power.set',enabled=False);r.invoke('power.set',enabled=True);load(r,m.reference(0))
  with self.assertRaisesRegex(m.ActionRejected,'NOT_READY'):r.invoke('acquisition.start',purpose='calibration')
  r.step(300)
  with self.assertRaisesRegex(m.ActionRejected,'NOT_CALIBRATED'):r.invoke('acquisition.start',purpose='sample')
 def test_07_setting_invalidates_calibration(self):
  r=calibrate(ready(),(0,.4));call(r,'method.configure',**dict(m.METHOD_EXAMPLE,sample_interval_s=.5));self.assertTrue(r.calibrated());call(r,'method.configure',**dict(m.METHOD_EXAMPLE,freeze_k=266.65));self.assertFalse(r.calibrated())
 def test_08_volume_50(self):
  r=ready(volume=50e-9);r.invoke('head.position',position='raised')
  with self.assertRaisesRegex(m.ActionRejected,'VOLUME_MISMATCH'):r.invoke('sample.load',**m.reference(0))
  calibrate(r,(0,.4));load(r,dict(m.SAMPLE_EXAMPLE,volume_m3=50e-9,water_mass_kg=.00005));r.invoke('acquisition.start',purpose='sample');r.step(130);self.assertEqual(r.osm,.187)
 def test_09_faults(self):
  for fault,code,rate in [('early_freeze','FREEZE',.4),('no_freeze','NO_FREEZE',.4),('none','COOLER_TIMEOUT',0.)]:
   r=ready(dict(m.PLANT_EXAMPLE,fault=fault,cooling_rate_k_s=rate));load(r,m.reference(0));r.invoke('acquisition.start',purpose='calibration');r.step(301);self.assertEqual(r.phase,'failed');self.assertEqual(r.failure,code);self.assertLess(abs(r._ledger()['energy_residual_j']),1e-8)
 def test_10_partition_invariance(self):
  a=ready();load(a,m.reference(.4));a.invoke('acquisition.start',purpose='calibration');b=copy.deepcopy(a);a.step(118.)
  for _ in range(1180):b.step(.1)
  for k in ['H','ice','temp','freezing','elapsed']:self.assertEqual(getattr(a,k),getattr(b,k))
 def test_11_invalid_atomic(self):
  r=ready()
  for a in [dict(m.METHOD_EXAMPLE,freeze_k=267.2),dict(m.METHOD_EXAMPLE,sample_interval_s=.15),dict(m.METHOD_EXAMPLE,cooling_limit_k=float('nan')),dict(m.METHOD_EXAMPLE,sample_volume_m3=True)]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(m.ActionRejected):r.invoke('method.configure',**a)
   self.assertEqual(before,r.__dict__)
  for dt in [-1,True,float('nan')]:
   with self.assertRaises(m.ActionRejected):r.step(dt)
 def test_12_observation_calibration_not_truth(self):
  r=calibrate(ready());load(r,m.SAMPLE_EXAMPLE);r.invoke('acquisition.start',purpose='sample');r.step(130);peak=r.freezing;value=r.calibrated_osm(peak);r.solutes={'NaCl':1e-7};self.assertEqual(r.calibrated_osm(peak),value);self.assertNotEqual(r.calibrated_osm(peak-.1),value)
 def test_13_mixing_and_pulse(self):
  r=ready();load(r,m.reference(.4));r.invoke('acquisition.start',purpose='calibration');r.step(130)
  with self.assertRaisesRegex(m.ActionRejected,'NOT_READY'):r.invoke('acquisition.start',purpose='calibration')
  self.assertTrue(call(r,'vibrator.pulse')['active']);r.step(5.1);self.assertFalse(r.read()['vibrator.active']);r.invoke('acquisition.start',purpose='calibration')
 def test_14_session_isolation(self):
  r=ready();r.register_principal('a');r.register_principal('b');r.register_principal('e','evaluator');a=r.session('a');b=r.session('b');self.assertNotIn('sample.identity',a.read(instance_id=r.instance_id)['observations']);self.assertNotIn('sample.load',a.describe(asset_type_id=m.ASSET_TYPE_ID)['capabilities']);self.assertIn('sample.load',r.session('e').describe(instance_id=r.instance_id)['capabilities'])
  x=dict(instance_id=r.instance_id,capability_id='head.position',arguments={'position':'parked'},request_id='1');v=a.invoke(**x);rev=r.revision;self.assertEqual(v,r.session('a').invoke(**x));self.assertEqual(rev,r.revision);x['arguments']={'position':'raised'};self.assertEqual(a.invoke(**x)['error']['code'],'REQUEST_CONFLICT')
  with self.assertRaises(m.ActionRejected):a.read(instance_id='other')
  denied=a.invoke(instance_id=r.instance_id,capability_id='ledger.read',arguments={},request_id='ledger');self.assertEqual(denied['status'],'rejected');self.assertEqual(b.read(instance_id=r.instance_id)['observations']['head.position']['value'],'parked')
 def test_15_operation_ownership_and_observation_time(self):
  r=ready();load(r,m.reference(0));r.register_principal('a');r.register_principal('b');a=r.session('a');b=r.session('b');v=a.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'purpose':'calibration'},request_id='op');op=v['operation_id'];r.step(130);self.assertEqual(a.poll(op)['status'],'succeeded')
  with self.assertRaises(m.ActionRejected):b.poll(op)
  before=a.read(instance_id=r.instance_id,state_ids=['freezing.temperature_k']);r.step(1);after=a.read(instance_id=r.instance_id,state_ids=['freezing.temperature_k']);self.assertEqual(before['observations'],after['observations'])
 def test_16_schema_and_unsupported(self):
  spec=importlib.util.spec_from_file_location('asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);r=ready();meta=r.describe();self.assertEqual(len(meta['state_slots']),36)  # 24 process slots + 12 front-panel slots (1.1.0)
  for sid,value in r.read('evaluator').items():self.assertFalse(tool.validate_value(value,meta['state_slots'][sid]['value_schema']))
  for k in m.UNSUPPORTED:
   with self.assertRaisesRegex(m.ActionRejected,'UNSUPPORTED'):r.invoke(k)
 def test_17_reject_bad_sample_and_source_range(self):
  r=ready();r.invoke('head.position',position='raised')
  for s in [dict(m.SAMPLE_EXAMPLE,components=[dict(substance_id='NaCl',molality_mol_kg=1)]),dict(m.SAMPLE_EXAMPLE,accept_temperature_transfer=False),dict(m.SAMPLE_EXAMPLE,components=m.SAMPLE_EXAMPLE['components']*2)]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(m.ActionRejected):r.invoke('sample.load',**s)
   self.assertEqual(before,r.__dict__)

class FrontPanelCommands(unittest.TestCase):
 """Device functions behind the front panel (1.1.0): settings, clock, output log, cooler, CALIBRATE fields (----, %%%%), CALIBRATE-T."""
 def run_cal(self,r,v,field):
  load(r,m.reference(v,r.method['sample_volume_m3']));call(r,'acquisition.start',purpose='calibration');r.step(130);self.assertTrue(r.read()['calibration.pending']);x=call(r,'calibration.field_accept',field=field);unload(r);return x
 def test_settings_clock_and_output_log(self):
  r=ready();self.assertEqual(r.read()['device.settings'],dict(data_output='PRINTER',result_unit='mOsmol',temp_unit='degC'))
  call(r,'device.configure',data_output='PC',result_unit='degC',temp_unit='degF');self.assertEqual(r.read()['device.settings']['temp_unit'],'degF')
  self.assertEqual(call(r,'device.set_clock',year=2015,month=3,day=23,hour=8,minute=24,second=35)['datetime'],'23.03.15 08:24:35');r.step(10);self.assertEqual(r.read()['device.datetime'],'23.03.15 08:24:45')
  with self.assertRaisesRegex(m.ActionRejected,'INVALID_DATE'):r.invoke('device.set_clock',year=2015,month=2,day=30,hour=0,minute=0,second=0)
  calibrate(r,(0,.4));load(r,m.SAMPLE_EXAMPLE);call(r,'acquisition.start',purpose='sample');r.step(130);log=r.read()['device.output_log']
  self.assertEqual([e['lines'][0] for e in log],['Calibrated 0000 mOsmol','Calibrated 0400 mOsmol','Measured 0%03d mOsmol'%round(r.osm*1000)]);self.assertEqual(log[-1]['port'],'PC')
  r.invoke('power.set',enabled=False)
  with self.assertRaisesRegex(m.ActionRejected,'POWER_OFF'):r.invoke('device.configure',data_output='PRINTER',result_unit='mOsmol',temp_unit='degC')
 def test_error_print_and_cooler(self):
  r=ready(dict(m.PLANT_EXAMPLE,fault='no_freeze'));self.assertAlmostEqual(r.read()['cooler.temperature_k'],m.STANDBY_K);load(r,m.reference(0));r.invoke('acquisition.start',purpose='calibration');r.step(30)
  self.assertLess(r.read()['cooler.temperature_k'],m.STANDBY_K-10);r.step(300);self.assertEqual(r.failure,'NO_FREEZE');self.assertEqual(r.read()['device.output_log'][-1]['lines'][0],'NO FREEZE')
 def test_calibration_fields_replace_and_off(self):
  r=ready();self.run_cal(r,0.,1);call(r,'calibration.select_field',field=2,value=.4);self.run_cal(r,.4,2);self.assertEqual(r.read()['calibration.fields'],[0.,.4,None]);self.assertTrue(r.calibrated())
  call(r,'calibration.select_field',field=2,value=.85);self.assertEqual(len(r.calibration),2)       # the old point stays until a new run on that field is accepted
  self.run_cal(r,.85,2);self.assertEqual([p['known'] for p in r.calibration],[0.,.85])
  call(r,'calibration.select_field',field=3,value=.4);self.run_cal(r,.4,3);self.assertEqual([p['known'] for p in r.calibration],[0.,.4,.85])
  call(r,'calibration.select_field',field=3,value='off');self.assertEqual([p['known'] for p in r.calibration],[0.,.85]);self.assertEqual(r.read()['calibration.fields'],[0.,.85,None])
  load(r,m.reference(.4));call(r,'acquisition.start',purpose='calibration');r.step(130);call(r,'calibration.discard');self.assertFalse(r.read()['calibration.pending'])
  with self.assertRaisesRegex(m.ActionRejected,'CALIBRATION_RUN_REQUIRED'):r.invoke('calibration.field_accept',field=2)
  self.assertEqual([p['known'] for p in r.calibration],[0.,.85])
 def test_relative_mode(self):
  r=ready();self.run_cal(r,0.,1);call(r,'calibration.select_field',field=2,value='relative');self.assertTrue(r.read()['calibration.relative_mode']);self.assertFalse(r.calibrated())
  self.run_cal(r,.4,2);self.assertTrue(r.calibrated());load(r,m.reference(.2));call(r,'acquisition.start',purpose='sample');r.step(130)
  x=r.read();self.assertIsNone(x['osmolality.mol_kg']);self.assertAlmostEqual(x['osmolality.relative_percent'],50.,delta=1.5)
 def test_pre_calibration_corrects_the_head_offset(self):
  r=ready(dict(m.PLANT_EXAMPLE,sensor_bias_k=.2));calibrate(r,(0,.4));load(r,m.reference(0));call(r,'acquisition.start',purpose='pre_calibration');r.step(130)
  self.assertEqual(r.read()['calibration.pre_result'],'successful');self.assertAlmostEqual(call(r,'calibration.pre_accept')['head_offset_k'],-.2,places=2)
  self.assertFalse(r.calibrated());self.assertIsNone(r.read()['calibration.pre_result']);unload(r);load(r,m.reference(0));call(r,'acquisition.start',purpose='calibration');r.step(130);self.assertAlmostEqual(r.freezing,273.15,places=2)
  unload(r);load(r,m.reference(.85));call(r,'acquisition.start',purpose='pre_calibration');r.step(130);self.assertEqual(r.read()['calibration.pre_result'],'not_successful')
  with self.assertRaisesRegex(m.ActionRejected,'NO_PENDING'):r.invoke('calibration.pre_accept')
if __name__=='__main__':unittest.main()
