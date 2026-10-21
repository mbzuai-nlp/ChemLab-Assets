"""Runtime of CHEM-163 instrument.biotek_405ts - Agilent BioTek 405 TS microplate washer, single 96-tube manifold, internal buffer switching (A-D).

Process model (SI; host clock only - step(dt_s) is the only way time advances):
  - fluid path: four supply bottles on the buffer valves A-D, 108 + 2 mL internal dead volume (guide p.38) as two stirred tanks in series - the
    dispensed liquid's purity after priming V is 1 - exp(-2V/V_dead)(1 + 2V/V_dead) (a 300 mL prime gives >= 95 %, the manufacturer recommendation); external vacuum
    pump on the switched accessory outlet, two waste bottles (the one with the level sensor stops a run when full), fluid / flow / vacuum / waste sensors
    (instrument configuration screen);
  - wash step (guide p.43-46): carrier to the manifold, per cycle aspirate (manifold descends at the travel rate to the aspirate height, Z steps x
    0.127 mm above the plate seat, delay, optional secondary aspirate) then dispense (volume 50-3000 uL/well at flow rates 3-11, cell-wash rates 1-2 only
    with the cell-wash option; vacuum delay; with the vacuum on, the aspirate tips at the dispense height skim the liquid above the tip (tip below the rim)
    or above the pinned meniscus (tip up to 1.5 mm above the rim); a blocked aspirate tube does not skim), optional
    bottom wash, soak and shake (carrier sent home when soak + shake exceed 1 min, vacuum pump off), final aspirate, carrier home;
  - wells (plate_library.json: volume, depth, SBS well-bottom elevation, mean radius; per-plate bottom-elevation scatter): residual volume after an
    aspiration = bottom layer A g^2/(g + g0) (g = tip height above the well bottom) + floor film A delta0 + wall film (Landau-Levich,
    t = 0.94 l_c Ca^(2/3), Ca = mu U / sigma, l_c = sqrt(sigma / (rho g)), wetted height V / A) + bottom-corner fillet, with the liquid density,
    viscosity and surface tension of wash_liquid_library.json at the room temperature; calibrated on the two manufacturer statements (<= 2 uL at the
    default 29 steps with water + 0.1 % Tween 20; about 100 uL at 50 steps);
  - wash efficiency: two-compartment wells - the dispense jet mixes the old liquid into the fresh buffer with the efficiency m_jet; the unmixed part
    (1 - m_jet) V0 / (V0 + v) stays as a bottom layer, which soak and shake mix in; the next aspiration leaves the bottom layer first; non-specifically adsorbed
    species desorb (k_off, faster with surfactant); specifically bound species stay (slow k_off); overflow when a well exceeds its volume (spill to the
    four neighbours: cross-contamination); drips from the parked manifold onto a plate left under it;
  - adherent cells: detachment by the dispense jet wall shear (jet speed from the tube flow), by aspiration close to the cell layer, and by aspiration
    to dryness with a long dwell; the cell-wash settings (rates 1-2 through the low-flow tubing, CW travel rates, high aspirate height) protect them;
  - tube clogging: dispense and aspirate tubes clog when salt / protein buffers dry in an idle manifold (rate by the salt mass fraction of the liquid
    left in the tubes and the idle time; a water rinse keeps the manifold wet); partially clogged dispense tubes under-dispense, clogged aspirate tubes
    leave their wells full; the flow sensor reports a total-flow deficit (Verify clog detection is a "Q"-model option: unsupported);
  - wash log (programme, cycles, alarms), waste and buffer bookkeeping, energy ledger; the plate content (unbound / non-specific / specific amounts,
    cells, residual volumes, clogs) is evaluator truth.
Generic schema / capability / session framework follows the CHEM-162 runtime.

1.1.0 adds the touch-screen software of the 405 TS (Getting Started Guide 1171009 Rev C) as device commands (DEVICE_CAPS): the protocol store and editor,
protocol runs with Wash / Aspirate / Dispense / Prime / Soak / Delay / Remark / Loop steps, the onboard Maintenance / QC / sample protocols, washer
settings (manifold, magnet height offset, sensors, vacuum dissipate delay), AutoPrime, the clock, Self-Check and STOP; and the front panel (panel.press /
panel.touch / panel.capture / panel.frame): the touch screen and the power switch of panel_ui.json beside the contract, run by the generic engine
_panel_ui.py on top of these commands (no second copy of the logic).
"""
from __future__ import annotations

import copy
import datetime
import functools
import hashlib
import json
import math
import os
import re
import tempfile
import weakref
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.biotek_405ts"
CATALOG_ID = "CHEM-163"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/biotek_405ts'
LIQUID_LIB_PATH = ASSET_DIR / 'wash_liquid_library.json'
PLATE_LIB_PATH = ASSET_DIR / 'plate_library.json'
G = 9.80665

# ---------------------------------------------------------------- manufacturer values (workbook row 168, 405 TS Getting Started Guide, EL406 manual)
VOLUME_RANGE_M3 = (50e-9, 3000e-9)          # 96-tube manifold dispense volume per well
VOLUME_STEP_M3 = 1e-9                       # 1 uL increments
CYCLES_RANGE = (1, 250)
Z_STEP_M = 0.127e-3                         # manifold height per Z step
ASP_Z_DEFAULT, DISP_Z_DEFAULT = 29, 121
ASP_DELAY_MAX_S = 5.0                       # 0-5000 ms
VAC_DELAY_M3 = (10e-9, 350e-9)
SHAKE_SOAK_MAX_S = 3600.0
HOME_AFTER_S = 60.0                         # carrier home when soak + shake exceed 1 min (vacuum pump off)
DEAD_VOLUME_M3 = 110e-6                     # 108 mL + 2 mL buffer-switching tubing
LINE_TANKS = 2                              # tanks-in-series of the fluid path (2: a 300 mL prime -> 97 %, 3 x dead volume -> 98 %; engineering, meets the >= 95 % statement)
PRIME_FOR_CHANGE_M3 = 300e-6                # manufacturer prime for a buffer change (>= 95 % purity)
MANIFOLD_PRIME_M3 = 50e-6
TRAVEL_SPEED = {'2': 3.4e-3, '3': 4.0e-3}   # m/s (EL406 manual, same manifold family)
ACCESSORY_OUTLET_A = 5.0
RESIDUAL_SPEC_M3 = 2e-9                     # <= 2 uL/well after a 3-cycle wash (0.1 % Tween 20 water, Costar flat)
CV_SPEC = 0.03                              # <= 3 % CV at 300 uL/well
# engineering values (not published)
FLOW_RATE_M3_S = {1: 10e-9, 2: 20e-9, 3: 25e-9, 4: 35e-9, 5: 50e-9, 6: 70e-9, 7: 100e-9, 8: 135e-9, 9: 175e-9, 10: 220e-9, 11: 270e-9}   # per well
TRAVEL_RATES = ['1', '2', '3', '4', '5', '1CW', '2CW', '3CW', '4CW', '6CW']
TRAVEL = {'1': (1.5e-3, 1.0), '2': (3.4e-3, 1.0), '3': (4.0e-3, 1.0), '4': (6.0e-3, 1.0), '5': (8.5e-3, 1.0),        # (speed m/s, disturbance factor)
          '1CW': (1.0e-3, 0.45), '2CW': (1.5e-3, 0.40), '3CW': (2.2e-3, 0.35), '4CW': (3.0e-3, 0.30), '6CW': (3.0e-3, 0.20)}
PARK_TIP_M = 0.034                          # aspirate tips above the plate seat when parked (scene joint 0; above the 250-step maximum 31.75 mm)
DISP_TIP_ABOVE_ASP_M = 0.0025
CARRIER_TRAVEL_M = 0.160                    # home (load) -> under the manifold (scene joint -0.160 m)
CARRIER_MOVE_S = 1.0
LIFT_SPEED_M_S = 0.030
VALVE_S = 0.4
TUBE_INNER_R_M = 0.25e-3                    # dispense tube bore
LOW_FLOW_R_M = 0.40e-3                      # cell-wash low-flow outlet (wider, slower jet)
G0_M = 0.10e-3                              # bottom-layer drainage length (50 steps -> ~88 uL in a Costar 360 uL well; guide: 'approximately 100 uL')
DELTA0_M = 20e-6                            # floor film left around the tip at the well bottom
FILLET_FACTOR = 0.10                        # bottom-corner fillet radius / capillary length
RESIDUAL_SCATTER = 0.12                     # lognormal scatter of the residual per well
WBE_SCATTER_M = 0.04e-3                     # well-bottom elevation scatter within a plate (SLAS-6 variation)
CV_REPEAT, CV_TUBE, CV_ABS_M3 = 0.012, 0.012, 1.0e-9    # dispense scatter: shot-to-shot, fixed tube-to-tube, absolute
MIX_JET_M_S = 0.6                           # jet speed for e-fold mixing of the residual into the fresh buffer
MIX_MIN = 0.5                               # convective mixing of the residual by a slowly poured buffer (low-flow dispense)
SHAKE_TAU = {'slow': 20.0, 'medium': 8.0, 'fast': 3.0, 'variable': 6.0}
SOAK_TAU_S = 1.0e4                          # soak: diffusive mixing of the residual layer (r^2 / D for small molecules)
K_OFF_NS, K_OFF_NS_SURF, K_OFF_SPEC = 0.004, 0.04, 2e-6  # 1/s: non-specific desorption (plain / with surfactant), specific
TAU_CELL_PA = 2.5                           # wall shear for e-fold detachment of adherent cells (weak adhesion; x4 strong)
CELL_LAYER_CLEAR_M = 0.35e-3                # tip gap below which aspiration scrapes the cell layer
DRY_DWELL_S = 20.0                          # a nearly dry well (< 3 uL) longer than this damages the monolayer
CLOG_TAU_S = 4.0 * 86400.0                  # idle-manifold clogging time scale for 1 % salt (scales with 1 / salt fraction)
MENISCUS_M = 1.5e-3                         # liquid pinned at the rim can bulge this far above it before it spills (engineering)
IDLE_W, ACTIVE_W = 14.0, 46.0               # electrical power (two internal 60 W supplies)
DROP_M3 = 0.25e-9                           # liquid on the outside of a tube tip after an aspiration
DROPS_PER_TIP = 4                           # drops a wetted dispense tube tip can shed before it is dry (engineering assumption)
DRIP_P_PER_S = 1.5e-4                       # per tube and second while a plate sits under the parked manifold


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- schema helpers
def number(unit='1', lo=None, hi=None):
    s = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def obj(properties, optional=()):
    return dict(type='object', properties=properties, required=[k for k in properties if k not in optional], additionalProperties=False)


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


def array(s, minimum=0, maximum=4096):
    return dict(type='array', items=s, minItems=minimum, maxItems=maximum)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


BOOL = {'type': 'boolean'}
FREEOBJ = {'type': 'object'}                       # free-form mapping (settings echo, composition by liquid id)
TEXT = {'type': 'string'}
INT = {'type': 'integer', 'x-unit': '1'}


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


def validate(s, v):
    if 'anyOf' in s:
        for o in s['anyOf']:
            try:
                validate(o, v)
                return
            except ActionRejected:
                pass
        raise ActionRejected('INVALID_ARGUMENT')
    k = s.get('type')
    if k in ('number', 'integer'):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (k == 'integer' and not float(v).is_integer()):
            raise ActionRejected('INVALID_ARGUMENT')
        if not s.get('minimum', -math.inf) <= v <= s.get('maximum', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'object':
        if not isinstance(v, dict):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'properties' in s:
            req = set(s.get('required', s['properties']))
            if not req <= set(v) or not set(v) <= set(s['properties']):
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                validate(s['properties'][key], value)
    elif k == 'array':
        if not isinstance(v, list) or not s.get('minItems', 0) <= len(v) <= s.get('maxItems', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
        for x in v:
            validate(s['items'], x)
    elif k == 'boolean' and type(v) is not bool:
        raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'string':
        if not isinstance(v, str) or len(v) > s.get('maxLength', 10 ** 6) or len(v) < s.get('minLength', 0):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'pattern' in s:
            import re
            if not re.fullmatch(s['pattern'], v):
                raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- template libraries
@functools.lru_cache(maxsize=1)
def liquid_lib():
    return json.loads(LIQUID_LIB_PATH.read_text())


@functools.lru_cache(maxsize=1)
def plate_lib():
    return json.loads(PLATE_LIB_PATH.read_text())


def liquid_props(lid, T):
    """(density kg/m3, viscosity Pa s, surface tension N/m) of a library liquid at T (linear in T, log-linear for the viscosity), clamped to its grid."""
    e = liquid_lib()['substances'][lid]
    Ts = np.array(e['temperature_K'])
    t = float(np.clip(T, Ts[0], Ts[-1]))
    rho = float(np.interp(t, Ts, e['density_kg_m3']))
    mu = float(np.exp(np.interp(t, Ts, np.log(e['viscosity_Pa_s']))))
    sig = float(np.interp(t, Ts, e['surface_tension_N_m']))
    return rho, mu, sig


def mixture_props(comp, T):
    """Properties of a mixture of library liquids by volume fractions: linear density and surface tension, log-mean viscosity; the surface tension
    of a mixture with a surfactant-containing fraction >= 5 % takes the surfactant value (surface excess)."""
    if not comp:
        return liquid_props('water', T)
    tot = sum(comp.values())
    rho = mu_ln = sig = 0.0
    surf = [k for k in comp if liquid_lib()['substances'][k].get('surfactant')]
    for k, f in comp.items():
        r, m, s = liquid_props(k, T)
        rho += f / tot * r
        mu_ln += f / tot * math.log(m)
        sig += f / tot * s
    if surf and sum(comp[k] for k in surf) / tot >= 0.05:
        sig = min(liquid_props(k, T)[2] for k in surf)
    return rho, math.exp(mu_ln), sig


def salt_fraction(comp):
    """Mass fraction of dissolved salts of a mixture (drives drying / clogging of the tubes)."""
    tot = sum(comp.values()) or 1.0
    return sum(f / tot * sum(liquid_lib()['substances'][k].get('mass_fractions', {}).values()) for k, f in comp.items())


def is_surfactant(comp):
    tot = sum(comp.values()) or 1.0
    return sum(f for k, f in comp.items() if liquid_lib()['substances'][k].get('surfactant')) / tot >= 0.05


# ---------------------------------------------------------------- well physics
def film_volume(V, A, r, rho, mu, sig, U):
    """Landau-Levich wall film left by a receding meniscus at speed U on the wetted height V / A of a cylindrical well of radius r."""
    lc = math.sqrt(sig / (rho * G))
    Ca = mu * max(U, 1e-5) / sig
    t = 0.94 * lc * Ca ** (2.0 / 3.0)
    return 2 * math.pi * r * min(V / A, 0.02) * t


def residual_volume(V, gap, plate, rho, mu, sig, U, delay_s=0.0):
    """Liquid left in a well after aspiration with the tips gap m above the well bottom (gap <= 0: tips on the bottom)."""
    r = plate['mean_well_radius_m']
    A = math.pi * r * r
    g = max(0.0, gap)
    layer = A * g * g / (g + G0_M)
    floor = A * DELTA0_M
    lc = math.sqrt(sig / (rho * G))
    rf = FILLET_FACTOR * lc
    fillet = 2 * math.pi * r * (1 - math.pi / 4) * rf * rf
    film = film_volume(V, A, r, rho, mu, sig, U)
    if delay_s > 0 and film > 0:   # the aspirate delay lets the wall film drain to the bottom, where the tips take it: t = t0 / sqrt(1 + t/tau)
        H = min(V / A, 0.02)
        t0 = film / (2 * math.pi * r * max(H, 1e-6))
        film /= math.sqrt(1 + delay_s * rho * G * t0 ** 2 / (mu * max(H, 1e-6)))
    return min(V, layer + floor + fillet + film)


def jet_speed(rate):
    Q = FLOW_RATE_M3_S[rate]
    rr = LOW_FLOW_R_M if rate <= 2 else TUBE_INNER_R_M
    return Q / (math.pi * rr * rr)


def wall_shear(rate, depth, rho):
    """Wall shear stress of the dispense jet at the well bottom (Pa): stagnation pressure 0.5 rho v^2 scaled down with the liquid depth above the
    bottom (the jet decays over ~2 mm of liquid) and the tube offset to the side of the well (engineering)."""
    v = jet_speed(rate)
    return 0.5 * rho * v * v * 0.02 * math.exp(-depth / 2.0e-3)


# ---------------------------------------------------------------- capabilities and state slots
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


BOTTLES = ['A', 'B', 'C', 'D']
SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
LIBID = dict(type='string', minLength=1, maxLength=60)
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1, vacuum_pump=True, cell_wash_installed=False, waste_capacity_m3=0.008)
PLANT = obj(dict(room_k=number('K', 283.15, 313.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1), vacuum_pump=BOOL,
                 cell_wash_installed=BOOL, waste_capacity_m3=number('m3', 0.004, 0.020)))
PLATE = obj(dict(plate_id=SID, plate_type=LIBID, fill_volume_m3=number('m3', 0.0, 3.6e-7), liquid_id=LIBID, unbound_mol=number('mol', 0.0, 1e-6),
                 nonspecific_mol=number('mol', 0.0, 1e-9), specific_mol=number('mol', 0.0, 1e-9), cells_per_well=number('1', 0.0, 2e5),
                 cell_adhesion=enum(['none', 'weak', 'strong']), wells=nullable(array(integer(0, 95), 1, 96))))
PLATE_EXAMPLE = dict(plate_id='elisa_plate', plate_type='costar_3590', fill_volume_m3=1.0e-7, liquid_id='pbs_1x', unbound_mol=1e-12, nonspecific_mol=2e-14,
                     specific_mol=5e-14, cells_per_well=0.0, cell_adhesion='none', wells=None)
TRAVEL_E = enum(TRAVEL_RATES)
WASH = obj(dict(buffer=enum(BOTTLES), cycles=integer(*CYCLES_RANGE), dispense_volume_m3=number('m3', *VOLUME_RANGE_M3), dispense_rate=integer(1, 11),
                dispense_z_steps=integer(40, 250), aspirate_z_steps=integer(1, 250), travel_rate=TRAVEL_E, aspirate_delay_s=number('s', 0.0, ASP_DELAY_MAX_S),
                secondary_aspirate_z_steps=nullable(integer(1, 250)), vacuum_delay_m3=number('m3', *VAC_DELAY_M3), soak_s=number('s', 0.0, SHAKE_SOAK_MAX_S),
                shake_s=number('s', 0.0, SHAKE_SOAK_MAX_S), shake_intensity=enum(list(SHAKE_TAU)), bottom_wash=BOOL, final_aspirate=BOOL))
WASH_EXAMPLE = dict(buffer='A', cycles=3, dispense_volume_m3=300e-9, dispense_rate=7, dispense_z_steps=DISP_Z_DEFAULT, aspirate_z_steps=ASP_Z_DEFAULT, travel_rate='3',
                    aspirate_delay_s=0.0, secondary_aspirate_z_steps=None, vacuum_delay_m3=10e-9, soak_s=0.0, shake_s=0.0, shake_intensity='medium', bottom_wash=False,
                    final_aspirate=True)
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
ALARMS = ['WASTE_FULL', 'VACUUM_LOW', 'BUFFER_EMPTY', 'FLOW_ERROR', 'POWER_LOST']
RECORD = obj(dict(record_id=TEXT, kind=enum(['wash', 'prime', 'clean']), status=STATUS, started_s=number('s'), finished_s=nullable(number('s')), plate_id=nullable(TEXT),
                  settings=FREEOBJ, cycles_done=INT, programmed_dispense_m3=number('m3'), alarms=array(enum(ALARMS), 0, 12),
                  warnings=array(TEXT, 0, 12), template_source=obj(dict(liquids=array(TEXT, 0, 8), plate=nullable(TEXT)))))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'fluid', 'plate', 'program', 'alarm', 'fault', 'maintenance']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['electrical_energy_j', 'dispensed_m3', 'aspirated_m3', 'primed_m3', 'dripped_m3', 'waste_added_m3', 'overflow_m3', 'carrier_travel_m', 'manifold_travel_m', 'cycles']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'm3' if k.endswith('_m3') else 'm' if k.endswith('_m') else '1') for k in LEDGER_KEYS})

cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['frame_base', 'rear_connectors'],
    '宿主声明实验室条件与外部附件：室温、电源电压、噪声种子、真空泵是否接在附件插座、是否装细胞洗涤选件、废液瓶总容量', 'evaluator', 'simulation')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['switch_power', 'rear_panel'], '主电源开/关')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['switch_power'], '按一下后面板主电源开关（翻转开/关）')
cap('buffer.load', obj(dict(bottle=enum(BOTTLES), liquid_id=LIBID, volume_m3=number('m3', 0.0, 0.010))), obj(dict(bottle=TEXT, liquid_id=TEXT, volume_m3=number('m3'))),
    dict(bottle='A', liquid_id='pbst_0_05pct_tween20', volume_m3=0.004), ['buffer_inlets'], '向供液瓶A–D加注或更换洗液（液体取洗液模板库；瓶内原液体被替换）', 'agent', 'robot_skill')
cap('waste.empty', obj({}), obj(dict(emptied_m3=number('m3'))), {}, ['rear_connectors'], '倒空废液瓶（液位传感器复位）', 'agent', 'robot_skill')
cap('plate.provide', PLATE, obj(dict(plate_id=TEXT, location=TEXT)), PLATE_EXAMPLE, ['plate_carrier'],
    '在实验台上准备一块微孔板：板型取板模板库（96孔），各孔的初始液体、可溶游离物、非特异吸附物、特异结合物与贴壁细胞（真值只对评测者可见）', 'agent', 'robot_skill')
cap('plate.load', obj(dict(plate_id=SID)), obj(dict(plate_id=TEXT)), dict(plate_id='elisa_plate'), ['plate_carrier'],
    '把微孔板放到载板上（A1在左后角；载板在初始位、洗头停放、无运行）', 'agent', 'robot_skill')
cap('plate.unload', obj(dict(plate_id=SID)), obj(dict(plate_id=TEXT)), dict(plate_id='elisa_plate'), ['plate_carrier'], '从载板取下微孔板放回实验台', 'agent', 'robot_skill')
cap('prime.run', obj(dict(buffer=enum(BOTTLES), volume_m3=number('m3', 5e-6, 0.5e-3), rate=integer(3, 11))), OPERATION, dict(buffer='A', volume_m3=300e-6, rate=9),
    ['manifold', 'prime_trough', 'buffer_inlets'], '灌注：切换到所选缓冲阀，洗头停在灌注槽上方分配给定体积（换液推荐300 mL，洗头灌注50 mL），更新管路内液体组成', 'agent')
cap('wash.run', obj(dict(protocol=WASH)), OPERATION, dict(protocol=WASH_EXAMPLE), ['manifold', 'plate_carrier', 'buffer_inlets', 'touch_screen'],
    '运行洗板步骤：载板移到洗头下，逐循环抽吸（行进速度、抽吸高度、延时、二次抽吸）与分配（体积、流速、分配高度、真空延迟），可选底洗、浸泡、摇动与最终抽吸；结束回到初始位', 'agent')
cap('maintenance.clean_tubes', obj({}), OPERATION, {}, ['manifold', 'manifold_tubes'], '用清洁针逐根疏通抽吸/分配管（人工/机器人维护，约10 min；解除堵针）', 'agent', 'robot_skill')
cap('log.read', obj({}), obj(dict(entries=array(obj(dict(record_id=TEXT, kind=TEXT, status=TEXT, cycles_done=INT, alarms=array(TEXT, 0, 12), finished_s=nullable(number('s')))), 0, 200))),
    {}, ['touch_screen'], '读清洗日志（程序、完成循环、报警）', 'agent', 'query')
