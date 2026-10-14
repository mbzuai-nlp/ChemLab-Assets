import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
import asset_tool
from asset_runtime.hepatochem_photoredoxbox import *
def prepared(rack='8x4ml',source=None,thermal=None,**sample):
 h=HepatochemPhotoredoxboxRuntime();h.invoke('lid.set_lift',lift_m=.12);h.invoke('rack.configure',mode=rack);h.invoke('thermal.configure',**(thermal or CAPS['thermal.configure'][6]));led=copy.deepcopy(source or LED_EXAMPLE)
 if rack=='4x20ml':led['capture_fraction']=[r[:4] for r in led['capture_fraction']]
 h.invoke('led.configure',**led);h.invoke('sample.configure',**dict(EXAMPLE,**sample));h.invoke('lid.set_lift',lift_m=0.);h.invoke('power.set',enabled=True);return h
class PhotoRedoxTest(unittest.TestCase):
 def test_contract_schemas(self):
  m=export_hepatochem_photoredoxbox_contract_metadata()
  self.assertEqual(len(m['capabilities']),15)
  for cap in m['capabilities'].values():
   self.assertFalse(asset_tool.lint_schema(cap['input_schema']));self.assertFalse(asset_tool.lint_schema(cap['output_schema']));self.assertFalse(asset_tool.validate_value(cap['example_arguments'],cap['input_schema']))
 def test_72_real_spectra_and_anchor_units(self):
  h=HepatochemPhotoredoxboxRuntime();self.assertEqual(len(h.templates),72)
  for t in h.templates.values():
   self.assertEqual(t['evidence'],'derived');self.assertEqual(t['measurement_evidence'],'measured_literature');self.assertTrue(t['conditions']['solvent']);self.assertIsNone(t['photochemical_quantum_yield']);self.assertIn('photochemcad.com',t['source']);self.assertAlmostEqual(h.epsilon(t['substance_id'],t['calibration']['wavelength_nm']),.1*t['calibration']['epsilon_L_mol_cm'],7)
  self.assertAlmostEqual(h.epsilon('photochemcad_H01',275),1100.)
 def test_every_template_drives_absorption(self):
  records=HepatochemPhotoredoxboxRuntime().templates.values()
  for t in records:
   self.assertTrue(t['runtime_wavelengths_nm']);wave=t['runtime_wavelengths_nm'][0];led=copy.deepcopy(LED_EXAMPLE)
   for s in led['sources']:s['wavelength_m']=float(str(wave)+'e-9')
   h=prepared(source=led,substance_id=t['substance_id'],solvent=t['conditions']['solvent']);h.invoke('acquisition.start');h.step(.04);self.assertGreater(h.run_incident,0);self.assertGreaterEqual(h.samples[0]['absorbed_energy_j'],0)
 def test_four_source_dose_and_photon_equation(self):
  h=prepared();h.invoke('acquisition.start');h.step(2.3);self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.elapsed_s,2.,12);self.assertAlmostEqual(h.run_emitted,4.,11);self.assertAlmostEqual(h.run_incident,.2,12)
  absorbed=.2*.9*(1-10**(-1100*.1*.01));self.assertAlmostEqual(h.samples[0]['absorbed_energy_j'],absorbed,12);self.assertAlmostEqual(h.samples[0]['absorbed_photons_mol'],absorbed*275e-9/(NA*H*C),15)
 def test_optical_and_thermal_balance(self):
  h=prepared();h.invoke('acquisition.start');h.step(2.4);e=h.energy
  self.assertAlmostEqual(e['emitted'],sum(e[k] for k in ['absorbed','container_loss','transmitted','uncaptured']),10);self.assertAlmostEqual(e['thermal_input'],h._stored()+e['ambient_loss'],8);self.assertAlmostEqual(e['nonthermal'],.2*e['absorbed'],12)
 def test_zero_light_no_active_heater(self):
  led=copy.deepcopy(LED_EXAMPLE)
  for s in led['sources']:s['intensity']=0.
  h=prepared(source=led);h.invoke('temperature.set',target_k=333.15);h.invoke('acquisition.start');h.step(2.);self.assertEqual(h.chamber_k,298.15);self.assertEqual(h.samples[0]['temperature_k'],298.15);self.assertEqual(h.run_emitted,0);self.assertEqual(h.energy['thermal_input'],0)
 def test_cooling_and_target_only(self):
  h=prepared();h.invoke('temperature.set',target_k=298.15);h.invoke('stir.set_speed',speed_rad_s=600*RPM);self.assertEqual(h.actual_speed,0);self.assertEqual(h.chamber_k,298.15);h.invoke('acquisition.start');h.step(2.);self.assertGreater(h.fan,0);t=h.chamber_k;h.step(30.);self.assertLess(h.chamber_k,t);self.assertAlmostEqual(h.energy['thermal_input'],h._stored()+h.energy['ambient_loss'],7)
 def test_both_racks_capacity_and_mass_volume(self):
  for rack,(n,volume) in RACKS.items():
   h=prepared(rack,volume_m3=volume);h.invoke('lid.set_lift',lift_m=.12)
   for i in range(1,n):h.invoke('sample.configure',**dict(EXAMPLE,slot=i,container_id='v'+str(i),volume_m3=volume))
   self.assertEqual(len(h.samples),n)
   with self.assertRaises(ActionRejected):h.invoke('sample.configure',**dict(EXAMPLE,slot=n if n<8 else 7,container_id='extra'))
   with self.assertRaises(ActionRejected):h.invoke('rack.configure',mode=rack)
   for i in range(n):h.invoke('sample.unload',slot=i)
   for ax in ['kg','m3']:
    v=h.read('evaluator');self.assertAlmostEqual(v['sample.input_'+ax],v['sample.retained_'+ax]+v['sample.removed_'+ax],12)
 def test_empty_slots_do_not_redistribute_light(self):
  a=prepared();b=prepared();b.invoke('lid.set_lift',lift_m=.12);b.invoke('sample.configure',**dict(EXAMPLE,slot=1,container_id='second'));b.invoke('lid.set_lift',lift_m=0.)
  for h in [a,b]:h.invoke('acquisition.start');h.step(.2)
  self.assertAlmostEqual(a.samples[0]['incident_energy_j'],b.samples[0]['incident_energy_j'],13);self.assertAlmostEqual(b.run_incident,2*a.run_incident,13)
 def test_no_spectral_extrapolation_or_solvent_substitution(self):
  h=prepared();h.invoke('lid.set_lift',lift_m=.12);led=copy.deepcopy(LED_EXAMPLE)
  for s in led['sources']:s['wavelength_m']=808e-9
  h.invoke('led.configure',**led);h.invoke('lid.set_lift',lift_m=0.);old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
  self.assertEqual(old,h.debug_snapshot());h.invoke('lid.set_lift',lift_m=.12)
  with self.assertRaises(ActionRejected):h.invoke('sample.configure',**dict(EXAMPLE,slot=1,container_id='b',solvent='water'))
 def test_atomic_bad_arguments_and_interlock(self):
  h=prepared();old=h.debug_snapshot()
  for cap,a in [('lid.set_lift',{'lift_m':True}),('temperature.set',{'target_k':290.}),('sample.unload',{'slot':.0}),('sample.unload',{'slot':-1}),('stir.set_speed',{'speed_rad_s':float('nan')})]:
   with self.assertRaises(ActionRejected):h.invoke(cap,**a)
   self.assertEqual(old,h.debug_snapshot())
  h.invoke('acquisition.start');old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('lid.set_lift',lift_m=.12)
  self.assertEqual(old,h.debug_snapshot())
 def test_explicit_power_and_capture_constraints(self):
  h=HepatochemPhotoredoxboxRuntime();h.invoke('lid.set_lift',lift_m=.12);h.invoke('thermal.configure',**CAPS['thermal.configure'][6]);bad=copy.deepcopy(LED_EXAMPLE);bad['capture_fraction'][0]=[.2]*8
  with self.assertRaises(ActionRejected):h.invoke('led.configure',**bad)
  bad=copy.deepcopy(LED_EXAMPLE)
  for s in bad['sources']:s['radiant_power_w']=25.
  with self.assertRaises(ActionRejected):h.invoke('led.configure',**bad)
  bad=copy.deepcopy(LED_EXAMPLE);bad['sources']=bad['sources'][:3]
  with self.assertRaises(ActionRejected):h.invoke('led.configure',**bad)
 def test_cancel_restart_and_removed_heat(self):
  h=prepared();h.invoke('acquisition.start');h.step(.2);absorbed=h.samples[0]['absorbed_energy_j'];h.invoke('acquisition.stop');self.assertEqual(h.phase,'cancelled');self.assertEqual(h.samples[0]['absorbed_energy_j'],absorbed);h.invoke('acquisition.start');self.assertEqual(h.run_incident,0);h.step(.2);self.assertGreater(h.samples[0]['absorbed_energy_j'],absorbed);h.invoke('power.set',enabled=False);h.invoke('lid.set_lift',lift_m=.12);h.invoke('sample.unload',slot=0);h.step(1.);self.assertAlmostEqual(h.energy['thermal_input'],h._stored()+h.energy['ambient_loss'],8)
 def test_ideal_overtemperature_failure(self):
  h=prepared(initial_temperature_k=333.149,heat_capacity_j_k=.1,sample_ua_w_k=0.);h.invoke('acquisition.start');h.step(.2);self.assertEqual(h.phase,'failed');self.assertIn('not_certified',h.fault);q=h.run_emitted;h.step(.2);self.assertEqual(q,h.run_emitted)
 def test_reads_pure_and_unknown_products(self):
  h=prepared();h.invoke('acquisition.start');h.step(.3);old=copy.deepcopy(h.__dict__)
  for _ in range(3):h.read();h.describe();h.invoke('irradiation.measure');h.invoke('history.read');h.invoke('product.read')
  self.assertEqual(old,h.__dict__);self.assertIsNone(h.invoke('irradiation.measure')['yield_fraction']);self.assertIsNone(h.invoke('product.read')['samples'][0]['conversion_fraction'])
 def test_instance_isolation_and_agent_truth_filter(self):
  a=prepared();b=HepatochemPhotoredoxboxRuntime('other');a.invoke('acquisition.start');a.step(.2);self.assertFalse(b.powered);self.assertFalse(b.samples);s=a.session();self.assertNotIn('truth.samples',s.read(instance_id=a.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=a.instance_id)['capabilities'])
  for k in ['product.read','led.configure']:
   r=s.invoke(instance_id=a.instance_id,capability_id=k,arguments={},request_id=k);self.assertEqual(r['error']['code'],'ACCESS_DENIED')
 def test_native_async_dedup_window_cancel_and_write(self):
  h=prepared();s=h.session();args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**args);self.assertEqual(r['status'],'accepted');self.assertEqual(s.invoke(**args),r);h.step(.2);self.assertEqual(s.poll(r['operation_id'])['status'],'running');obs=s.read(instance_id=h.instance_id,state_ids=['optics.incident_energy_j'])['observations']['optics.incident_energy_j'];self.assertEqual(obs['sample_window_s'],[h.run_start_s,h.run_start_s+h.elapsed_s]);self.assertEqual(s.cancel(r['operation_id'],'cancel')['status'],'cancelled');self.assertGreater(h.run_incident,0);self.assertEqual(s.write(instance_id=h.instance_id,state_id='stir.target_rad_s',value=600*RPM,request_id='speed')['status'],'succeeded')
 def test_all_actual_outputs_against_schemas(self):
  h=HepatochemPhotoredoxboxRuntime()
  def ck(k,a=None):
   r=h.invoke(k,**(CAPS[k][6] if a is None else a));self.assertFalse(asset_tool.validate_value(r,CAPS[k][1]),k);return r
  for k in ['lid.set_lift','rack.configure','thermal.configure','led.configure','sample.configure','power.set','method.configure','temperature.set','stir.set_speed']:ck(k)
  ck('lid.set_lift',{'lift_m':0.});ck('acquisition.start');h.step(.2)
  for k in ['irradiation.measure','history.read','product.read','acquisition.stop']:ck(k)
  for k,v in h.read('evaluator').items():self.assertFalse(asset_tool.validate_value(v,SLOTS[k][0]),k)
  ck('stir.set_speed',{'speed_rad_s':0.});h.step(.02);ck('lid.set_lift');ck('sample.unload')
 def test_bad_dt(self):
  h=prepared()
  for dt in [0,-1,True,float('nan'),float('inf')]:
   with self.assertRaises(ValueError):h.step(dt)
if __name__=='__main__':unittest.main()
