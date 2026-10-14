import copy,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.specac_atlasmanual import *
def configured(fast=False,**sample):
 r=SpecacAtlasmanualRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);m=copy.deepcopy(METHOD_EXAMPLE)
 if fast:m['hold_s']=.5
 r.invoke('method.configure',**m);r.invoke('die.configure',**DIE_EXAMPLE);r.invoke('sample.configure',**dict(SAMPLE_EXAMPLE,**sample));r.invoke('screw.set_position',position_m=.152-r.die['fixed_stack_height_m']-r.initial_height-.001);r.invoke('guard.set_raised',side='front',raised=False);r.invoke('valve.set_open',open=False);r.invoke('relief.set_limit',load_N=m['target_load_N']);r.step(10);return r
def run_manual(r):
 r.invoke('acquisition.start')
 for _ in range(600):
  if r.phase!='running':break
  if not r.pump_active and r.force<r.method['target_load_N']*.995:r.invoke('pump.stroke')
  r.step(.2)
 return r
def balances(r):
 p=r._product();return [p['initial_sample_kg']-p['remaining_sample_kg']-p['collected_sample_kg'],p['initial_binder_kg']-p['remaining_binder_kg']-p['collected_binder_kg'],OIL-p['oil_reservoir_reference_m3']-p['oil_transferred_reference_m3'],p['oil_transferred_reference_m3']-p['ram_displaced_m3']-p['oil_compression_reference_m3'],r.oil-r.pumped+r.returned]
