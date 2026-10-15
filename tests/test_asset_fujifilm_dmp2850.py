"""CHEM-058 runtime contract, finite ink and uncalibrated physical invariants."""
import copy,math,unittest
from scripts.asset_runtime import fujifilm_dmp2850 as m
from scripts.asset_runtime.fujifilm_dmp2850 import *
def ready(ink=None,method=None,sample=None,pattern=None):
    r=m.FujifilmDmp2850Runtime()
    for k,a in [('plant',m.PLANT_EXAMPLE),('ink',ink or m.INK_EXAMPLE),('method',method or m.METHOD_EXAMPLE),('substrate',sample or m.SAMPLE_EXAMPLE),('pattern',pattern or m.PATTERN_EXAMPLE)]:r.invoke(k+'.configure',**copy.deepcopy(a))
    r.invoke('power.set',enabled=True);r.invoke('vacuum.set',enabled=True);return r
def first_flight(r):
    r.invoke('acquisition.start')
    speeds=[r.plant['axis_speed_m_s']]*2+[r.plant['manual_speed_m_s'],r.plant['manual_angle_rad_s']]
    t=max(abs(a-b)/v for a,b,v in zip(r.q,r.target,speeds))
    r.step(max(1e-8,t));assert r.flight is not None
class TestDmp2850(unittest.TestCase):
    def assertLedger(self,r):
        p=r._product()
        for sp in ['solvent','solute']:
            self.assertAlmostEqual(p['initial_'+sp+'_kg'],sum(p[loc+'_'+sp+'_kg'] for loc in ['cartridge','flight','deposit','waste','evaporated','collected']),delta=1e-17)
    def rejected(self,r,k,**a):
        before=r.debug_snapshot()
        with self.assertRaises(m.ActionRejected):r.invoke(k,**a)
        self.assertEqual(before,r.debug_snapshot())
    def test_specs_picoliter_and_pitch(self):
        self.assertEqual(m.DROP_VOLUME,2.4e-15);self.assertAlmostEqual(m.PITCH,338.6666666667e-6)
        self.assertAlmostEqual(math.degrees(math.asin(20e-6/m.PITCH)),3.3855,places=3)
    def test_configuration_after_world_time(self):
        r=m.FujifilmDmp2850Runtime();r.step(.02);r.invoke('plant.configure',**m.PLANT_EXAMPLE);self.assertTrue(r.read()['plant.configured'])
    def test_read_and_describe_are_pure(self):
        r=ready();before=r.debug_snapshot()
        for _ in range(3):r.read();r.describe();r.session().read(instance_id=r.instance_id)
        self.assertEqual(before,r.debug_snapshot())
    def test_set_temperature_only_target(self):
        r=ready();r.invoke('thermal.set_temperature',platen_k=313.15,cartridge_k=308.15);self.assertEqual(r.temps,[298.15,298.15]);self.assertEqual(r.temp_targets,[313.15,308.15])
    def test_thermal_energy_balance_and_cooldown(self):
        r=ready();r.invoke('thermal.set_temperature',platen_k=323.15,cartridge_k=318.15);r.invoke('thermal.set_enabled',enabled=True);r.step(20);self.assertGreater(r.temps[0],298.15);self.assertLess(r.temps[0],323.15)
        t=r.temps[:];r.invoke('power.set',enabled=False);r.step(20);self.assertTrue(all(a<b for a,b in zip(r.temps,t)))
        p=r._product();self.assertAlmostEqual(p['heater_input_j'],p['thermal_loss_j']+p['sensible_j'],delta=1e-8)
    def test_temperature_limits_atomic(self):
        r=ready()
        for t in [280.,333.16,float('nan'),True]:self.rejected(r,'thermal.set_temperature',platen_k=t,cartridge_k=298.15)
    def test_invalid_step_atomic(self):
        r=ready()
        for t in [0,-1,float('nan'),float('inf'),True]:
            before=r.debug_snapshot()
            with self.assertRaises(m.ActionRejected):r.step(t)
            self.assertEqual(before,r.debug_snapshot())
    def test_invalid_arguments_atomic(self):
        r=ready()
        for k,a in [('power.set',{'enabled':1}),('power.set',{'enabled':True,'junk':4}),('pattern.configure',dict(m.PATTERN_EXAMPLE,rows=['12'])),('method.configure',dict(m.METHOD_EXAMPLE,nozzles=[1,1]))]:self.rejected(r,k,**a)
    def test_capacity_limited(self):
        r=m.FujifilmDmp2850Runtime();r.invoke('plant.configure',**m.PLANT_EXAMPLE);self.rejected(r,'ink.configure',**dict(m.INK_EXAMPLE,solvent_kg=.002))
    def test_explicit_model_acceptance(self):
        r=m.FujifilmDmp2850Runtime();self.rejected(r,'plant.configure',**dict(m.PLANT_EXAMPLE,accept_uncalibrated_model=False))
    def test_no_refill_single_charge(self):
        r=ready();r.invoke('ink.drain');self.rejected(r,'ink.configure',**m.INK_EXAMPLE);self.assertLedger(r)
    def test_print_area_thickness_boundary(self):
        for h,L in [(.000499,.308),(.0005,.253),(.025,.253)]:
            r=m.FujifilmDmp2850Runtime();r.invoke('plant.configure',**m.PLANT_EXAMPLE);r.invoke('substrate.configure',**dict(m.SAMPLE_EXAMPLE,thickness_m=h,length_m=L))
        r=m.FujifilmDmp2850Runtime();r.invoke('plant.configure',**m.PLANT_EXAMPLE);self.rejected(r,'substrate.configure',**dict(m.SAMPLE_EXAMPLE,thickness_m=.0005,length_m=.254))
    def test_pattern_validation(self):
        r=ready()
        for rows in [['00'],['1','11'],['x'],['1'*17]]:self.rejected(r,'pattern.configure',**dict(m.PATTERN_EXAMPLE,rows=rows))
        self.rejected(r,'pattern.configure',**dict(m.PATTERN_EXAMPLE,x_m=.101))
    def test_waveform_period_and_factor(self):
        r=ready();self.rejected(r,'method.configure',**dict(m.METHOD_EXAMPLE,frequency_hz=15000,dwell_s=.001));self.rejected(r,'method.configure',**dict(m.METHOD_EXAMPLE,voltage_v=1.))
    def test_no_vacuum_no_print(self):
        r=ready();r.invoke('vacuum.set',enabled=False);self.rejected(r,'acquisition.start')
    def test_motion_is_asynchronous(self):
        r=ready();old=r.q[:];r.invoke('axis.move_to',x_m=.05,y_m=-.03,z_m=.003);self.assertEqual(r.q,old);r.step(1);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.q[:3],[.05,-.03,.003])
    def test_manual_angle_target(self):
        r=ready();r.invoke('cartridge.set_angle',angle_rad=.7);self.assertEqual(r.q[3],0.);r.step(1);self.assertEqual(r.q[3],.7)
    def test_nozzle_pattern_offset_compensation(self):
        r=ready();first_flight(r);x,y,n=r.points[0];delta=(n-5.5)*m.PITCH;a=r.q[3]
        self.assertAlmostEqual(r.q[0]+delta*math.cos(a),x-.104)
        self.assertAlmostEqual(-r.q[1]+y-.154,delta*math.sin(a))
        self.assertAlmostEqual(r.q[2]-r.sample['thickness_m'],r.method['gap_m'])
    def test_complete_print_and_mass(self):
        r=ready();r.invoke('acquisition.start');r.step(3);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.drop_count,5);self.assertEqual(len(r.spots),5);self.assertEqual({s['n'] for s in r.spots},{0,5,11});self.assertLedger(r)
    def test_cancel_preserves_inflight_and_partial_product(self):
        r=ready();first_flight(r);self.assertEqual(r.drop_count,1);self.assertFalse(r.spots);r.invoke('acquisition.stop');self.assertIsNotNone(r.flight);r.step(.01);self.assertIsNone(r.flight);self.assertEqual(len(r.spots),1);self.assertEqual(r.phase,'cancelled');self.assertLedger(r)
    def test_power_loss_releases_vacuum_not_material(self):
        r=ready();first_flight(r);r.invoke('power.set',enabled=False);r.step(.01);self.assertFalse(r.vacuum);self.assertEqual(len(r.spots),1);self.assertLedger(r)
    def test_periodic_cleaning_consumes_finite_ink(self):
        r=ready(method=dict(m.METHOD_EXAMPLE,clean_every_drops=2));r.invoke('acquisition.start');r.step(20);self.assertEqual(r.phase,'succeeded');self.assertEqual(r.cleanings,2);self.assertGreater(r.waste[0],0);self.assertLedger(r)
    def test_cancel_cleaning_keeps_waste(self):
        r=ready();r.invoke('axis.move_to',x_m=0,y_m=0,z_m=.003);r.step(1);r.invoke('cleaning.start');r.step(2.02);r.invoke('acquisition.stop');self.assertGreater(r.waste[0],0);self.assertLess(r.waste[0],m.PLANT_EXAMPLE['cleaning_flow_m3_s']*.05*m.INK_EXAMPLE['solvent_density_kg_m3']);self.assertLedger(r)
    def test_empty_cartridge_stops_without_negative_mass(self):
        r=ready(ink=dict(m.INK_EXAMPLE,solvent_kg=789*m.DROP_VOLUME*2.5,solute_kg=0.));r.invoke('acquisition.start');r.step(3);self.assertEqual(r.phase,'failed');self.assertEqual(r.drop_count,2);self.assertEqual(r.fault,'INK_EMPTY');self.assertLedger(r)
    def test_drying_evaporates_only_solvent(self):
        r=ready();r.invoke('acquisition.start');r.step(3);solute=sum(s['m'][1] for s in r.spots);before=r.evaporated[0];r.step(100);self.assertGreater(r.evaporated[0],before);self.assertEqual(sum(s['m'][1] for s in r.spots),solute);self.assertLedger(r)
    def test_dropwatch_uses_fired_drop_and_no_time_advance(self):
        r=ready();r.invoke('axis.move_to',x_m=0,y_m=0,z_m=.003);r.step(1);r.invoke('dropwatch.start');r.step(3);self.assertEqual(r.drop_count,1);self.assertGreater(r.waste[0],0);before=r.debug_snapshot();c=r.invoke('dropwatch.observe',delay_s=.0001);self.assertEqual(before,r.debug_snapshot());self.assertEqual(len(c['pixels']),4096);self.assertIn(32,c['pixels']);self.assertAlmostEqual(c['drop_diameter_m'],(6*m.DROP_VOLUME/math.pi)**(1/3));self.assertLedger(r)
    def test_camera_after_impact_blank_and_uncalibrated(self):
        r=ready();self.rejected(r,'dropwatch.observe',delay_s=.0001);first_flight(r);c=r.invoke('dropwatch.observe',delay_s=.01);self.assertEqual(set(c['pixels']),{240});self.assertIn('synthetic',c['evidence'])
    def test_collection_and_drain(self):
        r=ready();r.invoke('acquisition.start');r.step(3);r.invoke('product.collect',recipient_id='slide');self.assertFalse(r.sample_present);self.assertFalse(r.spots);self.assertGreater(r.collected[1],0);r.invoke('ink.drain');self.assertEqual(r.cartridge,[0,0]);self.assertLedger(r)
    def test_busy_and_inflight_attachment_rejected(self):
        r=ready();first_flight(r);self.rejected(r,'method.configure',**m.METHOD_EXAMPLE);r.invoke('acquisition.stop');self.rejected(r,'product.collect',recipient_id='x')
    def test_independent_instances(self):
        a,b=ready(),ready();before=b.debug_snapshot();a.invoke('acquisition.start');a.step(3);self.assertEqual(before,b.debug_snapshot())
    def test_reference_no_extrapolation(self):
        r=ready();self.assertGreater(r.invoke('reference.lookup',substance_id='ethanol',temperature_k=298.15)['vapor_pressure_pa'],1000);self.rejected(r,'reference.lookup',substance_id='ethanol',temperature_k=100.);self.rejected(r,'reference.lookup',substance_id='no_such_ink',temperature_k=298.15)
    def test_session_truth_and_role_isolation(self):
        r=ready();s=r.session();self.assertNotIn('truth.ledger',s.describe(instance_id=r.instance_id)['state_slots'])
        for cap in ['product.read','ink.drain','vacuum.set']:
            out=s.invoke(instance_id=r.instance_id,capability_id=cap,arguments={'enabled':False} if cap=='vacuum.set' else {},request_id=cap);self.assertEqual(out['error']['code'],'ACCESS_DENIED');self.assertEqual(out['result'],{})
        with self.assertRaises(m.ActionRejected):s.read(instance_id=r.instance_id,state_ids=['truth.ledger'])
    def test_session_idempotency_and_conflict(self):
        r=ready();s=r.session();args=dict(instance_id=r.instance_id,capability_id='power.set',arguments={'enabled':True},request_id='one');a=s.invoke(**args);rev=r.revision;self.assertEqual(s.invoke(**args),a);self.assertEqual(r.revision,rev);args['arguments']={'enabled':False};self.assertEqual(s.invoke(**args)['error']['code'],'REQUEST_CONFLICT');self.assertTrue(r.powered)
    def test_session_poll_cancel_owner_and_write(self):
        r=ready();r.register_principal('second');s=r.session();out=s.invoke(instance_id=r.instance_id,capability_id='acquisition.start',arguments={},request_id='p');op=out['operation_id'];self.assertEqual(out['status'],'accepted')
        before=r.debug_snapshot();s.poll(op);self.assertEqual(before,r.debug_snapshot())
        with self.assertRaises(m.ActionRejected):r.session('second').poll(op)
        s.cancel(op,'c');self.assertEqual(r.phase,'cancelled');s.write(instance_id=r.instance_id,state_id='thermal.enabled',value=True,request_id='heat');self.assertTrue(r.heating)
    def test_step_partition_mass_positions(self):
        a,b=ready(),ready();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(3)
        for _ in range(300):b.step(.01)
        self.assertEqual(a.q,b.q);self.assertEqual(a.drop_count,b.drop_count);self.assertEqual(a.phase,b.phase);self.assertLedger(a);self.assertLedger(b)
        self.assertAlmostEqual(a.evaporated[0],b.evaporated[0],delta=1e-16)

