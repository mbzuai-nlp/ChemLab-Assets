"""CHEM-059 actual physics/protocol guards; no hardware validation."""
import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime.linari_rtadvanced import *
def ready(all_pumps=False,method=None,plant=None):
    h=LinariRtadvancedRuntime();h.invoke('plant.configure',**(plant or PLANT_EXAMPLE));h.invoke('collector.configure',**COLLECTOR_EXAMPLE)
    for i in range(8 if all_pumps else 1):h.invoke('solution.load',**dict(SOLUTION_EXAMPLE,pump=i))
    m=copy.deepcopy(method or METHOD_EXAMPLE)
    if all_pumps:m['flows_m3_s']=[m['flows_m3_s'][0]]*8
    h.invoke('method.configure',**m);h.invoke('power.set',enabled=True);h.invoke('vent.set',enabled=True);h.invoke('door.set',opened=False);h.invoke('hv.set',enabled=True);h.step(5)
    return h
class TestRTAdvanced(unittest.TestCase):
    def rejected(self,h,k,**a):
        before=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke(k,**a)
        self.assertEqual(before,h.debug_snapshot())
    def conserved(self,h):
        p=h.invoke('product.read')
        for s in ['solvent','polymer']:self.assertAlmostEqual(p['initial_'+s+'_kg'],sum(p[l+'_'+s+'_kg'] for l in ['syringe','cone','mat','waste','evaporated','collected']),places=14)
    def test_zero_stroke_needle(self):
        h=ready(method=dict(METHOD_EXAMPLE,needle_low_m=[.03,.03],needle_high_m=[.03,.03]));h.invoke('acquisition.start');h.step(4);self.assertEqual(h.needle,[.03,.03]);self.conserved(h)
    def test_button_power_toggle(self):
        h=ready();h.invoke('power.toggle');self.assertFalse(h.powered);h.invoke('power.toggle');self.assertTrue(h.powered)
    def test_axial_deposition_matches_tip_datum(self):
        h=ready(method=dict(METHOD_EXAMPLE,needle_low_m=[.03,.03],needle_high_m=[.03,.03]));h.invoke('acquisition.start');h.step(4)
        rows=h._product()['axial_polymer_kg'];expected=int((.03+.035)/.5*32);self.assertGreater(rows[expected],0);self.assertEqual(sum(rows[:expected]+rows[expected+1:]),0)
    def test_outside_collector_goes_to_waste(self):
        h=ready(plant=dict(PLANT_EXAMPLE,needle_speed_m_s=.1),method=dict(METHOD_EXAMPLE,needle_low_m=[.49,.49],needle_high_m=[.49,.49],duration_s=6.));h.invoke('acquisition.start');h.step(6)
        self.assertEqual(h._product()['mat_polymer_kg'],0);self.assertGreater(h._product()['waste_polymer_kg'],0);self.conserved(h)
    def test_ambiguous_morphology_not_runtime_anchor(self):
        h=ready();rows=[r for r in h.templates.values() if r['fiber_morphology_ambiguous']];self.assertGreater(len(rows),0);self.assertTrue(all(not r['usable_as_runtime_anchor'] for r in rows))
    def test_dimensions_and_ratings(self):
        g=json.loads((ROOT/'assets/expansion_batch04/t5_automation/linari_rtadvanced/geometry_manifest.json').read_text());self.assertEqual(g['overall_dimensions_m'],[1.05,1.035,2.2])
    def test_real_reference_lineage(self):
        h=LinariRtadvancedRuntime();self.assertGreater(len(h.templates),1800)
        for r in h.templates.values():self.assertGreater(r['value'],0);self.assertTrue(r['source']);self.assertGreater(r['row'],1);self.assertEqual(h.invoke('reference.lookup',substance_id=r['substance_id'])['mean_diameter_m'],r['value'])
    def test_reference_is_not_rt_measurement(self):
        h=ready();r=h.invoke('reference.lookup',substance_id='prelim_row_1261');self.assertEqual(r['evidence'],'measured_literature');self.assertIn('NOT_RT',h.invoke('spinning.measure')['evidence'])
    def test_unknown_reference_atomic(self):self.rejected(ready(),'reference.lookup',substance_id='fake')
    def test_eight_pumps_mass(self):
        h=ready(True);h.invoke('acquisition.start');h.step(10);self.conserved(h);self.assertGreater(h.invoke('product.read')['mat_polymer_kg'],0)
        self.assertTrue(all(h.read()['plunger.pump_'+str(i)+'_m']>0 for i in range(8)))
    def test_finite_exhaustion(self):
        h=ready(method=dict(METHOD_EXAMPLE,flows_m3_s=[1e-7]+[0.]*7,duration_s=100.));h.invoke('acquisition.start');h.step(30);self.assertEqual(h.phase,'failed');self.assertEqual(h.fault,'SYRINGE_EMPTY');self.conserved(h)
    def test_low_field_drips(self):
        h=ready(method=dict(METHOD_EXAMPLE,voltage_v=[100.,100.]));h.invoke('acquisition.start');h.step(10);p=h.invoke('product.read');self.assertEqual(p['mat_polymer_kg'],0);self.assertGreater(p['waste_polymer_kg'],0);self.conserved(h)
    def test_cancel_retains_cone(self):
        h=ready();h.invoke('acquisition.start');h.step(4);before=sum(h.cones[0]);h.invoke('acquisition.stop');self.assertEqual(sum(h.cones[0]),before);self.assertGreater(before,0);h.step(2);self.assertLess(sum(h.cones[0]),before);self.conserved(h)
    def test_time_completion(self):
        h=ready();h.invoke('acquisition.start');h.step(20);self.assertEqual(h.phase,'succeeded');self.assertAlmostEqual(h.elapsed_s,10.)
    def test_plunger_volume(self):
        h=ready();h.invoke('acquisition.start');h.step(6);consumed=h.loaded_volumes[0]-h._volume(h.syringes[0],h.solutions[0]);self.assertAlmostEqual(h.read()['plunger.pump_0_m']*math.pi*.022**2/4,consumed,places=16)
    def test_two_independent_hv(self):
        h=ready(method=dict(METHOD_EXAMPLE,voltage_v=[10000.,20000.]));self.assertAlmostEqual(h.volts[1],2*h.volts[0]);self.assertGreater(h.volts[0],0)
    def test_rc_energy_conservation(self):
        h=ready();h.step(3);h.invoke('hv.set',enabled=False);h.step(20);p=h.invoke('product.read');self.assertAlmostEqual(p['hv_source_j'],p['hv_dissipated_j']+p['hv_stored_j'],places=12)
    def test_residual_hv_blocks_door(self):
        h=ready();h.invoke('hv.set',enabled=False);self.rejected(h,'door.set',opened=True);h.step(25);h.invoke('door.set',opened=True);self.assertTrue(h.door)
    def test_door_blocks_hv(self):
        h=ready();h.invoke('hv.set',enabled=False);h.step(25);h.invoke('door.set',opened=True);self.rejected(h,'hv.set',enabled=True)
    def test_vent_loss_fails_and_bleeds(self):
        h=ready();h.invoke('acquisition.start');h.step(1);v=h.volts[0];h.invoke('vent.set',enabled=False);self.assertEqual(h.phase,'failed');h.step(1);self.assertLess(h.volts[0],v);self.assertFalse(h.hv)
    def test_emergency_latched(self):
        h=ready();h.invoke('acquisition.start');h.invoke('safety.trip');self.assertTrue(h.latched);self.assertEqual(h.phase,'failed');self.rejected(h,'safety.reset');h.step(25);h.invoke('safety.reset');self.assertFalse(h.latched)
    def test_poweroff_residuals(self):
        h=ready();h.invoke('acquisition.start');h.step(4);v=h.volts[0];h.invoke('power.set',enabled=False);self.assertEqual(h.phase,'cancelled');self.assertGreater(h.volts[0],0);h.step(1);self.assertLess(h.volts[0],v);self.conserved(h)
    def test_climate_noninstant_and_balance(self):
        h=ready();h.invoke('climate.set_targets',temperature_k=313.15,humidity=.65);h.invoke('climate.set',enabled=True);self.assertEqual(h.temperature,298.15);h.step(10);self.assertGreater(h.temperature,298.15);self.assertLess(h.temperature,313.15);self.assertGreater(h.rh,.4);self.assertLess(h.rh,.65)
        p=h.invoke('product.read');self.assertAlmostEqual(p['heater_input_j']-p['thermal_loss_j'],p['sensible_j'],places=7)
    def test_cooling_after_poweroff(self):
        h=ready();h.invoke('climate.set_targets',temperature_k=313.15,humidity=.65);h.invoke('climate.set',enabled=True);h.step(10);before=h.temperature;h.invoke('power.set',enabled=False);h.step(10);self.assertLess(h.temperature,before)
    def test_task_rh_temperature_bounds(self):
        h=ready()
        for t,r in [(324.,.5),(293.15,.5),(303.15,.19),(303.15,.71)]:self.rejected(h,'climate.set_targets',temperature_k=t,humidity=r)
    def test_rotation_negative_and_angle(self):
        h=ready(method=dict(METHOD_EXAMPLE,rpm=-5000.));h.invoke('acquisition.start');h.step(1);self.assertLess(h.rpm,0);self.assertGreaterEqual(h.angle,0);self.assertLess(h.angle,2*math.pi)
    def test_needle_reversal_bounds(self):
        h=ready(method=dict(METHOD_EXAMPLE,needle_low_m=[.02,.02],needle_high_m=[.04,.04],duration_s=10));h.invoke('acquisition.start')
        for _ in range(15):h.step(.2);self.assertTrue(all(0<=z<=.04 for z in h.needle))
        self.assertTrue(any(d<0 for d in h.direction))
    def test_collector_range_and_capacity(self):
        h=LinariRtadvancedRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);self.rejected(h,'solution.load',**dict(SOLUTION_EXAMPLE,solvent_kg=.06));h.invoke('collector.configure',**dict(COLLECTOR_EXAMPLE,length_m=.1));self.rejected(h,'method.configure',**METHOD_EXAMPLE)
    def test_invalid_scalars_and_extra_fields(self):
        h=ready()
        for d in [-1,float('nan'),float('inf'),True]:
            before=h.debug_snapshot()
            with self.assertRaises(ActionRejected):h.step(d)
            self.assertEqual(before,h.debug_snapshot())
        self.rejected(h,'power.set',enabled=1);self.rejected(h,'power.set',enabled=True,extra=1)
    def test_double_loading_atomic(self):
        h=ready();self.rejected(h,'solution.load',**SOLUTION_EXAMPLE)
    def test_clean_drain_collect_mass(self):
        h=ready();h.invoke('acquisition.start');h.step(10);h.invoke('hv.set',enabled=False);h.step(25);h.invoke('needle.clean',group=0);h.invoke('door.set',opened=True);h.invoke('product.collect',recipient_id='mat');h.invoke('solution.drain',pump=0);self.conserved(h);self.assertFalse(h.collector_present)
    def test_step_subdivision(self):
        a=ready();b=ready()
        for h in [a,b]:h.invoke('acquisition.start')
        a.step(8)
        for _ in range(400):b.step(.02)
        for k in ['mat_polymer_kg','evaporated_solvent_kg','syringe_polymer_kg']:self.assertAlmostEqual(a._product()[k],b._product()[k],places=14)
    def test_material_50_systems_not_rows(self):
        h=ready();labels={r['canonical_material_system'] for r in h.templates.values()};self.assertGreaterEqual(len(labels),50);self.assertGreater(len(h.templates),len(labels))
    def test_agent_truth_and_role_boundary(self):
        h=ready();s=h.session();self.assertNotIn('truth.ledger',s.describe(instance_id=h.instance_id)['state_slots'])
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
        r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='x');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
        self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities'])
    def test_request_idempotency_and_conflict(self):
        h=ready();s=h.session();args=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a');a=s.invoke(**args);rev=h.revision;self.assertEqual(s.invoke(**args),a);self.assertEqual(h.revision,rev)
        r=s.invoke(**dict(args,capability_id='acquisition.stop'));self.assertEqual(r['error']['code'],'REQUEST_CONFLICT')
    def test_operation_ownership_and_cancel(self):
        h=ready();s=h.session();h.register_principal('other');t=h.session('other');r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='a');op=r['operation_id']
        with self.assertRaises(ActionRejected):t.poll(op)
        h.step(3);self.assertEqual(s.cancel(op,'cancel')['status'],'succeeded');self.assertEqual(s.poll(op)['status'],'cancelled')
    def test_read_does_not_advance_or_alias(self):
        h=ready();before=h.debug_snapshot();p=h.read('evaluator');p['truth.ledger']['axial_polymer_kg'][0]=999;self.assertEqual(h.debug_snapshot(),before);h.invoke('spinning.measure');self.assertEqual(h.debug_snapshot(),before)
    def test_write_routes_and_permissions(self):
        h=ready();s=h.session();r=s.write(instance_id=h.instance_id,state_id='hv.enabled',value=False,request_id='w');self.assertEqual(r['status'],'succeeded')
        with self.assertRaises(ActionRejected):s.write(instance_id=h.instance_id,state_id='vent.enabled',value=True,request_id='forbidden')
    def test_session_errors_and_unknown_instance(self):
        h=ready();s=h.session();r=s.invoke(instance_id='unknown',capability_id='power.set',arguments={'enabled':True},request_id='x');self.assertEqual(r['error']['code'],'UNKNOWN_INSTANCE')
        r=s.invoke(instance_id=h.instance_id,capability_id='fake',arguments={},request_id='x');self.assertEqual(r['error']['code'],'UNKNOWN_CAPABILITY')
    def test_collector_reconfigure_and_no_free_mass(self):
        h=ready();h.invoke('acquisition.start');h.step(10);h.invoke('hv.set',enabled=False);h.step(25);h.invoke('needle.clean',group=0);h.invoke('door.set',opened=True);h.invoke('product.collect',recipient_id='mat');h.invoke('collector.configure',**COLLECTOR_EXAMPLE);self.conserved(h);self.assertEqual(sum(h._product()['axial_polymer_kg']),0)
if __name__=='__main__':unittest.main()
