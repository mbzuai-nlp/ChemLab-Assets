"""tests for CHEM-170 cytiva_biacoret200 (Biacore T200 SPR system): python3 -m unittest tests/test_asset_cytiva_biacoret200.py

Themes: set-points only write targets; read / describe are pure; rejected calls leave the state unchanged; two instances are isolated; agent /
evaluator truth isolation and foreign-agent ownership; idempotent request ids; step-partition independence; start-up lamps, software connection and
the 5 K ~ 1 h temperature response; chip insert / dock / eject / reuse; standby consumption (65 mL / 24 h) and expiry; Prime and the gradual buffer
change; liquid conservation (bottles -> waste / spill); rack tray, door and clamp interlocks; bulk refractive index (1 % DMSO ~ 1200 RU) and its
temperature dependence; amine coupling (Getting Started > 8000 RU, pH trend), pH scouting and aim-for-level; 1:1 kinetics with mass transport
recovered by the evaluation; steady-state affinity, CFCA, regeneration and ligand damage, solvent correction, van 't Hoff / Eyring; NTA and SA
capture; normalization; System Check; maintenance and shutdown; manual-run queue; cancellation, power loss; every output validated against its
contract schema; declared state effects cover the observed changes."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import cytiva_biacoret200 as rt  # noqa: E402

UL, ULM = rt.UL, rt.UL_MIN
CHECKS = []
INST = 'biacore_t200_01'
GS_L, GS_A = rt.GS_LIGAND, rt.GS_ANALYTE
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
_ALWAYS = {'operation.started_by_other_agent'}


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return x != y


class Rig:
    def __init__(self, seed=1, room=298.15, instance=INST):
        self.h = rt.CytivaBiacoret200Runtime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', room_k=room, mains_voltage_v=230.0, noise_seed=seed)

    def call(self, sess, cap, expect='succeeded', **args):
        """Instrumented call: outputs validated against the declared schema; a success changes only declared immediate slots and never the clock;
        a rejection leaves every slot unchanged and returns a declared error code."""
        self.n += 1
        before, t0 = self.h.read('evaluator'), self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'r{self.n}')
        after = self.h.read('evaluator')
        changed = {k for k in after if _differ(after[k], before[k])} - {'clock.time_s'}
        if expect in ('succeeded', 'accepted'):
            assert r['status'] in ('succeeded', 'accepted'), (cap, r['error'])
            rt.validate(rt.CAPS[cap]['output_schema'], r['result'])
            if cap != 'sim.step':
                extra = changed - set(rt.EFFECTS.get(cap, [])) - _ALWAYS
                assert not extra, (cap, sorted(extra))
                assert self.h.t == t0, cap
            SEEN_CAPS.add(cap)
            PROCESS_EVIDENCE.setdefault(cap, []).append(dict(arguments=json.dumps(args, sort_keys=True, default=str)[:200], time_s=t0, status=r['status']))
            return r['result']
        assert r['status'] in ('rejected', 'unsupported'), (cap, r['status'], r.get('result'))
        assert r['error']['code'] == expect, (cap, r['error']['code'], expect)
        assert not changed, (cap, 'rejected call changed', sorted(changed))
        assert expect in [e['code'] for e in rt.export_cytiva_biacoret200_contract_metadata()['capabilities'][cap]['errors']] or expect == 'UNSUPPORTED', (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def until(self, pred, limit=7200.0, dt=10.0):
        t = 0.0
        while not pred() and t < limit:
            self.step(dt)
            t += dt
        assert pred(), f'condition not reached in {limit} s'

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def ready(self, chip='CM5', chip_id='chip_1', buffer='hbs_ep_plus', prime=True):
        A = self.A
        self.call(A, 'power.set', enabled=True)
        self.step(35)
        self.call(A, 'solution.prepare', solution_id='run_buffer', volume_m3=1.0e-3, base=buffer)
        self.call(A, 'solution.prepare', solution_id='dist_water', volume_m3=0.5e-3, base='water')
        self.call(A, 'bottle.place', slot='buffer_a', solution_id='run_buffer', volume_m3=0.95e-3)
        self.call(A, 'bottle.place', slot='water', solution_id='dist_water', volume_m3=0.45e-3)
        self.call(A, 'bottle.empty_waste')
        self.call(A, 'chip.open_port')
        self.step(2)
        self.call(A, 'chip.insert', chip_type=chip, chip_id=chip_id)
        self.call(A, 'chip.close_cover')
        self.step(2)
        self.call(A, 'chip.dock')
        self.step(50)
        if prime:
            self.call(A, 'flow.prime')
            self.step(400)
        self.until(lambda: self.h._stable(), 7200)

    def rack(self, config='sample_and_reagent_rack', plate='none', items=()):
        A = self.A
        if self.h.rack['state'] == 'inside':
            self.call(A, 'rack.eject')
            self.step(8)
        if self.h.rack['config'] is None or self.h.rack['config']['config'] != config:
            self.call(A, 'rack.configure', config=config, plate=plate)
        for pos, sid, v in items:
            if pos in self.h.rack['positions']:
                self.call(A, 'rack.remove', position=pos)
            self.call(A, 'rack.place', position=pos, solution_id=sid, volume_m3=v)
        self.call(A, 'rack.retract')
        self.step(8)

    def gs_kit(self):
        self.call(self.E, 'kit.provide', kit='getting_started')
        self.call(self.E, 'kit.provide', kit='amine_coupling')

    def amine(self, fc, ligand_sid, contact=420.0, flow=10 * ULM, run_id=None, mode='contact', target=None, wash=None):
        items = [('R1A1', 'edc_0_4m', 150 * UL), ('R1A2', 'nhs_0_1m', 150 * UL), ('R1A4', 'ethanolamine_1m', 150 * UL), ('R1A5', ligand_sid, 400 * UL)]
        if wash:
            items.append(('R1A6', wash, 300 * UL))
        if 'R1A3' in self.h.rack['positions'] and self.h.rack['state'] == 'inside':
            self.call(self.A, 'rack.eject')
            self.step(8)
            self.call(self.A, 'rack.remove', position='R1A3')
        self.rack(items=items)
        f = dict(fc=fc, chemistry='amine', mode=mode, ligand_position='R1A5', ligand_name=ligand_sid, flow_rate_m3_s=flow, edc_position='R1A1', nhs_position='R1A2',
                 mix_position='R1A3', ethanolamine_position='R1A4')
        if mode == 'contact':
            f['contact_s'] = contact
        if mode == 'aim':
            f.update(target_ru=target, wash_position='R1A6')
        op = self.call(self.A, 'wizard.immobilization', run_id=run_id or f'immob_{fc}_{self.n}', flow_cells=[f])['operation_id']
        self.until(lambda: self.h.operations[op]['phase'] != 'running', 14400, 30.0)
        return self.h.operations[op]['result']

    def kinetics_method(self, run_id, concs_nm, path='3-4', ref='4-3', contact=120.0, diss=600.0, flow=30 * ULM, regen='glycine_2_5', stock='gs_analyte_b2m', mw=11800.0,
                        rate=1, startup=1, base='hbs_ep_plus'):
        stock_c = self.h.solutions[stock]['label'][0]['concentration_mol_m3']
        items = []
        for i, c in enumerate(concs_nm):
            sid = f'{run_id}_s{i}'
            src = [dict(source_id=stock, volume_m3=c * 1e-6 * 600 * UL / stock_c)] if c else []
            self.call(self.A, 'solution.prepare', solution_id=sid, volume_m3=600 * UL, base=base, sources=src)
            items.append((f'R1B{i + 1}', sid, 400 * UL))
        self.call(self.A, 'solution.prepare', solution_id=f'{run_id}_regen', volume_m3=3.0e-6, base=regen)
        items.append(('R1A9', f'{run_id}_regen', 3.0e-6))
        self.rack(items=items)
        cyc = [dict(purpose='startup', commands=[dict(type='sample', position='R1B1', contact_s=contact, dissociation_s=60.0, flow_rate_m3_s=flow),
                                                 dict(type='regeneration', position='R1A9', contact_s=30.0, flow_rate_m3_s=flow, stabilization_s=60.0)])] * startup
        for i, c in enumerate(concs_nm):
            cyc.append(dict(purpose='blank' if c == 0 else 'sample', keywords=dict(sample_name=f'{c} nM', concentration_mol_m3=c * 1e-6, mw_da=mw),
                            commands=[dict(type='sample', position=f'R1B{i + 1}', contact_s=contact, dissociation_s=diss, flow_rate_m3_s=flow),
                                      dict(type='regeneration', position='R1A9', contact_s=30.0, flow_rate_m3_s=flow, stabilization_s=60.0)]))
        op = self.call(self.A, 'method.run', run_id=run_id, data_rate_hz=rate, detection='dual' if '-' in path and len(path) == 3 else 'multi', flow_path=path,
                       reference_subtraction=ref, cycles=cyc)['operation_id']
        self.until(lambda: self.h.operations[op]['phase'] != 'running', 40000, 60.0)
        return op


def truth(r):
    return r.read(*[k for k, v in rt.SLOTS.items() if v['visibility'] == 'evaluator' or True])


class ContractAndLibraries(unittest.TestCase):
    def test_metadata_consistency(self):
        m = rt.export_cytiva_biacoret200_contract_metadata()
        self.assertEqual(len(m['capabilities']), len(rt.CAPS))
        for k, c in m['capabilities'].items():
            if k in rt.UNSUPPORTED:
                self.assertEqual(c['availability'], 'unsupported')
                continue
            for e in c['state_effects']:
                self.assertIn(e['state_id'], rt.SLOTS)
            for e in c['errors']:
                self.assertIn(e['code'], rt.ERROR_ZH)
            if rt.CAPS[k]['kind'] not in ('query',) and k != 'sim.step':
                self.assertTrue(c['preconditions'], k)
        for k, v in rt.SLOTS.items():
            if v['origin'] == 'process_truth':
                self.assertNotEqual(v['visibility'], 'agent', k)
        CHECKS.append(dict(check='metadata_consistency', status='passed', capabilities=len(rt.CAPS), slots=len(rt.SLOTS)))

    def test_libraries(self):
        lib = rt.interactions()
        doc = [k for k, v in lib.items() if v['evidence'] == 'documented_literature']
        self.assertGreaterEqual(len(doc), 50)
        self.assertGreaterEqual(len(rt.CRC), 50)
        self.assertAlmostEqual(rt.n_water(293.15), 1.33211, places=4)
        self.assertLess(rt.n_water(310.15), rt.n_water(298.15))
        dn, eta, I = rt.solution_optics(rt.library_composition('hbs_ep_plus_dmso_5'))
        dn0, _, _ = rt.solution_optics(rt.library_composition('hbs_ep_plus'))
        self.assertAlmostEqual((dn - dn0) * rt.S_BULK / 5.0, 1200.0, delta=1.0)
        D = rt.diffusion_estimate(100000.0)
        self.assertAlmostEqual(D, 6.9e-11, delta=0.3e-11)                      # Cytiva: ~6e-11 m2/s at 100 kDa
        kt = rt.km_flow_cell(rt.diffusion_estimate(15000.0), 30 * ULM) * 15000.0 * 1e9
        self.assertTrue(5e7 < kt < 1e9, kt)                                    # Software Handbook: proteins ~1e8 RU M^-1 s^-1
        CHECKS.append(dict(check='libraries', status='passed', interactions=len(doc), crc=len(rt.CRC), kt_15kDa=kt))


class Basics(unittest.TestCase):
    def test_read_pure_and_setpoint_only(self):
        r = Rig(room=295.15)
        r.call(r.A, 'power.set', enabled=True)
        r.step(35)
        a = r.read()
        b = r.read()
        self.assertEqual(a, b)
        t0 = r.h.t
        r.A.describe(instance_id=INST)
        self.assertEqual(r.h.t, t0)
        T0 = r.read('truth.analysis_temperature_k')
        r.call(r.A, 'temperature.set', analysis_k=300.15)
        self.assertEqual(r.read('temperature.analysis_setpoint_k'), 300.15)
        self.assertEqual(r.read('truth.analysis_temperature_k'), T0)
        r.step(60)
        self.assertGreater(r.read('truth.analysis_temperature_k'), T0)
        CHECKS.append(dict(check='read_pure_setpoint_only', status='passed'))

    def test_rejection_atomic_and_codes(self):
        r = Rig()
        before = copy.deepcopy(r.read())
        r.call(r.A, 'temperature.set', 'POWER_OFF', analysis_k=300.0)
        r.call(r.A, 'chip.dock', 'POWER_OFF')
        r.call(r.A, 'rack.place', 'RACK_NOT_EJECTED', position='R1A1', solution_id='x', volume_m3=1e-7)
        r.call(r.A, 'solution.prepare', 'UNKNOWN_SOLUTION', solution_id='a', volume_m3=1e-6, base='water', sources=[dict(source_id='nope', volume_m3=1e-8)])
        r.call(r.A, 'clamp.set', 'DOOR_CLOSED', closed=False)
        r.call(r.A, 'surface.inspect', 'ACCESS_DENIED')
        r.call(r.A, 'sample.recovery', 'UNSUPPORTED')
        self.assertEqual(before, r.read())
        r.call(r.A, 'power.set', enabled=True)
        r.call(r.A, 'chip.open_port', 'NOT_CONNECTED')
        r.step(35)
        r.call(r.A, 'temperature.set', 'BELOW_AMBIENT_LIMIT', analysis_k=277.15)
        r.call(r.A, 'flow.prime', 'CHIP_NOT_DOCKED')
        CHECKS.append(dict(check='rejection_atomic', status='passed'))

    def test_two_instances_and_isolation(self):
        r1, r2 = Rig(seed=3), Rig(seed=3, instance='biacore_t200_02')
        r1.call(r1.A, 'power.set', enabled=True)
        r1.step(40)
        self.assertTrue(r1.read('power.enabled'))
        self.assertFalse(r2.read('power.enabled'))
        agent_view = r1.read(who=r1.A)
        self.assertFalse(any(k.startswith('truth.') for k in agent_view))
        with self.assertRaises(rt.ActionRejected):
            r1.A.read(instance_id=INST, state_ids=['truth.complexes_ru'])
        with self.assertRaises(rt.ActionRejected):
            r1.A.read(instance_id='biacore_t200_02')
        r1.h.register_principal('agent2', 'agent')
        B = r1.h.session('agent2')
        r1.call(r1.A, 'solution.prepare', solution_id='mine', volume_m3=1e-5, base='water')
        rr = B.invoke(instance_id=INST, capability_id='solution.prepare', arguments=dict(solution_id='b', volume_m3=1e-6, base='water',
                                                                                          sources=[dict(source_id='mine', volume_m3=1e-7)]), request_id='x1')
        self.assertEqual(rr['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('mine', B.read(instance_id=INST, state_ids=['solutions.available'])['observations']['solutions.available']['value'])
        CHECKS.append(dict(check='instances_isolation', status='passed'))

    def test_idempotent_requests(self):
        r = Rig()
        a = r.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(enabled=True), request_id='same')
        b = r.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(enabled=True), request_id='same')
        self.assertEqual(a, b)
        c = r.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(enabled=False), request_id='same')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')
        self.assertTrue(r.read('power.enabled'))
        CHECKS.append(dict(check='idempotent', status='passed'))

    def test_step_partition_independence(self):
        out = []
        for split in (1, 7):
            r = Rig(seed=5)
            r.ready(prime=False)
            r.gs_kit()
            r.call(r.A, 'solution.prepare', solution_id='lig', volume_m3=400 * UL, base='acetate_5_0', sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=12 * UL)])
            r.rack(items=[('R1A5', 'lig', 300 * UL)])
            r.call(r.A, 'run.manual_start', run_id='m', flow_rate_m3_s=10 * ULM, flow_path='2', data_rate_hz=10)
            r.call(r.A, 'run.command', command=dict(command='sample_injection', position='R1A5', contact_s=60.0))
            total = 120.0
            for k in range(split):
                r.h.step(total / split)
            out.append(r.read())
        for k in out[0]:
            a, b = out[0][k], out[1][k]
            if isinstance(a, float):
                self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(a)), msg=k)
            else:
                self.assertEqual(a, b, k)
        CHECKS.append(dict(check='step_partition_independence', status='passed'))


class PowerTemperatureChip(unittest.TestCase):
    def test_startup_lamps_connection(self):
        r = Rig()
        r.call(r.A, 'power.press_switch')
        r.step(1)
        L = r.read('lamp.ready', 'lamp.system', 'lamp.temperature', 'lamp.sensor_chip', 'lamp.run')
        self.assertTrue(all(L.values()))
        r.step(4)
        self.assertTrue(r.read('lamp.ready'))
        self.assertFalse(r.read('lamp.system'))
        self.assertFalse(r.read('system.connected'))
        r.step(30)
        self.assertTrue(r.read('system.connected'))
        r.call(r.A, 'power.press_switch')
        self.assertFalse(r.read('power.enabled'))
        CHECKS.append(dict(check='startup_lamps', status='passed'))

    def test_temperature_5k_about_one_hour(self):
        r = Rig(room=298.15)
        r.call(r.A, 'power.set', enabled=True)
        r.step(40)
        r.call(r.A, 'temperature.set', analysis_k=303.15)
        flash = set()
        t = 0.0
        while not r.read('temperature.analysis_stable'):
            r.h.step(0.5)
            t += 0.5
            flash.add(r.read('lamp.temperature'))
            if t > 3 * 3600:
                break
        self.assertTrue(40 * 60 < t < 80 * 60, t)                               # Operating Instructions: "A temperature change of 5 C takes about 1 hour"
        self.assertEqual(flash, {True, False})
        r.step(10)
        self.assertTrue(r.read('lamp.temperature'))
        CHECKS.append(dict(check='temperature_5k', status='passed', minutes=t / 60))

    def test_chip_lifecycle_and_reuse(self):
        r = Rig()
        r.call(r.A, 'power.set', enabled=True)
        r.step(35)
        r.call(r.A, 'chip.insert', 'PORT_CLOSED', chip_type='CM5', chip_id='c1')
        r.call(r.A, 'chip.open_port')
        r.step(2)
        self.assertGreater(r.read('chip.port_cover_angle_rad'), 1.1)
        r.call(r.A, 'chip.insert', chip_type='CM5', chip_id='c1')
        seen = set()
        for _ in range(4):
            r.h.step(0.5)
            seen.add(r.read('lamp.sensor_chip'))
        self.assertEqual(seen, {True, False})
        r.call(r.A, 'chip.dock', 'COVER_OPEN')
        r.call(r.A, 'chip.close_cover')
        r.step(2)
        r.call(r.A, 'chip.dock', 'NO_BUFFER') if False else None
        op = r.call(r.A, 'chip.dock')['operation_id']
        r.step(50)
        self.assertEqual(r.read('chip.state'), 'docked')
        self.assertTrue(r.read('lamp.sensor_chip'))
        r.call(r.A, 'chip.open_port', 'CHIP_DOCKED')
        r.call(r.A, 'chip.eject')
        r.step(35)
        self.assertEqual(r.read('chip.state'), 'ejected')
        r.call(r.A, 'chip.remove')
        r.call(r.A, 'chip.insert', 'CHIP_ID_EXISTS', chip_type='CM5', chip_id='c1')
        r.call(r.A, 'chip.insert', 'CHIP_UNKNOWN', chip_type='CM5', chip_id='c9', reuse=True)
        r.call(r.A, 'chip.insert', 'CHIP_TYPE_MISMATCH', chip_type='SA', chip_id='c1', reuse=True)
        r.call(r.A, 'chip.insert', chip_type='CM5', chip_id='c1', reuse=True)
        self.assertEqual(r.h._chip_obj()['docks'], 1)
        CHECKS.append(dict(check='chip_lifecycle', status='passed'))

    def test_noise_and_docking_drift(self):
        r = Rig(seed=11)
        r.ready()
        r.call(r.A, 'run.manual_start', run_id='n', flow_rate_m3_s=30 * ULM, flow_path='1-2-3-4', data_rate_hz=1)
        r.step(600)
        cyc = r.h.runs['n']['cycles'][0]
        y = np.array(cyc['r']['2']) - np.array(cyc['r']['1'])
        res = y - np.polyval(np.polyfit(cyc['t'], y, 2), cyc['t'])
        rms = float(np.std(res))
        self.assertTrue(0.01 < rms < 0.06, rms)                                 # 2-1 noise ~ sqrt(2) x 0.025 RU (data file: typically < 0.03 RU per curve)
        raw = np.array(cyc['r']['2'])
        slope_early = np.polyfit(cyc['t'][:120], raw[:120], 1)[0] * 60
        self.assertGreater(abs(slope_early), 0.3)                               # hydration drift after docking a new chip
        r.call(r.A, 'run.stop')
        CHECKS.append(dict(check='noise_drift', status='passed', rms_2_1=rms))


class Fluidics(unittest.TestCase):
    def test_standby_consumption_and_expiry(self):
        r = Rig()
        r.ready(prime=False)
        self.assertEqual(r.read('flow.state'), 'standby')
        b0 = r.read('truth.bottle_volumes_m3')
        r.step(86400)
        b1 = r.read('truth.bottle_volumes_m3')
        used = (b0['buffer_a'] - b1['buffer_a']) + (b0['water'] - b1['water'])
        self.assertAlmostEqual(used / 65e-6, 1.0, delta=0.05)                    # Operating Instructions: ~65 mL per 24 h
        r.call(r.A, 'bottle.place', slot='buffer_a', solution_id='run_buffer', volume_m3=5e-5)   # top-up (remaining bench volume)
        led = r.call(r.E, 'ledger.read')['ledger']
        self.assertAlmostEqual(led['buffer_used_m3'] + led['water_used_m3'], led['waste_added_m3'] + led['waste_spilled_m3'], delta=1e-12)
        CHECKS.append(dict(check='standby_65ml', status='passed', ml_per_day=used * 1e6))

    def test_standby_expiry_7_days(self):
        r = Rig()
        r.ready(prime=False)
        r.h.bottles['buffer_a']['V'] = 1.0e-3
        r.h.bottles['water']['V'] = 0.5e-3
        r.step(7 * 86400 + 60)
        self.assertEqual(r.read('flow.state'), 'stopped')
        self.assertIn('STANDBY_EXPIRED', r.read('warnings.active'))
        CHECKS.append(dict(check='standby_expiry', status='passed'))

    def test_prime_and_gradual_buffer_change(self):
        r = Rig(seed=4)
        r.ready()
        self.assertEqual(r.read('truth.system_buffer'), 'run_buffer')
        r.call(r.A, 'solution.prepare', solution_id='pbs_run', volume_m3=1.0e-3, base='pbs')
        r.call(r.A, 'bottle.place', slot='buffer_a', solution_id='pbs_run', volume_m3=0.9e-3)
        r.step(20)
        self.assertIn('PRIME_RECOMMENDED', r.read('warnings.active'))
        R0 = r.read('response.ru')['1']
        r.step(1200)
        R1 = r.read('response.ru')['1']
        self.assertGreater(abs(R1 - R0), 10.0)                                  # baseline moves while the new buffer replaces the old one
        r.call(r.A, 'flow.prime')
        r.step(400)
        self.assertEqual(r.read('truth.system_buffer'), 'pbs_run')
        self.assertNotIn('PRIME_RECOMMENDED', r.read('warnings.active'))
        CHECKS.append(dict(check='prime_buffer_change', status='passed', shift_ru=R1 - R0))

    def test_buffer_empty_fails_run_and_waste_bottle(self):
        r = Rig()
        r.ready()
        r.call(r.A, 'run.manual_start', run_id='e', flow_rate_m3_s=100 * ULM, flow_path='1')
        r.h.bottles['buffer_a']['V'] = 5e-8
        r.step(120)
        self.assertEqual(r.h.runs['e']['state'], 'failed')
        self.assertIn('BUFFER_EMPTY', r.read('errors.active'))
        r.h.bottles['buffer_a']['V'] = 5e-4
        r.call(r.A, 'bottle.remove', slot='waste')
        r.call(r.A, 'run.manual_start', 'NO_WASTE_BOTTLE', run_id='e2', flow_rate_m3_s=10 * ULM, flow_path='1')
        CHECKS.append(dict(check='buffer_empty', status='passed'))

    def test_clamp_door_interlock_and_air(self):
        r = Rig()
        r.ready()
        r.call(r.A, 'clamp.set', 'DOOR_CLOSED', closed=False)
        r.call(r.A, 'door.set', door='right_pump', open=True)
        r.step(2)
        r.call(r.A, 'clamp.set', closed=False)
        r.step(2)
        self.assertGreater(r.read('clamp.lever_angle_rad'), 1.3)
        self.assertIn('CLAMP_OPEN', r.read('warnings.active'))
        r.call(r.A, 'solution.prepare', solution_id='buf', volume_m3=1e-6, base='hbs_ep_plus')
        r.rack(items=[('R1B1', 'buf', 300 * UL)])
        r.call(r.A, 'run.manual_start', run_id='air', flow_rate_m3_s=30 * ULM, flow_path='1-2')
        r.call(r.A, 'run.command', command=dict(command='sample_injection', position='R1B1', contact_s=60.0))
        r.step(120)
        inj = r.h.runs['air']['cycles'][0]['injections'][0]
        self.assertTrue(inj['air'])
        self.assertLess(min(r.h.runs['air']['cycles'][0]['r']['1']), r.h.runs['air']['cycles'][0]['r']['1'][0] - 5000)   # air spike
        r.call(r.A, 'run.stop')
        CHECKS.append(dict(check='clamp_air', status='passed'))


class RackAndDoors(unittest.TestCase):
    def test_rack_eject_retract_and_mounting(self):
        r = Rig(seed=2)
        r.call(r.A, 'plant.configure', 'ACCESS_DENIED', room_k=298.0, mains_voltage_v=230.0, noise_seed=2)
        r.call(r.E, 'plant.configure', room_k=298.15, mains_voltage_v=230.0, noise_seed=2, rack_auto_retract_s=60.0)
        r.call(r.A, 'power.set', enabled=True)
        r.step(35)
        r.call(r.A, 'rack.eject')
        r.step(8)
        self.assertAlmostEqual(r.read('rack.carriage_position_m'), 0.14, places=6)
        r.call(r.A, 'rack.configure', config='tray_plate_type2', plate='96_deep', correctly_mounted=False)
        r.call(r.A, 'rack.retract', 'RACK_NOT_SEATED')
        r.step(120)
        self.assertEqual(r.read('rack.state'), 'ejected')
        r.call(r.A, 'rack.configure', config='tray_plate_type2', plate='96_deep')
        r.call(r.A, 'rack.place', 'UNKNOWN_POSITION', position='R1A1', solution_id='x', volume_m3=1e-7)
        r.call(r.A, 'solution.prepare', solution_id='s', volume_m3=2e-6, base='hbs_ep_plus')
        r.call(r.A, 'rack.place', 'VOLUME_OUT_OF_RANGE', position='R2B1', solution_id='s', volume_m3=1.0e-6)
        r.call(r.A, 'rack.place', position='P1A1', solution_id='s', volume_m3=0.2e-6)
        r.step(70)
        self.assertEqual(r.read('rack.state'), 'inside')                         # automatic retraction after the preset time
        CHECKS.append(dict(check='rack', status='passed'))

    def test_sample_compartment_door(self):
        r = Rig()
        r.call(r.A, 'power.set', enabled=True)
        r.step(35)
        r.call(r.A, 'sample_compartment.open_door', 'POWER_ON')
        r.call(r.A, 'power.set', enabled=False)
        r.call(r.A, 'sample_compartment.open_door')
        r.step(3)
        self.assertGreater(r.read('sample_compartment.door_angle_rad'), 1.7)
        r.call(r.A, 'sample_compartment.clean')
        r.call(r.A, 'power.set', 'SAMPLE_COMPARTMENT_OPEN', enabled=True)
        r.call(r.A, 'sample_compartment.close_door')
        r.step(3)
        r.call(r.A, 'power.set', enabled=True)
        CHECKS.append(dict(check='sample_door', status='passed'))


class Optics(unittest.TestCase):
    def test_dmso_bulk_and_temperature(self):
        r = Rig(seed=8)
        r.ready()
        r.call(r.A, 'solution.prepare', solution_id='d1', volume_m3=1e-6, base='hbs_ep_plus', dmso_volume_fraction=0.01)
        r.rack(items=[('R1B1', 'd1', 400 * UL)])
        r.call(r.A, 'run.manual_start', run_id='bulk', flow_rate_m3_s=30 * ULM, flow_path='1', data_rate_hz=1)
        r.step(30)
        r.call(r.A, 'run.command', command=dict(command='sample_injection', position='R1B1', contact_s=60.0))
        r.step(120)
        rows = r.call(r.A, 'data.report_points', run_id='bulk')['rows']
        rel = next(x for x in rows if x['label'] == 'sample:binding' and x['curve'] == '1')['relative_ru']
        self.assertAlmostEqual(rel / 1200.0, 1.0, delta=0.03)                   # Software Handbook: 1 % DMSO ~ 1200 RU
        r.call(r.A, 'run.stop')
        r.step(4 * 3600)                                                        # docking drift of the new chip decays
        R0 = r.read('response.ru')['1']
        r.call(r.A, 'temperature.set', analysis_k=299.15)
        r.until(lambda: r.h._stable(), 7200)
        R1 = r.read('response.ru')['1']
        self.assertTrue(-160 < R1 - R0 < -90, R1 - R0)                          # water dn/dT ~ -1.2e-4 /K
        CHECKS.append(dict(check='dmso_bulk', status='passed', relative_ru=rel, per_K=R1 - R0))


class Immobilization(unittest.TestCase):
    def test_getting_started_amine_coupling(self):
        r = Rig(seed=21)
        r.ready()
        r.gs_kit()
        r.call(r.A, 'solution.prepare', solution_id='lig50', volume_m3=400 * UL, base='acetate_5_0', sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=12 * UL)])
        res = r.amine(4, 'lig50')
        im = res['results']['immobilization'][0]
        self.assertGreater(im['response_final_ru'], 8000.0)                     # Getting Started: > 8000 RU satisfactory
        self.assertGreater(im['response_bound_ru'], 0.0)
        ins = r.call(r.E, 'surface.inspect')
        f4 = ins['flow_cells'][3]
        self.assertEqual(f4['ligands'][0]['species'], GS_L)
        self.assertAlmostEqual(f4['active_esters'], 0.0, delta=1e-3)
        self.assertEqual(ins['flow_cells'][2]['ligands'], [])
        CHECKS.append(dict(check='gs_amine', status='passed', final_ru=im['response_final_ru']))

    def test_ph_trend_and_scouting(self):
        finals = {}
        for ph, base in ((4.0, 'acetate_4_0'), (5.0, 'acetate_5_0'), (5.5, 'acetate_5_5')):
            r = Rig(seed=22)
            r.ready()
            r.gs_kit()
            r.call(r.A, 'solution.prepare', solution_id='lig', volume_m3=400 * UL, base=base, sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=12 * UL)])
            finals[ph] = r.amine(2, 'lig')['results']['immobilization'][0]['response_final_ru']
        self.assertLess(finals[4.0], finals[5.0])                               # Getting Started: pH 4.0 lowest immobilization level
        self.assertLess(finals[5.0], finals[5.5])                               # pH 5.5 higher than 5.0
        r = Rig(seed=23)
        r.ready()
        r.step(3 * 3600)                                                        # docking drift of the new chip decays
        r.gs_kit()
        items = []
        for i, base in enumerate(('acetate_5_5', 'acetate_5_0', 'acetate_4_5', 'acetate_4_0')):
            r.call(r.A, 'solution.prepare', solution_id=f'p{i}', volume_m3=300 * UL, base=base, sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=9 * UL)])
            items.append((f'R1B{i + 1}', f'p{i}', 250 * UL))
        r.call(r.A, 'solution.prepare', solution_id='naoh', volume_m3=1e-6, base='naoh_50mm')
        items.append(('R1A1', 'naoh', 600 * UL))
        r.rack(items=items)
        op = r.call(r.A, 'wizard.ph_scouting', run_id='scout', fc=2, ligand_positions=['R1B1', 'R1B2', 'R1B3', 'R1B4'], wash_position='R1A1')['operation_id']
        r.until(lambda: r.h.operations[op]['phase'] != 'running', 7200, 30)
        pc = [x['preconcentration_ru'] for x in r.h.operations[op]['result']['results']['ph_scouting']]
        self.assertTrue(pc[0] < pc[1] < pc[2] < pc[3], pc)                       # attraction increases with decreasing pH
        self.assertLess(max(abs(x['residual_ru']) for x in r.h.operations[op]['result']['results']['ph_scouting']), 50.0)   # dissociates completely
        CHECKS.append(dict(check='ph_trend', status='passed', finals=finals, preconc=pc))

    def test_aim_for_level(self):
        r = Rig(seed=24)
        r.ready()
        r.gs_kit()
        r.call(r.A, 'solution.prepare', solution_id='lig10', volume_m3=400 * UL, base='acetate_5_0', sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=4 * UL)])
        r.call(r.A, 'solution.prepare', solution_id='naoh', volume_m3=1e-6, base='naoh_50mm')
        res = r.amine(4, 'lig10', mode='aim', target=1200.0, wash='naoh')
        im = res['results']['immobilization'][0]
        self.assertTrue(im['target_reached'], im)
        self.assertAlmostEqual(im['response_bound_ru'] / 1200.0, 1.0, delta=0.25)
        lvl = r.call(r.E, 'surface.inspect')['flow_cells'][3]['ligands'][0]['level_ru']
        self.assertAlmostEqual(im['response_final_ru'] / lvl, 1.0, delta=0.06)  # the reported final level is the immobilized ligand (no large docking drift)
        CHECKS.append(dict(check='aim_level', status='passed', bound=im['response_bound_ru'], final=im['response_final_ru'], ligand=lvl))


class Kinetics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = Rig(seed=31)
        r.ready()
        r.gs_kit()
        r.call(r.A, 'solution.prepare', solution_id='lig10', volume_m3=400 * UL, base='acetate_5_0', sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=4 * UL)])
        r.call(r.A, 'solution.prepare', solution_id='naoh', volume_m3=1e-6, base='naoh_50mm')
        r.amine(4, 'lig10', mode='aim', target=1200.0, wash='naoh')
        r.kinetics_method('kin', [0, 2, 4, 8, 16, 32, 8, 0], path='3-4', ref='4-3')
        cls.r = r

    def test_kinetics_recovered(self):
        r = self.r
        res = r.call(r.A, 'eval.kinetics', run_id='kin', cycles=[3, 4, 5, 6, 7, 8], subtraction='4-3', blank_cycles=[2, 9])
        p = r.h.pairs['gs_kit_anti_b2m']
        self.assertAlmostEqual(res['ka_m3_mol_s'] / p['ka'], 1.0, delta=0.15)
        self.assertAlmostEqual(res['kd_s'] / p['kd'], 1.0, delta=0.15)
        self.assertLess(res['chi2_ru2'], 2.0)
        self.assertLess(res['u_value'], 15.0)
        st = {q['item']: q['status'] for q in res['quality']}
        self.assertEqual(st['kinetic_constants_range'], 'pass')
        ins = r.call(r.E, 'surface.inspect')
        lig = ins['flow_cells'][3]['ligands'][0]
        rmax_true = lig['level_ru'] * rt.SHELF_MW(GS_A) / rt.SHELF_MW(GS_L) * 2 * lig['activity']
        self.assertAlmostEqual(res['rmax_ru'] / rmax_true, 1.0, delta=0.15)
        self.assertTrue(10.0 < rmax_true < 80.0, rmax_true)                     # Getting Started: 1200 RU -> Rmax ~30 RU
        CHECKS.append(dict(check='kinetics_recovered', status='passed', ka=res['ka_m3_mol_s'], kd=res['kd_s'], rmax=res['rmax_ru'], rmax_true=rmax_true))

    def test_affinity_and_sensorgram(self):
        r = self.r
        aff = r.call(r.A, 'eval.affinity', run_id='kin', cycles=[3, 4, 5, 6, 7, 8], subtraction='4-3', blank_cycles=[2, 9])
        self.assertTrue(aff['points'])
        sg = r.call(r.A, 'data.sensorgram', run_id='kin', cycle=7, curves=['4-3', '3', '4'], max_points=500)
        self.assertEqual(sg['keywords']['concentration_mol_m3'], 32e-6)
        self.assertGreater(max(sg['curves']['4-3']), 10.0)
        runs = r.call(r.A, 'runs.list')['runs']
        self.assertTrue(any(x['run_id'] == 'kin' and x['state'] == 'completed' for x in runs))
        CHECKS.append(dict(check='affinity_sensorgram', status='passed', KD=aff['KD_mol_m3']))


class MorePhysics(unittest.TestCase):
    def test_cfca_concentration(self):
        r = Rig(seed=41)
        r.ready()
        r.gs_kit()
        r.call(r.A, 'solution.prepare', solution_id='lig50', volume_m3=400 * UL, base='acetate_5_0', sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=12 * UL)])
        r.amine(2, 'lig50')
        stock_c = r.h.solutions['gs_analyte_b2m']['label'][0]['concentration_mol_m3']
        r.call(r.A, 'solution.prepare', solution_id='unk', volume_m3=1.5e-6, base='hbs_ep_plus', sources=[dict(source_id='gs_analyte_b2m', volume_m3=1.5e-6 * 5e-6 / stock_c)])
        true_c = r.h.solutions['unk']['comp']['species'][GS_A]
        r.call(r.A, 'solution.prepare', solution_id='gly', volume_m3=2e-6, base='glycine_2_5')
        r.rack(items=[('R1B1', 'unk', 1.4e-6), ('R1A9', 'gly', 2e-6)])
        cyc = []
        for f in (5 * ULM, 100 * ULM):
            cyc.append(dict(purpose='sample', commands=[dict(type='sample', position='R1B1', contact_s=36.0, dissociation_s=30.0, flow_rate_m3_s=f),
                                                       dict(type='regeneration', position='R1A9', contact_s=30.0, flow_rate_m3_s=30 * ULM, stabilization_s=30.0)]))
        op = r.call(r.A, 'method.run', run_id='cfca', data_rate_hz=10, detection='dual', flow_path='1-2', reference_subtraction='2-1', cycles=cyc)['operation_id']
        r.until(lambda: r.h.operations[op]['phase'] != 'running', 7200, 30)
        res = r.call(r.A, 'eval.concentration', run_id='cfca', mode='cfca', subtraction='2-1', sample_cycles=[1, 2], mw_da=11800.0)
        c = res['samples'][0]['concentration_mol_m3']
        self.assertIsNotNone(c, res)
        self.assertAlmostEqual(c / true_c, 1.0, delta=0.25)
        CHECKS.append(dict(check='cfca', status='passed', measured=c, true=true_c))

    def test_regeneration_and_damage(self):
        r = Rig(seed=42)
        r.ready()
        r.gs_kit()
        r.call(r.A, 'solution.prepare', solution_id='lig50', volume_m3=400 * UL, base='acetate_5_0', sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=12 * UL)])
        r.amine(2, 'lig50')
        stock_c = r.h.solutions['gs_analyte_b2m']['label'][0]['concentration_mol_m3']
        r.call(r.A, 'solution.prepare', solution_id='an', volume_m3=1.5e-6, base='hbs_ep_plus', sources=[dict(source_id='gs_analyte_b2m', volume_m3=1.5e-6 * 50e-6 / stock_c)])
        for sid, base in (('g30', 'glycine_3_0'), ('g25', 'glycine_2_5'), ('sds', 'sds_0_5')):
            r.call(r.A, 'solution.prepare', solution_id=sid, volume_m3=1e-6, base=base)
        r.rack(items=[('R1B1', 'an', 1.4e-6), ('R1A7', 'g30', 0.9e-6), ('R1A8', 'g25', 0.9e-6), ('R1A9', 'sds', 0.9e-6)])
        r.call(r.A, 'run.manual_start', run_id='reg', flow_rate_m3_s=30 * ULM, flow_path='1-2', reference_subtraction='2-1')

        def bound():
            return sum(v for k, v in r.read('truth.complexes_ru').items() if k.startswith('2:'))
        out = {}
        for pos in ('R1A7', 'R1A8'):
            r.call(r.A, 'run.command', command=dict(command='sample_injection', position='R1B1', contact_s=60.0))
            r.call(r.A, 'run.command', command=dict(command='wait', wait_s=60.0))
            r.step(150)
            b0 = bound()
            r.call(r.A, 'run.command', command=dict(command='regeneration_injection', position=pos, contact_s=30.0))
            r.step(70)
            out[pos] = bound() / b0
        self.assertGreater(out['R1A7'], 0.2)                                    # glycine pH 3.0: too mild for the kit antibody (residual)
        self.assertGreater(out['R1A7'], 5 * out['R1A8'])
        self.assertLess(out['R1A8'], 0.05)                                      # glycine pH 2.5 (Getting Started regeneration): complete
        act0 = list(r.read('truth.ligand_activity').values())[0]
        r.call(r.A, 'run.command', command=dict(command='regeneration_injection', position='R1A9', contact_s=30.0))
        r.step(70)
        act1 = list(r.read('truth.ligand_activity').values())[0]
        self.assertLess(act1, act0 * 0.999)                                     # harsh SDS damages the ligand
        r.call(r.A, 'run.stop')
        CHECKS.append(dict(check='regeneration', status='passed', remaining=out, activity=[act0, act1]))

    def test_solvent_correction(self):
        r = Rig(seed=43)
        r.ready()
        r.gs_kit()
        r.call(r.A, 'solution.prepare', solution_id='lig50', volume_m3=400 * UL, base='acetate_5_0', sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=12 * UL)])
        r.amine(2, 'lig50', contact=600.0)
        items = []
        for i, pct in enumerate((4.5, 4.8, 5.1, 5.4, 5.8, 5.2, 4.7)):
            r.call(r.A, 'solution.prepare', solution_id=f'sc{i}', volume_m3=600 * UL, base='hbs_ep_plus', dmso_volume_fraction=pct / 100.0)
            items.append((f'R1B{i + 1}', f'sc{i}', 400 * UL))
        r.call(r.A, 'solution.prepare', solution_id='rb5', volume_m3=1.0e-3, base='hbs_ep_plus_dmso_5')
        r.call(r.A, 'bottle.place', slot='buffer_a', solution_id='rb5', volume_m3=0.95e-3)
        r.call(r.A, 'flow.prime')
        r.step(400)
        r.rack(items=items)
        cyc = [dict(purpose='solvent_correction' if i < 5 else 'sample', commands=[dict(type='sample', position=f'R1B{i + 1}', contact_s=30.0, dissociation_s=30.0,
                                                                                        flow_rate_m3_s=30 * ULM)]) for i in range(7)]
        op = r.call(r.A, 'method.run', run_id='sc', data_rate_hz=1, detection='dual', flow_path='1-2', reference_subtraction='2-1', cycles=cyc)['operation_id']
        r.until(lambda: r.h.operations[op]['phase'] != 'running', 7200, 30)
        res = r.call(r.A, 'eval.solvent_correction', run_id='sc', correction_cycles=[1, 2, 3, 4, 5], sample_cycles=[6, 7], active_fc=2, reference_fc=1)
        self.assertLess(res['chi2_ru2'], 2.0)
        for row in res['corrected']:
            self.assertGreater(abs(row['uncorrected_ru']), 2.0 * abs(row['corrected_ru']), row)
        CHECKS.append(dict(check='solvent_correction', status='passed', rows=res['corrected']))

    def test_thermodynamics(self):
        ent = []
        p = rt.LIB_PAIRS['gs_kit_anti_b2m']
        for T in (288.15, 298.15, 308.15):
            ka, kd = rt.eyring(p['ka'], p['dHa'], p['T0'], T), rt.eyring(p['kd'], p['dHd'], p['T0'], T)
            ent.append(dict(temperature_k=T, KD_mol_m3=kd / ka, ka_m3_mol_s=ka, kd_s=kd))
        r = Rig()
        res = r.call(r.A, 'eval.thermodynamics', entries=ent)
        self.assertAlmostEqual(res['dH_j_mol'], p['dHa'] - p['dHd'], delta=2000.0)
        self.assertAlmostEqual(res['association']['dH_j_mol'], p['dHa'], delta=500.0)
        self.assertAlmostEqual(res['dissociation']['dH_j_mol'], p['dHd'], delta=500.0)
        CHECKS.append(dict(check='thermodynamics', status='passed', dH=res['dH_j_mol']))

    def test_capture_nta_and_sa(self):
        r = Rig(seed=44)
        r.ready(chip='NTA', chip_id='nta1', buffer='hbs_n')                      # EDTA-free running buffer (EDTA strips the nickel)
        r.call(r.E, 'species.define', species_id='his_ligand', name='His10-tagged receptor', mw_da=45000.0, isoelectric_point=6.0, tags=['his10'])
        r.call(r.E, 'species.define', species_id='small_an', name='analyte', mw_da=20000.0, isoelectric_point=5.0)
        r.call(r.E, 'interaction.define', interaction_id='hl', ligand='his_ligand', analyte='small_an', ka_m3_mol_s=500.0, kd_s=1e-3)
        r.call(r.E, 'stock.provide', stock_id='hl_stock', volume_m3=1e-6, base='hbs_n', species=[dict(species_id='his_ligand', concentration_mol_m3=1e-3)])
        r.call(r.E, 'kit.provide', kit='his_capture')
        r.call(r.A, 'solution.prepare', solution_id='hl', volume_m3=500 * UL, base='hbs_n', sources=[dict(source_id='hl_stock', volume_m3=10 * UL)])
        r.rack(items=[('R1B1', 'hl', 400 * UL), ('R1A1', 'nickel_0_5mm', 400 * UL), ('R1A2', 'edta_350mm', 600 * UL)])
        op = r.call(r.A, 'wizard.immobilization', run_id='cap', flow_cells=[dict(fc=2, chemistry='his_capture', mode='contact', ligand_position='R1B1', contact_s=120.0,
                                                                                 nickel_position='R1A1')])['operation_id']
        r.until(lambda: r.h.operations[op]['phase'] != 'running', 7200, 30)
        lvl = r.call(r.E, 'surface.inspect')['flow_cells'][1]['ligands'][0]['level_ru']
        self.assertGreater(lvl, 100.0)
        self.assertGreater(r.h.operations[op]['result']['results']['immobilization'][0]['response_final_ru'], 100.0)
        r.step(1800)
        lvl2 = r.call(r.E, 'surface.inspect')['flow_cells'][1]['ligands'][0]['level_ru']
        self.assertTrue(0.5 * lvl < lvl2 < lvl, (lvl, lvl2))                     # His10 capture decays slowly
        r.call(r.A, 'run.manual_start', run_id='strip', flow_rate_m3_s=10 * ULM, flow_path='2')
        r.call(r.A, 'run.command', command=dict(command='regeneration_injection', position='R1A2', contact_s=60.0))
        r.step(120)
        ligs = r.call(r.E, 'surface.inspect')['flow_cells'][1]['ligands']
        self.assertTrue(not ligs or ligs[0]['level_ru'] < 0.05 * lvl2)          # EDTA strips nickel and the captured ligand
        r.call(r.A, 'run.stop')
        r.call(r.A, 'wizard.immobilization', 'CHEMISTRY_NOT_SUPPORTED', run_id='bad', flow_cells=[dict(fc=1, chemistry='amine', mode='blank', edc_position='R1A1',
                                                                                                        nhs_position='R1A1', mix_position='R1C1', ethanolamine_position='R1A1')])
        CHECKS.append(dict(check='capture_nta', status='passed', levels=[lvl, lvl2]))


class Maintenance(unittest.TestCase):
    def test_normalize_system_check_maintenance_shutdown(self):
        r = Rig(seed=51)
        r.ready()
        c = r.h._chip_obj()
        g0 = np.std(c['gains'])
        r.call(r.E, 'kit.provide', kit='maintenance')
        r.call(r.A, 'detector.normalize', 'POSITION_EMPTY', position='R2A1')
        r.call(r.A, 'solution.prepare', solution_id='hbsn', volume_m3=1e-5, base='hbs_n')
        r.rack(items=[('R1A1', 'bianormalizing', 1.0e-6), ('R1A2', 'biatest', 1.0e-6), ('R1A3', 'biadesorb_1', 1.0e-6), ('R1A4', 'biadesorb_2', 1.0e-6)])
        op = r.call(r.A, 'detector.normalize', position='R1A1')['operation_id']
        r.step(500)
        self.assertEqual(r.h.operations[op]['phase'], 'succeeded')
        self.assertTrue(r.read('chip.normalized'))
        self.assertLess(np.std(r.h._chip_obj()['gains']), 0.2 * g0)
        r.call(r.A, 'door.set', door='right_pump', open=True)
        r.step(2)
        r.call(r.A, 'clamp.set', closed=False)
        op = r.call(r.A, 'system.check', position='R1A2')['operation_id']
        r.step(6100)                                                            # System Check about 100 min (Operating Instructions App. A)
        res = r.read('system_check.last')
        self.assertFalse(res['passed'])
        self.assertIn('reagent_pump', res['failed_tests'])
        self.assertIn('buffer_selector_b', res['failed_tests'])
        r.call(r.A, 'clamp.set', closed=True)
        r.call(r.A, 'temperature.set', analysis_k=291.15)
        r.call(r.A, 'maintenance.run', 'DESORB_BELOW_20C', procedure='desorb', positions=dict(biadesorb_1='R1A3', biadesorb_2='R1A4'))
        r.call(r.A, 'temperature.set', analysis_k=298.15)
        r.h.contamination = 0.5
        op = r.call(r.A, 'maintenance.run', procedure='desorb', positions=dict(biadesorb_1='R1A3', biadesorb_2='R1A4'))['operation_id']
        r.step(1600)
        self.assertEqual(r.h.operations[op]['phase'], 'succeeded')
        self.assertLess(r.read('truth.contamination'), 0.2)
        r.call(r.A, 'system.shutdown', 'RACK_NOT_REMOVED')
        r.call(r.A, 'rack.eject')
        r.step(8)
        for pos in list(r.h.rack['positions']):
            r.call(r.A, 'rack.remove', position=pos)
        r.call(r.A, 'rack.configure', config='empty', plate='none')            # take the racks off the tray
        self.assertIsNone(r.read('rack.config'))
        r.call(r.A, 'solution.prepare', solution_id='w2', volume_m3=0.6e-3, base='water')
        r.call(r.A, 'solution.prepare', solution_id='et', volume_m3=0.25e-3, base='ethanol_70')
        r.call(r.A, 'bottle.place', slot='buffer_a', solution_id='w2', volume_m3=0.5e-3)
        r.call(r.A, 'bottle.place', slot='buffer_b', solution_id='et', volume_m3=0.2e-3)
        op = r.call(r.A, 'system.shutdown')['operation_id']
        r.step(1300)
        self.assertEqual(r.read('flow.state'), 'dry')
        self.assertIn('CLAMP_RELEASE_REQUIRED', r.read('warnings.active'))
        r.call(r.A, 'clamp.set', closed=False)
        r.step(5)
        self.assertNotIn('CLAMP_RELEASE_REQUIRED', r.read('warnings.active'))
        CHECKS.append(dict(check='maintenance', status='passed'))


class ManualRun(unittest.TestCase):
    def test_queue_commands(self):
        r = Rig(seed=61)
        r.ready()
        r.call(r.A, 'solution.prepare', solution_id='b', volume_m3=1e-6, base='hbs_ep_plus')
        r.rack(items=[('R1B1', 'b', 600 * UL)])
        r.call(r.A, 'run.command', 'NO_MANUAL_RUN', command=dict(command='wait', wait_s=10.0))
        r.call(r.A, 'run.manual_start', run_id='q', flow_rate_m3_s=10 * ULM, flow_path='1-2', reference_subtraction='2-1')
        r.call(r.A, 'run.command', 'INJECTION_VOLUME_OUT_OF_RANGE', command=dict(command='sample_injection', position='R1B1', contact_s=6000.0))
        r.call(r.A, 'run.command', 'FLOW_PATH_NOT_ALLOWED', command=dict(command='flow_path', flow_path='1-2-3-4'))
        r.call(r.A, 'run.command', command=dict(command='wait', wait_s=100.0))
        r.call(r.A, 'run.command', command=dict(command='sample_injection', position='R1B1', contact_s=60.0))
        r.call(r.A, 'run.command', command=dict(command='report_point', label='after'))
        r.call(r.A, 'run.command', command=dict(command='new_cycle', flow_path='3-4', reference_subtraction='4-3'))
        r.step(1)
        self.assertEqual(r.read('run.queue_length'), 3)
        r.call(r.A, 'run.queue_delete', index=3)
        r.call(r.A, 'run.stop_command')
        r.step(1)
        self.assertEqual(r.read('run.current_command'), 'inject:sample')
        r.call(r.A, 'run.pause')
        r.step(200)
        self.assertEqual(r.read('run.state'), 'paused')
        r.call(r.A, 'run.resume')
        r.step(100)
        cyc = r.h.runs['q']['cycles']
        self.assertEqual(len(cyc), 1)
        self.assertEqual(len(cyc[0]['injections']), 1)
        self.assertEqual(cyc[0]['report_points'][0]['label'], 'after')
        r.call(r.A, 'run.stop')
        self.assertEqual(r.h.runs['q']['state'], 'completed')
        self.assertEqual(r.read('flow.state'), 'standby')
        CHECKS.append(dict(check='manual_queue', status='passed'))

    def test_cancel_and_power_loss(self):
        r = Rig(seed=62)
        r.ready()
        r.call(r.A, 'solution.prepare', solution_id='pbs2', volume_m3=1e-3, base='pbs')
        r.call(r.A, 'bottle.place', slot='buffer_a', solution_id='pbs2', volume_m3=0.9e-3)
        op = r.call(r.A, 'flow.prime')['operation_id']
        r.step(60)
        r.A.cancel(op, 'c1')
        self.assertEqual(r.h.operations[op]['phase'], 'cancelled')
        self.assertEqual(r.read('flow.state'), 'standby')
        r.call(r.A, 'solution.prepare', solution_id='b', volume_m3=1e-6, base='hbs_ep_plus')
        r.rack(items=[('R1B1', 'b', 600 * UL)])
        cyc = [dict(purpose='sample', commands=[dict(type='sample', position='R1B1', contact_s=60.0, dissociation_s=300.0, flow_rate_m3_s=10 * ULM)])] * 3
        op = r.call(r.A, 'method.run', run_id='mm', data_rate_hz=1, detection='single', flow_path='1', cycles=cyc, ignore_temperature=True)['operation_id']
        r.step(200)
        r.call(r.E, 'fault.inject', fault='power_loss', active=True)
        self.assertEqual(r.h.runs['mm']['state'], 'failed')
        self.assertEqual(r.h.operations[op]['error'], 'POWER_OFF_INTERRUPT')
        self.assertTrue(r.h.runs['mm']['cycles'][0]['t'])
        CHECKS.append(dict(check='cancel_power_loss', status='passed'))


class Misc(unittest.TestCase):
    def test_queries_light_buffer_select_standby_bottles(self):
        r = Rig(seed=63)
        r.ready()
        r.call(r.A, 'sample_compartment.set_light', on=True)
        self.assertTrue(r.read('sample_compartment.light'))
        r.call(r.A, 'solution.prepare', solution_id='b2', volume_m3=0.3e-3, base='pbs')
        r.call(r.A, 'bottle.place', slot='buffer_b', solution_id='b2', volume_m3=0.2e-3)
        r.call(r.A, 'flow.select_buffer', tube='buffer_b')
        self.assertEqual(r.read('flow.selected_tube'), 'buffer_b')
        r.call(r.A, 'flow.stop_standby')
        r.call(r.A, 'flow.standby')
        self.assertEqual(r.read('flow.state'), 'standby')
        r.call(r.A, 'bottle.remove', slot='buffer_b')
        ev = r.call(r.A, 'events.read', since_s=0.0)['events']
        self.assertTrue(any(e['code'] == 'CHIP_DOCKED' for e in ev))
        pv = r.call(r.A, 'provenance.read')
        self.assertGreaterEqual(len(pv['species']), 100)
        self.assertNotIn('ka', json.dumps(pv['species']))
        CHECKS.append(dict(check='misc_queries', status='passed'))

    def test_calibrated_concentration(self):
        r = Rig(seed=64)
        r.ready()
        r.gs_kit()
        r.call(r.A, 'solution.prepare', solution_id='lig50', volume_m3=400 * UL, base='acetate_5_0', sources=[dict(source_id='gs_ligand_anti_b2m', volume_m3=12 * UL)])
        r.amine(2, 'lig50')
        stock_c = r.h.solutions['gs_analyte_b2m']['label'][0]['concentration_mol_m3']
        items, std = [], [5, 10, 20, 40, 80]
        for i, c in enumerate(std + [25]):
            r.call(r.A, 'solution.prepare', solution_id=f'c{i}', volume_m3=600 * UL, base='hbs_ep_plus',
                   sources=[dict(source_id='gs_analyte_b2m', volume_m3=c * 1e-6 * 600 * UL / stock_c)])
            items.append((f'R1B{i + 1}', f'c{i}', 400 * UL))
        r.call(r.A, 'solution.prepare', solution_id='gly', volume_m3=3e-6, base='glycine_2_5')
        items.append(('R1A9', 'gly', 3e-6))
        r.rack(items=items)
        cyc = [dict(purpose='sample', commands=[dict(type='sample', position=f'R1B{i + 1}', contact_s=120.0, dissociation_s=30.0, flow_rate_m3_s=10 * ULM),
                                                dict(type='regeneration', position='R1A9', contact_s=30.0, flow_rate_m3_s=30 * ULM, stabilization_s=30.0)]) for i in range(6)]
        op = r.call(r.A, 'method.run', run_id='conc', data_rate_hz=1, detection='dual', flow_path='1-2', reference_subtraction='2-1', cycles=cyc)['operation_id']
        r.until(lambda: r.h.operations[op]['phase'] != 'running', 14400, 30)
        res = r.call(r.A, 'eval.concentration', run_id='conc', mode='calibration', subtraction='2-1', standard_cycles=[1, 2, 3, 4, 5],
                     standard_concentrations_mol_m3=[c * 1e-6 for c in std], sample_cycles=[6])
        self.assertAlmostEqual(res['samples'][0]['concentration_mol_m3'] / 25e-6, 1.0, delta=0.12)
        CHECKS.append(dict(check='calibrated_concentration', status='passed', measured=res['samples'][0]['concentration_mol_m3']))


class ReviewRound1(unittest.TestCase):
    """Regressions for the independent review round 1 (runtime F-01..F-12, contract C-01, C-02, C-05, C-06)."""

    def test_species_mw_isolated_between_instances(self):                       # F-01
        a, b = Rig(seed=71, instance='t200_a'), Rig(seed=71, instance='t200_b')
        a.call(a.E, 'species.define', species_id='cmpd_q', name='compound Q', mw_da=300.0, kind='small_molecule')
        a.call(a.E, 'stock.provide', stock_id='q_stock', volume_m3=1e-6, base='hbs_ep_plus', species=[dict(species_id='cmpd_q', concentration_mol_m3=1.0)])
        comp = a.h.solutions['q_stock']['comp']
        dn0 = rt.liquid_props(comp, a.h._mw)['dn']
        b.call(b.E, 'species.define', species_id='cmpd_q', name='compound Q', mw_da=30000.0)
        self.assertEqual(rt.liquid_props(comp, a.h._mw)['dn'], dn0)            # the other instance's definition does not leak
        self.assertEqual((a.h._mw('cmpd_q'), b.h._mw('cmpd_q')), (300.0, 30000.0))
        self.assertEqual(rt.SHELF_MW('cmpd_q'), 50000.0)                         # module table holds library species only
        CHECKS.append(dict(check='mw_isolation', status='passed'))

    def test_blank_immobilization_small_after_docking(self):                    # F-02
        r = Rig(seed=72)
        r.ready()
        r.gs_kit()
        r.rack(items=[('R1A1', 'edc_0_4m', 150 * UL), ('R1A2', 'nhs_0_1m', 150 * UL), ('R1A4', 'ethanolamine_1m', 150 * UL)])
        op = r.call(r.A, 'wizard.immobilization', run_id='blank', flow_cells=[dict(fc=1, chemistry='amine', mode='blank', edc_position='R1A1', nhs_position='R1A2',
                                                                                 mix_position='R1A3', ethanolamine_position='R1A4')])['operation_id']
        r.until(lambda: r.h.operations[op]['phase'] != 'running', 14400, 30.0)
        im = r.h.operations[op]['result']['results']['immobilization'][0]
        self.assertLess(abs(im['response_final_ru']), 80.0, im)                 # activated + deactivated surface without ligand
        CHECKS.append(dict(check='blank_immobilization', status='passed', final=im['response_final_ru']))

    def test_sample_reaches_waste_and_ledger_closes(self):                      # F-03
        r = Rig(seed=73)
        r.ready()
        r.call(r.A, 'solution.prepare', solution_id='b', volume_m3=1e-6, base='hbs_ep_plus')
        r.rack(items=[('R1B1', 'b', 600 * UL)])
        r.call(r.A, 'run.manual_start', run_id='w', flow_rate_m3_s=30 * ULM, flow_path='1-2')
        r.call(r.A, 'run.command', command=dict(command='sample_injection', position='R1B1', contact_s=120.0))
        r.step(300)
        r.call(r.A, 'run.stop')
        led = r.call(r.E, 'ledger.read')['ledger']
        self.assertGreater(led['sample_aspirated_m3'], 50 * UL)
        self.assertAlmostEqual(led['buffer_used_m3'] + led['water_used_m3'] + led['sample_aspirated_m3'], led['waste_added_m3'] + led['waste_spilled_m3'], delta=1e-12)
        CHECKS.append(dict(check='sample_to_waste', status='passed'))

    def test_buffer_empty_cleared_by_refill(self):                              # F-04
        r = Rig(seed=74)
        r.ready()
        r.call(r.A, 'solution.prepare', solution_id='rb2', volume_m3=1e-3, base='hbs_ep_plus')
        r.call(r.A, 'bottle.place', slot='buffer_a', solution_id='rb2', volume_m3=1e-5)
        r.call(r.A, 'run.manual_start', run_id='e', flow_rate_m3_s=100 * ULM, flow_path='1')
        r.step(7200)
        self.assertIn('BUFFER_EMPTY', r.read('errors.active'))
        r.call(r.A, 'bottle.place', slot='buffer_a', solution_id='rb2', volume_m3=0.9e-3)
        self.assertNotIn('BUFFER_EMPTY', r.read('errors.active'))
        r.call(r.A, 'flow.prime')
        r.step(400)
        self.assertEqual(r.read('flow.state'), 'standby')
        CHECKS.append(dict(check='buffer_empty_cleared', status='passed'))

    def test_sensorgram_out_of_range_is_null(self):                             # F-05
        r = Rig(seed=75)
        r.ready()
        r.call(r.E, 'kit.provide', kit='maintenance')
        r.rack(items=[('R1A1', 'bianormalizing', 1.0e-6)])
        r.call(r.A, 'run.manual_start', run_id='g', flow_rate_m3_s=10 * ULM, flow_path='1', data_rate_hz=1)
        r.step(20)
        r.call(r.A, 'run.command', command=dict(command='sample_injection', position='R1A1', contact_s=60.0))
        r.step(150)
        r.call(r.A, 'run.stop')
        ys = r.call(r.A, 'data.sensorgram', run_id='g', cycle=1, curves=['1'])['curves']['1']
        self.assertTrue(any(v is None for v in ys))                              # 70 % glycerol: out of range
        self.assertFalse(any(v == 0.0 for v in ys))
        CHECKS.append(dict(check='sensorgram_null', status='passed'))

    def test_tool_durations_from_the_manual(self):                              # F-06
        self.assertEqual((rt.MAINT_S['desorb'], rt.MAINT_S['desorb_and_sanitize'], rt.MAINT_S['wash_buffer_tubing'], rt.MAINT_S['flow_system_wash'],
                          rt.MAINT_S['system_check']), (1200.0, 3600.0, 1800.0, 240.0, 6000.0))
        CHECKS.append(dict(check='tool_durations', status='passed'))

    def test_system_error_blocks_and_aborts_runs(self):                         # F-07
        r = Rig(seed=76)
        r.ready()
        r.call(r.A, 'run.manual_start', run_id='m1', flow_rate_m3_s=10 * ULM, flow_path='1')
        r.step(5)
        r.call(r.E, 'fault.inject', fault='system_error', active=True)
        self.assertEqual(r.h.runs['m1']['state'], 'failed')
        self.assertIn('SYSTEM_ERROR', r.read('errors.active'))
        r.call(r.A, 'run.manual_start', 'SYSTEM_ERROR', run_id='m2', flow_rate_m3_s=10 * ULM, flow_path='1')
        r.call(r.A, 'flow.prime', 'SYSTEM_ERROR')
        r.call(r.E, 'fault.inject', fault='system_error', active=False)
        r.call(r.A, 'run.manual_start', run_id='m2', flow_rate_m3_s=10 * ULM, flow_path='1')
        r.call(r.A, 'run.stop')
        CHECKS.append(dict(check='system_error', status='passed'))

    def test_chip_lamp_flashes_with_an_ejected_chip_in_the_port(self):          # F-08
        r = Rig()
        r.ready(prime=False)
        r.call(r.A, 'chip.eject')
        r.step(35)
        seen = set()
        for _ in range(4):
            r.h.step(0.5)
            seen.add(r.read('lamp.sensor_chip'))
        self.assertEqual(seen, {True, False})                                   # Operating Instructions 3.2: flashing when inserted but not docked
        r.call(r.A, 'chip.remove')
        r.step(1)
        self.assertFalse(r.read('lamp.sensor_chip'))
        CHECKS.append(dict(check='chip_lamp_ejected', status='passed'))

    def test_water_running_buffer_readable_at_45c(self):                        # F-09
        r = Rig(seed=77, room=298.15)
        r.ready(buffer='water')
        r.call(r.A, 'temperature.set', analysis_k=318.15)
        r.until(lambda: r.read('temperature.analysis_stable'), 5 * 3600, 60.0)
        self.assertIsNotNone(r.read('response.ru')['1'])
        CHECKS.append(dict(check='water_45c', status='passed'))

    def test_power_loss_halts_tray_and_cover_and_blanks_response(self):         # C-06, F-11, F-12
        r = Rig()
        r.ready(prime=False)
        r.call(r.A, 'rack.eject')
        r.step(2)
        self.assertEqual(r.read('rack.state'), 'ejecting')
        x = r.read('rack.carriage_position_m')
        r.call(r.E, 'fault.inject', fault='power_loss', active=True)
        self.assertEqual(r.read('rack.state'), 'stopped')
        self.assertTrue(all(v is None for v in r.read('response.ru').values()))
        r.step(30)
        self.assertEqual(r.read('rack.carriage_position_m'), x)                  # the tray stops where it is
        self.assertTrue(r.h._idle())                                            # nothing moves without power: 10 s idle blocks
        r.call(r.E, 'fault.inject', fault='power_loss', active=False)
        r.step(35)
        r.call(r.A, 'rack.retract')
        r.step(8)
        self.assertEqual(r.read('rack.state'), 'inside')
        r.call(r.A, 'chip.eject')
        r.step(35)
        r.call(r.A, 'chip.remove')
        r.call(r.A, 'chip.close_cover')
        r.step(2)
        r.call(r.A, 'chip.open_port')
        r.h.step(0.3)
        c = r.read('chip.port_cover_angle_rad')
        self.assertTrue(0.0 < c < 1.2, c)
        r.call(r.E, 'fault.inject', fault='power_loss', active=True)
        r.step(5)
        self.assertEqual(r.read('chip.port_cover_angle_rad'), c)                # the cover motor stops
        r.call(r.A, 'chip.close_cover')
        r.step(2)
        self.assertEqual(r.read('chip.port_cover_angle_rad'), 0.0)              # closing by hand works without power
        CHECKS.append(dict(check='power_loss_halts', status='passed'))

    def test_bottle_reading_is_a_graduation_reading(self):                      # C-02
        r = Rig()
        r.ready(prime=False)
        r.step(3600)
        v, t = r.read('bottle.volumes_m3'), r.read('truth.bottle_volumes_m3')
        for slot, step in rt.BOTTLE_GRADUATION_M3.items():
            if v[slot] is None:                                                 # no bottle in this position
                self.assertEqual(t[slot], 0.0)
                continue
            self.assertAlmostEqual(v[slot] / step, round(v[slot] / step), places=6)
            self.assertLessEqual(abs(v[slot] - t[slot]), 0.5 * step + 1e-12)
        self.assertNotEqual(v['buffer_a'], t['buffer_a'])
        CHECKS.append(dict(check='bottle_reading', status='passed'))


class ReviewRound2(unittest.TestCase):
    """Regressions for the independent review round 2 (runtime F2-01, contract C2-01 / C2-02)."""

    def test_only_the_emptied_tube_clears_buffer_empty(self):                  # F2-01
        r = Rig(seed=81)
        r.ready()
        r.call(r.A, 'solution.prepare', solution_id='rb3', volume_m3=1e-3, base='hbs_ep_plus')
        r.call(r.A, 'bottle.place', slot='buffer_a', solution_id='rb3', volume_m3=1e-5)
        r.call(r.A, 'run.manual_start', run_id='e', flow_rate_m3_s=100 * ULM, flow_path='1')
        r.step(7200)
        self.assertIn('BUFFER_EMPTY', r.read('errors.active'))
        r.call(r.A, 'bottle.place', slot='buffer_b', solution_id='rb3', volume_m3=2e-4)     # another tube: tube A is still empty
        self.assertIn('BUFFER_EMPTY', r.read('errors.active'))
        r.call(r.A, 'flow.select_buffer', tube='buffer_b')
        r.call(r.A, 'flow.prime')
        r.step(400)
        self.assertNotIn('BUFFER_EMPTY', r.read('errors.active'))                # primed from tube B
        CHECKS.append(dict(check='buffer_empty_tube', status='passed'))

    def test_power_loss_stops_an_opening_cover_later(self):                     # C2-01, C2-02
        meta = rt.export_cytiva_biacoret200_contract_metadata()['capabilities']
        for cap in ('power.set', 'power.press_switch', 'fault.inject', 'plant.configure'):
            later = {e['state_id'] for e in meta[cap]['state_effects'] if e.get('timing') != 'immediate'}
            self.assertIn('chip.port_cover_angle_rad', rt.ON_STEP[cap], cap)
            self.assertNotIn('door.left_pump_angle_rad', rt.ON_STEP[cap], cap)
        self.assertNotIn('rack.carriage_position_m', rt.EFFECTS['power.set'])
        self.assertIn('chip.port_cover_angle_rad', later)
        CHECKS.append(dict(check='power_cover_effect', status='passed'))


class ZDeclaredEffects(unittest.TestCase):
    """Observed changes of the immediate call and of the following steps (compared with the same host without the call) are declared."""

    def _pair(self, setup, call, steps=300.0):
        a, b = Rig(seed=71), Rig(seed=71)
        for r in (a, b):
            setup(r)
        before = a.read()
        call(a)
        after = a.read()
        imm = {k for k in after if after[k] != before[k]} - {'clock.time_s'}
        for r in (a, b):
            r.h.step(steps)
        ra, rb = a.read(), b.read()

        def differ(x, y):
            if isinstance(x, float) and isinstance(y, float):
                return abs(x - y) > 1e-9 * max(1.0, abs(x), abs(y))
            if isinstance(x, dict) and isinstance(y, dict):
                return set(x) != set(y) or any(differ(x[k], y[k]) for k in x)
            return x != y
        later = {k for k in ra if differ(ra[k], rb[k])} - {'clock.time_s'}
        return imm, later

    def test_declared_effects(self):
        def ready(r):
            r.ready()
        cases = {
            'temperature.set': (ready, lambda r: r.call(r.A, 'temperature.set', analysis_k=300.15)),
            'flow.stop_standby': (ready, lambda r: r.call(r.A, 'flow.stop_standby')),
            'door.set': (ready, lambda r: r.call(r.A, 'door.set', door='left_pump', open=True)),
            'rack.eject': (ready, lambda r: r.call(r.A, 'rack.eject')),
            'chip.eject': (ready, lambda r: r.call(r.A, 'chip.eject')),
            'run.manual_start': (ready, lambda r: r.call(r.A, 'run.manual_start', run_id='x', flow_rate_m3_s=20 * ULM, flow_path='1-2')),
        }
        bad = {}
        for cap, (setup, call) in cases.items():
            imm, later = self._pair(setup, call)
            declared = set(rt.EFFECTS.get(cap, [])) | {'warnings.active', 'operations.running', 'errors.active'}
            on = set(rt.ON_STEP.get(cap, [])) | declared
            if imm - declared or later - on:
                bad[cap] = dict(immediate=sorted(imm - declared), later=sorted(later - on))
        self.assertEqual(bad, {})
        CHECKS.append(dict(check='declared_effects', status='passed', cases=sorted(cases)))


class ZZOutputs(unittest.TestCase):
    def test_every_capability_exercised_or_listed(self):
        missing = sorted(k for k in rt.CAPS if k not in rt.UNSUPPORTED and k not in SEEN_CAPS)
        allowed = {'eval.concentration', 'power.press_switch', 'chip.remove', 'bottle.empty_waste', 'run.queue_delete'}
        self.assertTrue(set(missing) <= allowed | set(), missing)
        CHECKS.append(dict(check='capabilities_exercised', status='passed', exercised=len(SEEN_CAPS), missing=missing))


if __name__ == '__main__':
    unittest.main()
