"""CHEM138: actual chemistry/thermal integration, public protocol and conservation."""
import copy,importlib.util,json,math,unittest
from pathlib import Path
import numpy as np
from scipy.integrate import solve_ivp
from scripts.asset_runtime import ta_tamiv as r
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('tam_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');asset_tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(asset_tool)
OBSERVED=[]
class Checked(r.TaTamivRuntime):
    def invoke(self,k,**a):
        before=self.read();out=super().invoke(k,**a);after=self.read();changed={x for x in after if after[x]!=before[x]}
        assert not changed-set(r.CAPS[k]['effects']),(k,changed-set(r.CAPS[k]['effects']))
        assert not asset_tool.validate_value(out,r.CAPS[k]['output_schema']),(k,asset_tool.validate_value(out,r.CAPS[k]['output_schema']))
        OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(out)));return out

def make(plant=None,sample=None,reference=None,method=None):
    h=Checked();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,**(plant or {})));h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,**(sample or {})));h.invoke('reference.load',**dict(r.REFERENCE_EXAMPLE,**(reference or {})));h.invoke('power.set',enabled=True);h.invoke('thermal.set_temperature',temperature_k=298.15);h.invoke('sample.set_lift',lift_m=0.);h.step(1.);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,**(method or {})));return h

def finish(h,trigger=True):
    h.invoke('acquisition.start')
    if trigger:h.invoke('sample.trigger')
    h.step(h.method['duration_s']);return h.invoke('acquisition.result')
def remove(h):
    if h.phase=='running':h.invoke('acquisition.stop')
    h.invoke('calibration.set_power',power_w=0.);h.invoke('sample.set_lift',lift_m=.48);h.step(1.);h.invoke('sample.remove')
def sessions(h):
    for n,role in [('student','agent'),('teacher','evaluator'),('other','agent')]:h.register_principal(n,role)
    return h.session('student'),h.session('teacher'),h.session('other')
def call(s,c,a=None,rid='r1'):return s.invoke(instance_id=s._host.instance_id,capability_id=c,arguments=a or {},request_id=rid)
def snapshot(h):
    return json.dumps(dict(state=h.read(),plant=h.plant,sample=h.sample,reference=h.reference,ledger=h._ledger(),rows=h.rows,records=h.records,revision=h.revision,calibration=h.calibration,method=h.method,x=h.x.tolist()),sort_keys=True,allow_nan=False)

