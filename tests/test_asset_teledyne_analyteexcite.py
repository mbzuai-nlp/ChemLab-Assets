from pathlib import Path
import unittest,sys,copy,math,json,importlib.util
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'scripts'));from asset_runtime import teledyne_analyteexcite as m
spec=importlib.util.spec_from_file_location('excite_asset_tool',R/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);OBSERVED=set()
def call(h,k,a=None):
 out=h.invoke(k,**(a or {}));errors=tool.validate_value(out,m.CAPS[k][1]);assert not errors,(k,errors);OBSERVED.add(k);return out
def ready(method=None,sample=None,plant=None):
 h=m.TeledyneAnalyteexciteRuntime();call(h,'plant.configure',dict(m.PLANT_EXAMPLE,**(plant or {})));call(h,'method.configure',dict(m.METHOD_EXAMPLE,**(method or {})));call(h,'power.set',{'enabled':True});call(h,'guard.set',{'angle_rad':1.});call(h,'drawer.set',{'open_m':.23});call(h,'sample.load',dict(m.SAMPLE_EXAMPLE,**(sample or {})));call(h,'drawer.set',{'open_m':0.});call(h,'guard.set',{'angle_rad':0.});return h
def finish(h):
 for _ in range(1000):
  if h.phase!='running':return
  h.step(.1)
 raise AssertionError('timeout')
