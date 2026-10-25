"""Host-side world for the spectroscopy class: what the lab operator has done before the agent starts.

Default world (``config``), as lab-config set-up steps:

* Hach DR6000: two cuvettes registered with the instrument (``blank_01`` = blank, ``sample_01`` = a dye with one band at
  520 nm); the agent loads them itself (sample.load is an agent capability).
* JASCO FT/IR-4700: an ethanol sample (``ethanol_01``, NIST IR template) registered; the agent installs the ATR accessory and
  loads it.
* Magritek Spinsolve 80: an NMR tube ``ethanol_01`` (0.1 mmol ethanol in CDCl3, HMDB template) registered; the agent inserts it.
* JASCO J-1500 / FP-8350, Bruker ESR5000: a blank cell and a sample cell registered by the operator; the blank cell is in the
  holder (cells are placed by the operator - no agent capability).
* JASCO NRS-5500: the mineral sample registered and on the stage.  CPL-300 / FVS-6000: the blank cell in the holder, cover closed.
* Applied Photophysics SX20: drive syringes loaded, bath cover closed, drive valves open, drive gas on.
* Edinburgh FLS1000: the fluorescent reference in the holder, cover closed, the pulsed laser armed (the operator switched the
  instrument on to arm it).
* Ultrafast HELIOS: the dye cell in place, cover closed, laser armed.
* Bruker minispec mq20: the Gd-chelate tube registered and in the probe (lid still open); the VT accessory installed (plant).

Stage worlds for tasks and lowering cases (functions of a LabBackend, applied the same way to a lab and its twin):
``swap_cell`` (operator exchanges the cell), ``record_blank`` (operator records a blank with the instrument's own calls), ...
Nothing here is reachable from the sandbox; the class adapter never calls any of it.
"""
import copy

from ...labbackend import load_config

DR_SAMPLES = [{'host': 'register_sample', 'kwargs': {'sample_id': 'blank_01', 'kind': 'blank'}},
              {'host': 'register_sample', 'kwargs': {'sample_id': 'sample_01', 'kind': 'sample', 'peaks': [[5.2e-07, 4e-08, 0.8]],
                                                     'baseline_absorbance': 0.02}}]
FTIR_SAMPLES = [{'host': 'register_template_sample', 'kwargs': {'sample_id': 'ethanol_01', 'substance_id': 'ethanol', 'phase': 'liquid'}}]


def ev(cap, args='contract_example'):
    return {'capability': cap, 'arguments': args, 'evaluator': True}


def op(cap, args):
    return {'capability': cap, 'arguments': args}


# blank cells that match the contract example sample cells (same solvent / path), so a blank and the sample share a fingerprint
J1500_BLANK = {'sample_id': 'blank_01', 'kind': 'blank', 'template_id': '', 'residue_concentration_mol_m3': 0.0, 'path_length_m': 0.001,
               'cell_shape': 'rectangular', 'volume_m3': 2e-07, 'solvent_id': 'buffer_01', 'mean_epsilon_m2_mol': [600.0] * 66,
               'solvent_alpha_m_inv': [50.0] * 66, 'solvent_delta_alpha_m_inv': [0.05] * 66, 'accept_synthetic_uv': True,
               'accept_unknown_reference_conditions': True}
FP_BLANK = {'sample_id': 'blank_01', 'kind': 'blank', 'template_id': '', 'concentration_mol_m3': 0.0, 'volume_m3': 2e-06, 'solvent_id': 'ethanol',
            'solvent_background_s_inv': 40.0, 'reference_temperature_k': 298.15, 'accept_unknown_temperature': True, 'accept_source_mirror': True}
ESR_BLANK = {'sample_id': 'blank_01', 'kind': 'blank', 'material_phase': 'liquid', 'template_id': '', 'solvent_code': 'BZ', 'volume_m3': 1e-07,
             'mass_kg': 0.0, 'concentration_mol_m3': 0.0, 'solid_spin_count': 0.0, 'g_assumed': None, 'linewidth_pp_t': 0.0004,
             'saturation_power_w': 0.1, 'reference_temperature_k': 298.15, 'tube_od_m': 0.005, 'accept_missing_source_conditions': True,
             'accept_synthetic_solid': False}
