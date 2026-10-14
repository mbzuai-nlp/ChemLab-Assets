"""Observed-process / independent physical / protocol tests for CHEM-144 only."""
import copy,hashlib,json,math,sys,unittest,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.asset_runtime.shimadzu_ags10knxd import *
PROCESS_EVIDENCE={};CHECKS=[]
def call(h,k,**a):
    before=h.read('debug');v=h.invoke(k,**a);validate(CAPS[k]['output_schema'],v);after=h.read('debug')
    delta={s for s in after if before[s]!=after[s]};declared=set(CAPS[k]['effects'])
    if not delta<=declared:raise AssertionError(f'{k}: undeclared immediate effects {delta-declared}')
    PROCESS_EVIDENCE.setdefault(k,[]).append(dict(time_s=h.time_s,phase=h.phase,result_keys=list(v),actual_call=True));return v

def ready(instance='test',plant=None,sample=None,fixture=None,close=True):
    h=ShimadzuAgs10knxdRuntime(instance)
    call(h,'plant.configure',**dict(PLANT_EXAMPLE,**(plant or {})));call(h,'fixture.configure',**dict(FIXTURE_EXAMPLE,**(fixture or {})));call(h,'sample.load',**dict(SAMPLE_EXAMPLE,**(sample or {})));call(h,'power.set',enabled=True)
    if close:
        for side in ['upper','lower']:call(h,'grip.set_closed',which=side,closed=True)
    h.step(.61);call(h,'force.zero');return h

