import copy, json, math, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'scripts')); sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime.mt_mp70 import MtMp70Runtime, CAPS, SLOTS, ActionRejected, export_mt_mp70_contract_metadata
import asset_tool
def configure(h,channel=0,sid='benzoic_acid',**kwargs):
    a=dict(channel=channel,substance_id=sid,mass_kg=2e-6,purity=1.,particle_size_m=50e-6,decomposition_onset_k=None,decomposition_source=''); a.update(kwargs); h.invoke('sample.configure',**a)
def ready(h,ids=('benzoic_acid',)):
    h.invoke('power.set',enabled=True)
    for i,sid in enumerate(ids): configure(h,i,sid)
    vals=[h.templates[s]['value'] for s in ids]
    h.invoke('method.configure',method_id='test',start_k=max(293.15,min(vals)-10),end_k=min(623.15,max(vals)+18),ramp_k_s=20/60,criterion='transmission'); h.invoke('method.select',method_id='test')
class TestMP70(unittest.TestCase):
    def test_setpoint_read_pure(self):
        h=MtMp70Runtime(); h.invoke('thermal.set_temperature',temperature_k=350.); self.assertEqual(h.temperature_k,293.15)
        old=copy.deepcopy(h.__dict__); h.read(); h.describe(); h.session().read(instance_id=h.instance_id); self.assertEqual(h.__dict__,old)
    def test_rejected_atomic(self):
        h=MtMp70Runtime()
        for cap,args in [('acquisition.start',{}),('method.configure',dict(method_id='bad',start_k=350.,end_k=300.,ramp_k_s=1.,criterion='transmission')),('sample.configure',dict(CAPS['sample.configure'][6],channel=4)),('thermal.set_temperature',{'temperature_k':float('nan')}),('thermal.set_temperature',{'temperature_k':True})]:
            old=copy.deepcopy(h.__dict__)
            with self.assertRaises(ActionRejected): h.invoke(cap,**args)
            self.assertEqual(h.__dict__,old)
    def test_twenty_methods(self):
        h=MtMp70Runtime(); a=CAPS['method.configure'][6]
        for i in range(20): h.invoke('method.configure',**dict(a,method_id=str(i)))
        old=copy.deepcopy(h.methods)
        with self.assertRaises(ActionRejected): h.invoke('method.configure',**dict(a,method_id='21'))
        self.assertEqual(h.methods,old); h.invoke('method.configure',**dict(a,method_id='0',end_k=400.)); self.assertEqual(len(h.methods),20)
    def test_four_independent_channels(self):
        h=MtMp70Runtime(); ready(h,('benzoic_acid','acetanilide','naphthalene','vanillin')); h.invoke('acquisition.start'); h.step(1000)
        self.assertEqual(h.phase,'succeeded'); values=[]
        for i in range(4):
            r=h.invoke('melting.measure',channel=i); self.assertIsNotNone(r['onset_k']); self.assertLess(r['onset_k'],r['clear_k']); values.append(r['onset_k'])
            self.assertEqual(asset_tool.validate_value(r,CAPS['melting.measure'][1]),[])
        self.assertEqual(len(set(values)),4)
    def test_mass_and_cancel_residual(self):
        h=MtMp70Runtime(); ready(h); h.invoke('acquisition.start'); h.step(100); h.invoke('acquisition.stop'); old=copy.deepcopy(h.samples); h.read(); self.assertEqual(old,h.samples)
        self.assertEqual(h.phase,'cancelled'); self.assertAlmostEqual(h.input_kg,sum(s['mass_kg'] for s in h.samples.values())+h.removed_kg)
        h.invoke('thermal.set_temperature',temperature_k=293.15); h.step(2000); h.invoke('sample.unload',channel=0); self.assertAlmostEqual(h.input_kg,h.removed_kg)
    def test_cooling_not_fixed(self):
        h=MtMp70Runtime(); h.invoke('power.set',enabled=True); h.invoke('thermal.set_temperature',temperature_k=623.15); h.step(1000); h.invoke('thermal.set_temperature',temperature_k=293.15); h.step(330)
        self.assertGreater(h.temperature_k,323.15); h.step(100); self.assertLess(h.temperature_k,323.15)
    def test_async_permissions_idempotency(self):
        h=MtMp70Runtime(); ready(h); s=h.session(); h.register_principal('eval','evaluator')
        args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a'); r=s.invoke(**args); self.assertEqual(r['status'],'accepted'); self.assertEqual(r,s.invoke(**args))
        h.step(2); self.assertEqual(s.poll(r['operation_id'])['status'],'running'); self.assertEqual(s.cancel(r['operation_id'],'stop')['status'],'cancelled')
        self.assertEqual(s.invoke(**dict(args,arguments={'extra':1}))['status'],'rejected')
        self.assertNotIn('sample.input_kg',s.read(instance_id=h.instance_id)['observations'])
        self.assertNotIn('sample.configure',s.describe(instance_id=h.instance_id)['capabilities'])
        with self.assertRaises(ActionRejected): s.read(instance_id=h.instance_id,state_ids=['sample.input_kg'])
        self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='sample.configure',arguments=CAPS['sample.configure'][6],request_id='private')['status'],'rejected')
    def test_instance_isolation(self):
        a=MtMp70Runtime('a'); b=MtMp70Runtime('b'); old=copy.deepcopy(b.__dict__); ready(a); a.invoke('acquisition.start'); a.step(300); self.assertEqual(b.__dict__,old)
    def test_purity_particle_and_decomposition(self):
        results=[]
        for purity,particle,decomp in [(1.,50e-6,None),(.8,300e-6,385.)]:
            h=MtMp70Runtime(); h.invoke('power.set',enabled=True); configure(h,purity=purity,particle_size_m=particle,decomposition_onset_k=decomp,decomposition_source='synthetic test threshold' if decomp else '')
            h.invoke('method.configure',**dict(CAPS['method.configure'][6],start_k=380.,end_k=430.)); h.invoke('method.select',method_id='demo'); h.invoke('acquisition.start'); h.step(500)
            results.append(h.invoke('melting.measure',channel=0)); self.assertAlmostEqual(h.input_kg,h.read('evaluator')['sample.retained_kg']+h.removed_kg)
        self.assertNotEqual(results[0]['transmission'],results[1]['transmission']); self.assertEqual(results[1]['quality'],'suspect_decomposition')
    def test_curves_and_schema(self):
        h=MtMp70Runtime(); ready(h); h.invoke('acquisition.start'); h.step(200)
        curve=h.invoke('melting.curve',channel=0); self.assertEqual(asset_tool.validate_value(curve,CAPS['melting.curve'][1]),[]); self.assertEqual(len(curve['points']),math.floor(h.elapsed_s+1e-7))
        for a,b in zip(curve['points'],curve['points'][1:]): self.assertEqual(b['time_s']-a['time_s'],1.)
        for k,v in h.read('evaluator').items(): self.assertEqual(asset_tool.validate_value(v,SLOTS[k][0]),[],k)
        for cap in export_mt_mp70_contract_metadata()['capabilities'].values():
            self.assertEqual(asset_tool.lint_schema(cap['input_schema']),[]); self.assertEqual(asset_tool.lint_schema(cap['output_schema']),[])
    def test_invalid_dt_and_ineligible(self):
        h=MtMp70Runtime()
        for dt in [0,-1,True,float('inf'),float('nan')]:
            with self.assertRaises(ValueError): h.step(dt)
        for sid,row in h.templates.items():
            if not row['runtime_eligible']:
                with self.assertRaises(ActionRejected): configure(h,sid=sid)
    def test_every_capability_actual_return_schema(self):
        h=MtMp70Runtime()
        for cap in ['power.set','thermal.set_temperature','method.configure','method.select','sample.configure','melting.measure','melting.curve','sample.unload']:
            r=h.invoke(cap,**CAPS[cap][6]); self.assertEqual(asset_tool.validate_value(r,CAPS[cap][1]),[],cap)
        configure(h); r=h.invoke('acquisition.start'); self.assertEqual(asset_tool.validate_value(r,CAPS['acquisition.start'][1]),[]); r=h.invoke('acquisition.stop'); self.assertEqual(asset_tool.validate_value(r,CAPS['acquisition.stop'][1]),[])
if __name__=='__main__': unittest.main()
