import copy,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.retsch_pm100 import *
def ready(method=None,sample=None,plant=None):
 r=RetschPm100Runtime();r.invoke('plant.configure',**(plant or PLANT_EXAMPLE));r.invoke('method.configure',**(method or METHOD_EXAMPLE));r.invoke('lid.set',angle_rad=1.55);r.step(4);r.invoke('sample.configure',**(sample or example_sample()));r.invoke('mount.configure',clamped=True,balanced=True);r.invoke('lid.set',angle_rad=0.);r.step(4);r.invoke('power.set',enabled=True);return r
class Tests(unittest.TestCase):
 def rejected(self,r,cap,**args):
  before=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke(cap,**args)
  self.assertEqual(before,r.debug_snapshot())
 def test_bounds(self):
  r=ready()
  for x in [WMIN-1,WMAX+1,float('nan'),True]:self.rejected(r,'milling.set_speed',speed_rad_s=x)
 def test_no_teleport(self):
  r=ready();r.invoke('milling.set_speed',speed_rad_s=WMAX);self.assertEqual(r.w,0);r.invoke('acquisition.start');self.assertEqual(r.w,0);r.step(.1);self.assertGreater(r.w,0);self.assertLess(r.w,WMAX)
 def test_capacity(self):
  for key,value in [('sample_volume_m3',221e-6),('jar_volume_m3',.00025),('ball_count',24),('max_feed_m',.011),('initial_d50_m',.004)]:
   r=RetschPm100Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('lid.set',angle_rad=1.55);r.step(4);s=example_sample();s[key]=value;self.rejected(r,'sample.configure',**s)
 def test_clamp_balance(self):
  for field in ['clamped','balanced']:
   r=ready();setattr(r,field,False);self.rejected(r,'acquisition.start')
 def test_open_lid_interlock(self):
  r=ready();r.invoke('lid.set',angle_rad=1.55);self.rejected(r,'acquisition.start');r.step(1);self.assertAlmostEqual(r.lid,.4);r.invoke('lid.set',angle_rad=0.);self.rejected(r,'acquisition.start');r.step(1);r.invoke('acquisition.start');r.step(1);self.rejected(r,'lid.set',angle_rad=1.)
 def test_cancel_inertia(self):
  r=ready();r.invoke('acquisition.start');r.step(2);r.invoke('acquisition.stop');self.assertGreater(r.w,0);self.rejected(r,'lid.set',angle_rad=1.);before=(r.mass,r.d50);r.step(2);self.assertEqual(before,(r.mass,r.d50));self.assertEqual(r.w,0);r.invoke('lid.set',angle_rad=1.)
 def test_poweroff(self):
  r=ready();r.invoke('acquisition.start');r.step(2);r.invoke('power.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.rejected(r,'acquisition.start');r.step(2);self.assertEqual(r.w,0)
 def test_intervals_reverse(self):
  r=ready();r.invoke('acquisition.start');r.step(40);self.assertEqual(r.phase,'succeeded');self.assertAlmostEqual(r.active_s,20.);self.assertGreater(r.wall_s,26.);self.assertTrue(any(x['speed_rad_s']<0 for x in r.history));self.assertTrue(any(x['mode']=='pause' for x in r.history))
 def test_no_interval_pause(self):
  r=RetschPm100Runtime();r.invoke('plant.configure',**PLANT_EXAMPLE);self.rejected(r,'method.configure',**dict(METHOD_EXAMPLE,interval_s=0.))
 def test_done_stopped(self):
  r=ready(dict(METHOD_EXAMPLE,duration_s=.5,interval_s=0.,pause_s=0.,reverse=False));r.invoke('acquisition.start');r.step(2);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.w,0);self.assertAlmostEqual(r.active_s,.5)
 def test_planet_ratio(self):
  r=ready();r.invoke('acquisition.start');r.step(2);v=r.read();self.assertAlmostEqual(v['jar.relative_angle_rad']+v['sun.angle_rad'],-2*r.q);self.assertAlmostEqual(.0705*WMAX**2/9.80665,33.309,places=2)
 def test_energy(self):
  r=ready();r.invoke('acquisition.start');r.step(40);self.assertGreater(r.temperature,298.15);self.assertAlmostEqual(r.u,r.input_j-r.loss_j,places=8);u=r.u;r.step(100);self.assertLess(r.u,u);self.assertAlmostEqual(r.u,r.input_j-r.loss_j,places=8)
 def test_reference_known(self):
  r=ready();self.assertAlmostEqual(r._reference('al-002',298.15),24.209);self.assertGreater(len(r.templates),49);self.rejected(r,'reference.lookup',substance_id='missing',temperature_k=300.);self.rejected(r,'reference.lookup',substance_id='al-002',temperature_k=501.)
 def test_material_and_ratio(self):
  r=ready();self.assertAlmostEqual(r.mass,25*4/3*math.pi*.01**3*7800/10);m=r.mass;r.invoke('acquisition.start');r.step(40);self.assertEqual(r.mass,m);self.assertLess(r.d50,.001);self.assertGreater(r.d50,1e-6)
 def test_unknown_rate_zero(self):
  r=ready(sample=dict(example_sample(),breakage_rate_s=0.));r.invoke('acquisition.start');r.step(40);self.assertEqual(r.d50,.001)
 def test_resume_partial(self):
  r=ready();r.invoke('acquisition.start');r.step(3);r.invoke('acquisition.stop');r.step(2);d=r.d50;r.invoke('acquisition.start');self.assertEqual(r.d50,d);r.step(40);self.assertEqual(r.phase,'succeeded');self.assertAlmostEqual(r.active_s,20.)
 def test_query_pure(self):
  r=ready();before=r.debug_snapshot()
  for k in ['milling.measure','history.read','product.read']:r.invoke(k)
  r.describe();r.session().read(instance_id=r.instance_id);self.assertEqual(before,r.debug_snapshot())
 def test_permissions(self):
  r=ready();s=r.session();self.assertNotIn('truth.temperature_k',s.read(instance_id=r.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=r.instance_id)['capabilities']);self.assertEqual(s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='x')['error']['code'],'ACCESS_DENIED')
 def test_retry_conflict(self):
  r=ready();s=r.session();a=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='a');x=s.invoke(**a);b=r.debug_snapshot();self.assertEqual(s.invoke(**a),x);self.assertEqual(r.debug_snapshot(),b);a['capability_id']='acquisition.stop';self.assertEqual(s.invoke(**a)['error']['code'],'REQUEST_CONFLICT');self.assertEqual(r.debug_snapshot(),b)
 def test_dual_instances(self):
  a=ready();b=ready();a.invoke('acquisition.start');a.step(3);self.assertEqual(b.phase,'idle');self.assertEqual(b.temperature,298.15)
 def test_thermal_boundary(self):
  r=ready(dict(METHOD_EXAMPLE,speed_rad_s=WMAX,duration_s=1000,interval_s=0.,pause_s=0.,reverse=False),plant=dict(PLANT_EXAMPLE,heat_fraction=1.,loss_w_k=0.,jar_balls_heat_capacity_j_k=100.));r.invoke('acquisition.start');r.step(100);self.assertEqual(r.phase,'failed');self.assertEqual(r.fault,'THERMAL_MODEL_RANGE');self.assertAlmostEqual(r.temperature,500.,places=7);self.assertAlmostEqual(r.u,r.input_j-r.loss_j,places=7)
 def test_step_invalid(self):
  r=ready();b=r.debug_snapshot()
  for d in [-1,float('nan'),True]:
   with self.assertRaises(ActionRejected):r.step(d)
  self.assertEqual(b,r.debug_snapshot())
if __name__=='__main__':unittest.main()
