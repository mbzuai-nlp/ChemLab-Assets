import copy,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime.ika_ks4000 import IkaKs4000Runtime,CAPS,SLOTS,RPM,ActionRejected,load_limit,export_ika_ks4000_contract_metadata
import asset_tool
def ready(h,**load):
    h.invoke('power.set',enabled=True);h.invoke('hood.set_angle',angle_rad=math.pi/2);h.invoke('fixture.configure',**CAPS['fixture.configure'][6]);h.invoke('load.configure',**dict(CAPS['load.configure'][6],**load));h.invoke('hood.set_angle',angle_rad=0.)
def energy_error(h):
    s=h.read('evaluator');return s['energy.heater_input_j']+s['energy.loading_input_j']-s['energy.stored_j']-s['energy.loss_j']-s['energy.unloading_output_j']
class TestKS4000(unittest.TestCase):
    def test_setpoints_and_pure_reads(self):
        h=IkaKs4000Runtime();h.invoke('thermal.set_temperature',temperature_k=320.15);h.invoke('shaking.set_speed',speed_rad_s=300*RPM);self.assertEqual(h.temperature,298.15);self.assertEqual(h.speed,0.)
        old=copy.deepcopy(h.__dict__);h.read();h.describe();h.session().read(instance_id=h.instance_id);h.invoke('status.measure');self.assertEqual(h.__dict__,old)
    def test_atomic_rejections(self):
        h=IkaKs4000Runtime()
        for cap,a in [('shaking.start',{}),('thermal.set_enabled',{'enabled':True}),('load.configure',CAPS['load.configure'][6]),('thermal.set_temperature',{'temperature_k':299.15}),('thermal.set_temperature',{'temperature_k':310.16}),('shaking.set_speed',{'speed_rad_s':float('nan')}),('power.set',{'enabled':1})]:
            old=copy.deepcopy(h.__dict__)
            with self.assertRaises(ActionRejected):h.invoke(cap,**a)
            self.assertEqual(h.__dict__,old)
    def test_manufacturer_load_derating(self):
        for mass,rpm in [(6,500),(6.01,400),(12,400),(12.01,300),(15,300),(15.01,120),(20,120)]:self.assertAlmostEqual(load_limit(mass),rpm*RPM)
        h=IkaKs4000Runtime();ready(h,liquid_mass_kg=9.,container_mass_kg=1.)
        with self.assertRaises(ActionRejected):h.invoke('shaking.set_speed',speed_rad_s=400*RPM)
        h.invoke('shaking.set_speed',speed_rad_s=300*RPM);h.invoke('shaking.start')
    def test_overload_and_unsecured(self):
        h=IkaKs4000Runtime();ready(h,secured=False)
        with self.assertRaises(ActionRejected):h.invoke('shaking.start')
        h.invoke('hood.set_angle',angle_rad=1.5);old=copy.deepcopy(h.__dict__)
        with self.assertRaises(ActionRejected):h.invoke('load.configure',**dict(CAPS['load.configure'][6],slot=0,container_mass_kg=10.,liquid_mass_kg=10.))
        self.assertEqual(old,h.__dict__)
    def test_orbit_is_translation_and_analytic_phase(self):
        h=IkaKs4000Runtime();ready(h);h.invoke('shaking.start');h.step(10);expected=(h.speed_target*(10-2*(1-math.exp(-5))))%(2*math.pi)
        self.assertAlmostEqual(h.angle,expected,10);s=h.read();self.assertAlmostEqual(s['orbit.x_m']**2+s['orbit.y_m']**2,.0001,12)
    def test_timer_complete_keeps_independent_heater(self):
        h=IkaKs4000Runtime();ready(h);h.invoke('thermal.set_enabled',enabled=True);h.invoke('shaking.start');h.step(61);self.assertEqual(h.phase,'succeeded');self.assertTrue(h.heater);self.assertEqual(h.elapsed_s,60.);self.assertGreater(h.speed,0.);self.assertEqual(h.read()['timer.remaining_s'],0.)
    def test_hood_pause_resume_with_coast(self):
        h=IkaKs4000Runtime();ready(h);h.invoke('thermal.set_enabled',enabled=True);h.invoke('shaking.start');h.step(10);speed=h.speed;h.invoke('hood.set_angle',angle_rad=1.5);self.assertEqual(h.speed,speed);h.step(5)
        self.assertTrue(h.paused);self.assertFalse(h.heater);self.assertAlmostEqual(h.elapsed_s,10);self.assertLess(h.speed,speed);h.invoke('hood.set_angle',angle_rad=0.);h.step(50);self.assertEqual(h.phase,'succeeded')
    def test_fifteen_minute_open_timeout_and_reset(self):
        h=IkaKs4000Runtime();ready(h);h.invoke('shaking.start');h.step(3);h.invoke('hood.set_angle',angle_rad=1.5);h.step(900);self.assertEqual(h.phase,'failed');self.assertEqual(h.error,'hood_open_timer_timeout');h.invoke('hood.set_angle',angle_rad=0.)
        with self.assertRaises(ActionRejected):h.invoke('shaking.start')
        h.invoke('power.set',enabled=False);h.invoke('power.set',enabled=True);self.assertEqual(h.error,'');h.invoke('shaking.start')
    def test_continuous_stop_and_power_mode_a(self):
        h=IkaKs4000Runtime();ready(h);h.invoke('timer.configure',mode='continuous',duration_s=60.);h.invoke('shaking.start');h.step(65);self.assertEqual(h.phase,'running');self.assertIsNone(h.read()['timer.remaining_s']);h.invoke('power.set',enabled=False);angle=h.angle;h.step(1);self.assertNotEqual(h.angle,angle);self.assertEqual(h.phase,'cancelled');h.invoke('power.set',enabled=True);self.assertEqual(h.phase,'cancelled')
    def test_mass_and_energy_through_load_heat_unload(self):
        h=IkaKs4000Runtime();ready(h,initial_temperature_k=290.15);self.assertAlmostEqual(energy_error(h),0.,7);h.invoke('thermal.set_enabled',enabled=True);h.step(400);self.assertGreater(h.temperature,298.15);self.assertAlmostEqual(energy_error(h),0.,6)
        h.invoke('hood.set_angle',angle_rad=1.5);h.step(30);h.invoke('load.unload',slot=4);self.assertAlmostEqual(h.mass_in,h.mass_out,12);self.assertAlmostEqual(energy_error(h),0.,6)
    def test_async_permissions_and_retry(self):
        h=IkaKs4000Runtime();ready(h);s=h.session();h.register_principal('other','agent');a=dict(instance_id=h.instance_id,capability_id='shaking.start',arguments={},request_id='x');r=s.invoke(**a);self.assertEqual(r['status'],'accepted');self.assertEqual(s.invoke(**a),r);h.step(5)
        with self.assertRaises(ActionRejected):h.session('other').poll(r['operation_id'])
        self.assertEqual(s.cancel(r['operation_id'],'cancel')['status'],'cancelled');self.assertNotIn('mass.retained_kg',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('load.configure',s.describe(instance_id=h.instance_id)['capabilities'])
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['mass.retained_kg'])
        self.assertEqual(s.invoke(**dict(a,capability_id='shaking.stop'))['status'],'rejected')
    def test_two_instances_and_invalid_dt(self):
        a=IkaKs4000Runtime('a');b=IkaKs4000Runtime('b');old=copy.deepcopy(b.__dict__);ready(a);a.invoke('shaking.start');a.step(61);self.assertEqual(b.__dict__,old)
        for dt in [0,-1,True,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):a.step(dt)
    def test_all_capability_results_and_schemas(self):
        h=IkaKs4000Runtime();seen=set()
        def call(cap,**a):
            r=h.invoke(cap,**a);seen.add(cap);self.assertEqual(asset_tool.validate_value(r,CAPS[cap][1]),[],cap)
        for cap in ['power.set','hood.set_angle','fixture.configure','load.configure','thermal.set_temperature','timer.configure','safety.configure','calibration.configure']:call(cap,**CAPS[cap][6])
        call('hood.set_angle',angle_rad=0.);call('thermal.set_enabled',enabled=True);call('shaking.set_speed',**CAPS['shaking.set_speed'][6]);call('shaking.start');h.step(65);call('shaking.stop');call('thermal.set_enabled',enabled=False);call('hood.set_angle',angle_rad=1.5);h.step(30);call('load.unload',slot=4);call('status.measure');self.assertEqual(seen,set(CAPS))
        for k,v in h.read('evaluator').items():self.assertEqual(asset_tool.validate_value(v,SLOTS[k][0]),[],k)
        for c in export_ika_ks4000_contract_metadata()['capabilities'].values():self.assertEqual(asset_tool.lint_schema(c['input_schema']),[]);self.assertEqual(asset_tool.lint_schema(c['output_schema']),[])
if __name__=='__main__':unittest.main()
