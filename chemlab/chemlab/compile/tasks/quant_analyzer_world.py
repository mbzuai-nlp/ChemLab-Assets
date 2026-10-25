"""Host-side world for the quant_analyzer class: what the lab operator has done before the agent starts.

Every member of the class measures a sample that the operator puts in: a solution on the nebulizer / autosampler, a capsule in
the carousel, a cup in the XRF chamber, a gas in the RGA chamber, a pin in the LECO crucible.  None of that is an agent
capability (sample.load is evaluator-only on 11 of the 13 contracts), so the class verbs never do it; tasks and lowering
cases put samples in with the functions here.

Stage functions take a LabBackend or a GenericLab (``lab.backend``) and work the same on a lab and on its lowering twin:

* ``load(b, iid, kind, **kw)``: the operator exchanges the sample (unload the old one, load the new one) with the member's own
  evaluator calls; ``kind`` is blank / standard / sample, the member defaults below give the template and amounts;
* ``bring_up(b, iid, stage)``: the operator brings the instrument to a stage with the member's own calls (agent-principal calls
  for agent capabilities, the evaluator for host ones): 'on' (power, vacuum, gases), 'ready' (method set, source lit / furnace
  hot), 'blank' (a blank measured), 'standards' (three standards measured: records exist for `calibrate`), 'calibrated' (a curve
  fitted and selected), and a sample loaded at the end when ``sample=True``;
* ``STANDARDS[iid]``: what the standards are (reference id and levels), for gold solutions and checkers.

Nothing here is reachable from the sandbox; the class adapter never calls any of it.
"""
import copy

from ...labbackend import load_config

ICPE, ICPMS, TOC, AA, EDX = 'shimadzu_icpe9800', 'shimadzu_icpms2030', 'shimadzu_tocl', 'jena_contraa800', 'shimadzu_edx7200'
MX, DMA, VARIO, RGA, ORBI = 'jena_multix', 'milestone_dma80', 'elementar_varioel', 'stanford_rga100', 'thermo_orbitrapexploris'
LECO, IRMS, SEAL = 'leco_onh836', 'elementar_isoprimeprecision', 'seal_aa500'


class World:
    """Small helper over a LabBackend for one instrument: agent-principal calls, evaluator calls, lab time."""

    def __init__(self, lab, iid):
        self.b = getattr(lab, 'backend', lab)
        self.iid = iid
        self.n = 0

    def _rid(self, cap):
        n = getattr(self.b, '_quant_world_n', 0) + 1                        # per backend: a lab and its twin count alike
        self.b._quant_world_n = n
        return 'world-%s-%s-%d' % (self.iid, cap, n)

    def ag(self, cap, ok=True, **a):
        r = self.b.invoke('agent', self.iid, cap, copy.deepcopy(a), self._rid(cap))
        if ok:
            assert r.get('status') in ('succeeded', 'accepted', 'running'), (self.iid, cap, a, r.get('status'), r.get('error'))
        return r

    def ev(self, cap, ok=True, **a):
        r = self.b.evaluator_invoke(self.iid, cap, copy.deepcopy(a), self._rid(cap))
        if ok:
            assert r.get('status') == 'succeeded', (self.iid, cap, a, r.get('status'), r.get('error'))
        return r

    def v(self, slot):
        r = self.b.evaluator_read(self.iid)
        o = (r.get('observations') or {}).get(slot)
        return o.get('value') if isinstance(o, dict) else o

    def adv(self, s):
        self.b.advance(s)

    def until(self, test, mx=3600.0, step=1.0):
        t0 = self.b.now()
        while not test():
            assert self.b.now() - t0 < mx, (self.iid, 'world wait timed out', self.b.now())
            self.b.advance(step)

    def example(self, cap):
        return copy.deepcopy(self.b.instruments[self.iid].contract['capabilities'][cap].get('example_arguments') or {})

    def phase_done(self, slot='measurement.phase', mx=3600.0, step=1.0):
        self.until(lambda: self.v(slot) not in ('running', 'draining'), mx, step)


# ------------------------------------------------------------------ samples (operator side)
ICPE_SAMPLE = {'sample_id': 'unknown_multi', 'template_id': 'shimadzu_epa_qcs_axial', 'kind': 'sample', 'volume_m3': 1e-05,
               'dilution_factor': 1.0, 'background_relative': 0.02, 'background_slope': 0.01, 'assumed_elements': [],
               'accept_assumed_elements': False, 'accept_unknown_conditions': True, 'is_aqueous': True}
