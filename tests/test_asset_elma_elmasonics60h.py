import copy,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime.elma_elmasonics60h import ElmaElmasonics60hRuntime,CAPS,SLOTS,TIMES,ActionRejected,export_elma_elmasonics60h_contract_metadata
import asset_tool
def ready(h,load=True):
    h.invoke('cover.set_lift',lift_m=.3);h.invoke('bath.configure',**CAPS['bath.configure'][6])
    if load:h.invoke('load.configure',**CAPS['load.configure'][6])
    h.invoke('cover.set_lift',lift_m=0.);h.invoke('power.set',enabled=True)
def energy_error(h):
    s=h.read('evaluator');return s['energy.heater_input_j']+s['energy.ultrasound_input_j']+s['energy.loading_input_j']-sum(s['energy.'+k+'_j'] for k in ['stored','ambient_loss','acoustic_loss','unloading_output'])
class TestS60H(unittest.TestCase):
    def test_pure_read_and_setpoint(self):
        h=ElmaElmasonics60hRuntime();h.invoke('thermal.set_temperature',temperature_k=333.15);self.assertEqual(h.temperature,298.15);old=copy.deepcopy(h.__dict__);h.read();h.describe();h.invoke('status.measure');h.session().read(instance_id=h.instance_id);self.assertEqual(old,h.__dict__)
    def test_rejection_atomic(self):
        h=ElmaElmasonics60hRuntime()
        for cap,a in [('ultrasound.start',{'temperature_controlled':False}),('thermal.set_enabled',{'enabled':True}),('bath.configure',CAPS['bath.configure'][6]),('thermal.set_temperature',{'temperature_k':314.15}),('timer.configure',{'continuous':False,'duration_s':360.}),('power.set',{'enabled':1}),('drain.set_angle',{'angle_rad':float('nan')})]:
            old=copy.deepcopy(h.__dict__)
            with self.assertRaises(ActionRejected):h.invoke(cap,**a)
            self.assertEqual(old,h.__dict__)
    def test_all_manufacturer_timer_stages(self):
        h=ElmaElmasonics60hRuntime()
        for d in TIMES:h.invoke('timer.configure',continuous=False,duration_s=d);self.assertEqual(h.duration,d)
        for i in range(11):h.invoke('thermal.select_stage',stage=i);self.assertEqual(h.target,303.15+5*i)
        for i in [True,2.1,-1,11]:
            with self.assertRaises(ActionRejected):h.invoke('thermal.select_stage',stage=i)
    def test_timed_complete_and_independent_heat(self):
        h=ElmaElmasonics60hRuntime();ready(h);h.invoke('thermal.set_enabled',enabled=True);h.invoke('ultrasound.start',temperature_controlled=False);h.step(61);self.assertEqual(h.phase,'succeeded');self.assertTrue(h.heater);self.assertEqual(h.elapsed_s,60.);self.assertFalse(h.active)
    def test_peak_not_average_and_energy(self):
        h=ElmaElmasonics60hRuntime();ready(h);h.invoke('ultrasound.start',temperature_controlled=False);h.step(10);self.assertAlmostEqual(h.energy['ultrasound_input'],1500.);self.assertGreater(h.temperature,298.15);self.assertAlmostEqual(energy_error(h),0.,7)
    def test_sweep_degas_mutual_exclusion_and_auto(self):
        h=ElmaElmasonics60hRuntime();ready(h);h.invoke('ultrasound.set_mode',mode='sweep');h.invoke('degas.start');self.assertEqual(h.mode,'degas')
        with self.assertRaises(ActionRejected):h.invoke('ultrasound.set_mode',mode='sweep')
        h.step(600);self.assertEqual(h.phase,'succeeded');self.assertEqual(h.elapsed_s,600);self.assertIsNone(h._measure()['degassing_fraction']);self.assertAlmostEqual(energy_error(h),0.,6)
    def test_temperature_controlled_wait_mix(self):
        h=ElmaElmasonics60hRuntime();ready(h);h.invoke('thermal.set_temperature',temperature_k=303.15);h.invoke('thermal.set_enabled',enabled=True);h.invoke('ultrasound.start',temperature_controlled=True);h.step(65);self.assertTrue(h.waiting);self.assertEqual(h.elapsed_s,0.);self.assertAlmostEqual(h.energy['ultrasound_input'],750.)
        h.step(300);self.assertFalse(h.waiting);self.assertEqual(h.phase,'succeeded');self.assertTrue(h.heater);self.assertAlmostEqual(energy_error(h),0.,6)
    def test_continuous_twelve_hour_off(self):
        h=ElmaElmasonics60hRuntime();ready(h,False);h.invoke('timer.configure',continuous=True,duration_s=60.);h.invoke('ultrasound.set_mode',mode='degas');h.invoke('ultrasound.start',temperature_controlled=False);self.assertIsNone(h.read()['timer.remaining_s']);h.step(43200);self.assertEqual(h.phase,'succeeded');self.assertFalse(h.powered)
    def test_mass_energy_loading_draining(self):
        h=ElmaElmasonics60hRuntime();h.invoke('cover.set_lift',lift_m=.3);h.invoke('bath.configure',**dict(CAPS['bath.configure'][6],initial_temperature_k=288.15));h.invoke('load.configure',**dict(CAPS['load.configure'][6],initial_temperature_k=333.15));self.assertAlmostEqual(energy_error(h),0.,7)
        h.invoke('power.set',enabled=True);h.invoke('thermal.set_enabled',enabled=True);h.step(300);h.invoke('power.set',enabled=False);h.invoke('load.unload');h.invoke('drain.set_angle',angle_rad=math.pi/2);v=h.volume;h.step(20);self.assertLess(h.volume,v);self.assertAlmostEqual(h.mass_in,h._mass()+h.mass_out,10);self.assertAlmostEqual(energy_error(h),0.,6);h.step(1200);self.assertEqual(h.volume,0.);self.assertAlmostEqual(h.mass_in,h.mass_out,10)
    def test_privileges_async_cancel_dedup(self):
        h=ElmaElmasonics60hRuntime();ready(h);s=h.session();a=dict(instance_id=h.instance_id,capability_id='ultrasound.start',arguments={'temperature_controlled':False},request_id='start');r=s.invoke(**a);self.assertEqual(r['status'],'accepted');self.assertEqual(s.invoke(**a),r);h.step(10);temp=h.temperature;self.assertEqual(s.cancel(r['operation_id'],'cancel')['status'],'cancelled');self.assertEqual(h.temperature,temp)
        h.register_principal('other');
        with self.assertRaises(ActionRejected):h.session('other').poll(r['operation_id'])
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['bath.volume_m3'])
        self.assertNotIn('bath.configure',s.describe(instance_id=h.instance_id)['capabilities']);self.assertEqual(s.invoke(**dict(a,capability_id='ultrasound.stop',arguments={}))['status'],'rejected')
    def test_two_instances_and_dt(self):
        a=ElmaElmasonics60hRuntime('a');b=ElmaElmasonics60hRuntime('b');old=copy.deepcopy(b.__dict__);ready(a);a.invoke('degas.start');a.step(10);self.assertEqual(b.__dict__,old)
        old=copy.deepcopy(a.__dict__);a.step(0);self.assertEqual(old,a.__dict__)
        for x in [-1,True,float('nan'),float('inf')]:
            with self.assertRaises(ActionRejected):a.step(x)
    def test_all_capability_results_schemas(self):
        h=ElmaElmasonics60hRuntime();seen=set()
        def call(cap,**a):
            r=h.invoke(cap,**a);seen.add(cap);self.assertEqual(asset_tool.validate_value(r,CAPS[cap][1]),[],cap)
        for cap in ['cover.set_lift','bath.configure','load.configure','thermal.set_temperature','thermal.select_stage','timer.configure','ultrasound.set_mode','power.set','thermal.set_enabled','ultrasound.start']:call(cap,**CAPS[cap][6])
        h.step(60);call('degas.start');h.step(3);call('ultrasound.stop');call('power.set',enabled=False);call('load.unload');call('drain.set_angle',angle_rad=math.pi/2);h.step(3);call('status.measure');self.assertEqual(seen,set(CAPS))
        for k,v in h.read('evaluator').items():self.assertEqual(asset_tool.validate_value(v,SLOTS[k][0]),[],k)
        for c in export_elma_elmasonics60h_contract_metadata()['capabilities'].values():self.assertEqual(asset_tool.lint_schema(c['input_schema']),[]);self.assertEqual(asset_tool.lint_schema(c['output_schema']),[])
if __name__=='__main__':unittest.main()
