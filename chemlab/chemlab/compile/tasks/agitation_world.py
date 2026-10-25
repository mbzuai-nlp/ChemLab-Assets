"""Host-side world of the agitation class (what the lab operator has done before the agent starts), as lab-config set-up steps.

* 5910 Ri: two synthetic rotors (large capacity: positions at 0.2 m, 4500 rpm; fixed angle: 0.1 m, 14000 rpm), the process model
  and three registered tubes (tube_a, tube_b 50 g, tube_c 60 g) - host methods, before power-on; after the start-up the operator
  opens the lid and selects the large rotor.  The lab declares the rotor radii ("rotor_radius_m") for --rcf.
* ARE-310: a filled cup (140 g gross) in the holder, lid open.
* KS 4000: the platform fixture and one secured flask, hood closed again (the default lab has no fixture).
* T 25 and VCX 750: the sample vessel and the clamped tool / probe (evaluator sample.configure, mount.configure).
* S60H: the tank filled with 3.8 L of water (cover lifted while filling).
"""
import copy
import math

from ...labbackend import load_config
from .property_meter_world import ev, example, op, wait

RPM = 2 * math.pi / 60
ROTORS = (('large_capacity', 0.2, 4500), ('fixed_angle', 0.1, 14000))
TUBES = (('tube_a', 0.05), ('tube_b', 0.05), ('tube_c', 0.06))


def centrifuge_host():
    st = [{'host': 'configure_synthetic_rotor', 'kwargs': {'configuration': conf, 'slot_positions_xy_m': {'slot_1': [r, 0], 'slot_2': [-r, 0], 'slot_3': [0, r],
                                                                                                         'slot_4': [0, -r]},
                                                           'operating_speed_limit_rad_s': lim * RPM, 'imbalance_threshold_kg_m': 1e-4, 'evidence': 'synthetic'}}
          for conf, r, lim in ROTORS]
    st.append({'host': 'configure_synthetic_process', 'kwargs': dict(
        acceleration_rad_s2=[2.0 + i * 3 for i in range(10)], braking_rad_s2=[1.0 + i * 3 for i in range(10)], poweroff_deceleration_rad_s2=2.0,
        temperature_time_constant_s=300.0, ambient_temperature_k=295.15, initial_temperature_k=295.15, sample_interval_s=10.0, evidence='synthetic')})
    st += [{'host': 'register_sample', 'kwargs': {'sample_id': s, 'mass_kg': m}} for s, m in TUBES]
    return st


def world(e):
    slug = e['asset_type_id'].split('.', 1)[1]
    setup = list(e.get('setup') or [])
    if slug == 'eppendorf_5910ri':
        e['setup'] = centrifuge_host() + setup + [op('lid.set_angle', {'angle_rad': 1.5707963267948966}),
                                                  op('rotor.select', {'configuration': 'large_capacity'})]
        e['rotor_radius_m'] = {conf: r for conf, r, _ in ROTORS}
    elif slug == 'thinky_are310s':
        e['setup'] = setup + [ev('sample.configure', example(slug, 'sample.configure'))]
    elif slug == 'ika_ks4000':
        e['setup'] = setup + [op('hood.set_angle', {'angle_rad': 1.5707963267948966}), ev('fixture.configure', example(slug, 'fixture.configure')),
                              ev('load.configure', example(slug, 'load.configure')), op('hood.set_angle', {'angle_rad': 0.0})]
    elif slug in ('ika_t25', 'sonics_vcx750'):
        e['setup'] = setup + [ev('sample.configure', example(slug, 'sample.configure')), ev('mount.configure', example(slug, 'mount.configure'))]
    elif slug == 'elma_elmasonics60h':
        e['setup'] = [op('cover.set_lift', {'lift_m': 0.3}), ev('bath.configure', example(slug, 'bath.configure')),
                      op('cover.set_lift', {'lift_m': 0.0})] + setup
    return e


def config(base=None):
    """all203.json (or ``base``) with the agitation world."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        world(e)
    return cfg


_ = wait
