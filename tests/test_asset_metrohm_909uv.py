import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.metrohm_909uv import *
def prepared(plant=None,method=None,sample=None):
 r=Metrohm909uvRuntime();p=copy.deepcopy(PLANT_EXAMPLE);p.update(plant or {});m=copy.deepcopy(METHOD_EXAMPLE);m.update(method or {});s=example_sample();s.update(sample or {})
 for c,a in [('plant.configure',p),('method.configure',m),('sample.configure',s),('power.set',dict(enabled=True)),('shield.set_open',dict(open=False))]:r.invoke(c,**a)
 r.step(1.1);return r
class Tests(unittest.TestCase):
 def reject(self,r,c,**a):
  before=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke(c,**a)
  self.assertEqual(before,r.debug_snapshot())
 def test_setpoint_only(self):
  r=prepared();t=r.temperature;r.invoke('thermal.set_temperature',temperature_K=373.15);self.assertEqual(t,r.temperature)
 def test_shield_only_on_step(self):
  r=Metrohm909uvRuntime();r.invoke('shield.set_open',open=False);self.assertEqual(r.shield_angle,math.pi);r.step(.5);self.assertAlmostEqual(r.shield_angle,math.pi/2)
 def test_query_pure(self):
  r=prepared();before=r.debug_snapshot();r.read();r.describe();r.invoke('digestion.measure');r.session().read(instance_id=r.instance_id);self.assertEqual(before,r.debug_snapshot())
 def test_start_requires_all(self):
  self.reject(Metrohm909uvRuntime(),'acquisition.start')
 def test_volume(self):
  r=Metrohm909uvRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);s=example_sample();s['tubes'][0]['volume_m3']=12.1e-6;self.reject(r,'sample.configure',**s)
 def test_12_tubes(self):
  r=Metrohm909uvRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);s=example_sample();s['tubes'].pop();self.reject(r,'sample.configure',**s)
 def test_sensor6(self):
  r=Metrohm909uvRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);s=example_sample();s['sensor_position']=5;self.reject(r,'sample.configure',**s)
 def test_ph_guard(self):
  r=Metrohm909uvRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);s=example_sample();s['tubes'][5]['pH']=1.;self.reject(r,'sample.configure',**s)
 def test_unknown_reference(self):
  r=prepared();self.reject(r,'reference.lookup',substance_id='not_real')
 def test_method_granularity(self):
  r=prepared();self.reject(r,'method.configure',temperature_K=363.15,duration_s=61.);self.reject(r,'thermal.set_temperature',temperature_K=363.65)
 def test_timer_from_ignition(self):
  r=prepared();r.invoke('acquisition.start');r.step(1.);self.assertAlmostEqual(r.elapsed_s,1.);self.assertLess(r.temperature,353.15)
 def test_complete_then_cool(self):
  r=prepared(method=dict(duration_s=60.));r.invoke('acquisition.start');r.step(60.);self.assertEqual(r.phase,'succeeded');self.assertAlmostEqual(r.cooldown,600.);self.assertEqual(r.lamp_power,0.);self.reject(r,'acquisition.start');r.step(600.);r.invoke('acquisition.start')
 def test_cancel_residue(self):
  r=prepared();r.invoke('acquisition.start');r.step(5);p=r.parent.copy();r.invoke('acquisition.stop');r.step(3);self.assertEqual(p,r.parent);self.assertGreater(r.converted[5],0);self.assertAlmostEqual(r.cooldown,597.)
 def test_runtime_change_method(self):
  r=prepared();r.invoke('acquisition.start');r.step(10);r.invoke('method.configure',temperature_K=358.15,duration_s=60.);r.step(50);self.assertEqual(r.phase,'succeeded')
 def test_interruption_pauses_fan(self):
  r=prepared();r.invoke('acquisition.start');r.step(1);r.invoke('power.interrupt');r.step(10);self.assertAlmostEqual(r.cooldown,600);self.assertFalse(r.read()['fan.running']);r.invoke('power.set',enabled=True);r.step(10);self.assertAlmostEqual(r.cooldown,590)
 def test_host_guard(self):
  r=prepared();r.invoke('acquisition.start');self.reject(r,'shield.set_open',open=True);self.reject(r,'power.set',enabled=False)
 def test_sensor_fault(self):
  r=prepared();r.invoke('acquisition.start');r.step(1);r.invoke('sensor.set_connected',connected=False);self.assertEqual(r.phase,'failed');self.assertIsNone(r.indicated());r.step(600);self.reject(r,'fault.acknowledge');r.invoke('sensor.set_connected',connected=True);r.invoke('fault.acknowledge')
 def test_overtemp(self):
  r=prepared(plant=dict(absorbed_electrical_fraction=1.,fan_UA_W_K=1.,dry_heat_capacity_J_K=10.),method=dict(duration_s=600.));r.invoke('acquisition.start');r.step(600);self.assertEqual(r.phase,'failed');self.assertEqual(r.fault,'Max. temp. reached')
 def test_thermal_energy(self):
  r=prepared();r.invoke('acquisition.start');r.step(120);self.assertAlmostEqual(r.energy,r.storage+r.loss+r.collected_heat,places=7)
 def test_parent_conservation(self):
  r=prepared();r.invoke('acquisition.start');r.step(120)
  for i in range(12):self.assertAlmostEqual(r.initial[i],r.parent[i]+r.converted[i]+r.collected[i],places=15)
 def test_collect_finite(self):
  r=prepared();r.invoke('acquisition.start');r.step(720);r.invoke('shield.set_open',open=True);r.step(1.1);out=r.invoke('product.collect',fraction=.25,recipient_id='vials');self.assertAlmostEqual(out['transferred_volume_m3'],30e-6)
  for i in range(12):self.assertAlmostEqual(10e-6,r.volumes[i]+r.collected_volumes[i]);self.assertAlmostEqual(r.initial[i],r.parent[i]+r.converted[i]+r.collected[i])
  self.assertAlmostEqual(r.energy,r.storage+r.loss+r.collected_heat,places=7)
 def test_lamp_replace(self):
  r=prepared();r.invoke('acquisition.start');r.step(720);r.invoke('shield.set_open',open=True);r.step(1.1);self.reject(r,'lamp.replace',replacement_confirmed=True);r.invoke('power.set',enabled=False);r.invoke('lamp.replace',replacement_confirmed=True);self.assertEqual(r.lamp_usage,0.)
 def test_isolation(self):
  a=prepared();b=prepared();a.invoke('acquisition.start');a.step(10);self.assertEqual(b.elapsed_s,0.);self.assertEqual(b.parent[5],1e-7)
 def test_agent_filter(self):
  r=prepared();s=r.session();self.assertFalse(any(k.startswith('truth.') for k in s.read(instance_id=r.instance_id)['observations']));self.assertNotIn('product.read',s.describe(instance_id=r.instance_id)['capabilities']);o=s.invoke(instance_id=r.instance_id,capability_id='product.read',arguments={},request_id='x');self.assertEqual(o['error']['code'],'ACCESS_DENIED')
 def test_idempotency(self):
  r=prepared();s=r.session();kw=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');a=s.invoke(**kw);r.step(2);self.assertEqual(a,s.invoke(**kw));kw['capability_id']='acquisition.stop';self.assertEqual(s.invoke(**kw)['error']['code'],'REQUEST_CONFLICT')
 def test_poll_cancel_owner(self):
  r=prepared();r.register_principal('second','agent');s=r.session();o=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=o['operation_id'];r.step(1);self.assertEqual(s.poll(op)['status'],'running')
  with self.assertRaises(ActionRejected):r.session('second').poll(op)
  self.assertEqual(s.cancel(op,'cancel')['status'],'cancelled')
 def test_write_routes(self):
  r=prepared();s=r.session();o=s.write(instance_id=r.instance_id,state_id='thermal.target_k',value=368.15,request_id='t');self.assertEqual(o['status'],'succeeded');self.assertEqual(r.target,368.15)
 def test_invalid_dt(self):
  r=prepared()
  for dt in [-1,float('nan'),True,3601]:
   before=r.debug_snapshot()
   with self.assertRaises(ActionRejected):r.step(dt)
   self.assertEqual(before,r.debug_snapshot())
 def test_fifty_templates(self):
  r=prepared();self.assertEqual(len(r.templates),50)
 def test_zero_oh(self):
  r=prepared(plant=dict(oh_concentration_mol_m3=0));r.invoke('acquisition.start');r.step(120);self.assertEqual(r.parent[5],1e-7)
if __name__=='__main__':unittest.main()
