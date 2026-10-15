"""CHEM-054 actual process, material, protocol and geometry-limit tests."""
import copy,math,unittest
from scripts.asset_runtime.ossila_dipcoater import *
def configured(name='test',evap=0.,cycles=1):
    h=OssilaDipcoaterRuntime(name);h.invoke('plant.configure',**dict(PLANT_EXAMPLE,mass_transfer_m_s=evap))
    h.invoke('power.set',enabled=True);h.invoke('stage.home');h.step(.02)
    h.invoke('bath.configure',**BATH_EXAMPLE);h.invoke('method.configure',**dict(METHOD_EXAMPLE,cycles=cycles))
    h.invoke('clamp.set_gap',gap_m=.012);h.step(.7);h.invoke('substrate.configure',**SAMPLE_EXAMPLE);h.invoke('clamp.set_gap',gap_m=0.);h.step(.7);return h
def balance(h):
    p=h._product()
    return [p['initial_'+sp+'_kg']-sum(p[loc+'_'+sp+'_kg'] for loc in ['bath','film','solid','evaporated','collected']) for sp in ['solvent','solute']]
class DipTests(unittest.TestCase):
    def test_target_only(self):
        h=configured();q=h.q;h.invoke('stage.move_to',position_m=.16,speed_m_s=.005);self.assertEqual(q,h.q);h.step(.1);self.assertAlmostEqual(q-h.q,.0005)
    def test_home_needs_step(self):
        h=OssilaDipcoaterRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('stage.home');self.assertFalse(h.homed);h.step(.01);self.assertTrue(h.homed)
    def test_queries_pure(self):
        h=configured();before=h.debug_snapshot();h.read();h.describe();h.invoke('product.read');h.invoke('coating.measure');self.assertEqual(before,h.debug_snapshot())
    def test_rejection_atomic(self):
        h=configured();before=h.debug_snapshot()
        for arguments in [dict(METHOD_EXAMPLE,cycles=True),dict(METHOD_EXAMPLE,cycles=1001),dict(METHOD_EXAMPLE,withdrawal_initial_m_s=0.),dict(METHOD_EXAMPLE,bogus=1)]:
            with self.assertRaises(ActionRejected):h.invoke('method.configure',**arguments)
            self.assertEqual(before,h.debug_snapshot())
    def test_clamp_target_only(self):
        h=configured();h.invoke('product.collect',recipient_id='out');before=h.gap;self.assertEqual(before,0);self.assertEqual(h.gap,0);h.step(.1);self.assertGreater(h.gap,0)
    def test_cannot_open_attached(self):
        h=configured()
        with self.assertRaises(ActionRejected):h.invoke('clamp.set_gap',gap_m=.012)
    def test_bad_geometry(self):
        h=configured();h.invoke('method.configure',**dict(METHOD_EXAMPLE,coating_length_m=.15));before=h.debug_snapshot()
        with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
        self.assertEqual(before,h.debug_snapshot())
    def test_surface_displacement(self):
        h=configured();initial=h._surface();h.q=.04;higher=h._surface();self.assertGreater(higher,initial)
        submerged=max(0,min(h.sample['length_m'],higher-h._bottom()))
        b=h.bath_cfg;s=h.sample
        self.assertAlmostEqual(b['cross_section_m2']*(higher-b['bottom_z_m']),h._volume(h.bath)+s['width_m']*s['thickness_m']*submerged,places=14)
    def test_completed_program(self):
        h=configured();h.invoke('acquisition.start');h.step(60);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.cycle_index,1);self.assertGreater(h._product()['film_solute_kg'],0)
    def test_multicycle_preserves_species(self):
        h=configured(evap=.0001,cycles=3);h.invoke('acquisition.start');h.step(100)
        self.assertEqual(h.phase,'succeeded');self.assertEqual(h.cycle_index,3)
        for residual in balance(h):self.assertLess(abs(residual),1e-12)
        self.assertEqual(h.evaporated[1],0)
    def test_cancel_retains(self):
        h=configured();h.invoke('acquisition.start');h.step(12);self.assertGreater(h._product()['film_solute_kg'],0);p=h._product();q=h.q;h.invoke('acquisition.stop');self.assertEqual(h._product(),p);h.step(1);self.assertEqual(h.q,q);self.assertEqual(h.phase,'cancelled')
    def test_poweroff_needs_rehome(self):
        h=configured();h.invoke('acquisition.start');h.step(1);h.invoke('power.set',enabled=False);q=h.q;h.step(1);self.assertEqual(h.q,q);self.assertFalse(h.homed)
        h.invoke('power.set',enabled=True)
        with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
    def test_crash_immediate_and_reset(self):
        h=configured();h.invoke('acquisition.start');h.step(1);q=h.q;h.invoke('safety.set_crash',triggered=True);self.assertEqual(h.speed,0);h.step(.3);self.assertEqual(h.q,q);self.assertEqual(h.phase,'failed')
        with self.assertRaises(ActionRejected):h.invoke('fault.acknowledge')
        h.invoke('safety.set_crash',triggered=False);h.invoke('fault.acknowledge');self.assertFalse(h.fault)
    def test_bottom_collision(self):
        h=configured();h.invoke('stage.move_to',position_m=0.,speed_m_s=.02);h.bath_cfg['bottom_z_m']=.09;h.step(20);self.assertTrue(h.crash);self.assertGreaterEqual(h._bottom(),.091-1e-12)
    def test_finite_drying(self):
        h=configured(evap=.1);h.invoke('acquisition.start');h.step(40)
        for _ in range(5):h.step(1000)
        for residual in balance(h):self.assertLess(abs(residual),1e-10)
        self.assertTrue(all(v>=0 for v in [*h.bath,*h.evaporated,*h.collected]))
    def test_lld_reference_and_exponent(self):
        h=configured();u=.003;b=h.bath_cfg;rho=sum(h.bath)/h._volume(h.bath)
        expected=.94*math.sqrt(b['surface_tension_n_m']/(rho*9.80665))*(b['viscosity_pa_s']*u/b['surface_tension_n_m'])**(2/3)
        self.assertAlmostEqual(h._lld(u),expected,places=15);self.assertAlmostEqual(h._lld(2*u)/h._lld(u),2**(2/3))
    def test_lld_rejects_high_ca(self):
        h=configured();h.bath_cfg['viscosity_pa_s']=1.;before=h.debug_snapshot()
        with self.assertRaisesRegex(ActionRejected,'MODEL_OUT_OF_RANGE'):h.invoke('acquisition.start')
        self.assertEqual(before,h.debug_snapshot())
    def test_gradient_film(self):
        h=configured();h.invoke('acquisition.start');h.step(60);profile=h._product()['film_profile_m'];nonzero=[x for x in profile if x>0]
        self.assertGreater(len(nonzero),20);self.assertGreater(max(nonzero)/min(nonzero),1.1)
    def test_collect_exactly_once(self):
        h=configured();h.invoke('acquisition.start');h.step(60);h.invoke('product.collect',recipient_id='x');self.assertGreater(h.collected[1],0)
        with self.assertRaises(ActionRejected):h.invoke('product.collect',recipient_id='x')
        for residual in balance(h):self.assertLess(abs(residual),1e-12)
    def test_reference(self):
        h=configured();r=h.invoke('reference.lookup',substance_id='ethanol',temperature_k=298.15);self.assertAlmostEqual(r['vapor_pressure_pa'],7869.085,places=2)
        with self.assertRaises(ActionRejected):h.invoke('reference.lookup',substance_id='ethanol',temperature_k=5.)
    def test_two_instances(self):
        a=configured('a');b=configured('b');before=b.debug_snapshot();a.invoke('acquisition.start');a.step(10);self.assertEqual(before,b.debug_snapshot())
    def test_clock_partition(self):
        a=configured('a');b=configured('b');a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(2)
        for _ in range(20):b.step(.1)
        self.assertAlmostEqual(a.q,b.q,places=12);self.assertAlmostEqual(a.time_s,b.time_s,places=10)
    def test_invalid_clock(self):
        h=configured();before=h.debug_snapshot()
        for dt in [0,-1,float('nan'),True]:
            with self.assertRaises(ActionRejected):h.step(dt)
        self.assertEqual(before,h.debug_snapshot())
    def test_agent_truth_filter(self):
        h=configured();s=h.session();self.assertNotIn('truth.ledger',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities'])
        r=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='deny');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(ActionRejected):h.session('evaluator')
    def test_request_dedup(self):
        h=configured();s=h.session();a=dict(instance_id=h.instance_id,capability_id='power.toggle',arguments={},request_id='same');r=s.invoke(**a);powered=h.powered;self.assertEqual(s.invoke(**a),r);self.assertEqual(powered,h.powered)
        self.assertEqual(s.invoke(**dict(a,capability_id='power.set',arguments={'enabled':True}))['error']['code'],'REQUEST_CONFLICT')
    def test_async_owner_cancel(self):
        h=configured();s=h.session();h.register_principal('other');other=h.session('other');r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=r['operation_id'];self.assertEqual(r['status'],'accepted');h.step(12)
        with self.assertRaises(ActionRejected):other.poll(op)
        before=h._product();s.cancel(op,'cancel');self.assertEqual(s.poll(op)['status'],'cancelled');self.assertEqual(h._product(),before)
    def test_write_routes(self):
        h=configured();s=h.session();r=s.write(instance_id=h.instance_id,state_id='power.enabled',value=False,request_id='write');self.assertEqual(r['status'],'succeeded');self.assertFalse(h.powered)
        with self.assertRaises(ActionRejected):s.write(instance_id=h.instance_id,state_id='truth.ledger',value={},request_id='bad')
    def test_no_implicit_time(self):
        h=configured();t=h.time_s;h.invoke('acquisition.start');h.invoke('coating.measure');self.assertEqual(t,h.time_s)

