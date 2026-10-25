"""Host-side world for the particle_surface class: what the lab operator has done before the agent starts (fill liquids and
gases, put the sample in, close the access where the operator closes it).  Every function takes a LabBackend (the lowering
harness calls it on the lab and on its twin; tasks call it with lab.backend) and uses trusted evaluator calls only; the default
lab (all203.json) has just plant.configure for these instruments.

Contract example arguments are used for the operator's materials (sample.load, liquid.fill, ...), so the worlds follow the
contracts and change when they change.
"""
import copy

DLS, PSA, DIA, TURB, ASORB, PMAST, PLUX, UPYC, ACHEM, DVS, SURP, SIGMA, THETA, MEDL, OXTR = (
    'antonpaar_litesizerdls', 'antonpaar_psa1190', 'antonpaar_litesizerdia', 'microtrac_turbiscanlab', 'antonpaar_autosorb6100',
    'antonpaar_poremaster60', 'porometer_poroluxrevo', 'antonpaar_ultrapyc5000', 'micromeritics_autochemiii', 'surface_dvsresolution',
    'antonpaar_surpass3', 'biolin_sigma700', 'biolin_thetaflex', 'biolin_mediuml', 'ametek_oxtran')
MEMBERS = (DLS, PSA, DIA, TURB, ASORB, PMAST, PLUX, UPYC, ACHEM, DVS, SURP, SIGMA, THETA, MEDL, OXTR)


class WorldError(AssertionError):
    pass


def ex(b, iid, cap):
    """The contract's example arguments of a capability."""
    return copy.deepcopy((b.instruments[iid].contract['capabilities'].get(cap) or {}).get('example_arguments') or {})


def ev(b, iid, cap, args=None, **kw):
    """One trusted operator / evaluator call that must succeed."""
    n = getattr(b, '_ps_world_n', 0) + 1
    b._ps_world_n = n
    a = dict(args if args is not None else {}, **kw)
    r = b.evaluator_invoke(iid, cap, a, 'ps-world-%s-%d' % (iid, n))
    if r.get('status') not in ('succeeded', 'accepted', 'running'):
        raise WorldError('%s %s %s -> %s %s' % (iid, cap, a, r.get('status'), (r.get('error') or {}).get('code')))
    return r


def wait(b, seconds):
    b.advance(seconds)


def until(b, iid, slot, values, max_s, step=1.0):
    t = 0.0
    while t < max_s:
        v = b.evaluator_read(iid, [slot])['observations'][slot]
        v = v.get('value') if isinstance(v, dict) else v
        if v in values:
            return t
        b.advance(step)
        t += step
    raise WorldError('%s %s not in %s after %s s' % (iid, slot, values, max_s))


# ------------------------------------------------------------------ particle sizing
def dls(b, iid=DLS, closed=True, power=False, cell='omega', sample=None):
    """Litesizer DLS: the operator filled the cuvette (omega cell for zeta) and closed the measuring cell; power as given."""
    ev(b, iid, 'power.set', enabled=True)
    for cap, a in [('lid.release', {}), ('lid.set', {'angle_rad': 1.7}), ('cuvette.set', {'lift_m': 0.12}), ('cell.configure', {'kind': cell}),
                   ('cell_cap.set', {'lift_m': 0.022})]:
        ev(b, iid, cap, a)
    s = dict(ex(b, iid, 'sample.load'), **(sample or {}))
    if cell == 'quartz':
        s['volume_m3'] = 1e-6
    ev(b, iid, 'sample.load', s)
    if closed:
        for cap, a in [('cell_cap.set', {'lift_m': 0.0}), ('cuvette.set', {'lift_m': 0.0}), ('lid.set', {'angle_rad': 0.0})]:
            ev(b, iid, cap, a)
    if not power:
        ev(b, iid, 'power.set', enabled=False)


