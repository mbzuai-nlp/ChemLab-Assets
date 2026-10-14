"""CHEM-063 independent physical/protocol invariants, not manufacturer calibration."""
import copy,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_runtime.laboao_cvdltf12p50 import *
def ready(warm=True,plant=None,gas=None,method=None,sample=True):
    h=LaboaoCvdltf12p50Runtime();h.invoke('plant.configure',**dict(WARM_EXAMPLE if warm else PLANT_EXAMPLE,**(plant or {})));h.invoke('gas.install',**(gas or GASES_EXAMPLE))
    if sample:h.invoke('sample.install',**(HOT_SAMPLE_EXAMPLE if warm else SAMPLE_EXAMPLE))
    h.invoke('method.configure',**(method or METHOD_EXAMPLE))
    if not warm:h.invoke('flange.set',opened=False);h.invoke('furnace.set',opened=False)
    h.invoke('power.set',enabled=True);return h
class TestCVD(unittest.TestCase):
    def assertConservative(self,h):
        x=h.invoke('product.read');self.assertLess(max(map(abs,x['atom_residual_mol'])),1e-12);self.assertLess(abs(x['energy_residual_j']),1e-4)
        self.assertTrue(all(v>=0 for v in x['gas_chamber_mol'].values()));self.assertTrue(all(v>=0 for v in x['source_remaining_mol']))
    def test_library_count_identity_and_no_boolean_NO(self):
        h=LaboaoCvdltf12p50Runtime();self.assertEqual(len(h.templates),54);self.assertIn('gri_NO',h.templates);self.assertIn('gri_CH2(S)',h.templates);self.assertIn('solid_C(gr)',h.templates)
        self.assertEqual(len({r['name'] for r in h.templates.values()}),54)
    def test_every_thermo_record_cp_dhdt_and_source(self):
        h=LaboaoCvdltf12p50Runtime()
        for sid,r in h.templates.items():
            bounds=r['conditions']['temperature_ranges_k'];self.assertTrue(r['source']['sha256'])
            for t in [(bounds[0]+bounds[1])/2,(bounds[1]+bounds[-1])/2]:
                cp,enthalpy,entropy,u=h.thermo(sid,t);dh=(h.thermo(sid,t+.01)[1]-h.thermo(sid,t-.01)[1])/.02
                self.assertGreater(cp,0);self.assertAlmostEqual(cp,dh,delta=1e-5)
                self.assertAlmostEqual(enthalpy-u,R*t if r['phase']=='gas' else 0,delta=1e-8)
    def test_thermo_per_record_range_and_split(self):
        h=LaboaoCvdltf12p50Runtime()
        for sid,r in h.templates.items():
            low,mid,high=r['conditions']['temperature_ranges_k']
            for t in [low,mid,high]:h.thermo(sid,t)
            for t in [low-.01,high+.01]:
                with self.assertRaises(ActionRejected):h.thermo(sid,t)
        self.assertEqual(h.templates['gri_HNCO']['conditions']['temperature_ranges_k'][1],1478.)
        with self.assertRaises(ActionRejected):h.thermo('gri_N2',298.15)
    def test_ideal_argon_and_graphite(self):
        h=LaboaoCvdltf12p50Runtime();self.assertAlmostEqual(h.thermo('gri_AR',800)[0],2.5*R);self.assertEqual(h.templates['solid_C(gr)']['values']['density_kg_m3'],2160.)
    def test_warm_snapshot_initial_energy_not_device_teleport(self):
        h=ready();self.assertGreater(h.initial_energy,1e7);self.assertConservative(h)
        old=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke('plant.configure',**PLANT_EXAMPLE)
        self.assertEqual(old,h.debug_snapshot())
    def test_cold_initialization_cannot_smuggle_hot_state(self):
        h=LaboaoCvdltf12p50Runtime()
        with self.assertRaises(ActionRejected):h.invoke('plant.configure',**dict(PLANT_EXAMPLE,initial_oven_k=1173.15))
    def test_pump_delay_exact_boundary(self):
        h=ready();h.invoke('vacuum.set',enabled=True,mode='manual',pressure_pa=30.,manual_valve_fraction=1.)
        p0=h._p();h.step(9.95);self.assertFalse(h.solenoid);self.assertTrue(h.motor);self.assertLess(abs(h._p()-p0),.01)
        h.step(.05);self.assertTrue(h.solenoid);self.assertLess(h._p(),p0);self.assertConservative(h)
    def test_restart_resets_ten_second_delay(self):
        h=ready();a=dict(enabled=True,mode='manual',pressure_pa=30.,manual_valve_fraction=1.)
        h.invoke('vacuum.set',**a);h.step(11);h.invoke('vacuum.set',**dict(a,enabled=False));h.invoke('vacuum.set',**a);h.step(9);self.assertFalse(h.solenoid)
    def test_auto_motor_hysteresis_not_ideal_pressure_assignment(self):
        h=ready();h.invoke('vacuum.set',enabled=True,mode='auto',pressure_pa=20.,manual_valve_fraction=.8);self.assertEqual(h._p(),30.);h.step(12)
        self.assertFalse(h.motor);self.assertFalse(h.solenoid);self.assertLess(h._p(),20)
        self.assertConservative(h)
    def test_standalone_base_not_working_pressure(self):
        h=ready();h.invoke('vacuum.set',enabled=True,mode='manual',pressure_pa=30.,manual_valve_fraction=1.);h.step(20)
        self.assertAlmostEqual(h._p(),.3,delta=.001);self.assertConservative(h)
    def test_cvd_completes_finite_mass_and_energy(self):
        h=ready();h.invoke('acquisition.start',mode='cvd');h.step(20)
        self.assertEqual(h.phase,'succeeded');self.assertGreater(h.film,0);self.assertGreater(h.wall,0);self.assertGreater(h.gas['H2']+h.exhaust['H2'],0);self.assertConservative(h)
        self.assertGreater(h.oven,1100);self.assertTrue(h.fan)
    def test_CH4_stoichiometry_and_thickness(self):
        h=ready();h.invoke('acquisition.start',mode='cvd');h.step(16);x=h._ledger()
        consumed=GASES_EXAMPLE['initial_mol'][2]-h.remaining[2]-h.gas['CH4']-h.exhaust['CH4']
        self.assertAlmostEqual(consumed,h.film+h.wall,delta=1e-15);self.assertAlmostEqual(h.gas['H2']+h.exhaust['H2'],2*consumed,delta=1e-15)
        self.assertAlmostEqual(x['thickness_m'],h.film*.012011/1e-4/2000);self.assertConservative(h)
    def test_no_CH4_no_carbon_fabrication(self):
        h=ready(gas=dict(GASES_EXAMPLE,species=['N2','AR','H2']));h.invoke('acquisition.start',mode='cvd');h.step(20);self.assertEqual(h.film,0);self.assertConservative(h)
    def test_cancel_partial_film_and_reservoirs_preserved(self):
        h=ready();h.invoke('acquisition.start',mode='cvd');h.step(13);self.assertGreater(h.film,0);before=h.film;gas=list(h.remaining)
        h.invoke('acquisition.stop');h.step(1);self.assertEqual(h.film,before);self.assertEqual(h.remaining,gas);self.assertEqual(h.heater,0);self.assertTrue(h.fan);self.assertConservative(h)
    def test_open_clamshell_cuts_heat_without_venting_tube(self):
        h=ready();h.invoke('acquisition.start',mode='cvd');h.step(13);p=h._p();h.invoke('furnace.set',opened=True)
        self.assertEqual(h._p(),p);self.assertFalse(h.flange);self.assertEqual(h.heater,0);self.assertEqual(h.phase,'cancelled')
    def test_hot_vacuum_flange_open_rejected_atomically(self):
        h=ready();before=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke('flange.set',opened=True)
        self.assertEqual(h.debug_snapshot(),before)
    def test_vent_is_species_enthalpy_transfer_not_set_pressure(self):
        h=ready();h.invoke('vacuum.vent');self.assertEqual(h._p(),30.);h.step(8);self.assertGreater(h._p(),.999*101325);self.assertGreater(h.air['O2'],0);self.assertConservative(h)
    def test_fan_threshold_body_not_oven_temperature(self):
        h=ready(plant={'initial_oven_k':500.,'initial_body_k':340.});h.step(.05);self.assertTrue(h.fan)
        g=ready(plant={'initial_oven_k':1173.15,'initial_body_k':310.});g.step(.05);self.assertFalse(g.fan)
    def test_power_loss_stops_pump_gas_heat_not_heat_inventory(self):
        h=ready();h.invoke('acquisition.start',mode='cvd');h.step(13);t=h.oven;film=h.film
        h.invoke('power.set',enabled=False);self.assertFalse(h.fan);self.assertFalse(h.motor);self.assertEqual(h.flow,[0.]*3);self.assertEqual(h.oven,t);h.step(.2);self.assertEqual(h.film,film);self.assertConservative(h)
    def test_three_channels_and_explicit_unknown_scale(self):
        h=ready();self.assertEqual(len(h.gases['full_scale_std_m3_s']),3)
        old=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,flow_std_m3_s=[6e-6,0.,0.]))
        self.assertEqual(h.debug_snapshot(),old)
    def test_finite_source_underpressure(self):
        h=ready(gas=dict(GASES_EXAMPLE,initial_mol=[1e-6]*3));h.invoke('acquisition.start',mode='cvd');h.step(20)
        self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'SOURCE_UNDERPRESSURE');self.assertConservative(h)
    def test_purge_has_no_heat_or_deposition(self):
        h=ready(sample=False);h.invoke('acquisition.start',mode='purge');h.step(15)
        self.assertEqual(h.phase,'succeeded');self.assertEqual(h.electric,0);self.assertEqual(h.film,0);self.assertEqual(h.remaining[2],GASES_EXAMPLE['initial_mol'][2]);self.assertConservative(h)
    def test_cold_air_purge_oxygen_removed_finitely(self):
        h=ready(warm=False);h.invoke('acquisition.start',mode='purge');h.step(30)
        self.assertEqual(h.phase,'succeeded');self.assertLess(h.gas['O2']/sum(h.gas.values()),1e-4);self.assertGreater(h.exhaust['O2'],0);self.assertConservative(h)
    def test_cold_ramp_nominal20_limited_to5(self):
        h=ready(warm=False,method=dict(METHOD_EXAMPLE,segments=[dict(target_k=573.15,ramp_k_s=20/60,hold_s=1.)]))
        h.invoke('acquisition.start',mode='cvd');h.step(35);start=h.setpoint;temp=h.oven;h.step(12)
        self.assertAlmostEqual(h.setpoint-start,1.,delta=1e-7);self.assertGreater(h.oven,temp);self.assertLessEqual(h.heater,3000);self.assertLess(h.oven,573.15);self.assertConservative(h)
    def test_piecewise_ramp_limits(self):
        for t,lim in [(700,5/60),(800,10/60),(1100,5/60),(1400,2/60)]:
            h=ready(plant={'initial_oven_k':float(t)});self.assertAlmostEqual(h._ramp_limit(),lim)
    def test_zero_ramp_rejected_for_transition(self):
        h=ready()
        with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,segments=[dict(target_k=1200.,ramp_k_s=0.,hold_s=1.)]))
        h.invoke('method.configure',**dict(METHOD_EXAMPLE,segments=[dict(target_k=1173.15,ramp_k_s=0.,hold_s=1.)]));h.invoke('acquisition.start',mode='cvd');h.step(14);self.assertEqual(h.phase,'succeeded')
    def test_four_curves_total40_and_selection(self):
        h=ready()
        for i in range(4):h.invoke('method.configure',**dict(METHOD_EXAMPLE,curve_slot=i,segments=METHOD_EXAMPLE['segments']*10))
        self.assertEqual(sum(len(v['segments']) for v in h.programs.values()),40);h.invoke('program.select',curve_slot=2);self.assertEqual(h.method['curve_slot'],2)
        old=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke('method.configure',**dict(METHOD_EXAMPLE,curve_slot=0,segments=METHOD_EXAMPLE['segments']*11))
        self.assertEqual(h.debug_snapshot(),old)
    def test_display_resolution_and_quantization_not_calibration(self):
        h=ready();h.invoke('acquisition.start',mode='cvd')
        for _ in range(10):
            h.step(.13);m=h.invoke('process.measure');self.assertAlmostEqual(m['display_k']-273.15,round(m['display_k']-273.15));self.assertLessEqual(abs(m['display_k']-m['oven_k']),.5);self.assertFalse(m['calibrated'])
    def test_pump_timeout_preserves_partial_inventory(self):
        h=ready(warm=False,plant={'effective_pump_m3_s':1e-5,'pump_timeout_s':11.})
        h.invoke('acquisition.start',mode='purge');h.step(12);self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'PUMP_OR_PURGE_TIMEOUT');self.assertConservative(h)
    def test_no_unvalidated_substance_runtime_source(self):
        h=LaboaoCvdltf12p50Runtime();h.invoke('plant.configure',**PLANT_EXAMPLE)
        with self.assertRaises(ActionRejected):h.invoke('gas.install',**dict(GASES_EXAMPLE,species=['N2','AR','SiH4']))
        self.assertIsNone(h.gases)
    def test_collect_cold_sample_energy_and_identity(self):
        h=ready(warm=False);h.invoke('power.set',enabled=False);h.invoke('flange.set',opened=True);h.invoke('sample.collect',recipient_id='holder');self.assertIsNone(h.sample);self.assertConservative(h)
        with self.assertRaises(ActionRejected):h.invoke('sample.install',**SAMPLE_EXAMPLE)
    def test_invalid_nan_bool_unknown_atomic(self):
        h=ready()
        for args in [dict(enabled=1),dict(enabled=True,extra=True)]:
            before=h.debug_snapshot()
            with self.assertRaises(ActionRejected):h.invoke('power.set',**args)
            self.assertEqual(before,h.debug_snapshot())
        for dt in [True,float('nan'),-1]:
            with self.assertRaises(ActionRejected):h.step(dt)
    def test_zero_step_no_mutation(self):
        h=ready();before=h.debug_snapshot();h.step(0);self.assertEqual(h.debug_snapshot(),before)
    def test_external_clock_partition(self):
        h=ready();g=ready();h.invoke('acquisition.start',mode='cvd');g.invoke('acquisition.start',mode='cvd');h.step(15)
        for _ in range(300):g.step(.05)
        self.assertAlmostEqual(h.film,g.film,delta=1e-18);self.assertAlmostEqual(h.oven,g.oven,delta=1e-9)
    def test_agent_visibility_and_host_config_denial(self):
        h=ready();s=h.session();self.assertNotIn('truth.ledger',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('plant.configure',s.describe(instance_id=h.instance_id)['capabilities'])
        r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(r['status'],'rejected')
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
    def test_idempotency_conflict_and_instance(self):
        h=ready();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'mode':'cvd'},request_id='once');r=s.invoke(**a);rev=h.revision;self.assertEqual(s.invoke(**a),r);self.assertEqual(h.revision,rev)
        self.assertEqual(s.invoke(**dict(a,arguments={'mode':'purge'}))['error']['code'],'REQUEST_CONFLICT')
        self.assertEqual(s.invoke(**dict(a,instance_id='other'))['error']['code'],'UNKNOWN_INSTANCE')
    def test_async_owner_poll_cancel(self):
        h=ready();h.register_principal('other');s=h.session();r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={'mode':'cvd'},request_id='go');op=r['operation_id'];h.step(13)
        with self.assertRaises(ActionRejected):h.session('other').poll(op)
        self.assertEqual(s.poll(op)['status'],'running');s.cancel(op,'stop');self.assertEqual(s.poll(op)['status'],'cancelled');self.assertConservative(h)
    def test_write_routes_to_guarded_api(self):
        h=ready();s=h.session();r=s.write(instance_id=h.instance_id,state_id='power.enabled',value=False,request_id='off');self.assertEqual(r['status'],'succeeded');self.assertFalse(h.powered)
        with self.assertRaises(ActionRejected):s.write(instance_id=h.instance_id,state_id='oven.temperature_k',value=1200,request_id='bad')
    def test_host_clock_before_one_time_initialization(self):
        h=LaboaoCvdltf12p50Runtime();h.step(.1);h.invoke('plant.configure',**WARM_EXAMPLE);h.invoke('gas.install',**GASES_EXAMPLE);h.invoke('sample.install',**HOT_SAMPLE_EXAMPLE)
        self.assertEqual(h.initialized_at_s,.1);self.assertConservative(h)
        h.step(.01)
        with self.assertRaises(ActionRejected):h.invoke('plant.configure',**WARM_EXAMPLE)
    def test_auto_process_initial_band_no_deadlock(self):
        h=ready(method=dict(METHOD_EXAMPLE,pump_mode='auto'));h.invoke('acquisition.start',mode='cvd');h.step(90)
        self.assertEqual(h.phase,'succeeded');self.assertGreater(h.film,0);self.assertConservative(h)
    def test_low_pressure_pauses_hold_and_reaction(self):
        h=ready(method=dict(METHOD_EXAMPLE,flow_std_m3_s=[1e-9,1e-9,1e-9]))
        h.invoke('acquisition.start',mode='cvd');h.step(20);self.assertEqual(h.stage,'hold');self.assertEqual(h.phase,'running');self.assertLess(h._p(),10);self.assertEqual(h.film,0);self.assertConservative(h)
    def test_complete_cold_300C_program_with_finite_large_reservoirs(self):
        h=ready(warm=False,gas=dict(GASES_EXAMPLE,initial_mol=[2.]*3),method=dict(METHOD_EXAMPLE,segments=[dict(target_k=573.15,ramp_k_s=20/60,hold_s=5.)]))
        h.invoke('acquisition.start',mode='cvd');h.step(3500);x=h._ledger()
        self.assertEqual(h.phase,'succeeded');self.assertGreater(h.elapsed_s,3240.);self.assertGreater(h.film,0)
        self.assertLess(abs(x['energy_residual_j']),1e-4);self.assertLess(max(map(abs,x['atom_residual_mol'])),1e-10)
        self.assertGreater(h.remaining[0],0);self.assertGreater(h.electric,3e6)
    def test_independent_instances(self):
        h=ready();g=ready();old=g.debug_snapshot();h.invoke('acquisition.start',mode='cvd');h.step(14);self.assertEqual(g.debug_snapshot(),old)
if __name__=='__main__':unittest.main()

