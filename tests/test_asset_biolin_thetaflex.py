import unittest,sys,math,json,copy,base64,io
from pathlib import Path
from unittest import mock
import numpy as np,cv2
from PIL import Image
O=Path(__file__).resolve().parents[1]/'outputs/expansion_batch03/t4_measure/biolin_thetaflex';sys.path.insert(0,str(O));import example_setup as ex
rt=ex.rt
class TestThetaFlex(unittest.TestCase):
 def test_library_real_counts(self):
  l=rt.LIB;legacy=json.loads((O/'legacy_reference_values_hashes.json').read_text())['reference_values_sha256'];self.assertEqual(list(l['substances'])[:168],list(legacy));self.assertEqual(len(l['substances']),341);self.assertEqual(l['coverage']['legacy_response_templates'],168);self.assertEqual(len(l['experimental_images']),127);self.assertEqual(len(l['excluded_rows']),8);self.assertTrue(all(v['source'] and v['evidence'] for v in l['substances'].values()))
  c=l['coverage'];self.assertGreaterEqual(c['runtime_usable_distinct_pure_liquids'],50);self.assertGreaterEqual(c['permissive_licence_runtime_usable_distinct_pure_liquids'],50);self.assertTrue(c['distinct_chemical_species_ge50']);self.assertEqual(c['digitized_records'],0)
 def test_new_records_provenance_and_identity(self):
  new={k:v for k,v in rt.LIB['substances'].items() if v['kind']=='pure_liquid_pair' and not k.startswith('ptfe_pure_row')};self.assertEqual(len(new),173)
  for k,v in new.items():
   s=v['source'];self.assertEqual(v['evidence'],'measured_literature');self.assertFalse(v['digitized']);self.assertTrue(s['url'] and s['accessed'] and s['locator'] and s['sha256'] and s['licence'],k);self.assertIn(v['liquid_id'],rt.LIQUIDS)
   self.assertAlmostEqual(math.degrees(v['values']['contact_angle_rad']),float(v['raw_source']['theta_deg']),places=9)
  self.assertTrue(all('non_permissive' in v['source']['licence_flag'] for k,v in new.items() if k.startswith('il_pereira2015_')))
  counted=[L for L in rt.LIQUIDS.values() if L['counted_as_distinct_pure_species']];self.assertGreaterEqual(sum(bool(L['cas']) for L in counted),60)
  self.assertFalse(rt.LIQUIDS['tricresyl_phosphate']['counted_as_distinct_pure_species']);self.assertFalse(rt.LIQUIDS['cis_decalin']['counted_as_distinct_pure_species'])
  v=json.loads((O/'thin_library_validation.json').read_text());self.assertEqual(v['status'],'passed')
 def _reference_run(self,rid,use_props,volume=2e-10,instance='ref'):
  h=rt.BiolinThetaFlexRuntime(instance);h.invoke('plant.configure',**rt.PLANT_EXAMPLE);h.invoke('sample.load',reference_id=rid,accept_unknown_conditions=True,use_reference_liquid_properties=use_props);h.invoke('stock.load',reference_id=rid,volume_m3=2e-7)
  h.invoke('power.set',enabled=True);h.invoke('camera.calibrate',known_length_m=.001,observed_pixels=.001/(h.method['field_width_m']/rt.W));h.invoke('stage.move',x_m=.04,y_m=.02,z_m=.025);ex.finish(h);ex.put_drop(h,volume);h.invoke('acquisition.start');return h,ex.finish(h)
 def test_published_pairs_across_classes_measured_from_pixels(self):
  cases=[('il_pereira2015_c4c1im_ntf2_ptfe',False),('il_pereira2015_c4c1im_pf6_glass',False),('il_blanco2016_p66614_ntf2_steel',True),('il_blanco2019_n8881_c18_wc',False),('probe_blanco2019_water_steel',True),('hsp2024_formamide_pmma',True),('hsp2024_diiodomethane_cnpmma',False),('hts_cho2004_aniline',False),('hts_cho2004_benzene',True),('ptfe_pure_row40',False)]
  for rid,props in cases:
   with self.subTest(rid=rid):
    h,r=self._reference_run(rid,props);ref=rt.LIB['substances'][rid]['values']['contact_angle_rad'];row=r['rows'][-1]
    self.assertEqual(r['phase'],'succeeded');self.assertLess(abs(row['mean_rad']-ref),math.radians(2.));self.assertLess(abs(row['observed_volume_m3']/h.volume-1),.03);self.assertEqual(json.loads(r['template_source'])['source_id'],rt.LIB['substances'][rid]['source']['source_id'])
 def test_reference_liquid_properties_drive_profile_and_ledger(self):
  rid='il_blanco2016_p66614_ntf2_steel';L=rt.LIQUIDS['il_p66614_ntf2'];self.assertAlmostEqual(L['density']['value_kg_m3'],1071.1);self.assertAlmostEqual(L['surface_tension']['value_n_m'],.029327)
  a,_=self._reference_run(rid,True,volume=1e-9,instance='a');b,_=self._reference_run(rid,False,volume=1e-9,instance='b');self.assertAlmostEqual(a._rho_gamma()[0],1071.1);self.assertAlmostEqual(a._rho_gamma()[1],.029327);self.assertEqual(b._rho_gamma(),(rt.PLANT_EXAMPLE['density_kg_m3'],rt.PLANT_EXAMPLE['surface_tension_n_m']))
  za=rt.profile(a.volume,a.theta,*a._rho_gamma())[1][0];zb=rt.profile(b.volume,b.theta,*b._rho_gamma())[1][0];self.assertLess(za,zb)
  m0=a._ledger()['mass_error_kg'];a.loaded+=1e-12;self.assertLess(abs(a._ledger()['mass_error_kg']-m0-1e-12*1071.1),1e-17)
  h=rt.BiolinThetaFlexRuntime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'LIQUID_PROPERTIES_UNAVAILABLE'):h.invoke('sample.load',reference_id='il_pereira2015_c4c1im_ac_ptfe',accept_unknown_conditions=True,use_reference_liquid_properties=True)
  self.assertEqual(before,h.__dict__);h.invoke('sample.load',reference_id='il_pereira2015_c4c1im_ac_ptfe',accept_unknown_conditions=False);self.assertIsNone(h.liquid)
  with self.assertRaisesRegex(rt.ActionRejected,'UNKNOWN_CONDITIONS'):rt.BiolinThetaFlexRuntime().invoke('sample.load',reference_id='hts_cho2004_water',accept_unknown_conditions=False)
 def test_reference_owrk_reproduces_published_surface_energy(self):
  h=rt.BiolinThetaFlexRuntime();s=h.session()
  for tag,ref in rt.LIB['solid_surface_energy_references'].items():
   obs=[dict(angle_rad=rt.LIB['substances'][f'probe_blanco2019_{k}_{tag}']['values']['contact_angle_rad'],liquid_id=k) for k in ['water','diiodomethane']]
   r=s.invoke(instance_id=h.instance_id,capability_id='analysis.surface_energy_reference',arguments={'observations':obs},request_id='sfe'+tag);self.assertEqual(r['status'],'succeeded')
   self.assertLess(abs(r['result']['total_j_m2']-ref['total_j_m2']),2e-4);self.assertLess(abs(r['result']['dispersive_j_m2']-ref['dispersive_j_m2']),2e-4)
  r=s.invoke(instance_id=h.instance_id,capability_id='analysis.surface_energy_reference',arguments={'observations':[dict(angle_rad=1.,liquid_id='water'),dict(angle_rad=1.,liquid_id='il_c4c1im_ac')]},request_id='nocomp');self.assertEqual(r['error']['code'],'LIQUID_COMPONENTS_UNAVAILABLE')
 def test_reference_queries_hide_angles(self):
  h=rt.BiolinThetaFlexRuntime();s=h.session();state=h.read();revision=h.revision
  r=s.invoke(instance_id=h.instance_id,capability_id='reference.liquid',arguments={'liquid_id':'formamide'},request_id='liq')['result'];self.assertEqual(r['cas'],'75-12-7');self.assertAlmostEqual(r['surface_tension_n_m'],.05835);self.assertFalse(any('angle' in k for k in r))
  self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='reference.list',arguments={'liquid_id':'water','substrate':None},request_id='lst')['error']['code'],'ACCESS_DENIED')
  ids=h.session('evaluator').invoke(instance_id=h.instance_id,capability_id='reference.list',arguments={'liquid_id':'water','substrate':None},request_id='lst')['result']['reference_ids'];self.assertGreaterEqual(len(ids),12);self.assertTrue(all(rt.LIB['substances'][i]['liquid_id']=='water' for i in ids))
  self.assertEqual(state,h.read());self.assertEqual(revision,h.revision)
 def test_replicate_units(self):
  a=rt.LIB['substances']['al_ctab_a_row7'];self.assertAlmostEqual(math.degrees(a['values']['contact_angle_rad']),40.18);self.assertEqual(a['conditions']['concentration_mol_m3'],11.2);self.assertEqual(a['values']['advancing_rad'],None)
 def test_young_laplace_zero_gravity(self):
  v=2e-10;t=1.2;x,z,r=rt.profile(v,t,0.,.0728);expected=rt.cap_radius(v,t);self.assertAlmostEqual(r/expected,1,places=5);self.assertAlmostEqual(z[0]/(expected*(1-math.cos(t))),1,places=5)
 def test_young_laplace_volume(self):
  v=3e-9;x,z,r=rt.profile(v,1.8,998.2,.04);measured=-math.pi*np.trapz(x*x,z);self.assertLess(abs(measured/v-1),3e-5)
 def test_gravity_flattens(self):
  v=3e-9;t=1.3;x,z,r=rt.profile(v,t,0.,.04);xx,zz,rr=rt.profile(v,t,998.2,.04);self.assertLess(zz[0],z[0]);self.assertGreater(xx[-1],x[-1])
 def test_independent_circle_pixels(self):
  for deg in [30,60,90,110,140]:
   theta=math.radians(deg);a=np.full((1264,1984),230,np.uint8);b=1000;R=500;cy=round(b+R*math.cos(theta));cv2.circle(a,(992,cy),R,20,-1);buf=io.BytesIO();Image.fromarray(a).save(buf,format='PNG');q=rt.image_fit(base64.b64encode(buf.getvalue()).decode(),b,2e-6,'circle');expected=math.acos((cy-b)/R);self.assertLess(abs(q['mean_rad']-expected),math.radians(.25))
 def test_static_from_pixels(self):
  h,r=ex.static_example();self.assertEqual(len(r['rows']),5);self.assertLess(abs(r['rows'][-1]['mean_rad']-h.theta),math.radians(1.5));self.assertNotEqual(r['rows'][-1]['mean_rad'],h.theta);self.assertLess(abs(r['rows'][-1]['observed_volume_m3']/h.volume-1),.02)
 def test_pixel_scale_independent(self):
  h,r=ex.static_example();c=ex.call(h,'image.capture');a=rt.image_fit(c['image_png_base64'],c['baseline_px'],c['pixel_pitch_m'],'circle');b=rt.image_fit(c['image_png_base64'],c['baseline_px'],c['pixel_pitch_m']*2,'circle');self.assertAlmostEqual(a['mean_rad'],b['mean_rad']);self.assertAlmostEqual(b['volume_m3']/a['volume_m3'],8.)
 def test_fov_invalidates_calibration(self):
  h=ex.setup();m=copy.deepcopy(h.method);m['field_width_m']=.005;h.invoke('method.configure',**m);self.assertIsNone(h.pitch)
 def test_fov_crop_rejected(self):
  with self.assertRaisesRegex(rt.ActionRejected,'FIELD_OF_VIEW'):rt.render_drop(1e-8,1.5,1000.,.072,.00144,.006,.8)
 def test_invalid_image(self):
  with self.assertRaisesRegex(rt.ActionRejected,'INVALID_IMAGE'):rt.image_fit('notpng',1000,1e-6)
 def test_dynamic_pin_and_hysteresis(self):
  h,a,b,d=ex.dynamic_example();self.assertGreater(d['hysteresis_rad'],.8);self.assertLess(abs(d['advancing_rad']-h.plant['advancing_rad']),.03);self.assertLess(abs(d['receding_rad']-h.plant['receding_rad']),.06);self.assertAlmostEqual(h._ledger()['volume_error_m3'],0,places=18)
 def test_dynamic_bond_limit(self):
  h=ex.setup();m=copy.deepcopy(h.method);m['mode']='dynamic';h.invoke('method.configure',**m);h.invoke('dispenser.move',position_m=.024);ex.finish(h)
  with self.assertRaisesRegex(rt.ActionRejected,'DYNAMIC_BOND_LIMIT'):h.invoke('liquid.transfer',volume_m3=1e-8)
 def test_no_automatic_hysteresis_from_static(self):
  p=copy.deepcopy(rt.PLANT_EXAMPLE);p.update(advancing_rad=None,receding_rad=None,accept_dynamic_proxy=False);h=ex.setup(plant=p);m=copy.deepcopy(h.method);m['mode']='dynamic'
  with self.assertRaisesRegex(rt.ActionRejected,'DYNAMIC_INPUTS_REQUIRED'):h.invoke('method.configure',**m)
 def test_finite_stock(self):
  h=ex.setup();h.invoke('dispenser.move',position_m=.02);ex.finish(h);h.stock=5e-11;h.loaded=h.stock
  before=h.read()
  with self.assertRaisesRegex(rt.ActionRejected,'FINITE_INVENTORY'):h.invoke('liquid.transfer',volume_m3=1e-10)
  self.assertEqual(before,h.read())
 def test_partial_transfer_cancel_conserves(self):
  h=ex.setup();h.invoke('dispenser.move',position_m=.02);ex.finish(h);h.invoke('liquid.transfer',volume_m3=2e-10);h.step(.31);self.assertGreater(h.volume,0);v=h.volume;h.invoke('acquisition.stop');h.step(2.);self.assertEqual(v,h.volume);self.assertAlmostEqual(h._ledger()['mass_error_kg'],0,places=16)
 def test_clean_and_unload_conserves(self):
  h,r=ex.static_example();h.invoke('drop.clean');h.invoke('stock.unload');h.invoke('sample.remove');l=h._ledger();self.assertAlmostEqual(l['waste_m3'],l['loaded_m3']);self.assertAlmostEqual(l['volume_error_m3'],0,places=18)
 def test_motion_rate_and_cancel(self):
  h=rt.BiolinThetaFlexRuntime();h.invoke('plant.configure',**rt.PLANT_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('stage.move',x_m=.04,y_m=.02,z_m=.025);h.step(1.);self.assertAlmostEqual(h.q[0],.004);h.invoke('acquisition.stop');v=h.q[:];h.step(1.);self.assertEqual(v,h.q)
 def test_partition_independence(self):
  a=ex.setup();b=ex.setup();a.invoke('stage.move',x_m=.07,y_m=.03,z_m=.04);b.invoke('stage.move',x_m=.07,y_m=.03,z_m=.04);a.step(.37)
  for _ in range(37):b.step(.01)
  self.assertEqual(a.read(),b.read())
 def test_schema_atomic_bool_nan(self):
  h=ex.setup();before=h.read()
  for a in [{'x_m':True,'y_m':0.,'z_m':0.},{'x_m':float('nan'),'y_m':0.,'z_m':0.},{'x_m':0.,'y_m':0.,'z_m':0.,'extra':0}]:
   with self.assertRaises(rt.ActionRejected):h.invoke('stage.move',**a)
   self.assertEqual(before,h.read())
 def test_power_service_interlock(self):
  h=ex.setup()
  with self.assertRaisesRegex(rt.ActionRejected,'POWER_INTERLOCK'):h.invoke('service.set',distance_m=.09)
  h.invoke('power.set',enabled=False);h.invoke('service.set',distance_m=.09)
  with self.assertRaisesRegex(rt.ActionRejected,'NOT_READY'):h.invoke('power.set',enabled=True)
 def test_needle_collision_interlock(self):
  h=ex.setup()
  with self.assertRaisesRegex(rt.ActionRejected,'NEEDLE_COLLISION'):h.invoke('dispenser.move',position_m=.035)
  h.invoke('dispenser.move',position_m=.020);ex.finish(h)
  with self.assertRaisesRegex(rt.ActionRejected,'NEEDLE_INTERLOCK'):h.invoke('stage.move',x_m=.0,y_m=.0,z_m=.0)
 def test_unknown_conditions(self):
  h=rt.BiolinThetaFlexRuntime()
  with self.assertRaisesRegex(rt.ActionRejected,'UNKNOWN_CONDITIONS'):h.invoke('sample.load',reference_id=rt.SAMPLE_EXAMPLE['reference_id'],accept_unknown_conditions=False)
 def test_complete_wetting_not_cap(self):
  sid=next(k for k,v in rt.LIB['substances'].items() if v['values']['contact_angle_rad']==0)
  with self.assertRaisesRegex(rt.ActionRejected,'COMPLETE_WETTING_UNSUPPORTED'):rt.BiolinThetaFlexRuntime().invoke('sample.load',reference_id=sid,accept_unknown_conditions=True)
 def test_owrk_independent_observations(self):
  probes=[(.0218,.051),(.0508,0),(.029,.019)];gd=.021;gp=.008;obs=[dict(angle_rad=math.acos(2*(math.sqrt(gd*a)+math.sqrt(gp*b))/(a+b)-1),liquid_dispersive_j_m2=a,liquid_polar_j_m2=b) for a,b in probes];o=rt.surface_energy(obs);self.assertAlmostEqual(o['dispersive_j_m2'],gd);self.assertAlmostEqual(o['polar_j_m2'],gp)
 def test_degenerate_owrk(self):
  with self.assertRaisesRegex(rt.ActionRejected,'DEGENERATE_PROBES'):rt.surface_energy([dict(angle_rad=1.,liquid_dispersive_j_m2=.03,liquid_polar_j_m2=0.)]*2)
 def test_session_truth_and_ownership(self):
  h=ex.setup();a=h.session('agent');h.register_principal('other');b=h.session('other');d=a.describe(instance_id=h.instance_id);self.assertNotIn('truth.drop_m3',d['state_slots']);self.assertNotIn('plant.configure',d['capabilities']);r=a.invoke(instance_id=h.instance_id,capability_id='stage.move',arguments=dict(x_m=.06,y_m=.02,z_m=.025),request_id='1');op=r['operation_id'];b.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='2')
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):b.poll(op)
  self.assertEqual(b.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id='3')['error']['code'],'ACCESS_DENIED');self.assertEqual(a.cancel(op,'4')['status'],'succeeded')
 def test_idempotence_conflict(self):
  h=ex.setup();a=h.session('agent');kw=dict(instance_id=h.instance_id,capability_id='focus.set',arguments={'position_m':.006},request_id='x');one=a.invoke(**kw);self.assertEqual(one,a.invoke(**kw));kw['arguments']={'position_m':.01};self.assertEqual(a.invoke(**kw)['error']['code'],'REQUEST_CONFLICT')
 def test_pure_read_and_time_stamp(self):
  h,r=ex.static_example();state=copy.deepcopy(h.__dict__);h.read();h.invoke('acquisition.result');h.invoke('image.capture');self.assertEqual(state,h.__dict__);stamp=h.last_fit_time;h.step(1.);a=h.session('agent').read(instance_id=h.instance_id,state_ids=['angle.indicated_rad']);self.assertEqual(a['observations']['angle.indicated_rad']['sample_time_s'],stamp)
 def test_unsupported_explicit(self):
  h=ex.setup()
  for k in rt.UNSUPPORTED:
   with self.assertRaisesRegex(rt.ActionRejected,'UNSUPPORTED'):h.invoke(k)
 def test_tiny_capture_has_finite_terminal_state(self):
  for volume,expected in [(1e-12,'succeeded'),(1e-14,'failed')]:
   h=ex.setup();ex.put_drop(h,volume);h.invoke('acquisition.start');h.step(1.2);self.assertEqual(h.phase,expected)
   if expected=='succeeded':self.assertEqual(len(h.rows),h.method['frames'])
   else:self.assertEqual(h.error,'BASELINE_OR_EDGE')
 def test_terminal_result_survives_cleanup_and_reconfiguration(self):
  h=ex.setup();ex.put_drop(h);s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='capture')['operation_id'];h.step(1.2);snapshot=h.invoke('acquisition.result');self.assertEqual(snapshot['phase'],'succeeded');frozen=s.poll(op)
  h.invoke('drop.clean');h.invoke('stock.unload');h.invoke('sample.remove');h.invoke('method.configure',**dict(h.method,field_width_m=.005));self.assertEqual(snapshot,h.invoke('acquisition.result'));self.assertEqual(frozen,s.poll(op))
  snapshot['rows'][0]['mean_rad']=-1;self.assertNotEqual(snapshot,h.invoke('acquisition.result'));h.invoke('stage.move',x_m=0.,y_m=0.,z_m=0.);h.step(11.);self.assertEqual(frozen,s.poll(op))
 def test_exact_finite_inventory_rejects_atomically(self):
  h=ex.setup();h.invoke('stock.unload');h.invoke('stock.load',reference_id=h.sid,volume_m3=1e-10);ex.put_drop(h,9e-11);h.invoke('method.configure',**dict(h.method,mode='dynamic',flow_m3_s=1e-8));h.invoke('dispenser.move',position_m=.024);ex.finish(h)
  for volume in [h.stock+5e-19,-(h.volume+5e-19)]:
   before=copy.deepcopy(h.__dict__)
   with self.assertRaisesRegex(rt.ActionRejected,'FINITE_INVENTORY'):h.invoke('liquid.transfer',volume_m3=volume)
   self.assertEqual(before,h.__dict__)
  self.assertGreaterEqual(h.stock,0.);self.assertLessEqual(h.read()['plunger.position_m'],.04)
 def test_stock_exhaustion_is_terminal_and_conserves(self):
  h=ex.setup();h.invoke('stock.unload');h.invoke('stock.load',reference_id=h.sid,volume_m3=1e-10);h.invoke('dispenser.move',position_m=.02);ex.finish(h);s=h.session();op=s.invoke(instance_id=h.instance_id,capability_id='liquid.transfer',arguments={'volume_m3':9e-11},request_id='transfer')['operation_id'];h.step(.1)
  # Explicit host fault injection; lost stock enters waste, preserving loaded material.
  lost=8e-11;h.stock-=lost;h.waste+=lost;h.step(2.);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'INVENTORY_EXHAUSTED');self.assertEqual(h.stock,0.);self.assertAlmostEqual(h.volume,2e-11,delta=1e-24);self.assertLessEqual(h.read()['plunger.position_m'],.04)
  self.assertAlmostEqual(h._ledger()['volume_error_m3'],0.,delta=1e-22);self.assertAlmostEqual(h._ledger()['mass_error_kg'],0.,delta=1e-19);record=s.poll(op);volume=h.volume;h.step(3.);self.assertEqual(record,s.poll(op));self.assertEqual(volume,h.volume)
 def test_stage_needle_clearance_envelope(self):
  h=ex.setup();before=copy.deepcopy(h.__dict__)
  for cap,args,error in [('stage.move',dict(x_m=.04,y_m=.02,z_m=.0491),'INVALID_ARGUMENT'),('dispenser.move',dict(position_m=.02405),'NEEDLE_COLLISION')]:
   with self.assertRaisesRegex(rt.ActionRejected,error):h.invoke(cap,**args)
   self.assertEqual(before,h.__dict__)
  h.invoke('dispenser.move',position_m=.007);ex.finish(h);before=copy.deepcopy(h.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'NEEDLE_COLLISION'):h.invoke('stage.move',x_m=.04,y_m=.02,z_m=.043)
  self.assertEqual(before,h.__dict__);h.invoke('stage.move',x_m=.04,y_m=.02,z_m=.042)
  while h.phase=='running':h.step(.01);self.assertLessEqual(h.q[2]+h.dispenser,rt.STAGE_Z_MAX_M+1e-12)
  self.assertEqual(h.q[2],.042);h.invoke('dispenser.move',position_m=0.);ex.finish(h);h.invoke('stage.move',x_m=.04,y_m=.02,z_m=.049);ex.finish(h);self.assertEqual(h.q[2],.049)
  metadata=rt.export_biolin_thetaflex_contract_metadata();self.assertEqual(metadata['capabilities']['stage.move']['input_schema']['properties']['z_m']['maximum'],.049)
  for slot in ['stage.z_m','stage.target_z_m']:self.assertEqual(metadata['state_slots'][slot]['value_schema']['maximum'],.049)
 def test_instances_and_evaluator_transfer_boundaries(self):
  a=ex.setup('a');b=ex.setup('b');before=copy.deepcopy(b.__dict__);ex.put_drop(a);self.assertEqual(before,b.__dict__);s=a.session();state=copy.deepcopy(a.__dict__);s.describe(instance_id=a.instance_id);s.read(instance_id=a.instance_id);self.assertEqual(state,a.__dict__)
  for cap,args in [('stock.unload',{}),('drop.clean',{}),('sample.remove',{}),('ledger.read',{}),('sim.step',{'dt_s':1.})]:
   r=s.invoke(instance_id=a.instance_id,capability_id=cap,arguments=args,request_id=cap);self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertEqual(state,a.__dict__)
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):s.read(instance_id=a.instance_id,state_ids=['truth.drop_m3'])
 def test_metadata_covers_actual_state_changes(self):
  metadata=rt.export_biolin_thetaflex_contract_metadata();original=rt.BiolinThetaFlexRuntime.invoke;original_step=rt.BiolinThetaFlexRuntime.step;observed=set();stepped=set()
  def invoke(h,cap,**arguments):
   before=h.read();result=original(h,cap,**arguments);after=h.read();changed={key for key in before if before[key]!=after[key]};declared={effect['state_id'] for effect in metadata['capabilities'][cap]['state_effects'] if effect['timing']=='immediate'};self.assertFalse(changed-declared,(cap,changed-declared));observed.add(cap);return result
  def step(h,dt_s):
   cap={'move':'stage.move','dispenser':'dispenser.move','advance':'liquid.transfer','recede':'liquid.transfer','capture':'acquisition.start'}.get(h.stage) if h.phase=='running' else None;before=h.read();result=original_step(h,dt_s);after=h.read();changed={key for key in before if before[key]!=after[key]};declared={'clock.time_s'}|set(rt.STEP_EFFECTS.get(cap,[]));self.assertFalse(changed-declared,(cap,changed-declared));stepped.add(cap);return result
  with mock.patch.object(rt.BiolinThetaFlexRuntime,'invoke',invoke),mock.patch.object(rt.BiolinThetaFlexRuntime,'step',step):
   h,r=ex.static_example();h.invoke('power.set',enabled=False);h.invoke('power.set',enabled=True);h.invoke('method.configure',**dict(h.method,field_width_m=.005));h.invoke('drop.clean');h.invoke('stock.unload');h.invoke('sample.remove');h.invoke('stage.move',x_m=0.,y_m=0.,z_m=0.);h.invoke('sim.step',dt_s=.1);h.invoke('acquisition.stop');h.invoke('power.set',enabled=False);h.invoke('service.set',distance_m=.09);d,a,b,c=ex.dynamic_example();d.invoke('drop.clean')
  self.assertTrue(set(rt.IMMEDIATE_EFFECTS)<=observed);self.assertTrue(set(rt.STEP_EFFECTS)<=stepped)
if __name__=='__main__':unittest.main()
