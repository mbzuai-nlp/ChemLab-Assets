"""Host-side world for the evaporation class (what the lab has done before the agent starts), as lab-config set-up steps.

* IKA RV 10: the heating bath is filled (3 L water: heat capacity, start temperature, loss - a trusted host call before any
  heating), a 200 mL ethanol charge is in the flask (library template), and the VACUUBRAND pump is connected as its vacuum
  source at 175 mbar.  Without it thermal.start answers PRECONDITION_FAILED and the bath temperature reads null.
* BUCHI Lyovapor: the chamber is opened, pre-frozen samples are loaded (host initialize_sample, with a drying-rate model for the
  moisture endpoint), the chamber closed; a rotary vane pump (5 Pa ultimate) is connected and the measured boundary (pressure,
  shelf / sample / condenser temperatures) is set.  Variant "open": the chamber is left open at atmospheric pressure.
* BUCHI Mini Spray Dryer: the lab loads the drying recipe (evaluator method.configure) and connects the feed (sample.configure).
* Tousimis CPD: lid open, holder up, the wet specimen loaded under ethanol (sample.configure), holder down, lid closed.
* Genevac miVac: the release bring-up already loads method, rotor and samples; the "empty" variant leaves the samples out (the
  method can change only before loading).
* PILODIST 104: column fitted, the lab's method, the hexane/heptane charge, bath lift raised (6 s to travel).
* UIC KDL 1: the lab's method and the feed; "empty" leaves both out.
"""
import copy

from ...labbackend import load_config


def with_copies(base=None, copies=None):
    """A deep copy of the config with extra entries {new id: slug} (same asset type, own id; adapters match by asset type)."""
    cfg = copy.deepcopy(base or load_config())
    for new, slug in (copies or {}).items():
        src = next(e for e in cfg['instruments'] if e['asset_type_id'] == 'instrument.' + slug)
        e = copy.deepcopy(src)
        e['id'] = new
        e.pop('instance_id', None)
        cfg['instruments'].append(e)
    return cfg

EV = 'task world (evaporation): set up by the lab before the agent starts'


def rv10(iid):
    return [{'host': 'configure_bath', 'kwargs': {'instance_id': iid, 'effective_heat_capacity_j_k': 12000.0, 'initial_temperature_k': 293.15,
                                                  'heat_loss_w_k': 3.0, 'evidence': EV + ': bath filled with 3 L of water'}},
            {'host': 'configure_template_charge', 'kwargs': {'instance_id': iid, 'solvent_id': 'ethanol', 'volume_m3': 0.0002,
                                                             'condensation_efficiency': 0.95, 'heat_transfer_w_k': 20.0, 'evidence': EV + ': 200 mL ethanol'}},
            {'host': 'connect_vacuum_source', 'kwargs': {'instance_id': iid, 'source_instance_id': 'vacuubrand_pc3001_vario_select',
                                                         'pressure_pa': 17500.0, 'evidence': EV + ': pump at 175 mbar'}}]


def lyovapor(iid, variant='loaded'):
    if variant == 'open':                                       # samples loaded, chamber left open, still at atmospheric pressure
        opened, load, kinetics, _close, pump, boundary = lyovapor(iid)
        return [opened, load, kinetics, pump, dict(boundary, kwargs=dict(boundary['kwargs'], pressure_pa=101325.0))]
    return [{'capability': 'chamber.set_open', 'arguments': {'open': True}},
            {'host': 'initialize_sample', 'kwargs': {'instance_id': iid, 'dry_mass_kg': 0.01, 'ice_mass_kg': 0.09, 'bound_water_mass_kg': 0.005,
                                                     'pre_frozen': True, 'evidence': EV + ': 100 g pre-frozen product'}},
            {'host': 'configure_kinetics', 'kwargs': {'instance_id': iid, 'sublimation_rate_kg_s': 2.0e-5, 'desorption_rate_kg_s': 2.0e-6,
                                                      'evidence': EV + ': drying-rate model for the moisture endpoint'}},
            {'capability': 'chamber.set_open', 'arguments': {'open': False}},
            {'host': 'connect_vacuum', 'kwargs': {'instance_id': iid, 'source_instance_id': 'rotary_vane_pump_1', 'asset_type_id': 'instrument.rotary_vane_pump',
                                                  'minimum_pressure_pa': 5.0, 'evidence': EV + ': two-stage rotary vane pump, 5 Pa'}},
            {'host': 'set_boundary', 'kwargs': {'instance_id': iid, 'pressure_pa': 20.0, 'shelf_temperature_k': 253.15, 'sample_temperature_k': 243.15,
                                                'condenser_temperature_k': 188.15, 'evidence': EV + ': measured boundary'}}]


