"""CHEM142 finite-disk physical checks, real mapping/calibration events and protocols."""
import copy,importlib.util,json,math,unittest
from pathlib import Path
import numpy as np
from scripts.asset_runtime import signatone_quadpro2 as r
ROOT=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('quadpro_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');asset_tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(asset_tool)
OBSERVED=[]
class Checked(r.SignatoneQuadpro2Runtime):
    def invoke(self,k,**a):
        before=self.read();out=super().invoke(k,**a);after=self.read();changed={x for x in after if after[x]!=before[x]};assert not changed-set(r.CAPS[k]['effects']),(k,changed-set(r.CAPS[k]['effects']));assert not asset_tool.validate_value(out,r.CAPS[k]['output_schema']),(k,asset_tool.validate_value(out,r.CAPS[k]['output_schema']));OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(out)));return out

def make(plant=None,sample=None,method=None,wafer=None,probe=None,standard=False,map_args=None):
    h=Checked()
    for c,a in [('plant.configure',dict(r.PLANT_EXAMPLE,**(plant or {}))),('wafer.configure',dict(r.WAFER_EXAMPLE,**(wafer or {}))),('probe.configure',dict(r.PROBE_EXAMPLE,**(probe or {}))),('standard.load' if standard else 'sample.load',dict(r.STANDARD_EXAMPLE if standard else r.SAMPLE_EXAMPLE,**(sample or {}))),('power.set',{'enabled':True}),('method.configure',dict(r.METHOD_EXAMPLE,require_calibration=False,**(method or {}))),('map.configure',dict(r.MAP_EXAMPLE,**(map_args or {}))),('enclosure.set_open',{'opened':False})]:h.invoke(c,**a)
    h.step(3.);return h

def finish(h,kind='mapping',reference=100.):
    h.invoke('calibration.start',reference_sheet_resistance_ohm_sq=reference) if kind=='calibration' else h.invoke('acquisition.start');h.step(300.);assert h.phase!='running','test finish window too short';return h.invoke('acquisition.result')
def open_home(h):
    if h.phase=='running':h.invoke('acquisition.stop')
    h.invoke('probe.set_contact',contact=False);h.step(2.);h.invoke('stage.move',x_m=0.,y_m=0.);h.step(5.);h.invoke('enclosure.set_open',opened=True);h.step(3.)
def remove(h):open_home(h);h.invoke('sample.remove')
def sessions(h):
    for n,role in [('student','agent'),('teacher','evaluator'),('other','agent')]:h.register_principal(n,role)
    return h.session('student'),h.session('teacher'),h.session('other')
def call(s,c,a=None,rid='r1'):return s.invoke(instance_id=s._host.instance_id,capability_id=c,arguments=a or {},request_id=rid)
def snapshot(h):
    keys=['plant','wafer','probe','sample','sample_kind','map','accepted','excluded','method','axes','power','power_started_ns','calibration_gain','selected_current','phase','stage','operation_id','operation_kind','rows','auto_trials','records','time_ns','_clock_remainder_ns','revision','_event_ns','_site','_measure_phase','_partial']
    return json.dumps(dict(state=h.read(),ledger=h._ledger(),x=h.x.tolist(),**{k:getattr(h,k) for k in keys}),sort_keys=True,allow_nan=False)
def external_rows(Rs,diameter=.1,spacing=.00127,x=0.,y=0.,I=.0001):return [dict(configuration=c,current_a=sign*I,voltage_v=sign*I*Rs*r.disk_transfer_coefficient(diameter,spacing,x,y,c)+3e-5) for c in r.CONFIGURATIONS for sign in [1,-1]]

