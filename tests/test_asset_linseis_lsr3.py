"""CHEM139 actual transport, finite thermal program and session contract regression."""
import copy,importlib.util,json,math,unittest
from pathlib import Path
import numpy as np
from scipy.integrate import quad
from scripts.asset_runtime import linseis_lsr3 as r
ROOT=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('lsr_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');asset_tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(asset_tool)
OBSERVED=[]
class Checked(r.LinseisLsr3Runtime):
    def invoke(self,k,**a):
        before=self.read();out=super().invoke(k,**a);after=self.read();changed={x for x in after if after[x]!=before[x]}
        assert not changed-set(r.CAPS[k]['effects']),(k,changed-set(r.CAPS[k]['effects']))
        assert not asset_tool.validate_value(out,r.CAPS[k]['output_schema']),(k,asset_tool.validate_value(out,r.CAPS[k]['output_schema']))
        OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(out)));return out

def make(plant=None,sample=None,method=None,geometry=None,settle=True):
    h=Checked();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,**(plant or {})));h.invoke('geometry.configure',**dict(r.GEOMETRY_EXAMPLE,**(geometry or {})));h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,**(sample or {})));h.invoke('power.set',enabled=True);h.invoke('sample.set_clamp',closed=True);h.step(2.);h.invoke('furnace.set_open',opened=False);h.step(1.8);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,**(method or {})));h.invoke('thermal.set_temperature',temperature_k=305.,ramp_rate_k_min=100.);h.invoke('thermal.set_gradient',delta_k=1.)
    if settle:h.step(7.)
    return h

def finish(h):h.invoke('acquisition.start');h.step(h.method['duration_s']);return h.invoke('acquisition.result')
def remove(h):
    if h.phase=='running':h.invoke('acquisition.stop')
    h.invoke('furnace.set_open',opened=True);h.step(1.8);h.invoke('sample.set_clamp',closed=False);h.step(2.);h.invoke('sample.remove')
def sessions(h):
    for n,role in [('student','agent'),('teacher','evaluator'),('other','agent')]:h.register_principal(n,role)
    return h.session('student'),h.session('teacher'),h.session('other')
def call(s,c,a=None,rid='r1'):return s.invoke(instance_id=s._host.instance_id,capability_id=c,arguments=a or {},request_id=rid)
def snapshot(h):return json.dumps(dict(state=h.read(),plant=h.plant,sample=h.sample,geometry=h.geometry,ledger=h._ledger(),rows=h.rows,points=h.points,records=h.records,revision=h.revision,calibration=h.calibration,method=h.method,x=h.x.tolist(),axes=h.axes,program=h.program),sort_keys=True,allow_nan=False)

