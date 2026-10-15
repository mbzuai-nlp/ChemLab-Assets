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
class GlattReferenceLibraryTests(unittest.TestCase):
    """Real-data reference records (CC BY open-access sources) and the runtime paths that use them."""
    LIB=json.loads((ROOT/TEMPLATE_REL).read_text())
    REFS={r['reference_id']:r for r in LIB['reference_records']}
    SAMPLE_CORES=['core_itcm_z1','core_itcm_e15','core_itcm_rc_e14','core_ml_ibuprofen_70','core_carbobead_cp','core_polypropylene_pellets','core_soy_protein_isolate','core_mg_mcc_vivapur102','core_chitin_raw','core_glass_beads_88_149']
    def atomic(self,h,k,**a):
        before=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke(k,**a)
        self.assertEqual(before,h.debug_snapshot())
    def test_library_counts_and_provenance(self):
        cores={r['material_key'] for r in self.REFS.values() if r['kind']=='core_material' and reference_runtime_use(r) in ('fluidization_and_load','fluidization_only')}
        self.assertGreaterEqual(len(cores),50);self.assertEqual(self.LIB['coverage']['core_distinct_materials_fluidization_usable'],len(cores))
        self.assertGreaterEqual(len({r['pair_key'] for r in self.REFS.values() if r['kind']=='granulation_outcome'}),30)
        self.assertEqual(sum(r['kind']=='granulation_outcome' and r['miniglatt'] for r in self.REFS.values()),7)
        for r in self.REFS.values():
            self.assertIn(r['evidence'],('measured','measured_literature','derived','assumption'));self.assertTrue(r['source']['doi'] and r['source']['locator'] and r['accessed'])
            self.assertEqual(r['source']['license'],'CC BY 4.0');self.assertEqual(r['runtime_use'],reference_runtime_use(r))
            for name,pv in r.get('property_provenance',{}).items():
                if pv.get('evidence')=='derived':self.assertIn('formula',pv)
    def test_material_query_pure_across_kinds(self):
        h=prepared();before=h.debug_snapshot()
        for rid in self.SAMPLE_CORES+['binder_pva_4_88','binder_sucrose_10pct','outcome_mg_yeast_mcc','outcome_fb_cmc_sorbitol']:
            x=h.invoke('reference.material',reference_id=rid);r=self.REFS[rid]
            self.assertEqual(x['kind'],r['kind']);self.assertEqual(json.loads(x['properties']),r['properties']);self.assertEqual(x['license'],'CC BY 4.0');self.assertIn(r['source']['doi'],x['source'])
        self.assertEqual(before,h.debug_snapshot());self.atomic(h,'reference.material',reference_id='core_unknown')
    def test_fluidization_matches_bed_hydraulics(self):
        for rid in self.SAMPLE_CORES:
            h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE)
            out=h.invoke('powder.load_reference',**dict(LOADREF_EXAMPLE,reference_id=rid))
            f=h.invoke('reference.fluidization',reference_id=rid,temperature_k=PLANT_EXAMPLE['ambient_k'],pressure_pa=PLANT_EXAMPLE['pressure_pa'],sphericity_if_unmeasured=LOADREF_EXAMPLE['sphericity_if_unmeasured'],voidage_if_unmeasured=LOADREF_EXAMPLE['voidage_if_unmeasured'])
            self.assertEqual((f['voidage'],f['sphericity']),(out['voidage'],out['sphericity']))
            self.assertAlmostEqual(h._hydraulics()['umf']/f['umf_ergun_m_s'],1.,places=9,msg=rid)
            self.assertEqual(h.powder['particle_density_kg_m3'],record_density(self.REFS[rid]));self.assertEqual(h.powder['seed_diameter_m'],self.REFS[rid]['properties']['representative_diameter_m'])
    def test_measured_umf_comparison(self):
        h=GlattMiniglattRuntime();q=dict(temperature_k=293.15,pressure_pa=101325.,sphericity_if_unmeasured=1.,voidage_if_unmeasured=.45)
        g=h.invoke('reference.fluidization',reference_id='core_glass_beads_geldart_b',**q)
        self.assertAlmostEqual(g['umf_measured_m_s'],.1105);self.assertAlmostEqual(g['wen_yu_to_measured'],1.,delta=.05);self.assertEqual(g['geldart_group_approx'],'B')
        a=h.invoke('reference.fluidization',reference_id='core_alginate_aerogel_beads',**q)
        self.assertEqual(a['voidage_source'],'voidage_mf_record');self.assertAlmostEqual(a['umf_ergun_m_s']/.19,1.,delta=.1)
        for rid,r in self.REFS.items():
            if r['kind']=='core_material' and r['properties'].get('umf_measured_m_s') and reference_runtime_use(r)!='reference_only':
                x=h.invoke('reference.fluidization',reference_id=rid,**q);self.assertTrue(.2<x['wen_yu_to_measured']<5,rid)
        self.atomic(h,'reference.fluidization',reference_id='core_tio2_powder',**q);self.atomic(h,'reference.fluidization',reference_id='core_ml_lactochem_regular',**q);self.atomic(h,'reference.fluidization',reference_id='outcome_mg_yeast_mcc',**q)
    def test_reference_powders_run_with_conservation(self):
        for rid in self.SAMPLE_CORES:
            h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('kit.configure',micro=False,micro_maximum_kg=.025,accept_host_attachment=True)
            h.invoke('powder.load_reference',**dict(LOADREF_EXAMPLE,reference_id=rid));h.invoke('liquid.load',**LIQUID_EXAMPLE)
            f=h.invoke('reference.fluidization',reference_id=rid,temperature_k=333.15,pressure_pa=PLANT_EXAMPLE['pressure_pa'],sphericity_if_unmeasured=LOADREF_EXAMPLE['sphericity_if_unmeasured'],voidage_if_unmeasured=LOADREF_EXAMPLE['voidage_if_unmeasured'])
            air=min(90/3600,max(3/3600,2*f['umf_ergun_m_s']*PLANT_EXAMPLE['bed_area_m2']*273.15/333.15))   # normal-state flow for ~2 Umf at a 60 C inlet, inside the 100 Nm3/h budget
            h.invoke('method.configure',**dict(METHOD_EXAMPLE,air_normal_m3_s=air));h.invoke('tower.set',opened=False);h.invoke('power.set',enabled=True)
            if f['umf_ergun_m_s']*PLANT_EXAMPLE['bed_area_m2']*273.15/333.15>90/3600:self.atomic(h,'acquisition.start');continue   # e.g. too coarse/light for the Mini bed: rejected without side effects
            h.invoke('acquisition.start');h.step(10);self.assertTrue(h.read()['bed.fluidized'] or h.phase!='running');self.assertEqual(h.powder['source_tag'],rid);GlattTests.conserve(self,h)
    def test_reference_load_guards_atomic(self):
        h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE)
        for rid in ['core_alumina_fine','core_alginate_aerogel_beads','core_tio2_powder','core_ml_lactochem_regular','core_guar_gum_fine','binder_pva_4_88','outcome_fb_gg_water','missing']:self.atomic(h,'powder.load_reference',**dict(LOADREF_EXAMPLE,reference_id=rid))
        self.atomic(h,'powder.load_reference',**dict(LOADREF_EXAMPLE,accept_host_material=False))
    def test_voidage_and_sphericity_provenance(self):
        h=GlattMiniglattRuntime();q=dict(temperature_k=293.15,pressure_pa=101325.,sphericity_if_unmeasured=.7,voidage_if_unmeasured=.5)
        self.assertEqual(h.invoke('reference.fluidization',reference_id='core_glass_beads_88_149',**q)['voidage'],.38)
        s=h.invoke('reference.fluidization',reference_id='core_soy_protein_isolate',**q);self.assertEqual(s['voidage_source'],'voidage_bulk_record');self.assertEqual(s['sphericity_source'],'host_assumption')
        m=h.invoke('reference.fluidization',reference_id='core_ml_ibuprofen_70',**q);self.assertEqual((m['voidage_source'],m['sphericity']),('host_assumption',.83))
        c=h.invoke('reference.fluidization',reference_id='core_chitin_raw',**q);self.assertEqual(c['voidage_source'],'voidage_bulk_record');self.assertAlmostEqual(c['voidage'],1-190/1350)
        t=h.invoke('reference.fluidization',reference_id='core_alginate_aerogel_beads',**q);self.assertEqual((t['voidage'],t['sphericity_source']),(.415,'host_assumption'))
    def test_liquid_load_reference(self):
        for rid,src,frac in [('binder_urea_starch_b1','record',.5),('binder_pva_4_88','host_assumption',.08),('binder_sorbitol_10pct','host_assumption',.1)]:
            h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('powder.load_reference',**LOADREF_EXAMPLE)
            x=h.invoke('liquid.load_reference',**dict(LIQREF_EXAMPLE,reference_id=rid));self.assertEqual((x['density_source'],x['binder_mass_fraction']),(src,frac))
            self.assertAlmostEqual(h.mass['tank'][1],LIQREF_EXAMPLE['volume_m3']*x['density_kg_m3']*frac)
            h.invoke('method.configure',**dict(METHOD_EXAMPLE,operation='coating'));h.invoke('tower.set',opened=False);h.invoke('power.set',enabled=True);h.invoke('acquisition.start');h.step(20);GlattTests.conserve(self,h)
        self.assertAlmostEqual(x['viscosity_pa_s'],self.REFS['binder_sorbitol_10pct']['properties']['viscosity_pa_s'])
        h=GlattMiniglattRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('powder.load',**POWDER_EXAMPLE)
        self.atomic(h,'liquid.load_reference',**dict(LIQREF_EXAMPLE,reference_id='outcome_mg_dapsone_f1'));self.atomic(h,'liquid.load_reference',**dict(LIQREF_EXAMPLE,accept_water_only=False))
    def test_outcome_records(self):
        y=self.REFS['outcome_mg_yeast_mcc']['properties'];self.assertTrue(self.REFS['outcome_mg_yeast_mcc']['miniglatt']);self.assertAlmostEqual(y['d50_growth_ratio'],284/109)
        self.assertFalse(self.REFS['outcome_fb_cmc_sorbitol']['miniglatt']);self.assertIn('not MiniGlatt',self.REFS['outcome_fb_cmc_sorbitol']['instrument'])
        d=self.REFS['outcome_mg_dapsone_f10'];self.assertEqual(d['conditions']['spray'],'bottom (Micro-kit)');self.assertAlmostEqual(d['properties']['product_d50_m'],449.7e-6)
        for r in self.REFS.values():
            if r['kind']=='granulation_outcome':self.assertGreater(r['properties']['product_d50_m'],r['properties']['core_d50_m'])
if __name__=='__main__':unittest.main()
