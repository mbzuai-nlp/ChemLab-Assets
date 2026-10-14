"""CHEM137 tests: real templates, independent mechanics, ledgers and public protocol."""
import copy,importlib.util,json,math,unittest
from pathlib import Path
import numpy as np
from scipy.integrate import quad
from scripts.asset_runtime import antonpaar_mcr503 as r
ROOT=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('mcr_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');asset_tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(asset_tool)
OBSERVED=[]
class Checked(r.AntonpaarMcr503Runtime):
    def invoke(self,k,**a):
        before=self.read();out=super().invoke(k,**a);after=self.read();changed={x for x in after if after[x]!=before[x]}
        assert not changed-set(r.CAPS[k]['effects']),(k,changed-set(r.CAPS[k]['effects']))
        assert not asset_tool.validate_value(out,r.CAPS[k]['output_schema']),(k,asset_tool.validate_value(out,r.CAPS[k]['output_schema']))
        OBSERVED.append((k,copy.deepcopy(a),copy.deepcopy(out)));return out

def make(plant=None,sample=None,method=None,geometry=None):
    h=Checked();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,**(plant or {})));h.invoke('geometry.configure',**dict(r.GEOMETRY_EXAMPLE,**(geometry or {})));h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,**(sample or {})));h.invoke('power.set',enabled=True);h.invoke('geometry.set_gap',gap_m=.001);h.step(4.);h.invoke('thermal.set_temperature',temperature_k=298.15);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,**(method or {})));return h

def finish(h):h.invoke('acquisition.start');h.step(h.method['duration_s']);return h.invoke('acquisition.result')
def remove(h):
    if h.phase=='running':h.invoke('acquisition.stop')
    h.invoke('geometry.set_gap',gap_m=.08);h.step(4.);h.invoke('sample.remove')
def sessions(h):
    for name,role in [('student','agent'),('teacher','evaluator'),('other','agent')]:h.register_principal(name,role)
    return h.session('student'),h.session('teacher'),h.session('other')
def call(s,c,a=None,rid='r1'):return s.invoke(instance_id=s._host.instance_id,capability_id=c,arguments=a or {},request_id=rid)

