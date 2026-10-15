"""CHEM-057 protocol, finite species, energy, thermal, motion, fault and template-library regression tests."""
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

LIB=json.loads((ROOT/r.TEMPLATE_REL).read_text())
def loaded(rid,hot_k=None,**extra):
    h=r.ThermoProcess11Runtime();h.invoke('plant.configure',**dict(r.PLANT_EXAMPLE,heater_max_w=500.,controller_w_k=100.));h.invoke('method.configure',**dict(r.METHOD_EXAMPLE,feed_kg_s=.2/3600,die_radius_m=.0015))
    h.invoke('material.load_reference',reference_id=rid,mass_kg=.006,accept_uncalibrated_newtonian=True,**extra)
    h.invoke('coolant.set_connected',connected=True);h.invoke('power.set',enabled=True)
    if hot_k:
        for i in range(7):h.invoke('thermal.set_zone',zone=i,temperature_k=hot_k)
        h.invoke('thermal.set_enabled',enabled=True);h.step(60)
    return h
HOST={'density_kg_m3':1100.,'heat_capacity_j_kg_k':1800.}
def host_args(rid):
    rec=LIB['templates'][rid];a={}
    if not rec.get('density'):a['density_kg_m3']=HOST['density_kg_m3']
    if not rec.get('heat_capacity'):a['heat_capacity_j_kg_k']=HOST['heat_capacity_j_kg_k']
    return a