cap('measurement.result', obj(dict(record_id=TEXT)), RECORD, dict(record_id='WS0001'), ['touch_screen'], '读取一条运行记录（设置、完成循环、程序分配体积、报警、警告；实际分配/抽吸体积仪器不测）', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, 200))), {}, ['touch_screen'], '列出记录', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, 400), dropped=INT)), dict(since_s=0.0), ['frame_base'], '读事件日志', 'agent', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['frame_base'], '读守恒账目（电能、分配/抽吸/灌注/废液/溢流体积、行程、循环数）', 'evaluator', 'query')
cap('plate.inspect', obj(dict(plate_id=SID)), obj(dict(plate_id=TEXT, volume_m3=array(number('m3'), 96, 96), unbound_mol=array(number('mol'), 96, 96),
                                                    nonspecific_mol=array(number('mol'), 96, 96), specific_mol=array(number('mol'), 96, 96), cells=array(number('1'), 96, 96),
                                                    contamination_mol=array(number('mol'), 96, 96))),
    dict(plate_id='elisa_plate'), ['plate_carrier'], '评测者读板内真值：各孔液体体积、游离物、非特异/特异结合物、细胞数与交叉污染量', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(path=TEXT, sha256=TEXT, count=INT)), 2, 2))), {}, ['frame_base'], '模板库路径与sha256', 'agent', 'query')
cap('fault.inject', obj(dict(fault=enum(['vacuum_leak', 'buffer_line_air', 'dispense_tube_clog', 'aspirate_tube_clog', 'waste_sensor_stuck', 'power_loss']),
                             tube=nullable(integer(0, 95)), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))), dict(fault='dispense_tube_clog', tube=5, active=True),
    ['frame_base', 'manifold_tubes'], '评测者故障注入：真空泄漏、缓冲管进气、指定管堵塞（分配/抽吸）、废液传感器卡住、断电', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['frame_base'], '推进宿主时钟（空闲时间让管路干涸）', 'evaluator', 'simulation')
UNSUPPORTED = {
    'vacuum_filtration.run': '真空过滤（滤板底部抽滤）为选件（Vac配置、止动螺钉套件），本配置未装',
    'magnetic_bead.wash': '磁珠分离洗涤需磁板与止动螺钉套件，本配置未装',
    'verify.clog_test': 'Verify堵针检测只在“Q”型号上提供，本机不支持（堵针只能由流量传感器的总流量偏差间接提示）',
    'ultrasonic.autoclean': '超声自动清洗（AutoClean）为选件',
    'biostack.run': 'BioStack堆叠机联机未建模',
    'hardware.connect': '没有真实405 TS与LHC软件的硬件接口',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['frame_base'], _why, 'agent', 'device_command')

SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


slot('clock.time_s', number('s'), 's', 'derived', 'frame_base', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'frame_base', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'switch_power', '主电源')
slot('carrier.x_m', number('m'), 'm', 'derived', 'plate_carrier', '载板位置（0为初始装板位，−0.160 m为洗头下；场景载板关节镜像）',
     reference='资产坐标+X方向的平移，0=初始位（板中心x=0.078 m），−0.160 m=洗头下（板中心x=−0.082 m）')
slot('carrier.at_home', BOOL, '1', 'derived', 'plate_carrier', '载板在初始位（可装卸板）')
slot('manifold.z_m', number('m'), 'm', 'derived', 'manifold', '洗头位置（0为停放，管尖高于板位34 mm；场景洗头关节镜像）',
     reference='资产坐标+Z方向的平移，抽吸管尖高于载板板位 = 0.034 m + 该值；Z步长0.127 mm')
slot('manifold.tip_height_m', number('m'), 'm', 'derived', 'manifold', '抽吸管尖高于载板板位的高度', reference='载板板位（板底）为零点')
slot('buffer.selected', enum(BOTTLES), '1', 'setpoint', 'buffer_inlets', '当前缓冲阀')
for _b in BOTTLES:
    slot(f'buffer.{_b.lower()}.liquid', nullable(TEXT), '1', 'setpoint', 'buffer_inlets', f'供液瓶{_b}中的洗液（模板库ID）')
    slot(f'buffer.{_b.lower()}.volume_m3', number('m3'), 'm3', 'process_truth', 'buffer_inlets', f'供液瓶{_b}中的液体体积（宿主记账真值；仪器只有液体传感器）', 'evaluator', reference='液体实际体积（室温）')
slot('sensor.fluid_ok', BOOL, '1', 'sensor', 'buffer_inlets', '液体传感器：所选供液瓶有液')
slot('sensor.vacuum_ok', BOOL, '1', 'sensor', 'rear_connectors', '真空传感器：真空泵接好且无泄漏（抽吸时）')
slot('sensor.waste_full', BOOL, '1', 'sensor', 'rear_connectors', '废液瓶液位传感器：满')
slot('sensor.flow_ok', BOOL, '1', 'sensor', 'manifold', '流量传感器：上次分配总流量与设定相符（偏差<10 %）')
slot('program.phase', nullable(TEXT), '1', 'derived', 'touch_screen', '运行中的阶段（移动/抽吸/分配/浸泡/摇动/灌注/清洁）')
slot('operation.kind', nullable(TEXT), '1', 'derived', 'touch_screen', '进行中的操作（wash/prime/clean）')
slot('operation.progress', number('1'), '1', 'derived', 'touch_screen', '操作进度0–1')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'touch_screen', '进行中的操作属于另一个agent')
slot('display.cycles_remaining', INT, '1', 'derived', 'display_cycles_digits', '屏幕仿真数字：剩余循环数（无运行时0）')
slot('display.time_remaining_s', number('s'), 's', 'derived', 'display_time_digits', '屏幕仿真数字：剩余时间（无运行时0）')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'touch_screen', '最近完成的记录')
slot('plate.on_carrier', nullable(TEXT), '1', 'derived', 'plate_carrier', '载板上的微孔板')
slot('plate.bench_ids', array(TEXT, 0, 20), '1', 'derived', 'plate_carrier', '实验台上的板')
slot('truth.line_composition', FREEOBJ, '1', 'process_truth', 'buffer_inlets', '管路（死体积110 mL）内液体按体积分数的组成', 'evaluator')
slot('truth.waste_volume_m3', number('m3'), 'm3', 'process_truth', 'rear_connectors', '废液瓶中液体体积', 'evaluator', reference='液体实际体积（室温）')
slot('truth.residual_m3', nullable(array(number('m3'), 96, 96)), 'm3', 'process_truth', 'plate_carrier', '载板上板各孔的液体体积（A1..H12按列优先）', 'evaluator',
     reference='孔序：列1的A–H、列2的A–H……（index = 8 x 列 + 行）')
slot('truth.clogged_dispense', array(integer(0, 95), 0, 96), '1', 'process_truth', 'manifold_tubes', '堵塞（部分或全部）的分配管序号', 'evaluator')
slot('truth.clogged_aspirate', array(integer(0, 95), 0, 96), '1', 'process_truth', 'manifold_tubes', '堵塞的抽吸管序号', 'evaluator')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'frame_base', '注入的故障', 'evaluator')
slot('truth.idle_s', number('s'), 's', 'process_truth', 'manifold_tubes', '洗头自上次用水冲洗或运行以来的空闲时间', 'evaluator')
MAX_RECORDS, MAX_EVENTS, MAX_PLATES = 200, 400, 20


# ---------------------------------------------------------------- 1.1.0: the touch-screen software of the 405 TS (Getting Started Guide 1171009 Rev C,
# printed page numbers): the protocol store and editor (Define, p.32-33), protocol runs with Wash / Aspirate / Dispense / Prime / Soak / Delay / Remark /
# Loop steps (p.43-46, 52-54), Maintenance / QC / sample protocols (p.41-43), washer settings (Instrument > Options: manifold, magnet height offset,
# sensors, vacuum dissipate delay; p.23, 61, 69-70), AutoPrime (p.70-71, 79), the clock (p.23), Self-Check (p.25, 34) and STOP (p.79, 94).  Quick Prime
# and Quick Wash are prime.run / wash.run.  Each function is a device command (DEVICE_CAPS); the front panel (panel_ui.json) calls them.
ASSET_VERSION = '1.1.1'
CLOCK_EPOCH = datetime.datetime(2026, 1, 1, 8, 0, tzinfo=datetime.timezone.utc)   # instrument clock (GMT) at simulation time 0
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')
MAX_PROTOCOLS = 99                          # user protocols (assumption: the limit is not in the guide)
MAX_STEPS = 50                              # steps of one protocol (assumption)
MAX_SEGMENTS = 5000                         # steps of one run after the loops are unrolled (assumption)
NAME_PATTERN = '^[A-Za-z0-9_.-][A-Za-z0-9_. -]{0,31}$'   # protocol (file) names (assumption)
XY_STEP_M = 0.0457e-3                       # carrier X / Y offset per step (assumption; 45 steps put the tubes at the side of a 96-well well, p.53-54)
XY_RANGE = (-60, 60)                        # assumption
TUBE_OUTER_R_M = 0.6e-3                     # aspirate tube outer radius (assumption)
TROUGH_M3 = 90e-6                           # priming trough content with its drain closed (submerge; assumption: the AutoClean trough takes 93 mL, p.81)
TROUGH_Z = -PARK_TIP_M + 0.005              # manifold position in the priming trough (as prime.run)
DELAY_MAX_S = SUBMERGE_MAX_S = 86400.0      # assumption
LOOP_RANGE = (1, 999)                       # assumption
MAGNET_OFFSET_MM = (0.0, 10.0)              # assumption
VAC_DISSIPATE_S = (0.0, 60.0)               # 1 s per litre of waste container (p.23); range assumption
AUTOPRIME_INTERVAL_MIN = (1, 1440)          # idle interval up to 24 h, in minutes (p.71)
TIME_ZONE_H = (-12, 14)
PLATE_STEPS = ('wash', 'aspirate', 'dispense', 'soak')
PROTOCOL_KINDS = ['maintenance', 'qc', 'sample', 'user']
ACTION_STEPS = ('wash', 'aspirate', 'dispense', 'soak', 'prime', 'delay')
TEXT60 = dict(type='string', maxLength=60)
XY = integer(*XY_RANGE)
_ASP = dict(travel_rate=TRAVEL_E, aspirate_delay_s=number('s', 0.0, ASP_DELAY_MAX_S), aspirate_z_steps=integer(1, 250), aspirate_x_steps=XY, aspirate_y_steps=XY,
            secondary_aspirate=BOOL, secondary_z_steps=integer(1, 250), secondary_x_steps=XY, secondary_y_steps=XY)
_DISP = dict(buffer=enum(BOTTLES), dispense_volume_m3=number('m3', *VOLUME_RANGE_M3), dispense_rate=integer(1, 11), dispense_z_steps=integer(40, 250),
             dispense_x_steps=XY, dispense_y_steps=XY, vacuum_delay_m3=number('m3', *VAC_DELAY_M3), predispense=BOOL, predispense_rate=integer(3, 11),
             predispense_volume_m3=number('m3', 50e-9, 3000e-9))
_SHAKE = dict(shake=BOOL, shake_s=number('s', 1.0, SHAKE_SOAK_MAX_S), shake_intensity=enum(list(SHAKE_TAU)), soak=BOOL, soak_s=number('s', 1.0, SHAKE_SOAK_MAX_S),
              home_carrier=BOOL)
STEP_SCHEMAS = {
    'wash': obj(dict(type=enum(['wash']), cycles=integer(*CYCLES_RANGE), **_DISP, **_ASP, bottom_wash=BOOL, bottom_wash_rate=integer(1, 11),
                     bottom_wash_volume_m3=number('m3', 25e-9, 3000e-9), predispense_between_cycles=BOOL, **_SHAKE, final_aspirate=BOOL)),
    'aspirate': obj(dict(type=enum(['aspirate']), **_ASP)),
    'dispense': obj(dict(type=enum(['dispense']), **_DISP)),
    'prime': obj(dict(type=enum(['prime']), buffer=enum(BOTTLES), volume_m3=number('m3', 5e-6, 0.5e-3), rate=integer(1, 11), submerge=BOOL,
                      submerge_s=number('s', 1.0, SUBMERGE_MAX_S))),
    'soak': obj(dict(type=enum(['soak']), **_SHAKE)),
    'delay': obj(dict(type=enum(['delay']), duration_s=number('s', 0.0, DELAY_MAX_S), message=TEXT60, wait_for_user=BOOL)),
    'remark': obj(dict(type=enum(['remark']), text=TEXT60)),
    'loop': obj(dict(type=enum(['loop']), count=integer(*LOOP_RANGE))),
    'end_loop': obj(dict(type=enum(['end_loop']))),
}
STEP = {'anyOf': list(STEP_SCHEMAS.values())}
_WASH_DEF = dict(type='wash', cycles=3, buffer='A', dispense_volume_m3=300e-9, dispense_rate=7, dispense_z_steps=DISP_Z_DEFAULT, dispense_x_steps=0, dispense_y_steps=0,
                 vacuum_delay_m3=10e-9, predispense=False, predispense_rate=9, predispense_volume_m3=50e-9, travel_rate='3', aspirate_delay_s=0.0,
                 aspirate_z_steps=ASP_Z_DEFAULT, aspirate_x_steps=0, aspirate_y_steps=0, secondary_aspirate=False, secondary_z_steps=ASP_Z_DEFAULT,
                 secondary_x_steps=0, secondary_y_steps=0, bottom_wash=False, bottom_wash_rate=7, bottom_wash_volume_m3=250e-9, predispense_between_cycles=False,
                 shake=False, shake_s=5.0, shake_intensity='medium', soak=False, soak_s=30.0, home_carrier=False, final_aspirate=True)
STEP_DEFAULTS = {   # the defaults of the Wash Step Parameters Table (p.43-46); the other steps take the same values (assumption where the guide is silent)
    t: {k: _WASH_DEF[k] if k in _WASH_DEF else None for k in s['properties']} for t, s in STEP_SCHEMAS.items()}
STEP_DEFAULTS['aspirate']['type'], STEP_DEFAULTS['dispense']['type'], STEP_DEFAULTS['soak']['type'] = 'aspirate', 'dispense', 'soak'
STEP_DEFAULTS['soak']['soak'] = True
STEP_DEFAULTS['prime'].update(type='prime', buffer='A', volume_m3=300e-6, rate=9, submerge=False, submerge_s=300.0)
STEP_DEFAULTS['delay'].update(type='delay', duration_s=60.0, message='', wait_for_user=False)
STEP_DEFAULTS['remark'].update(type='remark', text='')
STEP_DEFAULTS['loop'].update(type='loop', count=2)
STEP_DEFAULTS['end_loop'].update(type='end_loop')


def _st(kind, **kw):
    return dict(STEP_DEFAULTS[kind], **kw)


def _pr(buffer, ml, soak_s=None):
    return _st('prime', buffer=buffer, volume_m3=ml * 1e-6, submerge=soak_s is not None, submerge_s=soak_s or 300.0)


def _ask(text):
    return _st('delay', duration_s=0.0, message=text, wait_for_user=True)


PREDEFINED = {   # name: (kind, steps): the onboard protocols of p.41-43 that fit this configuration (96-tube manifold, buffer switching, no vacuum filtration /
    # magnets); contents beyond the guide's description (volumes, rates, the decontamination passes) are assumptions (PREDEFINED_NOTE)
    'W-DAY_RINSE': ('maintenance', [_pr('A', 500)]),
    'W-OVERNIGHT_LOOP': ('maintenance', [_st('loop', count=999), _pr('A', 50, soak_s=4 * 3600.0), _st('end_loop')]),
    'W-RINSE_AND_SOAK': ('maintenance', [_pr('A', 500, soak_s=300.0)]),
    'W-DECONTAMINATE': ('maintenance', [_ask('Connect the disinfectant to Buffers A-D, then press Continue.'), _pr('A', 100), _pr('B', 100), _pr('C', 100),
                                        _pr('D', 100, soak_s=1200.0), _ask('Connect a bottle of water to Buffers A-D, then press Continue.'), _pr('A', 100),
                                        _pr('B', 100), _pr('C', 100), _pr('D', 100, soak_s=1200.0)]),
    'W-LONG_SHUTDOWN': ('maintenance', [_ask('Connect the disinfectant to Buffer A, then press Continue.'), _pr('A', 300),
                                        _ask('Connect a bottle of water to Buffer B, then press Continue.'), _pr('B', 300),
                                        _ask('Remove the bottle from Buffer C (air), then press Continue.'), _pr('C', 300)]),
    'PRIME_ALL_BUFFRS': ('maintenance', [_pr('D', 300), _pr('C', 300), _pr('B', 300), _pr('A', 300)]),
    'QC_96_DISP_TEST': ('qc', [_st('dispense', dispense_rate=6)]),
    'QC_96_EVAC_TEST': ('qc', [_st('wash')]),
    'W-CELLWASH_96': ('sample', [_st('wash', dispense_rate=1, dispense_volume_m3=200e-9, vacuum_delay_m3=200e-9, travel_rate='6CW', aspirate_z_steps=50)]),
    'W-Corning_FLAT': ('sample', [_st('wash')]),
}
PREDEFINED_NOTE = ('板载程序按说明书p.41-43的描述建模：W-DAY_RINSE（A瓶500 mL）、W-RINSE_AND_SOAK（再浸泡5 min）、W-OVERNIGHT_LOOP（每4 h灌注并浸泡，近似无限循环999次）、'
                   'W-DECONTAMINATE（提示后A–D各灌消毒液，浸泡20 min；再提示接水，A–D各冲洗并浸泡20 min）、W-LONG_SHUTDOWN（消毒液A、水B、空气C，带提示）、'
                   'PRIME_ALL_BUFFRS（从D到A依次灌注）、QC_96_DISP_TEST（300 µL/孔、流速6的分配）、QC_96_EVAC_TEST（默认三循环清洗）、W-CELLWASH_96（6CW、'
                   '流速1、抽吸高度50步）、W-Corning_FLAT（默认清洗）；说明书未写的体积、流速与每段次数为假设；192管、384孔、真空过滤与磁珠程序不在本配置列表中。')
WASHER_DEFAULT = dict(manifold='96', magnet_in_use=False, magnet_offset_mm=0.0, sensor_fluid=True, sensor_flow=True, sensor_waste=True, sensor_vac_filter=False,
                      vacuum_dissipate_s=0.0)
AUTOPRIME_DEFAULT = dict(enabled=False, interval_min=60, submerge_min=0, rate=9, volume_m3=50e-6, buffer='A')
WASHER = obj(dict(manifold=enum(['96', '192']), magnet_in_use=BOOL, magnet_offset_mm=number('mm', *MAGNET_OFFSET_MM), sensor_fluid=BOOL, sensor_flow=BOOL,
                  sensor_waste=BOOL, sensor_vac_filter=BOOL, vacuum_dissipate_s=number('s', *VAC_DISSIPATE_S)))
AUTOPRIME = obj(dict(enabled=BOOL, interval_min=integer(*AUTOPRIME_INTERVAL_MIN), submerge_min=integer(0, 1440), rate=integer(1, 11),
                     volume_m3=number('m3', 5e-6, 0.5e-3), buffer=enum(BOTTLES)))
EDITOR = obj(dict(name=TEXT, plate_type=TEXT, steps=array(FREEOBJ, 0, MAX_STEPS), source=nullable(TEXT), clipboard=INT))
RUN_STATE = obj(dict(operation_id=TEXT, name=TEXT, test=BOOL, status=enum(['running', 'waiting', 'prompt', 'completed', 'failed', 'cancelled']), step=nullable(INT),
                     step_type=nullable(TEXT), segment=INT, segments=INT, prompt=nullable(TEXT), wait_until_s=nullable(number('s')), error_code=nullable(TEXT),
                     records=array(TEXT, 0, MAX_RECORDS), started_s=number('s'), finished_s=nullable(number('s'))))
PROTOCOL_ROW = obj(dict(name=TEXT, kind=enum(PROTOCOL_KINDS), locked=BOOL, plate_type=TEXT, steps=array(FREEOBJ, 0, MAX_STEPS), modified_s=nullable(number('s')),
                        last_run_s=nullable(number('s'))))
DEVICE_CAPS = {}


def dcap(k, s, o, e, meaning, kind='device_command', c=('touch_screen',)):
    DEVICE_CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c), function_zh=meaning, visibility='agent', kind=kind)


dcap('protocol.edit_open', obj(dict(name=nullable(TEXT))), obj(dict(editor=EDITOR)), dict(name=None),
     'Define：Create（name为null，新程序）或选中程序后Edit：把程序装入编辑缓冲区（锁定程序也可打开，另存为新名即复制，p.32）')
dcap('protocol.edit_info', obj(dict(name=TEXT, plate_type=TEXT)), obj(dict(editor=EDITOR)), dict(name='MY_WASH', plate_type='costar_3590'),
     '编辑中的程序名（键盘）与板型（Info / Plate Type，取板模板库中适合96管洗头的板）')
dcap('protocol.edit_step', obj(dict(action=enum(['insert', 'replace', 'cut', 'paste']), index=integer(1, MAX_STEPS + 1), step=nullable(STEP))),
     obj(dict(editor=EDITOR)), dict(action='insert', index=1, step=STEP_DEFAULTS['wash']),
     '编辑步骤：Add（在选中步骤或<end of steps>之前插入；Loop同时插入End Loop）、Edit（替换同类型步骤的参数）、剪切与粘贴步骤（p.32）')
dcap('protocol.edit_close', obj({}), obj({}), {}, '离开编辑屏（未保存的修改丢弃）')
dcap('protocol.save', obj({}), obj(dict(name=TEXT, steps=INT)), {}, 'Save：通过Validate检查后把编辑缓冲区存为用户程序（出现在Home列表；锁定程序不能覆盖，p.33）')
dcap('protocol.validate', obj({}), obj(dict(valid=BOOL, steps=INT)), {}, 'Validate：检查步骤顺序与参数（循环成对、非空、细胞洗涤选件），不通过时以错误代码拒绝（p.33）', 'query')
dcap('protocol.delete', obj(dict(name=TEXT)), obj(dict(name=TEXT)), dict(name='MY_WASH'), 'Define > Delete：永久删除用户程序（锁定的板载程序不能删，p.33）')
dcap('protocol.run', obj(dict(name=TEXT)), OPERATION, dict(name='W-DAY_RINSE'),
     'Home/Maintenance：选中程序后Start：按步骤顺序运行（循环展开；Delay步骤计时或等待Continue；每个洗板/灌注步骤写一条记录）', c=('touch_screen', 'manifold', 'plate_carrier'))
dcap('protocol.test_run', obj({}), OPERATION, {}, 'Test Run：不保存，直接运行编辑缓冲区中的程序（p.33）', c=('touch_screen', 'manifold', 'plate_carrier'))
dcap('protocol.resume', obj({}), obj(dict(step=INT)), {}, 'Continue：确认Delay步骤的提示后继续运行（p.83）')
dcap('run.stop', obj({}), obj(dict(stopped=TEXT)), {}, 'STOP：停止正在进行的运行（程序、Quick Prime/Wash、AutoPrime）；洗头停放、载板回初始位（p.79、94）')
dcap('washer.configure', WASHER, obj(dict(settings=WASHER)), dict(WASHER_DEFAULT),
     'Instrument > Options：洗头选择（与实际96管洗头不符时自检报错）、磁铁高度偏移（启用时加到所有抽吸/分配高度）、传感器启用（液体、流量、废液、真空过滤）与Vacuum Dissipate延时（p.23、61、69-70）')
dcap('autoprime.configure', AUTOPRIME, obj(dict(autoprime=AUTOPRIME)), dict(AUTOPRIME_DEFAULT),
     'Instrument > AutoPrime：空闲间隔（≤24 h）、浸没时长、流速、体积与缓冲阀；在Home屏空闲达到间隔时自动灌注（任何操作重新计时，p.70-71、79）')
dcap('clock.set', obj(dict(time_zone_h=integer(*TIME_ZONE_H), date=dict(type='string', pattern=r'^\d{4}-\d{2}-\d{2}$'), time=dict(type='string', pattern=r'^\d{2}:\d{2}$'))),
     obj(dict(local_time=TEXT)), dict(time_zone_h=1, date='2026-10-19', time='14:05'),
     'Instrument > Next > Other > Set Time and Date：时区、日期与24小时制时间（程序的Last run / Last modified按此显示，p.23）')