class McrTests(unittest.TestCase):
    def budget(self,h):
        d=h.invoke('ledger.read')
        for key,value in d.items():
            if 'error' in key:self.assertLess(abs(value),1e-8,(key,value))
    def test_01_targets_do_not_teleport(self):
        h=make();temp=h.temperature;h.invoke('thermal.set_temperature',temperature_k=310.);self.assertEqual(h.temperature,temp);h.invoke('geometry.set_gap',gap_m=.08);self.assertEqual(h.gap,.001);h.step(.1);self.assertAlmostEqual(h.gap,.003);self.assertGreater(h.temperature,temp)
    def test_02_read_describe_and_zero_are_pure(self):
        h=make();a,e,_=sessions(h);before=copy.deepcopy(h.__dict__)
        for _ in range(2):a.read(instance_id=h.instance_id);a.describe(instance_id=h.instance_id);e.describe(asset_type_id=r.ASSET_TYPE_ID);h.step(0.)
        self.assertEqual(h.__dict__,before)
    def test_03_real_water_viscosity(self):
        h=make();o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertEqual(len(o['rows']),201);self.assertLess(abs(o['mean_apparent_viscosity_pa_s']/r.library()['water']['viscosity_Pa_s']-1),.0002);self.assertIsNone(o['oscillation_fit']);self.assertEqual(h.angle,16.);self.budget(h)
    def test_04_sixtynine_real_references(self):
        lib=r.library();self.assertGreaterEqual(len(lib),50)
        for row in lib.values():self.assertGreater(row['viscosity_Pa_s'],0);self.assertTrue(row['source']);self.assertTrue(row['evidence']);self.assertIsNone(row['storage_modulus_Pa'])
        a=make();b=make(sample={'reference_id':'glycerol'});oa=finish(a);ob=finish(b);self.assertGreater(ob['mean_apparent_viscosity_pa_s']/oa['mean_apparent_viscosity_pa_s'],1000)
    def test_05_newtonian_torque_radial_integral(self):
        eta=.954;rate=100.;R=.0125
        exact=quad(lambda radius:2*math.pi*eta*rate*radius/R*radius**2,0,R,epsabs=1e-15)[0]
        self.assertAlmostEqual(exact,r.pp_torque(rate,R,eta),14)
    def test_06_hb_radial_integral(self):
        radius=.0125;rate=100.;K=.4;n=.7;yield_stress=2.
        numerical=quad(lambda x:2*math.pi*(yield_stress+K*(rate*x/radius)**n)*x*x,0,radius,epsabs=1e-14)[0]
        self.assertAlmostEqual(numerical,float(r.hb_torque(rate,radius,yield_stress,K,n)),13)
    def test_07_external_hb_inverse(self):
        h=Checked();out=h.invoke('analysis.flow_curve',**r.FLOW_EXAMPLE);self.assertAlmostEqual(out['yield_stress_pa'],.2,7);self.assertAlmostEqual(out['consistency_pa_sn'],.3,7);self.assertAlmostEqual(out['flow_index'],.7,7);self.assertIsNone(h.sample)
    def test_08_hb_forward_apparent_viscosity(self):
        h=make(sample={'reference_id':'glycerol','constitutive_model':'herschel_bulkley','yield_stress_pa':.2,'flow_index':.7});o=finish(h);expected=float(r.hb_torque(100,.0125,.2,.954,.7))*2/(math.pi*.0125**3*100)
        self.assertAlmostEqual(o['mean_apparent_viscosity_pa_s'],expected,6);self.assertNotAlmostEqual(expected,.954,2);self.budget(h)
    def test_09_external_oscillation_inverse(self):
        h=Checked();out=h.invoke('analysis.oscillation',**r.OSC_EXAMPLE);self.assertAlmostEqual(out['storage_modulus_pa'],2.,12);self.assertAlmostEqual(out['loss_modulus_pa'],3.,12);self.assertIsNone(h.sample)
    def test_10_newtonian_oscillation(self):
        h=make(sample={'reference_id':'glycerol'},method={'mode':'oscillation'});o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertLess(abs(o['oscillation_fit']['storage_modulus_pa']),1e-5);self.assertAlmostEqual(o['oscillation_fit']['loss_modulus_pa'],.954*2*math.pi*5,5);self.budget(h)
    def test_11_maxwell_gprime_gdoubleprime(self):
        h=make(sample={'reference_id':'glycerol','constitutive_model':'maxwell'},method={'mode':'oscillation'});o=finish(h);u=2*math.pi*5*.05;G=.954/.05
        self.assertAlmostEqual(o['oscillation_fit']['storage_modulus_pa'],G*u*u/(1+u*u),4);self.assertAlmostEqual(o['oscillation_fit']['loss_modulus_pa'],G*u/(1+u*u),4);self.budget(h)
    def test_12_maxwell_startup_time_response(self):
        h=make(sample={'reference_id':'glycerol','constitutive_model':'maxwell'});h.invoke('acquisition.start');self.assertEqual(h.stress,0);h.step(.05);self.assertAlmostEqual(h.stress,.954*100*(1-math.exp(-1)),12);self.assertAlmostEqual(h.angle,.4,12);self.budget(h)
    def test_13_maxwell_work_independent_quadrature(self):
        h=make(sample={'reference_id':'glycerol','constitutive_model':'maxwell'},method={'mode':'oscillation'});h.invoke('acquisition.start');duration=.173
        numerical=quad(lambda t:h._mechanical(t)['torque']*h._mechanical(t)['speed'],0,duration,epsabs=1e-16)[0];h.step(duration);self.assertAlmostEqual(h.mechanical_work,numerical,15);self.assertGreaterEqual(h.viscous_heat,0);self.budget(h)
    def test_14_cancel_preserves_and_relaxes_energy(self):
        h=make(sample={'reference_id':'glycerol','constitutive_model':'maxwell'});h.invoke('acquisition.start');h.step(.05);h.invoke('acquisition.stop');elastic=h.elastic;stress=h.stress;heat=h.viscous_heat;work=h.mechanical_work;rows=copy.deepcopy(h.rows);angle=h.angle;h.step(.05)
        self.assertAlmostEqual(h.stress,stress/math.e,12);self.assertAlmostEqual(h.elastic,elastic/math.e**2,15);self.assertAlmostEqual(h.viscous_heat-heat,elastic-h.elastic,15);self.assertEqual(h.mechanical_work,work);self.assertEqual(h.angle,angle);self.assertEqual(h.rows,rows);self.budget(h)
    def test_15_thermal_control_heat_balance(self):
        h=make();h.invoke('thermal.set_temperature',temperature_k=308.15);h.step(120);self.assertAlmostEqual(h.temperature,308.15,3);self.assertGreater(h.controller_input,1000);self.budget(h)
        h.invoke('thermal.set_temperature',temperature_k=288.15);h.step(180);self.assertAlmostEqual(h.temperature,288.15,3);self.budget(h);h.invoke('thermal.disable');T=h.temperature;h.step(5);self.assertGreater(h.temperature,T);self.budget(h)
    def test_16_thermal_saturation_partition(self):
        a=make(plant={'heater_max_w':2.,'cooler_max_w':1.});b=make(plant={'heater_max_w':2.,'cooler_max_w':1.})
        for h in (a,b):h.invoke('thermal.set_temperature',temperature_k=300.15)
        a.step(600)
        for _ in range(120):b.step(5)
        self.assertAlmostEqual(a.temperature,b.temperature,10);self.assertAlmostEqual(a.controller_input,b.controller_input,8);self.budget(a);self.budget(b)
    def test_17_material_volume_and_remove_energy(self):
        h=make();mass=h._mass();volume=h.sample['volume_m3'];h.invoke('thermal.set_temperature',temperature_k=303.15);h.step(100);h.invoke('geometry.set_gap',gap_m=.08);h.step(4)
        self.assertEqual(h._mass(),mass);self.assertEqual(h.sample['volume_m3'],volume);h.invoke('sample.remove');d=h.invoke('ledger.read');self.assertEqual(d['mass_removed_kg'],mass);self.assertEqual(d['volume_removed_m3'],volume);self.assertGreater(d['sample_removed_heat_j'],0);self.assertEqual(d['mass_present_kg'],0);self.budget(h)
    def test_18_squeeze_and_fill_guards_atomic(self):
        h=make();before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'SQUEEZE_FLOW_UNSUPPORTED',h.invoke,'geometry.set_gap',gap_m=.0005);self.assertEqual(h.__dict__,before)
        h.invoke('geometry.set_gap',gap_m=.002);h.step(1);before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'GAP_FILL_MISMATCH',h.invoke,'acquisition.start');self.assertEqual(h.__dict__,before)
    def test_19_below_torque_range_null(self):
        h=make(method={'shear_rate_s_inv':1e-6});o=finish(h);self.assertEqual(o['phase'],'failed');self.assertEqual(o['error_code'],'TORQUE_BELOW_RANGE');self.assertIsNone(o['mean_apparent_viscosity_pa_s']);self.assertTrue(all(row['torque_nm'] is None for row in o['rows']))
    def test_20_overload_and_angular_speed(self):
        h=make(sample={'reference_id':'glycerol','volume_m3':math.pi*.05**2*.001},geometry={'radius_m':.05},method={'shear_rate_s_inv':3000});before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'TORQUE_OVERLOAD',h.invoke,'acquisition.start');self.assertEqual(h.__dict__,before)
        h=make(method={'shear_rate_s_inv':4000});before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'ANGULAR_SPEED_RANGE',h.invoke,'acquisition.start');self.assertEqual(h.__dict__,before)
    def test_21_250hz_internal_grid(self):
        h=make(sample={'reference_id':'glycerol'},method={'mode':'oscillation','frequency_hz':250.,'duration_s':.04,'sample_interval_s':.0002,'discard_time_s':.02});o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertEqual(len(o['rows']),201);self.assertAlmostEqual(o['oscillation_fit']['loss_modulus_pa'],.954*2*math.pi*250,3)
    def test_22_sampling_and_frequency_rejection(self):
        h=make();before=copy.deepcopy(h.__dict__)
        for patch in [{'frequency_hz':251.},{'mode':'oscillation','frequency_hz':250.},{'sample_interval_s':.03},{'discard_time_s':2.}]:self.assertRaises(r.ActionRejected,h.invoke,'method.configure',**dict(r.METHOD_EXAMPLE,**patch));self.assertEqual(h.__dict__,before)
    def test_23_no_temperature_extrapolation(self):
        h=make(sample={'reference_id':'benzoic_acid'});before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'REFERENCE_TEMPERATURE_RANGE',h.invoke,'acquisition.start');self.assertEqual(h.__dict__,before)
    def test_24_reference_drift_stops_at_sample(self):
        h=make(plant={'ambient_k':303.15},method={'duration_s':10.,'sample_interval_s':.05});h.step(150);self.assertLess(abs(h.temperature-298.15),.001);h.invoke('thermal.disable');h.invoke('acquisition.start');h.step(10.)
        self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'REFERENCE_TEMPERATURE_RANGE');self.assertLess(h.elapsed,10.);self.assertLess(len(h.rows),201);self.assertEqual(h.speed,0);self.budget(h)
    def test_25_independent_calibration(self):
        h=make(plant={'torque_gain':2.,'torque_offset_nm':1e-7});h.invoke('sensor.calibrate',torque_gain_correction=.5,torque_zero_nm=1e-7);o=finish(h);self.assertLess(abs(o['mean_apparent_viscosity_pa_s']/.0008949-1),.0002)
    def test_26_invalid_inputs_atomic(self):
        h=make();before=copy.deepcopy(h.__dict__)
        for patch in [{'strain_amplitude':True},{'shear_rate_s_inv':float('nan')},{'bad':1}]:self.assertRaises(r.ActionRejected,h.invoke,'method.configure',**dict(r.METHOD_EXAMPLE,**patch));self.assertEqual(h.__dict__,before)
        for dt in [-1,float('inf'),float('nan'),True]:self.assertRaises(r.ActionRejected,h.step,dt);self.assertEqual(h.__dict__,before)
    def test_27_busy_preconditions_atomic(self):
        h=make();h.invoke('acquisition.start');h.step(.1);before=copy.deepcopy(h.__dict__)
        for cap,args in [('sample.remove',{}),('geometry.set_gap',{'gap_m':.08}),('thermal.set_temperature',{'temperature_k':300.}),('method.configure',r.METHOD_EXAMPLE)]:self.assertRaisesRegex(r.ActionRejected,'BUSY',h.invoke,cap,**args);self.assertEqual(h.__dict__,before)
    def test_28_double_instance_isolation(self):
        a=make();b=make();before=copy.deepcopy(b.__dict__);finish(a);self.assertEqual(b.__dict__,before)
    def test_29_float_clock_partition_rows(self):
        a=make();b=make();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(2.)
        for _ in range(120):b.step(1/60)
        self.assertEqual(a.time_ns,b.time_ns);self.assertEqual(a.rows,b.rows);self.assertEqual(a.angle,b.angle);self.assertAlmostEqual(a.mechanical_work,b.mechanical_work,14);self.budget(b)
        c=Checked()
        for _ in range(3600):c.step(1/60)
        self.assertEqual(c.time_ns,60000000000)
    def test_30_identity_permissions(self):
        h=make();a,e,o=sessions(h);self.assertNotIn('sample.load',a.describe(instance_id=h.instance_id)['capabilities']);self.assertNotIn('truth.viscosity_pa_s',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.viscosity_pa_s',e.read(instance_id=h.instance_id)['observations']);self.assertRaises(r.ActionRejected,a.read,instance_id=h.instance_id,state_ids=['truth.viscosity_pa_s']);self.assertEqual(call(a,'sim.step',{'dt_s':1.})['error']['code'],'ACCESS_DENIED');self.assertRaises(TypeError,a.read,instance_id=h.instance_id,role='evaluator')
    def test_31_idempotency_owner_cancel(self):
        h=make();a,e,o=sessions(h);start=call(a,'acquisition.start');revision=h.revision;self.assertEqual(call(a,'acquisition.start'),start);self.assertEqual(h.revision,revision);self.assertEqual(call(a,'power.set',{'enabled':False})['error']['code'],'REQUEST_CONFLICT');op=start['operation_id'];h.step(.5);self.assertRaises(r.ActionRejected,o.poll,op);self.assertEqual(call(o,'acquisition.result')['error']['code'],'ACCESS_DENIED');cancel=a.cancel(op,'cancel');self.assertEqual(a.cancel(op,'cancel'),cancel);self.assertEqual(a.poll(op)['status'],'cancelled')
    def test_32_privileged_public_provenance_is_safe(self):
        h=make();a,e,_=sessions(h);op=call(e,'acquisition.start')['operation_id'];h.step(2.)
        for data in [call(e,'acquisition.result',rid='result'),e.poll(op)]:text=json.dumps(data);self.assertNotIn('Viscosity',text);self.assertNotIn('PubChem',text);self.assertNotIn('locator',text);self.assertIn('rheology_library.json',text)
        raw=call(e,'provenance.read',rid='provenance');self.assertEqual(raw['result']['reference_id'],'water');self.assertTrue(raw['result']['template_source']);self.assertEqual(call(a,'provenance.read')['error']['code'],'ACCESS_DENIED')
    def test_33_poweroff_and_archive_residue(self):
        h=make(sample={'reference_id':'glycerol','constitutive_model':'maxwell'});a,e,_=sessions(h);op=call(a,'acquisition.start')['operation_id'];h.step(.1);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertGreater(h.elastic,0);old=a.poll(op);h.step(1.);self.assertEqual(a.poll(op),old);self.assertFalse(h.thermal_enabled);self.budget(h)
    def test_34_restart_residual_maxwell(self):
        h=make(sample={'reference_id':'glycerol','constitutive_model':'maxwell'});h.invoke('acquisition.start');h.step(.02);h.invoke('acquisition.stop');initial=h.stress;h.invoke('acquisition.start');h.step(.01);expected=.954*100+(initial-.954*100)*math.exp(-.01/.05);self.assertAlmostEqual(h.stress,expected,11);self.budget(h)
    def test_35_model_acceptance_and_unsupported(self):
        h=Checked();h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('geometry.configure',**r.GEOMETRY_EXAMPLE);before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'ASSUMPTION_NOT_ACCEPTED',h.invoke,'sample.load',**dict(r.SAMPLE_EXAMPLE,accept_engineering_rheology=False));self.assertEqual(h.__dict__,before)
        h=make();a,_,_=sessions(h)
        for index,key in enumerate(r.UNSUPPORTED):self.assertEqual(call(a,key,rid='u'+str(index))['status'],'unsupported')
        h=make(sample={'constitutive_model':'herschel_bulkley'},method={'mode':'oscillation'});self.assertRaisesRegex(r.ActionRejected,'HB_OSCILLATION_UNSUPPORTED',h.invoke,'acquisition.start')
    def test_36_all_metadata_and_real_output_schemas(self):
        meta=r.export_antonpaar_mcr503_contract_metadata();self.assertEqual(set(meta['state_slots']),set(r.SLOTS))
        for key,cap in meta['capabilities'].items():self.assertEqual(asset_tool.lint_schema(cap['input_schema']),[],key);self.assertEqual(asset_tool.lint_schema(cap['output_schema']),[],key);self.assertEqual(asset_tool.validate_value(cap['example_arguments'],cap['input_schema']),[],key)
        self.assertNotIn('含一阶响应',meta['state_slots']['thermal.indicated_k']['meaning_zh']);self.assertNotIn('含一阶响应',meta['state_slots']['torque.indicated_nm']['meaning_zh']);self.assertNotIn('样品瓶',meta['state_slots']['truth.temperature_k']['meaning_zh']);self.assertIn('thermal.set_temperature',meta['state_slots']['thermal.target_k']['meaning_zh'])
        h=make();h.invoke('sim.step',dt_s=.01);h.invoke('provenance.read')
        for key,args,result in OBSERVED:self.assertEqual(asset_tool.validate_value(result,meta['capabilities'][key]['output_schema']),[],key)
        for key,value in h.read().items():self.assertEqual(asset_tool.validate_value(value,meta['state_slots'][key]['value_schema']),[],key)

    def test_37_real_host_session_all_implemented_entries(self):
        h=Checked();h.register_principal('host','evaluator');session=h.session('host')
        actions=[('plant.configure',r.PLANT_EXAMPLE),('geometry.configure',r.GEOMETRY_EXAMPLE),('sample.load',r.SAMPLE_EXAMPLE),('power.set',{'enabled':True}),('geometry.set_gap',{'gap_m':.001}),('sim.step',{'dt_s':4.}),('thermal.set_temperature',{'temperature_k':298.15}),('method.configure',r.METHOD_EXAMPLE),('sensor.calibrate',r.CALIBRATION_EXAMPLE),('acquisition.start',{}),('sim.step',{'dt_s':.5}),('acquisition.result',{}),('provenance.read',{}),('acquisition.stop',{}),('ledger.read',{}),('analysis.flow_curve',r.FLOW_EXAMPLE),('analysis.oscillation',r.OSC_EXAMPLE),('thermal.disable',{}),('geometry.set_gap',{'gap_m':.08}),('sim.step',{'dt_s':4.}),('sample.remove',{}),('power.set',{'enabled':False})]
        for index,(cap,args) in enumerate(actions):
            response=call(session,cap,args,str(index));self.assertIn(response['status'],['succeeded','accepted'],(cap,response));self.assertEqual(asset_tool.validate_value(response['result'],r.CAPS[cap]['output_schema']),[],cap)
        self.assertEqual(set(k for k,_ in actions),set(r.CAPS)-set(r.UNSUPPORTED));self.budget(h)

    def test_38_multiturn_pose_representation(self):
        h=make();finish(h);first=h.read();self.assertAlmostEqual(first['rotor.angle_rad'],16.,12)
        self.assertAlmostEqual(first['rotor.orientation_rad'],math.atan2(math.sin(16.),math.cos(16.)),12);self.assertGreater(first['rotor.angle_rad'],4*math.pi)
        finish(h);second=h.read();self.assertAlmostEqual(second['rotor.angle_rad'],32.,12)
        self.assertAlmostEqual(math.sin(second['rotor.orientation_rad']),math.sin(second['rotor.angle_rad']),12);self.assertAlmostEqual(math.cos(second['rotor.orientation_rad']),math.cos(second['rotor.angle_rad']),12)
        self.assertGreaterEqual(second['rotor.orientation_rad'],-math.pi);self.assertLessEqual(second['rotor.orientation_rad'],math.pi)
        self.assertEqual(second['rotor.speed_rad_s'],0.);self.budget(h)

if __name__=='__main__':unittest.main()