def start(h,**method):call(h,'method.configure',**dict(METHOD_EXAMPLE,**method));return call(h,'acquisition.start')
def analysis(h,**args):return call(h,'analysis.tensile',**dict(CAPS['analysis.tensile']['example_arguments'],rows=h.rows,**args))
class AGSTest(unittest.TestCase):
    def check(self,condition,key):
        self.assertTrue(condition,key);CHECKS.append(dict(check_id=key,status='passed'))
    def reject(self,h,k,code=None,**a):
        before=copy.deepcopy(h.__dict__)
        with self.assertRaises(ActionRejected) as ctx:h.invoke(k,**a)
        if code:self.assertEqual(str(ctx.exception),code)
        self.assertEqual(before,h.__dict__)
    def balanced(self,h,tol=1e-8):
        b=call(h,'ledger.read');self.check(abs(b['energy_residual_j'])<tol,'energy_balance');self.check(abs(b['mass_residual_kg'])<1e-15,'mass_balance');self.check(abs(b['volume_residual_m3'])<1e-18,'volume_balance');return b
    def test01_official_units_and_limits(self):
        self.assertAlmostEqual(MIN_SPEED_M_S*60000,.001);self.assertAlmostEqual(MAX_SPEED_M_S*60000,1000);self.assertEqual((LOAD_CELL_MAX_N,FIXTURE_MAX_N,POSITION_RESOLUTION_M),(10000,5000,1e-6));self.check(True,'official_SI_conversions')
    def test02_setting_has_no_time_or_motion(self):
        h=ready();t=h.time_s;call(h,'crosshead.move_to',position_m=.00001,speed_m_s=.0001);self.assertEqual(h.time_s,t);self.assertEqual(h.read('evaluator')['crosshead.position_m'],0);h.step(.05);self.assertAlmostEqual(h.read('evaluator')['crosshead.position_m'],.000005);self.check(True,'finite_motion_target_only')
    def test03_series_compliance(self):
        h=ready();x=.00002;call(h,'crosshead.move_to',position_m=x,speed_m_s=.0001);h.step(.3);expected=x/(.025/(2e-6*h.E)+1e-8);self.assertAlmostEqual(h.force,expected,places=9);r=h.read('evaluator');self.assertAlmostEqual(r['crosshead.position_m']-r['frame.deflection_m'],r['sample.visual_extension_m']);self.assertGreater(x/.025,h._strain());self.balanced(h)
    def test04_elastic_actuator_work(self):
        h=ready();call(h,'crosshead.move_to',position_m=.00002,speed_m_s=.0001);h.step(.3);self.assertAlmostEqual(h.ledger()['actuator_work_j'],.5*h.force*.00002,places=11);self.assertEqual(h.plastic,0.);self.check(True,'independent_elastic_work')
    def test05_plastic_return_mapping(self):
        h=ready();x=.0005;call(h,'crosshead.move_to',position_m=x,speed_m_s=.001);h.step(.5);C=.025/(2e-6*h.E)+1e-8;ep=(x-C*2e-6*h.Y)/(.025+C*2e-6*1e9);self.assertAlmostEqual(h.plastic,ep,places=13);self.assertAlmostEqual(h.force/2e-6,h.Y+1e9*ep,places=4);self.balanced(h)
    def test06_unload_retains_plastic(self):
        h=ready();call(h,'crosshead.move_to',position_m=.0005,speed_m_s=.001);h.step(.5);ep=h.plastic;call(h,'crosshead.move_to',position_m=.025*ep,speed_m_s=.001);h.step(.2);self.assertAlmostEqual(h.force,0,places=7);self.assertEqual(h.plastic,ep);self.assertGreater(h.ledger()['hardening_stored_j'],0);self.reject(h,'crosshead.move_to','COMPRESSION_OR_SLACK_UNSUPPORTED',position_m=0.,speed_m_s=.001);self.balanced(h)
    def test07_independent_work_trapezoid(self):
        h=ready();target=.0008;call(h,'crosshead.move_to',position_m=target,speed_m_s=.001);history=[(0.,0.)]
        for _ in range(800):h.step(.001);history.append((h._pos('crosshead'),h.force))
        work=float(np.trapezoid(np.array(history)[:,1],np.array(history)[:,0]));self.assertAlmostEqual(work,h.ledger()['actuator_work_j'],delta=5e-6);self.check(True,'external_force_displacement_integral')
    def test08_fracture_irreversible_energy(self):
        h=ready();start(h);h.step(4);self.assertTrue(h.broken);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.terminal_reason,'fracture');r=h.read('evaluator');self.assertAlmostEqual(r['sample.visual_extension_m']+r['sample.fracture_gap_m'],r['crosshead.position_m']);b=self.balanced(h);self.assertGreater(b['fracture_heat_j'],0);self.assertGreater(b['fracture_surface_j'],0);self.assertGreater(b['machine_damping_export_j'],0);self.reject(h,'acquisition.start','SAMPLE_BROKEN')
    def test09_fixed_1000hz_duration(self):
        h=ready();start(h,duration_s=.1);h.step(.15);self.assertEqual(len(h.rows),101);times=np.array([r['time_s'] for r in h.rows]);self.assertTrue(np.allclose(np.diff(times),.001,atol=1e-15));self.assertAlmostEqual(h.elapsed,.1);frozen=call(h,'acquisition.result');self.assertEqual(frozen['rows'],h.rows);self.check(True,'actual_1000Hz_grid')
    def test10_fracture_event_rows(self):
        h=ready();start(h);h.step(3);events=[r for r in h.rows if r['kind']!='sample'];self.assertEqual([r['kind'] for r in events],['fracture_pre','fracture_post']);self.assertEqual(events[0]['time_s'],events[1]['time_s']);self.assertFalse(events[0]['broken']);self.assertTrue(events[1]['broken']);self.assertIsNotNone(events[0]['extensometer_extension_m']);self.assertIsNone(events[1]['extensometer_extension_m']);self.check(True,'fracture_event_observations')
    def test11_step_partition_60hz(self):
        a=ready();b=ready();start(a);start(b);a.step(3.)
        for _ in range(180):b.step(1/60)
        self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.rows,b.rows);self.assertAlmostEqual(a.temperature,b.temperature,places=9);self.assertAlmostEqual(a.ledger()['actuator_work_j'],b.ledger()['actuator_work_j'],places=10);self.check(True,'60Hz_fractional_ns_partition')
    def test12_manual_yield_partition(self):
        a=ready();b=ready()
        for h in [a,b]:call(h,'crosshead.move_to',position_m=.0008,speed_m_s=.001)
        a.step(.8)
        for _ in range(137):b.step(.8/137)
        self.assertAlmostEqual(a.plastic,b.plastic,places=12);self.assertAlmostEqual(a.temperature,b.temperature,places=8);self.assertAlmostEqual(a.sensor_force,b.sensor_force,places=7);self.balanced(a);self.balanced(b)
    def test13_read_poll_no_time(self):
        h=ready();h.register_principal('a','agent');s=h.session('a');res=s.invoke(instance_id=h.instance_id,capability_id='method.configure',arguments=METHOD_EXAMPLE,request_id='m');self.assertEqual(res['status'],'succeeded');r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='s');before=copy.deepcopy(h.__dict__)
        for _ in range(20):s.read(instance_id=h.instance_id);s.poll(r['operation_id'])
        self.assertEqual(before,h.__dict__);self.check(True,'read_poll_are_pure')
    def test14_cancel_preserves_plastic(self):
        h=ready();start(h);h.step(.8);ep=h.plastic;work=h.ledger()['actuator_work_j'];call(h,'acquisition.stop');rows=copy.deepcopy(h.rows);x=h._pos('crosshead');h.step(.5);self.assertEqual(h.plastic,ep);self.assertEqual(h.rows,rows);self.assertEqual(h._pos('crosshead'),x);self.assertAlmostEqual(h.ledger()['actuator_work_j'],work);self.assertGreater(h.force,0);self.balanced(h)
    def test15_power_off_retains_static_load(self):
        h=ready();start(h);h.step(.8);F=h.force;ep=h.plastic;call(h,'power.set',enabled=False);h.step(.3);self.assertEqual(h.phase,'cancelled');self.assertAlmostEqual(h.force,F);self.assertEqual(h.plastic,ep);self.assertIsNone(h.read('evaluator')['force.indicated_n']);self.assertIsNone(h.read('evaluator')['crosshead.indicated_m']);self.assertTrue(h.read('evaluator')['fixture.extensometer_enabled']);self.balanced(h)
    def test16_emergency_release_does_not_resume(self):
        h=ready();start(h);h.step(.3);call(h,'emergency.engage');x=h._pos('crosshead');h.step(.2);self.assertEqual(h._pos('crosshead'),x);self.reject(h,'crosshead.move_to','EMERGENCY_ENGAGED',position_m=.001,speed_m_s=.001);call(h,'emergency.reset');h.step(.1);self.assertEqual(h._pos('crosshead'),x);self.assertEqual(h.phase,'cancelled');self.balanced(h)
    def test17_fixture_5000_separate_sensor10000(self):
        h=ready(sample=dict(host_elastic_modulus_pa=2e11,host_yield_strength_pa=5e9,engineering_strain_at_break=.2));start(h,target_crosshead_m=.01,speed_m_s=.001);h.step(2);self.assertEqual(h.phase,'failed');self.assertEqual(h.terminal_reason,'FIXTURE_FORCE_LIMIT');self.assertAlmostEqual(h.force,5000,delta=.01);self.assertLess(h.force,LOAD_CELL_MAX_N);self.balanced(h)
    def test18_method_force_stop(self):
        h=ready();start(h,force_stop_n=300.);h.step(1);self.assertEqual(h.terminal_reason,'METHOD_FORCE_STOP');self.assertAlmostEqual(h.force,300.,delta=.01);self.reject(h,'acquisition.start','METHOD_FORCE_STOP');self.balanced(h)
    def test19_control_rate_and_software_guards(self):
        h=ready();self.reject(h,'method.configure','AUTOMATIC_CONTROL_MAX_100HZ',**dict(METHOD_EXAMPLE,mode='force'));h=ready(fixture=dict(software_trapezium_x=False));self.reject(h,'method.configure','SOFTWARE_OPTION_REQUIRED',**METHOD_EXAMPLE);call(h,'method.configure',**dict(METHOD_EXAMPLE,sample_rate_hz=10));self.check(True,'software_and_100Hz_control_guards')
    def test20_force_control_observed_feedback(self):
        h=ready(plant=dict(force_gain=1.,force_offset_n=0.));start(h,mode='force',sample_rate_hz=100,duration_s=2.,target_force_n=400.);h.step(2);self.assertAlmostEqual(h.read('evaluator')['force.indicated_n'],400.,delta=1.);self.assertEqual(len(h.rows),201);self.balanced(h)
    def test21_strain_control_extensometer(self):
        h=ready();start(h,mode='strain',sample_rate_hz=100,duration_s=8.,target_strain=.01);h.step(8);self.assertAlmostEqual(h.read('evaluator')['extensometer.strain'],.01,delta=.0005);self.balanced(h)
    def test22_extensometer_not_faked_by_crosshead(self):
        h=ready(fixture=dict(extensometer_enabled=False));self.assertIsNone(h.read('evaluator')['extensometer.strain']);self.assertFalse(h.read('evaluator')['fixture.extensometer_enabled']);self.reject(h,'method.configure','EXTENSOMETER_REQUIRED',**dict(METHOD_EXAMPLE,mode='strain',sample_rate_hz=100));start(h,duration_s=.1);h.step(.1);a=analysis(h);self.assertIsNone(a['elastic_modulus_pa']);self.assertTrue(all(x['strain'] is None for x in a['points']));a=analysis(h,frame_compliance_prior_m_n=1e-8);self.assertTrue(any(x['strain'] is not None for x in a['points']));self.check(True,'no_extensometer_requires_compliance_prior')
    def test23_external_analysis_known_curve(self):
        h=ready();args=copy.deepcopy(CAPS['analysis.tensile']['example_arguments']);r=call(h,'analysis.tensile',**args);self.assertAlmostEqual(r['elastic_modulus_pa']/2e11,1.,places=12);self.assertEqual(r['ultimate_observed_stress_pa'],2e8);self.check(True,'external_OLS_modulus')
    def test24_unknown_area_gauge_not_jointly_inverted(self):
        h=ready();start(h,duration_s=.1);h.step(.1);a=analysis(h,area_prior_m2=None,gauge_prior_m=None);self.assertIsNone(a['elastic_modulus_pa']);self.assertIsNone(a['ultimate_observed_stress_pa']);self.assertTrue(all(x['strain'] is None and x['stress_pa'] is None for x in a['points']));self.check(True,'independent_geometric_priors_required')
    def test25_external_fracture_and_modulus(self):
        h=ready(plant=dict(force_gain=1.,force_offset_n=0.));start(h);h.step(3);a=analysis(h);self.assertAlmostEqual(a['observed_break_strain'],.05,delta=5e-6);self.assertAlmostEqual(a['elastic_modulus_pa']/h.E,1.,delta=.002);self.assertGreater(a['ultimate_observed_stress_pa'],h.Y);self.check(True,'fracture_and_modulus_from_observation')
    def test26_calibration_external_prior(self):
        h=ready();x=200*h._compliance();call(h,'crosshead.move_to',position_m=x,speed_m_s=.0001);h.step(.3);gain=call(h,'force.calibrate',reference_force_n=200.)['gain'];self.assertAlmostEqual(gain,1/PLANT_EXAMPLE['force_gain'],places=10);self.assertAlmostEqual(h.read('evaluator')['force.indicated_n'],200.,places=9);self.reject(h,'force.zero','UNLOAD_REQUIRED');self.check(True,'calibration_actual_detector_external_reference')
    def test27_force_range_overflow_vs_low_force(self):
        h=ready(plant=dict(force_gain=100.));start(h,duration_s=.1);h.step(.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.terminal_reason,'LOAD_CELL_OVERFLOW');self.assertIsNone(h.rows[-1]['force_n']);h=ready();start(h,duration_s=.001);h.step(.001);self.assertIsNotNone(h.rows[0]['force_n']);self.assertEqual(h.rows[0]['quality'],'below_documented_accuracy_range');self.check(True,'low_force_not_illegally_rejected')
    def test28_loading_sensible_heat(self):
        h=ready(sample=dict(initial_temperature_k=295.65));m=SAMPLE_EXAMPLE['volume_m3']*SAMPLE_EXAMPLE['density_kg_m3'];self.assertAlmostEqual(h.ledger()['sensible_loaded_j'],m*470*3,places=12);self.assertGreater(h.temperature,292.65);self.balanced(h)
    def test29_remove_fractured_inventory(self):
        h=ready();start(h);h.step(3)
        for side in ['upper','lower']:call(h,'grip.set_closed',which=side,closed=False)
        h.step(.61);removed=call(h,'sample.remove');self.assertAlmostEqual(removed['removed_mass_kg'],.0008635);self.assertFalse(h.read('evaluator')['sample.present']);self.assertGreater(h.ledger()['internal_removed_j'],0);self.assertEqual(h.read('evaluator')['sample.visual_extension_m'],0);self.balanced(h)
    def test30_remove_under_load_rejected(self):
        h=ready();call(h,'crosshead.move_to',position_m=.00002,speed_m_s=.0001);h.step(.3);self.reject(h,'grip.set_closed','UNLOAD_REQUIRED',which='upper',closed=False);self.reject(h,'sample.remove','UNLOAD_REQUIRED');self.check(True,'loaded_grip_interlock')
    def test31_unattached_sample_motion_guard(self):
        h=ready(close=False);self.reject(h,'crosshead.move_to','GRIPS_NOT_CLOSED',position_m=.001,speed_m_s=.001);self.reject(h,'acquisition.start','CONFIGURATION_REQUIRED');self.check(True,'unattached_visual_kinematic_guard')
    def test32_invalid_requests_atomic(self):
        h=ready();self.reject(h,'crosshead.move_to','INVALID_ARGUMENT',position_m=float('nan'),speed_m_s=.001);self.reject(h,'crosshead.move_to','INVALID_ARGUMENT',position_m=.001,speed_m_s=True);self.reject(h,'power.set','INVALID_ARGUMENT',enabled=True,extra=1);self.reject(h,'sample.load','SAMPLE_PRESENT',**SAMPLE_EXAMPLE)
        for dt in [-1,float('nan'),float('inf'),True]:
            before=copy.deepcopy(h.__dict__)
            with self.assertRaises(ActionRejected):h.step(dt)
            self.assertEqual(before,h.__dict__)
        self.check(True,'invalid_inputs_no_effects')
    def test33_library_real_and_all_templates_usable_explicitly(self):
        refs=library();self.assertGreaterEqual(len(refs),50);curves=sum(len(v.get('measured_true_stress_strain',[])) for v in refs.values());self.assertGreater(curves,1000)
        for ident,ref in refs.items():
            T=ref.get('reference_temperature_K') or 292.65
            if not 278.15<=T<=313.15:continue
            h=ready(plant=dict(ambient_k=T),sample=dict(reference_id=ident,initial_temperature_k=T,host_elastic_modulus_pa=None if ref.get('elastic_modulus_Pa') else 1e9,host_yield_strength_pa=None if ref.get('yield_strength_Pa') else 1e7));self.assertGreater(h.E,0);self.assertGreater(h.Y,0)
        self.check(True,'real_library_missing_properties_explicit_host_completion')
    def test34_acceptance_and_volume_guards(self):
        h=ShimadzuAgs10knxdRuntime();self.reject(h,'plant.configure','ENGINEERING_ACCEPTANCE_REQUIRED',**dict(PLANT_EXAMPLE,accept_engineering=False));call(h,'plant.configure',**PLANT_EXAMPLE);call(h,'fixture.configure',**FIXTURE_EXAMPLE);self.reject(h,'sample.load','ENGINEERING_ACCEPTANCE_REQUIRED',**dict(SAMPLE_EXAMPLE,accept_bilinear_geometry_temperature_fracture_assumptions=False));self.reject(h,'sample.load','VOLUME_BELOW_GAUGE',**dict(SAMPLE_EXAMPLE,volume_m3=1e-9));self.check(True,'explicit_material_and_thermal_priors')
    def test35_temperature_boundary(self):
        h=ShimadzuAgs10knxdRuntime();call(h,'plant.configure',**PLANT_EXAMPLE);call(h,'fixture.configure',**FIXTURE_EXAMPLE);self.reject(h,'sample.load','REFERENCE_TEMPERATURE_RANGE',**dict(SAMPLE_EXAMPLE,initial_temperature_k=310.,temperature_tolerance_k=.1));h=ready(sample=dict(temperature_tolerance_k=.00001));start(h);h.step(.3);self.assertEqual(h.phase,'failed');self.assertEqual(h.terminal_reason,'REFERENCE_TEMPERATURE_RANGE');self.balanced(h)
    def test36_passive_cooling_independent_solution(self):
        h=ready(sample=dict(initial_temperature_k=295.65));T0=h.temperature;C=.01+.0008635*470;h.step(.7);expected=292.65+(T0-292.65)*math.exp(-.1*.7/C);self.assertAlmostEqual(h.temperature,expected,places=11);self.balanced(h)
    def test37_multienv_isolation(self):
        a=ready('a');b=ready('b',sample=dict(host_elastic_modulus_pa=1e10,host_yield_strength_pa=1e8));start(a);start(b);a.step(.2);self.assertEqual(b.elapsed,0.);b.step(.2);self.assertNotEqual(a.rows[-1]['force_n'],b.rows[-1]['force_n']);self.check(True,'independent_instances')
    def test38_all_roles_result_redaction(self):
        h=ready();start(h,duration_s=.01);h.step(.01)
        for role in ['agent','evaluator','debug']:
            h.register_principal(role,role);s=h.session(role);h._owners[h.operation_id]=role;r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.result',arguments={},request_id='r');text=json.dumps(r);self.assertNotIn(SAMPLE_EXAMPLE['reference_id'],text);self.assertNotIn('S355',text);self.assertNotIn('LP1_S1',text);self.assertNotIn(SAMPLE_EXAMPLE['reference_id'],json.dumps(s.poll(h.operation_id)))
        self.check(True,'bridge_privileged_session_public_redaction')
    def test39_provenance_evaluator_and_snapshot(self):
        h=ready();start(h);h.step(3);h.register_principal('a','agent');h.register_principal('e','evaluator');r=h.session('a').invoke(instance_id=h.instance_id,capability_id='provenance.read',arguments={},request_id='x');self.assertEqual(r['error']['code'],'ACCESS_DENIED');prior=call(h,'provenance.read')['operation_source']
        for side in ['upper','lower']:call(h,'grip.set_closed',which=side,closed=False)
        h.step(.61);call(h,'sample.remove');p=call(h,'provenance.read');self.assertIsNone(p['current_sample']);self.assertEqual(p['operation_source'],prior);self.check(True,'private_source_and_operation_snapshot')
    def test40_session_identity_truth_and_describe(self):
        h=ready();h.register_principal('a','agent');s=h.session('a');d=s.describe(instance_id=h.instance_id);self.assertNotIn('truth.force_n',d['state_slots']);self.assertNotIn('plant.configure',d['capabilities'])
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.force_n'])
        r=s.invoke(instance_id='wrong',capability_id='power.set',arguments={'enabled':False},request_id='x');self.assertEqual(r['error']['code'],'UNKNOWN_INSTANCE');self.assertTrue(h.power);self.check(True,'session_identity_and_truth_isolation')
    def test41_idempotence_and_owner_cancel(self):
        h=ready();call(h,'method.configure',**METHOD_EXAMPLE)
        for n in ['a','b']:h.register_principal(n,'agent')
        a=h.session('a');b=h.session('b');r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start'),r);h.step(.1)
        with self.assertRaises(ActionRejected):b.poll(r['operation_id'])
        c=a.cancel(r['operation_id'],'stop');self.assertEqual(a.cancel(r['operation_id'],'stop'),c);bad=a.invoke(instance_id=h.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='start');self.assertEqual(bad['error']['code'],'REQUEST_CONFLICT');self.check(True,'operation_owner_and_retry')
    def test42_unsupported_accurate(self):
        h=ready()
        for k in UNSUPPORTED:self.reject(h,k,'UNSUPPORTED')
        self.check(True,'unsupported_negative_only')
    def test43_contract_schemas_and_bindings(self):
        meta=export_shimadzu_ags10knxd_contract_metadata();h=ready()
        schemas=[v[which] for v in CAPS.values() for which in ['input_schema','output_schema']]+[v['schema'] for v in SLOTS.values()]
        result=subprocess.run([sys.executable,'-c','import sys,json;from jsonschema import Draft202012Validator;[Draft202012Validator.check_schema(s) for s in json.load(sys.stdin)]'],input=json.dumps(schemas),text=True,capture_output=True);self.assertEqual(result.returncode,0,result.stderr)
        for k,v in CAPS.items():validate(v['input_schema'],v['example_arguments']);self.assertEqual(meta['capabilities'][k]['availability'],'unsupported' if k in UNSUPPORTED else 'implemented')
        for k,v in h.read('debug').items():validate(SLOTS[k]['schema'],v)
        self.check(True,'metadata_all_schemas_examples_state_units')
    def test44_simstep_cap_and_time_scale(self):
        h=ready();t=h.time_s;r=call(h,'sim.step',dt_s=1/60);self.assertAlmostEqual(r['time_s']-t,1/60,delta=1e-9);before=copy.deepcopy(h.__dict__);h.step(0);self.assertEqual(before,h.__dict__);self.check(True,'sole_clock_fractional_step')
    def test45_after_break_continue_gap_without_more_work(self):
        h=ready();start(h,stop_on_break=False,target_crosshead_m=.002,duration_s=5.);h.step(3);self.assertTrue(h.broken);ep=h.plastic;work=h.ledger()['actuator_work_j'];gap=h.read('evaluator')['sample.fracture_gap_m'];h.step(.5);self.assertGreater(h.read('evaluator')['sample.fracture_gap_m'],gap);self.assertEqual(h.plastic,ep);self.assertAlmostEqual(h.ledger()['actuator_work_j'],work);self.balanced(h)
    def test46_encoder_quantization_not_accuracy(self):
        h=ready();call(h,'crosshead.move_to',position_m=1.49e-6,speed_m_s=.0001);h.step(.02);self.assertEqual(h.read('evaluator')['crosshead.indicated_m'],1e-6);self.assertEqual(h.read('evaluator')['crosshead.position_m'],1.49e-6);self.check(True,'display_resolution_not_position_error')

    def test47_fracture_thermal_invalid_is_failed_not_rolled_back(self):
        h=ready(sample=dict(engineering_strain_at_break=.001,temperature_tolerance_k=.001));start(h);h.step(1)
        self.assertTrue(h.broken);self.assertEqual(h.phase,'failed');self.assertEqual(h.terminal_reason,'REFERENCE_TEMPERATURE_RANGE');self.assertEqual(h.rows[-1]['kind'],'fracture_post');self.assertIsNone(h.rows[-1]['force_n']);self.assertGreater(h.ledger()['fracture_heat_j'],0);self.balanced(h);self.check(True,'fracture_event_invalid_data_fails_preserving_physics')
    def test48_non_grid_terminal_invalid_observation(self):
        h=ready(plant=dict(force_gain=100.));start(h,sample_rate_hz=1,duration_s=.05);h.step(.2);self.assertEqual(h.phase,'failed');self.assertEqual(h.terminal_reason,'LOAD_CELL_OVERFLOW');self.assertAlmostEqual(h.elapsed,.05);self.assertIsNone(h.rows[-1]['force_n']);self.check(True,'terminal_event_invalid_data_fails')
    def test49_initial_or_feedback_invalid_observation(self):
        h=ready(plant=dict(force_gain=100.));call(h,'crosshead.move_to',position_m=.00002,speed_m_s=.0001);h.step(.3);call(h,'method.configure',**METHOD_EXAMPLE);self.reject(h,'acquisition.start','LOAD_CELL_OVERFLOW')
        h=ready(plant=dict(force_gain=100.,force_control_gain_m_per_n_s=.001));start(h,mode='force',sample_rate_hz=1,speed_m_s=.01,target_force_n=4000.,duration_s=.2);h.step(.2);self.assertEqual(h.phase,'failed');self.assertEqual(h.terminal_reason,'LOAD_CELL_OVERFLOW');self.check(True,'invalid_force_not_substituted_zero_in_feedback')

    def test50_exact_scene_coordinates_are_privileged(self):
        h=ready(fixture={'extensometer_enabled':False});start(h);h.step(.2);h.register_principal('a','agent');s=h.session('a')
        private=['frame.deflection_m','sample.visual_extension_m','sample.fracture_gap_m'];public=s.read(instance_id=h.instance_id)['observations'];description=s.describe(instance_id=h.instance_id)
        for key in private:
            self.assertNotIn(key,h.read());self.assertNotIn(key,public);self.assertNotIn(key,description['state_slots']);self.assertIn(key,h.read('evaluator'))
            with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=[key])
        self.assertIsNone(public['extensometer.extension_m']['value']);self.check(True,'no_extensometer_no_exact_scene_strain_leak')

if __name__=='__main__':unittest.main(verbosity=2)