def psa(b, iid=PSA, background=True, sample=True, power=True):
    """PSA 1190 (wet): the operator filled the dispersion tank; with background=True also switched on, waited for the laser and
    measured the background; with sample=True added the sample through the tank lid."""
    ev(b, iid, 'tank_lid.set', slide_m=0.17)
    ev(b, iid, 'liquid.fill', volume_m3=0.00045)
    ev(b, iid, 'tank_lid.set', slide_m=0.0)
    if power or background:
        ev(b, iid, 'power.set', enabled=True)
    if background:
        wait(b, 61)
        ev(b, iid, 'background.start')
        until(b, iid, 'acquisition.phase', ('succeeded', 'failed'), 300)
    if sample:
        ev(b, iid, 'tank_lid.set', slide_m=0.17)
        ev(b, iid, 'sample.load', dict(ex(b, iid, 'sample.load'), accept_range_truncation=True))
        ev(b, iid, 'tank_lid.set', slide_m=0.0)
    if not power:
        ev(b, iid, 'power.set', enabled=False)


def dia(b, iid=DIA, background=True, sample=True, power=True):
    """Litesizer DIA (liquid module): the operator filled the tank; background image; sample added through the lid."""
    ev(b, iid, 'lid.set', angle_rad=1.55)
    ev(b, iid, 'liquid.fill', volume_m3=0.0003)
    ev(b, iid, 'lid.set', angle_rad=0.0)
    if power or background:
        ev(b, iid, 'power.set', enabled=True)
    if background:
        ev(b, iid, 'background.capture')
    if sample:
        ev(b, iid, 'lid.set', angle_rad=1.55)
        ev(b, iid, 'sample.load', ex(b, iid, 'sample.load'))
        ev(b, iid, 'lid.set', angle_rad=0.0)
    if not power:
        ev(b, iid, 'power.set', enabled=False)


def turbiscan(b, iid=TURB, closed=False, power=False):
    """TURBISCAN: the operator put the filled vial into the raised holder (lid open); closed=True: also closed and lowered it."""
    ev(b, iid, 'sample.set_position', distance_m=0.105)
    ev(b, iid, 'lid.set', opened=True)
    ev(b, iid, 'sample.load', ex(b, iid, 'sample.load'))
    if closed:
        ev(b, iid, 'lid.set', opened=False)
        ev(b, iid, 'sample.set_position', distance_m=0.0)
    if power:
        ev(b, iid, 'power.set', enabled=True)


# ------------------------------------------------------------------ pores, surface area, density
def autosorb(b, iid=ASORB, stations=(0,), closed=False, power=True, coolant=True, calibrated=False, degas_slot=None):
    """Autosorb 6100: the operator mounted the (pre-degassed) sample tubes on the stations and filled the LN2 dewar; closed=True:
    tubes up, guard closed, dewar raised; calibrated=True: pressure and free-space calibration per station entered."""
    ev(b, iid, 'power.set', enabled=True)
    ev(b, iid, 'guard.set', angle_rad=1.5)
    for st in stations:
        ev(b, iid, 'sample%d.set' % st, drop_m=0.18)
        ev(b, iid, 'sample.load', dict(ex(b, iid, 'sample.load'), station=st, sample_id='sample_%d' % st))
    if degas_slot is not None:
        bank = degas_slot // 3
        ev(b, iid, 'heater%d.set' % bank, drop_m=0.16)
        ev(b, iid, 'degas.load', dict(ex(b, iid, 'degas.load'), slot=degas_slot))
        ev(b, iid, 'heater%d.set' % bank, drop_m=0.0)
    if coolant:
        ev(b, iid, 'coolant.fill', volume_m3=0.003)
    if calibrated:
        for st in stations:
            ev(b, iid, 'pressure.calibrate', dict(ex(b, iid, 'pressure.calibrate'), station=st))
            ev(b, iid, 'volume.calibrate', dict(ex(b, iid, 'volume.calibrate'), station=st))
    if closed:
        for st in stations:
            ev(b, iid, 'sample%d.set' % st, drop_m=0.0)
        ev(b, iid, 'guard.set', angle_rad=0.0)
        ev(b, iid, 'dewar.set', lift_m=0.29)
    if not power:
        ev(b, iid, 'power.set', enabled=False)


