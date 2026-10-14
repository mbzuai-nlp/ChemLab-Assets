import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
import asset_tool
from asset_runtime.thalesnano_hcube import *
def prepared(method=None,kinetics=None,**plant):
 h=ThalesnanoHcubeRuntime();h.invoke('clamp.set_lift',lift_m=.05)
 if method:h.invoke('method.configure',**method)
 h.invoke('plant.configure',**dict(EXAMPLE,**plant));h.invoke('kinetics.configure',**dict(KINETIC_EXAMPLE,**(kinetics or {})));h.invoke('clamp.set_lift',lift_m=0.);h.invoke('water_lid.set_lift',lift_m=.03);h.invoke('water.fill',volume_m3=100e-6,conductivity_s_m=1e-6);h.invoke('water_lid.set_lift',lift_m=0.);h.invoke('inlet.set',route='sample');h.invoke('outlet.set',route='product');h.invoke('power.set',enabled=True);return h
class HCubeTest(unittest.TestCase):
 def balance(self,h):
  v=h.read('evaluator');self.assertAlmostEqual(v['truth.input_mass_kg'],sum(v['truth.'+k+'_mass_kg'] for k in ['retained','product','waste','removed','vent']),11);self.assertAlmostEqual(h.initial_a,h._species(0)+h._species(1),12);self.assertAlmostEqual(h.h2_generated,h._species(2)+h.h2_reacted+h.h2_vented,12);self.assertAlmostEqual(h.h2_generated/2,h.o2_vented,12);self.assertAlmostEqual(h.water_input_kg,h.water_kg+h.h2_generated*MW,12)
  for c in h.cells:self.assertGreaterEqual(min(c),-1e-16)
 def test_contract_schemas(self):
  m=export_thalesnano_hcube_contract_metadata();self.assertEqual(len(m['capabilities']),17);self.assertEqual(len(m['state_slots']),41)
  for c in m['capabilities'].values():
   self.assertFalse(asset_tool.lint_schema(c['input_schema']));self.assertFalse(asset_tool.lint_schema(c['output_schema']));self.assertFalse(asset_tool.validate_value(c['example_arguments'],c['input_schema']))
 def test_all_78_real_templates_drive_query(self):
  h=ThalesnanoHcubeRuntime();self.assertEqual(len(h.templates),78);self.assertEqual(len({t['cas'] for t in h.templates.values()}),78)
  for t in h.templates.values():
   for f in t['fits']:
    temp=sum(f['runtime_temperature_range_k'])/2;r=h.invoke('thermo.lookup',substance_id=t['substance_id'],fit_id=f['fit_id'],temperature_k=temp);p=1e5*10**(f['A']-f['B_k']/(temp+f['C_k']));self.assertAlmostEqual(r['vapor_pressure_pa']/p,1.,12);self.assertFalse(asset_tool.validate_value(r,CAPS['thermo.lookup'][1]))
 def test_mass_water_hydrogen_substrate_conservation(self):
  h=prepared();h.invoke('acquisition.start')
  for _ in range(24):h.step(.19);self.balance(h)
  self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.product[0],2*MLMIN*4,13);self.assertGreater(h._species(1),0);self.assertGreater(h.h2_vented,0);self.assertLess(h.activity,1)
  h.invoke('collector.unload',container_id='product_01');self.assertEqual(h.product[0],0);self.assertGreater(h.removed[0],0);self.balance(h)
 def test_zero_hydrogen_no_reaction(self):
  h=prepared(method=dict(METHOD_EXAMPLE,hydrogen_reference_flow_m3_s=0.));h.invoke('acquisition.start');h.step(4.);self.assertEqual(h.h2_generated,0);self.assertEqual(h._species(1),0);self.balance(h)
 def test_zero_transfer_and_zero_catalyst(self):
  for kwargs in [dict(kla_s_inv=0.),dict(kinetics={'initial_activity':0.})]:
   h=prepared(**kwargs);h.invoke('acquisition.start');h.step(4.);self.assertEqual(h._species(1),0);self.assertGreater(h.h2_generated,0);self.balance(h)
 def test_ergun_pressure_loss_and_limit(self):
  h=prepared();self.assertGreater(h.dp(),0);h.invoke('acquisition.start');h.step(.02);self.assertAlmostEqual(h.pressure,4e6+h.dp(),5);self.balance(h);h.invoke('acquisition.stop');h.invoke('method.configure',**dict(METHOD_EXAMPLE,pressure_abs_pa=1e7));old=h.debug_snapshot()
  with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
  self.assertEqual(old,h.debug_snapshot())
 def test_bed_sizes_holdup_distinct_from_empty_volume(self):
  for size,(empty,length) in CARTS.items():
   h=prepared(cartridge_size=size);self.assertAlmostEqual(sum(h.volumes[1:5]),empty*.4,15);self.assertLess(sum(h.volumes[1:5]),empty);h.invoke('acquisition.start');h.step(.1);self.balance(h)
 def test_catalyst_decay_analytical_and_thermal(self):
  h=prepared();h.invoke('method.configure',**dict(METHOD_EXAMPLE,temperature_k=323.15));h.step(2);self.assertAlmostEqual(h.activity,math.exp(-.002),12);self.assertAlmostEqual(h.temp_k,323.15-10*math.exp(-.2),10);h.invoke('power.set',enabled=False);old=h.temp_k;h.step(1);self.assertEqual(h.temp_k,old)
 def test_routing_and_feed_wash(self):
  h=prepared();h.invoke('acquisition.start');h.step(1);h.invoke('outlet.set',route='waste');h.invoke('inlet.set',route='solvent');old=h.feed;h.step(2);self.assertEqual(h.feed,old);self.assertAlmostEqual(h.product[0],2*MLMIN,13);self.assertAlmostEqual(h.waste[0],4*MLMIN,13);self.balance(h)
 def test_cancel_retains_inventory_and_residual_reaction(self):
  h=prepared();h.invoke('acquisition.start');h.step(2);a=h._species(0);b=h._species(1);v=h.product[0];h.invoke('acquisition.stop');h.step(.5);self.assertEqual(h.product[0],v);self.assertLessEqual(h._species(0),a);self.assertGreaterEqual(h._species(1),b);self.assertEqual(h.pressure,PN);self.balance(h);h.invoke('acquisition.start');h.step(.2);h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.balance(h)
 def test_water_capacity_quality_and_fault(self):
  h=prepared();h.invoke('water_lid.set_lift',lift_m=.03);old=copy.deepcopy(h.__dict__)
  for args in [dict(volume_m3=201e-6,conductivity_s_m=1e-6),dict(volume_m3=1e-6,conductivity_s_m=1e-3)]:
   with self.assertRaises(ActionRejected):h.invoke('water.fill',**args)
   self.assertEqual(old,h.__dict__)
  h.invoke('water_lid.set_lift',lift_m=0.);h.water_kg=1e-10;h.water_input_kg=1e-10;h.invoke('acquisition.start');h.step(.1);self.assertEqual(h.fault,'water_empty');self.balance(h)
 def test_external_pump_and_known_carrier_bans(self):
  for args in [dict(external_pump_connected=False),dict(materials_and_recipe_reviewed=False),dict(solvent_id='acetonitrile'),dict(solvent_id='toluene'),dict(solvent_id='cyclohexane'),dict(solvent_id='dichloromethane'),dict(solvent_id='ethyl_acetate')]:
   h=ThalesnanoHcubeRuntime();h.invoke('clamp.set_lift',lift_m=.05);old=h.debug_snapshot()
   with self.assertRaises(ActionRejected):h.invoke('plant.configure',**dict(EXAMPLE,**args))
   self.assertEqual(old,h.debug_snapshot())
 def test_interlocks_and_atomic_rejection(self):
  h=prepared();old=copy.deepcopy(h.__dict__)
  for cap,args in [('clamp.set_lift',{'lift_m':True}),('method.configure',dict(METHOD_EXAMPLE,pressure_abs_pa=float('nan'))),('method.configure',dict(METHOD_EXAMPLE,temperature_k=423.15)),('kinetics.configure',KINETIC_EXAMPLE)]:
   with self.assertRaises(ActionRejected):h.invoke(cap,**args)
   self.assertEqual(old,h.__dict__)
  h.invoke('acquisition.start')
  for cap,args in [('clamp.set_lift',{'lift_m':.05}),('water_lid.set_lift',{'lift_m':.03})]:
   with self.assertRaises(ActionRejected):h.invoke(cap,**args)
  h.step(.1);h.invoke('acquisition.stop')
  with self.assertRaises(ActionRejected):h.invoke('clamp.set_lift',lift_m=.05)
  h.step(.1);h.invoke('clamp.set_lift',lift_m=.05)
 def test_three_measured_reference_points_not_instance_yield(self):
  h=prepared()
  for q,x in [(1,1.),(2,.73),(3,.4)]:
   r=h.invoke('calibration.reference',liquid_flow_m3_s=q*MLMIN);self.assertEqual(r['reported_conversion_fraction'],x);self.assertIn('不自动标定',r['conditions_zh'])
  self.assertIsNone(h.invoke('hydrogenation.measure')['real_conversion_fraction'])
  with self.assertRaises(ActionRejected):h.invoke('calibration.reference',liquid_flow_m3_s=1.5*MLMIN)
 def test_reads_pure_and_unknown_yield(self):
  h=prepared();h.invoke('acquisition.start');h.step(.2);old=copy.deepcopy(h.__dict__)
  for _ in range(3):h.read();h.describe();h.invoke('hydrogenation.measure');h.invoke('history.read');h.invoke('product.read')
  self.assertEqual(old,h.__dict__);self.assertIsNone(h.invoke('hydrogenation.measure')['isolated_yield_fraction'])
 def test_session_identity_filter_dedup_async(self):
  h=prepared();b=ThalesnanoHcubeRuntime('other');s=h.session();args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**args);self.assertEqual(r['status'],'accepted');self.assertEqual(s.invoke(**args),r);h.step(.2);self.assertEqual(s.poll(r['operation_id'])['status'],'running');self.assertNotIn('truth.cells',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities']);self.assertFalse(b.powered);self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['error']['code'],'ACCESS_DENIED');h.register_principal('second');other=h.session('second')
  with self.assertRaises(ActionRejected):other.poll(r['operation_id'])
  self.assertEqual(s.cancel(r['operation_id'],'cancel')['status'],'cancelled');self.balance(h);self.assertEqual(s.write(instance_id=h.instance_id,state_id='inlet.route',value='solvent',request_id='wash')['status'],'succeeded')
 def test_all_actual_outputs_and_slots_schema(self):
  h=ThalesnanoHcubeRuntime()
  def ck(k,a=None):
   r=h.invoke(k,**(CAPS[k][6] if a is None else a));self.assertFalse(asset_tool.validate_value(r,CAPS[k][1]),k)
  for k in ['clamp.set_lift','plant.configure','kinetics.configure','water_lid.set_lift','water.fill','power.set','method.configure','inlet.set','outlet.set']:ck(k)
  ck('clamp.set_lift',{'lift_m':0.});ck('water_lid.set_lift',{'lift_m':0.});ck('acquisition.start');h.step(1.)
  for k in ['hydrogenation.measure','history.read','product.read','thermo.lookup','calibration.reference','acquisition.stop','collector.unload']:ck(k)
  for k,v in h.read('evaluator').items():self.assertFalse(asset_tool.validate_value(v,SLOTS[k][0]),k)
 def test_bad_dt(self):
  h=prepared()
  for dt in [0,-1,True,float('nan'),float('inf')]:
   with self.assertRaises(ValueError):h.step(dt)
if __name__=='__main__':unittest.main()
