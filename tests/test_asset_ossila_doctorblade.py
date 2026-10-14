"""CHEM-055 actual physics, protocol and finite-inventory tests."""
import copy,math,unittest
from scripts.asset_runtime.ossila_doctorblade import *
def configured(name='test',evap=0.,charge=True):
    h=OssilaDoctorbladeRuntime(name);h.invoke('plant.configure',**dict(PLANT_EXAMPLE,mass_transfer_m_s=evap));h.invoke('power.set',enabled=True);h.invoke('stage.home');h.step(.02)
    h.invoke('method.configure',**METHOD_EXAMPLE);h.invoke('substrate.configure',**SAMPLE_EXAMPLE);h.invoke('gap.set',left_m=.0001,right_m=.0001);h.step(.1)
    if charge:h.invoke('charge.configure',**CHARGE_EXAMPLE)
    return h
def balance(h):
    p=h._product()
    return [p['initial_'+sp+'_kg']-sum(p[loc+'_'+sp+'_kg'] for loc in ['bead','film','waste','evaporated','collected']) for sp in ['solvent','solute']]
class BladeTests(unittest.TestCase):
    def test_targets_only(self):
        h=configured(charge=False);h.invoke('stage.move_to',position_m=.1,speed_m_s=.02);self.assertEqual(h.q,0.);h.step(.1);self.assertAlmostEqual(h.q,.002)
    def test_home_needs_clock(self):
        h=OssilaDoctorbladeRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('stage.home');self.assertFalse(h.homed);h.step(.01);self.assertTrue(h.homed)
    def test_manual_gap_poweroff(self):
        h=configured();h.invoke('power.set',enabled=False);h.invoke('gap.set',left_m=.001,right_m=.002);self.assertEqual(h.gaps,[.0001,.0001]);h.step(.05);self.assertGreater(h.gaps[0],.0001);h.step(1);self.assertEqual(h.gaps,[.001,.002])
    def test_nonparallel_gap_pose(self):
        h=configured();h.invoke('gap.set',left_m=.0001,right_m=.0003);h.step(.1);v=h.read();self.assertAlmostEqual(v['gap.mean_m'],.0002);self.assertAlmostEqual(v['gap.tilt_rad'],-math.atan(.002))
    def test_queries_pure(self):
        h=configured();before=h.debug_snapshot();h.read();h.describe();h.invoke('coating.measure');h.invoke('product.read');self.assertEqual(before,h.debug_snapshot())
    def test_rejection_atomic(self):
        h=configured();before=h.debug_snapshot()
        for a in [dict(METHOD_EXAMPLE,speed_m_s=True),dict(METHOD_EXAMPLE,speed_m_s=.000051),dict(METHOD_EXAMPLE,speed_m_s=float('nan')),dict(METHOD_EXAMPLE,bogus=1)]:
            with self.assertRaises(ActionRejected):h.invoke('method.configure',**a)
            self.assertEqual(before,h.debug_snapshot())
    def test_gap_increment(self):
        h=configured();before=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke('gap.set',left_m=.0001015,right_m=.0001)
        self.assertEqual(before,h.debug_snapshot())
    def test_schema_negative_control(self):
        import importlib.util
        s=importlib.util.spec_from_file_location('tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');t=importlib.util.module_from_spec(s);s.loader.exec_module(t)
        self.assertTrue(t.validate_value({'enabled':1},CAPS['power.set'][1]));self.assertTrue(t.validate_value({'phase':'fake'},CAPS['acquisition.stop'][1]))
    def test_complete_and_mass(self):
        h=configured(evap=.0002);h.invoke('acquisition.start');h.step(20);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.q,.26)
        self.assertGreater(h._product()['film_solute_kg'],0);self.assertGreater(h.waste[1],0)
        for e in balance(h):self.assertLess(abs(e),1e-14)
    def test_analytic_inventory_integral(self):
        h=configured();v=h._volume(h.bead);rho=sum(h.bead)/v;c=h.charge_cfg;g=.0001;u=.02;length=.01
        a=.1*g/2;b=.1*rho*9.80665*g**3/(12*c['viscosity_pa_s']*c['blade_contact_length_m']*u*c['reservoir_area_m2'])
        expect=(v+a/b)*math.exp(-b*length)-a/b;h._deposit(0,length,u);self.assertAlmostEqual(h._volume(h.bead),expect,places=15)
    def test_not_gap_equals_thickness(self):
        h=configured();h.invoke('acquisition.start');h.step(20);p=h._product();self.assertGreater(p['wet_thickness_m'],.00005);self.assertLess(p['wet_thickness_m'],.0001)
    def test_pressure_speed_dependence(self):
        a=configured();b=configured();a._deposit(.03,.05,.001);b._deposit(.03,.05,.1);self.assertGreater(a._product()['film_solute_kg'],b._product()['film_solute_kg'])
    def test_empty_inventory_no_creation(self):
        h=configured(charge=False);h.invoke('charge.configure',**dict(CHARGE_EXAMPLE,solvent_kg=1e-8,solute_kg=1e-8));h.invoke('acquisition.start');h.step(20)
        self.assertEqual(h.phase,'succeeded');self.assertLess(sum(h.bead),1e-20);self.assertEqual(h._product()['film_solute_kg'],0.)
        for e in balance(h):self.assertLess(abs(e),1e-20)
    def test_model_rejects_large_gap(self):
        h=configured();h.invoke('gap.set',left_m=.01,right_m=.01);h.step(3);before=h.debug_snapshot()
        with self.assertRaisesRegex(ActionRejected,'MODEL_OUT_OF_RANGE'):h.invoke('acquisition.start')
        self.assertEqual(before,h.debug_snapshot())
    def test_no_reverse_coating(self):
        h=configured()
        with self.assertRaises(ActionRejected):h.invoke('stage.move_to',position_m=.1,speed_m_s=.02)
    def test_cancel_retains_and_freezes(self):
        h=configured();h.invoke('acquisition.start');h.step(3);p=h._product();q=h.q;h.invoke('acquisition.stop');self.assertEqual(p,h._product());h.step(1);self.assertEqual(h.q,q);self.assertEqual(h.phase,'cancelled')
    def test_evaporation_after_stop(self):
        h=configured(evap=.0002);h.invoke('acquisition.start');h.step(3);h.invoke('acquisition.stop');before=h.evaporated[0];h.step(1);self.assertGreater(h.evaporated[0],before)
        for e in balance(h):self.assertLess(abs(e),1e-14)
    def test_poweroff_rehome(self):
        h=configured();h.invoke('power.set',enabled=False);self.assertFalse(h.homed);h.invoke('power.set',enabled=True)
        with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
    def test_emergency_reset_sequence(self):
        h=configured();h.invoke('acquisition.start');h.step(3);q=h.q;h.invoke('emergency.stop');h.step(1);self.assertEqual(h.q,q);self.assertFalse(h.powered);self.assertEqual(h.phase,'failed')
        with self.assertRaises(ActionRejected):h.invoke('power.set',enabled=True)
        h.invoke('emergency.release')
        with self.assertRaises(ActionRejected):h.invoke('power.set',enabled=True)
        h.invoke('power.set',enabled=False);h.invoke('power.set',enabled=True);self.assertFalse(h.latched);self.assertFalse(h.homed)
    def test_reset_powercycle_before_release_not_enough(self):
        h=configured();h.invoke('emergency.stop');h.invoke('power.set',enabled=False);h.invoke('emergency.release')
        with self.assertRaises(ActionRejected):h.invoke('power.set',enabled=True)
    def test_collect_and_cleanup(self):
        h=configured();h.invoke('acquisition.start');h.step(20);h.invoke('product.collect',recipient_id='x');self.assertGreater(h.collected[1],0);h.invoke('reservoir.empty');h.invoke('stage.home');h.step(20);self.assertEqual(h.q,0.)
        with self.assertRaises(ActionRejected):h.invoke('product.collect',recipient_id='x')
        for e in balance(h):self.assertLess(abs(e),1e-14)
    def test_finite_drying(self):
        h=configured(evap=.1);h.invoke('acquisition.start');h.step(100);self.assertEqual(h.evaporated[1],0.)
        for e in balance(h):self.assertLess(abs(e),1e-14)
        self.assertTrue(all(v>=0 for v in [*h.bead,*h.waste,*h.evaporated,*h.collected,*[v for f in h.film for v in f]]))
    def test_reference(self):
        h=configured();self.assertAlmostEqual(h.invoke('reference.lookup',substance_id='ethanol',temperature_k=298.15)['vapor_pressure_pa'],7869.085,places=2)
        with self.assertRaises(ActionRejected):h.invoke('reference.lookup',substance_id='ethanol',temperature_k=5.)
    def test_instances_and_partition(self):
        a=configured('a');b=configured('b');before=b.debug_snapshot();a.invoke('acquisition.start');a.step(2);self.assertEqual(before,b.debug_snapshot());b.invoke('acquisition.start')
        for _ in range(20):b.step(.1)
        self.assertAlmostEqual(a.q,b.q,places=12)
        self.assertAlmostEqual(a._product()['film_solute_kg'],b._product()['film_solute_kg'],places=14)
    def test_invalid_clock(self):
        h=configured();before=h.debug_snapshot()
        for dt in [0,-1,float('nan'),True]:
            with self.assertRaises(ActionRejected):h.step(dt)
        self.assertEqual(before,h.debug_snapshot())
    def test_agent_truth_filter(self):
        h=configured();s=h.session();self.assertNotIn('truth.ledger',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities'])
        self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(ActionRejected):h.session('evaluator')
    def test_request_dedup_and_write(self):
        h=configured();s=h.session();a=dict(instance_id=h.instance_id,capability_id='power.toggle',arguments={},request_id='same');r=s.invoke(**a);powered=h.powered;self.assertEqual(s.invoke(**a),r);self.assertEqual(powered,h.powered)
        self.assertEqual(s.invoke(**dict(a,capability_id='power.set',arguments={'enabled':True}))['error']['code'],'REQUEST_CONFLICT')
        self.assertEqual(s.write(instance_id=h.instance_id,state_id='power.enabled',value=True,request_id='write')['status'],'succeeded')
        with self.assertRaises(ActionRejected):s.write(instance_id=h.instance_id,state_id='truth.ledger',value={},request_id='bad')
    def test_async_owner_cancel(self):
        h=configured();s=h.session();h.register_principal('other');other=h.session('other');r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=r['operation_id'];self.assertEqual(r['status'],'accepted');h.step(3)
        with self.assertRaises(ActionRejected):other.poll(op)
        before=h._product();s.cancel(op,'cancel');self.assertEqual(s.poll(op)['status'],'cancelled');self.assertEqual(h._product(),before)
    def test_zero_distance_transition_does_not_double_gap_clock(self):
        h=configured(charge=False);h.invoke('gap.set',left_m=.01,right_m=.01);before=h.gaps[0];h.invoke('stage.home');h.step(.005)
        self.assertAlmostEqual(h.gaps[0]-before,.005*.005,places=15)
    def test_no_implicit_time(self):
        h=configured();t=h.time_s;h.invoke('acquisition.start');h.invoke('coating.measure');self.assertEqual(t,h.time_s)
if __name__=='__main__':unittest.main()