class LsrTests(unittest.TestCase):
    def budget(self,h,tol=1e-7):
        for k,v in h.invoke('ledger.read').items():
            if 'error' in k:self.assertLess(abs(v),tol,(k,v))
    def reject(self,h,k,**a):
        before=snapshot(h)
        with self.assertRaises(r.ActionRejected):h.invoke(k,**a)
        self.assertEqual(snapshot(h),before)
    def test_01_targets_and_axes_never_teleport(self):
        h=make();old=h.x.copy();h.invoke('thermal.set_temperature',temperature_k=315.,ramp_rate_k_min=100.);np.testing.assert_array_equal(old,h.x);h.invoke('furnace.set_open',opened=True);self.assertEqual(h.axes['furnace']['position'],0.);h.step(.9);self.assertAlmostEqual(h.axes['furnace']['position'],.09,12);self.budget(h)
    def test_02_read_describe_zero_are_pure(self):
        h=make();a,e,_=sessions(h);before=snapshot(h)
        for _ in range(2):a.read(instance_id=h.instance_id);e.describe(asset_type_id=r.ASSET_TYPE_ID);h.step(0.)
        self.assertEqual(snapshot(h),before)
    def test_03_real_digitized_material_curves(self):
        lib=r.library();self.assertGreaterEqual(len(lib),50)
        for v in lib.values():
            self.assertTrue(v['source']);self.assertTrue(v['evidence'])
            for key in ['seebeck','resistivity','thermal_conductivity']:
                t,y=r.curve_xy(v,key);self.assertTrue(np.all(np.diff(t)>0));self.assertTrue(np.all(np.isfinite(y)))
                if key!='seebeck':self.assertTrue(np.all(y>0))
        value=r.transport(lib['tematdb_00009'],305.,305.);self.assertAlmostEqual(value['seebeck_v_k'],1.8780087880629725e-4,13);self.assertAlmostEqual(value['resistivity_ohm_m'],8.038874346063209e-6,14)
    def test_04_seebeck_is_temperature_integral(self):
        row=r.library()['tematdb_00009'];t,y=r.curve_xy(row,'seebeck');hot=330.;cold=305.;expected=quad(lambda T:float(np.interp(T,t,y)),cold,hot,points=t[(t>cold)&(t<hot)],epsabs=1e-13)[0];actual=r.transport(row,hot,cold);self.assertAlmostEqual(actual['emf_v'],expected,12);self.assertAlmostEqual(r.transport(row,cold,hot)['emf_v'],-expected,12)
    def test_05_actual_positive_transport(self):
        h=make();o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertEqual(len(o['rows']),60);self.assertEqual(len(o['transport_points']),20);self.assertEqual([x['polarity'] for x in o['rows'][:3]],['zero','positive','negative']);point=o['transport_points'][-1];ref=r.transport(h.record,305.5,304.5);self.assertLess(abs(point['seebeck_v_k']/ref['seebeck_v_k']-1),.001);self.assertLess(abs(point['resistivity_ohm_m']/ref['resistivity_ohm_m']-1),.001);self.budget(h)
    def test_06_negative_material_sign(self):
        h=make(sample={'reference_id':'tematdb_00038'});o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertLess(o['transport_points'][-1]['seebeck_v_k'],0);self.assertGreater(o['transport_points'][-1]['conductivity_s_m'],0);self.budget(h)
    def test_07_inverse_uses_only_external_values(self):
        h=Checked();f=h.invoke('analysis.transport',**r.ANALYSIS_EXAMPLE);self.assertAlmostEqual(f['seebeck_v_k'],2e-4,12);self.assertAlmostEqual(f['four_probe_resistance_ohm'],.01,12);self.assertAlmostEqual(f['resistivity_ohm_m'],.01*.003**2/.008,12);self.assertIsNone(h.sample)
    def test_08_bulk_length_is_distinct_from_probe_spacing(self):
        h=make(plant={'current_contact_resistance_ohm':0.,'voltage_contact_leak_fraction':0.},method={'contact_resistance_correction_ohm':0.});h.invoke('acquisition.start');h.step(.05);v=h._electrical(h._temperature(1),h._temperature(2));rho=r.transport(h.record,h._temperature(1),h._temperature(2))['resistivity_ohm_m'];self.assertAlmostEqual(v['bulk_r'],rho*.02/.003**2,12);self.assertAlmostEqual((v['voltage']-v['emf'])/v['current'],rho*.008/.003**2,12)
    def test_09_four_wire_contact_rejection_and_explicit_correction(self):
        a=make(plant={'current_contact_resistance_ohm':2.,'voltage_contact_leak_fraction':0.},method={'contact_resistance_correction_ohm':0.});b=make(plant={'current_contact_resistance_ohm':.2,'voltage_contact_leak_fraction':0.},method={'contact_resistance_correction_ohm':0.});oa=finish(a);ob=finish(b);self.assertEqual(oa['transport_points'],ob['transport_points']);self.assertGreater(a._ledger()['joule_contact_export_j'],b._ledger()['joule_contact_export_j']*9)
        c=make(method={'contact_resistance_correction_ohm':0.});oc=finish(c);self.assertGreater(oc['transport_points'][-1]['resistivity_ohm_m'],ob['transport_points'][-1]['resistivity_ohm_m']);self.budget(a);self.budget(b);self.budget(c)
    def test_10_geometry_factor_is_used_and_inverted(self):
        h=make(method={'geometry_factor':1.2});o=finish(h);ref=r.transport(h.record,305.5,304.5);self.assertLess(abs(o['transport_points'][-1]['resistivity_ohm_m']/ref['resistivity_ohm_m']-1),.001)
    def test_11_actual_compliance_limits_current(self):
        h=make(plant={'current_compliance_v':.001,'current_contact_resistance_ohm':1.},method={'current_a':.1,'contact_resistance_correction_ohm':.001});h.invoke('acquisition.start');h.step(.1);row=h.rows[-1];self.assertGreater(row['current_a'],0);self.assertLess(row['current_a'],.001);self.budget(h)
    def test_12_joule_contact_peltier_energy_balance(self):
        h=make(method={'current_a':.08});finish(h);d=h._ledger();self.assertGreater(d['joule_sample_j'],0);self.assertGreater(d['joule_contact_export_j'],0);self.assertAlmostEqual(d['electrical_source_input_j'],d['joule_sample_j']+d['joule_contact_export_j']+d['peltier_input_j'],15);self.budget(h)
    def test_13_peltier_net_equals_source_thermoelectric_power(self):
        h=make();h.invoke('acquisition.start');h.step(.05);v=h._electrical(h._temperature(1),h._temperature(2));self.assertAlmostEqual(v['current']*v['s']*(h._temperature(1)-h._temperature(2)),v['current']*v['emf'],18)
    def test_14_hundred_k_per_min_really_moves_furnace(self):
        h=make();T=h._temperature(0);h.invoke('thermal.set_temperature',temperature_k=355.,ramp_rate_k_min=100.);h.step(3.);self.assertAlmostEqual(h.programmed,310.,7);self.assertAlmostEqual(h._temperature(0)-T,5.,6);self.assertGreater((h._temperature(1)+h._temperature(2))/2,309.);self.budget(h)
    def test_15_minimum_ramp_rate(self):
        h=make();h.invoke('thermal.set_temperature',temperature_k=306.,ramp_rate_k_min=.01);h.step(6.);self.assertAlmostEqual(h.programmed,305.001,8);self.budget(h)
    def test_16_program_segments_hold_and_zero_hold_events(self):
        h=make();h.invoke('program.configure',segments=[dict(temperature_k=305.,ramp_rate_k_min=100.,hold_s=0.),dict(temperature_k=305.,ramp_rate_k_min=100.,hold_s=0.),dict(temperature_k=306.,ramp_rate_k_min=100.,hold_s=.2),dict(temperature_k=305.,ramp_rate_k_min=100.,hold_s=0.)]);h.invoke('program.start');h.step(1.4);self.assertFalse(h.program_running);self.assertAlmostEqual(h.programmed,305.,9);self.budget(h)
    def test_17_program_stop_freezes_setpoint_not_temperature(self):
        h=make();h.invoke('program.configure',**r.PROGRAM_EXAMPLE);h.invoke('program.start');h.step(.3);before=h.programmed;temperature=h.x.copy();h.invoke('program.stop');self.assertEqual(h.target,before);h.step(.2);self.assertEqual(h.programmed,before);self.assertFalse(np.array_equal(h.x,temperature));self.budget(h)
    def test_18_ramped_temperature_curve_has_observed_transport(self):
        h=make(method={'duration_s':3.,'equilibrium_tolerance_k':1.});h.invoke('program.configure',segments=[dict(temperature_k=315.,ramp_rate_k_min=100.,hold_s=0.)]);h.invoke('program.start');o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertGreater(o['transport_points'][-1]['temperature_k']-o['transport_points'][0]['temperature_k'],4.);self.assertNotEqual(o['transport_points'][0]['seebeck_v_k'],o['transport_points'][-1]['seebeck_v_k']);self.budget(h)
    def test_19_heater_saturation_limits_real_heat(self):
        h=make(plant={'heater_limit_w':1.},settle=False);before=h.x[3];h.step(1.);self.assertLessEqual(h.x[3]-before,1.+1e-9);self.assertLess(h._temperature(0)-298.15,.1);self.budget(h)
    def test_20_open_furnace_uses_external_exchange(self):
        h=make();h.invoke('thermal.disable');h.invoke('furnace.set_open',opened=True);before=h._temperature(1);h.step(1.8);self.assertLess(h._temperature(1),before);self.assertGreater(h._ledger()['ambient_loss_j'],0);self.budget(h)
    def test_21_loading_unloading_preserves_mass_volume_sensible(self):
        h=make(sample={'initial_temperature_k':303.15});self.assertAlmostEqual(h.loaded_sensible,.001386*200.*5.,12);self.budget(h);remove(h);d=h._ledger();self.assertEqual(d['mass_present_kg'],0);self.assertEqual(d['volume_present_m3'],0);self.assertAlmostEqual(d['mass_removed_kg'],.001386,12);self.assertGreater(d['removed_sensible_j'],0);self.budget(h)
    def test_22_loaded_geometry_change_is_rejected(self):
        h=make();self.reject(h,'geometry.configure',**dict(r.GEOMETRY_EXAMPLE,width_m=.004));remove(h);self.reject(h,'geometry.configure',**dict(r.GEOMETRY_EXAMPLE,probe_spacing_m=.005));self.reject(h,'geometry.configure',**dict(r.GEOMETRY_EXAMPLE,length_m=.004));h.invoke('geometry.configure',**dict(r.GEOMETRY_EXAMPLE,width_m=.004));self.assertEqual(h.geometry['width_m'],.004)
    def test_23_cancel_freezes_rows_stops_current_retains_heat(self):
        h=make();h.invoke('acquisition.start');h.step(.1);self.assertNotEqual(h._electrical(h._temperature(1),h._temperature(2))['current'],0);h.invoke('acquisition.stop');rows=copy.deepcopy(h.rows);joule=h.x[5];control=h.x[3];h.step(.5);self.assertEqual(h.rows,rows);self.assertEqual(h.x[5],joule);self.assertEqual(h._polarity(),'zero');self.assertNotEqual(h.x[3],control);self.budget(h)
    def test_24_power_off_preserves_residual_and_passive_cooling(self):
        h=make();h.invoke('acquisition.start');h.step(.1);h.invoke('power.set',enabled=False);x=h.x.copy();stored=h._ledger()['thermal_stored_j'];h.step(.5);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.x[3],x[3]);self.assertEqual(h.x[4],x[4]);self.assertEqual(h.x[5],x[5]);self.assertLess(h._ledger()['thermal_stored_j'],stored);self.assertIsNone(h._observed()['voltage']);self.budget(h)
    def test_25_reference_overlap_no_extrapolation(self):
        record=r.library()['tematdb_00009'];self.assertIsNone(r.transport(record,600.,599.));h=make();h.invoke('thermal.set_temperature',temperature_k=600.,ramp_rate_k_min=100.);h.step(180.);self.reject(h,'acquisition.start');self.assertIsNone(h._observed()['voltage']);self.budget(h)
    def test_26_crossing_reference_boundary_fails_record(self):
        h=make(method={'duration_s':6.,'equilibrium_tolerance_k':1.});h.invoke('thermal.set_temperature',temperature_k=477.,ramp_rate_k_min=100.);h.step(107.);h.invoke('program.configure',segments=[dict(temperature_k=490.,ramp_rate_k_min=100.,hold_s=0.)]);h.invoke('program.start');h.invoke('acquisition.start');h.step(6.);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'REFERENCE_TEMPERATURE_RANGE');self.assertIsNone(h.rows[-1]['voltage_v']);self.assertEqual(h._polarity(),'zero');self.budget(h)
    def test_27_explicit_sensor_calibration_changes_observations(self):
        h=make(plant={'voltage_gain':1.2,'voltage_offset_v':2e-6,'hot_sensor_bias_k':.1,'cold_sensor_bias_k':-.1});h.invoke('sensor.calibrate',voltage_gain_correction=1/1.2,voltage_offset_v=2e-6,hot_offset_k=.1,cold_offset_k=-.1);o=finish(h);self.assertLess(abs(o['transport_points'][-1]['seebeck_v_k']/1.878008788e-4-1),.001)
    def test_28_sixty_hz_partition_clock_and_sampling(self):
        a=make();b=make()
        for h in (a,b):h.invoke('acquisition.start')
        a.step(3.)
        for _ in range(180):b.step(1/60)
        self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.rows,b.rows);np.testing.assert_allclose(a.x,b.x,rtol=1e-9,atol=2e-9);self.budget(a);self.budget(b)
    def test_29_program_motion_partition_invariance(self):
        a=make();b=make()
        for h in (a,b):h.invoke('program.configure',**r.PROGRAM_EXAMPLE);h.invoke('program.start')
        a.step(2.)
        for _ in range(137):b.step(2/137)
        self.assertEqual(a.time_ns,b.time_ns);self.assertAlmostEqual(a.programmed,b.programmed,8);np.testing.assert_allclose(a.x,b.x,rtol=1e-8,atol=1e-8)
    def test_30_input_grids_and_rejections_are_atomic(self):
        h=make()
        for dt in [True,-1,float('inf'),float('nan')]:
            before=snapshot(h)
            with self.assertRaises(r.ActionRejected):h.step(dt)
            self.assertEqual(snapshot(h),before)
        self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,duration_s=.2));self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,current_a=.22));self.reject(h,'thermal.set_gradient',delta_k=-1.);self.reject(h,'thermal.set_temperature',temperature_k=1100.,ramp_rate_k_min=100.);self.reject(h,'thermal.set_temperature',temperature_k=310.,ramp_rate_k_min=101.);self.reject(h,'thermal.set_temperature',temperature_k=True,ramp_rate_k_min=1.)
    def test_31_busy_mechanical_interlocks(self):
        h=make();self.reject(h,'sample.set_clamp',closed=False);h.invoke('acquisition.start');self.reject(h,'furnace.set_open',opened=True);self.reject(h,'sample.remove');self.reject(h,'method.configure',**r.METHOD_EXAMPLE);self.reject(h,'sensor.calibrate',**r.CALIBRATION_EXAMPLE)
    def test_32_two_instances_are_independent(self):
        a=make();b=make(sample={'reference_id':'tematdb_00038'});before=snapshot(b);finish(a);self.assertEqual(snapshot(b),before);self.assertNotEqual(a._source,b._source)
    def test_33_identity_visibility_and_authority(self):
        h=make();a,e,_=sessions(h);self.assertNotIn('truth.hot_k',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.hot_k',e.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',a.describe(instance_id=h.instance_id)['capabilities']);self.assertEqual(call(a,'sample.load',r.SAMPLE_EXAMPLE)['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(r.ActionRejected):h.session('nobody')
        with self.assertRaises(r.ActionRejected):a.read(instance_id=h.instance_id,state_ids=['truth.hot_k'])
    def test_34_request_dedup_owner_and_cancel(self):
        h=make();a,e,b=sessions(h);start=call(a,'acquisition.start');self.assertEqual(start,call(a,'acquisition.start'));self.assertEqual(call(a,'power.set',{'enabled':True})['error']['code'],'REQUEST_CONFLICT');self.assertEqual(call(b,'acquisition.stop')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(b,'power.set',{'enabled':False},'off')['error']['code'],'ACCESS_DENIED');h.step(.1);op=start['operation_id'];reply=a.cancel(op,'cancel');self.assertEqual(reply,a.cancel(op,'cancel'));self.assertEqual(a.poll(op)['status'],'cancelled')
    def test_35_all_role_public_results_hide_selected_provenance(self):
        h=make();a,e,_=sessions(h);op=call(a,'acquisition.start')['operation_id'];h.step(3.)
        for payload in [call(a,'acquisition.result',rid='a'),call(e,'acquisition.result',rid='e'),a.poll(op)]:
            text=json.dumps(payload);self.assertNotIn('tematdb_00009',text);self.assertNotIn('am201386',text);self.assertNotIn('Bi0.3',text)
        self.assertEqual(call(a,'provenance.read',rid='p')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(e,'provenance.read',rid='p')['result']['reference_id'],'tematdb_00009')
    def test_36_result_source_snapshot_and_reload(self):
        h=make();old=finish(h);remove(h);self.assertIsNone(h.invoke('provenance.read')['reference_id']);h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,reference_id='tematdb_00038'));self.assertEqual(h.invoke('acquisition.result'),old);self.assertEqual(h.invoke('provenance.read')['reference_id'],'tematdb_00038')
    def test_37_assumptions_and_unsupported_are_explicit(self):
        h=Checked();self.reject(h,'plant.configure',**dict(r.PLANT_EXAMPLE,accept_engineering=False));h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('geometry.configure',**r.GEOMETRY_EXAMPLE);self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,accept_directional_scalar_proxy=False));self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,accept_engineering_density_heat_capacity=False))
        for k in r.UNSUPPORTED:self.reject(h,k)
        h.register_principal('student');self.assertEqual(call(h.session('student'),'accuracy.certification')['status'],'unsupported')
    def test_38_analysis_failure_and_delta_guards(self):
        h=make();self.reject(h,'analysis.transport',**dict(r.ANALYSIS_EXAMPLE,hot_temperature_k=304.5));self.reject(h,'analysis.transport',**dict(r.ANALYSIS_EXAMPLE,positive_voltage_v=0.));h.invoke('thermal.set_gradient',delta_k=0.);h.step(3.);self.reject(h,'acquisition.start')
    def test_39_metadata_schemas_and_actual_outputs(self):
        h=make();meta=h.describe()
        for key,c in meta['capabilities'].items():self.assertEqual(asset_tool.lint_schema(c['input_schema']),[],key);self.assertEqual(asset_tool.lint_schema(c['output_schema']),[],key);self.assertEqual(asset_tool.validate_value(c['example_arguments'],c['input_schema']),[],key)
        for key,args,result in OBSERVED:self.assertEqual(asset_tool.validate_value(result,meta['capabilities'][key]['output_schema']),[],key)
        for key,value in h.read().items():self.assertEqual(asset_tool.validate_value(value,meta['state_slots'][key]['value_schema']),[],key)
    def test_40_all_implemented_session_entries(self):
        h=Checked();h.register_principal('eval','evaluator');s=h.session('eval');seen=set();plan=[('plant.configure',r.PLANT_EXAMPLE),('geometry.configure',r.GEOMETRY_EXAMPLE),('sample.load',r.SAMPLE_EXAMPLE),('power.set',{'enabled':True}),('sample.set_clamp',{'closed':True}),('sim.step',{'dt_s':2.}),('furnace.set_open',{'opened':False}),('sim.step',{'dt_s':1.8}),('thermal.set_temperature',{'temperature_k':305.,'ramp_rate_k_min':100.}),('thermal.set_gradient',{'delta_k':1.}),('sim.step',{'dt_s':7.}),('sensor.calibrate',r.CALIBRATION_EXAMPLE),('method.configure',r.METHOD_EXAMPLE),('program.configure',r.PROGRAM_EXAMPLE),('program.start',{}),('program.stop',{}),('acquisition.start',{}),('sim.step',{'dt_s':.1}),('acquisition.stop',{}),('acquisition.result',{}),('analysis.transport',r.ANALYSIS_EXAMPLE),('provenance.read',{}),('ledger.read',{}),('thermal.disable',{}),('furnace.set_open',{'opened':True}),('sim.step',{'dt_s':1.8}),('sample.set_clamp',{'closed':False}),('sim.step',{'dt_s':2.}),('sample.remove',{}),('power.set',{'enabled':False})]
        for i,(cap,args) in enumerate(plan):
            out=call(s,cap,args,str(i));self.assertIn(out['status'],('accepted','succeeded'),(cap,out));self.assertEqual(asset_tool.validate_value(out['result'],r.CAPS[cap]['output_schema']),[],cap);seen.add(cap)
        self.assertEqual(seen,set(r.CAPS)-set(r.UNSUPPORTED));self.budget(h)

if __name__=='__main__':unittest.main(verbosity=2)
