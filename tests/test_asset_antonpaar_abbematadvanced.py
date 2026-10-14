"""Abbemat 5201: real protocol, calibration specificity, optics and ledger tests."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'scripts')); sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime import antonpaar_abbematadvanced as rt
from asset_tool import lint_schema,validate_value
class AntonpaarAbbematadvancedTests(unittest.TestCase):
    def setUp(self):
        self.h=rt.AntonpaarAbbematadvancedRuntime(); self.agent=self.h.session(); self.h.register_principal('evaluator',visibility='evaluator'); self.ev=self.h.session('evaluator')
    def ready(self,fractions=None):
        h=self.h; h.invoke('power.set',enabled=True); h.invoke('lid.set_angle',angle_rad=math.pi/2)
        h.invoke('sample.configure',volume_fractions=fractions or {'ethanol':1.},volume_m3=100e-9,matrix_id='ethanol',contamination_fraction=0.)
        h.invoke('lid.set_angle',angle_rad=0.)
    def start(self,cap='acquisition.start',args=None):
        return self.agent.invoke(instance_id=self.h.instance_id,capability_id=cap,arguments=args or {},request_id='start-'+str(self.h._operation_count))
    def calibration(self,matrix='ethanol',temperature=293.15):
        return dict(matrix_id=matrix,temperature_k=temperature,points=[{'index':1.35,'mass_fraction':0.},{'index':1.38,'mass_fraction':1.}],source='synthetic test calibration, not experiment')
    def test_setpoints_are_not_temperature(self):
        self.h.invoke('thermal.set_temperature',temperature_k=313.15); self.assertEqual(self.h.temperature_k,293.15); self.assertEqual(self.h.time_s,0)
        self.h.invoke('power.set',enabled=True); self.h.step(10); self.assertGreater(self.h.temperature_k,293.15); self.assertLess(self.h.temperature_k,313.15)
    def test_reads_and_describe_pure_detached(self):
        self.ready(); self.start(); self.h.step(4); before=copy.deepcopy(self.h.__dict__)
        for _ in range(3):
            d=self.agent.describe(instance_id=self.h.instance_id); d['capabilities'].clear(); self.agent.read(instance_id=self.h.instance_id); self.h.invoke('refractometry.measure')
        self.assertEqual(before,self.h.__dict__)
    def test_rejections_atomic(self):
        for cap,args in [('acquisition.start',{}),('power.set',{'enabled':1}),('thermal.set_temperature',{'temperature_k':math.nan}),
            ('lid.set_angle',{'angle_rad':2}),('method.configure',{'mode':'brix'}),('power.set',{'enabled':True,'visibility':'evaluator'})]:
            before=copy.deepcopy(self.h.__dict__)
            with self.assertRaises(rt.ActionRejected): self.h.invoke(cap,**args)
            self.assertEqual(before,self.h.__dict__)
        for dt in [0,-1,True,math.inf,math.nan]:
            with self.assertRaises(ValueError): self.h.step(dt)
    def test_reference_and_async_completion(self):
        self.ready(); response=self.start(); self.assertEqual(response['status'],'accepted')
        self.assertIsNone(self.h.invoke('refractometry.measure')['index']); self.assertEqual(self.agent.poll(response['operation_id'])['status'],'running')
        self.h.step(15); result=self.h.invoke('refractometry.measure')
        self.assertEqual(result['phase'],'succeeded'); self.assertEqual(self.h.elapsed_s,10); self.assertEqual(self.h.time_s,15)
        self.assertAlmostEqual(result['index'],1.3611); self.assertEqual(result['evidence'],'derived'); self.assertIsNone(result['mass_fraction'])
    def test_calibration_specificity_not_universal_brix(self):
        self.ready(); self.h.invoke('method.configure',mode='calibrated_concentration')
        with self.assertRaises(rt.ActionRejected): self.h.invoke('acquisition.start')
        for matrix,temp in [('wrong_matrix',293.15),('ethanol',303.15)]:
            self.h.invoke('calibration.configure',**self.calibration(matrix,temp))
            with self.assertRaises(rt.ActionRejected): self.h.invoke('acquisition.start')
        self.h.invoke('calibration.configure',**self.calibration()); self.start(); self.h.step(10)
        r=self.h.invoke('refractometry.measure'); self.assertAlmostEqual(r['mass_fraction'],.37); self.assertEqual(r['concentration_evidence'],'user_supplied_calibration')
    def test_invalid_calibration_arrays_and_monotonicity(self):
        args=self.calibration()
        variants=[[],{},[{'index':1.35,'mass_fraction':0.}],
           [{'index':1.35,'mass_fraction':0.},{'index':1.35,'mass_fraction':1.}],
           [{'index':1.35,'mass_fraction':0.},{'index':1.38,'mass_fraction':math.nan}],
           [{'index':1.35,'mass_fraction':0.},{'index':1.36,'mass_fraction':.8},{'index':1.38,'mass_fraction':.2}]]
        for points in variants:
            before=copy.deepcopy(self.h.__dict__); args['points']=points
            with self.assertRaises(rt.ActionRejected): self.h.invoke('calibration.configure',**args)
            self.assertEqual(before,self.h.__dict__)
    def test_mixture_is_lorentz_lorenz_and_synthetic(self):
        self.ready({'ethanol':.5,'methanol':.5}); self.start(); self.h.step(10); result=self.h.invoke('refractometry.measure')
        nums=[self.h.templates[s]['value'] for s in ('ethanol','methanol')]
        ll=sum((n*n-1)/(n*n+2) for n in nums)/2; expected=math.sqrt((1+2*ll)/(1-ll))
        self.assertAlmostEqual(result['index'],expected,places=6); self.assertEqual(result['evidence'],'synthetic')
    def test_thermal_interlock_and_off_reference(self):
        self.ready(); self.h.invoke('thermal.set_temperature',temperature_k=303.15)
        with self.assertRaises(rt.ActionRejected): self.h.invoke('acquisition.start')
        self.h.step(200); self.start(); self.h.step(10); r=self.h.invoke('refractometry.measure')
        self.assertLess(r['index'],1.3611); self.assertEqual(r['evidence'],'synthetic')
    def test_cleaning_species_volume_conservation_cancel_recovery(self):
        self.ready({'ethanol':.5,'methanol':.5}); self.h.invoke('lid.set_angle',angle_rad=math.pi/2)
        response=self.start('cleaning.start',{'volume_m3':1e-6}); self.h.step(5)
        self.assertGreater(sum(self.h.waste_species.values()),0)
        self.agent.cancel(response['operation_id'],'cancel'); before=copy.deepcopy(self.h.remaining); self.h.step(5); self.assertEqual(self.h.remaining,before)
        self.start('cleaning.start',{'volume_m3':1e-6})
        for dt in [1,3,8,40]:
            self.h.step(dt)
            for sid,v in self.h.initial_species.items(): self.assertAlmostEqual(v,self.h.remaining.get(sid,0)+self.h.waste_species[sid],places=17)
            self.assertAlmostEqual(self.h.cleaner_input_m3,self.h.cleaner_remaining_m3+self.h.cleaner_waste_m3,places=17)
        self.assertFalse(self.h.read()['sample.filled']); self.assertEqual(self.h.phase,'succeeded')
    def test_lid_and_operation_interlocks_power_loss(self):
        self.ready(); self.start()
        for cap,args in [('lid.set_angle',{'angle_rad':1.5}),('calibration.configure',self.calibration()),('cleaning.start',{'volume_m3':1e-6})]:
            before=copy.deepcopy(self.h.__dict__)
            with self.assertRaises(rt.ActionRejected): self.h.invoke(cap,**args)
            self.assertEqual(before,self.h.__dict__)
        self.h.invoke('power.set',enabled=False); self.h.step(15); self.assertEqual(self.h.phase,'cancelled'); self.assertIsNone(self.h.result)
    def test_instance_principal_truth_isolation_retry(self):
        self.ready(); other=rt.AntonpaarAbbematadvancedRuntime('other'); before=copy.deepcopy(other.__dict__); response=self.start(); self.h.step(2); self.assertEqual(before,other.__dict__)
        desc=self.agent.describe(instance_id=self.h.instance_id); self.assertNotIn('sample.configure',desc['capabilities']); self.assertNotIn('sample.remaining_m3',desc['state_slots'])
        with self.assertRaises(rt.ActionRejected): self.agent.read(instance_id=self.h.instance_id,state_ids=['sample.remaining_m3'])
        with self.assertRaises(TypeError): self.agent.read(instance_id=self.h.instance_id,visibility='evaluator')
        self.h.register_principal('other'); s=self.h.session('other')
        with self.assertRaises(rt.ActionRejected): s.poll(response['operation_id'])
        self.assertNotIn('ethanol',json.dumps(self.agent.read(instance_id=self.h.instance_id)))
        kw=dict(instance_id=self.h.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='off')
        result=self.agent.invoke(**kw); revision=self.h.revision; self.assertEqual(result,self.agent.invoke(**kw)); self.assertEqual(revision,self.h.revision)
        kw['arguments']={'enabled':True}; self.assertEqual(self.agent.invoke(**kw)['error']['code'],'REQUEST_CONFLICT')
    def test_actual_result_schemas_and_unavailable_quality(self):
        meta=rt.export_antonpaar_abbematadvanced_contract_metadata()
        for cap in meta['capabilities'].values(): self.assertEqual(lint_schema(cap['input_schema']),[]); self.assertEqual(lint_schema(cap['output_schema']),[])
        self.assertEqual(self.agent.read(instance_id=self.h.instance_id)['observations']['optical.indicated_index']['quality'],'unavailable')
        self.ready(); self.start(); self.h.step(10)
        for cap,args in [('refractometry.measure',{}),('calibration.configure',self.calibration()),('lid.set_angle',{'angle_rad':math.pi/2}),('cleaning.start',{'volume_m3':1e-6}),('acquisition.stop',{})]:
            result=self.h.invoke(cap,**args); self.assertEqual(validate_value(result,meta['capabilities'][cap]['output_schema']),[])
        for key,v in self.h.read('evaluator').items(): self.assertEqual(validate_value(v,meta['state_slots'][key]['value_schema']),[])
    def test_templates_have_temperature_and_D_line(self):
        self.assertGreaterEqual(sum(t['runtime_eligible'] for t in self.h.templates.values()),50)
        for t in self.h.templates.values():
            self.assertEqual(t['evidence'],'measured_literature'); self.assertEqual(t['wavelength_m'],589.3e-9); self.assertIsNone(t['dn_dt_per_k']); self.assertTrue(t['source'].startswith('https://pubchem.ncbi.nlm.nih.gov/'))
if __name__=='__main__': unittest.main()