dcap('instrument.self_check', obj({}), obj(dict(passed=BOOL, error_code=nullable(TEXT))), {},
     'Instrument > Self-Check：自检（载板与洗头回位、排空灌注槽、洗头设置与实际洗头比对），结果显示为通过或错误代码（p.25、34）', c=('touch_screen', 'manifold', 'prime_trough'))
slot('protocols.list', array(PROTOCOL_ROW, 0, MAX_PROTOCOLS + len(PREDEFINED)), '1', 'setpoint', 'touch_screen',
     '仪器存储的程序：板载维护/QC/示例程序（锁定）与用户程序（名称、板型、步骤、最后修改与最后运行时间）')
slot('protocol.editor', nullable(EDITOR), '1', 'setpoint', 'touch_screen', 'Define编辑屏中的程序（名称、板型、步骤、剪贴板步数）；关机或离开编辑屏后为null')
slot('protocol.run', nullable(RUN_STATE), '1', 'derived', 'touch_screen', '当前或最近一次程序运行：状态、第几步、提示、等待截止时间、错误代码与写下的记录')
slot('washer.settings', WASHER, '1', 'setpoint', 'touch_screen', 'Instrument > Options中的洗板机设置')
slot('autoprime.settings', AUTOPRIME, '1', 'setpoint', 'touch_screen', 'AutoPrime设置（默认关闭）')
slot('clock.settings', obj(dict(time_zone_h=integer(*TIME_ZONE_H), offset_s=number('s'))), '1', 'setpoint', 'touch_screen',
     '仪器时钟：时区与相对仿真时钟的偏移（本地时间 = 2026-01-01 08:00 GMT + 仿真时间 + 偏移 + 时区）')
slot('selfcheck.last', nullable(obj(dict(passed=BOOL, error_code=nullable(TEXT), time_s=number('s')))), '1', 'derived', 'touch_screen', '最近一次自检的结果')
slot('manifold.submerged', BOOL, '1', 'derived', 'manifold', '洗头管尖浸没在灌注槽液体中（浸没时管尖不会干涸堵塞）')
slot('truth.trough_m3', number('m3'), 'm3', 'process_truth', 'prime_trough', '灌注槽中留存的液体（浸没时关闭排液；下次灌注或自检时排到废液）', 'evaluator',
     reference='液体实际体积（室温）')


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as (scripts.)asset_runtime.biotek_405ts (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


_panel_ui = _panel_engine()
PANEL_PATH = ASSET_DIR / 'panel_ui.json'
_PANEL_BUILD = os.environ.get('BIOTEK405_PANEL_BUILD') == '1' or not PANEL_PATH.is_file()   # the spec builder imports the runtime without the spec
PANEL_SPEC = None if _PANEL_BUILD else _panel_ui.load_spec(PANEL_PATH, capabilities=set(CAPS) | set(DEVICE_CAPS))
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}   # panel.press / touch / capture / frame (kept out of CAPS)
ALL_CAPS = dict(CAPS, **DEVICE_CAPS, **PANEL_CAPS)                                 # (CAPS stays the 1.0.0 command set)
_PANELS = weakref.WeakKeyDictionary()                                              # instance -> PanelUI (outside the instance dict)



class _Sum:
    def __init__(self):
        self.s = 0.0
        self.c = 0.0

    def add(self, x):
        t = self.s + x
        if abs(self.s) >= abs(x):
            self.c += (self.s - t) + x
        else:
            self.c += (x - t) + self.s
        self.s = t

    def value(self):
        return self.s + self.c


