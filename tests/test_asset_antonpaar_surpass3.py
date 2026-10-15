import unittest,copy,math,importlib.util,json
from scripts.asset_runtime import antonpaar_surpass3 as r
spec=importlib.util.spec_from_file_location('asset_tool',r.ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);OBSERVED=set()
def call(h,k,a=None):
 v=h.invoke(k,**(a or {}));tool.validate_value(v,r.CAPS[k][1]);OBSERVED.add(k);return v
def setup(plant=None,method=None,liquid=None,sample=None):
 h=r.AntonpaarSurpass3Runtime()
 for k,a in [('plant.configure',dict(r.PLANT_EXAMPLE,**(plant or {}))),('method.configure',dict(r.METHOD_EXAMPLE,**(method or {}))),('lock.set',{'angle_rad':0.}),('cell.set',{'withdrawal_m':.095}),('sample.load',dict(r.SAMPLE_EXAMPLE,**(sample or {}))),('cell.set',{'withdrawal_m':0.}),('lock.set',{'angle_rad':1.2}),('gap.set',{'height_m':.0001}),('lid.set',{'lift_m':.105}),('electrolyte.load',dict(r.LIQUID_EXAMPLE,**(liquid or {}))),('lid.set',{'lift_m':0.}),('ph.calibrate',r.PHCAL_EXAMPLE),('conductivity.calibrate',r.COND_EXAMPLE),('power.set',{'enabled':True})]:call(h,k,a)
 return h
def finish(h):
 if h.phase!='running':call(h,'acquisition.start')
 for _ in range(1800):
  h.step(1.)
  if h.phase!='running':break
 return h