def poremaster(b, iid=PMAST, power=True, calibrated=False, closed=False):
    """PoreMaster 60: mercury filled, the penetrometer with the dry sample in the low-pressure port 0 (guard open);
    calibrated=True: pressure, volume and blank calibration entered."""
    ev(b, iid, 'power.set', enabled=True)
    if calibrated:
        for cap in ('pressure.calibrate', 'volume.calibrate', 'blank.calibrate'):
            ev(b, iid, cap, ex(b, iid, cap))
    ev(b, iid, 'mercury.fill', mass_kg=0.1)
    ev(b, iid, 'guard0.set', angle_rad=1.4)
    ev(b, iid, 'sample.load', ex(b, iid, 'sample.load'))
    if closed:
        ev(b, iid, 'guard0.set', angle_rad=0.0)
    if not power:
        ev(b, iid, 'power.set', enabled=False)


def porolux(b, iid=PLUX, wetted=False, closed=False, power=False):
    """POROLUX Revo: the membrane disc in the open holder, wetting liquid and nitrogen supplied."""
    ev(b, iid, 'sample.load', ex(b, iid, 'sample.load'))
    ev(b, iid, 'liquid.load', ex(b, iid, 'liquid.load'))
    ev(b, iid, 'gas.load', ex(b, iid, 'gas.load'))
    if wetted:
        ev(b, iid, 'sample.wet')
    if closed:
        ev(b, iid, 'holder.set', opened=False)
    if power:
        ev(b, iid, 'power.set', enabled=True)


def ultrapyc(b, iid=UPYC, closed=False):
    """Ultrapyc 5000 (switched on by the lab set-up): the operator chose the cell insert (the contract's example method: the
    runtime takes a sample only for a configured cell), opened the cell and put the filled cup in (cup raised)."""
    ev(b, iid, 'method.configure', ex(b, iid, 'method.configure'))
    for cap, a in [('seal.set', {'angle_rad': 0.0}), ('lock.set', {'angle_rad': 0.0}), ('lid.set', {'lift_m': 0.13}), ('cup.set', {'lift_m': 0.12})]:
        ev(b, iid, cap, a)
    ev(b, iid, 'sample.load', ex(b, iid, 'sample.load'))
    if closed:
        for cap, a in [('cup.set', {'lift_m': 0.0}), ('lid.set', {'lift_m': 0.0}), ('lock.set', {'angle_rad': 1.1}), ('seal.set', {'angle_rad': 1.4})]:
            ev(b, iid, cap, a)


def autochem(b, iid=ACHEM, closed=False, mode='tpd'):
    """AutoChem III: the operator inserted the sample tube (released, withdrawn) and connected the carrier gas (inlet 1)."""
    ev(b, iid, 'sample.release', angle_rad=0.6)
    ev(b, iid, 'sample.set_position', distance_m=0.12)
    ev(b, iid, 'sample.load', dict(ex(b, iid, 'sample.load'), mode=mode))
    ev(b, iid, 'gas.load', ex(b, iid, 'gas.load'))
    if closed:
        ev(b, iid, 'sample.set_position', distance_m=0.0)
        ev(b, iid, 'sample.release', angle_rad=0.0)
        ev(b, iid, 'furnace.set_open', opened=False)


def dvs(b, iid=DVS, closed=False, power=False, sample=True, tared=False):
    """DVS Resolution: water reservoir and dry-gas supply filled; tared=True: the operator switched on and tared the microbalance
    with the empty pan first; sample=True: then put the sample pan in (door open, chamber lowered)."""
    ev(b, iid, 'stock.load', ex(b, iid, 'stock.load'))
    ev(b, iid, 'gas.load', ex(b, iid, 'gas.load'))
    if tared:
        ev(b, iid, 'power.set', enabled=True)
        wait(b, 2)
        ev(b, iid, 'balance.tare')
    if sample:
        ev(b, iid, 'sample.load', ex(b, iid, 'sample.load'))
    if closed:
        ev(b, iid, 'chamber.set_position', distance_m=0.0)
        ev(b, iid, 'reservoir.open', distance_m=0.0)
        ev(b, iid, 'door.set', opened=False)
    if power:
        ev(b, iid, 'power.set', enabled=True)
    elif tared:
        ev(b, iid, 'power.set', enabled=False)