class _TouchSoftware:
    """1.1.0 device layer of Biotek405tsRuntime: what the 405 TS touch-screen software stores and does besides the 1.0.0 commands (block comment above
    DEVICE_CAPS); also the host side of the front panel (panel_read / panel_invoke / panel_time, the panel.* commands)."""

    def _init_device(self):
        self.protocols = {n: dict(name=n, kind=k, locked=True, plate_type='costar_3590', steps=copy.deepcopy(s), modified_s=None, last_run_s=None)
                          for n, (k, s) in PREDEFINED.items()}
        self.editor = None
        self.proto = None
        self.washer = dict(WASHER_DEFAULT)
        self.autoprime = dict(AUTOPRIME_DEFAULT)
        self.clock = dict(time_zone_h=0, offset_s=0.0)
        self.selfcheck = None
        self.trough = 0.0
        self.last_activity_s = 0.0          # AutoPrime counts idle time from the last touch / command / run (p.79)
        self.last_run = None                # what the run screen shows after a run that is not a protocol
        self.power_epoch = 0
        self.panel_capture_dir = None
        self._caller = None                 # principal of the session call in progress (a panel gesture acts as that caller)

    # ---- values
    def _device_values(self):
        P, e = self.proto, self.editor
        return {
            'protocols.list': [dict(name=p['name'], kind=p['kind'], locked=p['locked'], plate_type=p['plate_type'], steps=copy.deepcopy(p['steps']),
                                    modified_s=p['modified_s'], last_run_s=p['last_run_s']) for p in self.protocols.values()],
            'protocol.editor': None if e is None else dict(name=e['name'], plate_type=e['plate_type'], steps=copy.deepcopy(e['steps']), source=e['source'],
                                                           clipboard=len(e['clipboard'])),
            'protocol.run': None if P is None else dict(operation_id=P['op'], name=P['name'], test=P['test'], status=P['status'], step=P['step'],
                                                        step_type=P['step_type'], segment=P['i'], segments=len(P['segments']), prompt=P['prompt'],
                                                        wait_until_s=P['wait_until'], error_code=P['code'], records=list(P['records']),
                                                        started_s=P['started_s'], finished_s=P['finished_s']),
            'washer.settings': dict(self.washer), 'autoprime.settings': dict(self.autoprime), 'clock.settings': dict(self.clock),
            'selfcheck.last': None if self.selfcheck is None else dict(self.selfcheck), 'manifold.submerged': self._submerged(), 'truth.trough_m3': self.trough}

    def _submerged(self):
        return self.trough > 1e-12 and self.manifold <= TROUGH_Z + 1e-9

    def _z_offset(self):
        return self.washer['magnet_offset_mm'] * 1e-3 if self.washer['magnet_in_use'] else 0.0

    def _proto_active(self):
        return self.proto is not None and self.proto['status'] in ('running', 'waiting', 'prompt')

    def _need_device(self):
        self._need_plant()
        self._need_power()

    def _need_editor(self):
        if self.editor is None:
            raise ActionRejected('EDITOR_CLOSED')

    @staticmethod
    def _check_plate_type(plate_type):
        e = plate_lib()['plates'].get(plate_type)
        if e is None:
            raise ActionRejected('UNKNOWN_PLATE_TYPE')
        if not e['compatible_with_96_tube_single_manifold']:
            raise ActionRejected('PLATE_INCOMPATIBLE')

    # ---- dispatch (device commands and panel gestures; the 1.0.0 commands keep their own invoke)
    def _invoke_any(self, capability_id, **a):
        if capability_id in CAPS:
            return self.invoke(capability_id, **a)
        if capability_id in PANEL_CAPS:
            validate(PANEL_CAPS[capability_id]['input_schema'], a)
            return self._panel_command(capability_id, a)
        d = DEVICE_CAPS.get(capability_id)
        if d is None:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(d['input_schema'], a)
        a = _ints(d['input_schema'], a)
        out = getattr(self, '_dev_' + capability_id.replace('.', '_'))(**a)
        if d['kind'] != 'query':
            self.revision += 1
            self.last_activity_s = self.t
        return out

    # ---- protocol store and editor (Define, p.32-33)
    def _dev_protocol_edit_open(self, name):
        self._need_device()
        if name is None:
            self.editor = dict(name='', plate_type='costar_3590', steps=[], source=None, clipboard=[])
        else:
            p = self.protocols.get(name)
            if p is None:
                raise ActionRejected('UNKNOWN_PROTOCOL')
            self.editor = dict(name=name, plate_type=p['plate_type'], steps=copy.deepcopy(p['steps']), source=name, clipboard=[])
        return dict(editor=self._device_values()['protocol.editor'])

    def _dev_protocol_edit_info(self, name, plate_type):
        self._need_device()
        self._need_editor()
        if name and not re.fullmatch(NAME_PATTERN, name):
            raise ActionRejected('INVALID_NAME')
        self._check_plate_type(plate_type)
        self.editor.update(name=name, plate_type=plate_type)
        return dict(editor=self._device_values()['protocol.editor'])

    @staticmethod
    def _block(steps, i):
        """Indices [lo, hi) of the step at i, a whole Loop ... End Loop block when i is either end of a loop."""
        t = steps[i]['type']
        if t not in ('loop', 'end_loop'):
            return i, i + 1
        d, j = 0, i
        step = 1 if t == 'loop' else -1
        while 0 <= j < len(steps):
            d += {'loop': 1, 'end_loop': -1}.get(steps[j]['type'], 0) * step
            if d == 0:
                break
            j += step
        return (i, j + 1) if t == 'loop' else (j, i + 1)

    def _dev_protocol_edit_step(self, action, index, step):
        self._need_device()
        self._need_editor()
        steps = self.editor['steps']
        n = len(steps)
        if action in ('insert', 'paste'):
            if not 1 <= index <= n + 1:
                raise ActionRejected('INVALID_ARGUMENT')
            if action == 'insert':
                if step is None or step['type'] == 'end_loop':
                    raise ActionRejected('INVALID_ARGUMENT')
                new = [step] + ([dict(type='end_loop')] if step['type'] == 'loop' else [])
            else:
                new = self.editor['clipboard']
                if not new:
                    raise ActionRejected('CLIPBOARD_EMPTY')
            if n + len(new) > MAX_STEPS:
                raise ActionRejected('TOO_MANY_STEPS')
            steps[index - 1:index - 1] = copy.deepcopy(new)
        else:
            if not 1 <= index <= n:
                raise ActionRejected('INVALID_ARGUMENT')
            if action == 'replace':
                if step is None or step['type'] != steps[index - 1]['type'] or step['type'] == 'end_loop':
                    raise ActionRejected('INVALID_ARGUMENT')
                steps[index - 1] = copy.deepcopy(step)
            else:
                lo, hi = self._block(steps, index - 1)
                self.editor['clipboard'] = copy.deepcopy(steps[lo:hi])
                del steps[lo:hi]
        return dict(editor=self._device_values()['protocol.editor'])

    def _dev_protocol_edit_close(self):
        self._need_device()
        self._need_editor()
        self.editor = None
        return {}

    def _segments(self, steps, limit=MAX_SEGMENTS + 1):
        """The run order of a (balanced) step list: loops unrolled, End Loop markers dropped; stops after limit entries."""
        out = []

        def walk(lo, hi):
            i = lo
            while i < hi and len(out) < limit:
                s = steps[i]
                if s['type'] == 'loop':
                    _, j = self._block(steps, i)
                    for _ in range(s['count']):
                        walk(i + 1, j - 1)
                        if len(out) >= limit:
                            break
                    i = j
                else:
                    if s['type'] != 'end_loop':
                        out.append(dict(index=i + 1, step=s))
                    i += 1
        walk(0, len(steps))
        return out

    def _needs_cell_wash(self, s):
        t = s['type']
        return ((t in ('wash', 'dispense') and s['dispense_rate'] <= 2) or (t in ('wash', 'aspirate') and s['travel_rate'].endswith('CW'))
                or (t == 'wash' and s['bottom_wash'] and s['bottom_wash_rate'] <= 2) or (t == 'prime' and s['rate'] <= 2))

    def _validate_steps(self, steps):
        """The Validate check (p.33): None, or the code of the first problem."""
        if not any(s['type'] in ACTION_STEPS for s in steps):
            return 'PROTOCOL_EMPTY'
        open_ = []
        for i, s in enumerate(steps):
            t = s['type']
            if t == 'loop':
                open_.append(i)
            elif t == 'end_loop':
                if not open_:
                    return 'LOOP_NOT_OPEN'
                if not any(x['type'] in ACTION_STEPS for x in steps[open_.pop() + 1:i]):
                    return 'LOOP_EMPTY'
            elif t == 'soak' and not (s['shake'] or s['soak']):
                return 'SOAK_STEP_EMPTY'
            if t in ('wash', 'dispense'):
                if abs(s['dispense_volume_m3'] / VOLUME_STEP_M3 - round(s['dispense_volume_m3'] / VOLUME_STEP_M3)) > 1e-6:
                    return 'VOLUME_NOT_IN_1UL_STEPS'
                if s['vacuum_delay_m3'] > s['dispense_volume_m3'] + 1e-15:
                    return 'VACUUM_DELAY_EXCEEDS_VOLUME'
            if t in ACTION_STEPS and t != 'delay' and self._needs_cell_wash(s) and not self.plant['cell_wash_installed']:
                return 'CELL_WASH_OPTION_ABSENT'
        if open_:
            return 'LOOP_NOT_CLOSED'
        if len(self._segments(steps)) > MAX_SEGMENTS:
            return 'PROTOCOL_TOO_LONG'
        return None

    def _dev_protocol_validate(self):
        self._need_device()
        self._need_editor()
        code = self._validate_steps(self.editor['steps'])
        if code:
            raise ActionRejected(code)
        return dict(valid=True, steps=len(self._segments(self.editor['steps'])))

    def _dev_protocol_save(self):
        self._need_device()
        self._need_editor()
        e = self.editor
        if not e['name']:
            raise ActionRejected('NAME_REQUIRED')
        old = self.protocols.get(e['name'])
        if old is not None and old['locked']:
            raise ActionRejected('PROTOCOL_LOCKED')
        if old is None and sum(p['kind'] == 'user' for p in self.protocols.values()) >= MAX_PROTOCOLS:
            raise ActionRejected('PROTOCOL_STORE_FULL')
        code = self._validate_steps(e['steps'])
        if code:
            raise ActionRejected(code)
        self.protocols[e['name']] = dict(name=e['name'], kind='user', locked=False, plate_type=e['plate_type'], steps=copy.deepcopy(e['steps']), modified_s=self.t,
                                         last_run_s=old['last_run_s'] if old else None)
        e['source'] = e['name']
        self._event('program', 'PROTOCOL_SAVED', f'程序{e["name"]}已保存（{len(e["steps"])}步）')
        return dict(name=e['name'], steps=len(e['steps']))

    def _dev_protocol_delete(self, name):
        self._need_device()
        p = self.protocols.get(name)
        if p is None:
            raise ActionRejected('UNKNOWN_PROTOCOL')
        if p['locked']:
            raise ActionRejected('PROTOCOL_LOCKED')
        if self._proto_active() and self.proto['name'] == name and not self.proto['test']:
            raise ActionRejected('OPERATION_RUNNING')
        del self.protocols[name]
        self._event('program', 'PROTOCOL_DELETED', f'程序{name}已删除')
        return dict(name=name)

    # ---- protocol runs
    def _seg_check(self, s):
        """What the washer checks before a step starts (sensors as enabled; plate on the carrier for plate steps)."""
        t = s['type']
        if t in PLATE_STEPS and self.on_carrier is None:
            raise ActionRejected('NO_PLATE')
        if t in ('wash', 'dispense', 'prime'):
            self._check_supply(s['buffer'])
        elif t == 'aspirate' and self.washer['sensor_waste'] and self._waste_full():
            raise ActionRejected('WASTE_FULL')
        if t in ('wash', 'aspirate', 'dispense', 'prime') and not self._vacuum_ok():
            raise ActionRejected('VACUUM_LOW')
        if t in ('wash', 'aspirate', 'dispense', 'prime', 'soak') and len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')

    def _proto_launch(self, name, plate_type, steps, test):
        code = self._validate_steps(steps)
        if code:
            raise ActionRejected(code)
        segs = self._segments(steps)
        if self.on_carrier is None and any(g['step']['type'] in PLATE_STEPS for g in segs):
            raise ActionRejected('NO_PLATE')
        first = next((g for g in segs if g['step']['type'] != 'remark'), None)
        if first is not None and first['step']['type'] != 'delay':
            self._seg_check(first['step'])
        self.seq['op'] += 1
        oid = f'op_{self.seq["op"]:04d}'
        self.proto = dict(op=oid, name=name, test=test, plate_type=plate_type, segments=segs, i=0, status='running', code=None, records=[], prompt=None,
                          wait_until=None, started_s=self.t, finished_s=None, step=None, step_type=None)
        self.operations[oid] = dict(phase='running', kind='protocol', record_id=None, op=oid)
        self.view = 'protocol'
        if not test:
            self.protocols[name]['last_run_s'] = self.t
        self._event('program', 'PROTOCOL_START', f'程序{name}开始（{len(segs)}步）')
        self._proto_next()
        return dict(operation_id=oid)

    def _dev_protocol_run(self, name):
        self._need_device()
        self._need_idle()
        p = self.protocols.get(name)
        if p is None:
            raise ActionRejected('UNKNOWN_PROTOCOL')
        return self._proto_launch(name, p['plate_type'], copy.deepcopy(p['steps']), test=False)

    def _dev_protocol_test_run(self):
        self._need_device()
        self._need_idle()
        self._need_editor()
        e = self.editor
        return self._proto_launch(e['name'] or '<new protocol>', e['plate_type'], copy.deepcopy(e['steps']), test=True)

    def _proto_next(self):
        """Start the next step of the protocol run (after the previous one ended normally)."""
        P = self.proto
        while P['i'] < len(P['segments']):
            seg = P['segments'][P['i']]
            P['i'] += 1
            s = seg['step']
            P.update(step=seg['index'], step_type=s['type'], prompt=None, wait_until=None)
            if s['type'] == 'remark':
                continue
            if s['type'] == 'delay':
                P['prompt'] = s['message'] or None
                if s['wait_for_user']:
                    P['status'] = 'prompt'
                    return
                if s['duration_s'] > 0:
                    P.update(status='waiting', wait_until=self.t + s['duration_s'])
                    return
                continue
            try:
                self._seg_check(s)
            except ActionRejected as exc:
                self._proto_end('failed', str(exc))
                return
            P['status'] = 'running'
            self._seg_start(seg)
            return
        self._proto_end('completed', None)

    def _seg_start(self, seg):
        P, s = self.proto, seg['step']
        settings = dict(copy.deepcopy(s), protocol=P['name'], step_number=seg['index'])
        if s['type'] == 'prime':
            phases = self._plan_prime(s['buffer'], s['volume_m3'], s['rate'], s['submerge_s'] if s['submerge'] else 0.0)
            rid = self._new_record('prime', 'PR', 'pr', settings, [x for x in [self.bottles[s['buffer']]['liquid']] if x])
            out = self._start('prime', phases, rid, buffer=s['buffer'], proto=P['op'])
        else:
            w = self._wash_like(s)
            phases = self._plan_wash(w, s['type'])
            liquids = [x for x in [self.bottles[w['buffer']]['liquid']] if x and s['type'] in ('wash', 'dispense')] + list(self.line)
            rid = self._new_record('wash', 'WS', 'ws', settings, liquids)
            if self.plates[self.on_carrier]['type'] != P['plate_type']:
                self.records[rid]['warnings'].append('plate_type_differs_from_protocol')
            out = self._start('wash', phases, rid, buffer=w['buffer'], protocol=w, proto=P['op'])
        P['records'].append(rid)
        owner = self._owners.get(P['op'])
        if owner is not None:
            self._owners[out['operation_id']] = owner
            self._owners[rid] = owner

    def _adopt(self, oid):
        """The owner of a protocol run also owns the step it started at once (sessions register the owner after the call)."""
        P = self.proto
        if P is None or P['op'] != oid or oid not in self._owners:
            return
        for rid in P['records']:
            self._owners[rid] = self._owners[oid]
        if self.op is not None and self.op.get('proto') == oid:
            self._owners[self.op['op']] = self._owners[oid]

    def _proto_end(self, status, code):
        P = self.proto
        P.update(status=status, code=code, finished_s=self.t, prompt=None, wait_until=None)
        self.operations[P['op']]['phase'] = status
        self.last_activity_s = self.t
        self._event('program', 'PROTOCOL_' + status.upper(), f'程序{P["name"]}{ {"completed": "完成", "failed": "失败", "cancelled": "已停止"}[status]}'
                    + (f'（{code}）' if code else ''))

    def _after_op(self, o, outcome, code=None):
        """A run ended: a protocol goes on with its next step (or ends with this one); other runs leave their result for the run screen."""
        self.last_activity_s = self.t
        P = self.proto
        if P is not None and o.get('proto') == P['op'] and P['status'] in ('running', 'waiting', 'prompt'):
            if outcome == 'completed':
                self._proto_next()
            else:
                self._proto_end(outcome, code)
            return
        self.last_run = dict(kind='autoprime' if o.get('autoprime') else o['kind'], status=outcome, code=code, finished_s=self.t, record_id=o['record_id'],
                             op=o['op'])

    def _dev_protocol_resume(self):
        self._need_plant()
        P = self.proto
        if P is None or P['status'] != 'prompt':
            raise ActionRejected('NOT_WAITING')
        nxt = next((g for g in P['segments'][P['i']:] if g['step']['type'] != 'remark'), None)
        if nxt is not None and nxt['step']['type'] != 'delay':
            self._need_power()
            self._seg_check(nxt['step'])                                      # e.g. the bottle the prompt asked for is still empty: stay at the prompt
        P['status'] = 'running'
        step = P['step']
        self._proto_next()
        return dict(step=step)

    def _dev_run_stop(self):
        self._need_plant()
        if self.op is not None:
            kind = 'protocol' if self._proto_active() else ('autoprime' if self.op.get('autoprime') else self.op['kind'])
            self._cancel_op(self.operations[self.op['op']])
            return dict(stopped=kind)
        if self._proto_active():
            self._proto_end('cancelled', None)
            return dict(stopped='protocol')
        raise ActionRejected('NOT_RUNNING')

    # ---- run plans (the 1.0.0 wash.run plan with the 1.1.0 step options; unset options keep the 1.0.0 plan)
    def _wash_like(self, s):
        """A protocol step as the wash dict the planner and the well physics use (op['protocol'])."""
        d = dict(_WASH_DEF, **{k: v for k, v in s.items() if k != 'type'})
        return dict(buffer=d['buffer'], cycles=d['cycles'] if s['type'] == 'wash' else 0, dispense_volume_m3=d['dispense_volume_m3'], dispense_rate=d['dispense_rate'],
                    dispense_z_steps=d['dispense_z_steps'], aspirate_z_steps=d['aspirate_z_steps'], travel_rate=d['travel_rate'],
                    aspirate_delay_s=d['aspirate_delay_s'], secondary_aspirate_z_steps=d['secondary_z_steps'] if d['secondary_aspirate'] else None,
                    vacuum_delay_m3=d['vacuum_delay_m3'], soak_s=d['soak_s'] if d['soak'] else 0.0, shake_s=d['shake_s'] if d['shake'] else 0.0,
                    shake_intensity=d['shake_intensity'], bottom_wash=d['bottom_wash'], final_aspirate=d['final_aspirate'] if s['type'] == 'wash' else False,
                    bottom_wash_rate=d['bottom_wash_rate'], bottom_wash_volume_m3=d['bottom_wash_volume_m3'], predispense=d['predispense'],
                    predispense_rate=d['predispense_rate'], predispense_volume_m3=d['predispense_volume_m3'],
                    predispense_between_cycles=d['predispense_between_cycles'], home_carrier=d['home_carrier'],
                    **{f'{p}_{a}_steps': d[f'{p}_{a}_steps'] for p in ('aspirate', 'secondary', 'dispense') for a in 'xy'})

    def _plan_prime(self, buffer, volume_m3, rate, submerge_s=0.0):
        phases = [dict(name='valve', duration=VALVE_S, z=0.0, x=0.0), dict(name='prime', duration=volume_m3 / (96 * FLOW_RATE_M3_S[rate]), z=TROUGH_Z, x=0.0,
                                                                         volume=volume_m3, buffer=buffer)]
        if submerge_s > 0:                                                    # the trough fills (drain closed) and the tubes stay in it
            phases[-1]['fill_trough'] = True
            phases.append(dict(name='submerge', duration=submerge_s, z=TROUGH_Z, x=0.0))
        return phases + [dict(name='park', duration=0.5, z=0.0, x=0.0)]

    def _plan_wash(self, p, kind='wash'):
        """Phases of a wash (wash.run: the 1.0.0 plan) or of an Aspirate / Dispense / Soak step.  The 1.1.0 options - pre-dispense (also between cycles),
        bottom-wash rate and volume, X / Y positions, carrier home for shake / soak, magnet height offset, vacuum dissipate delay - change nothing when unset."""
        speed = TRAVEL[p['travel_rate']][0]
        zo = self._z_offset()
        z_disp, z_asp = p['dispense_z_steps'] * Z_STEP_M + zo, p['aspirate_z_steps'] * Z_STEP_M + zo
        z_sec = None if p['secondary_aspirate_z_steps'] is None else p['secondary_aspirate_z_steps'] * Z_STEP_M + zo
        Q = FLOW_RATE_M3_S[p['dispense_rate']]
        t_disp = p['dispense_volume_m3'] / Q
        down = max(0.0, z_disp - z_asp) / speed

        def xy(part):
            return math.hypot(p.get(f'{part}_x_steps', 0), p.get(f'{part}_y_steps', 0)) * XY_STEP_M

        aspirate = [dict(name='aspirate', duration=down + p['aspirate_delay_s'] + 0.3, z=z_asp - PARK_TIP_M, speed=speed, height=z_asp, secondary=z_sec)]
        if xy('aspirate'):
            aspirate[0]['xy_m'] = xy('aspirate')
        if z_sec is not None:
            aspirate.append(dict(name='aspirate_secondary', duration=abs(z_asp - z_sec) / speed + 0.6, z=z_sec - PARK_TIP_M, speed=speed, height=z_sec))
            if xy('secondary'):
                aspirate[-1]['xy_m'] = xy('secondary')
        dispense = dict(name='dispense', duration=VALVE_S + (z_disp - min(z_asp, z_sec or z_asp)) / LIFT_SPEED_M_S + t_disp, z=z_disp - PARK_TIP_M,
                        rate=p['dispense_rate'], volume=p['dispense_volume_m3'], height=z_disp, vac_delay=p['vacuum_delay_m3'], buffer=p['buffer'])
        if xy('dispense'):
            dispense['xy_m'] = xy('dispense')
        pre = []
        if p.get('predispense') or p.get('predispense_between_cycles'):      # a small prime into the trough with the carrier home (p.45)
            pre = [dict(name='prime', duration=p['predispense_volume_m3'] / FLOW_RATE_M3_S[p['predispense_rate']], z=TROUGH_Z, x=0.0,
                        volume=96 * p['predispense_volume_m3'], buffer=p['buffer'], predispense=True), dict(name='park', duration=0.5, z=0.0, x=0.0)]
        to_plate = dict(name='move_to_manifold', duration=CARRIER_MOVE_S, z=0.0, x=-CARRIER_TRAVEL_M)
        home = bool(p.get('home_carrier')) or (p['soak_s'] + p['shake_s']) > HOME_AFTER_S
        phases = [dict(ph) for ph in pre] if p.get('predispense') else []
        if kind == 'aspirate':
            phases += [to_plate] + aspirate
        elif kind == 'dispense':
            phases += [to_plate, dict(dispense, name='dispense_only', duration=VALVE_S + max(0.0, PARK_TIP_M - z_disp) / LIFT_SPEED_M_S + t_disp)]
        elif kind == 'soak':
            x = 0.0 if home else -CARRIER_TRAVEL_M
            if not home:
                phases.append(to_plate)
            if p['shake_s'] > 0:
                phases.append(dict(name='shake', duration=p['shake_s'], z=0.0, x=x, intensity=p['shake_intensity'], home=home))
            if p['soak_s'] > 0:
                phases.append(dict(name='soak', duration=p['soak_s'], z=0.0, x=x, home=home))
        else:
            phases.append(to_plate)
            if p['bottom_wash']:
                if p.get('bottom_wash_rate') is None:                         # wash.run (1.0.0): the dispense volume at rate >= 3
                    bw_rate, bw_vol, Qb = max(3, p['dispense_rate']), p['dispense_volume_m3'], Q
                else:
                    bw_rate, bw_vol = p['bottom_wash_rate'], p['bottom_wash_volume_m3']
                    Qb = FLOW_RATE_M3_S[bw_rate]
                phases.append(dict(name='bottom_wash', duration=VALVE_S + (z_disp - z_asp) / LIFT_SPEED_M_S + 2 * bw_vol / Qb + 0.5, z=z_asp - PARK_TIP_M,
                                   rate=bw_rate, volume=bw_vol, height=z_asp, buffer=p['buffer'], speed=speed))
            for c in range(p['cycles']):
                if c > 0 and p.get('predispense_between_cycles'):
                    phases += [dict(name='move_home', duration=CARRIER_MOVE_S, z=0.0, x=0.0)] + [dict(ph) for ph in pre] + [dict(to_plate)]
                phases += [dict(ph, cycle=c) for ph in aspirate] + [dict(dispense, cycle=c)]
                if p['shake_s'] > 0:
                    phases.append(dict(name='shake', duration=p['shake_s'] + (2 * CARRIER_MOVE_S if home else 0.0), z=0.0, x=0.0 if home else -CARRIER_TRAVEL_M,
                                       intensity=p['shake_intensity'], cycle=c, home=home))
                if p['soak_s'] > 0:
                    phases.append(dict(name='soak', duration=p['soak_s'], z=0.0, x=0.0 if home else -CARRIER_TRAVEL_M, cycle=c, home=home))
                if home and (p['soak_s'] > 0 or p['shake_s'] > 0):
                    phases.append(dict(name='move_to_manifold', duration=CARRIER_MOVE_S, z=0.0, x=-CARRIER_TRAVEL_M))
            if p['final_aspirate']:
                phases += [dict(ph, cycle=p['cycles'], final=True) for ph in aspirate]
        phases.append(dict(name='move_home', duration=CARRIER_MOVE_S + self.washer['vacuum_dissipate_s'], z=0.0, x=0.0))
        return phases

    # ---- trough, settings, clock, self-check, AutoPrime
    def _drain_trough(self):
        if self.trough > 0:
            self._to_waste(self.trough)
            self.trough = 0.0

    def _dev_washer_configure(self, **a):
        self._need_device()
        if self.op is not None or self._proto_active():
            raise ActionRejected('OPERATION_RUNNING')
        self.washer = dict(a, magnet_offset_mm=float(a['magnet_offset_mm']), vacuum_dissipate_s=float(a['vacuum_dissipate_s']))
        return dict(settings=dict(self.washer))

    def _dev_autoprime_configure(self, **a):
        self._need_device()
        if self.op is not None or self._proto_active():
            raise ActionRejected('OPERATION_RUNNING')
        if a['rate'] <= 2 and not self.plant['cell_wash_installed']:
            raise ActionRejected('CELL_WASH_OPTION_ABSENT')
        self.autoprime = dict(a, volume_m3=float(a['volume_m3']))
        return dict(autoprime=dict(self.autoprime))

    def _local(self, t_s=None):
        """Instrument clock: local date and time of simulation time t_s (now when None)."""
        t = self.t if t_s is None else t_s
        return CLOCK_EPOCH + datetime.timedelta(seconds=t + self.clock['offset_s'] + 3600 * self.clock['time_zone_h'])

    def _dev_clock_set(self, time_zone_h, date, time):
        self._need_device()
        try:
            local = datetime.datetime.strptime(f'{date} {time}', '%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        self.clock = dict(time_zone_h=time_zone_h, offset_s=round((local - CLOCK_EPOCH).total_seconds() - 3600 * time_zone_h - self.t, 6))
        return dict(local_time=local.strftime('%Y-%m-%dT%H:%M'))

    def _dev_instrument_self_check(self):
        self._need_device()
        self._need_idle()
        self.led['carrier_travel_m'].add(abs(self.carrier))
        self.led['manifold_travel_m'].add(abs(self.manifold))
        self.carrier, self.manifold = 0.0, 0.0
        self._drain_trough()
        code = 'MANIFOLD_SETTING_MISMATCH' if self.washer['manifold'] != '96' else None
        self.selfcheck = dict(passed=code is None, error_code=code, time_s=self.t)
        self._event('program', 'SELF_CHECK', '自检通过' if code is None else f'自检失败（{code}）')
        return dict(passed=code is None, error_code=code)

    def _at_home_screen(self):
        p = _PANELS.get(self)
        if p is None:                                                         # nobody has used the screen: it shows Home
            return True
        ui = p.ui
        return ui.get('screen') == 'main' and ui.get('page') == 'home' and ui.get('overlay') is None

    def _device_due(self):
        """The next device event when no operation runs: the end of a timed Delay step, or the AutoPrime time."""
        P = self.proto
        if P is not None and P['status'] == 'waiting':
            return P['wait_until']
        a = self.autoprime
        if a['enabled'] and self.power and self.plant is not None and not self._proto_active():
            return max(self.t, self.last_activity_s + 60.0 * a['interval_min'])
        return None

    def _device_event(self):
        P = self.proto
        if P is not None and P['status'] == 'waiting':
            P.update(status='running', wait_until=None)
            self._proto_next()
            return
        self.last_activity_s = self.t                                         # the interval restarts (also after a skipped attempt)
        if not self._at_home_screen():
            return
        a = self.autoprime
        try:
            self._seg_check(dict(type='prime', buffer=a['buffer']))
        except ActionRejected as exc:
            self._event('program', 'AUTOPRIME_SKIPPED', f'AutoPrime未执行（{exc}）')
            return
        settings = dict(buffer=a['buffer'], volume_m3=a['volume_m3'], rate=a['rate'], autoprime=True, submerge_s=60.0 * a['submerge_min'])
        rid = self._new_record('prime', 'PR', 'pr', settings, [x for x in [self.bottles[a['buffer']]['liquid']] if x])
        self._start('prime', self._plan_prime(a['buffer'], a['volume_m3'], a['rate'], 60.0 * a['submerge_min']), rid, buffer=a['buffer'], autoprime=True)
        self._event('program', 'AUTOPRIME', 'AutoPrime开始（Home屏空闲达到间隔）')

    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    @property
    def panel(self):
        """The front panel of this instance (kept outside the instance dict: a deep copy of the runtime starts with a fresh screen)."""
        if PANEL_SPEC is None:
            return None
        if self not in _PANELS:
            _PANELS[self] = _panel_ui.PanelUI(PANEL_SPEC, self)
        return _PANELS[self]

    def _when(self, t_s):
        if t_s is None:
            return '<n/a>'
        d = self._local(t_s)
        return f'{_date_text(d)} {d:%H:%M}'

    def _run_view(self):
        """What the run screen shows: the protocol run, a Quick / AutoPrime run, or the result of the last run."""
        P, o = self.proto, self.op
        busy = o is not None or self._proto_active()
        phase = self.phase if o is not None else None
        left = max(0.0, o['t_end'] - self.t) if o is not None else 0.0
        cycles = self._remaining()[0] if o is not None else 0
        if self.view == 'protocol' and P is not None:
            if P['status'] == 'waiting':
                left = max(0.0, P['wait_until'] - self.t)
            return dict(busy=busy, kind='protocol', title=P['name'] + (' (Test Run)' if P['test'] else ''), status=P['status'], code=P['code'],
                        step=f'Step {P["step"]} ({P["i"]} of {len(P["segments"])}): {STEP_NAMES.get(P["step_type"], "")}' if P['step'] else '',
                        phase=phase, cycles=cycles, left_s=left, prompt=P['prompt'], auto=False, test=P['test'], id=P['op'])
        if o is not None:
            kind = 'autoprime' if o.get('autoprime') else o['kind']
            return dict(busy=True, kind=kind, title=RUN_TITLES[kind], status='running', code=None, step='', phase=phase, cycles=cycles, left_s=left, prompt=None,
                        auto=False, test=False, id=o['op'])
        r = self.last_run
        if r is None:
            return dict(busy=False, kind=None, title='', status='idle', code=None, step='', phase=None, cycles=0, left_s=0.0, prompt=None, auto=False, test=False,
                        id=None)
        return dict(busy=False, kind=r['kind'], title=RUN_TITLES[r['kind']], status=r['status'], code=r['code'], step='', phase=None, cycles=0, left_s=0.0,
                    prompt=None, auto=r['kind'] == 'autoprime', test=False, id=r['op'])

    def panel_read(self):
        """What the touch-screen software sees: the agent slots plus host values (power-up count, clock texts, the protocol lists and the protocol being
        edited as the screens show them, the run screen, the configuration)."""
        out = self.read('agent')
        loc = self._local()
        plates = plate_lib()['plates']
        rows = {}
        for p in self.protocols.values():
            depth, lines = 0, []
            for i, st_ in enumerate(p['steps']):
                depth -= st_['type'] == 'end_loop'
                lines.append(dict(line=f'{i + 1}. ' + '  ' * depth + _step_text(st_)))
                depth += st_['type'] == 'loop'
            rows[p['name']] = dict(name=p['name'], kind=p['kind'], locked=p['locked'], plate=_plate_text(plates.get(p['plate_type'])), last_run=self._when(p['last_run_s']),
                                   modified=self._when(p['modified_s']), lines=lines, n=len(p['steps']))
        e = self.editor
        editor = None
        if e is not None:
            depth, erows = 0, []
            for i, st_ in enumerate(e['steps']):
                depth -= st_['type'] == 'end_loop'
                erows.append(dict(st_, index=i + 1, line=f'{i + 1}. ' + '  ' * depth + _step_text(st_)))
                depth += st_['type'] == 'loop'
            erows.append(dict(type='end', index=len(e['steps']) + 1, line='<end of steps>'))
            editor = dict(name=e['name'] or '<new protocol>', raw_name=e['name'], plate_type=e['plate_type'], plate=_plate_text(plates.get(e['plate_type'])),
                          rows=erows, n=len(e['steps']), clipboard=len(e['clipboard']), locked=bool(e['source'] and self.protocols.get(e['source'], {}).get('locked')))
        sc = self.selfcheck
        out.update({
            'host.powered': self.power, 'host.power_epoch': self.power_epoch, 'host.instance_id': self.instance_id,
            'host.clock': dict(date=_date_text(loc), time=f'{loc:%H:%M}', iso_date=f'{loc:%Y-%m-%d}', iso_time=f'{loc:%H:%M}',
                               tz=f'GMT{self.clock["time_zone_h"]:+03d}:00', tz_h=self.clock['time_zone_h']),
            'host.protocols': rows, 'host.home_list': [r for r in rows.values() if r['kind'] in ('sample', 'user')],
            'host.maint_list': [r for r in rows.values() if r['kind'] in ('maintenance', 'qc')], 'host.define_list': list(rows.values()),
            'host.editor': editor, 'host.run': self._run_view(),
            'host.plate_types': [dict(value=k, text=_plate_text(v)) for k, v in plates.items() if v['compatible_with_96_tube_single_manifold']],
            'host.config': dict(cell_wash='Installed' if self.plant and self.plant['cell_wash_installed'] else 'Not installed',
                                selfcheck='Not run' if sc is None else ('Passed' if sc['passed'] else f'Failed: {sc["error_code"]}'),
                                selfcheck_time='' if sc is None else self._when(sc['time_s']),
                                manifold_setting={'96': '96-tube', '192': '192-tube'}[self.washer['manifold']])})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action calls the same command a direct call would (each command checks before it changes anything).  A gesture made through a
        session acts as that session's caller: the same ownership rule as a direct call (another agent's run can be viewed, not stopped or changed),
        and a run it starts belongs to that caller."""
        try:
            caller = self._caller
            if caller is not None:
                validate(ALL_CAPS[capability_id]['input_schema'], arguments)
                Biotek405tsSession(self, caller)._guard(capability_id, arguments)
            out = self._invoke_any(capability_id, **arguments)
            op = out.get('operation_id') if caller is not None and capability_id in ASYNC and isinstance(out, dict) else None
            if op:
                self._owners[op] = caller
                if self.operations[op].get('record_id'):
                    self._owners[self.operations[op]['record_id']] = caller
                self._adopt(op)
            return out
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.t

    def _panel_command(self, k, a):
        self._need_plant()
        if k == 'panel.frame':
            return dict(frame=self.panel.frame())
        if k == 'panel.capture':
            data, w, h = self.panel.png_bytes(a['display_id'])
            sha = hashlib.sha256(data).hexdigest()
            d = Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id
            d.mkdir(parents=True, exist_ok=True)
            path = d / f'{a["display_id"]}_{sha[:16]}.png'
            if not path.exists():
                path.write_bytes(data)
            return dict(display_id=a['display_id'], time_s=self.t, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))
        try:
            if k == 'panel.press':
                ok = self.panel.press(a['input_id'], float(a.get('duration_s', 0.1)))
            else:
                to = (a['to_x'], a['to_y']) if 'to_x' in a and 'to_y' in a else None
                ok = self.panel.touch(a['display_id'], a['x'], a['y'], float(a.get('duration_s', 0.1)), to)
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        self.revision += 1
        self.last_activity_s = self.t                                         # a touch restarts the AutoPrime interval (p.79)
        return dict(accepted=bool(ok))


def _ints(schema, v):
    """Integer fields as int (a panel number field may send 3.0): top level, objects, arrays and the step union."""
    if isinstance(v, dict) and 'anyOf' in schema:
        for s in schema['anyOf']:
            if s.get('type') == 'object' and v.get('type') in (s['properties'].get('type') or {}).get('enum', [None]):
                return _ints(s, v)
        return v
    if isinstance(v, dict) and schema.get('type') == 'object':
        props = schema.get('properties', {})
        return {k: _ints(props[k], x) if k in props else x for k, x in v.items()}
    if isinstance(v, list) and schema.get('type') == 'array':
        return [_ints(schema['items'], x) for x in v]
    if schema.get('type') == 'integer' and isinstance(v, float):
        return int(v)
    return v


STEP_NAMES = {'wash': 'Wash', 'aspirate': 'Aspirate', 'dispense': 'Dispense', 'prime': 'Prime', 'soak': 'Shake/Soak', 'delay': 'Delay', 'remark': 'Remark',
              'loop': 'Loop', 'end_loop': 'End Loop'}
RUN_TITLES = {'protocol': 'Protocol', 'wash': 'Wash', 'prime': 'Prime', 'autoprime': 'AutoPrime', 'clean': 'Clean tubes'}


def _date_text(d):
    """The only date format of the 405 TS: DD Month YY (p.23)."""
    return f'{d.day:02d} {MONTHS[d.month - 1]} {d.year % 100:02d}'


def _plate_text(p):
    return '---' if p is None else f'Costar {p["costar_number"]} {p["description"]}'[:40]


def _dur(s):
    s = float(s)
    if s < 60:
        return f'{s:g} s'
    if s < 3600:
        return f'{s / 60:g} min'
    return f'{int(s // 3600)}:{int(s % 3600 // 60):02d} h'


def _step_text(s):
    """One line of a protocol's step list."""
    t = s['type']
    ul = lambda v: f'{v * 1e9:g} uL'  # noqa: E731
    if t == 'wash':
        extra = [x for x, on in (('bottom wash', s['bottom_wash']), ('shake', s['shake']), ('soak', s['soak'])) if on]
        return f'Wash: {s["cycles"]} cycles, {ul(s["dispense_volume_m3"])}, buffer {s["buffer"]}' + ''.join(', ' + x for x in extra)
    if t == 'aspirate':
        return f'Aspirate: Z {s["aspirate_z_steps"]}, rate {s["travel_rate"]}' + (f', secondary Z {s["secondary_z_steps"]}' if s['secondary_aspirate'] else '')
    if t == 'dispense':
        return f'Dispense: {ul(s["dispense_volume_m3"])}, rate {s["dispense_rate"]}, buffer {s["buffer"]}'
    if t == 'prime':
        return f'Prime: {s["volume_m3"] * 1e6:g} mL, buffer {s["buffer"]}, rate {s["rate"]}' + (f', submerge {_dur(s["submerge_s"])}' if s['submerge'] else '')
    if t == 'soak':
        parts = ([f'Shake {_dur(s["shake_s"])} {s["shake_intensity"].capitalize()}'] if s['shake'] else []) + ([f'Soak {_dur(s["soak_s"])}'] if s['soak'] else [])
        return ' / '.join(parts) or 'Shake/Soak: off'
    if t == 'delay':
        return 'Delay: ' + ('wait for Continue' if s['wait_for_user'] else _dur(s['duration_s'])) + (f' - {s["message"]}' if s['message'] else '')
    if t == 'remark':
        return f'Remark: {s["text"]}'
    if t == 'loop':
        return f'Loop {s["count"]} times'
    return 'End Loop'


