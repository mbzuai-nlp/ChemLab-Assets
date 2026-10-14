"""CHEM-060 independent regression tests; finite material, process clock and trust boundary."""
import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.cytiva_nanoassemblrignite import *
def prepared(dilution=True,method=None):
    h=CytivaNanoassemblrigniteRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE)
    h.invoke('cartridge.install',dilution=dilution,cartridge_id='chip',accept_host_attachment=True)
    for x in SYRINGE_EXAMPLES:h.invoke('syringe.load',**x)
    h.invoke('tubes.install',sample_capacity_m3=50e-6,accept_host_attachment=True)
    h.invoke('heating.configure',installed=True,accept_host_attachment=True)
    h.invoke('method.configure',**(method or dict(METHOD_EXAMPLE,dilution_ratio=1. if dilution else 0.)))
    h.invoke('block.set',opened=False);h.invoke('lid.set',opened=False);h.invoke('power.set',enabled=True);return h
class IgniteTests(unittest.TestCase):
    def atomic(self,h,k,**a):
        before=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke(k,**a)
        self.assertEqual(before,h.debug_snapshot())
    def conserve(self,h):
        p=h.invoke('product.read')
        for suffix in ['m3','kg','solute_kg']:
            for i in range(3):self.assertAlmostEqual(p['initial_'+suffix][i],sum(p[loc+'_'+suffix][i] for loc in LOCS if loc!='initial'),delta=1e-11)
        self.assertAlmostEqual(p['heater_input_j'],p['thermal_loss_j']+p['sensible_j'],delta=2e-7)
    def test_default_conservation(self):
        h=prepared();h.invoke('acquisition.start');h.step(60);self.conserve(h);self.assertEqual(h.phase,'succeeded')
    def test_exact_routing_and_holdup(self):
        h=prepared();h.invoke('acquisition.start');h.step(60)
        self.assertAlmostEqual(sum(h.vols['sample']),3.47e-6,delta=1e-14);self.assertAlmostEqual(sum(h.vols['waste']),.5e-6,delta=1e-14)
        self.assertAlmostEqual(sum(h.vols['mixer']),20e-9,delta=1e-15);self.assertAlmostEqual(sum(h.vols['dilution']),10e-9,delta=1e-15)
    def test_L_not_in_primary_mixer(self):
        h=prepared();h.invoke('acquisition.start');h.step(30);self.assertEqual(h.vols['mixer'][0],0.);self.assertGreater(h.vols['dilution'][0],0.)
    def test_base_chip_two_inputs(self):
        h=prepared(False);h.invoke('acquisition.start');h.step(60);self.assertEqual(h.vols['syringe'][0],10e-6);self.assertEqual(sum(h.vols['dilution']),0.);self.assertAlmostEqual(sum(h.vols['sample']),1.73e-6,delta=1e-14);self.conserve(h)
    def test_incorrect_chip_topology_atomic(self):
        h=prepared(False,dict(METHOD_EXAMPLE));self.atomic(h,'acquisition.start')
    def test_20_flow_strictly_rejected(self):
        h=prepared();self.atomic(h,'method.configure',**dict(METHOD_EXAMPLE,flow_m3_s=20e-6/60))
    def test_just_below_20_accepted(self):
        h=prepared();h.invoke('method.configure',**dict(METHOD_EXAMPLE,flow_m3_s=math.nextafter(20e-6/60,0.)))
    def test_ignite_plus_ranges_rejected(self):
        h=prepared()
        for args in [dict(METHOD_EXAMPLE,volume_m3=60e-6),dict(METHOD_EXAMPLE,flow_m3_s=200e-6/60)]:self.atomic(h,'method.configure',**args)
    def test_method_low_flow_high_ratio(self):
        h=prepared();h.invoke('method.configure',**dict(METHOD_EXAMPLE,flow_m3_s=.1e-6/60,flow_ratio_c_r=10.,dilution_ratio=1.))
    def test_L_48_limit(self):
        h=prepared();h.invoke('method.configure',**dict(METHOD_EXAMPLE,flow_m3_s=8e-6/60,dilution_ratio=6.));self.atomic(h,'method.configure',**dict(METHOD_EXAMPLE,flow_m3_s=8e-6/60,dilution_ratio=6.01))
    def test_waste_sum_rejected(self):
        h=prepared();self.atomic(h,'method.configure',**dict(METHOD_EXAMPLE,start_waste_equiv_m3=2e-6))
    def test_all_waste_zero_sample(self):
        h=prepared(method=dict(METHOD_EXAMPLE,start_waste_equiv_m3=2e-6,end_waste_equiv_m3=0.));h.invoke('acquisition.start');h.step(60);self.assertEqual(sum(h.vols['sample']),0.);self.conserve(h)
    def test_no_waste_all_output_sample(self):
        h=prepared(method=dict(METHOD_EXAMPLE,start_waste_equiv_m3=0.,end_waste_equiv_m3=0.));h.invoke('acquisition.start');h.step(60);self.assertEqual(sum(h.vols['waste']),0.)
    def test_finite_empty_rejection(self):
        h=prepared(method=dict(METHOD_EXAMPLE,volume_m3=10e-6));self.atomic(h,'acquisition.start');self.assertFalse(h.used)
    def test_tube_capacity_preflight(self):
        h=prepared(method=dict(METHOD_EXAMPLE,start_waste_equiv_m3=2e-6,end_waste_equiv_m3=0.,dilution_ratio=4.))
        h.vols['waste'][0]=10e-6
        self.atomic(h,'acquisition.start')
    def test_lid_interrupts_not_rejected(self):
        h=prepared();h.invoke('acquisition.start');h.step(12);h.invoke('lid.set',opened=True);v=copy.deepcopy(h.vols);h.step(10);self.assertTrue(h.lid);self.assertEqual(h.phase,'cancelled');self.assertEqual(v,h.vols);self.conserve(h)
    def test_power_loss_partial(self):
        h=prepared();h.invoke('acquisition.start');h.step(12);h.invoke('power.set',enabled=False);v=copy.deepcopy(h.vols);h.step(10);self.assertEqual(v,h.vols);self.assertFalse(h.heating);self.conserve(h)
    def test_cancel_reset_no_refund(self):
        h=prepared();h.invoke('acquisition.start');h.step(12);h.invoke('acquisition.stop');v=copy.deepcopy(h.vols);pl=[h._consumed(i) for i in range(3)]
        h.invoke('cycle.reset');h.step(20);self.assertEqual(h.paddle,[0.]*3);self.assertEqual(v,h.vols);self.assertEqual(pl,[h._consumed(i) for i in range(3)])
    def test_completed_paddles_home_plungers_consumed(self):
        h=prepared();h.invoke('acquisition.start');h.step(60);self.assertEqual(h.paddle,[0.]*3);self.assertGreater(h._consumed(1),0.)
    def test_single_use_no_second_run(self):
        h=prepared();h.invoke('acquisition.start');h.step(60);self.atomic(h,'acquisition.start')
    def test_prime_6mm_not_fixed_volume(self):
        h=prepared();h.invoke('cartridge.prime');h.step(40)
        for i in range(3):self.assertAlmostEqual(h._consumed(i),.006,delta=1e-10)
        self.assertFalse(h.used);self.assertTrue(h.primed);self.assertEqual(sum(h.vols['sample']),0.);self.conserve(h)
    def test_prime_then_formulate(self):
        h=prepared();h.invoke('cartridge.prime');h.step(40);h.invoke('acquisition.start');h.step(60);self.conserve(h);self.assertTrue(h.used)
    def test_loaded_source_rebind_rejected(self):
        h=prepared();h.invoke('lid.set',opened=True);h.invoke('block.set',opened=True);h.invoke('syringe.unload',channel=1)
        self.atomic(h,'syringe.load',**dict(SYRINGE_EXAMPLES[1],density_kg_m3=900.))
    def test_syringe_stroke_limit(self):
        h=CytivaNanoassemblrigniteRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);self.atomic(h,'syringe.load',**dict(SYRINGE_EXAMPLES[1],inner_diameter_m=.002))
    def test_20ml_C_heated_configuration_not_assumed(self):
        h=CytivaNanoassemblrigniteRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);self.atomic(h,'syringe.load',**dict(SYRINGE_EXAMPLES[1],capacity_m3=20e-6))
    def test_invalid_solute_density_rejected(self):
        h=CytivaNanoassemblrigniteRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);self.atomic(h,'syringe.load',**dict(SYRINGE_EXAMPLES[1],solute_kg_m3=900.,density_kg_m3=800.))
    def test_collect_and_dispose_conservation(self):
        h=prepared();h.invoke('acquisition.start');h.step(60);h.invoke('lid.set',opened=True);h.invoke('tubes.collect',recipient_id='vial');h.invoke('block.set',opened=True);h.invoke('cartridge.remove')
        for i in range(3):h.invoke('syringe.unload',channel=i)
        self.conserve(h);self.assertEqual(sum(h.vols['mixer']),0.);self.assertGreater(sum(h.vols['collected_sample']),0.)
    def test_heater_75_and_30_achievable(self):
        h=prepared();o=h.invoke('heating.set',enabled=True,targets_k=[298.15,348.15]);self.assertEqual(h.targets,[298.15,348.15]);self.assertEqual(o['effective_targets_k'],[318.15,348.15])
        h.step(1200);self.assertAlmostEqual(h.temperature[0],318.15,places=6);self.assertAlmostEqual(h.temperature[1],348.15,places=6);self.conserve(h)
    def test_heater_upper_invalid_atomic(self):
        h=prepared();self.atomic(h,'heating.set',enabled=True,targets_k=[348.16,298.15])
    def test_heater_option_required(self):
        h=prepared();h.invoke('lid.set',opened=True);h.invoke('heating.configure',installed=False,accept_host_attachment=True);self.atomic(h,'heating.set',enabled=True,targets_k=[303.15]*2)
    def test_heat_no_fluid_size_prediction(self):
        h=prepared();h.invoke('heating.set',enabled=True,targets_k=[310.,315.]);h.step(200);self.assertIn('180',h.warning);self.assertFalse(h.invoke('formulation.measure')['particle_size_mapping_available']);self.conserve(h)
    def test_read_and_queries_are_pure(self):
        h=prepared();before=h.debug_snapshot()
        for k in ['formulation.measure','product.read']:h.invoke(k)
        h.invoke('reference.lookup',substance_id='lnp_atlas_913');h.read();h.session().read(instance_id=h.instance_id);self.assertEqual(before,h.debug_snapshot())
    def test_reference_methods_distinct(self):
        h=prepared();x=h.invoke('reference.lookup',substance_id='lnp_atlas_913');self.assertEqual([a['method'] for a in x['measurements']],['TDA','DLS']);self.assertAlmostEqual(x['measurements'][1]['value'],93e-9);self.assertEqual(x['evidence'],'measured_literature')
    def test_unknown_reference_and_nan_atomic(self):
        h=prepared();self.atomic(h,'reference.lookup',substance_id='missing');self.atomic(h,'method.configure',**dict(METHOD_EXAMPLE,volume_m3=float('nan')))
    def test_permission_truth_filter(self):
        h=prepared();s=h.session();self.assertNotIn('truth.ledger',s.describe(instance_id=h.instance_id)['state_slots'])
        self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny')['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
    def test_session_idempotency_conflict(self):
        h=prepared();s=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='once')
        first=s.invoke(**kw);snap=h.debug_snapshot();self.assertEqual(first,s.invoke(**kw));self.assertEqual(snap,h.debug_snapshot())
        self.assertEqual(s.invoke(**dict(kw,capability_id='acquisition.stop'))['error']['code'],'REQUEST_CONFLICT')
    def test_session_owner_poll_cancel(self):
        h=prepared();a=h.session();h.register_principal('other');b=h.session('other')
        op=a.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a')['operation_id'];h.step(12)
        with self.assertRaises(ActionRejected):b.poll(op)
        a.cancel(op,'cancel');self.assertEqual(a.poll(op)['status'],'cancelled');self.conserve(h)
    def test_state_write_route(self):
        h=prepared();s=h.session();s.write(instance_id=h.instance_id,state_id='lid.opened',value=True,request_id='open');self.assertTrue(h.lid)
        with self.assertRaises(ActionRejected):s.write(instance_id=h.instance_id,state_id='volume.sample_m3',value=1.,request_id='bad')
    def test_foreign_instance_rejected(self):
        h=prepared();self.assertEqual(h.session().invoke(instance_id='foreign',capability_id='power.set',arguments={'enabled':False},request_id='x')['error']['code'],'UNKNOWN_INSTANCE');self.assertTrue(h.powered)
    def test_clock_and_two_instances(self):
        a=prepared();b=prepared();a.invoke('acquisition.start');a.step(20);self.assertEqual(b.time_s,0.);self.assertEqual(b.phase,'idle');self.conserve(a)
    def test_time_partition_consistency(self):
        a=prepared();b=prepared();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(60)
        for _ in range(6000):b.step(.01)
        for loc in LOCS:
            for x,y in zip(a.vols[loc],b.vols[loc]):self.assertAlmostEqual(x,y,delta=1e-11)
    def test_zero_dt_and_invalid_dt(self):
        h=prepared();snap=h.debug_snapshot();h.step(0);self.assertEqual(snap,h.debug_snapshot())
        with self.assertRaises(ActionRejected):h.step(-1)
    def test_block_open_cannot_close_lid(self):
        h=prepared();h.invoke('lid.set',opened=True);h.invoke('block.set',opened=True);self.atomic(h,'lid.set',opened=False)
    def test_exhaustion_exact_batch(self):
        h=prepared(method=dict(METHOD_EXAMPLE,volume_m3=4e-6));h.invoke('acquisition.start');h.step(100);self.assertLess(h.vols['syringe'][1],1e-15);self.assertLess(h.vols['syringe'][2],1e-15);self.conserve(h)
    def test_used_chip_identity_cannot_reset(self):
        h=prepared();h.invoke('acquisition.start');h.step(60);h.invoke('lid.set',opened=True);h.invoke('block.set',opened=True);h.invoke('cartridge.remove')
        self.atomic(h,'cartridge.install',dilution=True,cartridge_id='chip',accept_host_attachment=True)
        h.invoke('cartridge.install',dilution=True,cartridge_id='fresh_chip',accept_host_attachment=True);self.assertFalse(h.used)
    def test_full_20ml_volume_with_two_10ml_syringes(self):
        h=prepared(False,dict(METHOD_EXAMPLE,flow_m3_s=19.9e-6/60,flow_ratio_c_r=1.,volume_m3=20e-6,dilution_ratio=0.))
        h.invoke('lid.set',opened=True);h.invoke('block.set',opened=True)
        for i in [1,2]:
            h.invoke('syringe.unload',channel=i);h.invoke('syringe.load',**dict(SYRINGE_EXAMPLES[i],capacity_m3=10e-6,volume_m3=10e-6,inner_diameter_m=.0145))
        h.invoke('block.set',opened=False);h.invoke('lid.set',opened=False);h.invoke('acquisition.start');h.step(100)
        self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.dispensed,20e-6,delta=1e-14);self.conserve(h)
if __name__=='__main__':unittest.main()


