import copy,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.buchi_encapsulatorb import *
def configured(fast=True,**sample):
 r=BuchiEncapsulatorbRuntime()
 for n,e in [('plant',PLANT_EXAMPLE),('method',dict(METHOD_EXAMPLE,target_volume_m3=.5e-6 if fast else 5e-6)),('sample',dict(SAMPLE_EXAMPLE,**sample)),('bath',BATH_EXAMPLE),('air',GAS_EXAMPLE)]:r.invoke(n+'.configure',**e)
 r.invoke('power.set',enabled=True);r.invoke('vibration.toggle');r.invoke('electrostatic.toggle');r.invoke('air.set_pressure',gauge_pa=40000.);r.step(4);return r
def run_batch(r=None):
 r=r or configured();r.invoke('acquisition.start');r.invoke('valve.set_opening',fraction=1.)
 for _ in range(1000):
  r.step(.1)
  if r.phase!='running':break
 r.invoke('valve.set_opening',fraction=0.);r.step(.3);return r
def balances(r):
 p=r._product();v=[]
 for n in ['polymer','payload','water']:v.append(p['initial_'+n+'_kg']-sum(p[k+'_'+n+'_kg'] for k in ['feed','bead','bath','collected']))
 v += [p['initial_ca_mol']-p['free_ca_mol']-p['bead_ca_mol']-p['collected_ca_mol'],p['initial_gas_mol']-p['supply_gas_mol']-p['bottle_gas_mol']-p['vented_gas_mol'],(r.sample['volume_m3'] if r.sample else 0.)-r.feed_volume-r.emitted,p['thermal_residual_j']];return v
