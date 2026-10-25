"""Host-side world for the dosing class (what the lab operator has done before the agent starts), as lab-config set-up steps.

Quantos Q2 + XPE206DR: the front-door geometry is registered (travel = the contract door_kinematics.authored_travel (0.5495 m since asset 1.2.2: the arc length of the top-hung door's hinge pin, lever fully lifted; manual p.25-26)),
the operator opens the front door, inserts a free-flowing powder dosing head (2 g of powder) and puts an empty vial on the
weighing pan, closes the door, and the dosing rate of the powder is declared (0.5 mg/s: about 20 s for 10 mg, the order of the
brochure's typical 10 mg dosing time).  Nothing here is reachable from the sandbox.
"""
import copy

from ...labbackend import load_config

Q = 'mt_quantos_q2_xpe206dr'


def contract_travel_m(slug=Q):
    """The front door's full travel: door_kinematics.parameters.authored_travel of the asset contract (0.5495 m in 1.2.2)."""
    import json
    from ...hosts import contract_index
    c = json.loads(contract_index()['instrument.' + slug].read_text(encoding='utf-8'))
    return float(c['mechanisms']['door_kinematics']['parameters']['authored_travel']['value'])


TRAVEL_M = contract_travel_m()
RATE_KG_S = 5e-7


def world_steps(iid=Q, *, source_kg=0.002, vial_content_kg=0.0, head_id='head_1', vial_id='vial_1', position='balance_pan',
                rate_kg_s=RATE_KG_S, head=True, vial=True, configure=True):
    steps = [{'host': 'host_configure_geometry', 'kwargs': {'iid': iid, 'door_travel_m': TRAVEL_M, 'evidence': 'lab set-up: front door travel'}}]
    if head or vial:
        steps.append({'capability': 'door.set_opening', 'arguments': {'opening_m': TRAVEL_M}})
        if head:
            steps.append({'host': 'host_load_head', 'kwargs': {'iid': iid, 'head_id': head_id, 'source_mass_kg': source_kg, 'free_flowing': True,
                                                               'evidence': 'lab set-up: free-flowing powder head inserted'}})
        if vial:
            steps.append({'host': 'host_load_container', 'kwargs': {'iid': iid, 'container_id': vial_id, 'position_id': position,
                                                                    'content_mass_kg': vial_content_kg, 'evidence': 'lab set-up: vial on the pan'}})
        steps.append({'capability': 'door.set_opening', 'arguments': {'opening_m': 0.0}})
    if configure:
        steps.append({'host': 'host_configure_dosing', 'kwargs': {'iid': iid, 'rate_kg_s': rate_kg_s, 'rate_evidence': 'lab set-up: powder rate',
                                                                  'observation_evidence': 'lab set-up: ideal mass observation'}})
    return steps


def config(base=None, **kw):
    """all203.json (or ``base``) with the dosing world added to the Quantos entry."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        if e['asset_type_id'].split('.', 1)[1] == 'mt_quantos_q2_xpe206dr':
            e['default_world'] = False        # this world inserts its own head and vial (the default lab has the CaCO3 check head in)
            e['setup'] = list(e.get('setup') or []) + world_steps(e.get('instance_id') or e['id'], **kw)
    return cfg
