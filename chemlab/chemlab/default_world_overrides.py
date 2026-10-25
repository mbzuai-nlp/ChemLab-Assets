"""Hand-written part of the default lab (see default_world.py): changes to the generated per-instrument set-up and instruments the
class worlds did not cover.  Every value has a source in "why": a manual page (page number only) or a stated typical value with its
reason.  The containers themselves (bottles, vials, plates, cylinders) are in the inventory (chemlab/inventory.py ->
configs/inventory_default.json).

OVERRIDES[slug] keys (all optional):
    "drop": [capability or host names]   remove these generated steps ("*": all of them)
    "add": [steps]                       add these steps (tag "default": GROUP; "at": "start" | "before:CAP" | "after:CAP" | end)
    "replace_bringup": [capabilities]    remove these steps from the release bring-up (they are re-done here with real values)
    "entry": {...}                       extra lab-config keys (scene, deck, ...)
"""


def ev(cap, args='contract_example', group='state', at=None, why=None):
    st = {'capability': cap, 'arguments': args, 'evaluator': True, 'default': group}
    if at:
        st['at'] = at
    if why:
        st['why'] = why
    return st


def op(cap, args='contract_example', group='state', at=None, why=None):
    st = ev(cap, args, group, at, why)
    st.pop('evaluator')
    return st


def adv(s, group='state', at=None):
    st = {'advance_s': float(s), 'default': group}
    if at:
        st['at'] = at
    return st


from .compile.tasks.dosing_world import TRAVEL_M as QUANTOS_TRAVEL_M      # read from the Quantos contract (door_kinematics)

# a 50 L cylinder at 200 bar and 20 °C holds p V / (R T) = 2e7 * 0.05 / (8.314 * 293.15) ≈ 410 mol (ideal gas)
CYLINDER_50L_MOL = 410.0

