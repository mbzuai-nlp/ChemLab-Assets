"""Host-side world for the thermal_analysis class: what the lab operator has done before the agent starts, as lab-config set-up
steps (capability calls as the operator, or "evaluator": true for host-only ones; "advance_s" lets that instrument's time run).

* DSC 2500 / TGA 5500: a sample pan on tray position 1 (indium for the DSC; the TGA uses the reference of its sample.load
  example, because the tray.place example names a reference the runtime does not know).  The DSC purge supply is
  replaced by a 10 mol N2 cylinder: the default set-up's 0.1 mol (the contract example) lasts only about 49 min at 50 mL/min.
* DMA 850 / TMA 450: the specimen mounted (DMA clamp open, TMA probe up).
* LFA 467: purge and protective gas connected, detector coolant filled, switched on and the flash lamp charged, a SiC specimen
  in changer position 1.
* TPS 2500 S: sensor connected, specimen halves mounted, clamped, hood lowered (the hood only after the clamp has stopped).
* TAM IV: sample and reference ampoules inserted and lowered.
* MMC 315: tray out, lid open, bomb loaded, lid closed, tray docked.
* C 6000: the system test has run, the bomb C6012-0001 is prepared with 1 g benzoic acid, scanned and registered as "C6012 no. 1"
  with a C value of 8700 J/K in the isoperibol 25 °C mode (the lab's earlier calibration); the working mode is left as the
  instrument starts (isoperibol 22 °C), which does not match the cooling water - the agent changes it (`method mode=...`).
"""
import copy

from ...labbackend import load_config

EV = True


def step(cap, args='contract_example', evaluator=True):
    return {'capability': cap, 'arguments': args, 'evaluator': evaluator}


def wait(s):
    return {'advance_s': s}


TGA_PAN = {'position': 1, 'sample': {'reference_id': 'fsri_abs_n2_10k_r1', 'mass_kg': 1e-05, 'pan_material': 'ceramic', 'heat_capacity_j_kg_k': 1000.0,
                                    'loss_relaxation_s': 1.0, 'loss_heat_j_kg': 0.0, 'oxidation_capacity_fraction': 0.0, 'oxidation_rate_s_inv': 0.1,
                                    'oxidation_activation_j_mol': 50000.0, 'oxidation_reference_temperature_k': 600.0, 'oxidation_heat_j_kg': 0.0,
                                    'accept_engineering': True, 'accept_curve_projection': True, 'accept_condition_transfer': True,
                                    'accept_cold_extension': True}}
LFA_PROTECTIVE = {'channel': 'protective', 'gas': 'N2', 'amount_mol': 1.0, 'reference_temperature_k': 298.15, 'reference_pressure_pa': 101325.0,
                  'accept_reference_conditions': True}

WORLD = {
    'ta_dsc2500': [step('tray.place'), step('gas.unload', {}),
                   step('gas.load', {'gas': 'N2', 'amount_mol': 10.0, 'reference_temperature_k': 298.15, 'reference_pressure_pa': 101325.0,
                                     'accept_reference_conditions': True})],
    'ta_tga5500': [step('tray.place', TGA_PAN)],
    'ta_dma850': [step('sample.load')],
    'ta_tma450': [step('sample.load')],
    'netzsch_lfa467': [step('gas.load'), step('gas.load', LFA_PROTECTIVE), step('coolant.load'), step('power.set', {'enabled': True}),
                       step('pulse.charge'), step('sample.load')],
    'hot_tps2500': [step('probe.configure'), step('sample.load'), step('fixture.set_clamp', {'closed': True}), wait(5.0),
                    step('fixture.set_hood', {'opened': False}), wait(15.0)],
    'ta_tamiv': [step('sample.load'), step('reference.load'), step('sample.set_lift', {'lift_m': 0.0}), wait(5.0)],
    'netzsch_mmc315': [step('tray.move', {'extend': True}), wait(30.0), step('lid.set', {'open': True}), wait(30.0), step('sample.load'),
                       step('lid.set', {'open': False}), wait(30.0), step('tray.move', {'extend': False}), wait(30.0)],
    'ika_c6000': [wait(300.0), step('vessel.prepare'), step('vessel.scan', {'rfid': 'C6012-0001'}, evaluator=False),
                  step('vessel.register', 'contract_example', evaluator=False),
                  step('vessel.set_c_value', {'vessel_name': 'C6012 no. 1', 'mode': 'isoperibol_25', 'c_value_j_k': 8700.0}, evaluator=False)],
}


def config(base=None, only=None):
    """all203.json (or ``base``) with the thermal_analysis world steps appended to the members' set-ups."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in WORLD and (only is None or slug in only):
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(WORLD[slug])
    return cfg


def setup_problems(lab):
    """Set-up steps that did not succeed (a broken world fails loudly in the tests)."""
    out = []
    for iid, inst in lab.backend.instruments.items():
        for r in getattr(inst, 'setup_report', []):
            if r.get('status') not in ('succeeded', 'accepted', 'ok'):
                out.append((iid, r))
    return out
