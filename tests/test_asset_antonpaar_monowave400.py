import copy,hashlib,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime.antonpaar_monowave400 import *
from asset_tool import validate_value,lint_schema
def loaded(i='test'):
 h=AntonpaarMonowave400Runtime(i);h.invoke('lid.set_lift',lift_m=.15);h.invoke('sample.configure',**EXAMPLE);h.invoke('lid.set_lift',lift_m=0.);h.invoke('lock.set_travel',travel_m=.01);h.invoke('gas.configure',**CAPS['gas.configure'][6]);h.invoke('power.set',enabled=True);h.invoke('method.configure',**CAPS['method.configure'][6]);return h
class MonowaveTests(unittest.TestCase):
 def test_01_schema_all_slots_and_capabilities(self):
  meta=export_antonpaar_monowave400_contract_metadata();h=loaded()
  for k,(schema,*_) in SLOTS.items():self.assertEqual(validate_value(h.read('evaluator')[k],schema),[],k)
  for k,c in meta['capabilities'].items():
   self.assertEqual(lint_schema(c['input_schema']),[],k);self.assertEqual(lint_schema(c['output_schema']),[],k)
  for k in ['reaction.measure','history.read','product.read']:self.assertEqual(validate_value(h.invoke(k),CAPS[k][1]),[],k)
 def test_02_invalid_arguments_atomic(self):
  h=loaded()
  for cap,a in [('method.configure',dict(CAPS['method.configure'][6],power_limit_w=851)),('method.configure',dict(CAPS['method.configure'][6],stir_rpm=1201)),('method.configure',dict(CAPS['method.configure'][6],pressure_limit_pa=3000001)),('method.configure',dict(CAPS['method.configure'][6],target_k=float('nan'))),('power.set',{'enabled':1}),('lid.set_lift',{'lift_m':.16})]:
   before=h.debug_snapshot();rev=h.revision
   with self.assertRaises(ActionRejected):h.invoke(cap,**a)
   self.assertEqual(before,h.debug_snapshot());self.assertEqual(rev,h.revision)
 def test_03_pure_read(self):
  h=loaded();before=copy.deepcopy(h.__dict__)
  for _ in range(3):h.read();h.describe();h.invoke('reaction.measure');h.invoke('history.read');h.invoke('product.read')
  self.assertEqual(before,h.__dict__)
 def test_04_closed_mass_energy(self):
  h=loaded();h.invoke('acquisition.start');h.step(8.);r=h.read('evaluator')
  self.assertGreater(r['truth.temperature_k'],298.15);self.assertAlmostEqual(r['truth.liquid_kg']+r['truth.vapor_kg'],EXAMPLE['mass_kg'],12)
  self.assertAlmostEqual(r['truth.stored_energy_j'],r['truth.absorbed_energy_j']-r['truth.heat_out_j'],8)
  self.assertEqual(h.invoke('product.read')['conversion_fraction'],None)
 def test_05_async_completes_and_unloads(self):
  h=loaded();h.register_principal('e','evaluator');s=h.session('e');op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id']
  h.step(120);self.assertEqual(s.poll(op)['status'],'succeeded');self.assertLessEqual(h.temperature,301.15)
  h.invoke('lock.set_travel',travel_m=0);h.invoke('lid.set_lift',lift_m=.15);r=h.invoke('sample.unload');self.assertTrue(r['attachment_event'].startswith('host_release:'));self.assertEqual(h.read('evaluator')['sample.removed_kg'],.008)
 def test_06_cancel_preserves_residual(self):
  h=loaded();s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')['operation_id'];h.step(1);t=h.temperature;u=h.absorbed
  s.cancel(op,'cancel');self.assertEqual(h.phase,'cancelled');self.assertEqual(h.temperature,t);self.assertEqual(h.absorbed,u);self.assertEqual(h.output_w,0);h.step(2);self.assertLess(h.temperature,t)
 def test_07_identity_truth_and_idempotency(self):
  h=loaded();s=h.session();h.register_principal('e','evaluator');e=h.session('e')
  with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.temperature_k'])
  r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(r['status'],'rejected')
  self.assertNotIn('truth.temperature_k',s.describe(instance_id=h.instance_id)['state_slots'])
  a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='x');r=s.invoke(**a);rev=h.revision;self.assertEqual(r,s.invoke(**a));self.assertEqual(rev,h.revision)
  with self.assertRaises(ActionRejected):e.poll(r['operation_id'])
  a['arguments']={'bogus':True};self.assertEqual(s.invoke(**a)['status'],'rejected')
 def test_08_pressure_trip_and_hot_lock(self):
  h=loaded();a=dict(CAPS['method.configure'][6],target_k=350.,pressure_limit_pa=120000.);h.invoke('method.configure',**a);h.invoke('acquisition.start');h.step(15.)
  self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'pressure_limit');self.assertEqual(h.output_w,0)
  # A distinct warmer fixture verifies the retained hot/pressure interlock.
  h=loaded();h.invoke('method.configure',**dict(a,pressure_limit_pa=3e6,hold_s=100.));h.invoke('acquisition.start');h.step(20);h.invoke('acquisition.stop');self.assertFalse(h._safe())
  before=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('lock.set_travel',travel_m=0.)
  self.assertEqual(before,h.debug_snapshot())
 def test_09_gas_loss_power_and_busy(self):
  h=loaded();h.invoke('acquisition.start');h.step(1)
  with self.assertRaises(ActionRejected):h.invoke('lid.set_lift',lift_m=.15)
  h.invoke('gas.configure',connected=True,pressure_pa=450000.,flow_l_min=200.);self.assertEqual(h.fault,'cooling_gas_lost');self.assertEqual(h.output_w,0)
  h.invoke('power.set',enabled=False);self.assertIsNone(h.invoke('reaction.measure')['temperature_k'])
 def test_10_instance_lockstep_and_step_validation(self):
  a=loaded('a');b=loaded('b');a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(2.)
  for _ in range(20):b.step(.1)
  for k in SLOTS:
   if isinstance(a.read('evaluator')[k],float):self.assertAlmostEqual(a.read('evaluator')[k],b.read('evaluator')[k],7,k)
   else:self.assertEqual(a.read('evaluator')[k],b.read('evaluator')[k],k)
  a.invoke('power.set',enabled=False);self.assertTrue(b.powered)
  for v in [0,-1,True,float('inf'),float('nan')]:
   with self.assertRaises(ValueError):a.step(v)
 def test_11_source_templates(self):
  h=loaded();self.assertGreaterEqual(len(h.templates),50)
  for r in h.templates.values():
   self.assertEqual(hashlib.sha256((ROOT/r['raw_source']).read_bytes()).hexdigest(),r['sha256']);self.assertTrue(r['source'].startswith('https://webbook.nist.gov/'))
   for f in r['fits']:
    for point in f['reference_points']:self.assertAlmostEqual(1e5*10**(f['A']-f['B_k']/(point['temperature_k']+f['C_k'])),point['pressure_pa'],7)
  f=h.templates['ethanol']['fits'][1];self.assertAlmostEqual(1e5*10**(f['A']-f['B_k']/(298.15+f['C_k'])),7869.08504803628,5)
 def test_12_fit_and_container_guard(self):
  h=loaded();before=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(CAPS['method.configure'][6],target_k=573.15))
  self.assertEqual(before,h.debug_snapshot())
  h=AntonpaarMonowave400Runtime();h.invoke('lid.set_lift',lift_m=.15)
  for change in [dict(fit_id='missing'),dict(compatible_sealed_g30=False),dict(volume_m3=21e-6),dict(temperature_k=400.)]:
   with self.assertRaises(ActionRejected):h.invoke('sample.configure',**dict(EXAMPLE,**change))
   self.assertIsNone(h.sample)
 def test_13_record_filter_and_zero_power(self):
  h=loaded();h.invoke('method.configure',**dict(CAPS['method.configure'][6],power_limit_w=0));h.invoke('acquisition.start');h.step(2);self.assertAlmostEqual(h.temperature,298.15,7)
  hist=h.invoke('history.read');self.assertTrue(hist['records']);self.assertIsNone(hist['records'][0]['temperature_k']);self.assertEqual(validate_value(hist,CAPS['history.read'][1]),[])
  for k,r in h.session().read(instance_id=h.instance_id)['observations'].items():self.assertFalse(k.startswith('truth.'))
 def test_14_all_template_cold_loading_guards(self):
  records=loaded().templates;accepted=0
  for r in records.values():
   h=AntonpaarMonowave400Runtime();h.invoke('lid.set_lift',lift_m=.15)
   fit_id=r['cold_start_fit_ids'][0] if r['runtime_eligible'] else r['fits'][0]['fit_id']
   a=dict(EXAMPLE,substance_id=r['substance_id'],fit_id=fit_id,noncondensable_pressure_pa=0.)
   if r['runtime_eligible']:h.invoke('sample.configure',**a);self.assertTrue(h.sample);accepted+=1
   else:
    with self.assertRaises(ActionRejected):h.invoke('sample.configure',**a)
    self.assertIsNone(h.sample)
  self.assertEqual(accepted,34)
 def test_15_all_action_output_schemas(self):
  h=AntonpaarMonowave400Runtime()
  for cap,a in [('power.set',{'enabled':True}),('gas.configure',CAPS['gas.configure'][6]),('lid.set_lift',{'lift_m':.15}),('sample.configure',EXAMPLE),('method.configure',CAPS['method.configure'][6]),('lid.set_lift',{'lift_m':0}),('lock.set_travel',{'travel_m':.01}),('acquisition.start',{}),('acquisition.stop',{}),('reaction.measure',{}),('history.read',{}),('product.read',{})]:self.assertEqual(validate_value(h.invoke(cap,**a),CAPS[cap][1]),[],cap)
  h.invoke('lock.set_travel',travel_m=0.);h.invoke('lid.set_lift',lift_m=.15);self.assertEqual(validate_value(h.invoke('sample.unload'),CAPS['sample.unload'][1]),[])
if __name__=='__main__':unittest.main()