class B390Tests(unittest.TestCase):
 def test_standard_no_syringe_or_sterile(self):self.assertFalse(any('syringe' in k or 'sterile' in k for k in CAPS))
 def test_setup_required(self):
  with self.assertRaises(ActionRejected):BuchiEncapsulatorbRuntime().invoke('acquisition.start')
 def test_heater_target_only(self):
  r=configured();r.invoke('thermal.set_temperature',temperature_k=333.15);self.assertEqual(r.h_temp,298.15)
 def test_heater_actual_lag_and_energy(self):
  r=configured();r.invoke('thermal.toggle');r.step(20);self.assertGreater(r.h_temp,r.n_temp);self.assertGreater(r.n_temp,298.15);self.assertLess(r.h_temp,313.15);self.assertLess(abs(balances(r)[-1]),1e-7)
 def test_no_active_cooling(self):
  r=configured();r.invoke('thermal.set_temperature',temperature_k=283.15);r.invoke('thermal.toggle');r.step(10);self.assertEqual(r.h_temp,298.15)
 def test_heating_delay(self):
  r=BuchiEncapsulatorbRuntime();r.invoke('plant.configure',**dict(PLANT_EXAMPLE,heater_delay_s=120.));r.invoke('power.set',enabled=True);r.invoke('thermal.toggle');r.step(20);self.assertEqual(r.h_temp,298.15)
 def test_pressure_target_only(self):
  r=configured();p=r.pressure;r.invoke('air.set_pressure',gauge_pa=80000.);self.assertEqual(r.pressure,p);r.step(.1);self.assertLess(r.pressure,80000)
 def test_start_no_auto_flow(self):
  r=configured();r.invoke('acquisition.start');r.step(3);self.assertEqual(r.flow,0);self.assertEqual(r.emitted,0)
 def test_valve_target_only(self):
  r=configured();r.invoke('valve.set_opening',fraction=1.);self.assertEqual(r.valve_angle,0);self.assertEqual(r.emitted,0)
 def test_no_false_flow_sensor(self):self.assertNotIn('truth.flow_m3_s',configured().read())
 def test_weber_stable(self):
  r=run_batch();self.assertEqual(r.phase,'succeeded');self.assertGreater(len(r.packets),0);self.assertTrue(.0005<r._product()['mean_bead_diameter_m']<.0008)
 def test_particle_volume_identity(self):
  r=run_batch();self.assertTrue(all(abs(x['count']*math.pi*x['d']**3/6-x['volume'])<1e-16 for x in r.packets))
 def test_material_gas_ca_balances(self):self.assertTrue(all(abs(v)<1e-9 for v in balances(run_batch())))
 def test_higher_frequency_smaller_drops(self):
  a=run_batch();b=configured();b.invoke('vibration.set_frequency',frequency_hz=950.);b=run_batch(b);self.assertLess(b._product()['mean_bead_diameter_m'],a._product()['mean_bead_diameter_m'])
 def test_unstable_jet_not_fake_beads(self):
  r=configured();r.invoke('vibration.set_frequency',frequency_hz=40.);r.invoke('valve.set_opening',fraction=1.);r.step(1);n=len(r.packets);m=r.bath_material[0];r.step(2);self.assertEqual(len(r.packets),n);self.assertGreater(r.ratio,1.3);self.assertGreater(r.bath_material[0],m)
 def test_poweroff_does_not_close_manual_flow(self):
  r=configured();r.invoke('valve.set_opening',fraction=1.);r.step(1);r.invoke('power.set',enabled=False);v=r.emitted;r.step(1);self.assertGreater(r.emitted,v);self.assertEqual(r.frequency,0);self.assertEqual(r.voltage,0)
 def test_cancel_keeps_flow_and_material(self):
  r=configured();r.invoke('acquisition.start');r.invoke('valve.set_opening',fraction=1.);r.step(1);v=r.emitted;r.invoke('acquisition.stop');self.assertEqual(r.emitted,v);r.step(.5);self.assertGreater(r.emitted,v);self.assertEqual(r.phase,'cancelled')
 def test_success_keeps_flow(self):
  r=configured();r.invoke('acquisition.start');r.invoke('valve.set_opening',fraction=1.);r.step(7);self.assertEqual(r.phase,'succeeded');v=r.emitted;r.step(.2);self.assertGreater(r.emitted,v)
 def test_gelation_ca_limited(self):
  r=configured();r.ca_initial=r.ca_free=0.;r=run_batch(r);r.step(20);self.assertTrue(all(x['gel']==0 for x in r.packets))
 def test_cure_and_leak(self):
  r=run_batch();b=r._product();r.step(30);a=r._product();self.assertGreater(a['mature_bead_count'],b['mature_bead_count']);self.assertLess(a['bead_payload_kg'],b['bead_payload_kg']);self.assertGreater(a['bath_payload_kg'],b['bath_payload_kg'])
 def test_electrostatic_requires_ground(self):
  r=BuchiEncapsulatorbRuntime();r.invoke('bath.configure',**dict(BATH_EXAMPLE,grounded=False))
  with self.assertRaises(ActionRejected):r.invoke('electrostatic.toggle')
 def test_gap_no_live_adjustment(self):
  with self.assertRaises(ActionRejected):configured().invoke('electrode.set_gap',gap_m=.006)
 def test_electrode_target_motion(self):
  r=configured();r.invoke('electrostatic.toggle');r.invoke('electrode.set_gap',gap_m=.008);self.assertEqual(r.gap,.003);r.step(.2);self.assertAlmostEqual(r.gap,.005)
 def test_collect_requires_hv_off(self):
  r=run_batch();r.step(20)
  with self.assertRaises(ActionRejected):r.invoke('product.collect',**CAPS['product.collect'][6])
 def test_collect_finite(self):
  r=run_batch();r.step(20);r.invoke('electrostatic.toggle');r.invoke('product.collect',**CAPS['product.collect'][6]);self.assertGreater(r.collected[1],0);self.assertTrue(all(abs(v)<1e-9 for v in balances(r)))
 def test_dead_volume(self):
  r=configured(volume_m3=5e-6,payload_diffusion_m2_s=0.);r.invoke('vibration.toggle');r.invoke('valve.set_opening',fraction=1.);r.step(70);self.assertAlmostEqual(r.feed_volume,2e-6,13);self.assertTrue(all(abs(v)<1e-9 for v in balances(r)))
 def test_finite_gas(self):
  r=configured();r.gas_initial-=r.gas_supply;r.gas_supply=0.;r.invoke('air.set_pressure',gauge_pa=100000.);r.invoke('valve.set_opening',fraction=1.);r.step(2);self.assertEqual(r.gas_supply,0);self.assertTrue(all(abs(v)<1e-9 for v in balances(r)))
 def test_identity_library50(self):
  r=configured();self.assertEqual(len(r.templates),50);self.assertEqual(r.invoke('reference.lookup',substance_id='caffeine')['evidence'],'derived')
 def test_no_false_payload_transport_reference(self):self.assertTrue(all(x['diffusivity_m2_s'] is None and x['encapsulation_efficiency'] is None for x in configured().templates.values()))
 def test_pure_queries(self):
  r=configured();b=r.debug_snapshot();r.read();r.describe();r.invoke('encapsulation.measure');self.assertEqual(b,r.debug_snapshot())
 def test_rejection_atomic(self):
  r=configured();b=r.debug_snapshot()
  with self.assertRaises(ActionRejected):r.invoke('nozzle.configure',diameter_m=.002)
  self.assertEqual(b,r.debug_snapshot())
 def test_instances(self):
  a=configured();b=configured();a.invoke('valve.set_opening',fraction=1.);a.step(1);self.assertEqual(b.emitted,0)
 def test_bad_step(self):
  for x in [True,-1,float('nan'),float('inf')]:
   with self.assertRaises(ActionRejected):configured().step(x)
 def test_zero_step(self):
  r=configured();b=r.debug_snapshot();r.step(0);self.assertEqual(b,r.debug_snapshot())
if __name__=='__main__':unittest.main()
