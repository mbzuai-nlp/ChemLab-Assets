"""CHEM-056 finite fluid chain, metering, energy, timing and trusted protocol tests."""
import copy,math,unittest
from scripts.asset_runtime.ossila_slotdie import *
def configured(name='test',evap=0.,prime=True,ink=True):
    h=OssilaSlotdieRuntime(name);h.invoke('plant.configure',**dict(PLANT_EXAMPLE,mass_transfer_m_s=evap));h.invoke('power.set',enabled=True);h.invoke('stage.home');h.step(.02)
    h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('substrate.configure',**SAMPLE_EXAMPLE);h.invoke('gap.set',left_m=.0001,right_m=.0001);h.step(.1)
    if ink:h.invoke('ink.configure',**INK_EXAMPLE)
    if prime and ink:h.invoke('pump.dispense',volume_m3=1e-7,flow_m3_s=2e-8);h.step(6)
    return h
def residuals(h):
    p=h._product()
    return [p['initial_'+sp+'_kg']-sum(p[loc+'_'+sp+'_kg'] for loc in ['syringe','line','bead','film','waste','evaporated','collected']) for sp in ['solvent','solute']]
class SlotTests(unittest.TestCase):
    def test_spec_limits(self):
        h=configured(ink=False)
        for speed in [.00005,.075]:h.invoke('method.configure',**dict(METHOD_EXAMPLE,speed_m_s=speed))
        for x in [0,.013]:h.invoke('gap.set',left_m=x,right_m=x)
        h.invoke('thermal.set_temperature',temperature_k=413.15)
        with self.assertRaises(ActionRejected):h.invoke('thermal.set_temperature',temperature_k=413.16)
        self.assertAlmostEqual(Q_MIN*1e9,.499937346929,places=5);self.assertAlmostEqual(Q_MAX*1e9,624.921683661265,places=4)
    def test_targets_and_home_are_asynchronous(self):
        h=OssilaSlotdieRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('stage.home');self.assertFalse(h.homed);h.step(.02);self.assertTrue(h.homed)
        h.invoke('stage.move_to',position_m=.1,speed_m_s=.01);self.assertEqual(h.q,0);h.step(.1);self.assertAlmostEqual(h.q,.001)
    def test_manual_gap_and_exact_endpoints(self):
        h=configured(ink=False);h.invoke('power.set',enabled=False);h.invoke('gap.set',left_m=.001,right_m=.003);self.assertEqual(h.gaps,[.0001,.0001]);h.step(1);s=h.read()
        self.assertAlmostEqual(s['gap.mean_m']+.05*math.sin(s['gap.tilt_rad']),.001)
        self.assertAlmostEqual(s['gap.mean_m']-.05*math.sin(s['gap.tilt_rad']),.003)
    def test_no_double_zero_distance_gap_clock(self):
        h=configured(ink=False);h.invoke('gap.set',left_m=.01,right_m=.01);q=h.gaps[0];h.invoke('stage.home');h.step(.005);self.assertAlmostEqual(h.gaps[0]-q,.000025)
    def test_query_purity(self):
        h=configured();snap=h.debug_snapshot();h.describe();h.read();h.invoke('coating.measure');h.invoke('product.read');self.assertEqual(h.debug_snapshot(),snap)
    def test_atomic_rejection(self):
        h=configured();snap=h.debug_snapshot()
        for args in [dict(METHOD_EXAMPLE,speed_m_s=True),dict(METHOD_EXAMPLE,flow_m3_s=Q_MIN/2),dict(METHOD_EXAMPLE,flow_m3_s=float('nan')),dict(METHOD_EXAMPLE,bogus=1)]:
            with self.assertRaises(ActionRejected):h.invoke('method.configure',**args)
            self.assertEqual(h.debug_snapshot(),snap)
    def test_cannot_start_unprimed(self):
        h=configured(prime=False);snap=h.debug_snapshot()
        with self.assertRaisesRegex(ActionRejected,'NOT_PRIMED'):h.invoke('acquisition.start')
        self.assertEqual(h.debug_snapshot(),snap)
    def test_prime_chain_and_plunger(self):
        h=configured(prime=False);q=h.pump_q;h.invoke('pump.dispense',volume_m3=1e-7,flow_m3_s=2e-8);self.assertEqual(q,h.pump_q);h.step(6)
        self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h._volume(h.line),5e-8,places=15);self.assertAlmostEqual(h._volume(h.bead),1e-8,places=15);self.assertAlmostEqual(h._volume(h.waste),4e-8,places=15)
        self.assertAlmostEqual((h.pump_q-q)*PISTON_AREA,1e-7,places=15)
    def test_closed_mass_and_complete(self):
        h=configured(evap=.0002);h.invoke('acquisition.start');h.step(20);self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.q,.14)
        for r in residuals(h):self.assertLess(abs(r),1e-13)
        self.assertGreater(h._product()['film_solute_kg'],0);self.assertEqual(h.evaporated[1],0)
    def test_metered_thickness(self):
        h=configured();h.invoke('method.configure',**dict(METHOD_EXAMPLE,stage_delay_s=0.));h.invoke('acquisition.start');h.step(20)
        self.assertAlmostEqual(h._product()['wet_thickness_m'],METHOD_EXAMPLE['flow_m3_s']/(.1*METHOD_EXAMPLE['speed_m_s']),places=12)
    def test_flow_over_speed_scaling(self):
        a=configured();b=configured();b.invoke('method.configure',**dict(METHOD_EXAMPLE,flow_m3_s=4e-8));a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(20);b.step(20)
        self.assertAlmostEqual(b._product()['wet_thickness_m']/a._product()['wet_thickness_m'],2.,places=8)
    def test_delay_pumps_without_axis_motion(self):
        h=configured();h.invoke('method.configure',**dict(METHOD_EXAMPLE,stage_delay_s=2));before=h.delivered;h.invoke('acquisition.start');h.step(1);self.assertEqual(h.q,0);self.assertAlmostEqual(h.delivered-before,2e-8,places=15)
    def test_delay_zero_stage_never_divides_zero(self):
        h=configured();h.invoke('pump.dispense',volume_m3=2e-8,flow_m3_s=2e-8);h.step(2);self.assertEqual(h.q,0)
        for r in residuals(h):self.assertLess(abs(r),1e-13)
    def test_window_rejection_atomic(self):
        h=configured();h.invoke('gap.set',left_m=.013,right_m=.013);h.step(3);snap=h.debug_snapshot()
        with self.assertRaisesRegex(ActionRejected,'MODEL_OUT_OF_RANGE'):h.invoke('acquisition.start')
        self.assertEqual(snap,h.debug_snapshot())
    def test_cancel_preserves_line_film(self):
        h=configured();h.invoke('acquisition.start');h.step(3);p=h._product();q=h.q;h.invoke('acquisition.stop');self.assertEqual(p,h._product());h.step(1);self.assertEqual(q,h.q);self.assertEqual(h.flow,0)
    def test_empty_syringe_fails_without_creation(self):
        h=configured(ink=False);volume=1.2e-7;h.invoke('ink.configure',**dict(INK_EXAMPLE,solvent_kg=volume*789,solute_kg=0.));h.invoke('pump.dispense',volume_m3=8e-8,flow_m3_s=2e-8);h.step(5)
        h.invoke('acquisition.start');h.step(10);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'SYRINGE_EMPTY')
        for r in residuals(h):self.assertLess(abs(r),1e-14)
    def test_heating_target_only_and_energy(self):
        h=configured(ink=False);t=h.temp;h.invoke('thermal.set_temperature',temperature_k=313.15);h.invoke('thermal.set_enabled',enabled=True);self.assertEqual(t,h.temp);h.step(20);self.assertGreater(h.temp,t);self.assertLess(h.temp,313.15)
        p=h._product();self.assertAlmostEqual(p['sensible_j'],p['energy_input_j']-p['energy_loss_j']-p['latent_j'],places=9)
    def test_film_evaporation_latent_energy(self):
        h=configured(evap=.001);h.invoke('thermal.set_temperature',temperature_k=313.15);h.invoke('thermal.set_enabled',enabled=True);h.invoke('acquisition.start');h.step(20)
        self.assertGreater(h.evaporated[0],0);p=h._product();self.assertAlmostEqual(p['latent_j'],h.evaporated[0]*INK_EXAMPLE['latent_heat_j_kg'],places=9)
        self.assertAlmostEqual(p['sensible_j'],p['energy_input_j']-p['energy_loss_j']-p['latent_j'],places=8)
    def test_normal_stop_preserves_heater(self):
        h=configured();h.invoke('thermal.set_temperature',temperature_k=313.15);h.invoke('thermal.set_enabled',enabled=True);h.invoke('acquisition.start');h.step(2);h.invoke('acquisition.stop');self.assertTrue(h.heating);t=h.temp;h.step(1);self.assertGreater(h.temp,t)
    def test_poweroff_cools_not_teleports(self):
        h=configured(ink=False);h.invoke('thermal.set_temperature',temperature_k=313.15);h.invoke('thermal.set_enabled',enabled=True);h.step(20);t=h.temp;h.invoke('power.set',enabled=False);self.assertEqual(h.temp,t);self.assertFalse(h.heating);h.step(1);self.assertLess(h.temp,t)
    def test_reference_target_guard(self):
        h=configured();snap=h.debug_snapshot()
        with self.assertRaisesRegex(ActionRejected,'REFERENCE_OUT_OF_RANGE'):h.invoke('thermal.set_temperature',temperature_k=413.15)
        self.assertEqual(snap,h.debug_snapshot())
    def test_offset_display_only(self):
        h=configured();t=h.temp;h.invoke('thermal.set_offset',offset_k=2.);self.assertEqual(h.temp,t);self.assertEqual(h.read()['thermal.indicated_k'],t+2)
    def test_emergency_stops_both_and_heat(self):
        h=configured();h.invoke('thermal.set_enabled',enabled=True);h.invoke('acquisition.start');h.step(2);h.invoke('emergency.stop');q=h.q;p=h.pump_q;h.step(1);self.assertEqual(h.q,q);self.assertEqual(h.pump_q,p);self.assertFalse(h.heating)
        h.invoke('emergency.release')
        with self.assertRaises(ActionRejected):h.invoke('power.set',enabled=True)
        h.invoke('power.set',enabled=False);h.invoke('power.set',enabled=True);self.assertFalse(h.latched);self.assertFalse(h.homed)
    def test_collect_drain_and_conservation(self):
        h=configured();h.invoke('acquisition.start');h.step(20);h.invoke('product.collect',recipient_id='x');h.invoke('fluid.drain');self.assertGreater(h.collected[1],0)
        for r in residuals(h):self.assertLess(abs(r),1e-13)
        with self.assertRaises(ActionRejected):h.invoke('product.collect',recipient_id='x')
    def test_time_partition_instances(self):
        a=configured('a');b=configured('b');before=b.debug_snapshot();a.invoke('acquisition.start');a.step(2);self.assertEqual(before,b.debug_snapshot());b.invoke('acquisition.start')
        for _ in range(20):b.step(.1)
        self.assertAlmostEqual(a.q,b.q,places=12);self.assertAlmostEqual(a.delivered,b.delivered,places=14)
    def test_invalid_clock(self):
        h=configured();snap=h.debug_snapshot()
        for dt in [0,-1,True,float('nan')]:
            with self.assertRaises(ActionRejected):h.step(dt)
        self.assertEqual(snap,h.debug_snapshot())
    def test_agent_truth_filter(self):
        h=configured();s=h.session();self.assertNotIn('truth.ledger',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('ink.configure',s.describe(instance_id=h.instance_id)['capabilities'])
        self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(ActionRejected):h.session('evaluator')
    def test_dedup_and_write(self):
        h=configured();s=h.session();a=dict(instance_id=h.instance_id,capability_id='power.toggle',arguments={},request_id='id');r=s.invoke(**a);self.assertEqual(r,s.invoke(**a));self.assertFalse(h.powered)
        self.assertEqual(s.invoke(**dict(a,arguments={'enabled':True},capability_id='power.set'))['error']['code'],'REQUEST_CONFLICT')
        self.assertEqual(s.write(instance_id=h.instance_id,state_id='power.enabled',value=True,request_id='w')['status'],'succeeded')
    def test_async_owner_cancel(self):
        h=configured();s=h.session();h.register_principal('other');other=h.session('other');r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=r['operation_id'];h.step(2)
        with self.assertRaises(ActionRejected):other.poll(op)
        s.cancel(op,'c');self.assertEqual(s.poll(op)['status'],'cancelled')
    def test_negative_schema(self):
        import importlib.util
        s=importlib.util.spec_from_file_location('tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');t=importlib.util.module_from_spec(s);s.loader.exec_module(t)
        self.assertTrue(t.validate_value({'enabled':1},CAPS['power.set'][1]));self.assertTrue(t.validate_value({'phase':'fake'},CAPS['acquisition.stop'][1]))
if __name__=='__main__':unittest.main()