def surpass(b, iid=SURP, closed=True, power=False, electrolyte=True):
    """SurPASS 3: the sample mounted in the cell; closed=True: cell inserted and clamped, then the electrolyte beaker filled (the
    runtime moves the cell only while no electrolyte is in the circuit, so the operator fills it last)."""
    ev(b, iid, 'lock.set', angle_rad=0.0)
    ev(b, iid, 'cell.set', withdrawal_m=0.095)
    ev(b, iid, 'sample.load', ex(b, iid, 'sample.load'))
    if closed:
        ev(b, iid, 'cell.set', withdrawal_m=0.0)
        ev(b, iid, 'lock.set', angle_rad=1.2)
        if electrolyte:
            ev(b, iid, 'lid.set', lift_m=0.105)
            ev(b, iid, 'electrolyte.load', ex(b, iid, 'electrolyte.load'))
            ev(b, iid, 'lid.set', lift_m=0.0)
    if power:
        ev(b, iid, 'power.set', enabled=True)


# ------------------------------------------------------------------ interfaces
def sigma(b, iid=SIGMA, closed=False, power=False, sample=None):
    """Sigma 700: the vessel with the liquid on the stage (door open), plate probe hanging."""
    ev(b, iid, 'door.set', angle_rad=2.35)
    ev(b, iid, 'sample.load', dict(ex(b, iid, 'sample.load'), **(sample or {})))
    if closed:
        ev(b, iid, 'door.set', angle_rad=0.0)
    if power:
        ev(b, iid, 'power.set', enabled=True)


def theta(b, iid=THETA, power=False):
    """Theta Flex: the substrate on the stage and the syringe filled with the probe liquid."""
    ref = ex(b, iid, 'sample.load')['reference_id']
    ev(b, iid, 'sample.load', reference_id=ref, accept_unknown_conditions=True)
    ev(b, iid, 'stock.load', reference_id=ref, volume_m3=2e-7)
    if power:
        ev(b, iid, 'power.set', enabled=True)


def mediuml(b, iid=MEDL, power=True, plate_down=False, solution=True):
    """KSV NIMA Medium trough: subphase (PBS) filled with the Wilhelmy plate lifted; the spreading solution in the syringe."""
    ev(b, iid, 'probe.move', distance_m=0.04)
    ev(b, iid, 'subphase.load', ex(b, iid, 'subphase.load'))
    if power:
        ev(b, iid, 'power.set', enabled=True)
    if plate_down:
        ev(b, iid, 'probe.move', distance_m=0.0)
    if solution:
        ev(b, iid, 'solution.load', ex(b, iid, 'solution.load'))


def oxtran(b, iid=OXTR, cells=('A',), clamped=False):
    """OX-TRAN 2/22 (switched on by the lab set-up): test gases connected, the film mounted in the drawer of each cell."""
    ev(b, iid, 'gas.configure', ex(b, iid, 'gas.configure'))
    for c in cells:
        ev(b, iid, 'drawer.move', cell=c, open=True)
        wait(b, 3)
        ev(b, iid, 'sample.mount', dict(ex(b, iid, 'sample.mount'), cell=c))
        ev(b, iid, 'drawer.move', cell=c, open=False)
        wait(b, 3)
        if clamped:
            ev(b, iid, 'cell.load_unload', cell=c)
            wait(b, 10)


def bare(cfg):
    """The class's members without the default lab's set-up: the default lab (chemlab/default_world.py) already holds these
    worlds (recorded as set-up steps); tasks and lowering cases apply their own world to the bare lab."""
    for e in cfg['instruments']:
        if e['asset_type_id'].split('.', 1)[1] in MEMBERS:
            e['default_world'] = False
    return cfg


WORLDS = {DLS: dls, PSA: psa, DIA: dia, TURB: turbiscan, ASORB: autosorb, PMAST: poremaster, PLUX: porolux, UPYC: ultrapyc,
          ACHEM: autochem, DVS: dvs, SURP: surpass, SIGMA: sigma, THETA: theta, MEDL: mediuml, OXTR: oxtran}
