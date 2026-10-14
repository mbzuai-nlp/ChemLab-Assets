"""DMA 5002 actual protocol, physical evolution, provenance and conservation."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts')); sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime import antonpaar_dma5002 as rt
from asset_tool import lint_schema,validate_value
class AntonpaarDma5002Tests(unittest.TestCase):
    def setUp(self):
        self.h=rt.AntonpaarDma5002Runtime(); self.agent=self.h.session()
        self.h.register_principal('evaluator',visibility='evaluator'); self.ev=self.h.session('evaluator')
    def ready(self,bubbles=0,contamination=0):
        self.h.invoke('power.set',enabled=True)
        self.h.invoke('sample.configure',substance_id='ethanol',volume_m3=1e-6,bubble_fraction=bubbles,contamination_fraction=contamination)
    def start(self,cap='acquisition.start',args=None):
        return self.agent.invoke(instance_id=self.h.instance_id,capability_id=cap,arguments=args or {},request_id=str(self.h._operation_count)+'start')
    def test_real_templates(self):
        self.assertGreaterEqual(len(self.h.templates),50)
        for t in self.h.templates.values():
            self.assertEqual(t['evidence'],'measured_literature'); self.assertEqual(t['unit'],'kg/m^3')
            self.assertTrue(t['source'].startswith('https://pubchem.ncbi.nlm.nih.gov/')); self.assertIsNotNone(t['temperature_k'])
            self.assertIsNone(t['thermal_expansion_per_k']); self.assertIsNone(t['pressure_pa'])
        self.assertEqual(self.h.templates['ethanol']['value'],789.3)
    def test_setpoints_and_reads_do_not_advance(self):
        t=self.h.temperature_k; self.h.invoke('thermal.set_temperature',temperature_k=303.15)
        self.assertEqual(t,self.h.temperature_k); self.assertEqual(self.h.time_s,0)
        self.h.invoke('power.set',enabled=True); self.h.step(5)
        self.assertGreater(self.h.temperature_k,t); self.assertLess(self.h.temperature_k,303.15)
        before=copy.deepcopy(self.h.__dict__)
        for _ in range(3): self.agent.read(instance_id=self.h.instance_id); self.agent.describe(instance_id=self.h.instance_id); self.h.invoke('density.measure')
        self.assertEqual(before,self.h.__dict__)
    def test_atomic_reject(self):
        for cap,args in [('acquisition.start',{}),('power.set',{'enabled':1}),('thermal.set_temperature',{'temperature_k':math.nan}),
            ('thermal.set_temperature',{'temperature_k':374}),('method.configure',{'mode':'brix'}),('cleaning.start',{'volume_m3':1e-5}),
            ('power.set',{'enabled':True,'visibility':'evaluator'}),('calibration.check',{'reference_density_kg_m3':789.3,'reference_temperature_k':293.15})]:
            before=copy.deepcopy(self.h.__dict__)
            with self.assertRaises(rt.ActionRejected): self.h.invoke(cap,**args)
            self.assertEqual(before,self.h.__dict__)
        for dt in [0,-1,True,math.nan,math.inf]:
            before=copy.deepcopy(self.h.__dict__)
            with self.assertRaises(ValueError): self.h.step(dt)
            self.assertEqual(before,self.h.__dict__)
    def test_completed_reference_measurement(self):
        self.ready(); r=self.start(); self.assertEqual(r['status'],'accepted'); self.assertEqual(self.agent.poll(r['operation_id'])['status'],'running')
        self.assertIsNone(self.h.invoke('density.measure')['density_kg_m3']); self.h.step(35)
        r=self.h.invoke('density.measure'); self.assertEqual(r['phase'],'succeeded'); self.assertAlmostEqual(r['density_kg_m3'],789.3)
        self.assertEqual(r['evidence'],'derived'); self.assertEqual(self.h.elapsed_s,30); self.assertEqual(self.h.time_s,35)
        self.assertAlmostEqual(self.h.invoke('calibration.check',reference_density_kg_m3=789.3,reference_temperature_k=293.15)['deviation_kg_m3'],0)
    def test_temperature_interlock_and_synthetic_response(self):
        self.ready(); self.h.invoke('thermal.set_temperature',temperature_k=303.15)
        with self.assertRaises(rt.ActionRejected): self.h.invoke('acquisition.start')
        self.h.step(180); self.start(); self.h.step(30)
        r=self.h.invoke('density.measure'); self.assertEqual(r['evidence'],'synthetic'); self.assertLess(r['density_kg_m3'],789.3)
    def test_bubble_and_contamination_response(self):
        self.ready(.02,.01); self.start(); self.h.step(30); r=self.h.invoke('density.measure')
        self.assertTrue(r['bubble_warning']); self.assertEqual(r['quality'],'suspect'); self.assertLess(r['density_kg_m3'],789.3)
    def test_cancel_preserves_partial_cleaning_and_recover(self):
        self.ready(); r=self.start('cleaning.start',{'volume_m3':1e-5}); self.h.step(10)
        self.assertGreater(self.h.waste_kg,0); before=self.h.debug_snapshot()
        self.assertEqual(self.agent.cancel(r['operation_id'],'cancel')['status'],'cancelled'); self.h.step(15)
        self.assertEqual(before['state']['sample.cell_kg'],self.h.cell_kg)
        self.start('cleaning.start',{'volume_m3':1e-5}); self.h.step(80)
        self.assertEqual(self.h.cell_kg,0); self.assertEqual(self.h.cleaner_remaining_kg,0)
        self.assertAlmostEqual(self.h.input_kg,self.h.waste_kg,places=14); self.assertAlmostEqual(self.h.cleaner_input_kg,self.h.cleaner_waste_kg,places=14)
        self.h.invoke('sample.configure',substance_id='ethyl_acetate',volume_m3=1e-6,bubble_fraction=0,contamination_fraction=0)
        self.assertAlmostEqual(self.h.input_kg,self.h.cell_kg+self.h.waste_kg,places=14)
    def test_cleaning_conservation_at_every_step(self):
        self.ready(); self.start('cleaning.start',{'volume_m3':1e-5})
        for dt in [1,3,7,15,100]:
            self.h.step(dt)
            self.assertAlmostEqual(self.h.input_kg,self.h.cell_kg+self.h.waste_kg,places=14)
            self.assertAlmostEqual(self.h.cleaner_input_kg,self.h.cleaner_remaining_kg+self.h.cleaner_waste_kg,places=14)
        self.assertEqual(self.h.phase,'succeeded'); self.assertFalse(self.h.read()['fill.detected'])
    def test_principal_instance_truth_isolation_and_retry(self):
        self.ready(); other=rt.AntonpaarDma5002Runtime('other'); before=copy.deepcopy(other.__dict__)
        r=self.start(); self.h.step(3); self.assertEqual(before,other.__dict__)
        desc=self.agent.describe(instance_id=self.h.instance_id)
        self.assertNotIn('sample.configure',desc['capabilities']); self.assertNotIn('sample.cell_kg',desc['state_slots'])
        with self.assertRaises(rt.ActionRejected): self.agent.read(instance_id=self.h.instance_id,state_ids=['sample.cell_kg'])
        with self.assertRaises(TypeError): self.agent.read(instance_id=self.h.instance_id,visibility='evaluator')
        self.h.register_principal('other'); other_session=self.h.session('other')
        with self.assertRaises(rt.ActionRejected): other_session.poll(r['operation_id'])
        self.assertNotIn('ethanol',json.dumps(self.agent.read(instance_id=self.h.instance_id)))
        kw=dict(instance_id=self.h.instance_id,capability_id='power.set',arguments={'enabled':False},request_id='off')
        r=self.agent.invoke(**kw); revision=self.h.revision; self.assertEqual(r,self.agent.invoke(**kw)); self.assertEqual(self.h.revision,revision)
        kw['arguments']={'enabled':True}; self.assertEqual(self.agent.invoke(**kw)['error']['code'],'REQUEST_CONFLICT')
    def test_running_and_phase_interlocks(self):
        self.ready(); self.start()
        for cap,args in [('thermal.set_temperature',{'temperature_k':303.15}),('cleaning.start',{'volume_m3':1e-5}),('method.configure',{'mode':'density'})]:
            before=copy.deepcopy(self.h.__dict__)
            with self.assertRaises(rt.ActionRejected): self.h.invoke(cap,**args)
            self.assertEqual(before,self.h.__dict__)
        self.h.invoke('power.set',enabled=False); self.h.step(50); self.assertEqual(self.h.phase,'cancelled')
    def test_all_actual_schemas(self):
        meta=rt.export_antonpaar_dma5002_contract_metadata()
        for cap in meta['capabilities'].values():
            self.assertEqual(lint_schema(cap['input_schema'],'input'),[]); self.assertEqual(lint_schema(cap['output_schema'],'output'),[])
        self.ready(); self.start(); self.h.step(30)
        cases=[('density.measure',{}),('calibration.check',{'reference_density_kg_m3':789.3,'reference_temperature_k':293.15}),
          ('power.set',{'enabled':True}),('thermal.set_temperature',{'temperature_k':293.15}),('method.configure',{'mode':'density'}),
          ('cleaning.start',{'volume_m3':1e-5}),('acquisition.stop',{})]
        for cap,args in cases:
            result=self.h.invoke(cap,**args); self.assertEqual(validate_value(result,meta['capabilities'][cap]['output_schema']),[])
        for key,value in self.h.read('evaluator').items(): self.assertEqual(validate_value(value,meta['state_slots'][key]['value_schema']),[])
if __name__=='__main__': unittest.main()