ICPMS_SAMPLE = {'sample_id': 'unknown_solution', 'template_id': 'infant1849a_measured_digest', 'kind': 'sample', 'volume_m3': 1e-05,
                'dilution_factor': 20.0, 'internal_standard_reference_id': '', 'internal_standard_dilution_factor': 1.0,
                'matrix_transmission': 1.0, 'interferences': [], 'is_aqueous': True, 'accept_natural_isotope_assumption': True,
                'accept_unknown_conditions': True}
TOC_SAMPLE = {'sample_id': 'external_water_sample', 'kind': 'sample', 'template_id': 'shimadzu_pfos_1', 'template_channel': 'TC',
              'volume_m3': 2e-05, 'assumed_ic_kg_m3': None, 'assumed_poc_kg_m3': 0.0, 'assumed_npoc_kg_m3': None,
              'accept_partition_assumptions': True, 'accept_unknown_conditions': True}
AA_SAMPLE = {'sample_id': 'external_solution', 'kind': 'sample', 'template_id': 'aj_wastewater_inlet_cu', 'volume_m3': 1e-05, 'vial_index': 0,
             'dilution_factor': 1.0, 'background_absorbance': 0.02, 'background_slope': 0.005, 'accept_unknown_conditions': True,
             'accept_assumed_matrix': True}
EDX_SAMPLE = {'template_id': 'blackmass_ec_a', 'kind': 'sample', 'mass_kg': 0.01, 'dimensions_m': [0.02, 0.02, 0.01], 'phase': 'powder',
              'sealed_cup': True, 'reference_fraction': 1.0, 'matrix_factor': 1.0, 'accept_conditional_response': True,
              'accept_geometric_clearance_assumption': True}
MX_SAMPLE = {'template_id': 'aox_linear_100_r1_c1', 'kind': 'sample', 'input_form': 'loaded_carbon', 'mass_kg': 5e-05, 'volume_m3': 0.0,
             'reference_fraction': 0.2, 'accept_prepared_input': True, 'accept_source_condition_mismatch': True, 'halogen_moles': []}
DMA_SAMPLE = {'slot_index': 0, 'template_id': 'nist_2448_certified', 'kind': 'sample', 'input_form': 'solid', 'boat_material': 'metal',
              'mass_kg': 0.0001, 'volume_m3': 0.0, 'reference_fraction': 1.0, 'dry_mass_fraction': None, 'host_mercury_mass_kg': None,
              'accept_source_condition_mismatch': True}

# what the standards are (reference + levels): gold solutions and checkers use these
STANDARDS = {
    ICPE: {'reference': 'shimadzu_epa_ipc_assigned', 'levels': [100.0, 10.0, 1.0], 'level': 'dilution'},
    ICPMS: {'reference': 'infant_cal4', 'levels': [100.0, 10.0, 1.0], 'level': 'dilution'},
    TOC: {'reference': 'crm_altus_khp_10mg', 'levels': [10.0, 2.0, 1.0], 'level': 'dilution', 'channel': 'TC'},
    AA: {'reference': 'aj_wastewater_std3_cu', 'levels': [10.0, 2.0, 1.0], 'level': 'dilution'},
    EDX: {'reference': 'blackmass_icp_f', 'levels': [0.25, 0.5, 1.0], 'level': 'fraction'},
    MX: {'references': ['aox_linear_10_r1_c1', 'aox_linear_100_r1_c1', 'aox_linear_250_r1_c1']},
    DMA: {'reference': 'nist_2448_certified', 'masses_kg': [2e-05, 5e-05, 1e-04]},
    # (the unknowns: ICPE qcs Cu 0.99 mg/L; TOC pfos TC 1.898 mg/L; AAS inlet Cu 0.083 mg/L; EDX black mass Cu 2.04 %; DMA soil SRM
    #  2448 Hg 0.723 mg/kg; vario wheat flour C 39.4 %; AA500 river nitrate-N 1.8 mg/L - template values)
    VARIO: {'reference': 'oem_sulfanilic_acid', 'masses_kg': [1e-06, 2e-06, 3e-06], 'slots': [1, 2, 3]},
}


DMA_SAMPLE_KG = 5e-05                                                       # 50 mg of SRM 2448 in slot 5 (inside the 20-100 mg standards)
VARIO_SAMPLE = ('oem_wheat_flour', 2e-06)                                   # 2 mg wheat flour in slot 4


