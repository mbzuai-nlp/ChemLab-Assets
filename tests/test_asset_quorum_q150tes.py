"""Q150TES source/gas/heat conservation and protocol regression tests."""
import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.quorum_q150tes import *
def prepared(source=None,plant=None,method=None):
    h=QuorumQ150tesRuntime();h.invoke('plant.configure',**(plant or PLANT_EXAMPLE));h.invoke('source.install',**(source or SOURCE_EXAMPLE));h.invoke('samples.load',**SAMPLES_EXAMPLE)
    h.invoke('method.configure',**(method or METHOD_EXAMPLE));h.invoke('chamber.set',opened=False);h.invoke('power.set',enabled=True);return h
def run(h,t=100):
    h.invoke('acquisition.start');h.step(t);return h
def access(h):
    if h.phase=='running':h.invoke('acquisition.stop')
    h.invoke('vacuum.vent');h.step(5);h.invoke('chamber.set',opened=True);h.invoke('power.set',enabled=False)
class QuorumTests(unittest.TestCase):
    def atomic(self,h,k,**a):
        before=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke(k,**a)
        self.assertEqual(before,h.debug_snapshot())
    def conserve(self,h):
        p=h.invoke('product.read')
        for lot in p['lots']:
            total=sum(lot[k] for k in ['remaining_kg','stock_kg','returned_kg','wall_kg','shutter_kg','crystal_kg','waste_kg'])+sum(lot['sample_kg'])+sum(lot['collected_kg'])
            self.assertAlmostEqual(total,lot['initial_kg'],delta=1e-12)
            self.assertTrue(all(lot[k]>=-1e-16 for k in ['remaining_kg','stock_kg','returned_kg','wall_kg','shutter_kg','crystal_kg','waste_kg']))
        self.assertAlmostEqual(p['gas_initial_mol']+p['gas_outgas_mol']+p['gas_argon_mol']+p['gas_vent_mol'],p['gas_chamber_mol']+p['gas_pumped_mol'],delta=1e-11)
        self.assertAlmostEqual(p['electrical_j'],p['environment_j']+p['carbon_latent_j']+sum(p['sample_sensible_j'])+p['collected_sensible_j']+sum(x['source_sensible_j'] for x in p['lots']),delta=1e-7)
    def test_sputter_complete_finite_deposition(self):
        h=run(prepared());self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.deposit_s,30);self.assertGreater(h.elapsed_s,35);self.assertGreater(sum(h._lot()['sample_kg']),0);self.conserve(h)
    def test_carbon_complete_and_stock_not_consumed(self):
        h=run(prepared(source=CARBON_EXAMPLE));self.assertEqual(h.phase,'succeeded');self.assertGreater(sum(h._lot()['sample_kg']),0);self.assertAlmostEqual(h._lot()['stock_kg']/h._lot()['initial_kg'],.8);self.conserve(h)
    def test_vacuum_alone_no_coating(self):
        h=prepared();h.invoke('vacuum.pump',enabled=True);h.step(20);self.assertAlmostEqual(h._p(),.005,places=8);self.assertEqual(sum(h._lot()['sample_kg']),0);self.assertEqual(h.electric,0);self.conserve(h)
    def test_working_not_ultimate_pressure(self):
        h=prepared();h.invoke('acquisition.start');h.step(17);self.assertEqual(h.stage,'deposition');self.assertAlmostEqual(h._p(),1,places=3)
    def test_pressure_and_current_limits(self):
        h=prepared()
        for key,value in [('working_pressure_pa',.49),('working_pressure_pa',10.01),('sputter_current_a',.1501),('sputter_current_a',.00099),('carbon_current_a',60.01),('duration_s',3600.1),('rotation_rpm',7.99),('rotation_rpm',20.01)]:
            self.atomic(h,'method.configure',**dict(METHOD_EXAMPLE,**{key:value}))
    def test_unknown_host_parameters_not_implicit(self):
        h=QuorumQ150tesRuntime();self.atomic(h,'power.set',enabled=True);self.atomic(h,'plant.configure',**dict(PLANT_EXAMPLE,accept_uncalibrated_model=False))
    def test_plant_once_no_reinterpretation(self):
        h=prepared();access(h);self.atomic(h,'plant.configure',**PLANT_EXAMPLE)
    def test_cr_frozen_thickness(self):
        h=QuorumQ150tesRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);self.atomic(h,'source.install',**dict(SOURCE_EXAMPLE,target_thickness_m=.0003))
    def test_incompatible_source_rejected(self):
        h=QuorumQ150tesRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE)
        self.atomic(h,'source.install',**dict(SOURCE_EXAMPLE,substance_id='ic6_004'));self.atomic(h,'source.install',**dict(CARBON_EXAMPLE,substance_id='ic6_032'))
    def test_finite_source_exhaustion_preserves_mass(self):
        h=run(prepared(plant=dict(PLANT_EXAMPLE,sputter_yield_kg_c=.001),method=dict(METHOD_EXAMPLE,duration_s=3600)),300);self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'SOURCE_EMPTY');self.conserve(h)
    def test_preclean_intercepts_flux(self):
        h=prepared(method=dict(METHOD_EXAMPLE,preclean_s=60.));h.invoke('acquisition.start');h.step(20);self.assertEqual(h.stage,'preclean');self.assertGreater(h._lot()['shutter_kg'],0);self.assertEqual(sum(h._lot()['sample_kg']),0);self.assertEqual(h._ftm(),0);self.conserve(h)
    def test_cooling_pauses_source(self):
        h=prepared();h.invoke('acquisition.start')
        for _ in range(4000):
            h.step(.02)
            if h.stage=='cooling':break
        self.assertEqual(h.stage,'cooling');mass=h._lot()['remaining_kg'];elapsed=h.deposit_s;h.step(1);self.assertEqual(mass,h._lot()['remaining_kg']);self.assertEqual(elapsed,h.deposit_s);self.conserve(h)
    def test_ftm_requires_option(self):
        h=prepared();self.atomic(h,'method.configure',**dict(METHOD_EXAMPLE,ftm_enabled=False,thickness_endpoint=True))
    def test_ftm_endpoint_not_time_claim(self):
        h=run(prepared(method=dict(METHOD_EXAMPLE,thickness_endpoint=True,endpoint_m=1e-9)));self.assertEqual(h.phase,'succeeded');self.assertLess(h.deposit_s,30);self.assertGreaterEqual(h._ftm(),1e-9);self.assertLess(h._ftm(),1.02e-9);self.conserve(h)
    def test_ftm_density_vs_actual_film_density(self):
        a=run(prepared());b=run(prepared(source=dict(SOURCE_EXAMPLE,film_density_kg_m3=3600.)))
        self.assertAlmostEqual(a._ftm(),b._ftm());self.assertAlmostEqual(sum(a._lot()['sample_kg']),sum(b._lot()['sample_kg']))
        self.assertNotEqual(a._lot()['film_density_kg_m3'],b._lot()['film_density_kg_m3'])
    def test_ftm_disabled_no_crystal_capture(self):
        h=run(prepared(method=dict(METHOD_EXAMPLE,ftm_enabled=False)));self.assertEqual(h._ftm(),0);self.assertEqual(h._lot()['crystal_kg'],0);self.conserve(h)
    def test_rotation_changes_angular_distribution(self):
        a=run(prepared());b=run(prepared(method=dict(METHOD_EXAMPLE,rotation_enabled=False)))
        self.assertEqual(b.angle,0);self.assertNotEqual(a._lot()['sample_kg'],b._lot()['sample_kg'])
        self.assertLess(max(a._lot()['sample_kg'])/min(a._lot()['sample_kg']),max(b._lot()['sample_kg'])/min(b._lot()['sample_kg']))
    def test_stage_open_adjust_only(self):
        h=prepared();self.atomic(h,'stage.set_height',lift_m=.01);access(h);h.invoke('stage.set_height',lift_m=.01);self.assertEqual(h.read()['stage.lift_m'],.01)
    def test_source_switch_preserves_old_identity_and_returned_mass(self):
        h=run(prepared());access(h);h.invoke('samples.collect',recipient_id='metal_coated');before=copy.deepcopy(h._lot());h.invoke('source.install',**CARBON_EXAMPLE)
        self.assertEqual(h.lots[0]['substance_id'],'ic6_052');self.assertEqual(h.lots[0]['returned_kg'],before['remaining_kg']);self.assertEqual(h._lot()['substance_id'],'ic6_031');self.conserve(h)
    def test_source_exchange_while_samples_loaded_rejected(self):
        h=prepared();access(h);self.atomic(h,'source.install',**CARBON_EXAMPLE)
    def test_collect_and_clean_preserve_waste(self):
        h=run(prepared());access(h);h.invoke('samples.collect',recipient_id='holder');wall=h._lot()['wall_kg'];h.invoke('service.clean',accept_host_cleaning=True);self.assertEqual(h._lot()['wall_kg'],0);self.assertGreater(h._lot()['waste_kg'],wall);self.assertGreater(sum(h._lot()['collected_kg']),0);self.conserve(h)
    def test_sample_identity_cannot_reuse_after_collect(self):
        h=prepared();access(h);h.invoke('samples.collect',recipient_id='holder');self.atomic(h,'samples.load',**SAMPLES_EXAMPLE)
    def test_duplicate_ids_rejected(self):
        h=QuorumQ150tesRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('source.install',**SOURCE_EXAMPLE);self.atomic(h,'samples.load',**dict(SAMPLES_EXAMPLE,sample_ids=['one']*6))
    def test_open_under_vacuum_atomic(self):
        h=prepared();h.invoke('vacuum.pump',enabled=True);h.step(10);self.atomic(h,'chamber.set',opened=True)
    def test_vent_then_open(self):
        h=run(prepared());access(h);self.assertTrue(h.opened);self.assertFalse(h.pump);self.assertFalse(h.powered);self.conserve(h)
    def test_power_failure_preserves_coating(self):
        h=prepared();h.invoke('acquisition.start');h.step(20);mass=copy.deepcopy(h._lot());h.invoke('power.set',enabled=False);h.step(10);self.assertEqual(h.phase,'cancelled');self.assertEqual(h._lot()['sample_kg'],mass['sample_kg']);self.conserve(h)
    def test_cancellation_preserves_partial_product(self):
        h=prepared();h.invoke('acquisition.start');h.step(20);h.invoke('acquisition.stop');partial=h._lot()['sample_kg'][:];h.step(10);self.assertEqual(partial,h._lot()['sample_kg']);self.conserve(h)
    def test_gas_leak_blocks_vacuum(self):
        h=run(prepared(plant=dict(PLANT_EXAMPLE,outgas_pa_m3_s=.1)));self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'PUMP_TIMEOUT');self.assertEqual(h.electric,0);self.conserve(h)
    def test_sample_overheat_stops(self):
        h=run(prepared(plant=dict(PLANT_EXAMPLE,sample_heat_capacity_j_k=.01,sample_loss_w_k=0.,sample_trip_k=316.)));self.assertEqual(h.phase,'failed');self.assertEqual(h.warning,'SAMPLE_OVERHEAT');self.conserve(h)
    def test_real_reference_counts_and_Z_unknown(self):
        h=prepared();self.assertEqual(len(h.templates),255);self.assertEqual(sum(x['values']['z_established'] for x in h.templates.values()),117)
        self.assertFalse(h.invoke('reference.lookup',substance_id='ic6_005')['z_established']);self.assertEqual(h.invoke('reference.lookup',substance_id='ic6_052')['density_kg_m3'],7200)
    def test_all_255_conversions_from_real_density(self):
        h=prepared()
        for sid in h.templates:
            r=h.invoke('qcm.convert',substance_id=sid,areal_mass_kg_m2=.0001,tooling=2.);self.assertAlmostEqual(r['thickness_m']*r['density_kg_m3'],.0002)
    def test_invalid_numbers_and_step_atomic(self):
        h=prepared()
        for value in [True,float('nan'),float('inf'),-1]:
            self.atomic(h,'rotation.set',enabled=True,rpm=value)
            snap=h.debug_snapshot()
            with self.assertRaises(ActionRejected):h.step(value)
            self.assertEqual(snap,h.debug_snapshot())
    def test_queries_never_advance_and_copy(self):
        h=prepared();before=h.debug_snapshot();r=h.invoke('product.read');r['lots'][0]['remaining_kg']=0;h.read()['cycle.phase']='failed';h.invoke('coating.measure');self.assertEqual(before,h.debug_snapshot())
    def test_zero_and_fractional_time(self):
        h=prepared();h.step(0);self.assertEqual(h.time_s,0);h.invoke('acquisition.start');h.step(47.113);self.assertAlmostEqual(h.time_s,47.113,places=8);self.conserve(h)
    def test_step_partition_equivalence(self):
        a=prepared();b=prepared();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(40)
        for _ in range(400):b.step(.1)
        self.assertAlmostEqual(a._ftm(),b._ftm(),delta=1e-17);self.assertEqual(a.phase,b.phase);self.conserve(b)
    def test_default_and_private_metadata(self):
        h=prepared();s=h.session();d=s.describe(instance_id=h.instance_id);self.assertNotIn('product.read',d['capabilities']);self.assertNotIn('truth.ledger',d['state_slots'])
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
        r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
    def test_async_owner_idempotency_and_conflict(self):
        h=prepared();h.register_principal('other');s=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');r=s.invoke(**kw);self.assertEqual(r,s.invoke(**kw));self.assertEqual(h._operation_count,1)
        conflict=s.invoke(**dict(kw,capability_id='acquisition.stop'));self.assertEqual(conflict['error']['code'],'REQUEST_CONFLICT')
        with self.assertRaises(ActionRejected):h.session('other').poll(r['operation_id'])
        self.assertEqual(s.poll(r['operation_id'])['status'],'running');s.cancel(r['operation_id'],'cancel');self.assertEqual(h.phase,'cancelled')
    def test_unknown_instance_and_forged_role(self):
        h=prepared();r=h.session().invoke(instance_id='wrong',capability_id='power.set',arguments={'enabled':True},request_id='x');self.assertEqual(r['error']['code'],'UNKNOWN_INSTANCE')
        with self.assertRaises(ActionRejected):h.session('evaluator')
        with self.assertRaises(ActionRejected):h.register_principal('bad','root')
    def test_two_instances_isolated(self):
        a=run(prepared());b=prepared();self.assertEqual(b.phase,'idle');self.assertEqual(sum(b._lot()['sample_kg']),0);self.assertNotEqual(a._ftm(),b._ftm())
    def test_method_busy_rejection_atomic(self):
        h=prepared();h.invoke('acquisition.start');self.atomic(h,'method.configure',**METHOD_EXAMPLE)
if __name__=='__main__':unittest.main()

