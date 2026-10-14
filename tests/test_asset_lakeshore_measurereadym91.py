"""CHEM141 conventional Hall/vdP inverse, external field, real warmup and energy."""
import copy,importlib.util,json,math,unittest
from pathlib import Path
import numpy as np
from scripts.asset_runtime import lakeshore_measurereadym91 as r
ROOT=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('m91_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');asset_tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(asset_tool)
OBSERVED=[]
class Checked(r.LakeShoreMeasurereadyM91Runtime):
    def invoke(self,k,**a):
        before=self.read();out=super().invoke(k,**a);after=self.read();changed={x for x in after if after[x]!=before[x]}
        assert not changed-set(r.CAPS[k]['effects']),(k,changed-set(r.CAPS[k]['effects']))
        assert not asset_tool.validate_value(out,r.CAPS[k]['output_schema']),(k,asset_tool.validate_value(out,r.CAPS[k]['output_schema']))
        OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(out)));return out

def make(plant=None,sample=None,method=None,geometry=None,thermal=None,field=None,warm=True):
    h=Checked()
    for c,a in [('plant.configure',dict(r.PLANT_EXAMPLE,**(plant or {}))),('geometry.configure',dict(r.GEOMETRY_EXAMPLE,**(geometry or {}))),('thermal.configure',dict(r.THERMAL_EXAMPLE,**(thermal or {}))),('field.configure',dict(r.FIELD_EXAMPLE,**(field or {}))),('sample.load',dict(r.SAMPLE_EXAMPLE,**(sample or {}))),('power.set',{'enabled':True}),('sample.set_clamp',{'closed':True}),('thermal.set_temperature',{'temperature_k':r.PLANT_EXAMPLE['ambient_k']}),('field.set_target',{'field_t':.5}),('method.configure',dict(r.METHOD_EXAMPLE,**(method or {})))]:h.invoke(c,**a)
    if warm:h.step(1800.)
    return h

def finish(h):
    h.invoke('acquisition.start');h.step(h.expected_duration);return h.invoke('acquisition.result')
def paired(h):
    rows=[];operations=[]
    for B in [.5,-.5]:
        h.invoke('field.set_target',field_t=B);h.step(3.);out=finish(h);assert out['phase']=='succeeded',out;rows+=out['rows'];operations.append(out)
    a=dict(r.ANALYSIS_EXAMPLE,observations=[{k:x[k] for k in r.ANALYSIS_ROW['properties']} for x in rows]);return h.invoke('analysis.hall_vdp',**a),operations

def remove(h):
    if h.phase=='running':h.invoke('acquisition.stop')
    h.invoke('sample.set_clamp',closed=False);h.step(.012/h.plant['clamp_speed_m_s']);h.invoke('sample.remove')
def sessions(h):
    for n,role in [('student','agent'),('teacher','evaluator'),('other','agent')]:h.register_principal(n,role)
    return h.session('student'),h.session('teacher'),h.session('other')
def call(s,c,a=None,rid='r1'):return s.invoke(instance_id=s._host.instance_id,capability_id=c,arguments=a or {},request_id=rid)
def snapshot(h):
    keys=['plant','sample','geometry','thermal','field','field_target','field_actual','excitation_mode','voltage','programmed_voltage','current_limit','high_resistance_firmware_enabled','axes','power','target','method','calibration','rows','records','revision','time_ns','_clock_remainder_ns','_counter','_phase_index','_power_started_ns','_button_started_ns']
    return json.dumps(dict(state=h.read(),ledger=h._ledger(),x=h.x.tolist(),**{k:getattr(h,k) for k in keys}),sort_keys=True,allow_nan=False)