class Tests(unittest.TestCase):
 def ledger(self,h):
  l=call(h,'ledger.read')
  for k in ['volume_residual_m3','k_residual_mol','cl_residual_mol','electroneutral_residual_mol','titrant_residual_m3','gas_mole_residual']:self.assertLess(abs(l[k]),1e-13,k)
  self.assertLess(abs(l['work_residual_j']),1e-10);self.assertGreaterEqual(h.V-sum(h.chamber),-1e-15)
 def test_01_original_references(self):
  h=setup();self.assertEqual(len(h.library['substances']),h.library['counts']['total_records']);self.assertEqual(sum(k.startswith('nanomile_') for k in h.library['substances']),69);self.assertAlmostEqual(call(h,'template.reference',{'substance_id':'nanomile_2'})['zeta_v'],-.00121);self.assertFalse(call(h,'template.reference',{'substance_id':'nanomile_2'})['applicable_to_surpass']);self.assertEqual(call(h,'template.reference',{'substance_id':'cotton_agcl'})['zeta_v'],-.019)
  with self.assertRaisesRegex(r.ActionRejected,'PARTICLE_REFERENCE_ONLY'):setup(sample={'reference_id':'nanomile_2'})
 def test_02_complete_ols_and_iep(self):
  h=finish(setup());self.assertEqual(h.phase,'succeeded');self.assertEqual(len(h.fits),16);self.assertAlmostEqual(h._iep(),4.1,places=6)
  for f in h.fits:
   expected=-.019-.01*(f['ph']-6);self.assertAlmostEqual(f['zeta_from_i_v'],expected,places=10);self.assertAlmostEqual(f['zeta_from_v_v'],expected,places=10);self.assertGreater(f['r2_i'],.999999)
  self.ledger(h);self.assertEqual({x['direction'] for x in call(h,'process.measure')['trace']},{1,-1})
 def test_03_surface_conduction_bias(self):
  h=finish(setup(plant={'surface_conductance_s':1e-6},method={'ph_targets':[6.],'cycles':1}));f=h.fits[0];self.assertAlmostEqual(f['zeta_from_i_v'],-.019,places=12);self.assertLess(abs(f['zeta_from_v_v']),abs(f['zeta_from_i_v']));self.ledger(h)
 def test_04_ideal_chemistry_titration(self):
  h=finish(setup(method={'ph_targets':[4.,8.,5.],'cycles':1}));self.assertEqual(h.phase,'succeeded');self.assertGreater(h.added[0],0);self.assertGreater(h.added[1],0);self.assertAlmostEqual(h.chemistry()[0],5.,places=6);self.ledger(h)
 def test_05_independent_pressure_work(self):
  h=setup(method={'ph_targets':[6.],'cycles':1});call(h,'acquisition.start');h.step(1.);self.assertEqual(h.stage,'ramp');self.ledger(h);p=h.plant;pv=(h.method['start_pressure_pa']+p['ambient_pa'])*p['gas_volume_m3'];expected=pv*math.log((p['gas_volume_m3']+h.moved)/p['gas_volume_m3'])-p['ambient_pa']*h.moved;self.assertAlmostEqual(h.work,expected,places=10);self.assertAlmostEqual(h.gas_n,(h.pressure+p['ambient_pa'])*(h.gas_v+h.moved)/8.314462618/p['temperature_k'],places=12)
 def test_06_cancel_partial_dose_and_poweroff(self):
  h=setup(method={'ph_targets':[3.],'cycles':1});call(h,'acquisition.start');h.step(30.);self.assertGreater(h.added[0],0);call(h,'acquisition.stop');before=[h.V,h.nk,h.ncl];h.step(20);self.assertEqual(before,[h.V,h.nk,h.ncl]);self.assertEqual(h.phase,'cancelled');self.ledger(h)
  h=setup(method={'ph_targets':[6.],'cycles':1});call(h,'acquisition.start');h.step(1);call(h,'power.set',{'enabled':False});self.assertEqual(h.phase,'cancelled');self.assertEqual(h.pressure,0);self.ledger(h)
 def test_07_stock_depletion(self):
  h=finish(setup(method={'ph_targets':[3.],'cycles':1},liquid={'acid_stock_m3':1e-7}));self.assertEqual(h.failure,'TITRANT_DEPLETED');self.assertGreater(h.added[0],0);self.ledger(h)
 def test_08_capacity_failure(self):
  h=finish(setup(method={'ph_targets':[3.],'cycles':1},liquid={'volume_m3':.00025}));self.assertEqual(h.failure,'BEAKER_CAPACITY');self.ledger(h)
 def test_09_blocked_pressure_timeout(self):
  h=finish(setup(plant={'blocked_fraction':1.},method={'ph_targets':[6.],'cycles':1,'ramp_timeout_s':10.}));self.assertEqual(h.failure,'PRESSURE_TIMEOUT');self.assertEqual(h.circulated,0);self.assertTrue(all(x['streaming_a']==0 for x in h.trace));self.ledger(h)
 def test_10_chamber_empty_failure(self):
  h=finish(setup(plant={'gas_volume_m3':1e-4},method={'ph_targets':[6.],'cycles':1,'fill_volume_m3':2e-5}));self.assertEqual(h.failure,'CHAMBER_EMPTY');self.ledger(h)
 def test_11_read_pure_isolation_partition(self):
  a=setup();b=copy.deepcopy(a);call(a,'acquisition.start');call(b,'acquisition.start');a.step(7.3)
  for _ in range(73):b.step(.1)
  self.assertEqual(a.read('evaluator'),b.read('evaluator'));old=a.read('evaluator');call(a,'process.measure');a.describe();call(a,'ledger.read');self.assertEqual(a.read('evaluator'),old);c=setup();self.assertEqual(c.time_s,0)
 def test_12_invalid_atomic_interlocks(self):
  h=setup();old=h.read('evaluator')
  for k,a in [('cell.set',{'withdrawal_m':.09}),('method.configure',dict(r.METHOD_EXAMPLE,start_pressure_pa=20000.,stop_pressure_pa=30000.)),('gap.set',{'height_m':.0001005}),('power.set',{'enabled':1}),('ph.calibrate',{'buffer_ph':[4.,4.],'measured_v':[-.2,-.2]})]:
   with self.assertRaises(r.ActionRejected):call(h,k,a)
   self.assertEqual(h.read('evaluator'),old)
  call(h,'acquisition.start')
  with self.assertRaisesRegex(r.ActionRejected,'BUSY'):call(h,'lid.set',{'lift_m':.1})
 def test_13_calibration_bias_correction(self):
  h=setup(plant={'ph_slope_v':-.06,'ph_offset_v':.01,'conductivity_gain':1.08},method={'ph_targets':[6.],'cycles':1});call(h,'ph.calibrate',{'buffer_ph':[4.,7.],'measured_v':[-.23,-.41]});call(h,'conductivity.calibrate',{'reference_s_m':.1413,'indicated_s_m':.1413*1.08});self.assertAlmostEqual(h.indicated()[0],6.,places=8);self.assertAlmostEqual(h.indicated()[1],h.chemistry()[1],places=12);h=finish(h);self.assertAlmostEqual(h.fits[0]['zeta_from_v_v'],-.019,places=10)
 def test_14_no_unbracketed_iep_and_rinse(self):
  h=finish(setup(method={'ph_targets':[6.,7.],'cycles':1}));self.assertIsNone(h._iep());h=finish(setup(method={'ph_targets':[6.],'cycles':1,'mode':'rinse'}));self.assertEqual(h.phase,'succeeded');self.assertFalse(h.fits);self.assertGreater(h.circulated,0);self.ledger(h)
 def test_15_drain_and_sample_unload(self):
  h=finish(setup(method={'ph_targets':[6.],'cycles':1}));call(h,'lid.set',{'lift_m':.105});v=call(h,'electrolyte.drain');self.assertGreater(v['volume_m3'],0);self.assertEqual(call(h,'electrolyte.drain')['volume_m3'],0);call(h,'lock.set',{'angle_rad':0.});call(h,'cell.set',{'withdrawal_m':.095});call(h,'sample.unload');self.assertFalse(h.sample);self.ledger(h)
 def test_16_electrical_limits_and_sensor_offsets(self):
  h=finish(setup(plant={'voltage_gain':1.3,'current_gain':.8,'voltage_offset_v':.01,'current_offset_a':1e-7},method={'ph_targets':[6.],'cycles':1}));self.assertAlmostEqual(h.fits[0]['zeta_from_v_v'],-.019*1.3,places=10);self.assertAlmostEqual(h.fits[0]['zeta_from_i_v'],-.019*.8,places=10)
  h=finish(setup(method={'ph_targets':[8.],'cycles':1},liquid={'kcl_mol_m3':.01},sample={'ph_slope_v':-.05}));self.assertEqual(h.failure,'ELECTRICAL_RANGE');self.ledger(h)
 def test_17_gap_flow_and_tubing(self):
  a=setup(method={'ph_targets':[6.],'cycles':1});b=setup(plant={'extra_resistance_pa_s_m3':5e10},method={'ph_targets':[6.],'cycles':1});call(a,'acquisition.start');call(b,'acquisition.start');a.step(1.);b.step(1.);self.assertGreater(a.circulated,b.circulated);self.assertLess(b.cell_pressure,b.pressure);finish(b);self.assertEqual(b.phase,'succeeded');self.assertAlmostEqual(b.fits[0]['zeta_from_i_v'],-.019,places=12)
 def test_18_permissions_dedup_schema(self):
  h=setup();h.register_principal('a');h.register_principal('b');h.register_principal('eval','evaluator');s=h.session('a');q=h.session('b');self.assertNotIn('sample.identity',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',s.describe(instance_id=h.instance_id)['capabilities']);args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');v=s.invoke(**args);self.assertEqual(v,s.invoke(**args));self.assertEqual(s.invoke(**dict(args,capability_id='acquisition.stop'))['error']['code'],'REQUEST_CONFLICT')
  with self.assertRaises(r.ActionRejected):q.poll(v['operation_id'])
  self.assertEqual(s.cancel(v['operation_id'],'cancel')['status'],'succeeded');self.assertEqual(s.poll(v['operation_id'])['status'],'cancelled');self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='forbidden')['error']['code'],'ACCESS_DENIED')
  for k in r.UNSUPPORTED:
   with self.assertRaisesRegex(r.ActionRejected,'UNSUPPORTED'):h.invoke(k)
  for dt in [True,-1,float('nan'),float('inf')]:
   with self.assertRaises(r.ActionRejected):h.step(dt)
 # ---- literature macroscopic surfaces (zeta_library.json runtime modes) ----
 def lit(self,sid,targets,kcl=1.,cycles=1,**sample):
  h=finish(setup(method={'ph_targets':targets,'cycles':cycles},liquid={'kcl_mol_m3':kcl},sample=dict({'reference_id':sid,'sample_id':'private_'+sid},**sample)));return h
 def test_19_curve_records_across_classes(self):
  for sid,targets in [('cotton_fabric_bleached_pmc7178377',[8.,5.]),('pip_pa_nf_lab_pmc12073387',[6.,4.]),('titanium_cp_pmc9324478',[5.,4.5]),('glass_slide_pmc7077729',[4.,3.]),('pvdf_nh2_hyperbranched_pmc5512528',[8.,7.5]),('nf90_compiled_pmc9317247',[6.,4.])]:
   e=r.AntonpaarSurpass3Runtime().library['substances'][sid]['electrolyte'];h=self.lit(sid,targets,accept_electrolyte_mismatch=not(e['salt']=='KCl' and e['concentration_mol_m3']==1.));ref=h.library['substances'][sid];self.assertIn(ref['runtime_mode'],('anchor_curve','measured_curve'));self.assertEqual(h.phase,'succeeded',sid);self.assertEqual(len(h.fits),len(targets)*2)
   for f in h.fits:self.assertAlmostEqual(f['zeta_from_i_v'],r.reference_zeta(ref,f['ph'],-.01),places=9);self.assertGreater(f['r2_i'],.999999)
   res=call(h,'process.measure');self.assertIn(sid,res['template_source']);self.assertTrue(any('piecewise-linear' in w for w in res['warnings']));self.ledger(h)
  h=self.lit('psf_membrane_m0_pmc6926845',[6.,5.],accept_electrolyte_mismatch=True);ref=h.library['substances']['psf_membrane_m0_pmc6926845'];self.assertEqual(ref['runtime_mode'],'measured_curve')
  for f in h.fits:self.assertAlmostEqual(f['zeta_from_i_v'],r.reference_zeta(ref,f['ph'],-.01),places=9)
  ref=h.library['substances']['nf90_compiled_pmc9317247']
  for x,y in zip(ref['curve']['ph'],ref['curve']['zeta_v']):self.assertAlmostEqual(r.reference_zeta(ref,x,-.05),y,places=12)
  self.assertEqual(r.reference_zeta(ref,2.,-.05),ref['curve']['zeta_v'][0]);self.assertEqual(r.reference_zeta(ref,11.5,-.05),ref['curve']['zeta_v'][-1])
  c=h.library['substances']['cotton_cationized_chptac_pmc8510459'];self.assertGreater(r.reference_zeta(c,2.5,-.05),0);self.assertAlmostEqual(r.reference_zeta(c,2.5,-.05),.0075/3.24*.76,places=12)
  n=h.library['substances']['pvdf_nh2_hyperbranched_pmc5512528'];self.assertLess(r.reference_zeta(n,10.,-.05),0)
 def test_20_published_iep_reproduced(self):
  h=self.lit('pip_pa_nf_lab_pmc12073387',[4.3,4.5]);self.assertLess(abs(h._iep()-4.4),.05)
  h=self.lit('cotton_cationized_chptac_pmc8510459',[3.,3.5]);self.assertLess(abs(h._iep()-3.26),.05)
  h=self.lit('ptfe_untreated_pmc6213335',[3.,4.],ph_slope_v=-.01);self.assertAlmostEqual(h._iep(),3.4,places=6)
  for f in h.fits:self.assertAlmostEqual(f['zeta_from_i_v'],-.01*(f['ph']-3.4),places=9)
  self.assertTrue(any('host assumption' in w for w in call(h,'process.measure')['warnings']))
 def test_21_electrolyte_mismatch_and_ph_range(self):
  h=setup(method={'ph_targets':[6.],'cycles':1},sample={'reference_id':'psf_membrane_nmp_pmc11279014'})
  with self.assertRaisesRegex(r.ActionRejected,'ELECTROLYTE_MISMATCH'):call(h,'acquisition.start')
  h=finish(setup(method={'ph_targets':[6.],'cycles':1},sample={'reference_id':'psf_membrane_nmp_pmc11279014','accept_electrolyte_mismatch':True}));self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.fits[0]['zeta_from_i_v'],-.03009,places=9)
  self.assertTrue(any('mismatch' in w for w in call(h,'process.measure')['warnings']))
  h=setup(method={'ph_targets':[10.],'cycles':1},liquid={'kcl_mol_m3':1.},sample={'reference_id':'cotton_fabric_bleached_pmc7178377','accept_ph_extrapolation':False})
  with self.assertRaisesRegex(r.ActionRejected,'PH_OUTSIDE_REFERENCE'):call(h,'acquisition.start')
  h=finish(setup(method={'ph_targets':[8.],'cycles':1},liquid={'kcl_mol_m3':1.},sample={'reference_id':'cotton_fabric_bleached_pmc7178377','accept_ph_extrapolation':False}));self.assertEqual(h.phase,'succeeded')
  h=finish(setup(method={'ph_targets':[10.],'cycles':1},liquid={'kcl_mol_m3':1.},sample={'reference_id':'cotton_fabric_bleached_pmc7178377'}));self.assertAlmostEqual(h.fits[0]['zeta_from_i_v'],-.020,places=9)
  h=setup(method={'ph_targets':[6.],'cycles':1},liquid={'kcl_mol_m3':1.},sample={'reference_id':'sapphire_c_pmc4084798'})
  with self.assertRaisesRegex(r.ActionRejected,'ELECTROLYTE_MISMATCH'):call(h,'acquisition.start')
 def test_22_load_rules_and_listing(self):
  for sample,code in [({'reference_id':'ptfe_untreated_pmc6213335','accept_ph_extrapolation':False},'LOAD_PRECONDITION'),({'reference_id':'gold_on_glass_pmc12199473','ph_slope_v':.01},'LOAD_PRECONDITION'),({'reference_id':'no_such_surface'},'UNKNOWN_REFERENCE'),({'reference_id':'nanomile_5'},'PARTICLE_REFERENCE_ONLY'),({'reference_id':'cotton_fabric_bleached_pmc7178377','accept_geometry_transfer':False},'LOAD_PRECONDITION')]:
   with self.assertRaisesRegex(r.ActionRejected,code):setup(sample=sample)
  h=r.AntonpaarSurpass3Runtime();lst=call(h,'template.list')['references'];ids={x['substance_id'] for x in lst};lib=h.library
  self.assertEqual(len(lst),lib['counts']['runtime_usable_records']);self.assertFalse(any(i.startswith('nanomile_') for i in ids));self.assertIn('cotton_agcl',ids)
  self.assertGreaterEqual(len({x['surface_identity'] for x in lst}),50);self.assertGreaterEqual(len({x['material_class'] for x in lst}),7)
  v=call(h,'template.reference',{'substance_id':'xle_compiled_pmc9317247'});self.assertEqual(v['runtime_mode'],'anchor_curve');self.assertEqual(v['iep_ph'],3.5);self.assertEqual(v['curve_points'],6);self.assertIn('europepmc',v['template_source'].lower()+lib['substances']['xle_compiled_pmc9317247']['source']['url'])
  v=call(h,'template.reference',{'substance_id':'gold_on_glass_pmc12199473'});self.assertEqual((v['runtime_mode'],v['ph'],v['zeta_v']),('point_host_slope',6.4,-.076));self.assertEqual(v['electrolyte'],'KCl 1.0 mol/m3')
  v=call(h,'template.reference',{'substance_id':'nanomile_2'});self.assertEqual(v['runtime_mode'],'reference_only')
 def test_23_every_usable_record_loads_and_is_finite(self):
  h0=r.AntonpaarSurpass3Runtime();ids=[x['substance_id'] for x in call(h0,'template.list')['references']]
  for sid in ids:
   ref=h0.library['substances'][sid];h=setup(sample={'reference_id':sid,'accept_electrolyte_mismatch':True});self.assertEqual(h.sample['reference_id'],sid)
   for ph in [2.,3.5,5.,7.,9.,12.]:z=r.reference_zeta(ref,ph,-.01);self.assertTrue(math.isfinite(z) and abs(z)<.3,sid)
   for p in ref.get('published_points') or []:
    if ref['curve']:self.assertAlmostEqual(r.reference_zeta(ref,p['ph'],-.01),p['zeta_v'],places=12)
   if ref['runtime_mode']=='iep_host_slope':self.assertEqual(r.reference_zeta(ref,ref['iep_ph'],-.01),0.)
   self.assertTrue(ref['source'].get('locator') or ref['source'].get('page'));self.assertIn(ref['evidence'],('measured_literature','documented_reference'))
 def test_24_sample_runs_by_class(self):
  done=set();h0=r.AntonpaarSurpass3Runtime()
  for x in call(h0,'template.list')['references']:
   if x['material_class'] in done or x['substance_id']=='cotton_agcl':continue
   done.add(x['material_class']);ref=h0.library['substances'][x['substance_id']];e=ref['electrolyte'] or {}
   kcl=e.get('concentration_mol_m3') if e.get('salt')=='KCl' and e.get('concentration_mol_m3') and e['concentration_mol_m3']<=60 else 1.
   t=min(max(ref['curve']['ph'][0],3.),9.) if ref['curve'] else 6.
   h=self.lit(x['substance_id'],[t],kcl=kcl,accept_electrolyte_mismatch=True);self.assertEqual(h.phase,'succeeded',x);self.assertAlmostEqual(h.fits[0]['zeta_from_i_v'],r.reference_zeta(ref,h.fits[0]['ph'],-.01),places=9);self.ledger(h)
  self.assertGreaterEqual(len(done),7)
if __name__=='__main__':unittest.main()
