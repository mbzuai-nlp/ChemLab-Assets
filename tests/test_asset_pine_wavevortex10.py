import sys,json,math,copy,unittest,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import pine_wavevortex10 as rt
spec=importlib.util.spec_from_file_location('assettool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
OBSERVED=set()
def call(h,k,**a):
 r=h.invoke(k,**a);OBSERVED.add(k);errors=tool.validate_value(r,rt.CAPS[k][1]);assert not errors,errors;return r
def prepared(sample=None):
 h=rt.PineWavevortex10Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'electrochem.configure',**rt.ELECTROCHEM_EXAMPLE);call(h,'enclosure.set',lift_m=.23);call(h,'sample.load',**(sample or rt.SAMPLE_EXAMPLE));call(h,'enclosure.set',lift_m=0.);call(h,'power.set',enabled=True);call(h,'speed.set',angular_speed_rad_s=1000*rt.RPM);call(h,'motor.run',enabled=True);h.step(3);return h
class Tests(unittest.TestCase):
 def test_01_templates_and_geometry_collection(self):
  h=rt.PineWavevortex10Runtime();self.assertEqual(len(h.templates),59);self.assertAlmostEqual(rt.N_GEOM,.255511374556499,13)
  for sid in h.templates:self.assertGreater(call(h,'template.read',substance_id=sid)['diffusion_m2_s'],0)
  self.assertAlmostEqual(h.templates['o2_neutral']['diffusion_m2_s'],2.35e-9)
 def test_02_realistic_drive_and_tip_limits(self):
  h=rt.PineWavevortex10Runtime();call(h,'plant.configure',**rt.PLANT_EXAMPLE);call(h,'power.set',enabled=True);call(h,'speed.set',angular_speed_rad_s=8000*rt.RPM)
  with self.assertRaisesRegex(rt.ActionRejected,'TIP_SPEED'):h.invoke('motor.run',enabled=True)
  self.assertFalse(h.running);call(h,'speed.set',angular_speed_rad_s=3000*rt.RPM);call(h,'motor.run',enabled=True);h.step(4);self.assertAlmostEqual(h.omega/rt.RPM,3000,2)
  with self.assertRaises(rt.ActionRejected):h.invoke('speed.set',angular_speed_rad_s=50*rt.RPM)
 def test_03_window_auto_resume(self):
  h=prepared();call(h,'enclosure.set',lift_m=.23);h.step(3);self.assertTrue(h.running);self.assertLess(h.omega,.002);call(h,'enclosure.set',lift_m=0.);h.step(3);self.assertAlmostEqual(h.omega/rt.RPM,1000,1)
 def test_04_external_truth_table_and_gains(self):
  h=prepared();call(h,'motor.run',enabled=False);call(h,'speed.set',angular_speed_rad_s=0.)
  for gain in [1.,2.,4.]:
   c=dict(rt.EXTERNAL_EXAMPLE,mode='external',gain_rpm_per_mv=gain,input_v=1./gain);call(h,'control.configure',**c);call(h,'motor.run',enabled=True);h.step(3);self.assertAlmostEqual(h.omega/rt.RPM,1000,1)
   call(h,'control.configure',**dict(c,stop_v=0.));h.step(3);self.assertLess(h.omega,.002);self.assertTrue(h.running)
   call(h,'control.configure',**dict(c,stop_mode='active_low',stop_v=0.));h.step(3);self.assertAlmostEqual(h.omega/rt.RPM,1000,1)
   call(h,'control.configure',**dict(c,stop_mode='active_low',stop_v=5.));h.step(3);self.assertLess(h.omega,.002)
  with self.assertRaisesRegex(rt.ActionRejected,'UNSPECIFIED'):h.invoke('control.configure',**dict(c,stop_v=1.))
 def test_05_power_on_pause_and_coast_energy(self):
  h=prepared();e=h.energy();call(h,'power.set',enabled=False);self.assertEqual(h.energy(),e);h.step(1);self.assertGreater(h.energy(),0);self.assertLess(h.energy(),e);call(h,'power.set',enabled=True);self.assertFalse(h.running);h.step(5);self.assertLess(h.omega,.001);self.assertLess(abs(call(h,'ledger.read')['energy_residual_j']),1e-10)
 def test_06_torque_power_energy_bounded(self):
  h=rt.PineWavevortex10Runtime();call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,rotor_inertia_kg_m2=.001));call(h,'power.set',enabled=True);call(h,'speed.set',angular_speed_rad_s=3000*rt.RPM);call(h,'motor.run',enabled=True)
  for _ in range(3000):
   work=h.work;h.step(.01);self.assertLessEqual(h.work-work,.110000001);self.assertLessEqual(abs(h.target_torque),.018700001)
  self.assertLess(abs(h._ledger()['energy_residual_j']),1e-9);self.assertLess(h.angle,0)
 def test_07_head_interlock_and_unload(self):
  h=prepared()
  with self.assertRaises(rt.ActionRejected):h.invoke('head.set',lift_m=.06)
  call(h,'motor.run',enabled=False);h.step(3);call(h,'enclosure.set',lift_m=.23);call(h,'sample.unload');call(h,'head.set',lift_m=.06);self.assertEqual(h.head,.06);self.assertAlmostEqual(h._ledger()['material_residual_mol'],0,15)
 def test_08_rrde_finite_conservation_and_inference(self):
  h=prepared();call(h,'acquisition.start',duration_s=5.,sample_interval_s=.1);h.step(5);self.assertEqual(h.phase,'succeeded');r=call(h,'process.measure');self.assertEqual(len(r['points']),50);p=r['points'][-1];est=call(h,'analysis.orr',disk_current_a=p['disk_current_a'],ring_current_a=p['ring_current_a'],collection_fraction=rt.N_GEOM);self.assertAlmostEqual(est['peroxide_fraction'],.35,3);self.assertAlmostEqual(est['electron_number'],3.3,3);l=call(h,'ledger.read');self.assertLess(abs(l['material_residual_mol']),1e-16);self.assertLess(abs(l['charge_residual_c']),1e-12);self.assertGreater(l['disk_electron_mol'],0)
 def test_09_empirical_collection_from_observations(self):
  s=dict(rt.SAMPLE_EXAMPLE,reaction='generator_collector',disk_electrons=1.,ring_electrons=1.,intermediate_fraction=1.);h=prepared(s);call(h,'acquisition.start',duration_s=5.,sample_interval_s=.1);h.step(5);p=h.points[-1];n=call(h,'analysis.collection',disk_current_a=p['disk_current_a'],ring_current_a=p['ring_current_a'],disk_electrons=1.,ring_electrons=1.)['collection_fraction'];self.assertAlmostEqual(n,rt.N_GEOM,3)
 def test_10_levich_speed_scaling_and_template_changes(self):
  def current(rpm,sample=None):
   h=prepared(sample);call(h,'speed.set',angular_speed_rad_s=rpm*rt.RPM);h.step(3);call(h,'acquisition.start',duration_s=1.,sample_interval_s=.1);h.step(1);return abs(h.points[-1]['disk_current_a'])
  self.assertAlmostEqual(current(2000)/current(500),2,2)
  sample=dict(rt.SAMPLE_EXAMPLE,reaction='generator_collector',disk_electrons=1.,ring_electrons=1.,intermediate_fraction=1.)
  a=current(1000,sample);b=current(1000,dict(sample,substance_id='na_cation1'));self.assertGreater(a,b)
 def test_11_partition_invariant(self):
  a=prepared();b=prepared()
  for h in [a,b]:call(h,'acquisition.start',duration_s=2.,sample_interval_s=.1)
  a.step(2.)
  for _ in range(200):b.step(.01)
  self.assertEqual(a.points,b.points);self.assertEqual(a._ledger(),b._ledger())
 def test_12_cancel_and_query_pure(self):
  h=prepared();call(h,'acquisition.start',duration_s=5.,sample_interval_s=.1);h.step(.47);ledger=h._ledger();call(h,'acquisition.stop');self.assertEqual(h._ledger(),ledger);before=copy.deepcopy(h.__dict__);call(h,'process.measure');self.assertEqual(h.__dict__,before);self.assertEqual(h.phase,'cancelled');self.assertEqual(len(h.points),4)
 def test_13_privacy_dedup_and_ownership(self):
  h=prepared();h.register_principal('a');h.register_principal('b');h.register_principal('e','evaluator');a=h.session('a');b=h.session('b');e=h.session('e');i=h.instance_id
  self.assertNotIn('sample.identity',a.read(instance_id=i)['observations']);self.assertIn('sample.identity',e.read(instance_id=i)['observations'])
  r=a.invoke(instance_id=i,capability_id='ledger.read',arguments={},request_id='x');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
  kwargs=dict(instance_id=i,capability_id='acquisition.start',arguments={'duration_s':2.,'sample_interval_s':.1},request_id='start');r=a.invoke(**kwargs);self.assertEqual(r,a.invoke(**kwargs));self.assertEqual(h.serial,1)
  with self.assertRaises(rt.ActionRejected):b.poll(r['operation_id'])
  h.step(.3);self.assertEqual(a.cancel(r['operation_id'],'cancel')['status'],'succeeded')
  d=a.describe(instance_id=i);self.assertNotIn('sample.load',d['capabilities']);self.assertNotIn('sample.identity',d['state_slots']);self.assertNotIn('private_oxygen_bath',json.dumps(a.poll(r['operation_id'])))
 def test_14_rejection_atomicity_and_nan(self):
  h=prepared()
  for k,a in [('speed.set',{'angular_speed_rad_s':float('nan')}),('head.set',{'lift_m':.06}),('control.configure',dict(rt.EXTERNAL_EXAMPLE,mode='external',input_v=1.)),('acquisition.start',{'duration_s':.11,'sample_interval_s':.1}),('analysis.orr',{'disk_current_a':-1e-5,'ring_current_a':1.,'collection_fraction':.25})]:
   before=copy.deepcopy(h.__dict__)
   with self.assertRaises(rt.ActionRejected):h.invoke(k,**a)
   self.assertEqual(h.__dict__,before)
 def test_15_instances_and_unsupported(self):
  a=prepared();b=prepared();call(a,'speed.set',angular_speed_rad_s=2000*rt.RPM);self.assertNotEqual(a.setpoint,b.setpoint)
  for k in rt.UNSUPPORTED:
   with self.assertRaisesRegex(rt.ActionRejected,'UNSUPPORTED'):a.invoke(k)
 def test_16_independent_schema_and_source_in_metadata(self):
  h=prepared();m=h.describe()
  for k,v in h.read('evaluator').items():self.assertEqual(tool.validate_value(v,m['state_slots'][k]['value_schema']),[],k)
  self.assertEqual(set(m['capabilities']),set(rt.CAPS));self.assertIn('rrde',m['mechanisms']);self.assertEqual(len(m['state_slots']),30)
 def test_17_observed_koutecky_levich(self):
  h=rt.PineWavevortex10Runtime();D=2.35e-9;nu=1e-6;c=.25;n=4;ik=.0006;w=[r*rt.RPM for r in [500,1000,2000,3000]];B=.620*n*rt.F*rt.AREA*D**(2/3)*nu**(-1/6)*c;currents=[-1/(1/ik+1/(B*math.sqrt(v))) for v in w];r=call(h,'analysis.levich',angular_speed_rad_s=w,disk_current_a=currents,electron_number=n,concentration_mol_m3=c,kinematic_viscosity_m2_s=nu);self.assertAlmostEqual(r['diffusion_m2_s']/D,1.,12);self.assertAlmostEqual(r['kinetic_current_magnitude_a'],ik,12);self.assertAlmostEqual(r['r_squared'],1,12)
  with self.assertRaises(rt.ActionRejected):h.invoke('analysis.levich',angular_speed_rad_s=[100*rt.RPM]*3,disk_current_a=[-1e-4]*3,electron_number=1.,concentration_mol_m3=1.,kinematic_viscosity_m2_s=nu)
if __name__=='__main__':unittest.main()