class M91Tests(unittest.TestCase):
    def budget(self,h,tol=1e-8):
        for k,v in h.invoke('ledger.read').items():
            if 'error' in k:self.assertLess(abs(v),tol,(k,v))
    def reject(self,h,k,**a):
        before=snapshot(h)
        with self.assertRaises(r.ActionRejected):h.invoke(k,**a)
        self.assertEqual(snapshot(h),before)
    def test_01_full_real_warmup_and_no_skip(self):
        h=make(warm=False);h.step(1799.);self.assertEqual(h.read()['warmup.elapsed_s'],1799.);self.reject(h,'acquisition.start');h.step(1.);self.assertTrue(h.read()['warmup.ready']);self.assertEqual(finish(h)['phase'],'succeeded')
    def test_02_power_reset_but_repeated_on_does_not_reset(self):
        h=make();h.invoke('power.set',enabled=True);self.assertEqual(h._warmup(),1800.);h.invoke('power.set',enabled=False);self.assertEqual(h._warmup(),0.);h.invoke('power.set',enabled=True);h.step(10.);self.assertEqual(h._warmup(),10.);self.reject(h,'acquisition.start')
    def test_03_button_releases_and_manual_tilt_has_finite_speed(self):
        h=make(warm=False);self.assertEqual(h.read()['power.position_m'],0.);h.invoke('ui.set_tilt',angle_rad=.26);self.assertEqual(h.read()['ui.tilt_rad'],0.);h.step(.05);self.assertAlmostEqual(h.read()['power.position_m'],.0015);self.assertAlmostEqual(h.read()['ui.tilt_rad'],.025);h.step(.05);self.assertEqual(h.read()['power.position_m'],0.);self.assertTrue(h.power);h.step(1.);self.assertEqual(h.read()['ui.tilt_rad'],.26)
    def test_04_read_describe_and_zero_step_do_not_advance(self):
        h=make();a,e,_=sessions(h);before=snapshot(h)
        for _ in range(2):a.read(instance_id=h.instance_id);e.describe(asset_type_id=r.ASSET_TYPE_ID);h.step(0.)
        self.assertEqual(snapshot(h),before)
    def test_05_real_hall_library_has_signed_experimental_curves(self):
        lib=r.library();self.assertGreaterEqual(len(lib),50)
        for row in lib.values():
            self.assertTrue(row['source']);self.assertEqual(row['evidence'],'measured_literature')
            for key,value in [('hall_coefficient','value_m3_C'),('resistivity','value_ohm_m')]:
                self.assertGreaterEqual(len(row['curves'][key]),2);self.assertTrue(all(math.isfinite(p[value]) for p in row['curves'][key]))
        self.assertGreater(r.transport(lib['starry_40927'],300)['hall_coefficient_m3_c'],0);self.assertLess(r.transport(lib['starry_36130'],300)['hall_coefficient_m3_c'],0)
    def test_06_paper_temperature_domain_is_stricter_than_raw_grid(self):
        p=r.library()['starry_40927'];self.assertIsNone(r.transport(p,299));self.assertIsNotNone(r.transport(p,300));h=make();remove(h);self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,initial_temperature_k=299))
    def test_07_external_field_target_slew_and_response_are_not_instant(self):
        h=make(field={'slew_t_s':.1,'response_tau_s':1.},warm=False);self.assertEqual(h.field_actual,0.);h.step(2);self.assertAlmostEqual(h.field_actual,.2,14);h.step(3);self.assertAlmostEqual(h.field_actual,.5-.1*math.exp(-1),14);self.reject(h,'field.set_target',field_t=3.)
    def test_08_external_field_continues_when_m91_off(self):
        h=make();h.invoke('power.set',enabled=False);h.invoke('field.set_target',field_t=-.5);before=h.field_actual;h.step(.2);self.assertLess(h.field_actual,before);self.assertFalse(h.power);h.step(3.);self.assertAlmostEqual(h.field_actual,-.5,6)
    def test_09_vdp_forward_identity_all_asymmetries(self):
        for Rs in [.1,1.,1e6]:
            for u in [-5.,-.2,0.,.2,5.]:
                a,b=r.vdp_resistances(Rs,u);self.assertAlmostEqual(math.exp(-math.pi*a/Rs)+math.exp(-math.pi*b/Rs),1.,14)
    def test_10_external_inverse_uses_observations_without_sample(self):
        h=Checked();a=dict(r.ANALYSIS_EXAMPLE,hall_factor=1.4);o=h.invoke('analysis.hall_vdp',**a);self.assertAlmostEqual(o['hall_coefficient_m3_c'],.001,12);self.assertAlmostEqual(o['vdp_resistance_a_ohm'],.2,12);self.assertAlmostEqual(o['vdp_resistance_b_ohm'],.3,12);self.assertAlmostEqual(math.exp(-math.pi*.2/o['sheet_resistance_ohm'])+math.exp(-math.pi*.3/o['sheet_resistance_ohm']),1.,12);self.assertAlmostEqual(o['assumed_single_carrier_density_m3']/o['hall_carrier_density_m3'],1.4);self.assertIsNone(h.sample)
    def test_11_positive_hall_actual_reversal_fit(self):
        h=make();o,ops=paired(h);truth=r.transport(h.record,300);self.assertLess(abs(o['hall_coefficient_m3_c']/truth['hall_coefficient_m3_c']-1),2e-5);self.assertLess(abs(o['resistivity_ohm_m']/truth['resistivity_ohm_m']-1),1e-5);self.assertEqual(o['carrier_type'],'hole_like_positive_hall');self.assertEqual([len(x['rows']) for x in ops],[9,9]);self.budget(h)
    def test_12_negative_hall_is_measured_sign_not_seebeck_guess(self):
        h=make(sample={'reference_id':'starry_36130'});o,_=paired(h);truth=r.transport(h.record,300);self.assertLess(o['hall_coefficient_m3_c'],0);self.assertLess(abs(o['hall_coefficient_m3_c']/truth['hall_coefficient_m3_c']-1),2e-4);self.assertLess(abs(o['resistivity_ohm_m']/truth['resistivity_ohm_m']-1),2e-5);self.assertEqual(o['carrier_type'],'electron_like_negative_hall');self.budget(h)
    def test_13_reversal_removes_offsets_misalignment_and_linear_drift(self):
        h=make(plant={'voltage_offset_a_v':5e-5,'voltage_offset_b_v':-4e-5,'voltage_offset_hall_v':8e-5,'voltage_drift_v_s':5e-8,'hall_misalignment_fraction':.3});o,_=paired(h);truth=r.transport(h.record,300);self.assertLess(abs(o['hall_coefficient_m3_c']/truth['hall_coefficient_m3_c']-1),2e-4);self.assertLess(o['fit_rms_v'],1e-9)
    def test_14_microamp_range_programs_ten_picoamp(self):
        h=make(method={'current_range_a':1e-6,'current_a':1e-11});self.assertAlmostEqual(h.programmed_current,1e-11,25);o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertAlmostEqual(o['rows'][1]['current_a'],1e-11,25)
    def test_15_all_six_current_ranges_have_declared_resolution(self):
        h=make()
        for full in r.CURRENT_RANGES:
            h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,current_range_a=full,current_a=3.2e-5*full));self.assertAlmostEqual(h.programmed_current,3e-5*full,20)
        self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,current_range_a=1e-6,current_a=1.1e-6))
    def test_16_millivolt_range_accepts_microvolt_not_minimum_threshold(self):
        h=make();o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertTrue(all(0<abs(v['voltage_v'])<.001 for v in o['rows']));self.assertLess(abs(o['rows'][6]['voltage_v']),1e-5)
    def test_17_voltage_fullscale_overflow_is_unavailable(self):
        h=make(plant={'voltage_offset_a_v':.002});o=finish(h);self.assertEqual(o['phase'],'failed');self.assertEqual(o['error_code'],'VOLTAGE_OVERFLOW');self.assertIsNone(o['rows'][0]['voltage_v']);self.budget(h)
    def test_18_source_compliance_lowers_actual_current_and_preserves_heat(self):
        h=make(plant={'current_contact_resistance_ohm':1e6});o=finish(h);self.assertEqual(o['phase'],'failed');self.assertEqual(o['error_code'],'SOURCE_COMPLIANCE');self.assertLess(abs(o['rows'][-1]['current_a']),1.1e-5);self.assertGreater(h._ledger()['source_input_j'],0);self.budget(h)
    def test_19_zero_excitation_is_not_fake_hall(self):
        h=make(method={'current_a':0.});self.reject(h,'acquisition.start');self.assertEqual(h.programmed_current,0.)
    def test_20_each_contact_phase_has_real_elapsed_duration(self):
        h=make(method={'phase_duration_s':20.});h.invoke('acquisition.start');h.step(19.);self.assertEqual(h.rows,[]);h.step(1.);self.assertEqual(len(h.rows),1);self.assertEqual(h.rows[0]['polarity'],'zero');self.assertEqual(h._polarity(),'positive');h.step(160.);self.assertEqual(h.phase,'succeeded');self.assertEqual([x['time_s'] for x in h.rows],[20.*i for i in range(1,10)])
    def test_21_sixty_hz_partition_matches_bulk_event_sampling(self):
        a=make();b=make()
        for h in (a,b):h.invoke('acquisition.start')
        a.step(1.)
        for _ in range(60):b.step(1/60)
        self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.rows,b.rows);np.testing.assert_allclose(a.x,b.x,rtol=2e-7,atol=1e-13);self.budget(a);self.budget(b)
    def test_22_ten_second_scene_clock_matches_bulk_warmup_and_phases(self):
        a=make(method={'phase_duration_s':20.},warm=False);b=make(method={'phase_duration_s':20.},warm=False);a.step(1800.)
        for _ in range(180):b.step(10.)
        for h in (a,b):h.invoke('acquisition.start')
        a.step(180.)
        for _ in range(18):b.step(10.)
        self.assertEqual(a.rows,b.rows);self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(b._warmup(),1800.);np.testing.assert_allclose(a.x,b.x,rtol=1e-7,atol=1e-12)
    def test_23_cancel_freezes_rows_and_electrical_energy_but_not_heat(self):
        h=make();h.invoke('acquisition.start');h.step(.25);h.invoke('acquisition.stop');rows=copy.deepcopy(h.rows);energy=h.x[2:5].copy();temperature=h.x[0];h.step(2.);self.assertEqual(h.rows,rows);np.testing.assert_array_equal(h.x[2:5],energy);self.assertLess(h.x[0],temperature);self.assertEqual(h._electrical()['current'],0.);self.budget(h)
    def test_24_power_off_stops_acquisition_not_external_temperature_control(self):
        h=make();h.invoke('thermal.set_temperature',temperature_k=305.);h.invoke('acquisition.start');h.step(.15);h.invoke('power.set',enabled=False);before=h._temperature();energy=h.x[2:5].copy();h.step(2.);self.assertGreater(h._temperature(),before);np.testing.assert_array_equal(h.x[2:5],energy);self.assertEqual(h.phase,'cancelled');self.assertTrue(h.thermal_enabled);self.budget(h)
    def test_25_source_joule_contact_and_external_thermal_ledgers(self):
        h=make();finish(h);L=h._ledger();self.assertGreater(L['source_input_j'],0);self.assertAlmostEqual(L['source_input_j'],L['sample_joule_j']+L['contact_heat_export_j'],15);self.assertAlmostEqual(L['contact_heat_export_j'],6*.1*(.0005**2)*20,15);self.budget(h)
    def test_26_temperature_target_and_bounded_power_only_advance_on_step(self):
        h=make(thermal={'heating_limit_w':.1});before=h.x.copy();h.invoke('thermal.set_temperature',temperature_k=320);np.testing.assert_array_equal(h.x,before);start=h.x[5];h.step(.2);self.assertAlmostEqual(h.x[5]-start,.02,11);self.budget(h);self.reject(h,'thermal.set_temperature',temperature_k=400.)
    def test_27_loading_removal_sensible_heat_mass_and_volume(self):
        h=make(plant={'ambient_k':299.},warm=False);self.assertAlmostEqual(h._mass(),.0003,15);self.assertAlmostEqual(h.loaded_sensible,.09,12);self.budget(h);h.step(2.);h.invoke('thermal.disable');remove(h);self.assertEqual(h._volume(),0.);self.assertAlmostEqual(h.mass_removed,.0003,15);self.assertGreater(h.removed_sensible,0.);self.budget(h)
    def test_28_geometry_is_locked_while_sample_present(self):
        h=make();self.reject(h,'geometry.configure',side_m=.02,thickness_m=.0005);remove(h);h.invoke('geometry.configure',side_m=.02,thickness_m=.0005);h.invoke('sample.load',**r.SAMPLE_EXAMPLE);self.assertAlmostEqual(h._mass(),.0012,15);self.budget(h)
    def test_29_open_fixture_disconnects_external_heater(self):
        h=make();h.invoke('thermal.set_temperature',temperature_k=320.);h.invoke('sample.set_clamp',closed=False);h.step(2.);self.assertAlmostEqual(h._temperature(),300.,12);self.assertEqual(h.x[5],0.);self.budget(h)
    def test_30_invalid_arguments_and_busy_commands_are_atomic(self):
        h=make()
        for dt in [True,-1.,float('inf'),float('nan')]:
            before=snapshot(h)
            with self.assertRaises(r.ActionRejected):h.step(dt)
            self.assertEqual(snapshot(h),before)
        self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,phase_duration_s=.0100000000005));self.reject(h,'thermal.set_temperature',temperature_k=True);self.reject(h,'sample.set_clamp',closed=True,extra=1);h.invoke('acquisition.start');self.reject(h,'field.set_target',field_t=-.5);self.reject(h,'sample.set_clamp',closed=False);self.reject(h,'sensor.calibrate',**r.CALIBRATION_EXAMPLE)
    def test_31_field_must_settle_and_clamp_must_close(self):
        h=make();h.invoke('field.set_target',field_t=-.5);self.reject(h,'acquisition.start');h.step(3.);h.invoke('sample.set_clamp',closed=False);self.reject(h,'acquisition.start');self.reject(h,'sample.remove')
    def test_32_calibration_uses_external_coefficients_and_changes_reading(self):
        h=make(plant={'voltage_gain':1.2},field={'probe_gain':1.1,'probe_offset_t':0.});raw,_=paired(h);h.invoke('sensor.calibrate',**dict(r.CALIBRATION_EXAMPLE,voltage_gain_correction=1/1.2,field_gain_correction=1/1.1,field_offset_t=0.));fixed,_=paired(h);truth=r.transport(h.record,300);self.assertGreater(abs(raw['resistivity_ohm_m']/truth['resistivity_ohm_m']-1),.19);self.assertLess(abs(fixed['resistivity_ohm_m']/truth['resistivity_ohm_m']-1),1e-5);self.assertLess(abs(fixed['hall_coefficient_m3_c']/truth['hall_coefficient_m3_c']-1),1e-4)
    def test_33_two_instances_have_independent_material_field_and_clock(self):
        a=make();b=make(sample={'reference_id':'starry_36130'});before=snapshot(b);finish(a);a.invoke('field.set_target',field_t=-.5);a.step(3.);self.assertEqual(snapshot(b),before)
    def test_34_visibility_principal_and_target_guards(self):
        h=make();a,e,_=sessions(h);self.assertNotIn('truth.sample_k',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.field_t',e.read(instance_id=h.instance_id)['observations']);self.assertNotIn('field.set_target',a.describe(instance_id=h.instance_id)['capabilities']);self.assertEqual(call(a,'sample.load',r.SAMPLE_EXAMPLE)['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(r.ActionRejected):h.session('unknown')
        with self.assertRaises(r.ActionRejected):a.read(instance_id=h.instance_id,state_ids=['truth.sample_k'])
        with self.assertRaises(r.ActionRejected):a.read(instance_id='different')
    def test_35_idempotence_and_operation_owner_cancellation(self):
        h=make();a,e,b=sessions(h);start=call(a,'acquisition.start');self.assertEqual(start,call(a,'acquisition.start'));self.assertEqual(call(a,'power.set',{'enabled':True})['error']['code'],'REQUEST_CONFLICT');self.assertEqual(call(b,'acquisition.stop')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(b,'power.set',{'enabled':False},'off')['error']['code'],'ACCESS_DENIED');h.step(.25);op=start['operation_id'];reply=a.cancel(op,'cancel');self.assertEqual(reply,a.cancel(op,'cancel'));self.assertEqual(a.poll(op)['status'],'cancelled')
    def test_36_all_roles_public_results_hide_selected_provenance(self):
        h=make();a,e,_=sessions(h);op=call(a,'acquisition.start')['operation_id'];h.step(h.expected_duration)
        for payload in [call(a,'acquisition.result',rid='a'),call(e,'acquisition.result',rid='e'),a.poll(op)]:
            text=json.dumps(payload);self.assertNotIn('starry_40927',text);self.assertNotIn('Cu1.99',text);self.assertNotIn('original_csv',text)
        self.assertEqual(call(a,'provenance.read',rid='p')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(e,'provenance.read',rid='p')['result']['reference_id'],'starry_40927')
    def test_37_acquisition_keeps_source_snapshot_after_new_load(self):
        h=make();old=finish(h);remove(h);self.assertIsNone(h.invoke('provenance.read')['reference_id']);h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,reference_id='starry_36130'));self.assertEqual(h.invoke('acquisition.result'),old)
    def test_38_reversal_inverse_rejects_missing_asymmetric_or_rankless_input(self):
        h=Checked();a=copy.deepcopy(r.ANALYSIS_EXAMPLE)
        for row in a['observations']:row['field_t']=.5
        self.reject(h,'analysis.hall_vdp',**a)
        a=copy.deepcopy(r.ANALYSIS_EXAMPLE)
        for row in a['observations']:
            if row['field_t']<0:row['field_t']=-.1
        self.reject(h,'analysis.hall_vdp',**a)
        a=copy.deepcopy(r.ANALYSIS_EXAMPLE)
        for row in a['observations']:row['current_a']=0.
        self.reject(h,'analysis.hall_vdp',**a);self.reject(h,'analysis.hall_vdp',**dict(r.ANALYSIS_EXAMPLE,accept_single_carrier_model=False))
    def test_39_assumptions_and_unsupported_are_explicit(self):
        h=Checked();self.reject(h,'plant.configure',**dict(r.PLANT_EXAMPLE,accept_engineering=False));self.reject(h,'plant.configure',**dict(r.PLANT_EXAMPLE,compliance_voltage_v=100));h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('geometry.configure',**r.GEOMETRY_EXAMPLE);self.reject(h,'thermal.configure',**dict(r.THERMAL_EXAMPLE,accept_external_thermal_boundary=False));h.invoke('thermal.configure',**r.THERMAL_EXAMPLE);self.reject(h,'field.configure',**dict(r.FIELD_EXAMPLE,accept_external_magnet=False));self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,accept_directional_scalar_and_thermal_assumptions=False))
        for c in r.UNSUPPORTED:self.reject(h,c)
    def test_40_contract_schemas_and_actual_returns(self):
        h=make();meta=h.describe()
        for key,c in meta['capabilities'].items():self.assertEqual(asset_tool.lint_schema(c['input_schema']),[],key);self.assertEqual(asset_tool.lint_schema(c['output_schema']),[],key);self.assertEqual(asset_tool.validate_value(c['example_arguments'],c['input_schema']),[],key)
        for key,args,result in OBSERVED:self.assertEqual(asset_tool.validate_value(result,meta['capabilities'][key]['output_schema']),[],key)
        for key,value in h.read().items():self.assertEqual(asset_tool.validate_value(value,meta['state_slots'][key]['value_schema']),[],key)
    def test_41_all_implemented_session_entries_executed(self):
        h=Checked();h.register_principal('eval','evaluator');s=h.session('eval');seen=set();plan=[('plant.configure',r.PLANT_EXAMPLE),('geometry.configure',r.GEOMETRY_EXAMPLE),('thermal.configure',r.THERMAL_EXAMPLE),('field.configure',r.FIELD_EXAMPLE),('sample.load',r.SAMPLE_EXAMPLE),('power.set',{'enabled':True}),('sample.set_clamp',{'closed':True}),('ui.set_tilt',{'angle_rad':.13}),('thermal.set_temperature',{'temperature_k':300.}),('field.set_target',{'field_t':.5}),('method.configure',r.METHOD_EXAMPLE),('sensor.calibrate',r.CALIBRATION_EXAMPLE),('sim.step',{'dt_s':1800.}),('acquisition.start',{}),('sim.step',{'dt_s':.25}),('acquisition.stop',{}),('acquisition.result',{}),('analysis.hall_vdp',r.ANALYSIS_EXAMPLE),('provenance.read',{}),('ledger.read',{}),('options.configure',{'high_resistance_firmware_enabled':True}),('excitation.voltage.configure',r.VOLTAGE_EXAMPLE),('acquisition.start',{}),('sim.step',{'dt_s':.25}),('acquisition.stop',{}),('thermal.disable',{}),('sample.set_clamp',{'closed':False}),('sim.step',{'dt_s':2.}),('sample.remove',{}),('power.set',{'enabled':False})]
        for i,(cap,args) in enumerate(plan):
            reply=call(s,cap,args,str(i));self.assertIn(reply['status'],('succeeded','accepted'),(cap,reply));self.assertEqual(asset_tool.validate_value(reply['result'],r.CAPS[cap]['output_schema']),[],cap);seen.add(cap)
        self.assertEqual(seen,set(r.CAPS)-set(r.UNSUPPORTED));self.budget(h)
    def test_42_true_temperature_outside_source_domain_fails_measurement(self):
        h=make();h.invoke('thermal.set_temperature',temperature_k=299.);h.invoke('acquisition.start');h.step(.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'REFERENCE_TEMPERATURE_RANGE');self.budget(h)

    def test_43_voltage_excitation_requires_explicit_hr_firmware(self):
        h=make();self.assertEqual(h.excitation_mode,'current');self.assertFalse(h.high_resistance_firmware_enabled);self.reject(h,'excitation.voltage.configure',**r.VOLTAGE_EXAMPLE);a,e,_=sessions(h);self.assertEqual(call(a,'options.configure',{'high_resistance_firmware_enabled':True})['error']['code'],'ACCESS_DENIED');self.assertEqual(call(e,'options.configure',{'high_resistance_firmware_enabled':True})['status'],'succeeded');self.assertEqual(call(a,'excitation.voltage.configure',r.VOLTAGE_EXAMPLE,'v')['status'],'succeeded');self.assertEqual(h.excitation_mode,'voltage')
    def test_44_voltage_actual_pair_inverse_positive_and_negative_materials(self):
        for reference in ['starry_40927','starry_36130']:
            h=make(sample={'reference_id':reference});h.invoke('options.configure',high_resistance_firmware_enabled=True);h.invoke('excitation.voltage.configure',**r.VOLTAGE_EXAMPLE);o,_=paired(h);truth=r.transport(h.record,300.);self.assertLess(abs(o['hall_coefficient_m3_c']/truth['hall_coefficient_m3_c']-1),1e-4);self.assertLess(abs(o['resistivity_ohm_m']/truth['resistivity_ohm_m']-1),1e-5);self.budget(h)
    def test_45_original_method_restores_current_mode_and_option_can_disable(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True);h.invoke('excitation.voltage.configure',**r.VOLTAGE_EXAMPLE);self.reject(h,'options.configure',high_resistance_firmware_enabled=False);h.invoke('method.configure',**r.METHOD_EXAMPLE);self.assertEqual(h.excitation_mode,'current');self.assertEqual(h.read()['excitation.programmed_voltage_v'],0.);h.invoke('options.configure',high_resistance_firmware_enabled=False);self.assertEqual(finish(h)['phase'],'succeeded')
    def test_46_voltage_programming_four_ranges_and_100nv_resolution(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True)
        for full in r.VOLTAGE_EXCITATION_RANGES:
            h.invoke('excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,voltage_range_v=full,voltage_v=1.2e-5*full));self.assertAlmostEqual(h.programmed_voltage,full*1e-5,20)
        h.invoke('excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,voltage_range_v=.01,voltage_v=1e-7));self.assertAlmostEqual(h.programmed_voltage,1e-7,22);self.reject(h,'excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,voltage_range_v=1.,voltage_v=1.001));self.reject(h,'excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,voltage_range_v=10.,voltage_v=10.001))
    def test_47_voltage_source_current_limit_is_physical_and_voltage_droops(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True);h.invoke('excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,voltage_range_v=10.,voltage_v=10.,current_measurement_range_a=.1,current_limit_a=.1));h.invoke('acquisition.start');h.step(.15);e=h._electrical();self.assertAlmostEqual(e['current'],.1,14);self.assertLess(e['source_voltage'],3.);self.assertTrue(e['compliance']);self.assertAlmostEqual(e['source_voltage']*e['current'],e['sample_power']+e['contact_power'],14);self.assertAlmostEqual(h.read()['excitation.source_voltage_v'],e['source_voltage']);h.step(.05);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'SOURCE_COMPLIANCE');self.budget(h)
    def test_48_measurement_overflow_does_not_limit_actual_current(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True);h.invoke('excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,current_measurement_range_a=1e-8));h.invoke('acquisition.start');h.step(.15);e=h._electrical();self.assertGreater(e['current'],1e-4);self.assertFalse(e['compliance']);self.assertIsNone(h.read()['measured.current_a']);self.assertGreater(h._ledger()['source_input_j'],0.);h.step(.05);self.assertEqual(h.error,'CURRENT_MEASUREMENT_OVERFLOW');self.assertIsNone(h.rows[-1]['current_a']);self.budget(h)
    def test_49_running_voltage_mode_rejects_mode_and_option_changes_atomically(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True);h.invoke('excitation.voltage.configure',**r.VOLTAGE_EXAMPLE);h.invoke('acquisition.start');self.reject(h,'method.configure',**r.METHOD_EXAMPLE);self.reject(h,'excitation.voltage.configure',**r.VOLTAGE_EXAMPLE);self.reject(h,'options.configure',high_resistance_firmware_enabled=False)
    def test_50_all_four_current_measurement_ranges_accept_subrange_signals(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True)
        for full,V in [(1e-8,1e-7),(1e-5,1e-5),(.01,.01),(.1,.01)]:
            h.invoke('excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,voltage_range_v=.01,voltage_v=V,current_measurement_range_a=full));o=finish(h);self.assertEqual(o['phase'],'succeeded');current=o['rows'][1]['current_a'];self.assertGreater(current,0.);self.assertLess(current,full)
    def test_51_voltage_sixty_hz_partition_preserves_current_voltage_and_energy(self):
        a=make();b=make()
        for h in (a,b):h.invoke('options.configure',high_resistance_firmware_enabled=True);h.invoke('excitation.voltage.configure',**r.VOLTAGE_EXAMPLE);h.invoke('acquisition.start')
        a.step(1.)
        for _ in range(60):b.step(1/60)
        self.assertEqual(a.rows,b.rows);np.testing.assert_allclose(a.x,b.x,rtol=1e-7,atol=1e-13);self.budget(a);self.budget(b)
    def test_52_voltage_cancel_freezes_observations_and_source_energy(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True);h.invoke('excitation.voltage.configure',**r.VOLTAGE_EXAMPLE);h.invoke('acquisition.start');h.step(.25);h.invoke('acquisition.stop');rows=copy.deepcopy(h.rows);energy=h.x[2:5].copy();h.step(2.);self.assertEqual(h.rows,rows);np.testing.assert_array_equal(h.x[2:5],energy);self.assertIsNone(h.read()['excitation.source_voltage_v']);self.budget(h)
    def test_53_overflow_null_cannot_be_input_to_inverse(self):
        h=make();a=copy.deepcopy(r.ANALYSIS_EXAMPLE);a['observations'][1]['current_a']=None;self.reject(h,'analysis.hall_vdp',**a)
    def test_54_scpi_current_limit_and_source_zero_voltage_guards(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True)
        for limit in [0.,9e-8,.101]:self.reject(h,'excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,current_limit_a=limit))
        h.invoke('excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,current_limit_a=1e-7));self.assertAlmostEqual(h.current_limit,1e-7,22);o=finish(h);self.assertEqual(o['error_code'],'SOURCE_COMPLIANCE');h.invoke('excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,voltage_v=0.));self.reject(h,'acquisition.start');self.budget(h)
    def test_55_voltage_current_measurement_quantization_is_declared_not_true_copy(self):
        h=make();h.invoke('options.configure',high_resistance_firmware_enabled=True);h.invoke('excitation.voltage.configure',**dict(r.VOLTAGE_EXAMPLE,current_resolution_fraction=.01));h.invoke('acquisition.start');h.step(.15);observed=h.read()['measured.current_a'];true=h._electrical()['current'];self.assertAlmostEqual(observed,.0005,14);self.assertGreater(abs(observed-true),1e-5);h.invoke('acquisition.stop');self.budget(h)

if __name__=='__main__':unittest.main(verbosity=2)
