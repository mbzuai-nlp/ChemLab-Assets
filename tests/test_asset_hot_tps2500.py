"""CHEM136 numerical, evidence, protocol and atomicity regression tests."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import unittest
import numpy as np
from scipy.integrate import quad
from scripts.asset_runtime import hot_tps2500 as r
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('tps_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py')
asset_tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(asset_tool)
OBSERVED=[]

class Checked(r.HotTps2500Runtime):
    def invoke(self,key,**a):
        before=self.read();out=super().invoke(key,**a);after=self.read()
        changed={k for k in after if after[k]!=before[k]}
        assert not changed-set(r.CAPS[key]['effects']), (key,changed-set(r.CAPS[key]['effects']))
        assert not asset_tool.validate_value(out,r.CAPS[key]['output_schema']), (key,out,asset_tool.validate_value(out,r.CAPS[key]['output_schema']))
        OBSERVED.append((key,copy.deepcopy(a),copy.deepcopy(out)))
        return out

def make(plant=None,sample=None,method=None):
    h=Checked();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,**(plant or {})));h.invoke('probe.configure',**r.PROBE_EXAMPLE);h.invoke('sample.load',**dict(r.SAMPLE_EXAMPLE,**(sample or {})));h.invoke('power.set',enabled=True)
    h.invoke('fixture.set_clamp',closed=True);h.step(2.);h.invoke('fixture.set_hood',opened=False);h.step(12.);h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,**(method or {})));return h

def finish(h):
    h.invoke('acquisition.start');h.step(h.method['duration_s']);return h.invoke('acquisition.result')

def remove(h):
    if h.phase=='running':h.invoke('acquisition.stop')
    h.invoke('fixture.set_hood',opened=True);h.step(12.);h.invoke('fixture.set_clamp',closed=False);h.step(2.);h.invoke('sample.remove')

def sessions(h):
    h.register_principal('student','agent');h.register_principal('teacher','evaluator');h.register_principal('other','agent');return h.session('student'),h.session('teacher'),h.session('other')

def call(session,cap,args=None,request_id='r1'):
    return session.invoke(instance_id=session._host.instance_id,capability_id=cap,arguments=args or {},request_id=request_id)

class TpsTests(unittest.TestCase):
    def test_01_setup_only_targets(self):
        h=Checked();h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('probe.configure',**r.PROBE_EXAMPLE);h.invoke('sample.load',**r.SAMPLE_EXAMPLE)
        before=h.time_s;h.invoke('fixture.set_clamp',closed=True);self.assertEqual(h.clamp,.035);h.step(.5);self.assertAlmostEqual(h.clamp,.025);self.assertEqual(h.time_s,before+.5)
        old=h.read();self.assertRaises(r.ActionRejected,h.invoke,'fixture.set_hood',opened=False);self.assertEqual(h.read(),old)
    def test_02_read_describe_zero_step_pure(self):
        h=make();a,e,_=sessions(h);before=copy.deepcopy(h.__dict__)
        for _ in range(3):a.read(instance_id=h.instance_id);a.describe(instance_id=h.instance_id);e.describe(asset_type_id=r.ASSET_TYPE_ID);h.step(0.)
        self.assertEqual(h.__dict__,before)
    def test_03_real_steel_measurement(self):
        h=make();o=finish(h);self.assertEqual(o['phase'],'succeeded');self.assertEqual(len(o['rows']),61)
        self.assertLess(abs(o['fit']['conductivity_w_m_k']/13.69-1),1e-5);self.assertLess(abs(o['fit']['diffusivity_m2_s']/3.705e-6-1),1e-5)
        self.assertAlmostEqual(o['fit']['volumetric_heat_capacity_j_m3_k'],o['fit']['conductivity_w_m_k']/o['fit']['diffusivity_m2_s']);self.assertIn('HotDisk',o['template_source']);self.assertNotIn('reference_id',o)
    def test_04_template_count_provenance(self):
        lib=r.library();self.assertGreaterEqual(len(lib),50)
        for row in lib.values():
            self.assertTrue(row['source']);self.assertTrue(row['evidence']);self.assertTrue(math.isfinite(row['thermal_conductivity_W_m_K']))
    def test_05_gaussian_kernel_independent_integral(self):
        k=13.69;alpha=3.705e-6;rad=.0064;P=.5;t=1.5;cv=k/alpha
        # Substitute u=v² to remove the heat-kernel endpoint singularity.
        numerical=P/cv*quad(lambda v:1/(math.pi**1.5*math.sqrt(alpha)*(rad*rad+4*alpha*v*v)),0,math.sqrt(t),epsabs=1e-12)[0]
        self.assertAlmostEqual(numerical,float(r.gaussian_plane_rise(t,P,k,alpha,rad)),12)
    def test_06_external_inverse_no_hidden_sample(self):
        h=Checked();args=copy.deepcopy(r.FIT_EXAMPLE);args['temperature_rise_k']=(r.gaussian_plane_rise(args['time_s'],.5,22.,1.2e-5,.0064)+.032).tolist()
        out=h.invoke('analysis.fit',**args);self.assertLess(abs(out['conductivity_w_m_k']/22.-1),1e-7);self.assertLess(abs(out['diffusivity_m2_s']/1.2e-5-1),1e-7);self.assertAlmostEqual(out['offset_k'],.032,7);self.assertIsNone(h.sample)
    def test_07_partition_invariance(self):
        a=make();b=make();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(1.5)
        for dt in [.007,.033,.11,.35,.4,.6]:b.step(dt)
        self.assertEqual(a.rows,b.rows);self.assertEqual(a.fit,b.fit);self.assertAlmostEqual(a._heat_stored,b._heat_stored,14)
    def test_08_energy_mass_conservation_cancel_remove(self):
        h=make();initial_mass=h._mass();h.invoke('acquisition.start');h.step(.5);h.invoke('acquisition.stop');self.assertEqual(h._heat_input,.25);self.assertEqual(h._heat_stored,.25)
        rows=copy.deepcopy(h.rows);h.step(.3);self.assertEqual(h.rows,rows);self.assertEqual(h._heat_input,.25);self.assertEqual(h._mass(),initial_mass)
        remove(h);d=h.invoke('ledger.read');self.assertLess(abs(d['energy_error_j']),1e-14);self.assertLess(abs(d['mass_error_kg']),1e-14);self.assertEqual(d['sample_removed_heat_j'],.25);self.assertEqual(d['sample_present_kg'],0)
    def test_09_cancel_residual_cools(self):
        h=make();h.invoke('acquisition.start');h.step(.5);before=h.read()['truth.sensor_k'];h.invoke('acquisition.stop');h.step(.1)
        self.assertLess(h.read()['truth.sensor_k'],before);self.assertGreater(h.read()['truth.sensor_k'],h.plant['ambient_k']);self.assertEqual(h.phase,'cancelled');self.assertEqual(h._heat_stored,.25)
    def test_10_duration_overshoot_exact_energy(self):
        h=make();h.invoke('acquisition.start');h.step(100.);self.assertEqual(h._heat_input,.75);self.assertEqual(h.elapsed,1.5);self.assertEqual(h.phase,'succeeded');self.assertEqual(len(h.rows),61)
        self.assertIsNone(h.read()['truth.sensor_k']);self.assertIsNone(h.read()['measurement.indicated_k']);self.assertEqual(h.model_fault,'POSTPULSE_FINITE_BOUNDARY_DOMAIN')
    def test_11_small_domain_atomic_rejection(self):
        h=make(method={'duration_s':5.});before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'FINITE_BOUNDARY_DOMAIN',h.invoke,'acquisition.start');self.assertEqual(h.__dict__,before)
        h=make(plant={'maximum_rise_k':.001});before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'SMALL_RISE_DOMAIN',h.invoke,'acquisition.start');self.assertEqual(h.__dict__,before)
    def test_12_sensor_saturation_not_fake_success(self):
        h=make(plant={'sensor_maximum_k':296.151});out=finish(h);self.assertEqual(out['phase'],'failed');self.assertEqual(out['error_code'],'SENSOR_RANGE');self.assertIsNone(out['fit']);self.assertTrue(any(x['temperature_k'] is None for x in out['rows']));self.assertEqual(h._heat_input,.75)
    def test_13_external_calibration(self):
        h=make(plant={'sensor_gain':2.,'sensor_offset_k':.3});h.invoke('sensor.calibrate',gain_correction=.5,offset_correction_k=.3);out=finish(h)
        self.assertLess(abs(out['fit']['conductivity_w_m_k']/13.69-1),1e-5);self.assertLess(abs(out['fit']['diffusivity_m2_s']/3.705e-6-1),1e-5)
    def test_14_reference_temperature_atomic(self):
        h=Checked();h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('probe.configure',**r.PROBE_EXAMPLE);before=copy.deepcopy(h.__dict__)
        self.assertRaisesRegex(r.ActionRejected,'REFERENCE_TEMPERATURE_RANGE',h.invoke,'sample.load',**dict(r.SAMPLE_EXAMPLE,reference_id='nist_sic'));self.assertEqual(h.__dict__,before)
    def test_15_missing_alpha_explicit_completion(self):
        record=r.library()['usgs_quartz'];h=Checked();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,ambient_k=record['reference_temperature_K']));h.invoke('probe.configure',**r.PROBE_EXAMPLE)
        sample=dict(r.SAMPLE_EXAMPLE,reference_id='usgs_quartz');before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'MISSING_REFERENCE_PROPERTY',h.invoke,'sample.load',**sample);self.assertEqual(h.__dict__,before)
        h.invoke('sample.load',**dict(sample,diffusivity_override_m2_s=3e-6,accept_property_completion=True));self.assertTrue(h.sample['completed']);self.assertEqual(h.sample['alpha'],3e-6)
    def test_16_invalid_input_atomic(self):
        h=make();before=copy.deepcopy(h.__dict__)
        for patch in [{'power_w':True},{'power_w':float('nan')},{'bad':1},{'duration_s':.5},{'sample_interval_s':.07},{'temperature_k':300.}]:
            self.assertRaises(r.ActionRejected,h.invoke,'method.configure',**dict(r.METHOD_EXAMPLE,**patch));self.assertEqual(h.__dict__,before)
        for dt in [-1,float('inf'),True,float('nan')]:self.assertRaises(r.ActionRejected,h.step,dt);self.assertEqual(h.__dict__,before)
    def test_17_busy_rejection_keeps_state(self):
        h=make();h.invoke('acquisition.start');h.step(.2);before=copy.deepcopy(h.__dict__)
        for cap,args in [('sample.remove',{}),('fixture.set_clamp',{'closed':False}),('fixture.set_hood',{'opened':True}),('sensor.calibrate',r.CALIBRATION_EXAMPLE),('method.configure',r.METHOD_EXAMPLE)]:
            self.assertRaisesRegex(r.ActionRejected,'BUSY',h.invoke,cap,**args);self.assertEqual(h.__dict__,before)
    def test_18_two_instance_isolation(self):
        a=make();b=make();before=copy.deepcopy(b.__dict__);finish(a);self.assertEqual(b.__dict__,before)
    def test_19_session_host_bound_visibility(self):
        h=make();a,e,o=sessions(h);description=a.describe(instance_id=h.instance_id)
        self.assertNotIn('sample.load',description['capabilities']);self.assertNotIn('sim.step',description['capabilities']);self.assertFalse(any(k.startswith('truth.') for k in description['state_slots']))
        self.assertNotIn('truth.sensor_k',a.read(instance_id=h.instance_id)['observations']);self.assertIn('truth.sensor_k',e.read(instance_id=h.instance_id)['observations'])
        self.assertRaises(r.ActionRejected,a.read,instance_id=h.instance_id,state_ids=['truth.sensor_k']);self.assertRaises(TypeError,a.read,instance_id=h.instance_id,role='evaluator')
        self.assertEqual(call(a,'sim.step',{'dt_s':1.})['error']['code'],'ACCESS_DENIED');self.assertEqual(call(a,'sample.load',r.SAMPLE_EXAMPLE,'r2')['error']['code'],'ACCESS_DENIED');self.assertRaises(r.ActionRejected,h.session,'evaluator')
    def test_20_request_idempotency_and_conflict(self):
        h=make();a,e,_=sessions(h);first=call(a,'acquisition.start');revision=h.revision;self.assertEqual(call(a,'acquisition.start'),first);self.assertEqual(h.revision,revision)
        self.assertEqual(call(a,'power.set',{'enabled':False})['error']['code'],'REQUEST_CONFLICT');self.assertEqual(h.phase,'running')
    def test_21_operation_owner_poll_cancel(self):
        h=make();a,e,o=sessions(h);operation=call(a,'acquisition.start')['operation_id'];h.step(.5)
        self.assertRaises(r.ActionRejected,o.poll,operation);self.assertEqual(call(o,'acquisition.result')['error']['code'],'ACCESS_DENIED');self.assertEqual(call(o,'power.set',{'enabled':False},'r2')['error']['code'],'ACCESS_DENIED')
        before=h.time_s;response=a.cancel(operation,'cancel1');self.assertEqual(h.time_s,before);self.assertEqual(a.cancel(operation,'cancel1'),response);self.assertEqual(a.poll(operation)['status'],'cancelled');self.assertEqual(h._heat_input,.25)
    def test_22_completed_poll_frozen_after_reload(self):
        h=make();a,e,_=sessions(h);operation=call(a,'acquisition.start')['operation_id'];h.step(1.5);old=a.poll(operation);remove(h)
        h.invoke('sample.load',**r.SAMPLE_EXAMPLE);self.assertEqual(a.poll(operation),old);self.assertEqual(h._heat_stored,0);self.assertEqual(h._heat_removed,.75)
    def test_23_power_off_cancel_preserves_energy(self):
        h=make();h.invoke('acquisition.start');h.step(.5);h.invoke('power.set',enabled=False);h.step(1.)
        self.assertEqual(h.phase,'cancelled');self.assertEqual(h.error,'POWER_OFF');self.assertEqual(h._heat_input,.25);self.assertIsNone(h.read()['measurement.indicated_k'])
    def test_24_no_hot_restart(self):
        h=make();finish(h);before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'RELOAD_REQUIRED',h.invoke,'acquisition.start');self.assertEqual(h.__dict__,before)
    def test_25_analysis_ambiguous_rejected(self):
        h=Checked();a=copy.deepcopy(r.FIT_EXAMPLE);a['time_s']=np.linspace(0,1e-5,41).tolist();a['temperature_rise_k']=r.gaussian_plane_rise(a['time_s'],.5,10,1e-6,.0064).tolist()
        self.assertRaisesRegex(r.ActionRejected,'ANALYSIS_UNRESOLVED',h.invoke,'analysis.fit',**a)
        a=copy.deepcopy(r.FIT_EXAMPLE);a['time_s'][3]=a['time_s'][2];self.assertRaisesRegex(r.ActionRejected,'ANALYSIS_DATA',h.invoke,'analysis.fit',**a)
    def test_26_unsupported_hardware_and_oem(self):
        h=make();a,_,_=sessions(h);before=copy.deepcopy(h.__dict__)
        for index,key in enumerate(r.UNSUPPORTED):self.assertEqual(call(a,key,request_id='u'+str(index))['status'],'unsupported')
        self.assertEqual(h.__dict__,before)
    def test_27_metadata_and_all_return_schemas(self):
        metadata=r.export_hot_tps2500_contract_metadata();self.assertEqual(set(metadata['state_slots']),set(r.SLOTS));h=make();values=h.read()
        for key,value in metadata['capabilities'].items():
            self.assertEqual(asset_tool.lint_schema(value['input_schema']),[],key);self.assertEqual(asset_tool.lint_schema(value['output_schema']),[],key);self.assertEqual(asset_tool.validate_value(value['example_arguments'],value['input_schema']),[],key)
        for key,value in values.items():self.assertEqual(asset_tool.validate_value(value,metadata['state_slots'][key]['value_schema']),[],key)
        for key,args,out in OBSERVED:self.assertEqual(asset_tool.validate_value(out,metadata['capabilities'][key]['output_schema']),[],key)
    def test_28_sim_step_real_entry_schema(self):
        h=Checked();out=h.invoke('sim.step',dt_s=.001);self.assertEqual(out,{'time_s':.001});self.assertEqual(h.time_s,.001)
    def test_29_known_alpha_cannot_overwrite(self):
        h=Checked();h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('probe.configure',**r.PROBE_EXAMPLE);before=copy.deepcopy(h.__dict__)
        self.assertRaisesRegex(r.ActionRejected,'REFERENCE_OVERRIDE_FORBIDDEN',h.invoke,'sample.load',**dict(r.SAMPLE_EXAMPLE,diffusivity_override_m2_s=1e-6,accept_property_completion=True));self.assertEqual(h.__dict__,before)
    def test_30_source_provenance_isolation(self):
        h=make();a,e,_=sessions(h);op=call(a,'acquisition.start')['operation_id'];h.step(1.5)
        for response in [call(a,'acquisition.result',request_id='read1'),a.poll(op)]:
            text=json.dumps(response);self.assertNotIn('316L',text);self.assertNotIn('stainless',text);self.assertNotIn('locator',text);self.assertIn('thermal_transport_library.json',text)
        privileged_public=call(e,'acquisition.result');self.assertNotIn('locator',json.dumps(privileged_public));self.assertNotIn('316L',json.dumps(privileged_public));self.assertEqual(privileged_public['result']['template_source'],a.poll(op)['result']['template_source'])
        full=call(e,'provenance.read',request_id='provenance1');self.assertIn('locator',json.dumps(full));self.assertIn('316',json.dumps(full));self.assertEqual(full['result']['reference_id'],'stainless_steel_316l_hotdisk')
        denied=call(a,'provenance.read',request_id='provenance2');self.assertEqual(denied['error']['code'],'ACCESS_DENIED');self.assertNotIn('locator',json.dumps(denied))
        self.assertEqual(call(a,'acquisition.result',request_id='read1')['result']['template_source'],a.poll(op)['result']['template_source'])
    def test_31_manufacturer_ambient_domain(self):
        h=Checked()
        for temperature in [283.15,313.15]:self.assertRaises(r.ActionRejected,h.invoke,'plant.configure',**dict(r.PLANT_EXAMPLE,ambient_k=temperature));self.assertIsNone(h.plant)
    def test_32_sixty_hz_clock_and_sampling(self):
        whole=make();frames=make();whole.invoke('acquisition.start');frames.invoke('acquisition.start');whole.step(1.5)
        before=frames.time_ns
        for _ in range(90):frames.step(1/60)
        self.assertEqual(frames.time_ns-before,1500000000);self.assertEqual(frames.time_ns,whole.time_ns)
        self.assertEqual(frames.rows,whole.rows);self.assertEqual(frames.fit,whole.fit);self.assertEqual(frames.phase,'succeeded')
        self.assertAlmostEqual(frames._heat_input,whole._heat_input,13);self.assertLess(abs(frames._clock_remainder_ns),1e-6)
    def test_33_subnanosecond_accumulation(self):
        h=Checked()
        for _ in range(1000):h.step(1e-12)
        self.assertEqual(h.time_ns,1);self.assertLess(abs(h._clock_remainder_ns),1e-12)
        before=copy.deepcopy(h.__dict__);h.step(0.);self.assertEqual(h.__dict__,before)
    def test_34_sixty_hz_long_clock_no_drift(self):
        h=Checked()
        for _ in range(3600):h.step(1/60)
        self.assertEqual(h.time_ns,60000000000);self.assertLess(abs(h._clock_remainder_ns),.00001)
    def test_35_bridge_evaluator_dispatch_public_is_safe(self):
        h=make();_,e,_=sessions(h);operation=call(e,'acquisition.start')['operation_id'];h.step(1.5)
        for public in [call(e,'acquisition.result',request_id='measure'),e.poll(operation)]:
            serialized=json.dumps(public);self.assertNotIn('316L',serialized);self.assertNotIn('stainless',serialized);self.assertNotIn('locator',serialized);self.assertIn('thermal_transport_library.json',serialized)
        provenance=call(e,'provenance.read',request_id='sources');self.assertEqual(provenance['result']['reference_id'],'stainless_steel_316l_hotdisk');self.assertEqual(provenance['result']['operation_id'],operation)
        self.assertEqual(asset_tool.validate_value(provenance['result'],r.CAPS['provenance.read']['output_schema']),[])
    def test_36_missing_geometry_interlocks(self):
        h=Checked();before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'PRECONDITION_FAILED',h.invoke,'acquisition.start');self.assertEqual(h.__dict__,before)
        h.invoke('plant.configure',**r.PLANT_EXAMPLE);h.invoke('probe.configure',**r.PROBE_EXAMPLE);before=copy.deepcopy(h.__dict__);self.assertRaisesRegex(r.ActionRejected,'SAMPLE_GEOMETRY',h.invoke,'sample.load',**dict(r.SAMPLE_EXAMPLE,diameter_m=.003));self.assertEqual(h.__dict__,before)

if __name__=='__main__':unittest.main()