class QuadproTests(unittest.TestCase):
    def budget(self,h,tol=1e-8):
        for k,v in h.invoke('ledger.read').items():
            if 'error' in k:self.assertLess(abs(v),tol,(k,v))
    def reject(self,h,k,**a):
        before=snapshot(h)
        with self.assertRaises(r.ActionRejected):h.invoke(k,**a)
        self.assertEqual(snapshot(h),before)
    def test_01_xy_targets_move_in_host_time_only(self):
        h=make();h.invoke('stage.move',x_m=.02,y_m=-.01);self.assertEqual(h.axes['x']['position'],0.);h.step(.25);self.assertAlmostEqual(h.axes['x']['position'],.01,12);self.assertAlmostEqual(h.axes['y']['position'],-.01,12);h.step(.25);self.assertEqual(h.axes['x']['position'],.02)
    def test_02_probe_contact_blocks_xy_until_lifted(self):
        h=make();h.invoke('probe.set_contact',contact=True);h.step(1.);self.assertAlmostEqual(h.axes['z']['position'],.005);self.reject(h,'stage.move',x_m=.01,y_m=0.);h.step(1.);self.assertEqual(h.axes['z']['position'],0.);self.reject(h,'stage.move',x_m=.01,y_m=0.);h.invoke('probe.set_contact',contact=False);h.step(2.);h.invoke('stage.move',x_m=.01,y_m=0.)
    def test_03_hood_loading_interlocks_and_finite_motion(self):
        h=make();self.reject(h,'sample.remove');h.invoke('enclosure.set_open',opened=True);self.assertEqual(h.axes['hood']['position'],0.);h.step(1.);self.assertAlmostEqual(h.axes['hood']['position'],.5);self.reject(h,'acquisition.start');self.reject(h,'sample.remove');h.step(2.);h.invoke('sample.remove');self.budget(h)
    def test_04_reads_describe_and_zero_dt_are_pure(self):
        h=make();a,e,_=sessions(h);before=snapshot(h)
        for _ in range(2):a.read(instance_id=h.instance_id);e.describe(asset_type_id=r.ASSET_TYPE_ID);h.step(0.)
        self.assertEqual(snapshot(h),before)
    def test_05_real_resistivity_library_is_not_real_spatial_map(self):
        lib=r.library();self.assertGreaterEqual(len(lib),50)
        for row in lib.values():
            self.assertEqual(row['evidence'],'measured_literature');self.assertTrue(row['source']);self.assertGreaterEqual(len(row['curves']['resistivity']),2);self.assertTrue(all(p['value_ohm_m']>0 for p in row['curves']['resistivity']))
        self.assertIsNone(r.reference_rho(lib['starry_40927'],299));self.assertAlmostEqual(r.reference_rho(lib['starry_40927'],300)[0],4.9076758e-4,12)
    def test_06_center_green_matches_closed_form_and_historical_table(self):
        for u,tab in [(4,2.9289),(5,3.3625),(7.5,3.9273),(10,4.1716),(15,4.3646),(20,4.4364),(40,4.5076)]:
            actual=1/r.disk_transfer_coefficient(u*.001,.001,0,0);closed=math.pi/math.log(2*(u*u+3)/(u*u-3));self.assertAlmostEqual(actual,closed,12);self.assertLess(abs(actual-tab),5e-4)
        self.assertAlmostEqual(1/r.disk_transfer_coefficient(1e5,.001,0,0),math.pi/math.log(2),12)
    def test_07_insulating_circle_has_independent_zero_normal_flux(self):
        R=.05;points=r.probe_points(.03,.005,.00127);a,b=points[0],points[-1];phi=lambda z:-(r.disk_kernel(z,a,R)-r.disk_kernel(z,b,R))/(2*math.pi);delta=R*1e-5
        for angle in np.linspace(0,2*math.pi,49)[:-1]:
            unit=complex(math.cos(angle),math.sin(angle));derivative=(phi((R+delta)*unit)-phi((R-delta)*unit))/(2*delta);self.assertLess(abs(R*derivative),2e-9)
    def test_08_green_reciprocity_for_off_center_contacts(self):
        R=.05;p=r.probe_points(.031,.013,.00127)
        def transfer(a,b,c,d):return -(r.disk_kernel(p[c],p[a],R)-r.disk_kernel(p[c],p[b],R)-r.disk_kernel(p[d],p[a],R)+r.disk_kernel(p[d],p[b],R))/(2*math.pi)
        self.assertAlmostEqual(transfer(0,3,1,2),transfer(1,2,0,3),14);self.assertAlmostEqual(transfer(0,2,1,3),transfer(1,3,0,2),14)
    def test_09_geometry_rotational_and_scale_invariance(self):
        base=r.disk_transfer_coefficient(.1,.00127,.031,.008)
        for angle in [.3,1.2,2.7]:
            z=complex(.031,.008)*complex(math.cos(angle),math.sin(angle));self.assertAlmostEqual(r.disk_transfer_coefficient(.1,.00127,z.real,z.imag,orientation_rad=angle),base,13)
        self.assertAlmostEqual(r.disk_transfer_coefficient(.2,.00254,.062,.016),base,13)
    def test_10_external_near_edge_inverse_corrects_bias(self):
        h=Checked();x=.046;rows=external_rows(250.,x=x);o=h.invoke('analysis.sheet_resistance',**dict(r.ANALYSIS_EXAMPLE,x_m=x,observations=rows));self.assertAlmostEqual(o['sheet_resistance_ohm_sq'],250.,10);naive=(rows[0]['voltage_v']-rows[1]['voltage_v'])/(rows[0]['current_a']-rows[1]['current_a'])*math.pi/math.log(2);self.assertGreater(naive,250.*1.01);self.assertIsNone(h.sample)
    def test_11_probe_footprint_and_thin_layer_domain_rejected(self):
        with self.assertRaises(r.ActionRejected):r.disk_transfer_coefficient(.1,.00127,.049,0)
        h=make();remove(h);self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,conducting_thickness_m=.0005))
    def test_12_actual_calibration_waits_180_seconds_and_fits_observation(self):
        h=make(standard=True);h.invoke('calibration.start',reference_sheet_resistance_ohm_sq=100.);h.step(179.9);self.assertIsNone(h.calibration_gain);self.assertEqual(h.phase,'running');h.step(100.);self.assertEqual(h.phase,'succeeded');self.assertGreaterEqual(h.elapsed,180.);self.assertLess(abs(h.calibration_gain-1/1.03),2e-6);self.assertGreater(h._ledger()['source_input_j'],0);self.budget(h)
    def test_13_calibration_uses_supplied_standard_not_hidden_gain(self):
        h=make(standard=True);out=finish(h,'calibration',reference=110.);self.assertAlmostEqual(h.calibration_gain,110/out['rows'][0]['sheet_resistance_ohm_sq'],14);self.assertGreater(abs(h.calibration_gain-1/1.03),.09)
    def test_14_replacing_probe_invalidates_calibration(self):
        h=make(standard=True);finish(h,'calibration');open_home(h);h.invoke('probe.configure',**dict(r.PROBE_EXAMPLE,spacing_m=.001016));self.assertIsNone(h.calibration_gain);h.invoke('map.configure',**r.MAP_EXAMPLE);h.invoke('method.configure',**r.METHOD_EXAMPLE);h.invoke('enclosure.set_open',opened=False);h.step(3.);self.reject(h,'acquisition.start')
    def test_15_mapping_throughput_is_at_least_1p2s_per_point_plus_motion(self):
        h=make(method={'auto_current':False});out=finish(h);self.assertEqual(out['phase'],'succeeded');self.assertEqual(len(out['rows']),5);self.assertGreater(out['elapsed_s'],5*1.2);previous=0.
        for row in out['rows']:self.assertGreaterEqual(row['time_s']-previous,1.2);previous=row['time_s'];self.assertEqual(len(row['observations']),4)
        self.assertTrue(h._raised());self.budget(h)
    def test_16_autorange_only_first_site_and_fixed_current_afterward(self):
        h=make();out=finish(h);self.assertEqual(out['phase'],'succeeded');self.assertGreater(len(out['auto_trials']),1);self.assertLessEqual(len(out['auto_trials']),9);currents={abs(p['current_a']) for row in out['rows'] for p in row['observations']};self.assertEqual(len(currents),1);self.assertAlmostEqual(next(iter(currents)),out['selected_current_a'],12);self.assertTrue(all(x['time_s']<out['rows'][0]['time_s'] for x in out['auto_trials']))
    def test_17_autorange_changes_with_actual_observation_gain(self):
        a=make(plant={'voltage_gain':.1},map_args={'points':[dict(x_m=0.,y_m=0.)]});b=make(plant={'voltage_gain':1.},map_args={'points':[dict(x_m=0.,y_m=0.)]});finish(a);finish(b);self.assertGreater(a.selected_current,b.selected_current)
    def test_18_source_current_programming_uses_2450_resolution(self):
        h=make(method={'auto_current':False,'fixed_current_a':.000123456});h.invoke('acquisition.start');self.assertAlmostEqual(h.selected_current,.00012345,15)
    def test_19_compliance_failure_keeps_real_energy(self):
        h=make(method={'auto_current':False,'fixed_current_a':.001,'voltage_compliance_v':.002});out=finish(h);self.assertEqual(out['phase'],'failed');self.assertEqual(out['error_code'],'SOURCE_COMPLIANCE');self.assertIsNone(out['rows'][0]['sheet_resistance_ohm_sq']);self.assertGreater(h._ledger()['source_input_j'],0);self.budget(h)
    def test_20_2450_voltage_current_and_power_envelope(self):
        h=make();self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,current_max_a=1.,voltage_compliance_v=200.));h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,require_calibration=False,auto_current=False,current_max_a=.1,fixed_current_a=.1,voltage_compliance_v=200.,voltage_range_v=200.));h.invoke('acquisition.start');h.step(2.3);e=h._electrical();self.assertLessEqual(abs(e['current']),.1);self.assertLessEqual(abs(e['source_voltage']),200+1e-10);self.assertLessEqual(abs(e['current']*e['source_voltage']),20+1e-10);h.invoke('acquisition.stop');self.budget(h)
    def test_21_low_current_ranges_require_rear_triax(self):
        h=make(plant={'rear_triax_connected':False});self.reject(h,'acquisition.start');h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,require_calibration=False,current_min_a=1e-6));h.invoke('acquisition.start')
    def test_22_resistivity_only_with_independent_thickness_prior(self):
        h=Checked();a=h.invoke('analysis.sheet_resistance',**r.ANALYSIS_EXAMPLE);b=h.invoke('analysis.sheet_resistance',**dict(r.ANALYSIS_EXAMPLE,thickness_prior_m=2e-6));self.assertIsNone(a['resistivity_ohm_m']);self.assertEqual(a['sheet_resistance_ohm_sq'],b['sheet_resistance_ohm_sq']);self.assertAlmostEqual(b['resistivity_ohm_m'],a['sheet_resistance_ohm_sq']*2e-6,15)
    def test_23_edge_exclusion_reports_unmeasured_points(self):
        h=make();out=finish(h);self.assertEqual(len(out['excluded_points']),1);self.assertEqual(out['excluded_points'][0]['reason'],'EDGE_EXCLUSION');self.assertEqual(out['excluded_points'][0]['point_index'],5);self.assertEqual({p['point_index'] for p in out['rows']},set(range(5)));h.invoke('map.configure',**dict(r.MAP_EXAMPLE,points=[dict(x_m=.049,y_m=0.)]));self.reject(h,'acquisition.start')
    def test_24_14000_custom_points_are_supported_without_fake_completion(self):
        h=make();points=[dict(x_m=0.,y_m=0.) for _ in range(14000)];out=h.invoke('map.configure',**dict(r.MAP_EXAMPLE,points=points));self.assertEqual(out['accepted_points'],14000);self.reject(h,'map.configure',**dict(r.MAP_EXAMPLE,points=points+[points[0]]));h.invoke('acquisition.start');h.step(.01);self.assertEqual(h.rows,[]);self.assertEqual(h.phase,'running');h.invoke('acquisition.stop')
    def test_25_300mm_geometry_has_declared_mechanical_edge_guard(self):
        h=make(wafer={'diameter_m':.3},map_args={'edge_exclusion_m':.02});self.reject(h,'map.configure',**dict(r.MAP_EXAMPLE,edge_exclusion_m=.019));self.reject(h,'stage.move',x_m=.131,y_m=0.);remove(h);self.reject(h,'wafer.configure',diameter_m=.301,substrate_thickness_m=.0005);h.invoke('wafer.configure',diameter_m=.01,substrate_thickness_m=.0005)
    def test_26_reference_paper_temperature_and_explicit_assumptions(self):
        h=make();remove(h);self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,initial_temperature_k=299.));self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,accept_synthetic_map_and_bulk_reference=False));self.reject(h,'standard.load',**dict(r.STANDARD_EXAMPLE,accept_host_standard_not_certified=False))
    def test_27_mass_volume_sensible_heat_load_and_remove(self):
        h=make(sample={'initial_temperature_k':305.});expected=math.pi*.05**2*(.0005*2330+1e-6*6000);self.assertAlmostEqual(h._mass(),expected,14);self.assertGreater(h.loaded_sensible,30.);self.budget(h);remove(h);self.assertEqual(h._mass(),0.);self.assertAlmostEqual(h.mass_removed,expected,14);self.assertGreater(h.removed_sensible,0.);self.budget(h)
    def test_28_loaded_wafer_geometry_is_mass_locked(self):
        h=make();self.reject(h,'wafer.configure',diameter_m=.2,substrate_thickness_m=.0005);mass=h._mass();remove(h);h.invoke('wafer.configure',diameter_m=.2,substrate_thickness_m=.0005);h.invoke('sample.load',**r.SAMPLE_EXAMPLE);self.assertAlmostEqual(h._mass(),4*mass,12);self.budget(h)
    def test_29_cancel_retains_completed_points_and_dissipated_heat(self):
        h=make(method={'auto_current':False});h.invoke('acquisition.start')
        while len(h.rows)<2:h.step(.2)
        h.invoke('acquisition.stop');rows=copy.deepcopy(h.rows);energy=h.x[2:5].copy();positions=[h.axes[k]['position'] for k in ['x','y','z']];temp=h.x[0];h.step(10.);self.assertEqual(h.rows,rows);np.testing.assert_array_equal(h.x[2:5],energy);self.assertEqual([h.axes[k]['position'] for k in ['x','y','z']],positions);self.assertLess(h.x[0],temp);self.budget(h)
    def test_30_poweroff_stops_motion_without_teleport_and_blanks_display(self):
        h=make(method={'auto_current':False});finish(h);cached=h._last_sheet;h.invoke('acquisition.start');h.step(1.);before=[h.axes[k]['position'] for k in ['x','y','z']];h.invoke('power.set',enabled=False);h.step(2.);self.assertEqual([h.axes[k]['position'] for k in ['x','y','z']],before);self.assertEqual(h.read()['measurement.last_sheet_resistance_ohm_sq'],cached);self.assertIsNone(h.read()['display.sheet_resistance_ohm_sq']);self.assertIsNone(h.read()['measured.voltage_v']);self.budget(h)
    def test_31_sixty_hz_partition_matches_whole_step(self):
        opts=dict(method={'auto_current':False},map_args={'points':[dict(x_m=0.,y_m=0.)]});a=make(**opts);b=make(**opts)
        for h in (a,b):h.invoke('acquisition.start')
        a.step(6.)
        for _ in range(360):b.step(1/60)
        self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.rows,b.rows);np.testing.assert_allclose(a.x,b.x,rtol=1e-8,atol=1e-12);self.budget(a);self.budget(b)
    def test_32_optional_warmup_requires_full_3600_host_seconds(self):
        h=make(method={'require_warmup':True});self.reject(h,'acquisition.start');h.step(3596.999);self.assertFalse(h.read()['source.warmup_ready']);self.reject(h,'acquisition.start');h.step(.001);self.assertTrue(h.read()['source.warmup_ready']);h.invoke('acquisition.start');h.invoke('acquisition.stop');h.invoke('power.set',enabled=False);h.invoke('power.set',enabled=True);self.assertEqual(h._warmup(),0.);self.reject(h,'acquisition.start')
    def test_33_invalid_and_busy_requests_are_atomic(self):
        h=make()
        for dt in [True,-1.,float('nan'),float('inf')]:
            before=snapshot(h)
            with self.assertRaises(r.ActionRejected):h.step(dt)
            self.assertEqual(snapshot(h),before)
        self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,phase_duration_s=.299));self.reject(h,'map.configure',**dict(r.MAP_EXAMPLE,edge_exclusion_m=True));h.invoke('acquisition.start');self.reject(h,'probe.configure',**r.PROBE_EXAMPLE);self.reject(h,'map.configure',**r.MAP_EXAMPLE);self.reject(h,'enclosure.set_open',opened=True);self.reject(h,'method.configure',**r.METHOD_EXAMPLE)
    def test_34_dual_instances_remain_independent(self):
        a=make();b=make(sample={'reference_id':'starry_36130','gradient_x_fraction':-.1});before=snapshot(b);finish(a);self.assertEqual(snapshot(b),before);finish(b);self.assertNotEqual(a.rows,b.rows)
    def test_35_identity_and_truth_visibility(self):
        h=make();a,e,_=sessions(h);self.assertNotIn('truth.conducting_thickness_m',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.conducting_thickness_m',e.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',a.describe(instance_id=h.instance_id)['capabilities']);self.assertEqual(call(a,'sample.load',r.SAMPLE_EXAMPLE)['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(r.ActionRejected):h.session('unknown')
        with self.assertRaises(r.ActionRejected):a.read(instance_id=h.instance_id,state_ids=['truth.sample_k'])
    def test_36_calibration_async_owner_idempotence_and_cancel(self):
        h=make(standard=True);a,e,b=sessions(h);start=call(a,'calibration.start',{'reference_sheet_resistance_ohm_sq':100.});self.assertEqual(start,call(a,'calibration.start',{'reference_sheet_resistance_ohm_sq':100.}));self.assertEqual(call(b,'acquisition.stop')['error']['code'],'ACCESS_DENIED');h.step(15.);op=start['operation_id'];reply=a.cancel(op,'cancel');self.assertEqual(reply,a.cancel(op,'cancel'));self.assertEqual(a.poll(op)['status'],'cancelled');self.assertIsNone(h.calibration_gain);self.budget(h)
    def test_37_all_session_roles_redact_selected_material(self):
        h=make();a,e,_=sessions(h);op=call(a,'acquisition.start')['operation_id'];h.step(100.)
        for payload in [call(a,'acquisition.result',rid='a'),call(e,'acquisition.result',rid='e'),a.poll(op)]:
            text=json.dumps(payload);self.assertNotIn('starry_40927',text);self.assertNotIn('Cu1.99',text);self.assertNotIn('original_csv',text)
        self.assertEqual(call(a,'provenance.read',rid='p')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(e,'provenance.read',rid='p')['result']['reference_id'],'starry_40927')
    def test_38_completed_result_snapshot_survives_sample_and_calibration_change(self):
        h=make();old=finish(h);remove(h);h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,reference_id='starry_36130'));h.invoke('calibration.clear');self.assertEqual(h.invoke('acquisition.result'),old)
    def test_39_all_actual_returns_and_state_changes_match_contract(self):
        h=make();meta=h.describe()
        for key,c in meta['capabilities'].items():self.assertEqual(asset_tool.lint_schema(c['input_schema']),[],key);self.assertEqual(asset_tool.lint_schema(c['output_schema']),[],key);self.assertEqual(asset_tool.validate_value(c['example_arguments'],c['input_schema']),[],key)
        for key,args,result in OBSERVED:self.assertEqual(asset_tool.validate_value(result,meta['capabilities'][key]['output_schema']),[],key)
        for key,value in h.read().items():self.assertEqual(asset_tool.validate_value(value,meta['state_slots'][key]['value_schema']),[],key)
    def test_40_all_implemented_session_entries_are_executed(self):
        h=Checked();h.register_principal('eval','evaluator');s=h.session('eval');seen=set();plan=[('plant.configure',r.PLANT_EXAMPLE),('wafer.configure',r.WAFER_EXAMPLE),('probe.configure',r.PROBE_EXAMPLE),('standard.load',r.STANDARD_EXAMPLE),('power.set',{'enabled':True}),('enclosure.set_open',{'opened':False}),('sim.step',{'dt_s':3.}),('method.configure',r.METHOD_EXAMPLE),('calibration.start',{'reference_sheet_resistance_ohm_sq':100.}),('sim.step',{'dt_s':220.}),('enclosure.set_open',{'opened':True}),('sim.step',{'dt_s':3.}),('sample.remove',{}),('sample.load',r.SAMPLE_EXAMPLE),('stage.move',{'x_m':0.,'y_m':0.}),('enclosure.set_open',{'opened':False}),('sim.step',{'dt_s':3.}),('probe.set_contact',{'contact':True}),('sim.step',{'dt_s':2.}),('probe.set_contact',{'contact':False}),('sim.step',{'dt_s':2.}),('map.configure',r.MAP_EXAMPLE),('acquisition.start',{}),('sim.step',{'dt_s':12.}),('acquisition.stop',{}),('acquisition.result',{}),('analysis.sheet_resistance',r.ANALYSIS_EXAMPLE),('provenance.read',{}),('ledger.read',{}),('calibration.clear',{}),('power.set',{'enabled':False})]
        for i,(cap,args) in enumerate(plan):
            reply=call(s,cap,args,str(i));self.assertIn(reply['status'],('succeeded','accepted'),(cap,reply));self.assertEqual(asset_tool.validate_value(reply['result'],r.CAPS[cap]['output_schema']),[],cap);seen.add(cap)
        self.assertEqual(seen,set(r.CAPS)-set(r.UNSUPPORTED));self.budget(h)
    def test_41_unsupported_guards_are_not_fake_implementation(self):
        h=make()
        for c in r.UNSUPPORTED:self.reject(h,c)
    def test_42_system_ohm_range_is_not_mislabelled_as_sheet_resistance_range(self):
        h=Checked();rows=external_rows(1e10,I=1e-12);o=h.invoke('analysis.sheet_resistance',**dict(r.ANALYSIS_EXAMPLE,observations=rows));self.assertAlmostEqual(o['sheet_resistance_ohm_sq']/1e10,1.,12)
    def test_43_probe_spacings_and_force_are_explicit_si_configuration(self):
        h=make();open_home(h)
        for pitch in r.PROBE_SPACINGS:h.invoke('probe.configure',**dict(r.PROBE_EXAMPLE,spacing_m=pitch));self.assertEqual(h.probe['spacing_m'],pitch)
        self.assertAlmostEqual(r.PROBE_EXAMPLE['force_per_pin_n'],85*.00980665,12);self.reject(h,'probe.configure',**dict(r.PROBE_EXAMPLE,spacing_m=.002))
    def test_44_statistics_are_computed_only_from_actual_observed_rows(self):
        h=make(map_args={'thickness_prior_m':None});out=finish(h);values=np.array([x['sheet_resistance_ohm_sq'] for x in out['rows']]);self.assertEqual(out['statistics']['count'],len(values));self.assertAlmostEqual(out['statistics']['mean_ohm_sq'],float(values.mean()),12);self.assertAlmostEqual(out['statistics']['std_ohm_sq'],float(values.std()),12);self.assertTrue(all(x['resistivity_ohm_m'] is None for x in out['rows']))
    def test_45_fixed_world_probe_implies_negative_stage_coordinates(self):
        h=make(method={'auto_current':False},map_args={'points':[dict(x_m=.02,y_m=-.01)]});finish(h);self.assertEqual(h.axes['x']['position'],-.02);self.assertEqual(h.axes['y']['position'],.01);self.assertEqual(h.rows[0]['x_m'],.02);self.assertEqual(h.rows[0]['y_m'],-.01)
    def test_46_inverse_rejects_nonreversed_and_zero_current_data(self):
        h=Checked();a=copy.deepcopy(r.ANALYSIS_EXAMPLE)
        for row in a['observations']:row['current_a']=abs(row['current_a'])
        self.reject(h,'analysis.sheet_resistance',**a);a=copy.deepcopy(r.ANALYSIS_EXAMPLE)
        for row in a['observations']:row['current_a']=0.
        self.reject(h,'analysis.sheet_resistance',**a)

if __name__=='__main__':unittest.main(verbosity=2)
