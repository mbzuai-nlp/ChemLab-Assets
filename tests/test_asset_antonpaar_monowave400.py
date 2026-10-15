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
  lib=json.loads((ROOT/TEMPLATE_REL).read_text());self.assertEqual(accepted,lib['n_runtime_eligible']);self.assertGreaterEqual(accepted,50)
 def test_15_all_action_output_schemas(self):
  h=AntonpaarMonowave400Runtime()
  for cap,a in [('power.set',{'enabled':True}),('gas.configure',CAPS['gas.configure'][6]),('lid.set_lift',{'lift_m':.15}),('sample.configure',EXAMPLE),('method.configure',CAPS['method.configure'][6]),('lid.set_lift',{'lift_m':0}),('lock.set_travel',{'travel_m':.01}),('acquisition.start',{}),('acquisition.stop',{}),('reaction.measure',{}),('history.read',{}),('product.read',{})]:self.assertEqual(validate_value(h.invoke(cap,**a),CAPS[cap][1]),[],cap)
  h.invoke('lock.set_travel',travel_m=0.);h.invoke('lid.set_lift',lift_m=.15);self.assertEqual(validate_value(h.invoke('sample.unload'),CAPS['sample.unload'][1]),[])
 # ---- library v1.1: low-temperature fits, NIST liquid Cp, template.read ----
 def _load(self,sid,cp=None,noncond=100000.):
  h=AntonpaarMonowave400Runtime();h.invoke('lid.set_lift',lift_m=.15);r=h.templates[sid];dens=(r.get('liquid_density') or {}).get('value_kg_m3') or 800.
  a=dict(EXAMPLE,substance_id=sid,fit_id=r['cold_start_fit_ids'][0],volume_m3=10e-6,mass_kg=round(dens*10e-6,6),heat_capacity_j_kg_k=cp,noncondensable_pressure_pa=noncond)
  h.invoke('sample.configure',**a);h.invoke('lid.set_lift',lift_m=0.);h.invoke('lock.set_travel',travel_m=.01);h.invoke('gas.configure',**CAPS['gas.configure'][6]);h.invoke('power.set',enabled=True);return h,a
 def test_16_library_coverage_and_provenance(self):
  lib=json.loads((ROOT/TEMPLATE_REL).read_text());recs=lib['templates'];elig=[r for r in recs if r['runtime_eligible']]
  self.assertGreaterEqual(len(elig),50);self.assertEqual(len({r['cas'] for r in elig}),len(elig))
  kinds={f['kind'] for r in elig for f in r['fits'] if f['fit_id'] in r['cold_start_fit_ids']}
  self.assertTrue({'nist_antoine','composite_low_t_anchor_plus_nist_fit','nist_srd69_eos_antoine_refit'}<=kinds)
  for r in recs:
   for f in r['fits']:
    if f['kind']=='nist_antoine':continue
    d=f['derivation'];self.assertEqual(f['evidence'],'derived')
    if f['kind'].startswith('composite'):
     a=d['anchor'];self.assertTrue(a['source_name'] and a['raw_path'] and not a['extrapolated']);self.assertEqual(hashlib.sha256((ROOT/a['raw_path']).read_bytes()).hexdigest(),a['raw_sha256'])
     self.assertLessEqual(f['temperature_range_k'][0],298.15);self.assertEqual(d['status'],'accepted')
    else:self.assertTrue(d['accepted']);self.assertLessEqual(d['max_abs_ln_deviation'],.01)
   if r['dielectric_2450mhz'] or r['sealed_vessel_reference']:self.assertIn('cem_vendor_numbers_only_non_permissive',r['licence_flags'])
   if not r['runtime_eligible']:self.assertTrue(r['blocked_reason'])
  for sid in ['benzoic_acid','naphthalene','carbon_dioxide','ammonia','acetaldehyde']:self.assertFalse(lib and {x['substance_id']:x for x in recs}[sid]['runtime_eligible'],sid)
 def test_17_new_records_heat_across_classes(self):
  for sid in ['propan_2_ol','ethylene_glycol','dmf','chlorobenzene','pyridine','butyl_acetate','methyl_benzoate','triethylamine','nitromethane','heptane','dodecane','glycerol']:
   h,a=self._load(sid);r=h.templates[sid];f=h.sample['fit'];self.assertNotEqual(f.get('kind','nist_antoine'),'nist_antoine',sid)
   self.assertEqual(h.sample['heat_capacity_source'][:9],'template:',sid);self.assertAlmostEqual(h.sample['heat_capacity_j_kg_k'],r['heat_capacity']['value_j_kg_k'])
   target=min(f['runtime_temperature_range_k'][1]-5.,353.15)
   h.invoke('method.configure',**dict(CAPS['method.configure'][6],target_k=target,ramp_s=20.,hold_s=5.,cool_to_k=310.));h.invoke('acquisition.start');h.step(25.)
   v=h.read('evaluator');self.assertGreater(v['truth.temperature_k'],320.,sid)
   self.assertAlmostEqual(v['truth.liquid_kg']+v['truth.vapor_kg'],a['mass_kg'],12);self.assertAlmostEqual(v['truth.stored_energy_j'],v['truth.absorbed_energy_j']-v['truth.heat_out_j'],7)
   t=v['truth.temperature_k'];ps=1e5*10**(f['A']-f['B_k']/(t+f['C_k']));self.assertAlmostEqual(v['truth.pressure_pa'],ps+a['noncondensable_pressure_pa']*t/298.15,3)
 def test_18_template_read_all_records(self):
  h=AntonpaarMonowave400Runtime();s=h.session();before=copy.deepcopy(h.__dict__)
  for sid in h.templates:
   r=h.invoke('template.read',substance_id=sid);self.assertEqual(validate_value(r,CAPS['template.read'][1]),[],sid);self.assertEqual(r['runtime_eligible'],h.templates[sid]['runtime_eligible'])
   if r['dielectric_2450mhz']:
    d=r['dielectric_2450mhz'];self.assertAlmostEqual(d['dielectric_loss']/d['dielectric_constant'],d['tan_delta'],delta=max(.002,d['tan_delta']*.051/d['dielectric_constant']+.0006))
  self.assertEqual(before,h.__dict__)
  r=s.invoke(instance_id=h.instance_id,capability_id='template.read',arguments={'substance_id':'pyridine'},request_id='t');self.assertEqual(r['status'],'succeeded');self.assertTrue(r['result']['sealed_vessel_reference'])
  r=s.invoke(instance_id=h.instance_id,capability_id='template.read',arguments={'substance_id':'unobtainium'},request_id='u');self.assertEqual(r['status'],'rejected')
  r=h.invoke('template.read',substance_id='dmf');self.assertEqual(r['dielectric_2450mhz']['coupling_class_vendor'],'medium');self.assertEqual(r['cold_start_fit_ids'],['c0'])
 def test_19_heat_capacity_resolution(self):
  h,a=self._load('ethanol',cp=2400.);self.assertEqual(h.sample['heat_capacity_source'],'host_input');self.assertEqual(h.sample['heat_capacity_j_kg_k'],2400.)
  h,a=self._load('water');self.assertAlmostEqual(h.sample['heat_capacity_j_kg_k'],h.templates['water']['heat_capacity']['value_j_kg_k']);self.assertTrue(4100<h.sample['heat_capacity_j_kg_k']<4250)
  missing=[sid for sid,r in loaded().templates.items() if r['runtime_eligible'] and not r.get('heat_capacity')];self.assertTrue(missing)
  h=AntonpaarMonowave400Runtime();h.invoke('lid.set_lift',lift_m=.15);before=h.debug_snapshot();r=h.templates[missing[0]]
  with self.assertRaises(ActionRejected):h.invoke('sample.configure',**dict(EXAMPLE,substance_id=missing[0],fit_id=r['cold_start_fit_ids'][0],heat_capacity_j_kg_k=None))
  self.assertEqual(before,h.debug_snapshot())
  h.invoke('sample.configure',**dict(EXAMPLE,substance_id=missing[0],fit_id=r['cold_start_fit_ids'][0],heat_capacity_j_kg_k=1900.));self.assertEqual(h.sample['heat_capacity_source'],'host_input')
 def test_20_low_temperature_fit_never_extrapolates(self):
  h,a=self._load('hexan_1_ol');f=h.sample['fit'];hi=f['runtime_temperature_range_k'][1]
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(CAPS['method.configure'][6],target_k=hi+1.))
  h=AntonpaarMonowave400Runtime();h.invoke('lid.set_lift',lift_m=.15);r=h.templates['hexanoic_acid'];self.assertFalse(r['runtime_eligible'])
  for f in r['fits']:
   with self.assertRaises(ActionRejected):h.invoke('sample.configure',**dict(EXAMPLE,substance_id='hexanoic_acid',fit_id=f['fit_id']))
if __name__=='__main__':unittest.main()