def load(lab, iid, kind='sample', **kw):
    """The operator exchanges the sample on a single-sample member (old one returned, new one connected / placed)."""
    w = World(lab, iid)
    if iid == ICPE:
        if w.v('sample.loaded'):
            w.ev('sample.unload', recipient_id='host_return')
        a = dict(ICPE_SAMPLE, kind=kind)
        if kind == 'blank':
            a.update(sample_id='matrix_blank', template_id='', background_relative=0.0, background_slope=0.0)
        elif kind == 'standard':
            a.update(sample_id='standard', template_id=STANDARDS[ICPE]['reference'])
        a.update(kw)
        w.ev('sample.load', **a)
    elif iid == ICPMS:
        if w.v('sample.loaded'):
            w.ev('sample.unload', recipient_id='host')
        a = dict(ICPMS_SAMPLE, kind=kind)
        if kind == 'blank':
            a.update(sample_id='blank', template_id='', dilution_factor=1.0)
        elif kind == 'standard':
            a.update(sample_id='standard', template_id=STANDARDS[ICPMS]['reference'])
        a.update(kw)
        w.ev('sample.load', **a)
    elif iid == TOC:
        if w.v('sample.loaded'):
            w.ev('sample.unload', recipient_id='external_bottle')
        a = dict(TOC_SAMPLE, kind=kind)
        if kind == 'blank':
            a.update(sample_id='blank', template_id='', assumed_poc_kg_m3=None)
        elif kind == 'standard':
            a.update(sample_id='standard', template_id=STANDARDS[TOC]['reference'], assumed_ic_kg_m3=0.0, assumed_poc_kg_m3=0.0)
        a.update(kw)
        w.ev('sample.load', **a)
    elif iid == AA:
        if w.v('sample.loaded'):
            w.ev('sample.unload', recipient_id='external_bottle')
        a = dict(AA_SAMPLE, kind=kind)
        if kind == 'blank':
            a.update(sample_id='blank', template_id='', background_absorbance=0.0, background_slope=0.0)
        elif kind == 'standard':
            a.update(sample_id='standard', template_id=STANDARDS[AA]['reference'])
        a.update(kw)
        w.ev('sample.load', **a)
    elif iid == EDX:
        w.ev('cover.set', opened=True)
        if w.v('sample.loaded'):
            w.ev('sample.unload', recipient_id='host')
        a = dict(EDX_SAMPLE, kind=kind)
        if kind == 'blank':
            a.update(template_id='')
        elif kind == 'standard':
            a.update(template_id=STANDARDS[EDX]['reference'])
        a.update(kw)
        w.ev('sample.load', **a)
        w.ev('cover.set', opened=False)
    elif iid == MX:
        if w.v('sample.loaded'):
            w.ev('sample.unload', recipient_id='host')
        a = dict(MX_SAMPLE, kind=kind)
        if kind == 'blank':
            a.update(template_id='', reference_fraction=1.0)
        a.update(kw)
        w.ev('sample.load', **a)
    else:
        raise ValueError('load: %s has no single-sample loader here' % iid)


# ------------------------------------------------------------------ bring-up per member
STAGES = ('on', 'ready', 'blank', 'standards', 'calibrated')


def _upto(stage, s):
    return STAGES.index(stage) >= STAGES.index(s)


def _measure(w, start='measurement.start', slot='measurement.phase', mx=3600.0, step=1.0, **a):
    w.ag(start, **a)
    w.phase_done(slot, mx, step)
    assert w.v(slot) == 'succeeded', (w.iid, start, w.v(slot), w.v('safety.warning'))


def bring_up(lab, iid, stage='ready', sample=False, curve='cal1'):
    """The operator brings ``iid`` to ``stage`` (see the module docstring) and, with ``sample``, loads the unknown sample."""
    w = World(lab, iid)
    fn = BRING_UP[iid]
    fn(w, stage, curve)
    if sample:
        if iid in (ICPE, ICPMS, TOC, AA, EDX, MX):
            load(lab, iid, 'sample')
        elif iid in SAMPLE_LOADERS:
            SAMPLE_LOADERS[iid](w)
    return w