# --- 298.15 K real solvent-property library (shared thin-film solvent set, asset_version 1.1.0) ---
LIT_298K = {  # CRC-Handbook-level literature values at 25 C used as independent sanity anchors (rho kg/m3, mu Pa s, sigma N/m)
    'water': (997.05, 0.890e-3, 71.99e-3), 'ethanol': (785.1, 1.074e-3, 21.97e-3), 'toluene': (862.3, 0.560e-3, 27.93e-3),
    'ethylene_glycol': (1110.0, 16.1e-3, 47.99e-3), 'dmso': (1095.4, 1.987e-3, 42.92e-3), 'heptane': (679.5, 0.387e-3, 19.66e-3),
    'butyl_acetate': (876.4, 0.685e-3, 24.88e-3), 'nmp': (1028.0, 1.666e-3, 40.79e-3), 'chloroform': (1479.0, 0.537e-3, 26.67e-3),
    'cyclohexanone': (942.0, 2.017e-3, 34.57e-3)}


class SolventLibraryTests(unittest.TestCase):
    def runtime(self):
        return OssilaDipcoaterRuntime()
    def test_at_least_50_distinct_usable_real_records(self):
        h=self.runtime();usable=[r for r in h.templates.values() if r.get('usable_at_298_15K')]
        self.assertGreaterEqual(len(usable),50);self.assertEqual(len({r['cas'] for r in usable}),len(usable))
        for r in usable:
            p=r['properties_298_15K']
            for k in ('density_kg_m3','viscosity_pa_s','surface_tension_n_m','vapor_pressure_pa'):
                self.assertIn(p[k]['evidence'],('measured_literature','derived'),(r['substance_id'],k));self.assertGreater(p[k]['value'],0)
            self.assertGreater(r['normal_boiling_point']['value_k'],298.15);self.assertLess(r['melting_point']['value_k'],298.15)
            self.assertTrue(r['inchikey']);self.assertTrue(r['raw_files'])
    def test_properties_across_classes_match_literature(self):
        h=self.runtime()
        for sid,(rho,mu,sig) in LIT_298K.items():
            p=h.invoke('reference.properties',substance_id=sid)
            self.assertAlmostEqual(p['density_kg_m3']/rho,1,delta=.006,msg=sid);self.assertAlmostEqual(p['viscosity_pa_s']/mu,1,delta=.10,msg=sid)
            self.assertAlmostEqual(p['surface_tension_n_m']/sig,1,delta=.04,msg=sid);self.assertEqual(p['temperature_k'],298.15)
            self.assertGreater(p['vapor_pressure_pa'],0);self.assertGreater(p['normal_boiling_point_k'],298.15)
        self.assertAlmostEqual(h.invoke('reference.properties',substance_id='water')['vapor_pressure_pa'],3169.9,delta=5)
        self.assertAlmostEqual(h.invoke('reference.lookup',substance_id='nonane',temperature_k=298.15)['vapor_pressure_pa'],580.6,delta=1)
    def test_incomplete_and_unknown_records_rejected(self):
        h=self.runtime();before=h.debug_snapshot()
        for sid,code in [('menthol','REFERENCE_INCOMPLETE'),('tert_butanol','REFERENCE_INCOMPLETE'),('acetaldehyde','REFERENCE_INCOMPLETE'),('no_such_solvent','UNKNOWN_REFERENCE')]:
            with self.assertRaisesRegex(ActionRejected,code):h.invoke('reference.properties',substance_id=sid)
        self.assertEqual(before,h.debug_snapshot())
    def test_derived_vapour_pressure_fits_never_extrapolate(self):
        h=self.runtime()
        r=h.invoke('reference.lookup',substance_id='cyclohexanone',temperature_k=298.15);self.assertIn('Clausius_Clapeyron',r['evidence']);self.assertGreater(r['vapor_pressure_pa'],100)
        with self.assertRaisesRegex(ActionRejected,'REFERENCE_OUT_OF_RANGE'):h.invoke('reference.lookup',substance_id='cyclohexanone',temperature_k=310.)
        self.assertIn('ThermoML',h.invoke('reference.lookup',substance_id='nmp',temperature_k=298.15)['evidence'])
        self.assertIn('SRD69',h.invoke('reference.lookup',substance_id='heptane',temperature_k=298.15)['evidence'])
        self.assertAlmostEqual(h.invoke('reference.lookup',substance_id='ethanol',temperature_k=298.15)['vapor_pressure_pa'],7869.085,places=2)
    def test_reference_properties_agent_visible_and_schema(self):
        import importlib.util
        s=importlib.util.spec_from_file_location('tool_lib',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py');t=importlib.util.module_from_spec(s);s.loader.exec_module(t)
        h=self.runtime();sess=h.session()
        out=sess.invoke(instance_id=h.instance_id,capability_id='reference.properties',arguments={'substance_id':'ethylene_glycol'},request_id='p1')
        self.assertEqual(out['status'],'succeeded');self.assertFalse(t.validate_value(out['result'],CAPS['reference.properties'][1]))
        self.assertIn('reference.properties',sess.describe(instance_id=h.instance_id)['capabilities'])

    def test_bath_from_reference_drives_lld_with_real_properties(self):
        h=OssilaDipcoaterRuntime('ref');h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('power.set',enabled=True);h.invoke('stage.home');h.step(.02)
        out=h.invoke('bath.configure_from_reference',**BATH_REF_EXAMPLE);p=h.invoke('reference.properties',substance_id='toluene')
        self.assertEqual(out['surface_tension_n_m'],p['surface_tension_n_m']);self.assertEqual(h.bath_cfg['viscosity_pa_s'],p['viscosity_pa_s']);self.assertEqual(h.bath_cfg['solvent_density_kg_m3'],p['density_kg_m3'])
        u=.003;b=h.bath_cfg;rho=sum(h.bath)/h._volume(h.bath)
        self.assertAlmostEqual(h._lld(u),.94*math.sqrt(b['surface_tension_n_m']/(rho*9.80665))*(b['viscosity_pa_s']*u/b['surface_tension_n_m'])**(2/3),places=15)
    def test_bath_from_reference_guards(self):
        h=OssilaDipcoaterRuntime('g');h.invoke('plant.configure',**dict(PLANT_EXAMPLE,temperature_k=303.15));before=h.debug_snapshot()
        with self.assertRaisesRegex(ActionRejected,'REFERENCE_OUT_OF_RANGE'):h.invoke('bath.configure_from_reference',**BATH_REF_EXAMPLE)
        self.assertEqual(before,h.debug_snapshot())
        g=OssilaDipcoaterRuntime('g2');g.invoke('plant.configure',**PLANT_EXAMPLE)
        with self.assertRaisesRegex(ActionRejected,'REFERENCE_INCOMPLETE'):g.invoke('bath.configure_from_reference',**dict(BATH_REF_EXAMPLE,solvent_id='menthol'))
        r=g.session().invoke(instance_id=g.instance_id,capability_id='bath.configure_from_reference',arguments=BATH_REF_EXAMPLE,request_id='a');self.assertEqual(r['error']['code'],'ACCESS_DENIED')
    def test_lld_thickness_ranks_with_real_capillary_number(self):
        res={}
        for sid in ('water','ethanol','ethylene_glycol'):
            h=OssilaDipcoaterRuntime(sid);h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('bath.configure_from_reference',**dict(BATH_REF_EXAMPLE,solvent_id=sid));res[sid]=h._lld(.002)
        self.assertGreater(res['ethylene_glycol'],res['ethanol']);self.assertGreater(res['ethanol'],res['water'])

if __name__=='__main__':unittest.main()
