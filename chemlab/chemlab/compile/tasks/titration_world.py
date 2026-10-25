"""Host-side world for the titration class (what the lab operator has done before the agent starts), as lab-config set-up steps.
Nothing here is reachable from the sandbox.

* OMNIS: 25 mL of an acetic acid sample (1 mmol, public pKa library) in the titration beaker, NaOH titrant in the bottle
  (host_load_template_sample).  The electrode and the beaker are not yet registered in their slots (the agent connects them with
  the ids the lab gives: electrode_01, beaker_01).
* 899 Coulometer: cell, generator and indicator electrode installed with the power off (operator slot.set_installed, before the
  release bring-up switches it on), then 10 mg of copper(II) sulfate pentahydrate added with fresh reagent (host
  load_template_sample; about 3.6 mg of water).
* UDK 159: the default lab is ready (digested tube clamped, tanks and cooling water, library method 1 selected).
"""
import copy

from ...labbackend import load_config

OMNIS_SAMPLE = {'components': [{'substance_id': 'acetic_acid', 'amount_mol': 0.001}], 'volume_m3': 2.5e-05,
                'titrant_id': 'titrant_sodium_hydroxide'}
KF_SAMPLE = {'substance_id': 'copper_ii_sulfate_pentahydrate', 'sample_mass_kg': 1e-05, 'background_drift_kg_s': 0.0,
             'reagent_ready': True, 'request_id': 'world_sample'}


def config(base=None):
    """all203.json (or ``base``) with the titration world steps added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        iid = e.get('instance_id') or e['id']
        if slug == 'metrohm_omnis':
            e['setup'] = list(e.get('setup') or []) + [{'host': 'host_load_template_sample',
                                                        'kwargs': {'instance_id': iid, 'analyte': copy.deepcopy(OMNIS_SAMPLE)}}]
        elif slug == 'metrohm_899':
            e['setup'] = ([{'capability': 'slot.set_installed', 'arguments': {'slot': s, 'installed': True}} for s in ('cell', 'generator', 'indicator')]
                          + list(e.get('setup') or [])
                          + [{'host': 'load_template_sample', 'kwargs': dict(KF_SAMPLE, instance_id=iid)}])
    return cfg
