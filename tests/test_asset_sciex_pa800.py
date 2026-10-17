"""tests for CHEM-093 SCIEX PA 800 Plus capillary electrophoresis runtime: python3 -m unittest tests/test_asset_sciex_pa800.py

Topics: setpoints only write targets; read / describe / electropherogram.read are pure; rejected preconditions leave state unchanged; two
instances are isolated; agent / evaluator truth isolation; conservation of every species and of liquid volume; real return values validate
against the contract schemas; asynchronous run completion, cancellation, door interlock and faults; electrophoresis physics against
analytic expectations and literature values; the mobility template library (>= 50 real records, evidence, licence flags, CAS)."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import sciex_pa800 as rt  # noqa: E402
import asset_tool  # noqa: E402

PSI = rt.PSI
BORATE = {'boric_acid': 50.0, 'sodium_ion': 25.0}
MIX = {'benzoic_acid': 0.5, 'caffeine': 0.2, 'phthalic_acid': 0.2, 'salicylic_acid': 0.5}


class Bench:
    """Host + agent / evaluator sessions with request-id bookkeeping."""

    def __init__(self, iid='pa800_01'):
        self.h = rt.SciexPa800Runtime(iid)
        self.iid = iid
        self.h.register_principal('ev', 'evaluator')
        self.ev, self.ag = self.h.session('ev'), self.h.session()
        self.n = 0

    def call(self, cap, args=None, who='ag', expect=('succeeded', 'accepted')):
        self.n += 1
        s = self.ev if who == 'ev' else self.ag
        r = s.invoke(instance_id=self.iid, capability_id=cap, arguments=args or {}, request_id=f'q{self.n}')
        if expect is not None and r['status'] not in expect:
            raise AssertionError(f'{cap}: {r["status"]} {r["error"]}')
        return r

    def ready(self, coating='bare_fused_silica', ld=0.5, idm=5e-5, bge=BORATE, sample=MIX, warm=True, outlet_volume=1.0e-6, wavelength=254e-9):
        c = self.call
        c('power.set', {'enabled': True})
        c('lamp.set', {'on': True})
        c('detector.configure', {'module': 'pda', 'mode': 'direct_uv', 'wavelength_m': wavelength, 'data_rate_hz': 4.0})
        c('sample_cover.set_position', {'angle_rad': math.pi / 2})
        c('cartridge_cover.set_position', {'angle_rad': 1.4})
        c('insertion_bar.set_position', {'angle_rad': 1.0})
        c('cartridge.install', {'inner_diameter_m': idm, 'length_to_detector_m': ld, 'coating': coating})
        c('insertion_bar.set_position', {'angle_rad': 0.0})
        c('cartridge_cover.set_position', {'angle_rad': 0.0})
        for pos in ('BI:A1', 'BI:B1'):
            c('vial.place', {'position': pos, 'volume_m3': 1.4e-6, 'composition_mol_m3': bge, 'label': 'BGE'}, who='ev')
        for pos in ('BO:A1', 'BO:B1'):
            c('vial.place', {'position': pos, 'volume_m3': outlet_volume, 'composition_mol_m3': bge, 'label': 'BGE out'}, who='ev')
        if sample is not None:
            c('vial.place', {'position': 'SI:A1', 'volume_m3': 2e-7, 'composition_mol_m3': sample, 'label': 'sample'}, who='ev')
        c('sample_cover.set_position', {'angle_rad': 0.0})
        if warm:
            self.h.step(300.0)

    def method(self, voltage=25000.0, sep_s=600.0, rinse_s=120.0, inj=('pressure', 0.5 * PSI, 5.0), limit=3e-4, control='voltage', current=None,
               sep_pressure=None):
        sep = {'type': 'separate', 'inlet': 'BI:B1', 'outlet': 'BO:B1', 'control': control, 'voltage_v': voltage, 'ramp_s': 10.0, 'duration_s': sep_s}
        if sep_pressure is not None:
            sep['pressure_pa'] = sep_pressure
        if control == 'voltage':
            sep['current_limit_a'] = limit
        else:
            sep['current_a'] = current
        injs = {'type': 'inject', 'injection_mode': inj[0], 'inlet': 'SI:A1', 'outlet': 'BO:B1', 'duration_s': inj[2]}
        injs['pressure_pa' if inj[0] == 'pressure' else 'voltage_v'] = inj[1]
        steps = [{'type': 'rinse', 'inlet': 'BI:A1', 'outlet': 'BO:A1', 'pressure_pa': 20 * PSI, 'duration_s': rinse_s}, injs, sep]
        return self.call('method.configure', {'steps': steps})

    def run(self, seconds=None):
        self.call('run.start')
        total = sum(s['duration_s'] for s in self.h.method) + 1.0
        self.h.step(seconds if seconds is not None else total)

    def truth(self):
        return self.h.read('evaluator')


def schema_errors(value, schema):
    return asset_tool.validate_value(value, schema)


class LibraryTests(unittest.TestCase):
    def test_library_has_50_real_records_with_evidence_cas_and_licence(self):
        lib, _ = rt.library()
        recs = lib['records']
        self.assertGreaterEqual(len(recs), 50)
        perm = [r for r in recs.values() if r['licence']['permissive']]
        self.assertGreaterEqual(len(perm), 25)
        for sid, r in recs.items():
            self.assertTrue(r['cas'] and asset_cas_ok(r['cas']), sid)
            self.assertIn(r['evidence'], ('derived', 'measured_literature', 'literature_compilation'))
            self.assertTrue(r['source'].startswith('https://'))
            self.assertTrue(r['accessed'])
            self.assertIn('permissive', r['licence'])
            if not r['licence']['permissive']:
                self.assertTrue(r['licence']['flag'])
            for f in r['ionic_forms']:
                self.assertTrue(5e-9 <= abs(f['mobility_m2_V_s']) <= 4e-7, sid)
                self.assertEqual(f['charge'] > 0, f['mobility_m2_V_s'] > 0, sid)
        cas = [r['cas'] for r in recs.values()]
        self.assertEqual(len(cas), len(set(cas)))

    def test_phreeqc_nernst_einstein_values(self):
        recs = rt.library()[0]['records']
        # chloride: D25 = 2.033e-9 m2/s (phreeqc.dat) -> 7.91e-8 m2/(V s); literature limiting mobility 7.91e-8
        self.assertAlmostEqual(abs(recs['chloride']['ionic_forms'][0]['mobility_m2_V_s']), 7.91e-8, delta=0.02e-8)
        self.assertAlmostEqual(recs['sodium_ion']['ionic_forms'][0]['mobility_m2_V_s'], 5.19e-8, delta=0.02e-8)


def asset_cas_ok(c):
    import re
    m = re.fullmatch(r'(\d{2,7})-(\d{2})-(\d)', c)
    if not m:
        return False
    d = (m.group(1) + m.group(2))[::-1]
    return sum((i + 1) * int(x) for i, x in enumerate(d)) % 10 == int(m.group(3))


class ChemistryTests(unittest.TestCase):
    def test_buffer_ph_and_conductivity(self):
        b = rt.solve_bge({'acetic_acid': 50.0, 'sodium_ion': 25.0}, 298.15)
        self.assertAlmostEqual(b['pH'], 4.756, delta=0.02)          # half-neutralised acetic acid: pH = pKa
        b = rt.solve_bge({'phosphoric_acid': 25.0, 'sodium_ion': 37.5}, 298.15)
        self.assertAlmostEqual(b['pH'], 7.2, delta=0.05)
        b = rt.solve_bge({'chloride': 10.0, 'sodium_ion': 10.0}, 298.15)
        self.assertAlmostEqual(b['pH'], 7.0, delta=0.02)
        # 10 mM NaCl at 25 C: literature 0.1185 S/m (molar conductivity 118.5 S cm2/mol)
        self.assertAlmostEqual(b['conductivity_s_m'], 0.1185, delta=0.004)
        self.assertAlmostEqual(b['ionic_mol_m3'], 10.0, delta=0.01)

    def test_conductivity_rises_with_temperature(self):
        b25 = rt.solve_bge(BORATE, 298.15)
        b40 = rt.solve_bge(BORATE, 313.15)
        self.assertGreater(b40['conductivity_s_m'] / b25['conductivity_s_m'], 1.25)   # ~2 %/K (water viscosity)
        self.assertLess(b40['conductivity_s_m'] / b25['conductivity_s_m'], 1.45)

    def test_effective_mobility_follows_pka(self):
        mu_lo = rt.effective_mobility('benzoic_acid', 2.0, 10.0, 298.15)[0]
        mu_pk = rt.effective_mobility('benzoic_acid', 4.203, 10.0, 298.15)[0]
        mu_hi = rt.effective_mobility('benzoic_acid', 9.0, 10.0, 298.15)[0]
        self.assertLess(abs(mu_lo), 0.02 * abs(mu_hi))
        self.assertAlmostEqual(mu_pk / mu_hi, 0.5, delta=0.01)
        self.assertLess(mu_hi, 0.0)
        self.assertGreater(rt.effective_mobility('tris', 7.0, 10.0, 298.15)[0], 0.0)

    def test_tris_pka_temperature_shift(self):
        f25 = rt.fractions('tris', 8.07, 298.15)
        f37 = rt.fractions('tris', 8.07, 310.15)
        self.assertLess(f37[1], f25[1])                              # dH = +47 kJ/mol: pKa drops with temperature


class ProtocolTests(unittest.TestCase):
    def test_identity_and_dt(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.sciex_pa800')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-093')
        for bad in (0, -1, float('nan'), True):
            with self.assertRaises(ValueError):
                rt.SciexPa800Runtime().step(bad)

    def test_setpoints_only_write_targets(self):
        b = Bench()
        b.call('power.set', {'enabled': True})
        t0 = b.h.cap_temp
        b.call('capillary.set_temperature', {'temperature_k': 313.15})
        self.assertEqual(b.h.cap_temp, t0)
        self.assertEqual(b.h.read()['capillary.target_k'], 313.15)
        b.h.step(60.0)
        self.assertGreater(b.h.cap_temp, t0)
        self.assertLess(b.h.cap_temp, 313.15)
        b.h.step(900.0)
        self.assertAlmostEqual(b.h.cap_temp, 313.15, delta=0.01)
        b.call('sample_storage.set_temperature', {'temperature_k': 277.15})
        self.assertNotAlmostEqual(b.h.sample_temp, 277.15, delta=1.0)

    def test_read_describe_and_result_are_pure(self):
        b = Bench()
        b.ready()
        b.method()
        b.call('run.start')
        b.h.step(200.0)
        snap = copy.deepcopy(b.h.__dict__)
        rev, t = b.h.revision, b.h.time_s
        for _ in range(3):
            b.ag.read(instance_id=b.iid)
            b.ev.read(instance_id=b.iid)
            b.ag.describe(instance_id=b.iid)
            b.call('electropherogram.read')
        self.assertEqual(b.h.time_s, t)
        self.assertEqual(b.h.revision, rev)                           # queries change no state and no revision
        for k in ('zones', 'vials', 'trace_t', 'trace_a', 'phase', 'cap_fill', 'charge'):
            self.assertEqual(getattr(b.h, k), snap[k], k)

    def test_rejections_leave_state_unchanged(self):
        b = Bench()
        b.call('power.set', {'enabled': True})
        cases = [('run.start', {}), ('cartridge_cover.set_position', {'angle_rad': 1.4}), ('insertion_bar.set_position', {'angle_rad': 1.0}),
                 ('cartridge.install', {'inner_diameter_m': 5e-5, 'length_to_detector_m': 0.5, 'coating': 'neutral'}),
                 ('detector.configure', {'module': 'uv_filter', 'mode': 'direct_uv', 'wavelength_m': 230e-9, 'data_rate_hz': 4.0}),
                 ('method.configure', {'steps': [{'type': 'inject', 'injection_mode': 'pressure', 'inlet': 'SI:A1', 'outlet': 'BO:A1',
                                                  'voltage_v': 5000.0, 'duration_s': 5.0}]}),
                 ('method.configure', {'steps': [{'type': 'separate', 'inlet': 'SI:A9', 'outlet': 'BO:A1', 'control': 'voltage',
                                                  'voltage_v': 20000.0, 'duration_s': 5.0}]}),
                 ('method.configure', {'steps': [{'type': 'separate', 'inlet': 'BI:A1', 'outlet': 'BO:A1', 'control': 'voltage',
                                                  'voltage_v': 20050.0, 'duration_s': 5.0}]}),
                 ('lif.configure', {'excitation_m': 4.88e-7, 'emission_m': 5.2e-7}),
                 ('capillary.set_temperature', {'temperature_k': 280.0})]
        for cap, args in cases:
            skip = ('_requests', 'revision', '_rng')
            before = copy.deepcopy({k: v for k, v in b.h.__dict__.items() if k not in skip})
            rng = b.h._rng.getstate()
            r = b.call(cap, args, expect=('rejected',))
            self.assertEqual(r['error']['side_effects'], 'none')
            after = {k: v for k, v in b.h.__dict__.items() if k not in skip}
            self.assertEqual(rng, b.h._rng.getstate())
            self.assertEqual(before, after, cap)
        r = b.call('vial.place', {'position': 'SI:A1', 'volume_m3': 1e-7, 'composition_mol_m3': {'benzoic_acid': 1.0}, 'label': 'x'}, who='ev',
                   expect=('rejected',))
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')       # sample cover closed
        b.call('sample_cover.set_position', {'angle_rad': math.pi / 2})
        r = b.call('vial.place', {'position': 'SI:A1', 'volume_m3': 1e-7, 'composition_mol_m3': {'no_such': 1.0}, 'label': 'x'}, who='ev',
                   expect=('rejected',))
        self.assertEqual(r['error']['code'], 'UNKNOWN_SPECIES')
        self.assertEqual(b.h.vials, {})

    def test_two_instances_isolated(self):
        a, c = Bench('pa800_a'), Bench('pa800_b')
        a.ready()
        a.method()
        a.run()
        self.assertEqual(c.h.read()['cycle.phase'], 'idle')
        self.assertFalse(c.h.read()['cartridge.installed'])
        self.assertEqual(c.h.vials, {})
        r = a.ag.invoke(instance_id='pa800_b', capability_id='power.set', arguments={'enabled': True}, request_id='x')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')

    def test_agent_evaluator_isolation(self):
        b = Bench()
        b.ready()
        b.method()
        b.run()
        agent = b.ag.read(instance_id=b.iid)['observations']
        self.assertFalse(any(k.startswith('truth.') for k in agent))
        with self.assertRaises(rt.ActionRejected):
            b.ag.read(instance_id=b.iid, state_ids=['truth.peak_identities'])
        self.assertTrue(b.ev.read(instance_id=b.iid, state_ids=['truth.peak_identities'])['observations'])
        r = b.call('vial.remove', {'position': 'SI:A1'}, expect=('rejected',))
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = b.ag.describe(instance_id=b.iid)
        self.assertNotIn('vial.place', d['capabilities'])
        self.assertNotIn('truth.zones', d['state_slots'])
        res = b.call('electropherogram.read')['result']
        text = json.dumps(res)
        for sid in MIX:
            self.assertNotIn(sid, text)
        self.assertNotIn('benzoic', json.dumps(agent))

    def test_idempotent_requests_and_write(self):
        b = Bench()
        r1 = b.ag.invoke(instance_id=b.iid, capability_id='power.set', arguments={'enabled': True}, request_id='same')
        r2 = b.ag.invoke(instance_id=b.iid, capability_id='power.set', arguments={'enabled': True}, request_id='same')
        self.assertEqual(r1, r2)
        r3 = b.ag.invoke(instance_id=b.iid, capability_id='power.set', arguments={'enabled': False}, request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')
        w = b.ag.write(instance_id=b.iid, state_id='capillary.target_k', value=303.15, request_id='w1')
        self.assertEqual(w['status'], 'succeeded')
        self.assertEqual(b.h.cap_target, 303.15)
        with self.assertRaises(rt.ActionRejected):
            b.ag.write(instance_id=b.iid, state_id='hv.current_a', value=1.0, request_id='w2')

    def test_return_values_match_contract_schemas(self):
        b = Bench()
        b.ready()
        b.method()
        for cap in ('power.set', 'lamp.set'):
            pass
        r = b.call('run.start')
        self.assertEqual(schema_errors(r['result'], rt.CAPS['run.start'][1]), [])
        b.h.step(800.0)
        res = b.call('electropherogram.read')['result']
        self.assertEqual(schema_errors(res, rt.CAPS['electropherogram.read'][1]), [])
        vals = b.h.read('evaluator')
        for k, v in vals.items():
            self.assertEqual(schema_errors(v, rt.SLOTS[k][0]), [], k)
        for cap, (inp, out, *_rest) in rt.CAPS.items():
            self.assertEqual(asset_tool.lint_schema(inp), [], cap)
            self.assertEqual(asset_tool.lint_schema(out), [], cap)
            self.assertEqual(asset_tool.validate_value(rt.CAPS[cap][6], inp), [], cap)
        for cap, args in [('power.set', {'enabled': True}), ('capillary.set_temperature', {'temperature_k': 300.15}),
                          ('detector.configure', {'module': 'uv_filter', 'mode': 'direct_uv', 'wavelength_m': 214e-9, 'data_rate_hz': 8.0}),
                          ('sample_storage.set_temperature', {'temperature_k': 283.15}), ('power.toggle', {}), ('power.toggle', {})]:
            out = b.call(cap, args)['result']
            self.assertEqual(schema_errors(out, rt.CAPS[cap][1]), [], cap)


class RunTests(unittest.TestCase):
    def test_full_run_order_times_and_conservation(self):
        b = Bench()
        b.ready()
        v0 = sum(v['volume_m3'] for v in b.h.vials.values())
        b.method()
        b.run()
        self.assertEqual(b.h.phase, 'succeeded')
        t = b.truth()
        peaks = {p['substance_id']: p for p in t['truth.peak_identities']}
        # borate pH 9.24, normal polarity, bare silica: neutral (EOF) < benzoate < salicylate < phthalate
        self.assertLess(peaks['caffeine']['expected_migration_time_s'], peaks['benzoic_acid']['expected_migration_time_s'])
        self.assertLess(peaks['benzoic_acid']['expected_migration_time_s'], peaks['salicylic_acid']['expected_migration_time_s'])
        self.assertLess(peaks['salicylic_acid']['expected_migration_time_s'], peaks['phthalic_acid']['expected_migration_time_s'])
        res = b.call('electropherogram.read')['result']
        found = sorted(p['migration_time_s'] for p in res['peaks'])
        self.assertEqual(len(found), 4)
        for sid in ('caffeine', 'benzoic_acid', 'salicylic_acid', 'phthalic_acid'):
            exp = peaks[sid]['expected_migration_time_s']
            self.assertTrue(any(abs(f - exp) < 0.02 * exp + 6.0 for f in found), (sid, exp, found))
        self.assertAlmostEqual(sum(p['area_percent'] for p in res['peaks']), 100.0, delta=1e-6)
        for sid, row in t['truth.species_ledger_mol'].items():
            self.assertLess(abs(row['residual']), 1e-12 * max(row['placed'], 1e-12) + 1e-18, sid)
        cap_liquid = b.h._cap_volume() * b.h.liquid_fraction
        v1 = sum(v['volume_m3'] for v in b.h.vials.values()) + cap_liquid
        self.assertAlmostEqual(v1, v0, delta=1e-15)
        self.assertGreater(t['truth.charge_passed_c'], 0.0)
        self.assertGreater(len(res['time_s']), 2000)

    def test_migration_time_matches_analytic(self):
        b = Bench()
        b.ready()
        b.method(voltage=20000.0, sep_s=900.0)
        b.run()
        t = b.truth()
        c = b.h.cartridge
        bge = rt.solve_bge(b.h.cap_fill, b.h.cap_temp)
        eof = b.h._eof(bge, b.h.cap_temp)
        mu = rt.effective_mobility('benzoic_acid', bge['pH'], bge['ionic_mol_m3'], b.h.cap_temp)[0]
        analytic = c['ld_m'] * c['lt_m'] / ((mu + eof) * 20000.0)
        res = b.call('electropherogram.read')['result']
        p = min(res['peaks'], key=lambda q: abs(q['migration_time_s'] - analytic))
        self.assertLess(abs(p['migration_time_s'] - analytic) / analytic, 0.03)     # ramp (5 s) and Joule heating only
        self.assertGreater(t['truth.buffer_temperature_k'], 0)

    def test_current_and_joule_heating(self):
        b = Bench()
        b.ready(bge={'phosphoric_acid': 100.0, 'sodium_ion': 150.0}, idm=1.0e-4)
        b.method(voltage=30000.0, sep_s=60.0, limit=3e-4)
        b.call('run.start')
        b.h.step(120 + 5 + 40)
        self.assertEqual(b.h.stage, 'separate')
        self.assertLessEqual(b.h.current, 3e-4 + 1e-12)
        self.assertTrue(math.isclose(b.h.current, 3e-4, rel_tol=1e-9))         # this BGE / bore exceeds the 300 uA limit: current-limited
        self.assertLess(abs(b.h.voltage), 30000.0)
        self.assertGreater(b.h.buffer_temp - b.h.cap_temp, 2.0)                 # Joule heating
        self.assertTrue(b.h.read()['led.hv_positive'])
        self.assertFalse(b.h.read()['led.hv_negative'])

    def test_constant_current_mode(self):
        b = Bench()
        b.ready()
        b.method(control='current', current=1.0e-5, voltage=30000.0, sep_s=60.0)
        b.call('run.start')
        b.h.step(120 + 5 + 30)
        self.assertAlmostEqual(b.h.current, 1.0e-5, delta=1e-8)
        self.assertGreater(b.h.voltage, 0)
        self.assertLess(b.h.voltage, 30000.0)
        b2 = Bench()                                                     # 20 uA would need > 30 kV in this BGE: the compliance voltage caps it
        b2.ready()
        b2.method(control='current', current=2.0e-5, voltage=30000.0, sep_s=60.0)
        b2.call('run.start')
        b2.h.step(120 + 5 + 30)
        self.assertAlmostEqual(b2.h.voltage, 30000.0, delta=1e-6)
        self.assertLess(b2.h.current, 2.0e-5)

    def test_injection_volume_poiseuille(self):
        vols = []
        for p_psi, t_s in ((0.5, 5.0), (1.0, 5.0), (0.5, 10.0)):
            b = Bench()
            b.ready()
            b.method(inj=('pressure', p_psi * PSI, t_s), sep_s=1.0)
            v0 = b.h.vials['SI:A1']['volume_m3']
            b.run(120 + t_s + 0.5)
            vols.append(v0 - b.h.vials['SI:A1']['volume_m3'])
        self.assertAlmostEqual(vols[1] / vols[0], 2.0, delta=1e-4)
        self.assertAlmostEqual(vols[2] / vols[0], 2.0, delta=1e-4)
        # 0.5 psi, 5 s, 50 um x 60.2 cm at 25 C: about 5 nL (Hagen-Poiseuille)
        self.assertAlmostEqual(vols[0], 4.9e-12, delta=0.6e-12)

    def test_reverse_polarity_indirect_uv_anions_on_neutral_capillary(self):
        b = Bench()
        b.ready(coating='neutral', bge={'phthalic_acid': 5.0, 'tris': 15.0},
                sample={'chloride': 0.5, 'sulfate': 0.25, 'nitrate': 0.5, 'sodium_ion': 1.5})
        b.call('detector.configure', {'module': 'pda', 'mode': 'indirect_uv', 'wavelength_m': 254e-9, 'data_rate_hz': 4.0})
        b.method(voltage=-20000.0, sep_s=600.0)
        b.run()
        t = b.truth()
        self.assertEqual(t['truth.eof_mobility_m2_v_s'], 0.0)
        res = b.call('electropherogram.read')['result']
        self.assertEqual(res['detection'], 'indirect_uv')
        self.assertGreaterEqual(len(res['peaks']), 2)                      # sulfate and chloride co-migrate in this BGE
        self.assertLess(min(res['absorbance_au']), -5 * rt.PLANT['baseline_noise_au'])   # negative (indirect) peaks
        ids = {p['substance_id']: p for p in t['truth.peak_identities']}
        self.assertIsNone(ids['sodium_ion']['expected_migration_time_s'])               # cation migrates away from the detector
        self.assertLess(ids['sulfate']['expected_migration_time_s'], ids['nitrate']['expected_migration_time_s'])
        self.assertTrue(b.h.read('evaluator')['led.hv_negative'] is False)              # run finished: HV off

    def test_cancel_keeps_partial_data_and_mass(self):
        b = Bench()
        b.ready()
        b.method()
        r = b.call('run.start')
        op = r['operation_id']
        b.h.step(120 + 5 + 250)
        n0 = len(b.h.trace_t)
        self.assertTrue(b.h.read()['led.hv_positive'])
        c = b.ag.cancel(op, 'cancel-1')
        self.assertEqual(c['status'], 'cancelled')
        self.assertEqual(b.h.fault, 'USER_STOP')
        self.assertFalse(b.h.read()['led.hv_positive'])
        b.h.step(100.0)
        self.assertEqual(len(b.h.trace_t), n0)
        for sid, row in b.truth()['truth.species_ledger_mol'].items():
            self.assertLess(abs(row['residual']), 1e-12 * max(row['placed'], 1e-12) + 1e-18, sid)
        self.assertEqual(b.ag.poll(op)['status'], 'cancelled')
        with self.assertRaises(rt.ActionRejected):
            b.ev.poll(op)

    def test_door_interlock_and_power_loss(self):
        b = Bench()
        b.ready()
        b.method()
        b.call('run.start')
        b.h.step(120 + 5 + 50)
        b.call('sample_cover.set_position', {'angle_rad': 0.3})
        self.assertEqual((b.h.phase, b.h.fault), ('cancelled', 'DOOR_INTERLOCK'))
        self.assertEqual(b.h.voltage, 0.0)
        b2 = Bench()
        b2.ready()
        b2.method()
        b2.call('run.start')
        b2.h.step(60.0)
        b2.call('power.toggle')
        self.assertEqual((b2.h.phase, b2.h.fault), ('cancelled', 'POWER_LOSS'))
        self.assertFalse(b2.h.read()['led.power'])

    def test_faults_dry_capillary_overflow_and_preconditions(self):
        b = Bench()
        b.ready()
        b.call('method.configure', {'steps': [{'type': 'separate', 'inlet': 'BI:B1', 'outlet': 'BO:B1', 'control': 'voltage', 'voltage_v': 20000.0,
                                               'duration_s': 30.0}]})
        b.call('run.start')
        b.h.step(5.0)
        self.assertEqual((b.h.phase, b.h.fault), ('failed', 'CURRENT_FAULT'))           # new capillary never rinsed
        b2 = Bench()
        b2.ready(outlet_volume=1.45e-6)
        b2.call('method.configure', {'steps': [{'type': 'rinse', 'inlet': 'BI:A1', 'outlet': 'BO:A1', 'pressure_pa': 100 * PSI, 'duration_s': 3600.0}]})
        b2.call('run.start')
        b2.h.step(3600.0)
        self.assertEqual((b2.h.phase, b2.h.fault), ('failed', 'VIAL_OVERFLOW'))
        b3 = Bench()
        b3.ready(warm=False)
        b3.method()
        r = b3.call('run.start', expect=('rejected',))
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')                    # lamp not warmed up
        b3.h.step(300.0)
        b3.call('sample_cover.set_position', {'angle_rad': math.pi / 2})
        r = b3.call('run.start', expect=('rejected',))
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')                    # door open
        r = b3.call('sample_cover.set_position', {'angle_rad': 0.0})
        b3.call('sample_cover.set_position', {'angle_rad': math.pi / 2})
        b3.call('cartridge_cover.set_position', {'angle_rad': 1.4})
        r = b3.call('sample_cover.set_position', {'angle_rad': 0.0}, expect=('rejected',))
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')                    # cannot close over the open cartridge cover

    def test_cartridge_removal_books_capillary_content(self):
        b = Bench()
        b.ready()
        b.method(sep_s=30.0)
        b.run()
        b.call('sample_cover.set_position', {'angle_rad': math.pi / 2})
        b.call('cartridge_cover.set_position', {'angle_rad': 1.4})
        b.call('insertion_bar.set_position', {'angle_rad': 1.0})
        b.call('cartridge.remove')
        self.assertFalse(b.h.read()['cartridge.installed'])
        for sid, row in b.truth()['truth.species_ledger_mol'].items():
            self.assertLess(abs(row['residual']), 1e-12 * max(row['placed'], 1e-12) + 1e-18, sid)
            self.assertEqual(row['in_zones'], 0.0)

    def test_eof_neutral_marker_and_coating(self):
        for coating, expect_detect in (('bare_fused_silica', True), ('neutral', False)):
            b = Bench()
            b.ready(coating=coating, sample={'caffeine': 0.3})
            b.method(sep_s=500.0)
            b.run()
            res = b.call('electropherogram.read')['result']
            self.assertEqual(bool(res['peaks']), expect_detect, coating)

    def test_electrokinetic_injection_has_current_and_hv_lamp(self):
        b = Bench()
        b.ready()
        b.method(inj=('voltage', 5000.0, 5.0), sep_s=60.0)
        b.call('run.start')
        b.h.step(120.0 + 2.0)
        self.assertEqual(b.h.stage, 'inject')
        v = b.h.read('evaluator')
        self.assertGreater(v['hv.current_a'], 1e-6)
        self.assertTrue(v['led.hv_positive'])
        self.assertGreater(v['truth.charge_passed_c'], 0.0)
        self.assertTrue(any(z['n_mol'] > 0 for z in b.h.zones))

    def test_lamp_off_before_separation_aborts(self):
        b = Bench()
        b.ready()
        b.method()
        b.call('run.start')
        b.h.step(30.0)                                                    # still rinsing
        b.call('lamp.set', {'on': False})
        self.assertEqual((b.h.phase, b.h.fault), ('cancelled', 'LAMP_OFF'))

    def test_power_off_turns_lamp_off(self):
        b = Bench()
        b.ready()
        b.call('power.set', {'enabled': False})
        v = b.h.read()
        self.assertFalse(v['lamp.on'] or v['led.uv'] or v['lamp.ready'])

    def test_truth_expectation_with_pressure_and_constant_current(self):
        for kw in (dict(voltage=25000.0, sep_s=600.0, sep_pressure=1 * PSI), dict(control='current', current=1.0e-5, voltage=30000.0, sep_s=900.0)):
            b = Bench()
            b.ready(sample={'benzoic_acid': 0.5})
            b.method(**kw)
            b.run()
            exp = [p['expected_migration_time_s'] for p in b.truth()['truth.peak_identities'] if p['substance_id'] == 'benzoic_acid'][0]
            found = [p['migration_time_s'] for p in b.call('electropherogram.read')['result']['peaks']]
            self.assertTrue(found and min(abs(f - exp) for f in found) < 0.02 * exp + 2.0, (kw, exp, found))

    def test_contract_metadata(self):
        meta = rt.export_sciex_pa800_contract_metadata()
        self.assertEqual(set(meta['capabilities']), set(rt.CAPS))
        self.assertEqual(meta['capabilities']['lif.configure']['availability'], 'unsupported')
        for k, s in meta['state_slots'].items():
            self.assertTrue(s['meaning_zh'], k)
            if s['origin'] == 'process_truth':
                self.assertNotEqual(s['visibility'], 'agent', k)
        self.assertIn('electrophoresis', meta['mechanisms'])


if __name__ == '__main__':
    unittest.main()
