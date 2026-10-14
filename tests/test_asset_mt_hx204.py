"""HX204 actual API, physical evolution and isolation regression tests."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime import mt_hx204 as rt
from asset_tool import lint_schema, validate_value

class MtHx204Tests(unittest.TestCase):
    def setUp(self):
        self.h=rt.MtHx204Runtime()
        self.h.register_principal('evaluator',visibility='evaluator')
        self.agent=self.h.session(); self.evaluator=self.h.session('evaluator')

    def ready(self, mode='standard', basis='wet', duration=60):
        h=self.h
        h.invoke('power.set',enabled=True); h.invoke('lid.set_position',position_m=.1085)
        h.invoke('sample.configure',species_kg={'ethanol':.001,'methanol':.002},residue_kg=.004)
        h.invoke('lid.set_position',position_m=0.)
        h.invoke('method.configure',mode=mode,duration_s=duration,basis=basis)

    def start(self):
        return self.agent.invoke(instance_id=self.h.instance_id,capability_id='acquisition.start',arguments={},request_id='start_'+str(self.h._operation_count+1))

    def test_targets_do_not_advance_process(self):
        before=self.h.temperature_k
        self.h.invoke('thermal.set_temperature',temperature_k=400.15)
        self.assertEqual(self.h.time_s,0); self.assertEqual(self.h.temperature_k,before)
        self.assertEqual(self.h.target_k,400.15)

    def test_read_describe_measure_are_pure_and_detached(self):
        self.ready(); self.start(); self.h.step(3)
        before=copy.deepcopy(self.h.__dict__)
        for _ in range(2):
            self.agent.read(instance_id=self.h.instance_id); d=self.agent.describe(instance_id=self.h.instance_id)
            m=self.h.invoke('moisture.measure'); m['curve'].clear(); d['capabilities'].clear()
        self.assertEqual(self.h.__dict__,before)

    def test_invalid_inputs_and_rejected_preconditions_are_atomic(self):
        for cap,args in [('acquisition.start',{}),('lid.set_position',{'position_m':.1}),
                         ('power.set',{'enabled':1}),('thermal.set_temperature',{'temperature_k':313.5}),
                         ('thermal.set_temperature',{'temperature_k':math.nan}),('power.set',{'enabled':True,'extra':1}),
                         ('method.configure',{'mode':'standard','duration_s':True,'basis':'wet'})]:
            before=copy.deepcopy(self.h.__dict__)
            with self.assertRaises(rt.ActionRejected): self.h.invoke(cap,**args)
            self.assertEqual(before,self.h.__dict__)
        for dt in [0,-1,True,math.inf,math.nan]:
            before=copy.deepcopy(self.h.__dict__)
            with self.assertRaises(ValueError): self.h.step(dt)
            self.assertEqual(before,self.h.__dict__)

    def test_sample_range_and_unknown_species_are_atomic(self):
        self.ready(); self.h.invoke('lid.set_position',position_m=.1085)
        for args in [{'species_kg':{'unknown_secret':.001},'residue_kg':0},
                     {'species_kg':{'ethanol':.201},'residue_kg':.001},
                     {'species_kg':{},'residue_kg':0}]:
            before=self.h.debug_snapshot()
            with self.assertRaisesRegex(rt.ActionRejected,'INVALID_ARGUMENT'): self.h.invoke('sample.configure',**args)
            self.assertEqual(before,self.h.debug_snapshot())

    def test_species_and_total_mass_conservation(self):
        self.ready(); self.start()
        initial=self.h.read('evaluator')['sample.initial_kg']
        for dt in [1,2,5,12,40]:
            self.h.step(dt)
            v=self.h.read('evaluator')
            self.assertAlmostEqual(initial,v['sample.remaining_kg']+v['sample.evaporated_kg'],places=14)
            for sid,m in self.h.initial_species.items():
                self.assertAlmostEqual(m,self.h.remaining[sid]+self.h.evaporated[sid],places=14)
        self.assertGreater(self.h.evaporated['ethanol'],0)
        curve=self.h.invoke('moisture.measure')['curve']
        self.assertTrue(all(a['mass_kg']>=b['mass_kg'] for a,b in zip(curve,curve[1:])))

    def test_async_completion_does_not_overshoot(self):
        self.ready(); response=self.start()
        self.assertEqual(response['status'],'accepted')
        self.assertEqual(self.agent.poll(response['operation_id'])['status'],'running')
        self.h.step(90)
        self.assertEqual(self.h.phase,'succeeded'); self.assertEqual(self.h.elapsed_s,60)
        self.assertEqual(self.h.time_s,90); self.assertEqual(self.h.curve[-1]['time_s'],60)

    def test_cancel_and_power_loss_preserve_partial_mass(self):
        self.ready(); response=self.start(); self.h.step(10)
        before=self.h.debug_snapshot(); curve=copy.deepcopy(self.h.curve)
        self.assertEqual(self.agent.cancel(response['operation_id'],'cancel')['status'],'cancelled')
        self.h.step(100)
        self.assertEqual(self.h.remaining,before['species']); self.assertEqual(curve,self.h.curve)
        self.assertGreater(sum(self.h.evaporated.values()),0)
        self.ready(); self.start(); self.h.step(2); before=dict(self.h.remaining)
        self.h.invoke('power.set',enabled=False); self.h.step(5)
        self.assertEqual(self.h.phase,'cancelled'); self.assertEqual(self.h.remaining,before)

    def test_run_interlocks_do_not_mutate(self):
        self.ready(); self.start()
        for cap,args in [('lid.set_position',{'position_m':.1085}),('thermal.set_temperature',{'temperature_k':400.15}),
                         ('method.configure',{'mode':'rapid','duration_s':60,'basis':'wet'})]:
            before=copy.deepcopy(self.h.__dict__)
            with self.assertRaises(rt.ActionRejected): self.h.invoke(cap,**args)
            self.assertEqual(before,self.h.__dict__)

    def test_instance_principal_and_truth_isolation(self):
        self.ready(); other=rt.MtHx204Runtime('other'); before=copy.deepcopy(other.__dict__)
        self.start(); self.h.step(10); self.assertEqual(before,other.__dict__)
        public=self.agent.describe(instance_id=self.h.instance_id)
        self.assertNotIn('sample.configure',public['capabilities'])
        self.assertNotIn('sample.remaining_kg',public['state_slots'])
        with self.assertRaises(rt.ActionRejected): self.agent.read(instance_id=self.h.instance_id,state_ids=['sample.remaining_kg'])
        with self.assertRaises(rt.ActionRejected): self.agent.read(instance_id='other')
        with self.assertRaises(TypeError): self.agent.read(instance_id=self.h.instance_id,visibility='evaluator')
        rejected=self.agent.invoke(instance_id=self.h.instance_id,capability_id='sample.configure',arguments={},request_id='deny')
        self.assertEqual(rejected['error']['code'],'ACCESS_DENIED')
        self.assertNotIn('ethanol',json.dumps(self.agent.read(instance_id=self.h.instance_id)))
        result=self.h.invoke('moisture.measure')
        self.assertNotIn('ethanol',json.dumps(result))
        self.h.register_principal('other_agent'); other_session=self.h.session('other_agent')
        with self.assertRaises(rt.ActionRejected): other_session.poll(self.h.operation_id)
        self.assertIn('sample.remaining_kg',self.evaluator.read(instance_id=self.h.instance_id)['observations'])

    def test_retry_dedup_conflict_and_write(self):
        kwargs=dict(instance_id=self.h.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='power')
        r=self.agent.invoke(**kwargs); revision=self.h.revision
        self.assertEqual(r,self.agent.invoke(**kwargs)); self.assertEqual(revision,self.h.revision)
        kwargs['arguments']={'enabled':False}
        self.assertEqual(self.agent.invoke(**kwargs)['error']['code'],'REQUEST_CONFLICT'); self.assertTrue(self.h.powered)
        r=self.agent.write(instance_id=self.h.instance_id,state_id='thermal.target_k',value=333.15,request_id='write')
        self.assertEqual(r['status'],'succeeded'); self.assertEqual(self.h.target_k,333.15)

    def test_real_result_and_observation_schemas(self):
        meta=rt.export_mt_hx204_contract_metadata(); self.ready()
        cases=[('power.set',{'enabled':True}),('thermal.set_temperature',{'temperature_k':400.15}),
               ('method.configure',{'mode':'gentle','duration_s':60,'basis':'dry'}),('lid.set_position',{'position_m':.1085}),
               ('sample.configure',{'species_kg':{'ethanol':.001},'residue_kg':.001}),('lid.set_position',{'position_m':0}),
               ('acquisition.start',{}),('moisture.measure',{}),('acquisition.stop',{})]
        for cap,args in cases:
            with self.subTest(cap=cap):
                spec=meta['capabilities'][cap]
                self.assertEqual(lint_schema(spec['input_schema']),[]); self.assertEqual(lint_schema(spec['output_schema']),[])
                result=self.h.invoke(cap,**args)
                self.assertEqual(validate_value(result,spec['output_schema']),[])
        for k,v in self.h.read('evaluator').items(): self.assertEqual(validate_value(v,meta['state_slots'][k]['value_schema']),[])
        self.h.step(1)

    def test_templates_and_programs_are_distinct_and_honest(self):
        self.assertGreaterEqual(len(self.h.templates),50)
        for row in self.h.templates.values():
            self.assertIn('webbook.nist.gov',row['source']); self.assertEqual(row['unit'],'K')
        temps=[]
        for mode in ['standard','rapid','gentle','steps']:
            self.h=rt.MtHx204Runtime(); self.ready(mode=mode)
            self.h.invoke('acquisition.start'); self.h.step(10); temps.append(self.h.temperature_k)
            self.assertEqual(self.h.invoke('moisture.measure')['evidence'],'synthetic')
        self.assertEqual(len(set(temps)),4)

if __name__=='__main__': unittest.main()

