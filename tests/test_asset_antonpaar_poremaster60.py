import unittest,sys,copy,math,json,hashlib,zipfile,io
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/expansion_batch03/t4_measure/antonpaar_poremaster60';sys.path.insert(0,str(OUT));from example_setup import rt,setup,run,high,call,OBSERVED

def _need(p):
    """Raise FileNotFoundError for a missing build record or source cache (the public release skips such tests)."""
    if not Path(p).exists():
        raise FileNotFoundError(2, "not present in this tree", str(p))

class PorosimetryTests(unittest.TestCase):
 def test_01_si_washburn_and_mass_normalized_measurement(self):
  r=setup();out=run(r)
  self.assertEqual(len(out['points']),4)
  for p in out['points']:
   self.assertAlmostEqual(p['diameter_m']*p['pressure_pa'],-4*.48*math.cos(math.radians(140)),places=12)
   xs,ys=rt.CURVES[(rt.SID,'intrusion')];expected=rt.interp(xs,ys,p['pressure_pa']);self.assertLess(abs(p['specific_volume_m3_kg']-expected),2e-6)
 def test_02_real_cycle_energy_and_ledgers(self):
  r=setup();run(r);high(r);out=run(r);l=call(r,'ledger.read');self.assertEqual(len(out['points']),13);self.assertEqual(sum(p['branch']=='extrusion' for p in out['points']),4);self.assertGreater(out['max_pore_volume_m3_kg'],.0025)
  for k in ['hg_residual_kg','solid_residual_kg','gas_residual_mol','oil_residual_m3']:self.assertLess(abs(l[k]),1e-12)
  self.assertGreater(l['hydraulic_pump_work_j'],4000);self.assertLess(abs(l['work_balance_error_j']),.01);self.assertGreater(l['capillary_work_j'],0);self.assertGreater(l['hg_intruded_kg'],0)
 def test_03_fibre_missing_sparse_columns_recovered(self):
  d=rt.LIB['substances']['hemp_fibre'];xs,_=rt.CURVES[('hemp_fibre','intrusion')];self.assertGreater(len(xs),800);self.assertAlmostEqual(max(xs),400911800.);r=setup(ref='hemp_fibre');run(r);high(r);out=run(r);self.assertEqual(out['phase'],'succeeded')
 def test_04_no_fabricated_rock_extrusion(self):
  r=setup(ref='dggs_09BG010-38.5b',mass=.0002);run(r);high(r,cycle=False)
  m=copy.deepcopy(r.method);m['pressures_pa']+=[1e8]
  with self.assertRaisesRegex(rt.ActionRejected,'EXTRUSION_UNAVAILABLE'):r.invoke('method.configure',**m)
 def test_05_no_partial_source_peak_hysteresis(self):
  r=setup();run(r);high(r);m=copy.deepcopy(r.method);m['pressures_pa']=[50*rt.PSI,1e6,1e8,1e6]
  with self.assertRaisesRegex(rt.ActionRejected,'SOURCE_PEAK_REQUIRED'):r.invoke('method.configure',**m)
 def test_06_no_template_extrapolation(self):
  r=setup();m=copy.deepcopy(r.method);m['pressures_pa']=[.2*rt.PSI,10*rt.PSI]
  with self.assertRaisesRegex(rt.ActionRejected,'TEMPLATE_RANGE'):r.invoke('method.configure',**m)
 def test_07_wrong_entered_mass_changes_reading(self):
  a=setup();b=setup(entered_mass=rt.SAMPLE_EXAMPLE['true_dry_mass_kg']*2);ra=run(a);rb=run(b)
  self.assertAlmostEqual(a.v,b.v,places=14)
  for x,y in zip(ra['points'],rb['points']):self.assertAlmostEqual(x['specific_volume_m3_kg'],y['specific_volume_m3_kg']*2,places=13)
 def test_08_sensor_calibration_is_independent(self):
  r=setup(plant={'pressure_gain':[1.1,1.05,.95],'pressure_offset_pa':[20.,100.,10000.],'capacitance_volume_factor_m3_f':31000.,'capacitance_offset_f':7e-12});out=run(r)
  point=out['points'][-1];xs,ys=rt.CURVES[(rt.SID,'intrusion')];self.assertLess(abs(point['specific_volume_m3_kg']-rt.interp(xs,ys,point['pressure_pa'])),2e-6)
 def test_09_blank_correction_is_observable(self):
  good=setup();bad=setup();call(bad,'blank.calibrate',pressures_pa=[0.,rt.P_MAX],volumes_m3=[0.,0.]);run(good);run(bad);high(good);high(bad);a=run(good);b=run(bad);self.assertGreater(b['max_pore_volume_m3_kg']-a['max_pore_volume_m3_kg'],.0004)
 def test_10_mercury_is_finite(self):
  r=setup();r.hg_added=r.hg_stock=.001;call(r,'acquisition.start')
  for _ in range(500):
   r.step(.1)
   if r.phase!='running':break
  self.assertEqual(r.failure,'MERCURY_EXHAUSTED');self.assertAlmostEqual(r._ledger()['hg_residual_kg'],0,places=12);self.assertGreater(r.hg_cell,0)
 def test_11_oil_is_finite(self):
  r=setup(plant={'oil_volume_m3':1e-6});run(r);high(r);call(r,'acquisition.start')
  for _ in range(10000):
   r.step(.1)
   if r.phase!='running':break
  self.assertEqual(r.failure,'OIL_EXHAUSTED');self.assertAlmostEqual(r._ledger()['oil_residual_m3'],0,places=14)
 def test_12_gas_supply_is_finite(self):
  r=setup(plant={'source_volume_m3':1e-5,'source_pressure_pa':50*rt.PSI});call(r,'acquisition.start')
  for _ in range(10000):
   r.step(.1)
   if r.phase!='running':break
  self.assertEqual(r.failure,'GAS_EXHAUSTED');self.assertAlmostEqual(r._ledger()['gas_residual_mol'],0,places=12)
 def test_13_cancel_retains_pressure_and_samples(self):
  r=setup();run(r);high(r);call(r,'acquisition.start')
  while r.p<1e7:r.step(.1)
  before=(r.p,r.hg_cell,r.sample.copy(),r.oil);call(r,'acquisition.stop');self.assertEqual(before,(r.p,r.hg_cell,r.sample.copy(),r.oil))
  with self.assertRaisesRegex(rt.ActionRejected,'PRESSURE_ACCESS_INTERLOCK'):r.invoke('cap.set',lift_m=.19)
  with self.assertRaisesRegex(rt.ActionRejected,'NOT_READY'):r.invoke('acquisition.start')
  r.step(40.);self.assertLess(abs(r.p-rt.AMBIENT),1);self.assertEqual(r.phase,'cancelled')
 def test_14_emergency_and_power_stop(self):
  r=setup();call(r,'acquisition.start');r.step(2.);call(r,'emergency.stop');self.assertEqual(r.phase,'cancelled');call(r,'power.set',enabled=False);self.assertEqual(r.read()['lamp.power'],False);r.step(10.);self.assertLess(abs(r.p-rt.AMBIENT),1)
 def test_15_unload_retains_hg_in_sample(self):
  r=setup();run(r);high(r);run(r);call(r,'cap.set',lift_m=.19);call(r,'guard0.set',angle_rad=1.4);call(r,'cell.transfer',destination='low0');r.step(5.);call(r,'cell.drain');out=call(r,'sample.unload');self.assertGreater(out['retained_hg_kg'],.002);self.assertAlmostEqual(out['returned_dry_mass_kg'],.0000655);l=call(r,'ledger.read');self.assertLess(abs(l['hg_residual_kg']),1e-12);self.assertLess(abs(l['solid_residual_kg']),1e-12)
 def test_16_second_low_port_and_cumulative_xyz(self):
  r=setup(low1=True);self.assertEqual(r.read()['cell.x_m'],.328);run(r);high(r);self.assertEqual([r.read()['cell.'+a+'_m'] for a in ['x','y','z']],[.164,.185,.265])
 def test_17_motion_interlocks(self):
  r=setup()
  with self.assertRaisesRegex(rt.ActionRejected,'TRANSFER_INTERLOCK'):r.invoke('cell.transfer',destination='high')
  call(r,'guard0.set',angle_rad=1.4)
  with self.assertRaisesRegex(rt.ActionRejected,'POSITION_INTERLOCK'):r.invoke('acquisition.start')
 def test_18_sample_capacity_atomicity(self):
  r=rt.AntonpaarPoremaster60Runtime();call(r,'plant.configure',**rt.PLANT_EXAMPLE);call(r,'guard0.set',angle_rad=1.4);before=copy.deepcopy(r.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'CAPACITY'):r.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,true_dry_mass_kg=.1))
  self.assertEqual(before,r.__dict__)
 def test_19_inputs_strict_nan_bool_unknown(self):
  r=setup();before=copy.deepcopy(r.__dict__)
  for a in [{'enabled':1},{'enabled':True,'oops':1}]:
   with self.assertRaises(rt.ActionRejected):r.invoke('power.set',**a)
  with self.assertRaises(rt.ActionRejected):r.invoke('cap.set',lift_m=float('nan'))
  with self.assertRaises(rt.ActionRejected):r.step(True)
  self.assertEqual(before,r.__dict__)
 def test_20_fixed_clock_partition_invariance(self):
  a=setup();b=setup();call(a,'acquisition.start');call(b,'acquisition.start');a.step(20.)
  for _ in range(400):b.step(.05)
  self.assertEqual(a.read('evaluator'),b.read('evaluator'));self.assertEqual(a._ledger(),b._ledger())
 def test_21_read_purity(self):
  r=setup();call(r,'acquisition.start');r.step(5.);before=copy.deepcopy(r.__dict__)
  for _ in range(5):call(r,'process.measure');call(r,'ledger.read');call(r,'template.reference',substance_id=rt.SID);r.read('evaluator')
  self.assertEqual(before,r.__dict__)
 def test_22_session_visibility(self):
  r=setup();r.register_principal('a');r.register_principal('e','evaluator');a=r.session('a');e=r.session('e');self.assertNotIn('sample.identity',a.read(instance_id=r.instance_id)['observations']);self.assertIn('sample.identity',e.read(instance_id=r.instance_id)['observations']);ans=a.invoke(instance_id=r.instance_id,capability_id='ledger.read',arguments={},request_id='deny');self.assertEqual(ans['error']['code'],'ACCESS_DENIED')
  with self.assertRaises(rt.ActionRejected):a.read(instance_id=r.instance_id,state_ids=['sample.mass_kg'])
 def test_23_idempotency_and_async_ownership(self):
  r=setup();r.register_principal('a');r.register_principal('b');a=r.session('a');b=r.session('b');kwargs=dict(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');x=a.invoke(**kwargs);y=a.invoke(**kwargs);self.assertEqual(x,y);self.assertEqual(r.serial,1)
  with self.assertRaises(rt.ActionRejected):b.poll(x['operation_id'])
  res=a.invoke(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='start');self.assertEqual(res['error']['code'],'REQUEST_CONFLICT');self.assertEqual(a.cancel(x['operation_id'],'cancel')['status'],'succeeded');self.assertEqual(a.poll(x['operation_id'])['status'],'cancelled')
 def test_24_unsupported_not_faked(self):
  r=setup()
  for k in rt.UNSUPPORTED:
   with self.assertRaisesRegex(rt.ActionRejected,'UNSUPPORTED'):r.invoke(k)
 def test_25_library_identity_and_units(self):
  original_ids=set('dggs_09BG010-38.5b dggs_13A017-12.7b dggs_13A017-6.0b dggs_13A017-6.15b dggs_13A017-6.8b dggs_13A017-8.2b dggs_13A017-8.3b dggs_13A027-0.5c dggs_13A027-1.9b dggs_13A027-100.5c dggs_13A027-113.1c dggs_13A027-122.8c dggs_13A027-14.5c dggs_13A027-143.0c dggs_13A027-151.0c dggs_13A027-157.7b dggs_13A027-160.0c dggs_13A027-169.4c dggs_13A027-180.5c dggs_13A027-190.8c dggs_13A027-198.8c dggs_13A027-209.9c dggs_13A027-220.2c dggs_13A027-23.6c dggs_13A027-231.4c dggs_13A027-238.8c dggs_13A027-250.2c dggs_13A027-259.4c dggs_13A027-270.0c dggs_13A027-282.0c dggs_13A027-292.4c dggs_13A027-300.2c dggs_13A027-315.2c dggs_13A027-320.8c dggs_13A027-323.2b dggs_13A027-323.8b dggs_13A027-326.8b dggs_13A027-329.5b dggs_13A027-332.2b dggs_13A027-336.0b dggs_13A027-338.1b dggs_13A027-339.8c dggs_13A027-39.0c dggs_13A027-48.6c dggs_13A027-56.8c dggs_13A027-68.0c dggs_13A027-74.2b dggs_13A027-8.4b dggs_13A027-80.1b dggs_13A027-81.0c dggs_13A027-9.5b dggs_13A027-9.6b dggs_13A027-94.5c dggs_13A027-99.5c limestone_indiana limestone_st_maximin_sml limestone_majella_m limestone_thala_th22a limestone_purbeck_p21 limestone_leitha_ua_31_ limestone_leitha_ul4_26_ limestone_leitha_ub17_21_ hemp_shiv hemp_fibre'.split());self.assertTrue(original_ids<=rt.LIB['substances'].keys());self.assertGreaterEqual(len(rt.LIB['substances']),64);self.assertGreaterEqual(len(set(v['material_group'] for v in rt.LIB['substances'].values())),13)
  source_hashes={}
  for k,v in rt.LIB['substances'].items():
   if k in original_ids:
    source=v['source'];path=OUT/source['file']
    if path not in source_hashes:source_hashes[path]=hashlib.sha256(path.read_bytes()).hexdigest()
    self.assertEqual(source_hashes[path],source['sha256']);self.assertTrue(source['url']);self.assertEqual(v['evidence'],'measured_literature')
    if 'raw_member_sha256' in source:
     with zipfile.ZipFile(path) as archive:self.assertEqual(hashlib.sha256(archive.read(source['locator'].split(' / ')[0])).hexdigest(),source['raw_member_sha256'])
   if rt.CURVES[(k,'extrusion')][0]:
    self.assertTrue(v['evidence'] in ['measured','measured_literature'] or (v['evidence']=='derived' and v['pressure_axis']=='measured' and v['volume_basis']=='saturation_x_specific_pore_volume'));self.assertEqual(v['conditions']['extrusion_status'],'experimental_descent_from_source_peak');self.assertTrue(v['source']['url']);self.assertTrue(v['source']['sha256'])
  d=rt.LIB['substances']['dggs_09BG010-38.5b'];p=d['value'][0];self.assertAlmostEqual(p['pressure_pa'],1.64*rt.PSI);self.assertAlmostEqual(p['volume_m3_kg'],7.07868e-8);self.assertAlmostEqual(d['conditions']['closure_m3_kg'],.0025669119*.001)
 def test_26_metadata_exposes_real_contract(self):
  d=rt.export_antonpaar_poremaster60_contract_metadata();self.assertEqual(set(d['state_slots']),set(rt.SLOTS));self.assertEqual(len(d['capabilities']),28);self.assertEqual(sum(c['availability']=='implemented' for c in d['capabilities'].values()),21)
 def test_27_overlap_not_a_resolved_pore_bin(self):
  r=setup();run(r);high(r);out=run(r);self.assertEqual(len(out['distribution']['diameter_m']),7);ds=out['distribution']['diameter_m'];self.assertTrue(all(a>b for a,b in zip(ds,ds[1:])))
 def test_28_completed_operation_survives_sample_replacement(self):
  r=setup();r.register_principal('a');s=r.session('a');ans=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='old_sample');op=ans['operation_id']
  while r.phase=='running':r.step(.1)
  original=s.poll(op);self.assertEqual(len(original['result']['points']),4)
  call(r,'guard0.set',angle_rad=1.4);call(r,'cell.drain');call(r,'sample.unload');call(r,'sample.load',**dict(rt.SAMPLE_EXAMPLE,sample_id='replacement'))
  self.assertEqual(original,s.poll(op))
  call(r,'guard0.set',angle_rad=0.);call(r,'method.configure',**rt.METHOD_EXAMPLE)
  ans=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='new_sample');self.assertNotEqual(op,ans['operation_id']);r.step(.1);self.assertEqual(original,s.poll(op))
  returned=s.poll(op);returned['result']['points'].clear();self.assertEqual(original,s.poll(op))
 def test_29_cancelled_operation_result_is_immutable(self):
  r=setup();r.register_principal('a');s=r.session('a');ans=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r.step(5.)
  s.cancel(ans['operation_id'],'cancel');original=s.poll(ans['operation_id']);self.assertEqual(original['status'],'cancelled');r.step(20.);self.assertEqual(original,s.poll(ans['operation_id']))
 def test_30_failed_operation_retains_partial_result(self):
  r=setup();method=copy.deepcopy(r.method);method['point_timeout_s']=1.;call(r,'method.configure',**method);r.register_principal('a');s=r.session('a');ans=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='start')
  while r.phase=='running':r.step(.1)
  original=s.poll(ans['operation_id']);self.assertEqual(original['status'],'failed');self.assertEqual(r.failure,'POINT_TIMEOUT');r.step(20.);self.assertEqual(original,s.poll(ans['operation_id']))
 def test_31_metadata_covers_actual_observable_changes(self):
  metadata=rt.export_antonpaar_poremaster60_contract_metadata();original=rt.AntonpaarPoremaster60Runtime.invoke;observed=set()
  def audited_invoke(r,k,**arguments):
   before=r.read('evaluator');result=original(r,k,**arguments);after=r.read('evaluator');changed={state for state in before if before[state]!=after[state]};declared={effect['state_id'] for effect in metadata['capabilities'][k]['state_effects'] if effect['timing']=='immediate'}
   self.assertFalse(changed-declared,(k,changed-declared));observed.add(k);return result
  with mock.patch.object(rt.AntonpaarPoremaster60Runtime,'invoke',audited_invoke):
   r=setup();run(r);high(r);run(r);call(r,'cap.set',lift_m=.19);call(r,'guard0.set',angle_rad=1.4);call(r,'cell.transfer',destination='low0');r.step(5.);call(r,'cell.drain');call(r,'sample.unload')
   # At idle, calibration changes sensor observations without advancing the process.
   call(r,'pressure.calibrate',**dict(rt.PCAL_EXAMPLE,measured_pa=[0.,55*rt.PSI]));call(r,'volume.calibrate',**dict(rt.VCAL_EXAMPLE,measured_capacitance_f=[0.,10e-12]));call(r,'blank.calibrate',pressures_pa=[0.,rt.P_MAX],volumes_m3=[0.,0.]);call(r,'cell.configure',bulb_volume_m3=6.6e-6,stem_capacity_m3=2e-6)
   for stop in ['acquisition.stop','emergency.stop','power.set']:
    q=setup();call(q,'acquisition.start');q.step(1.);call(q,stop,**({'enabled':False} if stop=='power.set' else {}))
  self.assertTrue(set(rt.IMMEDIATE_EFFECTS)<=observed)
 # ---- 2026-10 library extension: >=50 distinct material groups, all runtime-usable ----
 SAMPLE_REFS=['tb2012_berea_sandstone','clay_opalinus_init','pc_paste_wc05_20c','bam_d_15','ceramic_membrane_150kda','pvdf_hfp_go20_membrane','globugraphite_reference','airlime_c0l100_28d','polymer_foam_pan440','sdc_1_washburn','mercia_mudstone_150146','bioglass_bg_p_0ti_850c']
 def _low(self,ref,mass=.0001):
  xs,_=rt.CURVES[(ref,'intrusion')];lo=max(xs[0],.2*rt.PSI);hi=min(xs[-1],50*rt.PSI);self.assertLess(lo,hi,ref)
  return [lo*(hi/lo)**(i/3) for i in range(3)]+[hi]
 def _load(self,ref,mass=.0001):
  r=rt.AntonpaarPoremaster60Runtime('ext_'+ref);p=rt.PLANT_EXAMPLE;call(r,'plant.configure',**p);call(r,'cell.configure',bulb_volume_m3=3.2e-6,stem_capacity_m3=.5e-6);call(r,'power.set',enabled=True)
  for i in range(3):xmax=[50*rt.PSI,1500*rt.PSI,rt.P_MAX][i];call(r,'pressure.calibrate',range_index=i,reference_pa=[0.,xmax],measured_pa=[0.,xmax])
  call(r,'volume.calibrate',reference_volume_m3=[0.,.2e-6],measured_capacitance_f=[p['capacitance_offset_f'],.2e-6/p['capacitance_volume_factor_m3_f']+p['capacitance_offset_f']]);call(r,'mercury.fill',mass_kg=.1)
  bulk=max(rt.CURVES[(ref,'intrusion')][1])*mass*1.5+1e-9;call(r,'guard0.set',angle_rad=1.4);call(r,'sample.load',sample_id='s_'+ref,reference_id=ref,true_dry_mass_kg=mass,true_bulk_volume_m3=bulk,dry=True,accept_cross_instrument_transfer=True);call(r,'guard0.set',angle_rad=0.)
  fill=r.bulb-bulk+r.capacity;grid=[0.]+[rt.P_MAX*i/1000 for i in range(1,1001)];call(r,'blank.calibrate',pressures_pa=grid,volumes_m3=[fill*(-math.expm1(-v/p['hg_bulk_modulus_pa']))+p['glass_compliance_m3_pa']*v for v in grid])
  m=copy.deepcopy(rt.METHOD_EXAMPLE);m.update(entered_mass_kg=mass,pressures_pa=self._low(ref));call(r,'method.configure',**m);return r
 def test_32_extension_counts_and_provenance(self):
  subs=rt.LIB['substances'];self.assertGreaterEqual(len(rt.MATERIAL_GROUPS),50);self.assertGreaterEqual(len(subs),114)
  ext=[v for v in subs.values() if v.get('extension')=='2026-10'];self.assertGreaterEqual(len(ext),50)
  for v in subs.values():
   self.assertIn(v['evidence'],['measured','measured_literature','derived']);self.assertTrue(v['licence']);self.assertTrue(v['source']['url']);self.assertTrue(v['source']['sha256']);self.assertIsNone(v['cas']);self.assertFalse(v.get('digitized'))
   self.assertNotIn('NC',v['licence'].replace('NON','')) 
   if v['evidence']=='derived':self.assertTrue(v['processing'])
   if v['pressure_axis']=='washburn_assumed_params':
    w=v['washburn_parameters'];k=(2 if w['geometry']=='radius' else 4)
    for q in v['value']:
     r=q['source_diameter_m']/(2 if w['geometry']=='radius' else 1);self.assertAlmostEqual(q['pressure_pa'],-k*w['gamma_n_m']*math.cos(math.radians(w['theta_deg']))/r,delta=1e-9*q['pressure_pa'])
  classes={v['material_class'] for v in subs.values()};self.assertGreaterEqual(len(classes),8)
  ot=json.loads((ROOT/'assets/expansion_batch03/t4_measure/antonpaar_poremaster60/output_templates.json').read_text());self.assertEqual(ot['distinct_material_group_count'],len(rt.MATERIAL_GROUPS));self.assertEqual(ot['runtime_usable_count'],len(subs))
 def test_33_every_reference_is_runtime_configurable(self):
  for ref in rt.LIB['substances']:
   xs,ys=rt.CURVES[(ref,'intrusion')];self.assertGreater(len(xs),3,ref);self.assertTrue(all(b>=a for a,b in zip(ys,ys[1:])),ref);self.assertTrue(all(b>a for a,b in zip(xs,xs[1:])),ref)
   with self.subTest(ref=ref):r=self._load(ref);self.assertIsNotNone(r.method,ref)
 def test_34_extension_low_high_cycle_across_classes(self):
  for ref in self.SAMPLE_REFS:
   r=self._load(ref);out=run(r);self.assertEqual(out['phase'],'succeeded',ref);xs,ys=rt.CURVES[(ref,'intrusion')]
   for p in out['points']:self.assertLess(abs(p['specific_volume_m3_kg']-rt.interp(xs,ys,p['pressure_pa'])),2e-6+.02*max(ys),ref)
   call(r,'guard0.set',angle_rad=1.4);call(r,'cap.set',lift_m=.19);call(r,'cell.transfer',destination='high');call(r,'guard0.set',angle_rad=0.);call(r,'cap.set',lift_m=0.)
   xe,_=rt.CURVES[(ref,'extrusion')];peak=max(xe) if xe and max(xe)<=rt.P_MAX else min(xs[-1],rt.P_MAX);lo=max(50*rt.PSI,xs[0]);ps=[lo*(peak/lo)**(i/4) for i in range(4)]+[peak]
   if xe and max(xe)<=rt.P_MAX:ps+=[x for x in [peak/10,peak/100] if x>max(xe[0],20*rt.PSI)]
   m=copy.deepcopy(rt.METHOD_EXAMPLE);m.update(stage='high',entered_mass_kg=r.sample['true_dry_mass_kg'],pressure_rate_pa_s=2e7,pressure_tolerance_pa=7000.,pressures_pa=ps);call(r,'method.configure',**m);out=run(r)
   self.assertEqual(out['phase'],'succeeded',ref);self.assertEqual(out['evidence'],rt.LIB['substances'][ref]['evidence']+'_reference_with_explicit_engineering_transfer')
   hp=[p for p in out['points'] if p['stage']=='high' and p['branch']=='intrusion'];self.assertTrue(hp)
   for p in hp:self.assertLess(abs(p['specific_volume_m3_kg']-rt.interp(xs,ys,p['pressure_pa'])),2e-6+.02*max(ys),ref)
   if xe and max(xe)<=rt.P_MAX and len(ps)>5:self.assertTrue(any(p['branch']=='extrusion' for p in out['points']),ref)
   if rt.LIB['substances'][ref]['pressure_axis']=='washburn_assumed_params':self.assertTrue(any('washburn_assumed_params' in w for w in out['warnings']),ref)
 def test_35_extension_values_recheck_raw(self):
  _need(OUT/'sources'/'vendor');sys.path.insert(0,str(OUT/'sources'/'vendor'));import xlrd
  d=rt.LIB['substances']['tb2012_berea_sandstone'];c=d['conditions'];w=xlrd.open_workbook(str(OUT/d['source']['file']),logfile=io.StringIO()).sheet_by_name('MICP')
  spv=c['micp_porosity_fraction']/(c['grain_density_kg_m3']*(1-c['helium_porosity_fraction']))
  for q in d['value'][::17]:row=w.row_values(q['source_row']-1);self.assertAlmostEqual(q['pressure_pa'],row[0]*rt.PSI,places=6);self.assertAlmostEqual(q['volume_m3_kg'],row[4]/100*spv,places=15)
  d=rt.LIB['substances']['bam_d_15'];raw=zipfile.ZipFile(OUT/d['source']['file']).read(d['source']['locator'].split(' / ')[0]).decode('latin-1').splitlines()
  for q in d['value'][::50]:
   cells=[x.strip().replace(',','.') for x in raw[q['source_row']-1].split(';')];j=0 if q['branch']=='intrusion' else 2;self.assertAlmostEqual(q['pressure_pa'],float(cells[j])*1e6,places=3);self.assertAlmostEqual(q['volume_m3_kg'],float(cells[j+1])*1e-6,places=12)
  rep=json.loads((OUT/'extension_source_validation.json').read_text());self.assertEqual(rep['status'],'passed');self.assertGreaterEqual(rep['records'],50)
 def test_36_template_reference_reports_material_metadata(self):
  r=rt.AntonpaarPoremaster60Runtime()
  for ref,cls,axis in [('globugraphite_reference','carbon','measured'),('sdc_2_washburn','ceramic','washburn_assumed_params'),('hemp_shiv','plant_biomass','measured')]:
   out=call(r,'template.reference',substance_id=ref);self.assertEqual(out['material_class'],cls);self.assertEqual(out['pressure_axis'],axis);self.assertTrue(out['licence']);self.assertEqual(out['material_group'],rt.LIB['substances'][ref]['material_group'])
if __name__=='__main__':unittest.main()
