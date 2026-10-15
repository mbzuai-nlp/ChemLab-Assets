"""Meaningful process, ledger, schema, cancellation and access-boundary tests."""
import copy,importlib.util,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import ossila_spincoater as rt
from asset_runtime.ossila_spincoater import *
spec=importlib.util.spec_from_file_location('spin_asset_tool',ROOT/'skills/chemistry-lab-assets/scripts/asset_tool.py')
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)

def configured(name='spincoater_01',evap=.001):
    h=rt.OssilaSpincoaterRuntime(name)
    h.invoke('plant.configure',**dict(rt.PLANT_EXAMPLE,mass_transfer_m_s=evap))
    h.invoke('method.configure',**copy.deepcopy(rt.METHOD_EXAMPLE))
    h.invoke('lid.set_angle',angle_rad=1.5);h.step(1.1)
    h.invoke('sample.configure',**copy.deepcopy(rt.SAMPLE_EXAMPLE))
    h.invoke('lid.set_angle',angle_rad=0.);h.step(1.1);h.invoke('power.set',enabled=True)
    return h

def balances(h):
    return [h.initial[i]-h.film[i]-h.waste[i]-h.evaporated[i]-h.collected[i] for i in range(2)]

class SpinCoaterTests(unittest.TestCase):
    def test_schema_negative_control(self):
        self.assertTrue(tool.validate_value({'enabled':1},rt.CAPS['power.set'][1]))
        self.assertTrue(tool.validate_value({'phase':'invented'},rt.CAPS['acquisition.stop'][1]))
    def test_target_does_not_advance(self):
        h=configured();before=h.read('evaluator');h.invoke('acquisition.start')
        self.assertEqual(h.speed,0);self.assertEqual(h.film[0],before['truth.ledger']['film_solvent_kg'])
        self.assertGreater(h.target_speed,0);h.step(.1);self.assertGreater(h.speed,0)
    def test_read_describe_are_pure(self):
        h=configured();before=h.debug_snapshot()
        for _ in range(3):h.read();h.describe();h.invoke('coating.measure')
        self.assertEqual(before,h.debug_snapshot())
    def test_reject_preserves_all_process_state(self):
        h=configured();before=h.debug_snapshot()
        for cap,args in [('method.configure',{'steps':[]}),('lid.set_angle',{'angle_rad':float('nan')}),('power.set',{'enabled':1}),('sample.configure',rt.SAMPLE_EXAMPLE)]:
            with self.assertRaises(rt.ActionRejected):h.invoke(cap,**args)
            self.assertEqual(before,h.debug_snapshot())
    def test_standard_has_no_acceleration_device_setting(self):
        h=configured();before=h.debug_snapshot()
        with self.assertRaises(rt.ActionRejected):h.invoke('method.configure',steps=rt.METHOD_EXAMPLE['steps'],acceleration_rad_s2=10.)
        self.assertEqual(before,h.debug_snapshot())
        self.assertNotIn('plant.configure',h.describe()['capabilities'])
    def test_open_lid_blocks_start(self):
        h=configured();h.invoke('lid.set_angle',angle_rad=1.5);h.step(1.1);before=h.debug_snapshot()
        with self.assertRaises(rt.ActionRejected):h.invoke('acquisition.start')
        self.assertEqual(before,h.debug_snapshot())
    def test_open_lid_during_running_decelerates(self):
        h=configured();h.invoke('acquisition.start');h.step(.5);speed=h.speed
        h.invoke('lid.set_angle',angle_rad=1.5);self.assertEqual(h.speed,speed);self.assertEqual(h.phase,'stopping')
        h.step(.1);self.assertLess(h.speed,speed);h.step(2);self.assertEqual(h.phase,'cancelled')
    def test_power_off_is_not_instant_stop(self):
        h=configured();h.invoke('acquisition.start');h.step(.5);h.invoke('power.set',enabled=False)
        self.assertGreater(h.speed,0);h.step(3);self.assertEqual(h.speed,0)
    def test_sequence_completes_after_braking(self):
        h=configured();h.invoke('acquisition.start');h.step(6)
        self.assertAlmostEqual(h.elapsed_s,6);self.assertEqual(h.step_index,2);self.assertEqual(h.phase,'stopping')
        h.step(4);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.speed,0)
    def test_material_conservation_and_cancel_residue(self):
        h=configured();h.invoke('acquisition.start');h.step(1.8)
        film=h.film.copy();self.assertGreater(h.waste[0],0);self.assertGreater(h.evaporated[0],0)
        h.invoke('acquisition.stop');self.assertEqual(film,h.film)
        h.step(4)
        for value in balances(h):self.assertLess(abs(value),1e-16)
        self.assertEqual(h.evaporated[1],0);self.assertGreater(h.film[1],0)
    def test_no_clock_no_evaporation(self):
        h=configured();before=h.film.copy()
        for _ in range(10):h.invoke('product.read')
        self.assertEqual(before,h.film)
    def test_cancel_does_not_stop_natural_evaporation(self):
        h=configured();h.invoke('acquisition.start');h.step(.1);h.invoke('acquisition.stop');h.step(1)
        solvent=h.film[0];h.step(2);self.assertLess(h.film[0],solvent)
    def test_dry_mass_finite(self):
        h=configured(evap=.1);h.invoke('acquisition.start');h.step(100)
        self.assertGreaterEqual(h.film[0],0);self.assertGreaterEqual(h.film[1],0)
        for value in balances(h):self.assertLess(abs(value),1e-16)
    def test_collect_and_no_repeated_transfer(self):
        h=configured();h.invoke('acquisition.start');h.step(12)
        h.invoke('lid.set_angle',angle_rad=1.5);h.step(1.1)
        h.invoke('product.collect',recipient_id='output');before=h.debug_snapshot()
        self.assertGreater(h.collected[1],0)
        with self.assertRaises(rt.ActionRejected):h.invoke('product.collect',recipient_id='output')
        self.assertEqual(before,h.debug_snapshot())
        for value in balances(h):self.assertLess(abs(value),1e-16)
    def test_two_instances_isolated(self):
        a=configured('a');b=configured('b');before=b.debug_snapshot()
        a.invoke('acquisition.start');a.step(3);self.assertEqual(before,b.debug_snapshot())
    def test_agent_truth_filter(self):
        h=configured();s=h.session()
        self.assertNotIn('truth.ledger',s.read(instance_id=h.instance_id)['observations'])
        self.assertNotIn('product.read',s.describe(instance_id=h.instance_id)['capabilities'])
        result=s.invoke(instance_id=h.instance_id,capability_id='product.read',arguments={},request_id='truth')
        self.assertEqual(result['error']['code'],'ACCESS_DENIED');self.assertNotIn('solvent',json.dumps(result))
        with self.assertRaises(rt.ActionRejected):s.read(instance_id=h.instance_id,state_ids=['truth.ledger'])
    def test_rejected_role_escalation(self):
        h=configured();s=h.session()
        with self.assertRaises(TypeError):s.read(instance_id=h.instance_id,visibility='evaluator')
        with self.assertRaises(rt.ActionRejected):h.session('evaluator')
    def test_idempotency_and_request_conflict(self):
        h=configured();s=h.session();kw=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start')
        a=s.invoke(**kw);h.step(.3);before=h.debug_snapshot();b=s.invoke(**kw)
        self.assertEqual(a,b);self.assertEqual(before,h.debug_snapshot())
        result=s.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id='start')
        self.assertEqual(result['error']['code'],'REQUEST_CONFLICT')
    def test_async_owner_and_cancel(self):
        h=configured();s=h.session();h.register_principal('other');other=h.session('other')
        r=s.invoke(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='start');op=r['operation_id']
        self.assertEqual(r['status'],'accepted');h.step(.5)
        with self.assertRaises(rt.ActionRejected):other.poll(op)
        before=h.debug_snapshot();s.poll(op);self.assertEqual(before,h.debug_snapshot())
        s.cancel(op,'cancel');h.step(3);self.assertEqual(s.poll(op)['status'],'cancelled')
    def test_write_routes_capability(self):
        h=configured();s=h.session();s.write(instance_id=h.instance_id,state_id='power.enabled',value=False,request_id='off')
        self.assertFalse(h.powered)
        with self.assertRaises(rt.ActionRejected):s.write(instance_id=h.instance_id,state_id='motor.indicated_rad_s',value=200.,request_id='bad')
    def test_reference_known_value_and_no_extrapolation(self):
        h=configured();r=h.invoke('reference.lookup',substance_id='ethanol',temperature_k=298.15)
        self.assertAlmostEqual(r['vapor_pressure_pa'],7869.08504803628,places=8)
        with self.assertRaises(rt.ActionRejected):h.invoke('reference.lookup',substance_id='ethanol',temperature_k=1.)
        self.assertGreaterEqual(len(h.templates),50)
    def test_analytic_pure_solvent_drainage(self):
        h=configured(evap=0);h.film[1]=0.;h.initial[1]=0.;h.waste[1]=0.
        h0=h._product()['wet_thickness_m'];omega=120*rt.RPM;dt=.1
        rho=h.sample['solvent_density_kg_m3'];mu=h.sample['viscosity_pa_s']
        expected=h0/math.sqrt(1+4*rho*omega**2*h0**2*dt/(3*mu))
        h._film_step(dt,omega)
        self.assertAlmostEqual(h._product()['wet_thickness_m'],expected,places=14)
    def test_step_partition_consistent(self):
        a=configured();b=configured();a.invoke('acquisition.start');b.invoke('acquisition.start');a.step(3.)
        for _ in range(300):b.step(.01)
        self.assertAlmostEqual(a.speed,b.speed,places=9);self.assertAlmostEqual(a.film[0],b.film[0],places=12)
    def test_invalid_clock(self):
        h=configured();before=h.debug_snapshot()
        for value in [0,-1,True,float('inf'),float('nan')]:
            with self.assertRaises(rt.ActionRejected):h.step(value)
            self.assertEqual(before,h.debug_snapshot())
    def test_actual_output_schemas(self):
        h=configured();results=[]
        for c,args in [('power.set',{'enabled':True}),('power.toggle',{}),('method.configure',rt.METHOD_EXAMPLE),('reference.lookup',{'substance_id':'ethanol','temperature_k':298.15}),('coating.measure',{}),('product.read',{})]:
            result=h.invoke(c,**args);errors=tool.validate_value(result,rt.CAPS[c][1],path='result');self.assertFalse(errors,(c,errors));results.append(c)
        h.invoke('power.set',enabled=True)
        for c in ['acquisition.start','acquisition.stop']:
            result=h.invoke(c);errors=tool.validate_value(result,rt.CAPS[c][1],path='result');self.assertFalse(errors,(c,errors))
        for k,v in h.read('evaluator').items():
            errors=tool.validate_value(v,rt.SLOTS[k][0],path=k);self.assertFalse(errors,(k,errors))