FVS_BLANK = {'sample_id': 'solvent_blank', 'kind': 'blank', 'template_id': '', 'solvent': 'DMSO-d6', 'is_aqueous': False, 'volume_m3': 5e-08,
             'concentration_kg_m3': 0.0, 'ir_peak_absorbance': 0.0, 'synthetic_vcd_ratio': 0.0, 'accept_inferred_units': True,
             'accept_unknown_conditions': True, 'accept_surrogate': False}
CPL_BLANK = {'sample_id': 'solvent_blank', 'kind': 'blank', 'template_id': '', 'solvent': 'basic ethanol', 'volume_m3': 2e-06,
             'concentration_mol_m3': 0.0, 'peak_pl_relative': 0.0, 'synthetic_glum': 0.0, 'accept_unknown_conditions': True,
             'accept_synthetic_cpl': False}
FLS_SCATTERER = {'sample_id': 'ludox_scatterer', 'kind': 'scatterer', 'components': [], 'volume_m3': 1e-06,
                 'mean_photons_per_pulse_at_peak': 0.005, 'emission_sigma_m': 2e-08, 'accept_assumed_response': True}

DEFAULT = {
    'hach_dr6000': DR_SAMPLES,
    'jasco_ftir4700': FTIR_SAMPLES,
    'jasco_j1500': [ev('solution.register', J1500_BLANK), ev('solution.register'), ev('sample.load', {'sample_id': 'blank_01'})],
    'jasco_fp8350': [ev('solution.register', FP_BLANK), ev('solution.register'), ev('sample.load', {'sample_id': 'blank_01'})],
    'bruker_magnettechesr5000': [ev('sample.register', ESR_BLANK), ev('sample.register'), ev('sample.load', {'sample_id': 'blank_01'})],
    'jasco_nrs5500': [ev('sample.register'), ev('sample.load')],
    'jasco_fvs6000': [ev('cover.set', {'opened': True}), ev('sample.load', FVS_BLANK), ev('cover.set', {'opened': False})],
    'jasco_cpl300': [ev('cover.set', {'opened': True}), ev('sample.load', CPL_BLANK), ev('cover.set', {'opened': False})],
    'applied_sx20': [ev('sample.load'), ev('bath.cover', {'opened': False}), ev('valves.set', {'drive': True}), ev('gas.set', {'enabled': True})],
    'edinburgh_fls1000tcspc': [ev('sample.load'), ev('sample.cover', {'opened': False}), op('power.set', {'enabled': True}),
                               ev('source.arm', {'enabled': True})],
    'ultrafast_helios': [ev('sample.load'), ev('sample.cover', {'opened': False}), ev('laser.arm', {'enabled': True})],
    'bruker_minispecmq20': [ev('solution.register'), ev('sample.load')],
}


def spinsolve_steps(entry):
    iid = entry.get('instance_id') or entry['id']
    return [{'host': 'register_template_sample', 'kwargs': {'instance_id': iid, 'sample_id': 'ethanol_01',
                                                            'composition': [{'substance_id': 'ethanol', 'amount_mol': 0.0001}],
                                                            'solvent': 'CDCl3'}}]