class TestExcite(unittest.TestCase):
 def assertLedger(self,h):
  d=call(h,'ledger.read')
  for r in d['species']:self.assertLess(abs(r['residual_kg']),max(1e-19,r['loaded_kg']*1e-12))
  self.assertLess(abs(d['helium_residual_mol']),1e-12);self.assertLess(abs(d['argon_residual_mol']),1e-12)
 def test_01_templates(self):
  h=ready();self.assertGreaterEqual(len(h.library['substances']),50);self.assertEqual(call(h,'template.reference',{'reference_id':'NISTSRM610','substance_id':'Sr'})['mass_fraction'],515.5e-6)
  self.assertAlmostEqual(call(h,'template.reference',{'reference_id':'NISTSRM612','substance_id':'U'})['mass_fraction'],37.38e-6)
 def test_02_schema_and_purity(self):
  h=ready();a=copy.deepcopy(h.__dict__);h.describe();h.read();call(h,'process.measure');self.assertEqual(a,h.__dict__)
  for v in m.CAPS.values():self.assertFalse(tool.lint_schema(v[0]));self.assertFalse(tool.lint_schema(v[1]))
 def test_03_target_only(self):
  h=ready();call(h,'stage.move',{'position_m':[.04,.02,.01]});self.assertEqual(h.q,[0.,0.,0.]);h.step(.1);self.assertGreater(h.q[0],0);self.assertLess(h.q[0],.04)
 def test_04_atomic_refusal(self):
  h=ready();before=copy.deepcopy(h.__dict__)
  for k,a in [('stage.move',{'position_m':[.1,.1,.06]}),('method.configure',dict(m.METHOD_EXAMPLE,spot_size_m=40e-6)),('drawer.set',{'open_m':.2})]:
   with self.assertRaises(m.ActionRejected):call(h,k,a)
   self.assertEqual(before,h.__dict__)
 def test_05_finite_spot_and_energy(self):
  h=ready();call(h,'acquisition.start');finish(h);self.assertEqual(h.phase,'succeeded');r=call(h,'process.measure');self.assertEqual(len(r['pulses']),10);depth=10*1e-7*math.log(6e4*.95/1.5e4);surface=call(h,'surface.read');self.assertEqual(len(surface['cells']),100);self.assertAlmostEqual(surface['max_depth_m'],depth,places=16);self.assertAlmostEqual(h.removed,80e-6**2*depth,places=22);self.assertAlmostEqual(h.energy,10*6e4*.95*(80e-6)**2,places=14);self.assertLedger(h)
 def test_06_depletion_cap(self):
  h=ready(sample={'thickness_m':1e-8});call(h,'acquisition.start');finish(h);self.assertAlmostEqual(h.removed,(80e-6)**2*1e-8,places=24);call(h,'acquisition.start');finish(h);self.assertAlmostEqual(h.removed,(80e-6)**2*1e-8,places=24);self.assertLedger(h)
 def test_07_below_threshold(self):
  h=ready(method={'fluence_j_m2':1e4});call(h,'acquisition.start');finish(h);self.assertEqual(h.removed,0.);self.assertGreater(h.energy,0.);self.assertLedger(h)
 def test_08_two_volume_exact(self):
  h=ready(plant={'inner_volume_m3':1e-6,'outer_volume_m3':5e-6});h.inner={'Sr':1e-9};h.outer={'Sr':0.};h._transport(.25);ki=2.;ko=1.;self.assertAlmostEqual(h.inner['Sr'],1e-9*math.exp(-.5),places=22);self.assertAlmostEqual(h.outer['Sr'],2e-9*(math.exp(-.25)-math.exp(-.5)),places=22)
 def test_09_equal_rates(self):
  h=ready(plant={'inner_volume_m3':1e-6,'outer_volume_m3':2.5e-6});h.inner={'Sr':1e-9};h._transport(.25);self.assertAlmostEqual(h.outer['Sr'],.5e-9*math.exp(-.5),places=22)
 def test_10_continuous_scan(self):
  h=ready(method={'mode':'scan','path_m':[[.02,.02,.01],[.0208,.02,.01]],'scan_speed_m_s':.0004,'frequency_hz':20.});call(h,'acquisition.start');finish(h);r=call(h,'process.measure');self.assertEqual(len(r['pulses']),41);self.assertAlmostEqual(r['pulses'][-1]['position_m'][0],.0208,places=12);self.assertAlmostEqual(r['pulses'][1]['position_m'][0]-.02,.0004/20,places=13);self.assertEqual(h.q,[.0208,.02,.01]);self.assertLedger(h)
 def test_11_cancel_retains_then_flush(self):
  h=ready(method={'shots':300});call(h,'acquisition.start');h.step(1.4);self.assertGreater(len(h.pulses),0);old=h.removed;call(h,'acquisition.stop');a=copy.deepcopy(h.inner);h.step(.5);self.assertEqual(a,h.inner);self.assertEqual(h.removed,old);call(h,'washout.start');finish(h);self.assertEqual(h.removed,old);self.assertLedger(h)
 def test_12_output_handoff(self):
  h=ready();call(h,'acquisition.start');finish(h);p=call(h,'aerosol.collect');self.assertGreater(sum(x['mass_kg'] for x in p['species']),0);self.assertFalse(call(h,'aerosol.collect')['species']);self.assertLedger(h)
 def test_13_unload_and_service(self):
  h=ready();call(h,'guard.set',{'angle_rad':1.1});call(h,'drawer.set',{'open_m':.23});call(h,'sample.unload');call(h,'service.set',{'angle_rad':1.35});self.assertFalse(h.sample);self.assertLedger(h)
 def test_14_eqc(self):
  h=ready();v=call(h,'eqc.measure');self.assertAlmostEqual(v['fluence_j_m2'],57000.);self.assertAlmostEqual(v['pulse_energy_j'],57000*(80e-6)**2)
 def test_15_isolation_and_dt(self):
  a=ready();b=ready();call(a,'acquisition.start');call(b,'acquisition.start');a.step(4.);[b.step(.01) for _ in range(400)];self.assertEqual(a.read('evaluator'),b.read('evaluator'));self.assertEqual(a.depth,b.depth);call(a,'guard.set',{'angle_rad':1.});self.assertEqual(b.guard,0.)
 def test_16_permissions_dedupe_ownership(self):
  h=ready();h.register_principal('a');h.register_principal('b');h.register_principal('e','evaluator');a=h.session('a');b=h.session('b');self.assertNotIn('sample.identity',a.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',a.describe(instance_id=h.instance_id)['capabilities']);v=a.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='deny');self.assertEqual(v['status'],'rejected');kwargs=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='once');v=a.invoke(**kwargs);self.assertEqual(v,a.invoke(**kwargs));self.assertEqual(h.serial,1)
  with self.assertRaises(m.ActionRejected):b.poll(v['operation_id'])
  self.assertEqual(a.cancel(v['operation_id'],'cancel')['status'],'succeeded')
 def test_17_interlocks_and_loss(self):
  h=ready(plant={'helium_supply_mol':.001},method={'shots':300});call(h,'acquisition.start');before=copy.deepcopy(h.__dict__)
  with self.assertRaises(m.ActionRejected):call(h,'guard.set',{'angle_rad':1.})
  self.assertEqual(before,h.__dict__);h.step(20.);self.assertEqual(h.phase,'failed');self.assertEqual(h.failure,'GAS_DEPLETED');self.assertLedger(h)
 def test_18_ranges_grid_and_rate(self):
  h=ready(method={'shape':'circle','spot_size_m':50e-6,'frequency_hz':300.,'shots':30},sample={'grid_pitch_m':5e-6});call(h,'acquisition.start');finish(h);self.assertEqual(len(h.pulses),30);self.assertAlmostEqual(h.pulses[-1]['time_s']-h.pulses[0]['time_s'],29/300,places=11);self.assertLedger(h)
  for value in [float('nan'),-1,True]:
   with self.assertRaises(m.ActionRejected):h.step(value)
 def test_19_power_cancel_unsupported(self):
  h=ready();call(h,'acquisition.start');h.step(1.4);call(h,'power.set',{'enabled':False});self.assertEqual(h.phase,'cancelled');self.assertLedger(h)
  for k in m.UNSUPPORTED:
   with self.assertRaises(m.ActionRejected):h.invoke(k)
class TestCrmMaterials(unittest.TestCase):
 """2026-10-14: solids from assets/reactions/crm_element_library (NIST/USGS public values; GeoReM flagged)."""
 SHARED=R/'assets/reactions/crm_element_library/templates.json'
 @classmethod
 def setUpClass(cls):
  import hashlib
  cls.shared=json.loads(cls.SHARED.read_text());cls.byid={r['substance_id']:r for r in cls.shared['templates']};cls.sha=hashlib.sha256(cls.SHARED.read_bytes()).hexdigest()
 def test_20_material_counts_and_classes(self):
  h=m.TeledyneAnalyteexciteRuntime();mats=h.library['matrices']
  self.assertGreaterEqual(len(mats),50);self.assertEqual(sum(v['matrix_class']=='reference_glass' for v in mats.values()),26);self.assertEqual(sum(v['matrix_class']=='pressed_powder_pellet' for v in mats.values()),27)
  self.assertEqual(m.REFERENCE_IDS[:2],['NISTSRM610','NISTSRM612']);self.assertEqual(set(m.REFERENCE_IDS),set(mats));self.assertEqual(h.library['shared_library']['shared_library_sha256'],self.sha)
 def test_21_reference_query_provenance_and_licence(self):
  h=ready()
  r=call(h,'template.reference',{'reference_id':'nist_srm_614','substance_id':'Sr'});self.assertAlmostEqual(r['mass_fraction'],45.8e-6,places=15);self.assertIsNone(r['licence_flag']);self.assertEqual(r['value_category'],'certified');self.assertIn('nist_srm_614__sr',r['template_source'])
  r=call(h,'template.reference',{'reference_id':'usgs_bcr_2g','substance_id':'Ba'});self.assertAlmostEqual(r['mass_fraction'],683e-6,places=15);self.assertIsNone(r['licence_flag']);self.assertEqual(r['value_category'],'literature_estimate')
  r=call(h,'template.reference',{'reference_id':'mpi_ding_kl2_g','substance_id':'Sr'});self.assertEqual(r['licence_flag'],'unknown_licence');self.assertEqual(r['value_category'],'georem_preferred')
  r=call(h,'template.reference',{'reference_id':'NISTSRM610','substance_id':'Sr'});self.assertEqual(r['mass_fraction'],515.5e-6);self.assertEqual(r['licence_flag'],'unknown_licence');self.assertEqual(r['matrix_class'],'reference_glass')
  r=call(h,'template.reference',{'reference_id':'nist_srm_2709a','substance_id':'Pb'});self.assertEqual(r['matrix_class'],'pressed_powder_pellet');self.assertIsNone(r['licence_flag'])
  with self.assertRaises(m.ActionRejected):call(h,'template.reference',{'reference_id':'nist_srm_2709a','substance_id':'Ir'})
 def test_22_every_copied_value_matches_shared_record(self):
  h=m.TeledyneAnalyteexciteRuntime()
  for mid,mat in h.library['matrices'].items():
   if mid in ('NISTSRM610','NISTSRM612'):continue
   for el,row in mat['elements'].items():
    x=self.byid[row['source']['shared_substance_id']];self.assertEqual((x['element'],x['material_id'],x['value'],x['licence_flag']),(el,mid,row['mass_fraction'],row['licence_flag']))
   self.assertAlmostEqual(mat['matrix_remainder_fraction'],1-sum(r['mass_fraction'] for r in mat['elements'].values()),places=14)
 def test_23_ablated_aerosol_follows_material_composition(self):
  for mid,a,b in [('nist_srm_614','Sr','Pb'),('usgs_bcr_2g','Ba','Sr'),('mpi_ding_kl2_g','Sr','Zr'),('nist_srm_2709a','Fe','Mn'),('usgs_bhvo_2','Ti','Sr')]:
   h=ready(sample={'reference_id':mid,'density_kg_m3':2500. if 'srm_27' not in mid else 1800.});call(h,'acquisition.start');finish(h);self.assertEqual(h.phase,'succeeded')
   p={x['substance_id']:x['mass_kg'] for x in call(h,'aerosol.collect')['species']};w=h.library['matrices'][mid]['elements']
   self.assertGreater(p[a],0);self.assertAlmostEqual(p[a]/p[b],w[a]['mass_fraction']/w[b]['mass_fraction'],places=9)
   self.assertAlmostEqual(sum(p.values()),h.removed*h.sample['density_kg_m3']*h.plant['entrainment_fraction']*h.plant['delivery_fraction'],delta=1e-4*sum(p.values()))  # <1e-4: aerosol still in the cell after the 1 s washout
   self.assertIn('matrix_remainder',p);self.assertLedger(h)
 def assertLedger(self,h):
  d=call(h,'ledger.read')
  for r in d['species']:self.assertLess(abs(r['residual_kg']),max(1e-19,r['loaded_kg']*1e-12))
 def test_24_unknown_material_rejected_atomically(self):
  h=m.TeledyneAnalyteexciteRuntime();call(h,'plant.configure',m.PLANT_EXAMPLE);call(h,'power.set',{'enabled':True});call(h,'guard.set',{'angle_rad':1.});call(h,'drawer.set',{'open_m':.23});before=copy.deepcopy(h.__dict__)
  with self.assertRaises(m.ActionRejected):call(h,'sample.load',dict(m.SAMPLE_EXAMPLE,reference_id='NISTSRM999'))
  self.assertEqual(before,h.__dict__)

if __name__=='__main__':unittest.main()