# --- 298.15 K real solvent-property library (shared thin-film solvent set, asset_version 1.1.0) ---
LIT_298K = {  # CRC-Handbook-level literature values at 25 C used as independent sanity anchors (rho kg/m3, mu Pa s, sigma N/m)
    'water': (997.05, 0.890e-3, 71.99e-3), 'ethanol': (785.1, 1.074e-3, 21.97e-3), 'toluene': (862.3, 0.560e-3, 27.93e-3),
    'ethylene_glycol': (1110.0, 16.1e-3, 47.99e-3), 'dmso': (1095.4, 1.987e-3, 42.92e-3), 'heptane': (679.5, 0.387e-3, 19.66e-3),
    'butyl_acetate': (876.4, 0.685e-3, 24.88e-3), 'nmp': (1028.0, 1.666e-3, 40.79e-3), 'chloroform': (1479.0, 0.537e-3, 26.67e-3),
    'cyclohexanone': (942.0, 2.017e-3, 34.57e-3)}


class SolventLibraryTests(unittest.TestCase):
    def runtime(self):
        return FujifilmDmp2850Runtime()
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

    def test_printability_numbers_from_real_properties(self):
        h=self.runtime();L=(6*DROP_VOLUME/math.pi)**(1/3);v=5.
        for sid in ('water','ethanol','ethylene_glycol','dmso','butoxyethanol_2'):
            p=h.invoke('reference.properties',substance_id=sid);o=h.invoke('ink.printability',substance_id=sid,characteristic_length_m=L,drop_velocity_m_s=v)
            oh=p['viscosity_pa_s']/math.sqrt(p['density_kg_m3']*p['surface_tension_n_m']*L)
            self.assertAlmostEqual(o['ohnesorge'],oh,places=12);self.assertAlmostEqual(o['z_number']*o['ohnesorge'],1,places=12)
            self.assertAlmostEqual(o['reynolds'],p['density_kg_m3']*v*L/p['viscosity_pa_s'],places=9);self.assertAlmostEqual(math.sqrt(o['weber'])/o['reynolds'],oh,places=12)
            self.assertEqual(o['within_z_1_to_10'],1<1/oh<10)
        eg=h.invoke('ink.printability',substance_id='ethylene_glycol',characteristic_length_m=L,drop_velocity_m_s=v);w=h.invoke('ink.printability',substance_id='water',characteristic_length_m=L,drop_velocity_m_s=v)
        self.assertTrue(eg['within_z_1_to_10']);self.assertFalse(w['within_z_1_to_10']);self.assertFalse(eg['within_cartridge_viscosity_window'])
    def test_no_pure_solvent_inside_samba_ideal_window(self):
        h=self.runtime()
        inside=[s for s,r in h.templates.items() if r.get('usable_at_298_15K') and .004<=r['properties_298_15K']['viscosity_pa_s']['value']<=.008 and .028<=r['properties_298_15K']['surface_tension_n_m']['value']<=.032]
        self.assertEqual(inside,[]);self.assertNotIn('ink.configure_from_reference',CAPS)
    def test_printability_rejects_incomplete_record_and_bad_length(self):
        h=self.runtime();before=h.debug_snapshot()
        with self.assertRaisesRegex(ActionRejected,'REFERENCE_INCOMPLETE'):h.invoke('ink.printability',substance_id='menthol',characteristic_length_m=2e-5,drop_velocity_m_s=5.)
        with self.assertRaises(ActionRejected):h.invoke('ink.printability',substance_id='water',characteristic_length_m=0.,drop_velocity_m_s=5.)
        self.assertEqual(before,h.debug_snapshot())

if __name__=='__main__':unittest.main()