MINISPRAY = [{'capability': 'method.configure', 'arguments': 'contract_example', 'evaluator': True},
             {'capability': 'sample.configure', 'arguments': 'contract_example', 'evaluator': True}]
TOUSIMIS = [{'capability': 'lid.set_lift', 'arguments': {'lift_m': 0.065}, 'evaluator': True},
            {'capability': 'holder.set_lift', 'arguments': {'lift_m': 0.035}, 'evaluator': True},
            {'capability': 'sample.configure', 'arguments': 'contract_example', 'evaluator': True},
            {'capability': 'holder.set_lift', 'arguments': {'lift_m': 0.0}, 'evaluator': True},
            {'capability': 'lid.set_lift', 'arguments': {'lift_m': 0.0}, 'evaluator': True}]
PILODIST = [{'capability': 'column.configure', 'arguments': 'contract_example', 'evaluator': True},
            {'capability': 'method.configure', 'arguments': 'contract_example', 'evaluator': True},
            {'capability': 'sample.configure', 'arguments': 'contract_example', 'evaluator': True},
            {'capability': 'bath.set_engaged', 'arguments': {'engaged': True}, 'evaluator': True},
            {'advance_s': 6.0}]
PILODIST_EMPTY = [{'capability': 'column.configure', 'arguments': 'contract_example', 'evaluator': True}]
UIC = [{'capability': 'method.configure', 'arguments': 'contract_example', 'evaluator': True},
       {'capability': 'sample.configure', 'arguments': 'contract_example', 'evaluator': True}]


def steps(slug, iid, variant='loaded'):
    """Extra set-up steps of one member (after the release bring-up in all203.json)."""
    if slug == 'ika_rv10_digital_v':
        return rv10(iid)
    if slug == 'buchi_lyovapor_l250_pro':
        return lyovapor(iid, variant)
    if slug == 'buchi_minispray':
        return MINISPRAY if variant == 'loaded' else []
    if slug == 'tousimis_autosamdri815':
        return TOUSIMIS if variant == 'loaded' else []
    if slug == 'pilodist_pilodist104':
        return PILODIST if variant == 'loaded' else PILODIST_EMPTY
    if slug == 'uic_kdl1basic':
        return UIC if variant == 'loaded' else []
    return []


def config(base=None, variants=None, copies=None):
    """all203.json (or ``base``) with the evaporation world added; ``variants``: {entry id or slug: 'loaded' | 'empty'} (default
    loaded); ``copies``: {new id: slug} adds more entries of a member (e.g. an "empty" one beside the loaded one)."""
    cfg = with_copies(base, copies)
    variants = variants or {}
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        iid = e.get('instance_id') or e['id']
        v = variants.get(e['id'], variants.get(slug, 'loaded'))
        if v == 'empty':
            e['default_world'] = False        # the empty variant: without the default lab's loaded state
        setup = list(e.get('setup') or [])
        if slug == 'genevac_mivac2' and v == 'empty':
            setup = [s for s in setup if s.get('capability') != 'sample.configure']
        if slug == 'buchi_lyovapor_l250_pro':                     # the samples go in after the bring-up (chamber.set_open needs power)
            e['setup'] = setup + steps(slug, iid, v)
        elif slug in ('ika_rv10_digital_v',):                     # the bath before the power comes on
            e['setup'] = steps(slug, iid, v) + setup
        else:
            e['setup'] = setup + steps(slug, iid, v)
    return cfg