# --- 298.15 K real solvent-property library (shared thin-film solvent set, asset_version 1.1.0) ---
LIT_298K = {  # CRC-Handbook-level literature values at 25 C used as independent sanity anchors (rho kg/m3, mu Pa s, sigma N/m)
    'water': (997.05, 0.890e-3, 71.99e-3), 'ethanol': (785.1, 1.074e-3, 21.97e-3), 'toluene': (862.3, 0.560e-3, 27.93e-3),
    'ethylene_glycol': (1110.0, 16.1e-3, 47.99e-3), 'dmso': (1095.4, 1.987e-3, 42.92e-3), 'heptane': (679.5, 0.387e-3, 19.66e-3),
    'butyl_acetate': (876.4, 0.685e-3, 24.88e-3), 'nmp': (1028.0, 1.666e-3, 40.79e-3), 'chloroform': (1479.0, 0.537e-3, 26.67e-3),
    'cyclohexanone': (942.0, 2.017e-3, 34.57e-3)}


class SolventLibraryTests(unittest.TestCase):
    def runtime(self):
        return OssilaSpincoaterRuntime()
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

    def test_sample_from_reference_equals_host_path_with_same_numbers(self):
        def prep():
            h=OssilaSpincoaterRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('method.configure',**copy.deepcopy(METHOD_EXAMPLE))
            h.invoke('lid.set_angle',angle_rad=1.5);h.step(1.1);return h
        a=prep();out=a.invoke('sample.configure_from_reference',**SAMPLE_REF_EXAMPLE);p=a.invoke('reference.properties',substance_id='toluene')
        self.assertEqual(out['solvent_density_kg_m3'],p['density_kg_m3']);self.assertEqual(a.sample['viscosity_pa_s'],p['viscosity_pa_s']);self.assertIn('library_298.15K',out['evidence'])
        b=prep();b.invoke('sample.configure',**dict(SAMPLE_REF_EXAMPLE,solvent_density_kg_m3=p['density_kg_m3'],viscosity_pa_s=p['viscosity_pa_s']))
        for h in (a,b):h.invoke('lid.set_angle',angle_rad=0.);h.step(1.1);h.invoke('power.set',enabled=True);h.invoke('acquisition.start');h.step(8.)
        self.assertEqual(a.film,b.film);self.assertEqual(a.evaporated,b.evaporated);self.assertGreater(a.waste[0],0)
    def test_sample_from_reference_guards(self):
        h=OssilaSpincoaterRuntime();h.invoke('plant.configure',**dict(PLANT_EXAMPLE,temperature_k=303.15));h.invoke('lid.set_angle',angle_rad=1.5);h.step(1.1);before=h.debug_snapshot()
        with self.assertRaisesRegex(ActionRejected,'REFERENCE_OUT_OF_RANGE'):h.invoke('sample.configure_from_reference',**SAMPLE_REF_EXAMPLE)
        self.assertEqual(before,h.debug_snapshot())
        g=OssilaSpincoaterRuntime();g.invoke('plant.configure',**PLANT_EXAMPLE);g.invoke('lid.set_angle',angle_rad=1.5);g.step(1.1)
        with self.assertRaisesRegex(ActionRejected,'REFERENCE_INCOMPLETE'):g.invoke('sample.configure_from_reference',**dict(SAMPLE_REF_EXAMPLE,solvent_id='menthol'))
        s=g.session();r=s.invoke(instance_id=g.instance_id,capability_id='sample.configure_from_reference',arguments=SAMPLE_REF_EXAMPLE,request_id='a')
        self.assertEqual(r['error']['code'],'ACCESS_DENIED')
    def test_viscous_glycol_thins_slower_than_volatile_solvent(self):
        res={}
        for sid in ('ethylene_glycol','acetone'):
            h=OssilaSpincoaterRuntime();h.invoke('plant.configure',**PLANT_EXAMPLE);h.invoke('method.configure',**copy.deepcopy(METHOD_EXAMPLE));h.invoke('lid.set_angle',angle_rad=1.5);h.step(1.1)
            h.invoke('sample.configure_from_reference',**dict(SAMPLE_REF_EXAMPLE,solvent_id=sid));h.invoke('lid.set_angle',angle_rad=0.);h.step(1.1);h.invoke('power.set',enabled=True);h.invoke('acquisition.start');h.step(8.)
            res[sid]=(h._product()['wet_thickness_m'],h.evaporated[0])
        self.assertGreater(res['ethylene_glycol'][0],res['acetone'][0]);self.assertGreater(res['acetone'][1],res['ethylene_glycol'][1])

if __name__=='__main__':unittest.main()