class Biotek405tsRuntime(_TouchSoftware):
    """405 TS microplate washer: power, buffer bottles and valves, prime, wash step (aspirate / dispense / soak / shake), plate carrier and manifold
    positions, sensors, wash log; plates with per-well liquid, soluble / adsorbed / bound species and adherent cells (SI units)."""

    def __init__(self, instance_id='washer_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self.plant = None
        self.power = False
        self.bottles = {b: dict(liquid=None, volume=0.0) for b in BOTTLES}
        self.valve = 'A'
        self.tanks = [{'water': 1.0} for _ in range(LINE_TANKS)]   # the fluid path (tanks in series) is wet with water (primed at installation)
        self.waste = 0.0
        self.carrier = 0.0
        self.manifold = 0.0
        self.phase = None
        self.plates = {}
        self.on_carrier = None
        self.records = {}
        self.seq = dict(ws=0, pr=0, cl=0, op=0)
        self.op = None
        self.operations = {}
        self.faults = set()
        self.clog_d = {}                                   # tube -> remaining flow fraction (0 = blocked)
        self.clog_a = set()
        self.idle_s = 0.0
        self.idle_n, self.idle_H, self.clog_E = 0, 0.0, None   # idle clogging: hazard integral and pre-sampled per-tube thresholds
        self.drip_clock, self.drip_next = 0.0, None        # plate-under-manifold exposure time and each tube's next drop
        self.drip_left = np.zeros(96, int)                 # drops still hanging at each tube tip (refilled by a dispense / prime)
        self.tip_liquid = {'water': 1.0}                   # composition of the liquid wetting the tube tips
        self.flow_ok = True
        self.events, self.events_dropped = [], 0
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self._tube_bias = None
        self._principals, self._requests, self._owners = {}, {}, {}
        self.view = None                                   # what the run screen follows: 'protocol' or 'op'
        self._air = False
        self._init_device()

    # ---------------------------------------------------------------- basics
    @property
    def time_s(self):
        return self.t

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator') or name in self._principals:
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return Biotek405tsSession(self, name)

    def _event(self, kind, code, message, rid=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _rng(self, tag):
        h = hashlib.sha256(f'{self.plant["noise_seed"] if self.plant else 0}:{tag}'.encode()).digest()
        return np.random.default_rng(int.from_bytes(h[:8], 'little'))

    def _T(self):
        return self.plant['room_k'] if self.plant else 295.15

    def _vacuum_ok(self):
        return bool(self.plant and self.plant['vacuum_pump'] and self.power and 'vacuum_leak' not in self.faults)

    def _waste_full(self):
        if 'waste_sensor_stuck' in self.faults:
            return True
        return bool(self.plant) and bool(self.waste >= 0.9 * self.plant['waste_capacity_m3'])

    def _fluid_ok(self, b=None):
        b = b or self.valve
        return bool(self.bottles[b]['volume'] > 1e-6 and self.bottles[b]['liquid'] is not None and 'buffer_line_air' not in self.faults)

    def _tip(self):
        return PARK_TIP_M + self.manifold

    def _remaining(self):
        o = self.op
        if o is None:
            return 0, 0.0
        left = [p for p in o['phases'][o['i']:]]
        cycles = sum(1 for p in left if p['name'] == 'dispense')
        t_left = max(0.0, o['t_end'] - self.t)
        return cycles, t_left

    def read(self, visibility='agent'):
        o = self.op
        prog = 0.0 if o is None else min(1.0, (self.t - o['t0']) / max(1e-9, o['t_end'] - o['t0']))
        cyc, tl = self._remaining()
        p = self.plates.get(self.on_carrier)
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'carrier.x_m': self.carrier,
            'carrier.at_home': abs(self.carrier) < 1e-12, 'manifold.z_m': self.manifold, 'manifold.tip_height_m': self._tip(), 'buffer.selected': self.valve,
            **{f'buffer.{b.lower()}.liquid': self.bottles[b]['liquid'] for b in BOTTLES}, **{f'buffer.{b.lower()}.volume_m3': self.bottles[b]['volume'] for b in BOTTLES},
            'sensor.fluid_ok': self._fluid_ok(), 'sensor.vacuum_ok': self._vacuum_ok(), 'sensor.waste_full': self._waste_full(), 'sensor.flow_ok': self.flow_ok,
            'program.phase': self.phase, 'operation.kind': o['kind'] if o else None, 'operation.progress': prog, 'operation.started_by_other_agent': False,
            'display.cycles_remaining': int(cyc) if self.power else 0, 'display.time_remaining_s': float(round(tl)) if self.power else 0.0,
            'result.last_record_id': self._last_record(), 'plate.on_carrier': self.on_carrier,
            'plate.bench_ids': sorted(k for k, v in self.plates.items() if v['location'] == 'bench'),
            'truth.line_composition': dict(self.line), 'truth.waste_volume_m3': self.waste,
            'truth.residual_m3': None if p is None else [float(v) for v in p['V']], 'truth.clogged_dispense': sorted(self.clog_d),
            'truth.clogged_aspirate': sorted(self.clog_a), 'truth.faults': sorted(self.faults), 'truth.idle_s': self.idle_s,
        }
        values.update(self._device_values())
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] == 'completed' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        props = CAPS[capability_id]['input_schema'].get('properties', {})
        a = {k: (int(v) if props.get(k, {}).get('type') == 'integer' and v is not None else v) for k, v in a.items()}
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
            self.revision += 1
        if CAPS[capability_id]['kind'] == 'device_command':
            self.last_activity_s = self.t                                     # an interaction restarts the AutoPrime interval (p.79)
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_idle(self):
        self._need_plant()
        if self.op is not None or self._proto_active():                      # (a protocol run between two steps is running too)
            raise ActionRejected('OPERATION_RUNNING')

    def _need_power(self):
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed, vacuum_pump, cell_wash_installed, waste_capacity_m3):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        if self.op is not None and (vacuum_pump != self.plant['vacuum_pump'] or cell_wash_installed != self.plant['cell_wash_installed']):
            raise ActionRejected('OPERATION_RUNNING')
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, noise_seed=noise_seed, vacuum_pump=vacuum_pump, cell_wash_installed=cell_wash_installed,
                          waste_capacity_m3=waste_capacity_m3)
        if self._tube_bias is None:
            self._tube_bias = self._rng('tube_bias').normal(0.0, CV_TUBE, 96)
        if self.power and not (100.0 * 0.9 <= mains_voltage_v <= 240.0 * 1.1):
            self._power_off('MAINS_FAULT', '供电电压超出范围，电源断开')
        return dict(plant=dict(self.plant))

    def _power_off(self, code, msg):
        self.power = False
        if self.op is not None:
            self._fail_op('POWER_LOST')
        elif self._proto_active():
            self._proto_end('failed', 'POWER_LOST')
        self.editor = None                                                    # the protocol being edited is lost (assumption)
        self._event('power', code, msg)

    def _set_power(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            if not (100.0 * 0.9 <= self.plant['mains_voltage_v'] <= 240.0 * 1.1):
                raise ActionRejected('MAINS_OUT_OF_RANGE')
            self.power = True
            self.power_epoch += 1
            self.view, self.last_run = None, None                             # after a power-up the screen starts at Home
            self.last_activity_s = self.t
            self._event('power', 'POWER_ON', '通电（自检后载板回初始位、洗头停放）')
            self.led['carrier_travel_m'].add(abs(self.carrier))
            self.led['manifold_travel_m'].add(abs(self.manifold))
            self.carrier, self.manifold = 0.0, 0.0
        elif not enabled and self.power:
            self._power_off('POWER_OFF', '断电')
        return dict(enabled=self.power)

    def _cmd_power_set(self, enabled):
        return self._set_power(enabled)

    def _cmd_power_press_switch(self):
        return self._set_power(not self.power)

    def _cmd_buffer_load(self, bottle, liquid_id, volume_m3):
        self._need_plant()
        if liquid_id not in liquid_lib()['substances']:
            raise ActionRejected('UNKNOWN_LIQUID')
        if self.op is not None and self.op.get('buffer') == bottle:
            raise ActionRejected('OPERATION_RUNNING')
        self.bottles[bottle] = dict(liquid=liquid_id, volume=float(volume_m3))
        self._event('fluid', 'BOTTLE_FILLED', f'供液瓶{bottle}：{liquid_id} {volume_m3 * 1e3:.2f} L')
        return dict(bottle=bottle, liquid_id=liquid_id, volume_m3=float(volume_m3))

    def _cmd_waste_empty(self):
        self._need_plant()
        if self.op is not None:
            raise ActionRejected('OPERATION_RUNNING')
        v = self.waste
        self.waste = 0.0
        self._event('fluid', 'WASTE_EMPTIED', f'倒空废液约{round(v / 50e-6) * 0.05:.2f} L')
        return dict(emptied_m3=round(v / 50e-6) * 50e-6)                   # read on the bottle's 50 mL graduations (the true volume is ledger truth)

    def _cmd_plate_provide(self, plate_id, plate_type, fill_volume_m3, liquid_id, unbound_mol, nonspecific_mol, specific_mol, cells_per_well, cell_adhesion, wells):
        self._need_plant()
        if plate_id in self.plates:
            raise ActionRejected('PLATE_EXISTS')
        if len(self.plates) >= MAX_PLATES:
            raise ActionRejected('TOO_MANY_PLATES')
        pt = plate_lib()['plates'].get(plate_type)
        if pt is None:
            raise ActionRejected('UNKNOWN_PLATE_TYPE')
        if not pt['compatible_with_96_tube_single_manifold']:
            raise ActionRejected('PLATE_INCOMPATIBLE')
        if liquid_id not in liquid_lib()['substances']:
            raise ActionRejected('UNKNOWN_LIQUID')
        if fill_volume_m3 > pt['well_volume_m3']:
            raise ActionRejected('WELL_OVERFILLED')
        if cells_per_well > 0 and cell_adhesion == 'none':
            raise ActionRejected('INVALID_ARGUMENT')
        used = np.zeros(96, bool)
        used[list(range(96)) if wells is None else wells] = True
        rng = self._rng(f'plate:{plate_id}')
        f = used.astype(float)
        self.plates[plate_id] = dict(
            location='bench', type=plate_type, liquid=liquid_id, used=used.tolist(), V=f * fill_volume_m3, unbound=f * unbound_mol, ns=f * nonspecific_mol,
            spec=f * specific_mol, cells=f * cells_per_well, cells0=f * cells_per_well, adhesion=cell_adhesion, contamination=np.zeros(96), comp=[{liquid_id: 1.0} for _ in range(96)],
            wbe=pt['well_bottom_elevation_m'] + rng.normal(0.0, WBE_SCATTER_M, 96), dry_s=np.zeros(96), ufrac=np.zeros(96), layer_V=np.zeros(96))
        self._event('plate', 'PLATE_PROVIDED', f'板{plate_id}（{plate_type}）放在实验台上')
        return dict(plate_id=plate_id, location='bench')

    def _cmd_plate_load(self, plate_id):
        self._need_idle()
        p = self.plates.get(plate_id)
        if p is None:
            raise ActionRejected('UNKNOWN_PLATE')
        if p['location'] != 'bench':
            raise ActionRejected('PLATE_NOT_ON_BENCH')
        if self.on_carrier is not None:
            raise ActionRejected('CARRIER_OCCUPIED')
        if abs(self.carrier) > 1e-12 or abs(self.manifold) > 1e-12:
            raise ActionRejected('CARRIER_NOT_HOME')
        p['location'] = 'carrier'
        self.on_carrier = plate_id
        self._event('plate', 'PLATE_LOADED', f'板{plate_id}放上载板', rid=None)
        return dict(plate_id=plate_id)

    def _cmd_plate_unload(self, plate_id):
        self._need_idle()
        if self.on_carrier != plate_id:
            raise ActionRejected('PLATE_NOT_ON_CARRIER')
        if abs(self.carrier) > 1e-12 or abs(self.manifold) > 1e-12:
            raise ActionRejected('CARRIER_NOT_HOME')
        self.plates[plate_id]['location'] = 'bench'
        self.on_carrier = None
        self._event('plate', 'PLATE_UNLOADED', f'板{plate_id}取下')
        return dict(plate_id=plate_id)

    # ---------------------------------------------------------------- operations: planning
    def _new_record(self, kind, prefix, key, settings, liquids):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        self.seq[key] += 1
        rid = f'{prefix}{self.seq[key]:04d}'
        self.records[rid] = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, plate_id=self.on_carrier if kind == 'wash' else None,
                                 settings=copy.deepcopy(settings), cycles_done=0, programmed_dispense_m3=0.0, _dispensed_m3=0.0, _aspirated_m3=0.0, alarms=[], warnings=[],
                                 template_source=dict(liquids=sorted(liquids), plate=self.plates[self.on_carrier]['type'] if kind == 'wash' and self.on_carrier else None))
        return rid

    def _start(self, kind, phases, rid, **extra):
        self.seq['op'] += 1
        oid = f'op_{self.seq["op"]:04d}'
        t = self.t
        for ph in phases:
            ph['t_start'] = t
            t += ph['duration']
        self.op = dict(op=oid, kind=kind, t0=self.t, t_end=t, phases=phases, i=0, record_id=rid, **extra)
        self.operations[oid] = dict(phase='running', kind=kind, record_id=rid, op=oid)
        self.view = 'protocol' if extra.get('proto') else 'op'
        self._begin_phase()
        return dict(operation_id=oid)

    def _check_supply(self, bottle):
        if self.washer['sensor_waste'] and self._waste_full():                # sensors as enabled on Instrument > Options (p.69-70)
            raise ActionRejected('WASTE_FULL')
        if self.washer['sensor_fluid'] and not self._fluid_ok(bottle):
            raise ActionRejected('BUFFER_EMPTY')

    def _cmd_prime_run(self, buffer, volume_m3, rate):
        self._need_idle()
        self._need_power()
        if abs(self.manifold) > 1e-12 or abs(self.carrier) > 1e-12:
            raise ActionRejected('CARRIER_NOT_HOME')
        if self.on_carrier is not None:
            raise ActionRejected('PLATE_ON_CARRIER')         # priming dispenses into the trough exposed with an empty carrier at home
        self._check_supply(buffer)
        if not self._vacuum_ok():
            raise ActionRejected('VACUUM_LOW')
        dur = volume_m3 / (96 * FLOW_RATE_M3_S[rate])
        rid = self._new_record('prime', 'PR', 'pr', dict(buffer=buffer, volume_m3=volume_m3, rate=rate), [self.bottles[buffer]['liquid']])
        phases = [dict(name='valve', duration=VALVE_S, z=0.0, x=0.0), dict(name='prime', duration=dur, z=-PARK_TIP_M + 0.005, x=0.0, volume=volume_m3, buffer=buffer),
                  dict(name='park', duration=0.5, z=0.0, x=0.0)]
        return self._start('prime', phases, rid, buffer=buffer)

    def _cmd_wash_run(self, protocol):
        self._need_idle()
        self._need_power()
        p = dict(protocol)
        if self.on_carrier is None:
            raise ActionRejected('NO_PLATE')
        if abs(self.carrier) > 1e-12 or abs(self.manifold) > 1e-12:
            raise ActionRejected('CARRIER_NOT_HOME')
        cw_disp, cw_trav = p['dispense_rate'] <= 2, p['travel_rate'].endswith('CW')
        if (cw_disp or cw_trav) and not self.plant['cell_wash_installed']:
            raise ActionRejected('CELL_WASH_OPTION_ABSENT')
        if abs(p['dispense_volume_m3'] / VOLUME_STEP_M3 - round(p['dispense_volume_m3'] / VOLUME_STEP_M3)) > 1e-6:
            raise ActionRejected('VOLUME_NOT_IN_1UL_STEPS')
        if p['vacuum_delay_m3'] > p['dispense_volume_m3']:
            raise ActionRejected('INVALID_ARGUMENT')
        self._check_supply(p['buffer'])
        if not self._vacuum_ok():
            raise ActionRejected('VACUUM_LOW')
        pt = plate_lib()['plates'][self.plates[self.on_carrier]['type']]
        phases = self._plan_wash(p)
        liquids = [self.bottles[p['buffer']]['liquid']] + list(self.line)
        rid = self._new_record('wash', 'WS', 'ws', p, liquids)
        if pt['well_volume_m3'] < p['dispense_volume_m3']:
            self.records[rid]['warnings'].append('dispense_volume_exceeds_well_volume')
        return self._start('wash', phases, rid, buffer=p['buffer'], protocol=p)

    def _cmd_maintenance_clean_tubes(self):
        self._need_idle()
        rid = self._new_record('clean', 'CL', 'cl', {}, [])
        phases = [dict(name='clean', duration=600.0, z=0.0, x=0.0)]
        return self._start('clean', phases, rid)

    # ---------------------------------------------------------------- operations: execution
    def _begin_phase(self):
        o = self.op
        ph = o['phases'][o['i']]
        self.phase = ph['name']
        d = abs(ph.get('x', self.carrier) - self.carrier)
        self.led['carrier_travel_m'].add(d)
        self.led['manifold_travel_m'].add(abs(ph.get('z', self.manifold) - self.manifold))
        self.carrier = ph.get('x', self.carrier)
        self.manifold = ph.get('z', self.manifold)
        if ph['name'] == 'valve' or ph['name'] in ('dispense', 'dispense_only', 'bottom_wash', 'prime'):
            self.valve = ph.get('buffer', self.valve)

    def _next_event_t(self):
        o = self.op
        if o is None:
            return self._device_due()                                         # a timed Delay step ending, or AutoPrime
        ph = o['phases'][o['i']]
        return ph['t_start'] + ph['duration']

    def step(self, dt_s):
        if not (isinstance(dt_s, (int, float)) and math.isfinite(dt_s) and dt_s >= 0):
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + dt_s
        while True:
            nxt = self._next_event_t()
            if nxt is None or nxt > t_end + 1e-12:
                self._advance(t_end - self.t)
                break
            self._advance(max(0.0, nxt - self.t))
            if self.op is not None:
                self._finish_phase()
            else:
                self._device_event()
        return dict(time_s=self.t)

    def _advance(self, h):
        if h <= 0:
            return
        if self.power:
            self.led['electrical_energy_j'].add((ACTIVE_W if self.op is not None and self.op['kind'] != 'clean' else IDLE_W) * h)
        if self.op is None or self.op['kind'] == 'clean':
            self._idle(h)
        self._drip(h)
        self._dry(h)
        self.t += h

    def _reset_idle(self):
        """Fresh liquid in the tubes: restart the idle clock and draw each tube's clogging threshold (unit exponential) and residual flow; each tube
        tip holds DROPS_PER_TIP drops that can fall later (siphoned from the selected bottle through the line; booked in dripped_m3)."""
        self.idle_s, self.idle_H, self.idle_n = 0.0, 0.0, self.idle_n + 1
        self.clog_E = None
        self.drip_left[:] = DROPS_PER_TIP

    def _idle(self, h):
        """Idle manifold: liquid dries in the tube tips; each dispense and aspirate tube clogs with the hazard rate 0.5 / tau, tau = CLOG_TAU_S (1 %
        salt) / salt fraction of the liquid left in the tubes.  The thresholds are drawn once per idle period, so the result does not depend on how the
        idle time is split into steps."""
        self.idle_s += h
        if self._submerged():                                                 # tips in the filled priming trough do not dry (p.82)
            return
        s = salt_fraction(self.tip_liquid)
        if s <= 0 or self.plant is None:
            return
        if self.clog_E is None:
            rng = self._rng(f'clog:{self.idle_n}')
            self.clog_E = (rng.exponential(1.0, 96), rng.exponential(1.0, 96), rng.uniform(0.0, 0.7, 96))
        H0 = self.idle_H
        self.idle_H += 0.5 * h / (CLOG_TAU_S * 0.01 / s)
        Ed, Ea, frac = self.clog_E
        for i in np.where((Ed > H0) & (Ed <= self.idle_H))[0]:
            self.clog_d[int(i)] = min(self.clog_d.get(int(i), 1.0), float(frac[i]))
        for i in np.where((Ea > H0) & (Ea <= self.idle_H))[0]:
            self.clog_a.add(int(i))

    def _drip(self, h):
        """A plate left under the parked manifold catches drops from the tube tips (the guide sends the carrier home for soaks > 1 min): a Poisson
        process per tube in the exposure time, with the drop times drawn per tube and drop so that the result does not depend on the step size."""
        p = self.plates.get(self.on_carrier)
        if p is None or abs(self.carrier + CARRIER_TRAVEL_M) > 1e-9 or self.phase not in ('soak', 'shake', None):
            return
        seed = self.plant['noise_seed'] if self.plant else 0
        if self.drip_next is None:
            self.drip_next = [(float(np.random.default_rng([seed, 7, i, 0]).exponential(1 / DRIP_P_PER_S)), 0) for i in range(96)]
        end = self.drip_clock + h
        n = 0
        pt = plate_lib()['plates'][p['type']]
        cap = pt['well_volume_m3'] + math.pi * pt['mean_well_radius_m'] ** 2 * MENISCUS_M * 0.5
        spilled, lost = np.zeros(96), np.zeros(96)
        for i in range(96):
            t, k = self.drip_next[i]
            while t <= end and self.drip_left[i] > 0 and self.bottles[self.valve]['volume'] >= DROP_M3:
                self.drip_left[i] -= 1                                   # the drop siphons out of the line: the selected bottle loses it
                self.bottles[self.valve]['volume'] -= DROP_M3
                self.led['dripped_m3'].add(DROP_M3)                        # clean line buffer: it dilutes, it carries no analyte
                if p['V'][i] + DROP_M3 > cap:                                # a full well runs over (the drop's volume leaves at the rim)
                    spilled[i] += p['V'][i] + DROP_M3 - cap
                    keep = cap / (p['V'][i] + DROP_M3)
                    lost[i] += (p['unbound'][i] + p['contamination'][i]) * (1 - keep)
                    p['unbound'][i] *= keep
                    p['contamination'][i] *= keep
                    p['V'][i] = cap
                else:
                    p['V'][i] += DROP_M3
                n += 1
                k += 1
                t += float(np.random.default_rng([seed, 7, i, k]).exponential(1 / DRIP_P_PER_S))
            while t <= end:                                              # the tip is dry: the drop times still advance
                k += 1
                t += float(np.random.default_rng([seed, 7, i, k]).exponential(1 / DRIP_P_PER_S))
            self.drip_next[i] = (t, k)
        self.drip_clock = end
        if spilled.any():
            self._spill(p, spilled, self.records.get(self.op['record_id']) if self.op else None, lost)
        if n:
            self._event('plate', 'DRIP', f'{n}滴从洗头管尖落入板孔')

    def _tip_conc(self):
        return self.op['tip_conc'] if self.op and 'tip_conc' in self.op else 0.0

    def _dry(self, h):
        """Wells that are almost empty dry out; adherent cells in them are damaged after DRY_DWELL_S."""
        for p in self.plates.values():
            low = (p['V'] < 3e-9) & (p['cells'] > 0)
            p['dry_s'][low] += h
            p['dry_s'][~low] = 0.0
            bad = p['dry_s'] > DRY_DWELL_S
            if bad.any():
                p['cells'][bad] *= math.exp(-h / 60.0)

    def _finish_phase(self):
        o = self.op
        ph = o['phases'][o['i']]
        rec = self.records[o['record_id']]
        ok = self._apply_phase(ph, rec)
        if not ok:
            return
        o['i'] += 1
        if o['i'] >= len(o['phases']):
            self._complete()
        else:
            self._begin_phase()

    def _apply_phase(self, ph, rec):
        n = ph['name']
        if n == 'prime':
            return self._do_prime(ph, rec)
        if n in ('aspirate', 'aspirate_secondary'):
            return self._do_aspirate(ph, rec)
        if n == 'dispense':
            return self._do_dispense(ph, rec)
        if n == 'dispense_only':                                              # Dispense step (no wash cycle)
            return self._do_dispense(ph, rec, count=False)
        if n == 'submerge':                                                   # end of the submerge: the trough drains, the manifold parks
            self._drain_trough()
            return True
        if n == 'bottom_wash':
            return self._do_bottom_wash(ph, rec) and self._do_aspirate(ph, rec) and self._do_dispense(dict(ph, vac_delay=ph['volume']), rec, count=False)
        if n == 'shake':
            self._mix(ph['duration'], SHAKE_TAU[ph['intensity']], shake=True)
            return True
        if n == 'soak':
            self._mix(ph['duration'], None, shake=False)
            return True
        if n == 'clean':
            n_cl = len(self.clog_d) + len(self.clog_a)
            self.clog_d.clear()
            self.clog_a.clear()
            self._event('maintenance', 'TUBES_CLEANED', f'清洁针疏通{n_cl}处堵塞')
            return True
        return True

    @property
    def line(self):
        """Composition of the liquid at the manifold end of the fluid path (the last tank)."""
        return self.tanks[-1]

    def _draw(self, bottle, V, rec):
        """Take up to V from a supply bottle through the fluid path (LINE_TANKS stirred tanks in series, total dead volume 110 mL).  Returns the mean
        composition of the liquid that leaves at the manifold and the volume drawn; a bottle that runs dry mid-draw delivers what it holds and raises
        BUFFER_EMPTY (fluid sensor); (None, 0) when it is empty from the start."""
        b = self.bottles[bottle]
        if not self._fluid_ok(bottle):
            if not self.washer['sensor_fluid']:                              # fluid sensor off: the pump draws air, nothing is delivered (p.70)
                self._air = True
                return dict(self.line), 0.0
            self._alarm(rec, 'BUFFER_EMPTY')
            return None, 0.0
        V_take = min(V, b['volume'])
        Vt = DEAD_VOLUME_M3 / LINE_TANKS
        n = max(1, int(math.ceil(V_take / (Vt / 40))))
        dv = V_take / n
        out = {}
        for _ in range(n):
            inflow = {b['liquid']: 1.0}
            for j, tank in enumerate(self.tanks):
                new = {k: x * (1 - dv / Vt) for k, x in tank.items()}
                for k, x in inflow.items():
                    new[k] = new.get(k, 0.0) + x * dv / Vt
                inflow = tank                                            # the outflow of this tank (its composition before the step) feeds the next
                self.tanks[j] = new
            for k, x in inflow.items():
                out[k] = out.get(k, 0.0) + x * dv / max(V_take, 1e-30)
        self.tanks = [{k: x / sum(t.values()) for k, x in t.items() if x > 1e-12} for t in self.tanks]
        b['volume'] -= V_take
        if V_take < V * (1 - 1e-12):
            if self.washer['sensor_fluid']:
                self._alarm(rec, 'BUFFER_EMPTY')
            else:
                self._air = True
        tot = sum(out.values()) or 1.0
        return {k: x / tot for k, x in out.items() if x / tot > 1e-12}, V_take

    def _alarm(self, rec, code):
        if code not in rec['alarms']:
            rec['alarms'].append(code)
        self._event('alarm', code, {'WASTE_FULL': '废液瓶满，运行停止', 'VACUUM_LOW': '真空不足，抽吸失败', 'BUFFER_EMPTY': '所选供液瓶无液',
                                    'FLOW_ERROR': '分配总流量低于设定（可能堵针）', 'POWER_LOST': '断电'}[code], rid=rec['record_id'])
        if code in ('WASTE_FULL', 'VACUUM_LOW', 'BUFFER_EMPTY', 'POWER_LOST'):
            self._fail_op(code)

    def _do_prime(self, ph, rec):
        self._drain_trough()                                                  # liquid left in the trough by a submerge goes first
        over = self.waste + ph['volume'] > self.plant['waste_capacity_m3']   # the prime volume must fit into the bottles (C4)
        if (self.washer['sensor_waste'] and self._waste_full()) or over:
            self._alarm(rec, 'WASTE_FULL' if self.washer['sensor_waste'] else 'VACUUM_LOW')   # sensor off: the waste backs up into the pump (p.37)
            return False
        self._air = False
        comp, got = self._draw(ph['buffer'], ph['volume'], rec)
        if comp is None:
            return False
        rec['_dispensed_m3'] += got
        rec['programmed_dispense_m3'] += ph['volume']
        self.led['primed_m3'].add(got)
        keep = min(TROUGH_M3 - self.trough, got) if ph.get('fill_trough') else 0.0   # submerge: the trough drain stays closed
        self._to_waste(got - keep)
        self.trough += keep
        if got > 0 or not self._air:
            self.tip_liquid = dict(self.line)
            self._reset_idle()
        return got >= ph['volume'] * (1 - 1e-12) or self._air

    def _to_waste(self, V):
        self.waste += V
        self.led['waste_added_m3'].add(V)

    def _aspirated(self, rec, V):
        rec['_aspirated_m3'] += V
        self.led['aspirated_m3'].add(V)
        self._to_waste(V)

    def _do_aspirate(self, ph, rec):
        if not self._vacuum_ok():
            self._alarm(rec, 'VACUUM_LOW')
            return False
        p = self.plates[self.on_carrier]
        over = self.waste + float(np.sum(p['V'])) > self.plant['waste_capacity_m3']   # the plate's liquid must fit (C4)
        if (self.washer['sensor_waste'] and self._waste_full()) or over:
            self._alarm(rec, 'WASTE_FULL' if self.washer['sensor_waste'] else 'VACUUM_LOW')
            return False
        pt = plate_lib()['plates'][p['type']]
        if ph.get('xy_m', 0.0) + TUBE_OUTER_R_M > pt['mean_well_radius_m'] and 'tubes_touch_well_wall' not in rec['warnings']:
            rec['warnings'].append('tubes_touch_well_wall')                   # X / Y offset beyond the well (no limit on the instrument either)
        A = math.pi * pt['mean_well_radius_m'] ** 2
        speed = ph['speed']
        delay = self.op['protocol'].get('aspirate_delay_s', 0.0) if self.op else 0.0
        dist = TRAVEL[self.op['protocol']['travel_rate']][1]
        rng = self._rng(f'asp:{rec["record_id"]}:{self.op["i"]}')
        removed = 0.0
        tip_c = []
        for i in range(96):
            V = p['V'][i]
            if V <= 0:
                continue
            gap = ph['height'] - p['wbe'][i]
            if gap < -0.1e-3:
                if 'tubes_touch_well_bottom' not in rec['warnings']:
                    rec['warnings'].append('tubes_touch_well_bottom')
            if i in self.clog_a:
                continue
            rho, mu, sig = mixture_props(p['comp'][i], self._T())
            res = residual_volume(V, gap, pt, rho, mu, sig, speed, delay) * float(np.exp(rng.normal(0.0, RESIDUAL_SCATTER)))
            res = min(V, res)
            u, Lv = p['ufrac'][i], min(p['layer_V'][i], V)
            # the unmixed layer (volume Lv, the fraction u of the species) lies at the bottom and is left first; the mixed fraction 1 - u is
            # spread uniformly over the whole volume V, so the residual res keeps (1 - u) res / V of it
            if res <= Lv:
                f = u * res / max(Lv, 1e-18) + (1 - u) * res / V
            else:
                f = u + (1 - u) * res / V
            conc = (p['unbound'][i] + p['contamination'][i]) / V
            tip_c.append(conc)
            p['unbound'][i] *= f
            p['contamination'][i] *= f
            p['ufrac'][i], p['layer_V'][i] = 0.0, 0.0
            removed += V - res
            p['V'][i] = res
            if p['cells'][i] > 0:                                           # aspiration shear / scraping near the cell layer
                scrape = max(0.0, 1 - max(gap, 0.0) / CELL_LAYER_CLEAR_M) * 0.25 * dist
                suction = 0.02 * dist * (speed / 4e-3)
                strong = 0.25 if p['adhesion'] == 'strong' else 1.0
                p['cells'][i] *= math.exp(-(scrape + suction) * strong)
        self._aspirated(rec, removed)
        self.op['tip_conc'] = float(np.mean(tip_c)) if tip_c else 0.0
        if ph.get('final') and ph['name'] == 'aspirate':
            rec['cycles_done'] = max(rec['cycles_done'], self.op['protocol']['cycles'])
        return True

    def _do_dispense(self, ph, rec, count=True):
        p = self.plates[self.on_carrier]
        pt = plate_lib()['plates'][p['type']]
        rate = ph['rate']
        rng = self._rng(f'disp:{rec["record_id"]}:{self.op["i"]}')
        cv = math.sqrt(CV_REPEAT ** 2 + (CV_ABS_M3 / ph['volume']) ** 2)
        want = np.array([max(0.0, ph['volume'] * self.clog_d.get(i, 1.0) * (1 + self._tube_bias[i] + rng.normal(0.0, cv))) for i in range(96)])
        self._air = False
        comp, got = self._draw(ph['buffer'], float(want.sum()), rec)       # the bottle gives what the tubes actually deliver (C1)
        if comp is None:
            return False
        air = self._air
        xyf = max(0.0, 1.0 - ph.get('xy_m', 0.0) / pt['mean_well_radius_m']) ** 2   # a jet aimed at the well wall shears the bottom less (p.53)
        vols = want * (got / want.sum()) if want.sum() > 0 else want       # a bottle running dry shares its rest over the tubes
        rho, mu, sig = mixture_props(comp, self._T())
        m_jet = 1 - (1 - MIX_MIN) * math.exp(-jet_speed(rate) / MIX_JET_M_S)
        A = math.pi * pt['mean_well_radius_m'] ** 2
        top = pt['height_m']
        tip = ph['height']
        vac = self._vacuum_ok() and ph['vac_delay'] < ph['volume']
        bulge = A * MENISCUS_M * 0.5
        spilled, lost = np.zeros(96), np.zeros(96)
        skimmed = 0.0
        for i in range(96):
            v = float(vols[i])
            V0 = p['V'][i]
            V1 = V0 + v
            # the residual mixes into the fresh buffer with the jet efficiency; the unmixed rest stays as a layer at the bottom
            if v > 0:   # the jet entrains the old liquid V0 into the fresh v; the part left unmixed (1 - m_jet) V0 / (V0 + v) stays as a bottom layer
                p['ufrac'][i] = (1 - m_jet) * V0 / (V0 + v)
                p['layer_V'][i] = V0
            # with the vacuum on, this well's aspirate tip skims what rises above it (tip below the rim: the level stops at the tip; tip up to
            # 1.5 mm above the rim: at the pinned meniscus); a blocked aspirate tube does not skim and its well runs over the rim (S1, S2)
            skim_here = vac and i not in self.clog_a and tip <= top + MENISCUS_M
            if not skim_here:
                v_cap = pt['well_volume_m3'] + bulge
            elif tip < top:
                v_cap = min(pt['well_volume_m3'], A * max(tip - p['wbe'][i], 0.0))
            else:
                v_cap = pt['well_volume_m3'] + bulge * (tip - top) / MENISCUS_M
            if V1 > v_cap:
                excess = V1 - v_cap
                if skim_here:
                    skimmed += excess
                else:
                    spilled[i] = excess
                u = p['ufrac'][i]                                           # the liquid leaving at the top carries the mixed fraction only,
                tot = u + (1 - u) * math.exp(-excess / v_cap)               # washed out continuously while the jet runs (stirred-tank overflow)
                if not skim_here:
                    lost[i] = (p['unbound'][i] + p['contamination'][i]) * (1 - tot)
                p['unbound'][i] *= tot
                p['contamination'][i] *= tot
                p['ufrac'][i] = u / tot if tot > 0 else 0.0
                p['layer_V'][i] = min(p['layer_V'][i], v_cap)
                V1 = v_cap
            p['V'][i] = V1
            p['comp'][i] = self._mix_comp(p['comp'][i], V0, comp, v)
            if p['cells'][i] > 0:
                tau = wall_shear(rate, V0 / A, rho) * xyf
                strong = 4.0 if p['adhesion'] == 'strong' else 1.0
                p['cells'][i] *= math.exp(-(tau / (TAU_CELL_PA * strong)) ** 2)
        if spilled.any():
            self._spill(p, spilled, rec, lost)
        if skimmed > 0:
            self._aspirated(rec, skimmed)
        self.led['dispensed_m3'].add(got)
        rec['_dispensed_m3'] += got
        rec['programmed_dispense_m3'] += 96 * ph['volume']
        full = got >= float(want.sum()) * (1 - 1e-12)
        self.flow_ok = bool(got >= 0.9 * 96 * ph['volume'])
        if not self.flow_ok and (full or air) and self.washer['sensor_flow']:
            self._alarm(rec, 'FLOW_ERROR')
        if got > 0 or not air:
            self.tip_liquid = comp
            self._reset_idle()
        self._desorb(p, contact=ph.get('duration', 3.0), surf=is_surfactant(comp))
        if count and full:
            rec['cycles_done'] = max(rec['cycles_done'], ph.get('cycle', 0) + 1)
            self.led['cycles'].add(1)
        return full or air

    def _do_bottom_wash(self, ph, rec):
        """Bottom wash (guide p.45): fluid dispensed and aspirated at the same time at the aspirate height - a flow-through of the volume v through the
        liquid held below the tips, n <- n exp(-m v / V_hold), with the jet close to the bottom (vigorous: cell layers suffer)."""
        if not self._vacuum_ok():
            self._alarm(rec, 'VACUUM_LOW')
            return False
        p = self.plates[self.on_carrier]
        pt = plate_lib()['plates'][p['type']]
        A = math.pi * pt['mean_well_radius_m'] ** 2
        want = np.array([ph['volume'] * self.clog_d.get(i, 1.0) for i in range(96)])
        self._air = False
        comp, got = self._draw(ph['buffer'], float(want.sum()), rec)
        if comp is None:
            return False
        air = self._air
        vols = want * (got / want.sum()) if want.sum() > 0 else want
        rho, mu, sig = mixture_props(comp, self._T())
        m_jet = 1 - (1 - MIX_MIN) * math.exp(-jet_speed(ph['rate']) / MIX_JET_M_S)
        cap = pt['well_volume_m3'] + A * MENISCUS_M * 0.5
        removed = 0.0
        spilled, lost = np.zeros(96), np.zeros(96)
        for i in range(96):
            v = float(vols[i])
            hold = max(A * max(ph['height'] - p['wbe'][i], 0.0) + A * DELTA0_M, 1e-12)
            if i in self.clog_a:                                         # no aspiration: the well fills and runs over the rim if needed
                V0 = p['V'][i]
                p['comp'][i] = self._mix_comp(p['comp'][i], V0, comp, v)
                if V0 + v > cap:
                    spilled[i] = V0 + v - cap
                    keep = cap / (V0 + v)
                    lost[i] = (p['unbound'][i] + p['contamination'][i]) * (1 - keep)
                    p['unbound'][i] *= keep
                    p['contamination'][i] *= keep
                p['V'][i] = min(V0 + v, cap)
                continue
            f = math.exp(-m_jet * v / hold)                               # flow-through; what leaves through the tip goes to waste (C2)
            p['unbound'][i] *= f
            p['contamination'][i] *= f
            V_new = min(p['V'][i], hold)
            removed += p['V'][i] + v - V_new
            p['V'][i] = V_new
            p['comp'][i] = dict(comp)
            if p['cells'][i] > 0:
                strong = 4.0 if p['adhesion'] == 'strong' else 1.0
                p['cells'][i] *= math.exp(-(wall_shear(ph['rate'], hold / A, rho) / (TAU_CELL_PA * strong)) ** 2)
        if spilled.any():
            self._spill(p, spilled, rec, lost)
        rec['_dispensed_m3'] += got
        rec['programmed_dispense_m3'] += 96 * ph['volume']
        self.led['dispensed_m3'].add(got)
        self._aspirated(rec, removed)
        if got > 0 or not air:
            self.tip_liquid = comp
            self._reset_idle()
        return got >= float(want.sum()) * (1 - 1e-12) or air

    @staticmethod
    def _mix_comp(c0, V0, c1, v):
        tot = V0 + v
        if tot <= 0:
            return dict(c1)
        out = {k: x * V0 / tot for k, x in c0.items()}
        for k, x in c1.items():
            out[k] = out.get(k, 0.0) + x * v / tot
        return {k: x for k, x in out.items() if x > 1e-9}

    def _spill(self, p, spilled, rec, lost):
        """Liquid above the rim runs over into the four neighbouring wells (and the carrier): 10 % of the soluble species that left each well over
        the rim (lost, mol) reach its neighbours (the same rule for the dispense, bottom-wash and drip overflows)."""
        if rec is not None and 'overflow_observed' not in rec['warnings']:   # no overflow sensor: a visible observation, not an instrument alarm
            rec['warnings'].append('overflow_observed')
        self._event('plate', 'OVERFLOW', '孔内液体溢出（可见，仪器无传感器）', rid=rec['record_id'] if rec is not None else None)
        total = float(spilled.sum())
        self.led['overflow_m3'].add(total)
        for i in np.where(spilled > 0)[0]:
            c, r = divmod(int(i), 8)
            nb = [8 * cc + rr for cc, rr in ((c - 1, r), (c + 1, r), (c, r - 1), (c, r + 1)) if 0 <= cc < 12 and 0 <= rr < 8]
            for j in nb:
                p['contamination'][j] += 0.1 * lost[i] / len(nb)

    def _desorb(self, p, contact, surf):
        k_ns = K_OFF_NS_SURF if surf else K_OFF_NS
        d_ns = p['ns'] * (1 - math.exp(-k_ns * contact))
        d_sp = p['spec'] * (1 - math.exp(-K_OFF_SPEC * contact))
        p['ns'] -= d_ns
        p['spec'] -= d_sp
        before = p['unbound'] + p['contamination']
        p['unbound'] += d_ns + d_sp
        after = p['unbound'] + p['contamination']
        p['ufrac'] = np.where(after > 0, p['ufrac'] * before / np.maximum(after, 1e-300), 0.0)   # desorbed species enter the bulk

    def _mix(self, t, tau, shake):
        """Soak / shake: mixing of the residual layer with the bulk (shake), desorption of adsorbed species (both); the jet mixing deficit of the
        last dispense decays with the shake time constant."""
        p = self.plates.get(self.on_carrier)
        if p is None:
            return
        p['ufrac'] *= math.exp(-t / (tau if shake else SOAK_TAU_S))
        self._desorb(p, contact=t, surf=is_surfactant(self.tip_liquid))
        if shake and (p['cells'] > 0).any():
            strong = 4.0 if p['adhesion'] == 'strong' else 1.0
            p['cells'] *= math.exp(-t / (3600.0 * strong) * (3.0 / tau))

    def _complete(self):
        o = self.op
        rec = self.records[o['record_id']]
        rec['status'] = 'completed'
        rec['finished_s'] = self.t
        self.operations[o['op']]['phase'] = 'completed'
        self.phase = None
        self.carrier, self.manifold = 0.0, 0.0
        self._event('program', 'RUN_DONE', f'{rec["kind"]}记录{rec["record_id"]}完成', rid=rec['record_id'])
        self.op = None
        self._after_op(o, 'completed')

    def _fail_op(self, code):
        o = self.op
        if o is None:
            return
        rec = self.records[o['record_id']]
        if code not in rec['alarms']:
            rec['alarms'].append(code)
        rec['status'] = 'failed'
        rec['finished_s'] = self.t
        self.operations[o['op']]['phase'] = 'failed'
        self.phase = None
        self.op = None
        if self.power:                                                     # the instrument homes both axes after an error (booked travel)
            self.led['carrier_travel_m'].add(abs(self.carrier))
            self.led['manifold_travel_m'].add(abs(self.manifold))
            self.carrier, self.manifold = 0.0, 0.0
        self._after_op(o, 'failed', code)

    def _cancel_op(self, op):
        o, P = self.op, self.proto
        if P is not None and P['status'] in ('running', 'waiting', 'prompt') and self.operations.get(P['op']) is op:   # a protocol run: its step stops too
            if o is not None and o.get('proto') == P['op']:
                self._cancel_step(o)
            self._proto_end('cancelled', None)
            return
        if o is None or self.operations.get(o['op']) is not op:
            raise ActionRejected('NOT_RUNNING')
        self._cancel_step(o)
        self._after_op(o, 'cancelled')

    def _cancel_step(self, o):
        rec = self.records[o['record_id']]
        rec['status'] = 'cancelled'
        rec['finished_s'] = self.t
        self.operations[o['op']]['phase'] = 'cancelled'
        self._event('program', 'CANCELLED', '运行取消（洗头停放、载板回初始位）', rid=rec['record_id'])
        self.led['carrier_travel_m'].add(abs(self.carrier))
        self.led['manifold_travel_m'].add(abs(self.manifold))
        self.carrier, self.manifold = 0.0, 0.0
        self.phase = None
        self.op = None

    # ---------------------------------------------------------------- queries, faults, time
    def _cmd_log_read(self):
        return dict(entries=[dict(record_id=r['record_id'], kind=r['kind'], status=r['status'], cycles_done=r['cycles_done'], alarms=list(r['alarms']),
                                  finished_s=r['finished_s']) for r in self.records.values()])

    @staticmethod
    def _out(r):
        """The published record: settings, cycles, programmed dispense volume, alarms, warnings - the washer does not measure the volumes it actually
        delivered or aspirated (those stay private and in the evaluator ledger)."""
        return {k: copy.deepcopy(v) for k, v in r.items() if not k.startswith('_')}

    def _cmd_measurement_result(self, record_id):
        r = self.records.get(record_id)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._out(r)

    def _cmd_records_list(self):
        return dict(records=sorted(self.records))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    def _cmd_plate_inspect(self, plate_id):
        p = self.plates.get(plate_id)
        if p is None:
            raise ActionRejected('UNKNOWN_PLATE')
        return dict(plate_id=plate_id, volume_m3=[float(v) for v in p['V']], unbound_mol=[float(v) for v in p['unbound']], nonspecific_mol=[float(v) for v in p['ns']],
                    specific_mol=[float(v) for v in p['spec']], cells=[float(v) for v in p['cells']], contamination_mol=[float(v) for v in p['contamination']])

    def _cmd_provenance_read(self):
        return dict(libraries=[dict(path=str(q.relative_to(ROOT)), sha256=hashlib.sha256(q.read_bytes()).hexdigest(), count=json.loads(q.read_text())['count'])
                               for q in (LIQUID_LIB_PATH, PLATE_LIB_PATH)])

    def _cmd_fault_inject(self, fault, tube, active):
        self._need_plant()
        if fault in ('dispense_tube_clog', 'aspirate_tube_clog'):
            if tube is None:
                raise ActionRejected('INVALID_ARGUMENT')
            if fault == 'dispense_tube_clog':
                (self.clog_d.__setitem__(tube, 0.0) if active else self.clog_d.pop(tube, None))
            else:
                (self.clog_a.add(tube) if active else self.clog_a.discard(tube))
        elif fault == 'power_loss':
            if active and self.power:
                self._power_off('POWER_LOST', '断电（故障注入）')
        else:
            (self.faults.add if active else self.faults.discard)(fault)
            if active and fault == 'vacuum_leak' and self.op is not None and self.phase in ('aspirate', 'aspirate_secondary', 'bottom_wash', 'prime'):
                self._alarm(self.records[self.op['record_id']], 'VACUUM_LOW')
        self._event('fault', 'FAULT_' + ('ON' if active else 'OFF'), f'{fault} {tube if tube is not None else ""}')
        return dict(faults=sorted(self.faults | {f'dispense_clog_{i}' for i in self.clog_d} | {f'aspirate_clog_{i}' for i in self.clog_a})[:8])

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)