OVERRIDES = {
    # the bring-up installs 0.05 mol per bottle (runs out after ~1400 s).  Three cylinders on the gas panel (inventory cyl_n2_2,
    # cyl_ar_1, cyl_ch4_1), each full: contents from NIST WebBook SRD 69 densities at 15 C / 200 bar gauge
    # (laboao_cvdltf12p50 1.1.3 caps a channel at one full cylinder of its gas instead of 100 mol).
    'laboao_cvdltf12p50': {
        'replace_bringup': ['gas.install'],
        'drop': ['gas.install'],
        'add': [ev('gas.install', {'species': ['N2', 'AR', 'CH4'], 'initial_mol': [400.1, 447.7, 525.1], 'inlet_gauge_pa': [100000.0, 100000.0, 100000.0],
                                   'full_scale_std_m3_s': [5e-06, 5e-06, 5e-06], 'actual_gas_factor': [1.0, 1.0, 1.0], 'accept_host_supply': True},
                   'utility', at='after:plant.configure',
                   why='N2 / Ar / CH4 cylinders on the gas panel, full 50 L cylinders at 200 bar gauge and 15 C = 400.1 / 447.7 / '
                       '525.1 mol (NIST Chemistry WebBook SRD 69 densities at 288.15 K, 201.01 bar abs: 8.0018 / 8.9545 / 10.502 mol/L; table '
                       'outputs/expansion_batch04/t5_automation/laboao_cvdltf12p50/sources/nist_fluid_288K_201bar.json); regulators at 1 bar '
                       'gauge and 300 sccm full-scale MFCs as in the contract example')],
    },
    # the contract advertises 0-450 °C (TRB³ datasheet: heating up to 450 °C dry, 150 °C in liquid); the bring-up's plant has no
    # heater.  The heating stage installed: 450 °C maximum (datasheet), 400 W (a typical cartridge-heater stage; not in the datasheet).
    'antonpaar_trb': {
        'replace_bringup': ['plant.configure'],
        'add': [ev('plant.configure', {"accept_engineering": True, "ambient_k": 296.15, "relative_humidity": 0.5, "humidity_offset": 0.0, "thermal_capacity_j_k": 100.0, "heat_loss_w_k": 1.0, "heater_installed": True, "heater_max_w": 400.0, "heater_gain_w_k": 10.0, "safe_access_temperature_k": 313.15, "temperature_max_k": 723.15, "stage_mass_kg": 0.2, "rotational_inertia_kg_m2": 0.0001, "linear_drive_limit_n": 100.0, "normal_force_factor": 1.0, "normal_read_gain": 1.0, "normal_read_offset_n": 0.0, "friction_gain_left": 1.0, "friction_gain_right": 1.0, "friction_offset_left_n": 0.0, "friction_offset_right_n": 0.0, "common_drift_n_s": 0.0, "thermal_common_drift_n_k": 0.0, "force_sensor_tau_s": 0.002, "minimum_observable_load_n": 0.05, "wear_sensor_enabled": False, "wear_gain": 1.0, "wear_offset_m": 0.0, "temperature_sensor_offset_k": 0.0}, 'accessory', at='start',
                   why='heating stage installed: temperature_max 723.15 K = 450 °C (manual: trb3_product_page p.1, options); heater 400 W (typical '
                       'cartridge-heater stage, stated); the rest is the contract example')],
    },
    # 0.1 mol N2 purge lasts ~49 min at 50 mL/min.  The lab's nitrogen: a 50 L cylinder at 200 bar (~410 mol, ideal gas).
    'ta_dsc2500': {
        'drop': ['gas.load'],
        'add': [ev('gas.load', {'gas': 'N2', 'amount_mol': CYLINDER_50L_MOL, 'reference_temperature_k': 298.15, 'reference_pressure_pa': 101325.0,
                                'accept_reference_conditions': True}, 'utility',
                   why='purge gas: one 50 L N2 cylinder at 200 bar = 410 mol (p V / R T); lasts ~8 months at 50 mL/min (manual: '
                       'dsc_q_series_getting_started_guide p.35, purge gases)')],
    },
    # FTIR: the standard sampling accessory installed (ATR), as the contract example of accessory.install; the polystyrene film
    # check standard (inventory ir_polystyrene_film) registered so the agent can load it (jasco_ftir4700 1.1.2 has its template)
    'jasco_ftir4700': {
        'add': [op('lid.set_angle', {'angle_rad': 1.5707963267948966}, 'accessory'),
                op('accessory.install', {'mode': 'atr'}, 'accessory', why='ATR accessory installed: the contract example (the usual sampling '
                                                                            'accessory of the FT/IR-4700 in a teaching / analytical lab)'),
                op('lid.set_angle', {'angle_rad': 0.0}, 'accessory'),
                {'host': 'register_template_sample', 'kwargs': {'sample_id': 'ir_polystyrene_film', 'substance_id': 'polystyrene',
                                                                 'phase': 'solid'}, 'default': 'standard',
                 'why': 'polystyrene film wavenumber check standard (manual: ftir4000_6000_series_brochure p.4, standard sample: '
                        'polystyrene film); template = NIST WebBook spectrum + SRM 1921b certified bands (jasco_ftir4700 1.1.2); the '
                        'card is inventory ir_polystyrene_film'}],
    },
    # AA500: the calibrants the default set-up loads (std0-3, wash water) become the lab's, so the agent can run them
    # (seal_aa500 1.0.1 host release_to_lab: without it the cups belong to the set-up's operator session)
    'seal_aa500': {
        'add': [{'host': 'release_to_lab', 'kwargs': {}, 'default': 'consumable',
                 'why': 'the cups and standards the lab set-up loaded are the lab\'s, usable by any caller (seal_aa500 1.0.1)'}],
    },
    # gel: a ready-to-load protein ladder (prestained marker, 10 µL per lane as the marker maker recommends for mini gels - typical)
    'biorad_miniprotean': {
        'add': [op('sample.prepare', {'sample_id': 'ladder_1', 'proteins_kg': {}, 'marker': 'precision_plus_prestained', 'marker_volume_m3': 1e-08,
                                      'sample_buffer': 'laemmli_2x_reducing', 'heat': 'none', 'volume_m3': 1e-08}, 'standard',
                   why='protein ladder: prestained marker, 10 µL (typical mini-gel loading)')],
    },
    # Flex: the release bring-up switches it on; the deck (tip racks, reservoir, plate, tube rack) is registered by its scene
    'opentrons_flex_single_5_1000ul': {'drop': ['power.set']},
    # the sample goes in through the door and cap before the instrument is switched on (as the extraction world does)
    'sft_sft110': {
        'drop': ['door.set_angle', 'cap.set_lift', 'sample.configure'],
        'add': [ev('door.set_angle', {'angle_rad': 1.2}, 'standard', at='before:panel.switch'),
                ev('cap.set_lift', {'lift_m': 0.07}, 'standard', at='before:panel.switch'),
                ev('sample.configure', 'contract_example', 'standard', at='before:panel.switch',
                   why='the contract example charge (the documented reference matrix of the asset) in the extraction vessel'),
                ev('cap.set_lift', {'lift_m': 0.0}, 'standard', at='before:panel.switch'),
                ev('door.set_angle', {'angle_rad': 0.0}, 'standard', at='before:panel.switch')],
    },
    # MiniFlex: the class world's quartz and calcite specimens plus two reference specimens from the phase library: Si (line
    # position, SRM 640 type) and corundum (intensity, SRM 676 type) - the holders are in the inventory (bench shelf / cabinet)
    'rigaku_miniflex600_external_cooling': {
        'entry': {'samples': {'quartz_holder': [{'phase_id': 'quartz', 'weight_fraction': 1.0}],
                              'calcite_holder': [{'phase_id': 'calcite', 'weight_fraction': 1.0}],
                              'si_srm640_holder': [{'phase_id': 'silicon', 'weight_fraction': 1.0}],
                              'corundum_srm676_holder': [{'phase_id': 'corundum', 'weight_fraction': 1.0}]}},
    },
    # Quantos Q2 + XPE206DR: the lab's CaCO3 check head (METTLER TOLEDO QH016-BNMW with 4 g of the powder the dosing
    # specification is written for) inserted and an empty target vial on the pan; the KBr head and more empty vials are in the
    # inventory (bench shelf, desiccator) for a host or robot to insert.  A task world that wants the Quantos empty (head and vial
    # inserted later by the robot / host) adds {"replace_default": ["consumable"]} to the entry.
    'mt_quantos_q2_xpe206dr': {
        'drop': '*',
        'add': [{'host': 'host_configure_geometry', 'kwargs': {'iid': '$instance', 'door_travel_m': QUANTOS_TRAVEL_M, 'evidence': 'lab set-up: front door travel'},
                 'default': 'utility', 'why': "front-door travel = the contract door_kinematics.authored_travel (0.5495 m since asset 1.2.2: the arc length of the top-hung door's hinge pin, lever fully lifted; manual p.25-26)"},
                {'capability': 'door.set_opening', 'arguments': {'opening_m': QUANTOS_TRAVEL_M}, 'default': 'consumable'},
                {'host': 'host_load_head', 'kwargs': {'iid': '$instance', 'head_id': 'qh_caco3_1', 'source_mass_kg': 0.004, 'free_flowing': True,
                                                      'evidence': 'lab set-up: CaCO3 check head (QH016-BNMW, 4 g) inserted'},
                 'default': 'consumable',
                 'why': 'head = inventory qh_caco3_1: METTLER TOLEDO QH016-BNMW powder dosing head (16 mL, 0.1-250 mg per dose; part 11141503; '
                        'manual: quantos_operating_instructions p.102, p.107) with 4 g calcium carbonate Sigma-Aldrich 21061, the powder of '
                        'the dosing specification (p.102); 4 g of the light precipitated powder fills 7-13 mL (assumed 0.3-0.6 g/mL)'},
                {'host': 'host_load_container', 'kwargs': {'iid': '$instance', 'container_id': 'quantos_vial_pan', 'position_id': 'balance_pan',
                                                           'content_mass_kg': 0.0, 'evidence': 'lab set-up: empty target vial on the pan'},
                 'default': 'consumable', 'why': 'inventory quantos_vial_pan (empty target vial)'},
                {'capability': 'door.set_opening', 'arguments': {'opening_m': 0.0}, 'default': 'consumable'},
                {'host': 'host_configure_dosing', 'kwargs': {'iid': '$instance', 'rate_kg_s': 5e-07, 'rate_evidence': 'lab set-up: powder rate',
                                                             'observation_evidence': 'lab set-up: ideal mass observation'},
                 'default': 'utility', 'why': 'dosing rate 0.5 mg/s (dosing class world: about 20 s for 10 mg; manual p.102 gives 40 s typical '
                                              'for 50 mg CaCO3)'}],
    },
    # HIT 5P: the start-of-day friction calibration swing (operator), pendulum re-raised, a notched reference bar on the supports
    'zwick_hit5p': {
        'drop': '*',
        'add': [ev('calibration.start', {}, 'standard'), adv(20, 'standard'), ev('pendulum.catch', {}, 'standard'), adv(5, 'standard'),
                ev('pendulum.raise', {}, 'standard'), adv(5, 'standard'), ev('shield.set_open', {'opened': True}, 'standard'), adv(3, 'standard'),
                ev('sample.load', 'contract_example', 'standard', why='the contract example notched bar (reference specimen)'),
                ev('shield.set_open', {'opened': False}, 'standard'), adv(3, 'standard')],
    },
}
