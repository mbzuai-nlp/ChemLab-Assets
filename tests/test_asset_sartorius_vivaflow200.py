import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.sartorius_vivaflow200 import *
def configured(plant=None,method=None,cassette=None,sample=None):
 h=SartoriusVivaflow200Runtime();h.invoke('cassette.configure',**dict(CASSETTE_EXAMPLE,**(cassette or {})));h.invoke('plant.configure',**dict(EXAMPLE,**(plant or {})));h.invoke('method.configure',**dict(METHOD_EXAMPLE,**(method or {})));h.invoke('sample.configure',**dict(SAMPLE_EXAMPLE,**(sample or {})));return h
def finish(h,seconds=12000):h.invoke('acquisition.start');h.step(seconds);return h
class TestVivaflow(unittest.TestCase):
 def assertLedger(self,h):
  v=h.read('evaluator')
  for u,parts in [('protein_kg',['retentate','permeate','adsorbed']),('liquid_m3',['retentate','permeate','buffer']),('osmoles_mol',['retentate','permeate','buffer'])]:self.assertAlmostEqual(v['truth.initial_'+u],sum(v['truth.'+p+'_'+u] for p in parts),places=11)
  for i,x in enumerate(h.sample['solutes']):self.assertAlmostEqual(x['mass_kg'],h.mass[i]+h.permeate_mass[i]+h.adsorbed[i],places=12)
 def test_01_references(self):
  h=configured();self.assertEqual(len(h.templates),60);self.assertEqual(len(h.performance),16);r=h.invoke('reference.lookup',substance_id='uniprot_p02769');self.assertTrue(66<r['unmodified_chain_molar_mass_kg_mol']<67);self.assertIsNone(r['measured_sieving']);self.assertEqual(h.performance[4]['average_permeate_m3_s'],41e-6/60)
 def test_02_concentration_conservation(self):
  h=finish(configured());self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.volume,.0001);self.assertLedger(h);self.assertGreater(h.mass[0]/h.volume,9.);self.assertGreater(h.rf,0.)
 def test_03_diafiltration_closed_form(self):
  h=finish(configured(method={'mode':'diafilter','target_diavolumes':2.,'timeout_s':18000.}));self.assertEqual(h.phase,'succeeded');self.assertEqual(h.volume,.001);self.assertAlmostEqual(h.buffer_used,.002);self.assertAlmostEqual(h.osmoles,.3*math.exp(-2),places=10);self.assertLedger(h)
 def test_04_pause_preserves_partial(self):
  h=configured();h.invoke('acquisition.start');h.step(100);before=h.debug_snapshot();h.invoke('acquisition.stop');h.step(100);self.assertEqual(h.mass,before['masses']);self.assertEqual(h.adsorbed,before['adsorbed']);self.assertEqual(h.rf,before['state']['truth.fouling_resistance_ratio']);self.assertEqual(h.read()['pressure.retentate_gauge_pa'],0);h.invoke('acquisition.start');h.step(50);self.assertLess(h.volume,before['state']['volume.retentate_m3']);self.assertLedger(h)
 def test_05_pressure_semantics(self):
  h=configured();h.invoke('acquisition.start');v=h.read();self.assertEqual(v['pressure.feed_gauge_pa'],300000.);self.assertEqual(v['pressure.retentate_gauge_pa'],250000.);self.assertEqual(v['pressure.tmp_pa'],275000.);self.assertAlmostEqual(v['indicator.lift_m'],.025*250000/350000)
 def test_06_pressure_guard_atomic(self):
  h=SartoriusVivaflow200Runtime();h.invoke('plant.configure',**EXAMPLE);before=h.read('evaluator')
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,tmp_pa=390000.))
  self.assertEqual(before,h.read('evaluator'))
 def test_07_buffer_empty(self):
  h=finish(configured(plant={'buffer_volume_m3':1e-6},method={'mode':'diafilter'}),100);self.assertEqual(h.fault,'BUFFER_EMPTY');self.assertLedger(h)
 def test_08_permeate_full(self):
  h=finish(configured(plant={'permeate_capacity_m3':1e-6}),100);self.assertEqual(h.fault,'PERMEATE_VESSEL_FULL');self.assertLedger(h)
 def test_09_membrane_and_fouling(self):
  a=configured();b=configured(cassette={'material':'RC'});c=configured(plant={'fouling_coefficient_m2_kg':0.,'adsorption_rate_s_inv':0.})
  for h in [a,b,c]:h.invoke('acquisition.start');h.step(500);self.assertLedger(h)
  self.assertLess(a.volume,b.volume);self.assertLess(c.volume,a.volume)
 def test_10_osmotic_stall(self):
  h=configured(plant={'channel_resistance_pa_s_m3':1e8},method={'tmp_pa':1000.},sample={'solutes':[{'substance_id':'uniprot_p02769','mass_kg':.05}]});finish(h,100);self.assertEqual(h.fault,'OSMOTIC_OR_FLOW_STALL');self.assertLedger(h)
 def test_11_schema_and_clock(self):
  h=configured();before=h.debug_snapshot();t=h.time_s;h.read();h.describe();h.invoke('filtration.measure');self.assertEqual(t,h.time_s)
  for value in [True,-1,float('nan')]:
   with self.assertRaises(ActionRejected):h.step(value)
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,tmp_pa=float('inf')))
  self.assertEqual(before,h.debug_snapshot())
 def test_12_protocol(self):
  h=configured();h.register_principal('e','evaluator');a=h.session();e=h.session('e');self.assertNotIn('truth.initial_protein_kg',a.read(instance_id=h.instance_id)['observations']);r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(r['status'],'accepted');self.assertEqual(r,a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one'));self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='filtration.measure',arguments={},request_id='one')['error']['code'],'REQUEST_CONFLICT');self.assertEqual(a.poll(r['operation_id'])['status'],'running')
  with self.assertRaises(ActionRejected):e.poll(r['operation_id'])
  a.cancel(r['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['status'],'rejected');self.assertEqual(a.invoke(instance_id='wrong',capability_id='filtration.measure',arguments={},request_id='wrong')['status'],'rejected')
 def test_13_cassette_identity(self):
  h=SartoriusVivaflow200Runtime()
  with self.assertRaises(ActionRejected):h.invoke('cassette.configure',material='RC',mwco_kg_mol=3.,legacy_2023_configuration_confirmed=True)
  h.invoke('cassette.configure',**CASSETTE_EXAMPLE)
  with self.assertRaises(ActionRejected):h.invoke('cassette.configure',**CASSETTE_EXAMPLE)
 def test_14_instances_and_timeout(self):
  a=configured(method={'timeout_s':10.});b=configured();finish(a,20);self.assertEqual(a.fault,'TIMEOUT_OR_TUBE_6H');self.assertEqual(b.phase,'idle');self.assertEqual(b.volume,.001);self.assertLedger(a)
 def test_15_multi_solute(self):
  h=configured(sample={'solutes':[{'substance_id':'uniprot_p02769','mass_kg':.0005},{'substance_id':'uniprot_p00698','mass_kg':.0005}]});h.invoke('acquisition.start');h.step(500);self.assertLedger(h);self.assertGreater(h.permeate_mass[1],h.permeate_mass[0]);self.assertLessEqual(sum(h.adsorbed),h.plant['adsorption_capacity_kg'])
if __name__=='__main__':unittest.main()