class TamTests(unittest.TestCase):
    def budget(self,h,tol=1e-9):
        for k,v in h.invoke('ledger.read').items():
            if 'error' in k:self.assertLess(abs(v),tol,(k,v))
    def reject(self,h,k,**a):
        before=snapshot(h)
        with self.assertRaises(r.ActionRejected):h.invoke(k,**a)
        self.assertEqual(snapshot(h),before)
    def test_01_targets_and_motion_are_not_teleports(self):
        h=make();old=h.x.copy();h.invoke('thermal.set_temperature',temperature_k=300.15);np.testing.assert_array_equal(h.x,old);h.invoke('sample.set_lift',lift_m=.48);self.assertEqual(h.lift,0.);h.step(.5);self.assertEqual(h.lift,.24);self.assertGreater(h._temperature(0),298.15);self.budget(h)
    def test_02_reads_describe_and_zero_are_pure(self):
        h=make();a,e,_=sessions(h);before=snapshot(h)
        for _ in range(2):a.read(instance_id=h.instance_id);e.describe(asset_type_id=r.ASSET_TYPE_ID);h.step(0.)
        self.assertEqual(before,snapshot(h))
    def test_03_sixtysix_real_enthalpies_and_mass_closure(self):
        lib=r.library();self.assertGreaterEqual(len(lib),50)
        for row in lib.values():
            self.assertEqual(row['reference_temperature_K'],298.15);self.assertTrue(row['source']);self.assertTrue(row['enthalpy_J_mol']);self.assertAlmostEqual(sum(p['stoichiometry']*p['molar_mass_kg_mol'] for p in row['products']),row['molar_mass_kg_mol'],places=9)
    def test_04_halite_heat_and_true_vs_recorded_integral(self):
        h=make();o=finish(h);expected=1e-6*(1-math.exp(-.5*12));self.assertEqual(o['phase'],'succeeded');self.assertEqual(len(o['rows']),241);self.assertAlmostEqual(h.sample['converted'],expected,17);self.assertAlmostEqual(h.reaction_heat,-3700*expected,14);self.assertLess(min(x['heat_flow_w'] for x in o['rows']),0);self.assertGreater(abs(h.reaction_heat-o['recorded_heat_j']),1e-5);self.budget(h)
    def test_05_stoichiometric_products_not_constant_mass_shortcut(self):
        h=make();h.invoke('sample.trigger');h.step(2.);xi=h.sample['converted'];d=h.species_ledger();self.assertAlmostEqual(d['products']['Na+']['amount_mol'],xi,18);self.assertAlmostEqual(d['products']['Cl-']['amount_mol'],xi,18);m=d['carrier_water_kg']+d['solid_mol']*h.sample['molar_mass']+sum(v['mass_kg'] for v in d['products'].values());self.assertEqual(m,h._mass());self.budget(h)
    def test_06_crystal_water_is_conserved(self):
        h=make(sample={'reference_id':'chalcanthite_dissolution'});h.invoke('sample.trigger');h.step(3.);water=next(p for p in h.sample['products'] if p['phase']=='liquid_water');self.assertEqual(water['stoichiometry'],5.);self.assertAlmostEqual(h.species_ledger()['products'][water['id']]['mass_kg'],5*h.sample['converted']*water['molar_mass_kg_mol'],18);self.budget(h)
    def test_07_solubility_limit_leaves_solid(self):
        h=make(sample={'reference_id':'barite_dissolution'});h.invoke('sample.trigger');limit=.0001*10**(-9.98/2);self.assertAlmostEqual(h.x[4],limit,18);h.step(40.);self.assertGreater(h.sample['amount_mol']-h.sample['converted'],.99e-6);self.assertLessEqual(h.sample['converted'],limit);self.budget(h)
    def test_08_exothermic_sign(self):
        h=make(sample={'reference_id':'cuso4_dissolution','amount_mol':1e-7});o=finish(h);self.assertGreater(h.reaction_heat,0);self.assertGreater(max(row['heat_flow_w'] for row in o['rows']),0);self.budget(h)
    def test_09_blank_and_no_automatic_reaction(self):
        h=make(plant={'baseline_offset_w':0.,'baseline_drift_w_s':0.});o=finish(h,False);self.assertFalse(h.sample['triggered']);self.assertEqual(h.reaction_heat,0.);self.assertTrue(all(row['heat_flow_w']==0. for row in o['rows']));self.budget(h)
    def test_10_electric_heat_enters_sample_before_sensor(self):
        h=make(plant={'baseline_offset_w':0.,'baseline_drift_w_s':0.});h.invoke('calibration.set_power',power_w=.001);self.assertEqual(h._observed(),0.);h.step(.01);self.assertGreater(h.x[1],h.x[0]);self.assertGreater(h._observed(),0.);self.assertLess(h._observed(),.001);self.assertAlmostEqual(h.electrical_input,.00001,15);h.invoke('calibration.set_power',power_w=0.);h.step(.1);self.assertAlmostEqual(h.electrical_input,.00001,15);self.budget(h)
    def test_11_baseline_estimated_from_actual_blank(self):
        h=make(plant={'baseline_offset_w':4e-7,'baseline_drift_w_s':2e-9,'heatflow_resolution_w':1e-12});o=finish(h,False);args=dict(time_s=[x['host_time_s'] for x in o['rows']],heat_flow_w=[x['heat_flow_w'] for x in o['rows']]);f=h.invoke('analysis.baseline',**args);self.assertAlmostEqual(f['intercept_w'],4e-7,15);self.assertAlmostEqual(f['drift_w_s'],2e-9,15)
    def test_12_actual_electric_platform_gain(self):
        h=make(plant={'sensor_gain':1.2,'baseline_offset_w':3e-7,'baseline_drift_w_s':0.},method={'duration_s':25.});blank=h._observed();h.invoke('calibration.set_power',power_w=.0001);o=finish(h,False);plateau=o['rows'][-1]['heat_flow_w'];f=h.invoke('analysis.electrical_calibration',known_power_w=.0001,plateau_observed_w=plateau,baseline_observed_w=blank);self.assertAlmostEqual(f['gain_correction'],1/1.2,places=4);self.assertAlmostEqual(h.electrical_input,.0025,14);self.budget(h)
    def test_13_external_kinetics_has_no_private_sample(self):
        h=Checked();f=h.invoke('analysis.kinetics',**r.KINETICS_EXAMPLE);self.assertAlmostEqual(f['rate_s_inv'],.5,6);self.assertAlmostEqual(f['total_heat_j'],-.0037,10);self.assertIsNone(h.sample)
    def test_14_near_equal_response_rates_are_finite(self):
        t=np.linspace(0,8,101);out=r.first_order_observation(t,1.,1.,1.);expected=.5*t*t*np.exp(-t);np.testing.assert_allclose(out,expected,atol=1e-15)
    def test_15_independent_ode_response_and_external_fit(self):
        t=np.linspace(0,18.,361);k=.37;H=-.0074;thermal=2.34;sensor=.2
        def ode(t,y):return [-k*y[0],(H*k*y[0]-y[1])/thermal,(y[1]-y[2])/sensor]
        sol=solve_ivp(ode,(0,18),[1.,0.,0.],t_eval=t,rtol=1e-10,atol=1e-14);h=Checked();f=h.invoke('analysis.kinetics',time_s=t.tolist(),heat_flow_w=(sol.y[2]+2e-7+1e-9*t).tolist(),trigger_time_s=0.,thermal_tau_s=thermal,sensor_tau_s=sensor,minimum_rate_s_inv=.01,maximum_rate_s_inv=10.,accept_fixed_bath_first_order=True);self.assertAlmostEqual(f['rate_s_inv'],k,6);self.assertAlmostEqual(f['total_heat_j'],H,9)
    def test_16_cancel_freezes_rows_but_chemistry_continues(self):
        h=make();h.invoke('acquisition.start');h.invoke('sample.trigger');h.step(2.);h.invoke('acquisition.stop');rows=copy.deepcopy(h.rows);q=h.recorded_heat;xi=h.sample['converted'];h.step(10.);self.assertEqual(h.rows,rows);self.assertEqual(h.recorded_heat,q);self.assertGreater(h.sample['converted'],xi);self.budget(h)
    def test_17_power_off_does_not_arrest_chemistry(self):
        h=make();h.invoke('acquisition.start');h.invoke('sample.trigger');h.invoke('calibration.set_power',power_w=.0001);h.step(.5);h.invoke('power.set',enabled=False);xi=h.sample['converted'];e=h.electrical_input;c=h.controller_input;h.step(2.);self.assertGreater(h.sample['converted'],xi);self.assertEqual(h.electrical_input,e);self.assertEqual(h.controller_input,c);self.assertIsNone(h._observed());self.assertEqual(h.phase,'cancelled');self.budget(h)
    def test_18_remove_exports_remaining_chemistry(self):
        h=make();h.invoke('sample.trigger');h.step(1.);remove(h);d=h.invoke('ledger.read');self.assertGreater(d['reactant_removed_mol'],0);self.assertGreater(d['converted_removed_mol'],0);self.assertEqual(d['sample_mass_present_kg'],0);q=h.reaction_heat;h.step(2.);self.assertEqual(q,h.reaction_heat);self.budget(h)
    def test_19_loading_and_unloading_do_not_create_sensible_heat(self):
        h=Checked();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,ambient_k=293.15));h.invoke('sample.load',**r.SAMPLE_EXAMPLE);h.invoke('reference.load',**dict(r.REFERENCE_EXAMPLE,initial_temperature_k=303.15));self.assertAlmostEqual(h.loaded_sensible,.418*15,14);self.budget(h);h.step(2.);h.invoke('sample.remove');h.invoke('reference.remove');self.assertGreater(h.removed_sensible,0);self.budget(h)
    def test_20_lift_switches_sample_to_ambient(self):
        a=make(plant={'retracted_conductance_w_k':0.});b=make(plant={'retracted_conductance_w_k':0.})
        for h in (a,b):h.invoke('thermal.set_temperature',temperature_k=300.15)
        b.invoke('sample.set_lift',lift_m=.48);a.step(1.);b.step(1.);self.assertEqual(b.x[1],0.);self.assertGreater(a.x[1],0.);self.budget(a);self.budget(b)
    def test_21_motion_arrival_partition(self):
        a=make();b=make()
        for h in (a,b):h.invoke('sample.trigger');h.invoke('sample.set_lift',lift_m=.24)
        a.step(1.2)
        for _ in range(72):b.step(1/60)
        np.testing.assert_allclose(a.x,b.x,rtol=1e-12,atol=1e-15);self.assertEqual(a.lift,b.lift);self.budget(a);self.budget(b)
    def test_22_controller_heat_and_cool_limits(self):
        for target,expected in [(310.15,2.),(280.15,-1.)]:
            h=make(plant={'bath_capacity_j_k':10.,'controller_heating_limit_w':2.,'controller_cooling_limit_w':1.});h.invoke('thermal.set_temperature',temperature_k=target);h.step(1.);self.assertAlmostEqual(h.controller_input,expected,10);self.budget(h)
    def test_23_saturated_power_partition(self):
        a=make(plant={'bath_capacity_j_k':10.,'controller_heating_limit_w':2.});b=make(plant={'bath_capacity_j_k':10.,'controller_heating_limit_w':2.})
        for h in (a,b):h.invoke('thermal.set_temperature',temperature_k=310.15)
        a.step(1.)
        for _ in range(60):b.step(1/60)
        np.testing.assert_allclose(a.x,b.x,rtol=1e-9,atol=1e-11);self.assertAlmostEqual(a.controller_input,b.controller_input,9);self.budget(a);self.budget(b)
    def test_24_nominal_target_limits_and_disable(self):
        h=make();h.invoke('thermal.set_temperature',temperature_k=423.15);h.step(.1);h.invoke('thermal.disable');c=h.controller_input;h.step(.1);self.assertEqual(c,h.controller_input);h.invoke('thermal.set_temperature',temperature_k=277.15);self.reject(h,'thermal.set_temperature',temperature_k=423.16);self.budget(h)
    def test_25_temperature_invalidates_observation_not_chemistry(self):
        h=make(plant={'bath_capacity_j_k':1.});h.invoke('sample.trigger');h.invoke('thermal.set_temperature',temperature_k=303.15);h.step(3.);self.assertIsNone(h._observed());self.reject(h,'acquisition.start');xi=h.sample['converted'];h.step(1.);self.assertGreater(h.sample['converted'],xi);self.budget(h)
    def test_26_temperature_drift_fails_active_record(self):
        h=make(sample={'reference_id':'cuso4_dissolution','amount_mol':1e-5});h.invoke('acquisition.start');h.invoke('sample.trigger');h.step(2.);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'REFERENCE_TEMPERATURE_RANGE');self.assertIsNone(h.recorded_heat);self.assertIsNone(h.rows[-1]['heat_flow_w']);xi=h.sample['converted'];h.step(1.);self.assertGreater(h.sample['converted'],xi);self.budget(h)
    def test_27_sensor_range_is_missing_not_zero(self):
        h=make(plant={'heatflow_limit_w':1e-6});o=finish(h);self.assertEqual(o['phase'],'failed');self.assertEqual(o['error_code'],'SENSOR_RANGE');self.assertIsNone(o['recorded_heat_j']);self.assertTrue(any(x['heat_flow_w'] is None for x in o['rows']))
    def test_28_sixty_hz_clock_and_internal_rows(self):
        a=make();b=make()
        for h in (a,b):h.invoke('acquisition.start');h.invoke('sample.trigger')
        a.step(12.)
        for _ in range(720):b.step(1/60)
        self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.rows,b.rows);np.testing.assert_allclose(a.x,b.x,rtol=1e-11,atol=1e-14);self.budget(a);self.budget(b)
    def test_29_sampling_grid_and_input_atomicity(self):
        h=make()
        for override in [{'duration_s':.123,'sample_interval_s':.05},{'duration_s':1.,'sample_interval_s':.00000001}]:self.reject(h,'method.configure',**dict(r.METHOD_EXAMPLE,**override))
        for dt in [True,-1.,float('nan'),float('inf')]:
            before=snapshot(h)
            with self.assertRaises(r.ActionRejected):h.step(dt)
            self.assertEqual(before,snapshot(h))
        self.reject(h,'thermal.set_temperature',temperature_k=True);self.reject(h,'thermal.set_temperature',temperature_k=298.15,extra=1)
    def test_30_busy_load_and_capacity_guards(self):
        h=make();h.invoke('acquisition.start');self.reject(h,'sample.set_lift',lift_m=.48);self.reject(h,'sensor.calibrate',**r.CALIBRATION_EXAMPLE);self.reject(h,'sample.remove');h.invoke('acquisition.stop');remove(h);self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,volume_m3=21e-6));self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,reference_id='missing'));self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,initial_temperature_k=303.15))
    def test_31_double_instance_isolation(self):
        a=make();b=make();before=snapshot(b);finish(a);self.assertEqual(snapshot(b),before);self.assertNotEqual(a.reaction_heat,b.reaction_heat)
    def test_32_identity_and_visibility(self):
        h=make();a,e,_=sessions(h);self.assertNotIn('truth.sample_k',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.sample_k',e.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.trigger',a.describe(instance_id=h.instance_id)['capabilities']);self.assertEqual(call(a,'sample.trigger')['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(r.ActionRejected):h.session('unregistered')
        with self.assertRaises(r.ActionRejected):a.read(instance_id=h.instance_id,state_ids=['truth.sample_k'])
        with self.assertRaises(r.ActionRejected):h.register_principal('student','evaluator')
    def test_33_idempotency_owner_and_cancel(self):
        h=make();a,e,b=sessions(h);start=call(a,'acquisition.start');self.assertEqual(start,call(a,'acquisition.start'));self.assertEqual(call(a,'power.set',{'enabled':True})['error']['code'],'REQUEST_CONFLICT');self.assertEqual(call(b,'acquisition.stop')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(b,'power.set',{'enabled':False},'off')['error']['code'],'ACCESS_DENIED');h.invoke('sample.trigger');h.step(.2);op=start['operation_id'];response=a.cancel(op,'cancel');self.assertEqual(response,a.cancel(op,'cancel'));self.assertEqual(a.poll(op)['status'],'cancelled')
    def test_34_privileged_public_results_do_not_leak_source(self):
        h=make();a,e,_=sessions(h);op=call(a,'acquisition.start')['operation_id'];h.invoke('sample.trigger');h.step(12.)
        for payload in [call(a,'acquisition.result',rid='a'),call(e,'acquisition.result',rid='e'),a.poll(op)]:
            text=json.dumps(payload);self.assertNotIn('halite',text.lower());self.assertNotIn('minteq',text.lower());self.assertNotIn('3700',text)
        self.assertEqual(call(a,'provenance.read',rid='p')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(e,'provenance.read',rid='p')['result']['reference_id'],'halite_dissolution')
    def test_35_result_provenance_snapshots_survive_new_sample(self):
        h=make();old=finish(h);remove(h);self.assertIsNone(h.invoke('provenance.read')['reference_id']);h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,reference_id='barite_dissolution'));new=h.invoke('acquisition.result');self.assertEqual(new,old);self.assertEqual(h.invoke('provenance.read')['reference_id'],'barite_dissolution')
    def test_36_assumption_acceptance_and_unsupported(self):
        h=Checked();self.reject(h,'plant.configure',**dict(r.PLANT_EXAMPLE,accept_engineering=False));h.invoke('plant.configure',**r.PLANT_EXAMPLE);self.reject(h,'sample.load',**dict(r.SAMPLE_EXAMPLE,accept_ideal_solution_and_kinetics=False));self.reject(h,'reference.load',**dict(r.REFERENCE_EXAMPLE,accept_inert_reference=False))
        for c in r.UNSUPPORTED:self.reject(h,c)
        h.register_principal('a');self.assertEqual(call(h.session('a'),'measurement.micro_solution_accessory')['status'],'unsupported')
    def test_37_external_analysis_rejections_are_atomic(self):
        h=make();self.reject(h,'analysis.baseline',time_s=[0.,0.,1.],heat_flow_w=[0.,1.,2.]);self.reject(h,'analysis.electrical_calibration',known_power_w=.001,plateau_observed_w=0.,baseline_observed_w=1e-6);self.reject(h,'analysis.kinetics',**dict(r.KINETICS_EXAMPLE,heat_flow_w=[0.]*101));self.reject(h,'analysis.kinetics',**dict(r.KINETICS_EXAMPLE,accept_fixed_bath_first_order=False))
    def test_38_all_contract_schemas_and_actual_returns(self):
        h=make();meta=h.describe()
        for key,c in meta['capabilities'].items():
            self.assertEqual(asset_tool.lint_schema(c['input_schema']),[],key);self.assertEqual(asset_tool.lint_schema(c['output_schema']),[],key);self.assertEqual(asset_tool.validate_value(c['example_arguments'],c['input_schema']),[],key)
        for key,args,result in OBSERVED:self.assertEqual(asset_tool.validate_value(result,meta['capabilities'][key]['output_schema']),[],key)
        for key,value in h.read().items():self.assertEqual(asset_tool.validate_value(value,meta['state_slots'][key]['value_schema']),[],key)
    def test_39_session_entrypoints_all_implemented_capabilities(self):
        h=Checked();h.register_principal('eval','evaluator');s=h.session('eval');seen=set()
        plan=[('plant.configure',r.PLANT_EXAMPLE),('sample.load',r.SAMPLE_EXAMPLE),('reference.load',r.REFERENCE_EXAMPLE),('power.set',{'enabled':True}),('thermal.set_temperature',{'temperature_k':298.15}),('sample.set_lift',{'lift_m':0.}),('sim.step',{'dt_s':1.}),('method.configure',r.METHOD_EXAMPLE),('sensor.calibrate',r.CALIBRATION_EXAMPLE),('acquisition.start',{}),('sample.trigger',{}),('calibration.set_power',{'power_w':.0001}),('sim.step',{'dt_s':.2}),('acquisition.stop',{}),('calibration.set_power',{'power_w':0.}),('acquisition.result',{}),('provenance.read',{}),('ledger.read',{}),('analysis.baseline',r.CAPS['analysis.baseline']['example_arguments']),('analysis.electrical_calibration',r.CAPS['analysis.electrical_calibration']['example_arguments']),('analysis.kinetics',r.KINETICS_EXAMPLE),('thermal.disable',{}),('sample.set_lift',{'lift_m':.48}),('sim.step',{'dt_s':1.}),('sample.remove',{}),('power.set',{'enabled':False}),('reference.remove',{})]
        for i,(cap,args) in enumerate(plan):
            out=call(s,cap,args,str(i));self.assertIn(out['status'],('accepted','succeeded'),(cap,out));self.assertEqual(asset_tool.validate_value(out['result'],r.CAPS[cap]['output_schema']),[],cap);seen.add(cap)
        self.assertEqual(seen,set(r.CAPS)-set(r.UNSUPPORTED));self.budget(h)
    def test_40_equilibrium_trigger_and_electrical_lift_guards(self):
        h=make();h.invoke('calibration.set_power',power_w=.001);self.reject(h,'sample.set_lift',lift_m=.48);h.invoke('calibration.set_power',power_w=0.);h.invoke('thermal.set_temperature',temperature_k=310.15);self.reject(h,'acquisition.start');h.invoke('sample.set_lift',lift_m=.48);h.step(.2);self.reject(h,'sample.trigger');self.reject(h,'sample.remove');self.reject(h,'reference.remove')

if __name__=='__main__':unittest.main(verbosity=2)
