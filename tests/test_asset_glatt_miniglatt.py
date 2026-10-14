"""Mini-Glatt2012 actual numerical/contract boundary regression tests."""
import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.glatt_miniglatt import *
def prepared(method=None,plant=None,powder=None,liquid=None,micro=False):
    h=GlattMiniglattRuntime();h.invoke('plant.configure',**(plant or PLANT_EXAMPLE))
    h.invoke('kit.configure',micro=micro,micro_maximum_kg=.025,accept_host_attachment=True)
    h.invoke('powder.load',**(powder or POWDER_EXAMPLE));h.invoke('liquid.load',**(liquid or LIQUID_EXAMPLE))
    h.invoke('method.configure',**(method or METHOD_EXAMPLE));h.invoke('tower.set',opened=False);h.invoke('power.set',enabled=True);return h
class GlattTests(unittest.TestCase):
    def atomic(self,h,k,**a):
        before=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke(k,**a)
        self.assertEqual(before,h.debug_snapshot())
    def conserve(self,h):
        p=h.invoke('product.read')
        for i in range(3):self.assertAlmostEqual(p['initial_kg'][i],sum(p[x+'_kg'][i] for x in LOCS if x!='initial'),delta=1e-11)
        self.assertAlmostEqual(p['heater_input_j'],sum(p[k] for k in ['exhaust_sensible_j','loss_j','latent_j','vapor_sensible_j','bed_sensible_j','filter_sensible_j','collected_sensible_j']),delta=2e-6)
        self.assertTrue(all(v>=-1e-14 for loc in LOCS for v in p[loc+'_kg']))
    def test_default_conservation_and_finite_time(self):
        h=prepared();h.invoke('acquisition.start');h.step(60);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.elapsed_s,60.);self.conserve(h)
    def test_no_fluidization_atomic_start(self):
        h=prepared(method=dict(METHOD_EXAMPLE,air_normal_m3_s=1e-9));self.atomic(h,'acquisition.start');self.assertFalse(h.air)
    def test_drop_air_flow_stops_with_partial_material(self):
        h=prepared();h.invoke('acquisition.start');h.step(5);h.invoke('fluidization.set_flow',flow_normal_m3_s=0.);h.step(.02);self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'NOT_FLUIDIZED');self.conserve(h)
    def test_total_air_includes_atomizer(self):
        h=prepared();self.atomic(h,'fluidization.set_flow',flow_normal_m3_s=100/3600);h.invoke('fluidization.set_flow',flow_normal_m3_s=97/3600);h.invoke('acquisition.start');self.assertAlmostEqual(h._utility(),100/3600)
    def test_power_kva_not_kw(self):
        h=GlattMiniglattRuntime();self.atomic(h,'plant.configure',**dict(PLANT_EXAMPLE,heater_w=3300.))
    def test_inlet_power_limited(self):
        h=prepared(plant=dict(PLANT_EXAMPLE,heater_w=30.));h.invoke('acquisition.start');self.assertLess(h.read()['thermal.inlet_k'],333.15);self.assertGreater(h.read()['thermal.inlet_k'],298.15)
    def test_inlet_80_limit_atomic(self):
        h=prepared();h.invoke('thermal.set_temperature',temperature_k=353.15);self.atomic(h,'thermal.set_temperature',temperature_k=353.16)
    def test_temperature_target_not_bed_jump(self):
        h=prepared();t=h._temperature();h.invoke('thermal.set_temperature',temperature_k=353.15);self.assertEqual(t,h._temperature())
    def test_pump_100ml_min_limit(self):
        h=prepared();h.invoke('spray.set_rate',flow_m3_s=100e-6/60);self.atomic(h,'spray.set_rate',flow_m3_s=100.01e-6/60)
    def test_finite_tank_exhaustion(self):
        h=prepared(liquid=dict(LIQUID_EXAMPLE,volume_m3=.2e-6));h.invoke('acquisition.start');h.step(10);self.assertLess(sum(h.mass['tank']),1e-15);self.assertGreater(sum(h.mass['bed']),.08);self.conserve(h)
    def test_spray_stops_after_scheduled_window(self):
        h=prepared();h.invoke('acquisition.start');h.step(45);mass=copy.deepcopy(h.mass['tank']);h.step(15);self.assertEqual(mass,h.mass['tank'])
    def test_standard_25g_lower_limit(self):
        h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);self.atomic(h,'powder.load',**dict(POWDER_EXAMPLE,dry_mass_kg=.02499));h.invoke('powder.load',**dict(POWDER_EXAMPLE,dry_mass_kg=.025))
    def test_standard_375g_upper_limit(self):
        h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('powder.load',**dict(POWDER_EXAMPLE,dry_mass_kg=.375))
    def test_micro_minimum_and_bottom(self):
        h=prepared(micro=True,powder=dict(POWDER_EXAMPLE,dry_mass_kg=.0025,water_mass_kg=0.),method=dict(METHOD_EXAMPLE,spray_position='bottom'));h.invoke('acquisition.start');h.step(1);self.conserve(h)
    def test_micro_top_rejected(self):
        h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('kit.configure',micro=True,micro_maximum_kg=.025,accept_host_attachment=True);self.atomic(h,'method.configure',**METHOD_EXAMPLE)
    def test_micro_unknown_max_explicit(self):
        h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('kit.configure',micro=True,micro_maximum_kg=.01,accept_host_attachment=True);self.atomic(h,'powder.load',**dict(POWDER_EXAMPLE,dry_mass_kg=.02))
    def test_drying_no_spray(self):
        h=prepared(method=dict(METHOD_EXAMPLE,operation='drying',spray_m3_s=0.,spray_duration_s=0.));h.invoke('acquisition.start');h.step(60);self.assertAlmostEqual(h.mass['tank'][1],.0018);self.assertAlmostEqual(h.mass['tank'][2],.0282);self.assertGreater(h.mass['vapor'][2],0.);self.conserve(h)
    def test_coating_conserves_particle_count(self):
        h=prepared(method=dict(METHOD_EXAMPLE,operation='coating'));n=h.n_initial;h.invoke('acquisition.start');h.step(60);self.assertAlmostEqual(sum(h.particles.values())/n,1.,places=11);self.assertGreater(h._diameter(),POWDER_EXAMPLE['seed_diameter_m']);self.conserve(h)
    def test_aggregation_reduces_count(self):
        h=prepared(powder=dict(POWDER_EXAMPLE,breakage_s_inv=0.,aggregation_s_inv=.2));h.invoke('acquisition.start');h.step(60);self.assertLess(sum(h.particles.values()),h.n_initial);self.assertGreater(h._diameter(),100e-6)
    def test_breakage_no_mass_loss(self):
        h=prepared(powder=dict(POWDER_EXAMPLE,aggregation_s_inv=0.,breakage_s_inv=.0001));h.invoke('acquisition.start');h.step(10);self.assertGreater(sum(h.particles.values()),h.n_initial);self.conserve(h)
    def test_filter_clog_fails(self):
        h=prepared(plant=dict(PLANT_EXAMPLE,filter_resistance_pa_per_kg=1e8,filter_trip_pa=100.));h.invoke('acquisition.start');h.step(2);self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'FILTER_CLOGGED');self.conserve(h)
    def test_blowback_requires_step_and_returns_fraction(self):
        h=prepared();h.invoke('acquisition.start');h.step(20);h.invoke('acquisition.stop');old=copy.deepcopy(h.mass['filter']);air=h.air_used
        h.invoke('filter.clean');self.assertEqual(old,h.mass['filter']);h.step(.5)
        for a,b in zip(old,h.mass['filter']):self.assertAlmostEqual(b,a*.15,delta=1e-14)
        self.assertAlmostEqual(h.air_used-air,100/3600*.5,delta=1e-12);self.conserve(h)
    def test_blowback_not_during_run(self):
        h=prepared();h.invoke('acquisition.start');self.atomic(h,'filter.clean')
    def test_open_tower_interrupts_preserves(self):
        h=prepared();h.invoke('acquisition.start');h.step(10);h.invoke('tower.set',opened=True);m=copy.deepcopy(h.mass);h.step(10);self.assertEqual(m,h.mass);self.assertEqual(h.phase,'cancelled');self.conserve(h)
    def test_power_loss_preserves(self):
        h=prepared();h.invoke('acquisition.start');h.step(10);h.invoke('power.set',enabled=False);m=copy.deepcopy(h.mass);h.step(10);self.assertEqual(m,h.mass);self.assertFalse(h.air);self.conserve(h)
    def test_collection_keeps_filter_and_heat(self):
        h=prepared();h.invoke('acquisition.start');h.step(60);f=copy.deepcopy(h.mass['filter']);h.invoke('tower.set',opened=True);h.invoke('product.collect',recipient_id='receiver');self.assertEqual(f,h.mass['filter']);self.assertEqual(sum(h.mass['bed']),0.);self.conserve(h)
    def test_no_rebind_powder_or_liquid(self):
        h=prepared();h.invoke('tower.set',opened=True);self.atomic(h,'powder.load',**POWDER_EXAMPLE);self.atomic(h,'liquid.load',**LIQUID_EXAMPLE)
    def test_heater_requires_flow(self):
        h=prepared();self.atomic(h,'heating.set',enabled=True)
    def test_queries_read_describe_pure(self):
        h=prepared();before=h.debug_snapshot()
        h.read();h.session().read(instance_id=h.instance_id);h.session().describe(instance_id=h.instance_id)
        for k in ['product.read','granulation.measure']:h.invoke(k)
        h.invoke('reference.lookup',substance_id='mutylo_g1');self.assertEqual(before,h.debug_snapshot())
    def test_real_sieve_not_DLS(self):
        h=prepared();x=h.invoke('reference.lookup',substance_id='mutylo_g2');self.assertGreater(x['mass_fraction'][0],.5);self.assertEqual(x['median_interval_m'][0],0.);self.assertAlmostEqual(x['median_interval_m'][1],90e-6);self.assertTrue(x['last_bin_open']);self.assertEqual(x['evidence'],'measured_literature')
    def test_truth_filter(self):
        h=prepared();s=h.session();self.assertNotIn('truth.ledger',s.describe(instance_id=h.instance_id)['state_slots'])
        self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='x')['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
    def test_session_idempotency_conflict(self):
        h=prepared();s=h.session();a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='once');r=s.invoke(**a);before=h.debug_snapshot();self.assertEqual(r,s.invoke(**a));self.assertEqual(before,h.debug_snapshot());self.assertEqual(s.invoke(**dict(a,capability_id='acquisition.stop'))['error']['code'],'REQUEST_CONFLICT')
    def test_poll_owner_and_cancel_partial(self):
        h=prepared();s=h.session();h.register_principal('other');op=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a')['operation_id'];h.step(3)
        with self.assertRaises(ActionRejected):h.session('other').poll(op)
        s.cancel(op,'cancel');self.assertEqual(s.poll(op)['status'],'cancelled');self.conserve(h)
    def test_write_route(self):
        h=prepared();h.session().write(instance_id=h.instance_id,state_id='thermal.target_k',value=340.,request_id='temp');self.assertEqual(h.method['temperature_k'],340.)
        with self.assertRaises(ActionRejected):h.session().write(instance_id=h.instance_id,state_id='truth.ledger',value={},request_id='bad')
    def test_foreign_instance(self):
        h=prepared();self.assertEqual(h.session().invoke(instance_id='foreign',capability_id='power.set',arguments={'enabled':False},request_id='x')['error']['code'],'UNKNOWN_INSTANCE');self.assertTrue(h.powered)
    def test_panel_toggles(self):
        h=prepared();h.invoke('air.toggle');self.assertTrue(h.air);h.invoke('heating.toggle');self.assertTrue(h.heating);h.invoke('air.toggle');self.assertFalse(h.heating)
    def test_panel_heat_toggle_atomic_without_air(self):
        h=prepared();self.atomic(h,'heating.toggle')
    def test_two_instances_isolated(self):
        a=prepared();b=prepared();before=b.debug_snapshot();a.invoke('acquisition.start');a.step(10);self.assertEqual(before,b.debug_snapshot())
    def test_nan_bool_and_unknown_atomic(self):
        h=prepared();self.atomic(h,'spray.set_rate',flow_m3_s=True);self.atomic(h,'fluidization.set_flow',flow_normal_m3_s=float('nan'));self.atomic(h,'reference.lookup',substance_id='missing')
    def test_zero_and_invalid_time(self):
        h=prepared();before=h.debug_snapshot();h.step(0);self.assertEqual(before,h.debug_snapshot())
        with self.assertRaises(ActionRejected):h.step(-1)
    def test_time_partition_selected_case(self):
        a=prepared();b=prepared();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(60)
        for _ in range(6000):b.step(.01)
        for loc in LOCS:
            for x,y in zip(a.mass[loc],b.mass[loc]):self.assertAlmostEqual(x,y,delta=2e-7)
        self.assertAlmostEqual(a._temperature(),b._temperature(),delta=.005);self.conserve(a);self.conserve(b)
    def test_normal_flow_conversion_and_ergun_weight(self):
        h=prepared();h.invoke('acquisition.start');x=h._hydraulics();q,t,_=h._gas();self.assertAlmostEqual(x['u'],q*t/273.15/PLANT_EXAMPLE['bed_area_m2']);self.assertTrue(x['fluidized']);self.assertLess(x['dp'],sum(h.mass['bed'])*9.80665/PLANT_EXAMPLE['bed_area_m2'])
if __name__=='__main__':unittest.main()
