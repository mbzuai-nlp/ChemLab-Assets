"""Independent process, observation, protocol and source-boundary regressions for CHEM-086."""
import copy,csv,io,json,math,re,sys,unittest,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from asset_runtime import milestone_dma80 as rt

def new(plant=None,method=None,library=None):
 r=rt.MilestoneDma80Runtime('dma80',library=library);r.invoke('plant.configure',**(rt.PLANT_EXAMPLE|(plant or {})))
 r.invoke('method.configure',**(rt.METHOD_EXAMPLE|(method or {})));r.invoke('dust_cover.set',opened=True);return r

def warm(r):
 r.invoke('dust_cover.set',opened=False);r.invoke('power.set',enabled=True);r.invoke('gas.set',enabled=True);r.step(.3)

def run(r,indices):
 r.invoke('sequence.start',slot_indices=indices)
 for _ in range(20000):
  r.step(.02)
  if r.phase!='running':return r.invoke('sequence.read')['completed_records']
 raise AssertionError('finite sequence failed to terminate')

def until(r,stage):
 for _ in range(10000):
  if r.stage==stage:return
  r.step(.01)
 raise AssertionError('stage not reached: '+stage)

def load(r,i,a):r.invoke('sample.load',**(a|dict(slot_index=i)))
def blank_ready(plant=None,method=None):
 r=new(plant,method);load(r,0,rt.BLANK_EXAMPLE);load(r,1,rt.SAMPLE_EXAMPLE);warm(r);run(r,[0]);return r

def calibrated():
 r=new();load(r,0,rt.BLANK_EXAMPLE)
 for i,m in enumerate([.0001,.0005,.001],1):load(r,i,rt.STANDARD_EXAMPLE|dict(mass_kg=m,volume_m3=m*.001))
 for i,m in [(4,.0001),(5,.00008),(6,.00015)]:load(r,i,rt.SAMPLE_EXAMPLE|dict(mass_kg=m))
 warm(r);run(r,[0,1,2,3]);r.invoke('calibration.fit',curve_id='observed',record_ids=['record_2','record_3','record_4']);r.invoke('calibration.select',curve_id='observed');return r

