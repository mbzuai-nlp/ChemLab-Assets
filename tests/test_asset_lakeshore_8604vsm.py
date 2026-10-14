"""Independent physical/temporal/protocol acceptance tests for CHEM143."""
import copy,json,math,unittest
import numpy as np
from scripts.asset_runtime import lakeshore_8604vsm as m

class VsmTests(unittest.TestCase):
    def make(self,standard=False,sample=None,plant=None,lower=True):
        r=m.LakeShore8604VsmRuntime('vsm_test');r.invoke('plant.configure',**(plant or m.PLANT_EXAMPLE));r.invoke('coil.configure',**m.COIL_EXAMPLE)
        if sample is not False:r.invoke('standard.load' if standard else 'sample.load',**(m.STANDARD_EXAMPLE if standard else sample or m.SAMPLE_EXAMPLE))
        r.invoke('power.set',enabled=True)
        if lower:r.invoke('sample.set_lift',lift_m=0.);r.step(3.5);r.invoke('vibration.configure',**m.VIBRATION_EXAMPLE)
        return r
    def finish(self,r,dt=20):
        while r.phase=='running':r.step(dt)
        return r._result()
    def loop(self,r,method=None):r.invoke('method.configure',**(method or m.METHOD_EXAMPLE));r.invoke('acquisition.start');return self.finish(r)
    def zero_raise(self,r):
        r.invoke('field.set_target',field_t=0.,ramp_rate_t_s=1.);r.step(2.4);r.invoke('vibration.configure',**dict(m.VIBRATION_EXAMPLE,enabled=False));r.invoke('sample.set_lift',lift_m=.35);r.step(3.5)
    def session(self,r,name='evaluator',role='evaluator'):r.register_principal(name,role);return r.session(name)
    def call(self,s,c,a,rid=None):return s.invoke(instance_id=s._host.instance_id,capability_id=c,arguments=copy.deepcopy(a),request_id=rid or c+str(s._host.revision))
    def test_01_field_setpoint_does_not_advance_physics(self):
        r=self.make(sample=False);t=r.time_s;r.invoke('field.set_target',field_t=1.,ramp_rate_t_s=1.);self.assertEqual(r._pos('field'),0);self.assertEqual(r.time_s,t);r.step(.3);self.assertAlmostEqual(r._pos('field'),.3,9);r.step(.7);self.assertAlmostEqual(r._pos('field'),1.,9)
    def test_02_field_resolution_and_standard_gap_limit(self):
        r=self.make(sample=False);r.invoke('field.set_target',field_t=.123456789,ramp_rate_t_s=1.);self.assertAlmostEqual(r.axes['field']['target'],.1234568,15)
        with self.assertRaises(m.ActionRejected):r.invoke('field.set_target',field_t=2.61,ramp_rate_t_s=1.)
    def test_03_real_10second_integration_window(self):
        r=self.make();r.invoke('method.configure',**dict(m.METHOD_EXAMPLE,field_points_t=[0.],averaging_time_s=10.,settling_time_s=0.));r.invoke('acquisition.start');r.step(9.99);self.assertEqual(len(r.rows),0);r.step(.01);self.assertEqual(len(r.rows),1);self.assertAlmostEqual(r.rows[0]['window_end_s']-r.rows[0]['window_start_s'],10.)
    def test_04_interval_mean_is_not_endpoint(self):
        p=dict(m.PLANT_EXAMPLE,detector_tau_s=1.,voltage_offset_v=0.,voltage_gain=1.);r=self.make(plant=p);r.invoke('method.configure',**dict(m.METHOD_EXAMPLE,field_points_t=[0.],averaging_time_s=.1,settling_time_s=0.));r.invoke('acquisition.start');r.step(.1);target=r._sensitivity(True)*r._moment();expected=target*(1-(1-math.exp(-.1))/.1);self.assertAlmostEqual(r.rows[0]['in_phase_voltage_v'],expected,14);self.assertNotAlmostEqual(r.rows[0]['in_phase_voltage_v'],r.sensor,10)
    def test_05_zero_and_small_signal_are_allowed(self):
        s=dict(m.SAMPLE_EXAMPLE,mass_kg=0.);p=dict(m.PLANT_EXAMPLE,voltage_offset_v=0.);r=self.make(sample=s,plant=p);res=self.loop(r);self.assertEqual(res['phase'],'succeeded');self.assertTrue(all(row['moment_a_m2']==0 for row in r.rows));self.assertTrue(all(row['quality']=='below_documented_dynamic_range' for row in r.rows))
    def test_06_actual_range_overflow_not_clipped_success(self):
        r=self.make();r.invoke('coil.configure',**dict(m.COIL_EXAMPLE,moment_range_a_m2=1e-5));res=self.loop(r);self.assertEqual(res['phase'],'failed');self.assertIsNone(r.rows[0]['moment_a_m2']);self.assertEqual(r.rows[0]['quality'],'overflow');self.assertGreater(r.ledger()['coil_joule_j'],0)
    def test_07_measured_library_anchor_units(self):
        r=self.make();record=m.library()['kuleuven_cf_300k'];self.assertAlmostEqual(sum(r.amplitudes),m.SAMPLE_EXAMPLE['mass_kg']*75.48749849);self.assertGreater(record['measured_specific_moment_max_abs_applied_field_t'],2.37)
    def test_08_more_than_50_real_references_not_fake_loops(self):
        records=m.library();chi=[x for x in records.values() if x.get('molar_susceptibility_m3_mol') is not None];self.assertGreaterEqual(len(chi),50);self.assertTrue(all(x.get('source') and x.get('evidence') for x in records.values()));self.assertGreaterEqual(len([x for x in records.values() if x.get('measured_hysteresis_sequence')]),5)
    def test_09_crc_molar_chi_independent_si_conversion(self):
        sample=dict(m.SAMPLE_EXAMPLE,reference_id='crc_cuso4_5h2o',magnetic_model='linear_susceptibility');r=self.make(sample=sample);rec=m.library()['crc_cuso4_5h2o'];expected=sample['mass_kg']/rec['molar_mass_kg_mol']*(1460*4*math.pi*1e-12)*.1/(4*math.pi*1e-7);self.assertAlmostEqual(r._moment(.1),expected,16)
    def test_10_explicit_proxy_required_when_real_hysteresis_absent(self):
        r=self.make(sample=False,lower=False);s=dict(m.SAMPLE_EXAMPLE,reference_id='crc_cuso4_5h2o')
        with self.assertRaisesRegex(m.ActionRejected,'EXPLICIT_PROXY'):r.invoke('sample.load',**s)
    def test_11_temperature_single_point_tolerance_and_wide_domain(self):
        r=self.make();self.loop(r);self.assertGreater(r.sample_k,300.);self.assertTrue(r._temperature_valid());r.sample_k=301.;self.assertFalse(r._temperature_valid());self.assertIsNone(r.read()['moment.indicated_a_m2'])
        s=dict(m.SAMPLE_EXAMPLE,reference_id='crc_cuso4_5h2o',magnetic_model='linear_susceptibility');q=self.make(sample=s);q.sample_k=300.01;self.assertFalse(q._temperature_valid())
    def test_12_temperature_zero_tolerance_fails_observation_after_heat(self):
        r=self.make(sample=dict(m.SAMPLE_EXAMPLE,temperature_tolerance_k=0.));res=self.loop(r);self.assertEqual(res['error'],'REFERENCE_TEMPERATURE_RANGE');self.assertIsNone(res['rows'][0]['moment_a_m2'])
    def test_13_hysterons_switch_at_actual_field_not_setpoint(self):
        r=self.make();r.invoke('field.set_target',field_t=.1,ramp_rate_t_s=1.);self.assertTrue(all(x['state']==-1 for x in r.relays));r.step(.019);self.assertTrue(all(x['state']==-1 for x in r.relays));r.step(.002);self.assertEqual([x['state'] for x in r.relays],[1,-1,-1,-1,-1])
    def test_14_closed_cycle_loss_has_independent_rectangle_area(self):
        r=self.make();r.invoke('field.set_target',field_t=.2,ramp_rate_t_s=1.);r.step(.2);initial=r.book['hysteresis_heat_j']
        for B in [-.2,.2]:r.invoke('field.set_target',field_t=B,ramp_rate_t_s=1.);r.step(.4)
        expected=sum(4*a*x['coercive_field_t'] for a,x in zip(r.amplitudes,r.relays));self.assertAlmostEqual(r.book['hysteresis_heat_j']-initial,expected,12);self.assertLess(abs(r.ledger()['energy_residual_j']),1e-7)
    def test_15_biased_relay_stores_energy_separately_from_heat(self):
        s=dict(m.SAMPLE_EXAMPLE,relays=[dict(coercive_field_t=.05,bias_field_t=.01,weight=1.,initial_sign=-1)]);r=self.make(sample=s);r.invoke('field.set_target',field_t=.1,ramp_rate_t_s=1.);r.step(.1);amp=sum(r.amplitudes);self.assertAlmostEqual(r.book['hysteresis_heat_j'],.1*amp,12);self.assertAlmostEqual(r._magnetic_energy(),.01*amp,12);self.assertLess(abs(r.ledger()['energy_residual_j']),1e-7)
    def test_16_inductor_and_joule_integrals_for_linear_ramp(self):
        r=self.make(sample=False);p=r.plant;r.invoke('field.set_target',field_t=1.,ramp_rate_t_s=1.);r.step(1.);self.assertAlmostEqual(r.ledger()['coil_stored_j'],.5*p['coil_inductance_h']/p['field_per_current_t_a']**2);self.assertAlmostEqual(r.book['coil_joule_j'],p['coil_resistance_ohm']/p['field_per_current_t_a']**2/3);self.assertLess(abs(r.ledger()['energy_residual_j']),1e-7)
    def test_17_magnet_passive_thermal_matches_independent_integral(self):
        from scipy.integrate import quad
        r=self.make(sample=False);p=r.plant;r.invoke('field.set_target',field_t=1.,ramp_rate_t_s=1.);r.step(1.);expected=quad(lambda t:p['coil_resistance_ohm']/p['field_per_current_t_a']**2*t*t*math.exp(-(1-t)*p['magnet_cooling_conductance_w_k']/p['magnet_thermal_capacity_j_k']),0,1)[0]/p['magnet_thermal_capacity_j_k'];self.assertAlmostEqual(r.magnet_k-p['ambient_k'],expected,12)
    def test_18_controller_full_envelope_rejects_impossible_source(self):
        for p in [dict(m.PLANT_EXAMPLE,field_per_current_t_a=.001),dict(m.PLANT_EXAMPLE,coil_resistance_ohm=10),dict(m.PLANT_EXAMPLE,coil_inductance_h=10)]:
            r=m.LakeShore8604VsmRuntime()
            with self.assertRaisesRegex(m.ActionRejected,'POWER_SUPPLY'):r.invoke('plant.configure',**p)
            self.assertIsNone(r.plant)
    def test_19_mass_volume_and_loaded_sensible_energy(self):
        r=self.make(sample=False,lower=False);s=dict(m.SAMPLE_EXAMPLE,initial_temperature_k=299.8);r.invoke('sample.load',**s);expected=s['mass_kg']*s['specific_heat_capacity_j_kg_k']*(299.8-r.plant['ambient_k']);self.assertAlmostEqual(r.ledger()['sample_sensible_loaded_j'],expected);r.step(2);r.invoke('sample.remove');d=r.ledger();self.assertAlmostEqual(d['mass_residual_kg'],0);self.assertAlmostEqual(d['volume_residual_m3'],0);self.assertLess(abs(d['energy_residual_j']),1e-7)
    def test_20_sample_removal_preserves_remaining_local_heat(self):
        r=self.make();self.loop(r);self.zero_raise(r);t=r.sample_k;r.invoke('sample.remove');self.assertEqual(r.sample_k,t);self.assertAlmostEqual(r.ledger()['sample_thermal_j'],r.plant['sample_local_capacity_j_k']*(t-300));self.assertLess(abs(r.ledger()['energy_residual_j']),1e-7)
    def test_21_cancel_keeps_magnetic_state_and_continued_coil_heat(self):
        r=self.make();r.invoke('method.configure',**m.METHOD_EXAMPLE);r.invoke('acquisition.start');r.step(.5);r.invoke('acquisition.stop');rows=copy.deepcopy(r.rows);B=r._pos('field');states=[x['state'] for x in r.relays];q=r.book['coil_joule_j'];r.step(2);self.assertEqual(rows,r.rows);self.assertEqual(B,r._pos('field'));self.assertEqual(states,[x['state'] for x in r.relays]);self.assertGreater(r.book['coil_joule_j'],q)
    def test_22_zero_field_poweroff_guard_and_display(self):
        r=self.make();r.invoke('field.set_target',field_t=.1,ramp_rate_t_s=1);r.step(.1)
        with self.assertRaisesRegex(m.ActionRejected,'ZERO_FIELD'):r.invoke('power.set',enabled=False)
        self.assertTrue(r.power);r.invoke('field.set_target',field_t=0.,ramp_rate_t_s=1);r.step(.1);r.invoke('power.set',enabled=False);self.assertIsNone(r.read()['moment.indicated_a_m2']);self.assertIsNone(r.read()['field.indicated_t'])
    def test_23_motion_finite_lift_rotation_and_storage(self):
        r=self.make(lower=False);r.invoke('sample.set_lift',lift_m=0.);r.step(.2);self.assertAlmostEqual(r._pos('lift'),.33);r.step(3.3);r.invoke('sample.set_angle',angle_rad=7.);r.invoke('storage.set_open',opened=True);r.step(.2);self.assertAlmostEqual(r._pos('angle'),.2,6);self.assertAlmostEqual(r._pos('drawer'),.02);r.step(10);self.assertGreater(r._pos('angle'),2*math.pi);self.assertAlmostEqual(r.read()['sample.orientation_rad'],math.atan2(math.sin(r._pos('angle')),math.cos(r._pos('angle'))))
    def test_24_rotation_projection_at_zero_field(self):
        r=self.make();r.invoke('vibration.configure',**dict(m.VIBRATION_EXAMPLE,enabled=False));r.invoke('sample.set_angle',angle_rad=math.pi/2);r.step(2);self.assertAlmostEqual(r._moment(),0.,13)
    def test_25_vibration_strobe_exact_host_phase(self):
        r=self.make();r.step(.203);expected=.0005*math.sin(2*math.pi*82*r.time_s);self.assertAlmostEqual(r.read()['sample.vibration_z_m'],expected,15)
    def test_26_loading_and_motion_interlocks(self):
        r=self.make()
        for c,a in [('sample.remove',{}),('sample.set_lift',{'lift_m':.35}),('sample.set_angle',{'angle_rad':1.})]:
            before=r.read('evaluator')
            with self.assertRaises(m.ActionRejected):r.invoke(c,**a)
            self.assertEqual(before,r.read('evaluator'))
    def test_27_real_calibration_uses_external_observed_voltage(self):
        r=self.make(standard=True);r.invoke('calibration.start',reference_moment_a_m2=.00347,field_t=m.STANDARD_EXAMPLE['reference_field_t'],averaging_time_s=1.);res=self.finish(r);v1,v2=[x['in_phase_voltage_v'] for x in res['rows']];self.assertAlmostEqual(r.calibration_gain,2*.00347*r._sensitivity()/(v1-v2));self.assertAlmostEqual(r.calibration_gain,1/1.03,5);self.assertEqual(r.calibration_offset,(v1+v2)/2)
    def test_28_wrong_external_standard_changes_gain_without_hidden_correction(self):
        r=self.make(standard=True);r.invoke('calibration.start',reference_moment_a_m2=.00694,field_t=m.STANDARD_EXAMPLE['reference_field_t'],averaging_time_s=1.);self.finish(r);self.assertAlmostEqual(r.calibration_gain,2/1.03,5)
    def test_29_coil_or_vibration_settings_invalidate_calibration(self):
        r=self.make();r.calibration_valid=True;r.invoke('coil.configure',**m.COIL_EXAMPLE);self.assertFalse(r.calibration_valid);r.calibration_valid=True;r.invoke('vibration.configure',**dict(m.VIBRATION_EXAMPLE,frequency_hz=83));self.assertFalse(r.calibration_valid)
    def test_30_external_loop_crossings_and_units(self):
        rows=copy.deepcopy(m.CAPS['analysis.loop']['example_arguments']['rows']);res=m.analyze_loop(rows);self.assertTrue(all(abs(x['field_t'])<1e-12 for x in res['coercive_crossings']));self.assertTrue(all(abs(x['external_h_a_m'])<1e-6 for x in res['coercive_crossings']));self.assertAlmostEqual(m.infer_moment(.002,2.,.5,0.),.0005)
    def test_31_forc_actual_paths_and_local_derivative(self):
        r=self.make();r.invoke('forc.configure',**m.FORC_EXAMPLE);r.invoke('acquisition.start');res=self.finish(r);self.assertEqual(len(res['rows']),36);self.assertEqual(set(x['reversal_field_t'] for x in r.rows),set(m.FORC_EXAMPLE['reversal_fields_t']));self.assertEqual(len(m.analyze_forc(r.rows)['distribution']),36)
        rows=copy.deepcopy(m.CAPS['analysis.forc']['example_arguments']['rows']);result=m.analyze_forc(rows);self.assertTrue(all(abs(x['density_a_m2_per_t2']-.0005)<1e-12 for x in result['distribution']))
    def test_32_forc_requires_reachable_model_saturation_and_valid_rows(self):
        r=self.make();r.invoke('forc.configure',**dict(m.FORC_EXAMPLE,saturation_field_t=.01,reversal_fields_t=[-.1,-.08,-.05]));before=r.read('evaluator')
        with self.assertRaisesRegex(m.ActionRejected,'SATURATION'):r.invoke('acquisition.start')
        self.assertEqual(before,r.read('evaluator'));rows=copy.deepcopy(m.CAPS['analysis.forc']['example_arguments']['rows']);rows[0]['moment_a_m2']=None
        with self.assertRaises(m.ActionRejected):m.analyze_forc(rows)
    def test_33_partition_invariance_sixty_hz(self):
        a=self.make();b=self.make()
        for r in [a,b]:r.invoke('method.configure',**m.METHOD_EXAMPLE);r.invoke('acquisition.start')
        a.step(4.)
        for _ in range(240):b.step(1/60)
        self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.phase,b.phase)
        for x,y in zip(a.rows,b.rows):
            self.assertEqual(x['window_end_s'],y['window_end_s']);self.assertAlmostEqual(x['moment_a_m2'],y['moment_a_m2'],12)
        self.assertAlmostEqual(a.book['source_energy_j'],b.book['source_energy_j'],9)
    def test_34_reads_and_describe_are_pure(self):
        r=self.make();s=self.session(r);before=copy.deepcopy(r.__dict__)
        for _ in range(3):s.read(instance_id=r.instance_id);s.describe(instance_id=r.instance_id);r.step(0.)
        self.assertEqual(before,r.__dict__)
    def test_35_bad_inputs_and_busy_rejections_are_atomic(self):
        r=self.make();r.invoke('method.configure',**m.METHOD_EXAMPLE);r.invoke('acquisition.start')
        for c,a in [('field.set_target',dict(field_t=.1,ramp_rate_t_s=1.)),('sim.step',{'dt_s':float('nan')}),('sim.step',{'dt_s':True}),('sim.step',{'dt_s':-1}),('coil.configure',m.COIL_EXAMPLE)]:
            before=copy.deepcopy(r.__dict__)
            with self.assertRaises(m.ActionRejected):r.invoke(c,**a)
            self.assertEqual(before,r.__dict__)
    def test_36_dual_instances_do_not_share_material_memory_or_clock(self):
        a=self.make();b=self.make(sample=dict(m.SAMPLE_EXAMPLE,proxy_specific_moment_a_m2_kg=20.));self.loop(a);self.assertEqual(b.time_s,3.5);self.assertTrue(all(x['state']==-1 for x in b.relays));self.assertNotEqual(a._moment(),b._moment())
    def test_37_public_results_redact_identity_even_evaluator_forward(self):
        for role in ['agent','evaluator','debug']:
            r=self.make();s=self.session(r,'person',role);self.call(s,'method.configure',m.METHOD_EXAMPLE);op=self.call(s,'acquisition.start',{})['operation_id'];self.finish(r);text=json.dumps(self.call(s,'acquisition.result',{})['result'])+json.dumps(s.poll(op));self.assertNotIn('kuleuven',text);self.assertNotIn('CoFe2O4',text);self.assertNotIn('1LDRAQ',text);self.assertIn('synthetic_forward_model',text)
            if role=='agent':self.assertEqual(self.call(s,'provenance.read',{})['status'],'rejected')
            else:self.assertIn('1LDRAQ',json.dumps(self.call(s,'provenance.read',{})))
    def test_38_owner_idempotence_and_request_conflict(self):
        r=self.make();a=self.session(r,'a','agent');b=self.session(r,'b','agent');self.call(a,'method.configure',m.METHOD_EXAMPLE);first=self.call(a,'acquisition.start',{},'once');self.assertEqual(first,self.call(a,'acquisition.start',{},'once'));self.assertEqual(self.call(a,'acquisition.stop',{},'once')['error']['code'],'REQUEST_CONFLICT')
        with self.assertRaises(m.ActionRejected):b.poll(first['operation_id'])
        self.assertEqual(self.call(b,'acquisition.stop',{})['error']['code'],'ACCESS_DENIED');a.cancel(first['operation_id'],'cancel');self.assertEqual(a.cancel(first['operation_id'],'cancel')['status'],'succeeded')
    def test_39_visibility_and_wrong_instance(self):
        r=self.make();s=self.session(r,'a','agent');self.assertNotIn('truth.mass_kg',s.read(instance_id=r.instance_id)['observations']);self.assertNotIn('plant.configure',s.describe(instance_id=r.instance_id)['capabilities'])
        with self.assertRaises(m.ActionRejected):s.read(instance_id='wrong')
        with self.assertRaises(m.ActionRejected):s.read(instance_id=r.instance_id,state_ids=['truth.mass_kg'])
    def test_40_completed_snapshot_survives_new_sample_and_calibration(self):
        r=self.make();s=self.session(r);self.call(s,'method.configure',m.METHOD_EXAMPLE);op=self.call(s,'acquisition.start',{})['operation_id'];self.finish(r);old=s.poll(op);self.zero_raise(r);r.invoke('sample.remove');r.invoke('sample.load',**dict(m.SAMPLE_EXAMPLE,reference_id='crc_cuso4_5h2o',magnetic_model='linear_susceptibility'));r.invoke('calibration.clear');self.assertEqual(old,s.poll(op))
    def test_41_all_metadata_examples_and_actual_states_validate(self):
        meta=m.export_lakeshore_8604vsm_contract_metadata()
        for c in meta['capabilities'].values():m.validate(c['input_schema'],c['example_arguments'])
        r=self.make();self.loop(r)
        for key,value in r.read('evaluator').items():m.validate(meta['state_slots'][key]['value_schema'],value)
        for result,schema in [(r._result(),m.RESULT),(r.ledger(),m.LEDGER),(m.analyze_loop(r.rows),m.LOOP_OUTPUT)]:m.validate(schema,result)
    def test_42_unsupported_are_explicit(self):
        r=self.make();s=self.session(r)
        for c in m.UNSUPPORTED:self.assertEqual(self.call(s,c,{})['status'],'unsupported')
    def test_43_calibration_async_owner_and_wait(self):
        r=self.make(standard=True);s=self.session(r,'a','agent');out=self.call(s,'calibration.start',dict(reference_moment_a_m2=.00347,field_t=m.STANDARD_EXAMPLE['reference_field_t'],averaging_time_s=10.));r.step(1.);self.assertFalse(r.calibration_valid);self.assertEqual(s.poll(out['operation_id'])['status'],'running');s.cancel(out['operation_id'],'stop');self.assertFalse(r.calibration_valid)
    def test_44_each_implemented_capability_executes_with_declared_output(self):
        r=m.LakeShore8604VsmRuntime('complete');s=self.session(r);seen=set()
        def call(c,a):
            response=self.call(s,c,a);self.assertIn(response['status'],['succeeded','accepted'],(c,response));m.validate(m.CAPS[c]['output_schema'],response['result']);seen.add(c);return response
        for c,a in [('plant.configure',m.PLANT_EXAMPLE),('coil.configure',m.COIL_EXAMPLE),('standard.load',m.STANDARD_EXAMPLE),('power.set',{'enabled':True}),('sample.set_angle',{'angle_rad':0.}),('storage.set_open',{'opened':True}),('sample.set_lift',{'lift_m':0.}),('sim.step',{'dt_s':3.5}),('vibration.configure',m.VIBRATION_EXAMPLE),('calibration.start',dict(reference_moment_a_m2=.00347,field_t=m.STANDARD_EXAMPLE['reference_field_t'],averaging_time_s=.2))]:call(c,a)
        self.finish(r);call('field.set_target',dict(field_t=0.,ramp_rate_t_s=1.));r.step(2.4);r.invoke('vibration.configure',**dict(m.VIBRATION_EXAMPLE,enabled=False));r.invoke('sample.set_lift',lift_m=.35);r.step(3.5);call('sample.remove',{});call('sample.load',m.SAMPLE_EXAMPLE);r.invoke('sample.set_lift',lift_m=0.);r.step(3.5);r.invoke('vibration.configure',**m.VIBRATION_EXAMPLE)
        call('method.configure',m.METHOD_EXAMPLE);call('acquisition.start',{});self.finish(r);loop=call('acquisition.result',{})['result'];call('analysis.loop',dict(rows=loop['rows']));call('forc.configure',m.FORC_EXAMPLE);call('acquisition.start',{});self.finish(r);call('analysis.forc',dict(rows=r.rows));call('analysis.moment',m.CAPS['analysis.moment']['example_arguments']);call('provenance.read',{});call('ledger.read',{});call('calibration.clear',{});call('acquisition.start',{});r.step(.1);call('acquisition.stop',{});self.assertEqual(seen,set(m.CAPS)-set(m.UNSUPPORTED))

    def test_45_pending_lift_motion_blocks_field_before_first_step(self):
        r=self.make();r.invoke('vibration.configure',**dict(m.VIBRATION_EXAMPLE,enabled=False));r.invoke('sample.set_lift',lift_m=.35);before=copy.deepcopy(r.__dict__)
        with self.assertRaisesRegex(m.ActionRejected,'MEASUREMENT_POSITION'):r.invoke('field.set_target',field_t=.1,ramp_rate_t_s=1.)
        self.assertEqual(before,r.__dict__)
    def test_46_forc_reversal_labels_are_real_quantized_distinct_fields(self):
        r=self.make();before=copy.deepcopy(r.__dict__)
        with self.assertRaisesRegex(m.ActionRejected,'FORC_GRID'):r.invoke('forc.configure',**dict(m.FORC_EXAMPLE,reversal_fields_t=[-.12000001,-.12000002,-.12000003]))
        self.assertEqual(before,r.__dict__);r.invoke('forc.configure',**dict(m.FORC_EXAMPLE,reversal_fields_t=[-.12000001,-.08000002,-.04000003]));r.invoke('acquisition.start');self.finish(r);self.assertEqual(set(x['reversal_field_t'] for x in r.rows),set(m.quant(x,m.FIELD_RESOLUTION_T) for x in [-.12,-.08,-.04]))
    def test_47_all_actual_command_state_changes_are_declared(self):
        r=self.make();s=self.session(r);meta=m.export_lakeshore_8604vsm_contract_metadata()
        def checked(c,a):
            before=r.read('evaluator');response=self.call(s,c,a);self.assertIn(response['status'],('succeeded','accepted'));after=r.read('evaluator');changed={k for k in before if before[k]!=after[k]};allowed={x['state_id'] for x in meta['capabilities'][c]['state_effects']};self.assertFalse(changed-allowed,(c,changed-allowed));return response
        checked('method.configure',m.METHOD_EXAMPLE);checked('acquisition.start',{});checked('sim.step',{'dt_s':4.});checked('calibration.clear',{});checked('coil.configure',m.COIL_EXAMPLE);checked('field.set_target',{'field_t':0.,'ramp_rate_t_s':1.});checked('sim.step',{'dt_s':1.});checked('vibration.configure',dict(m.VIBRATION_EXAMPLE,enabled=False));checked('sample.set_angle',{'angle_rad':1.});checked('sim.step',{'dt_s':1.1});checked('sample.set_lift',{'lift_m':.35});checked('sim.step',{'dt_s':3.5});checked('sample.remove',{});checked('sample.load',m.SAMPLE_EXAMPLE);checked('power.set',{'enabled':False})

    def test_48_all_available_molar_chi_records_drive_actual_samples(self):
        count=0
        for record in m.library().values():
            if record.get('molar_susceptibility_m3_mol') is None:continue
            sample=dict(m.SAMPLE_EXAMPLE,reference_id=record['id'],magnetic_model='linear_susceptibility',initial_temperature_k=299.);r=self.make(sample=sample,plant=dict(m.PLANT_EXAMPLE,ambient_k=299.),lower=False);expected=sample['mass_kg']/record['molar_mass_kg_mol']*record['molar_susceptibility_m3_mol']*.01/m.MU0;self.assertAlmostEqual(r._moment(.01),expected,14);count+=1
        self.assertGreaterEqual(count,50)

if __name__=='__main__':unittest.main()