def _icp(w, stage, curve, ms=False):
    w.ag('power.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('vacuum.set', enabled=True)
    w.ag('method.configure', **w.example('method.configure'))
    w.adv(30.0)
    w.ag('plasma.ignite')
    w.until(lambda: w.v('plasma.lit'), 120.0, 0.5)
    w.adv(2.0)
    if not _upto(stage, 'blank'):
        return
    load(w.b, w.iid, 'blank')
    _measure(w)
    if not _upto(stage, 'standards'):
        return
    for d in STANDARDS[w.iid]['levels']:
        load(w.b, w.iid, 'standard', dilution_factor=d)
        _measure(w)
    if not _upto(stage, 'calibrated'):
        return
    obs = []
    for i, d in enumerate(STANDARDS[w.iid]['levels']):
        rec = w.ag('records.read', record_id='record_%d' % (i + 2))['result']
        if ms:
            for a in rec['assays']:
                obs.append({'reference_id': STANDARDS[w.iid]['reference'], 'dilution_factor': d, 'channels': a['channels']})
        else:
            obs.append({'reference_id': STANDARDS[w.iid]['reference'], 'dilution_factor': d,
                        'line_integrals': [{'line_id': c['line_id'], 'integrated_intensity_s': c['mean_integrated_intensity_s']}
                                           for c in rec['concentrations']]})
    w.ag('calibration.fit', curve_id=curve, observations=obs, accept_same_response_conditions=True)
    w.ag('calibration.select', curve_id=curve)


TOC_METHOD = {'mode': 'TC', 'injection_volume_m3': 5e-07, 'dilution_factor': 1.0, 'repeats': 1, 'acid_volume_m3': 1e-09, 'acidification_s': 0.02,
              'sparging_s': 0.2, 'combustion_s': 0.15, 'minimum_peak_s': 0.03, 'maximum_peak_s': 1.0, 'baseline_threshold': 1e-05,
              'baseline_samples': 2, 'temperature_tolerance_k': 2.0}


def _toc(w, stage, curve):
    if not w.v('power.enabled'):
        w.ag('power.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('furnace.configure', temperature_k=953.15)
    w.ag('method.configure', **TOC_METHOD)
    w.until(lambda: w.v('furnace.ready'), 3600.0, 5.0)
    if not _upto(stage, 'blank'):
        return
    load(w.b, TOC, 'blank')
    _measure(w)
    if not _upto(stage, 'standards'):
        return
    for d in STANDARDS[TOC]['levels']:
        load(w.b, TOC, 'standard')
        w.ag('method.configure', **dict(TOC_METHOD, dilution_factor=d))
        _measure(w)
    w.ag('method.configure', **TOC_METHOD)
    if not _upto(stage, 'calibrated'):
        return
    obs = []
    for i, d in enumerate(STANDARDS[TOC]['levels']):
        rec = w.ag('records.read', record_id='%s:result:%d' % (TOC, i + 2))['result']
        for a in rec['assays']:
            obs.append({'reference_id': STANDARDS[TOC]['reference'], 'reference_channel': 'TC', 'injection_volume_m3': a['injection_volume_m3'],
                        'dilution_factor': d, 'peak_area_s': a['peak_area_s']})
    w.ag('calibration.fit', curve_id=curve, channel='TC', observations=obs, accept_source_channel_transfer=False,
         accept_same_response_conditions=True)
    w.ag('calibration.select', channel='TC', curve_id=curve)


def _aa(w, stage, curve):
    w.ag('power.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('method.configure', **w.example('method.configure'))
    w.adv(2.0)
    w.ag('flame.ignite')
    w.until(lambda: w.v('flame.lit'), 120.0, 0.5)
    if not _upto(stage, 'blank'):
        return
    load(w.b, AA, 'blank')
    _measure(w)
    if not _upto(stage, 'standards'):
        return
    for d in STANDARDS[AA]['levels']:
        load(w.b, AA, 'standard', dilution_factor=d, background_absorbance=0.02, background_slope=0.005)
        _measure(w)
    if not _upto(stage, 'calibrated'):
        return
    obs = []
    for i, d in enumerate(STANDARDS[AA]['levels']):
        rec = w.ag('records.read', record_id='%s:result:%d' % (AA, i + 2))['result']
        for a in rec['assays']:
            obs.append({'reference_id': STANDARDS[AA]['reference'], 'dilution_factor': d, 'injection_volume_m3': a['injection_volume_m3'],
                        'integrated_absorbance_s': a['integrated_absorbance_s']})
    w.ag('calibration.fit', curve_id=curve, observations=obs, accept_same_response_conditions=True)
    w.ag('calibration.select', curve_id=curve)


def _edx(w, stage, curve):
    w.ag('power.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('method.configure', **w.example('method.configure'))
    w.until(lambda: w.v('instrument.ready'), 3600.0, 5.0)
    if not _upto(stage, 'blank'):
        return
    load(w.b, EDX, 'blank')
    _measure(w)
    if not _upto(stage, 'standards'):
        return
    for f in STANDARDS[EDX]['levels']:
        load(w.b, EDX, 'standard', reference_fraction=f)
        _measure(w)
    if not _upto(stage, 'calibrated'):
        return
    obs = []
    for i, f in enumerate(STANDARDS[EDX]['levels']):
        rec = w.ag('records.read', record_id='record_%d' % (i + 2))['result']
        for a in rec['assays']:
            obs.append({'acquisition_signature': rec['acquisition_signature'], 'reference_id': STANDARDS[EDX]['reference'], 'reference_fraction': f,
                        'line_observations': [{k: x[k] for k in ('line_id', 'net_rate_s_inv', 'roi_low_j', 'roi_high_j')}
                                              for x in a['line_observations']]})
    w.ag('calibration.fit', curve_id=curve, observations=obs, accept_same_matrix_response=True)
    w.ag('calibration.select', curve_id=curve)


MX_METHOD_INTEGRATION_S = 60.0


def mx_prepare_cell(lab):
    """multi X 2500: the operator has filled and fitted the titration cell (chlorine door open, cell.prepare, door closed)."""
    w = World(lab, MX)
    w.ev('cover.set', door='chlorine', opened=True)
    w.ev('cell.prepare', cell_type='sensitive')
    w.ev('cover.set', door='chlorine', opened=False)


def _mx(w, stage, curve):
    if not w.v('cell.prepared'):
        mx_prepare_cell(w.b)
    w.ag('power.set', enabled=True)
    w.ag('gas.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('method.configure', **dict(w.example('method.configure'), integration_s=MX_METHOD_INTEGRATION_S))
    w.until(lambda: w.v('instrument.ready'), 3600.0, 5.0)
    if not _upto(stage, 'blank'):
        return
    load(w.b, MX, 'blank')
    _measure(w)
    if not _upto(stage, 'standards'):
        return
    for t in STANDARDS[MX]['references']:
        load(w.b, MX, 'standard', template_id=t)
        _measure(w)
    if not _upto(stage, 'calibrated'):
        return
    w.ag('calibration.fit', curve_id=curve, record_ids=['record_2', 'record_3', 'record_4'])
    w.ag('calibration.select', curve_id=curve)


def dma_load_carousel(lab, entries):
    """DMA-80 evo: the operator opens the dust cover, puts boats into carousel slots and closes it.
    ``entries``: [(slot, kind, mass_kg[, template_id])]."""
    w = World(lab, DMA)
    w.ev('dust_cover.set', opened=True)
    for e in entries:
        slot, kind, mass = e[:3]
        a = dict(DMA_SAMPLE, slot_index=slot, kind=kind, mass_kg=mass)
        if kind == 'blank':
            a['template_id'] = ''
        elif len(e) > 3:
            a['template_id'] = e[3]
        w.ev('sample.load', **a)
    w.ev('dust_cover.set', opened=False)


def _dma(w, stage, curve):
    if not w.v('power.enabled'):
        w.ag('power.set', enabled=True)
    w.ag('gas.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('method.configure', **w.example('method.configure'))
    w.until(lambda: w.v('instrument.ready'), 3600.0, 5.0)
    dma_load_carousel(w.b, [(0, 'blank', 1e-4)] + [(i + 1, 'standard', m) for i, m in enumerate(STANDARDS[DMA]['masses_kg'])]
                      + [(5, 'sample', DMA_SAMPLE_KG)])
    if not _upto(stage, 'blank'):
        return
    _measure(w, slot_index=0)
    if not _upto(stage, 'standards'):
        return
    _measure(w, start='sequence.start', slot_indices=[1, 2, 3])
    if not _upto(stage, 'calibrated'):
        return
    w.ag('calibration.fit', curve_id=curve, record_ids=['record_2', 'record_3', 'record_4'])
    w.ag('calibration.select', curve_id=curve)


def vario_load_capsules(lab, entries):
    """vario EL cube: the operator weighs capsules into autosampler slots.  ``entries``: [(slot, kind, mass_kg[, template_id])]."""
    w = World(lab, VARIO)
    ex = w.example('sample.load')
    for e in entries:
        slot, kind, mass = e[:3]
        a = dict(ex, slot_index=slot, sample_id='%s_%d' % (kind, slot), kind=kind, mass_kg=mass)
        if kind == 'blank':
            a.update(template_id='', mass_kg=0.0, assumed_unreported_fractions={'C': None, 'H': None, 'N': None, 'S': None})
        elif len(e) > 3:
            a['template_id'] = e[3]
        w.ev('sample.load', **a)


def _vario(w, stage, curve):
    w.ag('power.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('furnace.configure', combustion_k=1373.15, reduction_k=1123.15)
    w.ag('method.configure', **w.example('method.configure'))
    w.until(lambda: w.v('furnace.ready'), 3600.0, 5.0)
    vario_load_capsules(w.b, [(0, 'blank', 0.0)] + [(s, 'standard', m) for s, m in zip(STANDARDS[VARIO]['slots'], STANDARDS[VARIO]['masses_kg'])]
                        + [(4, 'sample', VARIO_SAMPLE[1], VARIO_SAMPLE[0])])
    if not _upto(stage, 'blank'):
        return
    w.ag('sequence.configure', slot_indices=[0])
    _measure(w, start='sequence.start', slot='sequence.phase')
    if not _upto(stage, 'standards'):
        return
    w.ag('sequence.configure', slot_indices=STANDARDS[VARIO]['slots'])
    _measure(w, start='sequence.start', slot='sequence.phase')
    if not _upto(stage, 'calibrated'):
        return
    res = w.ag('result.read')['result']
    for el in ('C', 'H', 'N', 'S'):
        obs = [{'reference_id': STANDARDS[VARIO]['reference'], 'sample_mass_kg': a['mass_kg'], 'peak_area_s': a['areas_s'][el]}
               for a in res['completed_assays'] if a['kind'] == 'standard']
        w.ag('calibration.fit', element=el, observations=obs, accept_same_method_basis=True)


RGA_GAS = {'label': 'gas', 'kind': 'sample', 'components': [{'species_id': 'nitrogen', 'amount_mol': 6e-11}], 'standard_species_id': None,
           'accept_unknown_ei_conditions': True}
RGA_STANDARDS_MOL = [2e-11, 5e-11, 1e-10]


def rga_admit(lab, kind='sample', **kw):
    """RGA100: the lab admits a gas into the sealed chamber (the filament must be off)."""
    w = World(lab, RGA)
    a = dict(RGA_GAS, kind=kind)
    if kind == 'blank':
        a.update(label='blank', components=[])
    elif kind == 'standard':
        a.update(label='n2 standard', standard_species_id='nitrogen')
    a.update(kw)
    w.ev('sample.load', **a)


def _rga(w, stage, curve):
    w.ev('vacuum.pump', enabled=True)
    w.ag('power.set', enabled=True)
    w.until(lambda: w.v('instrument.ready'), 3600.0, 10.0)
    if not _upto(stage, 'ready'):
        return
    w.ag('method.configure', **dict(w.example('method.configure'), candidates=['nitrogen']))
    rga_admit(w.b, 'blank')
    w.ag('filament.set', enabled=True)
    if not _upto(stage, 'blank'):
        return
    _measure(w, start='scan.start', slot='scan.phase')
    if not _upto(stage, 'standards'):
        return
    for n in RGA_STANDARDS_MOL:
        w.ag('filament.set', enabled=False)
        rga_admit(w.b, 'standard', components=[{'species_id': 'nitrogen', 'amount_mol': n}])
        w.ag('filament.set', enabled=True)
        _measure(w, start='scan.start', slot='scan.phase')
    if not _upto(stage, 'calibrated'):
        return
    w.ag('calibration.fit', curve_id=curve, species_id='nitrogen', record_ids=['record_2', 'record_3', 'record_4'])
    w.ag('calibration.select', curve_ids=[curve])


def _rga_sample(w):
    on = w.v('filament.enabled')
    if on:
        w.ag('filament.set', enabled=False)
    rga_admit(w.b, 'sample')
    if on:
        w.ag('filament.set', enabled=True)


ORBI_SAMPLE = {'label': 'host_prepared_benzamide_solution', 'kind': 'sample', 'volume_m3': 1e-07, 'components': [{'template_id': 'sm808001', 'amount_mol': 1e-08}],
               'accept_transferred_reference': True}
ORBI_REFS = ['sm800003', 'sm808001', 'sm814103']


def orbi_load(lab, kind='sample', **kw):
    """Orbitrap: the lab puts a solution on the source (source must be off; it is switched back on by the caller)."""
    w = World(lab, ORBI)
    a = dict(ORBI_SAMPLE, kind=kind)
    if kind == 'blank':
        a.update(label='blank', components=[])
    a.update(kw)
    w.ev('sample.load', **a)


def _orbi(w, stage, curve):
    w.ag('power.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('method.configure', **w.example('method.configure'))
    orbi_load(w.b, 'blank')
    w.ag('source.set', enabled=True)
    w.until(lambda: w.v('instrument.ready'), 600.0, 1.0)
    if not _upto(stage, 'blank'):
        return
    _measure(w, start='scan.start', slot='scan.phase')
    if not _upto(stage, 'standards'):
        return
    for t in ORBI_REFS:
        w.ag('source.set', enabled=False)
        orbi_load(w.b, 'standard', label='mass reference', components=[{'template_id': t, 'amount_mol': 1e-08}])
        w.ag('source.set', enabled=True)
        w.until(lambda: w.v('instrument.ready'), 600.0, 1.0)
        _measure(w, start='scan.start', slot='scan.phase')
    if not _upto(stage, 'calibrated'):
        return
    w.ag('calibration.fit', curve_id=curve, record_ids=['record_2', 'record_3', 'record_4'])
    w.ag('calibration.select', curve_id=curve)


def _orbi_sample(w):
    on = w.v('source.enabled')
    if on:
        w.ag('source.set', enabled=False)
    orbi_load(w.b, 'sample')
    if on:
        w.ag('source.set', enabled=True)
        w.until(lambda: w.v('instrument.ready'), 600.0, 1.0)


LECO_SAMPLE = {'template_id': 'leco_503558_reference', 'kind': 'sample', 'mass_kg': 0.00012, 'declared_elements': ['O', 'N', 'H'],
               'mass_basis': 'source_conditioned', 'dry_mass_fraction': None, 'accept_source_condition_mismatch': True}
LECO_STD_MASSES = [1.0e-4, 1.2e-4, 1.5e-4]


def leco_new_crucible(lab):
    """LECO: open the electrode furnace, swap the crucible (operator), close it - the furnace motions are the agent capability
    furnace.open, done here by the operator as part of the set-up."""
    w = World(lab, LECO)
    w.ag('furnace.open', opened=True)
    w.adv(10.0)
    if w.v('crucible.installed'):
        w.ev('crucible.remove', recipient_id='host_crucible_waste')
    w.ev('crucible.install', graphite_mass_kg=0.002, blank_element_masses_kg={'O': 1e-09, 'N': 2e-10, 'H': 1e-10})
    w.ag('furnace.open', opened=False)
    w.adv(10.0)


def leco_load(lab, kind='sample', loader_back=True, **kw):
    """LECO: a fresh crucible, the loader down, the operator drops the weighed sample, the loader back (ready to measure)."""
    w = World(lab, LECO)
    leco_new_crucible(lab)
    w.ag('loading.set', loading=True)
    w.adv(10.0)
    a = dict(LECO_SAMPLE, kind=kind)
    if kind == 'blank':
        a.update(template_id='', mass_kg=0.0, declared_elements=[])
    a.update(kw)
    w.ev('sample.load', **a)
    if loader_back:
        w.ag('loading.set', loading=False)
        w.adv(10.0)


def _leco(w, stage, curve):
    w.ag('power.set', enabled=True)
    w.ag('gas.set', enabled=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('method.configure', **w.example('method.configure'))
    if not _upto(stage, 'blank'):
        return
    leco_load(w.b, 'blank')
    _measure(w)
    if not _upto(stage, 'standards'):
        return
    for m in LECO_STD_MASSES:
        leco_load(w.b, 'standard', mass_kg=m)
        _measure(w)
    if not _upto(stage, 'calibrated'):
        return
    w.ag('calibration.fit', record_ids=['record_2', 'record_3', 'record_4'], elements=['O', 'N', 'H'])
    w.ag('calibration.select', curve_id='curve_1')


IRMS_REFERENCE_DELTAS = {'13C': -0.0367, '18O': 0.0251}


def _irms(w, stage, curve):
    w.ag('power.set', on=True)
    w.ag('vacuum.pump_down')
    w.until(lambda: w.v('vacuum.state') == 'ready', 4 * 3600.0, 30.0)
    if not _upto(stage, 'ready'):
        return
    w.ag('source.set', filament=True)
    w.until(lambda: w.v('status.led') == 'green', 600.0, 10.0)
    w.ag('magnet.set_species', species='CO2')
    w.ag('centrion.set', inlet=1, carrier_on=True)
    w.ag('reference.assign', gas=1, deltas=dict(IRMS_REFERENCE_DELTAS))
    if not _upto(stage, 'standards'):
        return
    w.ag('acquisition.start', duration_s=600.0, reference_pulses=[{'gas': 1, 'start_s': 20.0, 'duration_s': 20.0}])
    w.ag('sample.inject', inlet=1, species='CO2', amount_mol=4e-06, substance_id='usgs40', delay_s=100.0)
    w.ag('sample.inject', inlet=1, species='CO2', amount_mol=4e-06, substance_id='usgs41', delay_s=250.0)
    w.adv(620.0)
    if not _upto(stage, 'calibrated'):
        return
    w.ag('calibration.normalize', isotope='13C', standards=[{'peak': 2, 'substance_id': 'usgs40'}, {'peak': 3, 'substance_id': 'usgs41'}])


def irms_declare(lab, sample_id='leaf_a', deltas=None):
    """isoprime: the lab declares a sample (its true deltas are the host's) so that it can be injected by its id."""
    World(lab, IRMS).ev('sample.declare', sample_id=sample_id, deltas=dict(deltas or {'13C': -0.0281, '15N': 0.0034}))


SEAL_KITS = ['nitrate_stock', 'nitrite_stock', 'ammonium_stock', 'phosphate_stock', 'silicate_stock', 'reagents_nitrate_nitrite', 'reagents_nitrite',
             'reagents_ammonia', 'reagents_phosphate', 'reagents_silicate', 'wash_water', 'cadmium_activation']
SEAL_TOPS = {'nitrate_stock': 0.0028, 'ammonium_stock': 0.006, 'phosphate_stock': 0.008, 'silicate_stock': 0.026}
SEAL_STOCK = {'nitrate_stock': 1.0, 'ammonium_stock': 1.0, 'phosphate_stock': 0.1, 'silicate_stock': 0.1}
SEAL_LEVELS = [0.0, 0.25, 0.5, 1.0]
SEAL_RIVER = {'nitrate_n': 0.0018, 'ammonium_n': 0.0002, 'phosphate_p': 0.00012, 'silicate_si': 0.0035}
SEAL_CUPS = 'S:22:baseline,S:1:calibrant,S:2:calibrant,S:3:calibrant,S:4:calibrant,A:1:sample,S:22:baseline'


def seal_standards(lab):
    """AA500: the analyst's calibrants std0..std3 (mixed stocks, 0 / 25 / 50 / 100 % of the top standard) in S:1..S:4, wash water in S:22."""
    w = World(lab, SEAL)
    w.ag('sampler.load', rack='S', position=22, item_id='wash_water', volume_m3=1e-05)
    for i, f in enumerate(SEAL_LEVELS):
        comps = [{'source_id': k, 'volume_m3': 1e-4 * v / SEAL_STOCK[k] * f} for k, v in SEAL_TOPS.items() if f > 0]
        comps.append({'source_id': 'wash_water', 'volume_m3': 1e-4 - sum(x['volume_m3'] for x in comps)})
        w.ag('standard.prepare', standard_id='std%d' % i, volume_m3=1e-4, components=comps)
        w.ag('sampler.load', rack='S', position=1 + i, item_id='std%d' % i, volume_m3=1e-05)


def seal_provide(lab, sample_id='river_1', composition=None):
    World(lab, SEAL).ev('sample.provide', sample_id=sample_id, volume_m3=5e-05, composition=dict(composition or SEAL_RIVER))


def _seal(w, stage, curve):
    for k in SEAL_KITS:
        w.ev('kit.provide', kit=k, volume_m3=3e-3)
    for c, rg in (w.v('reagents.levels_m3') or {}).items():
        for r in rg:
            w.ag('reagent.load', channel=c, reagent=r, volume_m3=4e-4)
    w.ag('power.set', on=True)
    if not _upto(stage, 'ready'):
        return
    w.ag('system.startup')
    w.until(lambda: w.v('system.state') == 'ready', 6000.0, 60.0)
    seal_standards(w.b)
    seal_provide(w.b)
    if not _upto(stage, 'standards'):
        return
    w.ag('sampler.load', rack='A', position=1, item_id='river_1', volume_m3=1e-05)
    cups = [dict(zip(('rack', 'position', 'type'), (c.split(':')[0], int(c.split(':')[1]), c.split(':')[2]))) for c in SEAL_CUPS.split(',')]
    w.ag('run.start', cups=cups, sample_s=60.0, wash_s=30.0)
    w.until(lambda: (w.v('run.status') or {}).get('state') not in ('running', 'draining'), 20000.0, 60.0)


BRING_UP = {ICPE: _icp, ICPMS: lambda w, s, c: _icp(w, s, c, ms=True), TOC: _toc, AA: _aa, EDX: _edx, MX: _mx, DMA: _dma, VARIO: _vario,
            RGA: _rga, ORBI: _orbi, LECO: _leco, IRMS: _irms, SEAL: _seal}
SAMPLE_LOADERS = {RGA: _rga_sample, ORBI: _orbi_sample, LECO: lambda w: leco_load(w.b, 'sample')}


MEMBERS = (ICPE, ICPMS, TOC, AA, EDX, MX, DMA, VARIO, RGA, ORBI, LECO, IRMS, SEAL)


def bare(cfg):
    """The class's members without the default lab's set-up: the default lab (chemlab/default_world.py) already holds this
    world's start-of-day stage, recorded as set-up steps; tasks and lowering cases apply their own stages to the bare lab."""
    for e in cfg['instruments']:
        if e['asset_type_id'].split('.', 1)[1] in MEMBERS:
            e['default_world'] = False
    return cfg


def config(base=None):
    """all203.json (or ``base``) with the members bare (see bare()): the class's world is applied by tasks and lowering cases
    (stage functions above)."""
    return bare(copy.deepcopy(base or load_config()))