# ---------------------------------------------------------------- sessions (instance, identity, idempotent requests) - as CHEM-162
ASYNC = {'prime.run', 'wash.run', 'maintenance.clean_tubes', 'protocol.run', 'protocol.test_run'}
_DATA_GUARDED = {'measurement.result'}
_RECORD_ARG = {'measurement.result': 'record_id'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] == 'device_command' and k not in UNSUPPORTED
                    and k not in ('power.set', 'power.press_switch')}       # lead 10-19: the rear power switch (hard switch) and the hand / robot
# skills (bottles, waste, plates, tube cleaning) are physical acts: not owner-guarded; the device's own rules (no plate move, waste emptying or
# tube cleaning during a run, no change of the bottle in use) still refuse them with the device's reason


class Biotek405tsSession:
    def __init__(self, host, principal_id):
        self._host = host
        self._principal = principal_id

    @property
    def _role(self):
        return self._host._principals[self._principal]

    def _target(self, instance_id):
        if not isinstance(instance_id, str) or instance_id != self._host.instance_id:
            raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:
            self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID:
            raise ActionRejected('UNKNOWN_INSTANCE')
        meta = copy.deepcopy(export_biotek_405ts_contract_metadata())
        meta['capabilities'].update(export_device_contract_metadata())
        meta['capabilities'].update(export_panel_contract_metadata())
        caps = {k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'}
        slots = {k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'}
        if self._role == 'agent':
            for c in caps.values():
                c['state_effects'] = [e for e in c['state_effects'] if e['state_id'] in slots]
                c['observes'] = [x for x in c['observes'] if x in slots]
                for pre in c['preconditions']:
                    pre['state_ids'] = [x for x in pre['state_ids'] if x in slots]
                c['execution']['completion_state_ids'] = [x for x in c['execution']['completion_state_ids'] if x in slots]
            for m in meta['mechanisms'].values():
                m['state_ids'] = [x for x in m['state_ids'] if x in slots]
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION, capabilities=caps, state_slots=slots)

    def _foreign(self, object_id):
        owner = self._host._owners.get(object_id)
        return owner is not None and owner != self._principal and self._host._principals.get(owner) == 'agent'

    def _foreign_op(self):
        h = self._host
        return (h.op is not None and self._foreign(h.op['op'])) or (h._proto_active() and self._foreign(h.proto['op']))

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['operation.started_by_other_agent'] = self._role == 'agent' and self._foreign_op()
        if self._role == 'agent':
            values['result.last_record_id'] = h._last_record(lambda k: not self._foreign(k))
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if (capability_id in _CONTROL_GUARDED or capability_id in _DEVICE_GUARDED) and self._foreign_op():
            raise ActionRejected('ACCESS_DENIED')                            # (the power switch is not guarded: a power loss ends the run)
        if capability_id in _DATA_GUARDED and self._foreign(arguments.get(_RECORD_ARG[capability_id])):
            raise ActionRejected('ACCESS_DENIED')

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        h = self._host
        key = digest = None
        try:
            self._target(instance_id)
            if not isinstance(request_id, str) or not request_id:
                raise ActionRejected('INVALID_ARGUMENT')
            key = (self._principal, request_id)
            try:
                digest = json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (TypeError, ValueError):
                digest = None
            if key in h._requests:
                old, response = h._requests[key]
                if digest is None or old != digest or not isinstance(response, dict):
                    raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(response)
            if not isinstance(capability_id, str) or capability_id not in ALL_CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and ALL_CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(ALL_CAPS[capability_id]['input_schema'], arguments)
            self._guard(capability_id, arguments)
            h._caller = self._principal                                       # panel gestures in this call act as this principal
            try:
                result = h._invoke_any(capability_id, **arguments)
            finally:
                h._caller = None
            if self._role == 'agent':
                if capability_id == 'records.list':
                    result = dict(records=[r for r in result['records'] if not self._foreign(r)])
                elif capability_id == 'log.read':
                    result = dict(entries=[e for e in result['entries'] if not self._foreign(e['record_id'])])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid')) and e['kind'] != 'fault'], dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
                rid = h.operations[operation].get('record_id')
                if rid:
                    h._owners[rid] = self._principal
                h._adopt(operation)
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=copy.deepcopy(result), observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            return response
        except ActionRejected as exc:
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None,
                            status='unsupported' if str(exc) == 'UNSUPPORTED' else 'rejected', state_revision=h.revision,
                            clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                            error=dict(code=str(exc), message=ERROR_ZH.get(str(exc), str(exc)), retryable=False, side_effects='none'))
            if key is not None and digest is not None and key not in h._requests:
                h._requests[key] = (digest, copy.deepcopy(response))
            return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        raise ActionRejected('ACCESS_DENIED')

    def poll(self, operation_id):
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        op = h.operations.get(operation_id)
        if op is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        res = h._out(h.records[op['record_id']]) if op.get('record_id') in h.records else dict(phase=op['phase'])
        return dict(operation_id=operation_id, status='succeeded' if op['phase'] == 'completed' else op['phase'], result=res)

    def cancel(self, operation_id, request_id):
        h = self._host
        if not isinstance(request_id, str) or not request_id:
            raise ActionRejected('INVALID_ARGUMENT')
        key = (self._principal, request_id)
        digest = json.dumps(['cancel', operation_id]) if isinstance(operation_id, str) else None
        if key in h._requests:
            old, response = h._requests[key]
            if digest is None or old != digest:
                raise ActionRejected('REQUEST_CONFLICT')
            if isinstance(response, str):
                raise ActionRejected(response)
            return copy.deepcopy(response)
        try:
            self.poll(operation_id)
            op = h.operations[operation_id]
            if op['phase'] != 'running':
                raise ActionRejected('NOT_RUNNING')
            h._cancel_op(op)
        except ActionRejected as exc:
            if digest is not None:
                h._requests[key] = (digest, str(exc))
            raise
        h.revision += 1
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=dict(phase=op['phase']), observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response


ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值槽、另一个agent的记录或操作）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '电源电压超出100–240 VAC ±10 %',
    'POWER_OFF': '仪器未通电', 'OPERATION_RUNNING': '有运行中的灌注/洗板/清洁', 'UNKNOWN_LIQUID': '洗液模板库中没有该液体',
    'PLATE_EXISTS': '板ID已存在', 'TOO_MANY_PLATES': '板数量超过上限（20）', 'UNKNOWN_PLATE_TYPE': '板模板库中没有该板型',
    'PLATE_INCOMPATIBLE': '单96管洗头只能处理96孔板（384孔板需双洗头或192管洗头）', 'WELL_OVERFILLED': '初始体积超过孔体积', 'UNKNOWN_PLATE': '板不存在',
    'PLATE_NOT_ON_BENCH': '板不在实验台上', 'CARRIER_OCCUPIED': '载板上已有板', 'CARRIER_NOT_HOME': '载板不在初始位或洗头未停放',
    'PLATE_NOT_ON_CARRIER': '该板不在载板上', 'PLATE_ON_CARRIER': '灌注前请取下载板上的板（灌注槽在载板下方）', 'NO_PLATE': '载板上没有板',
    'WASTE_FULL': '废液瓶满（液位传感器）', 'BUFFER_EMPTY': '所选供液瓶无液（液体传感器）', 'VACUUM_LOW': '真空不足（泵未接或泄漏）',
    'CELL_WASH_OPTION_ABSENT': '细胞洗涤流速1–2与CW行进速度需要细胞洗涤选件（低流量管路）', 'VOLUME_NOT_IN_1UL_STEPS': '分配体积须为1 µL的整数倍',
    'RECORDS_FULL': '记录数已达上限（200）', 'UNKNOWN_RECORD': '记录不存在', 'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作已结束',
}
_PLATE = ['plate.on_carrier', 'plate.bench_ids', 'truth.residual_m3']
_OPS = ['operation.kind', 'operation.progress', 'program.phase', 'display.cycles_remaining', 'display.time_remaining_s']
_MOVE = ['carrier.x_m', 'carrier.at_home', 'manifold.z_m', 'manifold.tip_height_m']
_FLUID = ['buffer.selected', 'sensor.fluid_ok', 'truth.line_composition'] + [f'buffer.{b.lower()}.volume_m3' for b in BOTTLES]
_VAC = ['sensor.vacuum_ok']
EFFECTS = {
    'plant.configure': ['plant.configured', 'power.enabled', 'sensor.vacuum_ok', 'sensor.waste_full'] + _OPS,
    'power.set': ['power.enabled', 'sensor.vacuum_ok', 'display.cycles_remaining', 'display.time_remaining_s'] + _OPS + _MOVE,
    'power.press_switch': ['power.enabled', 'sensor.vacuum_ok', 'display.cycles_remaining', 'display.time_remaining_s'] + _OPS + _MOVE,
    'buffer.load': [f'buffer.{b.lower()}.liquid' for b in BOTTLES] + [f'buffer.{b.lower()}.volume_m3' for b in BOTTLES] + ['sensor.fluid_ok'],
    'waste.empty': ['sensor.waste_full', 'truth.waste_volume_m3'], 'plate.provide': ['plate.bench_ids'], 'plate.load': _PLATE, 'plate.unload': _PLATE,
    'prime.run': _OPS + _MOVE + ['buffer.selected'], 'wash.run': _OPS + _MOVE + ['buffer.selected'], 'maintenance.clean_tubes': _OPS + ['program.phase'],
    'fault.inject': ['truth.faults', 'truth.clogged_dispense', 'truth.clogged_aspirate', 'sensor.vacuum_ok', 'sensor.waste_full', 'sensor.fluid_ok', 'power.enabled',
                     'display.cycles_remaining', 'display.time_remaining_s', 'result.last_record_id'] + _OPS + _MOVE,
    'sim.step': ['clock.time_s', 'truth.idle_s'],
}
_RUN_STEP = _OPS + _MOVE + _FLUID + _VAC + ['result.last_record_id', 'sensor.waste_full', 'sensor.flow_ok', 'truth.waste_volume_m3', 'truth.residual_m3',
                                           'truth.clogged_dispense', 'truth.clogged_aspirate', 'truth.idle_s', 'truth.line_composition']
