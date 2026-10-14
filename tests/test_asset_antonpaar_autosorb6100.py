from pathlib import Path
import sys,math,copy,json,hashlib,unittest,importlib.util
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));from asset_runtime import antonpaar_autosorb6100 as r
spec=importlib.util.spec_from_file_location('autosorb_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
OBSERVED=set()
def call(h,k,**a):
 v=h.invoke(k,**a);errors=tool.validate_value(v,r.CAPS[k][1]);assert not errors,(k,errors);OBSERVED.add(k);return v
def configured(ids=(0,),plant=None,method=None):
 h=r.AntonpaarAutosorb6100Runtime();call(h,'plant.configure',**(plant or r.PLANT_EXAMPLE));call(h,'power.set',enabled=True);call(h,'guard.set',angle_rad=1.5)
 for i in ids:call(h,'sample'+str(i)+'.set',drop_m=.18);call(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,station=i,sample_id='private'+str(i)));call(h,'sample'+str(i)+'.set',drop_m=0.);call(h,'method.configure',**dict(method or r.METHOD_EXAMPLE,station=i));call(h,'pressure.calibrate',**dict(r.PCAL_EXAMPLE,station=i));call(h,'volume.calibrate',**dict(r.VCAL_EXAMPLE,station=i))
 call(h,'guard.set',angle_rad=0.);call(h,'coolant.fill',volume_m3=.003);call(h,'dewar.set',lift_m=.29);return h
def finish(h,limit=3600):
 for _ in range(limit):
  h.step(1.)
  if h.phase!='running':break
 return call(h,'process.measure')
def balances(h):
 d=call(h,'ledger.read')
 for k in ['n2_residual_mol','sample_residual_kg','ln2_residual_kg','water_residual_kg']:assert abs(d[k])<1e-10,(k,d[k])
 for k in ['manifold_energy_residual_j','degas_energy_residual_j']:assert abs(d[k])<1e-6,(k,d[k])
 return d
