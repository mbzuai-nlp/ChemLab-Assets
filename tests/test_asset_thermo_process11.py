"""CHEM-057 protocol, finite species, energy, thermal, motion and fault regression tests."""
import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import thermo_process11 as r
def configured(material=True,hot=False,method=None,plant=None):
    h=r.ThermoProcess11Runtime();h.invoke('plant.configure',**(plant or r.PLANT_EXAMPLE));h.invoke('method.configure',**(method or r.METHOD_EXAMPLE))
    if material:h.invoke('material.configure',**r.MATERIAL_EXAMPLE)
    h.invoke('coolant.set_connected',connected=True);h.invoke('power.set',enabled=True)
    if hot:
        for i in range(7):h.invoke('thermal.set_zone',zone=i,temperature_k=438.15)
        h.invoke('thermal.set_enabled',enabled=True);h.step(40)
    return h
def running(**kw):
    h=configured(hot=True,**kw);h.invoke('drive.set_enabled',enabled=True);h.invoke('acquisition.start');return h
class Process11Tests(unittest.TestCase):
    def assert_balanced(self,h):
        p=h.invoke('product.read')
        for sp in ['base','additive']:
            self.assertAlmostEqual(p['initial_'+sp+'_kg'],sum(p[loc+'_'+sp+'_kg'] for loc in ['hopper','barrel','product','collected','waste']),places=12)
        self.assertLess(abs(p['energy_residual_j']),2e-6)
    def rejected(self,h,cap,args):
        before=h.debug_snapshot()
        with self.assertRaises(r.ActionRejected):h.invoke(cap,**args)
        self.assertEqual(before,h.debug_snapshot())
    def test_spec_limits_si(self):
        h=configured()
        for s in [r.OMEGA_MIN,r.OMEGA_MAX]:h.invoke('drive.set_speed',speed_rad_s=s)
        for v in [r.FEED_MIN,r.FEED_MAX]:h.invoke('feed.set_rate',mass_rate_kg_s=v)
        h.invoke('thermal.set_zone',zone=6,temperature_k=623.15)
        self.rejected(h,'drive.set_speed',{'speed_rad_s':r.OMEGA_MAX+.01})
        self.rejected(h,'thermal.set_zone',{'zone':0,'temperature_k':723.15})
    def test_delayed_first_configuration(self):
        h=r.ThermoProcess11Runtime();h.step(1);h.invoke('plant.configure',**r.PLANT_EXAMPLE)
        self.assertAlmostEqual(h.time_s,1);self.assertEqual(h.temps,[298.15]*7)
        self.rejected(h,'plant.configure',r.PLANT_EXAMPLE)
    def test_target_only(self):
        h=configured(False);old=h.read()
        h.invoke('drive.set_speed',speed_rad_s=r.OMEGA_MAX);h.invoke('thermal.set_zone',zone=0,temperature_k=400)
        self.assertEqual(h.speed,0);self.assertEqual(h.temps[0],old['thermal.zone_0_k'])
    def test_pure_reads_describe(self):
        h=configured();old=h.debug_snapshot()
        for _ in range(3):h.read();h.describe();h.session().read(instance_id=h.instance_id);h.invoke('extrusion.measure');h.invoke('product.read')
        self.assertEqual(old,h.debug_snapshot())
    def test_rejection_atomic_bad_args(self):
        h=configured()
        for args in [{'speed_rad_s':True},{'speed_rad_s':float('nan')},{'speed_rad_s':20,'extra':1},{'speed_rad_s':0}]:self.rejected(h,'drive.set_speed',args)
        self.rejected(h,'thermal.set_zone',{'zone':True,'temperature_k':400})
    def test_no_implicit_warmup(self):
        h=configured();h.invoke('drive.set_enabled',enabled=True)
        self.rejected(h,'acquisition.start',{})
        self.assertEqual(h.time_s,0)
    def test_coolant_guard(self):
        h=configured(False);h.invoke('coolant.set_connected',connected=False)
        self.rejected(h,'drive.set_enabled',{'enabled':True})
    def test_cover_host_motion(self):
        h=configured(False);h.invoke('power.set',enabled=False);h.invoke('barrel.set_open',opened=True)
        self.assertEqual(h.cover,0);h.step(1);self.assertAlmostEqual(h.cover,.05)
        h.invoke('power.set',enabled=True);self.rejected(h,'drive.set_enabled',{'enabled':True})
    def test_cover_hot_rejected(self):
        h=configured(hot=True);h.invoke('power.set',enabled=False)
        self.rejected(h,'barrel.set_open',{'opened':True})
    def test_corotation_and_speed_ramp(self):
        h=configured(False);h.invoke('drive.set_enabled',enabled=True);h.step(.1)
        self.assertAlmostEqual(h.speed,5);self.assertGreater(h.angle,0)
        self.assertAlmostEqual((h.read()['screw.b_angle_rad']-h.angle)%r.TAU,math.pi/2)
    def test_finite_feed_mass_energy(self):
        h=running();h.step(25);self.assert_balanced(h);self.assertEqual(h.phase,'succeeded')
        self.assertGreater(sum(h.product),0);self.assertGreater(sum(map(sum,h.zones)),0)
    def test_feed_rate_affects_output(self):
        a=running();b=running();b.invoke('feed.set_rate',mass_rate_kg_s=r.FEED_MAX);a.step(10);b.step(10)
        self.assertGreater(sum(b.product),sum(a.product));self.assert_balanced(b)
    def test_seven_zones_separate(self):
        h=configured(False)
        h.invoke('thermal.set_zone',zone=2,temperature_k=450);h.invoke('thermal.set_enabled',enabled=True);h.step(1)
        self.assertGreater(h.temps[2],h.temps[1]);self.assertEqual(h.temps[1],298.15);self.assert_balanced(h)
    def test_cancel_retains_material_and_heat(self):
        h=running();h.step(10);before=copy.deepcopy(h.zones);product=h.product.copy();temp=h.temps.copy();h.invoke('acquisition.stop')
        self.assertEqual(before,h.zones);self.assertEqual(temp,h.temps);h.step(1)
        self.assertEqual(before,h.zones);self.assertEqual(product,h.product);self.assertEqual(h.phase,'cancelled');self.assert_balanced(h)
    def test_program_exact_duration(self):
        method=dict(r.METHOD_EXAMPLE,duration_s=.025);h=running(method=method);h.step(.5)
        self.assertEqual(h.elapsed_s,.025);self.assertEqual(h.phase,'succeeded')
        self.assertAlmostEqual(sum(h.initial)-sum(h.hopper),h.feed_target*.025,places=12)
    def test_coolant_loss_stops_feed(self):
        h=running();h.step(3);h.invoke('coolant.set_connected',connected=False);q=h.angle;h.step(1)
        self.assertEqual(q,h.angle);self.assertEqual(h.actual_feed,0);self.assertEqual(h.phase,'cancelled')
    def test_emergency_latched_reset(self):
        h=running();h.step(4);q=h.angle;m=h._ledger();h.invoke('emergency.stop');h.step(.1)
        self.assertEqual(q,h.angle);self.assertFalse(h.heating);self.assertTrue(h.latched);self.assertEqual(h.phase,'failed');self.assert_balanced(h)
        self.rejected(h,'fault.reset',{});h.invoke('emergency.release');h.invoke('power.set',enabled=False);h.invoke('fault.reset');h.invoke('power.set',enabled=True)
        self.assertFalse(h.latched)
    def test_pressure_fault_before_transfer(self):
        h=running(method=dict(r.METHOD_EXAMPLE,die_radius_m=.0001));h.step(20)
        self.assertEqual(h.fault,'PRESSURE_LIMIT');self.assertTrue(h.latched);self.assert_balanced(h)
    def test_torque_fault(self):
        h=running(plant=dict(r.PLANT_EXAMPLE,drag_coefficient_m3=.001),method=dict(r.METHOD_EXAMPLE,feed_kg_s=r.FEED_MAX));h.step(20)
        self.assertEqual(h.fault,'TORQUE_LIMIT');self.assert_balanced(h)
    def test_overtemperature_trip(self):
        h=configured(False);h.temps[0]=624.;h.step(.01)
        self.assertEqual(h.fault,'OVERTEMPERATURE')
    def test_poweroff_cools_not_teleports(self):
        h=configured(hot=True);t=h.temps[0];h.invoke('power.set',enabled=False)
        self.assertEqual(t,h.temps[0]);h.step(10);self.assertLess(h.temps[0],t);self.assert_balanced(h)
    def test_collect_clear_species_energy(self):
        h=running();h.step(25);h.invoke('product.collect',recipient_id='collector',accept_hot_output=True);h.invoke('material.clear',accept_hot_residue=True)
        self.assertEqual(sum(h.product)+sum(h.hopper)+sum(map(sum,h.zones)),0);self.assertGreater(sum(h.waste),0);self.assert_balanced(h)
    def test_age_grows_while_stopped(self):
        h=running();h.step(8);h.invoke('acquisition.stop');age=sum(h.age_mass);mass=sum(map(sum,h.zones));h.step(3)
        self.assertAlmostEqual(sum(h.age_mass)-age,mass*3,places=11)
    def test_two_instances(self):
        a=configured(False);b=configured(False);before=b.debug_snapshot();a.invoke('drive.set_enabled',enabled=True);a.step(1)
        self.assertEqual(before,b.debug_snapshot())
    def test_substep_consistency(self):
        a=running();b=running();a.step(1)
        for _ in range(10):b.step(.1)
        for za,zb in zip(a.zones,b.zones):
            for x,y in zip(za,zb):self.assertAlmostEqual(x,y,places=12)
        self.assertAlmostEqual(a.angle,b.angle,places=10);self.assert_balanced(a)
    def test_agent_filter(self):
        h=configured();agent=h.session()
        self.assertNotIn('truth.ledger',h.read());self.assertNotIn('product.read',agent.describe(instance_id=h.instance_id)['capabilities'])
        self.assertEqual(agent.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='secret')['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(r.ActionRejected):agent.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
    def test_roles_trusted(self):
        h=configured()
        with self.assertRaises(r.ActionRejected):h.session('evaluator')
        h.register_principal('judge','evaluator');self.assertIn('truth.ledger',h.session('judge').read(instance_id=h.instance_id)['observations'])
    def test_session_async_idempotency_cancel(self):
        h=configured(hot=True);h.invoke('drive.set_enabled',enabled=True);s=h.session()
        kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='run')
        v=s.invoke(**kw);h.step(4);before=h.debug_snapshot();self.assertEqual(v,s.invoke(**kw));self.assertEqual(before,h.debug_snapshot())
        self.assertEqual(s.poll(v['operation_id'])['status'],'running');s.cancel(v['operation_id'],'cancel');self.assertEqual(s.poll(v['operation_id'])['status'],'cancelled')
        self.assert_balanced(h)
    def test_request_conflict_and_operation_owner(self):
        h=configured(hot=True);h.invoke('drive.set_enabled',enabled=True);s=h.session();v=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='same')
        c=s.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id='same');self.assertEqual(c['error']['code'],'REQUEST_CONFLICT')
        h.register_principal('other')
        with self.assertRaises(r.ActionRejected):h.session('other').poll(v['operation_id'])
    def test_write_routes_and_units(self):
        h=configured();v=h.session().write(instance_id=h.instance_id,state_id='drive.target_rad_s',value=10.,request_id='write')
        self.assertEqual(v['status'],'succeeded');self.assertEqual(h.speed_target,10.)
    def test_reference_exact_no_extrapolation(self):
        h=configured();v=h.invoke('reference.lookup',reference_id='pcm30_aff70',temperature_k=438.15)
        self.assertEqual(v['torque_total_range_nm'],[1.68,2.04]);self.assertEqual(v['pressure_range_pa'],[24e5,29e5])
        self.rejected(h,'reference.lookup',{'reference_id':'pcm30_aff70','temperature_k':430})
        self.rejected(h,'reference.lookup',{'reference_id':'unknown','temperature_k':438.15})
    def test_geometry_method_not_mutable_running(self):
        h=running();self.rejected(h,'method.configure',dict(r.METHOD_EXAMPLE,die_radius_m=.002))
    def test_invalid_step_atomic(self):
        h=configured();before=h.debug_snapshot()
        for x in [0,-1,True,float('nan'),float('inf'),3601]:
            with self.assertRaises(r.ActionRejected):h.step(x)
        self.assertEqual(before,h.debug_snapshot())
    def test_no_refill_or_fake_composition(self):
        h=configured();self.rejected(h,'material.configure',r.MATERIAL_EXAMPLE)
        h=configured(False);self.rejected(h,'material.configure',dict(r.MATERIAL_EXAMPLE,additive_kg=.01))
    def test_no_negative_or_overcapacity_inventory(self):
        h=running(method=dict(r.METHOD_EXAMPLE,duration_s=80.,feed_kg_s=r.FEED_MAX,speed_rad_s=r.OMEGA_MIN));h.step(80)
        self.assertTrue(all(0<=sum(z)<=h.plant['zone_capacity_kg']+1e-12 for z in h.zones));self.assertGreaterEqual(min(h.hopper),0);self.assert_balanced(h)
if __name__=='__main__':unittest.main()
