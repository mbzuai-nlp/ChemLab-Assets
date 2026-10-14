import copy,json,math,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'skills/chemistry-lab-assets/scripts'))
from asset_runtime.aqualab_4te import Aqualab4teRuntime,CAPS,SLOTS,ActionRejected,export_aqualab_4te_contract_metadata
import asset_tool
def configure(h,sid='sodium_chloride',**kw):
    row=h.templates[sid];index=min(range(len(row['records'])),key=lambda i:abs(row['records'][i]['temperature_k']-298.15))
    a=dict(CAPS['sample.configure'][6],substance_id=sid,record_index=index);a.update(kw)
    h.invoke('lid.set_angle',angle_rad=math.pi/2);h.invoke('sample.configure',**a)
def ready(h,sid='sodium_chloride',**kw):
    h.invoke('power.set',enabled=True);configure(h,sid,**kw)
    h.invoke('thermal.set_temperature',temperature_k=round((h.sample['reference']['temperature_k']-273.15)*10)/10+273.15)
    h.invoke('lid.set_angle',angle_rad=0.);h.invoke('latch.set_angle',angle_rad=math.pi/2)
def ledger(h):
    return h.water_input+h.ambient_in-(h.sample_water+h.vapor+h.water_removed+h.ambient_out)
class Test4TE(unittest.TestCase):
    def test_setpoint_and_reads_pure(self):
        h=Aqualab4teRuntime();h.invoke('thermal.set_temperature',temperature_k=303.15);self.assertEqual(h.block_k,298.15)
        old=copy.deepcopy(h.__dict__);h.read();h.describe();h.session().read(instance_id=h.instance_id);h.invoke('water_activity.measure');self.assertEqual(old,h.__dict__)
    def test_rejection_atomic(self):
        h=Aqualab4teRuntime()
        for cap,args in [('acquisition.start',{}),('sample.configure',CAPS['sample.configure'][6]),('thermal.set_temperature',{'temperature_k':298.16}),('thermal.set_temperature',{'temperature_k':True}),('thermal.set_temperature',{'temperature_k':float('nan')}),('isotherm.configure',dict(CAPS['isotherm.configure'][6],points=[{'water_activity':.5,'moisture_dry_basis':.1}]*2))]:
            old=copy.deepcopy(h.__dict__)
            with self.assertRaises(ActionRejected):h.invoke(cap,**args)
            self.assertEqual(old,h.__dict__)
    def test_lid_lock_interlock(self):
        h=Aqualab4teRuntime();ready(h)
        with self.assertRaises(ActionRejected):h.invoke('lid.set_angle',angle_rad=1.5)
        h.invoke('acquisition.start');h.step(15);h.invoke('latch.set_angle',angle_rad=0.);self.assertEqual(h.phase,'cancelled');h.invoke('lid.set_angle',angle_rad=1.5)
        with self.assertRaises(ActionRejected):h.invoke('latch.set_angle',angle_rad=1.5)
    def test_water_and_total_mass_ledger(self):
        h=Aqualab4teRuntime();ready(h);h.invoke('acquisition.start');h.step(400);self.assertAlmostEqual(ledger(h),0.,14)
        h.invoke('latch.set_angle',angle_rad=0.);h.invoke('lid.set_angle',angle_rad=1.5);h.step(20);h.invoke('sample.unload');h.step(20)
        self.assertAlmostEqual(ledger(h),0.,14);self.assertAlmostEqual(h.input_kg+h.ambient_in,h.removed_kg+h.vapor+h.ambient_out,14)
    def test_equilibrium_warmup_and_resolution(self):
        h=Aqualab4teRuntime();ready(h);h.invoke('acquisition.start');h.step(600);self.assertEqual(h.phase,'succeeded');r=h.invoke('water_activity.measure')
        self.assertTrue(r['equilibrated']);self.assertLess(abs(r['water_activity']-h.sample['reference']['value']),.003);self.assertEqual(r['quality'],'warmup_incomplete');self.assertIsNone(r['moisture_dry_basis'])
        h.step(300);self.assertTrue(h.read()['warmup.ready']);self.assertEqual(h.invoke('water_activity.measure')['quality'],'estimated')
    def test_continuous_and_cancel_residual(self):
        h=Aqualab4teRuntime();ready(h);h.invoke('method.configure',**dict(CAPS['method.configure'][6],mode='continuous'));h.invoke('acquisition.start');h.step(400)
        self.assertTrue(h.stable);self.assertEqual(h.phase,'running');h.invoke('acquisition.stop');v=h.vapor;h.step(10);self.assertEqual(h.phase,'cancelled');self.assertNotEqual(v,h.vapor);self.assertAlmostEqual(ledger(h),0.,14)
    def test_calibration_and_isotherm(self):
        h=Aqualab4teRuntime();ready(h);h.invoke('calibration.configure',offset_aw=.001,source='synthetic test');h.invoke('isotherm.configure',**dict(CAPS['isotherm.configure'][6],matrix_id='salt'));h.step(900);h.invoke('acquisition.start');h.step(600)
        r=h.invoke('calibration.verify',reference_aw=h.sample['reference']['value']);self.assertTrue(r['within_vendor_tolerance']);m=h.invoke('water_activity.measure');self.assertIsNotNone(m['moisture_dry_basis']);self.assertEqual(m['moisture_evidence'],'user_supplied_isotherm')
        h.invoke('isotherm.configure',**CAPS['isotherm.configure'][6]);self.assertIsNone(h.invoke('water_activity.measure')['moisture_dry_basis']);h.invoke('power.set',enabled=False)
        with self.assertRaises(ActionRejected):h.invoke('calibration.verify',reference_aw=.9)
    def test_volatile_is_flagged_not_compensated(self):
        h=Aqualab4teRuntime();ready(h,volatile_interference=True);h.step(900);h.invoke('acquisition.start');h.step(600);self.assertEqual(h.invoke('water_activity.measure')['quality'],'suspect_volatile')
    def test_mode_limits_and_invalid_dt(self):
        h=Aqualab4teRuntime()
        for args in [dict(CAPS['method.configure'][6],mode='custom'),dict(CAPS['method.configure'][6],mode='low_emitting',stability_delta=.02),dict(CAPS['method.configure'][6],consecutive_readings=1.2)]:
            with self.assertRaises(ActionRejected):h.invoke('method.configure',**args)
        for dt in [0,-1,True,float('inf'),float('nan')]:
            with self.assertRaises(ValueError):h.step(dt)
    def test_async_identity_permissions_retry(self):
        h=Aqualab4teRuntime();ready(h);s=h.session();h.register_principal('other','agent');h.register_principal('eval','evaluator')
        a=dict(instance_id=h.instance_id,capability_id='acquisition.start',arguments={},request_id='one');r=s.invoke(**a);self.assertEqual(r['status'],'accepted');self.assertEqual(s.invoke(**a),r);h.step(20)
        self.assertEqual(s.poll(r['operation_id'])['status'],'running')
        with self.assertRaises(ActionRejected):h.session('other').poll(r['operation_id'])
        self.assertEqual(s.cancel(r['operation_id'],'two')['status'],'cancelled');self.assertEqual(s.invoke(**dict(a,capability_id='acquisition.stop'))['status'],'rejected')
        self.assertNotIn('water.sample_kg',s.read(instance_id=h.instance_id)['observations']);self.assertNotIn('sample.configure',s.describe(instance_id=h.instance_id)['capabilities'])
        with self.assertRaises(ActionRejected):s.read(instance_id=h.instance_id,state_ids=['water.sample_kg'])
        self.assertEqual(s.invoke(instance_id=h.instance_id,capability_id='sample.configure',arguments=CAPS['sample.configure'][6],request_id='private')['status'],'rejected')
    def test_instance_isolation(self):
        a=Aqualab4teRuntime('a');b=Aqualab4teRuntime('b');old=copy.deepcopy(b.__dict__);ready(a);a.invoke('acquisition.start');a.step(350);self.assertEqual(old,b.__dict__)
    def test_quarantine_and_off_reference(self):
        h=Aqualab4teRuntime()
        for sid,r in h.templates.items():
            if not r['runtime_eligible']:
                with self.assertRaises(ActionRejected):configure(h,sid)
        h=Aqualab4teRuntime();ready(h);h.invoke('thermal.set_temperature',temperature_k=318.15)
        with self.assertRaises(ActionRejected):h.invoke('acquisition.start')
    def test_every_capability_and_schema(self):
        h=Aqualab4teRuntime();seen=set()
        def call(cap,**args):
            result=h.invoke(cap,**args);self.assertEqual(asset_tool.validate_value(result,CAPS[cap][1]),[],cap);seen.add(cap)
        for cap in ['power.set','thermal.set_temperature','lid.set_angle','sample.configure','method.configure','calibration.configure','isotherm.configure']:call(cap,**CAPS[cap][6])
        call('lid.set_angle',angle_rad=0.);call('latch.set_angle',angle_rad=math.pi/2);h.step(900);call('acquisition.start');h.step(900);call('water_activity.measure');call('calibration.verify',reference_aw=.92);call('acquisition.stop');call('latch.set_angle',angle_rad=0.);call('lid.set_angle',angle_rad=1.5);call('sample.unload')
        self.assertEqual(seen,set(CAPS))
        for k,v in h.read('evaluator').items():self.assertEqual(asset_tool.validate_value(v,SLOTS[k][0]),[],k)
        for c in export_aqualab_4te_contract_metadata()['capabilities'].values():self.assertEqual(asset_tool.lint_schema(c['input_schema']),[]);self.assertEqual(asset_tool.lint_schema(c['output_schema']),[])
if __name__=='__main__':unittest.main()