ON_STEP = {'prime.run': _RUN_STEP, 'wash.run': _RUN_STEP, 'maintenance.clean_tubes': _OPS + ['result.last_record_id', 'truth.clogged_dispense', 'truth.clogged_aspirate',
                                                                                           'truth.idle_s'],
           'fault.inject': _RUN_STEP, 'plant.configure': _RUN_STEP, 'power.set': _RUN_STEP, 'power.press_switch': _RUN_STEP,   # stopping a run stops its later phases
           'sim.step': _RUN_STEP}
ON_STEP_ZH = {'prime.run': '按阶段推进：切阀、分配到灌注槽（管路组成更新、废液增加）、停放', 'wash.run': '按阶段推进：载板移动、抽吸、分配、浸泡、摇动、最终抽吸、回初始位',
              'maintenance.clean_tubes': '10 min后解除全部堵针', 'fault.inject': '断电或真空泄漏使运行失败（真空泄漏时洗头停放、载板回初始位，断电时停在原处），之后的阶段（抽吸、分配、废液、管路组成、显示）不再推进', 'plant.configure': '电源电压超范围断电时进行中的运行失败，之后的阶段不再推进', 'power.set': '关机使进行中的运行失败，之后的阶段不再推进', 'power.press_switch': '关机使进行中的运行失败，之后的阶段不再推进', 'sim.step': '时钟推进；进行中的操作按阶段完成；空闲时管路干涸'}
