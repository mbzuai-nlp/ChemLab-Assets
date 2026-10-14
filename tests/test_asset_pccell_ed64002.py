import copy,importlib.util,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.pccell_ed64002 import *
spec=importlib.util.spec_from_file_location('pccell_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
def ready(sid='nacl',plant=None,membrane=None,method=None,sample=None):
 h=PccellEd64002Runtime();h.invoke('plant.configure',**(plant or EXAMPLE));h.invoke('membrane.configure',**(membrane or MEMBRANE_EXAMPLE));h.invoke('method.configure',**(method or METHOD_EXAMPLE));s=dict(SAMPLE_EXAMPLE,substance_id=sid);s.update(sample or {});h.invoke('sample.configure',**s);h.invoke('power.set',enabled=True);return h
class EDTests(unittest.TestCase):
 def test_reference_52_distinct(self):
  h=ready();self.assertEqual(len(h.templates),52);self.assertEqual(len({x['formula'] for x in h.templates.values()}),52)
  for r in h.templates.values():
   self.assertEqual(sum(s['count']*s['charge'] for s in r['stoichiometry']),0);self.assertTrue(all(s['diffusion_m2_s']>0 and s['line']>0 for s in r['stoichiometry']));self.assertEqual(r['evidence'],'derived')
 def test_nacl_conductivity_reference(self):
  h=ready();self.assertAlmostEqual(h._kappa()[0],h.templates['nacl']['value']*.3);self.assertGreater(h.templates['nacl']['value'],.012);self.assertLess(h.templates['nacl']['value'],.013)
 def test_setpoint_only(self):
  h=ready();n=h.amount[:];h.invoke('electrical.set_voltage',voltage_v=5.);self.assertEqual(h.voltage,0);self.assertEqual(h.amount,n);self.assertEqual(h.time_s,0)
 def test_queries_pure(self):
  h=ready();before=h.debug_snapshot();h.read();h.describe();h.invoke('electrodialysis.measure');h.invoke('product.read');h.invoke('history.read');self.assertEqual(before,h.debug_snapshot())
 def test_reject_atomic(self):
  h=ready()
  for cap,a in [('electrical.set_voltage',{'voltage_v':25.}),('flow.set_rate',{'flow_m3_s':1e-4}),('sample.configure',SAMPLE_EXAMPLE),('reference.lookup',{'substance_id':'missing'})]:
   before=h.debug_snapshot()
   with self.assertRaises(ActionRejected):h.invoke(cap,**a)
   self.assertEqual(before,h.debug_snapshot())
 def test_integer_validation(self):
  for val in [9,20,10.5,True,'10',float('nan')]:
   h=PccellEd64002Runtime();before=h.debug_snapshot()
   with self.assertRaises(ActionRejected):h.invoke('membrane.configure',**dict(MEMBRANE_EXAMPLE,pairs=val))
   self.assertEqual(before,h.debug_snapshot())
 def test_invalid_numbers(self):
  h=ready()
  for val in [True,float('nan'),float('inf'),-1,'1']:
   b=h.debug_snapshot()
   with self.assertRaises(ActionRejected):h.invoke('electrical.set_current',current_a=val)
   with self.assertRaises(ActionRejected):h.step(val)
   self.assertEqual(b,h.debug_snapshot())
 def test_temperature_confirmations(self):
  for a in [dict(EXAMPLE,temperature_k=300.),dict(EXAMPLE,external_electrode_loop_confirmed=False),dict(EXAMPLE,reference_conditions_accepted=False)]:
   h=PccellEd64002Runtime()
   with self.assertRaises(ActionRejected):h.invoke('plant.configure',**a)
   self.assertIsNone(h.plant)
 def test_double_instance(self):
  a,b=ready(),ready();snap=b.debug_snapshot();a.invoke('acquisition.start');a.step(5);self.assertEqual(snap,b.debug_snapshot())
 def test_conservation_all_templates(self):
  for sid in ready().templates:
   h=ready(sid);h.invoke('acquisition.start');h.step(121.);self.assertEqual(h.phase,'succeeded',sid);self.assertAlmostEqual(sum(h.amount),h.initial_amount,places=14);self.assertAlmostEqual(sum(h.vol),h.initial_volume,places=14);self.assertLess(h.amount[0],SAMPLE_EXAMPLE['diluate_amount_mol']);self.assertTrue(min(h.amount)>=0)
   for ion in h.templates[sid]['stoichiometry']:self.assertAlmostEqual(sum(h.amount)*ion['count'],h.initial_amount*ion['count'],places=13)
 def test_faradaic_charge(self):
  h=ready(membrane=dict(MEMBRANE_EXAMPLE,salt_permeability_m_s=0,water_drag_mol_per_equivalent=0));h.invoke('acquisition.start');h.step(30);expected=.93*10*h.charge/F;self.assertAlmostEqual(SAMPLE_EXAMPLE['diluate_amount_mol']-h.amount[0],expected,places=15);self.assertEqual(h.vol,[.001,.001])
 def test_water_drag(self):
  h=ready();h.invoke('acquisition.start');h.step(30);self.assertAlmostEqual(.001-h.vol[0],5*.93*10*h.charge/F*18e-6,places=15)
 def test_zero_selectivity(self):
  h=ready(membrane=dict(MEMBRANE_EXAMPLE,anion_selectivity=0,salt_permeability_m_s=0));h.invoke('acquisition.start');n=h.amount[:];h.step(20);self.assertEqual(n,h.amount);self.assertGreater(h.energy,0)
 def test_backdiffusion(self):
  h=ready(method=dict(METHOD_EXAMPLE,current_a=0),sample=dict(diluate_amount_mol=.0001,concentrate_amount_mol=.0003));h.invoke('acquisition.start');h.step(10);self.assertGreater(h.amount[0],.0001);self.assertEqual(h.charge,0)
 def test_polarization_and_compliance(self):
  h=ready();h.invoke('acquisition.start');h.step(.5);self.assertLessEqual(h.current,.8*h.ilim);self.assertLessEqual(h.voltage,h.method['voltage_v']);self.assertLessEqual(h.current,h.method['current_a'])
 def test_current_voltage_modes(self):
  for mode in ['constant_current','constant_voltage']:
   h=ready(method=dict(METHOD_EXAMPLE,mode=mode));h.invoke('acquisition.start');h.step(10);self.assertGreater(h.current,0);self.assertLessEqual(h.voltage,20)
 def test_cancel_resume(self):
  h=ready();h.invoke('acquisition.start');h.step(20);n=h.amount[:];e=h.energy;h.invoke('acquisition.stop');h.step(4);self.assertEqual(h.amount,n);self.assertEqual(h.energy,e);h.invoke('acquisition.start');h.step(101);self.assertEqual(h.phase,'succeeded');self.assertLess(h.amount[0],n[0])
 def test_power_cancel(self):
  h=ready();h.invoke('acquisition.start');h.step(5);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.current,0)
 def test_timeout_retains_partial(self):
  h=ready(method=dict(METHOD_EXAMPLE,timeout_s=3.));h.invoke('acquisition.start');h.step(10);self.assertEqual(h.fault,'TIMEOUT');self.assertAlmostEqual(h.elapsed_s,3.);self.assertAlmostEqual(h.time_s,10.);self.assertGreater(h.energy,0);self.assertAlmostEqual(sum(h.amount),h.initial_amount)
 def test_endpoint(self):
  h=ready(method=dict(METHOD_EXAMPLE,endpoint_conductivity_s_m=.01));h.invoke('acquisition.start');h.step(1);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.energy,0)
 def test_capacity_boundary(self):
  h=ready(sample=dict(concentrate_volume_m3=.002));h.invoke('acquisition.start');h.step(10);self.assertEqual(h.fault,'VOLUME_OR_REFERENCE_BOUNDARY');self.assertEqual(h.energy,0)
 def test_agent_isolation(self):
  h=ready();a=h.session();self.assertFalse(any(k.startswith('truth.') for k in a.read(instance_id=h.instance_id)['observations']));self.assertNotIn('product.read',a.describe(instance_id=h.instance_id)['capabilities']);r=a.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='x');self.assertEqual(r['error']['code'],'ACCESS_DENIED');self.assertNotIn('nacl',json.dumps(r))
 def test_native_dedup_ownership(self):
  h=ready();a=h.session();kwargs=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=a.invoke(**kwargs);self.assertEqual(r['status'],'accepted');self.assertEqual(a.invoke(**kwargs),r);h.register_principal('other');other=h.session('other')
  with self.assertRaises(ActionRejected):other.poll(r['operation_id'])
  h.step(5);self.assertEqual(a.poll(r['operation_id'])['status'],'running');self.assertEqual(a.cancel(r['operation_id'],'cancel')['status'],'cancelled');self.assertEqual(a.invoke(**dict(kwargs,capability_id='power.set',arguments={'enabled':False}))['error']['code'],'REQUEST_CONFLICT')
 def test_native_write_and_address(self):
  h=ready();s=h.session();self.assertEqual(s.write(instance_id=h.instance_id,state_id='electrical.voltage_target_v',value=8.,request_id='w')['status'],'succeeded');self.assertEqual(s.invoke(instance_id='wrong',capability_id='power.set',arguments={'enabled':True},request_id='bad')['error']['code'],'UNKNOWN_INSTANCE')
 def test_all_actual_api_and_slots_schema(self):
  h=PccellEd64002Runtime();seen=set()
  for cap in ['plant.configure','membrane.configure','method.configure','sample.configure','power.set','electrical.set_voltage','electrical.set_current','flow.set_rate','acquisition.start','electrodialysis.measure','product.read','history.read','reference.lookup','acquisition.stop']:
   a=copy.deepcopy(CAPS[cap][6]);out=h.invoke(cap,**a);seen.add(cap);self.assertFalse(tool.validate_value(out,CAPS[cap][1]),cap)
   if cap=='acquisition.start':h.step(5)
  self.assertEqual(seen,set(CAPS))
  for k,v in h.read('evaluator').items():self.assertFalse(tool.validate_value(v,SLOTS[k][0]),k)
  for c in CAPS.values():self.assertFalse(tool.lint_schema(c[0]));self.assertFalse(tool.lint_schema(c[1]))
 def test_step_convergence(self):
  a,b=ready(),ready();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(20)
  for _ in range(200):b.step(.1)
  self.assertLess(abs(a.amount[0]-b.amount[0]),1e-8);self.assertLess(abs(a.energy-b.energy),1e-3)
if __name__=='__main__':unittest.main()
