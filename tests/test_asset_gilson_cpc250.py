import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.gilson_cpc250 import *
def configured(**method):
 h=GilsonCpc250Runtime();h.invoke('plant.configure',**EXAMPLE);h.invoke('method.configure',**dict(METHOD_EXAMPLE,**method));h.invoke('sample.configure',**SAMPLE_EXAMPLE);h.invoke('power.set',enabled=True);h.step(30);return h
class TestCpc(unittest.TestCase):
 def assertLedger(self,h):
  v=h.read('evaluator')
  for u,parts in [('solute_kg',['loop','column','collected','waste']),('liquid_m3',['reservoir','loop','column','collected','waste'])]:self.assertAlmostEqual(v['truth.initial_'+u],sum(v['truth.'+p+'_'+u] for p in parts),places=12)
  if h.sample:
   for i,s in enumerate(h.sample['solutes']):self.assertAlmostEqual(s['mass_kg'],h.loop[i]+sum(h.mobile[i])+sum(h.stationary[i])+h.waste[i]+sum(f['solute_masses_kg'][i] for f in h.fractions),places=12)
 def test_01_templates(self):
  h=configured();self.assertEqual(len(h.templates),60);r=h.invoke('reference.lookup',substance_id='cas_57-55-6');self.assertEqual(r['log10_kow'],-.92);self.assertIsNone(r['temperature_k'])
 def test_02_conservation(self):
  h=configured();h.invoke('acquisition.start');h.step(2500);self.assertEqual(h.phase,'succeeded');self.assertLedger(h);self.assertGreater(h.col_volume,.00059);self.assertTrue(any(sum(f['solute_masses_kg'])>0 for f in h.fractions))
 def test_03_direction(self):
  a=configured(direction='ASC');d=configured();self.assertAlmostEqual(a.k[0]*d.k[0],1);self.assertNotEqual(a.k,d.k)
 def test_04_windows(self):
  h=configured(duration_s=200,collection_start_s=40,collection_end_s=100);h.invoke('acquisition.start');h.step(240);self.assertAlmostEqual(h.col_volume,60*h.method['flow_m3_s'],places=12);self.assertAlmostEqual(h.waste_volume,140*h.method['flow_m3_s'],places=12);self.assertLedger(h)
 def test_05_cancel_coast(self):
  h=configured();h.invoke('acquisition.start');h.step(80);before=h.read('evaluator');h.invoke('acquisition.stop');self.assertGreater(h.speed,0);h.step(30);self.assertEqual(h.speed,0);self.assertLess(h.pressure,before['pressure.gauge_pa']);self.assertEqual(h.reservoir,before['truth.reservoir_liquid_m3']);self.assertLedger(h);h.invoke('acquisition.start');h.step(30);self.assertGreater(h.elapsed_s,before['cycle.elapsed_s'])
 def test_06_pressure_fault(self):
  h=configured();h.plant['hydraulic_resistance_pa_s_m3']=1e14;h.invoke('acquisition.start');h.step(50);self.assertEqual(h.fault,'CPC_100BAR_GUARD');self.assertLedger(h)
 def test_07_supply_fault(self):
  h=GilsonCpc250Runtime();h.invoke('plant.configure',**dict(EXAMPLE,reservoir_m3=1e-6));h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('sample.configure',**SAMPLE_EXAMPLE);h.invoke('power.set',enabled=True);h.step(30);h.invoke('acquisition.start');h.step(100);self.assertEqual(h.fault,'RESERVOIR_EMPTY');self.assertLedger(h)
 def test_08_schema_atomic(self):
  h=configured();old=h.debug_snapshot()
  for args in [dict(METHOD_EXAMPLE,speed_rad_s=float('nan')),dict(METHOD_EXAMPLE,flow_m3_s=True),dict(METHOD_EXAMPLE,unknown=1)]:
   with self.assertRaises(ActionRejected):h.invoke('method.configure',**args)
  self.assertEqual(old,h.debug_snapshot())
  for dt in [True,-1,float('inf')]:
   with self.assertRaises(ActionRejected):h.step(dt)
 def test_09_boot_and_power(self):
  h=configured();h.invoke('power.set',enabled=False);h.invoke('power.set',enabled=True)
  with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
  h.step(30);h.invoke('acquisition.start');h.step(1)
  with self.assertRaises(ActionRejected):h.invoke('power.set',enabled=False)
 def test_10_exceptional_speed(self):
  h=GilsonCpc250Runtime()
  with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,speed_rad_s=WMAX))
  h.invoke('method.configure',**dict(METHOD_EXAMPLE,speed_rad_s=WMAX,exceptional_speed_reviewed=True))
 def test_11_protocol(self):
  h=configured();h.register_principal('host','evaluator');a=h.session();e=h.session('host');self.assertNotIn('truth.initial_solute_kg',a.read(instance_id=h.instance_id)['observations']);r=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');self.assertEqual(r['status'],'accepted');self.assertEqual(r,a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one'));self.assertEqual(a.poll(r['operation_id'])['status'],'running');a.cancel(r['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled');self.assertEqual(a.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['status'],'rejected')
 def test_12_partition_sensitivity(self):
  a=configured(duration_s=500,collection_end_s=500);b=configured(direction='ASC',duration_s=500,collection_end_s=500)
  for h in [a,b]:h.invoke('acquisition.start');h.step(540);self.assertLedger(h)
  self.assertGreater(sum(f['solute_masses_kg'][0] for f in a.fractions),sum(f['solute_masses_kg'][0] for f in b.fractions))
 def test_13_collector_full(self):
  h=configured(duration_s=1000,collection_end_s=1000,fraction_volume_m3=1e-6);h.invoke('acquisition.start');h.step(1100);self.assertEqual(h.fault,'COLLECTOR_FULL');self.assertEqual(len(h.fractions),192);self.assertLedger(h)
 def test_14_step_only_clock(self):
  h=configured();before=h.time_s;h.invoke('cpc.measure');h.read();h.describe();self.assertEqual(h.time_s,before);h.step(0);self.assertEqual(h.time_s,before)
if __name__=='__main__':unittest.main()
