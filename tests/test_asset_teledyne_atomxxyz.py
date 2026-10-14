from pathlib import Path
import sys,unittest,copy,math,importlib.util,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));from asset_runtime import teledyne_atomxxyz as m
OBSERVED=set()
_spec=importlib.util.spec_from_file_location('_atomx_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');_tool=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_tool)
def call(r,k,**a):
 v=r.invoke(k,**a);errors=_tool.validate_value(v,m.CAPS[k][1]);assert not errors,(k,errors);OBSERVED.add(k);return v
def ready(method=None,plant=None,sample=None):
 r=m.TeledyneAtomxxyzRuntime();call(r,'plant.configure',**(plant or m.PLANT_EXAMPLE));call(r,'method.configure',**(method or dict(m.METHOD_EXAMPLE,purge_s=10.,dry_purge_s=1.,desorb_s=10.,bake_s=5.)));call(r,'power.set',enabled=True)
 if sample is not False:call(r,'sample.load',**(sample or m.SAMPLE_EXAMPLE))
 return r
def stage(r,want):
 for _ in range(10000):
  if r.stage==want:return
  r.step(.1)
 raise AssertionError((want,r.stage,r.phase))
def finish(r):
 for _ in range(4000):
  if r.phase!='running':return
  r.step(1.)
 raise AssertionError(r.stage)