def point(x,q):return dict(time_s=0.,pressure_pa=x*r.P0,relative_pressure=x,loading_mol_kg=q,p0_pa=r.P0,temperature_k=77.)
class AutosorbTests(unittest.TestCase):
 def test_01_source_integrity_and_real_count(self):
  self.assertEqual(len(r.LIB['substances']),100)
  for sid,v in r.LIB['substances'].items():
   self.assertEqual(sid,v['substance_id']);self.assertEqual(v['evidence'],'measured_literature');self.assertEqual(hashlib.sha256((ROOT/v['source']['raw_path']).read_bytes()).hexdigest(),v['source']['sha256']);self.assertIsNone(v['conditions']['branch']);x=v['runtime_equilibrium']['pressure_pa'];y=v['runtime_equilibrium']['loading_mol_kg'];self.assertTrue(all(b>a for a,b in zip(x,x[1:])));self.assertTrue(all(b>=a for a,b in zip(y,y[1:])))
 def test_02_full_pressure_inversion(self):
  h=configured();call(h,'acquisition.start',stations=[0]);out=finish(h);self.assertEqual(out['phase'],'succeeded');s=h.stations[0];self.assertEqual(len(s['points']),18);d=r.LIB['substances'][r.SID]['runtime_equilibrium']
  for p in s['points']:
   expected=r.interp(d['pressure_pa'],d['loading_mol_kg'],p['pressure_pa']);self.assertLess(abs(p['loading_mol_kg']/expected-1),.002)
  self.assertEqual(s['bet']['status'],'conditional_branch_unspecified');self.assertGreater(s['bet']['area_m2_kg'],1e6);self.assertEqual(out['stations'][0]['psd']['status'],'not_available_branch_unspecified');self.assertIsNone(out['stations'][0]['total_pore_volume_m3_kg']);balances(h)
 def test_03_three_independent_stations(self):
  h=configured((0,1,2),method=dict(r.METHOD_EXAMPLE,relative_pressures=[.002,.005,.01,.02]));call(h,'acquisition.start',stations=[0,1,2]);out=finish(h);self.assertEqual(out['phase'],'succeeded');self.assertEqual([len(s['points']) for s in h.stations],[4,4,4]);balances(h)
 def test_04_mass_readout_independent_truth(self):
  h=configured(method=dict(r.METHOD_EXAMPLE,entered_mass_kg=.0002));call(h,'acquisition.start',stations=[0]);finish(h);p=h.stations[0]['points'][5];curve=r.LIB['substances'][r.SID]['runtime_equilibrium'];q=r.interp(curve['pressure_pa'],curve['loading_mol_kg'],p['pressure_pa']);self.assertAlmostEqual(p['loading_mol_kg']/q,.5,delta=.002);self.assertEqual(h.stations[0]['sample']['true_dry_mass_kg'],.0001);balances(h)
 def test_05_free_space_two_temperature(self):
  h=configured();v=call(h,'volume.calibrate',**r.VCAL_EXAMPLE);self.assertAlmostEqual(v['warm_volume_m3'],1e-5,places=14);self.assertAlmostEqual(v['cold_volume_m3'],1e-5,places=14)
  before=copy.deepcopy(h.__dict__)
  with self.assertRaises(r.ActionRejected):h.invoke('volume.calibrate',**dict(r.VCAL_EXAMPLE,cold_dosed_mol=r.VCAL_EXAMPLE['warm_dosed_mol']))
  self.assertEqual(h.__dict__,before)
 def test_06_pressure_sensor_calibration(self):
  h=configured(plant=dict(r.PLANT_EXAMPLE,pressure_gain=[1.1]*3,pressure_offset_pa=[20.]*3));p=h.pressure(0);self.assertAlmostEqual(h.indicated(0),1.1*p+20.,delta=.003);call(h,'pressure.calibrate',station=0,reference_pa=[0.,100000.],measured_pa=[20.,110020.]);self.assertAlmostEqual(h.indicated(0),p,delta=.003)
 def test_07_cancel_preserves_resources(self):
  h=configured();call(h,'acquisition.start',stations=[0]);h.step(220.);source=h.source;call(h,'acquisition.stop');h.step(10.);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.source,source);self.assertTrue(h.stations[0]['sample']);self.assertGreater(h.pumped,0);balances(h)
 def test_08_source_depletion_timeout(self):
  h=configured(plant=dict(r.PLANT_EXAMPLE,source_volume_m3=.00001));call(h,'acquisition.start',stations=[0]);finish(h,3600);self.assertEqual(h.phase,'failed');self.assertEqual(h.failure,'POINT_TIMEOUT');self.assertGreaterEqual(h.source,0);balances(h)
 def test_09_dewar_tube_interlocks(self):
  h=configured()
  for k,a in [('guard.set',{'angle_rad':1.5}),('sample0.set',{'drop_m':.18})]:
   with self.assertRaises(r.ActionRejected):h.invoke(k,**a)
  call(h,'acquisition.start',stations=[0])
  with self.assertRaises(r.ActionRejected):h.invoke('dewar.set',lift_m=0.)
 def test_10_finite_backfill_and_unload(self):
  h=configured(method=dict(r.METHOD_EXAMPLE,relative_pressures=[.002,.005,.01,.02]));call(h,'acquisition.start',stations=[0]);finish(h);call(h,'dewar.set',lift_m=0.)
  with self.assertRaises(r.ActionRejected):h.invoke('sample0.set',drop_m=.18)
  source=h.source;call(h,'sample.backfill',station=0);h.step(5.);self.assertLess(h.source,source);self.assertAlmostEqual(h.pressure(0),101325.,delta=15.);call(h,'guard.set',angle_rad=1.5);call(h,'sample0.set',drop_m=.18);self.assertEqual(call(h,'sample.unload',station=0)['returned_mass_kg'],.0001);balances(h)
 def test_11_ln2_capacity_and_depletion(self):
  h=configured()
  with self.assertRaises(r.ActionRejected):h.invoke('coolant.fill',volume_m3=.001)
  h.ln2=1e-8;h.ln2_added=h.ln2;call(h,'acquisition.start',stations=[0]);h.step(1.);self.assertEqual(h.phase,'failed');self.assertEqual(h.ln2,0.);balances(h)
 def test_12_degas_six_samples_two_heaters(self):
  h=r.AntonpaarAutosorb6100Runtime();call(h,'plant.configure',**dict(r.PLANT_EXAMPLE,degas_heat_capacity_j_k=20.,degas_power_w=500.));call(h,'power.set',enabled=True);call(h,'coolant.fill',volume_m3=.003)
  for b in range(2):call(h,'heater'+str(b)+'.set',drop_m=.16)
  for i in range(6):call(h,'degas.load',**dict(r.DLOAD_EXAMPLE,slot=i,water_rate_at_373_s=.1))
  for b in range(2):call(h,'heater'+str(b)+'.set',drop_m=0.)
  call(h,'degas.start',**dict(r.DPROGRAM_EXAMPLE,steps=[dict(temperature_k=333.15,hold_s=220.)],ramp_k_s=5.))
  h.step(250.);self.assertEqual(h.degas_phase,'succeeded');self.assertTrue(h.read('evaluator')['degas.dry_enough']);self.assertGreater(h.trap,5e-5)
  h.step(150.)
  for b in range(2):call(h,'heater'+str(b)+'.set',drop_m=.16)
  for i in range(6):call(h,'degas.unload',slot=i)
  balances(h)
 def test_13_degas_cancel_and_hot_interlock(self):
  h=configured(plant=dict(r.PLANT_EXAMPLE,degas_heat_capacity_j_k=20.,degas_power_w=500.));call(h,'heater0.set',drop_m=.16);call(h,'degas.load',**r.DLOAD_EXAMPLE);call(h,'heater0.set',drop_m=0.);call(h,'degas.start',**r.DPROGRAM_EXAMPLE);h.step(40.)
  with self.assertRaises(r.ActionRejected):h.invoke('heater0.set',drop_m=.16)
  call(h,'degas.stop');before=h.dtemps[0];h.step(5.);self.assertLess(h.dtemps[0],before);balances(h)
 def test_14_trap_capacity_failure(self):
  h=configured(plant=dict(r.PLANT_EXAMPLE,trap_capacity_kg=1e-6));call(h,'heater0.set',drop_m=.16);call(h,'degas.load',**r.DLOAD_EXAMPLE);call(h,'heater0.set',drop_m=0.);call(h,'degas.start',**r.DPROGRAM_EXAMPLE);h.step(100.);self.assertEqual(h.degas_phase,'failed');self.assertAlmostEqual(h.trap,1e-6);balances(h)
 def test_15_bet_independent_analytic_curve(self):
  qm=4.;C=75.;pts=[point(x,qm*C*x/((1-x)*(1+(C-1)*x))) for x in [.005,.01,.02,.03,.05,.08,.10,.15,.20,.25,.3]];b=r.bet_fit(pts,branch_confirmed=True);self.assertEqual(b['status'],'valid_estimate');self.assertAlmostEqual(b['monolayer_mol_kg'],qm,places=8);self.assertAlmostEqual(b['C'],C,places=8);self.assertAlmostEqual(b['area_m2_kg'],qm*r.NA*.162e-18,places=7)
  self.assertIsNone(r.bet_fit([point(.2,0.),point(.3,-1.)])['area_m2_kg'])
 def test_16_bjh_formula_and_branch_guard(self):
  h=configured();pts=[point(x,q) for x,q in [(.4,2.),(.6,4.),(.8,8.),(.95,12.)]];out=call(h,'analysis.bjh',points=pts,branch='desorption');self.assertTrue(out['diameter_m']);x1=.95;x2=.8;t1=.354e-9*(5/-math.log(x1))**(1/3);t2=.354e-9*(5/-math.log(x2))**(1/3);k1=-2*r.N2['surface_tension_n_m']*r.VLIQ/(r.R*77*math.log(x1));k2=-2*r.N2['surface_tension_n_m']*r.VLIQ/(r.R*77*math.log(x2));radius=(t1+t2+k1+k2)/2;expected=4*r.VLIQ*(radius/((k1+k2)/2+t1-t2))**2;self.assertAlmostEqual(out['pore_volume_m3_kg'][0],expected,places=12);self.assertEqual(call(h,'analysis.bjh',points=pts,branch='unspecified')['diameter_m'],[])
 def test_17_template_range_and_method_validation(self):
  h=configured();call(h,'method.configure',**dict(r.METHOD_EXAMPLE,relative_pressures=[.002,.01,.5,.996]))
  with self.assertRaises(r.ActionRejected):h.invoke('acquisition.start',stations=[0])
  for points in [[.1,.2,.15,.3],[.1]*4,[]]:
   with self.assertRaises(r.ActionRejected):h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,relative_pressures=points))
  for v in [True,float('nan'),float('inf'),-1.]:
   with self.assertRaises(r.ActionRejected):h.step(v)
 def test_18_permissions_dedup_poll_and_write(self):
  h=configured();h.register_principal('agent');h.register_principal('other');agent=h.session('agent');other=h.session('other');d=agent.describe(instance_id=h.instance_id);self.assertNotIn('plant.configure',d['capabilities']);self.assertNotIn('station0.identity',agent.read(instance_id=h.instance_id)['observations']);denied=agent.invoke(instance_id=h.instance_id,capability_id='sample.load',arguments=r.SAMPLE_EXAMPLE,request_id='deny');self.assertEqual(denied['error']['code'],'ACCESS_DENIED');args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'stations':[0]},request_id='start');a=agent.invoke(**args);self.assertEqual(a,agent.invoke(**args));op=a['operation_id'];self.assertEqual(agent.poll(op)['status'],'running')
  with self.assertRaises(r.ActionRejected):other.poll(op)
  bad=agent.invoke(**dict(args,arguments={'stations':[1]}));self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT');h.step(3.);agent.cancel(op,'cancel');self.assertEqual(agent.poll(op)['status'],'cancelled');agent.write(instance_id=h.instance_id,state_id='power.enabled',value=False,request_id='off');self.assertFalse(h.powered)
 def test_19_queries_atomicity_and_independent_instances(self):
  h=configured();before=copy.deepcopy(h.__dict__);call(h,'process.measure');call(h,'ledger.read');call(h,'template.reference',substance_id=r.SID);h.read();self.assertEqual(h.__dict__,before);other=configured();call(h,'acquisition.start',stations=[0]);h.step(1.);self.assertEqual(other.time_s,0.);self.assertEqual(other.phase,'idle')
  for k in r.UNSUPPORTED:
   with self.assertRaises(r.ActionRejected):h.invoke(k)
  with self.assertRaises(r.ActionRejected):h.invoke('sample0.set',drop_m=True)
 def test_20_fixed_clock_partition_and_slot_schemas(self):
  a=configured();b=configured();call(a,'acquisition.start',stations=[0]);call(b,'acquisition.start',stations=[0]);a.step(20.)
  for _ in range(400):b.step(.05)
  self.assertEqual(a.read('evaluator'),b.read('evaluator'))
  for k,v in a.read('evaluator').items():self.assertFalse(tool.validate_value(v,r.SLOTS[k][0]),(k,v))
  self.assertEqual(set(a.read('evaluator')),set(r.SLOTS));balances(a)
 def test_21_warmup_retained_gas_interlock(self):
  h=configured();call(h,'acquisition.start',stations=[0]);h.step(400.);call(h,'acquisition.stop')
  with self.assertRaises(r.ActionRejected):h.invoke('dewar.set',lift_m=0.)
  h.step(600.);call(h,'dewar.set',lift_m=0.);balances(h)
if __name__=='__main__':unittest.main()
