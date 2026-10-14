"""CHEM140 finite-dwell complex spectroscopy, actual OSL and conserved active energy."""
import copy,importlib.util,json,math,unittest
from pathlib import Path
import numpy as np
from scripts.asset_runtime import novocontrol_alphat as r
ROOT=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('alpha_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');asset_tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(asset_tool)
OBSERVED=[]
class Checked(r.NovocontrolAlphatRuntime):
    def invoke(self,k,**a):
        before=self.read();out=super().invoke(k,**a);after=self.read();changed={x for x in after if after[x]!=before[x]}
        assert not changed-set(r.CAPS[k]['effects']),(k,changed-set(r.CAPS[k]['effects']))
        assert not asset_tool.validate_value(out,r.CAPS[k]['output_schema']),(k,asset_tool.validate_value(out,r.CAPS[k]['output_schema']))
        OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(out)));return out

def make(plant=None,sample=None,method=None,geometry=None,thermal=None):
    h=Checked();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,**(plant or {})));h.invoke('geometry.configure',**dict(r.GEOMETRY_EXAMPLE,**(geometry or {})));h.invoke('thermal.configure',**dict(r.THERMAL_EXAMPLE,**(thermal or {})));h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,**(sample or {})));h.invoke('power.set',enabled=True);h.invoke('sample.set_clamp',closed=True);h.step(1.);h.invoke('thermal.set_temperature',temperature_k=r.PLANT_EXAMPLE['ambient_k']);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,**(method or {})));return h

def finish(h):
    h.invoke('acquisition.start');remaining=h.expected_duration
    while remaining>0:
        step=min(remaining,86400.);h.step(step);remaining-=step
    return h.invoke('acquisition.result')
def calibrate(h):
    observations={}
    for mode in ['open','short','load']:
        h.invoke('fixture.select_load',mode=mode,load_resistance_ohm=1000.);observations[mode]=finish(h)['rows']
    records=[h.invoke('calibration.solve',frequency_hz=f,open_measured_ohm=observations['open'][i]['raw_impedance_ohm'],short_measured_ohm=observations['short'][i]['raw_impedance_ohm'],load_measured_ohm=observations['load'][i]['raw_impedance_ohm'],load_resistance_ohm=1000.) for i,f in enumerate(h.method['frequencies_hz'])]
    h.invoke('calibration.apply',records=records);h.invoke('fixture.select_load',mode='sample',load_resistance_ohm=1000.);return observations,records

def remove(h):
    if h.phase=='running':h.invoke('acquisition.stop')
    h.invoke('sample.set_clamp',closed=False);h.step(1.);h.invoke('sample.remove')
def sessions(h):
    for n,role in [('student','agent'),('teacher','evaluator'),('other','agent')]:h.register_principal(n,role)
    return h.session('student'),h.session('teacher'),h.session('other')
def call(s,c,a=None,rid='r1'):return s.invoke(instance_id=s._host.instance_id,capability_id=c,arguments=a or {},request_id=rid)
def snapshot(h):return json.dumps(dict(state=h.read(),plant=h.plant,sample=h.sample,geometry=h.geometry,thermal=h.thermal,ledger=h._ledger(),rows=h.rows,records=h.records,revision=h.revision,calibration=h.calibration,method=h.method,x=h.x.tolist(),detector=[r.pack(z) for z in h.detector]),sort_keys=True,allow_nan=False)