PRECONDITIONS = {
    'plant.configure': ('噪声种子只能在首次声明时设定；运行中不能改真空泵与细胞洗涤选件', ['plant.configured', 'operation.kind']),
    'power.set': ('已声明实验室条件；开机时电源电压在100–240 VAC ±10 %', ['plant.configured', 'power.enabled']),
    'power.press_switch': ('已声明实验室条件；断电时按下为开机（电压须在范围内），通电时按下为关机（进行中的运行失败）', ['plant.configured', 'power.enabled']),
    'buffer.load': ('已声明实验室条件；液体在洗液模板库中；不能更换运行中正在用的瓶', ['plant.configured', 'operation.kind', 'buffer.selected']),
    'waste.empty': ('已声明实验室条件；无运行', ['plant.configured', 'operation.kind']),
    'plate.provide': ('已声明实验室条件；板型在模板库中且为96孔；初始体积不超过孔体积', ['plant.configured', 'plate.bench_ids']),
    'plate.load': ('板在实验台上、载板空、载板在初始位且洗头停放、无运行', ['plate.bench_ids', 'plate.on_carrier', 'carrier.at_home', 'operation.kind']),
    'plate.unload': ('板在载板上、载板在初始位、无运行', ['plate.on_carrier', 'carrier.at_home', 'operation.kind']),
    'prime.run': ('通电、无运行、载板空且在初始位（灌注槽露出）、所选瓶有液、废液未满、真空正常', ['power.enabled', 'operation.kind', 'plate.on_carrier', 'carrier.at_home',
                                                                                  'sensor.fluid_ok', 'sensor.waste_full', 'sensor.vacuum_ok']),
    'wash.run': ('通电、无运行、载板上有板且在初始位、所选瓶有液、废液未满、真空正常；细胞洗涤档位需选件；体积为1 µL整数倍，真空延迟不大于分配体积',
                 ['power.enabled', 'operation.kind', 'plate.on_carrier', 'carrier.at_home', 'sensor.fluid_ok', 'sensor.waste_full', 'sensor.vacuum_ok']),
    'maintenance.clean_tubes': ('已声明实验室条件；无运行', ['plant.configured', 'operation.kind']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_IDLE = ['PLANT_REQUIRED', 'OPERATION_RUNNING']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'OPERATION_RUNNING'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'buffer.load': ['PLANT_REQUIRED', 'UNKNOWN_LIQUID', 'OPERATION_RUNNING'], 'waste.empty': ['PLANT_REQUIRED', 'OPERATION_RUNNING'],
    'plate.provide': ['PLANT_REQUIRED', 'PLATE_EXISTS', 'TOO_MANY_PLATES', 'UNKNOWN_PLATE_TYPE', 'PLATE_INCOMPATIBLE', 'UNKNOWN_LIQUID', 'WELL_OVERFILLED'],
    'plate.load': _IDLE + ['UNKNOWN_PLATE', 'PLATE_NOT_ON_BENCH', 'CARRIER_OCCUPIED', 'CARRIER_NOT_HOME'], 'plate.unload': _IDLE + ['PLATE_NOT_ON_CARRIER', 'CARRIER_NOT_HOME'],
    'prime.run': _IDLE + ['POWER_OFF', 'CARRIER_NOT_HOME', 'PLATE_ON_CARRIER', 'WASTE_FULL', 'BUFFER_EMPTY', 'VACUUM_LOW', 'RECORDS_FULL'],
    'wash.run': _IDLE + ['POWER_OFF', 'NO_PLATE', 'CARRIER_NOT_HOME', 'CELL_WASH_OPTION_ABSENT', 'VOLUME_NOT_IN_1UL_STEPS', 'WASTE_FULL', 'BUFFER_EMPTY', 'VACUUM_LOW', 'RECORDS_FULL'],
    'maintenance.clean_tubes': _IDLE + ['RECORDS_FULL'], 'log.read': [], 'measurement.result': ['UNKNOWN_RECORD'], 'records.list': [], 'events.read': [], 'ledger.read': [],
    'plate.inspect': ['UNKNOWN_PLATE'], 'provenance.read': [], 'fault.inject': ['PLANT_REQUIRED'], 'sim.step': [],
}
_PARTIAL = {'WASTE_FULL', 'BUFFER_EMPTY', 'VACUUM_LOW'}
CAP_ERROR_ZH = {('wash.run', 'WASTE_FULL'): '启动时废液满；或运行中废液满（记录failed，已完成的循环效果保留）',
                ('wash.run', 'BUFFER_EMPTY'): '启动时供液瓶无液；或运行中瓶空（记录failed，已完成的循环效果保留）',
                ('wash.run', 'VACUUM_LOW'): '启动时真空不足；或运行中真空泄漏（记录failed，孔内保留液体）',
                ('prime.run', 'WASTE_FULL'): '废液满（启动或运行中）', ('prime.run', 'BUFFER_EMPTY'): '供液瓶无液（启动或运行中）', ('prime.run', 'VACUUM_LOW'): '真空不足'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件与外部附件，没有可见模型变化', 'buffer.load': '供液瓶是外部附件，场景中没有瓶子几何', 'waste.empty': '废液瓶是外部附件',
    'plate.provide': '只在过程层实验台上增加板；场景没有板几何（载板板位为接口点）', 'plate.load': '板只在过程层（场景载板板位为接口点）', 'plate.unload': '同上',
    'maintenance.clean_tubes': '人工/机器人维护抽象，没有可见模型变化', 'log.read': '只读日志', 'measurement.result': '只读记录', 'records.list': '只读列表',
    'events.read': '只读事件', 'ledger.read': '只读账目（evaluator）', 'plate.inspect': '只读真值（evaluator）', 'provenance.read': '只读来源记录',
    'fault.inject': '评估者故障注入', 'sim.step': '推进时钟',
}
SCENE_MIRRORED = {'carrier.x_m', 'manifold.z_m', 'power.enabled', 'display.cycles_remaining', 'display.time_remaining_s'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节或屏幕数字（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'prime.run': ('操作结束：切阀0.4 s、按流速分配给定体积到灌注槽（96管合计）、停放', '失败时（瓶空、废液满、真空不足、断电）已分配部分计入管路组成与废液。'),
    'wash.run': ('操作结束：按程序逐阶段（载板移动、抽吸、分配、底洗、浸泡、摇动、最终抽吸）完成并回初始位，写wash记录',
                 '取消时洗头停放、载板回初始位；故障时记录failed：真空、废液或供液故障时洗头停放、载板回初始位，断电时洗头与载板都停在原处（再次通电自检后回位）；已完成阶段的孔内效果保留。'),
    'maintenance.clean_tubes': ('操作结束：10 min后解除全部堵针', '取消时堵针保持。'),
}
RESOURCES = {'prime.run': ['manifold', 'buffer_valves', 'vacuum', 'host_clock'], 'wash.run': ['manifold', 'plate_carrier', 'buffer_valves', 'vacuum', 'host_clock'],
             'maintenance.clean_tubes': ['manifold', 'host_clock'], 'plate.load': ['plate_carrier'], 'plate.unload': ['plate_carrier'], 'buffer.load': ['supply_bottles'],
             'waste.empty': ['waste_bottles'], 'power.set': ['mains'], 'power.press_switch': ['mains'], 'plant.configure': ['room', 'mains', 'accessories'],
             'plate.provide': ['bench'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock']}
OBSERVES = {
    'power.set': ['power.enabled'], 'power.press_switch': ['power.enabled'], 'buffer.load': ['sensor.fluid_ok'], 'waste.empty': ['sensor.waste_full'],
    'plate.load': ['plate.on_carrier'], 'plate.unload': ['plate.bench_ids'], 'prime.run': ['operation.kind', 'program.phase', 'result.last_record_id'],
    'wash.run': ['operation.kind', 'program.phase', 'display.cycles_remaining', 'display.time_remaining_s', 'carrier.x_m', 'manifold.z_m', 'result.last_record_id'],
    'maintenance.clean_tubes': ['operation.kind', 'result.last_record_id'],
}
_WB = 'user_workbook'
_GSG = 'manufacturer_getting_started_guide'
_EL = 'manufacturer_el406_manual'
_SLAS = 'ansi_slas_standards'
_ARB = 'arbor_plate_dimensions'
_VDI = 'vdi_ppds'
_LAL = 'laliberte2009'
_SIM = 'simulation_assumption'
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({
    'power.set': [_GSG], 'power.press_switch': [_GSG], 'buffer.load': [_GSG, _VDI, _LAL], 'waste.empty': [_GSG], 'plate.provide': [_ARB, _SLAS, _WB, _SIM],
    'plate.load': [_GSG, _SLAS], 'plate.unload': [_GSG], 'prime.run': [_GSG, _SIM], 'wash.run': [_WB, _GSG, _EL, _VDI, _LAL, _SLAS, _ARB, _SIM],
    'maintenance.clean_tubes': [_GSG, _SIM], 'provenance.read': [_VDI, _LAL, _ARB]})
CAP_SOURCES.update({k: [_GSG] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温（洗液物性按此取值）', 'mains_voltage_v': '供电电压', 'noise_seed': '噪声随机数种子（只在首次声明时生效）',
            'vacuum_pump': '真空泵接在附件插座上', 'cell_wash_installed': '细胞洗涤选件（低流量管路，流速1–2与CW行进速度）', 'waste_capacity_m3': '废液瓶总容量'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.washer_405ts.limitations。'
CAP_LIMITS = {
    'wash.run': ['残余体积按孔底液层（g²/(g+g0)）、孔底液膜、底角液环与Landau–Levich壁面液膜计算，参数按手册两点标定（默认29步≤2 µL；50步约100 µL，模型约88 µL：圆柱孔在该高度的液层上限约90 µL）；溢流无传感器，只作可见警告；'
                 '流速档位对应的每孔流量、行进速度（2、3档为EL406手册值，其余为工程值）、分配CV分解、射流混合与剪切、解吸速率为工程值；'
                 '细胞脱落、堵针、滴落与溢流污染为工程模型，未经实验标定。',
                 '洗头X/Y偏移、扇区洗、384孔、双洗头与底部抽滤（Vac）未实现；真空泵抽速与废液管路压降未建模（真空只有好/坏两态）。'],
    'prime.run': ['管路死体积110 mL按两级串联全混釜处理（工程模型；300 mL灌注后纯度约97 %，与厂家“≥95 %”“三倍死体积保证纯度”一致）；低流量管路（细胞洗涤选件）的单独灌注未区分。'],
    'plate.provide': ['板内“游离物/非特异/特异结合物”为通用ELISA代理量（mol），不对应某一具体抗体或底物；细胞以每孔数量与黏附强度（弱/强）表示。'],
    'buffer.load': ['洗液物性取洗液模板库：纯液体为VDI PPDS关联式，缓冲液密度/黏度为Laliberté (2009)模型，表面张力对稀盐溶液取水、对吐温洗液取36 mN/m（假设）；'
                    '有机溶剂与管路材料的化学相容性未检查。'],
    'maintenance.clean_tubes': ['用清洁针逐根疏通为人工/机器人维护抽象（10 min）；超声自动清洗为选件（不支持）。'],
}
LIMITATIONS = [
    '选定配置：405 TS单96管洗头（只处理96孔板）、内置四路缓冲切换（A–D）、标准载板；外部真空泵、供液瓶与废液瓶为附件（场景不建）；'
    '细胞洗涤低流量管路为选件（plant.cell_wash_installed，默认未装）；真空过滤、磁珠分离、Verify堵针检测、超声清洗与BioStack不支持。',
    '孔几何取板模板库（SBS/ANSI-SLAS标准与经销商板尺寸表；孔底高度为推导值），孔底高度在板内按SLAS-6变化量随机分布。',
    '板、液体与细胞在场景中没有几何；放板、加液与倒废液是宿主/机器人抽象，没有接触验证。',
    '没有真实405 TS与LHC软件的硬件接口。',
]


def _sampling_text(k):
    if k in ('clock.time_s', 'operation.progress', 'display.time_remaining_s', 'truth.idle_s'):
        return 'Instantaneous value at the read time; operation phases complete at their scheduled times inside step().'
    return 'Changes when a command is accepted or at a phase event of an operation; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的操作进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def export_biotek_405ts_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'Biotek405tsSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.biotek_405ts.Biotek405tsSession.invoke',
                                              invocation=f'session.invoke(instance_id="washer_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='Biotek405tsRuntime.invoke / Biotek405tsSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量；运行阶段只随step推进')
                                for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询',
                                completion_state_ids=['operation.kind', 'result.last_record_id'] if async_ else [],
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'volume_range_m3': (list(VOLUME_RANGE_M3), 'm3', '96管洗头分配体积50–3000 µL/孔（厂家；调研表已核验）', [_WB, _GSG]),
            'volume_step_m3': (VOLUME_STEP_M3, 'm3', '体积步进1 µL（调研表）', [_WB]), 'cycles_range': (list(CYCLES_RANGE), '1', '清洗1–250循环（调研表）', [_WB]),
            'residual_spec_m3': (RESIDUAL_SPEC_M3, 'm3', '三循环后平均残余≤2 µL/孔（300 µL水+0.1 %吐温-20，Costar平底板，厂家）', [_GSG, _WB]),
            'cv_spec': (CV_SPEC, '1', '300 µL/孔分配精密度≤3 % CV（厂家）', [_GSG, _WB]), 'z_step_m': (Z_STEP_M, 'm', '洗头Z步长0.127 mm（EL406手册；50步≈6.35 mm）', [_EL, _GSG]),
            'aspirate_z_default': (ASP_Z_DEFAULT, '1', '96孔板默认抽吸高度29步（厂家）', [_GSG]), 'dispense_z_default': (DISP_Z_DEFAULT, '1', '96孔板默认分配高度121步（厂家）', [_GSG]),
            'aspirate_delay_max_s': (ASP_DELAY_MAX_S, 's', '抽吸延时0–5000 ms（厂家）', [_GSG]), 'vacuum_delay_range_m3': (list(VAC_DELAY_M3), 'm3', '真空延迟10–350 µL/孔（厂家）', [_GSG]),
            'shake_soak_max_s': (SHAKE_SOAK_MAX_S, 's', '摇动/浸泡1 s–1 h（厂家）', [_GSG]), 'home_after_s': (HOME_AFTER_S, 's', '浸泡+摇动超过1 min载板回初始位、真空泵关（厂家）', [_GSG]),
            'dead_volume_m3': (DEAD_VOLUME_M3, 'm3', '死体积108 mL＋缓冲切换管路2 mL（厂家）', [_GSG]), 'prime_for_change_m3': (PRIME_FOR_CHANGE_M3, 'm3', '换液推荐灌注300 mL（纯度≥95 %，厂家）', [_GSG]),
            'travel_speed_m_s': ({k: v for k, v in TRAVEL_SPEED.items()}, 'm/s', '抽吸行进速度：2档3.4 mm/s、3档4.0 mm/s（EL406手册，同系列洗头）', [_EL]),
            'accessory_outlet_a': (ACCESSORY_OUTLET_A, 'A', '真空泵附件插座≤5 A（厂家）', [_GSG])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning) in {
            'flow_rate_m3_s': ({str(k): v for k, v in FLOW_RATE_M3_S.items()}, 'm3/s', '分配流速档位1–11对应的每孔流量（按“300 µL三循环≤30 s”与默认7档反推的工程值）'),
            'travel_rates': ({k: v[0] for k, v in TRAVEL.items()}, 'm/s', '抽吸行进速度（除2、3档外为工程值）'),
            'travel_disturbance': ({k: v[1] for k, v in TRAVEL.items()}, '1', '行进速度档对细胞层的扰动系数（CW档较小）'),
            'line_tanks': (LINE_TANKS, '1', '管路按串联全混釜数（标定到厂家300 mL灌注≥95 %）'), 'park_tip_m': (PARK_TIP_M, 'm', '洗头停放时抽吸管尖高于板位'), 'disp_tip_above_asp_m': (DISP_TIP_ABOVE_ASP_M, 'm', '分配管比抽吸管短'),
            'carrier_travel_m': (CARRIER_TRAVEL_M, 'm', '载板行程'), 'carrier_move_s': (CARRIER_MOVE_S, 's', '载板移动时间'), 'lift_speed_m_s': (LIFT_SPEED_M_S, 'm/s', '洗头快速升降速度'),
            'valve_s': (VALVE_S, 's', '切阀时间'), 'tube_inner_r_m': (TUBE_INNER_R_M, 'm', '分配管内半径'), 'low_flow_r_m': (LOW_FLOW_R_M, 'm', '低流量出口等效半径'),
            'g0_m': (G0_M, 'm', '孔底液层排空长度（按50步≈100 µL标定）'), 'delta0_m': (DELTA0_M, 'm', '管尖到底时残留的孔底液膜厚度'),
            'fillet_factor': (FILLET_FACTOR, '1', '底角液环半径/毛细长度'), 'residual_scatter': (RESIDUAL_SCATTER, '1', '单孔残余的对数正态离散'),
            'wbe_scatter_m': (WBE_SCATTER_M, 'm', '板内孔底高度离散'), 'cv_repeat': (CV_REPEAT, '1', '分配逐次离散'), 'cv_tube': (CV_TUBE, '1', '管间固定偏差'),
            'cv_abs_m3': (CV_ABS_M3, 'm3', '分配绝对离散'), 'mix_jet_m_s': (MIX_JET_M_S, 'm/s', '射流混合特征速度'), 'mix_min': (MIX_MIN, '1', '慢速注液对残液的最低对流混合效率'), 'shake_tau_s': (dict(SHAKE_TAU), 's', '摇动混合时间常数'),
            'k_off_ns': (K_OFF_NS, '1/s', '非特异吸附解吸速率（无表面活性剂）'), 'k_off_ns_surf': (K_OFF_NS_SURF, '1/s', '非特异吸附解吸速率（含吐温）'),
            'k_off_spec': (K_OFF_SPEC, '1/s', '特异结合解离速率'), 'tau_cell_pa': (TAU_CELL_PA, 'Pa', '弱贴壁细胞e倍脱落的壁面剪切（强贴壁×4）'),
            'cell_layer_clear_m': (CELL_LAYER_CLEAR_M, 'm', '管尖低于此间隙时刮擦细胞层'), 'dry_dwell_s': (DRY_DWELL_S, 's', '近干孔损伤细胞的停留时间'),
            'clog_tau_s': (CLOG_TAU_S, 's', '1 %盐洗液在空闲洗头中的堵针时间尺度'), 'meniscus_m': (MENISCUS_M, 'm', '液面可高出孔口的高度'),
            'idle_w': (IDLE_W, 'W', '待机电功率'), 'active_w': (ACTIVE_W, 'W', '运行电功率'), 'drop_m3': (DROP_M3, 'm3', '管尖外壁带液'),
            'drip_p_per_s': (DRIP_P_PER_S, '1/s', '板停在洗头下时每管每秒滴落概率'), 'drops_per_tip': (DROPS_PER_TIP, '1', '每次分配后每个管尖最多可滴落的液滴数')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['manifold', 'manifold_tubes', 'plate_carrier', 'prime_trough', 'buffer_inlets', 'rear_connectors', 'touch_screen', 'switch_power'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='405 TS洗板：供液瓶–缓冲阀–管路（死体积）–96管洗头的分配与真空抽吸，孔内残余、清洗效率、细胞脱落、堵针、溢流/滴落污染与传感器报警',
        equations=['fluid path: LINE_TANKS = 2 stirred tanks in series of V_dead / 2 each (V_dead = 110 mL): purity after priming V = 1 - exp(-2V/V_dead)(1 + 2V/V_dead)',
                   'residual = A g^2/(g + g0) + A delta0 + 2 pi r (1 - pi/4) (0.1 l_c)^2 + 2 pi r (V/A) 0.94 l_c Ca^(2/3); g = z_tip - z_well_bottom, Ca = mu U / sigma, l_c = sqrt(sigma / (rho g))',
                   'dispensed per tube: v = V_set f_clog (1 + b_tube + N(0, sqrt(cv_rep^2 + (dv/V_set)^2)))',
                   'two-compartment well: after a dispense u = (1 - m_jet) V0/(V0 + v) of the species sit in the bottom layer L = V0, m_jet = 1 - (1 - m_min) exp(-v_j / 0.6 m/s); soak / shake mix u -> 0',
                   'soluble species after aspiration: n <- n f, f = u res/L + (1 - u) res/V (res <= L) or u + (1 - u) res/V ; desorption: dn_ns = n_ns (1 - exp(-k_off t_contact))',
                   'cell detachment by the jet: N <- N exp(-(tau_w / tau_c)^2), tau_w = 0.01 rho v_j^2 exp(-h / 2 mm), v_j = Q / (pi r_tube^2)',
                   'species leaving at the top while the jet runs (skim or spill): n <- n (u + (1 - u) exp(-V_excess / V_cap)); level cap with the vacuum on and a free aspirate tube: V <= A (z_tip - z_bottom) (tip below the rim) or V_well + A h_men/2 (z_tip - z_rim)/h_men (tip up to h_men = 1.5 mm above); otherwise V > V_well + A h_men / 2 overflows to the four neighbours',
                   'drips from the parked manifold: Poisson per tube (rate p_drip), at most DROPS_PER_TIP drops of 0.25 uL of clean line buffer after each dispense, prime or bottom wash; a full well runs over'],
        parameters=params, source_ids=[_SIM, _WB, _GSG, _EL, _VDI, _LAL, _SLAS, _ARB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_405ts_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('biotek_405ts', caps, slots, {'washer_405ts': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'washer_405ts': mechanism})


# ---------------------------------------------------------------- 1.1.0 device commands and the front panel: effects, errors, contract entries
ERROR_ZH.update({
    'EDITOR_CLOSED': '没有打开的程序编辑屏（先Create或Edit）', 'UNKNOWN_PROTOCOL': '程序不存在', 'INVALID_NAME': '程序名只能用字母、数字、空格与 _ . -（≤32字符）',
    'NAME_REQUIRED': '保存前须输入程序名', 'PROTOCOL_LOCKED': '锁定的程序不能覆盖或删除（另存为新名即复制）', 'PROTOCOL_STORE_FULL': '用户程序已达上限（99）',
    'TOO_MANY_STEPS': '程序步骤已达上限（50）', 'CLIPBOARD_EMPTY': '剪贴板中没有步骤', 'PROTOCOL_EMPTY': '程序没有可执行的步骤',
    'LOOP_NOT_OPEN': 'End Loop之前没有Loop', 'LOOP_NOT_CLOSED': 'Loop没有对应的End Loop', 'LOOP_EMPTY': '循环内没有可执行的步骤',
    'SOAK_STEP_EMPTY': 'Shake/Soak步骤既未启用摇动也未启用浸泡', 'VACUUM_DELAY_EXCEEDS_VOLUME': '真空延迟体积大于分配体积',
    'PROTOCOL_TOO_LONG': '循环展开后步骤超过5000', 'NOT_WAITING': '程序没有在等待Continue', 'MANIFOLD_SETTING_MISMATCH': '洗头设置与实际安装的96管洗头不符',
})
_DEV_RUN = _OPS + _MOVE + ['buffer.selected', 'protocol.run']
DEVICE_EFFECTS = {
    'protocol.edit_open': ['protocol.editor'], 'protocol.edit_info': ['protocol.editor'], 'protocol.edit_step': ['protocol.editor'],
    'protocol.edit_close': ['protocol.editor'], 'protocol.save': ['protocols.list', 'protocol.editor'], 'protocol.validate': [], 'protocol.delete': ['protocols.list'],
    'protocol.run': ['protocols.list'] + _DEV_RUN, 'protocol.test_run': list(_DEV_RUN), 'protocol.resume': list(_DEV_RUN), 'run.stop': _OPS + _MOVE + ['protocol.run'],
    'washer.configure': ['washer.settings'], 'autoprime.configure': ['autoprime.settings'], 'clock.set': ['clock.settings'],
    'instrument.self_check': ['selfcheck.last', 'truth.trough_m3', 'manifold.submerged', 'truth.waste_volume_m3', 'sensor.waste_full'] + _MOVE,
}
_DEVICE_SLOTS = ['protocol.run', 'manifold.submerged', 'truth.trough_m3']
_RUN_STEP += _DEVICE_SLOTS                                                    # (in place: the 1.0.0 on_step lists of the runs and of sim.step)
for _k in ('power.set', 'power.press_switch'):
    EFFECTS[_k] += ['protocol.run', 'protocol.editor', 'manifold.submerged']   # power off ends a protocol run and drops the edit; power on homes the manifold
for _k in ('plant.configure', 'fault.inject'):
    EFFECTS[_k] += ['protocol.run', 'protocol.editor']                        # a mains fault or a power loss does the same
DEVICE_ON_STEP = {'protocol.run': _RUN_STEP, 'protocol.test_run': _RUN_STEP, 'protocol.resume': _RUN_STEP, 'autoprime.configure': _RUN_STEP}
DEVICE_ON_STEP_ZH = {'protocol.run': '按步骤推进：每个洗板/灌注步骤按阶段完成并写记录，Delay步骤计时或等待Continue，最后一步结束时程序完成；故障、断电或STOP使程序结束',
                     'protocol.test_run': '同protocol.run（不更新Last run）', 'protocol.resume': '继续后的步骤按阶段推进',
                     'autoprime.configure': '启用后，Home屏空闲达到间隔时自动灌注（PR记录，可浸没），任何操作重新计时'}
_START = ['NO_PLATE', 'WASTE_FULL', 'BUFFER_EMPTY', 'VACUUM_LOW', 'RECORDS_FULL']
_VALID = ['PROTOCOL_EMPTY', 'LOOP_NOT_OPEN', 'LOOP_NOT_CLOSED', 'LOOP_EMPTY', 'SOAK_STEP_EMPTY', 'VOLUME_NOT_IN_1UL_STEPS', 'VACUUM_DELAY_EXCEEDS_VOLUME',
          'CELL_WASH_OPTION_ABSENT', 'PROTOCOL_TOO_LONG']
_DEV = ['PLANT_REQUIRED', 'POWER_OFF']
DEVICE_CAP_ERRORS = {
    'protocol.edit_open': _DEV + ['UNKNOWN_PROTOCOL'], 'protocol.edit_info': _DEV + ['EDITOR_CLOSED', 'INVALID_NAME', 'UNKNOWN_PLATE_TYPE', 'PLATE_INCOMPATIBLE'],
    'protocol.edit_step': _DEV + ['EDITOR_CLOSED', 'TOO_MANY_STEPS', 'CLIPBOARD_EMPTY'], 'protocol.edit_close': _DEV + ['EDITOR_CLOSED'],
    'protocol.save': _DEV + ['EDITOR_CLOSED', 'NAME_REQUIRED', 'PROTOCOL_LOCKED', 'PROTOCOL_STORE_FULL'] + _VALID, 'protocol.validate': _DEV + ['EDITOR_CLOSED'] + _VALID,
    'protocol.delete': _DEV + ['UNKNOWN_PROTOCOL', 'PROTOCOL_LOCKED', 'OPERATION_RUNNING'],
    'protocol.run': _DEV + ['OPERATION_RUNNING', 'UNKNOWN_PROTOCOL'] + _VALID + _START,
    'protocol.test_run': _DEV + ['OPERATION_RUNNING', 'EDITOR_CLOSED'] + _VALID + _START, 'protocol.resume': ['PLANT_REQUIRED', 'NOT_WAITING', 'POWER_OFF'] + _START,
    'run.stop': ['PLANT_REQUIRED', 'NOT_RUNNING'], 'washer.configure': _DEV + ['OPERATION_RUNNING'],
    'autoprime.configure': _DEV + ['OPERATION_RUNNING', 'CELL_WASH_OPTION_ABSENT'], 'clock.set': list(_DEV), 'instrument.self_check': _DEV + ['OPERATION_RUNNING'],
}
_DEVICE_GUARDED = {k for k, v in DEVICE_CAPS.items() if v['kind'] == 'device_command'}
_GSG_PAGES = {'protocol.edit_open': 'p.32', 'protocol.edit_info': 'p.32-33', 'protocol.edit_step': 'p.32, 43-46', 'protocol.edit_close': 'p.31',
              'protocol.save': 'p.33', 'protocol.validate': 'p.33', 'protocol.delete': 'p.33', 'protocol.run': 'p.39, 41-43', 'protocol.test_run': 'p.33',
              'protocol.resume': 'p.83', 'run.stop': 'p.79, 94', 'washer.configure': 'p.23, 61, 69-70', 'autoprime.configure': 'p.70-71, 79', 'clock.set': 'p.23',
              'instrument.self_check': 'p.25, 34, 82'}
DEVICE_LIMITS = [
    '1.1.0设备层（触摸屏软件）：程序存储与编辑（Create/Edit/Delete、名称与板型、Add/Edit/剪切/粘贴步骤、Validate、Test Run、Save）、按步骤运行程序（Wash、Aspirate、Dispense、'
    'Prime（可浸没）、Shake/Soak、Delay（计时或等待Continue）、Remark、Loop）、Quick Prime/Quick Wash（即prime.run / wash.run）、Maintenance/QC/示例板载程序、'
    'Instrument > Options（洗头选择、磁铁高度偏移、液体/流量/废液/真空过滤传感器、Vacuum Dissipate延时）、AutoPrime、时钟、Self-Check与STOP，均为运行层设备命令。',
    PREDEFINED_NOTE,
    '程序与步骤上限（99个用户程序、50步、展开后5000步）、程序名字符集、X/Y步长（0.0457 mm/步，±60步）、灌注槽浸没液量（90 mL）、磁铁偏移与Vacuum Dissipate的范围、'
    'Delay与浸没时长上限（24 h）、Loop次数（1–999）为假设（说明书未写）；X/Y偏移只按“分配射流打在孔壁上时孔底剪切减小”（(1-d/r)²）与“管外缘碰孔壁”警告建模，'
    '对抽吸残余无影响；Wash format（Plate/Sector）只对大板型有意义，96孔板只有一个扇区，未提供。',
    '传感器关闭的效果：液体传感器关闭时启动不查瓶，瓶空后泵抽空气、不再分配（管路液体不被空气排空，属简化）；流量传感器关闭时不报FLOW_ERROR；废液传感器关闭时不在90 %报满，'
    '废液超过瓶总容量即倒灌真空泵（VACUUM_LOW，说明书p.37）；真空过滤传感器无对应硬件（模块未装）。',
    '协议中的Prime步骤与AutoPrime在载板上有板时照常灌注（载板在初始位，灌注槽在停放的洗头下；说明书p.52的细胞洗涤程序先Prime再Wash），而1.0.0的prime.run（Quick Prime）'
    '保留其“载板上有板时拒绝”（PLATE_ON_CARRIER）的规则。',
    '仪器时钟从2026-01-01 08:00 GMT起随仿真时钟走，Set Time and Date只改偏移与时区；日期只有DD Month YY一种格式（说明书p.23）。',
    'Self-Check只比对洗头设置与实际洗头、回位并排空灌注槽，不模拟其他硬件自检；序列号、Basecode版本与帮助文字不在已保存的文档中。',
]
PANEL_LIMITS = [
    '前面板层（panel.*）：按panel_ui.json（405 TS Getting Started Guide 1171009 Rev C页码证据）实现触摸屏（Home程序列表与Quick/Define/Maintenance/Instrument、程序运行屏、'
    'Define编辑屏与12种步骤按钮、各步骤参数页、Instrument的Current Config/Options/AutoPrime/Next/Other/BioStack/Set Time and Date、帮助）与后面板电源开关；'
    '每个屏幕操作都调用本运行层的同名能力（没有第二份仪器逻辑）；手势不推进仿真时钟。',
    '说明书只有入门指南（完整操作手册在仪器U盘上，未保存）：屏幕版式、帮助文字、错误代码的具体编号与文字、部分字段的默认值来自入门指南的描述或为假设（spec标approximate）；'
    '真空过滤、AutoClean（超声）、Verify堵针检测、BioStack、U盘程序传输与视频播放需要本配置没有的硬件，屏幕显示相应提示（unsupported）。',
    '面板不含3D联动：屏幕纹理（render()）与电源键关节触发手势属于后续Matterix步骤；本版本只在过程层验证（panel_ui_validation）。',
    '后面板电源开关是硬开关（负责人澄清10-19）：物理动作，不受所有权限制（power.set、power.press_switch不再因另一agent的运行被拒）；另一agent关断时进行中的运行按断电失败。',
    '手动/机器人操作（buffer.load、plate.provide、plate.load、plate.unload、waste.empty、maintenance.clean_tubes）是物理动作（负责人澄清10-19），不受所有权限制；仪器自己的规则仍然适用：运行中（含程序两步之间）不能装卸板或疏通管路、运行中不能倒废液、不能更换正在使用的供液瓶（OPERATION_RUNNING）。',
    '入门指南p.94只给出两个错误代码：6045（Serial write error，LHC协议中的COM口设置错误）与810D（仪器须在主菜单/Home屏才能与LHC通讯）；二者都属于LHC（Liquid Handling Control）PC软件经USB/RS232的通讯，本配置没有该PC软件，保留为unsupported（需要缺失的硬件/软件）；其余系统错误代码（0000-A500）与软件错误代码（6000-6100）的编号与文字在未保存的操作手册中，屏幕错误对话框显示运行层错误代码与英文文字（假设）。',
]


def _dev_errors(k):
    extra = ['ACCESS_DENIED'] if k in _DEVICE_GUARDED else []
    return list(dict.fromkeys(DEVICE_CAP_ERRORS[k] + _COMMON_ERRORS + extra))


def export_device_contract_metadata():
    """Contract entries of the 1.1.0 device commands (DEVICE_CAPS), in the form of export_biotek_405ts_contract_metadata()."""
    caps = {}
    for k, v in DEVICE_CAPS.items():
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='implemented',
                 implementation={layer: 'implemented' if layer in ('process', 'host') else 'absent' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.biotek_405ts.Biotek405tsSession.invoke',
                              invocation=f'session.invoke(instance_id="washer_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[dict(description_zh='已声明实验室条件且通电（触摸屏软件只在通电时工作）；' + {
                     'protocol.run': '无运行；程序通过Validate；含洗板类步骤时载板上有板；第一步的瓶、废液、真空与记录数检查（按启用的传感器）',
                     'protocol.test_run': '无运行；编辑屏已打开；其余同protocol.run', 'protocol.resume': '程序在等待Continue；下一步的检查通过（不通过时仍停在提示）',
                     'run.stop': '有运行中的操作或程序', 'protocol.save': '编辑屏已打开、已输入名称、不是锁定程序、通过Validate',
                     'protocol.delete': '程序存在且未锁定、不在运行', 'washer.configure': '无运行', 'autoprime.configure': '无运行；流速1–2需细胞洗涤选件',
                     'instrument.self_check': '无运行'}.get(k, '编辑类命令需已打开编辑屏' if k.startswith('protocol.edit') and k != 'protocol.edit_open' else '无其他条件')
                     + _foreign_text(k) + '；被拒时状态不变。', state_ids=['plant.configured', 'power.enabled', 'operation.kind', 'protocol.run', 'protocol.editor']
                     + (['operation.started_by_other_agent'] if k in _DEVICE_GUARDED else []), enforced_by='runtime',
                     check='Biotek405tsRuntime._invoke_any / Biotek405tsSession._guard')],
                 state_effects=[dict(state_id=s, timing='immediate', description_zh='命令立即写入或改变的状态') for s in dict.fromkeys(DEVICE_EFFECTS[k])]
                 + [dict(state_id=s, timing='on_step', description_zh=DEVICE_ON_STEP_ZH[k]) for s in dict.fromkeys(DEVICE_ON_STEP.get(k, []))],
                 observes=['protocol.run', 'operation.kind', 'result.last_record_id'] if k in ('protocol.run', 'protocol.test_run', 'protocol.resume', 'run.stop')
                 else [x for x in dict.fromkeys(DEVICE_EFFECTS[k]) if SLOTS[x]['visibility'] == 'agent'][:3],
                 model_effects=_model_effects_device(k),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion='程序全部步骤完成（protocol.run状态completed）' if async_ else '原子设定、编辑或查询',
                                completion_state_ids=['protocol.run'] if async_ else [], timeout_s=None, cancellation='supported' if async_ else 'unsupported',
                                retry='same_request_id', resources=['manifold', 'plate_carrier', 'buffer_valves', 'vacuum', 'host_clock'] if async_ else ['touch_screen'],
                                partial_failure_zh='某一步失败或被停止时程序结束（protocol.run为failed/cancelled，该步记录为failed/cancelled，已完成步骤的效果保留）'
                                if async_ else '同步原子操作：成功时一次写入全部效果，被拒时状态不变'),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _dev_errors(k)],
                 verification=[], limitations=list(DEVICE_LIMITS), source_ids=[_GSG, _SIM])
        c['function_zh'] += f'（入门指南{_GSG_PAGES[k]}）'
        caps[k] = c
    return caps


def _model_effects_device(k):
    seen = sorted((set(DEVICE_EFFECTS.get(k, [])) | set(DEVICE_ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节或屏幕数字（' + '、'.join(seen) + '），运行层本身不驱动模型')
    return dict(mode='none', description_zh='触摸屏软件的设置与存储，没有可见模型变化', binding_ids=[])


def export_panel_contract_metadata():
    """Contract entries of the panel capabilities (panel.press / touch / capture / frame): a gesture changes what the commands it calls change."""
    if not PANEL_SPEC:
        return {}
    called = _panel_ui.spec_capabilities(PANEL_SPEC)
    effects = list(dict.fromkeys(x for c in called for x in (EFFECTS.get(c, []) if c in CAPS else DEVICE_EFFECTS.get(c, []))))
    out = {}
    for k, d in PANEL_CAPS.items():
        query = k in ('panel.capture', 'panel.frame')
        c = {key: copy.deepcopy(d[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        errors = ['PLANT_REQUIRED', 'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'] + (['ACCESS_DENIED'] if d['visibility'] != 'agent' or not query else [])
        c.update(availability='implemented',
                 implementation={layer: 'implemented' if layer in ('process', 'host') else 'absent' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.biotek_405ts.Biotek405tsSession.invoke',
                              invocation=f'session.invoke(instance_id="washer_01",capability_id={k!r},arguments={d["example_arguments"]!r},request_id="unique")'),
                 preconditions=[dict(description_zh='须已声明实验室条件；断电时屏幕暗、触摸无反应（只有电源开关起作用）；被调用能力的前提同样适用（被拒时屏幕显示仪器的提示，手势不被接受）；'
                                                    '经会话的手势以该会话的调用者身份执行：另一个agent的运行只能查看，不能STOP或修改（ACCESS_DENIED，屏幕显示错误提示，状态不变），'
                                                    '由面板启动的运行归该调用者' if not query else '须已声明实验室条件（关机时为暗屏）',
                                     state_ids=['plant.configured', 'power.enabled'], enforced_by='runtime', check='Biotek405tsRuntime._panel_command')],
                 state_effects=[] if query else [dict(state_id=x, timing='immediate', description_zh='手势调用的能力所写的状态（屏幕导航本身不改状态槽）') for x in effects],
                 observes=['power.enabled', 'operation.kind', 'protocol.run'] if not query else [],
                 model_effects=dict(mode='none', description_zh='面板层只改界面状态与被调用能力的状态，屏幕画面由panel.capture渲染', binding_ids=[]),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion='手势瞬时完成（不推进仿真时钟）：界面状态与被调用能力的效果立即生效；结果只说明是否被仪器接受，屏幕须用panel.capture查看'
                                if not query else '立即返回当前画面', completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id',
                                resources=['host_clock'], partial_failure_zh='被拒的手势不改变运行层状态（只显示仪器的提示）'),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, {'INVALID_ARGUMENT': '未知输入件/显示件、手势与输入件不符或坐标在屏外'}.get(e, e)), side_effects='none')
                         for e in errors],
                 verification=[], limitations=list(PANEL_LIMITS), source_ids=[_GSG, _SIM])
        out[k] = c
    return out