class LibraryTests(Process11Tests.__mro__[1]):
    assert_balanced=Process11Tests.assert_balanced;rejected=Process11Tests.rejected
    def test_library_counts_provenance(self):
        t=LIB['templates'];s=LIB['summary']
        self.assertEqual(s['distinct_records'],len(t));self.assertGreaterEqual(len(t),55)
        self.assertEqual(s['runtime_usable_records'],sum(x['runtime_usage']!='reference_only' for x in t.values()))
        self.assertGreaterEqual(s['process_model_records'],13);self.assertFalse(s['target_50_met'])
        for rid,x in t.items():
            for k in ['evidence','source','accessed','locator','raw_source','sha256','licence_flag']:self.assertTrue(x.get(k),(rid,k))
            self.assertIn(x['licence_flag'],['permissive','unknown_licence_facts_only','restricted_nc_nd'])
            self.assertFalse(x.get('digitized',False))
    def test_every_process_model_record_runs(self):
        for rid,x in LIB['templates'].items():
            if x['runtime_usage']!='process_model':continue
            lo=x['minimum_processing']['temperature_k'];td=x.get('degradation_onset_k')
            if td and td-lo<5:  # published window overlaps TGA onset (Eudragit RL PO): covered by test_degradation_onset_trip
                self.assertTrue(loaded(rid,**host_args(rid)).material['degradation_onset_k']);continue
            T=lo+3 if x.get('extrudable_window_k') else x['viscosity_law']['reference_temperature_k']+10
            h=loaded(rid,T,**host_args(rid));h.invoke('drive.set_enabled',enabled=True);h.invoke('acquisition.start');h.step(10)
            self.assertEqual(h.fault,'',rid);self.assertGreater(h.torque,0,rid);self.assertGreater(sum(h.product)+sum(map(sum,h.zones)),0,rid)
            self.assertIn(rid,h.invoke('extrusion.measure')['evidence']);self.assert_balanced(h)
    def test_window_laws_reproduce_published_bounds(self):
        n=0
        for rid,x in LIB['templates'].items():
            if x['runtime_usage']!='process_model' or not x.get('extrudable_window_k'):continue
            h=loaded(rid,**host_args(rid));lo,hi=x['extrudable_window_k'];n+=1
            self.assertAlmostEqual(h._viscosity(lo),1e4,delta=1e-6);self.assertAlmostEqual(h._viscosity(hi),1e3,delta=1e-6)
        self.assertGreaterEqual(n,11)
    def test_wlf_and_measured_arrhenius_records(self):
        h=loaded('fam20_eudragit_rl_po');self.assertAlmostEqual(h._viscosity(403.15),50250.);self.assertLess(h._viscosity(413.15),50250.)
        self.assertAlmostEqual(h.material['heat_capacity_j_kg_k'],2095.35+1.55*130);self.assertAlmostEqual(h.material['density_kg_m3'],1000/(35221/44180+1.2637*130/2651.9))
        p=loaded('pa1010_vestamid_ds16',heat_capacity_j_kg_k=2000.);self.assertAlmostEqual(p._viscosity(503.15),1857.);self.assertEqual(p.material['density_kg_m3'],1050.)
        self.assertIn('heat_capacity:host_assumption',p.material['parameter_evidence']);self.assertIn('density:literature',p.material['parameter_evidence'])
    def test_material_classes_change_model_output(self):
        out={}
        for rid in ['kollidon_va64','soluplus','klucel_lf','eudragit_rs_po']:
            x=LIB['templates'][rid];T=x['extrudable_window_k'][0]+3
            h=loaded(rid,T,**host_args(rid));h.invoke('drive.set_enabled',enabled=True);h.invoke('acquisition.start');h.step(15);out[rid]=h.torque;self.assert_balanced(h)
        self.assertEqual(len(set(round(v,9) for v in out.values())),4)
    def test_literature_minimum_processing_interlock(self):
        x=LIB['templates']['soluplus'];h=loaded('soluplus',x['minimum_processing']['temperature_k']-5,**host_args('soluplus'));h.invoke('drive.set_enabled',enabled=True)
        self.rejected(h,'acquisition.start',{})
    def test_degradation_onset_trip(self):
        x=LIB['templates']['eudragit_rl_po'];td=x['degradation_onset_k'];self.assertLess(td,x['extrudable_window_k'][1])
        h=loaded('eudragit_rl_po',td+3,**host_args('eudragit_rl_po'));h.invoke('drive.set_enabled',enabled=True);h.invoke('acquisition.start');h.step(5)
        self.assertEqual(h.fault,'DEGRADATION_ONSET');self.assertTrue(h.latched);self.assert_balanced(h)
    def test_load_reference_guards(self):
        h=r.ThermoProcess11Runtime();h.invoke('plant.configure',**r.PLANT_EXAMPLE)
        base=dict(mass_kg=.006,accept_uncalibrated_newtonian=True)
        self.rejected(h,'material.load_reference',dict(base,reference_id='kollidon_va64',density_kg_m3=1000.))
        self.rejected(h,'material.load_reference',dict(base,reference_id='soluplus'))
        self.rejected(h,'material.load_reference',dict(base,reference_id='kollidon_30'))
        self.rejected(h,'material.load_reference',dict(base,reference_id='ind30_kollidon_va64'))
        self.rejected(h,'material.load_reference',dict(base,reference_id='nope'))
        self.rejected(h,'material.load_reference',dict(base,reference_id='kollidon_va64',accept_uncalibrated_newtonian=False))
        h.invoke('material.load_reference',reference_id='kollidon_va64',**base);self.rejected(h,'material.load_reference',dict(base,reference_id='kollidon_va64'))
        self.rejected(h,'material.configure',r.MATERIAL_EXAMPLE)
    def test_reference_lookup_new_records_exact_only(self):
        h=r.ThermoProcess11Runtime();L=lambda **a:h.invoke('reference.lookup',**a)
        v=L(reference_id='ind30_kollidon_va64',temperature_k=433.15);self.assertEqual(v['torque_percent_range'],[40,40]);self.assertEqual(v['pressure_range_pa'],[0.,1e5]);self.assertIsNone(v['torque_total_range_nm'])
        v=L(reference_id='soluplus',temperature_k=413.15);self.assertEqual(v['viscosity_pa_s'],1e4);self.assertAlmostEqual(v['torque_total_range_nm'][0],9.6)
        self.rejected(h,'reference.lookup',dict(reference_id='pzb_kollidon_va64',temperature_k=453.15))
        v=L(reference_id='pzb_kollidon_va64',temperature_k=453.15,screw_speed_rad_s=300*r.TAU/60);self.assertEqual(v['torque_reported_range_nm'],[5.5,5.5])
        self.rejected(h,'reference.lookup',dict(reference_id='alginate_glycerol',temperature_k=383.15))
        self.assertEqual(L(reference_id='alginate_glycerol',temperature_k=383.15,variant='SA-45G')['torque_percent_range'],[24,26])
        v=L(reference_id='fam20_eudragit_rl_po',temperature_k=393.15,screw_speed_rad_s=100*r.TAU/60,variant='PN 1 0.1 kg/h');self.assertAlmostEqual(v['sme_j_kg'],.41*3.6e6);self.assertEqual(v['licence_flag'],'restricted_nc_nd')
        self.assertAlmostEqual(L(reference_id='cop_naproxen',temperature_k=423.15,variant='30% naproxen')['viscosity_pa_s'],259.)
        for rid,T in [('cop_praziquantel',423.15),('pzb_hpmc',443.15),('ind30_soluplus',433.0),('kollidon_30',433.15)]:self.rejected(h,'reference.lookup',dict(reference_id=rid,temperature_k=T))
        self.assertEqual(L(reference_id='pcm30_aff70',temperature_k=438.15)['torque_total_range_nm'],[1.68,2.04])
    def test_material_lookup_every_record(self):
        h=r.ThermoProcess11Runtime();before=h.debug_snapshot();classes=set()
        for rid,x in LIB['templates'].items():
            v=h.invoke('material.lookup',reference_id=rid);self.assertEqual(set(v),set(r.MATERIAL_INFO['properties']));self.assertEqual(v['runtime_usage'],x['runtime_usage']);classes.add(v['material_class'])
        self.assertGreaterEqual(len(classes),10);self.assertEqual(before,h.debug_snapshot())
        self.assertFalse(h.invoke('material.lookup',reference_id='eudragit_l100')['neat_extrudable'])
    def test_agent_visibility_of_library(self):
        h=r.ThermoProcess11Runtime();d=h.session().describe(instance_id=h.instance_id)['capabilities']
        self.assertIn('material.lookup',d);self.assertNotIn('material.load_reference',d)
        self.assertEqual(d and h.session().invoke(instance_id=h.instance_id,capability_id='material.load_reference',arguments=r.LOAD_REF_EXAMPLE,request_id='x')['error']['code'],'ACCESS_DENIED')
if __name__=='__main__':unittest.main()
