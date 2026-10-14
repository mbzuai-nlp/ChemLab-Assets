import unittest,copy,pickle,sys,math
from pathlib import Path
from unittest import mock
import numpy as np
from scripts.asset_runtime import antonpaar_psa1190 as r
O=Path(__file__).resolve().parents[1]/'outputs/expansion_batch03/t4_measure/antonpaar_psa1190';sys.path.insert(0,str(O));from example_setup import build
class TestPsa1190(unittest.TestCase):
 def test29_host_registration(self):
  h=r.AntonpaarPsa1190Runtime();h.register_principal('matterix_agent');h.register_principal('matterix_evaluator','evaluator');self.assertNotIn('truth.solid_kg',h.session('matterix_agent').read(instance_id=h.instance_id)['observations'])
  with self.assertRaises(r.ActionRejected):h.register_principal('matterix_agent','evaluator')
  with self.assertRaises(r.ActionRejected):h.session('unregistered')
 def done(self,h,dt=15.1):h.invoke('acquisition.start');h.step(dt);self.assertEqual(h.phase,'succeeded',h.error);return h
 def test01_sources(self):
  c=r.LIB['coverage'];self.assertEqual(c['total_records'],232);self.assertEqual(c['full_distribution_records'],118);self.assertEqual(c['pharmaceutical_named_valid_records'],97)
  for k,v in r.LIB['substances'].items():self.assertTrue(v['source']);self.assertEqual(v['evidence'],'measured_literature')
 def test02_rayleigh(self):
  x=.001;m=1.5+0j;_,qe,qs=r.mie_scattering(x,m,np.array([.1,.2]));reference=8/3*x**4*abs((m*m-1)/(m*m+2))**2;self.assertAlmostEqual(qs/reference,1,places=5);self.assertAlmostEqual(qe/qs,1,places=9)
 def test03_mie_energy(self):
  th=np.linspace(0,np.pi,12001);I,qe,qs=r.mie_scattering(2.,1.5+0j,th);integral=2/4*np.trapz(I*np.sin(th),th);self.assertAlmostEqual(qe,qs,places=11);self.assertAlmostEqual(integral,qs,places=6);self.assertAlmostEqual(qs,1.798418163226,places=8)
 def test04_mie_absorption(self):
  _,qe,qs=r.mie_scattering(2.,1.5+.1j,np.array([.1]));self.assertGreater(qe,qs);self.assertGreater(qs,0)
 def test05_fraunhofer_kernel(self):
  K,_=r.optical_kernel(2.9,3.4,2.9,3.1,1.,'fraunhofer');j=int(np.searchsorted(r.DIAM,100e-6));d=r.DIAM[j];k=2*np.pi/830e-9;x=k*d/2;u=x*np.sin(np.deg2rad(r.THETA[0]));expected=x**4*(2*r.j1(u)/u)**2/4/k**2*6/(np.pi*d**3);self.assertAlmostEqual(K[0,j]/expected,1,places=12)
 def test06_inverse_single_bin(self):
  m=dict(r.METHOD_EXAMPLE,regularization=0.);K,_=r.optical_kernel(2.9,3.4,2.9,3.1,1.33,'mie_hybrid');j=int(np.searchsorted(r.DIAM,40e-6));p=r.invert(K[:,j],m);self.assertAlmostEqual(p['d50_m']/r.DIAM[j],1,places=6);self.assertGreater(p['volume_fraction'][j],.999)
 def test07_source_steel(self):
  h=self.done(build());self.assertLess(abs(h.psd['d50_m']/36.7e-6-1),.03);self.assertLess(h.psd['relative_fit_residual'],1e-3);self.assertEqual(h.mass_waste,0);self.assertIn('assumed',h.evidence)
 def test08_soil_full_curve(self):
  h=self.done(build('wet','soil_2014_111'));p,*_=r.template('soil_2014_111',False,True,'wet');d=r.quantiles(p);self.assertLess(abs(h.psd['d50_m']/d[1]-1),.06);self.assertLess(abs(sum(h.psd['volume_fraction'])-1),1e-9)
 def test09_refractive_input_independent(self):
  K,_=r.optical_kernel(1.543,.01,1.543,.01,1.33,'mie_hybrid');p=np.zeros(len(r.DIAM));p[np.searchsorted(r.DIAM,1.e-6)]=1;m=dict(r.METHOD_EXAMPLE,n635=1.543,k635=.01,n830=1.543,k830=.01,regularization=0.);good=r.invert(K@p,m);bad=r.invert(K@p,dict(m,n635=1.8,n830=1.8));self.assertLess(abs(good['d50_m']/r.DIAM[np.argmax(p)]-1),.01);self.assertGreater(np.linalg.norm(np.array(good['volume_fraction'])-bad['volume_fraction']),.1)
 def test10_calibration(self):
  h=build();h.invoke('calibration.size',reference_m=50e-6,observed_m=40e-6);self.done(h);self.assertLess(abs(h.psd['d50_m']/36.7e-6-1.25),.04)
 def test11_wet_mass_and_drain_heat(self):
  h=self.done(build());h.invoke('liquid.drain');h.step(23.);self.assertEqual(h.phase,'succeeded');l=h._ledger();self.assertAlmostEqual(l['solid_waste_kg'],.008,places=11);self.assertAlmostEqual(l['liquid_present_kg'],0);self.assertLess(abs(l['heat_error_j']),1e-6);self.assertLess(abs(l['solid_error_kg']),1e-12)
 def test12_dry_finite_recover(self):
  h=self.done(build('dry'));self.assertAlmostEqual(h.mass_waste,.006,places=10);self.assertLess(h.air,2.);h.invoke('power.set',enabled=False);h.invoke('dry_hood.set',lift_m=.2);h.invoke('sample.recover');self.assertAlmostEqual(h.mass_loaded,h.mass_waste+h.mass_returned,places=10);self.assertLess(abs(h._ledger()['air_error_mol']),1e-11)
 def test13_cancel_partial(self):
  h=build('dry');h.invoke('acquisition.start');h.step(3.);m=h.mass_waste;gas=h.air_used;h.invoke('acquisition.stop');h.step(2.);self.assertEqual(h.mass_waste,m);self.assertEqual(h.air_used,gas);self.assertGreater(m,0)
 def test14_warmup(self):
  h=r.AntonpaarPsa1190Runtime();h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('power.set',enabled=True);h.step(59.9)
  with self.assertRaisesRegex(r.ActionRejected,'NOT_READY'):h.invoke('background.start')
  h.step(.1);self.assertEqual(h.warm,60.)
 def test15_background_clock(self):
  h=build();h.invoke('liquid.drain');h.step(23);h.invoke('tank_lid.set',slide_m=.17);h.invoke('liquid.fill',volume_m3=.00045);h.invoke('tank_lid.set',slide_m=0.);h.invoke('background.start');h.step(59.9);self.assertFalse(h.bg_ready);h.step(.1);self.assertTrue(h.bg_ready)
 def test16_interlocks(self):
  h=build();h.invoke('tank_lid.set',slide_m=.17)
  with self.assertRaisesRegex(r.ActionRejected,'CLOSURE'):h.invoke('acquisition.start')
  with self.assertRaisesRegex(r.ActionRejected,'POWER_INTERLOCK'):h.invoke('cover.set',angle_rad=1.2)
  with self.assertRaisesRegex(r.ActionRejected,'BLANK_NOT_EMPTY'):h.invoke('tank_lid.set',slide_m=0.);h.invoke('background.start')
 def test17_strict_atomic(self):
  h=build()
  for cap,args in [('power.set',{'enabled':1}),('method.configure',dict(r.METHOD_EXAMPLE,acquisition_s=60)),('air.add',{'amount_mol':float('nan')}),('sample.load',dict(r.SAMPLE_EXAMPLE))]:
   before=pickle.dumps(h.__dict__)
   with self.assertRaises(r.ActionRejected):h.invoke(cap,**args)
   self.assertEqual(before,pickle.dumps(h.__dict__))
 def test18_truth_and_instances(self):
  h=build();g=build('dry');a=h.session('agent');self.assertNotIn('truth.solid_kg',a.read(instance_id=h.instance_id)['observations']);self.assertNotIn('plant.configure',a.describe(instance_id=h.instance_id)['capabilities'])
  with self.assertRaises(r.ActionRejected):a.read(instance_id=h.instance_id,state_ids=['truth.solid_kg'])
  self.done(h);self.assertEqual(g.phase,'succeeded');self.assertIsNone(g.psd)
 def test19_pure_reads_dedup(self):
  h=build();a=h.session();before=pickle.dumps(h.__dict__)
  for _ in range(3):h.read();a.describe(instance_id=h.instance_id);a.read(instance_id=h.instance_id)
  self.assertEqual(before,pickle.dumps(h.__dict__));args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='same');x=a.invoke(**args);y=a.invoke(**args);self.assertEqual(x,y);self.assertEqual(h._counter,2);z=a.invoke(**dict(args,capability_id='power.set',arguments={'enabled':False}));self.assertEqual(z['error']['code'],'REQUEST_CONFLICT')
 def test20_async_record_ownership(self):
  h=build();a=h.session();e=h.session('evaluator');q=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=q['operation_id']
  with self.assertRaises(r.ActionRejected):e.poll(op)
  h.step(15.1);before=a.poll(op);h.invoke('liquid.drain');h.step(23.);self.assertEqual(before,a.poll(op))
 def test21_gas_exhaustion(self):
  h=build('dry');h.air=.001;h.air_loaded=.001;h.invoke('acquisition.start');h.step(2.);self.assertEqual(h.error,'GAS_EXHAUSTED');self.assertGreaterEqual(h.air,0);self.assertLess(abs(h._ledger()['air_error_mol']),1e-12)
 def test22_obscuration(self):
  h=build();h.wet*=10;h.mass_loaded*=10;h.invoke('acquisition.start');h.step(15.);self.assertEqual(h.error,'OBSCURATION_RANGE');self.assertIsNone(h.psd)
 def test23_heat_and_cooling(self):
  h=self.done(build());hot=h.temperature;self.assertGreater(hot,293.15);h.invoke('power.set',enabled=False);self.assertEqual(hot,h.temperature);h.step(100.);self.assertLess(h.temperature,hot);self.assertLess(abs(h._ledger()['heat_error_j']),1e-6)
 def test24_settling_and_deaggregation(self):
  h=build();h.plant['settling_factor']=1.;h.agg=.5;total=h.read()['truth.solid_kg'];h.invoke('acquisition.start');h.step(5.);self.assertGreater(h.settled.sum(),0);self.assertLess(h.agg,.5);self.assertAlmostEqual(h.read()['truth.solid_kg'],total,places=12)
 def test25_clock_partition(self):
  a=build();b=copy.deepcopy(a);a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(2.3)
  for _ in range(230):b.step(.01)
  self.assertEqual(a.read(),b.read());np.testing.assert_allclose(a.wet,b.wet)
 def test26_template_rejection(self):
  for sid,cross,trunc in [('powder_050',True,True),('powder_057',True,True),('powder_001',False,True)]:
   with self.assertRaises(r.ActionRejected):r.template(sid,cross,trunc,'wet')
  v,_,e,_=r.template('powder_001',True,True,'wet');self.assertEqual(len(v),110);self.assertIn('assumed',e)
 def test27_timeout_and_unsupported(self):
  h=build();h.invoke('method.configure',**dict(h.method,timeout_s=1.));h.invoke('acquisition.start');h.step(2.);self.assertEqual(h.error,'TIMEOUT')
  for k in r.UNSUPPORTED:
   with self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED'):h.invoke(k)
 def test28_no_implicit_temperature_sensor(self):
  h=build();a=h.session();obs=a.read(instance_id=h.instance_id)['observations'];self.assertNotIn('truth.temperature_k',obs);self.assertFalse(any('temperature' in k for k in obs))
 def test30_result_query_cannot_take_operation_ownership(self):
  h=build();h.register_principal('observer');owner=h.session();observer=h.session('observer');op=owner.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='owned')['operation_id'];h.step(.1);before=h._owners.copy()
  queried=observer.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='query');self.assertEqual(queried['status'],'succeeded');self.assertIsNone(queried['operation_id']);self.assertEqual(queried['result']['operation_id'],op);self.assertEqual(h._owners,before)
  with self.assertRaisesRegex(r.ActionRejected,'ACCESS_DENIED'):observer.cancel(op,'unauthorized')
  self.assertEqual(owner.cancel(op,'owner_cancel')['status'],'succeeded');self.assertEqual(owner.poll(op)['status'],'cancelled')
 def test31_result_source_belongs_to_acquisition(self):
  h=self.done(build('dry'));original=h.invoke('acquisition.result');h.invoke('method.configure',**dict(h.method,regularization=.0002));self.assertEqual(original,h.invoke('acquisition.result'));h.invoke('power.set',enabled=False);h.invoke('dry_hood.set',lift_m=.2);h.invoke('sample.recover')
  h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,substance_id='powder_001',mass_kg=.03,true_density_kg_m3=1000.,accept_cross_method=True,accept_range_truncation=True));self.assertNotEqual(original['template_source'],h.source);self.assertEqual(original,h.invoke('acquisition.result'))
  h.invoke('dry_hood.set',lift_m=0.);h.invoke('power.set',enabled=True);h.step(60.);h.invoke('acquisition.start');self.assertEqual(h.invoke('acquisition.result')['template_source'],h.source)
 def test32_metadata_covers_actual_state_changes(self):
  metadata=r.export_antonpaar_psa1190_contract_metadata();original=r.AntonpaarPsa1190Runtime.invoke;observed=set()
  def audited_invoke(h,cap,**arguments):
   before=h.read();result=original(h,cap,**arguments);after=h.read();changed={state for state in before if before[state]!=after[state]};declared={effect['state_id'] for effect in metadata['capabilities'][cap]['state_effects'] if effect['timing']=='immediate'}
   self.assertFalse(changed-declared,(cap,changed-declared));observed.add(cap);return result
  with mock.patch.object(r.AntonpaarPsa1190Runtime,'invoke',audited_invoke):
   h=build();h.invoke('acquisition.start');h.invoke('sim.step',dt_s=15.1);h.invoke('calibration.size',reference_m=50e-6,observed_m=40e-6);h.invoke('liquid.drain');h.invoke('sim.step',dt_s=23.);h.invoke('tank_lid.set',slide_m=.17);h.invoke('liquid.fill',volume_m3=.00045);h.invoke('tank_lid.set',slide_m=0.);h.invoke('method.configure',**dict(h.method,medium_n=1.34));h.invoke('background.start');h.invoke('sim.step',dt_s=60.)
   d=build('dry');d.invoke('acquisition.start');d.invoke('sim.step',dt_s=2.);d.invoke('acquisition.stop');d.invoke('power.set',enabled=False);d.invoke('dry_hood.set',lift_m=.2);d.invoke('sample.recover');d.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,mass_kg=.03,agglomerated_fraction=.5))
   q=r.AntonpaarPsa1190Runtime();q.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,ambient_k=298.15));d.invoke('dry_hood.set',lift_m=0.);d.invoke('power.set',enabled=True);d.invoke('sim.step',dt_s=60.);d.invoke('acquisition.start');d.invoke('power.set',enabled=False)
  self.assertTrue(set(r.IMMEDIATE_EFFECTS)<=observed)
 def test33_recovery_mass_requires_evaluator(self):
  h=build('dry');h.invoke('power.set',enabled=False);h.invoke('dry_hood.set',lift_m=.2);agent=h.session();evaluator=h.session('evaluator');self.assertNotIn('sample.recover',agent.describe(instance_id=h.instance_id)['capabilities']);before=pickle.dumps(h.__dict__)
  denied=agent.invoke(instance_id=h.instance_id,capability_id='sample.recover',arguments={},request_id='deny_truth');self.assertEqual(denied['error']['code'],'ACCESS_DENIED');self.assertEqual(before,pickle.dumps(h.__dict__));recovered=evaluator.invoke(instance_id=h.instance_id,capability_id='sample.recover',arguments={},request_id='recover');self.assertEqual(recovered['status'],'succeeded');self.assertAlmostEqual(recovered['result']['returned_mass_kg'],.03);self.assertLess(abs(h._ledger()['solid_error_kg']),1e-12)
if __name__=='__main__':unittest.main()
