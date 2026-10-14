import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
import asset_tool
from asset_runtime.cytiva_ktaoligosynt import *
def prepared(method=None,kinetics=None,**plant):
 h=CytivaKtaoligosyntRuntime();h.invoke('clamp.set_lift',lift_m=.04);h.invoke('plant.configure',**dict(copy.deepcopy(EXAMPLE),**plant));h.invoke('kinetics.configure',**dict(copy.deepcopy(KINETIC_EXAMPLE),**(kinetics or {})));h.invoke('method.configure',**copy.deepcopy(method or METHOD_EXAMPLE));h.invoke('clamp.set_lift',lift_m=0.);h.invoke('power.set',enabled=True);return h
class OligosyntTest(unittest.TestCase):
 def balance(self,h):
  v=h.read('evaluator');self.assertAlmostEqual(v['truth.input_mass_kg'],sum(v['truth.'+k+'_mass_kg'] for k in ['retained','waste','removed']),9)
  for i,n in enumerate(h.initial_monomer):self.assertAlmostEqual(n,sum(v['truth.'+k+'_monomer_mol'][i] for k in ['remaining','bound','waste','removed']),11)
  for r in h.columns.values():
   self.assertAlmostEqual(r['free']+r['protected']+r['capped'],r['site'],12);self.assertLessEqual(r['correct_free'],r['free']+1e-14);self.assertLessEqual(r['correct_protected']+r['pending'],r['protected']+1e-14);self.assertGreaterEqual(min(r['mass'],r['free'],r['protected'],r['capped'],r['unoxidized'],*r['bound']),-1e-14)
  self.assertGreaterEqual(min([p['mass'] for p in h.pools.values()]+[n for p in h.pools.values() for n in p['tags']]),0.)
 def finish(self,h,dt=1.7):
  h.invoke('acquisition.start')
  for _ in range(5000):
   h.step(dt);self.balance(h)
   if h.phase!='running':break
  self.assertEqual(h.phase,'succeeded',h.fault)
 def test_contract_schemas(self):
  m=export_cytiva_ktaoligosynt_contract_metadata();self.assertEqual(len(m['capabilities']),12)
  for c in m['capabilities'].values():
   self.assertFalse(asset_tool.lint_schema(c['input_schema']));self.assertFalse(asset_tool.lint_schema(c['output_schema']));self.assertFalse(asset_tool.validate_value(c['example_arguments'],c['input_schema']))
 def test_all_80_real_templates_368_records(self):
  h=CytivaKtaoligosyntRuntime();self.assertEqual(len(h.templates),80);rows=0
  for t in h.templates.values():
   for i,f in enumerate(t['values']):
    r=h.invoke('reference.lookup',substance_id=t['substance_id'],row_index=i);self.assertFalse(asset_tool.validate_value(r,CAPS['reference.lookup'][1]));self.assertEqual(r['sequence_5to3'],t['sequence_5to3']);self.assertTrue(r['template_source']);rows+=1
    if t['kind']=='measured_retention':self.assertAlmostEqual(r['normalized_retention_s'],f['original_retention_min']*60,10)
    else:self.assertAlmostEqual(r['epsilon_m2_mol'],f['original_epsilon_l_mol_cm']*.1,10)
  self.assertEqual(rows,368)
 def test_mass_sites_monomer_and_unload(self):
  h=prepared();self.finish(h);r=h.invoke('product.read',column_position=1);self.assertTrue(0<r['conditional_full_length_fraction']<1);self.assertGreater(r['unoxidized_link_mol'],0);self.assertIsNone(r['true_purity_fraction']);h.invoke('clamp.set_lift',lift_m=.04);h.invoke('support.unload',column_position=1,support_id='support_1');self.balance(h)
  with self.assertRaises(ActionRejected):h.invoke('support.unload',column_position=1,support_id='support_1')
 def test_oxidation_zero_preserves_physical_links(self):
  q=copy.deepcopy(METHOD_EXAMPLE);q['jobs']=[job_example('AGC')];h=prepared(method=q,kinetics={'oxidation_k_s_inv':0.});self.finish(h);r=h.columns[1];self.assertGreater(r['unoxidized'],0);self.assertAlmostEqual(r['unoxidized'],sum(r['bound']),12);self.assertEqual(h.invoke('product.read',column_position=1)['conditional_full_length_fraction'],0.)
 def test_zero_detritylation_and_sequence_factor(self):
  h=prepared(kinetics={'detritylation_k_s_inv':0.});self.finish(h);self.assertEqual(sum(h.columns[1]['bound']),0.)
  values=[]
  for f in [0.,.2,1.]:
   q=copy.deepcopy(METHOD_EXAMPLE)
   for s in q['jobs'][0]['steps']:s['sequence_factor']=f
   h=prepared(method=q);self.finish(h);values.append(h.columns[1]['correct_free'])
  self.assertEqual(values[0],0.);self.assertLess(values[1],values[2])
 def test_wash_changes_carryover(self):
  values=[]
  for t in [1.,9.]:
   q=copy.deepcopy(METHOD_EXAMPLE)
   for s in q['jobs'][0]['steps']:s['wash_s']=t
   h=prepared(method=q);self.finish(h);values.append((h.pools['column_1']['tags'][4],h.columns[1]['correct_free']))
  self.assertLess(values[1][0],values[0][0]);self.assertGreater(values[1][1],values[0][1])
 def test_recirculation_no_external_consumption_or_waste(self):
  h=prepared();h.invoke('acquisition.start')
  while h._values()['cycle.stage']!='recirculate':h.step(.01)
  h.step(.01);stocks=[s['volume_m3'] for s in h.stocks];waste=sum(w['volume'] for w in h.wastes);h.step(.2);self.assertTrue(h.recirculating);self.assertEqual(stocks,[s['volume_m3'] for s in h.stocks]);self.assertEqual(waste,sum(w['volume'] for w in h.wastes));self.balance(h)
 def test_five_columns_serial_and_stagnant_unselected(self):
  q=dict(copy.deepcopy(METHOD_EXAMPLE),jobs=[job_example('C',i) for i in range(1,6)],waste_position=11);h=prepared(method=q);prime=copy.deepcopy(h.pools['column_5']);h.invoke('acquisition.start');h.step(1.);self.assertEqual(prime,h.pools['column_5']);h.step(100.);self.assertEqual(h.phase,'succeeded');self.balance(h);self.assertTrue(all(r['done'] for r in h.columns.values()));self.assertEqual([p['column_position'] for p in h.history],sorted(p['column_position'] for p in h.history));self.assertTrue(all(w['volume']==0 for w in h.wastes[:10]));self.assertGreater(h.wastes[10]['volume'],0)
 def test_loading_geometry_scale_endpoints_and_atomic_bad(self):
  for scale in [1e-5,.012]:
   q=copy.deepcopy(METHOD_EXAMPLE);q['jobs'][0].update(scale_mol=scale,support_mass_kg=scale/.5);h=prepared(method=q);self.assertEqual(h.columns[1]['site'],scale)
  h=CytivaKtaoligosyntRuntime();h.invoke('clamp.set_lift',lift_m=.04);h.invoke('plant.configure',**EXAMPLE);old=copy.deepcopy(h.__dict__)
  for field,value in [('scale_mol',.013),('loading_mol_kg',1.),('preloaded_3prime','A'),('sequence_5to3','AU'),('column_position',True),('steps',[]),('diameter_m',.1)]:
   q=copy.deepcopy(METHOD_EXAMPLE);q['jobs'][0][field]=value
   with self.assertRaises(ActionRejected):h.invoke('method.configure',**q)
   self.assertEqual(old,h.__dict__)
 def test_pressure_system_and_uv_limits(self):
  for kwargs in [dict(line_resistance_pa_s_m3=6e12),dict(outlet_resistance_pa_s_m3=4.2e12)]:
   h=prepared(**kwargs);before=h._inventory();h.invoke('acquisition.start');h.step(.1);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'pressure_limit');self.assertEqual(before,h._inventory());self.balance(h)
  h=prepared(outlet_resistance_pa_s_m3=4.2e12);e=h.plan[0];p,uvp=h._hydraulics(e,.5e-6);self.assertLess(p,2.5e6);self.assertGreater(uvp,2e6)
 def test_flow_high_flow_kit_and_three_wavelength_grid(self):
  q=copy.deepcopy(METHOD_EXAMPLE);q['pump_a_flow_m3_s']=150e-6/60;q['pump_b_flow_m3_s']=150e-6/60
  with self.assertRaises(ActionRejected):prepared(method=q)
  h=prepared(method=q,high_flow_kit=True);wash=next(e for e in h.plan if e['kind']=='wash');self.assertAlmostEqual(sum(f for _,f in wash['feeds']),300e-6/60)
  with self.assertRaises(ActionRejected):prepared(kinetics={'wavelengths_m':[260.5e-9,280e-9,498e-9]})
 def test_stock_empty_preserves_partial_transport(self):
  stocks=copy.deepcopy(EXAMPLE['stocks']);stocks[19]['volume_m3']=.35e-6;h=prepared(stocks=stocks);h.invoke('acquisition.start');h.step(2.);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'stock_empty');self.assertAlmostEqual(h.elapsed_s,.7,8);self.assertGreater(h.pools['mix']['tags'][4],0);self.balance(h)
 def test_cancel_power_and_step_partition(self):
  h=prepared();h.invoke('acquisition.start');h.step(2.3);h.invoke('acquisition.stop');snap=h.debug_snapshot();h.step(3);self.assertEqual(snap,h.debug_snapshot());self.finish(h);a=prepared();self.finish(a,.37);self.assertAlmostEqual(h.columns[1]['correct_free'],a.columns[1]['correct_free'],8);self.assertAlmostEqual(sum(w['volume'] for w in h.wastes),sum(w['volume'] for w in a.wastes),12)
  h=prepared();h.invoke('acquisition.start');h.step(.1);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertEqual(h._signals(),([None]*3,None));self.balance(h)
 def test_interlocks_pure_reads_native_identity_async(self):
  h=prepared();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**a);self.assertEqual(r['status'],'accepted');self.assertEqual(r,s.invoke(**a));h.step(.2);self.assertEqual(s.poll(r['operation_id'])['status'],'running');old=copy.deepcopy(h.__dict__)
  with self.assertRaises(ActionRejected):h.invoke('clamp.set_lift',lift_m=.04)
  h.read();h.describe();h.invoke('synthesis.measure');h.invoke('history.read');self.assertEqual(old,h.__dict__);self.assertNotIn('truth.input_mass_kg',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities']);h.register_principal('other')
  with self.assertRaises(ActionRejected):h.session('other').poll(r['operation_id'])
  self.assertEqual(s.cancel(r['operation_id'],'stop')['status'],'cancelled');b=prepared();self.assertEqual(b.phase,'idle');self.assertEqual(b.elapsed_s,0.)
 def test_all_outputs_states_and_invalid_dt(self):
  h=CytivaKtaoligosyntRuntime()
  def ck(k,a=None):
   r=h.invoke(k,**(CAPS[k][6] if a is None else a));self.assertFalse(asset_tool.validate_value(r,CAPS[k][1]),(k,r))
  for k in ['clamp.set_lift','plant.configure','kinetics.configure','method.configure','power.set']:ck(k)
  ck('clamp.set_lift',{'lift_m':0.});ck('acquisition.start');h.step(.2);ck('acquisition.stop');self.finish(h)
  for k in ['synthesis.measure','history.read','product.read','reference.lookup','clamp.set_lift','support.unload']:ck(k)
  for k,v in h.read('evaluator').items():self.assertFalse(asset_tool.validate_value(v,SLOTS[k][0]),k)
  for dt in [0,-1,True,float('nan'),float('inf')]:
   with self.assertRaises(ValueError):h.step(dt)
if __name__=='__main__':unittest.main()
