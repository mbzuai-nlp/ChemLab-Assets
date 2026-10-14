import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
import asset_tool
from asset_runtime.cem_libertyblue import *
def prepared(queue=None,kinetics=None,**plant):
 h=CemLibertyblueRuntime();h.invoke('cover.set_lift',lift_m=.08);h.invoke('clamp.set_lift',lift_m=.04);h.invoke('plant.configure',**dict(copy.deepcopy(EXAMPLE),**plant));h.invoke('kinetics.configure',**dict(KINETIC_EXAMPLE,**(kinetics or {})));h.invoke('queue.configure',**(queue or QUEUE_EXAMPLE));h.invoke('cover.set_lift',lift_m=0.);h.invoke('clamp.set_lift',lift_m=0.);h.invoke('power.set',enabled=True);return h
class LibertyTest(unittest.TestCase):
 def balance(self,h):
  v=h.read('evaluator');self.assertAlmostEqual(v['truth.input_mass_kg'],sum(v['truth.'+k+'_mass_kg'] for k in ['retained','waste','vent','removed']),10)
  for i,n in enumerate(h.initial_aa):self.assertAlmostEqual(n,sum(v['truth.'+k+'_mol'][i] for k in ['remaining_aa','bound_aa','waste_aa','removed_aa']),12)
  for r in h.resins:
   self.assertAlmostEqual(r['free']+r['protected'],r['site'],12);self.assertLessEqual(r['correct_free'],r['free']+1e-15);self.assertLessEqual(r['correct_protected'],r['protected']+1e-15);self.assertGreaterEqual(min(r['mass'],r['free'],r['protected'],*r['bound']),-1e-14)
 def finish(self,h,dt=.37):
  h.invoke('acquisition.start')
  for _ in range(15000):
   h.step(dt);self.balance(h)
   if h.phase!='running':break
  self.assertEqual(h.phase,'succeeded',h.fault)
 def test_contract_schemas(self):
  m=export_cem_libertyblue_contract_metadata();self.assertEqual(len(m['capabilities']),13)
  for c in m['capabilities'].values():
   self.assertFalse(asset_tool.lint_schema(c['input_schema']));self.assertFalse(asset_tool.lint_schema(c['output_schema']));self.assertFalse(asset_tool.validate_value(c['example_arguments'],c['input_schema']))
 def test_all_80_real_templates_drive_query(self):
  h=CemLibertyblueRuntime();self.assertEqual(len(h.templates),80);rows=0
  for t in h.templates.values():
   for i,f in enumerate(t['values']):
    r=h.invoke('reference.lookup',substance_id=t['substance_id'],row_index=i);self.assertEqual(r['first_area'],f['values']['first_area']);self.assertAlmostEqual(r['width_minus_height'],r['first_width']-r['first_height'],12);self.assertFalse(asset_tool.validate_value(r,CAPS['reference.lookup'][1]));rows+=1
  self.assertEqual(rows,1402)
 def test_mass_sites_and_aa_ledger(self):
  h=prepared();self.finish(h);self.assertGreater(sum(h.resins[0]['bound']),0);self.assertGreater(h.vent_mass,0);self.assertGreater(h.waste['volume'],0);r=h.invoke('product.read',job_index=0);self.assertTrue(r['ready_for_cleavage']);self.assertTrue(0<r['conditional_full_length_fraction']<1);self.assertIsNone(r['true_purity_fraction']);h.invoke('cover.set_lift',lift_m=.08);h.invoke('clamp.set_lift',lift_m=.04);h.invoke('resin.unload',job_index=0,resin_id='resin_01');self.balance(h)
 def test_incomplete_deprotect_coupling_and_sequence(self):
  outcomes=[]
  for factor in [0.,.2,1.]:
   q=copy.deepcopy(QUEUE_EXAMPLE)
   for s in q['jobs'][0]['steps']:s['sequence_factor']=factor
   h=prepared(queue=q);self.finish(h);outcomes.append(h.resins[0]['correct_free'])
  self.assertEqual(outcomes[0],0.);self.assertLess(outcomes[1],outcomes[2])
  h=prepared(kinetics={'deprotect_k_s_inv':0.});self.finish(h);self.assertEqual(sum(h.resins[0]['bound']),0.)
 def test_wash_residual_changes_conditional_result(self):
  residual=[];outcomes=[]
  for cycles in [1,4]:
   q=copy.deepcopy(QUEUE_EXAMPLE)
   for s in q['jobs'][0]['steps']:s['wash_cycles']=cycles
   h=prepared(queue=q);self.finish(h);residual.append(h.pool['base']);outcomes.append(h.resins[0]['correct_free'])
  self.assertLess(residual[1],residual[0]);self.assertGreater(outcomes[1],outcomes[0])
 def test_queue_module_capacities_sequential(self):
  for module,n in [('none',1),('HT4',4),('HT12',12)]:
   p=copy.deepcopy(EXAMPLE);p['external_stocks'][2]['volume_m3']=.001;p['external_stocks'][3]['volume_m3']=.001;q=dict(copy.deepcopy(QUEUE_EXAMPLE),jobs=[job_example('C','r'+str(i)) for i in range(n)]);h=prepared(queue=q,queue_module=module,external_stocks=p['external_stocks']);self.finish(h,1.7);self.assertEqual(len(h.resins),n);events=[p['job_index'] for p in h.history];self.assertEqual(events,sorted(events));self.assertTrue(all(r['done'] for r in h.resins))
 def test_loading_scale_and_bad_queues_atomic(self):
  h=CemLibertyblueRuntime();h.invoke('cover.set_lift',lift_m=.08);h.invoke('clamp.set_lift',lift_m=.04);h.invoke('plant.configure',**EXAMPLE);old=copy.deepcopy(h.__dict__)
  for field,value in [('scale_mol',.006),('loading_mol_kg',1.),('preloaded_c_terminal','A'),('sequence_ntoc','X'),('steps',[])]:
   q=copy.deepcopy(QUEUE_EXAMPLE);q['jobs'][0][field]=value
   with self.assertRaises(ActionRejected):h.invoke('queue.configure',**q)
   self.assertEqual(old,h.__dict__)
  q=copy.deepcopy(QUEUE_EXAMPLE);q['jobs'][0]['steps'][0]['wash_cycles']=1.5
  with self.assertRaises(ActionRejected):h.invoke('queue.configure',**q)
  self.assertEqual(old,h.__dict__)
 def test_hard_scale_endpoints(self):
  for scale in [5e-6,5e-3]:
   q=copy.deepcopy(QUEUE_EXAMPLE);q['jobs'][0].update(scale_mol=scale,resin_mass_kg=scale/.5);h=prepared(queue=q);self.assertAlmostEqual(h.resins[0]['site'],scale)
 def test_stock_empty_partial_failure_preserves_balance(self):
  p=copy.deepcopy(EXAMPLE);p['external_stocks'][2]['volume_m3']=.35e-6;h=prepared(external_stocks=p['external_stocks']);h.invoke('acquisition.start');h.step(4.);self.assertEqual(h.phase,'failed');self.assertIn('stock_empty',h.fault);self.assertGreater(h.pool['volume'],0);self.balance(h)
 def test_cancel_resume_and_power_retains_stage(self):
  h=prepared();h.invoke('acquisition.start');h.step(2.3);h.invoke('acquisition.stop');snap=h.debug_snapshot();h.step(2);self.assertEqual(snap,h.debug_snapshot());self.finish(h);h=prepared();h.invoke('acquisition.start');h.step(.4);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.balance(h)
 def test_residual_carryover_aa_effect_and_no_double_mass(self):
  h=prepared();self.assertAlmostEqual(h.input_mass,sum(s['density_kg_m3']*s['volume_m3'] for s in h.stocks)+2e-5);self.finish(h);self.assertGreater(sum(h.waste['aa']),0.);self.assertGreater(sum(h.pool['aa']),0.)
 def test_step_partition_consistency(self):
  a=prepared();b=prepared();self.finish(a,.1);self.finish(b,.37);self.assertAlmostEqual(a.resins[0]['correct_free'],b.resins[0]['correct_free'],8);self.assertAlmostEqual(a.waste['volume'],b.waste['volume'],12)
 def test_interlocks_bad_indices_and_pure_reads(self):
  h=prepared();h.invoke('acquisition.start');old=copy.deepcopy(h.__dict__)
  for cap,args in [('cover.set_lift',{'lift_m':.08}),('clamp.set_lift',{'lift_m':.04}),('product.read',{'job_index':True}),('reference.lookup',{'substance_id':next(iter(h.templates)),'row_index':-1})]:
   with self.assertRaises(ActionRejected):h.invoke(cap,**args)
   self.assertEqual(old,h.__dict__)
  h.read();h.describe();h.invoke('synthesis.measure');h.invoke('history.read');h.invoke('product.read',job_index=0);self.assertEqual(old,h.__dict__)
 def test_native_identity_dedup_async(self):
  h=prepared();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**a);self.assertEqual(r['status'],'accepted');self.assertEqual(s.invoke(**a),r);h.step(.2);self.assertEqual(s.poll(r['operation_id'])['status'],'running');self.assertNotIn('truth.free_sites_mol',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities']);h.register_principal('other')
  with self.assertRaises(ActionRejected):h.session('other').poll(r['operation_id'])
  self.assertEqual(s.cancel(r['operation_id'],'stop')['status'],'cancelled');self.balance(h)
 def test_all_actual_outputs_and_states(self):
  h=CemLibertyblueRuntime()
  def ck(k,a=None):
   r=h.invoke(k,**(CAPS[k][6] if a is None else a));self.assertFalse(asset_tool.validate_value(r,CAPS[k][1]),(k,r))
  for k in ['cover.set_lift','clamp.set_lift','plant.configure','kinetics.configure','queue.configure','power.set']:ck(k)
  ck('cover.set_lift',{'lift_m':0.});ck('clamp.set_lift',{'lift_m':0.});ck('acquisition.start');h.step(.2);ck('acquisition.stop');self.finish(h)
  for k in ['synthesis.measure','history.read','product.read','reference.lookup']:ck(k)
  ck('cover.set_lift');ck('clamp.set_lift');ck('resin.unload')
  for k,v in h.read('evaluator').items():self.assertFalse(asset_tool.validate_value(v,SLOTS[k][0]),k)
 def test_bad_dt(self):
  h=prepared()
  for dt in [0,-1,True,float('nan'),float('inf')]:
   with self.assertRaises(ValueError):h.step(dt)
if __name__=='__main__':unittest.main()