class Tests(unittest.TestCase):
 def assertLedger(self,r):
  x=call(r,'ledger.read')
  for row in x['species']:self.assertLess(abs(row['residual_mol']),1e-18)
  self.assertLess(abs(x['liquid_residual_m3']),1e-15);self.assertLess(abs(x['gas_residual_mol']),1e-11)
 def test_01_real_templates(self):
  r=ready();self.assertEqual(len(r.templates),77);self.assertEqual(len(r.references),81)
  for sid,t in r.templates.items():self.assertAlmostEqual(call(r,'template.evaluate',substance_id=sid,temperature_k=298.15)['hscp_mol_m3_pa'],t['henry_hscp_mol_m3_pa'],places=10)
  for sid,t in r.references.items():self.assertEqual(call(r,'template.reference',substance_id=sid)['mdl_kg_m3'],t['mdl_kg_m3'])
  self.assertEqual(r.references['acetone']['linearity']['metric'],'linear_regression_coefficient_reported');self.assertEqual(r.references['acetone']['mdl_spike_kg_m3'],2.5e-6)
 def test_02_complete_water_gas_delivery(self):
  r=ready();call(r,'acquisition.start',vial_index=0);finish(r);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.stage,'ready');self.assertGreater(sum(r.gc.values()),0);x=call(r,'process.measure');self.assertTrue(x['complete']);self.assertFalse('benzene' in json.dumps(x));self.assertLedger(r);packet=call(r,'gas.collect');self.assertGreater(sum(x['moles'] for x in packet['species']),0);self.assertEqual(call(r,'gas.collect')['species'],[]);self.assertLedger(r);call(r,'sample.unload',vial_index=0);self.assertLedger(r)
 def test_03_henry_purge_independent_exponential(self):
  r=ready();call(r,'acquisition.start',vial_index=0);stage(r,'purge');n=r.cell['benzene'];V=r.cell_v;t=1.;H=r.henry('benzene',r.method['purge_k']);q=r.method['purge_flow_m3_s']*r.method['purge_k']/r.plant['gas_reference_k'];expected=n*math.exp(-r.plant['transfer_efficiency']*q*t/(H*m.R*r.method['purge_k']*V));r.step(t);self.assertAlmostEqual(r.cell['benzene'],expected,places=20);self.assertLedger(r)
 def test_04_trap_saturation_and_carryover(self):
  r=ready(plant=dict(m.PLANT_EXAMPLE,trap_capacity_mol=1e-10));call(r,'acquisition.start',vial_index=0);stage(r,'dry');self.assertLessEqual(sum(r.trap.values()),1e-10*(1+1e-10));self.assertGreater(sum(r.vent.values()),sum(r.trap.values()));finish(r);old=sum(r.trap.values());self.assertGreater(old,0);call(r,'acquisition.start',vial_index=0);stage(r,'purge');self.assertAlmostEqual(sum(r.trap.values()),old);self.assertLedger(r)
 def test_05_water_dilution_and_standards(self):
  method=dict(m.METHOD_EXAMPLE,dilution=100,purge_s=2.,dry_purge_s=0.,desorb_s=2.,bake_s=2.,standard_doses_m3=[5e-9,0.,0.]);r=ready(method);call(r,'standard.load',**m.CAPS['standard.load'][6]);call(r,'acquisition.start',vial_index=0);stage(r,'purge');self.assertAlmostEqual(r.cell_v,5e-6,places=14);self.assertAlmostEqual(r.cell['benzene'],1e-8*((5e-6-5e-9)/100)/20e-6,places=20);self.assertAlmostEqual(r.cell['toluene'],1e-6*5e-9/15e-6,places=20);self.assertLedger(r);finish(r);self.assertLedger(r)
 def test_06_soil_low_partition(self):
  sample=dict(m.SAMPLE_EXAMPLE,matrix='soil_low',liquid_volume_m3=0.,soil_mass_kg=.005,components=[dict(substance_id='benzene',moles=1e-8,soil_kd_m3_kg=.1)]);r=ready(dict(m.METHOD_EXAMPLE,matrix='soil_low',purge_s=2.,dry_purge_s=0.,desorb_s=2.,bake_s=2.),sample=sample);call(r,'acquisition.start',vial_index=0);stage(r,'purge');self.assertEqual(r.cell_soil,.005);before=r.cell['benzene'];r.step(1.);self.assertGreater(r.cell['benzene'],before*.99);finish(r);self.assertGreater(r.vials[0]['n']['benzene'],0);self.assertEqual(r.vials[0]['v'],5e-6);self.assertLedger(r)
 def test_07_high_soil_extraction_cancel_and_restart(self):
  method=dict(m.METHOD_EXAMPLE,matrix='soil_high',dilution=50,purge_s=2.,dry_purge_s=0.,desorb_s=2.,bake_s=2.,extraction_s=2.);sample=dict(m.SAMPLE_EXAMPLE,matrix='soil_high',liquid_volume_m3=0.,soil_mass_kg=.005);r=ready(method,sample=sample);call(r,'acquisition.start',vial_index=0);stage(r,'extract');r.step(1.);self.assertLedger(r);call(r,'acquisition.stop');self.assertLedger(r);self.assertEqual(r.vials[0]['n']['benzene'],1e-8);call(r,'acquisition.start',vial_index=0);finish(r);self.assertEqual(r.phase,'succeeded');self.assertGreater(sum(r.gc.values()),0);self.assertLedger(r)
 def test_08_cancellation_preserves_and_rinse(self):
  r=ready();call(r,'acquisition.start',vial_index=0);stage(r,'purge');r.step(2.);old=copy.deepcopy(r._ledger());call(r,'acquisition.stop');self.assertEqual(old,r._ledger());call(r,'system.rinse',volume_m3=7e-6);self.assertEqual(r.cell_v,0);self.assertGreater(sum(r.trap.values()),0);self.assertLedger(r)
 def test_09_power_failure_and_finite_gas(self):
  r=ready(plant=dict(m.PLANT_EXAMPLE,gas_supply_mol=.001));call(r,'acquisition.start',vial_index=0);finish(r);self.assertEqual(r.phase,'failed');self.assertEqual(r.failure,'GAS_DEPLETED');self.assertLedger(r)
  r=ready();call(r,'acquisition.start',vial_index=0);stage(r,'purge');r.step(1.);call(r,'power.set',enabled=False);self.assertEqual(r.phase,'cancelled');old=r._ledger();r.step(10.);self.assertEqual(old,r._ledger())
 def test_10_settings_pure_queries_and_atomicity(self):
  r=ready();t=r.temp;call(r,'method.configure',**dict(r.method,desorb_k=550.));self.assertEqual(r.temp,t)
  for fn in [r.read,r.describe,lambda:r.invoke('process.measure')]:before=copy.deepcopy(r.__dict__);fn();self.assertEqual(before,r.__dict__)
  for a in [dict(r.method,sample_volume_m3=5.05e-6),dict(r.method,purge_s=.15),dict(r.method,purge_k=float('nan')),dict(r.method,dilution=True),dict(r.method,matrix='soil_high',dilution=2)]:
   before=copy.deepcopy(r.__dict__)
   with self.assertRaises(m.ActionRejected):r.invoke('method.configure',**a)
   self.assertEqual(before,r.__dict__)
 def test_11_door_interlocks(self):
  r=ready();call(r,'guard.set',angle_rad=1.)
  with self.assertRaisesRegex(m.ActionRejected,'NOT_READY'):r.invoke('acquisition.start',vial_index=0)
  call(r,'guard.set',angle_rad=0.);call(r,'trap_door.set',angle_rad=1.);call(r,'trap_door.set',angle_rad=0.);call(r,'acquisition.start',vial_index=0)
  with self.assertRaisesRegex(m.ActionRejected,'BUSY'):r.invoke('guard.set',angle_rad=1.)
  stage(r,'desorb');call(r,'acquisition.stop')
  with self.assertRaisesRegex(m.ActionRejected,'HOT_TRAP'):r.invoke('trap_door.set',angle_rad=1.)
 def test_12_grid_capacity_and_positions(self):
  r=ready(sample=False)
  for i in range(84):call(r,'sample.load',**dict(m.SAMPLE_EXAMPLE,vial_index=i))
  self.assertEqual(r.read()['sample.count'],84)
  with self.assertRaises(m.ActionRejected):r.invoke('sample.load',**dict(m.SAMPLE_EXAMPLE,vial_index=84))
  for i in [0,6,77,83]:
   q=m.vial_target(i);self.assertTrue(0<=q[0]<=.365 and 0<=q[1]<=.4)
  self.assertLedger(r)
 def test_13_time_partition_and_instances(self):
  a=ready();call(a,'acquisition.start',vial_index=0);b=copy.deepcopy(a);a.step(73.)
  for _ in range(730):b.step(.1)
  for key in ['q','cell','trap','gc','gas','temp','elapsed','points']:self.assertEqual(getattr(a,key),getattr(b,key))
  c=ready();self.assertEqual(c.phase,'idle');self.assertNotEqual(c.gas,a.gas)
 def test_14_identity_request_filtering(self):
  r=ready();r.register_principal('a');r.register_principal('b');r.register_principal('e','evaluator');a=r.session('a');self.assertNotIn('sample.identity',a.read(instance_id=r.instance_id)['observations']);self.assertNotIn('sample.load',a.describe(asset_type_id=m.ASSET_TYPE_ID)['capabilities'])
  denied=a.invoke(instance_id=r.instance_id,capability_id='ledger.read',arguments={},request_id='deny');self.assertEqual(denied['error']['code'],'ACCESS_DENIED');x=dict(instance_id=r.instance_id,capability_id='guard.set',arguments={'angle_rad':1.},request_id='1');v=a.invoke(**x);rev=r.revision;self.assertEqual(v,a.invoke(**x));self.assertEqual(rev,r.revision);x['arguments']={'angle_rad':0.};self.assertEqual(a.invoke(**x)['error']['code'],'REQUEST_CONFLICT')
 def test_15_async_poll_cancel_ownership(self):
  r=ready();r.register_principal('a');r.register_principal('b');a=r.session('a');b=r.session('b');v=a.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={'vial_index':0},request_id='op');op=v['operation_id'];self.assertEqual(v['status'],'accepted');r.step(3.)
  with self.assertRaises(m.ActionRejected):b.poll(op)
  x=a.cancel(op,'cancel');self.assertEqual(x['result']['phase'],'cancelled');self.assertEqual(a.poll(op)['status'],'cancelled');self.assertLedger(r)
 def test_16_schema_actual_returns(self):
  sp=importlib.util.spec_from_file_location('asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');tool=importlib.util.module_from_spec(sp);sp.loader.exec_module(tool);r=ready();meta=r.describe()
  for sid,value in r.read('evaluator').items():self.assertFalse(tool.validate_value(value,meta['state_slots'][sid]['value_schema']))
  for k in m.UNSUPPORTED:
   with self.assertRaisesRegex(m.ActionRejected,'UNSUPPORTED'):r.invoke(k)
  for dt in [True,-1,float('nan')]:
   with self.assertRaises(m.ActionRejected):r.step(dt)
 def test_17_no_phantom_inventory_on_rejection(self):
  r=ready(plant=dict(m.PLANT_EXAMPLE,water_supply_m3=0.));before=copy.deepcopy(r.__dict__)
  with self.assertRaisesRegex(m.ActionRejected,'WATER_DEPLETED'):r.invoke('acquisition.start',vial_index=0)
  self.assertEqual(before,r.__dict__)
 def test_18_multispecies_conservation(self):
  r=ready(sample=dict(m.SAMPLE_EXAMPLE,components=[dict(substance_id=sid,moles=1e-8,soil_kd_m3_kg=0.) for sid in ['benzene','toluene','acetone']]));call(r,'acquisition.start',vial_index=0);finish(r);self.assertLedger(r);self.assertEqual(len(r.gc),3);self.assertGreater(r.gc['benzene'],r.gc['acetone'])
if __name__=='__main__':unittest.main()
