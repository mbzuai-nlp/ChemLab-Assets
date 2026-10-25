"""Host-side world for the labware automation class (what the lab staff and the lab's 3-D scene provide), as lab-config set-up steps
and scene entries.

* a4S sealer: the block starts at room temperature and heats at an engineering 0.5 K/s (the runtime needs an explicit rate); a roll
  of film (100 seals) is threaded; an unsealed PCR plate (15.5 mm high) sits on its adapter on the carrier.
* IntelliXcap S1: a registered 15 mL screw-cap tube type, tube "t1" with cap "c1" in the holder, and the jaw / rotation motion
  (engineering values); the scene confirms clamp, thread and release (``thread_ok=False`` makes the thread fail).
* DP5 Mirage: switched off; a 96-rack "rack_1" on the reader with a task manifest; ``rack='mismatch'`` hides the barcode in C5 and
  puts another tube than expected in D7.
* PreciseFlex 400: switched off; the arm scene with named poses (home, plate_1 pick pose, sealer_nest), a 0.1 kg plate "plate_1" on
  the bench nest and the sealer's receiving nest.
"""
import copy

from ...labbackend import load_config

WELLS96 = [r + str(c) for r in 'ABCDEFGH' for c in range(1, 13)]


def rack(variant='ok'):
    seen = {w: 'TB%04d' % i for i, w in enumerate(WELLS96)}
    manifest = dict(seen)
    if variant == 'mismatch':
        seen.pop('C5')
        manifest['D7'] = 'TB9999'
    return {'rack_id': 'rack_1', 'rack_format': 96, 'well_ids': list(WELLS96), 'visible_barcodes': seen, 'task_manifest': manifest}


A4S = [{'host': 'host_set_temperature', 'kwargs': {'temperature_K': 295.15, 'evidence': 'task world: the block is at room temperature'}},
       {'host': 'host_configure_thermal_rate', 'kwargs': {'thermal_rate_K_s': 0.5, 'evidence': 'engineering heating rate 0.5 K/s'}},
       {'host': 'host_load_film', 'kwargs': {'roll_id': 'roll_1', 'width_m': 0.08, 'remaining_seals': 100, 'film_roll_position_id': 'roll_position',
                                             'compatibility_evidence': 'task world: peelable seal film threaded'}},
       {'host': 'host_load_plate', 'kwargs': {'plate_id': 'pcr_plate_1', 'height_m': 0.0155, 'plate_adapter_id': 'pcr_adapter',
                                              'compatibility_evidence': 'task world: skirted PCR plate on the PCR adapter'}}]

IXC = [{'host': 'host_register_consumable', 'kwargs': {'consumable_id': 'tube_15ml', 'cap_diameter_m': 0.02,
                                                       'compatibility_evidence': 'task world: 15 mL screw-cap tube, 20 mm cap'}, 'global': True},
       {'host': 'host_present_sample', 'kwargs': {'tube_id': 't1', 'cap_id': 'c1', 'consumable_id': 'tube_15ml', 'evidence': 'task world: tube t1 in the holder'}},
       {'host': 'host_configure_motion', 'kwargs': {'open_gap_m': 0.03, 'closed_gap_m': 0.019, 'rotation_delta_rad': 12.566370614359172,
                                                    'initial_rotation_rad': 0.0, 'clamp_duration_s': 1.0, 'rotation_duration_s': 2.0,
                                                    'release_duration_s': 1.0, 'evidence': 'engineering motion values'}}]

ARM = {'home': {'z_m': 0.2, 'shoulder_rad': 0.0, 'elbow_rad': 0.0, 'wrist_rad': 0.0},
       'containers': {'plate_1': {'pose': {'z_m': 0.05, 'shoulder_rad': 0.5, 'elbow_rad': -0.8, 'wrist_rad': 0.3}, 'port': 'bench_nest', 'payload_kg': 0.1}},
       'ports': {'sealer_nest': {'pose': {'z_m': 0.08, 'shoulder_rad': -0.6, 'elbow_rad': 0.9, 'wrist_rad': 0.0}}},
       'tolerance_m': 0.005, 'joint_speed_rad_s': 1.0, 'z_speed_m_s': 0.2}
ARM_LOCATIONS = {'home': {'joints': ARM['home']},
                 'plate_1': {'container_id': 'plate_1', 'constraint_id': 'grip_plate_1', 'joints': ARM['containers']['plate_1']['pose']},
                 'sealer_nest': {'receiver_port_id': 'sealer_nest', 'joints': ARM['ports']['sealer_nest']['pose']}}


def config(base=None, rack_variant='ok', thread_ok=True):
    """all203.json (or ``base``) with the labware world added to each member's entry."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        iid = e.get('instance_id') or e['id']
        steps = None
        if slug == 'azenta_automated_roll_heat_sealer_a4s':
            steps = A4S
        elif slug == 'azenta_intellixcap_s1_46_0001':
            steps = IXC
            e['scene'] = 'chemlab.compile.scenes.intellixcap:IntelliXcapScene'
            e['thread_ok'] = thread_ok
        elif slug == 'ziath_dp5_mirage_2023':
            steps = [{'host': 'host_load_rack', 'kwargs': rack(rack_variant)}]
        elif slug == 'brooks_preciseflex_400':
            e['scene'] = 'chemlab.compile.scenes.preciseflex:PreciseFlexScene'
            e['arm'] = copy.deepcopy(ARM)
            e['locations'] = copy.deepcopy(ARM_LOCATIONS)
        if steps:
            out = []
            for st in copy.deepcopy(steps):
                if not st.pop('global', False):
                    st['kwargs']['instance_id'] = iid
                out.append(st)
            e['setup'] = list(e.get('setup') or []) + out
    return cfg


def mismatch_config(base=None):
    return config(base, rack_variant='mismatch')


def stuck_cap_config(base=None):
    return config(base, thread_ok=False)