class AlphaTests(unittest.TestCase):
    def budget(self,h,tol=1e-8):
        for k,v in h.invoke('ledger.read').items():
            if 'error' in k:self.assertLess(abs(v),tol,(k,v))
    def reject(self,h,k,**a):
        before=snapshot(h)
        with self.assertRaises(r.ActionRejected):h.invoke(k,**a)
        self.assertEqual(snapshot(h),before)
    def test_01_target_and_clamp_do_not_teleport(self):
        h=make();before=h.x.copy();h.invoke('thermal.set_temperature',temperature_k=295.2);np.testing.assert_array_equal(h.x,before);h.invoke('sample.set_clamp',closed=False);self.assertEqual(h.clamp,0.);h.step(.5);self.assertAlmostEqual(h.clamp,.0125,12);self.assertGreater(h._temperature(0),293.2);self.budget(h)
    def test_02_read_describe_zero_are_pure(self):
        h=make();a,e,_=sessions(h);before=snapshot(h)
        for _ in range(2):a.read(instance_id=h.instance_id);e.describe(asset_type_id=r.ASSET_TYPE_ID);h.step(0.)
        self.assertEqual(snapshot(h),before)
    def test_03_real_static_library_is_not_fake_spectra(self):
        lib=r.library();self.assertGreaterEqual(len(lib),50)
        for row in lib.values():self.assertGreater(row['relative_permittivity_static'],1.);self.assertTrue(row['source']);self.assertTrue(row['evidence']);self.assertEqual(row['curves'],[]);self.assertIsNone(row['relaxation_time_s'])
        row=lib['water_crc_7732_18_5'];self.assertEqual(row['relative_permittivity_static'],80.1);self.assertEqual(row['reference_temperature_K'],293.2)
    def test_04_c0_and_debye_series_rc_identity(self):
        diameter=.02;thickness=.001;c0=r.vacuum_capacitance(diameter,thickness);self.assertAlmostEqual(c0,r.EPS0*math.pi*.01**2/.001,22);f=1000.;w=2*math.pi*f;delta=80.1-4.9;tau=1e-4;branch=1/(tau/(c0*delta)+1/(1j*w*c0*delta));expected=1j*w*c0*4.9+branch+5e-6*math.pi*.01**2/.001;actual=1j*w*c0*r.dielectric_response(80.1,4.9,tau,5e-6,f);self.assertAlmostEqual(abs(actual-expected),0.,20)
    def test_05_inverse_uses_external_complex_vi_only(self):
        h=Checked();out=h.invoke('analysis.dielectric',**r.ANALYSIS_EXAMPLE);c0=r.vacuum_capacitance(.02,.001);self.assertAlmostEqual(out['epsilon_real'],1e-6/(2*math.pi*1000*c0),9);self.assertAlmostEqual(out['tan_delta'],.01,12);self.assertIsNone(h.sample)
    def test_06_source_fifty_ohm_loading(self):
        h=make(method={'frequencies_hz':[4e7],'source_voltage_rms_v':1.});c=h._circuit(f=4e7);self.assertLess(abs(c['voltage']),.5);self.assertAlmostEqual(abs(complex(1.)-c['voltage']-50*c['current']),0.,12);self.assertEqual(r.SOURCE_RESISTANCE_OHM,50.)
    def test_07_rms_active_power_has_no_half_factor(self):
        h=make();c=h._circuit(f=1000.);generator,source,parasitic,reference,sample=c['power'];self.assertAlmostEqual(generator,(.3*c['current'].conjugate()).real,16);self.assertAlmostEqual(generator,source+parasitic+reference+sample,16);self.assertAlmostEqual(source,abs(c['current'])**2*50.,20)
    def test_08_real_frequency_wait_and_point_order(self):
        h=make();h.invoke('acquisition.start');self.assertEqual(h.rows,[]);h.step(.1);self.assertEqual(h.rows,[]);h.step(.010000001);self.assertEqual(len(h.rows),1);self.assertEqual(h.rows[0]['frequency_hz'],10.);h.step(h.expected_duration-h.elapsed);self.assertEqual(h.phase,'succeeded');self.assertEqual([row['frequency_hz'] for row in h.rows],h.method['frequencies_hz']);self.budget(h)
    def test_09_minimum_frequency_requires_days_of_simulated_time(self):
        h=make(method={'frequencies_hz':[3e-6],'settling_s':0.});h.invoke('acquisition.start');self.assertGreaterEqual(h.expected_duration,1/3e-6);h.step(1000.);self.assertEqual(h.rows,[]);remaining=h.expected_duration-h.elapsed
        while remaining>0:
            dt=min(86400.,remaining);h.step(dt);remaining-=dt
        self.assertEqual(h.phase,'succeeded');self.assertEqual(len(h.rows),1);self.assertGreater(h.rows[0]['point_duration_s'],333333.);self.budget(h,1e-7)
    def test_10_no_fast_option_rate(self):
        h=make(method={'frequencies_hz':[1e6,2e6],'settling_s':0.});out=finish(h);self.assertTrue(all(row['point_duration_s']>=1/10.5 for row in out['rows']));self.assertGreater(h.expected_duration,.19)
    def test_11_voltage_boundary_is_conservative(self):
        h=make();h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,frequencies_hz=[9999999.],source_voltage_rms_v=3.));self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,frequencies_hz=[1e7],source_voltage_rms_v=1.00001));self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,frequencies_hz=[4e7],source_voltage_rms_v=3.));h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,frequencies_hz=[1e7,4e7],source_voltage_rms_v=1.));self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,frequencies_hz=[4.01e7]))
    def test_12_current_converter_overflow_is_not_clipping(self):
        h=make(method={'frequencies_hz':[1000.],'source_voltage_rms_v':3.});h.invoke('fixture.select_load',mode='short',load_resistance_ohm=1000.);h.invoke('acquisition.start');self.assertGreater(abs(h._circuit()['current']),.04);self.assertEqual(h._observed(),(None,None));h.step(h.expected_duration);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'CURRENT_CONVERTER_OVERFLOW');self.assertIsNone(h.rows[0]['current_rms']);self.assertGreater(h.electrical_energy[1],0);self.budget(h)
    def test_13_zero_excitation_has_no_fabricated_impedance(self):
        h=make(method={'source_voltage_rms_v':0.});self.reject(h,'acquisition.start');self.assertEqual(h.electrical_energy.sum(),0.)
    def test_14_actual_open_short_load_observations(self):
        h=make();observations,records=calibrate(h);self.assertEqual(len(records),5);self.assertGreater(abs(r.unpack(observations['open'][2]['raw_impedance_ohm'])),1e6);self.assertLess(abs(r.unpack(observations['short'][2]['raw_impedance_ohm'])),1.);self.assertAlmostEqual(abs(r.unpack(observations['load'][2]['raw_impedance_ohm'])),1000.2,places=2);self.assertGreater(h._ledger()['reference_load_heat_j'],0);self.budget(h)
    def test_15_external_osl_recovery_with_complex_gains(self):
        h=make(plant={'voltage_gain_magnitude':1.12,'voltage_gain_phase_rad':.015,'current_gain_magnitude':.93,'current_gain_phase_rad':-.011});calibrate(h);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,require_calibration=True));o=finish(h)
        for row in o['rows']:
            expected=r.dielectric_response(80.1,4.9,1e-11,5e-6,row['frequency_hz']);self.assertTrue(row['calibration_applied']);self.assertLess(abs(row['epsilon_real']-expected.real),.01);self.assertLess(abs(row['epsilon_loss']+expected.imag)/max(abs(expected.imag),1.),1e-6)
        self.budget(h)
    def test_16_calibration_really_changes_observed_inverse(self):
        h=make();uncorrected=finish(h)['rows'][-1]['epsilon_real'];calibrate(h);corrected=finish(h)['rows'][-1]['epsilon_real'];self.assertGreater(abs(uncorrected-80.1),.5);self.assertLess(abs(corrected-80.1),1e-5)
    def test_17_missing_frequency_calibration_is_not_extrapolated(self):
        h=make();calibrate(h);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,frequencies_hz=[123.],require_calibration=True));self.reject(h,'acquisition.start');h.invoke('calibration.clear');self.assertFalse(h.calibration)
    def test_18_osl_external_solver_never_reads_hidden_state(self):
        h=Checked();zseries=.5+2j;yp=1e-5+2e-5j;gain=1.2+.1j;raw=lambda z:gain*(zseries+1/(1/z+yp));zo=gain*(zseries+1/yp);zs=gain*zseries;zl=raw(1000.);fit=h.invoke('calibration.solve',frequency_hz=1000.,open_measured_ohm=r.pack(zo),short_measured_ohm=r.pack(zs),load_measured_ohm=r.pack(zl),load_resistance_ohm=1000.);unknown=1234.-987j;self.assertAlmostEqual(abs(r.correct_impedance(raw(unknown),fit)-unknown),0.,9);self.assertIsNone(h.plant)
    def test_19_loss_conductivity_is_not_just_dc(self):
        h=make(sample={'relaxation_time_s':1e-4,'dc_conductivity_s_m':0.},method={'frequencies_hz':[1000.]});calibrate(h);o=finish(h);row=o['rows'][0];self.assertGreater(row['conductivity_real_s_m'],0.);self.assertEqual(h.sample['dc_conductivity_s_m'],0.);self.assertAlmostEqual(row['conductivity_real_s_m'],2*math.pi*1000*r.EPS0*row['epsilon_loss'],15)
    def test_20_zero_loss_ideal_debye_constant_passive(self):
        e=r.dielectric_response(80.1,80.1,1e-4,0.,1000.);self.assertEqual(e.imag,0.);self.assertEqual(e.real,80.1)
    def test_21_sample_loss_and_thermal_energy_balance(self):
        h=make(method={'frequencies_hz':[1000.],'source_voltage_rms_v':1.,'cycles_per_point':1000});h.invoke('thermal.disable');T=h._temperature(1);finish(h);self.assertGreater(h._temperature(1),T);self.assertGreater(h._ledger()['sample_dielectric_heat_j'],0);self.budget(h)
    def test_22_external_thermal_system_is_bounded_and_time_driven(self):
        h=make(thermal={'heating_limit_w':.1});h.invoke('thermal.set_temperature',temperature_k=303.15);before=h.x[2];h.step(2.);self.assertAlmostEqual(h.x[2]-before,.2,8);self.budget(h);self.reject(h,'thermal.set_temperature',temperature_k=400.)
    def test_23_loading_unloading_sensible_mass_volume(self):
        h=make(plant={'ambient_k':293.15});expected_mass=1000*math.pi*.01**2*.001;self.assertAlmostEqual(h._mass(),expected_mass,15);self.assertAlmostEqual(h.loaded_sensible,expected_mass*4180*.05,10);self.budget(h);remove(h);self.assertEqual(h._volume(),0);self.assertAlmostEqual(h.mass_removed,expected_mass,15);self.assertGreater(h.removed_sensible,0);self.budget(h)
    def test_24_loaded_geometry_is_immutable(self):
        h=make();self.reject(h,'geometry.configure',diameter_m=.03,thickness_m=.001);remove(h);h.invoke('geometry.configure',diameter_m=.03,thickness_m=.001);h.invoke('sample.load',**r.SAMPLE_EXAMPLE);self.assertAlmostEqual(h._volume(),math.pi*.015**2*.001,18);self.budget(h)
    def test_25_open_clamp_has_external_heat_boundary(self):
        h=make();h.invoke('thermal.set_temperature',temperature_k=298.2);h.invoke('sample.set_clamp',closed=False);Ts=h._temperature(1);h.step(1.);self.assertEqual(h._temperature(1),Ts);self.assertGreater(h._temperature(0),Ts);self.budget(h)
    def test_26_temperature_invalidates_static_reference(self):
        h=make();h.invoke('thermal.set_temperature',temperature_k=298.2);h.step(20.);self.assertGreater(h._temperature(1),294.);self.reject(h,'acquisition.start');self.budget(h)
    def test_27_cancel_freezes_complete_points_and_cuts_source(self):
        h=make();h.invoke('acquisition.start');h.step(.15);h.invoke('acquisition.stop');rows=copy.deepcopy(h.rows);energy=h.electrical_energy.copy();self.assertEqual(len(rows),1);h.step(1.);self.assertEqual(h.rows,rows);np.testing.assert_array_equal(h.electrical_energy,energy);self.assertEqual(h.read()['excitation.source_voltage_rms_v'],0.);self.budget(h)
    def test_28_power_off_retains_dissipated_heat(self):
        h=make();h.invoke('acquisition.start');h.step(.05);h.invoke('power.set',enabled=False);energy=h.electrical_energy.copy();thermal=h.x.copy();h.step(1.);self.assertEqual(h.phase,'cancelled');np.testing.assert_array_equal(h.electrical_energy,energy);self.assertEqual(h.x[2],thermal[2]);self.assertEqual(h.read()['measured.current_rms_a'],None);self.budget(h)
    def test_29_sixty_hz_partition_preserves_clock_and_rows(self):
        a=make();b=make()
        for h in (a,b):h.invoke('acquisition.start')
        a.step(1.)
        for _ in range(60):b.step(1/60)
        self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.rows,b.rows);np.testing.assert_allclose(a.x,b.x,rtol=1e-11,atol=1e-15);np.testing.assert_allclose(a.electrical_energy,b.electrical_energy,rtol=1e-13,atol=1e-20);self.budget(a);self.budget(b)
    def test_30_invalid_arguments_are_atomic(self):
        h=make()
        for dt in [True,-1.,float('inf'),float('nan')]:
            before=snapshot(h)
            with self.assertRaises(r.ActionRejected):h.step(dt)
            self.assertEqual(snapshot(h),before)
        self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,cycles_per_point=1.5));self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,frequencies_hz=[100.,100.]));self.reject(h,'thermal.set_temperature',temperature_k=True);self.reject(h,'sample.set_clamp',closed=True,extra=1)
    def test_31_busy_and_clamp_preconditions(self):
        h=make();h.invoke('acquisition.start');self.reject(h,'sample.set_clamp',closed=False);self.reject(h,'fixture.select_load',mode='short',load_resistance_ohm=1000.);self.reject(h,'method.configure',**r.METHOD_EXAMPLE);self.reject(h,'calibration.clear');h.invoke('acquisition.stop');self.reject(h,'sample.remove')
    def test_32_double_instances_are_independent(self):
        a=make();b=make(sample={'reference_id':'ethanol_crc_64_17_5'});before=snapshot(b);finish(a);self.assertEqual(snapshot(b),before);calibrate(b);o=finish(b);self.assertLess(abs(o['rows'][-1]['epsilon_real']-25.3),1e-5)
    def test_33_identity_and_hidden_state_filtering(self):
        h=make();a,e,_=sessions(h);self.assertNotIn('truth.dc_conductivity_s_m',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.sample_k',e.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.load',a.describe(instance_id=h.instance_id)['capabilities']);self.assertEqual(call(a,'sample.load',r.SAMPLE_EXAMPLE)['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(r.ActionRejected):h.session('unknown')
        with self.assertRaises(r.ActionRejected):a.read(instance_id=h.instance_id,state_ids=['truth.sample_k'])
    def test_34_request_dedup_and_owner_cancellation(self):
        h=make();a,e,b=sessions(h);start=call(a,'acquisition.start');self.assertEqual(start,call(a,'acquisition.start'));self.assertEqual(call(a,'power.set',{'enabled':True})['error']['code'],'REQUEST_CONFLICT');self.assertEqual(call(b,'acquisition.stop')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(b,'power.set',{'enabled':False},'off')['error']['code'],'ACCESS_DENIED');h.step(.15);op=start['operation_id'];reply=a.cancel(op,'cancel');self.assertEqual(reply,a.cancel(op,'cancel'));self.assertEqual(a.poll(op)['status'],'cancelled')
    def test_35_public_results_redacted_even_for_evaluator_forward(self):
        h=make();a,e,_=sessions(h);op=call(a,'acquisition.start')['operation_id'];h.step(h.expected_duration)
        for payload in [call(a,'acquisition.result',rid='a'),call(e,'acquisition.result',rid='e'),a.poll(op)]:
            text=json.dumps(payload);self.assertNotIn('water_crc',text);self.assertNotIn('7732',text);self.assertNotIn('original_row',text)
        self.assertEqual(call(a,'provenance.read',rid='p')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(e,'provenance.read',rid='p')['result']['reference_id'],'water_crc_7732_18_5')
    def test_36_source_snapshot_survives_new_sample(self):
        h=make();old=finish(h);remove(h);self.assertIsNone(h.invoke('provenance.read')['reference_id']);h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,reference_id='ethanol_crc_64_17_5'));self.assertEqual(h.invoke('acquisition.result'),old)
    def test_37_assumptions_and_unsupported_are_explicit(self):
        h=Checked();self.reject(h,'plant.configure',**dict(r.PLANT_EXAMPLE,accept_engineering=False));h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('geometry.configure',**r.GEOMETRY_EXAMPLE);self.reject(h,'thermal.configure',**dict(r.THERMAL_EXAMPLE,accept_external_engineering_system=False));h.invoke('thermal.configure',**r.THERMAL_EXAMPLE);self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,accept_spectrum_assumption=False));self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,accept_thermal_assumption=False))
        for c in r.UNSUPPORTED:self.reject(h,c)
    def test_38_degenerate_calibration_and_analysis_rejected(self):
        h=make();same=dict(r.OSL_EXAMPLE,load_measured_ohm=r.OSL_EXAMPLE['short_measured_ohm']);self.reject(h,'calibration.solve',**same);self.reject(h,'analysis.dielectric',**dict(r.ANALYSIS_EXAMPLE,current_rms=r.pack(0.)));self.reject(h,'analysis.dielectric',**dict(r.ANALYSIS_EXAMPLE,current_rms=r.pack(-1j)));record=r.solve_osl(**r.OSL_EXAMPLE);self.reject(h,'calibration.apply',records=[record,record])
    def test_39_contract_schemas_and_actual_outputs(self):
        h=make();meta=h.describe()
        for key,c in meta['capabilities'].items():self.assertEqual(asset_tool.lint_schema(c['input_schema']),[],key);self.assertEqual(asset_tool.lint_schema(c['output_schema']),[],key);self.assertEqual(asset_tool.validate_value(c['example_arguments'],c['input_schema']),[],key)
        for key,args,result in OBSERVED:self.assertEqual(asset_tool.validate_value(result,meta['capabilities'][key]['output_schema']),[],key)
        for key,value in h.read().items():self.assertEqual(asset_tool.validate_value(value,meta['state_slots'][key]['value_schema']),[],key)
    def test_40_all_implemented_session_entries(self):
        h=Checked();h.register_principal('eval','evaluator');s=h.session('eval');seen=set();plan=[('plant.configure',r.PLANT_EXAMPLE),('geometry.configure',r.GEOMETRY_EXAMPLE),('thermal.configure',r.THERMAL_EXAMPLE),('sample.load',r.SAMPLE_EXAMPLE),('power.set',{'enabled':True}),('sample.set_clamp',{'closed':True}),('sim.step',{'dt_s':1.}),('thermal.set_temperature',{'temperature_k':293.2}),('method.configure',r.METHOD_EXAMPLE),('fixture.select_load',{'mode':'sample','load_resistance_ohm':1000.}),('calibration.solve',r.OSL_EXAMPLE),('calibration.apply',{'records':[r.solve_osl(**r.OSL_EXAMPLE)]}),('calibration.clear',{}),('acquisition.start',{}),('sim.step',{'dt_s':.15}),('acquisition.stop',{}),('acquisition.result',{}),('analysis.dielectric',r.ANALYSIS_EXAMPLE),('provenance.read',{}),('ledger.read',{}),('thermal.disable',{}),('sample.set_clamp',{'closed':False}),('sim.step',{'dt_s':1.}),('sample.remove',{}),('power.set',{'enabled':False})]
        for i,(cap,args) in enumerate(plan):
            reply=call(s,cap,args,str(i));self.assertIn(reply['status'],('succeeded','accepted'),(cap,reply));self.assertEqual(asset_tool.validate_value(reply['result'],r.CAPS[cap]['output_schema']),[],cap);seen.add(cap)
        self.assertEqual(seen,set(r.CAPS)-set(r.UNSUPPORTED));self.budget(h)


    def test_41_capacitance_lower_range_is_unavailable(self):
        record=next(v for v in r.library().values() if v['relative_permittivity_static']<2.5 and v['reference_temperature_K']==293.2)
        h=make(plant={'fixture_parallel_capacitance_f':1e-18,'fixture_parallel_conductance_s':0.,'fixture_series_resistance_ohm':0.,'fixture_series_inductance_h':0.},sample={'reference_id':record['id'],'epsilon_infinity':1.,'relaxation_time_s':1e-15,'dc_conductivity_s_m':0.},geometry={'diameter_m':.001,'thickness_m':.02},method={'frequencies_hz':[1000.]});out=finish(h);self.assertEqual(out['phase'],'failed');self.assertEqual(out['error_code'],'MEASUREMENT_RANGE');self.assertIsNone(out['rows'][0]['epsilon_real']);self.budget(h)
    def test_42_impedance_upper_range_after_real_long_wait(self):
        h=make(plant={'fixture_parallel_capacitance_f':1e-18,'fixture_parallel_conductance_s':0.},sample={'dc_conductivity_s_m':0.},method={'frequencies_hz':[3e-6],'settling_s':0.});out=finish(h);self.assertEqual(out['phase'],'failed');self.assertEqual(out['error_code'],'MEASUREMENT_RANGE');self.assertGreater(abs(r.unpack(out['rows'][0]['impedance_ohm'])),2e14);self.assertGreater(out['elapsed_s'],333333.);self.budget(h)

if __name__=='__main__':unittest.main(verbosity=2)
