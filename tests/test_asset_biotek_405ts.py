"""tests for CHEM-163 biotek_405ts: python3 -m unittest tests/test_asset_biotek_405ts.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances are
isolated, agent/evaluator truth isolation, conservation (liquid volume: bottles -> dispensed -> wells / waste; electrical energy), real return values
validated against the declared schemas (asset_tool validate_value), asynchronous completion and cancellation remnants, host-step independence,
declared immediate and on_step effects over scenario states; plus the washer physics against the manufacturer statements: residual <= 2 uL/well after
a 3-cycle 300 uL wash with 0.1 % Tween 20 water on a Costar flat plate at the default 29 steps, about 100 uL at 50 steps, dispense precision <= 3 % CV
at 300 uL, 3 cycles of 300 uL within 30 s, a 300 mL prime giving >= 95 % buffer purity; the cell-wash option (rates 1-2, CW travel) protecting an
adherent monolayer, vacuum-skimmed overfill vs overflow cross-contamination, clogged tubes (flow sensor, full wells), sensors (waste, vacuum, fluid),
idle-manifold clogging with a salt buffer vs a water rinse, liquid properties changing the wall film, carrier home for soaks longer than 1 min.
"""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import biotek_405ts as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
INST = 'washer_01'


def _meta():
    if not _META:
        _META.update(rt.export_biotek_405ts_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.Biotek405tsRuntime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema and immediate state effects (a call does not advance time); every
    rejection code is recorded for the declaration check."""
    before = self.read('evaluator') if capability_id != 'sim.step' else None
    t0 = self.t
    try:
        out = _orig_invoke(self, capability_id, **a)
    except rt.ActionRejected as exc:
        REJECTIONS.setdefault(capability_id, set()).add(str(exc))
        raise
    meta = _meta()['capabilities'][capability_id]
    errs = asset_tool.validate_value(out, meta['output_schema'])
    if errs:
        raise AssertionError(f'{capability_id} output violates its schema: {errs[:3]}')
    if before is not None:
        assert self.t == t0, capability_id
        after = self.read('evaluator')
        undeclared = {k for k in after if _changed(before[k], after[k])} - set(rt.EFFECTS.get(capability_id, []))
        if undeclared:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.Biotek405tsRuntime.invoke = _instrumented_invoke
TWEEN = 'water_0_1pct_tween20'
ELISA = dict(rt.PLATE_EXAMPLE)
TEST_PLATE = dict(ELISA, plate_id='test_plate', liquid_id=TWEEN, fill_volume_m3=300e-9, unbound_mol=1e-12)
CELL_PLATE = dict(ELISA, plate_id='cell_plate', liquid_id='dpbs_ca_mg', fill_volume_m3=100e-9, unbound_mol=0.0, nonspecific_mol=0.0, specific_mol=0.0,
                  cells_per_well=5e4, cell_adhesion='weak')
CW = dict(rt.WASH_EXAMPLE, dispense_rate=1, travel_rate='6CW', aspirate_z_steps=50, dispense_volume_m3=200e-9)


def configured(seed=1, **plant):
    h = rt.Biotek405tsRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def ready(seed=1, liquid=TWEEN, prime=True, **plant):
    h = configured(seed, **plant)
    h.invoke('power.set', enabled=True)
    h.invoke('buffer.load', bottle='A', liquid_id=liquid, volume_m3=0.008)
    if prime:
        h.invoke('prime.run', buffer='A', volume_m3=300e-6, rate=9)
        run(h)
    return h


def loaded(plate=TEST_PLATE, seed=1, **kw):
    h = ready(seed, **kw)
    h.invoke('plate.provide', **plate)
    h.invoke('plate.load', plate_id=plate['plate_id'])
    return h


def run(h, dt=0.25, tmax=36000.0):
    t0 = h.t
    while h.op is not None and h.t - t0 < tmax:
        h.step(dt)
    return h


def wash(h, **proto):
    oid = h.invoke('wash.run', protocol=dict(rt.WASH_EXAMPLE, **proto))['operation_id']
    t0 = h.t
    run(h)
    return h.records[h.operations[oid]['record_id']], h.t - t0


def check(cid, **detail):
    CHECKS.append(dict(check=cid, status='passed', **detail))


class ContractAndLibraries(unittest.TestCase):
    def test_module_identity_and_export(self):
        m = _meta()
        self.assertEqual((rt.ASSET_TYPE_ID, rt.CATALOG_ID), ('instrument.biotek_405ts', 'CHEM-163'))
        self.assertEqual(set(m['capabilities']), set(rt.CAPS))
        self.assertEqual(set(m['state_slots']), set(rt.SLOTS))
        for k, c in m['capabilities'].items():
            self.assertTrue(c['errors'], k)
            self.assertEqual(asset_tool.validate_value(c['example_arguments'], c['input_schema']), [], k)
        for k, v in list(rt.EFFECTS.items()) + list(rt.ON_STEP.items()):
            self.assertTrue(set(v) <= set(rt.SLOTS), k)
        check('contract_export_consistent')

    def test_template_libraries(self):
        lib = rt.liquid_lib()
        self.assertGreaterEqual(lib['count'], 50)
        rho, mu, sig = rt.liquid_props('water', 298.15)
        self.assertAlmostEqual(rho, 997.05, delta=1.0)
        self.assertAlmostEqual(mu, 0.890e-3, delta=0.02e-3)
        self.assertAlmostEqual(sig, 0.0720, delta=0.0015)
        self.assertAlmostEqual(rt.liquid_props('pbs_1x', 298.15)[0], 1004.5, delta=1.5)          # 1x PBS density
        pl = rt.plate_lib()['plates']
        self.assertAlmostEqual(pl['costar_3590']['well_bottom_elevation_m'], 0.01435 - 0.01067, delta=1e-9)
        self.assertAlmostEqual(pl['costar_3590']['well_bottom_elevation_m'], rt.ASP_Z_DEFAULT * rt.Z_STEP_M, delta=5e-5)   # the default aspirate height meets the bottom
        self.assertFalse(pl['costar_3575']['compatible_with_96_tube_single_manifold'])
        check('template_libraries', water=(rho, mu, sig))


class ManufacturerStatements(unittest.TestCase):
    def test_residual_volume_default_and_cell_wash_height(self):
        h = loaded()
        rec, dt = wash(h)
        V = np.array(h.plates['test_plate']['V'])
        self.assertEqual(rec['status'], 'completed')
        self.assertLessEqual(V.mean(), rt.RESIDUAL_SPEC_M3)                   # <= 2 uL/well average after 3 cycles (guide p.105)
        self.assertGreater(V.mean(), 0.5e-9)
        self.assertLessEqual(dt, 30.0)                                        # 96-well 300 uL three cycles <= 30 s (workbook)
        g = loaded()
        wash(g, aspirate_z_steps=50, dispense_volume_m3=200e-9)
        V50 = np.array(g.plates['test_plate']['V']).mean()
        self.assertGreater(V50, 70e-9)
        self.assertLess(V50, 110e-9)                                          # guide: 50 steps -> approximately 100 uL
        check('residual_and_timing', residual_uL=V.mean() * 1e9, time_s=dt, residual_50_steps_uL=V50 * 1e9)

    def test_dispense_precision_and_1ul_steps(self):
        h = loaded(dict(TEST_PLATE, fill_volume_m3=0.0))
        h.invoke('plate.unload', plate_id='test_plate')
        h.invoke('plate.provide', **dict(TEST_PLATE, plate_id='empty', fill_volume_m3=0.0, unbound_mol=0.0))
        h.invoke('plate.load', plate_id='empty')
        oid = h.invoke('wash.run', protocol=dict(rt.WASH_EXAMPLE, cycles=1, final_aspirate=False, dispense_rate=6))['operation_id']
        run(h)
        V = np.array(h.plates['empty']['V'])
        res = V - 0.0
        cv = float(res.std() / res.mean())
        self.assertLess(cv, rt.CV_SPEC)                                       # <= 3 % CV at 300 uL/well, rate 6 (guide)
        self.assertGreater(cv, 0.005)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('wash.run', protocol=dict(rt.WASH_EXAMPLE, dispense_volume_m3=300.5e-9))
        check('dispense_precision', cv=cv)

    def test_prime_purity_and_buffer_switching(self):
        h = ready(liquid='pbs_1x', prime=False)
        h.invoke('prime.run', buffer='A', volume_m3=rt.PRIME_FOR_CHANGE_M3, rate=9)
        run(h)
        purity = h.line.get('pbs_1x', 0.0)
        self.assertGreaterEqual(purity, 0.95)                                 # 300 mL prime -> >= 95 % (guide p.38)
        h.invoke('buffer.load', bottle='B', liquid_id=TWEEN, volume_m3=0.004)
        h.invoke('prime.run', buffer='B', volume_m3=rt.MANIFOLD_PRIME_M3, rate=9)
        run(h)
        self.assertLess(h.line.get(TWEEN, 0.0), 0.7)                          # a 50 mL manifold prime does not change the buffer
        self.assertAlmostEqual(h.ledger()['primed_m3'], 350e-6, delta=1e-12)
        self.assertAlmostEqual(h.waste, 350e-6, delta=1e-12)
        check('prime_purity', purity=purity)


class WashPhysics(unittest.TestCase):
    def test_washing_removes_unbound_keeps_specific(self):
        h = loaded()
        p = h.plates['test_plate']
        n0, s0 = float(np.mean(p['unbound'])), float(np.mean(p['spec']))
        wash(h)
        self.assertLess(np.mean(p['unbound']) / n0, 2e-3)
        self.assertGreater(np.mean(p['spec']) / s0, 0.999)
        g = loaded()
        wash(g, cycles=1)
        self.assertGreater(np.mean(g.plates['test_plate']['unbound']), 3 * np.mean(p['unbound']))   # more cycles wash better (desorption floor)
        k = loaded()
        wash(k, shake_s=10.0, shake_intensity='fast')
        tot = lambda q: float(np.mean(q['unbound'] + q['ns']))                                    # shaking desorbs more of the non-specific load
        self.assertLess(tot(k.plates['test_plate']), tot(p))
        check('wash_efficiency', unbound_left=float(np.mean(p['unbound']) / n0))

    def test_surfactant_desorbs_nonspecific(self):
        a, b = loaded(liquid='pbs_1x'), loaded(liquid='pbst_0_05pct_tween20')
        wash(a, soak_s=30.0)
        wash(b, soak_s=30.0)
        self.assertLess(np.mean(b.plates['test_plate']['ns']), np.mean(a.plates['test_plate']['ns']))
        check('surfactant_desorption')

    def test_liquid_properties_change_the_residual(self):
        a, b = loaded(liquid='water'), loaded(liquid='glycerol')
        wash(a)
        wash(b)
        self.assertGreater(np.mean(b.plates['test_plate']['V']), 3 * np.mean(a.plates['test_plate']['V']))   # viscous film
        check('liquid_film', water_uL=float(np.mean(a.plates['test_plate']['V']) * 1e9), glycerol_uL=float(np.mean(b.plates['test_plate']['V']) * 1e9))

    def test_cell_wash_option_protects_the_monolayer(self):
        h = loaded(CELL_PLATE)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('wash.run', protocol=CW)                                 # cell-wash rates need the option
        wash(h)
        std_left = float(np.mean(h.plates['cell_plate']['cells']) / 5e4)
        g = loaded(CELL_PLATE, cell_wash_installed=True)
        rec, dt = wash(g, **{k: v for k, v in CW.items() if k != 'buffer'})
        cw_left = float(np.mean(g.plates['cell_plate']['cells']) / 5e4)
        self.assertLess(std_left, 0.3)
        self.assertGreater(cw_left, 0.9)
        self.assertGreater(np.mean(g.plates['cell_plate']['V']), 50e-9)       # the raised aspirate height keeps a liquid cover
        check('cell_wash', standard_left=std_left, cell_wash_left=cw_left, time_s=dt)

    def test_overfill_is_skimmed_but_spills_without_vacuum_skim(self):
        h = loaded()
        rec, _ = wash(h, dispense_volume_m3=1000e-9, cycles=1, final_aspirate=False)
        self.assertNotIn('overflow_observed', rec['warnings'])                # tips at 121 steps skim the liquid above the rim
        pt = rt.plate_lib()['plates']['costar_3590']
        A = math.pi * pt['mean_well_radius_m'] ** 2
        self.assertLessEqual(max(h.plates['test_plate']['V']), pt['well_volume_m3'] + A * rt.MENISCUS_M * 0.5 + 1e-12)   # the pinned meniscus up to the tip
        k = loaded()                                                          # tips 3 mm below the rim: the level stops at the tip (S1)
        z = int(round((pt['height_m'] - 3e-3) / rt.Z_STEP_M))
        rec, _ = wash(k, dispense_volume_m3=1000e-9, cycles=1, final_aspirate=False, dispense_z_steps=z)
        tipz = z * rt.Z_STEP_M
        self.assertLessEqual(max(k.plates['test_plate']['V'] - A * (tipz - k.plates['test_plate']['wbe'])), 1e-12)
        c = loaded()                                                          # a blocked aspirate tube cannot skim: its well runs over (S2)
        c.invoke('fault.inject', fault='aspirate_tube_clog', tube=40, active=True)
        rec, _ = wash(c, dispense_volume_m3=1000e-9, cycles=1, final_aspirate=False)
        self.assertIn('overflow_observed', rec['warnings'])
        self.assertGreater(c.ledger()['overflow_m3'], 0.0)
        g = loaded()
        rec, _ = wash(g, dispense_volume_m3=1000e-9, cycles=1, final_aspirate=False, dispense_z_steps=200, vacuum_delay_m3=10e-9)
        self.assertIn('overflow_observed', rec['warnings'])
        self.assertGreater(g.ledger()['overflow_m3'], 0.0)
        self.assertGreater(float(np.sum(g.plates['test_plate']['contamination'])), 0.0)
        check('overflow_and_skimming')

    def test_clogged_tubes_and_flow_sensor(self):
        h = loaded()
        h.invoke('fault.inject', fault='aspirate_tube_clog', tube=10, active=True)
        for t in range(12):
            h.invoke('fault.inject', fault='dispense_tube_clog', tube=20 + t, active=True)
        rec, _ = wash(h)
        V = h.plates['test_plate']['V']
        self.assertGreater(V[10], 200e-9)                                     # the clogged aspirate tube leaves its well full
        self.assertIn('FLOW_ERROR', rec['alarms'])                           # 12 blocked dispense tubes: > 10 % flow deficit
        self.assertFalse(h.flow_ok)
        oid = h.invoke('maintenance.clean_tubes')['operation_id']
        run(h, dt=10.0)
        self.assertEqual((h.clog_a, h.clog_d), (set(), {}))
        check('clogs_and_flow_sensor')

    def test_idle_clogging_depends_on_the_liquid_left_in_the_tubes(self):
        salt, water = ready(liquid='high_salt_1m_nacl'), ready(liquid='water')
        for h in (salt, water):
            h.step(3 * 86400.0)
        self.assertGreater(len(salt.clog_a) + len(salt.clog_d), 5)
        self.assertEqual(len(water.clog_a) + len(water.clog_d), 0)
        check('idle_clogging', salt_clogs=len(salt.clog_a) + len(salt.clog_d))

    def test_review_round1_physics(self):
        a, b = ready(liquid='high_salt_1m_nacl'), ready(liquid='high_salt_1m_nacl')    # idle clogging does not depend on the step size
        a.step(2 * 86400.0)
        for _ in range(48):
            b.step(3600.0)
        self.assertEqual((a.clog_a, a.clog_d), (b.clog_a, b.clog_d))
        h = loaded()                                                          # a bottle that runs dry delivers its rest and alarms
        h.bottles['A']['volume'] = 96 * 300e-9 * 0.5
        rec, _ = wash(h, cycles=1)
        self.assertIn('BUFFER_EMPTY', rec['alarms'])
        self.assertEqual(rec['status'], 'failed')
        self.assertAlmostEqual(h.bottles['A']['volume'], 0.0, delta=1e-15)
        self.assertAlmostEqual(h.ledger()['dispensed_m3'], 96 * 300e-9 * 0.5, delta=1e-12)
        w = loaded(waste_capacity_m3=0.004)                                   # the plate's liquid must fit into the waste bottles
        w.waste = 0.9 * 0.004 - 1e-6                                     # sensor not yet tripped, headroom 0.4 mL
        rec, _ = wash(w, cycles=1)
        self.assertIn('WASTE_FULL', rec['alarms'])
        self.assertLessEqual(w.waste, 0.004 + 1e-15)
        pt = rt.plate_lib()['plates']['costar_3590']                          # the aspirate delay drains the wall film
        r0 = rt.residual_volume(300e-9, 0.0, pt, 1000.0, 1e-3, 0.072, 4e-3)
        r1 = rt.residual_volume(300e-9, 0.0, pt, 1000.0, 1e-3, 0.072, 4e-3, 5.0)
        self.assertLess(r1, r0)
        s = h.read('evaluator')
        for k in ('sensor.vacuum_ok', 'sensor.waste_full', 'sensor.fluid_ok', 'sensor.flow_ok'):
            self.assertIs(type(s[k]), bool, k)
        check('review_round1_physics', residual_no_delay_uL=r0 * 1e9, residual_5s_delay_uL=r1 * 1e9)

    def test_review_round2_wash_volume_drips_and_homing(self):
        left = {}
        for v in (50e-9, 300e-9, 1000e-9):                                   # more buffer per cycle washes better, near the well-mixed limit
            h = loaded()
            n0 = float(np.sum(h.plates['test_plate']['unbound']))
            wash(h, dispense_volume_m3=v, cycles=1)
            left[v] = float(np.sum(h.plates['test_plate']['unbound'])) / n0
        self.assertGreater(left[50e-9], left[300e-9])
        self.assertGreater(left[300e-9], left[1000e-9])
        self.assertLess(left[300e-9], 0.05)
        h = loaded()                                                          # drips are finite and booked
        wash(h, cycles=1, final_aspirate=False)
        h.carrier = -rt.CARRIER_TRAVEL_M
        V0 = float(np.sum(h.plates['test_plate']['V']))
        h.step(60 * 86400.0)
        self.assertLessEqual(float(np.sum(h.plates['test_plate']['V'])) - V0, 96 * rt.DROPS_PER_TIP * rt.DROP_M3 + 1e-15)
        self.assertAlmostEqual(float(np.sum(h.plates['test_plate']['V'])) - V0, h.ledger()['dripped_m3'], delta=1e-15)
        g = loaded()                                                          # a vacuum fault homes both axes (booked)
        oid = g.invoke('wash.run', protocol=rt.WASH_EXAMPLE)['operation_id']
        g.step(5.0)
        g.invoke('fault.inject', fault='vacuum_leak', tube=0, active=True)
        run(g)
        self.assertEqual((g.carrier, g.manifold), (0.0, 0.0))
        self.assertNotIn('buffer.a.volume_m3', g.read('agent'))                    # the bottle level is host truth, not an instrument reading
        self.assertIn('buffer.a.volume_m3', g.read('evaluator'))
        check('review_round2', left_50uL=left[50e-9], left_300uL=left[300e-9], left_1000uL=left[1000e-9])

    def test_overflow_neighbours_get_ten_percent_of_what_ran_over(self):
        h = loaded()
        h.invoke('fault.inject', fault='aspirate_tube_clog', tube=40, active=True)
        p = h.plates['test_plate']
        p['ns'][:] = 0.0                                                         # soluble species only: no desorption during the dispense
        p['spec'][:] = 0.0
        n0 = float(p['unbound'][40] + p['contamination'][40])
        nb = [32, 48, 39, 41]
        c0 = float(sum(p['contamination'][j] for j in nb))
        wash(h, dispense_volume_m3=3000e-9, cycles=1, final_aspirate=False)
        lost = n0 - float(p['unbound'][40] + p['contamination'][40])
        gained = float(sum(p['contamination'][j] for j in nb)) - c0
        self.assertGreater(lost, 0.0)
        self.assertAlmostEqual(gained, 0.1 * lost, delta=1e-9 * lost)             # 10 % of what ran over the rim reaches the four neighbours
        check('overflow_neighbour_rule', lost_mol=lost, gained_mol=gained)

    def test_contract_review_manifold_stays_inside_the_joint(self):
        h = loaded()                                                          # the highest programmable heights keep the manifold at or below the park position
        oid = h.invoke('wash.run', protocol=dict(rt.WASH_EXAMPLE, cycles=1, dispense_z_steps=250, aspirate_z_steps=250, secondary_aspirate_z_steps=250))['operation_id']
        zmax = -1.0
        while h.op is not None:
            h.step(0.25)
            zmax = max(zmax, h.manifold)
        self.assertLessEqual(zmax, 1e-12)
        self.assertGreaterEqual(min(h.manifold, 0.0), -rt.PARK_TIP_M)
        check('manifold_inside_joint', z_max_m=zmax)

    def test_review_round3_overflow_drips_and_waste_reading(self):
        c = loaded()                                                          # drops onto a full well run over and are booked
        c.invoke('fault.inject', fault='aspirate_tube_clog', tube=40, active=True)
        wash(c, dispense_volume_m3=1000e-9, cycles=1, final_aspirate=False)
        c.carrier = -rt.CARRIER_TRAVEL_M
        o0, cont0 = c.ledger()['overflow_m3'], float(np.sum(c.plates['test_plate']['contamination']))
        c.step(30 * 86400.0)
        pt = rt.plate_lib()['plates']['costar_3590']
        cap = pt['well_volume_m3'] + math.pi * pt['mean_well_radius_m'] ** 2 * rt.MENISCUS_M * 0.5
        self.assertLessEqual(max(c.plates['test_plate']['V']), cap + 1e-15)
        self.assertGreater(c.ledger()['overflow_m3'], o0)
        d = loaded()                                                          # clean drops add no analyte to wells that do not run over
        wash(d, cycles=1)
        d.carrier = -rt.CARRIER_TRAVEL_M
        cd0, v0 = float(np.sum(d.plates['test_plate']['contamination'])), float(np.sum(d.plates['test_plate']['V']))
        d.step(30 * 86400.0)
        self.assertGreater(float(np.sum(d.plates['test_plate']['V'])), v0)
        self.assertAlmostEqual(float(np.sum(d.plates['test_plate']['contamination'])), cd0, delta=1e-30 + 1e-12 * cd0)
        big = {}
        for v in (1000e-9, 3000e-9):                                          # a larger skimmed overfill washes out more (continuous overflow)
            k = loaded()
            k.plates['test_plate']['ns'][:] = 0.0                            # soluble species only (no desorption floor)
            k.plates['test_plate']['spec'][:] = 0.0
            n0 = float(np.sum(k.plates['test_plate']['unbound']))
            wash(k, dispense_volume_m3=v, cycles=1)
            big[v] = float(np.sum(k.plates['test_plate']['unbound'])) / n0
        self.assertLess(big[3000e-9], 0.5 * big[1000e-9])
        w = loaded()
        wash(w)
        out = w.invoke('waste.empty')
        self.assertAlmostEqual(out['emptied_m3'] / 50e-6, round(out['emptied_m3'] / 50e-6), delta=1e-9)   # read on 50 mL graduations
        check('review_round3', skim_1000uL=big[1000e-9], skim_3000uL=big[3000e-9])

    def test_sensors_and_failures(self):
        h = loaded(waste_capacity_m3=0.004)
        h.waste = 0.0037
        with self.assertRaises(rt.ActionRejected):
            h.invoke('wash.run', protocol=rt.WASH_EXAMPLE)                    # waste sensor
        h.invoke('waste.empty')
        g = loaded()
        g.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, vacuum_pump=False))  # pump unplugged from the accessory outlet
        with self.assertRaises(rt.ActionRejected):
            g.invoke('wash.run', protocol=rt.WASH_EXAMPLE)                    # vacuum sensor
        k = loaded()
        oid = k.invoke('wash.run', protocol=rt.WASH_EXAMPLE)['operation_id']
        k.step(6.0)
        k.invoke('fault.inject', fault='vacuum_leak', tube=None, active=True)
        run(k)
        r = k.records[k.operations[oid]['record_id']]
        self.assertEqual(r['status'], 'failed')
        self.assertIn('VACUUM_LOW', r['alarms'])
        self.assertEqual(k.manifold, 0.0)
        m = loaded()
        m.bottles['A']['volume'] = 0.0
        with self.assertRaises(rt.ActionRejected):
            m.invoke('wash.run', protocol=rt.WASH_EXAMPLE)                    # fluid sensor
        check('sensors')

    def test_long_soak_sends_the_carrier_home(self):
        h = loaded()
        oid = h.invoke('wash.run', protocol=dict(rt.WASH_EXAMPLE, cycles=1, soak_s=120.0))['operation_id']
        seen = []
        while h.op is not None:
            h.step(0.5)
            seen.append((h.phase, h.carrier))
        self.assertIn(('soak', 0.0), seen)                                   # > 1 min: carrier home, no drips (guide p.46)
        g = loaded()
        g.invoke('wash.run', protocol=dict(rt.WASH_EXAMPLE, cycles=1, soak_s=30.0))
        s2 = []
        while g.op is not None:
            g.step(0.5)
            s2.append((g.phase, g.carrier))
        self.assertIn(('soak', -rt.CARRIER_TRAVEL_M), s2)
        check('carrier_home_for_long_soaks')


class HostClockConservationSessions(unittest.TestCase):
    def test_read_describe_pure_and_time(self):
        h = loaded()
        v0, t0 = h.read('evaluator'), h.t
        h.register_principal('ag', 'agent')
        h.session('ag').describe(instance_id=INST)
        h.session('ag').read(instance_id=INST)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True), json.dumps(v0, sort_keys=True))
        oid = h.invoke('wash.run', protocol=rt.WASH_EXAMPLE)['operation_id']
        self.assertEqual(h.t, t0)
        self.assertEqual(h.operations[oid]['phase'], 'running')
        h.step(0.5)
        self.assertEqual(h.carrier, -rt.CARRIER_TRAVEL_M)
        run(h)
        self.assertEqual(h.operations[oid]['phase'], 'completed')
        self.assertEqual((h.carrier, h.manifold), (0.0, 0.0))
        check('host_clock')

    def test_step_partition_independent(self):
        a, b = loaded(seed=3), loaded(seed=3)
        for h in (a, b):
            h.invoke('wash.run', protocol=rt.WASH_EXAMPLE)
        a.step(100.0)
        for _ in range(400):
            b.step(0.25)
        self.assertEqual(json.dumps(a.read('evaluator'), sort_keys=True, default=str), json.dumps(b.read('evaluator'), sort_keys=True, default=str))
        self.assertAlmostEqual(a.ledger()['electrical_energy_j'], b.ledger()['electrical_energy_j'], delta=1e-6)
        check('step_partition')

    def test_volume_and_energy_conservation(self):
        h = loaded()
        bottle0 = h.bottles['A']['volume']
        wells0 = float(np.sum(h.plates['test_plate']['V']))
        waste0 = h.waste
        wash(h)
        L = h.ledger()
        wells1 = float(np.sum(h.plates['test_plate']['V']))
        self.assertAlmostEqual(bottle0 - h.bottles['A']['volume'], L['dispensed_m3'] + L['dripped_m3'], delta=1e-12)     # drawn = delivered + drips
        self.assertAlmostEqual(wells0 + L['dispensed_m3'] + L['dripped_m3'] - (h.waste - waste0) - L['overflow_m3'], wells1, delta=1e-12)   # the prime went to the trough before
        self.assertAlmostEqual(L['aspirated_m3'] + L['primed_m3'], L['waste_added_m3'], delta=1e-12)
        e0 = h.ledger()['electrical_energy_j']
        h.step(100.0)
        self.assertAlmostEqual(h.ledger()['electrical_energy_j'] - e0, rt.IDLE_W * 100.0, delta=1e-6)
        h.invoke('power.set', enabled=False)
        e1 = h.ledger()['electrical_energy_j']
        h.step(50.0)
        self.assertEqual(h.ledger()['electrical_energy_j'], e1)
        check('conservation')

    def test_rejections_leave_state_unchanged(self):
        h = loaded()
        v0 = json.dumps(h.read('evaluator'), sort_keys=True, default=str)
        for cap, args in [('plate.load', dict(plate_id='test_plate')), ('plate.provide', dict(TEST_PLATE, plate_type='costar_3575', plate_id='x')),
                          ('prime.run', dict(buffer='A', volume_m3=50e-6, rate=9)), ('wash.run', dict(protocol=dict(rt.WASH_EXAMPLE, buffer='C'))),
                          ('buffer.load', dict(bottle='A', liquid_id='nope', volume_m3=0.001)), ('plate.unload', dict(plate_id='nope'))]:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(cap, **args)
        self.assertEqual(json.dumps(h.read('evaluator'), sort_keys=True, default=str), v0)
        check('rejections_pure')

    def test_cancellation(self):
        h = loaded()
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        r = s.invoke(instance_id=INST, capability_id='wash.run', arguments=dict(protocol=rt.WASH_EXAMPLE), request_id='w')
        h.step(12.0)
        done = h.records['WS0001']['cycles_done']
        s.cancel(r['operation_id'], 'c1')
        self.assertEqual(h.records['WS0001']['status'], 'cancelled')
        self.assertEqual((h.carrier, h.manifold, h.op), (0.0, 0.0, None))
        self.assertGreaterEqual(done, 1)
        self.assertEqual(s.poll(r['operation_id'])['status'], 'cancelled')
        check('cancellation')

    def test_two_instances_isolated(self):
        a, b = loaded(), loaded()
        wash(a)
        self.assertEqual(sorted(b.records), ['PR0001'])
        self.assertEqual(b.ledger()['cycles'], 0.0)
        check('isolation')

    def test_agent_truth_isolation_and_idempotence(self):
        h = loaded()
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        h.register_principal('ev', 'evaluator')
        s1, s2, e = h.session('a1'), h.session('a2'), h.session('ev')
        obs = s1.read(instance_id=INST)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        self.assertIn('truth.residual_m3', e.read(instance_id=INST)['observations'])
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='plate.inspect', arguments=dict(plate_id='test_plate'), request_id='i')['error']['code'], 'ACCESS_DENIED')
        r1 = s1.invoke(instance_id=INST, capability_id='wash.run', arguments=dict(protocol=rt.WASH_EXAMPLE), request_id='w')
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='wash.run', arguments=dict(protocol=rt.WASH_EXAMPLE), request_id='w'), r1)
        self.assertEqual(s1.invoke(instance_id=INST, capability_id='waste.empty', arguments={}, request_id='w')['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='buffer.load', arguments=dict(bottle='B', liquid_id='water', volume_m3=0.001),
                                   request_id='b')['error']['code'], 'ACCESS_DENIED')
        run(h)
        self.assertEqual(s2.invoke(instance_id=INST, capability_id='records.list', arguments={}, request_id='r')['result']['records'], ['PR0001'])   # the host's own prime has no owner
        self.assertEqual(s1.poll(r1['operation_id'])['status'], 'succeeded')
        self.assertEqual(s1.read(instance_id=INST)['observations']['result.last_record_id']['value'], 'WS0001')
        check('truth_isolation_idempotence')

    def test_switch_record_ledger_and_clock(self):
        h = configured()
        self.assertTrue(h.invoke('power.press_switch')['enabled'])
        self.assertFalse(h.invoke('power.press_switch')['enabled'])
        g = loaded()
        rec, _ = wash(g, cycles=2)
        r = g.invoke('measurement.result', record_id=rec['record_id'])
        self.assertEqual((r['kind'], r['cycles_done'], r['status']), ('wash', 2, 'completed'))
        self.assertAlmostEqual(r['programmed_dispense_m3'], 2 * 96 * 300e-9, delta=1e-15)
        self.assertNotIn('_dispensed_m3', r)
        L = g.invoke('ledger.read')['ledger']
        self.assertEqual(L['cycles'], 2.0)
        t = g.invoke('sim.step', dt_s=12.5)['time_s']
        self.assertAlmostEqual(t, g.t, delta=0.0)
        check('switch_record_ledger_clock')

    def test_queries_and_unsupported(self):
        h = loaded()
        wash(h)
        self.assertEqual(h.invoke('records.list')['records'], ['PR0001', 'WS0001'])
        self.assertEqual(h.invoke('log.read')['entries'][-1]['cycles_done'], 3)
        self.assertTrue(h.invoke('events.read', since_s=0.0)['events'])
        p = h.invoke('provenance.read')
        self.assertEqual(len(p['libraries']), 2)
        self.assertGreaterEqual(p['libraries'][0]['count'], 50)
        ins = h.invoke('plate.inspect', plate_id='test_plate')
        self.assertEqual(len(ins['volume_m3']), 96)
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(k)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('measurement.result', record_id='nope')
        check('queries')


# ---------------------------------------------------------------- declared effects over scenario states
_SC = {}


def _build(name):
    if name == 'fresh':
        return rt.Biotek405tsRuntime()
    if name == 'off':
        return configured()
    if name == 'ready':
        h = ready()
        h.invoke('plate.provide', **TEST_PLATE)
        h.invoke('plate.provide', **CELL_PLATE)
        return h
    if name == 'loaded':
        h = _scenario('ready')
        h.invoke('plate.load', plate_id='test_plate')
        return h
    if name == 'washing':
        h = _scenario('loaded')
        h.invoke('wash.run', protocol=rt.WASH_EXAMPLE)
        h.step(5.0)
        return h
    if name == 'priming':
        h = _scenario('ready')
        h.invoke('prime.run', buffer='A', volume_m3=50e-6, rate=9)
        h.step(0.2)
        return h
    if name == 'cleaning':
        h = _scenario('ready')
        h.invoke('maintenance.clean_tubes')
        return h
    if name == 'washed':
        h = _scenario('loaded')
        wash(h)
        return h
    if name == 'vacuum_fault':
        h = _scenario('loaded')
        h.invoke('fault.inject', fault='vacuum_leak', tube=None, active=True)
        return h
    if name == 'mains_fault':
        h = _scenario('loaded')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'ready', 'loaded', 'washing', 'priming', 'cleaning', 'washed', 'vacuum_fault', 'mains_fault']
STEP_DTS = (0.02, 1.0, 5.0, 60.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0), dict(rt.PLANT_EXAMPLE, vacuum_pump=False)],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_switch': [{}],
        'buffer.load': [dict(bottle='B', liquid_id='water', volume_m3=0.002), dict(bottle='A', liquid_id='pbs_1x', volume_m3=0.004)], 'waste.empty': [{}],
        'plate.provide': [dict(TEST_PLATE, plate_id='new')], 'plate.load': [dict(plate_id='test_plate'), dict(plate_id='cell_plate')],
        'plate.unload': [dict(plate_id='test_plate')], 'prime.run': [dict(buffer='A', volume_m3=20e-6, rate=9)],
        'wash.run': [dict(protocol=rt.WASH_EXAMPLE), dict(protocol=dict(rt.WASH_EXAMPLE, cycles=1, soak_s=5.0, shake_s=3.0, bottom_wash=True))],
        'maintenance.clean_tubes': [{}],
        'fault.inject': [dict(fault=f, tube=3, active=True) for f in ('vacuum_leak', 'buffer_line_air', 'dispense_tube_clog', 'aspirate_tube_clog', 'waste_sensor_stuck', 'power_loss')]
                        + [dict(fault=f, tube=3, active=False) for f in ('vacuum_leak', 'dispense_tube_clog')],
        'sim.step': [dict(dt_s=0.5), dict(dt_s=30.0)],
    }
    return V.get(cap, [])


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _flat(v, prefix=''):
    out = {}
    if isinstance(v, dict):
        for k, x in v.items():
            out.update(_flat(x, f'{prefix}.{k}'))
    elif isinstance(v, list) and len(v) <= 128:
        for i, x in enumerate(v):
            out.update(_flat(x, f'{prefix}[{i}]'))
    else:
        out[prefix] = json.dumps(v, sort_keys=True, default=str) if isinstance(v, list) else v
    return out


def _rel_changed(a, b, scale=None):
    if _num(a) and _num(b):
        return abs(a - b) > 1e-9 * (max(abs(a), abs(b)) if scale is None else scale) + 1e-300
    return _changed(a, b)


def _leaf_changed(a, b):
    fa, fb = _flat(a), _flat(b)
    return any(_rel_changed(fa.get(k, '<absent>'), fb.get(k, '<absent>')) for k in set(fa) | set(fb))


def _step_differs(v1, a2, v0, b2):
    f1, fa, f0, fb = _flat(v1), _flat(a2), _flat(v0), _flat(b2)
    for leaf in set(f1) | set(fa) | set(f0) | set(fb):
        x1, xa, x0, xb = (f.get(leaf, '<absent>') for f in (f1, fa, f0, fb))
        ca, cb = _rel_changed(x1, xa), _rel_changed(x0, xb)
        if not (ca or cb):
            continue
        if ca and cb and all(_num(x) for x in (x1, xa, x0, xb)) and not _rel_changed(xa - x1, xb - x0, max(abs(x1), abs(xa), abs(x0), abs(xb))):
            continue
        if ca and cb and (x1, xa) == (x0, xb):
            continue
        if ca and cb and x1 == x0 and not _rel_changed(xa, xb):
            continue
        return True
    return False


def _executable(cap):
    return cap not in rt.UNSUPPORTED and rt.CAPS[cap]['kind'] not in ('query', 'analysis')


class ZDeclaredEffects(unittest.TestCase):
    def test_immediate_and_on_step_effects_are_declared(self):
        missing_imm, missing_step = {}, {}
        states = {n: _scenario(n) for n in SCENARIOS}
        for cap in rt.CAPS:
            if not _executable(cap):
                continue
            for sname, S in states.items():
                for args in _variants(cap):
                    A = copy.deepcopy(S)
                    v0 = A.read('evaluator')
                    try:
                        _orig_invoke(A, cap, **args)
                    except rt.ActionRejected:
                        continue
                    v1 = A.read('evaluator')
                    if cap == 'sim.step':
                        for k in v0:
                            if _leaf_changed(v0[k], v1[k]) and k not in rt.ON_STEP['sim.step'] + rt.EFFECTS['sim.step']:
                                missing_step.setdefault('sim.step', set()).add((sname, k))
                        continue
                    for k in v0:
                        if _leaf_changed(v0[k], v1[k]) and k not in rt.EFFECTS.get(cap, []):
                            missing_imm.setdefault(cap, set()).add((sname, k))
                    for dt in STEP_DTS:
                        A2, B2 = copy.deepcopy(A), copy.deepcopy(S)
                        A2.step(dt)
                        B2.step(dt)
                        a2, b2 = A2.read('evaluator'), B2.read('evaluator')
                        for k in v0:
                            if k not in rt.ON_STEP.get(cap, []) and _step_differs(v1[k], a2[k], v0[k], b2[k]):
                                missing_step.setdefault(cap, set()).add((sname, k))
                            if k not in rt.ON_STEP['sim.step'] + ['clock.time_s'] and _leaf_changed(v0[k], b2[k]):
                                missing_step.setdefault('sim.step', set()).add((sname, k))
        self.assertEqual(missing_imm, {})
        self.assertEqual(missing_step, {})
        check('declared_effects_scenarios')

    def test_every_device_capability_is_accepted_in_some_scenario(self):
        accepted = set()
        for n in SCENARIOS:
            S = _scenario(n)
            for cap in rt.CAPS:
                if not _executable(cap) or cap in accepted:
                    continue
                for args in _variants(cap):
                    try:
                        _orig_invoke(copy.deepcopy(S), cap, **args)
                        accepted.add(cap)
                        break
                    except rt.ActionRejected:
                        pass
        self.assertEqual({c for c in rt.CAPS if _executable(c)} - accepted, set())


class ZZRejectionCodes(unittest.TestCase):
    def test_every_observed_rejection_code_is_declared(self):
        undeclared = {k: sorted(v - set(rt.CAP_ERRORS.get(k, [])) - {'INVALID_ARGUMENT', 'UNSUPPORTED', 'UNKNOWN_CAPABILITY'}) for k, v in REJECTIONS.items()}
        self.assertEqual({k: v for k, v in undeclared.items() if v}, {})


if __name__ == '__main__':
    unittest.main()