def config(base=None, only=None):
    """all203.json (or ``base``) with the spectroscopy default world added (``only``: these asset slugs)."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if only is not None and slug not in only:
            continue
        steps = spinsolve_steps(e) if slug == 'magritek_spinsolve80' else copy.deepcopy(DEFAULT.get(slug, []))
        if slug == 'jasco_ftir4700':
            e['default_world'] = False        # the class tasks start without the ATR accessory (the agent installs it)
        if steps:
            e['setup'] = list(e.get('setup') or []) + steps
    return cfg


# ------------------------------------------------------------------ stage worlds (trusted host, during task set-up)
class World:
    """Evaluator / operator calls on one instrument of a LabBackend with deterministic request ids (a lab and its twin get the same)."""

    def __init__(self, backend, iid, tag='w'):
        self.b, self.iid, self.tag, self.n = backend, iid, tag, 0

    def ev(self, cap, args):
        self.n += 1
        r = self.b.evaluator_invoke(self.iid, cap, copy.deepcopy(args), 'world-%s-%s-%d' % (self.tag, self.iid, self.n))
        if r.get('status') not in ('succeeded', 'accepted', 'running'):
            raise RuntimeError('world step %s %s failed: %s' % (cap, args, r.get('error')))
        return r

    def op(self, cap, args):
        from ...labbackend import OPERATOR
        self.n += 1
        inst = self.b.instruments[self.iid]
        r = self.b._invoke_raw(inst, OPERATOR, cap, copy.deepcopy(args), 'world-op-%s-%s-%d' % (self.tag, self.iid, self.n))
        if r.get('status') not in ('succeeded', 'accepted', 'running'):
            raise RuntimeError('world step %s %s failed: %s' % (cap, args, r.get('error')))
        return r

    def wait(self, s):
        self.b.advance(s)

    def ev_read(self):
        r = self.b.evaluator_read(self.iid)
        return {k: (o.get('value') if isinstance(o, dict) else o) for k, o in (r.get('observations') or {}).items()}

    def until(self, test, max_s, step=5.0):
        t = 0.0
        while t < max_s and not test(self.ev_read()):
            self.b.advance(step)
            t += step
        if not test(self.ev_read()):
            raise RuntimeError('world condition not reached on %s' % self.iid)


def j1500_blank_recorded(backend, iid='jasco_j1500'):
    """The operator has run the J-1500 up (purge, lamp), recorded the blank with the standard method and put the sample cell in;
    the purge is running again after the lid was opened."""
    w = World(backend, iid, 'j15')
    w.op('power.set', {'enabled': True})
    w.op('purge.set', {'flow_std_m3_s': 5e-3 / 60})
    w.until(lambda v: v.get('purge.ready'), 1800)
    w.op('lamp.set', {'enabled': True})
    w.op('lid.set', {'opened': False})
    meth = backend.instruments[iid].contract['capabilities']['method.configure']['example_arguments']
    w.op('method.configure', meth)
    w.until(lambda v: v.get('lamp.ready'), 600, 1.0)
    w.op('acquisition.start', {'kind': 'blank'})
    w.until(lambda v: v.get('scan.phase') != 'running', 1800, 2.0)
    w.op('lid.set', {'opened': True})
    w.ev('sample.unload', {'recipient_id': 'external_rack'})
    w.ev('sample.load', {'sample_id': 'sample_01'})
    w.op('lid.set', {'opened': False})
    w.until(lambda v: v.get('purge.ready'), 1800)
    return w


def jasco_blank_recorded(backend, iid, sample_id='sample_01', method=None, tag='jb'):
    """FP-8350 / ESR5000 style: the operator switched on, set the method (contract example unless given), recorded the blank and
    then put the sample cell in (lid closed again).  The source stays on."""
    w = World(backend, iid, tag)
    caps = backend.instruments[iid].contract['capabilities']
    w.op('power.set', {'enabled': True})
    w.op('lid.set', {'opened': False})
    w.op('method.configure', method or caps['method.configure']['example_arguments'])
    if 'lamp.set' in caps and caps['lamp.set'].get('visibility') == 'agent':
        w.op('lamp.set', {'enabled': True})
        w.until(lambda v: v.get('lamp.ready'), 600, 1.0)
    if 'microwave.set' in caps:
        w.op('microwave.set', {'enabled': True})
    w.op('acquisition.start', {'kind': 'blank'})
    w.until(lambda v: v.get('scan.phase') != 'running', 3600, 2.0)
    w.op('lid.set', {'opened': True})
    w.ev('sample.unload', {'recipient_id': 'external_rack'})
    w.ev('sample.load', {'sample_id': sample_id})
    w.op('lid.set', {'opened': False})
    if 'microwave.set' in caps:
        w.op('microwave.set', {'enabled': True})
    return w


def swap_cover_cell(backend, iid, sample, cover_cap='cover.set', unload=True, tag='sw'):
    """CPL-300 / FVS-6000: the operator opens the cover, exchanges the cell for ``sample`` (sample.load arguments), closes it."""
    w = World(backend, iid, tag)
    w.ev(cover_cap, {'opened': True})
    if unload:
        w.ev('sample.unload', {'recipient_id': 'external_rack'})
    w.ev('sample.load', sample)
    w.ev(cover_cap, {'opened': False})
    return w