class Dma80Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.cal=calibrated()
 def reject(self,r,k,args,code=None):
  before=copy.deepcopy(r.__dict__);rng=copy.deepcopy(r.rng.bit_generator.state)
  with self.assertRaises(rt.ActionRejected) as e:r.invoke(k,**args)
  if code:self.assertEqual(str(e.exception),code)
  after=copy.deepcopy(r.__dict__);before.pop('rng');after.pop('rng');self.assertEqual(before,after);self.assertEqual(rng,r.rng.bit_generator.state)
 def ledger(self,r):
  x=r.invoke('product.read');self.assertLess(abs(x['mass_residual_kg']),1e-14);self.assertLess(abs(x['energy_residual_j']),1e-7)
  for key,res,tol in [('mercury_ledger_json','residual_kg',1e-20),('gas_ledger_json','residual_mol',1e-10),('volume_ledger_json','residual_m3',1e-15)]:self.assertLess(abs(json.loads(x[key])[res]),tol)
 def test_identity_and_source_units(self):
  self.assertEqual(rt.CATALOG_ID,'CHEM-086');self.assertEqual(rt.WAVELENGTH_M,253.65e-9);self.assertEqual(rt.METHOD_EXAMPLE['range_min_kg'],1e-14)
 def test_step_rejects_zero_negative_nan_bool(self):
  for v in [0,-1,math.nan,math.inf,True]:
   r=new();before=r.time_s
   with self.assertRaises(rt.ActionRejected):r.step(v)
   self.assertEqual(r.time_s,before)
 def test_initialization_only(self):
  r=new();self.reject(r,'plant.configure',rt.PLANT_EXAMPLE,'INITIALIZATION_ONLY')
 def test_reads_and_describe_pure(self):
  r=copy.deepcopy(self.cal);t=r.time_s;v=r.revision;state=copy.deepcopy(r.rng.bit_generator.state)
  for _ in range(4):r.read();r.describe();r.invoke('result.read');r.invoke('signal.read');r.invoke('sample.inventory')
  self.assertEqual((r.time_s,r.revision,r.rng.bit_generator.state),(t,v,state))
 def test_loading_uses_independent_dust_cover(self):
  r=new();self.assertEqual(r.cover,0);load(r,0,rt.SAMPLE_EXAMPLE);self.assertEqual(len(r.samples),1)
 def test_main_cover_open_requires_dust_closed(self):
  self.reject(new(),'cover.set',{'opened':True},'DUST_COVER_OPEN')
 def test_main_cover_is_cold_maintenance(self):
  r=new();warm(r);self.reject(r,'cover.set',{'opened':True},'COLD_MAINTENANCE_REQUIRED')
 def test_main_cover_does_not_expose_loading(self):
  r=new();r.invoke('dust_cover.set',opened=False);r.invoke('cover.set',opened=True);self.reject(r,'sample.load',rt.SAMPLE_EXAMPLE,'SAMPLE_ACCESS')
 def test_dust_cover_blocks_indexing_and_measurement(self):
  r=new();load(r,0,rt.BLANK_EXAMPLE);warm(r);r.invoke('dust_cover.set',opened=True)
  self.reject(r,'carousel.select',{'slot_index':5},'CAROUSEL_INTERLOCK');self.reject(r,'measurement.start',{'slot_index':0},'NOT_READY')
 def test_carousel_moving_blocks_access(self):
  r=new();warm(r);r.invoke('carousel.select',slot_index=39);r.step(.01);self.reject(r,'dust_cover.set',{'opened':True},'CAROUSEL_MOVING')
 def test_power_guard_before_mutation(self):
  r=new();r.invoke('dust_cover.set',opened=False);r.invoke('cover.set',opened=True);self.reject(r,'power.set',{'enabled':True},'COVER_OPEN')
 def test_liquid_requires_quartz_and_positive_volume(self):
  for a in [dict(boat_material='metal'),dict(volume_m3=0.)]:self.reject(new(),'sample.load',rt.STANDARD_EXAMPLE|a,'LIQUID_QUARTZ_BOAT_REQUIRED')
 def test_nonliquid_has_no_fictitious_volume(self):self.reject(new(),'sample.load',rt.SAMPLE_EXAMPLE|dict(volume_m3=1e-7),'VOLUME_IS_LIQUID_ONLY')
 def test_mass_and_liquid_volume_limits(self):
  for a in [rt.SAMPLE_EXAMPLE|dict(mass_kg=.0015001),rt.STANDARD_EXAMPLE|dict(volume_m3=1.5001e-6)]:self.reject(new(),'sample.load',a,'INVALID_ARGUMENT')
 def test_unknown_density_is_not_inferred(self):
  r=new();a=rt.STANDARD_EXAMPLE|dict(volume_m3=1.4e-6);load(r,0,a);self.assertAlmostEqual(r.hg_registered,1.013e-11,23);self.assertEqual(r.vol_registered,1.4e-6)
 def test_dry_mass_requires_declared_fraction(self):
  lib={'compounds':{'dry':dict(physical_input_eligible=True,input_form='solid',calibration_eligible=False,mercury_mass_fraction_kg_kg=1e-6,conditions={'mass_basis':'dry_mass'})}}
  r=new(library=lib);a=rt.SAMPLE_EXAMPLE|dict(template_id='dry');self.reject(r,'sample.load',a,'DRY_MASS_FRACTION_REQUIRED');load(r,0,a|dict(dry_mass_fraction=.6));self.assertAlmostEqual(r.hg_registered,6e-11,22)
 def test_source_conditions_explicit_ack(self):self.reject(new(),'sample.load',rt.SAMPLE_EXAMPLE|dict(accept_source_condition_mismatch=False),'SOURCE_CONDITION_ACK_REQUIRED')
 def test_source_form_not_interchangeable(self):self.reject(new(),'sample.load',rt.SAMPLE_EXAMPLE|dict(input_form='liquid',boat_material='quartz',volume_m3=1e-7),'SOURCE_FORM_MISMATCH')
 def test_query_only_preserves_raw_record(self):
  record={'physical_input_eligible':False,'input_form':'solid','mercury_mass_kg':1e-11,'conditions':{'basis':'unknown'}};r=new(library={'compounds':{'q':record}})
  self.assertEqual(json.loads(r.invoke('template.read',substance_id='q')['template_json']),record);self.reject(r,'sample.load',rt.SAMPLE_EXAMPLE|dict(template_id='q'),'REFERENCE_QUERY_ONLY')
 def test_trap_requires_declared_option(self):
  a=rt.SAMPLE_EXAMPLE|dict(template_id='',input_form='collected_gas_sorbent',boat_material='collected_trap',host_mercury_mass_kg=1e-11);self.reject(new(),'sample.load',a,'COLLECTED_TRAP_OPTION_REQUIRED');r=new({'optional_collected_trap_installed':True});load(r,0,a);self.assertEqual(r.hg_registered,1e-11)
 def test_blank_cannot_smuggle_mercury(self):self.reject(new(),'sample.load',rt.BLANK_EXAMPLE|dict(host_mercury_mass_kg=1e-12),'BLANK_CONTENT')
 def test_element_mass_cannot_exceed_gross_mass(self):self.reject(new(),'sample.load',rt.SAMPLE_EXAMPLE|dict(template_id='',host_mercury_mass_kg=.001),'MERCURY_MASS_EXCEEDS_SAMPLE')
 def test_host_truth_not_a_source_standard(self):self.reject(new(),'sample.load',rt.STANDARD_EXAMPLE|dict(template_id='',host_mercury_mass_kg=1e-11),'SOURCE_STANDARD_REQUIRED')
 def test_all_40_independent_slots(self):
  r=new()
  for i in range(40):load(r,i,rt.BLANK_EXAMPLE)
  self.assertEqual(len(r.invoke('sample.inventory')['slots']),40);self.assertTrue(all(x['occupied'] for x in r.invoke('sample.inventory')['slots']));self.reject(r,'sample.load',rt.BLANK_EXAMPLE,'SLOT_OCCUPIED')
 def test_sequence_prevalidates_all_slots(self):
  r=blank_ready();self.reject(r,'sequence.start',{'slot_indices':[1,12]},'SAMPLE_MISSING_OR_PROCESSED');self.reject(r,'sequence.start',{'slot_indices':[1,1]},'DUPLICATE_SLOT')
 def test_blank_required_before_any_nonblank(self):
  r=new();load(r,0,rt.SAMPLE_EXAMPLE);warm(r);self.reject(r,'measurement.start',{'slot_index':0},'MATCHED_BLANK_REQUIRED')
 def test_full_40_slot_sequence(self):
  r=new()
  for i in range(40):load(r,i,rt.BLANK_EXAMPLE if i==0 else rt.SAMPLE_EXAMPLE|dict(template_id='',host_mercury_mass_kg=(i+1)*1e-12,mass_kg=1e-5))
  warm(r);results=run(r,list(range(40)));self.assertEqual([x['slot_index'] for x in results],list(range(40)));self.assertTrue(all(x['complete'] for x in results));self.assertAlmostEqual(r.carousel,39*math.pi/20);self.ledger(r)
 def test_carousel_target_finite_and_fourty_mapping(self):
  r=new();warm(r);r.invoke('carousel.select',slot_index=39);self.assertEqual(r.carousel,0);r.step(.01);self.assertGreater(r.carousel,0);self.assertLess(r.carousel,39*math.pi/20);r.step(1);self.assertAlmostEqual(r.carousel,39*math.pi/20)
 def test_lift_precedes_insertion_and_returns(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1);until(r,'lifting');r.step(.01);self.assertGreater(r.lift,0);self.assertLess(r.lift,.05);self.assertEqual(r.injection,0);until(r,'inserting');r.step(.01);self.assertEqual(r.lift,.05);self.assertGreater(r.injection,0);self.assertLess(r.injection,.23);until(r,'lowering');self.assertAlmostEqual(r.injection,0);r.step(.1);self.assertTrue(r._home())
 def test_mechanical_positions_bound_entire_assay(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1)
  while r.phase=='running':
   v=r.step(.02)
   for k in ['carousel.angle_rad','transfer.lift_m','injection.position_m']:rt.validate(rt.SLOTS[k][0],v[k])
 def test_temperature_changes_only_on_step(self):
  r=new();r.invoke('dust_cover.set',opened=False);r.invoke('power.set',enabled=True);self.assertEqual(r.temps['catalyst'],298.15);r.step(.01);self.assertGreater(r.temps['catalyst'],298.15);self.assertLess(r.temps['catalyst'],838.15)
 def test_window_duration_no_phantom_sample(self):
  windows=self.cal._records['record_2']['windows'];self.assertEqual(len(windows),16);self.assertAlmostEqual(sum(w['end_s']-w['start_s'] for w in windows),.8,11);self.assertTrue(all(abs(w['end_s']-w['start_s']-.05)<1e-10 for w in windows))
 def test_double_beam_log_relation(self):
  p=self.cal.plant
  for w in self.cal._records['record_2']['windows']:
   ratio=(w['sample_relative']-p['sample_dark_relative'])/(w['reference_relative']-p['reference_dark_relative'])/p['sample_beam_ratio'];self.assertAlmostEqual(w['transmission_ratio'],ratio,13);self.assertAlmostEqual(w['absorbance'],-math.log10(ratio),13)
 def test_integral_recomputed_from_actual_windows(self):
  for r in self.cal._records.values():self.assertAlmostEqual(r['integrated_absorbance_s'],sum(w['absorbance']*(w['end_s']-w['start_s']) for w in r['windows']),13)
 def test_blank_corrects_observed_baseline(self):
  self.assertAlmostEqual(self.cal._records['record_1']['integrated_absorbance_s'],.8*rt.PLANT_EXAMPLE['baseline_absorbance'],12)
 def test_fit_independent_lstsq(self):
  c=self.cal._curves['observed'];x=np.array([r['reference_mass_kg'] for r in c['observations']]);y=np.array([r['integrated_absorbance_s'] for r in c['observations']]);scale=x.max();co=np.linalg.lstsq(np.c_[x/scale,np.ones(3)],y,rcond=None)[0];self.assertAlmostEqual(c['slope_s_kg'],co[0]/scale,5);self.assertAlmostEqual(c['intercept_s'],co[1],15)
 def test_real_source_sample_is_observationally_recovered(self):
  r=copy.deepcopy(self.cal);out=run(r,[4])[0];expected=7.23e-11;self.assertLess(abs(out['mercury_mass_kg']/expected-1),.001);self.assertEqual(out['mercury_concentration_kg_m3'],None);self.assertAlmostEqual(out['mercury_mass_fraction_kg_kg'],out['mercury_mass_kg']/.0001);self.ledger(r)
 def test_no_calibration_keeps_signal_and_null(self):
  r=blank_ready();out=run(r,[1])[0];self.assertIsNone(out['mercury_mass_kg']);self.assertGreater(out['blank_corrected_integral_s'],0)
 def test_exact_upper_outside_calibration_is_null(self):
  r=copy.deepcopy(self.cal);out=run(r,[6])[0];self.assertIsNone(out['mercury_mass_kg']);self.assertTrue(any('interpolation' in q for q in out['quality']))
 def test_fit_cannot_accept_fabricated_observations(self):self.reject(copy.deepcopy(self.cal),'calibration.fit',{'curve_id':'x','record_ids':['record_1','record_2','record_3']},'ACTUAL_STANDARD_REQUIRED')
 def test_duplicate_standard_rejected(self):self.reject(copy.deepcopy(self.cal),'calibration.fit',{'curve_id':'x','record_ids':['record_2']*3},'DUPLICATE_STANDARD')
 def test_method_change_invalidates_both_blank_and_curve(self):
  r=copy.deepcopy(self.cal);old=r.invoke('result.read');r.invoke('method.configure',**(r.method|dict(window_s=.04)));self.assertFalse(r.read()['blank.valid']);self.assertFalse(r.read()['calibration.ready']);self.assertEqual(r.invoke('result.read'),old);self.reject(r,'calibration.select',{'curve_id':'observed'},'CALIBRATION_SIGNATURE_MISMATCH')
 def test_historical_record_preserves_method(self):
  r=copy.deepcopy(self.cal);old=r.invoke('records.read',record_id='record_2');r.invoke('method.configure',**(r.method|dict(record_s=1.)));self.assertEqual(r.invoke('records.read',record_id='record_2'),old)
 def test_response_signature_excludes_inventory_and_seed_only(self):
  a=new();b=new({'seed':7,'oxygen_supply_mol':2.});c=new({'sample_beam_ratio':.8});self.assertEqual(a._key(),b._key());self.assertNotEqual(a._key(),c._key())
 def test_cancel_partial_window_preserves_consumption(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1);until(r,'recording');r.step(.073);r.invoke('measurement.stop');out=r.invoke('result.read');self.assertEqual(out['status'],'cancelled');self.assertFalse(out['complete']);self.assertIsNone(out['mercury_mass_kg']);self.assertAlmostEqual(sum(w['end_s']-w['start_s'] for w in out['windows']),.073,10);self.assertGreater(r.products,0);self.assertGreater(r.hg_gold+r.hg_cell+r.hg_exhaust,0);self.ledger(r)
 def test_cancelled_cell_keeps_flushing_with_actual_carrier(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1);until(r,'recording');r.step(.02);r.invoke('measurement.stop');cell=r.hg_cell;exhaust=r.hg_exhaust;history=r.invoke('result.read');self.assertGreater(cell,0);r.step(.04);self.assertLess(r.hg_cell,cell);self.assertGreater(r.hg_exhaust,exhaust);self.assertEqual(r.invoke('result.read'),history);self.ledger(r)
 def test_exhausted_supply_cannot_flush_cancelled_cell(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1);until(r,'recording');r.step(.02);r.invoke('measurement.stop');r.oxygen_exhaust+=r.oxygen_remaining;r.oxygen_remaining=0.;cell=r.hg_cell;r.step(.04);self.assertEqual(r.hg_cell,cell);self.assertFalse(r.read()['instrument.ready']);self.ledger(r)
 def test_no_carrier_retains_cancelled_cell_inventory(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1);until(r,'recording');r.step(.02);r.invoke('gas.set',enabled=False);cell=r.hg_cell;r.step(.04);self.assertEqual(r.hg_cell,cell);self.ledger(r)
 def test_stop_does_not_restore_sample(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1);until(r,'decomposing');r.step(.2);before=r.samples[1]['remaining'];r.invoke('measurement.stop');self.assertEqual(r.samples[1]['remaining'],before);self.assertGreater(before,0);self.assertLess(before,.0001);self.ledger(r)
 def test_cancelled_transfer_returns_only_with_power(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1);until(r,'inserting');r.step(.1);r.invoke('power.set',enabled=False);q=r.injection;r.step(.1);self.assertEqual(r.injection,q);r.invoke('power.set',enabled=True);r.step(.4);self.assertTrue(r._home())
 def test_gas_loss_cancels_no_reset(self):
  r=blank_ready();r.invoke('measurement.start',slot_index=1);until(r,'decomposing');r.step(.1);r.invoke('gas.set',enabled=False);self.assertEqual(r.phase,'cancelled');self.assertGreater(r.products,0);self.ledger(r)
 def test_oxygen_inventory_depletion_fails_conservatively(self):
  r=new({'oxygen_supply_mol':4e-5});load(r,0,rt.BLANK_EXAMPLE);warm(r);run(r,[0]);self.assertEqual(r.phase,'failed');self.ledger(r)
 def test_oxygen_demand_cannot_borrow_previously_exhausted_carrier(self):
  r=new({'oxygen_demand_mol_kg':100.});load(r,0,rt.BLANK_EXAMPLE|dict(mass_kg=1e-9));load(r,1,rt.SAMPLE_EXAMPLE);warm(r);run(r,[0]);r.invoke('measurement.start',slot_index=1);until(r,'decomposing');r.step(.02);self.assertEqual(r.phase,'failed');self.ledger(r)
 def test_zero_reference_beam_protects_log(self):
  r=copy.deepcopy(self.cal);r._pending_dt=.02;r._pending_r=0;r._pending_s=.01;r._pending_end=r.time_s;r._window();self.assertIsNone(r._last_point['absorbance']);self.assertTrue(r._last_point['saturated'])
 def test_common_lamp_drift_cancels_blank(self):
  r=blank_ready({'common_drift_fraction':.7});self.assertAlmostEqual(r.area,.8*r.plant['baseline_absorbance'],12)
 def test_positive_noise_seed_and_query_independence(self):
  r=blank_ready({'beam_noise_density_sqrt_s':1e-5});a=copy.deepcopy(r);b=copy.deepcopy(r);a.invoke('measurement.start',slot_index=1);b.invoke('measurement.start',slot_index=1)
  while a.phase=='running':a.step(.02);b.read();b.invoke('signal.read');b.step(.02)
  self.assertEqual(a.windows,b.windows)
 def test_time_partition_matches_point_one_vs_point_zero_two(self):
  a=blank_ready();b=copy.deepcopy(a);a.invoke('measurement.start',slot_index=1);b.invoke('measurement.start',slot_index=1)
  for _ in range(40):
   a.step(.1)
   for _ in range(5):b.step(.02)
  self.assertEqual(a.phase,b.phase);self.assertAlmostEqual(a.area,b.area,12);self.assertAlmostEqual(a.hg_exhaust,b.hg_exhaust,22)
 def test_noninteger_release_boundary_preserved(self):
  r=blank_ready(method={'release_s':.505});r.invoke('measurement.start',slot_index=1);until(r,'recording');g=r.hg_gold;r.step(.505);self.assertAlmostEqual(r.hg_gold/g,math.exp(-.505/r.plant['release_tau_s']),7)
 def test_all_ledger_categories_after_unload_and_cleanup(self):
  r=copy.deepcopy(self.cal);run(r,[4]);r.invoke('power.set',enabled=False);r.invoke('gas.set',enabled=False);r.step(.5);r.invoke('dust_cover.set',opened=True);result=r.invoke('sample.unload',slot_index=4,recipient_id='waste');self.assertLess(abs(result['unburned_mass_kg']),1e-12);self.assertGreater(result['residue_mass_kg'],0);r.invoke('product.drain',recipient_id='trap_waste');self.ledger(r)
 def test_csv_json_actual_array_roundtrip(self):
  r=copy.deepcopy(self.cal);run(r,[4]);self.assertEqual(json.loads(r.invoke('result.export',format='json')['content']),r.invoke('result.read'));rows=list(csv.DictReader(io.StringIO(r.invoke('result.export',format='csv')['content'])));self.assertEqual(len(rows),len(r.windows));self.assertAlmostEqual(float(rows[3]['absorbance']),r.windows[3]['absorbance'])
 def test_every_unsupported_cap_explicitly_rejects(self):
  r=new()
  for cap in rt.UNSUPPORTED:self.reject(r,cap,{},'UNSUPPORTED')
 def test_acl_hides_sample_truth_and_host_loading(self):
  r=new();s=r.session();out=s.invoke(instance_id='dma80',capability_id='sample.load',arguments=rt.SAMPLE_EXAMPLE,request_id='x');self.assertEqual(out['error']['code'],'ACCESS_DENIED');self.assertNotIn('truth.ledger',s.read(instance_id='dma80')['observations']);self.assertNotIn('sample.load',s.describe(instance_id='dma80')['capabilities'])
 def test_unknown_instance_atomic(self):
  r=new();s=r.session();out=s.invoke(instance_id='other',capability_id='power.set',arguments={'enabled':True},request_id='x');self.assertEqual(out['error']['code'],'UNKNOWN_INSTANCE');self.assertFalse(r.powered)
 def test_request_dedup_and_conflict(self):
  r=new();s=r.session();args=dict(instance_id='dma80',capability_id='gas.set',arguments={'enabled':True},request_id='same');a=s.invoke(**args);revision=r.revision;r.step(.01);self.assertEqual(s.invoke(**args),a);self.assertEqual(r.revision,revision+1);self.assertEqual(s.invoke(**(args|dict(arguments={'enabled':False})))['error']['code'],'REQUEST_CONFLICT')
 def test_operation_history_owner_and_cancel_idempotence(self):
  r=copy.deepcopy(self.cal);s=r.session();response=s.invoke(instance_id='dma80',capability_id='measurement.start',arguments={'slot_index':4},request_id='a');op=response['operation_id'];r.step(.2);a=s.cancel(op,'stop');self.assertEqual(s.cancel(op,'stop'),a);old=s.poll(op);r.step(.5);s.invoke(instance_id='dma80',capability_id='measurement.start',arguments={'slot_index':5},request_id='b');self.assertEqual(s.poll(op),old);r.register_principal('other','agent')
  with self.assertRaises(rt.ActionRejected):r.session('other').poll(op)
 def test_completed_history_survives_record_removal(self):
  r=copy.deepcopy(self.cal);s=r.session();o=s.invoke(instance_id='dma80',capability_id='measurement.start',arguments={'slot_index':4},request_id='run')['operation_id'];until(r,'lowering');r.step(.1);history=s.poll(o);r.invoke('records.remove',record_id='record_5');self.assertEqual(s.poll(o),history)
 def test_sample_time_not_query_clock(self):
  r=copy.deepcopy(self.cal);s=r.session();before=s.read(instance_id='dma80',state_ids=['detector.absorbance']);r.step(.1);after=s.read(instance_id='dma80',state_ids=['detector.absorbance']);self.assertEqual(before['observations'],after['observations']);self.assertNotEqual(before['clock'],after['clock'])
 def test_instances_remain_isolated(self):
  a=copy.deepcopy(self.cal);b=copy.deepcopy(self.cal);before=b.read('evaluator');run(a,[4]);self.assertEqual(b.read('evaluator'),before)
 def test_state_and_output_schemas_are_real(self):
  r=copy.deepcopy(self.cal)
  for k,v in r.read('evaluator').items():rt.validate(rt.SLOTS[k][0],v)
  for name in ['process.measure','result.read','sequence.read','signal.read','blank.read','records.list','sample.inventory','product.read']:rt.validate(rt.CAPS[name][1],r.invoke(name))
 def test_metadata_matches_full_asset_schema(self):
  base=Path(__file__).resolve().parents[1].joinpath('skills/chemistry-lab-assets');spec=importlib.util.spec_from_file_location('dma_schema_tool',base/'scripts/asset_tool.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool);schema=json.loads((base/'assets/asset-contract.schema.json').read_text());meta=rt.export_milestone_dma80_contract_metadata()
  for group,name in [('capabilities','capability'),('state_slots','slot'),('mechanisms','mechanism')]:
   for k,v in meta[group].items():self.assertEqual(tool.validate_value(v,schema['$defs'][name],schema,path=k),[],k)
 def test_metadata_errors_legal_and_effects_defined(self):
  meta=rt.export_milestone_dma80_contract_metadata()
  for cap in meta['capabilities'].values():
   self.assertTrue(all(re.fullmatch('[A-Z][A-Z0-9_]*',x['code']) for x in cap['errors']));self.assertTrue(all(x['state_id'] in rt.SLOTS for x in cap['state_effects']))
   if cap['kind']=='query':self.assertEqual(cap['state_effects'],[])
 def test_authority_cannot_be_escalated_by_session_arguments(self):
  r=new()
  with self.assertRaises(rt.ActionRejected):r.session('unregistered')
  with self.assertRaises(rt.ActionRejected):r.register_principal('__public_agent__','evaluator')
 def test_method_rejection_is_atomic(self):self.reject(new(),'method.configure',rt.METHOD_EXAMPLE|dict(record_s=.1,release_s=.5),'INVALID_METHOD')
 def test_library_scalar_not_exposed_by_inventory(self):
  r=new();load(r,0,rt.SAMPLE_EXAMPLE);visible=json.dumps(r.invoke('sample.inventory'));self.assertNotIn('nist_2448',visible);self.assertNotIn('mercury',visible)

if __name__=='__main__':unittest.main()
