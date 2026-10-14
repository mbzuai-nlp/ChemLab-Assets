import sys,unittest,copy,math,json
from pathlib import Path
from unittest import mock
import numpy as np
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'scripts'));sys.path.insert(0,str(R/'outputs/expansion_batch03/t4_measure/antonpaar_litesizerdls'));import example_setup as ex
rt=ex.rt
class TestLitesizerDls(unittest.TestCase):
 def ready(self,**kw):return ex.configure(rt.AntonpaarLitesizerdlsRuntime(),**kw)
 def test_01_source_integrity(self):
  lib=rt.LIB['substances'];self.assertEqual(len(lib),103);self.assertEqual(len({lib['cad_'+str(i).zfill(2)]['chemical_formulation']['ionizable_lipid'] for i in range(1,97)}),96);self.assertEqual(lib['cad_85']['value']['pdi'],.16);self.assertIsNone(lib['bpd']['value']['diameter_m']);self.assertEqual(len(lib['bpd']['value']['g2']['acquisitions']),30)
 def test_02_independent_stokes_einstein(self):
  tau=np.geomspace(1e-7,.01,240);D=1.380649e-23*298.15/(3*math.pi*.00089*100e-9);q=4*math.pi*1.33*math.sin(math.pi/4)/658e-9;g2=1+.45*np.exp(-2*D*q*q*tau);out=rt.analyze(tau,g2,298.15,.00089,1.33,90);self.assertLess(abs(out['z_average_m']/100e-9-1),.003);self.assertLess(out['pdi'],.003);self.assertAlmostEqual(sum(out['intensity_fraction']),1.,12)
 def test_03_three_angles_inversion(self):
  for angle in [15,90,175]:
   tau=np.geomspace(1e-7,10,280);g=rt.second_order(tau,180e-9,.2,293.15,.001002,1.333,angle,.4);v=rt.analyze(tau,g,293.15,.001002,1.333,angle);self.assertLess(abs(v['z_average_m']/180e-9-1),1e-4);self.assertAlmostEqual(v['pdi'],.2,4)
 def test_04_measured_raw_protein(self):
  h=self.ready(kind='quartz',reference='bpd');v=ex.run(h);self.assertEqual(v['phase'],'succeeded');self.assertTrue(5e-9<v['dls']['z_average_m']<12e-9,v['dls']['z_average_m']);self.assertIn('measured_literature raw_g2',v['evidence']);self.assertEqual(len(v['g2']),len(rt.LIB['substances']['bpd']['value']['g2']['lag_s']))
 def test_05_bimodal_nnls(self):
  tau=np.geomspace(1e-7,.1,400);d=np.array([20e-9,300e-9]);q=4*math.pi*1.333*math.sin(math.pi/4)/658e-9;rates=q*q*1.380649e-23*293.15/(3*math.pi*.001002*d);g1=(np.exp(-rates[0]*tau)+np.exp(-rates[1]*tau))/2;out=rt.analyze(tau,1+.4*g1*g1,293.15,.001002,1.333,90,regularization=.0001);x=np.array(out['diameter_m']);w=np.array(out['intensity_fraction']);self.assertGreater(w[(x>10e-9)&(x<40e-9)].sum(),.3);self.assertGreater(w[(x>150e-9)&(x<600e-9)].sum(),.3)
 def test_06_entered_viscosity_not_truth(self):
  h=self.ready();m=dict(rt.METHOD_EXAMPLE,entered_viscosity_pa_s=rt.METHOD_EXAMPLE['entered_viscosity_pa_s']*2);ex.call(h,'method.configure',**m);v=ex.run(h);self.assertLess(abs(v['dls']['z_average_m']/rt.LIB['substances'][rt.SID]['value']['diameter_m']-.5),.002)
 def test_07_external_size_calibration(self):
  h=self.ready();ex.call(h,'dls.calibrate',reference_m=200e-9,measured_m=100e-9);v=ex.run(h);self.assertLess(abs(v['dls']['z_average_m']/rt.LIB['substances'][rt.SID]['value']['diameter_m']-2),.002)
 def test_08_zeta_phase_and_eof_rejection(self):
  h=self.ready(mode='zeta');v=ex.run(h);self.assertEqual(v['phase'],'succeeded');self.assertLess(abs(v['els']['zeta_v']/rt.LIB['substances'][rt.SID]['value']['zeta_v']-1),.002);self.assertLess(v['els']['phase_residual_rad'],1e-10);self.assertGreater(max(v['els']['phase_plus_rad'])-min(v['els']['phase_plus_rad']),.01)
 def test_09_external_mobility_calibration(self):
  h=self.ready(mode='zeta');ex.call(h,'mobility.calibrate',reference_m2_v_s=[-1e-8,1e-8],measured_m2_v_s=[-2e-8,2e-8]);v=ex.run(h);self.assertLess(abs(v['els']['zeta_v']/rt.LIB['substances'][rt.SID]['value']['zeta_v']-.5),.002)
 def test_10_finite_sample_energy_return(self):
  h=self.ready();ex.run(h);ex.unload(h);v=ex.call(h,'ledger.read');self.assertEqual(v['present_liquid_kg'],0);self.assertEqual(v['loaded_particles_kg'],v['returned_particles_kg']);self.assertLess(abs(v['heat_balance_j']),1e-7);self.assertGreater(v['returned_heat_j'],0.)
 def test_11_heat_setpoint_not_instant(self):
  h=self.ready();t=h.temperature;ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,temperature_k=303.15,timeout_s=500.));self.assertEqual(h.temperature,t);h.step(100.);self.assertTrue(t<h.temperature<303.16);self.assertLess(abs(h._ledger()['heat_balance_j']),1e-7)
 def test_12_poweroff_cancel_retains_heat(self):
  h=self.ready(mode='zeta');ex.call(h,'acquisition.start');h.step(2.);self.assertGreater(h.field(),0);t=h.temperature;ex.call(h,'power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertEqual(h.field(),0);self.assertEqual(h.temperature,t);h.step(1);self.assertLess(abs(h._ledger()['heat_balance_j']),1e-7)
 def test_13_warmup_360(self):
  h=self.ready();ex.call(h,'power.set',enabled=False);ex.call(h,'power.set',enabled=True);h.step(359.9)
  with self.assertRaisesRegex(rt.ActionRejected,'NOT_READY'):h.invoke('acquisition.start')
  h.step(.1);ex.run(h);self.assertEqual(h.phase,'succeeded')
 def test_14_cell_and_lid_interlocks(self):
  h=self.ready()
  with self.assertRaisesRegex(rt.ActionRejected,'SAMPLE_ACCESS'):h.invoke('cuvette.set',lift_m=.12)
  with self.assertRaisesRegex(rt.ActionRejected,'LID_NOT_RELEASED'):h.invoke('lid.set',angle_rad=1.7)
  ex.call(h,'acquisition.start')
  with self.assertRaisesRegex(rt.ActionRejected,'BUSY'):h.invoke('lid.release')
  ex.call(h,'acquisition.stop');self.assertEqual(h.phase,'cancelled')
 def test_15_quartz_volume_omega_solvent(self):
  h=self.ready();ex.unload(h)
  with self.assertRaisesRegex(rt.ActionRejected,'CELL_COMPATIBILITY'):h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,aqueous=False))
  ex.call(h,'cell.configure',kind='quartz')
  with self.assertRaisesRegex(rt.ActionRejected,'CELL_VOLUME'):h.invoke('sample.load',**rt.SAMPLE_EXAMPLE)
 def test_16_bubbles_and_unknown_reference(self):
  h=self.ready();ex.unload(h)
  for arg,code in [({'bubble_free':False},'BUBBLES'),({'reference_id':'missing'},'UNKNOWN_REFERENCE'),({'accept_cross_instrument_transfer':False},'TRANSFER_NOT_ACCEPTED')]:
   with self.assertRaisesRegex(rt.ActionRejected,code):h.invoke('sample.load',**dict(rt.SAMPLE_EXAMPLE,**arg))
 def test_17_els_angle_and_missing_zeta(self):
  h=self.ready(kind='quartz',reference='bsa')
  with self.assertRaisesRegex(rt.ActionRejected,'ELS_ANGLE'):h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,mode='zeta'))
  ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,mode='zeta',angle_deg=15))
  with self.assertRaisesRegex(rt.ActionRejected,'CELL_COMPATIBILITY'):h.invoke('acquisition.start')
 def test_18_temperature_calibration_independent(self):
  h=rt.AntonpaarLitesizerdlsRuntime();ex.call(h,'plant.configure',**dict(rt.PLANT_EXAMPLE,temperature_gain=1.01,temperature_offset_k=2.));self.assertNotAlmostEqual(h.indicated(),h.block);ex.call(h,'temperature.calibrate',reference_k=[293.15,313.15],measured_k=[293.15*1.01+2,313.15*1.01+2]);self.assertAlmostEqual(h.indicated(),293.15,9)
 def test_19_finite_purge(self):
  h=self.ready();ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,temperature_k=278.15))
  with self.assertRaisesRegex(rt.ActionRejected,'PURGE_REQUIRED'):h.invoke('acquisition.start')
  ex.call(h,'purge.fill',moles=1e-6);ex.call(h,'purge.set',enabled=True);ex.call(h,'acquisition.start');h.step(.1);self.assertEqual(h.failure,'GAS_EXHAUSTED');self.assertLess(abs(h._ledger()['gas_residual_mol']),1e-14)
 def test_20_liquid_phase_boundary(self):
  h=self.ready()
  with self.assertRaisesRegex(rt.ActionRejected,'LIQUID_PHASE'):h.invoke('method.configure',**dict(rt.METHOD_EXAMPLE,temperature_k=393.15))
 def test_21_strict_atomic_rejections(self):
  h=self.ready();before=copy.deepcopy(h.__dict__)
  for k,a in [('power.set',{'enabled':1}),('method.configure',dict(rt.METHOD_EXAMPLE,temperature_k=float('nan'))),('lid.set',{'angle_rad':True}),('dls.calibrate',{'reference_m':1e-6,'measured_m':1e-9})]:
   with self.assertRaises(rt.ActionRejected):h.invoke(k,**a)
  self.assertEqual(h.__dict__,before)
 def test_22_pure_reads_and_request_dedup(self):
  h=self.ready();h.register_principal('agent');s=h.session('agent');v=copy.deepcopy(h.__dict__);s.read(instance_id=h.instance_id);h.invoke('process.measure');self.assertEqual(h.__dict__,v);a=dict(instance_id=h.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='same');r=s.invoke(**a);self.assertEqual(r,s.invoke(**a));self.assertEqual(s.invoke(**dict(a,arguments={'enabled':False}))['error']['code'],'REQUEST_CONFLICT')
 def test_23_truth_permissions(self):
  h=self.ready();h.register_principal('agent');s=h.session('agent');self.assertNotIn('sample.identity',s.read(instance_id=h.instance_id)['observations']);r=s.invoke(instance_id=h.instance_id,capability_id='ledger.read',arguments={},request_id='bad');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
 def test_24_async_ownership_and_terminal_record(self):
  h=self.ready();h.register_principal('a');h.register_principal('b');a=h.session('a');b=h.session('b');op=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='run')['operation_id']
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):b.poll(op)
  a.cancel(op,'cancel');frozen=a.poll(op);ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,mode='zeta',angle_deg=15));self.assertEqual(a.poll(op),frozen)
 def test_25_clock_chunking(self):
  a=self.ready();b=copy.deepcopy(a);ex.call(a,'acquisition.start');ex.call(b,'acquisition.start');a.step(7.)
  for _ in range(100):b.step(.07)
  self.assertEqual(a.read('evaluator'),b.read('evaluator'));self.assertEqual(a._result(),b._result())
 def test_26_unsupported_and_template(self):
  h=self.ready();ex.call(h,'template.reference',substance_id=rt.SID)
  for k in rt.UNSUPPORTED:
   with self.assertRaisesRegex(rt.ActionRejected,'UNSUPPORTED'):h.invoke(k)
 def test_27_noise_not_measured_and_timeout(self):
  h=self.ready();ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,temperature_k=323.15,timeout_s=10.));ex.call(h,'acquisition.start');h.step(11.);self.assertEqual(h.phase,'failed');self.assertEqual(h.failure,'ACQUISITION_TIMEOUT');self.assertTrue(h.read()['lamp.status_red'])
 def test_28_completed_result_keeps_its_acquisition_mode(self):
  h=self.ready();original=ex.run(h);self.assertEqual(original['mode'],'dls');ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,mode='zeta',angle_deg=15));self.assertEqual(h.invoke('process.measure'),original)
  new=ex.run(h);self.assertEqual(new['mode'],'zeta');self.assertIsNone(new['dls']);self.assertIsNotNone(new['els'])
  ex.call(h,'method.configure',**rt.METHOD_EXAMPLE);self.assertEqual(h.invoke('process.measure'),new)
 def test_29_metadata_covers_actual_observable_effects(self):
  metadata=rt.export_antonpaar_litesizerdls_contract_metadata();original=rt.AntonpaarLitesizerdlsRuntime.invoke;observed=set()
  def audited_invoke(h,k,**arguments):
   before=h.read('evaluator');result=original(h,k,**arguments);after=h.read('evaluator');changed={state for state in before if before[state]!=after[state]};declared={effect['state_id'] for effect in metadata['capabilities'][k]['state_effects'] if effect['timing']=='immediate'}
   self.assertFalse(changed-declared,(k,changed-declared));observed.add(k);return result
  with mock.patch.object(rt.AntonpaarLitesizerdlsRuntime,'invoke',audited_invoke):
   h=self.ready();ex.run(h);ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,mode='zeta',angle_deg=15));ex.run(h);ex.unload(h)
   ex.call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,sample_id='warmer_sample',initial_temperature_k=298.15));ex.call(h,'temperature.calibrate',reference_k=[293.15,313.15],measured_k=[294.15,314.15]);ex.call(h,'purge.fill',moles=.01);ex.call(h,'purge.set',enabled=True);ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,temperature_k=303.15))
   other=rt.AntonpaarLitesizerdlsRuntime();ex.call(other,'plant.configure',**dict(rt.PLANT_EXAMPLE,ambient_k=298.15))
   for stop in ['acquisition.stop','power.set']:
    q=self.ready(mode='zeta');ex.call(q,'acquisition.start');q.step(2.);ex.call(q,stop,**({'enabled':False} if stop=='power.set' else {}))
  self.assertTrue(set(rt.IMMEDIATE_EFFECTS)<=observed)
 def test_30_terminal_ownership_survives_new_sample_and_failure(self):
  h=self.ready();h.register_principal('a');h.register_principal('b');s=h.session('a');op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='first')['operation_id']
  while h.phase=='running':h.step(.1)
  frozen=s.poll(op);self.assertEqual(frozen['status'],'succeeded');ex.unload(h);ex.call(h,'sample.load',**dict(rt.SAMPLE_EXAMPLE,sample_id='replacement'));ex.call(h,'cell_cap.set',lift_m=0.);ex.call(h,'cuvette.set',lift_m=0.);ex.call(h,'lid.set',angle_rad=0.)
  ex.call(h,'method.configure',**dict(rt.METHOD_EXAMPLE,temperature_k=323.15,timeout_s=10.));new_op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='second')['operation_id'];h.step(11.);failed=s.poll(new_op);self.assertEqual(failed['status'],'failed');h.step(1.);self.assertEqual(failed,s.poll(new_op));self.assertEqual(frozen,s.poll(op))
  with self.assertRaisesRegex(rt.ActionRejected,'ACCESS_DENIED'):h.session('b').poll(op)
  copied=s.poll(op);copied['result']['g2'].clear();self.assertEqual(frozen,s.poll(op))
if __name__=='__main__':unittest.main()