class AtlasTests(unittest.TestCase):
 def test_rated_metric_force(self):self.assertEqual(MAX_LOAD,147099.75)
 def test_no_electric_or_temperature_api(self):self.assertFalse(any('power' in k or 'temperature' in k for k in CAPS))
 def test_default_relief(self):self.assertEqual(SpecacAtlasmanualRuntime().relief_limit,10*TF)
 def test_start_needs_setup(self):
  with self.assertRaises(ActionRejected):SpecacAtlasmanualRuntime().invoke('acquisition.start')
 def test_13mm_die_limit(self):
  r=SpecacAtlasmanualRuntime()
  with self.assertRaises(ActionRejected):r.invoke('die.configure',**dict(DIE_EXAMPLE,maximum_load_N=11*TF))
 def test_target_never_auto_pumps(self):
  r=configured();r.invoke('acquisition.start');r.step(30);self.assertEqual(r.force,0);self.assertEqual(r.pumped,0);self.assertEqual(r.phase,'running')
 def test_stroke_queued_no_time(self):
  r=configured();t=r.time_s;r.invoke('pump.stroke');self.assertEqual(r.time_s,t);self.assertEqual(r.oil,0)
 def test_handstroke_finite_volume(self):
  r=configured();r.invoke('pump.stroke');r.step(.2);self.assertAlmostEqual(r.pumped,.5e-6,14);self.assertGreater(r.pump_angle,0);r.step(.6);self.assertAlmostEqual(r.pumped,1e-6,14);self.assertFalse(r.pump_active)
 def test_duplicate_stroke_denied(self):
  r=configured();r.invoke('pump.stroke')
  with self.assertRaises(ActionRejected):r.invoke('pump.stroke')
 def test_screw_target_not_teleport(self):
  r=SpecacAtlasmanualRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('screw.set_position',position_m=.05);self.assertEqual(r.screw,0);r.step(1);self.assertAlmostEqual(r.screw,.01)
 def test_guard_motion(self):
  r=SpecacAtlasmanualRuntime();r.invoke('plant.configure',**PLANT_EXAMPLE);r.invoke('guard.set_raised',side='front',raised=False);self.assertEqual(r.guard_lifts['front'],.18);r.step(.1);self.assertAlmostEqual(r.guard_lifts['front'],.15)
 def test_guard_prevents_manual_pump(self):
  r=configured();r.invoke('guard.set_raised',side='front',raised=True)
  with self.assertRaises(ActionRejected):r.invoke('pump.stroke')
 def test_gauge_reads_force_not_pressure(self):
  r=run_manual(configured(True));self.assertGreater(r.force,60000);self.assertLess(r.indicated,MAX_LOAD);self.assertGreater(r.read('evaluator')['truth.hydraulic_pressure_pa'],1e6)
 def test_real_manual_hold(self):
  r=run_manual(configured());self.assertEqual(r.phase,'succeeded');self.assertAlmostEqual(r.hold_s,12,10);self.assertGreater(r.force,60000)
 def test_finite_mass_oil(self):
  r=run_manual(configured(True,binder_kg=.00005));self.assertTrue(all(abs(x)<1e-12 for x in balances(r)))
 def test_compaction_and_rebound(self):
  r=run_manual(configured(True));p=r._product();self.assertGreater(r.phi,.35);self.assertLess(p['thickness_m'],p['unloaded_thickness_m']);self.assertLess(p['unloaded_thickness_m'],r.initial_height)
 def test_cancel_keeps_load_and_compaction(self):
  r=configured();r.invoke('acquisition.start')
  for _ in range(8):r.invoke('pump.stroke');r.step(.8)
  f,phi=r.force,r.phi;r.invoke('acquisition.stop');self.assertGreater(f,0);self.assertEqual(r.force,f);self.assertEqual(r.phi,phi);self.assertEqual(r.phase,'cancelled')
 def test_release_not_instant(self):
  r=run_manual(configured(True));f=r.force;r.invoke('valve.set_open',open=True);self.assertEqual(r.force,f);r.step(12);self.assertLess(r.force,1e-8);self.assertLess(r.ram,1e-6)
 def test_loaded_guard_denied(self):
  r=run_manual(configured(True))
  with self.assertRaises(ActionRejected):r.invoke('guard.set_raised',side='front',raised=True)
 def test_collect_requires_release(self):
  r=run_manual(configured(True))
  with self.assertRaises(ActionRejected):r.invoke('product.collect',fraction=1.,recipient_id='x',accept_external_manual_ejection=True)
 def test_collect_finite(self):
  r=run_manual(configured(True));r.invoke('valve.set_open',open=True);r.step(12);r.invoke('guard.set_raised',side='front',raised=True);r.step(1)
  for _ in range(3):r.invoke('product.collect',fraction=.5,recipient_id='x',accept_external_manual_ejection=True)
  self.assertAlmostEqual(r.collected_s,.0002*.875);self.assertTrue(all(abs(x)<1e-12 for x in balances(r)))
 def test_nominal_reference_not_measured(self):
  r=SpecacAtlasmanualRuntime();a=next(x for x in r.templates.values() if x['evidence']=='assumption');self.assertEqual(r.invoke('reference.lookup',substance_id=a['substance_id'])['evidence'],'assumption')
 def test_fiftyfive_traceable_references(self):
  r=SpecacAtlasmanualRuntime();self.assertEqual(len(r.templates),55);self.assertTrue(all(x['source'] for x in r.templates.values()))
 def test_kbr_reference(self):self.assertEqual(SpecacAtlasmanualRuntime().invoke('reference.lookup',substance_id='specac_kbr')['density_kg_m3'],2750)
 def test_truth_filtered(self):self.assertFalse(any(k.startswith('truth.') for k in configured().read()))
 def test_queries_pure(self):
  r=configured();b=r.debug_snapshot();r.invoke('press.measure');r.invoke('product.read');self.assertEqual(b,r.debug_snapshot())
 def test_bad_step(self):
  for x in [True,-1,float('nan'),float('inf'),7201]:
   with self.assertRaises(ActionRejected):configured().step(x)
 def test_no_hidden_step(self):
  r=configured();b=r.debug_snapshot();r.step(0);self.assertEqual(b,r.debug_snapshot())
 def test_instances_isolated(self):
  a=configured();b=configured();a.invoke('pump.stroke');a.step(.8);self.assertEqual(b.oil,0)
 def test_schema_rejects_extra(self):
  with self.assertRaises(ActionRejected):configured().invoke('pump.stroke',unexpected=True)
if __name__=='__main__':unittest.main()
