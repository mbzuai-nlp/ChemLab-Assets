from pathlib import Path
import sys,unittest,copy,importlib.util,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.ch_chi440c import *
sp=importlib.util.spec_from_file_location('asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(sp);sp.loader.exec_module(tool)
OBSERVED=set()
def call(r,k,**a):
 v=r.invoke(k,**a);err=tool.validate_value(v,CAPS[k][1]);assert not err,err;OBSERVED.add(k);return v
def prepared(crystal=None,method=None,sample=None):
 r=ChChi440cRuntime();call(r,'crystal.configure',**(crystal or copy.deepcopy(CRYSTAL_EXAMPLE)));call(r,'lid.set',lift_m=.05);call(r,'sample.load',**(sample or copy.deepcopy(SAMPLE_EXAMPLE)));call(r,'lid.set',lift_m=0.);call(r,'method.configure',**(method or copy.deepcopy(METHOD_EXAMPLE)));call(r,'power.set',enabled=True);return r
def qcm_method(t=3.):return dict(mode='qcm_only',steps=[dict(duration_s=t,potential_v=0.,current_a=0.)],sample_interval_s=.1,reference_electrode='not driven')
class EQCM(unittest.TestCase):
 def test_metadata_and_real_templates(self):
  r=prepared();m=r.describe()
  for c in m['capabilities'].values():self.assertEqual(tool.validate_value(c['example_arguments'],c['input_schema']),[])
  for k,v in r.read('evaluator').items():self.assertEqual(tool.validate_value(v,m['state_slots'][k]['value_schema']),[])
  self.assertEqual(len(r.templates),96)
  for sid in r.templates:self.assertGreater(call(r,'template.read',substance_id=sid)['molar_mass_kg_mol'],0.)
 def test_faraday_sauerbrey_independent(self):
  r=prepared();call(r,'acquisition.start');r.step(3.);self.assertEqual(r.phase,'succeeded');self.assertEqual(len(r.points),30);mass=1e-5*.063546/(2*F);self.assertAlmostEqual(r.film*r.mw,mass,delta=1e-25);self.assertLess(abs(r._ledger()['mass_residual_kg']),1e-21);self.assertLess(abs(r._ledger()['charge_residual_c']),1e-18);self.assertAlmostEqual(r.inferred(),mass,delta=.025/r.sensitivity());self.assertGreater(r.points[19]['inferred_delta_mass_kg'],r.points[-1]['inferred_delta_mass_kg'])
 def test_fixed_sampling_partition(self):
  a=prepared();b=prepared()
  for r in [a,b]:call(r,'acquisition.start')
  a.step(3.)
  for _ in range(1000):b.step(.003)
  self.assertEqual(a.points,b.points);self.assertEqual(a._ledger(),b._ledger())
 def test_voltage_mode_bv_current_and_limits(self):
  m=dict(METHOD_EXAMPLE,mode='potential',steps=[dict(duration_s=1.,potential_v=-.05,current_a=0.)]);r=prepared(method=m);call(r,'acquisition.start');r.step(1.);self.assertEqual(r.phase,'succeeded');self.assertGreater(r.current,0.);self.assertLess(abs(r.potential+.05),1e-12)
  r=prepared(method=dict(m,steps=[dict(duration_s=1.,potential_v=-10.,current_a=0.)]),sample=dict(SAMPLE_EXAMPLE,series_resistance_ohm=0.));call(r,'acquisition.start');r.step(1.);self.assertEqual(r.failure,'CURRENT_LIMIT');self.assertEqual(r.charge,0.)
 def test_galvanostatic_potential_limit(self):
  r=prepared(sample=dict(SAMPLE_EXAMPLE,series_resistance_ohm=1e9));call(r,'acquisition.start');r.step(1.);self.assertEqual(r.failure,'POTENTIAL_LIMIT');self.assertEqual(r.film,0.)
 def test_atomic_limits_sampling_and_types(self):
  r=prepared()
  for k,a in [('method.configure',dict(METHOD_EXAMPLE,sample_interval_s=.000999)),('method.configure',dict(METHOD_EXAMPLE,steps=[dict(duration_s=1.,potential_v=0.,current_a=.25001)])),('method.configure',dict(METHOD_EXAMPLE,mode='potential',steps=[dict(duration_s=1.,potential_v=10.01,current_a=0.)])),('method.configure',dict(METHOD_EXAMPLE,sample_interval_s=.13)),('power.set',{'enabled':1}),('lid.set',{'lift_m':float('nan')})]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(ActionRejected):r.invoke(k,**a)
   self.assertEqual(r.__dict__,before)
  for dt in [True,float('nan'),-1.]:
   with self.assertRaises(ActionRejected):r.step(dt)
 def test_maximum_1khz_boundary(self):
  r=prepared(method=dict(METHOD_EXAMPLE,steps=[dict(duration_s=.010,potential_v=0.,current_a=1e-5)],sample_interval_s=.001));call(r,'acquisition.start');r.step(.01);self.assertEqual(len(r.points),10);self.assertEqual(r.phase,'succeeded')
 def test_finite_exhaustion_preserves_transfers(self):
  s=dict(SAMPLE_EXAMPLE,volume_m3=1e-9,concentration_mol_m3=1e-5);r=prepared(sample=s);call(r,'acquisition.start');r.step(3.);self.assertEqual(r.phase,'failed');self.assertEqual(r.failure,'FINITE_MATERIAL_EXHAUSTED');self.assertEqual(r.solution,0.);self.assertGreater(r.film,0);self.assertLess(abs(r._ledger()['mass_residual_kg']),1e-25);self.assertAlmostEqual(r.charge,2*F*1e-14,delta=1e-22)
 def test_negative_current_without_film(self):
  r=prepared(method=dict(METHOD_EXAMPLE,steps=[dict(duration_s=1.,potential_v=0.,current_a=-1e-5)]));call(r,'acquisition.start');r.step(1.);self.assertEqual(r.failure,'FINITE_MATERIAL_EXHAUSTED');self.assertEqual(r.charge,0.);self.assertEqual(r.film,0.)
 def test_cancellation_unload_clean_ledger(self):
  r=prepared();call(r,'acquisition.start');r.step(1.);call(r,'acquisition.stop');l=r._ledger();r.step(10.);self.assertEqual(l,r._ledger())
  with self.assertRaisesRegex(ActionRejected,'LID_OPEN_REQUIRED'):r.invoke('sample.unload')
  call(r,'lid.set',lift_m=.05);call(r,'sample.unload');self.assertGreater(r.film,0.);call(r,'crystal.clean');self.assertEqual(r.film,0.);self.assertGreater(r.waste_mass,0.);self.assertLess(abs(r._ledger()['mass_residual_kg']),1e-21);call(r,'sample.load',**ADSORPTION_EXAMPLE);self.assertLess(abs(r._ledger()['mass_residual_kg']),1e-21)
 def test_baseline_does_not_clean_or_read_truth(self):
  r=prepared();r.step(.1);call(r,'baseline.zero');call(r,'acquisition.start');r.step(3.);mass=r.film*r.mw;frequency=r.frequency;call(r,'baseline.zero');self.assertEqual(r.inferred(),0.);self.assertEqual(r.film*r.mw,mass);r.film*=2.;self.assertEqual(r.frequency,frequency);self.assertEqual(r.inferred(),0.)
 def test_adsorption_finite_analytic(self):
  r=prepared(method=qcm_method(10.),sample=ADSORPTION_EXAMPLE);call(r,'acquisition.start');r.step(10.);self.assertGreater(r.film,0.);self.assertLess(r.film,1e-9);self.assertEqual(r.charge,0.);self.assertLess(abs(r._ledger()['mass_residual_kg']),1e-21);self.assertLess(r.points[-1]['frequency_hz'],r.points[0]['frequency_hz'])
  x=0.;dt=1e-4;n=ADSORPTION_EXAMPLE['volume_m3']*ADSORPTION_EXAMPLE['concentration_mol_m3'];s=ADSORPTION_EXAMPLE
  for _ in range(100000):x+=dt*(s['adsorption_on_m3_mol_s']*(n-x)*(s['site_capacity_mol']-x)/s['volume_m3']-s['desorption_off_s']*x)
  self.assertAlmostEqual(r.film,x,delta=1e-14)
 def test_adsorption_repeated_root_and_desorption(self):
  self.assertAlmostEqual(adsorb_exact(0.,1.,1.,1.,0.,1.,1.),.5);self.assertAlmostEqual(adsorb_exact(.3,1.,1.,0.,2.,1.,.5),.3/math.e)
 def test_thinfilm_domain_failure_keeps_mass(self):
  r=prepared(crystal=dict(CRYSTAL_EXAMPLE,max_relative_shift=1e-6),method=dict(METHOD_EXAMPLE,steps=[dict(duration_s=1.,potential_v=0.,current_a=.001)]));call(r,'acquisition.start');r.step(1.);self.assertEqual(r.failure,'THINFILM_DOMAIN');self.assertIsNone(r.frequency);self.assertGreater(r.film,0.);self.assertIsNone(r.inferred())
 def test_session_truth_dedup_owner_and_stale(self):
  r=prepared();r.register_principal('a');r.register_principal('b');a=r.session('a');b=r.session('b');i=r.instance_id;self.assertNotIn('ledger.read',a.describe(instance_id=i)['capabilities']);self.assertNotIn('sample.identity',a.read(instance_id=i)['observations']);self.assertNotIn('film.true_mass_kg',a.read(instance_id=i)['observations']);v=a.invoke(instance_id=i,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(v,a.invoke(instance_id=i,capability_id='acquisition.start',arguments={},request_id='one'))
  with self.assertRaises(ActionRejected):b.poll(v['operation_id'])
  r.step(1.);a.cancel(v['operation_id'],'stop');obs=a.read(instance_id=i,state_ids=['frequency.hz'])['observations']['frequency.hz'];self.assertEqual(obs['quality'],'stale');r.step(10);self.assertEqual(obs,a.read(instance_id=i,state_ids=['frequency.hz'])['observations']['frequency.hz'])
 def test_instances_busy_pure_and_unsupported(self):
  r=prepared();other=prepared();before=copy.deepcopy(other.__dict__);call(r,'acquisition.start')
  with self.assertRaisesRegex(ActionRejected,'BUSY'):r.invoke('lid.set',lift_m=.05)
  r.step(.5);self.assertEqual(other.__dict__,before);saved=copy.deepcopy(r.__dict__);r.describe();r.read();call(r,'process.measure');call(r,'ledger.read');self.assertEqual(r.__dict__,saved)
  for k in UNSUPPORTED:
   with self.assertRaisesRegex(ActionRejected,'UNSUPPORTED'):r.invoke(k)
if __name__=='__main__':unittest.main()
